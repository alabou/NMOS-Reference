# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The same cluster as ``_harness.py``, over real TCP instead of method calls.

Why both exist
--------------
The memory harness makes consensus scenarios deterministic, and that is what
lets election safety be driven through the exact interleaving that breaks it.
Everything it proves, though, it proves about a cluster that has never opened a
socket -- the transport, the handshake, framing, checksums, the CONTROL/BULK
split and every reconnection path are simply absent from it.

This module runs the identical churn against ``RaftTransport``. It is slower
and less deterministic, and that is the point: the failures it can find are the
ones that live in the code the memory harness replaces.

Partitions come from ``_proxy.py``: members dial forwarders rather than each
other, so cutting a link really does strand a TCP connection mid-stream and
force the transport to notice, reconnect and re-handshake.

The surface is deliberately the same
------------------------------------
``SocketCluster`` presents the members, timing, ``restart``, ``settle``,
``elect`` and a ``network`` with the fault methods ``ChurnDriver`` already
calls. One driver, one set of invariants, two transports underneath -- so a
divergence between them is a finding about the transport rather than about two
test harnesses that drifted apart.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import socket
from pathlib import Path
from typing import Any

from nmos.cluster.layout import MemberSpec, derive_cluster
from nmos.raft.cluster import RAFT_FLAVOUR, RaftLayout, derive_raft_layout
from nmos.raft.cursors import CursorAllocator
from nmos.raft.machine import StateMachine
from nmos.raft.node import RaftNode, RaftTiming, Role
from nmos.raft.ownership import OwnershipTable
from nmos.raft.persist import TermStore
from nmos.raft.snapshot import SnapshotStore
from nmos.raft.tests._proxy import ProxyMesh
from nmos.raft.transport import RaftTransport
from nmos.registry.registry import Registry
from nmos.registry.store import RegistryStore
from nmos.registry.subscriptions import SubscriptionManager

# Slower than the memory harness's FAST, because sockets are slower than method
# calls, with the election window kept an order of magnitude above the
# heartbeat -- that ratio is what stops a healthy leader losing its followers'
# timers, and scaling both together preserves it.
SOCKET_TIMING = RaftTiming(
    heartbeat=0.020,
    election_min=0.150,
    election_max=0.300,
)


class SocketMember:
    """One member: registry, state machine, node, over a real transport."""

    def __init__(
        self,
        layout: RaftLayout,
        *,
        root: Path,
        bind_port: int,
        dial: dict[int, tuple[str, int]],
        timing: RaftTiming,
    ) -> None:
        self.index = layout.local.index
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
        self.transport = RaftTransport(
            local=self.index,
            peers=dial,
            bind=("127.0.0.1", bind_port),
            cluster_id=layout.cluster_id,
            member_name=layout.local.name,
            incarnation=0,
            rpc_timeout=1.0,
        )
        self.node = RaftNode(
            layout,
            transport=self.transport,
            terms=TermStore(root / f"m{self.index}-state.json"),
            machine=self.machine,
            timing=timing,
            snapshots=self.snapshots,
        )
        self.transport.set_incarnation(self.node.incarnation)

    @property
    def ownership(self) -> OwnershipTable:
        return self.machine.ownership


class SocketFaults:
    """The fault surface ``ChurnDriver`` drives, backed by the proxy mesh.

    Method names match ``MemoryNetwork`` exactly so the driver needs no branch.
    ``rng`` and ``max_delay`` are accepted for the same reason; here they set a
    real per-link delay rather than a scheduling offset.
    """

    def __init__(self, mesh: ProxyMesh) -> None:
        self._mesh = mesh
        self._down: set[int] = set()
        self.rng: random.Random | None = None
        self._max_delay = 0.0

    @property
    def max_delay(self) -> float:
        return self._max_delay

    @max_delay.setter
    def max_delay(self, value: float) -> None:
        self._max_delay = value
        # Half the bound, as a constant: a uniform random delay per chunk
        # would reorder nothing (the pump is sequential) but would make runs
        # harder to compare for no gain.
        self._mesh.set_delay(value / 2.0)

    def stop(self, index: int) -> None:
        self._mesh.isolate(index)
        self._down.add(index)

    def resume(self, index: int) -> None:
        self._mesh.rejoin(index)
        self._down.discard(index)

    def isolate(self, index: int) -> None:
        self._mesh.isolate(index)

    def block(self, source: int, target: int) -> None:
        self._mesh.cut(source, target)

    def unblock(self, source: int, target: int) -> None:
        self._mesh.heal_link(source, target)

    def partition(self, *groups: set[int]) -> None:
        self._mesh.heal()
        members = set(self._mesh.members)
        for group in groups:
            for inside in group:
                for outside in members - group:
                    self._mesh.cut_both(inside, outside)
        # A member taken down stays down: a partition is about reachability
        # between the living, and healing must not quietly revive a corpse.
        for index in self._down:
            self._mesh.isolate(index)

    def heal(self) -> None:
        self._mesh.heal()
        for index in self._down:
            self._mesh.isolate(index)


