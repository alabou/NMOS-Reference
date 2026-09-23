// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Serving a router over TLS, one connection at a time.
//!
//! `axum::serve` builds one service and hands every connection to it, which is
//! right for plaintext and wrong here: each connection carries its own peer
//! identity, and the handler has to be able to ask who *this* caller is. So the
//! TLS path drives hyper directly, reads the identity once per connection at
//! the only point the TLS session is reachable, and layers it onto that
//! connection's copy of the router.
//!
//! The plaintext path in `nmos-registry-http`'s `serve` is unchanged and still
//! uses `axum::serve`; nothing here applies to it.

use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::pin::Pin;
use std::sync::Arc;

use axum::Router;
use hyper_util::rt::{TokioExecutor, TokioIo};
use hyper_util::server::conn::auto::Builder;
use hyper_util::service::TowerToHyperService;
use openssl::ssl::{Ssl, SslContext};
use tokio::net::TcpListener;
use tokio_openssl::SslStream;

use crate::identity::peer_identity;
use crate::tls::{ClientAuth, TlsError, server_context};

/// Build one listener's TLS context, or decide it is a plain HTTP listener.
///
/// Port of `nmos_registry.py`'s `_server_context`, including the part that is
/// easy to mistake for an oversight: **a missing certificate is a warning, not
/// an error**. The registry starts anyway, in plaintext. That is deliberate in
/// the original -- it keeps a misconfigured deployment diagnosable rather than
/// silently absent -- and changing it here would mean a launch script that
/// works against one implementation and refuses to start against the other.
///
/// The trust anchor is per-interface, which is what lets Registration require
/// mutual TLS while Query does not, or the other way round.
///
/// # Errors
///
/// A certificate, key or CRL that is present but unusable. Absent ones are the
/// plaintext case above and are not errors.
pub fn context_for(
    identities: &[(PathBuf, PathBuf)],
    disable_tls: bool,
    trust_anchors: &[PathBuf],
    optional_client_auth: bool,
    gcrl: Option<&Path>,
) -> Result<Option<(SslContext, ClientAuth)>, TlsError> {
    if disable_tls {
        // TR-10-SEC RAP 0. A development configuration; the specification
        // requires TLS for a compliant deployment.
        return Ok(None);
    }
    if identities.is_empty() {
        tracing::warn!(
            "TLS requested but no --registryCertificate/--registryKey supplied \
             \u{2014} running without TLS",
        );
        return Ok(None);
    }
    let (context, report, mode) = server_context(
        identities,
        trust_anchors,
        optional_client_auth,
        gcrl,
    )?;
    for line in report.describe() {
        tracing::info!("{line}");
    }
    Ok(Some((context, mode)))
}

/// Serve `app` on `listener`, over TLS when a context is given.
///
/// The one place that chooses between the two paths, so a caller cannot wire a
/// listener to the wrong one.
///
/// # Errors
///
/// A failure of the listening socket itself.
pub async fn serve_maybe_tls(
    listener: TcpListener,
    context: Option<Arc<SslContext>>,
    app: Router,
) -> std::io::Result<()> {
    match context {
        Some(context) => serve_tls(listener, context, app).await,
        None => axum::serve(listener, app).await,
    }
}

/// Serve `app` over TLS on `listener` until the task is dropped.
///
/// Never returns in normal operation. A connection that fails to handshake is
/// logged and dropped; it does not take the listener down, which matters
/// because an unauthenticated peer probing a `CERT_REQUIRED` port is an
/// expected event rather than a fault.
///
/// # Errors
///
/// Only a failure of `accept` itself on the listening socket.
pub async fn serve_tls(
    listener: TcpListener,
    context: Arc<SslContext>,
    app: Router,
) -> std::io::Result<()> {
    loop {
        let (stream, peer) = listener.accept().await?;
        let context = Arc::clone(&context);
        let app = app.clone();
        tokio::spawn(async move {
            if let Err(error) = serve_connection(stream, peer, &context, app).await {
                // Debug, not warn: a refused handshake is the security policy
                // working. A port doing its job would otherwise fill the log,
                // and `--verify-log-volume` compares log bytes across targets.
                tracing::debug!(%peer, %error, "tls connection ended");
            }
        });
    }
}

