# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""An in-process cluster over memory pipes, with injectable partitions.

Why this exists rather than a rig of real sockets
-------------------------------------------------
The failures that matter in consensus are the ones that are hard to provoke:
a member restarting at the instant a peer campaigns, a partition that heals in
the middle of an election, a follower acknowledging an entry and then losing
its log. Over real TCP those are reproduced by sleeping and hoping. Here they
are reproduced by calling a method.

That is the whole argument for ``transport.py`` being a Protocol. A consensus
layer testable only over sockets is tested only in the situations that are easy
to arrange, and those are precisely not the situations that break it.

Underscore-prefixed so pytest does not collect it, matching
``nmos/registry/tests/_fixtures.py``.
"""

from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import Any

from nmos.cluster.layout import MemberSpec, derive_cluster
from nmos.raft.cluster import RaftLayout, derive_raft_layout
from nmos.raft.cursors import CursorAllocator
from nmos.raft.errors import RaftUnavailable
from nmos.raft.machine import StateMachine
from nmos.raft.node import RaftNode, RaftTiming
from nmos.raft.ownership import OwnershipTable
from nmos.raft.persist import TermStore
from nmos.raft.snapshot import SnapshotStore
from nmos.raft.transport import PeerHandler
from nmos.raft.wire import MessageType, Stream
from nmos.registry.registry import Registry
from nmos.registry.store import RegistryStore
from nmos.registry.subscriptions import SubscriptionManager

# Fast enough that a test finishes in milliseconds, with the election window
# still an order of magnitude above the heartbeat -- the ratio is what stops a
# healthy leader losing its followers' timers, and compressing it uniformly
# preserves that.
FAST = RaftTiming(
    heartbeat=0.005,
    election_min=0.030,
    election_max=0.060,
)


class MemoryNetwork:
    """Routes messages between in-process members, subject to partitions.

    The fault model is deliberately the one our transport can actually suffer
    -------------------------------------------------------------------------
    ``transport.py`` runs **TCP**, two connections per peer. TCP does not lose,
    duplicate or reorder within a connection: it delivers in order or it
    breaks. So injecting per-message loss or reordering inside one stream would
    test a network we do not have, and would report failures against an
    implementation that is entitled to assume per-link FIFO.

    What this network therefore models, and all it models:

    * **link break** -- ``stop``/``partition``/``block`` make a link
      unreachable, and anything in flight on it is lost, exactly as a dropped
      connection loses its send buffer;
    * **one-way reachability** -- ``block`` is directional, because a firewall
      or a half-open connection really can let A reach B while B cannot reach
      A. This is the case ``check-quorum`` exists for;
    * **delay, FIFO within a stream** -- a slow link holds messages back
      without shuffling them;
    * **reordering across streams** -- CONTROL and BULK are separate
      connections, so they have no ordering relationship with each other.

    Every knob defaults to inert, so a test that sets none of them sees exactly
    the immediate, lossless, symmetric behaviour the suite had before.
    """

    def __init__(self) -> None:
        self._handlers: dict[int, PeerHandler] = {}
        self._blocked: set[tuple[int, int]] = set()
        self._down: set[int] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self.delivered = 0
        self.dropped = 0

        # -- chaos knobs, all inert by default --------------------------
        self.rng: random.Random | None = None
        """Set to make delays random. Seeded by the caller, so a failing soak
        is replayed by its seed rather than by luck."""

        self.max_delay: float = 0.0
        """Upper bound on per-message delay, in seconds. Zero keeps delivery on
        the very next loop iteration, as it was."""

        # Per-(source, target, stream) release clock. What keeps a delayed link
        # FIFO: each message leaves no earlier than the one before it.
        self._link_clock: dict[tuple[int, int, int], float] = {}

    # -- membership -----------------------------------------------------

    def attach(self, index: int, handler: PeerHandler) -> None:
        self._handlers[index] = handler
        for other in self._handlers:
            if other != index:
                self._announce(index, other)

    def detach(self, index: int) -> None:
        self._handlers.pop(index, None)
        for other in list(self._handlers):
            self._handlers[other].on_peer_state(index, up=False, incarnation=0)

    def _announce(self, a: int, b: int) -> None:
        """Tell each of two members the other is reachable."""
        if self.reachable(a, b):
            self._handlers[b].on_peer_state(a, up=True, incarnation=1)
        if self.reachable(b, a):
            self._handlers[a].on_peer_state(b, up=True, incarnation=1)

    # -- fault injection ------------------------------------------------

    def reachable(self, source: int, target: int) -> bool:
        if source in self._down or target in self._down:
            return False
        return (source, target) not in self._blocked

    def partition(self, *groups: set[int]) -> None:
        """Isolate the given groups from each other, and heal within them."""
        self._blocked.clear()
        members = set(self._handlers)
        for group in groups:
            for inside in group:
                for outside in members - group:
                    self._blocked.add((inside, outside))
                    self._blocked.add((outside, inside))
        self._resync()

    def block(self, source: int, target: int) -> None:
        """Make ``source -> target`` unreachable, leaving the reverse alone.

        The asymmetric case, which ``partition`` and ``isolate`` cannot express
        because they are defined in terms of symmetric groups. A leader whose
        heartbeats still arrive but whose acknowledgements never come back is
        the scenario check-quorum was written for -- and until this existed,
        that mechanism had no test that could produce its trigger.
        """
        self._blocked.add((source, target))
        self._resync()

    def unblock(self, source: int, target: int) -> None:
        self._blocked.discard((source, target))
        self._resync()

    def heal(self) -> None:
        self._blocked.clear()
        self._resync()

    def isolate(self, index: int) -> None:
        others = set(self._handlers) - {index}
        self.partition({index}, others)

    def stop(self, index: int) -> None:
        """Take a member off the network without detaching it.

        Symmetric, because unreachability is. Telling only the *other* members
        that this one is gone would leave it believing it still had a quorum,
        which is exactly the state a real partitioned leader does not enjoy --
        and check-quorum, the mechanism that makes it stand down, would never
        fire.
        """
        self._down.add(index)
        # ``.get`` on the stopped member: ``restart`` detaches it before
        # stopping it, so it may legitimately no longer be attached.
        stopped = self._handlers.get(index)
        for other in self._handlers:
            if other == index:
                continue
            self._handlers[other].on_peer_state(index, up=False, incarnation=0)
            if stopped is not None:
                stopped.on_peer_state(other, up=False, incarnation=0)

    def resume(self, index: int, *, incarnation: int = 1) -> None:
        self._down.discard(index)
        resumed = self._handlers.get(index)
        for other in self._handlers:
            if other == index or not self.reachable(index, other):
                continue
            self._handlers[other].on_peer_state(
                index, up=True, incarnation=incarnation,
            )
            if resumed is not None:
                resumed.on_peer_state(other, up=True, incarnation=1)

    def _resync(self) -> None:
        for source in self._handlers:
            for target in self._handlers:
                if source == target:
                    continue
                self._handlers[target].on_peer_state(
                    source,
                    up=self.reachable(source, target),
                    incarnation=1,
                )

    # -- delivery -------------------------------------------------------

    def deliver(
        self, source: int, target: int, message: Any,
        *, stream: Stream = Stream.CONTROL,
    ) -> None:
        """Hand a message to its recipient, on a later loop iteration.

        Via ``call_soon`` rather than directly, for two reasons: a synchronous
        handler must not re-enter the sender mid-update, and scheduling makes
        the delivery order deterministic without being instantaneous, which is
        what lets a test observe an in-between state.

        With ``max_delay`` set the message is instead scheduled at this link's
        release time, which never moves backwards -- so a slow link stays FIFO
        while two *different* streams drift apart, matching two TCP
        connections between the same pair of hosts.
        """
        if not self.reachable(source, target):
            self.dropped += 1
            return
        handler = self._handlers.get(target)
        if handler is None:
            self.dropped += 1
            return
        self.delivered += 1

        loop = asyncio.get_running_loop()
        if self.rng is None or self.max_delay <= 0.0:
            loop.call_soon(self._dispatch, source, target, message)
            return

        key = (source, target, int(stream))
        now = loop.time()
        release = max(
            self._link_clock.get(key, now), now,
        ) + self.rng.uniform(0.0, self.max_delay)
        self._link_clock[key] = release
        loop.call_later(
            release - now, self._dispatch, source, target, message,
        )

    def _dispatch(self, source: int, target: int, message: Any) -> None:
        handler = self._handlers.get(target)
        if handler is None or not self.reachable(source, target):
            return

        kind = message.TYPE
        reply: Any | None = None
        if kind is MessageType.REQUEST_VOTE:
            reply = handler.on_request_vote(source, message)
        elif kind is MessageType.REQUEST_VOTE_REPLY:
            handler.on_request_vote_reply(source, message)
        elif kind is MessageType.APPEND_ENTRIES:
            reply = handler.on_append_entries(source, message)
        elif kind is MessageType.APPEND_ENTRIES_REPLY:
            handler.on_append_entries_reply(source, message)
        elif kind is MessageType.PROMOTE:
            handler.on_promote(source, message)
        elif kind is MessageType.INSTALL_SNAPSHOT:
            reply = handler.on_install_snapshot(source, message)
        elif kind is MessageType.INSTALL_SNAPSHOT_REPLY:
            handler.on_install_snapshot_reply(source, message)
        elif kind is MessageType.PROPOSE:
            # Tracked, not fire-and-forget: an unowned task outlives the test
            # that created it and shows up as unexplained slowness in whatever
            # runs next.
            task = asyncio.get_running_loop().create_task(
                self._propose(source, target, message),
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        if reply is not None:
            # A snapshot reply travels back on the same connection the chunk
            # came in on, so it shares BULK's ordering domain rather than
            # racing along CONTROL beside the heartbeats.
            self.deliver(
                target, source, reply,
                stream=(
                    Stream.BULK if kind is MessageType.INSTALL_SNAPSHOT
                    else Stream.CONTROL
                ),
            )

    async def _propose(self, source: int, target: int, message: Any) -> None:
        handler = self._handlers.get(target)
        if handler is None:
            return
        await handler.on_propose(source, message)

    async def drain(self) -> None:
        """Cancel and await every in-flight delivery task."""
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        self._tasks.clear()


class MemoryTransport:
    """One member's view of :class:`MemoryNetwork`, satisfying ``Transport``."""

    def __init__(self, network: MemoryNetwork, index: int) -> None:
        self._network = network
        self._index = index

    async def start(self, handler: PeerHandler) -> None:
        self._network.attach(self._index, handler)

    async def close(self) -> None:
        self._network.detach(self._index)

    def send(
        self, peer: int, message: Any, *, stream: Stream = Stream.CONTROL,
    ) -> None:
        # The stream is carried through rather than dropped: CONTROL and BULK
        # are separate TCP connections in ``transport.py``, so they are
        # separate ordering domains, and the chaos driver reorders across them
        # on purpose. A snapshot on BULK overtaking a heartbeat on CONTROL is a
        # real interleaving, and it is the one that produced the duplicate
        # snapshot-offset bug this harness now guards against.
        self._network.deliver(self._index, peer, message, stream=stream)

    async def request(
        self, peer: int, message: Any, *, timeout: float | None = None,
        stream: Stream = Stream.CONTROL,
    ) -> Any:
        raise RaftUnavailable("the memory transport does not correlate requests")

    @property
    def live(self) -> frozenset[int]:
        return frozenset(
            other for other in self._network._handlers  # noqa: SLF001
            if other != self._index
            and self._network.reachable(other, self._index)
        )


