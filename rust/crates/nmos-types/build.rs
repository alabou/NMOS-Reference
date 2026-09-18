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
    clippy::missing_docs_in_private_items
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
// Written out rather than taken as a dependency. A build script's dependencies
// are compiled before anything else in the graph, so pulling a crate in here
// would add a build-time cost to everyone for a check that hashes about 4,000
// lines once. FIPS 180-4, and the Python test proves it agrees.

struct Sha256 {
    state: [u32; 8],
    buffer: Vec<u8>,
    length: u64,
}

const K: [u32; 64] = [
    0x428a_2f98,
    0x7137_4491,
    0xb5c0_fbcf,
    0xe9b5_dba5,
    0x3956_c25b,
    0x59f1_11f1,
    0x923f_82a4,
    0xab1c_5ed5,
    0xd807_aa98,
    0x1283_5b01,
    0x2431_85be,
    0x550c_7dc3,
    0x72be_5d74,
    0x80de_b1fe,
    0x9bdc_06a7,
    0xc19b_f174,
    0xe49b_69c1,
    0xefbe_4786,
    0x0fc1_9dc6,
    0x240c_a1cc,
    0x2de9_2c6f,
    0x4a74_84aa,
    0x5cb0_a9dc,
    0x76f9_88da,
    0x983e_5152,
    0xa831_c66d,
    0xb003_27c8,
    0xbf59_7fc7,
    0xc6e0_0bf3,
    0xd5a7_9147,
    0x06ca_6351,
    0x1429_2967,
    0x27b7_0a85,
    0x2e1b_2138,
    0x4d2c_6dfc,
    0x5338_0d13,
    0x650a_7354,
    0x766a_0abb,
    0x81c2_c92e,
    0x9272_2c85,
    0xa2bf_e8a1,
    0xa81a_664b,
    0xc24b_8b70,
    0xc76c_51a3,
    0xd192_e819,
    0xd699_0624,
    0xf40e_3585,
    0x106a_a070,
    0x19a4_c116,
    0x1e37_6c08,
    0x2748_774c,
    0x34b0_bcb5,
    0x391c_0cb3,
    0x4ed8_aa4a,
    0x5b9c_ca4f,
    0x682e_6ff3,
    0x748f_82ee,
    0x78a5_636f,
    0x84c8_7814,
    0x8cc7_0208,
    0x90be_fffa,
    0xa450_6ceb,
    0xbef9_a3f7,
    0xc671_78f2,
];

impl Sha256 {
    fn new() -> Self {
        Self {
            state: [
                0x6a09_e667,
                0xbb67_ae85,
                0x3c6e_f372,
                0xa54f_f53a,
                0x510e_527f,
                0x9b05_688c,
                0x1f83_d9ab,
                0x5be0_cd19,
            ],
            buffer: Vec::new(),
            length: 0,
        }
    }

    fn update(&mut self, data: &[u8]) {
        self.length += data.len() as u64;
        self.buffer.extend_from_slice(data);
        while self.buffer.len() >= 64 {
            let block: [u8; 64] = self.buffer[..64].try_into().unwrap_or([0; 64]);
            self.compress(&block);
            self.buffer.drain(..64);
        }
    }

    fn finish(mut self) -> String {
        let bit_length = self.length * 8;
        self.buffer.push(0x80);
        while self.buffer.len() % 64 != 56 {
            self.buffer.push(0);
        }
        let tail = bit_length.to_be_bytes();
        self.buffer.extend_from_slice(&tail);
        while self.buffer.len() >= 64 {
            let block: [u8; 64] = self.buffer[..64].try_into().unwrap_or([0; 64]);
            self.compress(&block);
            self.buffer.drain(..64);
        }
        self.state
            .iter()
            .map(|word| format!("{word:08x}"))
            .collect()
    }

    fn compress(&mut self, block: &[u8; 64]) {
        let mut w = [0u32; 64];
        for (index, word) in w.iter_mut().take(16).enumerate() {
            let at = index * 4;
            *word = u32::from_be_bytes([block[at], block[at + 1], block[at + 2], block[at + 3]]);
        }
        for index in 16..64 {
            let s0 = w[index - 15].rotate_right(7)
                ^ w[index - 15].rotate_right(18)
                ^ (w[index - 15] >> 3);
            let s1 = w[index - 2].rotate_right(17)
                ^ w[index - 2].rotate_right(19)
                ^ (w[index - 2] >> 10);
            w[index] = w[index - 16]
                .wrapping_add(s0)
                .wrapping_add(w[index - 7])
                .wrapping_add(s1);
        }

        let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut h] = self.state;
        for index in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ ((!e) & g);
            let temp1 = h
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(K[index])
                .wrapping_add(w[index]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let temp2 = s0.wrapping_add(maj);

            h = g;
            g = f;
            f = e;
            e = d.wrapping_add(temp1);
            d = c;
            c = b;
            b = a;
            a = temp1.wrapping_add(temp2);
        }

        for (slot, value) in self.state.iter_mut().zip([a, b, c, d, e, f, g, h]) {
            *slot = slot.wrapping_add(value);
        }
    }
}
