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
use nmos_registry_backend::RegistryBackend;
use nmos_registry_core::store::RegistryStore;
use nmos_registry_http::jwks_cache;
use nmos_registry_http::oauth2::SharedJwks;
use nmos_registry_http::security::InterfaceSecurity;
use nmos_registry_http::serve::{Assembly, Ports, collector_task, matcher_task, status_task};
use nmos_registry_raft::backend::RaftRegistryBackend;
use nmos_registry_raft::cluster::derive_raft_layout;
use nmos_registry_raft::cursors::CursorAllocator;
use nmos_registry_raft::machine::StateMachine;
use nmos_registry_raft::node::{ForwardHandler, RaftNode, RaftTiming};
use nmos_registry_raft::persist::TermStore;
use nmos_registry_raft::transport::{PeerTls, RaftTransport, Transport, TransportSettings};
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

    // `--distributed` swaps the storage layer and nothing else: the routers,
    // the subscriptions and the Query path are identical either way, which is
    // the whole point of the backend seam.
    let cluster = match nmos_registry_bin::distributed::resolve(&distributed_flags(&args)) {
        Ok(cluster) => cluster,
        Err(error) => {
            eprintln!("CONFIG: {error}");
            std::process::exit(1);
        }
    };
    let consensus = match cluster {
        None => None,
        Some(config) => match build_consensus(&config, &assembly.registry) {
            Ok(backend) => {
                assembly.backend = Arc::clone(&backend) as Arc<dyn RegistryBackend>;
                Some(backend)
            }
            Err(error) => {
                eprintln!("CONFIG: {error}");
                std::process::exit(1);
            }
        },
    };

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

    // The backend is started **after** the listeners are bound and before any
    // traffic is served. Consensus has to be running for the Registration API
    // to answer anything but 503, and binding first means a peer that connects
    // the instant this member starts finds a listener rather than a refusal.
    if let Some(ref backend) = consensus {
        if let Err(error) = backend.start().await {
            eprintln!("CONFIG: the consensus backend did not start: {error}");
            std::process::exit(1);
        }
        tracing::info!(
            member = %backend.node().index(),
            // The cluster SIZE, not the index again. It read
            // `backend.node().index()` twice, so a three-member cluster
            // reported `cluster=1` on member 1 -- which is what a
            // single-member cluster would say, and is exactly the wrong thing
            // to see while diagnosing whether members found each other.
            cluster = %backend.node().cluster_size(),
            "registry: consensus member started",
        );
    }

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
        Arc::clone(&assembly.backend),
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

/// The flags the distributed resolver reads, lifted out of the parsed CLI.
///
/// Separated so the rules can be exercised without building a command line --
/// and so which flags participate is visible in one place rather than spread
/// through a resolver.
fn distributed_flags(args: &cli::Args) -> nmos_registry_bin::distributed::DistributedFlags {
    nmos_registry_bin::distributed::DistributedFlags {
        distributed: args.distributed,
        backend: args.distributed_backend.clone(),
        advertised_host: args.registry_advertised_host.clone(),
        neighbours: args.registry_neighbour.clone(),
        namespace: args.raft_namespace.clone(),
        client_port: args.raft_client_port,
        peer_port: args.raft_peer_port,
        state_dir: args.raft_state_dir.clone(),
        certificate: args.raft_certificate.clone(),
        key: args.raft_key.clone(),
        trusted_root_ca: args.raft_trusted_root_ca.clone(),
        certificate_name: args.raft_certificate_name.clone(),
        crl_file: args.raft_crl_file.clone(),
        disable_tls: args.raft_disable_tls,
        rpc_timeout: args.raft_rpc_timeout,
        mutation_timeout: args.raft_mutation_timeout,
        // The same three inputs that decide the registry's access policy, so
        // the two can never disagree about whether this command line describes
        // a secured registry.
        registry_listeners_are_tls: !args.registry_disable_tls
            && !args.registry_certificate.is_empty()
            && !args.registry_key.is_empty(),
        // Nothing to collect: this build has no etcd flags to be given. The
        // field stays so that adding them later cannot forget the refusal.
        etcd_flags_given: Vec::new(),
    }
}

/// Wire one consensus member: layout, transport, term store, machine, backend.
///
/// Built here, in one place, rather than half in the backend and half in the
/// node -- the Python makes the same choice for the same reason.
fn build_consensus(
    config: &nmos_registry_bin::distributed::RaftConfig,
    registry: &Arc<Registry>,
) -> Result<Arc<RaftRegistryBackend>, String> {
    let raft = derive_raft_layout(&config.layout, config.layout.token.clone());

    let peers: std::collections::HashMap<u64, (String, u16)> = raft
        .peers()
        .into_iter()
        .map(|member| (member.index, (member.host.clone(), member.port)))
        .collect();

    let tls = if config.tls {
        Some(Arc::new(
            peer_tls(config).map_err(|error| format!("peer TLS: {error}"))?,
        ))
    } else {
        None
    };

    let bind: std::net::SocketAddr =
        format!("{}:{}", config.layout.local.bind_address, config.peer_port)
            .parse()
            .map_err(|_| {
                format!(
                    "cannot bind the peer listener at {}:{}",
                    config.layout.local.bind_address, config.peer_port,
                )
            })?;

    std::fs::create_dir_all(&config.state_dir)
        .map_err(|error| format!("{}: {error}", config.state_dir.display()))?;
    let terms = TermStore::new(
        config
            .state_dir
            .join(format!("{}.json", config.layout.local.name)),
    );

    let transport = Arc::new(RaftTransport::new(TransportSettings {
        local: raft.local.index,
        peers,
        bind,
        cluster_id: raft.cluster_id.clone(),
        member_name: raft.local.name.clone(),
        // Replaced by the node once it has loaded the term store: loading is
        // what increments the counter, so reading it here as well would tell
        // every peer this member had restarted once more than it had.
        incarnation: 0,
        tls,
        rpc_timeout_ms: config
            .rpc_timeout
            .as_millis()
            .try_into()
            .unwrap_or(u64::MAX),
    }));

    let machine = StateMachine::new(
        raft.local.index,
        CursorAllocator::new(raft.local.index).map_err(|error| format!("cursor lane: {error}"))?,
    );
    let node = RaftNode::new(
        raft,
        Arc::clone(&transport) as Arc<dyn Transport>,
        terms,
        machine,
        Arc::clone(registry),
        RaftTiming::default(),
    );
    transport.set_incarnation(node.incarnation());

    let backend = RaftRegistryBackend::new(Arc::clone(registry), node, config.mutation_timeout);
    // The backend answers forwarded mutations itself, so a mutation handed over
    // by another member takes the same path as a local one. Two paths would
    // validate differently, which is the divergence ownership exists to remove.
    backend
        .node()
        .set_forward_handler(Arc::clone(&backend) as Arc<dyn ForwardHandler>);
    Ok(backend)
}

/// The peer TLS material, from the same certificate in both directions.
fn peer_tls(
    config: &nmos_registry_bin::distributed::RaftConfig,
) -> Result<PeerTls, Box<dyn std::error::Error>> {
    let crl = (!config.crl_file.is_empty()).then(|| Path::new(&config.crl_file));
    let context = tls::peer_context(
        Path::new(&config.certificate),
        Path::new(&config.key),
        &config.trusted_root_ca,
        crl,
    )?;
    Ok(PeerTls {
        context,
        peer_name: config.certificate_name.clone(),
    })
}
