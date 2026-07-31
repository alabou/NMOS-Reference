# Copyright (C) 2026 Alain Bouchard
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
IPMX Privacy Encryption Protocol (PEP) -- Key Derivation, Cipher, and
Protocol Adaptations.

Implements VSF TR-10-13 (2026-02-17) key derivation (Section 12), privacy
cipher (Section 15), RTP adaptation (Section 20), and VSF TR-10-14
(2024-09-24) USB adaptation (Section 12).

All AES operations use ``from Crypto.Cipher import AES`` (pycryptodome).
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import struct
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Tuple

from Crypto.Cipher import AES
from Crypto.Hash import CMAC


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class PepProtocol(Enum):
    """Privacy encryption protocol adaptation identifiers.

    RTP/RTP_KV: TR-10-13 Section 20 (RTP adaptation).
    USB/USB_KV: TR-10-14 Section 14 (USB over IP adaptation).
    UDP/UDP_KV, SRT, RTSP/RTSP_KV: Matrox "NMOS With Privacy Encryption" §"Protocol".
    NULL: privacy encryption disabled or not supported.

    Note: SRT has no _KV variant per the Matrox spec.
    """
    RTP     = "RTP"
    RTP_KV  = "RTP_KV"
    UDP     = "UDP"
    UDP_KV  = "UDP_KV"
    USB     = "USB"
    USB_KV  = "USB_KV"
    SRT     = "SRT"
    RTSP    = "RTSP"
    RTSP_KV = "RTSP_KV"
    NULL    = "NULL"


def protocol_is_kv(protocol: PepProtocol) -> bool:
    """True if the protocol supports dynamic in-band key_version rotation."""
    return protocol.value.endswith("_KV")


class PepMode(Enum):
    """Privacy encryption modes (TR-10-13 Sec 20, TR-10-14 Sec 12).

    Non-ECDH modes use PSK-only key derivation.
    ECDH modes require an ECDH shared secret (key_pfs) in addition to the PSK.
    """
    AES_128_CTR              = "AES-128-CTR"
    AES_256_CTR              = "AES-256-CTR"
    AES_128_CTR_CMAC_64      = "AES-128-CTR_CMAC-64"
    AES_256_CTR_CMAC_64      = "AES-256-CTR_CMAC-64"
    AES_128_CTR_CMAC_64_AAD  = "AES-128-CTR_CMAC-64-AAD"
    AES_256_CTR_CMAC_64_AAD  = "AES-256-CTR_CMAC-64-AAD"
    # GMAC-128 modes — SRT-only per "NMOS With Privacy Encryption.md":166.
    # The cipher itself is applied by the SRT library using the PEP-derived
    # privacy_key as the SRT passphrase (spec line 294); this module only
    # registers the mode and derives the key.
    AES_128_GMAC_128         = "AES-128-GMAC-128"
    AES_256_GMAC_128         = "AES-256-GMAC-128"
    # ECDH modes — require key_pfs from ECDH shared secret
    ECDH_AES_128_CTR              = "ECDH_AES-128-CTR"
    ECDH_AES_256_CTR              = "ECDH_AES-256-CTR"
    ECDH_AES_128_CTR_CMAC_64      = "ECDH_AES-128-CTR_CMAC-64"
    ECDH_AES_256_CTR_CMAC_64      = "ECDH_AES-256-CTR_CMAC-64"
    ECDH_AES_128_CTR_CMAC_64_AAD  = "ECDH_AES-128-CTR_CMAC-64-AAD"
    ECDH_AES_256_CTR_CMAC_64_AAD  = "ECDH_AES-256-CTR_CMAC-64-AAD"
    ECDH_AES_128_GMAC_128         = "ECDH_AES-128-GMAC-128"
    ECDH_AES_256_GMAC_128         = "ECDH_AES-256-GMAC-128"


_MODE_LOOKUP: dict[str, PepMode] = {m.value: m for m in PepMode}

AES_BLOCK_SIZE = 16
CMAC_TAG_SIZE = 8  # 64-bit truncated MAC


def parse_mode(mode_str: str) -> PepMode:
    """Resolve a mode string to a :class:`PepMode`."""
    m = _MODE_LOOKUP.get(mode_str)
    if m is None:
        raise ValueError(f"Unknown PEP mode: '{mode_str}'")
    return m


def mode_is_ecdh(mode: PepMode) -> bool:
    """True if the mode requires an ECDH shared secret (key_pfs)."""
    return mode.value.startswith("ECDH_")


def mode_key_bits(mode: PepMode) -> int:
    """Return the privacy_key (AES) size in bits for the given mode.

    Parses the `AES-<bits>-...` prefix (optionally preceded by `ECDH_`).
    A naive ``"128" in mode.value`` check would be wrong for modes like
    ``AES-256-GMAC-128`` where a secondary "128" appears in the MAC-tag
    portion but the AES key is 256 bits.
    """
    s = mode.value
    if s.startswith("ECDH_"):
        s = s[len("ECDH_"):]
    # All remaining mode strings start with "AES-<bits>-..."
    assert s.startswith("AES-"), f"unexpected PEP mode prefix: {mode.value}"
    bits_str = s[len("AES-"):].split("-", 1)[0]
    return int(bits_str)


def mode_has_cmac(mode: PepMode) -> bool:
    return "CMAC-64" in mode.value


