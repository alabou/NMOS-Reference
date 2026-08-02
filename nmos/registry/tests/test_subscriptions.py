# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Subscription and WebSocket grain tests.

These drive a real aiohttp WebSocket against the real listener, so the grain
wire format, the event classification, the synchronisation burst and the rate
limiter are all exercised end to end rather than through the manager's
internals.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from nmos.registry import (
    InterfaceSecurity,
    Registry,
    create_query_app,
    create_query_ws_app,
)
from nmos.registry.decode import decode_resource
from nmos.registry.handlers_query import BASE_PATH
from nmos.registry.store import RegistryStore
from nmos.registry.subscriptions import SubscriptionManager
from nmos.registry.tests._fixtures import (
    DEVICE_ID,
    NODE_ID,
    NODE_ID_2,
    SENDER_ID,
    make_device,
    make_flow,
    make_node,
    make_sender,
    make_source,
    tai_version,
)
from nmos.registry.types import ResourceType

SUBSCRIPTIONS = f"{BASE_PATH}/subscriptions"

# Grains are pushed asynchronously, so a reader must be allowed to wait. Kept
# short so a genuine failure surfaces quickly rather than stalling the suite.
GRAIN_TIMEOUT_S = 2.0


@pytest.fixture
def registry() -> Registry:
    registry = Registry(
        RegistryStore(), query_id="8c4d1e70-6b3a-4f52-9d81-2e7c5a0b3f14",
    )
    registry.attach_subscriptions(SubscriptionManager(registry))
    return registry


@pytest.fixture
async def clients(aiohttp_client, registry: Registry):  # type: ignore[no-untyped-def]
    """An HTTP client and a WebSocket client over one shared registry."""
    security = InterfaceSecurity()
    http = await aiohttp_client(
        create_query_app(registry, security, ws_port=8448),
    )
    websocket = await aiohttp_client(create_query_ws_app(registry, security))
    return http, websocket


def seed(registry: Registry, resource_type: ResourceType, raw: dict) -> None:
    typed = decode_resource(resource_type, raw)
    result = registry.register(resource_type, dict(raw), typed)
    assert result.ok, result.detail


def seed_parents(registry: Registry) -> None:
    """Node and Device, so Senders and Sources can be registered."""
    seed(registry, ResourceType.NODE, make_node())
    seed(registry, ResourceType.DEVICE, make_device())


async def subscribe(http: Any, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "max_update_rate_ms": 0,
        "resource_path": "/senders",
        "persist": False,
        "params": {},
    }
    body.update(overrides)
    response = await http.post(SUBSCRIPTIONS, json=body)
    assert response.status in (200, 201), await response.text()
    result: dict[str, Any] = await response.json()
    return result


def ws_path(subscription: dict[str, Any]) -> str:
    """Local path for a subscription's socket.

    ``ws_href`` advertises the production WebSocket port, which is not the
    ephemeral port the test server bound, so the path is used directly. The
    path itself is asserted to match the advertised one.
    """
    path = f"{BASE_PATH}/subscriptions/{subscription['id']}"
    assert subscription["ws_href"].endswith(path)
    return path


async def read_grain(socket: Any) -> dict[str, Any]:
    """Read one grain message, failing the test if none arrives."""
    message = await asyncio.wait_for(socket.receive(), timeout=GRAIN_TIMEOUT_S)
    assert message.type.name == "TEXT", message
    payload: dict[str, Any] = json.loads(message.data)
    return payload


def entries(grain: dict[str, Any]) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = grain["grain"]["data"]
    return data


async def expect_no_grain(socket: Any) -> None:
    """Assert nothing arrives within a short window."""
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(socket.receive(), timeout=0.3)


# ---------------------------------------------------------------------------
# Connection and synchronisation
# ---------------------------------------------------------------------------

