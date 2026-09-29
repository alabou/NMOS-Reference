// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The applier is the only writer, and it must be deterministic.
//!
//! Port of `nmos/raft/tests/test_machine.py`. Two properties carry the weight:
//! every member applying the same log reaches the same store *byte for byte*,
//! and the one thing apply may not do is quietly disagree with the proposer.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::arithmetic_side_effects,
    clippy::panic
)]

use nmos_registry::registry::Registry;
use nmos_registry_core::cursor::TaiCursor;
use nmos_registry_core::resource_type::ResourceType;
use nmos_registry_core::store::RegistryStore;
use nmos_registry_raft::cursors::CursorAllocator;
use nmos_registry_raft::log::Entry;
use nmos_registry_raft::machine::{Outcome, StateMachine};
use nmos_registry_raft::operations::{Operation, OperationKind, ProposalId, Register};
use nmos_registry_raft::ownership::OwnershipTable;
use serde_json::json;

const NODE_ID: &str = "11111111-0000-4000-8000-000000000000";
const DEVICE_ID: &str = "22222222-0000-4000-8000-000000000000";
const SENDER_ID: &str = "33333333-0000-4000-8000-000000000000";

fn node_body(label: &str) -> String {
    json!({
        "id": NODE_ID, "version": "1000:0", "label": label, "description": "",
        "tags": {}, "href": "http://example/", "hostname": "example", "caps": {},
        "api": {
            "versions": ["v1.3"],
            "endpoints": [{"host": "example", "port": 80, "protocol": "http"}],
        },
        "services": [], "clocks": [], "interfaces": [],
    })
    .to_string()
}

fn device_body() -> String {
    json!({
        "id": DEVICE_ID, "version": "1000:1", "label": "d", "description": "",
        "tags": {}, "type": "urn:x-nmos:device:generic", "node_id": NODE_ID,
        "senders": [], "receivers": [], "controls": [],
    })
    .to_string()
}

fn sender_body() -> String {
    json!({
        "id": SENDER_ID, "version": "1000:2", "label": "s", "description": "",
        "tags": {}, "flow_id": null, "device_id": DEVICE_ID,
        "manifest_href": null, "transport": "urn:x-nmos:transport:rtp",
        "interface_bindings": [],
        "subscription": {"receiver_id": null, "active": false},
    })
    .to_string()
}

fn machine() -> StateMachine {
    StateMachine::new(0, CursorAllocator::new(0).expect("a valid owner"))
}

fn registry() -> Registry {
    Registry::new(RegistryStore::new())
}

/// A registration entry with everything the proposer decided.
fn register_entry(
    index: u64,
    resource_type: ResourceType,
    resource_id: &str,
    body_text: String,
    created: bool,
    at: TaiCursor,
) -> Entry<Operation> {
    let operation = Operation {
        proposal: ProposalId {
            member: 0,
            sequence: index,
        },
        kind: OperationKind::Register(Register {
            resource_type,
            resource_id: resource_id.to_owned(),
            node_id: NODE_ID.to_owned(),
            body_text,
            created: at,
            updated: at,
            health: 1234,
            expect_created: created,
            claim_owner: None,
        }),
    };
    Entry {
        term: 1,
        index,
        payload: operation.encode(),
        value: operation,
    }
}

fn entry(index: u64, kind: OperationKind) -> Entry<Operation> {
    let operation = Operation {
        proposal: ProposalId {
            member: 0,
            sequence: index,
        },
        kind,
    };
    Entry {
        term: 1,
        index,
        payload: operation.encode(),
        value: operation,
    }
}

/// The Node, Device and Sender, as three committed entries.
fn seed(machine: &mut StateMachine, registry: &Registry) {
    let entries = vec![
        register_entry(
            1,
            ResourceType::Node,
            NODE_ID,
            node_body("original"),
            true,
            TaiCursor::new(1000, 0),
        ),
        register_entry(
            2,
            ResourceType::Device,
            DEVICE_ID,
            device_body(),
            true,
            TaiCursor::new(1000, 1),
        ),
        register_entry(
            3,
            ResourceType::Sender,
            SENDER_ID,
            sender_body(),
            true,
            TaiCursor::new(1000, 2),
        ),
    ];
    machine.apply(registry, &entries);
}

