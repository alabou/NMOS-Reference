# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""A connection that stops moving is closed, and what it held is never delivered.

Over a real network a connection can go quiet without breaking: a path that
stops carrying packets sends no reset, and TCP keeps whatever was written,
retransmitting it until the path comes back -- then delivers all of it, in
order, as if nothing had happened. A transport that waits for that has no bound
on how late a message arrives. The chaos soak measured writes whose clients had
been answered 503 arriving 4.6-26 s later and committing after those clients'
own confirmed deletes.

etcd bounds it (``rafthttp``): every peer connection carries a 5 s read and
write deadline, a stream writer sends a link heartbeat every third of that, and
a connection whose deadline passes is closed with its queue discarded -- so a
message arrives within about the deadline or not at all. These tests hold a
connection's bytes in the proxy the members dial through -- the model of a
stalled path -- and check what the transport does about it.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import socket
import ssl
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from nmos.raft.errors import MemberTask, RaftUnavailable, RaftUnexpectedError
from nmos.raft.messages import (
    AppendEntries,
    AppendEntriesReply,
    decode_message,
    Hello,
    HelloAck,
    InstallSnapshot,
    InstallSnapshotReply,
    Pong,
    Promote,
)
from nmos.api.tests._tls_helpers import PKI_AVAILABLE, build_server_ssl_context
from nmos.raft.tests._proxy import ProxyMesh
from nmos.raft.transport import DIAL_TIMEOUT, RaftTransport
from nmos.raft.wire import (
    encode_frame,
    FLAG_REPLY,
    Frame,
    MAX_FRAME,
    MessageType,
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    read_frame,
    Stream,
)

# Small, so the tests take seconds rather than etcd's 5 s multiples; the
# mechanism does not depend on the value.
READ_TIMEOUT = 0.5

# How long a stalled path holds what it was given: well past the deadline, so
# a transport that waits it out is told apart from one that does not.
HOLD = 2.0


class _Recorder:
    """The callbacks these tests watch; anything else would be a surprise."""

    def __init__(self) -> None:
        self.promotes: list[tuple[int, Promote]] = []
        self.states: list[tuple[float, int, bool]] = []
        # A defect the transport met (``PeerHandler.on_unexpected_error``);
        # recorded rather than stopping anything, so a test can assert none.
        self.failures: list[RaftUnexpectedError] = []

    def on_promote(self, peer: int, message: Promote) -> None:
        self.promotes.append((peer, message))

    def on_peer_state(self, peer: int, *, up: bool, incarnation: int) -> None:
        self.states.append((asyncio.get_running_loop().time(), peer, up))

    def on_unexpected_error(self, error: RaftUnexpectedError) -> None:
        self.failures.append(error)

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)


class _Saving(_Recorder):
    """A member whose term file can stop being writable.

    Appends and append replies then fail as its save would, with the
    ``OSError``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.saves_fail = False
        self.appends = 0
        self.append_replies = 0

    def _save(self) -> None:
        if self.saves_fail:
            raise OSError(28, "No space left on device")

    def on_append_entries(
        self, peer: int, message: AppendEntries,
    ) -> AppendEntriesReply:
        self.appends += 1
        self._save()
        return AppendEntriesReply(
            term=message.term, success=True,
            match_index=message.prev_log_index + len(message.entries),
            conflict_index=0, conflict_term=0, catching_up=False,
            request_id=message.request_id,
        )

    def on_append_entries_reply(
        self, peer: int, message: AppendEntriesReply,
    ) -> None:
        self.append_replies += 1
        self._save()


class _Pair:
    """Members 0 and 1 on loopback, each reaching the other only by proxy."""

    def __init__(
        self, recorders: tuple[_Recorder, _Recorder] | None = None,
    ) -> None:
        self._held: list[socket.socket] = []
        ports = sorted(self._reserve() for _ in range(2))
        self.mesh = ProxyMesh({0: ports[0], 1: ports[1]})
        self._ports = ports
        self.recorders = recorders or (_Recorder(), _Recorder())
        self.transports: tuple[RaftTransport, ...] = ()

    def _reserve(self) -> int:
        held = socket.socket()
        held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        held.bind(("127.0.0.1", 0))
        self._held.append(held)
        return int(held.getsockname()[1])

    async def start(self) -> None:
        await self.mesh.start()
        for held in self._held:
            held.close()
        self._held.clear()
        self.transports = tuple(
            RaftTransport(
                local=index,
                peers=self.mesh.dial_targets(index),
                bind=("127.0.0.1", self._ports[index]),
                cluster_id="liveness",
                member_name=f"m{index}",
                incarnation=1,
                rpc_timeout=1.0,
                conn_read_timeout=READ_TIMEOUT,
            )
            for index in range(2)
        )
        for transport, recorder in zip(self.transports, self.recorders):
            await transport.start(recorder)
        await self._until_linked()

    async def _until_linked(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 10.0
        while loop.time() < deadline:
            if 1 in self.transports[0].live and 0 in self.transports[1].live:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("the two members never linked up")

    def stall(self, source: int, target: int, seconds: float) -> None:
        """Hold every byte on ``source``'s connections to ``target``, both ways."""
        self.mesh._proxies[(source, target)].delay = seconds  # noqa: SLF001

    async def close(self) -> None:
        for transport in self.transports:
            with contextlib.suppress(Exception):
                await transport.close()
        await self.mesh.close()
        for held in self._held:
            held.close()


