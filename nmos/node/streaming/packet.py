# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Test packet format for the streaming engine.

Packet structure (1432 bytes, little-endian):

    Offset  Size  Field
    0        4    magic (uint32) — 0x49504D58 ("IPMX") for integrity check
    4       16    sender_id (UUID as 16 raw bytes)
    20       8    sequence (uint64)
    28       8    timestamp_ns (uint64) — relative to stream start
    36       8    period_ns (uint64) — interval between packets
    44       8    pep_ctr (uint64) — PEP AES-CTR counter (0 if no PEP)
    52       4    pep_key_version (uint32) — dynamic key version (0 if no PEP)
    56    1376    padding (zeros)
    Total: 1432 bytes

The magic number allows the receiver to detect decryption failures:
after decrypting with the wrong key, the magic will be garbage and
from_bytes() raises ValueError. Without it, garbage bytes would parse
as "valid" packets with random sequence numbers.
"""

from __future__ import annotations

import struct
import uuid
from dataclasses import dataclass

# Packet size matches the MTU constant
PACKET_SIZE = 1432

# Clear PEP header prepended when encryption is enabled:
# [8B pep_ctr] [4B key_version] — mirrors RTP header extension fields
PEP_CLEAR_HEADER_SIZE = 12

# Magic number: "IPMX" in ASCII = 0x49504D58
_MAGIC = 0x49504D58

# Binary format: magic(u32) + UUID(16) + 4×u64 + u32 = 56 bytes header
_HEADER_FORMAT = "<I 16s Q Q Q Q I"
_HEADER_SIZE = struct.calcsize(_HEADER_FORMAT)  # 56 bytes
_PADDING_SIZE = PACKET_SIZE - _HEADER_SIZE      # 1376 bytes

assert _HEADER_SIZE == 56, f"Header size mismatch: {_HEADER_SIZE}"
assert _PADDING_SIZE == 1376, f"Padding size mismatch: {_PADDING_SIZE}"


@dataclass
class StreamPacket:
    """A single test packet."""

    sender_id: uuid.UUID
    sequence: int            # Increments per packet
    timestamp_ns: int        # Nanoseconds since stream start
    period_ns: int           # Interval between packets (default 1s = 1_000_000_000)
    pep_ctr: int = 0         # PEP AES-CTR counter
    pep_key_version: int = 0 # PEP dynamic key version

    def to_bytes(self) -> bytes:
        """Serialize to 1432-byte packet."""
        header = struct.pack(
            _HEADER_FORMAT,
            _MAGIC,
            self.sender_id.bytes,
            self.sequence,
            self.timestamp_ns,
            self.period_ns,
            self.pep_ctr,
            self.pep_key_version,
        )
        return header + b"\x00" * _PADDING_SIZE

    @classmethod
    def from_bytes(cls, data: bytes) -> StreamPacket:
        """Deserialize from 1432-byte packet.

        Raises ValueError if data is wrong size, magic mismatch, or unparseable.
        Magic mismatch after decryption indicates wrong encryption key.
        """
        if len(data) != PACKET_SIZE:
            raise ValueError(f"invalid packet size: {len(data)}, expected {PACKET_SIZE}")

        magic, sid_bytes, seq, ts, period, ctr, kv = struct.unpack(
            _HEADER_FORMAT, data[:_HEADER_SIZE],
        )

        if magic != _MAGIC:
            raise ValueError(
                f"packet magic mismatch: 0x{magic:08X} (expected 0x{_MAGIC:08X}) "
                "— wrong encryption key?"
            )

        return cls(
            sender_id=uuid.UUID(bytes=sid_bytes),
            sequence=seq,
            timestamp_ns=ts,
            period_ns=period,
            pep_ctr=ctr,
            pep_key_version=kv,
        )


# Default period: 1 second in nanoseconds
DEFAULT_PERIOD_NS = 1_000_000_000