// -- registrations ----------------------------------------------------------

#[test]
fn a_registration_lands_in_the_store() {
    let registry = registry();
    let mut machine = machine();
    let outcomes = machine.apply(
        &registry,
        &[register_entry(
            1,
            ResourceType::Node,
            NODE_ID,
            node_body("a node"),
            true,
            TaiCursor::new(1000, 0),
        )],
    );

    assert_eq!(outcomes.len(), 1);
    assert_eq!(outcomes[0].1, Outcome::Registered { created: true });
    assert!(registry.get(ResourceType::Node, NODE_ID).is_some());
    assert_eq!(machine.last_applied(), 1);
}

#[test]
fn the_carried_cursors_and_health_are_what_is_stored() {
    // Determinism rule 1 and 2. Left to itself the store reads this member's
    // clock and its own cursor allocator, and every member would then hold a
    // different `created`/`updated` for the same resource -- so every member
    // would page differently.
    let registry = registry();
    let mut machine = machine();
    let at = TaiCursor::new(777, 5);
    machine.apply(
        &registry,
        &[register_entry(
            1,
            ResourceType::Node,
            NODE_ID,
            node_body("a node"),
            true,
            at,
        )],
    );

    let stored = registry.get(ResourceType::Node, NODE_ID).expect("present");
    assert_eq!(
        stored.created, at,
        "the store allocated its own created cursor"
    );
    assert_eq!(
        stored.updated, at,
        "the store allocated its own updated cursor"
    );
    // Health is an atomic outside the locked record (divergence D3), so it has
    // its own accessor rather than riding on the snapshot.
    assert_eq!(
        registry.node_health(NODE_ID),
        Some(1234),
        "the store stamped its own clock instead of the carried health",
    );
}

#[test]
fn the_body_is_stored_verbatim() {
    // Not re-encoded. The fidelity guarantee is about bytes, and a body that
    // round-tripped through a parser would lose whatever the types do not
    // model -- on every member, for every resource.
    let registry = registry();
    let mut machine = machine();
    let raw = format!("  {}\n", node_body("spaced"));
    machine.apply(
        &registry,
        &[register_entry(
            1,
            ResourceType::Node,
            NODE_ID,
            raw.clone(),
            true,
            TaiCursor::new(1000, 0),
        )],
    );

    assert_eq!(
        registry
            .get(ResourceType::Node, NODE_ID)
            .expect("present")
            .body
            .text()
            .to_owned(),
        raw,
    );
}

#[test]
fn an_authoritative_rejection_is_returned_not_raised() {
    // The id-uniqueness check is global, so the proposer genuinely could not
    // decide it. A refusal here is the answer, not a divergence.
    let registry = registry();
    let mut machine = machine();
    seed(&mut machine, &registry);

    // The Sender's id, registered as a Device: a type conflict only the
    // replicated store can see.
    let conflicting = json!({
        "id": SENDER_ID, "version": "1001:0", "label": "clash",
        "description": "", "tags": {}, "type": "urn:x-nmos:device:generic",
        "node_id": NODE_ID, "senders": [], "receivers": [], "controls": [],
    })
    .to_string();

    let outcomes = machine.apply(
        &registry,
        &[register_entry(
            4,
            ResourceType::Device,
            SENDER_ID,
            conflicting,
            true,
            TaiCursor::new(1001, 0),
        )],
    );

    assert!(
        matches!(outcomes[0].1, Outcome::Refused { .. }),
        "expected a refusal, got {:?}",
        outcomes[0].1,
    );
}

#[test]
fn entries_already_applied_are_skipped() {
    // Ordinary after a snapshot install, where the log still holds entries the
    // snapshot covers.
    let registry = registry();
    let mut machine = machine();
    seed(&mut machine, &registry);

    let replayed = machine.apply(
        &registry,
        &[register_entry(
            2,
            ResourceType::Device,
            DEVICE_ID,
            device_body(),
            true,
            TaiCursor::new(1000, 1),
        )],
    );

    assert!(
        replayed.is_empty(),
        "an entry at or below last_applied was applied again",
    );
    assert_eq!(machine.last_applied(), 3);
}