class TestSync:
    async def test_sync_burst_on_connect(self, clients, registry) -> None:  # type: ignore[no-untyped-def]
        """``:166`` -- the initial burst carries identical pre and post.

        "This is used in initial synchronisation messages that ensure the
        client has received all data for a given topic."
        """
        http, ws_client = clients
        seed_parents(registry)
        seed(registry, ResourceType.SOURCE, make_source())
        seed(registry, ResourceType.FLOW, make_flow())
        seed(registry, ResourceType.SENDER, make_sender())

        subscription = await subscribe(http)
        async with ws_client.ws_connect(ws_path(subscription)) as socket:
            grain = await read_grain(socket)

            assert grain["grain_type"] == "event"
            assert grain["grain"]["topic"] == "/senders/"
            assert grain["flow_id"] == subscription["id"]
            assert grain["source_id"] == registry.query_id

            entry = entries(grain)[0]
            assert entry["path"] == SENDER_ID
            assert entry["pre"] == entry["post"], "sync events must be identical"

    async def test_no_grain_when_nothing_matches(self, clients) -> None:  # type: ignore[no-untyped-def]
        """An empty registry must produce NO sync grain at all.

        ``queryapi-subscriptions-websocket.json`` gives the ``data`` array
        ``minItems: 1``, so a grain with an empty array is schema-invalid. The
        AMWA mock sends one anyway.
        """
        http, ws_client = clients
        subscription = await subscribe(http)
        async with ws_client.ws_connect(ws_path(subscription)) as socket:
            await expect_no_grain(socket)

    async def test_sync_is_not_broadcast_to_existing_clients(
        self, clients, registry,
    ) -> None:  # type: ignore[no-untyped-def]
        """A second client connecting must not re-sync the first.

        The AMWA mock queues the sync grain to every connected client whenever
        any client connects.
        """
        http, ws_client = clients
        seed_parents(registry)
        seed(registry, ResourceType.SOURCE, make_source())
        seed(registry, ResourceType.FLOW, make_flow())
        seed(registry, ResourceType.SENDER, make_sender())

        subscription = await subscribe(http, persist=True)
        async with ws_client.ws_connect(ws_path(subscription)) as first:
            await read_grain(first)  # first client's own sync
            async with ws_client.ws_connect(ws_path(subscription)) as second:
                await read_grain(second)  # second client's own sync
                await expect_no_grain(first)

    async def test_unknown_subscription_is_404(self, clients) -> None:  # type: ignore[no-untyped-def]
        """A mistyped ws_href gets an HTTP error, not an instantly-dead socket."""
        _http, ws_client = clients
        response = await ws_client.get(f"{BASE_PATH}/subscriptions/{NODE_ID}")
        assert response.status == 404


# ---------------------------------------------------------------------------
# Event classification
# ---------------------------------------------------------------------------

