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

import asyncio
import logging
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
    PendingActivation,
    format_tai,
    tai_to_utc,
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

        # Copy the value.
        #
        # Ask the CLASS whether it has a `value` property, never the instance:
        # on these types `value` raises when the field is undefined, and
        # hasattr() only turns AttributeError into False — anything else
        # propagates. Probing an instance whose field happens to be undefined
        # would abort the entire patch, which is precisely the case here (a
        # client sets requested_time on a staged activation that has none yet).
        if hasattr(type(patch_field), 'value') and hasattr(type(staged_field), 'value'):
            staged_field.value = patch_field.value


# ---------------------------------------------------------------------------
# 3. Flip staged → active
# ---------------------------------------------------------------------------

def flip_activation(
    activation: Activation,
    legs: list[Leg],
    auto_resolvers: dict[str, Any] | None = None,
    serial_number: str = "",
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
                resolver(
                    active, sender_index, receiver_index, leg,
                    serial_number,
                )


#: Highest sender index that still yields a TR-10-9-v2 §17.1 compliant stream
#: number (S = index + 1 must satisfy 0 < S < 128).
_MAX_STREAM_INDEX = 126

#: Base of the auto-resolved destination-port space.
AUTO_PORT_BASE = 22000

#: Ports reserved per Node: 127 stream slots × 2 (RTP data + RTCP at +1).
_NODE_PORT_BLOCK = 256

#: Distinct Node port blocks, one per value of the serial's last two digits.
#: 100 × 256 = 25600 ports, so the space runs 22000–47599 and its highest
#: possible port (block 99, stream 126, RTCP) is 47597 — clear of 65535.
#:
#: 100 devices per host is the deliberate ceiling: past that you add another
#: host or VM, which brings its own IP address and therefore its own
#: ``239.S.C.D`` groups (TR-10-9-v2 §17.1). Ignoring all but the last two
#: serial digits costs nothing globally — serial numbers stay unique, only
#: their port block repeats, and two devices sharing a block cannot be on the
#: same host without also sharing an address.
_NODE_PORT_BLOCKS = 100

#: ASCII digits only, on purpose: ``str.isdigit()`` and ``\d`` both accept
#: non-ASCII decimal digits, which ``int()`` would then happily parse into a
#: block index nobody intended.
_ASCII_DIGITS = "0123456789"

#: Highest block index, i.e. the largest value the last two digits can form.
MAX_SERIAL_PORT_INDEX = _NODE_PORT_BLOCKS - 1

#: How many trailing digits select the block.
_SERIAL_PORT_DIGITS = 2


def serial_port_index(serial_number: str) -> int:
    """The port block ``serial_number`` addresses — its last two digits.

    ``SNX00001`` → 1, ``SNX12345`` → 45, ``SNX00099`` → 99. Only the last two
    digits participate, so the value is always a valid block and no serial is
    ever "too large": digits above the last two are simply not part of the
    port decision. See ``_NODE_PORT_BLOCKS`` for why 100 blocks is enough.

    Returns 0 for an empty serial — an unnamed device has no identity to
    encode, so it keeps the base block.

    Raises:
        InvalidData: if the serial does not end in an ASCII digit. That is
            reported rather than defaulted, because a serial whose last
            character is not a digit gives no basis for choosing a block, and
            silently using block 0 would collide with every other such device.
    """
    serial = (serial_number or "").strip()
    if not serial:
        return 0

    if serial[-1] not in _ASCII_DIGITS:
        raise InvalidData(
            f"serial number {serial!r} must end in a digit 0-9: its last "
            f"{_SERIAL_PORT_DIGITS} digits select the Node's destination-port "
            f"block, and {serial[-1]!r} is not a digit",
        )

    # Take up to the last two characters, stopping at the first non-digit: a
    # one-digit tail ("BOARD-7") addresses block 7 rather than being an error.
    tail = ""
    for ch in reversed(serial[-_SERIAL_PORT_DIGITS:]):
        if ch not in _ASCII_DIGITS:
            break
        tail = ch + tail

    index = int(tail)
    if index > MAX_SERIAL_PORT_INDEX:  # pragma: no cover — two digits cap at 99
        raise InvalidData(
            f"serial number {serial!r} selects block {index}, above the "
            f"maximum {MAX_SERIAL_PORT_INDEX}",
        )
    return index


def _serial_port_offset(serial_number: str) -> int:
    """Offset of ``serial_number``'s port block within the auto port space.

    TR-10-9-v2 §17.1 pins the default multicast address to the media port's
    own address (``239.S.C.D``, C.D being that address's last two octets), so
    two Nodes sharing a media-port address — every Node on a loopback rig —
    necessarily derive the *same* group for the same stream number. The group
    cannot carry Node identity, so the port does: each Node takes a block of
    ``_NODE_PORT_BLOCK`` ports selected by its serial number, whose trailing
    digits are its numeric identity (``SNX00002`` → 2). That follows the same
    "last serial characters distinguish the device" convention as ``nmos.uuid``.

    Raises ``InvalidData`` for a serial that cannot address a block — see
    ``serial_port_index``.
    """
    return serial_port_index(serial_number) * _NODE_PORT_BLOCK


def _get_unused_multicast_address_ipv4(sender_index: int, leg: Any) -> str:
    """Generate a unique IPv4 multicast address for a sender.

    Format: 239.<senderIndex+1>.<mgmtAddr[2]>.<mgmtAddr[3]>, per TR-10-9-v2
    §17.1 — the last two octets come from the leg's (media port's) IPv4
    address, so the address is only as unique as that address is.
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

    # TR-10-9-v2 §17.1: the default address "shall be 239.S.C.D where S is the
    # stream number. Where S shall be greater than 0 and less than 128." S is
    # ``sender_index + 1``, so the highest compliant index is 126 — clamping at
    # 127 emitted 239.128.C.D, one past the bound.
    if sender_index > _MAX_STREAM_INDEX:
        sender_index = _MAX_STREAM_INDEX

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

def resolve_rtp_sender(
    active: Any, sender_index: int, receiver_index: int, leg: Any,
    serial_number: str = "",
) -> None:
    """RTP sender auto-resolution.

    DestinationIp="auto"      → unique multicast address from leg IPv4
    DestinationPort="auto"    → <node block> + 2*senderIndex
    RtcpDestinationIp="auto"  → copy from resolved DestinationIp
    RtcpDestinationPort="auto"→ resolved DestinationPort + 1
    """
    # DestinationIp: "auto" → multicast address
    mcast = _get_unused_multicast_address_ipv4(sender_index, leg)
    _resolve_auto_field(active, "DestinationIp", mcast)

    # DestinationPort: "auto" → <node block> + 2*senderIndex, keeping RTP on
    # the even port and RTCP on the odd one above it (RFC 3550 §11).
    port = AUTO_PORT_BASE + _serial_port_offset(serial_number) + 2 * sender_index
    _resolve_auto_null_field(active, "DestinationPort", port)

    # RtcpDestinationIp: "auto" → copy from resolved DestinationIp
    _resolve_auto_field(active, "RtcpDestinationIp", active.DestinationIp.value)

    # RtcpDestinationPort: "auto" → DestinationPort + 1
    dp = active.DestinationPort.value
    if dp is not None and dp != "auto":
        _resolve_auto_null_field(active, "RtcpDestinationPort", int(dp) + 1)
    else:
        _resolve_auto_null_field(active, "RtcpDestinationPort", port + 1)


def resolve_rtp_receiver(
    active: Any, sender_index: int, receiver_index: int, leg: Any,
    serial_number: str = "",
) -> None:
    """RTP receiver auto-resolution.

    DestinationPort="auto"     → AUTO_PORT_BASE (a placeholder: a
                                 receiver takes the real port from
                                 the sender's SDP)
    RtcpDestinationIp="auto"   → InterfaceIp, override with MulticastIp if present
    RtcpDestinationPort="auto" → DestinationPort + 1
    """
    # DestinationPort: "auto" → base (placeholder, see docstring)
    _resolve_auto_null_field(active, "DestinationPort", AUTO_PORT_BASE)

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
                    rtcp_port_field.value = AUTO_PORT_BASE + 1
            else:
                rtcp_port_field.value = AUTO_PORT_BASE + 1


def resolve_udp_sender(
    active: Any, sender_index: int, receiver_index: int, leg: Any,
    serial_number: str = "",
) -> None:
    """UDP sender auto-resolution.

    DestinationIp="auto"   → unique multicast address
    DestinationPort="auto" → <node block> + senderIndex (NOT 2×, unlike RTP)
    """
    mcast = _get_unused_multicast_address_ipv4(sender_index, leg)
    _resolve_auto_field(active, "DestinationIp", mcast)
    # UDP port formula: <node block> + senderIndex (not 2*senderIndex — UDP
    # has no RTCP companion port to leave room for).
    _resolve_auto_null_field(
        active, "DestinationPort",
        AUTO_PORT_BASE + _serial_port_offset(serial_number) + sender_index,
    )


def resolve_udp_receiver(
    active: Any, sender_index: int, receiver_index: int, leg: Any,
    serial_number: str = "",
) -> None:
    """UDP receiver auto-resolution.

    DestinationPort="auto" → AUTO_PORT_BASE (placeholder, see above)
    """
    _resolve_auto_null_field(active, "DestinationPort", AUTO_PORT_BASE)


def resolve_srt_sender(
    active: Any, sender_index: int, receiver_index: int, leg: Any,
    serial_number: str = "",
) -> None:
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


def resolve_srt_receiver(
    active: Any, sender_index: int, receiver_index: int, leg: Any,
    serial_number: str = "",
) -> None:
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


def resolve_noop(
    active: Any, sender_index: int, receiver_index: int, leg: Any,
    serial_number: str = "",
) -> None:
    """No auto-resolution.  Used by RTSP, USB, NDI, MQTT, WebSocket, RTP-TCP.

    These transports' flip functions do not resolve any "auto" values.
    """
    pass


# ---------------------------------------------------------------------------
# 4. Do activation (orchestrate with atomic rollback)
# ---------------------------------------------------------------------------

def _active_peer_id(staged_state: Any, field_name: str, master_enable: bool) -> Any:
    """The peer id to store in an IS-04 sender/receiver ``subscription``.

    IS-04: the subscription's ``sender_id`` (Receiver) / ``receiver_id``
    (Sender) MUST be null in all cases except where the resource is currently
    configured to receive-from / transmit-to an NMOS peer — i.e. only while
    active. So the staged id is honoured ONLY when ``master_enable`` is true;
    on deactivation this returns ``None``, forcing the subscription id back to
    null (and with it, e.g., a controller's flow tracking that keys off it).
    """
    if not master_enable:
        return None
    field = getattr(staged_state, field_name, None)
    if field is None or not getattr(field, "defined", False):
        return None
    return field.value


def _receiver_transport_file_sdp(activation: Activation) -> str | None:
    """The SDP text from a Receiver's active transport_file, or None.

    The IS-05 PATCH delivers the stream description in ``transport_file``
    (``application/sdp``); after the staged→active flip it lives at
    ``active_state.TransportFile.value.Data``. This is the SDP the Receiver
    has accepted and must verify for IS-11 stream compatibility.
    """
    tf = getattr(activation.active_state, "TransportFile", None)
    if tf is None or not getattr(tf, "defined", False):
        return None
    tfv = getattr(tf, "value", None)
    data = getattr(tfv, "Data", None) if tfv is not None else None
    if data is not None and getattr(data, "defined", False):
        # The field can be present but JSON-null, which is not an SDP — keep
        # that distinct from a string rather than stringifying None.
        sdp_data = data.value
        return None if sdp_data is None else str(sdp_data)
    return None


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
        flip_activation(
            activation, node.legs, auto_resolvers,
            getattr(node, "serial_number", ""),
        )
    except Exception as exc:
        raise UnexpectedError(
            f"cannot flip staged to active: {exc}"
        ) from exc

    # Step 1b: Copy activation metadata from staged to active state
    # (SenderId/ReceiverId, TransportFile — done in the activation handlers)
    staged = activation.staged_state
    active = activation.active_state
    if staged is not None and active is not None:
        # MasterEnable comes from the caller, NOT from staged. For an immediate
        # activation the two are identical. For a scheduled one they can differ:
        # the on/off intent is fixed when the activation is scheduled, so a
        # master_enable staged afterwards belongs to some future activation, not
        # this one. Reading it from staged here would report a state on /active
        # that disagrees with what was actually done to the stream.
        if hasattr(active, "MasterEnable"):
            active.MasterEnable.value = master_enable

        for field_name in ("SenderId", "ReceiverId", "TransportFile"):
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

        elif not is_sender:
            # Receiver branch. NOTE: do NOT gate on ``has_sdp`` — that flag is
            # sender-centric ("the node generates an SDP for this resource")
            # and the receiver PATCH path always passes has_sdp=False, even
            # though the Receiver very much has an SDP: the incoming
            # transport_file. Gating on it here left the status permanently
            # ``unknown``.
            #
            # The incoming stream's SDP arrives in the transport_file
            # (IS-05 PATCH). Cache it — keyed by the receiver,
            # the same way a sender's generated SDP is — so the IS-11
            # stream-compatibility check has something to verify, then
            # ALWAYS (re)evaluate. This moves the receiver status off the
            # permanent "unknown":
            #   * valid SDP, within caps   → compliant_stream
            #   * valid SDP, outside caps  → non_compliant_stream
            #   * no SDP / unparseable SDP → unknown (evaluation can't run)
            # On deactivation the cache is dropped, so it returns to unknown.
            # A Receiver derives no registry state from the SDP, so this
            # cache is its only home. The store is best-effort: a malformed
            # incoming SDP must not roll back an otherwise-valid activation —
            # it just leaves nothing cached, so the status evaluates to
            # unknown rather than a stale value.
            try:
                if master_enable:
                    sdp_text = _receiver_transport_file_sdp(activation)
                    node.sdp.remove(static_id)
                    if sdp_text:
                        node._store_parsed_sdp(static_id, sdp_text)
                else:
                    node.sdp.remove(static_id)
            except Exception:
                node.sdp.remove(static_id)
            try:
                receiver = node.receivers.get(static_id)
                if receiver is not None:
                    node.set_receiver_compatibility_state(receiver)
            except Exception:
                pass

        # Step 4: Update IS-04 subscription (updateReceiverSubscription/updateSenderSubscription)
        # IS-04: the subscription peer id MUST be null unless active, so it is
        # read from the staged state ONLY when master_enable is true (see
        # ``_active_peer_id``) — on deactivation it is forced back to null.
        if is_sender:
            from nmos.node.updates import SenderUpdate
            node.update_sender(resource_id, SenderUpdate(
                subscription_active=master_enable,
                subscription_receiver_id=_active_peer_id(
                    activation.staged_state, "ReceiverId", master_enable),
            ))
        else:
            from nmos.node.updates import ReceiverUpdate
            node.update_receiver(resource_id, ReceiverUpdate(
                subscription_active=master_enable,
                subscription_sender_id=_active_peer_id(
                    activation.staged_state, "SenderId", master_enable),
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
        _cancel_pending(node, resource_id, activation)
        activation.state = ActivationState.NONE
        activation.mode = ActivationMode.NONE
        return response

    # Determine master_enable
    master_enable = _get_master_enable(staged_state)

    # An activation is already scheduled for this resource, and this PATCH is
    # not trying to replace it — a PATCH carrying its own mode would have been
    # refused with 423 before reaching here. So it is a PATCH staging
    # parameters alongside a pending activation, and it must not disturb it.
    #
    # This matters because the staged mode is still the scheduled one, so
    # processing it again would re-measure a relative delay from *this*
    # request's arrival. IS-05 defines the relative mode as firing when the
    # clock reaches "time of message receipt + requested_time", where the
    # message is the one that requested the activation — so re-anchoring to a
    # later request would move a deadline the client was already promised.
    #
    # The pending timer keeps everything it captured when it was armed,
    # including master_enable: a scheduled activation does what it was asked to
    # do at the moment it was scheduled. The transport parameters it applies are
    # read when it fires, so this PATCH still affects the configuration that
    # goes live — just not when, and not whether.
    # Neither response flag is set, so this answers 200 with a null
    # activation_time: IS-05 reserves 202 for a request that *schedules* an
    # activation, and reports a null activation_time when the request did not
    # ask for one. The staged activation still shows the pending mode and
    # requested_time, because /staged genuinely still has one pending.
    if (mode_value in ("activate_scheduled_relative", "activate_scheduled_absolute")
            and resource_id in node.dg_pending_activation):
        return response

    # Route by mode
    if mode_value == "activate_immediate":
        activation.state = ActivationState.IMMEDIATE
        activation.mode = ActivationMode.IMMEDIATE
        activation.time = datetime.fromtimestamp(now)
        activation.requested_time = datetime.fromtimestamp(now)

        # Compute TAI string once — reused by both PATCH response and GET /active
        activation.activation_time_tai = format_tai(now)

        do_activation(
            node, resource_id, activation, master_enable,
            is_sender, has_sdp, auto_resolvers,
        )

        response.immediate_activation = True
        response.activation_time = now

    elif mode_value == "activate_scheduled_relative":
        activation.state = ActivationState.PENDING
        activation.mode = ActivationMode.RELATIVE

        # Kept verbatim: the response echoes what was asked for, and rebuilding
        # it from the parsed value would shed nanoseconds.
        activation.requested_time_string = _get_requested_time_string(staged_state) or ""

        delay_sec = _get_requested_delay(staged_state)
        target_time = now + delay_sec

        if delay_sec <= 0:
            # Already past — activate immediately. The mode stays RELATIVE, so
            # /active still reports which kind of activation produced this state,
            # timestamped when it actually happened rather than when it was due.
            activation.state = ActivationState.IMMEDIATE
            activation.time = datetime.fromtimestamp(now)
            activation.activation_time_tai = format_tai(now)
            do_activation(
                node, resource_id, activation, master_enable,
                is_sender, has_sdp, auto_resolvers,
            )
            response.immediate_activation = True
        else:
            activation.time = datetime.fromtimestamp(target_time)
            activation.requested_time = datetime.fromtimestamp(target_time)
            activation.requested_delta_time = timedelta(seconds=delay_sec)
            # The 202 reports when the activation WILL happen, not now.
            activation.activation_time_tai = format_tai(target_time)
            _schedule_pending_activation(
                node, resource_id, activation, master_enable,
                is_sender, has_sdp, auto_resolvers, delay=delay_sec,
            )
            response.delayed_activation = True

        response.activation_time = now

    elif mode_value == "activate_scheduled_absolute":
        activation.state = ActivationState.PENDING
        activation.mode = ActivationMode.ABSOLUTE

        activation.requested_time_string = _get_requested_time_string(staged_state) or ""

        target_time = _get_requested_absolute_time(staged_state)
        delay = target_time - now

        if delay <= 0:
            # Target already passed — activate now, timestamped now.
            activation.state = ActivationState.IMMEDIATE
            activation.time = datetime.fromtimestamp(now)
            activation.activation_time_tai = format_tai(now)
            do_activation(
                node, resource_id, activation, master_enable,
                is_sender, has_sdp, auto_resolvers,
            )
            response.immediate_activation = True
        else:
            activation.time = datetime.fromtimestamp(target_time)
            activation.requested_time = datetime.fromtimestamp(target_time)
            # For an absolute activation the requested instant IS the activation
            # instant, so report the client's own string rather than a value
            # round-tripped through a float and a microsecond-resolution
            # datetime — that would come back a few hundred nanoseconds adrift.
            activation.activation_time_tai = (
                activation.requested_time_string or format_tai(target_time)
            )
            _schedule_pending_activation(
                node, resource_id, activation, master_enable,
                is_sender, has_sdp, auto_resolvers, delay=delay,
            )
            response.delayed_activation = True

        response.activation_time = now

    return response


# ---------------------------------------------------------------------------
# Scheduled activation timers
# ---------------------------------------------------------------------------

def _schedule_pending_activation(
    node: Any,
    resource_id: str,
    activation: Activation,
    master_enable: bool,
    is_sender: bool,
    has_sdp: bool,
    auto_resolvers: dict[str, Any] | None,
    delay: float,
) -> None:
    """Arm a background timer that activates this resource in ``delay`` seconds.

    The delay is computed once, here, from the target the client asked for; the
    timer never recomputes it. Everything the activation will need —
    ``master_enable``, the resolvers, the activation object itself — is captured
    now, so the activation that eventually happens is the one the client
    described in this PATCH.

    Synchronous on purpose. Arming a timer needs no await, and keeping this
    function (and therefore the whole PATCH handler) free of suspension points
    is what stops a timer firing halfway through a request. See the invariant
    next to ``Node.dg_pending_activation``.
    """
    # One pending activation per resource: a fresh scheduled PATCH replaces the
    # timer this resource already had rather than leaving two racing to fire.
    _cancel_pending_activation(node, resource_id)

    stop = asyncio.Event()
    task = asyncio.ensure_future(_pending_activation_task(
        node, resource_id, activation, master_enable,
        is_sender, has_sdp, auto_resolvers, delay, stop,
    ))
    node.dg_pending_activation[resource_id] = PendingActivation(task=task, stop=stop)


async def _pending_activation_task(
    node: Any,
    resource_id: str,
    activation: Activation,
    master_enable: bool,
    is_sender: bool,
    has_sdp: bool,
    auto_resolvers: dict[str, Any] | None,
    delay: float,
    stop: asyncio.Event,
) -> None:
    """Wait for the activation's deadline, then activate — unless cancelled."""
    try:
        try:
            # Two ways out of the wait: the stop event fires, meaning the client
            # cancelled with mode=null or replaced this activation with a later
            # PATCH — or the deadline elapses and the activation is due.
            await asyncio.wait_for(stop.wait(), timeout=delay)
            return
        except asyncio.TimeoutError:
            pass

        # Nothing below awaits. The activation therefore runs as one
        # uninterruptible step and cannot interleave with an API handler
        # mutating this same resource.
        entry = node.dg_pending_activation.get(resource_id)
        if entry is None or entry.task is not asyncio.current_task():
            # Cancelled or superseded after the deadline but before we ran.
            return

        do_activation(
            node, resource_id, activation, master_enable,
            is_sender, has_sdp, auto_resolvers,
        )

        # The activation has happened, so /staged must stop advertising it as
        # pending. That is also what releases the 423 lock on further PATCHes.
        # activation.state and .mode are deliberately left alone: GET /active
        # reports which scheduled activation produced the current active state
        # and reads both fields to do it.
        reset_staged_activation(activation)

        # No HTTP response carries this activation, so publish here or the
        # registry keeps serving the pre-activation snapshot indefinitely.
        node.publish()

    except asyncio.CancelledError:
        raise
    except Exception:
        # A background task has nobody to return an error to, so an unhandled
        # exception would vanish. do_activation has already rolled the resource
        # back; make the failure visible.
        logging.exception(
            "scheduled activation failed for %s", resource_id,
        )
    finally:
        entry = node.dg_pending_activation.get(resource_id)
        if entry is not None and entry.task is asyncio.current_task():
            del node.dg_pending_activation[resource_id]


def _cancel_pending_activation(node: Any, resource_id: str) -> None:
    """Stop this resource's pending activation timer, if it has one.

    Removing the entry is what actually disarms the activation: even if the
    timer has already passed its deadline and is queued to run, it re-checks
    that it is still the registered timer for this resource before doing
    anything. So there is no need to wait for the task to finish here, and
    therefore no await — which matters, because callers are inside await-free
    regions that must stay that way.
    """
    entry = node.dg_pending_activation.pop(resource_id, None)
    if entry is None:
        return
    entry.stop.set()
    entry.task.cancel()


def cancel_pending_activations(node: Any) -> None:
    """Stop every pending activation timer on this node.

    Called when the node shuts down. Without it a scheduled activation outlives
    the server that accepted it and fires into a half-dismantled Node — most
    visibly under test, where the event loop outlives any single Node.
    """
    for resource_id in list(node.dg_pending_activation):
        _cancel_pending_activation(node, resource_id)


def reset_staged_activation(activation: Activation) -> None:
    """Clear the staged activation's mode/requested_time/activation_time.

    Once an activation has been carried out, /staged must no longer advertise
    it as pending: IS-05 reports the completed activation on the PATCH response
    (and afterwards on /active), while /staged goes back to null/null/null.
    Clearing the mode is also what releases the 423 that a pending activation
    holds over subsequent PATCHes.
    """
    state = activation.staged_state
    if state is None or not hasattr(state, "Activation"):
        return
    act = state.Activation
    if not act.defined:
        return
    av = act.value
    if hasattr(av, "Mode"):
        av.Mode.value = None
    if hasattr(av, "ActivationTime"):
        av.ActivationTime.value = None
    if hasattr(av, "RequestedTime"):
        av.RequestedTime.value = None


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

def _cancel_pending(node: Any, resource_id: str, activation: Activation) -> None:
    """Cancel this resource's pending scheduled activation (PATCH mode=null).

    IS-05 lets a client withdraw an activation it has scheduled by PATCHing a
    null mode. That disarms the timer and returns the activation's timing fields
    to their unset state, so /staged reports nothing pending.
    """
    _cancel_pending_activation(node, resource_id)
    activation.state = ActivationState.NONE
    activation.mode = ActivationMode.NONE
    activation.time = None
    activation.requested_time = None
    activation.requested_delta_time = None
    activation.activation_time_tai = ""
    activation.requested_time_string = ""


def _activation_value(staged_state: Any) -> Any:
    """The activation object inside a staged/active state, or None.

    The state's ``Activation`` field is a wrapper; Mode, RequestedTime and
    ActivationTime live on the value *inside* it. Reading them off the wrapper
    finds nothing and reports no error, so every reader goes through here rather
    than unwrapping by hand.
    """
    if staged_state is None or not hasattr(staged_state, 'Activation'):
        return None
    wrapper = staged_state.Activation
    if not wrapper.defined:
        return None
    return wrapper.value


def _get_activation_mode(staged_state: Any) -> str | None:
    """The requested activation mode, or None if none is requested.

    A null mode is how a client withdraws an activation it scheduled earlier,
    so "no mode" and "mode explicitly null" are deliberately the same answer
    here — both mean "there is no activation to carry out".
    """
    act_val = _activation_value(staged_state)
    if act_val is None:
        return None
    if hasattr(act_val, 'Mode') and act_val.Mode.defined:
        mode_val = act_val.Mode.value
        if mode_val is None:
            return None
        return str(mode_val) if mode_val else None
    return None


def _get_requested_time_string(staged_state: Any) -> str | None:
    """The raw ``requested_time`` the client asked for, or None."""
    act_val = _activation_value(staged_state)
    if act_val is None:
        return None
    if not hasattr(act_val, 'RequestedTime') or not act_val.RequestedTime.defined:
        return None
    value = act_val.RequestedTime.value
    return None if value is None else str(value)


def _get_master_enable(staged_state: Any) -> bool:
    """Extract master_enable from staged state."""
    if hasattr(staged_state, 'MasterEnable'):
        if staged_state.MasterEnable.defined:
            return bool(staged_state.MasterEnable.value)
    return False


def _get_requested_delay(staged_state: Any) -> float:
    """How far in the future a relative activation was asked for, in seconds.

    ``requested_time`` is a duration here, not an instant, so no TAI conversion
    applies. Zero (or a missing value) means "as soon as possible", which the
    caller turns into an immediate activation.
    """
    time_str = _get_requested_time_string(staged_state)
    if time_str is None:
        return 0.0
    return _parse_tai_time_string(time_str)


def _get_requested_absolute_time(staged_state: Any) -> float:
    """Extract requested absolute time as POSIX timestamp from staged state.

    The client states the target in TAI, which currently runs 37 s ahead of the
    POSIX clock. The result is compared against time.time() to derive the delay,
    so it MUST be converted — treating a TAI instant as POSIX would fire every
    absolute activation 37 s late.

    A missing target means "now", which the caller turns into an immediate
    activation.
    """
    time_str = _get_requested_time_string(staged_state)
    if time_str is None:
        return time.time()
    return tai_to_utc(_parse_tai_time_string(time_str))


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
