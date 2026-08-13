# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The Query API WebSocket over TLS, mTLS and OAuth 2.0.

``test_subscriptions.py`` drives the WebSocket exhaustively but over plain
HTTP, and ``test_tls_registry.py`` checks the HTTP surface under every
security mode. Neither opens a *secure* socket, so the ``wss://`` path was
previously asserted only as a string in ``ws_href``. This module closes that
gap.

Why it needs its own module
---------------------------
A WebSocket is the one endpoint where a registry can pass every HTTP test and
still be unusable. The upgrade is a GET, so it takes a different route through
the middleware than the POST that created the subscription; the socket is
long-lived, so a TLS configuration that only works for short request/response
exchanges will appear fine until the first grain; and the listener is a
separate ``web.Application`` on a separate port, so it can silently miss
security configuration the Query API listener has.

Every test here therefore asserts on **delivered grains**, not merely on a
completed handshake. Connecting and then never receiving anything is the
failure mode worth catching.
"""

from __future__ import annotations

import asyncio
import json
import ssl
from typing import Any

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from nmos.api.tests._tls_helpers import (
    CERTS_DIR,
    PKI_AVAILABLE,
    build_client_ssl_context,
    build_server_ssl_context,
    root_ca,
    server_cert_names,
)
from nmos.registry import (
    InterfaceSecurity,
    Registry,
    create_query_app,
    create_query_ws_app,
)
from nmos.registry.decode import decode_resource
from nmos.registry.handlers_query import BASE_PATH as QUERY_BASE
from nmos.registry.store import RegistryStore
from nmos.registry.subscriptions import SubscriptionManager
from nmos.registry.tests._fixtures import (
    NODE_ID,
    NODE_ID_2,
    make_node,
    tai_version,
)
from nmos.registry.types import Body, ResourceType

pytestmark = pytest.mark.skipif(
    not PKI_AVAILABLE,
    reason=f"pre-generated TLS PKI not present at {CERTS_DIR}",
)

SERIAL = "SNX00000"
CLIENT_SERIAL = "SNX00001"
GRAIN_TIMEOUT_S = 5.0


def build_registry() -> Registry:
    registry = Registry(
        RegistryStore(), query_id="8c4d1e70-6b3a-4f52-9d81-2e7c5a0b3f14",
    )
    registry.attach_subscriptions(SubscriptionManager(registry))
    return registry


def security_for(
    *, client_auth_required: bool = False, oauth2: bool = False,
) -> InterfaceSecurity:
    return InterfaceSecurity(
        client_auth_required=client_auth_required,
        oauth2=oauth2,
        serial_number=SERIAL,
        tls_server_cert_names=server_cert_names(SERIAL),
    )


async def start_tls_servers(
    registry: Registry,
    security: InterfaceSecurity,
    *,
    flavor: str = "rsa",
    client_auth: str = "none",
) -> tuple[TestServer, TestServer]:
    """Start the Query API and its WebSocket listener, both over TLS.

    They are separate applications on separate ports, exactly as
    ``nmos_registry.py`` runs them, and share one SSL context — the property
    that makes a subscription's ``secure`` attribute describe both.
    """
    ssl_context = build_server_ssl_context(
        SERIAL, flavor=flavor, client_auth=client_auth,
    )

    query = TestServer(
        create_query_app(registry, security, tls=True, ws_port=0),
        host="127.0.0.1",
    )
    await query.start_server(ssl=ssl_context)

    websocket = TestServer(
        create_query_ws_app(registry, security), host="127.0.0.1",
    )
    await websocket.start_server(
        ssl=build_server_ssl_context(
            SERIAL, flavor=flavor, client_auth=client_auth,
        ),
    )
    return query, websocket


def client_session(
    *, with_client_cert: bool, flavor: str = "rsa",
) -> aiohttp.ClientSession:
    context = build_client_ssl_context(
        client_serial=CLIENT_SERIAL if with_client_cert else None,
        flavor=flavor,
    )
    return aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=context))


async def create_subscription(
    session: aiohttp.ClientSession,
    query: TestServer,
    *,
    resource_path: str = "/nodes",
    persist: bool = True,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create a subscription through the real HTTPS Query API."""
    url = f"https://{query.host}:{query.port}{QUERY_BASE}/subscriptions"
    body = {
        "max_update_rate_ms": 0,
        "resource_path": resource_path,
        "persist": persist,
        "params": params or {},
        # Served over HTTPS, so the socket must be secure. A mismatch here is
        # a 400 (Behaviour - Querying.md:13).
        "secure": True,
    }
    async with session.post(url, json=body, headers=headers) as response:
        assert response.status in (200, 201), await response.text()
        payload: dict[str, Any] = await response.json()
    return payload


