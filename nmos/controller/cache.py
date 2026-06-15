# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""In-process cache of IS-04 resources for the controller UI.

The cache is fed by two producers:

  * ``rds_query.RdsQueryClient`` — one-shot bootstrap at startup.
  * ``rds_websocket.RdsWebSocketClient`` — long-lived subscriber that
    pushes add / update / remove deltas as the registry notifies us.

Consumers are:

  * HTTP handlers — read a snapshot per page request.
  * SSE pump — subscribes to ``status_changed`` events and forwards
    them to browsers that have opted in (``/api/status-events?ids=…``).

The cache holds each resource as a plain ``dict`` (the JSON body as
delivered by the query API / WebSocket grain). This keeps it simple
and keeps the cache decoupled from the generated NMOS types.

Status semantics (kept deliberately minimal for v1):

  * For a **sender**, ``status`` = ``{"active": bool, "master_enable":
    bool}`` where ``active`` is the ``subscription.active`` field the
    owning Node publishes.
  * For a **receiver**, same shape plus ``sender_id: str | None`` so
    the UI can show which sender is currently consumed.

Larger per-resource state (error counters, lock flags, etc.) is
deferred to the dedicated status-monitoring pages listed as out of
scope in the plan.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import time as _time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Literal

from nmos.controller.grouping import (
    GROUP_HINT_TAG,
    GroupHint,
    device_address,
    device_serial,
    extract_group_hint,
    strip_transport_prefix,
)

log = logging.getLogger(__name__)

# TEMP flow-match instrumentation — emits to the --debug-in-depth JSONL
# log (the dedicated "nmos.controller.debug_trace" logger). No-op when
# that logger has no handlers (debug tracing off), so production paths
# are unaffected. Remove once the live flow-match update is confirmed.
_dbg_logger = logging.getLogger("nmos.controller.debug_trace")


def _dbg(kind: str, **fields: Any) -> None:
    if not _dbg_logger.handlers:
        return
    try:
        _dbg_logger.info(
            _json.dumps(
                {"t": round(_time.time(), 6), "kind": kind, **fields},
                default=str,
            )
        )
    except Exception:
        pass


ResourceKind = Literal["node", "device", "flow", "sender", "receiver", "source"]

# The four facet dots shown on each sender / receiver row, plus the
# badge ("overall"). The UI uses short names — the mapping to the
# NMOS-With-Status-Reporting attribute names lives in
# ``_FACET_ATTR_BY_KIND`` below.
StatusFacet = Literal["overall", "link", "sync", "conn", "media"]


# ---------------------------------------------------------------------------
# NMOS With Status Reporting — integer status decode
# ---------------------------------------------------------------------------
#
# Per ``specs/NMOS With Status Reporting.md`` line 93, each ``*_status``
# attribute on a monitoring Source's ``monitor_state`` object is a
# non-negative integer with the following meaning (BCP-008-01 / -02
# vocabulary, unified into one palette for UI rendering):
#
#     0 → Inactive (or NotUsed for synchronization_status)
#     1 → Healthy  (AllUp     in BCP-008-01 link_status terms)
#     2 → PartiallyHealthy    (SomeDown)
#     3 → Unhealthy           (AllDown)
#
# The UI uses lowercase-dash names (``inactive`` / ``not-used`` /
# ``healthy`` / ``partially-healthy`` / ``unhealthy``) so they map
# directly to ``is-<name>`` CSS classes. Only the ``sync`` facet
# renders value 0 as ``not-used``; every other facet renders 0 as
# ``inactive``. Both display as grey dots but are kept semantically
# distinct for future tooltip / text labels.

_STATUS_ZERO_BY_FACET: dict[StatusFacet, str] = {
    "overall": "inactive",
    "link":    "inactive",
    "conn":    "inactive",   # transmission_status (sender) / connection_status (receiver)
    "media":   "inactive",   # essence_status     (sender) / stream_status     (receiver)
    "sync":    "not-used",
}

_STATUS_NONZERO: dict[int, str] = {
    1: "healthy",
    2: "partially-healthy",
    3: "unhealthy",
}


def decode_status_code(code: Any, facet: StatusFacet) -> str:
    """Translate an integer ``monitor_state.*_status`` value into the UI
    status name. Unknown / non-integer values fall back to the facet's
    zero sentinel so the UI can always render a grey dot.
    """
    if code is None:
        return _STATUS_ZERO_BY_FACET[facet]
    try:
        i = int(code)
    except (TypeError, ValueError):
        return _STATUS_ZERO_BY_FACET[facet]
    if i == 0:
        return _STATUS_ZERO_BY_FACET[facet]
    return _STATUS_NONZERO.get(i, _STATUS_ZERO_BY_FACET[facet])


