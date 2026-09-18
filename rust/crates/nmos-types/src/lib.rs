// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! NMOS types, generated from the same descriptors the Python types come from.
//!
//! # One model, two emitters
//!
//! `nmos/codegen/definitions/*.py` is the source of truth: committed,
//! hand-edited `TypeDesc`/`MemberDesc` literals, with `predicates.py` --
//! hand-written and not derivable from anything else -- deciding which concrete
//! variant a polymorphic body decodes to. Two templates read it:
//!
//! ```text
//! nmos/codegen/definitions/*.py
//!   +-- templates/type.py.jinja2  -->  nmos/types/generated/        (Python)
//!   +-- templates/type.rs.jinja2  -->  this crate's src/generated/  (Rust)
//! ```
//!
//! They are peers. Neither is derived from the other's output, and **Go is not
//! in this picture** -- `nmos/codegen/go_parser.py` lifted the descriptors out
//! of Go once, long ago, and is not a pipeline stage. Generation never reads
//! `newGo/`.
//!
//! # Why the shape here does not mirror the Python shape
//!
//! The Python emitter produces two classes per type (`NNodeValue` + `NNode`), a
//! `_defined` flag per member, an explicit `clone()` on every field, and
//! hand-rolled `set_to_default`/`assert_valid`/`encode`/`decode`. All of that
//! exists to emulate Go value semantics in a language with neither `Option` nor
//! moves. Rust has both, so the entire layer evaporates: `_defined` **is**
//! `Option<T>`, `clone()` **is** `#[derive(Clone)]`, and 724 Python classes
//! collapse to roughly 280 Rust types.
//!
//! What does **not** change is behaviour. Accept/reject and the error text must
//! match Python exactly, including where the generated types are stricter than
//! the AMWA JSON schemas, and `nmos-parity` proves it rather than asserting it.
//!
//! # Drift
//!
//! Both generated trees carry `MODEL_FINGERPRINT`, a digest of the descriptors
//! they were built from. The Python test suite asserts the two values are
//! equal, which is what makes "these implementations describe one model" a
//! checkable claim. On this side `build.rs` also refuses to compile a tree
//! whose fingerprint no longer matches the descriptors on disk, so a stale Rust
//! tree cannot even build.

#![doc(html_no_source)]
// Test code is exempt from the panic-free lints: a test must panic when a value
// is not what it should be, so `unwrap`/`expect` there IS the assertion.
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

pub mod generated;
pub mod handwritten;