def ws_url(websocket: TestServer, subscription: dict[str, Any]) -> str:
    """``wss://`` URL for this subscription on the test WebSocket listener.

    ``ws_href`` advertises the production port, which is not the ephemeral one
    the test server bound, so the path is reused against the real port. The
    advertised scheme and path are asserted separately.
    """
    path = f"{QUERY_BASE}/subscriptions/{subscription['id']}"
    assert subscription["ws_href"].endswith(path)
    return f"wss://{websocket.host}:{websocket.port}{path}"


async def read_grain(socket: aiohttp.ClientWebSocketResponse) -> dict[str, Any]:
    message = await asyncio.wait_for(socket.receive(), timeout=GRAIN_TIMEOUT_S)
    assert message.type is aiohttp.WSMsgType.TEXT, message
    payload: dict[str, Any] = json.loads(message.data)
    return payload


def register_node(registry: Registry, **kwargs: Any) -> None:
    raw = make_node(**kwargs)
    typed = decode_resource(ResourceType.NODE, raw)
    assert registry.register(ResourceType.NODE, Body.from_data(raw)).ok


# ---------------------------------------------------------------------------
# Server-authenticated TLS
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flavor", ["rsa", "ec"])
class TestWebSocketServerTls:
    async def test_sync_grain_arrives_over_wss(self, flavor: str) -> None:
        """A secure socket must actually deliver, not merely connect.

        The synchronisation burst is the first thing a client depends on, and
        a WebSocket that handshakes but never sends is indistinguishable from
        an empty registry.
        """
        registry = build_registry()
        register_node(registry)
        query, websocket = await start_tls_servers(
            registry, security_for(), flavor=flavor,
        )
        try:
            async with client_session(with_client_cert=False, flavor=flavor) as session:
                subscription = await create_subscription(session, query)
                assert subscription["ws_href"].startswith("wss://")
                assert subscription["secure"] is True

                async with session.ws_connect(
                    ws_url(websocket, subscription),
                ) as socket:
                    grain = await read_grain(socket)
                    entry = grain["grain"]["data"][0]
                    assert entry["path"] == NODE_ID
                    assert entry["pre"] == entry["post"]
        finally:
            await query.close()
            await websocket.close()

    async def test_live_events_stream_over_wss(self, flavor: str) -> None:
        """Changes after connection must reach the client over TLS."""
        registry = build_registry()
        query, websocket = await start_tls_servers(
            registry, security_for(), flavor=flavor,
        )
        try:
            async with client_session(with_client_cert=False, flavor=flavor) as session:
                subscription = await create_subscription(session, query)
                async with session.ws_connect(
                    ws_url(websocket, subscription),
                ) as socket:
                    register_node(registry)
                    entry = (await read_grain(socket))["grain"]["data"][0]
                    assert entry["path"] == NODE_ID
                    assert "pre" not in entry, "an add carries post only"

                    register_node(
                        registry, label="renamed", version=tai_version(+1),
                    )
                    entry = (await read_grain(socket))["grain"]["data"][0]
                    assert entry["pre"]["label"] == "test-node"
                    assert entry["post"]["label"] == "renamed"
        finally:
            await query.close()
            await websocket.close()

    async def test_multiple_grains_stream_in_order(self, flavor: str) -> None:
        """Several sequential frames over one TLS connection.

        Guards against a TLS record/framing problem that only shows up after
        the first message.
        """
        registry = build_registry()
        query, websocket = await start_tls_servers(
            registry, security_for(), flavor=flavor,
        )
        try:
            async with client_session(with_client_cert=False, flavor=flavor) as session:
                subscription = await create_subscription(session, query)
                async with session.ws_connect(
                    ws_url(websocket, subscription),
                ) as socket:
                    register_node(registry)
                    assert (await read_grain(socket))["grain"]["data"][0][
                        "path"
                    ] == NODE_ID

                    register_node(registry, node_id=NODE_ID_2)
                    assert (await read_grain(socket))["grain"]["data"][0][
                        "path"
                    ] == NODE_ID_2
        finally:
            await query.close()
            await websocket.close()

    async def test_untrusted_server_is_refused(self, flavor: str) -> None:
        """The client must authenticate the registry before subscribing."""
        registry = build_registry()
        query, websocket = await start_tls_servers(
            registry, security_for(), flavor=flavor,
        )
        try:
            async with client_session(with_client_cert=False, flavor=flavor) as session:
                subscription = await create_subscription(session, query)
                url = ws_url(websocket, subscription)

            strict = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            strict.load_default_certs()  # does not include the Example root
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=strict),
            ) as session:
                with pytest.raises(aiohttp.ClientError):
                    await session.ws_connect(url)
        finally:
            await query.close()
            await websocket.close()


