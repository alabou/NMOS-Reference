# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Natural-group + device-serial helpers for the controller UI.

NMOS senders and receivers each declare their "natural group" via the
``urn:x-nmos:tag:grouphint/v1.0`` tag on their ResourceCore. The base
reference is ``specs/NMOS With Natural Groups.md`` §"Group Hint":

    ``"<group-name> <group-index>:<role-in-group> <role-index>"``

e.g. ``"RTP 3:VIDEO 0"`` — group "RTP 3", VIDEO format, role 0 (first
sender in that group). ``parse_group_hint`` relaxes this base form to
also accept non-conforming third-party devices (arbitrary group names,
``vid``/``aud``/``anc`` abbreviations, ``-``/``_`` role separators, a
trailing ``:device`` scope); see that function's docstring.

Two senders / receivers are members of the **same natural group** when:

  1. They belong to the same owning device (same ``device_id``), and
  2. They share the same group identity — the normalised text *before*
     the first ``:`` (``GroupHint.key``, e.g. ``"RTP 3"``).

Within a group, ``(format, role)`` identifies a member — a stereo audio
pair has role 0 (L) and role 1 (R). A hint whose role token is not a
recognised format is **not groupable**: it carries no ``format``/``role``
and its resource is left ungrouped.

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
# Matches "...SNXnnnnn..." — SNX-style serial form.
_SERIAL_RE = re.compile(r"\b(SNX\d{5,})\b")

# Spec §Format (line 53): the canonical <role-in-group> tokens. Per the
# spec the format MUST be one of these and comparison is case-insensitive;
# the role label SHOULD be uppercase. We store the canonical UPPERCASE form
# so leg-matching (`(format, role)`) and the lowercased cap:meta:format
# sites are stable regardless of input casing.
#
# To support non-conforming third-party devices we ALSO accept the common
# abbreviations seen in the field — "VID" for video, "AUD" for audio, and
# "ANC"/"ANCILLARY" for the ancillary-data role (the spec already allows
# "ANC" in place of "DATA", line 53). The key is normalised to the
# canonical token so members group and pair identically.
_FORMAT_SYNONYMS: dict[str, str] = {
    "VIDEO": "VIDEO", "VID": "VIDEO",
    "AUDIO": "AUDIO", "AUD": "AUDIO",
    "DATA": "DATA", "ANC": "DATA", "ANCILLARY": "DATA",
    "MUX": "MUX",
}

# Within the role segment (after the ':') some devices separate the
# role-in-group from the role-index with '-' or '_' instead of a space
# (e.g. "VIDEO-0", "AUDIO_1"). Normalise those to spaces before tokenising.
# We do this for the role segment ONLY — group names may legitimately
# contain '-'/'_' and we keep them verbatim for the group key.
_ROLE_SEP_RE = re.compile(r"[-_]+")

# Trailing decimal run of a group name — a best-effort group-index used
# ONLY for the USB/Thunderbolt "smallest natural_group_index wins"
# tie-break (handlers `_resource_group_index`); never for group identity.
# Defaults to a large sentinel when the group name carries no number so
# unnumbered groups sort last.
_TRAILING_INT_RE = re.compile(r"(\d+)\s*$")
_NO_GROUP_INDEX: int = 10**9


