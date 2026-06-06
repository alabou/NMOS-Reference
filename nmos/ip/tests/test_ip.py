# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for nmos.ip — IP address abstraction (ip, ipv4, ipv6)."""

from __future__ import annotations

import pytest

from nmos.errors import InvalidAddress, InvalidParameter
from nmos.ip import (
    Addr,
    IPv4Addr,
    IPv6Addr,
    are_equal,
    is_ipv4,
    is_ipv6,
    new_addr,
    new_addr_from_string,
)
from nmos.ip import ipv4, ipv6


# ===================================================================
# IPv4Addr
# ===================================================================


class TestIPv4Construction:
    """IPv4Addr construction from bytes and strings."""

    def test_from_bytes(self) -> None:
        addr = IPv4Addr.from_bytes(b"\xc0\xa8\x01\x01")
        assert str(addr) == "192.168.1.1"
        assert addr.as_bytes() == b"\xc0\xa8\x01\x01"

    def test_from_string(self) -> None:
        addr = IPv4Addr.from_string("10.0.0.1")
        assert str(addr) == "10.0.0.1"
        assert addr.as_bytes() == b"\x0a\x00\x00\x01"

    def test_from_bytes_wrong_length(self) -> None:
        with pytest.raises(InvalidParameter, match="expecting 4 bytes"):
            IPv4Addr.from_bytes(b"\x00" * 16)

    def test_from_string_invalid(self) -> None:
        with pytest.raises(InvalidParameter, match="expecting an IPv4"):
            IPv4Addr.from_string("not-an-ip")

    def test_from_string_rejects_ipv6(self) -> None:
        with pytest.raises(InvalidParameter):
            IPv4Addr.from_string("::1")


class TestIPv4Classification:
    """IPv4Addr address classification methods."""

    def test_is_ipv4_ipv6(self) -> None:
        addr = IPv4Addr.from_string("1.2.3.4")
        assert addr.is_ipv4() is True
        assert addr.is_ipv6() is False

    def test_is_any(self) -> None:
        assert ipv4.ANY_ADDR.is_any() is True
        assert IPv4Addr.from_string("1.2.3.4").is_any() is False

    def test_is_loopback(self) -> None:
        assert ipv4.LOOPBACK_ADDR.is_loopback() is True
        assert IPv4Addr.from_string("127.0.0.1").is_loopback() is True
        assert IPv4Addr.from_string("1.2.3.4").is_loopback() is False

    def test_is_multicast(self) -> None:
        assert IPv4Addr.from_string("224.0.0.1").is_multicast() is True
        assert IPv4Addr.from_string("239.255.255.255").is_multicast() is True
        assert IPv4Addr.from_string("223.255.255.255").is_multicast() is False
        assert IPv4Addr.from_string("240.0.0.0").is_multicast() is False

    def test_is_link_local(self) -> None:
        assert IPv4Addr.from_string("169.254.1.1").is_link_local() is True
        assert IPv4Addr.from_string("169.253.1.1").is_link_local() is False

    def test_is_unique_local(self) -> None:
        # RFC 1918 ranges
        assert IPv4Addr.from_string("10.0.0.1").is_unique_local() is True
        assert IPv4Addr.from_string("10.255.255.255").is_unique_local() is True
        assert IPv4Addr.from_string("172.16.0.1").is_unique_local() is True
        assert IPv4Addr.from_string("172.31.255.255").is_unique_local() is True
        assert IPv4Addr.from_string("192.168.0.1").is_unique_local() is True
        assert IPv4Addr.from_string("192.168.255.255").is_unique_local() is True
        # Not private
        assert IPv4Addr.from_string("8.8.8.8").is_unique_local() is False
        assert IPv4Addr.from_string("172.32.0.1").is_unique_local() is False

    def test_is_global(self) -> None:
        assert IPv4Addr.from_string("8.8.8.8").is_global() is True
        assert IPv4Addr.from_string("10.0.0.1").is_global() is False


