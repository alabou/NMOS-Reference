// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Differential test: what a generated type writes must be byte-identical to
//! what the Python one writes.
//!
//! `decode_parity.rs` is the accept/reject half of the emitter's claim. This is
//! the other half, and the one with a client on the far end: `build_grain` in
//! `nmos/registry/subscriptions.py` is the registry's only call to
//! `JsonEngine().encode()`, so these bytes are what a subscriber parses.
//!
//! # Why bytes and not structure
//!
//! Every interesting failure here survives a round trip, so comparing parsed
//! values would pass while the wire diverged:
//!
//! * **member order** -- Python writes in descriptor order. Rust's derived
//!   `Serialize` writes in declaration order, which the emitter renders from
//!   the same descriptors; but `#[serde(flatten)]` on the embedded types goes
//!   through a map serializer, and whether that keeps its position is a
//!   property of serde rather than of the model. Nothing but a byte comparison
//!   checks it.
//! * **applied defaults** -- decoding injects members the body never carried
//!   (`urn:x-nmos:cap:meta:enabled`, `transfer_characteristic`, a Rational's
//!   `denominator`) and marks them defined, so they are written out.
//! * **float spelling** -- `1000000.0` must not become `1000000`, `1e+16` must
//!   not become `1e16`. Both read back as the same double.
//! * **escaping** -- which characters each language chooses to escape is not
//!   identical by default.
//!
//! `encode_cases.json` records what Python actually wrote, produced by
//! `nmos/codegen/tests/_encode_corpus.py`.
//!
//! # The one permitted difference, and why it cannot reach the registry
//!
//! A member whose JSON value is a *dynamic map* -- a constraint set keyed by
//! capability URN, a tags object -- comes out in a different order in the two
//! languages. Python's `dict` keeps insertion order, so a constraint set is
//! written in the order the body listed it. Rust decodes from
//! `serde_json::Value`, whose `Map` is a `BTreeMap` because this workspace does
//! not enable `preserve_order` -- that feature costs 25% on every parse, which
//! is not affordable on the registry's hot path -- so the input order is gone
//! before any generated code sees it, and the output is sorted.
//!
//! RFC 8259 §4: an object is "an unordered collection", and only array element
//! order is significant. So the two encodings say the same thing, and no
//! conformant consumer can distinguish them.
//!
//! **The registry's own wire is not affected at all.** `build_grain` sets
//! `pre`/`post` to `RawJson(event.pre.text)`, so a resource body reaches a
//! subscriber as the bytes that were registered, spliced verbatim; the typed
//! encode covers only the grain envelope, whose members are fixed-shape and
//! descriptor-ordered. This difference is therefore a property of the type
//! *library* -- which matters for the eventual Node port, where resources are
//! built and encoded rather than relayed -- and not of this registry.
//!
//! It is admitted under a condition tight enough that nothing else fits
//! through: the two texts must parse to equal values **and** have identical
//! sorted bytes. Reordering an object's members permutes the byte sequence
//! exactly, so byte-multiset equality holds for a permutation and fails for a
//! dropped member, an added one, a different escape or a different float
//! spelling. The same discipline `float_parity.rs` applies to a rounding tie.

#![allow(clippy::unwrap_used, clippy::expect_used, clippy::panic)]

use nmos_json::engine::encode_compact;
use nmos_types::generated::{
    ndevice::NDevice, nflow::NFlow, nnode::NNode, nreceiver::NReceiver, nsender::NSender,
    nsource::NSource,
};
use serde_json::Value;

#[derive(Debug, serde::Deserialize)]
struct Case {
    label: String,
    resource_type: String,
    body: Value,
    encoded: String,
}

/// Decode as the named resource type and re-encode the way `build_grain` does.
fn round_trip(resource_type: &str, body: &Value) -> Result<String, String> {
    fn enc<T: serde::Serialize>(v: &T) -> Result<String, String> {
        encode_compact(v).map_err(|e| e.to_string())
    }
    match resource_type {
        "node" => NNode::decode(body)
            .map_err(|e| e.message().to_owned())
            .and_then(|v| enc(&v)),
        "device" => NDevice::decode(body)
            .map_err(|e| e.message().to_owned())
            .and_then(|v| enc(&v)),
        "source" => NSource::decode(body)
            .map_err(|e| e.message().to_owned())
            .and_then(|v| enc(&v)),
        "flow" => NFlow::decode(body)
            .map_err(|e| e.message().to_owned())
            .and_then(|v| enc(&v)),
        "sender" => NSender::decode(body)
            .map_err(|e| e.message().to_owned())
            .and_then(|v| enc(&v)),
        "receiver" => NReceiver::decode(body)
            .map_err(|e| e.message().to_owned())
            .and_then(|v| enc(&v)),
        other => panic!("unknown resource type in corpus: {other}"),
    }
}

fn load() -> Vec<Case> {
    serde_json::from_str(include_str!("encode_cases.json"))
        .expect("the encode corpus is valid JSON")
}