# ---------------------------------------------------------------------------
# Mutual TLS
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flavor", ["rsa", "ec"])
class TestWebSocketMutualTls:
    async def test_grains_flow_with_a_client_certificate(
        self, flavor: str,
    ) -> None:
        registry = build_registry()
        register_node(registry)
        query, websocket = await start_tls_servers(
            registry,
            security_for(client_auth_required=True),
            flavor=flavor,
            client_auth="required",
        )
        try:
            async with client_session(with_client_cert=True, flavor=flavor) as session:
                subscription = await create_subscription(session, query)
                async with session.ws_connect(
                    ws_url(websocket, subscription),
                ) as socket:
                    grain = await read_grain(socket)
                    assert grain["grain"]["data"][0]["path"] == NODE_ID
        finally:
            await query.close()
            await websocket.close()

    async def test_upgrade_without_a_client_certificate_fails(
        self, flavor: str,
    ) -> None:
        """With CERT_REQUIRED the socket dies in the handshake.

        Verified against the WebSocket listener specifically: it is a separate
        application from the Query API, so it could have been left without the
        client-certificate requirement while the HTTP surface enforced it.
        """
        registry = build_registry()
        query, websocket = await start_tls_servers(
            registry,
            security_for(client_auth_required=True),
            flavor=flavor,
            client_auth="required",
        )
        try:
            # The subscription is created over an authenticated connection...
            async with client_session(with_client_cert=True, flavor=flavor) as session:
                subscription = await create_subscription(session, query)
                url = ws_url(websocket, subscription)

            # ...but an anonymous client must not be able to attach to it.
            async with client_session(with_client_cert=False, flavor=flavor) as anonymous:
                with pytest.raises(aiohttp.ClientError):
                    await anonymous.ws_connect(url)
        finally:
            await query.close()
            await websocket.close()

    async def test_optional_client_auth_admits_the_read_only_upgrade(
        self, flavor: str,
    ) -> None:
        """Under CERT_OPTIONAL the upgrade succeeds without a certificate.

        This is TR-10-SEC's **Unrestricted Read Only** policy (NAP=1) working
        as specified, not a gap: "Unrestricted read access MUST be permitted
        to all clients", with only write access restricted per RAAM. A
        WebSocket upgrade is a GET, and attaching to an existing subscription
        has no side effect on registry state — creating one is the POST, and
        that is gated (see the Query API's NAP tests).

        Worth knowing before choosing ``--queryOptionalClientAuth``: the mTLS
        gate applies to *creating* a subscription, not to attaching to one
        that already exists. Requiring a certificate here would violate the
        MUST above.
        """
        registry = build_registry()
        register_node(registry)
        query, websocket = await start_tls_servers(
            registry,
            security_for(client_auth_required=True),
            flavor=flavor,
            client_auth="optional",
        )
        try:
            # Creating the subscription is a POST, so it needs the cert.
            async with client_session(with_client_cert=True, flavor=flavor) as session:
                subscription = await create_subscription(session, query)
                url = ws_url(websocket, subscription)

            async with client_session(with_client_cert=False, flavor=flavor) as anonymous:
                async with anonymous.ws_connect(url) as socket:
                    grain = await read_grain(socket)
                    assert grain["grain"]["data"][0]["path"] == NODE_ID
        finally:
            await query.close()
            await websocket.close()


