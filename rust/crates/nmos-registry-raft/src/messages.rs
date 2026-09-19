// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Every peer message, and the one place field numbers are assigned.
//!
//! Port of `nmos/raft/messages.py`. Part of the specification, with
//! [`crate::wire`]. Each message is an immutable struct with an
//! `encode`/`decode` pair, and the field numbers are **permanent**: a number,
//! once used for a meaning, is never reused for another, because a member
//! running an older build will skip a field it does not recognise rather than
//! reject it, and a reused number would be skipped as the wrong thing instead.
//!
//! Three groups:
//!
//! **Consensus** -- [`RequestVote`], [`AppendEntries`], [`InstallSnapshot`] and
//! their replies, plus [`Promote`]. Textbook Raft, with two additions that are
//! not textbook and are called out where they appear: `voting` and
//! `catching_up`, which together implement the non-voting rejoin that a volatile
//! log makes necessary.
//!
//! **Application** -- [`Propose`] and [`Forward`]. Neither is a Raft message.
//! `Propose` carries a follower's batch to the leader for appending; `Forward`
//! hands a whole registry mutation to the member that owns the Node it targets.
//! They travel on the same transport because they have the same peers, not
//! because they are the same kind of thing.
//!
//! **Liveness** -- [`Hello`] and its acknowledgement gate the connection;
//! [`Ping`]/[`Pong`] measure a link that is otherwise quiet.
//!
//! # Absent values
//!
//! Protobuf's convention is that an unset field reads as zero, and several
//! fields here are legitimately zero -- term 0, member index 0, an empty entry
//! list. So where "absent" must be distinguishable from "zero", the field is
//! encoded as *present-or-not* and decoded into `None`: [`ProposeReply::leader`]
//! is the example. Member indices start at 0, so a falsy check would read
//! "member 0" as "nobody", which is exactly the confusion that loses a vote.
//!
//! # Why indices are `u64` here and not `usize`
//!
//! These structs are the wire, and the wire has one width. A member index that
//! was `usize` would encode differently on a 32-bit build and would put a
//! fallible cast in every `encode`. The node layer converts once, at the point
//! it indexes a member table, where an out-of-range value is a protocol error it
//! can report properly.

use crate::errors::RaftProtocolError;
use crate::wire::{MessageType, Reader, Stream, Writer};

/// Opens a link and proves both ends belong to the same cluster.
///
/// `incarnation` is the load-bearing field. It increments every time a member
/// starts, so a leader that sees a peer's incarnation change knows that peer has
/// been restarted and has come back with an empty log -- which is the signal to
/// catch it up and withhold its vote until it is promoted, rather than counting
/// it toward quorum immediately.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Hello {
    /// Protocol major version. A mismatch refuses the link.
    ///
    /// Unbounded, not narrowed to the frame header's 16 bits, because the
    /// handshake both compares this against [`crate::wire::PROTOCOL_MAJOR`] and
    /// prints it in the refusal. Truncating would map 65537 onto 1 -- accepting
    /// a link this member cannot speak -- and would print the truncated number
    /// in the refusal for every value that did not collide.
    pub major: u64,
    /// Protocol minor version. A mismatch is tolerated.
    pub minor: u64,
    /// Which cluster the sender believes it belongs to.
    pub cluster_id: String,
    /// The sender's configured name.
    pub member_name: String,
    /// The sender's index in the configured member list.
    pub member_index: u64,
    /// Incremented on every start. See the struct docs.
    pub incarnation: u64,
    /// Which of the two links this connection is.
    pub stream: Stream,
}

impl Hello {
    /// The frame type that carries this message.
    pub const TYPE: MessageType = MessageType::Hello;

    /// This message's payload bytes.
    #[must_use]
    pub fn encode(&self) -> Vec<u8> {
        Writer::new()
            .uint(1, self.major)
            .uint(2, self.minor)
            .string(3, &self.cluster_id)
            .string(4, &self.member_name)
            .uint(5, self.member_index)
            .uint(6, self.incarnation)
            .uint(7, self.stream as u64)
            .take()
    }

    /// Reads a payload back.
    ///
    /// # Errors
    ///
    /// [`RaftProtocolError`] if the payload is malformed or names a stream class
    /// this build does not know.
    pub fn decode(payload: &[u8]) -> Result<Self, RaftProtocolError> {
        let mut message = Self {
            major: 0,
            minor: 0,
            cluster_id: String::new(),
            member_name: String::new(),
            member_index: 0,
            incarnation: 0,
            stream: Stream::Control,
        };
        let mut reader = Reader::new(payload);
        while let Some((number, wire)) = reader.next_field()? {
            match number {
                1 => message.major = reader.uint()?,
                2 => message.minor = reader.uint()?,
                3 => message.cluster_id = reader.string()?,
                4 => message.member_name = reader.string()?,
                5 => message.member_index = reader.uint()?,
                6 => message.incarnation = reader.uint()?,
                7 => message.stream = Stream::from_wire(reader.uint()?)?,
                _ => reader.skip(wire)?,
            }
        }
        Ok(message)
    }
}

