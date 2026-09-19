// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! A snapshot is an image of one moment, not of the walk that produced it.
//!
//! Port of `nmos/raft/tests/test_snapshot.py`. Every test here is about the
//! same hazard: the store is mutated in place while the snapshot is being
//! taken, so a naive walk produces a state that never existed on any member.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::arithmetic_side_effects,
    clippy::panic
)]

use nmos_registry_core::body::Body;
use nmos_registry_core::cursor::TaiCursor;
use nmos_registry_core::resource_type::ResourceType;
use nmos_registry_core::store::RegistryStore;
use nmos_registry_raft::ownership::OwnershipTable;
use nmos_registry_raft::snapshot::{
    SNAPSHOT_VERSION, SnapshotStore, collect_all, decode_snapshot, install, walk_order,
};
use serde_json::json;

const NODE_ID: &str = "11111111-0000-4000-8000-000000000000";
const DEVICE_ID: &str = "22222222-0000-4000-8000-000000000000";
const SENDER_ID: &str = "33333333-0000-4000-8000-000000000000";

fn node_body(label: &str) -> serde_json::Value {
    json!({
        "id": NODE_ID, "version": "1000:0", "label": label, "description": "",
        "tags": {}, "href": "http://example/", "hostname": "example",
        "caps": {},
        "api": {
            "versions": ["v1.3"],
            "endpoints": [{"host": "example", "port": 80, "protocol": "http"}],
        },
        "services": [], "clocks": [], "interfaces": [],
    })
}

fn device_body() -> serde_json::Value {
    json!({
        "id": DEVICE_ID, "version": "1000:1", "label": "d", "description": "",
        "tags": {}, "type": "urn:x-nmos:device:generic", "node_id": NODE_ID,
        "senders": [], "receivers": [], "controls": [],
    })
}

fn sender_body(id: &str, label: &str) -> serde_json::Value {
    json!({
        "id": id, "version": "1000:2", "label": label, "description": "",
        "tags": {}, "flow_id": null, "device_id": DEVICE_ID,
        "manifest_href": null, "transport": "urn:x-nmos:transport:rtp",
        "interface_bindings": [], "subscription": {"receiver_id": null, "active": false},
    })
}

fn put(store: &mut RegistryStore, kind: ResourceType, body: serde_json::Value, at: TaiCursor) {
    let body = Body::from_value(body);
    let prepared = store
        .prepare(kind, body.data())
        .unwrap_or_else(|e| panic!("{kind:?} refused: {}", e.detail));
    store.apply_committed(&prepared, body, Some(at), Some(at), Some(1));
}

/// A Node, its Device and one Sender.
fn seeded() -> RegistryStore {
    let mut store = RegistryStore::new();
    put(
        &mut store,
        ResourceType::Node,
        node_body("original"),
        TaiCursor::new(1000, 0),
    );
    put(
        &mut store,
        ResourceType::Device,
        device_body(),
        TaiCursor::new(1000, 1),
    );
    put(
        &mut store,
        ResourceType::Sender,
        sender_body(SENDER_ID, "original"),
        TaiCursor::new(1000, 2),
    );
    store
}

/// Take a whole snapshot the way a one-member cluster does.
fn take(store: &RegistryStore, snapshots: &mut SnapshotStore) -> Vec<u8> {
    let capture = snapshots.capture().expect("a capture is open");
    let (records, live) = collect_all(store, capture);
    snapshots.finish(records, &live).expect("finishes")
}

// -- round trip -------------------------------------------------------------

#[test]
fn a_snapshot_round_trips() {
    let store = seeded();
    let mut snapshots = SnapshotStore::new();
    snapshots
        .begin(10, 2, &OwnershipTable::new())
        .expect("begins");
    let payload = take(&store, &mut snapshots);

    let (meta, _ownership, records) = decode_snapshot(&payload).expect("decodes");
    assert_eq!(meta.last_index, 10);
    assert_eq!(meta.last_term, 2);
    assert_eq!(meta.resources, 3);
    assert_eq!(records.len(), 3);
}

#[test]
fn a_body_survives_a_snapshot_verbatim() {
    // The fidelity guarantee, on the path that caught-up members take. A
    // snapshot that re-encoded bodies would break it on exactly those members,
    // and the difference would surface only as two members disagreeing about a
    // vendor extension.
    let mut store = RegistryStore::new();
    put(
        &mut store,
        ResourceType::Node,
        node_body("original"),
        TaiCursor::new(1000, 0),
    );
    let before = store
        .get(ResourceType::Node, NODE_ID)
        .expect("present")
        .body
        .text()
        .to_owned();

    let mut snapshots = SnapshotStore::new();
    snapshots
        .begin(1, 1, &OwnershipTable::new())
        .expect("begins");
    let payload = take(&store, &mut snapshots);

    let (_meta, _own, records) = decode_snapshot(&payload).expect("decodes");
    assert_eq!(records[0].body.text(), before);
}

