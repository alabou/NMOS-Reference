// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The consensus core: roles, elections, replication, and the commit rule.
//!
//! Port of `nmos/raft/node.py`. Raft as described in the paper, with one
//! deliberate departure that the rest of this package exists to make safe.
//!
//! # The departure, and why it is needed
//!
//! The log is not durable. Raft's election-safety argument depends on it being
//! durable, in a way that is easy to miss: the up-to-dateness check in
//! `RequestVote` is what stops a candidate missing a committed entry from being
//! elected, and it works because a voter that *has* the entry refuses. Take
//! away the voter's log and it refuses nothing.
//!
//! > A is leader in term 5 and replicates entry E to B. Quorum {A, B} commits
//! > it, the client is told 201. B restarts: it recovers its term from
//! > [`crate::persist`], but its log is empty. C -- which never received E --
//! > times out and campaigns in term 6. B's log is empty, so every candidate
//! > looks up to date, and B votes for C. C wins with {B, C}, and C's log has
//! > no E.
//!
//! An acknowledged registration, lost to a single non-simultaneous failure. A
//! rolling restart -- this design's upgrade and resize procedure -- is that
//! scenario once per member.
//!
//! The fix: a member that has ever acknowledged entries does not vote until it
//! has been caught up and explicitly promoted. While non-voting it grants no
//! votes, starts no elections, and reports `catching_up` so the leader does not
//! count its acknowledgements toward a commit.
//!
//! # Why "ever acknowledged" and not "has restarted"
//!
//! A member starting for the very first time (`incarnation == 1`) has never
//! acknowledged anything, so the intersection argument still protects it: any
//! majority that could elect a leader contains a member that holds every
//! committed entry, and that member refuses. Making a *fresh* member non-voting
//! would deadlock a cold start -- every member of a new cluster would be
//! waiting for a promotion from a leader that can never be elected.
//!
//! The distinction is exactly right, and it is why [`crate::persist`] counts
//! starts rather than storing a boolean.
//!
//! # Apply is bounded
//!
//! [`crate::machine::StateMachine::apply`] is synchronous from first mutation
//! to last grain, because the store's no-await invariant depends on it. The
//! other half of that bargain is here: apply a bounded run, yield between runs,
//! never inside one. A 50,000-entry catch-up applied in one block would stall
//! the HTTP server and the heartbeat timer -- and a stalled heartbeat timer
//! causes an election, which causes more catch-up.
//!
//! # What holds the state
//!
//! One `parking_lot::Mutex<NodeState>`, and every handler takes it, does its
//! work, and drops it without awaiting. That is the same property the Python
//! gets from a single-threaded event loop: `on_request_vote` reads the term and
//! records a vote with nothing able to interleave, which is what stops a member
//! voting twice in one term.
//!
//! The two handlers that *are* async -- `on_propose` and `on_forward` -- are
//! not consensus messages. They take the lock, finish with it, and only then
//! await.

#![expect(
    clippy::significant_drop_tightening,
    reason = "This module's shape IS one acquisition per decision. A consensus \
              decision reads several fields and writes several more, and \
              releasing between them is what lets a term change land halfway \
              through -- which is how a member votes twice in one term, or \
              replicates one term's entries to half the cluster and another's \
              to the rest. The lint asks for the guard's life to be minimised; \
              here its life is the decision, deliberately, and shortening it \
              would reintroduce exactly the interleaving the Python avoids by \
              running on one event loop."
)]

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::sync::Arc;
use std::time::Duration;

use nmos_registry::fence::RevisionFence;
use nmos_registry::registry::Registry;
use parking_lot::Mutex;
use tokio::sync::oneshot;
use tokio::time::Instant;

use crate::batcher::{MAX_BATCH, Pending, ProposalBatcher, ProposalDrain, proposal_channel};
use crate::cluster::RaftLayout;
use crate::commit::{follower_commit_index, last_new_index, leader_commit_index};
use crate::errors::RaftInvariantViolated;
use crate::log::{Entry, RaftLog};
use crate::machine::{Outcome, StateMachine};
use crate::messages::{
    AppendEntries, AppendEntriesReply, InstallSnapshot, InstallSnapshotReply, Message, Promote,
    RequestVote, RequestVoteReply, WireEntry,
};
use crate::operations::{Operation, OperationKind, ProposalId};
use crate::persist::{PersistentState, TermStore};
use crate::snapshot::SnapshotMeta;
use crate::transport::{RaftUnavailable, Transport};
use nmos_registry_core::cursor::TaiCursor;
use nmos_registry_core::resource_type::ResourceType;

/// Proposal ids one incarnation may mint before treading on the next's range.
///
/// Four billion is far beyond what any member will issue between restarts, and
/// the id is a varint on the wire, so the larger numbers cost a couple of bytes
/// per entry and nothing else.
pub const PROPOSALS_PER_INCARNATION: u64 = 1 << 32;

/// Where a member stands in the algorithm.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Role {
    /// Following, or waiting to time out.
    Follower,
    /// Asking whether it *would* win, without having claimed a term.
    ///
    /// Raft §9.6. A pre-candidate has incremented nothing and persisted
    /// nothing, so a member that has lost contact can discover it would lose
    /// without forcing a term increment on a cluster that is working. It also,
    /// by having given up on its leader, stops refusing votes on that leader's
    /// behalf -- which is what lets a quorum of pre-candidates replace a leader
    /// that really has died.
    PreCandidate,
    /// Standing in a term it has claimed.
    Candidate,
    /// Leading.
    Leader,
}

/// Timings, all injectable so tests can compress them.
///
/// The election window must be comfortably larger than the heartbeat interval,
/// or a healthy leader's heartbeats race its followers' timers and the cluster
/// churns leadership under no load at all. The randomised range is what stops
/// every follower campaigning in the same instant and splitting the vote
/// forever.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RaftTiming {
    /// How often a leader replicates.
    pub heartbeat_ms: u64,
    /// The shortest an election timeout may be.
    pub election_min_ms: u64,
    /// The longest.
    pub election_max_ms: u64,
    /// Entries in one `AppendEntries`.
    pub max_entries_per_append: usize,
    /// Entries applied in one uninterrupted run.
    pub max_apply_batch: usize,
    /// Applied entries held before a snapshot is taken.
    pub compaction_threshold: u64,
    /// Hard cap.
    ///
    /// Beyond it the log is compacted even though a follower still needs the
    /// entries -- that follower is caught up by a snapshot instead. Without a
    /// cap, one unreachable member makes the log grow without bound, which
    /// turns a partial outage into an out-of-memory failure.
    pub max_log_entries: u64,
    /// Bytes of snapshot per chunk.
    pub snapshot_chunk: usize,
}

impl Default for RaftTiming {
    fn default() -> Self {
        Self {
            heartbeat_ms: 50,
            election_min_ms: 300,
            election_max_ms: 600,
            max_entries_per_append: 256,
            max_apply_batch: 128,
            compaction_threshold: 4096,
            max_log_entries: 65536,
            snapshot_chunk: 1 << 20,
        }
    }
}

impl RaftTiming {
    /// A randomised election timeout within the configured window.
    ///
    /// Randomness from OpenSSL rather than a new dependency. The quality is
    /// irrelevant -- this only has to spread timeouts so two followers do not
    /// campaign in the same instant -- but the dependency count is not, and
    /// this workspace already links one crypto library on purpose.
    #[must_use]
    pub fn election_timeout(&self) -> Duration {
        let span = self.election_max_ms.saturating_sub(self.election_min_ms);
        if span == 0 {
            return Duration::from_millis(self.election_min_ms);
        }
        let mut bytes = [0u8; 8];
        // A failed draw falls back to the middle of the window rather than to
        // a constant end of it: every member picking the minimum together is
        // the split vote this randomisation exists to avoid.
        let offset = if openssl::rand::rand_bytes(&mut bytes).is_ok() {
            u64::from_le_bytes(bytes).checked_rem(span).unwrap_or(0)
        } else {
            span / 2
        };
        Duration::from_millis(self.election_min_ms.saturating_add(offset))
    }
}

/// What a leader tracks about one follower.
#[derive(Debug, Clone)]
struct PeerState {
    next_index: u64,
    match_index: u64,
    up: bool,
    incarnation: u64,
    /// The peer says its acknowledgements must not count yet.
    ///
    /// Authoritative from the peer's own reply rather than inferred from the
    /// incarnation the leader happened to see: the peer knows whether it has
    /// been promoted, and a leader that had to remember incarnations across its
    /// own restarts would get this wrong exactly when it matters.
    catching_up: bool,
    /// Index this peer must reach before its vote counts again.
    promote_through: u64,
    /// Highest index sent and not yet acknowledged.
    ///
    /// Flow control, and the reason for it is measurable. `next_index` only
    /// advances on a reply, so without this the leader re-sends the *same*
    /// window on every heartbeat until the peer answers. A healthy peer answers
    /// within a tick and the cost is nil -- measured at x1.0 -- but a peer one
    /// RTT behind receives the window once per heartbeat for as long as the
    /// round trip takes: measured at **x15** against a 0.5 s link and a 20 ms
    /// heartbeat, and it scales with RTT / heartbeat.
    pending_through: u64,
    /// Correlation id of the outstanding append.
    ///
    /// Needed because "still in flight" and "lost" are otherwise
    /// indistinguishable: a heartbeat sent meanwhile draws a reply whose
    /// `match_index` is still behind, which looks exactly like loss and would
    /// retransmit for the same reason the pause exists to avoid.
    pending_request: u64,
    /// When `pending_through` was sent, so a genuinely lost append -- or a lost
    /// reply -- is still retransmitted rather than waited on forever.
    pending_since: Option<Instant>,
    /// How much of the snapshot this peer has confirmed receiving.
    snapshot_offset: usize,
    /// The commit index last *sent* to this peer.
    ///
    /// `go.etcd.io/raft` calls this `sentCommit` and gates an eager send on it
    /// (`tracker/progress.go:189`, `CanBumpCommit`). Without it the leader
    /// either re-sends the same commit index to a peer that already has it, or
    /// -- which is what happened here -- never sends it at all until the next
    /// heartbeat.
    sent_commit: u64,
    /// A chunk is out and unanswered.
    ///
    /// Without this the transfer is driven from two places at once -- the
    /// replication tick and the previous chunk's reply -- so two chunks go out
    /// carrying the same offset, the follower sees the second as out of order,
    /// restarts from zero, and the pair loop forever making no progress.
    snapshot_in_flight: bool,
    /// Correlation ids at or below this belong to a superseded exchange.
    ///
    /// Set to the current value of the node-wide append sequence whenever this
    /// leader's view of the peer is reset -- on becoming leader, and on a
    /// reconnect. Every send made afterwards is minted above it, so a reply at
    /// or below it was drawn by a send this leader has since disowned.
    ///
    /// It exists because nothing else identifies one. A reply carries no
    /// incarnation, and an append reply is not a correlated request whose
    /// future the transport fails when the link drops, so a reply from the
    /// incarnation that has just been replaced arrives looking exactly like a
    /// current one -- and is then applied to the member that replaced it.
    reply_floor: u64,
    /// When this peer last *answered*, which is what check-quorum runs on.
    ///
    /// `go.etcd.io/raft` keeps the same evidence as `RecentActive`, set only on
    /// a reply (`raft.go:1388`, `:1580`) and cleared for every peer once an
    /// election interval (`raft.go:1286`); `QuorumActive` then asks whether a
    /// majority answered inside that window. A timestamp rather than a
    /// flag-and-sweep, which is the same question asked without a second timer.
    ///
    /// The distinction from `up` is the point. `up` is the TCP link, and a peer
    /// whose process is stopped, deadlocked or stalled holds its socket open
    /// for minutes: the kernel keeps the connection and nothing errors until
    /// retransmits give up. Counting such a peer toward the quorum is how a
    /// leader goes on answering as leader, and accepting writes that can never
    /// commit, while a majority of the cluster is not actually there.
    last_heard_at: Option<Instant>,
}

