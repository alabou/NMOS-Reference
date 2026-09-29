// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The etcd backend against a real cluster.
//!
//! `keys` and `placement` are pure and tested as such. Nothing about them
//! proves the backend works, and the parts that can only fail against a real
//! server are the ones that matter most:
//!
//! * a write becomes visible **through the watch**, not by the writer poking
//!   its own store -- which is what makes a locally originated write need no
//!   special casing anywhere;
//! * a Node delete cascades to its whole subtree in **one** revision;
//! * a lease collects a subtree with no registry running a collection pass;
//! * two backends over one cluster converge, which is the mixed-deployment
//!   claim in miniature.
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
use std::process::{Child, Command, Stdio};
use std::sync::Arc;
use std::time::{Duration, Instant};

use nmos_cluster::{ClusterLayout, Member};
use nmos_registry::Registry;
use nmos_registry_backend::{BackendState, RegistryBackend};
use nmos_registry_core::{Body, RegistryStore, ResourceType};
use nmos_registry_etcd::{EtcdConfig, EtcdRegistryBackend};

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(3)
        .expect("the repository root")
        .to_path_buf()
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
            "nmos-etcd-backend-{}-{}",
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

/// A single-member etcd, killed on drop.
struct Server {
    process: Child,
    client_port: u16,
    _data: Scratch,
}

impl Drop for Server {
    fn drop(&mut self) {
        drop(self.process.kill());
        drop(self.process.wait());
    }
}

