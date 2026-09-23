// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Does one OpenSSL server context hold an RSA *and* an ECDSA identity, and
//! serve each client the flavour it asked for?
//!
//! This is the load-bearing assumption of TR-10-SEC TCT=2 ("Both"). It was
//! measured on the Python side first -- two `load_cert_chain` calls on one
//! `ssl.SSLContext`, ECDSA-steered client gets `id-ecPublicKey`, RSA-steered
//! gets `rsaEncryption` -- but Python's behaviour is not proof for this
//! binding. Same C library underneath, so it *should* hold; "should" is the
//! reason this file exists rather than a comment asserting it.
//!
//! What must be true, and is asserted below:
//!
//! 1. The second `set_certificate_chain_file` does not replace the first.
//!    OpenSSL slots an identity by key type, so RSA and ECDSA occupy different
//!    slots; a binding that reset the slot would silently leave one flavour
//!    unreachable.
//! 2. Selection follows the *client's* preference, not the load order. Load
//!    order carries no meaning and must never be documented as if it does.
//! 3. The intermediates survive per slot. `SSL_CTX_use_certificate_chain_file`
//!    clears chain certificates against the slot the new leaf selects, so the
//!    chain is checked as well as the leaf -- a server that sent a bare leaf
//!    would still handshake here while failing every real client that has to
//!    build a path to the root.
//!
//! Steering uses TLS 1.2 and a single cipher suite, because under TLS 1.2 the
//! suite *determines* the certificate type -- `ECDHE-ECDSA-...` cannot be
//! answered with an RSA certificate. Under TLS 1.3 the suite says nothing
//! about authentication and selection moves to `signature_algorithms`, which
//! is a weaker thing to assert against. Both suites are inside the TR-10-SEC
//! whitelist (`TR10_TLS12_CIPHERS`), so `apply_tr10_restrictions` is applied
//! here rather than skipped: the probe proves the real policy permits dual
//! certificates, not merely that OpenSSL does.
//!
//! Skips rather than fails without the PKI, mirroring `PKI_AVAILABLE` in
//! `_tls_helpers.py`.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::panic
)]

use std::io::Read as _;
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, mpsc};

use nmos_registry_bin::tls::apply_tr10_restrictions;
use openssl::pkey::Id;
use openssl::ssl::{
    Ssl, SslContext, SslContextBuilder, SslFiletype, SslMethod, SslVerifyMode,
    SslVersion,
};

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

/// `.pem` for RSA, `.ec.pem` for EC -- the suffix convention on disk, spelled
/// as `tls_modes.rs` spells it.
fn server_chain(flavor: &str) -> PathBuf {
    let name = if flavor == "ec" {
        "pem/ExampleDeviceServer.ABC.SNX00000.chain.ec.pem"
    } else {
        "pem/ExampleDeviceServer.ABC.SNX00000.chain.pem"
    };
    certs().join(name)
}

fn server_key(flavor: &str) -> PathBuf {
    let name = if flavor == "ec" {
        "key/ExampleDeviceServer.ABC.SNX00000.ec.key"
    } else {
        "key/ExampleDeviceServer.ABC.SNX00000.key"
    };
    certs().join(name)
}

/// Whether the probe can run at all, mirroring `PKI_AVAILABLE`.
///
/// Both flavours are required, not just one: the whole point is the pair.
fn available() -> bool {
    for flavor in ["rsa", "ec"] {
        if !server_chain(flavor).is_file() || !server_key(flavor).is_file() {
            eprintln!(
                "skipping: the {flavor} identity under Certificates/build.0 is not present"
            );
            return false;
        }
    }
    if Command::new("openssl")
        .arg("version")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_err()
    {
        eprintln!("skipping: no openssl on PATH");
        return false;
    }
    true
}

