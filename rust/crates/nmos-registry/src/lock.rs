// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The one lock, and why awaiting inside it does not compile.
//!
//! # What this replaces
//!
//! `nmos/registry/store.py:29-35` states the Python invariant:
//!
//! > **No locking.** This is single-threaded asyncio. Every public method here
//! > completes without awaiting, so no other coroutine can observe a
//! > half-applied mutation. That invariant is the reason there are no locks --
//! > preserve it: if you ever need to await inside one of these methods, the
//! > concurrency model changes and callers that assume atomicity will break.
//!
//! On one event loop that argument is sound and needs no machinery. This port
//! is multi-threaded -- that is the entire reason it exists -- so the argument
//! does not survive as written, and it is replaced by one the compiler checks.
//!
//! # Why `parking_lot` and not `tokio::sync`
//!
//! This is the single most load-bearing line in the crate, and the obvious
//! choice is precisely the wrong one.
//!
//! | | guards are | awaiting inside a critical section |
//! |---|---|---|
//! | `parking_lot::RwLock` | `!Send` | **does not compile** |
//! | `tokio::sync::RwLock` | `Send` | compiles cleanly, silently |
//! | `std::sync::RwLock` | `!Send` | does not compile, but poisons |
//!
//! Every axum handler future must be `Send`, so a `!Send` guard held across an
//! `.await` makes the future `!Send` and the handler stops compiling. That is
//! the enforcement. `tokio::sync::RwLock` looks like the natural choice in an
//! async program and is exactly wrong here: its guards *are* `Send`, so
//! awaiting under the lock compiles, the atomicity argument dies with no
//! diagnostic, and the failure surfaces later as a dropped or reordered grain.
//!
//! `std` also has `!Send` guards but adds poisoning: one panic anywhere under
//! the lock makes every subsequent acquisition fail, so a single bug in a
//! handler takes the registry down permanently. Losing poisoning is bought
//! back by making the write path panic-free by construction -- this workspace
//! denies `unwrap_used`, `expect_used`, `indexing_slicing`, `panic` and
//! `arithmetic_side_effects` on exactly these crates.
//!
//! # The closures are not `async`, and that is the second half
//!
//! `with_read` and `with_write` take ordinary closures. There is no way to
//! spell an `await` inside one, so the guarantee does not rely on a handler
//! author noticing anything. `R` must be owned, which forces the useful
//! discipline as a type error: copy what you need out of the store, drop the
//! guard, and encode afterwards.
//!
//! `tests/compile_fail.rs` asserts all of this, because a claim about what does
//! not compile is worth exactly as much as a test that it does not.

use parking_lot::{RwLock, RwLockReadGuard, RwLockWriteGuard};

/// A value that may only be touched inside a non-async critical section.
///
/// The inner value is private and there is no accessor: [`Self::with_read`] and
/// [`Self::with_write`] are the only ways in. That is deliberate -- a method
/// handing out a guard would let a caller hold it across an await in a context
/// where the future does not have to be `Send`, and the guarantee would depend
/// on nobody doing so.
#[derive(Debug, Default)]
pub struct Locked<T> {
    inner: RwLock<T>,
}

impl<T> Locked<T> {
    /// Put a value behind the lock.
    pub const fn new(value: T) -> Self {
        Self {
            inner: RwLock::new(value),
        }
    }

    /// Read the value, returning something owned.
    ///
    /// The closure is **not** `async`, so nothing inside it can await. The
    /// return type is owned rather than borrowed, so a caller cannot smuggle a
    /// reference past the guard's lifetime.
    pub fn with_read<R>(&self, f: impl FnOnce(&T) -> R) -> R {
        let guard: RwLockReadGuard<'_, T> = self.inner.read();
        f(&guard)
    }