impl Default for PeerState {
    fn default() -> Self {
        Self {
            next_index: 1,
            match_index: 0,
            up: false,
            incarnation: 0,
            catching_up: false,
            promote_through: 0,
            pending_through: 0,
            pending_request: 0,
            pending_since: None,
            sent_commit: 0,
            snapshot_offset: 0,
            snapshot_in_flight: false,
            reply_floor: 0,
            last_heard_at: None,
        }
    }
}

/// Everything one member holds, behind one lock.
pub(crate) struct NodeState {
    pub(crate) term: u64,
    pub(crate) voted_for: Option<u64>,
    pub(crate) incarnation: u64,
    pub(crate) role: Role,
    pub(crate) leader: Option<u64>,
    pub(crate) commit_index: u64,
    pub(crate) log: RaftLog<Operation>,
    peers: BTreeMap<u64, PeerState>,
    /// Whether this member's vote counts.
    pub(crate) voting: bool,
    /// Peers positively observed answering `voting = false`.
    ///
    /// Evidence, not belief: a peer that has simply not replied is absent from
    /// this set and is therefore treated as a voter, which is what keeps a
    /// partition from being mistaken for a cluster that has forgotten
    /// everything. Cleared whenever a leader is heard from, so it can never go
    /// stale and justify a recovery the cluster does not need.
    observed_amnesiac: BTreeSet<u64>,
    votes: BTreeSet<u64>,
    pre_votes: BTreeSet<u64>,
    append_sequence: u64,
    /// Seeded from the incarnation, **not** from zero.
    ///
    /// A restarted member's entries outlive it: they are still in the cluster's
    /// log and will apply after it comes back. Starting the sequence again at
    /// zero mints ids the previous incarnation already used, and the outcome of
    /// an old entry then resolves a *new* caller's future -- observed as a
    /// registration being answered with an unregistration's result.
    sequence: u64,
    waiters: HashMap<ProposalId, oneshot::Sender<Result<Outcome, RaftUnavailable>>>,
    deadline: Instant,
    /// When this member last accepted an `AppendEntries` from a leader.
    ///
    /// The basis of the lease: a follower being served by a healthy leader
    /// refuses to help depose it.
    heard_from_leader_at: Option<Instant>,
    pub(crate) machine: StateMachine,
    terms: TermStore,
    /// The most recent snapshot this member holds, for serving to followers
    /// that have fallen below the log's first index.
    snapshot: Vec<u8>,
    snapshot_meta: Option<SnapshotMeta>,
    /// Inbound transfers, by the leader sending them.
    installing: HashMap<u64, Vec<u8>>,
}

impl NodeState {
    fn quorum(&self, layout: &RaftLayout) -> usize {
        layout.quorum()
    }

    /// Is a quorum of voters provably impossible?
    ///
    /// Counts only members *known* to have forgotten -- this member if it has,
    /// plus peers observed saying so, plus `also` when evaluating a candidate's
    /// claim. Everything else counts as a voter, including members nobody has
    /// heard from, because an unreachable member is not evidence of anything
    /// and treating it as one is how a partition turns into a cluster that
    /// elects itself a second leader.
    fn cluster_has_forgotten(&self, layout: &RaftLayout, also: &[u64]) -> bool {
        let known: BTreeSet<u64> = layout.members.iter().map(|m| m.index).collect();
        let mut forgotten: BTreeSet<u64> = self.observed_amnesiac.clone();
        forgotten.extend(also.iter().copied());
        if !self.voting {
            forgotten.insert(layout.local.index);
        }
        let forgotten: BTreeSet<u64> = forgotten.intersection(&known).copied().collect();
        layout.size().saturating_sub(forgotten.len()) < layout.quorum()
    }

    /// Has this candidate collected enough of the right votes?
    ///
    /// Ordinarily a quorum, unchanged. Once a quorum of voters is impossible, a
    /// quorum of votes is necessary but no longer sufficient: every member not
    /// known to have forgotten must *also* have granted, because those are the
    /// only members whose up-to-dateness check still means anything and the
    /// surviving copy of a committed entry can only be on one of them.
    ///
    /// Members nobody has heard from count among those, and they cannot have
    /// granted -- so a partitioned cluster never satisfies this, which is the
    /// intended answer.
    fn won(&self, layout: &RaftLayout, tally: &BTreeSet<u64>) -> bool {
        if tally.len() < self.quorum(layout) {
            return false;
        }
        if !self.cluster_has_forgotten(layout, &[]) {
            return true;
        }
        let mut forgotten = self.observed_amnesiac.clone();
        if !self.voting {
            forgotten.insert(layout.local.index);
        }
        let remembering: BTreeSet<u64> = layout
            .members
            .iter()
            .map(|m| m.index)
            .filter(|index| !forgotten.contains(index))
            .collect();
        remembering.is_subset(tally)
    }

    /// Is this member currently being served by a leader it believes in?
    ///
    /// `election_min` rather than the randomised timeout, so the lease is
    /// always shorter than the shortest interval after which any member would
    /// legitimately start an election. A lease that could outlast a real
    /// election window would refuse votes to a candidate the cluster needs.
    ///
    /// A leader does not hold a lease against anyone: it answers on its own
    /// terms, and a higher term is how it learns it has been replaced.
    fn leader_lease_holds(&self, timing: &RaftTiming, now: Instant) -> bool {
        if self.role == Role::Leader || self.leader.is_none() {
            return false;
        }
        let Some(heard) = self.heard_from_leader_at else {
            return false;
        };
        now.saturating_duration_since(heard) < Duration::from_millis(timing.election_min_ms)
    }

    fn persist(&mut self) {
        let state = PersistentState {
            term: self.term,
            voted_for: self.voted_for,
            incarnation: self.incarnation,
        };
        if let Err(error) = self.terms.save(&state) {
            // Logged rather than propagated: the caller is a consensus decision
            // that has already been made, and unwinding it would leave the
            // member's in-memory term ahead of its recorded one -- which is the
            // state the file exists to rule out.
            tracing::error!(error = %error.0, "raft: could not persist term and vote");
        }
    }

    /// Adopt a higher term and return to following.
    fn step_down(&mut self, term: u64) -> bool {
        let was_leader = self.role == Role::Leader;
        self.term = term;
        self.voted_for = None;
        self.role = Role::Follower;
        self.leader = None;
        self.votes.clear();
        self.pre_votes.clear();
        self.persist();
        was_leader
    }

    fn reset_election_timer(&mut self, timing: &RaftTiming, now: Instant) {
        self.deadline = now.checked_add(timing.election_timeout()).unwrap_or(now);
    }

    fn next_proposal(&mut self, local: u64) -> ProposalId {
        self.sequence = self.sequence.saturating_add(1);
        ProposalId {
            member: local,
            sequence: self.sequence,
        }
    }

    /// Append this member's own operations at its current term.
    ///
    /// `None` when there was nothing to append, which the log refuses rather
    /// than treating as a no-op: an empty append would report a first index
    /// that no entry occupies, and the caller would register a waiter against
    /// it.
    fn append_local(&mut self, operations: Vec<Operation>) -> Option<(u64, u64)> {
        if operations.is_empty() {
            return None;
        }
        let encoded: Vec<(Vec<u8>, Operation)> =
            operations.into_iter().map(|op| (op.encode(), op)).collect();
        match self.log.append(self.term, encoded) {
            Ok(range) => Some(range),
            Err(error) => {
                tracing::error!(error = %error.0, "raft: local append refused");
                None
            }
        }
    }

    /// The two invariants `go.etcd.io/raft` asserts about one member.
    ///
    /// Transcribed from its source rather than from memory: `log.go:48` states
    /// `applied <= committed` outright, `log.go:332-334` panics in `appliedTo`
    /// when `committed < i`, and `log.go:322-330` panics in `commitTo` when
    /// `lastIndex() < tocommit`.
    ///
    /// This is a **bug detector** and deliberately not a safeguard against
    /// anything else. Nothing a peer sends can reach these numbers except
    /// through logic in this file, so a violation means a defect here. The one
    /// that prompted it applied an uncommitted tail because the apply batch was
    /// bounded by size and not by the commit index, and it went unnoticed for
    /// as long as it did precisely because nothing ever looked.
    fn check_applied_within_committed(&self) -> Result<(), RaftInvariantViolated> {
        let applied = self.machine.last_applied();
        if applied > self.commit_index {
            return Err(RaftInvariantViolated(format!(
                "applied through {applied} but committed only through {}",
                self.commit_index,
            )));
        }
        let reachable = self.log.last_index().max(self.log.snapshot_index());
        if self.commit_index > reachable {
            return Err(RaftInvariantViolated(format!(
                "committed through {} but holds only [{}..{}] with snapshot {}",
                self.commit_index,
                self.log.first_index(),
                self.log.last_index(),
                self.log.snapshot_index(),
            )));
        }
        Ok(())
    }
}

/// One member's consensus state machine.
pub struct RaftNode {
    layout: RaftLayout,
    transport: Arc<dyn Transport>,
    timing: RaftTiming,
    registry: Arc<Registry>,
    fence: Arc<RevisionFence>,
    state: Mutex<NodeState>,
    /// Woken when there is something to apply.
    ///
    /// One long-lived applier rather than a task per advance: the tasks would
    /// be unowned, so shutdown could not wait for them, and several could
    /// interleave mid-catch-up.
    ///
    /// Woken with `notify_one`, never `notify_waiters`. `notify_waiters` wakes
    /// only tasks *already* parked and stores nothing, so an advance that
    /// happens while the applier is between iterations is lost and the entry
    /// is never applied -- the proposer then waits forever on an outcome that
    /// nothing will produce. `notify_one` stores a permit, which is what the
    /// Python's `asyncio.Event` does. Measured: with `notify_waiters` every
    /// test that proposed anything hung.
    apply_wake: tokio::sync::Notify,
    leader_changed: tokio::sync::Notify,
    closing: std::sync::atomic::AtomicBool,
    batcher: ProposalBatcher<Operation, Result<Outcome, RaftUnavailable>>,
    /// Taken by `start`, which spawns the task that owns it.
    drain: Mutex<Option<ProposalDrain<Operation, Result<Outcome, RaftUnavailable>>>>,
    tasks: Mutex<Vec<tokio::task::JoinHandle<()>>>,
    forwarder: Mutex<Option<std::sync::Weak<dyn ForwardHandler>>>,
}

/// What a forwarded registry mutation is handed to.
///
/// The backend implements it. The node knows only that something answers, so
/// the consensus layer does not gain a dependency on the registry's HTTP
/// vocabulary for the sake of one message type.
#[async_trait::async_trait]
pub trait ForwardHandler: Send + Sync + 'static {
    /// Apply a mutation this member owns, and say what happened.
    async fn forward(&self, message: &crate::messages::Forward) -> crate::messages::ForwardReply;
}

