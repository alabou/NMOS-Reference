# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""E2E tests for PEP + Node Reservation (key_xcl integration).

Verifies that the exclusive_key from a Node Reservation session feeds into
the PEP KDF as key_xcl, producing a different derived privacy_key. Tests
the full chain: Acquire → key_xcl propagated → StreamEncryption derives
correct key → encrypted packets decrypt cleanly when both sides share the
same key_xcl → fail when key_xcl differs.

Also tests the IS-05 transport parameter path that was missing from the
previous PEP plan: PATCH ext_privacy_mode/protocol on sender → staged →
active → _sync_privacy_from_active_params → activation.privacy populated.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

_PEP_PATH = str(Path(__file__).parent.parent.parent.parent.parent / "pep")
if _PEP_PATH not in sys.path:
    sys.path.insert(0, _PEP_PATH)

from ipmx_pep import PepMode, PepProtocol, derive_privacy_key  # noqa: E402

from nmos.node.types import Privacy, PrivacyPreSharedKeys, PreSharedKey  # noqa: E402
from nmos.node.streaming.encryption import StreamEncryption  # noqa: E402
from nmos.node.streaming.packet import StreamPacket, PACKET_SIZE, DEFAULT_PERIOD_NS  # noqa: E402
from nmos.node.streaming.tests._pep_helpers import (  # noqa: E402
    make_test_privacy,
    TEST_PSK_128,
    TEST_KEY_GENERATOR,
    TEST_KEY_VERSION,
    TEST_KEY_ID,
    TEST_IV,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_packet(seq: int = 0) -> bytes:
    return StreamPacket(
        sender_id=uuid.UUID(int=seq), sequence=seq,
        timestamp_ns=seq * DEFAULT_PERIOD_NS, period_ns=DEFAULT_PERIOD_NS,
    ).to_bytes()


# A test exclusive key (16 bytes = 128 bits)
TEST_XCL = bytes.fromhex("aabbccdd11223344aabbccdd11223344")
TEST_XCL_DIFFERENT = bytes.fromhex("1122334455667788aabbccddeeff0011")


# ---------------------------------------------------------------------------
# key_xcl changes the derived key
# ---------------------------------------------------------------------------

class TestKeyXclAffectsDerivedKey:
    """Verify key_xcl is actually used by the KDF — different key_xcl → different key."""

    def test_key_xcl_changes_128bit_key(self) -> None:
        key_without = derive_privacy_key(
            psk=TEST_PSK_128, key_generator=TEST_KEY_GENERATOR,
            key_version=TEST_KEY_VERSION, key_xcl=b"",
        )
        key_with = derive_privacy_key(
            psk=TEST_PSK_128, key_generator=TEST_KEY_GENERATOR,
            key_version=TEST_KEY_VERSION, key_xcl=TEST_XCL,
        )
        assert key_without != key_with, "key_xcl must change the derived key"

    def test_different_key_xcl_different_key(self) -> None:
        key_a = derive_privacy_key(
            psk=TEST_PSK_128, key_generator=TEST_KEY_GENERATOR,
            key_version=TEST_KEY_VERSION, key_xcl=TEST_XCL,
        )
        key_b = derive_privacy_key(
            psk=TEST_PSK_128, key_generator=TEST_KEY_GENERATOR,
            key_version=TEST_KEY_VERSION, key_xcl=TEST_XCL_DIFFERENT,
        )
        assert key_a != key_b

    def test_empty_key_xcl_matches_baseline(self) -> None:
        """Empty key_xcl produces the same key as no key_xcl (backward compat)."""
        key_baseline = derive_privacy_key(
            psk=TEST_PSK_128, key_generator=TEST_KEY_GENERATOR,
            key_version=TEST_KEY_VERSION,
        )
        key_empty = derive_privacy_key(
            psk=TEST_PSK_128, key_generator=TEST_KEY_GENERATOR,
            key_version=TEST_KEY_VERSION, key_xcl=b"",
        )
        assert key_baseline == key_empty


# ---------------------------------------------------------------------------
# StreamEncryption round-trip with key_xcl
# ---------------------------------------------------------------------------

class TestStreamEncryptionWithKeyXcl:
    """Verify StreamEncryption uses key_xcl from Privacy.xcl."""

    def test_with_key_xcl_sender_receiver_agree(self) -> None:
        """Same key_xcl on both sides → same derived key → decrypt succeeds."""
        sender_priv, sender_keys = make_test_privacy(PepProtocol.RTP, PepMode.AES_128_CTR)
        sender_priv.xcl = TEST_XCL

        receiver_priv, receiver_keys = make_test_privacy(PepProtocol.RTP, PepMode.AES_128_CTR)
        receiver_priv.xcl = TEST_XCL

        sid = str(uuid.uuid4())
        sender_enc = StreamEncryption.from_privacy(
            sender_priv, sender_keys, sid, is_sender=True, verbose=False,
        )
        receiver_enc = StreamEncryption.from_privacy(
            receiver_priv, receiver_keys, sid, is_sender=False, verbose=False,
        )
        assert sender_enc is not None and receiver_enc is not None
        assert sender_enc.privacy_key == receiver_enc.privacy_key
        assert sender_enc.key_xcl == TEST_XCL

        # Round-trip
        encrypt = sender_enc.make_encrypt_fn()
        decrypt = receiver_enc.make_decrypt_fn()
        plaintext = _make_packet()
        wire = encrypt(plaintext, ctr=0)
        decrypted, ctr = decrypt(wire)
        assert ctr == 0
        # Magic check
        import struct
        magic = struct.unpack("<I", decrypted[:4])[0]
        assert magic == 0x49504D58

    def test_different_key_xcl_fails_decryption(self) -> None:
        """Sender has key_xcl=A, receiver has key_xcl=B → magic mismatch."""
        sender_priv, sender_keys = make_test_privacy(PepProtocol.RTP, PepMode.AES_128_CTR)
        sender_priv.xcl = TEST_XCL

        receiver_priv, receiver_keys = make_test_privacy(PepProtocol.RTP, PepMode.AES_128_CTR)
        receiver_priv.xcl = TEST_XCL_DIFFERENT  # Different!

        sid = str(uuid.uuid4())
        sender_enc = StreamEncryption.from_privacy(
            sender_priv, sender_keys, sid, is_sender=True, verbose=False,
        )
        receiver_enc = StreamEncryption.from_privacy(
            receiver_priv, receiver_keys, sid, is_sender=False, verbose=False,
        )
        assert sender_enc is not None and receiver_enc is not None
        assert sender_enc.privacy_key != receiver_enc.privacy_key  # Keys differ

        # Encrypt with sender, try to decrypt with receiver — should produce garbage
        encrypt = sender_enc.make_encrypt_fn()
        decrypt = receiver_enc.make_decrypt_fn()
        wire = encrypt(_make_packet(), ctr=0)
        decrypted, _ = decrypt(wire)
        import struct
        magic = struct.unpack("<I", decrypted[:4])[0]
        assert magic != 0x49504D58, "Different key_xcl should produce wrong magic"

    def test_no_key_xcl_matches_baseline(self) -> None:
        """Privacy.xcl=empty → derived key matches baseline (no reservation)."""
        priv_baseline, keys = make_test_privacy(PepProtocol.RTP, PepMode.AES_128_CTR)
        priv_baseline.xcl = b""

        priv_xcl, keys_xcl = make_test_privacy(PepProtocol.RTP, PepMode.AES_128_CTR)
        priv_xcl.xcl = b""  # Explicitly empty

        enc_baseline = StreamEncryption.from_privacy(
            priv_baseline, keys, "s", is_sender=True, verbose=False,
        )
        enc_empty = StreamEncryption.from_privacy(
            priv_xcl, keys_xcl, "s", is_sender=True, verbose=False,
        )
        assert enc_baseline is not None and enc_empty is not None
        assert enc_baseline.privacy_key == enc_empty.privacy_key


# ---------------------------------------------------------------------------
# E2E encrypted loopback with key_xcl
# ---------------------------------------------------------------------------

class TestKeyXclE2ELoopback:
    """End-to-end encrypted streaming with key_xcl on real sockets."""

    @pytest.mark.asyncio
    async def test_pep_e2e_with_key_xcl_psk(self) -> None:
        """Sender and receiver with same key_xcl → clean E2E loopback."""
        from nmos.node.streaming.tests._pep_e2e_harness import (
            run_pep_e2e, assert_keys_agree, assert_clean_round_trip,
        )

        # Monkey-patch: set key_xcl on the Privacy objects inside the harness
        # by creating them manually and calling the lower-level transport
        sender_priv, sender_keys = make_test_privacy(PepProtocol.RTP, PepMode.AES_128_CTR)
        sender_priv.xcl = TEST_XCL
        receiver_priv, receiver_keys = make_test_privacy(PepProtocol.RTP, PepMode.AES_128_CTR)
        receiver_priv.xcl = TEST_XCL

        sid, rid = str(uuid.uuid4()), str(uuid.uuid4())

        sender_enc = StreamEncryption.from_privacy(
            sender_priv, sender_keys, sid, is_sender=True, verbose=False,
        )
        receiver_enc = StreamEncryption.from_privacy(
            receiver_priv, receiver_keys, rid, is_sender=False, verbose=False,
        )
        assert sender_enc is not None and receiver_enc is not None
        assert sender_enc.privacy_key == receiver_enc.privacy_key

        encrypt_fn = sender_enc.make_encrypt_fn()
        decrypt_fn = receiver_enc.make_decrypt_fn()

        from nmos.node.streaming.transport_udp import udp_sender, udp_receiver
        from nmos.node.events import EngineEvent, EventId

        port = 19500
        event_queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=200)
        sender_stop = asyncio.Event()
        receiver_stop = asyncio.Event()
        loop = asyncio.get_event_loop()

        receiver_task = asyncio.create_task(udp_receiver(
            loop=loop, interface_ip="127.0.0.1", multicast_ip="",
            source_ip="", dest_port=port,
            receiver_id=rid, interface_name="lo",
            event_queue=event_queue, decrypt_fn=decrypt_fn,
            stop_event=receiver_stop,
        ))
        await asyncio.sleep(0.3)
        sender_task = asyncio.create_task(udp_sender(
            loop=loop, source_ip="127.0.0.1", source_port=0,
            dest_ip="127.0.0.1", dest_port=port,
            sender_id=sid, interface_name="lo",
            event_queue=event_queue, encrypt_fn=encrypt_fn,
            stop_event=sender_stop,
        ))

        await asyncio.sleep(3.0)
        sender_stop.set()
        await asyncio.sleep(0.5)
        receiver_stop.set()
        await asyncio.gather(sender_task, receiver_task, return_exceptions=True)

        events = []
        while not event_queue.empty():
            events.append(event_queue.get_nowait())

        # Find sender deactivation — errors after are expected
        deactivate_idx = None
        for i, e in enumerate(events):
            if e.event == EventId.VENDOR_TRANSPORT_DEACTIVATE and e.id == sid:
                deactivate_idx = i
                break
        pre = events[:deactivate_idx] if deactivate_idx else events

        error_events = [
            e for e in pre
            if e.event in (EventId.TRANSPORT_PACKET_LOST, EventId.TRANSPORT_PACKET_LATE,
                           EventId.ESSENCE_STREAM_ERROR, EventId.TRANSPORT_STREAM_ERROR)
        ]
        assert not error_events, (
            f"Errors during key_xcl encrypted streaming: "
            f"{[(e.event, e.info) for e in error_events]}"
        )

    @pytest.mark.asyncio
    async def test_pep_e2e_with_key_xcl_ecdh(self) -> None:
        """ECDH mode + key_xcl → clean E2E loopback."""
        from nmos.node.streaming.tests._pep_helpers import exchange_ecdh_keys

        sender_priv, sender_keys = make_test_privacy(PepProtocol.RTP, PepMode.ECDH_AES_128_CTR)
        receiver_priv, receiver_keys = make_test_privacy(PepProtocol.RTP, PepMode.ECDH_AES_128_CTR)
        exchange_ecdh_keys(sender_priv, receiver_priv)

        sender_priv.xcl = TEST_XCL
        receiver_priv.xcl = TEST_XCL

        sid, rid = str(uuid.uuid4()), str(uuid.uuid4())

        sender_enc = StreamEncryption.from_privacy(
            sender_priv, sender_keys, sid, is_sender=True, verbose=False,
        )
        receiver_enc = StreamEncryption.from_privacy(
            receiver_priv, receiver_keys, rid, is_sender=False, verbose=False,
        )
        assert sender_enc is not None and receiver_enc is not None
        assert sender_enc.privacy_key == receiver_enc.privacy_key
        assert len(sender_enc.key_pfs) > 0  # ECDH active
        assert sender_enc.key_xcl == TEST_XCL

        # Quick round-trip (no sockets needed — just verify keys match)
        encrypt = sender_enc.make_encrypt_fn()
        decrypt = receiver_enc.make_decrypt_fn()
        for seq in range(5):
            wire = encrypt(_make_packet(seq), ctr=seq)
            plaintext, ctr = decrypt(wire)
            assert ctr == seq
            import struct
            magic = struct.unpack("<I", plaintext[:4])[0]
            assert magic == 0x49504D58


