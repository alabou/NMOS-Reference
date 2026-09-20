// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Both implementations spell the same key and read the same envelope.
//!
//! `key_vectors.json` is recorded from the Python by
//! `nmos/registry/tests/_keys_corpus.py`. Regenerate with
//! `python -m nmos.registry.tests._keys_corpus`.
//!
//! # Why this corpus earns its keep more than most
//!
//! A raft frame that decodes differently fails to parse and the link drops,
//! loudly. An etcd **key** spelled differently is written successfully and
//! then never seen by the watcher: the resource is in the cluster, invisible
//! on the member that did not write it, and nothing fails anywhere. There is
//! no runtime symptom to notice -- only a Controller that cannot see a Sender,
//! days later.
//!
//! The refusals are checked too, because they are the whole of what an
//! operator sees when a value turns out to be unreadable, and two members
//! describing the same corruption in different words is a support problem
//! discovered while somebody is diffing two logs.
//!
//! # The two places parity is over something narrower than the value
//!
//! Both are recorded in the corpus rather than assumed here, so shrinking them
//! is a visible change to a committed file:
//!
//! * `parity_prefix` -- one refusal interpolates `UnicodeDecodeError`, which
//!   is CPython's words for CPython's exception. Everything up to that point
//!   must match exactly. Every other refusal, including the ones that
//!   interpolate the span scanner, is compared whole.
//! * `version_fits_i64` / `health_fits_i64` -- Python's `int` is unbounded and
//!   this port's is not. Where a value leaves 64 bits the **verdict** must
//!   still match; the number itself cannot be reproduced and is not compared.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::panic
)]

use std::fs;
use std::path::PathBuf;

use nmos_registry_core::{Body, ResourceType, TaiCursor};
use nmos_registry_etcd::keys::{ENVELOPE_VERSION, Envelope, Namespace};
use serde_json::Value;

fn corpus() -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/key_vectors.json");
    let text = fs::read_to_string(&path).unwrap_or_else(|exc| {
        panic!(
            "{} is missing or unreadable ({exc}). Regenerate it with \
             `python -m nmos.registry.tests._keys_corpus`.",
            path.display(),
        )
    });
    serde_json::from_str(&text).expect("the corpus is JSON")
}

fn cases(kind: &str) -> Vec<Value> {
    corpus()["cases"]
        .as_array()
        .expect("the corpus has a cases array")
        .iter()
        .filter(|case| case["kind"] == kind)
        .cloned()
        .collect()
}

fn text(case: &Value, field: &str) -> String {
    case[field]
        .as_str()
        .unwrap_or_else(|| panic!("case {case} has no string {field}"))
        .to_owned()
}

