# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""BCP-008 Status Reporting — Event Consumer and State Machine.

Consumes engine events from node.event_queue, maintains per-domain status
state machines with 3-second hysteresis, and publishes status updates to
monitor sources via IS-04.

Lightweight implementation: no IS-12, no MS-05-02. Status flows through
IS-04 registration only, per the local spec (specs/NMOS With Status Reporting.md).

Architecture:
- Single async task reads from the shared event_queue
- Per-resource state machines (dict[resource_id → ResourceMonitor])
- 1-second tick for time-delayed transitions
- asyncio.Lock protects monitor source updates

Implements the NcSenderMonitor/NcReceiverMonitor event loops.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from nmos.node.events import (
    EngineEvent, AlertDomain, AlertScope, EventId, EventState,
)


# ---------------------------------------------------------------------------
# BCP-008 Status Constants
# ---------------------------------------------------------------------------

NC_INACTIVE = 0
NC_HEALTHY = 1            # NcAllUp for link
NC_PARTIALLY_HEALTHY = 2  # NcSomeDown for link
NC_UNHEALTHY = 3          # NcAllDown for link

# BCP-008 zero-value semantic alias. For most facets value 0 means
# "Inactive" (the resource isn't operating), but for the
# ``synchronization_status`` facet the spec's vocabulary names it
# ``NotUsed`` — "this resource is not using a clock reference".
# The integer is the same; the alias exists so sync call sites can
# be self-documenting at the point where the value is written.
NC_NOT_USED = 0

# Reporting delay in seconds (BCP-008: fixed at 3 seconds)
STATUS_REPORTING_DELAY = 3.0


# ---------------------------------------------------------------------------
# Event → Status Converters (get*NewState functions)
# ---------------------------------------------------------------------------

def get_link_new_state(event: EngineEvent) -> int:
    """getLinkNewState."""
    eid = event.event
    if eid in (EventId.LINK, EventId.LINK_DOWN, EventId.VENDOR_LINK):
        return NC_UNHEALTHY  # NcAllDown
    elif eid == EventId.LINK_OK:
        return NC_HEALTHY    # NcAllUp
    return -1


def get_transmission_new_state(event: EngineEvent) -> int:
    """getTransmissionNewState."""
    eid = event.event
    if eid in (EventId.TRANSPORT, EventId.TRANSPORT_PACKET_LATE,
               EventId.TRANSPORT_PACKET_LOST, EventId.TRANSPORT_STREAM_ERROR,
               EventId.VENDOR_TRANSPORT_BASE):
        return NC_UNHEALTHY
    elif eid == EventId.TRANSPORT_PACKET_RECOVERED:
        return NC_PARTIALLY_HEALTHY
    elif eid == EventId.TRANSPORT_OK:
        return NC_HEALTHY
    elif eid == EventId.VENDOR_TRANSPORT_ACTIVATE:
        return NC_HEALTHY
    elif eid == EventId.VENDOR_TRANSPORT_DEACTIVATE:
        return NC_INACTIVE
    return -1


def get_connection_new_state(event: EngineEvent) -> int:
    """getConnectionNewState. Same as transmission."""
    return get_transmission_new_state(event)


def get_essence_new_state(event: EngineEvent) -> int:
    """getEssenceNewState.

    Extended to include the IS-11 compatibility
    vendor events (``VENDOR_ESSENCE_CONSTRAINT_VIOLATED`` / ``_OK``) so
    an ``active_constraints_violation`` on a sender (or
    ``non_compliant_stream`` on a receiver — ``get_stream_new_state``
    delegates here) flips the respective monitor's essence /
    stream_status. See the emit sites in
    ``Node.set_sender_compatibility_state`` and
    ``Node.set_receiver_compatibility_state`` for the transition-edge
    logic that keeps the hysteresis machine honest.
    """
    eid = event.event
    if eid in (EventId.ESSENCE, EventId.ESSENCE_STREAM_ERROR,
               EventId.VENDOR_ESSENCE_BASE, EventId.VENDOR_ESSENCE_STOP,
               EventId.VENDOR_ESSENCE_CONSTRAINT_VIOLATED):
        return NC_UNHEALTHY
    elif eid in (EventId.ESSENCE_OK, EventId.VENDOR_ESSENCE_START,
                 EventId.VENDOR_ESSENCE_CONSTRAINT_OK):
        return NC_HEALTHY
    return -1


def get_stream_new_state(event: EngineEvent) -> int:
    """getStreamNewState. Same as essence."""
    return get_essence_new_state(event)


