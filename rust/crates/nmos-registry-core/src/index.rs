// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The cursor-ordered index a Query pages over.
//!
//! One of these exists per `(ResourceType, Order)`, holding every resource of
//! that type keyed by `(cursor, id)`. It is what lets a Query return a page
//! without sorting the collection first: the caller filters this stream, and a
//! filtered subsequence of a sorted sequence is still sorted.
//!
//! The id is in the key as a **tie-break**, not as data. Two resources sharing
//! an instant must still have a total order, or two cluster members that
//! received the same resources would page differently.
//!
//! # Divergence: a `BTreeSet`, not a lazily-sorted insertion-ordered map
//!
//! Python keeps `dict[id, None]` per index and relies on dicts preserving
//! insertion order plus cursors being allocated strictly increasing: "append on
//! create, move to end on update" then keeps it sorted in O(1) per mutation.
//! When that assumption breaks -- a distributed preload applies resources in
//! *key* order, not cursor order, and those cursors are not the registry's to
//! choose -- it sets a dirty flag and re-sorts lazily on the next read.
//!
//! That cannot port as written, and the reason is not style. The lazy re-sort
//! happens inside `iter_ordered`, which mutates the index under what every
//! caller treats as a read. In Rust that is a borrow error through `&self`, and
//! both tempting repairs are wrong: a `RefCell` is `!Sync` and so cannot live
//! in a store shared across threads, and a nested lock reintroduces exactly the
//! contention the outer lock was taken to avoid.
//!
//! A `BTreeSet<(TaiCursor, ResourceId)>` has the invariant by construction.
//! `_touch_order`, `_order_dirty` and the lazy re-sort are three mechanisms
//! maintaining something the container simply *is*, including the `(cursor, id)`
//! tie-break. Reads never sort, and a preload is no longer a special case.
//!
//! The cost is O(log n) per mutation where Python pays amortised O(1). At
//! registry write rates that is irrelevant, and the read path is strictly
//! better.
//!
//! # The porting bug this module is shaped to prevent
//!
//! A resource's cursor *changes* on update, so repositioning it means removing
//! the key it currently occupies and inserting a new one. Removing by the
//! **new** cursor removes nothing -- no such key exists -- and leaves the old
//! entry behind as a phantom: the resource then appears twice in every page,
//! at its old position and its new one.
//!
//! It is a one-character mistake and it is invisible until a client pages. So
//! [`CursorIndex::reposition`] takes the old cursor as an argument rather than
//! discovering it, and is the only way to move an entry.

use std::collections::BTreeSet;
use std::ops::Bound;

use crate::cursor::TaiCursor;
use crate::resource::ResourceId;

/// Every resource of one type, ordered by one of its cursors.
#[derive(Debug, Default, Clone)]
pub struct CursorIndex {
    entries: BTreeSet<(TaiCursor, ResourceId)>,
}

impl CursorIndex {
    /// An empty index.
    #[must_use]
    pub fn new() -> Self {
        Self {
            entries: BTreeSet::new(),
        }
    }

    /// How many entries this index holds.
    ///
    /// Every resource of the type appears exactly once, extant or not.
    /// Asserting on this is how a phantom is caught.
    #[must_use]
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// Whether this index holds nothing.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// Add a resource at its cursor.
    ///
    /// Returns whether it was newly added. A repeat with the same key is a
    /// no-op, which is what makes a replayed backend operation idempotent.
    pub fn insert(&mut self, cursor: TaiCursor, id: ResourceId) -> bool {
        self.entries.insert((cursor, id))
    }

    /// Remove a resource from its cursor.
    ///
    /// Returns whether anything was there. `false` means the caller passed a
    /// cursor the entry is not filed under, which is the phantom bug arriving
    /// -- so callers that know the entry exists should check it.
    pub fn remove(&mut self, cursor: TaiCursor, id: &str) -> bool {
        self.entries.remove(&(cursor, id.to_owned()))
    }