fn bytes(case: &Value, field: &str) -> Vec<u8> {
    let hex = text(case, field);
    assert!(hex.len().is_multiple_of(2), "odd hex in {field}");
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

/// The name a failure reports, matching the Python's own key for the record.
fn name(case: &Value) -> String {
    format!("{}/{}", text(case, "kind"), text(case, "name"))
}

/// The number Python recorded, with `True`/`False` read as the ints they are.
fn integer(case: &Value, field: &str) -> i64 {
    match &case[field] {
        Value::Bool(flag) => i64::from(*flag),
        Value::Number(number) => number.as_i64().unwrap_or_else(|| {
            panic!("{}: {field} is outside i64 and was not flagged", name(case))
        }),
        other => panic!("{}: {field} is {other}", name(case)),
    }
}

#[test]
fn the_corpus_was_recorded_against_this_envelope_version() {
    // A version bump on one side and not the other is the single change that
    // would make every other assertion here agree about the wrong thing.
    let corpus = corpus();
    assert_eq!(
        corpus["envelope_version"].as_i64(),
        Some(ENVELOPE_VERSION),
        "the corpus was recorded at a different envelope version; regenerate \
         it with `python -m nmos.registry.tests._keys_corpus`",
    );
}

#[test]
fn a_namespace_is_accepted_or_refused_identically() {
    let cases = cases("namespace");
    assert!(cases.len() >= 10, "the namespace tier shrank");
    for case in cases {
        let result = Namespace::new(text(&case, "prefix"));
        match (case["accepted"].as_bool(), result) {
            (Some(true), Ok(_)) => {}
            (Some(false), Err(fault)) => assert_eq!(
                fault.message(),
                text(&case, "message"),
                "{}: the refusal is worded differently",
                name(&case),
            ),
            (Some(true), Err(fault)) => {
                panic!("{}: refused here, accepted by Python: {fault}", name(&case))
            }
            (Some(false), Ok(_)) => panic!(
                "{}: accepted here, refused by Python with {:?}",
                name(&case),
                text(&case, "message"),
            ),
            (None, _) => panic!("{}: no `accepted` field", name(&case)),
        }
    }
}

#[test]
fn every_key_constructor_spells_the_same_bytes() {
    let cases = cases("key");
    assert!(cases.len() >= 40, "the key tier shrank");
    for case in cases {
        let namespace = Namespace::new(text(&case, "prefix")).expect("a valid prefix");
        let args: Vec<String> = case["args"]
            .as_array()
            .expect("args is an array")
            .iter()
            .map(|arg| arg.as_str().expect("an argument is a string").to_owned())
            .collect();

        let produced: Result<Vec<u8>, String> = match text(&case, "call").as_str() {
            "root" => Ok(namespace.root()),
            "meta_config" => Ok(namespace.meta_config()),
            "ids_root" => Ok(namespace.ids_root()),
            "nodes_root" => Ok(namespace.nodes_root()),
            "id_claim" => Ok(namespace.id_claim(&args[0])),
            "node" => Ok(namespace.node(&args[0])),
            "node_subtree" => Ok(namespace.node_subtree(&args[0])),
            "device" => Ok(namespace.device(&args[0], &args[1])),
            "device_subtree" => Ok(namespace.device_subtree(&args[0], &args[1])),
            "child" => {
                let kind = ResourceType::from_singular(&args[0]).expect("a resource type");
                namespace
                    .child(kind, &args[1], &args[2], &args[3])
                    .map_err(|fault| fault.message().to_owned())
            }
            other => panic!("{}: unknown constructor {other}", name(&case)),
        };

        match (case.get("key_hex"), produced) {
            (Some(_), Ok(key)) => assert_eq!(
                String::from_utf8_lossy(&key),
                String::from_utf8_lossy(&bytes(&case, "key_hex")),
                "{}: the key is spelled differently",
                name(&case),
            ),
            (None, Err(message)) => assert_eq!(
                message,
                text(&case, "message"),
                "{}: the refusal is worded differently",
                name(&case),
            ),
            (Some(_), Err(message)) => panic!(
                "{}: refused here with {message:?}, produced a key in Python",
                name(&case),
            ),
            (None, Ok(key)) => panic!(
                "{}: produced {:?} here, refused by Python",
                name(&case),
                String::from_utf8_lossy(&key),
            ),
        }
    }
}

#[test]
fn every_key_parses_to_the_same_thing() {
    let cases = cases("parse");
    assert!(cases.len() >= 30, "the parse tier shrank");
    for case in cases {
        let namespace = Namespace::new(text(&case, "prefix")).expect("a valid prefix");
        let key = bytes(&case, "key_hex");
        let outcome = text(&case, "outcome");
        match (outcome.as_str(), namespace.parse(&key)) {
            ("ignored", Ok(None)) => {}
            ("parsed", Ok(Some(parsed))) => {
                assert_eq!(
                    parsed.resource_type.singular(),
                    text(&case, "resource_type"),
                    "{}: type",
                    name(&case),
                );
                assert_eq!(
                    parsed.resource_id,
                    text(&case, "resource_id"),
                    "{}: id",
                    name(&case)
                );
                assert_eq!(
                    parsed.node_id,
                    text(&case, "node_id"),
                    "{}: node",
                    name(&case)
                );
                assert_eq!(
                    parsed.device_id.as_deref(),
                    case["device_id"].as_str(),
                    "{}: device",
                    name(&case),
                );
                assert_eq!(
                    parsed.is_node(),
                    case["is_node"].as_bool().expect("is_node"),
                    "{}: is_node",
                    name(&case),
                );
                assert_eq!(
                    i64::from(parsed.depth()),
                    case["depth"].as_i64().expect("depth"),
                    "{}: depth -- parents would be applied out of order",
                    name(&case),
                );
            }
            ("error", Err(fault)) => assert_eq!(
                fault.message(),
                text(&case, "message"),
                "{}: the refusal is worded differently",
                name(&case),
            ),
            (wanted, got) => panic!(
                "{}: Python said {wanted}, this said {}",
                name(&case),
                match got {
                    Ok(None) => "ignored".to_owned(),
                    Ok(Some(parsed)) => format!("parsed as {}", parsed.resource_type),
                    Err(fault) => format!("error {}", fault.message()),
                },
            ),
        }
    }
}

#[test]
fn every_envelope_encodes_to_the_same_bytes() {
    let cases = cases("encode");
    assert!(cases.len() >= 30, "the encode tier shrank");
    for case in cases {
        let envelope = Envelope {
            version: integer(&case, "version"),
            resource_type: ResourceType::from_singular(&text(&case, "resource_type"))
                .expect("a resource type"),
            body: Body::new(text(&case, "body_text")),
            created: TaiCursor::parse(&text(&case, "created")).expect("a cursor"),
            updated: TaiCursor::parse(&text(&case, "updated")).expect("a cursor"),
            health: integer(&case, "health"),
        };
        let expected = bytes(&case, "encoded_hex");
        assert_eq!(
            String::from_utf8_lossy(&envelope.encode()),
            String::from_utf8_lossy(&expected),
            "{}: the stored value differs",
            name(&case),
        );

        // Python recorded that the body's bytes survive the round trip. If it
        // did not, this corpus is describing a broken splice and the
        // assertion below would be enforcing it.
        assert!(
            case["round_trips"].as_bool().expect("round_trips"),
            "{}: Python's own round trip lost the body",
            name(&case),
        );
        let decoded = Envelope::decode(&expected).expect("what we encoded decodes");
        assert_eq!(
            decoded.body.text(),
            text(&case, "body_text"),
            "{}: the body was not returned byte for byte",
            name(&case),
        );
        assert_eq!(decoded, envelope, "{}: a field was lost", name(&case));
    }
}

#[test]
fn every_stored_value_decodes_to_the_same_thing() {
    let cases = cases("decode");
    assert!(cases.len() >= 60, "the decode tier shrank");
    for case in cases {
        let value = bytes(&case, "value_hex");
        let outcome = text(&case, "outcome");
        match (outcome.as_str(), Envelope::decode(&value)) {
            ("decoded", Ok(envelope)) => {
                assert_eq!(
                    envelope.resource_type.singular(),
                    text(&case, "resource_type"),
                    "{}: type",
                    name(&case),
                );
                assert_eq!(
                    envelope.body.text(),
                    text(&case, "body_text"),
                    "{}: the body is not byte-identical",
                    name(&case),
                );
                assert_eq!(
                    envelope.created.to_string(),
                    text(&case, "created"),
                    "{}: created",
                    name(&case),
                );
                assert_eq!(
                    envelope.updated.to_string(),
                    text(&case, "updated"),
                    "{}: updated",
                    name(&case),
                );
                // Compared only where Python's unbounded int had a 64-bit
                // value to record. Where it did not, agreeing on the verdict
                // is the whole of what can be asked.
                if case["version_fits_i64"]
                    .as_bool()
                    .expect("version_fits_i64")
                {
                    assert_eq!(
                        envelope.version,
                        integer(&case, "version"),
                        "{}: version",
                        name(&case),
                    );
                }
                if case["health_fits_i64"].as_bool().expect("health_fits_i64") {
                    assert_eq!(
                        envelope.health,
                        integer(&case, "health"),
                        "{}: health",
                        name(&case),
                    );
                }
            }
            ("error", Err(fault)) => {
                let prefix = text(&case, "parity_prefix");
                assert!(
                    fault.message().starts_with(&prefix),
                    "{}: refused with {:?}, which does not begin with Python's {:?}",
                    name(&case),
                    fault.message(),
                    prefix,
                );
            }
            ("decoded", Err(fault)) => panic!(
                "{}: refused here with {:?}, accepted by Python -- the two \
                 members would disagree about whether the resource exists",
                name(&case),
                fault.message(),
            ),
            ("error", Ok(_)) => panic!(
                "{}: accepted here, refused by Python with {:?}",
                name(&case),
                text(&case, "message"),
            ),
            (other, _) => panic!("{}: unknown outcome {other}", name(&case)),
        }
    }
}

#[test]
fn the_corpus_still_exercises_the_quirks_it_exists_for() {
    // The tiers above would all pass against a corpus that had quietly stopped
    // reaching the interesting cases. These are the three that a reasonable
    // independent implementation would get wrong, so their presence is
    // asserted rather than trusted.
    let decoded: Vec<Value> = cases("decode")
        .into_iter()
        .filter(|case| case["outcome"] == "decoded")
        .collect();

    // A boolean must be refused where an integer is required, and the integers
    // it would have decoded to must still be accepted. Both halves, because a
    // fix for the first that breaks the second is the likely mistake: `False`
    // is `0`, and `0` is a legitimate health.
    let all = cases("decode");
    let named = |name: &str| -> Value {
        all.iter()
            .find(|case| case["name"] == name)
            .unwrap_or_else(|| panic!("the corpus has no decode case {name}"))
            .clone()
    };
    for name in ["v_true", "v_false", "health_true", "health_false"] {
        assert_eq!(
            named(name)["outcome"],
            "error",
            "{name} must be refused; `isinstance(True, int)` is true in \
             Python, so this is the one check whose obvious spelling is wrong",
        );
    }
    for name in ["v_zero", "health_zero"] {
        assert_eq!(
            named(name)["outcome"],
            "decoded",
            "{name} must still be accepted",
        );
    }
    assert!(
        decoded
            .iter()
            .any(|case| case["version_fits_i64"] == false || case["health_fits_i64"] == false),
        "no case reaches an integer outside 64 bits, so an implementation that \
         refused one instead of saturating would pass",
    );
    assert!(
        decoded
            .iter()
            .any(|case| text(case, "body_text").contains("NaN")
                || text(case, "body_text").contains("Infinity")),
        "no case carries a body `serde_json` cannot parse, so a `data` check \
         that re-parsed instead of reading the span would pass",
    );
}
