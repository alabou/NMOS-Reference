// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The runtime the generated NMOS types are built on.
//!
//! Counterpart to `nmos/json/` plus `nmos/validators.py` on the Python side:
//! the 25 base types, their decode rules, the encoder, the span slicer and the
//! 67 `Check*` assertions. Nothing here is generated; everything in
//! `nmos-types` calls into it.
//!
//! # Why the decode path is written, not derived
//!
//! `nmos/registry/handlers_registration.py:158` puts the decode exception's
//! text straight into the HTTP 400 body, so **the error message is an
//! observable part of the API**, not a debugging aid. Python fails in
//! *descriptor* order -- every member is decoded in the order the model
//! declares, then defaults apply, then required-presence, then assertions --
//! which is independent of the order keys appear in the document.
//!
//! `#[derive(Deserialize)]` visits fields in *document* order and fails on the
//! first one that does not parse, so `{"version":"y","id":"x"}` and
//! `{"id":"x","version":"y"}` would report different errors where Python
//! reports the same one. Untagged enums additionally backtrack, and Python's
//! polymorphic dispatch never does: once a predicate matches, that variant's
//! error is the answer.
//!
//! So the types derive `Serialize` and hand-write `decode`. This costs nothing
//! on the hot path -- the DOM being walked was already parsed by the span
//! slicer that the byte-fidelity guarantee requires -- and it is in fact
//! cheaper than deriving, which would decode from the raw slice and then need a
//! second parse to populate the body's cached form.
//!
//! # Fidelity rules that are easy to "clean up" and must not be
//!
//! These are transcribed from `nmos/json/types.py` deliberately, quirks
//! included, because the parity harness compares against Python's verdicts:
//!
//! * a JSON `null` for a plain string is **silently dropped**, leaving the
//!   member undefined -- it is not an error (`types.py:157-158`);
//! * a `null` for a URL yields a defined empty string, which re-encodes as
//!   `null`;
//! * an integer member accepts an integral float (`8080.0` is `8080`);
//! * a tags member coerces a non-list to `[]` rather than erroring;
//! * floats encode through Python's `repr` spelling, which differs from Rust's
//!   own in three ways that all reach the wire (`1000000.0` not `1000000`,
//!   `1e-05` not `0.00001`, `1e+20` not `100000000000000000000`), and which
//!   emits `inf`/`nan` bare -- invalid JSON that Python produces today and Rust
//!   reproduces. See [`engine`] for the `%g` precision bug this replaced.
//!
//! Only the nullable types (`NNullString`, `NNull`, `NGeneric`) have a true
//! three-state model. For everything else, "defined or not" is `Option<T>` and
//! null is simply not representable -- which turns a Python runtime convention
//! into a compile-time fact.

#![doc(html_no_source)]
// Test code is exempt from the panic-free lints configured for this workspace.
// Those exist to keep the *write path* panic-free, because `parking_lot` locks
// do not poison and a panic mid-mutation would leave a half-applied store. A
// test has the opposite requirement: it must panic when a value is not what it
// should be, so `unwrap` there IS the assertion rather than a hazard.
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

pub mod decode;
pub mod engine;
pub mod enums;
pub mod error;
pub mod spans;
pub mod validators;
pub mod value;

pub use enums::EnumId;
pub use error::{Error, ErrorKind, Result};
pub use value::{Hyperlink, Nullable, RawJson, Tags, Tai};