def mode_has_gmac(mode: PepMode) -> bool:
    """True for SRT-only GMAC-128 modes.

    Per NMOS With Privacy Encryption.md:294 the AES-GCM / GMAC cipher is
    applied by the real SRT library using the PEP-derived ``privacy_key``
    as its passphrase; this module only derives the key.
    """
    return "GMAC-128" in mode.value


def mode_has_aad(mode: PepMode) -> bool:
    return mode.value.endswith("-AAD")


# ---------------------------------------------------------------------------
# Key Derivation (TR-10-13 Section 12)
# ---------------------------------------------------------------------------

_PREFIX_AB = b"\xAB"
_PREFIX_CD = b"\xCD"


def derive_privacy_key(
    psk: bytes,
    key_generator: bytes,
    key_version: bytes,
    key_pfs: bytes = b"",
    key_bits: int = 128,
    key_xcl: bytes = b"",
) -> bytes:
    """Derive a privacy_key per TR-10-13 Section 12 + Matrox Node Reservation.

    The KDF input message is constructed as:
        prefix || key_generator || key_version || key_pfs || key_xcl

    When ``key_xcl`` is empty (the default), the formula reduces to the
    standard TR-10-13 KDF, preserving backward compatibility with all
    existing test vectors.

    Args:
        psk:           Pre-Shared Key (16, 32, or 64 bytes).
        key_generator: 16-byte key generator.
        key_version:   4-byte key version.
        key_pfs:       ECDH shared secret (empty for non-ECDH modes).
        key_bits:      128 or 256 -- size of the derived key.
        key_xcl:       Node Reservation exclusive key (16 bytes when active,
                       empty when no exclusive session). Appended to the KDF
                       input per Matrox "NMOS With Privacy Encryption" §"Node
                       Reservation".

    Returns:
        The derived privacy_key (16 or 32 bytes).
    """
    psk_bits = len(psk) * 8
    if psk_bits not in (128, 256, 512):
        raise ValueError(f"PSK must be 128, 256, or 512 bits; got {psk_bits}")
    if len(key_generator) != 16:
        raise ValueError("key_generator must be 16 bytes")
    if len(key_version) != 4:
        raise ValueError("key_version must be 4 bytes")
    if key_bits not in (128, 256):
        raise ValueError(f"key_bits must be 128 or 256; got {key_bits}")
    if key_bits == 128 and psk_bits != 128:
        raise ValueError("128-bit key derivation requires a 128-bit PSK")

    if key_bits == 128:
        return _kdf_cmac(psk, _PREFIX_AB, key_generator, key_version, key_pfs, key_xcl)

    if psk_bits == 512:
        return _kdf_hmac_sha512_256(
            psk, _PREFIX_AB, key_generator, key_version, key_pfs, key_xcl
        )

    # 256-bit key with 128 or 256-bit PSK: two CMAC rounds
    # key_xcl is appended to BOTH halves (Matrox spec §"Node Reservation")
    pfs_high, pfs_low = _split_pfs(key_pfs)
    hi = _kdf_cmac(psk, _PREFIX_AB, key_generator, key_version, pfs_high, key_xcl)
    lo = _kdf_cmac(psk, _PREFIX_CD, key_generator, key_version, pfs_low, key_xcl)
    return hi + lo


def _kdf_cmac(
    psk: bytes,
    prefix: bytes,
    key_generator: bytes,
    key_version: bytes,
    key_pfs: bytes,
    key_xcl: bytes = b"",
) -> bytes:
    """One round of AES-CMAC-based KDF (NIST SP 800-108 counter mode)."""
    msg = prefix + key_generator + key_version + key_pfs + key_xcl
    mac = CMAC.new(psk, ciphermod=AES)
    mac.update(msg)
    return mac.digest()


def _kdf_hmac_sha512_256(
    psk: bytes,
    prefix: bytes,
    key_generator: bytes,
    key_version: bytes,
    key_pfs: bytes,
    key_xcl: bytes = b"",
) -> bytes:
    """HMAC-SHA-512/256-based KDF for 512-bit PSK (NIST SP 800-108)."""
    msg = prefix + key_generator + key_version + key_pfs + key_xcl
    return hmac.new(psk, msg, "sha512_256").digest()


def _split_pfs(key_pfs: bytes) -> Tuple[bytes, bytes]:
    """Split key_pfs into HIGH and LOW halves.  Empty stays empty."""
    if not key_pfs:
        return b"", b""
    half = len(key_pfs) // 2
    return key_pfs[:half], key_pfs[half:]


# ---------------------------------------------------------------------------
# Privacy Cipher (TR-10-13 Section 15)
# ---------------------------------------------------------------------------

class IvMode(Enum):
    """IV byte-order mode for debugging interoperability.

    SPEC (default): TR-10-13 compliant — iv is a big-endian integer,
        substreamid addition is big-endian, iv'_ctr = BE(iv') || BE(ctr).
    SWAP: Device-compat mode — iv bytes are reinterpreted as a native
        little-endian integer for the substreamid addition, then iv' is
        packed in little-endian into the counter block.  This matches the
        Matrox SecureEngine x86 implementation but is NOT spec-compliant.
    """
    SPEC = "spec"
    SWAP = "swap"


class KvMode(Enum):
    """R2S key_version selection strategy.

    RANDOM (default): TR-10-13 compliant — Receiver selects an initial
        random key_version at activation.
    S2R: Use the Sender's current key_version (from S2R messages).
        Workaround for devices whose InitPrivateKey() has side-effects
        when it sees a previously-unknown key_version.
    SDP: Use the key_version from the SDP transport file.
    """
    RANDOM = "random"
    S2R = "s2r"
    SDP = "sdp"


