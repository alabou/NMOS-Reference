# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""TLS + OAuth2 binding tests (O1, O2 — both sides, independently).

Two orthogonal binding dimensions per ``NMOS With OAuth2.0.md``:

  * **O1 — server-side (aud ↔ TLS server cert)** — spec line 115.
    The ``aud`` DNS entry MUST be either the CN or one of the DNS SANs of
    the TLS **server** cert the Node presents.
  * **O2 — client-side (client_id ↔ TLS client cert)** — spec line 155.
    When the endpoint is accessed using mTLS, the Node MUST additionally
    verify that the token's ``client_id`` matches the CN or a SAN of the
    TLS **client** cert the peer presented during the handshake.

The two bindings are independent. A request must satisfy both when both
bindings are in play; mismatch on either side MUST cause rejection.

Each test is parametrised across RSA and EC cert flavours. Real HTTPS
handshake via ``aiohttp.test_utils.TestServer`` + ``TCPConnector(ssl=…)``.

Note: we deliberately keep ``check_hostname=False`` on the client's
SSLContext because the test server binds to ``127.0.0.1`` which is not
in any SAN. Chain validation (up to MatroxRootCA) remains enforced.
"""

from __future__ import annotations

import ssl
from typing import Any

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from nmos.api import create_app
from nmos.crypto import ExclusiveSession
from nmos.node import Node
from nmos.oauth2.tests._mock_as import MockAuthorizationServer

from nmos.api.tests._tls_helpers import (
    PKI_AVAILABLE,
    build_client_ssl_context,
    build_server_ssl_context,
    client_cert_name,
    server_cert_names,
)


pytestmark = pytest.mark.skipif(
    not PKI_AVAILABLE,
    reason="pre-generated TLS PKI not present at /home/alain/Projects/IPMX/Certificates/build",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SERIAL = "MTX00000"
OTHER_SERIAL = "MTX00099"  # never matches the node — binding-mismatch target


def _make_oauth2_node(mock_as: MockAuthorizationServer, *, serial: str = SERIAL) -> Node:
    """Build an OAuth2-enabled node wired with the mock AS JWKS.

    ``tls_server_cert_names`` is seeded from the PKI so the aud-binding
    cross-check runs against the actual SANs of the server cert the
    Node will present.
    """
    node = Node()
    node.init(serial_number=serial)
    node.oauth2 = True
    node.exclusive_session = ExclusiveSession()
    node.privacy_enabled = True
    node.set_oauth2_public_keys(mock_as.jwks())
    node.tls_server_cert_names = server_cert_names(serial)
    node.use_serial_number_in_aud = True
    return node


async def _start_tls_server(
    flavor: str,
    mock_as: MockAuthorizationServer,
    *,
    client_auth: str = "none",
    serial: str = SERIAL,
    client_auth_required: bool = False,
) -> TestServer:
    node = _make_oauth2_node(mock_as, serial=serial)
    node.client_auth_required = client_auth_required
    app = create_app(node)
    server = TestServer(app, host="127.0.0.1")
    ssl_ctx = build_server_ssl_context(serial, flavor=flavor, client_auth=client_auth)
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


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Class 4 — TestTlsOAuth2ServerBindingAudience (O1)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flavor", ["rsa", "ec"])
class TestTlsOAuth2ServerBindingAudience:
    """O1: ``aud`` must match a CN/SAN of the TLS server cert.

    No mTLS in this class — ``client_auth='none'`` — so the O2 client-
    side binding is not triggered. Every rejection here is due purely
    to the aud↔server-cert check.
    """

    @pytest.mark.asyncio
    async def test_token_with_aud_matching_server_cert_san_accepted(
        self, flavor: str,
    ) -> None:
        mock_as = MockAuthorizationServer("RS256")
        server = await _start_tls_server(flavor, mock_as, client_auth="none")
        try:
            # aud = full-form SAN ("…Server.MTX.MTX00000.matrox.com") — contains
            # SERIAL substring AND is in tls_server_cert_names.
            aud = [f"Matrox.Graphics.Device.Server.MTX.{SERIAL}.matrox.com"]
            token = mock_as.issue_token_for_node(SERIAL, ["node"], aud=aud)
            async with _make_session(flavor) as session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/node/v1.3/self"
                async with session.get(url, headers=_auth(token)) as resp:
                    assert resp.status == 200
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_token_with_aud_matching_san_short_form_accepted(
        self, flavor: str,
    ) -> None:
        # Short-form SAN "MTX-MTX00000.local" — still contains serial and is
        # in the tls_server_cert_names list.
        mock_as = MockAuthorizationServer("RS256")
        server = await _start_tls_server(flavor, mock_as, client_auth="none")
        try:
            token = mock_as.issue_token_for_node(
                SERIAL, ["node"], aud=[f"MTX-{SERIAL}.local"],
            )
            async with _make_session(flavor) as session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/node/v1.3/self"
                async with session.get(url, headers=_auth(token)) as resp:
                    assert resp.status == 200
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_token_with_aud_wildcard_accepted(self, flavor: str) -> None:
        # Spec: a wildcard aud entry "*" short-circuits the server-side
        # binding (spec pseudocode line 342).
        mock_as = MockAuthorizationServer("RS256")
        server = await _start_tls_server(flavor, mock_as, client_auth="none")
        try:
            token = mock_as.issue_token_for_node(SERIAL, ["node"], aud=["*"])
            async with _make_session(flavor) as session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/node/v1.3/self"
                async with session.get(url, headers=_auth(token)) as resp:
                    assert resp.status == 200
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_token_with_aud_missing_serial_rejected(
        self, flavor: str,
    ) -> None:
        # aud="example.com" — doesn't contain SERIAL substring → aud check
        # fails → valid_token=True, allowed=False → 403.
        mock_as = MockAuthorizationServer("RS256")
        server = await _start_tls_server(flavor, mock_as, client_auth="none")
        try:
            token = mock_as.issue_token_for_node(
                SERIAL, ["node"], aud=["example.com"],
            )
            async with _make_session(flavor) as session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/node/v1.3/self"
                async with session.get(url, headers=_auth(token)) as resp:
                    assert resp.status == 403
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_token_with_aud_matching_different_serial_rejected(
        self, flavor: str,
    ) -> None:
        # aud looks like a valid SAN but targets a different serial
        # ("…MTX.MTX00099.matrox.com") → doesn't contain our node serial →
        # aud check fails → 403.
        mock_as = MockAuthorizationServer("RS256")
        server = await _start_tls_server(flavor, mock_as, client_auth="none")
        try:
            token = mock_as.issue_token_for_node(
                SERIAL,
                ["node"],
                aud=[f"Matrox.Graphics.Device.Server.MTX.{OTHER_SERIAL}.matrox.com"],
            )
            async with _make_session(flavor) as session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/node/v1.3/self"
                async with session.get(url, headers=_auth(token)) as resp:
                    assert resp.status == 403
        finally:
            await server.close()


# ---------------------------------------------------------------------------
# Class 5 — TestTlsOAuth2ClientBindingClientId (O2)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flavor", ["rsa", "ec"])
class TestTlsOAuth2ClientBindingClientId:
    """O2: ``client_id`` must match a CN/SAN of the TLS client cert.

    Server is ``CERT_REQUIRED`` here so a client cert is always present
    on the wire (triggers the middleware's client-cert-binding branch).
    In every test the aud is set to a valid server-side value so O1 is
    never the cause of rejection — isolating O2.
    """

    @pytest.mark.asyncio
    async def test_mtls_with_matching_client_id_accepted(
        self, flavor: str,
    ) -> None:
        mock_as = MockAuthorizationServer("RS256")
        server = await _start_tls_server(flavor, mock_as, client_auth="required")
        try:
            # client_id matches the client cert's CN for MTX00000.
            token = mock_as.issue_token_for_node(
                SERIAL,
                ["node"],
                client_id=client_cert_name(SERIAL),
                aud=[f"Matrox.Graphics.Device.Server.MTX.{SERIAL}.matrox.com"],
            )
            async with _make_session(flavor, client_serial=SERIAL) as session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/node/v1.3/self"
                async with session.get(url, headers=_auth(token)) as resp:
                    assert resp.status == 200
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_mtls_with_mismatched_client_id_rejected(
        self, flavor: str,
    ) -> None:
        # Client cert is MTX00000 but token claims client_id=MTX00099 →
        # middleware rejects with 401 BEFORE aud is evaluated.
        mock_as = MockAuthorizationServer("RS256")
        server = await _start_tls_server(flavor, mock_as, client_auth="required")
        try:
            token = mock_as.issue_token_for_node(
                SERIAL,
                ["node"],
                client_id=client_cert_name(OTHER_SERIAL),
                aud=[f"Matrox.Graphics.Device.Server.MTX.{SERIAL}.matrox.com"],
            )
            async with _make_session(flavor, client_serial=SERIAL) as session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/node/v1.3/self"
                async with session.get(url, headers=_auth(token)) as resp:
                    assert resp.status == 401
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_mtls_optional_no_client_cert_read_only_passes_oauth2_only(
        self, flavor: str,
    ) -> None:
        # CERT_OPTIONAL: no client cert presented → O2 binding is not
        # triggered (spec line 155 only applies "when accessed using
        # mTLS"). With a valid OAuth2 token, the read succeeds.
        mock_as = MockAuthorizationServer("RS256")
        server = await _start_tls_server(flavor, mock_as, client_auth="optional")
        try:
            token = mock_as.issue_token_for_node(
                SERIAL,
                ["node"],
                aud=[f"Matrox.Graphics.Device.Server.MTX.{SERIAL}.matrox.com"],
            )
            # No client cert.
            async with _make_session(flavor) as session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/node/v1.3/self"
                async with session.get(url, headers=_auth(token)) as resp:
                    assert resp.status == 200
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_mtls_optional_wrong_client_id_with_client_cert_rejected(
        self, flavor: str,
    ) -> None:
        # CERT_OPTIONAL BUT the client DOES present its cert. The server's
        # peercert is populated → middleware enforces O2. Mismatched
        # client_id → 401.
        mock_as = MockAuthorizationServer("RS256")
        server = await _start_tls_server(flavor, mock_as, client_auth="optional")
        try:
            token = mock_as.issue_token_for_node(
                SERIAL,
                ["node"],
                client_id=client_cert_name(OTHER_SERIAL),
                aud=[f"Matrox.Graphics.Device.Server.MTX.{SERIAL}.matrox.com"],
            )
            async with _make_session(flavor, client_serial=SERIAL) as session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/node/v1.3/self"
                async with session.get(url, headers=_auth(token)) as resp:
                    assert resp.status == 401
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_mtls_optional_state_changing_without_cert_rejected_at_mtls_layer(
        self, flavor: str,
    ) -> None:
        # Combo #9b deny symmetry: CERT_OPTIONAL +
        # client_auth_required=True + OAuth2 enabled. Cert-less handshake
        # succeeds; client_auth_middleware rejects the state-changing
        # request with 401 BEFORE OAuth2 token validation runs. Proves
        # the mTLS layer short-circuits the OAuth2 layer even in the
        # optional-client-auth configuration.
        mock_as = MockAuthorizationServer("RS256")
        server = await _start_tls_server(
            flavor, mock_as, client_auth="optional", client_auth_required=True,
        )
        try:
            # A well-formed OAuth2 token — should never be consulted.
            token = mock_as.issue_token_for_node(
                SERIAL,
                ["connection"],
                client_id=client_cert_name(SERIAL),
                aud=[f"Matrox.Graphics.Device.Server.MTX.{SERIAL}.matrox.com"],
                read_write_apis={"connection": {"read": ["*"], "write": ["*"]}},
            )
            async with _make_session(flavor) as session:  # no client cert
                url = (
                    f"https://127.0.0.1:{server.port}"
                    "/x-nmos/connection/v1.1/single/senders/"
                    "00000000-0000-0000-0000-000000000000/staged"
                )
                async with session.patch(
                    url, json={}, headers=_auth(token),
                ) as resp:
                    assert resp.status == 401
                    assert "nmos-mtls" in resp.headers.get("WWW-Authenticate", "")
        finally:
            await server.close()


# ---------------------------------------------------------------------------
# Class 5b — TestTlsOAuth2BothBindingsCombined (O1 × O2 orthogonality)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flavor", ["rsa", "ec"])
class TestTlsOAuth2BothBindingsCombined:
    """O1 + O2 orthogonality: each binding dimension must reject a
    mismatch independently of the other.

    All tests use a CERT_REQUIRED server with client cert = MTX00000.
    The token's aud / client_id are varied to cover the matrix:

      aud\\client_id   | match   | mismatch
      match            | ✓ 200   | O2 rejects → 401
      mismatch         | O1 →403 | (both wrong — undefined order; skip)
      wildcard "*"     | ✓ 200   | O2 still rejects → 401
    """

    @pytest.mark.asyncio
    async def test_both_bindings_match_accepted(self, flavor: str) -> None:
        mock_as = MockAuthorizationServer("RS256")
        server = await _start_tls_server(flavor, mock_as, client_auth="required")
        try:
            token = mock_as.issue_token_for_node(
                SERIAL,
                ["node"],
                client_id=client_cert_name(SERIAL),
                aud=[f"Matrox.Graphics.Device.Server.MTX.{SERIAL}.matrox.com"],
            )
            async with _make_session(flavor, client_serial=SERIAL) as session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/node/v1.3/self"
                async with session.get(url, headers=_auth(token)) as resp:
                    assert resp.status == 200
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_aud_mismatch_but_client_id_match_rejected(
        self, flavor: str,
    ) -> None:
        # Client-side valid, server-side invalid → aud check fails → 403.
        mock_as = MockAuthorizationServer("RS256")
        server = await _start_tls_server(flavor, mock_as, client_auth="required")
        try:
            token = mock_as.issue_token_for_node(
                SERIAL,
                ["node"],
                client_id=client_cert_name(SERIAL),
                aud=[f"Matrox.Graphics.Device.Server.MTX.{OTHER_SERIAL}.matrox.com"],
            )
            async with _make_session(flavor, client_serial=SERIAL) as session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/node/v1.3/self"
                async with session.get(url, headers=_auth(token)) as resp:
                    assert resp.status == 403
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_aud_match_but_client_id_mismatch_rejected(
        self, flavor: str,
    ) -> None:
        # Server-side valid, client-side invalid → middleware's client_id
        # check fires first → 401.
        mock_as = MockAuthorizationServer("RS256")
        server = await _start_tls_server(flavor, mock_as, client_auth="required")
        try:
            token = mock_as.issue_token_for_node(
                SERIAL,
                ["node"],
                client_id=client_cert_name(OTHER_SERIAL),
                aud=[f"Matrox.Graphics.Device.Server.MTX.{SERIAL}.matrox.com"],
            )
            async with _make_session(flavor, client_serial=SERIAL) as session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/node/v1.3/self"
                async with session.get(url, headers=_auth(token)) as resp:
                    assert resp.status == 401
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_both_bindings_wildcard_aud_still_checks_client_id(
        self, flavor: str,
    ) -> None:
        # aud="*" short-circuits O1, but O2 is enforced independently.
        # Mismatched client_id → 401. This proves the wildcard does NOT
        # exempt the client-side binding.
        mock_as = MockAuthorizationServer("RS256")
        server = await _start_tls_server(flavor, mock_as, client_auth="required")
        try:
            token = mock_as.issue_token_for_node(
                SERIAL,
                ["node"],
                client_id=client_cert_name(OTHER_SERIAL),
                aud=["*"],
            )
            async with _make_session(flavor, client_serial=SERIAL) as session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/node/v1.3/self"
                async with session.get(url, headers=_auth(token)) as resp:
                    assert resp.status == 401
        finally:
            await server.close()
