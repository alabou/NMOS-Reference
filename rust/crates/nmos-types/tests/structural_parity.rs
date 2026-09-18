// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Differential test: mutate every position in a body, not just the top level.
//!
//! `decode_parity.rs` mutates a resource's top-level keys, which exercises the
//! six resource types and nothing below them. Most of the generated tree is
//! below them: an endpoint inside `api`, a component inside `components`, a
//! clock, an interface, a channel, a constraint set. Each is a separate
//! generated type with its own member order, its own required-member checks and
//! its own assertions, and no top-level mutation ever enters one.
//!
//! `structural_cases.json` is built by walking each body to every position a
//! value can occupy -- through objects, through array elements, to any depth --
//! and mutating there. It reaches six levels down, into the rational inside the
//! enum inside a constraint set inside `caps`.
//!
//! It also covers Receiver, which the top-level corpus never built at all.
//!
//! # What the mutations are for
//!
//! * **delete** -- inside a nested type, required-member order is that type's
//!   own descriptor order, and which member gets named is the assertion.
//! * **null** -- the null rules are per base type, and they are asymmetric: a
//!   null string is dropped and stays undefined, a null URL becomes a defined
//!   empty string, a null anything-else is an error.
//! * **retype** -- the message renders a Python type name (`int`, `NoneType`,
//!   `list`) that Rust reproduces from its own values.
//! * **newline** -- the reason `nmos/validators.py` moved from `$` to `\Z`
//!   during this port. Every string position is probed, because which of them
//!   are pattern-checked is what is being asserted rather than assumed.
//! * **empty** -- an emptied array or object passes the type check and then
//!   meets whatever assertion counts its contents, which is a different path
//!   from a missing member.

#![allow(clippy::unwrap_used, clippy::expect_used, clippy::panic)]

mod common;

use common::{Case, Outcome, compare};

fn load() -> Vec<Case> {
    serde_json::from_str(include_str!("structural_cases.json"))
        .expect("the structural corpus is valid JSON")
}

#[test]
fn every_position_decodes_exactly_as_python_does() {
    let cases = load();
    assert!(cases.len() > 700, "corpus looks truncated: {}", cases.len());

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

    assert!(compared > 700, "only {compared} cases actually compared");
    assert!(
        skipped_registry_level <= 20,
        "{skipped_registry_level} cases skipped as registry-level; that number \
         should be small and stable",
    );
    assert!(
        disagreements.is_empty(),
        "{} of {compared} compared cases disagree with Python:\n  {}",
        disagreements.len(),
        disagreements
            .iter()
            .take(25)
            .cloned()
            .collect::<Vec<_>>()
            .join("\n  "),
    );
}

#[test]
fn the_corpus_reaches_past_the_top_level() {
    // Guard the guard. The whole point of this corpus is depth, and a body
    // whose optional members stopped being populated would silently shrink it
    // back to what `decode_parity` already covers.
    let cases = load();

    let depth = |label: &str| {
        label
            .rsplit(':')
            .next()
            .map_or(0, |path| path.split('.').count())
    };
    let deep = cases.iter().filter(|c| depth(&c.label) >= 3).count();
    assert!(deep > 100, "only {deep} cases reach three levels or deeper");

    let deepest = cases.iter().map(|c| depth(&c.label)).max().unwrap_or(0);
    assert!(deepest >= 4, "deepest path is only {deepest} levels");

    // The nested types that exist solely to be reached this way.
    for fragment in [
        "api.endpoints.0",
        "interfaces.0",
        "clocks.0",
        "components.0",
        "channels.0",
        "caps.constraint_sets.0",
        "subscription",
    ] {
        assert!(
            cases.iter().any(|c| c.label.contains(fragment)),
            "nothing mutates inside {fragment}",
        );
    }

    // Receiver is here and nowhere else.
    assert!(
        cases.iter().any(|c| c.resource_type == "receiver"),
        "the structural corpus is the only one that covers Receiver",
    );
}

#[test]
fn the_corpus_covers_both_verdicts_at_depth() {
    // A corpus that rejects everything proves only that Rust also rejects
    // everything. Accepted cases are what prove the mutations are targeted --
    // a nulled optional string, for instance, is *not* an error.
    let cases = load();
    let accepted = cases.iter().filter(|c| c.ok).count();
    let rejected = cases.len() - accepted;
    assert!(accepted > 50, "only {accepted} accepted cases");
    assert!(rejected > 300, "only {rejected} rejected cases");
}
