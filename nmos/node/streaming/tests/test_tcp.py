# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for TCP client-server transport."""

from __future__ import annotations

import asyncio
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
