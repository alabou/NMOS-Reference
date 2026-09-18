// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The invariant, as something that runs.
//!
//! The crate documentation says awaiting inside a critical section "does not
//! compile". That is a claim about the type system, and a claim about the type
//! system is worth exactly as much as a test that checks it -- otherwise a
//! future change to `Locked` (a `Send` guard, an `async` closure parameter, a
//! method handing the guard out) would quietly turn the guarantee off and every
//! doc comment in the crate would go on asserting it.
//!
//! `trybuild` compiles each case in `tests/compile_fail/` and requires it to
//! **fail**, with the message in the matching `.stderr`. A case that starts
//! compiling is a test failure.

#[test]
fn awaiting_inside_a_critical_section_does_not_compile() {
    let t = trybuild::TestCases::new();
    t.compile_fail("tests/compile_fail/*.rs");
}
