// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The registry's HTTP surface.
//!
//! Counterpart to `nmos/registry/__init__.py` (the route table),
//! `nmos/registry/handlers_*.py` and the registry's slice of `nmos/api/` --
//! `response.py`, `middleware.py` and `tr10_tls.py`.

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

pub mod browse;
pub mod config;
pub mod discovery;
pub mod escape;
pub mod jwks_cache;
pub mod oauth2;
pub mod query;
pub mod registration;
pub mod response;
pub mod router;
pub mod security;
pub mod serve;
pub mod websocket;

pub use browse::{Ordered, json_to_html};
pub use escape::escape;
pub use response::{CORS_HEADERS, Caching, RequestView, error, json, json_body, status_only};