class TestAConnectionThatStopsMovingIsClosed:

    async def test_a_message_held_past_the_read_deadline_is_never_delivered(
        self,
    ) -> None:
        pair = _Pair()
        await pair.start()
        try:
            pair.stall(0, 1, HOLD)
            pair.transports[0].send(1, Promote(term=4, leader=0, through_index=9))
            # Long enough that the proxy has taken the frame and is holding
            # it; lifting the delay then frees only what comes after.
            await asyncio.sleep(0.1)
            pair.stall(0, 1, 0.0)
            await asyncio.sleep(HOLD + 1.0)

            assert not pair.recorders[1].promotes, (
                f"a message held {HOLD}s by a stalled path was delivered when "
                f"the path recovered, {HOLD / READ_TIMEOUT:.0f}x the read "
                f"deadline late: nothing bounds how late a message can arrive"
            )
        finally:
            await pair.close()

    async def test_a_link_that_stops_moving_is_reported_down_within_the_deadline(
        self,
    ) -> None:
        pair = _Pair()
        await pair.start()
        try:
            loop = asyncio.get_running_loop()
            stalled_at = loop.time()
            pair.stall(0, 1, 10 * HOLD)
            await asyncio.sleep(3 * READ_TIMEOUT)

            downs = [
                at - stalled_at
                for at, peer, up in pair.recorders[0].states
                if peer == 1 and not up and at >= stalled_at
            ]
            assert downs, (
                f"member 0's link to member 1 carried nothing for "
                f"{3 * READ_TIMEOUT}s and is still reported up"
            )
            # The deadline, plus up to one heartbeat interval for the last
            # frame before the stall to have been the latest one read.
            assert downs[0] <= READ_TIMEOUT + READ_TIMEOUT / 3 + 0.25, (
                f"reported down {downs[0]:.2f}s into the stall"
            )
        finally:
            await pair.close()

    async def test_an_idle_link_stays_up_past_the_read_deadline(self) -> None:
        # The heartbeat's guard: with nothing to send, a link must still carry
        # enough to keep both ends' reads inside the deadline.
        pair = _Pair()
        await pair.start()
        try:
            await asyncio.sleep(5 * READ_TIMEOUT)

            for member, recorder in enumerate(pair.recorders):
                downs = [state for state in recorder.states if not state[2]]
                assert not downs, (
                    f"member {member} saw an idle, healthy link go down: {downs}"
                )
        finally:
            await pair.close()


class _AnswersChunks(_Recorder):
    """A member that acknowledges every snapshot chunk it is sent, whole."""

    def on_install_snapshot(
        self, peer: int, message: InstallSnapshot,
    ) -> InstallSnapshotReply:
        return InstallSnapshotReply(
            term=message.term, bytes_received=message.offset + len(message.data),
            done=message.done, commit_index=0, request_id=message.request_id,
        )


class _SendsChunks(_Recorder):
    """A member that records what its chunks' answers said was received."""

    def __init__(self) -> None:
        super().__init__()
        self.answered = 0

    def on_install_snapshot_reply(
        self, peer: int, message: InstallSnapshotReply,
    ) -> None:
        self.answered = max(self.answered, message.bytes_received)


# A chunk as a leader sends one; its size is what a test makes slow.
_CHUNK = InstallSnapshot(
    term=3, leader=0, last_index=9, last_term=3, offset=0, data=b"x" * 16,
    done=False, ownership=b"", request_id=0,
)

# What a slowed path holds every piece it relays, both ways: a frame of many
# pieces crawls across, its reader seeing bytes well inside the deadline.
_TRICKLE = 0.15


