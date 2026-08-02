# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""NMOS IS-04 Registry — Registration API and Query API server.

This package implements the server side of AMWA IS-04 v1.3: the write-only
Registration API that Nodes POST their resources to, and the read-only Query
API (HTTP + WebSocket) that Controllers read them back from. It is the
counterpart to ``nmos/node/registry.py``, which is the *client* of the same
Registration API.

Why it exists
-------------
Running ``nmos-reference`` used to require installing and configuring a
third-party registry first. Shipping one in-tree makes the whole system —
Node, Controller, registry — runnable from a single checkout, which is the
difference between "clone and run" and "read a setup guide".

Normative sources (read them, do not paraphrase them)
-----------------------------------------------------
Everything here is written against the documents in ``nmos/registry/specs/``,
mirrored verbatim from AMWA IS-04 ``v1.3.x`` at tag ``v1.3.3``. See
``specs/README.md`` for provenance. The behaviour documents in particular —
``Behaviour - Registration.md`` and ``Behaviour - Querying.md`` — are cited
inline throughout this package by line number.

Security model
--------------
The two interfaces are deliberately NOT symmetric, and this asymmetry is
normative rather than a simplification.

``NMOS With Control Plane Security.md:105`` (IPMX TR-10-SEC) states:

    The IS-04 Registration API MUST not require the NMOS Nodes to use OAuth
    2.0 authorizations. The IS-04 Registration API MUST be secured using TLS
    with server authentication or mutual client-server authentication.

and ``:107`` requires ``api_auth`` of the Registry DNS-SD record to be false.
So:

* **Registration API** — no TLS, TLS server auth, or mTLS. Never OAuth 2.0.
  These three map exactly onto the Registry Access Policy (RAP) values 0, 1
  and 2 defined in the same document, which is why this package reuses
  ``nmos.node.security_tags.RAP`` rather than defining a parallel enum.
* **Query API** — the full five-mode matrix a Node supports: no TLS, TLS
  server auth, mTLS, OAuth 2.0 + TLS, OAuth 2.0 + mTLS. The OAuth 2.0 scope
  is ``"query"``, per the same document at line 439.

Note that this is a deliberate divergence from nmos-cpp, which does support
BCP-003-02 authorization on its Registration API.

