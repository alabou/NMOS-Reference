# Copyright (C) 2026 Matrox Graphics Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""JWT minting for the IPMX security validator.

The Stage 1 fake AS and the §14 token-validation matrix both need to
emit OAuth 2.0 Bearer tokens whose claims and signing parameters can be
arbitrarily mutated — the spec's adversarial test surface includes
expired ``exp``, missing required claims, wrong ``alg``, mismatched
``client_id``, etc. Real Keycloak refuses to mint such tokens, so the
validator owns its own signing key and shapes the tokens directly.

Layout:

* :class:`SigningKey` — wraps an RSA or EC private key plus the
  ``alg`` it signs with (RS256, RS512, ES256, ES512). Generated at
  validator start-up and cached on disk for the run.
* :func:`mint_token` — build a JWT with sensible default claims, then
  optionally apply a ``mutate`` callback to the claims dict and a
  ``header_mutate`` callback to the JWS header.
* :func:`jwks_for` — serialise the public half of a signing key into
  the standard JWKS shape, suitable for serving from the fake AS's
  ``/jwks`` endpoint.

We use the ``cryptography`` library directly for key generation and
JWT signing; PyJWT is the de-facto Python OAuth library but its API
makes claim mutation awkward (it eagerly canonicalises and signs).
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, cast

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa, padding
from cryptography.hazmat.primitives.asymmetric.types import (
    PrivateKeyTypes, PublicKeyTypes,
)


# ---------------------------------------------------------------------------
# Constants — TR-10-SEC §14.3.3.2 permitted algorithms
# ---------------------------------------------------------------------------

Algorithm = Literal["RS256", "RS512", "ES256", "ES512"]
"""The four ``alg`` values TR-10-SEC §14.3.3.2 permits for Bearer tokens.

ES256 requires the P-256 curve; ES512 requires P-521. RSA algorithms
take a 2048+ bit RSA key (TR-10-SEC §12.5).
"""

ALL_PERMITTED_ALGORITHMS: tuple[Algorithm, ...] = (
    "RS256", "RS512", "ES256", "ES512",
)

# Permitted ``typ`` values per §14.3.3.2.
PERMITTED_TYP_VALUES: frozenset[str] = frozenset({
    "JWT", "at+jwt", "application/at+jwt",
})


# ---------------------------------------------------------------------------
# SigningKey
# ---------------------------------------------------------------------------

@dataclass
class SigningKey:
    """RSA or EC signing key + associated JWS ``alg`` + ``kid``."""
    alg: Algorithm
    kid: str
    private_key: PrivateKeyTypes
    public_key: PublicKeyTypes

    @classmethod
    def generate(cls, alg: Algorithm = "RS256", kid: str = "validator-1") -> "SigningKey":
        """Generate a fresh signing key for ``alg``.

        RSA keys are 2048-bit (TR-10-SEC §12.5 minimum). EC keys use
        the curve mandated by the algorithm: P-256 for ES256, P-521
        for ES512.
        """
        priv: PrivateKeyTypes
        pub: PublicKeyTypes
        if alg in ("RS256", "RS512"):
            rsa_priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            priv = rsa_priv
            pub = rsa_priv.public_key()
        elif alg == "ES256":
            ec_priv = ec.generate_private_key(ec.SECP256R1())
            priv = ec_priv
            pub = ec_priv.public_key()
        elif alg == "ES512":
            ec_priv = ec.generate_private_key(ec.SECP521R1())
            priv = ec_priv
            pub = ec_priv.public_key()
        else:
            raise ValueError(f"unsupported algorithm {alg!r}")
        return cls(alg=alg, kid=kid, private_key=priv, public_key=pub)


# ---------------------------------------------------------------------------
# Base64-url helpers
# ---------------------------------------------------------------------------

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_int(value: int) -> str:
    """Pad-and-encode a Python int as base64url big-endian bytes.

    Used to serialise RSA / EC public-key components into JWK form.
    """
    blen = (value.bit_length() + 7) // 8
    return _b64url(value.to_bytes(blen, "big"))


# ---------------------------------------------------------------------------
# Token minting
# ---------------------------------------------------------------------------

DEFAULT_TTL_SECONDS: int = 3600
"""Default ``exp - iat`` window. Falls within TR-10-SEC §14.3.3.1's
1–24h band so default-minted tokens pass the lifetime check."""


