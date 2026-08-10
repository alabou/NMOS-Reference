# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for BCP-008 status monitor — event consumption and state machine."""

from __future__ import annotations

import time

import pytest

from nmos.node.events import EngineEvent, AlertDomain, AlertScope, EventId, EventState
from nmos.node.status_monitor import (
    NC_INACTIVE, NC_HEALTHY, NC_PARTIALLY_HEALTHY, NC_UNHEALTHY,
    DomainState, ResourceMonitor,
    process_one_domain, compute_overall_status,
    get_link_new_state, get_transmission_new_state,
    get_connection_new_state, get_essence_new_state,
    get_stream_new_state, get_synchronization_new_state,
)


# ---------------------------------------------------------------------------
# Event → Status Converters
# ---------------------------------------------------------------------------

class TestEventConverters:
    """Verify event→status mappings."""

    def test_link_events(self) -> None:
        e = EngineEvent(AlertDomain.LINK, AlertScope.RECEIVER, EventId.LINK_DOWN,
                        EventState.ERROR, 1, "test", "*", "")
        assert get_link_new_state(e) == NC_UNHEALTHY

        e.event = EventId.LINK_OK
        assert get_link_new_state(e) == NC_HEALTHY

    def test_transmission_events(self) -> None:
        def _e(eid: int) -> EngineEvent:
            return EngineEvent(AlertDomain.TRANSPORT, AlertScope.SENDER, eid,
                               EventState.WARNING, 1, "test", "*", "")

        assert get_transmission_new_state(_e(EventId.TRANSPORT_PACKET_LOST)) == NC_UNHEALTHY
        assert get_transmission_new_state(_e(EventId.TRANSPORT_PACKET_LATE)) == NC_UNHEALTHY
        assert get_transmission_new_state(_e(EventId.TRANSPORT_STREAM_ERROR)) == NC_UNHEALTHY
        assert get_transmission_new_state(_e(EventId.TRANSPORT_PACKET_RECOVERED)) == NC_PARTIALLY_HEALTHY
        assert get_transmission_new_state(_e(EventId.TRANSPORT_OK)) == NC_HEALTHY
        assert get_transmission_new_state(_e(EventId.VENDOR_TRANSPORT_ACTIVATE)) == NC_HEALTHY
        assert get_transmission_new_state(_e(EventId.VENDOR_TRANSPORT_DEACTIVATE)) == NC_INACTIVE

    def test_connection_same_as_transmission(self) -> None:
        """Receiver connection uses same mapping as sender transmission."""
        e = EngineEvent(AlertDomain.TRANSPORT, AlertScope.RECEIVER,
                        EventId.TRANSPORT_PACKET_LOST, EventState.WARNING, 1, "test", "*", "")
        assert get_connection_new_state(e) == get_transmission_new_state(e)

    def test_essence_events(self) -> None:
        def _e(eid: int) -> EngineEvent:
            return EngineEvent(AlertDomain.ESSENCE, AlertScope.SENDER, eid,
                               EventState.WARNING, 1, "test", "*", "")

        assert get_essence_new_state(_e(EventId.ESSENCE_STREAM_ERROR)) == NC_UNHEALTHY
        assert get_essence_new_state(_e(EventId.ESSENCE_OK)) == NC_HEALTHY
        assert get_essence_new_state(_e(EventId.VENDOR_ESSENCE_START)) == NC_HEALTHY

    def test_stop_is_inactive_not_a_fault(self) -> None:
        """``VENDOR_ESSENCE_STOP`` means "shutting down", not "broken".

        It is raised only from the transports' shutdown paths, always paired
        with ``VENDOR_TRANSPORT_DEACTIVATE``, and carries
        ``EventState.INACTIVE``. The deactivation clauses of BCP-008-01 and
        BCP-008-02 require a resource being deactivated to reach Inactive
        without generating an intermediate PartiallyHealthy or Unhealthy
        state, so this must not map to a fault. ``ESSENCE_STREAM_ERROR`` is
        the event that means a genuine essence fault.
        """
        stop = EngineEvent(AlertDomain.VENDOR_ESSENCE, AlertScope.SENDER,
                           EventId.VENDOR_ESSENCE_STOP, EventState.INACTIVE,
                           1, "test", "*", "sender stopping")
        assert get_essence_new_state(stop) == NC_INACTIVE
        assert get_stream_new_state(stop) == NC_INACTIVE

        fault = EngineEvent(AlertDomain.ESSENCE, AlertScope.SENDER,
                            EventId.ESSENCE_STREAM_ERROR, EventState.WARNING,
                            1, "test", "*", "essence broken")
        assert get_essence_new_state(fault) == NC_UNHEALTHY

    def test_stream_same_as_essence(self) -> None:
        e = EngineEvent(AlertDomain.ESSENCE, AlertScope.RECEIVER,
                        EventId.ESSENCE_STREAM_ERROR, EventState.WARNING, 1, "test", "*", "")
        assert get_stream_new_state(e) == get_essence_new_state(e)

    def test_sync_events(self) -> None:
        def _e(eid: int) -> EngineEvent:
            return EngineEvent(AlertDomain.CLOCK, AlertScope.RECEIVER, eid,
                               EventState.WARNING, 1, "test", "*", "")

        assert get_synchronization_new_state(_e(EventId.CLOCK_OK)) == NC_HEALTHY
        assert get_synchronization_new_state(_e(EventId.CLOCK_SOURCE_CHANGE)) == NC_PARTIALLY_HEALTHY
        # BCP-008: "Unhealthy when expected to use but not locked"
        assert get_synchronization_new_state(_e(EventId.CLOCK_UNLOCK)) == NC_UNHEALTHY

    def test_unknown_event_returns_minus_one(self) -> None:
        e = EngineEvent(AlertDomain.LINK, AlertScope.SENDER, 9999,
                        EventState.NORMAL, 0, "test", "*", "")
        assert get_link_new_state(e) == -1


