// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The consensus backend behind the same seam the standalone one sits behind.
//!
//! Port of `nmos/raft/tests/test_raft_backend.py`. What matters here is not
//! that consensus works -- `consensus.rs` covers that -- but that the four
//! methods and the state behave the way the Registration API expects, because
//! that is the whole contract the HTTP layer is written against.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::arithmetic_side_effects,
    clippy::panic
)]

mod fabric;

use std::sync::Arc;
use std::time::Duration;

use nmos_cluster::{Derivation, MemberSpec, derive_cluster};
use nmos_registry::registry::Registry;
use nmos_registry_backend::{BackendState, RegistryBackend};
use nmos_registry_core::body::Body;
use nmos_registry_core::resource_type::ResourceType;
use nmos_registry_core::store::RegistryStore;
use nmos_registry_raft::backend::RaftRegistryBackend;
use nmos_registry_raft::cluster::{RAFT_FLAVOUR, derive_raft_layout};
use nmos_registry_raft::cursors::CursorAllocator;
use nmos_registry_raft::machine::StateMachine;
use nmos_registry_raft::node::{RaftNode, RaftTiming};
use nmos_registry_raft::persist::TermStore;
use nmos_registry_raft::transport::Transport;
use serde_json::json;

use fabric::Fabric;

static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

struct Scratch(std::path::PathBuf);

impl Scratch {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "nmos-raft-backend-{}-{}",
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

const NODE_ID: &str = "11111111-0000-4000-8000-000000000000";
const DEVICE_ID: &str = "22222222-0000-4000-8000-000000000000";
const SENDER_ID: &str = "33333333-0000-4000-8000-000000000000";

fn node_body(label: &str) -> Body {
    Body::from_value(json!({
        "id": NODE_ID, "version": "1000:0", "label": label, "description": "",
        "tags": {}, "href": "http://example/", "hostname": "example", "caps": {},
        "api": {
            "versions": ["v1.3"],
            "endpoints": [{"host": "example", "port": 80, "protocol": "http"}],
        },
        "services": [], "clocks": [], "interfaces": [],
    }))
}

fn device_body() -> Body {
    Body::from_value(json!({
        "id": DEVICE_ID, "version": "1000:1", "label": "d", "description": "",
        "tags": {}, "type": "urn:x-nmos:device:generic", "node_id": NODE_ID,
        "senders": [], "receivers": [], "controls": [],
    }))
}

fn sender_body(device_id: &str) -> Body {
    Body::from_value(json!({
        "id": SENDER_ID, "version": "1000:2", "label": "s", "description": "",
        "tags": {}, "flow_id": null, "device_id": device_id,
        "manifest_href": null, "transport": "urn:x-nmos:transport:rtp",
        "interface_bindings": [],
        "subscription": {"receiver_id": null, "active": false},
    }))
}

/// A cluster of backends on the fabric.
struct Backends {
    backends: Vec<Arc<RaftRegistryBackend>>,
    _fabric: Arc<Fabric>,
    _scratch: Scratch,
}

impl Backends {
    fn build(size: usize) -> Self {
        Self::with_gc(size, 12)
    }

    /// A cluster whose stores use `gc_interval` seconds.
    ///
    /// Zero makes a Node expirable one second after it was last heard from,
    /// which is what lets a collection test have something to collect. With the
    /// default twelve, every collection assertion is vacuous -- the Node was
    /// just registered, so nothing was ever going to expire, and a backend that
    /// collected while degraded would pass.
    fn with_gc(size: usize, gc_interval: i64) -> Self {
        let scratch = Scratch::new();
        let fabric = Fabric::new();
        let timing = RaftTiming {
            heartbeat_ms: 10,
            election_min_ms: 60,
            election_max_ms: 120,
            ..RaftTiming::default()
        };

        let specs: Vec<MemberSpec> = (0..size)
            .map(|index| MemberSpec {
                host: "127.0.0.1".to_owned(),
                client_port: 2481 + (index as u16) * 2,
                peer_port: 2482 + (index as u16) * 2,
                name: Some(format!("member-{index}")),
                bind_address: None,
            })
            .collect();

        let mut backends = Vec::new();
        for index in 0..size {
            let layout = derive_cluster(
                &specs,
                &Derivation {
                    local_host: "127.0.0.1",
                    local_peer_port: Some(2482 + (index as u16) * 2),
                    namespace: "/nmos",
                    tls: false,
                    flavour: RAFT_FLAVOUR,
                },
            )
            .expect("a valid cluster");
            let token = layout.token.clone();
            let registry = Arc::new(Registry::new(RegistryStore::with_intervals(
                gc_interval,
                12,
            )));
            let node = RaftNode::new(
                derive_raft_layout(&layout, token),
                fabric.transport(index as u64) as Arc<dyn Transport>,
                TermStore::new(scratch.0.join(format!("m{index}.json"))),
                StateMachine::new(
                    index as u64,
                    CursorAllocator::new(index as u64).expect("a lane"),
                ),
                Arc::clone(&registry),
                timing,
            );
            backends.push(RaftRegistryBackend::new(
                registry,
                node,
                Duration::from_secs(2),
            ));
        }

        Self {
            backends,
            _fabric: fabric,
            _scratch: scratch,
        }
    }