def compute_iv_prime(base_iv: int, substreamid: int = 0,
                     iv_mode: IvMode = IvMode.SPEC) -> int:
    """Compute iv' = (iv + substreamid) mod 2^64.

    TR-10-13 Section 14: the iv is a 64-bit Octet String in big-endian.
    The substreamid SHALL be added to the numeral value of that Octet String.

    When *iv_mode* is SWAP, the addition is performed on the little-endian
    (byte-reversed) interpretation of the IV bytes, matching certain x86
    device implementations that store the IV as a native integer.
    """
    if substreamid == 0:
        return base_iv
    if iv_mode == IvMode.SPEC:
        return (base_iv + substreamid) & 0xFFFFFFFFFFFFFFFF
    # SWAP mode: reinterpret big-endian bytes as native LE, add, swap back
    iv_bytes = base_iv.to_bytes(8, 'big')
    iv_native = int.from_bytes(iv_bytes, 'little')
    iv_native = (iv_native + substreamid) & 0xFFFFFFFFFFFFFFFF
    result_bytes = iv_native.to_bytes(8, 'little')
    return int.from_bytes(result_bytes, 'big')


def _build_iv_ctr(iv_prime: int, ctr: int,
                  iv_mode: IvMode = IvMode.SPEC) -> bytes:
    """Build the 128-bit AES-CTR counter block: iv'(64-bit) || ctr(64-bit).

    TR-10-13 Section 15: iv'_ctr is the concatenation iv' || ctr of two
    64-bit Octet Strings, each in big-endian.

    When *iv_mode* is SWAP, iv' is packed in little-endian matching the
    Matrox device behavior where the native integer is written directly
    to the counter block memory.
    """
    iv_pack = "<Q" if iv_mode == IvMode.SWAP else ">Q"
    return struct.pack(iv_pack, iv_prime & 0xFFFFFFFFFFFFFFFF) + \
           struct.pack(">Q", ctr & 0xFFFFFFFFFFFFFFFF)


def pep_encrypt(
    key: bytes,
    iv_prime: int,
    ctr: int,
    plaintext: bytes,
    iv_mode: IvMode = IvMode.SPEC,
) -> bytes:
    """AES-CTR encrypt arbitrary-length data per TR-10-13 Section 15.

    Args:
        key:       16 or 32 byte privacy_key.
        iv_prime:  64-bit effective iv'.
        ctr:       64-bit starting counter value.
        plaintext: Data to encrypt (any length).
        iv_mode:   SPEC (big-endian) or SWAP (device-compat little-endian).

    Returns:
        Ciphertext of the same length as plaintext.
    """
    if not plaintext:
        return b""
    initial_value = _build_iv_ctr(iv_prime, ctr, iv_mode)
    cipher = AES.new(key, AES.MODE_CTR, nonce=b"", initial_value=initial_value)
    return cipher.encrypt(plaintext)


def pep_decrypt(
    key: bytes,
    iv_prime: int,
    ctr: int,
    ciphertext: bytes,
    iv_mode: IvMode = IvMode.SPEC,
) -> bytes:
    """AES-CTR decrypt (symmetric with encrypt)."""
    return pep_encrypt(key, iv_prime, ctr, ciphertext, iv_mode)


def pep_encrypt_slices(
    key: bytes,
    iv_prime: int,
    ctr: int,
    plaintext: bytes,
    iv_mode: IvMode = IvMode.SPEC,
) -> Tuple[bytes, int]:
    """Encrypt and return (ciphertext, next_ctr).

    next_ctr is ctr + number_of_16byte_slices (ceiling division), which
    is the counter value to use for the next segment.
    """
    result = pep_encrypt(key, iv_prime, ctr, plaintext, iv_mode)
    num_slices = (len(plaintext) + AES_BLOCK_SIZE - 1) // AES_BLOCK_SIZE
    return result, ctr + num_slices


# ---------------------------------------------------------------------------
# CMAC-64 Authentication (TR-10-13 Sections 15, 20)
# ---------------------------------------------------------------------------

def pep_cmac64(key: bytes, data: bytes, aad: bytes = b"") -> bytes:
    """Compute 64-bit truncated AES-CMAC (most significant 8 bytes).

    For AAD modes the CMAC input is ``aad || data``.
    """
    mac = CMAC.new(key, ciphermod=AES)
    if aad:
        mac.update(aad)
    mac.update(data)
    return mac.digest()[:CMAC_TAG_SIZE]


def pep_cmac64_verify(
    key: bytes, data: bytes, expected_mac: bytes, aad: bytes = b""
) -> bool:
    """Verify a 64-bit truncated AES-CMAC tag."""
    computed = pep_cmac64(key, data, aad)
    return hmac.compare_digest(computed, expected_mac)


# ---------------------------------------------------------------------------
# USB Protocol Adaptation (TR-10-14 Section 12)
# ---------------------------------------------------------------------------

_USB_HEADER_SIZE = 16
_USB_MAC_SIZE = 8


def usb_ctr_advance(msg_length: int) -> int:
    """Return the number of AES-CTR blocks consumed by encrypting one USB message.

    AES-CTR encrypts the DATA + MAC portion of the message (everything
    after the 16-byte header).  Each 16-byte block consumes one counter
    value, so the next message must start its CTR at ``current_ctr +
    usb_ctr_advance(len(msg))``.
    """
    encrypted_len = msg_length - _USB_HEADER_SIZE
    if encrypted_len <= 0:
        return 0
    return (encrypted_len + AES_BLOCK_SIZE - 1) // AES_BLOCK_SIZE


