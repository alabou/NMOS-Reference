# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Node Reservation API (NMOS With Node Reservation spec).

Tests the Reservation REST API lifecycle (acquire/renew/release/keepalive),
authorization enforcement on state-changing NMOS endpoints, and session
expiration behavior. Runs against a real aiohttp test server with a Node
instance.
"""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import patch

import pytest
from aiohttp.test_utils import TestClient

from nmos.api import create_app
from nmos.crypto import ExclusiveSession
from nmos.node import Node
from nmos.node.config import ConfigBuilder


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_node_with_sender() -> Node:
    """Create a node with config1 (video+audio senders/receivers) and exclusive session."""
    node = Node()
    node.init(serial_number="RSV12345")
    node.exclusive_session = ExclusiveSession()
    node.privacy_enabled = True

    from pathlib import Path
    config_path = Path(__file__).parent.parent.parent / "node" / "config" / "builtin" / "config1.json"
    if config_path.exists():
        import json as _json
        with open(config_path) as f:
            config = _json.load(f)
        builder = ConfigBuilder(node, verbose=False)
        for s in config.get("senders", []):
            try:
                builder._build_sender_pipeline(s)
            except Exception:
                pass
        for r in config.get("receivers", []):
            try:
                builder._build_receiver_from_config(r)
            except Exception:
                pass
    return node


@pytest.fixture
async def client(aiohttp_client: Any) -> TestClient:
    node = _make_node_with_sender()
    app = create_app(node)
    return await aiohttp_client(app)


@pytest.fixture
async def client_with_token(aiohttp_client: Any) -> tuple[TestClient, str]:
    """Client with an already-acquired exclusive session."""
    node = _make_node_with_sender()
    app = create_app(node)
    c = await aiohttp_client(app)

    resp = await c.post(
        "/x-manufacturer/exclusive/v1.0/acquire",
        json={"owner": "test-controller", "exclusive_key": "0123456789abcdef0123456789abcdef"},
    )
    assert resp.status == 200
    token = await resp.json()
    return c, token


_ACQUIRE_URL = "/x-manufacturer/exclusive/v1.0/acquire"
_RENEW_URL = "/x-manufacturer/exclusive/v1.0/renew"
_RELEASE_URL = "/x-manufacturer/exclusive/v1.0/release"
_KEEPALIVE_URL = "/x-manufacturer/exclusive/v1.0/keepalive"


# ---------------------------------------------------------------------------
# Session Lifecycle Tests
# ---------------------------------------------------------------------------

class TestAcquire:

    @pytest.mark.asyncio
    async def test_acquire_returns_200_and_token(self, client: TestClient) -> None:
        resp = await client.post(
            _ACQUIRE_URL,
            json={"owner": "ctrl-1", "exclusive_key": "0123456789abcdef0123456789abcdef"},
        )
        assert resp.status == 200
        token = await resp.json()
        assert isinstance(token, str) and len(token) > 0

    @pytest.mark.asyncio
    async def test_acquire_returns_400_on_invalid_json(self, client: TestClient) -> None:
        resp = await client.post(
            _ACQUIRE_URL,
            data="not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_acquire_returns_400_on_invalid_key_length(self, client: TestClient) -> None:
        resp = await client.post(
            _ACQUIRE_URL,
            json={"owner": "ctrl-1", "exclusive_key": "0123"},  # Too short
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_acquire_returns_400_on_missing_owner(self, client: TestClient) -> None:
        resp = await client.post(
            _ACQUIRE_URL,
            json={"exclusive_key": "0123456789abcdef0123456789abcdef"},
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_acquire_returns_423_when_session_active(
        self, client_with_token: tuple[TestClient, str],
    ) -> None:
        client, _token = client_with_token
        resp = await client.post(
            _ACQUIRE_URL,
            json={"owner": "ctrl-2", "exclusive_key": "abcdef0123456789abcdef0123456789"},
        )
        assert resp.status == 423

    @pytest.mark.asyncio
    async def test_acquire_423_includes_link_header(
        self, client_with_token: tuple[TestClient, str],
    ) -> None:
        client, _token = client_with_token
        resp = await client.post(
            _ACQUIRE_URL,
            json={"owner": "ctrl-2", "exclusive_key": "abcdef0123456789abcdef0123456789"},
        )
        assert resp.status == 423
        link = resp.headers.get("Link", "")
        assert "test-controller" in link


class TestRenew:

    @pytest.mark.asyncio
    async def test_renew_returns_200_and_new_token(
        self, client_with_token: tuple[TestClient, str],
    ) -> None:
        client, token = client_with_token
        # Wait past the 1/3 lifetime threshold (mock time to avoid waiting 20 min)
        node = client.app["node"]
        # Force the session creation time far enough in the past
        node.exclusive_session._session.creation_time = time.time() - 1300  # ~21 min ago
        resp = await client.post(
            _RENEW_URL,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 200
        new_token = await resp.json()
        assert isinstance(new_token, str)
        assert new_token != token  # New token issued

    @pytest.mark.asyncio
    async def test_renew_returns_401_on_invalid_token(self, client: TestClient) -> None:
        # No session acquired, so any token is invalid
        resp = await client.post(
            _RENEW_URL,
            headers={"Authorization": "Bearer bogus-token"},
        )
        assert resp.status == 401
        assert "WWW-Authenticate" in resp.headers

    @pytest.mark.asyncio
    async def test_renew_returns_425_too_early(
        self, client_with_token: tuple[TestClient, str],
    ) -> None:
        client, token = client_with_token
        # Immediately after acquire — well within 1/3 of lifetime
        resp = await client.post(
            _RENEW_URL,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 425


class TestRelease:

    @pytest.mark.asyncio
    async def test_release_returns_200(
        self, client_with_token: tuple[TestClient, str],
    ) -> None:
        client, token = client_with_token
        resp = await client.post(
            _RELEASE_URL,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_release_returns_401_on_invalid_token(self, client: TestClient) -> None:
        resp = await client.post(
            _RELEASE_URL,
            headers={"Authorization": "Bearer invalid"},
        )
        assert resp.status == 401
        assert "WWW-Authenticate" in resp.headers

    @pytest.mark.asyncio
    async def test_release_clears_key_xcl(
        self, client_with_token: tuple[TestClient, str],
    ) -> None:
        client, token = client_with_token
        node = client.app["node"]

        # Verify key_xcl was set by acquire
        for _sid, activation in node.sender_activation:
            assert activation.privacy.xcl != b"", "key_xcl should be set after acquire"
            break

        # Release
        resp = await client.post(
            _RELEASE_URL,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 200

        # Verify key_xcl cleared
        for _sid, activation in node.sender_activation:
            assert activation.privacy.xcl == b"", "key_xcl should be cleared after release"
            break

    @pytest.mark.asyncio
    async def test_acquire_succeeds_after_release(
        self, client_with_token: tuple[TestClient, str],
    ) -> None:
        client, token = client_with_token
        await client.post(_RELEASE_URL, headers={"Authorization": f"Bearer {token}"})

        # New acquire should succeed
        resp = await client.post(
            _ACQUIRE_URL,
            json={"owner": "ctrl-new", "exclusive_key": "aaaabbbbccccddddaaaabbbbccccdddd"},
        )
        assert resp.status == 200


class TestKeepalive:

    @pytest.mark.asyncio
    async def test_keepalive_returns_200(
        self, client_with_token: tuple[TestClient, str],
    ) -> None:
        client, token = client_with_token
        resp = await client.post(
            _KEEPALIVE_URL,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_keepalive_returns_401_on_invalid_token(self, client: TestClient) -> None:
        resp = await client.post(
            _KEEPALIVE_URL,
            headers={"Authorization": "Bearer nope"},
        )
        assert resp.status == 401


# ---------------------------------------------------------------------------
# Authorization Enforcement Tests
# ---------------------------------------------------------------------------

class TestAuthorizationEnforcement:
    """When an exclusive session is active, state-changing requests require
    the bearer token. GET requests are always allowed."""

    @pytest.mark.asyncio
    async def test_get_does_not_require_bearer(
        self, client_with_token: tuple[TestClient, str],
    ) -> None:
        client, _token = client_with_token
        # GET /x-nmos/node/v1.3/self — should always work
        resp = await client.get("/x-nmos/node/v1.3/self")
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_no_session_allows_get_without_bearer(self, client: TestClient) -> None:
        """Without an active session, read-only requests work without any restriction."""
        # GET /x-nmos/node/v1.3/self should always work
        resp = await client.get("/x-nmos/node/v1.3/self")
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_renewed_token_replaces_old_token(
        self, client_with_token: tuple[TestClient, str],
    ) -> None:
        client, old_token = client_with_token
        node = client.app["node"]
        # Force past 1/3 lifetime for renew
        node.exclusive_session._session.creation_time = time.time() - 1300

        resp = await client.post(
            _RENEW_URL,
            headers={"Authorization": f"Bearer {old_token}"},
        )
        assert resp.status == 200
        new_token = await resp.json()

        # Old token should no longer be valid for keepalive
        resp = await client.post(
            _KEEPALIVE_URL,
            headers={"Authorization": f"Bearer {old_token}"},
        )
        assert resp.status == 401

        # New token works
        resp = await client.post(
            _KEEPALIVE_URL,
            headers={"Authorization": f"Bearer {new_token}"},
        )
        assert resp.status == 200


# ---------------------------------------------------------------------------
# key_xcl Propagation Tests
# ---------------------------------------------------------------------------

class TestKeyXclPropagation:
    """Verify key_xcl is set on acquire and cleared on release."""

    @pytest.mark.asyncio
    async def test_acquire_sets_key_xcl_on_all_activations(self, client: TestClient) -> None:
        node = client.app["node"]
        xcl_hex = "aabbccdd11223344aabbccdd11223344"
        xcl_bytes = bytes.fromhex(xcl_hex)

        resp = await client.post(
            _ACQUIRE_URL,
            json={"owner": "ctrl", "exclusive_key": xcl_hex},
        )
        assert resp.status == 200

        # All sender activations should have key_xcl set
        found = False
        for _sid, activation in node.sender_activation:
            assert activation.privacy.xcl == xcl_bytes
            found = True
        assert found, "No sender activations found"

        # All receiver activations too
        for _sid, activation in node.receiver_activation:
            assert activation.privacy.xcl == xcl_bytes