def get_synchronization_new_state(event: EngineEvent) -> int:
    """getExternalSynchronizationNewState."""
    eid = event.event
    if eid in (EventId.CLOCK, EventId.CLOCK_UNLOCK, EventId.VENDOR_CLOCK):
        return NC_UNHEALTHY
    elif eid == EventId.CLOCK_SOURCE_CHANGE:
        return NC_PARTIALLY_HEALTHY
    elif eid == EventId.CLOCK_OK:
        return NC_HEALTHY
    return -1


# ---------------------------------------------------------------------------
# Domain State Machine
# ---------------------------------------------------------------------------

@dataclass
class DomainState:
    """Per-domain status state with hysteresis.

    internal_time is initialized to now, not zero. Python uses time.monotonic()
    which returns seconds since an arbitrary reference — we initialize to current
    time to prevent false immediate transitions.
    """
    status: int = NC_INACTIVE
    internal_status: int = NC_INACTIVE
    internal_time: float = field(default_factory=time.monotonic)
    activation_time: float = 0.0
    counter: int = 0
    last_event_info: str = ""  # Stored for delayed worse transitions


def process_one_domain(
    state: DomainState, new_status: int, delay: float = STATUS_REPORTING_DELAY,
) -> tuple[bool, bool]:
    """Process one domain's status with time-delayed hysteresis.

    Implements processOneDomainStatus.

    Returns (status_updated, worse_transition).
    """
    now = time.monotonic()

    # Update internal status on new event
    if new_status >= 0:
        if (new_status != state.internal_status
                or new_status == NC_INACTIVE
                or state.internal_status == NC_INACTIVE):
            state.internal_status = new_status
            state.internal_time = now

        # Mark activation time when transitioning FROM Inactive
        if new_status != state.status and state.status == NC_INACTIVE:
            state.activation_time = now

    # Publish logic
    if state.internal_status == NC_INACTIVE or state.status == NC_INACTIVE:
        # Inactive transitions: immediate
        if state.status != state.internal_status:
            state.status = state.internal_status
            return True, False

    elif state.internal_status > state.status:
        # Worse: delayed from activation
        if state.activation_time + delay <= now:
            state.status = state.internal_status
            state.counter += 1
            return True, True  # worse_transition = True

    else:
        # Better: delayed from internal change
        if state.internal_time + delay <= now:
            if state.status != state.internal_status:
                state.status = state.internal_status
                return True, False

    return False, False


# ---------------------------------------------------------------------------
# Overall Status
# ---------------------------------------------------------------------------

def compute_overall_status(
    link: int, transport: int, essence: int, sync: int,
) -> int:
    """Compute overall status from domain statuses."""
    if transport == NC_INACTIVE:
        return NC_INACTIVE
    return max(link, transport, essence, sync)


# ---------------------------------------------------------------------------
# Per-Resource Monitor
# ---------------------------------------------------------------------------

