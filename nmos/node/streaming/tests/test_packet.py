# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for streaming packet format."""

from __future__ import annotations

import uuid

from nmos.node.streaming.packet import StreamPacket, PACKET_SIZE, DEFAULT_PERIOD_NS


class TestStreamPacket:
    """Test packet serialization and deserialization."""

    def test_round_trip(self) -> None:
        """Build a packet, serialize, deserialize — all fields match."""
        sid = uuid.uuid4()
        pkt = StreamPacket(
            sender_id=sid,
            sequence=42,
            timestamp_ns=1_000_000_000,
            period_ns=DEFAULT_PERIOD_NS,
            pep_ctr=123456,
            pep_key_version=7,
        )
        data = pkt.to_bytes()
        assert len(data) == PACKET_SIZE

        parsed = StreamPacket.from_bytes(data)
        assert parsed.sender_id == sid
        assert parsed.sequence == 42
        assert parsed.timestamp_ns == 1_000_000_000
        assert parsed.period_ns == DEFAULT_PERIOD_NS
        assert parsed.pep_ctr == 123456
        assert parsed.pep_key_version == 7

    def test_packet_size(self) -> None:
        """Packet is always exactly 1432 bytes."""
        pkt = StreamPacket(
            sender_id=uuid.UUID(int=0),
            sequence=0,
            timestamp_ns=0,
            period_ns=DEFAULT_PERIOD_NS,
        )
        assert len(pkt.to_bytes()) == 1432

    def test_zero_pep_fields(self) -> None:
        """PEP fields default to 0 when not specified."""
        pkt = StreamPacket(
            sender_id=uuid.UUID(int=1),
            sequence=100,
            timestamp_ns=5_000_000_000,
            period_ns=DEFAULT_PERIOD_NS,
        )
        data = pkt.to_bytes()
        parsed = StreamPacket.from_bytes(data)
        assert parsed.pep_ctr == 0
        assert parsed.pep_key_version == 0

    def test_max_values(self) -> None:
        """Test with maximum uint64/uint32 values."""
        pkt = StreamPacket(
            sender_id=uuid.UUID(int=(2**128) - 1),
            sequence=(2**64) - 1,
            timestamp_ns=(2**64) - 1,
            period_ns=(2**64) - 1,
            pep_ctr=(2**64) - 1,
            pep_key_version=(2**32) - 1,
        )
        data = pkt.to_bytes()
        parsed = StreamPacket.from_bytes(data)
        assert parsed.sequence == (2**64) - 1
        assert parsed.pep_ctr == (2**64) - 1
        assert parsed.pep_key_version == (2**32) - 1

    def test_wrong_size_raises(self) -> None:
        """Parsing wrong-sized data raises ValueError."""
        import pytest
        with pytest.raises(ValueError, match="invalid packet size"):
            StreamPacket.from_bytes(b"\x00" * 100)

    def test_padding_is_zeros(self) -> None:
        """Trailing 1376 bytes are all zeros."""
        pkt = StreamPacket(
            sender_id=uuid.uuid4(),
            sequence=999,
            timestamp_ns=42,
            period_ns=DEFAULT_PERIOD_NS,
            pep_ctr=1,
            pep_key_version=2,
        )
        data = pkt.to_bytes()
        assert data[56:] == b"\x00" * 1376

    def test_magic_number(self) -> None:
        """Packet starts with IPMX magic 0x49504D58."""
        pkt = StreamPacket(
            sender_id=uuid.UUID(int=0),
            sequence=0,
            timestamp_ns=0,
            period_ns=DEFAULT_PERIOD_NS,
        )
        data = pkt.to_bytes()
        import struct
        magic = struct.unpack("<I", data[:4])[0]
        assert magic == 0x49504D58

    def test_wrong_magic_raises(self) -> None:
        """Corrupted magic (wrong key) raises ValueError."""
        import pytest as _pytest
        pkt = StreamPacket(
            sender_id=uuid.uuid4(),
            sequence=0,
            timestamp_ns=0,
            period_ns=DEFAULT_PERIOD_NS,
        )
        data = bytearray(pkt.to_bytes())
        data[0] = 0xFF  # Corrupt the magic
        with _pytest.raises(ValueError, match="magic mismatch.*wrong encryption key"):
            StreamPacket.from_bytes(bytes(data))

    def test_sender_id_preserved(self) -> None:
        """UUID round-trips correctly for a known value."""
        sid = uuid.UUID("12345678-1234-5678-1234-567812345678")
        pkt = StreamPacket(
            sender_id=sid,
            sequence=0,
            timestamp_ns=0,
            period_ns=DEFAULT_PERIOD_NS,
        )
        parsed = StreamPacket.from_bytes(pkt.to_bytes())
        assert parsed.sender_id == sid
        assert str(parsed.sender_id) == "12345678-1234-5678-1234-567812345678"
