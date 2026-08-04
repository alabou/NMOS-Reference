# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for UDP multicast/unicast transport."""

from __future__ import annotations

import asyncio
import socket
import uuid

import pytest

from nmos.node.events import EngineEvent, EventId
from nmos.node.streaming.transport_udp import udp_sender, udp_receiver


class TestUdpLoopback:
    """Loopback tests: sender → receiver on localhost."""

    @pytest.mark.asyncio
    async def test_unicast_loopback(self) -> None:
        """Sender sends 3 packets via unicast, receiver gets them all."""
        loop = asyncio.get_event_loop()
        event_queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=100)
        sender_stop = asyncio.Event()
        receiver_stop = asyncio.Event()
        sid = str(uuid.uuid4())
        rid = str(uuid.uuid4())
        port = 17500

        received_count = 0
        errors: list[str] = []

        async def run_sender() -> None:
            await udp_sender(
                loop=loop,
                source_ip="127.0.0.1",
                source_port=0,  # OS-assigned
                dest_ip="127.0.0.1",
                dest_port=port,
                sender_id=sid,
                interface_name="lo",
                event_queue=event_queue,
                stop_event=sender_stop,
            )

        async def run_receiver() -> None:
            nonlocal received_count
            await udp_receiver(
                loop=loop,
                interface_ip="127.0.0.1",
                multicast_ip="",
                source_ip="",
                dest_port=port,
                receiver_id=rid,
                interface_name="lo",
                event_queue=event_queue,
                stop_event=receiver_stop,
            )

        # Start receiver first, then sender
        receiver_task = asyncio.create_task(run_receiver())
        await asyncio.sleep(0.2)  # Let receiver bind
        sender_task = asyncio.create_task(run_sender())

        # Let 3 packets through (3 seconds + margin)
        await asyncio.sleep(3.5)

        # Stop both simultaneously — receiver must stop before its timeout fires
        sender_stop.set()
        receiver_stop.set()

        await asyncio.gather(sender_task, receiver_task, return_exceptions=True)

        # Check events: should have activate+starting for both
        events: list[EngineEvent] = []
        while not event_queue.empty():
            events.append(event_queue.get_nowait())

        # Filter events that occurred BEFORE deactivation
        # (timeout after sender stops is expected, not a real error)
        deactivate_time = None
        for i, e in enumerate(events):
            if e.event == EventId.VENDOR_TRANSPORT_DEACTIVATE and e.id == sid:
                deactivate_time = i
                break

        pre_deactivate_events = events[:deactivate_time] if deactivate_time else events

        # Verify no packet loss or error events while sender was active
        error_events = [
            e for e in pre_deactivate_events
            if e.event in (
                EventId.TRANSPORT_PACKET_LOST,
                EventId.TRANSPORT_PACKET_LATE,
                EventId.ESSENCE_STREAM_ERROR,
            )
        ]
        assert not error_events, (
            f"Unexpected error events: {[(e.event, e.info) for e in error_events]}"
        )

        # Verify we got activate events for both sender and receiver
        activate_events = [e for e in events if e.event == EventId.VENDOR_TRANSPORT_ACTIVATE]
        assert len(activate_events) >= 2, "Expected activate events for sender and receiver"

    @pytest.mark.asyncio
    async def test_multicast_loopback(self) -> None:
        """Multicast sender → receiver over loopback delivers packets.

        Regression: the receiver used to bind the multicast group address,
        which Winsock rejects with WSAEADDRNOTAVAIL — the task died before
        joining the group, so nothing was ever received. The sender in turn
        cannot route to a loopback group until someone joins it, so the
        receiver is started first here (as an IS-05 activation would).
        """
        loop = asyncio.get_event_loop()
        event_queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=100)
        sender_stop = asyncio.Event()
        receiver_stop = asyncio.Event()
        sid = str(uuid.uuid4())
        rid = str(uuid.uuid4())
        group = "239.255.77.1"
        port = 17600

        receiver_task = asyncio.create_task(udp_receiver(
            loop=loop,
            interface_ip="127.0.0.1",
            multicast_ip=group,
            source_ip="127.0.0.1",
            dest_port=port,
            receiver_id=rid, interface_name="lo",
            event_queue=event_queue, stop_event=receiver_stop,
        ))
        await asyncio.sleep(0.5)  # Let the receiver bind and join the group

        sender_task = asyncio.create_task(udp_sender(
            loop=loop,
            source_ip="127.0.0.1", source_port=0,
            dest_ip=group, dest_port=port,
            sender_id=sid, interface_name="lo",
            event_queue=event_queue, stop_event=sender_stop,
        ))

        # Run past the receiver's 3s starvation timer so that a receiver
        # which never gets a packet reports it *before* the sender stops.
        await asyncio.sleep(3.5)
        sender_stop.set()
        receiver_stop.set()
        results = await asyncio.gather(
            sender_task, receiver_task, return_exceptions=True,
        )
        raised = [r for r in results if isinstance(r, BaseException)]
        assert not raised, f"streaming task raised: {raised}"

        events: list[EngineEvent] = []
        while not event_queue.empty():
            events.append(event_queue.get_nowait())

        # The receiver is blocked in a 3s recv when the stop is signalled, so
        # it always emits one starvation timeout on the way out. Only faults
        # raised while the sender was still transmitting are meaningful.
        sender_gone = next(
            (i for i, e in enumerate(events)
             if e.event == EventId.VENDOR_TRANSPORT_DEACTIVATE and e.id == sid),
            len(events),
        )
        faults = [
            e.info for e in events[:sender_gone]
            if e.event == EventId.TRANSPORT_STREAM_ERROR and e.id == rid
        ]
        # Covers both a socket-setup failure (WSAEADDRNOTAVAIL on the old
        # group-address bind) and 3s of starvation.
        assert not faults, f"receiver reported transport faults: {faults}"

    @pytest.mark.skipif(not socket.has_ipv6, reason="no IPv6 support")
    @pytest.mark.asyncio
    async def test_ipv6_multicast_sender_socket_setup(self) -> None:
        """An IPv6 multicast sender must survive its socket setup.

        Regression: the hop-limit and loopback options were set with the
        IPPROTO_IP names, which an AF_INET6 socket rejects with WSAEINVAL.
        The OSError escaped the coroutine and killed the sender task.

        Only setup is asserted, not delivery: whether a loopback IPv6 group
        is routable depends on a Receiver having joined it, which is a
        separate concern (see test_multicast_loopback).
        """
        loop = asyncio.get_event_loop()
        event_queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=100)
        stop = asyncio.Event()
        sid = str(uuid.uuid4())

        task = asyncio.create_task(udp_sender(
            loop=loop,
            source_ip="::1", source_port=0,
            dest_ip="ff15::77", dest_port=19700,
            sender_id=sid, interface_name="lo",
            event_queue=event_queue, stop_event=stop,
        ))
        await asyncio.sleep(0.5)
        stop.set()
        results = await asyncio.gather(task, return_exceptions=True)
        raised = [r for r in results if isinstance(r, BaseException)]
        # The lifecycle events are emitted from a finally block, so they show
        # up either way — an escaped OSError is the only usable signal here.
        assert not raised, f"IPv6 sender setup raised: {raised}"

    @pytest.mark.asyncio
    async def test_sender_clears_transport_error_on_next_send(self) -> None:
        """A send failure followed by a success emits a TRANSPORT_OK recovery.

        Regression: the sender emitted the error but never a recovery, so a
        self-healing fault (e.g. a loopback multicast group with no joiner
        yet, WinError 1231) pinned transmissionStatus at Error for the whole
        activation.
        """
        loop = asyncio.get_event_loop()
        event_queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=100)
        stop = asyncio.Event()
        sid = str(uuid.uuid4())

        real_sendto = loop.sock_sendto
        calls = {"n": 0}

        async def flaky_sendto(sock, data, addr):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError(1231, "The network location cannot be reached")
            return await real_sendto(sock, data, addr)

        setattr(loop, "sock_sendto", flaky_sendto)
        try:
            task = asyncio.create_task(udp_sender(
                loop=loop,
                source_ip="127.0.0.1", source_port=0,
                dest_ip="127.0.0.1", dest_port=18600,
                sender_id=sid, interface_name="lo",
                event_queue=event_queue, stop_event=stop,
            ))
            await asyncio.sleep(2.5)  # 1s period — fail, then succeed
            stop.set()
            await task
        finally:
            setattr(loop, "sock_sendto", real_sendto)

        assert calls["n"] >= 2, "test needs at least one send after the failure"

        events: list[EngineEvent] = []
        while not event_queue.empty():
            events.append(event_queue.get_nowait())

        error_idx = next(
            (i for i, e in enumerate(events)
             if e.event == EventId.TRANSPORT_STREAM_ERROR and e.id == sid),
            None,
        )
        assert error_idx is not None, "expected the injected send error"
        recovered = any(
            e.event == EventId.TRANSPORT_OK and e.id == sid
            for e in events[error_idx + 1:]
        )
        assert recovered, "sender never cleared the transport error"

    @pytest.mark.asyncio
    async def test_sender_emits_lifecycle_events(self) -> None:
        """Sender emits activate → starting(5) → stopping → deactivate."""
        loop = asyncio.get_event_loop()
        event_queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=100)
        stop = asyncio.Event()
        sid = str(uuid.uuid4())

        async def run() -> None:
            await udp_sender(
                loop=loop,
                source_ip="127.0.0.1", source_port=0,
                dest_ip="127.0.0.1", dest_port=18500,
                sender_id=sid, interface_name="lo",
                event_queue=event_queue, stop_event=stop,
            )

        task = asyncio.create_task(run())
        await asyncio.sleep(0.5)
        stop.set()
        await task

        events: list[EngineEvent] = []
        while not event_queue.empty():
            events.append(event_queue.get_nowait())

        event_ids = [e.event for e in events]

        # Lifecycle: activate, 4× starting, stopping, deactivate
        assert event_ids[0] == EventId.VENDOR_TRANSPORT_ACTIVATE
        assert EventId.VENDOR_ESSENCE_START in event_ids
        assert EventId.TRANSPORT_OK in event_ids
        assert EventId.ESSENCE_OK in event_ids
        assert EventId.LINK_OK in event_ids
        # Activation must NOT emit CLOCK_OK — the sync facet reflects the
        # effective clock (PTP), not stream start, and is driven separately
        # only when the clock is a locked PTP reference. This loopback sender
        # is on the internal clock, so no CLOCK_OK is expected.
        assert EventId.CLOCK_OK not in event_ids
        assert event_ids[-2] == EventId.VENDOR_ESSENCE_STOP
        assert event_ids[-1] == EventId.VENDOR_TRANSPORT_DEACTIVATE
