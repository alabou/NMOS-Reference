// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The registry's pure core: resources, cursors, the store, paging and filters.
//!
//! Everything here is synchronous and has no runtime. That is not an accident
//! of what has been ported so far -- it is the concurrency model.
//!
//! # The invariant this crate exists to hold
//!
//! `nmos/registry/store.py:29-35` states it for the Python: "no method
//! awaits... that invariant is the reason there are no locks". On one
//! event loop, a method that never awaits is atomic by construction, so the
//! store needs no locking at all.
//!
//! This port is multi-threaded -- that is the whole reason it exists -- so the
//! argument does not survive as written. It is replaced by one the compiler
//! checks: the store lives behind a `parking_lot::RwLock` whose guards are
//! `!Send`, reachable only through closures that are **not** `async`, in a
//! crate that **cannot name an async runtime**. Awaiting inside a critical
//! section is therefore not a lint, not a convention and not a review
//! question; it does not compile.
//!
//! The absence of a `tokio` dependency in `Cargo.toml` is load-bearing. If one
//! ever appears here, the guarantee is gone and every caller that assumed
//! atomicity is affected without anything failing.
//!
//! It also makes the pure logic testable with no runtime at all: ordering,
//! paging arithmetic and filter evaluation are ordinary functions over ordinary
//! values, which is most of what this crate is.
//!
//! # Where the Python is followed, and where it is not
//!
//! The default is to port as-is: the Python implementation is the
//! specification, and every difference is a liability that has to earn its
//! place. The differences that earned it are each documented on the item that
//! carries them, with what is given up:
//!
//! * [`body`] -- the parsed form is genuinely lazy, where Python holds both
//!   representations for every resource's lifetime;
//! * [`resource`] -- `health` is an atomic, so the highest-rate writer in the
//!   system leaves the exclusive lock;
//! * [`event`] -- unchanged in shape, and the reason matching can leave the
//!   critical section at all.

#![doc(html_no_source)]
// Test code is exempt from the panic-free lints this workspace denies. Those
// exist to keep the *write path* panic-free: `parking_lot` locks do not poison,
// so a panic mid-mutation would leave a half-applied store with no indication.
// A test has the opposite requirement -- it must panic when a value is not what
// it should be, so `unwrap` there IS the assertion.
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

pub mod body;
pub mod cursor;
pub mod event;
pub mod index;
pub mod links;
pub mod paging;
pub mod per;
pub mod query_filter;
pub mod resource;
pub mod resource_type;
pub mod store;

pub use body::Body;
pub use cursor::{TAI_UTC_OFFSET, TaiCursor};
pub use event::{EventKind, RegistrationError, ResourceEvent};
pub use index::CursorIndex;
pub use links::LinkResolver;
pub use paging::{Page, PagingError, PagingRequest, apply_paging, paging_headers, parse_paging};
pub use query_filter::{QueryError, UnsupportedQuery, filter_params, matches};
pub use resource::{Order, RegisteredResource, ResourceId, Tombstone};
pub use resource_type::ResourceType;
pub use store::{
    Applied, PreparedRegistration, RegistrationFailure, RegistryStatistics, RegistryStore,
};
