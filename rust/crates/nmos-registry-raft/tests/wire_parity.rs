// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Does this codec produce the bytes the Python one produces?
//!
//! `wire_vectors.json` is exported from `test_wire.py`'s `_GOLDEN` **without
//! modifying that file** -- decision #12 of the port plan. Neither
//! implementation holds its own copy of the vectors, because two copies drift
//! and the drift is invisible: each side keeps agreeing with its own.
//!
//! This is the layer a mixed Python/Rust cluster rests on. Everything above it
//! can be equivalent-but-different; this has to be identical to the byte, and
//! a difference here would show up as a member that cannot talk to half the
//! cluster rather than as a failing test.
//!
//! Regenerate with `python -m nmos.raft.tests._wire_corpus`.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::arithmetic_side_effects,
    clippy::assertions_on_constants,
    clippy::panic
)]

use std::path::Path;

use nmos_registry_raft::wire::{
    Frame, MAX_FRAME, MessageType, Reader, Stream, WireType, Writer, decode_frame, encode_frame,
    payload_length,
};
use serde_json::Value;

fn unhex(text: &str) -> Vec<u8> {
    (0..text.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&text[i..i + 2], 16).expect("hex"))
        .collect()
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn corpus() -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/wire_vectors.json");
    serde_json::from_slice(
        &std::fs::read(&path)
            .unwrap_or_else(|_| panic!("{} is missing -- regenerate it", path.display())),
    )
    .expect("the corpus parses")
}

fn stream_of(value: u64) -> Stream {
    match value {
        0 => Stream::Control,
        1 => Stream::Bulk,
        other => panic!("the corpus names an unknown stream {other}"),
    }
}

fn type_of(value: u64) -> MessageType {
    match value {
        0x01 => MessageType::Hello,
        0x02 => MessageType::HelloAck,
        0x10 => MessageType::RequestVote,
        0x11 => MessageType::RequestVoteReply,
        0x12 => MessageType::AppendEntries,
        0x13 => MessageType::AppendEntriesReply,
        0x14 => MessageType::InstallSnapshot,
        0x15 => MessageType::InstallSnapshotReply,
        0x16 => MessageType::Promote,
        0x20 => MessageType::Propose,
        0x21 => MessageType::ProposeReply,
        0x22 => MessageType::Forward,
        0x23 => MessageType::ForwardReply,
        0x30 => MessageType::Ping,
        0x31 => MessageType::Pong,
        other => panic!("the corpus names an unknown message type {other:#04x}"),
    }
}

#[test]
fn every_golden_frame_encodes_to_the_same_bytes() {
    let corpus = corpus();
    let cases = corpus["cases"].as_array().expect("cases");
    assert!(!cases.is_empty(), "an empty corpus proves nothing");

    let mut differing = Vec::new();
    for case in cases {
        let name = case["name"].as_str().expect("name");
        let frame = Frame {
            stream: stream_of(case["stream"].as_u64().expect("stream")),
            message_type: type_of(case["type"].as_u64().expect("type")),
            flags: u16::try_from(case["flags"].as_u64().expect("flags")).expect("flags fit"),
            minor: u16::try_from(case["minor"].as_u64().expect("minor")).expect("minor fits"),
            payload: unhex(case["payload_hex"].as_str().expect("payload")),
        };
        let encoded = hex(&encode_frame(&frame).expect("encodes"));
        let expected = case["encoded_hex"].as_str().expect("encoded");
        if encoded != expected {
            differing.push(format!(
                "  {name}\n    python: {expected}\n    rust  : {encoded}"
            ));
        }
    }
    assert!(
        differing.is_empty(),
        "{} of {} golden frames encode differently:\n{}",
        differing.len(),
        cases.len(),
        differing.join("\n"),
    );
}

