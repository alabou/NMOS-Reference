# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""E2E bidirectional PEP tests — RTSP and USB/TCP with substreamid 0/1.

Verifies TR-10-13 §14: bidirectional streams use even substreamid for
sender→receiver (forward) and odd substreamid for receiver→sender (reverse).
Each direction derives its own privacy_key from its own key_version.

Both directions stream encrypted traffic simultaneously over a single TCP
connection and both sides decrypt the peer's packets correctly.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

_PEP_PATH = str(Path(__file__).parent.parent.parent.parent.parent / "pep")
if _PEP_PATH not in sys.path:
    sys.path.insert(0, _PEP_PATH)

from ipmx_pep import PepMode, PepProtocol  # noqa: E402

from nmos.node.events import EngineEvent  # noqa: E402
from nmos.node.streaming.encryption import StreamEncryption  # noqa: E402
from nmos.node.streaming.transport_tcp import (  # noqa: E402
    tcp_bidirectional_sender,
    tcp_bidirectional_receiver,
)
from nmos.node.streaming.tests._pep_helpers import (  # noqa: E402
    make_test_privacy,
    exchange_ecdh_keys,
    make_bidirectional_contexts,
)

_PORT = 19900


def _next_port() -> int:
    global _PORT
    _PORT += 2
    return _PORT


# ---------------------------------------------------------------------------
# Bidirectional E2E tests over real TCP sockets
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("protocol,mode", [
    (PepProtocol.RTSP, PepMode.AES_128_CTR),
    (PepProtocol.RTSP, PepMode.AES_128_CTR_CMAC_64_AAD),
    (PepProtocol.USB, PepMode.AES_128_CTR_CMAC_64_AAD),
])
async def test_bidirectional_psk_e2e(protocol: PepProtocol, mode: PepMode) -> None:
    """PSK-only bidirectional: both directions stream, both decrypt cleanly."""
    sender_priv, sender_keys = make_test_privacy(protocol, mode)
    receiver_priv, receiver_keys = make_test_privacy(protocol, mode)
    sid, rid = str(uuid.uuid4()), str(uuid.uuid4())

    ctx = make_bidirectional_contexts(
        sender_priv, sender_keys, receiver_priv, receiver_keys, sid, rid,
        forward_kv=b"\x00\x00\x00\x01",
        reverse_kv=b"\x00\x00\x00\x02",
    )

    # Verify same-direction keys agree, cross-direction differ
    assert ctx["sender_tx"].privacy_key == ctx["receiver_rx"].privacy_key
    assert ctx["sender_rx"].privacy_key == ctx["receiver_tx"].privacy_key
    assert ctx["sender_tx"].privacy_key != ctx["sender_rx"].privacy_key
    assert ctx["sender_tx"].iv_prime + 1 == ctx["sender_rx"].iv_prime

    port = _next_port()
    event_queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=200)
    sender_stop = asyncio.Event()
    receiver_stop = asyncio.Event()

    sender_task = asyncio.create_task(tcp_bidirectional_sender(
        listen_ip="127.0.0.1", listen_port=port,
        sender_id=sid, interface_name="lo",
        event_queue=event_queue,
        tx_encrypt_fn=ctx["sender_tx"].make_encrypt_fn(),
        rx_decrypt_fn=ctx["sender_rx"].make_decrypt_fn(),
        stop_event=sender_stop,
    ))
    await asyncio.sleep(0.5)

    receiver_task = asyncio.create_task(tcp_bidirectional_receiver(
        dest_ip="127.0.0.1", dest_port=port,
        receiver_id=rid, interface_name="lo",
        event_queue=event_queue,
        rx_decrypt_fn=ctx["receiver_rx"].make_decrypt_fn(),
        tx_encrypt_fn=ctx["receiver_tx"].make_encrypt_fn(),
        stop_event=receiver_stop,
    ))

    await asyncio.sleep(3.0)
    sender_stop.set()
    receiver_stop.set()

    sender_result = await sender_task
    receiver_result = await receiver_task

    # Both directions must have sent and received packets
    assert sender_result["tx_count"] > 0, "sender sent no forward packets"
    assert receiver_result["rx_count"] > 0, "receiver received no forward packets"
    assert receiver_result["tx_count"] > 0, "receiver sent no reverse packets"
    assert sender_result["rx_count"] > 0, "sender received no reverse packets"

    # Zero decryption errors on both sides
    assert sender_result["rx_errors"] == 0, f"sender had {sender_result['rx_errors']} decrypt errors on reverse channel"
    assert receiver_result["rx_errors"] == 0, f"receiver had {receiver_result['rx_errors']} decrypt errors on forward channel"


