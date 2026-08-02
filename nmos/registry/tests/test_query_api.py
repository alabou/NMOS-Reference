# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""HTTP-level tests for the IS-04 Query API."""

from __future__ import annotations

import pytest
from aiohttp import web

from nmos.registry import (
    InterfaceSecurity,
    Registry,
    create_query_app,
    create_registration_app,
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
    make_receiver,
    make_sender,
    make_source,
)
from nmos.registry.types import ResourceType

SUBSCRIPTIONS = f"{BASE_PATH}/subscriptions"


def build_registry() -> Registry:
    registry = Registry(
        RegistryStore(), query_id="8c4d1e70-6b3a-4f52-9d81-2e7c5a0b3f14",
    )
    registry.attach_subscriptions(SubscriptionManager(registry))
    return registry


def seed(registry: Registry, resource_type: ResourceType, raw: dict) -> None:
    typed = decode_resource(resource_type, raw)
    result = registry.register(resource_type, dict(raw), typed)
    assert result.ok, result.detail


def seed_tree(registry: Registry) -> None:
    seed(registry, ResourceType.NODE, make_node())
    seed(registry, ResourceType.DEVICE, make_device())
    seed(registry, ResourceType.SOURCE, make_source())
    seed(registry, ResourceType.FLOW, make_flow())
    seed(registry, ResourceType.SENDER, make_sender())
    seed(registry, ResourceType.RECEIVER, make_receiver())


@pytest.fixture
def registry() -> Registry:
    return build_registry()


@pytest.fixture
async def client(aiohttp_client, registry: Registry):  # type: ignore[no-untyped-def]
    app = create_query_app(registry, InterfaceSecurity(), ws_port=8448)
    return await aiohttp_client(app)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