    /// Move a resource from one cursor to another.
    ///
    /// The old cursor is a parameter, not something looked up, because looking
    /// it up is where the phantom comes from: by the time a store applies an
    /// update the record already carries the new cursor, so "remove where this
    /// resource is" reads the new one and removes nothing.
    ///
    /// Returns whether the old entry was found. A caller applying an update to
    /// a resource it just read from the store should treat `false` as a bug in
    /// itself rather than as a condition to handle.
    pub fn reposition(&mut self, from: TaiCursor, to: TaiCursor, id: &str) -> bool {
        let removed = self.entries.remove(&(from, id.to_owned()));
        self.entries.insert((to, id.to_owned()));
        removed
    }

    /// The highest cursor in the index, ignoring any filter.
    ///
    /// This is what `X-Paging-Until` reports when the client supplied no
    /// cursors: `APIs - Query Parameters.md` Edge Cases 3 and 4 both return a
    /// page whose newest record is older than the reported `until`, or no
    /// records at all, and still report the collection's own maximum. That is
    /// what makes the `next` cursor usable as a "watch from now on" bookmark
    /// even when the current filter matches nothing.
    #[must_use]
    pub fn max_cursor(&self) -> Option<TaiCursor> {
        self.entries.last().map(|(cursor, _)| *cursor)
    }

    /// The lowest cursor in the index, ignoring any filter.
    #[must_use]
    pub fn min_cursor(&self) -> Option<TaiCursor> {
        self.entries.first().map(|(cursor, _)| *cursor)
    }

    /// Every entry, ascending.
    pub fn iter(&self) -> impl DoubleEndedIterator<Item = (TaiCursor, &str)> {
        self.entries
            .iter()
            .map(|(cursor, id)| (*cursor, id.as_str()))
    }

    /// Every entry in the half-open window `(since, until]`, ascending.
    ///
    /// The bounds are the protocol's: `paging.since` is non-inclusive and
    /// `paging.until` is inclusive (`QueryAPI.raml:29,33`).
    ///
    /// # How the bounds are expressed without a maximum id
    ///
    /// The key is `(cursor, id)`, so a bound on the cursor alone needs the
    /// extreme id to pair with it -- and `String` has a minimum (`""`) but no
    /// maximum. Rather than invent a sentinel, this uses the fact that
    /// **cursors are discrete**: nanosecond granularity means `since.next()` is
    /// genuinely the next representable cursor, so
    ///
    /// * `c > since`  is  `(since.next(), "")` inclusive, and
    /// * `c <= until` is  `(until.next(), "")` exclusive.
    ///
    /// Both are exact rather than approximate, and neither depends on any
    /// property of the ids.
    pub fn range(
        &self,
        since: Option<TaiCursor>,
        until: Option<TaiCursor>,
    ) -> impl DoubleEndedIterator<Item = (TaiCursor, &str)> {
        let low = match since {
            Some(cursor) => Bound::Included((cursor.next(), String::new())),
            None => Bound::Unbounded,
        };
        let high = match until {
            Some(cursor) => Bound::Excluded((cursor.next(), String::new())),
            None => Bound::Unbounded,
        };
        self.entries
            .range((low, high))
            .map(|(cursor, id)| (*cursor, id.as_str()))
    }

    /// Whether a particular entry is present at a particular cursor.
    #[must_use]
    pub fn contains(&self, cursor: TaiCursor, id: &str) -> bool {
        self.entries.contains(&(cursor, id.to_owned()))
    }

