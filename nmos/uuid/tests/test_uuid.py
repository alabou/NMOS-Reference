# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for nmos.uuid — ResourceUuid with base-62 serial numbers."""

from __future__ import annotations

import re

import pytest

from nmos.errors import InvalidData, InvalidParameter
from nmos.uuid import (
    ResourceSubType,
    ResourceType,
    ResourceUuid,
    update_resource_unique_id,
)
from nmos.uuid.base62 import (
    char_to_clear,
    clear_to_char,
    decode,
    encode,
    is_valid_serial,
)

_NMOS_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}"
    r"-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


# ===================================================================
# Base-62 bijective encoding
# ===================================================================


class TestBase62Encode:
    """Bijective base-62 encode."""

    def test_empty(self) -> None:
        assert encode("") == 0

    def test_single_zero(self) -> None:
        """'0' is position 0, but bijective offset makes it 1."""
        assert encode("0") == 1

    def test_single_z(self) -> None:
        """'z' is position 61, bijective = 1 + 61 = 62."""
        assert encode("z") == 62

    def test_two_zeros(self) -> None:
        """'00' must differ from '0'."""
        assert encode("00") == 63  # offset(2) + 0

    def test_preserves_length(self) -> None:
        """Different lengths always produce different values."""
        assert encode("0") != encode("00")
        assert encode("A") != encode("0A")
        assert encode("AB") != encode("0AB")

    def test_max_10_chars(self) -> None:
        """10-char string should encode without error."""
        val = encode("ABCDEFGHIJ")
        assert val > 0

    def test_exceeds_max_raises(self) -> None:
        with pytest.raises(InvalidParameter, match="exceeds"):
            encode("ABCDEFGHIJK")  # 11 chars

    def test_invalid_char_raises(self) -> None:
        with pytest.raises(InvalidParameter, match="not alphanumeric"):
            encode("AB-CD")


class TestBase62Decode:
    """Bijective base-62 decode."""

    def test_zero_is_empty(self) -> None:
        assert decode(0) == ""

    def test_roundtrip_single(self) -> None:
        for ch in "09AZaz":
            assert decode(encode(ch)) == ch

    def test_roundtrip_multi(self) -> None:
        cases = ["AB", "0A", "00", "ABC123", "zzzzzzzzzz", "SNX12345"]
        for s in cases:
            assert decode(encode(s)) == s, f"roundtrip failed for {s!r}"

    def test_roundtrip_all_lengths(self) -> None:
        """Verify roundtrip for strings of every possible length."""
        for length in range(0, 11):
            s = "A" * length
            assert decode(encode(s)) == s


class TestCharClear:
    """char-'0' encoding for sn_clear bytes."""

    def test_digits(self) -> None:
        for d in "0123456789":
            assert char_to_clear(d) == ord(d) - 0x30
            assert clear_to_char(char_to_clear(d)) == d

    def test_digit_values(self) -> None:
        """Digits 0-9 map to 0x00-0x09 for direct hex readability."""
        assert char_to_clear("0") == 0x00
        assert char_to_clear("5") == 0x05
        assert char_to_clear("9") == 0x09

    def test_uppercase(self) -> None:
        assert char_to_clear("A") == 0x11
        assert char_to_clear("Z") == 0x2A
        assert clear_to_char(0x11) == "A"

    def test_lowercase(self) -> None:
        assert char_to_clear("a") == 0x31
        assert char_to_clear("z") == 0x4A
        assert clear_to_char(0x31) == "a"

    def test_roundtrip(self) -> None:
        for ch in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz":
            assert clear_to_char(char_to_clear(ch)) == ch


class TestIsValidSerial:
    """Serial number validation."""

    def test_valid(self) -> None:
        assert is_valid_serial("A") is True
        assert is_valid_serial("ABC12345") is True
        assert is_valid_serial("012345678901") is True  # 12 chars

    def test_empty(self) -> None:
        assert is_valid_serial("") is False

    def test_too_long(self) -> None:
        assert is_valid_serial("A" * 13) is False

    def test_invalid_chars(self) -> None:
        assert is_valid_serial("AB-CD") is False
        assert is_valid_serial("AB CD") is False


# ===================================================================
# ResourceUuid construction
# ===================================================================


