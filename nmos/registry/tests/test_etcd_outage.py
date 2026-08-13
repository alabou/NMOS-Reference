# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Degradation, outage and recovery.

    pytest nmos/registry/tests/test_etcd_outage.py -m e2e

The property under test throughout: **losing etcd must not lose Query.** A
registry serving a cached view during an outage is still useful to every
Controller in the facility, and refusing reads because writes are impossible
turns a partial outage into a total one.

These start their own etcd rather than using the session fixture, because they
need to kill it.
"""

from __future__ import annotations

import asyncio
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

from nmos.registry.backend import BackendState, MutationUnavailable
from nmos.registry.decode import decode_resource
from nmos.registry.etcd_backend import EtcdRegistryBackend
from nmos.registry.tests._fixtures import make_device, make_node, make_sender
from nmos.registry.tests.test_etcd_backend import _config, _eventually, build_registry
from nmos.registry.types import Body, ResourceType

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# A killable etcd
# ---------------------------------------------------------------------------

class Killable:
    """One etcd this test can stop and restart, keeping its data."""

    def __init__(self, binary: str, data_dir: Path) -> None:
        self.binary = binary
        self.data_dir = data_dir
        self.client_port = _free_port()
        self.peer_port = _free_port()
        self.process: subprocess.Popen[bytes] | None = None

    @property
    def endpoint(self) -> str:
        return f"127.0.0.1:{self.client_port}"

    def start(self, *, new: bool) -> None:
        client = f"http://127.0.0.1:{self.client_port}"
        peer = f"http://127.0.0.1:{self.peer_port}"
        self.process = subprocess.Popen(
            [
                self.binary,
                "--name", "outage",
                "--data-dir", str(self.data_dir),
                "--listen-client-urls", client,
                "--advertise-client-urls", client,
                "--listen-peer-urls", peer,
                "--initial-advertise-peer-urls", peer,
                "--initial-cluster", f"outage={peer}",
                "--initial-cluster-state", "new" if new else "existing",
                "--initial-cluster-token", "nmos-outage-test",
                "--log-level", "error",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Wait until etcd is SERVING, not merely until the port accepts.
        # A listening socket appears before the server can answer, and these
        # tests deliberately run with a 1s RPC deadline, so "port is open" loses
        # that race often enough to be flaky. etcd multiplexes its /health
        # endpoint onto the client port, which is the cheapest true readiness
        # signal available without a gRPC client.
        deadline = time.monotonic() + 30.0
        health_url = f"http://127.0.0.1:{self.client_port}/health"
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(health_url, timeout=1.0) as reply:
                    if b'"health":"true"' in reply.read():
                        return
            except (urllib.error.URLError, OSError, TimeoutError):
                pass
            time.sleep(0.1)
        raise RuntimeError("etcd did not become healthy")

    def kill(self) -> None:
        if self.process is None:
            return
        self.process.kill()
        self.process.wait(timeout=10)
        self.process = None
        # Wait for the port to actually free, so a reconnect attempt really does
        # fail rather than landing on a socket in TIME_WAIT.
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                probe.settimeout(0.25)
                if probe.connect_ex(("127.0.0.1", self.client_port)) != 0:
                    return
            time.sleep(0.1)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@pytest.fixture
def killable(tmp_path: Path) -> Iterator[Killable]:
    import shutil

    from nmos.etcd.tests.etcd_server import BUNDLED_ETCD

    binary = str(BUNDLED_ETCD) if BUNDLED_ETCD.is_file() else shutil.which("etcd")
    if binary is None:
        pytest.skip("etcd not installed; run ./install-etcd.sh")

    server = Killable(binary, tmp_path / "member")
    server.start(new=True)
    try:
        yield server
    finally:
        server.kill()


@pytest.fixture
async def outage_backend(
    killable: Killable,
) -> AsyncIterator[tuple[Any, EtcdRegistryBackend, Killable]]:
    namespace = f"/nmos-test/outage/{uuid.uuid4().hex[:8]}"
    registry = build_registry()
    config = _config(killable.endpoint, namespace)
    # Short deadlines: these tests deliberately make etcd unreachable, and the
    # point is the answer, not how long it takes to give up.
    config = type(config)(**{**config.__dict__, "rpc_timeout": 1.0,
                             "mutation_timeout": 2.0})
    backend = EtcdRegistryBackend(registry, config)
    await backend.start()
    try:
        yield registry, backend, killable
    finally:
        await backend.close()


async def _register(backend: EtcdRegistryBackend, rt: ResourceType, raw: dict):
    decode_resource(rt, raw)
    return await backend.register(rt, Body.from_data(raw))


# ---------------------------------------------------------------------------
# Outage
# ---------------------------------------------------------------------------

async def test_query_keeps_serving_when_etcd_dies(
    outage_backend: tuple[Any, EtcdRegistryBackend, Killable],
) -> None:
    """The headline property: losing etcd must not lose the Query API."""
    registry, backend, server = outage_backend

    node, device, sender = make_node(), make_device(), make_sender()
    for rt, raw in (
        (ResourceType.NODE, node),
        (ResourceType.DEVICE, device),
        (ResourceType.SENDER, sender),
    ):
        assert (await _register(backend, rt, raw)).ok

    server.kill()
    await asyncio.sleep(1.0)

    # Every resource still readable, from the cached view.
    assert registry.store.get(ResourceType.NODE, node["id"]) is not None
    assert registry.store.get(ResourceType.DEVICE, device["id"]) is not None
    assert registry.store.get(ResourceType.SENDER, sender["id"]) is not None
    assert registry.store.count_extant(ResourceType.SENDER) == 1


async def test_mutations_become_unavailable_when_etcd_dies(
    outage_backend: tuple[Any, EtcdRegistryBackend, Killable],
) -> None:
    """Writes fail loudly and retryably; they are never queued.

    Queuing them would mean acknowledging a registration the cluster has not
    accepted, and a Node that believes it is registered stops re-registering.
    """
    _registry, backend, server = outage_backend
    assert (await _register(backend, ResourceType.NODE, make_node())).ok

    server.kill()

    with pytest.raises(MutationUnavailable):
        await _register(backend, ResourceType.NODE, make_node())


async def test_the_backend_reports_degraded_after_an_outage(
    outage_backend: tuple[Any, EtcdRegistryBackend, Killable],
) -> None:
    """DEGRADED is what makes the handler answer 503 rather than 500."""
    _registry, backend, server = outage_backend
    assert (await _register(backend, ResourceType.NODE, make_node())).ok

    server.kill()
    with pytest.raises(MutationUnavailable):
        await _register(backend, ResourceType.NODE, make_node())

    assert backend.state is BackendState.DEGRADED
    assert backend.state.accepts_mutations is False
    # ... but the view is still worth serving, which is the whole point.
    assert backend.state.serves_queries is True


async def test_local_collection_stays_suspended_during_an_outage(
    outage_backend: tuple[Any, EtcdRegistryBackend, Killable],
) -> None:
    """The most damaging thing an outage could trigger.

    If a member fell back to health-based expiry while etcd was unreachable, it
    would collect every Node whose heartbeats it could no longer process -- and
    on recovery the cluster would tell it they were alive all along.
    """
    registry, backend, server = outage_backend
    node = make_node()
    assert (await _register(backend, ResourceType.NODE, node)).ok

    server.kill()
    for resource_type in ResourceType:
        for resource in registry.store.iter_extant(resource_type):
            resource.health = 0

    assert await backend.collect_garbage() == 0
    assert registry.store.get(ResourceType.NODE, node["id"]) is not None


async def test_heartbeat_is_unavailable_rather_than_404_during_an_outage(
    outage_backend: tuple[Any, EtcdRegistryBackend, Killable],
) -> None:
    """404 would be a lie that makes the Node re-register everything.

    ``Behaviour - Registration.md:112-114`` makes 404 mean "the registry has
    forgotten you". Answering that because *we* cannot reach etcd would trigger
    a full re-registration storm from every Node at exactly the moment the
    cluster is least able to absorb one.
    """
    _registry, backend, server = outage_backend
    node = make_node()
    assert (await _register(backend, ResourceType.NODE, node)).ok

    server.kill()

    with pytest.raises(MutationUnavailable):
        await backend.heartbeat(node["id"])


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------

async def test_recovery_returns_to_ready_and_accepts_writes(
    outage_backend: tuple[Any, EtcdRegistryBackend, Killable],
) -> None:
    registry, backend, server = outage_backend
    node = make_node()
    assert (await _register(backend, ResourceType.NODE, node)).ok

    server.kill()
    with pytest.raises(MutationUnavailable):
        await _register(backend, ResourceType.NODE, make_node())
    assert backend.state is BackendState.DEGRADED

    server.start(new=False)
    await _eventually(
        lambda: backend.state is BackendState.READY, timeout=30.0,
    )

    device = make_device()
    assert (await _register(backend, ResourceType.DEVICE, device)).ok
    assert registry.store.get(ResourceType.DEVICE, device["id"]) is not None
    # And nothing was lost across the outage.
    assert registry.store.get(ResourceType.NODE, node["id"]) is not None


async def test_changes_made_during_the_outage_are_picked_up_on_recovery(
    outage_backend: tuple[Any, EtcdRegistryBackend, Killable],
) -> None:
    """The watch resumes at last_applied + 1, so nothing in between is skipped."""
    registry, backend, server = outage_backend
    node = make_node()
    assert (await _register(backend, ResourceType.NODE, node)).ok

    server.kill()
    with pytest.raises(MutationUnavailable):
        await _register(backend, ResourceType.NODE, make_node())

    server.start(new=False)
    await _eventually(
        lambda: backend.state is BackendState.READY, timeout=30.0,
    )

    # Write straight to etcd, as another member would have.
    from nmos.etcd.channel import EtcdChannelPool, parse_endpoints
    from nmos.etcd.kv import EtcdKV, put_op
    from nmos.registry.keys import Namespace
    from nmos.registry.tests.test_etcd_backend import _envelope

    pool = EtcdChannelPool(
        parse_endpoints([server.endpoint]),
        credentials=None, target_name=None, rpc_timeout=5.0,
    )
    try:
        ns = Namespace(backend.namespace.prefix)
        other = make_node("6f1c2f0a-9d3b-4e77-8a21-5c4b7e0d9a12")
        await EtcdKV(pool).txn(success=[
            put_op(ns.node(other["id"]), _envelope(ResourceType.NODE, other)),
        ])
    finally:
        await pool.close()

    await _eventually(
        lambda: registry.store.get(
            ResourceType.NODE, "6f1c2f0a-9d3b-4e77-8a21-5c4b7e0d9a12",
        ) is not None,
        timeout=20.0,
    )


# ---------------------------------------------------------------------------
# Compaction
# ---------------------------------------------------------------------------

async def test_compaction_triggers_a_resnapshot_and_keeps_serving(
    outage_backend: tuple[Any, EtcdRegistryBackend, Killable],
) -> None:
    """Compaction is the one failure a reconnect cannot fix.

    The replacement snapshot is built off to the side and installed in one
    assignment, so Query answers from the previous view right up to the swap
    and never sees an empty or half-loaded store.
    """
    registry, backend, _server = outage_backend

    node, device, sender = make_node(), make_device(), make_sender()
    for rt, raw in (
        (ResourceType.NODE, node),
        (ResourceType.DEVICE, device),
        (ResourceType.SENDER, sender),
    ):
        assert (await _register(backend, rt, raw)).ok

    # Compact past everything the watch could resume from.
    head = await backend.kv.range_at(b"/nothing")
    await backend.kv.compact(head.revision)

    # Force the watch to reconnect into the compacted range.
    node["version"] = make_node()["version"]
    for _ in range(3):
        try:
            await _register(backend, ResourceType.NODE, node)
        except MutationUnavailable:
            pass
        await asyncio.sleep(0.2)

    await _eventually(
        lambda: backend.state is BackendState.READY, timeout=30.0,
    )
    # Every resource survived the rebuild.
    assert registry.store.get(ResourceType.NODE, node["id"]) is not None
    assert registry.store.get(ResourceType.DEVICE, device["id"]) is not None
    assert registry.store.get(ResourceType.SENDER, sender["id"]) is not None


async def test_a_restart_with_no_etcd_cannot_reach_ready(
    killable: Killable,
) -> None:
    """There is no durable local snapshot, so a cold start needs etcd.

    Serving an empty registry would be far worse than serving nothing: every
    Controller would conclude the facility had gone away.
    """
    from nmos.etcd.errors import EtcdError

    killable.kill()

    namespace = f"/nmos-test/outage/{uuid.uuid4().hex[:8]}"
    registry = build_registry()
    config = _config(killable.endpoint, namespace)
    config = type(config)(**{**config.__dict__, "rpc_timeout": 1.0,
                             "mutation_timeout": 2.0})
    backend = EtcdRegistryBackend(registry, config)
    try:
        with pytest.raises(EtcdError):
            await backend.start()
        assert backend.state is not BackendState.READY
    finally:
        await backend.close()