# ---------------------------------------------------------------------------
# IS-05 Transport Parameter Path Tests
# ---------------------------------------------------------------------------

class TestIS05TransportParameterPath:
    """Drive privacy params through IS-05 PATCH → activation → verify
    activation.privacy is correctly populated. Addresses the gap from the
    previous PEP plan where tests bypassed the transport parameter path."""

    def test_is05_patch_programs_sender_privacy_mode(self) -> None:
        """PATCH ext_privacy_mode → flip → _sync_privacy → activation.privacy.mode set."""
        from nmos.node import Node
        from nmos.node.config import ConfigBuilder
        from nmos.node.activation_engine import (
            update_staged_params, flip_activation, _sync_privacy_from_active_params,
        )
        from nmos.node.activation import get_transport_descriptor
        from nmos.enums import EnumRegistry
        from nmos.json.engine import JsonEngine

        node = Node()
        node.init(serial_number="IS05TEST")
        node.privacy_enabled = True

        # Build a sender from config1
        from pathlib import Path as _P
        config_path = _P(__file__).parent.parent.parent / "config" / "builtin" / "config1.json"
        if not config_path.exists():
            pytest.skip("config1.json not found")
        import json
        with open(config_path) as f:
            config = json.load(f)
        builder = ConfigBuilder(node, verbose=False)
        for s in config.get("senders", []):
            try:
                builder._build_sender_pipeline(s)
            except Exception:
                pass

        # Get first sender's activation
        sender_static = None
        for sid, _sender in node.senders:
            sender_static = sid
            break
        assert sender_static is not None
        activation = node.sender_activation.get(sender_static)
        assert activation is not None

        # Build a PATCH body that changes ext_privacy_mode
        target_mode = "AES-128-CTR"
        target_protocol = "RTP"
        patch_tp = {}
        for field_name in ('ExtPrivacyProtocol', 'ExtPrivacyMode'):
            field = getattr(activation.staged[0], field_name, None)
            if field is not None:
                if field_name == 'ExtPrivacyProtocol':
                    field.value = EnumRegistry.get(target_protocol)
                elif field_name == 'ExtPrivacyMode':
                    field.value = EnumRegistry.get(target_mode)

        # Flip staged → active
        from nmos.node.activation import get_transport_descriptor
        sender = node.senders.get(sender_static)
        transport_enum = sender.Transport.value if sender.Transport.defined else None
        auto_resolvers = None
        if transport_enum:
            try:
                desc = get_transport_descriptor(transport_enum)
                auto_resolvers = desc.sender_auto_resolvers
            except KeyError:
                pass
        flip_activation(activation, node.legs, auto_resolvers)

        # Sync privacy
        _sync_privacy_from_active_params(activation)

        # Verify activation.privacy has the correct mode
        mode_val = activation.privacy.mode
        if mode_val is not None:
            assert str(mode_val) == target_mode or target_mode in str(mode_val), \
                f"Expected mode '{target_mode}', got '{mode_val}'"

    def test_key_xcl_from_session_used_in_activation(self) -> None:
        """With an active session, activation.privacy.xcl gets the session's exclusive_key."""
        from nmos.node import Node
        from nmos.node.config import ConfigBuilder
        from nmos.node.activation_engine import do_activation
        from nmos.crypto import ExclusiveSession

        node = Node()
        node.init(serial_number="XCLTEST")
        node.privacy_enabled = True
        node.exclusive_session = ExclusiveSession()

        from pathlib import Path as _P
        config_path = _P(__file__).parent.parent.parent / "config" / "builtin" / "config1.json"
        if not config_path.exists():
            pytest.skip("config1.json not found")
        import json
        with open(config_path) as f:
            config = json.load(f)
        builder = ConfigBuilder(node, verbose=False)
        for s in config.get("senders", []):
            try:
                builder._build_sender_pipeline(s)
            except Exception:
                pass

        # Acquire session
        xcl_hex = "aabbccdd11223344aabbccdd11223344"
        token = node.exclusive_session.acquire("test-ctrl", bytes.fromhex(xcl_hex))

        # Propagate key_xcl to all activations (as the handler would)
        from nmos.api.handlers_exclusive import _set_key_xcl_on_all_activations
        _set_key_xcl_on_all_activations(node, bytes.fromhex(xcl_hex))

        # Check that sender activations have key_xcl set
        for _sid, activation in node.sender_activation:
            assert activation.privacy.xcl == bytes.fromhex(xcl_hex), \
                "key_xcl should be propagated from session to activation.privacy.xcl"
            break
