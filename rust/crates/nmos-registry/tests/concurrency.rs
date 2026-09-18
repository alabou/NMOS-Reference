// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The obligations the concurrency divergences create.
//!
//! Moving subscription matching out of the write lock and health onto an atomic
//! buys write throughput, and each buys it by giving something up. What is
//! given up is only acceptable if the properties below actually hold, and none
//! of them is visible in a single-threaded test -- a violation is a rare
//! interleaving, not a failing assertion.
//!
//! So these run real threads, and each one names the divergence it is the price
//! of:
//!
//! 1. **Grain ordering now rests on the commit sequence, not on the lock.** Per
//!    resource, what a connection observes must be a subsequence of what
//!    actually happened, in order. A second matcher, an out-of-order drain or a
//!    mis-anchored connect reorders or drops grains -- and the symptom is a
//!    client with a subtly wrong view, not a crash.
//! 2. **No event is lost across a connect.** A writer racing `connect` produces
//!    a resource that appears either in the sync burst or in a later grain --
//!    never neither, and never both.
//! 3. **Health refreshes children before the Node.** Garbage collection decides
//!    on the *Node's* health and cascades, so a collector that sees a fresh
//!    Node must be guaranteed its subtree was already refreshed. Refreshing the
//!    Node first would let a concurrent collector expire a live subtree.
//!
//! These are the tests the plan requires before the divergences are allowed to
//! stand.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing
)]

use std::collections::HashMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;

use nmos_registry::commit::Sequence;
use nmos_registry::registry::{Registry, classify_batch};
use nmos_registry::subscription::Subscription;
use nmos_registry_core::body::Body;
use nmos_registry_core::cursor::TaiCursor;
use nmos_registry_core::resource_type::ResourceType;
use nmos_registry_core::store::{RegistryStore, health_now};
use serde_json::json;

const NODE_ID: &str = "3b8be755-08ff-452b-b217-c9151eb21193";
const DEVICE_ID: &str = "a370d258-69de-4422-860a-ee4cf32ee9f4";

fn node_body(id: &str, version: u64, label: &str) -> Body {
    Body::from_value(json!({
        "id": id, "version": format!("{version}:0"), "label": label,
    }))
}

fn device_body(id: &str, node_id: &str, version: u64) -> Body {
    Body::from_value(json!({
        "id": id, "version": format!("{version}:0"), "label": "d",
        "node_id": node_id,
    }))
}

fn sender_body(id: &str, device_id: &str, version: u64, label: &str) -> Body {
    Body::from_value(json!({
        "id": id, "version": format!("{version}:0"), "label": label,
        "device_id": device_id,
    }))
}

fn subscription(params: &[(&str, &str)]) -> Subscription {
    Subscription {
        id: "sub-1".to_owned(),
        ws_href: "ws://example.test/ws/?uid=sub-1".to_owned(),
        resource_path: "/senders".to_owned(),
        resource_type: ResourceType::Sender,
        params: params
            .iter()
            .map(|(k, v)| ((*k).to_owned(), (*v).to_owned()))
            .collect(),
        max_update_rate_ms: 0,
        persist: true,
        secure: false,
        authorization: false,
        created: TaiCursor::new(100, 0),
        host: "example.test".to_owned(),
    }
}

fn seeded_registry() -> Arc<Registry> {
    let registry = Arc::new(Registry::new(RegistryStore::new()));
    registry
        .register(ResourceType::Node, node_body(NODE_ID, 100, "n"))
        .expect("node");
    registry
        .register(ResourceType::Device, device_body(DEVICE_ID, NODE_ID, 100))
        .expect("device");
    // Clear the seeding events so the tests below see only their own.
    registry.drain_commits();
    registry
}

fn sender_id(n: usize) -> String {
    format!("{n:08x}-0000-4000-8000-00000000000a")
}

// ---------------------------------------------------------------------------
// Obligation 1: ordering rests on the sequence, not the lock
// ---------------------------------------------------------------------------