def encrypt_usb_message(
    raw_msg: bytes,
    key: bytes,
    iv_prime: int,
    mode: PepMode,
    iv_mode: IvMode = IvMode.SPEC,
) -> bytes:
    """Encrypt an IPMX USB TCP message in-place per TR-10-14 Section 12.

    The raw_msg must already have CTR and KEYVERSION set in the header.
    The DATA and MAC portions are encrypted; the header is authenticated
    as AAD when the mode requires it.

    Scheme: mac-then-encrypt -- compute CMAC over plaintext (with optional
    AAD), append MAC, then encrypt DATA+MAC together.
    """
    if len(raw_msg) < _USB_HEADER_SIZE + _USB_MAC_SIZE:
        raise ValueError("Message too short for USB encryption")

    header = raw_msg[:_USB_HEADER_SIZE]
    ctr = struct.unpack_from(">Q", header, 0)[0]
    plain_data = raw_msg[_USB_HEADER_SIZE:-_USB_MAC_SIZE]

    aad = header if mode_has_aad(mode) else b""
    if mode_has_cmac(mode):
        tag = pep_cmac64(key, header + plain_data, aad=b"") \
            if not mode_has_aad(mode) \
            else pep_cmac64(key, plain_data, aad=aad)
    else:
        tag = b"\x00" * _USB_MAC_SIZE

    to_encrypt = plain_data + tag
    encrypted = pep_encrypt(key, iv_prime, ctr, to_encrypt, iv_mode)

    return header + encrypted


def decrypt_usb_message(
    enc_msg: bytes,
    key: bytes,
    iv_prime: int,
    mode: PepMode,
    iv_mode: IvMode = IvMode.SPEC,
) -> Tuple[bytes, bool]:
    """Decrypt an IPMX USB TCP message per TR-10-14 Section 12.

    Returns (decrypted_raw_msg, mac_ok).  mac_ok is True when no CMAC is
    used or when the CMAC verifies correctly.
    """
    if len(enc_msg) < _USB_HEADER_SIZE + _USB_MAC_SIZE:
        raise ValueError("Message too short for USB decryption")

    header = enc_msg[:_USB_HEADER_SIZE]
    ctr = struct.unpack_from(">Q", header, 0)[0]
    encrypted_payload = enc_msg[_USB_HEADER_SIZE:]

    decrypted = pep_decrypt(key, iv_prime, ctr, encrypted_payload, iv_mode)
    plain_data = decrypted[:-_USB_MAC_SIZE]
    recovered_mac = decrypted[-_USB_MAC_SIZE:]

    mac_ok = True
    if mode_has_cmac(mode):
        aad = header if mode_has_aad(mode) else b""
        if mode_has_aad(mode):
            mac_ok = pep_cmac64_verify(key, plain_data, recovered_mac, aad=aad)
        else:
            mac_ok = pep_cmac64_verify(
                key, header + plain_data, recovered_mac, aad=b""
            )

    rebuilt = header + plain_data + b"\x00" * _USB_MAC_SIZE
    return rebuilt, mac_ok


# ---------------------------------------------------------------------------
# RTP Protocol Adaptation (TR-10-13 Section 20)
# ---------------------------------------------------------------------------

def build_rtp_full_extension(
    ext_id: int,
    ctr: int,
    dynamic_key_version: int = 0,
) -> bytes:
    """Build a CTR Full RTP Extension Header (Table 3, TR-10-13).

    Layout (20 bytes / 5 x 32-bit words):
      Word 0:  0xBEDE | length=4
      Word 1:  ID(4) | L=14(4) | 0(1) | RESERVED(23)
      Word 2:  dynamic_key_version
      Word 3:  ctr_high (first 4 octets of ctr)
      Word 4:  ctr_low  (last 4 octets of ctr)
    """
    ctr_high = (ctr >> 32) & 0xFFFFFFFF
    ctr_low = ctr & 0xFFFFFFFF
    id_l = ((ext_id & 0x0F) << 4) | 14
    return struct.pack(">HHBBHIII",
                       0xBEDE, 4,       # word 0
                       id_l, 0, 0,      # word 1: ID|L, 0-bit, reserved
                       dynamic_key_version,
                       ctr_high,
                       ctr_low)


def parse_rtp_full_extension(
    data: bytes,
) -> Tuple[int, int, int]:
    """Parse a complete CTR Full RTP Extension Header (20 bytes).

    Returns:
        (dynamic_key_version, ctr_high, ctr_low)
    """
    if len(data) < 20:
        raise ValueError(
            f"Full RTP extension must be >= 20 bytes, got {len(data)}")
    dkv = struct.unpack_from(">I", data, 8)[0]
    ctr_high = struct.unpack_from(">I", data, 12)[0]
    ctr_low = struct.unpack_from(">I", data, 16)[0]
    return dkv, ctr_high, ctr_low


def recover_full_ctr(ctr_high: int, ctr_low: int) -> int:
    """Reconstruct the 64-bit ctr from Full RTP Extension fields."""
    return (ctr_high << 32) | ctr_low


def build_rtp_short_extension(ext_id: int, ctr: int) -> bytes:
    """Build a CTR Short RTP Extension Header (Table 4, TR-10-13).

    Returns 8 bytes: 0xBEDE | length=1 | ID|L=2 | ctr_short(3 bytes)
    """
    ctr_short = ctr & 0xFFFFFF
    id_l = ((ext_id & 0x0F) << 4) | 2  # L=2 means 3 bytes follow
    hdr = struct.pack(">HH", 0xBEDE, 1)
    body = bytes([id_l,
                  (ctr_short >> 16) & 0xFF,
                  (ctr_short >> 8) & 0xFF,
                  ctr_short & 0xFF])
    return hdr + body


