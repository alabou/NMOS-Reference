// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Does this operation codec produce the bytes the Python one produces?
//!
//! `operation_vectors.json` is exported from `test_operations.py`'s `_SAMPLES`
//! **without modifying that file**.
//!
//! This is the layer where a difference stops being a connection problem and
//! becomes a correctness one. A frame that decodes differently fails to parse;
//! an *operation* that decodes differently applies a different mutation from
//! the same committed entry -- so two members reach different states from an
//! identical log, which is precisely the State Machine Safety violation the
//! whole package exists to prevent.
//!
//! Regenerate with `python -m nmos.raft.tests._operations_corpus`.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::arithmetic_side_effects,
    clippy::panic
)]

use std::path::Path;

use nmos_registry_core::cursor::TaiCursor;
use nmos_registry_core::resource_type::ResourceType;
use nmos_registry_raft::operations::{Operation, OperationKind, ProposalId, Register};
use serde_json::Value;

fn unhex(text: &str) -> Vec<u8> {
    (0..text.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&text[i..i + 2], 16).expect("hex"))
        .collect()
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn corpus() -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/operation_vectors.json");
    serde_json::from_slice(
        &std::fs::read(&path)
            .unwrap_or_else(|_| panic!("{} is missing -- regenerate it", path.display())),
    )
    .expect("the corpus parses")
}

fn u(fields: &Value, name: &str) -> u64 {
    fields[name]
        .as_u64()
        .unwrap_or_else(|| panic!("field {name:?} is not an unsigned integer"))
}

fn s(fields: &Value, name: &str) -> String {
    fields[name]
        .as_str()
        .unwrap_or_else(|| panic!("field {name:?} is not a string"))
        .to_owned()
}

fn b(fields: &Value, name: &str) -> bool {
    fields[name]
        .as_bool()
        .unwrap_or_else(|| panic!("field {name:?} is not a boolean"))
}

fn cursor(fields: &Value, name: &str) -> TaiCursor {
    let value = &fields[name];
    // The struct literal, like the codec: a nanosecond field at or above one
    // second is pattern-valid and must not be normalised on the way in.
    TaiCursor {
        seconds: value["seconds"].as_u64().expect("seconds"),
        nanoseconds: value["nanoseconds"].as_u64().expect("nanoseconds"),
    }
}

fn resource_type(name: &str) -> ResourceType {
    ResourceType::from_singular(name)
        .unwrap_or_else(|| panic!("the corpus names an unknown resource type {name:?}"))
}

fn proposal(fields: &Value) -> ProposalId {
    let value = &fields["proposal"];
    ProposalId {
        member: value["member"].as_u64().expect("member"),
        sequence: value["sequence"].as_u64().expect("sequence"),
    }
}

/// Rebuild the Rust operation the Python sample described.
///
/// Matched on the Python class name so that an operation the exporter adds and
/// this test cannot build fails loudly, rather than falling into a catch-all
/// that quietly stops covering it.
fn build(name: &str, fields: &Value) -> Operation {
    let kind = match name {
        "NoopOp" => OperationKind::Noop,
        "RegisterOp" => OperationKind::Register(Register {
            resource_type: resource_type(&s(fields, "resource_type")),
            resource_id: s(fields, "resource_id"),
            node_id: s(fields, "node_id"),
            body_text: s(fields, "body_text"),
            created: cursor(fields, "created"),
            updated: cursor(fields, "updated"),
            health: u(fields, "health"),
            expect_created: b(fields, "expect_created"),
            claim_owner: if fields["claim_owner"].is_null() {
                None
            } else {
                Some(u(fields, "claim_owner"))
            },
        }),
        "UnregisterOp" => OperationKind::Unregister {
            resource_type: resource_type(&s(fields, "resource_type")),
            resource_id: s(fields, "resource_id"),
        },
        "ExpireOp" => OperationKind::Expire {
            node_id: s(fields, "node_id"),
        },
        "ForgetOp" => OperationKind::Forget {
            victims: fields["victims"]
                .as_array()
                .expect("victims")
                .iter()
                .map(|pair| {
                    let parts = pair.as_array().expect("a victim pair");
                    (
                        resource_type(parts[0].as_str().expect("a resource type")),
                        parts[1].as_str().expect("a resource id").to_owned(),
                    )
                })
                .collect(),
        },
        "ClaimOwnershipOp" => OperationKind::ClaimOwnership {
            node_id: s(fields, "node_id"),
            owner: u(fields, "owner"),
        },
        "ReleaseOwnershipOp" => OperationKind::ReleaseOwnership {
            node_id: s(fields, "node_id"),
        },
        "MemberDownOp" => OperationKind::MemberDown {
            member: u(fields, "member"),
        },
        other => panic!(
            "the corpus carries a {other} sample this test cannot build. Add \
             it here rather than letting the case go unchecked."
        ),
    };
    Operation {
        proposal: proposal(fields),
        kind,
    }
}

