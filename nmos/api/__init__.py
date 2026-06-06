# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""NMOS Node REST API — aiohttp application factory.

Creates an aiohttp web.Application with all NMOS API routes registered:
- IS-04 Node API (v1.3)
- IS-05 Connection API (v1.1, v1.2)
- IS-11 Stream Compatibility API (v1.0)
- x-manufacturer Exclusive Session API
- Root/Discovery endpoints
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, cast

from aiohttp import web

from nmos.api.middleware import cors_middleware, check_oauth2
from nmos.api import handlers_root as root
from nmos.api import handlers_node as node_h
from nmos.api import handlers_connection as conn_h
from nmos.api import handlers_compat as compat_h
from nmos.api import handlers_exclusive as excl_h
from nmos.api.response import options_response, error_response

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


@web.middleware
async def _trailing_slash_middleware(
    request: web.Request,
    handler: Handler,
) -> web.StreamResponse:
    """NMOS requires both /path and /path/ to work identically.

    If the current handler raises 404 or 405, try resolving with the alternate
    path form (add or remove trailing slash). Falls back to JSON error response.
    """
    from nmos.api.response import error_response

    try:
        return await handler(request)
    except (web.HTTPNotFound, web.HTTPMethodNotAllowed) as original_exc:
        path = request.path
        alt_path = path.rstrip("/") if path.endswith("/") else path + "/"

        # Try to resolve the alternate path
        try:
            match_info = await request.app.router.resolve(request.clone(rel_url=alt_path))
            if isinstance(match_info, web.UrlMappingMatchInfo):
                mutable_match_info = cast(Any, match_info)
                mutable_request = cast(Any, request)
                mutable_match_info._apps = [request.app]
                mutable_match_info._current_app = request.app
                mutable_request._match_info = match_info
                return await match_info.handler(request)
        except (web.HTTPException, ValueError, KeyError):
            pass

        # No alt route — return JSON error
        return error_response(original_exc.status, original_exc.reason or "", request=request)


def create_app(node: Any) -> web.Application:
    """Create the aiohttp Application with all NMOS routes.

    Used when IS-05/IS-11 share the Node API listener (the default,
    pre-``--controlTrustedRootCA`` topology). When the operator opts
    into split listeners, ``create_node_app`` and ``create_control_app``
    are used instead.

    Args:
        node: The initialized Node object (nmos.node.Node)

    Returns:
        Configured aiohttp.web.Application ready to run
    """
    return _create_app(node, include_node=True, include_control=True)


def create_node_app(node: Any) -> web.Application:
    """App for the Node port in split-listener mode.

    Registers everything *except* the IS-05/IS-11 route groups: IS-04
    Node API, x-manufacturer exclusive session, and the root-level
    discovery routes that point at those APIs. Pair with
    ``create_control_app`` to fully cover the surface.
    """
    return _create_app(node, include_node=True, include_control=False)


def create_control_app(node: Any) -> web.Application:
    """App for the Control port in split-listener mode.

    Registers IS-05 + IS-11 routes plus their root-level discovery
    endpoints (``GET /x-nmos/connection`` / ``…/streamcompatibility``).
    Top-level ``GET /`` and ``GET /x-nmos`` are deliberately NOT
    registered here — a control-only port has no meaningful root
    listing that mixes Node-API discovery; clients call the IS-05 /
    IS-11 routes directly via the URLs published in the Node's
    ``device.controls[]``.
    """
    return _create_app(node, include_node=False, include_control=True)


def _create_app(
    node: Any,
    *,
    include_node: bool,
    include_control: bool,
) -> web.Application:
    """Shared factory backing all three ``create_*_app`` entry points."""
    from nmos.api.middleware import client_auth_middleware
    middlewares: list[Any] = [
        _trailing_slash_middleware,
        cors_middleware,
        client_auth_middleware,
    ]
    app = web.Application(middlewares=middlewares)
    app["node"] = node

    if include_node:
        _register_root_routes_node(app)
        _register_node_routes(app)
        _register_exclusive_routes(app)
    if include_control:
        _register_root_routes_control(app)
        _register_connection_routes(app)
        _register_compat_routes(app)

    return app


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

