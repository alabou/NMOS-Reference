# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Every message round-trips, and the registry of them is exhaustive.

Two classes of bug live here. The first is a field that encodes but does not
decode -- caught by round-tripping every message with every field set to
something distinguishable from its default, which is why none of the values
below are zero, empty or False unless the test is specifically about that.

The second is subtler: a field whose "absent" and "zero" cases are confused.
Member indices start at 0, so a ``leader`` field that encoded ``None`` as 0
would name the first member as leader whenever no leader was known. Those
fields get their own tests.
"""

from __future__ import annotations

import pytest

from nmos.raft.messages import (
    BY_TYPE,
    AppendEntries,
    AppendEntriesReply,
    Forward,
    ForwardReply,
    Hello,
    HelloAck,
    InstallSnapshot,
    InstallSnapshotReply,
    Ping,
    Promote,
    Propose,
    ProposeReply,
    RequestVote,
    RequestVoteReply,
    WireEntry,
    decode_message,
)
from nmos.raft.wire import MessageType, Stream, Writer

_SAMPLES = [
    Hello(
        major=1, minor=3, cluster_id="nmos-registry-abc123",
        member_name="nmos-registry-h0", member_index=2, incarnation=7,
        stream=Stream.BULK,
    ),
    HelloAck(
        accepted=True, reason="ok", minor=3, member_index=1, incarnation=4,
    ),
    RequestVote(term=5, candidate=2, last_log_index=90, last_log_term=4),
    RequestVoteReply(term=5, granted=True, voting=True),
    AppendEntries(
        term=9, leader=1, prev_log_index=42, prev_log_term=8,
        leader_commit=40, request_id=77,
        entries=(
            WireEntry(term=9, index=43, payload=b"\x01\x02"),
            WireEntry(term=9, index=44, payload=b"\x03"),
        ),
    ),
    AppendEntriesReply(
        term=9, success=True, match_index=44, conflict_index=0,
        conflict_term=0, catching_up=False, request_id=77,
    ),
    InstallSnapshot(
        term=4, leader=0, last_index=1000, last_term=3, offset=2048,
        data=b"\xde\xad\xbe\xef", done=True, ownership=b"owners",
    ),
    InstallSnapshotReply(term=4, bytes_received=2052, done=True),
    Promote(term=6, leader=1, through_index=500),
    Propose(proposals=(b"op-one", b"op-two", b"op-three"), request_id=11),
    ProposeReply(
        accepted=True, reason="", term=6, first_index=120, request_id=11,
        leader=1,
    ),
    Forward(
        verb="register", resource_type="sender",
        resource_id="8c4f1e2a-0000-4000-8000-000000000000",
        body_text='{"id":"8c4f1e2a","vendor:extension":true}', request_id=3,
    ),
    ForwardReply(
        ok=True, created=True, error="", detail="", applied_index=321,
        not_owner=False, request_id=3, owner=2,
    ),
    Ping(nonce=99),
]


@pytest.mark.parametrize(
    "message", _SAMPLES, ids=lambda m: type(m).__name__,
)
def test_every_message_round_trips(message: object) -> None:
    encoded = message.encode()  # type: ignore[attr-defined]
    assert decode_message(message.TYPE, encoded) == message  # type: ignore[attr-defined]


class TestRegistryIsExhaustive:
    def test_every_message_type_has_a_decoder(self) -> None:
        """Adding a message without registering it must fail here, not at a peer."""
        assert set(BY_TYPE) == set(MessageType)

    def test_every_decoder_reports_the_type_that_selects_it(self) -> None:
        for message_type, cls in BY_TYPE.items():
            assert cls.TYPE is message_type

    def test_every_sample_message_type_is_covered_except_pong(self) -> None:
        """Pong is Ping's twin; everything else needs its own round-trip."""
        covered = {type(sample).TYPE for sample in _SAMPLES}
        assert set(MessageType) - covered == {MessageType.PONG}


class TestAbsentVersusZero:
    """Member indices start at 0, so 'nobody' cannot be encoded as 0."""

    def test_propose_reply_without_a_known_leader(self) -> None:
        reply = ProposeReply(
            accepted=False, reason="not leader", term=3, first_index=0,
            request_id=1, leader=None,
        )
        assert decode_message(reply.TYPE, reply.encode()).leader is None

    def test_propose_reply_naming_member_zero(self) -> None:
        reply = ProposeReply(
            accepted=False, reason="not leader", term=3, first_index=0,
            request_id=1, leader=0,
        )
        assert decode_message(reply.TYPE, reply.encode()).leader == 0

    def test_forward_reply_without_a_known_owner(self) -> None:
        reply = ForwardReply(
            ok=False, created=False, error="", detail="", applied_index=0,
            not_owner=True, request_id=1, owner=None,
        )
        assert decode_message(reply.TYPE, reply.encode()).owner is None

    def test_forward_reply_naming_member_zero(self) -> None:
        reply = ForwardReply(
            ok=False, created=False, error="", detail="", applied_index=0,
            not_owner=True, request_id=1, owner=0,
        )
        assert decode_message(reply.TYPE, reply.encode()).owner == 0