/// Accepts or refuses a link, saying why when it refuses.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HelloAck {
    /// Whether the link may be used.
    pub accepted: bool,
    /// Why not, when it may not.
    pub reason: String,
    /// The answering member's protocol minor version.
    pub minor: u64,
    /// The answering member's index.
    pub member_index: u64,
    /// The answering member's incarnation.
    pub incarnation: u64,
}

impl HelloAck {
    /// The frame type that carries this message.
    pub const TYPE: MessageType = MessageType::HelloAck;

    /// This message's payload bytes.
    #[must_use]
    pub fn encode(&self) -> Vec<u8> {
        Writer::new()
            .bool(1, self.accepted)
            .string(2, &self.reason)
            .uint(3, self.minor)
            .uint(4, self.member_index)
            .uint(5, self.incarnation)
            .take()
    }

    /// Reads a payload back.
    ///
    /// # Errors
    ///
    /// [`RaftProtocolError`] if the payload is malformed.
    pub fn decode(payload: &[u8]) -> Result<Self, RaftProtocolError> {
        let mut message = Self {
            accepted: false,
            reason: String::new(),
            minor: 0,
            member_index: 0,
            incarnation: 0,
        };
        let mut reader = Reader::new(payload);
        while let Some((number, wire)) = reader.next_field()? {
            match number {
                1 => message.accepted = reader.bool()?,
                2 => message.reason = reader.string()?,
                3 => message.minor = reader.uint()?,
                4 => message.member_index = reader.uint()?,
                5 => message.incarnation = reader.uint()?,
                _ => reader.skip(wire)?,
            }
        }
        Ok(message)
    }
}

/// One log entry as it travels: term, index, and the operation's bytes.
///
/// The decoded operation is deliberately not here. It is reconstructed on
/// receipt so that a malformed payload is rejected at the edge, by the code that
/// can still drop the link, rather than inside apply -- which runs
/// synchronously, mutates the store, and has nowhere good to fail.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WireEntry {
    /// The term the entry was created in.
    pub term: u64,
    /// The entry's position in the log.
    pub index: u64,
    /// The operation, still encoded.
    pub payload: Vec<u8>,
}

impl WireEntry {
    /// This entry's payload bytes.
    #[must_use]
    pub fn encode(&self) -> Vec<u8> {
        Writer::new()
            .uint(1, self.term)
            .uint(2, self.index)
            .bytes(3, &self.payload)
            .take()
    }

    /// Reads an entry back.
    ///
    /// # Errors
    ///
    /// [`RaftProtocolError`] if the payload is malformed.
    pub fn decode(payload: &[u8]) -> Result<Self, RaftProtocolError> {
        let mut message = Self {
            term: 0,
            index: 0,
            payload: Vec::new(),
        };
        let mut reader = Reader::new(payload);
        while let Some((number, wire)) = reader.next_field()? {
            match number {
                1 => message.term = reader.uint()?,
                2 => message.index = reader.uint()?,
                3 => message.payload = reader.bytes()?.to_vec(),
                _ => reader.skip(wire)?,
            }
        }
        Ok(message)
    }
}

/// Raft §5.2. Ask a peer for its vote in `term`.
///
/// Two fields beyond the paper's, both serving the recovery path for "when every
/// voter has forgotten".
///
/// `probe` asks a peer only to state whether it can vote, never for the vote
/// itself. A member that came back from a restart needs to know how many of its
/// peers are in the same condition before it may do anything about it, and
/// without a probe the only way to ask would be to stand for election -- which
/// inflates the term on every attempt and, in the state this exists to escape,
/// can never succeed.
///
/// `amnesiac` carries the evidence: the members this candidate has *itself
/// observed* answering `voting = false`. A voter that has lost its log grants
/// nothing on trust; it re-does the arithmetic on this list plus its own status,
/// and grants only when the two together prove that no quorum of voters can
/// exist. Members the candidate has not heard from are absent from the list and
/// therefore counted as voters -- which is what stops a partition looking like an
/// empty cluster.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RequestVote {
    /// The term being stood for -- prospective when `pre_vote` is set.
    pub term: u64,
    /// The candidate's member index.
    pub candidate: u64,
    /// The candidate's last log index, for §5.4.1.
    pub last_log_index: u64,
    /// The term of that entry.
    pub last_log_term: u64,
    /// Ask only whether this peer can vote, not for the vote.
    pub probe: bool,
    /// Members observed to have answered `voting = false`.
    pub amnesiac: Vec<u64>,
    /// Raft §9.6, and etcd's `MsgPreVote`: "would you vote for me?".
    ///
    /// `term` then carries the term the candidate *would* stand in -- its own
    /// plus one -- while the candidate's own term stays where it is. A voter
    /// answers without changing its term, its recorded vote or anything on disk,
    /// which is the whole point: a member that has lost contact can find out
    /// whether it could win before inflicting a term increment on a cluster that
    /// is working perfectly well without it.
    pub pre_vote: bool,
}

