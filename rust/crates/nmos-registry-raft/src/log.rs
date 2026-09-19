// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The replicated log: append, conflict resolution, compaction.
//!
//! Port of `nmos/raft/log.py`.
//!
//! # Indexing
//!
//! Raft indexes from 1, and index 0 means "before the first entry". After
//! compaction the log no longer starts at 1, so every lookup goes through
//! [`RaftLog::position`], which is the single place the snapshot boundary is
//! reasoned about. Scattering that arithmetic would put an off-by-one in
//! whichever caller was written last.
//!
//! # What this module is not responsible for
//!
//! The **commit rule** lives in the node, not here: a log does not know what a
//! leader said about `leaderCommit`. That matters because defect 3.3 -- a
//! follower committing entries beyond the window an `AppendEntries` covered --
//! reads like a log bug and is not one. This module's job is to make the
//! arithmetic the node needs available and correct; `commit.rs` holds the rule
//! itself, with the reasoning next to it.

use crate::errors::RaftLogCompacted;

/// One log entry: where it sits, what it says, and what it means.
///
/// `payload` is the encoded form that replicates; `value` is the decoded form
/// the state machine applies. Both are carried so an entry is decoded once on
/// receipt rather than again on every apply.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Entry<T> {
    /// The term in which the leader created it.
    pub term: u64,
    /// Its position, from 1.
    pub index: u64,
    /// The bytes that replicate.
    pub payload: Vec<u8>,
    /// The decoded operation.
    pub value: T,
}

/// An append-only sequence with Raft's conflict and compaction rules.
#[derive(Debug)]
pub struct RaftLog<T> {
    entries: Vec<Entry<T>>,
    snapshot_index: u64,
    snapshot_term: u64,
}

impl<T> Default for RaftLog<T> {
    fn default() -> Self {
        Self::new()
    }
}

impl<T> RaftLog<T> {
    /// A log that starts from nothing.
    #[must_use]
    pub const fn new() -> Self {
        Self {
            entries: Vec::new(),
            snapshot_index: 0,
            snapshot_term: 0,
        }
    }

    /// A log resuming after a snapshot.
    ///
    /// `snapshot_term` is needed because a follower's very first
    /// `AppendEntries` after an install compares against it.
    #[must_use]
    pub const fn from_snapshot(snapshot_index: u64, snapshot_term: u64) -> Self {
        Self {
            entries: Vec::new(),
            snapshot_index,
            snapshot_term,
        }
    }

    // -- geometry ----------------------------------------------------------

    /// Index of the last entry covered by a snapshot.
    #[must_use]
    pub const fn snapshot_index(&self) -> u64 {
        self.snapshot_index
    }

    /// That entry's term.
    #[must_use]
    pub const fn snapshot_term(&self) -> u64 {
        self.snapshot_term
    }

    /// Lowest index still retained. One past the snapshot.
    #[must_use]
    pub const fn first_index(&self) -> u64 {
        self.snapshot_index.saturating_add(1)
    }

    /// The highest index this log holds, or the snapshot boundary if empty.
    #[must_use]
    pub fn last_index(&self) -> u64 {
        self.entries.last().map_or(self.snapshot_index, |e| e.index)
    }

    /// The term of the last entry, or the snapshot's if empty.
    #[must_use]
    pub fn last_term(&self) -> u64 {
        self.entries.last().map_or(self.snapshot_term, |e| e.term)
    }

    /// How many entries are in memory, after compaction.
    #[must_use]
    pub fn entries_held(&self) -> usize {
        self.entries.len()
    }

    /// Whether any entry is retained.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    // -- reading -----------------------------------------------------------

    /// Where `index` sits in the backing vector.
    ///
    /// The only place the snapshot boundary is turned into an offset.
    ///
    /// # Errors
    ///
    /// The index is at or below the snapshot, or past the end.
    fn position(&self, index: u64) -> Result<usize, RaftLogCompacted> {
        if index <= self.snapshot_index {
            return Err(RaftLogCompacted(format!(
                "index {index} is at or below the snapshot at {}; this peer \
                 needs a snapshot, not entries",
                self.snapshot_index,
            )));
        }
        let offset = index.saturating_sub(self.first_index());
        let offset = usize::try_from(offset).unwrap_or(usize::MAX);
        if offset >= self.entries.len() {
            return Err(RaftLogCompacted(format!(
                "index {index} is not in the log [{}, {}]",
                self.first_index(),
                self.last_index(),
            )));
        }
        Ok(offset)
    }

