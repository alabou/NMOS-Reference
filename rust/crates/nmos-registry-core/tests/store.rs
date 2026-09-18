// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The store's behaviour, ported from `nmos/registry/tests/test_store.py`.
//!
//! Every test here asserts something the IS-04 documents require, and the
//! docstrings say which line. That is not decoration: several of these are
//! places the AMWA mock registry behaves differently, and without the citation
//! a future reader has no way to tell a deliberate strictness from a bug.
//!
//! # The index check that runs after almost every case
//!
//! The paging indexes are a second representation of the same resources, and a
//! mutation path that forgets to update one produces a resource that pages
//! twice or not at all -- with nothing else wrong and no error anywhere.
//! [`RegistryStore::check_indexes`] compares the two representations, and it is
//! called after mutations throughout rather than in one dedicated test, because
//! the question "did *this* operation corrupt the index?" is the one worth
//! being able to answer.

// In a test, `events[0]` IS the assertion -- it must panic when the event
// is missing. Same exemption `lib.rs` grants its own `cfg(test)` modules,
// and for the same reason: the panic-free lints exist for the write path.
#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing
)]

use nmos_registry_core::body::Body;
use nmos_registry_core::cursor::TaiCursor;
use nmos_registry_core::event::{EventKind, RegistrationError};
use nmos_registry_core::resource::Order;
use nmos_registry_core::resource_type::ResourceType;
use nmos_registry_core::store::{RegistryStore, health_now};
use serde_json::json;

const NODE_ID: &str = "3b8be755-08ff-452b-b217-c9151eb21193";
const DEVICE_ID: &str = "a370d258-69de-4422-860a-ee4cf32ee9f4";
const DEVICE_ID_2: &str = "8a4d1c0e-6f3b-4a1e-9a2c-1f5b7d3e9c02";
const SENDER_ID: &str = "171d5c80-7fff-4c23-9383-46503eb1c63e";
const FLOW_ID: &str = "b3bb5be7-9fe9-4324-a5bb-4c70e1084449";
const SOURCE_ID: &str = "c23c6a65-8e91-4f6c-a484-046363dbca29";

/// A version that is always parseable and can be ordered against another.
fn version(seconds: u64) -> String {
    format!("{seconds}:0")
}

fn node_body(id: &str, version_seconds: u64) -> Body {
    Body::from_value(json!({
        "id": id,
        "version": version(version_seconds),
        "label": "test-node",
    }))
}

fn device_body(id: &str, node_id: &str, version_seconds: u64) -> Body {
    Body::from_value(json!({
        "id": id,
        "version": version(version_seconds),
        "label": "test-device",
        "node_id": node_id,
    }))
}

fn child_body(id: &str, device_id: &str, version_seconds: u64) -> Body {
    Body::from_value(json!({
        "id": id,
        "version": version(version_seconds),
        "label": "test-child",
        "device_id": device_id,
    }))
}

/// A Node with a Device beneath it, which is the minimum a child needs.
fn register_tree(store: &mut RegistryStore) {
    store
        .insert_or_update(ResourceType::Node, node_body(NODE_ID, 100))
        .expect("the node registers");
    store
        .insert_or_update(ResourceType::Device, device_body(DEVICE_ID, NODE_ID, 100))
        .expect("the device registers");
    store.check_indexes().expect("indexes agree");
}

fn ids_in_order(store: &RegistryStore, resource_type: ResourceType, order: Order) -> Vec<String> {
    store
        .iter_ordered(resource_type, order)
        .map(|resource| resource.id.clone())
        .collect()
}

// ---------------------------------------------------------------------------
// Registration
// ---------------------------------------------------------------------------

#[test]
fn a_first_registration_creates_and_a_second_updates() {
    // `Behaviour - Registration.md:25`: 201 for a create, 200 for an update.
    let mut store = RegistryStore::new();

    let first = store
        .insert_or_update(ResourceType::Node, node_body(NODE_ID, 100))
        .unwrap();
    assert!(first.created, "a first registration is a create");
    assert_eq!(first.events.len(), 1);
    assert_eq!(first.events[0].kind, EventKind::Added);

    let second = store
        .insert_or_update(ResourceType::Node, node_body(NODE_ID, 101))
        .unwrap();
    assert!(!second.created, "a repeat registration is an update");
    assert_eq!(second.events[0].kind, EventKind::Modified);

    assert_eq!(store.count_extant(ResourceType::Node), 1);
    store.check_indexes().unwrap();
}

