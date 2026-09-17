# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Paging cursors that stay unique and ordered without anyone coordinating.

The problem
-----------
``APIs - Query Parameters.md:17`` requires that no two resources of the same
type share a creation or update timestamp, because the timestamp *is* the
paging cursor: a client walking a collection with ``paging.since=<cursor>``
resumes strictly after it, so two records on the same instant means one of them
is never returned.

Standalone gets this for free -- one allocator, one high-water mark, fall
forward a nanosecond on collision (``store._next_cursor``). Distributed does
not. The etcd backend solved it by making the cursor part of the value it
writes, so whichever member commits decides, and every member applies the same
number. That works because *something* serialises the write.

Here, several members allocate cursors concurrently and only find out about
each other's afterwards. So the allocation itself has to be collision-proof.

Two mechanisms, both required
-----------------------------
**Owner bits.** The low bits of the nanosecond field carry the allocating
member's index, so two members physically cannot produce the same value. A
member only ever allocates within its own lane. Three bits covers the maximum
cluster size of five, and costs 8 ns of granularity -- against a field whose
real resolution is a Python clock read, that is free.

**A hybrid logical clock.** Owner bits stop collisions but not *inversions*: a
member whose clock runs slow would allocate a cursor below one the cluster has
already published, and a client mid-page would skip the record. So every cursor
that arrives through the log is observed, the high-water mark rises to it, and
the next local allocation is pushed above it. Real time is a lower bound on the
cursor, never an upper one.

The consequence worth stating: a cursor is no longer exactly a wall-clock
instant. It is a monotonic identifier that starts from one, and under clock
skew it runs ahead of local time until local time catches up. The spec uses it
as an opaque ordered token -- ``paging.since``/``paging.until`` compare it, they
do not interpret it -- so this is within what the format promises, but a reader
expecting the nanoseconds to be a measurement will be surprised, and that is
what this paragraph is for.
"""

from __future__ import annotations

from nmos.registry.types import ResourceType, TaiCursor

# Enough lanes for the largest permitted cluster (5), with headroom. Fixed
# rather than derived from the member count so that a cluster resized from 3
# to 5 does not renumber the lanes and start colliding with cursors already
# published under the old numbering.
OWNER_BITS = 3
MAX_OWNERS = 1 << OWNER_BITS
_LANE_MASK = MAX_OWNERS - 1

_NANOS_PER_SECOND = 1_000_000_000


def owner_of(cursor: TaiCursor) -> int:
    """Which member allocated this cursor.

    Diagnostic rather than load-bearing -- nothing in the protocol reads it --
    but when two members disagree about an ordering, the first question is
    which of them minted the cursor, and answering it from the cursor itself
    beats correlating logs.
    """
    return cursor.nanoseconds & _LANE_MASK


class CursorAllocator:
    """Allocates cursors for one member, per resource type.

    Args:
        owner: This member's index in the canonical member order. Must be
            stable for the life of the cluster and distinct across members --
            both of which ``nmos/cluster/layout.py`` guarantees by deriving it
            from a total order over the member list.
    """

    __slots__ = ("_owner", "_high_water")

    def __init__(self, owner: int) -> None:
        if not 0 <= owner < MAX_OWNERS:
            raise ValueError(
                f"owner index {owner} does not fit in {OWNER_BITS} bits; "
                f"a cluster may have at most {MAX_OWNERS} members",
            )
        self._owner = owner
        # Per *type*, because the uniqueness rule is per type. Sharing one mark
        # across types would still be correct but would waste lanes, pushing
        # every type's cursors ahead of real time whenever any type was busy.
        self._high_water: dict[ResourceType, TaiCursor] = {}

    @property
    def owner(self) -> int:
        return self._owner

    def allocate(self, resource_type: ResourceType) -> TaiCursor:
        """The next cursor for ``resource_type``: in our lane, strictly ahead.

        Strictly ahead of both the local clock and everything this allocator
        has observed, so the sequence a client pages through never goes
        backwards even while members disagree about the time.
        """
        candidate = self._in_lane(TaiCursor.now())
        previous = self._high_water.get(resource_type)
        if previous is not None and candidate <= previous:
            candidate = self._next_in_lane(previous)
        self._high_water[resource_type] = candidate
        return candidate

    def observe(self, resource_type: ResourceType, cursor: TaiCursor) -> None:
        """Note a cursor that arrived through the log.

        Called from apply for every committed operation, including this
        member's own -- uniformly, because a rule that applied only to remote
        entries would leave the mark behind after a leadership change.
        """
        previous = self._high_water.get(resource_type)
        if previous is None or cursor > previous:
            self._high_water[resource_type] = cursor

    def high_water(self, resource_type: ResourceType) -> TaiCursor | None:
        """The highest cursor seen or allocated for a type, for diagnostics."""
        return self._high_water.get(resource_type)

    # -- lane arithmetic ------------------------------------------------

    def _in_lane(self, cursor: TaiCursor) -> TaiCursor:
        """Round a real instant down into this member's lane."""
        return TaiCursor(
            cursor.seconds,
            (cursor.nanoseconds & ~_LANE_MASK) | self._owner,
        )

    def _next_in_lane(self, after: TaiCursor) -> TaiCursor:
        """The smallest cursor in this member's lane strictly above ``after``.

        Always advances by at least one lane stride, so it terminates without
        a loop and cannot return ``after`` itself even when ``after`` is
        already in our lane.
        """
        nanoseconds = (((after.nanoseconds >> OWNER_BITS) + 1) << OWNER_BITS)
        nanoseconds |= self._owner
        if nanoseconds >= _NANOS_PER_SECOND:
            return TaiCursor(after.seconds + 1, self._owner)
        return TaiCursor(after.seconds, nanoseconds)
