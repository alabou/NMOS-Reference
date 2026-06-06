# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Root and discovery API handlers.

Provides the top-level NMOS discovery endpoints that controllers use
to find available API versions and types.
"""

from __future__ import annotations

from aiohttp import web

from nmos.api.response import json_response


async def handle_get_root(request: web.Request) -> web.Response:
    """GET / — List available API families."""
    return json_response(["x-nmos/", "x-manufacturer/"], request=request)


async def handle_get_xnmos(request: web.Request) -> web.Response:
    """GET /x-nmos — List available NMOS APIs."""
    return json_response([
        "node/",
        "connection/",
        "streamcompatibility/",
    ], request=request)


async def handle_get_xnmos_node(request: web.Request) -> web.Response:
    """GET /x-nmos/node — List Node API versions."""
    return json_response(["v1.3/"], request=request)


async def handle_get_xnmos_connection(request: web.Request) -> web.Response:
    """GET /x-nmos/connection — List Connection API versions.

    Serves v1.1 (and optionally v1.2 as rewrite). No v1.0.
    """
    return json_response(["v1.1/", "v1.2/"], request=request)


async def handle_get_xnmos_streamcompat(request: web.Request) -> web.Response:
    """GET /x-nmos/streamcompatibility — List Stream Compatibility API versions."""
    return json_response(["v1.0/"], request=request)


async def handle_get_manufacturer(request: web.Request) -> web.Response:
    """GET /x-manufacturer — List manufacturer APIs."""
    return json_response(["exclusive/"], request=request)


async def handle_get_xnmos_node_v13(request: web.Request) -> web.Response:
    """GET /x-nmos/node/v1.3 — List Node API resources."""
    return json_response([
        "self/", "devices/", "sources/", "flows/", "senders/", "receivers/",
    ], request=request)

async def handle_get_xnmos_connection_v10(request: web.Request) -> web.Response:
    """GET /x-nmos/connection/v1.0 — List Connection API resources."""
    return json_response(["bulk/", "single/"], request=request)


async def handle_get_xnmos_connection_v10_single(request: web.Request) -> web.Response:
    """GET /x-nmos/connection/v1.0/single — List single resources."""
    return json_response(["senders/", "receivers/"], request=request)


async def handle_get_xnmos_connection_v10_bulk(request: web.Request) -> web.Response:
    """GET /x-nmos/connection/v1.0/bulk — List bulk resources."""
    return json_response(["senders/", "receivers/"], request=request)

async def handle_get_xnmos_streamcompat_v10(request: web.Request) -> web.Response:
    """GET /x-nmos/streamcompatibility/v1.0 — List resources."""
    return json_response([
        "senders/", "receivers/", "inputs/", "outputs/",
    ], request=request)
