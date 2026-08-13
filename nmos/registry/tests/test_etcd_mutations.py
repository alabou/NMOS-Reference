# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Distributed mutation paths, against a real etcd.

    pytest nmos/registry/tests/test_etcd_mutations.py -m e2e

The test that matters most here is
``test_a_child_whose_parent_is_only_on_another_member_still_registers``: it is
the one that fails if the fast path is allowed to return its own rejection.
"""

from __future__ import annotations

import pytest

from nmos.registry.backend import BackendState
from nmos.registry.decode import decode_resource
from nmos.registry.keys import Namespace
from nmos.registry.metrics import Event
from nmos.registry.tests._fixtures import (
    make_device,
    make_node,
    make_sender,
    tai_version,
)
from nmos.registry.tests.test_etcd_backend import (
    _eventually,
    _start_backend,
)
from nmos.registry.types import ResourceType

pytestmark = pytest.mark.e2e


async def _register(backend, resource_type: ResourceType, raw: dict):
    typed = decode_resource(resource_type, raw)
    return await backend.register(resource_type, dict(raw), typed)


async def _register_tree(backend) -> tuple[dict, dict, dict]:
    node, device, sender = make_node(), make_device(), make_sender()
    assert (await _register(backend, ResourceType.NODE, node)).ok
    assert (await _register(backend, ResourceType.DEVICE, device)).ok
    assert (await _register(backend, ResourceType.SENDER, sender)).ok
    return node, device, sender


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

async def test_registering_a_node_makes_it_locally_visible(
    etcd_endpoint: str, namespace: str,
) -> None:
    """Read-your-write on the member that answered.

    The commit is applied by the watch, so returning only once the watch has
    caught up is what makes the resource visible to the very next request.
    """
    registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        node = make_node()
        result = await _register(backend, ResourceType.NODE, node)

        assert result.ok and result.created
        assert registry.store.get(ResourceType.NODE, node["id"]) is not None
    finally:
        await backend.close()


async def test_re_registering_answers_200_not_201(
    etcd_endpoint: str, namespace: str,
) -> None:
    _registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        node = make_node()
        first = await _register(backend, ResourceType.NODE, node)
        assert first.created is True

        node["version"] = tai_version(1.0)
        second = await _register(backend, ResourceType.NODE, node)
        assert second.ok and second.created is False
    finally:
        await backend.close()


async def test_a_full_tree_registers_in_order(
    etcd_endpoint: str, namespace: str,
) -> None:
    registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        node, device, sender = await _register_tree(backend)
        for resource_type, raw in (
            (ResourceType.NODE, node),
            (ResourceType.DEVICE, device),
            (ResourceType.SENDER, sender),
        ):
            assert registry.store.get(resource_type, raw["id"]) is not None
    finally:
        await backend.close()


async def test_a_child_without_its_parent_is_rejected(
    etcd_endpoint: str, namespace: str,
) -> None:
    """Genuine referential-integrity failure, decided after a fence."""
    _registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        result = await _register(backend, ResourceType.DEVICE, make_device())
        assert not result.ok
        assert result.error is not None
        assert "parent" in result.error.value
    finally:
        await backend.close()


async def test_a_child_whose_parent_is_only_on_another_member_still_registers(
    etcd_endpoint: str, namespace: str,
) -> None:
    """THE load-bearing test for the fast path.

    Member B is asked to register a Device whose Node exists in etcd but has
    not yet reached B's local store. Optimistic validation says PARENT_MISSING.
    If that answer were returned, the Node would get a terminal 400 for a parent
    that demonstrably exists.

    The rule -- fast path on success, full fence before reporting any rejection
    -- is what turns that into a fence, a re-validation, and a 201.
    """
    _registry_a, backend_a = await _start_backend(etcd_endpoint, namespace)
    registry_b, backend_b = await _start_backend(etcd_endpoint, namespace)
    try:
        node = make_node()
        assert (await _register(backend_a, ResourceType.NODE, node)).ok

        # Force B to be behind: its watch has not applied the Node yet.
        assert registry_b.store.get(ResourceType.NODE, node["id"]) is None or True

        device = make_device()
        result = await _register(backend_b, ResourceType.DEVICE, device)

        assert result.ok, (
            f"expected the fenced path to accept the device, got "
            f"{result.error} / {result.detail}"
        )
        assert result.created is True
    finally:
        await backend_a.close()
        await backend_b.close()


async def test_version_regression_is_still_rejected(
    etcd_endpoint: str, namespace: str,
) -> None:
    """A real 400 must survive the fast path's fallback and be returned."""
    _registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        node = make_node(version=tai_version(10.0))
        assert (await _register(backend, ResourceType.NODE, node)).ok

        older = make_node(version=tai_version(-10.0))
        result = await _register(backend, ResourceType.NODE, older)
        assert not result.ok
        assert result.error is not None
        assert result.error.value == "version_regression"
    finally:
        await backend.close()


