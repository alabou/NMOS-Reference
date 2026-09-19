// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Does this message layer produce the bytes the Python one produces?
//!
//! `message_vectors.json` is exported from `test_messages.py`'s `_SAMPLES`
//! **without modifying that file**, the same arrangement decision #12 sets for
//! the frame vectors and for the same reason: two hand-written sample sets
//! drift, and the drift is invisible because each side keeps agreeing with its
//! own.
//!
//! Each sample appears three times -- as the test holds it, with every field
//! saturated to a non-default, and with its optional fields absent. The
//! saturated variant exists because thirteen fields across six samples sit at
//! their defaults, and a field that encodes but does not decode reads back as
//! its default: measured, two decoder mutations survived the unsaturated
//! corpus. See `_messages_corpus.py`.
//!
//! Each case is checked in both directions, and the two catch different things:
//!
//! * **build and encode** -- the Rust struct is assembled from the *field
//!   values* the Python sample held, and its bytes must match. Replaying the
//!   recorded hex through `decode` alone would test the framing and nothing
//!   below it; the field numbers, the varint widths and the repeated-field
//!   layout are where an off-by-one hides.
//! * **decode** -- the recorded bytes must produce that same struct, which is
//!   what catches a field that encodes but does not decode.
//!
//! Regenerate with `python -m nmos.raft.tests._messages_corpus`.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::arithmetic_side_effects,
    clippy::panic
)]

use std::path::Path;

use nmos_registry_raft::messages::{
    AppendEntries, AppendEntriesReply, Forward, ForwardReply, Hello, HelloAck, InstallSnapshot,
    InstallSnapshotReply, Message, Ping, Promote, Propose, ProposeReply, RequestVote,
    RequestVoteReply, WireEntry, decode_message,
};
use nmos_registry_raft::wire::{MessageType, Stream};
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

/// A case's message name and variant, for failure messages that say which of
/// the three a difference is in.
fn names(case: &Value) -> (&str, &str) {
    (
        case["message"].as_str().expect("a message name"),
        case["variant"].as_str().expect("a variant"),
    )
}

fn corpus() -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/message_vectors.json");
    serde_json::from_slice(
        &std::fs::read(&path)
            .unwrap_or_else(|_| panic!("{} is missing -- regenerate it", path.display())),
    )
    .expect("the corpus parses")
}

// -- field accessors --------------------------------------------------------
//
// Each panics rather than defaulting. A missing field that silently became 0
// would let the build-and-encode direction pass by accident whenever the
// encoded form of the default happened to match.

fn field<'a>(fields: &'a Value, name: &str) -> &'a Value {
    fields
        .get(name)
        .unwrap_or_else(|| panic!("the corpus case has no field {name:?}"))
}

fn u(fields: &Value, name: &str) -> u64 {
    field(fields, name)
        .as_u64()
        .unwrap_or_else(|| panic!("field {name:?} is not an unsigned integer"))
}

fn s(fields: &Value, name: &str) -> String {
    field(fields, name)
        .as_str()
        .unwrap_or_else(|| panic!("field {name:?} is not a string"))
        .to_owned()
}

fn b(fields: &Value, name: &str) -> bool {
    field(fields, name)
        .as_bool()
        .unwrap_or_else(|| panic!("field {name:?} is not a boolean"))
}

/// `None` is a real value here, not a missing field -- see "Absent values" in
/// the message module.
fn opt_u(fields: &Value, name: &str) -> Option<u64> {
    let value = field(fields, name);
    if value.is_null() {
        None
    } else {
        Some(
            value
                .as_u64()
                .unwrap_or_else(|| panic!("field {name:?} is neither null nor an integer")),
        )
    }
}

/// The exporter tags bytes as `{"bytes": "<hex>"}`, because a hex string and a
/// string field are indistinguishable once both are JSON strings.
fn bytes(fields: &Value, name: &str) -> Vec<u8> {
    unhex(
        field(fields, name)["bytes"]
            .as_str()
            .unwrap_or_else(|| panic!("field {name:?} is not a tagged byte string")),
    )
}

fn list<'a>(fields: &'a Value, name: &str) -> &'a Vec<Value> {
    field(fields, name)
        .as_array()
        .unwrap_or_else(|| panic!("field {name:?} is not a list"))
}

