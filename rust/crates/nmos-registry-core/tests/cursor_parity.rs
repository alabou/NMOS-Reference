// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The accept/reject boundary for a cursor is the same on both sides.
//!
//! `cursor_cases.json` is recorded from `nmos/registry/types.py`'s
//! `TaiCursor.parse` by `nmos/registry/tests/_cursor_corpus.py`.
//!
//! This is decision #7 -- "Rust's accept/reject must match Python exactly" --
//! applied to the one value three different client-reachable inputs share:
//! `paging.since`, `paging.until`, and a resource's `version`. All three are
//! bounded only by `^[0-9]+:[0-9]+$`, which puts no ceiling on either field.
//!
//! It exists because that boundary had already moved without anything
//! noticing. A 32-bit nanosecond field refused `0:5000000000` -- pattern-valid,
//! and accepted by the Python -- so the same registration was a 201 against one
//! implementation and a 400 against the other. The store corpus did not catch
//! it: a version is only parsed when it *updates* an existing resource, and no
//! boundary version happened to land on an update. Coverage without exercise.
//!
//! Regenerate with `python -m nmos.registry.tests._cursor_corpus`.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::panic
)]

use nmos_registry_core::cursor::TaiCursor;
use serde_json::Value;

fn corpus() -> Value {
    serde_json::from_str(include_str!("cursor_cases.json")).expect("the corpus is valid JSON")
}

#[test]
fn every_string_is_accepted_or_refused_the_same_way() {
    let corpus = corpus();
    let cases = corpus["cases"].as_array().expect("cases");
    assert!(!cases.is_empty(), "the corpus is empty");

    for case in cases {
        let text = case["text"].as_str().expect("a candidate string");
        let parsed = TaiCursor::parse(text);

        match case["seconds"].as_u64() {
            None => assert!(
                parsed.is_none(),
                "{text:?} is refused by the Python and accepted here as \
                 {parsed:?} -- the same request is a 400 against one \
                 implementation and a 200 against the other",
            ),
            Some(seconds) => {
                let nanoseconds = case["nanoseconds"].as_u64().expect("nanoseconds");
                let cursor = parsed.unwrap_or_else(|| {
                    panic!(
                        "{text:?} is accepted by the Python as \
                         {seconds}:{nanoseconds} and refused here"
                    )
                });
                assert_eq!(cursor.seconds, seconds, "{text:?}: seconds differ");
                assert_eq!(
                    cursor.nanoseconds, nanoseconds,
                    "{text:?}: nanoseconds differ",
                );
            }
        }
    }
}

#[test]
fn an_accepted_cursor_renders_back_to_the_python_form() {
    // `X-Paging-Since` and `X-Paging-Until` echo the cursor, and a `Link`
    // header carries it into the next request. A cursor that parses but
    // renders differently would send a client to a different page than the one
    // it asked for -- and it would do so only for the inputs that differ, so it
    // would look like an intermittent paging fault.
    for case in corpus()["cases"].as_array().expect("cases") {
        let Some(expected) = case["rendered"].as_str() else {
            continue;
        };
        let text = case["text"].as_str().expect("a candidate string");
        let cursor = TaiCursor::parse(text).expect("the Python accepted it");
        assert_eq!(cursor.to_string(), expected, "{text:?} renders differently",);
    }
}

#[test]
fn the_corpus_still_reaches_past_thirty_two_bits() {
    // The specific hole this corpus was built for. A corpus that drifted back
    // to ordinary cursors would pass every assertion above while testing
    // nothing that had ever been wrong.
    let corpus = corpus();
    let beyond = corpus["cases"]
        .as_array()
        .expect("cases")
        .iter()
        .filter(|case| {
            case["nanoseconds"]
                .as_u64()
                .is_some_and(|n| n > u64::from(u32::MAX))
        })
        .count();
    assert!(
        beyond >= 2,
        "only {beyond} accepted case(s) carry a nanosecond field above 2^32; \
         the narrowing this corpus exists to catch would go unnoticed again",
    );
}