impl RaftNode {
    /// Build a member. Nothing starts until [`Self::start`].
    #[must_use]
    pub fn new(
        layout: RaftLayout,
        transport: Arc<dyn Transport>,
        mut terms: TermStore,
        machine: StateMachine,
        registry: Arc<Registry>,
        timing: RaftTiming,
    ) -> Arc<Self> {
        // Loading is what increments the incarnation, so it happens once, here,
        // and the transport is told the answer rather than reading it again.
        let persisted = terms.load().unwrap_or_else(|error| {
            tracing::error!(error = %error.0, "raft: starting with no stored term");
            PersistentState {
                term: 0,
                voted_for: None,
                incarnation: 1,
            }
        });

        let peers: BTreeMap<u64, PeerState> = layout
            .members
            .iter()
            .filter(|m| m.index != layout.local.index)
            .map(|m| (m.index, PeerState::default()))
            .collect();

        let now = Instant::now();
        let state = NodeState {
            term: persisted.term,
            voted_for: persisted.voted_for,
            incarnation: persisted.incarnation,
            role: Role::Follower,
            leader: None,
            commit_index: 0,
            log: RaftLog::new(),
            peers,
            // A member that has never started before has never acknowledged an
            // entry, so nothing it could be missing was ever counted toward a
            // commit. Making it wait for a promotion would deadlock a cold
            // start.
            voting: persisted.incarnation == 1 || layout.size() == 1,
            observed_amnesiac: BTreeSet::new(),
            votes: BTreeSet::new(),
            pre_votes: BTreeSet::new(),
            append_sequence: 0,
            sequence: persisted
                .incarnation
                .saturating_mul(PROPOSALS_PER_INCARNATION),
            waiters: HashMap::new(),
            deadline: now,
            heard_from_leader_at: None,
            machine,
            terms,
            snapshot: Vec::new(),
            snapshot_meta: None,
            installing: HashMap::new(),
        };

        let (batcher, drain) = proposal_channel(MAX_BATCH);
        Arc::new(Self {
            layout,
            transport,
            timing,
            registry,
            fence: Arc::new(RevisionFence::new(0)),
            state: Mutex::new(state),
            apply_wake: tokio::sync::Notify::new(),
            leader_changed: tokio::sync::Notify::new(),
            closing: std::sync::atomic::AtomicBool::new(false),
            batcher,
            drain: Mutex::new(Some(drain)),
            tasks: Mutex::new(Vec::new()),
            forwarder: Mutex::new(None),
        })
    }

    // -- introspection ------------------------------------------------------

    /// This member's role.
    #[must_use]
    pub fn role(&self) -> Role {
        self.state.lock().role
    }

    /// Its current term.
    #[must_use]
    pub fn term(&self) -> u64 {
        self.state.lock().term
    }

    /// The leader it believes in, if any.
    #[must_use]
    pub fn leader(&self) -> Option<u64> {
        self.state.lock().leader
    }

    /// Whether this member's vote counts.
    #[must_use]
    pub fn voting(&self) -> bool {
        self.state.lock().voting
    }

    /// Its start counter.
    #[must_use]
    pub fn incarnation(&self) -> u64 {
        self.state.lock().incarnation
    }

    /// How far the cluster has committed, as this member knows it.
    #[must_use]
    pub fn commit_index(&self) -> u64 {
        self.state.lock().commit_index
    }

    /// How far this member has applied.
    #[must_use]
    pub fn last_applied(&self) -> u64 {
        self.state.lock().machine.last_applied()
    }

    /// Its index in the canonical member order.
    #[must_use]
    pub fn index(&self) -> u64 {
        self.layout.local.index
    }

    /// How many members this cluster has, this one included.
    #[must_use]
    pub fn cluster_size(&self) -> usize {
        self.layout.size()
    }

    /// The fence callers wait on before answering a client.
    #[must_use]
    pub fn fence(&self) -> Arc<RevisionFence> {
        Arc::clone(&self.fence)
    }

    /// Where a mutation is submitted.
    #[must_use]
    pub fn batcher(&self) -> ProposalBatcher<Operation, Result<Outcome, RaftUnavailable>> {
        self.batcher.clone()
    }

    /// What this leader believes about one peer, for diagnostics.
    ///
    /// `(match_index, catching_up, promote_through)`. Exposed because a member
    /// stuck catching up is the first thing anyone looks at when a cluster will
    /// not commit, and because the promotion bar is otherwise invisible -- a
    /// stale one silently promotes a member that is still missing committed
    /// entries.
    #[must_use]
    pub fn peer_progress(&self, peer: u64) -> Option<(u64, bool, u64)> {
        self.state.lock().peers.get(&peer).map(|tracked| {
            (
                tracked.match_index,
                tracked.catching_up,
                tracked.promote_through,
            )
        })
    }

    /// The term of one log entry, or `None` if it has been compacted away.
    ///
    /// Diagnostic. The pair `(index, term)` is what every consistency check on
    /// the wire is about, so being unable to read it back makes a disagreement
    /// between two members impossible to describe.
    #[must_use]
    pub fn log_term_at(&self, index: u64) -> Option<u64> {
        self.state.lock().log.term_at(index).ok()
    }

    /// The last index this member holds.
    #[must_use]
    pub fn last_log_index(&self) -> u64 {
        self.state.lock().log.last_index()
    }

    /// Who owns a Node, as this member's replicated table says.
    #[must_use]
    pub fn ownership_of(&self, node_id: &str) -> Option<u64> {
        self.state
            .lock()
            .machine
            .ownership()
            .owner_of(node_id)
            .map(|held| held.owner)
    }

    /// Allocate the next cursor for a resource type.
    ///
    /// On the node rather than exposing the allocator, because the allocator's
    /// high-water mark is consensus state: handing out a `&mut` to it would let
    /// a caller allocate without the lock that keeps two mutations from taking
    /// the same lane position.
    pub fn allocate_cursor(&self, resource_type: ResourceType) -> TaiCursor {
        self.state
            .lock()
            .machine
            .cursors_mut()
            .allocate(resource_type)
    }

    /// Send a correlated request to one peer.
    ///
    /// The forwarding path, and the only place the node's own transport is used
    /// for anything but consensus.
    ///
    /// # Errors
    ///
    /// [`RaftUnavailable`] if the peer is unreachable or does not answer.
    pub async fn request_peer(
        &self,
        peer: u64,
        message: &Message,
        timeout_ms: Option<u64>,
    ) -> Result<Message, RaftUnavailable> {
        self.transport
            .request(peer, message, crate::wire::Stream::Control, timeout_ms)
            .await
    }

    /// Which peers have a live control link.
    #[must_use]
    pub fn live_peers(&self) -> Vec<u64> {
        self.transport.live()
    }

    /// Whether a majority is reachable, this member included.
    ///
    /// Reachability, not responsiveness -- see [`Self::quorum_is_answering`]
    /// for the stronger question a leader asks of itself. This one is asked by
    /// members that are *not* leading, which send nothing and so have no
    /// replies to count, and by the backend when reporting readiness.
    #[must_use]
    pub fn has_quorum(&self) -> bool {
        self.transport
            .live()
            .len()
            .saturating_add(1)
            .ge(&self.layout.quorum())
    }

    /// Have a majority *answered* this leader within an election window?
    ///
    /// `go.etcd.io/raft`'s `QuorumActive` (`tracker/tracker.go:208`), which
    /// check-quorum consults once an election interval and which counts only
    /// peers that have actually replied.
    ///
    /// The difference from [`Self::has_quorum`] is that a stopped or stalled
    /// peer keeps its socket open and stays `up` for minutes, so a leader
    /// counting connections can believe it has a quorum while a majority of the
    /// cluster is answering nothing. Writes accepted in that state can never
    /// commit.
    ///
    /// Members catching up are excluded for the same reason they are excluded
    /// from the commit count: their acknowledgements do not establish a quorum.
    /// etcd excludes learners from `QuorumActive` identically.
    fn quorum_is_answering(&self, state: &NodeState, now: Instant) -> bool {
        let window = Duration::from_millis(self.timing.election_max_ms);
        let answering = state
            .peers
            .values()
            .filter(|peer| {
                !peer.catching_up
                    && peer
                        .last_heard_at
                        .is_some_and(|heard| now.duration_since(heard) < window)
            })
            .count()
            .saturating_add(1);
        answering >= self.layout.quorum()
    }

    /// Where a forwarded mutation goes.
    ///
    /// **Held weakly, and that is load-bearing.** The handler is the backend,
    /// and the backend owns this node -- so storing it strongly closes a cycle
    /// that nothing breaks: backend -> node -> backend. Rust has no cycle
    /// collector, so neither object is ever dropped, and with them the store,
    /// the log, the state machine and every snapshot they own.
    ///
    /// Measured before this was a `Weak`: after closing a backend and dropping
    /// every handle to it, a `Weak` to it still upgraded. See
    /// `a_closed_backend_is_dropped`.
    ///
    /// The same shape is harmless in the Python, which is why it ports without
    /// anyone noticing: `raft_backend.py` installs `self._on_forward`, a bound
    /// method that keeps the backend alive just as firmly, and CPython's cycle
    /// collector reclaims it. Behaviour is identical in both; only the
    /// mechanism that reclaims it differs, because the languages do.
    ///
    /// Takes the `Arc` by value and drops it: the caller keeps ownership, this
    /// keeps only a way back.
    pub fn set_forward_handler(&self, handler: Arc<dyn ForwardHandler>) {
        *self.forwarder.lock() = Some(Arc::downgrade(&handler));
    }

    // -- lifecycle ----------------------------------------------------------

    /// Begin listening, connecting, ticking and applying.
    ///
    /// # Errors
    ///
    /// Whatever prevented the transport from starting.
    pub async fn start(self: &Arc<Self>) -> std::io::Result<()> {
        self.closing
            .store(false, std::sync::atomic::Ordering::SeqCst);
        self.transport
            .start(Arc::clone(self) as Arc<dyn crate::transport::PeerHandler>)
            .await?;

        {
            let mut state = self.state.lock();
            let now = Instant::now();
            state.reset_election_timer(&self.timing, now);
        }

        let mut tasks = self.tasks.lock();
        let ticking = Arc::clone(self);
        tasks.push(tokio::spawn(async move { ticking.tick_forever().await }));
        let applying = Arc::clone(self);
        tasks.push(tokio::spawn(async move { applying.apply_forever().await }));
        // Bound before the `if let`, not inside its scrutinee: a guard in the
        // scrutinee lives to the end of the expression, and this one would then
        // be held across a spawn.
        let drain = self.drain.lock().take();
        if let Some(drain) = drain {
            let draining = Arc::clone(self);
            tasks.push(tokio::spawn(
                async move { draining.drain_forever(drain).await },
            ));
        }
        Ok(())
    }

    /// Stop, releasing every waiter.
    pub async fn close(&self) {
        self.closing
            .store(true, std::sync::atomic::Ordering::SeqCst);
        // Woken so the loops observe `closing` rather than sleeping out their
        // intervals first.
        self.apply_wake.notify_one();

        let tasks: Vec<_> = self.tasks.lock().drain(..).collect();
        for task in tasks {
            task.abort();
            drop(task.await);
        }

        // Every waiter released, not dropped: a caller parked on a proposal
        // that will never commit has to be told, or it waits out its deadline
        // learning nothing.
        let waiters: Vec<_> = self.state.lock().waiters.drain().collect();
        for (_, sender) in waiters {
            drop(sender.send(Err(RaftUnavailable("member is shutting down".to_owned()))));
        }
        self.transport.close().await;
    }

    async fn tick_forever(self: Arc<Self>) {
        let interval = Duration::from_millis(self.timing.heartbeat_ms);
        while !self.closing.load(std::sync::atomic::Ordering::SeqCst) {
            tokio::time::sleep(interval).await;
            self.tick();
        }
    }

