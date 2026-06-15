# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Match a resource's current flow against its declared constraint sets.

The controller UI wants to show, on the capabilities page, which declared
constraint set (CS) the resource's **current flow** is operating in (rendered
in a green font). This module computes that match from the IS-04 flow JSON the
controller already caches.

Rather than re-deriving each flow property → capability mapping, it reuses the
canonical node-side converter
[get_flow_to_caps](../node/flow_caps.py) — the single source of truth that
already derives ``color_sampling`` from the flow's ``components`` and audio
``channel_count`` from the **source**. The controller has only JSON, so the
flow / source JSON is decoded into the generated polymorphic ``NFlow`` /
``NSource`` types and a tiny shim object (the only node attribute
``get_flow_to_caps`` reads is ``node.sources``) is passed in.

Everything degrades to "no match" (``FlowMatch(None, {})``) when MatroxCCF is
unavailable, the JSON can't be decoded, or a required field is missing — the
page still renders, just without the green highlight.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from fractions import Fraction
from types import SimpleNamespace
from typing import Any

from nmos.controller.compat import nmos_caps_json_to_ccf_caps

log = logging.getLogger(__name__)


def flow_match_key(value: Any) -> str:
    """Canonical, type-tagged string key for value-equality comparison.

    The configuration page must decide whether a constraint-set option
    (raw IS-04/BCP JSON: ``str`` / ``int`` / ``bool`` / rational
    ``{"numerator","denominator"}``) equals the flow's CURRENT value for
    that URN. But the flow value comes from the CCF flow CapSet (``str`` /
    ``int`` / ``bool`` / ``Fraction``). Mapping BOTH sides through this
    function yields a representation-independent key, so the **server**
    (render) and the **browser** (live SSE update) compare identically —
    the server stamps each option's key into the DOM and ships the flow's
    per-URN key over SSE; the client just string-compares.

    Rationals are reduced (``Fraction``) so ``{"numerator":48000,
    "denominator":1}`` and ``Fraction(48000, 1)`` collapse to the same key.
    The type tag prevents cross-type collisions (e.g. int ``2`` vs str
    ``"2"``).
    """
    if isinstance(value, bool):
        return f"b:{'true' if value else 'false'}"
    if isinstance(value, int):
        return f"i:{value}"
    if isinstance(value, Fraction):
        return f"r:{value.numerator}/{value.denominator}"
    if isinstance(value, float):
        fr = Fraction(value).limit_denominator(1_000_000)
        return f"r:{fr.numerator}/{fr.denominator}"
    if isinstance(value, dict) and "numerator" in value:
        num = value.get("numerator", 0)
        den = value.get("denominator", 1) or 1
        try:
            fr = Fraction(int(num), int(den))
            return f"r:{fr.numerator}/{fr.denominator}"
        except (ValueError, ZeroDivisionError, TypeError):
            return f"x:{value}"
    if isinstance(value, str):
        return f"s:{value}"
    return f"x:{value}"


def flow_value_keys(matched_values: dict[str, Any]) -> dict[str, str]:
    """Map a ``FlowMatch.matched_values`` dict (URN → CCF value) to URN →
    canonical key (see ``flow_match_key``). JSON-safe — used as the SSE
    ``flow_match.matched_values`` payload the configure page consumes."""
    return {urn: flow_match_key(v) for urn, v in matched_values.items()}


@dataclass(frozen=True)
class FlowMatch:
    """Result of matching a flow against a list of declared constraint sets.

    * ``matched_cs_index`` — the index (into the resource's
      ``caps.constraint_sets``) of the single most-specific CS the flow falls
      inside, or ``None`` when the flow matches no CS / is unknown.
    * ``matched_values`` — the flow's current value per capability URN (read
      off the converted flow CapSet, so it already includes derived
      ``color_sampling`` / ``channel_count``). Used by the configuration-page
      phase to highlight the matching multi-value option.
    """

    matched_cs_index: int | None = None
    matched_values: dict[str, Any] = field(default_factory=dict)


