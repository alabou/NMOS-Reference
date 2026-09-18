// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! One connected WebSocket's grain buffer.
//!
//! This is the "grain" of nmos-cpp's resource model: it lives as long as the
//! socket does and holds that client's pending events.
//!
//! # Why this is unbounded, and why that is safe
//!
//! It coalesces. Keyed by resource id with an in-place merge, so however many
//! changes arrive for one resource in a window, the buffer holds **one** entry
//! for it -- which bounds the whole thing by the registry's own resource count,
//! not by the mutation rate.
//!
//! That is the opposite of the commit queue, which is unbounded and
//! deliberately does **not** coalesce: merging an add and a remove there would
//! be an observable divergence from Python. The difference is that coalescing
//! is *defined* per subscription, after classification -- it is what
//! `max_update_rate_ms` means.
//!
//! # Not an mpsc channel
//!
//! The obvious reach is a bounded `mpsc`, and both of its behaviours are wrong
//! here: `send` awaits, which cannot happen where events are produced, and
//! `try_send` drops, which loses a grain the subscriber is owed. Neither
//! preserves the per-resource coalescing the protocol requires.
//!
//! # The two wake signals
//!
//! Python's connection carries two `asyncio.Event`s and they mean different
//! things:
//!
//! * **wake** -- "there may be work". The sender waits on it, drains, writes a
//!   grain, then sleeps out the rest of its rate-limit window.
//! * **shutdown** -- "this connection is finished". Distinct from the first so
//!   that a *server-side* close tears the socket down: `Behaviour -
//!   Querying.md:19` requires a DELETE of a persistent subscription to forcibly
//!   close its connected clients, and reaping a non-persistent one does the
//!   same. Without it the handler would sit in its reader loop until the
//!   **client** chose to disconnect, and a deleted subscription would keep
//!   serving.
//!
//! Both are `tokio::sync::Notify` here, and the delivery guarantee was
//! measured rather than assumed, because a lost shutdown is a socket that
//! never goes away:
//!
//! | call | with no waiter registered |
//! |---|---|
//! | `notify_one` | **stores a permit**, delivered to the next `notified()` |
//! | `notify_waiters` | **stores nothing** -- the signal is lost |
//! | three `notify_one` | still one permit; they do not accumulate |
//!
//! `enqueue` therefore uses `notify_one`, whose permit covers a sender that has
//! not started waiting yet -- which is every sender, because `connect` queues
//! the sync burst before the task exists.
//!
//! `close` issues `notify_waiters` for anyone already parked **and**
//! `notify_one` for a waiter caught between its flag check and its
//! registration -- the one position no flag re-read can rescue, because
//! `notify_waiters` stores nothing. See `close` for the three cases.

use std::collections::HashMap;

use parking_lot::Mutex;
use tokio::sync::Notify;

use crate::commit::Sequence;
use crate::subscription::{PendingEvent, Subscription};

/// The pending events for one connected client.
#[derive(Debug)]
pub struct ConnectionBuffer {
    /// The subscription this connection serves.
    subscription: Subscription,
    /// The commit sequence this connection's sync burst describes.
    ///
    /// The matcher delivers only events **above** it. That is what replaces
    /// Python's "register the connection and queue the burst in one critical
    /// section" (`subscriptions.py:345-366`): a change committed at or below
    /// the anchor is already in the burst, one above it arrives as a grain, and
    /// there is no instant that is neither.
    anchor: Sequence,
    state: Mutex<BufferState>,
    /// "There may be work." Woken by every enqueue, and by close.
    wake: Notify,
    /// "This connection is finished." Woken only by close.
    shutdown: Notify,
}

#[derive(Debug, Default)]
struct BufferState {
    /// One entry per resource, merged in place.
    pending: HashMap<String, PendingEvent>,
    /// First-appearance order of the ids in `pending`.
    ///
    /// A `HashMap` has no order, and the grain a subscriber receives would
    /// otherwise list its entries differently on two members handed the same
    /// changes. Same reason the store sorts a cascade and `classify_batch`
    /// emits in arrival order.
    order: Vec<String>,
    /// Set once the connection is finished.
    closed: bool,
}

