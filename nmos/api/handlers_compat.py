# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""IS-11 Stream Compatibility API handlers (v1.0).

GET endpoints for sender/receiver status, constraints, inputs/outputs.
PUT/DELETE for active constraints.

Delegates to Node methods which call compatibility.py (CCF-based constraint algebra).
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from nmos.api.response import json_response, error_response
from nmos.api.middleware import check_exclusive_authorization
from nmos.json.engine import JsonEngine


def _encode_sender_field(sender: object, field_name: str, html_mode: bool = False) -> str:
    """Encode a sender field (Constraints, Caps, etc.) to JSON string.

    Uses the generated type's encode() method via JsonEngine.
    Returns "[]" if the field is undefined.
    """
    field = getattr(sender, field_name, None)
    if field is None:
        return "[]"
    if hasattr(field, 'defined') and not field.defined:
        return "[]"
    engine = JsonEngine()
    engine.generate_html = html_mode
    if html_mode:
        engine.level_indentation = 2
    engine.reset()
    field.encode(engine, None)
    return engine.get_output()


def _decode_active_constraints(body: dict[str, Any]) -> object:
    """Decode a JSON body into NSenderActiveConstraints."""
    from nmos.types.generated.nsender_active_constraints import (
        NSenderActiveConstraintsValue,
    )
    obj = NSenderActiveConstraintsValue()
    obj.decode(JsonEngine(), body)
    return obj


# ---------------------------------------------------------------------------
# Senders
# ---------------------------------------------------------------------------

async def handle_get_senders(request: web.Request) -> web.Response:
    """GET /x-nmos/streamcompatibility/v1.0/senders"""
    node = request.app["node"]
    result = []
    for static_id, sender in node.senders:
        result.append(sender.ResourceCore.Id.value + "/")
    return json_response(result, no_store=True, request=request)


async def handle_get_sender(request: web.Request) -> web.Response:
    """GET /x-nmos/streamcompatibility/v1.0/senders/{senderId}"""
    return json_response([
        "status/", "inputs/", "constraints/",
    ], no_store=True, request=request)


async def handle_get_sender_status(request: web.Request) -> web.Response:
    """GET /x-nmos/streamcompatibility/v1.0/senders/{senderId}/status

    Computes compatibility status by checking flow against sender caps/constraints.
    """
    node = request.app["node"]
    sender_id = request.match_info["senderId"]
    sender = node.senders.get(sender_id)
    if sender is None:
        return error_response(404, f"sender {sender_id} not found", request=request)

    status = node.set_sender_compatibility_state(sender)
    return json_response({"state": status}, no_store=True, request=request)


async def handle_get_sender_inputs(request: web.Request) -> web.Response:
    """GET /x-nmos/streamcompatibility/v1.0/senders/{senderId}/inputs"""
    return json_response([], no_store=True, request=request)


async def handle_get_sender_constraints(request: web.Request) -> web.Response:
    """GET /x-nmos/streamcompatibility/v1.0/senders/{senderId}/constraints"""
    return json_response(["supported/", "active/"], no_store=True, request=request)


async def handle_get_sender_constraints_supported(request: web.Request) -> web.Response:
    """GET /x-nmos/streamcompatibility/v1.0/senders/{senderId}/constraints/supported

    Returns the list of constraint parameter URNs the sender supports.
    This tells controllers which parameters they can use in PUT constraints/active.
    """
    node = request.app["node"]
    sender_id = request.match_info["senderId"]
    sender = node.senders.get(sender_id)
    if sender is None:
        return error_response(404, f"sender {sender_id} not found", request=request)

    # Return ALL supported constraint parameter names (including meta).
    # returns the full list without filtering.
    from nmos.node.compatibility import get_supported_constraints
    format_str = sender.Format.value.s if sender.Format.defined else ""
    supported = get_supported_constraints(format_str)
    param_urns = [str(u) for u in supported]
    return json_response({"parameter_constraints": param_urns}, no_store=True, request=request)


async def handle_get_sender_constraints_active(request: web.Request) -> web.Response:
    """GET /x-nmos/streamcompatibility/v1.0/senders/{senderId}/constraints/active

    Returns the currently active constraints applied to the sender.
    """
    node = request.app["node"]
    sender_id = request.match_info["senderId"]
    sender = node.senders.get(sender_id)
    if sender is None:
        return error_response(404, f"sender {sender_id} not found", request=request)

    # Return active constraints — encode via JsonEngine with HTML support
    from nmos.api.response import json_response_raw, _wants_html
    html = _wants_html(request)
    if hasattr(sender, 'Constraints') and sender.Constraints.defined:
        constraints_json = _encode_sender_field(sender, "Constraints", html_mode=html)
    else:
        constraints_json = '{"constraint_sets":[]}'
    return json_response_raw(constraints_json, no_store=True, request=request)


