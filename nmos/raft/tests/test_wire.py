# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The wire format, pinned byte for byte.

A frame bug has two shapes. One is a crash, which is fine -- the link drops and
the peer reconnects. The other is a silent mis-parse, which is not: a
mis-decoded ``leader_commit`` applies entries that were never committed, and
nothing anywhere reports an error.

So this file is unusually strict about three things:

* **Golden vectors.** Committed hex for one frame of each shape. Regenerating
  them is a deliberate act (see ``_GOLDEN``), so a codec change cannot pass
  silently -- and a second implementation in another language has concrete
  bytes to check itself against rather than prose to interpret.
* **Every rejection is specific.** The negative suite asserts not just that a
  malformed frame raises, but that it raises ``RaftProtocolError`` -- the class
  whose whole contract is "drop the link, never guess".
* **Unknown fields survive.** Forward compatibility is a property with a test,
  not an intention, because the first time it matters will be a mixed-version
  cluster in production.
"""

from __future__ import annotations

import asyncio

import pytest

from nmos.raft.errors import RaftProtocolError
from nmos.raft.wire import (
    FLAG_REPLY,
    HEADER_SIZE,
    MAGIC,
    MAX_FRAME,
    PROTOCOL_MAJOR,
    Frame,
    MessageType,
    Reader,
    Stream,
    WireType,
    Writer,
    decode_frame,
    decode_varint,
    encode_frame,
    encode_varint,
    read_frame,
)

# Golden vectors. To regenerate after a DELIBERATE format change, run the
# snippet in ``test_the_golden_vectors_are_reproducible`` and paste the output;
# doing it by accident is not possible, because every value here is asserted
# against a frame built from named fields rather than from another hex string.
_GOLDEN: dict[str, str] = {
    "request_vote": "41524d4e01000000001000000800000008051000180a2005785503d7",
    "vote_reply_granted": (
        "41524d4e010000000011010006000000080510011801a1a217f7"
    ),
    "hello": (
        "41524d4e01000000000100001b000000080110001a116e6d6f732d7265676973"
        "7472792d616263200228076c6cc01f"
    ),
    "append_empty": (
        "41524d4e01000000001200000c00000008091001182a2009282a4803bf677e90"
    ),
    "install_snapshot": (
        "41524d4e01000000011400000f00000008041000186420043a03010203400171"
        "ac3a69"
    ),
    "ping": "41524d4e010000000030000000000000522f1348",
}


def _frames() -> dict[str, Frame]:
    """The frames the golden vectors encode, built from named fields."""
    return {
        "request_vote": Frame(
            stream=Stream.CONTROL, type=MessageType.REQUEST_VOTE, flags=0,
            payload=Writer().uint(1, 5).uint(2, 0).uint(3, 10).uint(4, 5).take(),
        ),
        "vote_reply_granted": Frame(
            stream=Stream.CONTROL, type=MessageType.REQUEST_VOTE_REPLY,
            flags=FLAG_REPLY,
            payload=Writer().uint(1, 5).bool_(2, True).bool_(3, True).take(),
        ),
        "hello": Frame(
            stream=Stream.CONTROL, type=MessageType.HELLO, flags=0,
            payload=Writer().uint(1, 1).uint(2, 0)
            .string(3, "nmos-registry-abc").uint(4, 2).uint(5, 7).take(),
        ),
        "append_empty": Frame(
            stream=Stream.CONTROL, type=MessageType.APPEND_ENTRIES, flags=0,
            payload=Writer().uint(1, 9).uint(2, 1).uint(3, 42).uint(4, 9)
            .uint(5, 42).uint(9, 3).take(),
        ),
        "install_snapshot": Frame(
            stream=Stream.BULK, type=MessageType.INSTALL_SNAPSHOT, flags=0,
            payload=Writer().uint(1, 4).uint(2, 0).uint(3, 100).uint(4, 4)
            .bytes_(7, b"\x01\x02\x03").bool_(8, True).take(),
        ),
        "ping": Frame(
            stream=Stream.CONTROL, type=MessageType.PING, flags=0, payload=b"",
        ),
    }


class TestGoldenVectors:
    @pytest.mark.parametrize("name", sorted(_GOLDEN))
    def test_encoding_matches_the_committed_bytes(self, name: str) -> None:
        assert encode_frame(_frames()[name]).hex() == _GOLDEN[name]

    @pytest.mark.parametrize("name", sorted(_GOLDEN))
    def test_decoding_the_committed_bytes_yields_the_frame(
        self, name: str,
    ) -> None:
        assert decode_frame(bytes.fromhex(_GOLDEN[name])) == _frames()[name]

    def test_the_golden_vectors_are_reproducible(self) -> None:
        """Regeneration recipe, executed so it cannot go stale.

        If a deliberate format change makes the vectors above wrong, this is
        the code that produces the replacements.
        """
        regenerated = {
            name: encode_frame(frame).hex()
            for name, frame in _frames().items()
        }
        assert regenerated == _GOLDEN


class TestVarints:
    @pytest.mark.parametrize(
        "value", [0, 1, 127, 128, 255, 300, 2**31, 2**63 - 1],
    )
    def test_round_trip(self, value: int) -> None:
        encoded = encode_varint(value)
        decoded, offset = decode_varint(encoded, 0)
        assert decoded == value
        assert offset == len(encoded)

    def test_small_values_are_one_byte(self) -> None:
        assert len(encode_varint(127)) == 1
        assert len(encode_varint(128)) == 2

    def test_negative_values_are_a_programming_error(self) -> None:
        with pytest.raises(ValueError):
            encode_varint(-1)

    def test_a_run_of_continuation_bytes_is_refused(self) -> None:
        """Without a cap this is an unbounded loop driven by a peer."""
        with pytest.raises(RaftProtocolError):
            decode_varint(b"\x80" * 32, 0)

    def test_a_truncated_varint_is_refused(self) -> None:
        with pytest.raises(RaftProtocolError):
            decode_varint(b"\x80", 0)


class TestPayloadFields:
    def test_every_field_type_round_trips(self) -> None:
        payload = (
            Writer()
            .uint(1, 42)
            .bool_(2, True)
            .bool_(3, False)
            .bytes_(4, b"\x00\xff binary")
            .string(5, "a string with ünicode")
            .take()
        )
        reader = Reader(payload)
        seen: dict[int, object] = {}
        for field, wire in reader:
            if field == 1:
                seen[field] = reader.uint()
            elif field in (2, 3):
                seen[field] = reader.bool_()
            elif field == 4:
                seen[field] = reader.bytes_()
            else:
                seen[field] = reader.string()

        assert seen == {
            1: 42, 2: True, 3: False,
            4: b"\x00\xff binary", 5: "a string with ünicode",
        }

    def test_empty_and_maximal_strings(self) -> None:
        payload = Writer().string(1, "").string(2, "x" * 10_000).take()
        reader = Reader(payload)
        values = [reader.string() for _ in reader]
        assert values == ["", "x" * 10_000]

    def test_an_unknown_field_can_be_skipped(self) -> None:
        """Forward compatibility: a newer peer's extra fields are ignored.

        This is what makes a minor version bump additive rather than a flag
        day. A decoder that rejected unknown fields would turn every new
        field into a cluster-wide upgrade.
        """
        payload = (
            Writer().uint(1, 7).uint(99, 12345).string(100, "future").take()
        )
        reader = Reader(payload)
        known = None
        for field, wire in reader:
            if field == 1:
                known = reader.uint()
            else:
                reader.skip(wire)
        assert known == 7

    def test_forgetting_to_read_a_field_is_refused(self) -> None:
        """A desynchronised reader decodes garbage that might still parse."""
        payload = Writer().uint(1, 1).uint(2, 2).take()
        reader = Reader(payload)
        with pytest.raises(RaftProtocolError, match="neither read nor skipped"):
            for _field, _wire in reader:
                pass

    def test_a_length_past_the_end_is_refused(self) -> None:
        # Field 1, length-delimited, claiming 200 bytes it does not have.
        payload = bytes([0x0A]) + encode_varint(200) + b"short"
        reader = Reader(payload)
        with pytest.raises(RaftProtocolError, match="past the end"):
            for _field, _wire in reader:
                reader.bytes_()

    def test_invalid_utf8_is_refused(self) -> None:
        payload = Writer().bytes_(1, b"\xff\xfe").take()
        reader = Reader(payload)
        with pytest.raises(RaftProtocolError, match="not valid UTF-8"):
            for _field, _wire in reader:
                reader.string()

    def test_an_unsupported_wire_type_is_refused(self) -> None:
        # Wire type 5 (fixed32) is not part of this protocol.
        payload = bytes([(1 << 3) | 5]) + b"\x00\x00\x00\x00"
        with pytest.raises(RaftProtocolError, match="unsupported wire type"):
            for _field, _wire in Reader(payload):
                pass


class TestFrameRejections:
    """Every one of these drops the link rather than guessing at the bytes."""

    def _good(self) -> bytes:
        return bytes.fromhex(_GOLDEN["request_vote"])

    def test_a_short_buffer(self) -> None:
        with pytest.raises(RaftProtocolError, match="shorter than"):
            decode_frame(b"\x00" * 8)

    def test_bad_magic(self) -> None:
        corrupt = bytearray(self._good())
        corrupt[0] ^= 0xFF
        with pytest.raises(RaftProtocolError, match="protocol magic"):
            decode_frame(bytes(corrupt))

    def test_an_unsupported_major_version(self) -> None:
        corrupt = bytearray(self._good())
        corrupt[4] = PROTOCOL_MAJOR + 1
        with pytest.raises(RaftProtocolError, match="not negotiable"):
            decode_frame(bytes(corrupt))

    def test_a_higher_minor_is_tolerated(self) -> None:
        """The whole point of separating major from minor."""
        frame = _frames()["request_vote"]
        raw = bytearray(encode_frame(Frame(
            stream=frame.stream, type=frame.type, flags=frame.flags,
            payload=frame.payload, minor=9,
        )))
        decoded = decode_frame(bytes(raw))
        assert decoded.minor == 9
        assert decoded.payload == frame.payload

    def test_a_length_above_the_cap(self) -> None:
        corrupt = bytearray(self._good())
        corrupt[12:16] = (MAX_FRAME + 1).to_bytes(4, "little")
        with pytest.raises(RaftProtocolError, match="above the"):
            decode_frame(bytes(corrupt))

    def test_a_length_that_does_not_match_the_buffer(self) -> None:
        corrupt = bytearray(self._good())
        corrupt[12:16] = (4).to_bytes(4, "little")
        with pytest.raises(RaftProtocolError, match="does not match"):
            decode_frame(bytes(corrupt))

    def test_a_corrupt_payload_fails_the_checksum(self) -> None:
        corrupt = bytearray(self._good())
        corrupt[HEADER_SIZE] ^= 0xFF
        with pytest.raises(RaftProtocolError, match="checksum mismatch"):
            decode_frame(bytes(corrupt))

    def test_a_reserved_flag_bit_is_refused(self) -> None:
        """Set means the sender meant something by it."""
        frame = _frames()["request_vote"]
        raw = encode_frame(Frame(
            stream=frame.stream, type=frame.type, flags=0x8000,
            payload=frame.payload,
        ))
        with pytest.raises(RaftProtocolError, match="reserved flag"):
            decode_frame(raw)

    def test_an_unknown_message_type_is_refused(self) -> None:
        corrupt = bytearray(self._good())
        corrupt[9] = 0xEE
        corrupt[-4:] = (
            __import__("zlib").crc32(bytes(corrupt[4:-4])) & 0xFFFFFFFF
        ).to_bytes(4, "little")
        with pytest.raises(RaftProtocolError, match="unknown message type"):
            decode_frame(bytes(corrupt))

    def test_an_unknown_stream_class_is_refused(self) -> None:
        corrupt = bytearray(self._good())
        corrupt[8] = 7
        corrupt[-4:] = (
            __import__("zlib").crc32(bytes(corrupt[4:-4])) & 0xFFFFFFFF
        ).to_bytes(4, "little")
        with pytest.raises(RaftProtocolError, match="unknown stream class"):
            decode_frame(bytes(corrupt))

    def test_an_oversized_payload_is_refused_at_encode_time(self) -> None:
        with pytest.raises(ValueError, match="MAX_FRAME"):
            encode_frame(Frame(
                stream=Stream.CONTROL, type=MessageType.PING, flags=0,
                payload=b"\x00" * (MAX_FRAME + 1),
            ))


class TestStreamReading:
    async def test_frames_are_read_one_at_a_time(self) -> None:
        reader = asyncio.StreamReader()
        for name in ("request_vote", "ping", "hello"):
            reader.feed_data(bytes.fromhex(_GOLDEN[name]))
        reader.feed_eof()

        assert (await read_frame(reader)).type is MessageType.REQUEST_VOTE
        assert (await read_frame(reader)).type is MessageType.PING
        assert (await read_frame(reader)).type is MessageType.HELLO

    async def test_an_absurd_length_is_refused_before_allocating(self) -> None:
        """The reason the header is read separately from the payload."""
        header = bytearray(bytes.fromhex(_GOLDEN["ping"])[:HEADER_SIZE])
        header[12:16] = (0xFFFFFFFF).to_bytes(4, "little")
        reader = asyncio.StreamReader()
        reader.feed_data(bytes(header))
        reader.feed_eof()

        with pytest.raises(RaftProtocolError, match="above the"):
            await read_frame(reader)

    async def test_a_truncated_stream_raises(self) -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(bytes.fromhex(_GOLDEN["ping"])[:10])
        reader.feed_eof()
        with pytest.raises(asyncio.IncompleteReadError):
            await read_frame(reader)


def test_the_magic_spells_the_protocol_name() -> None:
    """Readable in a hex dump, which is where it will be needed."""
    assert MAGIC.to_bytes(4, "little") == b"ARMN"
    assert MAGIC.to_bytes(4, "big") == b"NMRA"


def test_wire_types_are_the_protobuf_ones() -> None:
    """So a Rust port can point ``prost`` at a matching .proto and be done."""
    assert int(WireType.VARINT) == 0
    assert int(WireType.LENGTH_DELIMITED) == 2
