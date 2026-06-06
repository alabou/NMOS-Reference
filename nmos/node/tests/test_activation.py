# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for nmos.node.activation — transport registry + activation engine."""

from __future__ import annotations

import pytest

from nmos.enums import EnumRegistry
from nmos.errors import InvalidData
from nmos.node.activation import (
    TransportDescriptor,
    _build_registry,
    get_transport_descriptor,
    init_sender_activation,
    init_receiver_activation,
)
from nmos.node.activation_engine import (
    ActivationResponse,
    check_constraint,
    flip_activation,
    update_staged_params,
    _parse_tai_time_string,
)
from nmos.node.types import (
    MAX_LEGS,
    Activation,
    Leg,
    Privacy,
    PrivacyPreSharedKeys,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_transport(urn: str) -> object:
    e = EnumRegistry.get(urn)
    if e is None:
        pytest.skip(f"enum {urn} not registered")
    return e


def _make_activation(descriptor: TransportDescriptor, is_sender: bool = True) -> Activation:
    """Create an Activation pre-populated with empty typed objects."""
    if is_sender:
        return Activation(
            staged=[descriptor.sender_params_type() for _ in range(MAX_LEGS)],
            active=[descriptor.sender_params_type() for _ in range(MAX_LEGS)],
            constraints=[descriptor.sender_constraints_type() for _ in range(MAX_LEGS)],
            staged_state=descriptor.sender_activation_type(),
            active_state=descriptor.sender_activation_type(),
            sender_index=5,
            sender_name="TST12345",
            privacy=Privacy(),
            privacy_keys=PrivacyPreSharedKeys(),
        )
    else:
        assert descriptor.receiver_params_type is not None
        return Activation(
            staged=[descriptor.receiver_params_type() for _ in range(MAX_LEGS)],
            active=[descriptor.receiver_params_type() for _ in range(MAX_LEGS)],
            constraints=[descriptor.receiver_constraints_type() for _ in range(MAX_LEGS)],
            staged_state=descriptor.receiver_activation_type(),
            active_state=descriptor.receiver_activation_type(),
            receiver_index=3,
            privacy=Privacy(),
            privacy_keys=PrivacyPreSharedKeys(),
        )


def _make_legs() -> list[Leg]:
    return [
        Leg(enable=True, name="eth0", use_ipv4=True),
        Leg(enable=False, name="eth1"),
    ]


# ---------------------------------------------------------------------------
# Transport registry
# ---------------------------------------------------------------------------

class TestTransportRegistry:

    def test_registry_populated(self) -> None:
        registry = _build_registry()
        assert len(registry) >= 19  # 9 base + variants

    def test_rtp_descriptor(self) -> None:
        transport = _get_transport("urn:x-nmos:transport:rtp")
        desc = get_transport_descriptor(transport)
        assert desc.has_privacy is True
        assert desc.has_rtcp is True
        assert desc.has_fec is True
        assert desc.has_sdp is True

    def test_ndi_descriptor(self) -> None:
        transport = _get_transport("urn:x-matrox:transport:ndi")
        desc = get_transport_descriptor(transport)
        assert desc.has_privacy is False
        assert desc.has_sdp is False

    def test_mqtt_no_receiver(self) -> None:
        transport = _get_transport("urn:x-nmos:transport:mqtt")
        desc = get_transport_descriptor(transport)
        assert desc.receiver_params_type is None

    def test_all_transports_have_sender(self) -> None:
        """All 9 base transports have sender params type."""
        for urn in [
            "urn:x-nmos:transport:rtp",
            "urn:x-matrox:transport:udp",
            "urn:x-matrox:transport:srt",
            "urn:x-matrox:transport:rtp.tcp",
            "urn:x-matrox:transport:rtsp",
            "urn:x-nmos:transport:usb",
            "urn:x-matrox:transport:ndi",
            "urn:x-nmos:transport:mqtt",
            "urn:x-nmos:transport:websocket",
        ]:
            transport = _get_transport(urn)
            desc = get_transport_descriptor(transport)
            assert desc.sender_params_type is not None, f"{urn} missing sender_params_type"

    def test_unknown_transport_raises(self) -> None:
        with pytest.raises(KeyError, match="unsupported"):
            get_transport_descriptor("bogus")


# ---------------------------------------------------------------------------
# Sender init for all 9 transports
# ---------------------------------------------------------------------------

class TestSenderInit:

    @pytest.mark.parametrize("urn", [
        "urn:x-nmos:transport:rtp",
        "urn:x-matrox:transport:udp",
        "urn:x-matrox:transport:srt",
        "urn:x-matrox:transport:rtp.tcp",
        "urn:x-matrox:transport:rtsp",
        "urn:x-nmos:transport:usb",
        "urn:x-matrox:transport:ndi",
        "urn:x-nmos:transport:mqtt",
        "urn:x-nmos:transport:websocket",
    ])
    def test_init_sender(self, urn: str) -> None:
        """Init sender activation for each transport produces valid objects."""
        transport = _get_transport(urn)
        desc = get_transport_descriptor(transport)
        activation = _make_activation(desc, is_sender=True)
        legs = _make_legs()

        init_sender_activation(
            activation, legs, transport, desc,
            group_hint="RTP 0:VIDEO 0",  # for NDI
        )

        # Staged and active should have SourceIp set on enabled leg
        staged_0 = activation.staged[0]
        if hasattr(staged_0, "SourceIp") and staged_0.SourceIp.defined:
            assert staged_0.SourceIp.value != ""

    def test_rtp_sender_port_calculation(self) -> None:
        """RTP sender port = 27500 + 2*index."""
        transport = _get_transport("urn:x-nmos:transport:rtp")
        desc = get_transport_descriptor(transport)
        activation = _make_activation(desc)
        activation.sender_index = 7
        legs = _make_legs()

        init_sender_activation(activation, legs, transport, desc)

        staged = activation.staged[0]
        assert staged.SourcePort.value == 27500 + 2 * 7

    def test_ndi_sender_source_name(self) -> None:
        """NDI sender gets SourceName from group_hint."""
        transport = _get_transport("urn:x-matrox:transport:ndi")
        desc = get_transport_descriptor(transport)
        activation = _make_activation(desc)
        legs = _make_legs()

        init_sender_activation(
            activation, legs, transport, desc,
            group_hint="NDI 3:VIDEO 0",
        )

        staged = activation.staged[0]
        assert staged.SourceName.value == "NDI3_VIDEO0"

    def test_ndi_sender_machine_name(self) -> None:
        """NDI sender gets MachineName from sender_name (serial number)."""
        transport = _get_transport("urn:x-matrox:transport:ndi")
        desc = get_transport_descriptor(transport)
        activation = _make_activation(desc)
        activation.sender_name = "SNX12345"
        legs = _make_legs()

        init_sender_activation(activation, legs, transport, desc, group_hint="")

        staged = activation.staged[0]
        assert staged.MachineName.value == "SNX12345"


# ---------------------------------------------------------------------------
# Receiver init
# ---------------------------------------------------------------------------

class TestReceiverInit:

    @pytest.mark.parametrize("urn", [
        "urn:x-nmos:transport:rtp",
        "urn:x-matrox:transport:udp",
        "urn:x-matrox:transport:srt",
        "urn:x-matrox:transport:rtp.tcp",
        "urn:x-matrox:transport:rtsp",
        "urn:x-nmos:transport:usb",
        "urn:x-matrox:transport:ndi",
        "urn:x-nmos:transport:websocket",
    ])
    def test_init_receiver(self, urn: str) -> None:
        """Init receiver activation for each transport (except MQTT)."""
        transport = _get_transport(urn)
        desc = get_transport_descriptor(transport)
        if desc.receiver_params_type is None:
            pytest.skip(f"{urn} doesn't support receivers")
        activation = _make_activation(desc, is_sender=False)
        legs = _make_legs()
        fmt = _get_transport("urn:x-nmos:format:video")

        init_receiver_activation(
            activation, legs, transport, fmt, desc,
            group_hint="RTP 0:VIDEO 0",
        )

        staged_0 = activation.staged[0]
        # Should have at least one identifying field set
        has_field = False
        for fname in ("InterfaceIp", "DestinationIp", "SourceIp", "ConnectionUri"):
            field = getattr(staged_0, fname, None)
            if field is not None:
                try:
                    if field.defined:
                        has_field = True
                        break
                except AttributeError:
                    pass
        assert has_field, f"No identifying field set on {type(staged_0).__name__}"

    def test_srt_receiver_port(self) -> None:
        """SRT receiver port = 37500 + 2*index."""
        transport = _get_transport("urn:x-matrox:transport:srt")
        desc = get_transport_descriptor(transport)
        activation = _make_activation(desc, is_sender=False)
        activation.receiver_index = 4
        legs = _make_legs()
        fmt = _get_transport("urn:x-nmos:format:video")

        init_receiver_activation(activation, legs, transport, fmt, desc)

        staged = activation.staged[0]
        assert staged.DestinationPort.value == 37500 + 2 * 4


# ---------------------------------------------------------------------------
# Constraint validation
# ---------------------------------------------------------------------------

class TestCheckConstraint:

    def test_undefined_constraint_passes(self) -> None:
        """Undefined constraint accepts any value."""

        class MockConstraint:
            defined = False

        check_constraint(MockConstraint(), "anything")  # should not raise

    def test_auto_skips_validation(self) -> None:
        """'auto' values skip all constraint validation."""

        class MockValue:
            defined = True
            value = "auto"

        class MockConstraint:
            defined = True
            value = type('CV', (), {
                'Minimum': type('', (), {'defined': True, 'value': 100})(),
                'Maximum': type('', (), {'defined': True, 'value': 200})(),
                'Enum': type('', (), {'defined': False})(),
                'Pattern': type('', (), {'defined': False})(),
            })()

        check_constraint(MockConstraint(), MockValue())  # should not raise


class TestParseTime:

    def test_valid(self) -> None:
        assert _parse_tai_time_string("1000:500000000") == 1000.5

    def test_invalid_format(self) -> None:
        with pytest.raises(InvalidData, match="invalid activation time"):
            _parse_tai_time_string("no-colon")


# ---------------------------------------------------------------------------
# Flip staged → active
# ---------------------------------------------------------------------------

class TestFlipActivation:

    def test_flip_copies_staged_to_active(self) -> None:
        """Flip should copy defined staged fields to active."""
        transport = _get_transport("urn:x-nmos:transport:rtp")
        desc = get_transport_descriptor(transport)
        activation = _make_activation(desc)
        legs = _make_legs()

        # Init to get valid staged values
        init_sender_activation(activation, legs, transport, desc)

        # Modify a staged field
        activation.staged[0].DestinationIp.value = "239.1.2.3"

        # Flip
        flip_activation(activation, legs)

        # Active should now have the staged value
        assert activation.active[0].DestinationIp.value == "239.1.2.3"
