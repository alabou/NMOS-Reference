// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! What one registration costs in peer messages, by cluster size.
//!
//! A benchmark on one machine cannot separate "the algorithm sends more" from
//! "the machine was busier". This can: the fabric delivers every message in
//! process, so the count is the algorithm's and nothing else's.

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
use nmos_registry_raft::node::{ForwardHandler, RaftNode, RaftTiming};
use nmos_registry_raft::persist::TermStore;
use nmos_registry_raft::transport::Transport;
use serde_json::json;

use fabric::Fabric;

fn node_body(id: &str) -> Body {
    Body::from_value(json!({
        "id": id, "version": "1000:0", "label": "n", "description": "",
        "tags": {}, "href": "http://example/", "hostname": "example", "caps": {},
        "api": {
            "versions": ["v1.3"],
            "endpoints": [{"host": "example", "port": 80, "protocol": "http"}],
        },
        "services": [], "clocks": [], "interfaces": [],
    }))
}

async fn cost_of(
    members: usize,
    registrations: usize,
    heartbeat_ms: u64,
) -> (Vec<(&'static str, u64)>, Duration) {
    let dir = std::env::temp_dir().join(format!("nmos-raft-cost-{}-{members}", std::process::id()));
    drop(std::fs::remove_dir_all(&dir));
    std::fs::create_dir_all(&dir).expect("a scratch directory");

    let fabric = Fabric::new();
    let timing = RaftTiming {
        heartbeat_ms,
        election_min_ms: heartbeat_ms * 6,
        election_max_ms: heartbeat_ms * 12,
        ..RaftTiming::default()
    };
    let specs: Vec<MemberSpec> = (0..members)
        .map(|index| MemberSpec {
            host: "127.0.0.1".to_owned(),
            client_port: 2481 + (index as u16) * 2,
            peer_port: 2482 + (index as u16) * 2,
            name: Some(format!("member-{index}")),
            bind_address: None,
        })
        .collect();

    let mut backends = Vec::new();
    for index in 0..members {
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
        let registry = Arc::new(Registry::new(RegistryStore::new()));
        let node = RaftNode::new(
            derive_raft_layout(&layout, token),
            fabric.transport(index as u64) as Arc<dyn Transport>,
            TermStore::new(dir.join(format!("m{index}.json"))),
            StateMachine::new(
                index as u64,
                CursorAllocator::new(index as u64).expect("a lane"),
            ),
            registry.clone(),
            timing,
        );
        backends.push(RaftRegistryBackend::new(
            registry,
            node,
            Duration::from_secs(5),
        ));
    }
    for backend in &backends {
        backend
            .node()
            .set_forward_handler(Arc::clone(backend) as Arc<dyn ForwardHandler>);
        backend.start().await.expect("starts");
    }
    for _ in 0..400 {
        if backends.iter().all(|b| b.state() == BackendState::Ready) {
            break;
        }
        tokio::time::sleep(Duration::from_millis(5)).await;
    }

    // Drive a member that is definitely **not** the leader, so the measurement
    // is the same shape every run. Which member wins an election is
    // deliberately randomised, so picking member 0 and hoping gives a number
    // that means something different from run to run -- and the follower path
    // is the one under investigation.
    let leader = backends
        .iter()
        .position(|b| b.node().leader() == Some(b.node().index()))
        .unwrap_or(0);
    let driven = (0..members).find(|&i| i != leader).unwrap_or(0);

    // Counted from here, so the election is not in the measurement.
    fabric.reset_counts();
    let mut latencies = Vec::with_capacity(registrations);
    for index in 0..registrations {
        let id = format!("{index:08x}-0000-4000-8000-000000000000");
        let started = std::time::Instant::now();
        backends[driven]
            .register(ResourceType::Node, node_body(&id))
            .await
            .expect("commits")
            .expect("accepted");
        latencies.push(started.elapsed());
    }
    let counts = fabric.delivered();
    latencies.sort_unstable();
    eprintln!(
        "    latency p50 {:?}  p90 {:?}  p99 {:?}  (drove follower {driven}, leader {leader})",
        latencies[latencies.len() / 2],
        latencies[latencies.len() * 9 / 10],
        latencies[latencies.len() * 99 / 100],
    );

    let p90 = latencies[latencies.len() * 9 / 10];
    for backend in &backends {
        backend.close().await;
    }
    drop(std::fs::remove_dir_all(&dir));
    (counts, p90)
}

#[tokio::test(flavor = "multi_thread")]
async fn the_message_cost_of_a_registration_by_cluster_size() {
    const REGISTRATIONS: usize = 200;
    for (members, heartbeat) in [(1usize, 10u64), (3, 10), (5, 10), (5, 40)] {
        let (counts, _p90) = cost_of(members, REGISTRATIONS, heartbeat).await;
        let total: u64 = counts.iter().map(|&(_, n)| n).sum();
        let with_entries = counts
            .iter()
            .find(|&&(name, _)| name == "AppendEntries(entries)")
            .map_or(0, |&(_, n)| n);
        eprintln!(
            "{members} members @ {heartbeat}ms heartbeat: {total} messages for \
             {REGISTRATIONS} registrations ({:.1}/registration), carrying \
             entries: {with_entries}",
            total as f64 / REGISTRATIONS as f64,
        );
        for (name, count) in counts {
            eprintln!("    {name:26} {count}");
        }
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn a_follower_driven_registration_does_not_wait_for_a_heartbeat() {
    // The defect this pins, and the shape that proves it.
    //
    // A registration driven at a follower resolves only when that follower
    // applies it, which needs its commit index to move, which needs an
    // `AppendEntries` whose window covers the entry. If the leader only sends
    // that on its next tick, the caller waits a heartbeat -- and the signature
    // is that **the tail tracks the heartbeat interval** rather than the round
    // trip.
    //
    // So the assertion is not "fast enough", which would be a number pulled
    // from nowhere and flaky on a loaded machine. It is that lengthening the
    // heartbeat four-fold does not lengthen the tail: if it did, the tail is
    // the tick and not the work.
    //
    // Measured before the fix: p90 of 10.5 ms at a 10 ms heartbeat and 39.6 ms
    // at 40 ms -- tracking it almost exactly. After: 1.9 ms and 1.8 ms.
    const REGISTRATIONS: usize = 200;

    let (_, quick) = cost_of(5, REGISTRATIONS, 10).await;
    let (_, slow) = cost_of(5, REGISTRATIONS, 40).await;

    // Generous, because this is a timing test on a shared machine: the point is
    // the *shape*, and a tail that was the tick would be four times longer, not
    // twice.
    assert!(
        slow < quick * 3 + Duration::from_millis(5),
        "the p90 grew from {quick:?} to {slow:?} when the heartbeat went from \
         10 ms to 40 ms. A tail that tracks the heartbeat is a caller waiting \
         for a tick, which means the leader is not sending a peer what it is \
         missing until one fires.",
    );
}
