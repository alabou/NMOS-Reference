# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Generic activation pipeline — 5-step process for all transports.

Uses 5 generic functions parameterized by TransportDescriptor instead of
90 per-transport functions (5 steps × 9 transports × 2 sender/receiver).

Pipeline steps:
    1. check()   — validate transport params against constraints
    2. update()  — patch staged state with incoming params
    3. flip()    — copy staged → active, resolve "auto" values
    4. do()      — orchestrate: backup → flip → SDP → subscription → engine
    5. process() — handle activation mode (immediate/scheduled/cancel)

Atomicity guarantee: active parameters and associated sender/receiver are NOT modified
until activation is proven successful. On failure, all state is rolled back.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from nmos.errors import InvalidData, InvalidOperation, UnexpectedError
from nmos.node.types import (
    Activation,
    ActivationMode,
    ActivationState,
    EngineState,
    Leg,
)


# ---------------------------------------------------------------------------
# Activation response
# ---------------------------------------------------------------------------

@dataclass
class ActivationResponse:
    """Result of process_activation()."""
    immediate_activation: bool = False
    delayed_activation: bool = False
    activation_time: float = 0.0  # POSIX timestamp


# ---------------------------------------------------------------------------
# 1. Constraint validation
# ---------------------------------------------------------------------------

def check_constraint(constraint: Any, value: Any) -> None:
    """Validate a transport parameter value against its constraint.

    Constraint types supported:
    - enum: list of allowed values (exact match)
    - minimum/maximum: range check for int/float
    - pattern: regex match for strings

    "auto" string values skip all validation.
    Empty/undefined constraints are unconstrained (any value accepted).

    Raises InvalidData on constraint violation.
    """
    if not hasattr(constraint, 'defined') or not constraint.defined:
        return  # no constraint defined

    cv = constraint.value
    if cv is None:
        return

    # Check if constraint is empty (unconstrained)
    has_min = hasattr(cv, 'Minimum') and cv.Minimum.defined
    has_max = hasattr(cv, 'Maximum') and cv.Maximum.defined
    has_enum = hasattr(cv, 'Enum') and cv.Enum.defined
    has_pattern = hasattr(cv, 'Pattern') and cv.Pattern.defined

    if not has_min and not has_max and not has_enum and not has_pattern:
        return  # unconstrained

    # Extract the actual value from the NType wrapper
    actual = _extract_value(value)

    # "auto" values skip validation
    if isinstance(actual, str) and actual == "auto":
        return

    # None (null) value
    if actual is None:
        if has_enum:
            enum_values = _get_enum_list(cv)
            if None not in enum_values:
                raise InvalidData("constraint mismatch: null not in enum")
        return

    # Bool value
    if isinstance(actual, bool):
        if has_enum:
            enum_values = _get_enum_list(cv)
            if actual not in enum_values:
                raise InvalidData(f"constraint mismatch: {actual} not in enum")
        return

    # Int value
    if isinstance(actual, int) and not isinstance(actual, bool):
        if has_min and actual < int(cv.Minimum.value):
            raise InvalidData(f"constraint mismatch: {actual} < minimum {cv.Minimum.value}")
        if has_max and actual > int(cv.Maximum.value):
            raise InvalidData(f"constraint mismatch: {actual} > maximum {cv.Maximum.value}")
        if has_enum:
            enum_values = _get_enum_list(cv)
            if actual not in enum_values:
                raise InvalidData(f"constraint mismatch: {actual} not in enum")
        return

    # Float value
    if isinstance(actual, float):
        if has_min and actual < float(cv.Minimum.value):
            raise InvalidData(f"constraint mismatch: {actual} < minimum")
        if has_max and actual > float(cv.Maximum.value):
            raise InvalidData(f"constraint mismatch: {actual} > maximum")
        if has_enum:
            enum_values = _get_enum_list(cv)
            if actual not in enum_values:
                raise InvalidData(f"constraint mismatch: {actual} not in enum")
        return

    # String value
    if isinstance(actual, str):
        if has_pattern:
            pattern = cv.Pattern.value
            if not re.match(pattern, actual):
                raise InvalidData(f"constraint mismatch: '{actual}' doesn't match pattern")
        if has_enum:
            enum_values = _get_enum_list(cv)
            if actual not in enum_values:
                raise InvalidData(f"constraint mismatch: '{actual}' not in enum")
        return


def _extract_value(wrapped: Any) -> Any:
    """Extract the raw value from an NType wrapper (NString, NInt, NBool, etc.)."""
    if wrapped is None:
        return None
    if hasattr(wrapped, 'defined'):
        if not wrapped.defined:
            return None
        if hasattr(wrapped, 'value'):
            return wrapped.value
    return wrapped


def _get_enum_list(cv: Any) -> list[Any]:
    """Extract the enum list from a constraint value."""
    if hasattr(cv, 'Enum') and cv.Enum.defined:
        raw = cv.Enum.value
        if isinstance(raw, list):
            return raw
        if hasattr(raw, '_inner') and isinstance(raw._inner, list):
            return raw._inner
    return []


# ---------------------------------------------------------------------------
# 2. Update staged state
# ---------------------------------------------------------------------------