#[test]
fn a_modified_event_carries_both_states() {
    // What a subscriber needs to compute the difference itself.
    let mut store = RegistryStore::new();
    store
        .insert_or_update(ResourceType::Node, node_body(NODE_ID, 100))
        .unwrap();

    let updated = Body::from_value(json!({
        "id": NODE_ID, "version": version(101), "label": "renamed",
    }));
    let applied = store.insert_or_update(ResourceType::Node, updated).unwrap();

    let event = &applied.events[0];
    let pre = event.pre.as_ref().expect("a modified event has a pre");
    let post = event.post.as_ref().expect("a modified event has a post");
    assert!(pre.text().contains("test-node"));
    assert!(post.text().contains("renamed"));
}

#[test]
fn the_body_is_served_back_verbatim() {
    // The fidelity guarantee, and every part of it is a thing a
    // parse-and-reserialise would quietly change:
    //
    //   * the double space and the missing one -- whitespace is not canonical;
    //   * `1e3`, which would come back as `1000.0`;
    //   * `\u00e9`, an escape that must stay escaped;
    //   * an emoji, raw UTF-8 that must stay raw. `test_03_2` of the AMWA
    //     suite registers a Node containing one, so this is the case that
    //     suite will find if it is wrong.
    let mut store = RegistryStore::new();
    let escaped = "caf\\u00e9";
    let original = format!(
        r#"{{"id": "{NODE_ID}",  "version":"100:0", "n": 1e3, "e": "{escaped}", "r": "🎬"}}"#
    );
    assert!(
        original.contains(r"\u00e9"),
        "the source lost its escape, so this asserts nothing about escapes",
    );

    store
        .insert_or_update(ResourceType::Node, Body::new(original.clone()))
        .unwrap();

    let stored = store.get(ResourceType::Node, NODE_ID).unwrap();
    assert_eq!(stored.body.text(), original);
    assert!(
        stored.body.text().contains(r"\u00e9"),
        "an escape was expanded"
    );
    assert!(stored.body.text().contains("🎬"), "raw UTF-8 was escaped");
    assert!(
        stored.body.text().contains("1e3"),
        "a number was renormalised"
    );

    // And the parsed view still reads the escape correctly, so preserving the
    // bytes has not cost the field access.
    assert_eq!(stored.body.string_member("e"), Some("café"));
}

#[test]
fn a_resource_without_an_id_or_version_is_rejected() {
    let mut store = RegistryStore::new();

    let no_id = store.insert_or_update(
        ResourceType::Node,
        Body::from_value(json!({"version": "100:0"})),
    );
    assert_eq!(no_id.unwrap_err().error, RegistrationError::Schema);

    let no_version =
        store.insert_or_update(ResourceType::Node, Body::from_value(json!({"id": NODE_ID})));
    assert_eq!(no_version.unwrap_err().error, RegistrationError::Schema);

    let empty_id = store.insert_or_update(
        ResourceType::Node,
        Body::from_value(json!({"id": "", "version": "100:0"})),
    );
    assert_eq!(empty_id.unwrap_err().error, RegistrationError::Schema);
}

// ---------------------------------------------------------------------------
// The five documented 400 conditions
// ---------------------------------------------------------------------------

#[test]
fn an_id_already_used_by_another_type_is_refused() {
    // `Behaviour - Registration.md:101`.
    let mut store = RegistryStore::new();
    register_tree(&mut store);

    // The Device's id, re-registered as a Sender.
    let clash = store.insert_or_update(ResourceType::Sender, child_body(DEVICE_ID, DEVICE_ID, 100));
    let failure = clash.unwrap_err();
    assert_eq!(failure.error, RegistrationError::IdTypeConflict);
    assert!(failure.detail.contains("already registered as a device"));
}

#[test]
fn a_version_going_backwards_is_refused() {
    // `:102`.
    let mut store = RegistryStore::new();
    store
        .insert_or_update(ResourceType::Node, node_body(NODE_ID, 200))
        .unwrap();

    let older = store.insert_or_update(ResourceType::Node, node_body(NODE_ID, 199));
    assert_eq!(
        older.unwrap_err().error,
        RegistrationError::VersionRegression
    );
}

