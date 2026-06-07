# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Streaming engine sub-module.

Provides transport-level streaming for NMOS senders and receivers.
When IS-05 activates a sender/receiver, the streaming engine sends/receives
test packets over the configured transport (UDP multicast, SRT unicast, TCP).

Packet verification on the receiver side detects:
- Packet loss (sequence gaps)
- Late packets (>100ms)
- Source address mismatches
- Invalid packet sizes

Events are emitted to the Node's event queue using the engineEvent structure.
"""

from __future__ import annotations

import asyncio
from typing import Any

from nmos.node.events import EngineEvent
from nmos.node.types import Activation, EngineState


# Transport enum string → streaming function mapping
_MULTICAST_TRANSPORTS = {
    "urn:x-nmos:transport:rtp",
    "urn:x-nmos:transport:rtp.mcast",
    "urn:x-nmos:transport:udp",
}

_UNICAST_UDP_TRANSPORTS = {
    "urn:x-nmos:transport:rtp.ucast",
}

_SRT_TRANSPORTS = {
    "urn:x-matrox:transport:srt",
    "urn:x-nmos:transport:srt",
}

_TCP_TRANSPORTS = {
    "urn:x-matrox:transport:rtsp",
    "urn:x-nmos:transport:rtp-tcp",
    "urn:x-nmos:transport:usb",
    "urn:x-matrox:transport:usb",
    "urn:x-matrox:transport:ndi",
}

_NO_STREAMING = {
    "urn:x-nmos:transport:mqtt",
    "urn:x-nmos:transport:websocket",
}


def start_streaming(
    node: Any,
    activation: Activation,
    resource_id: str,
    is_sender: bool,
    transport_str: str,
    interface_name: str = "*",
) -> None:
    """Start streaming for an activated sender/receiver.

    Called from _manage_engine_lifecycle after IS-05 activation.
    Creates an asyncio task for the appropriate transport.
    The task runs in the background and is cancelled on deactivation.

    Dispatches to doSenderRtpStreaming/doReceiverRtpStreaming inside the
    activation's Engine DispatchGroup.
    """
    if transport_str in _NO_STREAMING:
        activation.engine_state = EngineState.INACTIVE
        return

    event_queue = getattr(node, 'event_queue', None)
    stop_event = asyncio.Event()
    activation.engine = stop_event  # Store for cancellation on deactivation

    # Build encryption functions if privacy enabled
    encrypt_fn = None
    decrypt_fn = None
    if node.privacy_enabled:
        from nmos.node.streaming.encryption import StreamEncryption
        enc = StreamEncryption.from_privacy(
            activation.privacy, activation.privacy_keys,
            resource_id, is_sender, verbose=True,
        )
        if enc is not None:
            encrypt_fn = enc.make_encrypt_fn()
            decrypt_fn = enc.make_decrypt_fn()

    # Extract transport params from active[0]
    params = activation.active[0] if activation.active else None
    if params is None:
        activation.engine_state = EngineState.ERROR
        return

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        activation.engine_state = EngineState.ERROR
        return

    # Dispatch the appropriate streaming coroutine
    coro = _build_streaming_coro(
        loop, transport_str, params, resource_id, interface_name,
        is_sender, event_queue, encrypt_fn, decrypt_fn, stop_event,
    )

    if coro is None:
        activation.engine_state = EngineState.INACTIVE
        return

    activation.engine_state = EngineState.ACTIVE
    asyncio.ensure_future(coro)


def stop_streaming(activation: Activation) -> None:
    """Stop streaming for a deactivated sender/receiver.

    Sets the stop_event stored in activation.engine.
    """
    if activation.engine is not None:
        if isinstance(activation.engine, asyncio.Event):
            activation.engine.set()
        activation.engine = None
    activation.engine_state = EngineState.INACTIVE


def _build_streaming_coro(
    loop: asyncio.AbstractEventLoop,
    transport_str: str,
    params: Any,
    resource_id: str,
    interface_name: str,
    is_sender: bool,
    event_queue: asyncio.Queue[EngineEvent] | None,
    encrypt_fn: Any | None,
    decrypt_fn: Any | None,
    stop_event: asyncio.Event,
) -> Any | None:
    """Build the streaming coroutine for the given transport.

    Returns None if the transport is not supported for streaming.
    """
    if transport_str in _MULTICAST_TRANSPORTS or transport_str in _UNICAST_UDP_TRANSPORTS:
        return _build_udp_coro(
            loop, params, resource_id, interface_name,
            is_sender, event_queue, encrypt_fn, decrypt_fn, stop_event,
        )
    elif transport_str in _SRT_TRANSPORTS:
        return _build_srt_coro(
            loop, params, resource_id, interface_name,
            is_sender, event_queue, encrypt_fn, decrypt_fn, stop_event,
        )
    elif transport_str in _TCP_TRANSPORTS:
        return _build_tcp_coro(
            params, resource_id, interface_name,
            is_sender, event_queue, encrypt_fn, decrypt_fn, stop_event,
        )
    return None


def _get_field(params: Any, name: str, default: Any = "") -> Any:
    """Safely get a field value from transport params."""
    field = getattr(params, name, None)
    if field is None:
        return default
    if hasattr(field, 'defined') and not field.defined:
        return default
    val = field.value if hasattr(field, 'value') else field
    if val is None:
        return default
    return val


def _build_udp_coro(
    loop: asyncio.AbstractEventLoop,
    params: Any,
    resource_id: str,
    interface_name: str,
    is_sender: bool,
    event_queue: Any,
    encrypt_fn: Any,
    decrypt_fn: Any,
    stop_event: asyncio.Event,
) -> Any:
    """Build UDP multicast/unicast coroutine from IS-05 transport params."""
    from nmos.node.streaming.transport_udp import udp_sender, udp_receiver

    if is_sender:
        source_ip = str(_get_field(params, "SourceIp", "0.0.0.0"))
        source_port = int(_get_field(params, "SourcePort", 0) or 0)
        dest_ip = str(_get_field(params, "DestinationIp", "0.0.0.0"))
        dest_port = int(_get_field(params, "DestinationPort", 0) or 0)

        return udp_sender(
            loop=loop,
            source_ip=source_ip, source_port=source_port,
            dest_ip=dest_ip, dest_port=dest_port,
            sender_id=resource_id, interface_name=interface_name,
            event_queue=event_queue,
            encrypt_fn=encrypt_fn,
            stop_event=stop_event,
        )
    else:
        interface_ip = str(_get_field(params, "InterfaceIp", "0.0.0.0"))
        multicast_ip = str(_get_field(params, "MulticastIp", ""))
        source_ip = str(_get_field(params, "SourceIp", ""))
        dest_port = int(_get_field(params, "DestinationPort", 0) or 0)

        return udp_receiver(
            loop=loop,
            interface_ip=interface_ip, multicast_ip=multicast_ip,
            source_ip=source_ip, dest_port=dest_port,
            receiver_id=resource_id, interface_name=interface_name,
            event_queue=event_queue,
            decrypt_fn=decrypt_fn,
            stop_event=stop_event,
        )


def _build_srt_coro(
    loop: asyncio.AbstractEventLoop,
    params: Any,
    resource_id: str,
    interface_name: str,
    is_sender: bool,
    event_queue: Any,
    encrypt_fn: Any,
    decrypt_fn: Any,
    stop_event: asyncio.Event,
) -> Any:
    """Build SRT UDP unicast coroutine from IS-05 transport params."""
    from nmos.node.streaming.transport_srt import srt_sender, srt_receiver

    if is_sender:
        listen_ip = str(_get_field(params, "SourceIp", "0.0.0.0"))
        listen_port = int(_get_field(params, "SourcePort", 0) or 0)

        return srt_sender(
            loop=loop,
            listen_ip=listen_ip, listen_port=listen_port,
            sender_id=resource_id, interface_name=interface_name,
            event_queue=event_queue,
            encrypt_fn=encrypt_fn,
            stop_event=stop_event,
        )
    else:
        dest_ip = str(_get_field(params, "DestinationIp", "0.0.0.0"))
        dest_port = int(_get_field(params, "DestinationPort", 0) or 0)

        return srt_receiver(
            loop=loop,
            dest_ip=dest_ip, dest_port=dest_port,
            receiver_id=resource_id, interface_name=interface_name,
            event_queue=event_queue,
            decrypt_fn=decrypt_fn,
            stop_event=stop_event,
        )


def _build_tcp_coro(
    params: Any,
    resource_id: str,
    interface_name: str,
    is_sender: bool,
    event_queue: Any,
    encrypt_fn: Any,
    decrypt_fn: Any,
    stop_event: asyncio.Event,
) -> Any:
    """Build TCP sender/receiver coroutine from IS-05 transport params."""
    from nmos.node.streaming.transport_tcp import tcp_sender, tcp_receiver

    if is_sender:
        listen_ip = str(_get_field(params, "SourceIp", "0.0.0.0"))
        listen_port = int(_get_field(params, "SourcePort", 0) or 0)

        return tcp_sender(
            listen_ip=listen_ip, listen_port=listen_port,
            sender_id=resource_id, interface_name=interface_name,
            event_queue=event_queue,
            encrypt_fn=encrypt_fn,
            stop_event=stop_event,
        )
    else:
        dest_ip = str(_get_field(params, "DestinationIp", "0.0.0.0"))
        dest_port = int(_get_field(params, "DestinationPort", 0) or 0)

        return tcp_receiver(
            dest_ip=dest_ip, dest_port=dest_port,
            receiver_id=resource_id, interface_name=interface_name,
            event_queue=event_queue,
            decrypt_fn=decrypt_fn,
            stop_event=stop_event,
        )
