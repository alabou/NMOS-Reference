# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""HTTP-level tests for the IS-04 Registration API.

Exercises the real aiohttp application through ``aiohttp_client``, so the
shared middleware stack (trailing slash, CORS, JSON error bodies) is under
test alongside the handlers.
"""

from __future__ import annotations

import pytest
from aiohttp import web

from nmos.registry import InterfaceSecurity, Registry, create_registration_app
from nmos.registry.handlers_registration import BASE_PATH
from nmos.registry.store import RegistryStore, health_now
from nmos.registry.types import ResourceType
from nmos.registry.tests._fixtures import (
    DEVICE_ID,
    NODE_ID,
    SENDER_ID,
    make_device,
    make_flow,
    make_node,
    make_receiver,
    make_sender,
    make_source,
    tai_version,
)

RESOURCE = f"{BASE_PATH}/resource"
HEALTH = f"{BASE_PATH}/health/nodes"


def build_app() -> web.Application:
    """A Registration API app with no subscription manager attached.

    Grain publication is exercised by the subscription tests; leaving it
    unattached here keeps these tests focused on the Registration API's own
    contract and confirms the registry works standalone (Registry._publish is
    a no-op when nothing is listening).
    """
    store = RegistryStore()
    registry = Registry(store, query_id="8c4d1e70-6b3a-4f52-9d81-2e7c5a0b3f14")
    return create_registration_app(registry, InterfaceSecurity())


@pytest.fixture
async def client(aiohttp_client):  # type: ignore[no-untyped-def]
    return await aiohttp_client(build_app())


async def post(client, resource_type: str, data: dict):  # type: ignore[no-untyped-def]
    return await client.post(RESOURCE, json={"type": resource_type, "data": data})


async def register_tree(client) -> None:  # type: ignore[no-untyped-def]
    """Register the whole tree in the order :57-64 mandates."""
    for resource_type, data in (
        ("node", make_node()),
        ("device", make_device()),
        ("source", make_source()),
        ("flow", make_flow()),
        ("sender", make_sender()),
        ("receiver", make_receiver()),
    ):
        response = await post(client, resource_type, data)
        assert response.status == 201, await response.text()


# ---------------------------------------------------------------------------
# Discovery ladder
# ---------------------------------------------------------------------------

class TestDiscovery:
    async def test_root(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/")
        assert response.status == 200
        assert await response.json() == ["x-nmos/"]

    async def test_xnmos_lists_only_registration(self, client) -> None:  # type: ignore[no-untyped-def]
        """The registration port must not advertise ``query/``.

        The Query API is a different listener with a different security
        policy; advertising it here would point clients at a closed port.
        """
        response = await client.get("/x-nmos")
        assert await response.json() == ["registration/"]

    async def test_versions(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/x-nmos/registration")
        assert await response.json() == ["v1.3/"]

    async def test_base_matches_schema(self, client) -> None:  # type: ignore[no-untyped-def]
        """registrationapi-base.json pins this to exactly two entries."""
        response = await client.get(BASE_PATH)
        assert response.status == 200
        assert await response.json() == ["resource/", "health/"]


# ---------------------------------------------------------------------------
# POST /resource
# ---------------------------------------------------------------------------

class TestPostResource:
    async def test_create_returns_201_with_location(self, client) -> None:  # type: ignore[no-untyped-def]
        """:25 plus RegistrationAPI.raml:49-56."""
        response = await post(client, "node", make_node())
        assert response.status == 201
        assert response.headers["Location"] == f"{RESOURCE}/nodes/{NODE_ID}"

    async def test_update_returns_200_with_location(self, client) -> None:  # type: ignore[no-untyped-def]
        """:25 -- an update to a previous record answers 200, not 201."""
        await post(client, "node", make_node())
        response = await post(
            client, "node", make_node(version=tai_version(+1), label="renamed"),
        )
        assert response.status == 200
        assert response.headers["Location"] == f"{RESOURCE}/nodes/{NODE_ID}"

    async def test_response_body_is_the_resource(self, client) -> None:  # type: ignore[no-untyped-def]
        """registrationapi-resource-response.json -- the body is the resource."""
        response = await post(client, "node", make_node())
        body = await response.json()
        assert body["id"] == NODE_ID
        assert body["label"] == "test-node"

    async def test_unmodelled_attributes_are_echoed(self, client) -> None:  # type: ignore[no-untyped-def]
        """The registry must not rewrite what a Node registered.

        ``hostname`` is a real (optional, deprecated) node.json attribute that
        the generated NNode type does not model.
        """
        node = make_node(hostname="studio-1.example.com")
        response = await post(client, "node", node)
        assert (await response.json())["hostname"] == "studio-1.example.com"

    async def test_content_type_is_json(self, client) -> None:  # type: ignore[no-untyped-def]
        """APIs.md:26 -- Content-Type: application/json, without a charset."""
        response = await post(client, "node", make_node())
        assert response.headers["Content-Type"] == "application/json"

    async def test_invalid_json(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            RESOURCE, data="{not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status == 400

    async def test_unknown_type(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await post(client, "widget", make_node())
        assert response.status == 400
        assert "widget" in (await response.json())["debug"]

    async def test_missing_envelope_members(self, client) -> None:  # type: ignore[no-untyped-def]
        for body in ({}, {"type": "node"}, {"data": make_node()}):
            response = await client.post(RESOURCE, json=body)
            assert response.status == 400, body

    async def test_schema_failure(self, client) -> None:  # type: ignore[no-untyped-def]
        """:100 -- a body that does not meet the schema is a 400."""
        broken = make_node()
        del broken["interfaces"]
        response = await post(client, "node", broken)
        assert response.status == 400

    async def test_referential_integrity(self, client) -> None:  # type: ignore[no-untyped-def]
        """:55 -- a Device with no registered Node is refused."""
        response = await post(client, "device", make_device())
        assert response.status == 400
        assert NODE_ID in (await response.json())["debug"]

    async def test_version_regression(self, client) -> None:  # type: ignore[no-untyped-def]
        """:102."""
        await post(client, "node", make_node(version=tai_version(+10)))
        response = await post(client, "node", make_node(version=tai_version(-10)))
        assert response.status == 400

    async def test_id_reused_by_another_type(self, client) -> None:  # type: ignore[no-untyped-def]
        """:101."""
        await post(client, "node", make_node())
        response = await post(client, "device", make_device(device_id=NODE_ID))
        assert response.status == 400

    async def test_full_tree_registers(self, client) -> None:  # type: ignore[no-untyped-def]
        await register_tree(client)


# ---------------------------------------------------------------------------
# DELETE and debug GET
# ---------------------------------------------------------------------------

class TestDeleteResource:
    async def test_delete_returns_204(self, client) -> None:  # type: ignore[no-untyped-def]
        """RegistrationAPI.raml:91-92 -- 204 No Content."""
        await post(client, "node", make_node())
        response = await client.delete(f"{RESOURCE}/nodes/{NODE_ID}")
        assert response.status == 204
        assert await response.read() == b""

    async def test_delete_unknown_returns_404(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.delete(f"{RESOURCE}/nodes/{NODE_ID}")
        assert response.status == 404

    async def test_delete_cascades(self, client) -> None:  # type: ignore[no-untyped-def]
        """:68 -- deleting a parent removes every child immediately."""
        await register_tree(client)
        response = await client.delete(f"{RESOURCE}/nodes/{NODE_ID}")
        assert response.status == 204

        for plural, resource_id in (
            ("devices", DEVICE_ID), ("senders", SENDER_ID),
        ):
            probe = await client.get(f"{RESOURCE}/{plural}/{resource_id}")
            assert probe.status == 404, plural

    async def test_unknown_resource_type_segment(self, client) -> None:  # type: ignore[no-untyped-def]
        """RegistrationAPI.raml:75-82 fixes the six permitted plural names."""
        response = await client.delete(f"{RESOURCE}/widgets/{NODE_ID}")
        assert response.status == 404

    async def test_singular_type_segment_is_rejected(self, client) -> None:  # type: ignore[no-untyped-def]
        """No ``rstrip("s")`` -- ``/resource/node/{id}`` is not a valid path."""
        await post(client, "node", make_node())
        response = await client.delete(f"{RESOURCE}/node/{NODE_ID}")
        assert response.status == 404

    async def test_malformed_uuid_is_404(self, client) -> None:  # type: ignore[no-untyped-def]
        """The RAML constrains the id to a UUID (:72)."""
        response = await client.delete(f"{RESOURCE}/nodes/not-a-uuid")
        assert response.status == 404

    async def test_debug_get(self, client) -> None:  # type: ignore[no-untyped-def]
        """RegistrationAPI.raml:104 -- read back a registration."""
        await post(client, "node", make_node())
        response = await client.get(f"{RESOURCE}/nodes/{NODE_ID}")
        assert response.status == 200
        assert (await response.json())["id"] == NODE_ID


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

class TestHealth:
    async def test_heartbeat_returns_health_as_string(self, client) -> None:  # type: ignore[no-untyped-def]
        """registrationapi-health-response.json -- ``health`` is a STRING.

        nmos-cpp agrees. The AMWA mock returns a JSON number, which does not
        satisfy its own specification's schema.
        """
        await post(client, "node", make_node())
        response = await client.post(f"{HEALTH}/{NODE_ID}")
        assert response.status == 200

        body = await response.json()
        assert isinstance(body["health"], str), body
        assert body["health"].isdigit()
        assert abs(int(body["health"]) - health_now()) <= 2

    async def test_heartbeat_unknown_node_returns_404(self, client) -> None:  # type: ignore[no-untyped-def]
        """:112-114 -- the Node must then re-register all of its resources."""
        response = await client.post(f"{HEALTH}/{NODE_ID}")
        assert response.status == 404

    async def test_heartbeat_after_delete_is_404(self, client) -> None:  # type: ignore[no-untyped-def]
        await post(client, "node", make_node())
        await client.delete(f"{RESOURCE}/nodes/{NODE_ID}")
        response = await client.post(f"{HEALTH}/{NODE_ID}")
        assert response.status == 404

    async def test_debug_get_health_does_not_heartbeat(self, client) -> None:  # type: ignore[no-untyped-def]
        """RegistrationAPI.raml:152 -- a diagnostic read must not keep a Node
        alive, or it would mask the very problem it is used to investigate."""
        await post(client, "node", make_node())
        app_registry: Registry = client.app["registry"]
        node = app_registry.store.get(ResourceType.NODE, NODE_ID)
        assert node is not None
        node.health = 1000

        response = await client.get(f"{HEALTH}/{NODE_ID}")
        assert response.status == 200
        assert (await response.json())["health"] == "1000"
        assert node.health == 1000

    async def test_health_unknown_node_get(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get(f"{HEALTH}/{NODE_ID}")
        assert response.status == 404


# ---------------------------------------------------------------------------
# Cross-cutting HTTP requirements
# ---------------------------------------------------------------------------

class TestHttpConventions:
    async def test_trailing_slash_equivalence(self, client) -> None:  # type: ignore[no-untyped-def]
        """APIs.md:83-92 -- both spellings work, with no redirect."""
        for path in ("/x-nmos/registration", "/x-nmos/registration/", BASE_PATH, BASE_PATH + "/"):
            response = await client.get(path, allow_redirects=False)
            assert response.status == 200, path

    async def test_post_works_without_trailing_slash(self, client) -> None:  # type: ignore[no-untyped-def]
        """:92 -- state-changing verbs MUST work without a trailing slash and
        SHOULD NOT be answered with a 3xx."""
        response = await client.post(
            RESOURCE, json={"type": "node", "data": make_node()},
            allow_redirects=False,
        )
        assert response.status == 201

    async def test_error_bodies_are_nmos_json(self, client) -> None:  # type: ignore[no-untyped-def]
        """APIs.md:102-114 -- every status >= 400 carries {code, error, debug}.

        The AMWA mock returns Flask's HTML error pages here.
        """
        response = await client.delete(f"{RESOURCE}/nodes/{NODE_ID}")
        assert response.status == 404
        assert response.headers["Content-Type"] == "application/json"

        body = await response.json()
        assert body["code"] == 404
        assert isinstance(body["error"], str) and body["error"]
        assert "debug" in body

    async def test_options_preflight(self, client) -> None:  # type: ignore[no-untyped-def]
        """RegistrationAPI.raml:30-34, :83-87, :128-132 define OPTIONS."""
        for path in (RESOURCE, f"{RESOURCE}/nodes/{NODE_ID}", f"{HEALTH}/{NODE_ID}"):
            response = await client.options(path)
            assert response.status == 200, path
            assert "Access-Control-Allow-Methods" in response.headers

    async def test_cors_headers_present(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get(BASE_PATH)
        assert response.headers["Access-Control-Allow-Origin"] == "*"
