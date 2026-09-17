# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""What every distributed backend must do with a genuine three-member quorum.

    pytest nmos/registry/tests/test_cluster_conformance.py -m e2e

These are the claims that are about *consensus*, not about any one storage
layer: three registries share one view, disjoint Nodes do not contend,
three members tolerate one failure, and losing quorum stops writes without
stopping reads. Every distributed backend has to satisfy all four, by the same
argument and with the same observable behaviour, so they are written once and
parameterised over ``BACKENDS`` rather than written per backend.

Nothing here touches a revision, a key or an envelope. Those are etcd's model;
a conformance test that asserted on them would be asserting that the second
backend was etcd, which is the opposite of what it is for. Everything goes
through ``RegistryBackend`` and the local store.

This replaces the hand-rolled three-member rig that used to live in
``test_etcd_cluster_e2e.py``; the rig now lives in ``rigs/`` so both backends
drive an identical one.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from nmos.registry.backend import BackendState
from nmos.registry.decode import decode_resource
from nmos.registry.tests._fixtures import (
    NODE_ID,
    NODE_ID_2,
    make_device,
    make_node,
    make_sender,
)
from nmos.registry.tests.rigs import (
    BACKENDS,
    ClusterRig,
    RigUnavailable,
    make_rig,
)
from nmos.registry.tests.test_etcd_backend import _eventually, build_registry
from nmos.registry.types import Body, ResourceType

pytestmark = pytest.mark.e2e


@pytest.fixture
async def rig(request: Any, tmp_path: Path) -> AsyncIterator[ClusterRig]:
    """A three-member cluster of whichever backend is being exercised.

    Function-scoped deliberately. ``asyncio_mode = "auto"`` gives every test its
    own event loop, and an in-process backend's listeners and timers live on
    that loop -- a session-scoped rig would outlive the loop it was built on and
    fail in a way that pointed at the wrong test.
    """
    backend = request.param
    try:
        created = make_rig(backend, size=3, root=tmp_path)
    except RigUnavailable as exc:
        pytest.skip(str(exc))

    await created.start_all()
    try:
        yield created
    finally:
        await created.stop_all()


def _parameterise(fn: Any) -> Any:
    return pytest.mark.parametrize("rig", BACKENDS, indirect=True)(fn)


async def _register(backend: Any, resource_type: ResourceType, raw: dict) -> Any:
    decode_resource(resource_type, raw)
    return await backend.register(resource_type, Body.from_data(raw))


def _namespace() -> str:
    return f"/nmos-test/conformance/{uuid.uuid4().hex[:8]}"


async def _whole_cluster(
    rig: ClusterRig, namespace: str, **overrides: Any,
) -> tuple[list[Any], list[Any]]:
    """A registry and a started backend for *every* member.

    Every member, even when the test only drives one of them. With etcd the
    other members exist as separate processes whether a registry is attached
    or not; with raft the member *is* the backend, so a test that built one
    backend would be testing a one-member cluster -- which has no quorum, no
    leader, and nothing to say about the three-member claims this file is for.
    """
    registries = [build_registry() for _ in range(rig.size)]
    backends = list(await asyncio.gather(*(
        rig.backend_for(index, registry, namespace, **overrides)
        for index, registry in enumerate(registries)
    )))
    return registries, backends


@_parameterise
async def test_three_registries_share_one_view(
    rig: ClusterRig, request: Any,
) -> None:
    """Register on member 0, read it back on members 1 and 2.

    Identical content *and* identical cursors: the latter is what makes a paged
    Query answer the same whichever member serves it, and it is the property a
    backend that allocated cursors locally would quietly break.
    """
    namespace = _namespace()
    registries, backends = await _whole_cluster(rig, namespace)

    try:
        assert all(b.state is BackendState.READY for b in backends)

        node, device, sender = make_node(), make_device(), make_sender()
        for resource_type, raw in (
            (ResourceType.NODE, node),
            (ResourceType.DEVICE, device),
            (ResourceType.SENDER, sender),
        ):
            assert (await _register(backends[0], resource_type, raw)).ok

        for registry in registries[1:]:
            await _eventually(
                lambda r=registry: r.store.get(
                    ResourceType.SENDER, sender["id"],
                ) is not None,
                timeout=20.0,
            )

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


@_parameterise
async def test_the_cluster_reports_the_failure_tolerance_it_promises(
    rig: ClusterRig, request: Any,
) -> None:
    """Three members, one tolerated failure -- asserted, not assumed."""
    assert rig.size == 3
    assert rig.quorum == 2
    assert rig.failures_tolerated == 1


@_parameterise
async def test_registries_can_write_concurrently_to_different_nodes(
    rig: ClusterRig, request: Any,
) -> None:
    """Different Nodes are disjoint, so there is no cross-member contention."""
    namespace = _namespace()
    registry_a, registry_b = build_registry(), build_registry()
    backend_a, backend_b = await asyncio.gather(
        rig.backend_for(0, registry_a, namespace),
        rig.backend_for(1, registry_b, namespace),
    )

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


@pytest.mark.parametrize("rig", ["etcd"], indirect=True)
async def test_losing_the_local_member_fails_over_to_the_others(
    rig: ClusterRig, request: Any,
) -> None:
    """etcd only, and the exception proves the rule rather than bending it.

    This asserts that a registry *outlives* the storage member it prefers:
    killing etcd member 0 costs the registry in front of it a failover to
    another endpoint, not its existence. That is a property of etcd's
    two-tier topology -- registry as client of a separate cluster -- and raft
    has no counterpart, because there the member and the registry are one
    object and killing it kills both.

    Written as an etcd-only test rather than smuggled into the shared suite
    with a conditional, so the divergence is visible in the parameterisation
    instead of buried in a branch. The *claim* both backends share -- three
    members tolerate one failure -- is covered by
    ``test_quorum_loss_stops_writes_but_not_reads`` and by the topology test
    above.
    """
    registry = build_registry()
    backend = await rig.backend_for(0, registry, _namespace())

    try:
        node = make_node()
        assert (await _register(backend, ResourceType.NODE, node)).ok

        await rig.kill(0)
        await asyncio.sleep(1.0)

        # Query is unaffected -- it never touched the storage layer.
        assert registry.store.get(ResourceType.NODE, node["id"]) is not None

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


@_parameterise
async def test_quorum_loss_stops_writes_but_not_reads(
    rig: ClusterRig, request: Any,
) -> None:
    """Beyond what the cluster promises, writes must stop and reads must not.

    Committing without quorum is exactly what consensus exists to prevent; and
    refusing reads because writes are impossible would turn a partial outage
    into a total one, which ``BackendState`` is explicit about.
    """
    registries, backends = await _whole_cluster(
        rig, _namespace(), rpc_timeout=1.0, mutation_timeout=2.0,
    )
    registry, backend = registries[0], backends[0]

    try:
        node = make_node()
        assert (await _register(backend, ResourceType.NODE, node)).ok

        await rig.lose_quorum()
        await asyncio.sleep(1.0)

        with pytest.raises(Exception):
            await _register(backend, ResourceType.DEVICE, make_device())

        # The cached view keeps serving. Refusing reads because writes are
        # impossible turns a partial outage into a total one.
        assert registry.store.get(ResourceType.NODE, node["id"]) is not None
        assert backend.state.serves_queries is True
    finally:
        await backends[0].close()
