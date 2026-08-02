# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Discovery endpoints for both registry interfaces.

Each API exposes a ladder of index resources — ``/`` → ``/x-nmos`` →
``/x-nmos/<api>`` → ``/x-nmos/<api>/v1.3`` → the resources themselves — so a
client can walk down from the root without prior knowledge of the layout.

The two interfaces get separate ladders on purpose. The Registration and Query
APIs listen on different ports with different security policies (see the
package docstring), so advertising ``query/`` from the registration port would
point a client at an endpoint that is not there. Each port lists only what it
actually serves.

The contents of the two version-level responses are fixed by schema, not by
choice: ``registrationapi-base.json`` requires exactly
``["resource/", "health/"]``, and ``queryapi-base.json`` requires exactly the
seven collection names.
"""

from __future__ import annotations

from aiohttp import web

from nmos.api.response import json_response
from nmos.registry.types import ResourceType

API_VERSION = "v1.3"


# ---------------------------------------------------------------------------
# Shared root
# ---------------------------------------------------------------------------

async def handle_get_root(request: web.Request) -> web.Response:
    """GET / — the only API family this process serves."""
    return json_response(["x-nmos/"], request=request)


# ---------------------------------------------------------------------------
# Registration interface
# ---------------------------------------------------------------------------

async def handle_get_xnmos_registration_root(
    request: web.Request,
) -> web.Response:
    """GET /x-nmos — APIs available on the registration port."""
    return json_response(["registration/"], request=request)


async def handle_get_registration_versions(
    request: web.Request,
) -> web.Response:
    """GET /x-nmos/registration — supported Registration API versions."""
    return json_response([f"{API_VERSION}/"], request=request)


async def handle_get_registration_base(request: web.Request) -> web.Response:
    """GET /x-nmos/registration/v1.3 — the two Registration API resources.

    ``registrationapi-base.json`` pins this to exactly these two entries,
    with ``minItems``/``maxItems`` both 2.
    """
    return json_response(["resource/", "health/"], request=request)


# ---------------------------------------------------------------------------
# Query interface
# ---------------------------------------------------------------------------

async def handle_get_xnmos_query_root(request: web.Request) -> web.Response:
    """GET /x-nmos — APIs available on the query port."""
    return json_response(["query/"], request=request)


async def handle_get_query_versions(request: web.Request) -> web.Response:
    """GET /x-nmos/query — supported Query API versions."""
    return json_response([f"{API_VERSION}/"], request=request)


async def handle_get_query_base(request: web.Request) -> web.Response:
    """GET /x-nmos/query/v1.3 — the seven Query API collections.

    ``queryapi-base.json`` pins this to exactly seven entries: the six
    resource collections in IS-04 order, plus ``subscriptions/``.
    """
    collections = [f"{rt.plural}/" for rt in ResourceType]
    collections.append("subscriptions/")
    return json_response(collections, request=request)
