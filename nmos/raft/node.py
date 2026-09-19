# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The consensus core: roles, elections, replication, and the commit rule.

Raft as described in the paper, with one deliberate departure that the rest of
this package exists to make safe.

The departure, and why it is needed
-----------------------------------
The log is not durable. Raft's election-safety argument depends on it being
durable, in a way that is easy to miss: the up-to-dateness check in
``RequestVote`` is what stops a candidate missing a committed entry from being
elected, and it works because a voter that *has* the entry refuses. Take away
the voter's log and it refuses nothing.

    A is leader in term 5 and replicates entry E to B. Quorum {A, B} commits
    it, the client is told 201. B restarts: it recovers its term from
    ``persist.py``, but its log is empty. C -- which never received E -- times
    out and campaigns in term 6. B's log is empty, so every candidate looks
    up to date, and B votes for C. C wins with {B, C}, and C's log has no E.

An acknowledged registration, lost to a single non-simultaneous failure. A
rolling restart -- this design's upgrade and resize procedure -- is that
scenario once per member.

The fix: a member that has ever acknowledged entries does not vote until it has
been caught up and explicitly promoted. While non-voting it grants no votes,
starts no elections, and reports ``catching_up`` so the leader does not count
its acknowledgements toward a commit.

Why "ever acknowledged" and not "has restarted"
-----------------------------------------------
A member starting for the very first time (``incarnation == 1``) has never
acknowledged anything, so the intersection argument still protects it: any
majority that could elect a leader contains a member that holds every committed
entry, and that member refuses. Making a *fresh* member non-voting would
deadlock a cold start -- every member of a new cluster would be waiting for a
promotion from a leader that can never be elected.

The distinction is exactly right, and it is why ``persist.py`` counts starts
rather than storing a boolean.

Apply is bounded
----------------
``machine.apply`` is synchronous from first mutation to last grain, because
``store.py``'s no-locks invariant depends on it. The other half of that bargain
is here: apply a bounded run, yield between runs, never inside one. A
50,000-entry catch-up applied in one block would stall the HTTP server and the
heartbeat timer -- and a stalled heartbeat timer causes an election, which
causes more catch-up.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from nmos.raft.batcher import Pending, ProposalBatcher
from nmos.raft.cluster import RaftLayout
from nmos.raft.cursors import CursorAllocator
from nmos.raft.errors import (
    RaftInvariantViolated,
    RaftLogCompacted,
    RaftUnavailable,
)
from nmos.raft.log import Entry, RaftLog
from nmos.raft.machine import Outcome, StateMachine
from nmos.raft.messages import (
    AppendEntries,
    AppendEntriesReply,
    InstallSnapshot,
    InstallSnapshotReply,
    Promote,
    Propose,
    ProposeReply,
    RequestVote,
    RequestVoteReply,
    WireEntry,
)
from nmos.raft.operations import (
    NoopOp,
    ProposalId,
    RegistryOperation,
    decode_operation,
    encode_operation,
)
from nmos.raft.ownership import OwnershipTable
from nmos.raft.persist import PersistentState, TermStore
from nmos.raft.snapshot import SnapshotMeta, SnapshotStore, decode_snapshot, install
from nmos.raft.transport import Transport
from nmos.raft.wire import Stream
from nmos.registry.fence import RevisionFence

log = logging.getLogger(__name__)

# Proposal ids a single incarnation of a member may mint before it would tread
# on the next one's range. Four billion is far beyond what any member will
# issue between restarts, and the id is a varint on the wire, so the larger
# numbers cost a couple of bytes per entry and nothing else.
PROPOSALS_PER_INCARNATION = 1 << 32


class Role(Enum):
    FOLLOWER = "follower"
    PRE_CANDIDATE = "pre-candidate"
    """Asking whether it *would* win, without having claimed a term.

    Raft §9.6. A pre-candidate has incremented nothing and persisted nothing,
    so a member that has lost contact can discover it would lose without
    forcing a term increment on a cluster that is working. It also, by having
    given up on its leader, stops refusing votes on that leader's behalf --
    which is what lets a quorum of pre-candidates replace a leader that really
    has died. See ``_pre_campaign``.
    """

    CANDIDATE = "candidate"
    LEADER = "leader"


@dataclass(frozen=True)
class RaftTiming:
    """Timings, all injectable so tests can compress them.

    The election window must be comfortably larger than the heartbeat interval,
    or a healthy leader's heartbeats race its followers' timers and the cluster
    churns leadership under no load at all. The randomised range is what stops
    every follower campaigning in the same instant and splitting the vote
    forever.
    """

    heartbeat: float = 0.05
    election_min: float = 0.30
    election_max: float = 0.60
    max_entries_per_append: int = 256
    max_apply_batch: int = 128

    compaction_threshold: int = 4096
    """Applied entries held before a snapshot is taken."""

    max_log_entries: int = 65536
    """Hard cap. Beyond it the log is compacted even though a follower still
    needs the entries -- that follower is caught up by a snapshot instead.
    Without a cap, one unreachable member makes the log grow without bound,
    which turns a partial outage into an out-of-memory failure."""

    snapshot_chunk: int = 1 << 20

    def election_timeout(self) -> float:
        return random.uniform(self.election_min, self.election_max)


@dataclass
class _PeerState:
    """What a leader tracks about one follower."""

    next_index: int = 1
    match_index: int = 0
    up: bool = False
    incarnation: int = 0

    catching_up: bool = False
    """The peer says its acknowledgements must not count yet.

    Authoritative from the peer's own reply rather than inferred from the
    incarnation the leader happened to see: the peer knows whether it has been
    promoted, and a leader that had to remember incarnations across its own
    restarts would get this wrong exactly when it matters.
    """

    promote_through: int = 0
    """Index this peer must reach before its vote counts again."""

    pending_through: int = 0
    """Highest index sent to this peer and not yet acknowledged.

    Flow control, and the reason for it is measurable. ``next_index`` only
    advances on a reply, so without this the leader re-sends the *same* window
    on every heartbeat until the peer answers. A healthy peer answers within a
    tick and the cost is nil -- measured at x1.0 -- but a peer one RTT behind
    receives the window once per heartbeat for as long as the round trip
    takes: measured at **x15** against a 0.5 s link and a 20 ms heartbeat,
    and it scales with RTT / heartbeat.

    Retransmission is idempotent, so this was never a correctness problem. It
    is a feedback loop: the slower a link gets, the more traffic is pushed
    into it. etcd avoids it by sending heartbeats as a separate message from
    appends, which is what ``_send_append`` now does when this is outstanding.
    """

    pending_request: int = 0
    """Correlation id of the outstanding append.

    Needed because "still in flight" and "lost" are otherwise
    indistinguishable: a heartbeat sent meanwhile draws a reply whose
    ``match_index`` is still behind, which looks exactly like loss and would
    retransmit for the same reason the pause exists to avoid. Replies echo the
    id, so only the answer to *this* append clears it.
    """

    pending_since: float = 0.0
    """When ``pending_through`` was sent, so a genuinely lost append -- or a
    lost reply -- is still retransmitted rather than waited on forever."""

    sent_commit: int = 0
    """The commit index last *sent* to this peer.

    ``go.etcd.io/raft`` calls this ``sentCommit`` and gates an eager send on it
    (``tracker/progress.go:189``, ``CanBumpCommit``). Without it the leader
    either repeats a commit index the peer already has, or -- which is what
    happened here -- never sends it at all until the next heartbeat.
    """

    reply_floor: int = 0
    """Correlation ids at or below this belong to a superseded exchange.

    Set to the current value of the node-wide append sequence whenever this
    leader's view of the peer is reset -- on becoming leader, and on a
    reconnect. Every send made afterwards is minted above it, so a reply at or
    below it was drawn by a send this leader has since disowned.

    It exists because nothing else identifies one. A reply carries no
    incarnation, and an append reply is not a correlated request whose future
    the transport fails when the link drops, so a reply from the incarnation
    that has just been replaced arrives looking exactly like a current one --
    and is then applied to the member that replaced it.
    """

    last_heard_at: float = 0.0
    """When this peer last *answered*, which is what check-quorum runs on.

    ``go.etcd.io/raft`` keeps the same evidence as ``RecentActive``, set only
    on a reply (``raft.go:1388``, ``:1580``) and cleared for every peer once an
    election interval (``raft.go:1286``); ``QuorumActive`` then asks whether a
    majority answered inside that window.

    A timestamp rather than a flag-and-sweep, which is the same question asked
    without a second timer.

    The distinction from ``up`` is the point. ``up`` is the TCP link, and a
    peer whose process is stopped, deadlocked or stalled holds its socket open
    for minutes: the kernel keeps the connection and nothing errors until
    retransmits give up. Counting such a peer toward the quorum is how a leader
    goes on answering as leader, and accepting writes that can never commit,
    while a majority of the cluster is not actually there.
    """

    snapshot_offset: int = 0
    """How much of the snapshot this peer has confirmed receiving."""

    snapshot_in_flight: bool = False
    """A chunk is out and unanswered.

    Without this the transfer is driven from two places at once -- the
    replication tick and the previous chunk's reply -- so two chunks go out
    carrying the same offset, the follower sees the second as out of order,
    restarts from zero, and the pair loop forever making no progress. The
    symptom is a member that never catches up while the leader appears busy
    sending it everything it needs.
    """


