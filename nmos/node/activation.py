# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Transport activation descriptor registry + init functions.

Implements InitSenderActivation / InitReceiverActivation as a data-driven
registry instead of a 9-arm switch. Each transport registers a descriptor
containing type constructors, port formulas, privacy support, and a small
init_extra function for transport-specific fields.

Adding a new transport = adding 1 descriptor + 1 init_extra function.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, cast

from nmos.node.types import (
    MAX_LEGS,
    Activation,
    Leg,
    Privacy,
    PrivacyPreSharedKeys,
    hex_from_bytes,
)


# ---------------------------------------------------------------------------
# Transport descriptor
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TransportDescriptor:
    """Describes activation object construction for a specific transport."""

    # Type constructors — called with () to create fresh instances
    sender_params_type: type
    sender_constraints_type: type
    sender_activation_type: type
    receiver_params_type: type | None    # None = not supported (MQTT receiver)
    receiver_constraints_type: type | None
    receiver_activation_type: type | None

    # Privacy
    has_privacy: bool
    privacy_protocol: Any = None         # EnumId
    privacy_kv_protocol: Any = None      # EnumId

    # Port calculation: index → port number
    sender_port_fn: Callable[[int], int] = lambda i: 27500 + i
    receiver_port_fn: Callable[[int], int] = lambda i: 27500

    # Transport-specific init (sets fields beyond common pattern)
    init_sender_extra: Callable[..., None] | None = None
    init_receiver_extra: Callable[..., None] | None = None

    # Auto-resolution rules for flip (field_name → resolver)
    sender_auto_resolvers: dict[str, Callable[..., Any]] = field(default_factory=dict)
    receiver_auto_resolvers: dict[str, Callable[..., Any]] = field(default_factory=dict)

    # Features
    has_sdp: bool = True
    has_streaming_engine: bool = True
    has_rtcp: bool = False
    has_fec: bool = False


# ---------------------------------------------------------------------------
# Registry — populated lazily
# ---------------------------------------------------------------------------

_TRANSPORT_REGISTRY: dict[Any, TransportDescriptor] | None = None


def get_transport_descriptor(transport_enum: Any) -> TransportDescriptor:
    """Look up transport descriptor. Raises KeyError if not registered."""
    _ensure_registry()
    assert _TRANSPORT_REGISTRY is not None
    desc = _TRANSPORT_REGISTRY.get(transport_enum)
    if desc is None:
        raise KeyError(f"unsupported transport: {transport_enum}")
    return desc


def _ensure_registry() -> None:
    global _TRANSPORT_REGISTRY
    if _TRANSPORT_REGISTRY is not None:
        return
    _TRANSPORT_REGISTRY = _build_registry()


# ---------------------------------------------------------------------------
# Generic sender/receiver init
# ---------------------------------------------------------------------------

def init_sender_activation(
    activation: Activation,
    legs: list[Leg],
    transport: Any,
    descriptor: TransportDescriptor,
    privacy_enabled: bool = True,
    **extra: Any,
) -> None:
    """Initialize sender activation for any transport.

    Creates typed param/constraint/activation objects, sets common fields
    (source IP, port), then calls transport-specific init_extra.
    """
    for index in range(MAX_LEGS):
        leg = legs[index] if index < len(legs) else Leg()
        staged = activation.staged[index]
        active = activation.active[index]
        constraints = activation.constraints[index]

        # Common: SourceIp (or InterfaceIp for MQTT)
        if leg.enable:
            ip_str = (
                str(leg.ipv6.address) if leg.use_ipv6 and leg.ipv6.address
                else str(leg.ipv4.address) if leg.ipv4.address
                else "auto"
            )
        else:
            ip_str = "auto"

        _set_field(staged, "SourceIp", ip_str)
        _set_field(active, "SourceIp", ip_str if leg.enable else "0.0.0.0")
        # MQTT uses InterfaceIp instead
        _set_field(staged, "InterfaceIp", ip_str)
        _set_field(active, "InterfaceIp", ip_str if leg.enable else "0.0.0.0")

        # Common: SourcePort
        port = descriptor.sender_port_fn(activation.sender_index)
        _set_null_field(staged, "SourcePort", port)
        _set_null_field(active, "SourcePort", port)

        # Initialize constraints map (NTransportConstraintsValue is a map wrapper)
        if hasattr(constraints, 'Constraints'):
            constraints.Constraints._inner = {}

        # Transport-specific extra fields
        if descriptor.init_sender_extra is not None:
            descriptor.init_sender_extra(
                staged, active, constraints, leg, activation, **extra,
            )

        # Common constraints: SourceIp (cannot change interface binding)
        _set_static_constraint(constraints, "SourceIp", staged)
        _set_static_constraint(constraints, "InterfaceIp", staged)
        _set_static_constraint(constraints, "SourcePort", staged)

    # Privacy — only populate fields when privacy_enabled=True.
    # When False, fields exist in transport params but stay NULL (per TR-10-13).
    # Skip privacy initialization entirely when transport privacy encryption is disabled.
    if descriptor.has_privacy and privacy_enabled:
        for index in range(MAX_LEGS):
            if index < len(legs) and legs[index].enable:
                init_sender_privacy(
                    activation.privacy, activation.privacy_keys,
                    activation.staged[index], activation.active[index],
                    activation.constraints[index],
                    descriptor.privacy_protocol,
                    descriptor.privacy_kv_protocol,
                )


def init_receiver_activation(
    activation: Activation,
    legs: list[Leg],
    transport: Any,
    format_enum: Any,
    descriptor: TransportDescriptor,
    privacy_enabled: bool = True,
    **extra: Any,
) -> None:
    """Initialize receiver activation for any transport."""
    if descriptor.receiver_params_type is None:
        raise InvalidOperationError("transport does not support receivers")

    for index in range(MAX_LEGS):
        leg = legs[index] if index < len(legs) else Leg()
        staged = activation.staged[index]
        active = activation.active[index]
        constraints = activation.constraints[index]

        # Common: InterfaceIp
        if leg.enable:
            ip_str = (
                str(leg.ipv6.address) if leg.use_ipv6 and leg.ipv6.address
                else str(leg.ipv4.address) if leg.ipv4.address
                else "auto"
            )
        else:
            ip_str = "auto"

        _set_field(staged, "InterfaceIp", ip_str)
        _set_field(active, "InterfaceIp", ip_str if leg.enable else "0.0.0.0")
        # SRT receiver uses DestinationIp instead
        _set_field(staged, "DestinationIp", ip_str)
        _set_field(active, "DestinationIp", ip_str if leg.enable else "0.0.0.0")

        # Common: receiver port
        port = descriptor.receiver_port_fn(activation.receiver_index)
        _set_null_field(staged, "DestinationPort", port)
        _set_null_field(active, "DestinationPort", port)

        # Initialize constraints map
        if hasattr(constraints, 'Constraints'):
            constraints.Constraints._inner = {}

        # Transport-specific extra fields
        if descriptor.init_receiver_extra is not None:
            descriptor.init_receiver_extra(
                staged, active, constraints, leg, activation, format_enum, **extra,
            )

        # Common constraints
        _set_static_constraint(constraints, "InterfaceIp", staged)
        _set_static_constraint(constraints, "DestinationIp", staged)

    # Privacy — only populate when privacy_enabled=True (same gate as sender)
    if descriptor.has_privacy and descriptor.receiver_params_type is not None and privacy_enabled:
        for index in range(MAX_LEGS):
            if index < len(legs) and legs[index].enable:
                init_receiver_privacy(
                    activation.privacy, activation.privacy_keys,
                    activation.staged[index], activation.active[index],
                    activation.constraints[index],
                    descriptor.privacy_protocol,
                    descriptor.privacy_kv_protocol,
                )


