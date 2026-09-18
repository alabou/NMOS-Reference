// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Differential test: the generated Rust types must decode exactly as the
//! generated Python types do.
//!
//! This is the claim the emitter exists to satisfy. The validator corpus proves
//! 67 hand-ported assertions agree; this proves that the machinery *around*
//! them -- member ordering, required-presence, nulls, type errors and
//! polymorphic dispatch -- agrees too, for types nobody wrote by hand on either
//! side.
//!
//! `decode_cases.json` records what Python actually did, produced by
//! `nmos/codegen/tests/_decode_corpus.py`. Asserting against it is asserting
//! against the specification rather than against a second opinion.
//!
//! Mutations here are of a resource's **top-level** keys. `structural_parity.rs`
//! is the companion that walks into the nested types.
//!
//! # The message prefix
//!
//! The corpus records the message `nmos/registry/decode.py` puts in the HTTP
//! 400 body, which wraps the type layer's own text: `node failed validation:
//! missing required member Id`. That wrapper is registry code, not type code,
//! and the registry has not been ported yet -- so the comparison strips it and
//! checks the inner message, which is what the generated types produce.
//!
//! Cases whose message has no such wrapper come from `decode.py` before the
//! type layer is reached (`expected a JSON object for node`). They are counted
//! and skipped rather than silently ignored; the test fails if the number ever
//! grows.

#![allow(clippy::unwrap_used, clippy::expect_used, clippy::panic)]

mod common;

use common::{Case, Outcome, compare, decode};

fn load() -> Vec<Case> {
    serde_json::from_str(include_str!("decode_cases.json"))
        .expect("the decode corpus is valid JSON")
}

#[test]
fn generated_types_decode_exactly_as_python_does() {
    let cases = load();
    assert!(cases.len() > 150, "corpus looks truncated: {}", cases.len());

    let mut disagreements = Vec::new();
    let mut skipped_registry_level = 0;
    let mut compared = 0;

    for case in &cases {
        match compare(case) {
            Outcome::Agreed => compared += 1,
            Outcome::RegistryLevel => skipped_registry_level += 1,
            Outcome::Disagreed(why) => {
                compared += 1;
                disagreements.push(why);
            }
        }
    }

    assert!(compared > 100, "only {compared} cases actually compared");
    assert!(
        skipped_registry_level <= 10,
        "{skipped_registry_level} cases skipped as registry-level; that number \
         should be small and stable",
    );
    assert!(
        disagreements.is_empty(),
        "{} of {compared} compared cases disagree with Python:\n  {}",
        disagreements.len(),
        disagreements.join("\n  "),
    );
}

#[test]
fn key_order_does_not_change_the_verdict() {
    // The property that forced a generated decode instead of
    // `#[derive(Deserialize)]`: Python fails in descriptor order, so a body and
    // its key-reversed twin must reach the same answer.
    let cases = load();
    let mut pairs = 0;

    for case in cases.iter().filter(|c| c.label == "keys_reversed") {
        let twin = cases
            .iter()
            .find(|c| c.resource_type == case.resource_type && c.label == "valid")
            .expect("every reversed case has a valid twin");

        let a = decode(&case.resource_type, &case.body);
        let b = decode(&twin.resource_type, &twin.body);
        assert_eq!(
            a.is_ok(),
            b.is_ok(),
            "{}: reversing key order changed the verdict",
            case.resource_type,
        );
        if let (Err(x), Err(y)) = (&a, &b) {
            assert_eq!(
                x, y,
                "{}: reversing key order changed the message",
                case.resource_type
            );
        }
        pairs += 1;
    }

    assert!(pairs >= 4, "only {pairs} key-order pairs found");
}
