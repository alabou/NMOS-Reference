# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""TLS test helpers — SSLContext builders + cert path resolution.

The pre-generated PKI lives outside the repo at
``/home/alain/Projects/IPMX/Certificates/build/`` and contains:

  - ``MatroxRootCA.pem`` / ``.ec.pem``               — root CA
  - ``MatroxProductCA.0.0.pem`` / ``.ec.pem``        — intermediate CA
  - ``pem/MatroxDeviceServer.MTX.MTXnnnnn.pem``      — server leaf (bare)
  - ``pem/MatroxDeviceServer.MTX.MTXnnnnn.chain.pem`` — server leaf + intermediate
  - ``pem/MatroxDeviceClient.MTX.MTXnnnnn.pem``      — client leaf (bare)
  - ``pem/MatroxDeviceClient.MTX.MTXnnnnn.chain.pem`` — client leaf + intermediate
  - ``key/MatroxDeviceServer.MTX.MTXnnnnn.key``      — server private key
  - ``key/MatroxDeviceClient.MTX.MTXnnnnn.key``      — client private key

Each identity has serial numbers ``MTX00000`` … ``MTX00009`` and comes in
RSA (``.pem`` / ``.key``) and EC (``.ec.pem`` / ``.ec.key``) flavours.

The serial is encoded in the cert's subject CN
(``Matrox.Graphics.Device.Server.MTX.MTX00000.matrox.com``) and DNS SANs
(several forms including ``MTX-MTX00000.local``) — this is exactly the
data that OAuth2 `aud` / `client_id` binding uses.
"""

from __future__ import annotations

import ssl
from pathlib import Path
from typing import Literal

# Absolute path to the pre-generated PKI. Tests skip when this directory is
# missing (e.g. the repo is checked out without the sibling Certificates
# workspace).
CERTS_DIR = Path("/home/alain/Projects/IPMX/Certificates/build")

Flavor = Literal["rsa", "ec"]
ClientAuth = Literal["none", "optional", "required"]


def _suffix(flavor: Flavor, extension: str) -> str:
    """Map flavor + extension to the file-suffix convention used on disk.
    RSA files end in ``.<ext>``; EC files end in ``.ec.<ext>``.
    """
    return f"ec.{extension}" if flavor == "ec" else extension


def server_chain(serial: str, flavor: Flavor = "rsa") -> Path:
    """Server cert chain file (leaf + intermediate PEM blocks)."""
    return CERTS_DIR / "pem" / f"MatroxDeviceServer.MTX.{serial}.chain.{_suffix(flavor, 'pem')}"


def server_leaf(serial: str, flavor: Flavor = "rsa") -> Path:
    """Server cert bare leaf (no intermediate)."""
    return CERTS_DIR / "pem" / f"MatroxDeviceServer.MTX.{serial}.{_suffix(flavor, 'pem')}"


def server_key(serial: str, flavor: Flavor = "rsa") -> Path:
    return CERTS_DIR / "key" / f"MatroxDeviceServer.MTX.{serial}.{_suffix(flavor, 'key')}"


def client_chain(serial: str, flavor: Flavor = "rsa") -> Path:
    """Client cert chain file (leaf + intermediate PEM blocks)."""
    return CERTS_DIR / "pem" / f"MatroxDeviceClient.MTX.{serial}.chain.{_suffix(flavor, 'pem')}"


def client_leaf(serial: str, flavor: Flavor = "rsa") -> Path:
    return CERTS_DIR / "pem" / f"MatroxDeviceClient.MTX.{serial}.{_suffix(flavor, 'pem')}"


def client_key(serial: str, flavor: Flavor = "rsa") -> Path:
    return CERTS_DIR / "key" / f"MatroxDeviceClient.MTX.{serial}.{_suffix(flavor, 'key')}"


def root_ca(flavor: Flavor = "rsa") -> Path:
    """Root CA public cert used as the trust anchor on both sides."""
    return CERTS_DIR / ("MatroxRootCA.ec.pem" if flavor == "ec" else "MatroxRootCA.pem")


def product_ca(flavor: Flavor = "rsa") -> Path:
    """Intermediate (product) CA public cert."""
    return CERTS_DIR / ("MatroxProductCA.0.0.ec.pem" if flavor == "ec" else "MatroxProductCA.0.0.pem")


# ---------------------------------------------------------------------------
# DNS SAN / CN resolution (what middleware matches aud / client_id against)
# ---------------------------------------------------------------------------

def server_cert_names(serial: str) -> list[str]:
    """DNS names present in the server cert's CN + SANs for a given serial.

    Mirrors the static content baked by
    ``IPMX/Certificates/genCerts.sh``. Used to seed
    ``node.tls_server_cert_names`` so the OAuth2 audience cross-check
    (``nmos/api/middleware.py:125-127``) can exercise the binding.
    """
    return [
        f"Matrox.Graphics.Device.Server.MTX.{serial}.matrox.com",  # CN + SAN
        "Matrox.Graphics.Device.matrox.com",
        "Matrox.Graphics.Device.Server.matrox.com",
        "Matrox.Graphics.Device.Server.MTX.matrox.com",
        f"MTX-{serial}.local",
        f"MTX-{serial}",
    ]


def client_cert_name(serial: str) -> str:
    """Primary client cert DNS name (CN + SAN) for a given serial.

    Used as the OAuth2 ``client_id`` claim when mTLS is in effect so the
    ``nmos/api/middleware.py:129-136`` client-cert-binding check passes.
    """
    return f"Matrox.Graphics.Device.Client.MTX.{serial}.matrox.com"


# ---------------------------------------------------------------------------
# SSLContext factories
# ---------------------------------------------------------------------------

def build_server_ssl_context(
    serial: str,
    flavor: Flavor = "rsa",
    client_auth: ClientAuth = "none",
) -> ssl.SSLContext:
    """Build a server-side SSLContext configured for the Node API.

    Args:
        serial: Server cert identity (``MTX00000`` .. ``MTX00009``).
        flavor: ``rsa`` or ``ec``.
        client_auth: ``none`` → no client-cert request,
            ``optional`` → request but don't require (verb-gated mTLS),
            ``required`` → require and reject handshake without one.

    Mirrors ``nmos_node.py::build_server_ssl_context`` but as a pure
    function the tests can call without touching CLI argparse.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2  # T1 / T2

    # Server's identity: leaf + intermediate + private key.
    ctx.load_cert_chain(
        certfile=str(server_chain(serial, flavor)),
        keyfile=str(server_key(serial, flavor)),
    )

    if client_auth == "required":
        ctx.verify_mode = ssl.CERT_REQUIRED
    elif client_auth == "optional":
        ctx.verify_mode = ssl.CERT_OPTIONAL
    else:
        ctx.verify_mode = ssl.CERT_NONE

    if client_auth != "none":
        # When asking for a client cert, trust the root so we validate the
        # client's chain up to the same CA that signed our own leaf.
        ctx.load_verify_locations(cafile=str(root_ca(flavor)))

    return ctx