def _register_root_routes_node(app: web.Application) -> None:
    """Root + Node-API + manufacturer discovery endpoints.

    Split from the combined root-route registration so the
    ``create_node_app`` factory can omit the IS-05/IS-11 discovery
    entries that ``create_control_app`` owns.
    """
    ro_node = check_oauth2(False, "node")
    ro_mfr = check_oauth2(False, "manufacturer")

    app.router.add_get("/", ro_node(root.handle_get_root))
    app.router.add_get("/x-nmos", ro_node(root.handle_get_xnmos))
    app.router.add_get("/x-nmos/", ro_node(root.handle_get_xnmos))
    app.router.add_get("/x-nmos/node", ro_node(root.handle_get_xnmos_node))
    app.router.add_get("/x-nmos/node/", ro_node(root.handle_get_xnmos_node))
    app.router.add_get("/x-manufacturer", ro_mfr(root.handle_get_manufacturer))
    app.router.add_get("/x-manufacturer/", ro_mfr(root.handle_get_manufacturer))


def _register_root_routes_control(app: web.Application) -> None:
    """Top-level discovery for IS-05 / IS-11 (``GET /x-nmos/connection``,
    ``GET /x-nmos/streamcompatibility``). Used by both the unified app
    and the split control-only app so the discovery surface is identical."""
    ro_conn = check_oauth2(False, "connection")
    ro_sc = check_oauth2(False, "streamcompatibility")

    app.router.add_get("/x-nmos/connection", ro_conn(root.handle_get_xnmos_connection))
    app.router.add_get("/x-nmos/connection/", ro_conn(root.handle_get_xnmos_connection))
    app.router.add_get("/x-nmos/streamcompatibility", ro_sc(root.handle_get_xnmos_streamcompat))
    app.router.add_get("/x-nmos/streamcompatibility/", ro_sc(root.handle_get_xnmos_streamcompat))


def _register_node_routes(app: web.Application) -> None:
    """IS-04 Node API v1.3 endpoints."""
    prefix = "/x-nmos/node/v1.3"

    # All Node API GET endpoints require OAuth2 read access (read_write=False)
    # per spec: "ReadOnly access to a Node's API MUST be blocked if one of the
    # following claims reject Read access."
    ro_node = check_oauth2(False, "node")

    app.router.add_get(f"{prefix}", ro_node(root.handle_get_xnmos_node_v13))
    app.router.add_get(f"{prefix}/", ro_node(root.handle_get_xnmos_node_v13))

    app.router.add_get(f"{prefix}/self", ro_node(node_h.handle_get_self))
    app.router.add_get(f"{prefix}/self/", ro_node(node_h.handle_get_self))

    app.router.add_get(f"{prefix}/devices", ro_node(node_h.handle_get_devices))
    app.router.add_get(f"{prefix}/devices/", ro_node(node_h.handle_get_devices))
    app.router.add_get(f"{prefix}/devices/{{deviceId}}", ro_node(node_h.handle_get_device))
    app.router.add_get(f"{prefix}/devices/{{deviceId}}/", ro_node(node_h.handle_get_device))

    app.router.add_get(f"{prefix}/sources", ro_node(node_h.handle_get_sources))
    app.router.add_get(f"{prefix}/sources/", ro_node(node_h.handle_get_sources))
    app.router.add_get(f"{prefix}/sources/{{sourceId}}", ro_node(node_h.handle_get_source))
    app.router.add_get(f"{prefix}/sources/{{sourceId}}/", ro_node(node_h.handle_get_source))

    app.router.add_get(f"{prefix}/flows", ro_node(node_h.handle_get_flows))
    app.router.add_get(f"{prefix}/flows/", ro_node(node_h.handle_get_flows))
    app.router.add_get(f"{prefix}/flows/{{flowId}}", ro_node(node_h.handle_get_flow))
    app.router.add_get(f"{prefix}/flows/{{flowId}}/", ro_node(node_h.handle_get_flow))

    app.router.add_get(f"{prefix}/senders", ro_node(node_h.handle_get_senders))
    app.router.add_get(f"{prefix}/senders/", ro_node(node_h.handle_get_senders))
    app.router.add_get(f"{prefix}/senders/{{senderId}}", ro_node(node_h.handle_get_sender))
    app.router.add_get(f"{prefix}/senders/{{senderId}}/", ro_node(node_h.handle_get_sender))

    app.router.add_get(f"{prefix}/receivers", ro_node(node_h.handle_get_receivers))
    app.router.add_get(f"{prefix}/receivers/", ro_node(node_h.handle_get_receivers))
    app.router.add_get(f"{prefix}/receivers/{{receiverId}}", ro_node(node_h.handle_get_receiver))
    app.router.add_get(f"{prefix}/receivers/{{receiverId}}/", ro_node(node_h.handle_get_receiver))


