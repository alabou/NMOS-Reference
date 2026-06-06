# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for PEP encryption wrapper."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from nmos.node.streaming.packet import StreamPacket, PACKET_SIZE, DEFAULT_PERIOD_NS

# Check if PEP module is available
try:
    from nmos.node.streaming.encryption import StreamEncryption
    from nmos.node.types import Privacy, PrivacyPreSharedKeys, PreSharedKey
    HAS_PEP = True
except ImportError:
    HAS_PEP = False


@pytest.mark.skipif(not HAS_PEP, reason="PEP module not available")
class TestStreamEncryption:
    """Test PEP encryption/decryption round-trip."""

    def _make_privacy(self) -> tuple:
        """Build Privacy + PrivacyPreSharedKeys with known test values."""
        privacy = Privacy()
        privacy.iv = bytes(range(8))               # 0x0001020304050607
        privacy.key_generator = bytes(range(16))    # 0x000102...0f
        privacy.key_version = b"\x00\x00\x00\x01"  # version 1
        privacy.key_id = bytes(range(8))

        psk = bytes(range(16))  # 128-bit PSK
        privacy.psk = psk

        keys = PrivacyPreSharedKeys(keys=[
            PreSharedKey(key_id=bytes(range(8)), psk=psk),
        ])
        return privacy, keys

    def test_encrypt_decrypt_round_trip(self) -> None:
        """Encrypt a packet, decrypt it — content matches."""
        privacy, keys = self._make_privacy()
        sid = str(uuid.uuid4())

        # Build encryption contexts for sender and receiver
        sender_enc = StreamEncryption.from_privacy(
            privacy, keys, sid, is_sender=True, verbose=True,
        )
        receiver_enc = StreamEncryption.from_privacy(
            privacy, keys, sid, is_sender=False, verbose=True,
        )

        assert sender_enc is not None
        assert receiver_enc is not None

        # Verify same key derived
        assert sender_enc.privacy_key == receiver_enc.privacy_key, (
            f"Key mismatch: sender={sender_enc.privacy_key.hex()} "
            f"receiver={receiver_enc.privacy_key.hex()}"
        )

        # Build a test packet
        pkt = StreamPacket(
            sender_id=uuid.UUID(sid),
            sequence=42,
            timestamp_ns=1_000_000_000,
            period_ns=DEFAULT_PERIOD_NS,
            pep_ctr=0,
            pep_key_version=1,
        )
        plaintext = pkt.to_bytes()

        # Encrypt with sender
        encrypt_fn = sender_enc.make_encrypt_fn()
        ciphertext = encrypt_fn(plaintext, ctr=0)

        # 12-byte clear header (pep_ctr + key_version) + 1432 encrypted payload = 1444
        assert len(ciphertext) == PACKET_SIZE + 12
        assert ciphertext[12:] != plaintext  # Encrypted portion should differ

        # Decrypt with receiver
        decrypt_fn = receiver_enc.make_decrypt_fn()
        decrypted, ctr = decrypt_fn(ciphertext)

        assert decrypted == plaintext, "Decrypted content doesn't match original"
        assert ctr == 0

        # Parse the decrypted packet
        parsed = StreamPacket.from_bytes(decrypted)
        assert parsed.sequence == 42
        assert parsed.sender_id == uuid.UUID(sid)

    def test_sequential_packets(self) -> None:
        """Multiple packets with incrementing ctr decrypt correctly."""
        privacy, keys = self._make_privacy()
        sid = str(uuid.uuid4())

        enc = StreamEncryption.from_privacy(privacy, keys, sid, is_sender=True, verbose=False)
        assert enc is not None

        encrypt_fn = enc.make_encrypt_fn()
        decrypt_fn = enc.make_decrypt_fn()

        for seq in range(5):
            pkt = StreamPacket(
                sender_id=uuid.UUID(sid),
                sequence=seq,
                timestamp_ns=seq * DEFAULT_PERIOD_NS,
                period_ns=DEFAULT_PERIOD_NS,
                pep_ctr=seq,
            )
            plaintext = pkt.to_bytes()
            ciphertext = encrypt_fn(plaintext, ctr=seq)
            decrypted, ctr_out = decrypt_fn(ciphertext)

            assert decrypted == plaintext, f"Mismatch at seq={seq}"
            assert ctr_out == seq

    def test_key_derivation_output(self, capsys: pytest.CaptureFixture) -> None:
        """Console output shows all derivation parameters and derived key."""
        privacy, keys = self._make_privacy()
        sid = "12345678-1234-5678-1234-567812345678"

        enc = StreamEncryption.from_privacy(privacy, keys, sid, is_sender=True, verbose=True)
        assert enc is not None

        captured = capsys.readouterr()
        assert "PSK:" in captured.out
        assert "KeyGenerator:" in captured.out
        assert "KeyVersion:" in captured.out
        assert "KeyId:" in captured.out
        assert "IV:" in captured.out
        assert "IV':" in captured.out
        assert "Derived Key:" in captured.out
        assert "Sender" in captured.out

    def test_late_join_receiver(self) -> None:
        """Receiver joining late can still decrypt — ctr is in the clear prefix."""
        privacy, keys = self._make_privacy()
        sid = str(uuid.uuid4())

        enc = StreamEncryption.from_privacy(privacy, keys, sid, is_sender=True, verbose=False)
        assert enc is not None

        encrypt_fn = enc.make_encrypt_fn()
        decrypt_fn = enc.make_decrypt_fn()

        # Sender sends packets 0-9, but receiver only gets packet 5
        all_encrypted = []
        for seq in range(10):
            pkt = StreamPacket(
                sender_id=uuid.UUID(sid),
                sequence=seq,
                timestamp_ns=seq * DEFAULT_PERIOD_NS,
                period_ns=DEFAULT_PERIOD_NS,
                pep_ctr=seq,
            )
            plaintext = pkt.to_bytes()
            ciphertext = encrypt_fn(plaintext, ctr=seq)
            all_encrypted.append((plaintext, ciphertext))

        # Receiver joins late — first packet it sees is #5
        for seq in (5, 6, 7, 8, 9):
            plaintext_expected, ciphertext = all_encrypted[seq]
            decrypted, ctr_out = decrypt_fn(ciphertext)
            assert decrypted == plaintext_expected, f"Late-join decrypt failed at seq={seq}"
            assert ctr_out == seq

    def test_no_psk_returns_none(self) -> None:
        """Without PSK, from_privacy returns None."""
        privacy = Privacy()
        keys = PrivacyPreSharedKeys()

        enc = StreamEncryption.from_privacy(
            privacy, keys, "test-id", is_sender=True, verbose=False,
        )
        assert enc is None