class TestResourceUuidSet:
    """ResourceUuid.set() with various parameters."""

    def test_basic(self) -> None:
        u = ResourceUuid()
        u.set(ResourceType.DEVICE, ResourceSubType.NONE, 10, "SNX12345", 0, False)
        assert u.resource_type == ResourceType.DEVICE
        assert u.resource_sub_type == ResourceSubType.NONE
        assert u.index == 10
        assert u.serial_number == "SNX12345"
        assert u.on_demand is False

    def test_all_resource_types(self) -> None:
        for rt in ResourceType:
            u = ResourceUuid()
            u.set(rt, ResourceSubType.NONE, 0, "SN1", 0, False)
            assert u.resource_type == rt

    def test_on_demand(self) -> None:
        u = ResourceUuid()
        u.set(ResourceType.SENDER, ResourceSubType.NONE, 0, "SN1", 0, True)
        assert u.is_on_demand() is True

    def test_invalid_serial_raises(self) -> None:
        u = ResourceUuid()
        with pytest.raises(InvalidParameter, match="serial"):
            u.set(ResourceType.NODE, ResourceSubType.NONE, 0, "", 0, False)

    def test_invalid_index_raises(self) -> None:
        u = ResourceUuid()
        with pytest.raises(InvalidParameter, match="index"):
            u.set(ResourceType.NODE, ResourceSubType.NONE, 256, "SN1", 0, False)


# ===================================================================
# NMOS UUID format compliance
# ===================================================================


class TestNmosCompliance:
    """Every generated UUID must match the NMOS regex."""

    def _make(
        self,
        rt: ResourceType = ResourceType.NODE,
        rst: ResourceSubType = ResourceSubType.NONE,
        idx: int = 0,
        sn: str = "SN1",
        uid: int = 0,
        od: bool = False,
    ) -> str:
        u = ResourceUuid()
        u.set(rt, rst, idx, sn, uid, od)
        return str(u)

    def test_basic(self) -> None:
        s = self._make()
        assert _NMOS_UUID_RE.match(s), f"UUID {s} doesn't match NMOS regex"

    def test_all_resource_types(self) -> None:
        for rt in ResourceType:
            s = self._make(rt=rt)
            assert _NMOS_UUID_RE.match(s), f"type={rt.name}: {s}"

    def test_on_demand(self) -> None:
        s = self._make(od=True)
        assert _NMOS_UUID_RE.match(s), f"on_demand: {s}"

    def test_various_serials(self) -> None:
        serials = ["A", "AB", "ABC", "SNX12345", "ABCDEFGHIJKL", "0123456789"]
        for sn in serials:
            s = self._make(sn=sn)
            assert _NMOS_UUID_RE.match(s), f"sn={sn!r}: {s}"

    def test_max_unique_id(self) -> None:
        s = self._make(uid=0xFFFFFFFF)
        assert _NMOS_UUID_RE.match(s), f"max uid: {s}"

    def test_max_index(self) -> None:
        s = self._make(idx=255)
        assert _NMOS_UUID_RE.match(s), f"max index: {s}"


# ===================================================================
# Roundtrip: set → str → set_from_string
# ===================================================================


class TestRoundtrip:
    """set() → __str__() → set_from_string() must recover all fields."""

    def _roundtrip(
        self,
        rt: ResourceType,
        rst: ResourceSubType,
        idx: int,
        sn: str,
        uid: int,
        od: bool,
    ) -> None:
        a = ResourceUuid()
        a.set(rt, rst, idx, sn, uid, od)
        uuid_str = str(a)

        b = ResourceUuid()
        b.set_from_string(uuid_str)

        assert b.resource_type == rt, f"resource_type: {b.resource_type} != {rt}"
        assert b.resource_sub_type == rst, f"sub_type: {b.resource_sub_type} != {rst}"
        assert b.index == idx, f"index: {b.index} != {idx}"
        assert b.serial_number == sn, f"serial: {b.serial_number!r} != {sn!r}"
        assert b.unique_id == uid, f"uid: {b.unique_id} != {uid}"
        assert b.on_demand == od, f"on_demand: {b.on_demand} != {od}"

    def test_minimal(self) -> None:
        self._roundtrip(ResourceType.NODE, ResourceSubType.NONE, 0, "A", 0, False)

    def test_2_char_sn(self) -> None:
        self._roundtrip(ResourceType.DEVICE, ResourceSubType.NONE, 5, "AB", 100, False)

    def test_3_char_sn(self) -> None:
        self._roundtrip(ResourceType.SENDER, ResourceSubType.NONE, 0, "ABC", 0, False)

    def test_matrox_7_char(self) -> None:
        self._roundtrip(ResourceType.SENDER, ResourceSubType.NONE, 3, "A123456", 0x12345678, False)

    def test_matrox_8_char(self) -> None:
        self._roundtrip(ResourceType.FLOW, ResourceSubType.NONE, 0, "SNX12345", 0xABCDEF01, False)

    def test_12_char_max(self) -> None:
        self._roundtrip(ResourceType.SOURCE, ResourceSubType.SENDER_MONITOR, 255, "ABCDEFGHIJ12", 0xFFFFFFFF, True)

    def test_all_digits(self) -> None:
        self._roundtrip(ResourceType.RECEIVER, ResourceSubType.NONE, 0, "0123456789", 42, False)

    def test_mixed_case(self) -> None:
        self._roundtrip(ResourceType.NODE, ResourceSubType.NONE, 0, "AbCdEfGh", 0, False)

    def test_leading_zeros(self) -> None:
        """S/N starting with '0' must be preserved."""
        self._roundtrip(ResourceType.NODE, ResourceSubType.NONE, 0, "00AB", 0, False)

    def test_all_resource_types_roundtrip(self) -> None:
        for rt in ResourceType:
            self._roundtrip(rt, ResourceSubType.NONE, 0, "SN1", 0, False)

    def test_all_sub_types_roundtrip(self) -> None:
        for rst in ResourceSubType:
            self._roundtrip(ResourceType.SOURCE, rst, 0, "SN1", 0, False)

    def test_on_demand_roundtrip(self) -> None:
        self._roundtrip(ResourceType.SENDER, ResourceSubType.NONE, 0, "SN1", 0, True)
        self._roundtrip(ResourceType.SENDER, ResourceSubType.NONE, 0, "SN1", 0, False)