def _register_connection_routes(app: web.Application) -> None:
    """IS-05 Connection API v1.0 (and v1.1 rewrite) endpoints."""
    # Serves v1.1 and v1.2 (v1.2 is a rewrite to v1.1 handlers).
    for ver in ("v1.1", "v1.2"):
        prefix = f"/x-nmos/connection/{ver}"
        ro_conn = check_oauth2(False, "connection")

        app.router.add_get(f"{prefix}", ro_conn(root.handle_get_xnmos_connection_v10))
        app.router.add_get(f"{prefix}/", ro_conn(root.handle_get_xnmos_connection_v10))

        app.router.add_get(f"{prefix}/single", ro_conn(root.handle_get_xnmos_connection_v10_single))
        app.router.add_get(f"{prefix}/single/", ro_conn(root.handle_get_xnmos_connection_v10_single))

        # Senders
        app.router.add_get(f"{prefix}/single/senders", ro_conn(conn_h.handle_get_single_senders))
        app.router.add_get(f"{prefix}/single/senders/", ro_conn(conn_h.handle_get_single_senders))
        app.router.add_get(f"{prefix}/single/senders/{{senderId}}", ro_conn(conn_h.handle_get_single_sender))
        app.router.add_get(f"{prefix}/single/senders/{{senderId}}/", ro_conn(conn_h.handle_get_single_sender))

        app.router.add_get(f"{prefix}/single/senders/{{senderId}}/staged", ro_conn(conn_h.handle_get_sender_staged))
        app.router.add_get(f"{prefix}/single/senders/{{senderId}}/staged/", ro_conn(conn_h.handle_get_sender_staged))
        app.router.add_patch(f"{prefix}/single/senders/{{senderId}}/staged",
                             check_oauth2(True, "connection")(conn_h.handle_patch_sender_staged))
        app.router.add_patch(f"{prefix}/single/senders/{{senderId}}/staged/",
                             check_oauth2(True, "connection")(conn_h.handle_patch_sender_staged))
        app.router.add_route("OPTIONS", f"{prefix}/single/senders/{{senderId}}/staged",
                             options_response)

        app.router.add_get(f"{prefix}/single/senders/{{senderId}}/active", ro_conn(conn_h.handle_get_sender_active))
        app.router.add_get(f"{prefix}/single/senders/{{senderId}}/active/", ro_conn(conn_h.handle_get_sender_active))

        app.router.add_get(f"{prefix}/single/senders/{{senderId}}/constraints", ro_conn(conn_h.handle_get_sender_constraints))
        app.router.add_get(f"{prefix}/single/senders/{{senderId}}/constraints/", ro_conn(conn_h.handle_get_sender_constraints))

        app.router.add_get(f"{prefix}/single/senders/{{senderId}}/transportfile", ro_conn(conn_h.handle_get_sender_transportfile))
        app.router.add_get(f"{prefix}/single/senders/{{senderId}}/transportfile/", ro_conn(conn_h.handle_get_sender_transportfile))
        app.router.add_get(f"{prefix}/single/senders/{{senderId}}/transporttype", ro_conn(conn_h.handle_get_sender_transporttype))
        app.router.add_get(f"{prefix}/single/senders/{{senderId}}/transporttype/", ro_conn(conn_h.handle_get_sender_transporttype))

        # Receivers
        app.router.add_get(f"{prefix}/single/receivers", ro_conn(conn_h.handle_get_single_receivers))
        app.router.add_get(f"{prefix}/single/receivers/", ro_conn(conn_h.handle_get_single_receivers))
        app.router.add_get(f"{prefix}/single/receivers/{{receiverId}}", ro_conn(conn_h.handle_get_single_receiver))
        app.router.add_get(f"{prefix}/single/receivers/{{receiverId}}/", ro_conn(conn_h.handle_get_single_receiver))

        app.router.add_get(f"{prefix}/single/receivers/{{receiverId}}/staged", ro_conn(conn_h.handle_get_receiver_staged))
        app.router.add_get(f"{prefix}/single/receivers/{{receiverId}}/staged/", ro_conn(conn_h.handle_get_receiver_staged))
        app.router.add_patch(f"{prefix}/single/receivers/{{receiverId}}/staged",
                             check_oauth2(True, "connection")(conn_h.handle_patch_receiver_staged))
        app.router.add_patch(f"{prefix}/single/receivers/{{receiverId}}/staged/",
                             check_oauth2(True, "connection")(conn_h.handle_patch_receiver_staged))
        app.router.add_route("OPTIONS", f"{prefix}/single/receivers/{{receiverId}}/staged",
                             options_response)

        app.router.add_get(f"{prefix}/single/receivers/{{receiverId}}/active", ro_conn(conn_h.handle_get_receiver_active))
        app.router.add_get(f"{prefix}/single/receivers/{{receiverId}}/active/", ro_conn(conn_h.handle_get_receiver_active))

        app.router.add_get(f"{prefix}/single/receivers/{{receiverId}}/constraints", ro_conn(conn_h.handle_get_receiver_constraints))
        app.router.add_get(f"{prefix}/single/receivers/{{receiverId}}/constraints/", ro_conn(conn_h.handle_get_receiver_constraints))

        app.router.add_get(f"{prefix}/single/receivers/{{receiverId}}/transporttype", ro_conn(conn_h.handle_get_receiver_transporttype))
        app.router.add_get(f"{prefix}/single/receivers/{{receiverId}}/transporttype/", ro_conn(conn_h.handle_get_receiver_transporttype))

        # Bulk listing
        app.router.add_get(f"{prefix}/bulk", root.handle_get_xnmos_connection_v10_bulk)
        app.router.add_get(f"{prefix}/bulk/", root.handle_get_xnmos_connection_v10_bulk)

        # Bulk endpoints (POST for bulk operations, GET on specific returns 405 per IS-05)
        app.router.add_post(f"{prefix}/bulk/senders", conn_h.handle_post_bulk_senders)
        app.router.add_post(f"{prefix}/bulk/senders/", conn_h.handle_post_bulk_senders)
        app.router.add_post(f"{prefix}/bulk/receivers", conn_h.handle_post_bulk_receivers)
        app.router.add_post(f"{prefix}/bulk/receivers/", conn_h.handle_post_bulk_receivers)
        app.router.add_get(f"{prefix}/bulk/senders", conn_h.handle_get_bulk_not_allowed)
        app.router.add_get(f"{prefix}/bulk/senders/", conn_h.handle_get_bulk_not_allowed)
        app.router.add_get(f"{prefix}/bulk/receivers", conn_h.handle_get_bulk_not_allowed)
        app.router.add_get(f"{prefix}/bulk/receivers/", conn_h.handle_get_bulk_not_allowed)


