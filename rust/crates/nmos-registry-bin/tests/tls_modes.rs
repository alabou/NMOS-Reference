// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The security modes each interface supports, through real handshakes.
//!
//! Port of `test_tls_registry.py`. Real TLS servers carrying the real routers,
//! driven by a real client, so a mode is proven by what a peer can actually do
//! rather than by what a configuration struct says.
//!
//! The two interfaces support deliberately different sets, and the point of
//! organising the tests this way is to prove exactly that:
//!
//! * **Registration** -- plain HTTP, server-authenticated TLS, mutual TLS, and
//!   **never** OAuth 2.0. `NMOS With Control Plane Security.md:105`: "The IS-04
//!   Registration API MUST not require the NMOS Nodes to use OAuth 2.0
//!   authorizations." Those are the Registry Access Policy values 0, 1 and 2.
//! * **Query** -- the same three, plus OAuth 2.0 over each, for the five-mode
//!   matrix a Node's own API supports.
//!
//! # Two independent switches, deliberately
//!
//! The TLS layer's `CERT_NONE`/`CERT_OPTIONAL`/`CERT_REQUIRED` and the
//! application's `client_auth_required` are set separately here, as Python sets
//! them, because the interesting configuration is the mismatched one:
//! `CERT_OPTIONAL` admits an anonymous connection so that reads work, and the
//! application then refuses the writes. Wiring the two together would make that
//! posture -- the one `--queryOptionalClientAuth` exists for -- unexpressible.
//!
//! # Both certificate flavours
//!
//! Every case runs against the RSA and the EC identity. They exercise different
//! code in the key loader and different signature algorithms in the handshake,
//! and until this file existed only RSA had ever been tried.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::panic
)]

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Arc;

use nmos_registry::registry::Registry;
use nmos_registry_bin::listen::serve_tls;
use nmos_registry_bin::tls::server_context;
use nmos_registry_core::store::RegistryStore;
use nmos_registry_http::oauth2::{Jwks, SharedJwks};
use nmos_registry_http::security::InterfaceSecurity;
use nmos_registry_http::serve::{Assembly, Ports};
use tokio::net::TcpListener;

/// The identity the registry serves as.
const SERIAL: &str = "SNX00000";
/// The identity the client presents, when it presents one.
const CLIENT_SERIAL: &str = "SNX00001";

/// `rsa` or `ec`; every case runs both.
type Flavor = &'static str;
const FLAVORS: [Flavor; 2] = ["rsa", "ec"];

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

/// `.pem` for RSA, `.ec.pem` for EC -- the suffix convention on disk.
fn suffix(flavor: Flavor, extension: &str) -> String {
    if flavor == "ec" {
        format!("ec.{extension}")
    } else {
        extension.to_owned()
    }
}

fn server_chain(flavor: Flavor) -> PathBuf {
    certs().join(format!(
        "pem/ExampleDeviceServer.ABC.{SERIAL}.chain.{}",
        suffix(flavor, "pem"),
    ))
}

fn server_key(flavor: Flavor) -> PathBuf {
    certs().join(format!(
        "key/ExampleDeviceServer.ABC.{SERIAL}.{}",
        suffix(flavor, "key"),
    ))
}

fn client_chain(flavor: Flavor) -> PathBuf {
    certs().join(format!(
        "pem/ExampleDeviceClient.ABC.{CLIENT_SERIAL}.chain.{}",
        suffix(flavor, "pem"),
    ))
}

fn client_key(flavor: Flavor) -> PathBuf {
    certs().join(format!(
        "key/ExampleDeviceClient.ABC.{CLIENT_SERIAL}.{}",
        suffix(flavor, "key"),
    ))
}

fn root_ca(flavor: Flavor) -> PathBuf {
    certs().join(if flavor == "ec" {
        "ExampleRootCA.ec.pem"
    } else {
        "ExampleRootCA.pem"
    })
}

