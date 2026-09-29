// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Paging cursors that stay unique and ordered without anyone coordinating.
//!
//! Port of `nmos/raft/cursors.py`.
//!
//! # The problem
//!
//! `APIs - Query Parameters.md:17` requires that no two resources of the same
//! type share a creation or update timestamp, because the timestamp *is* the
//! paging cursor: a client walking a collection with `paging.since=<cursor>`
//! resumes strictly after it, so two records on the same instant means one of
//! them is never returned.
//!
//! Standalone gets this for free -- one allocator, one high-water mark, fall
//! forward a nanosecond on collision. Distributed does not. The etcd backend
//! solved it by making the cursor part of the value it writes, so whichever
//! member commits decides and every member applies the same number. That works
//! because *something* serialises the write.
//!
//! Here, several members allocate cursors concurrently and only find out about
//! each other's afterwards. So the allocation itself has to be collision-proof.
//!
//! # Three mechanisms, all required
//!
//! **Owner bits.** The low bits of the nanosecond field carry the allocating
//! member's index, so two members physically cannot produce the same value. A
//! member only ever allocates within its own lane. Three bits covers the
//! maximum cluster size of five, and costs 8 ns of granularity -- against a
//! field whose real resolution is a clock read, that is free.
//!
//! **A hybrid logical clock.** Owner bits stop collisions but not *inversions*:
//! a member whose clock runs slow would allocate a cursor below one the cluster
//! has already published, and a client mid-page would skip the record. So every
//! cursor that arrives through the log is observed, the high-water mark rises
//! to it, and the next local allocation is pushed above it. Real time is a
//! lower bound on the cursor, never an upper one.
//!
//! **A reservation that outlives the process.** Owner bits keep two *members*
//! apart; nothing in them keeps two *incarnations* of one member apart, because
//! a restarted member allocates in the same lane it always did. While the clock
//! is ahead of every cursor in the log that costs nothing: a restarted member
//! reads a later time than any cursor it minted before. But once the log's
//! cursors are ahead of a member's clock -- its wall clock stepped back, or a
//! peer's clock runs fast -- every allocation is `next_in_lane(high_water)`, a
//! function of the log prefix this member has applied and nothing else. A
//! restart discards the high-water mark, the new incarnation rebuilds it from
//! the same log, and from the same prefix it mints the same cursor. Measured,
//! not supposed: with the log 5 s ahead of the clock, 5 of 172 chaos-soak runs
//! held two Nodes on one cursor, each pair minted by consecutive incarnations
//! of one member.
//!
//! So a member never hands out a cursor until an upper bound on it is on disk
//! ([`CursorAllocator::reservation_needed`], which the node persists beside its
//! term and vote), and a new incarnation resumes strictly above the bound its
//! predecessor recorded ([`CursorAllocator::resume`]). One write covers
//! [`RESERVATION_WINDOW_SECONDS`] of cursor progress, which keeps the disk out
//! of all but one allocation in that window.
//!
//! The consequence worth stating: a cursor is no longer exactly a wall-clock
//! instant. It is a monotonic identifier that starts from one, and under clock
//! skew it runs ahead of local time until local time catches up. The spec uses
//! it as an opaque ordered token -- `paging.since`/`paging.until` compare it,
//! they do not interpret it -- so this is within what the format promises, but
//! a reader expecting the nanoseconds to be a measurement will be surprised,
//! and that is what this paragraph is for.

use nmos_registry_core::cursor::TaiCursor;
use nmos_registry_core::resource_type::ResourceType;

/// Enough lanes for the largest permitted cluster (5), with headroom.
///
/// Fixed rather than derived from the member count so that a cluster resized
/// from 3 to 5 does not renumber the lanes and start colliding with cursors
/// already published under the old numbering.
pub const OWNER_BITS: u32 = 3;

/// How many members can allocate cursors without colliding.
pub const MAX_OWNERS: u64 = 1 << OWNER_BITS;

const LANE_MASK: u64 = MAX_OWNERS - 1;
const NANOS_PER_SECOND: u64 = 1_000_000_000;

/// How far past a cursor one durable reservation reaches.
///
/// The trade is between two bounded costs. A smaller window writes more often:
/// at most one write per window of cursor progress, and cursors progress with
/// real time, so one write a second while a member is allocating at all. A
/// larger window jumps further on restart: a new incarnation starts above the
/// reservation, so its first cursors can lead its clock by up to the window --
/// which only happens when the member restarts faster than the window, and
/// which the hybrid clock already treats as ordinary (cursors are allowed to
/// run ahead of real time). A second is small beside either cost.
pub const RESERVATION_WINDOW_SECONDS: u64 = 1;

