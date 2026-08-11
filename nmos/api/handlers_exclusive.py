# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Exclusive session (Node Reservation) API handlers.

POST /x-manufacturer/exclusive/v1.0/acquire
POST /x-manufacturer/exclusive/v1.0/renew
POST /x-manufacturer/exclusive/v1.0/release
POST /x-manufacturer/exclusive/v1.0/keepalive

Per NMOS With Node Reservation spec.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from aiohttp import web

from nmos.api.response import json_response, error_response, status_response
from nmos.crypto import TooEarly
from nmos.errors import Busy, NotAllowed
from nmos.json.engine import JsonEngine


def _set_key_xcl_on_all_activations(node: Any, key_xcl: bytes) -> None:
    """Set key_xcl on all sender/receiver activation Privacy objects.

    Spec: "A successful Acquire operation MUST set the hidden key_xcl IS-05
    transport parameter to the value of exclusive_key for all Senders and
    Receivers of the Node supporting Privacy Encryption."
    And: "A successful Release operation MUST set the hidden key_xcl active
    and staged transport parameters to an empty octet string."
    """
    for store in (node.sender_activation, node.receiver_activation):
        for _sid, activation in store:
            if hasattr(activation, 'privacy'):
                activation.privacy.xcl = key_xcl


async def handle_post_acquire(request: web.Request) -> web.Response:
    """POST /x-manufacturer/exclusive/v1.0/acquire — Acquire exclusive session."""
    node = request.app["node"]

    try:
        body = JsonEngine.parse_any(await request.text())
    except (ValueError, TypeError):
        return error_response(400, "invalid JSON body", request=request)

    owner = body.get("owner", "")
    exclusive_key_hex = body.get("exclusive_key", "")

    if not owner or not isinstance(owner, str):
        return error_response(400, "missing or invalid 'owner' field", request=request)

    if not exclusive_key_hex or len(exclusive_key_hex) != 32:
        return error_response(400, "exclusive_key must be 32 hex characters (128-bit)", request=request)

    try:
        exclusive_key = bytes.fromhex(exclusive_key_hex)
    except ValueError:
        return error_response(400, "exclusive_key must be valid hexadecimal", request=request)

    try:
        token = node.exclusive_session.acquire(owner, exclusive_key)
    except Busy:
        headers: dict[str, str] = {}
        # Optionally include Link header with owner info.
        current_owner = node.exclusive_session.owner
        if current_owner:
            encoded_owner = urllib.parse.quote(current_owner, safe="")
            headers["Link"] = f"<https://{encoded_owner}>"
        return error_response(
            423,
            "exclusive session already acquired",
            headers=headers or None,
            request=request,
        )

    # Spec: "A successful Acquire operation MUST set the hidden key_xcl IS-05
    # transport parameter to the value of exclusive_key for all Senders and
    # Receivers of the Node supporting Privacy Encryption."
    _set_key_xcl_on_all_activations(node, exclusive_key)

    return json_response(token, no_store=True)


async def handle_post_renew(request: web.Request) -> web.Response:
    """POST /x-manufacturer/exclusive/v1.0/renew — Renew exclusive session."""
    node = request.app["node"]

    oauth2_enabled = getattr(node, "oauth2", False)
    header_name = "PEP-Exclusive-Authorization" if oauth2_enabled else "Authorization"
    auth_header = request.headers.get(header_name, "")

    if not auth_header.startswith("Bearer "):
        return error_response(
            401,
            "bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
            request=request,
        )

    token = auth_header[7:].strip()

    try:
        new_token = node.exclusive_session.renew(token)
    except TooEarly as exc:
        # Spec §Renew: "A `425 Too Early` response MUST include a `Retry-After`
        # response header as defined in RFC 9110. The `delay-seconds` form MUST
        # be used and the `HTTP-date` form MUST NOT be used, so that the delay
        # is unaffected by any clock difference between the client and the
        # Node."
        #
        # This is the only channel through which a client can learn a Node's
        # configured Session Lifetime: §Renew lets it derive the Lifetime as
        # twice the sum of this delay and its own elapsed time since the last
        # Acquire or Renew. Omitting the header leaves a client polling blindly
        # against the 60-minute minimum it had to assume.
        return error_response(
            425,
            "too early to renew",
            headers={"Retry-After": str(exc.retry_after)},
            request=request,
        )
    except NotAllowed:
        return error_response(
            401,
            "invalid or expired session token",
            headers={"WWW-Authenticate": "Bearer"},
            request=request,
        )

    return json_response(new_token, no_store=True)


async def handle_post_release(request: web.Request) -> web.Response:
    """POST /x-manufacturer/exclusive/v1.0/release — Release exclusive session."""
    node = request.app["node"]

    oauth2_enabled = getattr(node, "oauth2", False)
    header_name = "PEP-Exclusive-Authorization" if oauth2_enabled else "Authorization"
    auth_header = request.headers.get(header_name, "")

    if not auth_header.startswith("Bearer "):
        return error_response(
            401,
            "bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
            request=request,
        )

    token = auth_header[7:].strip()

    try:
        node.exclusive_session.release(token)
    except NotAllowed:
        return error_response(
            401,
            "invalid or expired session token",
            headers={"WWW-Authenticate": "Bearer"},
            request=request,
        )

    # Spec: "A successful Release operation MUST set the hidden key_xcl active
    # and staged transport parameters to an empty octet string for all Senders
    # and Receivers of the Node supporting Privacy Encryption."
    _set_key_xcl_on_all_activations(node, b"")

    return status_response(200)


async def handle_post_keepalive(request: web.Request) -> web.Response:
    """POST /x-manufacturer/exclusive/v1.0/keepalive — Keep session alive."""
    node = request.app["node"]

    oauth2_enabled = getattr(node, "oauth2", False)
    header_name = "PEP-Exclusive-Authorization" if oauth2_enabled else "Authorization"
    auth_header = request.headers.get(header_name, "")

    if not auth_header.startswith("Bearer "):
        return error_response(
            401,
            "bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
            request=request,
        )

    token = auth_header[7:].strip()

    try:
        node.exclusive_session.keep_alive(token)
    except NotAllowed:
        return error_response(
            401,
            "invalid or expired session token",
            headers={"WWW-Authenticate": "Bearer"},
            request=request,
        )

    return status_response(200)
