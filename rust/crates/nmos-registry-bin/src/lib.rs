// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The registry binary's own pieces, exposed so tests can reach them.
//!
//! `cli` in particular: `tests/cli_parity.rs` checks the parser against a
//! recording of `nmos_registry.py`'s, which it cannot do through `main`.

#![doc(html_no_source)]
// Test code is exempt from the panic-free lints the workspace denies: those
// keep the serving path from leaving a half-applied state, and a test has the
// opposite requirement -- it must panic when a value is not what it should be.
#![cfg_attr(
    test,
    allow(
        clippy::unwrap_used,
        clippy::expect_used,
        clippy::indexing_slicing,
        clippy::panic
    )
)]

pub mod as_client;
pub mod cert_check;
pub mod cli;
pub mod distributed;
pub mod identity;
pub mod listen;
pub mod logging;
pub mod tls;