// -- apply decides ------------------------------------------------------------
//
// `expect_created` is the proposer's prediction, made against its own replica.
// A mismatch used to stop the applier for good -- on every member, since apply
// is deterministic -- and it was the chaos soak's largest failure class, from
// ordinary client races. Apply's own `prepare` is the answer.

/// A registration entry with the proposer's two cursors given separately.
fn predicted_entry(
    index: u64,
    resource_type: ResourceType,
    resource_id: &str,
    body_text: String,
    expect_created: bool,
    (created, updated): (TaiCursor, TaiCursor),
    claim_owner: Option<u64>,
) -> Entry<Operation> {
    let operation = Operation {
        proposal: ProposalId {
            member: 0,
            sequence: index,
        },
        kind: OperationKind::Register(Register {
            resource_type,
            resource_id: resource_id.to_owned(),
            node_id: NODE_ID.to_owned(),
            body_text,
            created,
            updated,
            health: 1234,
            expect_created,
            claim_owner,
        }),
    };
    Entry {
        term: 1,
        index,
        payload: operation.encode(),
        value: operation,
    }
}

#[test]
fn a_registration_predicted_as_a_create_that_applies_as_an_update_is_one() {
    // The same new Node registered at two members at once: both saw it absent,
    // both predicted a create, and the second to commit finds it present.
    let registry = registry();
    let mut machine = machine();
    seed(&mut machine, &registry);

    let outcomes = machine.apply(
        &registry,
        &[
            register_entry(
                4,
                ResourceType::Node,
                NODE_ID,
                node_body("updated"),
                true,
                TaiCursor::new(1001, 0),
            ),
            register_entry(
                5,
                ResourceType::Node,
                NODE_ID,
                node_body("again"),
                false,
                TaiCursor::new(1002, 0),
            ),
        ],
    );

    assert_eq!(
        outcomes[0].1,
        Outcome::Registered { created: false },
        "applied as whatever the proposer predicted, not as what the committed state is",
    );
    assert_eq!(
        machine.last_applied(),
        5,
        "the applier stopped at the mispredicted entry and never applied the next",
    );
    let stored = registry.get(ResourceType::Node, NODE_ID).expect("present");
    assert_eq!(
        stored.created,
        TaiCursor::new(1000, 0),
        "an update moved the resource's creation cursor",
    );
}

#[test]
fn a_registration_predicted_as_an_update_that_applies_as_a_create_is_stamped_now() {
    // An update racing a deletion: the proposer saw the Sender and carried its
    // creation cursor; by the time the entry applies the Sender is gone, so the
    // entry creates it -- and a resource created *at* this entry was created at
    // this entry's cursor, not at the one the proposer copied from a record that
    // no longer exists. Stamped with that, it would sit behind cursors a client
    // has already paged past.
    let registry = registry();
    let mut machine = machine();
    let seeded = vec![
        register_entry(
            1,
            ResourceType::Node,
            NODE_ID,
            node_body("original"),
            true,
            TaiCursor::new(1000, 0),
        ),
        register_entry(
            2,
            ResourceType::Device,
            DEVICE_ID,
            device_body(),
            true,
            TaiCursor::new(1000, 1),
        ),
    ];
    machine.apply(&registry, &seeded);

    let outcomes = machine.apply(
        &registry,
        &[predicted_entry(
            3,
            ResourceType::Sender,
            SENDER_ID,
            sender_body(),
            false,
            (TaiCursor::new(900, 0), TaiCursor::new(1002, 0)),
            None,
        )],
    );

    assert_eq!(outcomes[0].1, Outcome::Registered { created: true });
    let stored = registry
        .get(ResourceType::Sender, SENDER_ID)
        .expect("present");
    assert_eq!(
        stored.created,
        TaiCursor::new(1002, 0),
        "a resource this entry created carries the creation cursor the proposer copied \
         from a record that no longer exists",
    );
}

