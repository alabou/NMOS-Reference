// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The native consensus backend.
//!
//! Port of `nmos/raft/`. Built bottom-up: the wire format first, because it is
//! the one layer with golden vectors that a mixed Python/Rust cluster depends
//! on byte-for-byte.

#![forbid(unsafe_code)]
#![cfg_attr(
    test,
    allow(
        clippy::unwrap_used,
        clippy::expect_used,
        clippy::indexing_slicing,
        clippy::panic
    )
)]

pub mod backend;
pub mod batcher;
pub mod cluster;
pub mod commit;
pub mod cursors;
pub mod errors;
pub mod log;
pub mod machine;
pub mod messages;
pub mod node;
pub mod operations;
pub mod ownership;
pub mod persist;
pub mod snapshot;
pub mod transport;
pub mod wire;