def update_staged_params(staged: Any, patch: Any) -> None:
    """Patch staged transport parameters from incoming values.

    For each defined field in patch, copy its value to the corresponding
    field in staged. Undefined fields in patch are left unchanged in staged.
    """
    if patch is None:
        return

    for field_name in getattr(patch, '__slots__', []):
        patch_field = getattr(patch, field_name, None)
        if patch_field is None:
            continue
        if hasattr(patch_field, 'defined') and not patch_field.defined:
            continue

        staged_field = getattr(staged, field_name, None)
        if staged_field is None:
            continue

        # Copy the value
        if hasattr(patch_field, 'value') and hasattr(staged_field, 'value'):
            staged_field.value = patch_field.value


# ---------------------------------------------------------------------------
# 3. Flip staged → active
# ---------------------------------------------------------------------------

def flip_activation(
    activation: Activation,
    legs: list[Leg],
    auto_resolvers: dict[str, Any] | None = None,
) -> None:
    """Promote staged parameters to active, resolving "auto" values.

    Behaviour:
    1. For each field: if staged value is NOT "auto" for static fields (SourceIp,
       SourcePort, RtcpSourcePort), copy to active. For dynamic fields, always copy.
    2. After copying, resolve remaining "auto" values in active using senderIndex.

    Static fields ("auto maps to static value"):
    - SourceIp, SourcePort, RtcpSourcePort, InterfaceIp
    These keep their active value when staged is "auto".

    Dynamic fields (always copied, then resolved):
    - DestinationIp, DestinationPort, RtcpDestinationIp, RtcpDestinationPort
    - All other fields
    """
    # Static fields that keep their active value when staged is "auto"
    _STATIC_FIELDS = {"SourceIp", "SourcePort", "RtcpSourcePort", "InterfaceIp"}

    sender_index = getattr(activation, 'sender_index', 0)
    receiver_index = getattr(activation, 'receiver_index', 0)

    for index, leg in enumerate(legs):
        if index >= len(activation.staged) or index >= len(activation.active):
            break

        staged = activation.staged[index]
        active = activation.active[index]

        # Step 1: Copy staged→active, respecting static fields
        for field_name in getattr(staged, '__slots__', []):
            src_field = getattr(staged, field_name, None)
            dst_field = getattr(active, field_name, None)
            if src_field is None or dst_field is None:
                continue
            if not hasattr(src_field, 'defined') or not src_field.defined:
                continue

            # For static fields: skip if staged value is "auto" (keep active's init value)
            if field_name in _STATIC_FIELDS:
                if src_field.value == "auto":
                    continue  # Keep active's static value

            # Copy staged → active
            dst_field.value = src_field.value

        if not leg.enable:
            continue

        # Step 2: Resolve "auto" values via transport-specific resolver.
        #
        # Each transport registers its own resolver implementing its
        # auto-resolution logic.  This replaces the previous one-size-fits-all
        # sender logic that broke receiver activation
        # (rtcp_destination_ip = 0.0.0.0).
        if auto_resolvers is not None:
            resolver = auto_resolvers.get("flip_resolve")
            if resolver is not None:
                resolver(active, sender_index, receiver_index, leg)


def _get_unused_multicast_address_ipv4(sender_index: int, leg: Any) -> str:
    """Generate a unique IPv4 multicast address for a sender.

    Format: 239.<senderIndex+1>.<mgmtAddr[2]>.<mgmtAddr[3]>
    Uses the last two octets of the leg's IPv4 address.
    """
    # Get leg's IPv4 address octets
    mgmt_octets = [0, 0, 0, 0]
    try:
        addr = str(leg.ipv4.address) if leg.ipv4 and leg.ipv4.address else "0.0.0.0"
        parts = addr.split(".")
        if len(parts) == 4:
            mgmt_octets = [int(p) for p in parts]
    except (ValueError, AttributeError):
        pass

    if sender_index > 127:
        sender_index = 127  # IPMX limit

    return f"239.{sender_index + 1}.{mgmt_octets[2]}.{mgmt_octets[3]}"


def _resolve_auto_field(params: Any, field_name: str, resolved: Any) -> None:
    """Resolve a string field's "auto" value."""
    field = getattr(params, field_name, None)
    if field is not None and hasattr(field, 'defined') and field.defined:
        if field.value == "auto":
            field.value = resolved


def _resolve_auto_null_field(params: Any, field_name: str, resolved: Any) -> None:
    """Resolve a nullable field's "auto" value."""
    field = getattr(params, field_name, None)
    if field is not None and hasattr(field, 'defined') and field.defined:
        if field.value == "auto":
            field.value = resolved


# ---------------------------------------------------------------------------
# Transport-specific auto-resolvers for flip Step 2
#
# Each function implements the "auto" resolution logic for its transport.
# Imported by activation.py and wired into
# TransportDescriptor.sender_auto_resolvers / receiver_auto_resolvers
# with key "flip_resolve".
# ---------------------------------------------------------------------------

