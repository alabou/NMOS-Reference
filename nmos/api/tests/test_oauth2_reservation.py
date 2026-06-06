# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""OAuth2 + Node Reservation coexistence E2E tests.

When OAuth2 is enabled, state-changing requests require BOTH:
1. Authorization: Bearer <JWT> (OAuth2 layer validates first)
2. PEP-Exclusive-Authorization: Bearer <session_token> (exclusive session layer)

Read-only requests need only the OAuth2 JWT.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from aiohttp.test_utils import TestClient

from nmos.api import create_app
from nmos.crypto import ExclusiveSession
from nmos.node import Node
from nmos.oauth2.tests._mock_as import MockAuthorizationServer


NODE_SERIAL = "RSVAUTH"


def _make_node(mock_as: MockAuthorizationServer) -> Node:
    node = Node()
    node.init(serial_number=NODE_SERIAL)
    node.oauth2 = True
    node.exclusive_session = ExclusiveSession()
    node.privacy_enabled = True
    node.set_oauth2_public_keys(mock_as.jwks())
    node.tls_server_cert_names = [NODE_SERIAL]
    node.use_serial_number_in_aud = True
    return node


@pytest.fixture
def mock_as() -> MockAuthorizationServer:
    return MockAuthorizationServer("RS256")


@pytest.fixture
async def client(aiohttp_client: Any, mock_as: MockAuthorizationServer) -> TestClient:
    node = _make_node(mock_as)
    app = create_app(node)
    return await aiohttp_client(app)


_ACQUIRE_URL = "/x-manufacturer/exclusive/v1.0/acquire"
_RELEASE_URL = "/x-manufacturer/exclusive/v1.0/release"
_RENEW_URL = "/x-manufacturer/exclusive/v1.0/renew"
_KEEPALIVE_URL = "/x-manufacturer/exclusive/v1.0/keepalive"


class TestOAuth2ReservationCoexistence:

    @pytest.mark.asyncio
    async def test_acquire_requires_oauth2_token(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        """POST /acquire without OAuth2 JWT → 401 from OAuth2 layer."""
        resp = await client.post(
            _ACQUIRE_URL,
            json={"owner": "ctrl", "exclusive_key": "0" * 32},
        )
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_acquire_with_oauth2_succeeds(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        """POST /acquire with valid JWT (scope=manufacturer) → 200."""
        token = mock_as.make_read_write_token(NODE_SERIAL, ["manufacturer"])
        resp = await client.post(
            _ACQUIRE_URL,
            json={"owner": "ctrl", "exclusive_key": "0" * 32},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_acquire_wrong_scope_returns_403(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        """POST /acquire with scope=node (wrong) → 403."""
        token = mock_as.make_read_write_token(NODE_SERIAL, ["node"])
        resp = await client.post(
            _ACQUIRE_URL,
            json={"owner": "ctrl", "exclusive_key": "0" * 32},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 403

    @pytest.mark.asyncio
    async def test_release_requires_both_headers(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        """Release needs OAuth2 JWT + exclusive session bearer."""
        # First acquire
        oauth_token = mock_as.make_read_write_token(NODE_SERIAL, ["manufacturer"])
        resp = await client.post(
            _ACQUIRE_URL,
            json={"owner": "ctrl", "exclusive_key": "aa" * 16},
            headers={"Authorization": f"Bearer {oauth_token}"},
        )
        assert resp.status == 200
        session_token = await resp.json()

        # Release with only OAuth2 → 401 from exclusive layer (no session bearer)
        resp = await client.post(
            _RELEASE_URL,
            headers={"Authorization": f"Bearer {oauth_token}"},
        )
        # The exclusive session handler checks PEP-Exclusive-Authorization when OAuth2 is on
        assert resp.status == 401

        # Release with both headers → 200
        resp = await client.post(
            _RELEASE_URL,
            headers={
                "Authorization": f"Bearer {oauth_token}",
                "PEP-Exclusive-Authorization": f"Bearer {session_token}",
            },
        )
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_keepalive_requires_oauth2(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        """Keepalive without OAuth2 JWT → 401."""
        resp = await client.post(
            _KEEPALIVE_URL,
            headers={"Authorization": "Bearer fake-session-token"},
        )
        # Without OAuth2 JWT, the OAuth2 middleware rejects first
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_renew_requires_oauth2(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        """Renew without OAuth2 JWT → 401."""
        resp = await client.post(
            _RENEW_URL,
            headers={"Authorization": "Bearer fake-session-token"},
        )
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_get_node_requires_only_oauth2(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        """GET /x-nmos/node/v1.3/self with OAuth2 JWT only → 200 (no session bearer needed for reads)."""
        # Acquire session first to make one active
        oauth_token = mock_as.make_read_write_token(NODE_SERIAL, ["manufacturer", "node"])
        await client.post(
            _ACQUIRE_URL,
            json={"owner": "ctrl", "exclusive_key": "bb" * 16},
            headers={"Authorization": f"Bearer {oauth_token}"},
        )

        # GET with just OAuth2 JWT → 200 (reads don't need session bearer)
        read_token = mock_as.make_read_only_token(NODE_SERIAL, ["node"])
        resp = await client.get(
            "/x-nmos/node/v1.3/self",
            headers={"Authorization": f"Bearer {read_token}"},
        )
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_full_lifecycle_with_both_auth_layers(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        """Full cycle: acquire → keepalive → renew → release, all with both auth layers."""
        oauth_rw = mock_as.make_read_write_token(NODE_SERIAL, ["manufacturer"])

        # Acquire
        resp = await client.post(
            _ACQUIRE_URL,
            json={"owner": "full-cycle-ctrl", "exclusive_key": "cc" * 16},
            headers={"Authorization": f"Bearer {oauth_rw}"},
        )
        assert resp.status == 200
        session_token = await resp.json()

        both_headers = {
            "Authorization": f"Bearer {oauth_rw}",
            "PEP-Exclusive-Authorization": f"Bearer {session_token}",
        }

        # Keepalive
        resp = await client.post(_KEEPALIVE_URL, headers=both_headers)
        assert resp.status == 200

        # Renew (need to be past 1/3 lifetime)
        node = client.app["node"]
        if node.exclusive_session._session is not None:
            node.exclusive_session._session.creation_time = time.time() - 1300
        resp = await client.post(_RENEW_URL, headers=both_headers)
        assert resp.status == 200
        new_session_token = await resp.json()

        # Release with new token
        resp = await client.post(
            _RELEASE_URL,
            headers={
                "Authorization": f"Bearer {oauth_rw}",
                "PEP-Exclusive-Authorization": f"Bearer {new_session_token}",
            },
        )
        assert resp.status == 200