def recover_short_ctr(prev_ctr: int, ctr_short: int) -> int:
    """Recover the full 64-bit ctr from a 24-bit ctr_short and previous ctr.

    Per TR-10-13 Section 20: if prev24 < new24, upper 40 bits unchanged;
    else upper 40 bits incremented by 1.
    """
    prev24 = prev_ctr & 0xFFFFFF
    new24 = ctr_short & 0xFFFFFF
    upper40 = prev_ctr >> 24
    if prev24 < new24:
        return (upper40 << 24) | new24
    else:
        return ((upper40 + 1) << 24) | new24


def build_rtp_aad_full(dynamic_key_version: int, ctr_high: int,
                       ctr_low: int, is_kv: bool) -> bytes:
    """Build the AAD for Full RTP Extension Header modes with AAD.

    RTP_KV: aad = 00000000 || dynamic_key_version || ctr_high || ctr_low
    RTP:    aad = 00000000 || 00000000 || ctr_high || ctr_low
    """
    dkv = dynamic_key_version if is_kv else 0
    return struct.pack(">IIII", 0, dkv, ctr_high, ctr_low)


def build_rtp_aad_short(ctr_short: int) -> bytes:
    """Build the AAD for Short RTP Extension Header modes with AAD.

    aad = 0000000000000000 || 0000000000 || ctr_short(3 bytes)
    """
    return b"\x00" * 8 + b"\x00" * 5 + struct.pack(">I", ctr_short & 0xFFFFFF)[1:]


def encrypt_rtp_payload(
    payload: bytes,
    key: bytes,
    iv_prime: int,
    ctr: int,
    mode: PepMode,
    aad: bytes = b"",
) -> Tuple[bytes, int]:
    """Encrypt an RTP payload per TR-10-13 Section 20.

    For CMAC modes: mac-then-encrypt.  The MAC is appended to the payload
    and encrypted together.

    Returns (encrypted_payload, next_ctr).  next_ctr is the counter after
    all slices.  For CMAC modes the returned payload is 8 bytes longer
    than the input (the encrypted MAC is appended).
    """
    if mode_has_cmac(mode):
        tag = pep_cmac64(key, payload, aad=aad)
        to_encrypt = payload + tag
    else:
        to_encrypt = payload

    encrypted, next_ctr = pep_encrypt_slices(key, iv_prime, ctr, to_encrypt)
    return encrypted, next_ctr


def decrypt_rtp_payload(
    encrypted_payload: bytes,
    key: bytes,
    iv_prime: int,
    ctr: int,
    mode: PepMode,
    aad: bytes = b"",
) -> Tuple[bytes, bool, int]:
    """Decrypt an RTP payload per TR-10-13 Section 20.

    Returns (plaintext, mac_ok, next_ctr).
    """
    decrypted, next_ctr = pep_encrypt_slices(
        key, iv_prime, ctr, encrypted_payload
    )

    mac_ok = True
    if mode_has_cmac(mode):
        plain_data = decrypted[:-CMAC_TAG_SIZE]
        recovered_mac = decrypted[-CMAC_TAG_SIZE:]
        mac_ok = pep_cmac64_verify(key, plain_data, recovered_mac, aad=aad)
        return plain_data, mac_ok, next_ctr

    return decrypted, mac_ok, next_ctr


# ---------------------------------------------------------------------------
# PEP Parameters
# ---------------------------------------------------------------------------