#[test]
fn every_python_sample_encodes_to_the_same_bytes() {
    let corpus = corpus();
    let cases = corpus["cases"].as_array().expect("cases");
    assert!(!cases.is_empty(), "the corpus is empty");

    for case in cases {
        let name = case["operation"].as_str().expect("an operation name");
        let expected = case["encoded_hex"].as_str().expect("the encoded bytes");
        assert_eq!(
            hex(&build(name, &case["fields"]).encode()),
            expected,
            "{name} encodes differently from the Python, so the same committed \
             entry means different things on the two implementations",
        );
    }
}

#[test]
fn every_python_sample_decodes_back_to_itself() {
    for case in corpus()["cases"].as_array().expect("cases") {
        let name = case["operation"].as_str().expect("an operation name");
        let recorded = unhex(case["encoded_hex"].as_str().expect("the encoded bytes"));

        let decoded =
            Operation::decode(&recorded).unwrap_or_else(|e| panic!("{name} failed to decode: {e}"));
        assert_eq!(
            decoded,
            build(name, &case["fields"]),
            "{name} decoded to something other than the sample it was built \
             from -- a field that encodes but does not decode reads as its \
             default here, and apply would then mutate the store differently",
        );
    }
}

#[test]
fn the_corpus_covers_every_operation_kind() {
    let corpus = corpus();
    let mut covered: Vec<u64> = corpus["cases"]
        .as_array()
        .expect("cases")
        .iter()
        .map(|case| case["kind"].as_u64().expect("a kind byte"))
        .collect();
    covered.sort_unstable();
    covered.dedup();
    assert_eq!(
        covered,
        vec![0, 1, 2, 3, 4, 5, 6, 7],
        "an operation kind has no vector, so the Rust codec is untested for it",
    );
}

// -- the properties the corpus cannot record --------------------------------

#[test]
fn an_absent_ownership_claim_is_not_member_zero() {
    // Member indices start at 0, so a `claim_owner` that encoded `None` as a
    // sentinel zero would hand the first member ownership of every Node on
    // every registration that was not claiming anything.
    let plain = Register {
        resource_type: ResourceType::Sender,
        resource_id: "s1".to_owned(),
        node_id: "n1".to_owned(),
        body_text: "{}".to_owned(),
        created: TaiCursor::new(1, 0),
        updated: TaiCursor::new(1, 0),
        health: 1,
        expect_created: true,
        claim_owner: None,
    };
    let operation = Operation {
        proposal: ProposalId {
            member: 0,
            sequence: 1,
        },
        kind: OperationKind::Register(plain.clone()),
    };
    let OperationKind::Register(decoded) = Operation::decode(&operation.encode())
        .expect("decodes")
        .kind
    else {
        panic!("decoded as the wrong kind")
    };
    assert_eq!(
        decoded.claim_owner, None,
        "a registration claiming nothing decoded as a claim for member 0",
    );

    let claiming = Operation {
        proposal: operation.proposal,
        kind: OperationKind::Register(Register {
            claim_owner: Some(0),
            ..plain
        }),
    };
    let OperationKind::Register(decoded) =
        Operation::decode(&claiming.encode()).expect("decodes").kind
    else {
        panic!("decoded as the wrong kind")
    };
    assert_eq!(
        decoded.claim_owner,
        Some(0),
        "member 0 claiming a Node decoded as claiming nothing",
    );
}

