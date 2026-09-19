// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The framing two members speak to each other.
//!
//! Port of `nmos/raft/wire.py`. The byte layout is a compatibility contract
//! between implementations, not an internal detail: a mixed Python/Rust cluster
//! is the strongest conformance evidence this port can produce, and it only
//! works if both sides agree to the byte.
//!
//! `tests/wire_parity.rs` asserts that against the golden vectors exported from
//! the unmodified `test_wire.py`, so neither side holds its own recording.
//!
//! ```text
//!   0      4      6      8    9    10     12           16
//!   +------+------+------+----+----+------+------------+---------+--------+
//!   | MAGIC| major| minor| st | ty | flags|   length   | payload | crc32  |
//!   +------+------+------+----+----+------+------------+---------+--------+
//!   |<-------------------- covered by the checksum ------------->|
//! ```
//!
//! Every field is little-endian. The checksum covers everything after the
//! magic, so a frame whose magic was corrupted is rejected before its length is
//! believed -- which matters because the length decides an allocation.

use crate::errors::RaftProtocolError;

/// This implementation's protocol major. A difference is not negotiable.
pub const PROTOCOL_MAJOR: u16 = 1;
/// This implementation's protocol minor.
pub const PROTOCOL_MINOR: u16 = 0;

/// `NMRA`, little-endian.
pub const MAGIC: u32 = 0x4E4D_5241;
/// Magic, versions, routing and length.
pub const HEADER_SIZE: usize = 16;
/// The CRC-32.
pub const TRAILER_SIZE: usize = 4;

/// The largest payload this member will allocate for.
///
/// A snapshot chunk is the largest thing that travels and is far below this.
/// The cap exists so a corrupt length field allocates nothing: without it, a
/// mis-parsed `u32` asks for four gigabytes.
pub const MAX_FRAME: usize = 16 * 1024 * 1024;

/// Which of a peer's two links a frame belongs to.
///
/// Two links per peer, because a multi-megabyte snapshot transfer must not
/// head-of-line-block heartbeats. A follower that stops hearing from the leader
/// starts an election, so letting a large transfer share the link with the
/// timer that prevents elections would make snapshot installs cause leadership
/// churn -- and the churn would then be blamed on load.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
#[repr(u8)]
pub enum Stream {
    /// Heartbeats, votes, appends.
    Control = 0,
    /// Snapshot transfer.
    Bulk = 1,
}

impl Stream {
    const fn from_byte(value: u8) -> Option<Self> {
        match value {
            0 => Some(Self::Control),
            1 => Some(Self::Bulk),
            _ => None,
        }
    }

    /// The stream class a message field carries, rejecting what this build does
    /// not know.
    ///
    /// Unknown *fields* are skipped, because a newer peer may send them; an
    /// unknown *value* in a field this build does understand is different -- it
    /// means the peer is routing on a class this member cannot honour, and
    /// guessing which link to answer on is worse than dropping.
    ///
    /// # Errors
    ///
    /// [`RaftProtocolError`] when `value` names no stream class.
    pub fn from_wire(value: u64) -> Result<Self, RaftProtocolError> {
        u8::try_from(value)
            .ok()
            .and_then(Self::from_byte)
            .ok_or_else(|| RaftProtocolError(format!("unknown stream class {value}")))
    }
}

/// Every frame this protocol defines.
///
/// **Numbers are permanent.** A value, once assigned, is never reused for
/// another message, so an old member decoding a frame it does not know can say
/// so precisely rather than mis-parsing it as something else.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum MessageType {
    /// Link handshake.
    Hello = 0x01,
    /// Link handshake reply.
    HelloAck = 0x02,

    /// Figure 2 RequestVote.
    RequestVote = 0x10,
    /// Its reply.
    RequestVoteReply = 0x11,
    /// Figure 2 AppendEntries, heartbeat included.
    AppendEntries = 0x12,
    /// Its reply.
    AppendEntriesReply = 0x13,
    /// Snapshot transfer.
    InstallSnapshot = 0x14,
    /// Its reply.
    InstallSnapshotReply = 0x15,
    /// Promotion of a non-voting member.
    Promote = 0x16,

    /// A client proposal.
    Propose = 0x20,
    /// Its reply.
    ProposeReply = 0x21,
    /// A proposal forwarded to the owner.
    Forward = 0x22,
    /// Its reply.
    ForwardReply = 0x23,

    /// Liveness probe.
    Ping = 0x30,
    /// Its reply.
    Pong = 0x31,
}

