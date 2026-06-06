# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers for PEP streaming tests.

These utilities are used across the PEP test suites (primitive regression
anchors, IS-05 integration, end-to-end subprocess harness). They centralize
the boilerplate for constructing :class:`Privacy` objects with specific
protocol/mode/curve combinations, exchanging ECDH public keys between peer
sides, building the IS-05 PATCH JSON body, and wiring up the four
:class:`StreamEncryption` instances required by a bidirectional transport.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Ensure the pep/ module directory is importable.
_PEP_PATH = str(Path(__file__).parent.parent.parent.parent.parent / "pep")
if _PEP_PATH not in sys.path:
    sys.path.insert(0, _PEP_PATH)

from nmos.node.types import Privacy, PrivacyPreSharedKeys, PreSharedKey
from nmos.node.streaming.encryption import StreamEncryption


# ---------------------------------------------------------------------------
# PSK fixtures
# ---------------------------------------------------------------------------

#: Deterministic 128-bit PSK (matches existing test_encryption.py fixture).
TEST_PSK_128 = bytes(range(16))
#: Deterministic 256-bit PSK for AES-256 modes.
TEST_PSK_256 = bytes(range(32))
#: 8-byte test KeyId.
TEST_KEY_ID = bytes(range(8))
#: 16-byte test KeyGenerator.
TEST_KEY_GENERATOR = bytes(range(16))
#: 8-byte test IV base.
TEST_IV = bytes(range(8))
#: Initial 4-byte key_version.
TEST_KEY_VERSION = b"\x00\x00\x00\x01"


def psk_for_mode(mode_str: str) -> bytes:
    """Return a PSK of the correct size for the given mode.

    128-bit modes require a 128-bit PSK; 256-bit modes require a 256-bit PSK
    (Matrox §"Mode" line 145-149).
    """
    return TEST_PSK_256 if "256" in mode_str else TEST_PSK_128


# ---------------------------------------------------------------------------
# Privacy object construction
# ---------------------------------------------------------------------------

def make_test_privacy(
    protocol: Any,
    mode: Any,
    curve: Any = None,
    *,
    psk: bytes | None = None,
    iv: bytes = TEST_IV,
    key_generator: bytes = TEST_KEY_GENERATOR,
    key_version: bytes = TEST_KEY_VERSION,
    key_id: bytes = TEST_KEY_ID,
) -> tuple[Privacy, PrivacyPreSharedKeys]:
    """Create a fully-populated :class:`Privacy` + :class:`PrivacyPreSharedKeys`
    pair for test use.

    Args:
        protocol: :class:`PepProtocol` member (or its string value).
        mode: :class:`PepMode` member (or its string value).
        curve: Optional ECDH curve EnumId (required for ECDH modes).
        psk: Pre-shared key bytes. Defaults based on the mode's AES key size.
        iv/key_generator/key_version/key_id: Static test values by default.

    Returns:
        ``(privacy, privacy_keys)`` — both fully populated and ready to feed
        into :meth:`StreamEncryption.from_privacy`.
    """
    # Resolve PSK size from the mode when not supplied.
    mode_str = mode.value if hasattr(mode, 'value') else str(mode)
    if psk is None:
        psk = psk_for_mode(mode_str)

    privacy = Privacy(
        iv=iv,
        key_generator=key_generator,
        key_version=key_version,
        key_id=key_id,
        psk=psk,
        protocol=protocol,
        mode=mode,
        curve=curve,
    )
    keys = PrivacyPreSharedKeys(keys=[PreSharedKey(key_id=key_id, psk=psk)])
    return privacy, keys


# ---------------------------------------------------------------------------
# ECDH key exchange
# ---------------------------------------------------------------------------

def exchange_ecdh_keys(
    sender_privacy: Privacy,
    receiver_privacy: Privacy,
    curve: Any = None,
) -> None:
    """Generate ECDH key pairs on both sides and exchange public keys.

    After this call, both :class:`Privacy` instances have the private key
    for their role plus the peer's public key, enabling
    :func:`nmos.node.privacy.compute_ecdh_shared_secret` to produce the
    matching PFS shared secret.

    The ``curve`` argument (if provided) overrides the default curve from
    :mod:`nmos.node.privacy`.
    """
    from nmos.node.privacy import (
        generate_ecdh_sender_key,
        generate_ecdh_receiver_key,
    )

    if curve is not None:
        sender_privacy.curve = curve
        receiver_privacy.curve = curve

    generate_ecdh_sender_key(sender_privacy, update=(curve is not None))
    generate_ecdh_receiver_key(receiver_privacy, update=(curve is not None))

    # Exchange the public keys (the private keys stay with their owner).
    sender_privacy.ecdh_receiver_public_key = receiver_privacy.ecdh_receiver_public_key
    receiver_privacy.ecdh_sender_public_key = sender_privacy.ecdh_sender_public_key