impl ConnectionBuffer {
    /// A buffer for one subscription, anchored at a commit sequence.
    #[must_use]
    pub fn new(subscription: Subscription, anchor: Sequence) -> Self {
        Self {
            subscription,
            anchor,
            state: Mutex::new(BufferState::default()),
            wake: Notify::new(),
            shutdown: Notify::new(),
        }
    }

    /// The subscription being served.
    #[must_use]
    pub const fn subscription(&self) -> &Subscription {
        &self.subscription
    }

    /// The commit sequence this connection's sync burst describes.
    #[must_use]
    pub const fn anchor(&self) -> Sequence {
        self.anchor
    }

    /// Buffer one event, coalescing with any pending change to the same id.
    ///
    /// Takes `&self`: the matcher enqueues from its own thread while the
    /// WebSocket task drains from another, and neither owns the buffer.
    pub fn enqueue(&self, event: PendingEvent) {
        let mut state = self.state.lock();
        if state.closed {
            // A closed connection accepts nothing. Without this a matcher that
            // has not yet noticed the close would grow a buffer nobody drains.
            return;
        }
        match state.pending.get_mut(&event.path) {
            Some(existing) => existing.merge(event),
            None => {
                state.order.push(event.path.clone());
                state.pending.insert(event.path.clone(), event);
            }
        }
        // After the mutation, before the guard drops, is fine: `notify_one`
        // does not block and does not take a lock of its own.
        drop(state);
        self.wake.notify_one();
    }

    /// Wait until there is work, or until the connection is closed.
    ///
    /// Loops rather than waiting once, because a `Notify` permit says only
    /// "something happened" -- the buffer may already have been drained by the
    /// time this wakes, and a sender that returned on the permit alone would
    /// write an empty grain, which
    /// `queryapi-subscriptions-websocket.json` forbids (`data` has
    /// `minItems: 1`).
    pub async fn wait(&self) {
        loop {
            let notified = self.wake.notified();
            if self.is_closed() || !self.is_empty() {
                return;
            }
            notified.await;
        }
    }

    /// Wait until this connection is closed from the server side.
    ///
    /// The other half of the pair: a DELETE of a persistent subscription
    /// (`Behaviour - Querying.md:19`) must tear its sockets down rather than
    /// wait for each client to notice.
    pub async fn wait_closed(&self) {
        loop {
            let notified = self.shutdown.notified();
            if self.is_closed() {
                return;
            }
            notified.await;
        }
    }

    /// Buffer several events, in order.
    pub fn enqueue_all(&self, events: impl IntoIterator<Item = PendingEvent>) {
        for event in events {
            self.enqueue(event);
        }
    }

    /// Take everything buffered, leaving the buffer empty.
    ///
    /// In first-appearance order, so the grain is reproducible.
    pub fn drain(&self) -> Vec<PendingEvent> {
        // The guard is dropped before the vector is built. Holding a lock
        // across an allocation is the small version of exactly what this port
        // exists to stop doing, and a subscriber's buffer is contended between
        // the matcher and the socket task.
        let (order, mut pending) = {
            let mut state = self.state.lock();
            (
                std::mem::take(&mut state.order),
                std::mem::take(&mut state.pending),
            )
        };
        order
            .into_iter()
            .filter_map(|path| pending.remove(&path))
            .collect()
    }

    /// How many resources have a change waiting.
    #[must_use]
    pub fn depth(&self) -> usize {
        self.state.lock().pending.len()
    }

