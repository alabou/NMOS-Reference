# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Auto-resolved multicast address and destination port.

**The address** follows TR-10-9-v2 §17.1: "The default multicast address for a
given IPMX media stream shall be 239.S.C.D where S is the stream number. Where
S shall be greater than 0 and less than 128." C.D are the last two octets of
the media port's own IPv4 address, so the spec's worked example is a media port
of 192.168.123.45 using 239.1.123.45 for video and 239.2.123.45 for audio.

Two consequences are tested here.

*The stream number is bounded.* S = sender_index + 1, so the highest compliant
index is 126. The clamp used to be 127, which emitted 239.128.C.D — one past
the bound the spec sets.

*The address cannot separate two Nodes.* Because C.D come from the media port
address, every Node sharing that address (all of them on a loopback rig)
derives the same group for the same stream number, and the group is fixed by
spec so it cannot be made unique. The **port** therefore carries Node identity,
keyed on the last two digits of the serial number — 100 blocks of 256 ports
from base 22000.
"""

from __future__ import annotations

import pytest

from nmos.errors import InvalidData
from nmos.node.activation_engine import (
    AUTO_PORT_BASE,
    _MAX_STREAM_INDEX,
    _NODE_PORT_BLOCK,
    MAX_SERIAL_PORT_INDEX,
    _get_unused_multicast_address_ipv4,
    node_port_offset,
    serial_port_index,
)
from nmos.node.types import IPv4Settings, Leg

MAX_PORT = 65535


def _leg(address: str | None) -> Leg:
    return Leg(name="lo", enable=True, use_ipv4=True,
               ipv4=IPv4Settings(address=address))


class TestMulticastAddress:
    """TR-10-9-v2 §17.1 — 239.S.C.D."""

    def test_spec_worked_example(self) -> None:
        """The example from §17.1, verbatim."""
        leg = _leg("192.168.123.45")
        assert _get_unused_multicast_address_ipv4(0, leg) == "239.1.123.45"
        assert _get_unused_multicast_address_ipv4(1, leg) == "239.2.123.45"

    def test_stream_number_stays_below_128(self) -> None:
        """S must satisfy 0 < S < 128 at and beyond the clamp.

        Regression: the clamp was 127, so index 127 produced 239.128.C.D.
        """
        leg = _leg("192.168.123.45")
        for index in (126, 127, 200, 10_000):
            addr = _get_unused_multicast_address_ipv4(index, leg)
            stream_number = int(addr.split(".")[1])
            assert 0 < stream_number < 128, f"index {index} → {addr}"

    def test_highest_compliant_index(self) -> None:
        assert _MAX_STREAM_INDEX == 126
        addr = _get_unused_multicast_address_ipv4(_MAX_STREAM_INDEX, _leg("10.0.0.7"))
        assert addr == "239.127.0.7"

    def test_name_or_missing_address_degrades_to_zero_octets(self) -> None:
        """A leg holding a name (or nothing) yields 239.S.0.0.

        ``nmos_node._resolve_leg_address`` exists to keep this from happening
        in practice; the behaviour is pinned here because it is what makes two
        Nodes collide when it does.
        """
        assert _get_unused_multicast_address_ipv4(0, _leg("XYZ-SNX00001")) == "239.1.0.0"
        assert _get_unused_multicast_address_ipv4(0, _leg(None)) == "239.1.0.0"


class TestSerialPortBlock:
    """The port carries Node identity, since the group cannot."""

    def test_distinct_serials_get_distinct_blocks(self) -> None:
        offsets = {s: node_port_offset(s)
                   for s in ("SNX00001", "SNX00002", "SNX00003")}
        assert len(set(offsets.values())) == 3, offsets

    def test_blocks_do_not_overlap(self) -> None:
        """Every stream of Node N stays below Node N+1's first port."""
        a = node_port_offset("SNX00001")
        b = node_port_offset("SNX00002")
        assert b - a == _NODE_PORT_BLOCK
        highest_rtcp_in_a = a + 2 * _MAX_STREAM_INDEX + 1
        assert highest_rtcp_in_a < b

    def test_no_serial_keeps_the_historical_base(self) -> None:
        """An unnamed device keeps base — it has no identity to encode."""
        assert node_port_offset("") == 0
        assert node_port_offset("   ") == 0

    def test_every_valid_serial_fits_in_16_bits(self) -> None:
        worst = AUTO_PORT_BASE + max(
            node_port_offset(f"SNX{n:05d}")
            for n in range(0, MAX_SERIAL_PORT_INDEX + 1)
        ) + 2 * _MAX_STREAM_INDEX + 1
        assert worst <= MAX_PORT, worst