@dataclass(frozen=True)
class TokenTemplate:
    """Default claims for a well-formed TR-10-SEC token.

    The validator copies this template and applies per-test
    mutations on top. Field defaults match what a Configuration B
    DUT (RAAM=1, OAIM=0) would expect:

      - ``iss`` is the validator's issuer URL (taken from the fake AS
        config at run time).
      - ``aud`` contains the DUT's instance-id (the serial number),
        which under OAIM=0 must also match the TLS server cert SAN.
      - ``scope`` includes the NMOS API names the validator probes, plus
        ``query`` for Registry DUTs (see the field below).
      - ``client_id`` matches the OAuth 2.0 client_id the validator
        registered at the fake AS.
      - ``sub == client_id`` so the token is valid as a client-credentials
        grant (TR-10-SEC §14.3.3.3).

    The ``x-nmos-*`` claims are added by mutation when a specific test
    needs them (read/write allow/deny matrices). The default template
    omits them so the spec's scope-grants-default-read path applies.
    """
    iss: str
    instance_id: str
    client_id: str
    # ``query`` is included so the same default token also reaches an NMOS
    # Registry's Query API, which is the other kind of DUT these tokens are
    # pointed at. Without it a client_credentials token authenticates
    # correctly and is then refused 403 "insufficient permissions" on every
    # read, and the Query API's OAuth 2.0 path cannot be exercised at all.
    # Read access is all this grants: per "NMOS With OAuth2.0" § Validation
    # the scope claim provides Read to the API's hierarchy, and Write needs an
    # ``x-nmos-*`` claim that this template deliberately omits.
    scope: str = "node connection streamcompatibility manufacturer query"
    ttl: int = DEFAULT_TTL_SECONDS
    aud_entry: str | None = None
    """The string the validator places in ``aud[0]``. When ``None``,
    falls back to ``instance_id``. Set this to the DUT's hostname (a
    cert SAN that contains the instance_id as a substring) so the
    Node's cert-binding rule from §14.3.3.4 accepts the token —
    a bare serial like ``SNX00001`` is a substring of the SAN but is
    not itself a cert identity, so the bare-serial form fails closed."""


ClaimMutator = Callable[[dict[str, Any]], None]
HeaderMutator = Callable[[dict[str, Any]], None]


def mint_token(
    template: TokenTemplate,
    key: SigningKey,
    *,
    mutate: ClaimMutator | None = None,
    header_mutate: HeaderMutator | None = None,
    now: float | None = None,
    grant_type: str = "client_credentials",
) -> str:
    """Build, sign, and return a JWT for ``template`` + ``key``.

    The default mutation produces a fully-compliant token; pass
    ``mutate`` to break a specific claim ("drop ``exp``", "set ``aud``
    to ``[]``", etc.) and observe the DUT's rejection.

    ``header_mutate`` lets tests change the JWS header — e.g. force
    a ``typ`` value the spec forbids, or override ``alg`` to verify
    the DUT rejects unsupported signature algorithms.

    ``grant_type`` selects which OAuth 2.0 grant the token's claim
    shape models — the validator runs every adversarial probe twice
    (once per grant) so the spec's per-grant rules are exercised:

      ``client_credentials`` (default): ``sub == client_id`` per the
      §14.3.3.3 SHALL. No ``azp`` claim — the OAuth 2.0 client IS
      the principal.

      ``authorization_code``: ``sub`` identifies the end user (not
      the OAuth client) and ``azp`` (Authorized Party, RFC 7519) is
      set to ``client_id``. The fake AS does NOT drive an actual
      login form — we synthesise the resulting claim shape as if
      the user had logged in. The Node side only sees the access
      token and validates claims; how the token was minted is opaque
      to it. (Real-world auth_code drives Keycloak's login form via
      the live AS; the Stage 2 scenarios CSV exercises that path.)
    """
    iat = int(now if now is not None else time.time())
    aud_value = template.aud_entry if template.aud_entry else template.instance_id
    if grant_type == "authorization_code":
        # Synthetic user identifier — keeps the validator headless
        # (no real login flow) while producing a sub ≠ client_id
        # token shape the Node accepts unless it's in cc-only mode.
        sub_value: str = f"user-login-of-{template.client_id}"
    elif grant_type == "client_credentials":
        sub_value = template.client_id
    else:
        raise ValueError(
            f"unknown grant_type {grant_type!r}; expected "
            "'client_credentials' or 'authorization_code'"
        )
    claims: dict[str, Any] = {
        "iss": template.iss,
        "sub": sub_value,
        "client_id": template.client_id,
        "aud": [aud_value],
        "scope": template.scope,
        "exp": iat + template.ttl,
    }
    if grant_type == "authorization_code":
        # RFC 7519 §4.1.1: ``azp`` (Authorized Party) names the
        # OAuth 2.0 client through which the end user authenticated.
        # Node-side validators distinguish the auth_code shape via
        # ``sub != client_id`` and may additionally consult ``azp``.
        claims["azp"] = template.client_id
    if mutate is not None:
        mutate(claims)

    header: dict[str, Any] = {
        "alg": key.alg,
        "typ": "at+jwt",
        "kid": key.kid,
    }
    if header_mutate is not None:
        header_mutate(header)

    return _encode_jws(header, claims, key)


def _encode_jws(
    header: dict[str, Any],
    claims: dict[str, Any],
    key: SigningKey,
) -> str:
    """Serialise + sign a compact-form JWS. Tolerates arbitrary header
    mutations (we don't re-validate ``alg`` here — the validator wants
    to be able to emit deliberately-broken tokens).
    """
    header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
    claims_bytes = json.dumps(claims, separators=(",", ":")).encode("utf-8")
    h64 = _b64url(header_bytes)
    p64 = _b64url(claims_bytes)
    signing_input = f"{h64}.{p64}".encode("ascii")

    # Sign with whatever the header says — if the test forced an
    # alg the key doesn't support, fall back to the key's native alg
    # so the test ends up with a token whose declared alg doesn't
    # match the signature (which is one of the failure modes the
    # validator wants to exercise).
    declared_alg = header.get("alg", key.alg)
    sig = _sign(signing_input, key, str(declared_alg))
    s64 = _b64url(sig)
    return f"{h64}.{p64}.{s64}"


