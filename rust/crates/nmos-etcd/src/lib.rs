// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! An etcd v3 client: the RPCs this registry speaks, and no others.
//!
//! The port of `nmos/etcd/`. Small and explicit by design rather than by
//! omission -- see [`generated`] for why the service stubs are not generated.
//!
//! # Layout
//!
//! * [`generated`] -- the protobuf message types, emitted from the vendored
//!   protos and committed.
//!
//! Still to come: the channel and its explicit method set, kv, lease, watch,
//! cluster (M10.3); process supervision (M10.5).
//!
//! # The wire contract is shared, and checked
//!
//! `nmos/etcd/proto/` holds the three protos, byte-identical to etcd's. One
//! stripper removes etcd's build annotations; two emitters then produce the
//! Python package and this module. Both carry `PROTO_FINGERPRINT`, both are
//! committed, and three things refuse a mismatch: this crate's `build.rs`, the
//! Python registry's startup check, and a test that compares the two trees'
//! stamps directly.
//!
//! That is the same arrangement `nmos-types` uses for the NMOS type model, and
//! it is why "both clients speak one wire contract" is a checkable claim here
//! rather than a hope.

#![doc(html_no_source)]
#![cfg_attr(
    test,
    allow(
        clippy::unwrap_used,
        clippy::expect_used,
        clippy::indexing_slicing,
        clippy::panic,
        clippy::arithmetic_side_effects
    )
)]

pub mod channel;
pub mod errors;
pub mod kv;
pub mod lease;
pub mod supervisor;
pub mod watch;

#[path = "generated/mod.rs"]
pub mod generated;

pub use channel::{Credentials, Endpoint, EtcdChannelPool, parse_endpoints};
pub use errors::{EtcdError, Result, classify};
pub use generated::PROTO_FINGERPRINT;
pub use kv::{EtcdKv, RangeResult, TxnResult, prefix_range_end};
pub use lease::{EtcdLease, Lease, LeaseStatus};
pub use supervisor::{EtcdSupervisor, ProcessOwnership, SupervisorConfig, SupervisorError};
pub use watch::{EtcdWatch, RevisionBatch, WatchStream};