# ---------------------------------------------------------------------------
# State Machine
# ---------------------------------------------------------------------------

class TestProcessOneDomain:
    """Test the BCP-008 state machine with hysteresis."""

    def test_inactive_to_healthy_immediate(self) -> None:
        """Inactive → Healthy transition is immediate (no delay)."""
        state = DomainState()
        assert state.status == NC_INACTIVE
        changed, _w = process_one_domain(state, NC_HEALTHY, delay=3.0)
        assert changed
        assert state.status == NC_HEALTHY

    def test_healthy_to_inactive_immediate(self) -> None:
        """Healthy → Inactive is immediate."""
        state = DomainState(status=NC_HEALTHY, internal_status=NC_HEALTHY)
        changed, _w = process_one_domain(state, NC_INACTIVE, delay=3.0)
        assert changed
        assert state.status == NC_INACTIVE

    def test_worse_transition_delayed(self) -> None:
        """Healthy → Unhealthy is delayed by activation_time + delay."""
        state = DomainState(status=NC_HEALTHY, internal_status=NC_HEALTHY)
        # Set activation time in the past (>3s ago)
        state.activation_time = time.monotonic() - 5.0

        changed, _w = process_one_domain(state, NC_UNHEALTHY, delay=3.0)
        assert changed
        assert state.status == NC_UNHEALTHY
        assert state.counter == 1  # Worse transition counted

    def test_worse_transition_blocked_by_delay(self) -> None:
        """Healthy → Unhealthy blocked when activation is recent."""
        state = DomainState(status=NC_HEALTHY, internal_status=NC_HEALTHY)
        state.activation_time = time.monotonic()  # Just activated

        changed, _w = process_one_domain(state, NC_UNHEALTHY, delay=3.0)
        assert not changed  # Blocked by delay
        assert state.status == NC_HEALTHY  # Unchanged
        assert state.internal_status == NC_UNHEALTHY  # Buffered

    def test_better_transition_delayed(self) -> None:
        """Unhealthy → Healthy delayed by internal_time + delay."""
        state = DomainState(
            status=NC_UNHEALTHY, internal_status=NC_UNHEALTHY,
            activation_time=time.monotonic() - 10.0,
        )
        # Set internal change time in the past (>3s ago)
        changed, _w = process_one_domain(state, NC_HEALTHY, delay=3.0)
        assert not changed  # Just changed internal, not published yet

        # Now tick with -1 after delay
        state.internal_time = time.monotonic() - 4.0  # >3s ago
        changed, _w = process_one_domain(state, -1, delay=3.0)
        assert changed
        assert state.status == NC_HEALTHY

    def test_counter_only_on_worse(self) -> None:
        """Counter increments only on worse transitions, not improvements."""
        state = DomainState(
            status=NC_HEALTHY, internal_status=NC_HEALTHY,
            activation_time=time.monotonic() - 10.0,
        )

        # Worse: counter increments
        changed, _w = process_one_domain(state, NC_UNHEALTHY, delay=0.0)
        assert state.counter == 1

        # Better: counter stays
        state.internal_time = time.monotonic() - 10.0
        changed, _w = process_one_domain(state, NC_HEALTHY, delay=0.0)
        assert state.counter == 1  # No increment

    def test_tick_processes_delayed_transitions(self) -> None:
        """Tick (new_status=-1) processes pending delayed transitions."""
        state = DomainState(
            status=NC_UNHEALTHY, internal_status=NC_HEALTHY,
            internal_time=time.monotonic() - 5.0,  # Changed >3s ago
            activation_time=time.monotonic() - 10.0,
        )

        changed, _w = process_one_domain(state, -1, delay=3.0)
        assert changed
        assert state.status == NC_HEALTHY

    def test_counter_ignores_inactive_transitions(self) -> None:
        """BCP-008: Transitions to/from Inactive are ignored for counters."""
        # Inactive → Healthy: counter stays at 0
        state = DomainState()
        assert state.counter == 0
        process_one_domain(state, NC_HEALTHY, delay=0.0)
        assert state.counter == 0  # Not a worse transition

        # Healthy → Inactive: counter stays at 0
        process_one_domain(state, NC_INACTIVE, delay=0.0)
        assert state.counter == 0

    def test_worse_transition_immediate_after_grace(self) -> None:
        """BCP-008: 'MUST make transition to less healthy state without delay'
        — once the activation grace period has elapsed."""
        state = DomainState(status=NC_HEALTHY, internal_status=NC_HEALTHY)
        state.activation_time = time.monotonic() - 10.0  # Grace period long passed

        changed, w = process_one_domain(state, NC_UNHEALTHY, delay=3.0)
        assert changed, "Worse transition should fire immediately after grace"
        assert w, "Should be flagged as worse transition"
        assert state.status == NC_UNHEALTHY

    def test_better_transition_not_published_before_delay(self) -> None:
        """BCP-008: 'MUST delay the transition to a more healthy state
        by the configured statusReportingDelay value and MUST only make
        the transition if the healthier state is maintained.'"""
        state = DomainState(
            status=NC_UNHEALTHY, internal_status=NC_UNHEALTHY,
            activation_time=time.monotonic() - 10.0,
        )

        # Receive Healthy event — internal changes, published does NOT
        changed, _w = process_one_domain(state, NC_HEALTHY, delay=3.0)
        assert not changed, "Better transition must not publish immediately"
        assert state.status == NC_UNHEALTHY
        assert state.internal_status == NC_HEALTHY

        # Tick immediately — delay has NOT elapsed yet
        changed, _w = process_one_domain(state, -1, delay=3.0)
        assert not changed, "Better transition must not fire before delay"
        assert state.status == NC_UNHEALTHY