/// Whether this flavour's PKI and `curl` are both present.
fn available(flavor: Flavor) -> bool {
    for path in [
        server_chain(flavor),
        server_key(flavor),
        client_chain(flavor),
        client_key(flavor),
        root_ca(flavor),
    ] {
        if !path.is_file() {
            eprintln!("skipping {flavor}: {} is not present", path.display());
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
        eprintln!("skipping: no curl to drive the handshake with");
        return false;
    }
    true
}

/// What the TLS layer asks of a client.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Tls {
    /// `CERT_NONE` -- no certificate requested.
    ServerOnly,
    /// `CERT_OPTIONAL` -- requested, verified if sent, not required.
    Optional,
    /// `CERT_REQUIRED` -- the handshake fails without one.
    Required,
}

/// Which interface the rig serves.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Interface {
    Registration,
    Query,
}

/// A live registry on an ephemeral port.
struct Rig {
    port: u16,
    flavor: Flavor,
}

/// Start one interface with a given TLS mode and application posture.
///
/// `client_auth_required` is the *application* switch and is independent of
/// `tls`; see the module docs.
async fn start(
    flavor: Flavor,
    interface: Interface,
    tls: Tls,
    client_auth_required: bool,
    oauth2: bool,
    keys: SharedJwks,
) -> Rig {
    let anchors: Vec<PathBuf> = match tls {
        Tls::ServerOnly => Vec::new(),
        Tls::Optional | Tls::Required => vec![root_ca(flavor)],
    };
    let (context, _, _) = server_context(
        &[(server_chain(flavor), server_key(flavor))],
        &anchors,
        tls == Tls::Optional,
        None,
    )
    .expect("the listener configures");

    let store = RegistryStore::with_intervals(12, 12);
    let mut assembly = Assembly::new(
        Registry::new(store),
        "8c4d1e70-6b3a-4f52-9d81-2e7c5a0b3f14".to_owned(),
    );
    assembly.registration_security = InterfaceSecurity {
        client_auth_required,
        // Never, on this interface. The constructor would normally enforce it;
        // the struct literal here is the rig's, and it keeps `oauth2: false`
        // for the same reason -- TR-10-SEC:105.
        oauth2: false,
        serial_number: SERIAL.to_owned(),
        ..InterfaceSecurity::default()
    };
    assembly.query_security = InterfaceSecurity {
        client_auth_required,
        oauth2,
        serial_number: SERIAL.to_owned(),
        oauth2_keys: keys,
        ..InterfaceSecurity::default()
    };

    let ports = Ports {
        registration: 0,
        query: 0,
        websocket: 8448,
    };
    let apps = assembly.routers(ports, true);
    let app = match interface {
        Interface::Registration => apps.registration,
        Interface::Query => apps.query,
    };

    let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
    let port = listener.local_addr().expect("addr").port();
    // The assembly owns the store; keeping it alive for the task's lifetime is
    // what the `move` does.
    tokio::spawn(async move {
        let _assembly = assembly;
        let _ = serve_tls(listener, Arc::new(context), app).await;
    });
    Rig { port, flavor }
}

/// The outcome of one request.
struct Answer {
    /// The HTTP status, or 0 when the connection never got that far.
    status: u16,
    /// Everything curl said, for a failure message worth reading.
    output: String,
}

impl Answer {
    /// Whether the handshake itself failed, as against being refused by the
    /// application.
    fn handshake_failed(&self) -> bool {
        self.status == 0
    }
}

