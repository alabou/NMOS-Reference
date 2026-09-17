# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The peer wire format. This file is the specification, not an implementation.

A second implementation in another language is expected to join the same
cluster, and a mixed cluster is the strongest available evidence that two
implementations agree. That only works if the format is written down precisely
enough to implement from, and pinned tightly enough that it cannot drift --
which is what ``tests/test_wire.py``'s committed golden vectors are for.

Frame layout
------------
Little-endian throughout. Fixed 16-byte header, payload, 4-byte trailer::

    offset  size  field
      0      4    magic     u32   0x4E4D5241  ("NMRA")
      4      2    major     u16   mismatch is fatal at handshake
      6      2    minor     u16   a higher minor is tolerated
      8      1    stream    u8    0 = CONTROL, 1 = BULK
      9      1    type      u8    message type
     10      2    flags     u16   bit 0 = this frame is a reply; rest reserved
     12      4    length    u32   payload bytes, <= MAX_FRAME
     16    length payload
    16+len   4    checksum  u32   CRC-32 over bytes [4, 16+length)

The checksum covers the header from ``major`` onward plus the whole payload.
``magic`` is excluded because it is what identifies the stream position in the
first place -- if it is wrong there is nothing to checksum.

**CRC-32, not CRC-32C.** The plan said Castagnoli; this uses the ISO-HDLC
polynomial that ``zlib.crc32`` implements, because it is in the standard
library and this package's entire premise is needing nothing installed. The
choice costs nothing in Rust either -- ``crc32fast`` implements the same
polynomial -- and the field is an integrity check against corruption, not a
cryptographic guarantee.

Payload encoding
----------------
Protobuf wire format, hand-written: LEB128 varints, ``tag = (field << 3) |
wire_type``, length-delimited bytes. Protobuf-*shaped* rather than
protobuf-*generated* because the raft backend must work without the etcd
extra -- no ``protobuf`` package, no codegen step, no committed stubs -- but a
Rust implementation can write a matching ``.proto`` and use ``prost`` against
it with no ambiguity about what the bytes mean.

