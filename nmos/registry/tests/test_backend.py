# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for the backend boundary between the Registration API and storage."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from aiohttp import web

from nmos.registry import (
    BackendState,
    InterfaceSecurity,
    Registry,
    StandaloneRegistryBackend,
    create_registration_app,
)
from nmos.registry.backend import RegistryBackend
from nmos.registry.decode import decode_resource
from nmos.registry.store import RegistryStore
from nmos.registry.subscriptions import SubscriptionManager
from nmos.registry.tests._fixtures import make_node
from nmos.registry.types import Body, RegistrationResult, ResourceType


def build_registry() -> Registry:
    registry = Registry(RegistryStore(), query_id=str(uuid.uuid4()))
    registry.attach_subscriptions(SubscriptionManager(registry))
    return registry


class FrozenBackend(StandaloneRegistryBackend):
    """A backend stuck in one state, for exercising the unavailable path."""

    def __init__(self, registry: Registry, state: BackendState) -> None:
        super().__init__(registry)
        self._state = state


# ---------------------------------------------------------------------------
# BackendState semantics
# ---------------------------------------------------------------------------

def test_only_ready_accepts_mutations() -> None:
    accepting = [s for s in BackendState if s.accepts_mutations]
    assert accepting == [BackendState.READY]


def test_every_state_but_starting_still_serves_queries() -> None:
    """Refusing reads because writes are impossible turns a partial outage
    into a total one. Only STARTING has no view worth serving."""
    for state in BackendState:
        assert state.serves_queries is (state is not BackendState.STARTING)


# ---------------------------------------------------------------------------
# StandaloneRegistryBackend
# ---------------------------------------------------------------------------

def test_standalone_backend_is_ready_without_start() -> None:
    """It has nothing to load, so reporting STARTING would 503 a working
    registry for anyone who forgot to call start()."""
    assert StandaloneRegistryBackend(build_registry()).state is BackendState.READY


def test_standalone_backend_satisfies_the_protocol() -> None:
    assert isinstance(StandaloneRegistryBackend(build_registry()), RegistryBackend)


async def test_standalone_backend_delegates_to_the_registry() -> None:
    registry = build_registry()
    backend = StandaloneRegistryBackend(registry)
    raw = make_node()
    typed = decode_resource(ResourceType.NODE, raw)

    result = await backend.register(ResourceType.NODE, Body.from_data(raw))
    assert isinstance(result, RegistrationResult)
    assert result.ok and result.created
    assert registry.store.count_extant(ResourceType.NODE) == 1

    assert await backend.heartbeat(raw["id"]) is not None
    assert await backend.unregister(ResourceType.NODE, raw["id"]) is True
    assert registry.store.count_extant(ResourceType.NODE) == 0


async def test_standalone_backend_heartbeat_of_unknown_node_is_none() -> None:
    backend = StandaloneRegistryBackend(build_registry())
    assert await backend.heartbeat(str(uuid.uuid4())) is None


# ---------------------------------------------------------------------------
# The app defaults, so existing callers keep working
# ---------------------------------------------------------------------------

def test_app_defaults_to_a_standalone_backend() -> None:
    """Every pre-existing caller passes only (registry, security)."""
    app = create_registration_app(build_registry(), InterfaceSecurity())
    assert isinstance(app["backend"], StandaloneRegistryBackend)
    assert app["backend"].registry is app["registry"]


def test_an_explicit_backend_is_used(aiohttp_client: Any) -> None:
    registry = build_registry()
    backend = StandaloneRegistryBackend(registry)
    app = create_registration_app(registry, InterfaceSecurity(), backend)
    assert app["backend"] is backend


# ---------------------------------------------------------------------------
# The 503 path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "state",
    [BackendState.STARTING, BackendState.DEGRADED, BackendState.RESYNCING],
)
async def test_mutations_answer_503_when_not_ready(
    aiohttp_client: Any, state: BackendState,
) -> None:
    registry = build_registry()
    app = create_registration_app(
        registry, InterfaceSecurity(), FrozenBackend(registry, state),
    )
    client = await aiohttp_client(app)
    raw = make_node()

    post = await client.post(
        "/x-nmos/registration/v1.3/resource",
        json={"type": "node", "data": raw},
    )
    assert post.status == 503
    # Seconds, not minutes: the conditions that produce it resolve quickly, and
    # a Node backing off for minutes stays unregistered long after recovery.
    assert post.headers["Retry-After"] == "1"

    heartbeat = await client.post(
        f"/x-nmos/registration/v1.3/health/nodes/{raw['id']}",
    )
    assert heartbeat.status == 503

    delete = await client.delete(
        f"/x-nmos/registration/v1.3/resource/nodes/{raw['id']}",
    )
    assert delete.status == 503


async def test_ready_backend_registers_normally(aiohttp_client: Any) -> None:
    registry = build_registry()
    app = create_registration_app(registry, InterfaceSecurity())
    client = await aiohttp_client(app)
    raw = make_node()

    post = await client.post(
        "/x-nmos/registration/v1.3/resource",
        json={"type": "node", "data": raw},
    )
    assert post.status == 201
    assert registry.store.count_extant(ResourceType.NODE) == 1


async def test_schema_errors_are_still_400_not_503(
    aiohttp_client: Any,
) -> None:
    """The body is validated before the backend is consulted, so a malformed
    registration is a client error even while storage is unavailable."""
    registry = build_registry()
    app = create_registration_app(
        registry, InterfaceSecurity(),
        FrozenBackend(registry, BackendState.DEGRADED),
    )
    client = await aiohttp_client(app)

    response = await client.post(
        "/x-nmos/registration/v1.3/resource",
        json={"type": "node", "data": {}},
    )
    assert response.status == 400


# ---------------------------------------------------------------------------
# Swappable store
# ---------------------------------------------------------------------------

def test_swap_store_replaces_the_view_atomically() -> None:
    """The compaction-recovery move: a replacement snapshot is built off to the
    side and installed in one assignment, so no reader sees a half-loaded store.
    """
    registry = build_registry()
    original = registry.store
    replacement = RegistryStore()

    returned = registry.swap_store(replacement)

    assert returned is original
    assert registry.store is replacement


def test_store_is_read_only_from_outside() -> None:
    registry = build_registry()
    with pytest.raises(AttributeError):
        registry.store = RegistryStore()  # type: ignore[misc]