def resolve_rtp_sender(active: Any, sender_index: int, receiver_index: int, leg: Any) -> None:
    """RTP sender auto-resolution.

    DestinationIp="auto"      → unique multicast address from leg IPv4
    DestinationPort="auto"    → 27500 + 2*senderIndex
    RtcpDestinationIp="auto"  → copy from resolved DestinationIp
    RtcpDestinationPort="auto"→ resolved DestinationPort + 1
    """
    # DestinationIp: "auto" → multicast address
    mcast = _get_unused_multicast_address_ipv4(sender_index, leg)
    _resolve_auto_field(active, "DestinationIp", mcast)

    # DestinationPort: "auto" → 27500 + 2*senderIndex
    _resolve_auto_null_field(active, "DestinationPort", 27500 + 2 * sender_index)

    # RtcpDestinationIp: "auto" → copy from resolved DestinationIp
    _resolve_auto_field(active, "RtcpDestinationIp", active.DestinationIp.value)

    # RtcpDestinationPort: "auto" → DestinationPort + 1
    dp = active.DestinationPort.value
    if dp is not None and dp != "auto":
        _resolve_auto_null_field(active, "RtcpDestinationPort", int(dp) + 1)
    else:
        _resolve_auto_null_field(active, "RtcpDestinationPort", 27501 + 2 * sender_index)


def resolve_rtp_receiver(active: Any, sender_index: int, receiver_index: int, leg: Any) -> None:
    """RTP receiver auto-resolution.

    DestinationPort="auto"     → 27500 (fixed, no index)
    RtcpDestinationIp="auto"   → InterfaceIp, override with MulticastIp if present
    RtcpDestinationPort="auto" → DestinationPort + 1
    """
    # DestinationPort: "auto" → 27500 (fixed)
    _resolve_auto_null_field(active, "DestinationPort", 27500)

    # RtcpDestinationIp: "auto" → InterfaceIp, then override with MulticastIp
    rtcp_field = getattr(active, "RtcpDestinationIp", None)
    if rtcp_field is not None and hasattr(rtcp_field, 'defined') and rtcp_field.defined:
        if rtcp_field.value == "auto":
            # Start with InterfaceIp
            rtcp_ip = "0.0.0.0"
            iface = getattr(active, "InterfaceIp", None)
            if iface is not None and hasattr(iface, 'defined') and iface.defined:
                rtcp_ip = iface.value
            # Override with MulticastIp if present
            mcast = getattr(active, "MulticastIp", None)
            if mcast is not None and hasattr(mcast, 'defined') and mcast.defined:
                v = mcast.value
                if v is not None:
                    rtcp_ip = v
            rtcp_field.value = rtcp_ip

    # RtcpDestinationPort: "auto" → DestinationPort + 1
    rtcp_port_field = getattr(active, "RtcpDestinationPort", None)
    if rtcp_port_field is not None and hasattr(rtcp_port_field, 'defined') and rtcp_port_field.defined:
        if rtcp_port_field.value == "auto":
            dp_field = getattr(active, "DestinationPort", None)
            if dp_field is not None and hasattr(dp_field, 'defined') and dp_field.defined:
                dp = dp_field.value
                if dp is not None and dp != "auto":
                    rtcp_port_field.value = int(dp) + 1
                else:
                    rtcp_port_field.value = 27501
            else:
                rtcp_port_field.value = 27501


def resolve_udp_sender(active: Any, sender_index: int, receiver_index: int, leg: Any) -> None:
    """UDP sender auto-resolution.

    DestinationIp="auto"   → unique multicast address
    DestinationPort="auto" → 27500 + senderIndex (NOT 2×, unlike RTP)
    """
    mcast = _get_unused_multicast_address_ipv4(sender_index, leg)
    _resolve_auto_field(active, "DestinationIp", mcast)
    # UDP port formula: 27500 + senderIndex (not 2*senderIndex)
    _resolve_auto_null_field(active, "DestinationPort", 27500 + sender_index)


def resolve_udp_receiver(active: Any, sender_index: int, receiver_index: int, leg: Any) -> None:
    """UDP receiver auto-resolution.

    DestinationPort="auto" → 27500 (fixed)
    """
    _resolve_auto_null_field(active, "DestinationPort", 27500)


def resolve_srt_sender(active: Any, sender_index: int, receiver_index: int, leg: Any) -> None:
    """SRT sender auto-resolution.

    Protocol-conditional:
    - Listener: DestinationIp→None, DestinationPort→None
    - RendezVous: DestinationPort→copy from SourcePort
    """
    from nmos.enums import Listener, RendezVous
    listener = Listener
    rendezvous = RendezVous

    protocol = None
    proto_field = getattr(active, "Protocol", None)
    if proto_field is not None and hasattr(proto_field, 'defined') and proto_field.defined:
        protocol = proto_field.value

    # DestinationIp: "auto" → None if Listener
    dst_ip_field = getattr(active, "DestinationIp", None)
    if dst_ip_field is not None and hasattr(dst_ip_field, 'defined') and dst_ip_field.defined:
        if dst_ip_field.value == "auto":
            if protocol is listener:
                dst_ip_field.value = None

    # DestinationPort: "auto" → None if Listener, SourcePort if RendezVous
    dst_port_field = getattr(active, "DestinationPort", None)
    if dst_port_field is not None and hasattr(dst_port_field, 'defined') and dst_port_field.defined:
        if dst_port_field.value == "auto":
            if protocol is listener:
                dst_port_field.value = None
            elif protocol is rendezvous:
                src_port = getattr(active, "SourcePort", None)
                if src_port is not None and hasattr(src_port, 'defined') and src_port.defined:
                    dst_port_field.value = src_port.value


