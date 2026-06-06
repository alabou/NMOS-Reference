# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""IS-04 Node API handlers (v1.3).

GET endpoints for Node self, devices, sources, flows, senders, receivers.
Uses the JsonEngine for encoding — the generated types' encode() methods
know exactly which fields to include/exclude per NMOS JSON schema.
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from nmos.api.response import json_response, json_response_raw, error_response, _wants_html
from nmos.json.engine import JsonEngine


import re as _re

_UUID_RE = _re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# Map JSON field names to their IS-04 collection paths.
# When a UUID value appears under one of these keys, it becomes a clickable
# link to the corresponding resource in the Node API.
_FIELD_TO_PATH: dict[str, str] = {
    "id":          "",           # same collection as the resource itself
    "node_id":     "/x-nmos/node/v1.3/self/",
    "device_id":   "/x-nmos/node/v1.3/devices/",
    "source_id":   "/x-nmos/node/v1.3/sources/",
    "flow_id":     "/x-nmos/node/v1.3/flows/",
    "sender_id":   "/x-nmos/node/v1.3/senders/",
    "receiver_id": "/x-nmos/node/v1.3/receivers/",
    "parents":     "auto",       # resolved from UUID resource_type
}

# Resource type → API collection path (for UUID-based resolution)
_RESOURCE_TYPE_PATH: dict[int, str] = {
    0: "/x-nmos/node/v1.3/self/",       # NODE
    1: "/x-nmos/node/v1.3/devices/",    # DEVICE
    2: "/x-nmos/node/v1.3/senders/",    # SENDER
    3: "/x-nmos/node/v1.3/receivers/",  # RECEIVER
    4: "/x-nmos/node/v1.3/flows/",      # FLOW
    5: "/x-nmos/node/v1.3/sources/",    # SOURCE
}


def _make_link_resolver(request_path: str) -> Any:
    """Create a link resolver for NMOS HTML rendering.

    Returns a callback (field_name, value) -> URL | None that maps
    UUID values in known fields to their REST API paths.
    """
    # Determine the collection base for "id" field links.
    # List endpoint: /x-nmos/node/v1.3/senders/ → collection = /x-nmos/node/v1.3/senders/
    # Single resource: /x-nmos/node/v1.3/senders/{uuid} → collection = /x-nmos/node/v1.3/senders/
    stripped = request_path.rstrip("/")
    last_segment = stripped.rsplit("/", 1)[-1] if "/" in stripped else ""
    if _UUID_RE.match(last_segment):
        # Single resource path — strip the UUID to get collection
        collection_base = stripped.rsplit("/", 1)[0] + "/"
    else:
        # List endpoint — path IS the collection
        collection_base = stripped + "/"

    def resolver(field_name: str, value: str) -> str | None:
        if not _UUID_RE.match(value):
            return None
        path = _FIELD_TO_PATH.get(field_name)
        if path is None:
            return None
        if path == "":
            # "id" field — link to this resource in its own collection
            return collection_base + value
        if path == "auto":
            # Resolve from UUID resource_type (parents field)
            try:
                from nmos.uuid import ResourceUuid
                uuid = ResourceUuid()
                uuid.set_from_string(value)
                type_path = _RESOURCE_TYPE_PATH.get(uuid.resource_type)
                if type_path is not None:
                    return type_path + value
            except Exception:
                pass
            return collection_base + value  # fallback to same collection
        if field_name == "node_id":
            return path  # node self is not /nodes/{id}
        return path + value

    return resolver


def _encode(resource: Any, html_mode: bool = False, request_path: str = "") -> str:
    """Encode an NMOS resource to JSON string using the JsonEngine.

    The generated types implement encode(engine, name) which produces
    correct NMOS JSON with only the spec-defined fields.

    When html_mode=True, the engine produces indented pretty-printed JSON
    with UUID fields rendered as clickable links to the REST API.

    Polymorphic types (NSourceValue, NFlowValue, NReceiverValue) wrap
    an inner value — we encode the inner value directly since the
    wrapper's encode may produce empty output.
    """
    engine = JsonEngine()
    engine.generate_html = html_mode
    if html_mode:
        engine.level_indentation = 2
        engine.link_resolver = _make_link_resolver(request_path)

    # Unwrap polymorphic types
    inner = resource
    if hasattr(resource, 'get') and callable(resource.get):
        got = resource.get()
        if got is not None:
            inner = got

    return engine.encode(inner)


# ---------------------------------------------------------------------------
# GET /x-nmos/node/v1.3/self
# ---------------------------------------------------------------------------