impl RequestVote {
    /// The frame type that carries this message.
    pub const TYPE: MessageType = MessageType::RequestVote;

    /// This message's payload bytes.
    #[must_use]
    pub fn encode(&self) -> Vec<u8> {
        let mut writer = Writer::new()
            .uint(1, self.term)
            .uint(2, self.candidate)
            .uint(3, self.last_log_index)
            .uint(4, self.last_log_term)
            .bool(5, self.probe);
        for member in &self.amnesiac {
            writer = writer.uint(6, *member);
        }
        writer.bool(7, self.pre_vote).take()
    }

    /// Reads a payload back.
    ///
    /// # Errors
    ///
    /// [`RaftProtocolError`] if the payload is malformed.
    pub fn decode(payload: &[u8]) -> Result<Self, RaftProtocolError> {
        let mut message = Self {
            term: 0,
            candidate: 0,
            last_log_index: 0,
            last_log_term: 0,
            probe: false,
            amnesiac: Vec::new(),
            pre_vote: false,
        };
        let mut reader = Reader::new(payload);
        while let Some((number, wire)) = reader.next_field()? {
            match number {
                1 => message.term = reader.uint()?,
                2 => message.candidate = reader.uint()?,
                3 => message.last_log_index = reader.uint()?,
                4 => message.last_log_term = reader.uint()?,
                5 => message.probe = reader.bool()?,
                6 => message.amnesiac.push(reader.uint()?),
                7 => message.pre_vote = reader.bool()?,
                _ => reader.skip(wire)?,
            }
        }
        Ok(message)
    }
}

/// Grant or refuse, and say whether this voter counts at all.
///
/// `voting` is the addition. A member that has restarted comes back with an
/// empty log, and an empty log considers every candidate up to date -- so it
/// would grant any vote it was asked for, defeating the check that stops a
/// candidate missing committed entries from winning. Until the leader has caught
/// it up and promoted it, it answers `voting = false` and the candidate does not
/// count it toward a majority.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RequestVoteReply {
    /// The voter's term, or the prospective term on a granted pre-vote.
    pub term: u64,
    /// Whether the vote was given.
    pub granted: bool,
    /// Whether this member's vote counts at all yet.
    pub voting: bool,
    /// Which question this answers.
    ///
    /// Load-bearing, not decoration: a granted pre-vote carries the
    /// *prospective* term and a refused one carries the voter's own, so a
    /// candidate comparing terms alone could not tell a pre-vote reply from a
    /// real one -- and would either count a pre-vote as a vote or step down from
    /// its own proposal.
    pub pre_vote: bool,
}

impl RequestVoteReply {
    /// The frame type that carries this message.
    pub const TYPE: MessageType = MessageType::RequestVoteReply;

    /// This message's payload bytes.
    #[must_use]
    pub fn encode(&self) -> Vec<u8> {
        Writer::new()
            .uint(1, self.term)
            .bool(2, self.granted)
            .bool(3, self.voting)
            .bool(4, self.pre_vote)
            .take()
    }

    /// Reads a payload back.
    ///
    /// # Errors
    ///
    /// [`RaftProtocolError`] if the payload is malformed.
    pub fn decode(payload: &[u8]) -> Result<Self, RaftProtocolError> {
        let mut message = Self {
            term: 0,
            granted: false,
            voting: false,
            pre_vote: false,
        };
        let mut reader = Reader::new(payload);
        while let Some((number, wire)) = reader.next_field()? {
            match number {
                1 => message.term = reader.uint()?,
                2 => message.granted = reader.bool()?,
                3 => message.voting = reader.bool()?,
                4 => message.pre_vote = reader.bool()?,
                _ => reader.skip(wire)?,
            }
        }
        Ok(message)
    }
}

/// Raft §5.3, and the heartbeat when `entries` is empty.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AppendEntries {
    /// The leader's term.
    pub term: u64,
    /// The leader's member index.
    pub leader: u64,
    /// The index immediately before the first entry carried.
    pub prev_log_index: u64,
    /// The term of that entry, for the consistency check.
    pub prev_log_term: u64,
    /// How far the leader has committed.
    pub leader_commit: u64,
    /// Correlates the reply with the round that produced it.
    pub request_id: u64,
    /// The entries, empty on a heartbeat.
    pub entries: Vec<WireEntry>,
}

impl AppendEntries {
    /// The frame type that carries this message.
    pub const TYPE: MessageType = MessageType::AppendEntries;

    /// This message's payload bytes.
    #[must_use]
    pub fn encode(&self) -> Vec<u8> {
        let mut writer = Writer::new()
            .uint(1, self.term)
            .uint(2, self.leader)
            .uint(3, self.prev_log_index)
            .uint(4, self.prev_log_term)
            .uint(5, self.leader_commit)
            .uint(6, self.request_id);
        for entry in &self.entries {
            writer = writer.bytes(7, &entry.encode());
        }
        writer.take()
    }

