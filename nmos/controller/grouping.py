# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Natural-group + device-serial helpers for the controller UI.

NMOS senders and receivers each declare their "natural group" via the
``urn:x-nmos:tag:grouphint/v1.0`` tag on their ResourceCore. The tag
value follows the format emitted by
[nmos.node.types.NaturalGroups.get_group_hint](../node/types.py):

    ``"{TRANSPORT_NAME} {group_index}:{FORMAT_LABEL} {role_index}"``

e.g. ``"RTP 3:VIDEO 0"`` — transport RTP, natural group 3, VIDEO
format, role 0 (first sender in that group).

Two senders / receivers are members of the **same natural group** when:

  1. They belong to the same owning device (same ``device_id``), and
  2. They share the same ``(transport, group_index, format_label)``
     prefix (the ``"RTP 3:VIDEO"`` portion of the hint).

Role index identifies a sender's position *within* the group — a stereo
audio pair has role 0 (L) and role 1 (R). Grouping two related senders
thus matches by prefix; separating their legs matches by role.

Device serial: the canonical, vendor-neutral serial number is the
BCP-002-02 *instance identifier* asset tag
``urn:x-nmos:tag:asset:instance-id/v1.0``. This module's
``device_serial()`` reads that tag first; when a device does not
publish it (older or third-party devices) it falls back to a
best-effort scan of the ``label``/``description``/``tags`` metadata for
an ``SNXnnnnn`` substring. Callers fall back to the bare UUID when no
serial is found.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from nmos.enums import TagAssetInstance

GROUP_HINT_TAG = "urn:x-nmos:tag:grouphint/v1.0"

# BCP-002-02 "Asset Distinguishing Information" instance identifier tag. Its
# value is the device's serial number (per
# https://specs.amwa.tv/bcp-002-02/releases/v1.0.0/docs/Overview.html#instance-identifier).
# This is the canonical, vendor-neutral serial and takes precedence over any
# vendor-specific convention. ``.s`` resolves to
# ``urn:x-nmos:tag:asset:instance-id/v1.0``.
ASSET_INSTANCE_ID_TAG = TagAssetInstance.s

# Per NMOS With Natural Groups.md §"Group Hint" (line 36-39) the tag
# value follows one of two forms:
#     "<group-name> <group-index>:<role-in-group> <role-index>"
#     "<group-name> <group-index>:<role-in-group>"
# where
#     <group-name>    = [A-Za-z]+
#     <role-in-group> = [A-Za-z]+   (case-insensitive per spec line 53)
#     <group-index>   = decimal, no leading zero unless exactly 0
#     <role-index>    = decimal, no leading zero unless exactly 0; when
#                       the segment is absent the role MUST be treated
#                       as 0 (spec line 57).

_TRANSPORT_URN_PREFIX = "urn:x-nmos:transport:"


def strip_transport_prefix(value: Any) -> str:
    """Trim the ``urn:x-nmos:transport:`` prefix from an IS-04 transport
    URN. Returns the empty string for anything that isn't a string or
    that doesn't carry the expected prefix.

    Example:
        ``urn:x-nmos:transport:rtp.mcast`` → ``rtp.mcast``
    """
    if not isinstance(value, str):
        return ""
    if value.startswith(_TRANSPORT_URN_PREFIX):
        return value[len(_TRANSPORT_URN_PREFIX):]
    return ""

# Matches both hint forms:
#   "RTP 3:VIDEO 0"    — full form
#   "RTP 3:VIDEO"      — role-index omitted, defaults to 0
# Character classes are tightened to the spec (letters-only for the
# two textual segments; digits-only for the two numeric segments).
_HINT_RE = re.compile(
    r"^\s*"
    r"(?P<transport>[A-Za-z]+)"
    r"\s+"
    r"(?P<group_index>\d+)"
    r":"
    r"(?P<format>[A-Za-z]+)"
    r"(?:\s+(?P<role>\d+))?"
    r"\s*$"
)

# Matches "...SNXnnnnn..." — SNX-style serial form.
_SERIAL_RE = re.compile(r"\b(SNX\d{5,})\b")


@dataclass(frozen=True)
class GroupHint:
    """Parsed ``grouphint/v1.0`` tag value.

    ``key`` is the tuple used to group senders / receivers that belong
    to the same natural group: ``(transport, group_index)``. Format
    and role are deliberately **not** part of the key — a single
    natural group (e.g. ``RTP 0``) contains members of multiple
    formats (AUDIO + VIDEO + ...), each distinguishable by its own
    ``(format, role)`` pair.
    """

    transport: str
    group_index: int
    format: str
    role: int

    @property
    def key(self) -> tuple[str, int]:
        return (self.transport, self.group_index)

    def __str__(self) -> str:
        return f"{self.transport} {self.group_index}:{self.format} {self.role}"


def parse_group_hint(value: str) -> GroupHint | None:
    """Parse a ``grouphint/v1.0`` tag value. Returns ``None`` on malformed.

    Per spec line 57, the role-index segment is optional — when absent
    the role is 0. Per spec line 53, ``<role-in-group>`` comparison is
    case-insensitive; we normalise the stored format to uppercase so
    equality and sort order are stable regardless of input casing.
    """
    if not value:
        return None
    m = _HINT_RE.match(value)
    if not m:
        return None
    try:
        role_str = m.group("role")
        role = int(role_str) if role_str is not None else 0
        return GroupHint(
            transport=m.group("transport").upper(),
            group_index=int(m.group("group_index")),
            format=m.group("format").upper(),
            role=role,
        )
    except (ValueError, TypeError):
        return None


