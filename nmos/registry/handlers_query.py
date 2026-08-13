# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""IS-04 Query API v1.3 handlers.

Implements ``QueryAPI.raml``: the six resource collections, single-resource
reads, and the ``/subscriptions`` lifecycle.

Every collection response is paged (``paging.py``) and filtered
(``query_filter.py``), in that order — ``APIs - Query Parameters.md:26``
requires filters to be applied before paging, or a page would be a slice of
the wrong set.

Status codes
============

===============================================  ======  =========================
Situation                                        Status  Source
===============================================  ======  =========================
Collection or single resource read               200     QueryAPI.raml:143, :160
Unknown resource id                              404     QueryAPI.raml:164
Malformed paging / downgrade parameter           400     QueryAPI.raml:72
RQL or ancestry requested                        501     Query Parameters.md:528, :578
Subscription created                             201     QueryAPI.raml:418
Existing subscription returned                   200     QueryAPI.raml:425
Subscription request invalid                     400     QueryAPI.raml:432
Subscription deleted                             204     QueryAPI.raml:479
DELETE of a non-persistent subscription          403     QueryAPI.raml:481
Unknown subscription                             404     QueryAPI.raml:485
===============================================  ======  =========================

The 501s matter more than they look. RQL and ancestry are both MAY features,
and the specification requires an explicit 501 when they are unimplemented
rather than allowing them to be ignored — a silently-ignored filter would hand
a client an unfiltered set that it would treat as filtered.
"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from nmos.api.response import error_response, json_response, status_response
from nmos.json.engine import JsonEngine
from nmos.registry import query_filter
from nmos.registry.links import make_link_resolver
from nmos.registry.paging import (
    PagingError,
    apply_paging,
    paging_headers,
    parse_paging,
)
from nmos.registry.query_filter import QueryError, UnsupportedQuery
from nmos.registry.registry import Registry
from nmos.registry.subscriptions import SubscriptionError
from nmos.registry.types import ResourceType

log = logging.getLogger(__name__)

API_VERSION = "v1.3"
BASE_PATH = f"/x-nmos/query/{API_VERSION}"

# Server paging defaults. These are nmos-cpp's ``query_paging_default`` and
# ``query_paging_limit``, so a client tuned against an nmos-cpp registry sees
# the same page sizes here. Overridden per-process from the CLI.
DEFAULT_PAGING_LIMIT = 10
MAX_PAGING_LIMIT = 100


def _registry(request: web.Request) -> Registry:
    registry: Registry = request.app["registry"]
    return registry


def _limits(request: web.Request) -> tuple[int, int]:
    """The configured (default, maximum) page size for this app."""
    return (
        request.app.get("paging_limit", DEFAULT_PAGING_LIMIT),
        request.app.get("paging_limit_max", MAX_PAGING_LIMIT),
    )


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------

async def handle_get_collection(request: web.Request) -> web.Response:
    """GET /x-nmos/query/v1.3/{collection} — list a resource type.

    Order of operations is fixed by the specification: reject unsupported
    query features, validate downgrade, filter, then page.
    """
    resource_type = ResourceType.from_plural(request.match_info["collection"])
    if resource_type is None:
        # Not reachable through the registered routes, which enumerate the six
        # collections explicitly; kept because the handler is also reachable
        # from tests and a future dynamic route would otherwise fail obscurely.
        return error_response(
            404, f"unknown collection {request.match_info['collection']!r}",
            request=request,
        )

    params = dict(request.query)

    try:
        query_filter.check_unsupported(params)
    except UnsupportedQuery as exc:
        return error_response(501, str(exc), request=request)

    try:
        query_filter.check_downgrade(params)
    except QueryError as exc:
        return error_response(400, str(exc), request=request)

    default_limit, max_limit = _limits(request)
    try:
        paging = parse_paging(
            params, default_limit=default_limit, max_limit=max_limit,
        )
    except PagingError as exc:
        return error_response(400, str(exc), request=request)

    store = _registry(request).store
    # Read in the order this request pages by, so nothing downstream has to
    # sort. The store maintains both orders incrementally, and a filtered
    # subsequence of a sorted sequence is still sorted, so ``matched`` below
    # inherits the ordering for free -- which is what ``presorted`` asserts.
    collection = list(store.iter_ordered(resource_type, paging.order))

    filters = query_filter.filter_params(params)
    matched = (
        collection if not filters
        else [
            resource for resource in collection
            if query_filter.matches(resource.raw, filters)
        ]
    )

    page = apply_paging(matched, collection, paging, presorted=True)

    response = json_response(
        [r.raw for r in page.resources],
        request=request,
        link_resolver=make_link_resolver(str(request.path), BASE_PATH),
    )
    base_url = str(request.url.with_query(None))
    for name, value in paging_headers(
        page, base_url, filters, paging.order,
    ).items():
        response.headers[name] = value
    return response