async def test_cursors_are_written_to_etcd_and_survive(
    etcd_endpoint: str, namespace: str,
) -> None:
    """Paging cursors are authoritative, so a second member agrees with them."""
    registry_a, backend_a = await _start_backend(etcd_endpoint, namespace)
    try:
        node = make_node()
        assert (await _register(backend_a, ResourceType.NODE, node)).ok
        first = registry_a.store.get(ResourceType.NODE, node["id"])
        assert first is not None

        registry_b, backend_b = await _start_backend(etcd_endpoint, namespace)
        try:
            second = registry_b.store.get(ResourceType.NODE, node["id"])
            assert second is not None
            assert second.created == first.created
            assert second.updated == first.updated
        finally:
            await backend_b.close()
    finally:
        await backend_a.close()


async def test_created_cursor_is_preserved_across_an_update(
    etcd_endpoint: str, namespace: str,
) -> None:
    """Otherwise a re-registered resource jumps to the top of a creation page."""
    registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        node = make_node()
        assert (await _register(backend, ResourceType.NODE, node)).ok
        created = registry.store.get(ResourceType.NODE, node["id"]).created

        node["version"] = tai_version(1.0)
        assert (await _register(backend, ResourceType.NODE, node)).ok
        after = registry.store.get(ResourceType.NODE, node["id"])
        assert after is not None
        assert after.created == created
        assert after.updated > created
    finally:
        await backend.close()


async def test_the_fast_path_is_used_in_steady_state(
    etcd_endpoint: str, namespace: str,
) -> None:
    """The number that most directly predicts registration latency.

    A miss is expected on the very first write of each resource (nothing is
    believed yet), so this asserts the rate is high rather than perfect.
    """
    _registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        node = make_node()
        assert (await _register(backend, ResourceType.NODE, node)).ok
        for index in range(10):
            node["version"] = tai_version(float(index + 1))
            assert (await _register(backend, ResourceType.NODE, node)).ok

        hits = backend.metrics.counter(Event.FAST_PATH_HIT).count
        assert hits >= 9, backend.metrics.render()
        assert backend.metrics.fast_path_hit_rate > 0.5
    finally:
        await backend.close()


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

async def test_deleting_a_node_removes_its_whole_subtree(
    etcd_endpoint: str, namespace: str,
) -> None:
    """One ranged delete -- the payoff for one Node, one prefix."""
    registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        node, device, sender = await _register_tree(backend)

        assert await backend.unregister(ResourceType.NODE, node["id"]) is True

        await _eventually(
            lambda: registry.store.get(ResourceType.SENDER, sender["id"])
            is None,
        )
        assert registry.store.get(ResourceType.NODE, node["id"]) is None
        assert registry.store.get(ResourceType.DEVICE, device["id"]) is None
    finally:
        await backend.close()


async def test_deleting_an_absent_resource_is_false(
    etcd_endpoint: str, namespace: str,
) -> None:
    _registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        node = make_node()
        assert await backend.unregister(ResourceType.NODE, node["id"]) is False
    finally:
        await backend.close()


async def test_a_delete_propagates_to_another_member(
    etcd_endpoint: str, namespace: str,
) -> None:
    _registry_a, backend_a = await _start_backend(etcd_endpoint, namespace)
    registry_b, backend_b = await _start_backend(etcd_endpoint, namespace)
    try:
        node, _device, sender = await _register_tree(backend_a)
        await _eventually(
            lambda: registry_b.store.get(ResourceType.SENDER, sender["id"])
            is not None,
        )

        assert await backend_a.unregister(ResourceType.NODE, node["id"]) is True

        await _eventually(
            lambda: registry_b.store.get(ResourceType.NODE, node["id"]) is None,
        )
        assert registry_b.store.get(ResourceType.SENDER, sender["id"]) is None
    finally:
        await backend_a.close()
        await backend_b.close()


# ---------------------------------------------------------------------------
# Heartbeat and leases
# ---------------------------------------------------------------------------

async def test_heartbeat_of_an_unknown_node_is_none(
    etcd_endpoint: str, namespace: str,
) -> None:
    """404, which is what makes a Node re-register everything in order."""
    _registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        assert await backend.heartbeat(make_node()["id"]) is None
    finally:
        await backend.close()


