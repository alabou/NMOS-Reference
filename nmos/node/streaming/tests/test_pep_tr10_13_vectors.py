# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""TR-10-13 Section 19: Official PEP key derivation test vectors.

These vectors are from VSF TR-10-13 v1.4 (2026-02-17) Table 2 and are the
authoritative compliance check for the KDF implementation. They cover:
- All 3 ECDH curves (25519, secp256r1, secp521r1) with ECDH PFS
- PSK-only modes (no ECDH PFS)
- 128-bit and 256-bit key derivation
- PSK sizes: 128, 256, and 512 bits
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PEP_PATH = str(Path(__file__).parent.parent.parent.parent.parent / "pep")
if _PEP_PATH not in sys.path:
    sys.path.insert(0, _PEP_PATH)

from ipmx_pep import derive_privacy_key  # noqa: E402


# ---------------------------------------------------------------------------
# Test vectors from TR-10-13 Section 19 (Table 2)
# ---------------------------------------------------------------------------

# Each tuple: (description, key_generator, key_version, psk, key_pfs, key_bits, expected_key)
_VECTORS = [
    # Vector 1: ECDH_AES-128-CTR, curve 25519, PSK=128b
    (
        "ECDH_AES-128-CTR, curve=25519, PSK=128b",
        bytes.fromhex("2a4ab04bd61219d37a91abf6f94ab124"),
        bytes.fromhex("a7938740"),
        bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
        bytes.fromhex("218f8b81501ea437e0bc2c21a8e9af2be7bee3b1c553f9ccaaf40e3dc19374c6"),
        128,
        bytes.fromhex("dee53f79ac29628644d01783b5b3c0b7"),
    ),
    # Vector 2: ECDH_AES-128-CTR, curve=secp256r1, PSK=128b
    (
        "ECDH_AES-128-CTR, curve=secp256r1, PSK=128b",
        bytes.fromhex("2edf9023a68fb83c5d1f018d7cd3783e"),
        bytes.fromhex("cc2301ed"),
        bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
        bytes.fromhex("dcf9d6b750d8c51419127f6e9ef9c91199bb99237d28e4054a6486f190b403d3"),
        128,
        bytes.fromhex("12d376fa12f933780b1a68b9ebdb4187"),
    ),
    # Vector 3: ECDH_AES-128-CTR, curve=secp521r1, PSK=128b
    (
        "ECDH_AES-128-CTR, curve=secp521r1, PSK=128b",
        bytes.fromhex("a7ebcd7bef2b32abc008e1d0d0c777a0"),
        bytes.fromhex("5c436e9d"),
        bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
        bytes.fromhex("015df637be34bb2edc8f493d3cdbb4ba05371b894cf20adf899ad5a1cbbba4c26acaf1342b3766e5f686b00537d810372fb840b28c4a3587bba07cf12721cff37846"),
        128,
        bytes.fromhex("56afadf373fccef80e70a755fe0a1588"),
    ),
    # Vector 4: ECDH_AES-256-CTR, curve=25519, PSK=128b → 256-bit key
    (
        "ECDH_AES-256-CTR, curve=25519, PSK=128b",
        bytes.fromhex("a208336568863d5cf6ee704837340d79"),
        bytes.fromhex("84f03939"),
        bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
        bytes.fromhex("79a44729b1f4d9f52a4e210a5b4e776de4f511837798b88beafd5aaa41eb0700"),
        256,
        bytes.fromhex("f78d42babb85119405b13bb1199a80bdd5557cc64a596d97abe9bf945079d81a"),
    ),
    # Vector 5: ECDH_AES-256-CTR, curve=secp256r1, PSK=128b → 256-bit key
    (
        "ECDH_AES-256-CTR, curve=secp256r1, PSK=128b",
        bytes.fromhex("51fa624b4c62a2125e45424c2f185cb9"),
        bytes.fromhex("2b7a8223"),
        bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
        bytes.fromhex("3e1e0e9836bd01b38a9f18fac02da9d5a545f1ca8149f076917d6f3e3a8b94eb"),
        256,
        bytes.fromhex("a3ba0f316f10fb6866bbeb3d6841b346505a1c1f5ec3e36c626721637c0c5aaa"),
    ),
    # Vector 6: ECDH_AES-256-CTR, curve=secp521r1, PSK=128b → 256-bit key
    (
        "ECDH_AES-256-CTR, curve=secp521r1, PSK=128b",
        bytes.fromhex("8623b4b1e6fa7067be1f5952ad6299b8"),
        bytes.fromhex("2af1988d"),
        bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
        bytes.fromhex("00c25350af2ccf296cd60e055b8d70c66a40db98eccb179103c0208700df96ba41d144abd1875128824a659ae133e394ace2d3e898d95f8f895e96e3a4593a570cf4"),
        256,
        bytes.fromhex("3b99a7d6eca76f53600084aec2ce920c5a73391b650b95fc285d00b6286e28d9"),
    ),
    # Vector 7: AES-128-CTR (PSK-only, no ECDH), PSK=128b → 128-bit key
    (
        "AES-128-CTR, no ECDH, PSK=128b",
        bytes.fromhex("52bbbea2b2cdc7ddbb18c23becd3c753"),
        bytes.fromhex("007c84b5"),
        bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
        b"",  # No PFS
        128,
        bytes.fromhex("650132d60b2700cd2aa3e25f24aa8980"),
    ),
    # Vector 8: AES-256-CTR (PSK-only, no ECDH), PSK=128b → 256-bit key
    (
        "AES-256-CTR, no ECDH, PSK=128b",
        bytes.fromhex("52bbbea2b2cdc7ddbb18c23becd3c753"),
        bytes.fromhex("007c84b5"),
        bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
        b"",
        256,
        bytes.fromhex("650132d60b2700cd2aa3e25f24aa8980cafd1d993e2e2a36640b7795579c089a"),
    ),
    # Vector 9: AES-256-CTR (PSK-only, no ECDH), PSK=256b → 256-bit key
    (
        "AES-256-CTR, no ECDH, PSK=256b",
        bytes.fromhex("f99067d1f5f72363d3b0e009ab34c36b"),
        bytes.fromhex("7251c65d"),
        bytes.fromhex("000102030405060708090a0b0c0d0e0f000102030405060708090a0b0c0d0e0f"),
        b"",
        256,
        bytes.fromhex("e9ceff8c8aa6aa6680c1928a5427fb71351ce3c9c507c92a9fba3bcbd65681f3"),
    ),
    # Vector 10: AES-256-CTR (PSK-only, no ECDH), PSK=512b → 256-bit key
    (
        "AES-256-CTR, no ECDH, PSK=512b",
        bytes.fromhex("1927a9d6914eb5579edd30712a081f84"),
        bytes.fromhex("c5f4a28d"),
        bytes.fromhex("000102030405060708090a0b0c0d0e0f000102030405060708090a0b0c0d0e0f000102030405060708090a0b0c0d0e0f000102030405060708090a0b0c0d0e0f"),
        b"",
        256,
        bytes.fromhex("2e4edd15087fa6d4fef2f5c16ee0d474fec93823c12099a47d00bd5cd54d87e6"),
    ),
]


@pytest.mark.parametrize(
    "desc,key_generator,key_version,psk,key_pfs,key_bits,expected_key",
    _VECTORS,
    ids=[v[0] for v in _VECTORS],
)
def test_tr10_13_key_derivation_vector(
    desc: str,
    key_generator: bytes,
    key_version: bytes,
    psk: bytes,
    key_pfs: bytes,
    key_bits: int,
    expected_key: bytes,
) -> None:
    """Verify derive_privacy_key() against official TR-10-13 §19 test vectors."""
    derived = derive_privacy_key(
        psk=psk,
        key_generator=key_generator,
        key_version=key_version,
        key_pfs=key_pfs,
        key_bits=key_bits,
    )
    assert derived == expected_key, (
        f"Vector '{desc}': expected {expected_key.hex()}, got {derived.hex()}"
    )