fn entry(value: &Value) -> WireEntry {
    let fields = &value["entry"];
    WireEntry {
        term: u(fields, "term"),
        index: u(fields, "index"),
        payload: bytes(fields, "payload"),
    }
}

fn stream_of(value: u64) -> Stream {
    match value {
        0 => Stream::Control,
        1 => Stream::Bulk,
        other => panic!("the corpus names an unknown stream {other}"),
    }
}

/// Rebuild the Rust message the Python sample described.
///
/// Matched on the Python class name rather than the type byte so that a message
/// the exporter adds and this test does not know about fails loudly here,
/// instead of falling into a catch-all that quietly stops covering it.
fn build(name: &str, fields: &Value) -> Message {
    match name {
        "Hello" => Message::Hello(Hello {
            major: u(fields, "major"),
            minor: u(fields, "minor"),
            cluster_id: s(fields, "cluster_id"),
            member_name: s(fields, "member_name"),
            member_index: u(fields, "member_index"),
            incarnation: u(fields, "incarnation"),
            stream: stream_of(u(fields, "stream")),
        }),
        "HelloAck" => Message::HelloAck(HelloAck {
            accepted: b(fields, "accepted"),
            reason: s(fields, "reason"),
            minor: u(fields, "minor"),
            member_index: u(fields, "member_index"),
            incarnation: u(fields, "incarnation"),
        }),
        "RequestVote" => Message::RequestVote(RequestVote {
            term: u(fields, "term"),
            candidate: u(fields, "candidate"),
            last_log_index: u(fields, "last_log_index"),
            last_log_term: u(fields, "last_log_term"),
            probe: b(fields, "probe"),
            amnesiac: list(fields, "amnesiac")
                .iter()
                .map(|v| v.as_u64().expect("an amnesiac member index"))
                .collect(),
            pre_vote: b(fields, "pre_vote"),
        }),
        "RequestVoteReply" => Message::RequestVoteReply(RequestVoteReply {
            term: u(fields, "term"),
            granted: b(fields, "granted"),
            voting: b(fields, "voting"),
            pre_vote: b(fields, "pre_vote"),
        }),
        "AppendEntries" => Message::AppendEntries(AppendEntries {
            term: u(fields, "term"),
            leader: u(fields, "leader"),
            prev_log_index: u(fields, "prev_log_index"),
            prev_log_term: u(fields, "prev_log_term"),
            leader_commit: u(fields, "leader_commit"),
            request_id: u(fields, "request_id"),
            entries: list(fields, "entries").iter().map(entry).collect(),
        }),
        "AppendEntriesReply" => Message::AppendEntriesReply(AppendEntriesReply {
            term: u(fields, "term"),
            success: b(fields, "success"),
            match_index: u(fields, "match_index"),
            conflict_index: u(fields, "conflict_index"),
            conflict_term: u(fields, "conflict_term"),
            catching_up: b(fields, "catching_up"),
            request_id: u(fields, "request_id"),
        }),
        "InstallSnapshot" => Message::InstallSnapshot(InstallSnapshot {
            term: u(fields, "term"),
            leader: u(fields, "leader"),
            last_index: u(fields, "last_index"),
            last_term: u(fields, "last_term"),
            offset: u(fields, "offset"),
            data: bytes(fields, "data"),
            done: b(fields, "done"),
            ownership: bytes(fields, "ownership"),
        }),
        "InstallSnapshotReply" => Message::InstallSnapshotReply(InstallSnapshotReply {
            term: u(fields, "term"),
            bytes_received: u(fields, "bytes_received"),
            done: b(fields, "done"),
        }),
        "Promote" => Message::Promote(Promote {
            term: u(fields, "term"),
            leader: u(fields, "leader"),
            through_index: u(fields, "through_index"),
        }),
        "Propose" => Message::Propose(Propose {
            proposals: list(fields, "proposals")
                .iter()
                .map(|v| unhex(v["bytes"].as_str().expect("a tagged proposal")))
                .collect(),
            request_id: u(fields, "request_id"),
        }),
        "ProposeReply" => Message::ProposeReply(ProposeReply {
            accepted: b(fields, "accepted"),
            reason: s(fields, "reason"),
            term: u(fields, "term"),
            first_index: u(fields, "first_index"),
            request_id: u(fields, "request_id"),
            leader: opt_u(fields, "leader"),
        }),
        "Forward" => Message::Forward(Forward {
            verb: s(fields, "verb"),
            resource_type: s(fields, "resource_type"),
            resource_id: s(fields, "resource_id"),
            body_text: s(fields, "body_text"),
            request_id: u(fields, "request_id"),
        }),
        "ForwardReply" => Message::ForwardReply(ForwardReply {
            ok: b(fields, "ok"),
            created: b(fields, "created"),
            error: s(fields, "error"),
            detail: s(fields, "detail"),
            applied_index: u(fields, "applied_index"),
            not_owner: b(fields, "not_owner"),
            request_id: u(fields, "request_id"),
            owner: opt_u(fields, "owner"),
        }),
        "Ping" => Message::Ping(Ping {
            nonce: u(fields, "nonce"),
        }),
        other => panic!(
            "the corpus carries a {other} sample this test cannot build. Add it \
             here rather than letting the case go unchecked."
        ),
    }
}