/// Which member allocated this cursor.
///
/// Diagnostic rather than load-bearing -- nothing in the protocol reads it --
/// but when two members disagree about an ordering, the first question is which
/// of them minted the cursor, and answering it from the cursor itself beats
/// correlating logs.
#[must_use]
pub const fn owner_of(cursor: TaiCursor) -> u64 {
    cursor.nanoseconds & LANE_MASK
}

/// An owner index that does not fit in [`OWNER_BITS`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OwnerOutOfRange(pub String);

impl std::fmt::Display for OwnerOutOfRange {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for OwnerOutOfRange {}

/// Allocates cursors for one member, per resource type.
#[derive(Debug, Clone)]
pub struct CursorAllocator {
    owner: u64,
    /// Per *type*, because the uniqueness rule is per type. Sharing one mark
    /// across types would still be correct but would waste lanes, pushing every
    /// type's cursors ahead of real time whenever any type was busy.
    ///
    /// An array rather than a map: there are exactly six resource types, this
    /// is read and written on every apply, and a hash lookup for a six-element
    /// domain is pure overhead.
    high_water: [Option<TaiCursor>; ResourceType::ALL.len()],
    /// What the previous incarnation reserved: nothing at or below it is ever
    /// allocated again.
    ///
    /// One bound for every type rather than one per type: it only has to be
    /// above everything handed out, and a single value is a single key in the
    /// state file.
    floor: Option<TaiCursor>,
    /// The bound that is durable *now* -- the floor at start, raised by each
    /// reservation.
    reserved: Option<TaiCursor>,
}

impl CursorAllocator {
    /// An allocator for the member at index `owner`.
    ///
    /// `owner` must be stable for the life of the cluster and distinct across
    /// members -- both of which the cluster layout guarantees by deriving it
    /// from a total order over the member list.
    ///
    /// # Errors
    ///
    /// [`OwnerOutOfRange`] if `owner` does not fit in [`OWNER_BITS`].
    pub fn new(owner: u64) -> Result<Self, OwnerOutOfRange> {
        if owner >= MAX_OWNERS {
            return Err(OwnerOutOfRange(format!(
                "owner index {owner} does not fit in {OWNER_BITS} bits; a \
                 cluster may have at most {MAX_OWNERS} members",
            )));
        }
        Ok(Self {
            owner,
            high_water: [None; ResourceType::ALL.len()],
            floor: None,
            reserved: None,
        })
    }

    /// This member's lane.
    #[must_use]
    pub const fn owner(&self) -> u64 {
        self.owner
    }

    /// The durable bound on every cursor handed out, if any yet.
    ///
    /// What the node writes whenever it saves its state for another reason, so
    /// that a term change never carries an older reservation over a newer one.
    #[must_use]
    pub const fn reservation(&self) -> Option<TaiCursor> {
        self.reserved
    }

    /// Continue after a restart: everything up to `reserved` may be out.
    ///
    /// Called once, by the node, with what the state file holds, before this
    /// allocator hands out anything. `None` is a member that never reserved --
    /// its first start, or a state file written before the reservation existed
    /// -- and resumes nothing.
    pub const fn resume(&mut self, reserved: Option<TaiCursor>) {
        self.floor = reserved;
        self.reserved = reserved;
    }

    /// The next cursor for `resource_type`: in our lane, strictly ahead.
    ///
    /// Strictly ahead of the local clock, of everything this allocator has
    /// observed, and of everything an earlier incarnation of this member may
    /// have handed out ([`Self::resume`]), so the sequence a client pages
    /// through never goes backwards even while members disagree about the time.
    ///
    /// Not durable on its own: a caller that hands the result to anyone must
    /// first make [`Self::reservation_needed`] durable. The node does both, in
    /// `RaftNode::allocate_cursor`, which is the only production caller.
    pub fn allocate(&mut self, resource_type: ResourceType) -> TaiCursor {
        let mut candidate = self.in_lane(TaiCursor::now());
        let above = match (self.high_water(resource_type), self.floor) {
            (Some(seen), Some(floor)) => Some(seen.max(floor)),
            (seen, floor) => seen.or(floor),
        };
        if let Some(previous) = above
            && candidate <= previous
        {
            candidate = self.next_in_lane(previous);
        }
        self.set_high_water(resource_type, candidate);
        candidate
    }