    /// Reads a payload back.
    ///
    /// # Errors
    ///
    /// [`RaftProtocolError`] if the payload or any entry within it is malformed.
    pub fn decode(payload: &[u8]) -> Result<Self, RaftProtocolError> {
        let mut message = Self {
            term: 0,
            leader: 0,
            prev_log_index: 0,
            prev_log_term: 0,
            leader_commit: 0,
            request_id: 0,
            entries: Vec::new(),
        };
        let mut reader = Reader::new(payload);
        while let Some((number, wire)) = reader.next_field()? {
            match number {
                1 => message.term = reader.uint()?,
                2 => message.leader = reader.uint()?,
                3 => message.prev_log_index = reader.uint()?,
                4 => message.prev_log_term = reader.uint()?,
                5 => message.leader_commit = reader.uint()?,
                6 => message.request_id = reader.uint()?,
                7 => {
                    let entry = WireEntry::decode(reader.bytes()?)?;
                    message.entries.push(entry);
                }
                _ => reader.skip(wire)?,
            }
        }
        Ok(message)
    }
}

/// Accept or reject, with enough detail to resume in one step.
///
/// `conflict_index`/`conflict_term` are the fast-backtrack optimisation: a naive
/// Raft walks back one index per round trip, which for a member rejoining a busy
/// registry is a round trip per committed entry. Reporting the start of the
/// conflicting term instead lets the leader skip the whole run at once.
///
/// `catching_up` is the other half of the non-voting rejoin: a restarted member
/// acknowledges nothing toward quorum until it has been promoted, so the leader
/// must not count this reply when deciding what is committed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AppendEntriesReply {
    /// The replying member's term.
    pub term: u64,
    /// Whether the consistency check passed.
    pub success: bool,
    /// How far this member's log now matches the leader's.
    pub match_index: u64,
    /// Where the conflicting run starts, on a rejection.
    pub conflict_index: u64,
    /// The term of that run.
    pub conflict_term: u64,
    /// Do not count this reply toward quorum.
    pub catching_up: bool,
    /// Echoes the request's id.
    pub request_id: u64,
}

impl AppendEntriesReply {
    /// The frame type that carries this message.
    pub const TYPE: MessageType = MessageType::AppendEntriesReply;

    /// This message's payload bytes.
    #[must_use]
    pub fn encode(&self) -> Vec<u8> {
        Writer::new()
            .uint(1, self.term)
            .bool(2, self.success)
            .uint(3, self.match_index)
            .uint(4, self.conflict_index)
            .uint(5, self.conflict_term)
            .bool(6, self.catching_up)
            .uint(7, self.request_id)
            .take()
    }

    /// Reads a payload back.
    ///
    /// # Errors
    ///
    /// [`RaftProtocolError`] if the payload is malformed.
    pub fn decode(payload: &[u8]) -> Result<Self, RaftProtocolError> {
        let mut message = Self {
            term: 0,
            success: false,
            match_index: 0,
            conflict_index: 0,
            conflict_term: 0,
            catching_up: false,
            request_id: 0,
        };
        let mut reader = Reader::new(payload);
        while let Some((number, wire)) = reader.next_field()? {
            match number {
                1 => message.term = reader.uint()?,
                2 => message.success = reader.bool()?,
                3 => message.match_index = reader.uint()?,
                4 => message.conflict_index = reader.uint()?,
                5 => message.conflict_term = reader.uint()?,
                6 => message.catching_up = reader.bool()?,
                7 => message.request_id = reader.uint()?,
                _ => reader.skip(wire)?,
            }
        }
        Ok(message)
    }
}

/// A chunk of a snapshot, for a follower too far behind to replicate to.
///
/// Travels on the [`Stream::Bulk`] stream. A snapshot is the whole registry
/// serialised, and sharing a link with heartbeats would let a large transfer
/// stall the timer that prevents elections -- so a snapshot install would cause
/// leadership churn, and the churn would be blamed on load.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InstallSnapshot {
    /// The leader's term.
    pub term: u64,
    /// The leader's member index.
    pub leader: u64,
    /// The last index the snapshot covers.
    pub last_index: u64,
    /// The term of that entry.
    pub last_term: u64,
    /// Where this chunk starts within the snapshot.
    pub offset: u64,
    /// The chunk itself.
    pub data: Vec<u8>,
    /// Whether this is the final chunk.
    pub done: bool,
    /// The ownership table, carried alongside the store.
    pub ownership: Vec<u8>,
}

impl InstallSnapshot {
    /// The frame type that carries this message.
    pub const TYPE: MessageType = MessageType::InstallSnapshot;

