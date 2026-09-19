# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Every peer message, and the one place field numbers are assigned.

Part of the specification, with ``wire.py``. Each message is a frozen
dataclass with an ``encode``/``decode`` pair, and the field numbers are
**permanent**: a number, once used for a meaning, is never reused for another,
because a member running an older build will skip a field it does not
recognise rather than reject it, and a reused number would be skipped as the
wrong thing instead.

Three groups:

**Consensus** -- ``RequestVote``, ``AppendEntries``, ``InstallSnapshot`` and
their replies, plus ``Promote``. Textbook Raft, with two additions that are
not textbook and are called out where they appear: ``voting`` and
``catching_up``, which together implement the non-voting rejoin that a
volatile log makes necessary (see ``persist.py``).

**Application** -- ``Propose`` and ``Forward``. Neither is a Raft message.
``Propose`` carries a follower's batch to the leader for appending;
``Forward`` hands a whole registry mutation to the member that owns the Node
it targets. They travel on the same transport because they have the same
peers, not because they are the same kind of thing.

**Liveness** -- ``Hello`` and its acknowledgement gate the connection;
``Ping``/``Pong`` measure a link that is otherwise quiet.

Absent values
-------------
Protobuf's convention is that an unset field reads as zero, and several fields
here are legitimately zero -- term 0, member index 0, an empty entry list. So
where "absent" must be distinguishable from "zero", the field is encoded as
*present-or-not* and decoded into ``None``: ``RequestVoteReply.leader`` is the
example. Member indices start at 0, so a falsy check would read "member 0" as
"nobody", which is exactly the confusion that loses a vote.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nmos.raft.errors import RaftProtocolError
from nmos.raft.wire import (
    MessageType,
    Reader,
    Stream,
    Writer,
)


@dataclass(frozen=True)
class Hello:
    """Opens a link and proves both ends belong to the same cluster.

    ``incarnation`` is the load-bearing field. It increments every time a
    member starts (``persist.py``), so a leader that sees a peer's incarnation
    change knows that peer has been restarted and has come back with an empty
    log -- which is the signal to catch it up and withhold its vote until it is
    promoted, rather than counting it toward quorum immediately.
    """

    major: int
    minor: int
    cluster_id: str
    member_name: str
    member_index: int
    incarnation: int
    stream: Stream

    TYPE = MessageType.HELLO

    def encode(self) -> bytes:
        return (
            Writer()
            .uint(1, self.major)
            .uint(2, self.minor)
            .string(3, self.cluster_id)
            .string(4, self.member_name)
            .uint(5, self.member_index)
            .uint(6, self.incarnation)
            .uint(7, int(self.stream))
            .take()
        )

    @classmethod
    def decode(cls, payload: bytes) -> Hello:
        values: dict[str, Any] = {
            "major": 0, "minor": 0, "cluster_id": "", "member_name": "",
            "member_index": 0, "incarnation": 0, "stream": Stream.CONTROL,
        }
        reader = Reader(payload)
        for number, wire in reader:
            if number == 1:
                values["major"] = reader.uint()
            elif number == 2:
                values["minor"] = reader.uint()
            elif number == 3:
                values["cluster_id"] = reader.string()
            elif number == 4:
                values["member_name"] = reader.string()
            elif number == 5:
                values["member_index"] = reader.uint()
            elif number == 6:
                values["incarnation"] = reader.uint()
            elif number == 7:
                values["stream"] = _stream(reader.uint())
            else:
                reader.skip(wire)
        return cls(**values)


