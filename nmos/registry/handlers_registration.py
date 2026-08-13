# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""IS-04 Registration API v1.3 handlers.

Implements ``RegistrationAPI.raml`` in full: the base route, ``/resource``
POST, ``/resource/{type}/{id}`` DELETE and debug GET, and
``/health/nodes/{id}`` POST and debug GET.

The interesting behaviour is all in the status codes, so they are set out
here rather than left to be inferred from the code:

===========================================  ======  ==============================
Situation                                    Status  Source
===========================================  ======  ==============================
New resource registered                      201     Behaviour - Registration.md:25
Existing resource updated                    200     Behaviour - Registration.md:25
Body fails schema / referential integrity    400     Behaviour - Registration.md:94-104
Resource deleted                             204     RegistrationAPI.raml:91
Resource / Node unknown                      404     RegistrationAPI.raml:93, :140
Heartbeat accepted                           200     RegistrationAPI.raml:136
===========================================  ======  ==============================

Both the 200 and 201 responses carry a ``Location`` header
(``RegistrationAPI.raml:45-56``), and both return the registered resource as
their body (``registrationapi-resource-response.json``).

On 409
------
``RegistrationAPI.raml`` defines a 409 on POST, DELETE, GET and heartbeat for
"the resource already exists in the registry at a different API version".
This registry serves v1.3 and only v1.3, so no resource can be held at another
version and the condition is unreachable. It is called out at each site rather
than silently omitted, so that adding v1.0-v1.2 later is a matter of storing a
per-resource API version and filling in the guard, not of rediscovering that
the case exists.

On authorization
----------------
None of these routes is wrapped in ``check_oauth2``. That is required, not an
omission: ``NMOS With Control Plane Security.md:105`` states the Registration
API "MUST not require the NMOS Nodes to use OAuth 2.0 authorizations". Access
control on this interface is TLS only — server-authenticated or mutual — and
mutual authentication is enforced for every state-changing verb by the shared
``client_auth_middleware``.
"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from nmos.api.response import error_response, json_response, status_response
from nmos.json.engine import JsonEngine
from nmos.registry import handlers_query as query_h
from nmos.registry.decode import DecodeFailure, decode_post_envelope
from nmos.registry.links import make_link_resolver
from nmos.registry.backend import BackendState, MutationUnavailable, RegistryBackend
from nmos.registry.registry import Registry
from nmos.registry.types import RegistrationError, ResourceType

log = logging.getLogger(__name__)

API_VERSION = "v1.3"
BASE_PATH = f"/x-nmos/registration/{API_VERSION}"

# ``Behaviour - Registration.md:94-104`` calls every one of these a client
# error the Node "MUST NOT" retry without corrective action, so they all map
# to 400. They are distinguished in the ``debug`` member, which :106 points at
# as the operator's debugging aid.
_ERROR_STATUS: dict[RegistrationError, int] = {
    RegistrationError.SCHEMA: 400,
    RegistrationError.ID_TYPE_CONFLICT: 400,
    RegistrationError.VERSION_REGRESSION: 400,
    RegistrationError.PARENT_CHANGED: 400,
    RegistrationError.PARENT_MISSING: 400,
}


def _registry(request: web.Request) -> Registry:
    """The Registry for this app.

    Stored under ``"registry"``; ``"node"`` holds the InterfaceSecurity that
    the shared middleware reads. See ``nmos/registry/registry.py``.
    """
    registry: Registry = request.app["registry"]
    return registry


def _backend(request: web.Request) -> RegistryBackend:
    """The storage backend behind the Registration API.

    Every mutation goes through this rather than through ``Registry`` directly,
    because in distributed mode a registration is an etcd transaction and a
    fence before it is a store mutation. In standalone mode the backend calls
    straight through to ``Registry`` without awaiting anything, so this
    indirection costs nothing there.
    """
    backend: RegistryBackend = request.app["backend"]
    return backend


def _unavailable(request: web.Request, state: BackendState) -> web.Response:
    """The 503 answered when the backend cannot accept mutations.

    ``Retry-After: 1`` because the conditions that produce it -- a lost etcd
    quorum, a resync after compaction, a member still preloading -- resolve on
    the order of seconds, and a Node that backs off for minutes would stay
    unregistered long after the registry recovered.

    Query is deliberately *not* gated on this: a registry serving a cached view
    during an etcd outage is still useful, and refusing reads because writes are
    impossible turns a partial outage into a total one.
    """
    response = error_response(
        503,
        f"registry storage is {state.value}; registration is temporarily "
        f"unavailable",
        request=request,
    )
    response.headers["Retry-After"] = "1"
    return response


