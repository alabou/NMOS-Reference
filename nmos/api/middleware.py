# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""API middleware — OAuth2 authorization, exclusive session, CORS.

Implements the two-layer authorization model:
1. OAuth2 (outer): Validates JWT from Authorization header
2. Exclusive Session (inner): Validates session token from
   PEP-Exclusive-Authorization (OAuth2) or Authorization (no OAuth2)

Per NMOS With Node Reservation spec + NMOS With OAuth2.0 spec.
"""

from __future__ import annotations

from typing import Any, Callable, Awaitable

from aiohttp import web

from nmos.api.response import error_response, CORS_HEADERS


# Type alias for aiohttp handler
Handler = Callable[[web.Request], Awaitable[web.Response]]


def _oauth_error_response(
    status: int,
    debug: str,
    realm: str,
    request: web.Request,
) -> web.Response:
    """Build an OAuth2/exclusive auth error using the shared JSON engine path."""
    return error_response(
        status,
        debug,
        headers={"WWW-Authenticate": f'Bearer realm="{realm}"'},
        request=request,
    )


# ---------------------------------------------------------------------------
# CORS middleware
# ---------------------------------------------------------------------------

@web.middleware
async def cors_middleware(
    request: web.Request,
    handler: Handler,
) -> web.Response:
    """Add CORS headers to all responses."""
    if request.method == "OPTIONS":
        headers = dict(CORS_HEADERS)
        req_headers = request.headers.get("Access-Control-Request-Headers", "")
        if req_headers:
            headers["Access-Control-Allow-Headers"] = req_headers
        return web.Response(status=200, headers=headers, content_type="application/json")

    try:
        response = await handler(request)
    except (web.HTTPNotFound, web.HTTPMethodNotAllowed):
        # Let these propagate to the trailing-slash middleware for path resolution
        raise
    except web.HTTPException as exc:
        # Convert aiohttp's default text/plain errors to application/json
        from nmos.api.response import error_response
        response = error_response(exc.status, exc.reason or "", request=request)
    for key, value in CORS_HEADERS.items():
        response.headers[key] = value
    return response


# ---------------------------------------------------------------------------
# mTLS enforcement middleware
# ---------------------------------------------------------------------------

_READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@web.middleware
async def client_auth_middleware(
    request: web.Request,
    handler: Handler,
) -> web.Response:
    """Global enforcement of ``node.client_auth_required`` for state-
    changing verbs.

    Per NMOS With OAuth2.0 (line 110) and NMOS With Node Reservation
    (line 57), when mTLS is enabled on the Node (operator sets
    ``client_auth_required=True``) every state-changing request MUST
    present a verified client certificate — regardless of whether
    OAuth2 or Reservation are also in use.

    This is the SINGLE enforcement point for that rule. Individual
    handlers ( ``check_exclusive_authorization`` , connection PATCH,
    exclusive acquire/renew/release/keepalive, etc.) no longer need
    to re-check ``client_auth_required`` themselves.

    Read-only verbs (GET/HEAD/OPTIONS) pass through — this preserves
    Reservation's "read-only granted without client certificate" rule
    (Node Reservation.md:41-45).
    """
    if request.method in _READ_ONLY_METHODS:
        return await handler(request)

    node = request.app.get("node")
    if node is None:
        return await handler(request)

    if not getattr(node, "client_auth_required", False):
        return await handler(request)

    if not _client_authenticated(request):
        return _oauth_error_response(
            401,
            "TLS client authentication required",
            "nmos-mtls",
            request,
        )

    return await handler(request)


# ---------------------------------------------------------------------------
# OAuth2 middleware
# ---------------------------------------------------------------------------

def check_oauth2(
    read_write: bool,
    api_name: str,
) -> Callable[[Handler], Handler]:
    """Decorator factory for OAuth2 authorization.

    Validates the Authorization Bearer JWT token against JWKS keys.
    If OAuth2 is disabled on the node, passes through.

    Args:
        read_write: True if the endpoint modifies state
        api_name: NMOS API name (node, connection, streamcompatibility, manufacturer)
    """
    def decorator(handler: Handler) -> Handler:
        async def wrapper(request: web.Request) -> web.Response:
            node = request.app.get("node")
            if node is None or not getattr(node, "oauth2", False):
                return await handler(request)

            # Extract Bearer token from Authorization header
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return _oauth_error_response(
                    401,
                    "bearer token is required",
                    "nmos-oauth2",
                    request,
                )

            token = auth_header[7:].strip()

            # Validate token
            from nmos.oauth2 import validate_token_with_claims, validate_access
            keys = getattr(node, "oauth2_keys", None)
            if keys is None:
                return error_response(401, "no OAuth2 public keys available", request=request)

            ok, claims = validate_token_with_claims(token, keys)
            if not ok:
                return _oauth_error_response(
                    401,
                    "bearer token is not authorized",
                    "nmos-oauth2",
                    request,
                )

            # Check access — returns (allowed, valid_token) per spec pseudocode
            serial = getattr(node, "serial_number", "")
            # TLS server cert names for audience validation (fake names for non-TLS testing)
            tls_names = getattr(node, "tls_server_cert_names", [])
            use_sn = getattr(node, "use_serial_number_in_aud", True)
            # mTLS client cert binding (separate from aud check)
            client_cert_names = _get_client_cert_names(request)
            if client_cert_names:
                from nmos.oauth2 import check_client_cert_name
                if not check_client_cert_name(client_cert_names, claims.get("client_id", "")):
                    return _oauth_error_response(
                        401, "client certificate does not match client_id",
                        "nmos-oauth2", request,
                    )

            allowed, valid_token = validate_access(
                claims, read_write, api_name, serial, tls_names,
                use_client_credentials_grant_only=getattr(node, "client_credentials_only", False),
                use_serial_number_in_aud=use_sn,
            )
            if not valid_token:
                return _oauth_error_response(
                    401,
                    "bearer token is invalid or malformed",
                    "nmos-oauth2",
                    request,
                )
            if not allowed:
                return error_response(403, "insufficient permissions", request=request)

            return await handler(request)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Exclusive session authorization
# ---------------------------------------------------------------------------

def check_exclusive_authorization(
    request: web.Request,
    node: Any,
) -> web.Response | None:
    """Check exclusive session token for state-changing operations.

    Per NMOS With Node Reservation spec:
    - If OAuth2 enabled: token in PEP-Exclusive-Authorization header
    - If OAuth2 disabled: token in Authorization header

    Returns None if authorized, or an error Response if not.
    """
    exclusive_session = getattr(node, "exclusive_session", None)
    if exclusive_session is None:
        return None  # No exclusive session configured — allow

    oauth2_enabled = getattr(node, "oauth2", False)
    header_name = "PEP-Exclusive-Authorization" if oauth2_enabled else "Authorization"

    auth_header = request.headers.get(header_name, "")

    if auth_header.startswith("Bearer "):
        # Token present — validate ownership. mTLS (if required) has
        # already been enforced by client_auth_middleware upstream.
        token = auth_header[7:].strip()
        if exclusive_session.is_owner(token):
            return None  # Authorized
        else:
            return _oauth_error_response(
                401,
                "invalid exclusive session token",
                "nmos-exclusive",
                request,
            )

    # No token — if a session is active, the bearer is mandatory.
    if exclusive_session.is_alive():
        return _oauth_error_response(
            401,
            "exclusive session active, bearer token required",
            "nmos-exclusive",
            request,
        )

    # No session, no bearer — mTLS (if required) already enforced by
    # client_auth_middleware, so nothing more to check here.
    return None


# ---------------------------------------------------------------------------
# TLS helpers
# ---------------------------------------------------------------------------

def _client_authenticated(request: web.Request) -> bool:
    """Check if TLS client certificate is verified.

    Returns True only when:
      * the peer is connected over TLS AND presented a verified cert, OR
      * the process is in test mode (``ALLOW_NON_TLS_FOR_TESTING``),
        which allows non-TLS transports to pass for in-process tests
        that cannot run a real TLS handshake.

    In production (default) a missing TLS transport or a missing
    peercert both return False — callers that enforce
    ``client_auth_required`` will reject the request with 401. This
    matches the NMOS With OAuth2.0 (line 110) and NMOS With Node
    Reservation (line 57) requirement that bare HTTP MUST NOT be used.
    """
    from nmos.config import allow_non_tls_for_testing

    transport = request.transport
    if transport is None:
        # No transport info — in tests this path is fine; in production
        # it would only happen at connection teardown.
        return allow_non_tls_for_testing()
    ssl_object = transport.get_extra_info("ssl_object")
    if ssl_object is None:
        # Plain HTTP — only permissible in test mode.
        return allow_non_tls_for_testing()
    peercert = ssl_object.getpeercert()
    return peercert is not None


def _get_client_cert_names(request: web.Request) -> list[str] | None:
    """Extract DNS names from TLS client certificate.

    Returns None if no TLS or no client cert.
    """
    transport = request.transport
    if transport is None:
        return None
    ssl_object = transport.get_extra_info("ssl_object")
    if ssl_object is None:
        return None
    peercert = ssl_object.getpeercert()
    if peercert is None:
        return None

    names: list[str] = []

    # Subject CN
    subject = peercert.get("subject", ())
    for rdns in subject:
        for attr_type, attr_value in rdns:
            if attr_type == "commonName":
                names.append(attr_value)

    # Subject Alternative Names (DNS)
    san = peercert.get("subjectAltName", ())
    for san_type, san_value in san:
        if san_type == "DNS":
            if san_value not in names:
                names.append(san_value)

    return names if names else None