    /// This message's payload bytes.
    #[must_use]
    pub fn encode(&self) -> Vec<u8> {
        Writer::new()
            .uint(1, self.term)
            .uint(2, self.leader)
            .uint(3, self.last_index)
            .uint(4, self.last_term)
            .uint(5, self.offset)
            .bytes(6, &self.ownership)
            .bytes(7, &self.data)
            .bool(8, self.done)
            .take()
    }

    /// Reads a payload back.
    ///
    /// # Errors
    ///
    /// [`RaftProtocolError`] if the payload is malformed.
    pub fn decode(payload: &[u8]) -> Result<Self, RaftProtocolError> {
        let mut message = Self {
            term: 0,
            leader: 0,
            last_index: 0,
            last_term: 0,
            offset: 0,
            data: Vec::new(),
            done: false,
            ownership: Vec::new(),
        };
        let mut reader = Reader::new(payload);
        while let Some((number, wire)) = reader.next_field()? {
            match number {
                1 => message.term = reader.uint()?,
                2 => message.leader = reader.uint()?,
                3 => message.last_index = reader.uint()?,
                4 => message.last_term = reader.uint()?,
                5 => message.offset = reader.uint()?,
                6 => message.ownership = reader.bytes()?.to_vec(),
                7 => message.data = reader.bytes()?.to_vec(),
                8 => message.done = reader.bool()?,
                _ => reader.skip(wire)?,
            }
        }
        Ok(message)
    }
}

/// How much of the snapshot has landed, so the leader can resume.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InstallSnapshotReply {
    /// The replying member's term.
    pub term: u64,
    /// How many bytes it now holds.
    pub bytes_received: u64,
    /// Whether it considers the transfer complete.
    pub done: bool,
}

impl InstallSnapshotReply {
    /// The frame type that carries this message.
    pub const TYPE: MessageType = MessageType::InstallSnapshotReply;

    /// This message's payload bytes.
    #[must_use]
    pub fn encode(&self) -> Vec<u8> {
        Writer::new()
            .uint(1, self.term)
            .uint(2, self.bytes_received)
            .bool(3, self.done)
            .take()
    }

    /// Reads a payload back.
    ///
    /// # Errors
    ///
    /// [`RaftProtocolError`] if the payload is malformed.
    pub fn decode(payload: &[u8]) -> Result<Self, RaftProtocolError> {
        let mut message = Self {
            term: 0,
            bytes_received: 0,
            done: false,
        };
        let mut reader = Reader::new(payload);
        while let Some((number, wire)) = reader.next_field()? {
            match number {
                1 => message.term = reader.uint()?,
                2 => message.bytes_received = reader.uint()?,
                3 => message.done = reader.bool()?,
                _ => reader.skip(wire)?,
            }
        }
        Ok(message)
    }
}

/// The leader telling a caught-up member that its vote now counts.
///
/// Sent once `match_index` has reached the commit index the leader held when it
/// first heard from this peer again. Until then the peer has an empty or partial
/// log and must not participate in elections.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Promote {
    /// The leader's term.
    pub term: u64,
    /// The leader's member index.
    pub leader: u64,
    /// The index this member is confirmed to hold.
    pub through_index: u64,
}

impl Promote {
    /// The frame type that carries this message.
    pub const TYPE: MessageType = MessageType::Promote;

    /// This message's payload bytes.
    #[must_use]
    pub fn encode(&self) -> Vec<u8> {
        Writer::new()
            .uint(1, self.term)
            .uint(2, self.leader)
            .uint(3, self.through_index)
            .take()
    }

    /// Reads a payload back.
    ///
    /// # Errors
    ///
    /// [`RaftProtocolError`] if the payload is malformed.
    pub fn decode(payload: &[u8]) -> Result<Self, RaftProtocolError> {
        let mut message = Self {
            term: 0,
            leader: 0,
            through_index: 0,
        };
        let mut reader = Reader::new(payload);
        while let Some((number, wire)) = reader.next_field()? {
            match number {
                1 => message.term = reader.uint()?,
                2 => message.leader = reader.uint()?,
                3 => message.through_index = reader.uint()?,
                _ => reader.skip(wire)?,
            }
        }
        Ok(message)
    }
}

/// A follower's batch of operations, for the leader to append.
///
/// Batched by construction: the field repeats, and everything a member proposed
/// within one event-loop tick travels in one message. That is the whole of the
/// batching design on the wire -- one message per tick per peer, however many
/// registrations arrived.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Propose {
    /// The encoded operations.
    pub proposals: Vec<Vec<u8>>,
    /// Correlates the reply.
    pub request_id: u64,
}

impl Propose {
    /// The frame type that carries this message.
    pub const TYPE: MessageType = MessageType::Propose;

    /// This message's payload bytes.
    #[must_use]
    pub fn encode(&self) -> Vec<u8> {
        let mut writer = Writer::new().uint(1, self.request_id);
        for proposal in &self.proposals {
            writer = writer.bytes(2, proposal);
        }
        writer.take()
    }

