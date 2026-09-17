# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The raft implementation of :class:`ClusterRig`, over real sockets.

Deliberately *not* the in-memory harness. ``nmos/raft/tests/_harness.py`` exists
to make consensus scenarios deterministic -- partitions as method calls,
restarts without timing -- and it does that by replacing the transport
entirely. Which means everything it proves, it proves about a cluster that has
never opened a socket.

This rig is the other half. It runs ``RaftTransport`` for real: an asyncio
listener per member, two links per peer, the ``Hello``/``HelloAck`` handshake,
framing, checksums, and reconnection. Nothing else in the suite exercises that
code, and a wire format with committed golden vectors is still only a wire
format until two processes have actually agreed over one.

Members *are* registries here
-----------------------------
With etcd, ``kill(2)`` stops a storage process and the registry in front of it
keeps running as a client of the survivors. With raft there is no separate
storage process: the member and the registry are one object, so ``kill(2)``
closes it. Both are honestly "member 2 is gone", which is what lets a shared
conformance test assert on it -- but the asymmetry is real, and it is why the
one conformance test that is specifically about a registry outliving its
storage member stays etcd-only.
"""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path
from typing import Any

from nmos.cluster.layout import ClusterLayout, MemberSpec, derive_cluster
from nmos.raft.cluster import RAFT_FLAVOUR, derive_raft_layout
from nmos.raft.cursors import CursorAllocator
from nmos.raft.machine import StateMachine
from nmos.raft.node import RaftNode, RaftTiming
from nmos.raft.ownership import OwnershipTable
from nmos.raft.persist import TermStore
from nmos.raft.snapshot import SnapshotStore
from nmos.raft.transport import RaftTransport

# Compressed from the production defaults so a test settles in milliseconds,
# with the election window kept an order of magnitude above the heartbeat --
# that ratio is what stops a healthy leader losing its followers' timers, and
# scaling both together preserves it. Slower than the in-memory harness
# because real sockets are slower than method calls.
RIG_TIMING = RaftTiming(
    heartbeat=0.020,
    election_min=0.150,
    election_max=0.300,
)


class RaftRig:
    """``size`` in-process raft members on loopback, talking over TCP."""

    def __init__(self, *, size: int, root: Path) -> None:
        self._root = root
        self._reserved: list[socket.socket] = []
        self._ports = [self._reserve() for _ in range(size)]
        self._status_ports = [self._reserve() for _ in range(size)]
        self._released = False

        specs = [
            MemberSpec(
                host="127.0.0.1",
                client_port=self._status_ports[index],
                peer_port=self._ports[index],
                name=f"m{index}",
            )
            for index in range(size)
        ]
        self._shared: list[ClusterLayout] = [
            derive_cluster(
                specs,
                local_host="127.0.0.1",
                local_peer_port=self._ports[index],
                namespace="/conformance",
                tls=False,
                flavour=RAFT_FLAVOUR,
            )
            for index in range(size)
        ]
        self._layouts = [derive_raft_layout(shared) for shared in self._shared]
        self._backends: dict[int, Any] = {}

    # -- ports ----------------------------------------------------------

    def _reserve(self) -> int:
        """Hold the socket until the instant before binding.

        Same reasoning as the etcd rig: the bind-and-close idiom leaves a
        window in which another test takes the port, and the failure surfaces
        far from the test that stole it.
        """
        held = socket.socket()
        held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        held.bind(("127.0.0.1", 0))
        self._reserved.append(held)
        port: int = held.getsockname()[1]
        return port

    def _release(self) -> None:
        if self._released:
            return
        for held in self._reserved:
            held.close()
        self._reserved.clear()
        self._released = True

    # -- ClusterRig -----------------------------------------------------

    @property
    def size(self) -> int:
        return len(self._layouts)

    @property
    def quorum(self) -> int:
        return self.size // 2 + 1

    @property
    def failures_tolerated(self) -> int:
        return self.size - self.quorum

    async def start_all(self) -> None:
        """Nothing separate to start.

        The cluster forms when the backends do, because the members are the
        registries. Releasing the reserved ports here is the whole of it.
        """
        self._release()

    async def stop_all(self) -> None:
        for index in list(self._backends):
            await self._close(index)
        self._release()

    async def backend_for(
        self, index: int, registry: Any, namespace: str, **overrides: Any,
    ) -> Any:
        from nmos.registry.raft_backend import RaftRegistryBackend

        self._release()
        backend = RaftRegistryBackend(
            registry,
            self._config_for(index, namespace, **overrides),
            self._node_for(index, registry),
        )
        self._backends[index] = backend
        await backend.start()
        return backend

    async def kill(self, index: int) -> None:
        """Close the member. Here that is the registry too -- see the module docstring."""
        await self._close(index)

    async def lose_quorum(self) -> None:
        survivors = self.size // 2 + 1
        for index in range(self.size - 1, survivors - 2, -1):
            await self._close(index)
        # Let the survivors notice. Without this a test can assert on a
        # backend that has not yet been told its peers are gone, and read
        # READY from a cluster that is already stuck.
        await asyncio.sleep(RIG_TIMING.election_max)

    # -- construction ---------------------------------------------------

    def _node_for(self, index: int, registry: Any) -> RaftNode:
        layout = self._layouts[index]
        transport = RaftTransport(
            local=layout.local.index,
            peers=layout.peer_targets(),
            bind=("127.0.0.1", layout.local.port),
            cluster_id=layout.cluster_id,
            member_name=layout.local.name,
            incarnation=0,
            rpc_timeout=1.0,
        )
        snapshots = SnapshotStore(registry.store)
        machine = StateMachine(
            registry,
            ownership=OwnershipTable(),
            cursors=CursorAllocator(layout.local.index),
            member=layout.local.index,
            snapshots=snapshots,
        )
        node = RaftNode(
            layout,
            transport=transport,
            terms=TermStore(self._root / f"m{index}-state.json"),
            machine=machine,
            timing=RIG_TIMING,
            snapshots=snapshots,
        )
        # The node loaded the term store, and loading is what increments the
        # counter -- so the transport takes the number the node is using
        # rather than reading it again and reporting one restart too many.
        transport.set_incarnation(node.incarnation)
        return node

    def _config_for(self, index: int, namespace: str, **overrides: Any) -> Any:
        import dataclasses

        from nmos.registry.distributed import RaftConfig

        layout = self._layouts[index]
        config = RaftConfig(
            layout=self._shared[index],
            endpoints=tuple(f"{m.host}:{m.port}" for m in layout.members),
            namespace=namespace,
            tls=False,
            certificate="", key="", trusted_root_ca=(), certificate_name="",
            rpc_timeout=1.0,
            mutation_timeout=5.0,
            state_dir=self._root,
            crl_file="",
            peer_port=layout.local.port,
        )
        return dataclasses.replace(config, **overrides) if overrides else config

    async def _close(self, index: int) -> None:
        backend = self._backends.pop(index, None)
        if backend is not None:
            await backend.close()
