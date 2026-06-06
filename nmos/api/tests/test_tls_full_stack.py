# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""TLS + OAuth2 + Reservation full-stack tests (R3).

Exercises the three-layer authorization chain when all three mechanisms
are enabled together:

  OAuth2 Bearer (Authorization header)
    ↓
  Exclusive session token (PEP-Exclusive-Authorization header)
    ↓
  mTLS client cert (TLS handshake)

Spec order (Node Reservation.md:41-45):
  * Read-only requests are authorized by OAuth2 only.
  * State-changing requests are authorized by OAuth2 → session token →
    mTLS (if enabled). The FIRST layer's failure MUST return without
    evaluating subsequent layers.

Enforcement points in the Python implementation:
  * OAuth2: [middleware.check_oauth2](../middleware.py:77) decorator —
    runs before the handler.
  * Session + mTLS: [middleware.check_exclusive_authorization](../middleware.py:162)
    called from inside each state-changing handler (e.g.
    [handlers_connection.py:437](../handlers_connection.py)).

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
from nmos.oauth2.tests._mock_as import MockAuthorizationServer

from nmos.api.tests._tls_helpers import (
    CERTS_DIR,
    PKI_AVAILABLE,
    build_client_ssl_context,
    build_server_ssl_context,
    client_cert_name,
    server_cert_names,
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
CONNECTION_PATCH_URL = (
    "/x-nmos/connection/v1.1/single/senders/"
    "00000000-0000-0000-0000-000000000000/staged"
)
NODE_SELF_URL = "/x-nmos/node/v1.3/self"


def _make_full_stack_node(mock_as: MockAuthorizationServer) -> Node:
    """Full-stack node: OAuth2 + exclusive session + mTLS gating."""
    node = Node()
    node.init(serial_number=SERIAL)
    node.oauth2 = True
    node.exclusive_session = ExclusiveSession()
    node.privacy_enabled = True
    node.client_auth_required = True
    node.set_oauth2_public_keys(mock_as.jwks())
    node.tls_server_cert_names = server_cert_names(SERIAL)
    node.use_serial_number_in_aud = True
    return node


async def _start_full_stack_server(
    flavor: str,
    mock_as: MockAuthorizationServer,
    *,
    client_auth: str = "required",
    client_auth_required: bool = True,
) -> TestServer:
    # Default: CERT_REQUIRED + mTLS-gate-on so every request carries a
    # client cert the middleware can bind to ``client_id``. Callers can
    # pass ``client_auth='none'`` / ``'optional'`` to exercise the
    # TLS+OAuth2+Reservation combinations that don't use mTLS, or
    # exercise the CERT_OPTIONAL verb-gate for "read-only without cert".
    node = _make_full_stack_node(mock_as)
    node.client_auth_required = client_auth_required
    app = create_app(node)
    server = TestServer(app, host="127.0.0.1")
    ssl_ctx = build_server_ssl_context(SERIAL, flavor=flavor, client_auth=client_auth)
    await server.start_server(ssl=ssl_ctx)
    return server


def _make_session(flavor: str) -> aiohttp.ClientSession:
    client_ctx = build_client_ssl_context(client_serial=SERIAL, flavor=flavor)
    connector = aiohttp.TCPConnector(ssl=client_ctx)
    return aiohttp.ClientSession(connector=connector)


def _make_session_no_cert(flavor: str) -> aiohttp.ClientSession:
    client_ctx = build_client_ssl_context(client_serial=None, flavor=flavor)
    connector = aiohttp.TCPConnector(ssl=client_ctx)
    return aiohttp.ClientSession(connector=connector)


def _write_token(
    mock_as: MockAuthorizationServer, *, scopes: list[str],
) -> str:
    """OAuth2 token with write access to the given scopes, client_id
    bound to the SNX00000 client cert CN, and a wildcard aud."""
    return mock_as.issue_token_for_node(
        SERIAL,
        scopes,
        client_id=client_cert_name(SERIAL),
        aud=["*"],
        read_write_apis={s: {"read": ["*"], "write": ["*"]} for s in scopes},
    )


def _read_token(
    mock_as: MockAuthorizationServer, *, scopes: list[str],
) -> str:
    """Scope-only OAuth2 token (grants implicit read), client_id bound."""
    return mock_as.issue_token_for_node(
        SERIAL,
        scopes,
        client_id=client_cert_name(SERIAL),
        aud=["*"],
    )


async def _acquire(
    session: aiohttp.ClientSession, base_url: str, token: str,
) -> str:
    """Acquire an exclusive session using the given OAuth2 token.
    Returns the session bearer."""
    async with session.post(
        f"{base_url}{ACQUIRE_URL}",
        json={
            "owner": "ctrl-1",
            "exclusive_key": "0123456789abcdef0123456789abcdef",
        },
        headers={"Authorization": f"Bearer {token}"},
    ) as resp:
        assert resp.status == 200, f"acquire failed: {resp.status}"
        return await resp.json()


# ---------------------------------------------------------------------------
# Combo #8 — mTLS CERT_REQUIRED + OAuth2 + NodeReservation (full stack)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flavor", ["rsa", "ec"])
class TestTlsOAuth2ReservationMtlsRequired:
    """All three authorization mechanisms in one stack, mTLS required."""

    @pytest.mark.asyncio
    async def test_full_stack_state_changing_all_three_valid(
        self, flavor: str,
    ) -> None:
        # OAuth2 valid (manufacturer + connection write scopes), session
        # valid, mTLS valid → handler runs. Sender UUID doesn't exist →
        # 404, which proves the auth chain passed completely.
        mock_as = MockAuthorizationServer("RS256")
        server = await _start_full_stack_server(flavor, mock_as)
        try:
            base = f"https://127.0.0.1:{server.port}"
            oauth_token = _write_token(mock_as, scopes=["manufacturer", "connection"])
            async with _make_session(flavor) as session:
                session_token = await _acquire(session, base, oauth_token)
                async with session.patch(
                    f"{base}{CONNECTION_PATCH_URL}",
                    json={},
                    headers={
                        "Authorization": f"Bearer {oauth_token}",
                        "PEP-Exclusive-Authorization": f"Bearer {session_token}",
                    },
                ) as resp:
                    # 404 (sender missing) or 400 (bad body) both mean the
                    # full auth chain passed. 401/403 would indicate a
                    # rejection from one of the three layers.
                    assert resp.status != 401
                    assert resp.status != 403
                    assert resp.status in (400, 404)
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_full_stack_oauth2_fails_short_circuits_before_session(
        self, flavor: str,
    ) -> None:
        # R3 ordering: OAuth2 is evaluated first. Tampered OAuth2 → 401
        # from the decorator; session is never consulted even though it
        # would be valid.
        mock_as = MockAuthorizationServer("RS256")
        server = await _start_full_stack_server(flavor, mock_as)
        try:
            base = f"https://127.0.0.1:{server.port}"
            oauth_token = _write_token(mock_as, scopes=["manufacturer", "connection"])
            async with _make_session(flavor) as session:
                # First, legitimately acquire a session.
                session_token = await _acquire(session, base, oauth_token)

                # Tamper the signature of the OAuth2 token only.
                parts = oauth_token.split(".")
                bad_token = f"{parts[0]}.{parts[1]}.AAAAAAAAAAAAAAAAAAAAAA"

                async with session.patch(
                    f"{base}{CONNECTION_PATCH_URL}",
                    json={},
                    headers={
                        "Authorization": f"Bearer {bad_token}",
                        "PEP-Exclusive-Authorization": f"Bearer {session_token}",
                    },
                ) as resp:
                    assert resp.status == 401
                    # The realm ID identifies the layer that rejected.
                    # nmos-oauth2 = OAuth2 layer; nmos-exclusive = session layer.
                    # Spec: OAuth2 failure short-circuits → realm must be nmos-oauth2.
                    www_auth = resp.headers.get("WWW-Authenticate", "")
                    assert "nmos-oauth2" in www_auth, (
                        f"Expected OAuth2 realm, got {www_auth!r}"
                    )
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_full_stack_oauth2_ok_but_no_session_rejected(
        self, flavor: str,
    ) -> None:
        # OAuth2 passes, a session IS active, but the request omits the
        # PEP-Exclusive-Authorization header → session layer rejects
        # with 401.
        mock_as = MockAuthorizationServer("RS256")
        server = await _start_full_stack_server(flavor, mock_as)
        try:
            base = f"https://127.0.0.1:{server.port}"
            oauth_token = _write_token(mock_as, scopes=["manufacturer", "connection"])
            async with _make_session(flavor) as session:
                await _acquire(session, base, oauth_token)  # session now active

                async with session.patch(
                    f"{base}{CONNECTION_PATCH_URL}",
                    json={},
                    headers={"Authorization": f"Bearer {oauth_token}"},
                ) as resp:
                    assert resp.status == 401
                    www_auth = resp.headers.get("WWW-Authenticate", "")
                    assert "nmos-exclusive" in www_auth, (
                        f"Expected exclusive-session realm, got {www_auth!r}"
                    )
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_full_stack_oauth2_and_session_ok_but_no_mtls_rejected(
        self, flavor: str,
    ) -> None:
        # mTLS is REQUIRED by the server context, so "no cert" can't even
        # complete the TLS handshake. To isolate the mTLS layer we run
        # this scenario against an otherwise-identical server built with
        # CERT_OPTIONAL — still client_auth_required=True on the node so
        # the global client_auth_middleware enforces the verb-gating. The
        # handshake succeeds without a cert; the middleware rejects with
        # 401 BEFORE OAuth2 or session validation runs (single
        # enforcement point for mTLS across all state-changing verbs).
        mock_as = MockAuthorizationServer("RS256")
        # Custom server with CERT_OPTIONAL so the cert-less handshake succeeds.
        node = _make_full_stack_node(mock_as)
        app = create_app(node)
        server = TestServer(app, host="127.0.0.1")
        ssl_ctx = build_server_ssl_context(SERIAL, flavor=flavor, client_auth="optional")
        await server.start_server(ssl=ssl_ctx)
        try:
            base = f"https://127.0.0.1:{server.port}"
            oauth_token = _write_token(mock_as, scopes=["manufacturer", "connection"])

            # Acquire with a cert-bearing session.
            async with _make_session(flavor) as cert_session:
                session_token = await _acquire(cert_session, base, oauth_token)

            # Re-connect WITHOUT a client cert and attempt the PATCH.
            # client_auth_middleware runs before OAuth2 / session, so the
            # rejection is at the mTLS layer with realm="nmos-mtls".
            async with _make_session_no_cert(flavor) as no_cert_session:
                async with no_cert_session.patch(
                    f"{base}{CONNECTION_PATCH_URL}",
                    json={},
                    headers={
                        "Authorization": f"Bearer {oauth_token}",
                        "PEP-Exclusive-Authorization": f"Bearer {session_token}",
                    },
                ) as resp:
                    assert resp.status == 401
                    www_auth = resp.headers.get("WWW-Authenticate", "")
                    assert "nmos-mtls" in www_auth, (
                        f"Expected mTLS realm, got {www_auth!r}"
                    )
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_read_only_only_needs_oauth2(self, flavor: str) -> None:
        # R3: read-only requests are authorized by OAuth2 alone. No
        # session, no mTLS-verb-gate — just a valid OAuth2 token with
        # the right scope → 200.
        mock_as = MockAuthorizationServer("RS256")
        server = await _start_full_stack_server(flavor, mock_as)
        try:
            base = f"https://127.0.0.1:{server.port}"
            oauth_token = _read_token(mock_as, scopes=["node"])
            async with _make_session(flavor) as session:
                async with session.get(
                    f"{base}{NODE_SELF_URL}",
                    headers={"Authorization": f"Bearer {oauth_token}"},
                ) as resp:
                    assert resp.status == 200
        finally:
            await server.close()


# ---------------------------------------------------------------------------
# Combo #4 — TLS + OAuth2 + NodeReservation (no mTLS)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flavor", ["rsa", "ec"])
class TestTlsOAuth2ReservationNoMtls:
    """Server-auth TLS + OAuth2 + Reservation, mTLS disabled.

    ``client_auth='none'`` and ``client_auth_required=False`` — no
    client certs anywhere. The authorization chain reduces to
    OAuth2 → session token. Because no client cert is presented the
    middleware's ``client_id`` ↔ cert binding (spec line 155) doesn't
    apply; the ``aud`` ↔ server-cert binding (spec line 115) still does.
    """

    @pytest.mark.asyncio
    async def test_state_changing_with_oauth2_and_session_ok(
        self, flavor: str,
    ) -> None:
        mock_as = MockAuthorizationServer("RS256")
        server = await _start_full_stack_server(
            flavor, mock_as, client_auth="none", client_auth_required=False,
        )
        try:
            base = f"https://127.0.0.1:{server.port}"
            oauth_token = _write_token(mock_as, scopes=["manufacturer", "connection"])
            async with _make_session_no_cert(flavor) as session:
                session_token = await _acquire(session, base, oauth_token)
                async with session.patch(
                    f"{base}{CONNECTION_PATCH_URL}",
                    json={},
                    headers={
                        "Authorization": f"Bearer {oauth_token}",
                        "PEP-Exclusive-Authorization": f"Bearer {session_token}",
                    },
                ) as resp:
                    assert resp.status != 401
                    assert resp.status != 403
                    assert resp.status in (400, 404)
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_oauth2_short_circuits_before_session(
        self, flavor: str,
    ) -> None:
        mock_as = MockAuthorizationServer("RS256")
        server = await _start_full_stack_server(
            flavor, mock_as, client_auth="none", client_auth_required=False,
        )
        try:
            base = f"https://127.0.0.1:{server.port}"
            oauth_token = _write_token(mock_as, scopes=["manufacturer", "connection"])
            async with _make_session_no_cert(flavor) as session:
                session_token = await _acquire(session, base, oauth_token)
                parts = oauth_token.split(".")
                bad = f"{parts[0]}.{parts[1]}.AAAAAAAAAAAAAAAAAAAAAA"
                async with session.patch(
                    f"{base}{CONNECTION_PATCH_URL}",
                    json={},
                    headers={
                        "Authorization": f"Bearer {bad}",
                        "PEP-Exclusive-Authorization": f"Bearer {session_token}",
                    },
                ) as resp:
                    assert resp.status == 401
                    assert "nmos-oauth2" in resp.headers.get("WWW-Authenticate", "")
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_read_only_passes_with_oauth2_only(self, flavor: str) -> None:
        mock_as = MockAuthorizationServer("RS256")
        server = await _start_full_stack_server(
            flavor, mock_as, client_auth="none", client_auth_required=False,
        )
        try:
            base = f"https://127.0.0.1:{server.port}"
            oauth_token = _read_token(mock_as, scopes=["node"])
            async with _make_session_no_cert(flavor) as session:
                async with session.get(
                    f"{base}{NODE_SELF_URL}",
                    headers={"Authorization": f"Bearer {oauth_token}"},
                ) as resp:
                    assert resp.status == 200
        finally:
            await server.close()


# ---------------------------------------------------------------------------
# Combo #9d — mTLS CERT_OPTIONAL + OAuth2 + NodeReservation (full stack)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flavor", ["rsa", "ec"])
class TestTlsOAuth2ReservationMtlsOptional:
    """Full stack with ``--nodeOptionalClientAuth``.

    Server is CERT_OPTIONAL, ``client_auth_required=True``. Cert-bearing
    clients traverse the full three-layer chain (same as
    ``TestTlsOAuth2ReservationMtlsRequired``); cert-less clients are
    rejected on state-changing verbs by the global
    client_auth_middleware before OAuth2 / session validation runs, but
    can still read.
    """

    @pytest.mark.asyncio
    async def test_full_chain_with_cert_ok(self, flavor: str) -> None:
        mock_as = MockAuthorizationServer("RS256")
        server = await _start_full_stack_server(
            flavor, mock_as, client_auth="optional", client_auth_required=True,
        )
        try:
            base = f"https://127.0.0.1:{server.port}"
            oauth_token = _write_token(mock_as, scopes=["manufacturer", "connection"])
            async with _make_session(flavor) as session:  # with client cert
                session_token = await _acquire(session, base, oauth_token)
                async with session.patch(
                    f"{base}{CONNECTION_PATCH_URL}",
                    json={},
                    headers={
                        "Authorization": f"Bearer {oauth_token}",
                        "PEP-Exclusive-Authorization": f"Bearer {session_token}",
                    },
                ) as resp:
                    assert resp.status != 401
                    assert resp.status != 403
                    assert resp.status in (400, 404)
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_state_changing_without_cert_rejected_at_mtls_layer(
        self, flavor: str,
    ) -> None:
        # Cert-less handshake succeeds (CERT_OPTIONAL), then the mTLS
        # middleware rejects BEFORE OAuth2 / session validation runs.
        mock_as = MockAuthorizationServer("RS256")
        server = await _start_full_stack_server(
            flavor, mock_as, client_auth="optional", client_auth_required=True,
        )
        try:
            base = f"https://127.0.0.1:{server.port}"
            # Acquire a session first (via a cert-bearing client).
            async with _make_session(flavor) as cert_session:
                oauth_token = _write_token(
                    mock_as, scopes=["manufacturer", "connection"],
                )
                session_token = await _acquire(cert_session, base, oauth_token)

            # Re-connect without a cert, try the PATCH — 401 at mTLS layer.
            async with _make_session_no_cert(flavor) as no_cert_session:
                async with no_cert_session.patch(
                    f"{base}{CONNECTION_PATCH_URL}",
                    json={},
                    headers={
                        "Authorization": f"Bearer {oauth_token}",
                        "PEP-Exclusive-Authorization": f"Bearer {session_token}",
                    },
                ) as resp:
                    assert resp.status == 401
                    assert "nmos-mtls" in resp.headers.get("WWW-Authenticate", "")
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_read_only_without_cert_passes_oauth2_only(
        self, flavor: str,
    ) -> None:
        # R5 + R3: cert-less GET with valid OAuth2 → 200. The mTLS
        # verb-gate doesn't apply to read-only; session isn't needed
        # for read-only.
        mock_as = MockAuthorizationServer("RS256")
        server = await _start_full_stack_server(
            flavor, mock_as, client_auth="optional", client_auth_required=True,
        )
        try:
            base = f"https://127.0.0.1:{server.port}"
            oauth_token = _read_token(mock_as, scopes=["node"])
            async with _make_session_no_cert(flavor) as session:
                async with session.get(
                    f"{base}{NODE_SELF_URL}",
                    headers={"Authorization": f"Bearer {oauth_token}"},
                ) as resp:
                    assert resp.status == 200
        finally:
            await server.close()
