# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""TCP client-server transport for the streaming engine.

Handles RTSP, RTP-TCP, and NDI transport types. The sender listens for
a TCP connection; the receiver connects to the sender. Test packets are
sent/received over the TCP stream with a 4-byte length prefix.

Wire format: [length: uint32 LE][packet: 1432 bytes]
The length prefix enables reliable framing over the TCP byte stream.
"""

from __future__ import annotations

import asyncio
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
from nmos.node.streaming.transport_udp import MAX_LATE_ARRIVAL_S

# TCP framing: 4-byte little-endian length prefix
_LEN_FORMAT = "<I"
_LEN_SIZE = struct.calcsize(_LEN_FORMAT)  # 4 bytes


# ---------------------------------------------------------------------------
# Sender (TCP Listener)
# ---------------------------------------------------------------------------

async def tcp_sender(
    listen_ip: str,
    listen_port: int,
    sender_id: str,
    interface_name: str,
    event_queue: asyncio.Queue[EngineEvent] | None,
    encrypt_fn: Any | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    """TCP sender: listen for connections, then send test packets to each.

    Used for RTSP, RTP-TCP, USB and NDI transports.

    **Many clients, one stream, served independently.** A listening socket is
    not a point-to-point link: several receivers may connect to the same
    sender, and each has to get the stream and be able to leave without
    affecting the others.

    Two properties follow, and both were previously absent:

    * The frame is built and encrypted **once** per packet and fanned out
      to every connected client. It must not be encrypted per client — the
      PEP counter ``ctr`` advances once per packet, so re-encrypting would
      give each client a different counter for the same stream position.
    * A failing write drops **that** client only. The stream continues for
      everyone else, and continues when the last client leaves, because a
      sender that no receiver happens to be reading is idle rather than
      broken — the same stance the multicast UDP sender takes, where the
      sender has no idea who is listening.

    The earlier version kept a list of accepted writers but only ever wrote
    to ``client_writer[0]``: a second receiver connected, was never sent
    anything, and timed out; and when the first receiver disconnected the
    write error broke the loop, which took the whole sender down (listener
    closed, engine state ERROR) while IS-04 and IS-05 still advertised it as
    actively transmitting.
    """
    import uuid

    emit_activate(event_queue, sender_id, interface_name, is_sender=True)

    server: asyncio.Server | None = None
    # Insertion-ordered so the fan-out is deterministic; a dict is used as an
    # ordered set because StreamWriter is not hashable-by-value but is
    # hashable by identity, which is exactly the identity we want.
    clients: dict[asyncio.StreamWriter, str] = {}

    try:
        emit_starting(event_queue, sender_id, interface_name, is_sender=True)

        async def handle_client(
            reader: asyncio.StreamReader, w: asyncio.StreamWriter,
        ) -> None:
            peer = w.get_extra_info("peername")
            clients[w] = str(peer)
            print(f"    Client connected: {peer} ({len(clients)} total)")

        # A listen failure (port in use, address not local) is reported and
        # re-raised: the event drives the monitor, the exception takes
        # engine_state to ERROR (see ``streaming._on_streaming_done``).
        # Unreported, it left the Node advertising a healthy sender that was
        # never listening.
        try:
            server = await asyncio.start_server(
                handle_client, listen_ip, listen_port,
            )
        except OSError as exc:
            emit_transport_error(
                event_queue, sender_id, interface_name, is_sender=True,
                info=f"sender listen failed: {exc}",
            )
            raise

        print(f"  [streaming] TCP Sender (Listener) {sender_id}")
        print(f"    Listening: {listen_ip}:{listen_port}")
        print(f"    PEP: {'enabled' if encrypt_fn else 'disabled'}")

        # No wait for a first client, and no deadline on one arriving.
        #
        # This used to block for up to 30 s and then report a transport error
        # and return, which ended the sender: the listener closed, so a client
        # arriving on the 31st second found nothing to connect to. That is
        # incompatible with serving several receivers, which by nature attach
        # at times of the operator's choosing — in the reference rig, simply
        # activating the sender and then walking the UI to a second receiver
        # takes a comparable time.
        #
        # A sender with no clients is idle, not faulty, so no alert is raised
        # for it either. That matches the multicast UDP sender, which likewise
        # transmits without knowing whether anything is listening, and keeps
        # transmissionStatus Healthy while it does so.
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

            # TCP framing: length prefix + payload
            frame = struct.pack(_LEN_FORMAT, len(data)) + data

            # Fan out to every client. Iterate over a snapshot so a client
            # dropped mid-loop doesn't mutate the mapping being walked.
            for writer, peer in list(clients.items()):
                try:
                    writer.write(frame)
                    await writer.drain()
                except (ConnectionError, OSError) as exc:
                    # A client going away is ordinary for a listening
                    # transport, so this raises no alert: the sender is not
                    # faulty, its link is not down, and the remaining clients
                    # are unaffected. Reporting it as a transport error with
                    # ``link_down=True`` — which is what happened before —
                    # published linkStatus AllDown for a remote socket close,
                    # sending an operator to inspect network interfaces that
                    # were never in trouble.
                    clients.pop(writer, None)
                    print(f"    Client disconnected: {peer} "
                          f"({exc}); {len(clients)} remaining")
                    try:
                        writer.close()
                    except OSError:
                        pass

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
        for writer in list(clients):
            try:
                writer.close()
            except OSError:
                pass
        clients.clear()
        if server is not None:
            server.close()
        emit_stopping(event_queue, sender_id, interface_name, is_sender=True)
        emit_deactivate(event_queue, sender_id, interface_name, is_sender=True)


# ---------------------------------------------------------------------------
# Receiver (TCP Connector)
# ---------------------------------------------------------------------------

async def tcp_receiver(
    dest_ip: str,
    dest_port: int,
    receiver_id: str,
    interface_name: str,
    event_queue: asyncio.Queue[EngineEvent] | None,
    decrypt_fn: Any | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    """TCP receiver: connect to sender, receive and verify test packets.

    Used for RTSP, RTP-TCP, NDI transports.
    """
    emit_activate(event_queue, receiver_id, interface_name, is_sender=False)

    reader: asyncio.StreamReader | None = None
    writer: asyncio.StreamWriter | None = None

    try:
        emit_starting(event_queue, receiver_id, interface_name, is_sender=False)

        print(f"  [streaming] TCP Receiver (Connector) {receiver_id}")
        print(f"    Connecting to: {dest_ip}:{dest_port}")
        print(f"    PEP: {'enabled' if decrypt_fn else 'disabled'}")

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(dest_ip, dest_port),
                timeout=10.0,
            )
        except (asyncio.TimeoutError, ConnectionError, OSError) as exc:
            emit_transport_error(
                event_queue, receiver_id, interface_name, is_sender=False,
                info=f"connect error: {exc}",
            )
            return

        assert reader is not None
        assert writer is not None
        print(f"    Connected")

        reference_pkt: StreamPacket | None = None
        reference_time: float = 0.0
        reference_ts: int = 0
        ok = True
        pending_recovery: set[int] = set()

        while not (stop_event and stop_event.is_set()):
            # Read length prefix
            try:
                len_data = await asyncio.wait_for(
                    reader.readexactly(_LEN_SIZE), timeout=3.0,
                )
            except asyncio.TimeoutError:
                emit_transport_error(
                    event_queue, receiver_id, interface_name, is_sender=False,
                    info="receiver socket timeout",
                )
                pending_recovery.add(EventId.TRANSPORT_OK)
                ok = False
                continue
            except (asyncio.IncompleteReadError, ConnectionError, OSError):
                emit_transport_error(
                    event_queue, receiver_id, interface_name, is_sender=False,
                    info="connection lost",
                )
                break

            payload_len = struct.unpack(_LEN_FORMAT, len_data)[0]

            expected_size = PACKET_SIZE + PEP_CLEAR_HEADER_SIZE if decrypt_fn is not None else PACKET_SIZE
            if payload_len != expected_size:
                emit_event(event_queue, EngineEvent(
                    domain=AlertDomain.TRANSPORT, scope=AlertScope.RECEIVER,
                    event=EventId.TRANSPORT_STREAM_ERROR, state=EventState.WARNING,
                    count=1, id=receiver_id, name=interface_name,
                    info=f"invalid frame size: {payload_len}, expected {expected_size}",
                ))
                pending_recovery.add(EventId.TRANSPORT_OK)
                ok = False
                continue

            # Read payload
            try:
                data = await asyncio.wait_for(
                    reader.readexactly(payload_len), timeout=3.0,
                )
            except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
                emit_transport_error(
                    event_queue, receiver_id, interface_name, is_sender=False,
                    info="incomplete frame",
                )
                break

            # Decrypt
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

            # Parse
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
        if writer is not None:
            writer.close()
        emit_stopping(event_queue, receiver_id, interface_name, is_sender=False)
        emit_deactivate(event_queue, receiver_id, interface_name, is_sender=False)


async def _wait_stop(stop_event: asyncio.Event | None) -> None:
    if stop_event is None:
        await asyncio.Future()
    else:
        await stop_event.wait()


# ---------------------------------------------------------------------------
# Bidirectional TCP (RTSP, USB/TCP)
#
# Per TR-10-13 §14: forward direction uses substreamid=0, reverse uses
# substreamid=1. Each direction has its own key_version and derives its
# own privacy_key. The encrypt/decrypt functions are pre-wired by the
# caller with the correct substreamid and key_version.
# ---------------------------------------------------------------------------

async def tcp_bidirectional_sender(
    listen_ip: str,
    listen_port: int,
    sender_id: str,
    interface_name: str,
    event_queue: asyncio.Queue[EngineEvent] | None,
    tx_encrypt_fn: Any | None = None,   # forward: sender→receiver (substreamid=0)
    rx_decrypt_fn: Any | None = None,   # reverse: receiver→sender (substreamid=1)
    stop_event: asyncio.Event | None = None,
) -> dict[str, int]:
    """Bidirectional TCP sender (listener): sends forward packets and
    receives reverse packets on the same TCP connection.

    Returns a dict with ``tx_count`` and ``rx_count`` for test assertions.
    """
    import uuid

    emit_activate(event_queue, sender_id, interface_name, is_sender=True)
    result = {"tx_count": 0, "rx_count": 0, "rx_errors": 0}

    writer: asyncio.StreamWriter | None = None
    server: asyncio.Server | None = None

    try:
        emit_starting(event_queue, sender_id, interface_name, is_sender=True)

        connected = asyncio.Event()
        client_streams: list[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = []

        async def handle_client(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
            client_streams.append((r, w))
            connected.set()

        # A listen failure (port in use, address not local) is reported and
        # re-raised: the event drives the monitor, the exception takes
        # engine_state to ERROR (see ``streaming._on_streaming_done``).
        # Unreported, it left the Node advertising a healthy sender that was
        # never listening.
        try:
            server = await asyncio.start_server(
                handle_client, listen_ip, listen_port,
            )
        except OSError as exc:
            emit_transport_error(
                event_queue, sender_id, interface_name, is_sender=True,
                info=f"sender listen failed: {exc}",
            )
            raise

        try:
            await asyncio.wait_for(connected.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            return result

        reader, writer = client_streams[0]

        sid = uuid.UUID(sender_id)
        ctr = 0

        async def _tx_loop() -> None:
            nonlocal ctr
            sequence = 0
            timestamp_ns = 0
            while not (stop_event and stop_event.is_set()):
                pkt = StreamPacket(
                    sender_id=sid, sequence=sequence,
                    timestamp_ns=timestamp_ns, period_ns=DEFAULT_PERIOD_NS,
                    pep_ctr=ctr,
                )
                data = pkt.to_bytes()
                if tx_encrypt_fn is not None:
                    data = tx_encrypt_fn(data, ctr)
                    ctr += 1
                frame = struct.pack(_LEN_FORMAT, len(data)) + data
                try:
                    writer.write(frame)
                    await writer.drain()
                except (ConnectionError, OSError):
                    break
                sequence += 1
                timestamp_ns += DEFAULT_PERIOD_NS
                result["tx_count"] = sequence
                try:
                    await asyncio.wait_for(
                        _wait_stop(stop_event),
                        timeout=DEFAULT_PERIOD_NS / 1_000_000_000,
                    )
                    break
                except asyncio.TimeoutError:
                    pass

        async def _rx_loop() -> None:
            while not (stop_event and stop_event.is_set()):
                try:
                    len_data = await asyncio.wait_for(
                        reader.readexactly(_LEN_SIZE), timeout=3.0,
                    )
                except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
                    break
                payload_len = struct.unpack(_LEN_FORMAT, len_data)[0]
                try:
                    data = await asyncio.wait_for(
                        reader.readexactly(payload_len), timeout=3.0,
                    )
                except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
                    break
                if rx_decrypt_fn is not None:
                    try:
                        data, _ = rx_decrypt_fn(data)
                    except Exception:
                        result["rx_errors"] += 1
                        continue
                try:
                    StreamPacket.from_bytes(data)
                    result["rx_count"] += 1
                except ValueError:
                    result["rx_errors"] += 1

        await asyncio.gather(_tx_loop(), _rx_loop(), return_exceptions=True)

    finally:
        if writer is not None:
            writer.close()
        if server is not None:
            server.close()
        emit_stopping(event_queue, sender_id, interface_name, is_sender=True)
        emit_deactivate(event_queue, sender_id, interface_name, is_sender=True)

    return result


async def tcp_bidirectional_receiver(
    dest_ip: str,
    dest_port: int,
    receiver_id: str,
    interface_name: str,
    event_queue: asyncio.Queue[EngineEvent] | None,
    rx_decrypt_fn: Any | None = None,   # forward: sender→receiver (substreamid=0)
    tx_encrypt_fn: Any | None = None,   # reverse: receiver→sender (substreamid=1)
    stop_event: asyncio.Event | None = None,
) -> dict[str, int]:
    """Bidirectional TCP receiver (connector): receives forward packets and
    sends reverse packets on the same TCP connection.

    Returns a dict with ``rx_count`` and ``tx_count`` for test assertions.
    """
    import uuid

    emit_activate(event_queue, receiver_id, interface_name, is_sender=False)
    result = {"rx_count": 0, "tx_count": 0, "rx_errors": 0}

    reader: asyncio.StreamReader | None = None
    writer: asyncio.StreamWriter | None = None

    try:
        emit_starting(event_queue, receiver_id, interface_name, is_sender=False)

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(dest_ip, dest_port), timeout=10.0,
            )
        except (asyncio.TimeoutError, ConnectionError, OSError) as exc:
            # Was a silent return: no event, so the monitor read healthy for a
            # receiver that never connected. Mirrors ``tcp_receiver``.
            emit_transport_error(
                event_queue, receiver_id, interface_name, is_sender=False,
                info=f"connect error: {exc}",
            )
            return result

        assert reader is not None
        assert writer is not None
        rid = uuid.UUID(receiver_id)
        ctr = 0

        async def _rx_loop() -> None:
            while not (stop_event and stop_event.is_set()):
                try:
                    len_data = await asyncio.wait_for(
                        reader.readexactly(_LEN_SIZE), timeout=3.0,
                    )
                except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
                    break
                payload_len = struct.unpack(_LEN_FORMAT, len_data)[0]
                try:
                    data = await asyncio.wait_for(
                        reader.readexactly(payload_len), timeout=3.0,
                    )
                except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
                    break
                if rx_decrypt_fn is not None:
                    try:
                        data, _ = rx_decrypt_fn(data)
                    except Exception:
                        result["rx_errors"] += 1
                        continue
                try:
                    StreamPacket.from_bytes(data)
                    result["rx_count"] += 1
                except ValueError:
                    result["rx_errors"] += 1

        async def _tx_loop() -> None:
            nonlocal ctr
            sequence = 0
            timestamp_ns = 0
            while not (stop_event and stop_event.is_set()):
                pkt = StreamPacket(
                    sender_id=rid, sequence=sequence,
                    timestamp_ns=timestamp_ns, period_ns=DEFAULT_PERIOD_NS,
                    pep_ctr=ctr,
                )
                data = pkt.to_bytes()
                if tx_encrypt_fn is not None:
                    data = tx_encrypt_fn(data, ctr)
                    ctr += 1
                frame = struct.pack(_LEN_FORMAT, len(data)) + data
                try:
                    writer.write(frame)
                    await writer.drain()
                except (ConnectionError, OSError):
                    break
                sequence += 1
                timestamp_ns += DEFAULT_PERIOD_NS
                result["tx_count"] = sequence
                try:
                    await asyncio.wait_for(
                        _wait_stop(stop_event),
                        timeout=DEFAULT_PERIOD_NS / 1_000_000_000,
                    )
                    break
                except asyncio.TimeoutError:
                    pass

        await asyncio.gather(_rx_loop(), _tx_loop(), return_exceptions=True)

    finally:
        if writer is not None:
            writer.close()
        emit_stopping(event_queue, receiver_id, interface_name, is_sender=False)
        emit_deactivate(event_queue, receiver_id, interface_name, is_sender=False)

    return result