#[test]
fn the_ownership_table_travels_with_it() {
    let store = seeded();
    let mut ownership = OwnershipTable::new();
    ownership.claim(NODE_ID, 2, 7);

    let mut snapshots = SnapshotStore::new();
    snapshots.begin(10, 1, &ownership).expect("begins");
    let payload = take(&store, &mut snapshots);

    let (_meta, restored, _records) = decode_snapshot(&payload).expect("decodes");
    assert!(
        restored.is_owned_by(NODE_ID, 2),
        "a member caught up by this snapshot would think the Node was unowned \
         and start claiming a Node that already has an owner",
    );
}

// -- copy on write ----------------------------------------------------------

#[test]
fn a_record_mutated_during_the_capture_keeps_its_pre_image() {
    let mut store = seeded();
    let before = store
        .get(ResourceType::Sender, SENDER_ID)
        .expect("present")
        .body
        .text()
        .to_owned();

    let mut snapshots = SnapshotStore::new();
    snapshots
        .begin(10, 1, &OwnershipTable::new())
        .expect("begins");

    // Apply hands the pre-image over *before* mutating, which is the whole
    // contract. Then the record changes underneath the capture.
    {
        let original = store.get(ResourceType::Sender, SENDER_ID).expect("present");
        snapshots.capture_mut().expect("open").capture(original);
    }
    put(
        &mut store,
        ResourceType::Sender,
        sender_body(SENDER_ID, "renamed after the capture opened"),
        TaiCursor::new(2000, 8),
    );

    let payload = take(&store, &mut snapshots);
    let (_meta, _own, records) = decode_snapshot(&payload).expect("decodes");
    let sender = records
        .iter()
        .find(|r| r.id == SENDER_ID)
        .expect("the sender is in the snapshot");

    assert_eq!(
        sender.body.text(),
        before,
        "the snapshot carries the post-mutation body, so it describes a state \
         later than the index it claims",
    );
    assert!(!sender.body.text().contains("renamed after"));
}

#[test]
fn capture_is_idempotent() {
    // Repeated updates keep the earliest state: the pinned one.
    let mut store = seeded();
    let mut snapshots = SnapshotStore::new();
    snapshots
        .begin(10, 1, &OwnershipTable::new())
        .expect("begins");

    {
        let first = store.get(ResourceType::Sender, SENDER_ID).expect("present");
        snapshots.capture_mut().expect("open").capture(first);
    }
    put(
        &mut store,
        ResourceType::Sender,
        sender_body(SENDER_ID, "second"),
        TaiCursor::new(2000, 8),
    );
    {
        let second = store.get(ResourceType::Sender, SENDER_ID).expect("present");
        snapshots.capture_mut().expect("open").capture(second);
    }

    assert_eq!(snapshots.capture().expect("open").held(), 1);

    // The count alone cannot tell an overwrite from a skip -- the key is the
    // same either way, so `held()` stays 1 whichever happened. What
    // distinguishes them is *which* image was kept, so the snapshot has to be
    // read. Measured: without this, removing the idempotence guard passed.
    let payload = take(&store, &mut snapshots);
    let (_meta, _own, records) = decode_snapshot(&payload).expect("decodes");
    let sender = records
        .iter()
        .find(|r| r.id == SENDER_ID)
        .expect("the sender is in the snapshot");
    assert!(
        sender.body.text().contains("\"label\":\"original\""),
        "the capture kept the *second* pre-image, so the snapshot describes a \
         state later than the index it claims: {}",
        sender.body.text(),
    );
}

#[test]
fn a_record_created_after_the_capture_is_excluded() {
    // Otherwise the snapshot describes a future its index had not reached.
    let mut store = seeded();
    let newcomer = "99999999-0000-4000-8000-000000000000";

    let mut snapshots = SnapshotStore::new();
    snapshots
        .begin(10, 1, &OwnershipTable::new())
        .expect("begins");
    snapshots
        .capture_mut()
        .expect("open")
        .capture_created(ResourceType::Sender, newcomer);
    put(
        &mut store,
        ResourceType::Sender,
        sender_body(newcomer, "newcomer"),
        TaiCursor::new(3000, 8),
    );

    let payload = take(&store, &mut snapshots);
    let (_meta, _own, records) = decode_snapshot(&payload).expect("decodes");
    assert!(
        records.iter().all(|r| r.id != newcomer),
        "a resource created after the pinned index is in the snapshot",
    );
}