@dataclass(frozen=True)
class GroupHint:
    """Parsed ``grouphint/v1.0`` tag value.

    The spec form is ``"<group-name> <group-index>:<role-in-group> <role-index>"``.
    To interoperate with non-conforming third-party devices we relax the
    parse around that base form, and split hints into two tiers:

    * **groupable** — the role segment (after the ``:``) names a known
      format (``VIDEO``/``AUDIO``/``DATA``/``MUX``, incl. ``vid``/``aud``/
      ``anc`` abbreviations and any capitalisation). Such a hint yields a
      reliable ``format`` and ``role`` (the role-index, defaulting to 0 per
      spec line 57 when omitted), so its members can be grouped and paired.
    * **not groupable** — the role token is not a recognised format. We
      cannot derive a ``format``/``role`` (both ``None``); the resource is
      left ungrouped. ``role_name`` preserves the raw post-``:`` text so the
      UI can still display something in place of ``<role> <role-index>``.

    ``key`` is the **group identity**: the whole normalised text *before*
    the first ``:`` (e.g. ``"RTP 3"``). Per the user's direction the group
    index is not split out for identity — any two members sharing that
    string belong to the same natural group, which lets the group name be
    an arbitrary vendor string, not just a transport name.
    """

    group_name: str          # normalised text before ':' — the group identity
    role_name: str           # raw text after ':' (scope stripped) — for display
    groupable: bool
    format: str | None       # canonical VIDEO/AUDIO/DATA/MUX — only when groupable
    role: int | None         # role-index/layer (explicit or 0) — only when groupable

    @property
    def key(self) -> str:
        """Group identity — the normalised group-name string."""
        return self.group_name

    @property
    def leaf(self) -> tuple[str, int] | None:
        """The ``(format, role)`` leaf identity, or ``None`` when the hint
        isn't groupable.

        ``format`` and ``role`` are populated *only* for groupable hints, so
        every caller that wants the pair has to prove groupability first.
        Returning the pair as one optional makes that a single check the type
        checker can follow — reading the two fields separately after a
        ``groupable`` test leaves them ``str | None`` / ``int | None``, which
        is how ``(None, None)`` leaves could otherwise reach leaf sets and
        break their ``sorted()`` / dict-key contracts.
        """
        if not self.groupable or self.format is None or self.role is None:
            return None
        return (self.format, self.role)

    @property
    def group_index(self) -> int:
        """Best-effort trailing integer of the group name. Used ONLY for the
        USB/Thunderbolt tie-break (``_resource_group_index``); never for
        identity. ``_NO_GROUP_INDEX`` when the name carries no number."""
        m = _TRAILING_INT_RE.search(self.group_name)
        return int(m.group(1)) if m else _NO_GROUP_INDEX

    @property
    def role_label(self) -> str:
        """Human-readable role label for the member's row. Groupable hints
        render ``"<FORMAT> <role-index>"`` (e.g. ``"VIDEO 0"``); non-groupable
        hints render the raw ``role_name`` so the UI never shows a blank in
        place of ``<role> <role-index>``."""
        if self.groupable:
            return f"{self.format} {self.role}"
        return self.role_name

    def __str__(self) -> str:
        return f"{self.group_name}:{self.role_name}"


def _normalise_group_name(text: str) -> str:
    """Collapse internal whitespace and uppercase — a stable group key.

    All members of one group carry the same literal before-``:`` text, so a
    consistent normalisation keeps the key stable without needing to isolate
    a group-index. Uppercasing matches the spec's RECOMMENDED casing and the
    historical ``transport`` field (which was uppercased)."""
    return " ".join(text.split()).upper()


def parse_group_hint(value: str) -> GroupHint | None:
    """Parse a ``grouphint/v1.0`` tag value. Returns ``None`` when the value
    has no ``:`` separator (no group/role split to work with).

    The base reference is ``specs/NMOS With Natural Groups.md`` §"Group Hint".
    We relax around it for non-conforming devices:

    * The optional trailing ``:<group-scope>`` (e.g. ``":device"``, BCP-002-01)
      is stripped and ignored — the spec scope is always ``device``.
    * The group name (before the first ``:``) is accepted verbatim — any
      vendor string, not just a ``[A-Za-z]+`` transport name.
    * Within the role segment ``-``/``_`` are treated as the role separator.
    * The role-in-group is mapped through ``_FORMAT_SYNONYMS`` (incl.
      ``vid``/``aud``/``anc`` and any casing). A recognised format makes the
      hint **groupable** with a reliable ``role`` (explicit, or 0 per spec
      line 57 when omitted). An unrecognised role token makes the hint
      **not groupable**: ``format``/``role`` are ``None`` and ``role_name``
      carries the raw text for display only.
    """
    if not value:
        return None
    # Strip an optional trailing ":<scope>" (BCP-002-01). The structural
    # form is "<group>:<role>[:<scope>]"; anything past the second ':' is
    # the scope and is ignored.
    parts = value.split(":")
    if len(parts) < 2:
        return None  # no ':' → no group/role split
    group_raw = parts[0]
    role_raw = parts[1]

    group_name = _normalise_group_name(group_raw)
    role_name = role_raw.strip()
    if not group_name:
        return None  # nothing usable as a group identity

    # Tokenise the role segment: "<role-in-group> [<role-index>]", with
    # '-'/'_' accepted as the separator.
    role_tokens = _ROLE_SEP_RE.sub(" ", role_name).split()
    fmt: str | None = None
    if role_tokens:
        fmt = _FORMAT_SYNONYMS.get(role_tokens[0].upper())

    if fmt is None:
        # Role-in-group not recognised → not groupable; keep raw text only.
        return GroupHint(
            group_name=group_name,
            role_name=role_name,
            groupable=False,
            format=None,
            role=None,
        )

    # Groupable: role-index is the next numeric token, else 0 (spec line 57).
    role_index = 0
    if len(role_tokens) >= 2 and role_tokens[1].isdigit():
        role_index = int(role_tokens[1])
    return GroupHint(
        group_name=group_name,
        role_name=role_name,
        groupable=True,
        format=fmt,
        role=role_index,
    )


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
