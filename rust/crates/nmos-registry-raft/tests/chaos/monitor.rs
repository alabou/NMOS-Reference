// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Raft's safety properties, and ours, checked continuously against a live
//! cluster.
//!
//! The Rust counterpart of `nmos/raft/tests/_invariants.py`. The five Raft
//! properties are Figure 3 of "In Search of an Understandable Consensus
//! Algorithm (Extended Version)", quoted in each check's documentation so the
//! code can be read against the paper without leaving the file; the rest are
//! the Python monitor's additions (local consistency, applied stability,
//! cursor uniqueness) plus four that only make sense here.
//!
//! # Two modes, because a multi-threaded runtime cannot be sampled atomically
//!
//! The Python monitor reads a node's log, term and role at one instant because
//! nothing else runs while it does: one event loop. The Rust node exposes the
//! same readings through accessors that each take the state lock separately,
//! so on a multi-threaded runtime a follower can truncate its log between two
//! of them, and a "digest" stitched from before and after is a log that never
//! existed -- the kind of thing that produces a Log Matching failure against
//! an implementation that did nothing wrong.
//!
//! * [`Mode::Exact`], on a current-thread runtime: a check is synchronous
//!   code between two awaits, so every reading it makes is of one instant,
//!   exactly as in Python. Every property runs after every step.
//! * [`Mode::Robust`], on a multi-threaded runtime: only the properties whose
//!   readings stay truthful however they interleave run per step -- Election
//!   Safety from the node's own `raft: is leader` log line, per-incarnation
//!   monotonicity, applied-within-committed read in the order that makes it
//!   sound, and agreement on *committed* entries, which never change once
//!   committed and so cannot be torn. Everything else runs once the cluster
//!   has converged and nothing is moving.
//!
//! # What is deliberately not checked
//!
//! Full linearizability, for the reason the Python gives: deciding it is
//! NP-hard and the cheap approximations produce false accusations. The
//! client-visible promise that matters most -- an acknowledged write survives
//! -- is checked exactly, at the end, per resource.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fmt::Write as _;

use nmos_registry::registry::Registry;
use nmos_registry_core::resource_type::ResourceType;
use nmos_registry_raft::node::{RaftNode, Role};

use super::cluster::SoakCluster;

/// How much of the cluster can be read atomically.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    /// A current-thread runtime: every reading in a check is of one instant.
    Exact,
    /// A multi-threaded runtime: only interleaving-proof readings per step.
    Robust,
}

/// How much to check.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Depth {
    /// After an ordinary step: the consensus properties the mode allows.
    Step,
    /// Periodically: those, plus the registry-level comparisons, which read
    /// every replica's whole store and are too costly for every step.
    Deep,
    /// Once, with nothing moving: everything, whatever the mode.
    Settled,
}

/// One broken property.
#[derive(Debug, Clone)]
pub struct Violation {
    /// Which property.
    pub property: &'static str,
    /// What was seen.
    pub detail: String,
    /// The log index it concerns, for filtering the forensics.
    pub index: Option<u64>,
    /// The term it concerns.
    pub term: Option<u64>,
    /// When it happened on the run's clock, if that is known more precisely
    /// than "before the check that found it".
    pub at_ms: Option<u64>,
}

impl std::fmt::Display for Violation {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.property, self.detail)
    }
}

fn violation(
    property: &'static str,
    detail: String,
    index: Option<u64>,
    term: Option<u64>,
) -> Violation {
    Violation {
        property,
        detail,
        index,
        term,
        at_ms: None,
    }
}

// -- observation --------------------------------------------------------------

/// What one member looked like when it was read.
#[derive(Debug, Clone)]
pub struct Observed {
    /// Member index.
    pub index: u64,
    /// Start counter.
    pub incarnation: u64,
    /// Role.
    pub role: Role,
    /// Term.
    pub term: u64,
    /// The leader it follows.
    pub leader: Option<u64>,
    /// Whether its vote counts.
    pub voting: bool,
    /// Commit index.
    pub commit: u64,
    /// Applied index.
    pub applied: u64,
    /// Last index covered by its snapshot.
    pub snapshot_index: u64,
    /// That entry's term.
    pub snapshot_term: u64,
    /// Last index held.
    pub last_index: u64,
    /// `(index, term)` for every entry held above the snapshot.
    pub log: Vec<(u64, u64)>,
}

impl Observed {
    /// Read a member through its public diagnostics.
    ///
    /// `applied` is read before `commit`, deliberately: both only grow within
    /// an incarnation, so in that order `applied <= commit` must hold even
    /// when the two readings are separated by other threads' work -- and
    /// seeing it fail therefore means the member really had applied past its
    /// commit index, never that the reading was torn.
    ///
    /// The snapshot boundary is found by bisection: `log_term_at` answers for
    /// the boundary itself and every index above it, and for nothing below.
    #[must_use]
    pub fn of(node: &RaftNode) -> Self {
        let applied = node.last_applied();
        let commit = node.commit_index();
        let role = node.role();
        let term = node.term();
        let last_index = node.last_log_index();
        let (mut low, mut high) = (0u64, last_index);
        while low < high {
            let middle = low + (high - low) / 2;
            if node.log_term_at(middle).is_some() {
                high = middle;
            } else {
                low = middle + 1;
            }
        }
        let snapshot_index = low;
        let snapshot_term = node.log_term_at(snapshot_index).unwrap_or(0);
        let log = (snapshot_index + 1..=last_index)
            .filter_map(|index| node.log_term_at(index).map(|term| (index, term)))
            .collect();
        Self {
            index: node.index(),
            incarnation: node.incarnation(),
            role,
            term,
            leader: node.leader(),
            voting: node.voting(),
            commit,
            applied,
            snapshot_index,
            snapshot_term,
            last_index,
            log,
        }
    }