#[test]
fn every_golden_frame_round_trips() {
    // Encoding alone would pass if both sides were wrong in the same way about
    // what the fields mean; decoding the recorded bytes back checks the
    // interpretation as well as the layout.
    let corpus = corpus();
    for case in corpus["cases"].as_array().expect("cases") {
        let name = case["name"].as_str().expect("name");
        let bytes = unhex(case["encoded_hex"].as_str().expect("encoded"));
        let frame = decode_frame(&bytes).unwrap_or_else(|e| panic!("{name}: {e}"));

        assert_eq!(
            frame.stream,
            stream_of(case["stream"].as_u64().expect("stream")),
            "{name}: stream",
        );
        assert_eq!(
            frame.message_type,
            type_of(case["type"].as_u64().expect("type")),
            "{name}: type",
        );
        assert_eq!(
            hex(&frame.payload),
            case["payload_hex"].as_str().expect("payload"),
            "{name}: payload",
        );
        assert_eq!(hex(&encode_frame(&frame).expect("re-encodes")), hex(&bytes));
    }
}

#[test]
fn the_reply_flag_survives_the_round_trip() {
    // One of the vectors is a reply; if the flag were dropped the frame would
    // still decode and would be routed as a fresh request.
    let corpus = corpus();
    let replies: Vec<_> = corpus["cases"]
        .as_array()
        .expect("cases")
        .iter()
        .filter(|case| case["flags"].as_u64() == Some(1))
        .collect();
    assert!(
        !replies.is_empty(),
        "no golden vector carries FLAG_REPLY, so the flag is untested",
    );
    for case in replies {
        let bytes = unhex(case["encoded_hex"].as_str().expect("encoded"));
        assert!(
            decode_frame(&bytes).expect("decodes").is_reply(),
            "{}: the reply flag was lost",
            case["name"].as_str().unwrap_or_default(),
        );
    }
}

// -- what must be refused ---------------------------------------------------

/// A valid frame to corrupt.
fn sample() -> Vec<u8> {
    encode_frame(&Frame::new(
        Stream::Control,
        MessageType::Ping,
        0,
        Vec::new(),
    ))
    .expect("encodes")
}

#[test]
fn a_corrupted_checksum_is_refused() {
    let mut bytes = sample();
    let last = bytes.len() - 1;
    bytes[last] ^= 0xFF;
    let error = decode_frame(&bytes).expect_err("a bad checksum must be refused");
    assert!(error.to_string().contains("checksum"), "{error}");
}

#[test]
fn a_wrong_magic_is_refused_before_the_length_is_believed() {
    // The order matters: the length decides an allocation, so a frame whose
    // magic is wrong must be rejected before anything reads its length.
    let mut bytes = sample();
    bytes[0] ^= 0xFF;
    let error = decode_frame(&bytes).expect_err("refused");
    assert!(error.to_string().contains("magic"), "{error}");
}

#[test]
fn a_different_protocol_major_is_not_negotiable() {
    let mut bytes = sample();
    bytes[4] = 2;
    let error = decode_frame(&bytes).expect_err("refused");
    assert!(error.to_string().contains("not negotiable"), "{error}");
}

#[test]
fn a_reserved_flag_bit_is_refused_rather_than_masked() {
    // A reserved bit that is set means the sender meant something by it.
    // Proceeding would be acting on a frame this member does not fully
    // understand, so the frame is refused rather than the bit ignored.
    let frame = Frame::new(Stream::Control, MessageType::Ping, 0x0002, Vec::new());
    let bytes = encode_frame(&frame).expect("encodes");
    let error = decode_frame(&bytes).expect_err("refused");
    assert!(error.to_string().contains("reserved flag"), "{error}");
}

#[test]
fn an_unknown_message_type_names_itself() {
    // The numbers are permanent precisely so this can be specific rather than
    // a mis-parse: an old member should say "I do not know 0x7f", not decode it
    // as something it does know.
    let mut bytes = sample();
    bytes[9] = 0x7F;
    // Re-checksum, so the type is what is rejected rather than the corruption.
    let end = bytes.len() - 4;
    let crc = crc32fast::hash(&bytes[4..end]);
    bytes[end..].copy_from_slice(&crc.to_le_bytes());

    let error = decode_frame(&bytes).expect_err("refused");
    assert!(error.to_string().contains("0x7f"), "{error}");
}