@dataclass(frozen=True)
class HelloAck:
    """Accepts or refuses a link, saying why when it refuses."""

    accepted: bool
    reason: str
    minor: int
    member_index: int
    incarnation: int

    TYPE = MessageType.HELLO_ACK

    def encode(self) -> bytes:
        return (
            Writer()
            .bool_(1, self.accepted)
            .string(2, self.reason)
            .uint(3, self.minor)
            .uint(4, self.member_index)
            .uint(5, self.incarnation)
            .take()
        )

    @classmethod
    def decode(cls, payload: bytes) -> HelloAck:
        values: dict[str, Any] = {
            "accepted": False, "reason": "", "minor": 0,
            "member_index": 0, "incarnation": 0,
        }
        reader = Reader(payload)
        for number, wire in reader:
            if number == 1:
                values["accepted"] = reader.bool_()
            elif number == 2:
                values["reason"] = reader.string()
            elif number == 3:
                values["minor"] = reader.uint()
            elif number == 4:
                values["member_index"] = reader.uint()
            elif number == 5:
                values["incarnation"] = reader.uint()
            else:
                reader.skip(wire)
        return cls(**values)


@dataclass(frozen=True)
class WireEntry:
    """One log entry as it travels: term, index, and the operation's bytes.

    The decoded operation is deliberately not here. It is reconstructed on
    receipt so that a malformed payload is rejected at the edge, by the code
    that can still drop the link, rather than inside apply -- which runs
    synchronously, mutates the store, and has nowhere good to fail.
    """

    term: int
    index: int
    payload: bytes

    def encode(self) -> bytes:
        return (
            Writer()
            .uint(1, self.term)
            .uint(2, self.index)
            .bytes_(3, self.payload)
            .take()
        )

    @classmethod
    def decode(cls, payload: bytes) -> WireEntry:
        term = index = 0
        body = b""
        reader = Reader(payload)
        for number, wire in reader:
            if number == 1:
                term = reader.uint()
            elif number == 2:
                index = reader.uint()
            elif number == 3:
                body = reader.bytes_()
            else:
                reader.skip(wire)
        return cls(term=term, index=index, payload=body)


@dataclass(frozen=True)
class RequestVote:
    """Raft §5.2. Ask a peer for its vote in ``term``.

    Two fields beyond the paper's, both serving the recovery path described in
    ``node.py`` under "when every voter has forgotten".

    ``probe`` asks a peer only to state whether it can vote, never for the vote
    itself. A member that came back from a restart needs to know how many of
    its peers are in the same condition before it may do anything about it, and
    without a probe the only way to ask would be to stand for election -- which
    inflates the term on every attempt and, in the state this exists to escape,
    can never succeed.

    ``amnesiac`` carries the evidence: the members this candidate has *itself
    observed* answering ``voting=False``. A voter that has lost its log grants
    nothing on trust; it re-does the arithmetic on this list plus its own
    status, and grants only when the two together prove that no quorum of
    voters can exist. Members the candidate has not heard from are absent from
    the list and therefore counted as voters -- which is what stops a partition
    looking like an empty cluster.
    """

    term: int
    candidate: int
    last_log_index: int
    last_log_term: int
    probe: bool = False
    amnesiac: tuple[int, ...] = ()
    pre_vote: bool = False
    """Raft §9.6, and etcd's ``MsgPreVote``: "would you vote for me?".

    ``term`` then carries the term the candidate *would* stand in -- its own
    plus one -- while the candidate's own term stays where it is. A voter
    answers without changing its term, its recorded vote or anything on disk,
    which is the whole point: a member that has lost contact can find out
    whether it could win before inflicting a term increment on a cluster that
    is working perfectly well without it.
    """

    TYPE = MessageType.REQUEST_VOTE

    def encode(self) -> bytes:
        writer = (
            Writer()
            .uint(1, self.term)
            .uint(2, self.candidate)
            .uint(3, self.last_log_index)
            .uint(4, self.last_log_term)
            .bool_(5, self.probe)
        )
        for member in self.amnesiac:
            writer.uint(6, member)
        writer.bool_(7, self.pre_vote)
        return writer.take()

    @classmethod
    def decode(cls, payload: bytes) -> RequestVote:
        term = candidate = last_index = last_term = 0
        probe = False
        pre_vote = False
        amnesiac: list[int] = []
        reader = Reader(payload)
        for number, wire in reader:
            if number == 1:
                term = reader.uint()
            elif number == 2:
                candidate = reader.uint()
            elif number == 3:
                last_index = reader.uint()
            elif number == 4:
                last_term = reader.uint()
            elif number == 5:
                probe = reader.bool_()
            elif number == 6:
                amnesiac.append(reader.uint())
            elif number == 7:
                pre_vote = reader.bool_()
            else:
                reader.skip(wire)
        return cls(
            term=term, candidate=candidate,
            last_log_index=last_index, last_log_term=last_term,
            probe=probe, amnesiac=tuple(amnesiac), pre_vote=pre_vote,
        )


