# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Receiver ↔ sender capability intersection for the controller UI.

Wraps [nmos.node.compatibility.intersect_constraints_with_caps](
../node/compatibility.py) and the MatroxCCF JSON → Caps converter so
the controller can work with *remote* senders / receivers whose caps
arrive as JSON over the RDS Query / WebSocket API (rather than the
CCF ``Caps`` objects cached locally at pipeline build).

Public entry points:

  * ``nmos_caps_json_to_ccf_caps(caps_json)`` — shallow pass-through
    to `convert_caps_json_to_caps`, wrapping the "no caps" case so
    callers never have to guard against the import.

  * ``is_compatible(sender_caps, receiver_caps)`` — returns ``True``
    iff the intersection is non-empty. Stateless, side-effect free.

  * ``compatible_senders(receiver, candidates)`` — given a receiver
    JSON resource and a list of candidate sender JSON resources,
    returns the subset whose caps intersect the receiver's caps.

  * ``compatible_sender_groups(receiver_group, sender_candidates)`` —
    for the "natural group of receivers" selection; a sender group
    matches a receiver group when the role shapes match and every
    receiver role has a compatible sender role.

  * ``intersect_caps(sender_caps, receiver_caps)`` — returns the CCF
    ``Caps`` intersection (or ``None``); used by the caps picker
    page to render the constraint sets the user can choose from.

The module is careful to degrade gracefully when MatroxCCF is missing
(``ImportError``) — all functions return "no match" / "no caps" so
the controller UI still renders, it just can't filter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from nmos.controller.cache import GroupedResource, NaturalGroupView

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JSON → CCF Caps
# ---------------------------------------------------------------------------

