// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! What a real client can actually negotiate with this listener.
//!
//! The Rust counterpart of `test_tr10_tls.py`'s handshake probes, plus the
//! per-group coverage that file leaves to the validator's SEC-8-5 pass.
//! Everything here runs a genuine handshake against a genuine listener via
//! `openssl s_client`, because a whitelist is only worth what a peer observes
//! -- asserting over the configured lists checks what was *asked for*, which is
//! a different and weaker claim.
//!
//! Since the registry moved from rustls to OpenSSL, three of these assertions
//! inverted: `secp521r1`, `x448` and the two `DHE_RSA` suites are no longer
//! absent from the library, so they negotiate here exactly as they do against
//! Python. What still must not negotiate is anything SEC-8-9's closed list
//! leaves out.
//!
//! These skip rather than fail when the PKI or `openssl` is absent, following
//! `PKI_AVAILABLE` in `_tls_helpers.py`: a clone without certificates should
//! still run the rest of the suite.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::panic
)]

use std::io::{Read as _, Write as _};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, mpsc};
use std::time::Duration;

use nmos_registry_bin::tls::{ClientAuth, server_context};
use openssl::ssl::{Ssl, SslContext};

/// `nmos-reference/`, three levels above this crate.
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
    certs().join("pem/ExampleDeviceServer.ABC.SNX00000.chain.pem")
}

fn server_key() -> PathBuf {
    certs().join("key/ExampleDeviceServer.ABC.SNX00000.key")
}

/// Whether the probes can run at all, mirroring `PKI_AVAILABLE`.
fn available() -> bool {
    if !server_chain().is_file() || !server_key().is_file() {
        eprintln!("skipping: the PKI under Certificates/build.0 is not present");
        return false;
    }
    if Command::new("openssl")
        .arg("version")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_err()
    {
        eprintln!("skipping: no openssl on PATH to probe with");
        return false;
    }
    true
}

/// A listener that serves exactly `count` handshakes, then stops.
///
/// Bound to port 0 so concurrent tests cannot collide.
fn serve(context: SslContext, count: usize) -> u16 {
    let (port, outcomes) = serve_reporting(context, count);
    // These callers read the client's view instead. Dropping the receiver only
    // makes the listener's reporting sends fail, which it ignores.
    drop(outcomes);
    port
}

/// `serve`, plus one `bool` per connection saying whether it *accepted*.
///
/// Needed wherever the assertion is about what the listener did rather than
/// about what the client printed. Under TLS 1.3 the client finishes its side
/// and reports `CONNECTION ESTABLISHED` before the server has looked at the
/// certificate it asked for, so a refusal reaches the client as a later,
/// asynchronous alert -- and `openssl s_client`, whose stdin `probe` closes
/// straight away, is free to send `close_notify` and exit before that alert
/// arrives.
///
/// Measured rather than assumed: under load, 2 runs in 30 of the
/// `CERT_REQUIRED` probe ended `CONNECTION ESTABLISHED ... DONE` with no alert
/// line at all, while the listener had refused every single time. The refusal
/// was never in doubt; only whether the client stayed alive long enough to
/// print it. Asking the listener removes the race rather than widening the
/// window in which it is lost.
fn serve_reporting(context: SslContext, count: usize) -> (u16, mpsc::Receiver<bool>) {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let port = listener.local_addr().expect("addr").port();
    let context = Arc::new(context);
    let (report, outcomes) = mpsc::channel();
    std::thread::spawn(move || {
        for _ in 0..count {
            let Ok((stream, _)) = listener.accept() else {
                return;
            };
            let context = Arc::clone(&context);
            let report = report.clone();
            std::thread::spawn(move || {
                let Ok(ssl) = Ssl::new(&context) else {
                    let _ = report.send(false);
                    return;
                };
                // A refused handshake is an ordinary outcome here -- half these
                // probes exist to cause one -- so the error is reported rather
                // than raised.
                let Ok(mut tls) = ssl.accept(stream) else {
                    let _ = report.send(false);
                    return;
                };
                let _ = report.send(true);
                let mut buf = [0_u8; 64];
                let _ = tls.read(&mut buf);
                let _ = tls.write(b"\n");
            });
        }
    });
    (port, outcomes)
}