class RaftNode:
    """One member's consensus state machine.

    Args:
        layout: The derived cluster: members, quorum, this member's index.
        transport: How peers are reached. A protocol, so the deterministic
            harness can substitute an in-memory one.
        terms: Durable term and vote.
        machine: The applier.
        timing: Election and replication timings.
    """

    def __init__(
        self,
        layout: RaftLayout,
        *,
        transport: Transport,
        terms: TermStore,
        machine: StateMachine,
        timing: RaftTiming | None = None,
        snapshots: SnapshotStore | None = None,
    ) -> None:
        self._layout = layout
        self._transport = transport
        self._terms = terms
        self._machine = machine
        self._timing = timing or RaftTiming()
        self._snapshots = snapshots

        state = terms.load()
        self._term = state.term
        self._voted_for = state.voted_for
        self._incarnation = state.incarnation

        self._role = Role.FOLLOWER
        self._leader: int | None = None
        self._commit_index = 0
        self._log: RaftLog[RegistryOperation] = RaftLog()
        self._peers: dict[int, _PeerState] = {
            member.index: _PeerState()
            for member in layout.members if member.index != layout.local.index
        }

        # A member that has never started before has never acknowledged an
        # entry, so nothing it could be missing was ever counted toward a
        # commit. Making it wait for a promotion would deadlock a cold start.
        self._voting = state.incarnation == 1 or layout.size == 1

        # Peers positively observed answering ``voting=False``. Evidence, not
        # belief: a peer that has simply not replied is absent from this set
        # and is therefore treated as a voter, which is what keeps a partition
        # from being mistaken for a cluster that has forgotten everything.
        # Cleared whenever a leader is heard from, so it can never go stale and
        # justify a recovery the cluster does not need.
        self._observed_amnesiac: set[int] = set()

        self._votes: set[int] = set()
        self._pre_votes: set[int] = set()
        # Correlates an append with its reply, so the flow-control pause
        # in ``_send_append`` releases on the right answer.
        self._append_sequence = 0
        self._waiters: dict[ProposalId, asyncio.Future[Outcome]] = {}
        # Seeded from the incarnation, NOT from zero. A restarted member's
        # entries outlive it: they are still in the cluster's log and will
        # apply after it comes back. Starting the sequence again at zero mints
        # ids the previous incarnation already used, and the outcome of an old
        # entry then resolves a *new* caller's future -- observed as a
        # registration being answered with an unregistration's result.
        #
        # Giving each incarnation its own range makes the id unique over the
        # member's whole history, which is what "identifies a proposal so its
        # originator can be answered" actually requires.
        self._sequence = state.incarnation * PROPOSALS_PER_INCARNATION
        self._batcher: ProposalBatcher[RegistryOperation, Outcome] = (
            ProposalBatcher(self._drain)
        )

        self._deadline = 0.0
        # When this member last accepted an AppendEntries from a leader. The
        # basis of the lease in ``on_request_vote``: a follower that is being
        # served by a healthy leader refuses to help depose it.
        self._heard_from_leader_at = 0.0
        self._ticker: asyncio.Task[None] | None = None
        self._applier: asyncio.Task[None] | None = None
        self._apply_wake = asyncio.Event()

        # The most recent snapshot this member holds, for serving to followers
        # that have fallen below ``log.first_index``.
        self._snapshot: bytes = b""
        self._snapshot_meta: SnapshotMeta | None = None
        # Inbound transfers, by the leader sending them.
        self._installing: dict[int, bytearray] = {}

        # How far this member has applied, for callers that must not answer
        # until a particular index is visible here. Reused verbatim from the
        # etcd backend -- it is generic over a monotonic integer, and the raft
        # log index is one.
        self._fence = RevisionFence(applied=0)
        self._forwarder: Any | None = None
        self._closing = False
        self._leader_changed = asyncio.Event()

    # -- introspection ---------------------------------------------------

    @property
    def role(self) -> Role:
        return self._role

    @property
    def term(self) -> int:
        return self._term

    @property
    def leader(self) -> int | None:
        return self._leader

    @property
    def voting(self) -> bool:
        return self._voting

    @property
    def incarnation(self) -> int:
        """How many times this member has started, from ``persist.py``.

        Read after construction because loading the term store is what bumps
        it -- the transport needs the same number the node is using, and
        loading it twice would give two different answers.
        """
        return self._incarnation

    @property
    def commit_index(self) -> int:
        return self._commit_index

    @property
    def last_applied(self) -> int:
        return self._machine.last_applied

    @property
    def log(self) -> RaftLog[RegistryOperation]:
        return self._log

    @property
    def index(self) -> int:
        return self._layout.local.index

    @property
    def fence(self) -> RevisionFence:
        return self._fence

    @property
    def transport(self) -> Transport:
        return self._transport

    @property
    def cursors(self) -> CursorAllocator:
        return self._machine.cursors

    @property
    def ownership(self) -> OwnershipTable:
        return self._machine.ownership

    @property
    def live_peers(self) -> frozenset[int]:
        """Peers this member can currently reach.

        Used to decide whether a Node's recorded owner is still in a position
        to serve it: an owner nobody can reach is, for the purpose of taking
        over, no owner at all.
        """
        return frozenset(
            peer for peer, state in self._peers.items() if state.up
        )

    def set_forward_handler(self, handler: Any) -> None:
        """Install the coroutine that answers a forwarded mutation.

        Forwarding is an application concern -- it hands a whole registration
        to the member that owns the Node -- so the consensus layer routes it
        rather than implementing it. Set by ``raft_backend.py`` at startup;
        until then a forward is refused rather than silently dropped.
        """
        self._forwarder = handler

    @property
    def has_quorum(self) -> bool:
        """Whether enough members are reachable for a write to commit.

        Reachability, not responsiveness -- see ``_quorum_is_answering`` for
        the stronger question a leader asks of itself. This one is asked by
        members that are *not* leading, which send nothing and so have no
        replies to count, and by the backend when reporting readiness.
        """
        reachable = 1 + sum(
            1 for peer in self._peers.values() if peer.up and not peer.catching_up
        )
        return reachable >= self._layout.quorum

    def _quorum_is_answering(self, now: float) -> bool:
        """Have a majority *answered* this leader within an election window?

        ``go.etcd.io/raft``'s ``QuorumActive`` (``tracker/tracker.go:208``),
        which check-quorum consults once an election interval and which counts
        only peers that have actually replied.

        The difference from ``has_quorum`` is the whole of finding 2 in the
        etcd comparison: a stopped or stalled peer keeps its socket open and
        stays ``up`` for minutes, so a leader counting connections can believe
        it has a quorum while a majority of the cluster is answering nothing.
        Writes accepted in that state can never commit.

        Members catching up are excluded for the same reason they are excluded
        from the commit count: their acknowledgements do not establish a
        quorum. etcd excludes learners from ``QuorumActive`` identically.
        """
        window = self._timing.election_max
        answering = 1 + sum(
            1 for peer in self._peers.values()
            if not peer.catching_up and now - peer.last_heard_at < window
        )
        return answering >= self._layout.quorum

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        self._closing = False
        await self._transport.start(self)
        self._reset_election_timer()
        self._ticker = asyncio.create_task(
            self._tick_forever(), name=f"raft-tick-{self._layout.local.name}",
        )
        self._applier = asyncio.create_task(
            self._apply_forever(), name=f"raft-apply-{self._layout.local.name}",
        )

    async def close(self) -> None:
        self._closing = True
        for task in (self._ticker, self._applier):
            if task is not None:
                task.cancel()
        # Awaited, not merely cancelled: a fire-and-forget apply task that
        # outlived close() could still be mutating the store while the member
        # is being torn down -- and, less dramatically but more often, leaks
        # into whatever runs next and perturbs its timing.
        pending = [t for t in (self._ticker, self._applier) if t is not None]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._ticker = None
        self._applier = None
        self._batcher.fail_all(RaftUnavailable("member is shutting down"))
        for future in self._waiters.values():
            if not future.done():
                future.set_exception(RaftUnavailable("member is shutting down"))
        self._waiters.clear()
        await self._transport.close()

    async def _tick_forever(self) -> None:
        while not self._closing:
            await asyncio.sleep(self._timing.heartbeat)
            try:
                self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("raft: tick failed")

    def _tick(self) -> None:
        now = asyncio.get_running_loop().time()
        if self._role is Role.LEADER:
            if not self._quorum_is_answering(now):
                # Check-quorum. A leader cut off from a majority cannot commit
                # anything, and the other side of the partition has had long
                # enough to elect someone else -- so continuing to answer as
                # leader would mean reporting READY while accepting writes
                # that can never commit.
                #
                # **One interval, not two.** ``_quorum_is_answering`` already
                # asks "within an election window", so counting a second
                # window down from the moment it turns false would double how
                # long a partitioned leader keeps the role.
                # ``go.etcd.io/raft`` steps down on the spot when
                # ``QuorumActive`` fails (``raft.go:1282``); the grace a fresh
                # leader needs comes from ``_become_leader`` seeding
                # ``last_heard_at``, not from a second timer.
                self._relinquish("lost contact with a quorum")
                return
            self._replicate()
            return
        if not self.has_quorum:
            # Not counting down while an election is impossible. Letting the
            # deadline expire unused means that the moment connectivity
            # returns, this member campaigns *immediately* -- with no fresh
            # timeout, before the existing leader's next heartbeat can reach
            # it -- and deposes a leader that never stopped being healthy.
            # Deferring instead makes a healed member wait a full election
            # window, which the incumbent's heartbeat comfortably wins.
            self._reset_election_timer()
            return
        if now < self._deadline:
            return
        if self._voting or self._cluster_has_forgotten():
            # Pre-Vote first, always. Winning the real election is the *only*
            # thing a term increment buys, so asking first costs one round trip
            # and saves every disruption a doomed campaign would cause.
            self._pre_campaign()
        else:
            # Cannot vote, and no evidence yet that the cluster has lost its
            # voters. Ask, rather than campaign: see the section above.
            self._probe_for_forgotten_peers()

    # -- elections -------------------------------------------------------
    #
    # When every voter has forgotten
    # ------------------------------
    # A member that restarts comes back with an empty log and must not vote:
    # an empty log considers every candidate up to date, so its vote would
    # defeat the §5.4.1 check that keeps a candidate missing committed entries
    # from winning. It is promoted back to voting by a leader, once caught up.
    #
    # That is safe and it deadlocks, because only a leader can promote. Once a
    # quorum's worth of members are non-voting, no election can succeed, so no
    # leader exists, so nobody is ever promoted. Restarting a whole cluster --
    # an upgrade, a power cycle -- reaches that state on the second boot and
    # never leaves it.
    #
    # The way out rests on one observation: **a guarantee that has already been
    # destroyed cannot be protected.** A committed entry lived on a quorum's
    # memory and nowhere else. If a quorum's worth of members have lost their
    # logs, then for any entry either some member that held it still has it, or
    # no copy exists anywhere. So:
    #
    #   * refuse to escalate while a quorum of voters is still *possible* --
    #     there, the ordinary rules protect real data and must not be relaxed;
    #   * once a voting quorum is provably impossible, let members that have
    #     forgotten vote again, but only for a candidate approved by **every**
    #     member not known to have forgotten.
    #
    # The second clause is what makes it sound rather than merely convenient.
    # Any entry that survives does so on a member that still has its log; that
    # member applies the ordinary up-to-dateness check and refuses a candidate
    # lacking the entry; and since its approval is required, such a candidate
    # cannot win. Entries held only by members that forgot are gone either way.
    #
    # Worked through on five members with three forgetful ones, where a
    # committed entry survives on one of the two remaining: the candidate that
    # lacks it needs that member's approval and is refused, so the candidate
    # that has it is the only one that can win. See
    # ``TestForgottenQuorumRecovery`` in ``test_consensus.py``.

    def _cluster_has_forgotten(self, also: set[int] | None = None) -> bool:
        """Is a quorum of voters provably impossible?

        Counts only members *known* to have forgotten -- this member if it has,
        plus peers observed saying so, plus ``also`` when evaluating a
        candidate's claim. Everything else counts as a voter, including members
        nobody has heard from, because an unreachable member is not evidence of
        anything and treating it as one is how a partition turns into a
        cluster that elects itself a second leader.
        """
        forgotten = set(self._observed_amnesiac)
        forgotten |= also or set()
        if not self._voting:
            forgotten.add(self._layout.local.index)
        forgotten &= {member.index for member in self._layout.members}
        return self._layout.size - len(forgotten) < self._layout.quorum

    def _leader_lease_holds(self) -> bool:
        """Is this member currently being served by a leader it believes in?

        ``election_min`` rather than the randomised timeout, so the lease is
        always shorter than the shortest interval after which any member would
        legitimately start an election. A lease that could outlast a real
        election window would refuse votes to a candidate the cluster needs.

        A leader does not hold a lease against anyone: it answers on its own
        terms, and a higher term is how it learns it has been replaced.
        """
        if self._role is Role.LEADER or self._leader is None:
            return False
        elapsed = (
            asyncio.get_running_loop().time() - self._heard_from_leader_at
        )
        return elapsed < self._timing.election_min

    def _probe_for_forgotten_peers(self) -> None:
        """Ask every peer whether it can vote, without standing for election.

        A member that has forgotten cannot campaign until it knows how many
        others have too, and cannot learn that without asking. Asking by
        campaigning would raise the term on every attempt while never
        succeeding -- which is precisely the runaway this replaces.

        Sent at the current term and granting nothing, so it disturbs neither
        an election in progress nor a healthy leader.
        """
        request = RequestVote(
            term=self._term, candidate=self._layout.local.index,
            last_log_index=self._log.last_index,
            last_log_term=self._log.last_term,
            probe=True,
        )
        for peer in self._peers:
            self._transport.send(peer, request)
        self._reset_election_timer()

    def _reset_election_timer(self) -> None:
        self._deadline = (
            asyncio.get_running_loop().time() + self._timing.election_timeout()
        )

    def _persist(self) -> None:
        self._terms.save(PersistentState(
            term=self._term, voted_for=self._voted_for,
            incarnation=self._incarnation,
        ))

    def _relinquish(self, reason: str) -> None:
        """Stop leading without changing term or vote.

        Deliberately *not* ``_step_down``: that clears ``voted_for``, which is
        correct when adopting a higher term and catastrophic here. This member
        voted for itself in the current term, and forgetting that would let it
        vote again in the same term -- the exact double-vote the persisted
        state exists to prevent.
        """
        if self._role is not Role.LEADER:
            return
        log.info(
            "raft: %s relinquishing leadership of term %d: %s",
            self._layout.local.name, self._term, reason,
        )
        self._role = Role.FOLLOWER
        self._leader = None
        self._votes.clear()
        self._pre_votes.clear()
        self._batcher.fail_all(RaftUnavailable(reason))
        self._reset_election_timer()

    def _step_down(self, term: int) -> None:
        """Adopt a higher term and return to following."""
        was_leader = self._role is Role.LEADER
        self._term = term
        self._voted_for = None
        self._role = Role.FOLLOWER
        self._leader = None
        self._votes.clear()
        self._pre_votes.clear()
        self._persist()
        if was_leader:
            # In-flight proposals cannot commit under a term we no longer own.
            self._batcher.fail_all(RaftUnavailable("no longer the leader"))

    def _pre_campaign(self) -> None:
        """Ask whether this member would win, before claiming a term.

        Raft §9.6, and etcd's ``MsgPreVote``. Nothing here is mutated that a
        peer could observe: the term is not incremented, the vote is not
        recorded, nothing reaches the disk. The request carries the term this
        member *would* stand in -- one above its own -- so voters can apply the
        up-to-dateness check against a real proposal.

        The role change is not cosmetic. Becoming a pre-candidate clears
        ``_leader``, which releases the lease this member was holding on its
        old leader's behalf. That is what makes etcd's ``prevote_checkquorum``
        case work: "a node is able to obtain prevotes ... if a quorum of voters
        are precandidates". Without it, PreVote and CheckQuorum together refuse
        to elect anyone after a leader dies -- each member still vouching for a
        leader that is gone.
        """
        self._role = Role.PRE_CANDIDATE
        self._leader = None
        self._pre_votes = {self._layout.local.index}
        self._reset_election_timer()

        log.debug(
            "raft: %s pre-campaigning for term %d",
            self._layout.local.name, self._term + 1,
        )

        if self._won_pre_vote():
            self._campaign()
            return

        request = RequestVote(
            term=self._term + 1, candidate=self._layout.local.index,
            last_log_index=self._log.last_index,
            last_log_term=self._log.last_term,
            amnesiac=tuple(sorted(self._observed_amnesiac)),
            pre_vote=True,
        )
        for peer in self._peers:
            self._transport.send(peer, request)

    def _won_pre_vote(self) -> bool:
        """Same arithmetic as a real election, over pre-votes.

        Shares ``_won``'s recovery clause deliberately: if a pre-vote round
        could be won on terms the real election would refuse, the pre-vote
        would stop predicting anything and the term increment it exists to
        avoid would happen anyway.
        """
        return self._won(votes=self._pre_votes)

    def _campaign(self) -> None:
        """Start an election. Only ever called with a reachable quorum.

        That guard matters more than it looks. A member cut off from the
        cluster cannot win an election, but without the check it would keep
        campaigning anyway, incrementing its term on every timeout. When the
        partition healed it would arrive carrying a term far above everyone
        else's, force the healthy leader to step down, and cause an election
        the cluster had no reason to hold -- the "disruptive server" problem.

        Pre-Vote is the general fix and is what larger implementations adopt.
        This is the narrow version of it: a member that cannot reach a quorum
        does not disturb one. It is enough here because the cluster is 1, 3 or
        5 members on a LAN, and it reuses the reachability the transport
        already reports rather than adding a round trip and a message type.
        """
        self._role = Role.CANDIDATE
        self._term += 1
        self._voted_for = self._layout.local.index
        self._persist()
        self._votes = {self._layout.local.index}
        self._leader = None
        self._reset_election_timer()

        log.debug(
            "raft: %s campaigning in term %d",
            self._layout.local.name, self._term,
        )

        if self._won():
            self._become_leader()
            return

        request = RequestVote(
            term=self._term, candidate=self._layout.local.index,
            last_log_index=self._log.last_index,
            last_log_term=self._log.last_term,
            # The evidence travels with the request so a voter that has
            # forgotten can re-do the arithmetic itself rather than take this
            # candidate's word for the state of the cluster.
            amnesiac=tuple(sorted(self._observed_amnesiac)),
        )
        for peer in self._peers:
            self._transport.send(peer, request)

    def on_request_vote(self, peer: int, message: RequestVote) -> RequestVoteReply:
        if message.probe:
            # A question, not a request. Answered at whatever term we hold, and
            # deliberately without adopting the asker's term or touching the
            # election timer: a probe must be able to survey a cluster without
            # changing it.
            return RequestVoteReply(
                term=self._term, granted=False, voting=self._voting,
            )

        if self._leader_lease_holds():
            # Raft §6's disruption problem, and the reason etcd's check-quorum
            # tests assert "votes are rejected when there is a current
            # leader". This member is being served right now, so a candidate
            # asking it to help depose that leader is answered no -- and
            # crucially **without adopting the candidate's term**, because
            # adopting it is itself the disruption: it clears the leader and
            # the vote, and the cluster holds an election it had no reason to.
            #
            # The quorum gate on ``_campaign`` already stops a *partitioned*
            # member from doing this. It does not stop one whose event loop
            # stalled long enough to miss its heartbeats -- which in Python,
            # under load, is the likelier cause of the two.
            #
            # Costs at most one election timeout when a leader really does
            # die: the lease expires and the next request is answered
            # normally.
            return RequestVoteReply(
                term=self._term, granted=False, voting=self._voting,
                pre_vote=message.pre_vote,
            )

        if message.pre_vote:
            return self._answer_pre_vote(message)

        if message.term > self._term:
            self._step_down(message.term)

        # A member that has forgotten its log normally refuses. It votes only
        # once the candidate's evidence, together with its own condition,
        # proves no quorum of voters can exist -- at which point no committed
        # entry can still be protected by refusing. The arithmetic is re-done
        # here rather than trusted, so a candidate cannot talk a voter into it.
        may_vote = self._voting or self._cluster_has_forgotten(
            set(message.amnesiac),
        )

        granted = False
        if message.term == self._term and may_vote:
            already = self._voted_for
            free = already is None or already == message.candidate
            current = self._log.is_at_least_as_current_as(
                message.last_log_index, message.last_log_term,
            )
            if free and current:
                granted = True
                self._voted_for = message.candidate
                # Durable BEFORE the reply leaves. A vote that is granted and
                # then forgotten is the whole failure this design guards
                # against, and the window is exactly here.
                self._persist()
                self._reset_election_timer()

        return RequestVoteReply(
            term=self._term, granted=granted, voting=self._voting,
        )

    def _answer_pre_vote(self, message: RequestVote) -> RequestVoteReply:
        """Answer "would you vote for me?" without becoming a party to it.

        Nothing is mutated: not the term, not ``voted_for``, not the election
        timer, nothing on disk. That is the entire contract of a pre-vote, and
        breaking any part of it would make the round as disruptive as the
        election it exists to avoid.

        The grant conditions are the real election's, minus the recorded vote
        -- a member may pre-vote for several candidates in the same round,
        because it has promised none of them anything.

        The reply's term follows etcd: the *prospective* term when granting, so
        the candidate can count it against the term it proposed, and this
        member's own term when refusing, so a candidate standing on a stale
        term learns to step down.
        """
        may_vote = self._voting or self._cluster_has_forgotten(
            set(message.amnesiac),
        )
        granted = (
            may_vote
            and message.term > self._term
            and self._log.is_at_least_as_current_as(
                message.last_log_index, message.last_log_term,
            )
        )
        return RequestVoteReply(
            term=message.term if granted else self._term,
            granted=granted, voting=self._voting, pre_vote=True,
        )

    def on_request_vote_reply(self, peer: int, message: RequestVoteReply) -> None:
        # Recorded before anything else, and for probes and pre-votes too:
        # this is the only way a member learns which of its peers have
        # forgotten, and a reply that arrives after the round it belonged to
        # is still evidence.
        if message.voting:
            self._observed_amnesiac.discard(peer)
        else:
            self._observed_amnesiac.add(peer)

        if message.pre_vote:
            self._on_pre_vote_reply(peer, message)
            return

        if message.term > self._term:
            self._step_down(message.term)
            return
        if self._role is not Role.CANDIDATE or message.term != self._term:
            return
        if message.granted:
            self._votes.add(peer)
            if self._won():
                self._become_leader()

    def _on_pre_vote_reply(self, peer: int, message: RequestVoteReply) -> None:
        """Count a pre-vote, or learn that this member is behind.

        A *refused* pre-vote carries the voter's own term. If that is above
        ours we are stale and step down -- which is the one state change a
        pre-vote round may cause, and it is a correction, not a disruption.

        A *granted* pre-vote carries the prospective term, which is ours plus
        one; it must never be mistaken for evidence that we are behind.
        """
        if not message.granted and message.term > self._term:
            self._step_down(message.term)
            return
        if self._role is not Role.PRE_CANDIDATE:
            return
        if message.granted and message.term == self._term + 1:
            self._pre_votes.add(peer)
            if self._won_pre_vote():
                self._campaign()

    def _won(self, votes: set[int] | None = None) -> bool:
        """Has this candidate collected enough of the right votes?

        Ordinarily a quorum, unchanged. Once a quorum of voters is impossible,
        a quorum of votes is necessary but no longer sufficient: every member
        not known to have forgotten must also have granted, because those are
        the only members whose up-to-dateness check still means anything and
        the surviving copy of a committed entry can only be on one of them.

        Members nobody has heard from count among those, and they cannot have
        granted -- so a partitioned cluster never satisfies this, which is the
        intended answer.
        """
        tally = self._votes if votes is None else votes
        if len(tally) < self._layout.quorum:
            return False
        if not self._cluster_has_forgotten():
            return True
        forgotten = set(self._observed_amnesiac)
        if not self._voting:
            forgotten.add(self._layout.local.index)
        remembering = {
            member.index for member in self._layout.members
        } - forgotten
        return remembering <= tally

    def _become_leader(self) -> None:
        self._role = Role.LEADER
        self._leader = self._layout.local.index
        # A leader is by definition a voter: if this member won through the
        # recovery path above it was not one a moment ago, and leaving it
        # non-voting would leave it unable to vote in the next election it
        # takes part in -- for no reason, since it is now the member the others
        # are being caught up *from*.
        self._voting = True
        # Whatever was observed about who had forgotten belonged to the
        # election just concluded. Keeping it would let a cluster that has
        # since recovered still believe its voters were gone.
        self._observed_amnesiac.clear()
        # Figure 2: nextIndex and matchIndex are "reinitialized after
        # election". Everything below describes *this leader's* relationship
        # with the peer, so it has the same lifetime and is reset with them.
        #
        # `catching_up` and `promote_through` in particular. They are a pair,
        # and keeping them was a safety bug: `promote_through` is only ever
        # assigned on a False->True transition of `catching_up`, so a stale
        # True means a new leader never re-decides the bar. Measured -- a
        # leader that had led before, committed through index 6, and saw the
        # peer report catching-up again kept `promote_through` at 1 from the
        # earlier term, and would have promoted that member back into the
        # electorate holding none of indices 2..6.
        #
        # A member promoted while still missing committed entries is a voter
        # that can grant a vote Raft's election restriction exists to refuse,
        # which is how a later leader ends up without a committed entry --
        # Leader Completeness, exactly as the chaos soak reported it.
        #
        # Nothing is lost by resetting: `catching_up` is authoritative from the
        # peer's own reply and arrives on the very next one, and that reply now
        # sets `promote_through` against *this* leader's commit index.
        #
        # `up` and `incarnation` are deliberately kept. They are observations
        # about the peer itself rather than about this leadership, and
        # forgetting them would make a healthy cluster look down for a tick.
        now = asyncio.get_running_loop().time()
        next_index = self._log.last_index + 1
        for peer in self._peers.values():
            peer.next_index = next_index
            peer.match_index = 0
            # One election window of grace before check-quorum asks anything of
            # them, matching the deadline armed below. A leader that demanded
            # evidence it has not had time to collect would step down in the
            # tick after winning.
            peer.last_heard_at = now
            peer.catching_up = False
            peer.promote_through = 0
            # In-flight bookkeeping for appends this member sent while it was
            # previously leader. A stale correlation id makes `_carrying_entries_would_repeat_them`
            # report an outstanding request that no longer exists, which
            # suppresses replication until the `election_min` backstop expires.
            peer.pending_through = 0
            peer.pending_request = 0
            peer.pending_since = 0.0
            # Same lifetime as the rest: what an earlier leadership told this
            # peer says nothing about what this one has committed.
            peer.sent_commit = 0
            # Replies drawn by the previous leadership's sends say nothing
            # about this one's, and this leader has just reset everything they
            # would report on.
            peer.reply_floor = self._append_sequence
            # Likewise a snapshot this member was sending in an earlier term:
            # the new transfer starts from zero, and a carried-over offset
            # would have the leader resume a stream the peer is not expecting.
            peer.snapshot_offset = 0
            peer.snapshot_in_flight = False
        log.info(
            "raft: %s is leader for term %d",
            self._layout.local.name, self._term,
        )
        self._leader_changed.set()
        self._leader_changed.clear()

        # Raft §8: a new leader cannot know what earlier terms committed until
        # it commits an entry of its own, so it appends one that does nothing.
        self._append_local([NoopOp(proposal=self._next_proposal())])
        self._replicate()

    # -- replication -----------------------------------------------------

    def _next_proposal(self) -> ProposalId:
        self._sequence += 1
        return ProposalId(
            member=self._layout.local.index, sequence=self._sequence,
        )

    def _append_local(self, operations: Sequence[RegistryOperation]) -> tuple[int, int]:
        return self._log.append(
            self._term,
            [(encode_operation(op), op) for op in operations],
        )

    def _replicate(self) -> None:
        for peer, state in self._peers.items():
            if not state.up:
                continue
            self._send_append(peer, state)

    def _send_append(self, peer: int, state: _PeerState) -> None:
        previous = state.next_index - 1
        try:
            prev_term = self._log.term_at(previous)
            entries = self._log.slice(
                state.next_index, self._timing.max_entries_per_append,
            )
            if self._carrying_entries_would_repeat_them(state):
                # An append is already outstanding to this peer. Send the
                # heartbeat without the payload: it still renews the lease,
                # still carries the commit index, and still draws the reply
                # that will tell us where the peer actually is -- without
                # putting the same entries on a link that has not yet drained
                # the last copy. See ``_PeerState.pending_through``.
                entries = ()
        except RaftLogCompacted:
            # The entries this peer needs have been compacted away. It cannot
            # be caught up by replication, so it is caught up by state.
            # Both calls are inside the guard: the boundary can fall between
            # them, and a leader that only checked one would raise out of its
            # own tick.
            self._send_snapshot(peer, state)
            return
        # **Every send is correlated, not only the ones carrying entries.**
        #
        # The id is what tells a reply apart from one sent by a *previous
        # incarnation* of this peer. Nothing else can: a reply carries no
        # incarnation of its own, and an append reply is not a correlated
        # request whose future the transport fails when the link drops. So a
        # reply the dying member had already put on the wire can arrive after
        # this leader has reset its view and be read as news about the member
        # that replaced it.
        #
        # Measured: a leader credited a restarted member with index 6 while it
        # held nothing, *and* took ``catching_up=False`` from the same reply,
        # which put it back into the commit tally. On three members that is
        # leader plus phantom -- a quorum -- so the leader could commit an index
        # only it held. Leader Completeness, from one stale message.
        #
        # Ids are minted from one node-wide sequence, so every send made after
        # a reconnect has an id above every send made before it. That is what
        # ``reply_floor`` compares against.
        #
        # Only a send that *carries entries* arms the flow-control pause, which
        # is unchanged: a heartbeat's id will never equal ``pending_request``.
        self._append_sequence += 1
        request_id = self._append_sequence
        if entries:
            state.pending_through = entries[-1].index
            state.pending_request = request_id
            state.pending_since = asyncio.get_running_loop().time()

        # What this message can actually deliver, which is not always the whole
        # commit index. The receiver adopts ``min(leader_commit, prev_log_index
        # + len(entries))``, so a send whose entries were suppressed above
        # carries a window ending at ``previous`` no matter how far this leader
        # has committed. Recording the full commit index there would be the
        # leader telling itself it had passed on something the peer could not
        # take -- and ``_should_send_now`` would then see nothing left to say
        # and leave the peer behind until the next tick, which is the exact
        # stall the eager send exists to remove.
        #
        # ``go.etcd.io/raft`` keeps the same book by splitting the message
        # types: ``maybeSendAppend`` records ``committed`` because its window
        # always reaches ``Next-1`` (``raft.go:660``), while ``sendHeartbeat``
        # -- which carries no window at all -- records the conservative
        # ``min(pr.Match, committed)`` (``raft.go:709``). This implementation
        # has one message type, so it caps by the window instead, which is the
        # same rule stated once rather than twice. ``CanBumpCommit``'s comment
        # says what is being tracked: a commit index "may bump the follower's
        # commit index up to Next-1".
        window_last = entries[-1].index if entries else previous
        state.sent_commit = min(self._commit_index, window_last)
        self._transport.send(peer, AppendEntries(
            term=self._term,
            leader=self._layout.local.index,
            prev_log_index=previous,
            prev_log_term=prev_term,
            leader_commit=self._commit_index,
            request_id=request_id,
            entries=tuple(
                WireEntry(term=e.term, index=e.index, payload=e.payload)
                for e in entries
            ),
        ))

    def _carrying_entries_would_repeat_them(self, state: _PeerState) -> bool:
        """Is an append already in flight to this peer, and not yet overdue?

        Overdue matters as much as outstanding: a lost append, or a lost
        reply, draws no answer at all, so a leader that waited forever would
        strand the peer. ``election_min`` is the backstop -- the same scale
        etcd un-pauses on, and necessarily shorter than the interval after
        which this leader would be replaced anyway.
        """
        if state.pending_request == 0:
            return False
        elapsed = asyncio.get_running_loop().time() - state.pending_since
        if elapsed >= self._timing.election_min:
            state.pending_request = 0
            return False
        return True

    def on_append_entries(
        self, peer: int, message: AppendEntries,
    ) -> AppendEntriesReply:
        if message.term < self._term:
            return self._append_reject(catching_up=not self._voting)

        if message.term > self._term:
            self._step_down(message.term)
        self._role = Role.FOLLOWER
        self._leader = message.leader
        self._reset_election_timer()
        self._heard_from_leader_at = asyncio.get_running_loop().time()
        # There is a leader, so a quorum of voters existed. Any evidence to the
        # contrary is out of date, and stale evidence is the one thing that
        # could justify the recovery path above when it is not warranted.
        self._observed_amnesiac.clear()

        if message.prev_log_index < self._commit_index:
            # A delayed or duplicated append anchored below what this member
            # has already committed. Answering it on its own terms would report
            # ``prev_log_index + len(entries)`` -- a match *below* our commit
            # index, which walks the leader's view of us backwards and makes it
            # re-send entries we hold. Answer with what we really have instead.
            #
            # ``go.etcd.io/raft`` returns early here for the same reason
            # (``raft.go:1796``), replying with ``r.raftLog.committed``.
            #
            # It is also the guard that keeps such a message away from the
            # truncation path below: everything at or below the commit index is
            # settled, and no append may reopen it.
            return AppendEntriesReply(
                term=self._term, success=True, match_index=self._commit_index,
                conflict_index=0, conflict_term=0,
                catching_up=not self._voting, request_id=message.request_id,
            )

        if not self._log.matches(message.prev_log_index, message.prev_log_term):
            conflict_index, conflict_term = self._log.find_conflict(
                message.prev_log_index, message.prev_log_term,
            )
            return AppendEntriesReply(
                term=self._term, success=False, match_index=0,
                conflict_index=conflict_index, conflict_term=conflict_term,
                catching_up=not self._voting, request_id=message.request_id,
            )

        if message.entries:
            self._log.append_replicated(
                [
                    Entry(
                        term=wire.term, index=wire.index, payload=wire.payload,
                        value=decode_operation(wire.payload),
                    )
                    for wire in message.entries
                ],
                committed=self._commit_index,
            )

        # Figure 2, AppendEntries receiver rule 5, verbatim: "If leaderCommit >
        # commitIndex, set commitIndex = min(leaderCommit, index of last new
        # entry)".
        #
        # "Index of last new entry" -- NOT this follower's last index, which is
        # what an earlier version used. The difference is a State Machine
        # Safety bug and the chaos soak found it: a follower holding stale
        # uncommitted entries *beyond* the window this message covered would
        # commit them on the strength of a commit index that said nothing about
        # them. Observed as index 3 applied from term 1 on an isolated member
        # while the majority held a term 2 entry there.
        #
        # The leader vouches only as far as ``prev_log_index`` plus what it
        # actually sent -- which for a heartbeat is ``prev_log_index`` alone,
        # and heartbeats are exactly when a follower's log runs ahead of the
        # leader's knowledge of it.
        vouched_for = message.prev_log_index + len(message.entries)
        advanced = min(message.leader_commit, vouched_for)
        if advanced > self._commit_index:
            # Compared rather than assigned, because ``min`` with a short
            # window can land below where this member already is, and a commit
            # index that moves backwards would un-apply committed state.
            self._commit_index = advanced
            self._schedule_apply()

        # ``vouched_for`` again, and for the same reason: Figure 2 has the
        # leader set ``matchIndex = prevLogIndex + entries.length`` from what it
        # SENT. Reporting ``self._log.last_index`` instead overstates whenever
        # this follower is holding stale uncommitted entries beyond the window
        # the message covered -- entries from a previous term that this leader
        # has never seen and does not have.
        #
        # The leader stores the number verbatim (``on_append_entries_reply``)
        # and ``_advance_commit`` counts it toward the quorum, so an overstated
        # match lets a leader commit an index that a majority does not actually
        # hold. That is Leader Completeness broken, and State Machine Safety
        # falls with it: the chaos soak observed one member applying a term-2
        # entry at index 7 while another applied a term-3 entry there.
        #
        # Reported by ``test_churn_over_real_sockets`` at roughly one run in
        # ten, which is why it survived -- a heartbeat has to arrive while the
        # follower's log runs ahead of the leader's knowledge of it, and that is
        # a narrow window outside a partition.
        #
        # This is the same quantity the commit rule above uses, and that is the
        # point: a follower vouches for exactly the range it would allow itself
        # to commit, and never for more.
        return AppendEntriesReply(
            term=self._term, success=True, match_index=vouched_for,
            conflict_index=0, conflict_term=0,
            catching_up=not self._voting, request_id=message.request_id,
        )

    def _append_reject(self, *, catching_up: bool) -> AppendEntriesReply:
        return AppendEntriesReply(
            term=self._term, success=False, match_index=0,
            conflict_index=0, conflict_term=0, catching_up=catching_up,
            request_id=0,
        )

    def on_append_entries_reply(
        self, peer: int, message: AppendEntriesReply,
    ) -> None:
        if message.term > self._term:
            self._step_down(message.term)
            return
        if self._role is not Role.LEADER or message.term != self._term:
            return

        state = self._peers.get(peer)
        if state is None:
            return

        if message.request_id <= state.reply_floor:
            # Drawn by a send this leader has since disowned -- see
            # ``_PeerState.reply_floor``. Believing it credits the member that
            # has just replaced this one with a log it does not have, and takes
            # ``catching_up`` from a member that no longer exists.
            return

        # Only the answer to *this* append releases the pause. A reply to a
        # send that carried no entries says nothing about whether entries
        # landed, and its id will never match ``pending_request``.
        answered_our_append = (
            message.request_id != 0
            and message.request_id == state.pending_request
        )
        if answered_our_append:
            state.pending_request = 0

        # Recorded before the success/failure split, as ``go.etcd.io/raft``
        # records ``RecentActive`` there (``raft.go:1388``, ``:1580``): the
        # peer answered, and that is true whatever it said. This is the
        # evidence check-quorum runs on -- see ``_quorum_is_answering``.
        state.last_heard_at = asyncio.get_running_loop().time()

        was_catching_up = state.catching_up
        state.catching_up = message.catching_up
        if message.catching_up and not was_catching_up:
            # Newly noticed: it must reach everything committed as of now
            # before its acknowledgements count again.
            state.promote_through = self._commit_index
            log.info(
                "raft: member %d is catching up through index %d",
                peer, state.promote_through,
            )

        if not message.success and message.conflict_index <= state.match_index:
            # Stale, and safe to say so only because of ``reply_floor``.
            #
            # ``go.etcd.io/raft`` refuses the same way in ``MaybeDecrTo``
            # (``tracker/progress.go:230``: ``if rejected <= pr.Match``),
            # commenting that "rejections can happen spuriously as messages
            # are sent out of order or duplicated". Its ``rejected`` is the
            # ``prev_log_index`` of the refused append, which this reply does
            # not carry; the conflict hint is used instead, and the two ask
            # subtly different questions.
            #
            # The substitution is sound because within one leadership a peer
            # that acknowledged ``match_index`` holds every index up to it,
            # identical to this leader's, by Log Matching -- so a genuine
            # conflict must lie above it. ``_become_leader`` resets
            # ``match_index``, so nothing is carried across terms.
            #
            # It was **not** sound before the fence above, and the difference
            # is worth keeping in view: a phantom ``match_index`` left by a
            # previous incarnation's reply made a rejoining member's honest
            # "resume from index 1" look stale, and the leader ignored it for
            # the rest of the term. Measured, as a member that never caught up
            # and a caller never answered.
            return

        if not message.success:
            # Resume from the start of the conflicting term rather than one
            # index back, so a far-behind member costs a handful of exchanges
            # instead of one per entry.
            #
            # No ``match_index + 1`` floor, unlike etcd's
            # ``max(min(rejected, matchHint+1), pr.Match+1)``
            # (``tracker/progress.go:249``). etcd needs one because `rejected`
            # and `matchHint` are two different quantities and the smaller can
            # fall below `Match`. Here there is one, and the guard above has
            # already returned unless ``conflict_index > match_index`` -- so a
            # floor could never be the larger term, and adding it would be a
            # line that looks load-bearing and is not.
            state.next_index = max(1, message.conflict_index)
            # The window just moved backwards, so anything recorded as told to
            # this peer above its new end was told through a message it
            # rejected. ``go.etcd.io/raft`` clamps the same way whenever
            # ``Next`` regresses (``tracker/progress.go:142``, ``:238``,
            # ``:251``), commenting that the sent commit "unlikely has been
            # applied".
            state.sent_commit = min(state.sent_commit, state.next_index - 1)
            self._send_append(peer, state)
            return

        # **Both indices only ever move forward within a leadership.**
        #
        # A follower vouches for ``prev_log_index + len(entries)`` -- the window
        # of the message it is answering. An entries-less send therefore draws a
        # reply vouching for ``prev_log_index`` alone, which is *less* than a
        # preceding append's reply vouched for. Taking that as news walks this
        # peer's position backwards and makes the leader re-send entries it
        # already holds. Measured before this guard: 15% of all replies on an
        # idle in-memory cluster, 24% over a slow link.
        #
        # ``go.etcd.io/raft`` has the invariant in one place, ``MaybeUpdate``
        # (``tracker/progress.go:205``), and gates its whole success branch on
        # it:
        #
        #     if n <= pr.Match { return false }
        #     pr.Match = n
        #     pr.Next = max(pr.Next, n+1)   // invariant: Match < Next
        #
        advanced = message.match_index > state.match_index
        if advanced:
            state.match_index = message.match_index
        # ``Match < Next``, which etcd states as an invariant on the same line
        # it advances them (``tracker/progress.go:211``). Enforced on every
        # success rather than only on an advance, because the two can be driven
        # apart by different messages: a rejection lowers ``next_index`` alone,
        # and a success can then leave ``match_index`` above it. A leader in
        # that state anchors its next append *below* what the peer has already
        # acknowledged, and if that anchor is under the peer's commit index the
        # peer answers without taking the entries -- so neither side moves and
        # the pair spin until the term ends. Measured as exactly that: a leader
        # at ``next=1 match=2`` re-sending ``prev=0`` forever.
        state.next_index = max(state.next_index, state.match_index + 1)

        # Above the guard below, deliberately. Promotion is decided by what the
        # peer says about *itself* together with where it has got to, and both
        # are known whether or not this particular reply moved anything. A
        # member that is caught up and simply repeating its position would
        # otherwise never be promoted, and a rejoining member's caller waits
        # forever -- measured exactly that way.
        if state.catching_up and state.match_index >= state.promote_through:
            state.catching_up = False
            self._transport.send(peer, Promote(
                term=self._term, leader=self._layout.local.index,
                through_index=state.promote_through,
            ))
            log.info("raft: member %d promoted", peer)

        if not advanced and not answered_our_append:
            # Told nothing new, and not the answer to an outstanding append, so
            # there is no replication decision to make. etcd stops here too:
            # its success branch runs only when ``MaybeUpdate`` moved
            # something, or when the reply releases a probing peer
            # (``raft.go:1528``). That second clause is why a non-advancing
            # reply which *did* clear our pause still falls through -- it
            # releases flow control, and entries may be waiting behind it.
            return

        before = self._commit_index
        self._advance_commit()
        if self._commit_index == before and self._should_send_now(state):
            # ``_advance_commit`` replicates to everyone when the commit index
            # moves. When it does not -- which is every reply from a peer
            # outside the quorum position -- this peer is left behind until the
            # next tick, and a caller waiting on it waits a whole heartbeat.
            self._send_append(peer, state)

    def _should_send_now(self, state: _PeerState) -> bool:
        """Does this peer need an append now, rather than at the next tick?

        Two reasons, both taken from ``go.etcd.io/raft``'s ``MsgAppResp``
        handling (``raft.go:1550-1571``), which does exactly this and says why:

        * **it has entries waiting** -- the reply just cleared its flow-control
          pause, and this leader already holds what it is missing;
        * **its commit index is behind** what this leader has committed, and it
          has not been told. This is etcd's ``CanBumpCommit``
          (``tracker/progress.go:189``): ``index > sentCommit and sentCommit <
          Next-1``. The first half avoids repeating a commit index the peer
          already has; the second avoids sending one it could not act on.

        The second is the case that matters most and the one this was missing.
        The commit index moves on the *quorum position*, so a reply from any
        peer outside it advances nothing -- and that peer, though caught up on
        entries, is never told the new commit index until a heartbeat fires.
        etcd's comment names the consequence exactly: "this is not strictly
        necessary because the periodic heartbeat messages deliver commit
        indices too. However, a message sent now may arrive earlier than the
        next heartbeat fires."

        Measured here as a registration driven at such a peer waiting one
        heartbeat interval: until its commit index moves it cannot apply, and
        until it applies the caller is not answered. At five members with the
        default 50 ms heartbeat that was a p90 of 39.6 ms against a round trip
        of under 2 ms.

        Gated on the pause, as etcd's ``maybeSendAppend`` is by ``IsPaused``
        (``raft.go:620``): a peer with an append already in flight will learn
        everything from that exchange.

        Neither condition can loop. A successful reply strictly advances
        ``next_index``, which is bounded by ``last_index``, and ``sent_commit``
        is monotonic within a leadership -- so both stop being true.
        """
        if state.pending_request != 0:
            return False
        has_entries = state.next_index <= self._log.last_index
        can_bump_commit = (
            self._commit_index > state.sent_commit
            and state.sent_commit < state.next_index - 1
        )
        return has_entries or can_bump_commit

    def on_promote(self, peer: int, message: Promote) -> None:
        if message.term < self._term:
            return
        if not self._voting:
            log.info(
                "raft: %s promoted by member %d through index %d",
                self._layout.local.name, peer, message.through_index,
            )
        self._voting = True

    def _advance_commit(self) -> None:
        """Raft §5.4.2: commit an index only once it is replicated and current.

        Two conditions, and the second is the one that is easy to drop:

        * a majority must hold the index;
        * the entry at that index must be from the **current** term. Committing
          an earlier term's entry on a count alone is the classic Raft bug --
          an entry can be present on a majority and still be overwritten by a
          future leader, because presence is not commitment.

        Members still catching up are excluded from the count. Their logs may
        be incomplete, and an acknowledgement from an incomplete log is not
        evidence the entry is safe.

        Advancing publishes immediately
        -------------------------------
        A follower learns the commit index only from ``leader_commit`` on an
        ``AppendEntries``, and a registration driven at a follower does not
        resolve until *that* member applies. Leaving the new index to ride the
        next heartbeat therefore puts a whole heartbeat interval on the
        critical path of every mutation that did not happen to arrive at the
        leader -- measured at 45.6 ms p50 against a 50 ms heartbeat, where the
        leader had committed in about 1 ms.

        So an advance replicates at once. It cannot loop: the extra round is
        empty of entries, the replies it draws leave ``candidate <=
        self._commit_index``, and the guard above returns without sending
        anything further.
        """
        counted = sorted(
            (
                state.match_index
                for state in self._peers.values() if not state.catching_up
            ),
            reverse=True,
        )
        # This member holds everything it has appended, hence the leading own
        # index; ``quorum - 1`` peers must match it.
        needed = self._layout.quorum - 1
        if len(counted) < needed:
            return
        candidate = (
            self._log.last_index if needed == 0 else counted[needed - 1]
        )

        if candidate <= self._commit_index:
            return
        try:
            if self._log.term_at(candidate) != self._term:
                return
        except RaftLogCompacted:
            return

        self._commit_index = candidate
        self._schedule_apply()
        self._replicate()

    # -- applying --------------------------------------------------------

    def _schedule_apply(self) -> None:
        """Ask the applier to catch up. Cheap, idempotent, and never spawns.

        A task per commit-index advance would be simpler to write and wrong in
        two ways: the tasks are unowned, so shutdown cannot wait for them, and
        several could interleave mid-catch-up. One long-lived applier woken by
        an event has neither problem, and an advance that arrives while it is
        already draining is picked up by the same pass.
        """
        self._apply_wake.set()

    def _check_applied_within_committed(self) -> None:
        """The two invariants ``go.etcd.io/raft`` asserts about one member.

        Transcribed from the source rather than from memory: ``log.go:48``
        states ``applied <= committed`` outright, ``log.go:332-334`` panics in
        ``appliedTo`` when ``committed < i``, and ``log.go:322-330`` panics in
        ``commitTo`` when ``lastIndex() < tocommit`` -- "Was the raft log
        corrupted, truncated, or lost?".

        Checked once per wake-up rather than per entry: two comparisons against
        the cost of a batch of applies is not worth measuring, and every path
        that could break either one runs between wake-ups.

        This is a **bug detector**, and deliberately not a safeguard against
        anything else. Nothing a peer sends can reach these numbers except
        through logic in this file, and nothing applied survives a restart, so
        a violation means a defect here. The one that prompted it applied an
        uncommitted tail because the apply batch was bounded by size and not by
        the commit index, and it went unnoticed for as long as it did precisely
        because nothing ever looked.
        """
        applied = self._machine.last_applied
        if applied > self._commit_index:
            raise RaftInvariantViolated(
                f"applied through {applied} but committed only through "
                f"{self._commit_index}",
            )
        reachable = max(self._log.last_index, self._log.snapshot_index)
        if self._commit_index > reachable:
            raise RaftInvariantViolated(
                f"committed through {self._commit_index} but holds only "
                f"[{self._log.first_index}..{self._log.last_index}] with "
                f"snapshot {self._log.snapshot_index}",
            )

    async def _apply_forever(self) -> None:
        while not self._closing:
            await self._apply_wake.wait()
            self._apply_wake.clear()
            try:
                await self._apply_committed()
            except asyncio.CancelledError:
                raise
            except RaftInvariantViolated:
                # Past the catch-all below, deliberately. That handler exists
                # so one bad apply cannot kill a member, and it is right for
                # everything transient -- but an invariant that is broken stays
                # broken, and logging it once per wake-up would be a silent
                # failure wearing the costume of a handled one.
                raise
            except Exception:
                log.exception("raft: applying committed entries failed")

    async def _apply_committed(self) -> None:
        """Apply up to the commit index, in bounded runs.

        The ``sleep(0)`` is between runs, never inside one: ``machine.apply``
        must not be interrupted mid-mutation, and the loop must not hold the
        event loop for a whole catch-up.
        """
        self._check_applied_within_committed()
        while self._machine.last_applied < self._commit_index:
            start = self._machine.last_applied + 1
            # Bounded by the commit index, not merely by the batch size.
            #
            # `slice` clamps to `last_index`, which is the *log*, and a
            # follower's log routinely runs ahead of what is committed -- that
            # is what replication looks like in flight. Asking for
            # `max_apply_batch` entries from `start` therefore applied
            # uncommitted ones whenever the tail was longer than the gap, which
            # breaks Raft in two ways at once:
            #
            #   * the state machine reflects operations that may never commit;
            #   * `last_applied` advances past them, so when a new leader
            #     overwrites those indices the applier -- which resumes at
            #     `last_applied + 1` -- never applies the entries that replaced
            #     them. The member is then permanently wrong at those indices
            #     and no later message repairs it.
            #
            # Observed as "index 72 applied as term 14 by one member and term
            # 12 by member 1" some four hundred steps after the fact, which is
            # why `go.etcd.io/raft` asserts the invariant at the moment it
            # would break instead: `log.go:332-334`, `appliedTo` panics when
            # `committed < i`, and `log.go:48` states it outright as
            # `applied <= committed`.
            wanted = min(
                self._timing.max_apply_batch, self._commit_index - start + 1,
            )
            try:
                entries = self._log.slice(start, wanted)
            except RaftLogCompacted:
                return
            if not entries:
                return
            outcomes = self._machine.apply(entries)
            self._resolve(outcomes)
            # After the mutations and their grains, never before: a waiter
            # released early would observe a half-applied run, which is the
            # bug the fence exists to prevent.
            await self._fence.advance(self._machine.last_applied)
            if self._machine.last_applied < self._commit_index:
                await asyncio.sleep(0)
        await self._maybe_compact()

    def _resolve(self, outcomes: dict[ProposalId, Outcome]) -> None:
        for proposal, outcome in outcomes.items():
            future = self._waiters.pop(proposal, None)
            if future is not None and not future.done():
                future.set_result(outcome)

    # -- proposing -------------------------------------------------------

    def propose(self, operation: RegistryOperation) -> asyncio.Future[Outcome]:
        """Submit an operation. Synchronous; returns a future.

        Not a coroutine, deliberately: the caller's interest is registered
        before anything can await, so the entry cannot commit and apply in the
        window between being accepted and having a waiter.
        """
        return self._batcher.submit(operation)

    def _drain(self, batch: list[Pending[RegistryOperation, Outcome]]) -> None:
        """Route one tick's proposals, as leader or as follower."""
        if self._role is Role.LEADER:
            operations = []
            for item in batch:
                proposal = self._next_proposal()
                operations.append(_rebind(item.operation, proposal))
                self._waiters[proposal] = item.future
            self._append_local(operations)
            self._replicate()
            # A single-member cluster has no peers to hear from, so nothing
            # would ever advance the commit index for it.
            if not self._peers:
                self._advance_commit()
            return

        leader = self._leader
        if leader is None:
            for item in batch:
                if not item.future.done():
                    item.future.set_exception(
                        RaftUnavailable("no leader elected"),
                    )
            return

        payloads = []
        for item in batch:
            proposal = self._next_proposal()
            operation = _rebind(item.operation, proposal)
            self._waiters[proposal] = item.future
            payloads.append(encode_operation(operation))
        self._transport.send(
            leader, Propose(proposals=tuple(payloads), request_id=0),
        )

    async def on_propose(self, peer: int, message: Propose) -> ProposeReply:
        if self._role is not Role.LEADER:
            return ProposeReply(
                accepted=False, reason="not the leader", term=self._term,
                first_index=0, request_id=message.request_id,
                leader=self._leader,
            )
        operations = [decode_operation(raw) for raw in message.proposals]
        if not operations:
            return ProposeReply(
                accepted=True, reason="", term=self._term, first_index=0,
                request_id=message.request_id, leader=self._layout.local.index,
            )
        first, _last = self._append_local(operations)
        self._replicate()
        if not self._peers:
            self._advance_commit()
        return ProposeReply(
            accepted=True, reason="", term=self._term, first_index=first,
            request_id=message.request_id, leader=self._layout.local.index,
        )

    # -- transport callbacks ---------------------------------------------

    def on_peer_state(self, peer: int, *, up: bool, incarnation: int) -> None:
        state = self._peers.get(peer)
        if state is None:
            return
        state.up = up
        if up:
            if state.incarnation and incarnation != state.incarnation:
                log.info(
                    "raft: member %d restarted (incarnation %d -> %d)",
                    peer, state.incarnation, incarnation,
                )
            state.incarnation = incarnation
            if self._role is Role.LEADER:
                # **Everything known about this peer's log is discarded, and
                # that is a deliberate divergence from ``go.etcd.io/raft``.**
                #
                # etcd keeps ``Match`` across unreachability -- ``MsgUnreachable``
                # (``raft.go:1629``) only moves the peer to probing, and
                # ``BecomeProbe`` sets ``Next = Match + 1`` -- because in etcd a
                # log is durable, so a peer that comes back still holds what it
                # acknowledged. Its premise does not hold here: this log lives
                # in memory, which is the whole reason the non-voting rejoin
                # exists, so a reconnect genuinely can mean the peer has
                # nothing.
                #
                # Keeping ``match_index`` on that assumption is not merely
                # optimistic, it deadlocks. A rejoining member rejects from
                # index 1, the staleness test in ``on_append_entries_reply``
                # reads that as a rejection below what it already acknowledged,
                # and the leader ignores it for as long as it holds the term --
                # measured directly, as a rejoining member that never caught up
                # and a caller that was never answered.
                #
                # The cost of being conservative is a transient one: until this
                # peer's next successful reply it contributes nothing to the
                # commit count, so a connection flap can defer a commit by a
                # round trip. That is the right trade against losing a member.
                state.match_index = 0
                state.next_index = self._log.last_index + 1
                # Everything in flight to the incarnation that has gone is now
                # disowned; a reply it already sent must not be read as news
                # about the one that replaced it.
                state.reply_floor = self._append_sequence
                # A reconnect invalidates anything that was in flight: neither
                # the chunk nor the append it was waiting on will ever be
                # answered, and holding the pause open would strand the peer.
                state.snapshot_in_flight = False
                state.pending_request = 0
                state.snapshot_offset = 0
                state.sent_commit = 0
                self._send_append(peer, state)
        else:
            state.catching_up = False

    # -- compaction ------------------------------------------------------

    async def _maybe_compact(self) -> None:
        """Take a snapshot and drop the entries it covers.

        A log that is never compacted grows for the life of the cluster, and
        every member holds all of it in memory. Compaction is therefore not an
        optimisation here; it is what makes an in-memory log viable at all.

        Two decisions, not one, and they take different answers:

        * **what the snapshot describes** -- always ``last_applied``, because
          the payload is serialised from the live store and that is the state
          it holds. This is not a choice;
        * **how much of the log may be discarded** -- bounded by the slowest
          *reachable* follower's ``match_index``, because discarding an entry a
          follower has not yet received strands it on replication and forces a
          whole snapshot transfer instead.

        They were one quantity until a chaos run showed what that costs; the
        body says what happened.

        ``max_log_entries`` overrides the second, deliberately. One unreachable
        member must not be able to make the log grow without bound -- a partial
        outage turning into an out-of-memory failure is a worse outcome than
        that member needing a snapshot when it returns.
        """
        if self._snapshots is None:
            return
        applied = self._machine.last_applied
        held = applied - self._log.first_index + 1
        if held < self._timing.compaction_threshold:
            return
        if self._snapshots.capture is not None:
            return

        # These are two different quantities and conflating them was a bug.
        #
        # A snapshot's payload is serialised from the *live store*, so it
        # describes the state at ``last_applied`` -- whatever index is written
        # on it. Labelling it with the slowest follower's ``match_index``
        # therefore produces a snapshot that claims an index its contents have
        # already moved past, and the copy-on-write pinning cannot rescue it:
        # that mechanism photographs a record *before* apply mutates it, so it
        # covers changes made while the capture is open and can do nothing
        # about ones made before it opened.
        #
        # Measured, on the run that found this. The leader captured with
        # ``index=11`` while its machine was at ``applied=12``, pinning zero
        # pre-images because nothing changed during the walk -- and the
        # resulting payload was byte-identical to a peer's snapshot labelled
        # 12. A follower installing it set ``last_applied = 11`` over a store
        # that already held entry 12's registration, replayed 12, computed
        # ``creates=False`` where the proposer had said ``True``, and raised
        # ``DivergenceDetected``. That member then stops applying for good:
        # committed at 14, applied stuck at 11, a private view of the registry
        # that no amount of further replication repairs.
        #
        # So the snapshot is labelled ``applied``, always, because that is what
        # it contains. The ``min(matchIndex)`` bound keeps its real job --
        # deciding how much of the log may be *discarded* -- where discarding
        # an entry a follower has not yet received is what strands it.
        if applied <= self._log.snapshot_index:
            return

        discard_to = applied
        if self._role is Role.LEADER and held < self._timing.max_log_entries:
            confirmed = [
                state.match_index for state in self._peers.values() if state.up
            ]
            if confirmed:
                discard_to = min(applied, min(confirmed))

        try:
            term = self._log.term_at(applied)
        except RaftLogCompacted:
            return

        capture = self._snapshots.begin(
            index=applied, term=term, ownership=self._machine.ownership,
        )
        try:
            payload = await self._snapshots.finish(capture)
        except Exception:
            self._snapshots.abandon()
            log.exception("raft: taking a snapshot failed")
            return

        self._snapshot = payload
        self._snapshot_meta = SnapshotMeta(
            last_index=applied, last_term=term, resources=0,
        )

        # Discarding is now the separate, bounded step. The snapshot covers at
        # least as much as this drops, so a follower too far behind to be
        # served from the log is still served from the snapshot, and one that
        # is merely a little behind keeps being served entries.
        if discard_to <= self._log.snapshot_index:
            log.debug(
                "raft: %s snapshotted through index %d but discarded nothing; "
                "a follower is still behind at %d",
                self._layout.local.name, applied, discard_to,
            )
            return
        try:
            discard_term = self._log.term_at(discard_to)
        except RaftLogCompacted:
            return
        freed = self._log.discard_through(discard_to, discard_term)
        log.info(
            "raft: %s snapshotted through index %d and compacted through %d, "
            "freeing %d entries",
            self._layout.local.name, applied, discard_to, freed,
        )

    # -- snapshot transfer -----------------------------------------------

    def _send_snapshot(self, peer: int, state: _PeerState) -> None:
        """Send the next chunk of this member's snapshot to a stranded peer."""
        meta = self._snapshot_meta
        if meta is None or not self._snapshot:
            # Nothing to send yet. The peer stays behind until the next
            # compaction produces one, which is correct: there is no state to
            # hand it that it does not already have.
            return

        if state.snapshot_in_flight:
            # One chunk at a time. See ``_PeerState.snapshot_in_flight``.
            return

        offset = state.snapshot_offset
        chunk = self._snapshot[offset:offset + self._timing.snapshot_chunk]
        done = offset + len(chunk) >= len(self._snapshot)
        state.snapshot_in_flight = True
        self._transport.send(
            peer,
            InstallSnapshot(
                term=self._term, leader=self._layout.local.index,
                last_index=meta.last_index, last_term=meta.last_term,
                offset=offset, data=bytes(chunk), done=done,
                ownership=b"",
            ),
            # BULK, so a multi-megabyte transfer cannot head-of-line-block the
            # heartbeats that keep this member's leadership alive.
            stream=Stream.BULK,
        )

    def on_install_snapshot(
        self, peer: int, message: InstallSnapshot,
    ) -> InstallSnapshotReply:
        """Accumulate a snapshot from the leader, and install it when complete.

        Chunks are accumulated rather than applied incrementally: a partially
        installed snapshot is a registry describing a state that never existed,
        and the whole point of this path is that the member using it cannot
        tell the difference on its own.
        """
        if message.term < self._term:
            return InstallSnapshotReply(
                term=self._term, bytes_received=0, done=False,
            )
        if message.term > self._term:
            self._step_down(message.term)
        self._role = Role.FOLLOWER
        self._leader = message.leader
        self._reset_election_timer()

        buffer = self._installing.get(peer)
        if message.offset == 0 or buffer is None:
            buffer = bytearray()
            self._installing[peer] = buffer
        if message.offset != len(buffer):
            # A chunk out of order, or a retransmission from a different
            # offset. Restart rather than splice: a snapshot assembled from
            # mismatched pieces would parse and be wrong.
            self._installing[peer] = bytearray()
            return InstallSnapshotReply(
                term=self._term, bytes_received=0, done=False,
            )
        buffer += message.data

        if not message.done:
            return InstallSnapshotReply(
                term=self._term, bytes_received=len(buffer), done=False,
            )

        try:
            meta, ownership, records = decode_snapshot(bytes(buffer))
            store = install(
                records,
                gc_interval=self._machine.store_intervals[0],
                forget_interval=self._machine.store_intervals[1],
            )
        except Exception:
            log.exception("raft: refusing a snapshot that did not install")
            self._installing.pop(peer, None)
            return InstallSnapshotReply(
                term=self._term, bytes_received=0, done=False,
            )

        impossible = self._why_the_snapshot_cannot_be_real(meta, message.term)
        if impossible is not None:
            # openraft's issue-1892 lesson, in their words: "rejects
            # protocol-impossible input early, instead of corrupting its
            # state". A correct leader cannot produce these, because it builds
            # a snapshot from its own committed state -- so seeing one means a
            # peer is wrong, and the only safe answer is to keep our own state
            # rather than adopt theirs.
            #
            # Installing it anyway is worse than it sounds: the log would take
            # the snapshot's term as its own, and a member whose last log term
            # is above its current term considers itself impossibly up to date.
            # It would then refuse every vote and win any election it entered.
            log.error(
                "raft: refusing a snapshot from member %d: %s",
                peer, impossible,
            )
            self._installing.pop(peer, None)
            return InstallSnapshotReply(
                term=self._term, bytes_received=0, done=False,
            )

        self._machine.install_snapshot(store, ownership, meta.last_index)
        self._log.reset_to_snapshot(meta.last_index, meta.last_term)
        # The fence jumps rather than advances: everything through this index
        # is now visible, however little of it arrived as entries.
        asyncio.get_running_loop().create_task(
            self._fence.reset(meta.last_index),
        )
        self._commit_index = max(self._commit_index, meta.last_index)
        self._installing.pop(peer, None)
        log.info(
            "raft: %s installed a snapshot through index %d (%d resources)",
            self._layout.local.name, meta.last_index, meta.resources,
        )
        return InstallSnapshotReply(
            term=self._term, bytes_received=len(buffer), done=True,
        )

    def _why_the_snapshot_cannot_be_real(
        self, meta: SnapshotMeta, sender_term: int,
    ) -> str | None:
        """Is this snapshot's metadata possible at all? ``None`` if it is.

        Two checks, both about metadata rather than content -- the content
        already had to decode and install before we got here.

        A snapshot cannot describe a term above the one its sender holds: the
        sender built it from entries it had committed, and it cannot have
        committed an entry from a term it has not reached.

        Nor can it move this member's snapshot boundary *backwards*. Everything
        below the boundary is already applied, so accepting an older snapshot
        would un-apply committed state -- the one thing a state machine may
        never do.
        """
        if meta.last_term > sender_term:
            return (
                f"it covers term {meta.last_term} but arrived from a member "
                f"at term {sender_term}"
            )
        if meta.last_index <= self._commit_index:
            # Against the **commit index**, not the compaction boundary.
            # ``snapshot_index <= commit_index`` always, so comparing against
            # the boundary let through every snapshot landing in between --
            # and installing one of those replaces the state machine with older
            # state while ``_commit_index`` correctly stays put, leaving
            # committed entries un-applied. That is the one thing a state
            # machine may never do.
            #
            # ``go.etcd.io/raft`` refuses on exactly this line
            # (``raft.go:1861``): ``if s.Metadata.Index <= r.raftLog.committed
            # { return false }``.
            return (
                f"it ends at index {meta.last_index}, at or below what is "
                f"already committed here ({self._commit_index})"
            )
        return None

    def on_install_snapshot_reply(
        self, peer: int, message: InstallSnapshotReply,
    ) -> None:
        if message.term > self._term:
            self._step_down(message.term)
            return
        if self._role is not Role.LEADER:
            return
        state = self._peers.get(peer)
        meta = self._snapshot_meta
        if state is None or meta is None:
            return

        # A peer working through a transfer is answering, and must count toward
        # check-quorum exactly as an append reply does. In ``go.etcd.io/raft``
        # the snapshot acknowledgement arrives as an ordinary ``MsgAppResp``,
        # so it sets ``RecentActive`` on the same line.
        state.last_heard_at = asyncio.get_running_loop().time()

        state.snapshot_in_flight = False

        if message.done:
            state.next_index = meta.last_index + 1
            state.match_index = meta.last_index
            state.snapshot_offset = 0
            self._send_append(peer, state)
            return

        # ``bytes_received`` is how much the follower has assembled, so it is
        # both the acknowledgement and the offset to resume from -- including
        # zero, which is the follower saying it threw the transfer away.
        state.snapshot_offset = message.bytes_received
        self._send_snapshot(peer, state)

    async def on_forward(self, peer: int, message: Any) -> Any:
        """Hand a forwarded mutation to the backend, or refuse it."""
        from nmos.raft.messages import ForwardReply

        if self._forwarder is None:
            return ForwardReply(
                ok=False, created=False, error="unavailable",
                detail="this member is not serving registrations",
                applied_index=0, not_owner=False,
                request_id=message.request_id, owner=None,
            )
        reply: Any = await self._forwarder(message)
        return reply

    # -- waiting ---------------------------------------------------------

    async def wait_for_leader(self, *, timeout: float) -> int:
        """Block until a leader is known. Raises when none appears in time."""
        deadline = asyncio.get_running_loop().time() + timeout
        while self._leader is None:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise RaftUnavailable("no leader was elected within the deadline")
            await asyncio.sleep(min(self._timing.heartbeat, remaining))
        return self._leader


def _rebind(
    operation: RegistryOperation, proposal: ProposalId,
) -> RegistryOperation:
    """Stamp a proposal id onto an operation built without one.

    Callers construct operations without knowing where in the sequence they
    will land; the id is assigned at drain time, when the order is decided.
    """
    import dataclasses

    return dataclasses.replace(operation, proposal=proposal)
