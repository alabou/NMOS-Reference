// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! What a heartbeat actually costs in the store, with no HTTP above it.
//!
//! The benchmark measures `POST /health/nodes/{id}` end to end and reports the
//! Rust registry *behind* the Python one on that phase, reproducibly. Heartbeat
//! is the dominant call once a deployment is stable -- every Node, every five
//! seconds, whether or not anything changed -- so the number matters more than
//! its size suggests.
//!
//! End-to-end latency cannot say **where** the time goes. This isolates the one
//! layer divergence D3 is about: the read-lock subtree walk plus atomic stores
//! that replaced Python's exclusive-lock mutation. If D3 is the problem this
//! number is large; if it is microseconds, the cost is above the store and D3 is
//! exonerated.
//!
//! Run it as a measurement rather than a pass/fail:
//!
//! ```text
//! cargo test -p nmos-registry-core --release --test heartbeat_cost -- --nocapture
//! ```
//!
//! The assertions are deliberately loose. This exists to produce numbers; a
//! tight threshold here would fail on a loaded machine and tell nobody anything.

#![allow(clippy::unwrap_used, clippy::expect_used, clippy::panic)]

use std::time::Instant;

use nmos_registry_core::body::Body;
use nmos_registry_core::resource_type::ResourceType;
use nmos_registry_core::store::RegistryStore;
use serde_json::json;

/// A deterministic UUID for index `n` of a kind, so a tree can be rebuilt.
fn id(kind: &str, n: usize) -> String {
    // Version 4, variant 8: the validators check both, and a store that
    // rejected these would make the measurement meaningless.
    format!("{:08x}-0000-4000-8000-{:012x}", n, kind.len() * 1_000_000 + n)
}

fn node_body(id: &str) -> Body {
    Body::from_value(json!({"id": id, "version": "100:0", "label": "n"}))
}

fn device_body(id: &str, node_id: &str) -> Body {
    Body::from_value(json!({
        "id": id, "version": "100:0", "label": "d", "node_id": node_id,
    }))
}

fn child_body(id: &str, device_id: &str) -> Body {
    Body::from_value(json!({
        "id": id, "version": "100:0", "label": "c", "device_id": device_id,
    }))
}

/// Populate `nodes` Nodes, each with the AMWA six-resource shape.
///
/// One Device and four children per Node, which is the subtree a single
/// heartbeat has to walk. The published AMWA figure is 2500 Nodes x 6
/// resources; the shape is what matters here, not the absolute count.
fn populate(nodes: usize) -> RegistryStore {
    let mut store = RegistryStore::new();
    for n in 0..nodes {
        let node = id("node", n);
        store
            .insert_or_update(ResourceType::Node, node_body(&node))
            .expect("node registers");
        let device = id("device", n);
        store
            .insert_or_update(ResourceType::Device, device_body(&device, &node))
            .expect("device registers");
        for (kind, resource_type) in [
            ("source", ResourceType::Source),
            ("flow", ResourceType::Flow),
            ("sender", ResourceType::Sender),
            ("receiver", ResourceType::Receiver),
        ] {
            let child = id(kind, n);
            let _ = store.insert_or_update(resource_type, child_body(&child, &device));
        }
    }
    store
}

/// Microseconds at the given percentile.
fn percentile(sorted: &[u128], p: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    #[allow(clippy::cast_precision_loss, clippy::cast_possible_truncation, clippy::cast_sign_loss)]
    let index = ((sorted.len() as f64 - 1.0) * p).round() as usize;
    #[allow(clippy::cast_precision_loss)]
    let value = sorted[index.min(sorted.len() - 1)] as f64;
    value / 1000.0
}

#[test]
fn how_long_one_heartbeat_takes_in_the_store() {
    for nodes in [10_usize, 250, 2500] {
        let store = populate(nodes);

        // Warm: the first pass would measure page faults as much as the walk.
        for n in 0..nodes {
            let _ = store.heartbeat(&id("node", n));
        }

        let mut samples: Vec<u128> = Vec::with_capacity(nodes * 4);
        for _ in 0..4 {
            for n in 0..nodes {
                let key = id("node", n);
                let started = Instant::now();
                let health = store.heartbeat(&key);
                samples.push(started.elapsed().as_nanos());
                assert!(health.is_some(), "the node should be registered");
            }
        }
        samples.sort_unstable();

        println!(
            "  store heartbeat, {nodes:>4} nodes ({} resources): \
             p50 {:>7.2}us  p95 {:>7.2}us  p99 {:>7.2}us  max {:>8.2}us",
            nodes * 6,
            percentile(&samples, 0.50),
            percentile(&samples, 0.95),
            percentile(&samples, 0.99),
            percentile(&samples, 1.0),
        );
    }
}

#[test]
fn the_walk_does_not_grow_with_the_registry() {
    // D3's claim in one assertion: a heartbeat touches one Node's subtree, so
    // its cost is a property of that subtree and not of how many other Nodes
    // exist. If this fails, the heartbeat is scanning something global and the
    // end-to-end regression has its explanation.
    let mut cost = Vec::new();
    for nodes in [10_usize, 1000] {
        let store = populate(nodes);
        for n in 0..nodes {
            let _ = store.heartbeat(&id("node", n));
        }
        let mut samples: Vec<u128> = Vec::with_capacity(nodes * 2);
        for _ in 0..2 {
            for n in 0..nodes {
                let key = id("node", n);
                let started = Instant::now();
                let _ = store.heartbeat(&key);
                samples.push(started.elapsed().as_nanos());
            }
        }
        samples.sort_unstable();
        cost.push(percentile(&samples, 0.50));
    }

    let (small, large) = (cost[0], cost[1]);
    println!("  p50 at 10 nodes: {small:.2}us   at 1000 nodes: {large:.2}us");
    assert!(
        large < small * 8.0 + 5.0,
        "heartbeat cost grew from {small:.2}us to {large:.2}us with 100x the \
         registry -- it is touching something global, which D3 says it must not",
    );
}