    /// Reads a payload back.
    ///
    /// # Errors
    ///
    /// [`RaftProtocolError`] if the payload is malformed.
    pub fn decode(payload: &[u8]) -> Result<Self, RaftProtocolError> {
        let mut message = Self {
            proposals: Vec::new(),
            request_id: 0,
        };
        let mut reader = Reader::new(payload);
        while let Some((number, wire)) = reader.next_field()? {
            match number {
                1 => message.request_id = reader.uint()?,
                2 => message.proposals.push(reader.bytes()?.to_vec()),
                _ => reader.skip(wire)?,
            }
        }
        Ok(message)
    }
}

/// Accepted and assigned an index, or refused with the leader's identity.
///
/// Not on the critical path: the proposer learns its operation committed by
/// applying it, like every other member. This exists to reject promptly when
/// this member is not the leader, and to let the proposer map its batch to
/// indices for diagnostics.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProposeReply {
    /// Whether the batch was appended.
    pub accepted: bool,
    /// Why not, when it was not.
    pub reason: String,
    /// The answering member's term.
    pub term: u64,
    /// The index the first proposal landed at.
    pub first_index: u64,
    /// Echoes the request's id.
    pub request_id: u64,
    /// Who the leader is, when this member knows and is not it.
    ///
    /// `None` rather than zero: member 0 is a real member.
    pub leader: Option<u64>,
}

impl ProposeReply {
    /// The frame type that carries this message.
    pub const TYPE: MessageType = MessageType::ProposeReply;

    /// This message's payload bytes.
    #[must_use]
    pub fn encode(&self) -> Vec<u8> {
        let writer = Writer::new()
            .bool(1, self.accepted)
            .string(2, &self.reason)
            .uint(3, self.term)
            .uint(4, self.first_index)
            .uint(5, self.request_id);
        // Present-or-absent, because member 0 is a real member: encoding a
        // sentinel zero would name the first member as leader whenever none was
        // known.
        match self.leader {
            Some(leader) => writer.uint(6, leader).take(),
            None => writer.take(),
        }
    }

    /// Reads a payload back.
    ///
    /// # Errors
    ///
    /// [`RaftProtocolError`] if the payload is malformed.
    pub fn decode(payload: &[u8]) -> Result<Self, RaftProtocolError> {
        let mut message = Self {
            accepted: false,
            reason: String::new(),
            term: 0,
            first_index: 0,
            request_id: 0,
            leader: None,
        };
        let mut reader = Reader::new(payload);
        while let Some((number, wire)) = reader.next_field()? {
            match number {
                1 => message.accepted = reader.bool()?,
                2 => message.reason = reader.string()?,
                3 => message.term = reader.uint()?,
                4 => message.first_index = reader.uint()?,
                5 => message.request_id = reader.uint()?,
                6 => message.leader = Some(reader.uint()?),
                _ => reader.skip(wire)?,
            }
        }
        Ok(message)
    }
}

/// A whole registry mutation, handed to the member that owns its Node.
///
/// `body_text` is the request body **verbatim**. The registry's fidelity
/// guarantee is that what a client registered is what every member serves, byte
/// for byte, including vendor extensions the type layer does not model -- so
/// re-encoding it here, even through a round trip that looks lossless, would
/// break that guarantee at exactly the point nobody would look.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Forward {
    /// The HTTP verb the client used.
    pub verb: String,
    /// Which collection the resource belongs to.
    pub resource_type: String,
    /// The resource's id.
    pub resource_id: String,
    /// The request body, verbatim.
    pub body_text: String,
    /// Correlates the reply.
    pub request_id: u64,
}

impl Forward {
    /// The frame type that carries this message.
    pub const TYPE: MessageType = MessageType::Forward;

    /// This message's payload bytes.
    #[must_use]
    pub fn encode(&self) -> Vec<u8> {
        Writer::new()
            .string(1, &self.verb)
            .string(2, &self.resource_type)
            .string(3, &self.resource_id)
            .string(4, &self.body_text)
            .uint(5, self.request_id)
            .take()
    }

    /// Reads a payload back.
    ///
    /// # Errors
    ///
    /// [`RaftProtocolError`] if the payload is malformed.
    pub fn decode(payload: &[u8]) -> Result<Self, RaftProtocolError> {
        let mut message = Self {
            verb: String::new(),
            resource_type: String::new(),
            resource_id: String::new(),
            body_text: String::new(),
            request_id: 0,
        };
        let mut reader = Reader::new(payload);
        while let Some((number, wire)) = reader.next_field()? {
            match number {
                1 => message.verb = reader.string()?,
                2 => message.resource_type = reader.string()?,
                3 => message.resource_id = reader.string()?,
                4 => message.body_text = reader.string()?,
                5 => message.request_id = reader.uint()?,
                _ => reader.skip(wire)?,
            }
        }
        Ok(message)
    }
}

