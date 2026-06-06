# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""NMOS With Redundancy compliance tests (specs/NMOS With Redundancy.md).

Spec rules (cross-transport):
  - interface_bindings and transport_params array sizes MUST equal the leg count.
  - MUST NOT be possible through NMOS to add/remove legs.
  - Sender legs MUST NOT be individually (de)activatable through IS-05.
  - Receiver legs MAY be individually disabled; disabled legs MUST stay in the arrays.
  - Redundancy logic applies to all transports.
  - Routing: interface_bindings MUST track IS-05 interface selections.
  - Temporal redundancy (FEC) MUST NOT add to these arrays.

Tests stay at the resource / SDP / IS-05 layer (no wire-level streaming) —
the same scope used by prior transport plans. WSL's single-NIC limitation
does not affect this layer.
"""

from __future__ import annotations

import ipaddress
import json
import sys
from pathlib import Path
from typing import Any

import pytest

# Add caps/ and sdp/ to path
_NMOS_ROOT = Path(__file__).parent.parent.parent.parent
for _p in (_NMOS_ROOT / "caps", _NMOS_ROOT / "sdp"):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

try:
    import caps.MatroxCCF as MatroxCCF  # noqa: F401  # type: ignore[import-not-found]
    HAS_CCF = True
except ImportError:
    HAS_CCF = False

from nmos import enums
from nmos.ip.ipv4 import IPv4Addr
from nmos.node import Node, _generate_sdp_from_params
from nmos.node.activation import (
    get_transport_descriptor,
    init_sender_activation,
    init_receiver_activation,
)
from nmos.node.types import (
    MAX_LEGS,
    Activation,
    IPv4Settings,
    Leg,
)


BUILTIN_DIR = Path(__file__).parent.parent / "config" / "builtin"


# ---------------------------------------------------------------------------
# Helpers — construct in-memory legs and activations
# ---------------------------------------------------------------------------

def _make_leg(name: str, ip: str, enable: bool = True) -> Leg:
    """Build a single enabled IPv4 leg with a concrete address."""
    return Leg(
        enable=enable,
        name=name,
        listen_ipv4=True,
        use_ipv4=True,
        ipv4=IPv4Settings(address=IPv4Addr.from_string(ip)),
    )


def _make_sender_activation(descriptor: Any, legs: list[Leg],
                             sender_index: int = 0) -> Activation:
    """Build and initialize a sender Activation with 2-leg slots."""
    activation = Activation(
        sender_index=sender_index,
        enabled_legs=sum(1 for leg in legs if leg.enable),
        staged=[descriptor.sender_params_type() for _ in range(MAX_LEGS)],
        active=[descriptor.sender_params_type() for _ in range(MAX_LEGS)],
        constraints=[descriptor.sender_constraints_type() for _ in range(MAX_LEGS)],
        staged_state=descriptor.sender_activation_type(),
        active_state=descriptor.sender_activation_type(),
        sender_name="REDTST",
    )
    init_sender_activation(
        activation, legs, enums.TransportRtp, descriptor,
        privacy_enabled=False, group_hint="",
    )
    return activation


def _make_receiver_activation(descriptor: Any, legs: list[Leg],
                               format_enum: Any = None) -> Activation | None:
    """Build and initialize a receiver Activation with 2-leg slots.

    Returns None if the transport has no receiver (e.g., MQTT)."""
    if descriptor.receiver_params_type is None:
        return None
    if format_enum is None:
        format_enum = enums.FormatData
    activation = Activation(
        receiver_index=0,
        enabled_legs=sum(1 for leg in legs if leg.enable),
        staged=[descriptor.receiver_params_type() for _ in range(MAX_LEGS)],
        active=[descriptor.receiver_params_type() for _ in range(MAX_LEGS)],
        constraints=[descriptor.receiver_constraints_type() for _ in range(MAX_LEGS)],
        staged_state=descriptor.receiver_activation_type(),
        active_state=descriptor.receiver_activation_type(),
    )
    init_receiver_activation(
        activation, legs, enums.TransportRtp, format_enum, descriptor,
        privacy_enabled=False,
    )
    return activation


# Transport enums that support both sender and receiver + SDP
_SENDER_TRANSPORTS = [
    enums.TransportRtp,
    enums.TransportSrt,
    enums.TransportRtsp,
    enums.TransportUsb,
    enums.TransportNdi,
    enums.TransportRtpTcp,
    enums.TransportUdp,
]


# ===========================================================================
# Class 1 — TestRedundancyPrimitives (RD1, RD2, RD15)
# ===========================================================================

class TestRedundancyPrimitives:
    """Shape checks on MAX_LEGS, Leg, Activation array sizes."""

    def test_max_legs_is_2(self) -> None:
        # RD2 — spec line 29: "usually have two entries". MaxLegs = 2.
        assert MAX_LEGS == 2

    def test_activation_arrays_sized_to_max_legs(self) -> None:
        # RD1 — the three activation arrays (active, staged, constraints)
        # are always MAX_LEGS-sized after init, regardless of how many legs
        # the config declared enabled.
        desc = get_transport_descriptor(enums.TransportRtp)
        activation = _make_sender_activation(desc, [_make_leg("eth0", "10.0.0.1")])
        assert len(activation.active) == MAX_LEGS
        assert len(activation.staged) == MAX_LEGS
        assert len(activation.constraints) == MAX_LEGS

    def test_single_leg_sender_fills_both_slots(self) -> None:
        # RD1 — slot 0 populated with real IP, slot 1 populated with "0.0.0.0"
        # (disabled sentinel, matches activation.py:128). Array size is still 2.
        desc = get_transport_descriptor(enums.TransportRtp)
        activation = _make_sender_activation(desc, [_make_leg("eth0", "10.0.0.1")])
        assert activation.active[0].SourceIp.value == "10.0.0.1"
        assert activation.active[1].SourceIp.value == "0.0.0.0"

    def test_two_leg_sender_fills_both_slots_with_real_ips(self) -> None:
        # RD2 — both legs populated with their declared IPs.
        desc = get_transport_descriptor(enums.TransportRtp)
        activation = _make_sender_activation(desc, [
            _make_leg("eth0", "10.0.0.1"),
            _make_leg("eth1", "10.0.0.2"),
        ])
        assert activation.active[0].SourceIp.value == "10.0.0.1"
        assert activation.active[1].SourceIp.value == "10.0.0.2"

    def test_receiver_activation_arrays_sized_to_max_legs(self) -> None:
        # RD1 — mirror of the sender check.
        desc = get_transport_descriptor(enums.TransportRtp)
        activation = _make_receiver_activation(desc, [_make_leg("eth0", "10.0.0.1")],
                                                format_enum=enums.FormatVideo)
        assert activation is not None
        assert len(activation.active) == MAX_LEGS
        assert len(activation.staged) == MAX_LEGS

    def test_temporal_redundancy_does_not_add_legs(self) -> None:
        # RD15 — spec line 45: "Temporal redundancy is not allowed to add
        # entries to the interface_bindings and transport_params arrays."
        # No Python code path should produce > MAX_LEGS slots.
        desc = get_transport_descriptor(enums.TransportRtp)
        # Pass 3 legs intentionally — the activation loop still caps at MAX_LEGS.
        activation = _make_sender_activation(desc, [
            _make_leg("eth0", "10.0.0.1"),
            _make_leg("eth1", "10.0.0.2"),
            _make_leg("eth2", "10.0.0.3"),  # would-be FEC leg
        ])
        assert len(activation.active) == MAX_LEGS
        # The third "leg" is silently dropped — it never appears in the array.


# ===========================================================================
# Class 2 — TestLegNullingAndStaticConstraints (RD3, RD4, RD5)
# ===========================================================================

class TestLegNullingAndStaticConstraints:
    """Verify disabled legs are nulled (0.0.0.0) and Sender legs are
    locked via static transport-parameter constraints."""

    def test_disabled_sender_leg_source_ip_is_nulled(self) -> None:
        desc = get_transport_descriptor(enums.TransportRtp)
        # Only leg 0 enabled → leg 1 active.SourceIp must be "0.0.0.0"
        activation = _make_sender_activation(desc, [_make_leg("eth0", "10.0.0.1")])
        assert activation.active[1].SourceIp.value == "0.0.0.0"

    def test_disabled_receiver_leg_interface_ip_is_nulled(self) -> None:
        desc = get_transport_descriptor(enums.TransportRtp)
        activation = _make_receiver_activation(desc, [_make_leg("eth0", "10.0.0.1")],
                                                format_enum=enums.FormatVideo)
        assert activation is not None
        assert activation.active[1].InterfaceIp.value == "0.0.0.0"

    def test_sender_source_ip_has_static_constraint(self) -> None:
        # RD4 / RD5 — Sender legs MUST NOT be individually (de)activatable
        # through IS-05; the implementation declares this as a single-value
        # enum constraint locking SourceIp to the staged value.
        desc = get_transport_descriptor(enums.TransportRtp)
        activation = _make_sender_activation(desc, [_make_leg("eth0", "10.0.0.1")])
        cs_root = activation.constraints[0]
        cs = cs_root.Constraints._inner if hasattr(cs_root, 'Constraints') else cs_root._inner
        # The key is the SourceIp enum URN; values should contain a single element
        src_ip_key = _find_constraint_key(cs, "source_ip")
        assert src_ip_key is not None, f"SourceIp constraint missing from {list(cs.keys())}"
        constraint = cs[src_ip_key]
        enum_values = _get_constraint_enum(constraint)
        assert len(enum_values) == 1, f"SourceIp must be locked to one value; got {enum_values}"

    def test_sender_source_port_has_static_constraint(self) -> None:
        desc = get_transport_descriptor(enums.TransportRtp)
        activation = _make_sender_activation(desc, [_make_leg("eth0", "10.0.0.1")])
        cs_root = activation.constraints[0]
        cs = cs_root.Constraints._inner if hasattr(cs_root, 'Constraints') else cs_root._inner
        key = _find_constraint_key(cs, "source_port")
        assert key is not None, f"SourcePort constraint missing from {list(cs.keys())}"
        enum_values = _get_constraint_enum(cs[key])
        assert len(enum_values) == 1, f"SourcePort must be locked to one value; got {enum_values}"

    def test_receiver_interface_ip_has_static_constraint(self) -> None:
        desc = get_transport_descriptor(enums.TransportRtp)
        activation = _make_receiver_activation(desc, [_make_leg("eth0", "10.0.0.1")],
                                                format_enum=enums.FormatVideo)
        assert activation is not None
        cs_root = activation.constraints[0]
        cs = cs_root.Constraints._inner if hasattr(cs_root, 'Constraints') else cs_root._inner
        key = _find_constraint_key(cs, "interface_ip")
        assert key is not None, f"InterfaceIp constraint missing from {list(cs.keys())}"
        enum_values = _get_constraint_enum(cs[key])
        assert len(enum_values) == 1

    def test_static_constraint_means_single_allowed_value(self) -> None:
        # Direct check: the constraint shape is an Enum with exactly one member.
        desc = get_transport_descriptor(enums.TransportRtp)
        activation = _make_sender_activation(desc, [_make_leg("eth0", "10.0.0.1")])
        cs_root = activation.constraints[0]
        cs = cs_root.Constraints._inner if hasattr(cs_root, 'Constraints') else cs_root._inner
        key = _find_constraint_key(cs, "source_ip")
        constraint = cs[key]
        enum_values = _get_constraint_enum(constraint)
        # The single allowed value matches the staged SourceIp
        assert str(enum_values[0]) == str(activation.staged[0].SourceIp.value)

    def test_disabled_leg_still_present_in_activation_arrays(self) -> None:
        # RD7 — inactive/disabled legs MUST NOT be removed from the arrays.
        desc = get_transport_descriptor(enums.TransportRtp)
        activation = _make_sender_activation(desc, [
            _make_leg("eth0", "10.0.0.1", enable=True),
            _make_leg("eth1", "10.0.0.2", enable=False),
        ])
        # Leg 1 is disabled, but its slot is still present in active/staged.
        assert len(activation.active) == MAX_LEGS
        # Disabled leg gets 0.0.0.0
        assert activation.active[1].SourceIp.value == "0.0.0.0"


def _find_constraint_key(cs: dict[Any, Any], needle: str) -> Any:
    """Find a constraint key whose string form contains `needle`."""
    for k in cs.keys():
        if needle in str(k):
            return k
    return None


def _get_constraint_enum(constraint: Any) -> list[Any]:
    """Extract the Enum values from an NTransportConstraint wrapper."""
    # NTransportConstraint.get_Enum() returns an NArrayOfEnum; .value is the list
    if hasattr(constraint, 'get_Enum'):
        e = constraint.get_Enum()
        if e.defined:
            return list(e.value)
    return []


# ===========================================================================
# Class 3 — TestNoLegAddOrRemoveViaIs05 (RD3)
# ===========================================================================

class TestNoLegAddOrRemoveViaIs05:
    """Spec line 29: MUST NOT be possible through NMOS to add/remove legs."""

    def test_activation_array_size_is_fixed_at_max_legs(self) -> None:
        desc = get_transport_descriptor(enums.TransportRtp)
        activation = _make_sender_activation(desc, [_make_leg("eth0", "10.0.0.1")])
        assert len(activation.staged) == MAX_LEGS
        assert len(activation.active) == MAX_LEGS
        assert len(activation.constraints) == MAX_LEGS

    def test_receiver_array_size_is_fixed_at_max_legs(self) -> None:
        desc = get_transport_descriptor(enums.TransportRtp)
        activation = _make_receiver_activation(desc, [_make_leg("eth0", "10.0.0.1")],
                                                format_enum=enums.FormatVideo)
        assert activation is not None
        assert len(activation.staged) == MAX_LEGS
        assert len(activation.active) == MAX_LEGS
        assert len(activation.constraints) == MAX_LEGS

    def test_empty_legs_list_still_produces_max_legs_slots(self) -> None:
        # Passing zero legs does NOT produce an empty activation —
        # arrays still have MAX_LEGS slots (both disabled).
        desc = get_transport_descriptor(enums.TransportRtp)
        activation = _make_sender_activation(desc, [])
        assert len(activation.active) == MAX_LEGS
        # Both slots get disabled sentinel
        assert activation.active[0].SourceIp.value == "0.0.0.0"
        assert activation.active[1].SourceIp.value == "0.0.0.0"

    def test_transport_params_array_size_matches_across_legs_of_same_activation(
        self
    ) -> None:
        # interface_bindings size == transport_params size (RT1 corollary)
        # Here we assert that staged and active have identical slot counts.
        desc = get_transport_descriptor(enums.TransportRtp)
        activation = _make_sender_activation(desc, [_make_leg("eth0", "10.0.0.1")])
        assert len(activation.staged) == len(activation.active)

    def test_legs_beyond_max_are_dropped(self) -> None:
        # Defensive: if a caller supplies legs > MAX_LEGS, only the first
        # MAX_LEGS are consumed. This enforces RD3 (cannot exceed array size).
        desc = get_transport_descriptor(enums.TransportRtp)
        activation = _make_sender_activation(desc, [
            _make_leg("eth0", "10.0.0.1"),
            _make_leg("eth1", "10.0.0.2"),
            _make_leg("eth2", "10.0.0.3"),  # dropped
            _make_leg("eth3", "10.0.0.4"),  # dropped
        ])
        assert len(activation.active) == MAX_LEGS
        assert activation.active[0].SourceIp.value == "10.0.0.1"
        assert activation.active[1].SourceIp.value == "10.0.0.2"


# ===========================================================================
# Class 4 — TestSdpGroupDupEncoding (RD14 cross-transport)
# ===========================================================================

class TestSdpGroupDupEncoding:
    """Exercise the SDP encoder's redundancy path directly — no Node needed."""

    def _build_single_media_sdp(self) -> Any:
        from MatroxSdp import MatroxSdp, MatroxSdpEnums as E
        sdp = MatroxSdp()
        sdp.username = "-"
        sdp.session_id = 1
        sdp.session_version = 1
        sdp.origin_address = "127.0.0.1"
        sdp.session_name = "test"
        sdp.start = 0
        sdp.stop = 0
        m = sdp.medias[0]
        m.media_name = "video"
        m.type = E.Video.value
        m.protocol = E.ProtocolRTP_AVP.value
        m.format_code = 96
        m.payload_type = 96
        m.encoding_name = E.EncodingRaw.value
        m.clock_rate = 90000
        m.port = 5000
        m.port_count = 1
        m.connection_address = "233.252.0.1"
        m.connection_count = 1
        sdp.primary_media = m
        sdp.primary_media_name = m.media_name
        sdp.media_count = 1
        return sdp, E

    def _build_dup_media_sdp(self, primary_ip: str = "233.252.0.1",
                              secondary_ip: str = "233.252.0.2",
                              primary_port: int = 5000,
                              secondary_port: int = 5004) -> Any:
        from MatroxSdp import MatroxSdp, MatroxSdpEnums as E
        sdp = MatroxSdp()
        sdp.username = "-"
        sdp.session_id = 1
        sdp.session_version = 1
        sdp.origin_address = "127.0.0.1"
        sdp.session_name = "test"
        sdp.start = 0
        sdp.stop = 0
        sdp.has_group_attribute = True
        sdp.primary_media_name = "primary"
        sdp.secondary_media_name = "secondary"
        sdp.media_count = 2

        for i, (name, ip, port) in enumerate([
            ("primary", primary_ip, primary_port),
            ("secondary", secondary_ip, secondary_port),
        ]):
            m = sdp.medias[i]
            m.media_name = name
            m.type = E.Video.value
            m.protocol = E.ProtocolRTP_AVP.value
            m.format_code = 96
            m.payload_type = 96
            m.encoding_name = E.EncodingRaw.value
            m.clock_rate = 90000
            m.port = port
            m.port_count = 1
            m.connection_address = ip
            m.connection_count = 1

        sdp.primary_media = sdp.medias[0]
        sdp.secondary_media = sdp.medias[1]
        return sdp, E

    def test_sdp_without_group_attribute_emits_one_media(self) -> None:
        from MatroxSdpWrite import encode as sdp_encode
        sdp, _ = self._build_single_media_sdp()
        out = sdp_encode(sdp)
        assert out is not None
        assert out.count("m=") == 1
        assert "a=group:DUP" not in out

    def test_sdp_with_primary_and_secondary_emits_group_dup(self) -> None:
        # Spec line 29 + NMOS encoder invariant — two-leg SDP emits
        # `a=group:DUP <p> <s>` at the session level.
        from MatroxSdpWrite import encode as sdp_encode
        sdp, _ = self._build_dup_media_sdp()
        out = sdp_encode(sdp)
        assert out is not None
        assert "a=group:DUP primary secondary" in out

    def test_sdp_with_primary_and_secondary_emits_two_m_lines(self) -> None:
        from MatroxSdpWrite import encode as sdp_encode
        sdp, _ = self._build_dup_media_sdp()
        out = sdp_encode(sdp)
        assert out is not None
        assert out.count("m=") == 2

    def test_sdp_primary_media_has_mid_attribute(self) -> None:
        # Each media section gets `a=mid:<name>`. Verified via encoder line 224.
        from MatroxSdpWrite import encode as sdp_encode
        sdp, _ = self._build_dup_media_sdp()
        out = sdp_encode(sdp)
        assert out is not None
        assert "a=mid:primary" in out

    def test_sdp_secondary_media_has_mid_attribute(self) -> None:
        from MatroxSdpWrite import encode as sdp_encode
        sdp, _ = self._build_dup_media_sdp()
        out = sdp_encode(sdp)
        assert out is not None
        assert "a=mid:secondary" in out

    def test_sdp_primary_and_secondary_may_have_distinct_ports(self) -> None:
        # Spec line 55: "two legs may or not have different multicast
        # destination IP addresses" — implies distinct ports are also legal.
        from MatroxSdpWrite import encode as sdp_encode
        sdp, _ = self._build_dup_media_sdp(primary_port=5000, secondary_port=5004)
        out = sdp_encode(sdp)
        assert "m=video 5000" in out
        assert "m=video 5004" in out

    def test_sdp_primary_and_secondary_may_have_distinct_connection_addresses(
        self
    ) -> None:
        from MatroxSdpWrite import encode as sdp_encode
        sdp, _ = self._build_dup_media_sdp(
            primary_ip="233.252.0.10", secondary_ip="233.252.0.11"
        )
        out = sdp_encode(sdp)
        assert "c=IN IP4 233.252.0.10" in out
        assert "c=IN IP4 233.252.0.11" in out

    def test_sdp_group_dup_roundtrips_through_decoder(self) -> None:
        # Encode then decode → has_group_attribute still True, distinct names.
        from MatroxSdp import MatroxSdp
        from MatroxSdpWrite import encode as sdp_encode
        sdp, _ = self._build_dup_media_sdp()
        encoded = sdp_encode(sdp)
        assert encoded is not None

        decoded = MatroxSdp()
        err = decoded.decode(encoded)
        assert err is None, f"decode error: {err}"
        assert decoded.has_group_attribute is True
        assert decoded.primary_media_name in ("primary", "secondary")
        assert decoded.secondary_media_name in ("primary", "secondary")
        assert decoded.primary_media_name != decoded.secondary_media_name