async def handle_get_self(request: web.Request) -> web.Response:
    """GET /x-nmos/node/v1.3/self — Node self description."""
    node = request.app["node"]
    if node.node_value is None:
        return error_response(500, "node not initialized", request=request)

    return json_response_raw(_encode(node.node_value, _wants_html(request), request.path), no_store=True, request=request)


# ---------------------------------------------------------------------------
# GET /x-nmos/node/v1.3/devices
# ---------------------------------------------------------------------------

async def handle_get_devices(request: web.Request) -> web.Response:
    """GET /x-nmos/node/v1.3/devices — List all devices."""
    node = request.app["node"]
    if node.device_value is None:
        return json_response([], no_store=True, request=request)

    return json_response_raw("[" + _encode(node.device_value, _wants_html(request), request.path) + "]", no_store=True, request=request)


async def handle_get_device(request: web.Request) -> web.Response:
    """GET /x-nmos/node/v1.3/devices/{deviceId} — Single device."""
    node = request.app["node"]
    device_id = request.match_info["deviceId"]

    if node.device_value is None:
        return error_response(404, f"device {device_id} not found", request=request)

    return json_response_raw(_encode(node.device_value, _wants_html(request), request.path), no_store=True, request=request)


# ---------------------------------------------------------------------------
# GET /x-nmos/node/v1.3/sources, /senders, /receivers, /flows
# ---------------------------------------------------------------------------

async def handle_get_sources(request: web.Request) -> web.Response:
    """GET /x-nmos/node/v1.3/sources — List all sources."""
    node = request.app["node"]
    items = [_encode(src, _wants_html(request), request.path) for _, src in node.sources]
    return json_response_raw("[" + ",".join(items) + "]", no_store=True, request=request)


async def handle_get_source(request: web.Request) -> web.Response:
    """GET /x-nmos/node/v1.3/sources/{sourceId}"""
    node = request.app["node"]
    source_id = request.match_info["sourceId"]
    src = node.sources.get(source_id)
    if src is None:
        return error_response(404, f"source {source_id} not found", request=request)
    return json_response_raw(_encode(src, _wants_html(request), request.path), no_store=True, request=request)


async def handle_get_flows(request: web.Request) -> web.Response:
    """GET /x-nmos/node/v1.3/flows — List all flows."""
    node = request.app["node"]
    items = [_encode(flow, _wants_html(request), request.path) for _, flow in node.flows]
    return json_response_raw("[" + ",".join(items) + "]", no_store=True, request=request)


async def handle_get_flow(request: web.Request) -> web.Response:
    """GET /x-nmos/node/v1.3/flows/{flowId}"""
    node = request.app["node"]
    flow_id = request.match_info["flowId"]
    flow = node.flows.get(flow_id)
    if flow is None:
        return error_response(404, f"flow {flow_id} not found", request=request)
    return json_response_raw(_encode(flow, _wants_html(request), request.path), no_store=True, request=request)


async def handle_get_senders(request: web.Request) -> web.Response:
    """GET /x-nmos/node/v1.3/senders — List all senders."""
    node = request.app["node"]
    items = [_encode(sender, _wants_html(request), request.path) for _, sender in node.senders]
    return json_response_raw("[" + ",".join(items) + "]", no_store=True, request=request)


async def handle_get_sender(request: web.Request) -> web.Response:
    """GET /x-nmos/node/v1.3/senders/{senderId}"""
    node = request.app["node"]
    sender_id = request.match_info["senderId"]
    sender = node.senders.get(sender_id)
    if sender is None:
        return error_response(404, f"sender {sender_id} not found", request=request)
    return json_response_raw(_encode(sender, _wants_html(request), request.path), no_store=True, request=request)


async def handle_get_receivers(request: web.Request) -> web.Response:
    """GET /x-nmos/node/v1.3/receivers — List all receivers."""
    node = request.app["node"]
    items = [_encode(recv, _wants_html(request), request.path) for _, recv in node.receivers]
    return json_response_raw("[" + ",".join(items) + "]", no_store=True, request=request)


async def handle_get_receiver(request: web.Request) -> web.Response:
    """GET /x-nmos/node/v1.3/receivers/{receiverId}"""
    node = request.app["node"]
    receiver_id = request.match_info["receiverId"]
    recv = node.receivers.get(receiver_id)
    if recv is None:
        return error_response(404, f"receiver {receiver_id} not found", request=request)
    return json_response_raw(_encode(recv, _wants_html(request), request.path), no_store=True, request=request)
