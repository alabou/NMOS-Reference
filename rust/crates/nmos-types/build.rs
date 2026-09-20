// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Refuse to build a generated tree that no longer matches the model.
//!
//! `src/generated/` is committed, which is what lets `cargo build` work without
//! a Python interpreter. The price of committing generated code is that it can
//! go stale against the descriptors it came from, and a stale type layer is not
//! a build error on its own -- it is a registry validating bodies against a
//! model nobody edits any more.
//!
//! The Python side already compares the two trees' `MODEL_FINGERPRINT` in
//! `nmos/codegen/tests/test_fingerprint.py`. That catches it, but only when
//! someone runs the Python suite. This makes a stale Rust tree fail at the
//! point it would be used.
//!
//! # Why it skips rather than fails when the model is absent
//!
//! A published or vendored copy of this crate has no `nmos/codegen/` beside it,
//! and neither does a checkout where someone is building only the Rust side.
//! Failing there would be refusing to build for a reason that cannot be acted
//! on. The check is therefore opportunistic: present model, enforce it; absent
//! model, say so and continue.

// A hash function is fixed-size array indexing and wrapping arithmetic, which
// is what FIPS 180-4 specifies; the panic-free lints this workspace denies fire
// on nearly every line of it.
//
// Those lints exist for the registry's write path, where `parking_lot` locks do
// not poison and a panic mid-mutation would leave a half-applied store. A build
// script is not that: it runs once, before anything exists, and every index
// here is into an array whose length is a compile-time constant. The one input
// of variable length -- the file contents -- is only ever appended to a buffer
// and drained in 64-byte blocks, never indexed directly.
#![allow(
    clippy::indexing_slicing,
    clippy::arithmetic_side_effects,
    clippy::manual_div_ceil,
    clippy::missing_docs_in_private_items,
    // The shared SHA-256 carries a little more surface than either caller
    // needs; the alternative is two copies that differ, which is the thing
    // sharing it is for.
    dead_code
)]

use std::fs;
use std::path::{Path, PathBuf};

fn main() {
    // `nmos-types/` -> `crates/` -> `rust/` -> repository root.
    let manifest =
        PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap_or_else(|_| ".".to_owned()));
    let Some(repo_root) = manifest.ancestors().nth(3) else {
        println!("cargo:warning=cannot locate the repository root; skipping the model check");
        return;
    };

    let definitions = repo_root.join("nmos/codegen/definitions");
    let descriptors = repo_root.join("nmos/codegen/descriptors.py");
    let stamp = manifest.join("src/generated/fingerprint.json");

    if !definitions.is_dir() || !descriptors.is_file() {
        // Nothing to compare against. See the note above.
        return;
    }

    // Rerun only when an input could actually have changed.
    println!("cargo:rerun-if-changed={}", definitions.display());
    println!("cargo:rerun-if-changed={}", descriptors.display());
    println!("cargo:rerun-if-changed={}", stamp.display());

    let Some(committed) = read_committed(&stamp) else {
        println!(
            "cargo:warning=src/generated/fingerprint.json is missing or unreadable; \
             run: python -m nmos.codegen.generate"
        );
        return;
    };

    let Some(current) = model_fingerprint(&definitions, &descriptors) else {
        println!("cargo:warning=could not hash the model; skipping the check");
        return;
    };

    assert!(
        committed == current,
        "\n\n  The generated Rust types are stale.\n\
         \n  They were built from a model with fingerprint {}, \
         but nmos/codegen/definitions/ now hashes to {}.\n\
         \n  Regenerate BOTH trees, so the Python and Rust types keep describing \
         one model:\n\n      python -m nmos.codegen.generate\n\n",
        &committed[..12.min(committed.len())],
        &current[..12.min(current.len())],
    );
}

fn read_committed(stamp: &Path) -> Option<String> {
    // A three-line JSON file written by the generator, read without pulling a
    // JSON parser into the build graph.
    let text = fs::read_to_string(stamp).ok()?;
    let key = "\"model_fingerprint\"";
    let after = text.split_once(key)?.1;
    let start = after.find('"')? + 1;
    let end = after[start..].find('"')? + start;
    Some(after[start..end].to_owned())
}

/// SHA-256 over `(filename, bytes)` for the model, matching
/// `nmos/codegen/fingerprint.py`'s `model_fingerprint`.
///
/// The name is hashed as well as the contents so that adding, removing or
/// renaming a descriptor module counts as a change -- otherwise a deleted
/// module would go unnoticed whenever another was edited in the same commit.
fn model_fingerprint(definitions: &Path, descriptors: &Path) -> Option<String> {
    let mut files: Vec<(String, PathBuf)> = fs::read_dir(definitions)
        .ok()?
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| path.extension().is_some_and(|e| e == "py"))
        .filter_map(|path| {
            let name = path.file_name()?.to_str()?.to_owned();
            Some((name, path))
        })
        .collect();
    files.sort();
    files.push(("descriptors.py".to_owned(), descriptors.to_path_buf()));

    let mut hasher = Sha256::new();
    for (name, path) in files {
        hasher.update(name.as_bytes());
        hasher.update(&[0]);
        hasher.update(&fs::read(path).ok()?);
    }
    Some(hasher.finish())
}

// ---------------------------------------------------------------------------
// A minimal SHA-256
// ---------------------------------------------------------------------------
//
// Shared with `nmos-etcd/build.rs`, which guards the vendored etcd protos the
// same way this guards the type model. Two hand-written copies of a hash
// function is two chances to fix a bug in only one of them, so there is one
// copy and both scripts splice it in. `include!` rather than a crate because a
// crate here would be a build dependency, which is precisely what writing the
// hash out by hand avoids.
include!("../../build-support/sha256.rs");
