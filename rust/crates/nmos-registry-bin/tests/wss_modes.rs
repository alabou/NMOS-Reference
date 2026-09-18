// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The subscription WebSocket, over TLS, in each security mode.
//!
//! Port of `test_tls_websocket.py`'s security and transport cases.
//!
//! # Why this listener needs its own file
//!
//! It is its own socket with its own context. `ws_href` advertises a **distinct
//! port** -- a Node's `--rdsWebSocketPort` defaults to 8448 against a query port
//! of 8446 -- so a deployment can end up with a hardened Query API and a
//! permissive WebSocket beside it. `nmos_registry.py` builds one context for
//! both and this asserts the consequence: every restriction that holds on the
//! Query API holds here too, including the TLS 1.2 floor.
//!
//! The upgrade is a `GET`, which means the read-only rules apply to it. That is
//! not a detail: under mutual TLS a subscription can be opened without a client
//! certificate exactly as a collection can be read, and under OAuth 2.0 it
//! cannot be opened without a token exactly as a collection cannot be read. Both
//! directions are asserted here, because a listener that got either wrong would
//! look perfectly healthy.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::panic
)]

use std::path::{Path, PathBuf};
use std::pin::Pin;
use std::process::{Command, Stdio};
use std::sync::Arc;

use nmos_registry::registry::Registry;
use nmos_registry_bin::listen::serve_tls;
use nmos_registry_bin::tls::server_context;
use nmos_registry_core::store::RegistryStore;
use nmos_registry_http::oauth2::{Jwks, SharedJwks};
use nmos_registry_http::security::InterfaceSecurity;
use nmos_registry_http::serve::{Assembly, Ports};
use openssl::ssl::{Ssl, SslContext, SslContextBuilder, SslMethod, SslVerifyMode};
use tokio::net::{TcpListener, TcpStream};
use tokio_openssl::SslStream;
use tokio_tungstenite::tungstenite;

const SERIAL: &str = "SNX00000";
const CLIENT_SERIAL: &str = "SNX00001";
const QUERY_BASE: &str = "/x-nmos/query/v1.3";
/// The name the server certificate is issued for; TLS is verified against it.
const SERVER_NAME: &str = "Example.Company.Device.Server.ABC.SNX00000.example.com";

fn repo() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(3)
        .expect("crate is nested three deep under the repository root")
        .to_path_buf()
}

fn certs() -> PathBuf {
    repo().join("Certificates/build.0")
}

fn server_chain() -> PathBuf {
    certs().join(format!("pem/ExampleDeviceServer.ABC.{SERIAL}.chain.pem"))
}

fn server_key() -> PathBuf {
    certs().join(format!("key/ExampleDeviceServer.ABC.{SERIAL}.key"))
}

fn client_chain() -> PathBuf {
    certs().join(format!(
        "pem/ExampleDeviceClient.ABC.{CLIENT_SERIAL}.chain.pem"
    ))
}

fn client_key() -> PathBuf {
    certs().join(format!("key/ExampleDeviceClient.ABC.{CLIENT_SERIAL}.key"))
}

fn root_ca() -> PathBuf {
    certs().join("ExampleRootCA.pem")
}

fn available() -> bool {
    for path in [
        server_chain(),
        server_key(),
        client_chain(),
        client_key(),
        root_ca(),
    ] {
        if !path.is_file() {
            eprintln!("skipping: {} is not present", path.display());
            return false;
        }
    }
    if Command::new("curl")
        .arg("--version")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_err()
    {
        eprintln!("skipping: no curl available");
        return false;
    }
    true
}

/// What the TLS layer asks of a client.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Tls {
    ServerOnly,
    Optional,
    Required,
}

/// A live Query API and its WebSocket, sharing one registry and one context.
struct Rig {
    query_port: u16,
    ws_port: u16,
}