class TestBothEndsOfAConnectionBeat:
    """Each end of a connection says something every third of the deadline.

    A connection carries requests one way and their answers the other, and the
    dialling end's reads were fed only by answers -- to its requests, and to
    its own heartbeat -- all of them queued behind whatever it was sending. So a
    frame taking longer than the deadline to cross, a snapshot chunk over a slow
    link, left its sender hearing nothing, and the sender closed a connection
    that was working: measured, a leader closing its BULK link every 5.07 s
    while one 64 KiB chunk crawled across at 8 KiB/s, never past that chunk
    (part 17 of the fix record). The accepting end now sends a heartbeat of
    its own, so each end's reads are fed by the other end's timer, whatever the
    other direction carries. etcd's streams each carry their own writer's
    heartbeat (``stream.go:169``), and its one connection that carries a single
    long message, the snapshot's, has no deadline at its sending end at all
    (``rafthttp/util.go:45-52``).
    """

    async def test_the_accepting_end_sends_its_own_heartbeat(self) -> None:
        held = socket.socket()
        held.bind(("127.0.0.1", 0))
        port = int(held.getsockname()[1])
        nowhere = socket.socket()
        nowhere.bind(("127.0.0.1", 0))
        held.close()
        member = RaftTransport(
            local=0, peers={1: ("127.0.0.1", int(nowhere.getsockname()[1]))},
            bind=("127.0.0.1", port), cluster_id="liveness", member_name="m0",
            incarnation=1, rpc_timeout=1.0, conn_read_timeout=READ_TIMEOUT,
        )
        await member.start(_Recorder())
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(encode_frame(Frame(
                stream=Stream.CONTROL, type=MessageType.HELLO, flags=0,
                payload=Hello(
                    major=PROTOCOL_MAJOR, minor=PROTOCOL_MINOR,
                    cluster_id="liveness", member_name="m1", member_index=1,
                    incarnation=1, stream=Stream.CONTROL,
                ).encode(),
            )))
            ack = await asyncio.wait_for(read_frame(reader), 2.0)
            assert ack.type is MessageType.HELLO_ACK
            assert decode_message(ack.type, ack.payload).accepted

            # Admitted -- and now say nothing, and listen.
            try:
                frame = await asyncio.wait_for(read_frame(reader), READ_TIMEOUT)
            except (
                asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError,
            ) as exc:
                pytest.fail(
                    f"the accepting end of a connection said nothing for "
                    f"{READ_TIMEOUT}s, the whole read deadline ({exc!r}): the "
                    f"dialling end's reads are fed only by answers, which queue "
                    f"behind whatever it is sending"
                )
            assert frame.type is MessageType.PONG and frame.is_reply, (
                f"the accepting end's heartbeat was a {frame.type.name}; a "
                f"Pong nobody asked for draws no answer"
            )
        finally:
            if writer is not None:
                writer.close()
            nowhere.close()
            await member.close()

    async def test_a_frame_slower_than_the_read_deadline_is_answered(self) -> None:
        # Sent as a leader sends a chunk, and answered as a member answers one:
        # on the connection it came by, to the sender's handler.
        sender = _SendsChunks()
        pair = _Pair(recorders=(sender, _AnswersChunks()))
        await pair.start()
        try:
            # BULK up, and answering, before anything is slowed.
            loop = asyncio.get_running_loop()
            warming = loop.time()
            while sender.answered < len(_CHUNK.data):
                assert loop.time() - warming < 5.0, "BULK never came up"
                pair.transports[0].send(1, _CHUNK, stream=Stream.BULK)
                await asyncio.sleep(0.05)

            # Half a megabyte crawls across in pieces, each held on the way,
            # the member reading some of it every ``_TRICKLE`` -- inside its
            # deadline -- while nothing it could answer comes back.
            pair.stall(0, 1, _TRICKLE)
            chunk = dataclasses.replace(_CHUNK, data=bytes(512 * 1024))
            began = loop.time()
            pair.transports[0].send(1, chunk, stream=Stream.BULK)
            arrived = await _until(
                lambda: sender.answered >= len(chunk.data), timeout=20.0,
            )
            took = loop.time() - began
            assert arrived, (
                f"a frame taking longer than the {READ_TIMEOUT}s read deadline "
                f"to cross was never answered in {took:.1f}s: its sender heard "
                f"nothing while it crossed, and closed a connection that was "
                f"working"
            )
            assert took > 2 * READ_TIMEOUT, (
                f"the frame crossed in {took:.2f}s, inside the deadline: this "
                f"proves nothing about one that does not"
            )
        finally:
            await pair.close()