    /// The entry at `index`.
    ///
    /// # Errors
    ///
    /// The index is compacted away or past the end.
    pub fn get(&self, index: u64) -> Result<&Entry<T>, RaftLogCompacted> {
        let offset = self.position(index)?;
        self.entries
            .get(offset)
            .ok_or_else(|| RaftLogCompacted(format!("index {index} vanished")))
    }

    /// The term of `index`, including the snapshot boundary itself.
    ///
    /// The boundary is answerable because a follower whose log ends exactly at
    /// the snapshot point still has to match `prev_log_term` against it --
    /// refusing there would make a correctly caught-up follower look conflicted
    /// and send it straight back into a snapshot transfer.
    ///
    /// # Errors
    ///
    /// The index is below the boundary, or past the end.
    pub fn term_at(&self, index: u64) -> Result<u64, RaftLogCompacted> {
        if index == self.snapshot_index {
            return Ok(self.snapshot_term);
        }
        if index == 0 && self.snapshot_index == 0 {
            // "Before the first entry" has term 0 -- but only while nothing has
            // been compacted. Once the snapshot has moved past it, index 0 is
            // below the boundary like any other discarded index, and a peer
            // asking from there needs a snapshot rather than an answer. Saying
            // 0 here would let a leader believe it could still replicate to
            // that peer, and the failure surfaces much later as a slice that
            // cannot be taken.
            return Ok(0);
        }
        Ok(self.get(index)?.term)
    }

    /// Up to `limit` entries from `start`, clamped to what exists.
    ///
    /// Returns empty rather than failing when `start` is past the end: a leader
    /// asking for entries a follower already has is the steady state, not an
    /// error.
    ///
    /// # Errors
    ///
    /// `start` is at or below the snapshot -- that peer needs a snapshot.
    pub fn slice(&self, start: u64, limit: usize) -> Result<&[Entry<T>], RaftLogCompacted> {
        if limit == 0 || start > self.last_index() {
            return Ok(&[]);
        }
        let begin = self.position(start)?;
        let end = begin.saturating_add(limit).min(self.entries.len());
        Ok(self.entries.get(begin..end).unwrap_or_default())
    }

    // -- appending ---------------------------------------------------------

    /// Append new entries as leader, returning `(first_index, last_index)`.
    ///
    /// # Errors
    ///
    /// `entries` is empty. A caller asking to append nothing has a bug --
    /// almost always an empty batch that should have been filtered before it
    /// reached consensus -- and returning a meaningless index range would hide
    /// it.
    pub fn append(
        &mut self,
        term: u64,
        entries: Vec<(Vec<u8>, T)>,
    ) -> Result<(u64, u64), AppendError> {
        if entries.is_empty() {
            return Err(AppendError("append() needs at least one entry".to_owned()));
        }
        let first = self.last_index().saturating_add(1);
        for (offset, (payload, value)) in entries.into_iter().enumerate() {
            self.entries.push(Entry {
                term,
                index: first.saturating_add(offset as u64),
                payload,
                value,
            });
        }
        Ok((first, self.last_index()))
    }

    /// Append entries received from the leader, resolving conflicts.
    ///
    /// Implements the rule that makes replication converge: where an existing
    /// entry disagrees with the leader about the term at an index, that entry
    /// and **everything after it** is discarded. Entries that already match are
    /// left alone rather than rewritten, so a duplicated `AppendEntries` --
    /// which retries make ordinary -- is idempotent.
    ///
    /// # Errors
    ///
    /// The entries are not contiguous with what this log holds.
    pub fn append_replicated(&mut self, entries: Vec<Entry<T>>) -> Result<(), AppendError>
    where
        T: PartialEq,
    {
        for entry in entries {
            if entry.index <= self.snapshot_index {
                // Already covered by the snapshot; nothing to do and nothing to
                // check. The leader is simply further back than we are.
                continue;
            }
            if entry.index <= self.last_index() {
                let existing_term = self
                    .get(entry.index)
                    .map_err(|e| AppendError(e.to_string()))?
                    .term;
                if existing_term == entry.term {
                    continue;
                }
                self.truncate_suffix(entry.index)
                    .map_err(|e| AppendError(e.to_string()))?;
            }
            if entry.index != self.last_index().saturating_add(1) {
                return Err(AppendError(format!(
                    "entry {} does not follow {}; replication must be contiguous",
                    entry.index,
                    self.last_index(),
                )));
            }
            self.entries.push(entry);
        }
        Ok(())
    }

