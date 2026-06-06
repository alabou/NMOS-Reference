# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for nmos.api — REST API endpoints."""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from nmos.api import create_app
from nmos.crypto import ExclusiveSession
from nmos.node import Node
from nmos.enums import EnumRegistry
from nmos.types.generated.nsender import NSenderValue
from nmos.types.generated.nrtp_sender_transport_params import NRtpSenderTransportParamsValue


def _make_node() -> Node:
    """Create an initialized node with an exclusive session."""
    node = Node()
    node.init(serial_number="TST12345")
    node.exclusive_session = ExclusiveSession()
    return node


# ---------------------------------------------------------------------------
# Test using aiohttp test client
# ---------------------------------------------------------------------------

@pytest.fixture
async def client(aiohttp_client):  # type: ignore
    node = _make_node()
    app = create_app(node)
    return await aiohttp_client(app)


class TestRootEndpoints:

    @pytest.mark.asyncio
    async def test_get_root(self, client) -> None:  # type: ignore
        resp = await client.get("/")
        assert resp.status == 200
        data = await resp.json()
        assert "x-nmos/" in data

    @pytest.mark.asyncio
    async def test_get_xnmos(self, client) -> None:  # type: ignore
        resp = await client.get("/x-nmos")
        assert resp.status == 200
        data = await resp.json()
        assert "node/" in data

    @pytest.mark.asyncio
    async def test_get_node_versions(self, client) -> None:  # type: ignore
        resp = await client.get("/x-nmos/node")
        assert resp.status == 200
        data = await resp.json()
        assert "v1.3/" in data

    @pytest.mark.asyncio
    async def test_get_connection_versions(self, client) -> None:  # type: ignore
        resp = await client.get("/x-nmos/connection")
        assert resp.status == 200
        data = await resp.json()
        assert "v1.1/" in data

    @pytest.mark.asyncio
    async def test_cors_headers(self, client) -> None:  # type: ignore
        resp = await client.get("/")
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"


class TestNodeAPI:

    @pytest.mark.asyncio
    async def test_get_node_v13(self, client) -> None:  # type: ignore
        resp = await client.get("/x-nmos/node/v1.3")
        assert resp.status == 200
        data = await resp.json()
        assert "self/" in data

    @pytest.mark.asyncio
    async def test_get_sources_empty(self, client) -> None:  # type: ignore
        resp = await client.get("/x-nmos/node/v1.3/sources")
        assert resp.status == 200
        data = await resp.json()
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_get_senders_empty(self, client) -> None:  # type: ignore
        resp = await client.get("/x-nmos/node/v1.3/senders")
        assert resp.status == 200
        data = await resp.json()
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_get_receivers_empty(self, client) -> None:  # type: ignore
        resp = await client.get("/x-nmos/node/v1.3/receivers")
        assert resp.status == 200
        data = await resp.json()
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_get_sender_not_found(self, client) -> None:  # type: ignore
        resp = await client.get("/x-nmos/node/v1.3/senders/nonexistent")
        assert resp.status == 404


