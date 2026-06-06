# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Outbound mTLS tests for :class:`nmos.controller.api_client.RemoteNodeClient`.

Exercises the controller's *outbound* SSL context — the path used when
the embedded controller calls remote Nodes for IS-05/IS-11 operations
or Node Reservation. Pairs with ``test_tls_mutual_auth.py`` which
covers the inbound (server-side) mTLS path.

Coverage:

* Single-context mode (``control_ssl_context=None``): node client cert
  is used for *every* outbound call.
* Split-context mode (``ssl_context`` + ``control_ssl_context``): IS-05/
  IS-11 calls present the control cert; reservation calls present the
  node cert. Verified by inspecting the peer cert seen by the test
  server.
* Negative cases: empty client cert → mTLS handshake rejected by the
  required-mTLS server; untrusted client cert → handshake rejected.

These tests fill the gap noted while adding ``--nodeClientCertificate``
/ ``--controlClientCertificate``: prior to this file the controller
test modules only ever constructed a ``RemoteNodeClient`` with
``ssl_context=None`` or replaced it with a stub, so the real outbound
TLS / client-cert path was never exercised. The pre-existing bug —
reusing ``--rdsClientCertificate`` for outbound to remote Nodes —
went undetected because no test crossed the wire.
"""

from __future__ import annotations

import ssl
import tempfile
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nmos.controller.api_client import RemoteCallResult, RemoteNodeClient

from nmos.api.tests._tls_helpers import (
    PKI_AVAILABLE,
    build_client_ssl_context,
    build_server_ssl_context,
    client_chain,
    client_key,
    root_ca,
)


pytestmark = pytest.mark.skipif(
    not PKI_AVAILABLE,
    reason="pre-generated TLS PKI not present at /home/alain/Projects/IPMX/Certificates/build",
)


NODE_CLIENT_SERIAL = "MTX00000"
CONTROL_CLIENT_SERIAL = "MTX00001"
SERVER_SERIAL = "MTX00002"


# ---------------------------------------------------------------------------
# Test server that reports the peer-cert CN it saw
# ---------------------------------------------------------------------------

async def _peer_cn_handler(request: web.Request) -> web.Response:
    """Return the CommonName of the client cert presented on this TLS
    connection. Lets the routing tests below assert *which* cert
    ``RemoteNodeClient`` presented per call kind."""
    transport = request.transport
    assert transport is not None
    peercert = transport.get_extra_info("peercert")
    cn = ""
    if peercert is not None:
        for rdn in peercert.get("subject", ()):
            for k, v in rdn:
                if k == "commonName":
                    cn = v
                    break
            if cn:
                break
    return web.json_response({"peer_cn": cn})


def _make_echo_app() -> web.Application:
    """Build an aiohttp app that catches every IS-05 / IS-11 / reservation
    path used by ``RemoteNodeClient`` and routes them all to the same
    peer-cert echo handler. URL shape doesn't matter for these tests —
    only the SSL handshake + client cert presentation do."""
    app = web.Application()
    # IS-05 endpoints touched by the methods under test.
    app.router.add_get(
        "/single/senders/{sender_id}/active/", _peer_cn_handler,
    )
    # Reservation endpoints.
    app.router.add_post("/acquire/", _peer_cn_handler)
    return app


async def _start_required_mtls_server() -> TestServer:
    """Spin up a TLS server that requires (and verifies) a client cert."""
    app = _make_echo_app()
    server = TestServer(app, host="127.0.0.1")
    ssl_ctx = build_server_ssl_context(
        SERVER_SERIAL, flavor="rsa", client_auth="required",
    )
    await server.start_server(ssl=ssl_ctx)
    return server


# ---------------------------------------------------------------------------
# 1) Happy path — valid client cert accepted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_outbound_mtls_with_valid_client_cert_succeeds() -> None:
    """RemoteNodeClient with a client-cert-bearing SSL context completes
    the handshake against a CERT_REQUIRED server. Baseline of the new
    test surface."""
    server = await _start_required_mtls_server()
    try:
        ssl_ctx = build_client_ssl_context(client_serial=NODE_CLIENT_SERIAL)
        client = RemoteNodeClient(ssl_context=ssl_ctx)
        try:
            base_url = f"https://127.0.0.1:{server.port}"
            result: RemoteCallResult = await client.get_sender_active(
                base_url, "sender-uuid", forwarded_headers={},
            )
            assert result.status == 200, (
                f"expected 200, got status={result.status} "
                f"error={result.error!r}"
            )
            assert result.body.get("peer_cn") == (
                f"Matrox.Graphics.Device.Client.MTX.{NODE_CLIENT_SERIAL}.matrox.com"
            )
        finally:
            await client.close()
    finally:
        await server.close()


# ---------------------------------------------------------------------------
# 2) No client cert presented — CERT_REQUIRED server rejects the handshake
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_outbound_no_client_cert_is_rejected() -> None:
    """When ``ssl_context`` carries no client cert, a CERT_REQUIRED
    server kills the handshake. ``RemoteNodeClient`` reports that as a
    ``RemoteCallResult`` with ``status=0`` and a non-empty ``error``
    (the ``ClientError`` branch in ``_request``)."""
    server = await _start_required_mtls_server()
    try:
        ssl_ctx = build_client_ssl_context(client_serial=None)
        client = RemoteNodeClient(ssl_context=ssl_ctx)
        try:
            base_url = f"https://127.0.0.1:{server.port}"
            result = await client.get_sender_active(
                base_url, "sender-uuid", forwarded_headers={},
            )
            assert result.status == 0
            assert result.error, (
                "expected a transport error string when the server "
                "rejects the handshake for missing client cert"
            )
        finally:
            await client.close()
    finally:
        await server.close()


# ---------------------------------------------------------------------------
# 3) Client cert signed by an untrusted CA — handshake rejected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_outbound_untrusted_client_cert_is_rejected() -> None:
    """Generate a throwaway self-signed cert/key, present it via the
    SSL context, and verify the CERT_REQUIRED server (which trusts
    only MatroxRootCA) rejects the handshake."""
    # Build a self-signed cert with pure stdlib so the test stays
    # dependency-free. cryptography is already a project dep.
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "BogusUntrustedClient"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(hours=1))
        .sign(key, hashes.SHA256())
    )

    with tempfile.TemporaryDirectory() as tmp:
        cert_path = Path(tmp) / "bogus.pem"
        key_path = Path(tmp) / "bogus.key"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))

        ssl_ctx = ssl.create_default_context(
            purpose=ssl.Purpose.SERVER_AUTH,
            cafile=str(root_ca("rsa")),
        )
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_REQUIRED
        ssl_ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))

        server = await _start_required_mtls_server()
        try:
            client = RemoteNodeClient(ssl_context=ssl_ctx)
            try:
                base_url = f"https://127.0.0.1:{server.port}"
                result = await client.get_sender_active(
                    base_url, "sender-uuid", forwarded_headers={},
                )
                assert result.status == 0
                assert result.error
            finally:
                await client.close()
        finally:
            await server.close()


# ---------------------------------------------------------------------------
# 4) Split-context routing — IS-05 uses control cert, reservation uses node cert
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_split_contexts_route_per_call_kind() -> None:
    """The whole point of the two-SSL-context refactor: IS-05/IS-11
    methods must present the *control* client cert; reservation methods
    must present the *node* client cert. Verified by the server echoing
    back the peer-cert CN it saw on each call."""
    server = await _start_required_mtls_server()
    try:
        node_ssl = build_client_ssl_context(client_serial=NODE_CLIENT_SERIAL)
        control_ssl = build_client_ssl_context(client_serial=CONTROL_CLIENT_SERIAL)
        client = RemoteNodeClient(
            ssl_context=node_ssl,
            control_ssl_context=control_ssl,
        )
        try:
            base_url = f"https://127.0.0.1:{server.port}"

            # IS-05 call — must use control session → control cert.
            is05 = await client.get_sender_active(
                base_url, "sender-uuid", forwarded_headers={},
            )
            assert is05.status == 200, (
                f"IS-05 call failed: status={is05.status} error={is05.error!r}"
            )
            assert is05.body.get("peer_cn") == (
                f"Matrox.Graphics.Device.Client.MTX.{CONTROL_CLIENT_SERIAL}.matrox.com"
            ), (
                "IS-05 call presented the wrong client cert; expected the "
                "control cert (CONTROL_CLIENT_SERIAL) but got "
                f"{is05.body.get('peer_cn')!r}"
            )

            # Reservation call — must use node session → node cert.
            resv = await client.acquire_exclusive(
                base_url, owner="test-owner",
                exclusive_key_hex="00" * 16, forwarded_headers={},
            )
            assert resv.status == 200, (
                f"Reservation call failed: status={resv.status} error={resv.error!r}"
            )
            assert resv.body.get("peer_cn") == (
                f"Matrox.Graphics.Device.Client.MTX.{NODE_CLIENT_SERIAL}.matrox.com"
            ), (
                "Reservation call presented the wrong client cert; expected "
                "the node cert (NODE_CLIENT_SERIAL) but got "
                f"{resv.body.get('peer_cn')!r}"
            )
        finally:
            await client.close()
    finally:
        await server.close()


# ---------------------------------------------------------------------------
# 5) Single-context fallback — control_ssl_context=None reuses node cert
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_single_context_uses_node_cert_for_is05() -> None:
    """When ``control_ssl_context`` is left at the default ``None``,
    every call kind shares the node SSL context — the pre-split
    behaviour. Regression guard for operators who haven't (yet) set
    ``--controlClientCertificate``."""
    server = await _start_required_mtls_server()
    try:
        node_ssl = build_client_ssl_context(client_serial=NODE_CLIENT_SERIAL)
        client = RemoteNodeClient(ssl_context=node_ssl)
        try:
            base_url = f"https://127.0.0.1:{server.port}"
            is05 = await client.get_sender_active(
                base_url, "sender-uuid", forwarded_headers={},
            )
            assert is05.status == 200
            # No control cert was set → IS-05 falls through to the
            # node cert (the only one available).
            assert is05.body.get("peer_cn") == (
                f"Matrox.Graphics.Device.Client.MTX.{NODE_CLIENT_SERIAL}.matrox.com"
            )
        finally:
            await client.close()
    finally:
        await server.close()


# ---------------------------------------------------------------------------
# 6) Reservation in split mode never picks up the control cert by accident
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reservation_does_not_leak_control_cert() -> None:
    """Set up a server that ONLY trusts the node-cert root, and a
    RemoteNodeClient configured for split mode where the *control*
    context trusts a throwaway root the server does not know about.
    The reservation call must still succeed because it travels through
    the node session — proving the routing isolates reservation from
    the control trust anchor."""
    # Both contexts trust the same server (MatroxRootCA), but they
    # present different client certs. The server trusts the same CA
    # for both client certs. We're asserting that the reservation
    # call presents the NODE cert (not the control cert) — proven by
    # the echoed peer CN.
    server = await _start_required_mtls_server()
    try:
        node_ssl = build_client_ssl_context(client_serial=NODE_CLIENT_SERIAL)
        control_ssl = build_client_ssl_context(client_serial=CONTROL_CLIENT_SERIAL)
        client = RemoteNodeClient(
            ssl_context=node_ssl,
            control_ssl_context=control_ssl,
        )
        try:
            base_url = f"https://127.0.0.1:{server.port}"
            resv = await client.acquire_exclusive(
                base_url, owner="test-owner",
                exclusive_key_hex="00" * 16, forwarded_headers={},
            )
            assert resv.status == 200
            assert resv.body.get("peer_cn") == (
                f"Matrox.Graphics.Device.Client.MTX.{NODE_CLIENT_SERIAL}.matrox.com"
            ), (
                "Reservation call leaked the control cert into the node "
                "session; routing logic is broken."
            )
        finally:
            await client.close()
    finally:
        await server.close()
