// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Emit the etcd protobuf message types for `nmos-etcd`.
//!
//! The Rust half of `python -m nmos.etcd.generate`. The Python strips the
//! vendored protos -- there is **one** stripper, and it is the Python's,
//! because two would drift and a drifted stripper silently drops a field --
//! then stages the result and runs this.
//!
//! ```text
//! nmos/etcd/proto/*.proto      (vendored, byte-identical to etcd)
//!          |
//!          +-- strip_proto()   (generate.py: one authoritative stripper)
//!                   |
//!                   +-- protoc --python_out  -->  nmos/etcd/generated/
//!                   +-- this                 -->  rust/crates/nmos-etcd/src/generated/
//! ```
//!
//! Two emitters, one committed input, neither derived from the other's output
//! -- the same arrangement `nmos/codegen/` uses for the NMOS type model.
//!
//! # Usage
//!
//! ```text
//! nmos-etcd-codegen <staged-proto-dir> <output-dir> <fingerprint>
//! ```
//!
//! `PROTOC` must name a `protoc`. `generate.py` sets it to the one grpcio-tools
//! already ships, so regenerating needs nothing a contributor does not have.

use std::path::{Path, PathBuf};
use std::{env, fs, process};

/// The proto files, in the order `generate.py` compiles them.
const PROTOS: [&str; 3] = ["kv.proto", "auth.proto", "rpc.proto"];

/// What each proto package becomes, as a Rust module.
///
/// prost names its output after the protobuf package, so these are the file
/// names it writes. Listed rather than globbed so a package that appears or
/// disappears upstream is a loud failure here instead of a module that quietly
/// stops being emitted.
const PACKAGES: [(&str, &str); 3] = [
    ("mvccpb", "the MVCC key/value record"),
    ("authpb", "authentication, for the user and role RPCs"),
    ("etcdserverpb", "the request and response messages for every RPC"),
];

fn main() {
    if let Err(message) = run() {
        eprintln!("nmos-etcd-codegen: {message}");
        process::exit(1);
    }
}

fn run() -> Result<(), String> {
    let args: Vec<String> = env::args().skip(1).collect();
    let [staged, output, fingerprint] = args.as_slice() else {
        return Err(
            "usage: nmos-etcd-codegen <staged-proto-dir> <output-dir> <fingerprint>\n\
             Run it through `python -m nmos.etcd.generate --lang rust` rather \
             than directly: the vendored protos must be stripped first."
                .to_owned(),
        );
    };
    let staged = PathBuf::from(staged);
    let output = PathBuf::from(output);

    for name in PROTOS {
        if !staged.join(name).is_file() {
            return Err(format!("missing staged proto: {}", staged.join(name).display()));
        }
    }

    // Rebuilt rather than overwritten: a module left behind by a package that
    // no longer exists keeps compiling and silently shadows nothing, which is
    // the same orphan hazard `test_fingerprint.py` checks for on the NMOS tree.
    if output.exists() {
        fs::remove_dir_all(&output).map_err(|exc| format!("cannot clear {}: {exc}", output.display()))?;
    }
    fs::create_dir_all(&output).map_err(|exc| format!("cannot create {}: {exc}", output.display()))?;

    let mut config = prost_build::Config::new();
    config.out_dir(&output);
    // `bytes` fields become `bytes::Bytes` rather than `Vec<u8>`.
    //
    // Every etcd key and value is a `bytes` field, and the watch path carries
    // them from the wire to the store on every revision. `Bytes` is a view
    // into the buffer tonic already decoded, so that path copies a refcount
    // where `Vec<u8>` copies the value. This is the one representation choice
    // that would be a mechanical change across the whole client later, which
    // is why it is made here rather than deferred.
    config.bytes(["."]);
    // No `derive(Serialize)`: these are wire messages, and nothing serialises
    // them as JSON. The registry's own JSON goes through `nmos-json`.
    config
        .compile_protos(
            &PROTOS.map(|name| staged.join(name)),
            &[staged.as_path()],
        )
        .map_err(|exc| {
            format!(
                "protoc failed: {exc}\n\
                 PROTOC={}",
                env::var("PROTOC").unwrap_or_else(|_| "(unset)".to_owned()),
            )
        })?;

    for (package, _) in PACKAGES {
        let produced = output.join(format!("{package}.rs"));
        if !produced.is_file() {
            return Err(format!(
                "prost did not emit {}. The proto package may have been \
                 renamed upstream; update PACKAGES rather than globbing, so \
                 this stays a loud failure.",
                produced.display(),
            ));
        }
    }

    write_module(&output)?;
    write_fingerprint(&output, fingerprint)?;

    println!("generated {} files in {}", PACKAGES.len() + 2, output.display());
    Ok(())
}

