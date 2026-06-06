# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""TLS + Node Reservation tests (R1, R2, R4, R5).

OAuth2 is DISABLED in this class (``node.oauth2=False``). The layering
under test is:

  * **R1/R2** — acquire endpoint works end-to-end over HTTPS. State-
    changing NMOS endpoints return 401 + ``WWW-Authenticate`` when the
    configured auth (session token / mTLS client cert) is absent.
  * **R4** — with session token: state-changing requests also require
    mTLS if ``client_auth_required=True``. No mTLS → 401.
  * **R5** — read-only endpoints remain unrestricted even without a
    session or client cert ("read-only granted without client
    certificate" rule).

The enforcement for state-changing requests lives on the connection
PATCH handler via ``check_exclusive_authorization`` (see
[nmos/api/middleware.py:162](../middleware.py) and
[nmos/api/handlers_connection.py:437](../handlers_connection.py)).

Every test is parametrised across RSA and EC cert flavours.
"""

from __future__ import annotations

from typing import Any

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from nmos.api import create_app
from nmos.crypto import ExclusiveSession
from nmos.node import Node

from nmos.api.tests._tls_helpers import (
    CERTS_DIR,
    PKI_AVAILABLE,
    build_client_ssl_context,
    build_server_ssl_context,
)


pytestmark = pytest.mark.skipif(
    not PKI_AVAILABLE,
    reason=f"pre-generated TLS PKI not present at {CERTS_DIR}",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SERIAL = "SNX00000"
ACQUIRE_URL = "/x-manufacturer/exclusive/v1.0/acquire"
RELEASE_URL = "/x-manufacturer/exclusive/v1.0/release"
# Any UUID shape — the sender doesn't exist, so a handler that runs returns
# 404 (auth passed). A 401 means the auth layer rejected the request.
CONNECTION_PATCH_URL = (
    "/x-nmos/connection/v1.1/single/senders/"
    "00000000-0000-0000-0000-000000000000/staged"
)


def _make_node(*, client_auth_required: bool) -> Node:
    """Build a reservation-enabled node (OAuth2 off)."""
    node = Node()
    node.init(serial_number=SERIAL)
    node.exclusive_session = ExclusiveSession()
    node.privacy_enabled = True
    node.client_auth_required = client_auth_required
    return node


async def _start_tls_server(
    flavor: str,
    *,
    client_auth: str,
    client_auth_required: bool,
) -> TestServer:
    node = _make_node(client_auth_required=client_auth_required)
    app = create_app(node)
    server = TestServer(app, host="127.0.0.1")
    ssl_ctx = build_server_ssl_context(SERIAL, flavor=flavor, client_auth=client_auth)
    await server.start_server(ssl=ssl_ctx)
    return server


def _make_session(
    flavor: str,
    *,
    client_serial: str | None = None,
) -> aiohttp.ClientSession:
    client_ctx = build_client_ssl_context(client_serial=client_serial, flavor=flavor)
    connector = aiohttp.TCPConnector(ssl=client_ctx)
    return aiohttp.ClientSession(connector=connector)


# ---------------------------------------------------------------------------
# Combo #3 — TLS + NodeReservation (no OAuth2, no mTLS)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flavor", ["rsa", "ec"])
class TestTlsReservationNoMtls:
    """Plain HTTPS + Reservation, OAuth2 disabled, mTLS disabled.

    Server auth only (``client_auth='none'``), ``client_auth_required=
    False``. The only protection for state-changing requests is the
    exclusive-session token (R4).
    """

    @pytest.mark.asyncio
    async def test_acquire_over_https_ok(self, flavor: str) -> None:
        server = await _start_tls_server(
            flavor, client_auth="none", client_auth_required=False,
        )
        try:
            async with _make_session(flavor) as session:
                url = f"https://127.0.0.1:{server.port}{ACQUIRE_URL}"
                async with session.post(url, json={
                    "owner": "ctrl-1",
                    "exclusive_key": "0123456789abcdef0123456789abcdef",
                }) as resp:
                    assert resp.status == 200
                    assert isinstance(await resp.json(), str)
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_state_changing_with_session_ok(self, flavor: str) -> None:
        server = await _start_tls_server(
            flavor, client_auth="none", client_auth_required=False,
        )
        try:
            async with _make_session(flavor) as session:
                base = f"https://127.0.0.1:{server.port}"
                async with session.post(f"{base}{ACQUIRE_URL}", json={
                    "owner": "ctrl-1",
                    "exclusive_key": "0123456789abcdef0123456789abcdef",
                }) as resp:
                    token = await resp.json()
                async with session.patch(
                    f"{base}{CONNECTION_PATCH_URL}",
                    json={},
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp:
                    # Auth passes (404/400 from handler, not 401).
                    assert resp.status != 401
                    assert resp.status in (400, 404)
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_state_changing_without_session_when_active_rejected(
        self, flavor: str,
    ) -> None:
        # A session is active → state-changing without the bearer → 401.
        server = await _start_tls_server(
            flavor, client_auth="none", client_auth_required=False,
        )
        try:
            async with _make_session(flavor) as session:
                base = f"https://127.0.0.1:{server.port}"
                async with session.post(f"{base}{ACQUIRE_URL}", json={
                    "owner": "ctrl-1",
                    "exclusive_key": "0123456789abcdef0123456789abcdef",
                }) as resp:
                    assert resp.status == 200

                async with session.patch(
                    f"{base}{CONNECTION_PATCH_URL}",
                    json={},
                ) as resp:
                    assert resp.status == 401
                    assert "WWW-Authenticate" in resp.headers
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_read_only_always_allowed(self, flavor: str) -> None:
        # R5: no session, no mTLS — GETs pass unconditionally.
        server = await _start_tls_server(
            flavor, client_auth="none", client_auth_required=False,
        )
        try:
            async with _make_session(flavor) as session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/node/v1.3/self"
                async with session.get(url) as resp:
                    assert resp.status == 200
        finally:
            await server.close()


# ---------------------------------------------------------------------------
# Combo #7 — mTLS (CERT_REQUIRED) + NodeReservation (no OAuth2)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flavor", ["rsa", "ec"])
class TestTlsReservationMtlsRequired:
    """mTLS CERT_REQUIRED + Reservation, OAuth2 disabled.

    Every TCP client MUST present a valid cert at handshake time;
    cert-less connections fail before HTTP. State-changing requests
    then require the exclusive-session token; read-only requests only
    need the successful TLS handshake.
    """

    @pytest.mark.asyncio
    async def test_acquire_ok(self, flavor: str) -> None:
        server = await _start_tls_server(
            flavor, client_auth="required", client_auth_required=True,
        )
        try:
            async with _make_session(flavor, client_serial=SERIAL) as session:
                url = f"https://127.0.0.1:{server.port}{ACQUIRE_URL}"
                async with session.post(url, json={
                    "owner": "ctrl-1",
                    "exclusive_key": "0123456789abcdef0123456789abcdef",
                }) as resp:
                    assert resp.status == 200
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_state_changing_with_session_and_cert_ok(
        self, flavor: str,
    ) -> None:
        server = await _start_tls_server(
            flavor, client_auth="required", client_auth_required=True,
        )
        try:
            async with _make_session(flavor, client_serial=SERIAL) as session:
                base = f"https://127.0.0.1:{server.port}"
                async with session.post(f"{base}{ACQUIRE_URL}", json={
                    "owner": "ctrl-1",
                    "exclusive_key": "0123456789abcdef0123456789abcdef",
                }) as resp:
                    token = await resp.json()
                async with session.patch(
                    f"{base}{CONNECTION_PATCH_URL}",
                    json={},
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp:
                    assert resp.status != 401
                    assert resp.status in (400, 404)
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_read_only_with_cert_ok(self, flavor: str) -> None:
        server = await _start_tls_server(
            flavor, client_auth="required", client_auth_required=True,
        )
        try:
            async with _make_session(flavor, client_serial=SERIAL) as session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/node/v1.3/self"
                async with session.get(url) as resp:
                    assert resp.status == 200
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_connection_without_cert_handshake_fails(
        self, flavor: str,
    ) -> None:
        # With CERT_REQUIRED, a cert-less client can't complete the TLS
        # handshake — the connection attempt raises, which aiohttp wraps
        # as a ClientConnectionError.
        server = await _start_tls_server(
            flavor, client_auth="required", client_auth_required=True,
        )
        try:
            async with _make_session(flavor) as session:  # no client cert
                url = f"https://127.0.0.1:{server.port}{ACQUIRE_URL}"
                with pytest.raises(aiohttp.ClientConnectionError):
                    async with session.post(url, json={
                        "owner": "x",
                        "exclusive_key": "0" * 32,
                    }):
                        pass
        finally:
            await server.close()


# ---------------------------------------------------------------------------
# Combo #9c — mTLS (CERT_OPTIONAL) + NodeReservation (no OAuth2)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flavor", ["rsa", "ec"])
class TestTlsReservationMtlsOptional:
    """mTLS CERT_OPTIONAL + Reservation, OAuth2 disabled.

    Server requests but doesn't require a client cert. Read-only
    requests from cert-less clients succeed (R5); state-changing
    requests from cert-less clients are rejected by the global
    client_auth_middleware when ``client_auth_required=True``.
    """

    @pytest.mark.asyncio
    async def test_acquire_without_mtls_rejected_with_www_authenticate(
        self, flavor: str,
    ) -> None:
        # R2 at the acquire endpoint: client_auth_required=True, no client
        # cert → the global client_auth_middleware rejects with 401 +
        # WWW-Authenticate BEFORE the acquire handler runs.
        server = await _start_tls_server(
            flavor, client_auth="optional", client_auth_required=True,
        )
        try:
            async with _make_session(flavor) as session:  # no client cert
                url = f"https://127.0.0.1:{server.port}{ACQUIRE_URL}"
                async with session.post(url, json={
                    "owner": "ctrl-1",
                    "exclusive_key": "0123456789abcdef0123456789abcdef",
                }) as resp:
                    assert resp.status == 401
                    assert "WWW-Authenticate" in resp.headers
                    assert "nmos-mtls" in resp.headers["WWW-Authenticate"]
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_acquire_over_mtls_ok(self, flavor: str) -> None:
        # R1: acquire works end-to-end over HTTPS with an mTLS client.
        server = await _start_tls_server(
            flavor, client_auth="optional", client_auth_required=True,
        )
        try:
            async with _make_session(flavor, client_serial=SERIAL) as session:
                url = f"https://127.0.0.1:{server.port}{ACQUIRE_URL}"
                async with session.post(url, json={
                    "owner": "ctrl-1",
                    "exclusive_key": "0123456789abcdef0123456789abcdef",
                }) as resp:
                    assert resp.status == 200
                    token = await resp.json()
                    assert isinstance(token, str) and len(token) > 0
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_connection_patch_no_session_no_mtls_rejected_with_www_authenticate(
        self, flavor: str,
    ) -> None:
        # R2: no session token, no client cert → 401 with WWW-Authenticate.
        server = await _start_tls_server(
            flavor, client_auth="optional", client_auth_required=True,
        )
        try:
            async with _make_session(flavor) as session:  # no client cert
                url = f"https://127.0.0.1:{server.port}{CONNECTION_PATCH_URL}"
                async with session.patch(url, json={}) as resp:
                    assert resp.status == 401
                    assert "WWW-Authenticate" in resp.headers
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_connection_patch_with_session_and_mtls_reaches_handler(
        self, flavor: str,
    ) -> None:
        # R4: session held + mTLS client cert → auth passes → handler runs.
        # Sender doesn't exist → 404 from the handler; not a 401 from auth.
        server = await _start_tls_server(
            flavor, client_auth="optional", client_auth_required=True,
        )
        try:
            async with _make_session(flavor, client_serial=SERIAL) as session:
                # Acquire a session first.
                acquire_url = f"https://127.0.0.1:{server.port}{ACQUIRE_URL}"
                async with session.post(acquire_url, json={
                    "owner": "ctrl-1",
                    "exclusive_key": "0123456789abcdef0123456789abcdef",
                }) as resp:
                    assert resp.status == 200
                    token = await resp.json()

                # PATCH with session bearer + client cert.
                patch_url = f"https://127.0.0.1:{server.port}{CONNECTION_PATCH_URL}"
                headers = {"Authorization": f"Bearer {token}"}
                async with session.patch(
                    patch_url, json={}, headers=headers,
                ) as resp:
                    # 401 would mean the auth layer rejected → test fails.
                    # 404 (sender missing) or 400 (bad body) means auth passed.
                    assert resp.status != 401
                    assert resp.status in (400, 404)
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_connection_patch_with_session_but_no_mtls_rejected(
        self, flavor: str,
    ) -> None:
        # R4: session held but no client cert → middleware's
        # ``client_auth_required and not _client_authenticated`` guard
        # rejects with 401.
        server = await _start_tls_server(
            flavor, client_auth="optional", client_auth_required=True,
        )
        try:
            # Acquire using a client WITH a cert (so the session exists).
            async with _make_session(flavor, client_serial=SERIAL) as acq_session:
                acquire_url = f"https://127.0.0.1:{server.port}{ACQUIRE_URL}"
                async with acq_session.post(acquire_url, json={
                    "owner": "ctrl-1",
                    "exclusive_key": "0123456789abcdef0123456789abcdef",
                }) as resp:
                    assert resp.status == 200
                    token = await resp.json()

            # Re-connect WITHOUT a client cert and attempt the PATCH.
            async with _make_session(flavor) as no_cert_session:
                patch_url = f"https://127.0.0.1:{server.port}{CONNECTION_PATCH_URL}"
                headers = {"Authorization": f"Bearer {token}"}
                async with no_cert_session.patch(
                    patch_url, json={}, headers=headers,
                ) as resp:
                    assert resp.status == 401
                    assert "WWW-Authenticate" in resp.headers
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_read_only_allowed_without_session_or_mtls(
        self, flavor: str,
    ) -> None:
        # R5: read-only endpoints stay accessible without session or cert,
        # even when client_auth_required=True. Only state-changing verbs
        # gate on mTLS.
        server = await _start_tls_server(
            flavor, client_auth="optional", client_auth_required=True,
        )
        try:
            async with _make_session(flavor) as session:  # no client cert
                url = f"https://127.0.0.1:{server.port}/x-nmos/node/v1.3/self"
                async with session.get(url) as resp:
                    assert resp.status == 200
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_release_over_mtls_ok(self, flavor: str) -> None:
        # R1 + lifecycle: release works over mTLS with the session bearer.
        server = await _start_tls_server(
            flavor, client_auth="optional", client_auth_required=True,
        )
        try:
            async with _make_session(flavor, client_serial=SERIAL) as session:
                acquire_url = f"https://127.0.0.1:{server.port}{ACQUIRE_URL}"
                async with session.post(acquire_url, json={
                    "owner": "ctrl-1",
                    "exclusive_key": "0123456789abcdef0123456789abcdef",
                }) as resp:
                    assert resp.status == 200
                    token = await resp.json()

                release_url = f"https://127.0.0.1:{server.port}{RELEASE_URL}"
                async with session.post(
                    release_url,
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp:
                    assert resp.status == 200
        finally:
            await server.close()