#[test]
fn a_refused_registration_claims_nothing() {
    // A Device whose Node is not registered: `prepare` refuses it. It registered
    // nothing and must own nothing -- the claim once landed before `prepare`, so
    // a refused registration took its Node, and a cluster of Python and Rust
    // members (the Python claims after `prepare`) held two ownership tables.
    let registry = registry();
    let mut machine = machine();

    let outcomes = machine.apply(
        &registry,
        &[predicted_entry(
            1,
            ResourceType::Device,
            DEVICE_ID,
            device_body(),
            true,
            (TaiCursor::new(1000, 1), TaiCursor::new(1000, 1)),
            Some(1),
        )],
    );

    assert!(
        matches!(outcomes[0].1, Outcome::Refused { .. }),
        "the orphan Device was accepted: {:?}",
        outcomes[0].1,
    );
    assert_eq!(
        machine.ownership().owner_of(NODE_ID),
        None,
        "a refused registration claimed its Node",
    );
}

// -- installing over a capture ---------------------------------------------

#[test]
fn installing_a_snapshot_abandons_an_open_capture() {
    // A capture opened before an install photographs the store the install
    // replaces, pinned at an index the installed snapshot has passed. Left
    // open, the compaction serialising it finishes and stores its older
    // snapshot over the installed one.
    let registry = registry();
    let mut machine = machine();
    seed(&mut machine, &registry);
    let ownership = machine.ownership().clone();
    machine
        .snapshots_mut()
        .begin(3, 1, &ownership)
        .expect("no capture is open");

    machine.install_snapshot(
        &registry,
        RegistryStore::new(),
        OwnershipTable::default(),
        10,
    );

    assert!(
        machine.snapshots().capture().is_none(),
        "the install left open the capture that was photographing the store it replaced",
    );
    assert_eq!(machine.last_applied(), 10);
}

#[test]
fn an_update_declared_as_an_update_is_fine() {
    let registry = registry();
    let mut machine = machine();
    seed(&mut machine, &registry);

    let outcomes = machine.apply(
        &registry,
        &[register_entry(
            4,
            ResourceType::Node,
            NODE_ID,
            node_body("updated"),
            false,
            TaiCursor::new(1001, 0),
        )],
    );
    assert_eq!(outcomes[0].1, Outcome::Registered { created: false });
}

// -- deletion, expiry, forget -----------------------------------------------

#[test]
fn unregister_cascades() {
    let registry = registry();
    let mut machine = machine();
    seed(&mut machine, &registry);

    let outcomes = machine.apply(
        &registry,
        &[entry(
            4,
            OperationKind::Unregister {
                resource_type: ResourceType::Device,
                resource_id: DEVICE_ID.to_owned(),
            },
        )],
    );

    assert_eq!(outcomes[0].1, Outcome::Removed(true));
    assert!(registry.get(ResourceType::Device, DEVICE_ID).is_none());
    assert!(
        registry.get(ResourceType::Sender, SENDER_ID).is_none(),
        "the Sender survived its Device",
    );
}

#[test]
fn unregistering_something_absent_reports_false() {
    let registry = registry();
    let mut machine = machine();
    let outcomes = machine.apply(
        &registry,
        &[entry(
            1,
            OperationKind::Unregister {
                resource_type: ResourceType::Node,
                resource_id: NODE_ID.to_owned(),
            },
        )],
    );
    assert_eq!(outcomes[0].1, Outcome::Removed(false));
}

#[test]
fn expiry_removes_the_whole_subtree() {
    let registry = registry();
    let mut machine = machine();
    seed(&mut machine, &registry);

    let outcomes = machine.apply(
        &registry,
        &[entry(
            4,
            OperationKind::Expire {
                node_id: NODE_ID.to_owned(),
            },
        )],
    );

    assert_eq!(outcomes[0].1, Outcome::Count(3));
    for (kind, id) in [
        (ResourceType::Node, NODE_ID),
        (ResourceType::Device, DEVICE_ID),
        (ResourceType::Sender, SENDER_ID),
    ] {
        assert!(registry.get(kind, id).is_none(), "{kind:?} {id} survived");
    }
}