/// The `mod.rs` that names each emitted package.
///
/// prost writes bare `.rs` files named after the protobuf package and leaves
/// wiring them up to the caller. Writing this rather than hand-maintaining it
/// keeps "which packages exist" in one place -- the `PACKAGES` list above --
/// so adding one is a compile error here and not a module nobody includes.
fn write_module(output: &Path) -> Result<(), String> {
    let mut text = String::from(
        "// Copyright (C) 2025-2026 Alain Bouchard\n\
         // SPDX-License-Identifier: Apache-2.0\n\
         \n\
         //! Generated etcd protobuf message types. DO NOT EDIT.\n\
         //!\n\
         //! Produced by `python -m nmos.etcd.generate --lang rust` from the\n\
         //! vendored protos in `nmos/etcd/proto/`, which are byte-identical to\n\
         //! etcd's own (see that directory's `PROVENANCE.md`).\n\
         //!\n\
         //! Committed to git, like `nmos/etcd/generated/` and\n\
         //! `nmos-types/src/generated/`. That is what lets a fresh checkout\n\
         //! build with no `protoc` and no Python -- regenerating needs both,\n\
         //! using one is free.\n\
         //!\n\
         //! The cost of committing generated code is that it can go stale\n\
         //! against its source. [`PROTO_FINGERPRINT`] is the guard, and\n\
         //! `build.rs` refuses to build a tree that no longer matches the\n\
         //! protos beside it.\n\
         //!\n\
         //! **Messages only.** The RPCs are written out explicitly in\n\
         //! `channel.rs`, naming each method path, so the set of RPCs this\n\
         //! client can speak is one readable list rather than every method\n\
         //! etcd defines.\n\
         \n\
         // Generated code is not held to this workspace's lints: it is not\n\
         // hand-written, so a lint here has nobody to instruct.\n\
         #![allow(\n\
         \x20   missing_docs,\n\
         \x20   clippy::all,\n\
         \x20   clippy::pedantic,\n\
         \x20   clippy::nursery,\n\
         \x20   clippy::arithmetic_side_effects,\n\
         \x20   clippy::indexing_slicing,\n\
         \x20   clippy::unwrap_used,\n\
         \x20   clippy::expect_used,\n\
         \x20   clippy::panic\n\
         )]\n\
         \n\
         mod fingerprint;\n\
         pub use fingerprint::PROTO_FINGERPRINT;\n\
         \n",
    );
    // Joined rather than each-with-a-trailing-blank-line, so the file ends
    // with exactly one newline. `cargo fmt --check` is part of the gate, and a
    // generator whose output rustfmt wants to change means every regeneration
    // dirties the tree -- which trains people to ignore it.
    let modules: Vec<String> = PACKAGES
        .iter()
        .map(|(package, what)| {
            format!(
                "/// `{package}.proto`: {what}.\npub mod {package} {{\n    include!(\"{package}.rs\");\n}}\n",
            )
        })
        .collect();
    text.push_str(&modules.join("\n"));

    fs::write(output.join("mod.rs"), text)
        .map_err(|exc| format!("cannot write mod.rs: {exc}"))
}

/// The digest of the protos this tree was built from, plus a JSON sidecar.
///
/// Two forms of one value, for two readers. The Rust constant is what
/// `build.rs` and the runtime check compare against; the sidecar is what the
/// Python test reads to assert the two trees agree, without having to invoke
/// cargo to find out. The same arrangement `nmos-types/src/generated/` uses.
fn write_fingerprint(output: &Path, fingerprint: &str) -> Result<(), String> {
    let text = format!(
        "// Copyright (C) 2025-2026 Alain Bouchard\n\
         // SPDX-License-Identifier: Apache-2.0\n\
         \n\
         //! The digest of the protos this generated tree came from. DO NOT EDIT.\n\
         \n\
         /// SHA-256 over the vendored `.proto` sources, names and contents.\n\
         ///\n\
         /// `nmos/etcd/generated/__init__.py` carries this same value, and\n\
         /// `nmos/etcd/tests/test_generated_fingerprint.py` asserts they are\n\
         /// equal. That is what makes \"both clients speak one wire contract\"\n\
         /// a checkable claim rather than a hope.\n\
         // Wrapped because a 64-character digest plus the declaration exceeds\n\
         // rustfmt's line width, and `cargo fmt --check` is part of the gate.\n\
         // Emitting it already wrapped keeps a regeneration from dirtying the tree.\n\
         pub const PROTO_FINGERPRINT: &str =\n\
         \x20   \"{fingerprint}\";\n",
    );
    fs::write(output.join("fingerprint.rs"), text)
        .map_err(|exc| format!("cannot write fingerprint.rs: {exc}"))?;
    fs::write(
        output.join("fingerprint.json"),
        format!("{{\n  \"proto_fingerprint\": \"{fingerprint}\"\n}}\n"),
    )
    .map_err(|exc| format!("cannot write fingerprint.json: {exc}"))
}
