# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tier A/B regression anchors for the PEP encryption primitive.

These tests cover the new StreamEncryption features (parametrized
substreamid + key_version, wire-driven KV derivation, sender-side _KV
rotation, CMAC-64 mac-then-encrypt/verify, and the [PEP-KDF] log line)
without paying the cost of the full 310-test E2E matrix. They run in
seconds and catch fundamental breakage before the slow Tier C suite runs.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

import pytest

# Ensure pep/ is importable
import sys
from pathlib import Path
_PEP_PATH = str(Path(__file__).parent.parent.parent.parent.parent / "pep")
if _PEP_PATH not in sys.path:
    sys.path.insert(0, _PEP_PATH)

from ipmx_pep import PepMode, PepProtocol  # noqa: E402

from nmos.node.streaming.encryption import (
    StreamEncryption,
    CLEAR_HEADER_SIZE,
    CMAC_TAG_SIZE,
)
from nmos.node.streaming.packet import StreamPacket, PACKET_SIZE, DEFAULT_PERIOD_NS
from nmos.node.streaming.tests._pep_helpers import (
    make_test_privacy,
    exchange_ecdh_keys,
    make_bidirectional_contexts,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_packet(seq: int = 0) -> bytes:
    """Build a deterministic 1432-byte packet with known magic."""
    pkt = StreamPacket(
        sender_id=uuid.UUID(int=seq),
        sequence=seq,
        timestamp_ns=seq * DEFAULT_PERIOD_NS,
        period_ns=DEFAULT_PERIOD_NS,
        pep_ctr=seq,
    )
    return pkt.to_bytes()


# ---------------------------------------------------------------------------
# (A) Substreamid: iv_prime offset
# ---------------------------------------------------------------------------

class TestSubstreamidIvPrime:
    """TR-10-13 §14: iv_prime(substreamid=N) = iv_prime(substreamid=0) + N."""

    def test_iv_prime_offset_0_vs_1(self) -> None:
        """Forward (0) vs reverse (1) substreamid: iv_prime differs by 1."""
        privacy, keys = make_test_privacy(PepProtocol.RTP, PepMode.AES_128_CTR)
        sid = str(uuid.uuid4())

        enc_fwd = StreamEncryption.from_privacy(
            privacy, keys, sid, is_sender=True, verbose=False, substreamid=0,
        )
        enc_rev = StreamEncryption.from_privacy(
            privacy, keys, sid, is_sender=True, verbose=False, substreamid=1,
        )
        assert enc_fwd is not None and enc_rev is not None
        assert enc_rev.iv_prime == enc_fwd.iv_prime + 1

    def test_iv_prime_offset_generic(self) -> None:
        """Arbitrary substreamid N yields iv_prime(0) + N."""
        privacy, keys = make_test_privacy(PepProtocol.RTP, PepMode.AES_128_CTR)
        sid = str(uuid.uuid4())
        base = StreamEncryption.from_privacy(
            privacy, keys, sid, is_sender=True, verbose=False, substreamid=0,
        )
        assert base is not None
        for n in (2, 5, 10, 42, 1023):
            enc = StreamEncryption.from_privacy(
                privacy, keys, sid, is_sender=True, verbose=False, substreamid=n,
            )
            assert enc is not None
            assert enc.iv_prime == (base.iv_prime + n) & 0xFFFFFFFFFFFFFFFF


# ---------------------------------------------------------------------------
# (B) Per-direction key_version changes the derived key
# ---------------------------------------------------------------------------

class TestKeyVersionDerivation:
    """TR-10-13 §12/§20.3: different key_version → different privacy_key."""

    def test_different_kv_different_key(self) -> None:
        privacy, keys = make_test_privacy(PepProtocol.RTP, PepMode.AES_128_CTR)
        sid = str(uuid.uuid4())

        enc_a = StreamEncryption.from_privacy(
            privacy, keys, sid, is_sender=True, verbose=False,
            key_version_override=b"\x00\x00\x00\x01",
        )
        enc_b = StreamEncryption.from_privacy(
            privacy, keys, sid, is_sender=True, verbose=False,
            key_version_override=b"\x00\x00\x00\x02",
        )
        assert enc_a is not None and enc_b is not None
        assert enc_a.privacy_key != enc_b.privacy_key
        assert enc_a.key_version != enc_b.key_version

    def test_bidirectional_keys_differ(self) -> None:
        """Forward and reverse contexts on the same endpoint → different keys."""
        sender_priv, sender_keys = make_test_privacy(
            PepProtocol.RTSP, PepMode.AES_128_CTR,
        )
        receiver_priv, receiver_keys = make_test_privacy(
            PepProtocol.RTSP, PepMode.AES_128_CTR,
        )
        ctx = make_bidirectional_contexts(
            sender_priv, sender_keys, receiver_priv, receiver_keys,
            "s", "r",
            forward_kv=b"\x00\x00\x00\x01",
            reverse_kv=b"\x00\x00\x00\x02",
        )

        # Same-direction pairs must agree (sender_tx ↔ receiver_rx).
        assert ctx["sender_tx"].privacy_key == ctx["receiver_rx"].privacy_key
        assert ctx["sender_rx"].privacy_key == ctx["receiver_tx"].privacy_key

        # Cross-direction pairs must differ.
        assert ctx["sender_tx"].privacy_key != ctx["sender_rx"].privacy_key
        assert ctx["sender_tx"].iv_prime + 1 == ctx["sender_rx"].iv_prime


# ---------------------------------------------------------------------------
# (C) ECDH PFS changes the derived key
# ---------------------------------------------------------------------------

class TestEcdhPfs:
    """ECDH PFS input makes the derived key different from PSK-only."""

    def test_ecdh_vs_psk_only(self) -> None:
        # PSK-only context
        privacy_psk, keys_psk = make_test_privacy(
            PepProtocol.RTP, PepMode.AES_128_CTR,
        )
        enc_psk = StreamEncryption.from_privacy(
            privacy_psk, keys_psk, "s", is_sender=True, verbose=False,
        )

        # ECDH context (same PSK/KG/KV, but with PFS)
        privacy_ecdh, keys_ecdh = make_test_privacy(
            PepProtocol.RTP, PepMode.ECDH_AES_128_CTR,
        )
        privacy_receiver = privacy_ecdh.__class__(
            iv=privacy_ecdh.iv,
            key_generator=privacy_ecdh.key_generator,
            key_version=privacy_ecdh.key_version,
            key_id=privacy_ecdh.key_id,
            psk=privacy_ecdh.psk,
        )
        exchange_ecdh_keys(privacy_ecdh, privacy_receiver)
        enc_ecdh = StreamEncryption.from_privacy(
            privacy_ecdh, keys_ecdh, "s", is_sender=True, verbose=False,
        )

        assert enc_psk is not None and enc_ecdh is not None
        assert len(enc_ecdh.key_pfs) > 0
        assert enc_psk.privacy_key != enc_ecdh.privacy_key


# ---------------------------------------------------------------------------
# (D) CMAC-64 verification
# ---------------------------------------------------------------------------

class TestCmac64:
    """Mac-then-encrypt round-trip + tamper detection."""

    def test_cmac64_round_trip_accepts_valid(self) -> None:
        """Untampered CMAC-64 packet round-trips cleanly."""
        privacy, keys = make_test_privacy(
            PepProtocol.RTP, PepMode.AES_128_CTR_CMAC_64,
        )
        sid = str(uuid.uuid4())
        enc = StreamEncryption.from_privacy(
            privacy, keys, sid, is_sender=True, verbose=False,
        )
        dec = StreamEncryption.from_privacy(
            privacy, keys, sid, is_sender=False, verbose=False,
        )
        assert enc is not None and dec is not None

        encrypt = enc.make_encrypt_fn()
        decrypt = dec.make_decrypt_fn()

        plaintext = _make_packet(seq=0)
        wire = encrypt(plaintext, ctr=0)
        assert len(wire) == CLEAR_HEADER_SIZE + PACKET_SIZE

        decrypted, ctr = decrypt(wire)
        assert ctr == 0
        # CMAC modes zero-pad the tag slot on decrypt, so the first
        # (PACKET_SIZE - CMAC_TAG_SIZE) bytes match the plaintext.
        assert decrypted[: PACKET_SIZE - CMAC_TAG_SIZE] == plaintext[: PACKET_SIZE - CMAC_TAG_SIZE]

    def test_cmac64_round_trip_rejects_tampered(self) -> None:
        """Flipping one byte of ciphertext triggers a tag mismatch error."""
        privacy, keys = make_test_privacy(
            PepProtocol.RTP, PepMode.AES_128_CTR_CMAC_64,
        )
        enc = StreamEncryption.from_privacy(privacy, keys, "s", is_sender=True, verbose=False)
        dec = StreamEncryption.from_privacy(privacy, keys, "s", is_sender=False, verbose=False)
        assert enc is not None and dec is not None

        wire = enc.make_encrypt_fn()(_make_packet(), ctr=0)

        # Flip a byte inside the ciphertext (after the 12-byte clear header).
        mid = CLEAR_HEADER_SIZE + 200
        tampered = bytearray(wire)
        tampered[mid] ^= 0x01

        with pytest.raises(ValueError, match="CMAC-64"):
            dec.make_decrypt_fn()(bytes(tampered))

    def test_cmac64_aad_binds_clear_header(self) -> None:
        """AAD mode: changing the clear-header ctr invalidates the MAC."""
        privacy, keys = make_test_privacy(
            PepProtocol.RTP, PepMode.AES_128_CTR_CMAC_64_AAD,
        )
        enc = StreamEncryption.from_privacy(privacy, keys, "s", is_sender=True, verbose=False)
        dec = StreamEncryption.from_privacy(privacy, keys, "s", is_sender=False, verbose=False)
        assert enc is not None and dec is not None

        wire = enc.make_encrypt_fn()(_make_packet(), ctr=0)

        # Replace the clear-header counter with a value the MAC was not
        # computed against. AAD-bound MAC must detect the mismatch.
        import struct
        mutated = struct.pack(">QI", 999, 0) + wire[CLEAR_HEADER_SIZE:]
        with pytest.raises(ValueError, match="CMAC-64"):
            dec.make_decrypt_fn()(mutated)


# ---------------------------------------------------------------------------
# (E) _KV dynamic key rotation (sender + wire-driven receiver)
# ---------------------------------------------------------------------------

class TestKvRotation:
    """Sender-side rotation every N seconds; receiver re-derives from wire."""

    def test_sender_rotates_key_version(self) -> None:
        """With a short rotation period, key_version changes across calls."""
        privacy, keys = make_test_privacy(
            PepProtocol.RTP_KV, PepMode.AES_128_CTR,
        )
        enc = StreamEncryption.from_privacy(
            privacy, keys, "s", is_sender=True, verbose=False,
            key_rotation_period_sec=0.05,  # Fast rotation for unit-test speed
        )
        assert enc is not None
        encrypt = enc.make_encrypt_fn()

        observed_kvs: set[int] = set()
        import struct
        for seq in range(30):
            wire = encrypt(_make_packet(seq=seq), ctr=seq)
            _ctr, kv = struct.unpack(">QI", wire[:CLEAR_HEADER_SIZE])
            observed_kvs.add(kv)
            time.sleep(0.01)

        # Over ~0.3s with a 0.05s period we expect at least 2 distinct KVs.
        assert len(observed_kvs) >= 2

    def test_receiver_rederives_from_wire_kv(self) -> None:
        """Receiver uses wire KV to decrypt even when it differs from its initial."""
        privacy, keys = make_test_privacy(
            PepProtocol.RTP_KV, PepMode.AES_128_CTR,
        )
        # Sender rotates rapidly.
        enc = StreamEncryption.from_privacy(
            privacy, keys, "s", is_sender=True, verbose=False,
            key_rotation_period_sec=0.05,
        )
        # Receiver initial KV equal to sender's initial.
        dec = StreamEncryption.from_privacy(
            privacy, keys, "s", is_sender=False, verbose=False,
        )
        assert enc is not None and dec is not None

        encrypt = enc.make_encrypt_fn()
        decrypt = dec.make_decrypt_fn()

        for seq in range(30):
            wire = encrypt(_make_packet(seq=seq), ctr=seq)
            plaintext, ctr = decrypt(wire)
            assert ctr == seq
            # Magic check: first 4 bytes of any StreamPacket are 0x49504D58 = "IPMX"
            assert plaintext[:4] == b"IPMX" or plaintext[:4] == bytes.fromhex("58 4d 50 49".replace(" ", ""))

    def test_non_kv_protocol_does_not_rotate(self) -> None:
        """RTP (without _KV) keeps the same key_version forever."""
        privacy, keys = make_test_privacy(
            PepProtocol.RTP, PepMode.AES_128_CTR,
        )
        enc = StreamEncryption.from_privacy(
            privacy, keys, "s", is_sender=True, verbose=False,
            key_rotation_period_sec=0.05,  # Period set, but protocol is not _KV
        )
        assert enc is not None
        encrypt = enc.make_encrypt_fn()

        import struct
        observed_kvs: set[int] = set()
        for seq in range(10):
            wire = encrypt(_make_packet(seq=seq), ctr=seq)
            _ctr, kv = struct.unpack(">QI", wire[:CLEAR_HEADER_SIZE])
            observed_kvs.add(kv)
            time.sleep(0.02)

        assert len(observed_kvs) == 1  # Only one KV observed


# ---------------------------------------------------------------------------
# (F) [PEP-KDF] log line format (schema contract)
# ---------------------------------------------------------------------------

class TestPepKdfLogLine:
    """The Tier-C harness parses this line with a stable regex; lock the format."""

    _LINE_RE = re.compile(
        r"\[PEP-KDF\] role=(?P<role>\w+) resource=(?P<resource>\S+) "
        r"dir=(?P<dir>\w+) substreamid=(?P<substreamid>\d+) "
        r"protocol=(?P<protocol>\S+) mode=(?P<mode>\S+) "
        r"key_id=(?P<key_id>[0-9a-f]+) key_version=(?P<key_version>[0-9a-f]+) "
        r"iv_prime=(?P<iv_prime>[0-9a-f]+) pfs_len=(?P<pfs_len>\d+) "
        r"derived_key=(?P<derived_key>[0-9a-f]+)"
    )

    def _get_pep_kdf_line(self, capsys: Any, resource_id: str) -> dict[str, str]:
        captured = capsys.readouterr()
        for line in captured.out.splitlines():
            if line.startswith("[PEP-KDF]") and f"resource={resource_id}" in line:
                match = self._LINE_RE.match(line)
                assert match is not None, f"Malformed [PEP-KDF] line: {line}"
                return match.groupdict()
        pytest.fail("no [PEP-KDF] line for resource")

    def test_psk_only_line(self, capsys: Any) -> None:
        privacy, keys = make_test_privacy(PepProtocol.RTP, PepMode.AES_128_CTR)
        sid = str(uuid.uuid4())
        enc = StreamEncryption.from_privacy(
            privacy, keys, sid, is_sender=True, verbose=True, substreamid=0,
        )
        assert enc is not None

        fields = self._get_pep_kdf_line(capsys, sid)
        assert fields["role"] == "sender"
        assert fields["dir"] == "tx"
        assert fields["substreamid"] == "0"
        assert fields["protocol"] == "RTP"
        assert fields["mode"] == "AES-128-CTR"
        assert int(fields["pfs_len"]) == 0
        assert fields["derived_key"] == enc.privacy_key.hex()

    def test_ecdh_line_has_pfs(self, capsys: Any) -> None:
        sender_priv, sender_keys = make_test_privacy(
            PepProtocol.RTP, PepMode.ECDH_AES_128_CTR,
        )
        receiver_priv, _ = make_test_privacy(
            PepProtocol.RTP, PepMode.ECDH_AES_128_CTR,
        )
        exchange_ecdh_keys(sender_priv, receiver_priv)
        sid = str(uuid.uuid4())
        enc = StreamEncryption.from_privacy(
            sender_priv, sender_keys, sid, is_sender=True, verbose=True,
        )
        assert enc is not None

        fields = self._get_pep_kdf_line(capsys, sid)
        assert int(fields["pfs_len"]) > 0

    def test_direction_parameter_in_line(self, capsys: Any) -> None:
        """Direction arg threads through to the log line."""
        privacy, keys = make_test_privacy(PepProtocol.RTSP, PepMode.AES_128_CTR)
        sid = str(uuid.uuid4())
        enc = StreamEncryption.from_privacy(
            privacy, keys, sid, is_sender=False, verbose=True,
            substreamid=1, direction="rx",
        )
        assert enc is not None

        fields = self._get_pep_kdf_line(capsys, sid)
        assert fields["role"] == "receiver"
        assert fields["dir"] == "rx"
        assert fields["substreamid"] == "1"


# ---------------------------------------------------------------------------
# (G) Backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompat:
    """Existing call sites that don't pass new parameters must still work."""

    def test_default_args_match_pre_change_behavior(self) -> None:
        """from_privacy() with default substreamid/key_version_override reproduces
        the pre-change (substreamid=0, key_version from privacy.key_version) result."""
        privacy, keys = make_test_privacy(PepProtocol.RTP, PepMode.AES_128_CTR)

        enc_default = StreamEncryption.from_privacy(
            privacy, keys, "s", is_sender=True, verbose=False,
        )
        enc_explicit = StreamEncryption.from_privacy(
            privacy, keys, "s", is_sender=True, verbose=False,
            substreamid=0, key_version_override=privacy.key_version,
        )
        assert enc_default is not None and enc_explicit is not None
        assert enc_default.privacy_key == enc_explicit.privacy_key
        assert enc_default.iv_prime == enc_explicit.iv_prime
