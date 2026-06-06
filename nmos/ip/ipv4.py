# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""IPv4 address type — a 4-byte address wrapper.

Wraps Python's ipaddress.IPv4Address for parsing and standard classification,
adding the API surface used throughout the NMOS codebase.

    API:
    IPv4Addr.from_bytes(b"...")
    IPv4Addr.from_string("1.2.3.4")
    addr.is_multicast()
    addr.as_bytes()
    str(addr)
    addr[i]

Well-known addresses are module-level constants (e.g., ANY_ADDR, LOOPBACK_ADDR).
"""

from __future__ import annotations

import ipaddress
from typing import overload

from nmos.errors import InvalidParameter


class IPv4Addr:
    """IPv4 address — 4-byte immutable wrapper around ipaddress.IPv4Address.

    Implements the Addr protocol defined in nmos.ip, so it can be used
    wherever ip.Addr is expected (structural typing via Protocol).
    """

    __slots__ = ("_addr",)

    def __init__(self, addr: ipaddress.IPv4Address) -> None:
        self._addr = addr

    # --- Constructors ---

    @classmethod
    def from_bytes(cls, data: bytes | bytearray) -> IPv4Addr:
        """Create from a 4-byte sequence.

        Raises InvalidParameter if length is not 4.
        """
        if len(data) != 4:
            raise InvalidParameter(f"expecting 4 bytes, got {len(data)}")
        return cls(ipaddress.IPv4Address(bytes(data)))

    @classmethod
    def from_string(cls, s: str) -> IPv4Addr:
        """Parse an IPv4 address string.

        Raises InvalidParameter if the string is not a valid IPv4 address.
        """
        try:
            addr = ipaddress.IPv4Address(s)
        except (ipaddress.AddressValueError, ValueError) as exc:
            raise InvalidParameter(f"expecting an IPv4 address: {exc}") from exc
        return cls(addr)

    # --- Addr protocol: version checks ---

    def is_ipv4(self) -> bool:
        """Always True for IPv4 addresses."""
        return True

    def is_ipv6(self) -> bool:
        """Always False for IPv4 addresses."""
        return False

    # --- Addr protocol: classification ---

    def is_any(self) -> bool:
        """True if this is the unspecified address (0.0.0.0)."""
        return self._addr == _ANY_INNER

    def is_loopback(self) -> bool:
        """True if this is the loopback address (127.0.0.1).

        Note: only 127.0.0.1 exactly, not the full 127.0.0.0/8 range that
        Python's is_loopback covers.
        """
        return self._addr == _LOOPBACK_INNER

    def is_multicast(self) -> bool:
        """True if in the multicast range (224.0.0.0 – 239.255.255.255).

        Equivalent to the bit test: (o[0] & 0xf0) == 0xe0.
        """
        return self._addr.is_multicast

    def is_link_local(self) -> bool:
        """True if in the link-local range (169.254.0.0/16).

        Equivalent to the test: o[0] == 169 && o[1] == 254.
        """
        return self._addr.is_link_local

    def is_unique_local(self) -> bool:
        """True if in an RFC 1918 private range.

        Tests 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16.
        """
        # Python's is_private covers exactly RFC 1918 for IPv4 in Python 3.12+
        packed = self._addr.packed
        return (
            packed[0] == 10
            or (packed[0] == 192 and packed[1] == 168)
            or (packed[0] == 172 and (packed[1] & 0xF0) == 0x10)
        )

    def is_global(self) -> bool:
        """True if not in a private (RFC 1918) range.

        Equivalent to: !private (same logic as is_unique_local negated).
        """
        return not self.is_unique_local()

    # --- Addr protocol: equality ---

    def is_equal(self, data: bytes | bytearray) -> bool:
        """Type-aware equality: only equal if data is exactly 4 bytes.

        An IPv4 address is never equal to a 16-byte IPv4-mapped IPv6
        address, even if they represent the same host.
        """
        if len(data) != 4:
            return False
        return self._addr.packed == bytes(data)

    # --- Addr protocol: conversion ---

    def as_bytes(self) -> bytes:
        """Return the raw 4-byte representation."""
        return self._addr.packed

    def __str__(self) -> str:
        """Dotted-decimal string (e.g., "192.168.1.1")."""
        return str(self._addr)

    def __repr__(self) -> str:
        return f"IPv4Addr({self._addr!s})"

    # --- Byte-level access ---

    @overload
    def __getitem__(self, index: int) -> int: ...
    @overload
    def __getitem__(self, index: slice) -> bytes: ...

    def __getitem__(self, index: int | slice) -> int | bytes:
        """Indexed byte access: addr[0] returns the first octet.

        Enables byte-level address construction patterns:
            packed = self._addr.packed
            # mcastAddr[0] = 239  →  build via bytearray + from_bytes
        """
        return self._addr.packed[index]

    def __len__(self) -> int:
        """Always 4 for IPv4 addresses."""
        return 4

    # --- Python equality and hashing ---

    def __eq__(self, other: object) -> bool:
        if isinstance(other, IPv4Addr):
            return self._addr == other._addr
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._addr)


# --- Pre-built inner addresses for fast comparison ---
_ANY_INNER = ipaddress.IPv4Address(0)
_LOOPBACK_INNER = ipaddress.IPv4Address("127.0.0.1")


# --- Well-known addresses (module-level constants) ---

ANY_ADDR = IPv4Addr(ipaddress.IPv4Address(0))
"""0.0.0.0 — unspecified address."""

LOOPBACK_ADDR = IPv4Addr(ipaddress.IPv4Address("127.0.0.1"))
"""127.0.0.1 — loopback address."""

BROADCAST_ADDR = IPv4Addr(ipaddress.IPv4Address("255.255.255.255"))
"""255.255.255.255 — broadcast address."""

ALL_NODES_MULTICAST_ADDR = IPv4Addr(ipaddress.IPv4Address("224.0.0.1"))
"""224.0.0.1 — all-nodes multicast (local segment)."""

ALL_ROUTERS_MULTICAST_ADDR = IPv4Addr(ipaddress.IPv4Address("224.0.0.2"))
"""224.0.0.2 — all-routers multicast (local segment)."""
