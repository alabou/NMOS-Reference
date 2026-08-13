# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for the distributed backend, against a real etcd.

Marked ``e2e``: they need an etcd binary, which is an optional install.

    pytest nmos/registry/tests/test_etcd_backend.py -m e2e

These use a *single-member* etcd, because what is under test here is the
preload/watch/fence machinery rather than Raft. Two backends attached to the
same etcd model two registries in a cluster exactly as far as this layer is
concerned: they see the same keyspace and each maintains its own local view
from its own watch.
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
from nmos.registry.etcd_backend import EtcdRegistryBackend
from nmos.registry.keys import ENVELOPE_VERSION, Envelope, Namespace
from nmos.registry.registry import Registry
from nmos.registry.store import RegistryStore
from nmos.registry.subscriptions import SubscriptionManager
from nmos.registry.tests._fixtures import (
    NODE_ID_2,
    make_device,
    make_node,
    make_sender,
)
from nmos.registry.types import ResourceType, TaiCursor

pytestmark = pytest.mark.e2e

NAMESPACE = "/nmos-test/registry/v1"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _config(endpoint: str, namespace: str):
    """A DistributedConfig aimed at one plain-HTTP etcd."""
    from nmos.etcd.cluster import MemberSpec, derive_cluster
    from nmos.registry.distributed import DistributedConfig

    host, _, port = endpoint.rpartition(":")
    host = host.replace("http://", "").replace("https://", "")
    layout = derive_cluster(
        [MemberSpec(host=host, client_port=int(port), peer_port=int(port) + 1)],
        local_host=host,
        namespace=namespace,
        tls=False,
    )
    return DistributedConfig(
        layout=layout,
        endpoints=(f"{host}:{port}",),
        namespace=namespace,
        external=True,
        binary="",
        data_dir=Path(),
        bootstrap=False,
        tls=False,
        certificate="",
        key="",
        trusted_root_ca=(),
        certificate_name="",
        client_crl_file="",
        peer_crl_file="",
        rpc_timeout=5.0,
        mutation_timeout=10.0,
    )


def build_registry() -> Registry:
    registry = Registry(RegistryStore(), query_id=str(uuid.uuid4()))
    registry.attach_subscriptions(SubscriptionManager(registry))
    return registry


@pytest.fixture
async def writer(etcd_endpoint: str, namespace: str) -> AsyncIterator[Any]:
    """Direct etcd access, standing in for "another registry wrote this"."""
    from nmos.etcd.channel import EtcdChannelPool, parse_endpoints
    from nmos.etcd.kv import EtcdKV

    pool = EtcdChannelPool(
        parse_endpoints([etcd_endpoint]),
        credentials=None, target_name=None, rpc_timeout=5.0,
    )
    try:
        yield EtcdKV(pool)
    finally:
        await pool.close()


async def _start_backend(
    etcd_endpoint: str, namespace: str,
) -> tuple[Registry, EtcdRegistryBackend]:
    registry = build_registry()
    backend = EtcdRegistryBackend(registry, _config(etcd_endpoint, namespace))
    await backend.start()
    return registry, backend


def _envelope(resource_type: ResourceType, raw: dict) -> bytes:
    return Envelope(
        version=ENVELOPE_VERSION,
        resource_type=resource_type,
        raw=raw,
        created=TaiCursor(1000, 1),
        updated=TaiCursor(1000, 1),
        health=1000,
    ).encode()


async def _seed_tree(kv, ns: Namespace) -> tuple[dict, dict, dict]:
    """Write a Node -> Device -> Sender tree straight into etcd."""
    from nmos.etcd.kv import put_op

    node = make_node()
    device = make_device()
    sender = make_sender()

    await kv.txn(success=[
        put_op(ns.node(node["id"]), _envelope(ResourceType.NODE, node)),
        put_op(
            ns.device(node["id"], device["id"]),
            _envelope(ResourceType.DEVICE, device),
        ),
        put_op(
            ns.child(ResourceType.SENDER, node["id"], device["id"], sender["id"]),
            _envelope(ResourceType.SENDER, sender),
        ),
    ])
    return node, device, sender