def flow_caps_from_json(
    flow_json: dict[str, Any] | None,
    source_json: dict[str, Any] | None,
) -> Any:
    """Convert an IS-04 flow JSON (+ its source, for audio) to a CCF CapSet.

    Reuses ``nmos.node.flow_caps.get_flow_to_caps``. The flow JSON is decoded
    into a polymorphic ``NFlow`` and the source JSON (when present) into an
    ``NSource``; ``get_flow_to_caps`` reads the source only as
    ``node.sources.get(source_id)`` → ``.get()`` → ``.value`` → ``.Channels``,
    which a decoded ``NSource`` satisfies, so a ``SimpleNamespace`` carrying a
    plain ``{source_id: NSource}`` dict is a sufficient stand-in for the node
    (``flow.source_id`` equals the source's IS-04 ``id`` — no static/dynamic
    resolution needed).

    Returns the ``CapSet`` or ``None`` on any failure (missing MatroxCCF,
    undecodable JSON, or — for audio — a missing source, which
    ``get_flow_to_caps`` asserts on).
    """
    if not isinstance(flow_json, dict):
        return None
    try:
        from nmos.node.flow_caps import get_flow_to_caps
        from nmos.types.generated.nflow import NFlow
        from nmos.types.generated.nsource import NSource
        from nmos.json.engine import JsonEngine
    except ImportError:
        return None

    try:
        nflow = NFlow()
        nflow.decode(JsonEngine(), flow_json)

        sources: dict[str, Any] = {}
        if isinstance(source_json, dict) and source_json.get("id"):
            nsrc = NSource()
            nsrc.decode(JsonEngine(), source_json)
            sources[source_json["id"]] = nsrc

        shim = SimpleNamespace(sources=sources)
        capset = get_flow_to_caps(shim, nflow)
    except Exception as exc:
        log.debug("flow→caps conversion failed: %s", exc)
        return None

    # get_flow_to_caps returns a plain dict ({}) when MatroxCCF is missing.
    if not hasattr(capset, "caps"):
        return None
    return capset


def _capset_value_count(capset: Any) -> int:
    """Total number of candidate values across a capset's caps — a proxy for
    'narrowness' used to break ties between equally-preferred matches (fewer
    candidates = more specific)."""
    total = 0
    for cap in capset.caps.values():
        vals = getattr(cap.value, "values", None)
        total += len(vals) if vals else 1
    return total


def flow_match_for_sender(
    flow_json: dict[str, Any] | None,
    source_json: dict[str, Any] | None,
    constraint_sets: list[dict[str, Any]] | None,
) -> FlowMatch:
    """Find the single most-specific declared CS the current flow is in.

    Among the constraint sets whose declared capability set *includes* the
    flow's operating point, the match is the one with the highest
    ``urn:x-nmos:cap:meta:preference`` (native = 100 = fully-pinned tip = most
    specific), breaking ties toward the narrower capset and then list order.

    Returns ``FlowMatch(None, {})`` when there is no flow, no constraint sets,
    no match, or MatroxCCF is unavailable.
    """
    if not isinstance(constraint_sets, list) or not constraint_sets:
        return FlowMatch()

    flow_capset = flow_caps_from_json(flow_json, source_json)
    if flow_capset is None:
        return FlowMatch()

    try:
        from caps.MatroxCCF import conset_included_in_capset
    except ImportError:
        return FlowMatch()

    try:
        flow_conset = flow_capset.to_conset()
    except Exception as exc:
        log.debug("flow capset → conset failed: %s", exc)
        return FlowMatch()

    # (preference, -value_count, -index) ranks candidates; higher is better.
    best: tuple[int, int, int] | None = None
    best_index: int | None = None

    for i, cs in enumerate(constraint_sets):
        if not isinstance(cs, dict):
            continue
        declared = nmos_caps_json_to_ccf_caps({"constraint_sets": [cs]})
        if declared is None or not getattr(declared, "capsets", None):
            continue
        declared_capset = declared.capsets[0]
        try:
            if not conset_included_in_capset(flow_conset, declared_capset):
                continue
        except Exception as exc:
            log.debug("inclusion test failed for CS %d: %s", i, exc)
            continue
        pref = int(getattr(declared_capset, "preference", 0) or 0)
        rank = (pref, -_capset_value_count(declared_capset), -i)
        if best is None or rank > best:
            best = rank
            best_index = i

    if best_index is None:
        return FlowMatch()

    # Read the flow's current value per URN off the converted CapSet, so
    # derived values (color_sampling, channel_count) are already included.
    matched_values: dict[str, Any] = {}
    for urn, cap in flow_capset.caps.items():
        vals = getattr(cap.value, "values", None)
        if vals:
            matched_values[urn] = vals[0]

    return FlowMatch(matched_cs_index=best_index, matched_values=matched_values)