class Member:
    """One in-process member: registry, machine and node."""

    def __init__(
        self, layout: RaftLayout, network: MemoryNetwork, root: Path,
        *, timing: RaftTiming,
    ) -> None:
        self.index = layout.local.index
        self.root = root
        self.registry = Registry(RegistryStore(), query_id=f"q{self.index}")
        self.registry.attach_subscriptions(SubscriptionManager(self.registry))
        self.snapshots = SnapshotStore(self.registry.store)
        self.machine = StateMachine(
            self.registry,
            ownership=OwnershipTable(),
            cursors=CursorAllocator(self.index),
            member=self.index,
            snapshots=self.snapshots,
        )
        self.terms = TermStore(root / f"m{self.index}-state.json")
        self.node = RaftNode(
            layout,
            transport=MemoryTransport(network, self.index),
            terms=self.terms,
            machine=self.machine,
            timing=timing,
            snapshots=self.snapshots,
        )

    @property
    def ownership(self) -> OwnershipTable:
        """Read through the machine: a snapshot install replaces the table."""
        return self.machine.ownership


class Cluster:
    """``size`` in-process members sharing one :class:`MemoryNetwork`."""

    def __init__(
        self, size: int, root: Path, *, timing: RaftTiming | None = None,
    ) -> None:
        timing = timing if timing is not None else FAST
        self.network = MemoryNetwork()
        self.root = root
        self.timing = timing
        self._shared = _shared_layouts(size)
        self._layouts = [derive_raft_layout(shared) for shared in self._shared]
        self.members: list[Member] = [
            Member(layout, self.network, root, timing=timing)
            for layout in self._layouts
        ]
        self.backends: list[Any] = []

    def attach_backends(self, *, mutation_timeout: float = 2.0) -> list[Any]:
        """Put a ``RaftRegistryBackend`` in front of every member.

        Built before ``start`` so the backend owns the node's lifecycle, the
        way ``nmos_registry.py`` does -- starting the node here and the backend
        there would leave the forward handler uninstalled for exactly as long
        as it took someone to notice.
        """
        from nmos.registry.distributed import RaftConfig
        from nmos.registry.raft_backend import RaftRegistryBackend

        self.backends = [
            RaftRegistryBackend(
                member.registry,
                RaftConfig(
                    layout=shared,
                    endpoints=tuple(
                        f"{m.host}:{m.port}" for m in layout.members
                    ),
                    namespace="/harness",
                    tls=False, certificate="", key="", trusted_root_ca=(),
                    certificate_name="",
                    rpc_timeout=1.0, mutation_timeout=mutation_timeout,
                    state_dir=self.root,
                    crl_file="",
                    peer_port=layout.local.port,
                ),
                member.node,
            )
            for member, shared, layout in zip(
                self.members, self._shared, self._layouts,
            )
        ]
        return self.backends

    async def start(self) -> None:
        if self.backends:
            # Concurrently, because members really do start independently --
            # and because starting them in turn makes the first one wait out
            # its whole leader timeout before the second even exists.
            await asyncio.gather(*(b.start() for b in self.backends))
            return
        for member in self.members:
            await member.node.start()

    async def close(self) -> None:
        if self.backends:
            for backend in self.backends:
                await backend.close()
        else:
            for member in self.members:
                await member.node.close()
        await self.network.drain()

    def __getitem__(self, index: int) -> Member:
        return self.members[index]

    @property
    def leaders(self) -> list[Member]:
        from nmos.raft.node import Role

        return [m for m in self.members if m.node.role is Role.LEADER]

    async def elect(self, *, timeout: float = 5.0) -> Member:
        """Wait for exactly one leader, and return it."""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            leaders = self.leaders
            if len(leaders) == 1:
                return leaders[0]
            await asyncio.sleep(self.timing.heartbeat)
        raise AssertionError(
            f"no single leader after {timeout}s: "
            f"{[(m.index, m.node.role.value) for m in self.members]}",
        )

    async def settle(self, rounds: int = 40) -> None:
        """Let scheduled deliveries and timers run without asserting anything."""
        for _ in range(rounds):
            await asyncio.sleep(self.timing.heartbeat)

    async def restart(self, index: int) -> Member:
        """Stop a member and bring it back with an empty log.

        The scenario that makes the non-voting rejoin necessary. The term store
        survives -- that is the whole point of it being on disk -- but the log
        does not, exactly as a real restart would leave it.
        """
        await self.members[index].node.close()
        self.network.stop(index)

        replacement = Member(
            self._layouts[index], self.network, self.root, timing=self.timing,
        )
        self.members[index] = replacement
        self.network.resume(index, incarnation=2)
        await replacement.node.start()
        return replacement


def _shared_layouts(size: int) -> list[Any]:
    specs = [
        MemberSpec(host=f"h{index}", name=f"m{index}") for index in range(size)
    ]
    return [
        derive_cluster(
            specs, local_host=f"h{index}", namespace="/harness",
            tls=False, flavour="raft\n",
        )
        for index in range(size)
    ]


def _layouts(size: int) -> list[RaftLayout]:
    return [derive_raft_layout(shared) for shared in _shared_layouts(size)]