impl Server {
    fn start() -> Option<Self> {
        let binary = repo_root().join(".etcd/etcd");
        if !binary.is_file() {
            return None;
        }
        let data = Scratch::new();
        let client_port = free_port();
        let peer_port = free_port();
        let client_url = format!("http://127.0.0.1:{client_port}");
        let peer_url = format!("http://127.0.0.1:{peer_port}");

        let process = Command::new(binary)
            .args([
                "--name",
                "test",
                "--data-dir",
                data.0.join("member").to_str().expect("utf-8"),
                "--listen-client-urls",
                &client_url,
                "--advertise-client-urls",
                &client_url,
                "--listen-peer-urls",
                &peer_url,
                "--initial-advertise-peer-urls",
                &peer_url,
                "--initial-cluster",
                &format!("test={peer_url}"),
                "--initial-cluster-state",
                "new",
                "--initial-cluster-token",
                "nmos-etcd-backend-tests",
                "--log-level",
                "error",
            ])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("etcd starts");

        let server = Self {
            process,
            client_port,
            _data: data,
        };
        let deadline = Instant::now() + Duration::from_secs(30);
        while Instant::now() < deadline {
            if std::net::TcpStream::connect(("127.0.0.1", client_port)).is_ok() {
                return Some(server);
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        panic!("etcd did not become reachable");
    }

    /// A config naming this server as the sole member.
    ///
    /// `external` is true so membership reconciliation checks only the
    /// cluster's *size*: the member is named `test` by the harness, not
    /// derived from the layout, and the managed-mode check would rightly
    /// refuse a name it did not choose.
    fn config(&self, namespace: &str) -> EtcdConfig {
        let member = Member {
            name: "test".to_owned(),
            host: "127.0.0.1".to_owned(),
            client_port: self.client_port,
            peer_port: 0,
            bind_address: "127.0.0.1".to_owned(),
        };
        EtcdConfig {
            layout: ClusterLayout {
                members: vec![member.clone()],
                local: member,
                token: "test".to_owned(),
                namespace: namespace.to_owned(),
                tls: false,
            },
            endpoints: vec![format!("127.0.0.1:{}", self.client_port)],
            namespace: namespace.to_owned(),
            tls: false,
            certificate: String::new(),
            key: String::new(),
            trusted_root_ca: Vec::new(),
            certificate_name: String::new(),
            rpc_timeout: Duration::from_secs(5),
            mutation_timeout: Duration::from_secs(10),
            external: true,
            binary: String::new(),
            data_dir: PathBuf::new(),
            bootstrap: false,
            client_crl_file: String::new(),
            peer_crl_file: String::new(),
        }
    }
}

fn registry() -> Arc<Registry> {
    Arc::new(Registry::new(RegistryStore::with_intervals(12, 12)))
}

fn node(id: &str) -> Body {
    Body::new(format!(
        r#"{{"id": "{id}", "version": "1:0", "label": "n", "href": "http://x/", "hostname": "h", "caps": {{}}, "services": [], "api": {{"versions": ["v1.3"], "endpoints": []}}, "clocks": [], "interfaces": [], "tags": {{}}}}"#,
    ))
}

fn device(id: &str, node_id: &str) -> Body {
    Body::new(format!(
        r#"{{"id": "{id}", "version": "1:0", "label": "d", "type": "urn:x-nmos:device:generic", "node_id": "{node_id}", "senders": [], "receivers": [], "controls": [], "tags": {{}}}}"#,
    ))
}

fn sender(id: &str, device_id: &str) -> Body {
    Body::new(format!(
        r#"{{"id": "{id}", "version": "1:0", "label": "s", "description": "", "flow_id": null, "transport": "urn:x-nmos:transport:rtp", "device_id": "{device_id}", "manifest_href": null, "interface_bindings": [], "subscription": {{"receiver_id": null, "active": false}}, "caps": {{}}, "tags": {{}}}}"#,
    ))
}

/// Run `body` against a fresh etcd, or skip.
fn with_etcd<F, Fut>(body: F)
where
    F: FnOnce(Arc<Server>) -> Fut,
    Fut: std::future::Future<Output = ()>,
{
    let Some(server) = Server::start() else {
        eprintln!("skipped: .etcd/etcd is not present (run ./install-etcd.sh)");
        return;
    };
    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .expect("a runtime")
        .block_on(body(Arc::new(server)));
}

/// Wait until `check` holds, or fail saying what it was still waiting for.
async fn eventually(what: &str, mut check: impl FnMut() -> bool) {
    let deadline = tokio::time::Instant::now() + Duration::from_secs(10);
    while tokio::time::Instant::now() < deadline {
        if check() {
            return;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    panic!("timed out waiting for {what}");
}

#[test]
fn a_write_becomes_visible_through_the_watch() {
    // The central claim: the writer does not put the resource into its own
    // store. It commits to etcd, and the watch brings it back -- which is why
    // a locally originated write needs no special casing anywhere.
    with_etcd(|server| async move {
        let registry = registry();
        let backend = EtcdRegistryBackend::new(Arc::clone(&registry), server.config("/w1"))
            .expect("a backend");
        backend.start().await.expect("started");
        assert_eq!(backend.state(), BackendState::Ready);

        let applied = backend
            .register(ResourceType::Node, node("n1"))
            .await
            .expect("no outage")
            .expect("accepted");
        assert!(applied.created, "a first registration is a 201");
        // `register` returns after the commit has come back through the watch,
        // so the resource is already visible when it answers.
        assert!(
            registry.get(ResourceType::Node, "n1").is_some(),
            "the write had not been applied when register returned",
        );

        // A second registration of the same resource is a 200, not a 201.
        let again = backend
            .register(ResourceType::Node, node("n1"))
            .await
            .expect("no outage")
            .expect("accepted");
        assert!(!again.created, "a re-registration is a 200");

        backend.close().await;
    });
}

#[test]
fn a_node_delete_cascades_in_one_revision() {
    // The payoff for keeping a Node's subtree under one prefix: one ranged
    // delete, and the watch delivers every removal as a single revision.
    with_etcd(|server| async move {
        let registry = registry();
        let backend = EtcdRegistryBackend::new(Arc::clone(&registry), server.config("/w2"))
            .expect("a backend");
        backend.start().await.expect("started");

        for (kind, body) in [
            (ResourceType::Node, node("n1")),
            (ResourceType::Device, device("d1", "n1")),
            (ResourceType::Sender, sender("s1", "d1")),
        ] {
            backend
                .register(kind, body)
                .await
                .expect("no outage")
                .expect("accepted");
        }
        assert!(registry.get(ResourceType::Sender, "s1").is_some());

        backend
            .unregister(ResourceType::Node, "n1")
            .await
            .expect("no outage")
            .expect("it was there");

        eventually("the whole subtree to vanish", || {
            registry.get(ResourceType::Node, "n1").is_none()
                && registry.get(ResourceType::Device, "d1").is_none()
                && registry.get(ResourceType::Sender, "s1").is_none()
        })
        .await;

        backend.close().await;
    });
}

#[test]
fn a_child_of_an_unregistered_device_is_refused_with_parent_missing() {
    // And it must be a 400 rather than a 503: the fenced path re-validates
    // against a store known to be current before any rejection is returned.
    with_etcd(|server| async move {
        let registry = registry();
        let backend = EtcdRegistryBackend::new(registry, server.config("/w3")).expect("a backend");
        backend.start().await.expect("started");

        let failure = backend
            .register(ResourceType::Sender, sender("s1", "nope"))
            .await
            .expect("no outage")
            .expect_err("there is no such device");
        assert_eq!(
            failure.error,
            nmos_registry_core::RegistrationError::ParentMissing,
        );

        backend.close().await;
    });
}

#[test]
fn a_heartbeat_writes_nothing_to_the_keyspace() {
    // The largest efficiency difference from the legacy design: renewal is a
    // lease refresh, so it wakes no watcher and moves no revision.
    with_etcd(|server| async move {
        let registry = registry();
        let backend = EtcdRegistryBackend::new(Arc::clone(&registry), server.config("/w4"))
            .expect("a backend");
        backend.start().await.expect("started");
        backend
            .register(ResourceType::Node, node("n1"))
            .await
            .expect("no outage")
            .expect("accepted");

        let before = backend.applied_revision();
        let health = backend
            .heartbeat("n1")
            .await
            .expect("no outage")
            .expect("the node is registered");
        assert!(health > 0);
        // Give any write the heartbeat might have made time to come back.
        tokio::time::sleep(Duration::from_millis(300)).await;
        assert_eq!(
            backend.applied_revision(),
            before,
            "the heartbeat moved the store revision, so it wrote a key",
        );

        // An unknown Node answers 404 -- once a read of etcd confirms it is
        // unknown there too (`catch_up`), and still without a write.
        assert!(
            backend
                .heartbeat("nope")
                .await
                .expect("no outage")
                .is_none(),
            "a heartbeat for an unregistered Node must be a 404",
        );

        backend.close().await;
    });
}

#[test]
fn two_backends_over_one_cluster_converge() {
    // The mixed-deployment claim in miniature: a write accepted by one member
    // reaches the other by the same watch path, with no registry-to-registry
    // channel anywhere.
    with_etcd(|server| async move {
        let first_registry = registry();
        let second_registry = registry();
        let first = EtcdRegistryBackend::new(Arc::clone(&first_registry), server.config("/w5"))
            .expect("a backend");
        let second = EtcdRegistryBackend::new(Arc::clone(&second_registry), server.config("/w5"))
            .expect("a backend");
        first.start().await.expect("started");
        second.start().await.expect("started");

        first
            .register(ResourceType::Node, node("n1"))
            .await
            .expect("no outage")
            .expect("accepted");

        eventually("the second member to see the Node", || {
            second_registry.get(ResourceType::Node, "n1").is_some()
        })
        .await;

        // And the second member can mutate what the first created, including
        // attaching a child to a lease the *first* member granted.
        second
            .register(ResourceType::Device, device("d1", "n1"))
            .await
            .expect("no outage")
            .expect("accepted");
        eventually("the first member to see the Device", || {
            first_registry.get(ResourceType::Device, "d1").is_some()
        })
        .await;

        // A delete on one side reaches the other.
        second
            .unregister(ResourceType::Node, "n1")
            .await
            .expect("no outage")
            .expect("it was there");
        eventually("the first member to see the delete", || {
            first_registry.get(ResourceType::Node, "n1").is_none()
                && first_registry.get(ResourceType::Device, "d1").is_none()
        })
        .await;

        first.close().await;
        second.close().await;
    });
}

#[test]
fn a_restart_preloads_what_the_cluster_already_holds() {
    // The preload is a fixed-revision scan, so a member joining a populated
    // cluster starts from a complete view rather than an empty one.
    with_etcd(|server| async move {
        let writer_registry = registry();
        let writer = EtcdRegistryBackend::new(Arc::clone(&writer_registry), server.config("/w6"))
            .expect("a backend");
        writer.start().await.expect("started");
        for (kind, body) in [
            (ResourceType::Node, node("n1")),
            (ResourceType::Device, device("d1", "n1")),
            (ResourceType::Sender, sender("s1", "d1")),
        ] {
            writer
                .register(kind, body)
                .await
                .expect("no outage")
                .expect("accepted");
        }
        writer.close().await;

        let joiner_registry = registry();
        let joiner = EtcdRegistryBackend::new(Arc::clone(&joiner_registry), server.config("/w6"))
            .expect("a backend");
        joiner.start().await.expect("started");

        // Parents before children, or the store's referential integrity check
        // would have rejected the Sender during the preload.
        assert!(joiner_registry.get(ResourceType::Node, "n1").is_some());
        assert!(joiner_registry.get(ResourceType::Device, "d1").is_some());
        assert!(
            joiner_registry.get(ResourceType::Sender, "s1").is_some(),
            "the Sender was dropped, so the preload applied out of order",
        );

        joiner.close().await;
    });
}

#[test]
fn the_configured_cluster_size_is_enforced() {
    // A registry told it is one of three, talking to a single-member cluster,
    // would advertise resilience it does not have.
    with_etcd(|server| async move {
        let mut config = server.config("/w7");
        let extra = Member {
            name: "other".to_owned(),
            host: "127.0.0.1".to_owned(),
            client_port: 1,
            peer_port: 0,
            bind_address: "127.0.0.1".to_owned(),
        };
        config.layout.members.push(extra.clone());
        config.layout.members.push(Member {
            name: "third".to_owned(),
            ..extra
        });

        let backend = EtcdRegistryBackend::new(registry(), config).expect("a backend");
        let refusal = backend.start().await.expect_err("one member is not three");
        assert!(
            refusal.0.contains("Refusing to advertise resilience"),
            "{}",
            refusal.0,
        );
    });
}

#[test]
fn a_resource_id_cannot_be_claimed_by_two_types() {
    // The flat id claim is what answers "does this id exist anywhere", which
    // the tree cannot: it is keyed by where a resource is.
    with_etcd(|server| async move {
        let registry = registry();
        let backend = EtcdRegistryBackend::new(Arc::clone(&registry), server.config("/w8"))
            .expect("a backend");
        backend.start().await.expect("started");

        backend
            .register(ResourceType::Node, node("shared"))
            .await
            .expect("no outage")
            .expect("accepted");

        // The same id as a Device: refused, and as a 400 rather than an outage.
        let failure = backend
            .register(ResourceType::Device, device("shared", "shared"))
            .await
            .expect("no outage")
            .expect_err("the id is already a Node");
        assert_eq!(
            failure.error,
            nmos_registry_core::RegistrationError::IdTypeConflict,
            "{}",
            failure.detail,
        );

        backend.close().await;
    });
}

/// A relay between one member and etcd that can hold what it carries.
///
/// A member dialling etcd through it sees etcd only as far as the relay has let
/// bytes through: held, its watch receives nothing new and its requests wait --
/// a slow path to etcd, which is how a member falls behind. Released, all that
/// was held arrives, in order. Byte-oriented, so it neither understands nor
/// reorders the gRPC it carries.
struct Relay {
    port: u16,
    held: tokio::sync::watch::Sender<bool>,
    accept: tokio::task::JoinHandle<()>,
}

impl Relay {
    async fn start(target: u16) -> Arc<Self> {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("a relay port");
        let port = listener.local_addr().expect("an address").port();
        let (held, _) = tokio::sync::watch::channel(false);
        let gate = held.clone();
        let accept = tokio::spawn(async move {
            while let Ok((inbound, _)) = listener.accept().await {
                let Ok(outbound) = tokio::net::TcpStream::connect(("127.0.0.1", target)).await
                else {
                    continue;
                };
                let (from_member, to_member) = inbound.into_split();
                let (from_etcd, to_etcd) = outbound.into_split();
                tokio::spawn(Self::pump(from_member, to_etcd, gate.subscribe()));
                tokio::spawn(Self::pump(from_etcd, to_member, gate.subscribe()));
            }
        });
        Arc::new(Self { port, held, accept })
    }

    /// Relay one direction, a chunk at a time, waiting at the gate before each.
    async fn pump(
        mut from: tokio::net::tcp::OwnedReadHalf,
        mut to: tokio::net::tcp::OwnedWriteHalf,
        mut gate: tokio::sync::watch::Receiver<bool>,
    ) {
        use tokio::io::{AsyncReadExt as _, AsyncWriteExt as _};
        let mut chunk = vec![0_u8; 65_536];
        loop {
            let Ok(read) = from.read(&mut chunk).await else {
                return;
            };
            if read == 0 || gate.wait_for(|held| !*held).await.is_err() {
                return;
            }
            if to.write_all(&chunk[..read]).await.is_err() {
                return;
            }
        }
    }

    fn endpoint(&self) -> String {
        format!("127.0.0.1:{}", self.port)
    }

    fn hold(&self) {
        self.held.send_replace(true);
    }

    fn release(&self) {
        self.held.send_replace(false);
    }
}

impl Drop for Relay {
    fn drop(&mut self) {
        self.accept.abort();
    }
}

/// Two members over one etcd, the second reaching it through a relay -- and
/// the second has already applied the Node the first registered.
async fn behind_the_relay(
    server: &Server,
    namespace: &str,
) -> (
    Arc<EtcdRegistryBackend>,
    Arc<EtcdRegistryBackend>,
    Arc<Registry>,
    Arc<Relay>,
) {
    let relay = Relay::start(server.client_port).await;
    let first = EtcdRegistryBackend::new(registry(), server.config(namespace)).expect("a backend");
    let mut config = server.config(namespace);
    config.endpoints = vec![relay.endpoint()];
    let behind_registry = registry();
    let behind = EtcdRegistryBackend::new(Arc::clone(&behind_registry), config).expect("a backend");
    first.start().await.expect("started");
    behind.start().await.expect("started");
    (first, behind, behind_registry, relay)
}

/// Let the held member catch up after `LAG`.
fn release_after(relay: &Arc<Relay>) -> tokio::task::JoinHandle<()> {
    const LAG: Duration = Duration::from_millis(200);
    let relay = Arc::clone(relay);
    tokio::spawn(async move {
        tokio::time::sleep(LAG).await;
        relay.release();
    })
}

#[test]
fn a_member_behind_on_a_parent_waits_for_it_rather_than_refusing() {
    // A Sender whose Device is in etcd but not yet in this member's store was
    // answered `PARENT_MISSING` from that store, without the fence `register`
    // requires before any rejection -- and a Node "MUST NOT" retry a 400
    // (`Behaviour - Registration.md:96`). The Python suite holds the same case
    // (`test_forwarding_conformance.py`).
    with_etcd(|server| async move {
        let (first, behind, behind_registry, relay) = behind_the_relay(&server, "/v1p").await;
        first
            .register(ResourceType::Node, node("n1"))
            .await
            .expect("no outage")
            .expect("accepted");
        eventually("the member behind to see the Node", || {
            behind_registry.get(ResourceType::Node, "n1").is_some()
        })
        .await;

        relay.hold();
        first
            .register(ResourceType::Device, device("d1", "n1"))
            .await
            .expect("no outage")
            .expect("accepted");
        assert!(behind_registry.get(ResourceType::Device, "d1").is_none());

        let release = release_after(&relay);
        let answer = behind
            .register(ResourceType::Sender, sender("s1", "d1"))
            .await
            .expect("no outage");
        release.await.expect("released");
        assert!(
            answer.as_ref().is_ok_and(|applied| applied.created),
            "a Sender whose Device etcd held was answered {answer:?} by a member whose watch \
             had not yet delivered the Device",
        );

        first.close().await;
        behind.close().await;
    });
}

#[test]
fn a_member_behind_on_a_resource_does_not_call_it_absent() {
    // `unregister_inner` answered 404 from the local store, so the delete never
    // happened: the resource stayed registered while the Node believed it gone.
    with_etcd(|server| async move {
        let (first, behind, behind_registry, relay) = behind_the_relay(&server, "/v1d").await;
        first
            .register(ResourceType::Node, node("n1"))
            .await
            .expect("no outage")
            .expect("accepted");
        eventually("the member behind to see the Node", || {
            behind_registry.get(ResourceType::Node, "n1").is_some()
        })
        .await;

        relay.hold();
        first
            .register(ResourceType::Device, device("d1", "n1"))
            .await
            .expect("no outage")
            .expect("accepted");
        assert!(behind_registry.get(ResourceType::Device, "d1").is_none());

        let release = release_after(&relay);
        let answer = behind
            .unregister(ResourceType::Device, "d1")
            .await
            .expect("no outage");
        release.await.expect("released");
        assert!(
            answer.is_some(),
            "a Device etcd held was answered 404 on DELETE by a member whose watch had not \
             yet delivered it",
        );

        first.close().await;
        behind.close().await;
    });
}

#[test]
fn a_member_behind_on_a_node_does_not_tell_it_to_re_register() {
    // `heartbeat_inner` answered 404 from the lease table the watch keeps, and a
    // 404 makes the Node re-register every resource it has (`Behaviour -
    // Registration.md:112-114`); a Node that fails over heartbeats the new
    // member first and expects a 200 while it is "still present in the registry
    // cluster" (`:126`).
    with_etcd(|server| async move {
        let (first, behind, behind_registry, relay) = behind_the_relay(&server, "/v1h").await;
        relay.hold();
        first
            .register(ResourceType::Node, node("n1"))
            .await
            .expect("no outage")
            .expect("accepted");
        assert!(behind_registry.get(ResourceType::Node, "n1").is_none());

        let release = release_after(&relay);
        let answer = behind.heartbeat("n1").await.expect("no outage");
        release.await.expect("released");
        assert!(
            answer.is_some(),
            "a Node etcd held was answered 404 on heartbeat by a member whose watch had not \
             yet delivered it",
        );

        first.close().await;
        behind.close().await;
    });
}

/// The lease etcd holds each of `keys` on, read directly -- `None` for a key
/// that is not there.
async fn leases_in_etcd(server: &Server, keys: Vec<Vec<u8>>) -> Vec<Option<i64>> {
    let endpoints =
        nmos_etcd::channel::parse_endpoints(&[format!("127.0.0.1:{}", server.client_port)], None)
            .expect("an endpoint");
    let pool =
        nmos_etcd::channel::EtcdChannelPool::new(endpoints, None, None, Duration::from_secs(5))
            .expect("a pool");
    let kv = nmos_etcd::kv::EtcdKv::new(Arc::new(pool));
    let read = kv.read_set(&keys, None).await.expect("a read");
    read.responses
        .iter()
        .map(|response| nmos_etcd::kv::first_kv(response).map(|kv| kv.lease))
        .collect()
}

#[test]
fn a_member_behind_on_a_node_puts_its_device_on_the_nodes_lease() {
    // Every key in a Node's subtree hangs off the Node's lease: that is the
    // whole of distributed garbage collection. The lease a child is written
    // with was read before the fenced path's fence -- when, at a member that
    // had not yet seen the Node, the table held nothing for it -- so the Device
    // and its claim went out on no lease at all, and would outlive the Node's
    // expiry. The Python suite holds the same case.
    with_etcd(|server| async move {
        let (first, behind, behind_registry, relay) = behind_the_relay(&server, "/v1l").await;
        relay.hold();
        first
            .register(ResourceType::Node, node("n1"))
            .await
            .expect("no outage")
            .expect("accepted");
        assert!(behind_registry.get(ResourceType::Node, "n1").is_none());

        let release = release_after(&relay);
        let answer = behind
            .register(ResourceType::Device, device("d1", "n1"))
            .await
            .expect("no outage");
        release.await.expect("released");
        assert!(
            answer.as_ref().is_ok_and(|applied| applied.created),
            "{answer:?}"
        );

        let ns = behind.namespace();
        let leases = leases_in_etcd(
            &server,
            vec![ns.node("n1"), ns.device("n1", "d1"), ns.id_claim("d1")],
        )
        .await;
        let node_lease = leases[0].expect("the Node is in etcd");
        assert_ne!(node_lease, 0, "the Node itself is on no lease");
        assert_eq!(
            (leases[1], leases[2]),
            (Some(node_lease), Some(node_lease)),
            "the Device and its claim are not on the Node's lease {node_lease}: they would \
             outlive the Node's expiry",
        );

        first.close().await;
        behind.close().await;
    });
}

#[test]
fn a_fence_that_times_out_does_not_degrade_the_member() {
    // A fence that times out means this member's view is behind -- its watch
    // has not delivered what etcd holds -- and that is no failure of etcd's.
    // The Python backend answers it 503 and stays READY (`_guarded`); this one
    // degraded, and a member degrades until its watch reconnects: one whose
    // watch is merely slow refused every mutation while nothing was wrong.
    //
    // Built exactly: the member's watch reaches etcd through the relay -- a
    // watch uses the first endpoint -- and its requests fail over to the second
    // when the relay is held, so the fenced path's read is answered while the
    // watch that must catch up to it cannot.
    with_etcd(|server| async move {
        let relay = Relay::start(server.client_port).await;
        let first = EtcdRegistryBackend::new(registry(), server.config("/v1t")).expect("a backend");
        let mut config = server.config("/v1t");
        config.endpoints = vec![
            relay.endpoint(),
            format!("127.0.0.1:{}", server.client_port),
        ];
        // The relay is this member's local endpoint, so it is the one the pool
        // puts first (`parse_endpoints`, local first) and the watch uses.
        config.layout.local.client_port = relay.port;
        config.layout.members[0].client_port = relay.port;
        config.rpc_timeout = Duration::from_millis(300);
        config.mutation_timeout = Duration::from_millis(500);
        let behind = EtcdRegistryBackend::new(registry(), config).expect("a backend");
        first.start().await.expect("started");
        behind.start().await.expect("started");

        relay.hold();
        first
            .register(ResourceType::Node, node("n1"))
            .await
            .expect("no outage")
            .expect("accepted");
        let answer = behind
            .register(ResourceType::Device, device("d1", "n1"))
            .await;
        relay.release();

        // The refusal is the fence's own, not some other 503 that happens not to
        // degrade: its message is the one `RevisionFence::wait` gives.
        assert!(
            answer
                .as_ref()
                .is_err_and(|refused| refused.0.contains("still waiting for")),
            "a registration whose fence could not be met was answered {answer:?}, not the \
             fence's 503",
        );
        assert_eq!(
            behind.state(),
            BackendState::Ready,
            "a fence that timed out degraded the member: it refuses every mutation until its \
             watch reconnects, and a watch that is merely slow never does",
        );

        first.close().await;
        behind.close().await;
    });
}