@pytest.mark.asyncio
@pytest.mark.parametrize("curve", ["secp256r1", "25519"])
async def test_bidirectional_ecdh_e2e(curve: str) -> None:
    """ECDH bidirectional: both sides derive PFS, both directions decrypt cleanly."""
    from nmos.enums import EnumRegistry

    mode = PepMode.ECDH_AES_128_CTR
    protocol = PepProtocol.RTSP
    curve_enum = EnumRegistry.get(curve)

    sender_priv, sender_keys = make_test_privacy(protocol, mode, curve=curve_enum)
    receiver_priv, receiver_keys = make_test_privacy(protocol, mode, curve=curve_enum)
    exchange_ecdh_keys(sender_priv, receiver_priv, curve_enum)

    sid, rid = str(uuid.uuid4()), str(uuid.uuid4())

    ctx = make_bidirectional_contexts(
        sender_priv, sender_keys, receiver_priv, receiver_keys, sid, rid,
        forward_kv=b"\x00\x00\x00\x0A",
        reverse_kv=b"\x00\x00\x00\x0B",
    )

    assert ctx["sender_tx"].privacy_key == ctx["receiver_rx"].privacy_key
    assert ctx["sender_rx"].privacy_key == ctx["receiver_tx"].privacy_key
    assert ctx["sender_tx"].privacy_key != ctx["sender_rx"].privacy_key

    port = _next_port()
    event_queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=200)
    sender_stop = asyncio.Event()
    receiver_stop = asyncio.Event()

    sender_task = asyncio.create_task(tcp_bidirectional_sender(
        listen_ip="127.0.0.1", listen_port=port,
        sender_id=sid, interface_name="lo",
        event_queue=event_queue,
        tx_encrypt_fn=ctx["sender_tx"].make_encrypt_fn(),
        rx_decrypt_fn=ctx["sender_rx"].make_decrypt_fn(),
        stop_event=sender_stop,
    ))
    await asyncio.sleep(0.5)

    receiver_task = asyncio.create_task(tcp_bidirectional_receiver(
        dest_ip="127.0.0.1", dest_port=port,
        receiver_id=rid, interface_name="lo",
        event_queue=event_queue,
        rx_decrypt_fn=ctx["receiver_rx"].make_decrypt_fn(),
        tx_encrypt_fn=ctx["receiver_tx"].make_encrypt_fn(),
        stop_event=receiver_stop,
    ))

    await asyncio.sleep(3.0)
    sender_stop.set()
    receiver_stop.set()

    sender_result = await sender_task
    receiver_result = await receiver_task

    assert receiver_result["rx_count"] > 0
    assert sender_result["rx_count"] > 0
    assert sender_result["rx_errors"] == 0
    assert receiver_result["rx_errors"] == 0


@pytest.mark.asyncio
async def test_bidirectional_cross_direction_rejected() -> None:
    """A packet encrypted for the forward direction (substreamid=0) CANNOT
    be decrypted by the reverse context (substreamid=1) — directional isolation.

    This is a pure crypto test (no sockets) but validates the bidirectional
    invariant that both substreamid AND key_version prevent cross-talk.
    """
    from nmos.node.streaming.packet import StreamPacket, PACKET_SIZE, DEFAULT_PERIOD_NS

    sender_priv, sender_keys = make_test_privacy(PepProtocol.RTSP, PepMode.AES_128_CTR)
    receiver_priv, receiver_keys = make_test_privacy(PepProtocol.RTSP, PepMode.AES_128_CTR)

    ctx = make_bidirectional_contexts(
        sender_priv, sender_keys, receiver_priv, receiver_keys, "s", "r",
        forward_kv=b"\x00\x00\x00\x01",
        reverse_kv=b"\x00\x00\x00\x02",
    )

    pkt = StreamPacket(
        sender_id=uuid.UUID(int=0), sequence=0,
        timestamp_ns=0, period_ns=DEFAULT_PERIOD_NS,
    ).to_bytes()

    # Encrypt with forward context
    wire = ctx["sender_tx"].make_encrypt_fn()(pkt, ctr=0)

    # Try to decrypt with REVERSE context — should produce garbage magic
    reverse_decrypt = ctx["sender_rx"].make_decrypt_fn()
    decrypted, _ = reverse_decrypt(wire)
    import struct
    magic = struct.unpack("<I", decrypted[:4])[0]
    assert magic != 0x49504D58, (
        "Cross-direction decrypt should NOT produce valid magic — "
        "directional isolation is broken"
    )
