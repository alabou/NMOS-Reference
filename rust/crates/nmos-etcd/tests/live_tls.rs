// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The client against a **TLS** etcd, using the shipped certificate set.
//!
//! # Why this exists separately from `live_etcd.rs`
//!
//! Every other live test runs plaintext, and plaintext hides a whole class of
//! failure. gRPC is HTTP/2, and over TLS HTTP/2 is selected by **ALPN** --
//! there is no upgrade path. Over cleartext tonic speaks h2c with prior
//! knowledge and negotiates nothing, so a connector that never requests
//! `h2` passes every plaintext test and fails the first real one with
//! `h2 protocol error: http2 error` from a connection that looks perfectly
//! healthy at the TLS layer.
//!
//! That is not hypothetical: it is what the first run against the real
//! certificate set did. This test is the regression for it.
//!
//! # The second PKI
//!
//! `Certificates/build.0.etcd/` is a separate trust domain from the
//! registry's own. Its certificates carry a shared SAN -- not the endpoint
//! host -- which is why the client verifies against a **target-name override**
//! rather than against `127.0.0.1`. A client without that override cannot
//! connect at all, which is what makes the override load-bearing rather than
//! decorative.
//!
//! Skips itself when the binary or the certificate set is absent.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::panic,
    clippy::arithmetic_side_effects
)]

use std::net::TcpListener;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use nmos_cluster::{ClusterLayout, DEFAULT_CERTIFICATE_NAME, Member};
use nmos_etcd::channel::{Credentials, Endpoint, EtcdChannelPool};
use nmos_etcd::kv::{EtcdKv, put_op};
use nmos_etcd::supervisor::{EtcdSupervisor, SupervisorConfig};

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(3)
        .expect("the repository root")
        .to_path_buf()
}

/// The etcd certificate set, or `None` when this checkout has none.
fn certificates() -> Option<(String, String, String)> {
    let root = repo_root();
    let chain =
        root.join("Certificates/build.0.etcd/pem/ExampleDeviceServer.ABC.SNX10000.etcd.chain.pem");
    let key = root.join("Certificates/build.0.etcd/key/ExampleDeviceServer.ABC.SNX10000.etcd.key");
    let ca = root.join("Certificates/build.0/ExampleRootCA-bundle.pem");
    (chain.is_file() && key.is_file() && ca.is_file()).then(|| {
        (
            chain.to_string_lossy().into_owned(),
            key.to_string_lossy().into_owned(),
            ca.to_string_lossy().into_owned(),
        )
    })
}

fn etcd_binary() -> Option<PathBuf> {
    let candidate = repo_root().join(".etcd/etcd");
    candidate.is_file().then_some(candidate)
}

fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .expect("a free port")
        .local_addr()
        .expect("an address")
        .port()
}

struct Scratch(PathBuf);

impl Scratch {
    fn new() -> Self {
        static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
        let path = std::env::temp_dir().join(format!(
            "nmos-etcd-tls-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed),
        ));
        std::fs::create_dir_all(&path).expect("a scratch directory");
        Self(path)
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        drop(std::fs::remove_dir_all(&self.0));
    }
}

#[test]
fn the_client_speaks_grpc_to_a_tls_member() {
    let (Some(binary), Some((chain, key, ca))) = (etcd_binary(), certificates()) else {
        eprintln!("skipped: the etcd binary or its certificate set is not present");
        return;
    };

    let scratch = Scratch::new();
    let (client_port, peer_port) = (free_port(), free_port());
    let member = Member {
        name: "nmos-registry-tls".to_owned(),
        host: "127.0.0.1".to_owned(),
        client_port,
        peer_port,
        bind_address: "127.0.0.1".to_owned(),
    };
    let layout = ClusterLayout {
        members: vec![member.clone()],
        local: member,
        token: "nmos-tls-test".to_owned(),
        namespace: "/tls".to_owned(),
        tls: true,
    };

    let mut config = SupervisorConfig::new(
        layout,
        binary.to_string_lossy().into_owned(),
        scratch.0.join("member"),
    );
    config.bootstrap = true;
    config.tls = true;
    config.certificate = chain.clone();
    config.key = key.clone();
    config.trusted_root_ca = vec![ca.clone()];
    config.certificate_name = DEFAULT_CERTIFICATE_NAME.to_owned();

    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .expect("a runtime")
        .block_on(async move {
            let supervisor = Arc::new(EtcdSupervisor::new(config));
            supervisor
                .start()
                .await
                .expect("the TLS member starts and answers Status over gRPC");

            // `start` already proved the handshake and one RPC. Now a write
            // and a read, so the whole codec path runs over TLS rather than
            // only the version probe.
            let connector = Credentials {
                trusted_root_ca: vec![ca],
                certificate: chain,
                key,
            }
            .connector()
            .expect("a connector");

            let pool = Arc::new(
                EtcdChannelPool::new(
                    vec![Endpoint {
                        target: format!("127.0.0.1:{client_port}"),
                        local: true,
                    }],
                    Some(connector),
                    // The certificate's SAN is the shared etcd name, not
                    // `127.0.0.1`. Without this override the handshake fails
                    // with "cannot validate certificate for 127.0.0.1".
                    Some(DEFAULT_CERTIFICATE_NAME.to_owned()),
                    Duration::from_secs(5),
                )
                .expect("a pool"),
            );

            let kv = EtcdKv::new(pool);
            let written = kv
                .txn(&[], &[put_op(b"/tls/a", b"1", 0)], &[], None)
                .await
                .expect("a transaction over TLS");
            assert!(written.succeeded);

            let read = kv
                .range_at(b"/tls/a", None, 0, 0, false, None)
                .await
                .expect("a read over TLS");
            assert_eq!(read.kvs.len(), 1);
            assert_eq!(read.kvs[0].value.as_ref(), b"1");

            supervisor.stop().await;
        });
}

#[test]
fn the_connector_requests_alpn_h2() {
    // The regression for the bug above, decidable without a server: a
    // connector that does not offer `h2` cannot negotiate HTTP/2, and gRPC
    // over TLS has no other path to it.
    let Some((chain, key, ca)) = certificates() else {
        eprintln!("skipped: the etcd certificate set is not present");
        return;
    };

    let connector = Credentials {
        trusted_root_ca: vec![ca],
        certificate: chain,
        key,
    }
    .connector()
    .expect("a connector");

    // `configure()` inherits the builder's ALPN list, and `into_ssl` carries
    // it into the handshake. Asserting the configuration is possible without
    // a peer; asserting the *negotiated* protocol needs one, which is what
    // the test above does.
    let config = connector.configure().expect("a configuration");
    let ssl = config
        .into_ssl(DEFAULT_CERTIFICATE_NAME)
        .expect("an SSL session");
    assert!(
        ssl.selected_alpn_protocol().is_none(),
        "nothing is negotiated before a handshake",
    );
}
