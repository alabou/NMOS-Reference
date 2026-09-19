// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Cursors stay unique and ordered without anyone coordinating.
//!
//! Port of `nmos/raft/tests/test_cursors.py`. The property under test is the
//! one `APIs - Query Parameters.md:17` requires: no two resources of the same
//! type share a cursor, because the cursor *is* the paging position and a
//! collision means one record is never returned.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::arithmetic_side_effects,
    // Several assertions here are over constants on purpose: they pin the
    // cluster-size headroom and the lane-width divisibility that the rollover
    // depends on, and a constant assertion is exactly the right shape for a
    // precondition that must not drift.
    clippy::assertions_on_constants,
    clippy::panic
)]

use std::collections::HashSet;

use nmos_registry_core::cursor::TaiCursor;
use nmos_registry_core::resource_type::ResourceType;
use nmos_registry_raft::cursors::{CursorAllocator, MAX_OWNERS, OWNER_BITS, owner_of};

const SENDER: ResourceType = ResourceType::Sender;
const RECEIVER: ResourceType = ResourceType::Receiver;

fn allocator(owner: u64) -> CursorAllocator {
    CursorAllocator::new(owner).expect("a valid owner index")
}

// -- lanes ------------------------------------------------------------------

#[test]
fn every_cursor_carries_its_allocator() {
    for owner in 0..MAX_OWNERS {
        assert_eq!(owner_of(allocator(owner).allocate(SENDER)), owner);
    }
}

#[test]
fn an_owner_index_that_does_not_fit_is_refused() {
    let error = CursorAllocator::new(MAX_OWNERS).expect_err("refused");
    assert!(
        error.0.contains("at most"),
        "unhelpful refusal: {}",
        error.0
    );
}

#[test]
fn three_bits_covers_the_largest_permitted_cluster() {
    // 1, 3 or 5 members, so 8 lanes is headroom rather than a limit.
    assert_eq!(OWNER_BITS, 3);
    assert!(MAX_OWNERS >= 5);
}

// -- uniqueness -------------------------------------------------------------

#[test]
fn members_allocating_together_never_collide() {
    // The property owner bits exist for. Two members cannot be made to produce
    // the same cursor, whatever their clocks say, because they never write into
    // the same lane.
    let mut allocators: Vec<CursorAllocator> = (0..5).map(allocator).collect();
    let mut seen: HashSet<TaiCursor> = HashSet::new();

    for _round in 0..200 {
        for each in &mut allocators {
            let cursor = each.allocate(SENDER);
            assert!(
                seen.insert(cursor),
                "two members minted {cursor}; a client paging past it never \
                 sees one of the two records",
            );
        }
    }
}

#[test]
fn repeated_allocation_from_one_member_is_strictly_increasing() {
    // The clock has coarser resolution than the allocation rate, so this is
    // exercising the fall-forward and not the clock.
    let mut each = allocator(1);
    let mut previous = each.allocate(SENDER);
    for _ in 0..1000 {
        let current = each.allocate(SENDER);
        assert!(
            current > previous,
            "{current} did not advance past {previous}"
        );
        previous = current;
    }
}

#[test]
fn types_are_independent() {
    // Uniqueness is per type, so one type's mark must not drag another.
    //
    // Pushed via `observe` rather than by allocating in a loop: the clock
    // advances faster than the lane stride, so a busy type simply tracks real
    // time and the two marks would stay together for reasons that have nothing
    // to do with independence.
    let mut each = allocator(2);
    let far_ahead = TaiCursor::new(TaiCursor::now().seconds + 3600, 0);
    each.observe(SENDER, far_ahead);

    assert!(each.allocate(SENDER) > far_ahead);
    assert!(
        each.allocate(RECEIVER) < far_ahead,
        "a Sender's mark dragged the Receiver lane an hour into the future",
    );
}

// -- monotonicity under skew ------------------------------------------------

#[test]
fn observing_a_higher_cursor_pushes_the_next_allocation_above_it() {
    // A member whose clock lags must not allocate into the past. Without this a
    // client mid-page would skip the record: it asked for everything after
    // cursor X, and the registry minted the new resource below X.
    let mut each = allocator(0);
    let ahead = TaiCursor::new(each.allocate(SENDER).seconds + 60, 500);
    each.observe(SENDER, ahead);

    let following = each.allocate(SENDER);
    assert!(following > ahead);
    assert_eq!(owner_of(following), 0);
}

#[test]
fn observing_a_lower_cursor_changes_nothing() {
    let mut each = allocator(0);
    let current = each.allocate(SENDER);
    each.observe(SENDER, TaiCursor::new(1, 1));
    assert!(each.allocate(SENDER) > current);
}