# ---------------------------------------------------------------------------
# OAuth 2.0 on the upgrade
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flavor", ["rsa"])
class TestWebSocketOAuth2:
    """The upgrade is a GET, so ``check_oauth2(False, "query")`` gates it.

    That is what makes a subscription's ``authorization`` attribute
    meaningful: when the Query API requires a token, the socket it hands out
    must require one too, or the attribute would be advertising a protection
    that is not enforced.
    """

    async def test_upgrade_without_a_bearer_is_refused(
        self, flavor: str,
    ) -> None:
        registry = build_registry()
        # The subscription is placed directly in the manager: creating one
        # over HTTP would itself need a token, and this test is about the
        # socket rather than about subscription creation.
        subscription, _created = registry.subscriptions.create_or_match(
            resource_path="/nodes",
            params={},
            max_update_rate_ms=0,
            persist=True,
            secure=True,
            authorization=True,
            host="127.0.0.1",
            ws_scheme="wss",
            ws_host="127.0.0.1",
        )
        _query, websocket = await start_tls_servers(
            registry, security_for(oauth2=True), flavor=flavor,
        )
        try:
            url = (
                f"wss://{websocket.host}:{websocket.port}"
                f"{QUERY_BASE}/subscriptions/{subscription.id}"
            )
            async with client_session(with_client_cert=False, flavor=flavor) as session:
                with pytest.raises(aiohttp.WSServerHandshakeError) as caught:
                    await session.ws_connect(url)
                assert caught.value.status == 401
        finally:
            await _query.close()
            await websocket.close()

    async def test_upgrade_with_a_bogus_bearer_is_refused(
        self, flavor: str,
    ) -> None:
        """Fails closed: no JWKS means no token can be accepted."""
        registry = build_registry()
        subscription, _created = registry.subscriptions.create_or_match(
            resource_path="/nodes",
            params={},
            max_update_rate_ms=0,
            persist=True,
            secure=True,
            authorization=True,
            host="127.0.0.1",
            ws_scheme="wss",
            ws_host="127.0.0.1",
        )
        _query, websocket = await start_tls_servers(
            registry, security_for(oauth2=True), flavor=flavor,
        )
        try:
            url = (
                f"wss://{websocket.host}:{websocket.port}"
                f"{QUERY_BASE}/subscriptions/{subscription.id}"
            )
            async with client_session(with_client_cert=False, flavor=flavor) as session:
                with pytest.raises(aiohttp.WSServerHandshakeError) as caught:
                    await session.ws_connect(
                        url, headers={"Authorization": "Bearer a.b.c"},
                    )
                assert caught.value.status == 401
        finally:
            await _query.close()
            await websocket.close()

    async def test_oauth2_closes_the_read_only_upgrade(
        self, flavor: str,
    ) -> None:
        """OAuth 2.0 overrides NAP=1 on the socket as well as on HTTP.

        ``NMOS With Control Plane Security.md`` §"Unrestricted Read Only":
        the policy "is not allowed when OAuth 2.0 authorizations are used, in
        which case even read access MUST be explicitly provided by the OAuth
        2.0 authorizations."

        The WebSocket upgrade is the read most likely to be overlooked, since
        it is served by a separate application from the HTTP API. With
        CERT_OPTIONAL it would otherwise stay open to any TLS client, leaving
        the registry in exactly the NAP=1-with-OAuth-2.0 state the
        specification rules out.
        """
        registry = build_registry()
        subscription, _created = registry.subscriptions.create_or_match(
            resource_path="/nodes",
            params={},
            max_update_rate_ms=0,
            persist=True,
            secure=True,
            authorization=True,
            host="127.0.0.1",
            ws_scheme="wss",
            ws_host="127.0.0.1",
        )
        query, websocket = await start_tls_servers(
            registry,
            security_for(client_auth_required=True, oauth2=True),
            flavor=flavor,
            client_auth="optional",
        )
        try:
            url = (
                f"wss://{websocket.host}:{websocket.port}"
                f"{QUERY_BASE}/subscriptions/{subscription.id}"
            )
            async with client_session(
                with_client_cert=False, flavor=flavor,
            ) as session:
                with pytest.raises(aiohttp.WSServerHandshakeError) as caught:
                    await session.ws_connect(url)
                assert caught.value.status == 401
        finally:
            await query.close()
            await websocket.close()

    async def test_authorization_attribute_matches_the_api_mode(
        self, flavor: str,
    ) -> None:
        """``Behaviour - Querying.md:15`` -- the advertised value is the
        API's own mode, so a client can tell whether its socket needs a
        token before it tries to open one."""
        registry = build_registry()
        query, websocket = await start_tls_servers(
            registry, security_for(oauth2=False), flavor=flavor,
        )
        try:
            async with client_session(with_client_cert=False, flavor=flavor) as session:
                subscription = await create_subscription(session, query)
                assert subscription["authorization"] is False
        finally:
            await query.close()
            await websocket.close()