/// Where two strings first differ, with a window of context around it.
///
/// A whole encoded Node is several hundred bytes, and `assert_eq!` on two of
/// them prints two walls of near-identical text. This points at the byte.
fn first_difference(want: &str, got: &str) -> String {
    const WINDOW: usize = 40;

    let at = want
        .bytes()
        .zip(got.bytes())
        .position(|(a, b)| a != b)
        .unwrap_or_else(|| want.len().min(got.len()));

    // Back off to a char boundary before slicing. A difference in escaping
    // lands in the middle of a multi-byte character, so this is the normal
    // case here rather than a defensive one.
    let floor = |s: &str, i: usize| {
        let mut i = i.min(s.len());
        while i > 0 && !s.is_char_boundary(i) {
            i = i.saturating_sub(1);
        }
        i
    };
    let ceil = |s: &str, i: usize| {
        let mut i = i.min(s.len());
        while i < s.len() && !s.is_char_boundary(i) {
            i = i.saturating_add(1);
        }
        i
    };

    let start = at.saturating_sub(WINDOW);
    let end = at.saturating_add(WINDOW);
    format!(
        "first differs at byte {at}\n      python: ...{}...\n      rust:   ...{}...",
        &want[floor(want, start)..ceil(want, end)],
        &got[floor(got, start)..ceil(got, end)],
    )
}

/// Is the difference between these two nothing but object member order?
///
/// Both conditions are needed and neither alone is enough:
///
/// * **equal parsed values** rules out a dropped, added or altered member --
///   but on its own it would also wave through `1e+16` vs `1e16`, since both
///   parse to the same double;
/// * **identical sorted bytes** rules that out in turn. Reordering members
///   permutes the byte sequence exactly: the same key text, the same value
///   text, the same count of `,` and `:`. Any change of spelling changes the
///   multiset.
fn differs_only_in_member_order(want: &str, got: &str) -> bool {
    if want.len() != got.len() {
        return false;
    }
    let (Ok(a), Ok(b)) = (
        serde_json::from_str::<Value>(want),
        serde_json::from_str::<Value>(got),
    ) else {
        return false;
    };
    if a != b {
        return false;
    }
    let mut want_bytes = want.as_bytes().to_vec();
    let mut got_bytes = got.as_bytes().to_vec();
    want_bytes.sort_unstable();
    got_bytes.sort_unstable();
    want_bytes == got_bytes
}

#[test]
fn generated_types_encode_exactly_as_python_does() {
    let cases = load();
    assert!(cases.len() >= 40, "corpus looks truncated: {}", cases.len());

    let mut disagreements = Vec::new();
    let mut reordered = Vec::new();

    for case in &cases {
        match round_trip(&case.resource_type, &case.body) {
            Ok(got) if got == case.encoded => {}
            Ok(got) if differs_only_in_member_order(&case.encoded, &got) => {
                reordered.push(case.label.clone());
            }
            Ok(got) => disagreements.push(format!(
                "{}: {}",
                case.label,
                first_difference(&case.encoded, &got),
            )),
            Err(why) => disagreements.push(format!(
                "{}: rust refused to decode a body python encoded: {why}",
                case.label,
            )),
        }
    }

    assert!(
        disagreements.is_empty(),
        "{} of {} cases differ from Python:\n  {}",
        disagreements.len(),
        cases.len(),
        disagreements.join("\n  "),
    );

    // Not an allowlist that absorbs whatever arrives. These are the cases with
    // a dynamic map in them, they are named, and a new one has to be added
    // here deliberately -- at which point the question "is this really just a
    // map?" gets asked again.
    reordered.sort();
    assert_eq!(
        reordered,
        vec!["receiver_constraint_sets".to_owned()],
        "the set of key-order differences changed; see the module docs before \
         widening it",
    );
}

#[test]
fn the_corpus_still_covers_the_paths_it_was_built_for() {
    // A corpus can rot into a set of cases that all take the same path. These
    // are the five properties the byte comparison exists to check; if a case is
    // renamed or dropped the test says which coverage went with it.
    let cases = load();
    let labels: Vec<&str> = cases.iter().map(|c| c.label.as_str()).collect();

    let floats = labels
        .iter()
        .filter(|l| l.starts_with("receiver_float_"))
        .count();
    assert!(floats >= 15, "only {floats} float spellings covered");

    // The two spellings that Rust's own `{}` gets wrong.
    for needed in ["receiver_float_1e+16", "receiver_float_1000000.0"] {
        assert!(labels.contains(&needed), "missing the {needed} case");
    }

    let unicode = labels
        .iter()
        .filter(|l| l.starts_with("node_label_"))
        .count();
    assert!(unicode >= 6, "only {unicode} escaping cases covered");

    // Every resource type, so no embedded/flatten arrangement goes unchecked.
    for resource_type in ["node", "device", "source", "flow", "sender", "receiver"] {
        assert!(
            cases.iter().any(|c| c.resource_type == resource_type),
            "no case for resource type {resource_type}",
        );
    }

    // Both branches of the Flow polymorphic chain.
    assert!(
        labels.contains(&"flow_coded"),
        "the coded Flow branch is not covered"
    );
}

#[test]
fn an_applied_default_reaches_the_bytes() {
    // The property most easily lost: a member the body never carried is
    // injected by decode, marked defined, and must therefore be written. If
    // Rust skipped it the structure would still be equivalent -- a consumer
    // applying the same default reads the same thing -- but the bytes would
    // differ and the grain would not match what Python published.
    let cases = load();
    let flow = cases
        .iter()
        .find(|c| c.label == "flow")
        .expect("the baseline flow case exists");

    assert!(
        !flow.body.get("transfer_characteristic").is_some(),
        "the fixture must NOT carry transfer_characteristic, or this proves nothing",
    );
    assert!(
        flow.encoded.contains(r#""transfer_characteristic":"SDR""#),
        "python injected the default; the corpus should show it",
    );

    let got = round_trip(&flow.resource_type, &flow.body).expect("decodes and encodes");
    assert!(
        got.contains(r#""transfer_characteristic":"SDR""#),
        "rust dropped an applied default from the encoding",
    );
}