impl MessageType {
    const fn from_byte(value: u8) -> Option<Self> {
        match value {
            0x01 => Some(Self::Hello),
            0x02 => Some(Self::HelloAck),
            0x10 => Some(Self::RequestVote),
            0x11 => Some(Self::RequestVoteReply),
            0x12 => Some(Self::AppendEntries),
            0x13 => Some(Self::AppendEntriesReply),
            0x14 => Some(Self::InstallSnapshot),
            0x15 => Some(Self::InstallSnapshotReply),
            0x16 => Some(Self::Promote),
            0x20 => Some(Self::Propose),
            0x21 => Some(Self::ProposeReply),
            0x22 => Some(Self::Forward),
            0x23 => Some(Self::ForwardReply),
            0x30 => Some(Self::Ping),
            0x31 => Some(Self::Pong),
            _ => None,
        }
    }
}

/// This frame answers an earlier one.
pub const FLAG_REPLY: u16 = 0x0001;

/// Every bit that is not [`FLAG_REPLY`].
const RESERVED_FLAGS: u16 = !FLAG_REPLY;

/// One decoded frame: its routing header and its undecoded payload.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Frame {
    /// Which link it belongs to.
    pub stream: Stream,
    /// What it is.
    pub message_type: MessageType,
    /// Currently only [`FLAG_REPLY`].
    pub flags: u16,
    /// The protocol minor the sender speaks.
    pub minor: u16,
    /// Still encoded; decoding it belongs to `messages`.
    pub payload: Vec<u8>,
}

impl Frame {
    /// A frame with this implementation's minor.
    #[must_use]
    pub fn new(stream: Stream, message_type: MessageType, flags: u16, payload: Vec<u8>) -> Self {
        Self {
            stream,
            message_type,
            flags,
            minor: PROTOCOL_MINOR,
            payload,
        }
    }

    /// Whether this answers an earlier frame.
    #[must_use]
    pub const fn is_reply(&self) -> bool {
        self.flags & FLAG_REPLY != 0
    }
}

/// Serialise one frame, checksum included.
///
/// # Errors
///
/// The payload is above [`MAX_FRAME`].
pub fn encode_frame(frame: &Frame) -> Result<Vec<u8>, RaftProtocolError> {
    if frame.payload.len() > MAX_FRAME {
        return Err(RaftProtocolError(format!(
            "payload of {} bytes exceeds MAX_FRAME",
            frame.payload.len(),
        )));
    }

    // The checksum covers the body only, so it is built first and the magic
    // prepended after -- exactly as `encode_frame` does in the Python.
    let mut body = Vec::with_capacity(HEADER_SIZE.saturating_add(frame.payload.len()));
    body.extend_from_slice(&PROTOCOL_MAJOR.to_le_bytes());
    body.extend_from_slice(&frame.minor.to_le_bytes());
    body.push(frame.stream as u8);
    body.push(frame.message_type as u8);
    body.extend_from_slice(&frame.flags.to_le_bytes());
    let length = u32::try_from(frame.payload.len())
        .map_err(|_| RaftProtocolError("payload length does not fit in u32".to_owned()))?;
    body.extend_from_slice(&length.to_le_bytes());
    body.extend_from_slice(&frame.payload);

    let checksum = crc32fast::hash(&body);

    let mut out = Vec::with_capacity(body.len().saturating_add(4 + TRAILER_SIZE));
    out.extend_from_slice(&MAGIC.to_le_bytes());
    out.extend_from_slice(&body);
    out.extend_from_slice(&checksum.to_le_bytes());
    Ok(out)
}