    /// The term held at `index`, including the snapshot boundary.
    #[must_use]
    pub fn term_at(&self, index: u64) -> Option<u64> {
        if index == self.snapshot_index {
            return Some(self.snapshot_term);
        }
        let first = self.log.first()?.0;
        if index < first {
            return None;
        }
        self.log
            .get(usize::try_from(index - first).ok()?)
            .filter(|&&(at, _)| at == index)
            .map(|&(_, term)| term)
    }

    /// One line for a failure report.
    #[must_use]
    pub fn line(&self) -> String {
        format!(
            "m{} inc={} {:?} term={} leader={:?} voting={} commit={} applied={} log=({}..{}] snapshot=({},t{})",
            self.index,
            self.incarnation,
            self.role,
            self.term,
            self.leader,
            self.voting,
            self.commit,
            self.applied,
            self.snapshot_index,
            self.last_index,
            self.snapshot_index,
            self.snapshot_term,
        )
    }
}

/// A replica's registry, reduced to what every member must agree on.
///
/// Health is excluded -- a heartbeat refreshes it on the owner only, by
/// design -- and so are tombstones, which each member forgets on its own
/// schedule. Everything a client can read is included, cursors too: paging is
/// defined over them, so two members that agree on content but not on cursors
/// would page differently.
pub fn replica_digest(member: &RaftNode, registry: &Registry) -> Vec<String> {
    let mut rows: Vec<String> = registry.with_read_store(|store| {
        ResourceType::ALL
            .iter()
            .flat_map(|&kind| {
                store.iter_extant(kind).map(move |resource| {
                    format!(
                        "{kind} {} v={} c={} u={} parent={:?} body={}",
                        resource.id,
                        resource.version,
                        resource.created,
                        resource.updated,
                        resource.parent_id,
                        resource.body.text(),
                    )
                })
            })
            .collect()
    });
    let nodes: Vec<String> = registry.with_read_store(|store| {
        store
            .iter_extant(ResourceType::Node)
            .map(|node| node.id.clone())
            .collect()
    });
    for node in nodes {
        rows.push(format!("owner {node} = {:?}", member.ownership_of(&node)));
    }
    rows.sort_unstable();
    rows
}

// -- the client's side of the bargain -------------------------------------

/// The anomaly counted when a deleted resource is back at the version of a
/// registration that was undecided (see [`Owed::Absent`]).
pub const REVIVED: &str = "a confirmed delete undone by a registration answered 503 before it";

/// What a client is owed for one resource, at the end of the run.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Owed {
    /// Acknowledged and never since deleted: present everywhere, and when
    /// the version is known, at exactly that version.
    Present(Option<String>),
    /// A confirmed deletion with nothing after it: absent everywhere -- or
    /// present at the version of a registration that is undecided (see
    /// [`Ledger::undecided`]).
    ///
    /// A registration answered 503 was not refused, only not seen through: it
    /// may sit in a stalled connection and commit long after its client moved
    /// on. Committed after the delete, it finds a tombstone, which `prepare`
    /// treats as absent -- re-registering a deleted id is a create -- and
    /// revives the resource at its own version. That history is linearizable:
    /// an operation whose outcome its client never learned may take effect at
    /// any point after it was invoked, including after a later operation
    /// completed. Measured (seed 130051): two registrations answered 503 at
    /// 3.3s and 7.8s reached the leader at 29.3s, after both Nodes' confirmed
    /// deletes at 8.3s and 12.6s, and re-created them.
    ///
    /// Any other version is one no undecided registration carried, so nothing
    /// a client did can explain it, and it stays a violation. `Present` needs
    /// no such allowance: while the record is extant, a late older version is
    /// refused against the newer one (`check_update`, `:102`).
    Absent,
    /// Its fate was left open -- a timeout, a 503, a race the driver made on
    /// purpose, an expiry. Asserting either way would be asserting something
    /// no client was promised.
    Open,
}

/// Every promise made to a client, per resource.
#[derive(Debug, Default)]
pub struct Ledger {
    owed: BTreeMap<(ResourceType, String), Owed>,
    parents: BTreeMap<(ResourceType, String), (ResourceType, String)>,
    /// Per resource, the versions of registrations that may yet commit: each
    /// was answered without its client learning whether it had.
    undecided: BTreeMap<(ResourceType, String), BTreeSet<String>>,
    /// Client-visible oddities that are not safety failures, counted by kind.
    pub anomalies: BTreeMap<&'static str, u64>,
    /// First example of each anomaly.
    pub anomaly_examples: BTreeMap<&'static str, String>,
}