#[test]
fn an_identical_version_is_accepted() {
    // Explicitly NOT an error. A Node that re-registers after a failed
    // heartbeat replays its resources verbatim (`:114`), and refusing that
    // would break the documented recovery path.
    let mut store = RegistryStore::new();
    store
        .insert_or_update(ResourceType::Node, node_body(NODE_ID, 100))
        .unwrap();

    let replay = store
        .insert_or_update(ResourceType::Node, node_body(NODE_ID, 100))
        .expect("a verbatim replay is accepted");
    assert!(!replay.created, "a replay is an update, not a create");
}

#[test]
fn changing_a_parent_by_update_is_refused() {
    // `:103`.
    let mut store = RegistryStore::new();
    register_tree(&mut store);
    store
        .insert_or_update(
            ResourceType::Node,
            node_body("11111111-0000-4000-8000-000000000001", 100),
        )
        .unwrap();

    let moved = store.insert_or_update(
        ResourceType::Device,
        device_body(DEVICE_ID, "11111111-0000-4000-8000-000000000001", 101),
    );
    let failure = moved.unwrap_err();
    assert_eq!(failure.error, RegistrationError::ParentChanged);
    assert!(failure.detail.contains("node_id cannot be modified"));
}

#[test]
fn a_child_without_a_registered_parent_is_refused() {
    // `:55` and `:104`. The AMWA mock skips this entirely, and without it a
    // Sender can outlive every Node and never be collected.
    let mut store = RegistryStore::new();

    let orphan = store.insert_or_update(ResourceType::Device, device_body(DEVICE_ID, NODE_ID, 100));
    let failure = orphan.unwrap_err();
    assert_eq!(failure.error, RegistrationError::ParentMissing);
    assert!(failure.detail.contains("is not registered"));
}

#[test]
fn a_parent_of_the_wrong_type_is_refused() {
    // `:104` -- "the ID matches the wrong type of resource".
    let mut store = RegistryStore::new();
    register_tree(&mut store);

    // A Source whose `device_id` names the *Node*, not a Device.
    let wrong = store.insert_or_update(ResourceType::Source, child_body(SOURCE_ID, NODE_ID, 100));
    let failure = wrong.unwrap_err();
    assert_eq!(failure.error, RegistrationError::ParentMissing);
    assert!(
        failure.detail.contains("names a node"),
        "the detail should say what it found: {}",
        failure.detail,
    );
}

#[test]
fn a_child_missing_its_parent_key_entirely_is_a_schema_error() {
    // Distinct from a parent that is merely absent: the body is malformed
    // rather than the registry being out of step, so it is `:100` not `:104`.
    let mut store = RegistryStore::new();
    register_tree(&mut store);

    let keyless = store.insert_or_update(
        ResourceType::Sender,
        Body::from_value(json!({"id": SENDER_ID, "version": version(100)})),
    );
    let failure = keyless.unwrap_err();
    assert_eq!(failure.error, RegistrationError::Schema);
    assert!(failure.detail.contains("device_id"));
}

#[test]
fn registration_order_is_enforced_end_to_end() {
    // `Behaviour - Registration.md:57-64`: Node, then Device, then the rest.
    // Each step must fail until its parent exists.
    let mut store = RegistryStore::new();

    assert!(
        store
            .insert_or_update(ResourceType::Sender, child_body(SENDER_ID, DEVICE_ID, 100))
            .is_err(),
        "a Sender registered before its Device",
    );
    assert!(
        store
            .insert_or_update(ResourceType::Device, device_body(DEVICE_ID, NODE_ID, 100))
            .is_err(),
        "a Device registered before its Node",
    );

    store
        .insert_or_update(ResourceType::Node, node_body(NODE_ID, 100))
        .unwrap();
    store
        .insert_or_update(ResourceType::Device, device_body(DEVICE_ID, NODE_ID, 100))
        .unwrap();
    store
        .insert_or_update(ResourceType::Sender, child_body(SENDER_ID, DEVICE_ID, 100))
        .expect("the whole chain is present now");
    store.check_indexes().unwrap();
}

// ---------------------------------------------------------------------------
// Deletion
// ---------------------------------------------------------------------------

#[test]
fn deleting_something_unregistered_reports_nothing() {
    // The caller answers 404 on this.
    let mut store = RegistryStore::new();
    assert!(store.delete(ResourceType::Node, NODE_ID).is_none());
}

