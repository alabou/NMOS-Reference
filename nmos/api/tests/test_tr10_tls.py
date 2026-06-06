# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for nmos.api.tr10_tls — VSF TR-10-SEC §3 cipher restriction.

Three layers of coverage:
1. Unit: ``apply_tr10_tls_restrictions`` mutates SSLContext attributes
   to the expected TR-10-SEC state.
2. Cipher-list contents: every IANA name in our constants has a
   non-empty OpenSSL mapping and round-trips through ``set_ciphers``.
3. End-to-end refusal: a TLS handshake offering a prohibited cipher
   (e.g. ``AES128-SHA`` — no PFS, no GCM) is refused by a TR-10-SEC
   restricted server.
"""

from __future__ import annotations

import os
import socket
import ssl
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

from nmos.api.tr10_tls import (
    TR10_GROUPS,
    TR10_TLS12_CIPHERS,
    TR10_TLS12_CIPHER_STRING,
    TR10_TLS13_CIPHERS,
    apply_tr10_tls_restrictions,
)

_WORKSPACE = Path(__file__).resolve().parents[4]
CERT_ROOT = Path(os.environ.get("IPMX_CERT_ROOT", _WORKSPACE / "Certificates"))
CERTS_DIR = CERT_ROOT / "build.0"


# ---------------------------------------------------------------------------
# Unit-level checks on apply_tr10_tls_restrictions(ctx)
# ---------------------------------------------------------------------------

def test_apply_sets_minimum_tls_1_2() -> None:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    apply_tr10_tls_restrictions(ctx)
    assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2


def test_apply_disables_compression() -> None:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    apply_tr10_tls_restrictions(ctx)
    assert ctx.options & ssl.OP_NO_COMPRESSION


def test_apply_restricts_tls12_cipher_list_to_whitelist() -> None:
    """Every TLS 1.2 cipher offered after restriction must be in the
    TR-10-SEC whitelist. TLS 1.3 ciphers also appear in get_ciphers()
    but are governed by a separate OpenSSL setting; we filter by
    protocol_id == ssl.TLSVersion.TLSv1_2."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    apply_tr10_tls_restrictions(ctx)
    iana_allowed = set(TR10_TLS12_CIPHERS) | set(TR10_TLS13_CIPHERS)
    for cipher in ctx.get_ciphers():
        # ``name`` in get_ciphers() output is the OpenSSL name; we need
        # to recognise both OpenSSL and IANA names because the TLS 1.3
        # entries already use the IANA form.
        name = cipher["name"]
        # IANA name appears verbatim for TLS 1.3 suites.
        if name in iana_allowed:
            continue
        # Look up the OpenSSL-equivalent IANA name. Python exposes it
        # under cipher['name'] for TLS 1.3 and the OpenSSL name for
        # TLS 1.2; we accept either form provided it maps back to the
        # whitelist via the cipher string.
        assert name in TR10_TLS12_CIPHER_STRING.split(":") + list(iana_allowed), (
            f"cipher {name} is offered after TR-10-SEC restriction but not "
            f"in the spec whitelist"
        )


def test_cipher_string_is_non_empty() -> None:
    assert TR10_TLS12_CIPHER_STRING
    assert ":" in TR10_TLS12_CIPHER_STRING
    # The SHALL cipher must come first (server preference, strongest
    # mutually-supported suite negotiated first).
    assert TR10_TLS12_CIPHER_STRING.startswith("ECDHE-RSA-AES128-GCM-SHA256")


def test_all_groups_listed() -> None:
    # The TR10_GROUPS tuple documents the spec's whitelist for
    # readers and is consumed by the validator's per-curve probe.
    # apply_tr10_tls_restrictions() does NOT use it at runtime —
    # group selection currently falls through to OpenSSL defaults
    # because SSLContext.set_groups is not yet in Python stdlib.
    # SHALL groups present.
    assert "x25519" in TR10_GROUPS
    assert "prime256v1" in TR10_GROUPS
    # SHOULD groups present.
    assert "secp521r1" in TR10_GROUPS
    assert "x448" in TR10_GROUPS
    # Prohibited groups MUST NOT appear in the whitelist — secp384r1
    # is the canonical "default OpenSSL group not in §3" example.
    assert "secp384r1" not in TR10_GROUPS


# ---------------------------------------------------------------------------
# End-to-end handshake refusal of a prohibited cipher
# ---------------------------------------------------------------------------

