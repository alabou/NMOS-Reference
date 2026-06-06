# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""IP address abstraction for the nmos.ip package.

Provides a version-agnostic Addr protocol and factory functions that dispatch
to IPv4Addr or IPv6Addr based on input. This allows most NMOS code to handle
addresses without caring about the IP version.

Public API:
    new_addr(b"...")
    new_addr_from_string("1.2.3.4")
    is_ipv4("1.2.3.4")
    is_ipv6("::1")
    are_equal(a, b)

Version-specific imports:
    from nmos.ip.ipv4 import IPv4Addr, LOOPBACK_ADDR
    from nmos.ip.ipv6 import IPv6Addr, MulticastScope
"""

from __future__ import annotations

import ipaddress
from typing import Protocol, Union, runtime_checkable

from nmos.errors import InvalidParameter
from nmos.ip.ipv4 import IPv4Addr
from nmos.ip.ipv6 import IPv6Addr


# ---------------------------------------------------------------------------
# Addr protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class Addr(Protocol):
    """Abstract IP address interface — structural typing for IPv4/IPv6.

    Both IPv4Addr and IPv6Addr satisfy this protocol without needing to
    inherit from it (structural subtyping via implicit interface satisfaction).

    Use runtime_checkable so isinstance(addr, Addr) works for defensive code.
    """

    def is_ipv4(self) -> bool: ...
    def is_ipv6(self) -> bool: ...
    def is_any(self) -> bool: ...
    def is_loopback(self) -> bool: ...
    def is_multicast(self) -> bool: ...
    def is_link_local(self) -> bool: ...
    def is_unique_local(self) -> bool: ...
    def is_global(self) -> bool: ...
    def is_equal(self, data: bytes | bytearray) -> bool: ...
    def as_bytes(self) -> bytes: ...
    def __str__(self) -> str: ...
    def __getitem__(self, index: int) -> int: ...
    def __len__(self) -> int: ...


# Re-export concrete types for convenience
# Usage: from nmos.ip import Addr, IPv4Addr, IPv6Addr
__all__ = [
    "Addr",
    "IPv4Addr",
    "IPv6Addr",
    "new_addr",
    "new_addr_from_string",
    "is_ipv4",
    "is_ipv6",
    "are_equal",
]


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------

def new_addr(data: bytes | bytearray) -> Union[IPv4Addr, IPv6Addr]:
    """Create an address from a byte sequence, dispatching by length.

    Dispatch rules:
    - 4 bytes  → IPv4Addr
    - 16 bytes → IPv6Addr
    - other    → raises InvalidParameter

    Why dispatch by length: this is unambiguous because IPv4 and IPv6
    have different fixed sizes (4 vs 16 bytes).
    """
    if len(data) == 4:
        return IPv4Addr.from_bytes(data)
    elif len(data) == 16:
        return IPv6Addr.from_bytes(data)
    else:
        raise InvalidParameter(
            f"expecting 4 (IPv4) or 16 (IPv6) bytes, got {len(data)}"
        )


def new_addr_from_string(s: str) -> Union[IPv4Addr, IPv6Addr]:
    """Parse an IP address string, returning IPv4Addr or IPv6Addr.

    Uses Python's ipaddress module for parsing, then wraps the result in
    our typed wrapper. Raises InvalidParameter if the string is not a
    valid IP address.
    """
    try:
        addr = ipaddress.ip_address(s)
    except ValueError as exc:
        raise InvalidParameter(
            f"expecting an IPv4, IPv6 or IPv4-mapped IPv6 address: {exc}"
        ) from exc

    if isinstance(addr, ipaddress.IPv4Address):
        return IPv4Addr(addr)
    else:
        return IPv6Addr(addr)


# ---------------------------------------------------------------------------
# String-based version detection
# ---------------------------------------------------------------------------

def is_ipv4(address: str) -> bool:
    """Quick heuristic: True if the string has fewer than 2 colons.

    Uses a colon count (< 2) as a lightweight check for protocol selection
    (tcp4 vs tcp6), NOT a full address validator.
    """
    return address.count(":") < 2


def is_ipv6(address: str) -> bool:
    """Quick heuristic: True if the string has 2 or more colons.

    Uses a colon count (>= 2) as a lightweight check for protocol
    selection, NOT a full validator.
    """
    return address.count(":") >= 2


# ---------------------------------------------------------------------------
# Equality
# ---------------------------------------------------------------------------

def are_equal(a: Addr, b: Addr) -> bool:
    """Compare two addresses for type-aware equality.

    Two addresses are equal only if they are the same version AND have the
    same bytes. An IPv4 address is never equal to an IPv4-mapped IPv6
    address.
    """
    return a.is_equal(b.as_bytes())