class TestDiscovery:
    async def test_xnmos_lists_only_query(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/x-nmos")
        assert await response.json() == ["query/"]

    async def test_base_lists_seven_collections(self, client) -> None:  # type: ignore[no-untyped-def]
        """queryapi-base.json pins this to exactly seven entries."""
        response = await client.get(BASE_PATH)
        assert await response.json() == [
            "nodes/", "devices/", "sources/", "flows/",
            "senders/", "receivers/", "subscriptions/",
        ]


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------

class TestCollections:
    async def test_empty_collection(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get(f"{BASE_PATH}/nodes")
        assert response.status == 200
        assert await response.json() == []

    async def test_all_six_collections(self, client, registry) -> None:  # type: ignore[no-untyped-def]
        seed_tree(registry)
        for resource_type in ResourceType:
            response = await client.get(f"{BASE_PATH}/{resource_type.plural}")
            assert response.status == 200, resource_type.plural
            assert len(await response.json()) == 1, resource_type.plural

    async def test_unknown_collection_is_404(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get(f"{BASE_PATH}/widgets")
        assert response.status == 404

    async def test_paging_headers_always_present(self, client, registry) -> None:  # type: ignore[no-untyped-def]
        """``:13`` -- X-Paging-Limit MUST be returned when paging is in use."""
        seed(registry, ResourceType.NODE, make_node())
        response = await client.get(f"{BASE_PATH}/nodes")
        for header in (
            "X-Paging-Limit", "X-Paging-Since", "X-Paging-Until", "Link",
        ):
            assert header in response.headers, header

    async def test_descending_order(self, client, registry) -> None:  # type: ignore[no-untyped-def]
        """``:90`` -- most recently updated first."""
        seed(registry, ResourceType.NODE, make_node())
        seed(registry, ResourceType.NODE, make_node(node_id=NODE_ID_2))
        body = await (await client.get(f"{BASE_PATH}/nodes")).json()
        assert [n["id"] for n in body] == [NODE_ID_2, NODE_ID]

    async def test_limit_is_honoured(self, client, registry) -> None:  # type: ignore[no-untyped-def]
        seed(registry, ResourceType.NODE, make_node())
        seed(registry, ResourceType.NODE, make_node(node_id=NODE_ID_2))
        response = await client.get(f"{BASE_PATH}/nodes?paging.limit=1")
        assert len(await response.json()) == 1
        assert response.headers["X-Paging-Limit"] == "1"

    async def test_malformed_cursor_is_400(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get(f"{BASE_PATH}/nodes?paging.since=nonsense")
        assert response.status == 400

    async def test_resources_are_served_verbatim(self, client, registry) -> None:  # type: ignore[no-untyped-def]
        """The HTTP view must match what was registered, extensions included."""
        node = make_node(hostname="studio-1.example.com")
        node["urn:x-vendor:ext"] = {"a": [1, 2]}
        seed(registry, ResourceType.NODE, node)

        body = await (await client.get(f"{BASE_PATH}/nodes")).json()
        assert body[0]["hostname"] == "studio-1.example.com"
        assert body[0]["urn:x-vendor:ext"] == {"a": [1, 2]}


# ---------------------------------------------------------------------------
# Single resource
# ---------------------------------------------------------------------------

class TestSingleResource:
    async def test_get_by_id(self, client, registry) -> None:  # type: ignore[no-untyped-def]
        seed(registry, ResourceType.NODE, make_node())
        response = await client.get(f"{BASE_PATH}/nodes/{NODE_ID}")
        assert response.status == 200
        assert (await response.json())["id"] == NODE_ID

    async def test_unknown_id_is_404(self, client) -> None:  # type: ignore[no-untyped-def]
        """QueryAPI.raml:164."""
        response = await client.get(f"{BASE_PATH}/nodes/{NODE_ID}")
        assert response.status == 404

    async def test_deleted_resource_is_404(self, client, registry) -> None:  # type: ignore[no-untyped-def]
        """A tombstoned resource is gone as far as any API client is concerned."""
        seed(registry, ResourceType.NODE, make_node())
        registry.unregister(ResourceType.NODE, NODE_ID)
        response = await client.get(f"{BASE_PATH}/nodes/{NODE_ID}")
        assert response.status == 404


# ---------------------------------------------------------------------------
# Basic queries
# ---------------------------------------------------------------------------

class TestBasicQueries:
    async def test_filter_on_a_top_level_attribute(self, client, registry) -> None:  # type: ignore[no-untyped-def]
        seed(registry, ResourceType.NODE, make_node(label="alpha"))
        seed(registry, ResourceType.NODE, make_node(node_id=NODE_ID_2, label="beta"))

        body = await (await client.get(f"{BASE_PATH}/nodes?label=alpha")).json()
        assert [n["id"] for n in body] == [NODE_ID]

    async def test_two_parameters_are_anded(self, client, registry) -> None:  # type: ignore[no-untyped-def]
        """Example 2 at ``:476``."""
        seed_tree(registry)
        query = (
            f"{BASE_PATH}/sources"
            f"?format=urn:x-nmos:format:video&device_id={DEVICE_ID}"
        )
        assert len(await (await client.get(query)).json()) == 1

        mismatch = (
            f"{BASE_PATH}/sources"
            f"?format=urn:x-nmos:format:audio&device_id={DEVICE_ID}"
        )
        assert await (await client.get(mismatch)).json() == []

    async def test_dotted_path_into_an_object(self, client, registry) -> None:  # type: ignore[no-untyped-def]
        """``:447`` -- ``?subscription.sender_id=...``."""
        seed(registry, ResourceType.NODE, make_node())
        seed(registry, ResourceType.DEVICE, make_device())
        seed(
            registry, ResourceType.RECEIVER,
            make_receiver(subscription={"sender_id": SENDER_ID, "active": True}),
        )
        query = f"{BASE_PATH}/receivers?subscription.sender_id={SENDER_ID}"
        assert len(await (await client.get(query)).json()) == 1

    async def test_array_containment_in_tags(self, client, registry) -> None:  # type: ignore[no-untyped-def]
        """``:498`` -- ``tags.studio=HQ1`` matches when the ARRAY contains it."""
        seed(registry, ResourceType.NODE, make_node(tags={"studio": ["HQ1", "HQ2"]}))
        assert len(
            await (await client.get(f"{BASE_PATH}/nodes?tags.studio=HQ1")).json()
        ) == 1
        assert await (
            await client.get(f"{BASE_PATH}/nodes?tags.studio=HQ9")
        ).json() == []

    async def test_dotted_path_into_an_array_of_objects(
        self, client, registry,
    ) -> None:  # type: ignore[no-untyped-def]
        """Example 4 at ``:500`` -- ``?services.type=urn:...``."""
        seed(registry, ResourceType.NODE, make_node(services=[
            {"href": "http://192.0.2.1/tally", "type": "urn:x-manufacturer:service:tally"},
        ]))
        query = f"{BASE_PATH}/nodes?services.type=urn:x-manufacturer:service:tally"
        assert len(await (await client.get(query)).json()) == 1

    async def test_unknown_attribute_yields_empty_set(self, client, registry) -> None:  # type: ignore[no-untyped-def]
        """``:444`` -- "If a query parameter is requested which does not match
        an attribute found in any resource, an empty result set MUST be
        returned." Not a 400, and certainly not a 500.

        The AMWA mock ignores unknown filters and returns everything.
        """
        seed(registry, ResourceType.NODE, make_node())
        response = await client.get(f"{BASE_PATH}/nodes?no_such_attribute=x")
        assert response.status == 200
        assert await response.json() == []

    async def test_unknown_id_yields_empty_set_not_500(self, client, registry) -> None:  # type: ignore[no-untyped-def]
        """The AMWA mock raises KeyError into a 500 here."""
        seed(registry, ResourceType.NODE, make_node())
        response = await client.get(f"{BASE_PATH}/nodes?id={NODE_ID_2}")
        assert response.status == 200
        assert await response.json() == []

    async def test_filters_apply_before_paging(self, client, registry) -> None:  # type: ignore[no-untyped-def]
        """``:26`` -- filtering first, then paging the filtered set."""
        seed(registry, ResourceType.NODE, make_node(label="keep"))
        seed(registry, ResourceType.NODE, make_node(node_id=NODE_ID_2, label="drop"))

        response = await client.get(f"{BASE_PATH}/nodes?label=keep&paging.limit=10")
        body = await response.json()
        assert [n["id"] for n in body] == [NODE_ID]

    async def test_filters_are_preserved_in_link_header(
        self, client, registry,
    ) -> None:  # type: ignore[no-untyped-def]
        seed(registry, ResourceType.NODE, make_node(label="alpha"))
        response = await client.get(f"{BASE_PATH}/nodes?label=alpha")
        assert "label=alpha" in response.headers["Link"]


# ---------------------------------------------------------------------------
# Downgrade, RQL, ancestry
# ---------------------------------------------------------------------------

class TestOptionalQueryFeatures:
    async def test_downgrade_within_major_is_accepted(self, client, registry) -> None:  # type: ignore[no-untyped-def]
        """A v1.x downgrade is valid. This registry stores only v1.3, so it
        adds nothing, but it must not be refused."""
        seed(registry, ResourceType.NODE, make_node())
        response = await client.get(f"{BASE_PATH}/nodes?query.downgrade=v1.0")
        assert response.status == 200
        assert len(await response.json()) == 1

    async def test_downgrade_across_major_is_400(self, client) -> None:  # type: ignore[no-untyped-def]
        """``:434`` -- "Returns an HTTP 400 (Bad Request) error code as
        downgrade queries MUST NOT be performed between major API versions"."""
        response = await client.get(f"{BASE_PATH}/nodes?query.downgrade=v2.0")
        assert response.status == 400

    async def test_malformed_downgrade_is_400(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get(f"{BASE_PATH}/nodes?query.downgrade=1.0")
        assert response.status == 400

    async def test_downgrade_on_single_resource(self, client, registry) -> None:  # type: ignore[no-untyped-def]
        """QueryAPI.raml:157 -- the single-resource GET carries the trait too."""
        seed(registry, ResourceType.NODE, make_node())
        ok = await client.get(f"{BASE_PATH}/nodes/{NODE_ID}?query.downgrade=v1.1")
        assert ok.status == 200
        bad = await client.get(f"{BASE_PATH}/nodes/{NODE_ID}?query.downgrade=v3.0")
        assert bad.status == 400

    async def test_rql_is_501(self, client) -> None:  # type: ignore[no-untyped-def]
        """``:528`` -- 501 when RQL is unsupported.

        Silently ignoring it would hand back an unfiltered set that the client
        would treat as filtered.
        """
        response = await client.get(f"{BASE_PATH}/nodes?query.rql=eq(label,x)")
        assert response.status == 501

    async def test_ancestry_is_501(self, client) -> None:  # type: ignore[no-untyped-def]
        """``:578``."""
        response = await client.get(
            f"{BASE_PATH}/sources?query.ancestry_id={NODE_ID}"
            f"&query.ancestry_type=children",
        )
        assert response.status == 501


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------

def subscription_body(**overrides: object) -> dict:
    body = {
        "max_update_rate_ms": 100,
        "resource_path": "/senders",
        "persist": False,
        "params": {},
    }
    body.update(overrides)
    return body


class TestSubscriptions:
    async def test_create_returns_201_with_location(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(SUBSCRIPTIONS, json=subscription_body())
        assert response.status == 201

        body = await response.json()
        assert response.headers["Location"] == f"{SUBSCRIPTIONS}/{body['id']}"

    async def test_response_matches_schema_shape(self, client) -> None:  # type: ignore[no-untyped-def]
        """queryapi-subscription-response.json required members."""
        response = await client.post(SUBSCRIPTIONS, json=subscription_body())
        body = await response.json()
        for name in (
            "id", "ws_href", "max_update_rate_ms", "persist", "secure",
            "resource_path", "params",
        ):
            assert name in body, name

    async def test_ws_href_targets_the_websocket_port(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(SUBSCRIPTIONS, json=subscription_body())
        ws_href = (await response.json())["ws_href"]
        assert ws_href.startswith("ws://")
        assert ":8448/x-nmos/query/v1.3/subscriptions/" in ws_href

    async def test_matching_request_returns_200(self, client) -> None:  # type: ignore[no-untyped-def]
        """``:25`` -- an existing matching subscription MAY be returned."""
        first = await client.post(SUBSCRIPTIONS, json=subscription_body())
        assert first.status == 201
        second = await client.post(SUBSCRIPTIONS, json=subscription_body())
        assert second.status == 200
        assert (await first.json())["id"] == (await second.json())["id"]

    async def test_differing_params_create_separate_subscriptions(
        self, client,
    ) -> None:  # type: ignore[no-untyped-def]
        a = await client.post(SUBSCRIPTIONS, json=subscription_body())
        b = await client.post(
            SUBSCRIPTIONS,
            json=subscription_body(params={"transport": "urn:x-nmos:transport:rtp"}),
        )
        assert b.status == 201
        assert (await a.json())["id"] != (await b.json())["id"]

    async def test_params_are_preserved(self, client) -> None:  # type: ignore[no-untyped-def]
        """The old NEmpty typing silently discarded these."""
        params = {"transport": "urn:x-nmos:transport:rtp", "tags.studio": "HQ1"}
        response = await client.post(
            SUBSCRIPTIONS, json=subscription_body(params=params),
        )
        assert (await response.json())["params"] == params

    async def test_missing_required_members(self, client) -> None:  # type: ignore[no-untyped-def]
        for missing in (
            "max_update_rate_ms", "persist", "resource_path", "params",
        ):
            body = subscription_body()
            del body[missing]
            response = await client.post(SUBSCRIPTIONS, json=body)
            assert response.status == 400, missing

    async def test_unsubscribable_resource_path(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            SUBSCRIPTIONS, json=subscription_body(resource_path="/widgets"),
        )
        assert response.status == 400

    async def test_secure_mismatch_is_400(self, client) -> None:  # type: ignore[no-untyped-def]
        """``:13`` -- a client MAY request the opposite of the API's mode,
        "however they will receive a 400 (Bad Request) response code unless
        the Query API explicitly supports a mismatch"."""
        response = await client.post(
            SUBSCRIPTIONS, json=subscription_body(secure=True),
        )
        assert response.status == 400

    async def test_secure_defaults_to_the_api_mode(self, client) -> None:  # type: ignore[no-untyped-def]
        """Omitted ``secure`` takes the server's value -- false over HTTP."""
        response = await client.post(SUBSCRIPTIONS, json=subscription_body())
        assert (await response.json())["secure"] is False

    async def test_authorization_mismatch_is_400(self, client) -> None:  # type: ignore[no-untyped-def]
        """``:15``."""
        response = await client.post(
            SUBSCRIPTIONS, json=subscription_body(authorization=True),
        )
        assert response.status == 400

    async def test_get_subscription(self, client) -> None:  # type: ignore[no-untyped-def]
        created = await (
            await client.post(SUBSCRIPTIONS, json=subscription_body())
        ).json()
        response = await client.get(f"{SUBSCRIPTIONS}/{created['id']}")
        assert response.status == 200
        assert (await response.json())["id"] == created["id"]

    async def test_get_unknown_subscription_is_404(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get(f"{SUBSCRIPTIONS}/{NODE_ID}")
        assert response.status == 404

    async def test_list_subscriptions(self, client) -> None:  # type: ignore[no-untyped-def]
        await client.post(SUBSCRIPTIONS, json=subscription_body())
        response = await client.get(SUBSCRIPTIONS)
        assert response.status == 200
        assert len(await response.json()) == 1

    async def test_delete_non_persistent_is_403(self, client) -> None:  # type: ignore[no-untyped-def]
        """``:18`` -- "The Query API MUST NOT acknowledge HTTP DELETE requests
        for Subscriptions running in this non-persistent mode, instead issuing
        an HTTP 403 (Forbidden) response"."""
        created = await (
            await client.post(SUBSCRIPTIONS, json=subscription_body(persist=False))
        ).json()
        response = await client.delete(f"{SUBSCRIPTIONS}/{created['id']}")
        assert response.status == 403

    async def test_delete_persistent_is_204(self, client) -> None:  # type: ignore[no-untyped-def]
        created = await (
            await client.post(SUBSCRIPTIONS, json=subscription_body(persist=True))
        ).json()
        response = await client.delete(f"{SUBSCRIPTIONS}/{created['id']}")
        assert response.status == 204
        assert (await client.get(f"{SUBSCRIPTIONS}/{created['id']}")).status == 404

    async def test_delete_unknown_is_404(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.delete(f"{SUBSCRIPTIONS}/{NODE_ID}")
        assert response.status == 404

    async def test_subscriptions_route_precedes_collections(self, client) -> None:  # type: ignore[no-untyped-def]
        """``/subscriptions`` must not be captured as a resource collection."""
        response = await client.get(SUBSCRIPTIONS)
        assert response.status == 200


# ---------------------------------------------------------------------------
# Cross-cutting
# ---------------------------------------------------------------------------

class TestHttpConventions:
    async def test_trailing_slash_equivalence(self, client, registry) -> None:  # type: ignore[no-untyped-def]
        seed(registry, ResourceType.NODE, make_node())
        for path in (
            f"{BASE_PATH}/nodes", f"{BASE_PATH}/nodes/",
            f"{BASE_PATH}/nodes/{NODE_ID}", f"{BASE_PATH}/nodes/{NODE_ID}/",
        ):
            response = await client.get(path, allow_redirects=False)
            assert response.status == 200, path

    async def test_error_bodies_are_nmos_json(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get(f"{BASE_PATH}/nodes/{NODE_ID}")
        assert response.status == 404
        body = await response.json()
        assert body["code"] == 404
        assert isinstance(body["error"], str)

    async def test_options_preflight(self, client) -> None:  # type: ignore[no-untyped-def]
        """QueryAPI.raml:407-411, :471-475."""
        for path in (SUBSCRIPTIONS, f"{SUBSCRIPTIONS}/{NODE_ID}"):
            response = await client.options(path)
            assert response.status == 200, path


# ---------------------------------------------------------------------------
# Registration and Query over one store
# ---------------------------------------------------------------------------

class TestSharedStore:
    async def test_registered_resources_appear_in_query(
        self, aiohttp_client,
    ) -> None:  # type: ignore[no-untyped-def]
        """The two interfaces are separate listeners over one registry.

        This is the whole point of the process: a Node POSTs to Registration
        and a Controller reads the result from Query.
        """
        registry = build_registry()
        security = InterfaceSecurity()
        reg_client = await aiohttp_client(
            create_registration_app(registry, security),
        )
        query_client = await aiohttp_client(
            create_query_app(registry, security, ws_port=8448),
        )

        posted = await reg_client.post(
            "/x-nmos/registration/v1.3/resource",
            json={"type": "node", "data": make_node()},
        )
        assert posted.status == 201

        listed = await query_client.get(f"{BASE_PATH}/nodes")
        assert [n["id"] for n in await listed.json()] == [NODE_ID]

        await reg_client.delete(
            f"/x-nmos/registration/v1.3/resource/nodes/{NODE_ID}",
        )
        assert await (await query_client.get(f"{BASE_PATH}/nodes")).json() == []