/// The owner's answer, plus the index the asker must catch up to.
///
/// `applied_index` is what makes read-your-write survive the hop: the member
/// that answered the client may not have applied the entry yet, and a client
/// that immediately reads back from it would otherwise get a 404 for something
/// it was just told was created.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ForwardReply {
    /// Whether the mutation succeeded.
    pub ok: bool,
    /// Whether it created the resource, as opposed to updating it.
    pub created: bool,
    /// The error code, when it failed.
    pub error: String,
    /// The error detail, when it failed.
    pub detail: String,
    /// The index the asker must reach before answering the client.
    pub applied_index: u64,
    /// This member does not own the Node after all.
    pub not_owner: bool,
    /// Echoes the request's id.
    pub request_id: u64,
    /// Who does own it, when this member knows.
    ///
    /// `None` rather than zero: member 0 is a real member.
    pub owner: Option<u64>,
}

impl ForwardReply {
    /// The frame type that carries this message.
    pub const TYPE: MessageType = MessageType::ForwardReply;

    /// This message's payload bytes.
    #[must_use]
    pub fn encode(&self) -> Vec<u8> {
        let writer = Writer::new()
            .bool(1, self.ok)
            .bool(2, self.created)
            .string(3, &self.error)
            .string(4, &self.detail)
            .uint(5, self.applied_index)
            .bool(6, self.not_owner)
            .uint(7, self.request_id);
        match self.owner {
            Some(owner) => writer.uint(8, owner).take(),
            None => writer.take(),
        }
    }

    /// Reads a payload back.
    ///
    /// # Errors
    ///
    /// [`RaftProtocolError`] if the payload is malformed.
    pub fn decode(payload: &[u8]) -> Result<Self, RaftProtocolError> {
        let mut message = Self {
            ok: false,
            created: false,
            error: String::new(),
            detail: String::new(),
            applied_index: 0,
            not_owner: false,
            request_id: 0,
            owner: None,
        };
        let mut reader = Reader::new(payload);
        while let Some((number, wire)) = reader.next_field()? {
            match number {
                1 => message.ok = reader.bool()?,
                2 => message.created = reader.bool()?,
                3 => message.error = reader.string()?,
                4 => message.detail = reader.string()?,
                5 => message.applied_index = reader.uint()?,
                6 => message.not_owner = reader.bool()?,
                7 => message.request_id = reader.uint()?,
                8 => message.owner = Some(reader.uint()?),
                _ => reader.skip(wire)?,
            }
        }
        Ok(message)
    }
}

/// Liveness on an otherwise quiet link.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Ping {
    /// Echoed back, to match a [`Pong`] to its [`Ping`].
    pub nonce: u64,
}

impl Ping {
    /// The frame type that carries this message.
    pub const TYPE: MessageType = MessageType::Ping;

    /// This message's payload bytes.
    #[must_use]
    pub fn encode(&self) -> Vec<u8> {
        Writer::new().uint(1, self.nonce).take()
    }

    /// Reads a payload back.
    ///
    /// # Errors
    ///
    /// [`RaftProtocolError`] if the payload is malformed.
    pub fn decode(payload: &[u8]) -> Result<Self, RaftProtocolError> {
        Ok(Self {
            nonce: decode_nonce(payload)?,
        })
    }
}

/// The echo of a [`Ping`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Pong {
    /// The nonce from the [`Ping`] this answers.
    pub nonce: u64,
}

impl Pong {
    /// The frame type that carries this message.
    pub const TYPE: MessageType = MessageType::Pong;

    /// This message's payload bytes.
    #[must_use]
    pub fn encode(&self) -> Vec<u8> {
        Writer::new().uint(1, self.nonce).take()
    }

    /// Reads a payload back.
    ///
    /// # Errors
    ///
    /// [`RaftProtocolError`] if the payload is malformed.
    pub fn decode(payload: &[u8]) -> Result<Self, RaftProtocolError> {
        Ok(Self {
            nonce: decode_nonce(payload)?,
        })
    }
}

/// Any message a peer can send.
///
/// This is what `BY_TYPE` is in the Python: the dispatch table that turns a
/// frame's type byte into a decoded message. Here it is an enum rather than a
/// map, which gets what the Python's `test_messages.py` has to assert -- that
/// every [`MessageType`] is handled -- from the compiler instead, since
/// [`decode_message`] matches exhaustively.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Message {
    /// See [`Hello`].
    Hello(Hello),
    /// See [`HelloAck`].
    HelloAck(HelloAck),
    /// See [`RequestVote`].
    RequestVote(RequestVote),
    /// See [`RequestVoteReply`].
    RequestVoteReply(RequestVoteReply),
    /// See [`AppendEntries`].
    AppendEntries(AppendEntries),
    /// See [`AppendEntriesReply`].
    AppendEntriesReply(AppendEntriesReply),
    /// See [`InstallSnapshot`].
    InstallSnapshot(InstallSnapshot),
    /// See [`InstallSnapshotReply`].
    InstallSnapshotReply(InstallSnapshotReply),
    /// See [`Promote`].
    Promote(Promote),
    /// See [`Propose`].
    Propose(Propose),
    /// See [`ProposeReply`].
    ProposeReply(ProposeReply),
    /// See [`Forward`].
    Forward(Forward),
    /// See [`ForwardReply`].
    ForwardReply(ForwardReply),
    /// See [`Ping`].
    Ping(Ping),
    /// See [`Pong`].
    Pong(Pong),
}

