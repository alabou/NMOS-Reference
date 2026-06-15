# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for UDP multicast/unicast transport."""

from __future__ import annotations

import asyncio
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