#[test]
fn a_length_beyond_the_cap_allocates_nothing() {
    // The reason the cap exists: a mis-parsed u32 would otherwise ask for four
    // gigabytes. `payload_length` is what a reader consults after reading only
    // the header, before it has allocated for the body.
    let mut header = vec![0_u8; 16];
    header[0..4].copy_from_slice(&0x4E4D_5241_u32.to_le_bytes());
    header[12..16].copy_from_slice(&u32::MAX.to_le_bytes());
    let error = payload_length(&header).expect_err("refused");
    assert!(error.to_string().contains("cap"), "{error}");

    // And a length within the cap is reported.
    header[12..16].copy_from_slice(&64_u32.to_le_bytes());
    assert_eq!(payload_length(&header).expect("within the cap"), 64);
    // The cap is what makes the refusal above possible at all.
    assert_eq!(MAX_FRAME, 16 * 1024 * 1024);
}

#[test]
fn a_truncated_frame_is_refused() {
    let bytes = sample();
    for cut in [0, 1, HEADER_SIZE_MINUS_ONE, bytes.len() - 1] {
        assert!(
            decode_frame(&bytes[..cut.min(bytes.len())]).is_err(),
            "a frame truncated to {cut} bytes was accepted",
        );
    }
}

const HEADER_SIZE_MINUS_ONE: usize = 15;

// -- the payload primitives, not just the framing ---------------------------

/// Build a payload from the corpus's field description, with our own `Writer`.
fn build_payload(fields: &[Value]) -> Vec<u8> {
    let mut writer = Writer::new();
    for field in fields {
        let number = field["field"].as_u64().expect("field number");
        writer = match field["kind"].as_str().expect("kind") {
            "uint" => writer.uint(number, field["value"].as_u64().expect("uint")),
            "bool" => writer.bool(number, field["value"].as_bool().expect("bool")),
            "string" => writer.string(number, field["value"].as_str().expect("string")),
            "bytes" => writer.bytes(number, &unhex(field["value"].as_str().expect("bytes"))),
            other => panic!("unknown field kind {other}"),
        };
    }
    writer.take()
}

#[test]
fn our_writer_builds_the_same_payloads() {
    // Replaying `payload_hex` tests the framing and nothing below it. The
    // primitives -- tag, varint, length prefix -- are where an off-by-one
    // hides, and they are only exercised by building the payload here.
    let corpus = corpus();
    let mut differing = Vec::new();
    for case in corpus["cases"].as_array().expect("cases") {
        let name = case["name"].as_str().expect("name");
        let fields = case["fields"].as_array().expect("fields");
        let built = hex(&build_payload(fields));
        let expected = case["payload_hex"].as_str().expect("payload");
        if built != expected {
            differing.push(format!(
                "  {name}\n    python: {expected}\n    rust  : {built}"
            ));
        }
    }
    assert!(
        differing.is_empty(),
        "{} payload(s) built differently:\n{}",
        differing.len(),
        differing.join("\n"),
    );
}

#[test]
fn a_payload_reads_back_as_the_fields_it_was_built_from() {
    let corpus = corpus();
    for case in corpus["cases"].as_array().expect("cases") {
        let name = case["name"].as_str().expect("name");
        let fields = case["fields"].as_array().expect("fields");
        let payload = build_payload(fields);
        let mut reader = Reader::new(&payload);

        let mut seen = 0;
        while let Some((number, wire)) = reader.next_field().expect("a well-formed tag") {
            let expected = &fields[seen];
            assert_eq!(
                number,
                expected["field"].as_u64().expect("field"),
                "{name}: field {seen} has the wrong number",
            );
            match expected["kind"].as_str().expect("kind") {
                "uint" => assert_eq!(
                    reader.uint().expect("uint"),
                    expected["value"].as_u64().expect("uint"),
                    "{name}: field {number}",
                ),
                "bool" => assert_eq!(
                    reader.bool().expect("bool"),
                    expected["value"].as_bool().expect("bool"),
                    "{name}: field {number}",
                ),
                "string" => assert_eq!(
                    reader.string().expect("string"),
                    expected["value"].as_str().expect("string"),
                    "{name}: field {number}",
                ),
                "bytes" => assert_eq!(
                    hex(reader.bytes().expect("bytes")),
                    expected["value"].as_str().expect("bytes"),
                    "{name}: field {number}",
                ),
                other => panic!("unknown kind {other}"),
            }
            let _ = wire;
            seen += 1;
        }
        assert_eq!(seen, fields.len(), "{name}: not every field was read");
    }
}