# ---------------------------------------------------------------------------
# Overall Status
# ---------------------------------------------------------------------------

class TestOverallStatus:
    """Test overall status computation."""

    def test_inactive_when_transport_inactive(self) -> None:
        assert compute_overall_status(NC_HEALTHY, NC_INACTIVE, NC_HEALTHY, NC_HEALTHY) == NC_INACTIVE

    def test_healthy_all_domains(self) -> None:
        assert compute_overall_status(NC_HEALTHY, NC_HEALTHY, NC_HEALTHY, NC_HEALTHY) == NC_HEALTHY

    def test_worst_wins(self) -> None:
        assert compute_overall_status(NC_HEALTHY, NC_PARTIALLY_HEALTHY, NC_HEALTHY, NC_HEALTHY) == NC_PARTIALLY_HEALTHY
        assert compute_overall_status(NC_HEALTHY, NC_HEALTHY, NC_UNHEALTHY, NC_HEALTHY) == NC_UNHEALTHY
        assert compute_overall_status(NC_UNHEALTHY, NC_HEALTHY, NC_HEALTHY, NC_HEALTHY) == NC_UNHEALTHY

    def test_all_unhealthy(self) -> None:
        assert compute_overall_status(NC_UNHEALTHY, NC_UNHEALTHY, NC_UNHEALTHY, NC_UNHEALTHY) == NC_UNHEALTHY


# ---------------------------------------------------------------------------
# Resource Monitor
# ---------------------------------------------------------------------------