/// Read a little-endian `u16` at `offset`.
fn u16_at(data: &[u8], offset: usize) -> Option<u16> {
    let bytes = data.get(offset..offset.checked_add(2)?)?;
    Some(u16::from_le_bytes([*bytes.first()?, *bytes.get(1)?]))
}

/// Read a little-endian `u32` at `offset`.
fn u32_at(data: &[u8], offset: usize) -> Option<u32> {
    let bytes = data.get(offset..offset.checked_add(4)?)?;
    Some(u32::from_le_bytes([
        *bytes.first()?,
        *bytes.get(1)?,
        *bytes.get(2)?,
        *bytes.get(3)?,
    ]))
}

/// Decode one complete frame. Every rejection drops the link.
///
/// Validates in the order the fields are needed, so the reported error names
/// the first thing that was actually wrong rather than a downstream symptom.
///
/// # Errors
///
/// Any malformed field. The message says which.
pub fn decode_frame(data: &[u8]) -> Result<Frame, RaftProtocolError> {
    let fail = |message: String| Err(RaftProtocolError(message));

    if data.len() < HEADER_SIZE + TRAILER_SIZE {
        return fail("frame is shorter than its header and trailer".to_owned());
    }
    if u32_at(data, 0) != Some(MAGIC) {
        return fail("frame does not start with the protocol magic".to_owned());
    }

    let major = u16_at(data, 4).unwrap_or_default();
    if major != PROTOCOL_MAJOR {
        return fail(format!(
            "peer speaks protocol major {major}, this member speaks \
             {PROTOCOL_MAJOR}; a major difference is not negotiable",
        ));
    }
    let minor = u16_at(data, 6).unwrap_or_default();

    let length = usize::try_from(u32_at(data, 12).unwrap_or_default()).unwrap_or(usize::MAX);
    if length > MAX_FRAME {
        return fail(format!(
            "frame claims {length} payload bytes, above the {MAX_FRAME} cap",
        ));
    }
    let Some(end) = HEADER_SIZE.checked_add(length) else {
        return fail("frame length overflows".to_owned());
    };
    if data.len() != end.saturating_add(TRAILER_SIZE) {
        return fail("frame length does not match its buffer".to_owned());
    }

    let expected = u32_at(data, end).unwrap_or_default();
    let Some(body) = data.get(4..end) else {
        return fail("frame body is truncated".to_owned());
    };
    let actual = crc32fast::hash(body);
    if expected != actual {
        return fail(format!(
            "checksum mismatch: frame says {expected:#010x}, computed {actual:#010x}",
        ));
    }

    let flags = u16_at(data, 10).unwrap_or_default();
    if flags & RESERVED_FLAGS != 0 {
        // Refused rather than masked off. A reserved bit that is set means the
        // sender meant something by it, and proceeding would be acting on a
        // frame this member does not fully understand.
        return fail(format!("reserved flag bits set: {flags:#06x}"));
    }

    let stream_byte = *data.get(8).unwrap_or(&0xFF);
    let Some(stream) = Stream::from_byte(stream_byte) else {
        return fail(format!("unknown stream class {stream_byte}"));
    };
    let type_byte = *data.get(9).unwrap_or(&0xFF);
    let Some(message_type) = MessageType::from_byte(type_byte) else {
        return fail(format!("unknown message type {type_byte:#04x}"));
    };

    Ok(Frame {
        stream,
        message_type,
        flags,
        minor,
        payload: data.get(HEADER_SIZE..end).unwrap_or_default().to_vec(),
    })
}

