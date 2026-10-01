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
//! has been caught up and explicitly promoted. While non-voting its vote does
//! not count -- it still answers, saying `voting = false`, and a candidate
//! counts such a grant only where its own round proves that no quorum of voters
//! can exist (see "When every voter has forgotten" below) -- and it reports
//! `catching_up` so the leader does not count its acknowledgements toward a
//! commit.
//!
//! # When every voter has forgotten
//!
//! Only a leader promotes, so once a quorum's worth of members are non-voting
//! no election can succeed and nobody is ever promoted: restarting a whole
//! cluster reaches that state on the second boot. The way out is that a
//! guarantee already destroyed cannot be protected. While a quorum of voters is
//! still *possible* the ordinary rule stands; once one is provably impossible,
//! members that have forgotten vote again -- but only for a candidate approved
//! by **every** member not proven to have forgotten, since any surviving copy
//! of a committed entry is on one of them, and it refuses a candidate lacking
//! it.
//!
//! What counts as proof is the part that has to be right. A member has
//! forgotten, for the election of term T, only if its own reply to this
//! candidacy says `voting = false` at term T. That reply is binding: the member
//! has adopted T, and `on_promote` refuses a promotion from an earlier term, so
//! it stays non-voting until a leader of T or later promotes it -- which cannot
//! exist while T is being decided. The candidate does the arithmetic, over
//! nothing but such replies and its own condition ([`NodeState::won`]); the
//! voters do none. Evidence kept between rounds went stale -- a member is
//! promoted without the observer hearing of it -- and the chaos soak measured
//! a candidate counting as forgotten a member it had itself promoted, and
//! winning without the committed entries that member held (seeds 59925,
//! 110797, 111540). A pre-vote predicts the election by the same arithmetic,
//! counting as forgotten only members that *granted* while saying so: nothing
//! in a pre-vote is binding, and a prediction that promised a recovery the real
//! round would refuse would raise the term against a live leader.
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
//!
//! # What happens on a defect
//!
//! A member that finds its own state impossible stops ([`RaftInvariantViolated`]
//! -> [`RaftNode::fail`]): it relinquishes, answers every waiting caller
//! "unavailable", closes, and tells its owner ([`RaftNode::wait_for_failure`]),
//! which ends the process with status 1 for a service manager to restart. A
//! panic is the other kind of defect, and the process is what answers it: the
//! binary's panic policy (`nmos-registry-bin`'s `panic_policy`) aborts on any
//! panic, in the tick, in apply, in a handler the transport delivered to, or
//! anywhere else -- which is why nothing in this module joins the tasks it
//! spawns or catches an unwind. The Python member stops the same way on an
//! exception nothing in it expected (`RaftUnexpectedError`). The one failure a
//! tick expects, a term file that cannot be saved, is logged and retried at the
//! next timeout, in both.

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
use crate::errors::{RaftCursorReservationFailed, RaftInvariantViolated};
use crate::log::{AppendError, Entry, RaftLog};
use crate::machine::{Outcome, StateMachine};
use crate::messages::{
    AppendEntries, AppendEntriesReply, InstallSnapshot, InstallSnapshotReply, Message, Promote,
    ReadIndex, ReadIndexReply, RequestVote, RequestVoteReply, WireEntry,
};
use crate::operations::{Operation, OperationKind, ProposalId};
use crate::persist::{PersistentState, PersistentStateError, TermStore};
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
    /// Entries in one `AppendEntries`, at most.
    pub max_entries_per_append: usize,
    /// Payload bytes of entries in one `AppendEntries`, and of operations in
    /// one `Propose`, at most; an entry larger than it travels alone.
    ///
    /// etcd's `MaxSizePerMsg` (`raft.go`), 1 MiB there too
    /// (`etcdserver/raft.go`), applied as its `limitSize` applies it.
    /// [`crate::wire::MAX_FRAME`] is the backstop sixteen times above this,
    /// refused at encode time, not the bound: a window of 256 entries was
    /// bounded by count alone, and a window of nine 2 MiB entries was never
    /// sent and retried for ever (part 21 of the fix record).
    pub max_append_bytes: usize,
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
            max_append_bytes: 1 << 20,
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
    /// The snapshot this peer's transfer is *of*, pinned when it starts.
    ///
    /// An offset means something only relative to one byte sequence, and
    /// compaction replaces this member's snapshot whenever it likes. Slicing the
    /// current one at a saved offset spliced the head of one snapshot to the
    /// tail of the next -- the chaos soak's splice detector measured it, and when
    /// the result happened to decode it installed: replicas that had lost
    /// acknowledged writes while reporting themselves caught up. Pinned here,
    /// every chunk and the completion's credit come from the one snapshot the
    /// transfer began with, as etcd's server streams one point-in-time snapshot
    /// per transfer (`server/etcdserver/snapshot_merge.go:36-39`). Released when
    /// the transfer completes or is abandoned; the payload is shared, not copied.
    sending: Option<(SnapshotMeta, Arc<[u8]>)>,
    /// The commit index last *sent* to this peer.
    ///
    /// `go.etcd.io/raft` calls this `sentCommit` and gates an eager send on it
    /// (`tracker/progress.go:189`, `CanBumpCommit`). Without it the leader
    /// either re-sends the same commit index to a peer that already has it, or
    /// -- which is what happened here -- never sends it at all until the next
    /// heartbeat.
    sent_commit: u64,
    /// The id of the chunk out and unanswered, or 0 when none is.
    ///
    /// One chunk at a time, and only its reply drives the transfer. Without
    /// the first, the transfer was driven from two places at once -- the
    /// replication tick and the previous chunk's reply -- so two chunks went
    /// out carrying the same offset and the pair looped for ever. A flag alone
    /// was not enough for the second: a reconnect is reported for the CONTROL
    /// connection while chunks travel on BULK, so resetting on a reconnect
    /// cleared the flag while the last chunk was still in flight *and still
    /// answered* -- and that answer, taken as the one awaited, started a second
    /// stream beside the new one (seed 60195). The id says which reply is
    /// awaited; `reply_floor` fences everything sent before a reset.
    snapshot_request: u64,
    /// The BULK connection the chunk in flight went out on
    /// (`Transport::connection`).
    ///
    /// A chunk and its answer travel one connection, and while it is in place
    /// TCP delivers both, in order -- each end's read deadline closes one that
    /// stops moving -- so a chunk can be lost only with the connection it went
    /// out on. That connection ending is what sends it again, and nothing else
    /// is.
    ///
    /// It used to be a timer: a chunk unanswered for `election_min` went out
    /// again under a new id, the backstop for one lost with its BULK connection
    /// while CONTROL stayed up, which nothing reports (S6). A timer cannot tell
    /// a slow chunk from a lost one. Where one chunk's round trip outlasts
    /// `election_min` every answer arrived already superseded by the next copy,
    /// only the newest copy's driving the transfer, so the transfer never passed
    /// its first chunk -- measured over real sockets as 194 copies of chunk 0 in
    /// 30 s at 16 KiB/s, with 4 KiB chunks and a 150 ms `election_min` -- and
    /// every copy, a whole chunk, queued behind the first in this member's
    /// memory. etcd never sends a snapshot again because time has passed
    /// either: a peer awaiting one is paused until the transport reports the
    /// transfer failed (`tracker/progress.go:268-269`, `raft.go:1611-1628`).
    snapshot_carrier: Option<u64>,
    /// The highest append this peer has answered in this leadership.
    ///
    /// What a read waits for (`RaftNode::confirm_reads`): a reply, in this
    /// term, to an append sent after the read was recorded is this peer saying
    /// the leader still leads -- etcd's heartbeat context, echoed back
    /// (`read_only.go`, `recvAck`), with the append sequence as the position.
    heard_request: u64,
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
            sending: None,
            snapshot_request: 0,
            snapshot_carrier: None,
            heard_request: 0,
            reply_floor: 0,
            last_heard_at: None,
        }
    }
}

/// A snapshot being received: which one, and the bytes of it so far.
///
/// Reassembling by offset alone accepted the next chunk of *any* snapshot the
/// sender happened to be slicing -- the head of one and the tail of another,
/// joined because the offsets lined up. The identity is what makes an offset
/// mean something: a chunk continues this assembly only if it belongs to the
/// same transfer.
#[derive(Debug)]
struct Assembly {
    /// `(term, leader, last_index, last_term)` of the chunk that began it.
    identity: (u64, u64, u64, u64),
    data: Vec<u8>,
}

/// Is `chunk`, at `offset`, already part of `assembled`?
///
/// Decided by the bytes, not the offset: a copy is the same bytes at the same
/// place. Anything else at an offset already passed is no copy, and keeping the
/// buffer for it would be a splice.
/// Consecutive groups of `payloads`, each within `max_bytes`.
///
/// Order kept, nothing dropped, and never fewer than one payload in a group,
/// so a payload larger than the bound still goes, alone -- the rule
/// [`RaftLog::window`] applies to a leader's entries, for a follower's
/// proposals.
fn split_by_bytes(payloads: Vec<Vec<u8>>, max_bytes: usize) -> Vec<Vec<Vec<u8>>> {
    let mut groups = Vec::new();
    let mut current: Vec<Vec<u8>> = Vec::new();
    let mut total = 0usize;
    for payload in payloads {
        if !current.is_empty() && total.saturating_add(payload.len()) > max_bytes {
            groups.push(std::mem::take(&mut current));
            total = 0;
        }
        total = total.saturating_add(payload.len());
        current.push(payload);
    }
    if !current.is_empty() {
        groups.push(current);
    }
    groups
}

fn holds(assembled: &[u8], offset: usize, chunk: &[u8]) -> bool {
    offset
        .checked_add(chunk.len())
        .and_then(|end| assembled.get(offset..end))
        .is_some_and(|held| held == chunk)
}