/// One `openssl s_client` probe. Returns its combined output.
fn probe(port: u16, args: &[&str]) -> String {
    let mut command = Command::new("openssl");
    command
        .arg("s_client")
        .arg("-connect")
        .arg(format!("127.0.0.1:{port}"))
        .arg("-brief")
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = command.spawn().expect("spawn openssl");
    drop(child.stdin.take());
    let output = child.wait_with_output().expect("openssl finished");
    format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr),
    )
}

fn handshook(output: &str) -> bool {
    output.contains("Protocol version") || output.contains("CONNECTION ESTABLISHED")
}

/// Whether the server tore the connection down for want of a client
/// certificate.
///
/// Needed because "did the handshake succeed" is not a yes-or-no question under
/// TLS 1.3. The client finishes its side and prints `CONNECTION ESTABLISHED`
/// before the server has looked at the certificate it asked for, so the refusal
/// arrives afterwards as a fatal alert -- measured as `tlsv13 alert certificate
/// required`, alert 116. Reading only the `CONNECTION ESTABLISHED` line would
/// record a `CERT_REQUIRED` listener as having admitted an anonymous client,
/// which is the one thing it must never do.
fn refused_for_missing_certificate(output: &str) -> bool {
    output.contains("certificate required")
        || output.contains("alert number 116")
        || output.contains("peer did not return a certificate")
}

/// No client-certificate trust anchor: server-authenticated TLS only.
///
/// Spelled out because `&[]` alone leaves the element type ambiguous.
const NO_ANCHORS: &[&Path] = &[];

fn plain_context() -> SslContext {
    let (context, _, mode) =
        server_context(&[(server_chain(), server_key())], NO_ANCHORS, false, None)
            .expect("the listener configures");
    assert_eq!(mode, ClientAuth::None, "no anchor was given");
    context
}

// -- key exchange groups (SEC-8-5, SEC-8-9) -------------------------------

#[test]
fn every_group_sec_8_5_lists_negotiates() {
    if !available() {
        return;
    }
    // SEC-8-5: shall support 25519 and secp256r1, should support secp521r1 and
    // 448. All four, not just the two `shall`s -- this is the requirement the
    // rustls build could not meet, and the reason the port moved to OpenSSL.
    let port = serve(plain_context(), 16);
    for group in ["x25519", "prime256v1", "secp521r1", "x448"] {
        let output = probe(port, &["-groups", group]);
        assert!(
            handshook(&output),
            "SEC-8-5 lists {group} and it did not negotiate\n{output}",
        );
    }
}

#[test]
fn groups_outside_the_closed_list_are_refused() {
    if !available() {
        return;
    }
    // SEC-8-9: "only the cipher suites and key exchange groups listed ... shall
    // be used". A live Python listener accepts all three of these, because
    // CPython exposes no `set_groups` and OpenSSL's defaults are wider than the
    // list. `set_groups_list` closes it here.
    let port = serve(plain_context(), 16);
    for group in ["secp384r1", "ffdhe2048", "ffdhe3072"] {
        let output = probe(port, &["-groups", group]);
        assert!(
            !handshook(&output),
            "{group} is outside SEC-8-9's closed list but negotiated anyway \
             -- the group pinning is not in effect\n{output}",
        );
    }
}

// -- cipher suites (SEC-8-6, SEC-8-8, SEC-8-9) ----------------------------

