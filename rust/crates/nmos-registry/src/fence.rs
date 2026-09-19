// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The highest fully-applied revision, and waiting on it.
//!
//! Port of `nmos/registry/fence.py`.
//!
//! A member that answers a client must not then serve that client a view older
//! than the answer it just gave. The member that accepted a registration may
//! not be the one that applied it first, so "created, 201" has to be followed
//! by "and this member can now see it" -- otherwise a Node that immediately
//! reads back what it just registered gets a 404 for something it was told was
//! created.
//!
//! The fence is that follow-up. A caller waits for the index its mutation
//! landed at, and the wait is satisfied the moment this member has applied
//! through it.
//!
//! # Generic over a monotonic integer
//!
//! Written for etcd revisions and reused unchanged for log indices, because
//! nothing here knows or cares which it is. Both are monotonic integers that a
//! local view catches up to.
//!
//! # Why a `watch` channel
//!
//! The Python uses a condition variable and a predicate loop, because
//! `notify_all` wakes every waiter and one waiting for a higher revision must
//! go back to sleep. `tokio::sync::watch` has that structure already: each
//! waiter holds its own receiver, `changed()` returns when the value moves, and
//! the waiter re-reads and decides for itself. What the Python spells out in a
//! loop is what this channel is.

use std::time::Duration;

use tokio::sync::watch;

/// The local view did not catch up within the deadline.
///
/// The caller answers 503: the registry is behind, not broken, and the Node
/// should retry rather than treat its registration as rejected.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FenceTimeout(pub String);

impl std::fmt::Display for FenceTimeout {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for FenceTimeout {}

/// Tracks the highest fully-applied revision, and waits on it.
#[derive(Debug)]
pub struct RevisionFence {
    sender: watch::Sender<u64>,
}

impl RevisionFence {
    /// A fence already at `applied`.
    ///
    /// Seeding it correctly is what keeps startup from deadlocking: after a
    /// preload at revision `R` a fence seeded to zero would block every caller
    /// until its deadline, waiting for a revision nothing will ever announce
    /// because nothing has changed since.
    #[must_use]
    pub fn new(applied: u64) -> Self {
        Self {
            sender: watch::Sender::new(applied),
        }
    }

    /// The highest revision whose effects are fully applied locally.
    #[must_use]
    pub fn applied(&self) -> u64 {
        *self.sender.borrow()
    }

    /// How many callers are currently blocked. Diagnostic only.
    #[must_use]
    pub fn waiters(&self) -> usize {
        self.sender.receiver_count()
    }

    /// Whether a wait for `revision` would return immediately.
    #[must_use]
    pub fn satisfied(&self, revision: u64) -> bool {
        self.applied() >= revision
    }

    /// Record that everything through `revision` is applied, and wake waiters.
    ///
    /// Call this *after* the store mutation and the grain queueing for the
    /// revision, never before. The whole value of the fence is that a waiter
    /// which returns can rely on the change being visible; advancing early
    /// turns it into a hint.
    ///
    /// Never moves backwards. A redelivered revision at or below the applied
    /// one is ordinary, and treating it as a regression would let a later wait
    /// succeed against a view that had gone backwards.
    pub fn advance(&self, revision: u64) {
        self.sender.send_if_modified(|current| {
            if revision <= *current {
                return false;
            }
            *current = revision;
            true
        });
    }

    /// Re-seed after a snapshot install, waking everyone.
    ///
    /// A snapshot replaces the whole store, so the fence is repositioned
    /// rather than advanced -- the new index may be anywhere relative to the
    /// old one. Waiters are woken unconditionally because their target may now
    /// be unreachable in the ordinary way, and letting them time out with a
    /// clear message beats leaving them parked against a fence that no longer
    /// describes the same history.
    pub fn reset(&self, applied: u64) {
        // `send_modify` rather than `send_if_modified`: waking is the point,
        // and a reset to the value already held still has to release anyone
        // waiting on something the new history will never reach.
        self.sender.send_modify(|current| *current = applied);
    }

    /// Block until `revision` has been applied locally.
    ///
    /// Returns immediately when the fence is already at or past it, which is
    /// the common case: most mutations read a revision their own member has
    /// already seen.
    ///
    /// # Errors
    ///
    /// [`FenceTimeout`] if the deadline elapses first.
    pub async fn wait(&self, revision: u64, timeout: Duration) -> Result<(), FenceTimeout> {
        // Subscribing before the first check, not after: a `reset` landing
        // between the two would otherwise be missed, and the waiter would park
        // against a history that had already moved.
        let mut receiver = self.sender.subscribe();
        if *receiver.borrow_and_update() >= revision {
            return Ok(());
        }

        let watching = async {
            // A loop, not a single wait: every waiter is woken by any change,
            // and one waiting for a higher revision must go back to sleep
            // rather than proceed on someone else's wake-up.
            while receiver.changed().await.is_ok() {
                if *receiver.borrow_and_update() >= revision {
                    return true;
                }
            }
            false
        };

        match tokio::time::timeout(timeout, watching).await {
            Ok(true) => Ok(()),
            Ok(false) | Err(_) => Err(FenceTimeout(format!(
                "local view is at revision {}, still waiting for {revision} \
                 after {:.1}s",
                self.applied(),
                timeout.as_secs_f64(),
            ))),
        }
    }
}

impl Default for RevisionFence {
    fn default() -> Self {
        Self::new(0)
    }
}
