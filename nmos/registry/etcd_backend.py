# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The distributed backend: etcd is authoritative, the local store is a read model.

Shape of the thing
------------------
Every registry in the cluster keeps a complete local ``RegistryStore`` and
serves Query entirely from it -- no etcd access on the read path at all, which
is why Query performance is identical in standalone and distributed mode. That
store is fed by exactly one thing: the etcd watch.

    preload at fixed revision R  ->  install store  ->  watch from R+1
                                                          |
                                       every revision applied in order,
                                       grains queued in the same step,
                                       fence advanced afterwards

**The watch is the only writer**, including for changes this member itself
committed. That is what removes the entire class of bug the legacy dRDS carried an
origin-index byte to work around -- and still got wrong for deletes, because an
etcd DELETE event has no value to read the byte from. Here a local commit and a
remote commit take the identical path, so there is nothing to suppress, nothing
to deduplicate, and no way for the two to diverge.

Preload is pinned to one revision
---------------------------------
The snapshot is read page by page, but every page after the first is read at
*exactly* the revision the first page reported. Without that, a resource
deleted between page 1 and page 5 would be missing from a snapshot that also
contained its children, and a resource created during the scan could appear
twice or not at all.

Failure is never silent
-----------------------
A corrupt envelope, an orphaned resource, a key that does not parse -- none of
these are skipped. The candidate store is discarded and the previous view keeps
serving. A member that cannot agree with its peers about what exists must not
quietly serve its own version of the truth.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from dataclasses import dataclass

from nmos.registry.backend import BackendState, MutationUnavailable
from nmos.registry.fence import FenceTimeout, RevisionFence
from nmos.registry.keys import (
    ENVELOPE_VERSION,
    Envelope,
    KeyError_,
    Namespace,
    ParsedKey,
)
from nmos.registry.metrics import Event, RegistryMetrics
from nmos.registry.registry import Registry
from nmos.registry.store import RegistryStore, health_now
from nmos.registry.types import (
    RegistrationError,
    RegistrationResult,
    ResourceEvent,
    ResourceType,
    TaiCursor,
)

if TYPE_CHECKING:
    from nmos.etcd.channel import EtcdChannelPool
    from nmos.etcd.kv import EtcdKV
    from nmos.etcd.lease import EtcdLease
    from nmos.etcd.watch import EtcdWatch, RevisionBatch
    from nmos.registry.distributed import DistributedConfig

log = logging.getLogger(__name__)

# Page size for the preload scan. Small enough that one response stays well
# inside etcd's message limits even when every resource is large, big enough
# that a few thousand resources load in a handful of round trips.
PRELOAD_PAGE = 100

# How long to wait before retrying a watch that dropped. Short, because until
# it reconnects this member's view is frozen and Registration is DEGRADED.
_WATCH_RETRY_INITIAL = 0.25
_WATCH_RETRY_MAX = 5.0


def _fast_path_enabled() -> bool:
    """Whether the speculative CAS of §10.2.1 is attempted.

    Off via ``NMOS_ETCD_FAST_PATH=0``. An environment variable rather than a
    flag because it is a measurement and diagnosis switch, not a deployment
    choice: the benchmark runs the same workload with it forced on and forced
    off, and if the gap is not roughly the one round trip it is supposed to
    save, the optimisation is not earning its complexity and should go.

    Turning it off is always *safe* -- it only means every mutation takes the
    fenced path, which is the path a fast-path miss falls back to anyway.
    """
    import os

    return os.environ.get("NMOS_ETCD_FAST_PATH", "1").strip() not in {
        "0", "false", "no", "off",
    }