#[test]
fn a_delete_cascades_to_every_descendant() {
    // `:68` -- "Where a DELETE is issued against a parent resource, all child
    // resources MUST be removed from the registry immediately". The AMWA mock
    // removes only the addressed resource, leaving orphans.
    let mut store = RegistryStore::new();
    register_tree(&mut store);
    store
        .insert_or_update(ResourceType::Sender, child_body(SENDER_ID, DEVICE_ID, 100))
        .unwrap();
    store
        .insert_or_update(ResourceType::Flow, child_body(FLOW_ID, DEVICE_ID, 100))
        .unwrap();

    let events = store.delete(ResourceType::Node, NODE_ID).unwrap();
    assert_eq!(events.len(), 4, "node, device, sender and flow");

    for resource_type in ResourceType::ALL {
        assert_eq!(
            store.count_extant(resource_type),
            0,
            "{resource_type} survived"
        );
    }
    store.check_indexes().unwrap();
}

#[test]
fn a_cascade_removes_children_before_their_parents() {
    // The mirror of the registration ordering rule: a client replaying the
    // events in order must never see a parent disappear while its children are
    // still present.
    let mut store = RegistryStore::new();
    register_tree(&mut store);
    store
        .insert_or_update(ResourceType::Sender, child_body(SENDER_ID, DEVICE_ID, 100))
        .unwrap();

    let events = store.delete(ResourceType::Node, NODE_ID).unwrap();
    let order: Vec<&str> = events.iter().map(|e| e.resource_id.as_str()).collect();
    assert_eq!(order, [SENDER_ID, DEVICE_ID, NODE_ID]);
}

#[test]
fn a_deleted_resource_is_hidden_but_not_forgotten() {
    // Stage one of two. The record survives so a removal grain can carry its
    // final content and so paging cursors stay monotonic across the delete.
    let mut store = RegistryStore::new();
    register_tree(&mut store);
    store.delete(ResourceType::Device, DEVICE_ID).unwrap();

    assert!(store.get(ResourceType::Device, DEVICE_ID).is_none());
    assert!(
        store
            .get_including_tombstoned(ResourceType::Device, DEVICE_ID)
            .is_some(),
        "the tombstone was dropped immediately",
    );
    assert_eq!(store.statistics(0, 0).non_extant, 1);
    store.check_indexes().unwrap();
}

#[test]
fn re_registering_a_deleted_id_is_a_create() {
    // 201, not 200: the Node's own state machine has to stay in step.
    let mut store = RegistryStore::new();
    register_tree(&mut store);
    store.delete(ResourceType::Device, DEVICE_ID).unwrap();

    let revived = store
        .insert_or_update(ResourceType::Device, device_body(DEVICE_ID, NODE_ID, 101))
        .unwrap();
    assert!(revived.created, "reviving a deleted id must answer 201");
    assert_eq!(revived.events[0].kind, EventKind::Added);
    store.check_indexes().unwrap();
}

#[test]
fn a_revived_parent_does_not_adopt_its_erased_children() {
    // Otherwise a later cascade would resurrect-then-re-erase records the
    // client was already told were gone.
    let mut store = RegistryStore::new();
    register_tree(&mut store);
    store
        .insert_or_update(ResourceType::Sender, child_body(SENDER_ID, DEVICE_ID, 100))
        .unwrap();

    store.delete(ResourceType::Node, NODE_ID).unwrap();
    store
        .insert_or_update(ResourceType::Node, node_body(NODE_ID, 101))
        .unwrap();

    // The Device and Sender are still tombstoned; deleting the fresh Node must
    // not emit removal events for them a second time.
    let events = store.delete(ResourceType::Node, NODE_ID).unwrap();
    let ids: Vec<&str> = events.iter().map(|e| e.resource_id.as_str()).collect();
    assert_eq!(
        ids,
        [NODE_ID],
        "the revived node adopted its erased children"
    );
    store.check_indexes().unwrap();
}

#[test]
fn removing_one_does_not_cascade() {
    // The distributed counterpart: the backend has already cascaded, and doing
    // it again locally would emit duplicate removal grains.
    let mut store = RegistryStore::new();
    register_tree(&mut store);

    let event = store.remove_one(ResourceType::Node, NODE_ID).unwrap();
    assert_eq!(event.resource_id, NODE_ID);
    assert_eq!(
        store.count_extant(ResourceType::Device),
        1,
        "remove_one cascaded when it must not",
    );

    // And a repeat is a no-op, which is normal on a watch replay.
    assert!(store.remove_one(ResourceType::Node, NODE_ID).is_none());
}