@dataclass(frozen=True)
class RequestVoteReply:
    """Grant or refuse, and say whether this voter counts at all.

    ``voting`` is the addition. A member that has restarted comes back with an
    empty log, and an empty log considers every candidate up to date -- so it
    would grant any vote it was asked for, defeating the check that stops a
    candidate missing committed entries from winning. Until the leader has
    caught it up and promoted it, it answers ``voting=False`` and the candidate
    does not count it toward a majority.
    """

    term: int
    granted: bool
    voting: bool
    pre_vote: bool = False
    """Which question this answers.

    Load-bearing, not decoration: a granted pre-vote carries the *prospective*
    term and a refused one carries the voter's own, so a candidate comparing
    terms alone could not tell a pre-vote reply from a real one -- and would
    either count a pre-vote as a vote or step down from its own proposal.
    """

    TYPE = MessageType.REQUEST_VOTE_REPLY

    def encode(self) -> bytes:
        return (
            Writer()
            .uint(1, self.term)
            .bool_(2, self.granted)
            .bool_(3, self.voting)
            .bool_(4, self.pre_vote)
            .take()
        )

    @classmethod
    def decode(cls, payload: bytes) -> RequestVoteReply:
        term = 0
        granted = False
        voting = False
        pre_vote = False
        reader = Reader(payload)
        for number, wire in reader:
            if number == 1:
                term = reader.uint()
            elif number == 2:
                granted = reader.bool_()
            elif number == 3:
                voting = reader.bool_()
            elif number == 4:
                pre_vote = reader.bool_()
            else:
                reader.skip(wire)
        return cls(
            term=term, granted=granted, voting=voting, pre_vote=pre_vote,
        )


@dataclass(frozen=True)
class AppendEntries:
    """Raft §5.3, and the heartbeat when ``entries`` is empty."""

    term: int
    leader: int
    prev_log_index: int
    prev_log_term: int
    leader_commit: int
    request_id: int
    entries: tuple[WireEntry, ...] = ()

    TYPE = MessageType.APPEND_ENTRIES

    def encode(self) -> bytes:
        writer = (
            Writer()
            .uint(1, self.term)
            .uint(2, self.leader)
            .uint(3, self.prev_log_index)
            .uint(4, self.prev_log_term)
            .uint(5, self.leader_commit)
            .uint(6, self.request_id)
        )
        for entry in self.entries:
            writer.bytes_(7, entry.encode())
        return writer.take()

    @classmethod
    def decode(cls, payload: bytes) -> AppendEntries:
        term = leader = prev_index = prev_term = commit = request_id = 0
        entries: list[WireEntry] = []
        reader = Reader(payload)
        for number, wire in reader:
            if number == 1:
                term = reader.uint()
            elif number == 2:
                leader = reader.uint()
            elif number == 3:
                prev_index = reader.uint()
            elif number == 4:
                prev_term = reader.uint()
            elif number == 5:
                commit = reader.uint()
            elif number == 6:
                request_id = reader.uint()
            elif number == 7:
                entries.append(WireEntry.decode(reader.bytes_()))
            else:
                reader.skip(wire)
        return cls(
            term=term, leader=leader, prev_log_index=prev_index,
            prev_log_term=prev_term, leader_commit=commit,
            request_id=request_id, entries=tuple(entries),
        )


