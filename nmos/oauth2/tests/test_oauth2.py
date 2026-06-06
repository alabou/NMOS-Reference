# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for nmos.oauth2 — JWT validation and access control."""

from __future__ import annotations

import base64
import json
import time

import pytest

from cryptography.hazmat.primitives.asymmetric import rsa, ec
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.backends import default_backend

from nmos.oauth2 import (
    JWKS,
    JSONWebKey,
    _b64url_decode,
    _parse_jwks,
    validate_token,
    validate_token_with_claims,
    validate_access,
)


# ---------------------------------------------------------------------------
# Test key generation helpers
# ---------------------------------------------------------------------------

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _make_rsa_keypair() -> tuple[rsa.RSAPrivateKey, JSONWebKey]:
    """Generate RSA key pair and return (private_key, jwk)."""
    private_key = rsa.generate_private_key(65537, 2048, default_backend())
    public_key = private_key.public_key()
    pub_numbers = public_key.public_numbers()

    n_bytes = pub_numbers.n.to_bytes((pub_numbers.n.bit_length() + 7) // 8, "big")
    e_bytes = pub_numbers.e.to_bytes((pub_numbers.e.bit_length() + 7) // 8, "big")

    jwk = JSONWebKey(
        kty="RSA", alg="RS256", kid="test-rsa-key", use="sig",
        n=_b64url_encode(n_bytes), e=_b64url_encode(e_bytes),
    )
    return private_key, jwk


def _make_ec_keypair() -> tuple[ec.EllipticCurvePrivateKey, JSONWebKey]:
    """Generate EC P-256 key pair and return (private_key, jwk)."""
    private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
    public_key = private_key.public_key()
    pub_numbers = public_key.public_numbers()

    x_bytes = pub_numbers.x.to_bytes(32, "big")
    y_bytes = pub_numbers.y.to_bytes(32, "big")

    jwk = JSONWebKey(
        kty="EC", alg="ES256", kid="test-ec-key", use="sig",
        x=_b64url_encode(x_bytes), y=_b64url_encode(y_bytes),
    )
    return private_key, jwk


def _sign_jwt_rsa(header: dict, payload: dict, private_key: rsa.RSAPrivateKey) -> str:
    """Create a signed JWT with RS256."""
    h = _b64url_encode(json.dumps(header).encode())
    p = _b64url_encode(json.dumps(payload).encode())
    signed_data = f"{h}.{p}".encode("ascii")

    signature = private_key.sign(signed_data, rsa_padding.PKCS1v15(), hashes.SHA256())
    s = _b64url_encode(signature)
    return f"{h}.{p}.{s}"


def _sign_jwt_ec(header: dict, payload: dict, private_key: ec.EllipticCurvePrivateKey) -> str:
    """Create a signed JWT with ES256."""
    h = _b64url_encode(json.dumps(header).encode())
    p = _b64url_encode(json.dumps(payload).encode())
    signed_data = f"{h}.{p}".encode("ascii")

    der_sig = private_key.sign(signed_data, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der_sig)
    # JWT uses raw r||s format (32 bytes each for P-256)
    raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    sig_b64 = _b64url_encode(raw_sig)
    return f"{h}.{p}.{sig_b64}"


# ---------------------------------------------------------------------------
# JWKS parsing
# ---------------------------------------------------------------------------

class TestJWKSParsing:

    def test_parse_empty(self) -> None:
        jwks = _parse_jwks({"keys": []})
        assert len(jwks.keys) == 0

    def test_parse_rsa_key(self) -> None:
        _, jwk = _make_rsa_keypair()
        jwks = JWKS(keys=[jwk])
        assert jwks.keys[0].kty == "RSA"
        assert jwks.keys[0].kid == "test-rsa-key"


# ---------------------------------------------------------------------------
# JWT signature validation
# ---------------------------------------------------------------------------

class TestJWTValidation:

    def test_rsa256_valid(self) -> None:
        """Valid RS256 token should be accepted."""
        priv, jwk = _make_rsa_keypair()
        jwks = JWKS(keys=[jwk])

        header = {"alg": "RS256", "kid": "test-rsa-key", "typ": "JWT"}
        payload = {"sub": "test", "exp": time.time() + 3600}

        token = _sign_jwt_rsa(header, payload, priv)
        ok, claims = validate_token_with_claims(token, jwks)
        assert ok is True
        assert claims["sub"] == "test"

    def test_rsa256_tampered(self) -> None:
        """Tampered RS256 token should be rejected."""
        priv, jwk = _make_rsa_keypair()
        jwks = JWKS(keys=[jwk])

        # Include ``typ`` so the test exercises the signature-tamper
        # rejection path, not the §14.3.3.2 header validation.
        header = {"alg": "RS256", "kid": "test-rsa-key", "typ": "JWT"}
        payload = {"sub": "test"}
        token = _sign_jwt_rsa(header, payload, priv)

        # Tamper with payload
        parts = token.split(".")
        parts[1] = _b64url_encode(json.dumps({"sub": "hacked"}).encode())
        tampered = ".".join(parts)

        ok, _ = validate_token_with_claims(tampered, jwks)
        assert ok is False

    def test_es256_valid(self) -> None:
        """Valid ES256 token should be accepted."""
        priv, jwk = _make_ec_keypair()
        jwks = JWKS(keys=[jwk])

        header = {"alg": "ES256", "kid": "test-ec-key", "typ": "JWT"}
        payload = {"sub": "ec-test", "exp": time.time() + 3600}

        token = _sign_jwt_ec(header, payload, priv)
        ok, claims = validate_token_with_claims(token, jwks)
        assert ok is True
        assert claims["sub"] == "ec-test"

    def test_unknown_kid_rejected(self) -> None:
        """Token with unknown key ID should be rejected."""
        priv, jwk = _make_rsa_keypair()
        jwks = JWKS(keys=[jwk])

        # Include ``typ`` so the test exercises the kid-mismatch path,
        # not the §14.3.3.2 header validation.
        header = {"alg": "RS256", "kid": "wrong-kid", "typ": "JWT"}
        payload = {"sub": "test"}
        token = _sign_jwt_rsa(header, payload, priv)

        ok, _ = validate_token_with_claims(token, jwks)
        assert ok is False

    def test_malformed_token_rejected(self) -> None:
        ok = validate_token("not.a.valid.jwt.token", JWKS())
        assert ok is False

    def test_validate_token_shortcut(self) -> None:
        priv, jwk = _make_rsa_keypair()
        jwks = JWKS(keys=[jwk])
        header = {"alg": "RS256", "kid": "test-rsa-key", "typ": "JWT"}
        payload = {"sub": "test"}
        token = _sign_jwt_rsa(header, payload, priv)
        assert validate_token(token, jwks) is True

    # -------------------------------------------------------------------
    # VSF TR-10-SEC §14.3.3.2 — JOSE header restrictions
    # -------------------------------------------------------------------

    def test_missing_typ_rejected(self) -> None:
        """Per §14.3.3.2 the typ header must be present."""
        priv, jwk = _make_rsa_keypair()
        jwks = JWKS(keys=[jwk])
        header = {"alg": "RS256", "kid": "test-rsa-key"}  # no typ
        payload = {"sub": "test"}
        token = _sign_jwt_rsa(header, payload, priv)
        ok, _ = validate_token_with_claims(token, jwks)
        assert ok is False

    def test_typ_at_jwt_accepted(self) -> None:
        """``at+jwt`` is one of the three permitted typ values."""
        priv, jwk = _make_rsa_keypair()
        jwks = JWKS(keys=[jwk])
        header = {"alg": "RS256", "kid": "test-rsa-key", "typ": "at+jwt"}
        payload = {"sub": "test"}
        token = _sign_jwt_rsa(header, payload, priv)
        ok, _ = validate_token_with_claims(token, jwks)
        assert ok is True

    def test_typ_application_at_jwt_accepted(self) -> None:
        """``application/at+jwt`` is one of the three permitted typ values."""
        priv, jwk = _make_rsa_keypair()
        jwks = JWKS(keys=[jwk])
        header = {"alg": "RS256", "kid": "test-rsa-key", "typ": "application/at+jwt"}
        payload = {"sub": "test"}
        token = _sign_jwt_rsa(header, payload, priv)
        ok, _ = validate_token_with_claims(token, jwks)
        assert ok is True

    def test_typ_case_insensitive(self) -> None:
        """RFC 7515 §4.1.9: typ comparison is case-insensitive."""
        priv, jwk = _make_rsa_keypair()
        jwks = JWKS(keys=[jwk])
        header = {"alg": "RS256", "kid": "test-rsa-key", "typ": "jWt"}
        payload = {"sub": "test"}
        token = _sign_jwt_rsa(header, payload, priv)
        ok, _ = validate_token_with_claims(token, jwks)
        assert ok is True

    def test_typ_bogus_rejected(self) -> None:
        """A typ value outside the three permitted values is rejected."""
        priv, jwk = _make_rsa_keypair()
        jwks = JWKS(keys=[jwk])
        header = {"alg": "RS256", "kid": "test-rsa-key", "typ": "JWE"}
        payload = {"sub": "test"}
        token = _sign_jwt_rsa(header, payload, priv)
        ok, _ = validate_token_with_claims(token, jwks)
        assert ok is False

    def test_unsupported_alg_rejected(self) -> None:
        """Per §14.3.3.2 only RS256/RS512/ES256/ES512 are permitted."""
        priv, jwk = _make_rsa_keypair()
        jwks = JWKS(keys=[jwk])
        header = {"alg": "HS256", "kid": "test-rsa-key", "typ": "JWT"}
        payload = {"sub": "test"}
        # _sign_jwt_rsa still signs with RSA; the alg mismatch makes the
        # token invalid regardless of signature.
        token = _sign_jwt_rsa(header, payload, priv)
        ok, _ = validate_token_with_claims(token, jwks)
        assert ok is False


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

class TestAccessControl:
    """Unit tests for validate_access. These tests call the function
    directly without wiring up a TLS transport, so they opt into the
    non-TLS test-mode flag (see nmos/config.py) — otherwise the aud
    check correctly fails closed on empty tls_server_cert_names.
    """

    @pytest.fixture(autouse=True)
    def _allow_non_tls(self) -> Any:
        from nmos.config import allow_non_tls_for_testing_context
        with allow_non_tls_for_testing_context():
            yield

    def _make_claims(self, **overrides: Any) -> dict[str, Any]:
        claims = {
            "iss": "https://oauth2.example.com",
            "sub": "controller-1",
            "client_id": "controller-1",
            "aud": ["DEVICE-SN123", "DEVICE-SN456"],
            "exp": time.time() + 3600,
            "scope": "node connection",
        }
        claims.update(overrides)
        return claims

    def _allowed(self, claims: dict, rw: bool, api: str, sn: str) -> bool:
        """Convenience: call validate_access and return just the allowed flag."""
        allowed, _valid = validate_access(claims, rw, api, sn)
        return allowed

    def _valid_token(self, claims: dict, rw: bool, api: str, sn: str) -> bool:
        """Convenience: call validate_access and return just the valid_token flag."""
        _allowed, valid = validate_access(claims, rw, api, sn)
        return valid

    def test_valid_read_access(self) -> None:
        claims = self._make_claims()
        assert self._allowed(claims, False, "node", "DEVICE-SN123")

    def test_expired_token_denied(self) -> None:
        claims = self._make_claims(exp=time.time() - 100)
        assert not self._allowed(claims, False, "node", "DEVICE-SN123")

    def test_wrong_scope_denied(self) -> None:
        claims = self._make_claims(scope="connection")
        assert not self._allowed(claims, False, "node", "DEVICE-SN123")

    def test_wrong_audience_denied(self) -> None:
        claims = self._make_claims()
        assert not self._allowed(claims, False, "node", "UNKNOWN-DEVICE")

    def test_wildcard_audience_allowed(self) -> None:
        claims = self._make_claims(aud=["*"])
        assert self._allowed(claims, False, "node", "ANY-DEVICE")

    def test_write_without_xnmos_denied(self) -> None:
        """Write access without x-nmos-* claim should be denied."""
        claims = self._make_claims()
        assert not self._allowed(claims, True, "node", "DEVICE-SN123")

    def test_write_with_xnmos_allowed(self) -> None:
        claims = self._make_claims(**{
            "x-nmos-node": {"read": ["*"], "write": ["*"]},
        })
        assert self._allowed(claims, True, "node", "DEVICE-SN123")

    def test_xnmos_read_deny(self) -> None:
        """x-nmos-* with read=[""] should deny read."""
        claims = self._make_claims(**{
            "x-nmos-node": {"read": [""]},
        })
        assert not self._allowed(claims, False, "node", "DEVICE-SN123")

    def test_xnmos_in_ext_claim(self) -> None:
        """x-nmos-* claims in ext object should work."""
        claims = self._make_claims(ext={
            "x-nmos-node": {"read": ["*"], "write": ["*"]},
        })
        assert self._allowed(claims, True, "node", "DEVICE-SN123")

    def test_xnmos_read_with_aud_indices(self) -> None:
        """x-nmos-* with aud index array — allow-list."""
        claims = self._make_claims(**{
            "x-nmos-node": {"read": [0]},  # aud[0] = "DEVICE-SN123"
        })
        assert self._allowed(claims, False, "node", "DEVICE-SN123")

    def test_xnmos_read_with_aud_deny_index(self) -> None:
        """x-nmos-* with negative aud index — deny-list."""
        claims = self._make_claims(**{
            "x-nmos-node": {"read": [-1]},  # deny aud[1] = "DEVICE-SN456"
        })
        # DEVICE-SN123 not in deny list → allowed
        assert self._allowed(claims, False, "node", "DEVICE-SN123")
        # DEVICE-SN456 in deny list → denied
        assert not self._allowed(claims, False, "node", "DEVICE-SN456")

    def test_empty_scope_denied(self) -> None:
        claims = self._make_claims(scope="")
        assert not self._allowed(claims, False, "node", "DEVICE-SN123")

    def test_empty_aud_denied(self) -> None:
        claims = self._make_claims(aud=[])
        assert not self._allowed(claims, False, "node", "DEVICE-SN123")

    # --- New tests for spec compliance gaps ---

    def test_missing_iss_claim_rejected(self) -> None:
        """Token without 'iss' claim is invalid (spec: required claims check)."""
        claims = self._make_claims()
        del claims["iss"]
        assert not self._valid_token(claims, False, "node", "DEVICE-SN123")

    def test_wrong_order_indices_rejected(self) -> None:
        """Negative index before positive violates ordering → invalid token."""
        claims = self._make_claims(**{
            "x-nmos-node": {"read": [-1, 0]},  # Wrong order!
        })
        assert not self._valid_token(claims, False, "node", "DEVICE-SN123")

    def test_out_of_bounds_index_rejected(self) -> None:
        """Index beyond aud array length → invalid token."""
        claims = self._make_claims(**{
            "x-nmos-node": {"read": [99]},  # Only 2 entries in aud
        })
        assert not self._valid_token(claims, False, "node", "DEVICE-SN123")