impl Ledger {
    /// A registration was acknowledged at `version`.
    pub fn acknowledged(&mut self, key: (ResourceType, String), version: Option<String>) {
        self.owed.insert(key, Owed::Present(version));
    }

    /// Remember that `child` lives under `parent`, so a cascade can be followed.
    pub fn parent(&mut self, child: (ResourceType, String), parent: (ResourceType, String)) {
        self.parents.insert(child, parent);
    }

    /// A registration of `key` at `version` ended without its client learning
    /// whether it committed. It still may, at any later time -- after a
    /// delete of `key` included.
    pub fn undecided(&mut self, key: &(ResourceType, String), version: &str) {
        self.undecided
            .entry(key.clone())
            .or_default()
            .insert(version.to_owned());
    }

    /// The versions of `key` whose registration is undecided, in order.
    fn undecided_versions(&self, key: &(ResourceType, String)) -> Vec<&str> {
        self.undecided
            .get(key)
            .map(|versions| versions.iter().map(String::as_str).collect())
            .unwrap_or_default()
    }

    /// A deletion was confirmed: the resource and its subtree are owed
    /// absence, bar a revival by an undecided registration (see
    /// [`Owed::Absent`]). Each resource keeps its own undecided versions: a
    /// child revives only if its parent did first, since `prepare` refuses a
    /// registration whose parent is absent.
    pub fn deleted(&mut self, key: &(ResourceType, String)) {
        let keys: Vec<(ResourceType, String)> = self
            .owed
            .keys()
            .filter(|resource| *resource == key || self.descends(resource, key))
            .cloned()
            .collect();
        for resource in keys {
            self.owed.insert(resource, Owed::Absent);
        }
    }

    /// An operation's outcome is unknown: nothing is owed for it, or for
    /// anything beneath it.
    pub fn open(&mut self, key: &(ResourceType, String)) {
        let keys: Vec<(ResourceType, String)> = self
            .owed
            .keys()
            .filter(|resource| *resource == key || self.descends(resource, key))
            .cloned()
            .collect();
        for resource in keys {
            self.owed.insert(resource, Owed::Open);
        }
        self.owed.entry(key.clone()).or_insert(Owed::Open);
    }

    /// An update whose outcome is unknown: still owed presence, version open.
    pub fn update_unknown(&mut self, key: &(ResourceType, String)) {
        if let Some(owed) = self.owed.get_mut(key)
            && matches!(*owed, Owed::Present(_))
        {
            *owed = Owed::Present(None);
        }
    }

    /// What is currently owed for `key`.
    #[must_use]
    pub fn owed(&self, key: &(ResourceType, String)) -> Option<&Owed> {
        self.owed.get(key)
    }

    /// Note something a client could see that is not a safety failure.
    pub fn anomaly(&mut self, kind: &'static str, example: String) {
        *self.anomalies.entry(kind).or_insert(0) += 1;
        self.anomaly_examples.entry(kind).or_insert(example);
    }

    fn descends(
        &self,
        resource: &(ResourceType, String),
        ancestor: &(ResourceType, String),
    ) -> bool {
        let mut at = resource;
        for _ in 0..4 {
            match self.parents.get(at) {
                Some(parent) if parent == ancestor => return true,
                Some(parent) => at = parent,
                None => return false,
            }
        }
        false
    }

    /// How many promises of each kind are outstanding.
    #[must_use]
    pub fn tally(&self) -> (usize, usize, usize) {
        let mut tally = (0, 0, 0);
        for owed in self.owed.values() {
            match *owed {
                Owed::Present(_) => tally.0 += 1,
                Owed::Absent => tally.1 += 1,
                Owed::Open => tally.2 += 1,
            }
        }
        tally
    }
}

// -- the monitor ----------------------------------------------------------------

/// Accumulates history and re-checks every property on demand.
///
/// Stateful for the reason the Python gives: three of Raft's properties are
/// about change over time -- two leaders in one term, a leader that rewrote
/// its own log, an entry committed and then absent from a later leader -- and
/// no check of the present instant can see any of them.
pub struct Monitor {
    /// How much may be read atomically.
    pub mode: Mode,
    /// term -> the member that led it.
    leaders_by_term: BTreeMap<u64, u64>,
    /// How much of the capture's leader record has been folded in.
    leader_events_seen: usize,
    /// (member, term) -> its log while it led that term, for as long as it did.
    leader_logs: HashMap<(u64, u64), Vec<(u64, u64)>>,
    /// index -> (term, first member to report it committed).
    committed: BTreeMap<u64, (u64, u64)>,
    /// index -> the lowest term of any member seen reporting it committed.
    ///
    /// A bound on the term the entry was committed *in*, which is not its own
    /// term: an entry of an earlier term is committed only when a later leader
    /// commits one of its own above it (`commit.rs`, "The current-term
    /// check"). Every member reporting the index committed is in that term or
    /// a later one -- it learned the commit from a leader at least that recent
    /// -- so the true term never exceeds this bound, and equals it whenever the
    /// committing leader itself is seen. Leader Completeness owes the entry to
    /// leaders of later terms than this, and to no others.
    committed_by: BTreeMap<u64, u64>,
    /// index -> (term, first member to apply it).
    applied: BTreeMap<u64, (u64, u64)>,
    /// (member, index) -> term that member applied there.
    applied_by_member: HashMap<(u64, u64), u64>,
    /// (member, incarnation) -> (term, commit, applied) last seen.
    progress: HashMap<(u64, u64), (u64, u64, u64)>,
    /// Checks run.
    pub checks: u64,
    /// Promises made to clients.
    pub ledger: Ledger,
    /// Most recent observation of every member.
    pub last: Vec<Observed>,
}