class TestIPv4Equality:
    """IPv4Addr equality and is_equal semantics."""

    def test_python_eq(self) -> None:
        a = IPv4Addr.from_string("1.2.3.4")
        b = IPv4Addr.from_string("1.2.3.4")
        assert a == b
        assert hash(a) == hash(b)

    def test_python_ne(self) -> None:
        a = IPv4Addr.from_string("1.2.3.4")
        b = IPv4Addr.from_string("5.6.7.8")
        assert a != b

    def test_is_equal_same_bytes(self) -> None:
        addr = IPv4Addr.from_string("1.2.3.4")
        assert addr.is_equal(b"\x01\x02\x03\x04") is True

    def test_is_equal_wrong_length(self) -> None:
        """IPv4 is never equal to 16-byte data (even IPv4-mapped IPv6)."""
        addr = IPv4Addr.from_string("1.2.3.4")
        assert addr.is_equal(b"\x00" * 16) is False


class TestIPv4ByteAccess:
    """IPv4Addr byte-level indexing."""

    def test_getitem(self) -> None:
        addr = IPv4Addr.from_string("192.168.1.1")
        assert addr[0] == 192
        assert addr[1] == 168
        assert addr[2] == 1
        assert addr[3] == 1

    def test_len(self) -> None:
        assert len(IPv4Addr.from_string("1.2.3.4")) == 4

    def test_slice(self) -> None:
        addr = IPv4Addr.from_string("192.168.1.1")
        assert addr[0:2] == b"\xc0\xa8"


class TestIPv4WellKnown:
    """Well-known IPv4 address constants."""

    def test_any(self) -> None:
        assert str(ipv4.ANY_ADDR) == "0.0.0.0"
        assert ipv4.ANY_ADDR.as_bytes() == b"\x00\x00\x00\x00"

    def test_loopback(self) -> None:
        assert str(ipv4.LOOPBACK_ADDR) == "127.0.0.1"

    def test_broadcast(self) -> None:
        assert str(ipv4.BROADCAST_ADDR) == "255.255.255.255"

    def test_all_nodes_multicast(self) -> None:
        assert str(ipv4.ALL_NODES_MULTICAST_ADDR) == "224.0.0.1"

    def test_all_routers_multicast(self) -> None:
        assert str(ipv4.ALL_ROUTERS_MULTICAST_ADDR) == "224.0.0.2"


# ===================================================================
# IPv6Addr
# ===================================================================


class TestIPv6Construction:
    """IPv6Addr construction from bytes, strings, and IPv4 bytes."""

    def test_from_bytes(self) -> None:
        data = bytes([0xFE, 0x80] + [0] * 6 + [0] * 6 + [0, 1])
        addr = IPv6Addr.from_bytes(data)
        assert str(addr) == "fe80::1"

    def test_from_string(self) -> None:
        addr = IPv6Addr.from_string("::1")
        assert str(addr) == "::1"
        assert addr.as_bytes() == b"\x00" * 15 + b"\x01"

    def test_from_ipv4_bytes(self) -> None:
        """Creates IPv4-mapped IPv6 (::ffff:1.2.3.4)."""
        addr = IPv6Addr.from_ipv4_bytes(b"\x01\x02\x03\x04")
        assert str(addr) == "::ffff:1.2.3.4" or str(addr) == "::ffff:102:304"
        expected = b"\x00" * 10 + b"\xff\xff\x01\x02\x03\x04"
        assert addr.as_bytes() == expected

    def test_from_bytes_wrong_length(self) -> None:
        with pytest.raises(InvalidParameter, match="expecting 16 bytes"):
            IPv6Addr.from_bytes(b"\x00" * 4)

    def test_from_string_invalid(self) -> None:
        with pytest.raises(InvalidParameter, match="expecting an IPv6"):
            IPv6Addr.from_string("not-an-ip")

    def test_from_ipv4_bytes_wrong_length(self) -> None:
        with pytest.raises(InvalidParameter, match="expecting 4 bytes"):
            IPv6Addr.from_ipv4_bytes(b"\x00" * 16)


