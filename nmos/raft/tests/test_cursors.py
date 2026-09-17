# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Cursor allocation under concurrency and clock skew.

The failure this guards against is quiet and client-visible: two resources of
one type sharing a cursor, or a cursor arriving below one already published,
makes a client paging with ``paging.since`` skip a record. Nothing errors. The
registry simply never returns a resource that is sitting in it.

So these tests are about the two properties that prevent it -- uniqueness
across members, and a sequence that never goes backwards -- rather than about
the arithmetic that happens to implement them.
"""

from __future__ import annotations

import pytest

from nmos.raft.cursors import (
    MAX_OWNERS,
    OWNER_BITS,
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
        """Uniqueness must not depend on observation, only ordering does.

        A partitioned member keeps allocating. Its cursors may interleave with
        the majority's once it rejoins, but they must never *equal* one --
        that is the difference between a client seeing records out of order
        and a client never seeing one at all.
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
