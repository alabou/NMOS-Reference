// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Two decisions a leader makes about its peers, audited at the instant each is
//! made.
//!
//! # Why the monitor is not enough
//!
//! The monitor's properties are about *consequences*. A leader that commits an
//! index a majority does not hold, or re-admits a member to the electorate
//! while it is missing a committed entry, breaks nothing visible at that
//! moment. The damage shows only if a later leader lacking the entry is elected
//! and writes over it -- and then Committed Agreement or Leader Completeness
//! fires, many terms and thousands of messages after the decision that caused
//! it. Most runs never get that far, so the wrong decision goes unseen, and a
//! run that does get that far points at the overwrite rather than the cause.
//!
//! This audit checks each decision against the rule it must follow.
//!
//! ## Commit
//!
//! Figure 2, "Rules for Servers", Leaders:
//!
//! > If there exists an N such that N > commitIndex, a majority of
//! > matchIndex[i] >= N, and log[N].term == currentTerm: set commitIndex = N
//!
//! "A majority" is of the **voting configuration** -- every configured member,
//! whether or not it is currently able to acknowledge. A member that may not
//! be counted (here: one still catching up after a restart, whose
//! acknowledgements establish nothing) contributes no support but stays in the
//! denominator. The leader counts itself: it holds everything it appended.
//!
//! The majority is computed here from the cluster size, `n / 2 + 1`, rather
//! than through the implementation's own quorum helpers, so that an error in
//! those cannot hide itself.
//!
//! ## Promotion
//!
//! A restarted member comes back without its log and must not vote until it is
//! promoted, because the election restriction protects a committed entry only
//! through voters that hold it (`nmos/raft/node.py`, module docstring). The
//! rule a promotion must follow is therefore: **the member holds every entry
//! committed before it restarted.** Those are the entries that may have been
//! committed *counting it*, and whose majority its restart may have reduced to
//! a minority of voters; re-admitting it without them makes the voters lacking
//! such an entry a majority again. Entries committed after it restarted were
//! committed on a majority that excludes it, because a member catching up is
//! never counted, so lacking those is safe -- which is why the floor is taken
//! at the restart and not at the promotion.
//!
//! The audit records, when a member's new incarnation is built, the highest
//! index any member has been seen to commit, in any incarnation; a promotion
//! must find the member's match index at or above it. What the leader knew is
//! beside the point, and that is the point: a leader's own commit index can
//! lag what earlier leaders committed, and a rule that trusts it re-admits
//! members missing committed entries.
//!
//! # Why it is exact, and only in virtual runs
//!
//! The network calls the node's consensus handlers synchronously (see
//! `net.rs`, `arrive`). On a current-thread runtime nothing else runs until a
//! handler returns, so reading the member before and after the call brackets
//! exactly one handler: a commit index that moved, or a peer that stopped
//! catching up, between the two readings did so in that call. The tally read
//! after the call is the one the leader decided on, because within one handler
//! call a leader's view of its peers can only gain support after the decision
//! (a peer promoted out of catching up), never lose it -- match indices are
//! reset only when leadership begins and when a peer reconnects, each its own
//! call. So the audit can miss an unjustified decision in that one corner, but
//! it cannot accuse a justified one.
//!
//! In a multi-member cluster those handlers are the *only* place a leader's
//! commit index moves or a peer is promoted: the two other commit call sites in
//! the node run only when the member has no peers. Every commit index any
//! member reaches is set inside a handler too -- a follower's by
//! `AppendEntries` or `InstallSnapshot` -- so the highest one ever committed is
//! seen as it happens.
//!
//! On a multi-threaded runtime the bracket means nothing -- another thread
//! can run between the readings -- so the driver installs the audit for virtual
//! runs only.

use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Weak};

use nmos_registry_raft::node::{RaftNode, Role};
use parking_lot::Mutex;

/// The property a commit decision is checked against.
pub const COMMIT_JUSTIFICATION: &str = "Commit Justification";

/// The property a promotion decision is checked against.
pub const PROMOTION_JUSTIFICATION: &str = "Promotion Justification";

/// A member's position just before it handles one delivery.
#[derive(Debug, Clone)]
pub struct Before {
    term: u64,
    commit: u64,
    /// Peers this member, as leader, counted as catching up.
    catching_up: Vec<u64>,
}

/// A decision the rule does not justify.
#[derive(Debug, Clone)]
pub struct Unjustified {
    /// [`COMMIT_JUSTIFICATION`] or [`PROMOTION_JUSTIFICATION`].
    pub property: &'static str,
    /// What was decided, on what evidence.
    pub detail: String,
    /// The index the decision concerns.
    pub index: u64,
    /// The leader's term.
    pub term: u64,
    /// When, on the run's clock.
    pub at_ms: u64,
}

/// A committed index, and the first sighting of it.
#[derive(Debug, Clone, Copy, Default)]
struct Committed {
    index: u64,
    member: u64,
    term: u64,
}

/// The audit. Shared by the network, which brackets deliveries, and the
/// cluster, which says which node currently stands for each member.
#[derive(Default)]
pub struct DecisionAudit {
    nodes: Mutex<BTreeMap<u64, Weak<RaftNode>>>,
    /// The highest index any member has been seen to commit.
    highest: Mutex<Committed>,
    /// Per member: `highest` when its current incarnation was built.
    floors: Mutex<BTreeMap<u64, Committed>>,
    findings: Mutex<Vec<Unjustified>>,
    commits: AtomicU64,
    promotions: AtomicU64,
}

impl DecisionAudit {
    /// An audit with nothing tracked yet.
    #[must_use]
    pub fn new() -> Arc<Self> {
        Arc::new(Self::default())
    }