#[test]
fn the_mandatory_tls12_cipher_negotiates() {
    if !available() {
        return;
    }
    // SEC-8-6 shall. `test_mandatory_tls12_cipher_handshake_succeeds` asserts
    // the same against the Python listener.
    let port = serve(plain_context(), 4);
    let output = probe(port, &["-tls1_2", "-cipher", "ECDHE-RSA-AES128-GCM-SHA256"]);
    assert!(
        handshook(&output),
        "the one TLS 1.2 suite TR-10-SEC requires did not negotiate\n{output}",
    );
}

#[test]
fn the_dhe_suites_are_advertised_but_do_not_negotiate_on_either_implementation() {
    if !available() {
        return;
    }
    // SEC-8-6 lists both of these as `should`, and **neither implementation
    // meets it**. This test records that rather than asserting the outcome
    // anyone reading the cipher list would expect.
    //
    // The cause is not the cipher list and not the group list. Classic TLS 1.2
    // DHE sends explicit parameters in ServerKeyExchange, so it needs DH
    // parameters configured on the context -- nothing to do with
    // `set_groups_list`, which is why pinning the groups to SEC-8-5's all-EC
    // list does not prevent it. Measured: `openssl s_server` negotiates
    // `DHE-RSA-AES128-GCM-SHA256` fine under `-groups X25519:P-256:P-521:X448`,
    // announcing "Using default temp DH parameters" as it starts. So SEC-8-5
    // and SEC-8-9 do not conflict with SEC-8-6 here; there is no contradiction
    // in the specification.
    //
    // What neither implementation does is set those parameters.
    // `apply_tr10_tls_restrictions` never calls `load_dh_params`, which CPython
    // does expose, and this context never calls `set_tmp_dh`. Both therefore
    // *advertise* two suites they cannot complete, so a client offering only
    // DHE gets a handshake failure rather than a clean "no shared cipher".
    //
    // Left matching Python deliberately, since the two must behave alike.
    // Closing it is a one-line change on each side -- `ctx.load_dh_params(…)`
    // there, `set_tmp_dh(&Dh::get_2048_256()?)` here -- and wants deciding
    // together, not silently on one side.
    let port = serve(plain_context(), 8);
    for cipher in ["DHE-RSA-AES128-GCM-SHA256", "DHE-RSA-AES256-GCM-SHA384"] {
        let output = probe(port, &["-tls1_2", "-cipher", cipher]);
        assert!(
            !handshook(&output),
            "{cipher} negotiated -- DH parameters have been configured, so \
             this listener now satisfies SEC-8-6 where the Python one still \
             does not. Confirm both sides were changed together\n{output}",
        );
    }
}

#[test]
fn the_ccm_suite_negotiates_although_python_cannot_offer_it() {
    if !available() {
        return;
    }
    // SEC-8-8 should. `tr10_tls.py` asks for this through `set_ciphersuites`,
    // CPython has no such method, and so the Python registry does not offer it.
    // This is the one section-8 requirement this listener satisfies and the
    // reference implementation does not.
    let port = serve(plain_context(), 4);
    let output = probe(
        port,
        &["-tls1_3", "-ciphersuites", "TLS_AES_128_CCM_SHA256"],
    );
    assert!(
        handshook(&output),
        "TLS_AES_128_CCM_SHA256 did not negotiate, so the SEC-8-8 gain of \
         moving to OpenSSL is not actually there\n{output}",
    );
}

#[test]
fn a_prohibited_tls12_cipher_is_refused() {
    if !available() {
        return;
    }
    // `test_prohibited_tls12_cipher_handshake_refused`'s counterpart. CBC and
    // the non-AEAD suites are outside the whitelist.
    let port = serve(plain_context(), 8);
    for cipher in ["ECDHE-RSA-AES128-SHA", "AES128-SHA"] {
        let output = probe(port, &["-tls1_2", "-cipher", cipher]);
        assert!(
            !handshook(&output),
            "{cipher} is prohibited by SEC-8-9 but negotiated\n{output}",
        );
    }
}