Layout
------
``types.py``          ResourceType, RegisteredResource, events, cursors
``store.py``          resource storage, TAI paging cursors, health, GC, tombstones
``decode.py``         request-body decoding, which is also schema validation
``registry.py``       the Registry facade, and the per-interface security snapshot
``handlers_*.py``     aiohttp request handlers, one module per API
``paging.py``         paging cursor arithmetic and response headers
``query_filter.py``   basic queries and downgrade
``subscriptions.py``  subscription lifecycle and grain generation
``websocket.py``      the Query API WebSocket endpoint
``gc.py``             the garbage-collection and status-reporting tasks
"""

from __future__ import annotations

from typing import Any, cast

from aiohttp import web

from nmos.api import _trailing_slash_middleware
from nmos.api.middleware import check_oauth2, client_auth_middleware, cors_middleware
from nmos.api.response import options_response
from nmos.registry import handlers_query as query_h
from nmos.registry import handlers_registration as reg_h
from nmos.registry import handlers_root as root_h
from nmos.registry.registry import InterfaceSecurity, Registry
from nmos.registry.types import ResourceType
from nmos.registry.websocket import handle_subscription_websocket

__all__ = [
    "create_registration_app",
    "create_query_app",
    "create_query_ws_app",
    "InterfaceSecurity",
    "Registry",
]

# TR-10-SEC:439 fixes the OAuth 2.0 scope per API: "For IS-04 NodeAPI,
# QueryAPI, RegistrationAPI [...] the scope MUST be "node", "query",
# "registration" [...] respectively." Only the Query API uses one -- the
# Registration API must not require OAuth 2.0 at all (:105).
QUERY_SCOPE = "query"

# The RAML constrains ``{resourceId}`` / ``{nodeId}`` to a UUID
# (RegistrationAPI.raml:72, :127). Encoding that in the route pattern means a
# malformed id is a routing miss -- a clean 404 -- instead of reaching a
# handler that would have to re-validate it.
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"


def _new_app(security: InterfaceSecurity, registry: Registry) -> web.Application:
    """Create an app with the shared middleware stack and DI keys.

    The middleware chain is the Node's, imported rather than reimplemented:

    ``_trailing_slash_middleware``  ``APIs.md:83-92`` requires ``/path`` and
        ``/path/`` to behave identically. Routes are also registered in both
        forms; this middleware is the second-chance net.
    ``cors_middleware``             CORS headers, and conversion of aiohttp's
        own text/plain errors into the NMOS ``{code, error, debug}`` JSON body
        that ``APIs.md:102-114`` requires for every status >= 400.
    ``client_auth_middleware``      mTLS enforcement on state-changing verbs.

    Two DI keys are published. ``registry`` is what the registry's own
    handlers use. ``node`` is the key the shared middleware reads its
    configuration from -- see ``InterfaceSecurity`` for why an
    InterfaceSecurity is the right thing to put there.
    """
    middlewares: list[Any] = [
        _trailing_slash_middleware,
        cors_middleware,
        client_auth_middleware,
    ]
    app = web.Application(middlewares=middlewares)
    app["registry"] = registry
    app["node"] = security
    return app


def _add(app: web.Application, method: str, path: str, handler: Any) -> None:
    """Register a route in both its bare and trailing-slash forms.

    ``APIs.md:85`` requires GET/HEAD/OPTIONS to work either way, and ``:92``
    requires the state-changing verbs to work without a trailing slash and to
    avoid answering them with a redirect. Registering both spellings directly
    satisfies all of that without a single 3xx.
    """
    app.router.add_route(method, path, handler)
    app.router.add_route(method, path + "/", handler)


def create_registration_app(
    registry: Registry, security: InterfaceSecurity,
) -> web.Application:
    """Build the IS-04 Registration API application.

    Serves ``/x-nmos/registration/v1.3`` plus the discovery ladder above it.

    No route here is wrapped in ``check_oauth2``, and that is normative rather
    than incidental: ``NMOS With Control Plane Security.md:105`` requires that
    the Registration API "MUST not require the NMOS Nodes to use OAuth 2.0
    authorizations", and ``:107`` requires the registry's DNS-SD ``api_auth``
    to be false. Access control on this interface is TLS only — server
    authentication or mutual authentication — with mTLS enforced on every
    state-changing verb by ``client_auth_middleware``.
    """
    app = _new_app(security, registry)
    prefix = reg_h.BASE_PATH

    _add(app, "GET", "/", root_h.handle_get_root)
    _add(app, "GET", "/x-nmos", root_h.handle_get_xnmos_registration_root)
    _add(app, "GET", "/x-nmos/registration", root_h.handle_get_registration_versions)
    _add(app, "GET", prefix, root_h.handle_get_registration_base)

    _add(app, "POST", f"{prefix}/resource", reg_h.handle_post_resource)
    _add(app, "OPTIONS", f"{prefix}/resource", options_response)

    resource_path = (
        f"{prefix}/resource/{{resourceType}}/{{resourceId:{_UUID}}}"
    )
    _add(app, "DELETE", resource_path, reg_h.handle_delete_resource)
    _add(app, "GET", resource_path, reg_h.handle_get_resource)
    _add(app, "OPTIONS", resource_path, options_response)

    health_path = f"{prefix}/health/nodes/{{nodeId:{_UUID}}}"
    _add(app, "POST", health_path, reg_h.handle_post_health)
    _add(app, "GET", health_path, reg_h.handle_get_health)
    _add(app, "OPTIONS", health_path, options_response)

    return app


def create_query_app(
    registry: Registry,
    security: InterfaceSecurity,
    *,
    tls: bool = False,
    ws_port: int = 0,
    paging_limit: int = query_h.DEFAULT_PAGING_LIMIT,
    paging_limit_max: int = query_h.MAX_PAGING_LIMIT,
) -> web.Application:
    """Build the IS-04 Query API application.

    Unlike Registration, this interface supports the full five-mode security
    matrix, so every route is wrapped in ``check_oauth2`` with the ``query``
    scope. The decorator is a pass-through whenever ``security.oauth2`` is
    false, which is what makes the no-TLS and TLS-only modes work through the
    same code path.

    Reads use ``read_write=False`` and subscription writes use
    ``read_write=True``: creating a subscription allocates server-side state
    and a WebSocket, so it is not a read even though the Query API as a whole
    is described as read-only.

    Args:
        tls: Whether this listener runs TLS. Drives the ``ws``/``wss`` scheme
            of ``ws_href`` and the ``secure`` negotiation of
            ``Behaviour - Querying.md:13``.
        ws_port: Port of the companion WebSocket listener, used to build
            ``ws_href``.
        paging_limit: Server default page size.
        paging_limit_max: Largest page size the server will honour.
    """
    app = _new_app(security, registry)
    app["tls"] = tls
    app["ws_port"] = ws_port
    app["oauth2"] = security.oauth2
    app["paging_limit"] = paging_limit
    app["paging_limit_max"] = paging_limit_max

    prefix = query_h.BASE_PATH
    read = check_oauth2(False, QUERY_SCOPE)
    write = check_oauth2(True, QUERY_SCOPE)

    _add(app, "GET", "/", read(root_h.handle_get_root))
    _add(app, "GET", "/x-nmos", read(root_h.handle_get_xnmos_query_root))
    _add(app, "GET", "/x-nmos/query", read(root_h.handle_get_query_versions))
    _add(app, "GET", prefix, read(root_h.handle_get_query_base))

    # /subscriptions is registered BEFORE the generic {collection} routes.
    # aiohttp resolves in registration order, and "subscriptions" would
    # otherwise be captured by the {collection} pattern and rejected as an
    # unknown resource type.
    subscriptions = f"{prefix}/subscriptions"
    _add(app, "POST", subscriptions, write(query_h.handle_post_subscriptions))
    _add(app, "GET", subscriptions, read(query_h.handle_get_subscriptions))
    _add(app, "OPTIONS", subscriptions, options_response)

    subscription = f"{subscriptions}/{{subscriptionId:{_UUID}}}"
    _add(app, "GET", subscription, read(query_h.handle_get_subscription))
    _add(app, "DELETE", subscription, write(query_h.handle_delete_subscription))
    _add(app, "OPTIONS", subscription, options_response)

    # One route for all six collections, with the segment constrained to the
    # exact plural names. Restricting the pattern rather than accepting any
    # word means an unknown collection is a routing miss -- a clean 404 --
    # instead of reaching a handler that would have to re-validate it, while
    # still giving the handler a ``{collection}`` match to dispatch on.
    collections = "|".join(rt.plural for rt in ResourceType)
    collection_path = f"{prefix}/{{collection:{collections}}}"
    _add(app, "GET", collection_path, read(query_h.handle_get_collection))
    _add(
        app, "GET", f"{collection_path}/{{resourceId:{_UUID}}}",
        read(query_h.handle_get_resource),
    )

    return app


def create_query_ws_app(
    registry: Registry, security: InterfaceSecurity,
) -> web.Application:
    """Build the Query API WebSocket listener.

    A separate application on its own port, because ``ws_href`` advertises a
    distinct port (the Node's ``--rdsWebSocketPort`` defaults to 8448 against
    a query port of 8446) and because a WebSocket upgrade and a REST API have
    different lifetimes.

    The upgrade is a GET, so ``check_oauth2`` with ``read_write=False`` gates
    it exactly as it gates a collection read. That is what makes the
    ``authorization`` attribute of a subscription meaningful: when the Query
    API requires a token, so does the socket it hands out.
    """
    app = _new_app(security, registry)
    read = check_oauth2(False, QUERY_SCOPE)

    # ``check_oauth2`` is typed against ``Callable[[Request], Awaitable[Response]]``
    # -- the shape every other NMOS handler has. A WebSocket upgrade returns a
    # ``WebSocketResponse``, which is a ``StreamResponse`` but not a
    # ``Response``, so the annotation does not fit. The decorator itself is
    # indifferent: it either short-circuits with its own error response or
    # awaits and returns whatever the handler produced.
    #
    # Widening the shared alias would be the tidier fix on paper, but
    # ``cors_middleware`` is declared to return ``Response`` and mutates the
    # object it gets, so widening ripples into Node code this package has no
    # business changing. The cast is confined to this one line instead, where
    # the reason for it can be stated.
    gated_ws = read(cast("Any", handle_subscription_websocket))

    path = f"{query_h.BASE_PATH}/subscriptions/{{subscriptionId:{_UUID}}}"
    _add(app, "GET", path, gated_ws)
    return app
