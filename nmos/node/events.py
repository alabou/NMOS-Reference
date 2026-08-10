# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""NMOS Node event system.

Event types and constants for the NMOS Node event definitions.
Used by the streaming engine, and in future by IS-12 NMOS Events API
and Node monitoring.

Events are emitted to an asyncio.Queue on the Node object. The queue
is non-blocking: if full, events are dropped.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import IntEnum


# ---------------------------------------------------------------------------
# Alert Domains
# ---------------------------------------------------------------------------

class AlertDomain(IntEnum):
    """Alert domain — categorizes the subsystem reporting the event."""
    LINK = 1
    TRANSPORT = 2
    ESSENCE = 3
    APPLICATION = 4
    CLOCK = 5
    VENDOR = 10
    VENDOR_LINK = 11
    VENDOR_TRANSPORT = 12
    VENDOR_ESSENCE = 13
    VENDOR_APPLICATION = 14
    VENDOR_CLOCK = 15


# ---------------------------------------------------------------------------
# Alert Scopes
# ---------------------------------------------------------------------------

class AlertScope(IntEnum):
    """Alert scope — identifies the resource type."""
    DEVICE = 0
    SENDER = 1
    SENDER_VIDEO = 2
    SENDER_AUDIO = 3
    SENDER_DATA = 4
    SENDER_MUX = 5
    RECEIVER = 6
    RECEIVER_VIDEO = 7
    RECEIVER_AUDIO = 8
    RECEIVER_DATA = 9
    RECEIVER_MUX = 10
    INPUT = 11
    OUTPUT = 12


# ---------------------------------------------------------------------------
# Event States
# ---------------------------------------------------------------------------

class EventState(IntEnum):
    """Event state — severity of the event."""
    UNKNOWN = 0
    INACTIVE = 1
    WAITING = 2
    NORMAL = 3
    WARNING = 4
    ERROR = 5
    MALFUNCTION = 6


# ---------------------------------------------------------------------------
# Event IDs
# Base values: domain × 1000
# ---------------------------------------------------------------------------

class EventId(IntEnum):
    """Specific event identifiers."""
    # Base domain events (value = domain × 1000)
    LINK = 1000
    TRANSPORT = 2000
    ESSENCE = 3000
    APPLICATION = 4000
    CLOCK = 5000
    VENDOR = 10000
    VENDOR_LINK = 11000
    VENDOR_TRANSPORT_BASE = 12000   # Base (not activate/deactivate)
    VENDOR_ESSENCE_BASE = 13000     # Base (not start/stop)
    VENDOR_CLOCK = 15000

    # Link events (1000-series)
    LINK_DOWN = 1001
    LINK_OK = 1999

    # Transport events (2000-series)
    TRANSPORT_PACKET_LOST = 2001
    TRANSPORT_PACKET_LATE = 2002
    TRANSPORT_PACKET_RECOVERED = 2003
    TRANSPORT_STREAM_ERROR = 2004
    TRANSPORT_OK = 2999

    # Essence events (3000-series)
    ESSENCE_STREAM_ERROR = 3001
    ESSENCE_OK = 3999

    # Application events (4000-series)
    APPLICATION_OK = 4999

    # Clock events (5000-series)
    CLOCK_SOURCE_CHANGE = 5001
    CLOCK_UNLOCK = 5002
    CLOCK_OK = 5999

    # Vendor events (10000+ series)
    VENDOR_TEMPERATURE = 10001

    # Vendor transport events (12000-series)
    VENDOR_TRANSPORT_ACTIVATE = 12001
    VENDOR_TRANSPORT_DEACTIVATE = 12002

    # Vendor essence events (13000-series)
    VENDOR_ESSENCE_START = 13001
    VENDOR_ESSENCE_STOP = 13002
    # IS-11 compatibility-state → monitor essence-status bridge.
    # ``VENDOR_ESSENCE_CONSTRAINT_VIOLATED`` flips ``essence_status``
    # (sender) / ``stream_status`` (receiver) to ``NC_UNHEALTHY`` when
    # the Node computes ``active_constraints_violation`` (sender) or
    # ``non_compliant_stream`` (receiver). ``_OK`` is the recovery
    # edge. Kept distinct from the plain ``ESSENCE`` / ``ESSENCE_OK``
    # events so the IS-11 state machine cannot stomp an in-flight
    # streaming-engine error and vice-versa — see the comment on
    # ``Node.set_sender_compatibility_state`` for the transition-
    # only emit logic.
    VENDOR_ESSENCE_CONSTRAINT_VIOLATED = 13003
    VENDOR_ESSENCE_CONSTRAINT_OK = 13004
    # Partial/amber edge — the IS-11 sender states ``no_essence`` /
    # ``awaiting_essence`` map to ``NC_PARTIALLY_HEALTHY`` on the essence
    # facet (there is no essence yet, but it isn't a constraint violation).
    VENDOR_ESSENCE_CONSTRAINT_PARTIAL = 13005


