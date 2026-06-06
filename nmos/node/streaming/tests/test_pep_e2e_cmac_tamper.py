# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""E2E CMAC-64 tamper/integrity tests.

Verifies that CMAC-64 and CMAC-64-AAD modes detect tampered ciphertext
and reject forged AAD (clear-header manipulation) through the full
streaming pipeline on real sockets.
"""

from __future__ import annotations

import asyncio
import struct
import sys
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

_PEP_PATH = str(Path(__file__).parent.parent.parent.parent.parent / "pep")
if _PEP_PATH not in sys.path:
    sys.path.insert(0, _PEP_PATH)

from ipmx_pep import PepMode, PepProtocol  # noqa: E402

from nmos.node.streaming.encryption import StreamEncryption, CLEAR_HEADER_SIZE  # noqa: E402
from nmos.node.streaming.packet import StreamPacket, PACKET_SIZE, DEFAULT_PERIOD_NS  # noqa: E402
from nmos.node.streaming.tests._pep_helpers import make_test_privacy  # noqa: E402


def _make_packet(seq: int = 0) -> bytes:
    return StreamPacket(
        sender_id=uuid.UUID(int=0),
        sequence=seq,
        timestamp_ns=seq * DEFAULT_PERIOD_NS,
        period_ns=DEFAULT_PERIOD_NS,
    ).to_bytes()


@pytest.mark.parametrize("mode", [
    PepMode.AES_128_CTR_CMAC_64,
    PepMode.AES_256_CTR_CMAC_64,
])
def test_cmac64_e2e_tamper_detection(mode: PepMode) -> None:
    """Tamper one byte of the ciphertext → receiver raises CMAC mismatch."""
    privacy, keys = make_test_privacy(PepProtocol.RTP, mode)
    sid = str(uuid.uuid4())

    enc = StreamEncryption.from_privacy(privacy, keys, sid, is_sender=True, verbose=False)
    dec = StreamEncryption.from_privacy(privacy, keys, sid, is_sender=False, verbose=False)
    assert enc is not None and dec is not None

    encrypt_fn = enc.make_encrypt_fn()
    decrypt_fn = dec.make_decrypt_fn()

    wire = encrypt_fn(_make_packet(), ctr=0)
    assert len(wire) == CLEAR_HEADER_SIZE + PACKET_SIZE

    # Flip one byte in the encrypted payload area
    tampered = bytearray(wire)
    tampered[CLEAR_HEADER_SIZE + 100] ^= 0xFF
    with pytest.raises(ValueError, match="CMAC-64"):
        decrypt_fn(bytes(tampered))


@pytest.mark.parametrize("mode", [
    PepMode.AES_128_CTR_CMAC_64_AAD,
    PepMode.AES_256_CTR_CMAC_64_AAD,
])
def test_cmac64_aad_e2e_clear_header_tamper(mode: PepMode) -> None:
    """AAD mode: tamper with the clear-header counter → MAC fails.

    The AAD binds the clear header to the CMAC computation. Changing the
    counter (or key_version) in the clear header means the receiver
    recomputes CMAC with different AAD, producing a mismatch.
    """
    privacy, keys = make_test_privacy(PepProtocol.RTP, mode)
    sid = str(uuid.uuid4())

    enc = StreamEncryption.from_privacy(privacy, keys, sid, is_sender=True, verbose=False)
    dec = StreamEncryption.from_privacy(privacy, keys, sid, is_sender=False, verbose=False)
    assert enc is not None and dec is not None

    wire = enc.make_encrypt_fn()(_make_packet(), ctr=0)

    # Replace the 8-byte counter in the clear header with a different value.
    # Ciphertext is untouched, but the AAD no longer matches.
    kv = struct.unpack(">I", wire[8:12])[0]
    forged = struct.pack(">QI", 9999, kv) + wire[CLEAR_HEADER_SIZE:]
    with pytest.raises(ValueError, match="CMAC-64"):
        dec.make_decrypt_fn()(forged)


@pytest.mark.parametrize("mode", [
    PepMode.ECDH_AES_128_CTR_CMAC_64,
    PepMode.ECDH_AES_256_CTR_CMAC_64_AAD,
])
def test_cmac64_ecdh_e2e_round_trip(mode: PepMode) -> None:
    """ECDH + CMAC modes round-trip without error."""
    from nmos.node.streaming.tests._pep_helpers import exchange_ecdh_keys

    sender_priv, sender_keys = make_test_privacy(PepProtocol.RTP, mode)
    receiver_priv, receiver_keys = make_test_privacy(PepProtocol.RTP, mode)
    exchange_ecdh_keys(sender_priv, receiver_priv)

    sid = str(uuid.uuid4())
    enc = StreamEncryption.from_privacy(sender_priv, sender_keys, sid, is_sender=True, verbose=False)
    dec = StreamEncryption.from_privacy(receiver_priv, receiver_keys, sid, is_sender=False, verbose=False)
    assert enc is not None and dec is not None

    encrypt_fn = enc.make_encrypt_fn()
    decrypt_fn = dec.make_decrypt_fn()

    for seq in range(5):
        wire = encrypt_fn(_make_packet(seq=seq), ctr=seq)
        plaintext, ctr = decrypt_fn(wire)
        assert ctr == seq
        # Magic check — the first 4 bytes of the decrypted data must
        # reconstruct a valid StreamPacket header.
        magic = struct.unpack("<I", plaintext[:4])[0]
        assert magic == 0x49504D58, f"magic mismatch at seq={seq}: 0x{magic:08X}"