impl Monitor {
    /// A monitor with no history.
    #[must_use]
    pub fn new(mode: Mode) -> Self {
        Self {
            mode,
            leaders_by_term: BTreeMap::new(),
            leader_events_seen: 0,
            leader_logs: HashMap::new(),
            committed: BTreeMap::new(),
            committed_by: BTreeMap::new(),
            applied: BTreeMap::new(),
            applied_by_member: HashMap::new(),
            progress: HashMap::new(),
            checks: 0,
            ledger: Ledger::default(),
            last: Vec::new(),
        }
    }

    /// Terms that had a leader, for the run report.
    #[must_use]
    pub fn terms_led(&self) -> usize {
        self.leaders_by_term.len()
    }

    /// Highest committed index anyone reported.
    #[must_use]
    pub fn highest_committed(&self) -> u64 {
        self.committed.keys().next_back().copied().unwrap_or(0)
    }

    /// Every member's position, for a failure report.
    #[must_use]
    pub fn render_members(&self) -> String {
        let mut out = String::from("members when last read:\n");
        for observed in &self.last {
            let _ = writeln!(out, "  {}", observed.line());
        }
        out
    }

    /// Run every check the mode allows. `leaders` is the capture's record of
    /// `raft: is leader` lines, as `(member name, term)`.
    ///
    /// # Errors
    ///
    /// The first violation found, in the order that makes the report most
    /// diagnostic: a member contradicting itself before two members
    /// contradicting each other.
    pub fn check(
        &mut self,
        cluster: &SoakCluster,
        leaders: &[(String, u64)],
        depth: Depth,
    ) -> Result<(), Violation> {
        let observed = self.observe(cluster);
        let consensus = self.mode == Mode::Exact || depth == Depth::Settled;
        let registry = consensus && depth != Depth::Step;

        self.fold_leader_events(cluster, leaders)?;
        self.local_consistency(&observed)?;
        self.monotonicity(&observed)?;
        self.fold_committed(&observed)?;
        if consensus {
            self.fold_sampled_leaders(&observed)?;
            self.leader_append_only(&observed)?;
            self.log_matching(&observed)?;
            self.leader_completeness(&observed)?;
            self.applied_stability(&observed)?;
            self.state_machine_safety(&observed)?;
            Self::snapshot_recoverability(cluster, &observed)?;
        }
        if registry {
            self.replica_equality(cluster, &observed)?;
            self.store_integrity(cluster)?;
            self.cursor_uniqueness(cluster)?;
        }
        Ok(())
    }

    /// The checks that survive a blown amnesia budget.
    ///
    /// Once a quorum has forgotten, committed entries may genuinely be gone
    /// and re-committed differently, so every property about committed or
    /// applied history is off. What is still owed: one leader per term -- the
    /// term file survives every restart -- and no member contradicting itself.
    ///
    /// # Errors
    ///
    /// The first violation found.
    pub fn check_amnesiac(
        &mut self,
        cluster: &SoakCluster,
        leaders: &[(String, u64)],
    ) -> Result<(), Violation> {
        let observed = self.observe(cluster);
        self.fold_leader_events(cluster, leaders)?;
        self.local_consistency(&observed)?;
        self.monotonicity(&observed)
    }

    fn observe(&mut self, cluster: &SoakCluster) -> Vec<Observed> {
        self.checks += 1;
        let observed: Vec<Observed> = cluster
            .members
            .iter()
            .map(|member| Observed::of(&member.node))
            .collect();
        self.last.clone_from(&observed);
        observed
    }

    /// Every member can hand on what it has discarded.
    ///
    /// Not one of Figure 3's properties: the invariant this backend's
    /// compaction rests on. A member whose log starts after index 0 holds the
    /// entries below that point only as its snapshot, so a leader without one
    /// can reach a follower that needs them with nothing but keepalives -- the
    /// chaos soak's S3, which stranded followers for the rest of a run. etcd
    /// treats the same condition as impossible (`raft.go:680-682`: "need
    /// non-empty snapshot").
    ///
    /// Exact where it runs: the boundary and the snapshot are read one after
    /// the other, and on a current-thread runtime nothing moves in between.
    fn snapshot_recoverability(
        cluster: &SoakCluster,
        observed: &[Observed],
    ) -> Result<(), Violation> {
        for (member, seen) in cluster.members.iter().zip(observed) {
            if seen.snapshot_index == 0 {
                continue;
            }
            match member.node.snapshot_held() {
                Some((through, bytes)) if through >= seen.snapshot_index && bytes > 0 => {}
                held => {
                    return Err(violation(
                        "Snapshot Recoverability",
                        format!(
                            "m{} has discarded its log through {} but holds {held:?} to serve \
                             it from: a follower that needs those entries can be sent nothing \
                             but keepalives",
                            seen.index, seen.snapshot_index,
                        ),
                        Some(seen.snapshot_index),
                        None,
                    ));
                }
            }
        }
        Ok(())
    }

