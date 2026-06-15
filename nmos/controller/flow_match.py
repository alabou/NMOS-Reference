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


def _part_key(fmt: Any, layer: Any) -> str:
    """Canonical key for a flow 'part'. A standalone/trunk flow is
    ``"trunk"`` (CCF format/layer both None); a mux **sub-flow** is keyed by
    its ``(format, layer)`` — e.g. ``"urn:x-nmos:format:audio:0"``. Declared
    constraint sets carry the same part via ``cap:meta:format`` /
    ``cap:meta:layer``, so a CS is matched only against the operating point
    of its own part (CCF ``is_same_part``)."""
    if fmt is None and layer is None:
        return "trunk"
    return f"{fmt}:{layer}"


@dataclass(frozen=True)
class FlowMatch:
    """Result of matching a resource's CURRENT stream against its declared
    constraint sets — generalised to muxed streams.

    A mux is a tree: the **trunk** flow plus N **sub-flows** (one per
    ``(format, layer)``), each with its own source. So there can be several
    matched CS at once — the most-specific trunk CS *and* the most-specific
    sub-layer CS per layer. A non-mux flow yields at most one (the trunk).

    * ``matched_cs_indices`` — indices (into ``caps.constraint_sets``) of the
      CS to green: the most-specific match per part. Empty when nothing
      matches / unknown.
    * ``values_by_part`` — ``part_key`` → {capability URN → the flow's current
      value for that part} (off the converted CapSet, so derived
      ``color_sampling`` / ``channel_count`` are included). The config page
      greens the multi-value option whose value equals the flow value of the
      CS's own part.
    """

    matched_cs_indices: tuple[int, ...] = ()
    values_by_part: dict[str, dict[str, Any]] = field(default_factory=dict)


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


def _operating_capsets(
    cache: Any,
    flow_json: dict[str, Any] | None,
    source_json: dict[str, Any] | None,
) -> dict[str, Any]:
    """The resource's per-part operating points: ``part_key → CapSet``.

    Always the **trunk** (the flow itself). For a **mux** flow (``parents``
    is a list of sub-flow ids), also each sub-flow — resolved from the
    ``cache`` (``get_flow``/``get_source``): ``mux flow → parents[i] →
    sub-flow → source_id → sub-source``. Each sub-flow CapSet carries its
    ``(format, layer)`` (``get_flow_to_caps`` reads the sub-flow's
    ``FlowCore.Layer`` + concrete type), which becomes the part key.
    ``cache=None`` (or a non-mux flow) yields just the trunk.
    """
    out: dict[str, Any] = {}
    trunk = flow_caps_from_json(flow_json, source_json)
    if trunk is not None:
        out[_part_key(trunk.format, trunk.layer)] = trunk

    parents = flow_json.get("parents") if isinstance(flow_json, dict) else None
    if cache is not None and isinstance(parents, list):
        for pid in parents:
            sub_flow = cache.get_flow(pid) if pid else None
            if not isinstance(sub_flow, dict):
                continue
            sub_src = cache.get_source(sub_flow.get("source_id", "") or "")
            sub_caps = flow_caps_from_json(sub_flow, sub_src)
            if sub_caps is not None:
                out[_part_key(sub_caps.format, sub_caps.layer)] = sub_caps
    return out


def flow_match_for_sender(
    flow_json: dict[str, Any] | None,
    source_json: dict[str, Any] | None,
    constraint_sets: list[dict[str, Any]] | None,
    *,
    cache: Any = None,
) -> FlowMatch:
    """Find the most-specific declared CS per part the current stream is in.

    For each part (the trunk flow, plus each mux sub-flow when ``cache`` is
    supplied), among the constraint sets of that SAME part (CCF
    ``is_same_part`` on format/layer) whose capability set *includes* the
    part's operating point, pick the one with the highest
    ``urn:x-nmos:cap:meta:preference`` (native=100 = most specific),
    tie-breaking toward the narrower capset then list order. The result is
    the set of those per-part winners.

    ``cache`` enables the mux sub-flow chase; omit it for a plain trunk-only
    match. Returns an empty ``FlowMatch`` when there is no flow, no
    constraint sets, no match, or MatroxCCF is unavailable.
    """
    if not isinstance(constraint_sets, list) or not constraint_sets:
        return FlowMatch()

    ops = _operating_capsets(cache, flow_json, source_json)
    if not ops:
        return FlowMatch()

    try:
        from caps.MatroxCCF import conset_included_in_capset
    except ImportError:
        return FlowMatch()

    # Per part: the operating conset + the flow's current per-URN values.
    op_consets: dict[str, Any] = {}
    values_by_part: dict[str, dict[str, Any]] = {}
    for pk, capset in ops.items():
        try:
            op_consets[pk] = capset.to_conset()
        except Exception as exc:
            log.debug("flow capset → conset failed (%s): %s", pk, exc)
            continue
        vals: dict[str, Any] = {}
        for urn, cap in capset.caps.items():
            v = getattr(cap.value, "values", None)
            if v:
                vals[urn] = v[0]
        values_by_part[pk] = vals

    # Most-specific CS per part. (preference, -value_count, -index); higher wins.
    best: dict[str, tuple[tuple[int, int, int], int]] = {}
    for i, cs in enumerate(constraint_sets):
        if not isinstance(cs, dict):
            continue
        declared = nmos_caps_json_to_ccf_caps({"constraint_sets": [cs]})
        if declared is None or not getattr(declared, "capsets", None):
            continue
        declared_capset = declared.capsets[0]
        pk = _part_key(
            getattr(declared_capset, "format", None),
            getattr(declared_capset, "layer", None),
        )
        op = op_consets.get(pk)
        if op is None:
            continue  # no operating point for this CS's part
        try:
            if not conset_included_in_capset(op, declared_capset):
                continue
        except Exception as exc:
            log.debug("inclusion test failed for CS %d: %s", i, exc)
            continue
        pref = int(getattr(declared_capset, "preference", 0) or 0)
        rank = (pref, -_capset_value_count(declared_capset), -i)
        if pk not in best or rank > best[pk][0]:
            best[pk] = (rank, i)

    matched = tuple(sorted(entry[1] for entry in best.values()))
    return FlowMatch(matched_cs_indices=matched, values_by_part=values_by_part)


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


def flow_match_indices_for_sender(
    cache: Any, sender_id: str, constraint_sets: list[dict[str, Any]] | None,
) -> tuple[int, ...]:
    """Match the sender's CURRENT stream against ``constraint_sets`` and
    return the set of matched CS indices (per-part winners; see
    ``flow_match_for_sender``).

    Resolves the chain freshly from the stable sender id —
    ``sender → flow_id → flow → source_id → source`` (+ mux sub-flows via the
    cache). Pass the pre-narrowed CS for a receiver caps page (indices align
    with the narrowed rows) or the sender's own CS otherwise. ``cache`` is
    duck-typed (``get_sender`` / ``get_flow`` / ``get_source``).
    """
    sender = cache.get_sender(sender_id)
    if sender is None:
        return ()
    flow = cache.get_flow(sender.get("flow_id", "") or "")
    source = None
    if isinstance(flow, dict):
        source = cache.get_source(flow.get("source_id", "") or "")
    return flow_match_for_sender(
        flow, source, constraint_sets, cache=cache,
    ).matched_cs_indices
