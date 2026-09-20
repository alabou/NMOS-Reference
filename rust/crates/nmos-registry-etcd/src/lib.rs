// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The etcd backend.
//!
//! A second distributed backend beside `nmos-registry-raft`, offered because
//! the backend is the part an operator has an opinion about: a site that
//! mandates etcd should not have to forgo the rest of this implementation to
//! get it. The two are interchangeable behind `RegistryBackend`, and
//! `test_cluster_conformance.py` specifies what either must satisfy without
//! naming a revision or a key.
//!
//! # What must match the Python, and what may not
//!
//! Capability and observable behaviour must match `nmos/registry/keys.py` and
//! `nmos/registry/etcd_backend.py` exactly -- a key written by one member and
//! read by another has to mean the same thing whichever implementation wrote
//! it, and a mixed cluster is a real deployment rather than a thought
//! experiment. Internal structure is free to differ where the language forces
//! it, on the same terms as the divergences the rest of the port carries.
//!
//! # Layout
//!
//! * [`keys`] -- the key layout and the value envelope. Pure, no runtime.
//!
//! Still to come: the client (M10.3), the backend itself (M10.4), process
//! supervision (M10.5).

#![doc(html_no_source)]
// Test code is exempt from the panic-free lints this workspace denies, for the
// same reason `nmos-registry-core` exempts them: those lints keep the write
// path panic-free, and a test has the opposite requirement -- it must panic
// when a value is not what it should be, so `unwrap` there IS the assertion.
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

pub mod backend;
pub mod config;
pub mod keys;
pub mod placement;

pub use backend::{ClusterMismatch, EtcdRegistryBackend};
pub use config::EtcdConfig;
pub use keys::{ENVELOPE_VERSION, Envelope, KeyFault, Namespace, ParsedKey};
pub use placement::{ParentLookup, Placement, placement_for};