    /// Election Safety, from the node's own announcements.
    ///
    /// "at most one leader can be elected in a given term." (Figure 3)
    ///
    /// Exact in both modes: the line is logged inside `become_leader`, under
    /// the lock, once per election won.
    fn fold_leader_events(
        &mut self,
        cluster: &SoakCluster,
        leaders: &[(String, u64)],
    ) -> Result<(), Violation> {
        for (name, term) in leaders.iter().skip(self.leader_events_seen) {
            let Some(member) = cluster.index_of(name) else {
                continue;
            };
            match self.leaders_by_term.get(term) {
                Some(&incumbent) if incumbent != member => {
                    return Err(violation(
                        "Election Safety",
                        format!(
                            "term {term} was won by member {incumbent} and again by member {member} \
                             (both logged `raft: is leader`)"
                        ),
                        None,
                        Some(*term),
                    ));
                }
                _ => {
                    self.leaders_by_term.insert(*term, member);
                }
            }
        }
        self.leader_events_seen = leaders.len();
        Ok(())
    }

    /// The same property from sampled roles, as the Python checks it.
    fn fold_sampled_leaders(&mut self, observed: &[Observed]) -> Result<(), Violation> {
        for member in observed.iter().filter(|m| m.role == Role::Leader) {
            match self.leaders_by_term.get(&member.term) {
                Some(&incumbent) if incumbent != member.index => {
                    return Err(violation(
                        "Election Safety",
                        format!(
                            "term {} has been led by both member {incumbent} and member {}",
                            member.term, member.index
                        ),
                        None,
                        Some(member.term),
                    ));
                }
                _ => {
                    self.leaders_by_term.insert(member.term, member.index);
                }
            }
        }
        Ok(())
    }

    /// A member's commit and applied indices must be justifiable.
    ///
    /// The two assertions `go.etcd.io/raft` makes about one member's log,
    /// which the node also makes of itself in `check_applied_within_committed`
    /// -- where a failure is only logged. Here it fails the run on the step
    /// it happens.
    fn local_consistency(&self, observed: &[Observed]) -> Result<(), Violation> {
        for member in observed {
            let reachable = member.last_index.max(member.snapshot_index);
            if self.mode == Mode::Exact && member.commit > reachable {
                return Err(violation(
                    "Local Consistency",
                    format!(
                        "member {} has commit_index {} beyond anything it holds: {}",
                        member.index,
                        member.commit,
                        member.line()
                    ),
                    Some(member.commit),
                    None,
                ));
            }
            if member.applied > member.commit {
                return Err(violation(
                    "Local Consistency",
                    format!(
                        "member {} applied through {} but has only committed through {}",
                        member.index, member.applied, member.commit
                    ),
                    Some(member.applied),
                    None,
                ));
            }
        }
        Ok(())
    }

    /// Within one incarnation, term, commit and applied only ever grow.
    ///
    /// Not a Figure 3 property, and implied by all of them: a commit index
    /// that moves backwards un-commits, an applied index that moves backwards
    /// un-applies, and a term that moves backwards is the double vote the term
    /// file exists to prevent. A restart legitimately resets commit and
    /// applied, which is why the key includes the incarnation.
    fn monotonicity(&mut self, observed: &[Observed]) -> Result<(), Violation> {
        for member in observed {
            let key = (member.index, member.incarnation);
            if let Some(&(term, commit, applied)) = self.progress.get(&key) {
                let (property, was, now) = if member.term < term {
                    ("term", term, member.term)
                } else if member.commit < commit {
                    ("commit index", commit, member.commit)
                } else if member.applied < applied {
                    ("applied index", applied, member.applied)
                } else {
                    ("", 0, 0)
                };
                if !property.is_empty() {
                    return Err(violation(
                        "Monotonicity",
                        format!(
                            "member {} (incarnation {}) moved its {property} backwards from {was} to {now}",
                            member.index, member.incarnation
                        ),
                        Some(now),
                        None,
                    ));
                }
            }
            self.progress
                .insert(key, (member.term, member.commit, member.applied));
        }
        Ok(())
    }

    /// Record committed entries, and require every report of one to agree.
    ///
    /// A member's commit index is a claim that everything up to it is
    /// committed, and committed is permanent -- so the terms recorded here
    /// are what every future leader must still carry, and a second report of
    /// the same index with a different term is two members disagreeing about
    /// what is committed. Sound in both modes, because a committed entry never
    /// changes: a torn reading can only miss one, never misreport it.
    ///
    /// Public so the guards can replay a decoded history into it.
    pub fn fold_committed(&mut self, observed: &[Observed]) -> Result<(), Violation> {
        for member in observed {
            let top = member.commit.min(member.last_index);
            for &(index, term) in member.log.iter().take_while(|&&(index, _)| index <= top) {
                self.committed_by
                    .entry(index)
                    .and_modify(|by| *by = (*by).min(member.term))
                    .or_insert(member.term);
                match self.committed.get(&index) {
                    Some(&(known, witness)) if known != term => {
                        return Err(violation(
                            "Committed Agreement",
                            format!(
                                "index {index} is committed as term {known} by member {witness} \
                                 and as term {term} by member {}",
                                member.index
                            ),
                            Some(index),
                            Some(term),
                        ));
                    }
                    Some(_) => {}
                    None => {
                        self.committed.insert(index, (term, member.index));
                    }
                }
            }
        }
        Ok(())
    }