class EtcdRegistryBackend:
    """Registry storage backed by an etcd cluster.

    Args:
        registry: The local registry whose store this feeds.
        config: Validated distributed configuration.
        metrics: Where timings and their inputs are recorded. Always on -- the
            failures worth diagnosing here do not reproduce on demand.
    """

    def __init__(
        self,
        registry: Registry,
        config: DistributedConfig,
        *,
        metrics: RegistryMetrics | None = None,
    ) -> None:
        self._registry = registry
        self._config = config
        # Defaults to the registry's own buffer rather than a private one, so
        # a single dump interleaves the etcd-side samples (CAS, fence wait,
        # commit-to-watch) with the local ones (query, fan-out). Splitting
        # them across two buffers would make "is this cost ours or etcd's?"
        # unanswerable from either.
        self._metrics = metrics if metrics is not None else registry.metrics
        self._namespace = Namespace(config.namespace)

        self._state = BackendState.STARTING
        self._fence = RevisionFence(applied=0)

        self._pool: EtcdChannelPool | None = None
        self._kv: EtcdKV | None = None
        self._lease: EtcdLease | None = None
        self._watch: EtcdWatch | None = None
        self._watch_task: asyncio.Task[None] | None = None
        self._stopping = False
        # Which member the watch is currently pinned to, as an index into the
        # pool's endpoints. Index 0 is the local member, so the first attempt
        # always prefers it; this only advances when a connection fails.
        self._watch_endpoint = 0

        # What the watch last told us each key's mod_revision is. This is the
        # "believed" state the fast path builds its comparisons from -- a stale
        # entry cannot commit anything wrong, it just fails the compare.
        self._revisions: dict[bytes, int] = {}
        # node id -> lease id. Learned from etcd (every key carries its lease),
        # so a member can renew and attach to a lease another member granted.
        self._leases: dict[str, int] = {}
        self._fast_path = _fast_path_enabled()

    # -----------------------------------------------------------------------
    # Introspection
    # -----------------------------------------------------------------------

    @property
    def state(self) -> BackendState:
        return self._state

    @property
    def metrics(self) -> RegistryMetrics:
        return self._metrics

    @property
    def fence(self) -> RevisionFence:
        return self._fence

    @property
    def namespace(self) -> Namespace:
        return self._namespace

    @property
    def kv(self) -> EtcdKV:
        if self._kv is None:
            raise RuntimeError("backend has not been started")
        return self._kv

    @property
    def lease(self) -> EtcdLease:
        if self._lease is None:
            raise RuntimeError("backend has not been started")
        return self._lease

    def _set_state(self, state: BackendState) -> None:
        if state is self._state:
            return
        log.info("registry: backend %s -> %s", self._state.value, state.value)
        self._metrics.record(
            Event.BACKEND_STATE, None, was=self._state.value, now=state.value,
        )
        self._state = state

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    async def start(self) -> None:
        """Connect, verify the cluster, preload, and start watching."""
        from nmos.etcd.channel import (
            EtcdChannelPool,
            build_credentials,
            parse_endpoints,
        )
        from nmos.etcd.kv import EtcdKV
        from nmos.etcd.lease import EtcdLease
        from nmos.etcd.watch import EtcdWatch

        config = self._config
        credentials = None
        if config.tls:
            credentials = build_credentials(
                trusted_root_ca=list(config.trusted_root_ca),
                certificate=config.certificate,
                key=config.key,
            )

        self._pool = EtcdChannelPool(
            parse_endpoints(
                list(config.endpoints),
                local_target=config.layout.local.client_target,
            ),
            credentials=credentials,
            target_name=config.certificate_name if config.tls else None,
            rpc_timeout=config.rpc_timeout,
        )
        self._kv = EtcdKV(self._pool)
        self._lease = EtcdLease(self._pool)
        self._watch = EtcdWatch(self._pool)

        await self._verify_cluster()
        revision = await self._preload()
        self._start_watch(revision)
        await self._recovery_fence(revision)
        self._set_state(BackendState.READY)

    async def _verify_cluster(self) -> None:
        """Version gate and membership reconciliation, both as client RPCs.

        Done here rather than in the supervisor so it applies in every mode --
        managed, adopted and ``--etcdExternal`` -- rather than only where this
        process happens to have spawned the binary.
        """
        from nmos.etcd.channel import unary_method
        from nmos.etcd.generated import rpc_pb2
        from nmos.etcd.supervisor import _require_supported_version

        assert self._pool is not None
        status_rpc = unary_method(
            "Maintenance", "Status",
            rpc_pb2.StatusRequest, rpc_pb2.StatusResponse,
        )
        member_rpc = unary_method(
            "Cluster", "MemberList",
            rpc_pb2.MemberListRequest, rpc_pb2.MemberListResponse,
        )

        status = await self._pool.call(status_rpc, rpc_pb2.StatusRequest())
        _require_supported_version(status.version)

        members = await self._pool.call(
            member_rpc, rpc_pb2.MemberListRequest(),
        )
        self._reconcile_members([m.name for m in members.members])

    def _reconcile_members(self, actual: list[str]) -> None:
        """Check the cluster we reached is the cluster we were configured for.

        A client-side check against etcd, not a call to a peer registry -- there
        is no registry-to-registry channel anywhere in this design.

        What can be checked depends on who named the members, and conflating
        the two modes gets this wrong:

        **Managed.** This registry launched its member with a name derived from
        the canonical member list, and every peer derived the same list, so the
        names must match exactly. A mismatch means this member was launched
        against a different cluster than it was configured for, and serving from
        it would mean serving another deployment's data.

        **External.** The operator named the members; the names are not ours to
        have an opinion about. What *is* still ours is the failure tolerance the
        configuration promises: a registry told it is one of three, talking to a
        single-member cluster, would advertise resilience it does not have. So
        only a cluster *smaller* than configured is refused. A larger one is
        merely noted -- more members than expected is more resilient, not less.
        """
        found = {name for name in actual if name}
        expected = {member.name for member in self._config.layout.members}

        if not self._config.external:
            if found and found != expected:
                raise ClusterMismatch(
                    f"etcd reports members {sorted(found)} but this registry "
                    f"is configured for {sorted(expected)}. Refusing to serve: "
                    f"the member set must be identical on every registry.",
                )
            return

        configured = len(expected)
        if len(found) < configured:
            raise ClusterMismatch(
                f"--etcdExternal cluster has {len(found)} member(s) "
                f"({sorted(found)}) but this registry is configured as one of "
                f"{configured}, which promises "
                f"{self._config.layout.failures_tolerated} tolerated "
                f"failure(s). Refusing to advertise resilience the cluster "
                f"does not have.",
            )
        if len(found) > configured:
            log.info(
                "registry: external etcd cluster has %d member(s), more than "
                "the %d configured; tolerating more failures than promised",
                len(found), configured,
            )

    async def close(self) -> None:
        self._stopping = True
        self._set_state(BackendState.STOPPING)

        task = self._watch_task
        self._watch_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    # -----------------------------------------------------------------------
    # Preload
    # -----------------------------------------------------------------------

    async def _preload(self) -> int:
        """Build a complete snapshot at one fixed revision and install it.

        Returns the snapshot revision, which is also where the watch starts
        (at ``+ 1``) and what the fence is seeded to.
        """
        with self._metrics.timer(Event.PRELOAD) as timer:
            revision, candidate, count = await self._read_snapshot()
            timer.note(revision=revision, resources=count)

        self._registry.swap_store(candidate)
        log.info(
            "registry: preloaded %d resource(s) at revision %d",
            count, revision,
        )
        return revision

    async def _read_snapshot(self) -> tuple[int, RegistryStore, int]:
        """Page the whole namespace at one revision into an off-side store."""
        kv = self.kv
        candidate = RegistryStore(
            gc_interval=self._registry.store.gc_interval,
            forget_interval=self._registry.store.forget_interval,
        )

        # (depth, key) so parents are applied before children -- the store
        # enforces referential integrity, so a Sender applied before its Device
        # would be rejected.
        collected: list[tuple[int, ParsedKey, Envelope]] = []

        revision = 0
        start_after: bytes | None = None
        while True:
            page = await kv.range_prefix_at(
                self._namespace.root,
                revision=revision,
                limit=PRELOAD_PAGE,
                start_after=start_after,
            )
            if revision == 0:
                # The first response fixes the snapshot revision; every later
                # page reads at exactly this, so a concurrent write cannot make
                # pages overlap or skip.
                revision = page.revision

            for kv_pair in page.kvs:
                # Seed the believed revisions from the snapshot -- for every
                # key, claims included -- so the fast path is usable from the
                # first registration rather than only after the first watch
                # event.
                self._revisions[bytes(kv_pair.key)] = kv_pair.mod_revision

                parsed = self._namespace.parse(kv_pair.key)
                if parsed is None:
                    continue  # meta/config and id claims are not materialised
                envelope = Envelope.decode(kv_pair.value)
                self._check_envelope(parsed, envelope)
                if parsed.is_node and kv_pair.lease:
                    self._leases[parsed.node_id] = kv_pair.lease
                collected.append((parsed.depth, parsed, envelope))

            if not page.more or not page.kvs:
                break
            start_after = page.kvs[-1].key

        collected.sort(key=lambda item: (item[0], item[1].resource_id))
        for _depth, parsed, envelope in collected:
            self._apply_envelope(candidate, parsed, envelope)

        self._check_no_orphans(candidate)
        return revision, candidate, len(collected)

    def _check_envelope(self, parsed: ParsedKey, envelope: Envelope) -> None:
        """The key and the value must agree about what this resource is.

        They are written in one transaction, so disagreement means corruption
        or a bug, never a race -- and serving either half of a contradiction
        would put this member permanently out of step with its peers.
        """
        if envelope.resource_type is not parsed.resource_type:
            raise KeyError_(
                f"{parsed.resource_id}: key says {parsed.resource_type.value}, "
                f"envelope says {envelope.resource_type.value}",
            )
        body_id = envelope.raw.get("id")
        if body_id != parsed.resource_id:
            raise KeyError_(
                f"key names {parsed.resource_id} but the resource body has "
                f"id {body_id!r}",
            )

    def _apply_envelope(
        self, store: RegistryStore, parsed: ParsedKey, envelope: Envelope,
    ) -> list[ResourceEvent]:
        """Decode and insert one resource, carrying etcd's authoritative cursors."""
        from nmos.registry.decode import decode_resource

        typed = decode_resource(envelope.resource_type, envelope.raw)
        prepared = store.prepare(envelope.resource_type, envelope.raw)
        if isinstance(prepared, RegistrationResult):
            raise KeyError_(
                f"{parsed.resource_id} ({envelope.resource_type.value}) is not "
                f"valid against the snapshot: {prepared.detail}",
            )
        result = store.apply_committed(
            prepared,
            envelope.raw,
            typed,
            created=envelope.created,
            updated=envelope.updated,
            health=envelope.health,
        )
        return result.events

    def _check_no_orphans(self, store: RegistryStore) -> None:
        """Every non-Node resource must have found its parent.

        ``prepare`` already refuses a resource whose parent is absent, so this
        is belt and braces -- but a snapshot that quietly dropped a subtree is
        the kind of failure that shows up days later as "the Controller cannot
        see that sender", and it costs one pass to rule out.
        """
        for resource_type in ResourceType:
            if resource_type is ResourceType.NODE:
                continue
            for resource in store.iter_extant(resource_type):
                if resource.parent_id is None:
                    raise KeyError_(
                        f"{resource_type.value} {resource.id} has no parent "
                        f"after preload",
                    )

    # -----------------------------------------------------------------------
    # Watch
    # -----------------------------------------------------------------------

    def _start_watch(self, revision: int) -> None:
        """Seed the fence and begin applying revisions.

        The fence is seeded to the *preload* revision, not to zero, and that is
        load-bearing: etcd answers a progress request only once the store
        revision has reached the watch's start revision, so on a cluster with no
        writes since the preload no progress reply ever arrives. A fence at zero
        would block the recovery fence for its whole deadline on every startup.
        """
        self._fence = RevisionFence(applied=revision)
        self._watch_task = asyncio.create_task(
            self._watch_loop(revision + 1),
            name=f"registry-watch-{self._config.layout.local.name}",
        )

    async def _watch_loop(self, start_revision: int) -> None:
        """Apply revisions forever, reconnecting and resnapshotting as needed."""
        from nmos.etcd.errors import EtcdCompacted, EtcdError

        backoff = _WATCH_RETRY_INITIAL
        next_revision = start_revision

        while not self._stopping:
            try:
                await self._watch_once(next_revision)
                if self._stopping:
                    return
                # A clean end of stream is still a disconnection; resume from
                # the next unapplied revision.
                next_revision = self._fence.applied + 1
            except asyncio.CancelledError:
                return
            except EtcdCompacted as exc:  # noqa: PERF203
                # The only failure that cannot be fixed by reconnecting: the
                # history this watch needs is gone, so the view must be rebuilt.
                log.warning("registry: %s; resnapshotting", exc)
                next_revision = await self._resnapshot() + 1
                backoff = _WATCH_RETRY_INITIAL
                continue
            except EtcdError as exc:
                self._degrade(f"watch failed: {exc}")
                next_revision = self._fence.applied + 1
                self._watch_endpoint += 1
            except Exception as exc:  # pragma: no cover - defensive
                log.exception("registry: watch loop error: %s", exc)
                self._degrade(f"watch error: {exc}")
                next_revision = self._fence.applied + 1
                self._watch_endpoint += 1

            if self._stopping:
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _WATCH_RETRY_MAX)

    async def _watch_once(self, start_revision: int) -> None:
        """One watch connection, applying batches until it ends.

        Rotates to the next member on each attempt. A watch is a long-lived
        stream, so it is pinned to one member for its lifetime -- but if that
        member is the one that died, retrying it forever would leave this
        registry frozen while a perfectly healthy quorum sat next to it. The
        local member is index 0 and so is always tried first; rotation only
        matters once it has failed.
        """
        assert self._watch is not None
        assert self._pool is not None
        endpoints = self._pool.endpoints
        endpoint = endpoints[self._watch_endpoint % len(endpoints)]
        stream = self._watch.open(
            self._namespace.root,
            start_revision=start_revision,
            endpoint=endpoint,
        )
        async with stream:
            if self._state is BackendState.DEGRADED:
                # Reconnected: catch the view up before accepting mutations
                # again, so a write is never validated against a stale store.
                await self._recovery_fence(self._fence.applied)
                self._set_state(BackendState.READY)
            # The stream is established, so this endpoint works. Reset the
            # rotation to prefer the local member again on the next reconnect;
            # otherwise one transient failure would permanently exile the
            # co-located member and add a network hop to every change.
            self._watch_endpoint = 0
            async for batch in stream:
                if self._stopping:
                    return
                self._apply_batch(batch)

    def _apply_batch(self, batch: RevisionBatch) -> None:
        """Apply one complete revision, then advance the fence.

        Synchronous from the first mutation to the last grain queued: the store
        documents that no coroutine may observe a half-applied mutation, and a
        revision that deletes a Node and its whole subtree has to become visible
        as one step. The fence is advanced only afterwards, which is what makes
        a waiter that returns able to rely on the change being visible.
        """
        from nmos.etcd.generated import kv_pb2

        if batch.progress_only:
            # No events, but the revision is authoritative -- this is what lets
            # a fence advance on a quiet cluster.
            self._schedule_advance(batch.revision)
            return

        additions: list[tuple[int, ParsedKey, Envelope]] = []
        removals: list[tuple[int, ParsedKey]] = []

        for event in batch.events:
            key = bytes(event.kv.key)
            deleted = event.type == kv_pb2.Event.DELETE

            # Believed revisions are tracked for EVERY key under the namespace,
            # including the id claims -- which are bookkeeping for the Query
            # view but are still part of the write set every CAS compares
            # against. Recording them only for materialised resources left the
            # claim permanently believed-absent, so the second write of any
            # resource always failed its compare and fell to the fenced path.
            self._revisions[key] = 0 if deleted else event.kv.mod_revision

            try:
                parsed = self._namespace.parse(event.kv.key)
            except KeyError_ as exc:
                log.error("registry: unparseable key in watch: %s", exc)
                continue
            if parsed is None:
                continue  # meta/config and id claims are not materialised

            if deleted:
                if parsed.is_node:
                    self._leases.pop(parsed.node_id, None)
                removals.append((parsed.depth, parsed))
            else:
                if parsed.is_node and event.kv.lease:
                    self._leases[parsed.node_id] = event.kv.lease
                try:
                    envelope = Envelope.decode(event.kv.value)
                    self._check_envelope(parsed, envelope)
                except KeyError_ as exc:
                    log.error("registry: corrupt envelope in watch: %s", exc)
                    continue
                additions.append((parsed.depth, parsed, envelope))

        events: list[ResourceEvent] = []
        store = self._registry.store

        # Parents before children: the store enforces referential integrity,
        # and a revision can create a Device and its Senders together.
        additions.sort(key=lambda item: (item[0], item[1].resource_id))
        for _depth, parsed, envelope in additions:
            try:
                events.extend(self._apply_envelope(store, parsed, envelope))
            except KeyError_ as exc:
                log.error("registry: cannot apply %s: %s", parsed.resource_id, exc)

        # Descendants before ancestors, so a subscriber never sees a parent
        # vanish while its children are still present.
        removals.sort(key=lambda item: item[0], reverse=True)
        for _depth, parsed in removals:
            removed = store.remove_one(parsed.resource_type, parsed.resource_id)
            if removed is not None:
                events.append(removed)

        # Same uninterrupted step as the mutations above.
        self._registry.publish(events)

        self._metrics.record(
            Event.WATCH_BATCH, None,
            revision=batch.revision,
            added=len(additions),
            removed=len(removals),
            grains=len(events),
        )
        self._schedule_advance(batch.revision)

    def _schedule_advance(self, revision: int) -> None:
        """Advance the fence without awaiting inside the application step.

        ``advance`` takes the condition's lock, so it cannot be called from the
        synchronous batch application without breaking the "no await between
        mutation and grain" invariant. Scheduling it keeps that invariant and
        still guarantees ordering: the task runs after the current step
        completes, which is exactly when the revision really is applied.
        """
        asyncio.get_running_loop().create_task(self._fence.advance(revision))

    async def _recovery_fence(self, revision: int) -> None:
        """Wait until everything through ``revision`` is applied locally."""
        try:
            await self._fence.wait(
                revision, timeout=self._config.mutation_timeout,
            )
        except Exception as exc:
            log.warning("registry: recovery fence: %s", exc)

    def _degrade(self, reason: str) -> None:
        """Stop accepting mutations, keep serving the cached Query view."""
        if self._state in (BackendState.STOPPING, BackendState.RESYNCING):
            return
        log.error("registry: %s", reason)
        self._set_state(BackendState.DEGRADED)

    async def _resnapshot(self) -> int:
        """Rebuild the view after compaction, without ever serving an empty one.

        The replacement is built off to the side and installed in one
        assignment, so Query keeps answering from the previous snapshot right up
        to the swap. Subscribers are then sent the difference, because they have
        been told about the old state and are entitled to a consistent story
        about how it became the new one.
        """
        self._set_state(BackendState.RESYNCING)
        with self._metrics.timer(Event.RESNAPSHOT) as timer:
            revision, candidate, count = await self._read_snapshot()
            timer.note(revision=revision, resources=count)

            previous = self._registry.swap_store(candidate)
            diff = _diff_stores(previous, candidate)
            self._registry.publish(diff)

        await self._fence.reset(revision)
        log.info(
            "registry: resnapshotted %d resource(s) at revision %d, "
            "%d subscription event(s)",
            count, revision, len(diff),
        )
        self._set_state(BackendState.READY)
        return revision

    # -----------------------------------------------------------------------
    # Mutations
    # -----------------------------------------------------------------------

    async def _guarded(self, what: str, coro: Any) -> Any:
        """Run a mutation, turning etcd failure into DEGRADED + 503.

        Every mutation funnels through here so the two things that must happen
        together always do: the backend stops claiming it can accept writes,
        and the caller gets a retryable answer rather than a 500. Doing it per
        call site is how one path ends up reporting healthy while another has
        already given up.

        ``EtcdCompacted`` is deliberately NOT caught: it is the watch loop's to
        handle, and swallowing it here would let a mutation proceed against a
        view that is about to be rebuilt.
        """
        from nmos.etcd.errors import EtcdCompacted, EtcdError

        try:
            return await coro
        except EtcdCompacted:
            raise
        except FenceTimeout as exc:
            # The commit may well have succeeded -- we simply did not see it
            # applied in time. Never roll back: the Node replays, and version
            # plus CAS make the replay idempotent.
            raise MutationTimeout(f"{what}: {exc}") from exc
        except EtcdError as exc:
            self._degrade(f"{what} failed: {exc}")
            raise MutationTimeout(f"{what}: {exc}") from exc

    async def register(
        self, resource_type: ResourceType, raw: dict[str, Any], typed: Any,
    ) -> RegistrationResult:
        """Register or update one resource.

        Two round trips are the naive cost -- a linearizable read to fence
        against, then the commit. The read is removable in the common case, so
        this tries the fast path first and falls back only when it must.

        **The rule that makes it correct: fast path on success, full fence
        before reporting any rejection.** An optimistic validation runs against
        a store that may be behind, so a *failure* it produces may be wrong --
        a parent registered a moment ago on another member is not here yet, and
        ``PARENT_MISSING`` would be a lie. A 400 is terminal, something the Node
        "MUST NOT" retry without corrective action
        (``Behaviour - Registration.md:94``), so a rejection is never returned
        without first fencing and re-validating against current state.
        """
        deadline = asyncio.get_running_loop().time() + self._config.mutation_timeout
        placement = self._placement(resource_type, raw)
        if isinstance(placement, RegistrationResult):
            return placement

        if resource_type is ResourceType.NODE:
            # Every key in this Node's subtree will hang off this lease, so it
            # has to exist before the first write. Children reuse whatever the
            # Node's lease already is; if it is not known here yet, the parent
            # check has already failed and the fenced path re-decides.
            placement = placement.with_lease(
                await self._ensure_node_lease(placement.node_id),
            )

        async def run() -> RegistrationResult:
            if self._fast_path:
                fast = await self._try_fast_path(
                    resource_type, raw, typed, placement,
                )
                if fast is not None:
                    return fast
            return await self._fenced_register(
                resource_type, raw, typed, placement, deadline,
            )

        result: RegistrationResult = await self._guarded(
            f"registration of {placement.resource_id}", run(),
        )
        return result

    async def _try_fast_path(
        self,
        resource_type: ResourceType,
        raw: dict[str, Any],
        typed: Any,
        placement: _Placement,
    ) -> RegistrationResult | None:
        """One speculative CAS from believed revisions. None means "fall back".

        The compare set is what enforces correctness, so submitting from a
        stale belief cannot commit anything wrong -- it simply fails the
        compare and returns here as None.
        """
        store = self._registry.store
        prepared = store.prepare(resource_type, raw)
        if isinstance(prepared, RegistrationResult):
            # Might be a genuine 400, might be staleness. Not ours to answer.
            self._metrics.record(
                Event.FAST_PATH_MISS, None,
                reason="optimistic-validation-failed",
                error=prepared.error.value if prepared.error else "unknown",
            )
            return None

        compares = self._compare_set(placement, speculative=True)
        with self._metrics.timer(Event.CAS, path="fast") as timer:
            result = await self.kv.txn(
                compare=compares,
                success=self._write_ops(placement, raw, resource_type),
                failure=(),
            )
            timer.note(succeeded=result.succeeded, revision=result.revision)

        if not result.succeeded:
            self._metrics.record(
                Event.FAST_PATH_MISS, None,
                reason="compare-failed", key=placement.key.decode(),
                believed=self._revisions.get(placement.key, 0),
            )
            return None

        self._metrics.record(Event.FAST_PATH_HIT, None, revision=result.revision)
        await self._await_commit(result.revision)
        return RegistrationResult(created=prepared.creates, events=[])

    async def _fenced_register(
        self,
        resource_type: ResourceType,
        raw: dict[str, Any],
        typed: Any,
        placement: _Placement,
        deadline: float,
    ) -> RegistrationResult:
        """Read, fence, validate, commit -- retrying until the deadline."""
        from nmos.etcd.errors import EtcdError

        attempt = 0
        while True:
            attempt += 1
            try:
                revision = await self._read_fence(placement)
            except EtcdError as exc:
                self._degrade(f"registration read failed: {exc}")
                raise

            # Now validating against a store known to include everything up to
            # `revision`, so a rejection here is authoritative and returnable.
            prepared = self._registry.store.prepare(resource_type, raw)
            if isinstance(prepared, RegistrationResult):
                return prepared

            with self._metrics.timer(
                Event.CAS, path="fenced", attempt=attempt,
            ) as timer:
                result = await self.kv.txn(
                    compare=self._compare_set(placement, speculative=False),
                    success=self._write_ops(placement, raw, resource_type),
                    failure=(),
                )
                timer.note(succeeded=result.succeeded, revision=result.revision)

            if result.succeeded:
                await self._await_commit(result.revision)
                return RegistrationResult(created=prepared.creates, events=[])

            self._metrics.record(
                Event.CAS_RETRY, None,
                attempt=attempt, key=placement.key.decode(),
                read_revision=revision,
            )
            if asyncio.get_running_loop().time() >= deadline:
                raise MutationTimeout(
                    f"registration of {placement.resource_id} did not commit "
                    f"within {self._config.mutation_timeout:.1f}s "
                    f"({attempt} attempt(s))",
                )
            # Someone else committed first; re-read and re-validate rather than
            # re-submitting the same comparisons, which would fail identically.
            await asyncio.sleep(0)

    async def _read_fence(self, placement: _Placement) -> int:
        """Linearizable read of the write set, then wait for the view to match.

        The read gives the revisions the CAS must compare against; the wait is
        what makes the subsequent local validation trustworthy.
        """
        keys = [placement.key, placement.claim]
        if placement.parent is not None:
            keys.append(placement.parent)

        with self._metrics.timer(Event.LINEARIZABLE_READ) as timer:
            read = await self.kv.read_set(keys)
            timer.note(revision=read.revision, keys=len(keys))

        from nmos.etcd.kv import first_kv

        for key, response in zip(keys, read.responses):
            found = first_kv(response)
            self._revisions[key] = found.mod_revision if found else 0

        with self._metrics.timer(Event.FENCE_WAIT, target=read.revision) as timer:
            await self._fence.wait(
                read.revision, timeout=self._config.mutation_timeout,
            )
            timer.note(applied=self._fence.applied)
        return read.revision

    def _compare_set(
        self, placement: _Placement, *, speculative: bool,
    ) -> list[Any]:
        """Comparisons that make this write safe to commit.

        Identical in both paths -- only where the revisions came from differs.
        On the fast path they are what the watch last told us; on the fenced
        path they are what a linearizable read just returned.
        """
        from nmos.etcd.kv import compare_absent, compare_exists, compare_mod

        compares: list[Any] = []

        believed = self._revisions.get(placement.key, 0)
        compares.append(
            compare_mod(placement.key, believed) if believed
            else compare_absent(placement.key)
        )

        claim_revision = self._revisions.get(placement.claim, 0)
        compares.append(
            compare_mod(placement.claim, claim_revision) if claim_revision
            else compare_absent(placement.claim)
        )

        if placement.parent is not None:
            compares.append(compare_exists(placement.parent))
        return compares

    def _write_ops(
        self,
        placement: _Placement,
        raw: dict[str, Any],
        resource_type: ResourceType,
    ) -> list[Any]:
        """The resource and its id claim, both on the Node's lease.

        Attaching to the lease is the whole of distributed garbage collection:
        when the Node stops heartbeating, etcd removes every key on it, on
        every member, at once.
        """
        from nmos.etcd.kv import put_op

        cursors = self._cursors_for(placement, resource_type)
        envelope = Envelope(
            version=ENVELOPE_VERSION,
            resource_type=resource_type,
            raw=raw,
            created=cursors[0],
            updated=cursors[1],
            health=health_now(),
        )
        lease = placement.lease
        return [
            put_op(placement.key, envelope.encode(), lease=lease),
            put_op(
                placement.claim,
                _claim_value(placement.key),
                lease=lease,
            ),
        ]

    def _cursors_for(
        self, placement: _Placement, resource_type: ResourceType,
    ) -> tuple[TaiCursor, TaiCursor]:
        """Authoritative paging cursors for this write.

        ``created`` is preserved across updates so a resource does not jump to
        the top of a creation-ordered page every time it is re-registered.
        Uniqueness within a type is what stops paging skipping a record, so a
        fresh cursor is taken from the store's allocator rather than from the
        bare clock.
        """
        existing = self._registry.store.get(
            resource_type, placement.resource_id, include_non_extant=True,
        )
        updated = self._registry.store.next_cursor(resource_type)
        created = existing.created if existing is not None and existing.extant \
            else updated
        return created, updated

    async def _await_commit(self, revision: int) -> None:
        """Wait until our own commit has come back through the watch.

        This is what gives read-your-write on the member that answered, and it
        is why a locally originated write needs no special handling anywhere
        else: it becomes visible by exactly the same path as a remote one.
        """
        with self._metrics.timer(
            Event.COMMIT_TO_WATCH, revision=revision,
        ) as timer:
            await self._fence.wait(
                revision, timeout=self._config.mutation_timeout,
            )
            timer.note(applied=self._fence.applied)

    def _placement(
        self, resource_type: ResourceType, raw: dict[str, Any],
    ) -> _Placement | RegistrationResult:
        """Work out where a resource lives, and on whose lease.

        Every resource belongs to exactly one Node subtree, so the Node id has
        to be resolvable before anything can be written. For a Device it is in
        the body; for a Source/Flow/Sender/Receiver it is the Device's Node,
        which is looked up locally -- and if the Device is not here yet, that is
        a genuine ``PARENT_MISSING``, decided by the same store rule that
        governs it in standalone mode.
        """
        resource_id = raw.get("id")
        if not isinstance(resource_id, str) or not resource_id:
            return RegistrationResult.failure(
                RegistrationError.SCHEMA, "resource has no 'id' attribute",
            )

        ns = self._namespace
        claim = ns.id_claim(resource_id)

        if resource_type is ResourceType.NODE:
            return _Placement(
                resource_id=resource_id,
                node_id=resource_id,
                key=ns.node(resource_id),
                claim=claim,
                parent=None,
                lease=self._leases.get(resource_id, 0),
            )

        if resource_type is ResourceType.DEVICE:
            node_id = raw.get("node_id")
            if not isinstance(node_id, str):
                return RegistrationResult.failure(
                    RegistrationError.SCHEMA,
                    "device is missing its 'node_id' attribute",
                )
            return _Placement(
                resource_id=resource_id,
                node_id=node_id,
                key=ns.device(node_id, resource_id),
                claim=claim,
                parent=ns.node(node_id),
                lease=self._leases.get(node_id, 0),
            )

        device_id = raw.get("device_id")
        if not isinstance(device_id, str):
            return RegistrationResult.failure(
                RegistrationError.SCHEMA,
                f"{resource_type.value} is missing its 'device_id' attribute",
            )
        device = self._registry.store.get(ResourceType.DEVICE, device_id)
        if device is None or device.parent_id is None:
            return RegistrationResult.failure(
                RegistrationError.PARENT_MISSING,
                f"parent device {device_id} is not registered",
            )
        node_id = device.parent_id
        return _Placement(
            resource_id=resource_id,
            node_id=node_id,
            key=ns.child(resource_type, node_id, device_id, resource_id),
            claim=claim,
            parent=ns.device(node_id, device_id),
            lease=self._leases.get(node_id, 0),
        )

    async def _ensure_node_lease(self, node_id: str) -> int:
        """The lease every key in a Node's subtree hangs off.

        TTL is ``ceil(--garbageCollectionInterval)`` -- 12 s by default, the
        interval of ``Behaviour - Registration.md:47``. Deliberately not the
        15 s the legacy dRDS used against the same 12 s registry interval, which
        left a Node the registry had already collected alive in etcd for
        several more seconds.
        """
        existing = self._leases.get(node_id)
        if existing:
            return existing
        import math

        ttl = max(1, math.ceil(self._registry.store.gc_interval))
        lease = await self.lease.grant(ttl)
        self._leases[node_id] = lease.id
        return lease.id

    async def unregister(
        self, resource_type: ResourceType, resource_id: str,
    ) -> bool:
        """Delete a resource and, for a Node or Device, its whole subtree.

        The cascade is a single ranged delete rather than a walk, which is the
        payoff for keeping a Node's subtree under one prefix. Every key that
        goes produces a watch event, so the local store learns about each one
        individually and emits removals descendants-first.
        """
        deleted: bool = await self._guarded(
            f"delete of {resource_id}",
            self._unregister(resource_type, resource_id),
        )
        return deleted

    async def _unregister(
        self, resource_type: ResourceType, resource_id: str,
    ) -> bool:
        from nmos.etcd.kv import delete_op, delete_prefix_op

        resource = self._registry.store.get(resource_type, resource_id)
        if resource is None:
            return False

        placement = self._placement(resource_type, resource.raw)
        if isinstance(placement, RegistrationResult):
            return False

        ns = self._namespace
        ops: list[Any] = []
        if resource_type is ResourceType.NODE:
            ops.append(delete_prefix_op(ns.node_subtree(resource_id)))
        elif resource_type is ResourceType.DEVICE:
            ops.append(
                delete_prefix_op(
                    ns.device_subtree(placement.node_id, resource_id),
                ),
            )
        else:
            ops.append(delete_op(placement.key))
        # The id claim always goes with its resource. A claim outliving the key
        # it points at is safe -- it can be reclaimed transactionally -- but
        # leaving one behind for every delete would grow without bound.
        ops.append(delete_op(placement.claim))

        with self._metrics.timer(
            Event.CAS, path="delete", type=resource_type.value,
        ) as timer:
            result = await self.kv.txn(compare=(), success=ops)
            timer.note(revision=result.revision)

        await self._await_commit(result.revision)

        if resource_type is ResourceType.NODE:
            # Best-effort, and after the prefix delete: the lease has nothing
            # left on it, and racing this against natural expiry is fine --
            # both outcomes leave the cluster in the state the caller wanted.
            lease_id = self._leases.pop(resource_id, 0)
            if lease_id:
                await self.lease.revoke(lease_id)
        return True

    async def heartbeat(self, node_id: str) -> int | None:
        """Renew a Node's lease. Returns its health, or None if it is gone.

        No full-database fence and, deliberately, **no write**. The lease is the
        liveness record. The legacy dRDS wrote a health key on every beat and every
        member watched it -- 100 Nodes at the 5 s default is 100 Raft writes per
        second fanning out to 500 watch events per second across five members,
        to record something the lease already records more reliably, since a
        lease cannot be renewed by a member that has lost quorum.
        """
        health: int | None = await self._guarded(
            f"heartbeat of {node_id}", self._heartbeat(node_id),
        )
        return health

    async def _heartbeat(self, node_id: str) -> int | None:
        from nmos.etcd.errors import EtcdLeaseNotFound

        lease_id = self._leases.get(node_id)
        if not lease_id:
            return None

        with self._metrics.timer(Event.HEARTBEAT, node=node_id) as timer:
            try:
                ttl = await self.lease.keepalive_once(lease_id)
            except EtcdLeaseNotFound:
                # Authoritative: the cluster has collected this Node. Answering
                # 404 is what makes the Node re-register everything in order,
                # per Behaviour - Registration.md:112-114.
                self._leases.pop(node_id, None)
                timer.note(result="lease-gone")
                return None
            timer.note(ttl=ttl)

        health = health_now()
        # Local diagnostic health only. It must never drive collection: that is
        # the lease's job, and a member with a slow clock reviving resources its
        # peers had collected is exactly the divergence this design removes.
        node = self._registry.store.get(ResourceType.NODE, node_id)
        if node is not None:
            self._registry.store.heartbeat(node_id)
        return health

    async def collect_garbage(self) -> int:
        """Local expiry is disabled in distributed mode.

        A Node's liveness is an etcd lease. If every member also ran
        health-based expiry they could disagree about which Nodes are alive,
        and the member with the slowest clock would resurrect resources the
        others had collected.
        """
        return 0