class TestAChunkCanBeAwaited:
    """A snapshot chunk can be awaited like any request that carries an id.

    The node sends chunks fire-and-forget and handles their answers itself, but
    a chunk carries a correlation id, and ``request`` stamps and reads the id of
    any message with the field (``_with_request_id``, ``_resolve``). The Rust
    named the messages instead, and its list predated the chunk's id, so it
    refused to await one -- "carries no request_id and cannot be awaited" --
    until part 17 of the fix record made it match.
    """

    async def test_a_snapshot_chunk_can_be_awaited_as_a_correlated_request(
        self,
    ) -> None:
        pair = _Pair(recorders=(_Recorder(), _AnswersChunks()))
        await pair.start()
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 5.0
            while True:
                try:
                    reply = await pair.transports[0].request(
                        1, _CHUNK, timeout=2.0, stream=Stream.BULK,
                    )
                    break
                except RaftUnavailable as exc:
                    # BULK connects in its own time; until it has, there is no
                    # link to ask on.
                    if not str(exc).startswith("no link") or loop.time() > deadline:
                        pytest.fail(
                            f"a snapshot chunk could not be awaited as a request: "
                            f"{exc}"
                        )
                    await asyncio.sleep(0.02)

            assert isinstance(reply, InstallSnapshotReply), reply
            assert reply.bytes_received == len(_CHUNK.data)
            assert reply.request_id != 0, "the answer carries no correlation id"
        finally:
            await pair.close()


class TestAnOrderlyCloseIsNoError:
    """A peer that closes its end cleanly is a link going down, not a failure.

    The peer's close ends this member's read with ``IncompleteReadError`` --
    an ``EOFError``, not an ``OSError`` -- so it missed the branch that treats a
    connection lost as the ordinary event it is and reached the catch-all:
    "link to member N failed" at ERROR, with a traceback, in every run that
    stopped a member (every forwarding test, for one). The inbound side already
    treats it as silent, and so does the Rust transport, which ends such a link
    without a word (``pump``) and logs only a link that went silent.
    """

    async def test_a_peer_closing_its_end_is_not_logged_as_an_error(
        self, caplog: Any,
    ) -> None:
        pair = _Pair()
        await pair.start()
        try:
            caplog.set_level(logging.WARNING, logger="nmos.raft.transport")
            loop = asyncio.get_running_loop()
            closed_at = loop.time()
            await pair.transports[1].close()

            deadline = loop.time() + 5.0
            while loop.time() < deadline and not any(
                peer == 1 and not up and at >= closed_at
                for at, peer, up in pair.recorders[0].states
            ):
                await asyncio.sleep(0.01)
            assert any(
                peer == 1 and not up for _, peer, up in pair.recorders[0].states
            ), "member 0 never saw member 1 go"

            errors = [
                record.getMessage() for record in caplog.records
                if record.levelno >= logging.ERROR
            ]
            assert not errors, (
                f"a peer that closed its end cleanly was logged as an error: "
                f"{errors}"
            )
        finally:
            await pair.close()