    /// One heartbeat's worth of decisions.
    fn tick(&self) {
        let now = Instant::now();
        let mut wake_apply = false;
        {
            let mut state = self.state.lock();
            if state.role == Role::Leader {
                if !self.quorum_is_answering(&state, now) {
                    // Check-quorum. A leader cut off from a majority cannot
                    // commit anything, and the other side of the partition has
                    // had long enough to elect someone else -- so continuing to
                    // answer as leader would mean reporting READY while
                    // accepting writes that can never commit.
                    //
                    // **One interval, not two.** `quorum_is_answering` already
                    // asks "within an election window", so counting a second
                    // window down from the moment it turns false would double
                    // how long a partitioned leader keeps the role.
                    // `go.etcd.io/raft` steps down on the spot when
                    // `QuorumActive` fails (`raft.go:1282`); the grace a fresh
                    // leader needs comes from `become_leader` seeding
                    // `last_heard_at`, not from a second timer.
                    self.relinquish(&mut state, "lost contact with a quorum", now);
                    return;
                }
                drop(state);
                self.replicate();
                return;
            }

            if !self.has_quorum() {
                // Not counting down while an election is impossible. Letting
                // the deadline expire unused means that the moment
                // connectivity returns, this member campaigns *immediately* --
                // with no fresh timeout, before the existing leader's next
                // heartbeat can reach it -- and deposes a leader that never
                // stopped being healthy.
                state.reset_election_timer(&self.timing, now);
                return;
            }
            if now < state.deadline {
                return;
            }

            if state.voting || state.cluster_has_forgotten(&self.layout, &[]) {
                // Pre-Vote first, always. Winning the real election is the
                // *only* thing a term increment buys, so asking first costs one
                // round trip and saves every disruption a doomed campaign would
                // cause.
                wake_apply = self.pre_campaign(&mut state, now);
            } else {
                // Cannot vote, and no evidence yet that the cluster has lost
                // its voters. Ask, rather than campaign.
                self.probe_for_forgotten_peers(&mut state, now);
            }
        }
        if wake_apply {
            self.apply_wake.notify_one();
        }
    }

    /// Stop leading without changing term or vote.
    ///
    /// Deliberately *not* `step_down`: that clears `voted_for`, which is
    /// correct when adopting a higher term and catastrophic here. This member
    /// voted for itself in the current term, and forgetting that would let it
    /// vote again in the same term -- the exact double-vote the persisted state
    /// exists to prevent.
    fn relinquish(&self, state: &mut NodeState, reason: &str, now: Instant) {
        if state.role != Role::Leader {
            return;
        }
        tracing::info!(
            member = self.layout.local.name,
            term = state.term,
            reason,
            "raft: relinquishing leadership",
        );
        state.role = Role::Follower;
        state.leader = None;
        state.votes.clear();
        state.pre_votes.clear();
        state.reset_election_timer(&self.timing, now);
        self.fail_waiters(state, reason);
    }

    /// Release every proposal this member can no longer see through.
    fn fail_waiters(&self, state: &mut NodeState, reason: &str) {
        let waiters: Vec<_> = state.waiters.drain().collect();
        for (_, sender) in waiters {
            drop(sender.send(Err(RaftUnavailable(reason.to_owned()))));
        }
    }

    /// Ask every peer whether it can vote, without standing for election.
    ///
    /// A member that has forgotten cannot campaign until it knows how many
    /// others have too, and cannot learn that without asking. Asking by
    /// campaigning would raise the term on every attempt while never succeeding
    /// -- which is precisely the runaway this replaces.
    ///
    /// Sent at the current term and granting nothing, so it disturbs neither an
    /// election in progress nor a healthy leader.
    fn probe_for_forgotten_peers(&self, state: &mut NodeState, now: Instant) {
        let request = Message::RequestVote(RequestVote {
            term: state.term,
            candidate: self.layout.local.index,
            last_log_index: state.log.last_index(),
            last_log_term: state.log.last_term(),
            probe: true,
            amnesiac: Vec::new(),
            pre_vote: false,
        });
        let peers: Vec<u64> = state.peers.keys().copied().collect();
        for peer in peers {
            self.transport
                .send(peer, &request, crate::wire::Stream::Control);
        }
        state.reset_election_timer(&self.timing, now);
    }

    /// Ask whether this member would win, before claiming a term.
    ///
    /// Raft §9.6. Nothing here is mutated that a peer could observe: the term is
    /// not incremented, the vote is not recorded, nothing reaches the disk. The
    /// request carries the term this member *would* stand in -- one above its
    /// own -- so voters can apply the up-to-dateness check against a real
    /// proposal.
    ///
    /// The role change is not cosmetic. Becoming a pre-candidate clears
    /// `leader`, which releases the lease this member was holding on its old
    /// leader's behalf. Without it, pre-vote and check-quorum together refuse to
    /// elect anyone after a leader dies -- each member still vouching for a
    /// leader that is gone.
    ///
    /// Returns whether an apply should be woken, because winning outright
    /// appends a no-op.
    fn pre_campaign(&self, state: &mut NodeState, now: Instant) -> bool {
        state.role = Role::PreCandidate;
        state.leader = None;
        state.pre_votes = BTreeSet::from([self.layout.local.index]);
        state.reset_election_timer(&self.timing, now);

        if state.won(&self.layout, &state.pre_votes.clone()) {
            return self.campaign(state, now);
        }

        let request = Message::RequestVote(RequestVote {
            term: state.term.saturating_add(1),
            candidate: self.layout.local.index,
            last_log_index: state.log.last_index(),
            last_log_term: state.log.last_term(),
            probe: false,
            amnesiac: state.observed_amnesiac.iter().copied().collect(),
            pre_vote: true,
        });
        let peers: Vec<u64> = state.peers.keys().copied().collect();
        for peer in peers {
            self.transport
                .send(peer, &request, crate::wire::Stream::Control);
        }
        false
    }

    /// Start an election. Only ever called with a reachable quorum.
    ///
    /// That guard matters more than it looks. A member cut off from the cluster
    /// cannot win an election, but without the check it would keep campaigning
    /// anyway, incrementing its term on every timeout. When the partition healed
    /// it would arrive carrying a term far above everyone else's, force the
    /// healthy leader to step down, and cause an election the cluster had no
    /// reason to hold -- the "disruptive server" problem.
    fn campaign(&self, state: &mut NodeState, now: Instant) -> bool {
        state.role = Role::Candidate;
        state.term = state.term.saturating_add(1);
        state.voted_for = Some(self.layout.local.index);
        state.persist();
        state.votes = BTreeSet::from([self.layout.local.index]);
        state.leader = None;
        state.reset_election_timer(&self.timing, now);

        tracing::debug!(
            member = self.layout.local.name,
            term = state.term,
            "raft: campaigning",
        );

        if state.won(&self.layout, &state.votes.clone()) {
            return self.become_leader(state, now);
        }

        let request = Message::RequestVote(RequestVote {
            term: state.term,
            candidate: self.layout.local.index,
            last_log_index: state.log.last_index(),
            last_log_term: state.log.last_term(),
            probe: false,
            // The evidence travels with the request so a voter that has
            // forgotten can re-do the arithmetic itself rather than take this
            // candidate's word for the state of the cluster.
            amnesiac: state.observed_amnesiac.iter().copied().collect(),
            pre_vote: false,
        });
        let peers: Vec<u64> = state.peers.keys().copied().collect();
        for peer in peers {
            self.transport
                .send(peer, &request, crate::wire::Stream::Control);
        }
        false
    }
}

impl RaftNode {
    // -- replication --------------------------------------------------------

    fn replicate(&self) {
        let mut state = self.state.lock();
        let peers: Vec<u64> = state
            .peers
            .iter()
            .filter(|&(_, peer)| peer.up)
            .map(|(&index, _)| index)
            .collect();
        for peer in peers {
            self.send_append(&mut state, peer);
        }
    }

    /// Send one peer what it is missing, or a heartbeat.
    fn send_append(&self, state: &mut NodeState, peer: u64) {
        let Some(tracked) = state.peers.get(&peer) else {
            return;
        };
        let previous = tracked.next_index.saturating_sub(1);
        let next_index = tracked.next_index;

        let prev_term = match state.log.term_at(previous) {
            Ok(term) => term,
            Err(_) => {
                // The entries this peer needs have been compacted away. It
                // cannot be caught up by replication, so it is caught up by
                // state.
                self.send_snapshot(state, peer);
                return;
            }
        };

        let paused = self.carrying_entries_would_repeat_them(state, peer);
        let entries: Vec<WireEntry> = if paused {
            // An append is already outstanding to this peer. Send the heartbeat
            // without the payload: it still renews the lease, still carries the
            // commit index, and still draws the reply that will tell us where
            // the peer actually is -- without putting the same entries on a
            // link that has not yet drained the last copy.
            Vec::new()
        } else {
            match state
                .log
                .slice(next_index, self.timing.max_entries_per_append)
            {
                Ok(entries) => entries
                    .iter()
                    .map(|entry| WireEntry {
                        term: entry.term,
                        index: entry.index,
                        payload: entry.payload.clone(),
                    })
                    .collect(),
                Err(_) => {
                    self.send_snapshot(state, peer);
                    return;
                }
            }
        };

        // **Every send is correlated, not only the ones carrying entries.**
        //
        // The id is what tells a reply apart from one sent by a *previous
        // incarnation* of this peer. Nothing else can: a reply carries no
        // incarnation of its own, and an append reply is not a correlated
        // request whose future the transport fails when the link drops. So a
        // reply the dying member had already put on the wire can arrive after
        // this leader has reset its view and be read as news about the member
        // that replaced it.
        //
        // Measured: a leader credited a restarted member with index 6 while it
        // held nothing, *and* took `catching_up = false` from the same reply,
        // which put it back into the commit tally. On three members that is
        // leader plus phantom -- a quorum -- so the leader could commit an
        // index only it held. Leader Completeness, from one stale message.
        //
        // Only a send that *carries entries* arms the flow-control pause,
        // which is unchanged: a heartbeat's id never equals `pending_request`.
        state.append_sequence = state.append_sequence.saturating_add(1);
        let request_id = state.append_sequence;
        if let Some(last) = entries.last() {
            let last_index = last.index;
            if let Some(tracked) = state.peers.get_mut(&peer) {
                tracked.pending_through = last_index;
                tracked.pending_request = request_id;
                tracked.pending_since = Some(Instant::now());
            }
        }

        let leader_commit = state.commit_index;
        // What this message can actually deliver, which is not always the whole
        // commit index. The receiver adopts `min(leader_commit, prev_log_index +
        // len(entries))`, so a send whose entries were suppressed above carries
        // a window ending at `previous` however far this leader has committed.
        // Recording the full commit index there would be the leader telling
        // itself it had passed on something the peer could not take, and
        // `should_send_now` would then see nothing left to say and leave the
        // peer behind until the next tick -- the exact stall the eager send
        // exists to remove.
        //
        // `go.etcd.io/raft` keeps the same book by splitting the message types:
        // `maybeSendAppend` records `committed` because its window always
        // reaches `Next-1` (`raft.go:660`), while `sendHeartbeat`, which carries
        // no window at all, records the conservative `min(pr.Match, committed)`
        // (`raft.go:709`). This implementation has one message type, so it caps
        // by the window instead -- the same rule stated once rather than twice.
        let window_last = entries.last().map_or(previous, |entry| entry.index);
        if let Some(tracked) = state.peers.get_mut(&peer) {
            tracked.sent_commit = crate::commit::commit_to_record(leader_commit, window_last);
        }
        let message = Message::AppendEntries(AppendEntries {
            term: state.term,
            leader: self.layout.local.index,
            prev_log_index: previous,
            prev_log_term: prev_term,
            leader_commit,
            request_id,
            entries,
        });
        self.transport
            .send(peer, &message, crate::wire::Stream::Control);
    }

    /// Is an append already in flight to this peer, and not yet overdue?
    ///
    /// Overdue matters as much as outstanding: a lost append, or a lost reply,
    /// draws no answer at all, so a leader that waited forever would strand the
    /// peer. `election_min` is the backstop -- necessarily shorter than the
    /// interval after which this leader would be replaced anyway.
    fn carrying_entries_would_repeat_them(&self, state: &mut NodeState, peer: u64) -> bool {
        let Some(tracked) = state.peers.get_mut(&peer) else {
            return false;
        };
        if tracked.pending_request == 0 {
            return false;
        }
        let elapsed = tracked.pending_since.map_or(Duration::MAX, |since| {
            Instant::now().saturating_duration_since(since)
        });
        if elapsed >= Duration::from_millis(self.timing.election_min_ms) {
            tracked.pending_request = 0;
            return false;
        }
        true
    }