# ---------------------------------------------------------------------------
# GMAC-128 pass-through (SRT library applies the cipher at deploy time)
# ---------------------------------------------------------------------------

# The SRT GMAC-128 modes (NMOS With Privacy Encryption.md:166) are cipher-
# delegated to the real SRT library per spec line 294. In the simplified
# streaming transport used by these tests there is no libsrt, so the pep
# layer passes the packet through the clear-header framing WITHOUT applying
# any cipher — the same role UDP plays in the model (no pep-layer MAC).
# The tests below verify that:
#   (1) make_encrypt_fn produces clear_header + plaintext (no cipher);
#   (2) make_decrypt_fn is the exact inverse — roundtrip recovers the
#       plaintext + ctr for every configured GMAC mode.
#
# Closes CR-003 from the audit at the streaming-wire layer.

@pytest.mark.skipif(not HAS_PEP, reason="PEP module not available")
class TestStreamEncryptionGmacPassThrough:
    """GMAC-128 SRT modes: pep layer passes the payload through without
    applying a cipher; the real SRT library would encrypt + authenticate
    at its own layer using ``StreamEncryption.privacy_key`` as the SRT
    passphrase."""

    _GMAC_MODE_NAMES: tuple[str, ...] = (
        "AES-128-GMAC-128",
        "AES-256-GMAC-128",
        "ECDH_AES-128-GMAC-128",
        "ECDH_AES-256-GMAC-128",
    )

    def _make_privacy_gmac(self, mode_name: str) -> tuple:
        """Build a Privacy / PrivacyPreSharedKeys pair whose mode points
        to a GMAC-128 enum value. Key size follows the mode's AES prefix
        — AES-128 uses a 16-byte PSK, AES-256 uses 32 bytes."""
        from ipmx_pep import PepMode
        from nmos import enums

        mode_enum = PepMode(mode_name)
        # Pick the matching nmos.enums GMAC constant so from_privacy's
        # _to_pep_mode bridge resolves it back to our PepMode.
        nmos_mode_map = {
            "AES-128-GMAC-128":      enums.AES128_GCM128,
            "AES-256-GMAC-128":      enums.AES256_GCM128,
            "ECDH_AES-128-GMAC-128": enums.ECDH_AES128_GCM128,
            "ECDH_AES-256-GMAC-128": enums.ECDH_AES256_GCM128,
        }

        privacy = Privacy()
        privacy.iv = bytes(range(8))
        privacy.key_generator = bytes(range(16))
        privacy.key_version = b"\x00\x00\x00\x01"
        privacy.key_id = bytes(range(8))
        privacy.protocol = enums.SRT
        privacy.mode = nmos_mode_map[mode_name]

        # AES key size drives PSK length.
        psk_bytes = 32 if "256" in mode_name.split("-GMAC")[0] else 16
        psk = bytes(range(psk_bytes))
        privacy.psk = psk
        keys = PrivacyPreSharedKeys(keys=[
            PreSharedKey(key_id=bytes(range(8)), psk=psk),
        ])
        return privacy, keys, mode_enum

    @pytest.mark.parametrize("mode_name", _GMAC_MODE_NAMES)
    def test_make_encrypt_fn_passes_through_plaintext_for_gmac_modes(
        self, mode_name: str
    ) -> None:
        from nmos.node.streaming.encryption import CLEAR_HEADER_SIZE
        privacy, keys, mode_enum = self._make_privacy_gmac(mode_name)
        sid = str(uuid.uuid4())

        enc = StreamEncryption.from_privacy(
            privacy, keys, sid, is_sender=True, verbose=False,
        )
        assert enc is not None
        assert enc.mode is mode_enum
        # Key size derived correctly via the mode_key_bits bugfix.
        expected_key_len = 32 if "256" in mode_name.split("-GMAC")[0] else 16
        assert len(enc.privacy_key) == expected_key_len

        encrypt_fn = enc.make_encrypt_fn()
        plaintext = b"GMAC pass-through payload for SRT library to cipher."
        ctr = 42
        wire = encrypt_fn(plaintext, ctr)

        # Output is exactly: 12-byte clear header + untouched plaintext.
        assert len(wire) == CLEAR_HEADER_SIZE + len(plaintext)
        assert wire[CLEAR_HEADER_SIZE:] == plaintext, (
            "GMAC-128 pep layer must pass payload through; the real SRT "
            "library applies the cipher per NMOS With Privacy Encryption.md:294."
        )

    @pytest.mark.parametrize("mode_name", _GMAC_MODE_NAMES)
    def test_make_decrypt_fn_roundtrips_gmac_pass_through(
        self, mode_name: str
    ) -> None:
        privacy, keys, _ = self._make_privacy_gmac(mode_name)
        sid = str(uuid.uuid4())

        sender_enc = StreamEncryption.from_privacy(
            privacy, keys, sid, is_sender=True, verbose=False,
        )
        receiver_enc = StreamEncryption.from_privacy(
            privacy, keys, sid, is_sender=False, verbose=False,
        )
        assert sender_enc is not None and receiver_enc is not None
        assert sender_enc.privacy_key == receiver_enc.privacy_key, (
            "Sender and receiver must derive the same SRT passphrase"
        )

        encrypt_fn = sender_enc.make_encrypt_fn()
        decrypt_fn = receiver_enc.make_decrypt_fn()

        plaintext = b"roundtrip across pass-through SRT GMAC mode"
        ctr = 7
        wire = encrypt_fn(plaintext, ctr)
        recovered, ctr_out = decrypt_fn(wire)
        assert recovered == plaintext
        assert ctr_out == ctr


