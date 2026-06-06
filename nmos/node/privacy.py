# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Privacy / ECDH key generation for transport encryption.

ECDH key generation stays in the NMOS node. The actual PEP/KDP crypto
operations are external (ffmpeg). The node generates and stores:
- IV (8 bytes, unique per sender)
- KeyGenerator (16 bytes random)
- KeyVersion (4 bytes random)
- ECDH sender/receiver key pairs (P-256, P-521, or X25519)
"""

from __future__ import annotations

import os
from typing import Any, cast

from cryptography.hazmat.primitives.asymmetric.ec import (
    SECP256R1,
    SECP521R1,
    EllipticCurvePrivateKey,
    EllipticCurvePublicKey,
    generate_private_key as ec_generate_private_key,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)

from nmos.errors import UnexpectedError
from nmos.node.types import Activation, Privacy


# ---------------------------------------------------------------------------
# Curve enum URNs — resolved lazily to avoid circular import
# ---------------------------------------------------------------------------

_CURVE_SECP256R1: Any = None
_CURVE_SECP521R1: Any = None
_CURVE_25519: Any = None
_CURVE_448: Any = None
_CURVE_DEFAULT: Any = None  # UseTransportPrivacyEcdhCurve


def _init_curves() -> None:
    """Lazy-init curve enum references."""
    global _CURVE_SECP256R1, _CURVE_SECP521R1, _CURVE_25519, _CURVE_448, _CURVE_DEFAULT
    if _CURVE_SECP256R1 is not None:
        return
    try:
        import nmos.enums as enums
        _CURVE_SECP256R1 = enums.Curve_secp256r1
        _CURVE_SECP521R1 = enums.Curve_secp521r1
        _CURVE_25519 = enums.Curve_25519
        _CURVE_448 = enums.Curve_448 if hasattr(enums, 'Curve_448') else None
        _CURVE_DEFAULT = _CURVE_25519  # UseTransportPrivacyEcdhCurve
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Byte swapping (for X25519)
# ---------------------------------------------------------------------------

def _swap_bytes(data: bytes) -> bytes:
    """Reverse byte order. Used for X25519 public key encoding."""
    return bytes(reversed(data))


# ---------------------------------------------------------------------------
# Public key serialization (portable across cryptography library versions)
# ---------------------------------------------------------------------------

def _get_public_bytes(pub_key: Any) -> bytes:
    """Extract raw public key bytes, compatible with all cryptography versions.

    X25519/X448 keys use Raw encoding. EC keys (secp256r1, secp521r1) use
    uncompressed point format (0x04 || X || Y) since Raw is only available
    in cryptography >= 41 for EC.
    """
    # Try the modern API first (cryptography >= 41 supports it for all key types)
    if hasattr(pub_key, 'public_bytes_raw'):
        try:
            return cast(bytes, pub_key.public_bytes_raw())
        except Exception:
            pass
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    try:
        # X25519 / X448 — Raw encoding works
        return cast(bytes, pub_key.public_bytes(Encoding.Raw, PublicFormat.Raw))
    except (ValueError, TypeError):
        # EC keys — fall back to uncompressed point (0x04 prefix + X + Y)
        return cast(
            bytes,
            pub_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint),
        )


# ---------------------------------------------------------------------------
# ECDH key generation
# ---------------------------------------------------------------------------

def generate_ecdh_sender_key(privacy: Privacy, update: bool = False) -> None:
    """Generate an ECDH key pair for the sender side.

    The private key is stored in privacy.ecdh_sender_private.
    The public key bytes go in privacy.ecdh_sender_public_key.
    """
    _init_curves()
    curve = _CURVE_DEFAULT if not update else privacy.curve

    if curve is _CURVE_SECP256R1:
        ec_priv = ec_generate_private_key(SECP256R1())
        assert isinstance(ec_priv, EllipticCurvePrivateKey)
        pub_bytes = _get_public_bytes(ec_priv.public_key())
        privacy.ecdh_sender_public_key = pub_bytes
        privacy.ecdh_sender_private = ec_priv
        privacy.ecdh_sender_public = None

    elif curve is _CURVE_SECP521R1:
        ec_priv = ec_generate_private_key(SECP521R1())
        assert isinstance(ec_priv, EllipticCurvePrivateKey)
        pub_bytes = _get_public_bytes(ec_priv.public_key())
        privacy.ecdh_sender_public_key = pub_bytes
        privacy.ecdh_sender_private = ec_priv
        privacy.ecdh_sender_public = None

    elif curve is _CURVE_25519:
        x25519_priv = X25519PrivateKey.generate()
        pub_bytes = _get_public_bytes(x25519_priv.public_key())
        privacy.ecdh_sender_public_key = _swap_bytes(pub_bytes)
        privacy.ecdh_sender_private = x25519_priv
        privacy.ecdh_sender_public = None

    elif _CURVE_448 is not None and curve is _CURVE_448:
        from cryptography.hazmat.primitives.asymmetric.x448 import X448PrivateKey
        x448_priv = X448PrivateKey.generate()
        pub_bytes = _get_public_bytes(x448_priv.public_key())
        privacy.ecdh_sender_public_key = _swap_bytes(pub_bytes)
        privacy.ecdh_sender_private = x448_priv
        privacy.ecdh_sender_public = None

    else:
        raise UnexpectedError(f"unsupported ECDH curve: {curve}")


def generate_ecdh_receiver_key(privacy: Privacy, update: bool = False) -> None:
    """Generate an ECDH key pair for the receiver side."""
    _init_curves()
    curve = _CURVE_DEFAULT if not update else privacy.curve

    if curve is _CURVE_SECP256R1:
        ec_priv = ec_generate_private_key(SECP256R1())
        assert isinstance(ec_priv, EllipticCurvePrivateKey)
        pub_bytes = _get_public_bytes(ec_priv.public_key())
        privacy.ecdh_receiver_public_key = pub_bytes
        privacy.ecdh_receiver_private = ec_priv
        privacy.ecdh_receiver_public = None

    elif curve is _CURVE_SECP521R1:
        ec_priv = ec_generate_private_key(SECP521R1())
        assert isinstance(ec_priv, EllipticCurvePrivateKey)
        pub_bytes = _get_public_bytes(ec_priv.public_key())
        privacy.ecdh_receiver_public_key = pub_bytes
        privacy.ecdh_receiver_private = ec_priv
        privacy.ecdh_receiver_public = None

    elif curve is _CURVE_25519:
        x25519_priv = X25519PrivateKey.generate()
        pub_bytes = _get_public_bytes(x25519_priv.public_key())
        privacy.ecdh_receiver_public_key = _swap_bytes(pub_bytes)
        privacy.ecdh_receiver_private = x25519_priv
        privacy.ecdh_receiver_public = None

    elif _CURVE_448 is not None and curve is _CURVE_448:
        from cryptography.hazmat.primitives.asymmetric.x448 import X448PrivateKey
        x448_priv = X448PrivateKey.generate()
        pub_bytes = _get_public_bytes(x448_priv.public_key())
        privacy.ecdh_receiver_public_key = _swap_bytes(pub_bytes)
        privacy.ecdh_receiver_private = x448_priv
        privacy.ecdh_receiver_public = None

    else:
        raise UnexpectedError(f"unsupported ECDH curve: {curve}")


# ---------------------------------------------------------------------------
# ECDH shared secret computation
# ---------------------------------------------------------------------------

def compute_ecdh_shared_secret(privacy: Privacy, is_sender: bool) -> bytes:
    """Compute the ECDH shared secret (key_pfs) for PEP key derivation.

    The sender uses its private key and the receiver's public key.
    The receiver uses its private key and the sender's public key.
    Both arrive at the same shared secret.

    TR-10-13 Section 12: key_pfs is the ECDH shared secret used as
    additional input to the KDF when ECDH modes are active.

    Returns empty bytes if ECDH keys are not available.
    """
    from cryptography.hazmat.primitives.asymmetric.ec import ECDH

    if is_sender:
        priv = privacy.ecdh_sender_private
        peer_pub_bytes = privacy.ecdh_receiver_public_key
    else:
        priv = privacy.ecdh_receiver_private
        peer_pub_bytes = privacy.ecdh_sender_public_key

    if priv is None or not peer_pub_bytes:
        return b""

    try:
        # X448 (Curve448) — raw 56-byte public key, byte-swapped like X25519
        try:
            from cryptography.hazmat.primitives.asymmetric.x448 import (
                X448PrivateKey as _X448Priv,
                X448PublicKey as _X448Pub,
            )
            if isinstance(priv, _X448Priv):
                x448_pub = _X448Pub.from_public_bytes(_swap_bytes(peer_pub_bytes))
                return priv.exchange(x448_pub)
        except ImportError:
            pass

        if isinstance(priv, X25519PrivateKey):
            # X25519: raw 32-byte public key
            # The public key bytes may be byte-swapped (device compatibility)
            x25519_pub = X25519PublicKey.from_public_bytes(_swap_bytes(peer_pub_bytes))
            return priv.exchange(x25519_pub)

        elif isinstance(priv, EllipticCurvePrivateKey):
            # P-256 or P-521: compressed or uncompressed EC public key
            curve = priv.curve
            # Reconstruct EC public key from serialized bytes.
            # If bytes already have 0x04 prefix (uncompressed point) use as-is;
            # otherwise prepend 0x04 for the uncompressed format.
            if peer_pub_bytes[0:1] == b"\x04":
                encoded = peer_pub_bytes
            else:
                encoded = b"\x04" + peer_pub_bytes
            ec_pub = EllipticCurvePublicKey.from_encoded_point(curve, encoded)
            return priv.exchange(ECDH(), ec_pub)

    except Exception:
        return b""

    return b""


# ---------------------------------------------------------------------------
# IV / KeyGenerator / KeyVersion generation
# ---------------------------------------------------------------------------

def generate_sender_iv(existing_activations: dict[str, Activation]) -> bytes:
    """Generate a unique 8-byte IV for a sender.

    Retries up to 16 times to avoid collisions with existing sender IVs.
    """
    for _ in range(16):
        iv = os.urandom(8)
        # Check uniqueness against all existing sender activations
        collision = False
        for act in existing_activations.values():
            if act.privacy.iv == iv:
                collision = True
                break
        if not collision:
            return iv

    raise UnexpectedError("cannot generate a unique random IV after 16 attempts")


def generate_key_generator() -> bytes:
    """Generate a random 16-byte key generator."""
    return os.urandom(16)


def generate_key_version() -> bytes:
    """Generate a random 4-byte key version."""
    return os.urandom(4)


# ---------------------------------------------------------------------------
# High-level: generate all sender/receiver privacy parameters
# ---------------------------------------------------------------------------

def generate_sender_privacy_parameters(
    privacy: Privacy,
    existing_activations: dict[str, Activation],
) -> None:
    """Generate all privacy parameters for a new sender.

    Sets IV, KeyGenerator, KeyVersion, and ECDH sender key pair.
    """
    privacy.iv = generate_sender_iv(existing_activations)
    privacy.key_generator = generate_key_generator()
    privacy.key_version = generate_key_version()
    generate_ecdh_sender_key(privacy, update=False)


def generate_receiver_privacy_parameters(privacy: Privacy) -> None:
    """Generate all privacy parameters for a new receiver.

    Only generates ECDH receiver key pair (receivers don't generate IV/keys).
    """
    generate_ecdh_receiver_key(privacy, update=False)