class TestConnectionAPI:

    @pytest.mark.asyncio
    async def test_get_connection_v11(self, client) -> None:  # type: ignore
        resp = await client.get("/x-nmos/connection/v1.1")
        assert resp.status == 200
        data = await resp.json()
        assert "single/" in data

    @pytest.mark.asyncio
    async def test_get_single_senders(self, client) -> None:  # type: ignore
        resp = await client.get("/x-nmos/connection/v1.1/single/senders")
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_get_single_receivers(self, client) -> None:  # type: ignore
        resp = await client.get("/x-nmos/connection/v1.1/single/receivers")
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_get_single_sender_not_found(self, client) -> None:  # type: ignore
        resp = await client.get("/x-nmos/connection/v1.1/single/senders/nonexistent/")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_get_sender_staged_existing_sender(self, aiohttp_client) -> None:  # type: ignore
        node = _make_node()
        sender = NSenderValue()
        sender.set_to_default()
        sender.Format.value = EnumRegistry.get("urn:x-nmos:format:video")
        sender.Transport.value = EnumRegistry.get("urn:x-nmos:transport:rtp")
        node.add_sender(sender)

        app = create_app(node)
        client = await aiohttp_client(app)

        sender_id = sender.ResourceCore.Id.value
        resp = await client.get(f"/x-nmos/connection/v1.1/single/senders/{sender_id}/staged/")
        assert resp.status == 200
        data = await resp.json()
        assert "transport_params" in data
        assert isinstance(data["transport_params"], list)
        if data["transport_params"]:
            leg0 = data["transport_params"][0]
            assert "source_ip" in leg0
            assert "SourceIp" not in leg0

    @pytest.mark.asyncio
    async def test_get_sender_staged_html_does_not_link_non_paths(self, aiohttp_client) -> None:  # type: ignore
        node = _make_node()
        sender = NSenderValue()
        sender.set_to_default()
        sender.Format.value = EnumRegistry.get("urn:x-nmos:format:video")
        sender.Transport.value = EnumRegistry.get("urn:x-nmos:transport:rtp")
        node.add_sender(sender)

        app = create_app(node)
        client = await aiohttp_client(app)

        sender_id = sender.ResourceCore.Id.value
        resp = await client.get(
            f"/x-nmos/connection/v1.1/single/senders/{sender_id}/staged/",
            headers={"Accept": "text/html"},
        )
        assert resp.status == 200
        body = await resp.text()
        assert "auto" in body
        assert "href=\"/x-nmos/connection/v1.1/single/senders/" not in body

    @pytest.mark.asyncio
    async def test_get_sender_staged_not_found_renders_html_when_requested(self, client) -> None:  # type: ignore
        resp = await client.get(
            "/x-nmos/connection/v1.1/single/senders/nonexistent/staged/",
            headers={"Accept": "text/html"},
        )
        assert resp.status == 404
        assert resp.headers.get("Content-Type", "").startswith("text/html")
        body = await resp.text()
        assert "<!DOCTYPE html>" in body
        assert "sender nonexistent not found" in body

    @pytest.mark.asyncio
    async def test_get_sender_constraints_uses_nmos_json_keys(self, aiohttp_client) -> None:  # type: ignore
        node = _make_node()
        sender = NSenderValue()
        sender.set_to_default()
        sender.Format.value = EnumRegistry.get("urn:x-nmos:format:video")
        sender.Transport.value = EnumRegistry.get("urn:x-nmos:transport:rtp")
        node.add_sender(sender)

        app = create_app(node)
        client = await aiohttp_client(app)

        sender_id = sender.ResourceCore.Id.value
        resp = await client.get(f"/x-nmos/connection/v1.1/single/senders/{sender_id}/constraints/")
        assert resp.status == 200
        data = await resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        leg0 = data[0]
        assert "source_ip" in leg0
        assert "sourceip" not in leg0
        assert "rtcp_destination_port" in leg0
        assert "rtcpdestinationport" not in leg0

    @pytest.mark.asyncio
    async def test_patch_sender_staged_uses_generated_transport_params_types(self, aiohttp_client) -> None:  # type: ignore
        node = _make_node()
        sender = NSenderValue()
        sender.set_to_default()
        sender.Format.value = EnumRegistry.get("urn:x-nmos:format:video")
        sender.Transport.value = EnumRegistry.get("urn:x-nmos:transport:rtp")
        static_id = node.add_sender(sender)

        activation = node.sender_activation.get(static_id)
        assert activation is not None
        assert isinstance(activation.staged[0], NRtpSenderTransportParamsValue)

        app = create_app(node)
        client = await aiohttp_client(app)
        sender_id = sender.ResourceCore.Id.value

        resp = await client.patch(
            f"/x-nmos/connection/v1.1/single/senders/{sender_id}/staged",
            json={"transport_params": [{"destination_ip": "239.1.2.3"}]},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["transport_params"][0]["destination_ip"] == "239.1.2.3"

    @pytest.mark.asyncio
    async def test_get_single_senders_html_links_sender_ids(self, aiohttp_client) -> None:  # type: ignore
        node = _make_node()
        sender = NSenderValue()
        sender.set_to_default()
        sender.Format.value = EnumRegistry.get("urn:x-nmos:format:video")
        sender.Transport.value = EnumRegistry.get("urn:x-nmos:transport:rtp")
        node.add_sender(sender)

        app = create_app(node)
        client = await aiohttp_client(app)

        sender_id = sender.ResourceCore.Id.value
        resp = await client.get(
            "/x-nmos/connection/v1.1/single/senders/",
            headers={"Accept": "text/html"},
        )
        assert resp.status == 200
        body = await resp.text()
        assert f'href="/x-nmos/connection/v1.1/single/senders/{sender_id}/"' in body

    @pytest.mark.asyncio
    async def test_get_single_sender_html_links_subresources(self, aiohttp_client) -> None:  # type: ignore
        node = _make_node()
        sender = NSenderValue()
        sender.set_to_default()
        sender.Format.value = EnumRegistry.get("urn:x-nmos:format:video")
        sender.Transport.value = EnumRegistry.get("urn:x-nmos:transport:rtp")
        node.add_sender(sender)

        app = create_app(node)
        client = await aiohttp_client(app)

        sender_id = sender.ResourceCore.Id.value
        resp = await client.get(
            f"/x-nmos/connection/v1.1/single/senders/{sender_id}/",
            headers={"Accept": "text/html"},
        )
        assert resp.status == 200
        body = await resp.text()
        base = f"/x-nmos/connection/v1.1/single/senders/{sender_id}/"
        assert f'href="{base}staged/"' in body
        assert f'href="{base}active/"' in body
        assert f'href="{base}constraints/"' in body
        assert f'href="{base}transportfile/"' in body
        assert f'href="{base}transporttype/"' in body


class TestExclusiveAPI:

    @pytest.mark.asyncio
    async def test_acquire_release_cycle(self, client) -> None:  # type: ignore
        # Acquire
        resp = await client.post("/x-manufacturer/exclusive/v1.0/acquire", json={
            "owner": "test-controller",
            "exclusive_key": "0123456789abcdef0123456789abcdef",
        })
        assert resp.status == 200
        token = await resp.json()
        assert isinstance(token, str)

        # Keep alive
        resp = await client.post("/x-manufacturer/exclusive/v1.0/keepalive",
                                 headers={"Authorization": f"Bearer {token}"})
        assert resp.status == 200

        # Release
        resp = await client.post("/x-manufacturer/exclusive/v1.0/release",
                                 headers={"Authorization": f"Bearer {token}"})
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_acquire_when_busy(self, client) -> None:  # type: ignore
        # First acquire
        resp = await client.post("/x-manufacturer/exclusive/v1.0/acquire", json={
            "owner": "owner1",
            "exclusive_key": "0123456789abcdef0123456789abcdef",
        })
        assert resp.status == 200

        # Second acquire should fail with 423
        resp = await client.post("/x-manufacturer/exclusive/v1.0/acquire", json={
            "owner": "owner2",
            "exclusive_key": "abcdef0123456789abcdef0123456789",
        })
        assert resp.status == 423

    @pytest.mark.asyncio
    async def test_renew_without_token(self, client) -> None:  # type: ignore
        resp = await client.post("/x-manufacturer/exclusive/v1.0/renew")
        assert resp.status == 401


class TestStreamCompatibility:

    @pytest.mark.asyncio
    async def test_get_compat_v10(self, client) -> None:  # type: ignore
        resp = await client.get("/x-nmos/streamcompatibility/v1.0")
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_get_inputs(self, client) -> None:  # type: ignore
        resp = await client.get("/x-nmos/streamcompatibility/v1.0/inputs")
        assert resp.status == 200
        data = await resp.json()
        assert isinstance(data, list)
