// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Coalescing proposals into one quorum round per burst.
//!
//! Port of `nmos/raft/batcher.py`. This is where the throughput claim comes
//! from, and it is a smaller piece of code than the claim suggests.
//!
//! # The mechanism, and why it is not the Python's
//!
//! **Divergence, and the one place this crate could not port as-is.**
//!
//! The Python batches on `loop.call_soon`, which schedules a callback to run
//! after the current one finishes but *before* the loop returns to the
//! selector. So every handler resumed from one `epoll` wakeup -- which is every
//! request that arrived in the same burst -- submits before the drain runs, and
//! they all land in one batch. Batching with no timer, no configured window and
//! no added latency: the batch closes exactly when there is nothing left to add
//! to it.
//!
//! That argument rests on a single-threaded loop with a well-defined tick.
//! There is no tick here. Handlers run on several worker threads at once, and
//! "everything resumed from one wakeup" names no set.
//!
//! So the mechanism is a channel and a drain task: the task blocks on `recv`,
//! and once woken takes everything already queued with `try_recv` before
//! handing the batch over. What that coalesces is "every proposal that arrived
//! while the previous round was in flight", which is the same *effect* -- one
//! quorum round per burst, the batch closing as soon as nothing more is ready
//! -- reached by a different route. It is not a timer, and it adds no latency
//! of its own: an idle registry's single proposal wakes the task and is handed
//! over immediately.
//!
//! What is given up is the Python's exactness about *which* proposals share a
//! batch. Under load the batches are the same size for the same reason; at the
//! margin, a proposal that the Python would have put in batch N may land in
//! N+1. Nothing depends on that -- a proposal's outcome is decided by the log,
//! not by which batch carried it.
//!
//! # Why the receiver is created synchronously
//!
//! [`ProposalBatcher::submit`] is not `async`. It queues the operation and
//! returns the receiver in one uninterrupted step, so the caller's interest is
//! registered before anything can await. If it were async, the entry could
//! commit and apply in the window between "the operation was accepted" and "the
//! waiter exists" -- and the result would be delivered to nobody while the
//! caller waited forever for something that had already happened.
//!
//! # What it deliberately does not do
//!
//! No retry, no timeout, no reordering. A batcher that retried would duplicate
//! operations across terms; a batcher that reordered would break the per-Node
//! serialisation the ownership design relies on. Both belong to the layer that
//! knows about terms and leadership.

use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

use tokio::sync::{mpsc, oneshot};

/// One submitted operation and the channel waiting on its outcome.
#[derive(Debug)]
pub struct Pending<T, R> {
    /// What was proposed.
    pub operation: T,
    /// Where its outcome goes. The drain takes ownership and must resolve it.
    pub reply: oneshot::Sender<R>,
}

/// The batcher was shut down, or the node is no longer accepting proposals.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BatcherClosed;

impl std::fmt::Display for BatcherClosed {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("the proposal batcher is closed")
    }
}

impl std::error::Error for BatcherClosed {}

/// Collects operations and hands them to a drain as one batch.
///
/// Cloneable: every request handler holds one, and they all feed the single
/// drain.
#[derive(Debug)]
pub struct ProposalBatcher<T, R> {
    tx: mpsc::UnboundedSender<Pending<T, R>>,
    depth: Arc<AtomicUsize>,
}

impl<T, R> Clone for ProposalBatcher<T, R> {
    fn clone(&self) -> Self {
        Self {
            tx: self.tx.clone(),
            depth: Arc::clone(&self.depth),
        }
    }
}

/// The drain side. Held by the node, and by nothing else.
#[derive(Debug)]
pub struct ProposalDrain<T, R> {
    rx: mpsc::UnboundedReceiver<Pending<T, R>>,
    depth: Arc<AtomicUsize>,
    max_batch: usize,
}

/// Upper bound on one batch.
///
/// Reached only under sustained load heavier than one quorum round can absorb,
/// where the excess simply forms the next batch. It exists so a single
/// `AppendEntries` cannot grow past the frame cap.
pub const MAX_BATCH: usize = 1024;

/// A batcher and its drain.
#[must_use]
pub fn proposal_channel<T, R>(max_batch: usize) -> (ProposalBatcher<T, R>, ProposalDrain<T, R>) {
    // Unbounded, and that is the same decision the connection buffers make: a
    // bounded channel's `send` awaits, which would put an await between "the
    // operation was accepted" and "the waiter exists" -- the exact window
    // `submit` is synchronous to avoid. Growth is bounded in practice by the
    // drain running every round; if it ever is not, `depth` is the metric that
    // says so.
    let (tx, rx) = mpsc::unbounded_channel();
    let depth = Arc::new(AtomicUsize::new(0));
    (
        ProposalBatcher {
            tx,
            depth: Arc::clone(&depth),
        },
        ProposalDrain {
            rx,
            depth,
            max_batch,
        },
    )
}

impl<T, R> ProposalBatcher<T, R> {
    /// Queue an operation for the next drain. Returns where its outcome lands.
    ///
    /// Synchronous and non-awaiting, for the reason in the module docs.
    ///
    /// # Errors
    ///
    /// [`BatcherClosed`] if the drain is gone, which means this member is
    /// shutting down or has stopped accepting proposals.
    pub fn submit(&self, operation: T) -> Result<oneshot::Receiver<R>, BatcherClosed> {
        let (reply, receiver) = oneshot::channel();
        self.tx
            .send(Pending { operation, reply })
            .map_err(|_| BatcherClosed)?;
        self.depth.fetch_add(1, Ordering::Relaxed);
        Ok(receiver)
    }

    /// How many proposals are queued.
    ///
    /// The overload metric: sustained growth means the drain is not keeping up
    /// with the arrival rate, which is a cluster that cannot commit as fast as
    /// it is being asked to.
    #[must_use]
    pub fn pending(&self) -> usize {
        self.depth.load(Ordering::Relaxed)
    }
}

impl<T, R> ProposalDrain<T, R> {
    /// Wait for at least one proposal, then take everything already queued.
    ///
    /// `None` once every [`ProposalBatcher`] has been dropped.
    ///
    /// The `try_recv` loop after the first `recv` is the whole of the
    /// coalescing: it takes what is *already* there and does not wait for more,
    /// so the batch closes at the earliest moment it could.
    pub async fn next_batch(&mut self) -> Option<Vec<Pending<T, R>>> {
        let first = self.rx.recv().await?;
        let mut batch = Vec::with_capacity(1);
        batch.push(first);

        while batch.len() < self.max_batch {
            match self.rx.try_recv() {
                Ok(pending) => batch.push(pending),
                Err(_) => break,
            }
        }

        self.depth.fetch_sub(batch.len(), Ordering::Relaxed);
        Some(batch)
    }

    /// Take everything queued without waiting.
    ///
    /// For shutdown and for losing leadership, where the caller fails each
    /// waiter rather than proposing it. A member that loses leadership and
    /// regains it keeps the same batcher: one that refused to accept anything
    /// after a single failure would stop serving until it restarted.
    pub fn drain_now(&mut self) -> Vec<Pending<T, R>> {
        let mut batch = Vec::new();
        while let Ok(pending) = self.rx.try_recv() {
            batch.push(pending);
        }
        self.depth.fetch_sub(batch.len(), Ordering::Relaxed);
        batch
    }
}
