# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The replicated log: in memory, with a truncated prefix.

No write-ahead log and no fsync. Durability comes from the entry existing in
memory on a quorum of members, which is the trade this package is built on --
see ``nmos/raft/__init__.py`` for why IS-04 state can afford it and
``persist.py`` for the one piece that cannot.

Indices are 1-based and absolute
--------------------------------
Entry 1 is the first entry the cluster ever appended, and an index never means
anything else for the life of the cluster. Compaction removes entries from the
front, so ``first_index`` rises over time and a request for an index below it
raises :class:`RaftLogCompacted` rather than returning the wrong entry -- that
distinction is the whole reason the exception has its own class.

``snapshot_index`` is the index of the last entry the snapshot *includes*, so
``first_index == snapshot_index + 1`` always, and an empty log with a snapshot
at 100 reports ``first_index == 101`` and ``last_index == 100``. Reading that
as "first is greater than last, therefore corrupt" is the obvious mistake; it
simply means everything retained has been compacted away.

Decoded once
------------
An entry carries both its wire bytes and its decoded value. The bytes are what
replication sends; the value is what apply uses. Decoding on append rather than
on apply means a malformed entry is rejected while there is still someone to
tell, and means the apply step -- which runs synchronously, holds up the event
loop, and must not fail halfway -- does no parsing at all.

Generic over what it carries
----------------------------
The log has no idea what a registry operation is, and should not: it is a
sequence with consensus rules attached. Keeping it parameterised makes it
testable on its own terms, which for the piece of the system most likely to be
subtly wrong is worth more than the convenience of a concrete type.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Sequence, TypeVar

from nmos.raft.errors import RaftInvariantViolated, RaftLogCompacted

T = TypeVar("T")


@dataclass(frozen=True)
class Entry(Generic[T]):
    """One log entry: where it sits, what it says, and what it means."""

    term: int
    index: int
    payload: bytes
    value: T