# ---------------------------------------------------------------------------
# Transport-specific init_extra functions (SENDER)
# ---------------------------------------------------------------------------

def _init_rtp_sender_extra(
    staged: Any, active: Any, constraints: Any,
    leg: Leg, activation: Activation, **extra: Any,
) -> None:
    """RTP-specific sender fields: RTCP, RTP enabled."""
    idx = activation.sender_index
    _set_field(staged, "RtpEnabled", leg.enable)
    _set_field(active, "RtpEnabled", leg.enable)
    _set_field(staged, "RtcpEnabled", leg.enable)
    _set_field(active, "RtcpEnabled", leg.enable)
    _set_null_field(staged, "RtcpSourcePort", 27501 + 2 * idx)
    _set_null_field(active, "RtcpSourcePort", 27501 + 2 * idx)
    _set_field(staged, "DestinationIp", "auto")
    _set_field(active, "DestinationIp", "0.0.0.0")
    _set_null_field(staged, "DestinationPort", "auto")
    _set_null_field(active, "DestinationPort", 27500 + 2 * idx)
    _set_field(staged, "RtcpDestinationIp", "auto")
    _set_field(active, "RtcpDestinationIp", "0.0.0.0")
    _set_null_field(staged, "RtcpDestinationPort", "auto")
    _set_null_field(active, "RtcpDestinationPort", 27501 + 2 * idx)
    # Constraints
    cs = _get_constraint_set(constraints)
    _add_enum_constraint(cs, "RtpEnabled", [leg.enable], "cannot enable/disable RTP from transport parameters", staged)
    _add_enum_constraint(cs, "RtcpEnabled", [leg.enable], "cannot enable/disable RTCP from transport parameters", staged)
    _add_static_constraint_from_staged(cs, "RtcpSourcePort", staged)
    _add_enum_constraint(cs, "RtcpDestinationIp", ["auto"], "cannot configure RTCP", staged)
    _add_enum_constraint(cs, "RtcpDestinationPort", ["auto"], "cannot configure RTCP", staged)
    _add_unconstrained(cs, "DestinationIp", staged)
    _add_unconstrained(cs, "DestinationPort", staged)
    _set_constraint_set(constraints, cs)


def _init_udp_sender_extra(
    staged: Any, active: Any, constraints: Any,
    leg: Leg, activation: Activation, **extra: Any,
) -> None:
    """UDP-specific sender fields: Enabled, destination."""
    idx = activation.sender_index
    _set_field(staged, "Enabled", leg.enable)
    _set_field(active, "Enabled", leg.enable)
    _set_field(staged, "DestinationIp", "auto")
    _set_field(active, "DestinationIp", "0.0.0.0")
    _set_null_field(staged, "DestinationPort", "auto")
    _set_null_field(active, "DestinationPort", 27500 + idx)
    cs = _get_constraint_set(constraints)
    _add_enum_constraint(cs, "Enabled", [leg.enable], "cannot enable/disable UDP from transport parameters", staged)
    _add_unconstrained(cs, "DestinationIp", staged)
    _add_unconstrained(cs, "DestinationPort", staged)
    _set_constraint_set(constraints, cs)


def _init_srt_sender_extra(
    staged: Any, active: Any, constraints: Any,
    leg: Leg, activation: Activation, **extra: Any,
) -> None:
    """SRT-specific sender fields: Protocol, Latency, StreamId, DestinationIp."""
    _set_null_field(staged, "DestinationIp", None)
    _set_null_field(active, "DestinationIp", None)
    _set_null_field(staged, "DestinationPort", "auto")
    _set_null_field(active, "DestinationPort", 0)
    # SRT sender protocol: Listener (enums.Listener = "listener")
    from nmos.enums import Listener
    _try_set_enum(staged, "Protocol", Listener.s)
    _try_set_enum(active, "Protocol", Listener.s)
    _set_null_field(staged, "Latency", 0)
    _set_null_field(active, "Latency", 0)
    _set_null_field(staged, "StreamId", None)
    _set_null_field(active, "StreamId", None)
    cs = _get_constraint_set(constraints)
    _add_unconstrained(cs, "DestinationIp", staged)
    _add_unconstrained(cs, "DestinationPort", staged)
    _add_unconstrained(cs, "Latency", staged)
    _add_enum_constraint(cs, "StreamId", [None], "StreamID not supported", staged)
    _set_constraint_set(constraints, cs)


def _init_rtp_tcp_sender_extra(
    staged: Any, active: Any, constraints: Any,
    leg: Leg, activation: Activation, **extra: Any,
) -> None:
    """RTP-TCP sender: like RTP but no destination fields."""
    idx = activation.sender_index
    _set_field(staged, "RtpEnabled", leg.enable)
    _set_field(active, "RtpEnabled", leg.enable)
    _set_field(staged, "RtcpEnabled", leg.enable)
    _set_field(active, "RtcpEnabled", leg.enable)
    _set_null_field(staged, "RtcpSourcePort", 27501 + 2 * idx)
    _set_null_field(active, "RtcpSourcePort", 27501 + 2 * idx)
    cs = _get_constraint_set(constraints)
    _add_enum_constraint(cs, "RtpEnabled", [leg.enable], "cannot enable/disable RTP", staged)
    _add_enum_constraint(cs, "RtcpEnabled", [leg.enable], "cannot enable/disable RTCP", staged)
    _add_static_constraint_from_staged(cs, "RtcpSourcePort", staged)
    _set_constraint_set(constraints, cs)


def _init_rtsp_sender_extra(
    staged: Any, active: Any, constraints: Any,
    leg: Leg, activation: Activation, **extra: Any,
) -> None:
    """RTSP sender: minimal (SourceIp, SourcePort only)."""
    pass  # Common fields already set by generic init


def _init_usb_sender_extra(
    staged: Any, active: Any, constraints: Any,
    leg: Leg, activation: Activation, **extra: Any,
) -> None:
    """USB sender: minimal (SourceIp, SourcePort only)."""
    pass  # Common fields already set by generic init