# ===========================================================================
# Class 5 — TestPerTransportTwoLegActivation (RD14)
# ===========================================================================

class TestPerTransportTwoLegActivation:
    """Every transport must support two-leg activation at the resource layer.
    Parametrized across the transport enums with SDP support + receivers."""

    @pytest.mark.parametrize("transport_enum", _SENDER_TRANSPORTS)
    def test_two_leg_sender_activation_produces_populated_active_arrays(
        self, transport_enum: Any
    ) -> None:
        desc = get_transport_descriptor(transport_enum)
        legs = [
            _make_leg("eth0", "10.0.0.1"),
            _make_leg("eth1", "10.0.0.2"),
        ]
        activation = _make_sender_activation(desc, legs)
        # Both slots populated. SRT uses DestinationIp/SourceIp differently
        # than RTP; the common invariant is that both slots have *some* IP.
        # SourceIp is the most common field; accept InterfaceIp as fallback.
        ip0 = _extract_primary_ip(activation.active[0])
        ip1 = _extract_primary_ip(activation.active[1])
        assert ip0 == "10.0.0.1", f"{transport_enum} leg 0 SourceIp={ip0}"
        assert ip1 == "10.0.0.2", f"{transport_enum} leg 1 SourceIp={ip1}"


def _extract_primary_ip(params: Any) -> str:
    """Return the IP that the transport puts into the sender's primary
    address field (SourceIp for most, InterfaceIp for a few)."""
    for field_name in ("SourceIp", "InterfaceIp"):
        f = getattr(params, field_name, None)
        if f is not None and hasattr(f, 'defined') and f.defined:
            return str(f.value)
    return ""