/// The payload length a header declares, for a reader that has only the header.
///
/// Reading the header first and only then the payload is what stops a frame
/// claiming an absurd length from allocating anything.
///
/// # Errors
///
/// The header is short or declares more than [`MAX_FRAME`].
pub fn payload_length(header: &[u8]) -> Result<usize, RaftProtocolError> {
    if header.len() < HEADER_SIZE {
        return Err(RaftProtocolError(
            "header is shorter than HEADER_SIZE".to_owned(),
        ));
    }
    let length = u32_at(header, 12).unwrap_or_default() as usize;
    if length > MAX_FRAME {
        return Err(RaftProtocolError(format!(
            "frame claims {length} payload bytes, above the {MAX_FRAME} cap",
        )));
    }
    Ok(length)
}

// ---------------------------------------------------------------------------
// Payload primitives
// ---------------------------------------------------------------------------

/// Protobuf wire types. Only the two this protocol needs.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum WireType {
    /// LEB128 integer.
    Varint = 0,
    /// Length-prefixed bytes.
    LengthDelimited = 2,
}

/// LEB128, unsigned.
///
/// Negative values cannot arise: the type is unsigned, which is what the
/// Python's `ValueError` guards against at run time.
#[must_use]
pub fn encode_varint(mut value: u64) -> Vec<u8> {
    let mut out = Vec::with_capacity(2);
    loop {
        let byte = u8::try_from(value & 0x7F).unwrap_or(0);
        value >>= 7;
        if value == 0 {
            out.push(byte);
            return out;
        }
        out.push(byte | 0x80);
    }
}

/// Decode one LEB128 value, returning it and the offset after it.
///
/// Refuses a varint longer than ten bytes. Ten is enough for any 64-bit value,
/// and without the cap a run of `0x80` bytes is an unbounded loop driven by
/// whatever a peer chose to send.
///
/// # Errors
///
/// The varint is truncated or longer than ten bytes.
pub fn decode_varint(data: &[u8], offset: usize) -> Result<(u64, usize), RaftProtocolError> {
    let mut value: u64 = 0;
    let mut shift: u32 = 0;
    let end = offset.saturating_add(10).min(data.len());
    for index in offset..end {
        let byte = *data.get(index).unwrap_or(&0);
        value |= u64::from(byte & 0x7F) << shift;
        if byte & 0x80 == 0 {
            return Ok((value, index.saturating_add(1)));
        }
        shift = shift.saturating_add(7);
    }
    Err(RaftProtocolError(
        "varint is truncated or longer than 10 bytes".to_owned(),
    ))
}

/// Builds a payload field by field.
///
/// Fields are written in ascending number by convention, which costs nothing
/// and makes a hex dump readable. Decoding does not depend on it -- a decoder
/// that assumed order would break the moment a field became optional.
#[derive(Debug, Default)]
pub struct Writer {
    out: Vec<u8>,
}

impl Writer {
    /// An empty payload.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    fn tag(&mut self, field: u64, wire_type: WireType) {
        self.out
            .extend_from_slice(&encode_varint((field << 3) | wire_type as u64));
    }

    /// An unsigned integer field.
    #[must_use]
    pub fn uint(mut self, field: u64, value: u64) -> Self {
        self.tag(field, WireType::Varint);
        self.out.extend_from_slice(&encode_varint(value));
        self
    }

    /// A boolean, encoded as 0 or 1.
    #[must_use]
    pub fn bool(self, field: u64, value: bool) -> Self {
        self.uint(field, u64::from(value))
    }

    /// A length-delimited byte string.
    #[must_use]
    pub fn bytes(mut self, field: u64, value: &[u8]) -> Self {
        self.tag(field, WireType::LengthDelimited);
        self.out
            .extend_from_slice(&encode_varint(value.len() as u64));
        self.out.extend_from_slice(value);
        self
    }

    /// A UTF-8 string.
    #[must_use]
    pub fn string(self, field: u64, value: &str) -> Self {
        self.bytes(field, value.as_bytes())
    }

    /// The finished payload.
    #[must_use]
    pub fn take(self) -> Vec<u8> {
        self.out
    }
}