@dataclass(frozen=True)
class AppendEntriesReply:
    """Accept or reject, with enough detail to resume in one step.

    ``conflict_index``/``conflict_term`` are the fast-backtrack optimisation:
    a naive Raft walks back one index per round trip, which for a member
    rejoining a busy registry is a round trip per committed entry. Reporting
    the start of the conflicting term instead lets the leader skip the whole
    run at once.

    ``catching_up`` is the other half of the non-voting rejoin: a restarted
    member acknowledges nothing toward quorum until it has been promoted, so
    the leader must not count this reply when deciding what is committed.
    """

    term: int
    success: bool
    match_index: int
    conflict_index: int
    conflict_term: int
    catching_up: bool
    request_id: int

    TYPE = MessageType.APPEND_ENTRIES_REPLY

    def encode(self) -> bytes:
        return (
            Writer()
            .uint(1, self.term)
            .bool_(2, self.success)
            .uint(3, self.match_index)
            .uint(4, self.conflict_index)
            .uint(5, self.conflict_term)
            .bool_(6, self.catching_up)
            .uint(7, self.request_id)
            .take()
        )

    @classmethod
    def decode(cls, payload: bytes) -> AppendEntriesReply:
        term = match = conflict_index = conflict_term = request_id = 0
        success = catching_up = False
        reader = Reader(payload)
        for number, wire in reader:
            if number == 1:
                term = reader.uint()
            elif number == 2:
                success = reader.bool_()
            elif number == 3:
                match = reader.uint()
            elif number == 4:
                conflict_index = reader.uint()
            elif number == 5:
                conflict_term = reader.uint()
            elif number == 6:
                catching_up = reader.bool_()
            elif number == 7:
                request_id = reader.uint()
            else:
                reader.skip(wire)
        return cls(
            term=term, success=success, match_index=match,
            conflict_index=conflict_index, conflict_term=conflict_term,
            catching_up=catching_up, request_id=request_id,
        )


@dataclass(frozen=True)
class InstallSnapshot:
    """A chunk of a snapshot, for a follower too far behind to replicate to.

    Travels on the BULK stream. A snapshot is the whole registry serialised,
    and sharing a link with heartbeats would let a large transfer stall the
    timer that prevents elections -- so a snapshot install would cause
    leadership churn, and the churn would be blamed on load.
    """

    term: int
    leader: int
    last_index: int
    last_term: int
    offset: int
    data: bytes
    done: bool
    ownership: bytes = b""

    TYPE = MessageType.INSTALL_SNAPSHOT

    def encode(self) -> bytes:
        return (
            Writer()
            .uint(1, self.term)
            .uint(2, self.leader)
            .uint(3, self.last_index)
            .uint(4, self.last_term)
            .uint(5, self.offset)
            .bytes_(6, self.ownership)
            .bytes_(7, self.data)
            .bool_(8, self.done)
            .take()
        )

    @classmethod
    def decode(cls, payload: bytes) -> InstallSnapshot:
        term = leader = last_index = last_term = offset = 0
        data = ownership = b""
        done = False
        reader = Reader(payload)
        for number, wire in reader:
            if number == 1:
                term = reader.uint()
            elif number == 2:
                leader = reader.uint()
            elif number == 3:
                last_index = reader.uint()
            elif number == 4:
                last_term = reader.uint()
            elif number == 5:
                offset = reader.uint()
            elif number == 6:
                ownership = reader.bytes_()
            elif number == 7:
                data = reader.bytes_()
            elif number == 8:
                done = reader.bool_()
            else:
                reader.skip(wire)
        return cls(
            term=term, leader=leader, last_index=last_index,
            last_term=last_term, offset=offset, data=data, done=done,
            ownership=ownership,
        )


