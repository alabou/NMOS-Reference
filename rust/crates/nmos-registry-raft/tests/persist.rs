// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The 24 bytes that make elections safe across a restart.
//!
//! Port of `nmos/raft/tests/test_persist.py`, plus one test the Python cannot
//! have: that the file this writes is byte-identical to the file the Python
//! writes. A rolling upgrade replaces the binary and leaves the state
//! directory, so the *next* process to read a member's vote may be the other
//! implementation.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::panic
)]

use std::fs;
use std::path::PathBuf;

use nmos_registry_raft::persist::{PersistentState, STATE_VERSION, TermStore};

/// A directory that removes itself, so a failing test does not leak into the
/// next run's assertions about directory contents.
///
/// **The name carries no description of the test**, deliberately. Naming one
/// `...-unreadable` made `error.0.contains("unreadable")` pass against the
/// *path* the refusal interpolates rather than against the refusal, and the
/// mutation that stopped rejecting malformed JSON went undetected. Anything a
/// test asserts on appears in that path, so nothing in the path may be
/// meaningful.
struct Scratch(PathBuf);

/// Distinguishes concurrent tests without putting a word in the path.
static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

impl Scratch {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "nmos-raft-persist-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed),
        ));
        fs::create_dir_all(&path).expect("a scratch directory");
        Self(path)
    }

    fn file(&self) -> PathBuf {
        self.0.join("state.json")
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        drop(fs::remove_dir_all(&self.0));
    }
}

// -- a fresh member ---------------------------------------------------------

#[test]
fn a_missing_file_starts_at_term_zero() {
    let scratch = Scratch::new();
    let state = TermStore::new(scratch.file()).load().expect("loads");

    assert_eq!(state.term, 0);
    assert_eq!(state.voted_for, None);
    assert_eq!(
        state.incarnation, 1,
        "a member that has started once is on incarnation 1, not 0 -- a leader \
         distinguishes 'never seen' from 'seen and restarted' by this number",
    );
}

#[test]
fn loading_creates_the_file() {
    let scratch = Scratch::new();
    let path = scratch.file();
    assert!(!path.exists());

    TermStore::new(path.clone()).load().expect("loads");

    assert!(
        path.exists(),
        "the file is written on load, not on the first vote: a member that \
         crashed between starting and voting must still come back with the \
         right incarnation",
    );
}

// -- surviving a restart ----------------------------------------------------

#[test]
fn the_term_and_vote_come_back() {
    let scratch = Scratch::new();
    let mut store = TermStore::new(scratch.file());
    store.load().expect("loads");
    store
        .save(&PersistentState {
            term: 5,
            voted_for: Some(1),
            incarnation: 1,
        })
        .expect("saves");

    let reloaded = TermStore::new(scratch.file()).load().expect("loads");
    assert_eq!(reloaded.term, 5);
    assert_eq!(
        reloaded.voted_for,
        Some(1),
        "the vote did not survive the restart, which is the entire reason this \
         file exists -- see the log-loss scenario in the module docs",
    );
}

#[test]
fn a_vote_for_nobody_round_trips_as_none() {
    // And, below, a vote for member 0 round-trips as member 0. Member indices
    // start at 0, so collapsing the two would make a member that voted for the
    // first peer come back believing it had not voted at all -- and vote again,
    // in the same term, for someone else.
    let scratch = Scratch::new();
    let mut store = TermStore::new(scratch.file());
    store.load().expect("loads");
    store
        .save(&PersistentState {
            term: 3,
            voted_for: None,
            incarnation: 1,
        })
        .expect("saves");
    assert_eq!(
        TermStore::new(scratch.file())
            .load()
            .expect("loads")
            .voted_for,
        None,
    );

    store
        .save(&PersistentState {
            term: 4,
            voted_for: Some(0),
            incarnation: 1,
        })
        .expect("saves");
    assert_eq!(
        TermStore::new(scratch.file())
            .load()
            .expect("loads")
            .voted_for,
        Some(0),
        "a vote for member 0 came back as 'no vote', so this member would vote \
         twice in one term",
    );
}

// -- the incarnation --------------------------------------------------------

#[test]
fn the_incarnation_increments_on_every_load() {
    let scratch = Scratch::new();
    for expected in 1..=4 {
        assert_eq!(
            TermStore::new(scratch.file())
                .load()
                .expect("loads")
                .incarnation,
            expected,
        );
    }
}

#[test]
fn the_incarnation_survives_independently_of_the_term() {
    let scratch = Scratch::new();
    let mut store = TermStore::new(scratch.file());
    let first = store.load().expect("loads");
    store
        .save(&PersistentState {
            term: 9,
            voted_for: Some(2),
            incarnation: first.incarnation,
        })
        .expect("saves");

    let reloaded = TermStore::new(scratch.file()).load().expect("loads");
    assert_eq!(reloaded.term, 9);
    assert_eq!(reloaded.incarnation, 2);
}

