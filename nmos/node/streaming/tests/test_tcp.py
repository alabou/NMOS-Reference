# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for TCP client-server transport."""

from __future__ import annotations

import asyncio
import struct
import uuid

import pytest

from nmos.node.events import EngineEvent, EventId
from nmos.node.streaming import _build_streaming_coro
from nmos.node.streaming.transport_tcp import tcp_sender, tcp_receiver


class _Field:
    def __init__(self, value: object) -> None:
        self.defined = True
        self.value = value


class _TcpParams:
    SourceIp = _Field("127.0.0.1")
    SourcePort = _Field(0)
    DestinationIp = _Field("127.0.0.1")
    DestinationPort = _Field(0)


class TestTcpLoopback:
    """Loopback tests: TCP sender (listener) ↔ receiver (connector)."""

    @pytest.mark.asyncio
    async def test_usb_transports_dispatch_to_tcp_emulation(self) -> None:
        """USB is emulated over TCP, so both USB URNs must build TCP coroutines."""
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()

        for transport in (
            "urn:x-nmos:transport:usb",
            "urn:x-matrox:transport:usb",
        ):
            sender_coro = _build_streaming_coro(
                loop, transport, _TcpParams(),
                str(uuid.uuid4()), "lo", True, None, None, None, stop,
            )
            receiver_coro = _build_streaming_coro(
                loop, transport, _TcpParams(),
                str(uuid.uuid4()), "lo", False, None, None, None, stop,
            )
            assert sender_coro is not None
            assert receiver_coro is not None
            sender_coro.close()
            receiver_coro.close()

    @pytest.mark.asyncio
    async def test_receiver_connects_via_source_ip_port(self) -> None:
        """A connection-oriented (USB/RTSP/…) receiver must connect to
        SourceIp:SourcePort — the sender's endpoint mapped from the SDP — NOT
        DestinationIp/DestinationPort. Guards the regression where the receiver
        coro read Destination* and connected to 0.0.0.0:0 → connect error →
        link down. Here Destination* point nowhere; the connect only succeeds
        if Source* is used."""
        port = 19850
        q: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=200)
        stop_s = asyncio.Event()
        stop_r = asyncio.Event()
        loop = asyncio.get_running_loop()

        class _Params:
            SourceIp = _Field("127.0.0.1")
            SourcePort = _Field(port)
            DestinationIp = _Field("127.0.0.1")
            DestinationPort = _Field(0)   # deliberately wrong — must be ignored

        recv_coro = _build_streaming_coro(
            loop, "urn:x-nmos:transport:usb", _Params(),
            str(uuid.uuid4()), "lo", False, q, None, None, stop_r,
        )
        assert recv_coro is not None

        sender_task = asyncio.create_task(tcp_sender(
            listen_ip="127.0.0.1", listen_port=port,
            sender_id=str(uuid.uuid4()), interface_name="lo",
            event_queue=q, stop_event=stop_s,
        ))
        await asyncio.sleep(0.3)
        recv_task = asyncio.create_task(recv_coro)
        await asyncio.sleep(2.0)
        stop_s.set()
        stop_r.set()
        await asyncio.gather(sender_task, recv_task, return_exceptions=True)

        events: list[EngineEvent] = []
        while not q.empty():
            events.append(q.get_nowait())
        connect_errors = [e for e in events if "connect error" in (e.info or "")]
        activates = [e for e in events if e.event == EventId.VENDOR_TRANSPORT_ACTIVATE]
        assert not connect_errors, [e.info for e in connect_errors]
        assert len(activates) >= 2, "both sender and receiver must activate (link up)"

    @pytest.mark.asyncio
    async def test_tcp_loopback(self) -> None:
        """Sender listens, receiver connects, packets flow over TCP."""
        event_queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=100)
        sender_stop = asyncio.Event()
        receiver_stop = asyncio.Event()
        sid = str(uuid.uuid4())
        rid = str(uuid.uuid4())
        port = 19800

        async def run_sender() -> None:
            await tcp_sender(
                listen_ip="127.0.0.1", listen_port=port,
                sender_id=sid, interface_name="lo",
                event_queue=event_queue, stop_event=sender_stop,
            )

        async def run_receiver() -> None:
            await tcp_receiver(
                dest_ip="127.0.0.1", dest_port=port,
                receiver_id=rid, interface_name="lo",
                event_queue=event_queue, stop_event=receiver_stop,
            )

        # Start sender (listener) first, then receiver (connector)
        sender_task = asyncio.create_task(run_sender())
        await asyncio.sleep(0.3)
        receiver_task = asyncio.create_task(run_receiver())

        await asyncio.sleep(3.5)

        sender_stop.set()
        receiver_stop.set()
        await asyncio.gather(sender_task, receiver_task, return_exceptions=True)

        events: list[EngineEvent] = []
        while not event_queue.empty():
            events.append(event_queue.get_nowait())

        # Find sender deactivation
        deactivate_idx = None
        for i, e in enumerate(events):
            if e.event == EventId.VENDOR_TRANSPORT_DEACTIVATE and e.id == sid:
                deactivate_idx = i
                break

        pre = events[:deactivate_idx] if deactivate_idx else events

        error_events = [
            e for e in pre
            if e.event in (
                EventId.TRANSPORT_PACKET_LOST,
                EventId.TRANSPORT_PACKET_LATE,
                EventId.ESSENCE_STREAM_ERROR,
            )
        ]
        assert not error_events, (
            f"Unexpected errors: {[(e.event, e.info) for e in error_events]}"
        )

        activate_events = [e for e in events if e.event == EventId.VENDOR_TRANSPORT_ACTIVATE]
        assert len(activate_events) >= 2

    @pytest.mark.asyncio
    async def test_tcp_lifecycle_events(self) -> None:
        """Verify full lifecycle: activate → starting → stopping → deactivate."""
        event_queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=100)
        stop = asyncio.Event()
        sid = str(uuid.uuid4())
        rid = str(uuid.uuid4())
        port = 19900

        async def run_sender() -> None:
            await tcp_sender(
                listen_ip="127.0.0.1", listen_port=port,
                sender_id=sid, interface_name="lo",
                event_queue=event_queue, stop_event=stop,
            )

        async def run_receiver() -> None:
            await tcp_receiver(
                dest_ip="127.0.0.1", dest_port=port,
                receiver_id=rid, interface_name="lo",
                event_queue=event_queue, stop_event=stop,
            )

        sender_task = asyncio.create_task(run_sender())
        await asyncio.sleep(0.3)
        receiver_task = asyncio.create_task(run_receiver())

        await asyncio.sleep(1.5)
        stop.set()
        await asyncio.gather(sender_task, receiver_task, return_exceptions=True)

        events: list[EngineEvent] = []
        while not event_queue.empty():
            events.append(event_queue.get_nowait())

        event_ids = [e.event for e in events]

        # Both sender and receiver emit activate + starting(5) + stopping + deactivate
        assert event_ids.count(EventId.VENDOR_TRANSPORT_ACTIVATE) == 2
        assert event_ids.count(EventId.VENDOR_ESSENCE_STOP) == 2
        assert event_ids.count(EventId.VENDOR_TRANSPORT_DEACTIVATE) == 2