    /// Every id in the index, in no useful order. For consistency checks.
    #[must_use]
    pub fn ids(&self) -> Vec<&str> {
        self.entries.iter().map(|(_, id)| id.as_str()).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cursor(seconds: u64) -> TaiCursor {
        TaiCursor::new(seconds, 0)
    }

    fn ids_in_order(index: &CursorIndex) -> Vec<&str> {
        index.iter().map(|(_, id)| id).collect()
    }

    #[test]
    fn entries_come_out_in_cursor_order_whatever_order_they_went_in() {
        // The property the Python maintains by hand with "append on create,
        // move to end on update" plus a dirty flag, and that a BTreeSet simply
        // has. Inserting descending is what a distributed preload does.
        let mut index = CursorIndex::new();
        for (seconds, id) in [(5, "e"), (1, "a"), (4, "d"), (2, "b"), (3, "c")] {
            index.insert(cursor(seconds), id.to_owned());
        }
        assert_eq!(ids_in_order(&index), ["a", "b", "c", "d", "e"]);
    }

    #[test]
    fn a_shared_cursor_breaks_the_tie_on_id() {
        // Not cosmetic: two cluster members that received these in different
        // orders must page identically, and the id is what makes that true.
        let mut forwards = CursorIndex::new();
        let mut backwards = CursorIndex::new();
        for id in ["c", "a", "b"] {
            forwards.insert(cursor(7), id.to_owned());
        }
        for id in ["b", "a", "c"] {
            backwards.insert(cursor(7), id.to_owned());
        }
        assert_eq!(ids_in_order(&forwards), ["a", "b", "c"]);
        assert_eq!(ids_in_order(&forwards), ids_in_order(&backwards));
    }

    #[test]
    fn repositioning_twice_leaves_exactly_one_entry() {
        // THE porting bug. Removing by the *new* cursor removes nothing and
        // leaves the old entry behind, so the resource appears at both
        // positions -- invisible until a client pages.
        let mut index = CursorIndex::new();
        index.insert(cursor(1), "a".to_owned());
        assert_eq!(index.len(), 1);

        assert!(index.reposition(cursor(1), cursor(2), "a"));
        assert_eq!(index.len(), 1, "a phantom was left at the old cursor");

        assert!(index.reposition(cursor(2), cursor(3), "a"));
        assert_eq!(index.len(), 1, "a phantom was left at the old cursor");

        assert_eq!(ids_in_order(&index), ["a"]);
        assert!(index.contains(cursor(3), "a"));
        assert!(!index.contains(cursor(1), "a"));
        assert!(!index.contains(cursor(2), "a"));
    }

    #[test]
    fn repositioning_from_the_wrong_cursor_reports_it() {
        // What the return value is for. A store that reads the record's
        // *current* cursor and passes it as `from` gets `false` here, which is
        // the bug announcing itself instead of corrupting the index quietly.
        let mut index = CursorIndex::new();
        index.insert(cursor(1), "a".to_owned());

        assert!(
            !index.reposition(cursor(9), cursor(2), "a"),
            "a reposition from a cursor nothing is filed under claimed success",
        );
        // And the damage it would have done is still visible, so a caller that
        // ignores the return value is not silently fine.
        assert_eq!(index.len(), 2);
    }

    #[test]
    fn the_window_is_since_exclusive_and_until_inclusive() {
        // `QueryAPI.raml:29,33`. The asymmetry is the protocol's, not a
        // convenience, and getting it backwards duplicates or skips a record
        // at every page boundary.
        let mut index = CursorIndex::new();
        for seconds in 1..=5 {
            index.insert(cursor(seconds), format!("r{seconds}"));
        }

        let window: Vec<&str> = index
            .range(Some(cursor(2)), Some(cursor(4)))
            .map(|(_, id)| id)
            .collect();
        assert_eq!(window, ["r3", "r4"], "expected (2, 4]");
    }

    #[test]
    fn the_window_bounds_hold_when_ids_are_extreme() {
        // The bounds are expressed as `(cursor.next(), "")`, which is exact
        // only because cursors are discrete. An empty id is the smallest
        // possible and is the one that would slip through a sloppy bound.
        let mut index = CursorIndex::new();
        index.insert(cursor(2), String::new());
        index.insert(cursor(3), String::new());
        index.insert(cursor(3), "zzz".repeat(50));
        index.insert(cursor(4), String::new());

        let window: Vec<TaiCursor> = index
            .range(Some(cursor(2)), Some(cursor(3)))
            .map(|(c, _)| c)
            .collect();
        assert_eq!(
            window,
            [cursor(3), cursor(3)],
            "a boundary entry with an extreme id fell outside the window",
        );
    }

    #[test]
    fn a_window_bound_at_nanosecond_granularity_is_exact() {
        let mut index = CursorIndex::new();
        let a = TaiCursor::new(1, 0);
        let b = TaiCursor::new(1, 1);
        let c = TaiCursor::new(1, 2);
        for (cur, id) in [(a, "a"), (b, "b"), (c, "c")] {
            index.insert(cur, id.to_owned());
        }
        let window: Vec<&str> = index.range(Some(a), Some(b)).map(|(_, id)| id).collect();
        assert_eq!(window, ["b"], "expected exactly the one nanosecond after a");
    }

    #[test]
    fn an_open_bound_means_unbounded_not_zero() {
        let mut index = CursorIndex::new();
        for seconds in 1..=3 {
            index.insert(cursor(seconds), format!("r{seconds}"));
        }
        let all: Vec<&str> = index.range(None, None).map(|(_, id)| id).collect();
        assert_eq!(all, ["r1", "r2", "r3"]);

        let from_two: Vec<&str> = index
            .range(Some(cursor(2)), None)
            .map(|(_, id)| id)
            .collect();
        assert_eq!(from_two, ["r3"]);

        let to_two: Vec<&str> = index
            .range(None, Some(cursor(2)))
            .map(|(_, id)| id)
            .collect();
        assert_eq!(to_two, ["r1", "r2"]);
    }

    #[test]
    fn the_maximum_is_the_collections_own_not_a_pages() {
        // Edge Cases 3 and 4: `X-Paging-Until` reports the collection's newest
        // cursor even when the page is empty, which is what makes the `next`
        // link a usable bookmark.
        let mut index = CursorIndex::new();
        assert_eq!(index.max_cursor(), None);
        for seconds in [3, 1, 2] {
            index.insert(cursor(seconds), format!("r{seconds}"));
        }
        assert_eq!(index.max_cursor(), Some(cursor(3)));
        assert_eq!(index.min_cursor(), Some(cursor(1)));

        // A window that selects nothing does not change what the collection's
        // maximum is.
        assert_eq!(index.range(Some(cursor(9)), None).count(), 0);
        assert_eq!(index.max_cursor(), Some(cursor(3)));
    }

    #[test]
    fn a_page_can_be_taken_from_either_end() {
        // `since` given pages forwards from the bottom; `since` absent pages
        // backwards from the top. Both come off the same range.
        let mut index = CursorIndex::new();
        for seconds in 1..=6 {
            index.insert(cursor(seconds), format!("r{seconds}"));
        }

        let oldest_two: Vec<&str> = index
            .range(Some(cursor(1)), None)
            .take(2)
            .map(|(_, id)| id)
            .collect();
        assert_eq!(oldest_two, ["r2", "r3"]);

        let newest_two: Vec<&str> = index
            .range(None, Some(cursor(6)))
            .rev()
            .take(2)
            .map(|(_, id)| id)
            .collect();
        assert_eq!(newest_two, ["r6", "r5"]);
    }

    #[test]
    fn inserting_the_same_key_twice_is_idempotent() {
        // What makes a replayed backend operation safe.
        let mut index = CursorIndex::new();
        assert!(index.insert(cursor(1), "a".to_owned()));
        assert!(!index.insert(cursor(1), "a".to_owned()));
        assert_eq!(index.len(), 1);
    }

    #[test]
    fn removing_reports_whether_anything_was_there() {
        let mut index = CursorIndex::new();
        index.insert(cursor(1), "a".to_owned());
        assert!(!index.remove(cursor(2), "a"), "removed at the wrong cursor");
        assert!(!index.remove(cursor(1), "b"), "removed the wrong id");
        assert!(index.remove(cursor(1), "a"));
        assert!(index.is_empty());
    }
}