class TestResourceMonitor:
    """Test per-resource event routing and overall computation."""

    def _make_event(self, domain: int, event: int, scope: int = AlertScope.SENDER) -> EngineEvent:
        return EngineEvent(domain, scope, event, EventState.NORMAL, 1, "test-sender", "*", "")

    def test_activation_lifecycle(self) -> None:
        """Activate → starting → stopping → deactivate lifecycle."""
        mon = ResourceMonitor("test-sender", is_sender=True)
        assert mon.overall_status == NC_INACTIVE

        # Activate (transport domain)
        mon.process_event(self._make_event(AlertDomain.VENDOR_TRANSPORT, EventId.VENDOR_TRANSPORT_ACTIVATE))
        mon.tick()
        assert mon.transport.status == NC_HEALTHY

        # Starting (5 events)
        mon.process_event(self._make_event(AlertDomain.VENDOR_ESSENCE, EventId.VENDOR_ESSENCE_START))
        mon.process_event(self._make_event(AlertDomain.TRANSPORT, EventId.TRANSPORT_OK))
        mon.process_event(self._make_event(AlertDomain.ESSENCE, EventId.ESSENCE_OK))
        mon.process_event(self._make_event(AlertDomain.LINK, EventId.LINK_OK))
        mon.process_event(self._make_event(AlertDomain.CLOCK, EventId.CLOCK_OK))
        mon.tick()

        assert mon.overall_status == NC_HEALTHY

        # Deactivate
        mon.process_event(self._make_event(AlertDomain.VENDOR_TRANSPORT, EventId.VENDOR_TRANSPORT_DEACTIVATE))
        mon.tick()
        assert mon.transport.status == NC_INACTIVE
        assert mon.overall_status == NC_INACTIVE

    def test_packet_loss_degrades_status(self) -> None:
        """Packet loss on receiver → connection Unhealthy → overall Unhealthy."""
        mon = ResourceMonitor("test-receiver", is_sender=False)

        # Activate
        mon.process_event(EngineEvent(
            AlertDomain.VENDOR_TRANSPORT, AlertScope.RECEIVER,
            EventId.VENDOR_TRANSPORT_ACTIVATE, EventState.NORMAL, 1, "test-receiver", "*", "",
        ))
        mon.process_event(EngineEvent(
            AlertDomain.LINK, AlertScope.RECEIVER,
            EventId.LINK_OK, EventState.NORMAL, 1, "test-receiver", "*", "",
        ))
        # Force shared activation time in the past for delay testing
        # (activation_time is shared across all domains)
        mon.activation_time = time.monotonic() - 10.0
        mon.tick()

        # Packet loss
        mon.process_event(EngineEvent(
            AlertDomain.TRANSPORT, AlertScope.RECEIVER,
            EventId.TRANSPORT_PACKET_LOST, EventState.WARNING, 5, "test-receiver", "*", "",
        ))
        mon.tick()

        assert mon.transport.status == NC_UNHEALTHY
        assert mon.overall_status == NC_UNHEALTHY
        assert mon.transport.counter == 1

    def test_overall_message_from_event_info(self) -> None:
        """Overall message uses engine event info field."""
        mon = ResourceMonitor("test-sender", is_sender=True)

        # Activate
        mon.process_event(self._make_event(AlertDomain.VENDOR_TRANSPORT, EventId.VENDOR_TRANSPORT_ACTIVATE))

        # Link down with info message
        e = EngineEvent(AlertDomain.LINK, AlertScope.SENDER, EventId.LINK_DOWN,
                        EventState.ERROR, 1, "test-sender", "eth0", "link down on eth0")
        mon.process_event(e)
        mon.activation_time = time.monotonic() - 10.0
        mon.tick()

        assert mon.overall_message == "link down on eth0"

    def test_overall_message_breadcrumb_on_healthy(self) -> None:
        """BCP-008-01: on recovery to Healthy the prior fault is RETAINED as a
        'Previously: ' breadcrumb (not blanked)."""
        mon = ResourceMonitor("test-sender", is_sender=True)

        # Activate + make unhealthy
        mon.process_event(self._make_event(AlertDomain.VENDOR_TRANSPORT, EventId.VENDOR_TRANSPORT_ACTIVATE))
        e = EngineEvent(AlertDomain.LINK, AlertScope.SENDER, EventId.LINK_DOWN,
                        EventState.ERROR, 1, "test-sender", "*", "link down")
        mon.process_event(e)
        mon.activation_time = time.monotonic() - 10.0
        mon.tick()
        assert mon.overall_message == "link down"

        # Recover → message retained as a breadcrumb, not cleared
        mon.process_event(self._make_event(AlertDomain.LINK, EventId.LINK_OK))
        mon.link.internal_time = time.monotonic() - 10.0
        mon.tick()
        assert mon.overall_message == "Previously: link down"

    def test_fresh_fault_replaces_previously_breadcrumb(self) -> None:
        """A new fault overwrites a 'Previously: ' breadcrumb so the overall
        message always reflects the CURRENT problem (the breadcrumb means
        no active fault)."""
        mon = ResourceMonitor("test-sender", is_sender=True)
        mon.process_event(self._make_event(AlertDomain.VENDOR_TRANSPORT, EventId.VENDOR_TRANSPORT_ACTIVATE))
        # Simulate a post-recovery breadcrumb already in place.
        mon.overall_message = "Previously: link down"

        # A fresh fault must REPLACE the breadcrumb, not be suppressed by it.
        mon.process_event(EngineEvent(AlertDomain.LINK, AlertScope.SENDER, EventId.LINK_DOWN,
                                      EventState.ERROR, 1, "test-sender", "*", "cable unplugged"))
        mon.activation_time = time.monotonic() - 10.0
        mon.tick()
        assert mon.overall_message == "cable unplugged"

    def test_status_message_is_length_bounded(self) -> None:
        """Status messages are clipped so they stay readable in UI/model."""
        from nmos.node.status_monitor import _clip_status_message, MAX_STATUS_MESSAGE_LEN
        assert _clip_status_message("short cause") == "short cause"
        long_msg = "x" * (MAX_STATUS_MESSAGE_LEN + 50)
        clipped = _clip_status_message(long_msg)
        assert len(clipped) == MAX_STATUS_MESSAGE_LEN
        assert clipped.endswith("...")

    def test_emit_transport_error_does_not_infer_link_down_from_the_cause(
        self,
    ) -> None:
        """A connection fault over a working interface raises no LINK event.

        This test previously asserted the opposite — that
        ``emit_transport_error(..., link_down=True)`` carried a cause such as
        "connect error: refused" onto the LINK event. That was the bug: a
        refused connection is the peer's doing, and BCP-008-01 §"Link Status"
        scopes linkStatus to "the health of all the physical links", defined
        over interfaces. Publishing AllDown for it sent operators to inspect
        cabling that was never at fault, so the ``link_down`` argument is gone
        and the interface is consulted instead. ``lo`` is up on any running
        host, so nothing here may raise a link event.

        See ``test_link_status_attribution.py`` for the interface check itself
        and for the case where a genuinely down interface still reports one.
        """
        import asyncio
        from nmos.node.events import emit_transport_error
        q: asyncio.Queue = asyncio.Queue()
        emit_transport_error(q, "test-receiver", "lo", is_sender=False,
                             info="connect error: refused")
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        assert [e.event for e in events] == [EventId.TRANSPORT_STREAM_ERROR]
        assert events[0].info == "connect error: refused"
        assert not [e for e in events if e.domain == AlertDomain.LINK]

    def test_shared_activation_time_delays_link(self) -> None:
        """BCP-008: After activation, ALL domain worse transitions are delayed
        by statusReportingDelay — including link which starts Healthy.

        A single activation_time is shared across all domains.
        Link starts Healthy and never transitions from Inactive, but its
        worse transitions still respect the shared activation delay."""
        mon = ResourceMonitor("test-sender", is_sender=True)

        # Activate — sets shared activation_time
        mon.process_event(self._make_event(
            AlertDomain.VENDOR_TRANSPORT, EventId.VENDOR_TRANSPORT_ACTIVATE))

        # activation_time is now ~monotonic(). Link down arrives immediately.
        e = EngineEvent(AlertDomain.LINK, AlertScope.SENDER, EventId.LINK_DOWN,
                        EventState.ERROR, 1, "test-sender", "*", "")
        mon.process_event(e)
        mon.tick()

        # Link should STILL be Healthy — activation grace period blocks it
        assert mon.link.status == NC_HEALTHY, \
            "Link worse transition must be delayed during activation grace period"

        # Now move activation_time to the past (>3s ago)
        mon.activation_time = time.monotonic() - 5.0
        mon.tick()

        # NOW the delayed worse transition fires
        assert mon.link.status == NC_UNHEALTHY

    def test_sender_deactivation_essence_becomes_inactive(self) -> None:
        """BCP-008-02: Sender deactivation MUST transition essenceStatus
        to Inactive immediately."""
        mon = ResourceMonitor("test-sender", is_sender=True)

        # Activate + make fully healthy
        mon.process_event(self._make_event(
            AlertDomain.VENDOR_TRANSPORT, EventId.VENDOR_TRANSPORT_ACTIVATE))
        mon.process_event(self._make_event(
            AlertDomain.VENDOR_ESSENCE, EventId.VENDOR_ESSENCE_START))
        mon.process_event(self._make_event(AlertDomain.ESSENCE, EventId.ESSENCE_OK))
        mon.tick()
        assert mon.essence.status == NC_HEALTHY

        # Deactivate
        mon.process_event(self._make_event(
            AlertDomain.VENDOR_TRANSPORT, EventId.VENDOR_TRANSPORT_DEACTIVATE))
        mon.tick()

        assert mon.essence.status == NC_INACTIVE
        assert mon.transport.status == NC_INACTIVE
        assert mon.overall_status == NC_INACTIVE

    def test_receiver_deactivation_lifecycle(self) -> None:
        """BCP-008-01: Receiver deactivation MUST transition directly and
        immediately to Inactive for: overallStatus, connectionStatus, streamStatus.
        MUST NOT generate intermediate unhealthy states."""
        mon = ResourceMonitor("test-receiver", is_sender=False)

        # Activate receiver
        mon.process_event(EngineEvent(
            AlertDomain.VENDOR_TRANSPORT, AlertScope.RECEIVER,
            EventId.VENDOR_TRANSPORT_ACTIVATE, EventState.NORMAL, 1,
            "test-receiver", "*", ""))
        mon.process_event(EngineEvent(
            AlertDomain.VENDOR_ESSENCE, AlertScope.RECEIVER,
            EventId.VENDOR_ESSENCE_START, EventState.NORMAL, 1,
            "test-receiver", "*", ""))
        mon.process_event(EngineEvent(
            AlertDomain.TRANSPORT, AlertScope.RECEIVER,
            EventId.TRANSPORT_OK, EventState.NORMAL, 1,
            "test-receiver", "*", ""))
        mon.process_event(EngineEvent(
            AlertDomain.ESSENCE, AlertScope.RECEIVER,
            EventId.ESSENCE_OK, EventState.NORMAL, 1,
            "test-receiver", "*", ""))
        mon.process_event(EngineEvent(
            AlertDomain.LINK, AlertScope.RECEIVER,
            EventId.LINK_OK, EventState.NORMAL, 1,
            "test-receiver", "*", ""))
        mon.process_event(EngineEvent(
            AlertDomain.CLOCK, AlertScope.RECEIVER,
            EventId.CLOCK_OK, EventState.NORMAL, 1,
            "test-receiver", "*", ""))
        mon.tick()
        assert mon.overall_status == NC_HEALTHY

        # Deactivate
        mon.process_event(EngineEvent(
            AlertDomain.VENDOR_TRANSPORT, AlertScope.RECEIVER,
            EventId.VENDOR_TRANSPORT_DEACTIVATE, EventState.INACTIVE, 1,
            "test-receiver", "*", ""))
        mon.tick()

        # BCP-008-01: connectionStatus, streamStatus → Inactive immediately
        assert mon.transport.status == NC_INACTIVE, "connectionStatus must go Inactive"
        assert mon.essence.status == NC_INACTIVE, "streamStatus must go Inactive"
        assert mon.overall_status == NC_INACTIVE, "overallStatus must go Inactive"

    def test_message_reset_on_activation(self) -> None:
        """BCP-008: If autoResetCountersAndMessages is enabled, receivers/senders
        MUST reset ALL status message properties after each activation."""
        mon = ResourceMonitor("test-sender", is_sender=True)

        # Activate, cause error, set message
        mon.process_event(self._make_event(
            AlertDomain.VENDOR_TRANSPORT, EventId.VENDOR_TRANSPORT_ACTIVATE))
        e = EngineEvent(AlertDomain.LINK, AlertScope.SENDER, EventId.LINK_DOWN,
                        EventState.ERROR, 1, "test-sender", "*", "link down")
        mon.process_event(e)
        mon.activation_time = time.monotonic() - 10.0
        mon.tick()
        assert mon.overall_message == "link down"

        # Re-activate — must reset message
        mon.process_event(self._make_event(
            AlertDomain.VENDOR_TRANSPORT, EventId.VENDOR_TRANSPORT_ACTIVATE))
        assert mon.overall_message == "", "Message must reset on activation"

    def test_deactivation_no_intermediate_unhealthy(self) -> None:
        """BCP-008: MUST 'cleanly disconnect from the current stream by not
        generating intermediate unhealthy states and instead transition directly
        and immediately to Inactive'."""
        mon = ResourceMonitor("test-sender", is_sender=True)

        # Activate and make fully healthy
        mon.process_event(self._make_event(
            AlertDomain.VENDOR_TRANSPORT, EventId.VENDOR_TRANSPORT_ACTIVATE))
        mon.process_event(self._make_event(
            AlertDomain.VENDOR_ESSENCE, EventId.VENDOR_ESSENCE_START))
        mon.process_event(self._make_event(AlertDomain.TRANSPORT, EventId.TRANSPORT_OK))
        mon.process_event(self._make_event(AlertDomain.ESSENCE, EventId.ESSENCE_OK))
        mon.process_event(self._make_event(AlertDomain.LINK, EventId.LINK_OK))
        mon.process_event(self._make_event(AlertDomain.CLOCK, EventId.CLOCK_OK))
        mon.tick()
        assert mon.overall_status == NC_HEALTHY

        # Deactivate — capture status before and after
        prev_transport = mon.transport.status
        prev_essence = mon.essence.status
        assert prev_transport == NC_HEALTHY
        assert prev_essence == NC_HEALTHY

        mon.process_event(self._make_event(
            AlertDomain.VENDOR_TRANSPORT, EventId.VENDOR_TRANSPORT_DEACTIVATE))
        mon.tick()

        # Must go directly to Inactive — no Unhealthy intermediate
        assert mon.transport.status == NC_INACTIVE
        assert mon.essence.status == NC_INACTIVE
        assert mon.overall_status == NC_INACTIVE
        # Counter must NOT have incremented (Inactive transitions are ignored)
        assert mon.transport.counter == 0
        assert mon.essence.counter == 0