#[test]
fn a_cluster_under_skew_produces_one_increasing_sequence() {
    // The end-to-end property, simulated. Every member allocates, every member
    // observes every allocation through the log, and the order the cluster
    // publishes never goes backwards -- which is exactly what a paging client
    // walks.
    let mut allocators: Vec<CursorAllocator> = (0..3).map(allocator).collect();
    let mut published: Vec<TaiCursor> = Vec::new();

    // Member 2 is 30 s ahead. Simulated by observing an artificial cursor
    // rather than moving the clock.
    allocators[2].observe(SENDER, TaiCursor::new(TaiCursor::now().seconds + 30, 0));

    for round in 0..100 {
        let cursor = allocators[round % 3].allocate(SENDER);
        // The log delivers it to everyone, including the allocator.
        for peer in &mut allocators {
            peer.observe(SENDER, cursor);
        }
        published.push(cursor);
    }

    let mut sorted = published.clone();
    sorted.sort_unstable();
    assert_eq!(published, sorted, "the published sequence went backwards");

    let unique: HashSet<TaiCursor> = published.iter().copied().collect();
    assert_eq!(
        unique.len(),
        published.len(),
        "the cluster repeated a cursor"
    );
}

#[test]
fn the_sequence_survives_a_member_that_never_hears_the_others() {
    // Uniqueness must not depend on observation, only ordering does.
    //
    // A partitioned member keeps allocating. Its cursors may interleave with
    // the majority's once it rejoins, but they must never *equal* one -- that
    // is the difference between a client seeing records out of order and a
    // client never seeing one at all.
    let mut connected = allocator(0);
    let mut isolated = allocator(1);

    let theirs: HashSet<TaiCursor> = (0..50).map(|_| connected.allocate(SENDER)).collect();
    let ours: HashSet<TaiCursor> = (0..50).map(|_| isolated.allocate(SENDER)).collect();
    assert!(
        theirs.is_disjoint(&ours),
        "a partitioned member minted a cursor the majority had already used",
    );
}

// -- lane arithmetic --------------------------------------------------------

#[test]
fn advancing_rolls_over_the_second_boundary() {
    let mut each = allocator(5);
    // Dated ahead of the real clock, or the clock -- not the mark -- is what
    // the next allocation would follow, and the rollover never runs.
    let edge = TaiCursor::new(TaiCursor::now().seconds + 3600, 999_999_999);
    each.observe(SENDER, edge);

    let following = each.allocate(SENDER);
    assert_eq!(following.seconds, edge.seconds + 1);
    assert_eq!(
        owner_of(following),
        5,
        "the rollover left this member's lane, so two members can now collide",
    );
}

#[test]
fn advancing_past_a_cursor_in_our_own_lane() {
    // Must move, not return the same value.
    let mut each = allocator(3);
    let mine = each.allocate(SENDER);
    each.observe(SENDER, mine);
    assert!(each.allocate(SENDER) > mine);
}

#[test]
fn the_stride_is_one_lane_width() {
    // 8 ns of granularity, against a field whose real resolution is a clock
    // read.
    let mut each = allocator(0);
    let mark = TaiCursor::new(TaiCursor::now().seconds + 3600, 0);
    each.observe(SENDER, mark);
    assert_eq!(
        each.allocate(SENDER),
        TaiCursor::new(mark.seconds, MAX_OWNERS),
    );
}

#[test]
fn the_rollover_lands_on_the_first_slot_of_the_lane() {
    // The boundary case the Python's rollover test covers only for member 5.
    // `TaiCursor::new` normalises an overflowing nanosecond field by division,
    // which would land outside the lane -- so the rollover has to construct the
    // next second directly. Checked for every lane, because a division that
    // happened to land in lane 5 would pass the single-member test.
    for owner in 0..MAX_OWNERS {
        let mut each = allocator(owner);
        let edge = TaiCursor::new(TaiCursor::now().seconds + 3600, 999_999_999);
        each.observe(SENDER, edge);

        let following = each.allocate(SENDER);
        assert_eq!(following.seconds, edge.seconds + 1, "lane {owner}");
        assert_eq!(following.nanoseconds, owner, "lane {owner}");
        assert!(following > edge, "lane {owner} rolled backwards");
    }
}

#[test]
fn the_lane_width_divides_the_second() {
    // The precondition the second-rollover rests on. A lane width that does not
    // divide 10^9 makes the last slot of a second normalise into *another
    // member's* lane, which is a silent cursor collision between two members --
    // the one failure mode this whole module exists to make impossible.
    //
    // Asserted rather than left implicit because the rollover branch that
    // depends on it is invisible to every other test: with these constants it
    // computes exactly what plain normalisation computes, so removing it
    // changes nothing and only this assertion would notice the constants
    // drifting.
    assert_eq!(
        NANOS_PER_SECOND % MAX_OWNERS,
        0,
        "a lane width of {MAX_OWNERS} does not divide {NANOS_PER_SECOND}, so a \
         cursor in the last slot of a second rolls over into another member's \
         lane",
    );
}

/// Kept here rather than exported: it is a property of the cursor format, and a
/// public constant would invite code to depend on the nanosecond field being a
/// measurement -- which, as the module docs say, it is not.
const NANOS_PER_SECOND: u64 = 1_000_000_000;