    /// The bound to make durable before `cursor` is handed out, if any.
    ///
    /// `None` when the reservation already on disk covers it -- every
    /// allocation but about one per [`RESERVATION_WINDOW_SECONDS`]. Otherwise a
    /// bound [`RESERVATION_WINDOW_SECONDS`] past `cursor`: once it is durable,
    /// the next incarnation resumes above it, and so above `cursor`.
    #[must_use]
    pub fn reservation_needed(&self, cursor: TaiCursor) -> Option<TaiCursor> {
        match self.reserved {
            Some(reserved) if cursor <= reserved => None,
            _ => Some(TaiCursor::new(
                cursor.seconds.saturating_add(RESERVATION_WINDOW_SECONDS),
                cursor.nanoseconds,
            )),
        }
    }

    /// Note that `reserved` is now durable.
    pub fn confirm_reservation(&mut self, reserved: TaiCursor) {
        if self.reserved.is_none_or(|current| reserved > current) {
            self.reserved = Some(reserved);
        }
    }

    /// Note a cursor that arrived through the log.
    ///
    /// Called from apply for every committed operation, including this member's
    /// own -- uniformly, because a rule that applied only to remote entries
    /// would leave the mark behind after a leadership change.
    pub fn observe(&mut self, resource_type: ResourceType, cursor: TaiCursor) {
        match self.high_water(resource_type) {
            Some(previous) if cursor <= previous => {}
            _ => self.set_high_water(resource_type, cursor),
        }
    }

    /// The highest cursor seen or allocated for a type, for diagnostics.
    ///
    /// The slot lookup is `get` rather than an index because this crate forbids
    /// panicking indexing. `ResourceType` has exactly as many variants as the
    /// array has slots, so the fallback is unreachable -- and "unreachable, so
    /// index it" is how a crate that forbids panics acquires one anyway, the
    /// next time someone adds a variant.
    #[must_use]
    pub fn high_water(&self, resource_type: ResourceType) -> Option<TaiCursor> {
        self.high_water
            .get(resource_type as usize)
            .copied()
            .flatten()
    }

    fn set_high_water(&mut self, resource_type: ResourceType, cursor: TaiCursor) {
        if let Some(slot) = self.high_water.get_mut(resource_type as usize) {
            *slot = Some(cursor);
        }
    }

    // -- lane arithmetic ----------------------------------------------------

    /// Round a real instant down into this member's lane.
    const fn in_lane(&self, cursor: TaiCursor) -> TaiCursor {
        TaiCursor::new(
            cursor.seconds,
            (cursor.nanoseconds & !LANE_MASK) | self.owner,
        )
    }

    /// The smallest cursor in this member's lane strictly above `after`.
    ///
    /// Always advances by at least one lane stride, so it terminates without a
    /// loop and cannot return `after` itself even when `after` is already in
    /// our lane.
    const fn next_in_lane(&self, after: TaiCursor) -> TaiCursor {
        // Saturating throughout, though neither can trigger: `nanoseconds` is
        // below 1e9, so the shifted value is below 1.25e8 and the result below
        // 1e9 + 8 -- all far inside `u32`. Written this way because the crate
        // denies unchecked arithmetic, and an exception granted here would be
        // an exception granted to the next expression too.
        let lane = (after.nanoseconds >> OWNER_BITS).saturating_add(1);
        let nanoseconds = lane.saturating_mul(MAX_OWNERS) | self.owner;
        if nanoseconds >= NANOS_PER_SECOND {
            // The next cursor after the last lane slot of a second is the
            // *first* slot of the next second, which is the lane index itself.
            //
            // **This branch is redundant today and is kept deliberately.**
            // `TaiCursor::new` normalises an overflow by division, and that
            // happens to land in the same place: the overflow can only ever be
            // exactly `NANOS_PER_SECOND + owner`, and `NANOS_PER_SECOND` is
            // divisible by `MAX_OWNERS`, so the division preserves the lane.
            // Measured -- removing this branch changes no observable value, so
            // no test can catch its removal.
            //
            // It stays because that identity is a coincidence of the constants,
            // not a property of the algorithm: raise `OWNER_BITS` past 9 and
            // the division starts landing in another member's lane, which is a
            // silent cursor collision. `the_lane_width_divides_the_second`
            // guards the precondition so the coincidence cannot quietly lapse.
            TaiCursor::new(after.seconds.saturating_add(1), self.owner)
        } else {
            TaiCursor::new(after.seconds, nanoseconds)
        }
    }
}
