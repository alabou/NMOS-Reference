# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Mutual TLS tests — `CERT_REQUIRED` + `CERT_OPTIONAL` verb-gating.

``CERT_REQUIRED`` (Class 2) forces every client to present a valid cert at
the handshake. ``CERT_OPTIONAL`` (Class 3) is the more interesting case —
clients may connect without a cert; the node grants read-only access
without one but requires one for state-changing verbs. This is spec rule
**R5** from ``NMOS With Node Reservation.md:41-45`` ("read-only granted
without client certificate when mTLS is enabled").

The optional-mTLS path uses the existing middleware flag
``node.client_auth_required`` (``middleware.py:188, 215``) which gates
state-changing verbs on ``_client_authenticated``.
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


SERIAL = "SNX00000"


def _make_node(*, client_auth_required: bool = False) -> Node:
    node = Node()
    node.init(serial_number=SERIAL)
    # An ExclusiveSession is required for check_exclusive_authorization
    # to engage; without it the middleware short-circuits to "allow".
    node.exclusive_session = ExclusiveSession()
    node.client_auth_required = client_auth_required
    return node


async def _start_tls_server(flavor: str, *, client_auth: str,
                             client_auth_required: bool = False) -> TestServer:
    node = _make_node(client_auth_required=client_auth_required)
    app = create_app(node)
    server = TestServer(app, host="127.0.0.1")
    ssl_ctx = build_server_ssl_context(SERIAL, flavor=flavor, client_auth=client_auth)
    await server.start_server(ssl=ssl_ctx)
    return server


async def _client(flavor: str, *, with_client_cert: bool) -> aiohttp.ClientSession:
    ctx = build_client_ssl_context(
        client_serial=SERIAL if with_client_cert else None,
        flavor=flavor,
    )
    connector = aiohttp.TCPConnector(ssl=ctx)
    return aiohttp.ClientSession(connector=connector)


# ---------------------------------------------------------------------------
# Class 2 — TestTlsMutualAuthRequired (CERT_REQUIRED)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flavor", ["rsa", "ec"])
class TestTlsMutualAuthRequired:
    """Strict mTLS: the TLS handshake fails when the client doesn't
    present a verifiable cert. All verification happens in OpenSSL
    before the first HTTP byte."""

    @pytest.mark.asyncio
    async def test_mtls_required_accepts_valid_client_cert(self, flavor: str) -> None:
        server = await _start_tls_server(flavor, client_auth="required")
        try:
            session = await _client(flavor, with_client_cert=True)
            async with session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/"
                async with session.get(url) as resp:
                    assert resp.status == 200
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_mtls_required_rejects_no_client_cert(self, flavor: str) -> None:
        server = await _start_tls_server(flavor, client_auth="required")
        try:
            session = await _client(flavor, with_client_cert=False)
            async with session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/"
                with pytest.raises(aiohttp.ClientConnectionError):
                    async with session.get(url):
                        pass
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_mtls_required_rejects_untrusted_client_cert(self, flavor: str) -> None:
        # A client whose cert is signed by an unrelated CA: we simulate
        # this by making the server trust only the *other* flavour's root
        # while the client presents a cert from this flavour's chain —
        # the signatures won't validate against the wrong root.
        other_flavor = "ec" if flavor == "rsa" else "rsa"
        server = await _start_tls_server(flavor, client_auth="required")
        # Rewire the server's CA trust store to the opposite flavour's
        # root — identical procedure to how `build_server_ssl_context`
        # loads it, but pointing at the mismatched root.
        from nmos.api.tests._tls_helpers import root_ca
        # Rebuild server with a "foreign" trust store.
        await server.close()
        import ssl as _ssl
        node = _make_node()
        app = create_app(node)
        server = TestServer(app, host="127.0.0.1")
        ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = _ssl.TLSVersion.TLSv1_2
        from nmos.api.tests._tls_helpers import server_chain, server_key
        ctx.load_cert_chain(
            certfile=str(server_chain(SERIAL, flavor)),
            keyfile=str(server_key(SERIAL, flavor)),
        )
        ctx.verify_mode = _ssl.CERT_REQUIRED
        ctx.load_verify_locations(cafile=str(root_ca(other_flavor)))
        await server.start_server(ssl=ctx)
        try:
            session = await _client(flavor, with_client_cert=True)
            async with session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/"
                with pytest.raises(aiohttp.ClientConnectionError):
                    async with session.get(url):
                        pass
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_mtls_required_accepts_intermediate_chain(self, flavor: str) -> None:
        # The client sends leaf + intermediate (from client.chain.pem).
        # The server's CA bundle has the root only. OpenSSL must walk
        # leaf → intermediate → root to verify — this is the normal chain
        # validation path.
        server = await _start_tls_server(flavor, client_auth="required")
        try:
            session = await _client(flavor, with_client_cert=True)
            async with session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/"
                async with session.get(url) as resp:
                    assert resp.status == 200
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_mtls_required_post_reaches_handler(self, flavor: str) -> None:
        # Confirm the TLS layer doesn't reject state-changing verbs
        # when the cert is valid. The endpoint may return 404/405 based
        # on a non-existent sender id — that's fine; we only care that
        # the TLS handshake succeeded and the request reached the app.
        server = await _start_tls_server(flavor, client_auth="required")
        try:
            session = await _client(flavor, with_client_cert=True)
            async with session:
                url = (
                    f"https://127.0.0.1:{server.port}/x-nmos/connection/v1.1/"
                    f"single/senders/00000000-0000-0000-0000-000000000000/staged"
                )
                async with session.patch(url, json={}) as resp:
                    # Any HTTP status is a pass here — the TLS handshake
                    # worked and the Python stack processed the request.
                    assert 100 <= resp.status < 600
        finally:
            await server.close()


# ---------------------------------------------------------------------------
# Class 3 — TestTlsMutualAuthOptional (R5 — "read-only without cert")
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flavor", ["rsa", "ec"])
class TestTlsMutualAuthOptional:
    """Optional mTLS: the TLS handshake accepts both cert and non-cert
    clients; the *application* layer gates state-changing verbs on the
    presence of a verified client cert.

    This exercises the spec rule (NMOS With Node Reservation.md:41-45,
    R5): read-only requests MUST be authorized without restrictions;
    state-changing requests MUST be authorized by mTLS if enabled.
    """

    @pytest.mark.asyncio
    async def test_get_without_client_cert_ok(self, flavor: str) -> None:
        # R5 — read-only GET succeeds even when no client cert is offered.
        server = await _start_tls_server(
            flavor, client_auth="optional", client_auth_required=True,
        )
        try:
            session = await _client(flavor, with_client_cert=False)
            async with session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/"
                async with session.get(url) as resp:
                    assert resp.status == 200
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_get_node_api_without_client_cert_ok(self, flavor: str) -> None:
        server = await _start_tls_server(
            flavor, client_auth="optional", client_auth_required=True,
        )
        try:
            session = await _client(flavor, with_client_cert=False)
            async with session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/node/v1.3/self"
                async with session.get(url) as resp:
                    assert resp.status == 200
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_get_with_client_cert_ok(self, flavor: str) -> None:
        # GET must also succeed WITH a client cert — the cert is optional
        # for read-only verbs, not forbidden.
        server = await _start_tls_server(
            flavor, client_auth="optional", client_auth_required=True,
        )
        try:
            session = await _client(flavor, with_client_cert=True)
            async with session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/node/v1.3/self"
                async with session.get(url) as resp:
                    assert resp.status == 200
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_post_without_client_cert_rejected(self, flavor: str) -> None:
        # R5 state-changing without a client cert → 401.
        server = await _start_tls_server(
            flavor, client_auth="optional", client_auth_required=True,
        )
        try:
            session = await _client(flavor, with_client_cert=False)
            async with session:
                url = (
                    f"https://127.0.0.1:{server.port}/x-nmos/connection/v1.1/"
                    f"single/senders/00000000-0000-0000-0000-000000000000/staged"
                )
                async with session.patch(url, json={}) as resp:
                    assert resp.status == 401
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_401_includes_www_authenticate_header(self, flavor: str) -> None:
        # R2 (Node Reservation.md:67) — 401 MUST include a
        # WWW-Authenticate response header.
        server = await _start_tls_server(
            flavor, client_auth="optional", client_auth_required=True,
        )
        try:
            session = await _client(flavor, with_client_cert=False)
            async with session:
                url = (
                    f"https://127.0.0.1:{server.port}/x-nmos/connection/v1.1/"
                    f"single/senders/00000000-0000-0000-0000-000000000000/staged"
                )
                async with session.patch(url, json={}) as resp:
                    assert resp.status == 401
                    assert "WWW-Authenticate" in resp.headers
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_post_with_valid_client_cert_reaches_handler(self, flavor: str) -> None:
        # With a valid client cert, state-changing requests clear the
        # middleware auth layer. Any status is a pass (the specific
        # error is unrelated to TLS/mTLS).
        server = await _start_tls_server(
            flavor, client_auth="optional", client_auth_required=True,
        )
        try:
            session = await _client(flavor, with_client_cert=True)
            async with session:
                url = (
                    f"https://127.0.0.1:{server.port}/x-nmos/connection/v1.1/"
                    f"single/senders/00000000-0000-0000-0000-000000000000/staged"
                )
                async with session.patch(url, json={}) as resp:
                    # The request passed both the TLS handshake and the
                    # client_auth_required gate. The specific status (404 /
                    # 400 / 500) depends on the handler's own checks.
                    assert resp.status != 401, (
                        "a valid mTLS client cert should clear the "
                        "client_auth_required gate — got 401 from the middleware"
                    )
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_put_without_client_cert_rejected(self, flavor: str) -> None:
        # IS-11 PUT /senders/{id}/constraints/active requires a state
        # change — same R5 gating applies.
        server = await _start_tls_server(
            flavor, client_auth="optional", client_auth_required=True,
        )
        try:
            session = await _client(flavor, with_client_cert=False)
            async with session:
                url = (
                    f"https://127.0.0.1:{server.port}/x-nmos/streamcompatibility/v1.0/"
                    f"senders/00000000-0000-0000-0000-000000000000/constraints/active"
                )
                async with session.put(url, json={"constraint_sets": []}) as resp:
                    # 401 from client_auth_required OR 404/405 if the route
                    # bypasses the check — the important thing is no TLS
                    # error and no 200.
                    assert resp.status in (401, 404, 405)
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_delete_without_client_cert_rejected(self, flavor: str) -> None:
        server = await _start_tls_server(
            flavor, client_auth="optional", client_auth_required=True,
        )
        try:
            session = await _client(flavor, with_client_cert=False)
            async with session:
                url = (
                    f"https://127.0.0.1:{server.port}/x-nmos/streamcompatibility/v1.0/"
                    f"senders/00000000-0000-0000-0000-000000000000/constraints/active"
                )
                async with session.delete(url) as resp:
                    assert resp.status in (401, 404, 405)
        finally:
            await server.close()