    /// "a leader never overwrites or deletes entries in its log; it only
    /// appends new entries." (Figure 3)
    fn leader_append_only(&mut self, observed: &[Observed]) -> Result<(), Violation> {
        let leading: BTreeSet<(u64, u64)> = observed
            .iter()
            .filter(|m| m.role == Role::Leader)
            .map(|m| (m.index, m.term))
            .collect();
        // A record is only ever compared while its member still leads its
        // term, so the rest can go: a long run elects thousands of leaders.
        self.leader_logs.retain(|key, _| leading.contains(key));
        for member in observed.iter().filter(|m| m.role == Role::Leader) {
            let key = (member.index, member.term);
            if let Some(before) = self.leader_logs.get(&key) {
                let lowest = member.log.first().map_or(u64::MAX, |&(index, _)| index);
                for &(index, term) in before.iter().filter(|&&(index, _)| index >= lowest) {
                    match member.term_at(index) {
                        Some(now) if now != term => {
                            return Err(violation(
                                "Leader Append-Only",
                                format!(
                                    "member {}, still leader in term {}, changed index {index} \
                                     from term {term} to term {now}",
                                    member.index, member.term
                                ),
                                Some(index),
                                Some(member.term),
                            ));
                        }
                        _ => {}
                    }
                }
                let previous_last = before.last().map_or(0, |&(index, _)| index);
                if member.last_index < previous_last {
                    return Err(violation(
                        "Leader Append-Only",
                        format!(
                            "member {}, still leader in term {}, shortened its log from index \
                             {previous_last} to {}",
                            member.index, member.term, member.last_index
                        ),
                        Some(previous_last),
                        Some(member.term),
                    ));
                }
            }
            self.leader_logs.insert(key, member.log.clone());
        }
        Ok(())
    }

    /// "if two logs contain an entry with the same index and term, then the
    /// logs are identical in all entries up through the given index."
    /// (Figure 3)
    fn log_matching(&self, observed: &[Observed]) -> Result<(), Violation> {
        for (position, left) in observed.iter().enumerate() {
            for right in observed.iter().skip(position + 1) {
                let a: BTreeMap<u64, u64> = left.log.iter().copied().collect();
                let b: BTreeMap<u64, u64> = right.log.iter().copied().collect();
                let highest_agreeing = a
                    .iter()
                    .filter(|&(index, term)| b.get(index) == Some(term))
                    .map(|(&index, _)| index)
                    .next_back();
                let Some(highest) = highest_agreeing else {
                    continue;
                };
                for (&index, &term) in a.range(..=highest) {
                    if let Some(&other) = b.get(&index)
                        && other != term
                    {
                        return Err(violation(
                            "Log Matching",
                            format!(
                                "members {} and {} agree at index {highest} (term {}) but differ \
                                 at index {index}: terms {term} vs {other}",
                                left.index,
                                right.index,
                                a.get(&highest).copied().unwrap_or(0),
                            ),
                            Some(index),
                            Some(term),
                        ));
                    }
                }
            }
        }
        Ok(())
    }

    /// "if a log entry is committed in a given term, then that entry will be
    /// present in the logs of the leaders for all higher-numbered terms."
    /// (Figure 3)
    ///
    /// "Committed in a given term" is the term of the leader that committed
    /// it -- bounded by `committed_by` -- not the entry's own term. The two
    /// differ for an entry committed indirectly, and taking one for the other
    /// held leaders to entries committed after they were elected: seed 141387
    /// (7 of 24 runs) flagged a cut-off leader of term 12 for lacking an entry
    /// of term 11 that was committed only in term 13.
    ///
    /// Public so the guards can replay a decoded history into it.
    pub fn leader_completeness(&self, observed: &[Observed]) -> Result<(), Violation> {
        for member in observed.iter().filter(|m| m.role == Role::Leader) {
            // Entries inside the leader's snapshot are present by
            // construction, so only those above it are compared.
            for (&index, &(term, witness)) in self.committed.range(member.snapshot_index + 1..) {
                if self
                    .committed_by
                    .get(&index)
                    .is_none_or(|&by| by >= member.term)
                {
                    // Committed in this leader's term or a later one, as far
                    // as anything seen can tell -- so not owed to it. Subsumes
                    // the entry's own term: nothing is committed before it
                    // exists.
                    continue;
                }
                if index > member.last_index {
                    return Err(violation(
                        "Leader Completeness",
                        format!(
                            "member {} leads term {} without index {index}, committed earlier in \
                             term {term} (first reported by member {witness})\n{}",
                            member.index,
                            member.term,
                            self.members_at(observed, index)
                        ),
                        Some(index),
                        Some(member.term),
                    ));
                }
                if member.term_at(index) != Some(term) {
                    return Err(violation(
                        "Leader Completeness",
                        format!(
                            "member {} leads term {} holding term {:?} at index {index}, which \
                             was committed in term {term} (first reported by member {witness})\n{}",
                            member.index,
                            member.term,
                            member.term_at(index),
                            self.members_at(observed, index)
                        ),
                        Some(index),
                        Some(member.term),
                    ));
                }
            }
        }
        Ok(())
    }