def extract_group_hint(resource_tags: Any) -> GroupHint | None:
    """Find the group-hint tag on an IS-04 resource's Tags map.

    ``resource_tags`` may be a plain dict (from JSON) or a generated
    ``NTags`` value (from the in-process Node). The tag value is
    always an array; use the first entry.
    """
    if resource_tags is None:
        return None

    raw: Any = None
    # Plain dict from JSON: {"urn:x-nmos:tag:grouphint/v1.0": ["RTP 3:VIDEO 0"]}
    if isinstance(resource_tags, dict):
        raw = resource_tags.get(GROUP_HINT_TAG)
    else:
        # Generated typed object: probably exposes .get / .items / subscript
        getter = getattr(resource_tags, "get", None)
        if callable(getter):
            try:
                raw = getter(GROUP_HINT_TAG)
            except Exception:
                raw = None
        else:
            try:
                raw = resource_tags[GROUP_HINT_TAG]
            except (KeyError, TypeError, AttributeError):
                raw = None

    if raw is None:
        return None

    # Array form per IS-04 tags schema — take the first entry.
    if isinstance(raw, list):
        if not raw:
            return None
        raw = raw[0]

    if not isinstance(raw, str):
        return None

    return parse_group_hint(raw)


def _device_tags(device_resource: Any) -> dict[str, list[str]]:
    """Return a device resource's NMOS tags as ``{name: [values]}``.

    Handles both the plain dict form (IS-04 JSON straight from the
    registry) and the generated typed form (``ResourceCore.Tags`` is an
    ``NTags``). Returns ``{}`` when no usable tags are present.
    """
    if isinstance(device_resource, dict):
        tags = device_resource.get("tags")
        return tags if isinstance(tags, dict) else {}
    try:
        tags_obj = device_resource.ResourceCore.Tags
        val = tags_obj.get({})
        return val if isinstance(val, dict) else {}
    except Exception:
        return {}


def asset_instance_id(device_resource: Any) -> str | None:
    """Return the device's BCP-002-02 instance identifier (serial number).

    Reads the ``urn:x-nmos:tag:asset:instance-id/v1.0`` tag — an array of
    strings per the NMOS tag model — and returns its first non-empty value
    (stripped), or ``None`` when the tag is absent or empty.
    """
    values = _device_tags(device_resource).get(ASSET_INSTANCE_ID_TAG)
    if isinstance(values, list):
        for v in values:
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def device_serial(device_resource: Any) -> str | None:
    """Extract a device's serial number.

    The BCP-002-02 asset instance-identifier tag
    ``urn:x-nmos:tag:asset:instance-id/v1.0`` is the canonical,
    vendor-neutral serial and **takes precedence** over every other
    source. When that tag is absent (older or third-party devices that do
    not publish it) the function falls back to scanning the
    ``description``, ``label`` and any ``tags`` values for the first
    ``SNXnnnnn`` substring. Returns ``None`` when neither yields a serial.
    """
    if device_resource is None:
        return None

    # 1. BCP-002-02 instance identifier — authoritative, vendor-neutral.
    serial = asset_instance_id(device_resource)
    if serial:
        return serial

    # 2. Fallback: SNX-style serial embedded in description/label/tags.
    fields: list[str] = []

    if isinstance(device_resource, dict):
        for k in ("description", "label"):
            v = device_resource.get(k)
            if isinstance(v, str):
                fields.append(v)
        tags = device_resource.get("tags")
        if isinstance(tags, dict):
            for vv in tags.values():
                if isinstance(vv, list):
                    for entry in vv:
                        if isinstance(entry, str):
                            fields.append(entry)
    else:
        # Generated typed device — walk common attribute paths.
        for attr_path in (
            ("description",),
            ("label",),
            ("ResourceCore", "Description", "value"),
            ("ResourceCore", "Label", "value"),
        ):
            cur: Any = device_resource
            for step in attr_path:
                cur = getattr(cur, step, None)
                if cur is None:
                    break
            if isinstance(cur, str):
                fields.append(cur)

    for s in fields:
        m = _SERIAL_RE.search(s)
        if m:
            return m.group(1)
    return None


# Control URN prefixes the controller cares about when picking a device's
# "primary" address for display. ``sr-ctrl`` is the IS-05 connection
# management control — the same one the outbound proxy already uses.
_PREFERRED_CONTROL_URN_PREFIXES = (
    "urn:x-nmos:control:sr-ctrl/v1.",
    "urn:x-nmos:control:connection/v1.",
)


def device_address(device_resource: Any) -> str | None:
    """Extract the ``host:port`` a device serves its IS-05 API from.

    Looks through the device's IS-04 ``controls`` array for a
    connection-management control URN and returns the host:port of its
    ``href``. Returns ``None`` when the device didn't publish a
    matching control or the href is malformed.
    """
    if not isinstance(device_resource, dict):
        return None
    controls = device_resource.get("controls") or []
    if not isinstance(controls, list):
        return None

    candidates: list[tuple[str, str]] = []
    for ctrl in controls:
        if not isinstance(ctrl, dict):
            continue
        urn = ctrl.get("type")
        href = ctrl.get("href")
        if not isinstance(urn, str) or not isinstance(href, str):
            continue
        if any(urn.startswith(p) for p in _PREFERRED_CONTROL_URN_PREFIXES):
            candidates.append((urn, href))

    if not candidates:
        return None
    # Pick the highest-versioned URN for stability.
    candidates.sort(key=lambda p: p[0], reverse=True)
    from urllib.parse import urlsplit
    parts = urlsplit(candidates[0][1])
    if not parts.netloc:
        return None
    return parts.netloc