impl Rig {
    /// One request, optionally presenting a client certificate.
    fn request(&self, method: &str, path: &str, body: Option<&str>, cert: bool) -> Answer {
        let name = "Example.Company.Device.Server.ABC.SNX00000.example.com";
        let mut command = Command::new("curl");
        command
            .arg("-sS")
            .arg("--cacert")
            .arg(root_ca(self.flavor))
            .arg("--resolve")
            .arg(format!("{name}:{}:127.0.0.1", self.port))
            .arg("-o")
            .arg("/dev/null")
            .arg("-w")
            .arg("%{http_code}")
            .arg("-X")
            .arg(method);
        if cert {
            // `-cert` alone sends the leaf only, and these identities are
            // signed by an intermediate; the server trusts the root and nothing
            // else, so without the chain it answers `unknown ca`.
            command
                .arg("--cert")
                .arg(client_chain(self.flavor))
                .arg("--cert-type")
                .arg("PEM")
                .arg("--key")
                .arg(client_key(self.flavor));
        }
        if let Some(body) = body {
            command
                .arg("-H")
                .arg("Content-Type: application/json")
                .arg("-d")
                .arg(body);
        }
        command.arg(format!("https://{name}:{}{path}", self.port));

        let output = command.output().expect("curl runs");
        let combined = format!(
            "{}{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr),
        );
        let status = String::from_utf8_lossy(&output.stdout)
            .trim()
            .parse()
            .unwrap_or(0);
        Answer {
            status,
            output: combined,
        }
    }

    fn get(&self, path: &str, cert: bool) -> Answer {
        self.request("GET", path, None, cert)
    }

    fn post(&self, path: &str, body: &str, cert: bool) -> Answer {
        self.request("POST", path, Some(body), cert)
    }
}

const REG_BASE: &str = "/x-nmos/registration/v1.3";
const QUERY_BASE: &str = "/x-nmos/query/v1.3";

/// A Node body that satisfies `node.json`, matching `make_node`.
fn node_body() -> String {
    let id = uuid::Uuid::new_v4();
    format!(
        r#"{{"type":"node","data":{{
            "id":"{id}","version":"0:0","label":"test-node",
            "description":"registry test node","tags":{{}},
            "href":"http://192.0.2.1:8080/","caps":{{}},
            "api":{{"versions":["v1.3"],
                   "endpoints":[{{"host":"192.0.2.1","port":8080,"protocol":"http"}}]}},
            "services":[],"clocks":[],"interfaces":[]}}}}"#
    )
}

fn subscription_body() -> &'static str {
    r#"{"max_update_rate_ms":100,"resource_path":"/nodes","params":{},
        "persist":false,"secure":true}"#
}

// -- Registration: RAP 1, server-authenticated TLS -------------------------

#[tokio::test(flavor = "multi_thread")]
async fn registration_over_server_tls_accepts_a_client_with_no_certificate() {
    // RAP 1. The common deployment: the Node authenticates the registry and
    // registers without an identity of its own.
    for flavor in FLAVORS {
        if !available(flavor) {
            continue;
        }
        let rig = start(
            flavor,
            Interface::Registration,
            Tls::ServerOnly,
            false,
            false,
            SharedJwks::empty(),
        )
        .await;
        let answer = rig.post(&format!("{REG_BASE}/resource"), &node_body(), false);
        assert_eq!(
            answer.status, 201,
            "{flavor}: a registration over server-authenticated TLS was refused\n{}",
            answer.output,
        );
    }
}

// -- Registration: RAP 2, mutual TLS ---------------------------------------