def resolve_srt_receiver(active: Any, sender_index: int, receiver_index: int, leg: Any) -> None:
    """SRT receiver auto-resolution.

    Protocol-conditional:
    - Listener: SourceIp→None, SourcePort→None
    - RendezVous: SourcePort→copy from DestinationPort
    """
    from nmos.enums import Listener, RendezVous
    listener = Listener
    rendezvous = RendezVous

    protocol = None
    proto_field = getattr(active, "Protocol", None)
    if proto_field is not None and hasattr(proto_field, 'defined') and proto_field.defined:
        protocol = proto_field.value

    # SourceIp: "auto" → None if Listener
    src_ip_field = getattr(active, "SourceIp", None)
    if src_ip_field is not None and hasattr(src_ip_field, 'defined') and src_ip_field.defined:
        if src_ip_field.value == "auto":
            if protocol is listener:
                src_ip_field.value = None

    # SourcePort: "auto" → None if Listener, DestinationPort if RendezVous
    src_port_field = getattr(active, "SourcePort", None)
    if src_port_field is not None and hasattr(src_port_field, 'defined') and src_port_field.defined:
        if src_port_field.value == "auto":
            if protocol is listener:
                src_port_field.value = None
            elif protocol is rendezvous:
                dst_port = getattr(active, "DestinationPort", None)
                if dst_port is not None and hasattr(dst_port, 'defined') and dst_port.defined:
                    src_port_field.value = dst_port.value


def resolve_noop(active: Any, sender_index: int, receiver_index: int, leg: Any) -> None:
    """No auto-resolution.  Used by RTSP, USB, NDI, MQTT, WebSocket, RTP-TCP.

    These transports' flip functions do not resolve any "auto" values.
    """
    pass


# ---------------------------------------------------------------------------
# 4. Do activation (orchestrate with atomic rollback)
# ---------------------------------------------------------------------------