fn type_of(value: u64) -> MessageType {
    match value {
        0x01 => MessageType::Hello,
        0x02 => MessageType::HelloAck,
        0x10 => MessageType::RequestVote,
        0x11 => MessageType::RequestVoteReply,
        0x12 => MessageType::AppendEntries,
        0x13 => MessageType::AppendEntriesReply,
        0x14 => MessageType::InstallSnapshot,
        0x15 => MessageType::InstallSnapshotReply,
        0x16 => MessageType::Promote,
        0x20 => MessageType::Propose,
        0x21 => MessageType::ProposeReply,
        0x22 => MessageType::Forward,
        0x23 => MessageType::ForwardReply,
        0x30 => MessageType::Ping,
        0x31 => MessageType::Pong,
        other => panic!("the corpus names an unknown message type {other:#04x}"),
    }
}

#[test]
fn every_python_sample_encodes_to_the_same_bytes() {
    let corpus = corpus();
    let cases = corpus["cases"].as_array().expect("cases");
    assert!(!cases.is_empty(), "the corpus is empty");

    for case in cases {
        let (name, variant) = names(case);
        let expected = case["encoded_hex"].as_str().expect("the encoded bytes");
        let built = build(name, &case["fields"]);

        assert_eq!(
            hex(&built.encode()),
            expected,
            "{name} ({variant}) encodes differently from the Python. This is \
             the wire a mixed cluster runs on, so the symptom is a member that \
             cannot be understood by half of it, not a failing round trip.",
        );
    }
}

#[test]
fn every_python_sample_decodes_back_to_itself() {
    let corpus = corpus();
    for case in corpus["cases"].as_array().expect("cases") {
        let (name, variant) = names(case);
        let message_type = type_of(case["type"].as_u64().expect("a type byte"));
        let recorded = unhex(case["encoded_hex"].as_str().expect("the encoded bytes"));

        let decoded = decode_message(message_type, &recorded)
            .unwrap_or_else(|e| panic!("{name} ({variant}) failed to decode: {e}"));

        assert_eq!(
            decoded,
            build(name, &case["fields"]),
            "{name} ({variant}) decoded to something other than the sample it \
             was built from -- a field that encodes but does not decode reads \
             as its default here.",
        );
    }
}

#[test]
fn the_type_byte_a_message_reports_is_the_one_that_decodes_it() {
    // The Python asserts this over `BY_TYPE`; here it is the pairing between
    // `Message::message_type` and the byte the exporter recorded. A message
    // wired to the wrong arm of `decode_message` would round-trip perfectly
    // and still be undeliverable.
    let corpus = corpus();
    for case in corpus["cases"].as_array().expect("cases") {
        let (name, variant) = names(case);
        let recorded = type_of(case["type"].as_u64().expect("a type byte"));
        assert_eq!(
            build(name, &case["fields"]).message_type(),
            recorded,
            "{name} ({variant}) reports a different type than the frame that \
             carries it",
        );
    }
}

#[test]
fn the_corpus_covers_every_message_type_but_pong() {
    // `test_messages.py` asserts exactly this, and it is worth asserting on
    // both sides: the check that matters is that no message reaches the wire
    // without a vector, and only the side holding the vectors can see that.
    let corpus = corpus();
    let mut covered: Vec<u64> = corpus["cases"]
        .as_array()
        .expect("cases")
        .iter()
        .map(|case| case["type"].as_u64().expect("a type byte"))
        .collect();
    covered.sort_unstable();
    covered.dedup();

    let mut expected: Vec<u64> = vec![
        0x01, 0x02, 0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x20, 0x21, 0x22, 0x23,
        0x30, // Pong (0x31) is Ping's twin and is deliberately not sampled.
    ];
    expected.sort_unstable();

    assert_eq!(
        covered, expected,
        "the message corpus no longer covers every message type but Pong",
    );
}