@dataclass
class ResourceMonitor:
    """Aggregates all domain states for one sender or receiver."""
    resource_id: str
    is_sender: bool

    # link is initialized to NcAllUp (Healthy); others to NcInactive
    link: DomainState = field(default_factory=lambda: DomainState(
        status=NC_HEALTHY, internal_status=NC_HEALTHY))
    transport: DomainState = field(default_factory=DomainState)
    essence: DomainState = field(default_factory=DomainState)
    sync: DomainState = field(default_factory=DomainState)

    # Single activationTime shared across all domains.
    # Set when ANY domain transitions from Inactive. Used by all domains
    # for the worse-transition delay calculation.
    activation_time: float = 0.0

    overall_status: int = NC_INACTIVE
    overall_message: str = ""

    def process_event(self, event: EngineEvent) -> bool:
        """Route an engine event to the appropriate domain.

        Implements the event handler in getEventsFromEngine(). Each event is
        processed and the return value of process_one_domain determines
        whether to publish. Returns True if any published status changed.

        Also handles:
        - Vendor activation reset (counters + state)
        - Scope validation
        - Essence→connection error injection
        """
        # Scope validation
        if self.is_sender and event.scope != AlertScope.SENDER:
            return False
        if not self.is_sender and event.scope != AlertScope.RECEIVER:
            return False

        changed = False
        domain = event.domain

        def _on_worse(event: EngineEvent) -> None:
            """On worse transition: set overall_message to event.info if empty."""
            if not self.overall_message and event.info:
                self.overall_message = event.info

        def _route_to_domain(
            domain_state: DomainState, new_state: int, evt: EngineEvent,
        ) -> tuple[bool, bool]:
            """Route event to domain, store info for delayed worse transitions.

            Syncs shared activation_time before/after call so the single
            activationTime is shared across all domains.

            Stores event.info on the domain when the new state is worse than
            published status. This preserves the message for tick-driven
            delayed transitions.
            """
            if new_state > domain_state.status and new_state != NC_INACTIVE:
                domain_state.last_event_info = evt.info
            # Sync shared → domain before call
            domain_state.activation_time = self.activation_time
            u, w = process_one_domain(domain_state, new_state)
            # Sync domain → shared after call
            self.activation_time = domain_state.activation_time
            if w:
                _on_worse(evt)
            return u, w

        if domain == AlertDomain.LINK:
            new_state = get_link_new_state(event)
            if new_state >= 0:
                u, _w = _route_to_domain(self.link, new_state, event)
                if u:
                    changed = True

        elif domain in (AlertDomain.TRANSPORT, AlertDomain.VENDOR_TRANSPORT):
            # MvAlertDomainTransport, MvAlertDomainVendorTransport

            # Step 1: Activation reset
            if event.event == EventId.VENDOR_TRANSPORT_ACTIVATE:
                self._handle_activation_reset()
                changed = True  # Counter reset triggers publish

            # Step 2: Process transport through state machine
            if self.is_sender:
                new_state = get_transmission_new_state(event)
            else:
                new_state = get_connection_new_state(event)
            if new_state >= 0:
                u, _w = _route_to_domain(self.transport, new_state, event)
                if u:
                    changed = True

            # Step 3: Activate forces essence to Healthy
            if event.event == EventId.VENDOR_TRANSPORT_ACTIVATE:
                u, _w = _route_to_domain(self.essence, NC_HEALTHY, event)
                if u:
                    changed = True

            # Step 4: Deactivate forces essence to Inactive
            if event.event == EventId.VENDOR_TRANSPORT_DEACTIVATE:
                u, _w = _route_to_domain(self.essence, NC_INACTIVE, event)
                if u:
                    changed = True

        elif domain in (AlertDomain.ESSENCE, AlertDomain.VENDOR_ESSENCE):
            if self.is_sender:
                new_state = get_essence_new_state(event)
            else:
                new_state = get_stream_new_state(event)
            if new_state >= 0:
                u, _w = _route_to_domain(self.essence, new_state, event)
                if u:
                    changed = True

            # Essence→Connection injection
            # "Monitors test suite consider Transport as the main event"
            if not self.is_sender:
                if event.event == EventId.VENDOR_ESSENCE_STOP:
                    u, _w = _route_to_domain(self.transport, NC_UNHEALTHY, event)
                    if u:
                        changed = True
                elif event.event == EventId.VENDOR_ESSENCE_START:
                    u, _w = _route_to_domain(self.transport, NC_HEALTHY, event)
                    if u:
                        changed = True

        elif domain == AlertDomain.CLOCK:
            new_state = get_synchronization_new_state(event)
            if new_state >= 0:
                u, _w = _route_to_domain(self.sync, new_state, event)
                if u:
                    changed = True

        # Recompute overall after each event (processState)
        if changed:
            changed |= self._recompute_overall()

        return changed

    def tick(self) -> bool:
        """Process time-delayed transitions for all domains. Called every ~1 second.

        When a delayed worse transition fires, we use the domain's
        last_event_info (stored when the event was first received) to set the
        overall message.
        """
        changed = False
        for domain_state in (self.link, self.transport, self.essence, self.sync):
            # Sync shared activation_time
            domain_state.activation_time = self.activation_time
            u, w = process_one_domain(domain_state, -1)
            self.activation_time = domain_state.activation_time
            if u:
                changed = True
            if w and not self.overall_message and domain_state.last_event_info:
                self.overall_message = domain_state.last_event_info

        if changed:
            changed |= self._recompute_overall()

        return changed

    def _recompute_overall(self) -> bool:
        """Recompute overall status from domain statuses. Returns True if changed."""
        new_overall = compute_overall_status(
            self.link.status, self.transport.status,
            self.essence.status, self.sync.status,
        )
        if new_overall != self.overall_status:
            prev = self.overall_status
            self.overall_status = new_overall
            # Clear message when transitioning to healthy
            if prev > NC_HEALTHY and new_overall <= NC_HEALTHY:
                self.overall_message = ""
            return True
        return False

    def _handle_activation_reset(self) -> None:
        """Reset state on activation.

        Sequence:
        1. Set transport+essence to Inactive (reference point, no notifications)
        2. Clear overall message
        3. Reset all transition counters (if autoReset)

        After this, the normal event routing processes the activate event
        through the state machine (Inactive→Healthy transition).
        Then essence is explicitly forced to Healthy.
        """
        now = time.monotonic()

        # Reset domains to Inactive
        self.transport = DomainState(
            status=NC_INACTIVE, internal_status=NC_INACTIVE,
            internal_time=now,
        )
        self.essence = DomainState(
            status=NC_INACTIVE, internal_status=NC_INACTIVE,
            internal_time=now,
        )
        self.overall_message = ""

        # Auto-reset counters
        self.link.counter = 0
        self.transport.counter = 0
        self.essence.counter = 0
        self.sync.counter = 0


