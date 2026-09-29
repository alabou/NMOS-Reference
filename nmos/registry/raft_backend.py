# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The raft-backed distributed backend: one round trip, and no read before it.

Same seam as the etcd backend -- four methods and a state property, with Query
untouched -- and a materially different path underneath. The comparison is the
point, so it is worth stating in the terms the benchmark measures:

============================  ==========  ====================
operation                     etcd        raft
============================  ==========  ====================
registration, steady state    2           **1**
first registration of a Node  3           **1**
heartbeat                     1           **0**
rejection the body decides    0           0
rejection the store decides   1           1 (2 off the leader)
============================  ==========  ====================

Where the difference comes from
-------------------------------
**Ownership removes the read -- from every answer but a refusal.** The etcd
backend validates against a local store that may be behind, so it fences before
it trusts a rejection. Here exactly one member is responsible for a Node's
subtree, so a registration that passes validation is simply proposed: apply,
not the proposer, decides whether it creates, updates or is refused
(``machine.py``), and nothing stale can commit. A *refusal* is different,
because the refusal is the answer -- a 400 is terminal, something a Node "MUST
NOT" retry, and a 404 on heartbeat makes it re-register everything -- and an
owner's store is current only as of what it has applied: one restarted with
nothing validates against an empty store. So a refusal the store decides, and a
404 from a delete or a heartbeat, is given only after a read barrier
(``_read_barrier``): one quorum round, on those paths alone.

**Apply removes the second wait.** The etcd backend commits to etcd and then
waits for its own write to come back down the watch stream before it can
answer. Here the commit *is* applied by this member, in the same step, and the
caller's future is resolved from inside that apply -- so there is nothing to
wait for afterwards.

**Leases stop being writes.** A heartbeat is ``store.heartbeat`` on the owner
and nothing else: no entry, no consensus round, no network. What it costs
instead is that liveness is decided by the owner rather than by a cluster-wide
lease, which is why expiry is proposed rather than evaluated independently on
every member.

Where it is *worse*, honestly
-----------------------------
A mutation that arrives at a member which does not own the Node costs a hop to
the owner plus the commit -- two or three round trips against etcd's two. In
practice a Node registers with one registry and stays there, so this is the
rare path, but it is a real cost and it is why ``_forward`` exists rather than
"just claim it": claiming on every stray request would make two members fight
over a Node while a load balancer spread its traffic.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nmos.raft.errors import RaftCursorReservationFailed, RaftUnavailable
from nmos.raft.messages import Forward, ForwardReply
from nmos.raft.node import RaftNode, Role
from nmos.raft.operations import (
    ExpireOp,
    ForgetOp,
    ProposalId,
    RegisterOp,
    RegistryOperation,
    UnregisterOp,
)
from nmos.registry.backend import BackendState, MutationUnavailable
from nmos.registry.metrics import Event, RegistryMetrics
from nmos.registry.registry import Registry
from nmos.registry.store import health_now
from nmos.registry.types import (
    Body,
    RegistrationError,
    RegistrationResult,
    ResourceType,
)

if TYPE_CHECKING:
    from nmos.registry.distributed import RaftConfig

log = logging.getLogger(__name__)


class _NodeGate:
    """One in-flight proposal per Node subtree.

    This is what makes local validation sound. ``store.prepare`` is run against
    the current store and the result proposed; if a second registration for the
    same Node were validated before the first had applied, it would be deciding
    against a store missing its own predecessor -- a Sender validated before its
    Device landed would be rejected for a parent that was moments away.

    Per Node, not global: registrations for *different* Nodes are independent
    and must stay concurrent, because a facility powering up is hundreds of
    Nodes at once and serialising them all would give back exactly the
    throughput this design exists to gain.
    """

    __slots__ = ("_locks",)

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def lock(self, node_id: str) -> asyncio.Lock:
        existing = self._locks.get(node_id)
        if existing is None:
            existing = asyncio.Lock()
            self._locks[node_id] = existing
        return existing

    def forget(self, node_id: str) -> None:
        lock = self._locks.get(node_id)
        if lock is not None and not lock.locked():
            del self._locks[node_id]


@dataclass(frozen=True)
class _Beat:
    """What a heartbeat found at the member it reached (``_beat_here``)."""

    health: int | None
    """The Node's new health, or ``None``: this member does not hold it."""
    owner: int | None
    """Another member found to own the Node once this one was current."""
    trips: int
    """Round trips spent making sure (the read barrier's)."""