@dataclass(frozen=True)
class InstallSnapshotReply:
    """How much of the snapshot has landed, so the leader can resume."""

    term: int
    bytes_received: int
    done: bool

    TYPE = MessageType.INSTALL_SNAPSHOT_REPLY

    def encode(self) -> bytes:
        return (
            Writer()
            .uint(1, self.term)
            .uint(2, self.bytes_received)
            .bool_(3, self.done)
            .take()
        )

    @classmethod
    def decode(cls, payload: bytes) -> InstallSnapshotReply:
        term = received = 0
        done = False
        reader = Reader(payload)
        for number, wire in reader:
            if number == 1:
                term = reader.uint()
            elif number == 2:
                received = reader.uint()
            elif number == 3:
                done = reader.bool_()
            else:
                reader.skip(wire)
        return cls(term=term, bytes_received=received, done=done)


@dataclass(frozen=True)
class Promote:
    """The leader telling a caught-up member that its vote now counts.

    Sent once ``match_index`` has reached the commit index the leader held when
    it first heard from this peer again. Until then the peer has an empty or
    partial log and must not participate in elections.
    """

    term: int
    leader: int
    through_index: int

    TYPE = MessageType.PROMOTE

    def encode(self) -> bytes:
        return (
            Writer()
            .uint(1, self.term)
            .uint(2, self.leader)
            .uint(3, self.through_index)
            .take()
        )

    @classmethod
    def decode(cls, payload: bytes) -> Promote:
        term = leader = through = 0
        reader = Reader(payload)
        for number, wire in reader:
            if number == 1:
                term = reader.uint()
            elif number == 2:
                leader = reader.uint()
            elif number == 3:
                through = reader.uint()
            else:
                reader.skip(wire)
        return cls(term=term, leader=leader, through_index=through)


@dataclass(frozen=True)
class Propose:
    """A follower's batch of operations, for the leader to append.

    Batched by construction: the field repeats, and everything a member
    proposed within one event-loop tick travels in one message. That is the
    whole of the batching design on the wire -- one message per tick per peer,
    however many registrations arrived.
    """

    proposals: tuple[bytes, ...]
    request_id: int

    TYPE = MessageType.PROPOSE

    def encode(self) -> bytes:
        writer = Writer().uint(1, self.request_id)
        for proposal in self.proposals:
            writer.bytes_(2, proposal)
        return writer.take()

    @classmethod
    def decode(cls, payload: bytes) -> Propose:
        request_id = 0
        proposals: list[bytes] = []
        reader = Reader(payload)
        for number, wire in reader:
            if number == 1:
                request_id = reader.uint()
            elif number == 2:
                proposals.append(reader.bytes_())
            else:
                reader.skip(wire)
        return cls(proposals=tuple(proposals), request_id=request_id)


@dataclass(frozen=True)
class ProposeReply:
    """Accepted and assigned an index, or refused with the leader's identity.

    Not on the critical path: the proposer learns its operation committed by
    applying it, like every other member. This exists to reject promptly when
    this member is not the leader, and to let the proposer map its batch to
    indices for diagnostics.
    """

    accepted: bool
    reason: str
    term: int
    first_index: int
    request_id: int
    leader: int | None = None

    TYPE = MessageType.PROPOSE_REPLY

    def encode(self) -> bytes:
        writer = (
            Writer()
            .bool_(1, self.accepted)
            .string(2, self.reason)
            .uint(3, self.term)
            .uint(4, self.first_index)
            .uint(5, self.request_id)
        )
        # Present-or-absent, because member 0 is a real member: encoding a
        # sentinel zero would name the first member as leader whenever none
        # was known.
        if self.leader is not None:
            writer.uint(6, self.leader)
        return writer.take()

    @classmethod
    def decode(cls, payload: bytes) -> ProposeReply:
        accepted = False
        reason = ""
        term = first_index = request_id = 0
        leader: int | None = None
        reader = Reader(payload)
        for number, wire in reader:
            if number == 1:
                accepted = reader.bool_()
            elif number == 2:
                reason = reader.string()
            elif number == 3:
                term = reader.uint()
            elif number == 4:
                first_index = reader.uint()
            elif number == 5:
                request_id = reader.uint()
            elif number == 6:
                leader = reader.uint()
            else:
                reader.skip(wire)
        return cls(
            accepted=accepted, reason=reason, term=term,
            first_index=first_index, request_id=request_id, leader=leader,
        )


