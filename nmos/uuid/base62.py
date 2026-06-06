# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Base-62 encode/decode for serial number packing.

Alphabet: 0-9 (positions 0-9), A-Z (10-35), a-z (36-61).
Case-sensitive — 'A' and 'a' are distinct values.

Used by ResourceUuid to pack up to 10 leading serial number characters
into a 60-bit integer. Uses bijective encoding so the string length is
recoverable from the integer value alone — no separate length field needed.

Bijective encoding:
    ""     → 0
    "0"    → 1       (offset(1) + 0)
    "z"    → 62      (offset(1) + 61)
    "00"   → 63      (offset(2) + 0)
    "zz"   → 3906    (offset(2) + 62^2 - 1)
    ...up to 10 chars fits in 60 bits (max value ≈ 8.53e17 < 2^60 = 1.15e18)
"""

from __future__ import annotations

from nmos.errors import InvalidParameter

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
BASE = 62

# Reverse lookup: char → position (0-61)
_CHAR_TO_POS: dict[str, int] = {c: i for i, c in enumerate(ALPHABET)}

# Maximum encoded chars in the 60-bit field
MAX_ENCODED_CHARS = 10

# Maximum total serial number length (encoded + 2 clear)
MAX_SERIAL_LENGTH = 12

# Precomputed offsets: _OFFSETS[n] = start of range for n-char strings
# offset(0) = 0 (empty string)
# offset(n) = 1 + 62 + 62^2 + ... + 62^(n-1) = (62^n - 1) / 61  for n >= 1
_OFFSETS: list[int] = [0]
_acc = 0
for _i in range(1, MAX_ENCODED_CHARS + 2):
    _acc += BASE ** (_i - 1)
    _OFFSETS.append(_acc)


def encode(s: str) -> int:
    """Bijective base-62 encode: string → integer with embedded length.

    Empty string encodes to 0. For non-empty strings of length N, the
    value is offset(N) + positional_base62_value(s), guaranteeing that
    strings of different lengths map to non-overlapping integer ranges.

    This means the string length can be recovered from the integer alone,
    so "0" and "00" produce different values.

    Max 10 chars, all alphanumeric. Fits in 60 bits.
    """
    if len(s) > MAX_ENCODED_CHARS:
        raise InvalidParameter(
            f"encoded portion exceeds {MAX_ENCODED_CHARS} chars, got {len(s)}"
        )
    if not s:
        return 0

    # Positional base-62 value (big-endian: first char = most significant)
    positional = 0
    for ch in s:
        pos = _CHAR_TO_POS.get(ch)
        if pos is None:
            raise InvalidParameter(
                f"character {ch!r} is not alphanumeric (base-62)"
            )
        positional = positional * BASE + pos

    return _OFFSETS[len(s)] + positional


def decode(value: int) -> str:
    """Bijective base-62 decode: integer → string with recovered length.

    Inverse of encode(). Returns the original string including its exact
    length (leading '0' characters are preserved).
    """
    if value == 0:
        return ""

    # Find the length N such that offset(N) <= value < offset(N+1)
    length = 0
    for n in range(1, MAX_ENCODED_CHARS + 1):
        if value < _OFFSETS[n + 1]:
            length = n
            break
    else:
        raise InvalidParameter(f"encoded value {value} exceeds max range")

    positional = value - _OFFSETS[length]

    # Convert positional value to N-digit base-62 string
    chars: list[str] = []
    for _ in range(length):
        positional, remainder = divmod(positional, BASE)
        chars.append(ALPHABET[remainder])
    chars.reverse()
    return "".join(chars)


def char_to_clear(ch: str) -> int:
    """Encode a single char as ``ord(ch) - ord('0')`` for the sn_clear field.

    Digits 0-9 map to 0x00-0x09 (readable directly in hex).
    Uppercase letters: 'A'→0x11, 'B'→0x12, ..., 'Z'→0x2a.
    Lowercase letters: 'a'→0x31, 'b'→0x32, ..., 'z'→0x4a.
    """
    return ord(ch) - 0x30


def clear_to_char(value: int) -> str:
    """Decode a sn_clear byte back to the original character."""
    return chr(value + 0x30)


def is_valid_serial(s: str) -> bool:
    """Check if string is a valid serial number (1-12 alphanumeric chars)."""
    if not s or len(s) > MAX_SERIAL_LENGTH:
        return False
    return all(c in _CHAR_TO_POS for c in s)
