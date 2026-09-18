// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Differential test: `POST /resource` must accept, reject and *explain*
//! exactly as Python does.
//!
//! `envelope_cases.json` records what `nmos/registry/decode.py` did for 43
//! envelopes, written by `nmos/registry/tests/_envelope_corpus.py`. Three
//! things are compared, and all three are contracts:
//!
//! * **the verdict** -- accepted or refused;
//! * **the stored bytes** -- what a Controller will read back, which must be the
//!   span out of the request and not a re-encoding of its parsed form;
//! * **the message** -- `handlers_registration.py:158` puts it straight into
//!   the 400 body.
//!
//! # The case this exists for
//!
//! The message for a body that is not JSON at all comes from the **span
//! scanner**, not from the JSON parser, because that is what Python
//! interpolates. The two differ:
//!
//! ```text
//! expected '{' at offset 0, found 'n'      <- what the client is told
//! expected ident at line 1 column 2        <- what serde_json would say
//! ```
//!
//! The first version of `decode.rs` reached for the parser's error, and every
//! hand-written test still passed because they all checked the prefix. Nothing
//! but a comparison against the real thing would have caught it.

// Test code is exempt from the panic-free lints the workspace denies.
#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing,
    clippy::arithmetic_side_effects
)]

use nmos_registry::decode::decode_post_envelope;

#[derive(serde::Deserialize)]
struct Corpus {
    cases: Vec<Case>,
}

#[derive(serde::Deserialize)]
struct Case {
    name: String,
    source: String,
    ok: bool,
    #[serde(default)]
    error: Option<String>,
    #[serde(default, rename = "type")]
    resource_type: Option<String>,
    #[serde(default)]
    stored: Option<String>,
}

fn corpus() -> Corpus {
    serde_json::from_str(include_str!("envelope_cases.json")).expect("envelope_cases.json parses")
}

#[test]
fn every_recorded_envelope_reaches_the_same_verdict_bytes_and_message() {
    let corpus = corpus();
    let mut failures = Vec::new();

    for case in &corpus.cases {
        match (decode_post_envelope(&case.source), case.ok) {
            (Ok((kind, body)), true) => {
                if Some(kind.singular()) != case.resource_type.as_deref() {
                    failures.push(format!(
                        "{}: type {:?}, expected {:?}",
                        case.name,
                        kind.singular(),
                        case.resource_type,
                    ));
                }
                if Some(body.text()) != case.stored.as_deref() {
                    failures.push(format!(
                        "{}: stored bytes differ\n  expected: {:?}\n  actual:   {:?}",
                        case.name,
                        case.stored,
                        body.text(),
                    ));
                }
            }
            (Err(error), false) => {
                if Some(error.message()) != case.error.as_deref() {
                    failures.push(format!(
                        "{}: message differs\n  expected: {:?}\n  actual:   {:?}",
                        case.name,
                        case.error,
                        error.message(),
                    ));
                }
            }
            (Ok(_), false) => failures.push(format!(
                "{}: accepted an envelope Python refused with {:?}",
                case.name, case.error,
            )),
            (Err(error), true) => failures.push(format!(
                "{}: refused an envelope Python accepted -- {:?}",
                case.name,
                error.message(),
            )),
        }
    }

    assert!(
        failures.is_empty(),
        "{} of {} envelopes differ:\n\n{}",
        failures.len(),
        corpus.cases.len(),
        failures.join("\n\n"),
    );
}

#[test]
fn the_corpus_covers_both_verdicts_and_the_rules_that_are_easy_to_get_wrong() {
    // "43 cases pass" says nothing about which 43.
    let corpus = corpus();
    let accepted = corpus.cases.iter().filter(|case| case.ok).count();
    assert!(accepted >= 5, "the corpus lost its accepting cases");
    assert!(
        corpus.cases.len().saturating_sub(accepted) >= 20,
        "the corpus lost its rejecting cases",
    );

    let names: Vec<&str> = corpus.cases.iter().map(|case| case.name.as_str()).collect();
    for required in [
        // The scanner-vs-parser message.
        "not_json",
        // "valid JSON, wrong shape" must say something different.
        "array_document",
        // Byte fidelity through the span.
        "escaped_ascii_survives",
        "exponent_survives",
        "non_ascii_survives",
        // The coercion the AMWA mock does and this refuses.
        "type_plural",
        // One validation case per resource type.
        "node_empty",
        "device_empty",
        "source_empty",
        "flow_empty",
        "sender_empty",
        "receiver_empty",
    ] {
        assert!(
            names.contains(&required),
            "the corpus lost its `{required}` case",
        );
    }
}
