// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Differential test: the NMOS error body must be byte-identical to Python's.
//!
//! `error_cases.json` records what `error_response` produces, written by
//! `nmos/api/tests/_error_corpus.py`. The body matters because
//! `handlers_registration.py:158` puts a decode exception's text straight into
//! `debug` -- it is an API contract, not a debugging aid.
//!
//! The corpus covers **every** status `http.HTTPStatus` knows, one case each,
//! plus the codes it does not know (which take the `Unknown Error` branch),
//! plus the statuses the registry actually answers with crossed against a set
//! of `debug` strings that exercise the encoder. The phrase table is 62 entries
//! written out by hand in `response.rs`, and `StatusCode::canonical_reason` is
//! deliberately not used: it returns `None` for codes CPython knows and differs
//! in wording on others.

// Test code is exempt from the panic-free lints the workspace denies.
#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing,
    clippy::arithmetic_side_effects
)]

use axum::http::StatusCode;
use nmos_registry_http::response::error_body;

#[derive(serde::Deserialize)]
struct Corpus {
    cases: Vec<Case>,
}

#[derive(serde::Deserialize)]
struct Case {
    status: u16,
    debug: String,
    body: String,
}

fn corpus() -> Corpus {
    serde_json::from_str(include_str!("error_cases.json")).expect("error_cases.json parses")
}

#[test]
fn every_recorded_error_body_matches() {
    let corpus = corpus();
    let mut failures = Vec::new();
    for case in &corpus.cases {
        let Ok(status) = StatusCode::from_u16(case.status) else {
            continue;
        };
        let actual = error_body(status, &case.debug);
        if actual != case.body {
            failures.push(format!(
                "status {} debug {:?}\n  expected: {:?}\n  actual:   {:?}",
                case.status, case.debug, case.body, actual,
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} error bodies differ:\n\n{}",
        failures.len(),
        corpus.cases.len(),
        failures.join("\n\n"),
    );
}

#[test]
fn the_corpus_covers_the_whole_phrase_table_and_the_unknown_branch() {
    // "N cases pass" says nothing about which N. The phrase table is the point.
    let corpus = corpus();
    let statuses: Vec<u16> = corpus.cases.iter().map(|case| case.status).collect();
    assert!(
        statuses.iter().filter(|s| **s >= 100).count() >= 62,
        "the corpus no longer covers the whole phrase table",
    );
    for required in [100_u16, 200, 226, 308, 418, 451, 511] {
        assert!(
            statuses.contains(&required),
            "the corpus lost status {required}",
        );
    }
    assert!(
        corpus
            .cases
            .iter()
            .any(|case| case.body.contains("Unknown Error")),
        "the corpus lost the unknown-status branch",
    );
}