#[test]
fn untouched_records_come_from_the_live_store() {
    // No pre-image means nothing changed, so the live record is the image.
    let store = seeded();
    let mut snapshots = SnapshotStore::new();
    snapshots
        .begin(10, 1, &OwnershipTable::new())
        .expect("begins");
    let payload = take(&store, &mut snapshots);

    let (_meta, _own, records) = decode_snapshot(&payload).expect("decodes");
    assert_eq!(records.len(), 3);
}

#[test]
fn a_record_deleted_during_the_capture_is_still_in_the_snapshot() {
    // The orphan path, and the one whose absence is silent: a member caught up
    // during a cascading delete would be missing every resource that cascade
    // removed, and only on that member.
    let mut store = seeded();
    let mut snapshots = SnapshotStore::new();
    snapshots
        .begin(10, 1, &OwnershipTable::new())
        .expect("begins");

    {
        let doomed = store.get(ResourceType::Sender, SENDER_ID).expect("present");
        snapshots.capture_mut().expect("open").capture(doomed);
    }
    store.delete(ResourceType::Sender, SENDER_ID);

    let payload = take(&store, &mut snapshots);
    let (meta, _own, records) = decode_snapshot(&payload).expect("decodes");
    assert_eq!(
        meta.resources, 3,
        "the deleted resource was dropped from the snapshot, so a member \
         caught up by it silently holds less than its peers",
    );
    assert!(records.iter().any(|r| r.id == SENDER_ID));
}

// -- determinism ------------------------------------------------------------

#[test]
fn equal_stores_produce_identical_bytes() {
    // What makes a snapshot comparable or checksummable at all.
    let left_store = seeded();
    let right_store = seeded();

    let mut left = SnapshotStore::new();
    left.begin(1, 1, &OwnershipTable::new()).expect("begins");
    let mut right = SnapshotStore::new();
    right.begin(1, 1, &OwnershipTable::new()).expect("begins");

    assert_eq!(take(&left_store, &mut left), take(&right_store, &mut right));
}

#[test]
fn the_walk_order_is_sorted_within_each_type() {
    // Two members' hash maps iterate differently, so the order has to come from
    // the contents. Without it, equal stores serialise to different bytes.
    let mut store = seeded();
    for id in ["aaaaaaaa", "cccccccc", "bbbbbbbb"] {
        put(
            &mut store,
            ResourceType::Sender,
            sender_body(&format!("{id}-0000-4000-8000-000000000000"), id),
            TaiCursor::new(1500, 0),
        );
    }
    let order = walk_order(&store);
    let senders: Vec<&String> = order
        .iter()
        .filter(|(kind, _)| *kind == ResourceType::Sender)
        .map(|(_, id)| id)
        .collect();

    let mut sorted = senders.clone();
    sorted.sort();
    assert_eq!(senders, sorted, "the walk is not in id order");

    // And types come in a fixed order too.
    let types: Vec<ResourceType> = order.iter().map(|(kind, _)| *kind).collect();
    let mut deduped = types.clone();
    deduped.dedup();
    assert_eq!(
        deduped,
        vec![
            ResourceType::Node,
            ResourceType::Device,
            ResourceType::Sender
        ],
    );
}

// -- rejections -------------------------------------------------------------

#[test]
fn an_unknown_snapshot_version_is_refused() {
    let payload = nmos_registry_raft::wire::Writer::new()
        .uint(1, SNAPSHOT_VERSION + 1)
        .uint(4, 0)
        .take();
    let error = decode_snapshot(&payload).expect_err("refused");
    assert!(
        error.0.contains("snapshot version"),
        "unhelpful refusal: {}",
        error.0,
    );
}