def _init_ndi_sender_extra(
    staged: Any, active: Any, constraints: Any,
    leg: Leg, activation: Activation, **extra: Any,
) -> None:
    """NDI sender: SourceName from groupHint, MachineName from serial."""
    group_hint = extra.get("group_hint", "")
    source_name = group_hint.replace(" ", "").replace(":", "_")
    _set_field(staged, "SourceName", source_name)
    _set_field(active, "SourceName", source_name)
    _set_field(staged, "MachineName", activation.sender_name)
    _set_field(active, "MachineName", activation.sender_name)
    # Override port to fixed 5960
    _set_null_field(staged, "SourcePort", 5960)
    _set_null_field(active, "SourcePort", 5960)
    cs = _get_constraint_set(constraints)
    _add_enum_constraint(cs, "MachineName", [activation.sender_name], context=staged)
    _add_enum_constraint(cs, "SourceName", [source_name], context=staged)
    _add_static_constraint_from_staged(cs, "SourcePort", staged)
    _set_constraint_set(constraints, cs)


def _init_mqtt_sender_extra(
    staged: Any, active: Any, constraints: Any,
    leg: Leg, activation: Activation, **extra: Any,
) -> None:
    """MQTT sender: broker params, no SourceIp/SourcePort."""
    _set_null_field(staged, "DestinationHost", None)
    _set_null_field(active, "DestinationHost", None)
    _set_null_field(staged, "DestinationPort", None)
    _set_null_field(active, "DestinationPort", None)
    _set_null_field(staged, "BrokerTopic", None)
    _set_null_field(active, "BrokerTopic", None)
    _set_null_field(staged, "ConnectionStatusBrokerTopic", None)
    _set_null_field(active, "ConnectionStatusBrokerTopic", None)
    _set_field(staged, "BrokerAuthorization", False)
    _set_field(active, "BrokerAuthorization", False)
    cs = _get_constraint_set(constraints)
    _add_unconstrained(cs, "DestinationHost", staged)
    _add_unconstrained(cs, "DestinationPort", staged)
    _add_unconstrained(cs, "BrokerTopic", staged)
    _add_unconstrained(cs, "ConnectionStatusBrokerTopic", staged)
    _add_unconstrained(cs, "BrokerAuthorization", staged)
    _set_constraint_set(constraints, cs)


def _init_websocket_sender_extra(
    staged: Any, active: Any, constraints: Any,
    leg: Leg, activation: Activation, **extra: Any,
) -> None:
    """WebSocket sender: ConnectionUri, ConnectionAuthorization."""
    idx = activation.sender_index
    ip_str = "auto"
    if leg.enable:
        ip_str = (
            str(leg.ipv6.address) if leg.use_ipv6 and leg.ipv6.address
            else str(leg.ipv4.address) if leg.ipv4.address
            else "auto"
        )
    port = 27500 + idx
    if leg.use_ipv6:
        uri = f"wss://[{ip_str}]:{port}/x-manufacturer/wss"
    else:
        uri = f"wss://{ip_str}:{port}/x-manufacturer/wss"
    _set_null_field(staged, "ConnectionUri", uri)
    _set_null_field(active, "ConnectionUri", uri)
    _set_field(staged, "ConnectionAuthorization", False)
    _set_field(active, "ConnectionAuthorization", False)
    cs = _get_constraint_set(constraints)
    _add_static_constraint_from_staged(cs, "ConnectionUri", staged)
    _add_static_constraint_from_staged(cs, "ConnectionAuthorization", staged)
    _set_constraint_set(constraints, cs)


# ---------------------------------------------------------------------------
# Transport-specific init_extra functions (RECEIVER)
# ---------------------------------------------------------------------------

def _init_rtp_receiver_extra(
    staged: Any, active: Any, constraints: Any,
    leg: Leg, activation: Activation, format_enum: Any = None, **extra: Any,
) -> None:
    """RTP receiver: RtpEnabled, RtcpEnabled, MulticastIp, SourceIp, DestinationPort."""
    _set_field(staged, "RtpEnabled", leg.enable)
    _set_field(active, "RtpEnabled", leg.enable)
    _set_field(staged, "RtcpEnabled", leg.enable)
    _set_field(active, "RtcpEnabled", leg.enable)
    _set_null_field(staged, "SourceIp", None)
    _set_null_field(active, "SourceIp", None)
    _set_null_field(staged, "DestinationPort", "auto")
    _set_null_field(active, "DestinationPort", 27500)  # resolved from "auto"
    _set_null_field(staged, "MulticastIp", None)
    _set_null_field(active, "MulticastIp", None)
    _set_field(staged, "RtcpDestinationIp", "auto")
    _set_field(active, "RtcpDestinationIp", "0.0.0.0")
    _set_null_field(staged, "RtcpDestinationPort", "auto")
    _set_null_field(active, "RtcpDestinationPort", 27501)  # resolved from "auto"
    cs = _get_constraint_set(constraints)
    _add_enum_constraint(cs, "RtpEnabled", [leg.enable], context=staged)
    _add_enum_constraint(cs, "RtcpEnabled", [leg.enable], context=staged)
    _add_unconstrained(cs, "SourceIp", staged)
    _add_unconstrained(cs, "DestinationPort", staged)
    _add_unconstrained(cs, "RtcpDestinationPort", staged)
    _add_enum_constraint(cs, "RtcpDestinationIp", ["auto"], "cannot configure RTCP", staged)
    # MulticastIp: unconstrained for multicast transports, constrained to nil otherwise
    _add_unconstrained(cs, "MulticastIp", staged)
    _set_constraint_set(constraints, cs)


def _init_udp_receiver_extra(
    staged: Any, active: Any, constraints: Any,
    leg: Leg, activation: Activation, format_enum: Any = None, **extra: Any,
) -> None:
    """UDP receiver: Enabled, SourceIp, DestinationPort, MulticastIp."""
    _set_field(staged, "Enabled", leg.enable)
    _set_field(active, "Enabled", leg.enable)
    _set_null_field(staged, "SourceIp", None)
    _set_null_field(active, "SourceIp", None)
    _set_null_field(staged, "DestinationPort", "auto")
    _set_null_field(active, "DestinationPort", 0)
    _set_null_field(staged, "MulticastIp", None)
    _set_null_field(active, "MulticastIp", None)
    cs = _get_constraint_set(constraints)
    _add_enum_constraint(cs, "Enabled", [leg.enable], context=staged)
    _add_unconstrained(cs, "SourceIp", staged)
    _add_unconstrained(cs, "DestinationPort", staged)
    _add_unconstrained(cs, "MulticastIp", staged)
    _set_constraint_set(constraints, cs)