#[test]
fn the_subtree_is_deepest_first_and_stable() {
    let mut store = RegistryStore::new();
    register_tree(&mut store);
    store
        .insert_or_update(ResourceType::Sender, child_body(SENDER_ID, DEVICE_ID, 100))
        .unwrap();

    let walked = store.subtree(ResourceType::Node, NODE_ID);
    let ids: Vec<&str> = walked.iter().map(|(_, id)| id.as_str()).collect();
    assert_eq!(ids, [SENDER_ID, DEVICE_ID, NODE_ID]);

    // Absent or already-erased resources describe nothing to remove.
    assert!(store.subtree(ResourceType::Node, "nope").is_empty());
}

// ---------------------------------------------------------------------------
// Health and garbage collection
// ---------------------------------------------------------------------------

#[test]
fn a_heartbeat_for_an_unregistered_node_reports_nothing() {
    // The caller answers 404, on which the Node re-registers everything.
    let store = RegistryStore::new();
    assert!(store.heartbeat(NODE_ID).is_none());
}

#[test]
fn a_heartbeat_refreshes_the_whole_subtree() {
    // `RegisteredResource.health`: only Nodes heartbeat, but the refresh is
    // recursive. Without it every sub-resource would expire on its own after
    // one interval and the Node would be left childless while still alive.
    let mut store = RegistryStore::new();
    register_tree(&mut store);
    store
        .insert_or_update(ResourceType::Sender, child_body(SENDER_ID, DEVICE_ID, 100))
        .unwrap();

    for (resource_type, id) in [
        (ResourceType::Node, NODE_ID),
        (ResourceType::Device, DEVICE_ID),
        (ResourceType::Sender, SENDER_ID),
    ] {
        store
            .get(resource_type, id)
            .unwrap()
            .set_health(health_now() - 1000);
    }

    let health = store.heartbeat(NODE_ID).expect("the node is registered");
    for (resource_type, id) in [
        (ResourceType::Node, NODE_ID),
        (ResourceType::Device, DEVICE_ID),
        (ResourceType::Sender, SENDER_ID),
    ] {
        assert_eq!(
            store.get(resource_type, id).unwrap().health(),
            health,
            "{resource_type} was not refreshed by the heartbeat",
        );
    }
}

#[test]
fn collection_takes_a_silent_node_and_its_subtree() {
    // `:51`.
    let mut store = RegistryStore::new();
    register_tree(&mut store);
    store
        .insert_or_update(ResourceType::Sender, child_body(SENDER_ID, DEVICE_ID, 100))
        .unwrap();

    for (resource_type, id) in [
        (ResourceType::Node, NODE_ID),
        (ResourceType::Device, DEVICE_ID),
        (ResourceType::Sender, SENDER_ID),
    ] {
        store
            .get(resource_type, id)
            .unwrap()
            .set_health(health_now() - 100);
    }

    let events = store.collect_garbage();
    assert_eq!(events.len(), 3);
    assert_eq!(store.count_extant(ResourceType::Node), 0);
    assert_eq!(store.count_extant(ResourceType::Sender), 0);
    store.check_indexes().unwrap();
}

#[test]
fn collection_spares_a_node_that_is_still_heartbeating() {
    let mut store = RegistryStore::new();
    register_tree(&mut store);
    store.heartbeat(NODE_ID);

    assert!(store.collect_garbage().is_empty());
    assert_eq!(store.count_extant(ResourceType::Node), 1);
}

#[test]
fn collection_triggers_strictly_past_the_interval() {
    // 12 s is "just after two failed heartbeats at the default 5 second
    // interval" (`:47`); expiring exactly at the boundary would collect a Node
    // whose third heartbeat is still in flight.
    let mut store = RegistryStore::new();
    register_tree(&mut store);

    store
        .get(ResourceType::Node, NODE_ID)
        .unwrap()
        .set_health(health_now() - 12);
    assert!(
        store.collect_garbage().is_empty(),
        "a node exactly at the interval was collected",
    );
    assert_eq!(store.count_extant(ResourceType::Node), 1);

    store
        .get(ResourceType::Node, NODE_ID)
        .unwrap()
        .set_health(health_now() - 13);
    assert_eq!(store.collect_garbage().len(), 2, "node and device");
}

