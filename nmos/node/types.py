# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Supporting types for the Node module.

Defines PoolOfIndices, NaturalGroups, Leg, Interface, IPv4/IPv6Settings,
Activation, Privacy, Tracker, etc.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Any

from nmos.errors import Full, InvalidOperation
from nmos.ip import Addr, IPv4Addr, IPv6Addr
from nmos.ip.ipv4 import ANY_ADDR as IPV4_ANY


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_LEGS = 2
MAX_INTERFACES = 3
MAX_KEYS = 8
MAX_SEARCH_DOMAINS = 64
ON_DEMAND_EXPIRY_DELAY = 60.0  # seconds


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ActivationState(IntEnum):
    NONE = 0
    IMMEDIATE = 1
    PENDING = 2
    ERROR = 3


class ActivationMode(IntEnum):
    NONE = 0
    IMMEDIATE = 1
    RELATIVE = 2
    ABSOLUTE = 3


class EngineState(IntEnum):
    INVALID = 0
    INIT = 1
    ACTIVE = 2
    ERROR = 3
    INACTIVE = 4


# ---------------------------------------------------------------------------
# PoolOfIndices — 256-slot index allocator
# ---------------------------------------------------------------------------

class PoolOfIndices:
    """Pool of 256 indices (0-255) with allocate/release.

    Backed by a [256]bool array. Used to track which resource indices are
    in use within the ResourceUuid encoding scheme.
    """

    __slots__ = ("_used",)

    def __init__(self) -> None:
        self._used: list[bool] = [False] * 256

    def get_index(self) -> int:
        """Allocate the first unused index. Raises Full if all 256 are in use."""
        for i, used in enumerate(self._used):
            if not used:
                self._used[i] = True
                return i
        raise Full("no more indices available")

    def put_index(self, index: int) -> None:
        """Release an index back to the pool."""
        self._used[index] = False

    def is_used(self, index: int) -> bool:
        return self._used[index]


# ---------------------------------------------------------------------------
# Network configuration
# ---------------------------------------------------------------------------

@dataclass
class IPv4Settings:
    """IPv4 network configuration for a leg or interface."""
    port: int = 0
    address: Addr | None = None
    gateway: Addr | None = None
    dns_address: Addr | None = None
    subnet_mask: Addr | None = None
    static: bool = False
    dhcp: bool = False
    mdns: bool = False
    llmnr: bool = False
    hostname: str = ""


@dataclass
class IPv6Settings:
    """IPv6 network configuration for a leg or interface."""
    port: int = 0
    address: Addr | None = None
    gateway: Addr | None = None
    dns_address: Addr | None = None
    prefix_len: int = 0
    static: bool = False
    dhcp: bool = False
    mdns: bool = False
    llmnr: bool = False
    hostname: str = ""


@dataclass
class Leg:
    """Network redundancy leg (up to MAX_LEGS per node).

    Each leg can listen on IPv4 and/or IPv6. The leg's address is used
    as the source IP in transport parameters.
    """
    enable: bool = False
    name: str = ""
    listen_ipv4: bool = False
    use_ipv4: bool = False
    ipv4: IPv4Settings = field(default_factory=IPv4Settings)
    listen_ipv6: bool = False
    use_ipv6: bool = False
    ipv6: IPv6Settings = field(default_factory=IPv6Settings)


@dataclass
class Interface:
    """System network interface for Node API / services / controls.

    Up to MAX_INTERFACES per node. Interfaces determine where the node
    listens for HTTP requests.
    """
    enable: bool = False
    name: str = ""
    tls: bool = False
    listen_ipv4: bool = False
    use_ipv4: bool = False
    ipv4: IPv4Settings = field(default_factory=IPv4Settings)
    listen_ipv6: bool = False
    use_ipv6: bool = False
    ipv6: IPv6Settings = field(default_factory=IPv6Settings)
    mac_addr: bytes = b"\x00" * 6


# ---------------------------------------------------------------------------
# NaturalGroups — transport grouping for senders/receivers
# ---------------------------------------------------------------------------

# Format label lookup for group hint strings
_FORMAT_LABELS: dict[Any, str] = {}  # populated after enums import

_TRANSPORT_NAMES: dict[Any, str] = {}  # populated after enums import