async def handle_get_resource(request: web.Request) -> web.Response:
    """GET /x-nmos/query/v1.3/{collection}/{resourceId} — one resource.

    Carries the ``downgrade`` trait but not ``paged`` (``QueryAPI.raml:157``),
    so paging parameters are simply not consulted here.

    A 409 would be returned if the resource existed at a lower API version and
    no adequate downgrade was requested (``:168-174``). This registry holds
    v1.3 only, so a resource either matches the requested version or does not
    exist — see the note in ``handlers_registration.py``.
    """
    resource_type = ResourceType.from_plural(request.match_info["collection"])
    if resource_type is None:
        return error_response(
            404, f"unknown collection {request.match_info['collection']!r}",
            request=request,
        )

    params = dict(request.query)
    try:
        query_filter.check_downgrade(params)
    except QueryError as exc:
        return error_response(400, str(exc), request=request)

    resource_id = request.match_info["resourceId"]
    resource = _registry(request).store.get(resource_type, resource_id)
    if resource is None:
        return error_response(
            404,
            f"{resource_type.value} {resource_id} was not found",
            request=request,
        )
    return json_response(
        resource.raw,
        request=request,
        link_resolver=make_link_resolver(str(request.path), BASE_PATH),
    )


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------

def _subscription_location(subscription_id: str) -> str:
    return f"{BASE_PATH}/subscriptions/{subscription_id}"


def _ws_target(request: web.Request) -> tuple[str, str]:
    """The scheme and authority to advertise in ``ws_href``.

    ``secure`` selects ``ws://`` or ``wss://`` (``Behaviour - Querying.md:13``).
    The host is the one the client used to reach us, so the URL it is handed
    stays reachable by the same route; only the port is substituted, because
    the WebSocket listener is a separate socket from the HTTP one.
    """
    app = request.app
    tls: bool = app.get("tls", False)
    ws_port: int = app.get("ws_port", 0)

    host = request.url.host or "localhost"
    scheme = "wss" if tls else "ws"
    authority = f"{host}:{ws_port}" if ws_port else host
    return scheme, authority


async def handle_post_subscriptions(request: web.Request) -> web.Response:
    """POST /x-nmos/query/v1.3/subscriptions — create or match a subscription."""
    registry = _registry(request)

    try:
        body = JsonEngine.parse_any(await request.text())
    except (ValueError, TypeError) as exc:
        return error_response(400, f"invalid JSON body: {exc}", request=request)

    if not isinstance(body, dict):
        return error_response(400, "expected a JSON object", request=request)

    # queryapi-subscriptions-post-request.json:
    # required = [max_update_rate_ms, persist, resource_path, params]
    missing = [
        name for name in
        ("max_update_rate_ms", "persist", "resource_path", "params")
        if name not in body
    ]
    if missing:
        return error_response(
            400, f"missing required attributes: {', '.join(missing)}",
            request=request,
        )

    resource_path = body["resource_path"]
    if not isinstance(resource_path, str):
        return error_response(
            400, "resource_path must be a string", request=request,
        )

    max_update_rate_ms = body["max_update_rate_ms"]
    if not isinstance(max_update_rate_ms, int) or isinstance(max_update_rate_ms, bool):
        return error_response(
            400, "max_update_rate_ms must be an integer", request=request,
        )
    if max_update_rate_ms < 0:
        return error_response(
            400, "max_update_rate_ms must not be negative", request=request,
        )

    persist = body["persist"]
    if not isinstance(persist, bool):
        return error_response(400, "persist must be a boolean", request=request)

    raw_params = body["params"]
    if not isinstance(raw_params, dict):
        return error_response(400, "params must be an object", request=request)
    # Filter values arrive as JSON but are compared as query-string text --
    # see ``query_filter._scalar_matches`` for the rendering rules.
    params = {str(k): _param_text(v) for k, v in raw_params.items()}

    tls: bool = request.app.get("tls", False)
    oauth2: bool = request.app.get("oauth2", False)

    # ``Behaviour - Querying.md:13`` -- if the client does not specify, the
    # server assigns false for HTTP and true for HTTPS. A client MAY request
    # the opposite "however they will receive a 400 (Bad Request) response
    # code unless the Query API explicitly supports a mismatch". This one
    # does not: the WebSocket listener shares the HTTP listener's TLS
    # configuration, so a mismatch is not merely unsupported, it is
    # unimplementable.
    secure = body.get("secure", tls)
    if not isinstance(secure, bool):
        return error_response(400, "secure must be a boolean", request=request)
    if secure != tls:
        return error_response(
            400,
            f"secure={str(secure).lower()} was requested but this Query API "
            f"serves {'HTTPS' if tls else 'HTTP'}; a mismatch between "
            f"encrypted and insecure HTTP and WebSocket connections is not "
            f"supported",
            request=request,
        )

    # ``:15`` -- the same rule for ``authorization``.
    authorization = body.get("authorization", oauth2)
    if not isinstance(authorization, bool):
        return error_response(
            400, "authorization must be a boolean", request=request,
        )
    if authorization != oauth2:
        return error_response(
            400,
            f"authorization={str(authorization).lower()} was requested but "
            f"this Query API is operating with authorization "
            f"{'enabled' if oauth2 else 'disabled'}",
            request=request,
        )

    ws_scheme, ws_host = _ws_target(request)
    try:
        subscription, created = registry.subscriptions.create_or_match(
            resource_path=resource_path,
            params=params,
            max_update_rate_ms=max_update_rate_ms,
            persist=persist,
            secure=secure,
            authorization=authorization,
            host=request.headers.get("Host", ""),
            ws_scheme=ws_scheme,
            ws_host=ws_host,
        )
    except SubscriptionError as exc:
        return error_response(400, str(exc), request=request)

    response = json_response(
        subscription.to_json(),
        status=201 if created else 200,
        request=request,
    )
    response.headers["Location"] = _subscription_location(subscription.id)
    return response