#[test]
fn what_a_subscriber_observes_is_an_ordered_subsequence_of_what_happened() {
    // Many writers mutating the same few resources, one matcher draining.
    // Per resource, the labels a subscriber sees must appear in the order they
    // were written -- a dropped update is allowed (coalescing), a reordered one
    // is not.
    let registry = seeded_registry();
    let stop = Arc::new(AtomicBool::new(false));
    const WRITERS: usize = 4;
    const ROUNDS: u64 = 150;

    // Each writer owns its own resource, so the version sequence per resource
    // is unambiguous and any reordering is this code's fault rather than a
    // race between writers for one id.
    let mut handles = Vec::new();
    for writer in 0..WRITERS {
        let registry = Arc::clone(&registry);
        handles.push(thread::spawn(move || {
            let id = sender_id(writer);
            for round in 1..=ROUNDS {
                registry
                    .register(
                        ResourceType::Sender,
                        sender_body(&id, DEVICE_ID, 200 + round, &format!("v{round}")),
                    )
                    .expect("a sender registers");
            }
        }));
    }

    let sub = subscription(&[]);
    let observed = Arc::new(parking_lot::Mutex::new(
        HashMap::<String, Vec<String>>::new(),
    ));

    let matcher = {
        let registry = Arc::clone(&registry);
        let stop = Arc::clone(&stop);
        let observed = Arc::clone(&observed);
        let sub = sub.clone();
        thread::spawn(move || {
            let mut seen_through = Sequence::ZERO;
            loop {
                let batch = registry.drain_commits();
                if !batch.is_empty() {
                    if let Some(last) = batch.last() {
                        seen_through = last.sequence;
                    }
                    for pending in classify_batch(&sub, &batch, Sequence::ZERO) {
                        if let Some(post) = pending.post.as_ref()
                            && let Some(label) = post.string_member("label")
                        {
                            observed
                                .lock()
                                .entry(pending.path.clone())
                                .or_default()
                                .push(label.to_owned());
                        }
                    }
                } else if stop.load(Ordering::Relaxed) {
                    break;
                }
                std::hint::spin_loop();
            }
            seen_through
        })
    };

    for handle in handles {
        handle.join().expect("a writer panicked");
    }
    stop.store(true, Ordering::Relaxed);
    matcher.join().expect("the matcher panicked");

    let observed = observed.lock();
    assert_eq!(observed.len(), WRITERS, "not every resource was observed");

    for (id, labels) in observed.iter() {
        // Every label is `v<round>`; the rounds observed must be strictly
        // increasing. Coalescing may drop some, reordering may not happen.
        let rounds: Vec<u64> = labels
            .iter()
            .map(|label| {
                label
                    .strip_prefix('v')
                    .and_then(|n| n.parse().ok())
                    .unwrap_or_else(|| panic!("unexpected label {label}"))
            })
            .collect();
        assert!(
            rounds.windows(2).all(|pair| pair[0] < pair[1]),
            "{id}: observed out of order: {rounds:?}",
        );
        assert_eq!(
            rounds.last(),
            Some(&ROUNDS),
            "{id}: the final state was never observed",
        );
    }
}

// ---------------------------------------------------------------------------
// Obligation 2: nothing falls in the gap at connect
// ---------------------------------------------------------------------------