#[test]
fn a_truncated_snapshot_is_refused() {
    // It parsed. Installing it would leave this member silently missing
    // resources its peers hold, which is the failure mode with no symptom.
    let store = seeded();
    let mut snapshots = SnapshotStore::new();
    snapshots
        .begin(10, 1, &OwnershipTable::new())
        .expect("begins");
    let capture = snapshots.capture().expect("open");
    let (records, live) = collect_all(&store, capture);
    let full = snapshots.finish(records, &live).expect("finishes");

    // Re-encode claiming one more resource than it carries.
    let mut doctored = Vec::new();
    let mut reader = nmos_registry_raft::wire::Reader::new(&full);
    let mut writer = nmos_registry_raft::wire::Writer::new();
    while let Some((number, wire)) = reader.next_field().expect("parses") {
        match number {
            1 => writer = writer.uint(1, reader.uint().expect("v")),
            2 => writer = writer.uint(2, reader.uint().expect("i")),
            3 => writer = writer.uint(3, reader.uint().expect("t")),
            4 => writer = writer.uint(4, reader.uint().expect("c") + 1),
            5 => writer = writer.bytes(5, reader.bytes().expect("o")),
            6 => writer = writer.bytes(6, reader.bytes().expect("r")),
            _ => reader.skip(wire).expect("skips"),
        }
    }
    doctored.extend_from_slice(&writer.take());

    let error = decode_snapshot(&doctored).expect_err("refused");
    assert!(
        error.0.contains("claims 4 resources and carries 3"),
        "unhelpful refusal: {}",
        error.0,
    );
}

#[test]
fn a_second_capture_is_refused_while_one_is_open() {
    // Two concurrent captures would each see only the mutations that arrived
    // after it opened, so both would describe a state that never existed.
    let mut snapshots = SnapshotStore::new();
    snapshots
        .begin(1, 1, &OwnershipTable::new())
        .expect("begins");
    let error = snapshots
        .begin(2, 1, &OwnershipTable::new())
        .expect_err("refused");
    assert!(error.0.contains("already open"), "{}", error.0);
}

#[test]
fn abandoning_a_capture_releases_it() {
    let mut snapshots = SnapshotStore::new();
    snapshots
        .begin(1, 1, &OwnershipTable::new())
        .expect("begins");
    snapshots.abandon();
    assert!(snapshots.capture().is_none());
    snapshots
        .begin(2, 1, &OwnershipTable::new())
        .expect("a new capture may open");
}

// -- install ----------------------------------------------------------------

#[test]
fn install_restores_parents_before_children() {
    // The store refuses a child whose parent is absent, so an installer that
    // walked the records in snapshot order would fail on any snapshot whose
    // first record was a Sender.
    let store = seeded();
    let mut snapshots = SnapshotStore::new();
    snapshots
        .begin(10, 1, &OwnershipTable::new())
        .expect("begins");
    let payload = take(&store, &mut snapshots);

    let (_meta, _own, mut records) = decode_snapshot(&payload).expect("decodes");
    // Deepest first, which is the order that breaks a naive installer.
    records.reverse();

    let restored = install(records, 12, 12).expect("installs");
    assert!(restored.get(ResourceType::Node, NODE_ID).is_some());
    assert!(restored.get(ResourceType::Device, DEVICE_ID).is_some());
    assert!(restored.get(ResourceType::Sender, SENDER_ID).is_some());
}

#[test]
fn install_preserves_the_cursors_rather_than_reallocating() {
    // A member caught up by a snapshot must page identically to its peers. If
    // install let the store stamp its own cursors, every restored resource
    // would land at this member's clock instead of the cluster's.
    let store = seeded();
    let mut snapshots = SnapshotStore::new();
    snapshots
        .begin(10, 1, &OwnershipTable::new())
        .expect("begins");
    let payload = take(&store, &mut snapshots);

    let (_meta, _own, records) = decode_snapshot(&payload).expect("decodes");
    let restored = install(records, 12, 12).expect("installs");

    let node = restored.get(ResourceType::Node, NODE_ID).expect("present");
    assert_eq!(node.created, TaiCursor::new(1000, 0));
    assert_eq!(node.updated, TaiCursor::new(1000, 0));
    let sender = restored
        .get(ResourceType::Sender, SENDER_ID)
        .expect("present");
    assert_eq!(sender.updated, TaiCursor::new(1000, 2));
}

#[test]
fn install_keeps_the_intervals_it_was_given() {
    // A store rebuilt from a snapshot has to be configured like the one it
    // replaces, or this member expires and forgets on a different schedule from
    // its peers -- a divergence that only shows up much later, as resources
    // disappearing from one member first.
    let store = seeded();
    let mut snapshots = SnapshotStore::new();
    snapshots
        .begin(10, 1, &OwnershipTable::new())
        .expect("begins");
    let payload = take(&store, &mut snapshots);
    let (_meta, _own, records) = decode_snapshot(&payload).expect("decodes");

    let restored = install(records, 31, 47).expect("installs");
    assert_eq!(restored.gc_interval(), 31);
    assert_eq!(restored.forget_interval(), 47);
}