class ClusterMismatch(Exception):
    """etcd's membership does not match what this registry was configured for."""


class MutationTimeout(MutationUnavailable):
    """A mutation did not commit within the overall deadline.

    Answered as 503, not 500: the write lost repeated races or the cluster is
    slow, neither of which means the request was bad. The Node retries.
    """


@dataclass(frozen=True)
class _Placement:
    """Where a resource lives in the keyspace, and on whose lease.

    Computed once per mutation and passed around, because every step needs the
    same answer and recomputing it risks the fast path and the fenced path
    disagreeing about which keys they are talking about.
    """

    resource_id: str
    node_id: str
    key: bytes
    claim: bytes
    parent: bytes | None
    lease: int

    def with_lease(self, lease: int) -> _Placement:
        return _Placement(
            resource_id=self.resource_id,
            node_id=self.node_id,
            key=self.key,
            claim=self.claim,
            parent=self.parent,
            lease=lease,
        )


def _claim_value(key: bytes) -> bytes:
    """The body of an id claim: the resource key it points at.

    Deliberately just the key, not a copy of the resource. The claim exists to
    answer "is this id already in use, and by what" -- which is the cross-type
    collision check of ``Behaviour - Registration.md:101`` -- and storing the
    resource twice would create a second thing to keep consistent.
    """
    return key


def _diff_stores(
    previous: RegistryStore, current: RegistryStore,
) -> list[ResourceEvent]:
    """Subscription events describing how ``previous`` became ``current``.

    Emitted after a compaction resnapshot. Without it a subscriber's view would
    silently diverge: it was told about the old state, and the new snapshot may
    differ from it in ways no event ever described.
    """
    events: list[ResourceEvent] = []

    for resource_type in ResourceType:
        before = {r.id: r for r in previous.iter_extant(resource_type)}
        after = {r.id: r for r in current.iter_extant(resource_type)}

        for resource_id, resource in after.items():
            was = before.get(resource_id)
            if was is None:
                events.append(ResourceEvent.added(resource))
            elif was.raw != resource.raw:
                events.append(ResourceEvent.modified(was.raw, resource))

        for resource_id, resource in before.items():
            if resource_id not in after:
                events.append(ResourceEvent.removed(resource))

    return events
