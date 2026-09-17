# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The raft-backed distributed backend: one round trip, and no read before it.

Same seam as the etcd backend -- four methods and a state property, with Query
untouched -- and a materially different path underneath. The comparison is the
point, so it is worth stating in the terms the benchmark measures:

============================  ==========  =========
operation                     etcd        raft
============================  ==========  =========
registration, steady state    2           **1**
first registration of a Node  3           **1**
heartbeat                     1           **0**
rejection decided locally     0           0
============================  ==========  =========

Where the difference comes from
-------------------------------
**Ownership removes the read.** The etcd backend validates against a local
store that may be behind, so a rejection it produces might be a lie -- and a
400 is terminal, something a Node "MUST NOT" retry. It therefore cannot answer
*any* rejection without a linearizable read first. Here exactly one member is
responsible for a Node's subtree, so that member's view of the subtree is
authoritative by construction and the parent/version checks are decided
locally, with no round trip at all.

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
from typing import TYPE_CHECKING, Any

from nmos.raft.errors import RaftUnavailable
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

# How long a Node may remain owned by a member nobody can reach before another
# member takes it over. Deliberately below the 12 s garbage-collection interval
# of ``Behaviour - Registration.md:47``: a Node whose owner died must find a new
# one before its resources would otherwise be collected.
OWNERSHIP_GRACE_S = 6.0


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
        if self._stopping:
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
        node_id = self._resolve_node(resource_type, body.data)
        if isinstance(node_id, RegistrationResult):
            self._metrics.record(
                Event.MUTATION, None, units=0, verb="register",
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
                )
                timer.count(trips)
                return result

            async with self._gate.lock(node_id):
                result, trips = await self._register_as_owner(
                    resource_type, body, node_id, claim=owner is None,
                )
            timer.count(trips)
            return result

    async def _register_as_owner(
        self, resource_type: ResourceType, body: Body, node_id: str, *,
        claim: bool,
    ) -> tuple[RegistrationResult, int]:
        store = self._registry.store

        prepared = store.prepare(resource_type, body.data)
        if isinstance(prepared, RegistrationResult):
            # Authoritative, and free. The etcd backend cannot do this: its
            # store may be behind, so it must fence before it dares return a
            # terminal 400. Ownership is what makes the same answer safe here
            # without touching the network.
            return prepared, 0

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
        return result, trips

    async def _forward_register(
        self, owner: int, resource_type: ResourceType, body: Body,
        node_id: str,
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
            return 3, await self.register(resource_type, body)

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
                # A 404 costs nothing: the local store is a complete replica,
                # so "not here" is not a guess.
                timer.count(0)
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
        with self._metrics.timer(Event.MUTATION, verb="heartbeat") as timer:
            owner = self._owner_for(node_id)
            if owner is not None and owner != self._index:
                reply = await self._forward(Forward(
                    verb="heartbeat", resource_type="node",
                    resource_id=node_id, body_text="", request_id=0,
                ), owner, node_id)
                timer.count(1)
                if reply is None or not reply.ok:
                    return None
                return int(reply.applied_index) or health_now()

            timer.count(0)
            if self._registry.store.get(ResourceType.NODE, node_id) is None:
                return None
            self._last_seen[node_id] = asyncio.get_running_loop().time()
            return self._registry.store.heartbeat(node_id)

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

        if message.verb == "heartbeat":
            health = await self.heartbeat(message.resource_id)
            return ForwardReply(
                ok=health is not None, created=False, error="", detail="",
                applied_index=health or 0, not_owner=False,
                request_id=message.request_id, owner=None,
            )

        body = Body(message.body_text)
        node_id = self._resolve_node(resource_type, body.data)
        if isinstance(node_id, RegistrationResult):
            return _reply_for(node_id, 0, message.request_id)

        owner = self._owner_for(node_id)
        if owner is not None and owner != self._index:
            # It moved on. Say so rather than forwarding again: a request that
            # hops between members is a request with no bound on its latency.
            return ForwardReply(
                ok=False, created=False, error="", detail="",
                applied_index=0, not_owner=True,
                request_id=message.request_id, owner=owner,
            )

        async with self._gate.lock(node_id):
            result, _trips = await self._register_as_owner(
                resource_type, body, node_id, claim=owner is None,
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

        A Node owned by a member that has been unreachable for longer than the
        grace period reads as unowned, so whichever member it re-registers with
        can take over. The grace is what stops two members trading a Node back
        and forth while a load balancer spreads its traffic -- without it,
        every request would claim, and every claim would be a consensus round.
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
        else inherits its Device's -- which is looked up locally, and a Device
        that is genuinely absent is a genuine ``PARENT_MISSING`` decided by the
        same store rule that governs it in standalone mode.
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
        """
        updated = self._node.cursors.allocate(resource_type)
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

    async def _await_applied(self, index: int) -> None:
        """Wait until this member has applied ``index``.

        The one wait that survives ownership, and only on the forwarded path:
        the member that answered the client is not the member that applied the
        entry, and a client that immediately reads back from here would
        otherwise get a 404 for something it was just told was created.
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
