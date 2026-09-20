// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The supervisor against a real etcd binary.
//!
//! The ownership rule is the whole of what this module is for, and it is not
//! decidable without a real process: **stop what you started, never stop what
//! you adopted.** Getting it wrong either orphans a process holding the client
//! port and the data-directory lock, or kills a service-managed etcd out from
//! under systemd on a registry restart.
//!
//! Skips itself when `.etcd/etcd` is absent, as the Python suite does.

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

use nmos_cluster::{ClusterLayout, Member};
use nmos_etcd::supervisor::{
    EtcdSupervisor, ProcessOwnership, SupervisorConfig, parse_etcd_version,
    require_supported_version,
};

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(3)
        .expect("the repository root")
        .to_path_buf()
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
    fn new(tag: &str) -> Self {
        static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
        let path = std::env::temp_dir().join(format!(
            "nmos-etcd-sup-{tag}-{}-{}",
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

/// A one-member layout on free ports, bootstrapping a fresh cluster.
fn layout(client: u16, peer: u16) -> ClusterLayout {
    let member = Member {
        name: "nmos-registry-sup".to_owned(),
        host: "127.0.0.1".to_owned(),
        client_port: client,
        peer_port: peer,
        bind_address: "127.0.0.1".to_owned(),
    };
    ClusterLayout {
        members: vec![member.clone()],
        local: member,
        token: "nmos-supervisor-test".to_owned(),
        namespace: "/sup".to_owned(),
        tls: false,
    }
}

fn config(scratch: &Scratch, client: u16, peer: u16, bootstrap: bool) -> SupervisorConfig {
    let mut config = SupervisorConfig::new(
        layout(client, peer),
        etcd_binary()
            .expect("checked by the caller")
            .to_string_lossy()
            .into_owned(),
        scratch.0.join("member"),
    );
    config.bootstrap = bootstrap;
    // Plaintext: this suite is about process ownership, and the certificate
    // set has its own end-to-end coverage.
    config.tls = false;
    config.startup_timeout = Duration::from_secs(30);
    config
}

fn runtime() -> tokio::runtime::Runtime {
    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .expect("a runtime")
}

fn skip_without_etcd() -> bool {
    if etcd_binary().is_none() {
        eprintln!("skipped: .etcd/etcd is not present (run ./install-etcd.sh)");
        return true;
    }
    false
}

#[test]
fn a_launched_member_is_started_and_stopped() {
    if skip_without_etcd() {
        return;
    }
    let scratch = Scratch::new("launch");
    let (client, peer) = (free_port(), free_port());

    runtime().block_on(async {
        let supervisor = Arc::new(EtcdSupervisor::new(config(&scratch, client, peer, true)));
        let ownership = supervisor.start().await.expect("etcd starts");
        assert_eq!(ownership, ProcessOwnership::Launched);
        assert!(supervisor.owns_process().await);

        // It is really serving.
        assert!(
            std::net::TcpStream::connect(("127.0.0.1", client)).is_ok(),
            "the member is not listening on its client port",
        );

        supervisor.stop().await;
        // Stopping a launched member really stops it.
        tokio::time::sleep(Duration::from_millis(500)).await;
        assert!(
            std::net::TcpStream::connect(("127.0.0.1", client)).is_err(),
            "a launched member outlived the supervisor that started it",
        );
    });
}

#[test]
fn a_matching_member_is_adopted_and_left_running() {
    // The half that protects a service-managed etcd: this supervisor did not
    // start it, so it must not stop it.
    if skip_without_etcd() {
        return;
    }
    let scratch = Scratch::new("adopt");
    let (client, peer) = (free_port(), free_port());

    runtime().block_on(async {
        // One supervisor launches.
        let owner = Arc::new(EtcdSupervisor::new(config(&scratch, client, peer, true)));
        assert_eq!(
            owner.start().await.expect("etcd starts"),
            ProcessOwnership::Launched,
        );

        // A second, configured identically, finds it and adopts.
        let adopter = Arc::new(EtcdSupervisor::new(config(&scratch, client, peer, false)));
        let ownership = adopter.start().await.expect("adopts");
        assert_eq!(ownership, ProcessOwnership::Adopted);
        assert!(!adopter.owns_process().await);

        // Stopping the adopter leaves the member running.
        adopter.stop().await;
        tokio::time::sleep(Duration::from_millis(500)).await;
        assert!(
            std::net::TcpStream::connect(("127.0.0.1", client)).is_ok(),
            "the adopter stopped a member it did not start",
        );

        // The one that started it still can.
        owner.stop().await;
        tokio::time::sleep(Duration::from_millis(500)).await;
        assert!(std::net::TcpStream::connect(("127.0.0.1", client)).is_err());
    });
}

#[test]
fn a_member_with_a_different_name_is_refused_not_adopted() {
    // Adopting the wrong member would make this registry serve another
    // cluster's data.
    if skip_without_etcd() {
        return;
    }
    let scratch = Scratch::new("refuse");
    let (client, peer) = (free_port(), free_port());

    runtime().block_on(async {
        let owner = Arc::new(EtcdSupervisor::new(config(&scratch, client, peer, true)));
        owner.start().await.expect("etcd starts");

        let mut other = config(&scratch, client, peer, false);
        other.layout.local.name = "someone-elses-member".to_owned();
        other.layout.members[0].name = "someone-elses-member".to_owned();
        let stranger = Arc::new(EtcdSupervisor::new(other));

        let refusal = stranger
            .start()
            .await
            .expect_err("a differently named member must not be adopted");
        assert!(refusal.0.contains("Refusing to adopt"), "{}", refusal.0,);
        assert!(
            refusal.0.contains("someone-elses-member"),
            "the refusal must name what was expected: {}",
            refusal.0,
        );

        owner.stop().await;
    });
}

#[test]
fn bootstrap_on_a_populated_data_directory_is_refused() {
    // Bootstrapping an existing member creates a *new* cluster whose data is
    // the old member's, which is how a cluster silently forks.
    if skip_without_etcd() {
        return;
    }
    let scratch = Scratch::new("fork");
    let (client, peer) = (free_port(), free_port());

    runtime().block_on(async {
        // Populate the directory by running once.
        let first = Arc::new(EtcdSupervisor::new(config(&scratch, client, peer, true)));
        first.start().await.expect("etcd starts");
        first.stop().await;
        tokio::time::sleep(Duration::from_millis(500)).await;

        // Now ask to bootstrap again on the same directory.
        let again = Arc::new(EtcdSupervisor::new(config(&scratch, client, peer, true)));
        let refusal = again
            .start()
            .await
            .expect_err("bootstrapping a populated directory forks the cluster");
        assert!(refusal.0.contains("is not empty"), "{}", refusal.0,);
        assert!(
            refusal.0.contains("forks the cluster"),
            "the refusal must say why: {}",
            refusal.0,
        );

        // And the directory was NOT deleted to make the problem go away.
        assert!(
            scratch.0.join("member").is_dir(),
            "the supervisor deleted a data directory",
        );
    });
}

#[test]
fn the_argv_is_derived_entirely_from_the_layout() {
    if skip_without_etcd() {
        return;
    }
    let scratch = Scratch::new("argv");
    let (client, peer) = (free_port(), free_port());

    runtime().block_on(async {
        let supervisor = EtcdSupervisor::new(config(&scratch, client, peer, true));
        let argv = supervisor.build_argv().await.expect("an argv");
        let joined = argv.join(" ");

        // A fresh cluster bootstraps; everything else is "existing".
        assert!(joined.contains("--initial-cluster-state new"), "{joined}");
        assert!(
            joined.contains("--initial-cluster-token nmos-supervisor-test"),
            "{joined}",
        );
        assert!(
            joined.contains(&format!("http://127.0.0.1:{client}")),
            "{joined}"
        );
        assert!(
            joined.contains(&format!("http://127.0.0.1:{peer}")),
            "{joined}"
        );
        // Plaintext here, so none of the TLS flags may appear.
        assert!(!joined.contains("--cert-file"), "{joined}");
        assert!(!joined.contains("--client-cert-auth"), "{joined}");
    });
}

#[test]
fn the_tls_argv_carries_the_allowed_hostname() {
    // The control that stops any device sharing the Product CA from writing to
    // the registry database: the CA alone is not enough, the certificate must
    // also carry the etcd SAN.
    if skip_without_etcd() {
        return;
    }
    let scratch = Scratch::new("tls");
    let (client, peer) = (free_port(), free_port());

    runtime().block_on(async {
        let mut cfg = config(&scratch, client, peer, true);
        cfg.tls = true;
        cfg.certificate = "/tmp/cert.pem".to_owned();
        cfg.key = "/tmp/key.pem".to_owned();
        cfg.trusted_root_ca = vec!["/tmp/ca.pem".to_owned()];
        cfg.certificate_name = "Example.Company.Device.Etcd.ABC.example.com".to_owned();
        cfg.client_crl_file = "/tmp/client.crl".to_owned();
        cfg.peer_crl_file = "/tmp/peer.crl".to_owned();

        let supervisor = EtcdSupervisor::new(cfg);
        let joined = supervisor.build_argv().await.expect("an argv").join(" ");

        for expected in [
            "--cert-file /tmp/cert.pem",
            "--key-file /tmp/key.pem",
            "--trusted-ca-file /tmp/ca.pem",
            "--client-cert-auth",
            "--peer-cert-file /tmp/cert.pem",
            "--peer-trusted-ca-file /tmp/ca.pem",
            "--peer-client-cert-auth",
            "--tls-min-version TLS1.2",
            "--client-cert-allowed-hostname Example.Company.Device.Etcd.ABC.example.com",
            "--peer-cert-allowed-hostname Example.Company.Device.Etcd.ABC.example.com",
            "--client-crl-file /tmp/client.crl",
            "--peer-crl-file /tmp/peer.crl",
        ] {
            assert!(joined.contains(expected), "missing {expected} in {joined}");
        }
        // One certificate serves all four roles, so the peer flags name the
        // same file the client ones do.
        assert_eq!(joined.matches("/tmp/cert.pem").count(), 2, "{joined}");
    });
}

#[test]
fn tls_without_a_certificate_set_is_refused_before_anything_starts() {
    if skip_without_etcd() {
        return;
    }
    let scratch = Scratch::new("nocert");
    let (client, peer) = (free_port(), free_port());

    runtime().block_on(async {
        let mut cfg = config(&scratch, client, peer, true);
        cfg.tls = true;
        let supervisor = EtcdSupervisor::new(cfg);
        let refusal = supervisor
            .build_argv()
            .await
            .expect_err("TLS with no certificate must not launch");
        assert!(refusal.0.contains("--etcdCertificate"), "{}", refusal.0);
    });
}

#[test]
fn several_roots_become_one_trust_store_beside_the_data_directory() {
    // etcd takes ONE `--trusted-ca-file`. Passing only the first of several
    // would split the trust store in half: this member would reject peers the
    // registry's own client channel, in the same process, accepts.
    if skip_without_etcd() {
        return;
    }
    let scratch = Scratch::new("roots");
    let (client, peer) = (free_port(), free_port());

    runtime().block_on(async {
        let first = scratch.0.join("a.pem");
        let second = scratch.0.join("b.pem");
        // No trailing newline on the first: two roots concatenated without one
        // produce a single unparseable block.
        std::fs::write(&first, b"-----A-----").unwrap();
        std::fs::write(&second, b"-----B-----\n").unwrap();

        let mut cfg = config(&scratch, client, peer, true);
        cfg.tls = true;
        cfg.certificate = "/tmp/cert.pem".to_owned();
        cfg.key = "/tmp/key.pem".to_owned();
        cfg.trusted_root_ca = vec![
            first.to_string_lossy().into_owned(),
            second.to_string_lossy().into_owned(),
        ];

        let supervisor = EtcdSupervisor::new(cfg);
        let argv = supervisor.build_argv().await.expect("an argv");
        let index = argv
            .iter()
            .position(|item| item == "--trusted-ca-file")
            .expect("a trust store");
        let bundle = PathBuf::from(&argv[index + 1]);

        let contents = std::fs::read(&bundle).expect("the bundle exists");
        assert_eq!(contents, b"-----A-----\n-----B-----\n");

        // Beside the data directory, not inside it: inside, it would make an
        // empty data directory non-empty and trip the bootstrap refusal.
        assert_eq!(bundle.parent(), scratch.0.join("member").parent());
        assert!(!bundle.starts_with(scratch.0.join("member")));
    });
}

#[test]
fn the_version_gate_matches_the_shipped_binary() {
    // The floor is 3.6 because 3.5 spells the progress-notify flag
    // `--experimental-...`. The pinned binary must clear it, or the whole
    // design's watch fence rests on a flag that is not there.
    if skip_without_etcd() {
        return;
    }
    let output = std::process::Command::new(etcd_binary().unwrap())
        .arg("--version")
        .output()
        .expect("etcd --version");
    let text = String::from_utf8_lossy(&output.stdout);
    let version = text
        .lines()
        .find_map(|line| line.strip_prefix("etcd Version: "))
        .expect("a version line")
        .trim();

    require_supported_version(version)
        .unwrap_or_else(|exc| panic!("the shipped etcd {version} is refused: {}", exc.0));
    let parsed = parse_etcd_version(version).unwrap();
    assert!(
        parsed[0] >= 3 && (parsed[0] > 3 || parsed[1] >= 6),
        "{parsed:?}"
    );
}