@dataclass(frozen=True)
class Forward:
    """A whole registry mutation, handed to the member that owns its Node.

    ``body_text`` is the request body **verbatim**. The registry's fidelity
    guarantee is that what a client registered is what every member serves,
    byte for byte, including vendor extensions the type layer does not model --
    so re-encoding it here, even through a round trip that looks lossless,
    would break that guarantee at exactly the point nobody would look.
    """

    verb: str
    resource_type: str
    resource_id: str
    body_text: str
    request_id: int

    TYPE = MessageType.FORWARD

    def encode(self) -> bytes:
        return (
            Writer()
            .string(1, self.verb)
            .string(2, self.resource_type)
            .string(3, self.resource_id)
            .string(4, self.body_text)
            .uint(5, self.request_id)
            .take()
        )

    @classmethod
    def decode(cls, payload: bytes) -> Forward:
        verb = resource_type = resource_id = body_text = ""
        request_id = 0
        reader = Reader(payload)
        for number, wire in reader:
            if number == 1:
                verb = reader.string()
            elif number == 2:
                resource_type = reader.string()
            elif number == 3:
                resource_id = reader.string()
            elif number == 4:
                body_text = reader.string()
            elif number == 5:
                request_id = reader.uint()
            else:
                reader.skip(wire)
        return cls(
            verb=verb, resource_type=resource_type, resource_id=resource_id,
            body_text=body_text, request_id=request_id,
        )


@dataclass(frozen=True)
class ForwardReply:
    """The owner's answer, plus the index the asker must catch up to.

    ``applied_index`` is what makes read-your-write survive the hop: the
    member that answered the client may not have applied the entry yet, and a
    client that immediately reads back from it would otherwise get a 404 for
    something it was just told was created.
    """

    ok: bool
    created: bool
    error: str
    detail: str
    applied_index: int
    not_owner: bool
    request_id: int
    owner: int | None = None

    TYPE = MessageType.FORWARD_REPLY

    def encode(self) -> bytes:
        writer = (
            Writer()
            .bool_(1, self.ok)
            .bool_(2, self.created)
            .string(3, self.error)
            .string(4, self.detail)
            .uint(5, self.applied_index)
            .bool_(6, self.not_owner)
            .uint(7, self.request_id)
        )
        if self.owner is not None:
            writer.uint(8, self.owner)
        return writer.take()

    @classmethod
    def decode(cls, payload: bytes) -> ForwardReply:
        ok = created = not_owner = False
        error = detail = ""
        applied_index = request_id = 0
        owner: int | None = None
        reader = Reader(payload)
        for number, wire in reader:
            if number == 1:
                ok = reader.bool_()
            elif number == 2:
                created = reader.bool_()
            elif number == 3:
                error = reader.string()
            elif number == 4:
                detail = reader.string()
            elif number == 5:
                applied_index = reader.uint()
            elif number == 6:
                not_owner = reader.bool_()
            elif number == 7:
                request_id = reader.uint()
            elif number == 8:
                owner = reader.uint()
            else:
                reader.skip(wire)
        return cls(
            ok=ok, created=created, error=error, detail=detail,
            applied_index=applied_index, not_owner=not_owner,
            request_id=request_id, owner=owner,
        )


@dataclass(frozen=True)
class Ping:
    """Liveness on an otherwise quiet link."""

    nonce: int
    TYPE = MessageType.PING

    def encode(self) -> bytes:
        return Writer().uint(1, self.nonce).take()

    @classmethod
    def decode(cls, payload: bytes) -> Ping:
        nonce = 0
        reader = Reader(payload)
        for number, wire in reader:
            if number == 1:
                nonce = reader.uint()
            else:
                reader.skip(wire)
        return cls(nonce=nonce)


