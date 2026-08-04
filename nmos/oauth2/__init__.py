# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""OAuth2 JWT validation, JWKS fetching, and access control for NMOS.

Provides:
- JWKS key fetching from authorization server
- JWT token signature validation (RS256, RS512, ES256, ES512)
- Access control claims validation (exp, aud, scope, x-nmos-* permissions)
- Token acquisition flows (client_credentials, authorization_code, refresh)

Per NMOS With OAuth2.0 spec:
- Token type MUST be JWT
- Algorithms: RS256, RS512, ES256, ES512
- Required claims: iss, aud, sub, exp, scope, client_id
- Private claims x-nmos-* may be in top-level or ext claim
- Access control uses read/write arrays with aud index references

Base functionality only — HTTP API middleware is a separate phase.
"""

from __future__ import annotations

import base64
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec, padding, utils
from cryptography.hazmat.primitives.asymmetric.ec import (
    SECP256R1,
    SECP521R1,
    EllipticCurvePublicKey,
)
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey, RSAPublicNumbers
from cryptography.hazmat.primitives.hashes import SHA256, SHA512
from cryptography.hazmat.backends import default_backend

from nmos.errors import InvalidData, NotAllowed
from nmos.json.engine import JsonEngine


# ---------------------------------------------------------------------------
# TR-10-SEC §14.3.3.2 — permitted JOSE header values
# ---------------------------------------------------------------------------

_PERMITTED_TYP_VALUES: frozenset[str] = frozenset({
    "jwt", "at+jwt", "application/at+jwt",
})
"""Lower-cased set of acceptable ``typ`` values. Comparison is case-insensitive
per RFC 7515 §4.1.9."""

_PERMITTED_TOKEN_ALGORITHMS: frozenset[str] = frozenset({
    "RS256", "RS512", "ES256", "ES512",
})
"""TR-10-SEC §14.3.3.2 token-signing algorithm whitelist. The spec extends
IS-10's RS512-only list to four algorithms; Nodes claiming compliance
shall validate Bearer tokens using all of them."""


# ---------------------------------------------------------------------------
# JWKS types
# ---------------------------------------------------------------------------

@dataclass
class JSONWebKey:
    """A single key from a JWKS keyset."""
    kty: str = ""     # Key type: RSA, EC
    alg: str = ""     # Algorithm: RS256, RS512, ES256, ES512
    kid: str = ""     # Key ID
    use: str = ""     # Usage: sig
    n: str = ""       # RSA modulus (base64url)
    e: str = ""       # RSA exponent (base64url)
    x: str = ""       # EC X coordinate (base64url)
    y: str = ""       # EC Y coordinate (base64url)


@dataclass
class JWKS:
    """JSON Web Key Set — a collection of public keys."""
    keys: list[JSONWebKey] = field(default_factory=list)


# Configuration
USE_CLIENT_CREDENTIALS_GRANT_ONLY: bool = False

DEFAULT_APIS: list[str] = [
    "node", "connection", "streamcompatibility",
    "ncWebSocket", "ncWebSocketGuest",
]


# ---------------------------------------------------------------------------
# Authorization Server metadata discovery + JWKS fetch
# ---------------------------------------------------------------------------

async def discover_metadata(
    *,
    scheme: str,
    host: str,
    port: int,
    api_selector: str = "",
    client: Any = None,
) -> dict[str, Any]:
    """Discover the Authorization Server metadata document per the
    NMOS With OAuth2.0 spec §"Authorization Server Metadata Endpoint".

    Tries three URL forms in order, stopping at the first that returns
    HTTP 200 with a parseable JSON body:

    1. ``<scheme>://<host>:<port>/.well-known/oauth-authorization-server[/<api_selector>]``
       — IS-10 / RFC 8414 §3.1 normative form.
    2. ``<scheme>://<host>:<port>[/<api_selector>]/.well-known/oauth-authorization-server``
       — Keycloak-style placement (well-known appended to issuer).
    3. ``<scheme>://<host>:<port>[/<api_selector>]/.well-known/openid-configuration``
       — OpenID Connect Discovery 1.0 fallback, supported by virtually
       every OIDC-compliant AS.

    The three forms collapse to one (or two) when ``api_selector`` is
    empty, so the additional probes are cheap no-ops for Hydra-style
    deployments. Raises :class:`InvalidData` when none of the forms
    yield a valid metadata document.
    """
    import aiohttp

    sel = api_selector.strip("/")
    base = f"{scheme}://{host}:{port}"
    candidates: list[str] = []
    # Form (1) — RFC 8414 §3.1 normative.
    candidates.append(
        f"{base}/.well-known/oauth-authorization-server"
        + (f"/{sel}" if sel else ""),
    )
    # Form (2) — Keycloak-style placement. Skip when it would equal
    # form (1) (i.e. no api_selector → identical URL).
    keycloak_form = (
        f"{base}/{sel}/.well-known/oauth-authorization-server" if sel
        else f"{base}/.well-known/oauth-authorization-server"
    )
    if keycloak_form not in candidates:
        candidates.append(keycloak_form)
    # Form (3) — OIDC Discovery 1.0 fallback.
    oidc_form = (
        f"{base}/{sel}/.well-known/openid-configuration" if sel
        else f"{base}/.well-known/openid-configuration"
    )
    if oidc_form not in candidates:
        candidates.append(oidc_form)

    async def _try(session: Any) -> dict[str, Any]:
        last_err = ""
        for url in candidates:
            try:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        parsed: dict[str, Any] = JsonEngine.parse_any(text)
                        return parsed
                    last_err = f"{url} → HTTP {resp.status}"
            except Exception as exc:
                last_err = f"{url} → {exc}"
        raise InvalidData(
            f"AS metadata discovery failed (tried "
            f"{len(candidates)} URL forms); last: {last_err}",
        )

    if client is None:
        async with aiohttp.ClientSession() as session:
            return await _try(session)
    return await _try(client)


async def fetch_jwks(url: str, client: Any = None) -> JWKS:
    """Fetch JWKS from a known JWKS URL.

    Used when the JWKS URL is already known (e.g. from a prior
    metadata discovery via :func:`discover_metadata`, or by direct
    operator configuration). Callers that have only the AS host /
    port should use :func:`discover_jwks` instead, which discovers
    the metadata document, reads ``jwks_uri`` from it, and then
    fetches the keys.

    Args:
        url: The JWKS endpoint URL.
        client: Optional aiohttp.ClientSession.

    Returns:
        JWKS object with parsed keys.
    """
    import aiohttp

    if client is None:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise InvalidData(f"JWKS fetch failed: HTTP {resp.status}")
                data = JsonEngine.parse_any(await resp.text())
    else:
        async with client.get(url) as resp:
            if resp.status != 200:
                raise InvalidData(f"JWKS fetch failed: HTTP {resp.status}")
            data = JsonEngine.parse_any(await resp.text())

    return _parse_jwks(data)


async def discover_jwks(
    *,
    scheme: str,
    host: str,
    port: int,
    api_selector: str = "",
    client: Any = None,
) -> JWKS:
    """End-to-end: discover the AS metadata document, follow its
    ``jwks_uri``, fetch and parse the JWKS.

    This is the spec-compliant way to obtain Public Keys per IS-10 /
    NMOS With OAuth2.0 — the JWKS path is identified normatively
    only via the ``jwks_uri`` field of the metadata document.
    """
    metadata = await discover_metadata(
        scheme=scheme, host=host, port=port,
        api_selector=api_selector, client=client,
    )
    jwks_uri = metadata.get("jwks_uri")
    if not isinstance(jwks_uri, str) or not jwks_uri:
        raise InvalidData(
            "AS metadata document is missing the 'jwks_uri' field; "
            "cannot locate Public Keys."
        )
    return await fetch_jwks(jwks_uri, client=client)


def _parse_jwks(data: dict[str, Any]) -> JWKS:
    """Parse a JWKS JSON dict into a JWKS object."""
    keys: list[JSONWebKey] = []
    for key_data in data.get("keys", []):
        keys.append(JSONWebKey(
            kty=key_data.get("kty", ""),
            alg=key_data.get("alg", ""),
            kid=key_data.get("kid", ""),
            use=key_data.get("use", ""),
            n=key_data.get("n", ""),
            e=key_data.get("e", ""),
            x=key_data.get("x", ""),
            y=key_data.get("y", ""),
        ))
    return JWKS(keys=keys)


# ---------------------------------------------------------------------------
# JWT validation
# ---------------------------------------------------------------------------

def _b64url_decode(s: str) -> bytes:
    """Base64 URL decode (with padding fix)."""
    s = s.replace("-", "+").replace("_", "/")
    padding_needed = 4 - len(s) % 4
    if padding_needed != 4:
        s += "=" * padding_needed
    return base64.b64decode(s)


def _find_key(jwks: JWKS, kid: str) -> JSONWebKey | None:
    """Find a key by kid in the JWKS."""
    for key in jwks.keys:
        if key.kid == kid:
            return key
    return None


def _parse_rsa_public_key(jwk: JSONWebKey) -> RSAPublicKey:
    """Reconstruct RSA public key from JWK n and e."""
    n_bytes = _b64url_decode(jwk.n)
    e_bytes = _b64url_decode(jwk.e)
    n = int.from_bytes(n_bytes, "big")
    e = int.from_bytes(e_bytes, "big")
    return RSAPublicNumbers(e, n).public_key(default_backend())


def _parse_ec_public_key(jwk: JSONWebKey) -> EllipticCurvePublicKey:
    """Reconstruct ECDSA public key from JWK x and y."""
    x_bytes = _b64url_decode(jwk.x)
    y_bytes = _b64url_decode(jwk.y)
    x = int.from_bytes(x_bytes, "big")
    y = int.from_bytes(y_bytes, "big")

    curve: ec.EllipticCurve
    if jwk.alg == "ES256":
        curve = SECP256R1()
    elif jwk.alg == "ES512":
        curve = SECP521R1()
    else:
        raise InvalidData(f"unsupported EC algorithm: {jwk.alg}")

    return ec.EllipticCurvePublicNumbers(x, y, curve).public_key(default_backend())


def validate_token(token_string: str, jwks: JWKS) -> bool:
    """Validate a JWT token signature. Returns True if valid."""
    ok, _ = validate_token_with_claims(token_string, jwks)
    return ok


def validate_token_with_claims(
    token_string: str, jwks: JWKS,
) -> tuple[bool, dict[str, Any]]:
    """Validate a JWT token and return (ok, claims).

    Supports RS256, RS512, ES256, ES512. Enforces TR-10-SEC §14.3.3.2
    on the JOSE header — ``typ`` must be present and one of ``JWT``,
    ``at+jwt`` or ``application/at+jwt``; ``alg`` must be on the
    permitted list. Any other shape returns ``(False, {})`` so the
    bearer middleware emits HTTP 401 per §14.3.3.5.
    """
    parts = token_string.split(".")
    if len(parts) != 3:
        return False, {}

    header_b64, payload_b64, signature_b64 = parts

    # Parse header
    try:
        header = JsonEngine.parse_any(_b64url_decode(header_b64))
    except (ValueError, TypeError):
        return False, {}

    # TR-10-SEC §14.3.3.2: "The JOSE header typ parameter shall be
    # present and shall have one of the following values: JWT, at+jwt,
    # or application/at+jwt." The comparison is case-insensitive per
    # RFC 7515 §4.1.9 (the "typ" Header Parameter). A missing or
    # mismatched ``typ`` makes the token invalid → 401 from the caller.
    typ = header.get("typ", "")
    if not isinstance(typ, str) or typ.lower() not in _PERMITTED_TYP_VALUES:
        return False, {}

    alg = header.get("alg", "")
    # TR-10-SEC §14.3.3.2 also restricts ``alg`` to {RS256, RS512,
    # ES256, ES512}. Validate up-front so an unsupported alg fails fast
    # rather than falling through to the dispatch below — which would
    # also reject it, but with a less actionable message.
    if alg not in _PERMITTED_TOKEN_ALGORITHMS:
        return False, {}

    kid = header.get("kid", "")

    # Parse payload (claims)
    try:
        claims = JsonEngine.parse_any(_b64url_decode(payload_b64))
    except (ValueError, TypeError):
        return False, {}

    # Find key
    jwk = _find_key(jwks, kid)
    if jwk is None:
        return False, claims

    # Decode signature
    try:
        signature = _b64url_decode(signature_b64)
    except (ValueError, TypeError):
        return False, claims

    # Signed data = header.payload (as bytes)
    signed_data = f"{header_b64}.{payload_b64}".encode("ascii")

    try:
        if alg in ("RS256", "RS512"):
            rsa_pub_key = _parse_rsa_public_key(jwk)
            hash_alg = SHA256() if alg == "RS256" else SHA512()
            rsa_pub_key.verify(signature, signed_data, padding.PKCS1v15(), hash_alg)
            return True, claims

        elif alg in ("ES256", "ES512"):
            ec_pub_key = _parse_ec_public_key(jwk)
            hash_alg = SHA256() if alg == "ES256" else SHA512()
            # ECDSA signatures in JWT are raw r||s, not DER
            key_size = 32 if alg == "ES256" else 66  # P-256: 32 bytes, P-521: 66 bytes
            if len(signature) != key_size * 2:
                return False, claims
            r = int.from_bytes(signature[:key_size], "big")
            s = int.from_bytes(signature[key_size:], "big")
            der_sig = utils.encode_dss_signature(r, s)
            ec_pub_key.verify(der_sig, signed_data, ec.ECDSA(hash_alg))
            return True, claims

        else:
            return False, claims

    except (InvalidSignature, ValueError, TypeError, KeyError):
        return False, claims


# ---------------------------------------------------------------------------
# Access control — claims validation
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Access control tri-state
# ---------------------------------------------------------------------------

class _AccessResult:
    """Tri-state return for access evaluation per spec pseudocode."""
    pass

ALLOW = _AccessResult()   # Access granted
DENY = _AccessResult()    # Access denied (valid token, insufficient permissions → 403)
INVALID = _AccessResult() # Token is malformed/invalid → 401
_MISSING = object()       # Sentinel for absent attribute


def validate_access(
    claims: dict[str, Any],
    read_write: bool,
    api_name: str,
    node_instance_id: str,
    tls_server_cert_names: list[str] | None = None,
    use_client_credentials_grant_only: bool = False,
    use_serial_number_in_aud: bool = True,
) -> tuple[bool, bool]:
    """Validate JWT claims for API access.

    Faithful implementation of the pseudocode from NMOS With OAuth2.0 spec
    (lines 253-339). Returns ``(allowed, valid_token)`` where:
    - ``valid_token=False`` → caller should return HTTP 401
    - ``valid_token=True, allowed=False`` → caller should return HTTP 403
    - ``valid_token=True, allowed=True`` → access granted

    Args:
        claims: Decoded JWT access token claims (signature already validated).
        read_write: True if the request has side-effects on Node state.
        api_name: NMOS API name (e.g. "node", "connection", "manufacturer").
        node_instance_id: BCP-002-02 Instance Identifier (serial number).
        tls_server_cert_names: DNS names from TLS server certificate (CN + SANs).
            Empty list when TLS is not used.
        use_client_credentials_grant_only: Policy switch for grant type.
        use_serial_number_in_aud: True=serial number mode, False=DNS name mode.
    """
    if tls_server_cert_names is None:
        tls_server_cert_names = []

    # ---- required claims ----
    required = ["iss", "sub", "aud", "client_id", "exp", "scope"]
    for k in required:
        if k not in claims:
            return False, False  # invalid token

    # ---- exp check ----
    exp = claims["exp"]
    if not isinstance(exp, (int, float)):
        return False, False  # invalid token
    if time.time() > float(exp):
        return False, False  # expired → invalid

    # ---- grant policy ----
    sub = claims["sub"]
    client_id = claims["client_id"]
    if not isinstance(sub, str) or not isinstance(client_id, str):
        return False, False  # invalid token

    if use_client_credentials_grant_only:
        if sub != client_id:
            return False, False  # invalid token

    # ---- scope gating ----
    scope = claims["scope"]
    if not isinstance(scope, str):
        return False, False  # invalid token
    if api_name not in scope.split():
        return False, True  # valid token, wrong scope → 403

    # ---- aud gating ----
    aud = claims["aud"]
    if not isinstance(aud, list) or any(not isinstance(a, str) for a in aud):
        return False, False  # invalid token

    if not any(
        aud_entry_allows_current_node(a, node_instance_id, tls_server_cert_names, use_serial_number_in_aud)
        for a in aud
    ):
        return False, True  # valid token, wrong audience → 403

    # ---- locate private claim x-nmos-<api> (may be in ext or top-level) ----
    priv = None
    access_key = f"x-nmos-{api_name}"

    if "ext" in claims:
        if not isinstance(claims["ext"], dict):
            return False, False  # invalid token
        if access_key in claims["ext"]:
            priv = claims["ext"][access_key]
    else:
        if access_key in claims:
            priv = claims[access_key]

    # ---- no private claim: RO allowed, RW denied ----
    if priv is None:
        if not read_write:
            return True, True  # scope grants read
        return False, True  # no write without x-nmos-* → 403

    if not isinstance(priv, dict):
        return False, False  # invalid token

    # Presence of x-nmos-<api> removes default implicit read access from scope.
    read_result = _eval_indexed_attr(
        priv.get("read", _MISSING), aud, node_instance_id,
        tls_server_cert_names, use_serial_number_in_aud,
    )
    if read_result is INVALID:
        return False, False  # invalid token
    if read_result is not ALLOW:
        return False, True  # valid token, read denied → 403

    write_result = _eval_indexed_attr(
        priv.get("write", _MISSING), aud, node_instance_id,
        tls_server_cert_names, use_serial_number_in_aud,
    )
    if write_result is INVALID:
        return False, False  # invalid token

    # Consistency: write without read is invalid
    if write_result is ALLOW and read_result is not ALLOW:
        return False, False  # invalid token

    if read_write:
        allowed = (read_result is ALLOW) and (write_result is ALLOW)
        return allowed, True
    else:
        return (read_result is ALLOW), True


def aud_entry_allows_current_node(
    aud_entry: str,
    node_instance_id: str,
    tls_server_cert_names: list[str],
    use_serial_number_in_aud: bool,
) -> bool:
    """Check if a single aud entry allows access to this node.

    The Node accepts an aud entry if it satisfies **EITHER** of the two
    rules from "NMOS With OAuth2.0" §"Validation":

      1. **Serial-number rule** (default per the spec):
         the aud entry contains the Node's BCP-002-02 Instance Identifier
         (serial number) as a substring AND is exactly equal to one of
         the cert's CN/DNS-SAN identities.
      2. **DNS-name rule** (alternative per the spec):
         the aud entry, treated as a possibly-wildcarded DNS pattern
         per RFC 4592, matches one of the cert's CN/DNS-SAN identities.

    The Authorization Server may issue tokens with aud entries shaped
    for either rule (or both); the Node MUST NOT require operators to
    choose a single mode globally. Trying both rules per-aud-entry and
    accepting on the first match keeps the Node interoperable with
    Authorization Servers that mix audience-construction strategies.

    Wildcard ``"*"`` always accepted.

    The ``use_serial_number_in_aud`` parameter is retained for
    backward compatibility but is no longer a strict mode switch —
    it simply re-orders rule attempts (serial first when True, DNS
    first when False). Both rules are always tried.

    The empty-``tls_server_cert_names`` branch reflects "no server
    cert is configured" and is accepted only when test-mode is
    explicitly enabled (see :mod:`nmos.config.allow_non_tls_for_testing`);
    in production, an empty cert-names list MUST cause the audience
    check to fail closed.
    """
    if aud_entry == "*":
        return True

    # ---- Serial-number rule ----
    # The aud entry must include the Node's instance-id as a substring.
    # The cert-binding clause then requires either an exact match
    # against a cert identity, OR (if the operator has opted into
    # test mode) the bypass for in-process tests that can't run a
    # real TLS handshake.
    if node_instance_id and node_instance_id in aud_entry:
        if tls_server_cert_names:
            if aud_entry in tls_server_cert_names:
                return True
        else:
            from nmos.config import allow_non_tls_for_testing
            if allow_non_tls_for_testing():
                return True

    # ---- DNS-name rule ----
    # The aud entry, treated as a (possibly-wildcarded) DNS pattern
    # per RFC 4592, must match one of the cert identities. Without
    # any cert identities the DNS rule has nothing to match, so it
    # cannot accept the entry — there's no test-mode bypass for the
    # DNS rule because the rule is meaningless without a cert.
    if tls_server_cert_names and _matches_dns_wildcard(
        aud_entry, tls_server_cert_names,
    ):
        return True

    return False


def _matches_dns_wildcard(pattern: str, cert_names: list[str]) -> bool:
    """RFC 4592 DNS wildcard matching.

    Spec pseudocode lines 356-369.
    """
    for name in cert_names:
        if _dns_wildcard_matches(pattern, name):
            return True
    return False


def _dns_wildcard_matches(pattern: str, target: str) -> bool:
    """Match a single DNS pattern against a target name.

    RFC 4592: *.example.com matches sub.example.com but NOT example.com
    and NOT other.sub.example.com (only one label replaced).
    """
    if pattern.startswith("*."):
        domain = pattern[2:]
        if not target.endswith("." + domain):
            return False
        prefix = target[: -(len(domain) + 1)]
        return "." not in prefix  # Must be exactly one label
    return pattern.lower() == target.lower()


def _eval_indexed_attr(
    attr: Any,
    aud: list[str],
    node_instance_id: str,
    tls_server_cert_names: list[str],
    use_serial_number_in_aud: bool,
) -> _AccessResult:
    """Evaluate a read or write access attribute.

    Returns ALLOW, DENY, or INVALID per spec pseudocode lines 372-447.

    Supported forms:
    - ["*"]  → ALLOW
    - [""]   → DENY
    - [signed integers...] → aud index allow/deny lists
    - missing (sentinel) → DENY
    """
    if attr is _MISSING:
        return DENY

    if isinstance(attr, list) and len(attr) == 1 and isinstance(attr[0], str):
        if attr[0] == "*":
            return ALLOW
        if attr[0] == "":
            return DENY
        return INVALID

    if not isinstance(attr, list) or not all(isinstance(v, (int, float)) for v in attr):
        return INVALID

    if len(attr) == 0:
        return INVALID

    # Split into positive and negative, enforcing positive-before-negative order.
    list_pos: list[int] = []
    list_neg: list[int] = []
    seen_negative = False
    for v in attr:
        i = int(v)
        if i < 0:
            seen_negative = True
            list_neg.append(i)
        else:
            if seen_negative:
                return INVALID  # Ordering violation
            list_pos.append(i)

    def idx_matches(i: int) -> bool | _AccessResult:
        if abs(i) >= len(aud):
            return INVALID  # Out of bounds
        aud_entry = aud[abs(i)]
        return aud_entry_allows_current_node(
            aud_entry, node_instance_id, tls_server_cert_names, use_serial_number_in_aud,
        )

    # Phase 1: allow-list (if present)
    if len(list_pos) > 0:
        allowed = False
        for i in list_pos:
            m = idx_matches(i)
            if m is INVALID:
                return INVALID
            if m:
                allowed = True
                break
        if not allowed:
            return DENY

    # Phase 2: deny-list exceptions
    for i in list_neg:
        m = idx_matches(i)
        if m is INVALID:
            return INVALID
        if m:
            return DENY

    return ALLOW


def check_client_cert_name(cert_names: list[str], client_id: str) -> bool:
    """Check if any client certificate name matches the client_id.

    Per spec §"Mutual TLS Client Certificate Binding": case-insensitive
    comparison, wildcards MUST NOT be considered a match.
    """
    client_id_lower = client_id.lower()
    for name in cert_names:
        name_lower = name.lower()
        if "*" in name_lower:
            continue  # Wildcards MUST NOT be considered a match
        if name_lower == client_id_lower:
            return True
    return False


# ---------------------------------------------------------------------------
# Token acquisition flows (for client mode)
# ---------------------------------------------------------------------------

async def get_token_client_credentials(
    client_id: str,
    client_secret: str,
    scope: str,
    token_url: str,
    client: Any = None,
) -> tuple[str, float]:
    """Acquire access token using client_credentials grant.

    Returns (access_token, expire_at_posix_time).
    """
    import aiohttp

    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": scope,
        "audience": "*",
    }

    if client is None:
        async with aiohttp.ClientSession() as session:
            async with session.post(token_url, data=data) as resp:
                if resp.status != 200:
                    raise InvalidData(f"token request failed: HTTP {resp.status}")
                body = JsonEngine.parse_any(await resp.text())
    else:
        async with client.post(token_url, data=data) as resp:
            if resp.status != 200:
                raise InvalidData(f"token request failed: HTTP {resp.status}")
            body = JsonEngine.parse_any(await resp.text())

    access_token = body.get("access_token", "")
    expires_in = body.get("expires_in", 3600)
    expire_at = time.time() + float(expires_in)

    return access_token, expire_at


async def get_token_authorization_code(
    client_id: str,
    client_secret: str,
    code: str,
    scope: str,
    token_url: str,
    redirect_url: str,
    client: Any = None,
) -> tuple[str, str, float]:
    """Acquire access token using authorization_code grant.

    Returns (access_token, refresh_token, expire_at_posix_time).
    """
    import aiohttp

    data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "scope": scope,
        "redirect_uri": redirect_url,
    }

    if client is None:
        async with aiohttp.ClientSession() as session:
            async with session.post(token_url, data=data) as resp:
                if resp.status != 200:
                    raise InvalidData(f"token request failed: HTTP {resp.status}")
                body = JsonEngine.parse_any(await resp.text())
    else:
        async with client.post(token_url, data=data) as resp:
            if resp.status != 200:
                raise InvalidData(f"token request failed: HTTP {resp.status}")
            body = JsonEngine.parse_any(await resp.text())

    access_token = body.get("access_token", "")
    refresh_token = body.get("refresh_token", "")
    expires_in = body.get("expires_in", 3600)
    expire_at = time.time() + float(expires_in)

    return access_token, refresh_token, expire_at


async def refresh_access_token(
    client_id: str,
    client_secret: str,
    refresh_token_str: str,
    token_url: str,
    client: Any = None,
) -> tuple[str, str, float]:
    """Refresh an access token.

    Returns (new_access_token, new_refresh_token, expire_at_posix_time).
    """
    import aiohttp

    data = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token_str,
    }

    if client is None:
        async with aiohttp.ClientSession() as session:
            async with session.post(token_url, data=data) as resp:
                if resp.status != 200:
                    raise InvalidData(f"token refresh failed: HTTP {resp.status}")
                body = JsonEngine.parse_any(await resp.text())
    else:
        async with client.post(token_url, data=data) as resp:
            if resp.status != 200:
                raise InvalidData(f"token refresh failed: HTTP {resp.status}")
            body = JsonEngine.parse_any(await resp.text())

    new_access = body.get("access_token", "")
    new_refresh = body.get("refresh_token", refresh_token_str)
    expires_in = body.get("expires_in", 3600)
    expire_at = time.time() + float(expires_in)

    return new_access, new_refresh, expire_at
