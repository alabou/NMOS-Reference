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
been caught up and explicitly promoted. While non-voting its vote does not
count -- it still answers, saying ``voting=False``, and a candidate counts such
a grant only where its own round proves that no quorum of voters can exist
(see "When every voter has forgotten") -- and it reports ``catching_up`` so the
leader does not count its acknowledgements toward a commit.

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
    RaftCursorReservationFailed,
    RaftInvariantViolated,
    RaftLogCompacted,
    RaftProtocolError,
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
    ReadIndex,
    ReadIndexReply,
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
from nmos.raft.snapshot import (
    SnapshotAbandoned,
    SnapshotMeta,
    SnapshotStore,
    decode_snapshot,
    install,
)
from nmos.raft.transport import Transport
from nmos.raft.wire import Stream
from nmos.registry.fence import RevisionFence
from nmos.registry.types import ResourceType, TaiCursor

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

    heard_request: int = 0
    """The highest append this peer has answered in this leadership.

    What a read waits for (``RaftNode._confirm_reads``): a reply, in this
    term, to an append sent after the read was recorded is this peer saying
    the leader still leads -- etcd's heartbeat context, echoed back
    (``read_only.go``, ``recvAck``), with the append sequence as the position.
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

    sending: tuple[SnapshotMeta, bytes] | None = None
    """The snapshot this peer's transfer is *of*, pinned when it starts.

    An offset means something only relative to one byte sequence, and
    compaction replaces this member's snapshot whenever it likes. Slicing the
    current one at a saved offset spliced the head of one snapshot to the tail
    of the next -- the chaos soak's splice detector measured it, and when the
    result happened to decode it installed: replicas that had lost
    acknowledged writes while reporting themselves caught up. Pinned here,
    every chunk and the completion's credit come from the one snapshot the
    transfer began with, as etcd's server streams one point-in-time snapshot
    per transfer (``server/etcdserver/snapshot_merge.go:36-39``). Released
    when the transfer completes or is abandoned; ``bytes`` is immutable, so a
    pin shares the payload rather than copying it.
    """

    snapshot_request: int = 0
    """The id of the chunk out and unanswered, or 0 when none is.

    One chunk at a time, and only its reply drives the transfer. Without the
    first, the transfer was driven from two places at once -- the replication
    tick and the previous chunk's reply -- so two chunks went out carrying the
    same offset and the pair looped for ever. A flag alone was not enough for
    the second: a reconnect is reported for the CONTROL connection while
    chunks travel on BULK, so resetting on a reconnect cleared the flag while
    the last chunk was still in flight *and still answered* -- and that
    answer, taken as the one awaited, started a second stream beside the new
    one (seed 60195). The id says which reply is awaited; ``reply_floor``
    fences everything sent before a reset.
    """

    snapshot_sent_at: float = 0.0
    """When the chunk in flight was sent.

    Overdue matters as much as outstanding: a chunk lost with its BULK
    connection draws no answer, and nothing else ever clears it while CONTROL
    stays up. ``election_min`` is the backstop, as for appends.
    """


@dataclass
class _Assembly:
    """A snapshot being received: which one, and the bytes of it so far.

    Reassembling by offset alone accepted the next chunk of *any* snapshot the
    sender happened to be slicing -- the head of one and the tail of another,
    joined because the offsets lined up. The identity is what makes an offset
    mean something: a chunk continues this assembly only if it belongs to the
    same transfer.
    """

    identity: tuple[int, int, int, int]
    """``(term, leader, last_index, last_term)`` of the chunk that began it."""

    data: bytearray = field(default_factory=bytearray)


@dataclass
class _Read:
    """A read waiting for a quorum to confirm that this member still leads.

    etcd's ``readIndexRequest`` (``read_only.go``). ``index`` is the commit
    index it reads at, unknown until this leader has committed an entry of
    its own term; ``after`` the append sequence then, since only a reply to
    a later append is evidence gathered after the read began.
    """

    future: asyncio.Future[int]
    index: int | None = None
    after: int = 0


def _holds(assembled: bytearray, offset: int, chunk: bytes) -> bool:
    """Is ``chunk``, at ``offset``, already part of ``assembled``?

    Decided by the bytes, not the offset: a copy is the same bytes at the same
    place. Anything else at an offset already passed is no copy, and keeping
    the buffer for it would be a splice.
    """
    end = offset + len(chunk)
    return end <= len(assembled) and assembled[offset:end] == chunk


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
        # Before anything can allocate: the previous incarnation may have
        # handed out any cursor up to this, and this one must start above it.
        machine.cursors.resume(state.cursor_reservation)

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

        self._votes: set[int] = set()
        self._pre_votes: set[int] = set()
        self._pre_refusals: set[int] = set()
        # Who has forgotten, as the *current* round has proved it: peers whose
        # reply to this candidacy said ``voting=False`` -- see "When every voter
        # has forgotten" below. Reset with the votes, never carried from one
        # round to the next. A peer that has not replied is absent and so counts
        # as a voter, which is what keeps a partition from looking like a
        # cluster that has forgotten everything.
        self._forgotten: set[int] = set()
        self._pre_forgotten: set[int] = set()
        # Correlates an append with its reply, so the flow-control pause
        # in ``_send_append`` releases on the right answer.
        self._append_sequence = 0
        self._waiters: dict[ProposalId, asyncio.Future[Outcome]] = {}
        self._reads: list[_Read] = []
        """Reads waiting for a quorum to confirm this leadership (``read_index``)."""
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
        self._installing: dict[int, _Assembly] = {}

        # How far this member has applied, for callers that must not answer
        # until a particular index is visible here. Reused verbatim from the
        # etcd backend -- it is generic over a monotonic integer, and the raft
        # log index is one.
        self._fence = RevisionFence(applied=0)
        self._forwarder: Any | None = None
        self._closing = False
        self._leader_changed = asyncio.Event()
        # The broken invariant this member stopped on, once it has (``_fail``),
        # and the signal its owner waits on to end the process.
        self._failure: RaftInvariantViolated | None = None
        self._failed = asyncio.Event()
        # The one close in progress, which every caller awaits (``close``).
        self._closer: asyncio.Task[None] | None = None

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
    def failure(self) -> RaftInvariantViolated | None:
        """The broken invariant this member stopped on, or ``None`` (``_fail``)."""
        return self._failure

    async def wait_for_failure(self) -> RaftInvariantViolated:
        """Return once this member has stopped itself on a broken invariant.

        For the process that owns it, which must then exit: the member takes no
        further part, and only a restart -- which brings it back with nothing,
        to be caught up as a non-voting learner -- makes it whole again
        (``RaftInvariantViolated``). ``nmos_registry.py`` runs this in its task
        group, so the failure ends the process with status 1.
        """
        while self._failure is None:
            await self._failed.wait()
        return self._failure

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
        """The allocator, for observing and for diagnostics.

        Not for allocating: ``allocate_cursor`` is, because it makes the
        reservation durable before the cursor leaves this member. A cursor taken
        from here directly is one a restart can hand out again.
        """
        return self._machine.cursors

    def allocate_cursor(self, resource_type: ResourceType) -> TaiCursor:
        """The next paging cursor for ``resource_type``, reserved durably.

        About once per ``RESERVATION_WINDOW_SECONDS`` of cursor progress the
        cursor lies beyond the reservation on disk, and a new bound is written
        -- synchronously, beside the term and vote, for the reason ``persist.py``
        gives -- before the cursor is returned. Every other call is the
        allocator's arithmetic alone.

        Raises:
            RaftCursorReservationFailed: The bound could not be written. The
                cursor is not returned, so nothing that could repeat it after a
                restart has left this member; the allocator simply moves past
                it.
        """
        cursors = self._machine.cursors
        cursor = cursors.allocate(resource_type)
        needed = cursors.reservation_needed(cursor)
        if needed is not None:
            try:
                self._terms.save(PersistentState(
                    term=self._term, voted_for=self._voted_for,
                    incarnation=self._incarnation, cursor_reservation=needed,
                ))
            except OSError as exc:
                raise RaftCursorReservationFailed(
                    f"could not reserve paging cursors up to {needed} in "
                    f"{self._terms.path}: {exc}",
                ) from exc
            cursors.confirm_reservation(needed)
        return cursor

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
        """Stop, releasing every waiter.

        Every call awaits the same close. A member that stops itself (``_fail``)
        begins closing on its own, and its owner closes it again on the way out;
        two closes running at once would each tear down the transport while the
        other was still iterating its links. Shielded, so a caller cancelled
        while waiting leaves the close to finish rather than half done.
        """
        if self._closer is None:
            self._closer = asyncio.get_running_loop().create_task(
                self._close(), name=f"raft-close-{self._layout.local.name}",
            )
        await asyncio.shield(self._closer)

    async def _close(self) -> None:
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
        self._fail_reads("member is shutting down")
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
        # **No snapshot gate here, deliberately.** One stood here -- "absorbing
        # a snapshot is not a moment to campaign" -- modelled on etcd's
        # ``promotable()``, which refuses while ``hasNextOrInProgressSnapshot()``
        # (``raft.go:1946-1949``). But that is a *complete* snapshot pending
        # application (``log.go:287-291``: ``unstable.snapshot != nil``, set only
        # by ``restore``); etcd has no partial transfers to gate on, and this
        # member installs a completed one synchronously, so etcd's state never
        # exists here. What the gate actually tested was a *partial* buffer --
        # and every chunk and every keepalive from a live leader resets the
        # timer, so by the time the gate was reached the transfer had stopped.
        # It could only ever block a campaign the silence called for: the chaos
        # soak measured clusters with no leader because the one voter able to
        # lead held an abandoned buffer and never campaigned. A far-behind
        # member's pre-vote simply fails, which changes nothing.
        #
        # Pre-Vote first, always -- and for a member that has forgotten too.
        # Winning the real election is the *only* thing a term increment buys,
        # so asking first costs one round trip and saves every disruption a
        # doomed campaign would cause. A pre-vote changes nothing a peer can
        # observe, so it is also how a member that has forgotten learns whether
        # enough others have for a recovery election to be winnable; a separate
        # "probe" once did that, feeding evidence that outlived its round.
        self._pre_campaign()

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
    #     member not proven to have forgotten.
    #
    # The second clause is what makes it sound rather than merely convenient.
    # Any entry that survives does so on a member that still has its log; that
    # member applies the ordinary up-to-dateness check and refuses a candidate
    # lacking the entry; and since its approval is required, such a candidate
    # cannot win. Entries held only by members that forgot are gone either way.
    #
    # **What counts as proof** is the part that has to be right, and once was
    # not. A member has forgotten, for the election of term T, only if its own
    # reply to this candidacy says ``voting=False`` at term T. That reply is
    # binding: the member has adopted T, and ``on_promote`` refuses a promotion
    # from an earlier term, so it stays non-voting until a leader of T or later
    # promotes it -- and no such leader can exist while T is being decided. The
    # candidate does the arithmetic, over nothing but such replies and its own
    # condition (``_won``). The voters do none: a member that has forgotten
    # grants on log currency like any other, because its grant counts only
    # where the round itself has proved recovery warranted.
    #
    # Evidence gathered any other way goes stale. This was once decided on
    # observations kept between rounds -- a peer seen answering ``voting=False``
    # at some point -- which a candidate also sent the voters to re-check. But
    # a member is promoted back to voting by its leader without anyone else
    # hearing of it, so an observation could be false by the time it was used:
    # the Rust chaos soak measured a candidate counting as forgotten a member it
    # had itself promoted, which had since led a term, and winning without the
    # committed entries that member held (seeds 59925, 110797, 111540). A reply
    # refused under a leader lease carries the voter's lower term, so it proves
    # nothing -- correctly, since a leader exists.
    #
    # A pre-vote round predicts the election by the same arithmetic over its
    # own replies, counting as forgotten only members that *granted* while
    # saying ``voting=False``. Nothing in a pre-vote is binding, so the
    # prediction must never promise a recovery the real round would refuse: a
    # refusal may come from under a live leader's lease, and a term raised for
    # a doomed recovery is the very disruption pre-vote exists to prevent.
    #
    # Worked through on five members with three forgetful ones, where a
    # committed entry survives on one of the two remaining: the candidate that
    # lacks it needs that member's approval and is refused, so the candidate
    # that has it is the only one that can win. See
    # ``TestForgottenEvidenceIsBinding`` in ``test_consensus.py``.

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

    def _reset_election_timer(self) -> None:
        self._deadline = (
            asyncio.get_running_loop().time() + self._timing.election_timeout()
        )

    def _persist(self) -> None:
        self._terms.save(PersistentState(
            term=self._term, voted_for=self._voted_for,
            incarnation=self._incarnation,
            # The bound already durable, carried over: this save is about the
            # term, and must not erase the reservation the last one recorded.
            cursor_reservation=self._machine.cursors.reservation,
        ))

    def _fail(self, error: RaftInvariantViolated) -> None:
        """Stop this member for good: a broken invariant stays broken.

        Fail-stop, the position ``RaftInvariantViolated`` documents and the one
        ``go.etcd.io/raft`` takes with ``Panicf``: continuing from a state proven
        impossible can only spread the damage. So before this returns, every
        part that could spread it has stopped -- leadership, whose heartbeats
        would carry a commit index this member no longer vouches for; elections;
        applying; and every caller waiting on an answer, told "unavailable" (a
        503 the Node retries) because this member will never apply its entry.
        The transport then closes, which is when peers see the member gone, and
        the owner is told (``wait_for_failure``) so the process can exit: a
        restart -- automatic under a service manager -- brings the member back
        with nothing, and the leader catches it up as a non-voting learner.

        Once only: a second violation found while stopping says nothing new.
        """
        if self._failure is not None:
            return
        self._failure = error
        log.error(
            "raft: %s stops: consensus invariant violated: %s",
            self._layout.local.name, error,
        )
        self._closing = True
        if self._ticker is not None:
            self._ticker.cancel()
        self._relinquish("consensus invariant violated")
        # Released here, unlike ``_relinquish``, which releases nothing because
        # a later leader may yet commit what a caller waits on: this member will
        # never apply it, whoever commits it.
        reason = f"member stopped: consensus invariant violated: {error}"
        self._batcher.fail_all(RaftUnavailable(reason))
        self._fail_reads(reason)
        for future in self._waiters.values():
            if not future.done():
                future.set_exception(RaftUnavailable(reason))
        self._waiters.clear()
        self._failed.set()
        if self._closer is None:
            self._closer = asyncio.get_running_loop().create_task(
                self._close(), name=f"raft-close-{self._layout.local.name}",
            )

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
        self._pre_refusals.clear()
        self._forgotten.clear()
        self._pre_forgotten.clear()
        # Nothing is released, as etcd releases nothing when a leader steps
        # down: a proposal still queued is routed by this member's role when it
        # drains, and one already appended waits for its entry -- which a later
        # leader may yet commit -- or for its caller to stop waiting
        # (``_wait_for``). Failing them here told callers "unavailable" about
        # entries that went on to commit.
        #
        # Reads are the exception, as they are in etcd, whose server fails a
        # read with ``ErrLeaderChanged`` when the leader changes
        # (``read/read.go:170-193``): a read is confirmed by a quorum that this
        # member *still* leads, and it no longer does.
        self._fail_reads(f"no longer the leader: {reason}")
        self._reset_election_timer()

    def _step_down(self, term: int) -> None:
        """Adopt a higher term and return to following.

        Releases no proposal, for the reason ``_relinquish`` gives; fails any
        read, for the reason it gives too.
        """
        self._fail_reads("no longer the leader: a later term began")
        self._term = term
        # Every partial snapshot was sent in an earlier term, and no chunk of
        # an earlier term is accepted any more, so none of them can finish.
        # Kept, each was memory held for the life of the member.
        self._installing.clear()
        self._voted_for = None
        self._role = Role.FOLLOWER
        self._leader = None
        self._votes.clear()
        self._pre_votes.clear()
        self._pre_refusals.clear()
        self._forgotten.clear()
        self._pre_forgotten.clear()
        self._persist()

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
        self._pre_refusals = set()
        self._pre_forgotten = set()
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
        return self._won(self._pre_votes, self._pre_forgotten)

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
        # As in ``_step_down``: a new term ends every transfer of the old one.
        self._installing.clear()
        self._voted_for = self._layout.local.index
        self._persist()
        self._votes = {self._layout.local.index}
        self._forgotten = set()
        self._leader = None
        self._reset_election_timer()

        log.debug(
            "raft: %s campaigning in term %d",
            self._layout.local.name, self._term,
        )

        if self._won(self._votes, self._forgotten):
            self._become_leader()
            return

        request = RequestVote(
            term=self._term, candidate=self._layout.local.index,
            last_log_index=self._log.last_index,
            last_log_term=self._log.last_term,
        )
        for peer in self._peers:
            self._transport.send(peer, request)

    def on_request_vote(self, peer: int, message: RequestVote) -> RequestVoteReply:
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

        # A member that has forgotten grants on the same terms as any other --
        # log currency, one vote per term -- and says what it is in the reply.
        # Whether that grant counts is the candidate's to decide, from this
        # round's replies alone: see "When every voter has forgotten".
        granted = False
        if message.term == self._term:
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
        granted = (
            message.term > self._term
            and self._log.is_at_least_as_current_as(
                message.last_log_index, message.last_log_term,
            )
        )
        return RequestVoteReply(
            term=message.term if granted else self._term,
            granted=granted, voting=self._voting, pre_vote=True,
        )

    def on_request_vote_reply(self, peer: int, message: RequestVoteReply) -> None:
        if message.pre_vote:
            self._on_pre_vote_reply(peer, message)
            return

        if message.term > self._term:
            self._step_down(message.term)
            return
        if self._role is not Role.CANDIDATE or message.term != self._term:
            # Including a reply that arrives after its round is over: it is
            # evidence about that round and no other. Keeping such a reply was
            # how a member promoted since came to be counted as forgotten.
            return
        if not message.voting:
            # Binding -- this term's answer from the member itself.
            self._forgotten.add(peer)
        if message.granted:
            self._votes.add(peer)
        if self._won(self._votes, self._forgotten):
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
            if not message.voting:
                # From a grant only: see "When every voter has forgotten" for
                # why a pre-vote must never over-predict a recovery.
                self._pre_forgotten.add(peer)
            if self._won_pre_vote():
                self._campaign()
            return

        if not message.granted:
            # **A lost round ends the candidacy**, which is
            # ``go.etcd.io/raft``'s ``case quorum.VoteLost:
            # r.becomeFollower(r.Term, None)`` (``raft.go:1707``) and was
            # missing here.
            #
            # Losing is the *ordinary* outcome when the cluster is healthy: the
            # other members are inside their leader's lease and refuse on those
            # grounds. Without this the member simply stays a pre-candidate and
            # re-campaigns at every timeout, for as long as the leader lives.
            #
            # That is not merely untidy. A pre-candidate has cleared its
            # ``_leader`` -- deliberately, to release the lease it was holding
            # -- so while it stays one it answers every mutation with "no
            # leader elected", and a mutation *forwarded* to it because it owns
            # the Node fails with "the member owning node did not answer". A
            # member that lost a pre-vote is a follower, and a follower waits
            # to be told who leads instead of insisting nobody does.
            self._pre_refusals.add(peer)
            if self._pre_vote_is_lost():
                log.debug(
                    "raft: %s lost its pre-vote round and returns to following",
                    self._layout.local.name,
                )
                self._role = Role.FOLLOWER
                self._pre_votes.clear()
                self._pre_refusals.clear()
                # A fresh window, as etcd's ``becomeFollower`` -> ``reset``
                # gives. Without it the deadline that has already expired is
                # still expired, and the next tick campaigns again immediately.
                self._reset_election_timer()

    def _pre_vote_is_lost(self) -> bool:
        """Can this round no longer be won, however the rest answer?

        Every member that has refused is one that cannot later grant, so the
        best case left is everyone else saying yes. When even that falls short
        of a quorum the round is decided, and etcd's ``VoteResult`` calls it
        ``VoteLost``.

        Deliberately *not* expressed through ``_won``: that asks whether the
        votes in hand are sufficient, and the answer wanted here is whether the
        votes still outstanding could ever be. A member with a forgotten log
        makes ``_won`` stricter still, which can only make losing come sooner,
        so this bound stays correct under the recovery clause as well.
        """
        still_possible = self._layout.size - len(self._pre_refusals)
        return still_possible < self._layout.quorum

    def _won(self, votes: set[int], forgotten: set[int]) -> bool:
        """Has this round collected enough of the right votes?

        Ordinarily a quorum of grants from members that can vote: a grant from
        one that has forgotten says only that the candidate is as current as an
        empty log, which proves nothing. Once the round's own replies prove a
        quorum of voters impossible -- ``forgotten``, plus this member if it has
        forgotten too -- a quorum of grants of any kind is necessary but not
        sufficient: every member not proven to have forgotten must also have
        granted, because those are the only members whose up-to-dateness check
        still means anything, and the surviving copy of a committed entry can
        only be on one of them. See "When every voter has forgotten".

        Members nobody has heard from count among those, and they cannot have
        granted -- so a partitioned cluster never satisfies this, which is the
        intended answer.
        """
        members = {member.index for member in self._layout.members}
        proven = forgotten & members
        if not self._voting:
            proven.add(self._layout.local.index)
        granted = votes & members
        quorum = self._layout.quorum
        if len(granted - proven) >= quorum:
            return True
        if self._layout.size - len(proven) >= quorum:
            return False
        return len(granted) >= quorum and members - proven <= granted

    def _become_leader(self) -> None:
        self._role = Role.LEADER
        self._leader = self._layout.local.index
        # A leader is by definition a voter: if this member won through the
        # recovery path above it was not one a moment ago, and leaving it
        # non-voting would leave it unable to vote in the next election it
        # takes part in -- for no reason, since it is now the member the others
        # are being caught up *from*.
        self._voting = True
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
        # sets `promote_through` against *this* leader's log (see
        # `on_append_entries_reply` for why its last index and not its commit
        # index).
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
            peer.heard_request = 0
            # Likewise a snapshot this member was sending in an earlier term:
            # the new transfer starts from zero, and a carried-over offset
            # would have the leader resume a stream the peer is not expecting.
            peer.snapshot_offset = 0
            peer.snapshot_request = 0
            peer.sending = None
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

        contradiction = self._contradicting_committed(message)
        if contradiction is not None:
            # A leader that contradicts what this member has committed proves
            # committed data lost from the cluster (``_contradicting_committed``).
            # Nothing brings this member back into step -- it refuses every
            # append anchored at its commit point, and would serve its stale
            # store until the leader's snapshot passed that point -- so it
            # stops, as for any broken invariant (``_fail``), answering nothing
            # a leader could count: request id 0 is under every reply floor.
            self._fail(RaftInvariantViolated(contradiction))
            return self._append_reject(catching_up=not self._voting)

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
            try:
                entries = [
                    Entry(
                        term=wire.term, index=wire.index, payload=wire.payload,
                        value=decode_operation(wire.payload),
                    )
                    for wire in message.entries
                ]
            except RaftProtocolError as exc:
                # Refused at the edge, in the reply, rather than inside apply,
                # which runs synchronously and has nowhere to fail -- as the
                # Rust member refuses it. Raised past this handler, it made the
                # transport drop the whole connection instead.
                log.warning("raft: undecodable entry: %s", exc)
                return self._refusal(message)
            try:
                self._log.append_replicated(
                    entries, committed=self._commit_index,
                )
            except ValueError as exc:
                # Entries that do not follow what this log holds: a refusal,
                # which the leader answers by trying again from where it points,
                # as the Rust member refuses it. Raised past this handler, it
                # was logged as a failed connection, and the connection dropped.
                log.warning("raft: replicated append refused: %s", exc)
                return self._refusal(message)
            except RaftInvariantViolated as exc:
                # An entry here conflicting at or below the commit index. No
                # message can reach that -- ``prev_log_index < commit_index``
                # was answered above, so every entry here lies beyond the commit
                # index -- which is what makes it a broken invariant rather
                # than a refusal. Raised past this handler, it failed the link
                # the message came by, and the next one repeated it. The member
                # stops instead, and answers nothing a leader could count.
                self._fail(exc)
                return self._append_reject(catching_up=not self._voting)

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

    def _contradicting_committed(self, message: AppendEntries) -> str | None:
        """Where this leader's append contradicts an entry committed here, or ``None``.

        Raft makes it impossible: a leader holds every entry committed before
        its term (Leader Completeness, section 5.4.3), and Log Matching makes
        its entries at those indexes the ones committed here. So a disagreement
        proves committed data lost from the cluster -- which the non-voting
        rejoin and the recovery election's veto exist to prevent, so it means a
        defect. ``go.etcd.io/raft`` treats the same observation as corruption
        and panics (``maybeAppend``, ``log.go:117-121``).

        Checked wherever this member still can: the anchor, and every entry the
        message carries, that lie from the snapshot boundary -- whose term is
        kept -- up to the commit index. Below the boundary nothing is left to
        compare, and the leader's next append, anchored at the commit point, is
        where it shows. Before the ``prev_log_index < commit_index`` answer,
        deliberately: that answer credits the leader with ``match = commit``
        without looking, and an anchor that matches below the commit point can
        still carry entries contradicting it. At most a batch of comparisons.
        """
        floor = self._log.snapshot_index
        ceiling = min(self._commit_index, self._log.last_index)
        claims = [(message.prev_log_index, message.prev_log_term)]
        claims += [(wire.index, wire.term) for wire in message.entries]
        for index, term in claims:
            if floor <= index <= ceiling:
                held = self._log.term_at(index)
                if held != term:
                    return (
                        f"leader {message.leader} of term {message.term} holds "
                        f"index {index} at term {term}, committed here at term "
                        f"{held}: committed data was lost from the cluster"
                    )
        return None

    def _refusal(self, message: AppendEntries) -> AppendEntriesReply:
        """A plain refusal of ``message``: it names the append, and credits nothing."""
        return AppendEntriesReply(
            term=self._term, success=False, match_index=0, conflict_index=0,
            conflict_term=0, catching_up=not self._voting,
            request_id=message.request_id,
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
            # Newly noticed: it must hold everything committed as of now
            # before its vote counts again -- and "everything committed" is
            # bounded by this leader's *last* index, not by its commit index.
            #
            # The two differ exactly after an election. A new leader holds
            # every committed entry (Leader Completeness), but it learns that an
            # entry is committed only once one of its own term commits above
            # it; until then an entry an earlier leader committed looks, from
            # here, like one nobody committed. The chaos soak measured the
            # consequence of barring at the commit index: a leader committed an
            # entry with one follower and restarted before telling it; that
            # follower won the next term, still believing the commit index
            # below the entry, and promoted the restarted member at that bar
            # without it; the promoted member then voted in a candidate that
            # had never had the entry, which wrote over it. The module
            # docstring's own scenario, through the promotion rather than the
            # vote.
            #
            # Entries committed after the member restarted do not need waiting
            # for -- a member catching up is never counted, so they were
            # committed on a majority without it -- but nothing here can tell
            # them from the others, and they are in this log, so the whole log
            # is the bar. etcd's server judges a learner ready against the same
            # point, the leader's own ``Match`` (``server/etcdserver/server.go``,
            # ``isLearnerReady``).
            state.promote_through = self._log.last_index
            log.info(
                "raft: member %d is catching up through index %d",
                peer, state.promote_through,
            )

        # Whatever it said, the peer answered in this term -- a read's
        # confirmation, but only from a member that votes. etcd counts voters'
        # acknowledgements alone (``maybeAdvance(r.trk.Voters)``,
        # ``raft.go:1604-1605``; ``CommittedIndex`` over the voters' acks,
        # ``read_only.go:79-81``), as this leader counts only voters toward a
        # commit and toward check-quorum. Judged by the flag this reply
        # carries, not the one before it: the first reply of a member that
        # restarted with nothing is the one that says so.
        if not message.catching_up:
            state.heard_request = max(state.heard_request, message.request_id)
        self._confirm_reads()

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
            resume = max(1, message.conflict_index)
            if resume >= state.next_index:
                # Nothing learned: the peer asks to resume where this leader
                # already is, or past it. Sent again at once, the same append
                # draws the same refusal -- and it was, forever, at zero delay:
                # 124,991 appends inside one millisecond of cluster time in the
                # Rust soak (seed 111504), from a follower whose committed
                # snapshot contradicts this log at its boundary, which only lost
                # committed data (amnesia past the budget) can produce. etcd
                # re-sends only when a rejection lowers ``Next`` (``MaybeDecrTo``,
                # ``tracker/progress.go:226-254``); here the next heartbeat is
                # the next probe.
                return
            state.next_index = resume
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
        #
        # And never past this leader's own log: a follower cannot hold more of
        # it than it holds, as the snapshot-reply path already says
        # (``on_install_snapshot_reply``). In a correct run the two agree -- a
        # follower vouches for a window this leader sent, or for its commit
        # index, which Leader Completeness puts inside this log. Where they do
        # not, committed data has been lost, and the unbounded credit put
        # ``next_index`` past the end of the log: ``_send_append`` then raised
        # out of every tick, and the follower was never sent an anchor it could
        # check against what it committed (``_contradicting_committed``).
        vouched = min(message.match_index, self._log.last_index)
        advanced = vouched > state.match_index
        if advanced:
            state.match_index = vouched
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
        self._arm_reads()

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
            except RaftInvariantViolated as exc:
                # Past the catch-all below, deliberately. That handler exists
                # so one bad apply cannot kill a member, and it is right for
                # everything transient -- but an invariant that is broken stays
                # broken, and logging it once per wake-up would be a silent
                # failure wearing the costume of a handled one.
                #
                # Re-raising it was no better: it ended this task and nothing
                # else. Nobody awaits the task before ``close``, which gathers
                # it with ``return_exceptions=True``, and the reference kept to
                # it stops asyncio reporting it -- so the member went on
                # leading, voting and serving a store that no longer moved,
                # with not one line logged. It stops instead (``_fail``).
                self._fail(exc)
                return
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

    def _wait_for(
        self, proposal: ProposalId, future: asyncio.Future[Outcome],
    ) -> None:
        """Hold ``future`` until its entry applies -- or its caller stops waiting.

        A waiter used to leave only when its entry applied, so a forwarded
        proposal that never became an entry here -- its ``Propose`` never
        delivered, refused by a member no longer leading, or accepted and then
        overwritten -- kept one for the life of the member: 1,046 of them in 40
        chaos-soak runs, every one's caller long gone. etcd removes the waiter
        when the client's context ends (``v3_server.go:1117``, ``:1129``, "GC
        wait"); a caller's deadline cancelling this future is that here. The
        entry may still commit, with nobody waiting: what a caller answered
        "unavailable" was told to expect.
        """
        self._waiters[proposal] = future

        def release(done: asyncio.Future[Outcome]) -> None:
            if self._waiters.get(proposal) is done:
                del self._waiters[proposal]

        future.add_done_callback(release)

    # -- reading ---------------------------------------------------------

    async def read_index(self, *, timeout: float) -> int:
        """The index a read here must have applied before it may answer.

        etcd's ReadIndex in its default, quorum-confirmed mode
        (``ReadOnlySafe``, ``raft.go:58-70``): the commit index as it stood when
        the read began, released only once a quorum has confirmed, after that
        moment, that the leader giving it still leads. A member that has
        applied through it holds every write acknowledged before the read
        began, so an answer from its own store -- a 400, a 404 -- is one the
        leader would have given.

        On the leader the read is served here; a follower asks its leader
        (``on_read_index``). Raises ``RaftUnavailable`` when there is no leader,
        when leadership is lost before a quorum confirms, or at ``timeout``.
        """
        if self._role is not Role.LEADER:
            return await self._ask_leader_for_read_index(timeout)
        try:
            return await asyncio.wait_for(self._begin_read(), timeout)
        except asyncio.TimeoutError as exc:
            raise RaftUnavailable(
                "no quorum confirmed this member's leadership in time",
            ) from exc

    async def _ask_leader_for_read_index(self, timeout: float) -> int:
        leader = self._leader
        if leader is None:
            # etcd drops the request with no leader (``raft.go:1764-1768``); a
            # caller here is told at once rather than left to its deadline.
            raise RaftUnavailable("no leader elected")
        # A silent leader or a failed link raises ``RaftUnavailable`` from the
        # transport, which resolves a request only with the kind it expects
        # (``EXPECTED_REPLY``).
        reply: ReadIndexReply = await self._transport.request(
            leader, ReadIndex(request_id=0), timeout=timeout,
        )
        if not reply.ok:
            raise RaftUnavailable(
                f"member {leader} gave no read index: {reply.reason}",
            )
        return reply.index

    async def on_read_index(self, peer: int, message: ReadIndex) -> ReadIndexReply:
        """A member asking this one, as its leader, for a read index.

        Waits for confirmation, or for leadership to end, and for nothing else:
        a leader that cannot hear a quorum stands down within an election
        window (check-quorum), which fails every read waiting on it. The asker
        bounds its own wait; etcd's leader holds reads the same way.
        """
        if self._role is not Role.LEADER:
            return ReadIndexReply(
                ok=False, index=0, reason="not the leader",
                request_id=message.request_id,
            )
        try:
            index = await self._begin_read()
        except RaftUnavailable as exc:
            return ReadIndexReply(
                ok=False, index=0, reason=str(exc), request_id=message.request_id,
            )
        return ReadIndexReply(
            ok=True, index=index, reason="", request_id=message.request_id,
        )

    def _begin_read(self) -> asyncio.Future[int]:
        """Record a read on this leader; the future is its confirmed index."""
        future: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        if self._layout.size == 1:
            # A lone voter is answered at once, at its commit index, as etcd
            # answers one (``raft.go:1355-1361``): there is nobody to confirm
            # anything with, and it has acknowledged nothing it has not
            # committed.
            future.set_result(self._commit_index)
            return future
        self._reads.append(_Read(future=future))
        self._arm_reads()
        return future

    def _committed_in_current_term(self) -> bool:
        """etcd's ``committedEntryInCurrentTerm`` (``raft.go:2065-2070``).

        Until a leader commits an entry of its own term it cannot know how far
        earlier terms committed (Raft §8), so its commit index is no bound on
        what a read must see: etcd postpones reads until then
        (``raft.go:1365-1367``).
        """
        try:
            return self._log.term_at(self._commit_index) == self._term
        except RaftLogCompacted:
            return False

    def _arm_reads(self) -> None:
        """Give every postponed read its index, and ask a quorum to confirm it.

        Armed at the commit index and the append sequence of this moment, then
        a heartbeat to every peer at once -- etcd broadcasts one per read
        (``sendMsgReadIndexResponse``, ``raft.go:2146-2156``) -- whose replies,
        carrying later ids, are the confirmation ``_confirm_reads`` counts.
        """
        if self._role is not Role.LEADER or not self._committed_in_current_term():
            return
        armed = False
        for read in self._reads:
            if read.index is None:
                read.index = self._commit_index
                read.after = self._append_sequence
                armed = True
        if armed:
            self._replicate()
            self._confirm_reads()

    def _confirm_reads(self) -> None:
        """Release every read a quorum has confirmed.

        A read is confirmed when this member and enough voters to make a quorum
        have answered, in this term, an append sent after it was armed -- etcd's
        ``maybeAdvance`` over the voters' echoed positions
        (``raft.go:1600-1609``). Members catching up are not voters here, as
        they are not for commitment or check-quorum. A read whose caller has
        gone is simply dropped.
        """
        if not self._reads:
            return
        quorum = self._layout.quorum
        waiting: list[_Read] = []
        for read in self._reads:
            if read.future.done():
                continue
            if read.index is None:
                waiting.append(read)
                continue
            confirmed = 1 + sum(
                1 for peer in self._peers.values()
                if not peer.catching_up and peer.heard_request > read.after
            )
            if confirmed >= quorum:
                read.future.set_result(read.index)
            else:
                waiting.append(read)
        self._reads = waiting

    def _fail_reads(self, reason: str) -> None:
        """Fail every read waiting here: see ``_relinquish``."""
        for read in self._reads:
            if not read.future.done():
                read.future.set_exception(RaftUnavailable(reason))
        self._reads = []

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
                self._wait_for(proposal, item.future)
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
            self._wait_for(proposal, item.future)
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
                # A reconnect ends the exchange: whatever was in flight is
                # disowned (``reply_floor`` above fences its replies, which on
                # BULK may yet arrive), and holding the pause open would strand
                # the peer.
                state.snapshot_request = 0
                state.pending_request = 0
                state.snapshot_offset = 0
                state.sending = None
                state.sent_commit = 0
                self._send_append(peer, state)
        else:
            state.catching_up = False
            # A transfer from this peer may never resume, and held its buffer
            # for the life of the member if it did not. Nothing is lost by
            # dropping it: a reconnected leader restarts at offset 0, and if
            # the leader's own link survived -- links are directed -- its next
            # chunk finds no buffer, is answered with 0, and it starts over. A
            # restart, never a splice and never a stall.
            self._installing.pop(peer, None)

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
        # ``DivergenceDetected`` -- a tripwire since removed, see
        # ``machine.py`` -- and stopped applying for good: committed at 14,
        # applied stuck at 11. Replaying entries a snapshot already contains is
        # wrong whatever apply does about it: not every operation is idempotent.
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
        except SnapshotAbandoned:
            # A snapshot was installed while this one was being serialised
            # (``StateMachine.install_snapshot`` abandons the capture). It is
            # newer than anything this walk could produce, and it is already
            # held, so there is nothing to store and nothing to discard.
            log.debug(
                "raft: %s stopped a snapshot through index %d: a later one "
                "was installed", self._layout.local.name, applied,
            )
            return
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
        """Send the next chunk of this member's snapshot to a stranded peer.

        When there is no chunk to send this sends a **keepalive** instead, and
        that is not tidiness. ``go.etcd.io/raft`` carries heartbeats as their
        own message type, and ``bcastHeartbeat`` reaches every peer whatever
        its replication state -- so a follower waiting for a snapshot still
        hears from its leader and still knows one exists.

        Here a heartbeat *is* an ``AppendEntries``, so it arrives through
        ``_send_append``, which diverts to this method for any peer below the
        compaction boundary. Returning silently therefore sent that peer
        **nothing at all**: no entries, and no liveness either, because they
        are the same message. Its election timer expires, it campaigns, it
        loses against the leader's lease, and it does that for as long as the
        condition lasts -- while answering every mutation with "no leader
        elected", because campaigning clears its ``_leader``.
        """
        meta = self._snapshot_meta
        if meta is None or not self._snapshot:
            # Nothing to send yet. The peer stays behind until the next
            # compaction produces one, which is correct: there is no state to
            # hand it that it does not already have -- but it must still hear
            # that this leader is alive.
            self._send_keepalive(peer, state)
            return

        now = asyncio.get_running_loop().time()
        if state.snapshot_request:
            if now - state.snapshot_sent_at < self._timing.election_min:
                # One chunk at a time. See ``_PeerState.snapshot_request``. The
                # chunk is on BULK, which a slow transfer can occupy for a long
                # time, so the liveness signal goes separately on CONTROL.
                self._send_keepalive(peer, state)
                return
            # Overdue: lost with its connection, or its reply was. Sent again
            # under a new id, so should the first answer arrive after all it
            # is not the one awaited. See ``_PeerState.snapshot_sent_at``.
            state.snapshot_request = 0

        if state.snapshot_offset == 0 or state.sending is None:
            # A transfer starts -- or restarts, the follower having thrown its
            # buffer away -- so it is of the snapshot as it stands now, pinned
            # for its whole length. See ``_PeerState.sending``.
            state.sending = (meta, self._snapshot)
            state.snapshot_offset = 0
        sent_meta, payload = state.sending

        offset = state.snapshot_offset
        chunk = payload[offset:offset + self._timing.snapshot_chunk]
        done = offset + len(chunk) >= len(payload)
        # From the append sequence, so the floor a reset raises fences chunks
        # and appends alike.
        self._append_sequence += 1
        state.snapshot_request = self._append_sequence
        state.snapshot_sent_at = now
        self._transport.send(
            peer,
            InstallSnapshot(
                term=self._term, leader=self._layout.local.index,
                last_index=sent_meta.last_index, last_term=sent_meta.last_term,
                offset=offset, data=bytes(chunk), done=done,
                ownership=b"", request_id=state.snapshot_request,
            ),
            # BULK, so a multi-megabyte transfer cannot head-of-line-block the
            # heartbeats that keep this member's leadership alive.
            stream=Stream.BULK,
        )

    def _send_keepalive(self, peer: int, state: _PeerState) -> None:
        """Say "I am still the leader" to a peer that can be told nothing else.

        Anchored at index 0, which every log matches -- ``matches(0, 0)`` is
        true even of an empty one -- so the consistency check cannot fail and
        the peer cannot reject it. It carries no entries and a
        ``leader_commit`` of zero, so by the receiver's own rule
        (``min(leader_commit, prev_log_index + len(entries))``) it vouches for
        nothing and can move no commit index.

        What it does do is what etcd's ``MsgHeartbeat`` does: the receiver sets
        its leader, resets its election timer, and stops campaigning.

        The reply reports ``match_index`` 0, which the leader ignores --
        ``on_append_entries_reply`` takes only an acknowledgement that moves a
        peer *forward*. So this cannot be mistaken for progress, which is the
        one thing a keepalive must never be.
        """
        self._append_sequence += 1
        state.sent_commit = 0
        self._transport.send(peer, AppendEntries(
            term=self._term,
            leader=self._layout.local.index,
            prev_log_index=0,
            prev_log_term=0,
            leader_commit=0,
            request_id=self._append_sequence,
            entries=(),
        ))

    def on_install_snapshot(
        self, peer: int, message: InstallSnapshot,
    ) -> InstallSnapshotReply:
        """Accumulate a snapshot from the leader, and install it when complete.

        Chunks are accumulated rather than applied incrementally: a partially
        installed snapshot is a registry describing a state that never existed,
        and the whole point of this path is that the member using it cannot
        tell the difference on its own.

        Every answer carries this member's commit index and names the chunk it
        answers (``InstallSnapshotReply``): the first is how the leader learns
        to stop sending a snapshot this member already holds, the second how it
        tells the answer to the chunk in flight from one that outlived its
        transfer.
        """

        def answer(received: int, *, done: bool = False) -> InstallSnapshotReply:
            return InstallSnapshotReply(
                term=self._term, bytes_received=received, done=done,
                commit_index=self._commit_index, request_id=message.request_id,
            )

        if message.term < self._term:
            return answer(0)
        if message.term > self._term:
            self._step_down(message.term)
        self._role = Role.FOLLOWER
        self._leader = message.leader
        self._reset_election_timer()

        if (
            self._log.snapshot_index <= message.last_index <= self._commit_index
            and self._log.term_at(message.last_index) != message.last_term
        ):
            # Not held at all: the leader's snapshot ends on an entry committed
            # here at another term -- committed data lost from the cluster, as
            # in ``_contradicting_committed``. The member stops (``_fail``) and
            # credits nothing: request id 0 is under every reply floor.
            held = self._log.term_at(message.last_index)
            self._installing.pop(peer, None)
            self._fail(RaftInvariantViolated(
                f"leader {message.leader} of term {message.term} sent a "
                f"snapshot through index {message.last_index} at term "
                f"{message.last_term}, committed here at term {held}: "
                f"committed data was lost from the cluster",
            ))
            return InstallSnapshotReply(
                term=self._term, bytes_received=0, done=False,
                commit_index=0, request_id=0,
            )

        if message.last_index <= self._commit_index:
            # Already held: everything this snapshot covers is committed here,
            # and installing it would replace the state machine with older
            # state while ``_commit_index`` correctly stays put -- committed
            # entries un-applied, the one thing a state machine may never do.
            # Against the **commit index**, not the compaction boundary:
            # ``snapshot_index <= commit_index`` always, so the boundary let
            # through every snapshot landing in between. ``go.etcd.io/raft``
            # ignores it on exactly this line (``raft.go:1861``) and answers
            # with its commit index (``raft.go:1850-1853``), logged at Info:
            # it is a race between a leader's decision and this member's
            # progress, not a fault. Decided from the metadata at the first
            # chunk, rather than after a whole transfer has been assembled to
            # be thrown away.
            self._installing.pop(peer, None)
            log.info(
                "raft: %s ignored a snapshot through %d from member %d: "
                "committed through %d already",
                self._layout.local.name, message.last_index, peer,
                self._commit_index,
            )
            return answer(0)

        identity = (
            message.term, message.leader, message.last_index, message.last_term,
        )
        assembly = self._installing.get(peer)
        if message.offset == 0:
            # Only a first chunk may begin a transfer, and it always may: the
            # sender has started over, whatever it was sending before.
            assembly = _Assembly(identity)
            self._installing[peer] = assembly
        elif (
            assembly is not None
            and assembly.identity == identity
            and _holds(assembly.data, message.offset, message.data)
        ):
            # A copy of a chunk this member already holds. The leader sends a
            # chunk again once its answer is overdue, and a chunk that was slow
            # rather than lost arrives as well as its copy -- first, since one
            # connection carries both. Answered with what is assembled, which
            # is where the leader resumes; the copy is the chunk in flight, so
            # its answer is the one the leader acts on. Treated as a mismatch,
            # as it once was, it threw the transfer away and the leader began
            # again from nothing: 343 of 362 follower resets in chaos-soak seed
            # 140692, whose members needing a snapshot never finished one.
            return answer(len(assembly.data))
        elif (
            assembly is None
            or assembly.identity != identity
            or message.offset != len(assembly.data)
        ):
            # A chunk out of order, one disagreeing with what is held at its
            # offset, or -- the case an offset alone cannot see -- the next
            # chunk of a *different* snapshot. Restart rather than splice: a
            # snapshot assembled from mismatched pieces can parse and be wrong.
            self._installing.pop(peer, None)
            return answer(0)
        assembly.data += message.data
        buffer = assembly.data

        if not message.done:
            return answer(len(buffer))

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
            return answer(0)

        impossible = self._why_the_snapshot_cannot_be_real(
            meta, message.term, sent_as=(message.last_index, message.last_term),
        )
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
            return answer(0)

        self._machine.install_snapshot(store, ownership, meta.last_index)
        self._log.reset_to_snapshot(meta.last_index, meta.last_term)
        # Kept as this member's own snapshot. The log now starts at the
        # snapshot's boundary, so the entries below it exist on this member
        # only as these bytes -- and a member that cannot hand them on strands
        # every follower that needs them, should it ever lead. Only compaction
        # used to set these, so a leader that had caught up by snapshot and not
        # compacted since could send its stranded followers nothing but
        # keepalives, for as long as it led: the chaos soak's commonest
        # liveness failure. etcd keeps an applied snapshot as the storage's own
        # (``storage.go:218-237``) and serves that (``raft.go:672``).
        self._snapshot = bytes(buffer)
        self._snapshot_meta = meta
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
        return answer(len(buffer), done=True)

    def _why_the_snapshot_cannot_be_real(
        self, meta: SnapshotMeta, sender_term: int, *,
        sent_as: tuple[int, int],
    ) -> str | None:
        """Is this snapshot's metadata possible at all? ``None`` if it is.

        Two checks, both about metadata rather than content -- the content
        already had to decode and install before we got here.

        A snapshot's own header must describe the transfer that carried it, and
        it cannot describe a term above the one its sender holds: the sender
        built it from entries it had committed, and it cannot have committed an
        entry from a term it has not reached.

        A snapshot this member already holds -- one at or below its commit
        index -- is not impossible, only unneeded, and is answered before any
        of this (``on_install_snapshot``).
        """
        if (meta.last_index, meta.last_term) != sent_as:
            # The payload's own header and the transfer that carried it
            # describe different snapshots. The leader credits what it sent
            # *as*; installing what the bytes say would leave the two members
            # disagreeing about what this one holds -- a phantom match on one
            # side, a hole on the other.
            return (
                f"its contents describe a snapshot through ({meta.last_index}, "
                f"t{meta.last_term}) but it was sent as one through "
                f"({sent_as[0]}, t{sent_as[1]})"
            )
        if meta.last_term > sender_term:
            return (
                f"it covers term {meta.last_term} but arrived from a member "
                f"at term {sender_term}"
            )
        return None

    def on_install_snapshot_reply(
        self, peer: int, message: InstallSnapshotReply,
    ) -> None:
        if message.term > self._term:
            self._step_down(message.term)
            return
        if self._role is not Role.LEADER or message.term != self._term:
            # A reply is evidence only about the exchange it answers, and one
            # from an earlier term answers a transfer this leadership never
            # made. Its correlation id is below the floor this leadership
            # raised on beginning, so it would be fenced below as well; the
            # term is checked first, as ``on_append_entries_reply`` checks its
            # replies.
            # Believed, a stale ``done`` credited the peer with *this* leader's
            # current snapshot -- measured as a member credited with index 504
            # from a term-78 reply about a snapshot through 500, whose genuine
            # rejections were then discarded as stale for good. etcd drops every
            # lower-term message before per-type handling (``raft.go:1133-1186``).
            return
        state = self._peers.get(peer)
        if state is None:
            return
        if message.request_id <= state.reply_floor:
            # Sent before a reset -- a reconnect, or this leadership beginning
            # -- and so about an exchange this leader has disowned, possibly
            # with an incarnation of the peer that no longer exists. Fenced as
            # ``on_append_entries_reply`` fences its own.
            return

        # A peer working through a transfer is answering, and must count toward
        # check-quorum exactly as an append reply does. In ``go.etcd.io/raft``
        # the snapshot acknowledgement arrives as an ordinary ``MsgAppResp``,
        # so it sets ``RecentActive`` on the same line.
        state.last_heard_at = asyncio.get_running_loop().time()

        # The follower's own statement of what it holds, credited whichever
        # chunk this answers: its committed prefix is this leader's, so the
        # credit is true however stale the reply (see
        # ``InstallSnapshotReply.commit_index``). Bounded by this leader's log,
        # which a complete leader's commit-holding peers cannot exceed.
        credited = min(message.commit_index, self._log.last_index)
        if credited > state.match_index:
            state.match_index = credited
            state.next_index = max(state.next_index, credited + 1)

        if message.request_id != state.snapshot_request:
            # Not the reply to the chunk in flight -- a first copy answered
            # after an overdue one was sent again. It drives nothing: a second
            # stream beside the first is how the transfer once forked.
            return
        state.snapshot_request = 0

        pinned = state.sending
        if message.done or (
            pinned is not None and message.commit_index >= pinned[0].last_index
        ):
            # Installed -- or already held, which etcd calls an ignored
            # snapshot: either way the follower has everything the transfer
            # covers, and replication resumes from what it has said it holds.
            state.sending = None
            state.snapshot_offset = 0
            state.next_index = state.match_index + 1
            self._send_append(peer, state)
            return

        if message.bytes_received == 0:
            # The follower threw the transfer away. It starts again, from a
            # fresh pin -- but at the next heartbeat, not now: a refusal that
            # recurs would otherwise ping-pong at network speed. etcd pauses a
            # failed snapshot's peer the same way (``MsgAppFlowPaused``,
            # ``raft.go:1618-1628``).
            state.sending = None
            state.snapshot_offset = 0
            return

        # How much the follower has assembled: the acknowledgement, and the
        # offset to resume from.
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