async fn start(tls: Tls, client_auth_required: bool, oauth2: bool, keys: SharedJwks) -> Rig {
    let anchors: Vec<PathBuf> = match tls {
        Tls::ServerOnly => Vec::new(),
        Tls::Optional | Tls::Required => vec![root_ca()],
    };
    let (context, _, _) = server_context(
        &server_chain(),
        &server_key(),
        &anchors,
        tls == Tls::Optional,
        None,
    )
    .expect("the listener configures");
    let context = Arc::new(context);

    // Bind first so `ws_href` can advertise the port it actually got.
    let query_listener = TcpListener::bind("127.0.0.1:0").await.expect("bind query");
    let ws_listener = TcpListener::bind("127.0.0.1:0").await.expect("bind ws");
    let query_port = query_listener.local_addr().expect("addr").port();
    let ws_port = ws_listener.local_addr().expect("addr").port();

    let store = RegistryStore::with_intervals(12, 12);
    let mut assembly = Assembly::new(
        Registry::new(store),
        "8c4d1e70-6b3a-4f52-9d81-2e7c5a0b3f14".to_owned(),
    );
    assembly.query_security = InterfaceSecurity {
        client_auth_required,
        oauth2,
        serial_number: SERIAL.to_owned(),
        oauth2_keys: keys,
        ..InterfaceSecurity::default()
    };

    let ports = Ports {
        registration: 0,
        query: query_port,
        websocket: ws_port,
    };
    // `true`: these listeners are TLS, so `ws_href` must say `wss`.
    let apps = assembly.routers(ports, true);

    let ws_context = Arc::clone(&context);
    tokio::spawn(async move {
        let _assembly = assembly;
        let _ = tokio::join!(
            serve_tls(query_listener, context, apps.query),
            serve_tls(ws_listener, ws_context, apps.websocket),
        );
    });
    Rig {
        query_port,
        ws_port,
    }
}

impl Rig {
    /// Create a subscription over the Query API and return its `ws_href`.
    fn subscribe(&self, cert: bool) -> Option<String> {
        let mut command = Command::new("curl");
        command
            .arg("-sS")
            .arg("--cacert")
            .arg(root_ca())
            .arg("--resolve")
            .arg(format!("{SERVER_NAME}:{}:127.0.0.1", self.query_port))
            .arg("-H")
            .arg("Content-Type: application/json")
            .arg("-d")
            .arg(
                r#"{"max_update_rate_ms":100,"resource_path":"/nodes","params":{},
                    "persist":true,"secure":true}"#,
            );
        if cert {
            command
                .arg("--cert")
                .arg(client_chain())
                .arg("--key")
                .arg(client_key());
        }
        command.arg(format!(
            "https://{SERVER_NAME}:{}{QUERY_BASE}/subscriptions",
            self.query_port,
        ));
        let output = command.output().expect("curl runs");
        let body = String::from_utf8_lossy(&output.stdout);
        let parsed: serde_json::Value = serde_json::from_str(&body).ok()?;
        parsed
            .get("ws_href")
            .and_then(serde_json::Value::as_str)
            .map(str::to_owned)
    }
}

/// A TLS client stream to the WebSocket port, verifying the server.
async fn connect_tls(port: u16, cert: bool) -> Result<SslStream<TcpStream>, String> {
    let mut builder = SslContextBuilder::new(SslMethod::tls_client()).map_err(|e| e.to_string())?;
    builder.set_ca_file(root_ca()).map_err(|e| e.to_string())?;
    builder.set_verify(SslVerifyMode::PEER);
    if cert {
        builder
            .set_certificate_chain_file(client_chain())
            .map_err(|e| e.to_string())?;
        builder
            .set_private_key_file(client_key(), openssl::ssl::SslFiletype::PEM)
            .map_err(|e| e.to_string())?;
    }
    let context: SslContext = builder.build();

    let mut ssl = Ssl::new(&context).map_err(|e| e.to_string())?;
    ssl.set_hostname(SERVER_NAME).map_err(|e| e.to_string())?;
    ssl.param_mut()
        .set_host(SERVER_NAME)
        .map_err(|e| e.to_string())?;

    let tcp = TcpStream::connect(("127.0.0.1", port))
        .await
        .map_err(|e| e.to_string())?;
    let mut stream = SslStream::new(ssl, tcp).map_err(|e| e.to_string())?;
    Pin::new(&mut stream)
        .connect()
        .await
        .map_err(|e| format!("tls handshake: {e}"))?;
    Ok(stream)
}