# ---------------------------------------------------------------------------
# Integration: Full pipeline test (Node → events → monitor source update)
# ---------------------------------------------------------------------------

class TestStatusMonitorIntegration:
    """Test the full event → monitor source pipeline with a real Node."""

    @pytest.mark.asyncio
    async def test_events_update_monitor_source(self) -> None:
        """Emit events to node.event_queue → run_status_monitor → monitor source updated."""
        import asyncio

        from nmos.node import Node
        from nmos.node.tests.test_is11 import _make_node, _build_config
        from nmos.node.status_monitor import run_status_monitor, NC_HEALTHY, NC_INACTIVE
        from nmos.node.events import EngineEvent, AlertDomain, AlertScope, EventId, EventState

        node = _make_node()
        _build_config(node, "config1")

        # Get first sender's ID
        sender_id = None
        for sid, sender in node.senders:
            sender_id = sender.ResourceCore.Id.value
            break
        assert sender_id is not None

        # Start the status monitor as a background task
        monitor_task = asyncio.create_task(run_status_monitor(node))

        # Emit activation events (sender activate + sender starting)
        for domain, event_id in [
            (AlertDomain.VENDOR_TRANSPORT, EventId.VENDOR_TRANSPORT_ACTIVATE),
            (AlertDomain.VENDOR_ESSENCE, EventId.VENDOR_ESSENCE_START),
            (AlertDomain.TRANSPORT, EventId.TRANSPORT_OK),
            (AlertDomain.ESSENCE, EventId.ESSENCE_OK),
            (AlertDomain.LINK, EventId.LINK_OK),
            (AlertDomain.CLOCK, EventId.CLOCK_OK),
        ]:
            node.event_queue.put_nowait(EngineEvent(
                domain=domain, scope=AlertScope.SENDER,
                event=event_id, state=EventState.NORMAL,
                count=1, id=sender_id, name="*", info="test",
            ))

        # Wait for the monitor to process events + tick
        await asyncio.sleep(2.0)

        # Cancel the monitor
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass

        # Verify the monitor source was updated
        from nmos.node.store import to_static_id
        static_id = to_static_id(sender_id)
        resource = node.senders.get(static_id)
        assert resource is not None
        assert hasattr(resource, 'Monitor') and resource.Monitor.defined

        mon_src = resource.Monitor.value
        mon_inner = mon_src.get() if hasattr(mon_src, 'get') else mon_src
        assert hasattr(mon_inner, 'MonitorState') and mon_inner.MonitorState.defined

        ms = mon_inner.MonitorState.value
        overall = ms.MonitorOverallStatus.value
        transmission = ms.MonitorTransmissionStatus.value
        link = ms.MonitorLinkStatus.value
        print(f"Overall: {overall}, Transmission: {transmission}, Link: {link}")
        assert overall == NC_HEALTHY, f"Expected overall=Healthy(1), got {overall}"
        assert transmission == NC_HEALTHY, f"Expected transmission=Healthy(1), got {transmission}"
        assert link == NC_HEALTHY, f"Expected link=Healthy(1), got {link}"