class TestRepeatedFields:
    def test_an_append_with_no_entries_is_a_heartbeat(self) -> None:
        beat = AppendEntries(
            term=1, leader=0, prev_log_index=0, prev_log_term=0,
            leader_commit=0, request_id=1,
        )
        assert decode_message(beat.TYPE, beat.encode()).entries == ()

    def test_entry_order_is_preserved(self) -> None:
        """Replication is contiguous; reordering entries would break the log."""
        entries = tuple(
            WireEntry(term=2, index=i, payload=bytes([i])) for i in range(1, 30)
        )
        message = AppendEntries(
            term=2, leader=0, prev_log_index=0, prev_log_term=0,
            leader_commit=0, request_id=1, entries=entries,
        )
        assert decode_message(message.TYPE, message.encode()).entries == entries

    def test_a_propose_with_one_proposal(self) -> None:
        one = Propose(proposals=(b"solo",), request_id=5)
        assert decode_message(one.TYPE, one.encode()).proposals == (b"solo",)

    def test_an_empty_propose_round_trips(self) -> None:
        """Degenerate but decodable: rejecting it belongs in the caller."""
        empty = Propose(proposals=(), request_id=5)
        assert decode_message(empty.TYPE, empty.encode()).proposals == ()


class TestFidelity:
    def test_a_forwarded_body_survives_verbatim(self) -> None:
        """The registry's byte-for-byte guarantee has to survive the hop.

        A vendor extension the type layer does not model must reach the owner
        exactly as the client sent it, or the member that answers a later
        Query serves something the client never registered.
        """
        body = '{"id":"x","urn:x-vendor:odd":  [1,2,3] ,"label":"ünicode"}'
        message = Forward(
            verb="register", resource_type="sender", resource_id="x",
            body_text=body, request_id=1,
        )
        assert decode_message(message.TYPE, message.encode()).body_text == body

    def test_binary_snapshot_data_survives(self) -> None:
        payload = bytes(range(256))
        message = InstallSnapshot(
            term=1, leader=0, last_index=1, last_term=1, offset=0,
            data=payload, done=False,
        )
        assert decode_message(message.TYPE, message.encode()).data == payload


class TestForwardCompatibility:
    def test_an_unknown_field_is_ignored(self) -> None:
        """A newer peer's extra field must not drop the link.

        This is what makes a minor version bump additive rather than a flag
        day across the cluster.
        """
        extended = (
            Writer()
            .uint(1, 5)
            .uint(2, 2)
            .uint(3, 90)
            .uint(4, 4)
            .uint(64, 12345)
            .string(65, "a field from the future")
            .take()
        )
        decoded = decode_message(MessageType.REQUEST_VOTE, extended)
        assert decoded == RequestVote(
            term=5, candidate=2, last_log_index=90, last_log_term=4,
        )

    def test_a_missing_field_decodes_to_its_default(self) -> None:
        """An older peer's frame lacking a newer field must still decode."""
        minimal = Writer().uint(1, 5).take()
        decoded = decode_message(MessageType.REQUEST_VOTE, minimal)
        assert decoded == RequestVote(
            term=5, candidate=0, last_log_index=0, last_log_term=0,
        )


class TestTheNonVotingRejoinFields:
    """The two fields that are not textbook Raft, and why they exist."""

    def test_a_restarted_member_reports_that_it_cannot_vote(self) -> None:
        reply = RequestVoteReply(term=7, granted=False, voting=False)
        decoded = decode_message(reply.TYPE, reply.encode())
        assert decoded.voting is False
        assert decoded.granted is False

    def test_a_catching_up_member_reports_that_it_does_not_count(self) -> None:
        reply = AppendEntriesReply(
            term=7, success=True, match_index=10, conflict_index=0,
            conflict_term=0, catching_up=True, request_id=2,
        )
        decoded = decode_message(reply.TYPE, reply.encode())
        # Success AND catching_up together: the entries landed, but this
        # member's acknowledgement must not advance the commit index.
        assert decoded.success is True
        assert decoded.catching_up is True

    def test_the_incarnation_travels_in_the_handshake(self) -> None:
        hello = Hello(
            major=1, minor=0, cluster_id="c", member_name="m",
            member_index=1, incarnation=42, stream=Stream.CONTROL,
        )
        assert decode_message(hello.TYPE, hello.encode()).incarnation == 42