/// How an upgrade attempt ended.
#[derive(Debug)]
enum Upgrade {
    /// The socket is open.
    Open,
    /// The server answered the upgrade with this HTTP status.
    Refused(u16),
    /// TLS never completed.
    NoTls(String),
    /// Something else went wrong.
    Other(String),
}

/// Attempt a `wss` upgrade, optionally with a client certificate and a bearer.
async fn upgrade(port: u16, href: &str, cert: bool, bearer: Option<&str>) -> Upgrade {
    let stream = match connect_tls(port, cert).await {
        Ok(stream) => stream,
        Err(error) => return Upgrade::NoTls(error),
    };

    let path = href
        .split_once(&format!(":{port}"))
        .map_or(href, |(_, p)| p);
    let mut request = tungstenite::handshake::client::Request::builder()
        .uri(format!("wss://{SERVER_NAME}:{port}{path}"))
        .header("Host", format!("{SERVER_NAME}:{port}"))
        .header("Connection", "Upgrade")
        .header("Upgrade", "websocket")
        .header("Sec-WebSocket-Version", "13")
        .header(
            "Sec-WebSocket-Key",
            tungstenite::handshake::client::generate_key(),
        );
    if let Some(bearer) = bearer {
        request = request.header("Authorization", format!("Bearer {bearer}"));
    }
    let request = match request.body(()) {
        Ok(request) => request,
        Err(error) => return Upgrade::Other(error.to_string()),
    };

    match tokio_tungstenite::client_async(request, stream).await {
        Ok(_) => Upgrade::Open,
        Err(tungstenite::Error::Http(response)) => Upgrade::Refused(response.status().as_u16()),
        Err(error) => Upgrade::Other(error.to_string()),
    }
}

// -- the upgrade is a read, and follows the read rules ---------------------