    /// Raft §5.4.2: commit an index only once it is replicated and current.
    ///
    /// Two conditions, and the second is the one that is easy to drop: a
    /// majority must hold the index, **and** the entry at that index must be
    /// from the current term. Committing an earlier term's entry on a count
    /// alone is the classic Raft bug -- an entry can be present on a majority
    /// and still be overwritten by a future leader, because presence is not
    /// commitment.
    ///
    /// Members still catching up are excluded from the count. Their logs may be
    /// incomplete, and an acknowledgement from an incomplete log is not
    /// evidence the entry is safe.
    ///
    /// An advance replicates **at once**. A follower learns the commit index
    /// only from `leaderCommit` on an `AppendEntries`, so leaving a new index to
    /// ride the next heartbeat puts a whole heartbeat interval on the critical
    /// path of every mutation that did not arrive at the leader -- measured at
    /// 45.6 ms p50 against a 50 ms heartbeat, where the leader had committed in
    /// about 1 ms. It cannot loop: the extra round carries no entries, so no
    /// follower's match index moves and this returns without sending again.
    fn advance_commit(&self, state: &mut NodeState) -> bool {
        // This member holds everything it has appended, hence its own index in
        // the tally alongside the peers that are not catching up.
        let mut counted: Vec<u64> = state
            .peers
            .values()
            .filter(|peer| !peer.catching_up)
            .map(|peer| peer.match_index)
            .collect();
        counted.push(state.log.last_index());

        let term = state.term;
        let quorum = state.quorum(&self.layout);
        if counted.len() < quorum {
            return false;
        }

        let candidate = leader_commit_index(counted, state.commit_index, term, |index| {
            state.log.term_at(index).ok()
        });
        if candidate <= state.commit_index {
            return false;
        }
        state.commit_index = candidate;
        true
    }

    // -- applying -----------------------------------------------------------

    async fn apply_forever(self: Arc<Self>) {
        while !self.closing.load(std::sync::atomic::Ordering::SeqCst) {
            self.apply_wake.notified().await;
            if self.closing.load(std::sync::atomic::Ordering::SeqCst) {
                return;
            }
            if let Err(error) = self.apply_committed().await {
                // Past any catch-all, deliberately. An invariant that is broken
                // stays broken, and logging it once per wake-up would be a
                // silent failure wearing the costume of a handled one.
                tracing::error!(
                    error = %error,
                    member = self.layout.local.name,
                    "raft: consensus invariant violated; this member stops applying",
                );
                return;
            }
        }
    }

    /// Apply up to the commit index, in bounded runs.
    ///
    /// The yield is between runs, never inside one: apply must not be
    /// interrupted mid-mutation, and the loop must not hold a worker for a
    /// whole catch-up.
    async fn apply_committed(&self) -> Result<(), String> {
        loop {
            let (entries, applied_before, commit_index) = {
                let state = self.state.lock();
                state
                    .check_applied_within_committed()
                    .map_err(|error| error.0)?;

                let applied = state.machine.last_applied();
                if applied >= state.commit_index {
                    break;
                }
                let start = applied.saturating_add(1);
                // Bounded by the commit index, not merely by the batch size.
                //
                // `slice` clamps to the *log*, and a follower's log routinely
                // runs ahead of what is committed -- that is what replication
                // looks like in flight. Asking for a whole batch from `start`
                // therefore applied uncommitted entries whenever the tail was
                // longer than the gap, which breaks Raft twice over: the store
                // reflects operations that may never commit, and `last_applied`
                // advances past them, so when a new leader overwrites those
                // indices the applier never applies what replaced them.
                let room = state.commit_index.saturating_sub(start).saturating_add(1);
                let wanted = usize::try_from(room)
                    .unwrap_or(usize::MAX)
                    .min(self.timing.max_apply_batch);
                let Ok(slice) = state.log.slice(start, wanted) else {
                    return Ok(());
                };
                if slice.is_empty() {
                    return Ok(());
                }
                let entries: Vec<Entry<Operation>> = slice.to_vec();
                (entries, applied, state.commit_index)
            };

            let outcomes = {
                let mut state = self.state.lock();
                match state.machine.apply(&self.registry, &entries) {
                    Ok(outcomes) => outcomes,
                    Err(divergence) => return Err(divergence.0),
                }
            };

            let applied_now = {
                let mut state = self.state.lock();
                self.resolve(&mut state, outcomes);
                state.machine.last_applied()
            };

            // After the mutations and their grains, never before: a waiter
            // released early would observe a half-applied run, which is the bug
            // the fence exists to prevent.
            self.fence.advance(applied_now);

            if applied_now <= applied_before {
                // No progress, and looping would spin. Only reachable if the
                // log handed back entries at or below `last_applied`, which
                // apply skips.
                return Ok(());
            }
            if applied_now < commit_index {
                tokio::task::yield_now().await;
            }
        }
        self.maybe_compact().await;
        Ok(())
    }

    fn resolve(&self, state: &mut NodeState, outcomes: Vec<(ProposalId, Outcome)>) {
        for (proposal, outcome) in outcomes {
            if let Some(sender) = state.waiters.remove(&proposal) {
                drop(sender.send(Ok(outcome)));
            }
        }
    }

    // -- proposing ----------------------------------------------------------

    /// Submit an operation and wait for its outcome.
    ///
    /// # Errors
    ///
    /// [`RaftUnavailable`] if this member has no leader to route to, loses
    /// leadership before the entry commits, or shuts down first.
    pub async fn propose(&self, operation: Operation) -> Result<Outcome, RaftUnavailable> {
        let waiter = self
            .batcher
            .submit(operation)
            .map_err(|_| RaftUnavailable("member is shutting down".to_owned()))?;
        waiter
            .await
            .unwrap_or_else(|_| Err(RaftUnavailable("proposal was abandoned".to_owned())))
    }

    async fn drain_forever(
        self: Arc<Self>,
        mut drain: ProposalDrain<Operation, Result<Outcome, RaftUnavailable>>,
    ) {
        while let Some(batch) = drain.next_batch().await {
            self.drain_batch(batch);
        }
    }

    /// Route one batch of proposals, as leader or as follower.
    fn drain_batch(&self, batch: Vec<Pending<Operation, Result<Outcome, RaftUnavailable>>>) {
        let mut wake_apply = false;
        let replicate;
        {
            let mut state = self.state.lock();
            if state.role == Role::Leader {
                let mut operations = Vec::with_capacity(batch.len());
                for item in batch {
                    let proposal = state.next_proposal(self.layout.local.index);
                    operations.push(rebind(item.operation, proposal));
                    state.waiters.insert(proposal, item.reply);
                }
                state.append_local(operations);
                // A single-member cluster has no peers to hear from, so nothing
                // else would ever advance its commit index.
                if state.peers.is_empty() {
                    wake_apply = self.advance_commit(&mut state);
                }
                replicate = true;
            } else {
                let Some(leader) = state.leader else {
                    for item in batch {
                        drop(
                            item.reply
                                .send(Err(RaftUnavailable("no leader elected".to_owned()))),
                        );
                    }
                    return;
                };

                let mut payloads = Vec::with_capacity(batch.len());
                for item in batch {
                    let proposal = state.next_proposal(self.layout.local.index);
                    let operation = rebind(item.operation, proposal);
                    state.waiters.insert(proposal, item.reply);
                    payloads.push(operation.encode());
                }
                let message = Message::Propose(crate::messages::Propose {
                    proposals: payloads,
                    request_id: 0,
                });
                self.transport
                    .send(leader, &message, crate::wire::Stream::Control);
                return;
            }
        }
        if replicate {
            self.replicate();
        }
        if wake_apply {
            self.apply_wake.notify_one();
        }
    }
}

/// Should this peer be sent an append right now, rather than at the next tick?
///
/// The rule itself is [`crate::commit::should_send_now`], beside the commit
/// rules it is inseparable from; this reads the peer's bookkeeping for it.
fn should_send_now(state: &NodeState, peer: u64) -> bool {
    let Some(tracked) = state.peers.get(&peer) else {
        return false;
    };
    crate::commit::should_send_now(
        tracked.pending_request,
        tracked.next_index,
        state.log.last_index(),
        state.commit_index,
        tracked.sent_commit,
    )
}

/// Stamp a proposal id onto an operation built without one.
///
/// Callers construct operations without knowing where in the sequence they will
/// land; the id is assigned at drain time, when the order is decided.
fn rebind(operation: Operation, proposal: ProposalId) -> Operation {
    Operation {
        proposal,
        kind: operation.kind,
    }
}

impl RaftNode {
    /// Take leadership of the term this member just won.
    ///
    /// Figure 2: `nextIndex` and `matchIndex` are "reinitialized after
    /// election". Everything reset below describes *this leader's* relationship
    /// with the peer, so it has the same lifetime.
    ///
    /// `catching_up` and `promote_through` in particular. They are a pair, and
    /// keeping them was a **safety** bug: `promote_through` is only ever
    /// assigned on a false-to-true transition of `catching_up`, so a stale true
    /// means a new leader never re-decides the bar. Measured -- a leader that
    /// had led before, committed through index 6, and saw the peer report
    /// catching-up again kept `promote_through` at 1 from the earlier term, and
    /// would have promoted that member back into the electorate holding none of
    /// indices 2..6. A member promoted while still missing committed entries is
    /// a voter that can grant a vote the election restriction exists to refuse.
    ///
    /// `up` and `incarnation` are deliberately kept: they are observations about
    /// the peer itself rather than about this leadership, and forgetting them
    /// would make a healthy cluster look down for a tick.
    ///
    /// **Not falsifiable by the current suite.** Removing the
    /// `promote_through` reset leaves every test passing, because reaching it
    /// needs a peer that reports catching-up *across a leadership change* --
    /// and the in-memory fabric delivers live, so a peer's real reply
    /// overwrites any injected state within a tick, while forcing one member to
    /// lead twice is timing-dependent. The Python reaches it with a harness
    /// that owns the clock and the delivery order. Until there is one here,
    /// this reasoning is what carries the line; see
    /// `a_peer_is_never_promoted_below_its_bar`, which says the same thing
    /// where a reader will look for it.
    fn become_leader(&self, state: &mut NodeState, now: Instant) -> bool {
        state.role = Role::Leader;
        state.leader = Some(self.layout.local.index);
        // A leader is by definition a voter: if this member won through the
        // recovery path it was not one a moment ago, and leaving it non-voting
        // would leave it unable to vote in the next election it takes part in
        // -- for no reason, since it is now the member the others are being
        // caught up *from*.
        state.voting = true;
        // Whatever was observed about who had forgotten belonged to the
        // election just concluded. Keeping it would let a cluster that has
        // since recovered still believe its voters were gone.
        state.observed_amnesiac.clear();

        let next_index = state.log.last_index().saturating_add(1);
        let append_sequence = state.append_sequence;
        for peer in state.peers.values_mut() {
            peer.next_index = next_index;
            peer.match_index = 0;
            peer.catching_up = false;
            peer.promote_through = 0;
            // In-flight bookkeeping for appends sent while previously leader. A
            // stale correlation id reports an outstanding request that no
            // longer exists, which suppresses replication until the backstop
            // expires.
            peer.pending_through = 0;
            peer.pending_request = 0;
            peer.pending_since = None;
            peer.sent_commit = 0;
            // Replies drawn by the previous leadership's sends say nothing
            // about this one's, and this leader has just reset everything they
            // would report on.
            peer.reply_floor = append_sequence;
            // Likewise a snapshot this member was sending in an earlier term:
            // the new transfer starts from zero, and a carried-over offset
            // would have the leader resume a stream the peer is not expecting.
            peer.snapshot_offset = 0;
            peer.snapshot_in_flight = false;
            // One election window of grace before check-quorum asks anything of
            // them. A leader that demanded evidence it has not had time to
            // collect would step down in the tick after winning.
            peer.last_heard_at = Some(now);
        }

        tracing::info!(
            member = self.layout.local.name,
            term = state.term,
            "raft: is leader",
        );
        self.leader_changed.notify_waiters();
        // Raft §8: a new leader cannot know what earlier terms committed until
        // it commits an entry of its own, so it appends one that does nothing.
        let proposal = state.next_proposal(self.layout.local.index);
        state.append_local(vec![Operation {
            proposal,
            kind: OperationKind::Noop,
        }]);
        if state.peers.is_empty() {
            return self.advance_commit(state);
        }
        false
    }