def do_activation(
    node: Any,  # Node (avoiding circular import)
    resource_id: str,
    activation: Activation,
    master_enable: bool,
    is_sender: bool,
    has_sdp: bool = True,
    auto_resolvers: dict[str, Any] | None = None,
) -> None:
    """Orchestrate activation with atomic rollback on failure.

    Steps:
    1. Clear activation timing in staged state
    2. Backup current active state
    3. Flip staged → active (resolving "auto" values)
    4. Generate SDP (if applicable and master_enable)
    5. Update subscription
    6. Manage streaming engine lifecycle

    On failure at any step, all previous state is restored.
    """
    from nmos.node.store import to_static_id

    static_id = to_static_id(resource_id)

    # Backup active state for rollback
    active_state_backup = activation.active_state
    sdp_backup = node.sdp.get(static_id) if hasattr(node, 'sdp') else None

    # Step 1: Flip staged → active (transport params)
    try:
        flip_activation(activation, node.legs, auto_resolvers)
    except Exception as exc:
        raise UnexpectedError(
            f"cannot flip staged to active: {exc}"
        ) from exc

    # Step 1b: Copy activation metadata from staged to active state
    # (MasterEnable, SenderId/ReceiverId — done in the activation handlers)
    staged = activation.staged_state
    active = activation.active_state
    if staged is not None and active is not None:
        for field_name in ("MasterEnable", "SenderId", "ReceiverId", "TransportFile"):
            src = getattr(staged, field_name, None)
            dst = getattr(active, field_name, None)
            if src is None or dst is None:
                continue
            if not hasattr(src, 'defined') or not src.defined:
                continue
            # For wrapper types (NTransportFile, etc.) use set_value which clones.
            # For simple types (NNullString, NBool) use value setter.
            # This must NOT be wrapped in try/except — silent failure here caused the
            # TransportFile-not-propagating-to-active bug.
            if hasattr(dst, 'set_value') and hasattr(src, 'value'):
                dst.set_value(src.value)
            else:
                dst.value = src.value

    # Step 2: If flip succeeded but subsequent steps fail, roll back
    try:
        # Step 3: SDP regeneration from active params (sender only)
        if is_sender and has_sdp and master_enable:
            from nmos.node import _generate_sdp_from_params
            sender = node.senders.get(static_id)
            if sender is not None:
                sdp_text = _generate_sdp_from_params(node, sender, resource_id, activation)
                if sdp_text is not None:
                    node._store_parsed_sdp(static_id, sdp_text)

        elif is_sender and has_sdp and not master_enable:
            # Deactivating: clear SDP
            node.sdp.remove(static_id)

        # Step 4: Update IS-04 subscription (updateReceiverSubscription/updateSenderSubscription)
        if is_sender:
            from nmos.node.updates import SenderUpdate
            # Read receiver_id from staged_state if present
            receiver_id = None
            if hasattr(activation.staged_state, 'ReceiverId') and activation.staged_state.ReceiverId.defined:
                receiver_id = activation.staged_state.ReceiverId.value
            node.update_sender(resource_id, SenderUpdate(
                subscription_active=master_enable,
                subscription_receiver_id=receiver_id,
            ))
        else:
            from nmos.node.updates import ReceiverUpdate
            # Read sender_id from staged_state if present
            sender_id_val = None
            if hasattr(activation.staged_state, 'SenderId') and activation.staged_state.SenderId.defined:
                sender_id_val = activation.staged_state.SenderId.value
            node.update_receiver(resource_id, ReceiverUpdate(
                subscription_active=master_enable,
                subscription_sender_id=sender_id_val,
            ))

        # Step 4b: Sync privacy params from active transport params → activation.privacy
        # After flip, active[0] has the final values (from PATCH + SDP enrichment).
        # The streaming engine reads from activation.privacy, so copy them over.
        if master_enable and activation.active:
            _sync_privacy_from_active_params(activation)

        # Step 4c: Node Reservation key_xcl (Matrox "NMOS With Node Reservation" §Acquire)
        # When an exclusive session is active, key_xcl from the session feeds into the
        # PEP KDF as additional keying material. Without a session, key_xcl is empty.
        exclusive_session = getattr(node, 'exclusive_session', None)
        if exclusive_session is not None and hasattr(exclusive_session, 'get_key'):
            xcl_key = exclusive_session.get_key()
            if master_enable and xcl_key:
                activation.privacy.xcl = xcl_key
            else:
                # No session or deactivation → clear key_xcl (spec: "MUST set ... to empty")
                activation.privacy.xcl = b""
        elif master_enable:
            # No exclusive session mechanism on this node → no key_xcl
            activation.privacy.xcl = b""

        # Step 5: Engine lifecycle — start/stop streaming
        # Get transport string from the sender/receiver resource.
        # Sender: Transport on NSenderValue directly.
        # Receiver: Transport on inner.ReceiverCore (polymorphic wrapper).
        transport_str = ""
        resource = node.senders.get(static_id) if is_sender else node.receivers.get(static_id)
        if resource is not None:
            if hasattr(resource, 'Transport') and resource.Transport.defined:
                transport_str = str(resource.Transport.value)
            else:
                inner = resource.get() if hasattr(resource, 'get') else resource
                rv = inner.value if hasattr(inner, 'value') else inner
                core = getattr(rv, 'ReceiverCore', rv)
                if hasattr(core, 'Transport') and core.Transport.defined:
                    transport_str = str(core.Transport.value)

        _manage_engine_lifecycle(
            activation, master_enable,
            node=node, resource_id=resource_id, is_sender=is_sender,
            transport_str=transport_str,
        )

        # Step 6: Regenerate ECDH key on deactivation
        # When master_enable=false, regenerate the ECDH key pair so the staged
        # endpoint shows a fresh key for the next activation.
        if not master_enable and node.privacy_enabled:
            from nmos.node.privacy import (
                generate_ecdh_sender_key,
                generate_ecdh_receiver_key,
            )
            from nmos.node.types import hex_from_bytes
            # UpdateRtpSenderEcdhPrivacy / UpdateRtpReceiverEcdhPrivacy:
            # 1. Read curve from active params
            # 2. Generate new key
            # 3. Update staged + active + constraints for all enabled legs

            if is_sender:
                # Read curve from active, then regenerate
                generate_ecdh_sender_key(activation.privacy, update=False)
                pub_hex = hex_from_bytes(activation.privacy.ecdh_sender_public_key) if activation.privacy.ecdh_sender_public_key else "00"
                field_name = "ExtPrivacyEcdhSenderPublicKey"
            else:
                generate_ecdh_receiver_key(activation.privacy, update=False)
                pub_hex = hex_from_bytes(activation.privacy.ecdh_receiver_public_key) if activation.privacy.ecdh_receiver_public_key else "00"
                field_name = "ExtPrivacyEcdhReceiverPublicKey"

            # Update staged, active, AND constraint
            for idx in range(len(activation.staged)):
                if hasattr(activation.staged[idx], field_name):
                    getattr(activation.staged[idx], field_name).value = pub_hex
                if hasattr(activation.active[idx], field_name):
                    getattr(activation.active[idx], field_name).value = pub_hex
                # Directly replace constraint entry (constraintSet[enums.ExtPrivacy...] = ...)
                if hasattr(activation.constraints[idx], 'Constraints'):
                    from nmos.node.activation import _constraint_enum_key
                    key = _constraint_enum_key(field_name, activation.staged[idx])
                    if key is not None:
                        cs_dict = activation.constraints[idx].Constraints._inner
                        from nmos.types.generated.ntransport_constraint import (
                            NTransportConstraint, NTransportConstraintValue,
                        )
                        cv = NTransportConstraintValue()
                        cv.set_to_default()
                        cv.Enum._defined = True
                        cv.Enum._inner = [pub_hex]
                        cv.Description.value = "read-only"
                        tc = NTransportConstraint()
                        tc._defined = True
                        tc._value = cv
                        cs_dict[key] = tc

        # Step 7: Post-activation compatibility recheck
        # After activation, update IS-11 compatibility status so it reflects
        # the current state (e.g., SDP may have changed for receivers).
        try:
            if is_sender:
                sender = node.senders.get(static_id)
                if sender is not None:
                    node.set_sender_compatibility_state(sender)
            else:
                receiver = node.receivers.get(static_id)
                if receiver is not None:
                    node.set_receiver_compatibility_state(receiver)
        except Exception as exc:
            import logging
            logging.warning(f"Compatibility check failed during activation: {exc}")

    except Exception:
        # Rollback: restore active state
        activation.active_state = active_state_backup
        # Rollback: restore SDP
        if sdp_backup is not None:
            node.sdp.put(static_id, sdp_backup)
        elif has_sdp and is_sender:
            node.sdp.remove(static_id)
        raise


