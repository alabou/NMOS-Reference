# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""SRT-style UDP unicast transport for the streaming engine.

SRT uses UDP unicast with listener/caller semantics:
- Sender = Listener: binds to a port, waits for caller's first packet
- Receiver = Caller: sends a "hello" packet to the listener, then receives

This is a simplified model for transport verification — not real SRT protocol.
The same test packet format is used as for multicast UDP.
"""

from __future__ import annotations

import asyncio
import socket
import time
from typing import Any

from nmos.node.events import (
    EngineEvent, AlertDomain, AlertScope, EventId, EventState,
    emit_event, emit_activate, emit_deactivate,
    emit_starting, emit_stopping, emit_transport_error, emit_recovery,
)
from nmos.node.streaming.packet import (
    StreamPacket, PACKET_SIZE, PEP_CLEAR_HEADER_SIZE, DEFAULT_PERIOD_NS,
)
from nmos.node.streaming.transport_udp import MAX_LATE_ARRIVAL_S, SOCKET_TIMEOUT_S


# SRT "hello" marker — receiver sends this to announce itself to the sender
_HELLO_MARKER = b"IPMX-SRT-HELLO\x00\x00"  # 16 bytes


# ---------------------------------------------------------------------------
# Sender (SRT Listener)
# ---------------------------------------------------------------------------

async def srt_sender(
    loop: asyncio.AbstractEventLoop,
    listen_ip: str,
    listen_port: int,
    sender_id: str,
    interface_name: str,
    event_queue: asyncio.Queue[EngineEvent] | None,
    encrypt_fn: Any | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    """SRT Listener sender: bind, wait for caller, then send packets.

    The sender waits for a "hello" packet from the receiver (caller),
    then sends test packets to the caller's address.
    """
    import uuid

    emit_activate(event_queue, sender_id, interface_name, is_sender=True)

    try:
        emit_starting(event_queue, sender_id, interface_name, is_sender=True)

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setblocking(False)
        sock.bind((listen_ip, listen_port))

        print(f"  [streaming] SRT Sender (Listener) {sender_id}")
        print(f"    Listening: {listen_ip}:{listen_port}")
        print(f"    PEP: {'enabled' if encrypt_fn else 'disabled'}")

        # Wait for caller's hello
        caller_addr = None
        while caller_addr is None and not (stop_event and stop_event.is_set()):
            try:
                data, addr = await asyncio.wait_for(
                    loop.sock_recvfrom(sock, len(_HELLO_MARKER) + 64),
                    timeout=SOCKET_TIMEOUT_S,
                )
                if data[:len(_HELLO_MARKER)] == _HELLO_MARKER:
                    caller_addr = addr
                    print(f"    Caller connected: {addr[0]}:{addr[1]}")
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return

        if caller_addr is None:
            return

        sid = uuid.UUID(sender_id)
        sequence = 0
        timestamp_ns = 0
        ctr = 0

        while not (stop_event and stop_event.is_set()):
            pkt = StreamPacket(
                sender_id=sid, sequence=sequence,
                timestamp_ns=timestamp_ns, period_ns=DEFAULT_PERIOD_NS,
                pep_ctr=ctr,
            )
            data = pkt.to_bytes()

            if encrypt_fn is not None:
                data = encrypt_fn(data, ctr)
                ctr += 1

            try:
                await loop.sock_sendto(sock, data, caller_addr)
            except OSError as exc:
                emit_transport_error(
                    event_queue, sender_id, interface_name, is_sender=True,
                    info=f"send error: {exc}",
                )

            sequence += 1
            timestamp_ns += DEFAULT_PERIOD_NS

            try:
                await asyncio.wait_for(
                    _wait_stop(stop_event), timeout=DEFAULT_PERIOD_NS / 1_000_000_000,
                )
                break
            except asyncio.TimeoutError:
                pass

    finally:
        emit_stopping(event_queue, sender_id, interface_name, is_sender=True)
        emit_deactivate(event_queue, sender_id, interface_name, is_sender=True)
        sock.close()


# ---------------------------------------------------------------------------
# Receiver (SRT Caller)
# ---------------------------------------------------------------------------

async def srt_receiver(
    loop: asyncio.AbstractEventLoop,
    dest_ip: str,
    dest_port: int,
    receiver_id: str,
    interface_name: str,
    event_queue: asyncio.Queue[EngineEvent] | None,
    decrypt_fn: Any | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    """SRT Caller receiver: send hello to listener, then receive packets."""
    emit_activate(event_queue, receiver_id, interface_name, is_sender=False)

    try:
        emit_starting(event_queue, receiver_id, interface_name, is_sender=False)

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setblocking(False)

        print(f"  [streaming] SRT Receiver (Caller) {receiver_id}")
        print(f"    Connecting to: {dest_ip}:{dest_port}")
        print(f"    PEP: {'enabled' if decrypt_fn else 'disabled'}")

        # Send hello to listener
        await loop.sock_sendto(sock, _HELLO_MARKER, (dest_ip, dest_port))

        reference_pkt: StreamPacket | None = None
        reference_time: float = 0.0
        reference_ts: int = 0
        ok = True
        pending_recovery: set[int] = set()

        while not (stop_event and stop_event.is_set()):
            try:
                data, addr = await asyncio.wait_for(
                    loop.sock_recvfrom(sock, PACKET_SIZE + 64),
                    timeout=SOCKET_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                emit_transport_error(
                    event_queue, receiver_id, interface_name, is_sender=False,
                    info="receiver socket timeout",
                )
                pending_recovery.add(EventId.TRANSPORT_OK)
                ok = False
                continue
            except asyncio.CancelledError:
                break
            except OSError as exc:
                emit_transport_error(
                    event_queue, receiver_id, interface_name, is_sender=False,
                    info=f"recv error: {exc}", link_down=True,
                )
                pending_recovery.add(EventId.TRANSPORT_OK)
                ok = False
                await asyncio.sleep(1.0)
                continue

            expected_size = PACKET_SIZE + PEP_CLEAR_HEADER_SIZE if decrypt_fn is not None else PACKET_SIZE
            if len(data) != expected_size:
                emit_event(event_queue, EngineEvent(
                    domain=AlertDomain.TRANSPORT, scope=AlertScope.RECEIVER,
                    event=EventId.TRANSPORT_STREAM_ERROR, state=EventState.WARNING,
                    count=1, id=receiver_id, name=interface_name,
                    info=f"receiver invalid packet size: {len(data)}, expected {expected_size}",
                ))
                pending_recovery.add(EventId.TRANSPORT_OK)
                ok = False
                continue

            if decrypt_fn is not None:
                try:
                    data, _ = decrypt_fn(data)
                except Exception as exc:
                    emit_event(event_queue, EngineEvent(
                        domain=AlertDomain.ESSENCE, scope=AlertScope.RECEIVER,
                        event=EventId.ESSENCE_STREAM_ERROR, state=EventState.WARNING,
                        count=1, id=receiver_id, name=interface_name,
                        info=f"decryption error: {exc}",
                    ))
                    pending_recovery.add(EventId.ESSENCE_OK)
                    ok = False
                    continue

            try:
                pkt = StreamPacket.from_bytes(data)
            except ValueError as exc:
                emit_event(event_queue, EngineEvent(
                    domain=AlertDomain.ESSENCE, scope=AlertScope.RECEIVER,
                    event=EventId.ESSENCE_STREAM_ERROR, state=EventState.WARNING,
                    count=1, id=receiver_id, name=interface_name,
                    info=f"packet parse error: {exc}",
                ))
                pending_recovery.add(EventId.ESSENCE_OK)
                ok = False
                continue

            if reference_pkt is None:
                reference_pkt = pkt
                reference_time = time.monotonic()
                reference_ts = pkt.timestamp_ns
                continue

            expected_seq = reference_pkt.sequence + 1
            if pkt.sequence != expected_seq:
                gap = abs(pkt.sequence - expected_seq)
                emit_event(event_queue, EngineEvent(
                    domain=AlertDomain.TRANSPORT, scope=AlertScope.RECEIVER,
                    event=EventId.TRANSPORT_PACKET_LOST, state=EventState.WARNING,
                    count=gap, id=receiver_id, name=interface_name,
                    info="receiver interrupted packet sequence",
                ))
                reference_pkt = pkt
                ok = False
                pending_recovery.add(EventId.TRANSPORT_OK)
                continue

            expected_arrival = reference_time + (pkt.timestamp_ns - reference_ts) / 1_000_000_000
            if time.monotonic() > expected_arrival + MAX_LATE_ARRIVAL_S:
                emit_event(event_queue, EngineEvent(
                    domain=AlertDomain.TRANSPORT, scope=AlertScope.RECEIVER,
                    event=EventId.TRANSPORT_PACKET_LATE, state=EventState.WARNING,
                    count=1, id=receiver_id, name=interface_name,
                    info="receiver packet late",
                ))
                reference_pkt = pkt
                reference_time = time.monotonic()
                reference_ts = pkt.timestamp_ns
                ok = False
                pending_recovery.add(EventId.TRANSPORT_OK)
                continue

            reference_pkt = pkt

            if not ok and pending_recovery:
                emit_recovery(
                    event_queue, receiver_id, interface_name,
                    is_sender=False, pending_events=pending_recovery,
                )
                pending_recovery.clear()
            ok = True

    finally:
        emit_stopping(event_queue, receiver_id, interface_name, is_sender=False)
        emit_deactivate(event_queue, receiver_id, interface_name, is_sender=False)
        sock.close()


async def _wait_stop(stop_event: asyncio.Event | None) -> None:
    if stop_event is None:
        await asyncio.Future()
    else:
        await stop_event.wait()
