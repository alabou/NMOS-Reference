# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Validate live API responses against the IS-04 JSON schemas.

Every other test in this suite asserts behaviour the implementation was
written to produce. This one checks the output against an independent
authority: the schemas shipped in ``nmos/registry/specs/schemas/``, copied
verbatim from AMWA IS-04 ``v1.3.x`` at tag ``v1.3.3``.

That independence is the point. The registry validates *incoming* resources by
decoding them into the generated NMOS types, so a mistake in those type
definitions would be invisible to a test that used the same types to check the
answer. Validating the wire bytes against the published schema catches the
class of error where implementation and test share a wrong assumption.

``jsonschema`` is not a project dependency — it is a test-only convenience —
so the whole module skips when it is absent, following the same pattern as the
PKI-gated TLS suites.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

jsonschema = pytest.importorskip(
    "jsonschema", reason="jsonschema is an optional test-only dependency",
)

from nmos.registry import (  # noqa: E402
    InterfaceSecurity,
    Registry,
    create_query_app,
    create_registration_app,
)
from nmos.registry.handlers_query import BASE_PATH as QUERY_BASE  # noqa: E402
from nmos.registry.handlers_registration import BASE_PATH as REG_BASE  # noqa: E402
from nmos.registry.store import RegistryStore  # noqa: E402
from nmos.registry.subscriptions import SubscriptionManager, _PendingEvent  # noqa: E402
from nmos.registry.tests._fixtures import (  # noqa: E402
    NODE_ID,
    make_device,
    make_flow,
    make_node,
    make_receiver,
    make_sender,
    make_source,
)

SCHEMAS = Path(__file__).resolve().parents[1] / "specs" / "schemas"


def validator_for(name: str) -> Any:
    """Build a Draft-4 validator that resolves sibling ``$ref``s.

    The IS-04 schemas cross-reference each other by bare filename
    (``resource_core.json``, ``sender.json``, …), so every schema in the
    directory is preloaded into the resolver store.
    """
    from jsonschema import Draft4Validator, RefResolver

    store: dict[str, Any] = {}
    for path in SCHEMAS.glob("*.json"):
        document = json.loads(path.read_text())
        store[path.as_uri()] = document
        store[path.name] = document

    schema_path = SCHEMAS / name
    schema = json.loads(schema_path.read_text())
    resolver = RefResolver(
        base_uri=schema_path.as_uri(), referrer=schema, store=store,
    )
    return Draft4Validator(schema, resolver=resolver)


def assert_valid(name: str, document: Any) -> None:
    validator = validator_for(name)
    errors = sorted(validator.iter_errors(document), key=lambda e: str(e.path))
    assert not errors, "\n".join(
        f"{list(e.path)}: {e.message}" for e in errors[:5]
    )


def build_registry() -> Registry:
    registry = Registry(
        RegistryStore(), query_id="8c4d1e70-6b3a-4f52-9d81-2e7c5a0b3f14",
    )
    registry.attach_subscriptions(SubscriptionManager(registry))
    return registry


@pytest.fixture
def registry() -> Registry:
    return build_registry()


@pytest.fixture
async def clients(aiohttp_client, registry: Registry):  # type: ignore[no-untyped-def]
    security = InterfaceSecurity()
    registration = await aiohttp_client(
        create_registration_app(registry, security),
    )
    query = await aiohttp_client(
        create_query_app(registry, security, ws_port=8448),
    )
    return registration, query


async def register_tree(registration: Any) -> None:
    for resource_type, data in (
        ("node", make_node()),
        ("device", make_device()),
        ("source", make_source()),
        ("flow", make_flow()),
        ("sender", make_sender()),
        ("receiver", make_receiver()),
    ):
        response = await registration.post(
            f"{REG_BASE}/resource", json={"type": resource_type, "data": data},
        )
        assert response.status == 201, await response.text()


# ---------------------------------------------------------------------------
# Registration API
# ---------------------------------------------------------------------------

class TestRegistrationSchemas:
    async def test_base_response(self, clients) -> None:  # type: ignore[no-untyped-def]
        registration, _query = clients
        response = await registration.get(REG_BASE)
        assert_valid("registrationapi-base.json", await response.json())

    async def test_post_response(self, clients) -> None:  # type: ignore[no-untyped-def]
        """The POST body echoes the resource, per
        ``registrationapi-resource-response.json`` (a oneOf over the six)."""
        registration, _query = clients
        response = await registration.post(
            f"{REG_BASE}/resource", json={"type": "node", "data": make_node()},
        )
        assert_valid(
            "registrationapi-resource-response.json", await response.json(),
        )

    async def test_health_response(self, clients) -> None:  # type: ignore[no-untyped-def]
        """The schema types ``health`` as a string matching ``^[0-9]+$``.

        This is the check that would have caught the AMWA mock's integer.
        """
        registration, _query = clients
        await registration.post(
            f"{REG_BASE}/resource", json={"type": "node", "data": make_node()},
        )
        response = await registration.post(f"{REG_BASE}/health/nodes/{NODE_ID}")
        assert_valid(
            "registrationapi-health-response.json", await response.json(),
        )

    async def test_error_response(self, clients) -> None:  # type: ignore[no-untyped-def]
        """``APIs.md:102-114`` -- every status >= 400 carries the error body."""
        registration, _query = clients
        response = await registration.delete(f"{REG_BASE}/resource/nodes/{NODE_ID}")
        assert response.status == 404
        assert_valid("error.json", await response.json())


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------