    /// `node` now stands for `member` -- at build, and again at every restart.
    ///
    /// Also where the member's promotion floor is taken: everything committed
    /// so far may have been committed counting its previous incarnation.
    pub fn track(&self, member: u64, node: &Arc<RaftNode>) {
        let floor = *self.highest.lock();
        self.floors.lock().insert(member, floor);
        self.nodes.lock().insert(member, Arc::downgrade(node));
    }

    fn node(&self, member: u64) -> Option<Arc<RaftNode>> {
        self.nodes.lock().get(&member).and_then(Weak::upgrade)
    }

    fn observe_commit(&self, member: u64, node: &RaftNode) {
        let commit = node.commit_index();
        let mut highest = self.highest.lock();
        if commit > highest.index {
            *highest = Committed {
                index: commit,
                member,
                term: node.log_term_at(commit).unwrap_or(0),
            };
        }
    }

    /// Read `member` before it handles a delivery.
    #[must_use]
    pub fn before(&self, member: u64) -> Option<Before> {
        let node = self.node(member)?;
        self.observe_commit(member, &node);
        let catching_up = if node.role() == Role::Leader {
            (0..node.cluster_size() as u64)
                .filter(|&peer| {
                    node.peer_progress(peer)
                        .is_some_and(|(_, catching_up, _)| catching_up)
                })
                .collect()
        } else {
            Vec::new()
        };
        Some(Before {
            term: node.term(),
            commit: node.commit_index(),
            catching_up,
        })
    }

    /// Read `member` after handling a delivery of `kind` from `from`, and audit
    /// whatever that handler decided.
    pub fn after(&self, member: u64, from: u64, kind: &str, before: &Before, at_ms: u64) {
        let Some(node) = self.node(member) else {
            return;
        };
        self.observe_commit(member, &node);
        if node.role() != Role::Leader || node.term() != before.term {
            return;
        }
        self.audit_commit(&node, from, kind, before, at_ms);
        self.audit_promotions(&node, from, kind, before, at_ms);
    }

    fn audit_commit(&self, node: &RaftNode, from: u64, kind: &str, before: &Before, at_ms: u64) {
        let term = before.term;
        let commit = node.commit_index();
        if commit <= before.commit {
            return;
        }
        self.commits.fetch_add(1, Ordering::Relaxed);

        let size = node.cluster_size() as u64;
        let majority = size / 2 + 1;
        let me = node.index();
        let own_last = node.last_log_index();
        let mut support = u64::from(own_last >= commit);
        let mut tally = format!("m{me} last={own_last} [leader]");
        for peer in (0..size).filter(|&peer| peer != me) {
            match node.peer_progress(peer) {
                Some((matched, catching_up, promote_through)) => {
                    if !catching_up && matched >= commit {
                        support += 1;
                    }
                    let _ = write!(tally, "; m{peer} match={matched}");
                    if catching_up {
                        let _ = write!(tally, " catching-up (bar {promote_through})");
                    }
                }
                None => {
                    let _ = write!(tally, "; m{peer} untracked");
                }
            }
        }
        let entry_term = node.log_term_at(commit);
        if support >= majority && entry_term == Some(term) {
            return;
        }

        let mut detail = format!(
            "m{me} (term {term}) advanced its commit index {} -> {commit} while handling \
             {kind} from m{from}, with {support} of {size} members counted as holding index \
             {commit} (a majority is {majority}): {tally}",
            before.commit,
        );
        match entry_term {
            Some(at) if at == term => {
                let _ = write!(detail, "; entry {commit} is from the current term");
            }
            Some(at) => {
                let _ = write!(
                    detail,
                    "; entry {commit} is from term {at}, not the current term (Raft §5.4.2)"
                );
            }
            None => {
                let _ = write!(detail, "; the leader does not hold entry {commit}");
            }
        }
        self.findings.lock().push(Unjustified {
            property: COMMIT_JUSTIFICATION,
            detail,
            index: commit,
            term,
            at_ms,
        });
    }

    fn audit_promotions(
        &self,
        node: &RaftNode,
        from: u64,
        kind: &str,
        before: &Before,
        at_ms: u64,
    ) {
        let me = node.index();
        for &peer in &before.catching_up {
            let Some((matched, catching_up, bar)) = node.peer_progress(peer) else {
                continue;
            };
            if catching_up {
                continue;
            }
            self.promotions.fetch_add(1, Ordering::Relaxed);
            let floor = self.floors.lock().get(&peer).copied().unwrap_or_default();
            if matched >= floor.index {
                continue;
            }
            self.findings.lock().push(Unjustified {
                property: PROMOTION_JUSTIFICATION,
                detail: format!(
                    "m{me} (term {}) promoted m{peer} back into the electorate while handling \
                     {kind} from m{from}, at match {matched} (bar {bar}, leader commit {}); \
                     but index {} had been committed before m{peer} restarted (first seen \
                     committed by m{}, entry term {}), so m{peer} may be missing it -- and \
                     with its vote, a candidate that lacks it can be elected",
                    before.term,
                    node.commit_index(),
                    floor.index,
                    floor.member,
                    floor.term,
                ),
                index: floor.index,
                term: before.term,
                at_ms,
            });
        }
    }

    /// Every unjustified decision so far, oldest first.
    #[must_use]
    pub fn findings(&self) -> Vec<Unjustified> {
        self.findings.lock().clone()
    }

    /// How many commit decisions were audited, justified or not. A count of
    /// zero over a run that committed anything means the audit saw nothing,
    /// which is what a broken audit looks like.
    #[must_use]
    pub fn commits(&self) -> u64 {
        self.commits.load(Ordering::Relaxed)
    }

    /// How many promotions were audited, justified or not.
    #[must_use]
    pub fn promotions(&self) -> u64 {
        self.promotions.load(Ordering::Relaxed)
    }
}