def _register_compat_routes(app: web.Application) -> None:
    """IS-11 Stream Compatibility API v1.0 endpoints."""
    prefix = "/x-nmos/streamcompatibility/v1.0"

    ro_sc = check_oauth2(False, "streamcompatibility")

    app.router.add_get(f"{prefix}", ro_sc(root.handle_get_xnmos_streamcompat_v10))
    app.router.add_get(f"{prefix}/", ro_sc(root.handle_get_xnmos_streamcompat_v10))

    # Senders
    app.router.add_get(f"{prefix}/senders", ro_sc(compat_h.handle_get_senders))
    app.router.add_get(f"{prefix}/senders/", ro_sc(compat_h.handle_get_senders))
    app.router.add_get(f"{prefix}/senders/{{senderId}}", ro_sc(compat_h.handle_get_sender))
    app.router.add_get(f"{prefix}/senders/{{senderId}}/", ro_sc(compat_h.handle_get_sender))
    app.router.add_get(f"{prefix}/senders/{{senderId}}/status", ro_sc(compat_h.handle_get_sender_status))
    app.router.add_get(f"{prefix}/senders/{{senderId}}/status/", ro_sc(compat_h.handle_get_sender_status))
    app.router.add_get(f"{prefix}/senders/{{senderId}}/inputs", ro_sc(compat_h.handle_get_sender_inputs))
    app.router.add_get(f"{prefix}/senders/{{senderId}}/inputs/", ro_sc(compat_h.handle_get_sender_inputs))
    app.router.add_get(f"{prefix}/senders/{{senderId}}/constraints", ro_sc(compat_h.handle_get_sender_constraints))
    app.router.add_get(f"{prefix}/senders/{{senderId}}/constraints/", ro_sc(compat_h.handle_get_sender_constraints))
    app.router.add_get(f"{prefix}/senders/{{senderId}}/constraints/supported", ro_sc(compat_h.handle_get_sender_constraints_supported))
    app.router.add_get(f"{prefix}/senders/{{senderId}}/constraints/supported/", ro_sc(compat_h.handle_get_sender_constraints_supported))
    app.router.add_get(f"{prefix}/senders/{{senderId}}/constraints/active", ro_sc(compat_h.handle_get_sender_constraints_active))
    app.router.add_get(f"{prefix}/senders/{{senderId}}/constraints/active/", ro_sc(compat_h.handle_get_sender_constraints_active))
    app.router.add_put(f"{prefix}/senders/{{senderId}}/constraints/active",
                       check_oauth2(True, "streamcompatibility")(compat_h.handle_put_sender_constraints_active))
    app.router.add_put(f"{prefix}/senders/{{senderId}}/constraints/active/",
                       check_oauth2(True, "streamcompatibility")(compat_h.handle_put_sender_constraints_active))
    app.router.add_delete(f"{prefix}/senders/{{senderId}}/constraints/active",
                          check_oauth2(True, "streamcompatibility")(compat_h.handle_delete_sender_constraints_active))
    app.router.add_delete(f"{prefix}/senders/{{senderId}}/constraints/active/",
                          check_oauth2(True, "streamcompatibility")(compat_h.handle_delete_sender_constraints_active))
    app.router.add_route("OPTIONS", f"{prefix}/senders/{{senderId}}/constraints/active",
                         options_response)
    app.router.add_route("OPTIONS", f"{prefix}/senders/{{senderId}}/constraints/active/",
                         options_response)

    # Receivers
    app.router.add_get(f"{prefix}/receivers", ro_sc(compat_h.handle_get_receivers))
    app.router.add_get(f"{prefix}/receivers/", ro_sc(compat_h.handle_get_receivers))
    app.router.add_get(f"{prefix}/receivers/{{receiverId}}", ro_sc(compat_h.handle_get_receiver))
    app.router.add_get(f"{prefix}/receivers/{{receiverId}}/", ro_sc(compat_h.handle_get_receiver))
    app.router.add_get(f"{prefix}/receivers/{{receiverId}}/status", ro_sc(compat_h.handle_get_receiver_status))
    app.router.add_get(f"{prefix}/receivers/{{receiverId}}/status/", ro_sc(compat_h.handle_get_receiver_status))
    app.router.add_get(f"{prefix}/receivers/{{receiverId}}/outputs", ro_sc(compat_h.handle_get_receiver_outputs))
    app.router.add_get(f"{prefix}/receivers/{{receiverId}}/outputs/", ro_sc(compat_h.handle_get_receiver_outputs))

    # Inputs / Outputs
    app.router.add_get(f"{prefix}/inputs", ro_sc(compat_h.handle_get_inputs))
    app.router.add_get(f"{prefix}/inputs/", ro_sc(compat_h.handle_get_inputs))
    app.router.add_get(f"{prefix}/outputs", ro_sc(compat_h.handle_get_outputs))
    app.router.add_get(f"{prefix}/outputs/", ro_sc(compat_h.handle_get_outputs))