# ---------------------------------------------------------------------------
# 5. Process activation (handle timing modes)
# ---------------------------------------------------------------------------

def process_activation(
    node: Any,
    resource_id: str,
    activation: Activation,
    is_sender: bool,
    has_sdp: bool = True,
    auto_resolvers: dict[str, Any] | None = None,
) -> ActivationResponse:
    """Handle activation modes: immediate, scheduled, cancel.

    Modes:
    - mode=null: cancel pending activation
    - mode=activate_immediate: call do_activation() synchronously
    - mode=activate_scheduled_relative: compute delay, dispatch timer
    - mode=activate_scheduled_absolute: compute TAI→UTC time, dispatch timer
    """
    now = time.time()
    response = ActivationResponse()

    staged_state = activation.staged_state
    if staged_state is None:
        return response

    # Extract activation mode from staged state
    mode_value = _get_activation_mode(staged_state)

    # Handle cancellation
    if mode_value is None:
        _cancel_pending(node, activation)
        activation.state = ActivationState.NONE
        activation.mode = ActivationMode.NONE
        return response

    # Determine master_enable
    master_enable = _get_master_enable(staged_state)

    # Route by mode
    if mode_value == "activate_immediate":
        activation.state = ActivationState.IMMEDIATE
        activation.mode = ActivationMode.IMMEDIATE
        activation.time = datetime.fromtimestamp(now)
        activation.requested_time = datetime.fromtimestamp(now)

        # Compute TAI string once — reused by both PATCH response and GET /active
        from nmos.json.types import NTime
        tai_sec = int(now) + NTime.TAI_UTC_OFFSET
        tai_nsec = int((now % 1) * 1_000_000_000)
        activation.activation_time_tai = f"{tai_sec}:{tai_nsec}"

        do_activation(
            node, resource_id, activation, master_enable,
            is_sender, has_sdp, auto_resolvers,
        )

        response.immediate_activation = True
        response.activation_time = now

    elif mode_value == "activate_scheduled_relative":
        activation.state = ActivationState.PENDING
        activation.mode = ActivationMode.RELATIVE

        delay_sec = _get_requested_delay(staged_state)
        target_time = now + delay_sec

        if delay_sec <= 0:
            # Already past — activate immediately
            activation.state = ActivationState.IMMEDIATE
            activation.time = datetime.fromtimestamp(now)
            do_activation(
                node, resource_id, activation, master_enable,
                is_sender, has_sdp, auto_resolvers,
            )
            response.immediate_activation = True
        else:
            activation.time = datetime.fromtimestamp(target_time)
            activation.requested_time = datetime.fromtimestamp(target_time)
            activation.requested_delta_time = timedelta(seconds=delay_sec)
            # Schedule via DispatchGroup (deferred to streaming module)
            response.delayed_activation = True

        response.activation_time = now

    elif mode_value == "activate_scheduled_absolute":
        activation.state = ActivationState.PENDING
        activation.mode = ActivationMode.ABSOLUTE

        target_time = _get_requested_absolute_time(staged_state)
        delay = target_time - now

        if delay <= 0:
            activation.state = ActivationState.IMMEDIATE
            activation.time = datetime.fromtimestamp(now)
            do_activation(
                node, resource_id, activation, master_enable,
                is_sender, has_sdp, auto_resolvers,
            )
            response.immediate_activation = True
        else:
            activation.time = datetime.fromtimestamp(target_time)
            activation.requested_time = datetime.fromtimestamp(target_time)
            response.delayed_activation = True

        response.activation_time = now

    return response


# ---------------------------------------------------------------------------
# Engine lifecycle
# ---------------------------------------------------------------------------

def _manage_engine_lifecycle(
    activation: Activation, master_enable: bool,
    node: Any = None, resource_id: str = "", is_sender: bool = True,
    transport_str: str = "", interface_name: str = "*",
) -> None:
    """Start or stop the streaming engine.

    When master_enable=True: starts streaming via the streaming sub-module.
    When master_enable=False: stops streaming by setting the stop event.

    Dispatches to doSender*Streaming / doReceiver*Streaming inside the
    activation's Engine DispatchGroup.
    """
    from nmos.node.streaming import stop_streaming, start_streaming

    # Stop existing engine
    stop_streaming(activation)

    if master_enable and node is not None:
        start_streaming(
            node, activation, resource_id, is_sender,
            transport_str, interface_name,
        )
    else:
        activation.engine_state = EngineState.INACTIVE