def _resource_location(resource_type: ResourceType, resource_id: str) -> str:
    """The ``Location`` header value for a registered resource.

    ``RegistrationAPI.raml:47`` gives the form
    ``/x-nmos/registration/{version}/resource/nodes/{id}`` — a path, not an
    absolute URL, so it stays correct behind a reverse proxy.
    """
    return f"{BASE_PATH}/resource/{resource_type.plural}/{resource_id}"


# ---------------------------------------------------------------------------
# POST /resource
# ---------------------------------------------------------------------------

async def handle_post_resource(request: web.Request) -> web.Response:
    """POST /x-nmos/registration/v1.3/resource — create or update a resource."""
    registry = _registry(request)

    try:
        body = JsonEngine.parse_any(await request.text())
    except (ValueError, TypeError) as exc:
        return error_response(400, f"invalid JSON body: {exc}", request=request)

    try:
        resource_type, raw, typed = decode_post_envelope(body)
    except DecodeFailure as exc:
        # :100 -- "The request body does not meet the JSON schema for that
        # resource type". Decoding into the generated type IS that check.
        return error_response(400, str(exc), request=request)

    backend = _backend(request)
    if not backend.state.accepts_mutations:
        return _unavailable(request, backend.state)

    try:
        result = await backend.register(resource_type, raw, typed)
    except MutationUnavailable as exc:
        # The cluster could not commit within the deadline, or lost quorum
        # mid-request. Not a client error: the body was fine and the Node
        # should retry.
        log.warning("registry: registration unavailable: %s", exc)
        return _unavailable(request, backend.state)

    if not result.ok:
        assert result.error is not None
        status = _ERROR_STATUS[result.error]
        log.info(
            "registry: rejected %s registration (%s): %s",
            resource_type.value, result.error.value, result.detail,
        )
        return error_response(status, result.detail, request=request)

    # nmos-cpp logs the registry status from its POST handler as well as from
    # its expiry thread; matching that makes the two logs directly comparable
    # when diagnosing a registration problem.
    #
    # Guarded, and that guard is load-bearing rather than stylistic.
    # ``status_line()`` calls ``statistics()``, which walks every resource in
    # every bucket -- so it is O(registry size). As a bare argument to
    # ``log.info`` it is evaluated *eagerly*, on every successful POST, even
    # with INFO disabled: a registry holding 10 000 resources paid a 10 000-item
    # scan per registration to build a string it then threw away. Registration
    # latency is the metric this project is most trying to protect, and Node
    # startup issues these POSTs serially, so the cost lands squarely on the
    # critical path.
    if log.isEnabledFor(logging.INFO):
        log.info("registry: %s", registry.status_line())

    # :25 -- 201 for a create, 200 for an update, Location on both. The body
    # is the registered resource (registrationapi-resource-response.json), and
    # it is the stored raw form so a client sees exactly what was registered.
    response = json_response(
        raw,
        status=201 if result.created else 200,
        request=request,
    )
    # Set after construction because the shared json_response() takes no
    # headers argument. Extending that signature for one caller would change
    # a helper the whole Node API depends on.
    response.headers["Location"] = _resource_location(
        resource_type, raw["id"],
    )
    return response


# ---------------------------------------------------------------------------
# /resource/{resourceType}/{resourceId}
# ---------------------------------------------------------------------------

def _parse_resource_path(request: web.Request) -> ResourceType | web.Response:
    """Resolve the ``{resourceType}`` path segment, or produce the error.

    ``RegistrationAPI.raml:75-82`` fixes the permitted values as an explicit
    enum of the six plural names. Matching them exactly is what keeps a bad
    path segment from being coerced onto a real type — the AMWA mock derives
    the singular with ``rstrip("s")``, which strips every trailing ``s``.
    """
    name = request.match_info["resourceType"]
    resource_type = ResourceType.from_plural(name)
    if resource_type is None:
        permitted = ", ".join(rt.plural for rt in ResourceType)
        return error_response(
            404,
            f"unknown resource type {name!r}; expected one of: {permitted}",
            request=request,
        )
    return resource_type