#[test]
fn orphans_come_out_in_the_python_order_not_the_enum_order() {
    // The sort in `orphans` is by type **name**, which is what the Python
    // sorts by -- and that is not the enum's order. `ResourceType` is declared
    // in registration-dependency order (Node, Device, Source, ...), so the
    // `BTreeMap` holding the pre-images yields Node before Device. Python
    // yields "device" before "node".
    //
    // Nothing inside this implementation can tell the difference: both members
    // hold a `BTreeMap`, so both would be consistently wrong together, and the
    // two-member test below passes either way. Measured -- reversing the sort
    // went undetected until this test existed. What it breaks is a *mixed*
    // cluster, where one member's snapshot bytes no longer match the other's.
    let mut store = seeded();
    let mut snapshots = SnapshotStore::new();
    snapshots
        .begin(10, 1, &OwnershipTable::new())
        .expect("begins");

    for (kind, id) in [
        (ResourceType::Node, NODE_ID),
        (ResourceType::Device, DEVICE_ID),
        (ResourceType::Sender, SENDER_ID),
    ] {
        let doomed = store.get(kind, id).expect("present");
        snapshots.capture_mut().expect("open").capture(doomed);
    }
    // Deleting the Node cascades, so all three become orphans of the walk.
    store.delete(ResourceType::Node, NODE_ID);

    let payload = take(&store, &mut snapshots);
    let (_meta, _own, records) = decode_snapshot(&payload).expect("decodes");
    let order: Vec<&str> = records.iter().map(|r| r.resource_type.singular()).collect();

    assert_eq!(
        order,
        vec!["device", "node", "sender"],
        "orphans are not in the Python's (type name, id) order, so a Python \
         and a Rust member produce different snapshot bytes from equal state",
    );
}

#[test]
fn two_members_order_their_orphans_identically() {
    // Determinism, on the path that only exists when something was deleted
    // mid-capture. With a single orphan any order looks right, so this needs
    // two -- captured in opposite orders on the two members, which is exactly
    // what a cascading delete produces when two members' maps iterate
    // differently.
    let ids = [
        "44444444-0000-4000-8000-000000000000",
        "55555555-0000-4000-8000-000000000000",
    ];

    let mut payloads = Vec::new();
    for order in [[0, 1], [1, 0]] {
        let mut store = seeded();
        for id in ids {
            put(
                &mut store,
                ResourceType::Sender,
                sender_body(id, "doomed"),
                TaiCursor::new(1500, 0),
            );
        }

        let mut snapshots = SnapshotStore::new();
        snapshots
            .begin(10, 1, &OwnershipTable::new())
            .expect("begins");
        for index in order {
            let id = ids[index];
            let doomed = store.get(ResourceType::Sender, id).expect("present");
            snapshots.capture_mut().expect("open").capture(doomed);
        }
        for id in ids {
            store.delete(ResourceType::Sender, id);
        }
        payloads.push(take(&store, &mut snapshots));
    }

    assert_eq!(
        payloads[0], payloads[1],
        "two members produced different snapshot bytes from equal state, so a \
         snapshot cannot be compared or checksummed",
    );
}

#[test]
fn a_body_with_surrounding_whitespace_survives_byte_for_byte() {
    // The fidelity guarantee is about *bytes*, not about equivalent JSON. A
    // snapshot that trimmed, re-indented or re-ordered a body would serve
    // something other than what the client registered -- and every test whose
    // fixture is already-compact JSON would pass. Measured: without this,
    // trimming the body on the way into a snapshot went undetected.
    let raw = format!(
        "  {}\n",
        serde_json::to_string(&node_body("spaced")).expect("json")
    );
    let body = Body::new(raw.clone());
    let mut store = RegistryStore::new();
    let prepared = store
        .prepare(ResourceType::Node, body.data())
        .expect("accepted");
    store.apply_committed(
        &prepared,
        body,
        Some(TaiCursor::new(1000, 0)),
        Some(TaiCursor::new(1000, 0)),
        Some(1),
    );

    let mut snapshots = SnapshotStore::new();
    snapshots
        .begin(1, 1, &OwnershipTable::new())
        .expect("begins");
    let payload = take(&store, &mut snapshots);

    let (_meta, _own, records) = decode_snapshot(&payload).expect("decodes");
    assert_eq!(
        records[0].body.text(),
        raw,
        "the body did not survive the snapshot byte for byte",
    );
}