def _sync_privacy_from_active_params(activation: Activation) -> None:
    """Copy privacy values from active transport params to activation.privacy.

    After staged→active flip, the active transport params have the final
    privacy values (from PATCH body + SDP enrichment). This function:

    1. Reads Protocol, Mode, Curve from active transport params
    2. Reads IV, KeyGenerator, KeyVersion, KeyId (hex → bytes)
    3. Matches KeyId against PrivacyKeys to find PSK
    4. Reads ECDH sender/receiver public keys
    5. Stores everything in activation.privacy

    The actual key derivation happens later in StreamEncryption.from_privacy().
    """
    active = activation.active[0] if activation.active else None
    if active is None:
        return

    def _get_str(name: str) -> str:
        field = getattr(active, name, None)
        if field is None or not hasattr(field, 'defined') or not field.defined:
            return ""
        return str(field.value) if field.value is not None else ""

    def _get_hex_bytes(name: str, expected_len: int) -> bytes | None:
        hex_str = _get_str(name)
        if not hex_str:
            return None
        try:
            b = bytes.fromhex(hex_str)
            if len(b) == expected_len:
                return b
        except ValueError:
            pass
        return None

    # Protocol, Mode, Curve (enum values)
    protocol_str = _get_str("ExtPrivacyProtocol")
    mode_str = _get_str("ExtPrivacyMode")
    curve_str = _get_str("ExtPrivacyEcdhCurve")

    if protocol_str:
        from nmos.enums import EnumRegistry
        activation.privacy.protocol = EnumRegistry.get(protocol_str)
    if mode_str:
        from nmos.enums import EnumRegistry
        activation.privacy.mode = EnumRegistry.get(mode_str)
    if curve_str:
        from nmos.enums import EnumRegistry
        activation.privacy.curve = EnumRegistry.get(curve_str)

    # IV, KeyGenerator, KeyVersion, KeyId (hex → bytes)
    iv = _get_hex_bytes("ExtPrivacyIV", 8)
    if iv is not None:
        activation.privacy.iv = iv

    kg = _get_hex_bytes("ExtPrivacyKeyGenerator", 16)
    if kg is not None:
        activation.privacy.key_generator = kg

    kv = _get_hex_bytes("ExtPrivacyKeyVersion", 4)
    if kv is not None:
        activation.privacy.key_version = kv

    ki = _get_hex_bytes("ExtPrivacyKeyId", 8)
    if ki is not None:
        activation.privacy.key_id = ki

    # Match KeyId against PrivacyKeys to find PSK
    if ki is not None and activation.privacy_keys and activation.privacy_keys.keys:
        for entry in activation.privacy_keys.keys:
            if entry.key_id == ki:
                activation.privacy.psk = entry.psk
                break

    # ECDH public keys (hex → bytes, variable length)
    sender_pub_hex = _get_str("ExtPrivacyEcdhSenderPublicKey")
    if sender_pub_hex and sender_pub_hex != "00":
        try:
            activation.privacy.ecdh_sender_public_key = bytes.fromhex(sender_pub_hex)
        except ValueError:
            pass

    receiver_pub_hex = _get_str("ExtPrivacyEcdhReceiverPublicKey")
    if receiver_pub_hex and receiver_pub_hex != "00":
        try:
            activation.privacy.ecdh_receiver_public_key = bytes.fromhex(receiver_pub_hex)
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _cancel_pending(node: Any, activation: Activation) -> None:
    """Cancel a pending scheduled activation."""
    if hasattr(node, 'dg_pending_activation') and node.dg_pending_activation is not None:
        node.dg_pending_activation.cancel()
        node.dg_pending_activation = None
    activation.state = ActivationState.NONE
    activation.mode = ActivationMode.NONE
    activation.time = None
    activation.requested_time = None
    activation.requested_delta_time = None


def _get_activation_mode(staged_state: Any) -> str | None:
    """Extract the activation mode string from staged state.

    The Activation field is a wrapper (NActivation) whose .value contains
    the NActivationValue with Mode, RequestedTime, ActivationTime.
    """
    if not hasattr(staged_state, 'Activation'):
        return None
    act_wrapper = staged_state.Activation
    if not act_wrapper.defined:
        return None
    act_val = act_wrapper.value
    if hasattr(act_val, 'Mode') and act_val.Mode.defined:
        mode_val = act_val.Mode.value
        if mode_val is None:
            return None
        return str(mode_val) if mode_val else None
    return None


def _get_master_enable(staged_state: Any) -> bool:
    """Extract master_enable from staged state."""
    if hasattr(staged_state, 'MasterEnable'):
        if staged_state.MasterEnable.defined:
            return bool(staged_state.MasterEnable.value)
    return False


def _get_requested_delay(staged_state: Any) -> float:
    """Extract requested delay in seconds from staged state (for relative mode)."""
    if hasattr(staged_state, 'Activation'):
        act = staged_state.Activation
        if hasattr(act, 'RequestedTime') and act.RequestedTime.defined:
            time_str = act.RequestedTime.value
            if time_str is not None:
                return _parse_tai_time_string(str(time_str))
    return 0.0