    /// Whether anything is waiting.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.state.lock().pending.is_empty()
    }

    /// Whether this connection is finished.
    #[must_use]
    pub fn is_closed(&self) -> bool {
        self.state.lock().closed
    }

    /// Close this connection and discard what it had buffered.
    ///
    /// Discarding is deliberate. A closed socket has nobody to deliver to, and
    /// `Behaviour - Querying.md:19` says a DELETE of a persistent subscription
    /// SHOULD forcibly close its connected clients -- so anything still pending
    /// is owed to a client that is not there. Keeping it would be a leak with
    /// no reader.
    pub fn close(&self) {
        {
            let mut state = self.state.lock();
            state.closed = true;
            state.pending.clear();
            state.order.clear();
        }
        // Both calls, and the second one is the load-bearing one.
        //
        // There are three ways a waiter can be positioned when this runs:
        //
        // | waiter is... | what reaches it |
        // |---|---|
        // | not started | the `closed` flag, re-read before it ever awaits |
        // | parked | `notify_waiters` |
        // | between its check and its registration | **only a stored permit** |
        //
        // `notify_waiters` stores nothing (measured -- see the module docs), so
        // the third row needs `notify_one`, whose permit is delivered to the
        // registration that follows. That interleaving is a few instructions
        // wide and a race test for it is not a reliable detector: 300 rounds
        // against a build without the permit passed every time. It is covered
        // by construction rather than by a test, which is the honest reason
        // both calls are here.
        self.wake.notify_waiters();
        self.wake.notify_one();
        self.shutdown.notify_waiters();
        self.shutdown.notify_one();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use nmos_registry_core::body::Body;
    use nmos_registry_core::cursor::TaiCursor;
    use nmos_registry_core::resource_type::ResourceType;

    fn subscription() -> Subscription {
        Subscription {
            id: "sub-1".to_owned(),
            ws_href: "ws://example.test/ws/?uid=sub-1".to_owned(),
            resource_path: "/senders".to_owned(),
            resource_type: ResourceType::Sender,
            params: Vec::new(),
            max_update_rate_ms: 100,
            persist: true,
            secure: false,
            authorization: false,
            created: TaiCursor::new(100, 0),
            host: "example.test".to_owned(),
        }
    }

    fn event(path: &str, label: &str) -> PendingEvent {
        PendingEvent {
            path: path.to_owned(),
            pre: Some(Body::new(format!(r#"{{"label":"{label}-pre"}}"#))),
            post: Some(Body::new(format!(r#"{{"label":"{label}"}}"#))),
        }
    }

    #[test]
    fn changes_to_one_resource_coalesce_into_one_entry() {
        // What bounds the buffer by the registry's resource count rather than
        // by the mutation rate.
        let buffer = ConnectionBuffer::new(subscription(), Sequence::default());
        for label in ["a", "b", "c", "d"] {
            buffer.enqueue(event("s1", label));
        }

        assert_eq!(buffer.depth(), 1, "the buffer did not coalesce");
        let drained = buffer.drain();
        assert_eq!(drained.len(), 1);
        assert_eq!(
            drained[0]
                .post
                .as_ref()
                .and_then(|b| b.string_member("label")),
            Some("d"),
            "the last state in the window was not kept",
        );
        assert_eq!(
            drained[0]
                .pre
                .as_ref()
                .and_then(|b| b.string_member("label")),
            Some("a-pre"),
            "the first state in the window was not kept",
        );
    }

    #[test]
    fn different_resources_keep_separate_entries_in_arrival_order() {
        let buffer = ConnectionBuffer::new(subscription(), Sequence::default());
        for id in ["c", "a", "b"] {
            buffer.enqueue(event(id, "x"));
        }

        let paths: Vec<String> = buffer.drain().into_iter().map(|e| e.path).collect();
        assert_eq!(
            paths,
            ["c", "a", "b"],
            "the buffer emitted in hash order rather than arrival order",
        );
    }

    #[test]
    fn a_resource_reappearing_keeps_its_original_position() {
        // Coalescing merges into the existing entry, so a later change must not
        // move the resource to the end -- two members would then order the
        // grain differently depending on which change arrived when.
        let buffer = ConnectionBuffer::new(subscription(), Sequence::default());
        buffer.enqueue(event("a", "1"));
        buffer.enqueue(event("b", "1"));
        buffer.enqueue(event("a", "2"));

        let paths: Vec<String> = buffer.drain().into_iter().map(|e| e.path).collect();
        assert_eq!(paths, ["a", "b"]);
    }

    #[test]
    fn draining_empties_the_buffer() {
        let buffer = ConnectionBuffer::new(subscription(), Sequence::default());
        buffer.enqueue_all([event("a", "1"), event("b", "1")]);
        assert_eq!(buffer.drain().len(), 2);
        assert!(buffer.is_empty());
        assert!(buffer.drain().is_empty(), "a second drain returned entries");
    }

    #[test]
    fn a_closed_connection_accepts_nothing_and_keeps_nothing() {
        // Otherwise a matcher that has not yet noticed the close grows a buffer
        // nobody will ever drain.
        let buffer = ConnectionBuffer::new(subscription(), Sequence::default());
        buffer.enqueue(event("a", "1"));
        buffer.close();

        assert!(buffer.is_closed());
        assert!(buffer.is_empty(), "closing kept what was pending");

        buffer.enqueue(event("b", "1"));
        assert!(buffer.is_empty(), "a closed connection accepted an event");
    }

    // -- the wake signals --------------------------------------------------

    fn runtime() -> tokio::runtime::Runtime {
        tokio::runtime::Builder::new_current_thread()
            .enable_time()
            .build()
            .expect("a current-thread runtime")
    }

    #[test]
    fn waiting_returns_immediately_when_work_is_already_buffered() {
        // The sender must not park on a buffer that already has a grain in it,
        // which is exactly what the sync burst leaves behind.
        let buffer = ConnectionBuffer::new(subscription(), Sequence::default());
        buffer.enqueue(event("a", "1"));
        runtime().block_on(async {
            tokio::time::timeout(std::time::Duration::from_secs(1), buffer.wait())
                .await
                .expect("wait parked on a non-empty buffer");
        });
    }

    #[test]
    fn waiting_returns_immediately_on_a_closed_connection() {
        let buffer = ConnectionBuffer::new(subscription(), Sequence::default());
        buffer.close();
        runtime().block_on(async {
            tokio::time::timeout(std::time::Duration::from_secs(1), buffer.wait())
                .await
                .expect("wait parked on a closed connection");
            tokio::time::timeout(std::time::Duration::from_secs(1), buffer.wait_closed())
                .await
                .expect("wait_closed parked on a closed connection");
        });
    }

    #[test]
    fn an_enqueue_wakes_a_parked_sender() {
        let buffer =
            std::sync::Arc::new(ConnectionBuffer::new(subscription(), Sequence::default()));
        let writer = std::sync::Arc::clone(&buffer);
        runtime().block_on(async move {
            let waiter = tokio::spawn({
                let buffer = std::sync::Arc::clone(&buffer);
                async move { buffer.wait().await }
            });
            // Let the waiter park before anything is enqueued.
            tokio::task::yield_now().await;
            writer.enqueue(event("a", "1"));
            tokio::time::timeout(std::time::Duration::from_secs(1), waiter)
                .await
                .expect("the sender was never woken")
                .expect("the waiter did not panic");
        });
    }

    #[test]
    fn a_close_wakes_a_handler_parked_on_shutdown() {
        // `Behaviour - Querying.md:19` -- a DELETE of a persistent subscription
        // must tear its sockets down rather than wait for each client.
        let buffer =
            std::sync::Arc::new(ConnectionBuffer::new(subscription(), Sequence::default()));
        let closer = std::sync::Arc::clone(&buffer);
        runtime().block_on(async move {
            let waiter = tokio::spawn({
                let buffer = std::sync::Arc::clone(&buffer);
                async move { buffer.wait_closed().await }
            });
            tokio::task::yield_now().await;
            closer.close();
            tokio::time::timeout(std::time::Duration::from_secs(1), waiter)
                .await
                .expect("the handler was never told the connection closed")
                .expect("the waiter did not panic");
        });
    }

    #[test]
    fn a_close_also_wakes_a_sender_parked_for_work() {
        // Otherwise a sender on an idle subscription would sit until the
        // client happened to disconnect.
        let buffer =
            std::sync::Arc::new(ConnectionBuffer::new(subscription(), Sequence::default()));
        let closer = std::sync::Arc::clone(&buffer);
        runtime().block_on(async move {
            let waiter = tokio::spawn({
                let buffer = std::sync::Arc::clone(&buffer);
                async move { buffer.wait().await }
            });
            tokio::task::yield_now().await;
            closer.close();
            tokio::time::timeout(std::time::Duration::from_secs(1), waiter)
                .await
                .expect("a close left the sender parked")
                .expect("the waiter did not panic");
        });
    }

    #[test]
    fn a_close_racing_a_waiter_registration_is_never_lost() {
        // A smoke test, and labelled as one. `close` storing a permit is what
        // actually covers this interleaving; measured against a build without
        // the permit, 300 rounds detected nothing, because the window is a few
        // instructions wide. Kept because it costs little and would catch a
        // change that made the close path hang outright -- not because passing
        // it is evidence the race is handled.
        for round in 0..300 {
            let buffer =
                std::sync::Arc::new(ConnectionBuffer::new(subscription(), Sequence::default()));
            let closer = std::sync::Arc::clone(&buffer);
            let handle = std::thread::spawn(move || closer.close());
            runtime().block_on(async move {
                let waiter = tokio::spawn({
                    let buffer = std::sync::Arc::clone(&buffer);
                    async move { buffer.wait_closed().await }
                });
                tokio::time::timeout(std::time::Duration::from_secs(2), waiter)
                    .await
                    .unwrap_or_else(|_| panic!("round {round}: the close was lost"))
                    .expect("the waiter did not panic");
            });
            handle.join().expect("the closer did not panic");
        }
    }

    #[test]
    fn a_close_reaches_a_handler_that_had_not_started_waiting_yet() {
        // `close` stores no permit, so what saves this handler is that
        // `wait_closed` re-reads the flag after registering and returns without
        // ever awaiting. The ordering test above covers the racing case; this
        // covers the plain one.
        let buffer =
            std::sync::Arc::new(ConnectionBuffer::new(subscription(), Sequence::default()));
        // Closed first, waited on afterwards.
        buffer.close();
        runtime().block_on(async move {
            let waiter = tokio::spawn({
                let buffer = std::sync::Arc::clone(&buffer);
                async move { buffer.wait_closed().await }
            });
            tokio::time::timeout(std::time::Duration::from_secs(1), waiter)
                .await
                .expect("a close issued before the handler waited was lost")
                .expect("the waiter did not panic");
        });
    }

    #[test]
    fn an_enqueue_reaches_a_sender_that_had_not_started_waiting_yet() {
        // Same property on the other signal: the sync burst is enqueued by
        // `connect`, which necessarily runs before the sender task exists.
        let buffer =
            std::sync::Arc::new(ConnectionBuffer::new(subscription(), Sequence::default()));
        buffer.enqueue(event("a", "1"));
        runtime().block_on(async move {
            let waiter = tokio::spawn({
                let buffer = std::sync::Arc::clone(&buffer);
                async move { buffer.wait().await }
            });
            tokio::time::timeout(std::time::Duration::from_secs(1), waiter)
                .await
                .expect("the sender never saw work enqueued before it started")
                .expect("the waiter did not panic");
        });
    }

    #[test]
    fn waiting_does_not_return_on_a_stale_permit_with_an_empty_buffer() {
        // A permit says "something happened", not "there is work". A sender
        // that returned on the permit alone would write an empty grain, which
        // `queryapi-subscriptions-websocket.json` forbids.
        let buffer =
            std::sync::Arc::new(ConnectionBuffer::new(subscription(), Sequence::default()));
        buffer.enqueue(event("a", "1"));
        buffer.drain(); // the permit outlives the work
        let buffer2 = std::sync::Arc::clone(&buffer);
        runtime().block_on(async move {
            let parked =
                tokio::time::timeout(std::time::Duration::from_millis(200), buffer2.wait()).await;
            assert!(
                parked.is_err(),
                "wait returned with nothing buffered, so the sender would \
                 have written an empty grain",
            );
        });
    }

    #[test]
    fn enqueueing_from_several_threads_loses_nothing() {
        // The matcher enqueues from its thread while the socket task drains
        // from another; neither owns the buffer.
        use std::sync::Arc;
        use std::thread;

        let buffer = Arc::new(ConnectionBuffer::new(subscription(), Sequence::default()));
        let mut handles = Vec::new();
        for writer in 0..4_usize {
            let buffer = Arc::clone(&buffer);
            handles.push(thread::spawn(move || {
                for round in 0..250 {
                    buffer.enqueue(event(&format!("r{writer}"), &format!("{round}")));
                }
            }));
        }
        for handle in handles {
            handle.join().expect("no writer panicked");
        }

        let drained = buffer.drain();
        assert_eq!(drained.len(), 4, "one entry per resource, coalesced");
        for entry in drained {
            assert_eq!(
                entry.post.as_ref().and_then(|b| b.string_member("label")),
                Some("249"),
                "{}: the final state was not kept",
                entry.path,
            );
        }
    }
}
