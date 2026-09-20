// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Both generated trees agree about what the vendored protos mean.
//!
//! `message_vectors.json` is recorded from the Python by
//! `nmos/etcd/tests/_proto_corpus.py`. Regenerate with
//! `python -m nmos.etcd.tests._proto_corpus`.
//!
//! # What this adds over the fingerprint
//!
//! The fingerprint proves the two trees were generated from the *same* protos.
//! It does not prove the two generators agreed about what those protos say.
//! The gap is small and it is not empty: a field on a different number, a
//! `bytes` field one side treats as a string, an enum whose values shifted.
//! Every one of those is a member writing records its peers read differently,
//! with nothing failing at either end.
//!
//! # Why a round trip is a strong check and not a weak one
//!
//! prost **drops** unknown fields rather than preserving them. So if a field
//! here carried a different number from the one Python encoded, decoding would
//! see an unknown field, discard it, and re-encode to something shorter. Byte
//! equality after a decode-then-encode therefore catches exactly the class of
//! disagreement this file exists for, and the spot-checked values below are
//! there so a failure says *which* field rather than only "the bytes differ".

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::panic
)]

use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;

use nmos_etcd::PROTO_FINGERPRINT;
use nmos_etcd::generated::{etcdserverpb as pb, mvccpb};
use prost::Message;
use serde_json::Value;

fn corpus() -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/message_vectors.json");
    let text = fs::read_to_string(&path).unwrap_or_else(|exc| {
        panic!(
            "{} is missing or unreadable ({exc}). Regenerate it with \
             `python -m nmos.etcd.tests._proto_corpus`.",
            path.display(),
        )
    });
    serde_json::from_str(&text).expect("the corpus is JSON")
}

fn decode_hex(hex: &str) -> Vec<u8> {
    assert!(hex.len().is_multiple_of(2), "odd hex");
    hex.as_bytes()
        .as_chunks::<2>()
        .0
        .iter()
        .map(|pair| {
            let digits = std::str::from_utf8(pair).expect("hex is ASCII");
            u8::from_str_radix(digits, 16).expect("hex")
        })
        .collect()
}

/// Every case, as `name -> (message type, bytes)`.
fn vectors() -> BTreeMap<String, (String, Vec<u8>)> {
    corpus()["cases"]
        .as_array()
        .expect("cases is an array")
        .iter()
        .map(|case| {
            let name = case["name"].as_str().expect("a name").to_owned();
            assert!(
                case["round_trips"].as_bool().expect("round_trips"),
                "{name}: Python's own round trip lost bytes, so this vector \
                 describes something broken rather than something to match",
            );
            (
                name,
                (
                    case["message"].as_str().expect("a message").to_owned(),
                    decode_hex(case["encoded_hex"].as_str().expect("hex")),
                ),
            )
        })
        .collect()
}

/// Decode one vector, re-encode it, and require the bytes back unchanged.
macro_rules! round_trip {
    ($vectors:expr, $checked:expr, $name:expr, $type:ty) => {{
        let (_kind, bytes) = $vectors
            .get($name)
            .unwrap_or_else(|| panic!("the corpus has no case named {}", $name));
        let decoded = <$type>::decode(bytes.as_slice())
            .unwrap_or_else(|exc| panic!("{} did not decode: {exc}", $name));
        let re_encoded = decoded.encode_to_vec();
        assert_eq!(
            re_encoded, *bytes,
            "{}: re-encoding produced different bytes, so a field is on a \
             different number or has a different wire type here than in the \
             Python",
            $name,
        );
        $checked.push($name);
        decoded
    }};
}

#[test]
fn the_corpus_was_recorded_from_these_protos() {
    // Without this, a proto change plus a regeneration of only the Rust would
    // leave every assertion below comparing against the old wire format and
    // passing.
    assert_eq!(
        corpus()["proto_fingerprint"].as_str(),
        Some(PROTO_FINGERPRINT),
        "the message corpus was recorded from different protos than this tree \
         was generated from; regenerate both with \
         `python -m nmos.etcd.generate && python -m nmos.etcd.tests._proto_corpus`",
    );
}

