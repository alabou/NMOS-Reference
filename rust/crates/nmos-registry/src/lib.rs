// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The locked registry: the store behind one lock, plus subscriptions and
//! grains.
//!
//! `nmos-registry-core` holds the pure logic and cannot name an async runtime.
//! This crate is where concurrency arrives, and it arrives in exactly one
//! shape.
//!
//! # The invariant
//!
//! > **One lock, nothing inside it can await, and ordering is carried by a
//! > sequence number rather than by the lock.**
//!
//! The store lives behind a single [`lock::Locked`], reachable only through
//! `with_read` / `with_write`, which take non-async closures. Guards are
//! `!Send` and the closures are not `async`, so awaiting inside a critical
//! section does not compile -- which is how this version obtains what the
//! single-threaded asyncio original got from "no method awaits".
//!
//! A mutation applies its store change and appends `(seq, event)` to the commit
//! queue in one write critical section, and does nothing else there. It does
//! **not** match subscriptions, evaluate filters, parse bodies or build grains:
//! a matcher drains the queue in sequence order and does all of that with no
//! lock held.
//!
//! That is a deliberate divergence from the Python, where matching ran inline
//! because there was no lock to hold, and it is safe because a `ResourceEvent`
//! carries `pre` and `post` as **owned body snapshots** rather than references
//! into the store -- so classification can never observe torn state. What the
//! sequence number preserves is the property that actually matters: grains are
//! queued in commit order and none is lost. What it gives up -- a window where
//! the store holds a change whose grain is not yet buffered -- was never
//! client-visible, because delivery was always asynchronous and rate-limited.
//!
//! If you ever need to await inside a critical section, the concurrency model
//! has changed and every caller that assumes atomicity will break. The compiler
//! will tell you, and the fix is to restructure -- prepare, drop, await, apply
//! -- not to reach for `tokio::sync::RwLock` to make it compile.

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

pub mod commit;
pub mod connection;
pub mod decode;
pub mod grain;
pub mod lock;
pub mod manager;
pub mod matcher;
pub mod registry;
pub mod subscription;

pub use commit::{CommitQueue, Committed, Sequence};
pub use connection::ConnectionBuffer;
pub use decode::{DecodeFailure, decode_post_envelope};
pub use grain::{GrainError, build_grain};
pub use lock::Locked;
pub use manager::{SubscriptionError, SubscriptionManager, SubscriptionRequest};
pub use matcher::{RoutingReport, route_once};
pub use registry::{PageSnapshot, Registry, RegistryCore, ResourceSnapshot, classify_batch};
pub use subscription::{PendingEvent, Subscription, classify};
