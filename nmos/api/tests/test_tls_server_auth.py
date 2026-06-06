# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""TLS server-authentication tests (T1, T2, T4).

Bare HTTPS end-to-end: a real ``aiohttp.TestServer`` bound to a server
SSLContext, a real ``ClientSession`` with a client TCPConnector whose
SSLContext trusts the ``MatroxRootCA``. Each test is parametrised across
RSA and EC cert flavours.

Spec references:
  - ``NMOS With OAuth2.0.md:110`` — TLS v1.2 / v1.3 MUST when serving HTTP.
  - ``NMOS With Node Reservation.md:57`` — bare HTTP MUST NOT be used.
  - ``NMOS With OAuth2.0.md:33`` — TLS for the registry (SHOULD).
"""

from __future__ import annotations

import ssl
from typing import Any

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from nmos.api import create_app
from nmos.node import Node

from nmos.api.tests._tls_helpers import (
    PKI_AVAILABLE,
    build_client_ssl_context,
    build_server_ssl_context,
    product_ca,
    root_ca,
    server_chain,
)


pytestmark = pytest.mark.skipif(
    not PKI_AVAILABLE,
    reason="pre-generated TLS PKI not present at /home/alain/Projects/IPMX/Certificates/build",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SERIAL = "MTX00000"


def _make_node() -> Node:
    node = Node()
    node.init(serial_number=SERIAL)
    return node


async def _start_tls_server(flavor: str, client_auth: str = "none") -> TestServer:
    node = _make_node()
    app = create_app(node)
    server = TestServer(app, host="127.0.0.1")
    ssl_ctx = build_server_ssl_context(SERIAL, flavor=flavor, client_auth=client_auth)
    await server.start_server(ssl=ssl_ctx)
    return server


# ---------------------------------------------------------------------------
# Class 1 — TestTlsServerAuth
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flavor", ["rsa", "ec"])
class TestTlsServerAuth:
    """T1 + T2 + T4: plain HTTPS with a server cert from the PKI.

    `check_hostname=False` everywhere except the dedicated hostname-
    mismatch test — TestServer binds 127.0.0.1 which isn't in any cert
    SAN, so full hostname verification would fail even for the happy
    path. Chain validation (up to MatroxRootCA) is always performed.
    """

    @pytest.mark.asyncio
    async def test_https_get_200_with_trusted_root(self, flavor: str) -> None:
        # T1, T2: an HTTPS GET completes end-to-end when the client trusts
        # the root CA that signed the server's intermediate (via chain.pem).
        server = await _start_tls_server(flavor)
        try:
            client_ctx = build_client_ssl_context(flavor=flavor)
            connector = aiohttp.TCPConnector(ssl=client_ctx)
            async with aiohttp.ClientSession(connector=connector) as session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/"
                async with session.get(url) as resp:
                    assert resp.status == 200
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_https_get_reject_empty_ca_bundle(self, flavor: str) -> None:
        # T1 negative: with zero trust anchors, chain validation MUST fail.
        # The failure surfaces as an aiohttp ClientConnectorCertificateError
        # / ClientConnectorSSLError, both of which inherit ClientConnectionError.
        server = await _start_tls_server(flavor)
        try:
            empty_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            empty_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            empty_ctx.check_hostname = False
            empty_ctx.verify_mode = ssl.CERT_REQUIRED
            # Intentionally no CA loaded → anchor-less verify.
            connector = aiohttp.TCPConnector(ssl=empty_ctx)
            async with aiohttp.ClientSession(connector=connector) as session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/"
                with pytest.raises(aiohttp.ClientConnectionError):
                    async with session.get(url):
                        pass
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_chain_pem_resolves_through_intermediate(self, flavor: str) -> None:
        # Client trusts only the INTERMEDIATE (MatroxProductCA). By default
        # OpenSSL requires the trust anchor to be self-signed so using the
        # intermediate alone would fail; the spec compliance path always
        # anchors at the root. We opt into the PARTIAL_CHAIN flag here
        # purely to prove the server's wire chain (leaf + intermediate)
        # is well-formed — the intermediate-as-anchor behaviour itself is
        # not a spec requirement.
        server = await _start_tls_server(flavor)
        try:
            client_ctx = build_client_ssl_context(
                flavor=flavor,
                cafile=product_ca(flavor),
            )
            client_ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
            connector = aiohttp.TCPConnector(ssl=client_ctx)
            async with aiohttp.ClientSession(connector=connector) as session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/"
                async with session.get(url) as resp:
                    assert resp.status == 200
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_server_cert_hostname_mismatch_rejected(self, flavor: str) -> None:
        # T1 negative: with hostname checking on, connecting via a URL
        # whose hostname is NOT in the server cert's CN/SAN set must fail.
        # The cert covers MTX-MTX00000 / Matrox.Graphics.Device.Server…
        # but 127.0.0.1 isn't in any SAN.
        server = await _start_tls_server(flavor)
        try:
            client_ctx = build_client_ssl_context(
                flavor=flavor,
                check_hostname=True,
            )
            connector = aiohttp.TCPConnector(ssl=client_ctx)
            async with aiohttp.ClientSession(connector=connector) as session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/"
                with pytest.raises(aiohttp.ClientConnectionError):
                    async with session.get(url):
                        pass
        finally:
            await server.close()

    def test_server_ssl_context_minimum_version_is_tls_1_2(self, flavor: str) -> None:
        # T1: server context MUST require TLS v1.2 or v1.3.
        ctx = build_server_ssl_context(SERIAL, flavor=flavor)
        assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2

    @pytest.mark.asyncio
    async def test_tls_v1_3_negotiates(self, flavor: str) -> None:
        # T1: a client that requires TLS v1.3 completes the handshake.
        # Validates the server doesn't accidentally cap at v1.2.
        server = await _start_tls_server(flavor)
        try:
            client_ctx = build_client_ssl_context(flavor=flavor)
            client_ctx.minimum_version = ssl.TLSVersion.TLSv1_3
            connector = aiohttp.TCPConnector(ssl=client_ctx)
            async with aiohttp.ClientSession(connector=connector) as session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/"
                async with session.get(url) as resp:
                    assert resp.status == 200
        finally:
            await server.close()

    @pytest.mark.asyncio
    async def test_tls_v1_1_refused(self, flavor: str) -> None:
        # T1: a client that pins maximum_version=TLS v1.1 fails the
        # handshake because the server minimum is v1.2.
        server = await _start_tls_server(flavor)
        try:
            client_ctx = build_client_ssl_context(flavor=flavor)
            try:
                client_ctx.maximum_version = ssl.TLSVersion.TLSv1_1
            except ValueError:
                pytest.skip("TLS v1.1 pin not supported by this OpenSSL build")
            connector = aiohttp.TCPConnector(ssl=client_ctx)
            async with aiohttp.ClientSession(connector=connector) as session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/"
                with pytest.raises(aiohttp.ClientConnectionError):
                    async with session.get(url):
                        pass
        finally:
            await server.close()

    def test_chain_pem_contains_intermediate_ca(self, flavor: str) -> None:
        # PKI sanity: the `.chain.pem` MUST contain both the leaf and the
        # intermediate `MatroxProductCA.0.0` PEM blocks. Fails fast if the
        # cert bundle on disk is missing the intermediate append.
        chain_text = server_chain(SERIAL, flavor).read_text()
        begin_count = chain_text.count("-----BEGIN CERTIFICATE-----")
        end_count = chain_text.count("-----END CERTIFICATE-----")
        assert begin_count == end_count
        assert begin_count >= 2, (
            f"{server_chain(SERIAL, flavor).name} must contain leaf + intermediate; "
            f"found {begin_count} PEM block(s)"
        )
        # Sanity: intermediate CA PEM body overlaps the chain
        intermediate = product_ca(flavor).read_text()
        intermediate_body = intermediate.split(
            "-----BEGIN CERTIFICATE-----", 1)[1].split(
            "-----END CERTIFICATE-----", 1)[0].strip()
        assert intermediate_body in chain_text, (
            "chain.pem must embed the MatroxProductCA intermediate cert body"
        )
        # And the root CA cert body MUST NOT be in the chain (per spec —
        # the root belongs in the client's trust store, not on the wire).
        root = root_ca(flavor).read_text()
        root_body = root.split(
            "-----BEGIN CERTIFICATE-----", 1)[1].split(
            "-----END CERTIFICATE-----", 1)[0].strip()
        assert root_body not in chain_text, (
            "chain.pem must NOT embed the MatroxRootCA root cert"
        )