class TestEvents:
    async def test_added_event_has_post_only(self, clients, registry) -> None:  # type: ignore[no-untyped-def]
        """``:87`` -- "Event data containing only a post attribute signifies
        creation of a resource"."""
        http, ws_client = clients
        seed_parents(registry)
        seed(registry, ResourceType.SOURCE, make_source())
        seed(registry, ResourceType.FLOW, make_flow())

        subscription = await subscribe(http)
        async with ws_client.ws_connect(ws_path(subscription)) as socket:
            seed(registry, ResourceType.SENDER, make_sender())
            entry = entries(await read_grain(socket))[0]

            assert entry["path"] == SENDER_ID
            assert "pre" not in entry
            assert entry["post"]["id"] == SENDER_ID

    async def test_modified_event_has_pre_and_post(self, clients, registry) -> None:  # type: ignore[no-untyped-def]
        """``:135`` -- both present, and "All attributes of the resource MUST
        be specified (i.e. not just those that have changed)"."""
        http, ws_client = clients
        seed_parents(registry)
        seed(registry, ResourceType.SOURCE, make_source())
        seed(registry, ResourceType.FLOW, make_flow())
        seed(registry, ResourceType.SENDER, make_sender(label="before"))

        subscription = await subscribe(http)
        async with ws_client.ws_connect(ws_path(subscription)) as socket:
            await read_grain(socket)  # sync

            seed(
                registry, ResourceType.SENDER,
                make_sender(label="after", version=tai_version(+1)),
            )
            entry = entries(await read_grain(socket))[0]

            assert entry["pre"]["label"] == "before"
            assert entry["post"]["label"] == "after"
            assert set(entry["pre"]) == set(entry["post"])
            assert "transport" in entry["post"]

    async def test_removed_event_has_pre_only(self, clients, registry) -> None:  # type: ignore[no-untyped-def]
        """``:111`` -- "Event data containing only a pre attribute signifies
        deletion of a resource"."""
        http, ws_client = clients
        seed_parents(registry)
        seed(registry, ResourceType.SOURCE, make_source())
        seed(registry, ResourceType.FLOW, make_flow())
        seed(registry, ResourceType.SENDER, make_sender())

        subscription = await subscribe(http)
        async with ws_client.ws_connect(ws_path(subscription)) as socket:
            await read_grain(socket)  # sync

            registry.unregister(ResourceType.SENDER, SENDER_ID)
            entry = entries(await read_grain(socket))[0]

            assert entry["path"] == SENDER_ID
            assert entry["pre"]["id"] == SENDER_ID
            assert "post" not in entry

    async def test_cascade_delete_emits_child_events(
        self, clients, registry,
    ) -> None:  # type: ignore[no-untyped-def]
        """Deleting a Node must tell a /senders subscriber its Sender is gone."""
        http, ws_client = clients
        seed_parents(registry)
        seed(registry, ResourceType.SOURCE, make_source())
        seed(registry, ResourceType.FLOW, make_flow())
        seed(registry, ResourceType.SENDER, make_sender())

        subscription = await subscribe(http)
        async with ws_client.ws_connect(ws_path(subscription)) as socket:
            await read_grain(socket)  # sync

            registry.unregister(ResourceType.NODE, NODE_ID)
            entry = entries(await read_grain(socket))[0]
            assert entry["path"] == SENDER_ID
            assert "post" not in entry

    async def test_events_for_other_types_are_not_delivered(
        self, clients, registry,
    ) -> None:  # type: ignore[no-untyped-def]
        """A /senders subscriber must not receive Node events."""
        http, ws_client = clients
        subscription = await subscribe(http)
        async with ws_client.ws_connect(ws_path(subscription)) as socket:
            seed(registry, ResourceType.NODE, make_node())
            await expect_no_grain(socket)


# ---------------------------------------------------------------------------
# Filtered subscriptions
# ---------------------------------------------------------------------------