#[test]
fn a_writer_racing_a_connect_is_seen_exactly_once() {
    // The property `connect` trades the lock for. A resource committed before
    // the anchor is in the burst; one committed after arrives as a grain;
    // nothing is in neither and nothing is in both.
    const ATTEMPTS: usize = 60;
    const PER_ATTEMPT: usize = 12;

    for attempt in 0..ATTEMPTS {
        let registry = seeded_registry();
        let sub = subscription(&[]);
        let barrier = Arc::new(std::sync::Barrier::new(2));

        let writer = {
            let registry = Arc::clone(&registry);
            let barrier = Arc::clone(&barrier);
            thread::spawn(move || {
                barrier.wait();
                for n in 0..PER_ATTEMPT {
                    let id = sender_id(n);
                    registry
                        .register(ResourceType::Sender, sender_body(&id, DEVICE_ID, 300, "x"))
                        .expect("a sender registers");
                }
            })
        };

        barrier.wait();
        let (burst, anchor) = registry.connect(&sub);
        writer.join().expect("the writer panicked");

        // Everything committed after the anchor, classified as the matcher
        // would.
        let batch = registry.drain_commits();
        let grains = classify_batch(&sub, &batch, anchor);

        let mut in_burst: Vec<&str> = burst.iter().map(|p| p.path.as_str()).collect();
        let mut in_grains: Vec<&str> = grains.iter().map(|p| p.path.as_str()).collect();
        in_burst.sort_unstable();
        in_grains.sort_unstable();

        for n in 0..PER_ATTEMPT {
            let id = sender_id(n);
            let burst_has = in_burst.contains(&id.as_str());
            let grains_has = in_grains.contains(&id.as_str());
            assert!(
                burst_has || grains_has,
                "attempt {attempt}: {id} fell in the gap -- in neither the \
                 sync burst nor a grain",
            );
            assert!(
                !(burst_has && grains_has),
                "attempt {attempt}: {id} was delivered twice -- once in the \
                 burst and once as a grain",
            );
        }
    }
}

// ---------------------------------------------------------------------------
// Obligation 3: children are refreshed before the Node
// ---------------------------------------------------------------------------

#[test]
fn a_heartbeat_refreshes_children_before_the_node() {
    // The rule that makes `health` an atomic safe. Collection decides expiry on
    // the *Node's* health and then cascades, so refreshing the Node first would
    // let a concurrent collector see a fresh Node over a half-refreshed subtree
    // and expire live resources.
    //
    // # Making the window observable
    //
    // The first version of this test could not fail. It sampled with
    // `health_snapshot`, which allocates every resource per sample, and raced
    // that against a window a few nanoseconds wide -- so reversing the order
    // under test changed nothing. Two things fix it:
    //
    // * **a wide subtree.** With WIDTH senders under one Device, writing the
    //   Node first leaves WIDTH writes still to go, which is microseconds
    //   rather than nanoseconds;
    // * **a targeted read.** Two values per sample, not twenty thousand.
    //
    // # Why staleness is seeded through `apply_committed`
    //
    // Its authoritative `health` argument is the same entry point a distributed
    // backend uses on every applied revision. There is deliberately no "set
    // health" API to reach for: heartbeats are not replicated
    // (`raft_backend.py:346` -- "the beat writes nothing at all"), so one would
    // exist for this test and nothing else.
    const NODES: usize = 12;
    const WIDTH: usize = 400;

    let stale = health_now() - 10_000;
    let mut store = RegistryStore::new();
    for n in 0..NODES {
        seed_stale(&mut store, n, WIDTH, stale);
    }

    let registry = Arc::new(Registry::new(store));
    let fresh_from = health_now();
    let stop = Arc::new(AtomicBool::new(false));
    let started = Arc::new(std::sync::Barrier::new(2));

    let beater = {
        let registry = Arc::clone(&registry);
        let stop = Arc::clone(&stop);
        let started = Arc::clone(&started);
        thread::spawn(move || {
            started.wait();
            for n in 0..NODES {
                registry.heartbeat(&node_of(n));
            }
            stop.store(true, Ordering::Relaxed);
        })
    };

    let observer = {
        let registry = Arc::clone(&registry);
        let stop = Arc::clone(&stop);
        let started = Arc::clone(&started);
        thread::spawn(move || {
            started.wait();
            let mut violations: Vec<String> = Vec::new();
            let mut fresh_nodes_seen = 0_u64;
            while !stop.load(Ordering::Relaxed) {
                for n in 0..NODES {
                    let node = node_of(n);
                    // A collector reads the Node and cascades on it, so that is
                    // the order sampled here.
                    let Some(node_health) = registry.node_health(&node) else {
                        continue;
                    };
                    if node_health < fresh_from {
                        continue;
                    }
                    fresh_nodes_seen += 1;
                    // The LAST descendant the recursion reaches, which is the
                    // one still stale for longest if the order is wrong.
                    let last = sender_of(n, WIDTH - 1);
                    if let Some(child_health) =
                        registry.resource_health(ResourceType::Sender, &last)
                        && child_health < fresh_from
                    {
                        violations.push(format!(
                            "node {node} read fresh ({node_health}) while its \
                             sender {last} was still at {child_health}"
                        ));
                    }
                }
                std::hint::spin_loop();
            }
            (violations, fresh_nodes_seen)
        })
    };

    beater.join().expect("the heartbeat panicked");
    let (violations, fresh_nodes_seen) = observer.join().expect("the observer panicked");

    assert!(
        fresh_nodes_seen > 0,
        "the observer never caught a node fresh, so this proves nothing",
    );
    assert!(
        violations.is_empty(),
        "{} observations of a fresh Node over a stale subtree. The first: {}\n\n\
         The heartbeat is refreshing the Node before its children, so a \
         collector can expire a live subtree.",
        violations.len(),
        violations.first().map_or("", String::as_str),
    );
}

