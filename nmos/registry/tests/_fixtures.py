# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Shared resource builders for the registry test suite.

Underscore-prefixed so pytest does not collect it as a test module, matching
the convention used by ``nmos/api/tests/_tls_helpers.py``.

The builders produce resources that satisfy the real IS-04 v1.3 schemas — a
Node really does need ``href``, ``caps``, ``api``, ``services``, ``clocks``
and ``interfaces``, because the registry validates by decoding into the
generated types and a short-cut fixture would simply be rejected. Keeping the
minimal-but-valid shapes here means each test can say what it is actually
about.
"""

from __future__ import annotations

import time
from typing import Any

from nmos.node.types import utc_to_tai

# Fixed UUIDs, so failures name a recognisable resource rather than a random
# one. They satisfy the RAML pattern (version nibble 1-5, variant nibble 8-b).
NODE_ID = "3b8be755-08ff-452b-b217-c9151eb21193"
NODE_ID_2 = "1a3d5b90-4c1e-42a7-9f2b-6d8e0c4a7b31"
DEVICE_ID = "a370d258-69de-4422-860a-ee4cf32ee9f4"
DEVICE_ID_2 = "c5e91f04-2b7a-4d63-8e15-9a0b3c6d2f88"
SOURCE_ID = "c23c6a65-8e91-4f6c-a484-046363dbca29"
FLOW_ID = "b3bb5be7-9fe9-4324-a5bb-4c70e1084449"
SENDER_ID = "171d5c80-7fff-4c23-9383-46503eb1c63e"
RECEIVER_ID = "3350d113-1593-4271-a7f5-f4974415bb8e"


def tai_version(offset: float = 0.0) -> str:
    """A ``"<seconds>:<nanoseconds>"`` version string, optionally shifted.

    ``offset`` lets a test express "older than" / "newer than" without
    hard-coding an epoch.
    """
    seconds, nanoseconds = utc_to_tai(time.time() + offset)
    return f"{seconds}:{nanoseconds}"


def make_node(
    node_id: str = NODE_ID,
    *,
    version: str | None = None,
    label: str = "test-node",
    **extra: Any,
) -> dict[str, Any]:
    """A minimal Node that satisfies ``node.json``."""
    resource: dict[str, Any] = {
        "id": node_id,
        "version": version or tai_version(),
        "label": label,
        "description": "registry test node",
        "tags": {},
        "href": "http://192.0.2.1:8080/",
        "caps": {},
        "api": {
            "versions": ["v1.3"],
            "endpoints": [
                {"host": "192.0.2.1", "port": 8080, "protocol": "http"},
            ],
        },
        "services": [],
        "clocks": [],
        "interfaces": [],
    }
    resource.update(extra)
    return resource


def make_device(
    device_id: str = DEVICE_ID,
    node_id: str = NODE_ID,
    *,
    version: str | None = None,
    label: str = "test-device",
    **extra: Any,
) -> dict[str, Any]:
    """A minimal Device that satisfies ``device.json``."""
    resource: dict[str, Any] = {
        "id": device_id,
        "version": version or tai_version(),
        "label": label,
        "description": "registry test device",
        "tags": {},
        "type": "urn:x-nmos:device:generic",
        "node_id": node_id,
        "controls": [],
        "senders": [],
        "receivers": [],
    }
    resource.update(extra)
    return resource


def make_source(
    source_id: str = SOURCE_ID,
    device_id: str = DEVICE_ID,
    *,
    version: str | None = None,
    label: str = "test-source",
    **extra: Any,
) -> dict[str, Any]:
    """A minimal video Source that satisfies ``source_generic.json``."""
    resource: dict[str, Any] = {
        "id": source_id,
        "version": version or tai_version(),
        "label": label,
        "description": "registry test source",
        "tags": {},
        "device_id": device_id,
        "caps": {},
        "format": "urn:x-nmos:format:video",
        "parents": [],
        "clock_name": None,
        "grain_rate": {"numerator": 25, "denominator": 1},
    }
    resource.update(extra)
    return resource


def make_flow(
    flow_id: str = FLOW_ID,
    source_id: str = SOURCE_ID,
    device_id: str = DEVICE_ID,
    *,
    version: str | None = None,
    label: str = "test-flow",
    **extra: Any,
) -> dict[str, Any]:
    """A minimal raw-video Flow that satisfies ``flow_video_raw.json``."""
    resource: dict[str, Any] = {
        "id": flow_id,
        "version": version or tai_version(),
        "label": label,
        "description": "registry test flow",
        "tags": {},
        "device_id": device_id,
        "source_id": source_id,
        "parents": [],
        "format": "urn:x-nmos:format:video",
        "media_type": "video/raw",
        "frame_width": 1920,
        "frame_height": 1080,
        "interlace_mode": "progressive",
        "colorspace": "BT709",
        "components": [
            {"name": "Y", "width": 1920, "height": 1080, "bit_depth": 10},
            {"name": "Cb", "width": 960, "height": 1080, "bit_depth": 10},
            {"name": "Cr", "width": 960, "height": 1080, "bit_depth": 10},
        ],
        "grain_rate": {"numerator": 25, "denominator": 1},
    }
    resource.update(extra)
    return resource


def make_sender(
    sender_id: str = SENDER_ID,
    flow_id: str = FLOW_ID,
    device_id: str = DEVICE_ID,
    *,
    version: str | None = None,
    label: str = "test-sender",
    **extra: Any,
) -> dict[str, Any]:
    """A minimal Sender that satisfies ``sender.json``."""
    resource: dict[str, Any] = {
        "id": sender_id,
        "version": version or tai_version(),
        "label": label,
        "description": "registry test sender",
        "tags": {},
        "flow_id": flow_id,
        "transport": "urn:x-nmos:transport:rtp.mcast",
        "device_id": device_id,
        "manifest_href": "http://192.0.2.1:8080/sender.sdp",
        "interface_bindings": ["eth0"],
        "caps": {},
        "subscription": {"receiver_id": None, "active": False},
    }
    resource.update(extra)
    return resource


def make_receiver(
    receiver_id: str = RECEIVER_ID,
    device_id: str = DEVICE_ID,
    *,
    version: str | None = None,
    label: str = "test-receiver",
    **extra: Any,
) -> dict[str, Any]:
    """A minimal video Receiver that satisfies ``receiver_video.json``."""
    resource: dict[str, Any] = {
        "id": receiver_id,
        "version": version or tai_version(),
        "label": label,
        "description": "registry test receiver",
        "tags": {},
        "device_id": device_id,
        "transport": "urn:x-nmos:transport:rtp.mcast",
        "interface_bindings": ["eth0"],
        "subscription": {"sender_id": None, "active": False},
        "format": "urn:x-nmos:format:video",
        "caps": {"media_types": ["video/raw"]},
    }
    resource.update(extra)
    return resource