#[test]
fn a_tombstone_is_dropped_once_the_forget_interval_elapses() {
    // Stage two.
    let mut store = RegistryStore::with_intervals(12, 60);
    register_tree(&mut store);
    store.delete(ResourceType::Node, NODE_ID).unwrap();
    assert_eq!(store.statistics(0, 0).non_extant, 2);

    // Not yet due.
    store.collect_garbage();
    assert_eq!(store.statistics(0, 0).non_extant, 2);

    for (resource_type, id) in [
        (ResourceType::Node, NODE_ID),
        (ResourceType::Device, DEVICE_ID),
    ] {
        store
            .get_including_tombstoned(resource_type, id)
            .unwrap()
            .set_health(health_now() - 61);
    }

    store.collect_garbage();
    assert_eq!(store.statistics(0, 0).non_extant, 0);
    assert!(
        store
            .get_including_tombstoned(ResourceType::Node, NODE_ID)
            .is_none(),
    );
    store.check_indexes().unwrap();
}

#[test]
fn forgetting_refuses_to_drop_a_live_resource() {
    // Stage two clears what stage one retired. Dropping a live resource here
    // would erase it without the removal grain its subscribers are owed.
    let mut store = RegistryStore::new();
    register_tree(&mut store);

    assert!(!store.forget(ResourceType::Node, NODE_ID));
    assert_eq!(store.count_extant(ResourceType::Node), 1);
}

#[test]
fn the_pure_queries_read_no_clock_of_their_own() {
    // `forgettable` and `expirable` take the moment as an argument so that one
    // member can decide and every member apply the same list.
    let mut store = RegistryStore::with_intervals(12, 60);
    register_tree(&mut store);
    store.delete(ResourceType::Node, NODE_ID).unwrap();

    let now = health_now();
    assert!(store.forgettable(Some(now)).is_empty());
    assert_eq!(store.forgettable(Some(now + 61)).len(), 2);

    // And the list is sorted, so two members produce the same one.
    let victims = store.forgettable(Some(now + 61));
    let mut sorted = victims.clone();
    sorted.sort_by(|a, b| a.0.singular().cmp(b.0.singular()).then(a.1.cmp(&b.1)));
    assert_eq!(victims, sorted);
}

// ---------------------------------------------------------------------------
// Paging cursors
// ---------------------------------------------------------------------------

#[test]
fn cursors_are_unique_within_a_type() {
    // `APIs - Query Parameters.md:17`. Two registrations in one clock tick
    // would otherwise share a cursor, and a client paging from it would skip
    // whichever record sorted second.
    let mut store = RegistryStore::new();
    store
        .insert_or_update(ResourceType::Node, node_body(NODE_ID, 100))
        .unwrap();
    for n in 0..50u32 {
        let id = format!("{n:08x}-0000-4000-8000-00000000000a");
        store
            .insert_or_update(ResourceType::Device, device_body(&id, NODE_ID, 100))
            .unwrap();
    }

    let cursors: Vec<TaiCursor> = store
        .iter_extant(ResourceType::Device)
        .map(|r| r.updated)
        .collect();
    let mut unique = cursors.clone();
    unique.sort();
    unique.dedup();
    assert_eq!(unique.len(), cursors.len(), "two devices shared a cursor");
}

#[test]
fn cursors_are_monotonic_within_a_type() {
    let mut store = RegistryStore::new();
    store
        .insert_or_update(ResourceType::Node, node_body(NODE_ID, 100))
        .unwrap();
    for n in 0..20u32 {
        let id = format!("{n:08x}-0000-4000-8000-00000000000a");
        store
            .insert_or_update(ResourceType::Device, device_body(&id, NODE_ID, 100))
            .unwrap();
    }

    let order = ids_in_order(&store, ResourceType::Device, Order::Created);
    let expected: Vec<String> = (0..20u32)
        .map(|n| format!("{n:08x}-0000-4000-8000-00000000000a"))
        .collect();
    assert_eq!(
        order, expected,
        "creation order is not the create index order"
    );
}

#[test]
fn created_is_stable_across_updates_and_updated_is_not() {
    let mut store = RegistryStore::new();
    store
        .insert_or_update(ResourceType::Node, node_body(NODE_ID, 100))
        .unwrap();
    let (created, first_update) = {
        let node = store.get(ResourceType::Node, NODE_ID).unwrap();
        (node.created, node.updated)
    };

    store
        .insert_or_update(ResourceType::Node, node_body(NODE_ID, 101))
        .unwrap();
    let node = store.get(ResourceType::Node, NODE_ID).unwrap();
    assert_eq!(node.created, created, "created moved on an update");
    assert!(node.updated > first_update, "updated did not move");
    store.check_indexes().unwrap();
}

