# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""In-process E2E harness for PEP encrypted streaming tests.

Runs sender and receiver coroutines in the SAME asyncio loop on localhost
with real UDP/SRT/TCP sockets. Both sides use StreamEncryption contexts
constructed with the test-specified (protocol, mode, curve, substreamid,
key_version) parameters.

Verification:
- Direct attribute comparison (sender_enc.privacy_key == receiver_enc.privacy_key)
- Wire round-trip (zero decryption errors, zero magic mismatches)
- Event queue inspection (no ESSENCE_STREAM_ERROR, TRANSPORT_PACKET_LOST)
- [PEP-KDF] log line capture via capsys (optional, for Tier C cross-check)

This harness covers the plan's "all combinations must be tested in
end-to-end mode" requirement while keeping each test deterministic and
fast (~3-4s per combination).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from nmos.node.events import EngineEvent, EventId
from nmos.node.streaming.encryption import StreamEncryption, CLEAR_HEADER_SIZE
from nmos.node.streaming.tests._pep_helpers import (
    make_test_privacy,
    exchange_ecdh_keys,
    psk_for_mode,
    TEST_IV,
    TEST_KEY_GENERATOR,
    TEST_KEY_VERSION,
    TEST_KEY_ID,
)

# Default streaming duration per test (seconds).
DEFAULT_STREAM_DURATION = 3.0

# Port allocation: start high to avoid conflicts with other tests.
_PORT_COUNTER = 19000


def _next_port() -> int:
    """Return a unique port for each test invocation (simple bump)."""
    global _PORT_COUNTER
    _PORT_COUNTER += 2  # +2 to leave room for RTCP if needed
    return _PORT_COUNTER


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class PepE2EResult:
    """Captures the outcome of one E2E streaming run."""
    sender_enc: StreamEncryption | None = None
    receiver_enc: StreamEncryption | None = None
    sent_packets: int = 0        # inferred from stream duration × packet rate
    events: list[EngineEvent] = field(default_factory=list)

    @property
    def error_events_while_active(self) -> list[EngineEvent]:
        """Return error events that occurred BEFORE the sender deactivated."""
        deactivate_idx = None
        for i, e in enumerate(self.events):
            if e.event == EventId.VENDOR_TRANSPORT_DEACTIVATE:
                if deactivate_idx is None:
                    deactivate_idx = i
        pre = self.events[:deactivate_idx] if deactivate_idx else self.events
        return [
            e for e in pre
            if e.event in (
                EventId.TRANSPORT_PACKET_LOST,
                EventId.TRANSPORT_PACKET_LATE,
                EventId.ESSENCE_STREAM_ERROR,
                EventId.TRANSPORT_STREAM_ERROR,
            )
        ]


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------

def assert_keys_agree(result: PepE2EResult) -> None:
    """Assert sender and receiver derived the same privacy_key and iv_prime."""
    assert result.sender_enc is not None, "sender encryption context is None"
    assert result.receiver_enc is not None, "receiver encryption context is None"
    assert result.sender_enc.privacy_key == result.receiver_enc.privacy_key, (
        f"Key mismatch: sender={result.sender_enc.privacy_key.hex()} "
        f"receiver={result.receiver_enc.privacy_key.hex()}"
    )
    assert result.sender_enc.iv_prime == result.receiver_enc.iv_prime, (
        f"iv_prime mismatch: sender={result.sender_enc.iv_prime:016x} "
        f"receiver={result.receiver_enc.iv_prime:016x}"
    )


def assert_clean_round_trip(result: PepE2EResult) -> None:
    """Assert zero decryption/transport errors during the active streaming window."""
    errors = result.error_events_while_active
    assert not errors, (
        f"Errors during encrypted streaming: "
        f"{[(e.event.name if hasattr(e.event, 'name') else e.event, e.info) for e in errors]}"
    )


def assert_ecdh_pfs_present(result: PepE2EResult) -> None:
    """Assert both sides computed a non-empty ECDH PFS shared secret."""
    assert result.sender_enc is not None and len(result.sender_enc.key_pfs) > 0, \
        "sender key_pfs is empty — ECDH shared secret not computed"
    assert result.receiver_enc is not None and len(result.receiver_enc.key_pfs) > 0, \
        "receiver key_pfs is empty — ECDH shared secret not computed"


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------

