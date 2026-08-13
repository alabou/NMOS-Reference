# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""End-to-end against a real THREE-member etcd cluster.

    pytest nmos/registry/tests/test_etcd_cluster_e2e.py -m e2e

Everything else in the distributed test suite runs against a single member,
because the machinery under test there is preload/watch/fence rather than Raft.
These are the ones that need a genuine quorum: client failover when a member
dies, and the claim that three members tolerate one failure.
"""

from __future__ import annotations

import asyncio
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from nmos.registry.backend import BackendState
from nmos.registry.decode import decode_resource
from nmos.registry.etcd_backend import EtcdRegistryBackend
from nmos.registry.tests._fixtures import make_device, make_node, make_sender
from nmos.registry.tests.test_etcd_backend import _eventually, build_registry
from nmos.registry.types import Body, ResourceType

pytestmark = pytest.mark.e2e


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@dataclass
class Member:
    name: str
    client_port: int
    peer_port: int
    data_dir: Path
    process: subprocess.Popen[bytes] | None = None

    @property
    def endpoint(self) -> str:
        return f"127.0.0.1:{self.client_port}"

    @property
    def peer_url(self) -> str:
        return f"http://127.0.0.1:{self.peer_port}"


class Cluster:
    """Three etcd members on loopback, differing by port."""

    def __init__(self, binary: str, root: Path) -> None:
        self.binary = binary
        # Ports are reserved by HOLDING the sockets, and released only in the
        # instant before etcd is spawned. The usual bind-and-close idiom leaves
        # a window in which another test -- and the full e2e suite starts a lot
        # of servers -- can take the port before etcd binds it, which surfaces
        # as a cluster that mysteriously fails to form.
        self._reserved: list[socket.socket] = []
        self.members = [
            Member(
                name=f"m{index}",
                client_port=self._reserve(),
                peer_port=self._reserve(),
                data_dir=root / f"m{index}",
            )
            for index in range(3)
        ]

    def _reserve(self) -> int:
        held = socket.socket()
        held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        held.bind(("127.0.0.1", 0))
        self._reserved.append(held)
        port: int = held.getsockname()[1]
        return port

    def _release(self) -> None:
        for held in self._reserved:
            held.close()
        self._reserved.clear()

    @property
    def initial_cluster(self) -> str:
        return ",".join(f"{m.name}={m.peer_url}" for m in self.members)

    @property
    def endpoints(self) -> list[str]:
        return [m.endpoint for m in self.members]

    def start(self, member: Member, *, new: bool) -> None:
        client = f"http://127.0.0.1:{member.client_port}"
        member.process = subprocess.Popen(
            [
                self.binary,
                "--name", member.name,
                "--data-dir", str(member.data_dir),
                "--listen-client-urls", client,
                "--advertise-client-urls", client,
                "--listen-peer-urls", member.peer_url,
                "--initial-advertise-peer-urls", member.peer_url,
                "--initial-cluster", self.initial_cluster,
                "--initial-cluster-state", "new" if new else "existing",
                "--initial-cluster-token", "nmos-cluster-e2e",
                "--log-level", "error",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def start_all(self) -> None:
        # Concurrently: with initial-cluster-state=new each member blocks until
        # it can reach a quorum of peers, so starting them one at a time and
        # waiting for each would deadlock on the first.
        self._release()
        for member in self.members:
            self.start(member, new=True)
        deadline = time.monotonic() + 90.0
        while time.monotonic() < deadline:
            dead = [m for m in self.members if m.process and m.process.poll() is not None]
            if dead:
                raise RuntimeError(
                    "etcd member(s) "
                    + ", ".join(
                        f"{m.name} (exit {m.process.returncode})"
                        for m in dead if m.process
                    )
                    + " exited during startup",
                )
            if all(self._healthy(m) for m in self.members):
                return
            time.sleep(0.2)
        raise RuntimeError(
            "cluster did not form: "
            + ", ".join(
                f"{m.name} {'up' if self._healthy(m) else 'down'}"
                for m in self.members
            ),
        )

    def _healthy(self, member: Member) -> bool:
        """Serving, not merely listening -- the port opens before etcd answers."""
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{member.client_port}/health", timeout=1.0,
            ) as reply:
                return b'"health":"true"' in reply.read()
        except (urllib.error.URLError, OSError, TimeoutError):
            return False

    def kill(self, member: Member) -> None:
        if member.process is None:
            return
        member.process.kill()
        member.process.wait(timeout=10)
        member.process = None

    def stop_all(self) -> None:
        for member in self.members:
            self.kill(member)
        self._release()


@pytest.fixture
def cluster(tmp_path: Path) -> Iterator[Cluster]:
    from nmos.etcd.tests.etcd_server import BUNDLED_ETCD

    binary = str(BUNDLED_ETCD) if BUNDLED_ETCD.is_file() else shutil.which("etcd")
    if binary is None:
        pytest.skip("etcd not installed; run ./install-etcd.sh")

    created = Cluster(binary, tmp_path)
    created.start_all()
    try:
        yield created
    finally:
        created.stop_all()


def _config_for(cluster: Cluster, namespace: str, local_index: int) -> Any:
    """A DistributedConfig for one registry, aimed at all three members."""
    from nmos.etcd.cluster import MemberSpec, derive_cluster
    from nmos.registry.distributed import DistributedConfig

    specs = [
        MemberSpec(
            host="127.0.0.1",
            client_port=m.client_port,
            peer_port=m.peer_port,
            name=m.name,
            bind_address="127.0.0.1",
        )
        for m in cluster.members
    ]
    local = cluster.members[local_index]
    layout = derive_cluster(
        specs,
        local_host="127.0.0.1",
        local_peer_port=local.peer_port,
        namespace=namespace,
        tls=False,
    )
    return DistributedConfig(
        layout=layout,
        endpoints=tuple(
            # Local first, then the others -- the pool tries them in order.
            [local.endpoint] + [
                m.endpoint for m in cluster.members if m is not local
            ],
        ),
        namespace=namespace,
        external=True,
        binary="", data_dir=Path(), bootstrap=False,
        tls=False, certificate="", key="", trusted_root_ca=(),
        certificate_name="", client_crl_file="", peer_crl_file="",
        rpc_timeout=3.0, mutation_timeout=10.0,
    )


async def _register(backend: EtcdRegistryBackend, rt: ResourceType, raw: dict):
    decode_resource(rt, raw)
    return await backend.register(rt, Body.from_data(raw))


# ---------------------------------------------------------------------------
# Three registries, one cluster
# ---------------------------------------------------------------------------

async def test_three_registries_share_one_view(cluster: Cluster) -> None:
    """Register on member 0, read it back on members 1 and 2."""
    namespace = f"/nmos-test/cluster/{uuid.uuid4().hex[:8]}"
    registries = []
    backends = []
    for index in range(3):
        registry = build_registry()
        backend = EtcdRegistryBackend(
            registry, _config_for(cluster, namespace, index),
        )
        await backend.start()
        registries.append(registry)
        backends.append(backend)

    try:
        assert all(b.state is BackendState.READY for b in backends)
        assert backends[0]._config.layout.failures_tolerated == 1

        node, device, sender = make_node(), make_device(), make_sender()
        for rt, raw in (
            (ResourceType.NODE, node),
            (ResourceType.DEVICE, device),
            (ResourceType.SENDER, sender),
        ):
            assert (await _register(backends[0], rt, raw)).ok

        for registry in registries[1:]:
            await _eventually(
                lambda r=registry: r.store.get(
                    ResourceType.SENDER, sender["id"],
                ) is not None,
                timeout=20.0,
            )

        # Identical content AND identical cursors: the latter is what makes a
        # paged Query answer the same on whichever member serves it.
        reference = registries[0].store.get(ResourceType.SENDER, sender["id"])
        assert reference is not None
        for registry in registries[1:]:
            other = registry.store.get(ResourceType.SENDER, sender["id"])
            assert other is not None
            assert other.raw == reference.raw
            assert other.created == reference.created
            assert other.updated == reference.updated
    finally:
        for backend in backends:
            await backend.close()


async def test_registries_can_write_concurrently_to_different_nodes(
    cluster: Cluster,
) -> None:
    """Different Nodes are disjoint prefixes, so there is no cross-member contention."""
    from nmos.registry.tests._fixtures import NODE_ID, NODE_ID_2

    namespace = f"/nmos-test/cluster/{uuid.uuid4().hex[:8]}"
    registry_a, registry_b = build_registry(), build_registry()
    backend_a = EtcdRegistryBackend(registry_a, _config_for(cluster, namespace, 0))
    backend_b = EtcdRegistryBackend(registry_b, _config_for(cluster, namespace, 1))
    await backend_a.start()
    await backend_b.start()

    try:
        results = await asyncio.gather(
            _register(backend_a, ResourceType.NODE, make_node(NODE_ID)),
            _register(backend_b, ResourceType.NODE, make_node(NODE_ID_2)),
        )
        assert all(result.ok and result.created for result in results)

        for registry in (registry_a, registry_b):
            for node_id in (NODE_ID, NODE_ID_2):
                await _eventually(
                    lambda r=registry, n=node_id: r.store.get(
                        ResourceType.NODE, n,
                    ) is not None,
                    timeout=20.0,
                )
    finally:
        await backend_a.close()
        await backend_b.close()


async def test_losing_the_local_member_fails_over_to_the_others(
    cluster: Cluster,
) -> None:
    """Three members tolerate one failure -- asserted, not assumed.

    The registry's channel pool dials every member with the local one first, so
    killing the local member must cost a failover, not an outage. This is the
    behaviour that makes the plan's 1/3/5 table true.
    """
    namespace = f"/nmos-test/cluster/{uuid.uuid4().hex[:8]}"
    registry = build_registry()
    backend = EtcdRegistryBackend(registry, _config_for(cluster, namespace, 0))
    await backend.start()

    try:
        node = make_node()
        assert (await _register(backend, ResourceType.NODE, node)).ok

        # Kill the member this registry prefers.
        cluster.kill(cluster.members[0])
        await asyncio.sleep(1.0)

        # Query is unaffected -- it never touched etcd.
        assert registry.store.get(ResourceType.NODE, node["id"]) is not None

        # And writes still work, through another member.
        device = make_device()
        await _eventually(
            lambda: backend.state in (
                BackendState.READY, BackendState.DEGRADED,
            ),
            timeout=10.0,
        )
        for _ in range(20):
            try:
                result = await _register(backend, ResourceType.DEVICE, device)
                if result.ok:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.5)
        else:
            pytest.fail(
                "no write succeeded after losing the local member; a "
                "3-member cluster must tolerate one failure",
            )

        assert registry.store.get(ResourceType.DEVICE, device["id"]) is not None
    finally:
        await backend.close()


async def test_quorum_loss_stops_writes_but_not_reads(cluster: Cluster) -> None:
    """Two of three gone is beyond what the cluster promises.

    Writes must stop -- committing without quorum is exactly what Raft exists
    to prevent -- but the cached Query view must keep serving.
    """
    namespace = f"/nmos-test/cluster/{uuid.uuid4().hex[:8]}"
    registry = build_registry()
    config = _config_for(cluster, namespace, 0)
    config = type(config)(**{**config.__dict__, "rpc_timeout": 1.0,
                             "mutation_timeout": 2.0})
    backend = EtcdRegistryBackend(registry, config)
    await backend.start()

    try:
        node = make_node()
        assert (await _register(backend, ResourceType.NODE, node)).ok

        cluster.kill(cluster.members[1])
        cluster.kill(cluster.members[2])
        await asyncio.sleep(1.0)

        with pytest.raises(Exception):
            await _register(backend, ResourceType.DEVICE, make_device())

        # Reads unaffected.
        assert registry.store.get(ResourceType.NODE, node["id"]) is not None
        assert backend.state.serves_queries is True
    finally:
        await backend.close()