    /// Discard `from_index` and everything after it.
    ///
    /// Only ever called on entries that were never committed -- a committed
    /// entry is on a quorum, and no leader can be elected that lacks it.
    ///
    /// # Errors
    ///
    /// `from_index` is at or below the snapshot, which would discard committed
    /// state.
    pub fn truncate_suffix(&mut self, from_index: u64) -> Result<(), RaftLogCompacted> {
        if from_index <= self.snapshot_index {
            return Err(RaftLogCompacted(format!(
                "cannot truncate from {from_index}: it is at or below the \
                 snapshot at {}, which would discard committed state",
                self.snapshot_index,
            )));
        }
        if from_index > self.last_index() {
            return Ok(());
        }
        let offset = self.position(from_index)?;
        self.entries.truncate(offset);
        Ok(())
    }

    // -- compaction --------------------------------------------------------

    /// Compact away everything up to and including `index`.
    ///
    /// Returns how many entries were freed. Idempotent, and a no-op for an
    /// index at or below the current snapshot, so a caller that recomputes the
    /// compaction point on every apply pass costs nothing when nothing moved.
    ///
    /// # Errors
    ///
    /// `index` is beyond the log. Compacting past what has been applied would
    /// discard state no snapshot covers.
    pub fn discard_through(&mut self, index: u64, term: u64) -> Result<usize, RaftLogCompacted> {
        if index > self.last_index() {
            return Err(RaftLogCompacted(format!(
                "cannot compact through {index}: the log ends at {}",
                self.last_index(),
            )));
        }
        if index <= self.snapshot_index {
            return Ok(0);
        }
        let freed = self.position(index)?.saturating_add(1);
        self.entries.drain(..freed);
        self.snapshot_index = index;
        self.snapshot_term = term;
        Ok(freed)
    }

    /// Replace the whole log with a snapshot boundary.
    ///
    /// What a follower does after installing a snapshot: everything it held is
    /// either included in the snapshot or was never committed, so there is
    /// nothing worth keeping and the entries it does hold may conflict with the
    /// leader's.
    pub fn reset_to_snapshot(&mut self, index: u64, term: u64) {
        self.entries.clear();
        self.snapshot_index = index;
        self.snapshot_term = term;
    }

    // -- conflict resolution -----------------------------------------------

    /// Whether this log has `index` at `term` -- the `AppendEntries` check.
    #[must_use]
    pub fn matches(&self, index: u64, term: u64) -> bool {
        if index == 0 {
            // Only an uncompacted log agrees with an empty prefix. With a
            // snapshot in place this member holds state the leader's claim of
            // "you have nothing" contradicts.
            return self.snapshot_index == 0;
        }
        if index < self.snapshot_index || index > self.last_index() {
            return false;
        }
        if index == self.snapshot_index {
            return term == self.snapshot_term;
        }
        self.get(index).is_ok_and(|entry| entry.term == term)
    }

    /// Where to resume after a failed match: `(index, term)` to retry from.
    ///
    /// Raft's naive recovery walks back one index per round trip, which costs a
    /// round trip per entry when a follower is far behind. This returns the
    /// first index of the *conflicting term* instead, so the leader skips the
    /// whole run in one step -- the standard optimisation, and the difference
    /// between a rejoining member catching up in a few exchanges and in
    /// thousands.
    #[must_use]
    pub fn find_conflict(&self, index: u64, term: u64) -> (u64, u64) {
        if index > self.last_index() {
            // We simply do not have it yet; resume from our end.
            return (self.last_index().saturating_add(1), self.last_term());
        }

        let Ok(entry) = self.get(index) else {
            return (self.first_index(), self.snapshot_term);
        };
        let conflicting = entry.term;
        if conflicting == term {
            return (index, term);
        }

        let mut first = index;
        while first > self.first_index() {
            match self.get(first.saturating_sub(1)) {
                Ok(previous) if previous.term == conflicting => {
                    first = first.saturating_sub(1);
                }
                _ => break,
            }
        }
        (first, conflicting)
    }

    /// Raft's up-to-dateness test, from the voter's side (§5.4.1).
    ///
    /// A candidate wins a vote only if its log is at least as current as the
    /// voter's: a later last term wins, and at equal terms the longer log wins.
    /// This is what normally stops a candidate missing a committed entry from
    /// being elected.
    ///
    /// It is also exactly the check a restarted member with an empty log cannot
    /// make meaningfully -- everything looks current to a log with nothing in
    /// it -- which is why `persist`'s incarnation and the non-voting rejoin
    /// exist alongside it rather than instead of it.
    #[must_use]
    pub fn is_at_least_as_current_as(&self, index: u64, term: u64) -> bool {
        if term != self.last_term() {
            return term > self.last_term();
        }
        index >= self.last_index()
    }
}

/// An append that could not be made.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AppendError(pub String);

impl std::fmt::Display for AppendError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for AppendError {}