def _init_srt_receiver_extra(
    staged: Any, active: Any, constraints: Any,
    leg: Leg, activation: Activation, format_enum: Any = None, **extra: Any,
) -> None:
    """SRT receiver: DestinationIp/Port (binding), SourceIp/Port, Protocol, Latency."""
    idx = activation.receiver_index
    _set_null_field(staged, "DestinationPort", 37500 + 2 * idx)
    _set_null_field(active, "DestinationPort", 37500 + 2 * idx)
    _set_null_field(staged, "SourceIp", None)
    _set_null_field(active, "SourceIp", None)
    _set_null_field(staged, "SourcePort", "auto")
    _set_null_field(active, "SourcePort", 0)
    # SRT receiver protocol: Caller (enums.Caller = "caller")
    from nmos.enums import Caller
    _try_set_enum(staged, "Protocol", Caller.s)
    _try_set_enum(active, "Protocol", Caller.s)
    _set_null_field(staged, "Latency", 0)
    _set_null_field(active, "Latency", 0)
    _set_null_field(staged, "StreamId", None)
    _set_null_field(active, "StreamId", None)
    cs = _get_constraint_set(constraints)
    _add_static_constraint_from_staged(cs, "DestinationIp", staged)
    _add_static_constraint_from_staged(cs, "DestinationPort", staged)
    _add_unconstrained(cs, "SourceIp", staged)
    _add_unconstrained(cs, "SourcePort", staged)
    _add_unconstrained(cs, "Latency", staged)
    _add_enum_constraint(cs, "StreamId", [None], "StreamID not supported", staged)
    _set_constraint_set(constraints, cs)


def _init_rtp_tcp_receiver_extra(
    staged: Any, active: Any, constraints: Any,
    leg: Leg, activation: Activation, format_enum: Any = None, **extra: Any,
) -> None:
    """RTP-TCP receiver: SourceIp, SourcePort, RtpEnabled, RtcpEnabled."""
    _set_field(staged, "RtpEnabled", leg.enable)
    _set_field(active, "RtpEnabled", leg.enable)
    _set_field(staged, "RtcpEnabled", leg.enable)
    _set_field(active, "RtcpEnabled", leg.enable)
    _set_null_field(staged, "SourceIp", None)
    _set_null_field(active, "SourceIp", None)
    _set_null_field(staged, "SourcePort", 27500)
    _set_null_field(active, "SourcePort", 27500)
    _set_null_field(staged, "RtcpSourcePort", 27501)
    _set_null_field(active, "RtcpSourcePort", 27501)
    cs = _get_constraint_set(constraints)
    _add_enum_constraint(cs, "RtpEnabled", [leg.enable], context=staged)
    _add_enum_constraint(cs, "RtcpEnabled", [leg.enable], context=staged)
    _add_unconstrained(cs, "SourceIp", staged)
    _add_unconstrained(cs, "SourcePort", staged)
    _add_unconstrained(cs, "RtcpSourcePort", staged)
    _set_constraint_set(constraints, cs)


def _init_rtsp_receiver_extra(
    staged: Any, active: Any, constraints: Any,
    leg: Leg, activation: Activation, format_enum: Any = None, **extra: Any,
) -> None:
    """RTSP receiver: SourceIp, SourcePort."""
    _set_null_field(staged, "SourceIp", None)
    _set_null_field(active, "SourceIp", None)
    _set_null_field(staged, "SourcePort", 27500)
    _set_null_field(active, "SourcePort", 27500)
    cs = _get_constraint_set(constraints)
    _add_unconstrained(cs, "SourceIp", staged)
    _add_unconstrained(cs, "SourcePort", staged)
    _set_constraint_set(constraints, cs)


def _init_usb_receiver_extra(
    staged: Any, active: Any, constraints: Any,
    leg: Leg, activation: Activation, format_enum: Any = None, **extra: Any,
) -> None:
    """USB receiver: SourceIp, SourcePort, always-initialized layer mappings."""
    _set_null_field(staged, "SourceIp", None)
    _set_null_field(active, "SourceIp", None)
    _set_null_field(staged, "SourcePort", 27500)
    _set_null_field(active, "SourcePort", 27500)
    # USB always initializes layer mappings (unlike RTP/UDP which are conditional)
    _set_field(staged, "ExtAudioLayersMapping", "")
    _set_field(active, "ExtAudioLayersMapping", "")
    _set_field(staged, "ExtVideoLayersMapping", "")
    _set_field(active, "ExtVideoLayersMapping", "")
    _set_field(staged, "ExtDataLayersMapping", "")
    _set_field(active, "ExtDataLayersMapping", "")
    cs = _get_constraint_set(constraints)
    _add_unconstrained(cs, "SourceIp", staged)
    _add_unconstrained(cs, "SourcePort", staged)
    _set_constraint_set(constraints, cs)


def _init_ndi_receiver_extra(
    staged: Any, active: Any, constraints: Any,
    leg: Leg, activation: Activation, format_enum: Any = None, **extra: Any,
) -> None:
    """NDI receiver: SourceIp, SourcePort (fixed 5960), SourceName, MachineName."""
    group_hint = extra.get("group_hint", "")
    source_name = group_hint.replace(" ", "").replace(":", "_") if group_hint else None
    _set_null_field(staged, "SourceIp", None)
    _set_null_field(active, "SourceIp", None)
    _set_null_field(staged, "SourcePort", 5960)
    _set_null_field(active, "SourcePort", 5960)
    _set_null_field(staged, "SourceName", source_name)
    _set_null_field(active, "SourceName", source_name)
    _set_null_field(staged, "MachineName", None)
    _set_null_field(active, "MachineName", None)
    cs = _get_constraint_set(constraints)
    _add_static_constraint_from_staged(cs, "SourcePort", staged)
    _add_unconstrained(cs, "SourceIp", staged)
    _add_unconstrained(cs, "MachineName", staged)
    if source_name:
        _add_enum_constraint(cs, "SourceName", [source_name], context=staged)
    _set_constraint_set(constraints, cs)


def _init_websocket_receiver_extra(
    staged: Any, active: Any, constraints: Any,
    leg: Leg, activation: Activation, format_enum: Any = None, **extra: Any,
) -> None:
    """WebSocket receiver: ConnectionUri, ConnectionAuthorization."""
    _set_null_field(staged, "ConnectionUri", None)
    _set_null_field(active, "ConnectionUri", None)
    _set_field(staged, "ConnectionAuthorization", False)
    _set_field(active, "ConnectionAuthorization", False)
    cs = _get_constraint_set(constraints)
    _add_unconstrained(cs, "ConnectionUri", staged)
    _add_static_constraint_from_staged(cs, "ConnectionAuthorization", staged)
    _set_constraint_set(constraints, cs)


# ---------------------------------------------------------------------------
# Generic privacy init (replaces 9 per-transport functions)
# ---------------------------------------------------------------------------