    /// Mutate the value, returning something owned.
    ///
    /// Everything a mutation does happens here, and nothing else does: append
    /// the change, append its event to the commit queue, and return. Matching
    /// subscriptions, evaluating filters, parsing bodies and building grains
    /// all happen elsewhere, with no lock held -- see the crate docs.
    pub fn with_write<R>(&self, f: impl FnOnce(&mut T) -> R) -> R {
        let mut guard: RwLockWriteGuard<'_, T> = self.inner.write();
        f(&mut guard)
    }

    /// Replace the value wholesale, returning the old one.
    ///
    /// The seam a snapshot install needs. A Raft member handed a snapshot
    /// builds the replacement **outside** the lock -- deserialising a whole
    /// registry is far too long to hold it for -- and swaps it in with one
    /// exclusive acquisition. Readers keep serving the previous view right up
    /// to the swap and the next one afterwards, with no window in which the
    /// registry looks empty.
    pub fn replace(&self, value: T) -> T {
        let mut guard = self.inner.write();
        std::mem::replace(&mut guard, value)
    }

    /// Unwrap the value, consuming the lock. For tests and shutdown.
    pub fn into_inner(self) -> T {
        self.inner.into_inner()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;
    use std::thread;

    #[test]
    fn a_read_sees_what_a_write_left() {
        let locked = Locked::new(0_u32);
        locked.with_write(|value| *value = 7);
        assert_eq!(locked.with_read(|value| *value), 7);
    }

    #[test]
    fn concurrent_writers_do_not_lose_updates() {
        // The property the lock exists for. Without it this is a data race and
        // the total would come out below 8,000.
        let locked = Arc::new(Locked::new(0_u64));
        let mut handles = Vec::new();
        for _ in 0..8 {
            let shared = Arc::clone(&locked);
            handles.push(thread::spawn(move || {
                for _ in 0..1000 {
                    shared.with_write(|value| *value += 1);
                }
            }));
        }
        for handle in handles {
            handle.join().expect("no writer panicked");
        }
        assert_eq!(locked.with_read(|value| *value), 8000);
    }

    #[test]
    fn readers_run_concurrently_with_each_other() {
        // Not a timing assertion -- it would be flaky. It checks the weaker
        // thing that still matters: a second read acquired while a first is
        // held does not deadlock, which a mutex-backed implementation would.
        let locked = Locked::new(5_u32);
        let nested = locked.with_read(|outer| locked.with_read(|inner| *outer + *inner));
        assert_eq!(nested, 10);
    }

    #[test]
    fn replace_swaps_the_whole_value() {
        // The snapshot-install seam.
        let locked = Locked::new(vec![1, 2, 3]);
        let old = locked.replace(vec![9]);
        assert_eq!(old, vec![1, 2, 3]);
        assert_eq!(locked.with_read(Clone::clone), vec![9]);
    }

    #[test]
    fn a_panic_under_the_lock_does_not_poison_it() {
        // `std::sync::RwLock` would make every later acquisition fail, so one
        // bug in one handler would take the registry down permanently. The
        // price of not poisoning is that the write path must be panic-free by
        // construction, which is what the workspace lints enforce.
        let locked = Locked::new(1_u32);
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            locked.with_write(|_| panic!("something went wrong"));
        }));
        assert!(result.is_err(), "the panic did not propagate");

        assert_eq!(
            locked.with_read(|value| *value),
            1,
            "the lock was poisoned by a panic under it",
        );
        locked.with_write(|value| *value = 2);
        assert_eq!(locked.with_read(|value| *value), 2);
    }

    #[test]
    fn a_guard_is_not_send() {
        // The mechanism itself, asserted rather than assumed. If this ever
        // becomes true, `with_write(|c| async { ... }.await)` starts compiling
        // in a `Send` future and the whole model is gone with no other signal.
        fn is_send<T: Send>() {}
        // These compile, which is the baseline.
        is_send::<Locked<u32>>();
        is_send::<u32>();

        // And this is the claim. It cannot be written as a compiling
        // assertion -- a negative trait bound is not expressible -- so it is
        // checked by `tests/compile_fail.rs` instead, which is where a reader
        // should look.
        const _: () = ();
    }
}