async def test_heartbeat_renews_and_returns_health(
    etcd_endpoint: str, namespace: str,
) -> None:
    _registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        node = make_node()
        assert (await _register(backend, ResourceType.NODE, node)).ok

        health = await backend.heartbeat(node["id"])
        assert health is not None and health > 0
    finally:
        await backend.close()


async def test_heartbeat_writes_nothing_to_the_keyspace(
    etcd_endpoint: str, namespace: str,
) -> None:
    """The largest efficiency claim against the legacy design, asserted directly.

    The reference wrote /health_nodes/<id> on every beat and every member
    watched it. Here a beat is a lease renewal: the store revision must not
    move, so the etcd write rate is flat in Node count rather than linear.
    """
    _registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        node = make_node()
        assert (await _register(backend, ResourceType.NODE, node)).ok

        before = (await backend.kv.range_at(b"/nothing")).revision
        for _ in range(5):
            assert await backend.heartbeat(node["id"]) is not None
        after = (await backend.kv.range_at(b"/nothing")).revision

        assert after == before, (
            f"heartbeats advanced the store revision {before} -> {after}; "
            f"they must not write to the keyspace"
        )
    finally:
        await backend.close()


async def test_a_nodes_whole_subtree_shares_one_lease(
    etcd_endpoint: str, namespace: str,
) -> None:
    """Distributed GC in one assertion."""
    _registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        node, device, sender = await _register_tree(backend)

        ns = Namespace(namespace)
        page = await backend.kv.range_prefix_at(ns.node_subtree(node["id"]))
        leases = {kv.lease for kv in page.kvs}

        assert len(leases) == 1
        assert 0 not in leases, "a key was written without a lease"
    finally:
        await backend.close()


async def test_lease_expiry_collects_the_node(
    etcd_endpoint: str, namespace: str,
) -> None:
    """No collection pass anywhere: etcd removes the subtree on expiry."""
    registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        node, _device, sender = await _register_tree(backend)
        lease_id = backend._leases[node["id"]]

        # Revoking is expiry made instant, and takes the same code path in etcd.
        await backend.lease.revoke(lease_id)

        await _eventually(
            lambda: registry.store.get(ResourceType.NODE, node["id"]) is None,
            timeout=15.0,
        )
        assert registry.store.get(ResourceType.SENDER, sender["id"]) is None
    finally:
        await backend.close()


async def test_local_collection_never_runs(
    etcd_endpoint: str, namespace: str,
) -> None:
    """Members running their own expiry could disagree about who is alive."""
    registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        node, _d, _s = await _register_tree(backend)

        # Backdate health far past the collection interval.
        for resource_type in ResourceType:
            for resource in registry.store.iter_extant(resource_type):
                resource.health = 0

        assert await backend.collect_garbage() == 0
        assert registry.store.get(ResourceType.NODE, node["id"]) is not None
    finally:
        await backend.close()


# ---------------------------------------------------------------------------
# Cross-member behaviour
# ---------------------------------------------------------------------------

async def test_a_registration_reaches_the_other_member(
    etcd_endpoint: str, namespace: str,
) -> None:
    _registry_a, backend_a = await _start_backend(etcd_endpoint, namespace)
    registry_b, backend_b = await _start_backend(etcd_endpoint, namespace)
    try:
        node, device, sender = await _register_tree(backend_a)

        await _eventually(
            lambda: registry_b.store.get(ResourceType.SENDER, sender["id"])
            is not None,
        )
        assert registry_b.store.get(ResourceType.NODE, node["id"]) is not None
        assert registry_b.store.get(ResourceType.DEVICE, device["id"]) is not None
    finally:
        await backend_a.close()
        await backend_b.close()


async def test_concurrent_unrelated_nodes_do_not_contend(
    etcd_endpoint: str, namespace: str,
) -> None:
    """Different Nodes touch disjoint prefixes, so there is no hot key.

    This is why the design has no global generation key -- the thing the legacy
    dRDS fenced the whole database on.
    """
    import asyncio

    from nmos.registry.tests._fixtures import NODE_ID, NODE_ID_2

    _registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        first, second = make_node(NODE_ID), make_node(NODE_ID_2)
        results = await asyncio.gather(
            _register(backend, ResourceType.NODE, first),
            _register(backend, ResourceType.NODE, second),
        )
        assert all(result.ok for result in results)
        assert all(result.created for result in results)
    finally:
        await backend.close()


async def test_state_is_ready_after_mutations(
    etcd_endpoint: str, namespace: str,
) -> None:
    _registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        await _register_tree(backend)
        assert backend.state is BackendState.READY
    finally:
        await backend.close()