/// Handshake one connection, attach its identity, and serve it.
async fn serve_connection(
    stream: tokio::net::TcpStream,
    peer: SocketAddr,
    context: &SslContext,
    app: Router,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let ssl = Ssl::new(context)?;
    let mut stream = SslStream::new(ssl, stream)?;
    // `SslStream::accept` needs a pinned receiver; the stream stays on this
    // task's stack for the life of the connection, so pinning in place is
    // sound and avoids a heap allocation per connection.
    Pin::new(&mut stream).accept().await?;

    // Read the session exactly once, here. After this the TLS state is owned
    // by hyper and a handler could not reach it even if it wanted to.
    let identity = peer_identity(stream.ssl());
    tracing::debug!(%peer, ?identity, "tls connection established");

    // `Extension` as a layer puts the value itself into every request's
    // extensions, which is where `security::peer_of` looks for it. The layer is
    // applied to *this connection's* clone of the router -- cloning a `Router`
    // is cheap and shares the route table -- so two connections never see each
    // other's identity.
    let service = app.layer(axum::Extension(identity)).into_service();

    Builder::new(TokioExecutor::new())
        // `with_upgrades`, because the Query API's subscription endpoint is a
        // WebSocket and an upgrade on a plain `serve_connection` would hang.
        .serve_connection_with_upgrades(TokioIo::new(stream), TowerToHyperService::new(service))
        .await
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tls::server_context;
    use axum::routing::get;
    use nmos_registry_http::security::{PeerIdentity, peer_of};
    use std::path::{Path, PathBuf};

    fn certs() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .nth(3)
            .expect("crate is nested three deep under the repository root")
            .join("Certificates/build.0")
    }

    fn available() -> bool {
        let present = certs()
            .join("pem/ExampleDeviceServer.ABC.SNX00000.chain.pem")
            .is_file();
        if !present {
            eprintln!("skipping: the PKI under Certificates/build.0 is not present");
        }
        present
    }

    /// Stand up a TLS listener that reports what it saw, and return its port.
    async fn listener_reporting_identity(optional_client_auth: bool) -> u16 {
        let anchor = certs().join("ExampleRootCA.pem");
        let (context, _, _) = server_context(
            &[(
                certs().join("pem/ExampleDeviceServer.ABC.SNX00000.chain.pem"),
                certs().join("key/ExampleDeviceServer.ABC.SNX00000.key"),
            )],
            &[&anchor],
            optional_client_auth,
            None,
        )
        .expect("the listener configures");

        // The handler answers with what `peer_of` found, which is the whole
        // question: did the identity survive the trip from the TLS session to
        // a request extension?
        let app = Router::new().route(
            "/who",
            get(|request: axum::extract::Request| async move {
                match peer_of(&request) {
                    PeerIdentity::NotTls => "not-tls".to_owned(),
                    PeerIdentity::TlsAnonymous => "anonymous".to_owned(),
                    PeerIdentity::Verified { names } => format!("verified:{}", names.join(",")),
                }
            }),
        );

        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let port = listener.local_addr().expect("addr").port();
        tokio::spawn(serve_tls(listener, Arc::new(context), app));
        port
    }

    /// Ask the listener over TLS with `openssl s_client`, optionally
    /// presenting a client certificate.
    fn ask(port: u16, client_certificate: bool) -> String {
        let mut command = std::process::Command::new("openssl");
        command
            .arg("s_client")
            .arg("-connect")
            .arg(format!("127.0.0.1:{port}"))
            .arg("-quiet")
            .arg("-verify_return_error")
            .arg("-CAfile")
            .arg(certs().join("ExampleRootCA.pem"));
        if client_certificate {
            let chain = certs().join("pem/ExampleDeviceClient.ABC.SNX00000.chain.pem");
            command
                .arg("-cert")
                .arg(&chain)
                // `-cert` alone sends only the leaf, and this identity is
                // signed by the intermediate Product CA. The server trusts the
                // root and nothing else -- as the Python server context does,
                // loading only `root_ca` -- so without the intermediate it
                // cannot build a path and answers `unknown ca`. Python's client
                // context sends the whole chain via `load_cert_chain`; this is
                // the `s_client` equivalent.
                .arg("-cert_chain")
                .arg(&chain)
                .arg("-key")
                .arg(certs().join("key/ExampleDeviceClient.ABC.SNX00000.key"));
        }
        let mut child = command
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .expect("spawn openssl");
        {
            use std::io::Write as _;
            let stdin = child.stdin.as_mut().expect("stdin");
            let _ = stdin.write_all(b"GET /who HTTP/1.0\r\n\r\n");
        }
        let output = child.wait_with_output().expect("openssl finished");
        format!(
            "{}{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr),
        )
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn a_client_certificate_reaches_the_handler_as_a_verified_identity() {
        if !available() {
            return;
        }
        let port = listener_reporting_identity(true).await;
        let answer = ask(port, true);
        assert!(
            answer.contains("verified:Example.Company.Device.Client.ABC.SNX00000.example.com"),
            "the handler did not see the client's name -- the identity did not \
             survive from the TLS session to the request\n{answer}",
        );
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn a_client_without_a_certificate_is_anonymous_not_verified() {
        if !available() {
            return;
        }
        // The case the verb gate turns on: CERT_OPTIONAL lets this connection
        // up, and the handler must be able to tell it apart from an
        // authenticated one. If this ever answered `verified:` with an empty
        // name list, every state-changing request would be let through.
        let port = listener_reporting_identity(true).await;
        let answer = ask(port, false);
        assert!(
            answer.contains("anonymous"),
            "an anonymous caller was not reported as anonymous\n{answer}",
        );
        assert!(
            !answer.contains("verified:"),
            "an anonymous caller was reported as verified\n{answer}",
        );
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn two_connections_do_not_share_an_identity() {
        if !available() {
            return;
        }
        // The reason the layer is applied per connection rather than once to a
        // shared service. A single service carrying the first caller's identity
        // would authenticate the second one for free, and the failure would
        // only appear under concurrent traffic.
        let port = listener_reporting_identity(true).await;
        let authenticated = ask(port, true);
        let anonymous = ask(port, false);
        assert!(
            authenticated.contains("verified:"),
            "the certificate-bearing connection was not verified\n{authenticated}",
        );
        assert!(
            anonymous.contains("anonymous") && !anonymous.contains("verified:"),
            "the second connection inherited the first connection's identity\n{anonymous}",
        );
    }
}