def build_client_ssl_context(
    client_serial: str | None = None,
    flavor: Flavor = "rsa",
    *,
    check_hostname: bool = False,
    cafile: Path | None = None,
) -> ssl.SSLContext:
    """Build a client-side SSLContext for TestClient TCPConnector.

    Args:
        client_serial: if set, present this client identity for mTLS
            (``MTX00000`` .. ``MTX00009``). None → server-auth only.
        flavor: ``rsa`` or ``ec``.
        check_hostname: ``False`` by default — tests bind to
            ``127.0.0.1`` which is not in any SAN. Dedicated hostname-
            mismatch tests override this to exercise Python's default
            hostname-verification behaviour.
        cafile: trust anchor. Defaults to ``MatroxRootCA`` for the chosen
            flavour.

    Mirrors ``nmos_node.py::build_registry_ssl_context`` for the test
    client side of the connection.
    """
    ctx = ssl.create_default_context(
        purpose=ssl.Purpose.SERVER_AUTH,
        cafile=str(cafile) if cafile is not None else str(root_ca(flavor)),
    )
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = check_hostname
    if not check_hostname:
        # When hostname checking is off Python defaults CERT_REQUIRED
        # anyway — keep it explicit so the test intent is clear.
        ctx.verify_mode = ssl.CERT_REQUIRED

    if client_serial is not None:
        ctx.load_cert_chain(
            certfile=str(client_chain(client_serial, flavor)),
            keyfile=str(client_key(client_serial, flavor)),
        )

    return ctx


# ---------------------------------------------------------------------------
# Pre-flight check — skip TLS tests when the PKI is absent
# ---------------------------------------------------------------------------

PKI_AVAILABLE: bool = (
    CERTS_DIR.is_dir()
    and server_chain("MTX00000", "rsa").is_file()
    and server_chain("MTX00000", "ec").is_file()
    and client_chain("MTX00000", "rsa").is_file()
    and client_chain("MTX00000", "ec").is_file()
    and root_ca("rsa").is_file()
    and root_ca("ec").is_file()
)
"""True when the pre-generated PKI is available on disk.

Tests use ``@pytest.mark.skipif(not PKI_AVAILABLE, reason=...)`` to stay
green on systems that don't have the sibling ``Certificates/build``
directory.
"""
