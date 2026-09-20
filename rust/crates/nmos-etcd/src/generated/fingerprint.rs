// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The digest of the protos this generated tree came from. DO NOT EDIT.

/// SHA-256 over the vendored `.proto` sources, names and contents.
///
/// `nmos/etcd/generated/__init__.py` carries this same value, and
/// `nmos/etcd/tests/test_generated_fingerprint.py` asserts they are
/// equal. That is what makes "both clients speak one wire contract"
/// a checkable claim rather than a hope.
// Wrapped because a 64-character digest plus the declaration exceeds
// rustfmt's line width, and `cargo fmt --check` is part of the gate.
// Emitting it already wrapped keeps a regeneration from dirtying the tree.
pub const PROTO_FINGERPRINT: &str =
    "35c9ee6f6fe01a01a0e93a4a73e02e02b841e2444d0d9af9b23af74bf5b9cac1";