/// A read waiting for a quorum to confirm that this member still leads.
///
/// etcd's `readIndexRequest` (`read_only.go`). `index` is the commit index it
/// reads at, unknown until this leader has committed an entry of its own term;
/// `after` the append sequence then, since only a reply to a later append is
/// evidence gathered after the read began.
#[derive(Debug)]
struct Read {
    reply: oneshot::Sender<Result<u64, RaftUnavailable>>,
    index: Option<u64>,
    after: u64,
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
    votes: BTreeSet<u64>,
    pre_votes: BTreeSet<u64>,
    /// Members that refused this pre-vote round.
    ///
    /// Each one is a member that cannot later grant, so they are what
    /// decide when the round can no longer be won -- see
    /// `pre_vote_is_lost`.
    pre_refusals: BTreeSet<u64>,
    /// Who has forgotten, as the *current* round has proved it: peers whose
    /// reply to this candidacy said `voting = false` at its term.
    ///
    /// Reset with the votes and never carried from one round to the next --
    /// see "When every voter has forgotten" in the module docs. A peer that has
    /// not replied is absent and so counts as a voter, which is what keeps a
    /// partition from looking like a cluster that has forgotten everything.
    forgotten: BTreeSet<u64>,
    /// The same for the pre-vote round, counted from grants only.
    pre_forgotten: BTreeSet<u64>,
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
    /// Reads waiting for a quorum to confirm this leadership (`read_index`).
    reads: Vec<Read>,
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
    snapshot: Arc<[u8]>,
    snapshot_meta: Option<SnapshotMeta>,
    /// Inbound transfers, by the leader sending them.
    installing: HashMap<u64, Assembly>,
}

impl NodeState {
    fn quorum(&self, layout: &RaftLayout) -> usize {
        layout.quorum()
    }

    /// Can this pre-vote round no longer be won, however the rest answer?
    ///
    /// Every member that has refused is one that cannot later grant, so the
    /// best case left is everyone else saying yes. When even that falls short
    /// of a quorum the round is decided, and etcd's `VoteResult` calls it
    /// `VoteLost`.
    ///
    /// Deliberately *not* expressed through [`Self::won`]: that asks whether
    /// the votes in hand are sufficient, and the question here is whether the
    /// votes still outstanding could ever be. A member with a forgotten log
    /// makes `won` stricter still, which can only make losing come sooner, so
    /// this bound stays correct under the recovery clause as well.
    fn pre_vote_is_lost(&self, layout: &RaftLayout) -> bool {
        let still_possible = layout.size().saturating_sub(self.pre_refusals.len());
        still_possible < layout.quorum()
    }

