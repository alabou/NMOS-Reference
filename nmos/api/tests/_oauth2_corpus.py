# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Mint OAuth 2.0 tokens and record what this implementation decides about them.

The Rust registry reimplements ``nmos/oauth2``'s validation half, and "it looks
equivalent" is not evidence. This generator signs real tokens with real keys,
asks *this* module for its verdict on each, and writes both to JSON. The Rust
suite then asserts its own answers match, case for case.

The value is in the cases where the answer is not obvious: an expired token and
one with the wrong scope both fail, but one is 401 and the other 403, and only a
recording of what Python actually returns pins that down.

Regenerate with::

    python -m nmos.api.tests._oauth2_corpus
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils

from nmos.oauth2 import JWKS, JSONWebKey, validate_access, validate_token_with_claims

OUTPUT = (
    Path(__file__).resolve().parents[3]
    / "rust/crates/nmos-registry-http/tests/oauth2_cases.json"
)

# Fixed so the corpus is reproducible: a regenerated file should differ only
# when behaviour differs, not because a fresh key was minted.
_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_EC_KEY = ec.generate_private_key(ec.SECP256R1())

SERIAL = "SNX00000"
CERT_NAMES = ["registry.example.com", "reg-SNX00000.example.com"]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _int_to_b64(value: int, length: int) -> str:
    return _b64(value.to_bytes(length, "big"))


def _jwks() -> tuple[JWKS, dict[str, Any]]:
    """Both representations of the keyset: typed for Python, JSON for Rust."""
    rsa_numbers = _RSA_KEY.public_key().public_numbers()
    ec_numbers = _EC_KEY.public_key().public_numbers()

    rsa_key = JSONWebKey(
        kty="RSA", alg="RS256", kid="rsa-1", use="sig",
        n=_int_to_b64(rsa_numbers.n, 256),
        e=_int_to_b64(rsa_numbers.e, 3),
    )
    ec_key = JSONWebKey(
        kty="EC", alg="ES256", kid="ec-1", use="sig",
        x=_int_to_b64(ec_numbers.x, 32),
        y=_int_to_b64(ec_numbers.y, 32),
    )
    document = {"keys": [
        {"kty": "RSA", "alg": "RS256", "kid": "rsa-1", "use": "sig",
         "n": rsa_key.n, "e": rsa_key.e},
        {"kty": "EC", "alg": "ES256", "kid": "ec-1", "use": "sig",
         "x": ec_key.x, "y": ec_key.y},
    ]}
    return JWKS(keys=[rsa_key, ec_key]), document


def _sign(header: dict[str, Any], claims: dict[str, Any]) -> str:
    """Produce a genuinely signed JWT for the header's algorithm."""
    signing_input = f"{_b64(json.dumps(header).encode())}." \
                    f"{_b64(json.dumps(claims).encode())}"
    data = signing_input.encode("ascii")
    alg = header.get("alg")

    if alg == "RS256":
        signature = _RSA_KEY.sign(data, padding.PKCS1v15(), hashes.SHA256())
    elif alg == "ES256":
        der = _EC_KEY.sign(data, ec.ECDSA(hashes.SHA256()))
        r, s = utils.decode_dss_signature(der)
        # JWS carries r||s fixed-width and raw, not DER.
        signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    else:
        # An unsigned or wrongly-signed token is exactly what several cases
        # need; the bytes only have to be the right shape.
        signature = b"\x00" * 64

    return f"{signing_input}.{_b64(signature)}"


