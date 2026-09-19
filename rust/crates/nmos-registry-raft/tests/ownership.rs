// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Ownership is a replicated derivation, so every member computes the same one.
//!
//! Port of `nmos/raft/tests/test_ownership.py`. The properties that matter are
//! determinism -- two members applying the same entries must agree, including
//! about *order* -- and that the table survives a snapshot, because a follower
//! that rebuilt it only from later entries would believe every Node was unowned
//! and start claiming Nodes that already have owners.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::arithmetic_side_effects,
    clippy::panic
)]

use nmos_registry_raft::ownership::{Ownership, OwnershipTable};

// -- claiming ---------------------------------------------------------------

#[test]
fn an_unowned_node_has_no_owner() {
    let table = OwnershipTable::new();
    assert_eq!(table.owner_of("a"), None);
    assert!(!table.is_owned_by("a", 0));
    assert!(!table.contains("a"));
    assert!(table.is_empty());
}

#[test]
fn claiming_records_the_owner_and_the_epoch() {
    let mut table = OwnershipTable::new();
    assert!(table.claim("a", 1, 5));
    assert_eq!(table.owner_of("a"), Some(Ownership { owner: 1, epoch: 5 }));
}

#[test]
fn a_later_claim_takes_over() {
    let mut table = OwnershipTable::new();
    table.claim("a", 1, 5);
    assert!(table.claim("a", 2, 6));
    assert_eq!(table.owner_of("a"), Some(Ownership { owner: 2, epoch: 6 }));
}

#[test]
fn a_stale_claim_is_ignored() {
    // The tripwire. In a correctly ordered apply this cannot fire -- epochs are
    // log indices -- but a table that accepted it would leave two members each
    // believing they owned the Node, and the divergence would surface a long
    // way from the cause.
    let mut table = OwnershipTable::new();
    table.claim("a", 1, 5);

    assert!(
        !table.claim("a", 2, 4),
        "a claim from the past was accepted"
    );
    assert!(
        !table.claim("a", 2, 5),
        "a claim at the same epoch was accepted, so which of the two members \
         owns the Node depends on apply order",
    );
    assert_eq!(table.owner_of("a"), Some(Ownership { owner: 1, epoch: 5 }));
}

#[test]
fn ownership_is_per_node() {
    let mut table = OwnershipTable::new();
    table.claim("a", 1, 5);
    table.claim("b", 2, 6);
    assert!(table.is_owned_by("a", 1));
    assert!(table.is_owned_by("b", 2));
    assert!(!table.is_owned_by("a", 2));
}

// -- releasing --------------------------------------------------------------

#[test]
fn releasing_leaves_the_node_unowned() {
    let mut table = OwnershipTable::new();
    table.claim("a", 1, 5);
    assert!(table.release("a", 6));
    assert_eq!(table.owner_of("a"), None);
    assert!(!table.contains("a"));
}

#[test]
fn releasing_an_unowned_node_changes_nothing() {
    let mut table = OwnershipTable::new();
    assert!(!table.release("a", 6));
}

#[test]
fn a_stale_release_is_ignored() {
    let mut table = OwnershipTable::new();
    table.claim("a", 1, 5);
    assert!(!table.release("a", 4));
    assert!(!table.release("a", 5));
    assert!(table.is_owned_by("a", 1));
}

// -- member down ------------------------------------------------------------

#[test]
fn every_node_the_member_owned_is_released() {
    let mut table = OwnershipTable::new();
    table.claim("a", 1, 1);
    table.claim("b", 1, 2);
    table.claim("c", 2, 3);

    let released = table.member_down(1, 10);
    assert_eq!(released, vec!["a".to_owned(), "b".to_owned()]);
    assert_eq!(table.owner_of("a"), None);
    assert_eq!(table.owner_of("b"), None);
    assert!(
        table.is_owned_by("c", 2),
        "another member's Node was released",
    );
}

#[test]
fn a_member_owning_nothing_releases_nothing() {
    let mut table = OwnershipTable::new();
    table.claim("a", 0, 1);
    assert!(table.member_down(2, 10).is_empty());
}

#[test]
fn the_released_set_is_deterministic() {
    // Every member applying the entry must compute the same answer, in the same
    // order. Insertion order differs between a member that has been up for a
    // week and one that just installed a snapshot -- so the order has to come
    // from the contents, not the history.
    let mut forward = OwnershipTable::new();
    for (index, node_id) in ["c", "a", "b"].iter().enumerate() {
        forward.claim(node_id, 1, index as u64 + 1);
    }

    let mut backward = OwnershipTable::new();
    for (index, node_id) in ["b", "a", "c"].iter().enumerate() {
        backward.claim(node_id, 1, index as u64 + 1);
    }

    assert_eq!(forward.member_down(1, 99), backward.member_down(1, 99));
}

// -- snapshot transfer ------------------------------------------------------

#[test]
fn a_table_round_trips() {
    let mut table = OwnershipTable::new();
    table.claim("node-1", 0, 5);
    table.claim("node-2", 2, 7);

    let restored = OwnershipTable::decode(&table.encode()).expect("decodes");
    assert_eq!(
        restored.owner_of("node-1"),
        Some(Ownership { owner: 0, epoch: 5 })
    );
    assert_eq!(
        restored.owner_of("node-2"),
        Some(Ownership { owner: 2, epoch: 7 })
    );
    assert_eq!(restored.len(), 2);
}

#[test]
fn an_empty_table_round_trips() {
    let empty = OwnershipTable::new();
    assert!(
        OwnershipTable::decode(&empty.encode())
            .expect("decodes")
            .is_empty()
    );
}

#[test]
fn the_encoding_is_order_independent() {
    // Equal tables must serialise identically, whatever their history. Without
    // it, two members with the same ownership would produce different snapshot
    // bytes, and a snapshot could not be compared or checksummed at all.
    let mut forward = OwnershipTable::new();
    forward.claim("z", 1, 1);
    forward.claim("a", 2, 2);

    let mut backward = OwnershipTable::new();
    backward.claim("a", 2, 2);
    backward.claim("z", 1, 1);

    assert_eq!(forward.encode(), backward.encode());
}

#[test]
fn ownership_travels_with_the_snapshot() {
    // A follower that rebuilt ownership only from later entries would believe
    // every Node was unowned, and would start claiming Nodes that already have
    // owners.
    let mut leader = OwnershipTable::new();
    leader.claim("node-1", 0, 100);

    let mut follower = OwnershipTable::decode(&leader.encode()).expect("decodes");
    assert!(follower.is_owned_by("node-1", 0));
    assert!(
        !follower.claim("node-1", 1, 50),
        "the epoch did not survive the snapshot, so a claim from before it was \
         accepted",
    );
}

#[test]
fn a_member_owning_a_thousand_nodes_releases_them_all_in_one_step() {
    // The reason `member_down` is one operation rather than one entry per Node:
    // a member holding a thousand Nodes must not put a thousand entries through
    // consensus at the exact moment the cluster is already a member short.
    let mut table = OwnershipTable::new();
    for index in 0..1000u64 {
        table.claim(&format!("node-{index:04}"), 1, index + 1);
    }
    let released = table.member_down(1, 5000);
    assert_eq!(released.len(), 1000);
    assert!(table.is_empty());

    // And in sorted order, which is what makes every member's release list
    // identical rather than merely equal as a set.
    let mut sorted = released.clone();
    sorted.sort();
    assert_eq!(released, sorted);
}
