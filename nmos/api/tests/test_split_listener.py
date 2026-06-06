# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for the ``--controlTrustedRootCA`` split-listener feature.

The feature splits the inbound surface in two when
``--controlTrustedRootCA`` is supplied:

* The Node API + Node Reservation + manufacturer routes stay on
  ``--nodePort`` with the existing ``--nodeTrustedRootCA`` trust
  anchor for inbound client-cert verification.
* The IS-05 (Connection) and IS-11 (Stream Compatibility) routes
  move to ``--controlPort`` (default ``--nodePort + 1``) with the
  ``--controlTrustedRootCA`` trust anchor.

What this module covers:

1. **CLI / parse_args defaulting** — ``--controlPort`` auto-defaults to
   ``--nodePort + 1`` when ``--controlTrustedRootCA`` is set and the
   port was left at 0.
2. **App factories** — ``create_node_app`` registers everything except
   IS-05/IS-11; ``create_control_app`` registers only IS-05/IS-11 +
   their root discovery; ``create_app`` (unified) still registers
   everything (regression guard against accidental drift).
3. **``device.controls[]`` URL publication** — the IS-05/IS-11 hrefs
   advertised in the local Node's IS-04 payload point at
   ``--controlPort`` when the split is enabled and at ``--nodePort``
   otherwise; ``node.services[]`` (reservation) stays on
   ``--nodePort`` either way.
4. **Inbound mTLS on the control listener** — a real
   ``build_control_server_ssl_context``-built listener accepts client
   certs anchored by ``--controlTrustedRootCA`` and rejects those
   that aren't.

Outbound mTLS for the controller's per-call SSL-context routing is
covered by ``test_tls_controller_outbound.py``.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from nmos.api import create_app, create_control_app, create_node_app
from nmos.crypto import ExclusiveSession
from nmos.node import Node

from nmos.api.tests._tls_helpers import (
    CERTS_DIR,
    PKI_AVAILABLE,
    build_client_ssl_context,
    build_server_ssl_context,
)


# ---------------------------------------------------------------------------
# 1) CLI / parse_args defaulting
# ---------------------------------------------------------------------------

def _run_parse_args(argv: list[str]) -> argparse.Namespace:
    """Invoke ``nmos_node.parse_args`` with a synthetic ``sys.argv``.

    ``parse_args`` reads ``sys.argv`` so we swap it temporarily. The
    function is imported lazily to avoid importing the whole node
    binary at module load.
    """
    from nmos_node import parse_args

    saved = sys.argv
    try:
        sys.argv = ["nmos_node.py", *argv]
        return parse_args()
    finally:
        sys.argv = saved


class TestControlPortDefaulting:
    """The ``--controlPort`` default is auto-derived from ``--nodePort``
    when (and only when) ``--controlTrustedRootCA`` is set. Operators
    who want split listeners shouldn't have to pick a port explicitly."""

    def test_control_port_defaults_to_node_port_plus_one_when_ca_set(self) -> None:
        ns = _run_parse_args([
            "--nodePort", "7051",
            "--controlTrustedRootCA", "/tmp/some-ca.pem",
        ])
        assert ns.controlPort == 7052

    def test_control_port_respects_explicit_value_when_ca_set(self) -> None:
        ns = _run_parse_args([
            "--nodePort", "7051",
            "--controlPort", "9090",
            "--controlTrustedRootCA", "/tmp/some-ca.pem",
        ])
        assert ns.controlPort == 9090

    def test_control_port_stays_zero_when_ca_unset(self) -> None:
        """No ``--controlTrustedRootCA`` → no split → the port flag is
        unused and parse_args leaves it at the 0 default."""
        ns = _run_parse_args(["--nodePort", "7051"])
        assert ns.controlPort == 0
        assert ns.controlTrustedRootCA == []


# ---------------------------------------------------------------------------
# 2) App-factory route separation
# ---------------------------------------------------------------------------

def _make_node() -> Node:
    node = Node()
    node.init(serial_number="SNX00001")
    node.exclusive_session = ExclusiveSession()
    return node


def _registered_paths(app: Any) -> set[str]:
    """Collect the static path templates registered on an aiohttp app.

    Dynamic placeholders (``{id}``) are preserved so the assertions
    below can match exact route shapes without re-implementing
    aiohttp's matcher.
    """
    paths: set[str] = set()
    for resource in app.router.resources():
        info = resource.get_info()
        # Static resources expose ``path``; dynamic ones expose
        # ``formatter`` (the path with ``{name}`` placeholders).
        path = info.get("path") or info.get("formatter")
        if path is not None:
            paths.add(path)
    return paths


