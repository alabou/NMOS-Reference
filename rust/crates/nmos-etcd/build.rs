// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Refuse to build a generated tree that no longer matches the vendored protos.
//!
//! `src/generated/` is committed, which is what lets `cargo build` work with no
//! `protoc` and no Python interpreter. The price of committing generated code
//! is that it can go stale against its source, and a stale proto tree is not a
//! build error on its own -- it is a **client speaking a different wire
//! contract from its peers**, which surfaces as records the other members read
//! differently, or not at all.
//!
//! The Python side already refuses at startup when `nmos/etcd/proto/` no longer
//! hashes to its stamped `PROTO_FINGERPRINT`, and
//! `nmos/etcd/tests/test_generated_fingerprint.py` compares the two trees. This
//! makes a stale Rust tree fail at the point it would be used.
//!
//! # Why it skips rather than fails when the protos are absent
//!
//! A published or vendored copy of this crate has no `nmos/etcd/proto/` beside
//! it, and neither does a checkout where someone is building only the Rust.
//! Failing there would be refusing to build for a reason that cannot be acted
//! on. The check is opportunistic: protos present, enforce; protos absent, say
//! so and continue.

// A hash function is fixed-size array indexing and wrapping arithmetic, which
// is what FIPS 180-4 specifies; the panic-free lints this workspace denies fire
// on nearly every line of it. Those lints exist for the registry's write path,
// where `parking_lot` locks do not poison and a panic mid-mutation would leave
// a half-applied store. A build script is not that: it runs once, before
// anything exists.
#![allow(
    clippy::indexing_slicing,
    clippy::arithmetic_side_effects,
    clippy::manual_div_ceil,
    clippy::missing_docs_in_private_items,
    // The shared SHA-256 carries a little more surface than either caller
    // needs; the alternative is two copies that differ.
    dead_code
)]

use std::fs;
use std::path::{Path, PathBuf};

/// The vendored protos, in the order `nmos/etcd/generate.py` hashes them.
///
/// **Sorted**, because the Python hashes `sorted(PROTO_FILES)`. Spelled
/// already-sorted rather than sorted here, so the agreement is visible rather
/// than incidental.
const PROTO_FILES: [&str; 3] = ["auth.proto", "kv.proto", "rpc.proto"];

fn main() {
    // `nmos-etcd/` -> `crates/` -> `rust/` -> repository root.
    let manifest =
        PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap_or_else(|_| ".".to_owned()));
    let Some(repo_root) = manifest.ancestors().nth(3) else {
        println!("cargo:warning=cannot locate the repository root; skipping the proto check");
        return;
    };

    let proto_dir = repo_root.join("nmos/etcd/proto");
    let stamp = manifest.join("src/generated/fingerprint.json");

    if !proto_dir.is_dir() {
        // Nothing to compare against. See the note above.
        return;
    }

    // Rerun only when an input could actually have changed.
    println!("cargo:rerun-if-changed={}", proto_dir.display());
    println!("cargo:rerun-if-changed={}", stamp.display());

    let Some(committed) = read_committed(&stamp) else {
        println!(
            "cargo:warning=src/generated/fingerprint.json is missing or unreadable; \
             run: python -m nmos.etcd.generate"
        );
        return;
    };

    let Some(current) = proto_fingerprint(&proto_dir) else {
        println!("cargo:warning=could not hash the vendored protos; skipping the check");
        return;
    };

    assert!(
        committed == current,
        "\n\n  The generated etcd protobuf types are stale.\n\
         \n  They were built from protos with fingerprint {}, \
         but nmos/etcd/proto/ now hashes to {}.\n\
         \n  Regenerate BOTH trees, so the Python and Rust clients keep speaking \
         one wire contract:\n\n      python -m nmos.etcd.generate\n\n",
        &committed[..12.min(committed.len())],
        &current[..12.min(current.len())],
    );
}

fn read_committed(stamp: &Path) -> Option<String> {
    // A three-line JSON file written by the generator, read without pulling a
    // JSON parser into the build graph.
    let text = fs::read_to_string(stamp).ok()?;
    let key = "\"proto_fingerprint\"";
    let after = text.split_once(key)?.1;
    let start = after.find('"')? + 1;
    let end = after[start..].find('"')? + start;
    Some(after[start..end].to_owned())
}

/// SHA-256 over `(filename, bytes)` for each vendored proto.
///
/// Matches `nmos/etcd/generate.py`'s `proto_fingerprint` exactly, including
/// that the name is hashed with **no separator** before the contents -- so a
/// change here without a change there produces a value that agrees with
/// nothing. The Python test that compares the two trees is what proves they
/// still agree.
///
/// The name is hashed as well as the contents so adding or removing a proto
/// counts as a change, not only editing one.
fn proto_fingerprint(proto_dir: &Path) -> Option<String> {
    let mut hasher = Sha256::new();
    for name in PROTO_FILES {
        hasher.update(name.as_bytes());
        hasher.update(&fs::read(proto_dir.join(name)).ok()?);
    }
    Some(hasher.finish())
}

// ---------------------------------------------------------------------------
// A minimal SHA-256
// ---------------------------------------------------------------------------
//
// Shared with `nmos-types/build.rs`, which guards the NMOS type model the same
// way this guards the vendored protos. `include!` rather than a crate because
// a crate here would be a build dependency, which is precisely what writing
// the hash out by hand avoids.
include!("../../build-support/sha256.rs");