# UI facet → spec `monitor_state` attribute, per monitored kind.
# Senders vs. receivers carry different attributes for two facets
# (conn → transmission/connection; media → essence/stream) per the
# spec's table at line 116.
_FACET_ATTR_BY_KIND: dict[str, dict[StatusFacet, str]] = {
    "sender": {
        "overall": "overall_status",
        "link":    "link_status",
        "sync":    "synchronization_status",
        "conn":    "transmission_status",
        "media":   "essence_status",
    },
    "receiver": {
        "overall": "overall_status",
        "link":    "link_status",
        "sync":    "synchronization_status",
        "conn":    "connection_status",
        "media":   "stream_status",
    },
}


def _monitor_sibling_of(
    source: dict[str, Any],
) -> tuple[str, ResourceKind] | tuple[None, None]:
    """Return ``(sibling_id, sibling_kind)`` if ``source`` is a valid
    NMOS With Status Reporting monitor, else ``(None, None)``.

    A monitor Source has ``format == "urn:x-nmos:format:data"``,
    ``monitor_type`` of ``"sender"`` or ``"receiver"``, and a
    ``monitor_sibling_id`` naming the sender/receiver it monitors.
    """
    if not isinstance(source, dict):
        return (None, None)
    if source.get("format") != "urn:x-nmos:format:data":
        return (None, None)
    kind = source.get("monitor_type")
    if kind == "sender" or kind == "receiver":
        sib = source.get("monitor_sibling_id")
        if isinstance(sib, str) and sib:
            # kind is narrowed to Literal["sender","receiver"] here,
            # which is a subset of ResourceKind.
            return (sib, kind)
    return (None, None)


def extract_monitor_state(
    monitor_source: dict[str, Any],
) -> dict[str, Any] | None:
    """Project a monitoring Source resource into the UI's status dict.

    Input: an IS-04 Source resource that satisfies
    ``NMOS With Status Reporting`` — ``format=urn:x-nmos:format:data``,
    ``monitor_type`` ∈ {"sender","receiver"}, ``monitor_state`` object
    present with the per-kind attributes from the spec's table.

    Output: the same shape produced by ``extract_status`` — keys
    ``overall``, ``link``, ``sync``, ``conn``, ``media`` — so the cache
    can swap the placeholder dict in ``_status`` for the real values
    without the templates / SSE code noticing. Returns ``None`` if the
    source isn't a valid monitor source.
    """
    if not isinstance(monitor_source, dict):
        return None
    if monitor_source.get("format") != "urn:x-nmos:format:data":
        return None
    kind = monitor_source.get("monitor_type")
    if kind not in ("sender", "receiver"):
        return None
    state = monitor_source.get("monitor_state") or {}
    if not isinstance(state, dict):
        return None

    attr_map = _FACET_ATTR_BY_KIND[kind]
    result: dict[str, Any] = {}
    for facet, attr in attr_map.items():
        result[facet] = decode_status_code(state.get(attr), facet)
    msg = state.get("overall_message")
    if isinstance(msg, str) and msg:
        result["overall_message"] = msg
    return result


# ---------------------------------------------------------------------------
# Status extraction
# ---------------------------------------------------------------------------