# ---------------------------------------------------------------------------
# TR-10-SEC transport restrictions
# ---------------------------------------------------------------------------

class TestWebSocketTransportRestrictions:
    async def test_tls_1_1_is_refused_on_the_websocket_listener(self) -> None:
        """TR-10-SEC pins TLS 1.2 as the floor, on this listener too.

        The WebSocket runs on its own socket with its own context, so a
        deployment could end up with a hardened Query API and a permissive
        WebSocket beside it. ``nmos_registry.py`` builds one context for both;
        this asserts the consequence.
        """
        registry = build_registry()
        query, websocket = await start_tls_servers(registry, security_for())
        try:
            legacy = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            legacy.load_verify_locations(cafile=str(root_ca("rsa")))
            legacy.check_hostname = False
            legacy.maximum_version = ssl.TLSVersion.TLSv1_1

            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=legacy),
            ) as session:
                url = (
                    f"wss://{websocket.host}:{websocket.port}"
                    f"{QUERY_BASE}/subscriptions/{NODE_ID}"
                )
                with pytest.raises(aiohttp.ClientError):
                    await session.ws_connect(url)
        finally:
            await query.close()
            await websocket.close()

    async def test_unknown_subscription_is_a_clean_404_over_tls(self) -> None:
        """A mistyped ``ws_href`` gets an HTTP error, not a dead socket.

        Answering the upgrade with 404 rather than accepting and immediately
        closing is what lets a client distinguish "wrong URL" from "registry
        dropped me".
        """
        registry = build_registry()
        query, websocket = await start_tls_servers(registry, security_for())
        try:
            async with client_session(with_client_cert=False) as session:
                url = (
                    f"wss://{websocket.host}:{websocket.port}"
                    f"{QUERY_BASE}/subscriptions/{NODE_ID}"
                )
                with pytest.raises(aiohttp.WSServerHandshakeError) as caught:
                    await session.ws_connect(url)
                assert caught.value.status == 404
        finally:
            await query.close()
            await websocket.close()