def _init_enum_lookups() -> None:
    """Lazy-init enum lookups to avoid circular imports with nmos.enums."""
    if _FORMAT_LABELS:
        return
    try:
        from nmos.enums import EnumRegistry
        # Format labels
        for name, label in [
            ("urn:x-nmos:format:video", "VIDEO"),
            ("urn:x-nmos:format:audio", "AUDIO"),
            ("urn:x-nmos:format:data", "DATA"),
            ("urn:x-nmos:format:mux", "MUX"),
            ("urn:x-nmos:format:data.event", "EVENT"),
        ]:
            enum = EnumRegistry.get(name)
            if enum is not None:
                _FORMAT_LABELS[enum] = label

        # Transport name lookup
        transport_map = {
            "urn:x-nmos:transport:rtp": "RTP",
            "urn:x-nmos:transport:rtp.ucast": "RTP",
            "urn:x-nmos:transport:rtp.mcast": "RTP",
            "urn:x-nmos:transport:rtp.tcp": "RTP",
            "urn:x-nmos:transport:mqtt": "MQTT",
            "urn:x-nmos:transport:websocket": "WS",
            "urn:x-nmos:transport:ndi": "NDI",
            "urn:x-nmos:transport:rtsp": "RTSP",
            "urn:x-nmos:transport:rtsp.tcp": "RTSP",
            "urn:x-nmos:transport:srt": "SRT",
            "urn:x-nmos:transport:srt.mpeg2ts": "SRT",
            "urn:x-nmos:transport:srt.rtp": "SRT",
            "urn:x-nmos:transport:usb": "USB",
            "urn:x-nmos:transport:tcp": "TCP",
            "urn:x-nmos:transport:udp": "UDP",
            "urn:x-nmos:transport:udp.ucast": "UDP",
            "urn:x-nmos:transport:udp.mcast": "UDP",
            "urn:x-nmos:transport:udp.mpeg2ts": "UDP",
            "urn:x-nmos:transport:udp.mpeg2ts.ucast": "UDP",
            "urn:x-nmos:transport:udp.mpeg2ts.mcast": "UDP",
        }
        for urn, name in transport_map.items():
            enum = EnumRegistry.get(urn)
            if enum is not None:
                _TRANSPORT_NAMES[enum] = name
    except ImportError:
        pass  # enums not available yet — lookups will be empty


class NaturalGroup:
    """One of 256 natural groups, with per-format role index pools.

    Groups are used to associate related senders/receivers (e.g., the
    video and audio components of a single stream). The group hint string
    like "RTP 3:VIDEO 0" tells controllers which resources belong together.
    """

    __slots__ = ("description", "name", "role_indices")

    def __init__(self) -> None:
        self.description: str = ""
        self.name: str = ""
        # Keyed by format enum — one pool per format within this group
        self.role_indices: dict[Any, PoolOfIndices] = {}

    def _get_pool(self, format_enum: Any) -> PoolOfIndices:
        """Get or create the role index pool for a format."""
        pool = self.role_indices.get(format_enum)
        if pool is None:
            pool = PoolOfIndices()
            self.role_indices[format_enum] = pool
        return pool


class NaturalGroups:
    """256 natural groups for sender or receiver transport grouping.

    Each group tracks role indices per format (video, audio, data, mux,
    event). The group hint string format is:
    "{transport_name} {group_index}:{FORMAT_LABEL} {role_index}"
    """

    __slots__ = ("_groups",)

    def __init__(self) -> None:
        self._groups: list[NaturalGroup] = [NaturalGroup() for _ in range(256)]

    def get_group_hint(
        self, group_index: int, format_enum: Any, transport_enum: Any,
    ) -> tuple[str, int]:
        """Allocate a role index and return (hint_string, role_index).

        Unifies all 5 format-specific variants (video/audio/data/mux/event)
        into this single method.

        Returns:
            (hint, role_index) where hint is like "RTP 3:VIDEO 0"

        Raises:
            InvalidOperation if the role pool for this format is exhausted
            or the format is not recognized.
        """
        _init_enum_lookups()

        label = _FORMAT_LABELS.get(format_enum)
        if label is None:
            raise InvalidOperation(f"invalid format for natural group: {format_enum}")

        group = self._groups[group_index]
        pool = group._get_pool(format_enum)

        try:
            role_index = pool.get_index()
        except Full:
            raise InvalidOperation(
                f"too many {label.lower()} roles in group {group_index}"
            ) from None

        transport_name = _TRANSPORT_NAMES.get(transport_enum, "GROUP")
        group.name = transport_name

        hint = f"{group.name} {group_index}:{label} {role_index}"
        return hint, role_index

    def put_role_index(
        self, group_index: int, format_enum: Any, role_index: int,
    ) -> None:
        """Release a role index back to the pool.

        Unifies all format-specific release variants into a single method.
        """
        group = self._groups[group_index]
        pool = group._get_pool(format_enum)
        pool.put_index(role_index)

    def get_description(self, group_index: int) -> str:
        return self._groups[group_index].description

    def set_name(self, group_index: int, name: str) -> None:
        self._groups[group_index].name = name


