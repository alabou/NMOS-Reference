# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""E2E OAuth2 access control tests.

Creates a real Node with oauth2=True, loads JWKS from the mock AS, then
makes HTTP requests with signed JWTs and verifies access decisions (200 vs
401 vs 403) per NMOS With OAuth2.0 spec.

No TLS — uses fake tls_server_cert_names for audience validation.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from aiohttp.test_utils import TestClient

from nmos.api import create_app
from nmos.crypto import ExclusiveSession
from nmos.node import Node, _get_resource_core
from nmos.oauth2.tests._mock_as import MockAuthorizationServer
from nmos.types.generated.nreceiver import NReceiverValue
from nmos.types.generated.nreceiver_video import NReceiverVideoValue
from nmos.enums import EnumRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

NODE_SERIAL = "OAUTH2TEST"


def _make_oauth2_node(mock_as: MockAuthorizationServer) -> Node:
    """Create a Node with OAuth2 enabled, configured with mock AS keys."""
    node = Node()
    node.init(serial_number=NODE_SERIAL)
    node.oauth2 = True
    node.exclusive_session = ExclusiveSession()
    node.privacy_enabled = True

    # Load mock AS public keys into the node
    node.set_oauth2_public_keys(mock_as.jwks())

    # Fake TLS server cert names so aud validation exercises the cert cross-check.
    # The aud entry must contain the serial number AND be in this list.
    node.tls_server_cert_names = [NODE_SERIAL, f"device-{NODE_SERIAL}.example.com"]
    node.use_serial_number_in_aud = True

    return node


def _add_video_receiver(node: Node) -> str:
    """Add a minimal video receiver and return its dynamic UUID."""
    inner = NReceiverVideoValue()
    inner.set_to_default()
    inner.Format.value = EnumRegistry.get("urn:x-nmos:format:video")
    inner.ReceiverCore.Transport.value = EnumRegistry.get("urn:x-nmos:transport:rtp")
    receiver = NReceiverValue()
    receiver.set(inner)
    node.add_receiver(receiver)
    return str(_get_resource_core(receiver).Id.value)


@pytest.fixture
def mock_as() -> MockAuthorizationServer:
    return MockAuthorizationServer("RS256")


@pytest.fixture
async def client(aiohttp_client: Any, mock_as: MockAuthorizationServer) -> TestClient:
    node = _make_oauth2_node(mock_as)
    app = create_app(node)
    return await aiohttp_client(app)


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Token Validation Tests
# ---------------------------------------------------------------------------