// ---------------------------------------------------------------------------
// Cross-resource emission order
// ---------------------------------------------------------------------------

#[test]
fn a_batch_is_emitted_in_arrival_order_not_hash_order() {
    // `classify_batch` coalesces through a map, and a map has no order. Two
    // cluster members handed the same batch must publish the same grain, so the
    // emission order has to come from the batch rather than from the hashing --
    // the same reason the store sorts a cascade's removals.
    //
    // Deterministic on purpose. The stress test above asserts a *per-resource*
    // property and cannot see this one: with each writer owning its own id,
    // every order across resources looks the same.
    let registry = seeded_registry();
    let sub = subscription(&[]);

    let ids: Vec<String> = (0..24).map(sender_id).collect();
    for id in &ids {
        registry
            .register(ResourceType::Sender, sender_body(id, DEVICE_ID, 300, "a"))
            .expect("a sender registers");
    }

    let batch = registry.drain_commits();
    let emitted: Vec<String> = classify_batch(&sub, &batch, Sequence::ZERO)
        .into_iter()
        .map(|pending| pending.path)
        .collect();

    assert_eq!(
        emitted, ids,
        "the batch was not emitted in the order it arrived",
    );
}

/// A Node, a Device and `width` Senders, all seeded with a stale health.
fn seed_stale(store: &mut RegistryStore, n: usize, width: usize, health: i64) {
    let node = node_of(n);
    let device = format!("{n:08x}-1000-4000-8000-00000000000a");

    let mut place = |kind: ResourceType, body: Body| {
        let prepared = store
            .prepare(kind, body.data())
            .unwrap_or_else(|e| panic!("seeding {kind} failed: {}", e.detail));
        let cursor = store.next_cursor(kind);
        store.apply_committed(&prepared, body, Some(cursor), Some(cursor), Some(health));
    };

    place(ResourceType::Node, node_body(&node, 100, "n"));
    place(ResourceType::Device, device_body(&device, &node, 100));
    for s in 0..width {
        place(
            ResourceType::Sender,
            sender_body(&sender_of(n, s), &device, 100, "s"),
        );
    }
}

/// The id of the nth seeded Node.
fn node_of(n: usize) -> String {
    format!("{n:08x}-0000-4000-8000-00000000000a")
}

/// The id of node `n`'s sender `s`.
///
/// The node index has to be in here: every id is the same length, so a formula
/// over the *string* collides across nodes and the second node's senders arrive
/// claiming a different parent.
fn sender_of(n: usize, s: usize) -> String {
    format!("{n:08x}-2000-4000-8000-{s:012x}")
}
