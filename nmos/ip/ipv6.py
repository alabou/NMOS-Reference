# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""IPv6 address type — a 16-byte address representation.

Wraps Python's ipaddress.IPv6Address for parsing and standard classification,
adding a full API plus RFC 7371/4291 multicast analysis needed by IPMX
(scope, flags, prefix extraction, solicited-node multicast).

Public API:
    IPv6Addr.from_bytes(b"...")
    IPv6Addr.from_string("::1")
    IPv6Addr.from_ipv4_bytes(b"...")
    addr.is_multicast()
    addr.get_multicast_scope()
    addr.to_prefix()  # returns new obj
    make_solicited_node_multicast_addr(a)
"""

from __future__ import annotations

import ipaddress
from enum import IntEnum
from typing import overload

from nmos.errors import InvalidAddress, InvalidParameter


# ---------------------------------------------------------------------------
# Multicast constants
# ---------------------------------------------------------------------------

class MulticastScope(IntEnum):
    """IPv6 multicast scope values (low nibble of byte[1])."""
    LINK = 0x02
    ADMIN = 0x04
    SITE = 0x05
    ORGANIZATION = 0x08
    GLOBAL = 0x0E


class MulticastFlags(IntEnum):
    """IPv6 multicast flag bits (high nibble of byte[1]).

    As per RFC 7371 — the four flag bits in the second byte of a
    multicast address: 0bRPT0 where R=RendezVous, P=Prefix, T=Transient.
    """
    RESERVED = 0x80
    RENDEZ_VOUS = 0x40
    PREFIX = 0x20
    TRANSIENT = 0x10


# ---------------------------------------------------------------------------
# IPv6 address class
# ---------------------------------------------------------------------------

class IPv6Addr:
    """IPv6 address — 16-byte immutable wrapper around ipaddress.IPv6Address.

    Implements the Addr protocol defined in nmos.ip, so it can be used
    wherever ip.Addr is expected (structural typing via Protocol).
    """

    __slots__ = ("_addr", "_packed")

    def __init__(self, addr: ipaddress.IPv6Address) -> None:
        self._addr = addr
        # Cache packed bytes — used heavily for multicast bit inspection
        self._packed: bytes = addr.packed

    # --- Constructors ---

    @classmethod
    def from_bytes(cls, data: bytes | bytearray) -> IPv6Addr:
        """Create from a 16-byte sequence.

        Raises InvalidParameter if length is not 16.
        """
        if len(data) != 16:
            raise InvalidParameter(f"expecting 16 bytes, got {len(data)}")
        return cls(ipaddress.IPv6Address(bytes(data)))

    @classmethod
    def from_string(cls, s: str) -> IPv6Addr:
        """Parse an IPv6 address string.

        Raises InvalidParameter if the string is not a valid IPv6 address.
        """
        try:
            addr = ipaddress.IPv6Address(s)
        except (ipaddress.AddressValueError, ValueError) as exc:
            raise InvalidParameter(f"expecting an IPv6 address: {exc}") from exc
        return cls(addr)

    @classmethod
    def from_ipv4_bytes(cls, data: bytes | bytearray) -> IPv6Addr:
        """Create an IPv4-mapped IPv6 address (::ffff:a.b.c.d) from 4 bytes.

        Raises InvalidParameter if length is not 4.
        """
        if len(data) != 4:
            raise InvalidParameter(f"expecting 4 bytes, got {len(data)}")
        # Build ::ffff:a.b.c.d — bytes 10-11 are 0xff, bytes 12-15 are the IPv4
        buf = bytearray(16)
        buf[10] = 0xFF
        buf[11] = 0xFF
        buf[12] = data[0]
        buf[13] = data[1]
        buf[14] = data[2]
        buf[15] = data[3]
        return cls(ipaddress.IPv6Address(bytes(buf)))

    # --- Addr protocol: version checks ---

    def is_ipv4(self) -> bool:
        """Always False for IPv6 addresses."""
        return False

    def is_ipv6(self) -> bool:
        """Always True for IPv6 addresses."""
        return True

    # --- Addr protocol: classification ---

    def is_any(self) -> bool:
        """True if this is the unspecified address (::)."""
        return self._addr == _ANY_INNER

    def is_loopback(self) -> bool:
        """True if this is the loopback address (::1).

        Note: only ::1 exactly.
        """
        return self._addr == _LOOPBACK_INNER

    def is_multicast(self) -> bool:
        """True if in the multicast range (ff00::/8).

        Test: o[0] == 0xff.
        """
        return self._packed[0] == 0xFF

    def is_link_local(self) -> bool:
        """True if in the link-local range (fe80::/10).

        Test: o[0] == 0xfe and (o[1] & 0xc0) == 0x80.
        """
        return self._packed[0] == 0xFE and (self._packed[1] & 0xC0) == 0x80

    def is_unique_local(self) -> bool:
        """True if in the unique-local range (fc00::/7).

        Test: (o[0] & 0xfe) == 0xfc.
        """
        return (self._packed[0] & 0xFE) == 0xFC

    def is_global(self) -> bool:
        """True if in the global unicast range (2000::/3).

        Test: (o[0] & 0xe0) == 0x20.
        """
        return (self._packed[0] & 0xE0) == 0x20

    # --- Addr protocol: equality ---

    def is_equal(self, data: bytes | bytearray) -> bool:
        """Type-aware equality: only equal if data is exactly 16 bytes.

        An IPv6 address is never equal to a 4-byte IPv4 address, even if
        it is an IPv4-mapped IPv6 address.
        """
        if len(data) != 16:
            return False
        return self._packed == bytes(data)

    # --- Addr protocol: conversion ---

    def as_bytes(self) -> bytes:
        """Return the raw 16-byte representation."""
        return self._packed

    def __str__(self) -> str:
        """Compressed IPv6 string (e.g., "::1", "fe80::1").

        Uses the RFC 5952 compressed form via ipaddress.IPv6Address.
        """
        return str(self._addr)

    def __repr__(self) -> str:
        return f"IPv6Addr({self._addr!s})"

    # --- Byte-level access ---

    @overload
    def __getitem__(self, index: int) -> int: ...
    @overload
    def __getitem__(self, index: slice) -> bytes: ...

    def __getitem__(self, index: int | slice) -> int | bytes:
        """Indexed byte access: addr[0] returns the first byte."""
        return self._packed[index]

    def __len__(self) -> int:
        """Always 16 for IPv6 addresses."""
        return 16

    # --- Python equality and hashing ---

    def __eq__(self, other: object) -> bool:
        if isinstance(other, IPv6Addr):
            return self._addr == other._addr
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._addr)

    # --- IPv6-specific: prefix operations ---

    def is_prefix(self) -> bool:
        """True if the last 8 bytes are all zero (64-bit prefix).

        Only supports /64 prefix detection.
        """
        return self._packed[8:16] == _ZERO_SUFFIX

    def to_prefix(self) -> IPv6Addr:
        """Return a new address with the last 8 bytes zeroed (64-bit prefix).

        Returns a new immutable IPv6Addr rather than mutating in place,
        which is safer and more Pythonic.
        """
        buf = bytearray(self._packed)
        buf[8:16] = _ZERO_SUFFIX
        return IPv6Addr(ipaddress.IPv6Address(bytes(buf)))

    # --- IPv6-specific: solicited-node multicast ---

    def is_solicited_node_multicast(self) -> bool:
        """True if this is a solicited-node multicast address (ff02::1:ff00:0/104).

        Used in IPv6 Neighbor Discovery Protocol. The address is formed from
        ff02::1:ff plus the last 24 bits of the target address.
        """
        p = self._packed
        return (
            p[0] == 0xFF and p[1] == 0x02
            and p[2] == 0 and p[3] == 0
            and p[4] == 0 and p[5] == 0
            and p[6] == 0 and p[7] == 0
            and p[8] == 0 and p[9] == 0
            and p[10] == 0 and p[11] == 0x01
            and p[12] == 0xFF
        )

    # --- IPv6-specific: multicast scope and flags ---

    def get_multicast_scope(self) -> int:
        """Extract the 4-bit multicast scope from byte[1].

        Returns a value from MulticastScope (LINK, SITE, GLOBAL, etc.).
        Raises InvalidAddress if this is not a multicast address.
        """
        if not self.is_multicast():
            raise InvalidAddress("not a multicast address")
        return self._packed[1] & 0x0F

    def is_multicast_transient(self) -> bool:
        """True if the Transient (T) flag is set.

        A transient multicast address is dynamically assigned, as opposed to
        a well-known (permanently assigned) multicast address.
        Raises InvalidAddress if not a multicast address.
        """
        if not self.is_multicast():
            raise InvalidAddress("not a multicast address")
        return (self._packed[1] & MulticastFlags.TRANSIENT) != 0

    def is_multicast_prefixed(self) -> bool:
        """True if the Prefix (P) flag is set, with required T flag.

        As per RFC 7371: if P is set, T must also be set. If P is set
        without T, raises InvalidAddress (malformed address).
        Raises InvalidAddress if not multicast or flags are inconsistent.
        """
        if not self.is_multicast():
            raise InvalidAddress("not a multicast address")
        b = (self._packed[1] & MulticastFlags.PREFIX) != 0
        if b and (self._packed[1] & MulticastFlags.TRANSIENT) == 0:
            raise InvalidAddress("both P, T flags are required to be set")
        return b

    def is_multicast_rendez_vous(self) -> bool:
        """True if the RendezVous (R) flag is set, with required P and T flags.

        As per RFC 7371: if R is set, both P and T must also be set.
        Raises InvalidAddress if not multicast or flags are inconsistent.
        """
        if not self.is_multicast():
            raise InvalidAddress("not a multicast address")
        b = (self._packed[1] & MulticastFlags.RENDEZ_VOUS) != 0
        if b:
            if (self._packed[1] & MulticastFlags.PREFIX) == 0 or \
               (self._packed[1] & MulticastFlags.TRANSIENT) == 0:
                raise InvalidAddress(
                    "all R, P, T flags are required to be set"
                )
        return b

    def get_multicast_prefix(self) -> tuple[int, IPv6Addr]:
        """Extract the network prefix from a prefix-based multicast address.

        As per RFC 7371: byte[3] is the prefix length, bytes[4:12] are the
        64-bit network prefix.
        Returns (prefix_length, prefix_addr).
        Raises InvalidAddress if not a prefixed multicast address.
        """
        if not self.is_multicast_prefixed():
            raise InvalidAddress("invalid multicast address format")
        p = self._packed
        prefix_bytes = bytes([
            p[4], p[5], p[6], p[7],
            p[8], p[9], p[10], p[11],
            0, 0, 0, 0, 0, 0, 0, 0,
        ])
        return int(p[3]), IPv6Addr(ipaddress.IPv6Address(prefix_bytes))

    def get_multicast_group(self) -> tuple[int, IPv6Addr]:
        """Extract the group ID from a multicast address.

        As per RFC 7371 (prefixed) or RFC 4291 (non-prefixed):
        - Prefixed (P flag set): group is 32 bits in bytes[12:16]
        - Non-prefixed: group is 64 bits in bytes[8:16]
        Returns (group_length_bits, group_addr).
        Raises InvalidAddress if not a multicast address.
        """
        if not self.is_multicast():
            raise InvalidAddress("not a multicast address")
        p = self._packed

        # Check if prefixed — is_multicast_prefixed() may raise on malformed
        # flags, which is the correct behaviour
        if self.is_multicast_prefixed():
            group_bytes = bytes([
                0, 0, 0, 0, 0, 0, 0, 0,
                0, 0, 0, 0, p[12], p[13], p[14], p[15],
            ])
            return 32, IPv6Addr(ipaddress.IPv6Address(group_bytes))
        else:
            group_bytes = bytes([
                0, 0, 0, 0, 0, 0, 0, 0,
                p[8], p[9], p[10], p[11], p[12], p[13], p[14], p[15],
            ])
            return 64, IPv6Addr(ipaddress.IPv6Address(group_bytes))

    def get_multicast_rendez_vous_interface_id(self) -> int:
        """Extract the RendezVous Point interface ID.

        Returns the low nibble of byte[2], which identifies the RP interface.
        Raises InvalidAddress if not a rendez-vous multicast address.
        """
        if not self.is_multicast_rendez_vous():
            raise InvalidAddress("invalid multicast address")
        return self._packed[2] & 0x0F


# --- Internal constants ---
_ANY_INNER = ipaddress.IPv6Address(0)
_LOOPBACK_INNER = ipaddress.IPv6Address(1)
_ZERO_SUFFIX = b"\x00" * 8


# --- Well-known addresses (module-level constants) ---

ANY_ADDR = IPv6Addr(ipaddress.IPv6Address(0))
""":: — unspecified address."""

LOOPBACK_ADDR = IPv6Addr(ipaddress.IPv6Address(1))
"""::1 — loopback address."""

ALL_NODES_MULTICAST_ADDR = IPv6Addr(
    ipaddress.IPv6Address("ff02::1")
)
"""ff02::1 — all-nodes multicast (link-local scope)."""

ALL_ROUTERS_MULTICAST_ADDR = IPv6Addr(
    ipaddress.IPv6Address("ff02::2")
)
"""ff02::2 — all-routers multicast (link-local scope)."""

ALL_MLDV2_ROUTERS_MULTICAST_ADDR = IPv6Addr(
    ipaddress.IPv6Address("ff02::16")
)
"""ff02::16 — MLDv2 routers multicast (link-local scope)."""

LINK_LOCAL_PREFIX = IPv6Addr(
    ipaddress.IPv6Address("fe80::")
)
"""fe80:: — link-local prefix (/64)."""

IPV4_IN6_PREFIX = IPv6Addr(
    ipaddress.IPv6Address("::ffff:0:0")
)
"""::ffff:0:0 — IPv4-mapped IPv6 prefix."""


# --- Module-level functions ---

def make_solicited_node_multicast_addr(addr: IPv6Addr) -> IPv6Addr:
    """Create a solicited-node multicast address from the last 24 bits.

    Result: ff02::1:ffXX:XXXX where XX:XXXX are bytes[13:16] of addr.
    Used in IPv6 Neighbor Discovery Protocol.
    """
    p = addr.as_bytes()
    buf = bytes([
        0xFF, 0x02, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x01,
        0xFF, p[13], p[14], p[15],
    ])
    return IPv6Addr(ipaddress.IPv6Address(buf))