#[test]
fn the_registry_cursor_is_not_the_resources_own_version() {
    // The version is Node-controlled, may repeat and may go backwards; paging
    // on it is one of the reasons the AMWA mock's paging does not match.
    let mut store = RegistryStore::new();
    store
        .insert_or_update(ResourceType::Node, node_body(NODE_ID, 100))
        .unwrap();

    let node = store.get(ResourceType::Node, NODE_ID).unwrap();
    assert_eq!(node.version, "100:0");
    assert_ne!(
        node.updated,
        TaiCursor::new(100, 0),
        "the paging cursor was taken from the body's version",
    );
}

// ---------------------------------------------------------------------------
// The cursor-ordered indexes
// ---------------------------------------------------------------------------

#[test]
fn an_update_moves_only_the_update_index() {
    let mut store = RegistryStore::new();
    store
        .insert_or_update(ResourceType::Node, node_body(NODE_ID, 100))
        .unwrap();
    store
        .insert_or_update(ResourceType::Device, device_body(DEVICE_ID, NODE_ID, 100))
        .unwrap();
    store
        .insert_or_update(ResourceType::Device, device_body(DEVICE_ID_2, NODE_ID, 100))
        .unwrap();

    // Update the older one; it should move to the end of `update` only.
    store
        .insert_or_update(ResourceType::Device, device_body(DEVICE_ID, NODE_ID, 101))
        .unwrap();

    assert_eq!(
        ids_in_order(&store, ResourceType::Device, Order::Created),
        [DEVICE_ID, DEVICE_ID_2],
        "the create index reordered on an update",
    );
    assert_eq!(
        ids_in_order(&store, ResourceType::Device, Order::Updated),
        [DEVICE_ID_2, DEVICE_ID],
        "the update index did not reorder",
    );
    store.check_indexes().unwrap();
}

#[test]
fn repeated_updates_leave_exactly_one_index_entry() {
    // The phantom guard at store level. Removing by the *new* cursor would
    // leave the old entry behind, and the resource would page twice.
    let mut store = RegistryStore::new();
    store
        .insert_or_update(ResourceType::Node, node_body(NODE_ID, 100))
        .unwrap();

    for v in 101..=110 {
        store
            .insert_or_update(ResourceType::Node, node_body(NODE_ID, v))
            .unwrap();
        store.check_indexes().unwrap();
    }

    assert_eq!(store.index(ResourceType::Node, Order::Updated).len(), 1);
    assert_eq!(store.index(ResourceType::Node, Order::Created).len(), 1);
    assert_eq!(
        ids_in_order(&store, ResourceType::Node, Order::Updated),
        [NODE_ID]
    );
}

#[test]
fn a_revive_moves_both_indexes() {
    // A revive assigns a fresh `created`, so the create index reorders too --
    // the case a naive "insertion order is create order" index gets wrong,
    // because the record is replaced in place.
    let mut store = RegistryStore::new();
    register_tree(&mut store);
    store
        .insert_or_update(ResourceType::Device, device_body(DEVICE_ID_2, NODE_ID, 100))
        .unwrap();

    store.delete(ResourceType::Device, DEVICE_ID).unwrap();
    store
        .insert_or_update(ResourceType::Device, device_body(DEVICE_ID, NODE_ID, 101))
        .unwrap();

    assert_eq!(
        ids_in_order(&store, ResourceType::Device, Order::Created),
        [DEVICE_ID_2, DEVICE_ID],
        "the revived device kept its old position in the create index",
    );
    assert_eq!(store.index(ResourceType::Device, Order::Created).len(), 2);
    store.check_indexes().unwrap();
}

#[test]
fn non_extant_resources_are_skipped_by_the_ordered_view() {
    let mut store = RegistryStore::new();
    register_tree(&mut store);
    store
        .insert_or_update(ResourceType::Device, device_body(DEVICE_ID_2, NODE_ID, 100))
        .unwrap();
    store.delete(ResourceType::Device, DEVICE_ID_2).unwrap();

    let listed = ids_in_order(&store, ResourceType::Device, Order::Updated);
    assert_eq!(listed, [DEVICE_ID]);
    // Still indexed, though: the tombstone has not been forgotten.
    assert_eq!(store.index(ResourceType::Device, Order::Updated).len(), 2);
    store.check_indexes().unwrap();
}

