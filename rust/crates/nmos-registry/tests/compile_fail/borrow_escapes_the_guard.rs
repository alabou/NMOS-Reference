// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! A reference must not escape the critical section.
//!
//! The other half of the discipline. If `with_read` could return a borrow, a
//! caller would hold a reference into the store after the guard dropped --
//! which is either a use-after-free or, with a lifetime laundered through an
//! `Arc`, a read of a value another thread is mutating.
//!
//! Requiring an owned `R` is what forces the useful pattern instead: copy the
//! fragments out, drop the guard, and encode afterwards. That is not merely
//! tidier -- it is what keeps response encoding off the critical path.

use nmos_registry::Locked;

fn main() {
    let locked = Locked::new(String::from("a resource body"));

    let escaped: &str = locked.with_read(|value| value.as_str());

    println!("{escaped}");
}