class TestIPv6Classification:
    """IPv6Addr address classification methods."""

    def test_is_ipv4_ipv6(self) -> None:
        addr = IPv6Addr.from_string("::1")
        assert addr.is_ipv4() is False
        assert addr.is_ipv6() is True

    def test_is_any(self) -> None:
        assert ipv6.ANY_ADDR.is_any() is True
        assert IPv6Addr.from_string("::1").is_any() is False

    def test_is_loopback(self) -> None:
        assert ipv6.LOOPBACK_ADDR.is_loopback() is True
        assert IPv6Addr.from_string("::1").is_loopback() is True
        assert IPv6Addr.from_string("::2").is_loopback() is False

    def test_is_multicast(self) -> None:
        assert IPv6Addr.from_string("ff02::1").is_multicast() is True
        assert IPv6Addr.from_string("fe80::1").is_multicast() is False

    def test_is_link_local(self) -> None:
        assert IPv6Addr.from_string("fe80::1").is_link_local() is True
        assert IPv6Addr.from_string("fe80::abcd").is_link_local() is True
        assert IPv6Addr.from_string("fec0::1").is_link_local() is False

    def test_is_unique_local(self) -> None:
        assert IPv6Addr.from_string("fc00::1").is_unique_local() is True
        assert IPv6Addr.from_string("fd00::1").is_unique_local() is True
        assert IPv6Addr.from_string("fe00::1").is_unique_local() is False

    def test_is_global(self) -> None:
        assert IPv6Addr.from_string("2001:db8::1").is_global() is True
        assert IPv6Addr.from_string("3fff::1").is_global() is True
        assert IPv6Addr.from_string("fe80::1").is_global() is False
        assert IPv6Addr.from_string("fc00::1").is_global() is False


class TestIPv6Equality:
    """IPv6Addr equality and is_equal semantics."""

    def test_python_eq(self) -> None:
        a = IPv6Addr.from_string("fe80::1")
        b = IPv6Addr.from_string("fe80::1")
        assert a == b
        assert hash(a) == hash(b)

    def test_python_ne(self) -> None:
        a = IPv6Addr.from_string("fe80::1")
        b = IPv6Addr.from_string("fe80::2")
        assert a != b

    def test_is_equal_same_bytes(self) -> None:
        addr = IPv6Addr.from_string("::1")
        assert addr.is_equal(b"\x00" * 15 + b"\x01") is True

    def test_is_equal_wrong_length(self) -> None:
        """IPv6 is never equal to 4-byte data."""
        addr = IPv6Addr.from_string("::1")
        assert addr.is_equal(b"\x00" * 4) is False


class TestIPv6ByteAccess:
    """IPv6Addr byte-level indexing."""

    def test_getitem(self) -> None:
        addr = IPv6Addr.from_string("ff02::1")
        assert addr[0] == 0xFF
        assert addr[1] == 0x02
        assert addr[15] == 0x01

    def test_len(self) -> None:
        assert len(IPv6Addr.from_string("::1")) == 16


class TestIPv6WellKnown:
    """Well-known IPv6 address constants."""

    def test_any(self) -> None:
        assert str(ipv6.ANY_ADDR) == "::"
        assert ipv6.ANY_ADDR.as_bytes() == b"\x00" * 16

    def test_loopback(self) -> None:
        assert str(ipv6.LOOPBACK_ADDR) == "::1"

    def test_all_nodes_multicast(self) -> None:
        assert str(ipv6.ALL_NODES_MULTICAST_ADDR) == "ff02::1"

    def test_all_routers_multicast(self) -> None:
        assert str(ipv6.ALL_ROUTERS_MULTICAST_ADDR) == "ff02::2"

    def test_all_mldv2_routers(self) -> None:
        assert str(ipv6.ALL_MLDV2_ROUTERS_MULTICAST_ADDR) == "ff02::16"

    def test_link_local_prefix(self) -> None:
        assert str(ipv6.LINK_LOCAL_PREFIX) == "fe80::"
        assert ipv6.LINK_LOCAL_PREFIX.is_prefix() is True

    def test_ipv4_in6_prefix(self) -> None:
        assert str(ipv6.IPV4_IN6_PREFIX) == "::ffff:0:0"