**Unknown fields are skipped, never rejected.** That is what makes a minor
version bump additive: an older member reading a newer member's frame ignores
what it does not recognise instead of dropping the link. A field number, once
used, is therefore never reused for a different meaning.
"""

from __future__ import annotations

import asyncio
import zlib
from dataclasses import dataclass
from enum import IntEnum
from typing import Iterator

PROTOCOL_MAJOR = 1
PROTOCOL_MINOR = 0

MAGIC = 0x4E4D5241
HEADER_SIZE = 16
TRAILER_SIZE = 4

# A snapshot chunk is the largest thing that travels, and 16 MiB is far above
# any chunk this package sends. The cap exists so a corrupt length field
# allocates nothing: without it, a mis-parsed u32 asks for four gigabytes.
MAX_FRAME = 16 * 1024 * 1024


class Stream(IntEnum):
    """Which of a peer's two links a frame belongs to.

    Two links per peer, because a multi-megabyte snapshot transfer must not
    head-of-line-block heartbeats. A follower that stops hearing from the
    leader starts an election, so letting a large transfer share the link with
    the timer that prevents elections would make snapshot installs cause
    leadership churn -- and the churn would then be blamed on load.
    """

    CONTROL = 0
    BULK = 1


class MessageType(IntEnum):
    """Every frame this protocol defines.

    Numbers are permanent. A value, once assigned, is never reused for another
    message, so an old member decoding a frame it does not know can say so
    precisely rather than mis-parsing it as something else.
    """

    HELLO = 0x01
    HELLO_ACK = 0x02

    REQUEST_VOTE = 0x10
    REQUEST_VOTE_REPLY = 0x11
    APPEND_ENTRIES = 0x12
    APPEND_ENTRIES_REPLY = 0x13
    INSTALL_SNAPSHOT = 0x14
    INSTALL_SNAPSHOT_REPLY = 0x15
    PROMOTE = 0x16

    PROPOSE = 0x20
    PROPOSE_REPLY = 0x21
    FORWARD = 0x22
    FORWARD_REPLY = 0x23

    PING = 0x30
    PONG = 0x31


class WireType(IntEnum):
    """Protobuf wire types. Only the two this protocol needs are defined."""

    VARINT = 0
    LENGTH_DELIMITED = 2


FLAG_REPLY = 0x0001
_RESERVED_FLAGS = ~FLAG_REPLY & 0xFFFF


@dataclass(frozen=True)
class Frame:
    """One decoded frame: its routing header and its undecoded payload."""

    stream: Stream
    type: MessageType
    flags: int
    payload: bytes
    minor: int = PROTOCOL_MINOR

    @property
    def is_reply(self) -> bool:
        return bool(self.flags & FLAG_REPLY)


# ---------------------------------------------------------------------------
# Payload primitives
# ---------------------------------------------------------------------------

def encode_varint(value: int) -> bytes:
    """LEB128, unsigned. Negative values are a programming error, not input."""
    if value < 0:
        raise ValueError(f"varint cannot encode {value}")
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def decode_varint(data: bytes, offset: int) -> tuple[int, int]:
    """Returns ``(value, next_offset)``.

    Refuses a varint longer than ten bytes. Ten is enough for any 64-bit value,
    and without the cap a run of 0x80 bytes is an unbounded loop driven by
    whatever a peer chose to send.
    """
    value = 0
    shift = 0
    for index in range(offset, min(offset + 10, len(data))):
        byte = data[index]
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, index + 1
        shift += 7
    from nmos.raft.errors import RaftProtocolError

    raise RaftProtocolError("varint is truncated or longer than 10 bytes")


class Writer:
    """Builds a payload field by field.

    Fields are written in ascending number by convention, which costs nothing
    and makes a hex dump readable. Decoding does not depend on it -- a decoder
    that assumed order would break the moment a field became optional.
    """

    __slots__ = ("_out",)

    def __init__(self) -> None:
        self._out = bytearray()

    def _tag(self, field: int, wire_type: WireType) -> None:
        self._out += encode_varint((field << 3) | int(wire_type))

    def uint(self, field: int, value: int) -> Writer:
        self._tag(field, WireType.VARINT)
        self._out += encode_varint(value)
        return self

    def bool_(self, field: int, value: bool) -> Writer:
        return self.uint(field, 1 if value else 0)

    def bytes_(self, field: int, value: bytes) -> Writer:
        self._tag(field, WireType.LENGTH_DELIMITED)
        self._out += encode_varint(len(value))
        self._out += value
        return self

    def string(self, field: int, value: str) -> Writer:
        return self.bytes_(field, value.encode("utf-8"))

    def take(self) -> bytes:
        return bytes(self._out)


class Reader:
    """Walks a payload, yielding ``(field, wire_type)`` and reading the value.

    Usage is a loop over the reader, dispatching on the field number and
    calling the matching accessor, with ``skip`` for anything unrecognised::

        for field, wire in reader:
            if field == 1:
                term = reader.uint()
            else:
                reader.skip(wire)

    Forgetting the ``skip`` is the one way to misuse this, so it raises rather
    than silently desynchronising: a reader that fell out of step would decode
    the rest of the payload as garbage that might still parse.
    """

    __slots__ = ("_data", "_offset", "_pending")

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0
        self._pending = False

    def __iter__(self) -> Iterator[tuple[int, WireType]]:
        from nmos.raft.errors import RaftProtocolError

        while self._offset < len(self._data):
            if self._pending:
                raise RaftProtocolError(
                    "previous field was neither read nor skipped",
                )
            tag, self._offset = decode_varint(self._data, self._offset)
            wire = tag & 0x07
            if wire not in (WireType.VARINT, WireType.LENGTH_DELIMITED):
                raise RaftProtocolError(f"unsupported wire type {wire}")
            self._pending = True
            yield tag >> 3, WireType(wire)

    def uint(self) -> int:
        self._pending = False
        value, self._offset = decode_varint(self._data, self._offset)
        return value

    def bool_(self) -> bool:
        return bool(self.uint())

    def bytes_(self) -> bytes:
        from nmos.raft.errors import RaftProtocolError

        self._pending = False
        length, self._offset = decode_varint(self._data, self._offset)
        end = self._offset + length
        if end > len(self._data):
            raise RaftProtocolError("length-delimited field runs past the end")
        value = self._data[self._offset:end]
        self._offset = end
        return value

    def string(self) -> str:
        from nmos.raft.errors import RaftProtocolError

        raw = self.bytes_()
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RaftProtocolError("string field is not valid UTF-8") from exc

    def skip(self, wire_type: WireType) -> None:
        if wire_type is WireType.VARINT:
            self.uint()
        else:
            self.bytes_()


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------

def encode_frame(frame: Frame) -> bytes:
    """Serialise one frame, checksum included."""
    if len(frame.payload) > MAX_FRAME:
        raise ValueError(
            f"payload of {len(frame.payload)} bytes exceeds MAX_FRAME",
        )
    body = bytearray()
    body += PROTOCOL_MAJOR.to_bytes(2, "little")
    body += frame.minor.to_bytes(2, "little")
    body += bytes((int(frame.stream), int(frame.type)))
    body += frame.flags.to_bytes(2, "little")
    body += len(frame.payload).to_bytes(4, "little")
    body += frame.payload

    out = bytearray(MAGIC.to_bytes(4, "little"))
    out += body
    out += (zlib.crc32(bytes(body)) & 0xFFFFFFFF).to_bytes(4, "little")
    return bytes(out)


def decode_frame(data: bytes) -> Frame:
    """Decode one complete frame. Every rejection drops the link.

    Validates in the order the fields are needed, so the reported error names
    the first thing that was actually wrong rather than a downstream symptom.
    """
    from nmos.raft.errors import RaftProtocolError

    if len(data) < HEADER_SIZE + TRAILER_SIZE:
        raise RaftProtocolError("frame is shorter than its header and trailer")
    if int.from_bytes(data[0:4], "little") != MAGIC:
        raise RaftProtocolError("frame does not start with the protocol magic")

    major = int.from_bytes(data[4:6], "little")
    if major != PROTOCOL_MAJOR:
        raise RaftProtocolError(
            f"peer speaks protocol major {major}, this member speaks "
            f"{PROTOCOL_MAJOR}; a major difference is not negotiable",
        )
    minor = int.from_bytes(data[6:8], "little")

    length = int.from_bytes(data[12:16], "little")
    if length > MAX_FRAME:
        raise RaftProtocolError(
            f"frame claims {length} payload bytes, above the {MAX_FRAME} cap",
        )
    if len(data) != HEADER_SIZE + length + TRAILER_SIZE:
        raise RaftProtocolError("frame length does not match its buffer")

    expected = int.from_bytes(data[HEADER_SIZE + length:], "little")
    actual = zlib.crc32(data[4:HEADER_SIZE + length]) & 0xFFFFFFFF
    if expected != actual:
        raise RaftProtocolError(
            f"checksum mismatch: frame says {expected:#010x}, computed "
            f"{actual:#010x}",
        )

    flags = int.from_bytes(data[10:12], "little")
    if flags & _RESERVED_FLAGS:
        # Refused rather than masked off. A reserved bit that is set means the
        # sender meant something by it, and proceeding would be acting on a
        # frame this member does not fully understand.
        raise RaftProtocolError(f"reserved flag bits set: {flags:#06x}")

    try:
        stream = Stream(data[8])
    except ValueError as exc:
        raise RaftProtocolError(f"unknown stream class {data[8]}") from exc
    try:
        message_type = MessageType(data[9])
    except ValueError as exc:
        raise RaftProtocolError(f"unknown message type {data[9]:#04x}") from exc

    return Frame(
        stream=stream,
        type=message_type,
        flags=flags,
        payload=data[HEADER_SIZE:HEADER_SIZE + length],
        minor=minor,
    )


async def read_frame(reader: asyncio.StreamReader) -> Frame:
    """Read exactly one frame from a stream.

    Reads the header first and only then the payload, so a frame claiming an
    absurd length is refused before anything is allocated for it.
    """
    from nmos.raft.errors import RaftProtocolError

    header = await reader.readexactly(HEADER_SIZE)
    length = int.from_bytes(header[12:16], "little")
    if length > MAX_FRAME:
        raise RaftProtocolError(
            f"frame claims {length} payload bytes, above the {MAX_FRAME} cap",
        )
    rest = await reader.readexactly(length + TRAILER_SIZE)
    return decode_frame(header + rest)