# ===================================================================
# Visual identification: last 4 hex chars
# ===================================================================


class TestVisualIdentification:
    """Last 4 hex chars of UUID show last 2 S/N chars as char-'0'."""

    def test_digits_at_end(self) -> None:
        """S/N 'SNX12345' → last 2 chars '4','5' → 04,05 → hex '0405'."""
        u = ResourceUuid()
        u.set(ResourceType.DEVICE, ResourceSubType.NONE, 0, "SNX12345", 0, False)
        uuid_str = str(u)
        assert uuid_str.endswith("0405"), f"expected ...0405, got {uuid_str}"

    def test_letters_at_end(self) -> None:
        """S/N 'ABC' → last 2 chars 'B','C' → 12,13 → hex '1213'."""
        u = ResourceUuid()
        u.set(ResourceType.NODE, ResourceSubType.NONE, 0, "ABC", 0, False)
        uuid_str = str(u)
        assert uuid_str.endswith("1213"), f"expected ...1213, got {uuid_str}"

    def test_single_char_sn(self) -> None:
        """S/N '5' → clear_hi=0x00, clear_lo=0x05 → hex '0005'."""
        u = ResourceUuid()
        u.set(ResourceType.NODE, ResourceSubType.NONE, 0, "5", 0, False)
        uuid_str = str(u)
        assert uuid_str.endswith("0005"), f"expected ...0005, got {uuid_str}"

    def test_two_char_sn(self) -> None:
        """S/N 'Az' → 'A'→0x11, 'z'→0x4a → hex '114a'."""
        u = ResourceUuid()
        u.set(ResourceType.NODE, ResourceSubType.NONE, 0, "Az", 0, False)
        uuid_str = str(u)
        assert uuid_str.endswith("114a"), f"expected ...114a, got {uuid_str}"


# ===================================================================
# Group 2 readability: first hex digit = type (+ on-demand)
# ===================================================================


class TestGroup2Readability:
    """First hex digit of group 2 directly shows resource type."""

    def test_type_digit_no_od(self) -> None:
        """Without on-demand, first digit = resource type value."""
        for rt in ResourceType:
            u = ResourceUuid()
            u.set(rt, ResourceSubType.NONE, 0, "SN1", 0, False)
            uuid_str = str(u)
            # Group 2 starts at position 9 (after 8 hex + dash)
            first_nibble = uuid_str[9]
            assert first_nibble == f"{rt.value:x}", (
                f"type={rt.name}: expected '{rt.value:x}', got '{first_nibble}' in {uuid_str}"
            )

    def test_type_digit_with_od(self) -> None:
        """With on-demand, first digit = resource type + 8."""
        for rt in ResourceType:
            u = ResourceUuid()
            u.set(rt, ResourceSubType.NONE, 0, "SN1", 0, True)
            uuid_str = str(u)
            first_nibble = uuid_str[9]
            expected = f"{rt.value + 8:x}"
            assert first_nibble == expected, (
                f"type={rt.name}/OD: expected '{expected}', got '{first_nibble}' in {uuid_str}"
            )

    def test_index_readable(self) -> None:
        """Index 0x0a (10) should appear as '0a' in hex chars 2-3 of group 2."""
        u = ResourceUuid()
        u.set(ResourceType.DEVICE, ResourceSubType.NONE, 0x0A, "SN1", 0, False)
        uuid_str = str(u)
        # Group 2 is chars 9-12 (after dash)
        index_hex = uuid_str[10:12]
        assert index_hex == "0a", f"expected '0a', got '{index_hex}' in {uuid_str}"