#[test]
fn a_body_survives_verbatim() {
    // The fidelity guarantee, at the point it is easiest to break. A body that
    // was re-encoded here -- however losslessly it looked -- would serve
    // something other than what the client registered, on every member.
    let body = "{\"id\":\"x\",\r\n  \"vendor:ext\":[1.0,2.50,3e10],\
                \"emoji\":\"\u{1f600}\",\"trailing\":null}   ";
    let operation = Operation {
        proposal: ProposalId {
            member: 1,
            sequence: 1,
        },
        kind: OperationKind::Register(Register {
            resource_type: ResourceType::Node,
            resource_id: "n1".to_owned(),
            node_id: "n1".to_owned(),
            body_text: body.to_owned(),
            created: TaiCursor::new(1, 0),
            updated: TaiCursor::new(1, 0),
            health: 1,
            expect_created: true,
            claim_owner: None,
        }),
    };
    let OperationKind::Register(decoded) = Operation::decode(&operation.encode())
        .expect("decodes")
        .kind
    else {
        panic!("decoded as the wrong kind")
    };
    assert_eq!(
        decoded.body_text, body,
        "the body did not survive the round trip byte for byte",
    );
}

#[test]
fn a_cursor_beyond_one_second_is_not_normalised() {
    // `TaiCursor::new` folds an overflowing nanosecond field into the seconds;
    // the Python's constructor does not. A cursor that arrived as
    // `0:5000000000` and was stored as `5:0` would put this member's ordering
    // index somewhere no other member has it.
    let odd = TaiCursor {
        seconds: 0,
        nanoseconds: 5_000_000_000,
    };
    let operation = Operation {
        proposal: ProposalId {
            member: 1,
            sequence: 1,
        },
        kind: OperationKind::Register(Register {
            resource_type: ResourceType::Node,
            resource_id: "n1".to_owned(),
            node_id: "n1".to_owned(),
            body_text: "{}".to_owned(),
            created: odd,
            updated: odd,
            health: 1,
            expect_created: true,
            claim_owner: None,
        }),
    };
    let OperationKind::Register(decoded) = Operation::decode(&operation.encode())
        .expect("decodes")
        .kind
    else {
        panic!("decoded as the wrong kind")
    };
    assert_eq!(
        decoded.created, odd,
        "the cursor was normalised on the way in"
    );
    assert_eq!(decoded.created.seconds, 0);
    assert_eq!(decoded.created.nanoseconds, 5_000_000_000);
}

#[test]
fn an_unknown_operation_kind_is_refused_rather_than_guessed() {
    // Unknown *fields* are skipped, because a newer peer may send them. An
    // unknown *kind* is different: guessing which mutation it meant is how one
    // member applies something the rest of the cluster did not.
    let payload = nmos_registry_raft::wire::Writer::new()
        .uint(1, 99)
        .bytes(2, &[])
        .take();
    let error = Operation::decode(&payload).expect_err("refused");
    assert!(
        error.0.contains("unknown operation kind 99"),
        "unhelpful refusal: {}",
        error.0,
    );
}

#[test]
fn an_operation_with_no_kind_is_refused() {
    let payload = nmos_registry_raft::wire::Writer::new().bytes(2, &[]).take();
    let error = Operation::decode(&payload).expect_err("refused");
    assert!(
        error.0.contains("no kind"),
        "unhelpful refusal: {}",
        error.0,
    );
}

#[test]
fn an_unknown_resource_type_is_refused() {
    let body = nmos_registry_raft::wire::Writer::new()
        .bytes(
            1,
            &nmos_registry_raft::wire::Writer::new()
                .uint(1, 0)
                .uint(2, 1)
                .take(),
        )
        .string(2, "sprocket")
        .string(3, "x")
        .take();
    let payload = nmos_registry_raft::wire::Writer::new()
        .uint(1, 2) // Unregister
        .bytes(2, &body)
        .take();
    let error = Operation::decode(&payload).expect_err("refused");
    assert!(
        error.0.contains("unknown resource type 'sprocket'"),
        "unhelpful refusal: {}",
        error.0,
    );
}

#[test]
fn an_unknown_field_inside_an_operation_is_skipped() {
    // Forward compatibility: a newer member may add a field, and an older one
    // must keep applying the parts it understands rather than refusing the
    // entry and falling out of the cluster on upgrade.
    let mut encoded = Operation {
        proposal: ProposalId {
            member: 1,
            sequence: 9,
        },
        kind: OperationKind::Expire {
            node_id: "node-1".to_owned(),
        },
    }
    .encode();
    // Append an unknown field to the *outer* frame; the inner body is length
    // prefixed, so this is the level a later version would extend.
    encoded.extend_from_slice(
        &nmos_registry_raft::wire::Writer::new()
            .string(77, "from a later build")
            .take(),
    );

    let decoded = Operation::decode(&encoded).expect("an unknown field is skipped");
    assert_eq!(
        decoded.kind,
        OperationKind::Expire {
            node_id: "node-1".to_owned()
        }
    );
}
