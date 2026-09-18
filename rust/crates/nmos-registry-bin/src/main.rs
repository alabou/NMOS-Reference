// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The registry process.
//!
//! Counterpart to `nmos_registry.py`: the command line, the TLS configuration
//! and the wiring that turns them into three listeners and two background
//! tasks.

#![doc(html_no_source)]
#![allow(clippy::print_stdout)]

use std::net::SocketAddr;
use std::path::Path;
use std::sync::Arc;

use clap::Parser as _;
use nmos_registry_bin::listen::{context_for, serve_maybe_tls};
use nmos_registry_bin::{as_client, cert_check, cli, identity, logging, tls};

use nmos_registry::registry::Registry;
use nmos_registry_core::store::RegistryStore;
use nmos_registry_http::jwks_cache;
use nmos_registry_http::oauth2::SharedJwks;
use nmos_registry_http::security::InterfaceSecurity;
use nmos_registry_http::serve::{Assembly, Ports, collector_task, matcher_task, status_task};
use tokio::net::TcpListener;

#[tokio::main]
async fn main() -> std::io::Result<()> {
    let args = cli::Args::parse();

    // Two sinks at two verbosities, as `setup_logging` configures them: the
    // console at `NMOS_LOG_LEVEL` and the file at DEBUG. Without a subscriber
    // every `tracing::info!` in the workspace is evaluated and discarded, and
    // `bench_registry/compare.py --verify-log-volume` rejects a comparison when
    // two targets' log bytes differ by more than an order of magnitude -- so a
    // silent registry cannot be benchmarked against the Python one at all.
    logging::init(Path::new(&args.log_file));

    // After the logging, before anything binds -- the order `run()` uses, so a
    // CONFIG failure is reported the same way by both implementations.
    //
    // This is what stops a missing `--registryCertificate` from becoming a
    // registry quietly serving plain HTTP: `context_for` would warn and carry
    // on, and that warning path is unreachable in the real program precisely
    // because this has already exited.
    if let Err(error) = cert_check::validate_startup_certs(&args) {
        // `SystemExit(str)` prints the message to stderr and exits 1.
        eprintln!("{error}");
        std::process::exit(1);
    }

    let store = RegistryStore::with_intervals(
        args.garbage_collection_interval as i64,
        args.forget_interval as i64,
    );
    let mut assembly = Assembly::new(Registry::new(store), uuid::Uuid::new_v4().to_string());

    // TR-10-SEC:105 forbids the Registration API from requiring OAuth 2.0, so
    // `--oauth2` reaches only the Query interface. The constructor is what
    // makes that unexpressible rather than merely unwritten.
    assembly.registration_security =
        InterfaceSecurity::registration(args.registration_client_auth());
    // The other half of the OAuth 2.0 audience check: a token's `aud` entry has
    // to correspond to one of our own server certificate's identities. With no
    // certificate this is empty and every audience check fails, which is the
    // correct reading of an unconfigured deployment rather than an oversight.
    let tls_server_cert_names = if args.registry_certificate.is_empty() {
        Vec::new()
    } else {
        identity::server_cert_names(Path::new(&args.registry_certificate))
    };

    // Shared with the refresh task below: the handle is cloned into the router
    // now and written to whenever a fetch succeeds. It starts empty, which
    // refuses every bearer token.
    let oauth2_keys = SharedJwks::empty();

    assembly.query_security = InterfaceSecurity {
        client_auth_required: args.query_client_auth(),
        oauth2: args.oauth2,
        serial_number: args.registry_serial_number.clone(),
        tls_server_cert_names,
        // Empty until the first fetch succeeds. TR-10-SEC §14.3.2 requires
        // exactly that: bearer access is refused until keys arrive, rather than
        // admitted while they are missing.
        oauth2_keys: oauth2_keys.clone(),
        ..InterfaceSecurity::default()
    };

    let ports = Ports {
        registration: args.registration_port,
        query: args.query_port,
        websocket: args.query_websocket_port,
    };

    // One context per interface, because the trust anchor is per-interface --
    // that is what lets Registration require mutual TLS while Query does not.
    // Query and the WebSocket deliberately share one: a subscription's `secure`
    // attribute describes one negotiated mode for the pair, so splitting their
    // TLS configuration would make that attribute unrepresentable.
    let registration_tls = context_for(
        &args.registry_certificate,
        &args.registry_key,
        args.registry_disable_tls,
        &args.registration_trusted_root_ca,
        args.registration_optional_client_auth,
        args.gcrl.as_deref(),
    )
    .map_err(std::io::Error::other)?;
    let query_tls = context_for(
        &args.registry_certificate,
        &args.registry_key,
        args.registry_disable_tls,
        &args.query_trusted_root_ca,
        args.query_optional_client_auth,
        args.gcrl.as_deref(),
    )
    .map_err(std::io::Error::other)?;

    println!(
        "nmos-registry: registration :{} query :{} websocket :{} (RAP {})",
        ports.registration,
        ports.query,
        ports.websocket,
        assembly
            .registration_security
            // The posture actually reached, not the one asked for: a missing
            // certificate downgrades to plaintext with a warning, and the
            // announced RAP has to say so.
            .rap_for(registration_tls.is_some())
            .value(),
    );
    for (name, configured) in [("registration", &registration_tls), ("query", &query_tls)] {
        match configured {
            Some((_, mode)) => tracing::info!("{name}: TLS, client certificates {mode:?}"),
            None => tracing::warn!("{name}: plain HTTP \u{2014} TR-10-SEC requires TLS"),
        }
    }

    let registration_context = registration_tls.map(|(context, _)| Arc::new(context));
    let query_context = query_tls.map(|(context, _)| Arc::new(context));

    // `ws` against `wss` in `ws_href`, and the `secure` attribute a
    // subscription reports, follow the *Query* listener's posture.
    let apps = assembly.routers(ports, query_context.is_some());

    let registration =
        TcpListener::bind(SocketAddr::from(([0, 0, 0, 0], ports.registration))).await?;
    let query = TcpListener::bind(SocketAddr::from(([0, 0, 0, 0], ports.query))).await?;
    let websocket = TcpListener::bind(SocketAddr::from(([0, 0, 0, 0], ports.websocket))).await?;

    // The JWKS refresh loop, when OAuth 2.0 is on and there is somewhere to
    // fetch from. Only the Query API consumes these keys; Registration must not
    // require OAuth 2.0 at all (TR-10-SEC:105) and never consults them.
    let jwks = match (args.oauth2, args.oauth2_host.is_empty()) {
        (false, _) => None,
        (true, true) => {
            // Python warns and gives up in exactly this shape. Refusing to
            // start would be the other option and is the wrong one: the
            // Registration API is unaffected and should keep serving.
            tracing::warn!(
                "registry: --oauth2 set but no --oauth2Host; Query API bearer \
                 validation will fail closed",
            );
            None
        }
        (true, false) => {
            let tls = if args.oauth2_disable_tls {
                None
            } else {
                // `--oauth2TrustedRootCA` first, falling back to the general
                // `--trustedRootCA`, and to the system store when neither is
                // given -- `_ca_list` in the Python.
                let anchors: Vec<_> = if args.oauth2_trusted_root_ca.is_empty() {
                    args.trusted_root_ca.clone()
                } else {
                    args.oauth2_trusted_root_ca.clone()
                };
                Some(Arc::new(
                    tls::client_context(&anchors, args.gcrl.as_deref())
                        .map_err(std::io::Error::other)?,
                ))
            };
            let server = as_client::AuthorizationServer {
                scheme: if args.oauth2_disable_tls {
                    "http"
                } else {
                    "https"
                }
                .to_owned(),
                host: args.oauth2_host.clone(),
                port: args.oauth2_port,
                api_selector: args.oauth2_api_selector.clone(),
                tls,
            };
            tracing::info!(
                "registry: OAuth 2.0 keys from {}://{}:{}",
                server.scheme,
                server.host,
                server.port,
            );
            Some(tokio::spawn(jwks_cache::run(
                server,
                oauth2_keys,
                // Uniform on [0, 3600]: the jitter that stops a fleet of
                // registries refreshing on the same second.
                || {
                    let nanos = std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)
                        .map_or(0, |since| since.subsec_nanos());
                    f64::from(nanos % 3_600_000) / 1000.0
                },
            )))
        }
    };

    let matcher = tokio::spawn(matcher_task(
        Arc::clone(&assembly.registry),
        Arc::clone(&assembly.subscriptions),
    ));
    let status = tokio::spawn(status_task(
        Arc::clone(&assembly.registry),
        Arc::clone(&assembly.subscriptions),
        args.status_interval,
    ));
    let collector = tokio::spawn(collector_task(
        Arc::clone(&assembly.registry),
        Arc::clone(&assembly.subscriptions),
    ));

    let result = tokio::try_join!(
        serve_maybe_tls(registration, registration_context, apps.registration),
        serve_maybe_tls(query, query_context.clone(), apps.query),
        serve_maybe_tls(websocket, query_context, apps.websocket),
    );
    matcher.abort();
    collector.abort();
    status.abort();
    if let Some(jwks) = jwks {
        jwks.abort();
    }
    result.map(|((), (), ())| ())
}