/// Walks a payload, yielding `(field, wire_type)` and reading the value.
///
/// The caller loops on [`Self::next_field`], dispatches on the field number,
/// and calls the matching accessor or [`Self::skip`] for anything it does not
/// recognise.
///
/// Forgetting the skip is the one way to misuse this, so it is refused rather
/// than allowed to desynchronise silently: a reader that fell out of step would
/// decode the rest of the payload as garbage that might still parse.
#[derive(Debug)]
pub struct Reader<'a> {
    data: &'a [u8],
    offset: usize,
    pending: bool,
}

impl<'a> Reader<'a> {
    /// Read over a payload.
    #[must_use]
    pub const fn new(data: &'a [u8]) -> Self {
        Self {
            data,
            offset: 0,
            pending: false,
        }
    }

    /// The next field's number and wire type, or `None` at the end.
    ///
    /// # Errors
    ///
    /// The previous field was neither read nor skipped, or the tag is
    /// malformed.
    pub fn next_field(&mut self) -> Result<Option<(u64, WireType)>, RaftProtocolError> {
        if self.offset >= self.data.len() {
            return Ok(None);
        }
        if self.pending {
            return Err(RaftProtocolError(
                "previous field was neither read nor skipped".to_owned(),
            ));
        }
        let (tag, next) = decode_varint(self.data, self.offset)?;
        self.offset = next;
        let wire = match tag & 0x07 {
            0 => WireType::Varint,
            2 => WireType::LengthDelimited,
            other => {
                return Err(RaftProtocolError(format!("unsupported wire type {other}")));
            }
        };
        self.pending = true;
        Ok(Some((tag >> 3, wire)))
    }

    /// Read an unsigned integer.
    ///
    /// # Errors
    ///
    /// The varint is malformed.
    pub fn uint(&mut self) -> Result<u64, RaftProtocolError> {
        self.pending = false;
        let (value, next) = decode_varint(self.data, self.offset)?;
        self.offset = next;
        Ok(value)
    }

    /// Read a boolean.
    ///
    /// # Errors
    ///
    /// The varint is malformed.
    pub fn bool(&mut self) -> Result<bool, RaftProtocolError> {
        Ok(self.uint()? != 0)
    }

    /// Read a length-delimited byte string.
    ///
    /// # Errors
    ///
    /// The length runs past the end of the payload.
    pub fn bytes(&mut self) -> Result<&'a [u8], RaftProtocolError> {
        self.pending = false;
        let (length, next) = decode_varint(self.data, self.offset)?;
        self.offset = next;
        let length = usize::try_from(length)
            .map_err(|_| RaftProtocolError("length does not fit in memory".to_owned()))?;
        let Some(end) = self.offset.checked_add(length) else {
            return Err(RaftProtocolError("length overflows".to_owned()));
        };
        let Some(value) = self.data.get(self.offset..end) else {
            return Err(RaftProtocolError(
                "length-delimited field runs past the end".to_owned(),
            ));
        };
        self.offset = end;
        Ok(value)
    }

    /// Read a UTF-8 string.
    ///
    /// # Errors
    ///
    /// The field runs past the end, or is not valid UTF-8.
    pub fn string(&mut self) -> Result<String, RaftProtocolError> {
        let raw = self.bytes()?;
        std::str::from_utf8(raw)
            .map(str::to_owned)
            .map_err(|_| RaftProtocolError("string field is not valid UTF-8".to_owned()))
    }

    /// Discard a field this decoder does not recognise.
    ///
    /// # Errors
    ///
    /// The field is malformed.
    pub fn skip(&mut self, wire_type: WireType) -> Result<(), RaftProtocolError> {
        match wire_type {
            WireType::Varint => {
                self.uint()?;
            }
            WireType::LengthDelimited => {
                self.bytes()?;
            }
        }
        Ok(())
    }
}
