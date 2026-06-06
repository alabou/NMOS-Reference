# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""UDP multicast/unicast transport for the streaming engine.

Handles RTP and UDP transport types. Senders transmit test packets to a
multicast group (or unicast destination). Receivers join the multicast
group and verify received packets.

Uses asyncio DatagramProtocol for integration with the async event loop.
Multicast socket options (IP_ADD_MEMBERSHIP, IP_MULTICAST_IF, etc.) are
set via the socket module after protocol creation.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import struct
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

# maxLateArrival = 100ms
MAX_LATE_ARRIVAL_NS = 100_000_000  # 100ms in nanoseconds
MAX_LATE_ARRIVAL_S = 0.1           # 100ms in seconds

# maxSocketAlive = 3 seconds
SOCKET_TIMEOUT_S = 3.0


def _to_numeric(addr: str, family: int) -> str:
    """Return a numeric form of ``addr`` for the given socket family.

    Accepts either a literal IP (returned verbatim) or a DNS name
    (resolved via ``getaddrinfo``). Required because transport params
    carry hostnames such as ``XYZ-SNX00001`` whenever TLS is enabled,
    and ``socket.inet_aton`` / ``socket.inet_pton`` reject anything
    that isn't already a numeric address.
    """
    try:
        if family == socket.AF_INET6:
            socket.inet_pton(socket.AF_INET6, addr)
        else:
            socket.inet_aton(addr)
        return addr
    except OSError:
        infos = socket.getaddrinfo(addr, None, family=family, type=socket.SOCK_DGRAM)
        resolved = infos[0][4][0]
        assert isinstance(resolved, str)
        return resolved


# ---------------------------------------------------------------------------
# Sender
# ---------------------------------------------------------------------------

