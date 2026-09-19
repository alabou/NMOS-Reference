// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! What any distributed backend must satisfy, run against the standalone rig.
//!
//! Port of `test_cluster_conformance.py`. Each case describes a **condition**
//! -- quorum lost, a member gone -- rather than an action on a particular
//! backend, so the same file applies unchanged the day a Raft rig exists.
//!
//! # Why it runs against a one-member rig first
//!
//! To establish that the suite itself is right before there is a second backend
//! to blame. A conformance failure against a brand-new backend is ambiguous:
//! the backend may be wrong, or the case may be. Running it first where the
//! answers are not in doubt removes one possibility in advance.
//!
//! # Skips are load-bearing
//!
//! Most of this suite needs three members. At size 1 those cases **must skip
//! with a stated reason** rather than pass. A suite that reported success while
//! exercising nothing would be worse than none, because it would also report
//! coverage it does not have -- so the last test here asserts that the skips
//! actually happened and names them.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::panic
)]

use std::sync::atomic::{AtomicUsize, Ordering};

use nmos_registry_core::body::Body;
use nmos_registry_core::resource_type::ResourceType;
use nmos_registry_testkit::{ClusterRig, StandaloneRig, require_members};
use serde_json::json;

/// Counts the cases that skipped, so the last test can insist they did.
static SKIPPED: AtomicUsize = AtomicUsize::new(0);

/// Report a skip the way the Python suite does, and record it.
///
/// Rust's test harness has no runtime skip, so this prints the reason and
/// returns. Without the print a skipped case is indistinguishable from one that
/// silently did nothing.
fn skip(case: &str, reason: &str) {
    SKIPPED.fetch_add(1, Ordering::Relaxed);
    println!("  SKIP {case}: {reason}");
}

fn rig() -> StandaloneRig {
    StandaloneRig::new()
}

fn node(id: &str) -> Body {
    Body::from_value(json!({"id": id, "version": "100:0", "label": "n"}))
}

const NODE_A: &str = "3b8be755-08ff-452b-b217-c9151eb21193";
const NODE_B: &str = "aad9ed36-bfb9-400a-9890-a85da2e5842b";

// -- cases that need more members than a standalone rig has -----------------

#[tokio::test]
async fn three_registries_share_one_view() {
    let rig = rig();
    if let Err(reason) = require_members(&rig, 3) {
        skip("three_registries_share_one_view", &reason.to_string());
        return;
    }
    unreachable!("a standalone rig cannot reach here");
}

#[tokio::test]
async fn registries_can_write_concurrently_to_different_nodes() {
    let rig = rig();
    if let Err(reason) = require_members(&rig, 2) {
        skip(
            "registries_can_write_concurrently_to_different_nodes",
            &reason.to_string(),
        );
        return;
    }
    unreachable!("a standalone rig cannot reach here");
}

#[tokio::test]
async fn losing_the_local_member_fails_over_to_the_others() {
    // Skipped for two reasons at once, and the more specific is the honest one:
    // this case is etcd-only. A registry whose members *are* the storage layer
    // has nothing to fail over *to* -- losing the local member loses the
    // registry serving that request.
    skip(
        "losing_the_local_member_fails_over_to_the_others",
        "etcd-only: a backend whose members are the registries has no separate \
         storage layer to fail over to",
    );
}

// -- cases that are meaningful at size 1 ------------------------------------

#[tokio::test]
async fn the_cluster_reports_the_failure_tolerance_it_promises() {
    // Asserted, not assumed. The suite's other cases reason from these numbers,
    // so a rig that reported the wrong tolerance would make them agree with a
    // broken cluster.
    let rig = rig();
    let size = rig.size();
    assert_eq!(
        rig.quorum(),
        size.div_ceil(2),
        "quorum is a majority of the members, whatever stores the data",
    );
    assert_eq!(
        rig.failures_tolerated(),
        size - rig.quorum(),
        "tolerance is what is left over once a quorum is kept",
    );
    // At size 1 specifically: no failure is survivable, and saying otherwise
    // would be the sort of optimism that only shows up during an outage.
    if size == 1 {
        assert_eq!(rig.failures_tolerated(), 0);
    }
}

#[tokio::test]
async fn quorum_loss_stops_writes_but_not_reads() {
    // Meaningful at every size, including one. Committing without quorum is
    // what consensus exists to prevent; refusing reads because writes are
    // impossible would turn a partial outage into a total one.
    let rig = rig();
    rig.start_all().await.expect("the rig starts");
    let backend = rig.backend_for(0).await.expect("member 0");

    backend
        .register(ResourceType::Node, node(NODE_A))
        .await
        .expect("available before the outage")
        .expect("a valid body");

    rig.lose_quorum().await;

    let refused = backend.register(ResourceType::Node, node(NODE_B)).await;
    assert!(
        refused.is_err(),
        "a write committed with no quorum -- the one thing consensus exists \
         to prevent",
    );

    assert!(
        backend.state().serves_queries(),
        "reads were refused because writes were impossible",
    );
    assert!(
        rig.registry().heartbeat(NODE_A).is_some(),
        "the cached view lost a resource it had already accepted",
    );

    rig.stop_all().await;
}

#[tokio::test]
async fn a_killed_member_stops_accepting_writes() {
    // `kill` must not return until the member is genuinely unreachable, or a
    // test expecting the next operation to fail may still reach a dying one.
    let rig = rig();
    rig.start_all().await.expect("the rig starts");
    let backend = rig.backend_for(0).await.expect("member 0");

    rig.kill(0).await;
    assert!(
        backend
            .register(ResourceType::Node, node(NODE_A))
            .await
            .is_err(),
        "a write reached a member that `kill` had already returned for",
    );
}

// -- the skips are real -----------------------------------------------------

#[tokio::test]
async fn zzz_the_inapplicable_cases_actually_skipped() {
    // Named to sort last; cargo runs tests in parallel but this only reads a
    // counter the others increment before returning.
    //
    // The guard on the guard. If a future rig change made `require_members`
    // always succeed, the three cases above would fall through to their
    // `unreachable!` and fail loudly -- but if it made them always *skip*,
    // everything would stay green while the suite exercised nothing. This
    // insists the skips are as many as expected, so "all green" keeps meaning
    // something.
    tokio::time::sleep(std::time::Duration::from_millis(250)).await;
    let skipped = SKIPPED.load(Ordering::Relaxed);
    assert_eq!(
        skipped, 3,
        "expected exactly three cases to skip on a one-member rig \
         (two needing more members, one etcd-only); saw {skipped}. Either a \
          case stopped skipping, or one skipped that should have run.",
    );
}