#[tokio::test(flavor = "multi_thread")]
async fn registration_over_mutual_tls_accepts_a_valid_client_certificate() {
    for flavor in FLAVORS {
        if !available(flavor) {
            continue;
        }
        let rig = start(
            flavor,
            Interface::Registration,
            Tls::Required,
            true,
            false,
            SharedJwks::empty(),
        )
        .await;
        let answer = rig.post(&format!("{REG_BASE}/resource"), &node_body(), true);
        assert_eq!(
            answer.status, 201,
            "{flavor}: a valid client certificate was not accepted\n{}",
            answer.output,
        );
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn mutual_tls_refuses_a_missing_certificate_at_the_handshake() {
    // `CERT_REQUIRED`: the refusal happens below HTTP, so there is no status
    // code at all. That is the difference from the application-layer gate
    // below, and it is why read-only access is impossible in this mode.
    for flavor in FLAVORS {
        if !available(flavor) {
            continue;
        }
        let rig = start(
            flavor,
            Interface::Registration,
            Tls::Required,
            true,
            false,
            SharedJwks::empty(),
        )
        .await;
        let answer = rig.post(&format!("{REG_BASE}/resource"), &node_body(), false);
        assert!(
            answer.handshake_failed(),
            "{flavor}: a client with no certificate reached HTTP (status {}) \
             on a CERT_REQUIRED listener\n{}",
            answer.status,
            answer.output,
        );
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn the_application_enforces_client_auth_when_tls_is_permissive() {
    // The mismatched posture, and the one that matters: `CERT_OPTIONAL` lets
    // the connection up so reads can work, and the application refuses the
    // write with 401. Wiring the two switches together would make this
    // unexpressible -- and `--queryOptionalClientAuth` exists for it.
    for flavor in FLAVORS {
        if !available(flavor) {
            continue;
        }
        let rig = start(
            flavor,
            Interface::Registration,
            Tls::Optional,
            true,
            false,
            SharedJwks::empty(),
        )
        .await;
        let answer = rig.post(&format!("{REG_BASE}/resource"), &node_body(), false);
        assert_eq!(
            answer.status, 401,
            "{flavor}: the application did not refuse an unauthenticated write\n{}",
            answer.output,
        );
        // And the same connection may still read.
        let read = rig.get(&format!("{REG_BASE}/"), false);
        assert_eq!(
            read.status, 200,
            "{flavor}: a read was refused although only writes need a certificate\n{}",
            read.output,
        );
    }
}

// -- Registration never requires OAuth 2.0 ---------------------------------

#[tokio::test(flavor = "multi_thread")]
async fn registration_needs_no_token_even_when_oauth2_is_configured() {
    // TR-10-SEC:105. `InterfaceSecurity::registration` cannot even express the
    // non-compliant configuration; this proves the interface behaves that way
    // with a keyset present and OAuth 2.0 configured elsewhere in the process.
    for flavor in FLAVORS {
        if !available(flavor) {
            continue;
        }
        let rig = start(
            flavor,
            Interface::Registration,
            Tls::ServerOnly,
            false,
            // The Query side is configured for OAuth 2.0 with keys available.
            true,
            SharedJwks::with(Jwks::default()),
        )
        .await;
        let answer = rig.post(&format!("{REG_BASE}/resource"), &node_body(), false);
        assert_eq!(
            answer.status, 201,
            "{flavor}: Registration demanded a bearer token\n{}",
            answer.output,
        );
    }
}

// -- Query: the TLS modes --------------------------------------------------

#[tokio::test(flavor = "multi_thread")]
async fn query_reads_over_server_tls() {
    for flavor in FLAVORS {
        if !available(flavor) {
            continue;
        }
        let rig = start(
            flavor,
            Interface::Query,
            Tls::ServerOnly,
            false,
            false,
            SharedJwks::empty(),
        )
        .await;
        let answer = rig.get(&format!("{QUERY_BASE}/"), false);
        assert_eq!(answer.status, 200, "{flavor}: {}", answer.output);
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn query_reads_are_allowed_without_a_certificate_under_optional_mtls() {
    // NAP 1, "Unrestricted Read Only": the read is granted without a client
    // certificate. `Node Reservation.md:41-45`.
    for flavor in FLAVORS {
        if !available(flavor) {
            continue;
        }
        let rig = start(
            flavor,
            Interface::Query,
            Tls::Optional,
            true,
            false,
            SharedJwks::empty(),
        )
        .await;
        let answer = rig.get(&format!("{QUERY_BASE}/nodes"), false);
        assert_eq!(
            answer.status, 200,
            "{flavor}: a read-only request was refused\n{}",
            answer.output,
        );
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn a_subscription_write_needs_a_certificate_and_succeeds_with_one() {
    // Both halves of the same posture, so the refusal cannot be mistaken for a
    // listener that refuses everything.
    for flavor in FLAVORS {
        if !available(flavor) {
            continue;
        }
        let rig = start(
            flavor,
            Interface::Query,
            Tls::Optional,
            true,
            false,
            SharedJwks::empty(),
        )
        .await;
        let refused = rig.post(
            &format!("{QUERY_BASE}/subscriptions"),
            subscription_body(),
            false,
        );
        assert_eq!(
            refused.status, 401,
            "{flavor}: an unauthenticated subscription write was allowed\n{}",
            refused.output,
        );

        let allowed = rig.post(
            &format!("{QUERY_BASE}/subscriptions"),
            subscription_body(),
            true,
        );
        assert_eq!(
            allowed.status, 201,
            "{flavor}: a certificate-bearing subscription write was refused\n{}",
            allowed.output,
        );
    }
}

// -- Query: OAuth 2.0 over TLS ---------------------------------------------

#[tokio::test(flavor = "multi_thread")]
async fn query_without_a_bearer_is_refused_when_oauth2_is_on() {
    for flavor in FLAVORS {
        if !available(flavor) {
            continue;
        }
        let rig = start(
            flavor,
            Interface::Query,
            Tls::ServerOnly,
            false,
            true,
            SharedJwks::with(Jwks::default()),
        )
        .await;
        let answer = rig.get(&format!("{QUERY_BASE}/"), false);
        assert_eq!(
            answer.status, 401,
            "{flavor}: a request with no bearer token was served\n{}",
            answer.output,
        );
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn query_fails_closed_when_no_keys_have_been_fetched() {
    // TR-10-SEC §14.3.2: until the first fetch succeeds, bearer access is
    // refused. The dangerous alternative -- serving while the keyset is empty
    // -- is what this pins shut.
    for flavor in FLAVORS {
        if !available(flavor) {
            continue;
        }
        let rig = start(
            flavor,
            Interface::Query,
            Tls::ServerOnly,
            false,
            true,
            SharedJwks::empty(),
        )
        .await;
        let answer = rig.get(&format!("{QUERY_BASE}/"), false);
        assert_eq!(
            answer.status, 401,
            "{flavor}: the Query API served a request with no keys available\n{}",
            answer.output,
        );
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn oauth2_closes_the_read_that_optional_mtls_would_have_left_open() {
    // The interaction worth checking: under `--queryOptionalClientAuth` alone a
    // read is granted without a certificate, but with OAuth 2.0 on it still
    // needs a token. Otherwise turning OAuth 2.0 on would silently leave every
    // read unauthenticated.
    for flavor in FLAVORS {
        if !available(flavor) {
            continue;
        }
        let rig = start(
            flavor,
            Interface::Query,
            Tls::Optional,
            true,
            true,
            SharedJwks::with(Jwks::default()),
        )
        .await;
        let answer = rig.get(&format!("{QUERY_BASE}/nodes"), false);
        assert_eq!(
            answer.status, 401,
            "{flavor}: a read stayed open although OAuth 2.0 is enabled\n{}",
            answer.output,
        );
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn a_bogus_bearer_is_refused_even_on_a_certificate_bearing_connection() {
    // OAuth 2.0 and mutual TLS together: holding a valid client certificate
    // must not excuse a token that does not verify.
    for flavor in FLAVORS {
        if !available(flavor) {
            continue;
        }
        let rig = start(
            flavor,
            Interface::Query,
            Tls::Optional,
            true,
            true,
            SharedJwks::with(Jwks::default()),
        )
        .await;
        let name = "Example.Company.Device.Server.ABC.SNX00000.example.com";
        let output = Command::new("curl")
            .arg("-sS")
            .arg("--cacert")
            .arg(root_ca(flavor))
            .arg("--resolve")
            .arg(format!("{name}:{}:127.0.0.1", rig.port))
            .arg("--cert")
            .arg(client_chain(flavor))
            .arg("--key")
            .arg(client_key(flavor))
            .arg("-H")
            .arg("Authorization: Bearer not.a.token")
            .arg("-o")
            .arg("/dev/null")
            .arg("-w")
            .arg("%{http_code}")
            .arg(format!("https://{name}:{}{QUERY_BASE}/", rig.port))
            .output()
            .expect("curl runs");
        let status: u16 = String::from_utf8_lossy(&output.stdout)
            .trim()
            .parse()
            .unwrap_or(0);
        assert_eq!(
            status, 401,
            "{flavor}: a bogus token was accepted on an mTLS connection",
        );
    }
}