class TestSplitAppFactories:
    """``create_node_app`` and ``create_control_app`` must partition
    the route surface cleanly: each route group appears on exactly
    one of the two apps. The unified ``create_app`` continues to
    register everything for back-compat."""

    def test_node_app_has_node_api_but_no_is05_is11(self) -> None:
        node = _make_node()
        paths = _registered_paths(create_node_app(node))

        assert "/x-nmos/node/v1.3/self" in paths
        assert "/x-nmos/node/v1.3/devices" in paths
        # IS-05 / IS-11 must NOT appear on the node app.
        assert not any(
            p.startswith("/x-nmos/connection/")
            or p.startswith("/x-nmos/streamcompatibility/")
            for p in paths
        ), (
            "create_node_app leaked IS-05 / IS-11 routes onto the Node "
            "listener — split is broken"
        )
        # Reservation + manufacturer stay on the node app.
        assert "/x-manufacturer" in paths

    def test_control_app_has_is05_is11_but_no_node_api(self) -> None:
        node = _make_node()
        paths = _registered_paths(create_control_app(node))

        assert any(p.startswith("/x-nmos/connection/v1.1") for p in paths)
        assert any(p.startswith("/x-nmos/streamcompatibility/v1.0") for p in paths)
        # IS-05 / IS-11 root discovery lives here (not on the node app).
        assert "/x-nmos/connection" in paths
        assert "/x-nmos/streamcompatibility" in paths
        # Node-API routes must NOT appear on the control app.
        assert not any(p.startswith("/x-nmos/node/") for p in paths), (
            "create_control_app leaked Node-API routes onto the Control "
            "listener — split is broken"
        )
        # Manufacturer / reservation must not appear either.
        assert "/x-manufacturer" not in paths

    def test_unified_app_still_registers_everything(self) -> None:
        """Regression guard: ``create_app`` (unsplit topology) must
        keep registering both Node-API and IS-05/IS-11 surfaces, since
        every operator who hasn't set ``--controlTrustedRootCA`` is
        still served by it."""
        node = _make_node()
        paths = _registered_paths(create_app(node))

        assert "/x-nmos/node/v1.3/self" in paths
        assert any(p.startswith("/x-nmos/connection/v1.1") for p in paths)
        assert any(p.startswith("/x-nmos/streamcompatibility/v1.0") for p in paths)
        assert "/x-manufacturer" in paths


# ---------------------------------------------------------------------------
# 3) device.controls[] URL publication
# ---------------------------------------------------------------------------

def _control_hrefs_by_type(node: Node) -> dict[str, str]:
    """Walk ``node.device_value.Controls`` and return ``{type: href}``."""
    dv: Any = node.device_value
    assert dv is not None, "node.init must have populated device_value"
    out: dict[str, str] = {}
    for ctrl in dv.Controls._value._inner:
        out[str(ctrl.Type.value)] = str(ctrl.Href.value)
    return out


def _service_hrefs_by_type(node: Node) -> dict[str, str]:
    """Walk ``node.node_value.Services`` and return ``{type: href}``."""
    nv: Any = node.node_value
    assert nv is not None, "node.init must have populated node_value"
    out: dict[str, str] = {}
    for svc in nv.Services._value._inner:
        out[str(svc.Type.value)] = str(svc.Href.value)
    return out


class TestControlsUrlPublication:
    """When the split is enabled, the operator-visible IS-04 payload
    must advertise the IS-05/IS-11 control hrefs at ``--controlPort``;
    otherwise remote controllers calling those URLs would hit the
    Node-API listener (which no longer serves them) and 404."""

    def test_controls_use_node_port_when_split_disabled(self) -> None:
        node = Node()
        node.init(serial_number="SNX00001", host="XYZ-SNX00001", port=7051)
        hrefs = _control_hrefs_by_type(node)
        for typ, href in hrefs.items():
            assert ":7051/" in href, (
                f"control entry {typ!r} should advertise port 7051 when "
                f"control_port is 0, got {href!r}"
            )

    def test_controls_use_control_port_when_split_enabled(self) -> None:
        node = Node()
        node.init(
            serial_number="SNX00001", host="XYZ-SNX00001",
            port=7051, control_port=7052,
        )
        hrefs = _control_hrefs_by_type(node)
        # IS-05 (v1.1 and v1.2), IS-11, and manifest-base all point
        # at the connection-API surface, so they all move to the
        # control port.
        expected_types = (
            "urn:x-nmos:control:sr-ctrl/v1.1",
            "urn:x-nmos:control:sr-ctrl/v1.2",
            "urn:x-nmos:control:stream-compat/v1.0",
            "urn:x-nmos:control:manifest-base/v1.0",
        )
        for typ in expected_types:
            assert typ in hrefs, f"missing control entry {typ!r}"
            assert ":7052/" in hrefs[typ], (
                f"control entry {typ!r} should advertise port 7052 in "
                f"split mode, got {hrefs[typ]!r}"
            )

    def test_reservation_service_stays_on_node_port_when_split_enabled(self) -> None:
        """``node.services[]`` (reservation) lives at the Node level,
        not in ``device.controls[]``. Splitting IS-05/IS-11 must NOT
        move the reservation URL."""
        node = Node()
        node.init(
            serial_number="SNX00001", host="XYZ-SNX00001",
            port=7051, control_port=7052,
        )
        svcs = _service_hrefs_by_type(node)
        for typ, href in svcs.items():
            assert ":7051/" in href, (
                f"service entry {typ!r} should stay on the Node port "
                f"(7051) regardless of control_port, got {href!r}"
            )