#[test]
fn absent_is_not_zero_for_the_two_fields_where_it_matters() {
    // Member indices start at 0, so a `leader`/`owner` field that encoded
    // `None` as 0 would name the first member whenever none was known -- and
    // the corpus cannot catch it, because its samples set both.
    let no_leader = ProposeReply {
        accepted: false,
        reason: String::new(),
        term: 0,
        first_index: 0,
        request_id: 0,
        leader: None,
    };
    let decoded = ProposeReply::decode(&no_leader.encode()).expect("decodes");
    assert_eq!(
        decoded.leader, None,
        "a reply with no known leader decoded as member 0",
    );

    let leader_zero = ProposeReply {
        leader: Some(0),
        ..no_leader
    };
    assert_eq!(
        ProposeReply::decode(&leader_zero.encode())
            .expect("decodes")
            .leader,
        Some(0),
        "member 0 as leader decoded as 'no leader'",
    );

    let no_owner = ForwardReply {
        ok: false,
        created: false,
        error: String::new(),
        detail: String::new(),
        applied_index: 0,
        not_owner: true,
        request_id: 0,
        owner: None,
    };
    assert_eq!(
        ForwardReply::decode(&no_owner.encode())
            .expect("decodes")
            .owner,
        None,
        "a reply with no known owner decoded as member 0",
    );

    let owner_zero = ForwardReply {
        owner: Some(0),
        ..no_owner
    };
    assert_eq!(
        ForwardReply::decode(&owner_zero.encode())
            .expect("decodes")
            .owner,
        Some(0),
        "member 0 as owner decoded as 'no owner'",
    );
}

#[test]
fn an_unknown_field_is_skipped_rather_than_rejected() {
    // Forward compatibility, and the reason field numbers are permanent: a
    // newer peer may send a field this build has never heard of, and the right
    // answer is to ignore it. Rejecting would partition the cluster on upgrade.
    let mut payload = Promote {
        term: 6,
        leader: 1,
        through_index: 500,
    }
    .encode();
    payload.extend_from_slice(
        &nmos_registry_raft::wire::Writer::new()
            .string(99, "a field from a later build")
            .take(),
    );

    let decoded = Promote::decode(&payload).expect("an unknown field is skipped, not rejected");
    assert_eq!(decoded.term, 6);
    assert_eq!(decoded.leader, 1);
    assert_eq!(decoded.through_index, 500);
}

#[test]
fn an_unknown_stream_class_is_refused() {
    // The counterpart: an unknown *value* in a field this build does
    // understand is not forward compatibility, it is a peer routing on a class
    // this member cannot honour. Guessing which link to answer on is worse
    // than dropping.
    let payload = nmos_registry_raft::wire::Writer::new()
        .uint(1, 1)
        .uint(7, 7)
        .take();
    let error = Hello::decode(&payload).expect_err("an unknown stream class is refused");
    assert!(
        error.0.contains("unknown stream class 7"),
        "unhelpful refusal: {}",
        error.0,
    );
}

#[test]
fn the_corpus_still_carries_the_saturated_variants() {
    // Everything above passes on the unsaturated samples alone, and those
    // leave thirteen fields at their defaults. If the exporter stops emitting
    // the saturated variant the coverage disappears with no test failing --
    // which is the same silent-shrinking failure the Python-side guard's key
    // collision check exists to prevent.
    let corpus = corpus();
    let cases = corpus["cases"].as_array().expect("cases");

    let saturated = cases
        .iter()
        .filter(|case| case["variant"] == "saturated")
        .count();
    let samples = cases
        .iter()
        .filter(|case| case["variant"] == "sample")
        .count();

    assert_eq!(
        saturated, samples,
        "{samples} samples but {saturated} saturated variants -- every sample \
         needs one, because the sample alone cannot detect a field that \
         encodes but does not decode.",
    );
    assert!(
        cases.iter().any(|case| case["variant"] == "absent"),
        "no 'absent' variant: nothing then checks that a message with no known \
         leader or owner does not encode as member 0",
    );
}
