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
//
// `leader_commit_index(quorum, leader_last_index, acknowledged, commit, term,
// term_at)`: `acknowledged` holds only the peers whose acknowledgements count.

#[test]
fn a_leader_commits_at_the_quorum_position() {
    // Three members: the leader at 5, peers at 5 and 3. Two of three hold
    // index 5, which is a majority.
    let committed = leader_commit_index(2, 5, vec![5, 3], 0, 7, |_| Some(7));
    assert_eq!(committed, 5);

    // Five members and only one peer at 5: index 5 is on two of five -- not a
    // majority -- and 3 is.
    let committed = leader_commit_index(3, 5, vec![5, 3, 3, 3], 0, 7, |_| Some(7));
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
    let committed = leader_commit_index(2, 5, vec![5, 3], 0, 7, |index| {
        if index == 5 { Some(3) } else { Some(7) }
    });
    assert_eq!(
        committed, 0,
        "the leader committed index 5 from term 3 while in term 7 -- a later \
         leader may still overwrite it (Raft §5.4.2, figure 8)",
    );

    // And once an entry from the current term is there, it commits.
    let committed = leader_commit_index(2, 5, vec![5, 3], 0, 7, |_| Some(7));
    assert_eq!(committed, 5);
}

#[test]
fn a_leader_never_moves_its_commit_index_backwards() {
    // A follower that falls behind reduces the quorum position; the leader must
    // not un-commit in response.
    assert_eq!(leader_commit_index(2, 9, vec![2, 2], 5, 7, |_| Some(7)), 5);
}

#[test]
fn a_leader_with_no_countable_peer_commits_nothing() {
    // Three members, and no peer whose acknowledgement counts: the leader on
    // its own is one of three, which is not a majority.
    assert_eq!(leader_commit_index(2, 9, Vec::new(), 4, 7, |_| Some(7)), 4);
}

#[test]
fn a_single_member_cluster_commits_on_its_own() {
    // Quorum of one. A one-member cluster that could not commit would be a
    // registry that accepts nothing.
    assert_eq!(leader_commit_index(1, 3, Vec::new(), 0, 1, |_| Some(1)), 3);
}

// -- a majority of the voting configuration (chaos soak R1) ------------------
//
// A member catching up after a restart is not counted, but it is still one of
// the members a majority must outnumber. The rule once took the majority of
// whoever was counted, and a leader of three with one member catching up
// committed on its own.

#[test]
fn a_member_that_cannot_be_counted_is_still_in_the_majority() {
    // The decision the chaos soak's commit audit measured, verbatim: m2 leads
    // term 57 holding index 60, m0 is catching up and so is not counted, m1
    // has matched 52, and the commit index is 56. A majority of three is two,
    // and the highest index two members are known to hold is 52 -- already
    // committed.
    let committed = leader_commit_index(2, 60, vec![52], 56, 57, |_| Some(57));
    assert_eq!(
        committed, 56,
        "the leader committed through {committed} holding it alone: the \
         member catching up does not count, the other has matched only 52, \
         and one of three is not a majority",
    );
}

#[test]
fn five_members_with_one_catching_up_still_need_three() {
    // Leader and one peer at 10, two peers at 5, the fifth catching up. Index
    // 10 is on two of five. The majority of the four *counted* would be two,
    // which is the defect; the majority of the cluster is three.
    let committed = leader_commit_index(3, 10, vec![10, 5, 5], 0, 7, |_| Some(7));
    assert_eq!(
        committed, 5,
        "committed through {committed} on two of five members",
    );
}

#[test]
fn five_members_with_two_catching_up_need_both_of_the_others() {
    // Three counted -- the leader and two peers -- is exactly a majority of
    // five, so everything they all hold commits, and nothing beyond it.
    assert_eq!(
        leader_commit_index(3, 10, vec![10, 10], 0, 7, |_| Some(7)),
        10
    );
    assert_eq!(
        leader_commit_index(3, 10, vec![10, 4], 0, 7, |_| Some(7)),
        4
    );
}

#[test]
fn fewer_countable_members_than_a_majority_commit_nothing() {
    // Five members, three catching up: the two counted -- the leader and one
    // peer -- can never be a majority, however far both have got.
    assert_eq!(leader_commit_index(3, 10, vec![10], 2, 7, |_| Some(7)), 2);
}

#[test]
fn the_rule_is_figure_2_for_every_small_cluster() {
    // Every cluster of one to five members, every position and countability of
    // every peer, every commit index and every log whose terms rise with its
    // index, checked against Figure 2's rule stated as a search:
    //
    // > If there exists an N such that N > commitIndex, a majority of
    // > matchIndex[i] >= N, and log[N].term == currentTerm: set commitIndex = N
    //
    // with the majority taken over all `n` members, the leader counting
    // itself, and a member catching up holding nothing that counts. The
    // majority is written out here, `n / 2 + 1`, rather than taken from the
    // implementation, so that the two cannot share a mistake.
    //
    // The function looks at one candidate where the definition searches, and
    // that is sound only because terms never fall along a log: if the highest
    // index a majority holds is from an earlier term, so is every index below
    // it. The logs here are exactly those, so a function that relied on
    // anything else would fail below.
    const LAST: u64 = 3;
    let mut logs: Vec<Vec<u64>> = Vec::new();
    for first in 1..=3u64 {
        for second in first..=3 {
            for third in second..=3 {
                logs.push(vec![first, second, third]);
            }
        }
    }
    let mut checked = 0u64;
    for members in 1..=5usize {
        let majority = members / 2 + 1;
        let peers = members - 1;
        // Per peer: a match index in 0..=LAST, and whether it may be counted.
        let states_per_peer = (LAST + 1) * 2;
        let assignments = states_per_peer.pow(u32::try_from(peers).unwrap());
        for log in &logs {
            let highest_term = *log.last().unwrap();
            for current_term in [highest_term, highest_term + 1] {
                let term_at = |index: u64| -> Option<u64> {
                    match index {
                        0 => Some(0),
                        _ => log.get(usize::try_from(index - 1).ok()?).copied(),
                    }
                };
                for commit in 0..=LAST {
                    for assignment in 0..assignments {
                        let mut rest = assignment;
                        let mut acknowledged = Vec::new();
                        for _ in 0..peers {
                            let state = rest % states_per_peer;
                            rest /= states_per_peer;
                            let (matched, counted) = (state / 2, state.is_multiple_of(2));
                            if counted {
                                acknowledged.push(matched);
                            }
                        }

                        let defined = (commit + 1..=LAST)
                            .rev()
                            .find(|&index| {
                                let holding = 1 + acknowledged
                                    .iter()
                                    .filter(|&&matched| matched >= index)
                                    .count();
                                holding >= majority && term_at(index) == Some(current_term)
                            })
                            .unwrap_or(commit);
                        let computed = leader_commit_index(
                            majority,
                            LAST,
                            acknowledged.clone(),
                            commit,
                            current_term,
                            term_at,
                        );
                        assert_eq!(
                            computed, defined,
                            "{members} members, counted acknowledgements \
                             {acknowledged:?}, leader at {LAST}, log terms \
                             {log:?}, term {current_term}, commit {commit}: \
                             Figure 2 commits through {defined}, the rule \
                             through {computed}",
                        );
                        checked += 1;
                    }
                }
            }
        }
    }
    // A search that found nothing to check would pass by being empty.
    assert!(checked > 100_000, "only {checked} cases were checked");
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
