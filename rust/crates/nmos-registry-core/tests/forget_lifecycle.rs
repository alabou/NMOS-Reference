// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The two-stage deletion lifecycle, ported from
//! `nmos/registry/tests/test_forget_lifecycle.py`.
//!
//! Stage one marks a resource **non-extant**; stage two **forgets** it. The
//! intermediate state is what lets a removal grain carry the resource's final
//! content, and what keeps paging cursors monotonic across a delete.
//!
//! # Why the two halves of stage two are separate functions
//!
//! `forgettable` decides and `forget` acts, and the split is not tidiness. A
//! distributed backend has one member decide and **every** member apply the
//! same list -- so the decision has to be a pure function of the store's
//! contents, reproducible on a member that preloaded a minute ago and on one
//! that has been up for a week.
//!
//! That gives each half a rule the other must not break:
//!
//! * `forgettable` reads the clock and mutates nothing;
//! * `forget` mutates and reads **no** clock. Applying a replicated victim list
//!   must produce the same store whenever it happens to arrive.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing
)]

use nmos_registry_core::body::Body;
use nmos_registry_core::event::RegistrationError;
use nmos_registry_core::resource_type::ResourceType;
use nmos_registry_core::store::{RegistryStore, health_now};
use serde_json::json;

const NODE_ID: &str = "3b8be755-08ff-452b-b217-c9151eb21193";
const DEVICE_ID: &str = "a370d258-69de-4422-860a-ee4cf32ee9f4";
const SENDER_ID: &str = "171d5c80-7fff-4c23-9383-46503eb1c63e";

fn store(forget_interval: i64) -> RegistryStore {
    RegistryStore::with_intervals(12, forget_interval)
}

fn node_body(id: &str) -> Body {
    Body::from_value(json!({"id": id, "version": "100:0", "label": "n"}))
}

fn device_body(id: &str, node_id: &str) -> Body {
    Body::from_value(json!({
        "id": id, "version": "100:0", "label": "d", "node_id": node_id,
    }))
}

fn sender_body(id: &str, device_id: &str) -> Body {
    Body::from_value(json!({
        "id": id, "version": "100:0", "label": "s", "device_id": device_id,
    }))
}

/// A Node, a Device and a Sender -- the shortest complete chain.
fn seed(store: &mut RegistryStore) {
    store
        .insert_or_update(ResourceType::Node, node_body(NODE_ID))
        .expect("node");
    store
        .insert_or_update(ResourceType::Device, device_body(DEVICE_ID, NODE_ID))
        .expect("device");
    store
        .insert_or_update(ResourceType::Sender, sender_body(SENDER_ID, DEVICE_ID))
        .expect("sender");
}

// ---------------------------------------------------------------------------
// forgettable: the pure query
// ---------------------------------------------------------------------------

#[test]
fn an_extant_resource_is_never_forgettable() {
    let mut store = store(60);
    seed(&mut store);
    assert!(store.forgettable(None).is_empty());
}

#[test]
fn a_fresh_tombstone_is_not_yet_forgettable() {
    let mut store = store(60);
    seed(&mut store);
    store.delete(ResourceType::Sender, SENDER_ID).unwrap();
    assert!(store.forgettable(None).is_empty());
}

#[test]
fn an_elapsed_tombstone_is_forgettable() {
    let mut store = store(60);
    seed(&mut store);
    store.delete(ResourceType::Sender, SENDER_ID).unwrap();

    let victims = store.forgettable(Some(health_now() + 120));
    assert!(
        victims.contains(&(ResourceType::Sender, SENDER_ID.to_owned())),
        "{victims:?}",
    );
}

#[test]
fn the_query_mutates_nothing() {
    // It answers a question; `forget` acts on the answer.
    let mut store = store(0);
    seed(&mut store);
    store.delete(ResourceType::Sender, SENDER_ID).unwrap();

    let before = store.statistics(0, 0).non_extant;
    let moment = health_now() + 120;
    let first = store.forgettable(Some(moment));
    let second = store.forgettable(Some(moment));

    assert_eq!(first, second, "the query is not idempotent");
    assert_eq!(
        store.statistics(0, 0).non_extant,
        before,
        "the query dropped something",
    );
}