class RaftLog(Generic[T]):
    """An append-only sequence with Raft's conflict and compaction rules.

    Args:
        snapshot_index: Index of the last entry covered by a snapshot, or 0 for
            a log that starts from nothing.
        snapshot_term: That entry's term. Needed because a follower's very
            first ``AppendEntries`` after an install compares against it.
    """

    __slots__ = ("_entries", "_snapshot_index", "_snapshot_term")

    def __init__(self, *, snapshot_index: int = 0, snapshot_term: int = 0) -> None:
        self._entries: list[Entry[T]] = []
        self._snapshot_index = snapshot_index
        self._snapshot_term = snapshot_term

    # -- geometry -------------------------------------------------------

    @property
    def snapshot_index(self) -> int:
        return self._snapshot_index

    @property
    def snapshot_term(self) -> int:
        return self._snapshot_term

    @property
    def first_index(self) -> int:
        """Lowest index still retained. One past the snapshot."""
        return self._snapshot_index + 1

    @property
    def last_index(self) -> int:
        return (
            self._entries[-1].index if self._entries else self._snapshot_index
        )

    @property
    def last_term(self) -> int:
        return self._entries[-1].term if self._entries else self._snapshot_term

    @property
    def entries_held(self) -> int:
        """How many entries are in memory, after compaction."""
        return len(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    # -- reading --------------------------------------------------------

    def _position(self, index: int) -> int:
        if index <= self._snapshot_index:
            raise RaftLogCompacted(
                f"index {index} is at or below the snapshot at "
                f"{self._snapshot_index}; this peer needs a snapshot, not "
                f"entries",
            )
        offset = index - self.first_index
        if offset < 0 or offset >= len(self._entries):
            raise IndexError(
                f"index {index} is not in the log "
                f"[{self.first_index}, {self.last_index}]",
            )
        return offset

    def get(self, index: int) -> Entry[T]:
        return self._entries[self._position(index)]

    def term_at(self, index: int) -> int:
        """The term of ``index``, including the snapshot boundary itself.

        The boundary is answerable because a follower whose log ends exactly at
        the snapshot point still has to match ``prev_log_term`` against it --
        refusing there would make a correctly caught-up follower look
        conflicted and send it straight back into a snapshot transfer.
        """
        if index == self._snapshot_index:
            return self._snapshot_term
        if index == 0 and self._snapshot_index == 0:
            # "Before the first entry" has term 0 -- but only while nothing has
            # been compacted. Once the snapshot has moved past it, index 0 is
            # below the boundary like any other discarded index, and a peer
            # asking from there needs a snapshot rather than an answer. Saying
            # 0 here would let a leader believe it could still replicate to
            # that peer, and the failure surfaces much later as a slice that
            # cannot be taken.
            return 0
        return self.get(index).term

    def slice(self, start: int, limit: int) -> tuple[Entry[T], ...]:
        """Up to ``limit`` entries from ``start``, clamped to what exists.

        Returns empty rather than raising when ``start`` is past the end: a
        leader asking for entries a follower already has is the steady state,
        not an error.
        """
        if limit <= 0 or start > self.last_index:
            return ()
        begin = self._position(start)
        return tuple(self._entries[begin:begin + limit])

    # -- appending ------------------------------------------------------

    def append(self, term: int, entries: Sequence[tuple[bytes, T]]) -> tuple[int, int]:
        """Append new entries as leader. Returns ``(first_index, last_index)``.

        Raises:
            ValueError: ``entries`` is empty. A caller asking to append nothing
                has a bug -- almost always an empty batch that should have been
                filtered before it reached consensus -- and returning a
                meaningless index range would hide it.
        """
        if not entries:
            raise ValueError("append() needs at least one entry")
        first = self.last_index + 1
        for offset, (payload, value) in enumerate(entries):
            self._entries.append(
                Entry(term=term, index=first + offset, payload=payload,
                      value=value),
            )
        return first, self.last_index

    def append_replicated(
        self, entries: Sequence[Entry[T]], *, committed: int,
    ) -> None:
        """Append entries received from the leader, resolving conflicts.

        Implements the rule that makes replication converge: where an existing
        entry disagrees with the leader about the term at an index, that entry
        and **everything after it** is discarded. Entries that already match
        are left alone rather than rewritten, so a duplicated ``AppendEntries``
        -- which retries make ordinary -- is idempotent.

        ``committed`` is the caller's commit index, and it is a floor rather
        than a hint: a conflict at or below it would discard an entry this
        member has already committed, which Log Matching says cannot happen.
        Asserted rather than assumed, because the cost is one comparison and
        the alternative is discovering it as a silently wrong state machine.
        ``go.etcd.io/raft`` asserts the same thing in ``maybeAppend``
        (``log.go:120``) and panics.
        """
        for entry in entries:
            if entry.index <= self._snapshot_index:
                # Already covered by the snapshot; nothing to do and nothing
                # to check. The leader is simply further back than we are.
                continue
            if entry.index <= self.last_index:
                existing = self.get(entry.index)
                if existing.term == entry.term:
                    continue
                if entry.index <= committed:
                    raise RaftInvariantViolated(
                        f"entry {entry.index} arrived as term {entry.term} "
                        f"but is held here as term {existing.term}, and index "
                        f"{committed} is committed -- accepting it would "
                        f"discard committed state",
                    )
                self.truncate_suffix(entry.index)
            if entry.index != self.last_index + 1:
                raise ValueError(
                    f"entry {entry.index} does not follow {self.last_index}; "
                    f"replication must be contiguous",
                )
            self._entries.append(entry)

    def truncate_suffix(self, from_index: int) -> None:
        """Discard ``from_index`` and everything after it.

        Only ever called on entries that were never committed -- a committed
        entry is on a quorum, and no leader can be elected that lacks it.
        """
        if from_index <= self._snapshot_index:
            raise RaftLogCompacted(
                f"cannot truncate from {from_index}: it is at or below the "
                f"snapshot at {self._snapshot_index}, which would discard "
                f"committed state",
            )
        if from_index > self.last_index:
            return
        del self._entries[self._position(from_index):]

    # -- compaction -----------------------------------------------------

    def discard_through(self, index: int, term: int) -> int:
        """Compact away everything up to and including ``index``.

        Returns how many entries were freed. Idempotent, and a no-op for an
        index at or below the current snapshot, so a caller that recomputes the
        compaction point on every apply pass costs nothing when nothing moved.

        Raises:
            ValueError: ``index`` is beyond the log. Compacting past what has
                been applied would discard state no snapshot covers.
        """
        if index > self.last_index:
            raise ValueError(
                f"cannot compact through {index}: the log ends at "
                f"{self.last_index}",
            )
        if index <= self._snapshot_index:
            return 0
        freed = self._position(index) + 1
        del self._entries[:freed]
        self._snapshot_index = index
        self._snapshot_term = term
        return freed

    def reset_to_snapshot(self, index: int, term: int) -> None:
        """Replace the whole log with a snapshot boundary.

        What a follower does after installing a snapshot: everything it held is
        either included in the snapshot or was never committed, so there is
        nothing worth keeping and the entries it does hold may conflict with
        the leader's.
        """
        self._entries.clear()
        self._snapshot_index = index
        self._snapshot_term = term

    # -- conflict resolution --------------------------------------------

    def matches(self, index: int, term: int) -> bool:
        """Whether this log has ``index`` at ``term`` -- the AppendEntries check."""
        if index == 0:
            # Only an uncompacted log agrees with an empty prefix. With a
            # snapshot in place this member holds state the leader's claim of
            # "you have nothing" contradicts.
            return self._snapshot_index == 0
        if index < self._snapshot_index or index > self.last_index:
            return False
        if index == self._snapshot_index:
            return term == self._snapshot_term
        return self.get(index).term == term

    def find_conflict(self, index: int, term: int) -> tuple[int, int]:
        """Where to resume after a failed match: ``(index, term)`` to retry from.

        Raft's naive recovery walks back one index per round trip, which costs
        a round trip per entry when a follower is far behind. This returns the
        first index of the *conflicting term* instead, so the leader skips the
        whole run in one step -- the standard optimisation, and the difference
        between a rejoining member catching up in a few exchanges and in
        thousands.
        """
        if index > self.last_index:
            # We simply do not have it yet; resume from our end.
            return self.last_index + 1, self.last_term

        try:
            conflicting = self.get(index).term
        except RaftLogCompacted:
            return self.first_index, self._snapshot_term

        if conflicting == term:
            return index, term

        first = index
        while first > self.first_index:
            try:
                if self.get(first - 1).term != conflicting:
                    break
            except RaftLogCompacted:
                break
            first -= 1
        return first, conflicting

    def is_at_least_as_current_as(self, index: int, term: int) -> bool:
        """Raft's up-to-dateness test, from the voter's side.

        A candidate wins a vote only if its log is at least as current as the
        voter's: a later last term wins, and at equal terms the longer log
        wins. This is what normally stops a candidate missing a committed entry
        from being elected.

        It is also exactly the check a restarted member with an empty log
        cannot make meaningfully -- everything looks current to a log with
        nothing in it -- which is why ``persist.py``'s incarnation and the
        non-voting rejoin exist alongside it rather than instead of it.
        """
        if term != self.last_term:
            return term > self.last_term
        return index >= self.last_index