    /// Has this round collected enough of the right votes?
    ///
    /// Ordinarily a quorum of grants from members that can vote: a grant from
    /// one that has forgotten says only that the candidate is as current as an
    /// empty log, which proves nothing. Once the round's own replies prove a
    /// quorum of voters impossible -- `forgotten`, plus this member if it has
    /// forgotten too -- a quorum of grants of any kind is necessary but not
    /// sufficient: every member not proven to have forgotten must *also* have
    /// granted, because those are the only members whose up-to-dateness check
    /// still means anything, and the surviving copy of a committed entry can
    /// only be on one of them. See "When every voter has forgotten" in the
    /// module docs.
    ///
    /// Members nobody has heard from count among those, and they cannot have
    /// granted -- so a partitioned cluster never satisfies this, which is the
    /// intended answer.
    fn won(&self, layout: &RaftLayout, tally: &BTreeSet<u64>, forgotten: &BTreeSet<u64>) -> bool {
        let members: BTreeSet<u64> = layout.members.iter().map(|m| m.index).collect();
        let mut proven: BTreeSet<u64> = forgotten.intersection(&members).copied().collect();
        if !self.voting {
            proven.insert(layout.local.index);
        }
        let granted: BTreeSet<u64> = tally.intersection(&members).copied().collect();
        let quorum = self.quorum(layout);
        if granted.difference(&proven).count() >= quorum {
            return true;
        }
        if layout.size().saturating_sub(proven.len()) >= quorum {
            return false;
        }
        granted.len() >= quorum
            && members
                .difference(&proven)
                .all(|member| granted.contains(member))
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

    /// Make the term and vote durable, before anything that depends on them
    /// leaves this member.
    ///
    /// # Errors
    ///
    /// The store's, when the save fails. Returned, as the Python raises it, so
    /// that whatever made the decision stops there and sends nothing that rests
    /// on it: no vote granted, no campaign's requests, no answer in the new
    /// term. The decision stays in memory, as the Python's does. Nothing was
    /// promised on it, so a restart that reads the older file forgets nothing a
    /// peer was told. Logged and carried past instead, it was: measured, a
    /// member whose saves failed granted a vote, answered a new term's append
    /// and snapshot, and, alone, made itself leader.
    fn persist(&mut self) -> Result<(), PersistentStateError> {
        let state = PersistentState {
            term: self.term,
            voted_for: self.voted_for,
            incarnation: self.incarnation,
            // The bound already durable, carried over: this save is about the
            // term, and must not erase the reservation the last one recorded.
            cursor_reservation: self.machine.cursors().reservation(),
        };
        self.terms.save(&state)
    }

    /// Make a cursor reservation durable, then note it.
    ///
    /// Propagated, unlike [`Self::persist`]'s failure: there the caller is a
    /// consensus decision already made, here it is a cursor not yet handed out,
    /// and refusing it is both possible and the only safe answer.
    fn reserve_cursors(&mut self, upto: TaiCursor) -> Result<(), RaftCursorReservationFailed> {
        let state = PersistentState {
            term: self.term,
            voted_for: self.voted_for,
            incarnation: self.incarnation,
            cursor_reservation: Some(upto),
        };
        self.terms.save(&state).map_err(|error| {
            RaftCursorReservationFailed(format!(
                "could not reserve paging cursors up to {upto} in {}: {}",
                self.terms.path().display(),
                error.0,
            ))
        })?;
        self.machine.cursors_mut().confirm_reservation(upto);
        Ok(())
    }

    /// Adopt a higher term and return to following.
    ///
    /// Releases no proposal, for the reason `RaftNode::relinquish` gives; fails
    /// any read, for the reason it gives too.
    ///
    /// # Errors
    ///
    /// [`Self::persist`]'s. The save comes last, as in the Python, so a failed
    /// one leaves the new term adopted in memory and the caller stops.
    fn step_down(&mut self, term: u64) -> Result<bool, PersistentStateError> {
        self.fail_reads("no longer the leader: a later term began");
        let was_leader = self.role == Role::Leader;
        self.term = term;
        // Every partial snapshot was sent in an earlier term, and no chunk of an
        // earlier term is accepted any more, so none of them can finish. Kept,
        // each was memory held for the life of the member.
        self.installing.clear();
        self.voted_for = None;
        self.role = Role::Follower;
        self.leader = None;
        self.votes.clear();
        self.pre_votes.clear();
        self.pre_refusals.clear();
        self.forgotten.clear();
        self.pre_forgotten.clear();
        self.persist()?;
        Ok(was_leader)
    }

    /// Fail every read waiting here: see `RaftNode::relinquish`.
    fn fail_reads(&mut self, reason: &str) {
        for read in self.reads.drain(..) {
            drop(read.reply.send(Err(RaftUnavailable(reason.to_owned()))));
        }
    }

    /// etcd's `committedEntryInCurrentTerm` (`raft.go:2065-2070`).
    ///
    /// Until a leader commits an entry of its own term it cannot know how far
    /// earlier terms committed (Raft §8), so its commit index is no bound on
    /// what a read must see: etcd postpones reads until then
    /// (`raft.go:1365-1367`).
    fn committed_in_current_term(&self) -> bool {
        self.log
            .term_at(self.commit_index)
            .is_ok_and(|term| term == self.term)
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
                tracing::error!(error = %error, "raft: local append refused");
                None
            }
        }
    }

    /// Where this leader's append contradicts an entry committed here, or
    /// `None`.
    ///
    /// Raft makes it impossible: a leader holds every entry committed before
    /// its term (Leader Completeness, section 5.4.3), and Log Matching makes its
    /// entries at those indexes the ones committed here. So a disagreement
    /// proves committed data lost from the cluster -- which the non-voting
    /// rejoin and the recovery election's veto exist to prevent, so it means a
    /// defect. `go.etcd.io/raft` treats the same observation as corruption and
    /// panics (`maybeAppend`, `log.go:117-121`).
    ///
    /// Checked wherever this member still can: the anchor, and every entry the
    /// message carries, that lie from the snapshot boundary -- whose term is
    /// kept -- up to the commit index. Below the boundary nothing is left to
    /// compare, and the leader's next append, anchored at the commit point, is
    /// where it shows. Before the `prev_log_index < commit_index` answer,
    /// deliberately: that answer credits the leader with `match = commit`
    /// without looking, and an anchor that matches below the commit point can
    /// still carry entries contradicting it. At most a batch of comparisons.
    fn contradicting_committed(&self, message: &AppendEntries) -> Option<String> {
        let floor = self.log.snapshot_index();
        let ceiling = self.commit_index.min(self.log.last_index());
        std::iter::once((message.prev_log_index, message.prev_log_term))
            .chain(message.entries.iter().map(|wire| (wire.index, wire.term)))
            .filter(|&(index, _)| (floor..=ceiling).contains(&index))
            .find_map(|(index, term)| {
                let held = self.log.term_at(index).ok()?;
                (held != term).then(|| {
                    format!(
                        "leader {} of term {} holds index {index} at term {term}, committed \
                         here at term {held}: committed data was lost from the cluster",
                        message.leader, message.term,
                    )
                })
            })
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
    /// The broken invariant this member stopped on, once it has (`fail`), and
    /// how its owner learns of it (`wait_for_failure`).
    failure: tokio::sync::watch::Sender<Option<String>>,
    /// One `close` at a time. A member that stops itself begins closing on its
    /// own, and its owner closes it again on the way out.
    closing_now: tokio::sync::Mutex<()>,
    /// This member, for the part of `fail` that needs a task of its own.
    me: std::sync::Weak<Self>,
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
    ///
    /// # Errors
    ///
    /// [`PersistentStateError`] if the term file cannot be loaded, or, for a
    /// member that has none yet, cannot be written. Fatal at startup by
    /// design, as the Python's constructor makes it: a member that started
    /// anyway would have no memory of a vote it may already have cast.
    pub fn new(
        layout: RaftLayout,
        transport: Arc<dyn Transport>,
        mut terms: TermStore,
        mut machine: StateMachine,
        registry: Arc<Registry>,
        timing: RaftTiming,
    ) -> Result<Arc<Self>, PersistentStateError> {
        // Loading is what increments the incarnation, so it happens once, here,
        // and the transport is told the answer rather than reading it again.
        //
        // A refusal is returned, never replaced by a fresh start. Term 0, no
        // vote and incarnation 1 is what a brand-new member starts from, and a
        // member that is not brand new would, from there, grant a second vote
        // in a term it has already voted in; count as a voter at once rather
        // than rejoining as a learner; take proposal ids from its first
        // incarnation's range again and resume no cursor reservation; and, at
        // its first save, overwrite the file that was the only record of its
        // vote. Measured on the fallback this replaced: the second vote was
        // granted, and the file overwritten with it.
        let persisted = terms.load()?;
        // Before anything can allocate: the previous incarnation may have
        // handed out any cursor up to this, and this one must start above it.
        machine.cursors_mut().resume(persisted.cursor_reservation);

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
            forgotten: BTreeSet::new(),
            pre_forgotten: BTreeSet::new(),
            votes: BTreeSet::new(),
            pre_votes: BTreeSet::new(),
            pre_refusals: BTreeSet::new(),
            append_sequence: 0,
            sequence: persisted
                .incarnation
                .saturating_mul(PROPOSALS_PER_INCARNATION),
            waiters: HashMap::new(),
            reads: Vec::new(),
            deadline: now,
            heard_from_leader_at: None,
            machine,
            terms,
            snapshot: Arc::from(Vec::new()),
            snapshot_meta: None,
            installing: HashMap::new(),
        };

        let (batcher, drain) = proposal_channel(MAX_BATCH);
        Ok(Arc::new_cyclic(|me| Self {
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
            failure: tokio::sync::watch::Sender::new(None),
            closing_now: tokio::sync::Mutex::new(()),
            me: me.clone(),
        }))
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

    /// Callers still waiting for a proposal to resolve.
    ///
    /// Diagnostic, and the point of it is that it must come back to zero.
    /// Every entry is a `oneshot::Sender` held on behalf of a client, so an
    /// entry never removed is a caller never answered *and* memory never
    /// released -- reachable memory, owned by a live map, which no leak
    /// detector will ever report. The only way to know is to assert it.
    #[must_use]
    pub fn pending_waiters(&self) -> usize {
        self.state.lock().waiters.len()
    }

    /// Reads waiting for a quorum to confirm this leadership.
    ///
    /// Diagnostic, like [`Self::pending_waiters`]: each holds a caller, and a
    /// read whose caller has gone is dropped at the next reply this leader
    /// hears -- so outside a leadership, and a heartbeat after its callers
    /// finish, this is zero.
    #[must_use]
    pub fn pending_reads(&self) -> usize {
        self.state.lock().reads.len()
    }

    /// Partly-received snapshots being reassembled, one buffer per peer.
    ///
    /// Diagnostic, and bounded by the peer count rather than by traffic -- but
    /// each buffer is a whole snapshot, so one left behind by a transfer that
    /// neither finished nor was refused is megabytes held for the life of the
    /// process.
    #[must_use]
    pub fn snapshot_buffers(&self) -> usize {
        self.state.lock().installing.len()
    }

    /// The index an open snapshot capture is pinned at, if one is open.
    ///
    /// Diagnostic. A compaction holds one for as long as it takes to serialise
    /// the store, across yields; an install abandons it.
    #[must_use]
    pub fn snapshot_capture(&self) -> Option<u64> {
        self.state
            .lock()
            .machine
            .snapshots()
            .capture()
            .map(|capture| capture.index)
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

    /// The next index this leader will send one peer, for diagnostics.
    ///
    /// Exposed beside [`Self::peer_progress`] because the two are one
    /// invariant, `match_index < next_index` (etcd, `tracker/progress.go:211`):
    /// a leader whose next index falls to or below what a peer has acknowledged
    /// re-sends entries the peer already holds.
    #[must_use]
    pub fn peer_next_index(&self, peer: u64) -> Option<u64> {
        self.state
            .lock()
            .peers
            .get(&peer)
            .map(|tracked| tracked.next_index)
    }

    /// The snapshot this member holds for followers below its log, for
    /// diagnostics: `(last_index, payload bytes)`, or `None` when it holds none.
    ///
    /// Exposed because the invariant it lets a soak check -- the compacted
    /// prefix of this member's log is always recoverable from its own snapshot
    /// -- is otherwise invisible from outside, and a member that breaks it can
    /// lead for as long as it likes while sending its stranded followers
    /// nothing but keepalives. etcd treats the same condition as impossible:
    /// `maybeSendSnapshot` panics with "need non-empty snapshot"
    /// (`raft.go:680-682`).
    #[must_use]
    pub fn snapshot_held(&self) -> Option<(u64, usize)> {
        let state = self.state.lock();
        state
            .snapshot_meta
            .filter(|_| !state.snapshot.is_empty())
            .map(|meta| (meta.last_index, state.snapshot.len()))
    }

    /// The bytes of the snapshot [`Self::snapshot_held`] describes -- what a
    /// follower below this member's log would be caught up from. Empty when it
    /// holds none.
    ///
    /// Diagnostic, and shared rather than copied. Holding a snapshot is not the
    /// same as holding the right one: a member whose snapshots were taken of a
    /// store it no longer serves would pass every check on the metadata.
    #[must_use]
    pub fn snapshot_payload(&self) -> Arc<[u8]> {
        Arc::clone(&self.state.lock().snapshot)
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

    /// The next paging cursor for a resource type, reserved durably.
    ///
    /// On the node rather than exposing the allocator, because the allocator's
    /// high-water mark is consensus state: handing out a `&mut` to it would let
    /// a caller allocate without the lock that keeps two mutations from taking
    /// the same lane position -- and without the reservation.
    ///
    /// About once per `RESERVATION_WINDOW_SECONDS` of cursor progress the cursor
    /// lies beyond the reservation on disk, and a new bound is written --
    /// synchronously, beside the term and vote, for the reason `persist.rs`
    /// gives -- before the cursor is returned. Every other call is the
    /// allocator's arithmetic alone.
    ///
    /// # Errors
    ///
    /// [`RaftCursorReservationFailed`] if the bound could not be written. The
    /// cursor is not returned, so nothing that could repeat it after a restart
    /// has left this member; the allocator simply moves past it.
    pub fn allocate_cursor(
        &self,
        resource_type: ResourceType,
    ) -> Result<TaiCursor, RaftCursorReservationFailed> {
        let mut state = self.state.lock();
        let cursor = state.machine.cursors_mut().allocate(resource_type);
        if let Some(upto) = state.machine.cursors().reservation_needed(cursor) {
            state.reserve_cursors(upto)?;
        }
        Ok(cursor)
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

    /// Whether enough members are reachable for a write to commit.
    ///
    /// What the backend reports readiness from. Members catching up are left
    /// out, as the commit count leaves them out: while a majority of the
    /// cluster is catching up nothing can commit, and saying otherwise would
    /// report `Ready` in front of writes that cannot land -- which this did,
    /// counting every live link, until it was made the Python's. Reachability,
    /// not responsiveness -- [`Self::quorum_is_answering`] is what a leader asks
    /// of itself -- and not whether an election could be won, which is
    /// [`Self::reaches_a_majority`].
    #[must_use]
    pub fn has_quorum(&self) -> bool {
        let live = self.transport.live();
        let voting = {
            let state = self.state.lock();
            live.iter()
                .filter(|peer| {
                    state
                        .peers
                        .get(peer)
                        .is_some_and(|tracked| !tracked.catching_up)
                })
                .count()
        };
        voting.saturating_add(1) >= self.layout.quorum()
    }

    /// Whether a majority of the cluster is reachable, this member included.
    ///
    /// What the tick asks before campaigning: with fewer, no election can be
    /// won. Members catching up count. Whether their votes do is for the
    /// round's own replies to decide (`NodeState::won`), and they decide it
    /// exactly when it matters -- a majority having restarted, the one member
    /// still holding the log must lead them back. The Python left them out
    /// here, and that left the member unable ever to try: measured, two of
    /// three restarting together deadlocked its cluster for good.
    ///
    /// Takes no lock, so the tick may ask it while holding the state.
    fn reaches_a_majority(&self) -> bool {
        self.transport.live().len().saturating_add(1) >= self.layout.quorum()
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
    /// Members catching up count: the question is whether this leader is cut
    /// off, and a member that answered is proof it is not. With a majority of
    /// the cluster answering, no other member can gather one -- every member
    /// answering refuses other candidates while it hears from this one -- so
    /// standing down could only leave nobody to lead them back to voting. etcd
    /// leaves learners out of `QuorumActive` because they are outside its voter
    /// set, and so outside the quorum as well; a member catching up stays inside
    /// the quorum here, and leaving it out of the count alone made that quorum
    /// unreachable -- measured, a survivor holding the only copy of the log
    /// won, stood down a tick later, and won again, each new term throwing away
    /// the snapshot the last had begun. What these answers count toward is only
    /// whether to go on leading: commits, read confirmations and votes still
    /// leave them out.
    fn quorum_is_answering(&self, state: &NodeState, now: Instant) -> bool {
        let window = Duration::from_millis(self.timing.election_max_ms);
        let answering = state
            .peers
            .values()
            .filter(|peer| {
                peer.last_heard_at
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

    /// The broken invariant this member stopped on, or `None` (`fail`).
    #[must_use]
    pub fn failure(&self) -> Option<String> {
        self.failure.borrow().clone()
    }

    /// Return once this member has stopped itself on a broken invariant.
    ///
    /// For the process that owns it, which must then exit: the member takes no
    /// further part, and only a restart -- which brings it back with nothing,
    /// to be caught up as a non-voting learner -- makes it whole again
    /// ([`RaftInvariantViolated`]). `main.rs` waits on it beside the listeners,
    /// so the failure ends the process with status 1.
    pub async fn wait_for_failure(&self) -> String {
        let mut watching = self.failure.subscribe();
        loop {
            if let Some(failure) = watching.borrow_and_update().as_ref() {
                return failure.clone();
            }
            if watching.changed().await.is_err() {
                // Only once the sender is dropped, and it is a field of this
                // member, which `&self` keeps alive: never.
                std::future::pending::<()>().await;
            }
        }
    }

    /// Stop this member for good: a broken invariant stays broken.
    ///
    /// Fail-stop, the position [`RaftInvariantViolated`] documents and the one
    /// `go.etcd.io/raft` takes with `Panicf`: continuing from a state proven
    /// impossible can only spread the damage. So before this returns, every
    /// part that could spread it has stopped -- leadership, whose heartbeats
    /// would carry a commit index this member no longer vouches for; elections
    /// (`tick`); applying; and every caller waiting on an answer, told
    /// "unavailable" (a 503 the Node retries) because this member will never
    /// apply its entry. The tasks and the transport close right after, which
    /// is when peers see the member gone, and the owner is told
    /// (`wait_for_failure`) so the process can exit: a restart -- automatic
    /// under a service manager -- brings the member back with nothing, and the
    /// leader catches it up as a non-voting learner.
    ///
    /// Once only: a second violation found while stopping says nothing new.
    fn fail(&self, error: &RaftInvariantViolated) {
        let first = self.failure.send_if_modified(|failure| {
            if failure.is_some() {
                return false;
            }
            *failure = Some(error.0.clone());
            true
        });
        if !first {
            return;
        }
        tracing::error!(
            error = %error,
            member = self.layout.local.name,
            "raft: consensus invariant violated; this member stops",
        );
        self.closing
            .store(true, std::sync::atomic::Ordering::SeqCst);
        let reason = format!("member stopped: consensus invariant violated: {error}");
        {
            let mut state = self.state.lock();
            self.relinquish(&mut state, "consensus invariant violated", Instant::now());
            // Released here, unlike `relinquish`, which releases nothing
            // because a later leader may yet commit what a caller waits on:
            // this member will never apply it, whoever commits it.
            for (_, sender) in state.waiters.drain() {
                drop(sender.send(Err(RaftUnavailable(reason.clone()))));
            }
            state.fail_reads(&reason);
        }
        // A task of its own: this may be running inside one that `close`
        // aborts and waits for.
        if let Some(me) = self.me.upgrade() {
            tokio::spawn(async move { me.close().await });
        }
    }

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
    ///
    /// One at a time: a member that stops itself (`fail`) begins closing on its
    /// own, and its owner closes it again on the way out. Everything below is
    /// idempotent once serialised -- a second close finds nothing left to do.
    pub async fn close(&self) {
        let _one_at_a_time = self.closing_now.lock().await;
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
        self.state.lock().fail_reads("member is shutting down");
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
        if self.closing.load(std::sync::atomic::Ordering::SeqCst) {
            // A tick whose sleep began before a stop (`fail`, `close`) must not
            // lead or campaign after it: the loop only looks between sleeps.
            return;
        }
        let now = Instant::now();
        let wake_apply = {
            let mut state = self.state.lock();
            // A waiter goes when its entry applies (`resolve`) -- or, as here,
            // when its caller has stopped waiting: dropped the `propose`
            // future, closing the channel. It used to leave only the first
            // way, so a forwarded proposal that never became an entry here --
            // its `Propose` never delivered, refused by a member no longer
            // leading, or accepted and then overwritten -- kept one for the
            // life of the member: 1,046 of them in 40 chaos-soak runs, every
            // one's caller long gone. etcd removes the waiter when the client's
            // context ends (`v3_server.go:1117`, `:1129`, "GC wait"). The entry
            // may still commit, with nobody waiting: what a caller answered
            // "unavailable" was told to expect. Swept each tick, so a waiter
            // outlives its caller by at most a heartbeat.
            state.waiters.retain(|_, reply| !reply.is_closed());
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

            if !self.reaches_a_majority() {
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

            // **No snapshot gate here, deliberately.** One stood here --
            // "absorbing a snapshot is not a moment to campaign" -- modelled on
            // etcd's `promotable()`, which refuses while
            // `hasNextOrInProgressSnapshot()` (`raft.go:1946-1949`). But that is
            // a *complete* snapshot pending application (`log.go:287-291`:
            // `unstable.snapshot != nil`, set only by `restore`); etcd has no
            // partial transfers to gate on, and this member installs a completed
            // one synchronously, so etcd's state never exists here. What the
            // gate actually tested was a *partial* buffer -- and every chunk and
            // every keepalive from a live leader resets the timer, so by the
            // time the gate was reached the transfer had stopped. It could only
            // ever block a campaign the silence called for: the chaos soak
            // measured clusters with no leader because the one voter able to
            // lead held an abandoned buffer and never campaigned. A far-behind
            // member's pre-vote simply fails, which changes nothing.
            //
            // Pre-Vote first, always -- and for a member that has forgotten
            // too. Winning the real election is the *only* thing a term
            // increment buys, so asking first costs one round trip and saves
            // every disruption a doomed campaign would cause. A pre-vote
            // changes nothing a peer can observe, so it is also how a member
            // that has forgotten learns whether enough others have for a
            // recovery election to be winnable; a separate "probe" once did
            // that, feeding evidence that outlived its round.
            match self.pre_campaign(&mut state, now) {
                Ok(wake_apply) => wake_apply,
                Err(error) => {
                    // The campaign stopped at its save and sent nothing; the
                    // next election timeout tries again. The Python's
                    // `_tick_forever` logs the same failure the same way.
                    tracing::error!(error = %error.0, "raft: tick failed");
                    false
                }
            }
        };
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
        state.pre_refusals.clear();
        state.forgotten.clear();
        state.pre_forgotten.clear();
        state.reset_election_timer(&self.timing, now);
        // Nothing is released, as etcd releases nothing when a leader steps
        // down: a proposal still queued is routed by this member's role when it
        // drains, and one already appended waits for its entry -- which a later
        // leader may yet commit -- or for its caller to stop waiting (`tick`).
        // Every waiter used to be failed here, telling callers "unavailable"
        // about entries that went on to commit; the Python implementation never
        // did, and the two now agree.
        //
        // Reads are the exception, as they are in etcd, whose server fails a
        // read with `ErrLeaderChanged` when the leader changes
        // (`read/read.go:170-193`): a read is confirmed by a quorum that this
        // member *still* leads, and it no longer does.
        state.fail_reads(&format!("no longer the leader: {reason}"));
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
    ///
    /// # Errors
    ///
    /// [`Self::campaign`]'s, when the pre-vote is won outright.
    fn pre_campaign(
        &self,
        state: &mut NodeState,
        now: Instant,
    ) -> Result<bool, PersistentStateError> {
        state.role = Role::PreCandidate;
        state.leader = None;
        state.pre_votes = BTreeSet::from([self.layout.local.index]);
        state.pre_refusals.clear();
        state.pre_forgotten.clear();
        state.reset_election_timer(&self.timing, now);

        if state.won(
            &self.layout,
            &state.pre_votes.clone(),
            &state.pre_forgotten.clone(),
        ) {
            return self.campaign(state, now);
        }

        let request = Message::RequestVote(RequestVote {
            term: state.term.saturating_add(1),
            candidate: self.layout.local.index,
            last_log_index: state.log.last_index(),
            last_log_term: state.log.last_term(),
            pre_vote: true,
        });
        let peers: Vec<u64> = state.peers.keys().copied().collect();
        for peer in peers {
            self.transport
                .send(peer, &request, crate::wire::Stream::Control);
        }
        Ok(false)
    }

    /// Start an election. Only ever called with a reachable quorum.
    ///
    /// That guard matters more than it looks. A member cut off from the cluster
    /// cannot win an election, but without the check it would keep campaigning
    /// anyway, incrementing its term on every timeout. When the partition healed
    /// it would arrive carrying a term far above everyone else's, force the
    /// healthy leader to step down, and cause an election the cluster had no
    /// reason to hold -- the "disruptive server" problem.
    ///
    /// # Errors
    ///
    /// [`NodeState::persist`]'s. A vote for itself that is not on disk ends
    /// the campaign there, before it is won or a request is sent, where the
    /// Python's `_campaign` raises.
    fn campaign(&self, state: &mut NodeState, now: Instant) -> Result<bool, PersistentStateError> {
        state.role = Role::Candidate;
        state.term = state.term.saturating_add(1);
        // As in `step_down`: a new term ends every transfer of the old one.
        state.installing.clear();
        state.voted_for = Some(self.layout.local.index);
        state.persist()?;
        state.votes = BTreeSet::from([self.layout.local.index]);
        state.forgotten.clear();
        state.leader = None;
        state.reset_election_timer(&self.timing, now);

        tracing::debug!(
            member = self.layout.local.name,
            term = state.term,
            "raft: campaigning",
        );

        if state.won(&self.layout, &state.votes.clone(), &state.forgotten.clone()) {
            return Ok(self.become_leader(state, now));
        }

        let request = Message::RequestVote(RequestVote {
            term: state.term,
            candidate: self.layout.local.index,
            last_log_index: state.log.last_index(),
            last_log_term: state.log.last_term(),
            pre_vote: false,
        });
        let peers: Vec<u64> = state.peers.keys().copied().collect();
        for peer in peers {
            self.transport
                .send(peer, &request, crate::wire::Stream::Control);
        }
        Ok(false)
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
            match state.log.window(
                next_index,
                self.timing.max_entries_per_append,
                self.timing.max_append_bytes,
            ) {
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
    /// evidence the entry is safe. They are **not** excluded from the
    /// majority, which is of the whole voting configuration: see
    /// [`leader_commit_index`] for the defect that distinction was.
    ///
    /// An advance replicates **at once**. A follower learns the commit index
    /// only from `leaderCommit` on an `AppendEntries`, so leaving a new index to
    /// ride the next heartbeat puts a whole heartbeat interval on the critical
    /// path of every mutation that did not arrive at the leader -- measured at
    /// 45.6 ms p50 against a 50 ms heartbeat, where the leader had committed in
    /// about 1 ms. It cannot loop: the extra round carries no entries, so no
    /// follower's match index moves and this returns without sending again.
    fn advance_commit(&self, state: &mut NodeState) -> bool {
        let acknowledged: Vec<u64> = state
            .peers
            .values()
            .filter(|peer| !peer.catching_up)
            .map(|peer| peer.match_index)
            .collect();
        let candidate = leader_commit_index(
            state.quorum(&self.layout),
            state.log.last_index(),
            acknowledged,
            state.commit_index,
            state.term,
            |index| state.log.term_at(index).ok(),
        );
        if candidate <= state.commit_index {
            return false;
        }
        state.commit_index = candidate;
        self.arm_reads(state);
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
                //
                // Logging it and ending this loop was no better: nothing else
                // stopped, so the member went on leading, voting and serving a
                // store that no longer moved. It stops instead (`fail`).
                self.fail(&RaftInvariantViolated(error));
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
                state.machine.apply(&self.registry, &entries)
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
                // As few messages as `max_append_bytes` allows, in order on
                // one link, which keeps their order: a tick's batch is bounded
                // by count (`MAX_BATCH`), and only here, where the operations
                // are encoded, is their size known.
                for group in split_by_bytes(payloads, self.timing.max_append_bytes) {
                    let message = Message::Propose(crate::messages::Propose {
                        proposals: group,
                        request_id: 0,
                    });
                    self.transport
                        .send(leader, &message, crate::wire::Stream::Control);
                }
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
            peer.heard_request = 0;
            // Likewise a snapshot this member was sending in an earlier term:
            // the new transfer starts from zero, and a carried-over offset
            // would have the leader resume a stream the peer is not expecting.
            peer.snapshot_offset = 0;
            peer.snapshot_request = 0;
            peer.sending = None;
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
    /// Say "I am still the leader" to a peer that can be told nothing else.
    ///
    /// `go.etcd.io/raft` carries heartbeats as their own message type, and
    /// `bcastHeartbeat` reaches every peer whatever its replication state -- so
    /// a follower waiting for a snapshot still hears from its leader.
    ///
    /// Here a heartbeat *is* an `AppendEntries`, so it arrives through
    /// `send_append`, which diverts to `send_snapshot` for any peer below the
    /// compaction boundary. Returning silently from there sent that peer
    /// **nothing at all**: no entries, and no liveness either, because they are
    /// the same message. Its election timer expires, it campaigns, it loses
    /// against the leader's lease, and it keeps doing that -- while answering
    /// every mutation with "no leader elected", because campaigning clears its
    /// `leader`.
    ///
    /// Anchored at index 0, which every log matches -- even an empty one -- so
    /// the consistency check cannot fail. No entries and a `leader_commit` of
    /// zero mean that by the receiver's own rule it vouches for nothing and can
    /// move no commit index. The reply reports `match_index` 0, which the
    /// leader ignores, because a success is taken only when it moves a peer
    /// *forward*. So it cannot be mistaken for progress, which is the one thing
    /// a keepalive must never be.
    fn send_keepalive(&self, state: &mut NodeState, peer: u64) {
        state.append_sequence = state.append_sequence.saturating_add(1);
        let request_id = state.append_sequence;
        if let Some(tracked) = state.peers.get_mut(&peer) {
            tracked.sent_commit = 0;
        }
        self.transport.send(
            peer,
            &Message::AppendEntries(AppendEntries {
                term: state.term,
                leader: self.layout.local.index,
                prev_log_index: 0,
                prev_log_term: 0,
                leader_commit: 0,
                request_id,
                entries: Vec::new(),
            }),
            crate::wire::Stream::Control,
        );
    }

    fn send_snapshot(&self, state: &mut NodeState, peer: u64) {
        let Some(meta) = state.snapshot_meta else {
            // Nothing to send yet. The peer stays behind until the next
            // compaction produces one, which is correct: there is no state to
            // hand it that it does not already have -- but it must still hear
            // that this leader is alive.
            self.send_keepalive(state, peer);
            return;
        };
        if state.snapshot.is_empty() {
            self.send_keepalive(state, peer);
            return;
        }
        let carrier = self.transport.connection(peer, crate::wire::Stream::Bulk);
        let Some(tracked) = state.peers.get_mut(&peer) else {
            return;
        };
        if tracked.snapshot_request != 0 {
            if carrier.is_some() && carrier == tracked.snapshot_carrier {
                // One chunk at a time (`PeerState::snapshot_request`), and this
                // one is still on its way: the connection it went out on is in
                // place, so it will be answered or that connection will end
                // (`PeerState::snapshot_carrier`). The chunk is on BULK, which a
                // slow transfer can occupy for a long time, so the liveness
                // signal goes separately on CONTROL.
                self.send_keepalive(state, peer);
                return;
            }
            // Its connection has ended, taking the chunk or its answer with it.
            // Sent again below under a new id, so should the first answer
            // arrive after all it is not the one awaited.
            tracked.snapshot_request = 0;
        }
        if carrier.is_none() {
            // No BULK connection to carry a chunk until one is back.
            self.send_keepalive(state, peer);
            return;
        }

        let current = Arc::clone(&state.snapshot);
        let Some(tracked) = state.peers.get_mut(&peer) else {
            return;
        };
        if tracked.snapshot_offset == 0 || tracked.sending.is_none() {
            // A transfer starts -- or restarts, the follower having thrown its
            // buffer away -- so it is of the snapshot as it stands now, pinned
            // for its whole length. See `PeerState::sending`.
            tracked.sending = Some((meta, current));
            tracked.snapshot_offset = 0;
        }
        let Some((sent_meta, ref payload)) = tracked.sending else {
            return;
        };
        let offset = tracked.snapshot_offset;
        let end = offset
            .saturating_add(self.timing.snapshot_chunk)
            .min(payload.len());
        let chunk = payload.get(offset..end).unwrap_or_default().to_vec();
        let done = end >= payload.len();
        // From the append sequence, so the floor a reset raises fences chunks
        // and appends alike.
        state.append_sequence = state.append_sequence.saturating_add(1);
        let request_id = state.append_sequence;
        if let Some(tracked) = state.peers.get_mut(&peer) {
            tracked.snapshot_request = request_id;
            tracked.snapshot_carrier = carrier;
        }

        let message = Message::InstallSnapshot(InstallSnapshot {
            term: state.term,
            leader: self.layout.local.index,
            last_index: sent_meta.last_index,
            last_term: sent_meta.last_term,
            offset: offset as u64,
            data: chunk,
            done,
            ownership: Vec::new(),
            request_id,
        });
        // BULK, so a multi-megabyte transfer cannot head-of-line-block the
        // heartbeats that keep this member's leadership alive.
        self.transport
            .send(peer, &message, crate::wire::Stream::Bulk);
    }

    /// Is this snapshot's metadata possible at all? `None` if it is.
    ///
    /// Two checks, both about metadata rather than content. A snapshot's own
    /// header must describe the transfer that carried it, and it cannot
    /// describe a term above the one its sender holds: the sender built it from
    /// entries it had committed, and it cannot have committed an entry from a
    /// term it has not reached. A snapshot this member already holds -- one at
    /// or below its commit index -- is not impossible, only unneeded, and is
    /// answered before any of this (`on_install_snapshot`).
    ///
    /// Installing one anyway is worse than it sounds: the log would take the
    /// snapshot's term as its own, and a member whose last log term is above its
    /// current term considers itself impossibly up to date. It would refuse
    /// every vote and win any election it entered.
    fn why_the_snapshot_cannot_be_real(
        meta: &SnapshotMeta,
        sender_term: u64,
        sent_as: (u64, u64),
    ) -> Option<String> {
        if (meta.last_index, meta.last_term) != sent_as {
            // The payload's own header and the transfer that carried it
            // describe different snapshots. The leader credits what it sent
            // *as*; installing what the bytes say would leave the two members
            // disagreeing about what this one holds -- a phantom match on one
            // side, a hole on the other.
            return Some(format!(
                "its contents describe a snapshot through ({}, t{}) but it was sent as one \
                 through ({}, t{})",
                meta.last_index, meta.last_term, sent_as.0, sent_as.1,
            ));
        }
        if meta.last_term > sender_term {
            return Some(format!(
                "it covers term {} but arrived from a member at term {sender_term}",
                meta.last_term,
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

        // Installing a snapshot abandons the open capture
        // (`StateMachine::install_snapshot`), and an install can land whenever
        // the state lock is free -- at any yield below, and on the threaded
        // runtime between any two lock scopes. So every step that resumes after
        // one checks that the capture is still open, and a capture that is gone
        // means this snapshot was superseded: the installed one is newer than
        // anything this walk could produce, and already held.
        //
        // "A capture is open" suffices for "this capture is open": compaction
        // runs only here, and only from the apply task, which awaits it -- so
        // once this capture is abandoned no other can open until this returns.
        let superseded = || {
            tracing::debug!(
                member = self.layout.local.name,
                applied,
                "raft: stopped a snapshot: a later one was installed",
            );
        };

        // The walk is chunked so a large registry does not hold the store's
        // read lock for its whole length; copy-on-write is what makes a
        // non-atomic walk correct.
        let keys = self.registry.with_read_store(crate::snapshot::walk_order);
        let mut records = Vec::new();
        let mut live = std::collections::BTreeSet::new();
        for window in keys.chunks(crate::snapshot::CHUNK_RESOURCES) {
            {
                let state = self.state.lock();
                let Some(capture) = state.machine.snapshots().capture() else {
                    superseded();
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

        // Finishing and storing under one lock, so no install can land between
        // them: stored after one, this snapshot would replace a newer one with
        // an older.
        let mut state = self.state.lock();
        if state.machine.snapshots().capture().is_none() {
            superseded();
            return;
        }
        let payload = match state.machine.snapshots_mut().finish(records, &live) {
            Ok(payload) => payload,
            Err(error) => {
                tracing::error!(error = %error.0, "raft: taking a snapshot failed");
                state.machine.snapshots_mut().abandon();
                return;
            }
        };
        state.snapshot = Arc::from(payload);
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

    // -- reading ------------------------------------------------------------

    /// The index a read here must have applied before it may answer.
    ///
    /// etcd's ReadIndex in its default, quorum-confirmed mode (`ReadOnlySafe`,
    /// `raft.go:58-70`): the commit index as it stood when the read began,
    /// released only once a quorum has confirmed, after that moment, that the
    /// leader giving it still leads. A member that has applied through it holds
    /// every write acknowledged before the read began, so an answer from its
    /// own store -- a 400, a 404 -- is one the leader would have given.
    ///
    /// On the leader the read is served here; a follower asks its leader
    /// (`on_read_index`).
    ///
    /// # Errors
    ///
    /// [`RaftUnavailable`] when there is no leader, when leadership is lost
    /// before a quorum confirms, or at `timeout_ms`.
    pub async fn read_index(&self, timeout_ms: u64) -> Result<u64, RaftUnavailable> {
        let pending = {
            let mut state = self.state.lock();
            if state.role == Role::Leader {
                Ok(self.begin_read(&mut state))
            } else {
                Err(state.leader)
            }
        };
        let receiver = match pending {
            Ok(receiver) => receiver,
            Err(leader) => return self.ask_leader_for_read_index(leader, timeout_ms).await,
        };
        match tokio::time::timeout(Duration::from_millis(timeout_ms), receiver).await {
            Ok(Ok(answer)) => answer,
            Ok(Err(_)) => Err(RaftUnavailable("member is shutting down".to_owned())),
            Err(_) => Err(RaftUnavailable(
                "no quorum confirmed this member's leadership in time".to_owned(),
            )),
        }
    }

    async fn ask_leader_for_read_index(
        &self,
        leader: Option<u64>,
        timeout_ms: u64,
    ) -> Result<u64, RaftUnavailable> {
        let Some(leader) = leader else {
            // etcd drops the request with no leader (`raft.go:1764-1768`); a
            // caller here is told at once rather than left to its deadline.
            return Err(RaftUnavailable("no leader elected".to_owned()));
        };
        // A silent leader or a failed link is `RaftUnavailable` from the
        // transport, which resolves a request only with the kind it expects
        // (`Message::expected_reply`).
        let reply = self
            .transport
            .request(
                leader,
                &Message::ReadIndex(ReadIndex { request_id: 0 }),
                crate::wire::Stream::Control,
                Some(timeout_ms),
            )
            .await?;
        match reply {
            Message::ReadIndexReply(reply) if reply.ok => Ok(reply.index),
            Message::ReadIndexReply(reply) => Err(RaftUnavailable(format!(
                "member {leader} gave no read index: {}",
                reply.reason,
            ))),
            other => Err(RaftUnavailable(format!(
                "member {leader} answered a read index with {:?}",
                other.message_type(),
            ))),
        }
    }

    /// Record a read on this leader; the receiver is its confirmed index.
    fn begin_read(&self, state: &mut NodeState) -> oneshot::Receiver<Result<u64, RaftUnavailable>> {
        let (reply, receiver) = oneshot::channel();
        if self.layout.size() == 1 {
            // A lone voter is answered at once, at its commit index, as etcd
            // answers one (`raft.go:1355-1361`): there is nobody to confirm
            // anything with, and it has acknowledged nothing it has not
            // committed.
            drop(reply.send(Ok(state.commit_index)));
            return receiver;
        }
        state.reads.push(Read {
            reply,
            index: None,
            after: 0,
        });
        self.arm_reads(state);
        receiver
    }

    /// Give every postponed read its index, and ask a quorum to confirm it.
    ///
    /// Armed at the commit index and the append sequence of this moment, then
    /// a heartbeat to every peer at once -- etcd broadcasts one per read
    /// (`sendMsgReadIndexResponse`, `raft.go:2146-2156`) -- whose replies,
    /// carrying later ids, are the confirmation `confirm_reads` counts.
    fn arm_reads(&self, state: &mut NodeState) {
        if state.role != Role::Leader || !state.committed_in_current_term() {
            return;
        }
        let (index, after) = (state.commit_index, state.append_sequence);
        let mut armed = false;
        for read in state.reads.iter_mut().filter(|read| read.index.is_none()) {
            read.index = Some(index);
            read.after = after;
            armed = true;
        }
        if armed {
            let peers: Vec<u64> = state
                .peers
                .iter()
                .filter(|&(_, peer)| peer.up)
                .map(|(&peer, _)| peer)
                .collect();
            for peer in peers {
                self.send_append(state, peer);
            }
            self.confirm_reads(state);
        }
    }

    /// Release every read a quorum has confirmed.
    ///
    /// A read is confirmed when this member and enough voters to make a quorum
    /// have answered, in this term, an append sent after it was armed -- etcd's
    /// `maybeAdvance` over the voters' echoed positions (`raft.go:1600-1609`).
    /// Members catching up are not voters here, as they are not for commitment
    /// or check-quorum. A read whose caller has gone is simply dropped.
    fn confirm_reads(&self, state: &mut NodeState) {
        if state.reads.is_empty() {
            return;
        }
        let quorum = state.quorum(&self.layout);
        for read in std::mem::take(&mut state.reads) {
            if read.reply.is_closed() {
                continue;
            }
            let Some(index) = read.index else {
                state.reads.push(read);
                continue;
            };
            let confirmed = state
                .peers
                .values()
                .filter(|peer| !peer.catching_up && peer.heard_request > read.after)
                .count()
                .saturating_add(1);
            if confirmed >= quorum {
                drop(read.reply.send(Ok(index)));
            } else {
                state.reads.push(read);
            }
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
    fn on_request_vote(
        &self,
        _peer: u64,
        message: &RequestVote,
    ) -> Result<RequestVoteReply, PersistentStateError> {
        let now = Instant::now();
        let mut state = self.state.lock();

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
            return Ok(RequestVoteReply {
                term: state.term,
                granted: false,
                voting: state.voting,
                pre_vote: message.pre_vote,
            });
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
            let granted = message.term > state.term
                && state
                    .log
                    .is_at_least_as_current_as(message.last_log_index, message.last_log_term);
            return Ok(RequestVoteReply {
                // The prospective term when granting, so the candidate can
                // count it against the term it proposed; this member's own when
                // refusing, so a candidate standing on a stale term learns to
                // step down.
                term: if granted { message.term } else { state.term },
                granted,
                voting: state.voting,
                pre_vote: true,
            });
        }

        if message.term > state.term {
            state.step_down(message.term)?;
        }

        // A member that has forgotten grants on the same terms as any other --
        // log currency, one vote per term -- and says what it is in the reply.
        // Whether that grant counts is the candidate's to decide, from this
        // round's replies alone: see "When every voter has forgotten".
        let mut granted = false;
        if message.term == state.term {
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
                // against, and the window is exactly here. A save that fails
                // ends this without an answer; the vote stays recorded in
                // memory, where it still refuses anyone else in this term.
                state.persist()?;
                state.reset_election_timer(&self.timing, now);
            }
        }

        Ok(RequestVoteReply {
            term: state.term,
            granted,
            voting: state.voting,
            pre_vote: false,
        })
    }

    fn on_request_vote_reply(
        &self,
        peer: u64,
        message: &RequestVoteReply,
    ) -> Result<(), PersistentStateError> {
        let now = Instant::now();
        let mut wake_apply = false;
        {
            let mut state = self.state.lock();

            if message.pre_vote {
                // A *refused* pre-vote carries the voter's own term. If that is
                // above ours we are stale and step down -- the one state change
                // a pre-vote round may cause, and it is a correction rather
                // than a disruption. A *granted* one carries the prospective
                // term, ours plus one, and must never be mistaken for evidence
                // that we are behind.
                if !message.granted && message.term > state.term {
                    state.step_down(message.term)?;
                    return Ok(());
                }
                if state.role != Role::PreCandidate {
                    return Ok(());
                }
                if message.granted && message.term == state.term.saturating_add(1) {
                    state.pre_votes.insert(peer);
                    if !message.voting {
                        // From a grant only: see "When every voter has
                        // forgotten" for why a pre-vote must never over-predict
                        // a recovery.
                        state.pre_forgotten.insert(peer);
                    }
                    let (tally, forgotten) = (state.pre_votes.clone(), state.pre_forgotten.clone());
                    if state.won(&self.layout, &tally, &forgotten) {
                        wake_apply = self.campaign(&mut state, now)?;
                    }
                } else if !message.granted {
                    // **A lost round ends the candidacy**, which is
                    // `go.etcd.io/raft`'s `case quorum.VoteLost:
                    // r.becomeFollower(r.Term, None)` (`raft.go:1707`) and was
                    // missing here.
                    //
                    // Losing is the *ordinary* outcome when the cluster is
                    // healthy: the other members are inside their leader's
                    // lease and refuse on those grounds. Without this the
                    // member stays a pre-candidate and re-campaigns at every
                    // timeout for as long as the leader lives.
                    //
                    // That is not merely untidy. A pre-candidate has cleared
                    // its `leader` -- deliberately, to release the lease it
                    // held -- so while it stays one it answers every mutation
                    // with "no leader elected", and a mutation *forwarded* to
                    // it because it owns the Node fails with "the member
                    // owning node did not answer".
                    state.pre_refusals.insert(peer);
                    if state.pre_vote_is_lost(&self.layout) {
                        tracing::debug!(
                            member = self.layout.local.name,
                            "raft: lost its pre-vote round and returns to following",
                        );
                        state.role = Role::Follower;
                        state.pre_votes.clear();
                        state.pre_refusals.clear();
                        // A fresh window, as etcd's `becomeFollower` -> `reset`
                        // gives. Without it the deadline that has already
                        // expired is still expired, and the next tick
                        // campaigns again immediately.
                        state.reset_election_timer(&self.timing, now);
                    }
                }
            } else {
                if message.term > state.term {
                    state.step_down(message.term)?;
                    return Ok(());
                }
                if state.role != Role::Candidate || message.term != state.term {
                    // Including a reply that arrives after its round is over:
                    // it is evidence about that round and no other. Keeping
                    // such a reply was how a member promoted since came to be
                    // counted as forgotten.
                    return Ok(());
                }
                if !message.voting {
                    // Binding -- this term's answer from the member itself.
                    state.forgotten.insert(peer);
                }
                if message.granted {
                    state.votes.insert(peer);
                }
                let (tally, forgotten) = (state.votes.clone(), state.forgotten.clone());
                if state.won(&self.layout, &tally, &forgotten) {
                    wake_apply = self.become_leader(&mut state, now);
                }
            }
        }
        if wake_apply {
            self.apply_wake.notify_one();
        }
        if self.role() == Role::Leader {
            self.replicate();
        }
        Ok(())
    }

    fn on_append_entries(
        &self,
        _peer: u64,
        message: &AppendEntries,
    ) -> Result<AppendEntriesReply, PersistentStateError> {
        let now = Instant::now();
        let mut wake_apply = false;
        let reply = {
            let mut state = self.state.lock();

            if message.term < state.term {
                return Ok(AppendEntriesReply {
                    term: state.term,
                    success: false,
                    match_index: 0,
                    conflict_index: 0,
                    conflict_term: 0,
                    catching_up: !state.voting,
                    request_id: 0,
                });
            }

            if message.term > state.term {
                state.step_down(message.term)?;
            }
            state.role = Role::Follower;
            state.leader = Some(message.leader);
            state.reset_election_timer(&self.timing, now);
            state.heard_from_leader_at = Some(now);

            if let Some(contradiction) = state.contradicting_committed(message) {
                // A leader that contradicts what this member has committed
                // proves committed data lost from the cluster
                // (`contradicting_committed`). Nothing brings this member back
                // into step -- it refuses every append anchored at its commit
                // point, and would serve its stale store until the leader's
                // snapshot passed that point -- so it stops, as for any broken
                // invariant (`fail`, which takes this lock, hence the release
                // first), answering nothing a leader could count: request id 0
                // is under every reply floor.
                let refusal = AppendEntriesReply {
                    term: state.term,
                    success: false,
                    match_index: 0,
                    conflict_index: 0,
                    conflict_term: 0,
                    catching_up: !state.voting,
                    request_id: 0,
                };
                drop(state);
                self.fail(&RaftInvariantViolated(contradiction));
                return Ok(refusal);
            }

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
                return Ok(AppendEntriesReply {
                    term: state.term,
                    success: true,
                    match_index: state.commit_index,
                    conflict_index: 0,
                    conflict_term: 0,
                    catching_up: !state.voting,
                    request_id: message.request_id,
                });
            }

            if !state
                .log
                .matches(message.prev_log_index, message.prev_log_term)
            {
                let (conflict_index, conflict_term) = state
                    .log
                    .find_conflict(message.prev_log_index, message.prev_log_term);
                return Ok(AppendEntriesReply {
                    term: state.term,
                    success: false,
                    match_index: 0,
                    conflict_index,
                    conflict_term,
                    catching_up: !state.voting,
                    request_id: message.request_id,
                });
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
                            return Ok(AppendEntriesReply {
                                term: state.term,
                                success: false,
                                match_index: 0,
                                conflict_index: 0,
                                conflict_term: 0,
                                catching_up: !state.voting,
                                request_id: message.request_id,
                            });
                        }
                    }
                }
                let committed = state.commit_index;
                match state.log.append_replicated(decoded, committed) {
                    Ok(()) => {}
                    Err(AppendError::Refused(why)) => {
                        tracing::warn!(error = %why, "raft: replicated append refused");
                        return Ok(AppendEntriesReply {
                            term: state.term,
                            success: false,
                            match_index: 0,
                            conflict_index: 0,
                            conflict_term: 0,
                            catching_up: !state.voting,
                            request_id: message.request_id,
                        });
                    }
                    Err(AppendError::Invariant(violated)) => {
                        // An entry here conflicting at or below the commit
                        // index. No message can reach that --
                        // `prev_log_index < commit_index` was answered above,
                        // so every entry here lies beyond the commit index --
                        // which is what makes it a broken invariant rather
                        // than a refusal. The member stops (`fail`, which takes
                        // this lock, hence the release first), answering
                        // nothing a leader could count: request id 0 is under
                        // every reply floor.
                        let refusal = AppendEntriesReply {
                            term: state.term,
                            success: false,
                            match_index: 0,
                            conflict_index: 0,
                            conflict_term: 0,
                            catching_up: !state.voting,
                            request_id: 0,
                        };
                        drop(state);
                        self.fail(&violated);
                        return Ok(refusal);
                    }
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
        Ok(reply)
    }

    fn on_append_entries_reply(
        &self,
        peer: u64,
        message: &AppendEntriesReply,
    ) -> Result<(), PersistentStateError> {
        let mut wake_apply = false;
        let mut resend = false;
        // "This reply told us nothing to act on", the Python's early return.
        let mut settled = false;
        let mut promote: Option<Promote> = None;
        {
            let mut state = self.state.lock();
            if message.term > state.term {
                state.step_down(message.term)?;
                return Ok(());
            }
            if state.role != Role::Leader || message.term != state.term {
                return Ok(());
            }
            let Some(tracked) = state.peers.get_mut(&peer) else {
                return Ok(());
            };

            if message.request_id <= tracked.reply_floor {
                // Drawn by a send this leader has since disowned -- see
                // `PeerState::reply_floor`. Believing it credits the member
                // that has just replaced this one with a log it does not have,
                // and takes `catching_up` from a member that no longer exists.
                return Ok(());
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
                // Newly noticed: it must hold everything committed as of now
                // before its vote counts again -- and "everything committed" is
                // bounded by this leader's *last* index, not by its commit index.
                //
                // The two differ exactly after an election. A new leader holds
                // every committed entry (Leader Completeness), but it learns that
                // an entry is committed only once one of its own term commits
                // above it; until then an entry an earlier leader committed
                // looks, from here, like one nobody committed. The chaos soak's
                // promotion audit measured the consequence of barring at the
                // commit index: a leader committed an entry with one follower and
                // restarted before telling it; that follower won the next term,
                // still believing the commit index below the entry, and promoted
                // the restarted member at that bar without it; the promoted
                // member then voted in a candidate that had never had the entry,
                // which wrote over it. The Python module docstring's own
                // scenario, through the promotion rather than the vote.
                //
                // Entries committed after the member restarted do not need
                // waiting for -- a member catching up is never counted, so they
                // were committed on a majority without it -- but nothing here can
                // tell them from the others, and they are in this log, so the
                // whole log is the bar. etcd's server judges a learner ready
                // against the same point, the leader's own `Match`
                // (`server/etcdserver/server.go`, `isLearnerReady`).
                let bar = state.log.last_index();
                if let Some(tracked) = state.peers.get_mut(&peer) {
                    tracked.promote_through = bar;
                }
                tracing::info!(peer, through = bar, "raft: member is catching up");
            }

            // Whatever it said, the peer answered in this term -- a read's
            // confirmation, but only from a member that votes. etcd counts
            // voters' acknowledgements alone (`maybeAdvance(r.trk.Voters)`,
            // `raft.go:1604-1605`; `CommittedIndex` over the voters' acks,
            // `read_only.go:79-81`), as this leader counts only voters toward a
            // commit and toward check-quorum. Judged by the flag this reply
            // carries, not the one before it: the first reply of a member that
            // restarted with nothing is the one that says so.
            if !message.catching_up
                && let Some(tracked) = state.peers.get_mut(&peer)
            {
                tracked.heard_request = tracked.heard_request.max(message.request_id);
            }
            self.confirm_reads(&mut state);

            let matched = state.peers.get(&peer).map_or(0, |t| t.match_index);
            let own_last = state.log.last_index();
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
                return Ok(());
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
                let resume = message.conflict_index.max(1);
                if state
                    .peers
                    .get(&peer)
                    .is_some_and(|tracked| resume >= tracked.next_index)
                {
                    // Nothing learned: the peer asks to resume where this
                    // leader already is, or past it. Sent again at once, the
                    // same append draws the same refusal -- and it was, forever,
                    // at zero delay: 124,991 appends inside one millisecond of
                    // cluster time (seed 111504), from a follower whose
                    // committed snapshot contradicts this log at its boundary,
                    // which only lost committed data (amnesia past the budget)
                    // can produce. etcd re-sends only when a rejection lowers
                    // `Next` (`MaybeDecrTo`, `tracker/progress.go:226-254`);
                    // here the next heartbeat is the next probe.
                    settled = true;
                } else {
                    if let Some(tracked) = state.peers.get_mut(&peer) {
                        tracked.next_index = resume;
                        // The window just moved backwards, so anything recorded
                        // as told to this peer above its new end was told
                        // through a message it rejected. `go.etcd.io/raft`
                        // clamps the same way whenever `Next` regresses
                        // (`tracker/progress.go:142`, `:238`, `:251`),
                        // commenting that the sent commit "unlikely has been
                        // applied".
                        tracked.sent_commit = crate::commit::commit_after_regression(
                            tracked.sent_commit,
                            tracked.next_index,
                        );
                    }
                    resend = true;
                }
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
                //
                // And never past this leader's own log: a follower cannot hold
                // more of it than it holds, as the snapshot-reply path already
                // says (`on_install_snapshot_reply`). In a correct run the two
                // agree -- a follower vouches for a window this leader sent, or
                // for its commit index, which Leader Completeness puts inside
                // this log. Where they do not, committed data has been lost,
                // and the unbounded credit put `next_index` past the end of the
                // log: `send_append` then sent a snapshot the follower ignored,
                // and never an anchor it could check against what it committed
                // (`contradicting_committed`).
                let vouched = message.match_index.min(own_last);
                let advanced = vouched > tracked.match_index;
                if advanced {
                    tracked.match_index = vouched;
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
        Ok(())
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

    fn on_install_snapshot(
        &self,
        peer: u64,
        message: &InstallSnapshot,
    ) -> Result<InstallSnapshotReply, PersistentStateError> {
        let now = Instant::now();
        let mut state = self.state.lock();
        // Every answer carries this member's commit index and names the chunk
        // it answers (`InstallSnapshotReply`): the first is how the leader
        // learns to stop sending a snapshot this member already holds, the
        // second how it tells the answer to the chunk in flight from one that
        // outlived its transfer.
        let answer = |state: &NodeState, received: usize, done: bool| InstallSnapshotReply {
            term: state.term,
            bytes_received: received as u64,
            done,
            commit_index: state.commit_index,
            request_id: message.request_id,
        };

        if message.term < state.term {
            return Ok(answer(&state, 0, false));
        }
        if message.term > state.term {
            state.step_down(message.term)?;
        }
        state.role = Role::Follower;
        state.leader = Some(message.leader);
        state.reset_election_timer(&self.timing, now);

        let held = (state.log.snapshot_index()..=state.commit_index)
            .contains(&message.last_index)
            .then(|| state.log.term_at(message.last_index).ok())
            .flatten();
        if let Some(held) = held.filter(|&held| held != message.last_term) {
            // Not held at all: the leader's snapshot ends on an entry committed
            // here at another term -- committed data lost from the cluster, as
            // in `contradicting_committed`. The member stops (`fail`, which
            // takes this lock, hence the release first) and credits nothing:
            // request id 0 is under every reply floor.
            state.installing.remove(&peer);
            let refusal = InstallSnapshotReply {
                term: state.term,
                bytes_received: 0,
                done: false,
                commit_index: 0,
                request_id: 0,
            };
            drop(state);
            self.fail(&RaftInvariantViolated(format!(
                "leader {} of term {} sent a snapshot through index {} at term {}, committed \
                 here at term {held}: committed data was lost from the cluster",
                message.leader, message.term, message.last_index, message.last_term,
            )));
            return Ok(refusal);
        }

        if message.last_index <= state.commit_index {
            // Already held: everything this snapshot covers is committed here,
            // and installing it would replace the state machine with older
            // state while `commit_index` correctly stays put -- committed
            // entries un-applied, the one thing a state machine may never do.
            // Against the **commit index**, not the compaction boundary:
            // `snapshot_index <= commit_index` always, so the boundary let
            // through every snapshot landing in between. `go.etcd.io/raft`
            // ignores it on exactly this line (`raft.go:1861`) and answers with
            // its commit index (`raft.go:1850-1853`), logged at Info: it is a
            // race between a leader's decision and this member's progress, not
            // a fault. Decided from the metadata at the first chunk, rather
            // than after a whole transfer has been assembled to be thrown away.
            state.installing.remove(&peer);
            tracing::info!(
                member = self.layout.local.name,
                peer,
                through = message.last_index,
                committed = state.commit_index,
                "raft: ignored a snapshot: committed through it already",
            );
            return Ok(answer(&state, 0, false));
        }

        let offset = usize::try_from(message.offset).unwrap_or(usize::MAX);
        let identity = (
            message.term,
            message.leader,
            message.last_index,
            message.last_term,
        );
        if offset == 0 {
            // Only a first chunk may begin a transfer, and it always may: the
            // sender has started over, whatever it was sending before.
            state.installing.insert(
                peer,
                Assembly {
                    identity,
                    data: Vec::new(),
                },
            );
        } else if let Some(assembly) = state.installing.get(&peer)
            && assembly.identity == identity
            && holds(&assembly.data, offset, &message.data)
        {
            // A copy of a chunk this member already holds. The leader sends a
            // chunk again once the connection it went out on has ended, which
            // can take the answer and leave the chunk delivered; and a CONTROL
            // reconnect starts a transfer again while BULK may still carry the
            // old one's chunk. Answered with what is assembled, which is where
            // the leader resumes; the copy is the chunk in flight, so its answer
            // is the one the leader acts on. Treated as a mismatch, as it once
            // was, it threw the transfer away and the leader began again from
            // nothing: 343 of 362 follower resets in chaos-soak seed 140692,
            // whose members needing a snapshot never finished one.
            let assembled = assembly.data.len();
            return Ok(answer(&state, assembled, false));
        }
        let continues = state
            .installing
            .get(&peer)
            .is_some_and(|assembly| assembly.identity == identity && assembly.data.len() == offset);
        if !continues {
            // A chunk out of order, one disagreeing with what is held at its
            // offset, or -- the case an offset alone cannot see -- the next
            // chunk of a *different* snapshot. Restart rather than splice: a
            // snapshot assembled from mismatched pieces can parse and be wrong.
            state.installing.remove(&peer);
            return Ok(answer(&state, 0, false));
        }
        let assembled = state.installing.get_mut(&peer).map_or(0, |assembly| {
            assembly.data.extend_from_slice(&message.data);
            assembly.data.len()
        });

        if !message.done {
            return Ok(answer(&state, assembled, false));
        }

        let payload = state
            .installing
            .remove(&peer)
            .map(|assembly| assembly.data)
            .unwrap_or_default();
        let refusal = answer(&state, 0, false);

        let Ok((meta, ownership, records)) = crate::snapshot::decode_snapshot(&payload) else {
            tracing::error!("raft: refusing a snapshot that did not decode");
            return Ok(refusal);
        };
        if let Some(reason) = Self::why_the_snapshot_cannot_be_real(
            &meta,
            message.term,
            (message.last_index, message.last_term),
        ) {
            // Rejecting protocol-impossible input early rather than corrupting
            // state with it. A correct leader cannot produce these, so seeing
            // one means a peer is wrong and the only safe answer is to keep our
            // own state.
            tracing::error!(peer, reason, "raft: refusing a snapshot");
            return Ok(refusal);
        }
        let (gc, forget) = self
            .registry
            .with_read_store(|store| (store.gc_interval(), store.forget_interval()));
        let Ok(store) = crate::snapshot::install(records, gc, forget) else {
            tracing::error!("raft: refusing a snapshot that did not install");
            return Ok(refusal);
        };

        state
            .machine
            .install_snapshot(&self.registry, store, ownership, meta.last_index);
        state.log.reset_to_snapshot(meta.last_index, meta.last_term);
        // Kept as this member's own snapshot. The log now starts at the
        // snapshot's boundary, so the entries below it exist on this member only
        // as these bytes -- and a member that cannot hand them on strands every
        // follower that needs them, should it ever lead. Only compaction used to
        // set these, so a leader that had caught up by snapshot and not
        // compacted since could send its stranded followers nothing but
        // keepalives, for as long as it led: the chaos soak's commonest liveness
        // failure. etcd keeps an applied snapshot as the storage's own
        // (`storage.go:218-237`) and serves that (`raft.go:672`).
        state.snapshot = Arc::from(payload);
        state.snapshot_meta = Some(meta);
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

        Ok(answer(&state, assembled, true))
    }

    fn on_install_snapshot_reply(
        &self,
        peer: u64,
        message: &InstallSnapshotReply,
    ) -> Result<(), PersistentStateError> {
        let mut state = self.state.lock();
        if message.term > state.term {
            state.step_down(message.term)?;
            return Ok(());
        }
        if state.role != Role::Leader || message.term != state.term {
            // A reply is evidence only about the exchange it answers, and one
            // from an earlier term answers a transfer this leadership never
            // made. Its correlation id is below the floor this leadership
            // raised on beginning, so it would be fenced below as well; the
            // term is checked first, as `on_append_entries_reply` checks its
            // replies. Believed, a stale `done` credited the peer with *this* leader's
            // current snapshot -- measured as a member credited with index 504
            // from a term-78 reply about a snapshot through 500, whose genuine
            // rejections were then discarded as stale for good. etcd drops every
            // lower-term message before per-type handling (`raft.go:1133-1186`).
            return Ok(());
        }
        let last_index = state.log.last_index();
        let Some(tracked) = state.peers.get_mut(&peer) else {
            return Ok(());
        };
        if message.request_id <= tracked.reply_floor {
            // Sent before a reset -- a reconnect, or this leadership beginning
            // -- and so about an exchange this leader has disowned, possibly
            // with an incarnation of the peer that no longer exists. Fenced as
            // `on_append_entries_reply` fences its own.
            return Ok(());
        }

        // A peer working through a transfer is answering, and must count
        // toward check-quorum exactly as an append reply does. In
        // `go.etcd.io/raft` the snapshot acknowledgement arrives as an
        // ordinary `MsgAppResp`, so it sets `RecentActive` on the same line.
        tracked.last_heard_at = Some(Instant::now());

        // The follower's own statement of what it holds, credited whichever
        // chunk this answers: its committed prefix is this leader's, so the
        // credit is true however stale the reply (see
        // `InstallSnapshotReply::commit_index`). Bounded by this leader's log,
        // which a complete leader's commit-holding peers cannot exceed.
        let credited = message.commit_index.min(last_index);
        if credited > tracked.match_index {
            tracked.match_index = credited;
            tracked.next_index = tracked.next_index.max(credited.saturating_add(1));
        }

        if message.request_id != tracked.snapshot_request {
            // Not the reply to the chunk in flight -- the first copy of a chunk
            // sent again once its connection had ended, say, answered after
            // all. It drives nothing: a second stream beside the first is how
            // the transfer once forked.
            return Ok(());
        }
        tracked.snapshot_request = 0;

        let held = tracked
            .sending
            .as_ref()
            .is_some_and(|(pinned, _)| message.commit_index >= pinned.last_index);
        if message.done || held {
            // Installed -- or already held, which etcd calls an ignored
            // snapshot: either way the follower has everything the transfer
            // covers, and replication resumes from what it has said it holds.
            tracked.sending = None;
            tracked.snapshot_offset = 0;
            tracked.next_index = tracked.match_index.saturating_add(1);
            self.send_append(&mut state, peer);
            return Ok(());
        }
        if message.bytes_received == 0 {
            // The follower threw the transfer away. It starts again, from a
            // fresh pin -- but at the next heartbeat, not now: a refusal that
            // recurs would otherwise ping-pong at network speed. etcd pauses a
            // failed snapshot's peer the same way (`MsgAppFlowPaused`,
            // `raft.go:1618-1628`).
            tracked.sending = None;
            tracked.snapshot_offset = 0;
            return Ok(());
        }

        // How much the follower has assembled: the acknowledgement, and the
        // offset to resume from.
        tracked.snapshot_offset = usize::try_from(message.bytes_received).unwrap_or(0);
        self.send_snapshot(&mut state, peer);
        Ok(())
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

    /// A member asking this one, as its leader, for a read index.
    ///
    /// Waits for confirmation, or for leadership to end, and for nothing else:
    /// a leader that cannot hear a quorum stands down within an election window
    /// (check-quorum), which fails every read waiting on it. The asker bounds
    /// its own wait; etcd's leader holds reads the same way.
    async fn on_read_index(&self, _peer: u64, message: &ReadIndex) -> ReadIndexReply {
        let receiver = {
            let mut state = self.state.lock();
            if state.role != Role::Leader {
                return ReadIndexReply {
                    ok: false,
                    index: 0,
                    reason: "not the leader".to_owned(),
                    request_id: message.request_id,
                };
            }
            self.begin_read(&mut state)
        };
        let (ok, index, reason) = match receiver.await {
            Ok(Ok(index)) => (true, index, String::new()),
            Ok(Err(error)) => (false, 0, error.0),
            Err(_) => (false, 0, "member is shutting down".to_owned()),
        };
        ReadIndexReply {
            ok,
            index,
            reason,
            request_id: message.request_id,
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
            // A transfer from this peer may never resume, and held its buffer
            // for the life of the member if it did not. Nothing is lost by
            // dropping it: a reconnected leader restarts at offset 0, and if the
            // leader's own link survived -- links are directed -- its next chunk
            // finds no buffer, is answered with 0, and it starts over. A
            // restart, never a splice and never a stall.
            state.installing.remove(&peer);
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
                // A reconnect ends the exchange: whatever was in flight is
                // disowned (`reply_floor` above fences its replies, which on
                // BULK may yet arrive), and holding the pause open would strand
                // the peer.
                tracked.snapshot_request = 0;
                tracked.pending_request = 0;
                tracked.snapshot_offset = 0;
                tracked.sending = None;
                // The peer may have restarted and lost everything, so what it
                // was last told about the commit index says nothing now.
                tracked.sent_commit = 0;
            }
            self.send_append(&mut state, peer);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::split_by_bytes;

    #[test]
    fn payloads_are_grouped_by_bytes_in_order() {
        let payloads: Vec<Vec<u8>> = vec![
            vec![0; 10],
            vec![0; 10],
            vec![0; 10],
            vec![0; 100],
            vec![0; 5],
        ];
        let groups = split_by_bytes(payloads, 25);
        let sizes: Vec<Vec<usize>> = groups
            .iter()
            .map(|group| group.iter().map(Vec::len).collect())
            .collect();
        // Two fit, the third would not; the 100-byte one goes alone; the rest follow.
        assert_eq!(sizes, vec![vec![10, 10], vec![10], vec![100], vec![5]]);
    }

    #[test]
    fn nothing_is_dropped_and_a_bound_of_zero_is_one_per_group() {
        let payloads: Vec<Vec<u8>> = vec![vec![1], vec![2, 2], vec![3]];
        assert_eq!(
            split_by_bytes(payloads.clone(), 0),
            vec![vec![vec![1]], vec![vec![2, 2]], vec![vec![3]]]
        );
        assert!(split_by_bytes(Vec::new(), 0).is_empty());
        assert_eq!(split_by_bytes(payloads, usize::MAX).len(), 1);
    }
}