# ===================================================================
# Equality
# ===================================================================


class TestEquality:
    """is_static_equal and is_random_equal."""

    def test_static_equal_ignores_uid(self) -> None:
        a = ResourceUuid()
        a.set(ResourceType.SENDER, ResourceSubType.NONE, 0, "SN1", 100, False)
        b = ResourceUuid()
        b.set(ResourceType.SENDER, ResourceSubType.NONE, 0, "SN1", 999, False)
        assert a.is_static_equal(b) is True
        assert a.is_random_equal(b) is False

    def test_random_equal(self) -> None:
        a = ResourceUuid()
        a.set(ResourceType.SENDER, ResourceSubType.NONE, 0, "SN1", 42, False)
        b = ResourceUuid()
        b.set(ResourceType.SENDER, ResourceSubType.NONE, 0, "SN1", 42, False)
        assert a.is_random_equal(b) is True

    def test_different_type_not_equal(self) -> None:
        a = ResourceUuid()
        a.set(ResourceType.SENDER, ResourceSubType.NONE, 0, "SN1", 0, False)
        b = ResourceUuid()
        b.set(ResourceType.RECEIVER, ResourceSubType.NONE, 0, "SN1", 0, False)
        assert a.is_static_equal(b) is False


# ===================================================================
# Null / default
# ===================================================================


class TestNull:
    """set_to_null() and default state."""

    def test_set_to_null(self) -> None:
        u = ResourceUuid()
        u.set(ResourceType.SENDER, ResourceSubType.USB, 5, "SN123", 999, True)
        u.set_to_null()
        assert u.resource_type == ResourceType.NODE
        assert u.index == 0
        assert u.serial_number == ""
        assert u.on_demand is False

    def test_null_uuid_is_valid(self) -> None:
        """A null UUID (no S/N) should still produce valid NMOS UUID format."""
        u = ResourceUuid()
        uuid_str = str(u)
        assert _NMOS_UUID_RE.match(uuid_str), f"null UUID invalid: {uuid_str}"


# ===================================================================
# set_from_string edge cases
# ===================================================================


class TestSetFromString:
    """Parsing UUID strings."""

    def test_invalid_format_raises(self) -> None:
        u = ResourceUuid()
        with pytest.raises(InvalidData, match="invalid"):
            u.set_from_string("not-a-uuid")

    def test_uppercase_accepted(self) -> None:
        """NMOS UUIDs are lowercase, but we accept uppercase input."""
        u = ResourceUuid()
        u.set(ResourceType.NODE, ResourceSubType.NONE, 0, "SN1", 42, False)
        uuid_str = str(u).upper()
        # Uppercase won't match our strict regex, but the code lowercases first
        u2 = ResourceUuid()
        u2.set_from_string(uuid_str)
        assert u2.unique_id == 42


# ===================================================================
# update_resource_unique_id
# ===================================================================


class TestUpdateUniqueId:
    """Module-level update_resource_unique_id()."""

    def test_preserves_rest(self) -> None:
        u = ResourceUuid()
        u.set(ResourceType.SENDER, ResourceSubType.NONE, 5, "SNX12345", 100, False)
        original = str(u)

        updated = update_resource_unique_id(original, 999)
        assert updated[8:] == original[8:], "non-uniqueId portion changed"
        assert updated[:8] != original[:8], "uniqueId portion should differ"

    def test_roundtrip(self) -> None:
        u = ResourceUuid()
        u.set(ResourceType.SENDER, ResourceSubType.NONE, 5, "SNX12345", 100, False)
        original = str(u)

        updated = update_resource_unique_id(original, 0xDEADBEEF)
        u2 = ResourceUuid()
        u2.set_from_string(updated)
        assert u2.unique_id == 0xDEADBEEF
        assert u2.serial_number == "SNX12345"


# ===================================================================
# set_unique_id
# ===================================================================


class TestSetUniqueId:
    """ResourceUuid.set_unique_id()."""

    def test_changes_only_uid(self) -> None:
        u = ResourceUuid()
        u.set(ResourceType.FLOW, ResourceSubType.NONE, 3, "SN99", 0, False)
        str_before = str(u)

        u.set_unique_id(0xCAFEBABE)
        str_after = str(u)

        assert str_before[8:] == str_after[8:]
        assert u.unique_id == 0xCAFEBABE