def _register_exclusive_routes(app: web.Application) -> None:
    """x-manufacturer Exclusive Session (Node Reservation) endpoints."""
    prefix = "/x-manufacturer/exclusive/v1.0"

    app.router.add_post(f"{prefix}/acquire",
                        check_oauth2(True, "manufacturer")(excl_h.handle_post_acquire))
    app.router.add_post(f"{prefix}/acquire/",
                        check_oauth2(True, "manufacturer")(excl_h.handle_post_acquire))
    app.router.add_post(f"{prefix}/renew",
                        check_oauth2(True, "manufacturer")(excl_h.handle_post_renew))
    app.router.add_post(f"{prefix}/renew/",
                        check_oauth2(True, "manufacturer")(excl_h.handle_post_renew))
    app.router.add_post(f"{prefix}/release",
                        check_oauth2(True, "manufacturer")(excl_h.handle_post_release))
    app.router.add_post(f"{prefix}/release/",
                        check_oauth2(True, "manufacturer")(excl_h.handle_post_release))
    app.router.add_post(f"{prefix}/keepalive",
                        check_oauth2(True, "manufacturer")(excl_h.handle_post_keepalive))
    app.router.add_post(f"{prefix}/keepalive/",
                        check_oauth2(True, "manufacturer")(excl_h.handle_post_keepalive))