# ---------------------------------------------------------------------------
# Engine Event
# ---------------------------------------------------------------------------

@dataclass
class EngineEvent:
    """A single event from the streaming engine or Node subsystem."""
    domain: int    # AlertDomain value
    scope: int     # AlertScope value
    event: int     # EventId value
    state: int     # EventState value
    count: int     # Occurrence count (e.g., number of packets lost)
    id: str        # Resource UUID (sender or receiver)
    name: str      # Interface name or "*"
    info: str      # Human-readable description


# ---------------------------------------------------------------------------
# Event Queue Helper
# ---------------------------------------------------------------------------

def emit_event(queue: asyncio.Queue[EngineEvent] | None, event: EngineEvent) -> None:
    """Emit an event to the queue (non-blocking, drop if full)."""
    if queue is None:
        return
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        pass  # Drop the event when the queue is full


# ---------------------------------------------------------------------------
# Convenience emitters for the streaming engine lifecycle
# ---------------------------------------------------------------------------

def emit_activate(
    queue: asyncio.Queue[EngineEvent] | None,
    resource_id: str, interface_name: str, is_sender: bool,
) -> None:
    """Emit activation event (1 event)."""
    scope = AlertScope.SENDER if is_sender else AlertScope.RECEIVER
    role = "sender" if is_sender else "receiver"
    emit_event(queue, EngineEvent(
        domain=AlertDomain.VENDOR_TRANSPORT, scope=scope,
        event=EventId.VENDOR_TRANSPORT_ACTIVATE, state=EventState.NORMAL,
        count=1, id=resource_id, name=interface_name,
        info=f"{role} activate",
    ))


def emit_deactivate(
    queue: asyncio.Queue[EngineEvent] | None,
    resource_id: str, interface_name: str, is_sender: bool,
) -> None:
    """Emit deactivation event (1 event)."""
    scope = AlertScope.SENDER if is_sender else AlertScope.RECEIVER
    role = "sender" if is_sender else "receiver"
    emit_event(queue, EngineEvent(
        domain=AlertDomain.VENDOR_TRANSPORT, scope=scope,
        event=EventId.VENDOR_TRANSPORT_DEACTIVATE, state=EventState.INACTIVE,
        count=1, id=resource_id, name=interface_name,
        info=f"{role} deactivate",
    ))


def emit_starting(
    queue: asyncio.Queue[EngineEvent] | None,
    resource_id: str, interface_name: str, is_sender: bool,
) -> None:
    """Emit starting events (4 events).

    NOTE: the CLOCK domain is intentionally NOT touched here. Stream
    activation does not mean the resource is clock-locked — that would
    falsely report ``synchronization_status = Healthy`` (green) for a
    stream on an internal clock (no PTP). The synchronization facet
    reflects the EFFECTIVE clock and is driven separately, only when the
    clock is actually a locked PTP reference: senders from their source's
    ``clock_name``, receivers from the negotiated SDP ``ts-refclk`` — both
    at the activation handlers. Absent such an event the sync domain stays
    ``NC_INACTIVE`` (NotUsed / grey), which is correct for an internal clock.
    """
    scope = AlertScope.SENDER if is_sender else AlertScope.RECEIVER
    role = "sender" if is_sender else "receiver"
    msg = f"{role} starting"

    for domain, event_id in [
        (AlertDomain.VENDOR_ESSENCE, EventId.VENDOR_ESSENCE_START),
        (AlertDomain.TRANSPORT, EventId.TRANSPORT_OK),
        (AlertDomain.ESSENCE, EventId.ESSENCE_OK),
        (AlertDomain.LINK, EventId.LINK_OK),
    ]:
        emit_event(queue, EngineEvent(
            domain=domain, scope=scope,
            event=event_id, state=EventState.NORMAL,
            count=1, id=resource_id, name=interface_name,
            info=msg,
        ))


