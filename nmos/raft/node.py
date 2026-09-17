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
from nmos.raft.errors import RaftLogCompacted, RaftUnavailable
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


class Role(Enum):
    FOLLOWER = "follower"
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

        self._votes: set[int] = set()
        self._waiters: dict[ProposalId, asyncio.Future[Outcome]] = {}
        self._sequence = 0
        self._batcher: ProposalBatcher[RegistryOperation, Outcome] = (
            ProposalBatcher(self._drain)
        )

        self._deadline = 0.0
        self._quorum_deadline = 0.0
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
        """Whether enough members are reachable for a write to commit."""
        reachable = 1 + sum(
            1 for peer in self._peers.values() if peer.up and not peer.catching_up
        )
        return reachable >= self._layout.quorum

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
            if self.has_quorum:
                self._quorum_deadline = now + self._timing.election_max
            elif now >= self._quorum_deadline:
                # Check-quorum. A leader cut off from a majority cannot commit
                # anything, and the other side of the partition has had long
                # enough to elect someone else -- so continuing to answer as
                # leader would mean reporting READY while accepting writes
                # that can never commit.
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
        if now >= self._deadline and self._voting:
            self._campaign()

    # -- elections -------------------------------------------------------

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
        self._persist()
        if was_leader:
            # In-flight proposals cannot commit under a term we no longer own.
            self._batcher.fail_all(RaftUnavailable("no longer the leader"))

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

        if len(self._votes) >= self._layout.quorum:
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
        if message.term > self._term:
            self._step_down(message.term)

        granted = False
        if message.term == self._term and self._voting:
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

    def on_request_vote_reply(self, peer: int, message: RequestVoteReply) -> None:
        if message.term > self._term:
            self._step_down(message.term)
            return
        if self._role is not Role.CANDIDATE or message.term != self._term:
            return
        if not message.voting:
            # A member still catching up. Its vote is not a vote.
            return
        if message.granted:
            self._votes.add(peer)
            if len(self._votes) >= self._layout.quorum:
                self._become_leader()

    def _become_leader(self) -> None:
        self._role = Role.LEADER
        self._leader = self._layout.local.index
        next_index = self._log.last_index + 1
        for peer in self._peers.values():
            peer.next_index = next_index
            peer.match_index = 0
        log.info(
            "raft: %s is leader for term %d",
            self._layout.local.name, self._term,
        )
        self._leader_changed.set()
        self._leader_changed.clear()
        self._quorum_deadline = (
            asyncio.get_running_loop().time() + self._timing.election_max
        )

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
        except RaftLogCompacted:
            # The entries this peer needs have been compacted away. It cannot
            # be caught up by replication, so it is caught up by state.
            # Both calls are inside the guard: the boundary can fall between
            # them, and a leader that only checked one would raise out of its
            # own tick.
            self._send_snapshot(peer, state)
            return
        self._transport.send(peer, AppendEntries(
            term=self._term,
            leader=self._layout.local.index,
            prev_log_index=previous,
            prev_log_term=prev_term,
            leader_commit=self._commit_index,
            request_id=0,
            entries=tuple(
                WireEntry(term=e.term, index=e.index, payload=e.payload)
                for e in entries
            ),
        ))

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
            self._log.append_replicated([
                Entry(
                    term=wire.term, index=wire.index, payload=wire.payload,
                    value=decode_operation(wire.payload),
                )
                for wire in message.entries
            ])

        if message.leader_commit > self._commit_index:
            self._commit_index = min(message.leader_commit, self._log.last_index)
            self._schedule_apply()

        return AppendEntriesReply(
            term=self._term, success=True, match_index=self._log.last_index,
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

        if not message.success:
            # Resume from the start of the conflicting term rather than one
            # index back, so a far-behind member costs a handful of exchanges
            # instead of one per entry.
            state.next_index = max(1, message.conflict_index)
            self._send_append(peer, state)
            return

        state.match_index = message.match_index
        state.next_index = message.match_index + 1

        if state.catching_up and state.match_index >= state.promote_through:
            state.catching_up = False
            self._transport.send(peer, Promote(
                term=self._term, leader=self._layout.local.index,
                through_index=state.promote_through,
            ))
            log.info("raft: member %d promoted", peer)

        self._advance_commit()

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

    async def _apply_forever(self) -> None:
        while not self._closing:
            await self._apply_wake.wait()
            self._apply_wake.clear()
            try:
                await self._apply_committed()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("raft: applying committed entries failed")

    async def _apply_committed(self) -> None:
        """Apply up to the commit index, in bounded runs.

        The ``sleep(0)`` is between runs, never inside one: ``machine.apply``
        must not be interrupted mid-mutation, and the loop must not hold the
        event loop for a whole catch-up.
        """
        while self._machine.last_applied < self._commit_index:
            start = self._machine.last_applied + 1
            try:
                entries = self._log.slice(start, self._timing.max_apply_batch)
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
                state.next_index = self._log.last_index + 1
                state.match_index = 0
                # A reconnect invalidates any transfer that was in flight: the
                # chunk it was waiting on will never be answered.
                state.snapshot_in_flight = False
                state.snapshot_offset = 0
                self._send_append(peer, state)
        else:
            state.catching_up = False

    # -- compaction ------------------------------------------------------

    async def _maybe_compact(self) -> None:
        """Take a snapshot and drop the entries it covers.

        A log that is never compacted grows for the life of the cluster, and
        every member holds all of it in memory. Compaction is therefore not an
        optimisation here; it is what makes an in-memory log viable at all.

        The safety condition is what the ``min(matchIndex)`` is for: discarding
        an entry a follower has not yet received strands that follower on
        replication and forces a whole snapshot transfer instead. So in the
        normal case the leader compacts only as far as its slowest *reachable*
        follower has confirmed.

        ``max_log_entries`` overrides that, deliberately. One unreachable
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

        through = applied
        if self._role is Role.LEADER and held < self._timing.max_log_entries:
            confirmed = [
                state.match_index for state in self._peers.values() if state.up
            ]
            if confirmed:
                through = min(applied, min(confirmed))
        if through <= self._log.snapshot_index:
            return

        try:
            term = self._log.term_at(through)
        except RaftLogCompacted:
            return

        capture = self._snapshots.begin(
            index=through, term=term, ownership=self._machine.ownership,
        )
        try:
            payload = await self._snapshots.finish(capture)
        except Exception:
            self._snapshots.abandon()
            log.exception("raft: taking a snapshot failed")
            return

        self._snapshot = payload
        self._snapshot_meta = SnapshotMeta(
            last_index=through, last_term=term, resources=0,
        )
        freed = self._log.discard_through(through, term)
        log.info(
            "raft: %s compacted through index %d, freeing %d entries",
            self._layout.local.name, through, freed,
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