class RaftRegistryBackend:
    """Registry storage backed by the in-process raft cluster.

    Args:
        registry: The local registry this mutates and publishes from.
        config: Validated raft configuration.
        node: The consensus member. Constructed by the caller so that the
            transport, term store and state machine are wired once, in one
            place, rather than half here and half there.
        metrics: Shared with the registry's own buffer, so one dump interleaves
            consensus timings with query and fan-out timings. Splitting them
            would make "is this cost ours or the cluster's?" unanswerable from
            either.
    """

    def __init__(
        self,
        registry: Registry,
        config: RaftConfig,
        node: RaftNode,
        *,
        metrics: RegistryMetrics | None = None,
    ) -> None:
        self._registry = registry
        self._config = config
        self._node = node
        self._metrics = metrics if metrics is not None else registry.metrics
        self._gate = _NodeGate()
        self._last_seen: dict[str, float] = {}
        self._started = False
        self._stopping = False
        self._reported = BackendState.STARTING

    # -- introspection ---------------------------------------------------

    @property
    def state(self) -> BackendState:
        """Derived on every read, never cached.

        A cached state has to be refreshed by something, and whatever that
        something is will eventually not run. The first version of this cached
        it and refreshed it on start and on each mutation -- so a member that
        started before its peers waited out its timeout, went DEGRADED, and
        then reported DEGRADED *forever*, because nothing wrote to it and
        nothing else looked. Its Registration API answered 503 on a cluster
        that was perfectly healthy.

        Reading it from the consensus layer each time costs two attribute
        loads and cannot go stale.
        """
        if self._stopping or self._node.failure is not None:
            # A member that stopped itself on a broken invariant is going away
            # (``RaftNode._fail``): its process is about to exit, and until it
            # does, a mutation is answered 503 so the Node retries elsewhere.
            return BackendState.STOPPING
        if not self._started:
            return BackendState.STARTING
        if self._node.leader is not None and self._node.has_quorum:
            return BackendState.READY
        return BackendState.DEGRADED

    @property
    def metrics(self) -> RegistryMetrics:
        return self._metrics

    @property
    def node(self) -> RaftNode:
        return self._node

    def _log_state(self) -> None:
        """Log a transition, once, without the state depending on being asked."""
        current = self.state
        if current is self._reported:
            return
        if current is BackendState.READY:
            log.info("registry: raft backend ready")
        elif current is BackendState.DEGRADED:
            log.warning(
                "registry: raft backend degraded (leader=%s quorum=%s)",
                self._node.leader, self._node.has_quorum,
            )
        self._reported = current

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        self._node.set_forward_handler(self._on_forward)
        await self._node.start()
        self._started = True
        try:
            await self._node.wait_for_leader(
                timeout=self._config.mutation_timeout,
            )
        except RaftUnavailable:
            # Not fatal, and not sticky. A cluster whose other members have not
            # started yet is ordinary; DEGRADED means "Query serves,
            # Registration answers 503", which is right while an election runs
            # and stops being right the moment one finishes -- which is why
            # ``state`` is derived rather than set here.
            log.warning(
                "registry: no leader yet; serving queries and refusing "
                "registrations until one is elected",
            )
        self._log_state()

    async def close(self) -> None:
        self._stopping = True
        await self._node.close()

    # -- registration ----------------------------------------------------

    async def register(
        self, resource_type: ResourceType, body: Body,
    ) -> RegistrationResult:
        return await self._register(resource_type, body, route_again=True)

    async def _register(
        self, resource_type: ResourceType, body: Body, *, route_again: bool,
    ) -> RegistrationResult:
        """``register``, told whether a moved Node may still be routed again.

        ``route_again`` is spent by the one retry ``_forward_register`` makes
        when the member it forwarded to no longer owns the Node.
        """
        node_id = self._resolve_node(resource_type, body.data)
        # Round trips spent making sure of the parent, counted with the rest.
        barrier = 0
        if isinstance(node_id, RegistrationResult) and _decided_by_state(node_id):
            # A parent this member has not yet applied is not a missing one:
            # see ``_read_barrier``.
            await self._read_barrier()
            barrier = self._barrier_trips()
            node_id = self._resolve_node(resource_type, body.data)
        if isinstance(node_id, RegistrationResult):
            self._metrics.record(
                Event.MUTATION, None, units=barrier, verb="register",
                outcome="local",
            )
            return node_id

        with self._metrics.timer(
            Event.MUTATION, verb="register", type=resource_type.value,
        ) as timer:
            owner = self._owner_for(node_id)
            if owner is not None and owner != self._index:
                trips, result = await self._forward_register(
                    owner, resource_type, body, node_id,
                    route_again=route_again,
                )
                timer.count(barrier + trips)
                return result

            async with self._gate.lock(node_id):
                result, trips = await self._register_as_owner(
                    resource_type, body, node_id, claim=owner is None,
                )
            timer.count(barrier + trips)
            return result

    async def _register_as_owner(
        self, resource_type: ResourceType, body: Body, node_id: str, *,
        claim: bool,
    ) -> tuple[RegistrationResult, int]:
        store = self._registry.store

        prepared = store.prepare(resource_type, body.data)
        barrier = 0
        if isinstance(prepared, RegistrationResult) and _decided_by_state(prepared):
            # Validated against a store that may be behind. It was once
            # returned at once, as "authoritative, and free" because this member
            # owns the Node -- true only of an owner that has applied everything
            # committed, and an owner can lag like any member (one restarted
            # with nothing validates against an empty store). So this member
            # catches up to a read index first, exactly as the etcd backend
            # fences before it dares return a terminal 400, and decides again.
            await self._read_barrier()
            barrier = self._barrier_trips()
            prepared = store.prepare(resource_type, body.data)
        if isinstance(prepared, RegistrationResult):
            return prepared, barrier

        cursors = self._cursors_for(resource_type, prepared.resource_id)
        operation = RegisterOp(
            proposal=ProposalId(member=self._index, sequence=0),
            resource_type=resource_type,
            resource_id=prepared.resource_id,
            node_id=node_id,
            body_text=body.text,
            created=cursors[0],
            updated=cursors[1],
            # Read once, here, and carried: every member must stamp the same
            # health or they diverge on the very next status line.
            health=health_now(),
            expect_created=prepared.creates,
            claim_owner=self._index if claim else None,
        )
        result = await self._commit(operation, f"registration of {prepared.resource_id}")
        trips = 1 if self._node.role is Role.LEADER else 2
        return result, barrier + trips

    async def _forward_register(
        self, owner: int, resource_type: ResourceType, body: Body,
        node_id: str, *, route_again: bool,
    ) -> tuple[int, RegistrationResult]:
        reply = await self._forward(Forward(
            verb="register", resource_type=resource_type.value,
            resource_id=str(body.data.get("id", "")),
            body_text=body.text, request_id=0,
        ), owner, node_id)

        if reply is None:
            raise MutationUnavailable(
                f"the member owning node {node_id} did not answer",
            )
        if reply.not_owner:
            # Ownership moved underneath us. One retry, as owner or forwarder
            # depending on where it moved to -- and no more, because a request
            # that keeps chasing an owner is a request that never answers.
            #
            # "No more" has to be enforced, not merely intended: retrying
            # through ``register`` again, with nothing spent, recursed for as
            # long as this member's ownership table stayed behind -- and a
            # table that is behind names the same former owner every time, so
            # the retry forwards to the member that has just said no. Measured
            # over real sockets: 490 forwards in 0.4s, ended only by
            # ``RecursionError`` (reported by the transport as a failed link).
            # The same recursion aborts the Rust process on a stack overflow.
            # After the one retry this is a 503: the tables converge, and a
            # Node retries a 503.
            if not route_again:
                raise MutationUnavailable(
                    f"member {owner} no longer owns node {node_id}, and this "
                    f"member has not yet learned which does",
                )
            return 3, await self._register(
                resource_type, body, route_again=False,
            )

        await self._await_applied(reply.applied_index)
        return (3 if self._node.role is not Role.LEADER else 2), _result_of(reply)

    # -- deletion --------------------------------------------------------

    async def unregister(
        self, resource_type: ResourceType, resource_id: str,
    ) -> bool:
        with self._metrics.timer(
            Event.MUTATION, verb="unregister", type=resource_type.value,
        ) as timer:
            existing = self._registry.store.get(resource_type, resource_id)
            if existing is None:
                # "Not here" is a guess until this member is current: its
                # store is a complete replica only of what it has applied. See
                # ``_read_barrier``.
                await self._read_barrier()
                existing = self._registry.store.get(resource_type, resource_id)
                if existing is None:
                    timer.count(self._barrier_trips())
                    return False

            operation = UnregisterOp(
                proposal=ProposalId(member=self._index, sequence=0),
                resource_type=resource_type,
                resource_id=resource_id,
            )
            outcome = await self._commit(
                operation, f"delete of {resource_id}", raw=True,
            )
            timer.count(1 if self._node.role is Role.LEADER else 2)
            return bool(outcome)

    # -- liveness --------------------------------------------------------

    async def heartbeat(self, node_id: str) -> int | None:
        """Refresh a Node's liveness. Zero round trips on its owner.

        The property this preserves is the one the etcd backend's lease design
        argues for: 100 Nodes beating every 5 s must not become 100 consensus
        rounds per second. Here it is stronger -- the beat writes nothing at
        all, not even a lease renewal.
        """
        return await self._heartbeat(node_id, route_again=True)

    async def _heartbeat(self, node_id: str, *, route_again: bool) -> int | None:
        """``heartbeat``, told whether a moved Node may still be routed again.

        Routed exactly as ``_register`` is: forwarded to the Node's owner, and
        ``route_again`` spent by the one retry ``_forward_heartbeat`` makes when
        that member no longer owns it.
        """
        with self._metrics.timer(Event.MUTATION, verb="heartbeat") as timer:
            owner = self._owner_for(node_id)
            if owner is not None and owner != self._index:
                timer.count(1)
                return await self._forward_heartbeat(
                    owner, node_id, route_again=route_again,
                )
            beat = await self._beat_here(node_id)
            if beat.owner is not None:
                timer.count(beat.trips + 1)
                return await self._forward_heartbeat(
                    beat.owner, node_id, route_again=route_again,
                )
            timer.count(beat.trips)
            return beat.health

    async def _beat_here(self, node_id: str) -> _Beat:
        """Refresh a Node this member takes to be its own -- or find it is not.

        What both a Node's own heartbeat and a forwarded one do at the member
        they reach, so the two cannot drift.
        """
        trips = 0
        if self._registry.store.get(ResourceType.NODE, node_id) is None:
            # A 404 tells the Node to re-register everything
            # (``Behaviour - Registration.md:112-114``), so it has to be true:
            # see ``_read_barrier``. Once current, the Node may turn out to be
            # another member's.
            await self._read_barrier()
            trips = self._barrier_trips()
            owner = self._owner_for(node_id)
            if owner is not None and owner != self._index:
                return _Beat(health=None, owner=owner, trips=trips)
            if self._registry.store.get(ResourceType.NODE, node_id) is None:
                return _Beat(health=None, owner=None, trips=trips)
        self._last_seen[node_id] = asyncio.get_running_loop().time()
        return _Beat(
            health=self._registry.store.heartbeat(node_id), owner=None,
            trips=trips,
        )

    async def _forward_heartbeat(
        self, owner: int, node_id: str, *, route_again: bool,
    ) -> int | None:
        """A heartbeat for another member's Node, answered by that member."""
        reply = await self._forward(Forward(
            verb="heartbeat", resource_type="node",
            resource_id=node_id, body_text="", request_id=0,
        ), owner, node_id)
        if reply is None:
            # No answer is not "no such Node": the owner may well hold it. This
            # used to be a 404 -- a terminal instruction to re-register
            # everything, given because a link was slow.
            raise MutationUnavailable(
                f"the member owning node {node_id} did not answer",
            )
        if reply.not_owner:
            # Ownership moved underneath us, as it can for a registration
            # (``_forward_register``): one retry, as owner or forwarder
            # depending on where it moved to, and no more -- a table that is
            # behind names the same former owner every time. Read as a plain
            # refusal, as it once was, this was a 404: the terminal
            # "re-register every resource", for a Node the cluster still held.
            if not route_again:
                raise MutationUnavailable(
                    f"member {owner} no longer owns node {node_id}, and this "
                    f"member has not yet learned which does",
                )
            return await self._heartbeat(node_id, route_again=False)
        if not reply.ok:
            if reply.error == "unavailable":
                raise MutationUnavailable(
                    reply.detail or f"member {owner} could not answer",
                )
            return None
        return int(reply.applied_index) or health_now()

    async def collect_garbage(self) -> int:
        """Expire silent Nodes this member owns; forget tombstones everywhere.

        Expiry is **owner-decided and replicated**, not evaluated independently
        on every member. Independent evaluation is how members end up
        disagreeing about which Nodes are alive, and the member with the
        slowest clock resurrects resources the others have collected.

        Forgetting is local and unreplicated, exactly as in the etcd backend:
        it drops records that are already non-extant, so it cannot resurrect
        anything, cannot remove anything a peer still considers live, and emits
        no grains. The count returned is the *expiry* count, because stage two
        is invisible to every client.
        """
        if self.state is not BackendState.READY:
            return 0

        store = self._registry.store
        expired = 0
        threshold = health_now() - int(store.gc_interval)

        for node in list(store.iter_extant(ResourceType.NODE)):
            if node.health >= threshold:
                continue
            if not self._owns(node.id):
                continue
            try:
                await self._commit(
                    ExpireOp(
                        proposal=ProposalId(member=self._index, sequence=0),
                        node_id=node.id,
                    ),
                    f"expiry of {node.id}", raw=True,
                )
                expired += 1
            except MutationUnavailable:
                # The cluster cannot commit right now. The Node stays
                # registered and the next pass tries again, which is better
                # than removing it locally and diverging.
                break

        victims = store.forgettable()
        if victims and self._node.role is Role.LEADER:
            try:
                await self._commit(
                    ForgetOp(
                        proposal=ProposalId(member=self._index, sequence=0),
                        victims=tuple(victims),
                    ),
                    "forgetting tombstones", raw=True,
                )
            except MutationUnavailable:
                pass

        return expired

    # -- forwarding, as the receiver --------------------------------------

    async def _heartbeat_forwarded(self, message: Forward) -> ForwardReply:
        """Answer a forwarded heartbeat as its Node's owner, or say it is not.

        Never forwarded on, as a forwarded registration never is (below): a
        request that hops between members has no bound on its latency, and two
        members whose tables disagree about the owner handed a heartbeat back
        and forth until an RPC deadline cut the chain. This was answered by the
        member's own ``heartbeat``, which forwards.
        """
        owner = self._owner_for(message.resource_id)
        if owner is None or owner == self._index:
            beat = await self._beat_here(message.resource_id)
            owner = beat.owner
            if owner is None:
                return ForwardReply(
                    ok=beat.health is not None, created=False, error="",
                    detail="", applied_index=beat.health or 0, not_owner=False,
                    request_id=message.request_id, owner=None,
                )
        return ForwardReply(
            ok=False, created=False, error="", detail="", applied_index=0,
            not_owner=True, request_id=message.request_id, owner=owner,
        )

    async def _on_forward(self, message: Forward) -> ForwardReply:
        """Answer a mutation another member handed us because we own its Node."""
        try:
            resource_type = ResourceType(message.resource_type)
        except ValueError:
            return ForwardReply(
                ok=False, created=False, error="schema",
                detail=f"unknown resource type {message.resource_type!r}",
                applied_index=0, not_owner=False,
                request_id=message.request_id, owner=None,
            )

        try:
            if message.verb == "heartbeat":
                return await self._heartbeat_forwarded(message)

            body = Body(message.body_text)
            node_id = self._resolve_node(resource_type, body.data)
            if isinstance(node_id, RegistrationResult) and _decided_by_state(node_id):
                # See ``_register``: this member may be behind as well.
                await self._read_barrier()
                node_id = self._resolve_node(resource_type, body.data)
            if isinstance(node_id, RegistrationResult):
                return _reply_for(node_id, 0, message.request_id)
        except MutationUnavailable as error:
            # Answered, not raised, for the reason given below.
            return ForwardReply(
                ok=False, created=False, error="unavailable",
                detail=str(error), applied_index=0, not_owner=False,
                request_id=message.request_id, owner=None,
            )

        owner = self._owner_for(node_id)
        if owner is not None and owner != self._index:
            # It moved on. Say so rather than forwarding again: a request that
            # hops between members is a request with no bound on its latency.
            return ForwardReply(
                ok=False, created=False, error="", detail="",
                applied_index=0, not_owner=True,
                request_id=message.request_id, owner=owner,
            )

        try:
            async with self._gate.lock(node_id):
                result, _trips = await self._register_as_owner(
                    resource_type, body, node_id, claim=owner is None,
                )
        except MutationUnavailable as error:
            # This member owns the Node but could not commit. Say so at once,
            # with the reason, under the code every refusal of this kind uses
            # (``RaftNode.on_forward``, ``transport._application_refusal``) and
            # which the forwarder answers as a 503 -- see ``_result_of``.
            #
            # Raising instead lost the answer: the transport serves this on a
            # task with nobody to hand an exception to, so no reply was written
            # and the forwarder waited out its whole deadline to report "did
            # not answer". Measured over real sockets: the owner failed with
            # "no leader elected" in 0.00s, the forwarder answered after the
            # full 2.00s mutation timeout.
            return ForwardReply(
                ok=False, created=False, error="unavailable",
                detail=str(error), applied_index=0, not_owner=False,
                request_id=message.request_id, owner=None,
            )
        return _reply_for(result, self._node.last_applied, message.request_id)

    async def _forward(
        self, message: Forward, owner: int, node_id: str,
    ) -> ForwardReply | None:
        from nmos.raft.transport import Transport

        transport: Transport = self._node.transport
        try:
            reply: ForwardReply = await transport.request(
                owner, message, timeout=self._config.mutation_timeout,
            )
        except RaftUnavailable:
            return None
        return reply

    # -- helpers ---------------------------------------------------------

    @property
    def _index(self) -> int:
        return self._node.index

    def _owns(self, node_id: str) -> bool:
        return self._owner_for(node_id) == self._index

    def _owner_for(self, node_id: str) -> int | None:
        """Who owns this Node, or None when it is free to claim.

        A Node owned by a member this one cannot reach reads as unowned at
        once, so whichever member it re-registers with takes it over. There is
        no grace period, and none is wanted:

        * a claim rides the registration's own proposal (``claim_owner``), so
          taking a Node over adds no consensus round;
        * ownership moves only when the owner is unreachable from the member a
          request reached -- a load balancer spreading a Node's traffic over
          members that can reach its owner forwards, it does not claim;
        * for as long as a grace lasted, every request for the Node at another
          member would go to an owner nobody can reach and be answered 503;
        * and a Node whose owner died must find a new one before the 12 s
          collection (``Behaviour - Registration.md:47``) removes it, which
          immediate takeover serves best.

        A move is safe whenever it happens: apply, not the proposer, decides
        whether a registration creates or updates (``machine.py``).
        """
        held = self._node.ownership.owner_of(node_id)
        if held is None:
            return None
        if held.owner == self._index:
            return self._index
        if held.owner in self._node.live_peers:
            return held.owner
        return None

    def _resolve_node(
        self, resource_type: ResourceType, raw: dict[str, Any],
    ) -> str | RegistrationResult:
        """Which Node's subtree this resource belongs to.

        Mirrors what the etcd backend's ``_placement`` does, minus the key
        construction: a Node is its own, a Device names one, and everything
        else inherits its Device's -- which is looked up locally. A Device
        absent here is ``PARENT_MISSING`` only if this member is current, which
        is why every caller takes a read barrier before believing it
        (``_read_barrier``).
        """
        if resource_type is ResourceType.NODE:
            node_id = raw.get("id")
            if not isinstance(node_id, str):
                return RegistrationResult.failure(
                    RegistrationError.SCHEMA, "node has no id",
                )
            return node_id

        if resource_type is ResourceType.DEVICE:
            node_id = raw.get("node_id")
            if not isinstance(node_id, str):
                return RegistrationResult.failure(
                    RegistrationError.SCHEMA, "device has no node_id",
                )
            return node_id

        device_id = raw.get("device_id")
        if not isinstance(device_id, str):
            return RegistrationResult.failure(
                RegistrationError.SCHEMA,
                f"{resource_type.value} has no device_id",
            )
        device = self._registry.store.get(ResourceType.DEVICE, device_id)
        if device is None:
            return RegistrationResult.failure(
                RegistrationError.PARENT_MISSING,
                f"device {device_id} is not registered",
            )
        return device.parent_id or ""

    def _cursors_for(
        self, resource_type: ResourceType, resource_id: str,
    ) -> tuple[Any, Any]:
        """``(created, updated)``. ``created`` is stable across updates.

        Same rule and the same reason as the etcd backend: a client paging by
        creation order must not see a resource move because it was updated.

        Raises:
            MutationUnavailable: The cursor's reservation could not be made
                durable (``RaftNode.allocate_cursor``). Nothing was proposed,
                so the Node's retry starts clean.
        """
        try:
            updated = self._node.allocate_cursor(resource_type)
        except RaftCursorReservationFailed as exc:
            raise MutationUnavailable(str(exc)) from exc
        existing = self._registry.store.get(
            resource_type, resource_id, include_non_extant=True,
        )
        created = (
            existing.created if existing is not None and existing.extant
            else updated
        )
        return created, updated

    async def _commit(
        self, operation: RegistryOperation, what: str, *, raw: bool = False,
    ) -> Any:
        """Propose, wait for the apply, and translate failure into 503."""
        try:
            outcome = await asyncio.wait_for(
                self._node.propose(operation),
                self._config.mutation_timeout,
            )
        except RaftUnavailable as exc:
            self._log_state()
            raise MutationUnavailable(f"{what} could not commit: {exc}") from exc
        except asyncio.TimeoutError as exc:
            self._log_state()
            raise MutationUnavailable(
                f"{what} did not commit within "
                f"{self._config.mutation_timeout:.1f}s",
            ) from exc
        return outcome.result if not raw else outcome.result

    async def _read_barrier(self) -> None:
        """Bring this member's store up to everything committed when a read began.

        A member answers some requests from its own store -- a refusal from
        validation, a 404 for a delete or a heartbeat, a Node free to claim --
        and a store can be behind what is committed: a follower that has not
        yet applied, an owner restarted with nothing. Those answers were given
        as if the store were current, and a client acts on them: a Node told
        400 must not retry, one told 404 re-registers everything. The chaos
        soak counted them in hundreds per run set, each unjustifiable.

        So before such an answer this member learns a read index -- the commit
        index when the read began, confirmed by a quorum that its leader still
        leads (etcd's ReadIndex, ``RaftNode.read_index``) -- and waits until it
        has applied it. The answer it then gives is the one the leader would
        have given. Only those answers pay: a registration that commits, and a
        heartbeat that finds its Node, pay nothing.

        Raises ``MutationUnavailable`` when no read index can be had in time: a
        member that cannot show it is current answers 503, which a Node
        retries, rather than a terminal answer it cannot justify.
        """
        try:
            index = await self._node.read_index(
                timeout=self._config.mutation_timeout,
            )
        except RaftUnavailable as exc:
            raise MutationUnavailable(
                f"this member cannot confirm it is current: {exc}",
            ) from exc
        await self._await_applied(index)

    def _barrier_trips(self) -> int:
        """Network round trips a read barrier costs: the leader's quorum
        round, and the ask on the way to it from a follower."""
        return 1 if self._node.role is Role.LEADER else 2

    async def _await_applied(self, index: int) -> None:
        """Wait until this member has applied ``index``.

        Two waits use it. On the forwarded path, the member that answered the
        client is not the member that applied the entry, and a client that
        immediately reads back from here would otherwise get a 404 for
        something it was just told was created. And a read barrier
        (``_read_barrier``) waits here for its read index.
        """
        if index <= 0:
            return
        try:
            await self._node.fence.wait(
                index, timeout=self._config.mutation_timeout,
            )
        except Exception as exc:
            raise MutationUnavailable(
                f"this member did not catch up to index {index}",
            ) from exc


def _decided_by_state(result: RegistrationResult) -> bool:
    """Does this refusal depend on what the store holds?

    Every refusal but a malformed body (``SCHEMA``, ``Behaviour -
    Registration.md:100``), which is decided by the body alone: the others --
    an id of another type, an older version, a changed or missing parent
    (``:101-104``) -- are only as true as the store they were read from.
    """
    return result.error is not None and result.error is not RegistrationError.SCHEMA


def _result_of(reply: ForwardReply) -> RegistrationResult:
    if reply.ok:
        return RegistrationResult(created=reply.created, events=[])
    try:
        error = RegistrationError(reply.error)
    except ValueError:
        raise MutationUnavailable(
            reply.detail or "the owning member refused the registration",
        )
    return RegistrationResult.failure(error, reply.detail)


def _reply_for(
    result: RegistrationResult, applied: int, request_id: int,
) -> ForwardReply:
    return ForwardReply(
        ok=result.ok,
        created=result.created,
        error=result.error.value if result.error else "",
        detail=result.detail,
        applied_index=applied,
        not_owner=False,
        request_id=request_id,
        owner=None,
    )