@pytest.mark.skipif(not HAS_PEP, reason="PEP module not available")
class TestEcdhKeyDerivation:
    """Test ECDH shared secret computation and key derivation."""

    def test_ecdh_shared_secret_x25519(self) -> None:
        """Sender and receiver with X25519 keys derive the same shared secret."""
        from nmos.node.privacy import (
            generate_ecdh_sender_key, generate_ecdh_receiver_key,
            compute_ecdh_shared_secret,
        )

        # Simulate two Privacy objects (sender and receiver)
        sender_privacy = Privacy()
        receiver_privacy = Privacy()

        # Generate key pairs
        generate_ecdh_sender_key(sender_privacy, update=False)
        generate_ecdh_receiver_key(receiver_privacy, update=False)

        # Exchange public keys (sender gets receiver's public, receiver gets sender's)
        sender_privacy.ecdh_receiver_public_key = receiver_privacy.ecdh_receiver_public_key
        receiver_privacy.ecdh_sender_public_key = sender_privacy.ecdh_sender_public_key

        # Compute shared secrets
        sender_secret = compute_ecdh_shared_secret(sender_privacy, is_sender=True)
        receiver_secret = compute_ecdh_shared_secret(receiver_privacy, is_sender=False)

        assert sender_secret, "Sender shared secret should not be empty"
        assert receiver_secret, "Receiver shared secret should not be empty"
        assert sender_secret == receiver_secret, (
            f"Shared secrets must match:\n"
            f"  sender:   {sender_secret.hex()}\n"
            f"  receiver: {receiver_secret.hex()}"
        )

    def test_ecdh_pfs_produces_different_key(self) -> None:
        """Key derived with ECDH PFS differs from PSK-only key."""
        from ipmx_pep import derive_privacy_key
        from nmos.node.privacy import (
            generate_ecdh_sender_key, generate_ecdh_receiver_key,
            compute_ecdh_shared_secret,
        )

        psk = bytes(range(16))
        key_gen = bytes(range(16))
        key_ver = b"\x00\x00\x00\x01"

        # PSK-only key
        key_no_pfs = derive_privacy_key(psk, key_gen, key_ver, key_pfs=b"")

        # Generate ECDH and compute shared secret
        sender_priv = Privacy()
        receiver_priv = Privacy()
        generate_ecdh_sender_key(sender_priv)
        generate_ecdh_receiver_key(receiver_priv)
        sender_priv.ecdh_receiver_public_key = receiver_priv.ecdh_receiver_public_key
        pfs = compute_ecdh_shared_secret(sender_priv, is_sender=True)
        assert pfs, "PFS should be computed"

        # Key with PFS
        key_with_pfs = derive_privacy_key(psk, key_gen, key_ver, key_pfs=pfs)

        assert key_no_pfs != key_with_pfs, (
            "Key with ECDH PFS should differ from PSK-only key"
        )

    def test_encryption_with_ecdh_pfs(self) -> None:
        """Full encrypt/decrypt round-trip using ECDH-derived key."""
        from nmos.node.privacy import (
            generate_ecdh_sender_key, generate_ecdh_receiver_key,
            compute_ecdh_shared_secret,
        )

        # Build matching sender/receiver privacy with ECDH
        sender_privacy = Privacy()
        sender_privacy.iv = bytes(range(8))
        sender_privacy.key_generator = bytes(range(16))
        sender_privacy.key_version = b"\x00\x00\x00\x01"
        sender_privacy.key_id = bytes(range(8))
        sender_privacy.psk = bytes(range(16))

        receiver_privacy = Privacy()
        receiver_privacy.iv = sender_privacy.iv
        receiver_privacy.key_generator = sender_privacy.key_generator
        receiver_privacy.key_version = sender_privacy.key_version
        receiver_privacy.key_id = sender_privacy.key_id
        receiver_privacy.psk = sender_privacy.psk

        # Generate ECDH keys and exchange public keys
        generate_ecdh_sender_key(sender_privacy)
        generate_ecdh_receiver_key(receiver_privacy)
        sender_privacy.ecdh_receiver_public_key = receiver_privacy.ecdh_receiver_public_key
        receiver_privacy.ecdh_sender_public_key = sender_privacy.ecdh_sender_public_key

        # Compute PFS on both sides
        sender_privacy.pfs = compute_ecdh_shared_secret(sender_privacy, is_sender=True)
        receiver_privacy.pfs = compute_ecdh_shared_secret(receiver_privacy, is_sender=False)
        assert sender_privacy.pfs == receiver_privacy.pfs

        # Build encryption contexts
        keys = PrivacyPreSharedKeys(keys=[PreSharedKey(key_id=bytes(range(8)), psk=bytes(range(16)))])
        sid = str(uuid.uuid4())

        sender_enc = StreamEncryption.from_privacy(
            sender_privacy, keys, sid, is_sender=True, verbose=True,
        )
        receiver_enc = StreamEncryption.from_privacy(
            receiver_privacy, keys, sid, is_sender=False, verbose=True,
        )

        assert sender_enc is not None
        assert receiver_enc is not None
        assert sender_enc.privacy_key == receiver_enc.privacy_key
        assert sender_enc.key_pfs == receiver_enc.key_pfs

        # Encrypt and decrypt
        from nmos.node.streaming.packet import StreamPacket, PACKET_SIZE, DEFAULT_PERIOD_NS
        pkt = StreamPacket(
            sender_id=uuid.UUID(sid), sequence=0,
            timestamp_ns=0, period_ns=DEFAULT_PERIOD_NS,
        )
        plaintext = pkt.to_bytes()
        ciphertext = sender_enc.make_encrypt_fn()(plaintext, ctr=0)
        decrypted, _ = receiver_enc.make_decrypt_fn()(ciphertext)
        assert decrypted == plaintext