# ===================================================================
# IPv6 Prefix operations
# ===================================================================


class TestIPv6Prefix:
    """IPv6Addr prefix detection and extraction."""

    def test_is_prefix_true(self) -> None:
        addr = IPv6Addr.from_string("2001:db8::")
        assert addr.is_prefix() is True

    def test_is_prefix_false(self) -> None:
        addr = IPv6Addr.from_string("2001:db8::1")
        assert addr.is_prefix() is False

    def test_to_prefix(self) -> None:
        addr = IPv6Addr.from_string("2001:db8::abcd:1234")
        prefix = addr.to_prefix()
        assert str(prefix) == "2001:db8::"
        assert prefix.is_prefix() is True
        # Original is unchanged (immutable)
        assert str(addr) == "2001:db8::abcd:1234"


# ===================================================================
# IPv6 Multicast
# ===================================================================


class TestIPv6MulticastScope:
    """IPv6 multicast scope extraction."""

    def test_link_local_scope(self) -> None:
        addr = IPv6Addr.from_string("ff02::1")
        assert addr.get_multicast_scope() == ipv6.MulticastScope.LINK

    def test_site_scope(self) -> None:
        addr = IPv6Addr.from_string("ff05::1")
        assert addr.get_multicast_scope() == ipv6.MulticastScope.SITE

    def test_global_scope(self) -> None:
        addr = IPv6Addr.from_string("ff0e::1")
        assert addr.get_multicast_scope() == ipv6.MulticastScope.GLOBAL

    def test_not_multicast_raises(self) -> None:
        addr = IPv6Addr.from_string("fe80::1")
        with pytest.raises(InvalidAddress, match="not a multicast"):
            addr.get_multicast_scope()


class TestIPv6MulticastFlags:
    """IPv6 multicast flag checking (T, P, R)."""

    def test_well_known_not_transient(self) -> None:
        """ff02::1 has flags=0 — not transient."""
        addr = IPv6Addr.from_string("ff02::1")
        assert addr.is_multicast_transient() is False

    def test_transient(self) -> None:
        """ff12::1 has T flag set (0x10 in high nibble of byte[1])."""
        addr = IPv6Addr.from_string("ff12::1")
        assert addr.is_multicast_transient() is True

    def test_prefixed_requires_transient(self) -> None:
        """P flag without T flag is invalid per RFC 7371."""
        # byte[1] = 0x22 → P=1, T=0 — invalid
        data = bytearray(16)
        data[0] = 0xFF
        data[1] = 0x22  # scope=2, flags: P=1 T=0
        addr = IPv6Addr.from_bytes(bytes(data))
        with pytest.raises(InvalidAddress, match="P, T flags"):
            addr.is_multicast_prefixed()

    def test_prefixed_valid(self) -> None:
        """P and T both set — valid prefixed multicast."""
        # byte[1] = 0x32 → P=1, T=1
        data = bytearray(16)
        data[0] = 0xFF
        data[1] = 0x32  # scope=2, flags: P=1 T=1
        data[3] = 64    # prefix length
        addr = IPv6Addr.from_bytes(bytes(data))
        assert addr.is_multicast_prefixed() is True

    def test_rendez_vous_requires_p_and_t(self) -> None:
        """R flag without P and T is invalid."""
        # byte[1] = 0x42 → R=1, P=0, T=0 — invalid
        data = bytearray(16)
        data[0] = 0xFF
        data[1] = 0x42
        addr = IPv6Addr.from_bytes(bytes(data))
        with pytest.raises(InvalidAddress, match="R, P, T flags"):
            addr.is_multicast_rendez_vous()

    def test_rendez_vous_valid(self) -> None:
        """R, P, T all set — valid rendez-vous multicast."""
        # byte[1] = 0x72 → R=1, P=1, T=1
        data = bytearray(16)
        data[0] = 0xFF
        data[1] = 0x72  # scope=2, flags: R=1 P=1 T=1
        data[2] = 0x03  # RP interface ID in low nibble
        data[3] = 64    # prefix length
        addr = IPv6Addr.from_bytes(bytes(data))
        assert addr.is_multicast_rendez_vous() is True
        assert addr.get_multicast_rendez_vous_interface_id() == 3

    def test_not_multicast_raises(self) -> None:
        addr = IPv6Addr.from_string("2001:db8::1")
        with pytest.raises(InvalidAddress):
            addr.is_multicast_transient()
        with pytest.raises(InvalidAddress):
            addr.is_multicast_prefixed()
        with pytest.raises(InvalidAddress):
            addr.is_multicast_rendez_vous()