    // -- snapshot transfer --------------------------------------------------

    /// Send the next chunk of this member's snapshot to a stranded peer.
    fn send_snapshot(&self, state: &mut NodeState, peer: u64) {
        let Some(meta) = state.snapshot_meta else {
            // Nothing to send yet. The peer stays behind until the next
            // compaction produces one, which is correct: there is no state to
            // hand it that it does not already have.
            return;
        };
        if state.snapshot.is_empty() {
            return;
        }
        let Some(tracked) = state.peers.get(&peer) else {
            return;
        };
        if tracked.snapshot_in_flight {
            // One chunk at a time.
            return;
        }

        let offset = tracked.snapshot_offset;
        let end = offset
            .saturating_add(self.timing.snapshot_chunk)
            .min(state.snapshot.len());
        let chunk = state.snapshot.get(offset..end).unwrap_or_default().to_vec();
        let done = end >= state.snapshot.len();

        if let Some(tracked) = state.peers.get_mut(&peer) {
            tracked.snapshot_in_flight = true;
        }
        let message = Message::InstallSnapshot(InstallSnapshot {
            term: state.term,
            leader: self.layout.local.index,
            last_index: meta.last_index,
            last_term: meta.last_term,
            offset: offset as u64,
            data: chunk,
            done,
            ownership: Vec::new(),
        });
        // BULK, so a multi-megabyte transfer cannot head-of-line-block the
        // heartbeats that keep this member's leadership alive.
        self.transport
            .send(peer, &message, crate::wire::Stream::Bulk);
    }

    /// Is this snapshot's metadata possible at all? `None` if it is.
    ///
    /// Two checks, both about metadata rather than content. A snapshot cannot
    /// describe a term above the one its sender holds: the sender built it from
    /// entries it had committed, and it cannot have committed an entry from a
    /// term it has not reached. Nor can it move this member's snapshot boundary
    /// *backwards*: everything below the boundary is already applied, so
    /// accepting an older snapshot would un-apply committed state.
    ///
    /// Installing one anyway is worse than it sounds: the log would take the
    /// snapshot's term as its own, and a member whose last log term is above its
    /// current term considers itself impossibly up to date. It would refuse
    /// every vote and win any election it entered.
    fn why_the_snapshot_cannot_be_real(
        state: &NodeState,
        meta: &SnapshotMeta,
        sender_term: u64,
    ) -> Option<String> {
        if meta.last_term > sender_term {
            return Some(format!(
                "it covers term {} but arrived from a member at term {sender_term}",
                meta.last_term,
            ));
        }
        if meta.last_index <= state.commit_index {
            // Against the **commit index**, not the compaction boundary.
            // `snapshot_index <= commit_index` always, so comparing against the
            // boundary let through every snapshot landing in between -- and
            // installing one of those replaces the state machine with older
            // state while `commit_index` correctly stays put, leaving committed
            // entries un-applied. That is the one thing a state machine may
            // never do.
            //
            // `go.etcd.io/raft` refuses on exactly this line (`raft.go:1861`):
            // `if s.Metadata.Index <= r.raftLog.committed { return false }`.
            return Some(format!(
                "it ends at index {}, at or below what is already committed here ({})",
                meta.last_index, state.commit_index,
            ));
        }
        None
    }

    // -- compaction ---------------------------------------------------------

    /// Take a snapshot and drop the entries it covers.
    ///
    /// A log that is never compacted grows for the life of the cluster, and
    /// every member holds all of it in memory. Compaction is therefore not an
    /// optimisation here; it is what makes an in-memory log viable at all.
    ///
    /// Two decisions, not one, and they take different answers:
    ///
    /// * **what the snapshot describes** -- always `last_applied`, because the
    ///   payload is serialised from the live store and that is the state it
    ///   holds. This is not a choice;
    /// * **how much of the log may be discarded** -- bounded by the slowest
    ///   *reachable* follower's `match_index`, because discarding an entry a
    ///   follower has not yet received strands it on replication.
    ///
    /// They were one quantity until a chaos run showed what that costs. A
    /// leader captured at `index = 11` while its machine was at `applied = 12`,
    /// pinning zero pre-images because nothing changed during the walk -- and
    /// the payload was byte-identical to a peer's snapshot labelled 12. A
    /// follower installing it set `last_applied = 11` over a store that already
    /// held entry 12's registration, replayed 12, computed `creates = false`
    /// where the proposer had said `true`, and diverged. That member then stops
    /// applying for good.
    ///
    /// `max_log_entries` overrides the second bound deliberately: one
    /// unreachable member must not make the log grow without bound, because a
    /// partial outage turning into an out-of-memory failure is the worse
    /// outcome.
    async fn maybe_compact(&self) {
        let prepared = {
            let mut state = self.state.lock();
            let applied = state.machine.last_applied();
            let held = applied
                .saturating_sub(state.log.first_index())
                .saturating_add(1);
            if held < self.timing.compaction_threshold {
                return;
            }
            if state.machine.snapshots().capture().is_some() {
                return;
            }
            if applied <= state.log.snapshot_index() {
                return;
            }

            let mut discard_to = applied;
            if state.role == Role::Leader && held < self.timing.max_log_entries {
                let confirmed: Vec<u64> = state
                    .peers
                    .values()
                    .filter(|peer| peer.up)
                    .map(|peer| peer.match_index)
                    .collect();
                if let Some(&slowest) = confirmed.iter().min() {
                    discard_to = applied.min(slowest);
                }
            }

            let Ok(term) = state.log.term_at(applied) else {
                return;
            };
            let ownership = state.machine.ownership().clone();
            if state
                .machine
                .snapshots_mut()
                .begin(applied, term, &ownership)
                .is_err()
            {
                return;
            }
            (applied, term, discard_to)
        };

        let (applied, term, discard_to) = prepared;

        // The walk is chunked so a large registry does not hold the store's
        // read lock for its whole length; copy-on-write is what makes a
        // non-atomic walk correct.
        let payload = {
            let keys = self.registry.with_read_store(crate::snapshot::walk_order);
            let mut records = Vec::new();
            let mut live = std::collections::BTreeSet::new();
            for window in keys.chunks(crate::snapshot::CHUNK_RESOURCES) {
                {
                    let state = self.state.lock();
                    let Some(capture) = state.machine.snapshots().capture() else {
                        return;
                    };
                    let chunk = self.registry.with_read_store(|store| {
                        crate::snapshot::collect_chunk(store, capture, window, &mut live)
                    });
                    records.extend(chunk);
                    drop(state);
                }
                tokio::task::yield_now().await;
            }

            let mut state = self.state.lock();
            match state.machine.snapshots_mut().finish(records, &live) {
                Ok(payload) => payload,
                Err(error) => {
                    tracing::error!(error = %error.0, "raft: taking a snapshot failed");
                    state.machine.snapshots_mut().abandon();
                    return;
                }
            }
        };

        let mut state = self.state.lock();
        state.snapshot = payload;
        state.snapshot_meta = Some(SnapshotMeta {
            last_index: applied,
            last_term: term,
            resources: 0,
        });

        // Discarding is the separate, bounded step. The snapshot covers at
        // least as much as this drops, so a follower too far behind to be
        // served from the log is still served from the snapshot, and one that is
        // merely a little behind keeps being served entries.
        if discard_to <= state.log.snapshot_index() {
            tracing::debug!(
                member = self.layout.local.name,
                applied,
                discard_to,
                "raft: snapshotted but discarded nothing; a follower is behind",
            );
            return;
        }
        let Ok(discard_term) = state.log.term_at(discard_to) else {
            return;
        };
        match state.log.discard_through(discard_to, discard_term) {
            Ok(freed) => tracing::info!(
                member = self.layout.local.name,
                applied,
                discard_to,
                freed,
                "raft: snapshotted and compacted",
            ),
            Err(error) => tracing::debug!(error = %error.0, "raft: nothing to compact"),
        }
    }

    // -- waiting ------------------------------------------------------------

    /// Block until a leader is known.
    ///
    /// # Errors
    ///
    /// [`RaftUnavailable`] if none appears within the deadline.
    pub async fn wait_for_leader(&self, timeout: Duration) -> Result<u64, RaftUnavailable> {
        let deadline = Instant::now()
            .checked_add(timeout)
            .unwrap_or_else(Instant::now);
        loop {
            // Bound before the `if let`: a guard in the scrutinee lives to the
            // end of the expression, and this loop sleeps a few lines later.
            let leader = self.state.lock().leader;
            if let Some(leader) = leader {
                return Ok(leader);
            }
            let now = Instant::now();
            if now >= deadline {
                return Err(RaftUnavailable(
                    "no leader was elected within the deadline".to_owned(),
                ));
            }
            let step = Duration::from_millis(self.timing.heartbeat_ms)
                .min(deadline.saturating_duration_since(now));
            tokio::time::sleep(step).await;
        }
    }
}

// ---------------------------------------------------------------------------
// What arrives from peers
// ---------------------------------------------------------------------------

#[async_trait::async_trait]
impl crate::transport::PeerHandler for RaftNode {
    fn on_request_vote(&self, _peer: u64, message: &RequestVote) -> RequestVoteReply {
        let now = Instant::now();
        let mut state = self.state.lock();

        if message.probe {
            // A question, not a request. Answered at whatever term we hold, and
            // deliberately without adopting the asker's term or touching the
            // election timer: a probe must be able to survey a cluster without
            // changing it.
            return RequestVoteReply {
                term: state.term,
                granted: false,
                voting: state.voting,
                pre_vote: false,
            };
        }

        if state.leader_lease_holds(&self.timing, now) {
            // Raft §6's disruption problem. This member is being served right
            // now, so a candidate asking it to help depose that leader is
            // answered no -- and crucially **without adopting the candidate's
            // term**, because adopting it is itself the disruption: it clears
            // the leader and the vote, and the cluster holds an election it had
            // no reason to.
            //
            // The quorum gate on campaigning already stops a *partitioned*
            // member from doing this. It does not stop one whose scheduler
            // stalled long enough to miss its heartbeats.
            return RequestVoteReply {
                term: state.term,
                granted: false,
                voting: state.voting,
                pre_vote: message.pre_vote,
            };
        }

        if message.pre_vote {
            // Nothing is mutated: not the term, not the vote, not the election
            // timer, nothing on disk. That is the entire contract of a
            // pre-vote, and breaking any part of it would make the round as
            // disruptive as the election it exists to avoid.
            //
            // The grant conditions are the real election's, minus the recorded
            // vote -- a member may pre-vote for several candidates in the same
            // round, because it has promised none of them anything.
            let may_vote =
                state.voting || state.cluster_has_forgotten(&self.layout, &message.amnesiac);
            let granted = may_vote
                && message.term > state.term
                && state
                    .log
                    .is_at_least_as_current_as(message.last_log_index, message.last_log_term);
            return RequestVoteReply {
                // The prospective term when granting, so the candidate can
                // count it against the term it proposed; this member's own when
                // refusing, so a candidate standing on a stale term learns to
                // step down.
                term: if granted { message.term } else { state.term },
                granted,
                voting: state.voting,
                pre_vote: true,
            };
        }

        if message.term > state.term {
            state.step_down(message.term);
        }

        // A member that has forgotten its log normally refuses. It votes only
        // once the candidate's evidence, together with its own condition,
        // proves no quorum of voters can exist -- at which point no committed
        // entry can still be protected by refusing. The arithmetic is re-done
        // here rather than trusted, so a candidate cannot talk a voter into it.
        let may_vote = state.voting || state.cluster_has_forgotten(&self.layout, &message.amnesiac);

        let mut granted = false;
        if message.term == state.term && may_vote {
            let free = state
                .voted_for
                .is_none_or(|already| already == message.candidate);
            let current = state
                .log
                .is_at_least_as_current_as(message.last_log_index, message.last_log_term);
            if free && current {
                granted = true;
                state.voted_for = Some(message.candidate);
                // Durable BEFORE the reply leaves. A vote that is granted and
                // then forgotten is the whole failure this design guards
                // against, and the window is exactly here.
                state.persist();
                state.reset_election_timer(&self.timing, now);
            }
        }

        RequestVoteReply {
            term: state.term,
            granted,
            voting: state.voting,
            pre_vote: false,
        }
    }