// -- ownership --------------------------------------------------------------

#[test]
fn a_fused_claim_takes_ownership_in_one_entry() {
    // A Node's first registration takes ownership in the same entry rather
    // than paying a second round trip -- which matters because a facility
    // powering up is entirely first registrations.
    let registry = registry();
    let mut machine = machine();

    let mut first = register_entry(
        1,
        ResourceType::Node,
        NODE_ID,
        node_body("a node"),
        true,
        TaiCursor::new(1000, 0),
    );
    if let OperationKind::Register(ref mut op) = first.value.kind {
        op.claim_owner = Some(2);
    }
    machine.apply(&registry, &[first]);

    assert!(
        machine.ownership().is_owned_by(NODE_ID, 2),
        "the fused claim did not take effect",
    );
}

#[test]
fn a_standalone_claim_and_release() {
    let registry = registry();
    let mut machine = machine();
    machine.apply(
        &registry,
        &[entry(
            1,
            OperationKind::ClaimOwnership {
                node_id: NODE_ID.to_owned(),
                owner: 1,
            },
        )],
    );
    assert!(machine.ownership().is_owned_by(NODE_ID, 1));

    machine.apply(
        &registry,
        &[entry(
            2,
            OperationKind::ReleaseOwnership {
                node_id: NODE_ID.to_owned(),
            },
        )],
    );
    assert_eq!(machine.ownership().owner_of(NODE_ID), None);
}

#[test]
fn member_down_releases_everything_that_member_held() {
    let registry = registry();
    let mut machine = machine();
    for (index, node) in ["n-a", "n-b", "n-c"].iter().enumerate() {
        machine.apply(
            &registry,
            &[entry(
                index as u64 + 1,
                OperationKind::ClaimOwnership {
                    node_id: (*node).to_owned(),
                    owner: if index == 2 { 2 } else { 1 },
                },
            )],
        );
    }

    let outcomes = machine.apply(
        &registry,
        &[entry(10, OperationKind::MemberDown { member: 1 })],
    );
    assert_eq!(outcomes[0].1, Outcome::Count(2));
    assert!(machine.ownership().is_owned_by("n-c", 2));
}

#[test]
fn the_epoch_is_the_log_index() {
    // Which makes it monotonic by construction, and "who claimed most
    // recently" answerable without a clock.
    let registry = registry();
    let mut machine = machine();
    machine.apply(
        &registry,
        &[entry(
            42,
            OperationKind::ClaimOwnership {
                node_id: NODE_ID.to_owned(),
                owner: 1,
            },
        )],
    );
    assert_eq!(
        machine.ownership().owner_of(NODE_ID).expect("owned").epoch,
        42,
    );
}

// -- determinism ------------------------------------------------------------

#[test]
fn the_same_log_produces_the_same_store_on_every_member() {
    let log: Vec<Entry<Operation>> = vec![
        register_entry(
            1,
            ResourceType::Node,
            NODE_ID,
            node_body("original"),
            true,
            TaiCursor::new(1000, 0),
        ),
        register_entry(
            2,
            ResourceType::Device,
            DEVICE_ID,
            device_body(),
            true,
            TaiCursor::new(1000, 1),
        ),
        register_entry(
            3,
            ResourceType::Sender,
            SENDER_ID,
            sender_body(),
            true,
            TaiCursor::new(1000, 2),
        ),
        register_entry(
            4,
            ResourceType::Node,
            NODE_ID,
            node_body("renamed"),
            false,
            TaiCursor::new(1001, 0),
        ),
    ];

    // Two members, different indices, so any use of `self.member` in apply
    // would show up here.
    let mut images = Vec::new();
    for member in [0u64, 1] {
        let registry = registry();
        let mut machine = StateMachine::new(member, CursorAllocator::new(member).expect("valid"));
        machine.apply(&registry, &log);

        let mut image = Vec::new();
        for kind in ResourceType::ALL {
            let mut rows: Vec<String> = registry
                .health_snapshot()
                .into_iter()
                .filter(|(t, _, _)| *t == kind)
                .map(|(t, id, health)| {
                    let snap = registry.get(t, &id).expect("present");
                    format!(
                        "{} {} {} {} {} {}",
                        t.singular(),
                        id,
                        snap.created,
                        snap.updated,
                        health,
                        snap.body.text(),
                    )
                })
                .collect();
            rows.sort();
            image.extend(rows);
        }
        images.push(image);
    }

    assert_eq!(
        images[0], images[1],
        "two members applying the same log reached different stores",
    );
}