def _get_requested_absolute_time(staged_state: Any) -> float:
    """Extract requested absolute time as POSIX timestamp from staged state."""
    if hasattr(staged_state, 'Activation'):
        act = staged_state.Activation
        if hasattr(act, 'RequestedTime') and act.RequestedTime.defined:
            time_str = act.RequestedTime.value
            if time_str is not None:
                return _parse_tai_time_string(str(time_str))
    return time.time()


def _parse_tai_time_string(s: str) -> float:
    """Parse a TAI timestamp string 'seconds:nanoseconds' to POSIX seconds.

    TAI offset (currently 37 seconds) is NOT applied here — it is applied
    separately via TAItoUTC().
    """
    parts = s.split(":")
    if len(parts) != 2:
        raise InvalidData(f"invalid activation time format: {s}")
    try:
        sec = int(parts[0])
        nsec = int(parts[1])
        return sec + nsec / 1_000_000_000
    except ValueError as exc:
        raise InvalidData(f"invalid activation time: {exc}") from exc


# ---------------------------------------------------------------------------
# Constraint validation (checkRtpSenderActivation / checkRtpReceiverActivation)
# ---------------------------------------------------------------------------

def _check_constraint(constraint_value: Any, val: Any) -> str | None:
    """Check a single value against a single constraint.

    Returns an error message string on violation, None on pass.

    Behaviours:
    - Undefined value → pass (field wasn't patched)
    - Unconstrained (no min/max/enum/pattern) → pass
    - String "auto" → pass (resolved during flip)
    - Enum: value must be in the allowed list
    - Min/Max: numeric value must be in range
    - Pattern: string must match regex
    """
    cv = constraint_value

    # Unconstrained: no min/max/enum/pattern defined → accept anything
    has_min = hasattr(cv, 'Minimum') and cv.Minimum._defined
    has_max = hasattr(cv, 'Maximum') and cv.Maximum._defined
    has_enum = hasattr(cv, 'Enum') and cv.Enum._defined and cv.Enum._inner
    has_pattern = hasattr(cv, 'Pattern') and cv.Pattern._defined
    if not has_min and not has_max and not has_enum and not has_pattern:
        return None

    # Null value — only valid if null is in enum list
    if val is None:
        if has_enum:
            if None in cv.Enum._inner:
                return None
            return "null not allowed"
        return None

    # String "auto" → skip
    val_str = str(val)
    if val_str == "auto":
        return None

    # Enum check
    if has_enum:
        found = False
        for a in cv.Enum._inner:
            if a is None:
                continue
            if val_str == str(a):
                found = True
                break
            # Bool comparison
            if isinstance(a, bool) and isinstance(val, bool) and a == val:
                found = True
                break
        if not found:
            return f"value {val_str} not in enum {[str(a) for a in cv.Enum._inner]}"

    # Min/Max check (numeric)
    if has_min or has_max:
        try:
            num = float(val) if not isinstance(val, (int, float)) else val
            if has_min and num < cv.Minimum._value:
                return f"value {num} below minimum {cv.Minimum._value}"
            if has_max and num > cv.Maximum._value:
                return f"value {num} above maximum {cv.Maximum._value}"
        except (ValueError, TypeError):
            pass

    # Pattern check
    if has_pattern:
        import re as _re
        if not _re.fullmatch(cv.Pattern._value, val_str):
            return f"value {val_str} does not match pattern {cv.Pattern._value}"

    return None


def validate_transport_params_against_constraints(
    activation: Activation,
    patch_state: Any,
) -> None:
    """Validate patched transport params against constraints.

    For each defined field in the PATCH, checks it against the constraint set.
    Raises InvalidData on violation.
    """
    if not hasattr(patch_state, 'TransportParams') or not patch_state.TransportParams.defined:
        return

    patch_params = patch_state.TransportParams.value
    enabled = activation.enabled_legs

    from nmos.node.activation import _constraint_enum_key

    for i, patch_leg in enumerate(patch_params):
        if i >= enabled or i >= len(activation.constraints):
            continue
        constraint = activation.constraints[i]
        if not hasattr(constraint, 'Constraints'):
            continue
        cs_dict = constraint.Constraints._inner

        # Check each defined field in the patch against its constraint
        for field_name in dir(patch_leg):
            if field_name.startswith('_') or field_name[0].islower():
                continue
            patch_field = getattr(patch_leg, field_name, None)
            if patch_field is None or not hasattr(patch_field, 'defined') or not patch_field.defined:
                continue

            # Find matching constraint by enum key
            key = _constraint_enum_key(field_name, patch_leg)
            if key is None or key not in cs_dict:
                continue

            c = cs_dict[key]
            if not hasattr(c, '_defined') or not c._defined:
                continue

            # Get constraint inner value (NTransportConstraintValue)
            cv = c._value
            if cv is None:
                continue

            # Get the patch value
            val = patch_field.value

            err = _check_constraint(cv, val)
            if err is not None:
                raise InvalidData(f"invalid {field_name}: {err}")
