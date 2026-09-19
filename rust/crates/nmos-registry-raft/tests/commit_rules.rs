// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The two commit rules a from-the-paper port gets wrong by default.
//!
//! Defects 3.3 and 3.4 of the port brief. Each test here fails if the defect is
//! reintroduced, and each names the symptom the original bug produced, because
//! a test whose failure message says only `assertion failed` leaves the next
//! person to rediscover why the line is written the way it is.
//!
//! These are written before the node that uses them, deliberately: they are the
//! specification for it.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::panic
)]

use nmos_registry_raft::commit::{
    commit_after_regression, commit_to_record, follower_commit_index, last_new_index,
    leader_commit_index, should_replicate_commit, should_send_now,
};

// -- defect 3.3: the follower commit rule -----------------------------------

#[test]
fn a_follower_does_not_commit_beyond_the_window_the_message_covered() {
    // The State Machine Safety bug, in one assertion.
    //
    // A follower holds entries up to index 9 -- stale, uncommitted, from a term
    // that lost. The leader sends an AppendEntries covering indices 1..=3 and
    // says leaderCommit = 9.
    //
    // Using the follower's own last_index (9) commits entries 4..=9, which this
    // follower holds and the leader does not. Another member commits something
    // else at those indices. That is exactly the soak failure: "index 3 applied
    // as term 1 by one member and term 2 by another".
    //
    // Figure 2 says "index of last new entry" -- 3, the last index *this
    // message* carried.
    let prev_log_index = 0;
    let entries_in_this_message = 3;
    let follower_last_index = 9; // stale entries beyond the window

    let committed = follower_commit_index(
        0,
        9,
        last_new_index(prev_log_index, entries_in_this_message),
    );

    assert_eq!(
        committed, 3,
        "the follower committed to {committed}, beyond index 3 which is the \
         last entry this message carried. With stale entries at 4..=9 that \
         commits data the leader never sent, and another member commits \
         something else at the same indices -- State Machine Safety violated.",
    );
    assert_ne!(
        committed, follower_last_index,
        "the follower used its own last_index, which is the defect verbatim",
    );
}

#[test]
fn the_commit_index_never_moves_backwards() {
    // The second half of 3.3, and the half that is easy to drop while fixing
    // the first. A short heartbeat arriving after a long append carries a
    // smaller `prev_log_index + len`, so a naive `min` would step backwards --
    // un-applying committed state, which is worse than never committing it.
    let after_a_long_append = follower_commit_index(0, 100, last_new_index(0, 100));
    assert_eq!(after_a_long_append, 100);

    // Now a heartbeat while the leader is probing backwards: it has committed
    // *further* (150) but is sending prev_log_index = 5 with no entries,
    // because it thinks this follower is behind. `min(150, 5)` is 5 -- below
    // the 100 already committed here.
    //
    // The `leaderCommit > commitIndex` reading alone does not save this: 150 is
    // above 100. Only comparing the *result* does.
    let after_a_short_heartbeat =
        follower_commit_index(after_a_long_append, 150, last_new_index(5, 0));
    assert_eq!(
        after_a_short_heartbeat, 100,
        "a backwards-probing heartbeat moved the commit index to \
         {after_a_short_heartbeat} from 100, un-applying committed state",
    );
}

#[test]
fn a_lower_leader_commit_is_ignored() {
    // Figure 2's own guard: "if leaderCommit > commitIndex". A leader that has
    // not caught up with what this follower already knows is committed must not
    // drag it back.
    assert_eq!(follower_commit_index(50, 20, last_new_index(0, 100)), 50);
}

#[test]
fn a_follower_commits_no_further_than_the_leader_has() {
    // The other side of the `min`: the message may carry more entries than the
    // leader has committed, and those extra entries are not committed yet.
    let committed = follower_commit_index(0, 3, last_new_index(0, 10));
    assert_eq!(
        committed, 3,
        "the follower committed {committed} of 10 replicated entries, but the \
         leader has only committed 3",
    );
}

#[test]
fn the_ordinary_case_still_advances() {
    // Guard the guard: a rule that never advanced would satisfy every test
    // above and stop the cluster entirely.
    assert_eq!(follower_commit_index(0, 5, last_new_index(0, 5)), 5);
    assert_eq!(follower_commit_index(2, 7, last_new_index(2, 5)), 7);
}

// -- defect 3.4: an advanced commit index replicates at once ----------------

#[test]
fn advancing_the_commit_index_triggers_immediate_replication() {
    // Without this the follower learns the commit index only from the next
    // heartbeat: measured 45.6 ms p50 against a 50 ms heartbeat, where the
    // leader had committed in ~1 ms. The latency is almost entirely waiting.
    assert!(
        should_replicate_commit(4, 5),
        "a leader that advanced its commit index did not replicate it, so \
         every follower waits a full heartbeat to learn what was committed",
    );
}

#[test]
fn an_unchanged_commit_index_sends_nothing() {
    // The reason it cannot loop. The extra round carries no entries, so no
    // follower's match index moves, so the commit index does not advance again
    // and no further round is triggered.
    assert!(!should_replicate_commit(5, 5));
    assert!(!should_replicate_commit(5, 4));
}

// -- the leader's own commit rule (§5.4.2) ----------------------------------