# ---------------------------------------------------------------------------
# Bidirectional context builder
# ---------------------------------------------------------------------------

def make_bidirectional_contexts(
    sender_privacy: Privacy,
    sender_keys: PrivacyPreSharedKeys,
    receiver_privacy: Privacy,
    receiver_keys: PrivacyPreSharedKeys,
    sender_id: str,
    receiver_id: str,
    *,
    forward_kv: bytes | None = None,
    reverse_kv: bytes | None = None,
    verbose: bool = False,
) -> dict[str, StreamEncryption]:
    """Build the four :class:`StreamEncryption` instances required by a
    bidirectional transport.

    Returns a dict with keys ``sender_tx``, ``sender_rx``, ``receiver_rx``,
    ``receiver_tx`` where:

    - ``sender_tx`` / ``receiver_rx`` share ``substreamid=0`` + ``forward_kv``
      (Sender→Receiver direction — same derived key on both sides).
    - ``sender_rx`` / ``receiver_tx`` share ``substreamid=1`` + ``reverse_kv``
      (Receiver→Sender direction — same derived key on both sides).

    Per TR-10-13 §14 the even/odd substreamid split guarantees unique
    ``iv_prime`` per direction; per §20.3 each direction independently
    selects its initial ``key_version``.
    """
    if forward_kv is None:
        forward_kv = b"\x00\x00\x00\x01"
    if reverse_kv is None:
        reverse_kv = b"\x00\x00\x00\x02"  # Deliberately different from forward.

    sender_tx = StreamEncryption.from_privacy(
        sender_privacy, sender_keys, sender_id, is_sender=True,
        verbose=verbose, substreamid=0, key_version_override=forward_kv,
        direction="tx",
    )
    sender_rx = StreamEncryption.from_privacy(
        sender_privacy, sender_keys, sender_id, is_sender=True,
        verbose=verbose, substreamid=1, key_version_override=reverse_kv,
        direction="rx",
    )
    receiver_rx = StreamEncryption.from_privacy(
        receiver_privacy, receiver_keys, receiver_id, is_sender=False,
        verbose=verbose, substreamid=0, key_version_override=forward_kv,
        direction="rx",
    )
    receiver_tx = StreamEncryption.from_privacy(
        receiver_privacy, receiver_keys, receiver_id, is_sender=False,
        verbose=verbose, substreamid=1, key_version_override=reverse_kv,
        direction="tx",
    )

    assert sender_tx is not None and sender_rx is not None
    assert receiver_rx is not None and receiver_tx is not None

    return {
        "sender_tx": sender_tx,
        "sender_rx": sender_rx,
        "receiver_rx": receiver_rx,
        "receiver_tx": receiver_tx,
    }


# ---------------------------------------------------------------------------
# IS-05 PATCH body builder
# ---------------------------------------------------------------------------

def _hex(b: bytes) -> str:
    return b.hex() if b else "00"


def build_privacy_patch_body(
    protocol: str,
    mode: str,
    *,
    iv: bytes = TEST_IV,
    key_generator: bytes = TEST_KEY_GENERATOR,
    key_version: bytes = TEST_KEY_VERSION,
    key_id: bytes = TEST_KEY_ID,
    curve: str | None = None,
    sender_public_key: bytes | None = None,
    receiver_public_key: bytes | None = None,
    master_enable: bool = True,
) -> dict:
    """Build a JSON body suitable for PATCH to ``/single/<role>s/{id}/staged``.

    Fills only the ``ext_privacy_*`` transport parameter fields that apply to
    the requested protocol/mode/curve. Leaves ECDH fields out when
    ``curve`` is ``None``.
    """
    tp: dict[str, Any] = {
        "ext_privacy_protocol": protocol,
        "ext_privacy_mode": mode,
        "ext_privacy_iv": _hex(iv),
        "ext_privacy_key_generator": _hex(key_generator),
        "ext_privacy_key_version": _hex(key_version),
        "ext_privacy_key_id": _hex(key_id),
    }
    if curve is not None:
        tp["ext_privacy_ecdh_curve"] = curve
        if sender_public_key is not None:
            tp["ext_privacy_ecdh_sender_public_key"] = _hex(sender_public_key)
        if receiver_public_key is not None:
            tp["ext_privacy_ecdh_receiver_public_key"] = _hex(receiver_public_key)

    return {
        "master_enable": master_enable,
        "activation": {"mode": "activate_immediate"},
        "transport_params": [tp],
    }