def build() -> dict[str, Any]:
    """Every case, with this implementation's verdict attached."""
    jwks, jwks_document = _jwks()
    # The real clock, deliberately. ``validate_access`` takes no ``now``
    # parameter -- it calls ``time.time()`` itself -- so a fixed timestamp here
    # would make every token expired from its point of view and record a corpus
    # in which nothing is ever allowed. The instant is written to the JSON and
    # the Rust side replays against *it* rather than against its own clock, so
    # the comparison stays exact however long after generation it runs.
    now = time.time()
    live = now + 3600.0

    def claims(**overrides: Any) -> dict[str, Any]:
        base = {
            "iss": "https://as.example.com",
            "sub": "client-1",
            "aud": ["reg-SNX00000.example.com"],
            "client_id": "client-1",
            "exp": live,
            "scope": "query",
        }
        base.update(overrides)
        return base

    jwt = {"typ": "JWT", "alg": "RS256", "kid": "rsa-1"}
    ec_jwt = {"typ": "JWT", "alg": "ES256", "kid": "ec-1"}

    raw: list[tuple[str, dict[str, Any], dict[str, Any], bool]] = [
        # (name, header, claims, read_write)
        ("rs256_read", jwt, claims(), False),
        ("rs256_write_without_private_claim", jwt, claims(), True),
        ("es256_read", ec_jwt, claims(), False),
        ("expired", jwt, claims(exp=now - 1.0), False),
        ("wrong_scope", jwt, claims(scope="connection"), False),
        ("scope_is_whole_words", jwt, claims(scope="querying"), False),
        ("missing_scope_claim", jwt, {k: v for k, v in claims().items()
                                      if k != "scope"}, False),
        ("audience_not_ours", jwt, claims(aud=["someone.else.example.com"]), False),
        ("audience_star", jwt, claims(aud=["*"]), False),
        ("audience_wildcard_dns", jwt, claims(aud=["*.example.com"]), False),
        ("private_claim_star", jwt,
         claims(**{"x-nmos-query": {"read": ["*"], "write": ["*"]}}), True),
        ("private_claim_read_only_write_denied", jwt,
         claims(**{"x-nmos-query": {"read": ["*"]}}), True),
        ("private_claim_under_ext", jwt,
         claims(ext={"x-nmos-query": {"read": ["*"], "write": ["*"]}}), True),
        ("ext_present_shadows_top_level", jwt,
         claims(ext={}, **{"x-nmos-query": {"read": ["*"], "write": ["*"]}}), True),
        ("write_without_read", jwt,
         claims(**{"x-nmos-query": {"read": [""], "write": ["*"]}}), True),
        ("indexed_allow", jwt,
         claims(aud=["*", "other"], **{"x-nmos-query": {"read": [0], "write": [0]}}), True),
        ("indexed_ordering_violation", jwt,
         claims(aud=["*", "other"], **{"x-nmos-query": {"read": [-1, 0]}}), False),
        ("indexed_out_of_bounds", jwt,
         claims(**{"x-nmos-query": {"read": [7]}}), False),
        ("alg_none", {"typ": "JWT", "alg": "none", "kid": "rsa-1"}, claims(), False),
        ("alg_hs256", {"typ": "JWT", "alg": "HS256", "kid": "rsa-1"}, claims(), False),
        ("typ_missing", {"alg": "RS256", "kid": "rsa-1"}, claims(), False),
        ("typ_at_jwt", {"typ": "at+jwt", "alg": "RS256", "kid": "rsa-1"}, claims(), False),
        ("unknown_kid", {"typ": "JWT", "alg": "RS256", "kid": "absent"}, claims(), False),
    ]

    cases: list[dict[str, Any]] = []
    for name, header, payload, read_write in raw:
        token = _sign(header, payload)
        verified, decoded = validate_token_with_claims(token, jwks)
        # Only meaningful once the signature holds, which is why the caller
        # checks `verified` first and the recording keeps them separate.
        allowed, valid_token = validate_access(
            decoded, read_write, "query", SERIAL, CERT_NAMES,
        ) if verified else (False, False)
        cases.append({
            "name": name,
            "token": token,
            "read_write": read_write,
            "verified": verified,
            "allowed": allowed,
            "valid_token": valid_token,
        })

    # A token signed by a key that is not in the keyset: same shape, different
    # issuer. Nothing about it should verify.
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    signing_input = (
        f"{_b64(json.dumps(jwt).encode())}.{_b64(json.dumps(claims()).encode())}"
    )
    forged = (
        f"{signing_input}."
        f"{_b64(other.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256()))}"
    )
    verified, decoded = validate_token_with_claims(forged, jwks)
    cases.append({
        "name": "signed_by_a_stranger",
        "token": forged,
        "read_write": False,
        "verified": verified,
        "allowed": False,
        "valid_token": False,
    })

    return {
        "jwks": jwks_document,
        "now": now,
        "serial_number": SERIAL,
        "tls_server_cert_names": CERT_NAMES,
        "cases": cases,
    }


def main() -> None:
    corpus = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(corpus, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    verified = sum(1 for case in corpus["cases"] if case["verified"])
    print(f"{OUTPUT}: {len(corpus['cases'])} cases, {verified} verifying")


if __name__ == "__main__":
    main()