# ---------------------------------------------------------------------------
# Event Consumer (async task)
# ---------------------------------------------------------------------------

async def run_status_monitor(node: Any) -> None:
    """Consume engine events and update monitor sources.

    Single async task for the lifetime of the Node. Dispatched in
    nmos_node.py root DispatchGroup alongside the HTTP server.
    """
    monitors: dict[str, ResourceMonitor] = {}
    monitor_lock = getattr(node, 'monitor_lock', None)

    while True:
        # Wait for event or 1-second tick
        try:
            event = await asyncio.wait_for(node.event_queue.get(), timeout=1.0)
            monitor = monitors.get(event.id)
            if monitor is None:
                monitor = ResourceMonitor(
                    resource_id=event.id,
                    is_sender=(event.scope == AlertScope.SENDER),
                )
                monitors[event.id] = monitor

            # Process event and publish if status changed
            # (processOneDomainStatus return triggers updateSourceMonitor)
            if monitor.process_event(event):
                if monitor_lock is not None:
                    async with monitor_lock:
                        _publish_status(node, monitor)
                else:
                    _publish_status(node, monitor)

        except asyncio.TimeoutError:
            pass  # Tick — process delayed transitions below
        except asyncio.CancelledError:
            break

        # Tick all monitors for time-delayed transitions
        for monitor in monitors.values():
            if monitor.tick():
                if monitor_lock is not None:
                    async with monitor_lock:
                        _publish_status(node, monitor)
                else:
                    _publish_status(node, monitor)


def _publish_status(node: Any, monitor: ResourceMonitor) -> None:
    """Update the monitor source on the Node with current status."""
    from nmos.node.updates import MonitorSenderInfo, MonitorReceiverInfo

    if monitor.is_sender:
        sender_info = MonitorSenderInfo(
            auto_reset=True,
            overall_status=monitor.overall_status,
            overall_status_message=monitor.overall_message,
            link_status=monitor.link.status,
            transmission_status=monitor.transport.status,
            synchronization_status=monitor.sync.status,
            essence_status=monitor.essence.status,
            link_counter=monitor.link.counter,
            transmission_counter=monitor.transport.counter,
            synchronization_counter=monitor.sync.counter,
            essence_counter=monitor.essence.counter,
        )
        _update_monitor_source(node, monitor.resource_id, sender_info, is_sender=True)
    else:
        receiver_info = MonitorReceiverInfo(
            auto_reset=True,
            overall_status=monitor.overall_status,
            overall_status_message=monitor.overall_message,
            link_status=monitor.link.status,
            connection_status=monitor.transport.status,
            synchronization_status=monitor.sync.status,
            stream_status=monitor.essence.status,
            link_counter=monitor.link.counter,
            connection_counter=monitor.transport.counter,
            synchronization_counter=monitor.sync.counter,
            stream_counter=monitor.essence.counter,
        )
        _update_monitor_source(node, monitor.resource_id, receiver_info, is_sender=False)


def _update_monitor_source(
    node: Any, resource_id: str, info: Any, is_sender: bool,
) -> None:
    """Find the monitor source for a sender/receiver and update it."""
    from nmos.node.store import to_static_id

    static_id = to_static_id(resource_id)
    if is_sender:
        resource = node.senders.get(static_id)
    else:
        resource = node.receivers.get(static_id)

    if resource is None:
        return

    # Sender: Monitor on NSenderValue directly
    # Receiver: Monitor on inner.ReceiverCore (polymorphic wrapper)
    try:
        monitor_field = None
        if hasattr(resource, 'Monitor'):
            monitor_field = resource.Monitor
        else:
            inner = resource.get() if hasattr(resource, 'get') else resource
            if inner is not None:
                rv = inner.value if hasattr(inner, 'value') else inner
                core = getattr(rv, 'ReceiverCore', None)
                if core is not None and hasattr(core, 'Monitor'):
                    monitor_field = core.Monitor

        if monitor_field is None or not monitor_field.defined:
            return
        monitor_source = monitor_field.value
        if monitor_source is None:
            return
    except (AttributeError, TypeError) as exc:
        import logging
        logging.warning(f"Monitor source lookup failed for {resource_id}: {exc}")
        return

    if is_sender:
        node.update_source_monitor_sender(monitor_source, info)
    else:
        node.update_source_monitor_receiver(monitor_source, info)