class TestIPv6MulticastPrefixGroup:
    """IPv6 multicast prefix and group extraction (RFC 7371)."""

    def test_get_multicast_prefix(self) -> None:
        """Prefixed multicast: extract the 64-bit network prefix."""
        # Build: ff32:0040:2001:0db8:0000:0000:0000:0001
        # flags: P=1,T=1 scope=2, prefix_len=64, prefix=2001:db8::
        data = bytearray(16)
        data[0] = 0xFF
        data[1] = 0x32  # P=1, T=1, scope=LINK
        data[3] = 64    # prefix length
        # prefix in bytes 4-11
        data[4] = 0x20
        data[5] = 0x01
        data[6] = 0x0D
        data[7] = 0xB8
        # group in bytes 12-15
        data[12] = 0x00
        data[13] = 0x00
        data[14] = 0x00
        data[15] = 0x01
        addr = IPv6Addr.from_bytes(bytes(data))

        prefix_len, prefix = addr.get_multicast_prefix()
        assert prefix_len == 64
        assert str(prefix) == "2001:db8::"

    def test_get_multicast_group_prefixed(self) -> None:
        """Prefixed multicast: group is 32 bits in bytes[12:16]."""
        data = bytearray(16)
        data[0] = 0xFF
        data[1] = 0x32
        data[3] = 64
        data[12] = 0xAB
        data[13] = 0xCD
        data[14] = 0xEF
        data[15] = 0x01
        addr = IPv6Addr.from_bytes(bytes(data))

        group_len, group = addr.get_multicast_group()
        assert group_len == 32
        assert group[12] == 0xAB
        assert group[15] == 0x01

    def test_get_multicast_group_non_prefixed(self) -> None:
        """Non-prefixed multicast: group is 64 bits in bytes[8:16]."""
        addr = IPv6Addr.from_string("ff02::1")
        group_len, group = addr.get_multicast_group()
        assert group_len == 64
        assert group[15] == 0x01


class TestIPv6SolicitedNode:
    """IPv6 solicited-node multicast address construction and detection."""

    def test_make_solicited_node(self) -> None:
        addr = IPv6Addr.from_string("fe80::1234:5678")
        snma = ipv6.make_solicited_node_multicast_addr(addr)
        # Result: ff02::1:ff34:5678 — last 24 bits of original
        assert str(snma) == "ff02::1:ff34:5678"
        assert snma.is_solicited_node_multicast() is True

    def test_is_solicited_node_false(self) -> None:
        addr = IPv6Addr.from_string("ff02::1")
        assert addr.is_solicited_node_multicast() is False

    def test_roundtrip(self) -> None:
        """Solicited-node from any address preserves last 24 bits."""
        addr = IPv6Addr.from_string("2001:db8::aabb:ccdd")
        snma = ipv6.make_solicited_node_multicast_addr(addr)
        assert snma[13] == addr[13]
        assert snma[14] == addr[14]
        assert snma[15] == addr[15]