@dataclass
class PepParams:
    """All parameters needed for PEP key derivation and cipher operation."""
    protocol: PepProtocol = PepProtocol.NULL
    mode: PepMode = PepMode.AES_128_CTR
    iv: int = 0             # 64-bit base iv
    key_generator: bytes = field(default_factory=lambda: b"\x00" * 16)
    key_version: bytes = field(default_factory=lambda: b"\x00" * 4)
    key_id: bytes = field(default_factory=lambda: b"\x00" * 8)
    psk: bytes = b""
    key_pfs: bytes = b""    # ECDH shared secret (non-empty for ECDH modes)

    @property
    def key_bits(self) -> int:
        return mode_key_bits(self.mode)

    @property
    def is_kv(self) -> bool:
        """True when the protocol uses dynamic key versioning (USB_KV, RTP_KV)."""
        return self.protocol in (PepProtocol.USB_KV, PepProtocol.RTP_KV)

    @property
    def is_ecdh(self) -> bool:
        """True when the mode requires an ECDH shared secret."""
        return mode_is_ecdh(self.mode)

    def derive_key(self) -> bytes:
        """Derive the privacy_key from the stored parameters.

        For ECDH modes, key_pfs must be set (from ECDH shared secret).
        For non-ECDH modes, key_pfs is ignored (empty).
        """
        if not self.psk:
            raise ValueError("PSK is required for key derivation")
        if self.is_ecdh and not self.key_pfs:
            raise ValueError("ECDH mode requires key_pfs (shared secret)")
        return derive_privacy_key(
            self.psk, self.key_generator, self.key_version,
            key_pfs=self.key_pfs, key_bits=self.key_bits,
        )

    def derive_key_for_version(self, key_version_int: int) -> bytes:
        """Derive the privacy_key using an in-message KEYVERSION value.

        For ``_KV`` protocols (USB_KV, RTP_KV) the Sender increments the
        KEYVERSION at each TCP session, so the key must be re-derived
        with the actual value from the message header.
        """
        if not self.psk:
            raise ValueError("PSK is required for key derivation")
        kv_bytes = struct.pack(">I", key_version_int)
        return derive_privacy_key(
            self.psk, self.key_generator, kv_bytes,
            key_pfs=self.key_pfs, key_bits=self.key_bits,
        )

    @staticmethod
    def _parse_sdp(sdp_path: str) -> "tuple[Any, Any, Any]":
        """Parse an SDP file, returning (parser_obj, media, privacy_desc)."""
        from sdp.MatroxSdp import MatroxSdp as SdpParser

        parser = SdpParser()
        with open(sdp_path, "r", encoding="utf-8") as f:
            text = f.read()
        err = parser.decode(text)
        if err:
            raise ValueError(f"SDP parse error: {err}")

        pd = None
        media = None
        for i in range(parser.media_count):
            m = parser.medias[i]
            if m.privacy and m.privacy_desc.protocol is not None:
                pd = m.privacy_desc
                media = m
                break
        if pd is None and parser.privacy_desc.protocol is not None:
            pd = parser.privacy_desc
        return parser, media, pd

    @staticmethod
    def from_sdp(sdp_path: str, psk: bytes) -> "PepParams":
        """Load PEP parameters from an SDP file and a separately-provided PSK.

        Uses ``MatroxSdp.decode()`` to parse the file and extract the
        privacy descriptor from the first media section (or session level).
        """
        _, _, pd = PepParams._parse_sdp(sdp_path)
        if pd is None:
            raise ValueError("No privacy descriptor found in SDP")
        if pd.protocol is None or pd.mode is None:
            raise ValueError("SDP privacy descriptor missing protocol/mode")

        protocol_str = pd.protocol.s
        try:
            protocol = PepProtocol(protocol_str)
        except ValueError:
            raise ValueError(f"Unsupported protocol: {protocol_str}")

        mode = parse_mode(pd.mode.s)

        return PepParams(
            protocol=protocol,
            mode=mode,
            iv=int(pd.iv, 16) if pd.iv else 0,
            key_generator=bytes.fromhex(pd.key_generator),
            key_version=bytes.fromhex(pd.key_version),
            key_id=bytes.fromhex(pd.key_id) if pd.key_id else b"\x00" * 8,
            psk=psk,
        )

    @staticmethod
    def sdp_connection_info(sdp_path: str) -> Tuple[Optional[str], Optional[int]]:
        """Extract the Sender IP address and control port from an SDP file.

        Returns ``(ip, port)`` where either may be ``None`` if not present.
        """
        parser, media, _ = PepParams._parse_sdp(sdp_path)
        ip: Optional[str] = None
        port: Optional[int] = None
        if media is not None:
            if media.connection_address:
                ip = media.connection_address
            if media.port:
                port = int(media.port)
        if ip is None and parser.connection_address:
            ip = parser.connection_address
        return ip, port

    def write_sdp(self, sdp_path: str,
                  sender_ip: str = "0.0.0.0",
                  sender_port: int = 0) -> None:
        """Write a minimal USB SDP transport file with the privacy parameters.

        Uses ``MatroxSdpWrite.encode()`` to produce a valid SDP file
        that a receiver (OS side) can read to obtain the PEP parameters
        and the Sender's connection address/port.
        """
        from sdp.MatroxSdp import (
            MatroxSdp as SdpObj,
            MatroxSdpEnums as E,
            PrivacyDescriptor,
            EnumId,
        )
        from sdp import MatroxSdpWrite

        sdp = SdpObj()
        sdp.reset()
        sdp.username = "-"
        sdp.session_id = 1
        sdp.session_version = 1
        sdp.origin_address = sender_ip
        sdp.session_name = "IPMX USB"

        m = sdp.medias[0]
        m.media_name = "usb"
        m.type = E.Application.value
        m.protocol = EnumId("TCP")
        m.format_string = EnumId("usb")
        m.port = sender_port
        m.connection_address = sender_ip

        sdp.primary_media = m
        sdp.primary_media_name = m.media_name
        sdp.media_count = 1

        pd = PrivacyDescriptor()
        pd.protocol = EnumId(self.protocol.value)
        pd.mode = EnumId(self.mode.value)
        pd.iv = f"{self.iv:016x}"
        pd.key_generator = self.key_generator.hex()
        pd.key_version = self.key_version.hex()
        pd.key_id = self.key_id.hex()
        m.privacy = True
        m.privacy_desc = pd

        text = MatroxSdpWrite.encode(sdp)
        with open(sdp_path, "w", encoding="utf-8") as f:
            f.write(text)

    @staticmethod
    def from_cli(args: argparse.Namespace) -> "PepParams":
        """Build PepParams from parsed CLI arguments (see :func:`add_pep_args`)."""
        psk = b""
        if hasattr(args, "psk") and args.psk:
            psk = bytes.fromhex(args.psk)
        elif hasattr(args, "psk_file") and args.psk_file:
            with open(args.psk_file, "rb") as f:
                psk = f.read()

        if hasattr(args, "sdp") and args.sdp:
            return PepParams.from_sdp(args.sdp, psk)

        mode = parse_mode(args.pep_mode) if hasattr(args, "pep_mode") and args.pep_mode else PepMode.AES_128_CTR
        protocol_str = args.pep_protocol if hasattr(args, "pep_protocol") and args.pep_protocol else "USB_KV"
        try:
            protocol = PepProtocol(protocol_str)
        except ValueError:
            raise ValueError(f"Unsupported protocol: {protocol_str}")

        iv = int(args.pep_iv, 16) if hasattr(args, "pep_iv") and args.pep_iv else 0
        kg = bytes.fromhex(args.pep_key_generator) if hasattr(args, "pep_key_generator") and args.pep_key_generator else b"\x00" * 16
        kv = bytes.fromhex(args.pep_key_version) if hasattr(args, "pep_key_version") and args.pep_key_version else b"\x00" * 4
        kid = bytes.fromhex(args.pep_key_id) if hasattr(args, "pep_key_id") and args.pep_key_id else b"\x00" * 8

        return PepParams(
            protocol=protocol, mode=mode, iv=iv,
            key_generator=kg, key_version=kv, key_id=kid, psk=psk,
        )