@dataclass(frozen=True)
class Pong:
    """The echo of a :class:`Ping`."""

    nonce: int
    TYPE = MessageType.PONG

    def encode(self) -> bytes:
        return Writer().uint(1, self.nonce).take()

    @classmethod
    def decode(cls, payload: bytes) -> Pong:
        nonce = 0
        reader = Reader(payload)
        for number, wire in reader:
            if number == 1:
                nonce = reader.uint()
            else:
                reader.skip(wire)
        return cls(nonce=nonce)


def _stream(value: int) -> Stream:
    try:
        return Stream(value)
    except ValueError as exc:
        raise RaftProtocolError(f"unknown stream class {value}") from exc


# Every message, by the type byte that identifies it on the wire. Exhaustive
# by construction: ``test_messages.py`` asserts that every MessageType appears
# here, so adding a message without registering it fails immediately rather
# than at the first peer that sends one.
BY_TYPE: dict[MessageType, Any] = {
    MessageType.HELLO: Hello,
    MessageType.HELLO_ACK: HelloAck,
    MessageType.REQUEST_VOTE: RequestVote,
    MessageType.REQUEST_VOTE_REPLY: RequestVoteReply,
    MessageType.APPEND_ENTRIES: AppendEntries,
    MessageType.APPEND_ENTRIES_REPLY: AppendEntriesReply,
    MessageType.INSTALL_SNAPSHOT: InstallSnapshot,
    MessageType.INSTALL_SNAPSHOT_REPLY: InstallSnapshotReply,
    MessageType.PROMOTE: Promote,
    MessageType.PROPOSE: Propose,
    MessageType.PROPOSE_REPLY: ProposeReply,
    MessageType.FORWARD: Forward,
    MessageType.FORWARD_REPLY: ForwardReply,
    MessageType.PING: Ping,
    MessageType.PONG: Pong,
}


def decode_message(message_type: MessageType, payload: bytes) -> Any:
    """Decode a payload according to its frame's type byte."""
    try:
        cls = BY_TYPE[message_type]
    except KeyError as exc:  # pragma: no cover - BY_TYPE is exhaustive
        raise RaftProtocolError(
            f"no decoder registered for {message_type!r}",
        ) from exc
    decoded: Any = cls.decode(payload)
    return decoded


# ---------------------------------------------------------------------------
# Correlating a reply with the request that caused it
# ---------------------------------------------------------------------------

EXPECTED_REPLY: dict[MessageType, MessageType] = {
    MessageType.HELLO: MessageType.HELLO_ACK,
    MessageType.REQUEST_VOTE: MessageType.REQUEST_VOTE_REPLY,
    MessageType.APPEND_ENTRIES: MessageType.APPEND_ENTRIES_REPLY,
    MessageType.INSTALL_SNAPSHOT: MessageType.INSTALL_SNAPSHOT_REPLY,
    MessageType.PROPOSE: MessageType.PROPOSE_REPLY,
    MessageType.FORWARD: MessageType.FORWARD_REPLY,
    MessageType.PING: MessageType.PONG,
}
"""Which reply answers which request.

An id alone is not enough to correlate them, because two id spaces meet in one
``pending`` map: the transport mints ids for ``request``, while
``AppendEntries`` carries an id of the *leader's* own minting for flow control.
Both start at one and climb, so they collide -- most readily just after a
leader change, when a member that had been a follower has a low append sequence
and a low request id at the same time.

Matched on the number alone, an ``AppendEntriesReply`` can be handed to a
caller awaiting a ``ForwardReply``. That caller sees the wrong message and
gives up -- a registration refused with 503 -- and the append reply never
reaches the node, so the peer's ``match_index`` stalls for a tick. Two failures
from one number matching by accident.

Replies and one-way messages are absent: they answer nothing, so nothing may
wait on them.
"""