async def handle_put_sender_constraints_active(request: web.Request) -> web.Response:
    """PUT /x-nmos/streamcompatibility/v1.0/senders/{senderId}/constraints/active

    Apply active constraints to a sender. Validates constraints against
    sender capabilities, forces flow update, and sets compatibility status.

    Returns 200 with the applied constraints, or:
    - 400 on invalid JSON
    - 404 if sender not found
    - 422 if constraints violate capabilities
    """
    node = request.app["node"]
    sender_id = request.match_info["senderId"]

    auth_error = check_exclusive_authorization(request, node)
    if auth_error is not None:
        return auth_error

    sender = node.senders.get(sender_id)
    if sender is None:
        return error_response(404, f"sender {sender_id} not found", request=request)

    # do not allow changing the constraints of an active Sender (423 Locked)
    if (hasattr(sender, 'Subscription') and sender.Subscription.defined
            and hasattr(sender.Subscription.value, 'Active')
            and sender.Subscription.value.Active.defined
            and sender.Subscription.value.Active.value):
        return error_response(423, "cannot set active constraints of an active Sender", request=request)

    try:
        body = JsonEngine.parse_any(await request.text())
    except (ValueError, TypeError):
        return error_response(400, "invalid JSON body", request=request)

    # Decode into generated type
    try:
        constraints_obj = _decode_active_constraints(body)
    except Exception as exc:
        return error_response(400, f"invalid constraint format: {exc}", request=request)

    # Validate against sender capabilities
    _, err = node.validate_active_constraints(sender, constraints_obj)
    if err is not None:
        return error_response(422, str(err), request=request)

    # Apply constraints
    node.force_active_constraints(sender, constraints_obj)

    # bump IS-04 sender version after constraint change
    import time
    t = time.time_ns()
    sender.ResourceCore.Version.value = (t // 1_000_000_000, t % 1_000_000_000)

    # Update compatibility status
    node.set_sender_compatibility_state(sender)

    node.publish()

    # Return applied constraints
    return json_response(body, no_store=True, request=request)


async def handle_delete_sender_constraints_active(request: web.Request) -> web.Response:
    """DELETE /x-nmos/streamcompatibility/v1.0/senders/{senderId}/constraints/active

    Clear active constraints from a sender (reset to unconstrained).
    """
    node = request.app["node"]
    sender_id = request.match_info["senderId"]

    auth_error = check_exclusive_authorization(request, node)
    if auth_error is not None:
        return auth_error

    sender = node.senders.get(sender_id)
    if sender is None:
        return error_response(404, f"sender {sender_id} not found", request=request)

    # do not allow changing the constraints of an active Sender (423 Locked)
    if (hasattr(sender, 'Subscription') and sender.Subscription.defined
            and hasattr(sender.Subscription.value, 'Active')
            and sender.Subscription.value.Active.defined
            and sender.Subscription.value.Active.value):
        return error_response(423, "cannot delete active constraints of an active Sender", request=request)

    # Clear active constraints
    node.force_active_constraints(sender, None)

    # bump IS-04 sender version after constraint change
    import time
    t = time.time_ns()
    sender.ResourceCore.Version.value = (t // 1_000_000_000, t % 1_000_000_000)

    node.set_sender_compatibility_state(sender)

    node.publish()

    return json_response({"constraint_sets": []}, no_store=True, request=request)


# ---------------------------------------------------------------------------
# Receivers
# ---------------------------------------------------------------------------

async def handle_get_receivers(request: web.Request) -> web.Response:
    """GET /x-nmos/streamcompatibility/v1.0/receivers"""
    node = request.app["node"]
    result = []
    from nmos.node import _get_resource_core
    for static_id, recv in node.receivers:
        result.append(_get_resource_core(recv).Id.value + "/")
    return json_response(result, no_store=True, request=request)


async def handle_get_receiver(request: web.Request) -> web.Response:
    """GET /x-nmos/streamcompatibility/v1.0/receivers/{receiverId}"""
    return json_response(["status/", "outputs/"], no_store=True, request=request)


async def handle_get_receiver_status(request: web.Request) -> web.Response:
    """GET /x-nmos/streamcompatibility/v1.0/receivers/{receiverId}/status

    Computes receiver compatibility status by checking stream (SDP) against caps.
    """
    node = request.app["node"]
    receiver_id = request.match_info["receiverId"]
    recv = node.receivers.get(receiver_id)
    if recv is None:
        return error_response(404, f"receiver {receiver_id} not found", request=request)

    status = node.set_receiver_compatibility_state(recv)
    return json_response({"state": status}, no_store=True, request=request)


async def handle_get_receiver_outputs(request: web.Request) -> web.Response:
    """GET /x-nmos/streamcompatibility/v1.0/receivers/{receiverId}/outputs"""
    return json_response([], no_store=True, request=request)


# ---------------------------------------------------------------------------
# Inputs / Outputs (no-op — inputs/outputs not ported)
# ---------------------------------------------------------------------------

async def handle_get_inputs(request: web.Request) -> web.Response:
    """GET /x-nmos/streamcompatibility/v1.0/inputs"""
    return json_response([], no_store=True, request=request)


async def handle_get_outputs(request: web.Request) -> web.Response:
    """GET /x-nmos/streamcompatibility/v1.0/outputs"""
    return json_response([], no_store=True, request=request)