async def handle_delete_resource(request: web.Request) -> web.Response:
    """DELETE /resource/{resourceType}/{resourceId} — unregister a resource.

    Removes the resource and, cascading, every descendant
    (``Behaviour - Registration.md:68``, ``:74``).
    """
    resolved = _parse_resource_path(request)
    if isinstance(resolved, web.Response):
        return resolved

    resource_id = request.match_info["resourceId"]
    backend = _backend(request)
    if not backend.state.accepts_mutations:
        return _unavailable(request, backend.state)

    # A 409 would belong here if the resource were held at another API
    # version; unreachable in a single-version registry. See module docstring.
    try:
        deleted = await backend.unregister(resolved, resource_id)
    except MutationUnavailable as exc:
        log.warning("registry: delete unavailable: %s", exc)
        return _unavailable(request, backend.state)

    if not deleted:
        return error_response(
            404,
            f"{resolved.value} {resource_id} is not registered",
            request=request,
        )

    log.info("registry: unregistered %s %s", resolved.value, resource_id)
    # RegistrationAPI.raml:91-92 -- 204 No Content, no body.
    return status_response(204)


async def handle_get_resource(request: web.Request) -> web.Response:
    """GET /resource/{resourceType}/{resourceId} — read back a registration.

    ``RegistrationAPI.raml:105`` marks this "for debug use only": the
    Registration API is otherwise write-only and the Query API is the read
    interface. It is implemented because the RAML defines it, and it is
    genuinely useful when checking what a Node actually sent.
    """
    resolved = _parse_resource_path(request)
    if isinstance(resolved, web.Response):
        return resolved

    resource_id = request.match_info["resourceId"]
    resource = _registry(request).store.get(resolved, resource_id)
    if resource is None:
        return error_response(
            404,
            f"{resolved.value} {resource_id} is not registered",
            request=request,
        )
    # Cross-references resolve into the Query API: the Registration API has
    # no collections to browse (it is write-only apart from these debug
    # reads), so linking within it would only produce dead ends.
    return json_response(
        resource.raw,
        request=request,
        link_resolver=make_link_resolver(
            str(request.path), query_h.BASE_PATH,
        ),
    )


# ---------------------------------------------------------------------------
# /health/nodes/{nodeId}
# ---------------------------------------------------------------------------

def _health_body(health: int) -> dict[str, Any]:
    """The heartbeat response body.

    ``registrationapi-health-response.json`` types ``health`` as
    ``{"type": "string", "pattern": "^[0-9]+$"}`` — a STRING holding the TAI
    seconds. nmos-cpp agrees (``make_health_response_body`` emits
    ``json::value::string``); the AMWA mock returns a JSON number, which does
    not satisfy its own specification's schema.
    """
    return {"health": str(health)}


async def handle_post_health(request: web.Request) -> web.Response:
    """POST /health/nodes/{nodeId} — heartbeat.

    ``Behaviour - Registration.md:45`` — Nodes heartbeat every 5 s by
    default. The heartbeat refreshes the Node and, recursively, all of its
    sub-resources, so that the whole subtree survives as one unit.
    """
    node_id = request.match_info["nodeId"]
    backend = _backend(request)
    if not backend.state.accepts_mutations:
        return _unavailable(request, backend.state)

    try:
        health = await backend.heartbeat(node_id)
    except MutationUnavailable as exc:
        log.warning("registry: heartbeat unavailable: %s", exc)
        return _unavailable(request, backend.state)

    if health is None:
        # :112-114 -- 404 means "not known to the Registration API", most
        # likely because garbage collection removed it. The Node's documented
        # response is to re-register every resource in order.
        return error_response(
            404,
            f"node {node_id} is not registered",
            request=request,
        )

    return json_response(_health_body(health), request=request)


async def handle_get_health(request: web.Request) -> web.Response:
    """GET /health/nodes/{nodeId} — read health without heartbeating.

    ``RegistrationAPI.raml:152`` — "for debug use only". Deliberately does
    NOT refresh health: a diagnostic read that silently kept a Node alive
    would mask exactly the garbage-collection problem someone would be using
    it to investigate.
    """
    node_id = request.match_info["nodeId"]
    health = _registry(request).store.node_health(node_id)
    if health is None:
        return error_response(
            404,
            f"node {node_id} is not registered",
            request=request,
        )
    return json_response(_health_body(health), request=request)