def emit_clock_locked(
    queue: asyncio.Queue[EngineEvent] | None,
    resource_id: str, interface_name: str, is_sender: bool,
) -> None:
    """Emit a CLOCK_OK event → synchronization_status Healthy (green).

    Emitted by the activation handlers ONLY when the resource's effective
    clock is a locked PTP reference (sender: its source's clock_name;
    receiver: the negotiated SDP ts-refclk). For an internal clock no event
    is emitted, leaving the sync domain at NC_INACTIVE (NotUsed / grey).
    """
    scope = AlertScope.SENDER if is_sender else AlertScope.RECEIVER
    role = "sender" if is_sender else "receiver"
    emit_event(queue, EngineEvent(
        domain=AlertDomain.CLOCK, scope=scope,
        event=EventId.CLOCK_OK, state=EventState.NORMAL,
        count=1, id=resource_id, name=interface_name,
        info=f"{role} clock locked (ptp)",
    ))


def emit_stopping(
    queue: asyncio.Queue[EngineEvent] | None,
    resource_id: str, interface_name: str, is_sender: bool,
) -> None:
    """Emit stopping event (1 event)."""
    scope = AlertScope.SENDER if is_sender else AlertScope.RECEIVER
    role = "sender" if is_sender else "receiver"
    emit_event(queue, EngineEvent(
        domain=AlertDomain.VENDOR_ESSENCE, scope=scope,
        event=EventId.VENDOR_ESSENCE_STOP, state=EventState.INACTIVE,
        count=1, id=resource_id, name=interface_name,
        info=f"{role} stopping",
    ))


def emit_is11_compatibility_event(
    queue: asyncio.Queue[EngineEvent] | None,
    resource_id: str, is_sender: bool, tier: str, info: str,
) -> None:
    """Emit the IS-11 compatibility transition as a vendor-essence event.

    Used by ``Node.set_sender_compatibility_state`` (and the receiver
    equivalent) ONLY on state-transition edges — not on every call.
    See the design notes in those methods for why transition-only is
    required (hysteresis delay reset + overall_message churn).

    Args:
        queue: the Node's event queue. No-op when ``None`` (e.g. in
            unit tests that construct a Node without wiring the
            status-monitor task).
        resource_id: the sender or receiver UUID.
        is_sender: picks ``AlertScope.SENDER`` / ``AlertScope.RECEIVER``.
        tier: the target essence tier — ``"violation"`` (UNHEALTHY),
            ``"partial"`` (PARTIALLY_HEALTHY, e.g. sender
            ``no_essence``/``awaiting_essence``), or ``"healthy"``
            (recovery edge).
        info: human-readable ``overall_message`` text.
    """
    scope = AlertScope.SENDER if is_sender else AlertScope.RECEIVER
    if tier == "violation":
        event_id = EventId.VENDOR_ESSENCE_CONSTRAINT_VIOLATED
        state = EventState.ERROR
    elif tier == "partial":
        event_id = EventId.VENDOR_ESSENCE_CONSTRAINT_PARTIAL
        state = EventState.WARNING
    else:
        event_id = EventId.VENDOR_ESSENCE_CONSTRAINT_OK
        state = EventState.NORMAL
    emit_event(queue, EngineEvent(
        domain=AlertDomain.VENDOR_ESSENCE, scope=scope,
        event=event_id, state=state,
        count=1, id=resource_id, name="*",
        info=info,
    ))


def emit_transport_error(
    queue: asyncio.Queue[EngineEvent] | None,
    resource_id: str, interface_name: str, is_sender: bool,
    info: str = "", link_down: bool = False,
) -> None:
    """Emit transport error (1-2 events)."""
    scope = AlertScope.SENDER if is_sender else AlertScope.RECEIVER
    role = "sender" if is_sender else "receiver"
    emit_event(queue, EngineEvent(
        domain=AlertDomain.TRANSPORT, scope=scope,
        event=EventId.TRANSPORT_STREAM_ERROR, state=EventState.ERROR,
        count=1, id=resource_id, name=interface_name,
        info=info or f"{role} packet lost",
    ))
    if link_down:
        emit_event(queue, EngineEvent(
            domain=AlertDomain.LINK, scope=scope,
            event=EventId.LINK_DOWN, state=EventState.INACTIVE,
            count=1, id=resource_id, name=interface_name,
            # Carry the specific cause (e.g. "connect error: …") so the link
            # message names the real fault, not a generic "link down".
            info=info or "link down",
        ))


def emit_recovery(
    queue: asyncio.Queue[EngineEvent] | None,
    resource_id: str, interface_name: str, is_sender: bool,
    pending_events: set[int],
) -> None:
    """Emit recovery Ok events for each pending domain.

    Iterates the pending events; domain = event/1000.
    """
    scope = AlertScope.SENDER if is_sender else AlertScope.RECEIVER
    for event_id in pending_events:
        emit_event(queue, EngineEvent(
            domain=event_id // 1000, scope=scope,
            event=event_id, state=EventState.NORMAL,
            count=1, id=resource_id, name=interface_name,
            info="recovery",
        ))