def _sign(data: bytes, key: SigningKey, declared_alg: str) -> bytes:
    """Produce the JWS signature using ``key``'s native algorithm.

    ``declared_alg`` is honoured for hash selection where it makes sense
    (RS256 → SHA-256, RS512 → SHA-512) so a test that mutates the alg
    header still gets a signature consistent with that hash. If the
    declared alg is incompatible with the key type, we fall back to the
    key's native alg — and the DUT will reject the token at the alg
    check, which is exactly what the test wants.
    """
    hash_alg: hashes.HashAlgorithm
    if declared_alg in ("RS256", "ES256"):
        hash_alg = hashes.SHA256()
    elif declared_alg in ("RS512", "ES512"):
        hash_alg = hashes.SHA512()
    else:
        # Unknown alg — sign with the key's native hash. The DUT will
        # reject at the alg whitelist before checking the signature.
        if key.alg.endswith("256"):
            hash_alg = hashes.SHA256()
        else:
            hash_alg = hashes.SHA512()

    if isinstance(key.private_key, rsa.RSAPrivateKey):
        return key.private_key.sign(data, padding.PKCS1v15(), hash_alg)
    if isinstance(key.private_key, ec.EllipticCurvePrivateKey):
        # JWS expects raw R||S concatenation rather than DER-encoded
        # ASN.1; cryptography returns DER, so we decode and re-pack.
        der_sig = key.private_key.sign(data, ec.ECDSA(hash_alg))
        return _ecdsa_der_to_raw(der_sig, key.private_key.curve)
    raise TypeError(
        f"unsupported private-key type {type(key.private_key).__name__}"
    )


def _ecdsa_der_to_raw(der_sig: bytes, curve: ec.EllipticCurve) -> bytes:
    """Convert a DER-encoded ECDSA signature into JWS raw R||S form."""
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    r, s = decode_dss_signature(der_sig)
    # Each integer is padded to the curve's byte length.
    byte_len = (curve.key_size + 7) // 8
    return r.to_bytes(byte_len, "big") + s.to_bytes(byte_len, "big")


# ---------------------------------------------------------------------------
# JWKS export — the fake AS serves this at /jwks
# ---------------------------------------------------------------------------

def jwks_for(*keys: SigningKey) -> dict[str, Any]:
    """Serialise one or more ``SigningKey`` public halves as a JWKS doc.

    The fake AS exposes this at ``/jwks`` (or the URL pointed at by
    its metadata's ``jwks_uri`` field) so the DUT can fetch + cache
    it per TR-10-SEC §14.3.2.
    """
    return {"keys": [_public_jwk(k) for k in keys]}


def _public_jwk(key: SigningKey) -> dict[str, Any]:
    """One JWK entry for ``key``'s public component."""
    pub = key.public_key
    # Distinct names per branch: RSAPublicNumbers and EllipticCurvePublicNumbers
    # are unrelated types, so one shared name binds to whichever branch came
    # first and makes the other branch's field reads unprovable.
    if isinstance(pub, rsa.RSAPublicKey):
        rsa_numbers = pub.public_numbers()
        return {
            "kty": "RSA",
            "alg": key.alg,
            "kid": key.kid,
            "use": "sig",
            "n": _b64url_int(rsa_numbers.n),
            "e": _b64url_int(rsa_numbers.e),
        }
    if isinstance(pub, ec.EllipticCurvePublicKey):
        ec_numbers = pub.public_numbers()
        # JWKS curve names: P-256 / P-521 (RFC 7518 §6.2.1.1).
        curve_name = "P-256" if pub.curve.name == "secp256r1" else "P-521"
        byte_len = (pub.curve.key_size + 7) // 8
        return {
            "kty": "EC",
            "alg": key.alg,
            "kid": key.kid,
            "use": "sig",
            "crv": curve_name,
            "x": _b64url(ec_numbers.x.to_bytes(byte_len, "big")),
            "y": _b64url(ec_numbers.y.to_bytes(byte_len, "big")),
        }
    raise TypeError(f"unsupported public-key type {type(pub).__name__}")


# ---------------------------------------------------------------------------
# Convenience: peek at a token's claims without verifying signature
# ---------------------------------------------------------------------------

def decode_claims(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(header, claims)`` parsed from ``token`` — no signature
    check. Useful for diagnostic logging in test failures.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("not a JWS compact-form token (expected 3 parts)")

    def decode(part: str) -> dict[str, Any]:
        padded = part + "=" * (-len(part) % 4)
        return cast(dict[str, Any], json.loads(base64.urlsafe_b64decode(padded)))

    return decode(parts[0]), decode(parts[1])