class TestDeactivatedResourceStaysInactive:
    """Reaching Inactive is not the same as staying Inactive.

    BCP-008-01 §"Deactivating a receiver" requires overallStatus,
    connectionStatus and streamStatus to reach Inactive on deactivation;
    BCP-008-02 §"Deactivating a sender" says the same for overallStatus,
    transmissionStatus and essenceStatus. And the overallStatus mapping is
    explicit that "When the Receiver is Inactive the overallStatus uses the
    Inactive option".

    Only the transition was implemented. ``process_one_domain`` publishes
    without delay whenever either side of a transition is Inactive — the rule
    that makes activation responsive — so any event arriving after the
    deactivation flipped the status straight back out of Inactive. One
    trailing ``TRANSPORT_OK`` from a stream that had recovered moments before
    shutdown left a deactivated resource reporting Healthy overall.
    """

    @staticmethod
    def _emit(mon: ResourceMonitor, domain: int, eid: int, info: str = "") -> None:
        scope = AlertScope.SENDER if mon.is_sender else AlertScope.RECEIVER
        mon.process_event(EngineEvent(domain, scope, eid, EventState.WARNING,
                                      1, mon.resource_id, "lo", info))

    def _activated(self, is_sender: bool) -> ResourceMonitor:
        mon = ResourceMonitor("res-1", is_sender=is_sender)
        self._emit(mon, AlertDomain.VENDOR_TRANSPORT,
                   EventId.VENDOR_TRANSPORT_ACTIVATE)
        self._emit(mon, AlertDomain.VENDOR_ESSENCE,
                   EventId.VENDOR_ESSENCE_START)
        assert mon.transport.status == NC_HEALTHY
        assert mon.essence.status == NC_HEALTHY
        return mon

    def _deactivate(self, mon: ResourceMonitor) -> None:
        """The pair the transports emit on shutdown, in order."""
        self._emit(mon, AlertDomain.VENDOR_ESSENCE,
                   EventId.VENDOR_ESSENCE_STOP, "stopping")
        self._emit(mon, AlertDomain.VENDOR_TRANSPORT,
                   EventId.VENDOR_TRANSPORT_DEACTIVATE, "deactivate")

    @pytest.mark.parametrize("is_sender", [False, True])
    def test_deactivation_reaches_inactive(self, is_sender: bool) -> None:
        mon = self._activated(is_sender)
        self._deactivate(mon)
        assert mon.transport.status == NC_INACTIVE
        assert mon.essence.status == NC_INACTIVE
        assert mon.overall_status == NC_INACTIVE

    @pytest.mark.parametrize("is_sender", [False, True])
    def test_trailing_events_cannot_resurrect_a_deactivated_resource(
        self, is_sender: bool,
    ) -> None:
        """The regression, for both directions.

        ``ESSENCE_OK`` alone used to revive streamStatus / essenceStatus, and
        ``TRANSPORT_OK`` used to revive connectionStatus / transmissionStatus
        *and* overallStatus.
        """
        mon = self._activated(is_sender)
        self._deactivate(mon)

        for domain, eid in (
            (AlertDomain.ESSENCE, EventId.ESSENCE_OK),
            (AlertDomain.TRANSPORT, EventId.TRANSPORT_OK),
            (AlertDomain.TRANSPORT, EventId.TRANSPORT_PACKET_LATE),
            (AlertDomain.ESSENCE, EventId.ESSENCE_STREAM_ERROR),
            (AlertDomain.VENDOR_ESSENCE, EventId.VENDOR_ESSENCE_START),
        ):
            self._emit(mon, domain, eid, "late arrival")
            assert mon.transport.status == NC_INACTIVE, (
                f"{eid} moved the transport domain off Inactive")
            assert mon.essence.status == NC_INACTIVE, (
                f"{eid} moved the essence domain off Inactive")
            assert mon.overall_status == NC_INACTIVE, (
                f"{eid} left a deactivated resource reporting "
                f"{mon.overall_status}")

    @pytest.mark.parametrize("is_sender", [False, True])
    def test_a_resource_never_activated_stays_inactive(
        self, is_sender: bool,
    ) -> None:
        mon = ResourceMonitor("res-1", is_sender=is_sender)
        for domain, eid in ((AlertDomain.TRANSPORT, EventId.TRANSPORT_OK),
                            (AlertDomain.ESSENCE, EventId.ESSENCE_OK)):
            self._emit(mon, domain, eid)
        assert mon.transport.status == NC_INACTIVE
        assert mon.essence.status == NC_INACTIVE
        assert mon.overall_status == NC_INACTIVE

    @pytest.mark.parametrize("is_sender", [False, True])
    def test_reactivation_still_works(self, is_sender: bool) -> None:
        """The gate must not be a one-way door."""
        mon = self._activated(is_sender)
        self._deactivate(mon)
        self._emit(mon, AlertDomain.VENDOR_TRANSPORT,
                   EventId.VENDOR_TRANSPORT_ACTIVATE)
        assert mon.transport.status == NC_HEALTHY
        assert mon.essence.status == NC_HEALTHY
        assert mon.overall_status == NC_HEALTHY

    def test_link_is_not_gated_by_activation(self) -> None:
        """linkStatus keeps describing the interface while the resource is idle.

        It appears in neither specification's deactivation list, and
        BCP-008-01 §"Link Status" scopes it to "the health of all the physical
        links" — which does not stop being meaningful when a receiver is idle.
        The published value is still delayed by the reporting delay, so the
        tick is what makes the distinction between "not gated" and "gated"
        observable.
        """
        mon = self._activated(is_sender=False)
        self._deactivate(mon)
        self._emit(mon, AlertDomain.LINK, EventId.LINK_DOWN, "cable unplugged")
        mon.activation_time = time.monotonic() - 10.0
        mon.tick()
        assert mon.link.status == NC_UNHEALTHY, (
            "link status was gated by activation; it describes the interface, "
            "not the stream"
        )
        # ...and it must not drag overall status out of Inactive.
        assert mon.overall_status == NC_INACTIVE
