# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Typed wrapper around the optional ``netifaces`` dependency.

This quarantines the untyped third-party import behind a small handwritten
API so the rest of the codebase can stay under ``mypy --strict`` without
sprinkling import ignores across runtime modules.
"""

from __future__ import annotations

import socket
from typing import Any, cast

try:
    import netifaces as _netifaces
except ImportError:
    _netifaces = None


def find_interface_name_for_address(host: str) -> str | None:
    """Return the interface name that owns ``host``, or ``None``."""
    if _netifaces is None:
        return None

    for iface in _netifaces.interfaces():
        addrs = _netifaces.ifaddresses(iface)
        for addr_family in (_netifaces.AF_INET, _netifaces.AF_INET6):
            for addr in addrs.get(addr_family, []):
                record = cast(dict[str, Any], addr) if isinstance(addr, dict) else {}
                value = record.get("addr")
                if isinstance(value, str) and value == host:
                    return iface if isinstance(iface, str) else str(iface)
    return None


def get_interface_index_for_address(ip: str) -> int:
    """Return the interface index for ``ip``, or ``0`` if not found."""
    iface = find_interface_name_for_address(ip)
    if iface is None:
        return 0
    try:
        return socket.if_nametoindex(iface)
    except OSError:
        return 0