# ---------------------------------------------------------------------------
# Receiver-scoped helpers (capabilities page, sender↔receiver pairing)
# ---------------------------------------------------------------------------
#
# On the receiver caps page the displayed CS are the sender∩receiver
# *negotiable intersection* (``filter_sender_cs_by_receiver``), re-indexed.
# The narrowing depends only on the two sides' DECLARED capabilities, not on
# the flow — an IS-11 constraint changes the flow (operating point), not
# ``caps.constraint_sets``. So the narrowed set is invariant across flow
# changes: narrow ONCE per pairing, then re-run only the inclusion check as the
# flow changes. The resulting index is an index into the narrowed list, which
# is exactly what the page rendered (``data-caps-row`` carries that index).


def narrowed_constraint_sets_for_pair(
    cache: Any, sender_id: str, receiver_id: str,
) -> list[dict[str, Any]] | None:
    """Return the sender↔receiver narrowed ``constraint_sets`` (the negotiable
    intersection the receiver caps page displays), or ``None``.

    Flow-independent — compute once per pairing and reuse across flow changes.
    ``cache`` is duck-typed (``get_sender`` / ``get_receiver``) to avoid an
    import cycle with ``nmos.controller.cache``.
    """
    try:
        from nmos.controller.compat import filter_sender_cs_by_receiver
    except ImportError:
        return None
    sender = cache.get_sender(sender_id)
    receiver = cache.get_receiver(receiver_id)
    if sender is None or receiver is None:
        return None
    try:
        narrowed = filter_sender_cs_by_receiver(sender, receiver)
    except Exception as exc:
        log.debug("narrowing failed for %s↔%s: %s", sender_id, receiver_id, exc)
        return None
    caps = narrowed.get("caps") if isinstance(narrowed, dict) else None
    cs = caps.get("constraint_sets") if isinstance(caps, dict) else None
    return cs if isinstance(cs, list) else None


def flow_match_index_for_sender(
    cache: Any, sender_id: str, constraint_sets: list[dict[str, Any]] | None,
) -> int | None:
    """Match the sender's CURRENT flow against ``constraint_sets`` and return
    the matched index (or ``None``).

    Resolves the chain freshly from the stable sender id —
    ``sender → flow_id → flow → source_id → source`` — then delegates to
    ``flow_match_for_sender``. Pass the pre-narrowed CS for a receiver caps page
    (index aligns with the narrowed rows) or the sender's own CS otherwise.
    ``cache`` is duck-typed (``get_sender`` / ``get_flow`` / ``get_source``).
    """
    sender = cache.get_sender(sender_id)
    if sender is None:
        return None
    flow = cache.get_flow(sender.get("flow_id", "") or "")
    source = None
    if isinstance(flow, dict):
        source = cache.get_source(flow.get("source_id", "") or "")
    return flow_match_for_sender(
        flow, source, constraint_sets,
    ).matched_cs_index