# ---------------------------------------------------------------------------
# Privacy / Pre-Shared Keys
# ---------------------------------------------------------------------------

@dataclass
class PreSharedKey:
    """A single pre-shared key entry."""
    key_id: bytes = b"\x00" * 8    # 8 bytes
    psk: bytes = b""               # 16, 32, or 64 bytes


@dataclass
class PrivacyPreSharedKeys:
    """Up to MAX_KEYS pre-shared keys for a sender/receiver."""
    keys: list[PreSharedKey] = field(default_factory=list)


@dataclass
class Privacy:
    """Privacy/encryption state for a sender or receiver.

    ECDH key generation and PSK storage live here. The actual PEP/KDP
    crypto operations are external (ffmpeg).
    """
    iv: bytes = b"\x00" * 8
    key_generator: bytes = b"\x00" * 16
    key_version: bytes = b"\x00" * 4
    key_id: bytes = b"\x00" * 8

    ecdh_sender_public_key: bytes = b""
    ecdh_sender_private: Any = None   # ecdh.PrivateKey equivalent
    ecdh_sender_public: Any = None    # ecdh.PublicKey equivalent

    ecdh_receiver_public_key: bytes = b""
    ecdh_receiver_private: Any = None
    ecdh_receiver_public: Any = None

    psk: bytes = b""    # pre-shared key
    pfs: bytes = b""    # perfect forward secrecy
    xcl: bytes = b"\x00" * 16  # exclusive key
    key: bytes = b""    # effective key

    protocol: Any = None  # EnumId
    mode: Any = None      # EnumId
    curve: Any = None     # EnumId


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------

@dataclass
class Activation:
    """Transport parameter activation state for a sender or receiver.

    Holds staged, active, and constraint transport parameters as polymorphic
    lists (one per leg). The actual types depend on the transport protocol
    and are set by the transport descriptor registry (activation.py).
    """
    sender_index: int = 0
    receiver_index: int = 0
    enabled_legs: int = 0
    active: list[Any] = field(default_factory=list)       # [MAX_LEGS] transport params
    staged: list[Any] = field(default_factory=list)       # [MAX_LEGS] transport params
    constraints: list[Any] = field(default_factory=list)  # [MAX_LEGS] constraints
    staged_state: Any = None    # activation value type
    active_state: Any = None    # activation value type
    state: ActivationState = ActivationState.NONE
    mode: ActivationMode = ActivationMode.NONE
    time: datetime | None = None
    requested_time: datetime | None = None
    requested_delta_time: timedelta | None = None
    activation_time_tai: str = ""
    privacy: Privacy = field(default_factory=Privacy)
    privacy_keys: PrivacyPreSharedKeys = field(default_factory=PrivacyPreSharedKeys)
    engine: Any = None          # tasks.DispatchGroup
    engine_state: EngineState = EngineState.INVALID
    sender_name: str = ""


# ---------------------------------------------------------------------------
# Publish & Garbage tracking
# ---------------------------------------------------------------------------

@dataclass
class Tracker:
    """Version tracking for publish deduplication.

    Stores the last-seen version for a resource ID to detect whether a
    published resource has actually changed.
    """
    id: str = ""
    version: Any = None  # NTimeValue


@dataclass
class GarbageResource:
    """Tracks a deleted/replaced resource ID for delayed registry cleanup.

    When a resource's dynamic UUID changes (on update), the old UUID is
    pushed here. The registry removes it after the heartbeat period.
    """
    id: str = ""
    time: datetime = field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# Hex string utilities
# ---------------------------------------------------------------------------

def hex_from_bytes(data: bytes) -> str:
    """Convert bytes to lowercase hex string (no prefix)."""
    return data.hex()


def bytes_from_hex(hex_str: str) -> bytes:
    """Convert hex string to bytes. Raises InvalidData on invalid input."""
    from nmos.errors import InvalidData
    if len(hex_str) % 2 != 0:
        raise InvalidData("invalid hexadecimal value: odd length")
    try:
        return bytes.fromhex(hex_str)
    except ValueError as exc:
        raise InvalidData(f"invalid hexadecimal value: {exc}") from exc