    /// A member never changes an entry it has already applied.
    ///
    /// Checked before State Machine Safety for the Python's reason: it names
    /// the member that moved, where the other names only the pair that now
    /// disagree.
    fn applied_stability(&mut self, observed: &[Observed]) -> Result<(), Violation> {
        for member in observed {
            let top = member.applied.min(member.last_index);
            for &(index, term) in member.log.iter().take_while(|&&(index, _)| index <= top) {
                match self.applied_by_member.get(&(member.index, index)) {
                    Some(&was) if was != term => {
                        return Err(violation(
                            "Applied Stability",
                            format!(
                                "member {} applied term {was} at index {index} and now holds term \
                                 {term} there\n{}",
                                member.index,
                                self.members_at(observed, index)
                            ),
                            Some(index),
                            Some(term),
                        ));
                    }
                    Some(_) => {}
                    None => {
                        self.applied_by_member.insert((member.index, index), term);
                    }
                }
            }
        }
        Ok(())
    }

    /// "if a server has applied a log entry at a given index to its state
    /// machine, no other server will ever apply a different log entry for the
    /// same index." (Figure 3)
    ///
    /// Entries are compared by term, which identifies an entry uniquely while
    /// Election Safety and Leader Append-Only hold -- only one leader creates
    /// entries in a term, and it never rewrites one. Payloads the accessors do
    /// not expose are covered instead by [`Self::replica_equality`], which
    /// compares what the entries *did*.
    fn state_machine_safety(&mut self, observed: &[Observed]) -> Result<(), Violation> {
        for member in observed {
            let top = member.applied.min(member.last_index);
            for &(index, term) in member.log.iter().take_while(|&&(index, _)| index <= top) {
                match self.applied.get(&index) {
                    Some(&(known, witness)) if known != term => {
                        return Err(violation(
                            "State Machine Safety",
                            format!(
                                "index {index} applied as term {known} by member {witness} and as \
                                 term {term} by member {}\n{}",
                                member.index,
                                self.members_at(observed, index)
                            ),
                            Some(index),
                            Some(term),
                        ));
                    }
                    Some(_) => {}
                    None => {
                        self.applied.insert(index, (term, member.index));
                    }
                }
            }
        }
        Ok(())
    }

    /// Two members that have applied the same prefix hold the same registry.
    ///
    /// Not in the Python, and the strongest check here: apply is deterministic
    /// by design -- cursors, health and the ownership claim all travel inside
    /// the entry precisely so that it is -- so equal `last_applied` must mean
    /// byte-identical client-visible state. It catches what comparing terms
    /// cannot: an entry applied differently, applied twice, skipped, or
    /// installed from a snapshot that does not describe its own index.
    fn replica_equality(
        &self,
        cluster: &SoakCluster,
        observed: &[Observed],
    ) -> Result<(), Violation> {
        let mut by_applied: BTreeMap<u64, Vec<usize>> = BTreeMap::new();
        for (position, member) in observed.iter().enumerate() {
            by_applied.entry(member.applied).or_default().push(position);
        }
        for (applied, positions) in by_applied {
            if positions.len() < 2 {
                continue;
            }
            let digests: Vec<Vec<String>> = positions
                .iter()
                .map(|&position| {
                    let member = &cluster.members[position];
                    replica_digest(&member.node, &member.registry)
                })
                .collect();
            for (offset, digest) in digests.iter().enumerate().skip(1) {
                if *digest != digests[0] {
                    let first = observed[positions[0]].index;
                    let other = observed[positions[offset]].index;
                    let left: BTreeSet<&String> = digests[0].iter().collect();
                    let right: BTreeSet<&String> = digest.iter().collect();
                    let only_left: Vec<&&String> = left.difference(&right).take(6).collect();
                    let only_right: Vec<&&String> = right.difference(&left).take(6).collect();
                    return Err(violation(
                        "Replica Equality",
                        format!(
                            "members {first} and {other} have both applied through {applied} but \
                             hold different registries ({} vs {} rows)\n  only on m{first}: \
                             {only_left:?}\n  only on m{other}: {only_right:?}",
                            digests[0].len(),
                            digest.len(),
                        ),
                        Some(applied),
                        None,
                    ));
                }
            }
        }
        Ok(())
    }

    /// The store's own structural checks, on every replica.
    fn store_integrity(&self, cluster: &SoakCluster) -> Result<(), Violation> {
        for member in &cluster.members {
            let verdict = member
                .registry
                .with_read_store(|store| store.check_children().and(store.check_indexes()));
            if let Err(problem) = verdict {
                return Err(violation(
                    "Store Integrity",
                    format!("member {}: {problem}", member.index),
                    None,
                    None,
                ));
            }
        }
        Ok(())
    }