#[test]
fn a_field_that_is_neither_read_nor_skipped_is_refused() {
    // The one way to misuse the reader. Allowing it would desynchronise the
    // offset and decode the rest of the payload as garbage that might still
    // parse -- a wrong message rather than a rejected one.
    let payload = Writer::new().uint(1, 7).uint(2, 9).take();
    let mut reader = Reader::new(&payload);
    assert!(reader.next_field().expect("first tag").is_some());
    let error = reader
        .next_field()
        .expect_err("reading a second tag without consuming the first");
    assert!(
        error.to_string().contains("neither read nor skipped"),
        "{error}"
    );
}

#[test]
fn an_unrecognised_field_can_be_skipped_and_the_rest_still_reads() {
    // Forward compatibility: a newer member may send a field this one does not
    // know, and the fields after it must still decode.
    let payload = Writer::new()
        .uint(1, 7)
        .string(99, "from a newer member")
        .uint(2, 9)
        .take();
    let mut reader = Reader::new(&payload);
    let mut values = Vec::new();
    while let Some((number, wire)) = reader.next_field().expect("tag") {
        match number {
            1 | 2 => values.push(reader.uint().expect("uint")),
            _ => reader.skip(wire).expect("skips"),
        }
    }
    assert_eq!(values, [7, 9], "a skipped field desynchronised the reader");
}

#[test]
fn a_varint_of_eighty_ones_is_refused_rather_than_looped_on() {
    // Without the ten-byte cap this is an unbounded loop driven by whatever a
    // peer chose to send.
    let runaway = vec![0x80_u8; 80];
    let error = nmos_registry_raft::wire::decode_varint(&runaway, 0)
        .expect_err("a runaway varint must be refused");
    assert!(error.to_string().contains("10 bytes"), "{error}");
}

#[test]
fn a_length_delimited_field_running_past_the_end_is_refused() {
    // Claiming 200 bytes in a 4-byte payload. Trusting it would read whatever
    // followed in memory.
    let mut payload = Writer::new().bytes(1, b"ab").take();
    payload[1] = 200;
    let mut reader = Reader::new(&payload);
    assert!(reader.next_field().expect("tag").is_some());
    let error = reader.bytes().expect_err("refused");
    assert!(error.to_string().contains("past the end"), "{error}");
}

#[test]
fn a_string_that_is_not_utf8_is_refused() {
    let payload = Writer::new().bytes(1, &[0xFF, 0xFE]).take();
    let mut reader = Reader::new(&payload);
    assert!(reader.next_field().expect("tag").is_some());
    let error = reader.string().expect_err("refused");
    assert!(error.to_string().contains("UTF-8"), "{error}");
}

#[test]
fn varints_round_trip_at_the_boundaries() {
    // The LEB128 continuation boundaries, where an encoder is most likely to be
    // one byte out.
    for value in [
        0_u64,
        1,
        127,
        128,
        16_383,
        16_384,
        u32::MAX as u64,
        u64::MAX,
    ] {
        let encoded = nmos_registry_raft::wire::encode_varint(value);
        let (decoded, offset) =
            nmos_registry_raft::wire::decode_varint(&encoded, 0).expect("decodes");
        assert_eq!(decoded, value, "{value} did not round-trip");
        assert_eq!(offset, encoded.len(), "{value} consumed the wrong length");
    }
}

#[test]
fn a_wire_type_this_protocol_does_not_define_is_refused() {
    // Only varint and length-delimited exist here. A tag naming wire type 5
    // (32-bit) is either a different protocol or corruption.
    let payload = nmos_registry_raft::wire::encode_varint((1 << 3) | 5);
    let mut reader = Reader::new(&payload);
    let error = reader.next_field().expect_err("refused");
    assert!(
        error.to_string().contains("unsupported wire type"),
        "{error}"
    );
    let _ = WireType::Varint;
}