#[test]
fn every_message_the_client_uses_round_trips_byte_for_byte() {
    let vectors = vectors();
    let mut checked: Vec<&str> = Vec::new();

    // --- the record itself, and the watch event that carries it ------------
    let kv = round_trip!(vectors, checked, "KeyValue", mvccpb::KeyValue);
    assert_eq!(kv.key.as_ref(), b"/nmos/nodes/n1/self");
    assert_eq!(kv.mod_revision, 22);
    assert_eq!(kv.lease, 0x0BAD_C0DE_0BAD_C0DE);
    // The body is `bytes` and must survive as bytes: a `String` mapping would
    // still round-trip here and would corrupt the first non-UTF-8 value.
    assert_eq!(
        kv.value.as_ref(),
        r#"{"id": "n1", "label": "café"}"#.as_bytes(),
    );

    let event = round_trip!(vectors, checked, "Event", mvccpb::Event);
    assert_eq!(event.r#type, mvccpb::event::EventType::Delete as i32);
    assert!(event.prev_kv.is_some(), "prev_kv is what a delete carries");

    let header = round_trip!(vectors, checked, "ResponseHeader", pb::ResponseHeader);
    assert_eq!(header.revision, 4_242_424_242);
    assert_eq!(header.cluster_id, 0x1122_3344_5566_7788);

    // --- reads --------------------------------------------------------------
    let range = round_trip!(vectors, checked, "RangeRequest", pb::RangeRequest);
    assert_eq!(range.range_end.as_ref(), b"/nmos/nodes0");
    assert!(range.serializable, "the local-read flag");
    assert_eq!(range.limit, 500);

    let response = round_trip!(vectors, checked, "RangeResponse", pb::RangeResponse);
    assert_eq!(response.kvs.len(), 2);
    assert_eq!(response.count, 2);

    // --- writes -------------------------------------------------------------
    let put = round_trip!(vectors, checked, "PutRequest", pb::PutRequest);
    assert_eq!(put.lease, 123_456_789);
    assert!(put.prev_kv);

    round_trip!(
        vectors,
        checked,
        "DeleteRangeRequest",
        pb::DeleteRangeRequest
    );
    let deleted = round_trip!(
        vectors,
        checked,
        "DeleteRangeResponse",
        pb::DeleteRangeResponse
    );
    assert_eq!(deleted.deleted, 7);

    // --- the compare-and-set the mutation path is built on ------------------
    //
    // Every `CompareTarget` separately: the target selects which arm of a
    // oneof carries the value, so one wrong arm is a comparison against a
    // different field and a transaction that succeeds when it should fail.
    for (name, expected_target) in [
        ("Compare", pb::compare::CompareTarget::Mod),
        ("Compare/version", pb::compare::CompareTarget::Version),
        ("Compare/create", pb::compare::CompareTarget::Create),
        ("Compare/value", pb::compare::CompareTarget::Value),
        ("Compare/lease", pb::compare::CompareTarget::Lease),
    ] {
        let compare = round_trip!(vectors, checked, name, pb::Compare);
        assert_eq!(
            compare.target, expected_target as i32,
            "{name}: the compare target decoded as something else",
        );
        assert!(
            compare.target_union.is_some(),
            "{name}: the oneof carrying the compared value is empty, so the \
             comparison would be against a default",
        );
    }

    for name in ["RequestOp/put", "RequestOp/range", "RequestOp/delete"] {
        let op = round_trip!(vectors, checked, name, pb::RequestOp);
        assert!(op.request.is_some(), "{name}: the oneof is empty");
    }
    for name in ["ResponseOp/range", "ResponseOp/put"] {
        let op = round_trip!(vectors, checked, name, pb::ResponseOp);
        assert!(op.response.is_some(), "{name}: the oneof is empty");
    }

    let txn = round_trip!(vectors, checked, "TxnRequest", pb::TxnRequest);
    assert_eq!(txn.compare.len(), 1);
    assert_eq!(txn.success.len(), 1);
    assert_eq!(txn.failure.len(), 1);
    let txn_response = round_trip!(vectors, checked, "TxnResponse", pb::TxnResponse);
    assert!(txn_response.succeeded);

    // --- the watch ----------------------------------------------------------
    let create = round_trip!(
        vectors,
        checked,
        "WatchCreateRequest",
        pb::WatchCreateRequest
    );
    assert_eq!(create.start_revision, 4242);
    assert!(create.progress_notify, "the progress fence depends on this");
    assert_eq!(
        create.filters,
        vec![pb::watch_create_request::FilterType::Noput as i32],
    );

    round_trip!(vectors, checked, "WatchRequest/create", pb::WatchRequest);
    round_trip!(vectors, checked, "WatchRequest/progress", pb::WatchRequest);

    let watch = round_trip!(vectors, checked, "WatchResponse", pb::WatchResponse);
    assert!(watch.canceled);
    assert_eq!(watch.compact_revision, 4000);
    assert_eq!(watch.events.len(), 1);
    assert!(
        watch.cancel_reason.contains("compacted"),
        "the reason a watch has to be restarted from a new revision",
    );

    // --- leases -------------------------------------------------------------
    //
    // `TTL` and `ID` are capitalised in the proto, so prost renames them.
    // Naming them explicitly here is what proves the rename landed on the
    // field Python set rather than on a neighbour.
    let grant = round_trip!(vectors, checked, "LeaseGrantRequest", pb::LeaseGrantRequest);
    assert_eq!(grant.ttl, 30);
    assert_eq!(grant.id, 777);
    let granted = round_trip!(
        vectors,
        checked,
        "LeaseGrantResponse",
        pb::LeaseGrantResponse
    );
    assert_eq!(granted.id, 777);
    assert_eq!(granted.ttl, 30);

    let keep_alive = round_trip!(
        vectors,
        checked,
        "LeaseKeepAliveRequest",
        pb::LeaseKeepAliveRequest
    );
    assert_eq!(keep_alive.id, 777);
    let kept = round_trip!(
        vectors,
        checked,
        "LeaseKeepAliveResponse",
        pb::LeaseKeepAliveResponse
    );
    assert_eq!(kept.ttl, 29);

    round_trip!(
        vectors,
        checked,
        "LeaseRevokeRequest",
        pb::LeaseRevokeRequest
    );
    round_trip!(
        vectors,
        checked,
        "LeaseRevokeResponse",
        pb::LeaseRevokeResponse
    );
    round_trip!(
        vectors,
        checked,
        "LeaseTimeToLiveRequest",
        pb::LeaseTimeToLiveRequest
    );
    let ttl = round_trip!(
        vectors,
        checked,
        "LeaseTimeToLiveResponse",
        pb::LeaseTimeToLiveResponse
    );
    assert_eq!(ttl.granted_ttl, 30);
    assert_eq!(ttl.keys.len(), 1);

    // --- maintenance --------------------------------------------------------
    let compaction = round_trip!(vectors, checked, "CompactionRequest", pb::CompactionRequest);
    assert_eq!(compaction.revision, 4000);
    assert!(compaction.physical);
    round_trip!(
        vectors,
        checked,
        "CompactionResponse",
        pb::CompactionResponse
    );

    let status = round_trip!(vectors, checked, "StatusResponse", pb::StatusResponse);
    assert_eq!(status.version, "3.6.14");
    assert_eq!(status.db_size, 1_048_576);
    assert_eq!(status.errors, vec!["a recorded error".to_owned()]);

    let members = round_trip!(
        vectors,
        checked,
        "MemberListResponse",
        pb::MemberListResponse
    );
    assert_eq!(members.members.len(), 1);
    assert_eq!(members.members[0].name, "member-0");
    // `clientURLs` in the proto, and prost's snake_case of it is `client_ur_ls`
    // -- an awkward name, and the reason to assert the value here rather than
    // trust that the field one reaches for is the field the wire carries.
    assert_eq!(
        members.members[0].client_ur_ls,
        vec!["https://127.0.0.1:22379"],
    );
    assert_eq!(
        members.members[0].peer_ur_ls,
        vec!["https://127.0.0.1:22380"],
    );

    // Nothing in the corpus may go unasserted. A vector that is recorded and
    // never decoded is coverage the file reports and does not have.
    let unchecked: Vec<&String> = vectors
        .keys()
        .filter(|name| !checked.contains(&name.as_str()))
        .collect();
    assert!(
        unchecked.is_empty(),
        "the corpus carries vector(s) this test never decodes: {unchecked:?}",
    );
}