# ===================================================================
# Abstract ip module — factory functions and heuristics
# ===================================================================


class TestFactory:
    """ip.new_addr and ip.new_addr_from_string dispatch."""

    def test_new_addr_4_bytes(self) -> None:
        addr = new_addr(b"\x0a\x00\x00\x01")
        assert isinstance(addr, IPv4Addr)
        assert str(addr) == "10.0.0.1"

    def test_new_addr_16_bytes(self) -> None:
        data = b"\x00" * 15 + b"\x01"
        addr = new_addr(data)
        assert isinstance(addr, IPv6Addr)
        assert str(addr) == "::1"

    def test_new_addr_wrong_length(self) -> None:
        with pytest.raises(InvalidParameter, match="4 .* or 16"):
            new_addr(b"\x00" * 8)

    def test_new_addr_from_string_ipv4(self) -> None:
        addr = new_addr_from_string("192.168.1.1")
        assert isinstance(addr, IPv4Addr)

    def test_new_addr_from_string_ipv6(self) -> None:
        addr = new_addr_from_string("::1")
        assert isinstance(addr, IPv6Addr)

    def test_new_addr_from_string_invalid(self) -> None:
        with pytest.raises(InvalidParameter):
            new_addr_from_string("not-an-ip")


class TestHeuristics:
    """ip.is_ipv4 and ip.is_ipv6 string heuristics."""

    def test_ipv4_detection(self) -> None:
        assert is_ipv4("192.168.1.1") is True
        assert is_ipv4("10.0.0.1") is True

    def test_ipv6_detection(self) -> None:
        assert is_ipv6("::1") is True
        assert is_ipv6("fe80::1") is True
        assert is_ipv6("2001:db8::1") is True

    def test_mutual_exclusion(self) -> None:
        """For any valid IP string, exactly one heuristic returns True."""
        for s in ("1.2.3.4", "10.0.0.1", "255.255.255.255"):
            assert is_ipv4(s) is True
            assert is_ipv6(s) is False
        for s in ("::1", "fe80::1", "ff02::1"):
            assert is_ipv4(s) is False
            assert is_ipv6(s) is True


class TestAreEqual:
    """ip.are_equal cross-version comparison."""

    def test_same_ipv4(self) -> None:
        a = IPv4Addr.from_string("1.2.3.4")
        b = IPv4Addr.from_string("1.2.3.4")
        assert are_equal(a, b) is True

    def test_different_ipv4(self) -> None:
        a = IPv4Addr.from_string("1.2.3.4")
        b = IPv4Addr.from_string("5.6.7.8")
        assert are_equal(a, b) is False

    def test_same_ipv6(self) -> None:
        a = IPv6Addr.from_string("fe80::1")
        b = IPv6Addr.from_string("fe80::1")
        assert are_equal(a, b) is True

    def test_cross_version_never_equal(self) -> None:
        """IPv4 1.2.3.4 is never equal to IPv6, even IPv4-mapped."""
        v4 = IPv4Addr.from_string("1.2.3.4")
        v6_mapped = IPv6Addr.from_ipv4_bytes(b"\x01\x02\x03\x04")
        assert are_equal(v4, v6_mapped) is False
        assert are_equal(v6_mapped, v4) is False


class TestAddrProtocol:
    """Verify IPv4Addr and IPv6Addr satisfy the Addr Protocol."""

    def test_ipv4_is_addr(self) -> None:
        addr = IPv4Addr.from_string("1.2.3.4")
        assert isinstance(addr, Addr)

    def test_ipv6_is_addr(self) -> None:
        addr = IPv6Addr.from_string("::1")
        assert isinstance(addr, Addr)