class SocketCluster:
    """``size`` members on loopback, reachable only through cuttable proxies."""

    def __init__(
        self, size: int, root: Path, *, timing: RaftTiming | None = None,
    ) -> None:
        self.root = root
        self.timing = timing if timing is not None else SOCKET_TIMING
        self._held: list[socket.socket] = []
        # Sorted, because ``derive_cluster`` orders members by
        # ``(host, peer_port, client_port)`` and that order *is* the member
        # index every peer addresses. Every member here shares 127.0.0.1, so
        # the port alone decides it -- and reserved ports come back in
        # whatever order the kernel felt like. Leaving them unsorted makes
        # this list's position disagree with the cluster's own numbering, and
        # the members then refuse each other's handshakes with "member index N
        # is not in the member set".
        self._ports = sorted(self._reserve() for _ in range(size))

        specs = [
            MemberSpec(
                host="127.0.0.1", client_port=self._ports[index] - 1,
                peer_port=self._ports[index], name=f"m{index}",
            )
            for index in range(size)
        ]
        self._shared = [
            derive_cluster(
                specs, local_host="127.0.0.1",
                local_peer_port=self._ports[index],
                namespace="/soak", tls=False, flavour=RAFT_FLAVOUR,
            )
            for index in range(size)
        ]
        self._layouts = [derive_raft_layout(s) for s in self._shared]
        self._mesh = ProxyMesh({
            index: self._ports[index] for index in range(size)
        })
        self.network = SocketFaults(self._mesh)
        self.members: list[SocketMember] = []

    def _reserve(self) -> int:
        """Hold the socket until the instant before binding.

        Same reasoning as the other rigs: the bind-and-close idiom leaves a
        window in which another test takes the port, and the failure then
        surfaces far from whatever stole it.
        """
        held = socket.socket()
        held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        held.bind(("127.0.0.1", 0))
        self._held.append(held)
        return int(held.getsockname()[1])

    def _release(self) -> None:
        for held in self._held:
            with contextlib.suppress(Exception):
                held.close()
        self._held.clear()

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        # The proxies bind *first*, while the members' ports are still held.
        # Releasing first and then letting the proxies ask for any free port
        # lets the kernel hand one of them a port a member is about to want,
        # and the member's bind then fails with EADDRINUSE -- a collision this
        # rig created for itself, in the window its own reservation exists to
        # close.
        await self._mesh.start()
        self._release()
        self.members = [
            SocketMember(
                layout, root=self.root, bind_port=self._ports[index],
                dial=self._mesh.dial_targets(index), timing=self.timing,
            )
            for index, layout in enumerate(self._layouts)
        ]
        # Concurrently: members really do start independently, and starting
        # them in turn makes the first wait out its whole election timeout
        # before the second exists.
        await asyncio.gather(*(m.node.start() for m in self.members))

    async def close(self) -> None:
        for member in self.members:
            with contextlib.suppress(Exception):
                await member.node.close()
        self.members = []
        await self._mesh.close()
        self._release()

    async def restart(self, index: int) -> SocketMember:
        """Stop a member and bring it back with an empty log.

        The term store survives -- that is the whole point of it being on disk
        -- and the log does not, exactly as a real restart leaves it. Unlike
        the memory harness this also tears down and rebuilds a real listener,
        so the peers' reconnection and re-handshake paths are exercised.
        """
        await self.members[index].node.close()
        await self._await_port_free(self._ports[index])
        replacement = SocketMember(
            self._layouts[index], root=self.root,
            bind_port=self._ports[index],
            dial=self._mesh.dial_targets(index), timing=self.timing,
        )
        self.members[index] = replacement
        await replacement.node.start()
        return replacement

    @staticmethod
    async def _await_port_free(port: int, timeout: float = 5.0) -> None:
        """Wait for a just-closed listener's port to become bindable again.

        ``RaftTransport.close`` bounds its wait on ``wait_closed`` and gives up
        with a warning rather than hanging shutdown forever -- correct there,
        but it means the listener is occasionally still open when the
        replacement member tries to take the same port back, and the restart
        then dies with EADDRINUSE.

        A real deployment has the same race and the same answer: the
        supervisor retries. This is that retry, bounded, so a genuinely stuck
        port fails the test instead of hanging it.
        """
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            probe = socket.socket()
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                await asyncio.sleep(0.02)
                continue
            finally:
                probe.close()
            return
        raise AssertionError(
            f"port {port} did not become free within {timeout}s of closing "
            f"the member that held it",
        )

    # -- observation -----------------------------------------------------

    def __getitem__(self, index: int) -> SocketMember:
        return self.members[index]

    @property
    def leaders(self) -> list[SocketMember]:
        return [m for m in self.members if m.node.role is Role.LEADER]

    async def elect(self, *, timeout: float = 10.0) -> SocketMember:
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
        for _ in range(rounds):
            await asyncio.sleep(self.timing.heartbeat)

    def attach_backends(self, **_: Any) -> list[Any]:
        raise NotImplementedError(
            "the socket cluster drives nodes directly; the registry-level "
            "backend has its own rig in nmos/registry/tests/rigs/",
        )