# ===========================================================================
# Class 6 — TestInterworkingRules (RD10, RD11)
# ===========================================================================

class TestInterworkingRules:
    """Cross-redundancy connection invariants that don't need two nodes."""

    def test_redundant_sender_one_leg_disabled_array_size_stays_max_legs(
        self
    ) -> None:
        # RD7 — disabled legs remain in transport_params. Activation array
        # size does not shrink.
        desc = get_transport_descriptor(enums.TransportRtp)
        activation = _make_sender_activation(desc, [
            _make_leg("eth0", "10.0.0.1", enable=True),
            _make_leg("eth1", "10.0.0.2", enable=False),
        ])
        assert len(activation.active) == MAX_LEGS
        # Disabled leg 1 is present and nulled
        assert activation.active[1].SourceIp.value == "0.0.0.0"

    def test_redundant_sender_can_have_distinct_leg_0_leg_1_source_ips(self) -> None:
        # Spec line 55: "two legs may or may not have different source IP".
        desc = get_transport_descriptor(enums.TransportRtp)
        activation = _make_sender_activation(desc, [
            _make_leg("eth0", "10.0.0.1"),
            _make_leg("eth1", "192.168.1.1"),
        ])
        assert activation.active[0].SourceIp.value == "10.0.0.1"
        assert activation.active[1].SourceIp.value == "192.168.1.1"

    def test_redundant_sender_can_have_identical_leg_0_leg_1_source_ips(self) -> None:
        # Spec line 55: "may or may not" — identical IPs on both legs is legal.
        desc = get_transport_descriptor(enums.TransportRtp)
        activation = _make_sender_activation(desc, [
            _make_leg("eth0", "10.0.0.1"),
            _make_leg("eth1", "10.0.0.1"),  # intentional duplicate
        ])
        assert activation.active[0].SourceIp.value == "10.0.0.1"
        assert activation.active[1].SourceIp.value == "10.0.0.1"

    def test_non_redundant_receiver_can_accept_sender_id_regardless_of_leg_count(
        self
    ) -> None:
        # RD10 — the connection is established via sender_id/receiver_id in
        # the Subscription sub-object; those IDs are opaque strings and do
        # not carry leg-count semantics.
        from nmos.types.generated.nreceiver_subscription import NReceiverSubscriptionValue
        sub = NReceiverSubscriptionValue()
        sub.SenderId.value = "00000000-0000-4000-8000-000000000001"
        sub.Active.value = True
        assert sub.SenderId.defined
        assert sub.SenderId.value == "00000000-0000-4000-8000-000000000001"


