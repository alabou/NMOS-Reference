# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""TLS and OAuth 2.0 mode tests for both registry interfaces.

Runs real TLS servers with the pre-generated PKI, so the security modes are
exercised through an actual handshake rather than through mocked transports.

The two interfaces support deliberately different mode sets, and the tests are
organised around proving exactly that:

* **Registration** — no TLS, server-authenticated TLS, mutual TLS. Never OAuth
  2.0. ``specs/NMOS With Control Plane Security.md:105`` — "The IS-04
  Registration API MUST not require the NMOS Nodes to use OAuth 2.0
  authorizations." These map onto the Registry Access Policy values 0, 1, 2.
* **Query** — the same three, plus OAuth 2.0 over each of the TLS modes, for
  the full five-mode matrix a Node's own API supports. Scope ``query``, per
  the same document at line 439.
"""

from __future__ import annotations

import ssl

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from nmos.api.tests._tls_helpers import (
    CERTS_DIR,
    PKI_AVAILABLE,
    build_client_ssl_context,
    build_server_ssl_context,
    server_cert_names,
)
from nmos.registry import (
    InterfaceSecurity,
    Registry,
    create_query_app,
    create_registration_app,
)
from nmos.registry.handlers_query import BASE_PATH as QUERY_BASE
from nmos.registry.handlers_registration import BASE_PATH as REG_BASE
from nmos.registry.store import RegistryStore
from nmos.registry.subscriptions import SubscriptionManager
from nmos.registry.tests._fixtures import make_node

pytestmark = pytest.mark.skipif(
    not PKI_AVAILABLE,
    reason=f"pre-generated TLS PKI not present at {CERTS_DIR}",
)

SERIAL = "SNX00000"
CLIENT_SERIAL = "SNX00001"


def build_registry() -> Registry:
    registry = Registry(
        RegistryStore(), query_id="8c4d1e70-6b3a-4f52-9d81-2e7c5a0b3f14",
    )
    registry.attach_subscriptions(SubscriptionManager(registry))
    return registry


async def start_registration(
    flavor: str, *, client_auth: str, client_auth_required: bool,
) -> TestServer:
    security = InterfaceSecurity(
        client_auth_required=client_auth_required,
        oauth2=False,
        serial_number=SERIAL,
        tls_server_cert_names=server_cert_names(SERIAL),
    )
    server = TestServer(
        create_registration_app(build_registry(), security), host="127.0.0.1",
    )
    await server.start_server(
        ssl=build_server_ssl_context(SERIAL, flavor=flavor, client_auth=client_auth),
    )
    return server


async def start_query(
    flavor: str,
    *,
    client_auth: str,
    client_auth_required: bool = False,
    oauth2: bool = False,
    oauth2_keys: object = None,
) -> TestServer:
    security = InterfaceSecurity(
        client_auth_required=client_auth_required,
        oauth2=oauth2,
        serial_number=SERIAL,
        tls_server_cert_names=server_cert_names(SERIAL),
        oauth2_keys=oauth2_keys,
    )
    server = TestServer(
        create_query_app(build_registry(), security, tls=True, ws_port=8448),
        host="127.0.0.1",
    )
    await server.start_server(
        ssl=build_server_ssl_context(SERIAL, flavor=flavor, client_auth=client_auth),
    )
    return server


def client_session(
    flavor: str, *, with_client_cert: bool,
) -> aiohttp.ClientSession:
    ctx = build_client_ssl_context(
        client_serial=CLIENT_SERIAL if with_client_cert else None,
        flavor=flavor,
    )
    return aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ctx))


def post_body() -> dict[str, object]:
    return {"type": "node", "data": make_node()}


# ---------------------------------------------------------------------------
# Registration: RAP 1 -- server-authenticated TLS
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flavor", ["rsa", "ec"])
class TestRegistrationServerTls:
    """TR-10-SEC "Unrestricted Registration" over HTTPS (RAP=1).

    "An NMOS Registry configured with that policy grants registration access
    to anyone. [...] the IS-04 Registration API is protected by TLS and a
    device MUST authenticate the Registry using TLS server authentication."
    """

    async def test_registration_without_client_cert_is_accepted(
        self, flavor: str,
    ) -> None:
        server = await start_registration(
            flavor, client_auth="none", client_auth_required=False,
        )
        try:
            async with client_session(flavor, with_client_cert=False) as session:
                url = f"{server.scheme}://{server.host}:{server.port}{REG_BASE}/resource"
                async with session.post(url, json=post_body()) as response:
                    assert response.status == 201
        finally:
            await server.close()

    async def test_server_identity_is_verified(self, flavor: str) -> None:
        """A client that does not trust our CA must refuse the connection.

        This is the half of RAP=1 that protects the Node: it authenticates the
        registry before handing over its resource records.
        """
        server = await start_registration(
            flavor, client_auth="none", client_auth_required=False,
        )
        try:
            strict = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            strict.load_default_certs()  # does not include the Example root
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=strict),
            ) as session:
                url = f"{server.scheme}://{server.host}:{server.port}{REG_BASE}"
                with pytest.raises(aiohttp.ClientConnectorCertificateError):
                    await session.get(url)
        finally:
            await server.close()


# ---------------------------------------------------------------------------
# Registration: RAP 2 -- mutual TLS
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flavor", ["rsa", "ec"])
class TestRegistrationMutualTls:
    """TR-10-SEC "Restricted Registration" (RAP=2).

    "An NMOS Registry configured with that policy grants registration access
    to those presenting a client certificate authorized for registration. A
    device and a Registry MUST authenticate each other using TLS mutual
    authentication."
    """

    async def test_valid_client_cert_is_accepted(self, flavor: str) -> None:
        server = await start_registration(
            flavor, client_auth="required", client_auth_required=True,
        )
        try:
            async with client_session(flavor, with_client_cert=True) as session:
                url = f"{server.scheme}://{server.host}:{server.port}{REG_BASE}/resource"
                async with session.post(url, json=post_body()) as response:
                    assert response.status == 201
        finally:
            await server.close()

    async def test_missing_client_cert_fails_the_handshake(
        self, flavor: str,
    ) -> None:
        """With CERT_REQUIRED, rejection happens in OpenSSL before any HTTP."""
        server = await start_registration(
            flavor, client_auth="required", client_auth_required=True,
        )
        try:
            async with client_session(flavor, with_client_cert=False) as session:
                url = f"{server.scheme}://{server.host}:{server.port}{REG_BASE}/resource"
                with pytest.raises(aiohttp.ClientError):
                    await session.post(url, json=post_body())
        finally:
            await server.close()

    async def test_application_layer_enforces_when_tls_is_permissive(
        self, flavor: str,
    ) -> None:
        """With CERT_OPTIONAL the handshake succeeds, so the application must
        reject the write itself.

        This is the ``--registrationOptionalClientAuth`` configuration, and it
        is where ``client_auth_middleware`` earns its place: the TLS layer has
        let an anonymous client through and only the middleware stands between
        it and a registration.
        """
        server = await start_registration(
            flavor, client_auth="optional", client_auth_required=True,
        )
        try:
            async with client_session(flavor, with_client_cert=False) as session:
                url = f"{server.scheme}://{server.host}:{server.port}{REG_BASE}/resource"
                async with session.post(url, json=post_body()) as response:
                    assert response.status == 401
        finally:
            await server.close()


# ---------------------------------------------------------------------------
# Registration must never require OAuth 2.0
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flavor", ["rsa"])
class TestRegistrationNeverRequiresOAuth2:
    """The normative rule of ``NMOS With Control Plane Security.md:105``.

    Asserted directly rather than left implicit, because it is the one place
    this registry deliberately diverges from nmos-cpp — which does support
    BCP-003-02 authorization on its Registration API — and a future refactor
    that "helpfully" wrapped these routes in ``check_oauth2`` would be a
    specification violation rather than a hardening improvement.
    """

    async def test_no_token_required_even_with_oauth2_configured(
        self, flavor: str,
    ) -> None:
        # Deliberately construct the Registration interface with oauth2=True
        # and NO keys. If any route consulted OAuth 2.0, validation would fail
        # closed and answer 401. It must still answer 201.
        security = InterfaceSecurity(
            client_auth_required=False,
            oauth2=True,
            serial_number=SERIAL,
            tls_server_cert_names=server_cert_names(SERIAL),
            oauth2_keys=None,
        )
        server = TestServer(
            create_registration_app(build_registry(), security),
            host="127.0.0.1",
        )
        await server.start_server(
            ssl=build_server_ssl_context(SERIAL, flavor=flavor, client_auth="none"),
        )
        try:
            async with client_session(flavor, with_client_cert=False) as session:
                url = f"{server.scheme}://{server.host}:{server.port}{REG_BASE}/resource"
                async with session.post(url, json=post_body()) as response:
                    assert response.status == 201, (
                        "the Registration API must not require OAuth 2.0 "
                        "(TR-10-SEC:105)"
                    )
        finally:
            await server.close()


# ---------------------------------------------------------------------------
# Query: TLS and mTLS
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flavor", ["rsa", "ec"])
class TestQueryTls:
    async def test_reads_over_server_tls(self, flavor: str) -> None:
        server = await start_query(flavor, client_auth="none")
        try:
            async with client_session(flavor, with_client_cert=False) as session:
                url = f"{server.scheme}://{server.host}:{server.port}{QUERY_BASE}/nodes"
                async with session.get(url) as response:
                    assert response.status == 200
        finally:
            await server.close()

    async def test_reads_allowed_without_client_cert_under_optional_mtls(
        self, flavor: str,
    ) -> None:
        """Read-only verbs pass through ``client_auth_middleware``.

        The Query API is overwhelmingly reads, so this is what makes
        ``--queryOptionalClientAuth`` useful: browsers and diagnostic tools
        can read while writes stay locked to certificate holders.
        """
        server = await start_query(
            flavor, client_auth="optional", client_auth_required=True,
        )
        try:
            async with client_session(flavor, with_client_cert=False) as session:
                url = f"{server.scheme}://{server.host}:{server.port}{QUERY_BASE}/nodes"
                async with session.get(url) as response:
                    assert response.status == 200
        finally:
            await server.close()

    async def test_subscription_write_requires_client_cert(
        self, flavor: str,
    ) -> None:
        """Creating a subscription is a state-changing verb, so mTLS applies.

        It allocates server-side state and a WebSocket, which is why it is a
        write even though the Query API is nominally read-only.
        """
        server = await start_query(
            flavor, client_auth="optional", client_auth_required=True,
        )
        try:
            async with client_session(flavor, with_client_cert=False) as session:
                url = (
                    f"{server.scheme}://{server.host}:{server.port}"
                    f"{QUERY_BASE}/subscriptions"
                )
                body = {
                    "max_update_rate_ms": 100,
                    "resource_path": "/senders",
                    "persist": False,
                    "params": {},
                }
                async with session.post(url, json=body) as response:
                    assert response.status == 401
        finally:
            await server.close()

    async def test_subscription_write_succeeds_with_client_cert(
        self, flavor: str,
    ) -> None:
        server = await start_query(
            flavor, client_auth="optional", client_auth_required=True,
        )
        try:
            async with client_session(flavor, with_client_cert=True) as session:
                url = (
                    f"{server.scheme}://{server.host}:{server.port}"
                    f"{QUERY_BASE}/subscriptions"
                )
                body = {
                    "max_update_rate_ms": 100,
                    "resource_path": "/senders",
                    "persist": False,
                    "params": {},
                    "secure": True,
                }
                async with session.post(url, json=body) as response:
                    assert response.status == 201
                    payload = await response.json()
                    # Served over HTTPS, so the socket must be wss.
                    assert payload["ws_href"].startswith("wss://")
                    assert payload["secure"] is True
        finally:
            await server.close()


# ---------------------------------------------------------------------------
# Query: OAuth 2.0
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flavor", ["rsa"])
class TestNodeAccessPolicy:
    """The Query API against TR-10-SEC's Node Access Policy modes.

    §"Node Access Policy" defines three, and the Query API is configured
    against the same matrix a Node's own API is:

    * **Unrestricted Read Only (NAP=1)** -- "Unrestricted read access MUST be
      permitted to all clients", writes per RAAM. Selected by
      ``--queryOptionalClientAuth`` with OAuth 2.0 off.
    * **Restricted Read Write (NAP=2)** -- both reads and writes per RAAM.
      The default, and also what OAuth 2.0 forces.

    The rule these tests exist for is the constraint attached to NAP=1:
    "This mode of operation is not allowed when OAuth 2.0 authorizations are
    used, in which case even read access MUST be explicitly provided by the
    OAuth 2.0 authorizations." Enabling OAuth 2.0 must therefore collapse
    NAP=1 into NAP=2 rather than leaving reads open.
    """

    async def test_nap1_permits_unauthenticated_reads(self, flavor: str) -> None:
        """NAP=1: reads open to all clients, no certificate required."""
        server = await start_query(
            flavor, client_auth="optional", client_auth_required=True,
        )
        try:
            async with client_session(flavor, with_client_cert=False) as session:
                url = f"{server.scheme}://{server.host}:{server.port}{QUERY_BASE}/nodes"
                async with session.get(url) as response:
                    assert response.status == 200
        finally:
            await server.close()

    async def test_nap1_restricts_writes(self, flavor: str) -> None:
        """NAP=1: writes still enforced per RAAM (here, mutual TLS)."""
        server = await start_query(
            flavor, client_auth="optional", client_auth_required=True,
        )
        try:
            async with client_session(flavor, with_client_cert=False) as session:
                url = (
                    f"{server.scheme}://{server.host}:{server.port}"
                    f"{QUERY_BASE}/subscriptions"
                )
                body = {
                    "max_update_rate_ms": 100,
                    "resource_path": "/senders",
                    "persist": False,
                    "params": {},
                    "secure": True,
                }
                async with session.post(url, json=body) as response:
                    assert response.status == 401
        finally:
            await server.close()

    async def test_oauth2_forbids_nap1_reads_staying_open(
        self, flavor: str,
    ) -> None:
        """The NAP=1 prohibition, asserted directly.

        Optional client auth would ordinarily leave reads unrestricted. With
        OAuth 2.0 enabled that combination is forbidden, so an unauthenticated
        read MUST be refused: the deployment is NAP=2 whether or not the
        operator also passed ``--queryOptionalClientAuth``.

        Without this the two settings could silently combine into the one
        configuration the specification rules out, and every other test would
        still pass.
        """
        server = await start_query(
            flavor,
            client_auth="optional",
            client_auth_required=True,
            oauth2=True,
        )
        try:
            async with client_session(flavor, with_client_cert=False) as session:
                url = f"{server.scheme}://{server.host}:{server.port}{QUERY_BASE}/nodes"
                async with session.get(url) as response:
                    assert response.status == 401, (
                        "read access must be provided only by OAuth 2.0 when "
                        "authorizations are in use (TR-10-SEC NAP=1 is not "
                        "allowed with OAuth 2.0)"
                    )
        finally:
            await server.close()

    async def test_nap2_restricts_reads_at_the_handshake(
        self, flavor: str,
    ) -> None:
        """NAP=2 without OAuth 2.0: RAAM is mutual TLS, so reads need a cert.

        Enforced by the TLS layer rather than the application, which is why
        an anonymous client never reaches a handler at all.
        """
        server = await start_query(
            flavor, client_auth="required", client_auth_required=True,
        )
        try:
            async with client_session(flavor, with_client_cert=False) as session:
                url = f"{server.scheme}://{server.host}:{server.port}{QUERY_BASE}/nodes"
                with pytest.raises(aiohttp.ClientError):
                    await session.get(url)
        finally:
            await server.close()


@pytest.mark.parametrize("flavor", ["rsa"])
class TestQueryOAuth2:
    """OAuth 2.0 on the Query API, over TLS and over mTLS.

    These assert the gate engages and fails closed. Token-content validation
    (audience modes, scope claims, expiry) is exercised in depth by
    ``nmos/api/tests/test_oauth2_e2e.py`` against the same shared
    ``check_oauth2`` implementation, so it is not duplicated here.
    """

    async def test_missing_bearer_is_401(self, flavor: str) -> None:
        server = await start_query(flavor, client_auth="none", oauth2=True)
        try:
            async with client_session(flavor, with_client_cert=False) as session:
                url = f"{server.scheme}://{server.host}:{server.port}{QUERY_BASE}/nodes"
                async with session.get(url) as response:
                    assert response.status == 401
                    assert "Bearer" in response.headers.get("WWW-Authenticate", "")
        finally:
            await server.close()

    async def test_fails_closed_without_keys(self, flavor: str) -> None:
        """No JWKS yet means every authenticated request is refused.

        The JWKS cache starts empty and only populates after its first
        successful fetch; until then the correct answer is 401, not "allow
        because we cannot check".
        """
        server = await start_query(
            flavor, client_auth="none", oauth2=True, oauth2_keys=None,
        )
        try:
            async with client_session(flavor, with_client_cert=False) as session:
                url = f"{server.scheme}://{server.host}:{server.port}{QUERY_BASE}/nodes"
                headers = {"Authorization": "Bearer not-a-real-token"}
                async with session.get(url, headers=headers) as response:
                    assert response.status == 401
        finally:
            await server.close()

    async def test_garbage_token_is_401(self, flavor: str) -> None:
        server = await start_query(flavor, client_auth="none", oauth2=True)
        try:
            async with client_session(flavor, with_client_cert=False) as session:
                url = f"{server.scheme}://{server.host}:{server.port}{QUERY_BASE}/nodes"
                headers = {"Authorization": "Bearer a.b.c"}
                async with session.get(url, headers=headers) as response:
                    assert response.status == 401
        finally:
            await server.close()

    async def test_oauth2_plus_mtls(self, flavor: str) -> None:
        """The fifth mode: both gates active, both must pass.

        A valid client certificate does not substitute for a token.
        """
        server = await start_query(
            flavor,
            client_auth="required",
            client_auth_required=True,
            oauth2=True,
        )
        try:
            async with client_session(flavor, with_client_cert=True) as session:
                url = f"{server.scheme}://{server.host}:{server.port}{QUERY_BASE}/nodes"
                async with session.get(url) as response:
                    assert response.status == 401
        finally:
            await server.close()

    async def test_subscription_authorization_attribute_tracks_the_mode(
        self, flavor: str,
    ) -> None:
        """``Behaviour - Querying.md:15`` -- a subscription's ``authorization``
        must agree with the API's mode, and a mismatch is a 400.

        With OAuth 2.0 on, an unauthenticated request cannot get far enough to
        test the negotiation, so this checks the complementary case: with
        OAuth 2.0 off, requesting ``authorization: true`` is refused.
        """
        server = await start_query(flavor, client_auth="none", oauth2=False)
        try:
            async with client_session(flavor, with_client_cert=False) as session:
                url = (
                    f"{server.scheme}://{server.host}:{server.port}"
                    f"{QUERY_BASE}/subscriptions"
                )
                body = {
                    "max_update_rate_ms": 100,
                    "resource_path": "/senders",
                    "persist": False,
                    "params": {},
                    "secure": True,
                    "authorization": True,
                }
                async with session.post(url, json=body) as response:
                    assert response.status == 400
        finally:
            await server.close()