#[test]
fn applying_in_chunks_is_the_same_as_applying_at_once() {
    // The caller is required to apply bounded runs and yield between them, so
    // the split points are arbitrary and must not matter.
    let build = || -> Vec<Entry<Operation>> {
        vec![
            register_entry(
                1,
                ResourceType::Node,
                NODE_ID,
                node_body("original"),
                true,
                TaiCursor::new(1000, 0),
            ),
            register_entry(
                2,
                ResourceType::Device,
                DEVICE_ID,
                device_body(),
                true,
                TaiCursor::new(1000, 1),
            ),
            register_entry(
                3,
                ResourceType::Sender,
                SENDER_ID,
                sender_body(),
                true,
                TaiCursor::new(1000, 2),
            ),
        ]
    };

    let whole_registry = registry();
    let mut whole = machine();
    whole.apply(&whole_registry, &build());

    let chunked_registry = registry();
    let mut chunked = machine();
    for entry in build() {
        chunked.apply(&chunked_registry, std::slice::from_ref(&entry));
    }

    assert_eq!(whole.last_applied(), chunked.last_applied());
    for kind in ResourceType::ALL {
        assert_eq!(
            whole_registry.count_extant(kind),
            chunked_registry.count_extant(kind),
            "{kind:?} differs between the whole and chunked runs",
        );
    }
}

#[test]
fn a_cascade_publishes_events_in_a_total_order() {
    // The store walks a hash set to produce a cascade's events, so their order
    // differs between members. Subscribers would then see the same deletion
    // described differently on each -- a divergence even though every member
    // ends in the same state.
    let registry = registry();
    let mut machine = machine();
    seed(&mut machine, &registry);
    let _ = registry.drain_commits();

    machine.apply(
        &registry,
        &[entry(
            4,
            OperationKind::Expire {
                node_id: NODE_ID.to_owned(),
            },
        )],
    );

    let commits = registry.drain_commits();
    let order: Vec<(&str, &str)> = commits
        .iter()
        .map(|c| {
            (
                c.event.resource_type.singular(),
                c.event.resource_id.as_str(),
            )
        })
        .collect();

    // Deepest first: a subscriber must never see a parent disappear while its
    // children are still present.
    assert_eq!(
        order,
        vec![
            ("sender", SENDER_ID),
            ("device", DEVICE_ID),
            ("node", NODE_ID),
        ],
    );
}

#[test]
fn siblings_in_a_cascade_come_out_in_a_fixed_order() {
    // A three-resource chain is already ordered by depth alone, so it cannot
    // detect a missing tie-break -- measured: removing the sort passed. What
    // needs the tie-break is *siblings*, whose relative order comes straight
    // from a hash map's iteration and therefore differs between members and
    // between runs.
    let registry = registry();
    let mut machine = machine();
    seed(&mut machine, &registry);

    let sender_ids = [
        "aaaaaaaa-0000-4000-8000-000000000000",
        "bbbbbbbb-0000-4000-8000-000000000000",
        "cccccccc-0000-4000-8000-000000000000",
        "dddddddd-0000-4000-8000-000000000000",
    ];
    for (offset, id) in sender_ids.iter().enumerate() {
        let body = json!({
            "id": id, "version": "1000:3", "label": "s", "description": "",
            "tags": {}, "flow_id": null, "device_id": DEVICE_ID,
            "manifest_href": null, "transport": "urn:x-nmos:transport:rtp",
            "interface_bindings": [],
            "subscription": {"receiver_id": null, "active": false},
        })
        .to_string();
        machine.apply(
            &registry,
            &[register_entry(
                4 + offset as u64,
                ResourceType::Sender,
                id,
                body,
                true,
                TaiCursor::new(1000, 10 + offset as u64),
            )],
        );
    }
    let _ = registry.drain_commits();

    machine.apply(
        &registry,
        &[entry(
            20,
            OperationKind::Expire {
                node_id: NODE_ID.to_owned(),
            },
        )],
    );

    let commits = registry.drain_commits();
    let senders: Vec<&str> = commits
        .iter()
        .filter(|c| c.event.resource_type == ResourceType::Sender)
        .map(|c| c.event.resource_id.as_str())
        .collect();

    let mut expected: Vec<&str> = sender_ids.to_vec();
    expected.push(SENDER_ID);
    expected.sort_unstable();

    assert_eq!(
        senders, expected,
        "sibling removals were published in hash order, so two members \
         describe the same deletion differently to their subscribers",
    );
}

