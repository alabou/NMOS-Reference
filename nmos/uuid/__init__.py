# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""ResourceUuid — generalized NMOS-compliant UUID with base-62 serial numbers.

Generalized for any vendor. Supports up to 12 case-sensitive alphanumeric
characters for serial numbers (covers virtually all real-world hardware S/Ns
including GS1 AI-21).

Bit layout (128 bits):

    Hex:    UUUUUUUU-TIIs-4eee-veee-eeeeeeeeaabb

    UUUUUUUU  uniqueId (32 bits, byte-reversed)
    T         onDemand(MSB) | resourceType(3 bits)
    II        index (8 bits, directly readable in hex)
    s         resourceSubType(2 bits) | sn_encoded top 2 bits
    4         UUID version (locked)
    eee...    base-62 bijective-encoded leading S/N chars (60 bits scattered)
    v         UUID variant (locked 10xx) + 2 sn_encoded bits
    aabb      last 2 S/N chars encoded as char-'0' (digits read directly)

Reading the first hex digit of group 2 (T):
    When onDemand=0 (common), the digit IS the resource type:
    0=Node 1=Device 2=Sender 3=Receiver 4=Flow 5=Source 6=Input 7=Output
    When onDemand=1, add 8:
    8=Node/OD 9=Device/OD a=Sender/OD ...

Last 4 hex chars show last 2 S/N characters (char - '0'):
    '4','5' → 0405    digits read directly
    'A','B' → 1112    letters offset from '0'