def init_sender_privacy(
    privacy: Privacy,
    privacy_keys: PrivacyPreSharedKeys,
    staged: Any, active: Any, constraints: Any,
    protocol_enum: Any, kv_protocol_enum: Any,
) -> None:
    """Initialize privacy params on sender transport params.

    Single function replacing InitRtpSenderPrivacy, InitUdpSenderPrivacy, etc.
    """
    if privacy_keys.keys:
        privacy.key_id = privacy_keys.keys[0].key_id
        privacy.psk = privacy_keys.keys[0].psk

    import nmos.enums as _enums
    _set_field(staged, "ExtPrivacyProtocol", protocol_enum)
    _set_field(staged, "ExtPrivacyMode", _enums.AES128CTR)
    _set_field(staged, "ExtPrivacyIV", hex_from_bytes(privacy.iv))
    _set_field(staged, "ExtPrivacyKeyGenerator", hex_from_bytes(privacy.key_generator))
    _set_field(staged, "ExtPrivacyKeyVersion", hex_from_bytes(privacy.key_version))
    _set_field(staged, "ExtPrivacyKeyId", hex_from_bytes(privacy.key_id))
    _set_field(staged, "ExtPrivacyEcdhReceiverPublicKey", "00")
    _set_field(staged, "ExtPrivacyEcdhSenderPublicKey",
               hex_from_bytes(privacy.ecdh_sender_public_key) if privacy.ecdh_sender_public_key else "00")
    _set_field(staged, "ExtPrivacyEcdhCurve", _enums.Curve_secp256r1)

    # Copy staged → active
    for field_name in ("ExtPrivacyProtocol", "ExtPrivacyMode", "ExtPrivacyIV",
                       "ExtPrivacyKeyGenerator", "ExtPrivacyKeyVersion", "ExtPrivacyKeyId",
                       "ExtPrivacyEcdhReceiverPublicKey", "ExtPrivacyEcdhSenderPublicKey",
                       "ExtPrivacyEcdhCurve"):
        src = getattr(staged, field_name, None)
        dst = getattr(active, field_name, None)
        if src is not None and dst is not None and hasattr(src, 'defined') and src.defined:
            dst.value = src.value

    # Sender privacy constraints
    ecdh_send_key = hex_from_bytes(privacy.ecdh_sender_public_key) if privacy.ecdh_sender_public_key else "00"
    cs = _get_constraint_set(constraints)
    _add_enum_constraint(cs, "ExtPrivacyIV", [hex_from_bytes(privacy.iv)], "read-only", staged)
    _add_enum_constraint(cs, "ExtPrivacyKeyGenerator", [hex_from_bytes(privacy.key_generator)], "read-only", staged)
    _add_enum_constraint(cs, "ExtPrivacyKeyVersion", [hex_from_bytes(privacy.key_version)], "read-only", staged)
    _add_enum_constraint(cs, "ExtPrivacyKeyId", [hex_from_bytes(privacy.key_id)], "read-only", staged)
    protocol_values: list[Any] = []
    if protocol_enum is not None:
        protocol_values.append(str(protocol_enum))
    if kv_protocol_enum is not None:
        protocol_values.append(str(kv_protocol_enum))
    if protocol_values:
        _add_enum_constraint(cs, "ExtPrivacyProtocol", protocol_values, context=staged)
    else:
        _add_unconstrained(cs, "ExtPrivacyProtocol", staged)
    # Supported privacy modes
    _add_enum_constraint(cs, "ExtPrivacyMode", [
        str(_enums.AES128CTR), str(_enums.ECDH_AES128CTR),
        str(_enums.AES256CTR), str(_enums.ECDH_AES256CTR),
    ], context=staged)
    _add_enum_constraint(cs, "ExtPrivacyEcdhSenderPublicKey", [ecdh_send_key], "read-only", staged)
    _add_unconstrained(cs, "ExtPrivacyEcdhReceiverPublicKey", staged)
    # Lists UseTransportPrivacyEcdhCurve + secp256r1 (if different)
    # UseTransportPrivacyEcdhCurve = Curve_25519 → ["25519", "secp256r1"]
    curve_values: list[str] = [str(_enums.Curve_25519)]
    if _enums.Curve_25519 is not _enums.Curve_secp256r1:
        curve_values.append(str(_enums.Curve_secp256r1))
    _add_enum_constraint(cs, "ExtPrivacyEcdhCurve", curve_values, context=staged)
    _set_constraint_set(constraints, cs)


def init_receiver_privacy(
    privacy: Privacy,
    privacy_keys: PrivacyPreSharedKeys,
    staged: Any, active: Any, constraints: Any,
    protocol_enum: Any, kv_protocol_enum: Any,
) -> None:
    """Initialize privacy params on receiver transport params.

    Sets staged/active params AND constraints for all ext_privacy_* fields.
    """
    import nmos.enums as _enums

    if privacy_keys.keys:
        privacy.key_id = privacy_keys.keys[0].key_id
        privacy.psk = privacy_keys.keys[0].psk

    # Staged privacy params — ALL 9 fields
    # key_id MUST be one of the known PSK key IDs (constraint requires it)
    first_key_id = hex_from_bytes(privacy_keys.keys[0].key_id) if privacy_keys.keys and privacy_keys.keys[0].psk else "0000000000000000"
    _set_field(staged, "ExtPrivacyProtocol", protocol_enum)
    _set_field(staged, "ExtPrivacyMode", _enums.AES128CTR)
    _set_field(staged, "ExtPrivacyIV", "0000000000000000")
    _set_field(staged, "ExtPrivacyKeyGenerator", "00000000000000000000000000000000")
    _set_field(staged, "ExtPrivacyKeyVersion", "00000000")
    _set_field(staged, "ExtPrivacyKeyId", first_key_id)
    _set_field(staged, "ExtPrivacyEcdhSenderPublicKey", "00")
    ecdh_recv_key = hex_from_bytes(privacy.ecdh_receiver_public_key) if privacy.ecdh_receiver_public_key else "00"
    _set_field(staged, "ExtPrivacyEcdhReceiverPublicKey", ecdh_recv_key)
    _set_field(staged, "ExtPrivacyEcdhCurve", _enums.Curve_secp256r1)

    # Copy staged → active for all privacy fields
    for field_name in ("ExtPrivacyProtocol", "ExtPrivacyMode",
                       "ExtPrivacyIV", "ExtPrivacyKeyGenerator",
                       "ExtPrivacyKeyVersion", "ExtPrivacyKeyId",
                       "ExtPrivacyEcdhSenderPublicKey", "ExtPrivacyEcdhReceiverPublicKey",
                       "ExtPrivacyEcdhCurve"):
        src = getattr(staged, field_name, None)
        dst = getattr(active, field_name, None)
        if src is not None and dst is not None and hasattr(src, 'defined') and src.defined:
            dst.value = src.value

    # Privacy constraints
    cs = _get_constraint_set(constraints)
    _add_unconstrained(cs, "ExtPrivacyIV", staged)
    _add_unconstrained(cs, "ExtPrivacyKeyGenerator", staged)
    _add_unconstrained(cs, "ExtPrivacyKeyVersion", staged)
    # Key ID: constrained to known keys
    if privacy_keys.keys:
        key_ids = [hex_from_bytes(k.key_id) if k.key_id else "0000000000000000"
                   for k in privacy_keys.keys if k.psk]
        _add_enum_constraint(cs, "ExtPrivacyKeyId", key_ids, context=staged)
    else:
        _add_unconstrained(cs, "ExtPrivacyKeyId", staged)
    # Protocol: constrained to supported protocols
    protocol_values: list[Any] = []
    if protocol_enum is not None:
        protocol_values.append(str(protocol_enum))
    if kv_protocol_enum is not None:
        protocol_values.append(str(kv_protocol_enum))
    if protocol_values:
        _add_enum_constraint(cs, "ExtPrivacyProtocol", protocol_values, context=staged)
    else:
        _add_unconstrained(cs, "ExtPrivacyProtocol", staged)
    # Supported privacy modes
    _add_enum_constraint(cs, "ExtPrivacyMode", [
        str(_enums.AES128CTR), str(_enums.ECDH_AES128CTR),
        str(_enums.AES256CTR), str(_enums.ECDH_AES256CTR),
    ], context=staged)
    # ECDH receiver key: read-only
    _add_enum_constraint(cs, "ExtPrivacyEcdhReceiverPublicKey", [ecdh_recv_key],
                         "read-only", staged)
    _add_unconstrained(cs, "ExtPrivacyEcdhSenderPublicKey", staged)
    # Lists UseTransportPrivacyEcdhCurve + secp256r1 (if different)
    curve_values: list[str] = [str(_enums.Curve_25519)]
    if _enums.Curve_25519 is not _enums.Curve_secp256r1:
        curve_values.append(str(_enums.Curve_secp256r1))
    _add_enum_constraint(cs, "ExtPrivacyEcdhCurve", curve_values, context=staged)
    _set_constraint_set(constraints, cs)