#[test]
fn forgetting_empties_the_indexes_rather_than_filtering_them() {
    // A leaked entry would keep the forgotten resource's id alive forever.
    let mut store = RegistryStore::with_intervals(12, -1);
    register_tree(&mut store);
    store.delete(ResourceType::Node, NODE_ID).unwrap();
    store.collect_garbage();

    for resource_type in ResourceType::ALL {
        for order in Order::ALL {
            assert!(
                store.index(resource_type, order).is_empty(),
                "{resource_type}/{} still holds entries",
                order.wire(),
            );
        }
    }
    store.check_indexes().unwrap();
}

#[test]
fn out_of_order_cursors_still_page_in_cursor_order() {
    // The property Python maintains with a dirty flag and a lazy re-sort. A
    // distributed preload applies resources in key order, not cursor order,
    // and those cursors are not the registry's to choose.
    let mut store = RegistryStore::new();
    store
        .insert_or_update(ResourceType::Node, node_body(NODE_ID, 100))
        .unwrap();

    // Applied with descending authoritative cursors, as a preload would.
    for (n, seconds) in [(0u32, 500u64), (1, 400), (2, 300), (3, 200)] {
        let id = format!("{n:08x}-0000-4000-8000-00000000000a");
        let body = device_body(&id, NODE_ID, 100);
        let prepared = store.prepare(ResourceType::Device, body.data()).unwrap();
        store.apply_committed(
            &prepared,
            body,
            Some(TaiCursor::new(seconds, 0)),
            Some(TaiCursor::new(seconds, 0)),
            Some(0),
        );
    }

    let order = ids_in_order(&store, ResourceType::Device, Order::Created);
    let expected: Vec<String> = [3u32, 2, 1, 0]
        .iter()
        .map(|n| format!("{n:08x}-0000-4000-8000-00000000000a"))
        .collect();
    assert_eq!(order, expected, "a preload did not page in cursor order");
    store.check_indexes().unwrap();
}

#[test]
fn an_authoritative_cursor_raises_the_local_high_water_mark() {
    // Otherwise a later locally allocated cursor could collide with one that
    // already came from the backend.
    let mut store = RegistryStore::new();
    let far_future = TaiCursor::new(u64::from(u32::MAX), 0);

    let body = node_body(NODE_ID, 100);
    let prepared = store.prepare(ResourceType::Node, body.data()).unwrap();
    store.apply_committed(&prepared, body, Some(far_future), Some(far_future), Some(0));

    let next = store.next_cursor(ResourceType::Node);
    assert!(
        next > far_future,
        "the allocator handed out {next}, at or below the authoritative {far_future}",
    );
}

// ---------------------------------------------------------------------------
// Statistics
// ---------------------------------------------------------------------------

#[test]
fn an_empty_registry_reports_zeroes() {
    let store = RegistryStore::new();
    let stats = store.statistics(0, 0);
    assert_eq!(stats.total, 0);
    assert_eq!(stats.non_extant, 0);
    assert_eq!(stats.most_recent_update, TaiCursor::MIN);
}

#[test]
fn the_total_counts_subscriptions_and_grains_too() {
    // nmos-cpp's `put_resources_statistics`: `total` is every extant resource
    // across all eight kinds.
    let mut store = RegistryStore::new();
    register_tree(&mut store);

    let stats = store.statistics(3, 7);
    assert_eq!(stats.total, 2 + 3 + 7);
    assert_eq!(stats.subscriptions, 3);
    assert_eq!(stats.grains, 7);
}

#[test]
fn non_extant_resources_are_reported_not_deducted() {
    let mut store = RegistryStore::new();
    register_tree(&mut store);
    store.delete(ResourceType::Device, DEVICE_ID).unwrap();

    let stats = store.statistics(0, 0);
    assert_eq!(stats.non_extant, 1);
    assert_eq!(
        *stats.per_type.get(ResourceType::Device),
        0,
        "per-type is extant-only"
    );
    assert_eq!(stats.total, 1, "only the node is extant");
}

#[test]
fn least_health_is_the_minimum_over_extant_resources() {
    let mut store = RegistryStore::new();
    register_tree(&mut store);
    store
        .get(ResourceType::Node, NODE_ID)
        .unwrap()
        .set_health(500);
    store
        .get(ResourceType::Device, DEVICE_ID)
        .unwrap()
        .set_health(300);

    assert_eq!(store.statistics(0, 0).least_health, 300);
}