    fn on_request_vote_reply(&self, peer: u64, message: &RequestVoteReply) {
        let now = Instant::now();
        let mut wake_apply = false;
        {
            let mut state = self.state.lock();

            // Recorded before anything else, and for probes and pre-votes too:
            // this is the only way a member learns which of its peers have
            // forgotten, and a reply that arrives after the round it belonged
            // to is still evidence.
            if message.voting {
                state.observed_amnesiac.remove(&peer);
            } else {
                state.observed_amnesiac.insert(peer);
            }

            if message.pre_vote {
                // A *refused* pre-vote carries the voter's own term. If that is
                // above ours we are stale and step down -- the one state change
                // a pre-vote round may cause, and it is a correction rather
                // than a disruption. A *granted* one carries the prospective
                // term, ours plus one, and must never be mistaken for evidence
                // that we are behind.
                if !message.granted && message.term > state.term {
                    state.step_down(message.term);
                    return;
                }
                if state.role != Role::PreCandidate {
                    return;
                }
                if message.granted && message.term == state.term.saturating_add(1) {
                    state.pre_votes.insert(peer);
                    let tally = state.pre_votes.clone();
                    if state.won(&self.layout, &tally) {
                        wake_apply = self.campaign(&mut state, now);
                    }
                }
            } else {
                if message.term > state.term {
                    state.step_down(message.term);
                    return;
                }
                if state.role != Role::Candidate || message.term != state.term {
                    return;
                }
                if message.granted {
                    state.votes.insert(peer);
                    let tally = state.votes.clone();
                    if state.won(&self.layout, &tally) {
                        wake_apply = self.become_leader(&mut state, now);
                    }
                }
            }
        }
        if wake_apply {
            self.apply_wake.notify_one();
        }
        if self.role() == Role::Leader {
            self.replicate();
        }
    }

    fn on_append_entries(&self, _peer: u64, message: &AppendEntries) -> AppendEntriesReply {
        let now = Instant::now();
        let mut wake_apply = false;
        let reply = {
            let mut state = self.state.lock();

            if message.term < state.term {
                return AppendEntriesReply {
                    term: state.term,
                    success: false,
                    match_index: 0,
                    conflict_index: 0,
                    conflict_term: 0,
                    catching_up: !state.voting,
                    request_id: 0,
                };
            }

            if message.term > state.term {
                state.step_down(message.term);
            }
            state.role = Role::Follower;
            state.leader = Some(message.leader);
            state.reset_election_timer(&self.timing, now);
            state.heard_from_leader_at = Some(now);
            // There is a leader, so a quorum of voters existed. Any evidence to
            // the contrary is out of date, and stale evidence is the one thing
            // that could justify the recovery path when it is not warranted.
            state.observed_amnesiac.clear();

            if message.prev_log_index < state.commit_index {
                // A delayed or duplicated append anchored below what this member
                // has already committed. Answering it on its own terms would
                // report `prev_log_index + len(entries)` -- a match *below* our
                // commit index, which walks the leader's view of us backwards
                // and makes it re-send entries we hold. Answer with what we
                // really have instead.
                //
                // `go.etcd.io/raft` returns early here for the same reason
                // (`raft.go:1796`), replying with `r.raftLog.committed`.
                //
                // It is also the guard that keeps such a message away from the
                // truncation path below: everything at or below the commit index
                // is settled, and no append may reopen it.
                return AppendEntriesReply {
                    term: state.term,
                    success: true,
                    match_index: state.commit_index,
                    conflict_index: 0,
                    conflict_term: 0,
                    catching_up: !state.voting,
                    request_id: message.request_id,
                };
            }

            if !state
                .log
                .matches(message.prev_log_index, message.prev_log_term)
            {
                let (conflict_index, conflict_term) = state
                    .log
                    .find_conflict(message.prev_log_index, message.prev_log_term);
                return AppendEntriesReply {
                    term: state.term,
                    success: false,
                    match_index: 0,
                    conflict_index,
                    conflict_term,
                    catching_up: !state.voting,
                    request_id: message.request_id,
                };
            }

            if !message.entries.is_empty() {
                let mut decoded = Vec::with_capacity(message.entries.len());
                for wire in &message.entries {
                    match Operation::decode(&wire.payload) {
                        Ok(value) => decoded.push(Entry {
                            term: wire.term,
                            index: wire.index,
                            payload: wire.payload.clone(),
                            value,
                        }),
                        Err(error) => {
                            // Rejected at the edge rather than inside apply,
                            // which runs synchronously and has nowhere to fail.
                            tracing::warn!(error = %error.0, "raft: undecodable entry");
                            return AppendEntriesReply {
                                term: state.term,
                                success: false,
                                match_index: 0,
                                conflict_index: 0,
                                conflict_term: 0,
                                catching_up: !state.voting,
                                request_id: message.request_id,
                            };
                        }
                    }
                }
                let committed = state.commit_index;
                if let Err(error) = state.log.append_replicated(decoded, committed) {
                    tracing::warn!(error = %error.0, "raft: replicated append refused");
                    return AppendEntriesReply {
                        term: state.term,
                        success: false,
                        match_index: 0,
                        conflict_index: 0,
                        conflict_term: 0,
                        catching_up: !state.voting,
                        request_id: message.request_id,
                    };
                }
            }

            // `vouched_for` is the index of the last **new** entry -- not this
            // follower's last index. See `commit.rs`: the difference is a State
            // Machine Safety bug, and the same quantity is reported as
            // `match_index`, so a follower vouches for exactly the range it
            // would allow itself to commit and never for more.
            let vouched_for = last_new_index(message.prev_log_index, message.entries.len() as u64);
            let advanced =
                follower_commit_index(state.commit_index, message.leader_commit, vouched_for);
            if advanced > state.commit_index {
                state.commit_index = advanced;
                wake_apply = true;
            }

            AppendEntriesReply {
                term: state.term,
                success: true,
                match_index: vouched_for,
                conflict_index: 0,
                conflict_term: 0,
                catching_up: !state.voting,
                request_id: message.request_id,
            }
        };
        if wake_apply {
            self.apply_wake.notify_one();
        }
        reply
    }

    fn on_append_entries_reply(&self, peer: u64, message: &AppendEntriesReply) {
        let mut wake_apply = false;
        let mut resend = false;
        // "This reply told us nothing to act on", the Python's early return.
        let mut settled = false;
        let mut promote: Option<Promote> = None;
        {
            let mut state = self.state.lock();
            if message.term > state.term {
                state.step_down(message.term);
                return;
            }
            if state.role != Role::Leader || message.term != state.term {
                return;
            }
            let Some(tracked) = state.peers.get_mut(&peer) else {
                return;
            };

            if message.request_id <= tracked.reply_floor {
                // Drawn by a send this leader has since disowned -- see
                // `PeerState::reply_floor`. Believing it credits the member
                // that has just replaced this one with a log it does not have,
                // and takes `catching_up` from a member that no longer exists.
                return;
            }

            // Only the answer to *this* append releases the pause. A reply to a
            // send that carried no entries says nothing about whether entries
            // landed, and its id never matches `pending_request`.
            let answered_our_append =
                message.request_id != 0 && message.request_id == tracked.pending_request;
            if answered_our_append {
                tracked.pending_request = 0;
            }

            // Recorded before the success/failure split, as `go.etcd.io/raft`
            // records `RecentActive` there (`raft.go:1388`, `:1580`): the peer
            // answered, and that is true whatever it said. This is the evidence
            // check-quorum runs on -- see `quorum_is_answering`.
            tracked.last_heard_at = Some(Instant::now());

            let was_catching_up = tracked.catching_up;
            tracked.catching_up = message.catching_up;
            if message.catching_up && !was_catching_up {
                // Newly noticed: it must reach everything committed as of now
                // before its acknowledgements count again.
                let bar = state.commit_index;
                if let Some(tracked) = state.peers.get_mut(&peer) {
                    tracked.promote_through = bar;
                }
                tracing::info!(peer, through = bar, "raft: member is catching up");
            }

            let matched = state.peers.get(&peer).map_or(0, |t| t.match_index);
            if !message.success && message.conflict_index <= matched {
                // Stale, and safe to say so only because of `reply_floor`.
                //
                // `go.etcd.io/raft` refuses the same way in `MaybeDecrTo`
                // (`tracker/progress.go:230`: `if rejected <= pr.Match`),
                // commenting that "rejections can happen spuriously as messages
                // are sent out of order or duplicated". Its `rejected` is the
                // `prev_log_index` of the refused append, which this reply does
                // not carry; the conflict hint is used instead, and the two ask
                // subtly different questions.
                //
                // The substitution is sound because within one leadership a
                // peer that acknowledged `match_index` holds every index up to
                // it, identical to this leader's, by Log Matching -- so a
                // genuine conflict must lie above it. `become_leader` resets
                // `match_index`, so nothing carries across terms.
                //
                // It was **not** sound before the fence above: a phantom
                // `match_index` left by a previous incarnation's reply made a
                // rejoining member's honest "resume from index 1" look stale,
                // and the leader ignored it for the rest of the term.
                return;
            }

            if !message.success {
                // Resume from the start of the conflicting term rather than one
                // index back, so a far-behind member costs a handful of
                // exchanges instead of one per entry.
                //
                // No `match_index + 1` floor, unlike etcd's
                // `max(min(rejected, matchHint+1), pr.Match+1)`
                // (`tracker/progress.go:249`). etcd needs one because
                // `rejected` and `matchHint` are two different quantities and
                // the smaller can fall below `Match`. Here there is one, and
                // the guard above has already returned unless `conflict_index >
                // match_index` -- so a floor could never be the larger term,
                // and adding it would be a line that looks load-bearing and is
                // not.
                if let Some(tracked) = state.peers.get_mut(&peer) {
                    tracked.next_index = message.conflict_index.max(1);
                    // The window just moved backwards, so anything recorded as
                    // told to this peer above its new end was told through a
                    // message it rejected. `go.etcd.io/raft` clamps the same way
                    // whenever `Next` regresses (`tracker/progress.go:142`,
                    // `:238`, `:251`), commenting that the sent commit "unlikely
                    // has been applied".
                    tracked.sent_commit = crate::commit::commit_after_regression(
                        tracked.sent_commit,
                        tracked.next_index,
                    );
                }
                resend = true;
            } else if let Some(tracked) = state.peers.get_mut(&peer) {
                // **Both indices only ever move forward within a leadership.**
                //
                // A follower vouches for `prev_log_index + len(entries)` -- the
                // window of the message it is answering. An entries-less send
                // therefore draws a reply vouching for `prev_log_index` alone,
                // which is *less* than a preceding append's reply vouched for.
                // Taking that as news walks this peer's position backwards and
                // makes the leader re-send entries it already holds. Measured
                // before this guard: 15% of all replies on an idle in-memory
                // cluster, 24% over a slow link.
                //
                // `go.etcd.io/raft` keeps the invariant in one place,
                // `MaybeUpdate` (`tracker/progress.go:205`), and gates its
                // whole success branch on it.
                let advanced = message.match_index > tracked.match_index;
                if advanced {
                    tracked.match_index = message.match_index;
                }
                // `Match < Next`, which etcd states as an invariant on the same
                // line it advances them (`tracker/progress.go:211`). Enforced on
                // every success rather than only on an advance, because the two
                // can be driven apart by different messages: a rejection lowers
                // `next_index` alone, and a success can then leave `match_index`
                // above it. A leader in that state anchors its next append below
                // what the peer already acknowledged, and if that anchor is
                // under the peer's commit index the peer answers without taking
                // the entries -- so neither side moves and the pair spin until
                // the term ends.
                tracked.next_index = tracked
                    .next_index
                    .max(tracked.match_index.saturating_add(1));

                // Above the guard below, deliberately. Promotion is decided by
                // what the peer says about *itself* together with where it has
                // got to, and both are known whether or not this reply moved
                // anything. A member that is caught up and simply repeating its
                // position would otherwise never be promoted, and a rejoining
                // member's caller waits forever.
                if tracked.catching_up && tracked.match_index >= tracked.promote_through {
                    tracked.catching_up = false;
                    let through = tracked.promote_through;
                    promote = Some(Promote {
                        term: state.term,
                        leader: self.layout.local.index,
                        through_index: through,
                    });
                }

                if !advanced && !answered_our_append {
                    // Told nothing new, and not the answer to an outstanding
                    // append, so there is no replication decision to make. etcd
                    // stops here too: its success branch runs only when
                    // `MaybeUpdate` moved something, or when the reply releases
                    // a probing peer (`raft.go:1528`). That second clause is why
                    // a non-advancing reply which *did* clear our pause still
                    // falls through -- it releases flow control, and entries may
                    // be waiting behind it.
                    settled = true;
                } else {
                    wake_apply = self.advance_commit(&mut state);
                }
            }

            if resend {
                self.send_append(&mut state, peer);
            } else if !settled && !wake_apply && should_send_now(&state, peer) {
                // **The rejection path already did this; the success path did
                // not.** A reply that clears a peer's pause without moving the
                // commit index -- which is every reply from a peer outside the
                // quorum position -- left that peer's outstanding entries
                // unsent until the next heartbeat.
                //
                // `!wake_apply` is "the commit index did not move". When it
                // does, the `replicate()` below already sends every peer --
                // this one included -- so sending here as well would put two
                // messages on the same link for one reply.
                //
                // Measured: the caller of a registration driven at such a peer
                // waits one heartbeat interval, because until the entries
                // arrive the peer cannot advance its own commit index, and
                // until it does the registration is not applied there. At five
                // members with a 50 ms heartbeat that is most of a 252 ms
                // `node_online`.
                //
                // It cannot loop: a successful reply strictly advances
                // `next_index`, which is bounded by `last_index`, so the
                // condition above stops being true. And it changes no commit
                // or election rule -- it sends entries the leader already
                // holds to a peer already known to be missing them, which is
                // what replication is.
                self.send_append(&mut state, peer);
            }
        }

        if let Some(promote) = promote {
            self.transport.send(
                peer,
                &Message::Promote(promote),
                crate::wire::Stream::Control,
            );
            tracing::info!(peer, "raft: member promoted");
        }
        if wake_apply {
            self.apply_wake.notify_one();
            // An advance publishes immediately; see `advance_commit`.
            self.replicate();
        }
    }