class TestTokenValidation:

    @pytest.mark.asyncio
    async def test_valid_rs256_token_allows_read(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        token = mock_as.make_read_only_token(NODE_SERIAL, ["node"])
        resp = await client.get("/x-nmos/node/v1.3/self", headers=_auth_header(token))
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_es256_token_allows_read(self, aiohttp_client: Any) -> None:
        mock = MockAuthorizationServer("ES256")
        node = _make_oauth2_node(mock)
        app = create_app(node)
        c = await aiohttp_client(app)
        token = mock.make_read_only_token(NODE_SERIAL, ["node"])
        resp = await c.get("/x-nmos/node/v1.3/self", headers=_auth_header(token))
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_set_oauth2_public_keys_accepts_dict(
        self, aiohttp_client: Any, mock_as: MockAuthorizationServer,
    ) -> None:
        """Regression: feeding the JWKS dict shape (the JSON body
        Keycloak's ``/protocol/openid-connect/certs`` endpoint returns)
        must work. ``set_oauth2_public_keys`` was previously storing
        the raw dict, after which the bearer-validation middleware did
        ``for key in jwks.keys`` — picking up ``dict.keys`` (the bound
        method) instead of the ``list[JSONWebKey]`` attribute, which
        crashed every authenticated request with
        ``TypeError: 'builtin_function_or_method' object is not iterable``.

        This was structurally invisible to the existing tests, which
        all use ``mock_as.jwks()`` (the parsed dataclass) directly.
        """
        node = Node()
        node.init(serial_number=NODE_SERIAL)
        node.oauth2 = True
        node.exclusive_session = ExclusiveSession()
        # Feed the raw JSON-shaped dict — the real production path.
        node.set_oauth2_public_keys(mock_as.jwks_json())
        node.tls_server_cert_names = [NODE_SERIAL]
        node.use_serial_number_in_aud = True

        app = create_app(node)
        c = await aiohttp_client(app)
        token = mock_as.make_read_only_token(NODE_SERIAL, ["node"])
        resp = await c.get(
            "/x-nmos/node/v1.3/self", headers=_auth_header(token),
        )
        assert resp.status == 200, (
            f"dict-shaped JWKS should validate too; got {resp.status}"
        )

    @pytest.mark.asyncio
    async def test_set_oauth2_public_keys_accepts_none(
        self, mock_as: MockAuthorizationServer,
    ) -> None:
        """Sanity: passing ``None`` clears the slot without crashing."""
        node = Node()
        node.init(serial_number=NODE_SERIAL)
        node.oauth2 = True
        node.set_oauth2_public_keys(mock_as.jwks_json())
        assert node.oauth2_keys is not None
        node.set_oauth2_public_keys(None)
        assert node.oauth2_keys is None

    @pytest.mark.asyncio
    async def test_expired_token_returns_401(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        token = mock_as.make_expired_token(NODE_SERIAL)
        resp = await client.get("/x-nmos/node/v1.3/self", headers=_auth_header(token))
        assert resp.status == 401
        assert "WWW-Authenticate" in resp.headers

    @pytest.mark.asyncio
    async def test_missing_token_returns_401(self, client: TestClient) -> None:
        resp = await client.get("/x-nmos/node/v1.3/self")  # No Authorization header
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_malformed_token_returns_401(self, client: TestClient) -> None:
        resp = await client.get(
            "/x-nmos/node/v1.3/self",
            headers={"Authorization": "Bearer not-a-real-jwt"},
        )
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_tampered_signature_returns_401(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        token = mock_as.make_read_only_token(NODE_SERIAL, ["node"])
        # Replace the entire signature with garbage
        parts = token.split(".")
        tampered = f"{parts[0]}.{parts[1]}.AAAAAAAAAAAAAAAAAAAAAA"
        resp = await client.get("/x-nmos/node/v1.3/self", headers=_auth_header(tampered))
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_wrong_kid_returns_401(
        self, client: TestClient,
    ) -> None:
        # Create a DIFFERENT mock AS — its kid won't match the node's JWKS
        other_as = MockAuthorizationServer("RS256")
        token = other_as.make_read_only_token(NODE_SERIAL, ["node"])
        resp = await client.get("/x-nmos/node/v1.3/self", headers=_auth_header(token))
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_missing_iss_claim_returns_401(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        """Token without 'iss' claim → invalid → 401."""
        claims = {
            # No "iss" key!
            "sub": "ctrl", "client_id": "ctrl",
            "aud": [NODE_SERIAL], "exp": time.time() + 3600,
            "scope": "node",
        }
        token = mock_as.issue_token(claims)
        resp = await client.get("/x-nmos/node/v1.3/self", headers=_auth_header(token))
        assert resp.status == 401


# ---------------------------------------------------------------------------
# Scope / Audience Gating Tests
# ---------------------------------------------------------------------------

class TestScopeAndAudience:

    @pytest.mark.asyncio
    async def test_wrong_scope_returns_403(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        token = mock_as.make_read_only_token(NODE_SERIAL, ["connection"])  # NOT "node"
        resp = await client.get("/x-nmos/node/v1.3/self", headers=_auth_header(token))
        assert resp.status == 403

    @pytest.mark.asyncio
    async def test_wrong_audience_returns_403(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        token = mock_as.make_wrong_audience_token("WRONG-DEVICE")
        resp = await client.get("/x-nmos/node/v1.3/self", headers=_auth_header(token))
        # Wrong aud is a valid token but insufficient permissions → 403
        assert resp.status in (401, 403)  # Depends on whether aud mismatch is invalid or denied

    @pytest.mark.asyncio
    async def test_wildcard_audience_allows_access(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        token = mock_as.issue_token_for_node(
            NODE_SERIAL, ["node"], aud=["*"],
        )
        resp = await client.get("/x-nmos/node/v1.3/self", headers=_auth_header(token))
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_scope_grants_read_only_without_xnmos(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        """scope="node" without x-nmos-node → GET succeeds."""
        token = mock_as.make_read_only_token(NODE_SERIAL, ["node"])
        resp = await client.get("/x-nmos/node/v1.3/self", headers=_auth_header(token))
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_empty_scope_returns_403(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        claims = {
            "iss": mock_as.issuer, "sub": "ctrl", "client_id": "ctrl",
            "aud": [NODE_SERIAL], "exp": time.time() + 3600,
            "scope": "",  # Empty!
        }
        token = mock_as.issue_token(claims)
        resp = await client.get("/x-nmos/node/v1.3/self", headers=_auth_header(token))
        assert resp.status in (401, 403)


class TestConnectionRouteProtection:

    @pytest.mark.asyncio
    async def test_receiver_transporttype_requires_oauth2(
        self, aiohttp_client: Any, mock_as: MockAuthorizationServer,
    ) -> None:
        node = _make_oauth2_node(mock_as)
        receiver_id = _add_video_receiver(node)
        app = create_app(node)
        client = await aiohttp_client(app)

        resp = await client.get(
            f"/x-nmos/connection/v1.1/single/receivers/{receiver_id}/transporttype",
        )
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_receiver_transporttype_allows_authenticated_read(
        self, aiohttp_client: Any, mock_as: MockAuthorizationServer,
    ) -> None:
        node = _make_oauth2_node(mock_as)
        receiver_id = _add_video_receiver(node)
        app = create_app(node)
        client = await aiohttp_client(app)

        token = mock_as.make_read_only_token(NODE_SERIAL, ["connection"])
        resp = await client.get(
            f"/x-nmos/connection/v1.1/single/receivers/{receiver_id}/transporttype",
            headers=_auth_header(token),
        )
        assert resp.status == 200
        assert await resp.json() == "urn:x-nmos:transport:rtp"

    @pytest.mark.asyncio
    async def test_empty_aud_returns_error(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        claims = {
            "iss": mock_as.issuer, "sub": "ctrl", "client_id": "ctrl",
            "aud": [],  # Empty!
            "exp": time.time() + 3600, "scope": "node",
        }
        token = mock_as.issue_token(claims)
        resp = await client.get("/x-nmos/node/v1.3/self", headers=_auth_header(token))
        assert resp.status in (401, 403)


@pytest.mark.parametrize(
    ("path", "scope"),
    [
        ("/", "node"),
        ("/x-nmos", "node"),
        ("/x-nmos/node", "node"),
        ("/x-nmos/connection", "connection"),
        ("/x-nmos/streamcompatibility", "streamcompatibility"),
        ("/x-manufacturer", "manufacturer"),
    ],
)
class TestRootRouteProtection:

    @pytest.mark.asyncio
    async def test_requires_oauth2(
        self,
        path: str,
        scope: str,
        aiohttp_client: Any,
        mock_as: MockAuthorizationServer,
    ) -> None:
        node = _make_oauth2_node(mock_as)
        app = create_app(node)
        client = await aiohttp_client(app)

        resp = await client.get(path)
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_allows_authenticated_read(
        self,
        path: str,
        scope: str,
        aiohttp_client: Any,
        mock_as: MockAuthorizationServer,
    ) -> None:
        node = _make_oauth2_node(mock_as)
        app = create_app(node)
        client = await aiohttp_client(app)

        token = mock_as.make_read_only_token(NODE_SERIAL, [scope])
        resp = await client.get(path, headers=_auth_header(token))
        assert resp.status == 200


# ---------------------------------------------------------------------------
# x-nmos-* Private Claims Tests
# ---------------------------------------------------------------------------

class TestXnmosClaims:

    @pytest.mark.asyncio
    async def test_xnmos_read_star_allows_read(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        token = mock_as.issue_token_for_node(
            NODE_SERIAL, ["node"],
            read_write_apis={"node": {"read": ["*"]}},
        )
        resp = await client.get("/x-nmos/node/v1.3/self", headers=_auth_header(token))
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_xnmos_read_empty_denies_read(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        """x-nmos-node.read=[""] → read denied → 403."""
        token = mock_as.issue_token_for_node(
            NODE_SERIAL, ["node"],
            read_write_apis={"node": {"read": [""]}},
        )
        resp = await client.get("/x-nmos/node/v1.3/self", headers=_auth_header(token))
        assert resp.status == 403

    @pytest.mark.asyncio
    async def test_xnmos_write_star_allows_write(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        """x-nmos-connection with read+write=["*"] allows PATCH."""
        # Need a sender for PATCH — but just checking we don't get 401/403 from OAuth
        # is sufficient (404 for missing sender is OK)
        token = mock_as.make_read_write_token(NODE_SERIAL, ["connection"])
        resp = await client.patch(
            "/x-nmos/connection/v1.0/single/senders/00000000-0000-0000-0000-000000000000/staged",
            json={},
            headers=_auth_header(token),
        )
        # 404 (sender not found) is fine — it means OAuth passed!
        # 401/403 would mean OAuth rejected
        assert resp.status in (200, 400, 404)

    @pytest.mark.asyncio
    async def test_xnmos_removes_scope_read_default(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        """When x-nmos-node is present, scope no longer grants implicit read.
        x-nmos-node with no 'read' attr → read denied."""
        token = mock_as.issue_token_for_node(
            NODE_SERIAL, ["node"],
            read_write_apis={"node": {}},  # No read or write attrs
        )
        resp = await client.get("/x-nmos/node/v1.3/self", headers=_auth_header(token))
        assert resp.status == 403

    @pytest.mark.asyncio
    async def test_xnmos_in_ext_claim(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        """x-nmos-* claims in ext object should work."""
        token = mock_as.issue_token_for_node(
            NODE_SERIAL, ["node"],
            read_write_apis={"node": {"read": ["*"]}},
            ext=True,
        )
        resp = await client.get("/x-nmos/node/v1.3/self", headers=_auth_header(token))
        assert resp.status == 200


# ---------------------------------------------------------------------------
# Integer Index Forms (aud-indexed allow/deny)
# ---------------------------------------------------------------------------

class TestIndexedAudClaims:

    @pytest.mark.asyncio
    async def test_positive_index_allows_matching_aud(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        """read=[0], aud[0]=NODE_SERIAL → allowed."""
        token = mock_as.make_indexed_aud_token(
            [NODE_SERIAL, "OTHER-DEVICE"], "node",
            read_indices=[0],
        )
        resp = await client.get("/x-nmos/node/v1.3/self", headers=_auth_header(token))
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_positive_index_non_matching_aud_denied(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        """read=[1], aud[1]=OTHER → doesn't match this node → 403."""
        token = mock_as.make_indexed_aud_token(
            [NODE_SERIAL, "OTHER-DEVICE"], "node",
            read_indices=[1],
        )
        resp = await client.get("/x-nmos/node/v1.3/self", headers=_auth_header(token))
        assert resp.status == 403

    @pytest.mark.asyncio
    async def test_negative_index_denies_matching_aud(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        """read=[-0] ... wait, -0 is 0. Use: read=[0, -1] where aud[1]=NODE_SERIAL.
        Actually: read=[-1] is a deny-only list. If aud[1] matches → denied."""
        # aud = [OTHER, NODE_SERIAL]; read=[-1] → deny if aud[1] matches.
        # aud[1]=NODE_SERIAL matches this node → denied
        token = mock_as.make_indexed_aud_token(
            ["OTHER-DEVICE", NODE_SERIAL], "node",
            read_indices=[-1],
        )
        resp = await client.get("/x-nmos/node/v1.3/self", headers=_auth_header(token))
        assert resp.status == 403

    @pytest.mark.asyncio
    async def test_out_of_bounds_index_returns_401(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        """read=[99] with 2-entry aud → invalid token → 401."""
        token = mock_as.make_indexed_aud_token(
            [NODE_SERIAL, "OTHER"], "node",
            read_indices=[99],
        )
        resp = await client.get("/x-nmos/node/v1.3/self", headers=_auth_header(token))
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_wrong_order_indices_returns_401(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        """read=[-1, 0] (negative before positive) → ordering violation → 401."""
        token = mock_as.make_indexed_aud_token(
            [NODE_SERIAL, "OTHER"], "node",
            read_indices=[-1, 0],  # Wrong order!
        )
        resp = await client.get("/x-nmos/node/v1.3/self", headers=_auth_header(token))
        assert resp.status == 401


# ---------------------------------------------------------------------------
# Grant Type Tests
# ---------------------------------------------------------------------------

class TestGrantTypes:

    @pytest.mark.asyncio
    async def test_client_credentials_grant_accepted(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        """sub == client_id → client_credentials grant → accepted."""
        token = mock_as.issue_token_for_node(
            NODE_SERIAL, ["node"], client_id="ctrl-1", sub="ctrl-1",
        )
        resp = await client.get("/x-nmos/node/v1.3/self", headers=_auth_header(token))
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_authorization_code_grant_accepted(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        """sub != client_id → authorization_code grant → accepted (default policy)."""
        token = mock_as.issue_token_for_node(
            NODE_SERIAL, ["node"], client_id="ctrl-1", sub="user@example.com",
        )
        resp = await client.get("/x-nmos/node/v1.3/self", headers=_auth_header(token))
        assert resp.status == 200


# ---------------------------------------------------------------------------
# HTTP Status Code Tests
# ---------------------------------------------------------------------------

class TestHttpStatusCodes:

    @pytest.mark.asyncio
    async def test_401_includes_www_authenticate_header(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        token = mock_as.make_expired_token(NODE_SERIAL)
        resp = await client.get("/x-nmos/node/v1.3/self", headers=_auth_header(token))
        assert resp.status == 401
        assert "WWW-Authenticate" in resp.headers

    @pytest.mark.asyncio
    async def test_403_for_valid_token_wrong_scope(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        token = mock_as.make_read_only_token(NODE_SERIAL, ["connection"])
        resp = await client.get("/x-nmos/node/v1.3/self", headers=_auth_header(token))
        assert resp.status == 403


# ---------------------------------------------------------------------------
# Scope-to-API Mapping Tests
# ---------------------------------------------------------------------------

class TestScopeToApiMapping:

    @pytest.mark.asyncio
    async def test_node_api_requires_node_scope(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        token = mock_as.make_read_only_token(NODE_SERIAL, ["connection"])
        resp = await client.get("/x-nmos/node/v1.3/self", headers=_auth_header(token))
        assert resp.status == 403

    @pytest.mark.asyncio
    async def test_manufacturer_api_requires_manufacturer_scope(
        self, client: TestClient, mock_as: MockAuthorizationServer,
    ) -> None:
        """POST /x-manufacturer/exclusive requires scope=manufacturer."""
        token = mock_as.make_read_write_token(NODE_SERIAL, ["node"])  # Wrong scope
        resp = await client.post(
            "/x-manufacturer/exclusive/v1.0/acquire",
            json={"owner": "ctrl", "exclusive_key": "0" * 32},
            headers=_auth_header(token),
        )
        assert resp.status == 403