#[test]
fn a_tls13_suite_outside_the_list_is_refused() {
    if !available() {
        return;
    }
    // The TLS 1.3 half of SEC-8-9, which only became enforceable with
    // `set_ciphersuites`. Python cannot make this assertion hold.
    let port = serve(plain_context(), 4);
    let output = probe(
        port,
        &["-tls1_3", "-ciphersuites", "TLS_AES_128_CCM_8_SHA256"],
    );
    assert!(
        !handshook(&output),
        "TLS_AES_128_CCM_8_SHA256 is not on SEC-8-8's list but negotiated\n{output}",
    );
}

#[test]
fn tls_1_1_and_below_are_refused() {
    if !available() {
        return;
    }
    // SEC-8-2: TLS 1.2 is the floor. Python sets `minimum_version = TLSv1_2`;
    // this sets `set_min_proto_version(TLS1_2)`, the same call underneath.
    let port = serve(plain_context(), 4);
    let output = probe(port, &["-tls1_1"]);
    assert!(
        !handshook(&output),
        "a TLS 1.1 client completed a handshake\n{output}",
    );
}

// -- client authentication ------------------------------------------------

#[test]
fn cert_required_refuses_a_client_that_offers_nothing() {
    if !available() {
        return;
    }
    // `CERT_REQUIRED`. This is why read-only access is impossible once client
    // auth is mandatory, and therefore why `--queryOptionalClientAuth` exists.
    let anchor = certs().join("ExampleRootCA.pem");
    if !anchor.is_file() {
        eprintln!("skipping: no root CA in the PKI");
        return;
    }
    let (context, _, mode) =
        server_context(&[(server_chain(), server_key())], &[&anchor], false, None)
            .expect("the listener configures");
    assert_eq!(mode, ClientAuth::Required);

    let (port, outcomes) = serve_reporting(context, 4);
    let output = probe(port, &[]);
    // Ask the listener, not the client -- see `serve_reporting` for why the
    // client's own output cannot answer this reliably.
    let accepted = outcomes
        .recv_timeout(Duration::from_secs(10))
        .expect("the listener reported the handshake outcome");
    assert!(
        !accepted,
        "a client presenting no certificate was not refused by a \
         CERT_REQUIRED listener\n{output}",
    );
}

#[test]
fn cert_optional_admits_a_client_that_offers_nothing() {
    if !available() {
        return;
    }
    // `CERT_OPTIONAL`. The connection comes up and the verb gate decides --
    // the arrangement the operational requirement depends on.
    let anchor = certs().join("ExampleRootCA.pem");
    if !anchor.is_file() {
        eprintln!("skipping: no root CA in the PKI");
        return;
    }
    let (context, _, mode) = server_context(&[(server_chain(), server_key())], &[&anchor], true, None)
        .expect("the listener configures");
    assert_eq!(mode, ClientAuth::Optional);

    let port = serve(context, 4);
    let output = probe(port, &[]);
    assert!(
        handshook(&output),
        "CERT_OPTIONAL refused an anonymous client, so no read-only access is \
         possible\n{output}",
    );
    assert!(
        !refused_for_missing_certificate(&output),
        "CERT_OPTIONAL sent a `certificate required` alert -- the connection \
         only appeared to come up\n{output}",
    );
}

// -- the GCRL -------------------------------------------------------------

#[test]
fn a_missing_gcrl_refuses_to_configure_a_listener() {
    if !available() {
        return;
    }
    // SEC-14.3.3.5-3 fail-closed, at the point it actually matters: not in a
    // helper, but on the path that builds a listening socket.
    let error = server_context(
        &[(server_chain(), server_key())],
        NO_ANCHORS,
        false,
        Some(Path::new("/nonexistent/gcrl.pem")),
    )
    .expect_err("a declared but missing CRL must refuse to serve");
    assert!(error.to_string().contains("fail-closed"), "{error}");
}