    /// Ours, not Raft's: no two resources of one type share a cursor, on any
    /// member. Per type, because that is the uniqueness rule the allocator
    /// documents (`cursors.rs`).
    fn cursor_uniqueness(&self, cluster: &SoakCluster) -> Result<(), Violation> {
        let mut seen: HashMap<(ResourceType, String), (u64, String)> = HashMap::new();
        for member in &cluster.members {
            let rows: Vec<(ResourceType, String, String)> =
                member.registry.with_read_store(|store| {
                    ResourceType::ALL
                        .iter()
                        .flat_map(|&kind| {
                            store
                                .iter_extant(kind)
                                .map(move |r| (kind, r.updated.to_string(), r.id.clone()))
                        })
                        .collect()
                });
            for (kind, cursor, id) in rows {
                match seen.get(&(kind, cursor.clone())) {
                    Some((owner_member, owner)) if *owner != id => {
                        return Err(violation(
                            "Cursor Uniqueness",
                            format!(
                                "{kind} cursor {cursor} is held by {owner} (member {owner_member}) \
                                 and by {id} (member {})",
                                member.index
                            ),
                            None,
                            None,
                        ));
                    }
                    Some(_) => {}
                    None => {
                        seen.insert((kind, cursor), (member.index, id));
                    }
                }
            }
        }
        Ok(())
    }

    /// Every member's position relative to one index.
    fn members_at(&self, observed: &[Observed], index: u64) -> String {
        let mut out = String::from("  -- members at this index --");
        for member in observed {
            let _ = write!(
                out,
                "\n    {} term_at({index})={:?}",
                member.line(),
                member.term_at(index)
            );
        }
        out
    }

    // -- the end of the run ------------------------------------------------

    /// Every promise to a client, checked against every replica.
    ///
    /// Run once, at the end: it is a claim about the settled state, and asking
    /// it mid-churn would only measure how far replication had got. When the
    /// cluster did *not* settle, only an acknowledged write that no replica
    /// holds at all is reported -- that one is lost, however long anyone waits.
    ///
    /// A deleted resource revived by a registration that was undecided at the
    /// delete breaks no promise (see [`Owed::Absent`]) but is something a
    /// client sees -- a Node it deleted, back -- so it is counted as an anomaly,
    /// once per resource, and the run can say how often it happened.
    pub fn check_promises(&mut self, cluster: &SoakCluster, converged: bool) -> Vec<Violation> {
        let mut found = Vec::new();
        if !converged {
            for (key, owed) in &self.ledger.owed {
                if !matches!(*owed, Owed::Present(_)) {
                    continue;
                }
                let anywhere = cluster.members.iter().any(|member| {
                    member
                        .registry
                        .with_read_store(|store| store.get(key.0, &key.1).is_some())
                });
                if !anywhere {
                    found.push(violation(
                        "Acknowledged Write Durability",
                        format!(
                            "acknowledged {} {} is held by no member at all (the cluster did not \
                             converge, so only total loss is reported)",
                            key.0, key.1
                        ),
                        None,
                        None,
                    ));
                }
            }
            return found;
        }
        let mut revived = Vec::new();
        'promises: for (key, owed) in &self.ledger.owed {
            let (kind, id) = (key.0, &key.1);
            let mut came_back = None;
            for member in &cluster.members {
                let held = member
                    .registry
                    .with_read_store(|store| store.get(kind, id).map(|r| r.version.clone()));
                let broken = match (owed, held.as_deref()) {
                    (Owed::Present(_), None) => Some(format!(
                        "acknowledged {kind} {id} is absent on member {}",
                        member.index
                    )),
                    (Owed::Present(Some(version)), Some(held)) if held != version.as_str() => {
                        Some(format!(
                            "{kind} {id} was last acknowledged at version {version} but member {} \
                             holds version {held}",
                            member.index
                        ))
                    }
                    (Owed::Absent, Some(held)) => {
                        let undecided = self.ledger.undecided_versions(key);
                        if undecided.contains(&held) {
                            came_back = Some(held.to_owned());
                            None
                        } else if undecided.is_empty() {
                            Some(format!(
                                "{kind} {id} was deleted (confirmed) but member {} still holds \
                                 version {held}",
                                member.index
                            ))
                        } else {
                            Some(format!(
                                "{kind} {id} was deleted (confirmed) but member {} holds version \
                                 {held}; only an undecided registration could have revived it, \
                                 and those were at {}",
                                member.index,
                                undecided.join(", ")
                            ))
                        }
                    }
                    _ => None,
                };
                if let Some(detail) = broken {
                    found.push(violation(
                        "Acknowledged Write Durability",
                        detail,
                        None,
                        None,
                    ));
                    if found.len() >= 25 {
                        break 'promises;
                    }
                }
            }
            if let Some(version) = came_back {
                revived.push(format!("{kind} {id} back at version {version}"));
            }
        }
        for example in revived {
            self.ledger.anomaly(REVIVED, example);
        }
        found
    }
}
