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
import logging
import socket
from collections.abc import Callable
from typing import Any

import pytest

from nmos.raft.errors import RaftUnavailable
from nmos.raft.messages import AppendEntries, AppendEntriesReply, Promote
from nmos.raft.tests._proxy import ProxyMesh
from nmos.raft.transport import RaftTransport
from nmos.raft.wire import MessageType

# Small, so the tests take seconds rather than etcd's 5 s multiples; the
# mechanism does not depend on the value.
READ_TIMEOUT = 0.5

# How long a stalled path holds what it was given: well past the deadline, so
# a transport that waits it out is told apart from one that does not.
HOLD = 2.0


class _Recorder:
    """The two callbacks these tests watch; anything else would be a surprise."""

    def __init__(self) -> None:
        self.promotes: list[tuple[int, Promote]] = []
        self.states: list[tuple[float, int, bool]] = []

    def on_promote(self, peer: int, message: Promote) -> None:
        self.promotes.append((peer, message))

    def on_peer_state(self, peer: int, *, up: bool, incarnation: int) -> None:
        self.states.append((asyncio.get_running_loop().time(), peer, up))

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
