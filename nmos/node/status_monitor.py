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

# BCP-008-01 (docs/Overview.md): on recovery to Healthy from a
# (Partially)Unhealthy state, implementations are RECOMMENDED to RETAIN the
# prior status message by prepending "Previously: " rather than clearing it,
# so an operator can still see what the fault WAS. A fresh fault supersedes
# that breadcrumb. Messages are length-bounded so they stay readable in the
# controller UI and the NcStatusMonitor model.
_PREVIOUSLY_PREFIX = "Previously: "
MAX_STATUS_MESSAGE_LEN = 200


def _clip_status_message(msg: str) -> str:
    """Bound a status message to MAX_STATUS_MESSAGE_LEN (head + ellipsis)."""
    if len(msg) <= MAX_STATUS_MESSAGE_LEN:
        return msg
    return msg[: MAX_STATUS_MESSAGE_LEN - 3].rstrip() + "..."


def _is_status_breadcrumb(msg: str) -> bool:
    """True when ``msg`` is a 'Previously: ' recovery breadcrumb — i.e. there
    is no active fault, so a new fault may overwrite it."""
    return msg.startswith(_PREVIOUSLY_PREFIX)


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
               EventId.VENDOR_ESSENCE_BASE,
               EventId.VENDOR_ESSENCE_CONSTRAINT_VIOLATED):
        return NC_UNHEALTHY
    elif eid == EventId.VENDOR_ESSENCE_STOP:
        # Inactive, not Unhealthy. This event means "the essence stopped
        # because we are shutting down" — ``emit_stopping`` is called only
        # from the transports' shutdown paths, always immediately followed by
        # ``emit_deactivate``, and it carries ``EventState.INACTIVE`` to say
        # so. It is never raised for a mid-stream essence fault; that is
        # ``ESSENCE_STREAM_ERROR`` above.
        #
        # BCP-008-01 §"Deactivating a receiver" and BCP-008-02
        # §"Deactivating a sender": a resource being deactivated "MUST
        # cleanly [disconnect/interrupt] ... by not generating intermediate
        # unhealthy states (PartiallyHealthy or Unhealthy) and instead
        # transition directly and immediately ... to Inactive". Mapping this
        # to Unhealthy generated exactly such an intermediate state, and
        # because the transition counters "MUST increment each time the
        # associated status transitions to a less healthy state" while
        # "transitions to/from neutral states like Inactive or NotUsed are
        # ignored", every ordinary deactivation permanently inflated the
        # stream and connection counters — the record an operator relies on
        # to judge whether a resource has been misbehaving.
        #
        # The Unhealthy never even reached a client: the
        # VENDOR_TRANSPORT_DEACTIVATE that follows sets both domains to
        # Inactive a moment later (see the mapping above and Step 4 of
        # ``process_event``), so the counter increment was the whole of its
        # lasting effect.
        return NC_INACTIVE
    elif eid == EventId.VENDOR_ESSENCE_CONSTRAINT_PARTIAL:
        # IS-11 sender no_essence / awaiting_essence — amber, not a fault.
        return NC_PARTIALLY_HEALTHY
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

    active: bool = False
    """Whether the resource is currently activated.

    Gates the two domains whose statuses the specification ties to activation:
    transport (connectionStatus / transmissionStatus) and essence
    (streamStatus / essenceStatus). While this is False they stay Inactive and
    stream-level events are dropped.

    Needed because "transition to Inactive" is not the same as "stay
    Inactive", and only the first was implemented. BCP-008-01 §"Deactivating a
    receiver" and BCP-008-02 §"Deactivating a sender" require those statuses
    to reach Inactive on deactivation, and §overallStatus mapping requires
    "When the Receiver is Inactive the overallStatus uses the Inactive
    option". But ``process_one_domain`` publishes immediately whenever either
    side of a transition is Inactive — the rule that makes activation
    responsive — so a single event arriving after deactivation flipped the
    status straight back out of Inactive with no delay. A trailing recovery
    (``TRANSPORT_OK`` / ``ESSENCE_OK``) from a stream that had recovered just
    before shutdown was enough to leave a deactivated resource reporting
    Healthy, overall status included.

    It also covers the resource that has never been activated at all: it
    starts Inactive and no stray event can promote it.

    Link and synchronization are deliberately NOT gated. Neither appears in
    either specification's deactivation list: linkStatus describes "the health
    of all the physical links", which does not stop being meaningful when a
    receiver is idle, and synchronization has its own NotUsed value. Their
    contribution to overallStatus is already neutralised while inactive,
    because ``compute_overall_status`` returns Inactive whenever the transport
    domain is Inactive.
    """

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
            """On a worse transition, record the fault cause in overall_message.

            A fresh fault overwrites an empty message OR a 'Previously: '
            recovery breadcrumb (the breadcrumb means no active fault); an
            existing active-fault message is kept (first-fault-wins)."""
            if event.info and (
                not self.overall_message
                or _is_status_breadcrumb(self.overall_message)
            ):
                self.overall_message = _clip_status_message(event.info)

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

            # Step 0: while deactivated, only an activation may move these
            # statuses. See ``active`` for why: without this, one trailing
            # stream event promotes a deactivated resource straight out of
            # Inactive, because the Inactive branch of the hysteresis
            # deliberately publishes without delay.
            if not self.active and event.event != EventId.VENDOR_TRANSPORT_ACTIVATE:
                return False

            # Step 1: Activation reset
            if event.event == EventId.VENDOR_TRANSPORT_ACTIVATE:
                self.active = True
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
                # Last, so the routing above still ran: from here on both
                # domains hold Inactive until the next activation.
                self.active = False

        elif domain in (AlertDomain.ESSENCE, AlertDomain.VENDOR_ESSENCE):
            # Same gate as the transport domain. The shutdown pair is ordered
            # stopping-then-deactivate, so ``VENDOR_ESSENCE_STOP`` still
            # arrives while active and is what takes essence to Inactive;
            # anything after the deactivation is dropped.
            if not self.active:
                return False

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
            #
            # The start/stop pair is mirrored into the connection domain so
            # both move together. ``STOP`` injects **Inactive** rather than
            # Unhealthy for the reason given in ``get_essence_new_state``: it
            # signals a shutdown, and the deactivation clauses of BCP-008-01
            # and BCP-008-02 forbid an intermediate unhealthy state on the way
            # to Inactive. Injecting Unhealthy here is what pushed the
            # *connection* counter up on every clean deactivation, in addition
            # to the stream counter.
            if not self.is_sender:
                if event.event == EventId.VENDOR_ESSENCE_STOP:
                    u, _w = _route_to_domain(self.transport, NC_INACTIVE, event)
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
            if w and domain_state.last_event_info and (
                not self.overall_message
                or _is_status_breadcrumb(self.overall_message)
            ):
                self.overall_message = _clip_status_message(
                    domain_state.last_event_info)

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
            if prev > NC_HEALTHY and new_overall == NC_HEALTHY:
                # BCP-008-01: retain the prior fault as a "Previously: "
                # breadcrumb on recovery to Healthy (don't blank it).
                if self.overall_message and not _is_status_breadcrumb(
                        self.overall_message):
                    self.overall_message = _clip_status_message(
                        _PREVIOUSLY_PREFIX + self.overall_message)
            elif prev > NC_HEALTHY and new_overall == NC_INACTIVE:
                # Went inactive (deactivated) — no active fault to describe.
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

    # _publish_status is called without a lock. It mutates node state and
    # publishes, but it does so without awaiting, so it completes as one
    # uninterruptible step and cannot interleave with a request handler or with
    # a firing scheduled activation. See the invariant documented next to
    # Node.dg_pending_activation: if an await is ever added inside
    # _publish_status, that reasoning breaks and this needs real exclusion.
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
                _publish_status(node, monitor)

        except asyncio.TimeoutError:
            pass  # Tick — process delayed transitions below
        except asyncio.CancelledError:
            break

        # Tick all monitors for time-delayed transitions
        for monitor in monitors.values():
            if monitor.tick():
                _publish_status(node, monitor)


def _publish_status(node: Any, monitor: ResourceMonitor) -> None:
    """Update the monitor source on the Node with current status."""
    from nmos.node.updates import MonitorSenderInfo, MonitorReceiverInfo

    if monitor.is_sender:
        sender_info = MonitorSenderInfo(
            auto_reset=True,
            overall_status=monitor.overall_status,
            overall_message=monitor.overall_message,
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
            overall_message=monitor.overall_message,
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