// -- atomicity --------------------------------------------------------------

#[test]
fn a_save_leaves_no_temporary_files() {
    let scratch = Scratch::new();
    let mut store = TermStore::new(scratch.file());
    store.load().expect("loads");
    store
        .save(&PersistentState {
            term: 2,
            voted_for: Some(1),
            incarnation: 1,
        })
        .expect("saves");

    let mut names: Vec<String> = fs::read_dir(&scratch.0)
        .expect("readable")
        .map(|entry| {
            entry
                .expect("an entry")
                .file_name()
                .to_string_lossy()
                .into()
        })
        .collect();
    names.sort();
    assert_eq!(
        names,
        vec!["state.json".to_owned()],
        "a temporary file was left behind; the next process to list this \
         directory cannot tell it from state it should read",
    );
}

#[test]
fn an_unreadable_file_refuses_to_start() {
    // Refusing beats guessing: guessing here means guessing about a vote.
    let scratch = Scratch::new();
    fs::write(scratch.file(), "{ this is not json").expect("written");

    let error = TermStore::new(scratch.file())
        .load()
        .expect_err("refuses to start");
    assert!(
        error.0.contains("is unreadable:"),
        "unhelpful refusal: {}",
        error.0,
    );
}

#[test]
fn an_unknown_state_version_refuses_to_start() {
    let scratch = Scratch::new();
    fs::write(
        scratch.file(),
        format!(
            r#"{{"version": {}, "term": 1, "voted_for": null, "incarnation": 1}}"#,
            STATE_VERSION + 1
        ),
    )
    .expect("written");

    let error = TermStore::new(scratch.file())
        .load()
        .expect_err("refuses to start");
    assert!(
        error.0.contains("has state version 2,"),
        "unhelpful refusal: {}",
        error.0,
    );
}

#[test]
fn a_quoted_state_version_is_reported_as_quoted() {
    // The Python interpolates `{!r}`, so a hand-edited `"version": "1"` reads
    // as `'1'` and not as `1`. Someone staring at a refusal that says `1` when
    // the file says `1` has no way to see what is wrong.
    let scratch = Scratch::new();
    fs::write(
        scratch.file(),
        r#"{"version": "1", "term": 1, "voted_for": null, "incarnation": 1}"#,
    )
    .expect("written");

    let error = TermStore::new(scratch.file())
        .load()
        .expect_err("refuses to start");
    assert!(
        error.0.contains("has state version '1',"),
        "the refusal does not distinguish the string \"1\" from the number 1: {}",
        error.0,
    );
}

// -- write accounting -------------------------------------------------------

#[test]
fn writes_are_counted() {
    // The claim "this fsyncs on term changes, not on writes" is checkable. A
    // regression that started persisting per mutation would otherwise show up
    // only as throughput quietly collapsing, with nothing to point at.
    let scratch = Scratch::new();
    let mut store = TermStore::new(scratch.file());
    store.load().expect("loads"); // creates the file: one write
    assert_eq!(store.writes(), 1);

    for term in 2..6 {
        store
            .save(&PersistentState {
                term,
                voted_for: None,
                incarnation: 1,
            })
            .expect("saves");
    }
    assert_eq!(store.writes(), 5);
}

// -- the file is the same file ----------------------------------------------

#[test]
fn the_file_is_byte_identical_to_the_python_one() {
    // A rolling upgrade replaces the binary and keeps the state directory, so
    // the next process to read a member's vote may be the other implementation.
    // Equal-meaning JSON would be enough for that to work -- but a byte
    // comparison is what notices a key silently renamed or dropped, and it
    // costs one `assert_eq`.
    //
    // The expected text is `json.dumps({...}, indent=2)` over the same four
    // keys in the same order. Recorded here rather than exported because it is
    // four lines and a corpus file for four lines is harder to read than the
    // four lines.
    let scratch = Scratch::new();
    let mut store = TermStore::new(scratch.file());
    store
        .save(&PersistentState {
            term: 5,
            voted_for: Some(1),
            incarnation: 3,
        })
        .expect("saves");

    assert_eq!(
        fs::read_to_string(scratch.file()).expect("readable"),
        "{\n  \"version\": 1,\n  \"term\": 5,\n  \"voted_for\": 1,\n  \"incarnation\": 3\n}",
    );

    store
        .save(&PersistentState {
            term: 5,
            voted_for: None,
            incarnation: 3,
        })
        .expect("saves");
    assert_eq!(
        fs::read_to_string(scratch.file()).expect("readable"),
        "{\n  \"version\": 1,\n  \"term\": 5,\n  \"voted_for\": null,\n  \"incarnation\": 3\n}",
        "an absent vote must be written as null, not omitted: a reader that \
         finds no `voted_for` key cannot tell it from a vote it failed to parse",
    );
}