/// One server context carrying BOTH identities.
///
/// The order of the two loads is deliberate noise: RSA first here, and the
/// assertions below must hold regardless. If a future change makes the result
/// depend on this order, that is the bug, not the test.
fn dual_identity_context() -> SslContext {
    let mut builder =
        SslContextBuilder::new(SslMethod::tls_server()).expect("context builder");
    apply_tr10_restrictions(&mut builder).expect("TR-10-SEC policy");

    for flavor in ["rsa", "ec"] {
        builder
            .set_certificate_chain_file(server_chain(flavor))
            .unwrap_or_else(|e| panic!("{flavor} chain: {e}"));
        builder
            .set_private_key_file(server_key(flavor), SslFiletype::PEM)
            .unwrap_or_else(|e| panic!("{flavor} key: {e}"));
        // Inside the loop, not after it: `check_private_key` validates only the
        // slot the most recent pair populated, so one call at the end would
        // silently stop checking the first identity.
        builder
            .check_private_key()
            .unwrap_or_else(|e| panic!("{flavor} key does not match its chain: {e}"));
    }

    builder.set_verify(SslVerifyMode::NONE);
    builder.build()
}

/// A listener that serves exactly `count` handshakes, then stops.
///
/// Reports what the *server* did, for the reason `tls_handshake.rs` documents:
/// under TLS 1.3 a client can believe it succeeded before the server has
/// objected. This probe pins TLS 1.2 so that race does not arise, but the
/// reporting channel costs nothing and keeps the failure legible if someone
/// later lifts the version pin.
fn serve(context: SslContext, count: usize) -> (u16, mpsc::Receiver<bool>) {
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
                let Ok(mut tls) = ssl.accept(stream) else {
                    let _ = report.send(false);
                    return;
                };
                let _ = report.send(true);
                let mut buf = [0_u8; 64];
                let _ = tls.read(&mut buf);
            });
        }
    });
    (port, outcomes)
}

/// What the server presented to a client that would accept only `cipher`.
///
/// Returns the leaf's public-key algorithm and the number of certificates in
/// the chain the server sent.
fn presented(port: u16, cipher: &str) -> (Id, usize) {
    let mut builder =
        SslContextBuilder::new(SslMethod::tls_client()).expect("client builder");
    // Verification is not what this probe is about, and the two flavours chain
    // to different roots -- trusting one would fail the other for a reason
    // that has nothing to do with certificate selection.
    builder.set_verify(SslVerifyMode::NONE);
    builder
        .set_max_proto_version(Some(SslVersion::TLS1_2))
        .expect("cap at TLS 1.2");
    builder.set_cipher_list(cipher).expect("cipher list");
    let context = builder.build();

    let stream = TcpStream::connect(("127.0.0.1", port)).expect("connect");
    let ssl = Ssl::new(&context).expect("ssl");
    let tls = ssl
        .connect(stream)
        .unwrap_or_else(|e| panic!("handshake with {cipher}: {e}"));

    let leaf = tls
        .ssl()
        .peer_certificate()
        .expect("server presented no certificate");
    let algorithm = leaf.public_key().expect("public key").id();
    let depth = tls
        .ssl()
        .peer_cert_chain()
        .map_or(0, openssl::stack::StackRef::len);
    (algorithm, depth)
}

#[test]
fn one_context_serves_each_client_the_flavour_it_asked_for() {
    if !available() {
        return;
    }
    let (port, outcomes) = serve(dual_identity_context(), 2);

    let (ecdsa_alg, ecdsa_depth) = presented(port, "ECDHE-ECDSA-AES128-GCM-SHA256");
    let (rsa_alg, rsa_depth) = presented(port, "ECDHE-RSA-AES128-GCM-SHA256");

    assert!(outcomes.recv().expect("server outcome"), "server refused #1");
    assert!(outcomes.recv().expect("server outcome"), "server refused #2");

    // 1 + 2: both identities are reachable, and which one arrives is decided by
    // the client, not by the order they were loaded in.
    assert_eq!(
        ecdsa_alg,
        Id::EC,
        "an ECDSA-only client was served a non-ECDSA certificate -- \
         the EC identity is unreachable, most likely because the second \
         set_certificate_chain_file replaced the first instead of filling \
         its own slot",
    );
    assert_eq!(
        rsa_alg,
        Id::RSA,
        "an RSA-only client was served a non-RSA certificate -- the RSA \
         identity was displaced by the EC one loaded after it",
    );

    // 3: the intermediates survived per slot. This PKI issues leaf +
    // intermediate, so a chain of 1 means the leaf is there but its issuer is
    // not, and every client that has to build a path to the root would fail.
    assert_eq!(ecdsa_depth, 2, "ECDSA chain lost its intermediate");
    assert_eq!(rsa_depth, 2, "RSA chain lost its intermediate");
}