#[test]
fn the_victim_order_does_not_depend_on_insertion_history() {
    // Two members must produce identical victim lists. Bucket iteration order
    // differs between a member that has been up for a week and one that
    // preloaded a minute ago, so a replicated "forget these" has to be a
    // function of the contents rather than of the history.
    let mut forward = store(0);
    forward
        .insert_or_update(ResourceType::Node, node_body(NODE_ID))
        .unwrap();
    forward
        .insert_or_update(ResourceType::Device, device_body(DEVICE_ID, NODE_ID))
        .unwrap();
    forward
        .insert_or_update(ResourceType::Sender, sender_body(SENDER_ID, DEVICE_ID))
        .unwrap();
    forward.delete(ResourceType::Node, NODE_ID).unwrap();

    // The same three resources, registered through a different path: an extra
    // Device registered and forgotten first, so the buckets were grown in a
    // different order.
    let mut shuffled = store(0);
    shuffled
        .insert_or_update(ResourceType::Node, node_body(NODE_ID))
        .unwrap();
    let spare = "00000000-0000-4000-8000-00000000000a";
    shuffled
        .insert_or_update(ResourceType::Device, device_body(spare, NODE_ID))
        .unwrap();
    shuffled.delete(ResourceType::Device, spare).unwrap();
    shuffled.forget(ResourceType::Device, spare);
    shuffled
        .insert_or_update(ResourceType::Device, device_body(DEVICE_ID, NODE_ID))
        .unwrap();
    shuffled
        .insert_or_update(ResourceType::Sender, sender_body(SENDER_ID, DEVICE_ID))
        .unwrap();
    shuffled.delete(ResourceType::Node, NODE_ID).unwrap();

    let moment = health_now() + 120;
    let a = forward.forgettable(Some(moment));
    let b = shuffled.forgettable(Some(moment));
    assert_eq!(a, b, "two histories produced different victim lists");

    let mut sorted = a.clone();
    sorted.sort_by(|x, y| x.0.singular().cmp(y.0.singular()).then(x.1.cmp(&y.1)));
    assert_eq!(a, sorted, "the list is not in its documented order");
}

// ---------------------------------------------------------------------------
// forget: the mutation
// ---------------------------------------------------------------------------

#[test]
fn forgetting_frees_the_id_for_a_different_type() {
    // The user-visible consequence of the leak. While the tombstone holds the
    // id, re-registering it as another type is refused -- and no amount of
    // waiting helps, because the record that owns the id is never dropped.
    let mut store = store(0);
    seed(&mut store);
    store.delete(ResourceType::Sender, SENDER_ID).unwrap();

    let clash = device_body(SENDER_ID, NODE_ID);
    let refused = store.prepare(ResourceType::Device, clash.data());
    assert_eq!(
        refused.unwrap_err().error,
        RegistrationError::IdTypeConflict,
        "the tombstone should still claim the id",
    );

    assert!(store.forget(ResourceType::Sender, SENDER_ID));

    let allowed = store.prepare(ResourceType::Device, clash.data());
    assert!(allowed.is_ok(), "the id was not freed: {allowed:?}");
    store.check_indexes().unwrap();
    store.check_children().unwrap();
}

#[test]
fn forgetting_an_extant_resource_is_refused() {
    // Stage two must not do stage one's job. Dropping a live resource here
    // would erase it with no removal grain, so every subscriber would simply
    // stop seeing it with no event to explain why.
    let mut store = store(60);
    seed(&mut store);

    assert!(!store.forget(ResourceType::Sender, SENDER_ID));
    assert!(store.get(ResourceType::Sender, SENDER_ID).is_some());
}

#[test]
fn forgetting_something_absent_is_harmless() {
    let mut store = store(60);
    assert!(!store.forget(ResourceType::Sender, SENDER_ID));
}

