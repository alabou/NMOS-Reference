# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""SRT GMAC-128 mode recognition and key-derivation tests.

Closes CR-003 from docs/audits/2026-04-19-python-nmos-conflict-register.md:
the PEP module now registers the four SRT GMAC-128 modes required by
`NMOS With Privacy Encryption.md:166` and the existing KDF handles them
with the correct key size.

Per spec line 294 the cipher itself is applied by the real SRT library
using the PEP-derived `privacy_key` as the SRT passphrase; this file
does not exercise any AES-GCM cipher — that is not the pep module's
responsibility and the simplified streaming transport's wire path
treats GMAC modes as pass-through (see `nmos/node/streaming/encryption.py`
and the pass-through tests in `test_encryption.py`).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure pep/ is importable (it is not yet a Python package).
_PEP_PATH = str(Path(__file__).parent.parent.parent.parent.parent / "pep")
if _PEP_PATH not in sys.path:
    sys.path.insert(0, _PEP_PATH)

from ipmx_pep import (  # noqa: E402
    PepMode,
    derive_privacy_key,
    mode_has_aad,
    mode_has_cmac,
    mode_has_gmac,
    mode_is_ecdh,
    mode_key_bits,
    parse_mode,
)


# ---------------------------------------------------------------------------
# Class 1 — mode registration and helpers
# ---------------------------------------------------------------------------

class TestGmacModeRecognition:
    """PepMode registers AES-{128,256}-GMAC-128 and their ECDH variants,
    and the helper functions classify them correctly."""

    def test_parse_mode_resolves_aes_128_gmac_128(self) -> None:
        assert parse_mode("AES-128-GMAC-128") is PepMode.AES_128_GMAC_128

    def test_parse_mode_resolves_aes_256_gmac_128(self) -> None:
        assert parse_mode("AES-256-GMAC-128") is PepMode.AES_256_GMAC_128

    def test_parse_mode_resolves_ecdh_aes_128_gmac_128(self) -> None:
        assert parse_mode("ECDH_AES-128-GMAC-128") is PepMode.ECDH_AES_128_GMAC_128

    def test_parse_mode_resolves_ecdh_aes_256_gmac_128(self) -> None:
        assert parse_mode("ECDH_AES-256-GMAC-128") is PepMode.ECDH_AES_256_GMAC_128

    @pytest.mark.parametrize("mode", [
        PepMode.AES_128_GMAC_128,
        PepMode.AES_256_GMAC_128,
        PepMode.ECDH_AES_128_GMAC_128,
        PepMode.ECDH_AES_256_GMAC_128,
    ])
    def test_mode_has_gmac_true_for_all_four_variants(self, mode: PepMode) -> None:
        assert mode_has_gmac(mode) is True
        # And none of them carry CMAC or AAD semantics.
        assert mode_has_cmac(mode) is False
        assert mode_has_aad(mode) is False

    @pytest.mark.parametrize("mode", [
        PepMode.AES_128_CTR,
        PepMode.AES_256_CTR,
        PepMode.AES_128_CTR_CMAC_64,
        PepMode.AES_256_CTR_CMAC_64_AAD,
        PepMode.ECDH_AES_128_CTR,
    ])
    def test_mode_has_gmac_false_for_ctr_and_cmac_modes(self, mode: PepMode) -> None:
        assert mode_has_gmac(mode) is False

    def test_mode_key_bits_is_128_for_aes_128_gmac_128(self) -> None:
        # Regression: `"128" in mode.value` would also be true for the
        # AES-256 GMAC-128 variant (the "-128" comes from the MAC-tag
        # suffix), which is why mode_key_bits parses the prefix.
        assert mode_key_bits(PepMode.AES_128_GMAC_128) == 128

    def test_mode_key_bits_is_256_for_aes_256_gmac_128(self) -> None:
        # The bugfix: "256" prefix must win over "-128" tag suffix.
        assert mode_key_bits(PepMode.AES_256_GMAC_128) == 256
        assert mode_key_bits(PepMode.ECDH_AES_256_GMAC_128) == 256

    def test_mode_is_ecdh_true_only_for_ecdh_gmac_variants(self) -> None:
        assert mode_is_ecdh(PepMode.ECDH_AES_128_GMAC_128) is True
        assert mode_is_ecdh(PepMode.ECDH_AES_256_GMAC_128) is True
        assert mode_is_ecdh(PepMode.AES_128_GMAC_128) is False
        assert mode_is_ecdh(PepMode.AES_256_GMAC_128) is False


# ---------------------------------------------------------------------------
# Class 2 — key derivation works for GMAC modes
# ---------------------------------------------------------------------------

class TestGmacKeyDerivation:
    """The existing PEP KDF (TR-10-13 §12) produces the right-sized
    privacy_key for GMAC modes without code changes. This confirms the
    KDF is mode-agnostic and the added enum members route through it
    correctly via `mode_key_bits`."""

    # Deterministic fixtures — must not change across runs so that
    # regressions in derive_privacy_key show up as assertion failures.
    _PSK_128 = bytes(range(16))          # 128-bit PSK
    _PSK_256 = bytes(range(32))          # 256-bit PSK
    _KEY_GENERATOR = bytes(range(16))    # 16 bytes
    _KEY_VERSION = b"\x00\x00\x00\x01"   # 4 bytes

    def test_derive_privacy_key_for_aes_128_gmac_128_returns_16_bytes(self) -> None:
        bits = mode_key_bits(PepMode.AES_128_GMAC_128)
        key = derive_privacy_key(
            psk=self._PSK_128,
            key_generator=self._KEY_GENERATOR,
            key_version=self._KEY_VERSION,
            key_bits=bits,
        )
        assert bits == 128
        assert len(key) == 16

    def test_derive_privacy_key_for_aes_256_gmac_128_returns_32_bytes(self) -> None:
        # Regression for the key_bits bugfix: if we'd naively returned 128
        # for AES-256-GMAC-128, derive_privacy_key would return a 16-byte
        # key and a real SRT deployment would use the wrong passphrase.
        bits = mode_key_bits(PepMode.AES_256_GMAC_128)
        key = derive_privacy_key(
            psk=self._PSK_256,
            key_generator=self._KEY_GENERATOR,
            key_version=self._KEY_VERSION,
            key_bits=bits,
        )
        assert bits == 256
        assert len(key) == 32

    def test_derive_privacy_key_is_deterministic_for_gmac_modes(self) -> None:
        bits = mode_key_bits(PepMode.AES_128_GMAC_128)
        k1 = derive_privacy_key(
            psk=self._PSK_128, key_generator=self._KEY_GENERATOR,
            key_version=self._KEY_VERSION, key_bits=bits,
        )
        k2 = derive_privacy_key(
            psk=self._PSK_128, key_generator=self._KEY_GENERATOR,
            key_version=self._KEY_VERSION, key_bits=bits,
        )
        assert k1 == k2

    def test_derive_privacy_key_changes_with_key_version(self) -> None:
        # Sanity check: same PSK+KG with a rolled key_version yields a
        # different privacy_key (the SRT passphrase for a new KV period).
        bits = mode_key_bits(PepMode.AES_128_GMAC_128)
        k1 = derive_privacy_key(
            psk=self._PSK_128, key_generator=self._KEY_GENERATOR,
            key_version=b"\x00\x00\x00\x01", key_bits=bits,
        )
        k2 = derive_privacy_key(
            psk=self._PSK_128, key_generator=self._KEY_GENERATOR,
            key_version=b"\x00\x00\x00\x02", key_bits=bits,
        )
        assert k1 != k2
