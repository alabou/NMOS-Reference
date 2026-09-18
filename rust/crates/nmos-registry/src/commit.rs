// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The commit queue: what carries ordering now that the lock does not.
//!
//! # The divergence, and what it buys
//!
//! `nmos/registry/subscriptions.py` matches subscriptions **inline**, in the
//! same step that mutates the store, and its docstring says why: "queueing
//! happens in the same uninterrupted step so no coroutine can observe a change
//! whose grain has not been buffered". On one event loop that costs nothing,
//! because there is no lock to hold.
//!
//! Here it would cost a great deal. `publish` is O(events x subscriptions), and
//! classification calls `Body::data`, which **parses JSON on first use**.
//! Porting it as written would put a JSON parse and N filter evaluations inside
//! the exclusive write lock, in a port whose entire purpose is multi-core write
//! throughput.
//!
//! So a mutation does two things under the lock and nothing else:
//!
//! ```text
//! WRITE LOCK                      MATCHER (no lock held)
//!   store.apply(...)                drain in seq order
//!   queue.push(seq, event)  O(1)    for each subscription:
//! DROP                                classify()   <- the parse happens here
//!                                     connection.enqueue(pending)
//! ```
//!
//! # Why this is sound
//!
//! A [`ResourceEvent`] already carries `pre` and `post` as **owned body
//! snapshots**, not references into the store -- which is what lets a grain
//! splice `pre.text` verbatim. Filter evaluation therefore needs nothing from
//! the store, and running it later cannot observe torn state.
//!
//! # What is preserved, and what is given up
//!
//! Preserved: grains are queued in commit order, and none is lost. A single
//! drainer taking them in sequence order is what provides that, in place of the
//! lock.
//!
//! Given up: a window in which the store holds a change whose grain is not yet
//! buffered. That window is not client-observable, because grain *delivery* was
//! always asynchronous and rate-limited -- Python's synchronous matching never
//! made HTTP and WebSocket mutually ordered, it only made *buffering* ordered,
//! and a sequence number gives that directly.
//!
//! # The queue does not coalesce, deliberately
//!
//! Coalescing is per-subscription and happens *after* classification. Merging
//! an add and a remove for one resource into nothing here would be an
//! observable divergence from Python, which emits both. So this stays
//! unbounded, its depth is a metric, and sustained growth means the matcher is
//! the bottleneck and wants sharding -- an overload that would have stalled
//! Python outright.

use std::collections::VecDeque;

use nmos_registry_core::event::ResourceEvent;

/// A monotonically increasing position in the commit order.
///
/// Wraps a `u64`, which at a million mutations a second would take about
/// 580,000 years to exhaust -- so the arithmetic below saturates rather than
/// wrapping, and the saturation is unreachable rather than handled.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Default)]
pub struct Sequence(u64);

impl Sequence {
    /// The position before any commit. A connection anchored here receives
    /// everything.
    pub const ZERO: Self = Self(0);

    /// The next position.
    #[must_use]
    pub const fn next(self) -> Self {
        Self(self.0.saturating_add(1))
    }

    /// The raw value, for metrics and logs.
    #[must_use]
    pub const fn get(self) -> u64 {
        self.0
    }
}

/// One committed change, with its position in the commit order.
#[derive(Debug, Clone)]
pub struct Committed {
    /// Where this sits in the commit order.
    pub sequence: Sequence,
    /// What changed.
    pub event: ResourceEvent,
}

/// The queue a mutation appends to and a matcher drains.
///
/// Lives inside the store's critical section; the matcher takes the whole
/// backlog in one short acquisition and then works through it with no lock
/// held.
#[derive(Debug, Default)]
pub struct CommitQueue {
    entries: VecDeque<Committed>,
    next: Sequence,
    /// The highest depth reached since the last reset.
    ///
    /// The metric the module docs promise. Depth at any instant is nearly
    /// meaningless -- the matcher usually empties the queue between samples --
    /// so what is recorded is the high-water mark, which is what actually
    /// distinguishes "keeping up" from "falling behind".
    high_water: usize,
}

impl CommitQueue {
    /// An empty queue.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Append a change, assigning it the next sequence number.
    ///
    /// Called with the write lock held, and it is O(1) -- which is the whole
    /// point. Returns the assigned sequence so a caller can anchor to it.
    pub fn push(&mut self, event: ResourceEvent) -> Sequence {
        self.next = self.next.next();
        self.entries.push_back(Committed {
            sequence: self.next,
            event,
        });
        self.high_water = self.high_water.max(self.entries.len());
        self.next
    }

    /// Append several changes in order, returning the last sequence assigned.
    ///
    /// One mutation can produce several events -- a cascade delete produces
    /// one per descendant -- and they must occupy consecutive positions, in the
    /// order given, so a subscriber replaying them never sees a parent
    /// disappear while its children are still present.
    pub fn extend(&mut self, events: impl IntoIterator<Item = ResourceEvent>) -> Sequence {
        for event in events {
            self.push(event);
        }
        self.next
    }

    /// The sequence most recently assigned.
    ///
    /// What a connecting client anchors to: everything at or below this is in
    /// its sync burst, everything above arrives as a grain. That is what closes
    /// the gap a `connect` would otherwise leave.
    #[must_use]
    pub const fn latest(&self) -> Sequence {
        self.next
    }

