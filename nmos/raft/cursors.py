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

Three mechanisms, all required
------------------------------
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

**A reservation that outlives the process.** Owner bits keep two *members*
apart; nothing in them keeps two *incarnations* of one member apart, because a
restarted member allocates in the same lane it always did. While the clock is
ahead of every cursor in the log that costs nothing: a restarted member reads a
later time than any cursor it minted before. But once the log's cursors are
ahead of a member's clock -- its wall clock stepped back, or a peer's clock runs
fast -- every allocation is ``_next_in_lane(high_water)``, a function of the log
prefix this member has applied and nothing else. A restart discards the
high-water mark, the new incarnation rebuilds it from the same log, and from the
same prefix it mints the same cursor. Measured, not supposed: with the log 5 s
ahead of the clock, 5 of 172 chaos-soak runs held two Nodes on one cursor, each
pair minted by consecutive incarnations of one member.

So a member never hands out a cursor until an upper bound on it is on disk
(``reservation_needed``, which the node persists beside its term and vote), and
a new incarnation resumes strictly above the bound its predecessor recorded
(``resume``). One write covers ``RESERVATION_WINDOW_SECONDS`` of cursor
progress, which keeps the disk out of all but one allocation in that window.

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

# How far past a cursor one durable reservation reaches. The trade is between
# two bounded costs. A smaller window writes more often: at most one write per
# window of cursor progress, and cursors progress with real time, so one write
# a second while a member is allocating at all. A larger window jumps further
# on restart: a new incarnation starts above the reservation, so its first
# cursors can lead its clock by up to the window -- which only happens when the
# member restarts faster than the window, and which the hybrid clock already
# treats as ordinary (cursors are allowed to run ahead of real time). A second
# is small beside either cost.
RESERVATION_WINDOW_SECONDS = 1


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

    __slots__ = ("_owner", "_high_water", "_floor", "_reserved")

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
        # One bound for every type rather than one per type: it only has to be
        # above everything handed out, and a single value is a single key in
        # the state file.
        #
        # ``_floor`` is what the previous incarnation reserved: nothing at or
        # below it is ever allocated again. ``_reserved`` is the bound that is
        # durable *now* -- the floor at start, raised by each reservation.
        self._floor: TaiCursor | None = None
        self._reserved: TaiCursor | None = None

    @property
    def owner(self) -> int:
        return self._owner

    @property
    def reservation(self) -> TaiCursor | None:
        """The durable bound on every cursor handed out, if any yet.

        What the node writes whenever it saves its state for another reason, so
        that a term change never carries an older reservation over a newer one.
        """
        return self._reserved

    def resume(self, reserved: TaiCursor | None) -> None:
        """Continue after a restart: everything up to ``reserved`` may be out.

        Called once, by the node, with what the state file holds, before this
        allocator hands out anything. ``None`` is a member that never reserved
        -- its first start, or a state file written before the reservation
        existed -- and resumes nothing.
        """
        self._floor = reserved
        self._reserved = reserved

    def allocate(self, resource_type: ResourceType) -> TaiCursor:
        """The next cursor for ``resource_type``: in our lane, strictly ahead.

        Strictly ahead of the local clock, of everything this allocator has
        observed, and of everything an earlier incarnation of this member may
        have handed out (``resume``), so the sequence a client pages through
        never goes backwards even while members disagree about the time.

        Not durable on its own: a caller that hands the result to anyone must
        first make ``reservation_needed`` durable. The node does both, in
        ``RaftNode.allocate_cursor``, which is the only production caller.
        """
        candidate = self._in_lane(TaiCursor.now())
        previous = self._high_water.get(resource_type)
        if self._floor is not None and (
            previous is None or previous < self._floor
        ):
            previous = self._floor
        if previous is not None and candidate <= previous:
            candidate = self._next_in_lane(previous)
        self._high_water[resource_type] = candidate
        return candidate

    def reservation_needed(self, cursor: TaiCursor) -> TaiCursor | None:
        """The bound to make durable before ``cursor`` is handed out, if any.

        ``None`` when the reservation already on disk covers it -- every
        allocation but about one per ``RESERVATION_WINDOW_SECONDS``. Otherwise a
        bound ``RESERVATION_WINDOW_SECONDS`` past ``cursor``: once it is
        durable, the next incarnation resumes above it, and so above ``cursor``.
        """
        if self._reserved is not None and cursor <= self._reserved:
            return None
        return TaiCursor(
            cursor.seconds + RESERVATION_WINDOW_SECONDS, cursor.nanoseconds,
        )

    def confirm_reservation(self, reserved: TaiCursor) -> None:
        """Note that ``reserved`` is now durable."""
        if self._reserved is None or reserved > self._reserved:
            self._reserved = reserved

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
