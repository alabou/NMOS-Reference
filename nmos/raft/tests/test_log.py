# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The log's geometry, conflict rules and compaction.

Most of what can go wrong in a Raft implementation goes wrong here, quietly:
an off-by-one in the snapshot boundary, a conflict walk that stops one entry
early, a compaction that discards an entry the snapshot does not actually
cover. None of those raise at the time. They surface later as a follower that
never catches up, or as two members disagreeing about what is committed.

So these tests are about the boundaries rather than the happy path.
"""

from __future__ import annotations

import pytest

from nmos.raft.errors import RaftLogCompacted
from nmos.raft.log import Entry, RaftLog


def _log(*terms: int) -> RaftLog[str]:
    """A log whose entry at index i has the given term and value ``e<i>``."""
    log: RaftLog[str] = RaftLog()
    for term in terms:
        log.append(term, [(b"", f"e{log.last_index + 1}")])
    return log


class TestEmptyGeometry:
    def test_a_new_log_is_empty_at_index_zero(self) -> None:
        log: RaftLog[str] = RaftLog()
        assert log.first_index == 1
        assert log.last_index == 0
        assert log.last_term == 0
        assert log.entries_held == 0

    def test_a_log_that_starts_from_a_snapshot(self) -> None:
        """first > last is not corruption; it means everything is compacted."""
        log: RaftLog[str] = RaftLog(snapshot_index=100, snapshot_term=4)
        assert log.first_index == 101
        assert log.last_index == 100
        assert log.last_term == 4
        assert log.entries_held == 0


class TestAppending:
    def test_indices_are_one_based_and_contiguous(self) -> None:
        log = _log(1, 1, 2)
        assert [log.get(i).index for i in (1, 2, 3)] == [1, 2, 3]
        assert [log.get(i).term for i in (1, 2, 3)] == [1, 1, 2]

    def test_append_returns_the_range_it_wrote(self) -> None:
        log: RaftLog[str] = RaftLog()
        assert log.append(1, [(b"", "a"), (b"", "b")]) == (1, 2)
        assert log.append(1, [(b"", "c")]) == (3, 3)

    def test_appending_nothing_is_a_bug(self) -> None:
        """An empty batch should have been filtered before reaching consensus."""
        log: RaftLog[str] = RaftLog()
        with pytest.raises(ValueError, match="at least one entry"):
            log.append(1, [])

    def test_the_decoded_value_travels_with_the_bytes(self) -> None:
        """Apply must never parse: it runs synchronously and cannot fail halfway."""
        log: RaftLog[str] = RaftLog()
        log.append(1, [(b"wire-bytes", "decoded")])
        entry = log.get(1)
        assert entry.payload == b"wire-bytes"
        assert entry.value == "decoded"


class TestReading:
    def test_slice_is_clamped_to_what_exists(self) -> None:
        log = _log(1, 1, 1)
        assert len(log.slice(1, 10)) == 3
        assert len(log.slice(2, 1)) == 1

    def test_slice_past_the_end_is_empty_not_an_error(self) -> None:
        """A leader asking for entries a follower already has is steady state."""
        log = _log(1, 1)
        assert log.slice(3, 10) == ()
        assert log.slice(99, 10) == ()

    def test_a_zero_limit_returns_nothing(self) -> None:
        assert _log(1, 1).slice(1, 0) == ()

    def test_term_at_answers_the_snapshot_boundary(self) -> None:
        """Refusing here sends a caught-up follower back into a snapshot."""
        log = _log(1, 1, 2, 2)
        log.discard_through(2, 1)
        assert log.term_at(2) == 1
        assert log.term_at(3) == 2

    def test_term_at_zero_is_zero(self) -> None:
        assert RaftLog[str]().term_at(0) == 0

    def test_reading_below_the_snapshot_is_distinguishable(self) -> None:
        """Its own class, so replication cannot mistake it for a hiccup."""
        log = _log(1, 1, 1)
        log.discard_through(2, 1)
        with pytest.raises(RaftLogCompacted):
            log.get(1)

    def test_reading_past_the_end_is_an_index_error(self) -> None:
        """Distinct from compaction: this one is a caller bug, not a state."""
        with pytest.raises(IndexError):
            _log(1).get(5)


class TestReplication:
    def test_matching_entries_are_left_alone(self) -> None:
        """A duplicated AppendEntries is ordinary; it must be idempotent."""
        log = _log(1, 1, 1)
        log.append_replicated([
            Entry(term=1, index=2, payload=b"", value="e2"),
            Entry(term=1, index=3, payload=b"", value="e3"),
        ])
        assert log.last_index == 3

    def test_a_conflicting_entry_truncates_everything_after_it(self) -> None:
        log = _log(1, 1, 1, 1)
        log.append_replicated([
            Entry(term=2, index=3, payload=b"", value="new3"),
        ])
        assert log.last_index == 3
        assert log.get(3).term == 2
        assert log.get(3).value == "new3"

    def test_entries_below_the_snapshot_are_ignored(self) -> None:
        """The leader is simply further back than we are."""
        log = _log(1, 1, 1)
        log.discard_through(2, 1)
        log.append_replicated([
            Entry(term=1, index=1, payload=b"", value="stale"),
        ])
        assert log.first_index == 3

    def test_a_gap_is_refused(self) -> None:
        log = _log(1)
        with pytest.raises(ValueError, match="contiguous"):
            log.append_replicated([
                Entry(term=1, index=5, payload=b"", value="gap"),
            ])


class TestMatching:
    def test_index_zero_always_matches(self) -> None:
        """An empty log agrees with an empty prefix."""
        assert RaftLog[str]().matches(0, 0) is True

    def test_a_present_entry_matches_on_term(self) -> None:
        log = _log(1, 2)
        assert log.matches(2, 2) is True
        assert log.matches(2, 1) is False

    def test_an_absent_index_does_not_match(self) -> None:
        assert _log(1).matches(9, 1) is False

    def test_the_snapshot_boundary_matches_on_its_term(self) -> None:
        log = _log(1, 1, 3)
        log.discard_through(2, 1)
        assert log.matches(2, 1) is True
        assert log.matches(2, 9) is False


class TestConflictSearch:
    def test_a_matching_index_returns_itself(self) -> None:
        log = _log(1, 1, 2)
        assert log.find_conflict(3, 2) == (3, 2)

    def test_a_missing_index_resumes_from_our_end(self) -> None:
        log = _log(1, 1)
        assert log.find_conflict(10, 5) == (3, 1)

    def test_it_skips_the_whole_conflicting_term(self) -> None:
        """The optimisation that makes catch-up take exchanges, not thousands.

        Walking back one index per round trip costs a round trip per entry. A
        far-behind member rejoining a busy registry would take as many
        exchanges as the cluster had committed entries.
        """
        log = _log(1, 1, 1, 2, 2, 2, 2)
        index, term = log.find_conflict(7, 9)
        assert (index, term) == (4, 2)

    def test_it_stops_at_the_first_retained_entry(self) -> None:
        log = _log(2, 2, 2, 2)
        log.discard_through(2, 2)
        index, _term = log.find_conflict(4, 9)
        assert index == log.first_index


class TestCompaction:
    def test_discarding_frees_entries_and_moves_the_boundary(self) -> None:
        log = _log(1, 1, 1, 1, 1)
        assert log.discard_through(3, 1) == 3
        assert log.first_index == 4
        assert log.entries_held == 2
        assert log.last_index == 5

    def test_it_is_idempotent(self) -> None:
        """A caller recomputing the point on every apply pass costs nothing."""
        log = _log(1, 1, 1)
        assert log.discard_through(2, 1) == 2
        assert log.discard_through(2, 1) == 0
        assert log.discard_through(1, 1) == 0

    def test_compacting_past_the_log_is_refused(self) -> None:
        """It would discard state no snapshot covers."""
        log = _log(1, 1)
        with pytest.raises(ValueError, match="the log ends at"):
            log.discard_through(9, 1)

    def test_truncating_into_the_snapshot_is_refused(self) -> None:
        """Committed state is not a candidate for truncation, ever."""
        log = _log(1, 1, 1)
        log.discard_through(2, 1)
        with pytest.raises(RaftLogCompacted, match="committed state"):
            log.truncate_suffix(1)

    def test_reset_to_snapshot_clears_everything(self) -> None:
        log = _log(1, 1, 1)
        log.reset_to_snapshot(50, 7)
        assert log.entries_held == 0
        assert log.first_index == 51
        assert log.last_index == 50
        assert log.last_term == 7


class TestUpToDateness:
    def test_a_later_term_wins_regardless_of_length(self) -> None:
        log = _log(1, 1, 1, 1, 1)
        assert log.is_at_least_as_current_as(1, 2) is True

    def test_at_equal_terms_the_longer_log_wins(self) -> None:
        log = _log(1, 1, 1)
        assert log.is_at_least_as_current_as(3, 1) is True
        assert log.is_at_least_as_current_as(2, 1) is False

    def test_an_earlier_term_loses_however_long(self) -> None:
        log = _log(1, 2, 2)
        assert log.is_at_least_as_current_as(99, 1) is False

    def test_an_empty_log_considers_everyone_current(self) -> None:
        """Documented here because it is the hazard, not the feature.

        This is exactly why a restarted member must not vote until it has been
        caught up: with nothing in its log, this check -- Raft's entire defence
        against electing a leader that is missing committed entries -- returns
        True for every candidate that asks.
        """
        empty: RaftLog[str] = RaftLog()
        assert empty.is_at_least_as_current_as(0, 0) is True
        assert empty.is_at_least_as_current_as(9999, 1) is True