#[test]
fn applying_raises_the_cursor_high_water() {
    // The hybrid logical clock. Every cursor that arrives through the log is
    // observed, so this member's next allocation is pushed above it -- without
    // which a member whose clock lags would mint a cursor below one the cluster
    // has already published, and a client mid-page would skip the record.
    let registry = registry();
    let mut machine = machine();

    let far_ahead = TaiCursor::new(TaiCursor::now().seconds + 3600, 0);
    machine.apply(
        &registry,
        &[register_entry(
            1,
            ResourceType::Node,
            NODE_ID,
            node_body("from a member an hour ahead"),
            true,
            far_ahead,
        )],
    );

    assert_eq!(
        machine.cursors().high_water(ResourceType::Node),
        Some(far_ahead),
        "apply did not observe the committed cursor",
    );
    assert!(
        machine.cursors_mut().allocate(ResourceType::Node) > far_ahead,
        "the next local allocation is below a cursor the cluster has already \
         published, so a paging client skips the record",
    );
}

#[test]
fn the_store_already_orders_a_cascade() {
    // What `ordered` in the applier rests on, asserted against the core rather
    // than assumed. The core's child index is a `BTreeSet`, chosen for paging;
    // if it ever becomes a `HashSet`, siblings start arriving in hash order and
    // two members describe the same deletion differently to their subscribers.
    //
    // This is the only test that would notice. The applier sorts anyway, so
    // removing its sort changes nothing -- which is exactly why the assumption
    // needs an assertion of its own rather than a comment.
    let mut store = RegistryStore::new();
    let put = |store: &mut RegistryStore, kind, text: String| {
        let body = nmos_registry_core::body::Body::new(text);
        let prepared = store.prepare(kind, body.data()).expect("accepted");
        store.apply_committed(
            &prepared,
            body,
            Some(TaiCursor::new(1000, 0)),
            Some(TaiCursor::new(1000, 0)),
            Some(1),
        );
    };
    put(&mut store, ResourceType::Node, node_body("n"));
    put(&mut store, ResourceType::Device, device_body());

    let ids = [
        "dddddddd-0000-4000-8000-000000000000",
        "aaaaaaaa-0000-4000-8000-000000000000",
        "cccccccc-0000-4000-8000-000000000000",
        "bbbbbbbb-0000-4000-8000-000000000000",
    ];
    for id in ids {
        put(
            &mut store,
            ResourceType::Sender,
            json!({
                "id": id, "version": "1000:3", "label": "s", "description": "",
                "tags": {}, "flow_id": null, "device_id": DEVICE_ID,
                "manifest_href": null, "transport": "urn:x-nmos:transport:rtp",
                "interface_bindings": [],
                "subscription": {"receiver_id": null, "active": false},
            })
            .to_string(),
        );
    }

    let events = store.delete(ResourceType::Node, NODE_ID).expect("removed");
    let senders: Vec<&str> = events
        .iter()
        .filter(|e| e.resource_type == ResourceType::Sender)
        .map(|e| e.resource_id.as_str())
        .collect();

    let mut sorted = ids.to_vec();
    sorted.sort_unstable();
    assert_eq!(
        senders, sorted,
        "the core no longer yields a cascade's siblings in id order, so the \
         applier's sort has stopped being belt-and-braces and become load-\
         bearing",
    );
}