# ---------------------------------------------------------------------------
# Registry builder
# ---------------------------------------------------------------------------

def _build_registry() -> dict[Any, TransportDescriptor]:
    """Build the full transport → descriptor mapping."""
    try:
        from nmos.enums import EnumRegistry
        import nmos.enums as enums
        from nmos.types.generated.nrtp_sender_transport_params import NRtpSenderTransportParamsValue
        from nmos.types.generated.nrtp_transport_constraints import NRtpTransportConstraintsValue
        from nmos.types.generated.nrtp_sender_activation import NRtpSenderActivationValue
        from nmos.types.generated.nrtp_receiver_transport_params import NRtpReceiverTransportParamsValue
        from nmos.types.generated.nrtp_receiver_activation import NRtpReceiverActivationValue
        from nmos.types.generated.nudp_sender_transport_params import NUdpSenderTransportParamsValue
        from nmos.types.generated.nudp_transport_constraints import NUdpTransportConstraintsValue
        from nmos.types.generated.nudp_sender_activation import NUdpSenderActivationValue
        from nmos.types.generated.nudp_receiver_transport_params import NUdpReceiverTransportParamsValue
        from nmos.types.generated.nudp_receiver_activation import NUdpReceiverActivationValue
        from nmos.types.generated.nsrt_sender_transport_params import NSrtSenderTransportParamsValue
        from nmos.types.generated.nsrt_transport_constraints import NSrtTransportConstraintsValue
        from nmos.types.generated.nsrt_sender_activation import NSrtSenderActivationValue
        from nmos.types.generated.nsrt_receiver_transport_params import NSrtReceiverTransportParamsValue
        from nmos.types.generated.nsrt_receiver_activation import NSrtReceiverActivationValue
        from nmos.types.generated.nrtp_tcp_sender_transport_params import NRtpTcpSenderTransportParamsValue
        from nmos.types.generated.nrtp_tcp_transport_constraints import NRtpTcpTransportConstraintsValue
        from nmos.types.generated.nrtp_tcp_sender_activation import NRtpTcpSenderActivationValue
        from nmos.types.generated.nrtp_tcp_receiver_transport_params import NRtpTcpReceiverTransportParamsValue
        from nmos.types.generated.nrtp_tcp_receiver_activation import NRtpTcpReceiverActivationValue
        from nmos.types.generated.nrtsp_sender_transport_params import NRtspSenderTransportParamsValue
        from nmos.types.generated.nrtsp_transport_constraints import NRtspTransportConstraintsValue
        from nmos.types.generated.nrtsp_sender_activation import NRtspSenderActivationValue
        from nmos.types.generated.nrtsp_receiver_transport_params import NRtspReceiverTransportParamsValue
        from nmos.types.generated.nrtsp_receiver_activation import NRtspReceiverActivationValue
        from nmos.types.generated.nusb_sender_transport_params import NUsbSenderTransportParamsValue
        from nmos.types.generated.nusb_transport_constraints import NUsbTransportConstraintsValue
        from nmos.types.generated.nusb_sender_activation import NUsbSenderActivationValue
        from nmos.types.generated.nusb_receiver_transport_params import NUsbReceiverTransportParamsValue
        from nmos.types.generated.nusb_receiver_activation import NUsbReceiverActivationValue
        from nmos.types.generated.nndi_sender_transport_params import NNdiSenderTransportParamsValue
        from nmos.types.generated.nndi_transport_constraints import NNdiTransportConstraintsValue
        from nmos.types.generated.nndi_sender_activation import NNdiSenderActivationValue
        from nmos.types.generated.nndi_receiver_transport_params import NNdiReceiverTransportParamsValue
        from nmos.types.generated.nndi_receiver_activation import NNdiReceiverActivationValue
        from nmos.types.generated.nmqtt_sender_transport_params import NMqttSenderTransportParamsValue
        from nmos.types.generated.nmqtt_transport_constraints import NMqttTransportConstraintsValue
        from nmos.types.generated.nmqtt_sender_activation import NMqttSenderActivationValue
        from nmos.types.generated.nweb_socket_sender_transport_params import NWebSocketSenderTransportParamsValue
        from nmos.types.generated.nweb_socket_transport_constraints import NWebSocketTransportConstraintsValue
        from nmos.types.generated.nweb_socket_sender_activation import NWebSocketSenderActivationValue
        from nmos.types.generated.nweb_socket_receiver_transport_params import NWebSocketReceiverTransportParamsValue
        from nmos.types.generated.nweb_socket_receiver_activation import NWebSocketReceiverActivationValue
    except ImportError:
        return {}

    _get = EnumRegistry.get

    # Import transport-specific auto-resolvers for flip Step 2
    from nmos.node.activation_engine import (
        resolve_rtp_sender, resolve_rtp_receiver,
        resolve_udp_sender, resolve_udp_receiver,
        resolve_srt_sender, resolve_srt_receiver,
        resolve_noop,
    )

    # Descriptors — each wires its own flip resolver via sender/receiver_auto_resolvers.
    # Transports that have no auto-resolution use resolve_noop (RTSP, USB, NDI, MQTT,
    # WebSocket, RTP-TCP). Each descriptor encapsulates per-transport flip activation.
    rtp_desc = TransportDescriptor(
        sender_params_type=NRtpSenderTransportParamsValue,
        sender_constraints_type=NRtpTransportConstraintsValue,
        sender_activation_type=NRtpSenderActivationValue,
        receiver_params_type=NRtpReceiverTransportParamsValue,
        receiver_constraints_type=NRtpTransportConstraintsValue,
        receiver_activation_type=NRtpReceiverActivationValue,
        has_privacy=True,
        privacy_protocol=enums.RTP,
        privacy_kv_protocol=enums.RTP_KV,
        sender_port_fn=lambda i: 27500 + 2 * i,
        init_sender_extra=_init_rtp_sender_extra,
        init_receiver_extra=_init_rtp_receiver_extra,
        sender_auto_resolvers={"flip_resolve": resolve_rtp_sender},
        receiver_auto_resolvers={"flip_resolve": resolve_rtp_receiver},
        has_rtcp=True, has_fec=True,
    )

    udp_desc = TransportDescriptor(
        sender_params_type=NUdpSenderTransportParamsValue,
        sender_constraints_type=NUdpTransportConstraintsValue,
        sender_activation_type=NUdpSenderActivationValue,
        receiver_params_type=NUdpReceiverTransportParamsValue,
        receiver_constraints_type=NUdpTransportConstraintsValue,
        receiver_activation_type=NUdpReceiverActivationValue,
        has_privacy=True,
        privacy_protocol=enums.UDP,
        privacy_kv_protocol=enums.UDP_KV,
        sender_port_fn=lambda i: 27500 + i,
        init_sender_extra=_init_udp_sender_extra,
        init_receiver_extra=_init_udp_receiver_extra,
        sender_auto_resolvers={"flip_resolve": resolve_udp_sender},
        receiver_auto_resolvers={"flip_resolve": resolve_udp_receiver},
        has_fec=True,
    )

    srt_desc = TransportDescriptor(
        sender_params_type=NSrtSenderTransportParamsValue,
        sender_constraints_type=NSrtTransportConstraintsValue,
        sender_activation_type=NSrtSenderActivationValue,
        receiver_params_type=NSrtReceiverTransportParamsValue,
        receiver_constraints_type=NSrtTransportConstraintsValue,
        receiver_activation_type=NSrtReceiverActivationValue,
        has_privacy=True,
        privacy_protocol=enums.SRT,
        sender_port_fn=lambda i: 27500 + 2 * i,
        receiver_port_fn=lambda i: 37500 + 2 * i,
        init_sender_extra=_init_srt_sender_extra,
        init_receiver_extra=_init_srt_receiver_extra,
        sender_auto_resolvers={"flip_resolve": resolve_srt_sender},
        receiver_auto_resolvers={"flip_resolve": resolve_srt_receiver},
    )

    rtp_tcp_desc = TransportDescriptor(
        sender_params_type=NRtpTcpSenderTransportParamsValue,
        sender_constraints_type=NRtpTcpTransportConstraintsValue,
        sender_activation_type=NRtpTcpSenderActivationValue,
        receiver_params_type=NRtpTcpReceiverTransportParamsValue,
        receiver_constraints_type=NRtpTcpTransportConstraintsValue,
        receiver_activation_type=NRtpTcpReceiverActivationValue,
        has_privacy=True,
        privacy_protocol=enums.RTP,
        privacy_kv_protocol=enums.RTP_KV,
        sender_port_fn=lambda i: 27500 + 2 * i,
        init_sender_extra=_init_rtp_tcp_sender_extra,
        init_receiver_extra=_init_rtp_tcp_receiver_extra,
        sender_auto_resolvers={"flip_resolve": resolve_noop},
        receiver_auto_resolvers={"flip_resolve": resolve_noop},
        has_rtcp=True,
    )

    rtsp_desc = TransportDescriptor(
        sender_params_type=NRtspSenderTransportParamsValue,
        sender_constraints_type=NRtspTransportConstraintsValue,
        sender_activation_type=NRtspSenderActivationValue,
        receiver_params_type=NRtspReceiverTransportParamsValue,
        receiver_constraints_type=NRtspTransportConstraintsValue,
        receiver_activation_type=NRtspReceiverActivationValue,
        has_privacy=True,
        privacy_protocol=enums.RTSP,
        sender_port_fn=lambda i: 27500 + i,
        init_sender_extra=_init_rtsp_sender_extra,
        init_receiver_extra=_init_rtsp_receiver_extra,
        sender_auto_resolvers={"flip_resolve": resolve_noop},
        receiver_auto_resolvers={"flip_resolve": resolve_noop},
    )

    usb_desc = TransportDescriptor(
        sender_params_type=NUsbSenderTransportParamsValue,
        sender_constraints_type=NUsbTransportConstraintsValue,
        sender_activation_type=NUsbSenderActivationValue,
        receiver_params_type=NUsbReceiverTransportParamsValue,
        receiver_constraints_type=NUsbTransportConstraintsValue,
        receiver_activation_type=NUsbReceiverActivationValue,
        has_privacy=True,
        privacy_protocol=enums.USB,
        sender_port_fn=lambda i: 27500 + i,
        init_sender_extra=_init_usb_sender_extra,
        init_receiver_extra=_init_usb_receiver_extra,
        sender_auto_resolvers={"flip_resolve": resolve_noop},
        receiver_auto_resolvers={"flip_resolve": resolve_noop},
    )

    ndi_desc = TransportDescriptor(
        sender_params_type=NNdiSenderTransportParamsValue,
        sender_constraints_type=NNdiTransportConstraintsValue,
        sender_activation_type=NNdiSenderActivationValue,
        receiver_params_type=NNdiReceiverTransportParamsValue,
        receiver_constraints_type=NNdiTransportConstraintsValue,
        receiver_activation_type=NNdiReceiverActivationValue,
        has_privacy=False,
        sender_port_fn=lambda i: 5960,
        receiver_port_fn=lambda i: 5960,
        init_sender_extra=_init_ndi_sender_extra,
        init_receiver_extra=_init_ndi_receiver_extra,
        sender_auto_resolvers={"flip_resolve": resolve_noop},
        receiver_auto_resolvers={"flip_resolve": resolve_noop},
        has_sdp=False,
    )

    mqtt_desc = TransportDescriptor(
        sender_params_type=NMqttSenderTransportParamsValue,
        sender_constraints_type=NMqttTransportConstraintsValue,
        sender_activation_type=NMqttSenderActivationValue,
        receiver_params_type=None,  # MQTT receiver not supported
        receiver_constraints_type=None,
        receiver_activation_type=None,
        has_privacy=False,
        init_sender_extra=_init_mqtt_sender_extra,
        sender_auto_resolvers={"flip_resolve": resolve_noop},
    )

    websocket_desc = TransportDescriptor(
        sender_params_type=NWebSocketSenderTransportParamsValue,
        sender_constraints_type=NWebSocketTransportConstraintsValue,
        sender_activation_type=NWebSocketSenderActivationValue,
        receiver_params_type=NWebSocketReceiverTransportParamsValue,
        receiver_constraints_type=NWebSocketTransportConstraintsValue,
        receiver_activation_type=NWebSocketReceiverActivationValue,
        has_privacy=False,
        sender_port_fn=lambda i: 27500 + i,
        receiver_port_fn=lambda i: 27500 + i,
        init_sender_extra=_init_websocket_sender_extra,
        init_receiver_extra=_init_websocket_receiver_extra,
        sender_auto_resolvers={"flip_resolve": resolve_noop},
        receiver_auto_resolvers={"flip_resolve": resolve_noop},
    )

    # Map transport enum values → descriptors.
    # URNs MUST match the actual enum values defined in nmos/enums/__init__.py.
    # Standard NMOS transports use urn:x-nmos:; Matrox extensions use urn:x-matrox:.
    registry: dict[Any, TransportDescriptor] = {}
    _map = [
        (["urn:x-nmos:transport:rtp", "urn:x-nmos:transport:rtp.ucast", "urn:x-nmos:transport:rtp.mcast"], rtp_desc),
        (["urn:x-matrox:transport:udp", "urn:x-matrox:transport:udp.ucast", "urn:x-matrox:transport:udp.mcast",
          "urn:x-matrox:transport:udp.mp2t", "urn:x-matrox:transport:udp.mp2t.ucast", "urn:x-matrox:transport:udp.mp2t.mcast"], udp_desc),
        (["urn:x-matrox:transport:srt", "urn:x-matrox:transport:srt.mp2t", "urn:x-matrox:transport:srt.rtp"], srt_desc),
        (["urn:x-matrox:transport:rtp.tcp"], rtp_tcp_desc),
        (["urn:x-matrox:transport:rtsp", "urn:x-matrox:transport:rtsp.tcp"], rtsp_desc),
        (["urn:x-nmos:transport:usb"], usb_desc),
        (["urn:x-matrox:transport:ndi"], ndi_desc),
        (["urn:x-nmos:transport:mqtt"], mqtt_desc),
        (["urn:x-nmos:transport:websocket"], websocket_desc),
    ]

    for urns, desc in _map:
        for urn in urns:
            enum = _get(urn)
            if enum is not None:
                registry[enum] = desc

    return registry