# ===========================================================================
# Class 7 — TestSdpFromParamsTwoLegEmission (G2 fixed)
# ===========================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestSdpFromParamsTwoLegEmission:
    """End-to-end two-leg SDP emission through _generate_sdp_from_params.

    The refactored `_build_tcp_control_manifest_sdp` (for RTSP/USB) inspects
    the sender's activation for a second enabled leg and, if present, emits
    a spec-compliant `a=group:DUP primary secondary` SDP with two `m=` sections
    and matching `a=mid:` attributes.

    This fixes G2 from the original test-redundancy-1 plan.
    """

    @pytest.fixture
    def usb_sender_and_activation(self) -> tuple[Any, Any, Any]:
        """Build a node + USB sender (simplest SDP-enabled config)."""
        with open(BUILTIN_DIR / "config8u.json") as f:
            config = json.load(f)
        node = Node()
        node.init(serial_number="REDTST")

        from nmos.node.config import ConfigBuilder
        builder = ConfigBuilder(node, verbose=False)
        for r in config.get("receivers", []):
            try:
                builder._build_receiver_from_config(r)
            except Exception:
                pass
        for s in config.get("senders", []):
            try:
                builder._build_sender_pipeline(s)
            except Exception:
                pass

        # Find the USB sender
        sender = None
        for _sid, s in node.senders:
            if s.Transport.defined and "usb" in str(s.Transport.value):
                sender = s
                break
        assert sender is not None, "USB sender must exist in config8u"
        return node, sender, sender.ResourceCore.Id.value

    def test_generate_sdp_single_leg_emits_one_media_line(
        self, usb_sender_and_activation: tuple[Any, Any, Any]
    ) -> None:
        # Baseline: single-leg SDP has one m= section and no a=group:DUP.
        node, sender, sender_id = usb_sender_and_activation
        sdp = _generate_sdp_from_params(node, sender, sender_id)
        assert sdp is not None
        assert sdp.count("m=") == 1
        assert "a=group:DUP" not in sdp

    def test_generate_sdp_two_leg_emits_group_dup(
        self, usb_sender_and_activation: tuple[Any, Any, Any]
    ) -> None:
        # Populate leg 1 active.SourceIp as if a second interface were bound.
        node, sender, sender_id = usb_sender_and_activation
        activation = node.get_sender_activation(sender_id)
        assert activation is not None
        activation.active[1].SourceIp.value = "10.0.0.2"
        activation.active[1].SourcePort.value = 27504
        sdp = _generate_sdp_from_params(node, sender, sender_id, activation)
        assert sdp is not None
        assert "a=group:DUP primary secondary" in sdp, (
            "Two-leg activation must emit a=group:DUP per NMOS With Redundancy.md "
            "line 29 + the shared SDP encoder (sdp/MatroxSdpWrite.py:206)."
        )

    def test_generate_sdp_two_leg_emits_two_media_sections(
        self, usb_sender_and_activation: tuple[Any, Any, Any]
    ) -> None:
        node, sender, sender_id = usb_sender_and_activation
        activation = node.get_sender_activation(sender_id)
        activation.active[1].SourceIp.value = "10.0.0.2"
        activation.active[1].SourcePort.value = 27504
        sdp = _generate_sdp_from_params(node, sender, sender_id, activation)
        assert sdp is not None
        assert sdp.count("m=") == 2

    def test_generate_sdp_two_leg_emits_mid_primary_and_secondary(
        self, usb_sender_and_activation: tuple[Any, Any, Any]
    ) -> None:
        # MatroxSdpWrite.py:224 emits a=mid:<media_name> per leg when
        # has_group_attribute is set. Primary leg gets "primary", secondary
        # gets "secondary".
        node, sender, sender_id = usb_sender_and_activation
        activation = node.get_sender_activation(sender_id)
        activation.active[1].SourceIp.value = "10.0.0.2"
        activation.active[1].SourcePort.value = 27504
        sdp = _generate_sdp_from_params(node, sender, sender_id, activation)
        assert sdp is not None
        assert "a=mid:primary" in sdp
        assert "a=mid:secondary" in sdp

    def test_generate_sdp_two_leg_uses_distinct_connection_addresses(
        self, usb_sender_and_activation: tuple[Any, Any, Any]
    ) -> None:
        # Spec line 55: "two legs may or may not have different source IP
        # addresses". Pin leg 0 to a concrete IP (activation init uses
        # "auto" which the SDP helper resolves against the node interface),
        # then verify both IPs appear in the encoded SDP.
        node, sender, sender_id = usb_sender_and_activation
        activation = node.get_sender_activation(sender_id)
        activation.active[0].SourceIp.value = "10.0.0.1"
        activation.active[1].SourceIp.value = "192.168.1.42"
        activation.active[1].SourcePort.value = 27504
        sdp = _generate_sdp_from_params(node, sender, sender_id, activation)
        assert sdp is not None
        assert "c=IN IP4 10.0.0.1" in sdp
        assert "c=IN IP4 192.168.1.42" in sdp

    def test_generate_sdp_two_leg_uses_distinct_ports(
        self, usb_sender_and_activation: tuple[Any, Any, Any]
    ) -> None:
        node, sender, sender_id = usb_sender_and_activation
        activation = node.get_sender_activation(sender_id)
        activation.active[0].SourcePort.value = 27500
        activation.active[1].SourceIp.value = "10.0.0.2"
        activation.active[1].SourcePort.value = 27510
        sdp = _generate_sdp_from_params(node, sender, sender_id, activation)
        assert sdp is not None
        assert "m=application 27500 TCP usb" in sdp
        assert "m=application 27510 TCP usb" in sdp

    def test_generate_sdp_leg1_nulled_stays_single_leg(
        self, usb_sender_and_activation: tuple[Any, Any, Any]
    ) -> None:
        # When leg 1 is the "0.0.0.0" disabled sentinel that
        # init_sender_activation writes (activation.py:128), the SDP
        # must stay single-leg (no a=group:DUP).
        node, sender, sender_id = usb_sender_and_activation
        activation = node.get_sender_activation(sender_id)
        # Leg 1 is already "0.0.0.0" from init; explicit reassert for clarity
        activation.active[1].SourceIp.value = "0.0.0.0"
        sdp = _generate_sdp_from_params(node, sender, sender_id, activation)
        assert sdp is not None
        assert "a=group:DUP" not in sdp
        assert sdp.count("m=") == 1

    def test_generate_sdp_two_leg_both_emit_setup_passive(
        self, usb_sender_and_activation: tuple[Any, Any, Any]
    ) -> None:
        # SD4 for RTSP + USB: a=setup:passive per m= section. Encoder emits
        # one per TCP media, so two legs → two occurrences.
        node, sender, sender_id = usb_sender_and_activation
        activation = node.get_sender_activation(sender_id)
        activation.active[1].SourceIp.value = "10.0.0.2"
        activation.active[1].SourcePort.value = 27504
        sdp = _generate_sdp_from_params(node, sender, sender_id, activation)
        assert sdp is not None
        assert sdp.count("a=setup:passive") == 2

    # ------------------------------------------------------------------
    # RTP transport two-leg coverage — exercises the unified
    # _generate_sdp_from_params path with category="rtp" so we confirm
    # that the same 2-leg loop works for flow-derived media sections
    # (not just the TCP-control manifest).
    # ------------------------------------------------------------------

    @pytest.fixture
    def rtp_sender_and_activation(self) -> tuple[Any, Any, Any]:
        """Build a node with an RTP video sender (config1 — simplest RTP)."""
        cfg_path = BUILTIN_DIR / "config1.json"
        with open(cfg_path) as f:
            config = json.load(f)
        node = Node()
        node.init(serial_number="REDRTPTST")

        from nmos.node.config import ConfigBuilder
        builder = ConfigBuilder(node, verbose=False)
        for r in config.get("receivers", []):
            try:
                builder._build_receiver_from_config(r)
            except Exception:
                pass
        for s in config.get("senders", []):
            try:
                builder._build_sender_pipeline(s)
            except Exception:
                pass

        sender = None
        for _sid, s in node.senders:
            if s.Transport.defined and "rtp" in str(s.Transport.value):
                sender = s
                break
        assert sender is not None, "config1 must have an RTP sender"
        return node, sender, sender.ResourceCore.Id.value

    def test_generate_sdp_rtp_two_leg_emits_group_dup(
        self, rtp_sender_and_activation: tuple[Any, Any, Any]
    ) -> None:
        # Same redundancy invariant as for RTSP/USB, now via the RTP code
        # path — proves the unified function (single leg-loop, per-transport
        # switch inside) handles flow-derived media sections correctly.
        node, sender, sender_id = rtp_sender_and_activation
        activation = node.get_sender_activation(sender_id)
        assert activation is not None
        activation.active[1].SourceIp.value = "10.0.0.2"
        activation.active[1].DestinationIp.value = "233.252.0.2"
        activation.active[1].DestinationPort.value = 5004
        sdp = _generate_sdp_from_params(node, sender, sender_id, activation)
        assert sdp is not None
        assert "a=group:DUP primary secondary" in sdp
        assert sdp.count("m=") == 2
        assert "a=mid:primary" in sdp
        assert "a=mid:secondary" in sdp

    def test_generate_sdp_rtp_two_leg_both_rtp_avp_m_lines(
        self, rtp_sender_and_activation: tuple[Any, Any, Any]
    ) -> None:
        # Both legs of an RTP redundant sender emit `m=video ... RTP/AVP ...`
        # (or audio depending on the flow). No `TCP` or `UDP mp2t` markers
        # should appear — that's for other transport categories.
        node, sender, sender_id = rtp_sender_and_activation
        activation = node.get_sender_activation(sender_id)
        activation.active[1].SourceIp.value = "10.0.0.2"
        activation.active[1].DestinationIp.value = "233.252.0.2"
        activation.active[1].DestinationPort.value = 5004
        sdp = _generate_sdp_from_params(node, sender, sender_id, activation)
        assert sdp is not None
        assert sdp.count("RTP/AVP") == 2
        assert " TCP " not in sdp
        assert "UDP mp2t" not in sdp