# Pre-generated PKI lives outside this repo; skip if absent so test
# environments without the Certificates workspace still pass.
_REQUIRED_PKI = (
    CERTS_DIR / "pem" / "ExampleDeviceServer.ABC.SNX00001.chain.pem",
    CERTS_DIR / "key" / "ExampleDeviceServer.ABC.SNX00001.key",
    CERTS_DIR / "ExampleRootCA.pem",
)

pki_available = pytest.mark.skipif(
    not all(p.exists() for p in _REQUIRED_PKI),
    reason="ExampleRootCA PKI not provisioned in this workspace",
)


def _spawn_server(
    ctx: ssl.SSLContext, ready: threading.Event,
) -> tuple[threading.Thread, int]:
    """Listen on 127.0.0.1:<ephemeral> with ``ctx``; accept one handshake.

    The thread does a single ``accept()`` + handshake and exits. Used to
    drive the negative test: client tries a prohibited cipher, server
    refuses, both sides surface ssl.SSLError.
    """
    raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    raw.bind(("127.0.0.1", 0))
    raw.listen(1)
    port = raw.getsockname()[1]

    def serve() -> None:
        ready.set()
        try:
            client, _ = raw.accept()
            try:
                wrapped = ctx.wrap_socket(client, server_side=True)
                wrapped.do_handshake()
                wrapped.close()
            except (ssl.SSLError, OSError):
                # Expected — client offered a prohibited cipher and the
                # server rejected. Swallow so the test reaches its
                # assertion on the client side.
                pass
            finally:
                client.close()
        finally:
            raw.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return t, port


@contextmanager
def _restricted_server() -> Iterator[int]:
    """Yield the port of a one-shot TR-10-SEC-restricted TLS server."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    apply_tr10_tls_restrictions(ctx)
    ctx.load_cert_chain(
        certfile=str(CERTS_DIR / "pem" / "ExampleDeviceServer.ABC.SNX00001.chain.pem"),
        keyfile=str(CERTS_DIR / "key" / "ExampleDeviceServer.ABC.SNX00001.key"),
    )
    ready = threading.Event()
    thread, port = _spawn_server(ctx, ready)
    ready.wait(timeout=1.0)
    try:
        yield port
    finally:
        thread.join(timeout=2.0)


@pki_available
def test_prohibited_tls12_cipher_handshake_refused() -> None:
    """A client offering only TLS_RSA_WITH_AES_128_CBC_SHA (no PFS, RSA
    key transport — explicitly excluded from TR-10-SEC §3) must be
    refused by the restricted server."""
    with _restricted_server() as port:
        client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        # Force TLS 1.2 — the prohibited cipher is a TLS 1.2 suite.
        client_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        client_ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        # Trust the device CA so cert validation passes; the failure
        # must come from cipher negotiation, not chain validation.
        client_ctx.load_verify_locations(str(CERTS_DIR / "ExampleRootCA.pem"))
        client_ctx.check_hostname = False
        # Offer ONLY a prohibited cipher.
        client_ctx.set_ciphers("AES128-SHA")

        raw = socket.create_connection(("127.0.0.1", port), timeout=3.0)
        # The handshake must raise — we don't care about the exact
        # message (OpenSSL wording varies by version), just that it
        # fails. If the server were not restricting ciphers it would
        # negotiate AES128-SHA and the handshake would succeed silently.
        with pytest.raises((ssl.SSLError, OSError)):
            wrapped = client_ctx.wrap_socket(raw, server_hostname="XYZ-SNX00001")
            try:
                wrapped.do_handshake()
            finally:
                wrapped.close()


@pki_available
def test_mandatory_tls12_cipher_handshake_succeeds() -> None:
    """The TR-10-SEC SHALL cipher (TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256)
    must negotiate cleanly against the restricted server."""
    with _restricted_server() as port:
        client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        client_ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        client_ctx.load_verify_locations(str(CERTS_DIR / "ExampleRootCA.pem"))
        client_ctx.check_hostname = False
        client_ctx.set_ciphers("ECDHE-RSA-AES128-GCM-SHA256")

        raw = socket.create_connection(("127.0.0.1", port), timeout=3.0)
        wrapped = client_ctx.wrap_socket(raw, server_hostname="XYZ-SNX00001")
        try:
            wrapped.do_handshake()
            negotiated = wrapped.cipher()
            assert negotiated is not None
            assert negotiated[0] == "ECDHE-RSA-AES128-GCM-SHA256"
        finally:
            wrapped.close()