    /// Take everything queued, leaving the queue empty.
    ///
    /// The whole backlog rather than one entry, so the matcher holds the lock
    /// once per batch instead of once per event.
    pub fn drain(&mut self) -> Vec<Committed> {
        self.entries.drain(..).collect()
    }

    /// How many entries are waiting.
    #[must_use]
    pub fn depth(&self) -> usize {
        self.entries.len()
    }

    /// The highest depth reached since the last [`Self::reset_high_water`].
    #[must_use]
    pub const fn high_water(&self) -> usize {
        self.high_water
    }

    /// Start a new high-water measurement window.
    pub fn reset_high_water(&mut self) {
        self.high_water = self.entries.len();
    }

    /// Whether anything is waiting.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use nmos_registry_core::body::Body;
    use nmos_registry_core::event::EventKind;
    use nmos_registry_core::resource_type::ResourceType;

    fn event(id: &str) -> ResourceEvent {
        ResourceEvent {
            kind: EventKind::Added,
            resource_type: ResourceType::Sender,
            resource_id: id.to_owned(),
            pre: None,
            post: Some(Body::new(r#"{"id":"x"}"#)),
        }
    }

    #[test]
    fn sequences_are_assigned_in_order_and_start_above_zero() {
        // `ZERO` is "before anything", so a connection anchored there receives
        // the first commit. If the first assigned sequence were also zero, that
        // first commit would be filtered out as "already seen".
        let mut queue = CommitQueue::new();
        assert_eq!(queue.latest(), Sequence::ZERO);

        let first = queue.push(event("a"));
        assert!(
            first > Sequence::ZERO,
            "the first commit collides with ZERO"
        );
        assert_eq!(queue.push(event("b")), first.next());
    }

    #[test]
    fn draining_preserves_commit_order() {
        // What replaces the lock as the carrier of ordering.
        let mut queue = CommitQueue::new();
        for id in ["a", "b", "c", "d"] {
            queue.push(event(id));
        }

        let drained = queue.drain();
        let ids: Vec<&str> = drained
            .iter()
            .map(|c| c.event.resource_id.as_str())
            .collect();
        assert_eq!(ids, ["a", "b", "c", "d"]);

        let sequences: Vec<u64> = drained.iter().map(|c| c.sequence.get()).collect();
        assert_eq!(sequences, [1, 2, 3, 4], "sequences are not consecutive");
        assert!(queue.is_empty(), "drain left entries behind");
    }

    #[test]
    fn a_cascade_occupies_consecutive_positions_in_its_own_order() {
        // A delete produces one event per descendant, children first. They must
        // stay adjacent and in order, or a subscriber replaying them sees a
        // parent disappear while its children are still present.
        let mut queue = CommitQueue::new();
        queue.push(event("unrelated"));
        let last = queue.extend([event("child"), event("parent")]);

        let drained = queue.drain();
        let ids: Vec<&str> = drained
            .iter()
            .map(|c| c.event.resource_id.as_str())
            .collect();
        assert_eq!(ids, ["unrelated", "child", "parent"]);
        assert_eq!(last, drained.last().expect("not empty").sequence);
    }

    #[test]
    fn latest_is_what_a_connecting_client_anchors_to() {
        let mut queue = CommitQueue::new();
        queue.push(event("before"));
        let anchor = queue.latest();
        queue.push(event("after"));

        let drained = queue.drain();
        let later: Vec<&str> = drained
            .iter()
            .filter(|c| c.sequence > anchor)
            .map(|c| c.event.resource_id.as_str())
            .collect();
        assert_eq!(
            later,
            ["after"],
            "the anchor did not separate the burst from the grains",
        );
    }

    #[test]
    fn draining_does_not_reset_the_sequence() {
        // Sequences must keep increasing across drains, or a connection
        // anchored before a drain would start receiving events it has seen.
        let mut queue = CommitQueue::new();
        queue.push(event("a"));
        let anchor = queue.latest();
        queue.drain();
        let after = queue.push(event("b"));

        assert!(
            after > anchor,
            "a drain reset the sequence; an anchored connection would replay",
        );
    }

    #[test]
    fn the_queue_does_not_coalesce() {
        // Deliberately. Merging an add and a remove for one resource into
        // nothing would be an observable divergence from Python, which emits
        // both. Coalescing is per-subscription and happens after
        // classification.
        let mut queue = CommitQueue::new();
        queue.push(event("same"));
        queue.push(event("same"));
        queue.push(event("same"));
        assert_eq!(queue.depth(), 3, "the queue coalesced");
    }

    #[test]
    fn the_high_water_mark_records_the_backlog_that_was_reached() {
        // Depth at an instant is nearly meaningless -- the matcher usually
        // empties the queue between samples. The peak is what distinguishes
        // keeping up from falling behind.
        let mut queue = CommitQueue::new();
        for id in ["a", "b", "c"] {
            queue.push(event(id));
        }
        queue.drain();

        assert_eq!(queue.depth(), 0);
        assert_eq!(
            queue.high_water(),
            3,
            "the peak backlog was lost when the queue drained",
        );

        queue.reset_high_water();
        assert_eq!(queue.high_water(), 0);
    }
}