class TestFilteredSubscriptions:
    async def test_filter_excludes_non_matching_resources(
        self, clients, registry,
    ) -> None:  # type: ignore[no-untyped-def]
        http, ws_client = clients
        subscription = await subscribe(
            http, resource_path="/nodes", params={"label": "wanted"},
        )
        async with ws_client.ws_connect(ws_path(subscription)) as socket:
            seed(registry, ResourceType.NODE, make_node(label="unwanted"))
            await expect_no_grain(socket)

            seed(registry, ResourceType.NODE, make_node(
                node_id=NODE_ID_2, label="wanted",
            ))
            entry = entries(await read_grain(socket))[0]
            assert entry["post"]["label"] == "wanted"

    async def test_resource_that_stops_matching_is_reported_removed(
        self, clients, registry,
    ) -> None:  # type: ignore[no-untyped-def]
        """``:244`` -- a resource that stops matching MUST be reported as if
        it had been deleted.

        The AMWA mock implements no filter transitions at all.
        """
        http, ws_client = clients
        seed(registry, ResourceType.NODE, make_node(label="wanted"))

        subscription = await subscribe(
            http, resource_path="/nodes", params={"label": "wanted"},
        )
        async with ws_client.ws_connect(ws_path(subscription)) as socket:
            await read_grain(socket)  # sync

            seed(registry, ResourceType.NODE, make_node(
                label="no-longer", version=tai_version(+1),
            ))
            entry = entries(await read_grain(socket))[0]

            assert entry["path"] == NODE_ID
            assert entry["pre"]["label"] == "wanted"
            assert "post" not in entry, "must look like a deletion"

    async def test_resource_that_starts_matching_is_reported_added(
        self, clients, registry,
    ) -> None:  # type: ignore[no-untyped-def]
        """``:245`` -- a resource that begins to match MUST be reported as if
        it had been freshly created."""
        http, ws_client = clients
        seed(registry, ResourceType.NODE, make_node(label="other"))

        subscription = await subscribe(
            http, resource_path="/nodes", params={"label": "wanted"},
        )
        async with ws_client.ws_connect(ws_path(subscription)) as socket:
            await expect_no_grain(socket)  # nothing matches yet

            seed(registry, ResourceType.NODE, make_node(
                label="wanted", version=tai_version(+1),
            ))
            entry = entries(await read_grain(socket))[0]

            assert entry["path"] == NODE_ID
            assert "pre" not in entry, "must look like a creation"
            assert entry["post"]["label"] == "wanted"

    async def test_matching_resource_stays_modified(
        self, clients, registry,
    ) -> None:  # type: ignore[no-untyped-def]
        """A change that keeps the resource matching is an ordinary modify."""
        http, ws_client = clients
        seed(registry, ResourceType.NODE, make_node(label="wanted"))

        subscription = await subscribe(
            http, resource_path="/nodes", params={"label": "wanted"},
        )
        async with ws_client.ws_connect(ws_path(subscription)) as socket:
            await read_grain(socket)  # sync

            seed(registry, ResourceType.NODE, make_node(
                label="wanted", description="changed", version=tai_version(+1),
            ))
            entry = entries(await read_grain(socket))[0]
            assert entry["pre"]["description"] != entry["post"]["description"]

    async def test_sync_burst_respects_the_filter(
        self, clients, registry,
    ) -> None:  # type: ignore[no-untyped-def]
        http, ws_client = clients
        seed(registry, ResourceType.NODE, make_node(label="wanted"))
        seed(registry, ResourceType.NODE, make_node(
            node_id=NODE_ID_2, label="other",
        ))

        subscription = await subscribe(
            http, resource_path="/nodes", params={"label": "wanted"},
        )
        async with ws_client.ws_connect(ws_path(subscription)) as socket:
            grain = await read_grain(socket)
            assert [e["path"] for e in entries(grain)] == [NODE_ID]


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class TestRateLimiting:
    async def test_updates_are_coalesced_within_the_window(
        self, clients, registry,
    ) -> None:  # type: ignore[no-untyped-def]
        """``max_update_rate_ms`` bounds grain frequency, and changes within a
        window collapse to the net change.

        The AMWA mock does not implement this: it writes a grain per event.
        """
        http, ws_client = clients
        seed(registry, ResourceType.NODE, make_node(label="v0"))

        subscription = await subscribe(
            http, resource_path="/nodes", max_update_rate_ms=300,
        )
        async with ws_client.ws_connect(ws_path(subscription)) as socket:
            await read_grain(socket)  # sync, then the window opens

            # Three updates in quick succession, inside one window.
            for index in range(1, 4):
                seed(registry, ResourceType.NODE, make_node(
                    label=f"v{index}", version=tai_version(index),
                ))

            grain = await read_grain(socket)
            data = entries(grain)
            assert len(data) == 1, "the three updates must coalesce into one"
            # pre is the state before the FIRST change, post after the LAST.
            assert data[0]["pre"]["label"] == "v0"
            assert data[0]["post"]["label"] == "v3"

    async def test_zero_rate_delivers_immediately(
        self, clients, registry,
    ) -> None:  # type: ignore[no-untyped-def]
        """``max_update_rate_ms: 0`` means no imposed delay."""
        http, ws_client = clients
        subscription = await subscribe(
            http, resource_path="/nodes", max_update_rate_ms=0,
        )
        async with ws_client.ws_connect(ws_path(subscription)) as socket:
            seed(registry, ResourceType.NODE, make_node())
            grain = await read_grain(socket)
            assert entries(grain)[0]["path"] == NODE_ID


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    async def test_non_persistent_is_reaped_on_disconnect(
        self, clients, registry,
    ) -> None:  # type: ignore[no-untyped-def]
        """``:18`` -- "The Query API MAY remove any Subscriptions with persist
        set to false that no longer have WebSocket connections"."""
        http, ws_client = clients
        subscription = await subscribe(http, persist=False)
        assert registry.subscriptions.get(subscription["id"]) is not None

        async with ws_client.ws_connect(ws_path(subscription)):
            pass
        await asyncio.sleep(0.1)

        assert registry.subscriptions.get(subscription["id"]) is None

    async def test_persistent_survives_disconnect(
        self, clients, registry,
    ) -> None:  # type: ignore[no-untyped-def]
        """``:19`` -- "If a persistent Subscription has been requested by the
        client, this MUST NOT be cleaned up automatically by the Query API,
        even if all clients have disconnected"."""
        http, ws_client = clients
        subscription = await subscribe(http, persist=True)

        async with ws_client.ws_connect(ws_path(subscription)):
            pass
        await asyncio.sleep(0.1)

        assert registry.subscriptions.get(subscription["id"]) is not None

    async def test_delete_closes_connected_clients(
        self, clients, registry,
    ) -> None:  # type: ignore[no-untyped-def]
        """``:19`` -- "If an HTTP DELETE is issued prior to all WebSocket
        connections being closed, they SHOULD be forcibly closed"."""
        http, ws_client = clients
        subscription = await subscribe(http, persist=True)

        async with ws_client.ws_connect(ws_path(subscription)) as socket:
            response = await http.delete(f"{SUBSCRIPTIONS}/{subscription['id']}")
            assert response.status == 204

            message = await asyncio.wait_for(
                socket.receive(), timeout=GRAIN_TIMEOUT_S,
            )
            assert message.type.name in ("CLOSE", "CLOSED", "CLOSING")

    async def test_grain_count_tracks_connections(
        self, clients, registry,
    ) -> None:  # type: ignore[no-untyped-def]
        """A grain is a resource per connected socket, as in nmos-cpp -- and
        it is what the status line's ``grains`` counter reports."""
        http, ws_client = clients
        subscription = await subscribe(http, persist=True)
        assert registry.subscriptions.grain_count() == 0

        async with ws_client.ws_connect(ws_path(subscription)):
            await asyncio.sleep(0.05)
            assert registry.subscriptions.grain_count() == 1
            assert registry.statistics().grains == 1

        await asyncio.sleep(0.1)
        assert registry.subscriptions.grain_count() == 0

    async def test_two_subscriptions_are_independent(
        self, clients, registry,
    ) -> None:  # type: ignore[no-untyped-def]
        http, ws_client = clients
        nodes = await subscribe(http, resource_path="/nodes", persist=True)
        devices = await subscribe(http, resource_path="/devices", persist=True)

        async with ws_client.ws_connect(ws_path(nodes)) as node_socket:
            async with ws_client.ws_connect(ws_path(devices)) as device_socket:
                seed(registry, ResourceType.NODE, make_node())

                grain = await read_grain(node_socket)
                assert entries(grain)[0]["path"] == NODE_ID
                await expect_no_grain(device_socket)

                seed(registry, ResourceType.DEVICE, make_device())
                grain = await read_grain(device_socket)
                assert entries(grain)[0]["path"] == DEVICE_ID