    fn on_promote(&self, peer: u64, message: &Promote) {
        let mut state = self.state.lock();
        if message.term < state.term {
            return;
        }
        if !state.voting {
            tracing::info!(
                member = self.layout.local.name,
                peer,
                through = message.through_index,
                "raft: promoted",
            );
        }
        state.voting = true;
    }

    fn on_install_snapshot(&self, peer: u64, message: &InstallSnapshot) -> InstallSnapshotReply {
        let now = Instant::now();
        let mut state = self.state.lock();

        if message.term < state.term {
            return InstallSnapshotReply {
                term: state.term,
                bytes_received: 0,
                done: false,
            };
        }
        if message.term > state.term {
            state.step_down(message.term);
        }
        state.role = Role::Follower;
        state.leader = Some(message.leader);
        state.reset_election_timer(&self.timing, now);

        let offset = usize::try_from(message.offset).unwrap_or(usize::MAX);
        let buffer = state.installing.entry(peer).or_default();
        if offset == 0 {
            buffer.clear();
        }
        if offset != buffer.len() {
            // A chunk out of order, or a retransmission from a different
            // offset. Restart rather than splice: a snapshot assembled from
            // mismatched pieces would parse and be wrong.
            buffer.clear();
            return InstallSnapshotReply {
                term: state.term,
                bytes_received: 0,
                done: false,
            };
        }
        buffer.extend_from_slice(&message.data);
        let assembled = buffer.len();

        if !message.done {
            return InstallSnapshotReply {
                term: state.term,
                bytes_received: assembled as u64,
                done: false,
            };
        }

        let payload = state.installing.remove(&peer).unwrap_or_default();
        let refusal = InstallSnapshotReply {
            term: state.term,
            bytes_received: 0,
            done: false,
        };

        let Ok((meta, ownership, records)) = crate::snapshot::decode_snapshot(&payload) else {
            tracing::error!("raft: refusing a snapshot that did not decode");
            return refusal;
        };
        if let Some(reason) = Self::why_the_snapshot_cannot_be_real(&state, &meta, message.term) {
            // Rejecting protocol-impossible input early rather than corrupting
            // state with it. A correct leader cannot produce these, so seeing
            // one means a peer is wrong and the only safe answer is to keep our
            // own state.
            tracing::error!(peer, reason, "raft: refusing a snapshot");
            return refusal;
        }
        let (gc, forget) = self
            .registry
            .with_read_store(|store| (store.gc_interval(), store.forget_interval()));
        let Ok(store) = crate::snapshot::install(records, gc, forget) else {
            tracing::error!("raft: refusing a snapshot that did not install");
            return refusal;
        };

        state
            .machine
            .install_snapshot(&self.registry, store, ownership, meta.last_index);
        state.log.reset_to_snapshot(meta.last_index, meta.last_term);
        // The fence jumps rather than advances: everything through this index is
        // now visible, however little of it arrived as entries.
        self.fence.reset(meta.last_index);
        state.commit_index = state.commit_index.max(meta.last_index);
        tracing::info!(
            member = self.layout.local.name,
            through = meta.last_index,
            resources = meta.resources,
            "raft: installed a snapshot",
        );

        InstallSnapshotReply {
            term: state.term,
            bytes_received: assembled as u64,
            done: true,
        }
    }

    fn on_install_snapshot_reply(&self, peer: u64, message: &InstallSnapshotReply) {
        let mut state = self.state.lock();
        if message.term > state.term {
            state.step_down(message.term);
            return;
        }
        if state.role != Role::Leader {
            return;
        }
        let Some(meta) = state.snapshot_meta else {
            return;
        };
        if !state.peers.contains_key(&peer) {
            return;
        }

        if let Some(tracked) = state.peers.get_mut(&peer) {
            // A peer working through a transfer is answering, and must count
            // toward check-quorum exactly as an append reply does. In
            // `go.etcd.io/raft` the snapshot acknowledgement arrives as an
            // ordinary `MsgAppResp`, so it sets `RecentActive` on the same line.
            tracked.last_heard_at = Some(Instant::now());
            tracked.snapshot_in_flight = false;
            if message.done {
                tracked.next_index = meta.last_index.saturating_add(1);
                tracked.match_index = meta.last_index;
                tracked.snapshot_offset = 0;
            } else {
                // `bytes_received` is how much the follower has assembled, so
                // it is both the acknowledgement and the offset to resume from
                // -- including zero, which is the follower saying it threw the
                // transfer away.
                tracked.snapshot_offset = usize::try_from(message.bytes_received).unwrap_or(0);
            }
        }

        if message.done {
            self.send_append(&mut state, peer);
        } else {
            self.send_snapshot(&mut state, peer);
        }
    }

    async fn on_propose(
        &self,
        _peer: u64,
        message: &crate::messages::Propose,
    ) -> crate::messages::ProposeReply {
        let (reply, replicate, wake_apply) = {
            let mut state = self.state.lock();
            if state.role != Role::Leader {
                return crate::messages::ProposeReply {
                    accepted: false,
                    reason: "not the leader".to_owned(),
                    term: state.term,
                    first_index: 0,
                    request_id: message.request_id,
                    leader: state.leader,
                };
            }

            let mut operations = Vec::with_capacity(message.proposals.len());
            for raw in &message.proposals {
                match Operation::decode(raw) {
                    Ok(operation) => operations.push(operation),
                    Err(error) => {
                        return crate::messages::ProposeReply {
                            accepted: false,
                            reason: format!("undecodable proposal: {}", error.0),
                            term: state.term,
                            first_index: 0,
                            request_id: message.request_id,
                            leader: Some(self.layout.local.index),
                        };
                    }
                }
            }
            if operations.is_empty() {
                return crate::messages::ProposeReply {
                    accepted: true,
                    reason: String::new(),
                    term: state.term,
                    first_index: 0,
                    request_id: message.request_id,
                    leader: Some(self.layout.local.index),
                };
            }

            let first = state.append_local(operations).map_or(0, |(first, _)| first);
            let wake = if state.peers.is_empty() {
                self.advance_commit(&mut state)
            } else {
                false
            };
            (
                crate::messages::ProposeReply {
                    accepted: true,
                    reason: String::new(),
                    term: state.term,
                    first_index: first,
                    request_id: message.request_id,
                    leader: Some(self.layout.local.index),
                },
                true,
                wake,
            )
        };

        if replicate {
            self.replicate();
        }
        if wake_apply {
            self.apply_wake.notify_one();
        }
        reply
    }

    async fn on_forward(
        &self,
        _peer: u64,
        message: &crate::messages::Forward,
    ) -> crate::messages::ForwardReply {
        // `upgrade` failing means the backend has been dropped, which is the
        // same situation as one never having been installed: this member is
        // not serving registrations, and says so rather than pretending.
        let handler = self
            .forwarder
            .lock()
            .as_ref()
            .and_then(std::sync::Weak::upgrade);
        match handler {
            Some(handler) => handler.forward(message).await,
            None => crate::messages::ForwardReply {
                ok: false,
                created: false,
                error: "unavailable".to_owned(),
                detail: "this member is not serving registrations".to_owned(),
                applied_index: 0,
                not_owner: false,
                request_id: message.request_id,
                owner: None,
            },
        }
    }

    fn on_peer_state(&self, peer: u64, up: bool, incarnation: u64) {
        let mut state = self.state.lock();
        let Some(tracked) = state.peers.get_mut(&peer) else {
            return;
        };
        tracked.up = up;
        if !up {
            tracked.catching_up = false;
            return;
        }

        if tracked.incarnation != 0 && incarnation != tracked.incarnation {
            tracing::info!(
                peer,
                was = tracked.incarnation,
                now = incarnation,
                "raft: member restarted",
            );
        }
        tracked.incarnation = incarnation;

        if state.role == Role::Leader {
            let next = state.log.last_index().saturating_add(1);
            let append_sequence = state.append_sequence;
            if let Some(tracked) = state.peers.get_mut(&peer) {
                tracked.next_index = next;
                tracked.match_index = 0;
                // Everything in flight to the incarnation that has gone is now
                // disowned; a reply it already sent must not be read as news
                // about the one that replaced it.
                tracked.reply_floor = append_sequence;
                // A reconnect invalidates anything in flight: neither the chunk
                // nor the append it was waiting on will ever be answered, and
                // holding the pause open would strand the peer.
                tracked.snapshot_in_flight = false;
                tracked.pending_request = 0;
                tracked.snapshot_offset = 0;
                // The peer may have restarted and lost everything, so what it
                // was last told about the commit index says nothing now.
                tracked.sent_commit = 0;
            }
            self.send_append(&mut state, peer);
        }
    }
}