"""

from __future__ import annotations

import re
from enum import IntEnum

from nmos.errors import InvalidData, InvalidParameter
from nmos.uuid.base62 import (
    MAX_SERIAL_LENGTH,
    char_to_clear,
    clear_to_char,
    decode,
    encode,
    is_valid_serial,
)


# ---------------------------------------------------------------------------
# Enums — ResourceType and ResourceSubType
# ---------------------------------------------------------------------------

class ResourceType(IntEnum):
    """NMOS resource types (3 bits, values 0-7)."""
    NODE = 0
    DEVICE = 1
    SENDER = 2
    RECEIVER = 3
    FLOW = 4
    SOURCE = 5
    INPUT = 6
    OUTPUT = 7


class ResourceSubType(IntEnum):
    """NMOS resource sub-types (2 bits, values 0-3)."""
    NONE = 0
    USB = 1
    SENDER_MONITOR = 2
    RECEIVER_MONITOR = 3


# ---------------------------------------------------------------------------
# NMOS UUID regex
# ---------------------------------------------------------------------------

_NMOS_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}"
    r"-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


# ---------------------------------------------------------------------------
# ResourceUuid
# ---------------------------------------------------------------------------

class ResourceUuid:
    """Generalized NMOS-compliant UUID encoding resource identity + serial number.

    All fields are stored as typed Python attributes. The UUID string is
    computed on demand from the fields via __str__().
    """

    __slots__ = (
        "unique_id",
        "resource_type",
        "resource_sub_type",
        "on_demand",
        "index",
        "serial_number",
    )

    def __init__(self) -> None:
        self.unique_id: int = 0
        self.resource_type: ResourceType = ResourceType.NODE
        self.resource_sub_type: ResourceSubType = ResourceSubType.NONE
        self.on_demand: bool = False
        self.index: int = 0
        self.serial_number: str = ""

    # --- Primary API ---

    def set(
        self,
        resource_type: ResourceType,
        resource_sub_type: ResourceSubType,
        index: int,
        serial_number: str,
        unique_id: int,
        on_demand: bool,
    ) -> None:
        """Populate all fields.

        The serial_number must be 1-12 alphanumeric characters.
        """
        if not is_valid_serial(serial_number):
            raise InvalidParameter(
                f"serial number must be 1-{MAX_SERIAL_LENGTH} alphanumeric "
                f"chars, got {serial_number!r}"
            )
        if not 0 <= index <= 255:
            raise InvalidParameter(f"index must be 0-255, got {index}")

        self.unique_id = unique_id & 0xFFFFFFFF
        self.resource_type = resource_type
        self.resource_sub_type = resource_sub_type
        self.on_demand = on_demand
        self.index = index
        self.serial_number = serial_number

    def set_from_string(self, s: str) -> None:
        """Parse a UUID hex string back into all fields.

        Raises InvalidData if the string doesn't match the NMOS UUID format.
        """
        s = s.strip().lower()
        if not _NMOS_UUID_RE.match(s):
            raise InvalidData("invalid NMOS UUID format")

        hex_str = s.replace("-", "")
        value = int(hex_str, 16)
        self._unpack(value)

    def set_to_null(self) -> None:
        """Reset to default state."""
        self.unique_id = 0
        self.resource_type = ResourceType.NODE
        self.resource_sub_type = ResourceSubType.NONE
        self.on_demand = False
        self.index = 0
        self.serial_number = ""

    def set_unique_id(self, unique_id: int) -> None:
        """Update just the random/unique part."""
        self.unique_id = unique_id & 0xFFFFFFFF

    # --- Accessors ---

    def is_on_demand(self) -> bool:
        return self.on_demand

    def get_serial_number(self) -> str:
        return self.serial_number

    def get_resource_type(self) -> ResourceType:
        return self.resource_type

    def get_resource_sub_type(self) -> ResourceSubType:
        return self.resource_sub_type

    def get_index(self) -> int:
        return self.index

    # --- Equality ---

    def is_static_equal(self, other: ResourceUuid) -> bool:
        """Compare without considering uniqueId (random part)."""
        return (
            self.resource_type == other.resource_type
            and self.resource_sub_type == other.resource_sub_type
            and self.on_demand == other.on_demand
            and self.index == other.index
            and self.serial_number == other.serial_number
        )

    def is_random_equal(self, other: ResourceUuid) -> bool:
        """Compare including uniqueId (full equality)."""
        return self.is_static_equal(other) and self.unique_id == other.unique_id

    # --- String representation ---

    def __str__(self) -> str:
        """Format as NMOS-compliant UUID hex string."""
        value = self._pack()
        h = f"{value:032x}"
        return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

    def __repr__(self) -> str:
        return (
            f"ResourceUuid(type={self.resource_type.name}, "
            f"index={self.index}, sn={self.serial_number!r})"
        )

    # --- Internal: bit packing ---

    def _pack(self) -> int:
        """Assemble all fields into a 128-bit integer.

        Bit positions (MSB=127, LSB=0):
            [127:96] uniqueId (byte-reversed)
            [95]     onDemand
            [94:92]  resourceType
            [91:84]  index
            [83:82]  resourceSubType
            [81:80]  sn_encoded bits [59:58]
            [79:76]  version = 0100 (LOCKED)
            [75:64]  sn_encoded bits [57:46]
            [63:62]  variant = 10 (LOCKED)
            [61:48]  sn_encoded bits [45:32]
            [47:16]  sn_encoded bits [31:0]
            [15:8]   sn_clear high byte (second-to-last S/N char)
            [7:0]    sn_clear low byte (last S/N char)
        """
        sn_encoded, sn_clear_hi, sn_clear_lo = self._encode_serial()

        # uniqueId — byte-reversed
        uid = self.unique_id & 0xFFFFFFFF
        uid_rev = (
            ((uid & 0xFF) << 24)
            | ((uid & 0xFF00) << 8)
            | ((uid & 0xFF0000) >> 8)
            | ((uid >> 24) & 0xFF)
        )

        value = uid_rev
        value = (value << 1) | (1 if self.on_demand else 0)
        value = (value << 3) | (int(self.resource_type) & 0x7)
        value = (value << 8) | (self.index & 0xFF)
        value = (value << 2) | (int(self.resource_sub_type) & 0x3)
        value = (value << 2) | ((sn_encoded >> 58) & 0x3)
        value = (value << 4) | 0x4                           # version
        value = (value << 12) | ((sn_encoded >> 46) & 0xFFF)
        value = (value << 2) | 0x2                           # variant
        value = (value << 14) | ((sn_encoded >> 32) & 0x3FFF)
        value = (value << 32) | (sn_encoded & 0xFFFFFFFF)
        value = (value << 8) | (sn_clear_hi & 0xFF)
        value = (value << 8) | (sn_clear_lo & 0xFF)

        return value

    def _unpack(self, value: int) -> None:
        """Decompose a 128-bit integer into all fields."""
        # sn_clear (last 2 bytes)
        sn_clear_lo = value & 0xFF
        sn_clear_hi = (value >> 8) & 0xFF

        # sn_encoded: reassemble from scattered bit positions
        sn_enc_d = (value >> 16) & 0xFFFFFFFF      # bits [47:16]  → [31:0]
        sn_enc_c = (value >> 48) & 0x3FFF           # bits [61:48]  → [45:32]
        # skip variant [63:62]
        sn_enc_b = (value >> 64) & 0xFFF            # bits [75:64]  → [57:46]
        # skip version [79:76]
        sn_enc_a = (value >> 80) & 0x3              # bits [81:80]  → [59:58]

        sn_encoded = (
            (sn_enc_a << 58)
            | (sn_enc_b << 46)
            | (sn_enc_c << 32)
            | sn_enc_d
        )

        # Metadata
        on_demand = bool((value >> 95) & 0x1)
        resource_type = (value >> 92) & 0x7
        index = (value >> 84) & 0xFF
        resource_sub_type = (value >> 82) & 0x3

        # uniqueId (byte-reversed back)
        uid_rev = (value >> 96) & 0xFFFFFFFF
        unique_id = (
            ((uid_rev & 0xFF) << 24)
            | ((uid_rev & 0xFF00) << 8)
            | ((uid_rev & 0xFF0000) >> 8)
            | ((uid_rev >> 24) & 0xFF)
        )

        # Decode serial number
        self.serial_number = _decode_serial(sn_encoded, sn_clear_hi, sn_clear_lo)
        self.unique_id = unique_id
        self.resource_type = ResourceType(resource_type)
        self.resource_sub_type = ResourceSubType(resource_sub_type)
        self.on_demand = on_demand
        self.index = index

    # --- Internal: serial number codec ---

    def _encode_serial(self) -> tuple[int, int, int]:
        """Encode serial_number → (bijective_encoded_60bit, clear_hi, clear_lo).

        Last 2 S/N chars → clear bytes (char - '0'), right-aligned.
        Remaining leading chars → bijective base-62 into 60-bit integer.
        Bijective encoding preserves the exact length of the leading portion.
        """
        sn = self.serial_number
        if not sn:
            return 0, 0, 0

        if len(sn) == 1:
            return 0, 0, char_to_clear(sn[0])
        elif len(sn) == 2:
            return 0, char_to_clear(sn[0]), char_to_clear(sn[1])
        else:
            clear_hi = char_to_clear(sn[-2])
            clear_lo = char_to_clear(sn[-1])
            encoded = encode(sn[:-2])
            return encoded, clear_hi, clear_lo


def _decode_serial(sn_encoded: int, sn_clear_hi: int, sn_clear_lo: int) -> str:
    """Decode serial number from packed fields.

    The bijective encoding in sn_encoded self-describes the length of the
    leading portion. Combined with the 2 clear bytes, we recover the full
    original serial number string.
    """
    # Determine how many clear chars we have
    if sn_encoded == 0 and sn_clear_hi == 0 and sn_clear_lo == 0:
        return ""
    elif sn_encoded == 0 and sn_clear_hi == 0:
        # 1-char S/N: only last char
        return clear_to_char(sn_clear_lo)
    elif sn_encoded == 0:
        # 2-char S/N: both clear bytes
        return clear_to_char(sn_clear_hi) + clear_to_char(sn_clear_lo)
    else:
        # N+2 char S/N: decoded leading + 2 clear trailing
        leading = decode(sn_encoded)
        return leading + clear_to_char(sn_clear_hi) + clear_to_char(sn_clear_lo)


# ---------------------------------------------------------------------------
# Module-level utility
# ---------------------------------------------------------------------------

def update_resource_unique_id(uuid_str: str, unique_id: int) -> str:
    """Replace the uniqueId portion of a UUID string, preserving everything else."""
    uid = unique_id & 0xFFFFFFFF
    uid_rev = (
        ((uid & 0xFF) << 24)
        | ((uid & 0xFF00) << 8)
        | ((uid & 0xFF0000) >> 8)
        | ((uid >> 24) & 0xFF)
    )
    return f"{uid_rev:08x}{uuid_str[8:]}"