# ---------------------------------------------------------------------------
# Field setter helpers
# ---------------------------------------------------------------------------

def _set_field(obj: Any, name: str, value: Any) -> None:
    """Set a field on a transport param object (NString, NBool, NEnum).

    Safely checks for the field's existence without triggering property
    getters that raise NotAvailable on undefined values.
    """
    field = getattr(obj, name, None)
    if field is None:
        return
    # Check it has a value setter (all NType wrappers do via @value.setter)
    field_type = type(field)
    if hasattr(field_type, 'value') and isinstance(
        getattr(field_type, 'value', None), property
    ):
        field.value = value


def _set_null_field(obj: Any, name: str, value: Any) -> None:
    """Set a field that may be NNull (accepts int, str, None, 'auto')."""
    _set_field(obj, name, value)


def _try_set_enum(obj: Any, name: str, urn: str) -> None:
    """Try to set an enum field by URN string."""
    field = getattr(obj, name, None)
    if field is None:
        return
    try:
        from nmos.enums import EnumRegistry
        enum = EnumRegistry.get(urn)
        if enum is not None:
            field.value = enum
    except (ImportError, AttributeError):
        pass


def _get_constraint_set(constraints: Any) -> dict[Any, Any]:
    """Get or create the constraint set dict.

    NRtpTransportConstraintsValue.Constraints is NTransportConstraintsValue
    (a map wrapper with ._inner dict), NOT NTransportConstraints (which has
    .defined/.value). We access ._inner directly.
    """
    if hasattr(constraints, 'Constraints'):
        return cast(dict[Any, Any], constraints.Constraints._inner)
    return {}