async def udp_sender(
    loop: asyncio.AbstractEventLoop,
    source_ip: str,
    source_port: int,
    dest_ip: str,
    dest_port: int,
    sender_id: str,
    interface_name: str,
    event_queue: asyncio.Queue[EngineEvent] | None,
    encrypt_fn: Any | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Send test packets over UDP (multicast or unicast).

    Args:
        source_ip: Local interface IP to bind.
        source_port: Local UDP port.
        dest_ip: Destination IP (multicast group or unicast address).
        dest_port: Destination UDP port.
        sender_id: UUID string for packet sender_id field.
        interface_name: Network interface name (for events).
        event_queue: Node's event queue for streaming events.
        encrypt_fn: Optional callable(packet_bytes, ctr) -> encrypted_bytes.
        stop_event: Set this event to stop the sender gracefully.
    """
    import uuid

    is_multicast = _is_multicast(dest_ip)

    # Emit lifecycle events
    emit_activate(event_queue, sender_id, interface_name, is_sender=True)

    try:
        emit_starting(event_queue, sender_id, interface_name, is_sender=True)

        # Create UDP socket
        sock = socket.socket(
            socket.AF_INET6 if ":" in source_ip else socket.AF_INET,
            socket.SOCK_DGRAM,
            socket.IPPROTO_UDP,
        )
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setblocking(False)

        try:
            sock.bind((source_ip, source_port))
        except OSError:
            # Fallback: bind to any interface
            sock.bind(("", source_port))

        if is_multicast:
            # Set multicast interface
            if ":" in source_ip:
                # IPv6: IPV6_MULTICAST_IF
                idx = _get_interface_index(_to_numeric(source_ip, socket.AF_INET6))
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_IF, idx)
            else:
                # IPv4: IP_MULTICAST_IF
                sock.setsockopt(
                    socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                    socket.inet_aton(_to_numeric(source_ip, socket.AF_INET)),
                )
            # TTL = 255 (MulticastTTL)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
            # Allow loopback for local testing
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)

        dest_addr = (dest_ip, dest_port)
        sid = uuid.UUID(sender_id)
        sequence = 0
        timestamp_ns = 0
        period_ns = DEFAULT_PERIOD_NS
        ctr = 0

        print(f"  [streaming] Sender {sender_id}")
        print(f"    Transport: UDP {'multicast' if is_multicast else 'unicast'} → {dest_ip}:{dest_port}")
        print(f"    Source: {source_ip}:{source_port}")
        print(f"    PEP: {'enabled' if encrypt_fn else 'disabled'}")

        while not (stop_event and stop_event.is_set()):
            pkt = StreamPacket(
                sender_id=sid,
                sequence=sequence,
                timestamp_ns=timestamp_ns,
                period_ns=period_ns,
                pep_ctr=ctr,
                pep_key_version=0,  # Set by encrypt_fn if PEP enabled
            )
            data = pkt.to_bytes()

            if encrypt_fn is not None:
                data = encrypt_fn(data, ctr)
                ctr += 1

            try:
                await loop.sock_sendto(sock, data, dest_addr)
            except OSError as exc:
                emit_transport_error(
                    event_queue, sender_id, interface_name, is_sender=True,
                    info=f"send error: {exc}",
                )

            sequence += 1
            timestamp_ns += period_ns

            try:
                await asyncio.wait_for(
                    _wait_stop(stop_event), timeout=period_ns / 1_000_000_000,
                )
                break  # stop_event was set
            except asyncio.TimeoutError:
                pass  # Normal — send next packet

    finally:
        emit_stopping(event_queue, sender_id, interface_name, is_sender=True)
        emit_deactivate(event_queue, sender_id, interface_name, is_sender=True)
        sock.close()


# ---------------------------------------------------------------------------
# Receiver
# ---------------------------------------------------------------------------

async def udp_receiver(
    loop: asyncio.AbstractEventLoop,
    interface_ip: str,
    multicast_ip: str,
    source_ip: str,
    dest_port: int,
    receiver_id: str,
    interface_name: str,
    event_queue: asyncio.Queue[EngineEvent] | None,
    decrypt_fn: Any | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Receive and verify test packets over UDP (multicast or unicast).

    Args:
        interface_ip: Local interface IP to bind.
        multicast_ip: Multicast group to join (empty for unicast).
        source_ip: Expected source IP (empty to accept any).
        dest_port: Local UDP port to listen on.
        receiver_id: UUID string for events.
        interface_name: Network interface name (for events).
        event_queue: Node's event queue.
        decrypt_fn: Optional callable(encrypted_bytes) -> (plaintext_bytes, ctr).
        stop_event: Set this event to stop the receiver gracefully.
    """
    is_multicast = _is_multicast(multicast_ip) if multicast_ip else False

    # Source-filter compares numeric IPs from ``recvfrom`` against
    # ``source_ip``; resolve any hostname (e.g. ``XYZ-SNX00001`` under
    # TLS-enabled configs) to its numeric form once up-front so every
    # packet's source address matches as the spec expects.
    if source_ip:
        family = socket.AF_INET6 if ":" in source_ip else socket.AF_INET
        try:
            source_ip = _to_numeric(source_ip, family)
        except OSError:
            pass  # leave as-is; comparison will then never match

    emit_activate(event_queue, receiver_id, interface_name, is_sender=False)

    try:
        emit_starting(event_queue, receiver_id, interface_name, is_sender=False)

        # Create UDP socket
        family = socket.AF_INET6 if ":" in interface_ip else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, 'SO_REUSEPORT'):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.setblocking(False)

        if is_multicast:
            # Bind to multicast group address + port
            sock.bind((multicast_ip, dest_port))
            # Join multicast group
            if ":" in multicast_ip:
                iface_num = _to_numeric(interface_ip, socket.AF_INET6)
                idx = _get_interface_index(iface_num)
                mreq = struct.pack(
                    "16sI",
                    socket.inet_pton(socket.AF_INET6, _to_numeric(multicast_ip, socket.AF_INET6)),
                    idx,
                )
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_JOIN_GROUP, mreq)
            else:
                mreq = struct.pack(
                    "4s4s",
                    socket.inet_aton(_to_numeric(multicast_ip, socket.AF_INET)),
                    socket.inet_aton(_to_numeric(interface_ip, socket.AF_INET)),
                )
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        else:
            # Unicast: bind to interface
            sock.bind((interface_ip, dest_port))

        print(f"  [streaming] Receiver {receiver_id}")
        print(f"    Transport: UDP {'multicast ' + multicast_ip if is_multicast else 'unicast'}:{dest_port}")
        print(f"    Interface: {interface_ip}")
        print(f"    Source filter: {source_ip or 'any'}")
        print(f"    PEP: {'enabled' if decrypt_fn else 'disabled'}")

        # Verification state (referenceInfo/referenceTime pattern)
        reference_pkt: StreamPacket | None = None
        reference_time: float = 0.0
        reference_ts: int = 0
        ok = True
        pending_recovery: set[int] = set()

        buf = bytearray(PACKET_SIZE + 64)  # Slightly oversized for safety

        while not (stop_event and stop_event.is_set()):
            try:
                nbytes, addr = await asyncio.wait_for(
                    loop.sock_recvfrom(sock, PACKET_SIZE + 64),
                    timeout=SOCKET_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                # No packets for 3 seconds — transport error
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

            data = nbytes if isinstance(nbytes, bytes) else bytes(nbytes)
            sender_addr = addr[0] if addr else ""

            # Verify packet size.
            # When PEP encryption is enabled, packets include a clear header:
            # [12B pep_ctr + key_version] + [1432B encrypted payload] = 1444 bytes.
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

            # Verify source address
            if source_ip and sender_addr and sender_addr != source_ip:
                emit_event(event_queue, EngineEvent(
                    domain=AlertDomain.TRANSPORT, scope=AlertScope.RECEIVER,
                    event=EventId.TRANSPORT_STREAM_ERROR, state=EventState.WARNING,
                    count=1, id=receiver_id, name=interface_name,
                    info=f"receiver invalid source address: {sender_addr} (expected {source_ip})",
                ))
                pending_recovery.add(EventId.TRANSPORT_OK)
                ok = False
                continue

            # Decrypt if PEP enabled
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

            # Parse packet
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

            # First packet: establish reference
            if reference_pkt is None:
                reference_pkt = pkt
                reference_time = time.monotonic()
                reference_ts = pkt.timestamp_ns
                continue

            # Sequence check
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

            # Timing check
            expected_arrival = reference_time + (pkt.timestamp_ns - reference_ts) / 1_000_000_000
            if time.monotonic() > expected_arrival + MAX_LATE_ARRIVAL_S:
                emit_event(event_queue, EngineEvent(
                    domain=AlertDomain.TRANSPORT, scope=AlertScope.RECEIVER,
                    event=EventId.TRANSPORT_PACKET_LATE, state=EventState.WARNING,
                    count=1, id=receiver_id, name=interface_name,
                    info="receiver packet late",
                ))
                reference_pkt = pkt
                # Re-anchor time reference
                reference_time = time.monotonic()
                reference_ts = pkt.timestamp_ns
                ok = False
                pending_recovery.add(EventId.TRANSPORT_OK)
                continue

            # Update reference for next packet
            reference_pkt = pkt

            # Emit recovery events if we were in error state
            if not ok and pending_recovery:
                emit_recovery(
                    event_queue, receiver_id, interface_name,
                    is_sender=False, pending_events=pending_recovery,
                )
                pending_recovery.clear()
            ok = True

    finally:
        # Leave multicast group
        if is_multicast:
            try:
                if ":" in multicast_ip:
                    iface_num = _to_numeric(interface_ip, socket.AF_INET6)
                    idx = _get_interface_index(iface_num)
                    mreq = struct.pack(
                        "16sI",
                        socket.inet_pton(socket.AF_INET6, _to_numeric(multicast_ip, socket.AF_INET6)),
                        idx,
                    )
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_LEAVE_GROUP, mreq)
                else:
                    mreq = struct.pack(
                        "4s4s",
                        socket.inet_aton(_to_numeric(multicast_ip, socket.AF_INET)),
                        socket.inet_aton(_to_numeric(interface_ip, socket.AF_INET)),
                    )
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, mreq)
            except OSError:
                pass

        emit_stopping(event_queue, receiver_id, interface_name, is_sender=False)
        emit_deactivate(event_queue, receiver_id, interface_name, is_sender=False)
        sock.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_multicast(ip: str) -> bool:
    """Check if an IP address is multicast."""
    if not ip or ip in ("0.0.0.0", "::", "auto"):
        return False
    try:
        return ipaddress.ip_address(ip).is_multicast
    except ValueError:
        return False


def _get_interface_index(ip: str) -> int:
    """Get network interface index for an IP address. Returns 0 if not found."""
    from nmos.netifaces_compat import get_interface_index_for_address

    return get_interface_index_for_address(ip)


async def _wait_stop(stop_event: asyncio.Event | None) -> None:
    """Wait for stop_event to be set, or block forever if None."""
    if stop_event is None:
        await asyncio.Future()  # Never resolves
    else:
        await stop_event.wait()
