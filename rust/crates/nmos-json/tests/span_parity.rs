// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Differential test: span slicing must agree with Python exactly.
//!
//! Two properties, and both reach a client:
//!
//! * **the spans**, which are what the registry stores and serves back, so a
//!   difference means serving something other than what was registered;
//! * **the error messages**, which `nmos/registry/decode.py:149` writes into the
//!   HTTP 400 body verbatim.
//!
//! `span_cases.json` records what Python's scanner actually did, from
//! `nmos/codegen/tests/_span_corpus.py`. A third of the cases are seeded random
//! mutations — the part of the corpus nobody designed, and therefore the part
//! most likely to find something.

#![allow(clippy::unwrap_used, clippy::expect_used, clippy::panic)]

use nmos_json::spans::member_spans;

#[derive(Debug, serde::Deserialize)]
struct Case {
    source: String,
    origin: String,
    ok: bool,
    #[serde(default)]
    message: Option<String>,
    #[serde(default)]
    spans: Vec<(String, String)>,
}

#[test]
fn span_slicing_matches_python_exactly() {
    let cases: Vec<Case> = serde_json::from_str(include_str!("span_cases.json"))
        .expect("the span corpus is valid JSON");
    assert!(cases.len() > 200, "corpus looks truncated: {}", cases.len());

    let mut wrong = Vec::new();
    for case in &cases {
        match (case.ok, member_spans(&case.source)) {
            (true, Ok(got)) => {
                let mine: Vec<(String, String)> = got
                    .into_iter()
                    .map(|(name, span)| (name, span.to_owned()))
                    .collect();
                if mine != case.spans {
                    wrong.push(format!(
                        "[{}] {:?}\n      rust   {mine:?}\n      python {:?}",
                        case.origin, case.source, case.spans,
                    ));
                }
            }
            (false, Err(error)) => {
                let want = case.message.as_deref().unwrap_or_default();
                if error.message() != want {
                    wrong.push(format!(
                        "[{}] {:?}\n      rust   {:?}\n      python {want:?}",
                        case.origin,
                        case.source,
                        error.message(),
                    ));
                }
            }
            (true, Err(error)) => wrong.push(format!(
                "[{}] {:?}: rust rejected ({}) but python accepted",
                case.origin,
                case.source,
                error.message(),
            )),
            (false, Ok(_)) => wrong.push(format!(
                "[{}] {:?}: rust accepted but python rejected ({})",
                case.origin,
                case.source,
                case.message.as_deref().unwrap_or_default(),
            )),
        }
    }

    assert!(
        wrong.is_empty(),
        "{} of {} span cases disagree with Python:\n    {}",
        wrong.len(),
        cases.len(),
        wrong
            .iter()
            .take(15)
            .cloned()
            .collect::<Vec<_>>()
            .join("\n    "),
    );
}

#[test]
fn the_corpus_exercises_both_outcomes_and_the_fuzz_tier() {
    // Guard the guard: a corpus that had drifted to all-valid or all-invalid
    // would still compare cleanly while testing half of what matters.
    let cases: Vec<Case> = serde_json::from_str(include_str!("span_cases.json")).expect("parses");
    let accepted = cases.iter().filter(|c| c.ok).count();
    let rejected = cases.len() - accepted;
    let fuzzed = cases.iter().filter(|c| c.origin == "fuzz").count();

    assert!(accepted > 20, "only {accepted} accepting cases");
    assert!(rejected > 20, "only {rejected} rejecting cases");
    assert!(fuzzed > 100, "only {fuzzed} fuzz cases");
}