class TestTcpSenderServesManyClients:
    """A listening sender is not point-to-point.

    ``tcp_sender`` used to keep a list of accepted writers but only ever write
    to ``client_writer[0]``, and a failing write broke the send loop, which
    ended the sender altogether. So a second receiver connected and was never
    sent anything, and the first receiver disconnecting closed the listener
    while IS-04/IS-05 still advertised the sender as transmitting.

    These tests use raw connections rather than ``tcp_receiver`` so the frames
    each client actually gets can be counted directly.
    """

    @staticmethod
    async def _read_frame(reader: asyncio.StreamReader, timeout: float = 4.0) -> bytes:
        header = await asyncio.wait_for(reader.readexactly(4), timeout)
        (length,) = struct.unpack("<I", header)
        return await asyncio.wait_for(reader.readexactly(length), timeout)

    @staticmethod
    def _drain_queue(queue: "asyncio.Queue[EngineEvent]") -> list[EngineEvent]:
        events: list[EngineEvent] = []
        while not queue.empty():
            events.append(queue.get_nowait())
        return events

    async def _start_sender(
        self, port: int, queue: "asyncio.Queue[EngineEvent]",
        stop: asyncio.Event, sid: str,
    ) -> "asyncio.Task[None]":
        task = asyncio.create_task(tcp_sender(
            listen_ip="127.0.0.1", listen_port=port,
            sender_id=sid, interface_name="lo",
            event_queue=queue, stop_event=stop,
        ))
        await asyncio.sleep(0.3)          # let the listener bind
        return task

    @pytest.mark.asyncio
    async def test_two_clients_both_receive_the_same_stream(self) -> None:
        queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=200)
        stop = asyncio.Event()
        sid = str(uuid.uuid4())
        task = await self._start_sender(19910, queue, stop, sid)
        try:
            r1, w1 = await asyncio.open_connection("127.0.0.1", 19910)
            r2, w2 = await asyncio.open_connection("127.0.0.1", 19910)

            first = await self._read_frame(r1)
            second = await self._read_frame(r2)
            assert first, "first client received no frame"
            assert second, "second client received no frame"
            # One stream fanned out, so the same packet reaches both. This
            # also proves the frame is encrypted/built once rather than per
            # client, which the PEP counter requires.
            assert first == second

            for w in (w1, w2):
                w.close()
        finally:
            stop.set()
            await asyncio.gather(task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_one_client_leaving_does_not_disturb_the_other(self) -> None:
        """The regression that took the whole sender down."""
        queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=200)
        stop = asyncio.Event()
        sid = str(uuid.uuid4())
        task = await self._start_sender(19911, queue, stop, sid)
        try:
            r1, w1 = await asyncio.open_connection("127.0.0.1", 19911)
            r2, w2 = await asyncio.open_connection("127.0.0.1", 19911)
            await self._read_frame(r1)
            await self._read_frame(r2)

            # The first client goes away, as a TCP client normally will.
            w1.close()
            await w1.wait_closed()

            # The survivor keeps being served — two more packets, so the
            # sender has been through the write path after the disconnect.
            assert await self._read_frame(r2)
            assert await self._read_frame(r2)
            assert not task.done(), "the sender ended when a client left"

            events = self._drain_queue(queue)
            offenders = [
                e for e in events
                if e.id == sid and e.event in (
                    EventId.LINK_DOWN,
                    EventId.TRANSPORT_STREAM_ERROR,
                    EventId.TRANSPORT_PACKET_LOST,
                    EventId.TRANSPORT_PACKET_LATE,
                )
            ]
            assert not offenders, (
                f"a client disconnecting raised an alert on the sender: "
                f"{[(e.event, e.info) for e in offenders]} — a peer closing "
                f"its connection is ordinary and is not a link or "
                f"transmission fault"
            )
            w2.close()
        finally:
            stop.set()
            await asyncio.gather(task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_sender_keeps_listening_for_a_late_client(self) -> None:
        """No deadline on the first connection.

        The sender used to report "no client connected within 30s" and return,
        closing the listener — so a receiver activated later than that found
        nothing to connect to, which is incompatible with attaching several
        receivers over time.
        """
        queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=200)
        stop = asyncio.Event()
        sid = str(uuid.uuid4())
        task = await self._start_sender(19912, queue, stop, sid)
        try:
            # Idle with no client at all, then attach.
            await asyncio.sleep(2.5)
            assert not task.done(), "the sender ended while idle"
            reader, writer = await asyncio.open_connection("127.0.0.1", 19912)
            assert await self._read_frame(reader), "late client received nothing"

            events = self._drain_queue(queue)
            idle_alerts = [
                e for e in events
                if e.id == sid and e.event in (
                    EventId.LINK_DOWN, EventId.TRANSPORT_STREAM_ERROR)
            ]
            assert not idle_alerts, (
                f"an idle sender raised an alert: "
                f"{[(e.event, e.info) for e in idle_alerts]} — having no "
                f"client yet is idle, not faulty"
            )
            writer.close()
        finally:
            stop.set()
            await asyncio.gather(task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_deactivation_stops_accepting_promptly(self) -> None:
        """Deactivation is checked between packets and closes the listener."""
        queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=200)
        stop = asyncio.Event()
        sid = str(uuid.uuid4())
        task = await self._start_sender(19913, queue, stop, sid)
        reader, writer = await asyncio.open_connection("127.0.0.1", 19913)
        await self._read_frame(reader)

        stop.set()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True),
                              timeout=5.0)
        writer.close()

        with pytest.raises((ConnectionRefusedError, OSError)):
            r, w = await asyncio.open_connection("127.0.0.1", 19913)
            w.close()

        events = self._drain_queue(queue)
        ids = [e.event for e in events if e.id == sid]
        assert EventId.VENDOR_TRANSPORT_DEACTIVATE in ids