class TestSerialValidation:
    """A serial with no digit to key on is reported, not silently defaulted.

    Only the last two digits participate, so "too large" cannot arise — but a
    serial whose last character is not a digit gives no basis for a block, and
    quietly using block 0 would collide with every other such device.
    """

    def test_non_digit_last_character_is_rejected(self) -> None:
        for serial in ("SNX0000A", "SNX-00001-A", "SNX00001-"):
            with pytest.raises(InvalidData, match="must end in a digit"):
                serial_port_index(serial)

    def test_non_ascii_digit_is_rejected(self) -> None:
        """``str.isdigit()`` and ``\\d`` accept these; ``int()`` would parse
        them into a block nobody intended."""
        with pytest.raises(InvalidData, match="must end in a digit"):
            serial_port_index("SNX0000٣")  # ARABIC-INDIC DIGIT THREE

    def test_only_the_last_two_digits_select_the_block(self) -> None:
        """No serial is "too large": leading digits take no part.

        100 blocks is the deliberate ceiling — past 100 devices on one host you
        add another host, which brings its own address and therefore its own
        groups, so serial numbers stay globally unique while only their block
        repeats.
        """
        assert serial_port_index("SNX00001") == 1
        assert serial_port_index("SNX12345") == 45     # the shipped default
        assert serial_port_index("SNX00099") == 99
        assert serial_port_index("SNX00100") == 0      # ...00 wraps to block 0
        # Same two digits → same block, on purpose.
        assert serial_port_index("SNX12345") == serial_port_index("SNX00045")

    def test_single_trailing_digit_is_accepted(self) -> None:
        """A one-digit tail addresses its block rather than erroring."""
        assert serial_port_index("BOARD-7") == 7

    def test_highest_block_still_fits_in_16_bits(self) -> None:
        assert MAX_SERIAL_PORT_INDEX == 99
        assert serial_port_index(f"SNX{MAX_SERIAL_PORT_INDEX:05d}") == \
            MAX_SERIAL_PORT_INDEX
        assert AUTO_PORT_BASE + node_port_offset(
            f"SNX{MAX_SERIAL_PORT_INDEX:05d}"
        ) + 2 * _MAX_STREAM_INDEX + 1 <= MAX_PORT


class TestTwoNodesOnOneMediaAddress:
    """The end-to-end case this was all for.

    Two Nodes on the same media-port address — the shipped loopback rigs — must
    end up on different ports even though the spec forces them onto the same
    multicast group.
    """

    @staticmethod
    def _resolve(serial: str) -> tuple[str, int]:
        from nmos.node.activation import (
            get_transport_descriptor, init_sender_activation,
        )
        from nmos.node.activation_engine import flip_activation
        from nmos.node.tests.test_activation import (
            _get_transport, _make_activation, _make_legs,
        )

        transport = _get_transport("urn:x-nmos:transport:rtp")
        desc = get_transport_descriptor(transport)
        activation = _make_activation(desc)
        legs = _make_legs()
        legs[0].ipv4.address = "127.0.0.1"   # both Nodes share the media address
        init_sender_activation(activation, legs, transport, desc)
        activation.staged[0].DestinationIp.value = "auto"
        activation.staged[0].DestinationPort.value = "auto"
        flip_activation(activation, legs, desc.sender_auto_resolvers, serial)
        return (str(activation.active[0].DestinationIp.value),
                int(activation.active[0].DestinationPort.value))

    def test_same_group_different_ports(self) -> None:
        group1, port1 = self._resolve("SNX00001")
        group2, port2 = self._resolve("SNX00002")
        # The group is spec-mandated and identical — that is not a defect.
        assert group1 == group2
        # The port is what keeps the two streams apart.
        assert port1 != port2, f"both Nodes resolved to port {port1}"

    def test_rtp_port_is_even_so_rtcp_can_sit_above_it(self) -> None:
        _, port = self._resolve("SNX00001")
        assert port % 2 == 0, f"RTP port {port} must be even (RFC 3550 §11)"