def _param_text(value: Any) -> str:
    """Render a JSON filter value as the text a query string would carry.

    ``params`` is a free-form object, so a client may send ``true`` or ``5``
    where the query-string form would have ``"true"`` or ``"5"``. Normalising
    here means the matcher has one representation to compare against.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


async def handle_get_subscriptions(request: web.Request) -> web.Response:
    """GET /x-nmos/query/v1.3/subscriptions — list subscriptions.

    ``QueryAPI.raml:442`` marks this "for debug use only" and
    ``Behaviour - Querying.md:23`` actively discourages using it to find a
    subscription to reuse. It carries the ``paged`` trait (``:441``), so
    paging parameters are validated even though the collection is normally
    tiny — rejecting a malformed cursor here rather than ignoring it keeps the
    behaviour uniform across every paged resource.
    """
    default_limit, max_limit = _limits(request)
    try:
        parse_paging(
            dict(request.query), default_limit=default_limit, max_limit=max_limit,
        )
    except PagingError as exc:
        return error_response(400, str(exc), request=request)

    subscriptions = _registry(request).subscriptions.all()
    return json_response(
        [s.to_json() for s in subscriptions], request=request,
    )


async def handle_get_subscription(request: web.Request) -> web.Response:
    """GET /x-nmos/query/v1.3/subscriptions/{subscriptionId} — one subscription."""
    subscription_id = request.match_info["subscriptionId"]
    subscription = _registry(request).subscriptions.get(subscription_id)
    if subscription is None:
        return error_response(
            404, f"subscription {subscription_id} was not found", request=request,
        )
    return json_response(subscription.to_json(), request=request)


async def handle_delete_subscription(request: web.Request) -> web.Response:
    """DELETE /x-nmos/query/v1.3/subscriptions/{subscriptionId}.

    ``Behaviour - Querying.md:18`` — "The Query API MUST NOT acknowledge HTTP
    DELETE requests for Subscriptions running in this non-persistent mode,
    instead issuing an HTTP 403 (Forbidden) response." A non-persistent
    subscription belongs to the API, which reaps it when its last WebSocket
    closes; letting a client delete one would let it destroy a subscription
    another client is still using.
    """
    subscription_id = request.match_info["subscriptionId"]
    manager = _registry(request).subscriptions

    subscription = manager.get(subscription_id)
    if subscription is None:
        return error_response(
            404, f"subscription {subscription_id} was not found", request=request,
        )

    if not subscription.persist:
        return error_response(
            403,
            "a non-persistent subscription is managed by the Query API and "
            "cannot be deleted",
            request=request,
        )

    manager.delete(subscription_id)
    log.info("registry: deleted subscription %s", subscription_id)
    return status_response(204)
