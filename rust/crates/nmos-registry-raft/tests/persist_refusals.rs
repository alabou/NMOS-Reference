// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Both implementations refuse an unusable term/vote file the same way.
//!
//! `persist_refusals.json` is recorded from the Python by
//! `nmos/raft/tests/_persist_corpus.py`. Each case is a file that must not
//! load, plus the words the Python uses to say so.
//!
//! Why this is worth a corpus: the refusal is the only thing an operator sees,
//! and it is what tells them whether to delete the file or go looking for a
//! disk problem. Two implementations that describe the same file differently
//! are a support problem nobody notices until someone is reading a log at three
//! in the morning.
//!
//! **Parity is over the prefix, not the whole message.** Two of the three
//! refusals interpolate a caught exception, and those are CPython's words for
//! CPython's exceptions -- reproducing `KeyError('term')` in Rust would mean
//! copying strings out of another language's standard library and keeping them
//! in step with it forever. The exporter marks where that begins; everything
//! before it must match exactly, so a reader who has seen one implementation's
//! refusal recognises the other's.
//!
//! Regenerate with `python -m nmos.raft.tests._persist_corpus`.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::panic
)]

use std::fs;
use std::path::{Path, PathBuf};

use nmos_registry_raft::persist::TermStore;
use serde_json::Value;

static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

/// A directory named with a counter and nothing else.
///
/// Every refusal interpolates the path, so any word in the path is a word an
/// assertion can match by accident -- which is exactly how the mutation that
/// stopped rejecting malformed JSON went undetected once already.
struct Scratch(PathBuf);

impl Scratch {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "nmos-raft-refusals-{}-{}",
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

fn corpus() -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/persist_refusals.json");
    serde_json::from_slice(
        &std::fs::read(&path)
            .unwrap_or_else(|_| panic!("{} is missing -- regenerate it", path.display())),
    )
    .expect("the corpus parses")
}

#[test]
fn every_unusable_file_is_refused_in_the_same_words() {
    let corpus = corpus();
    let placeholder = corpus["path_placeholder"]
        .as_str()
        .expect("a path placeholder");
    let cases = corpus["cases"].as_array().expect("cases");
    assert!(!cases.is_empty(), "the corpus is empty");

    for case in cases {
        let name = case["name"].as_str().expect("a case name");
        let text = case["file"].as_str().expect("the file contents");
        let expected = case["parity_prefix"].as_str().expect("a parity prefix");

        let scratch = Scratch::new();
        fs::write(scratch.file(), text).expect("written");

        let error = TermStore::new(scratch.file())
            .load()
            .expect_err(&format!("{name}: loaded a file the Python refuses"));

        // Substituting our own path into the Python's message is the right way
        // round: it proves the path appears where the Python puts it, which
        // asserting on a suffix would not.
        let want = expected.replace(placeholder, &scratch.file().display().to_string());
        assert!(
            error.0.starts_with(&want),
            "{name}: the two implementations describe the same file \
             differently.\n  python: {want}\n  rust:   {}",
            error.0,
        );
    }
}

#[test]
fn the_corpus_covers_each_of_the_three_refusals() {
    // The three are not interchangeable: "unreadable" sends the reader to the
    // disk, "not an object" and "state version" send them to the file, and
    // "does not hold a usable term and vote" tells them which field is wrong.
    // A corpus that had drifted to cover only one would still pass the test
    // above.
    let corpus = corpus();
    let prefixes: Vec<String> = corpus["cases"]
        .as_array()
        .expect("cases")
        .iter()
        .map(|case| case["parity_prefix"].as_str().expect("a prefix").to_owned())
        .collect();

    for phrase in [
        "is unreadable: ",
        "not an object",
        "has state version",
        "does not hold a usable term and vote: ",
    ] {
        assert!(
            prefixes.iter().any(|prefix| prefix.contains(phrase)),
            "no corpus case produces the {phrase:?} refusal any more",
        );
    }
}