def nmos_caps_json_to_ccf_caps(caps_json: Any) -> Any:
    """Convert an IS-04 ``caps`` dict to a MatroxCCF ``Caps`` object.

    Returns ``None`` when:
      * ``caps_json`` is falsy / not a dict,
      * MatroxCCF isn't importable,
      * the conversion itself raises (malformed caps shape).
    """
    if not isinstance(caps_json, dict):
        return None
    try:
        from caps.MatroxCCF import convert_caps_json_to_caps
    except ImportError:
        return None
    try:
        return convert_caps_json_to_caps(caps_json)
    except Exception as exc:
        log.debug("caps conversion failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Resource → Caps resolution
# ---------------------------------------------------------------------------

def resource_ccf_caps(resource: dict[str, Any]) -> Any:
    """Return CCF Caps for an IS-04 sender / receiver JSON resource.

    The caps are embedded under the top-level ``caps`` key per IS-04.
    For senders without declared caps (BCP-004-01 pre-adoption), the
    sender field may be missing or contain an empty dict; the helper
    returns ``None`` and the caller treats it as "no constraint
    guidance available" (silently-permissive behaviour in that case).
    """
    return nmos_caps_json_to_ccf_caps(resource.get("caps"))


# ---------------------------------------------------------------------------
# Intersection
# ---------------------------------------------------------------------------

def intersect_caps(sender_caps: Any, receiver_caps: Any) -> Any:
    """Intersect two CCF ``Caps``. Returns ``None`` on no overlap.

    Thin wrapper over the in-tree intersection helper so the controller
    doesn't depend on `nmos.node.compatibility` directly.
    """
    if sender_caps is None or receiver_caps is None:
        # Treat missing caps as "cannot say no" — the controller's
        # policy: if either end declares no caps we keep the pair in
        # the candidate list and let the user decide. Returning
        # ``None`` here would DROP the candidate; returning a truthy
        # "unknown" is the right answer. We use the sentinel
        # ``UNKNOWN_CAPS`` for this case.
        return UNKNOWN_CAPS
    try:
        from nmos.node.compatibility import intersect_constraints_with_caps
    except ImportError:
        return UNKNOWN_CAPS
    try:
        return intersect_constraints_with_caps(sender_caps, receiver_caps)
    except Exception as exc:
        log.debug("caps intersection failed: %s", exc)
        return None


# Sentinel returned when caps are missing on one side and we therefore
# cannot prove incompatibility. Treated as "compatible" by ``is_compatible``.
UNKNOWN_CAPS: Any = object()


def is_compatible(sender_caps: Any, receiver_caps: Any) -> bool:
    """``True`` iff the intersection is non-empty OR caps are unknown."""
    result = intersect_caps(sender_caps, receiver_caps)
    return result is not None  # ``None`` = proven empty; anything else passes.


# ---------------------------------------------------------------------------
# Transport-family compatibility
# ---------------------------------------------------------------------------

# Keyed on the sender's transport URN; each entry is the set of receiver
# transport URNs the sender is willing to negotiate with. ``rtp`` (no
# suffix) is the generic RTP mark — accepts either unicast or multicast
# on the other side; ``rtp.ucast`` and ``rtp.mcast`` are strict.
_TRANSPORT_ACCEPTS: dict[str, set[str]] = {
    # RTP family
    "urn:x-nmos:transport:rtp.ucast": {
        "urn:x-nmos:transport:rtp.ucast",
        "urn:x-nmos:transport:rtp",
    },
    "urn:x-nmos:transport:rtp.mcast": {
        "urn:x-nmos:transport:rtp.mcast",
        "urn:x-nmos:transport:rtp",
    },
    "urn:x-nmos:transport:rtp": {
        "urn:x-nmos:transport:rtp",
        "urn:x-nmos:transport:rtp.ucast",
        "urn:x-nmos:transport:rtp.mcast",
    },
    # RTP over TCP — exact match only.
    "urn:x-matrox:transport:rtp.tcp": {"urn:x-matrox:transport:rtp.tcp"},
    # MQTT / WebSocket / NDI — exact match.
    "urn:x-nmos:transport:mqtt":       {"urn:x-nmos:transport:mqtt"},
    "urn:x-nmos:transport:websocket":  {"urn:x-nmos:transport:websocket"},
    "urn:x-matrox:transport:ndi":      {"urn:x-matrox:transport:ndi"},
    # SRT
    "urn:x-matrox:transport:srt":           {
        "urn:x-matrox:transport:srt",
        "urn:x-matrox:transport:srt.mpeg2ts",
    },
    "urn:x-matrox:transport:srt.mpeg2ts":   {
        "urn:x-matrox:transport:srt",
        "urn:x-matrox:transport:srt.mpeg2ts",
    },
    "urn:x-matrox:transport:srt.rtp":       {"urn:x-matrox:transport:srt.rtp"},
    # USB / TCP — exact match.
    "urn:x-matrox:transport:usb":      {"urn:x-matrox:transport:usb"},
    "urn:x-matrox:transport:tcp":      {"urn:x-matrox:transport:tcp"},
    # UDP family
    "urn:x-matrox:transport:udp": {
        "urn:x-matrox:transport:udp",
        "urn:x-matrox:transport:udp.ucast",
        "urn:x-matrox:transport:udp.mcast",
    },
    "urn:x-matrox:transport:udp.ucast": {
        "urn:x-matrox:transport:udp",
        "urn:x-matrox:transport:udp.ucast",
    },
    "urn:x-matrox:transport:udp.mcast": {
        "urn:x-matrox:transport:udp",
        "urn:x-matrox:transport:udp.mcast",
    },
    # UDP/MPEG2-TS family
    "urn:x-matrox:transport:udp.mpeg2ts": {
        "urn:x-matrox:transport:udp.mpeg2ts",
        "urn:x-matrox:transport:udp.mpeg2ts.ucast",
        "urn:x-matrox:transport:udp.mpeg2ts.mcast",
    },
    "urn:x-matrox:transport:udp.mpeg2ts.ucast": {
        "urn:x-matrox:transport:udp.mpeg2ts",
        "urn:x-matrox:transport:udp.mpeg2ts.ucast",
    },
    "urn:x-matrox:transport:udp.mpeg2ts.mcast": {
        "urn:x-matrox:transport:udp.mpeg2ts",
        "urn:x-matrox:transport:udp.mpeg2ts.mcast",
    },
    # RTSP
    "urn:x-matrox:transport:rtsp":     {"urn:x-matrox:transport:rtsp"},
    "urn:x-matrox:transport:rtsp.tcp": {"urn:x-matrox:transport:rtsp.tcp"},
}


def transport_compatible(
    sender_transport: str | None, receiver_transport: str | None,
) -> bool:
    """Transport-family compatibility check. Returns ``True`` when
    the sender's transport URN will accept a receiver of the given
    transport URN per the compatibility matrix (e.g. generic ``rtp``
    accepts either unicast or multicast, but ``rtp.ucast`` won't
    accept ``rtp.mcast``).

    Missing / unknown URN on either side → ``False`` (fail-closed).
    Aligns with the project's fail-closed philosophy on undeclared
    transport.
    """
    if not sender_transport or not receiver_transport:
        return False
    allowed = _TRANSPORT_ACCEPTS.get(sender_transport)
    if allowed is None:
        return False
    return receiver_transport in allowed


def format_compatible(
    sender_format: str | None, receiver_format: str | None,
) -> bool:
    """Per-receiver format check. Simple URN equality — NMOS format
    URNs (``urn:x-nmos:format:video`` / ``audio`` / ``data`` / ``mux``)
    never overlap, so equality is the right test.

    Missing format on either side → ``False`` (fail-closed).
    """
    if not sender_format or not receiver_format:
        return False
    return sender_format == receiver_format


# ---------------------------------------------------------------------------
# Sender filtering (single receiver)
# ---------------------------------------------------------------------------

def compatible_senders(
    receiver: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the subset of ``candidates`` compatible with ``receiver``.

    Senders that don't declare caps are kept: never drop a candidate
    that might be compatible just because it hasn't published its
    caps.
    """
    r_caps = resource_ccf_caps(receiver)
    result: list[dict[str, Any]] = []
    for sender in candidates:
        s_caps = resource_ccf_caps(sender)
        if is_compatible(s_caps, r_caps):
            result.append(sender)
    return result


# ---------------------------------------------------------------------------
# Natural-group filtering (group of receivers)
# ---------------------------------------------------------------------------

def compatible_sender_groups(
    receiver_group: NaturalGroupView,
    sender_groups: list[NaturalGroupView],
) -> list[NaturalGroupView]:
    """Given a receiver natural group, find sender groups whose leaf
    shape matches and whose per-leaf caps intersect non-trivially.

    **Leaf shape** = the sorted list of ``(format, role)`` tuples across
    the group's members. Using the tuple — not role alone — means a
    MUX group ``(VIDEO 0, AUDIO 0, AUDIO 1)`` (tuples
    ``[("AUDIO", 0), ("AUDIO", 1), ("VIDEO", 0)]``) only matches a
    sender group with the same multi-format topology.
    """
    target_leaves = sorted(
        ((m.hint.format, m.hint.role) for m in receiver_group.members
         if m.hint is not None),
    )
    if not target_leaves:
        return []

    matched: list[NaturalGroupView] = []
    for sg in sender_groups:
        sg_leaves = sorted(
            ((m.hint.format, m.hint.role) for m in sg.members
             if m.hint is not None),
        )
        if sg_leaves != target_leaves:
            continue
        if _all_roles_compatible(receiver_group, sg):
            matched.append(sg)
    return matched


@dataclass
class SupersetMatch:
    """A sender natural group that covers a receiver subset.

    * ``group``           — the **full** sender ``NaturalGroupView``,
                            including any legs that aren't part of the
                            match (so the UI can render them greyed-
                            out for context).
    * ``matched_members`` — the subset of ``group.members`` whose
                            ``(format, role)`` leaf identity matches
                            a receiver leaf, ordered by the subset's
                            sorted leaf signature. One entry per
                            subset leaf.
    * ``matched_ids``     — convenience: the set of member IDs in
                            ``matched_members``, for template lookups
                            (``m.id in match.matched_ids``).
    """

    group: NaturalGroupView
    matched_members: list[GroupedResource] = field(default_factory=list)
    matched_ids: set[str] = field(default_factory=set)


def compatible_sender_groups_superset(
    receiver_subset: NaturalGroupView,
    sender_groups: list[NaturalGroupView],
) -> list[SupersetMatch]:
    """Find sender natural groups whose leaf signature is a multiset-
    superset of the receiver subset's signature, and where each subset
    leaf pairs with a compatible sender leaf.

    This is the entry point for **subset mode** in the controller UI:
    the operator ticked K of N receivers from one natural group, and
    we want to find sender groups that can cover those K legs — the
    sender group may have extra legs that the operator isn't
    interested in (a common case is a MUX sender with ``V+A+A`` being
    partially routed to a pair of audio receivers).

    The per-leaf compatibility rule is **identical** to the one used
    by ``compatible_sender_groups`` (strict group mode) and by
    ``_member_compatible_with_all`` (single mode): leaf
    ``(format, role_index)`` identity + format URN equality +
    transport-family compatibility + caps intersection. Subset mode
    only relaxes WHICH sender shapes are eligible, never HOW legs are
    compared.

    ``compatible_sender_groups`` (the strict, shape-equal matcher)
    is the special case of this function where ``sender_group``'s
    leaf count equals ``receiver_subset``'s leaf count.

    Returns one ``SupersetMatch`` per qualifying sender group, in the
    order the caller supplied them. The matched member list is
    ordered by the subset's sorted ``(format, role)`` signature so
    callers can pair sender[i] ↔ receiver[i] deterministically.
    """
    target_leaves = sorted(
        (m.hint.format, m.hint.role)
        for m in receiver_subset.members if m.hint is not None
    )
    if not target_leaves:
        return []

    results: list[SupersetMatch] = []
    for sg in sender_groups:
        sg_leaves: set[tuple[str, int]] = {
            (m.hint.format, m.hint.role)
            for m in sg.members if m.hint is not None
        }
        # Multiset-superset: every target leaf present in the sender.
        # Natural groups use role-index to disambiguate same-format
        # legs, so duplicates-by-identity can't occur — a plain set
        # suffices for the containment test.
        if not all(leaf in sg_leaves for leaf in target_leaves):
            continue
        # ``_all_roles_compatible`` returns ``False`` if any receiver
        # leaf lacks a matching sender leaf or if any pair fails
        # format/transport/caps — exactly the predicate we want. The
        # sender group is allowed to have EXTRA legs beyond the
        # subset; the helper only iterates receiver leaves, so extras
        # are silently ignored.
        if not _all_roles_compatible(receiver_subset, sg):
            continue
        s_by_leaf: dict[tuple[str, int], GroupedResource] = {
            (m.hint.format, m.hint.role): m
            for m in sg.members if m.hint is not None
        }
        matched = [s_by_leaf[leaf] for leaf in target_leaves]
        results.append(SupersetMatch(
            group=sg,
            matched_members=matched,
            matched_ids={m.id for m in matched},
        ))
    return results


# ---------------------------------------------------------------------------
# Pair-by-identity (senders ↔ receivers at caps / configure)
# ---------------------------------------------------------------------------

def pair_by_identity(
    senders: list[dict[str, Any]], receivers: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Pair each receiver with the sender that shares its
    ``(format, role_index)`` leaf, regardless of URL order.

    Returns a list of ``(sender, receiver)`` tuples in the order of
    the supplied ``receivers``. Raises ``ValueError`` on any of:

      * a receiver has no group hint (can't look up by leaf identity),
      * a receiver's leaf has no matching sender,
      * two senders in ``senders`` carry the same leaf,
      * a sender has no group hint (can't be indexed).

    Used by ``receivers_caps`` and ``receivers_configure`` to replace
    the earlier URL-order ``zip`` — that was brittle because a user's
    selection order (group radio vs individual checkboxes vs subset
    ticks) isn't guaranteed to match the receiver order. Identity-
    based pairing means the operator can't accidentally cross-wire
    audio-to-video by clicking checkboxes in a "wrong" order.
    """
    from nmos.controller.grouping import extract_group_hint

    s_by_leaf: dict[tuple[str, int], dict[str, Any]] = {}
    for s in senders:
        hint = extract_group_hint(s.get("tags"))
        if hint is None:
            raise ValueError(
                f"sender {s.get('id', '?')!r} has no group hint — "
                "cannot pair by identity",
            )
        key = (hint.format, hint.role)
        if key in s_by_leaf:
            raise ValueError(
                f"senders {s_by_leaf[key].get('id', '?')!r} and "
                f"{s.get('id', '?')!r} both carry leaf {key!r} — "
                "cannot pair by identity",
            )
        s_by_leaf[key] = s

    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for r in receivers:
        hint = extract_group_hint(r.get("tags"))
        if hint is None:
            raise ValueError(
                f"receiver {r.get('id', '?')!r} has no group hint — "
                "cannot pair by identity",
            )
        key = (hint.format, hint.role)
        sender = s_by_leaf.get(key)
        if sender is None:
            raise ValueError(
                f"receiver {r.get('id', '?')!r} leaf {key!r} has no "
                "matching sender in the supplied list",
            )
        pairs.append((sender, r))
    return pairs


def filter_sender_cs_by_receiver(
    sender: dict[str, Any], receiver: dict[str, Any],
) -> dict[str, Any]:
    """Narrow the sender's ``caps.constraint_sets`` by the receiver's
    caps and return a shallow-copied sender whose constraint sets
    represent the negotiable intersection.
    X = sender (Caps), Y = receiver (Cons via ``Caps.to_cons()``):

        sort sender capsets by preference DESC
        for each receiver conset Y:
            try ``caps_constrict_adjust_by_conset(sender_caps, Y)``;
              CCF handles the inner X loop: format/layer match,
              native rule, per-param intersection, first-match break.
            on success: overwrite label from Y;
              preference is already Y's.
            on failure: no sender X matched this Y — drop.

    MatroxCCF's ``CapSet.__getitem__`` returns ``RangeValue(infinite=True)``
    for a missing param, so "receiver-only param → kept in the
    result as receiver-narrowed" falls out of the algebra for free
    (INF ∩ Y = Y).

    Degradation (keep the page renderable): receiver has no caps,
    sender has no constraint_sets, caps conversion fails, or the
    CCF import is missing — return the sender unchanged.
    """
    try:
        from caps.MatroxCCF import (
            caps_constrict_adjust_by_conset,
            convert_caps_caps_to_json,
            Caps,
        )
    except ImportError:
        return sender

    r_caps = resource_ccf_caps(receiver)
    if r_caps is None:
        return sender  # UNKNOWN_CAPS — keep everything

    orig_any: Any = sender.get("caps") or {}
    if not isinstance(orig_any, dict):
        return sender
    orig: dict[str, Any] = orig_any
    sets_any: Any = orig.get("constraint_sets")
    if not isinstance(sets_any, list):
        return sender

    sender_caps = nmos_caps_json_to_ccf_caps({"constraint_sets": sets_any})
    if sender_caps is None:
        return sender
    sender_caps = sender_caps.get(no_filter=True)           # sort by preference DESC via CCF API
    receiver_cons = r_caps.to_cons()

    narrowed: list[Any] = []
    for y_conset in receiver_cons.consets:
        try:
            cs = caps_constrict_adjust_by_conset(sender_caps, y_conset)
        except Exception:
            continue                                        # no sender X matched this Y
        cs.label = y_conset.label                           # overwrite label from Y
        narrowed.append(cs)

    # For a multiplexed stream the mux must be valid —
    # drop everything unless at least one result is a trunk CS
    # (no format + no layer). Without a valid trunk the transport-
    # level config of the mux isn't negotiable; showing layer-only
    # results would mislead the operator into picking a pairing
    # that can't activate.
    if narrowed and not any(
        cs.format is None and cs.layer is None for cs in narrowed
    ):
        narrowed = []

    new_sender: dict[str, Any] = dict(sender)
    new_caps: dict[str, Any] = dict(orig)
    new_caps["constraint_sets"] = convert_caps_caps_to_json(
        Caps(capsets=narrowed),
    )["constraint_sets"]
    new_sender["caps"] = new_caps
    return new_sender


def _all_roles_compatible(
    receiver_group: NaturalGroupView,
    sender_group: NaturalGroupView,
) -> bool:
    """Per-leaf compatibility check for a matched role-shape group.

    Per-receiver check:

        receiver_format != sender_format ||
        !transport_compatible(senderTransport, receiverTransport) ||
        layerRole != senderLayerRole || layer != senderLayer
            → continue (drop pair)

    **Leaf identity is ``(format, role)`` — NOT role alone.**
    A natural group can have two members with the same role index but
    different formats (e.g. a MUX group with ``VIDEO 0`` and
    ``AUDIO 0`` — both at role 0, distinguished by format). Keying a
    role-map on role alone would collapse these into a single entry,
    silently losing a leaf. Tuple-keying on ``(hint.format, hint.role)``
    preserves each leaf via its ``(layerRole, layer)`` identity.

    Per-leaf checks:
      * **format equality** — top-level NMOS format URN must match
        (video URN ≠ audio URN; equality is the right test).
      * **transport compatibility** — sender's transport URN must
        accept the receiver's per the whitelist matrix.
      * **leaf format identity via hint** — defensive, since the
        tuple-key already enforces it (a pair keyed by the same
        ``hint.format`` can't diverge).
      * **caps intersection** — CCF algebra.
    """
    r_by_leaf: dict[tuple[str, int], GroupedResource] = {
        (m.hint.format, m.hint.role): m
        for m in receiver_group.members if m.hint is not None
    }
    s_by_leaf: dict[tuple[str, int], GroupedResource] = {
        (m.hint.format, m.hint.role): m
        for m in sender_group.members if m.hint is not None
    }
    for leaf_key, receiver in r_by_leaf.items():
        sender = s_by_leaf.get(leaf_key)
        if sender is None:
            return False
        # Format equality at the leaf.
        if not format_compatible(
            sender.resource.get("format"), receiver.resource.get("format"),
        ):
            return False
        # Transport compatibility.
        if not transport_compatible(
            sender.resource.get("transport"),
            receiver.resource.get("transport"),
        ):
            return False
        if not is_compatible(
            resource_ccf_caps(sender.resource),
            resource_ccf_caps(receiver.resource),
        ):
            return False
    return True