async def _eventually(predicate, timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached within timeout")


# ---------------------------------------------------------------------------
# Preload
# ---------------------------------------------------------------------------

async def test_preload_of_an_empty_namespace_reaches_ready(
    etcd_endpoint: str, namespace: str,
) -> None:
    registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        assert backend.state is BackendState.READY
        assert registry.store.count_extant(ResourceType.NODE) == 0
        # Seeded to the preload revision, never zero -- otherwise the recovery
        # fence blocks for its whole deadline on a quiet cluster.
        assert backend.fence.applied > 0
    finally:
        await backend.close()


async def test_preload_loads_an_existing_tree(
    etcd_endpoint: str, namespace: str, writer: Any,
) -> None:
    ns = Namespace(namespace)
    node, device, sender = await _seed_tree(writer, ns)

    registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        assert registry.store.get(ResourceType.NODE, node["id"]) is not None
        assert registry.store.get(ResourceType.DEVICE, device["id"]) is not None
        assert registry.store.get(ResourceType.SENDER, sender["id"]) is not None
    finally:
        await backend.close()


async def test_preload_uses_the_authoritative_cursors(
    etcd_endpoint: str, namespace: str, writer: Any,
) -> None:
    """Cursors come from the envelope, not from the local clock.

    If each member allocated its own, the same resource would page differently
    on different members.
    """
    ns = Namespace(namespace)
    node, _device, _sender = await _seed_tree(writer, ns)

    registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        stored = registry.store.get(ResourceType.NODE, node["id"])
        assert stored is not None
        assert stored.created == TaiCursor(1000, 1)
        assert stored.updated == TaiCursor(1000, 1)
    finally:
        await backend.close()


async def test_preload_applies_parents_before_children(
    etcd_endpoint: str, namespace: str, writer: Any,
) -> None:
    """A Sender sorted before its Device by key order must still load.

    Referential integrity is enforced by the store, so ordering by tree depth
    rather than by key is what makes the snapshot loadable at all.
    """
    ns = Namespace(namespace)
    node, device, sender = await _seed_tree(writer, ns)

    registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        stored = registry.store.get(ResourceType.SENDER, sender["id"])
        assert stored is not None
        assert stored.parent_id == device["id"]
    finally:
        await backend.close()


async def test_corrupt_envelope_aborts_the_preload(
    etcd_endpoint: str, namespace: str, writer: Any,
) -> None:
    """A member that cannot read a resource must not serve a partial view."""
    from nmos.etcd.kv import put_op
    from nmos.registry.keys import KeyError_

    ns = Namespace(namespace)
    node = make_node()
    await writer.txn(success=[put_op(ns.node(node["id"]), b"{not json")])

    registry = build_registry()
    backend = EtcdRegistryBackend(registry, _config(etcd_endpoint, namespace))
    try:
        with pytest.raises(KeyError_):
            await backend.start()
        # The previous (empty) view is still installed, not a partial one.
        assert registry.store.count_extant(ResourceType.NODE) == 0
    finally:
        await backend.close()


async def test_key_and_body_disagreement_aborts_the_preload(
    etcd_endpoint: str, namespace: str, writer: Any,
) -> None:
    """Key and value are written in one transaction; disagreement is corruption."""
    from nmos.etcd.kv import put_op
    from nmos.registry.keys import KeyError_

    ns = Namespace(namespace)
    node = make_node()
    other = make_node(NODE_ID_2)
    assert node["id"] != other["id"]
    # The key names one Node, the value carries another. They are written in a
    # single transaction, so this can only be corruption or a bug -- never a
    # race -- and serving either half of the contradiction would put this
    # member permanently out of step with its peers.
    await writer.txn(success=[
        put_op(ns.node(node["id"]), _envelope(ResourceType.NODE, other)),
    ])

    registry = build_registry()
    backend = EtcdRegistryBackend(registry, _config(etcd_endpoint, namespace))
    try:
        with pytest.raises(KeyError_, match="id"):
            await backend.start()
    finally:
        await backend.close()


# ---------------------------------------------------------------------------
# Watch
# ---------------------------------------------------------------------------

async def test_a_remote_write_appears_through_the_watch(
    etcd_endpoint: str, namespace: str, writer: Any,
) -> None:
    """The core of distribution: another member's write becomes visible here."""
    ns = Namespace(namespace)
    registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        node, device, sender = await _seed_tree(writer, ns)

        await _eventually(
            lambda: registry.store.get(ResourceType.SENDER, sender["id"])
            is not None,
        )
        assert registry.store.get(ResourceType.NODE, node["id"]) is not None
        assert registry.store.get(ResourceType.DEVICE, device["id"]) is not None
    finally:
        await backend.close()


async def test_a_remote_delete_appears_through_the_watch(
    etcd_endpoint: str, namespace: str, writer: Any,
) -> None:
    ns = Namespace(namespace)
    registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        node, _device, sender = await _seed_tree(writer, ns)
        await _eventually(
            lambda: registry.store.get(ResourceType.SENDER, sender["id"])
            is not None,
        )

        # Delete the whole Node subtree, as a Node unregistration would.
        await writer.delete_prefix(ns.node_subtree(node["id"]))

        await _eventually(
            lambda: registry.store.get(ResourceType.NODE, node["id"]) is None,
        )
        assert registry.store.get(ResourceType.SENDER, sender["id"]) is None
    finally:
        await backend.close()


async def test_the_fence_advances_with_applied_revisions(
    etcd_endpoint: str, namespace: str, writer: Any,
) -> None:
    ns = Namespace(namespace)
    registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        before = backend.fence.applied
        node, _d, _s = await _seed_tree(writer, ns)

        await _eventually(
            lambda: registry.store.get(ResourceType.NODE, node["id"])
            is not None,
        )
        await _eventually(lambda: backend.fence.applied > before)
    finally:
        await backend.close()


async def test_a_watched_change_queues_exactly_one_grain(
    etcd_endpoint: str, namespace: str, writer: Any,
) -> None:
    """The watch is the only publisher, so a change produces one event here.

    This is what the legacy design needed an origin-index byte for -- and that byte
    could not work for deletes, whose events carry no value to read it from.
    """
    ns = Namespace(namespace)
    registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        subscription, _created = registry.subscriptions.create_or_match(
            resource_path="/nodes",
            params={},
            max_update_rate_ms=0,
            persist=True,
            secure=False,
            authorization=False,
            host="localhost",
            ws_scheme="ws",
            ws_host="localhost:8448",
        )
        connection = registry.subscriptions.connect(subscription)
        connection.drain()  # discard the sync burst

        node = make_node()
        from nmos.etcd.kv import put_op
        await writer.txn(success=[
            put_op(ns.node(node["id"]), _envelope(ResourceType.NODE, node)),
        ])

        await _eventually(
            lambda: registry.store.get(ResourceType.NODE, node["id"])
            is not None,
        )
        pending = connection.drain()
        assert len(pending) == 1
        assert pending[0].post is not None
        assert pending[0].pre is None
    finally:
        await backend.close()


async def test_two_backends_converge_on_the_same_view(
    etcd_endpoint: str, namespace: str, writer: Any,
) -> None:
    """Two registries on one cluster must end up agreeing."""
    ns = Namespace(namespace)
    registry_a, backend_a = await _start_backend(etcd_endpoint, namespace)
    registry_b, backend_b = await _start_backend(etcd_endpoint, namespace)
    try:
        node, device, sender = await _seed_tree(writer, ns)

        for registry in (registry_a, registry_b):
            await _eventually(
                lambda r=registry: r.store.get(  # type: ignore[misc]
                    ResourceType.SENDER, sender["id"],
                ) is not None,
            )

        for resource_type, resource_id in (
            (ResourceType.NODE, node["id"]),
            (ResourceType.DEVICE, device["id"]),
            (ResourceType.SENDER, sender["id"]),
        ):
            first = registry_a.store.get(resource_type, resource_id)
            second = registry_b.store.get(resource_type, resource_id)
            assert first is not None and second is not None
            # Same content AND same cursors -- the latter is what makes paged
            # Query answers identical across members.
            assert first.raw == second.raw
            assert first.created == second.created
            assert first.updated == second.updated
    finally:
        await backend_a.close()
        await backend_b.close()


# ---------------------------------------------------------------------------
# Mutations are not wired yet
# ---------------------------------------------------------------------------

async def test_local_garbage_collection_is_disabled(
    etcd_endpoint: str, namespace: str,
) -> None:
    """Liveness is a lease. Members running their own expiry could disagree."""
    _registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        assert await backend.collect_garbage() == 0
    finally:
        await backend.close()