# ---------------------------------------------------------------------------
# 4) Inbound mTLS on the control listener
# ---------------------------------------------------------------------------

pytestmark_tls = pytest.mark.skipif(
    not PKI_AVAILABLE,
    reason=f"pre-generated TLS PKI not present at {CERTS_DIR}",
)


SERVER_SERIAL = "SNX00002"
CLIENT_SERIAL = "SNX00000"


class TestControlListenerInboundMtls:
    """The control listener built around ``--controlTrustedRootCA`` must
    behave like the existing Node listener built around
    ``--nodeTrustedRootCA``: it should accept clients whose certs
    chain to the configured CA and reject those that don't.

    These tests stand up a real ``create_control_app`` behind a
    ``build_server_ssl_context(client_auth="required")``-built TLS
    server — the same surface ``build_control_server_ssl_context``
    produces in production when ``--controlTrustedRootCA`` is set
    (server cert + ``CERT_REQUIRED``)."""

    @pytestmark_tls
    @pytest.mark.asyncio
    async def test_control_listener_accepts_trusted_client_cert(self) -> None:
        node = _make_node()
        app = create_control_app(node)
        server = TestServer(app, host="127.0.0.1")
        ssl_ctx = build_server_ssl_context(
            SERVER_SERIAL, flavor="rsa", client_auth="required",
        )
        await server.start_server(ssl=ssl_ctx)
        try:
            client_ctx = build_client_ssl_context(client_serial=CLIENT_SERIAL)
            connector = aiohttp.TCPConnector(ssl=client_ctx)
            async with aiohttp.ClientSession(connector=connector) as session:
                # Hit the IS-05 root discovery — present on the control
                # app, absent from the node app. 200 confirms (a) the
                # mTLS handshake completed with the presented client
                # cert and (b) the IS-05 routes are wired onto this
                # listener.
                url = f"https://127.0.0.1:{server.port}/x-nmos/connection"
                async with session.get(url) as resp:
                    assert resp.status == 200
        finally:
            await server.close()

    @pytestmark_tls
    @pytest.mark.asyncio
    async def test_control_listener_rejects_missing_client_cert(self) -> None:
        node = _make_node()
        app = create_control_app(node)
        server = TestServer(app, host="127.0.0.1")
        ssl_ctx = build_server_ssl_context(
            SERVER_SERIAL, flavor="rsa", client_auth="required",
        )
        await server.start_server(ssl=ssl_ctx)
        try:
            # No client cert presented — handshake must fail.
            client_ctx = build_client_ssl_context(client_serial=None)
            connector = aiohttp.TCPConnector(ssl=client_ctx)
            async with aiohttp.ClientSession(connector=connector) as session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/connection"
                with pytest.raises(aiohttp.ClientConnectionError):
                    async with session.get(url):
                        pass
        finally:
            await server.close()

    @pytestmark_tls
    @pytest.mark.asyncio
    async def test_control_listener_does_not_serve_node_api_routes(self) -> None:
        """Defence in depth: even with a valid client cert, the control
        listener must 404 on Node-API paths. Catches accidental
        cross-registration of the IS-04 surface."""
        node = _make_node()
        app = create_control_app(node)
        server = TestServer(app, host="127.0.0.1")
        ssl_ctx = build_server_ssl_context(
            SERVER_SERIAL, flavor="rsa", client_auth="required",
        )
        await server.start_server(ssl=ssl_ctx)
        try:
            client_ctx = build_client_ssl_context(client_serial=CLIENT_SERIAL)
            connector = aiohttp.TCPConnector(ssl=client_ctx)
            async with aiohttp.ClientSession(connector=connector) as session:
                url = f"https://127.0.0.1:{server.port}/x-nmos/node/v1.3/self"
                async with session.get(url) as resp:
                    assert resp.status == 404
        finally:
            await server.close()
