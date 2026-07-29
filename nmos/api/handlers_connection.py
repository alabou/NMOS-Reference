# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""IS-05 Connection API handlers (v1.0/v1.1).

GET endpoints for staged/active transport params, constraints, transport files.
PATCH endpoints for staged params (triggers activation pipeline).

v1.1 is redirected to 1.0 as there is no functional change between the two versions.

"""

from __future__ import annotations

import io
import logging
from typing import Any, cast

from aiohttp import web

from nmos.api.response import json_response, json_response_raw, error_response, sdp_response, _wants_html


# Transport type mapping: variant → base (activation type switch
# for senders, and similar for receivers).
# IS-05 /transporttype returns the base type for schema validation.
# Variants are collapsed to their base (e.g., rtp.mcast → rtp) to match the
# transport params type used at activation time.
_TRANSPORT_BASE: dict[str, str] = {
    # Standard NMOS transports
    "urn:x-nmos:transport:rtp": "urn:x-nmos:transport:rtp",
    "urn:x-nmos:transport:rtp.ucast": "urn:x-nmos:transport:rtp",
    "urn:x-nmos:transport:rtp.mcast": "urn:x-nmos:transport:rtp",
    "urn:x-nmos:transport:mqtt": "urn:x-nmos:transport:mqtt",
    "urn:x-nmos:transport:websocket": "urn:x-nmos:transport:websocket",
    # Matrox transport extensions
    "urn:x-matrox:transport:rtp.tcp": "urn:x-matrox:transport:rtp.tcp",
    "urn:x-matrox:transport:ndi": "urn:x-matrox:transport:ndi",
    "urn:x-matrox:transport:srt": "urn:x-matrox:transport:srt",
    "urn:x-matrox:transport:srt.mp2t": "urn:x-matrox:transport:srt",
    "urn:x-matrox:transport:srt.rtp": "urn:x-matrox:transport:srt",
    "urn:x-matrox:transport:usb": "urn:x-matrox:transport:usb",
    "urn:x-matrox:transport:tcp": "urn:x-matrox:transport:tcp",
    "urn:x-matrox:transport:udp": "urn:x-matrox:transport:udp",
    "urn:x-matrox:transport:udp.ucast": "urn:x-matrox:transport:udp",
    "urn:x-matrox:transport:udp.mcast": "urn:x-matrox:transport:udp",
    "urn:x-matrox:transport:udp.mp2t": "urn:x-matrox:transport:udp",
    "urn:x-matrox:transport:udp.mp2t.ucast": "urn:x-matrox:transport:udp",
    "urn:x-matrox:transport:udp.mp2t.mcast": "urn:x-matrox:transport:udp",
    "urn:x-matrox:transport:rtsp": "urn:x-matrox:transport:rtsp",
    "urn:x-matrox:transport:rtsp.tcp": "urn:x-matrox:transport:rtsp",
}


def _base_transport(transport: str) -> str:
    """Map a transport URN variant to its base type for IS-05 /transporttype."""
    base = _TRANSPORT_BASE.get(transport)
    if base is not None:
        return base
    # Fallback: strip everything after the first '.' in the transport-specific part
    # e.g., "urn:x-nmos:transport:rtp.mcast" → "urn:x-nmos:transport:rtp"
    for prefix in ("urn:x-nmos:transport:", "urn:x-matrox:transport:"):
        if transport.startswith(prefix):
            specific = transport[len(prefix):]
            base_specific = specific.split(".")[0]
            return prefix + base_specific
    return transport
from nmos.api.middleware import check_exclusive_authorization
from nmos.json.engine import JsonEngine
from nmos.node.activation_engine import (
    ActivationResponse,
    check_constraint,
    update_staged_params,
    process_activation,
    reset_staged_activation,
)
from nmos.node.activation import get_transport_descriptor
from nmos.node.types import ActivationMode, format_duration, format_tai


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _encode_generated(value: Any, html_mode: bool = False) -> str:
    """Encode a generated NMOS value using JsonEngine."""
    engine = JsonEngine()
    engine.generate_html = html_mode
    if html_mode:
        engine.level_indentation = 2
    return engine.encode(value)


def _make_activation_view(
    activation: Any, use_active: bool = False, node: Any = None,
) -> Any | None:
    """Build generated activation view with typed TransportParams attached.

    When use_active=True, resolves "auto" values to concrete values
    (active params never contain "auto").
    """
    state = activation.active_state if use_active else activation.staged_state
    if state is None:
        return None

    params = activation.active if use_active else activation.staged
    view = state.clone() if hasattr(state, "clone") else state

    if hasattr(view, "TransportParams"):
        # Filter to enabled legs only (filtered by InterfaceBindings)
        enabled = activation.enabled_legs if hasattr(activation, 'enabled_legs') else len(params)
        filtered = params[:enabled] if enabled > 0 else params
        cloned_params = [p.clone() if hasattr(p, "clone") else p for p in filtered]
        # Active params are already resolved during flip_activation.
        # No additional resolution needed in the API handler.
        view.TransportParams.value = cloned_params

    # Ensure all required IS-05 response properties are defined.
    # These must be explicitly marked as defined.
    # Without this, the generated encoder skips undefined fields → schema violation.

    # Activation object — completes the active/staged response
    if hasattr(view, "Activation"):
        if not view.Activation.defined:
            view.Activation.set_to_default()
        av = view.Activation.value

        if use_active and hasattr(activation, 'mode'):
            # Active endpoint: report the activation that produced the current
            # active state — immediate or scheduled. A scheduled activation that
            # has already fired is still described here (its mode and target
            # time), which is how a controller learns that the state it is
            # looking at came from the activation it scheduled earlier.
            if not _fill_activation_fields(av, activation):
                # Never activated: null/null/null.
                if hasattr(av, "Mode") and not av.Mode.defined:
                    av.Mode.value = None
                if hasattr(av, "RequestedTime") and not av.RequestedTime.defined:
                    av.RequestedTime.value = None
                if hasattr(av, "ActivationTime") and not av.ActivationTime.defined:
                    av.ActivationTime.value = None
        else:
            # Staged endpoint: default to null
            if hasattr(av, "Mode") and not av.Mode.defined:
                av.Mode.value = None
            if hasattr(av, "RequestedTime") and not av.RequestedTime.defined:
                av.RequestedTime.value = None
            if hasattr(av, "ActivationTime") and not av.ActivationTime.defined:
                av.ActivationTime.value = None

    # Receiver: sender_id, master_enable, transport_file
    if hasattr(view, "SenderId") and not view.SenderId.defined:
        view.SenderId.value = None
    if hasattr(view, "MasterEnable") and not view.MasterEnable.defined:
        view.MasterEnable.value = False
    if hasattr(view, "TransportFile"):
        if not view.TransportFile.defined:
            view.TransportFile.set_to_default()
        tf = view.TransportFile.value
        if hasattr(tf, "Type") and not tf.Type.defined:
            tf.Type.value = None
        if hasattr(tf, "Data") and not tf.Data.defined:
            tf.Data.value = None

    # Sender: receiver_id, master_enable
    if hasattr(view, "ReceiverId") and not view.ReceiverId.defined:
        view.ReceiverId.value = None

    return view


def _resolve_auto_params(params: list[Any], node: Any) -> None:
    """Resolve "auto" values in active transport params to concrete values.

    IS-05 requires that /active never contains "auto" — all auto values
    must be resolved to actual IP addresses, ports, etc.
    Implements active parameter initialization in InitRtpReceiver/InitRtpSender.

    NOTE: Active params are initialized with resolved values at
    creation time (not resolved on-the-fly during GET). This function
    catches any remaining "auto" values that weren't resolved during init.
    """
    if node is None:
        return

    # Get the node's interface IP
    interface_ip = "127.0.0.1"
    try:
        if node.node_value is not None:
            ep = node.node_value.Api.value.Endpoints
            if ep.defined and len(ep.value) > 0:
                interface_ip = ep.value[0].Host.value
    except AttributeError:
        pass

    for p in params:
        # String fields: resolve "auto" to concrete values
        for attr_name, resolved in (
            ("InterfaceIp", interface_ip),
            ("SourceIp", interface_ip),
            ("DestinationIp", "0.0.0.0"),
            ("RtcpDestinationIp", "0.0.0.0"),
        ):
            field = getattr(p, attr_name, None)
            if field is not None and hasattr(field, 'defined') and field.defined:
                if field.value == "auto":
                    cast(Any, field).value = resolved

        # Nullable fields (NNull): resolve "auto" to valid port numbers
        # destination_port resolves to source_port value (both set per sender index).
        # Read source_port first to use as destination_port default.
        src_port = 27500
        rtcp_src_port = 27501
        src_field = getattr(p, "SourcePort", None)
        if src_field is not None and hasattr(src_field, 'defined') and src_field.defined:
            try:
                v = src_field.value
                if v is not None and v != "auto":
                    src_port = int(v)
                    rtcp_src_port = src_port + 1
            except (ValueError, TypeError):
                pass

        port_fields: tuple[tuple[str, int], ...] = (
            ("DestinationPort", src_port),
            ("RtcpDestinationPort", rtcp_src_port),
            ("RtcpSourcePort", rtcp_src_port),
            ("SourcePort", src_port),
        )
        for attr_name, resolved_port in port_fields:
            field = getattr(p, attr_name, None)
            if field is not None and hasattr(field, 'defined') and field.defined:
                if field.value == "auto":
                    cast(Any, field).value = resolved_port


_ACTIVATION_MODE_NAMES: dict[Any, str] = {
    ActivationMode.IMMEDIATE: "activate_immediate",
    ActivationMode.RELATIVE: "activate_scheduled_relative",
    ActivationMode.ABSOLUTE: "activate_scheduled_absolute",
}


def _fill_activation_fields(av: Any, activation: Any) -> bool:
    """Describe ``activation`` in the response's activation object.

    Returns False (leaving ``av`` untouched) when there is nothing to describe,
    i.e. no activation has been requested.

    The three IS-05 fields carry different things per mode:

    * ``mode`` — which of the three activation modes was used.
    * ``activation_time`` — when the activation happens or happened, in TAI.
      Always a point in time.
    * ``requested_time`` — what the client actually asked for, echoed back.
      For an absolute activation that is a TAI instant; for a relative one it is
      the *offset* the client gave, so the TAI epoch offset must not be applied
      to it. Immediate activations requested no time at all, hence null.
    """
    mode = getattr(activation, 'mode', None)
    mode_name = _ACTIVATION_MODE_NAMES.get(mode)
    tai_str = getattr(activation, 'activation_time_tai', "")
    if mode_name is None or not tai_str:
        return False

    if hasattr(av, "Mode"):
        av.Mode.value = mode_name
    if hasattr(av, "ActivationTime"):
        av.ActivationTime.value = tai_str

    if hasattr(av, "RequestedTime"):
        if mode in (ActivationMode.RELATIVE, ActivationMode.ABSOLUTE):
            # Echoed back exactly as received. A client may compare this against
            # what it sent, and reconstructing it from the parsed value loses
            # nanoseconds — a float cannot represent them at present-day epoch
            # values, and datetime resolves only to microseconds. Fall back to a
            # reconstruction only if the raw string was somehow not captured.
            raw = getattr(activation, 'requested_time_string', "")
            if raw:
                av.RequestedTime.value = raw
            elif mode == ActivationMode.RELATIVE:
                delta = getattr(activation, 'requested_delta_time', None)
                av.RequestedTime.value = (
                    format_duration(delta.total_seconds()) if delta is not None else None
                )
            else:
                requested = getattr(activation, 'requested_time', None)
                av.RequestedTime.value = (
                    format_tai(requested.timestamp()) if requested is not None else None
                )
        else:
            av.RequestedTime.value = None
    return True


def _set_activation_on_response(view: Any, activation: Any, response: Any) -> None:
    """Set activation fields on the PATCH response view.

    Three outcomes, and they differ in what the client is told:

    * **Activated now** (immediate, or a scheduled time already in the past) —
      the response describes the completed activation, and the staged activation
      is then cleared so later GETs of /staged report nothing pending. The one
      exception is a *scheduled* mode that turned out to be past-due: IS-05
      reports that as a plain completed activation with no timing echoed back,
      so all three fields are null.
    * **Scheduled for later** — the response describes the pending activation
      (mode, when it will happen, what was requested). The staged activation is
      deliberately NOT cleared: it is what makes /staged report the activation
      as pending and what holds the 423 against a second scheduled PATCH.
    * **Nothing requested** — untouched, so the view's defaults (null) stand.
    """
    if view is None or not hasattr(view, "Activation"):
        return

    act_wrapper = view.Activation
    if not act_wrapper.defined:
        act_wrapper.set_to_default()
    av = act_wrapper.value

    if response is None:
        return

    if response.delayed_activation:
        _fill_activation_fields(av, activation)
        return

    if not response.immediate_activation:
        return

    mode = getattr(activation, 'mode', None)
    if mode == ActivationMode.IMMEDIATE:
        _fill_activation_fields(av, activation)
    else:
        # Past-due scheduled activation: activated immediately, reported with no
        # timing information rather than as though it had been scheduled.
        for field_name in ("Mode", "ActivationTime", "RequestedTime"):
            if hasattr(av, field_name):
                getattr(av, field_name).value = None

    _reset_staged_activation(activation)


def _reset_staged_activation(activation: Any) -> None:
    """Clear the staged activation once it has been carried out.

    Delegates to the activation engine, which owns this rule: the same clearing
    is needed when a scheduled activation fires with no HTTP request in sight.
    """
    reset_staged_activation(activation)


def _encode_activation_state_raw(
    activation: Any, html_mode: bool = False, use_active: bool = False, node: Any = None,
) -> str:
    """Encode staged/active activation state as JSON string."""
    view = _make_activation_view(activation, use_active=use_active, node=node)
    if view is None:
        # Defensive fallback
        fallback = {
            "activation": {"mode": None, "requested_time": None, "activation_time": None},
            "transport_params": [],
        }
        return JsonEngine.dump_any(fallback, indent=2)
    return _encode_generated(view, html_mode=html_mode)


def _encode_constraints_raw(activation: Any, html_mode: bool = False) -> str:
    """Encode transport constraints array as JSON string.

    Filters to enabled legs only (filtered by InterfaceBindings).
    """
    enabled = activation.enabled_legs if hasattr(activation, 'enabled_legs') else len(activation.constraints)
    constraints = activation.constraints[:enabled] if enabled > 0 else activation.constraints
    items: list[str] = []
    for constraint in constraints:
        items.append(_encode_generated(constraint, html_mode=html_mode))
    if not items:
        return "[]"
    joiner = ",\n" if not html_mode else ","
    return "[" + joiner.join(items) + "]"


def _activation_mode_of(state: Any) -> Any:
    """The activation mode currently recorded in a staged/active state, or None."""
    if state is None or not hasattr(state, "Activation"):
        return None
    act = state.Activation
    if not act.defined:
        return None
    av = act.value
    if not hasattr(av, "Mode") or not av.Mode.defined:
        return None
    return av.Mode.value


def _pending_activation_conflict(activation: Any, patch_state: Any) -> bool:
    """True if this PATCH would displace an activation already scheduled.

    IS-05 answers 423 Locked when an activation is pending and the client tries
    to set another one, rather than silently replacing it — the first request
    was accepted, so the second has to be refused.

    The test is on the staged *mode*, not on the activation's internal state:
    that is what makes a null mode always pass, which is how a client withdraws
    a scheduled activation, and what makes the lock lift by itself once the
    activation has fired and cleared its staged mode.
    """
    if _activation_mode_of(activation.staged_state) is None:
        return False
    return _activation_mode_of(patch_state) is not None


def _decode_patch_state(template_state: Any, body: Any) -> Any:
    """Decode PATCH body into the generated activation type."""
    patch_state = type(template_state)()
    patch_state.decode(JsonEngine(), body)
    return patch_state


def _apply_defined_field(src_field: Any, dst_field: Any) -> None:
    """Copy a defined field value from src to dst.

    Handles NNullString and other types where .value property may raise
    NotAvailable if not yet defined — we use the setter directly.
    """
    if src_field is None or dst_field is None:
        return
    if not hasattr(src_field, "defined") or not src_field.defined:
        return
    # Use set_value() for wrapper types (NTransportFile etc.), value setter for simple types.
    # This must NOT be wrapped in try/except — silent failure here caused the
    # TransportFile-not-propagating-to-active bug.
    if hasattr(dst_field, 'set_value') and hasattr(src_field, 'value'):
        dst_field.set_value(src_field.value)
    else:
        dst_field.value = src_field.value


def _apply_patch_state_to_activation(activation: Any, patch_state: Any) -> None:
    """Apply generated activation patch onto runtime activation objects."""
    # Top-level activation metadata
    if hasattr(patch_state, "MasterEnable") and hasattr(activation.staged_state, "MasterEnable"):
        _apply_defined_field(patch_state.MasterEnable, activation.staged_state.MasterEnable)

    if hasattr(patch_state, "ReceiverId") and hasattr(activation.staged_state, "ReceiverId"):
        _apply_defined_field(patch_state.ReceiverId, activation.staged_state.ReceiverId)

    if hasattr(patch_state, "SenderId") and hasattr(activation.staged_state, "SenderId"):
        _apply_defined_field(patch_state.SenderId, activation.staged_state.SenderId)

    if hasattr(patch_state, "Activation") and hasattr(activation.staged_state, "Activation"):
        if patch_state.Activation.defined:
            if not activation.staged_state.Activation.defined:
                activation.staged_state.Activation.set_to_default()
            update_staged_params(
                activation.staged_state.Activation.value,
                patch_state.Activation.value,
            )

    if hasattr(patch_state, "TransportFile") and hasattr(activation.staged_state, "TransportFile"):
        if patch_state.TransportFile.defined:
            if not activation.staged_state.TransportFile.defined:
                activation.staged_state.TransportFile.set_to_default()
            update_staged_params(
                activation.staged_state.TransportFile.value,
                patch_state.TransportFile.value,
            )

    # Transport params: merge each leg using generated transport params types.
    if hasattr(patch_state, "TransportParams") and patch_state.TransportParams.defined:
        for i, patch_leg in enumerate(patch_state.TransportParams.value):
            if i < len(activation.staged):
                update_staged_params(activation.staged[i], patch_leg)


# ---------------------------------------------------------------------------
# Sender endpoints
# ---------------------------------------------------------------------------

async def handle_get_single_senders(request: web.Request) -> web.Response:
    """GET /x-nmos/connection/v1.0/single/senders — List sender IDs."""
    node = request.app["node"]
    result = []
    for static_id, sender in node.senders:
        result.append(sender.ResourceCore.Id.value + "/")
    return json_response(result, no_store=True, request=request)


async def handle_get_single_sender(request: web.Request) -> web.Response:
    """GET /x-nmos/connection/v1.0/single/senders/{senderId} — Sub-resources."""
    node = request.app["node"]
    sender_id = request.match_info["senderId"]
    if node.senders.get(sender_id) is None:
        return error_response(404, f"sender {sender_id} not found", request=request)

    return json_response([
        "staged/", "active/", "constraints/", "transportfile/", "transporttype/",
    ], no_store=True, request=request)


async def handle_get_sender_staged(request: web.Request) -> web.Response:
    """GET /x-nmos/connection/v1.0/single/senders/{senderId}/staged"""
    node = request.app["node"]
    sender_id = request.match_info["senderId"]
    activation = node.sender_activation.get(sender_id)
    if activation is None:
        return error_response(404, f"sender {sender_id} not found", request=request)

    body = _encode_activation_state_raw(activation, html_mode=_wants_html(request), use_active=False)
    return json_response_raw(body, no_store=True, request=request)


async def handle_patch_sender_staged(request: web.Request) -> web.Response:
    """PATCH /x-nmos/connection/v1.0/single/senders/{senderId}/staged

    Triggers activation pipeline:
    1. Check exclusive session authorization
    2. Decode incoming transport params
    3. Reject if an activation is already scheduled (423)
    4. Update staged params from patch
    5. Process activation (immediate/scheduled/cancel)
    6. Return updated staged state

    Everything after ``await request.text()`` below must stay free of ``await``.
    A scheduled activation's timer mutates the very same activation object, and
    the only thing keeping the two from interleaving is the absence of a
    suspension point in this region. See the invariant documented next to
    ``Node.dg_pending_activation``.
    """
    node = request.app["node"]
    sender_id = request.match_info["senderId"]

    # Authorization
    auth_error = check_exclusive_authorization(request, node)
    if auth_error is not None:
        return auth_error

    sender = node.senders.get(sender_id)
    if sender is None:
        return error_response(404, f"sender {sender_id} not found", request=request)

    # Do not allow PATCH if sender has active constraint violation
    if hasattr(sender, 'CompatibilityStatus') and sender.CompatibilityStatus.defined:
        from nmos.enums import EnumRegistry
        if sender.CompatibilityStatus.value == EnumRegistry.get("active_constraints_violation"):
            return error_response(500, "constraint violation", request=request)

    activation = node.sender_activation.get(sender_id)
    if activation is None:
        return error_response(404, f"sender activation {sender_id} not found", request=request)

    try:
        body = JsonEngine.parse_any(await request.text())
    except (ValueError, TypeError):
        return error_response(400, "invalid JSON body", request=request)

    # Decode PATCH into generated type, validate against constraints, THEN merge.
    # Order: check → update → process
    try:
        patch_state = _decode_patch_state(activation.staged_state, body)
    except Exception as exc:
        return error_response(400, f"invalid staged patch: {exc}", request=request)

    # An activation is already scheduled and this PATCH wants to set another →
    # 423 Locked. Checked before anything is applied, so a refused PATCH leaves
    # the staged state exactly as the accepted activation left it.
    if _pending_activation_conflict(activation, patch_state):
        return error_response(423, "pending activation", request=request)

    # Validate BEFORE applying (checkRtpSenderActivation)
    try:
        from nmos.node.activation_engine import validate_transport_params_against_constraints
        validate_transport_params_against_constraints(activation, patch_state)
    except Exception as exc:
        return error_response(400, f"constraint violation: {exc}", request=request)

    # Apply patch to staged (updateRtpSenderActivation)
    try:
        _apply_patch_state_to_activation(activation, patch_state)
    except Exception as exc:
        return error_response(400, f"invalid staged patch: {exc}", request=request)

    # Process activation (immediate/scheduled/cancel)
    try:
        # Get transport descriptor for auto-resolvers
        transport_enum = sender.Transport.value if sender.Transport.defined else None

        auto_resolvers = None
        if transport_enum is not None:
            try:
                desc = get_transport_descriptor(transport_enum)
                auto_resolvers = desc.sender_auto_resolvers
            except KeyError:
                pass

        response = process_activation(
            node, sender_id, activation,
            is_sender=True,
            has_sdp=True,
            auto_resolvers=auto_resolvers,
        )
    except Exception as exc:
        import traceback; traceback.print_exc()
        return error_response(400, f"activation failed: {exc}", request=request)

    # synchronization_status reflects the EFFECTIVE clock, not stream activity
    # (emit_starting no longer asserts CLOCK_OK). A sender is clock-locked only
    # if its source's clock_name is a locked PTP clock (clk0); on an internal
    # clock (clk1) no clock event is emitted and sync stays NotUsed (grey).
    from nmos.node.events import emit_clock_locked
    from nmos.node.status_monitor import NC_HEALTHY
    if node._sender_sync_seed(sender_id) == NC_HEALTHY:
        emit_clock_locked(node.event_queue, sender_id, "", is_sender=True)

    # Build response — construct view, set activation time, then reset staged state
    status = 202 if response.delayed_activation else 200
    view = _make_activation_view(activation, use_active=False)
    _set_activation_on_response(view, activation, response)
    data = _encode_generated(view, html_mode=_wants_html(request)) if view else "{}"

    # Publish changes
    node.publish()

    return json_response_raw(data, status=status, no_store=True, request=request)


async def handle_get_sender_active(request: web.Request) -> web.Response:
    """GET /x-nmos/connection/v1.0/single/senders/{senderId}/active"""
    node = request.app["node"]
    sender_id = request.match_info["senderId"]
    activation = node.sender_activation.get(sender_id)
    if activation is None:
        return error_response(404, f"sender {sender_id} not found", request=request)

    body = _encode_activation_state_raw(activation, html_mode=_wants_html(request), use_active=True, node=node)
    return json_response_raw(body, no_store=True, request=request)


async def handle_get_sender_constraints(request: web.Request) -> web.Response:
    """GET /x-nmos/connection/v1.0/single/senders/{senderId}/constraints"""
    node = request.app["node"]
    sender_id = request.match_info["senderId"]
    activation = node.sender_activation.get(sender_id)
    if activation is None:
        return error_response(404, f"sender {sender_id} not found", request=request)

    body = _encode_constraints_raw(activation, html_mode=_wants_html(request))
    return json_response_raw(body, no_store=True, request=request)


async def handle_get_sender_transportfile(request: web.Request) -> web.Response:
    """GET /x-nmos/connection/v1.0/single/senders/{senderId}/transportfile

    Implements doGetNmosConnectionV11SingleSenderTransportFile.
    When Accept: application/json is requested, returns SDP as a JSON string.
    Otherwise returns raw SDP with Content-Type: application/sdp.
    """
    node = request.app["node"]
    sender_id = request.match_info["senderId"]
    sdp_obj = node.sdp.get(sender_id)

    if sdp_obj is None:
        return error_response(404, f"no transport file for sender {sender_id}", request=request)

    # Get SDP as text
    if isinstance(sdp_obj, str):
        sdp_text = sdp_obj
    else:
        try:
            from sdp.MatroxSdpWrite import encode as sdp_encode
            sdp_text = sdp_encode(sdp_obj)
        except ImportError:
            return error_response(500, "SDP encoder not available", request=request)

    # Check Accept header — return JSON string if application/json requested
    # (used by enableReceiver.sh)
    accept = request.headers.get("Accept", "")
    if "application/json" in accept:
        import json as _json
        # Return the SDP as a JSON-encoded string (with quotes and escapes)
        return json_response_raw(_json.dumps(sdp_text), no_store=True, request=request)

    return sdp_response(sdp_text)


async def handle_get_sender_transporttype(request: web.Request) -> web.Response:
    """GET /x-nmos/connection/v1.0/single/senders/{senderId}/transporttype

    Returns the BASE transport type (no variant suffix).
    e.g., "urn:x-nmos:transport:rtp" not "urn:x-nmos:transport:rtp.mcast"
    """
    node = request.app["node"]
    sender_id = request.match_info["senderId"]
    sender = node.senders.get(sender_id)
    if sender is None:
        return error_response(404, f"sender {sender_id} not found", request=request)

    transport = _base_transport(str(sender.Transport.value)) if sender.Transport.defined else ""
    return json_response(transport, no_store=True, request=request)


# ---------------------------------------------------------------------------
# Receiver endpoints
# ---------------------------------------------------------------------------

async def handle_get_single_receivers(request: web.Request) -> web.Response:
    """GET /x-nmos/connection/v1.0/single/receivers — List receiver IDs."""
    node = request.app["node"]
    result = []
    from nmos.node import _get_resource_core
    for static_id, recv in node.receivers:
        result.append(_get_resource_core(recv).Id.value + "/")
    return json_response(result, no_store=True, request=request)


async def handle_get_single_receiver(request: web.Request) -> web.Response:
    """GET /x-nmos/connection/v1.0/single/receivers/{receiverId}"""
    node = request.app["node"]
    receiver_id = request.match_info["receiverId"]
    if node.receivers.get(receiver_id) is None:
        return error_response(404, f"receiver {receiver_id} not found", request=request)

    return json_response([
        "staged/", "active/", "constraints/", "transporttype/",
    ], no_store=True, request=request)


async def handle_get_receiver_staged(request: web.Request) -> web.Response:
    """GET /x-nmos/connection/v1.0/single/receivers/{receiverId}/staged"""
    node = request.app["node"]
    receiver_id = request.match_info["receiverId"]
    activation = node.receiver_activation.get(receiver_id)
    if activation is None:
        return error_response(404, f"receiver {receiver_id} not found", request=request)

    body = _encode_activation_state_raw(activation, html_mode=_wants_html(request), use_active=False)
    return json_response_raw(body, no_store=True, request=request)


async def handle_patch_receiver_staged(request: web.Request) -> web.Response:
    """PATCH /x-nmos/connection/v1.0/single/receivers/{receiverId}/staged

    Everything after ``await request.text()`` below must stay free of ``await``.
    A scheduled activation's timer mutates the very same activation object, and
    the only thing keeping the two from interleaving is the absence of a
    suspension point in this region. See the invariant documented next to
    ``Node.dg_pending_activation``.
    """
    node = request.app["node"]
    receiver_id = request.match_info["receiverId"]

    auth_error = check_exclusive_authorization(request, node)
    if auth_error is not None:
        return auth_error

    receiver = node.receivers.get(receiver_id)
    if receiver is None:
        return error_response(404, f"receiver {receiver_id} not found", request=request)

    activation = node.receiver_activation.get(receiver_id)
    if activation is None:
        return error_response(404, f"receiver activation {receiver_id} not found", request=request)

    try:
        body = JsonEngine.parse_any(await request.text())
    except (ValueError, TypeError):
        return error_response(400, "invalid JSON body", request=request)

    # Order: SDP enrich PATCH → check → update → process
    # (processRtpReceiverSdpTransportFile → checkRtpReceiverActivation → updateRtpReceiverActivation)
    try:
        patch_state = _decode_patch_state(activation.staged_state, body)
    except Exception as exc:
        return error_response(400, f"invalid staged patch: {exc}", request=request)

    # An activation is already scheduled and this PATCH wants to set another →
    # 423 Locked. Checked before anything is applied, so a refused PATCH leaves
    # the staged state exactly as the accepted activation left it.
    if _pending_activation_conflict(activation, patch_state):
        return error_response(423, "pending activation", request=request)

    # Step 1: Enrich PATCH with SDP values (processRtpReceiverSdpTransportFile)
    # Fields NOT defined in the PATCH are filled from the SDP transport file.
    transport_file_data = None
    if hasattr(patch_state, "TransportFile") and patch_state.TransportFile.defined:
        tf_value = patch_state.TransportFile.value
        if hasattr(tf_value, "Data") and tf_value.Data.defined:
            transport_file_data = tf_value.Data.value
    if transport_file_data is not None:
        from nmos.node.sdp_transport import process_receiver_sdp_transport_file
        # Receiver transport drives the transport-aware SDP→params mapping —
        # connection-oriented receivers (USB/RTSP/RTP-TCP/NDI) take the
        # sender's endpoint from the SDP into SourceIp/SourcePort.
        receiver_transport = ""
        _recv = node.receivers.get(receiver_id)
        if _recv is not None:
            _inner = _recv.get() if hasattr(_recv, "get") else _recv
            _rv = _inner.value if hasattr(_inner, "value") else _inner
            _core = getattr(_rv, "ReceiverCore", _rv)
            if _core.Transport.defined:
                receiver_transport = str(_core.Transport.value)
        sdp_params = process_receiver_sdp_transport_file(
            activation, transport_file_data, transport_str=receiver_transport,
        )
        # Apply SDP-extracted params to PATCH transport params.
        # If PATCH has no TransportParams, create empty ones per leg count.
        # Then set undefined fields from SDP.
        if sdp_params:
            if not (hasattr(patch_state, 'TransportParams') and patch_state.TransportParams.defined):
                # Create empty transport params for the fallback behavior
                n_legs = activation.enabled_legs or 1
                empty_legs = [type(activation.staged[0])() for _ in range(n_legs)]
                patch_state.TransportParams._defined = True
                patch_state.TransportParams._value._inner = empty_legs

            for i, patch_leg in enumerate(patch_state.TransportParams.value):
                for field_name, value in sdp_params.items():
                    field = getattr(patch_leg, field_name, None)
                    if field is not None and hasattr(field, 'defined') and not field.defined:
                        field.value = value

    # Step 2: Validate enriched PATCH (checkRtpReceiverActivation)
    try:
        from nmos.node.activation_engine import validate_transport_params_against_constraints
        validate_transport_params_against_constraints(activation, patch_state)
    except Exception as exc:
        return error_response(400, f"constraint violation: {exc}", request=request)

    # Step 3: Apply patch to staged (updateRtpReceiverActivation)
    try:
        _apply_patch_state_to_activation(activation, patch_state)
    except Exception as exc:
        return error_response(400, f"invalid staged patch: {exc}", request=request)

    # Resolve transport-specific auto-resolvers for flip (flipRtp*ReceiverActivation)
    auto_resolvers = None
    receiver = node.receivers.get(receiver_id)
    if receiver is not None:
        inner = receiver.get() if hasattr(receiver, 'get') else receiver
        rv = inner.value if hasattr(inner, 'value') else inner
        core = getattr(rv, 'ReceiverCore', rv)
        transport_enum = core.Transport.value if core.Transport.defined else None
        if transport_enum is not None:
            try:
                desc = get_transport_descriptor(transport_enum)
                auto_resolvers = desc.receiver_auto_resolvers
            except KeyError:
                pass

    # Process activation
    try:
        response = process_activation(
            node, receiver_id, activation,
            is_sender=False, has_sdp=False,
            auto_resolvers=auto_resolvers,
        )
    except Exception as exc:
        return error_response(400, f"activation failed: {exc}", request=request)

    # A receiver has no clock of its own — it locks to the connected stream's
    # clock, signalled by the SDP ``ts-refclk``. synchronization_status reflects
    # that EFFECTIVE clock (not stream activity): emit CLOCK_OK → Healthy
    # (green) only when the negotiated SDP names a PTP reference; an internal
    # clock (localmac) or no stream emits nothing → sync stays NotUsed (grey).
    # Driven here, where the receiver's SDP is parsed, so it tracks effective
    # state rather than the node's advertised clock list.
    from nmos.node.sdp_transport import sdp_ref_clock_is_ptp
    if transport_file_data is not None and sdp_ref_clock_is_ptp(transport_file_data):
        from nmos.node.events import emit_clock_locked
        emit_clock_locked(node.event_queue, receiver_id, "", is_sender=False)

    status = 202 if response.delayed_activation else 200

    # Build response — construct view, set activation time, then reset staged state
    view = _make_activation_view(activation, use_active=False)
    _set_activation_on_response(view, activation, response)
    data = _encode_generated(view, html_mode=_wants_html(request)) if view else "{}"

    node.publish()

    return json_response_raw(data, status=status, no_store=True, request=request)


async def handle_get_receiver_active(request: web.Request) -> web.Response:
    """GET /x-nmos/connection/v1.0/single/receivers/{receiverId}/active"""
    node = request.app["node"]
    receiver_id = request.match_info["receiverId"]
    activation = node.receiver_activation.get(receiver_id)
    if activation is None:
        return error_response(404, f"receiver {receiver_id} not found", request=request)

    body = _encode_activation_state_raw(activation, html_mode=_wants_html(request), use_active=True, node=node)
    return json_response_raw(body, no_store=True, request=request)


async def handle_get_receiver_constraints(request: web.Request) -> web.Response:
    """GET /x-nmos/connection/v1.0/single/receivers/{receiverId}/constraints"""
    node = request.app["node"]
    receiver_id = request.match_info["receiverId"]
    activation = node.receiver_activation.get(receiver_id)
    if activation is None:
        return error_response(404, f"receiver {receiver_id} not found", request=request)

    body = _encode_constraints_raw(activation, html_mode=_wants_html(request))
    return json_response_raw(body, no_store=True, request=request)


async def handle_get_receiver_transporttype(request: web.Request) -> web.Response:
    """GET /x-nmos/connection/v1.0/single/receivers/{receiverId}/transporttype

    Returns the BASE transport type (no variant suffix).
    """
    node = request.app["node"]
    receiver_id = request.match_info["receiverId"]
    recv = node.receivers.get(receiver_id)
    if recv is None:
        return error_response(404, f"receiver {receiver_id} not found", request=request)

    from nmos.node import _get_receiver_core
    receiver_core = _get_receiver_core(recv)
    transport = _base_transport(str(receiver_core.Transport.value)) if receiver_core.Transport.defined else ""
    return json_response(transport, no_store=True, request=request)


# ---------------------------------------------------------------------------
# Bulk endpoints (IS-05 spec requires these — POST for bulk, GET returns 405)
# ---------------------------------------------------------------------------

async def handle_post_bulk_senders(request: web.Request) -> web.Response:
    """POST /x-nmos/connection/v1.{0,1}/bulk/senders"""
    # TODO: implement bulk sender staging
    return error_response(501, "bulk sender staging not implemented", request=request)


async def handle_post_bulk_receivers(request: web.Request) -> web.Response:
    """POST /x-nmos/connection/v1.{0,1}/bulk/receivers"""
    # TODO: implement bulk receiver staging
    return error_response(501, "bulk receiver staging not implemented", request=request)


async def handle_get_bulk_not_allowed(request: web.Request) -> web.Response:
    """GET on /bulk/senders or /bulk/receivers returns 405 per IS-05."""
    return error_response(405, "Method Not Allowed", request=request)