async def run_pep_e2e(
    protocol: Any,
    transport: str,
    mode: Any,
    curve: Any = None,
    *,
    duration: float = DEFAULT_STREAM_DURATION,
    key_rotation_period_sec: float = 2.0,
    substreamid: int = 0,
    key_version_override: bytes | None = None,
) -> PepE2EResult:
    """Run one sender + one receiver for ``duration`` seconds and return results.

    This is the single function every parametrized E2E test calls.
    """
    mode_str = mode.value if hasattr(mode, 'value') else str(mode)

    # Build matching Privacy objects
    sender_priv, sender_keys = make_test_privacy(protocol, mode)
    receiver_priv, receiver_keys = make_test_privacy(protocol, mode)

    # Exchange ECDH keys if this is an ECDH mode
    is_ecdh = mode_str.startswith("ECDH_")
    if is_ecdh:
        if curve is not None:
            sender_priv.curve = curve
            receiver_priv.curve = curve
        exchange_ecdh_keys(sender_priv, receiver_priv, curve)

    sid = str(uuid.uuid4())
    rid = str(uuid.uuid4())

    # Build encryption contexts
    sender_enc = StreamEncryption.from_privacy(
        sender_priv, sender_keys, sid, is_sender=True, verbose=False,
        substreamid=substreamid,
        key_version_override=key_version_override,
        key_rotation_period_sec=key_rotation_period_sec,
    )
    receiver_enc = StreamEncryption.from_privacy(
        receiver_priv, receiver_keys, rid, is_sender=False, verbose=False,
        substreamid=substreamid,
        key_version_override=key_version_override,
    )

    if sender_enc is None or receiver_enc is None:
        raise RuntimeError("StreamEncryption.from_privacy returned None — missing PSK?")

    encrypt_fn = sender_enc.make_encrypt_fn()
    decrypt_fn = receiver_enc.make_decrypt_fn()

    # Dispatch the appropriate transport loopback
    port = _next_port()
    event_queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=500)
    sender_stop = asyncio.Event()
    receiver_stop = asyncio.Event()
    loop = asyncio.get_event_loop()

    transport_lower = transport.lower()

    if "udp" in transport_lower or "rtp_udp" in transport_lower or "rtp_mcast" in transport_lower:
        from nmos.node.streaming.transport_udp import udp_sender, udp_receiver
        sender_coro = udp_sender(
            loop=loop, source_ip="127.0.0.1", source_port=0,
            dest_ip="127.0.0.1", dest_port=port,
            sender_id=sid, interface_name="lo",
            event_queue=event_queue, encrypt_fn=encrypt_fn,
            stop_event=sender_stop,
        )
        receiver_coro = udp_receiver(
            loop=loop, interface_ip="127.0.0.1", multicast_ip="",
            source_ip="", dest_port=port,
            receiver_id=rid, interface_name="lo",
            event_queue=event_queue, decrypt_fn=decrypt_fn,
            stop_event=receiver_stop,
        )

    elif "srt" in transport_lower:
        from nmos.node.streaming.transport_srt import srt_sender, srt_receiver
        sender_coro = srt_sender(
            loop=loop, listen_ip="127.0.0.1", listen_port=port,
            sender_id=sid, interface_name="lo",
            event_queue=event_queue, encrypt_fn=encrypt_fn,
            stop_event=sender_stop,
        )
        receiver_coro = srt_receiver(
            loop=loop, dest_ip="127.0.0.1", dest_port=port,
            receiver_id=rid, interface_name="lo",
            event_queue=event_queue, decrypt_fn=decrypt_fn,
            stop_event=receiver_stop,
        )

    elif "tcp" in transport_lower or "rtsp" in transport_lower or "usb" in transport_lower:
        from nmos.node.streaming.transport_tcp import tcp_sender, tcp_receiver
        sender_coro = tcp_sender(
            listen_ip="127.0.0.1", listen_port=port,
            sender_id=sid, interface_name="lo",
            event_queue=event_queue, encrypt_fn=encrypt_fn,
            stop_event=sender_stop,
        )
        receiver_coro = tcp_receiver(
            dest_ip="127.0.0.1", dest_port=port,
            receiver_id=rid, interface_name="lo",
            event_queue=event_queue, decrypt_fn=decrypt_fn,
            stop_event=receiver_stop,
        )

    else:
        raise ValueError(f"unsupported transport for E2E test: {transport}")

    # Launch: for TCP/SRT the SENDER is the listener — start it first.
    # For UDP the RECEIVER binds first.
    if "tcp" in transport_lower or "rtsp" in transport_lower or "usb" in transport_lower:
        # TCP-based: sender listens, receiver connects
        sender_task = asyncio.create_task(sender_coro)
        await asyncio.sleep(0.5)  # Let sender bind/listen
        receiver_task = asyncio.create_task(receiver_coro)
    elif "srt" in transport_lower:
        # SRT: sender is the listener, receiver is the caller
        sender_task = asyncio.create_task(sender_coro)
        await asyncio.sleep(0.5)
        receiver_task = asyncio.create_task(receiver_coro)
    else:
        # UDP: receiver binds first
        receiver_task = asyncio.create_task(receiver_coro)
        await asyncio.sleep(0.3)
        sender_task = asyncio.create_task(sender_coro)

    await asyncio.sleep(duration)
    sender_stop.set()
    await asyncio.sleep(0.5)  # Let final packets drain
    receiver_stop.set()
    await asyncio.gather(sender_task, receiver_task, return_exceptions=True)

    # Collect events
    events: list[EngineEvent] = []
    while not event_queue.empty():
        events.append(event_queue.get_nowait())

    return PepE2EResult(
        sender_enc=sender_enc,
        receiver_enc=receiver_enc,
        sent_packets=int(duration),  # ~1 pkt/s for test packets
        events=events,
    )