class TestQuerySchemas:
    async def test_base_response(self, clients) -> None:  # type: ignore[no-untyped-def]
        _registration, query = clients
        response = await query.get(QUERY_BASE)
        assert_valid("queryapi-base.json", await response.json())

    @pytest.mark.parametrize(
        "collection,schema",
        [
            ("nodes", "nodes.json"),
            ("devices", "devices.json"),
            ("sources", "sources.json"),
            ("flows", "flows.json"),
            ("senders", "senders.json"),
            ("receivers", "receivers.json"),
        ],
    )
    async def test_collections(
        self, clients, collection: str, schema: str,
    ) -> None:  # type: ignore[no-untyped-def]
        """Every collection response must validate against its array schema.

        This is the strongest single check in the suite: it proves the
        registry serves back exactly what IS-04 says a Query API returns, for
        all six resource types at once.
        """
        registration, query = clients
        await register_tree(registration)

        response = await query.get(f"{QUERY_BASE}/{collection}")
        body = await response.json()
        assert len(body) == 1, collection
        assert_valid(schema, body)

    @pytest.mark.parametrize(
        "collection,schema",
        [
            ("nodes", "node.json"),
            ("devices", "device.json"),
            ("sources", "source.json"),
            ("flows", "flow.json"),
            ("senders", "sender.json"),
            ("receivers", "receiver.json"),
        ],
    )
    async def test_single_resources(
        self, clients, collection: str, schema: str,
    ) -> None:  # type: ignore[no-untyped-def]
        registration, query = clients
        await register_tree(registration)

        listed = await (await query.get(f"{QUERY_BASE}/{collection}")).json()
        resource_id = listed[0]["id"]

        response = await query.get(f"{QUERY_BASE}/{collection}/{resource_id}")
        assert_valid(schema, await response.json())

    async def test_subscription_response(self, clients) -> None:  # type: ignore[no-untyped-def]
        """``queryapi-subscription-response.json`` requires ``secure``.

        Before the definition fix, ``secure`` was optional in the generated
        type and ``params`` was typed ``NEmpty``, which dropped every filter.
        Both would fail here.
        """
        _registration, query = clients
        response = await query.post(f"{QUERY_BASE}/subscriptions", json={
            "max_update_rate_ms": 100,
            "resource_path": "/senders",
            "persist": False,
            "params": {"transport": "urn:x-nmos:transport:rtp"},
        })
        body = await response.json()
        assert_valid("queryapi-subscription-response.json", body)
        assert body["params"] == {"transport": "urn:x-nmos:transport:rtp"}

    async def test_subscriptions_list_response(self, clients) -> None:  # type: ignore[no-untyped-def]
        _registration, query = clients
        await query.post(f"{QUERY_BASE}/subscriptions", json={
            "max_update_rate_ms": 100,
            "resource_path": "/senders",
            "persist": True,
            "params": {},
        })
        response = await query.get(f"{QUERY_BASE}/subscriptions")
        assert_valid("queryapi-subscriptions-response.json", await response.json())

    async def test_error_response(self, clients) -> None:  # type: ignore[no-untyped-def]
        _registration, query = clients
        response = await query.get(f"{QUERY_BASE}/nodes/{NODE_ID}")
        assert response.status == 404
        assert_valid("error.json", await response.json())


# ---------------------------------------------------------------------------
# WebSocket grains
# ---------------------------------------------------------------------------

class TestGrainSchema:
    """``queryapi-subscriptions-websocket.json`` -- the grain envelope.

    Grains are built here rather than read off a socket so each event shape
    can be checked in isolation; the socket path itself is covered by
    ``test_subscriptions.py``.
    """

    def _subscription(self, registry: Registry) -> Any:
        subscription, _created = registry.subscriptions.create_or_match(
            resource_path="/senders",
            params={},
            max_update_rate_ms=0,
            persist=True,
            secure=False,
            authorization=False,
            host="localhost",
            ws_scheme="ws",
            ws_host="localhost:8448",
        )
        return subscription

    def test_added_grain(self, registry: Registry) -> None:
        subscription = self._subscription(registry)
        sender = make_sender()
        grain = registry.subscriptions.build_grain(
            subscription, [_PendingEvent(sender["id"], None, sender)],
        )
        assert_valid("queryapi-subscriptions-websocket.json", json.loads(grain))

    def test_removed_grain(self, registry: Registry) -> None:
        subscription = self._subscription(registry)
        sender = make_sender()
        grain = registry.subscriptions.build_grain(
            subscription, [_PendingEvent(sender["id"], sender, None)],
        )
        assert_valid("queryapi-subscriptions-websocket.json", json.loads(grain))

    def test_modified_grain(self, registry: Registry) -> None:
        subscription = self._subscription(registry)
        before = make_sender(label="before")
        after = make_sender(label="after")
        grain = registry.subscriptions.build_grain(
            subscription, [_PendingEvent(before["id"], before, after)],
        )
        assert_valid("queryapi-subscriptions-websocket.json", json.loads(grain))

    def test_sync_grain(self, registry: Registry) -> None:
        subscription = self._subscription(registry)
        sender = make_sender()
        grain = registry.subscriptions.build_grain(
            subscription, [_PendingEvent(sender["id"], sender, sender)],
        )
        assert_valid("queryapi-subscriptions-websocket.json", json.loads(grain))

    def test_multi_entry_grain(self, registry: Registry) -> None:
        """Coalesced windows produce several entries in one grain."""
        subscription = self._subscription(registry)
        first = make_sender()
        second = make_sender(
            sender_id="2c6f7a91-3b5d-4e28-9c14-8a0d6f3b2e57",
        )
        grain = registry.subscriptions.build_grain(
            subscription,
            [
                _PendingEvent(first["id"], None, first),
                _PendingEvent(second["id"], None, second),
            ],
        )
        document = json.loads(grain)
        assert len(document["grain"]["data"]) == 2
        assert_valid("queryapi-subscriptions-websocket.json", document)