def add_pep_args(parser: argparse.ArgumentParser) -> None:
    """Register PEP-related arguments on an existing ArgumentParser."""
    grp = parser.add_argument_group("PEP privacy encryption")
    grp.add_argument("--psk", type=str, default=None,
                     help="Pre-Shared Key as hex string")
    grp.add_argument("--psk-file", type=str, default=None,
                     help="Pre-Shared Key from binary file")
    grp.add_argument("--sdp", type=str, default=None,
                     help="SDP transport file for PEP parameters")
    grp.add_argument("--pep-protocol", type=str, default=None,
                     help="PEP protocol (RTP, RTP_KV, USB, USB_KV)")
    grp.add_argument("--pep-mode", type=str, default=None,
                     help="PEP mode (e.g. AES-128-CTR_CMAC-64-AAD)")
    grp.add_argument("--pep-iv", type=str, default=None,
                     help="Base IV as 16-char hex string")
    grp.add_argument("--pep-key-generator", type=str, default=None,
                     help="Key generator as 32-char hex string")
    grp.add_argument("--pep-key-version", type=str, default=None,
                     help="Key version as 8-char hex string")
    grp.add_argument("--pep-key-id", type=str, default=None,
                     help="Key ID as 16-char hex string")

    iv_grp = parser.add_argument_group(
        "IV byte-order debug",
        "Override the iv' computation and AES-CTR counter block layout "
        "per direction for interoperability debugging.  Default is spec-"
        "compliant (big-endian).  'swap' uses the byte-reversed LE "
        "interpretation matching certain x86 device implementations.")
    iv_grp.add_argument("--iv-s2r-swap0", action="store_true", default=False,
                        help="S2R substreamid=0: use SWAP iv mode")
    iv_grp.add_argument("--iv-r2s-swap0", action="store_true", default=False,
                        help="R2S substreamid=0: use SWAP iv mode (iv'=iv+0)")
    iv_grp.add_argument("--iv-r2s-swap1", action="store_true", default=False,
                        help="R2S substreamid=1: use SWAP iv mode (iv'=iv+1)")
    iv_grp.add_argument("--iv-r2s-spec0", action="store_true", default=False,
                        help="R2S: use SPEC iv mode with substreamid=0 "
                             "(instead of default substreamid=1)")
    iv_grp.add_argument("--iv-swapn", action="store_true", default=False,
                        help="Data channels: use SWAP iv mode for iv' = "
                             "swap(swap(iv) + substreamid).  Same byte-order "
                             "trick as --iv-s2r-swap0 / --iv-r2s-swap1 but "
                             "applied to the per-device data channel "
                             "substreamid (even for S2R, odd for R2S)")
    iv_grp.add_argument("--ctr-1", action="store_true", default=False,
                        help="Start CTR at 1 instead of 0 (workaround for "
                             "devices that reject CTR=0)")
    iv_grp.add_argument("--kv-s2r", action="store_true", default=False,
                        help="R2S key_version: echo the Sender's current "
                             "key_version (workaround for certain devices)")
    iv_grp.add_argument("--kv-sdp", action="store_true", default=False,
                        help="R2S key_version: use the SDP key_version "
                             "instead of a random one")


# ---------------------------------------------------------------------------
# Test vector validation
# ---------------------------------------------------------------------------

def run_test_vectors() -> bool:
    """Validate the KDF against all 10 test vectors from TR-10-13 Section 19.

    Returns True if all vectors pass.  Prints results to stdout.
    """
    vectors = _TR10_13_TEST_VECTORS
    all_ok = True
    for i, tv in enumerate(vectors, 1):
        psk = bytes.fromhex(tv["psk"])
        kg = bytes.fromhex(tv["key_generator"])
        kv = bytes.fromhex(tv["key_version"])
        pfs = bytes.fromhex(tv["key_pfs"]) if tv["key_pfs"] else b""
        expected = bytes.fromhex(tv["expected_key"])
        key_bits = len(expected) * 8

        derived = derive_privacy_key(psk, kg, kv, key_pfs=pfs,
                                     key_bits=key_bits)
        ok = derived == expected
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_ok = False
        print(f"  Vector {i:2d} ({tv['mode']:30s}): {status}"
              f"  PSK={len(psk)*8}b  key={key_bits}b"
              + ("" if ok else f"\n    expected: {expected.hex()}"
                               f"\n    got:      {derived.hex()}"))
    return all_ok


