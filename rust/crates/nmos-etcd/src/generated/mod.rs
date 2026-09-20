// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Generated etcd protobuf message types. DO NOT EDIT.
//!
//! Produced by `python -m nmos.etcd.generate --lang rust` from the
//! vendored protos in `nmos/etcd/proto/`, which are byte-identical to
//! etcd's own (see that directory's `PROVENANCE.md`).
//!
//! Committed to git, like `nmos/etcd/generated/` and
//! `nmos-types/src/generated/`. That is what lets a fresh checkout
//! build with no `protoc` and no Python -- regenerating needs both,
//! using one is free.
//!
//! The cost of committing generated code is that it can go stale
//! against its source. [`PROTO_FINGERPRINT`] is the guard, and
//! `build.rs` refuses to build a tree that no longer matches the
//! protos beside it.
//!
//! **Messages only.** The RPCs are written out explicitly in
//! `channel.rs`, naming each method path, so the set of RPCs this
//! client can speak is one readable list rather than every method
//! etcd defines.

// Generated code is not held to this workspace's lints: it is not
// hand-written, so a lint here has nobody to instruct.
#![allow(
    missing_docs,
    clippy::all,
    clippy::pedantic,
    clippy::nursery,
    clippy::arithmetic_side_effects,
    clippy::indexing_slicing,
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic
)]

mod fingerprint;
pub use fingerprint::PROTO_FINGERPRINT;

/// `mvccpb.proto`: the MVCC key/value record.
pub mod mvccpb {
    include!("mvccpb.rs");
}

/// `authpb.proto`: authentication, for the user and role RPCs.
pub mod authpb {
    include!("authpb.rs");
}

/// `etcdserverpb.proto`: the request and response messages for every RPC.
pub mod etcdserverpb {
    include!("etcdserverpb.rs");
}