#[test]
fn forgetting_is_idempotent() {
    let mut store = store(0);
    seed(&mut store);
    store.delete(ResourceType::Sender, SENDER_ID).unwrap();

    assert!(store.forget(ResourceType::Sender, SENDER_ID));
    assert!(
        !store.forget(ResourceType::Sender, SENDER_ID),
        "a repeat claimed to drop something",
    );
}

#[test]
fn forgetting_reads_no_clock() {
    // Applying a victim list must give the same store on every member.
    // `forgettable` owns the clock; `forget` must not, or two members applying
    // the same replicated decision at different moments would diverge.
    let mut store = store(10_000);
    seed(&mut store);
    store.delete(ResourceType::Sender, SENDER_ID).unwrap();

    // Nowhere near the forget interval -- and it goes anyway, because the
    // decision was the caller's.
    assert!(
        store.forget(ResourceType::Sender, SENDER_ID),
        "forget consulted the interval, which is forgettable's business",
    );
}

// ---------------------------------------------------------------------------
// Collection still does both stages
// ---------------------------------------------------------------------------

#[test]
fn collection_still_forgets_elapsed_tombstones() {
    // The split into a query and a mutation must not have moved behaviour out
    // of the standalone path.
    let mut store = store(-1);
    seed(&mut store);
    store.delete(ResourceType::Node, NODE_ID).unwrap();
    assert_eq!(store.statistics(0, 0).non_extant, 3);

    store.collect_garbage();
    assert_eq!(
        store.statistics(0, 0).non_extant,
        0,
        "collection stopped doing stage two",
    );
    store.check_indexes().unwrap();
    store.check_children().unwrap();
}

#[test]
fn the_returned_events_are_expiry_only() {
    // Collection returns removal events for what it *expired*. A tombstone it
    // forgets was already announced when it was erased, so announcing it again
    // would emit a second removal grain for one resource.
    let mut store = store(-1);
    seed(&mut store);
    store.delete(ResourceType::Node, NODE_ID).unwrap();

    let events = store.collect_garbage();
    assert!(
        events.is_empty(),
        "forgetting emitted {} removal events; they were already sent at delete",
        events.len(),
    );
}

#[test]
fn the_two_stages_are_independently_suppressible() {
    // A backend that disables expiry must not lose forgetting with it, which
    // is why collection routes through the same pure query rather than
    // inlining the decision.
    let mut store = RegistryStore::with_intervals(100_000, -1);
    seed(&mut store);

    // Nothing expires -- the interval is enormous.
    store
        .get(ResourceType::Node, NODE_ID)
        .unwrap()
        .set_health(health_now());
    store.delete(ResourceType::Sender, SENDER_ID).unwrap();

    let events = store.collect_garbage();
    assert!(events.is_empty(), "something expired that should not have");
    assert_eq!(
        store.statistics(0, 0).non_extant,
        0,
        "the tombstone was not forgotten although its interval had elapsed",
    );
    assert_eq!(
        store.count_extant(ResourceType::Node),
        1,
        "the node was collected although its health was fresh",
    );
}

#[test]
fn a_forgotten_resource_leaves_no_trace_anywhere() {
    // Four structures hold a resource: the bucket, the id->type map, the two
    // cursor indexes, and its parent's child set. A leak in any one of them is
    // invisible until something else trips over it.
    let mut store = store(0);
    seed(&mut store);
    store.delete(ResourceType::Sender, SENDER_ID).unwrap();
    assert!(store.forget(ResourceType::Sender, SENDER_ID));

    assert!(
        store
            .get_including_tombstoned(ResourceType::Sender, SENDER_ID)
            .is_none(),
    );
    assert!(
        store.find_any(SENDER_ID).is_none(),
        "the id->type map still holds it"
    );
    for order in nmos_registry_core::resource::Order::ALL {
        assert!(
            !store
                .index(ResourceType::Sender, order)
                .ids()
                .contains(&SENDER_ID),
            "the {} index still holds it",
            order.wire(),
        );
    }
    store.check_indexes().unwrap();
    store
        .check_children()
        .expect("the parent's child set still names it");
}