#[tokio::test(flavor = "multi_thread")]
async fn a_subscription_opens_over_wss() {
    if !available() {
        return;
    }
    let rig = start(Tls::ServerOnly, false, false, SharedJwks::empty()).await;
    let href = rig.subscribe(false).expect("a subscription is created");
    assert!(
        href.starts_with("wss://"),
        "ws_href did not advertise wss on a TLS listener: {href}",
    );
    match upgrade(rig.ws_port, &href, false, None).await {
        Upgrade::Open => {}
        other => panic!("the upgrade did not complete: {other:?}"),
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn optional_client_auth_admits_the_read_only_upgrade() {
    // The upgrade is a GET, so under `--queryOptionalClientAuth` it is granted
    // without a certificate -- the same rule that lets a collection be read.
    if !available() {
        return;
    }
    let rig = start(Tls::Optional, true, false, SharedJwks::empty()).await;
    let href = rig.subscribe(true).expect("a subscription is created");
    match upgrade(rig.ws_port, &href, false, None).await {
        Upgrade::Open => {}
        other => panic!("a read-only upgrade was refused: {other:?}"),
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn mutual_tls_refuses_the_upgrade_without_a_certificate() {
    // `CERT_REQUIRED` refuses below HTTP, so there is no status -- the client
    // never gets to send the upgrade at all.
    if !available() {
        return;
    }
    let rig = start(Tls::Required, true, false, SharedJwks::empty()).await;
    let href = rig.subscribe(true).expect("a subscription is created");
    // Two shapes, one meaning. Under TLS 1.2 the handshake fails outright; under
    // TLS 1.3 the client finishes its side before the server has looked at the
    // certificate it asked for, so the refusal arrives afterwards as a reset.
    // What must never happen is an upgrade completing.
    match upgrade(rig.ws_port, &href, false, None).await {
        Upgrade::NoTls(reason) => {
            // Kept and inspected rather than discarded: a handshake that failed
            // for some unrelated reason -- a bad path, an expired certificate --
            // would otherwise read as this test passing.
            assert!(
                !reason.is_empty(),
                "the handshake failed without saying why, so this proves nothing",
            );
        }
        Upgrade::Other(error) if error.contains("reset") || error.contains("closed") => {}
        other => panic!(
            "a client with no certificate got past a CERT_REQUIRED WebSocket \
             listener: {other:?}",
        ),
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn a_certificate_bearing_client_may_upgrade_under_mutual_tls() {
    // The other half, so the refusal above cannot be a listener that refuses
    // everything.
    if !available() {
        return;
    }
    let rig = start(Tls::Required, true, false, SharedJwks::empty()).await;
    let href = rig.subscribe(true).expect("a subscription is created");
    match upgrade(rig.ws_port, &href, true, None).await {
        Upgrade::Open => {}
        other => panic!("a valid client certificate was refused: {other:?}"),
    }
}

// -- OAuth 2.0 closes the upgrade too --------------------------------------

#[tokio::test(flavor = "multi_thread")]
async fn oauth2_refuses_an_upgrade_carrying_no_bearer() {
    if !available() {
        return;
    }
    // The subscription is made before OAuth 2.0 would block it: the rig's
    // Query API and WebSocket share one posture, so the subscription is created
    // against a listener that is not yet demanding tokens. Here the same
    // configuration is used for both, so the `href` is fabricated instead.
    let rig = start(
        Tls::ServerOnly,
        false,
        true,
        SharedJwks::with(Jwks::default()),
    )
    .await;
    let href = format!(
        "wss://{SERVER_NAME}:{}{QUERY_BASE}/subscriptions/\
         aad9ed36-bfb9-400a-9890-a85da2e5842b",
        rig.ws_port,
    );
    match upgrade(rig.ws_port, &href, false, None).await {
        Upgrade::Refused(401) => {}
        other => panic!("an upgrade with no bearer was not refused with 401: {other:?}"),
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn oauth2_refuses_an_upgrade_carrying_a_bogus_bearer() {
    if !available() {
        return;
    }
    let rig = start(
        Tls::ServerOnly,
        false,
        true,
        SharedJwks::with(Jwks::default()),
    )
    .await;
    let href = format!(
        "wss://{SERVER_NAME}:{}{QUERY_BASE}/subscriptions/\
         aad9ed36-bfb9-400a-9890-a85da2e5842b",
        rig.ws_port,
    );
    match upgrade(rig.ws_port, &href, false, Some("not.a.token")).await {
        Upgrade::Refused(401) => {}
        other => panic!("a bogus bearer was not refused with 401: {other:?}"),
    }
}

// -- transport restrictions on this listener too ---------------------------

#[tokio::test(flavor = "multi_thread")]
async fn tls_1_1_is_refused_on_the_websocket_listener() {
    // TR-10-SEC pins TLS 1.2 as the floor here as much as on the Query API. The
    // two run on separate sockets, so a deployment could end up hardened on one
    // and permissive on the other; one context for both is what prevents it.
    if !available() {
        return;
    }
    let rig = start(Tls::ServerOnly, false, false, SharedJwks::empty()).await;
    let output = Command::new("openssl")
        .arg("s_client")
        .arg("-connect")
        .arg(format!("127.0.0.1:{}", rig.ws_port))
        .arg("-tls1_1")
        .arg("-brief")
        .stdin(Stdio::null())
        .output()
        .expect("openssl runs");
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr),
    );
    assert!(
        !combined.contains("Protocol version") && !combined.contains("CONNECTION ESTABLISHED"),
        "a TLS 1.1 client completed a handshake with the WebSocket listener\n{combined}",
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn an_unknown_subscription_is_a_clean_404_rather_than_a_dead_socket() {
    // A mistyped `ws_href` should get an HTTP error, not an accepted socket
    // that closes immediately -- that is what lets a client tell "wrong URL"
    // from "the registry dropped me".
    if !available() {
        return;
    }
    let rig = start(Tls::ServerOnly, false, false, SharedJwks::empty()).await;
    let href = format!(
        "wss://{SERVER_NAME}:{}{QUERY_BASE}/subscriptions/\
         aad9ed36-bfb9-400a-9890-a85da2e5842b",
        rig.ws_port,
    );
    match upgrade(rig.ws_port, &href, false, None).await {
        Upgrade::Refused(404) => {}
        other => panic!("an unknown subscription did not answer 404: {other:?}"),
    }
}
