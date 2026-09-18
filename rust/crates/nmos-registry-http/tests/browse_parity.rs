// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Differential test: the Rust browsing view must render byte for byte as the
//! Python one.
//!
//! `browse_cases.json` records what `nmos/api/response.py`'s `_json_to_html`
//! actually produced for 63 cases, written by
//! `nmos/api/tests/_browse_corpus.py`. This renders the same cases and compares
//! the **whole page**, not a substring of it.
//!
//! # Why whole-page equality rather than assertions about it
//!
//! `test_html_links.py` asserts exact `<a href>` targets and exact span
//! classes, so the markup is already a contract. Hand-written Rust expectations
//! would assert only what someone thought to assert -- and this module's own
//! unit tests got two of those wrong before this corpus existed, both by
//! forgetting that `html.escape(quote=True)` escapes the JSON string's own
//! quotes. Comparing against a recording of the real thing does not have
//! opinions.
//!
//! The corpus is kept current by `nmos/api/tests/test_browse_corpus.py`, which
//! fails the moment the Python renderer changes without a rebuild. Without that
//! half, this test would keep agreeing with what Python *used* to do.

// Test code is exempt from the panic-free lints the workspace denies: those
// keep the write path from leaving a half-applied store behind a lock that does
// not poison, and a test has the opposite requirement -- it must panic when a
// value is not what it should be.
#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing,
    clippy::arithmetic_side_effects
)]

use nmos_registry_core::links::LinkResolver;
use nmos_registry_http::browse::json_to_html;

#[derive(serde::Deserialize)]
struct Corpus {
    api_base: String,
    cases: Vec<Case>,
}

#[derive(serde::Deserialize)]
struct Case {
    name: String,
    json: String,
    path: String,
    resolver: bool,
    html: String,
}

fn corpus() -> Corpus {
    let text = include_str!("browse_cases.json");
    serde_json::from_str(text).expect("browse_cases.json parses")
}

/// Where the first difference is, as line and column, with both sides shown.
///
/// A 40-line page differing in one character is unreadable as a raw diff, and
/// the whole point of this corpus is that the differences are small and exact.
fn first_difference(expected: &str, actual: &str) -> String {
    let mut line = 1_usize;
    let mut column = 1_usize;
    for (index, (want, got)) in expected.chars().zip(actual.chars()).enumerate() {
        if want != got {
            let from = index.saturating_sub(40);
            let to = (index + 40).min(expected.len().min(actual.len()));
            return format!(
                "line {line}, column {column}\n  expected: ...{}...\n  actual:   ...{}...",
                expected.get(from..to).unwrap_or(expected),
                actual.get(from..to).unwrap_or(actual),
            );
        }
        if want == '\n' {
            line += 1;
            column = 1;
        } else {
            column += 1;
        }
    }
    format!(
        "one is a prefix of the other: expected {} chars, got {}",
        expected.chars().count(),
        actual.chars().count(),
    )
}

#[test]
fn every_recorded_page_renders_identically() {
    let corpus = corpus();
    assert!(
        corpus.cases.len() >= 60,
        "the corpus shrank to {} cases -- regenerate it",
        corpus.cases.len(),
    );

    let mut failures = Vec::new();
    for case in &corpus.cases {
        let resolver = case
            .resolver
            .then(|| LinkResolver::new(&case.path, &corpus.api_base));
        let actual = json_to_html(&case.json, &case.path, resolver.as_ref());
        if actual != case.html {
            failures.push(format!(
                "{}: {}",
                case.name,
                first_difference(&case.html, &actual),
            ));
        }
    }

    assert!(
        failures.is_empty(),
        "{} of {} recorded pages differ:\n\n{}",
        failures.len(),
        corpus.cases.len(),
        failures.join("\n\n"),
    );
}

#[test]
fn the_corpus_covers_the_rules_that_are_easy_to_get_wrong() {
    // A corpus is only as good as what is in it, and "63 cases pass" says
    // nothing about which 63. These are the cases whose absence would make the
    // parity test pass while a real rule was broken.
    let corpus = corpus();
    let names: Vec<&str> = corpus.cases.iter().map(|case| case.name.as_str()).collect();
    for required in [
        "key_order_reversed",
        "uuid_under_a_resource_page",
        "bare_version_is_data",
        "version_index_is_a_link",
        "resolver_declines_unknown_reference",
        "apostrophe",
        "float_exponent_small",
        "int_stays_int",
        "not_json",
        "unknown_segment_is_not_linked",
    ] {
        assert!(
            names.contains(&required),
            "the corpus lost its `{required}` case",
        );
    }
}