async def _until(ready: Callable[[], bool], timeout: float = 5.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if ready():
            return True
        await asyncio.sleep(0.01)
    return ready()


def _down_since(recorder: _Recorder, peer: int, since: float) -> bool:
    """Whether ``recorder`` has seen its link to ``peer`` go down since then."""
    return any(
        who == peer and not up and at >= since
        for at, who, up in recorder.states
    )


def _failed_saves(records: list[logging.LogRecord]) -> list[tuple[str, OSError]]:
    """Every ERROR record carrying a save's ``OSError``, as (message, error)."""
    return [
        (record.getMessage(), record.exc_info[1])
        for record in records
        if record.levelno >= logging.ERROR
        and record.exc_info is not None
        and isinstance(record.exc_info[1], OSError)
    ]


_HEARTBEAT = AppendEntries(
    term=3, leader=0, prev_log_index=0, prev_log_term=0, leader_commit=0,
    request_id=0,
)


class TestAHandlerThatCannotSaveEndsItsConnection:
    """A failed save leaves nothing answered, and ends the connection.

    The save raises out of the handler and the transport stops there: an
    inbound connection is closed by ``_serve``; an outbound link is dropped and
    dialled again by ``_maintain``. The Rust transport ends them the same way
    (``serve``, ``pump``), from the failure its handlers return.
    """

    async def test_a_request_that_cannot_be_saved_gets_no_answer_and_ends_its_connection(
        self,
    ) -> None:
        saving = (_Saving(), _Saving())
        pair = _Pair(recorders=saving)
        await pair.start()
        try:
            began = asyncio.get_running_loop().time()
            saving[1].saves_fail = True

            with pytest.raises(RaftUnavailable):
                await pair.transports[0].request(1, _HEARTBEAT, timeout=2.0)
            assert saving[1].appends == 1, "the append never arrived"
            assert await _until(lambda: _down_since(saving[0], 1, began)), (
                "the connection outlived an append member 1 could not save"
            )

            # Dialled again, and answered on the new connection once saves work.
            saving[1].saves_fail = False
            assert await _until(lambda: 1 in pair.transports[0].live), (
                "the link never came back"
            )
            reply = await pair.transports[0].request(1, _HEARTBEAT, timeout=2.0)
            assert reply.success
        finally:
            await pair.close()

    async def test_a_reply_that_cannot_be_saved_ends_the_link_it_came_by(
        self,
    ) -> None:
        saving = (_Saving(), _Saving())
        pair = _Pair(recorders=saving)
        await pair.start()
        try:
            began = asyncio.get_running_loop().time()
            saving[0].saves_fail = True

            # Fire-and-forget, so the answer goes to the handler, which cannot
            # save what it says.
            pair.transports[0].send(1, _HEARTBEAT)
            assert await _until(lambda: saving[0].append_replies == 1), (
                "the reply never reached member 0"
            )
            assert await _until(lambda: _down_since(saving[0], 1, began)), (
                "the link outlived a reply member 0 could not save"
            )

            saving[0].saves_fail = False
            assert await _until(lambda: 1 in pair.transports[0].live), (
                "the link never came back"
            )
        finally:
            await pair.close()

    async def test_a_reply_that_cannot_be_saved_is_logged_as_the_failure_it_is(
        self, caplog: Any,
    ) -> None:
        """Not as a link going down, which is all ``_maintain`` can tell of an
        ``OSError``: the save's own error, at ERROR, with its traceback.

        Silent, a disk that filled up showed in the log as nothing but links
        reconnecting. The Rust says so too (``pump``: "raft: link to member
        failed").
        """
        saving = (_Saving(), _Saving())
        pair = _Pair(recorders=saving)
        await pair.start()
        try:
            caplog.set_level(logging.ERROR, logger="nmos.raft.transport")
            saving[0].saves_fail = True

            pair.transports[0].send(1, _HEARTBEAT)
            assert await _until(lambda: saving[0].append_replies == 1), (
                "the reply never reached member 0"
            )
            assert await _until(lambda: bool(_failed_saves(caplog.records))), (
                "member 0 could not save what a reply said, and nothing was "
                "logged"
            )
            message, error = _failed_saves(caplog.records)[0]
            assert message.startswith("raft: link to member 1 failed"), message
            assert error.errno == 28, error
        finally:
            await pair.close()


class _Undecodable:
    """A message no member can decode.

    Framed like any other (``_frame_for`` asks only for ``TYPE`` and
    ``encode``), so its checksum passes and its decoder refuses it -- what a
    sender with a bug, or of another version, would write.
    """

    def __init__(self, kind: MessageType) -> None:
        self.TYPE = kind

    def encode(self) -> bytes:
        # A varint that never ends: the first field's tag already fails.
        return b"\xff" * 11


class _AnswersOnceUndecodably(_Saving):
    """Answers its first append with bytes that do not decode, then as usual."""

    def __init__(self) -> None:
        super().__init__()
        self.undecodable_answers = 1

    def on_append_entries(self, peer: int, message: AppendEntries) -> Any:
        if self.undecodable_answers:
            self.undecodable_answers -= 1
            self.appends += 1
            return _Undecodable(MessageType.APPEND_ENTRIES_REPLY)
        return super().on_append_entries(peer, message)


def _undecodable_warnings(records: list[logging.LogRecord]) -> list[str]:
    return [
        record.getMessage() for record in records
        if record.levelno == logging.WARNING
        and record.getMessage().startswith("raft: undecodable frame")
    ]


class TestAnUndecodableFrameIsSkipped:
    """A frame that passes its checksum and will not decode is warned about and
    answered with nothing -- and the connection it came by carries on.

    The checksum proves the frame arrived as it was sent, so the stream is still
    aligned and the next frame reads correctly: the fault is the sender's, a bug
    or another version, and ending the connection would only fail every other
    exchange on it. The Rust has always done this (``dispatch``: "raft:
    undecodable frame"); the Python closed an inbound connection, and dropped an
    outbound link without a word. etcd closes its stream on any decode error --
    with no checksum to tell it the stream is still whole.
    """

    async def test_an_undecodable_request_is_not_answered_and_its_connection_stays(
        self, caplog: Any,
    ) -> None:
        recorders = (_Saving(), _Saving())
        pair = _Pair(recorders=recorders)
        await pair.start()
        try:
            caplog.set_level(logging.WARNING, logger="nmos.raft.transport")
            began = asyncio.get_running_loop().time()

            pair.transports[0].send(1, _Undecodable(MessageType.APPEND_ENTRIES))
            # Behind it on the same connection, and answered: the connection
            # carried on past it.
            try:
                reply = await pair.transports[0].request(1, _HEARTBEAT, timeout=2.0)
            except RaftUnavailable as exc:
                pytest.fail(f"the request behind the undecodable frame was lost: {exc}")
            assert reply.success
            assert recorders[1].appends == 1, "the undecodable frame reached the handler"
            assert not _down_since(recorders[0], 1, began), (
                "the connection was ended for one undecodable frame"
            )
            warnings = _undecodable_warnings(caplog.records)
            assert warnings and "from member 0" in warnings[0], warnings
        finally:
            await pair.close()

    async def test_an_undecodable_reply_leaves_the_link_up(
        self, caplog: Any,
    ) -> None:
        recorders = (_Saving(), _AnswersOnceUndecodably())
        pair = _Pair(recorders=recorders)
        await pair.start()
        try:
            caplog.set_level(logging.WARNING, logger="nmos.raft.transport")
            began = asyncio.get_running_loop().time()

            # Fire-and-forget, answered with bytes that do not decode; then a
            # request on the same link, answered as usual.
            pair.transports[0].send(1, _HEARTBEAT)
            try:
                reply = await pair.transports[0].request(1, _HEARTBEAT, timeout=2.0)
            except RaftUnavailable as exc:
                pytest.fail(f"the request behind the undecodable reply was lost: {exc}")
            assert reply.success
            assert recorders[0].append_replies == 0, (
                "the undecodable reply reached the handler"
            )
            assert not _down_since(recorders[0], 1, began), (
                "the link was dropped for one undecodable reply"
            )
            warnings = _undecodable_warnings(caplog.records)
            assert warnings and "from member 1" in warnings[0], warnings
        finally:
            await pair.close()


class TestADefectInAHandlerStopsTheMember:
    """Over real sockets: the inbound reader is where every consensus handler
    runs, and what it used to do with a handler that raised was log "inbound
    connection failed" and let the peer redial -- for ever, in front of a
    member whose own logic had been caught out. The member stops instead
    (``PeerHandler.on_unexpected_error``, ``RaftNode._fail``)."""

    async def test_a_handler_that_raises_stops_the_member(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from nmos.raft.log import RaftLog
        from nmos.raft.tests._sockets import SocketCluster
        from nmos.raft.tests.test_consensus import _register

        cluster = SocketCluster(3, tmp_path)
        await cluster.start()
        try:
            leader = await cluster.elect(timeout=10.0)
            broken = next(m for m in cluster.members if m is not leader)
            original = RaftLog.append_replicated

            def planted(log: Any, entries: Any, *, committed: int) -> None:
                if log is not broken.node._log:
                    original(log, entries, committed=committed)
                    return
                raise RuntimeError("planted: a handler that raises")

            # On the class: ``RaftLog`` has slots. Only ``broken``'s raises.
            monkeypatch.setattr(RaftLog, "append_replicated", planted)
            await asyncio.wait_for(
                leader.node.propose(_register(str(uuid.uuid4()), leader.index)),
                10.0,
            )

            try:
                failure = await asyncio.wait_for(
                    broken.node.wait_for_failure(), 5.0,
                )
            except asyncio.TimeoutError:
                pytest.fail(
                    "a member whose handler raised on the inbound reader went "
                    "on: the connection was dropped and the leader redialled",
                )
            assert isinstance(failure, RaftUnexpectedError), failure
            assert failure.task is MemberTask.INBOUND_CONNECTION
            assert "planted: a handler that raises" in repr(failure.__cause__)
        finally:
            await cluster.close()


def _oversized_chunk() -> InstallSnapshot:
    """A chunk one byte above the frame cap: what no bound should let through."""
    return InstallSnapshot(
        term=1, leader=0, last_index=1, last_term=1, offset=0,
        data=b"\x00" * (MAX_FRAME + 1), done=False,
    )


class TestAMessageAboveTheFrameCapIsNotSent:
    """A message above ``MAX_FRAME`` is refused at encode time, loudly, and
    nothing else happens to the link.

    ``encode_frame`` refused it before too -- and the refusal escaped ``send``
    into whatever called it: a tick, which ended that tick, or a reply
    handler, which ended the link. With a tick's exceptions now a stop, the
    escape stopped the leader. The bound that keeps ordinary messages under the
    cap is ``RaftTiming.max_append_bytes``; this is the backstop behind it, and
    a backstop that kills the member is no backstop.
    """

    async def test_an_oversized_send_is_logged_and_the_link_stays_up(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        pair = _Pair()
        await pair.start()
        try:
            with caplog.at_level(logging.ERROR, logger="nmos.raft.transport"):
                pair.transports[0].send(1, _oversized_chunk(), stream=Stream.BULK)
            # Sent after it, on the other stream: the link is as it was.
            pair.transports[0].send(1, Promote(term=1, leader=0, through_index=1))
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 2.0
            while loop.time() < deadline and not pair.recorders[1].promotes:
                await asyncio.sleep(0.01)
            assert pair.recorders[1].promotes, (
                "the message after the oversized one never arrived: the link "
                "was dropped for a frame that was never written"
            )
            assert 1 in pair.transports[0].live
            assert pair.recorders[1].failures == []
            refusals = [
                record for record in caplog.records
                if "exceeds the frame cap" in record.getMessage()
            ]
            assert len(refusals) == 1, [r.getMessage() for r in caplog.records]
            assert "InstallSnapshot to member 1" in refusals[0].getMessage()
        finally:
            await pair.close()

    async def test_an_oversized_request_fails_at_once(self) -> None:
        pair = _Pair()
        await pair.start()
        try:
            loop = asyncio.get_running_loop()
            began = loop.time()
            with pytest.raises(RaftUnavailable, match="frame cap"):
                await pair.transports[0].request(
                    1, _oversized_chunk(), timeout=2.0, stream=Stream.BULK,
                )
            assert loop.time() - began < 0.5, (
                "a request that could never be sent waited out its deadline"
            )
            assert 1 in pair.transports[0].live
        finally:
            await pair.close()


def _member(
    peer: tuple[str, int], *, dial_timeout: float, **tls: Any,
) -> tuple[RaftTransport, int]:
    """Member 0 on a port of its own, dialling member 1 at ``peer``."""
    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    port = int(held.getsockname()[1])
    held.close()
    member = RaftTransport(
        local=0, peers={1: peer}, bind=("127.0.0.1", port),
        cluster_id="liveness", member_name="m0", incarnation=1,
        rpc_timeout=1.0, conn_read_timeout=READ_TIMEOUT,
        dial_timeout=dial_timeout, **tls,
    )
    return member, port


async def _admit(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Be member 1 for one connection: admit it, then keep it fed.

    A stale connection -- one queued before the hole opened, whose client has
    gone -- ends at its first read, and that is all it does.
    """
    try:
        frame = await read_frame(reader)
        if frame.type is not MessageType.HELLO:
            return
        hello = decode_message(frame.type, frame.payload)
        writer.write(encode_frame(Frame(
            stream=hello.stream, type=MessageType.HELLO_ACK, flags=0,
            payload=HelloAck(
                accepted=True, reason="", minor=PROTOCOL_MINOR,
                member_index=1, incarnation=1,
            ).encode(),
        )))
        nonce = 0
        while True:
            # Something every third of the deadline, as the accepting end's
            # own heartbeat does; the member's pings are read and left.
            try:
                await asyncio.wait_for(read_frame(reader), READ_TIMEOUT / 3)
            except asyncio.TimeoutError:
                pass
            nonce += 1
            writer.write(encode_frame(Frame(
                stream=hello.stream, type=MessageType.PONG, flags=FLAG_REPLY,
                payload=Pong(nonce=nonce).encode(),
            )))
    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        return
    finally:
        with contextlib.suppress(OSError, RuntimeError):
            writer.close()


class TestADialIsBounded:
    """A connection attempt ends at ``DIAL_TIMEOUT``, at either end.

    Neither the TCP connect nor the TLS handshake had a bound. Into a path that
    drops SYNs -- a firewall that drops rather than refuses, a host mid-reboot
    -- ``open_connection`` waited out the kernel's retransmits, about 127 s on
    Linux; a handshake had asyncio's 60 s. Each test shortens the deadline to
    half a second and watches the attempt end at it.
    """

    async def test_the_deadline_is_seven_seconds(self) -> None:
        """Three SYN retransmits (1, 3 and 7 s on Linux), and the mutation
        timeout's figure; the same number the Rust uses."""
        assert DIAL_TIMEOUT == 7.0

    async def test_a_dial_into_a_black_hole_is_abandoned_at_the_deadline(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        # A listener whose accept queue is full drops further SYNs: the
        # kernel's own black hole, on loopback, with nothing to configure.
        backlog = 1
        hole = socket.socket()
        hole.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        hole.bind(("127.0.0.1", 0))
        hole.listen(backlog)
        port = int(hole.getsockname()[1])
        # Filled until a connect is dropped. Linux's queue holds backlog + 1,
        # so two must get in before the third is dropped; fewer means the first
        # connect was the one that timed out -- a loaded machine, not a full
        # queue -- and the member's SYN would be taken, which proves nothing.
        # Windows' holds backlog -- measured, listen(1) admits one and
        # listen(2) two, the next SYN dropped as on Linux -- so there one gets
        # in before the second is dropped, and expecting two skipped every run.
        capacity = backlog if sys.platform == "win32" else backlog + 1
        fills: list[socket.socket] = []
        for _ in range(8):
            filler = socket.socket()
            filler.settimeout(2.0)
            try:
                filler.connect(("127.0.0.1", port))
            except OSError:
                filler.close()
                break
            fills.append(filler)
        else:
            for filler in fills:
                filler.close()
            hole.close()
            pytest.skip("the accept queue never filled: this kernel takes SYNs past the backlog")
        if len(fills) < capacity:
            for filler in fills:
                filler.close()
            hole.close()
            pytest.skip(
                f"only {len(fills)} of {capacity} connection(s) got into the accept "
                f"queue before one was dropped: the machine is too loaded to fill it",
            )

        member, _ = _member(("127.0.0.1", port), dial_timeout=0.5)
        loop = asyncio.get_running_loop()
        server: asyncio.AbstractServer | None = None
        caplog.set_level(logging.DEBUG, logger="nmos.raft.transport")
        wall = time.time()
        await member.start(_Recorder())
        try:
            # The discriminator is the attempt, not the recovery. Without the
            # deadline one attempt hangs in the kernel's SYN retransmits for
            # well over a minute, and nothing is given up; with it, every
            # attempt ends at half a second and the next follows the backoff.
            # (When the hole opens is not a measure: this kernel retransmits
            # the first four SYNs a second apart, so an attempt with no
            # deadline connects within seconds of the opening too.)
            await asyncio.sleep(3.5)
            abandoned = [
                record for record in caplog.records
                if "did not accept a connection within 0.5s" in record.getMessage()
            ]
            assert len(abandoned) >= 2, (
                f"{len(abandoned)} attempt(s) given up in 3.5s: the dial waited "
                f"in the kernel's SYN retransmits instead of ending at the deadline"
            )
            assert abandoned[0].created - wall < 0.9, (
                f"the first attempt was abandoned {abandoned[0].created - wall:.2f}s "
                f"after it began"
            )

            # And once the hole opens, the link comes up.
            for filler in fills:
                filler.close()
            hole.setblocking(False)
            server = await asyncio.start_server(_admit, sock=hole)
            opened = loop.time()
            while loop.time() - opened < 10.0 and 1 not in member.live:
                await asyncio.sleep(0.02)
            assert 1 in member.live, "the link never came up within 10s of the hole opening"
        finally:
            await member.close()
            if server is not None:
                server.close()
                await server.wait_closed()
            else:
                hole.close()

    async def test_a_tls_handshake_the_acceptor_never_starts_is_abandoned_at_the_deadline(
        self,
    ) -> None:
        loop = asyncio.get_running_loop()
        seen: list[float] = []

        async def mute(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            began = loop.time()
            try:
                await reader.read()
            finally:
                seen.append(loop.time() - began)
                with contextlib.suppress(OSError, RuntimeError):
                    writer.close()

        server = await asyncio.start_server(mute, "127.0.0.1", 0)
        port = int(server.sockets[0].getsockname()[1])
        member, _ = _member(
            ("127.0.0.1", port), dial_timeout=0.5,
            client_ssl=ssl.create_default_context(), peer_name="raft",
        )
        await member.start(_Recorder())
        try:
            deadline = loop.time() + 3.0
            while loop.time() < deadline and not seen:
                await asyncio.sleep(0.02)
            assert seen, (
                "the dialler never gave up on a TLS handshake the acceptor "
                "never started: asyncio's own 60s applied"
            )
            assert seen[0] < 0.9, f"gave up after {seen[0]:.2f}s"
        finally:
            await member.close()
            server.close()
            await server.wait_closed()

    @pytest.mark.skipif(not PKI_AVAILABLE, reason="the PKI fixtures are not on disk")
    async def test_a_client_that_never_starts_the_tls_handshake_is_dropped_at_the_deadline(
        self,
    ) -> None:
        nowhere = socket.socket()
        nowhere.bind(("127.0.0.1", 0))
        member, port = _member(
            ("127.0.0.1", int(nowhere.getsockname()[1])), dial_timeout=0.5,
            server_ssl=build_server_ssl_context("SNX00000"),
        )
        await member.start(_Recorder())
        loop = asyncio.get_running_loop()
        writer: asyncio.StreamWriter | None = None
        try:
            # Plain TCP to a TLS listener, and then nothing.
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            began = loop.time()
            try:
                data = await asyncio.wait_for(reader.read(1), 3.0)
            except asyncio.TimeoutError:
                pytest.fail(
                    "a client that never started the TLS handshake held its "
                    "connection for 3s: asyncio's own 60s applied",
                )
            except ConnectionError:
                data = b""
            assert data == b"", f"the member sent {data!r} before any handshake"
            assert loop.time() - began < 0.9, f"dropped after {loop.time() - began:.2f}s"
        finally:
            if writer is not None:
                writer.close()
            nowhere.close()
            await member.close()