_TR10_13_TEST_VECTORS: list[dict] = [
    # Vector 1: ECDH_AES-128-CTR, Curve25519
    {
        "mode": "ECDH_AES-128-CTR",
        "psk": "000102030405060708090a0b0c0d0e0f",
        "key_generator": "2a4ab04bd61219d37a91abf6f94ab124",
        "key_version": "a7938740",
        "key_pfs": "218f8b81501ea437e0bc2c21a8e9af2be7bee3b1c553f9ccaaf40e3dc19374c6",
        "expected_key": "dee53f79ac29628644d01783b5b3c0b7",
    },
    # Vector 2: ECDH_AES-128-CTR, secp256r1
    {
        "mode": "ECDH_AES-128-CTR",
        "psk": "000102030405060708090a0b0c0d0e0f",
        "key_generator": "2edf9023a68fb83c5d1f018d7cd3783e",
        "key_version": "cc2301ed",
        "key_pfs": "dcf9d6b750d8c51419127f6e9ef9c91199bb99237d28e4054a6486f190b403d3",
        "expected_key": "12d376fa12f933780b1a68b9ebdb4187",
    },
    # Vector 3: ECDH_AES-128-CTR, secp521r1
    {
        "mode": "ECDH_AES-128-CTR",
        "psk": "000102030405060708090a0b0c0d0e0f",
        "key_generator": "a7ebcd7bef2b32abc008e1d0d0c777a0",
        "key_version": "5c436e9d",
        "key_pfs": "015df637be34bb2edc8f493d3cdbb4ba05371b894cf20adf899ad5a1cbbba4c26acaf1342b3766e5f686b00537d810372fb840b28c4a3587bba07cf12721cff37846",
        "expected_key": "56afadf373fccef80e70a755fe0a1588",
    },
    # Vector 4: ECDH_AES-256-CTR, Curve25519
    {
        "mode": "ECDH_AES-256-CTR",
        "psk": "000102030405060708090a0b0c0d0e0f",
        "key_generator": "a208336568863d5cf6ee704837340d79",
        "key_version": "84f03939",
        "key_pfs": "79a44729b1f4d9f52a4e210a5b4e776de4f511837798b88beafd5aaa41eb0700",
        "expected_key": "f78d42babb85119405b13bb1199a80bdd5557cc64a596d97abe9bf945079d81a",
    },
    # Vector 5: ECDH_AES-256-CTR, secp256r1
    {
        "mode": "ECDH_AES-256-CTR",
        "psk": "000102030405060708090a0b0c0d0e0f",
        "key_generator": "51fa624b4c62a2125e45424c2f185cb9",
        "key_version": "2b7a8223",
        "key_pfs": "3e1e0e9836bd01b38a9f18fac02da9d5a545f1ca8149f076917d6f3e3a8b94eb",
        "expected_key": "a3ba0f316f10fb6866bbeb3d6841b346505a1c1f5ec3e36c626721637c0c5aaa",
    },
    # Vector 6: ECDH_AES-256-CTR, secp521r1
    {
        "mode": "ECDH_AES-256-CTR",
        "psk": "000102030405060708090a0b0c0d0e0f",
        "key_generator": "8623b4b1e6fa7067be1f5952ad6299b8",
        "key_version": "2af1988d",
        "key_pfs": "00c25350af2ccf296cd60e055b8d70c66a40db98eccb179103c0208700df96ba41d144abd1875128824a659ae133e394ace2d3e898d95f8f895e96e3a4593a570cf4",
        "expected_key": "3b99a7d6eca76f53600084aec2ce920c5a73391b650b95fc285d00b6286e28d9",
    },
    # Vector 7: AES-128-CTR, no ECDH, 128-bit PSK
    {
        "mode": "AES-128-CTR",
        "psk": "000102030405060708090a0b0c0d0e0f",
        "key_generator": "52bbbea2b2cdc7ddbb18c23becd3c753",
        "key_version": "007c84b5",
        "key_pfs": "",
        "expected_key": "650132d60b2700cd2aa3e25f24aa8980",
    },
    # Vector 8: AES-256-CTR, no ECDH, 128-bit PSK
    {
        "mode": "AES-256-CTR",
        "psk": "000102030405060708090a0b0c0d0e0f",
        "key_generator": "52bbbea2b2cdc7ddbb18c23becd3c753",
        "key_version": "007c84b5",
        "key_pfs": "",
        "expected_key": "650132d60b2700cd2aa3e25f24aa8980cafd1d993e2e2a36640b7795579c089a",
    },
    # Vector 9: AES-256-CTR, no ECDH, 256-bit PSK
    {
        "mode": "AES-256-CTR",
        "psk": "000102030405060708090a0b0c0d0e0f000102030405060708090a0b0c0d0e0f",
        "key_generator": "f99067d1f5f72363d3b0e009ab34c36b",
        "key_version": "7251c65d",
        "key_pfs": "",
        "expected_key": "e9ceff8c8aa6aa6680c1928a5427fb71351ce3c9c507c92a9fba3bcbd65681f3",
    },
    # Vector 10: AES-256-CTR, no ECDH, 512-bit PSK
    {
        "mode": "AES-256-CTR",
        "psk": "000102030405060708090a0b0c0d0e0f000102030405060708090a0b0c0d0e0f"
               "000102030405060708090a0b0c0d0e0f000102030405060708090a0b0c0d0e0f",
        "key_generator": "1927a9d6914eb5579edd30712a081f84",
        "key_version": "c5f4a28d",
        "key_pfs": "",
        "expected_key": "2e4edd15087fa6d4fef2f5c16ee0d474fec93823c12099a47d00bd5cd54d87e6",
    },
]