def _set_constraint_set(constraints: Any, cs: dict[Any, Any]) -> None:
    """Commit the constraint set dict."""
    if hasattr(constraints, 'Constraints'):
        constraints.Constraints._inner = cs


def _constraint_json_key(name: str) -> str:
    """Convert transport field names to NMOS JSON keys.

    Examples:
      SourceIp -> source_ip
      RtcpDestinationPort -> rtcp_destination_port
      ext_privacy_key_id -> ext_privacy_key_id
    """
    if "_" in name:
        return name.lower()
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    return s2.lower()


def _constraint_json_key_from_generated_type(context: Any, name: str) -> str | None:
    """Resolve json_key from generated *Enums class for a transport params type."""
    try:
        module_name = type(context).__module__
        module = __import__(module_name, fromlist=["*"])
        type_name = type(context).__name__
        if not type_name.endswith("Value"):
            return None
        enums_name = f"{type_name[:-5]}Enums"
        enums_cls = getattr(module, enums_name, None)
        if enums_cls is None:
            return None
        enum_id = getattr(enums_cls, name, None)
        if enum_id is None:
            return None
        return enum_id.s if hasattr(enum_id, "s") else str(enum_id)
    except (ImportError, AttributeError, TypeError):
        return None


def _constraint_enum_key(name: str, context: Any | None = None) -> Any:
    """Resolve EnumId for a constraint key name."""
    from nmos.enums import EnumRegistry

    json_key = None
    if context is not None:
        json_key = _constraint_json_key_from_generated_type(context, name)
    if not json_key:
        json_key = _constraint_json_key(name)
    key = EnumRegistry.lookup(json_key, auto=False)
    if key is not None:
        return key
    # Fallback keeps behavior robust if enum is missing from pre-registered set.
    return EnumRegistry.get(json_key)


def _add_unconstrained(cs: dict[Any, Any], name: str, context: Any | None = None) -> None:
    """Add an unconstrained entry (empty constraint value)."""
    try:
        key = _constraint_enum_key(name, context)
        if key is not None:
            from nmos.types.generated.ntransport_constraint import NTransportConstraint
            cs[key] = NTransportConstraint()
            cs[key]._defined = True
    except ImportError:
        pass


def _add_enum_constraint(cs: dict[Any, Any], name: str, values: list[Any],
                         description: str = "", context: Any | None = None) -> None:
    """Add a constraint with an enum list of allowed values."""
    try:
        key = _constraint_enum_key(name, context)
        if key is not None:
            from nmos.types.generated.ntransport_constraint import NTransportConstraint
            c = NTransportConstraint()
            c.set_to_default()
            c.get_Enum().value = values
            if description:
                c.get_Description().value = description
            cs[key] = c
    except ImportError:
        pass


def _add_static_constraint_from_staged(cs: dict[Any, Any], name: str, staged: Any) -> None:
    """Add a constraint that locks a field to its staged value."""
    field = getattr(staged, name, None)
    if field is not None and hasattr(field, 'defined') and field.defined:
        _add_enum_constraint(cs, name, [field.value], "cannot change", staged)


def _set_static_constraint(constraints: Any, name: str, staged: Any) -> None:
    """Set a static constraint on the constraints object directly."""
    cs = _get_constraint_set(constraints)
    _add_static_constraint_from_staged(cs, name, staged)
    _set_constraint_set(constraints, cs)


class InvalidOperationError(Exception):
    """Transport operation not supported."""
    pass