def extract_status(
    kind: ResourceKind,
    resource: dict[str, Any],
    monitor_source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pull status-fields-of-interest out of an IS-04 sender/receiver dict.

    Status values follow the BCP-008 status-monitor vocabulary, kept as
    lowercase-with-dashes strings so the CSS/JS can map them directly
    to ``is-<value>`` class names:

    =========================  ======
    ``inactive``               grey
    ``healthy``                green
    ``partially-healthy``      yellow
    ``unhealthy``              red
    ``not-used``               grey
    =========================  ======

    When a ``monitor_source`` is provided (the sibling "NMOS With Status
    Reporting" Source whose ``monitor_sibling_id`` points at this
    sender/receiver), the four per-facet values come from its
    ``monitor_state`` via ``extract_monitor_state``. Without a monitor
    source, the four facets mirror ``overall`` as before.

    Returned keys:

    * ``active``   — ``subscription.active`` (bool).
    * ``peer_id``  — subscription peer id (receiver_id for senders,
                     sender_id for receivers); ``None`` if not set.
    * ``overall``  — monitor's ``overall_status`` when a monitor is
                     present; else ``"healthy"`` when active,
                     ``"inactive"`` otherwise.
    * ``link`` / ``sync`` / ``conn`` / ``media``
                   — monitor's per-facet statuses when a monitor is
                     present; else all four mirror ``overall``.

    Anything that isn't a sender or receiver returns an empty dict.
    """
    if kind not in ("sender", "receiver"):
        return {}

    sub = resource.get("subscription") or {}
    active = bool(sub.get("active", False))
    peer_key = "receiver_id" if kind == "sender" else "sender_id"
    base: dict[str, Any] = {
        "active":  active,
        "peer_id": sub.get(peer_key),
    }

    monitor_state = (
        extract_monitor_state(monitor_source) if monitor_source is not None else None
    )
    if monitor_state is not None:
        # Real BCP-008 values — ``extract_monitor_state`` already
        # returned the five facet keys keyed to our UI vocabulary.
        base.update(monitor_state)
        return base

    # Placeholder — subscription-based approximation, used until an
    # IS-04 monitor Source lands in the cache for this resource.
    overall = "healthy" if active else "inactive"
    base.update({
        "overall": overall,
        "link":    overall,
        "sync":    overall,
        "conn":    overall,
        "media":   overall,
    })
    return base


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StatusChanged:
    """Emitted by the cache whenever a sender / receiver's status changes.

    ``flow_match`` is an OPTIONAL, additive payload carried on events that
    were triggered by a flow change (the new ``flow`` branch of ``upsert``).
    When present it is ``{"matched_cs_index": int | None}`` — the index of
    the declared constraint set the resource's current flow now sits inside,
    so the capabilities page can move the green highlight without a reload.
    It defaults to ``None`` on every existing (status-only) construction, so
    no existing emitter or consumer is affected.
    """

    kind: ResourceKind
    resource_id: str
    status: dict[str, Any]
    flow_match: dict[str, Any] | None = None


StatusListener = Callable[[StatusChanged], None]


# ---------------------------------------------------------------------------
# Grouped views
# ---------------------------------------------------------------------------

@dataclass
class GroupedResource:
    """A sender or receiver in a grouped view, pre-decorated with the
    parsed group hint and the owning device's serial.
    """

    id: str
    label: str
    description: str
    device_id: str
    device_serial: str
    device_label: str
    hint: GroupHint | None
    resource: dict[str, Any]
    status: dict[str, Any] = field(default_factory=dict)


@dataclass
class NaturalGroupView:
    """A natural group shared by senders / receivers on the same device.

    ``hint_key`` is ``(transport, group_index)`` — the natural-group
    identifier. Members of the group may be of different formats (AUDIO
    + VIDEO + MUX …) and each member carries its own ``(format, role)``
    pair via its ``hint``.
    """

    device_id: str
    device_serial: str
    device_label: str
    hint_key: tuple[str, int]  # (transport, group_index)
    members: list[GroupedResource] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        transport, group_index = self.hint_key
        return f"{transport} {group_index}"


@dataclass
class DeviceView:
    """All natural groups for one device.

    ``device_address`` carries the ``host:port`` the remote Node serves
    its IS-05 / IS-11 APIs from (extracted from the device's
    ``sr-ctrl/v1.x`` control href). ``transports`` is the sorted set of
    distinct transport labels (e.g. ``["RTP"]`` or ``["RTP", "SRT"]``)
    derived from the device's natural groups — mixed-transport devices
    show the full set in the header.
    """

    device_id: str
    device_serial: str
    device_label: str
    device_address: str = ""
    transports: list[str] = field(default_factory=list)
    groups: list[NaturalGroupView] = field(default_factory=list)
    ungrouped: list[GroupedResource] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ResourceCache
# ---------------------------------------------------------------------------

class ResourceCache:
    """Thread-safe resource cache, event-emitting on status changes.

    All public methods are coroutine-safe (single event loop); the
    cache holds an ``asyncio.Lock`` around mutations so a snapshot
    read is always consistent.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._nodes: dict[str, dict[str, Any]] = {}
        self._devices: dict[str, dict[str, Any]] = {}
        self._senders: dict[str, dict[str, Any]] = {}
        self._receivers: dict[str, dict[str, Any]] = {}
        self._flows: dict[str, dict[str, Any]] = {}
        self._sources: dict[str, dict[str, Any]] = {}
        self._status: dict[str, dict[str, Any]] = {}  # id → status dict
        # id → last computed flow-match payload ({"matched_cs_index": …}).
        # Additive: populated only by the ``flow`` branch of ``upsert``;
        # read by handlers as a fallback when rendering the caps page.
        self._flow_match: dict[str, dict[str, Any]] = {}
        self._listeners: set[StatusListener] = set()

    # ------------------------------------------------------------------
    # Listener registration (SSE uses this)
    # ------------------------------------------------------------------

    def add_status_listener(self, listener: StatusListener) -> None:
        self._listeners.add(listener)

    def remove_status_listener(self, listener: StatusListener) -> None:
        self._listeners.discard(listener)

    def _fire(self, event: StatusChanged) -> None:
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:
                log.exception("status listener raised")

    # ------------------------------------------------------------------
    # Mutation API (called by rds_query / rds_websocket)
    # ------------------------------------------------------------------

    async def upsert(self, kind: ResourceKind, resource: dict[str, Any]) -> None:
        """Insert or update a resource. Emits a ``status_changed`` event
        when the derived status for a sender / receiver changes. A
        monitor Source upsert also triggers a re-derivation of its
        sibling's status — the controller recomputes the
        sender/receiver status whenever the monitor Source ticks.
        """
        rid = resource.get("id")
        if not isinstance(rid, str) or not rid:
            return

        events: list[StatusChanged] = []
        async with self._lock:
            store = self._store_for(kind)
            prev = store.get(rid)
            store[rid] = resource

            if kind in ("sender", "receiver"):
                # Primary path: this resource is a sender/receiver —
                # recompute ITS status using its (possibly-present)
                # sibling monitor Source.
                mon = self._find_monitor_source_locked(rid)
                new_status = extract_status(kind, resource, monitor_source=mon)
                status_changed = self._status.get(rid) != new_status
                if status_changed:
                    self._status[rid] = new_status

                # Did the sender repoint to a different flow? On nodes that
                # publish a new flow id per constraint this is the reliable
                # "the flow changed" signal, and it fires even when the
                # sender's own full-caps matched index is unchanged (the
                # receiver caps page narrows the CS, so its match can move
                # while the sender's does not — receiver SSE connections
                # recompute against the narrowed set on this event). Nodes
                # that mutate in place instead trigger the flow/source
                # branches (with ``force`` on a content/version change).
                flow_id_changed = (
                    kind == "sender"
                    and (prev or {}).get("flow_id") != resource.get("flow_id")
                )

                # Flow-match recompute, keyed on the STABLE sender id.
                # Node implementations differ in how a constraint changes a
                # stream: some publish a NEW flow (new id) and repoint the
                # sender's ``flow_id`` (so the SENDER update carries the
                # change); others mutate the flow/source in place under the
                # same id (so a FLOW/SOURCE update carries it). The
                # controller supports both by re-resolving the whole chain
                # — sender → flow_id → flow → source_id → source — from the
                # stable sender id on EACH of those upserts (this sender
                # branch + the flow/source branches below). A sender's
                # status may be unchanged across the change, so we fire on
                # a status change OR a flow-match change.
                fm_payload: dict[str, Any] | None = None
                fm_changed = False
                if kind == "sender":
                    fm_payload, fm_changed = (
                        self._recompute_sender_flow_match_locked(rid)
                    )
                    _dbg(
                        "sender_upsert", sid=rid,
                        flow_id=resource.get("flow_id"),
                        matched_cs_index=(fm_payload or {}).get(
                            "matched_cs_index"),
                        fm_changed=fm_changed,
                    )

                # Attach flow_match when the match changed OR the flow id
                # repointed (so receiver caps SSE connections recompute
                # their narrowed index even if the sender's own index held).
                fire_fm = bool(fm_changed or flow_id_changed)
                if status_changed or fire_fm:
                    events.append(StatusChanged(
                        kind=kind, resource_id=rid, status=new_status,
                        flow_match=fm_payload if fire_fm else None,
                    ))
                if kind == "sender" and fire_fm:
                    events.extend(
                        self._receiver_flow_match_events_locked(rid, fm_payload)
                    )
            elif kind == "source":
                # Monitor Source upsert: if it claims to monitor a
                # known sender/receiver, recompute THAT sibling's
                # status and emit the event against its id (NOT the
                # source's). Non-monitor sources are stored but
                # don't trigger anything.
                sibling_id, sibling_kind = _monitor_sibling_of(resource)
                if (
                    sibling_id
                    and sibling_kind in ("sender", "receiver")
                ):
                    sibling_store = self._store_for(sibling_kind)
                    sibling = sibling_store.get(sibling_id)
                    if sibling is not None:
                        new_status = extract_status(
                            sibling_kind, sibling, monitor_source=resource,
                        )
                        if self._status.get(sibling_id) != new_status:
                            self._status[sibling_id] = new_status
                            events.append(StatusChanged(
                                kind=sibling_kind,
                                resource_id=sibling_id,
                                status=new_status,
                            ))
                # A MEDIA source (channels, clock, …) feeds the flow-match
                # for any sender whose CURRENT flow references it (audio
                # channel_count is derived from the source). Re-resolve
                # from each sender and recompute. ``force`` when the source
                # CONTENT changed (in-place-mutating nodes) so receiver caps
                # pages re-evaluate their narrowed match even if the
                # sender's full-caps match is unchanged.
                source_changed = (
                    prev is None
                    or (prev or {}).get("version") != resource.get("version")
                )
                events.extend(
                    self._flow_match_events_for_source_locked(
                        rid, force=source_changed,
                    )
                )
            elif kind == "flow":
                # A flow upsert affects the flow-match of every sender
                # whose CURRENT ``flow_id`` points at it. Re-resolve from
                # the sender (the stable id) and recompute. ``force`` when
                # the flow CONTENT changed (in-place-mutating nodes; some
                # nodes mutate a flow's properties under the same id rather
                # than publishing a new flow id) so receiver caps pages
                # re-evaluate their narrowed match. Wrapped so any failure
                # is a silent no-op — never breaks the flow upsert.
                flow_changed = (
                    prev is None
                    or (prev or {}).get("version") != resource.get("version")
                )
                _dbg(
                    "flow_upsert", flow_id=rid,
                    media_type=resource.get("media_type"),
                    version=resource.get("version"), changed=flow_changed,
                )
                try:
                    events.extend(
                        self._flow_match_events_for_flow_locked(
                            rid, force=flow_changed,
                        )
                    )
                except Exception:
                    log.exception("flow-match recompute failed")
                    _dbg("flow_match_error", flow_id=rid)

        for ev in events:
            self._fire(ev)

    def _recompute_sender_flow_match_locked(
        self, sid: str,
    ) -> tuple[dict[str, Any] | None, bool]:
        """Recompute one sender's flow-match from its STABLE id.

        Resolves the full chain freshly — ``sender → flow_id → flow →
        source_id → source`` — and matches the current flow against the
        sender's declared constraint sets. Caches the result under the
        sender id and returns ``(payload, changed)`` where ``payload`` is
        ``{"matched_cs_index": int | None}`` (or ``None`` when the sender
        is unknown / has no caps), and ``changed`` is True when the
        payload differs from the previously cached one.

        Must be called with ``self._lock`` held.
        """
        from nmos.controller.flow_match import (
            flow_match_for_sender, flow_value_keys,
        )

        sender = self._senders.get(sid)
        if sender is None:
            return None, False
        caps = sender.get("caps")
        constraint_sets = (
            caps.get("constraint_sets") if isinstance(caps, dict) else None
        )
        if not constraint_sets:
            return None, False

        flow = self._flows.get(sender.get("flow_id", "") or "")
        source = None
        if isinstance(flow, dict):
            source = self._sources.get(flow.get("source_id", "") or "")

        match = flow_match_for_sender(flow, source, constraint_sets)
        payload = {
            "matched_cs_index": match.matched_cs_index,
            # Canonical per-URN keys of the flow's current operating point
            # — the configuration page greens the multi-value option whose
            # value matches. Part of the payload so a value change that
            # doesn't move the CS index still fires (configure page needs it).
            "matched_values": flow_value_keys(match.matched_values),
        }
        changed = self._flow_match.get(sid) != payload
        self._flow_match[sid] = payload
        return payload, changed

    def _receiver_flow_match_events_locked(
        self, sender_id: str, payload: dict[str, Any] | None,
    ) -> list[StatusChanged]:
        """Mirror a sender's flow-match onto receivers currently
        subscribed to it (best-effort — a receiver's caps list may be
        narrowed/re-indexed, so live refresh of a receiver page is not
        guaranteed pixel-exact). Must hold ``self._lock``."""
        if payload is None:
            return []
        out: list[StatusChanged] = []
        for rid_r, recv in self._receivers.items():
            sub = recv.get("subscription") or {}
            if sub.get("sender_id") != sender_id:
                continue
            self._flow_match[rid_r] = payload
            out.append(StatusChanged(
                kind="receiver", resource_id=rid_r,
                status=self._status.get(rid_r, {}), flow_match=payload,
            ))
        return out

    def _flow_match_events_for_flow_locked(
        self, flow_id: str, *, force: bool = False,
    ) -> list[StatusChanged]:
        """Recompute + emit flow-match events for every sender whose
        CURRENT ``flow_id`` equals ``flow_id``. Must hold ``self._lock``.

        ``force`` fires even when a sender's OWN (full-caps) matched index
        is unchanged — needed for nodes that **mutate a flow in place** (same
        id, new content): a receiver caps page narrows the CS, so its match
        can move while the sender's full-caps match holds. Receiver SSE
        connections recompute their narrowed index on this event.
        """
        events: list[StatusChanged] = []
        emitted = 0
        for sid, sender in self._senders.items():
            if sender.get("flow_id") != flow_id:
                continue
            payload, changed = self._recompute_sender_flow_match_locked(sid)
            if (changed or force) and payload is not None:
                events.append(StatusChanged(
                    kind="sender", resource_id=sid,
                    status=self._status.get(sid, {}), flow_match=payload,
                ))
                events.extend(
                    self._receiver_flow_match_events_locked(sid, payload)
                )
                emitted += 1
        _dbg("flow_match_events", flow_id=flow_id, senders_changed=emitted,
             total=len(events), force=force)
        return events

    def _flow_match_events_for_source_locked(
        self, source_id: str, *, force: bool = False,
    ) -> list[StatusChanged]:
        """Recompute + emit flow-match events for every sender whose
        CURRENT flow references ``source_id``. Must hold ``self._lock``.
        ``force`` as in ``_flow_match_events_for_flow_locked`` — needed for
        nodes that mutate a Source in place (e.g. channel_count change)."""
        events: list[StatusChanged] = []
        for sid, sender in self._senders.items():
            flow = self._flows.get(sender.get("flow_id", "") or "")
            if not isinstance(flow, dict):
                continue
            if flow.get("source_id") != source_id:
                continue
            payload, changed = self._recompute_sender_flow_match_locked(sid)
            if (changed or force) and payload is not None:
                events.append(StatusChanged(
                    kind="sender", resource_id=sid,
                    status=self._status.get(sid, {}), flow_match=payload,
                ))
                events.extend(
                    self._receiver_flow_match_events_locked(sid, payload)
                )
        return events

    async def remove(self, kind: ResourceKind, resource_id: str) -> None:
        """Remove a resource. When a monitor Source is removed, the
        sibling sender/receiver's status falls back to the
        subscription-based placeholder — re-derive and fire.
        """
        events: list[StatusChanged] = []
        async with self._lock:
            store = self._store_for(kind)
            removed = store.pop(resource_id, None)
            if kind in ("sender", "receiver"):
                self._status.pop(resource_id, None)
            elif kind == "source" and removed is not None:
                sibling_id, sibling_kind = _monitor_sibling_of(removed)
                if (
                    sibling_id
                    and sibling_kind in ("sender", "receiver")
                ):
                    sibling = self._store_for(sibling_kind).get(sibling_id)
                    if sibling is not None:
                        new_status = extract_status(
                            sibling_kind, sibling, monitor_source=None,
                        )
                        if self._status.get(sibling_id) != new_status:
                            self._status[sibling_id] = new_status
                            events.append(StatusChanged(
                                kind=sibling_kind,
                                resource_id=sibling_id,
                                status=new_status,
                            ))
        for ev in events:
            self._fire(ev)

    async def replace_all(
        self,
        kind: ResourceKind,
        resources: list[dict[str, Any]],
    ) -> None:
        """Bootstrap: install a full snapshot for one kind.

        When replacing senders or receivers, recompute their statuses
        using whatever monitor Sources are already in the cache —
        monitor Sources may have been delivered ahead of their sibling.
        When replacing sources, also recompute every sender/receiver
        whose monitor Source might have appeared, disappeared, or
        changed, since the snapshot may change the sibling set.
        """
        status_updates: list[StatusChanged] = []
        async with self._lock:
            store = self._store_for(kind)
            store.clear()
            if kind in ("sender", "receiver"):
                # Purge stale status entries whose id is not in the new set.
                new_ids = {r.get("id") for r in resources if r.get("id")}
                for sid in list(self._status.keys()):
                    if sid not in new_ids and sid in store:
                        self._status.pop(sid, None)

            for r in resources:
                rid = r.get("id")
                if not isinstance(rid, str) or not rid:
                    continue
                store[rid] = r
                if kind in ("sender", "receiver"):
                    mon = self._find_monitor_source_locked(rid)
                    new_status = extract_status(kind, r, monitor_source=mon)
                    prev_status = self._status.get(rid)
                    if prev_status != new_status:
                        self._status[rid] = new_status
                        status_updates.append(
                            StatusChanged(kind=kind, resource_id=rid, status=new_status)
                        )

            if kind == "source":
                # A source snapshot can add/remove monitors for any
                # sender/receiver — re-derive every sibling's status.
                for sibling_kind in ("sender", "receiver"):
                    sibling_store = self._store_for(sibling_kind)
                    for sib_id, sib in sibling_store.items():
                        mon = self._find_monitor_source_locked(sib_id)
                        new_status = extract_status(
                            sibling_kind, sib, monitor_source=mon,
                        )
                        if self._status.get(sib_id) != new_status:
                            self._status[sib_id] = new_status
                            status_updates.append(StatusChanged(
                                kind=sibling_kind,
                                resource_id=sib_id,
                                status=new_status,
                            ))

        for ev in status_updates:
            self._fire(ev)

    def _find_monitor_source_locked(
        self, resource_id: str,
    ) -> dict[str, Any] | None:
        """Return the Source whose ``monitor_sibling_id`` equals the
        given sender/receiver id, or ``None`` if none.

        Must be called with ``self._lock`` held (hence ``_locked``).
        """
        for src in self._sources.values():
            sibling_id, _kind = _monitor_sibling_of(src)
            if sibling_id == resource_id:
                return src
        return None

    def _store_for(self, kind: ResourceKind) -> dict[str, dict[str, Any]]:
        if kind == "node":
            return self._nodes
        if kind == "device":
            return self._devices
        if kind == "sender":
            return self._senders
        if kind == "receiver":
            return self._receivers
        if kind == "flow":
            return self._flows
        if kind == "source":
            return self._sources
        raise ValueError(f"unsupported resource kind: {kind}")

    # ------------------------------------------------------------------
    # Read API (used by handlers)
    # ------------------------------------------------------------------

    def get_sender(self, sender_id: str) -> dict[str, Any] | None:
        """Return the sender resource, with ``format`` resolved from
        the referenced flow when the sender's own field is missing.

        IS-04 senders inherit ``format`` from their flow, so a
        registry-published sender can legitimately carry
        ``format: null`` at the top level. Every consumer of this
        method (compat filter, caps pairing, handlers) expects a
        non-null URN for format checks — resolve it here so every
        call site sees a consistent view. The shallow copy avoids
        mutating the stored dict.
        """
        s = self._senders.get(sender_id)
        if s is None:
            return None
        if not s.get("format"):
            flow = self._flows.get(s.get("flow_id", "") or "")
            if flow is not None and flow.get("format"):
                return {**s, "format": flow["format"]}
        return s

    def get_receiver(self, receiver_id: str) -> dict[str, Any] | None:
        return self._receivers.get(receiver_id)

    def get_device(self, device_id: str) -> dict[str, Any] | None:
        return self._devices.get(device_id)

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Return a Node resource by id, or ``None``.

        Nodes carry the IS-04 ``services`` array that advertises
        per-Node APIs like the Node Reservation service at
        ``urn:x-matrox:service:exclusive/v1.0``. Controllers walk
        this array to discover the acquire/renew/release/keepalive
        base URL.
        """
        return self._nodes.get(node_id)

    def node_for_device(self, device_id: str) -> dict[str, Any] | None:
        """Convenience: resolve the Node that owns ``device_id``.

        Every device carries ``node_id`` pointing at its owning Node.
        Returns ``None`` if either the device isn't cached or its
        ``node_id`` references a Node the cache doesn't know about.
        """
        device = self._devices.get(device_id)
        if device is None:
            return None
        nid = device.get("node_id", "") or ""
        if not nid:
            return None
        return self._nodes.get(nid)

    def all_nodes(self) -> list[dict[str, Any]]:
        return list(self._nodes.values())

    def get_flow(self, flow_id: str) -> dict[str, Any] | None:
        return self._flows.get(flow_id)

    def get_source(self, source_id: str) -> dict[str, Any] | None:
        """Return a Source resource by id, or ``None``.

        Needed by the flow-match path: audio ``channel_count`` is derived
        from the source's ``channels``, not from the flow itself.
        """
        return self._sources.get(source_id or "")

    def get_flow_match(self, resource_id: str) -> dict[str, Any] | None:
        """Return the last cached flow-match payload for a sender/receiver
        id (``{"matched_cs_index": …}``), or ``None`` if none computed yet.
        Populated by the ``flow`` branch of ``upsert``.
        """
        return self._flow_match.get(resource_id)

    def get_status(self, resource_id: str) -> dict[str, Any]:
        return dict(self._status.get(resource_id, {}))

    def all_senders(self) -> list[dict[str, Any]]:
        """Return every sender, each with ``format`` resolved from its
        flow when the sender's own field is null/missing. See
        ``get_sender`` for rationale.
        """
        out: list[dict[str, Any]] = []
        for s in self._senders.values():
            if not s.get("format"):
                flow = self._flows.get(s.get("flow_id", "") or "")
                if flow is not None and flow.get("format"):
                    out.append({**s, "format": flow["format"]})
                    continue
            out.append(s)
        return out

    def all_receivers(self) -> list[dict[str, Any]]:
        return list(self._receivers.values())

    def all_devices(self) -> list[dict[str, Any]]:
        return list(self._devices.values())

    # ------------------------------------------------------------------
    # Grouped views — top-level listing pages render from these
    # ------------------------------------------------------------------

    def senders_grouped(self) -> list[DeviceView]:
        return self._build_grouped(kind="sender")

    def receivers_grouped(self) -> list[DeviceView]:
        return self._build_grouped(kind="receiver")

    def _build_grouped(self, kind: ResourceKind) -> list[DeviceView]:
        store = self._senders if kind == "sender" else self._receivers

        by_device: dict[str, DeviceView] = {}

        for r in store.values():
            rid = r.get("id", "") or ""
            device_id = r.get("device_id", "") or ""
            label = r.get("label", "") or ""
            description = r.get("description", "") or ""
            hint = extract_group_hint(r.get("tags"))

            # IS-04 senders aren't required to carry a top-level
            # ``format`` (it's inherited from the referenced flow).
            # If the sender resource has a null/missing format,
            # resolve it from the flow here so downstream compat
            # checks always see a valid format URN. The resource is
            # shallow-copied so the cache's raw store stays
            # untouched.
            if kind == "sender" and not r.get("format"):
                flow = self._flows.get(r.get("flow_id", "") or "")
                if flow is not None and flow.get("format"):
                    r = {**r, "format": flow["format"]}

            dev = self._devices.get(device_id) or {}
            serial = device_serial(dev) or ""
            dev_label = dev.get("label", "") or ""
            dev_addr = device_address(dev) or ""

            view = by_device.get(device_id)
            if view is None:
                view = DeviceView(
                    device_id=device_id,
                    device_serial=serial,
                    device_label=dev_label,
                    device_address=dev_addr,
                )
                by_device[device_id] = view

            grouped = GroupedResource(
                id=rid,
                label=label,
                description=description,
                device_id=device_id,
                device_serial=serial,
                device_label=dev_label,
                hint=hint,
                resource=r,
                status=dict(self._status.get(rid, {})),
            )

            if hint is None:
                view.ungrouped.append(grouped)
                continue

            key = hint.key
            group = next(
                (g for g in view.groups if g.hint_key == key),
                None,
            )
            if group is None:
                group = NaturalGroupView(
                    device_id=device_id,
                    device_serial=serial,
                    device_label=dev_label,
                    hint_key=key,
                )
                view.groups.append(group)
            group.members.append(grouped)

        # Sort inside each group by (format, role, id) so multi-format
        # groups (e.g. RTP 0 with both AUDIO and VIDEO members) present a
        # stable "AUDIO 0, AUDIO 1, VIDEO 0" order. Groups by
        # (transport, group_index). Devices by serial (empty serial last
        # so the UI stays stable when some devices don't advertise one).
        for view in by_device.values():
            for g in view.groups:
                g.members.sort(key=lambda m: (
                    m.hint.format if m.hint else "",
                    m.hint.role if m.hint else 0,
                    m.id,
                ))
            view.groups.sort(key=lambda g: g.hint_key)
            view.ungrouped.sort(key=lambda m: (m.label, m.id))
            # Distinct transports across the device's senders/receivers,
            # taken from each resource's IS-04 ``transport`` attribute
            # with the ``urn:x-nmos:transport:`` prefix stripped — so
            # e.g. ``urn:x-nmos:transport:rtp.mcast`` displays as
            # ``rtp.mcast``. Header shows the sorted unique set.
            transports: set[str] = set()
            for bucket in (*view.groups, None):
                members = (
                    bucket.members if bucket is not None else view.ungrouped
                )
                for m in members:
                    t = strip_transport_prefix(m.resource.get("transport"))
                    if t:
                        transports.add(t)
            view.transports = sorted(transports)

        return sorted(
            by_device.values(),
            key=lambda v: (v.device_serial == "", v.device_serial, v.device_id),
        )


# ---------------------------------------------------------------------------
# Helper: async iterator over status events for one subscriber
# ---------------------------------------------------------------------------

class StatusEventStream:
    """Async queue feeder for a single SSE subscriber.

    A subscriber registers a callback with the cache that pushes onto
    this object's internal queue. The SSE handler then awaits events
    from the queue and writes them out on the wire. When the client
    disconnects, the handler calls ``close()`` which detaches the
    listener from the cache.
    """

    def __init__(self, cache: ResourceCache, filter_ids: set[str]) -> None:
        self._cache = cache
        self._filter_ids = filter_ids
        self._queue: asyncio.Queue[StatusChanged | None] = asyncio.Queue()
        self._listener: StatusListener = self._on_event
        cache.add_status_listener(self._listener)

    def _on_event(self, event: StatusChanged) -> None:
        if not self._filter_ids or event.resource_id in self._filter_ids:
            # Fire-and-forget enqueue; sync callback in an async context
            # so use ``put_nowait`` — queue is unbounded, so this is safe.
            self._queue.put_nowait(event)

    async def events(self) -> AsyncIterator[StatusChanged]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    def close(self) -> None:
        self._cache.remove_status_listener(self._listener)
        self._queue.put_nowait(None)
