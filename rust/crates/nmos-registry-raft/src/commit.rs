// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! When a follower may advance its commit index, and when a leader must
//! replicate having done so.
//!
//! These two rules are one line of code each and are together the most
//! dangerous lines in the implementation. They are in their own module so the
//! reasoning is next to them rather than buried in a 1,800-line node.
//!
//! # Defect 3.3 — the follower commit rule
//!
//! Figure 2, AppendEntries receiver rule 5, verbatim:
//!
//! > "If leaderCommit > commitIndex, set commitIndex = min(leaderCommit,
//! > **index of last new entry**)"
//!
//! The phrase that matters is *index of last new entry* -- the last index **the
//! message carried**, which is `prev_log_index + entries.len()`. An earlier
//! version of the Python used the follower's own `last_index` instead. A
//! follower holding stale uncommitted entries *beyond* the window the message
//! covered would then commit them.
//!
//! That is a State Machine Safety violation: two members apply different
//! entries at the same index. It surfaced in the chaos soak as `index 3 applied
//! as term 1 by one member and term 2 by another`, in **one run in three, one
//! seed in eighty** -- rare enough to survive a lot of testing, and fatal.
//!
//! The second half is equally load-bearing: **compare before assigning**. A
//! short heartbeat arriving after a long append carries a smaller
//! `prev_log_index + len`, so a naive `min` would move the commit index
//! *backwards*, un-applying committed state. `commit_index` is monotonic, and
//! this is where that is enforced.
//!
//! # Defect 3.4 — an advanced commit index replicates at once
//!
//! A follower learns the commit index only from `leaderCommit` on an
//! `AppendEntries`. Leaving a newly advanced commit index to ride the next
//! heartbeat puts a full heartbeat interval on the critical path of every
//! mutation that did not arrive at the leader: **measured 45.6 ms p50 against a
//! 50 ms heartbeat**, where the leader had committed in ~1 ms.
//!
//! [`should_replicate_commit`] is what the leader consults. It cannot loop: the
//! extra round carries no entries, so it advances no follower's log and
//! therefore produces no further commit advance.

/// What a follower's commit index becomes after one `AppendEntries`.
///
/// `last_new_index` is `prev_log_index + entries.len()` -- the last index *this
/// message* carried, not the follower's own last index. See the module docs for
/// why that distinction is a safety property rather than a detail.
///
/// Returns the new commit index, which is never below `commit_index`.
#[must_use]
pub const fn follower_commit_index(
    commit_index: u64,
    leader_commit: u64,
    last_new_index: u64,
) -> u64 {
    // The leader vouches only as far as `prev_log_index` plus what it actually
    // sent -- which for a heartbeat is `prev_log_index` alone, and heartbeats
    // are exactly when a follower's log runs ahead of the leader's knowledge of
    // it.
    let advanced = if leader_commit < last_new_index {
        leader_commit
    } else {
        last_new_index
    };
    // **Compared, not assigned.** `min` with a short window can land below
    // where this member already is, and a commit index that moves backwards
    // would un-apply committed state.
    //
    // One guard, as the Python has one. Figure 2 also states "if leaderCommit >
    // commitIndex", but adding that as a separate precondition is redundant
    // given this comparison -- proved by reintroducing each in turn and finding
    // the tests could not tell the difference. A redundant guard is not free:
    // it would make a future reader think both were load-bearing and leave the
    // wrong one in place when simplifying.
    if advanced > commit_index {
        advanced
    } else {
        commit_index
    }
}

/// The last index an `AppendEntries` carried.
///
/// Spelled out because "index of last new entry" is the phrase the whole of
/// defect 3.3 turns on, and computing it at each call site is how it came to be
/// the follower's own `last_index` in the first place.
#[must_use]
pub const fn last_new_index(prev_log_index: u64, entry_count: u64) -> u64 {
    prev_log_index.saturating_add(entry_count)
}

/// Whether a leader that just advanced its commit index must replicate now.
///
/// Defect 3.4. `true` means send an `AppendEntries` immediately rather than
/// waiting for the heartbeat, because a follower cannot learn the commit index
/// any other way.
///
/// This cannot loop: the round it triggers carries no entries, so no follower's
/// match index moves, so the leader's commit index does not advance again.
#[must_use]
pub const fn should_replicate_commit(previous_commit: u64, new_commit: u64) -> bool {
    new_commit > previous_commit
}

/// Where a leader's commit index sits, given what each follower has matched.
///
/// Figure 2's leader rule: the highest `N` such that a majority have
/// `match_index >= N` **and** `log[N].term == currentTerm`.
///
/// The term check is §5.4.2 and is not optional: a leader may not commit an
/// entry from an earlier term merely because it is present on a majority now,
/// because a later leader could still overwrite it. It becomes committed
/// indirectly, when an entry from the current term commits above it.
///
/// `match_indices` must include the leader's own last index -- a leader counts
/// itself toward its own quorum.
#[must_use]
pub fn leader_commit_index(
    mut match_indices: Vec<u64>,
    current_commit: u64,
    current_term: u64,
    term_at: impl Fn(u64) -> Option<u64>,
) -> u64 {
    if match_indices.is_empty() {
        return current_commit;
    }
    // Descending, so the element at the quorum position is the highest index a
    // majority have reached.
    match_indices.sort_unstable_by(|a, b| b.cmp(a));
    let quorum = match_indices.len().div_ceil(2);
    let Some(&candidate) = match_indices.get(quorum.saturating_sub(1)) else {
        return current_commit;
    };

    if candidate <= current_commit {
        return current_commit;
    }
    // §5.4.2. Without this a leader can commit a stale entry that a future
    // leader is still entitled to overwrite -- the figure-8 scenario in the
    // paper, and the reason "replicated on a majority" is not sufficient.
    if term_at(candidate) != Some(current_term) {
        return current_commit;
    }
    candidate
}