@pytest.mark.skipif(not HAS_PEP, reason="PEP module not available")
class TestEncryptedUdpLoopback:
    """End-to-end: UDP sender with PEP → receiver with PEP on localhost."""

    @pytest.mark.asyncio
    async def test_encrypted_udp_loopback(self) -> None:
        """Encrypted packets sent via UDP, decrypted by receiver."""
        from nmos.node.streaming.transport_udp import udp_sender, udp_receiver
        from nmos.node.events import EngineEvent, EventId

        loop = asyncio.get_event_loop()
        event_queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=100)
        sender_stop = asyncio.Event()
        receiver_stop = asyncio.Event()
        sid = str(uuid.uuid4())
        rid = str(uuid.uuid4())
        port = 18900

        # Build matching encryption contexts
        privacy = Privacy()
        privacy.iv = bytes(range(8))
        privacy.key_generator = bytes(range(16))
        privacy.key_version = b"\x00\x00\x00\x01"
        privacy.key_id = bytes(range(8))
        privacy.psk = bytes(range(16))
        keys = PrivacyPreSharedKeys(keys=[PreSharedKey(key_id=bytes(range(8)), psk=bytes(range(16)))])

        sender_enc = StreamEncryption.from_privacy(privacy, keys, sid, is_sender=True)
        receiver_enc = StreamEncryption.from_privacy(privacy, keys, rid, is_sender=False)

        assert sender_enc is not None
        assert receiver_enc is not None
        assert sender_enc.privacy_key == receiver_enc.privacy_key

        encrypt_fn = sender_enc.make_encrypt_fn()
        decrypt_fn = receiver_enc.make_decrypt_fn()

        async def run_sender() -> None:
            await udp_sender(
                loop=loop,
                source_ip="127.0.0.1", source_port=0,
                dest_ip="127.0.0.1", dest_port=port,
                sender_id=sid, interface_name="lo",
                event_queue=event_queue,
                encrypt_fn=encrypt_fn,
                stop_event=sender_stop,
            )

        async def run_receiver() -> None:
            await udp_receiver(
                loop=loop,
                interface_ip="127.0.0.1", multicast_ip="",
                source_ip="", dest_port=port,
                receiver_id=rid, interface_name="lo",
                event_queue=event_queue,
                decrypt_fn=decrypt_fn,
                stop_event=receiver_stop,
            )

        receiver_task = asyncio.create_task(run_receiver())
        await asyncio.sleep(0.2)
        sender_task = asyncio.create_task(run_sender())

        await asyncio.sleep(3.5)
        sender_stop.set()
        receiver_stop.set()
        await asyncio.gather(sender_task, receiver_task, return_exceptions=True)

        events: list[EngineEvent] = []
        while not event_queue.empty():
            events.append(event_queue.get_nowait())

        # Find sender deactivation — errors after are expected
        deactivate_idx = None
        for i, e in enumerate(events):
            if e.event == EventId.VENDOR_TRANSPORT_DEACTIVATE and e.id == sid:
                deactivate_idx = i
                break

        pre = events[:deactivate_idx] if deactivate_idx else events

        # No packet loss, late, or decryption errors while both are active
        error_events = [
            e for e in pre
            if e.event in (
                EventId.TRANSPORT_PACKET_LOST,
                EventId.TRANSPORT_PACKET_LATE,
                EventId.ESSENCE_STREAM_ERROR,
            )
        ]
        assert not error_events, (
            f"Errors during encrypted streaming: {[(e.event, e.info) for e in error_events]}"
        )
