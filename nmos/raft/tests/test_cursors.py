# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Cursor allocation under concurrency and clock skew.

The failure this guards against is quiet and client-visible: two resources of
one type sharing a cursor, or a cursor arriving below one already published,
makes a client paging with ``paging.since`` skip a record. Nothing errors. The
registry simply never returns a resource that is sitting in it.

So these tests are about the three properties that prevent it -- uniqueness
across members, uniqueness across one member's restarts, and a sequence that
never goes backwards -- rather than about the arithmetic that happens to
implement them.
"""

from __future__ import annotations

import pytest

from nmos.raft.cursors import (
    MAX_OWNERS,
    OWNER_BITS,
    RESERVATION_WINDOW_SECONDS,
    CursorAllocator,
    owner_of,
)
from nmos.registry.types import ResourceType, TaiCursor

SENDER = ResourceType.SENDER
RECEIVER = ResourceType.RECEIVER


class TestLanes:
    def test_every_cursor_carries_its_allocator(self) -> None:
        for owner in range(MAX_OWNERS):
            allocator = CursorAllocator(owner)
            assert owner_of(allocator.allocate(SENDER)) == owner

    def test_an_owner_index_that_does_not_fit_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at most"):
            CursorAllocator(MAX_OWNERS)

    def test_three_bits_covers_the_largest_permitted_cluster(self) -> None:
        """1, 3 or 5 members, so 8 lanes is headroom rather than a limit."""
        assert OWNER_BITS == 3
        assert MAX_OWNERS >= 5


class TestUniqueness:
    def test_members_allocating_together_never_collide(self) -> None:
        """The property owner bits exist for.

        Two members cannot be made to produce the same cursor, whatever their
        clocks say, because they never write into the same lane.
        """
        allocators = [CursorAllocator(index) for index in range(5)]
        seen: set[TaiCursor] = set()
        for _round in range(200):
            for allocator in allocators:
                cursor = allocator.allocate(SENDER)
                assert cursor not in seen
                seen.add(cursor)

    def test_repeated_allocation_from_one_member_is_strictly_increasing(
        self,
    ) -> None:
        """The clock has coarser resolution than the allocation rate."""
        allocator = CursorAllocator(1)
        previous = allocator.allocate(SENDER)
        for _ in range(1000):
            current = allocator.allocate(SENDER)
            assert current > previous
            previous = current

    def test_types_are_independent(self) -> None:
        """Uniqueness is per type, so one type's mark must not drag another.

        Pushed via ``observe`` rather than by allocating in a loop: the clock
        advances faster than the lane stride, so a busy type simply tracks real
        time and the two marks would stay together for reasons that have
        nothing to do with independence.
        """
        allocator = CursorAllocator(2)
        far_ahead = TaiCursor(TaiCursor.now().seconds + 3600, 0)
        allocator.observe(SENDER, far_ahead)

        assert allocator.allocate(SENDER) > far_ahead
        # RECEIVER never heard about it, so it is still near real time.
        assert allocator.allocate(RECEIVER) < far_ahead


class TestMonotonicityUnderSkew:
    def test_observing_a_higher_cursor_pushes_the_next_allocation_above_it(
        self,
    ) -> None:
        """A member whose clock lags must not allocate into the past.

        Without this a client mid-page would skip the record: it asked for
        everything after cursor X, and the registry minted the new resource
        below X.
        """
        allocator = CursorAllocator(0)
        ahead = TaiCursor(allocator.allocate(SENDER).seconds + 60, 500)
        allocator.observe(SENDER, ahead)

        following = allocator.allocate(SENDER)
        assert following > ahead
        assert owner_of(following) == 0

    def test_observing_a_lower_cursor_changes_nothing(self) -> None:
        allocator = CursorAllocator(0)
        current = allocator.allocate(SENDER)
        allocator.observe(SENDER, TaiCursor(1, 1))
        assert allocator.allocate(SENDER) > current

    def test_a_cluster_under_skew_produces_one_increasing_sequence(
        self,
    ) -> None:
        """The end-to-end property, simulated.

        Every member allocates, every member observes every allocation through
        the log, and the order the cluster publishes never goes backwards --
        which is exactly what a paging client walks.
        """
        allocators = [CursorAllocator(index) for index in range(3)]
        published: list[TaiCursor] = []

        # Member 2 is 30 s ahead; member 1 is 10 s behind. Simulated by
        # observing an artificial cursor rather than moving the clock.
        allocators[2].observe(SENDER, TaiCursor(TaiCursor.now().seconds + 30, 0))

        for round_index in range(100):
            allocator = allocators[round_index % 3]
            cursor = allocator.allocate(SENDER)
            # The log delivers it to everyone, including the allocator.
            for peer in allocators:
                peer.observe(SENDER, cursor)
            published.append(cursor)

        assert published == sorted(published)
        assert len(set(published)) == len(published)

    def test_the_sequence_survives_a_member_that_never_hears_the_others(
        self,
    ) -> None:
        """Between members, uniqueness must not depend on observation.

        A partitioned member keeps allocating. Its cursors may interleave with
        the majority's once it rejoins, but they must never *equal* one --
        that is the difference between a client seeing records out of order
        and a client never seeing one at all.

        Between two incarnations of *one* member it is not observation but the
        reservation that keeps them apart; see ``TestRestarts``.
        """
        connected = CursorAllocator(0)
        isolated = CursorAllocator(1)

        theirs = [connected.allocate(SENDER) for _ in range(50)]
        ours = [isolated.allocate(SENDER) for _ in range(50)]
        assert not set(theirs) & set(ours)


class TestLaneArithmetic:
    def test_advancing_rolls_over_the_second_boundary(self) -> None:
        allocator = CursorAllocator(5)
        # Dated ahead of the real clock, or the clock -- not the mark -- is
        # what the next allocation would follow, and the rollover never runs.
        edge = TaiCursor(TaiCursor.now().seconds + 3600, 999_999_999)
        allocator.observe(SENDER, edge)

        following = allocator.allocate(SENDER)
        assert following.seconds == edge.seconds + 1
        assert owner_of(following) == 5

    def test_advancing_past_a_cursor_in_our_own_lane(self) -> None:
        """Must move, not return the same value."""
        allocator = CursorAllocator(3)
        mine = allocator.allocate(SENDER)
        allocator.observe(SENDER, mine)
        assert allocator.allocate(SENDER) > mine

    def test_the_stride_is_one_lane_width(self) -> None:
        """8 ns of granularity, against a field whose real resolution is a
        Python clock read."""
        allocator = CursorAllocator(0)
        mark = TaiCursor(TaiCursor.now().seconds + 3600, 0)
        allocator.observe(SENDER, mark)
        assert allocator.allocate(SENDER) == TaiCursor(
            mark.seconds, MAX_OWNERS,
        )


def _hand_out(allocator: CursorAllocator, resource_type: ResourceType) -> TaiCursor:
    """What ``RaftNode.allocate_cursor`` does, with the disk left out."""
    cursor = allocator.allocate(resource_type)
    needed = allocator.reservation_needed(cursor)
    if needed is not None:
        allocator.confirm_reservation(needed)
    return cursor


class TestRestarts:
    """One member's cursors stay unique across its own restarts.

    Owner bits separate members, not incarnations: a restarted member allocates
    in the lane it always did, so something else has to keep it from minting a
    cursor its predecessor minted. That is the reservation, carried across the
    restart in the state file and handed back through ``resume``.
    """

    def test_an_allocator_that_is_not_resumed_mints_its_predecessors_cursor(
        self,
    ) -> None:
        """The mechanism ``resume`` exists for, pinned so the fix stays falsifiable.

        Once the log is ahead of the clock -- the wall clock stepped back, or a
        peer's runs fast -- an allocation depends on nothing but the log prefix
        observed. Two incarnations that observed the same prefix therefore
        agree exactly. Were that ever to stop being true, the test below would
        pass whether or not ``resume`` did anything.
        """
        ahead = TaiCursor(TaiCursor.now().seconds + 60, 0)
        before, after = CursorAllocator(1), CursorAllocator(1)
        before.observe(SENDER, ahead)
        after.observe(SENDER, ahead)
        assert before.allocate(SENDER) == after.allocate(SENDER)

    def test_a_resumed_allocator_starts_above_everything_handed_out_before(
        self,
    ) -> None:
        ahead = TaiCursor(TaiCursor.now().seconds + 60, 0)
        before = CursorAllocator(1)
        before.observe(SENDER, ahead)
        handed_out = [_hand_out(before, SENDER) for _ in range(20)]

        after = CursorAllocator(1)
        after.resume(before.reservation)
        after.observe(SENDER, ahead)     # the same prefix, replayed
        assert after.allocate(SENDER) > max(handed_out)

    def test_the_bound_holds_for_every_type(self) -> None:
        """One bound for all types: it need only be above everything out."""
        before = CursorAllocator(0)
        before.observe(SENDER, TaiCursor(TaiCursor.now().seconds + 60, 0))
        _hand_out(before, SENDER)
        reservation = before.reservation
        assert reservation is not None

        after = CursorAllocator(0)
        after.resume(reservation)
        for resource_type in ResourceType:
            assert after.allocate(resource_type) > reservation

    def test_resuming_nothing_leaves_the_clock_in_charge(self) -> None:
        """A first start, or a state file written before reservations existed."""
        allocator = CursorAllocator(2)
        allocator.resume(None)
        assert allocator.reservation is None
        assert allocator.allocate(SENDER) < TaiCursor(
            TaiCursor.now().seconds + 1, 0,
        )


class TestReservations:
    """When a cursor needs a durable bound written first, and how far it reaches."""

    def test_the_first_cursor_needs_a_reservation_one_window_past_it(
        self,
    ) -> None:
        allocator = CursorAllocator(0)
        cursor = allocator.allocate(SENDER)
        assert allocator.reservation_needed(cursor) == TaiCursor(
            cursor.seconds + RESERVATION_WINDOW_SECONDS, cursor.nanoseconds,
        )

    def test_cursors_under_a_durable_reservation_need_no_write(self) -> None:
        """The disk is out of every allocation but about one per window."""
        allocator = CursorAllocator(0)
        _hand_out(allocator, SENDER)
        for _ in range(100):
            assert allocator.reservation_needed(allocator.allocate(SENDER)) is None

    def test_a_cursor_beyond_the_reservation_needs_a_new_one(self) -> None:
        allocator = CursorAllocator(0)
        _hand_out(allocator, SENDER)
        reserved = allocator.reservation
        assert reserved is not None
        allocator.observe(SENDER, TaiCursor(reserved.seconds + 5, 0))

        beyond = allocator.allocate(SENDER)
        needed = allocator.reservation_needed(beyond)
        assert needed is not None
        assert needed > beyond

    def test_a_reservation_never_moves_backwards(self) -> None:
        allocator = CursorAllocator(0)
        allocator.confirm_reservation(TaiCursor(100, 0))
        allocator.confirm_reservation(TaiCursor(50, 0))
        assert allocator.reservation == TaiCursor(100, 0)