#[test]
fn a_leader_commits_at_the_quorum_position() {
    // Three members, the leader included. match = [5, 5, 3] means two of three
    // have index 5, which is a majority.
    let committed = leader_commit_index(vec![5, 5, 3], 0, 7, |_| Some(7));
    assert_eq!(committed, 5);

    // With only one other member at 5, index 5 is on two of five -- not a
    // majority -- and 3 is.
    let committed = leader_commit_index(vec![5, 5, 3, 3, 3], 0, 7, |_| Some(7));
    assert_eq!(committed, 3);
}

#[test]
fn a_leader_does_not_commit_an_entry_from_an_earlier_term() {
    // §5.4.2, and the figure-8 scenario the paper devotes a section to. An
    // entry replicated on a majority is *not* necessarily safe: a later leader
    // can still overwrite it unless an entry from the current term has
    // committed above it.
    //
    // Here index 5 is on a majority but belongs to term 3, while the leader is
    // in term 7. Committing it is exactly the bug.
    let committed = leader_commit_index(vec![5, 5, 3], 0, 7, |index| {
        if index == 5 { Some(3) } else { Some(7) }
    });
    assert_eq!(
        committed, 0,
        "the leader committed index 5 from term 3 while in term 7 -- a later \
         leader may still overwrite it (Raft §5.4.2, figure 8)",
    );

    // And once an entry from the current term is there, it commits.
    let committed = leader_commit_index(vec![5, 5, 3], 0, 7, |_| Some(7));
    assert_eq!(committed, 5);
}

#[test]
fn a_leader_never_moves_its_commit_index_backwards() {
    // A follower that falls behind reduces the quorum position; the leader must
    // not un-commit in response.
    assert_eq!(leader_commit_index(vec![9, 2, 2], 5, 7, |_| Some(7)), 5);
}

#[test]
fn an_empty_match_set_changes_nothing() {
    assert_eq!(leader_commit_index(Vec::new(), 4, 7, |_| Some(7)), 4);
}

#[test]
fn a_single_member_cluster_commits_on_its_own() {
    // Quorum of one. A one-member cluster that could not commit would be a
    // registry that accepts nothing.
    assert_eq!(leader_commit_index(vec![3], 0, 1, |_| Some(1)), 3);
}

// -- what the leader records as told, and when it sends again ---------------
//
// The other half of defect 3.4, and the half that was missed on the first
// attempt. Advancing the commit index replicates at once, which
// `should_replicate_commit` covers -- but a reply that does *not* advance it
// still leaves that peer behind, and the bookkeeping that decides so has to be
// honest about what each message actually vouched for.

#[test]
fn a_send_records_only_the_commit_index_its_own_window_delivers() {
    // The suppressed case: an append to this peer is already in flight, so this
    // message carries no entries and its window ends at prev_log_index 4. The
    // follower will adopt min(10, 4) = 4 by `follower_commit_index`, so 4 is
    // what the leader may record having told it.
    assert_eq!(commit_to_record(10, 4), 4);

    // Recording 10 here is the leader telling itself it had passed on something
    // the peer could not take. `should_send_now` below then finds nothing left
    // to say, and the peer learns the rest at the next heartbeat -- the exact
    // stall the eager send exists to remove.
    assert_ne!(commit_to_record(10, 4), 10);
}

#[test]
fn a_send_that_reaches_the_commit_index_records_all_of_it() {
    // Entries ride along through index 12, past the commit index, so the whole
    // of it is delivered and recording less would send a needless second copy.
    assert_eq!(commit_to_record(10, 12), 10);
    assert_eq!(commit_to_record(10, 10), 10);
}

#[test]
fn a_rejection_takes_back_what_the_new_window_no_longer_covers() {
    // The peer rejected back to index 2, so the leader vouches only through 1.
    // A record of 7 was delivered through the very message it refused.
    assert_eq!(commit_after_regression(7, 2), 1);
    // Already honest, so untouched -- this only ever clamps downwards.
    assert_eq!(commit_after_regression(1, 9), 1);
    // The floor: rejected back to the start of the log vouches for nothing.
    assert_eq!(commit_after_regression(7, 1), 0);
}

#[test]
fn a_peer_missing_entries_is_sent_them_without_waiting_for_a_tick() {
    // next_index 5, last_index 10: five entries this leader already holds.
    assert!(should_send_now(0, 5, 10, 10, 4));
}

#[test]
fn a_peer_behind_only_on_the_commit_index_is_still_told_at_once() {
    // Caught up on entries (next_index 11 > last_index 10) and so contributing
    // nothing to `has_entries`. This is etcd's `CanBumpCommit`, and the case
    // that dominates at five members: the commit index moves on the quorum
    // position, so every peer outside it lands exactly here after every round.
    assert!(should_send_now(0, 11, 10, 10, 4));
}

#[test]
fn a_peer_already_told_the_commit_index_is_not_told_again() {
    // `index > sentCommit` is false -- it has nothing new to learn, and a send
    // would be one message per reply for no reason.
    assert!(!should_send_now(0, 11, 10, 10, 10));
}

#[test]
fn a_peer_that_could_not_act_on_the_commit_index_is_not_sent_it() {
    // The second half of `CanBumpCommit`: `sentCommit < Next-1`. This peer has
    // already been told a commit index reaching the end of its window, so a
    // higher one tells it nothing it can use until its window moves.
    assert!(!should_send_now(0, 5, 4, 10, 4));
}

#[test]
fn a_peer_with_an_append_in_flight_is_left_to_that_exchange() {
    // etcd gates `maybeSendAppend` on `IsPaused` for the same reason: a second
    // copy of entries the link has not drained is a feedback loop, and the
    // reply to the outstanding append will say where the peer really is.
    assert!(!should_send_now(42, 5, 10, 10, 4));
}