    async fn start_all(&self) {
        for backend in &self.backends {
            // The forward handler is the backend itself: a mutation handed over
            // by another member has to take the same path as a local one, or
            // the two would validate differently.
            backend.node().set_forward_handler(
                Arc::clone(backend) as Arc<dyn nmos_registry_raft::node::ForwardHandler>
            );
            backend.start().await.expect("starts");
        }
    }

    async fn close_all(&self) {
        for backend in &self.backends {
            backend.close().await;
        }
    }

    fn ready(&self) -> bool {
        self.backends
            .iter()
            .all(|backend| backend.state() == BackendState::Ready)
    }
}

async fn until(mut ready: impl FnMut() -> bool) -> bool {
    for _ in 0..400 {
        if ready() {
            return true;
        }
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    false
}

// -- state -------------------------------------------------------------------

#[tokio::test(flavor = "multi_thread")]
async fn a_backend_is_starting_before_it_starts() {
    let cluster = Backends::build(1);
    assert_eq!(cluster.backends[0].state(), BackendState::Starting);
    assert!(
        !BackendState::Starting.serves_queries(),
        "a registry that has not loaded must not answer queries with an empty \
         collection, which a client cannot tell from a registry that is empty",
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn a_one_member_cluster_becomes_ready() {
    let cluster = Backends::build(1);
    cluster.start_all().await;
    assert!(until(|| cluster.ready()).await, "never became ready");
    assert!(cluster.backends[0].state().accepts_mutations());
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn the_state_is_derived_and_cannot_go_stale() {
    // The first version of this cached the state and refreshed it on start and
    // on each mutation -- so a member that started before its peers waited out
    // its timeout, went degraded, and then reported degraded *forever*, because
    // nothing wrote to it and nothing else looked. Its Registration API
    // answered 503 on a perfectly healthy cluster.
    let cluster = Backends::build(3);
    cluster.start_all().await;
    assert!(until(|| cluster.ready()).await, "never became ready");

    // Nothing has mutated anything, and the state is still right.
    tokio::time::sleep(Duration::from_millis(100)).await;
    assert!(
        cluster.ready(),
        "the state went stale with no mutation to refresh it",
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_closing_backend_stops_accepting_mutations() {
    let cluster = Backends::build(1);
    cluster.start_all().await;
    assert!(until(|| cluster.ready()).await, "never became ready");

    cluster.backends[0].close().await;
    assert_eq!(cluster.backends[0].state(), BackendState::Stopping);
    assert!(!cluster.backends[0].state().accepts_mutations());
    assert!(
        cluster.backends[0].state().serves_queries(),
        "a member shutting down still answers queries from the replica it \
         holds; refusing them turns a rolling restart into an outage",
    );
}

// -- the four methods --------------------------------------------------------

#[tokio::test(flavor = "multi_thread")]
async fn a_registration_commits_and_is_visible_everywhere() {
    let cluster = Backends::build(3);
    cluster.start_all().await;
    assert!(until(|| cluster.ready()).await, "never became ready");

    let applied = cluster.backends[0]
        .register(ResourceType::Node, node_body("a node"))
        .await
        .expect("commits")
        .expect("accepted");
    assert!(applied.created, "the first registration was not a create");

    assert!(
        until(|| cluster.backends.iter().all(|backend| backend
            .registry()
            .get(ResourceType::Node, NODE_ID)
            .is_some()))
        .await,
        "the registration did not reach every member",
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_second_registration_is_an_update() {
    let cluster = Backends::build(1);
    cluster.start_all().await;
    assert!(until(|| cluster.ready()).await, "never became ready");

    cluster.backends[0]
        .register(ResourceType::Node, node_body("first"))
        .await
        .expect("commits")
        .expect("accepted");

    let again = Body::from_value(json!({
        "id": NODE_ID, "version": "1001:0", "label": "second", "description": "",
        "tags": {}, "href": "http://example/", "hostname": "example", "caps": {},
        "api": {
            "versions": ["v1.3"],
            "endpoints": [{"host": "example", "port": 80, "protocol": "http"}],
        },
        "services": [], "clocks": [], "interfaces": [],
    }));
    let applied = cluster.backends[0]
        .register(ResourceType::Node, again)
        .await
        .expect("commits")
        .expect("accepted");
    assert!(
        !applied.created,
        "a re-registration reported a create, so the client is told 201 for \
         something that already existed",
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_rejection_costs_no_round_trip() {
    // The claim the whole ownership design is for: a 400 is terminal, so the
    // etcd backend cannot answer one without a linearizable read first. Here
    // the owner's view is authoritative, so the refusal is decided locally.
    let cluster = Backends::build(3);
    cluster.start_all().await;
    assert!(until(|| cluster.ready()).await, "never became ready");

    // A Sender whose Device does not exist.
    let failure = cluster.backends[0]
        .register(ResourceType::Sender, sender_body(DEVICE_ID))
        .await
        .expect("answers")
        .expect_err("refused");
    assert_eq!(
        failure.error.as_str(),
        "parent_missing",
        "the refusal was {} rather than the parent rule",
        failure.error.as_str(),
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_subtree_registers_and_unregisters() {
    let cluster = Backends::build(3);
    cluster.start_all().await;
    assert!(until(|| cluster.ready()).await, "never became ready");

    for (kind, body) in [
        (ResourceType::Node, node_body("a node")),
        (ResourceType::Device, device_body()),
        (ResourceType::Sender, sender_body(DEVICE_ID)),
    ] {
        cluster.backends[0]
            .register(kind, body)
            .await
            .expect("commits")
            .expect("accepted");
    }

    assert!(
        until(|| cluster.backends.iter().all(|backend| backend
            .registry()
            .get(ResourceType::Sender, SENDER_ID)
            .is_some()))
        .await,
        "the sender did not reach every member",
    );

    let removed = cluster.backends[0]
        .unregister(ResourceType::Device, DEVICE_ID)
        .await
        .expect("commits");
    assert!(removed.is_some(), "the device was not there to remove");

    assert!(
        until(|| cluster.backends.iter().all(|backend| backend
            .registry()
            .get(ResourceType::Sender, SENDER_ID)
            .is_none()))
        .await,
        "the cascade did not reach every member: a Sender outlived its Device",
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn unregistering_something_absent_is_not_an_error() {
    let cluster = Backends::build(1);
    cluster.start_all().await;
    assert!(until(|| cluster.ready()).await, "never became ready");

    let removed = cluster.backends[0]
        .unregister(ResourceType::Node, NODE_ID)
        .await
        .expect("answers");
    assert!(removed.is_none(), "a 404 became an error");
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_heartbeat_writes_nothing_to_the_log() {
    // The property the etcd backend's lease design argues for, and which this
    // one makes stronger: 100 Nodes beating every 5 s must not become 100
    // consensus rounds per second. Here the beat writes nothing at all.
    let cluster = Backends::build(3);
    cluster.start_all().await;
    assert!(until(|| cluster.ready()).await, "never became ready");

    cluster.backends[0]
        .register(ResourceType::Node, node_body("a node"))
        .await
        .expect("commits")
        .expect("accepted");
    assert!(
        until(|| cluster.backends[0].node().last_applied() >= 1).await,
        "the registration never applied",
    );

    let before = cluster.backends[0].node().last_log_index();
    for _ in 0..25 {
        cluster.backends[0]
            .heartbeat(NODE_ID)
            .await
            .expect("answers")
            .expect("the node is registered");
    }
    let after = cluster.backends[0].node().last_log_index();

    assert_eq!(
        after,
        before,
        "25 heartbeats appended {} log entries; at AMWA scale that is a \
         consensus round per beat and the cluster does nothing else",
        after - before,
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_heartbeat_for_an_unknown_node_is_none_not_an_error() {
    let cluster = Backends::build(1);
    cluster.start_all().await;
    assert!(until(|| cluster.ready()).await, "never became ready");

    assert!(
        cluster.backends[0]
            .heartbeat(NODE_ID)
            .await
            .expect("answers")
            .is_none(),
        "an unregistered Node produced an error rather than a 404",
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_stale_node_is_expired_by_its_owner() {
    // Guard the two tests below: a collection that never collects anything
    // would satisfy both of them while doing nothing at all.
    let cluster = Backends::with_gc(1, 0);
    cluster.start_all().await;
    assert!(until(|| cluster.ready()).await, "never became ready");

    cluster.backends[0]
        .register(ResourceType::Node, node_body("a node"))
        .await
        .expect("commits")
        .expect("accepted");

    // Health is stamped at registration and the interval is zero, so one
    // second is enough to put it below the threshold.
    tokio::time::sleep(Duration::from_millis(1_100)).await;
    cluster.backends[0]
        .collect_garbage()
        .await
        .expect("answers");

    assert!(
        until(|| cluster.backends[0]
            .registry()
            .get(ResourceType::Node, NODE_ID)
            .is_none())
        .await,
        "a Node silent for longer than the interval was not expired",
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn collection_does_nothing_while_degraded() {
    // A member that cannot commit must not expire anything: removing it
    // locally is exactly the divergence the replicated expiry exists to
    // prevent.
    let cluster = Backends::with_gc(3, 0);
    cluster.start_all().await;
    assert!(until(|| cluster.ready()).await, "never became ready");

    cluster.backends[0]
        .register(ResourceType::Node, node_body("a node"))
        .await
        .expect("commits")
        .expect("accepted");
    assert!(
        until(|| cluster.backends[0]
            .registry()
            .get(ResourceType::Node, NODE_ID)
            .is_some())
        .await,
        "the registration never applied",
    );

    cluster._fabric.isolate(0, &[0, 1, 2]);
    assert!(
        until(|| cluster.backends[0].state() != BackendState::Ready).await,
        "an isolated member stayed ready",
    );

    // Stale by now, and this member owns it -- so only the degraded check
    // stands between the Node and a local expiry.
    tokio::time::sleep(Duration::from_millis(1_100)).await;
    cluster.backends[0]
        .collect_garbage()
        .await
        .expect("answers");

    assert!(
        cluster.backends[0]
            .registry()
            .get(ResourceType::Node, NODE_ID)
            .is_some(),
        "a degraded member expired a Node it could not replicate the expiry \
         of, so it now disagrees with the majority about what is registered",
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn only_the_owner_expires_a_node() {
    // Expiry is owner-decided and replicated. Evaluated independently on every
    // member, the one with the slowest clock resurrects resources the others
    // have collected -- and two members proposing the same expiry is two
    // consensus rounds for one removal.
    let cluster = Backends::with_gc(3, 0);
    cluster.start_all().await;
    assert!(until(|| cluster.ready()).await, "never became ready");

    cluster.backends[0]
        .register(ResourceType::Node, node_body("a node"))
        .await
        .expect("commits")
        .expect("accepted");
    assert!(
        until(|| cluster
            .backends
            .iter()
            .all(|b| b.node().ownership_of(NODE_ID) == Some(0)))
        .await,
        "ownership did not settle on member 0",
    );

    tokio::time::sleep(Duration::from_millis(1_100)).await;

    // A member that does not own it must collect nothing, even though its own
    // replica shows the Node as stale.
    let before = cluster.backends[1].node().last_log_index();
    cluster.backends[1]
        .collect_garbage()
        .await
        .expect("answers");
    let after = cluster.backends[1].node().last_log_index();
    assert_eq!(
        after, before,
        "a member proposed an expiry for a Node it does not own, so the \
         removal costs one consensus round per member rather than one",
    );
    assert!(
        cluster.backends[1]
            .registry()
            .get(ResourceType::Node, NODE_ID)
            .is_some(),
        "a non-owner expired the Node",
    );
    cluster.close_all().await;
}

// -- ownership ---------------------------------------------------------------

#[tokio::test(flavor = "multi_thread")]
async fn a_first_registration_claims_the_node() {
    // The fused claim: a Node's first registration takes ownership in the same
    // entry rather than paying a second round trip, which matters because a
    // facility powering up is entirely first registrations.
    let cluster = Backends::build(3);
    cluster.start_all().await;
    assert!(until(|| cluster.ready()).await, "never became ready");

    cluster.backends[0]
        .register(ResourceType::Node, node_body("a node"))
        .await
        .expect("commits")
        .expect("accepted");

    assert!(
        until(|| cluster.backends[0].node().ownership_of(NODE_ID) == Some(0)).await,
        "the first registration did not claim the Node, so every later \
         mutation for it pays a hop",
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn every_member_agrees_about_who_owns_a_node() {
    // Ownership is a replicated derivation, not a negotiation: two members that
    // disagreed would each validate that Node's subtree against their own
    // store, which is the read ownership exists to remove.
    let cluster = Backends::build(3);
    cluster.start_all().await;
    assert!(until(|| cluster.ready()).await, "never became ready");

    cluster.backends[1]
        .register(ResourceType::Node, node_body("a node"))
        .await
        .expect("commits")
        .expect("accepted");

    assert!(
        until(|| {
            let owners: Vec<Option<u64>> = cluster
                .backends
                .iter()
                .map(|backend| backend.node().ownership_of(NODE_ID))
                .collect();
            owners.iter().all(|owner| *owner == Some(1))
        })
        .await,
        "the members disagree about who owns the Node: {:?}",
        cluster
            .backends
            .iter()
            .map(|b| b.node().ownership_of(NODE_ID))
            .collect::<Vec<_>>(),
    );
    cluster.close_all().await;
}

// -- the properties the first falsification pass did not reach ---------------

#[tokio::test(flavor = "multi_thread")]
async fn a_refusal_the_store_decides_stays_a_refusal() {
    // The earlier rejection test exercised `resolve_node`, which refuses before
    // the store is consulted at all. The refusal that comes back from
    // `store.prepare` travels a different path -- and turning *that* into a 503
    // is the damaging direction: a 400 is terminal and a 503 says "retry", so a
    // Node would retry a body the cluster will never accept, forever.
    let cluster = Backends::build(1);
    cluster.start_all().await;
    assert!(until(|| cluster.ready()).await, "never became ready");

    cluster.backends[0]
        .register(ResourceType::Node, node_body("first"))
        .await
        .expect("commits")
        .expect("accepted");

    // The same id, registered as a Device: a type conflict, which only the
    // store can see.
    let conflicting = Body::from_value(json!({
        "id": NODE_ID, "version": "1001:0", "label": "clash", "description": "",
        "tags": {}, "type": "urn:x-nmos:device:generic", "node_id": NODE_ID,
        "senders": [], "receivers": [], "controls": [],
    }));
    let failure = cluster.backends[0]
        .register(ResourceType::Device, conflicting)
        .await
        .expect("answers, rather than reporting the cluster unavailable")
        .expect_err("refused");
    assert_eq!(
        failure.error.as_str(),
        "id_type_conflict",
        "the store's refusal came back as {}",
        failure.error.as_str(),
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_404_unregister_costs_no_consensus_round() {
    // The local store is a complete replica, so "not here" is not a guess. The
    // answer is the same either way -- which is why this asserts the *cost*:
    // proposing an entry to discover nothing was there turns every stray
    // DELETE into a quorum round.
    let cluster = Backends::build(3);
    cluster.start_all().await;
    assert!(until(|| cluster.ready()).await, "never became ready");

    let before = cluster.backends[0].node().last_log_index();
    for _ in 0..5 {
        assert!(
            cluster.backends[0]
                .unregister(ResourceType::Node, NODE_ID)
                .await
                .expect("answers")
                .is_none(),
        );
    }
    let after = cluster.backends[0].node().last_log_index();

    assert_eq!(
        after,
        before,
        "five deletes of an absent resource appended {} entries",
        after - before,
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn the_creation_cursor_does_not_move_when_a_resource_is_updated() {
    // A client paging by creation order must not see a resource move because it
    // was updated -- it would be delivered twice, or skipped, depending on
    // which side of the cursor it landed.
    let cluster = Backends::build(1);
    cluster.start_all().await;
    assert!(until(|| cluster.ready()).await, "never became ready");

    cluster.backends[0]
        .register(ResourceType::Node, node_body("first"))
        .await
        .expect("commits")
        .expect("accepted");
    let created = cluster.backends[0]
        .registry()
        .get(ResourceType::Node, NODE_ID)
        .expect("registered")
        .created;

    let updated_body = Body::from_value(json!({
        "id": NODE_ID, "version": "1001:0", "label": "second", "description": "",
        "tags": {}, "href": "http://example/", "hostname": "example", "caps": {},
        "api": {
            "versions": ["v1.3"],
            "endpoints": [{"host": "example", "port": 80, "protocol": "http"}],
        },
        "services": [], "clocks": [], "interfaces": [],
    }));
    cluster.backends[0]
        .register(ResourceType::Node, updated_body)
        .await
        .expect("commits")
        .expect("accepted");

    let snapshot = cluster.backends[0]
        .registry()
        .get(ResourceType::Node, NODE_ID)
        .expect("registered");
    assert_eq!(
        snapshot.created, created,
        "the creation cursor moved on update, so a client paging by creation \
         order sees this resource twice",
    );
    // **This passes whatever the backend carries**, because the store never
    // touches `created` on an update -- measured, by having the backend send a
    // fresh one and seeing nothing change. What the backend's lookup actually
    // covers is the *create* path, where the value it sends is the one stored;
    // `the_store_keeps_created_across_an_update` below pins the store
    // behaviour this rests on, so the redundancy cannot lapse silently.
    assert!(
        snapshot.updated > created,
        "the update cursor did not advance, so a client paging by update order \
         never sees the change",
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn health_is_a_real_clock_reading_carried_by_the_proposer() {
    // Carried, not stamped per member -- or two members disagree on the very
    // next status line. And a *real* reading, or nothing ever expires: a
    // constant would put every Node permanently below or above the threshold.
    let cluster = Backends::build(3);
    cluster.start_all().await;
    assert!(until(|| cluster.ready()).await, "never became ready");

    let before = nmos_registry_core::store::health_now();
    cluster.backends[0]
        .register(ResourceType::Node, node_body("a node"))
        .await
        .expect("commits")
        .expect("accepted");

    assert!(
        until(|| cluster
            .backends
            .iter()
            .all(|b| b.registry().node_health(NODE_ID).is_some()))
        .await,
        "the registration did not reach every member",
    );

    let healths: Vec<i64> = cluster
        .backends
        .iter()
        .map(|b| b.registry().node_health(NODE_ID).expect("registered"))
        .collect();

    assert!(
        healths.windows(2).all(|pair| pair[0] == pair[1]),
        "the members stamped different healths {healths:?}; they disagree \
         about when this Node was last heard from, so they expire it at \
         different moments",
    );
    let after = nmos_registry_core::store::health_now();
    assert!(
        healths[0] >= before && healths[0] <= after,
        "health {} is not a clock reading from the registration ({before}..{after}); \
         a constant means a Node either never expires or expires at once",
        healths[0],
    );
    cluster.close_all().await;
}

#[test]
fn the_store_keeps_created_across_an_update() {
    // What the backend's creation-cursor lookup rests on, asserted against the
    // store rather than assumed. The backend computes a stable `created` and
    // carries it; the store then ignores it on an update and keeps its own. So
    // sending the wrong one changes nothing -- and if the store ever stopped
    // preserving it, the backend's lookup would become load-bearing with no
    // test saying so.
    let mut store = RegistryStore::new();
    let body = node_body("first");
    let prepared = store
        .prepare(ResourceType::Node, body.data())
        .expect("accepted");
    store.apply_committed(
        &prepared,
        body,
        Some(nmos_registry_core::cursor::TaiCursor::new(1000, 0)),
        Some(nmos_registry_core::cursor::TaiCursor::new(1000, 0)),
        Some(1),
    );

    let updated_body = Body::from_value(json!({
        "id": NODE_ID, "version": "1001:0", "label": "second", "description": "",
        "tags": {}, "href": "http://example/", "hostname": "example", "caps": {},
        "api": {
            "versions": ["v1.3"],
            "endpoints": [{"host": "example", "port": 80, "protocol": "http"}],
        },
        "services": [], "clocks": [], "interfaces": [],
    }));
    let prepared = store
        .prepare(ResourceType::Node, updated_body.data())
        .expect("accepted");
    store.apply_committed(
        &prepared,
        updated_body,
        // Deliberately wrong: a `created` far in the future. The store must
        // ignore it.
        Some(nmos_registry_core::cursor::TaiCursor::new(9999, 0)),
        Some(nmos_registry_core::cursor::TaiCursor::new(2000, 0)),
        Some(1),
    );

    let stored = store.get(ResourceType::Node, NODE_ID).expect("registered");
    assert_eq!(
        stored.created,
        nmos_registry_core::cursor::TaiCursor::new(1000, 0),
        "the store adopted the creation cursor an update carried; the \
         backend's stable-`created` lookup has stopped being belt-and-braces \
         and become load-bearing",
    );
    assert_eq!(
        stored.updated,
        nmos_registry_core::cursor::TaiCursor::new(2000, 0),
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn collection_on_a_degraded_member_returns_at_once() {
    // The degraded check is about *cost*, not outcome. Without it the Node is
    // still not expired -- the commit simply fails -- but it fails by waiting
    // out the mutation timeout, once per candidate Node. A member that has lost
    // quorum would then spend its whole collection interval blocked, and with
    // many stale Nodes it would never finish a pass.
    //
    // Measured rather than asserted about state, because the state is the same
    // either way.
    let cluster = Backends::with_gc(3, 0);
    cluster.start_all().await;
    assert!(until(|| cluster.ready()).await, "never became ready");

    cluster.backends[0]
        .register(ResourceType::Node, node_body("a node"))
        .await
        .expect("commits")
        .expect("accepted");
    assert!(
        until(|| cluster.backends[0]
            .registry()
            .get(ResourceType::Node, NODE_ID)
            .is_some())
        .await,
        "the registration never applied",
    );

    cluster._fabric.isolate(0, &[0, 1, 2]);
    assert!(
        until(|| cluster.backends[0].state() != BackendState::Ready).await,
        "an isolated member stayed ready",
    );
    tokio::time::sleep(Duration::from_millis(1_100)).await;

    let started = std::time::Instant::now();
    cluster.backends[0]
        .collect_garbage()
        .await
        .expect("answers");
    let elapsed = started.elapsed();

    assert!(
        elapsed < Duration::from_millis(500),
        "collection on a degraded member took {elapsed:?}; it is waiting out \
         the mutation timeout for a commit that cannot succeed, once per stale \
         Node",
    );
    cluster.close_all().await;
}

/// Does a closed backend actually go away?
///
/// There is no leak checker anywhere in this workspace -- no sanitizer, no
/// Miri, no heap profiler -- so the one shape that leaks without any unsafe
/// code is worth asserting directly: a reference cycle.
///
/// `RaftRegistryBackend` holds `Arc<RaftNode>`, and the node holds the forward
/// handler, which *is* the backend (`main.rs`, and `start_all` above). Backend
/// -> node -> backend. Rust has no cycle collector, so if `close` does not
/// break it, neither object is ever dropped -- and with them the store, the
/// log, the state machine and every snapshot they own.
///
/// Asserted with a `Weak`: after dropping every strong handle this test holds,
/// upgrading must fail. It succeeding means the graph is holding itself up.
///
/// The same shape is harmless in the Python, which is why it is easy to port
/// without noticing: `raft_backend.py` hands `self._on_forward`, a bound
/// method that keeps the backend alive, and CPython's cycle collector reclaims
/// it. That difference is exactly the kind this test exists to catch.
#[tokio::test(flavor = "multi_thread")]
async fn a_closed_backend_is_dropped() {
    let cluster = Backends::build(1);
    cluster.start_all().await;
    assert!(until(|| cluster.ready()).await, "never became ready");
    cluster.close_all().await;

    let watch = Arc::downgrade(&cluster.backends[0]);
    let node_watch = Arc::downgrade(cluster.backends[0].node());
    drop(cluster);

    assert!(
        watch.upgrade().is_none(),
        "the backend outlived every handle to it: the node still holds it as \
         its forward handler, so backend -> node -> backend keeps both alive \
         and everything they own with them",
    );
    assert!(
        node_watch.upgrade().is_none(),
        "the node outlived the backend that owned it",
    );
}
