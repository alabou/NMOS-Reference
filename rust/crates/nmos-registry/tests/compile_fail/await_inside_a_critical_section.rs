// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Awaiting inside a critical section must not compile.
//!
//! This is the failure the concurrency model exists to prevent, in the shape it
//! would actually arrive in: a mutation that needs one more thing and reaches
//! for it in the obvious place.
//!
//! `with_write` takes an ordinary closure, not an async one, so there is no way
//! to spell the await at all. That is a stronger guarantee than "the future
//! would not be `Send`": it does not depend on the caller being in a context
//! that requires `Send`, so it holds in a test, in a background task, and in a
//! single-threaded runtime too.

use nmos_registry::Locked;

async fn fetch_the_missing_piece() -> u32 {
    7
}

fn main() {
    let locked = Locked::new(0_u32);

    locked.with_write(|value| {
        *value = fetch_the_missing_piece().await;
    });
}