impl Message {
    /// The frame type that carries this message.
    #[must_use]
    pub const fn message_type(&self) -> MessageType {
        match *self {
            Self::Hello(_) => MessageType::Hello,
            Self::HelloAck(_) => MessageType::HelloAck,
            Self::RequestVote(_) => MessageType::RequestVote,
            Self::RequestVoteReply(_) => MessageType::RequestVoteReply,
            Self::AppendEntries(_) => MessageType::AppendEntries,
            Self::AppendEntriesReply(_) => MessageType::AppendEntriesReply,
            Self::InstallSnapshot(_) => MessageType::InstallSnapshot,
            Self::InstallSnapshotReply(_) => MessageType::InstallSnapshotReply,
            Self::Promote(_) => MessageType::Promote,
            Self::Propose(_) => MessageType::Propose,
            Self::ProposeReply(_) => MessageType::ProposeReply,
            Self::Forward(_) => MessageType::Forward,
            Self::ForwardReply(_) => MessageType::ForwardReply,
            Self::Ping(_) => MessageType::Ping,
            Self::Pong(_) => MessageType::Pong,
        }
    }

    /// This message's payload bytes.
    #[must_use]
    pub fn encode(&self) -> Vec<u8> {
        match *self {
            Self::Hello(ref m) => m.encode(),
            Self::HelloAck(ref m) => m.encode(),
            Self::RequestVote(ref m) => m.encode(),
            Self::RequestVoteReply(ref m) => m.encode(),
            Self::AppendEntries(ref m) => m.encode(),
            Self::AppendEntriesReply(ref m) => m.encode(),
            Self::InstallSnapshot(ref m) => m.encode(),
            Self::InstallSnapshotReply(ref m) => m.encode(),
            Self::Promote(ref m) => m.encode(),
            Self::Propose(ref m) => m.encode(),
            Self::ProposeReply(ref m) => m.encode(),
            Self::Forward(ref m) => m.encode(),
            Self::ForwardReply(ref m) => m.encode(),
            Self::Ping(ref m) => m.encode(),
            Self::Pong(ref m) => m.encode(),
        }
    }
}

/// Decode a payload according to its frame's type byte.
///
/// # Errors
///
/// [`RaftProtocolError`] if the payload is malformed for the type it claims.
pub fn decode_message(
    message_type: MessageType,
    payload: &[u8],
) -> Result<Message, RaftProtocolError> {
    Ok(match message_type {
        MessageType::Hello => Message::Hello(Hello::decode(payload)?),
        MessageType::HelloAck => Message::HelloAck(HelloAck::decode(payload)?),
        MessageType::RequestVote => Message::RequestVote(RequestVote::decode(payload)?),
        MessageType::RequestVoteReply => {
            Message::RequestVoteReply(RequestVoteReply::decode(payload)?)
        }
        MessageType::AppendEntries => Message::AppendEntries(AppendEntries::decode(payload)?),
        MessageType::AppendEntriesReply => {
            Message::AppendEntriesReply(AppendEntriesReply::decode(payload)?)
        }
        MessageType::InstallSnapshot => Message::InstallSnapshot(InstallSnapshot::decode(payload)?),
        MessageType::InstallSnapshotReply => {
            Message::InstallSnapshotReply(InstallSnapshotReply::decode(payload)?)
        }
        MessageType::Promote => Message::Promote(Promote::decode(payload)?),
        MessageType::Propose => Message::Propose(Propose::decode(payload)?),
        MessageType::ProposeReply => Message::ProposeReply(ProposeReply::decode(payload)?),
        MessageType::Forward => Message::Forward(Forward::decode(payload)?),
        MessageType::ForwardReply => Message::ForwardReply(ForwardReply::decode(payload)?),
        MessageType::Ping => Message::Ping(Ping::decode(payload)?),
        MessageType::Pong => Message::Pong(Pong::decode(payload)?),
    })
}

/// [`Ping`] and [`Pong`] are the same one field; sharing the reader avoids two
/// copies of a loop that must stay identical.
fn decode_nonce(payload: &[u8]) -> Result<u64, RaftProtocolError> {
    let mut nonce = 0;
    let mut reader = Reader::new(payload);
    while let Some((number, wire)) = reader.next_field()? {
        if number == 1 {
            nonce = reader.uint()?;
        } else {
            reader.skip(wire)?;
        }
    }
    Ok(nonce)
}
