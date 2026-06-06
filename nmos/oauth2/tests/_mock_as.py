# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""In-process mock OAuth2 Authorization Server for E2E tests.

Generates RSA/EC key pairs, signs JWTs with configurable claims, and
optionally serves a JWKS endpoint via aiohttp so the Node can fetch
public keys. Uses PyJWT for JWT signing and the cryptography library
for key generation (same libraries already in the project).

Usage in tests:

    mock_as = MockAuthorizationServer()
    jwks = mock_as.jwks()                 # feed to node.set_oauth2_public_keys()
    token = mock_as.issue_token_for_node(
        node_serial="RSV12345",
        scopes=["node", "connection"],
    )
    # Use token in Authorization: Bearer header
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from typing import Any

import jwt as pyjwt
from cryptography.hazmat.primitives.asymmetric import rsa, ec
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1
from cryptography.hazmat.backends import default_backend

from nmos.oauth2 import JWKS, JSONWebKey


def _b64url(data: bytes) -> str:
    """Base64url encode (no padding)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class MockAuthorizationServer:
    """Mock OAuth2 AS that generates keys and signs JWTs in-process.

    No network server needed — just call ``jwks()`` to get the public
    keys and ``issue_token_*()`` to generate signed tokens.
    """

    def __init__(self, algorithm: str = "RS256") -> None:
        self.algorithm = algorithm
        self.kid = f"test-key-{uuid.uuid4().hex[:8]}"
        self.issuer = "https://mock-oauth2.test/v1.0"

        if algorithm.startswith("RS"):
            self._private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
                backend=default_backend(),
            )
            self._public_key = self._private_key.public_key()
        elif algorithm.startswith("ES"):
            if algorithm == "ES256":
                curve = SECP256R1()
            else:
                from cryptography.hazmat.primitives.asymmetric.ec import SECP521R1
                curve = SECP521R1()
            self._private_key = ec.generate_private_key(curve, default_backend())
            self._public_key = self._private_key.public_key()
        else:
            raise ValueError(f"Unsupported algorithm: {algorithm}")

    def jwks(self) -> JWKS:
        """Return the public keys as a JWKS object that the Node can use."""
        jwk = self._make_jwk()
        return JWKS(keys=[jwk])

    def jwks_json(self) -> dict[str, Any]:
        """Return JWKS as a JSON-serializable dict (for HTTP responses)."""
        jwk = self._make_jwk()
        result: dict[str, Any] = {
            "kty": jwk.kty,
            "alg": jwk.alg,
            "kid": jwk.kid,
            "use": jwk.use,
        }
        if jwk.n:
            result["n"] = jwk.n
            result["e"] = jwk.e
        if jwk.x:
            result["x"] = jwk.x
            result["y"] = jwk.y
        return {"keys": [result]}

    def _make_jwk(self) -> JSONWebKey:
        """Build the JSONWebKey from the generated public key."""
        jwk = JSONWebKey(alg=self.algorithm, kid=self.kid, use="sig")

        if self.algorithm.startswith("RS"):
            pub_numbers = self._public_key.public_numbers()
            n_bytes = pub_numbers.n.to_bytes(
                (pub_numbers.n.bit_length() + 7) // 8, "big"
            )
            e_bytes = pub_numbers.e.to_bytes(
                (pub_numbers.e.bit_length() + 7) // 8, "big"
            )
            jwk.kty = "RSA"
            jwk.n = _b64url(n_bytes)
            jwk.e = _b64url(e_bytes)
        else:
            pub_numbers = self._public_key.public_numbers()
            key_size = (pub_numbers.curve.key_size + 7) // 8
            x_bytes = pub_numbers.x.to_bytes(key_size, "big")
            y_bytes = pub_numbers.y.to_bytes(key_size, "big")
            jwk.kty = "EC"
            jwk.x = _b64url(x_bytes)
            jwk.y = _b64url(y_bytes)

        return jwk

    # ------------------------------------------------------------------
    # Token issuance
    # ------------------------------------------------------------------

    def issue_token(self, claims: dict[str, Any]) -> str:
        """Sign a JWT with the given claims.

        The ``kid`` header is set automatically. The caller provides ALL claims
        including ``iss``, ``exp``, ``aud``, ``sub``, ``client_id``, ``scope``.
        """
        headers = {"kid": self.kid, "typ": "JWT"}
        return pyjwt.encode(claims, self._private_key, algorithm=self.algorithm, headers=headers)

    def issue_token_for_node(
        self,
        node_serial: str,
        scopes: list[str],
        *,
        read_write_apis: dict[str, dict[str, Any]] | None = None,
        client_id: str = "test-controller",
        sub: str | None = None,
        aud: list[str] | None = None,
        lifetime_sec: int = 3600,
        ext: bool = False,
    ) -> str:
        """Convenience: build claims targeting a specific node and sign.

        Args:
            node_serial: BCP-002-02 Instance Identifier (used in aud).
            scopes: Space-separated list of API scopes.
            read_write_apis: Dict of api_name → {read: [...], write: [...]}.
                Placed as x-nmos-{api} claims (or in ext if ext=True).
            client_id: The client_id claim.
            sub: The sub claim. Defaults to client_id (client_credentials grant).
            aud: Explicit audience list. Defaults to [node_serial].
            lifetime_sec: Token lifetime in seconds.
            ext: If True, place x-nmos-* claims in an ext object.
        """
        now = time.time()
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "sub": sub if sub is not None else client_id,
            "client_id": client_id,
            "aud": aud if aud is not None else [node_serial],
            "exp": now + lifetime_sec,
            "iat": now,
            "scope": " ".join(scopes),
        }

        if read_write_apis:
            target = {}
            for api_name, perms in read_write_apis.items():
                target[f"x-nmos-{api_name}"] = perms

            if ext:
                claims["ext"] = target
            else:
                claims.update(target)

        return self.issue_token(claims)

    # ------------------------------------------------------------------
    # Convenience token generators for specific test scenarios
    # ------------------------------------------------------------------

    def make_read_only_token(self, node_serial: str, scopes: list[str]) -> str:
        """Scope-only token — no x-nmos-* claims → read access from scope."""
        return self.issue_token_for_node(node_serial, scopes)

    def make_read_write_token(
        self, node_serial: str, scopes: list[str],
        apis: dict[str, dict[str, Any]] | None = None,
    ) -> str:
        """Token with x-nmos-* read+write access."""
        if apis is None:
            apis = {s: {"read": ["*"], "write": ["*"]} for s in scopes}
        return self.issue_token_for_node(node_serial, scopes, read_write_apis=apis)

    def make_expired_token(self, node_serial: str) -> str:
        """Token with exp in the past."""
        return self.issue_token_for_node(node_serial, ["node"], lifetime_sec=-100)

    def make_wrong_audience_token(self, wrong_serial: str) -> str:
        """Token whose aud doesn't match the target node."""
        return self.issue_token_for_node(wrong_serial, ["node"])

    def make_insufficient_scope_token(self, node_serial: str, wrong_scope: str) -> str:
        """Token with a scope that doesn't match the target API."""
        return self.issue_token_for_node(node_serial, [wrong_scope])

    def make_indexed_aud_token(
        self,
        aud_list: list[str],
        api_name: str,
        read_indices: list[int],
        write_indices: list[int] | None = None,
    ) -> str:
        """Token with integer-indexed read/write arrays referencing aud entries."""
        perms: dict[str, Any] = {"read": read_indices}
        if write_indices is not None:
            perms["write"] = write_indices
        scopes = [api_name]
        return self.issue_token({
            "iss": self.issuer,
            "sub": "test-controller",
            "client_id": "test-controller",
            "aud": aud_list,
            "exp": time.time() + 3600,
            "scope": " ".join(scopes),
            f"x-nmos-{api_name}": perms,
        })
