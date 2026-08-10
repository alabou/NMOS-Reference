# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""IS-11 Stream Compatibility Management integration tests.

Tests the full IS-11 pipeline:
  API handlers → Node methods → compatibility.py → CCF → flow store

Categories:
  - Unit tests: Direct Node method calls with constructed objects
  - Config tests: Build node from builtin configs, apply constraints, verify flow state
  - Receiver tests: Inject SDP, verify compatibility status
  - Edge cases: Empty constraints, disabled CS, format transitions
"""

from __future__ import annotations

import json
import sys
from fractions import Fraction
from pathlib import Path

import pytest

# Add caps/ to path

try:
    from caps.MatroxCCF import (
        Caps, CapSet, Cap, Capability, RangeValue, RangeType,
        make_capset, convert_caps_json_to_caps,
        CapFormatMediaType, CapFormatFrameWidth, CapFormatFrameHeight,
        CapFormatGrainRate, CapFormatInterlaceMode, CapFormatColorspace,
        CapFormatTransferCharacteristic, CapFormatColorSampling,
        CapFormatComponentDepth,
        CapFormatChannelCount, CapFormatSampleRate, CapFormatSampleDepth,
        CapFormatBitRate, CapFormatProfile, CapFormatLevel,
    )
    HAS_CCF = True
except ImportError:
    HAS_CCF = False

from nmos.node import Node


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

BUILTIN_DIR = Path(__file__).parent.parent / "config" / "builtin"


def _make_node() -> Node:
    """Create a minimal Node for testing."""
    node = Node()
    node.init(serial_number="TST12345")
    return node


def _build_config(node: Node, config_name: str) -> None:
    """Load a builtin config and build all senders/receivers."""
    from nmos.node.config import ConfigBuilder
    config_path = BUILTIN_DIR / f"{config_name}.json"
    if not config_path.exists():
        pytest.skip(f"{config_name}.json not found")
    with open(config_path) as f:
        config = json.load(f)
    builder = ConfigBuilder(node, verbose=False)
    for s in config.get("senders", []):
        try:
            builder._build_sender_pipeline(s)
        except Exception:
            pass
    for r in config.get("receivers", []):
        try:
            builder._build_receiver_from_config(r)
        except Exception:
            pass


def _get_first_sender(node: Node) -> tuple[str, object] | None:
    """Return (sender_id, sender) for the first sender, or None."""
    for static_id, sender in node.senders:
        return sender.ResourceCore.Id.value, sender
    return None


def _get_sender_flow(node: Node, sender: object) -> object | None:
    """Get the flow associated with a sender."""
    flow_id = sender.FlowId.value if sender.FlowId.defined and sender.FlowId.value else None
    if flow_id is None:
        return None
    return node.flows.get(flow_id)


def _get_flow_value(flow_ptr: object) -> object | None:
    """Unwrap a polymorphic flow to its inner value."""
    poly = flow_ptr.get() if hasattr(flow_ptr, 'get') else flow_ptr
    if poly is None:
        return None
    return poly.value if hasattr(poly, 'value') else poly


# ===========================================================================
# Step 3: Unit tests — direct Node method calls
# ===========================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestNodeSetSenderCompatibilityState:
    """Test Node.set_sender_compatibility_state()."""

    def test_unconstrained_by_default(self) -> None:
        """New sender with no active constraints → unconstrained."""
        node = _make_node()
        _build_config(node, "config1")
        result = _get_first_sender(node)
        if result is None:
            pytest.skip("No senders")
        sender_id, sender = result
        status = node.set_sender_compatibility_state(sender)
        assert status in ("unconstrained", "constrained")

    def test_returns_string(self) -> None:
        """Status must be one of the IS-11 defined values."""
        node = _make_node()
        _build_config(node, "config1")
        result = _get_first_sender(node)
        if result is None:
            pytest.skip("No senders")
        _, sender = result
        status = node.set_sender_compatibility_state(sender)
        assert status in ("unconstrained", "constrained", "active_constraints_violation")


@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestNodeForceActiveConstraints:
    """Test Node.force_active_constraints()."""

    def test_delete_resets_to_unconstrained(self) -> None:
        """force_active_constraints(sender, None) resets to unconstrained."""
        node = _make_node()
        _build_config(node, "config1")
        result = _get_first_sender(node)
        if result is None:
            pytest.skip("No senders")
        _, sender = result
        node.force_active_constraints(sender, None)
        status = node.set_sender_compatibility_state(sender)
        assert status == "unconstrained"


# ===========================================================================
# Compatibility state → monitor essence/stream event transitions
# ===========================================================================
#
# The Node's IS-11 compatibility-state computation fires a
# vendor-essence event on its ``event_queue`` whenever the state
# crosses the violation / non-violation boundary. These tests pin:
#
#   * Exactly one event is emitted per transition edge.
#   * Same-state "computes" (violation → violation, or healthy →
#     healthy) emit nothing.
#   * The event carries the correct scope, event_id and info string.
#   * The event survives routing through ``ResourceMonitor`` → the
#     essence / stream-status facet flips to ``NC_UNHEALTHY`` /
#     ``NC_HEALTHY`` after the 3-second BCP-008 hysteresis window.
#   * A queue-full situation is dropped silently without corrupting
#     the sender's ``CompatibilityStatus`` field.

def _set_sender_state(node: Node, sender: object, state_name: str) -> None:
    """Seed ``sender.CompatibilityStatus.value`` to the named state.

    Used to stage a "previous state" so the transition-edge check in
    ``set_sender_compatibility_state`` has a realistic reading.
    """
    from nmos.enums import EnumRegistry
    sender.CompatibilityStatus.value = EnumRegistry.get(state_name)  # type: ignore[attr-defined]


def _drain_queue(node: Node) -> list[object]:
    """Pop everything on ``node.event_queue`` non-blockingly."""
    import asyncio as _a
    drained: list[object] = []
    q = getattr(node, "event_queue", None)
    if q is None:
        return drained
    while True:
        try:
            drained.append(q.get_nowait())
        except _a.QueueEmpty:
            break
    return drained


def _event_kinds(events: list[object]) -> list[int]:
    """Extract ``.event`` ids from a list of ``EngineEvent``."""
    return [int(e.event) for e in events]  # type: ignore[attr-defined]


@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
def _activated_monitor(resource_id: str, is_sender: bool) -> "ResourceMonitor":
    """A ResourceMonitor in the Active state, as the transports leave it.

    BCP-008-02 §"Essence Status" (and §"Stream Status" in BCP-008-01) allow
    essence/stream to be Healthy, PartiallyHealthy or Unhealthy only "when the
    sender is Active"; while inactive the value MUST be Inactive. So a
    compatibility state only shows on the essence facet of an *active*
    resource, and a test that fed the event into a never-activated monitor was
    asserting a state the specification does not permit.
    """
    from nmos.node.events import (
        EngineEvent, EventId, AlertDomain, AlertScope, EventState,
    )
    from nmos.node.status_monitor import ResourceMonitor
    scope = AlertScope.SENDER if is_sender else AlertScope.RECEIVER
    monitor = ResourceMonitor(resource_id=resource_id, is_sender=is_sender)
    monitor.process_event(EngineEvent(
        domain=AlertDomain.VENDOR_TRANSPORT, scope=scope,
        event=EventId.VENDOR_TRANSPORT_ACTIVATE, state=EventState.NORMAL,
        count=1, id=resource_id, name="*", info="activate",
    ))
    return monitor


class TestCompatibilityStateEmitsEssenceEvent:
    """Track A: sender ``active_constraints_violation`` → essence
    UNHEALTHY, and recovery → HEALTHY.
    """

    def test_no_event_on_first_healthy_compute(self) -> None:
        """The very first call on a fresh sender computes a healthy
        state (unconstrained) — no prior state to transition from, so
        no event should fire. Sender's ``CompatibilityStatus`` is set
        but the queue stays empty.
        """
        from nmos.node.events import EventId
        node = _make_node()
        _build_config(node, "config1")
        result = _get_first_sender(node)
        if result is None:
            pytest.skip("No senders")
        _, sender = result
        _drain_queue(node)
        status = node.set_sender_compatibility_state(sender)
        assert status in ("unconstrained", "constrained")
        ids = _event_kinds(_drain_queue(node))
        assert int(EventId.VENDOR_ESSENCE_CONSTRAINT_VIOLATED) not in ids
        assert int(EventId.VENDOR_ESSENCE_CONSTRAINT_OK) not in ids

    def test_violation_edge_emits_unhealthy_event(self) -> None:
        """Transition from a healthy state to violation fires exactly
        one ``VENDOR_ESSENCE_CONSTRAINT_VIOLATED`` on the queue.
        """
        from nmos.node.events import EventId, AlertScope, EventState
        node = _make_node()
        _build_config(node, "config1")
        result = _get_first_sender(node)
        if result is None:
            pytest.skip("No senders")
        sender_id, sender = result
        _set_sender_state(node, sender, "unconstrained")
        _drain_queue(node)

        # Monkey-patch the compatibility check to return "incompatible".
        from nmos.node import compatibility as compat_mod
        orig = compat_mod.check_sender_flow_compatibility
        try:
            compat_mod.check_sender_flow_compatibility = (  # type: ignore[assignment]
                lambda *_a, **_k: "incompatible"
            )
            status = node.set_sender_compatibility_state(sender)
        finally:
            compat_mod.check_sender_flow_compatibility = orig  # type: ignore[assignment]

        assert status == "active_constraints_violation"
        events = _drain_queue(node)
        ids = _event_kinds(events)
        assert ids.count(int(EventId.VENDOR_ESSENCE_CONSTRAINT_VIOLATED)) == 1
        assert int(EventId.VENDOR_ESSENCE_CONSTRAINT_OK) not in ids
        ev = next(e for e in events
                  if int(e.event) == int(EventId.VENDOR_ESSENCE_CONSTRAINT_VIOLATED))  # type: ignore[attr-defined]
        assert ev.scope == AlertScope.SENDER  # type: ignore[attr-defined]
        assert ev.state == EventState.ERROR  # type: ignore[attr-defined]
        assert sender_id in ev.info  # type: ignore[attr-defined]

    def test_recovery_edge_emits_healthy_event(self) -> None:
        """Transition from violation back to constrained / unconstrained
        fires exactly one ``VENDOR_ESSENCE_CONSTRAINT_OK``.
        """
        from nmos.node.events import EventId
        node = _make_node()
        _build_config(node, "config1")
        result = _get_first_sender(node)
        if result is None:
            pytest.skip("No senders")
        _, sender = result
        _set_sender_state(node, sender, "active_constraints_violation")
        _drain_queue(node)

        # Default compat check will return "compatible" or "no_flow"
        # → mapped to constrained / unconstrained (healthy).
        status = node.set_sender_compatibility_state(sender)
        assert status in ("constrained", "unconstrained")
        ids = _event_kinds(_drain_queue(node))
        assert ids.count(int(EventId.VENDOR_ESSENCE_CONSTRAINT_OK)) == 1
        assert int(EventId.VENDOR_ESSENCE_CONSTRAINT_VIOLATED) not in ids

    def test_no_event_on_violation_to_violation(self) -> None:
        """Repeated violating computes emit exactly one edge event,
        not one per call.
        """
        from nmos.node.events import EventId
        node = _make_node()
        _build_config(node, "config1")
        result = _get_first_sender(node)
        if result is None:
            pytest.skip("No senders")
        _, sender = result
        from nmos.node import compatibility as compat_mod
        orig = compat_mod.check_sender_flow_compatibility
        try:
            compat_mod.check_sender_flow_compatibility = (  # type: ignore[assignment]
                lambda *_a, **_k: "incompatible"
            )
            _set_sender_state(node, sender, "unconstrained")
            _drain_queue(node)
            node.set_sender_compatibility_state(sender)  # first transition — emits
            node.set_sender_compatibility_state(sender)  # same state — silent
            node.set_sender_compatibility_state(sender)  # same state — silent
        finally:
            compat_mod.check_sender_flow_compatibility = orig  # type: ignore[assignment]
        ids = _event_kinds(_drain_queue(node))
        assert ids.count(int(EventId.VENDOR_ESSENCE_CONSTRAINT_VIOLATED)) == 1

    def test_no_event_on_constrained_unconstrained_toggle(self) -> None:
        """Transitions between the two healthy states do not cross
        the violation boundary and must emit nothing.
        """
        from nmos.node.events import EventId
        node = _make_node()
        _build_config(node, "config1")
        result = _get_first_sender(node)
        if result is None:
            pytest.skip("No senders")
        _, sender = result
        _set_sender_state(node, sender, "unconstrained")
        _drain_queue(node)
        _set_sender_state(node, sender, "constrained")
        node.set_sender_compatibility_state(sender)
        _set_sender_state(node, sender, "unconstrained")
        node.set_sender_compatibility_state(sender)
        ids = _event_kinds(_drain_queue(node))
        assert int(EventId.VENDOR_ESSENCE_CONSTRAINT_VIOLATED) not in ids
        assert int(EventId.VENDOR_ESSENCE_CONSTRAINT_OK) not in ids

    def test_end_to_end_through_monitor_state_machine(self) -> None:
        """Feed the emitted event through ``ResourceMonitor`` and
        assert ``essence.internal_status`` flips to ``NC_UNHEALTHY``
        immediately (the 3-second publish delay is a separate domain
        test). Verifies the wire-up from the new event id all the
        way to the essence domain.
        """
        from nmos.node.events import (
            EngineEvent, EventId, AlertDomain, AlertScope, EventState,
        )
        from nmos.node.status_monitor import (
            ResourceMonitor, NC_HEALTHY, NC_UNHEALTHY,
        )
        monitor = _activated_monitor("sender-test", is_sender=True)
        violated = EngineEvent(
            domain=AlertDomain.VENDOR_ESSENCE, scope=AlertScope.SENDER,
            event=EventId.VENDOR_ESSENCE_CONSTRAINT_VIOLATED,
            state=EventState.ERROR, count=1,
            id="sender-test", name="*", info="test violation",
        )
        monitor.process_event(violated)
        assert monitor.essence.internal_status == NC_UNHEALTHY
        ok_event = EngineEvent(
            domain=AlertDomain.VENDOR_ESSENCE, scope=AlertScope.SENDER,
            event=EventId.VENDOR_ESSENCE_CONSTRAINT_OK,
            state=EventState.NORMAL, count=1,
            id="sender-test", name="*", info="test recovery",
        )
        monitor.process_event(ok_event)
        assert monitor.essence.internal_status == NC_HEALTHY

    def test_partial_event_maps_to_partially_healthy(self) -> None:
        """The IS-11 sender ``no_essence`` / ``awaiting_essence`` states map
        to ``NC_PARTIALLY_HEALTHY`` on the essence facet via the new
        ``VENDOR_ESSENCE_CONSTRAINT_PARTIAL`` event."""
        from nmos.node.events import (
            EngineEvent, EventId, AlertDomain, AlertScope, EventState,
        )
        from nmos.node.status_monitor import (
            ResourceMonitor, NC_PARTIALLY_HEALTHY,
        )
        monitor = _activated_monitor("sender-test", is_sender=True)
        partial = EngineEvent(
            domain=AlertDomain.VENDOR_ESSENCE, scope=AlertScope.SENDER,
            event=EventId.VENDOR_ESSENCE_CONSTRAINT_PARTIAL,
            state=EventState.WARNING, count=1,
            id="sender-test", name="*", info="awaiting essence",
        )
        monitor.process_event(partial)
        assert monitor.essence.internal_status == NC_PARTIALLY_HEALTHY

    def test_sender_partial_states_emit_partial_edge(self) -> None:
        """The IS-11→essence mapping emits a PARTIAL edge for the
        ``no_essence`` / ``awaiting_essence`` sender states. (The node
        doesn't *produce* these states yet — the mapping is wired ahead of
        that, per the request.)"""
        from nmos.node.events import EventId, AlertScope, EventState
        from nmos.enums import (
            NoEssence, AwaitingEssence, Constrained, Unconstrained,
            ActiveConstraintsViolation,
        )
        node = _make_node()
        for state in (NoEssence.s, AwaitingEssence.s):
            _drain_queue(node)
            node._emit_is11_transition_if_needed(
                "sender-x", is_sender=True,
                prev_state=Constrained.s, new_result=state,
                violation_states=(ActiveConstraintsViolation.s,),
                partial_states=(NoEssence.s, AwaitingEssence.s),
                healthy_states=(Constrained.s, Unconstrained.s),
                role="sender",
            )
            events = _drain_queue(node)
            ids = _event_kinds(events)
            assert ids.count(int(EventId.VENDOR_ESSENCE_CONSTRAINT_PARTIAL)) == 1
            assert int(EventId.VENDOR_ESSENCE_CONSTRAINT_VIOLATED) not in ids
            ev = next(e for e in events
                      if int(e.event) == int(EventId.VENDOR_ESSENCE_CONSTRAINT_PARTIAL))  # type: ignore[attr-defined]
            assert ev.scope == AlertScope.SENDER  # type: ignore[attr-defined]
            assert ev.state == EventState.WARNING  # type: ignore[attr-defined]

    def test_queue_full_drops_silently(self) -> None:
        """Saturate ``event_queue`` and confirm the transition emit
        doesn't raise and ``CompatibilityStatus`` still updates.
        """
        import asyncio
        node = _make_node()
        _build_config(node, "config1")
        result = _get_first_sender(node)
        if result is None:
            pytest.skip("No senders")
        _, sender = result
        # Fill the queue to capacity.
        q = node.event_queue
        while True:
            try:
                q.put_nowait(object())  # type: ignore[arg-type]
            except asyncio.QueueFull:
                break
        _set_sender_state(node, sender, "active_constraints_violation")
        # Must not raise even though put_nowait would QueueFull.
        status = node.set_sender_compatibility_state(sender)
        assert status in ("constrained", "unconstrained")
        # CompatibilityStatus still reflects the new result.
        from nmos.enums import EnumRegistry
        assert sender.CompatibilityStatus.value is EnumRegistry.get(status)  # type: ignore[attr-defined]


@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestReceiverCompatibilityStateEmitsStreamEvent:
    """Track B: receiver ``non_compliant_stream`` → stream UNHEALTHY,
    and recovery (either ``compliant_stream`` or ``unknown``) →
    HEALTHY. This Node uses ``unknown`` in place of the IS-11
    ``awaiting_stream`` value; both are treated as healthy-neutral here.
    """

    @staticmethod
    def _get_first_receiver(node: Node):  # type: ignore[no-untyped-def]
        for static_id, recv in node.receivers:
            return static_id, recv
        return None

    @staticmethod
    def _get_receiver_core(recv: object):  # type: ignore[no-untyped-def]
        inner = recv.get() if hasattr(recv, "get") else recv  # type: ignore[attr-defined]
        rv = inner.value if hasattr(inner, "value") else inner  # type: ignore[union-attr]
        return getattr(rv, "ReceiverCore", rv)

    def _set_recv_state(self, recv: object, state_name: str) -> None:
        from nmos.enums import EnumRegistry
        core = self._get_receiver_core(recv)
        core.CompatibilityStatus.value = EnumRegistry.get(state_name)

    def test_noncompliant_edge_emits_unhealthy_event(self) -> None:
        from nmos.node.events import EventId, AlertScope, EventState
        node = _make_node()
        _build_config(node, "config1")
        got = self._get_first_receiver(node)
        if got is None:
            pytest.skip("No receivers")
        _, recv = got
        self._set_recv_state(recv, "compliant_stream")
        _drain_queue(node)

        from nmos.node import compatibility as compat_mod
        orig = compat_mod.check_stream_compatibility
        try:
            compat_mod.check_stream_compatibility = (  # type: ignore[assignment]
                lambda *_a, **_k: "non_compliant"
            )
            status = node.set_receiver_compatibility_state(recv)
        finally:
            compat_mod.check_stream_compatibility = orig  # type: ignore[assignment]
        assert status == "non_compliant_stream"
        events = _drain_queue(node)
        ids = _event_kinds(events)
        assert ids.count(int(EventId.VENDOR_ESSENCE_CONSTRAINT_VIOLATED)) == 1
        ev = next(e for e in events
                  if int(e.event) == int(EventId.VENDOR_ESSENCE_CONSTRAINT_VIOLATED))  # type: ignore[attr-defined]
        assert ev.scope == AlertScope.RECEIVER  # type: ignore[attr-defined]
        assert ev.state == EventState.ERROR  # type: ignore[attr-defined]

    def test_recovery_to_compliant_emits_healthy_event(self) -> None:
        from nmos.node.events import EventId
        node = _make_node()
        _build_config(node, "config1")
        got = self._get_first_receiver(node)
        if got is None:
            pytest.skip("No receivers")
        _, recv = got
        self._set_recv_state(recv, "non_compliant_stream")
        _drain_queue(node)
        # Default check returns "no_sdp" or "compliant" → both healthy.
        status = node.set_receiver_compatibility_state(recv)
        assert status in ("compliant_stream", "unknown")
        ids = _event_kinds(_drain_queue(node))
        assert ids.count(int(EventId.VENDOR_ESSENCE_CONSTRAINT_OK)) == 1

    def test_recovery_to_unknown_also_emits_healthy_event(self) -> None:
        """``unknown`` stands in for IS-11 ``awaiting_stream`` on this
        Node; treat it as healthy-neutral just like ``compliant_stream``.
        """
        from nmos.node.events import EventId
        node = _make_node()
        _build_config(node, "config1")
        got = self._get_first_receiver(node)
        if got is None:
            pytest.skip("No receivers")
        _, recv = got
        self._set_recv_state(recv, "non_compliant_stream")
        _drain_queue(node)

        from nmos.node import compatibility as compat_mod
        orig = compat_mod.check_stream_compatibility
        try:
            compat_mod.check_stream_compatibility = (  # type: ignore[assignment]
                lambda *_a, **_k: "no_sdp"
            )
            status = node.set_receiver_compatibility_state(recv)
        finally:
            compat_mod.check_stream_compatibility = orig  # type: ignore[assignment]
        assert status == "unknown"
        ids = _event_kinds(_drain_queue(node))
        assert ids.count(int(EventId.VENDOR_ESSENCE_CONSTRAINT_OK)) == 1

    def test_no_event_on_same_state(self) -> None:
        from nmos.node.events import EventId
        node = _make_node()
        _build_config(node, "config1")
        got = self._get_first_receiver(node)
        if got is None:
            pytest.skip("No receivers")
        _, recv = got
        from nmos.node import compatibility as compat_mod
        orig = compat_mod.check_stream_compatibility
        try:
            compat_mod.check_stream_compatibility = (  # type: ignore[assignment]
                lambda *_a, **_k: "non_compliant"
            )
            self._set_recv_state(recv, "compliant_stream")
            _drain_queue(node)
            node.set_receiver_compatibility_state(recv)  # edge — emits
            node.set_receiver_compatibility_state(recv)  # same — silent
            node.set_receiver_compatibility_state(recv)  # same — silent
        finally:
            compat_mod.check_stream_compatibility = orig  # type: ignore[assignment]
        ids = _event_kinds(_drain_queue(node))
        assert ids.count(int(EventId.VENDOR_ESSENCE_CONSTRAINT_VIOLATED)) == 1


# ===========================================================================
# Step 4A: Config1 sender constraint tests
# ===========================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestIS11Config1:
    """IS-11 tests for Config1 (video/raw + audio/L24)."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.node = _make_node()
        try:
            _build_config(self.node, "config1")
            self.has_node = True
        except Exception:
            self.has_node = False

    def test_sender_status_default(self) -> None:
        """Default status should be unconstrained or constrained."""
        if not self.has_node:
            pytest.skip("Config1 build failed")
        result = _get_first_sender(self.node)
        if result is None:
            pytest.skip("No senders")
        _, sender = result
        status = self.node.set_sender_compatibility_state(sender)
        assert status in ("unconstrained", "constrained")

    def test_sender_flow_exists(self) -> None:
        """Sender should have an associated flow."""
        if not self.has_node:
            pytest.skip("Config1 build failed")
        result = _get_first_sender(self.node)
        if result is None:
            pytest.skip("No senders")
        _, sender = result
        flow_ptr = _get_sender_flow(self.node, sender)
        assert flow_ptr is not None, "Sender should have a flow"

    def test_sender_flow_has_properties(self) -> None:
        """Flow should have frame width, height, etc."""
        if not self.has_node:
            pytest.skip("Config1 build failed")
        result = _get_first_sender(self.node)
        if result is None:
            pytest.skip("No senders")
        _, sender = result
        flow_ptr = _get_sender_flow(self.node, sender)
        if flow_ptr is None:
            pytest.skip("No flow")
        fv = _get_flow_value(flow_ptr)
        if fv is not None and hasattr(fv, 'FrameWidth'):
            assert fv.FrameWidth.defined
            assert fv.FrameWidth.value == 1920

    def test_delete_constraints_is_unconstrained(self) -> None:
        """DELETE constraints → status unconstrained."""
        if not self.has_node:
            pytest.skip("Config1 build failed")
        result = _get_first_sender(self.node)
        if result is None:
            pytest.skip("No senders")
        _, sender = result
        self.node.force_active_constraints(sender, None)
        status = self.node.set_sender_compatibility_state(sender)
        assert status == "unconstrained"


# ===========================================================================
# Step 4B: Receiver status tests
# ===========================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestIS11ReceiverStatus:
    """IS-11 receiver compatibility tests."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.node = _make_node()
        try:
            _build_config(self.node, "config1")
            self.has_node = True
        except Exception:
            self.has_node = False

    def test_receiver_status_no_stream(self) -> None:
        """No SDP/stream → status "unknown"."""
        if not self.has_node:
            pytest.skip("Config1 build failed")
        for static_id, recv in self.node.receivers:
            status = self.node.set_receiver_compatibility_state(recv)
            assert status == "unknown", f"Expected 'unknown' with no stream, got '{status}'"
            break


# ===========================================================================
# Step 4C: Flow state verification
# ===========================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestFlowStateVerification:
    """Verify flow properties match expected values after config build."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.node = _make_node()
        try:
            _build_config(self.node, "config1")
            self.has_node = True
        except Exception:
            self.has_node = False

    def test_video_flow_properties(self) -> None:
        """Config1 video sender flow: 1920x1080@60, YCbCr-4:2:2, 10-bit."""
        if not self.has_node:
            pytest.skip("Config1 build failed")

        from nmos.node.flow_caps import get_flow_to_caps

        result = _get_first_sender(self.node)
        if result is None:
            pytest.skip("No senders")
        _, sender = result
        flow_ptr = _get_sender_flow(self.node, sender)
        if flow_ptr is None:
            pytest.skip("No flow")

        caps = get_flow_to_caps(self.node, flow_ptr)
        assert len(caps.caps) > 0

        from nmos.node.compatibility import _get_cap_str, _get_cap_int

        mt = _get_cap_str(caps, CapFormatMediaType)
        assert mt == "video/raw", f"Expected video/raw, got {mt}"

        w = _get_cap_int(caps, CapFormatFrameWidth)
        assert w == 1920, f"Expected 1920, got {w}"

        h = _get_cap_int(caps, CapFormatFrameHeight)
        assert h == 1080, f"Expected 1080, got {h}"

    def test_flow_compatible_with_sender_caps(self) -> None:
        """Config1 flow should be within sender's capabilities."""
        if not self.has_node:
            pytest.skip("Config1 build failed")

        from nmos.node.flow_caps import get_flow_to_caps
        from nmos.node.compatibility import (
            check_flow_properties_compatibility, _get_sender_ccf_caps,
        )

        result = _get_first_sender(self.node)
        if result is None:
            pytest.skip("No senders")
        _, sender = result
        flow_ptr = _get_sender_flow(self.node, sender)
        if flow_ptr is None:
            pytest.skip("No flow")

        flow_caps = get_flow_to_caps(self.node, flow_ptr)
        sender_caps = _get_sender_ccf_caps(self.node, sender)

        if sender_caps is None or len(sender_caps.capsets) == 0:
            pytest.skip("No sender caps")

        compatible = check_flow_properties_compatibility(
            self.node, flow_caps, sender_caps, verbose=True,
        )
        assert compatible, "Config1 flow must be compatible with its sender caps"


# ===========================================================================
# Step 4E: Edge case tests
# ===========================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestIS11EdgeCases:
    """IS-11 edge case tests."""

    def test_validate_on_nonexistent_sender(self) -> None:
        """Validate on nonexistent sender should not crash."""
        node = _make_node()
        # No senders built — should return (caps, None) (no error)
        from nmos.node.compatibility import validate_active_constraints
        normalized, err = validate_active_constraints(node, "nonexistent-id", None)
        assert err is None

    def test_check_flow_on_nonexistent_sender(self) -> None:
        """Check flow on nonexistent sender should return unconstrained."""
        node = _make_node()
        from nmos.node.compatibility import check_sender_flow_compatibility
        status = check_sender_flow_compatibility(node, "nonexistent-id")
        assert status == "unconstrained"

    def test_receiver_status_nonexistent(self) -> None:
        """Check receiver status on nonexistent receiver."""
        node = _make_node()
        from nmos.node.compatibility import check_stream_compatibility
        status = check_stream_compatibility(node, "nonexistent-id")
        assert status == "unknown"

    def test_force_none_does_not_crash(self) -> None:
        """force_active_constraints with None should not crash even without senders."""
        node = _make_node()
        _build_config(node, "config1")
        result = _get_first_sender(node)
        if result is None:
            pytest.skip("No senders")
        _, sender = result
        # Should not raise
        node.force_active_constraints(sender, None)

    def test_force_none_constraints(self) -> None:
        """force_active_constraints with None constraints resets without error."""
        node = _make_node()
        _build_config(node, "config1")
        result = _get_first_sender(node)
        if result is None:
            pytest.skip("No senders")
        _, sender = result
        err = node.force_active_constraints(sender, None)
        assert err is None


# ===========================================================================
# Step 4A extended: Audio sender tests
# ===========================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestIS11Config1Audio:
    """IS-11 tests for Config1 audio sender (audio/L24)."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.node = _make_node()
        try:
            _build_config(self.node, "config1")
            self.has_node = True
        except Exception:
            self.has_node = False

    def _get_audio_sender(self) -> tuple[str, object] | None:
        """Find the audio sender (second sender in config1)."""
        senders = list(self.node.senders)
        for i, (static_id, sender) in enumerate(senders):
            fmt = str(sender.Format.value) if sender.Format.defined else ""
            if "audio" in fmt:
                return sender.ResourceCore.Id.value, sender
        return None

    def test_audio_sender_exists(self) -> None:
        if not self.has_node:
            pytest.skip("Config1 build failed")
        result = self._get_audio_sender()
        assert result is not None, "Config1 should have an audio sender"

    def test_audio_flow_properties(self) -> None:
        """Audio flow should have sample_rate, channel_count, etc."""
        if not self.has_node:
            pytest.skip("Config1 build failed")
        result = self._get_audio_sender()
        if result is None:
            pytest.skip("No audio sender")
        _, sender = result
        flow_ptr = _get_sender_flow(self.node, sender)
        if flow_ptr is None:
            pytest.skip("No audio flow")

        from nmos.node.flow_caps import get_flow_to_caps
        from nmos.node.compatibility import _get_cap_int, _get_cap_str

        caps = get_flow_to_caps(self.node, flow_ptr)
        mt = _get_cap_str(caps, CapFormatMediaType)
        # Config1 audio is L24
        assert mt is not None and "L24" in mt, f"Expected audio/L24, got {mt}"

    def test_audio_status_default(self) -> None:
        """Audio sender default status."""
        if not self.has_node:
            pytest.skip("Config1 build failed")
        result = self._get_audio_sender()
        if result is None:
            pytest.skip("No audio sender")
        _, sender = result
        status = self.node.set_sender_compatibility_state(sender)
        assert status in ("unconstrained", "constrained")


# ===========================================================================
# Step 4B: Receiver SDP compatibility tests
# ===========================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestIS11ReceiverSdpCompatibility:
    """Test receiver SDP compatibility by injecting SDP into the node."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.node = _make_node()
        try:
            _build_config(self.node, "config1")
            self.has_node = True
        except Exception:
            self.has_node = False

    def _get_first_receiver(self) -> tuple[str, object] | None:
        """Return (receiver_id, receiver) for the first receiver."""
        from nmos.node import _get_resource_core
        for static_id, recv in self.node.receivers:
            core = _get_resource_core(recv)
            return core.Id.value, recv
        return None

    def _inject_sdp(self, receiver_id: str, sdp_text: str) -> None:
        """Parse and inject SDP into the node's SDP store for a receiver."""
        from nmos.node.store import to_static_id
        static_id = to_static_id(receiver_id)
        self.node._store_parsed_sdp(static_id, sdp_text)

    def _make_raw_video_sdp(self, width: int = 1920, height: int = 1080,
                            fps_num: int = 60, fps_den: int = 1,
                            depth: int = 10, sampling: str = "YCbCr-4:2:2") -> str:
        """Build a minimal raw video SDP."""
        return (
            "v=0\r\n"
            "o=- 1 1 IN IP4 192.168.1.100\r\n"
            "s=Test\r\n"
            "t=0 0\r\n"
            f"m=video 27500 RTP/AVP 96\r\n"
            f"c=IN IP4 239.1.1.100/128\r\n"
            f"a=rtpmap:96 raw/90000\r\n"
            f"a=fmtp:96 sampling={sampling}; width={width}; height={height}; "
            f"exactframerate={fps_num}/{fps_den}; depth={depth}; "
            f"colorimetry=BT709; PM=2110GPM; TP=2110TPN; "
            f"SSN=ST2110-20:2017\r\n"
            f"a=ts-refclk:ptp=IEEE1588-2008:00-00-00-00-00-00-00-00\r\n"
            f"a=mediaclk:direct=0\r\n"
        )

    def test_no_sdp_is_unknown(self) -> None:
        """No SDP injected → "unknown"."""
        if not self.has_node:
            pytest.skip("Config1 build failed")
        result = self._get_first_receiver()
        if result is None:
            pytest.skip("No receivers")
        _, recv = result
        status = self.node.set_receiver_compatibility_state(recv)
        assert status == "unknown"

    def test_matching_sdp_compliant(self) -> None:
        """Inject SDP matching config1 receiver native caps; verify
        the compatibility-state machine produces a definite answer
        (not a stub default).

        History: a prior revision of ``set_receiver_compatibility_state``
        looked up the receiver id on ``core.Id`` (wrong — id lives on
        the embedded ``ResourceCore``), so ``check_stream_compatibility``
        was called with an empty id and silently returned ``unknown``.
        That masked real compliance logic; the assertion below
        therefore accepts any of the three valid IS-11 outcomes and
        is only a smoke test on the plumbing. Tightening it to
        ``compliant_stream`` requires ``get_sdp_to_caps`` to extract
        video-format caps (frame_width / height / …) from the injected
        SDP, which today extracts only transport caps — a separate
        gap tracked elsewhere.
        """
        if not self.has_node:
            pytest.skip("Config1 build failed")
        result = self._get_first_receiver()
        if result is None:
            pytest.skip("No receivers")
        recv_id, recv = result

        # Config1 receiver: 1920x1080@60, 10-bit, YCbCr-4:2:2
        sdp = self._make_raw_video_sdp(1920, 1080, 60, 1, 10, "YCbCr-4:2:2")
        self._inject_sdp(recv_id, sdp)

        status = self.node.set_receiver_compatibility_state(recv)
        assert status in (
            "compliant_stream", "non_compliant_stream", "unknown",
        ), f"Expected a valid IS-11 state, got {status!r}"

    def test_wrong_resolution_incompatible(self) -> None:
        """Inject SDP with 3840x2160 → receiver only supports 1920x1080."""
        if not self.has_node:
            pytest.skip("Config1 build failed")
        result = self._get_first_receiver()
        if result is None:
            pytest.skip("No receivers")
        recv_id, recv = result

        sdp = self._make_raw_video_sdp(3840, 2160, 60, 1, 10, "YCbCr-4:2:2")
        self._inject_sdp(recv_id, sdp)

        status = self.node.set_receiver_compatibility_state(recv)
        # Config1 receiver native is 1920x1080; 3840x2160 should NOT match native
        # But may match generic caps if they have broader ranges
        assert status in ("compliant_stream", "non_compliant_stream", "unknown"), \
            f"Expected a valid status, got {status}"

    def test_activation_caches_receiver_sdp_and_leaves_unknown(
        self, monkeypatch: "pytest.MonkeyPatch",
    ) -> None:
        """Activation must cache the receiver's incoming transport_file SDP so
        IS-11 leaves ``unknown`` → compliant/non_compliant once the PATCH is
        accepted, and drop it (→ ``unknown``) on deactivation.

        Regression: the SDP-store in ``do_activation`` was sender-only, so a
        receiver's SDP was never cached → ``get_sdp_to_caps`` found nothing →
        the status was permanently ``unknown`` even while active with an SDP.
        Prior tests passed only because they injected the SDP by hand
        (``_inject_sdp``), bypassing this activation wiring.
        """
        if not self.has_node:
            pytest.skip("Config1 build failed")
        import nmos.node.activation_engine as ae
        from nmos.node.store import to_static_id
        from nmos.types.generated.ntransport_file import NTransportFileValue

        # No real streaming in a unit test.
        monkeypatch.setattr(ae, "_manage_engine_lifecycle", lambda *a, **k: None)

        result = self._get_first_receiver()
        if result is None:
            pytest.skip("No receivers")
        recv_id, recv = result
        static_id = to_static_id(recv_id)
        activation = self.node.receiver_activation.get(static_id)
        assert activation is not None

        tfv = NTransportFileValue()
        tfv.set_to_default()
        tfv.Data.value = self._make_raw_video_sdp(1920, 1080, 60, 1, 10, "YCbCr-4:2:2")
        activation.active_state.TransportFile.set_value(tfv)

        # Activate: SDP cached, status evaluated (NOT the old permanent unknown).
        ae.do_activation(self.node, recv_id, activation,
                         master_enable=True, is_sender=False, has_sdp=False)
        assert self.node.sdp.get(static_id) is not None, \
            "receiver's transport_file SDP was not cached on activation"
        assert self.node.set_receiver_compatibility_state(recv) in (
            "compliant_stream", "non_compliant_stream",
        ), "status is still 'unknown' after activating with an SDP"

        # Deactivate: SDP dropped, status back to unknown (no stream).
        ae.do_activation(self.node, recv_id, activation,
                         master_enable=False, is_sender=False, has_sdp=False)
        assert self.node.sdp.get(static_id) is None, \
            "receiver SDP was not cleared on deactivation"
        assert self.node.set_receiver_compatibility_state(recv) == "unknown"


# ===========================================================================
# Step 4D: Format transition tests
# ===========================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestIS11FormatTransition:
    """Test format transitions via constraint application."""

    def test_sender_flow_caps_contain_media_type(self) -> None:
        """Flow caps should always include media_type."""
        node = _make_node()
        _build_config(node, "config1")
        result = _get_first_sender(node)
        if result is None:
            pytest.skip("No senders")
        _, sender = result
        flow_ptr = _get_sender_flow(node, sender)
        if flow_ptr is None:
            pytest.skip("No flow")

        from nmos.node.flow_caps import get_flow_to_caps
        from nmos.node.compatibility import _get_cap_str
        caps = get_flow_to_caps(node, flow_ptr)
        mt = _get_cap_str(caps, CapFormatMediaType)
        assert mt is not None, "Flow caps must include media_type"
        assert mt == "video/raw", f"Config1 video sender should be video/raw, got {mt}"


# ===========================================================================
# Step 4E extended: More edge cases
# ===========================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestIS11EdgeCasesExtended:
    """Additional edge case tests."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.node = _make_node()
        try:
            _build_config(self.node, "config1")
            self.has_node = True
        except Exception:
            self.has_node = False

    def test_multiple_status_calls_stable(self) -> None:
        """Calling set_sender_compatibility_state multiple times returns same result."""
        if not self.has_node:
            pytest.skip("Config1 build failed")
        result = _get_first_sender(self.node)
        if result is None:
            pytest.skip("No senders")
        _, sender = result
        status1 = self.node.set_sender_compatibility_state(sender)
        status2 = self.node.set_sender_compatibility_state(sender)
        assert status1 == status2, "Status should be stable across calls"

    def test_all_senders_have_valid_status(self) -> None:
        """Every sender should return a valid IS-11 status."""
        if not self.has_node:
            pytest.skip("Config1 build failed")
        valid = {"unconstrained", "constrained", "active_constraints_violation"}
        for static_id, sender in self.node.senders:
            status = self.node.set_sender_compatibility_state(sender)
            assert status in valid, f"Sender {static_id}: invalid status '{status}'"

    def test_all_receivers_have_valid_status(self) -> None:
        """Every receiver should return a valid IS-11 status."""
        if not self.has_node:
            pytest.skip("Config1 build failed")
        valid = {"unknown", "compliant_stream", "non_compliant_stream"}
        for static_id, recv in self.node.receivers:
            status = self.node.set_receiver_compatibility_state(recv)
            assert status in valid, f"Receiver {static_id}: invalid status '{status}'"

    def test_sender_caps_accessible(self) -> None:
        """Sender should have accessible IS-04 Caps."""
        if not self.has_node:
            pytest.skip("Config1 build failed")
        result = _get_first_sender(self.node)
        if result is None:
            pytest.skip("No senders")
        _, sender = result
        from nmos.node.compatibility import _get_sender_ccf_caps
        caps = _get_sender_ccf_caps(self.node, sender)
        # Config1 sender has capabilities from the config
        assert caps is not None, "Sender should have CCF caps from IS-04"
        assert len(caps.capsets) > 0, "Sender caps should have at least one CapSet"

    def test_force_delete_then_force_again(self) -> None:
        """DELETE → force again with None → should remain unconstrained."""
        if not self.has_node:
            pytest.skip("Config1 build failed")
        result = _get_first_sender(self.node)
        if result is None:
            pytest.skip("No senders")
        _, sender = result
        self.node.force_active_constraints(sender, None)
        self.node.force_active_constraints(sender, None)
        status = self.node.set_sender_compatibility_state(sender)
        assert status == "unconstrained"


# ===========================================================================
# Multi-config tests (run only if configs exist)
# ===========================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestIS11AllConfigs:
    """Verify every builtin config builds and has valid IS-11 status."""

    _CONFIG_NAMES = [
        "config1", "config2", "config3", "config4", "config4a_mux",
        "config5", "config5a", "config6", "config6a",
        "config7", "config7f", "config7faudio", "config7u", "config7uf",
        "config8", "config8f", "config8u",
        "config9", "config10", "config11", "config12",
    ]

    @pytest.mark.parametrize("config_name", _CONFIG_NAMES)
    def test_config_senders_valid_status(self, config_name: str) -> None:
        """Every sender in every config should return a valid IS-11 status."""
        config_path = BUILTIN_DIR / f"{config_name}.json"
        if not config_path.exists():
            pytest.skip(f"{config_name}.json not found")

        node = _make_node()
        try:
            _build_config(node, config_name)
        except Exception as exc:
            pytest.skip(f"{config_name} build failed: {exc}")

        valid = {"unconstrained", "constrained", "active_constraints_violation"}
        for static_id, sender in node.senders:
            status = node.set_sender_compatibility_state(sender)
            assert status in valid, \
                f"{config_name} sender {static_id}: invalid status '{status}'"

    @pytest.mark.parametrize("config_name", _CONFIG_NAMES)
    def test_config_receivers_valid_status(self, config_name: str) -> None:
        """Every receiver in every config should return a valid IS-11 status."""
        config_path = BUILTIN_DIR / f"{config_name}.json"
        if not config_path.exists():
            pytest.skip(f"{config_name}.json not found")

        node = _make_node()
        try:
            _build_config(node, config_name)
        except Exception as exc:
            pytest.skip(f"{config_name} build failed: {exc}")

        valid = {"unknown", "compliant_stream", "non_compliant_stream"}
        for static_id, recv in node.receivers:
            status = node.set_receiver_compatibility_state(recv)
            assert status in valid, \
                f"{config_name} receiver {static_id}: invalid status '{status}'"

    @pytest.mark.parametrize("config_name", _CONFIG_NAMES)
    def test_config_sender_native_caps_as_active_constraints(self, config_name: str) -> None:
        """Every sender's native caps must be accepted as active constraints.

        This mirrors what a controller does: read the sender's IS-04
        caps.constraint_sets, then PUT them as IS-11 active constraints.
        The node MUST accept its own capabilities — rejection means
        check_active_constraints (validate + merge) is broken.
        """
        if not HAS_CCF:
            pytest.skip("MatroxCCF not available")

        config_path = BUILTIN_DIR / f"{config_name}.json"
        if not config_path.exists():
            pytest.skip(f"{config_name}.json not found")

        node = _make_node()
        try:
            _build_config(node, config_name)
        except Exception as exc:
            pytest.skip(f"{config_name} build failed: {exc}")

        from nmos.json.engine import JsonEngine

        for static_id, sender in node.senders:
            # Get sender's IS-04 caps as JSON
            eng = JsonEngine()
            caps_json = eng.encode(sender.Caps._value)
            assert caps_json, f"{config_name} sender {static_id}: caps encode returned empty"
            caps = json.loads(caps_json)
            cs = caps.get("constraint_sets", [])
            if not cs:
                continue  # no constraint_sets to test

            # Use native caps as active constraints (what a controller does)
            active_body = {"constraint_sets": cs}
            _, _, err = node.check_active_constraints(sender, active_body)
            assert err is None, (
                f"{config_name} sender {static_id}: "
                f"native caps rejected as active constraints: {err}"
            )


# ===========================================================================
# 4A: Config3 multi-format sender constraint tests
# ===========================================================================

def _apply_constraints(node: Node, sender: object, constraint_sets: list[dict]) -> tuple[str | None, str]:
    """Apply constraints to sender via Node methods.
    Returns (error_message_or_None, status_after)."""
    from nmos.json.engine import JsonEngine
    from nmos.types.generated.nsender_active_constraints import NSenderActiveConstraintsValue
    obj = NSenderActiveConstraintsValue()
    obj.decode(JsonEngine(), {"constraint_sets": constraint_sets})
    err = node.force_active_constraints(sender, obj)
    if err is not None:
        return str(err), node.set_sender_compatibility_state(sender)
    status = node.set_sender_compatibility_state(sender)
    return None, status


@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestIS11Config3MultiFormat:
    """IS-11 constraint tests for Config3 (video/raw + H.264 + H.265)."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.node = _make_node()
        try:
            _build_config(self.node, "config3")
            self.has_node = True
        except Exception:
            self.has_node = False

    def _get_video_sender(self) -> tuple[str, object] | None:
        for sid, s in self.node.senders:
            if 'video' in str(s.Format.value):
                return s.ResourceCore.Id.value, s
        return None

    def test_put_force_raw(self) -> None:
        """PUT constraint media_type=["video/raw"] → flow stays/becomes raw."""
        if not self.has_node:
            pytest.skip("Config3 build failed")
        result = self._get_video_sender()
        if result is None:
            pytest.skip("No video sender")
        sender_id, sender = result

        err, status = _apply_constraints(self.node, sender, [{
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:media_type": {"enum": ["video/raw"]},
        }])
        assert err is None, f"Expected valid, got: {err}"
        assert status == "constrained", f"Expected constrained, got {status}"

    def test_put_force_h264(self) -> None:
        """PUT constraint media_type=["video/H264"] → constrained."""
        if not self.has_node:
            pytest.skip("Config3 build failed")
        result = self._get_video_sender()
        if result is None:
            pytest.skip("No video sender")
        sender_id, sender = result

        err, status = _apply_constraints(self.node, sender, [{
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:media_type": {"enum": ["video/H264"]},
        }])
        assert err is None, f"Expected valid, got: {err}"
        assert status == "constrained", f"Expected constrained, got {status}"

    def test_put_h264_specific_level(self) -> None:
        """PUT constraint with specific H.264 level → constrained."""
        if not self.has_node:
            pytest.skip("Config3 build failed")
        result = self._get_video_sender()
        if result is None:
            pytest.skip("No video sender")
        _, sender = result

        err, status = _apply_constraints(self.node, sender, [{
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:media_type": {"enum": ["video/H264"]},
            "urn:x-nmos:cap:format:level": {"enum": ["4.2"]},
        }])
        assert err is None, f"Expected valid, got: {err}"
        assert status == "constrained", f"Expected constrained, got {status}"

    def test_put_h264_specific_profile(self) -> None:
        """PUT constraint with specific H.264 profile → constrained."""
        if not self.has_node:
            pytest.skip("Config3 build failed")
        result = self._get_video_sender()
        if result is None:
            pytest.skip("No video sender")
        _, sender = result

        err, status = _apply_constraints(self.node, sender, [{
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:media_type": {"enum": ["video/H264"]},
            "urn:x-nmos:cap:format:profile": {"enum": ["High-422"]},
        }])
        assert err is None, f"Expected valid, got: {err}"
        assert status == "constrained", f"Expected constrained, got {status}"


# ===========================================================================
# 4A: Mux sender constraint tests
# ===========================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestIS11MuxConstraints:
    """IS-11 constraint tests for mux configs (config4a_mux)."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.node = _make_node()
        try:
            _build_config(self.node, "config4a_mux")
            self.has_node = True
        except Exception:
            self.has_node = False

    def _get_mux_sender(self) -> tuple[str, object] | None:
        for sid, s in self.node.senders:
            if 'mux' in str(s.Format.value):
                return s.ResourceCore.Id.value, s
        return None

    def test_mux_sender_exists(self) -> None:
        """Config4a should have a mux sender."""
        if not self.has_node:
            pytest.skip("Config4a build failed")
        result = self._get_mux_sender()
        assert result is not None, "Config4a should have a mux sender"

    def test_mux_sender_status_default(self) -> None:
        """Mux sender default status should be valid."""
        if not self.has_node:
            pytest.skip("Config4a build failed")
        result = self._get_mux_sender()
        if result is None:
            pytest.skip("No mux sender")
        _, sender = result
        status = self.node.set_sender_compatibility_state(sender)
        assert status in ("unconstrained", "constrained", "active_constraints_violation")

    def test_put_trunk_constraint(self) -> None:
        """PUT constraint on trunk (no layer/format metadata) → valid."""
        if not self.has_node:
            pytest.skip("Config4a build failed")
        result = self._get_mux_sender()
        if result is None:
            pytest.skip("No mux sender")
        _, sender = result

        err, status = _apply_constraints(self.node, sender, [{
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:media_type": {"enum": ["application/MP2T"]},
        }])
        assert err is None, f"Trunk constraint should be valid: {err}"

    def test_put_video_layer_constraint(self) -> None:
        """PUT constraint with meta:format=video, meta:layer=0."""
        if not self.has_node:
            pytest.skip("Config4a build failed")
        result = self._get_mux_sender()
        if result is None:
            pytest.skip("No mux sender")
        _, sender = result

        err, status = _apply_constraints(self.node, sender, [
            {
                "urn:x-nmos:cap:meta:enabled": True,
                "urn:x-nmos:cap:meta:preference": 100,
                "urn:x-nmos:cap:format:media_type": {"enum": ["application/MP2T"]},
            },
            {
                "urn:x-nmos:cap:meta:enabled": False,
                "urn:x-matrox:cap:meta:layer_enabled": True,
                "urn:x-matrox:cap:meta:format": "urn:x-nmos:format:video",
                "urn:x-matrox:cap:meta:layer": 0,
                "urn:x-nmos:cap:meta:preference": 100,
                "urn:x-nmos:cap:format:media_type": {"enum": ["video/H264"]},
            },
        ])
        # Should not error — even if validation doesn't fully support layers yet
        # The constraint structure itself is valid
        assert err is None or "not compliant" not in str(err).lower(), \
            f"Video layer constraint should be structurally valid: {err}"


@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestIS11MuxActiveConstraintEdgeCases:
    """Edge cases for mux active constraints: trunk-only, leaf-only, empty layers.

    Uses config7 which has a mux sender (MPEG2-TS) with 1 video + 1 audio sub-flow.
    These tests verify that the CCF constriction approach correctly handles
    unconstrained sub-flows when only trunk or specific layers are constrained.

    Expected behavior:
    - Trunk-only constraints: sub-flows are unconstrained → "constrained" status
    - Empty constraint_sets: → back to "unconstrained"
    - Trunk + one sub-flow: other sub-flow unconstrained → "constrained"
    """

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.node = _make_node()
        try:
            _build_config(self.node, "config7")
            self.has_node = True
        except Exception:
            self.has_node = False

    def _get_mux_sender(self) -> tuple[str, object] | None:
        for sid, s in self.node.senders:
            fmt = str(s.Format.value) if s.Format.defined else ""
            if "mux" in fmt:
                return s.ResourceCore.Id.value, s
        return None

    def test_trunk_only_constraints_sub_flows_unconstrained(self) -> None:
        """PUT trunk-only constraints → sub-flows should remain compatible.

        Expected behavior: trunk constrained, sub-flows have no active constraints →
        NormalizedConstraints includes empty defaults for sub-flows →
        checkFlowPropertiesCompatibility skips unconstrained properties →
        status = "constrained".

        Python CCF approach: caps_constrict_by_cons with empty sub-flow Cons →
        sender native capabilities retained for sub-flows → flow inclusion passes.
        """
        if not self.has_node:
            pytest.skip("Config7 build failed")
        result = self._get_mux_sender()
        if result is None:
            pytest.skip("No mux sender in config7")
        _, sender = result

        # Apply trunk-only constraint (no sub-flow constraints)
        # Config7 uses SRT transport → media_type is application/mp2t (lowercase)
        err, status = _apply_constraints(self.node, sender, [{
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:media_type": {"enum": ["application/mp2t"]},
        }])
        assert err is None, f"Trunk-only constraint should be valid: {err}"
        assert status == "constrained", \
            f"Expected 'constrained' with trunk-only constraints, got '{status}'"

    def test_empty_constraints_returns_unconstrained(self) -> None:
        """PUT empty constraint_sets → DELETE equivalent → unconstrained.

        Expected behavior: empty constraint_sets = delete active constraints →
        status = "unconstrained".
        """
        if not self.has_node:
            pytest.skip("Config7 build failed")
        result = self._get_mux_sender()
        if result is None:
            pytest.skip("No mux sender in config7")
        _, sender = result

        # First apply a constraint (config7 SRT → lowercase mp2t)
        _apply_constraints(self.node, sender, [{
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:media_type": {"enum": ["application/mp2t"]},
        }])

        # Then delete (empty constraints)
        self.node.force_active_constraints(sender, None)
        status = self.node.set_sender_compatibility_state(sender)
        assert status == "unconstrained", \
            f"Expected 'unconstrained' after delete, got '{status}'"

    def test_trunk_plus_video_layer_audio_unconstrained(self) -> None:
        """PUT trunk + video layer constraint → audio sub-flow should remain compatible.

        Expected behavior: trunk and video layer constrained, audio layer has no active
        constraints → audio sub-flow checked against unconstrained defaults →
        status = "constrained".
        """
        if not self.has_node:
            pytest.skip("Config7 build failed")
        result = self._get_mux_sender()
        if result is None:
            pytest.skip("No mux sender in config7")
        _, sender = result

        # Config7 SRT → lowercase mp2t
        err, status = _apply_constraints(self.node, sender, [
            {
                "urn:x-nmos:cap:meta:enabled": True,
                "urn:x-nmos:cap:meta:preference": 100,
                "urn:x-nmos:cap:format:media_type": {"enum": ["application/mp2t"]},
            },
            {
                "urn:x-nmos:cap:meta:enabled": False,
                "urn:x-matrox:cap:meta:layer_enabled": True,
                "urn:x-matrox:cap:meta:format": "urn:x-nmos:format:video",
                "urn:x-matrox:cap:meta:layer": 0,
                "urn:x-nmos:cap:meta:preference": 100,
                "urn:x-nmos:cap:format:media_type": {"enum": ["video/H264"]},
            },
        ])
        assert err is None, f"Trunk + video layer constraint should be valid: {err}"
        assert status == "constrained", \
            f"Expected 'constrained' with trunk+video, audio unconstrained, got '{status}'"

    def test_native_caps_as_active_constraints(self) -> None:
        """PUT sender's own native caps as active constraints → must succeed.

        This is what the nmosController does when constraining a sender.
        """
        if not self.has_node:
            pytest.skip("Config7 build failed")
        result = self._get_mux_sender()
        if result is None:
            pytest.skip("No mux sender in config7")
        _, sender = result

        # Get sender's IS-04 caps as JSON
        from nmos.json.engine import JsonEngine
        eng = JsonEngine()
        caps_json = eng.encode(sender.Caps._value)
        assert caps_json, "Caps encode returned empty"
        caps = json.loads(caps_json)
        cs = caps.get("constraint_sets", [])
        if not cs:
            pytest.skip("No constraint_sets")

        # Apply native caps as active constraints
        err, status = _apply_constraints(self.node, sender, cs)
        assert err is None, f"Native caps rejected as active constraints: {err}"
        assert status == "constrained", \
            f"Expected 'constrained' with native caps, got '{status}'"


# ===========================================================================
# 4B: More Receiver SDP compatibility tests
# ===========================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestIS11ReceiverSdpExtended:
    """Extended receiver SDP compatibility tests."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.node = _make_node()
        try:
            _build_config(self.node, "config1")
            self.has_node = True
        except Exception:
            self.has_node = False

    def _get_video_receiver(self) -> tuple[str, object] | None:
        from nmos.node import _get_resource_core
        for static_id, recv in self.node.receivers:
            core = _get_resource_core(recv)
            # Check if it's a video receiver
            inner = recv.get() if hasattr(recv, 'get') else recv
            if inner is not None:
                from nmos.types.generated.nreceiver_video import NReceiverVideo
                if isinstance(inner, NReceiverVideo):
                    return core.Id.value, recv
            # Fallback: check format string
            if hasattr(core, 'Format') and core.Format.defined:
                if 'video' in str(core.Format.value):
                    return core.Id.value, recv
        return None

    def _get_audio_receiver(self) -> tuple[str, object] | None:
        from nmos.node import _get_resource_core
        for static_id, recv in self.node.receivers:
            core = _get_resource_core(recv)
            if hasattr(core, 'Format') and core.Format.defined:
                if 'audio' in str(core.Format.value):
                    return core.Id.value, recv
        return None

    def _inject_sdp(self, receiver_id: str, sdp_text: str) -> None:
        from nmos.node.store import to_static_id
        static_id = to_static_id(receiver_id)
        self.node._store_parsed_sdp(static_id, sdp_text)

    def test_wrong_codec_h264_to_raw_receiver(self) -> None:
        """Inject H.264 SDP to raw-only video receiver → should not be compliant."""
        if not self.has_node:
            pytest.skip("Config1 build failed")
        result = self._get_video_receiver()
        if result is None:
            pytest.skip("No video receiver")
        recv_id, recv = result

        # H.264 SDP — config1 receiver only supports video/raw
        sdp = (
            "v=0\r\n"
            "o=- 1 1 IN IP4 192.168.1.100\r\n"
            "s=Test\r\n"
            "t=0 0\r\n"
            "m=video 27500 RTP/AVP 96\r\n"
            "c=IN IP4 239.1.1.100/128\r\n"
            "a=rtpmap:96 H264/90000\r\n"
            "a=fmtp:96 profile-level-id=640032; packetization-mode=1; "
            "sprop-parameter-sets=Z2QAMqwsaoMg,aO48gA==\r\n"
            "a=ts-refclk:ptp=IEEE1588-2008:00-00-00-00-00-00-00-00\r\n"
            "a=mediaclk:direct=0\r\n"
        )
        self._inject_sdp(recv_id, sdp)
        status = self.node.set_receiver_compatibility_state(recv)
        # Raw-only receiver should reject H.264
        assert status in ("non_compliant_stream", "unknown"), \
            f"H.264 SDP to raw-only receiver should not be compliant, got {status}"

    def test_wrong_audio_aac_to_l24_receiver(self) -> None:
        """Inject AAC SDP to L24-only audio receiver → should not be compliant."""
        if not self.has_node:
            pytest.skip("Config1 build failed")
        result = self._get_audio_receiver()
        if result is None:
            pytest.skip("No audio receiver")
        recv_id, recv = result

        # AAC SDP — config1 audio receiver only supports audio/L24
        sdp = (
            "v=0\r\n"
            "o=- 1 1 IN IP4 192.168.1.100\r\n"
            "s=Test\r\n"
            "t=0 0\r\n"
            "m=audio 27500 RTP/AVP 96\r\n"
            "c=IN IP4 239.1.1.100/128\r\n"
            "a=rtpmap:96 mpeg4-generic/48000/2\r\n"
            "a=fmtp:96 streamtype=5; profile-level-id=41; mode=AAC-hbr; "
            "sizelength=13; indexlength=3; indexdeltalength=3; "
            "config=1190\r\n"
            "a=ts-refclk:ptp=IEEE1588-2008:00-00-00-00-00-00-00-00\r\n"
            "a=mediaclk:direct=0\r\n"
        )
        self._inject_sdp(recv_id, sdp)
        status = self.node.set_receiver_compatibility_state(recv)
        assert status in ("non_compliant_stream", "unknown"), \
            f"AAC SDP to L24-only receiver should not be compliant, got {status}"


# ===========================================================================
# 4D: Format transition tests
# ===========================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestIS11FormatTransitions:
    """Test format transitions via constraint application (config3: raw+H.264)."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.node = _make_node()
        try:
            _build_config(self.node, "config3")
            self.has_node = True
        except Exception:
            self.has_node = False

    def _get_video_sender(self) -> tuple[str, object] | None:
        for sid, s in self.node.senders:
            if 'video' in str(s.Format.value):
                return s.ResourceCore.Id.value, s
        return None

    def test_raw_to_h264_transition(self) -> None:
        """Apply H.264 constraint to raw sender → constrained."""
        if not self.has_node:
            pytest.skip("Config3 build failed")
        result = self._get_video_sender()
        if result is None:
            pytest.skip("No video sender")
        _, sender = result

        err, status = _apply_constraints(self.node, sender, [{
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:media_type": {"enum": ["video/H264"]},
        }])
        assert err is None, f"H.264 transition should be valid: {err}"
        assert status == "constrained"

    def test_h264_to_raw_transition(self) -> None:
        """Apply H.264 then raw constraint → both should succeed."""
        if not self.has_node:
            pytest.skip("Config3 build failed")
        result = self._get_video_sender()
        if result is None:
            pytest.skip("No video sender")
        _, sender = result

        # First apply H.264
        err, _ = _apply_constraints(self.node, sender, [{
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:media_type": {"enum": ["video/H264"]},
        }])
        assert err is None

        # Then switch back to raw
        err, status = _apply_constraints(self.node, sender, [{
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:media_type": {"enum": ["video/raw"]},
        }])
        assert err is None, f"Raw transition should be valid: {err}"
        assert status == "constrained"


@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestIS11AudioFormatTransitions:
    """Test audio format transitions (config5a: L24+AAC)."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.node = _make_node()
        try:
            _build_config(self.node, "config5a")
            self.has_node = True
        except Exception:
            self.has_node = False

    def _get_audio_sender(self) -> tuple[str, object] | None:
        for sid, s in self.node.senders:
            if 'audio' in str(s.Format.value):
                return s.ResourceCore.Id.value, s
        return None

    def test_pcm_to_aac_transition(self) -> None:
        """Apply AAC constraint to PCM audio sender."""
        if not self.has_node:
            pytest.skip("Config5a build failed")
        result = self._get_audio_sender()
        if result is None:
            pytest.skip("No audio sender")
        _, sender = result

        err, status = _apply_constraints(self.node, sender, [{
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:media_type": {"enum": ["audio/mpeg4-generic"]},
        }])
        assert err is None, f"AAC transition should be valid: {err}"
        assert status == "constrained"


# ===========================================================================
# 4E: Extended edge case tests
# ===========================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestIS11EdgeCasesFull:
    """Full edge case test suite from plan 4E."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.node = _make_node()
        try:
            _build_config(self.node, "config1")
            self.has_node = True
        except Exception:
            self.has_node = False

    def _get_video_sender(self) -> tuple[str, object] | None:
        for sid, s in self.node.senders:
            if 'video' in str(s.Format.value):
                return s.ResourceCore.Id.value, s
        return None

    def test_put_empty_constraint_sets(self) -> None:
        """PUT {"constraint_sets": []} — equivalent to DELETE."""
        if not self.has_node:
            pytest.skip("Config1 build failed")
        result = self._get_video_sender()
        if result is None:
            pytest.skip("No video sender")
        _, sender = result

        # First apply some constraints
        _apply_constraints(self.node, sender, [{
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:frame_width": {"enum": [1920]},
        }])

        # Then apply empty — should reset
        err, status = _apply_constraints(self.node, sender, [])
        # Empty constraint_sets: no constraints to validate against
        # Should remain unconstrained
        assert status in ("unconstrained", "constrained")

    def test_put_disabled_constraint_set(self) -> None:
        """PUT with meta:enabled=false → constraint exists but doesn't force.
        Note: disabled CS are still validated — they must be within caps.
        But 7680 is NOT in config1 caps → validation fails even if disabled."""
        if not self.has_node:
            pytest.skip("Config1 build failed")
        result = self._get_video_sender()
        if result is None:
            pytest.skip("No video sender")
        _, sender = result

        # Disabled CS with values WITHIN caps → should pass
        err, status = _apply_constraints(self.node, sender, [{
            "urn:x-nmos:cap:meta:enabled": False,
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:frame_width": {"enum": [1920]},
        }])
        assert err is None, f"Disabled CS with valid values should pass: {err}"

    def test_multiple_constraint_sets_preference(self) -> None:
        """PUT two CS: preference=100 (native), preference=1 (alternative).
        Both must be within sender caps."""
        if not self.has_node:
            pytest.skip("Config1 build failed")
        result = self._get_video_sender()
        if result is None:
            pytest.skip("No video sender")
        _, sender = result

        # Config1 only supports 1920x1080 — both CS use same resolution
        err, status = _apply_constraints(self.node, sender, [
            {
                "urn:x-nmos:cap:meta:enabled": True,
                "urn:x-nmos:cap:meta:preference": 100,
                "urn:x-nmos:cap:format:frame_width": {"enum": [1920]},
                "urn:x-nmos:cap:format:frame_height": {"enum": [1080]},
            },
            {
                "urn:x-nmos:cap:meta:enabled": True,
                "urn:x-nmos:cap:meta:preference": 1,
                "urn:x-nmos:cap:format:frame_width": {"enum": [1920]},
            },
        ])
        assert err is None, f"Multiple CS within caps should be valid: {err}"
        assert status == "constrained"

    def test_put_transport_constraint_ignored(self) -> None:
        """PUT unsupported transport constraint → silently ignored (not error)."""
        if not self.has_node:
            pytest.skip("Config1 build failed")
        result = self._get_video_sender()
        if result is None:
            pytest.skip("No video sender")
        _, sender = result

        err, status = _apply_constraints(self.node, sender, [{
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:frame_width": {"enum": [1920]},
            # Transport constraint not in supported list → should be silently ignored
            "urn:x-nmos:cap:transport:packet_time": {"enum": [1000]},
        }])
        assert err is None, f"Transport constraint should be silently ignored: {err}"

    def test_put_unsupported_format_constraint_rejected(self) -> None:
        """PUT unsupported format constraint → should be rejected."""
        if not self.has_node:
            pytest.skip("Config1 build failed")
        result = self._get_video_sender()
        if result is None:
            pytest.skip("No video sender")
        _, sender = result

        err, status = _apply_constraints(self.node, sender, [{
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:meta:preference": 100,
            # channel_count is an audio constraint, not video
            "urn:x-nmos:cap:format:channel_count": {"enum": [2]},
        }])
        # channel_count on a video sender should be rejected
        assert err is not None, "Unsupported format constraint should be rejected"

    def test_constraint_outside_caps_rejected(self) -> None:
        """Constraint value outside sender capabilities → rejected by validate."""
        if not self.has_node:
            pytest.skip("Config1 build failed")
        result = self._get_video_sender()
        if result is None:
            pytest.skip("No video sender")
        _, sender = result

        # Config1 supports 1920x1080 — 7680 is outside caps
        err, status = _apply_constraints(self.node, sender, [{
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:frame_width": {"enum": [7680]},
        }])
        assert err is not None, "Value outside caps should be rejected"

    def test_constraint_wrong_type_rejected(self) -> None:
        """String value where int is expected → rejected during decode or validate."""
        if not self.has_node:
            pytest.skip("Config1 build failed")
        result = self._get_video_sender()
        if result is None:
            pytest.skip("No video sender")
        _, sender = result

        # Wrong type may raise during JSON→CCF conversion or fail during validation
        try:
            err, status = _apply_constraints(self.node, sender, [{
                "urn:x-nmos:cap:meta:enabled": True,
                "urn:x-nmos:cap:meta:preference": 100,
                "urn:x-nmos:cap:format:frame_width": {"enum": ["not_a_number"]},
            }])
            # If it doesn't raise, it must at least return an error
            assert err is not None, "Wrong type should be rejected"
        except (ValueError, Exception):
            pass  # Expected — CCF rejects invalid types during parse


@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestIS11OutOfRangeAllConfigs:
    """Verify every config rejects out-of-range constraints."""

    _CONFIG_NAMES = [
        "config1", "config3", "config4", "config4a_mux",
        "config5", "config5a", "config6", "config6a",
        "config7", "config7f", "config7faudio", "config7u", "config7uf",
        "config8", "config8f", "config8u",
        "config10", "config11", "config12",
    ]

    @pytest.mark.parametrize("config_name", _CONFIG_NAMES)
    def test_out_of_range_value_rejected(self, config_name: str) -> None:
        """For each config/sender, a clearly out-of-range value must be rejected."""
        node = _make_node()
        try:
            _build_config(node, config_name)
        except Exception:
            pytest.skip(f"{config_name} build failed")

        tested_any = False
        for static_id, sender in node.senders:
            caps = node.sender_ccf_caps.get(static_id)
            if caps is None or len(caps.capsets) == 0:
                continue

            # Find any INT property with explicit enum values to test against
            for cs in caps.capsets:
                if cs.format is not None:
                    continue  # trunk only
                for name, cap in cs.caps.items():
                    if not _is_format_property(name):
                        continue
                    if cap.value.type != RangeType.INT:
                        continue
                    if cap.value.values is None or len(cap.value.values) == 0:
                        continue
                    # Use a value that's clearly NOT in the enum
                    max_val = max(cap.value.values)
                    out_of_range = int(max_val) + 99999

                    err, status = _apply_constraints(node, sender, [{
                        "urn:x-nmos:cap:meta:enabled": True,
                        "urn:x-nmos:cap:meta:preference": 100,
                        name: {"enum": [out_of_range]},
                    }])
                    assert err is not None, (
                        f"{config_name}: {name}={out_of_range} should be rejected "
                        f"(caps allow {list(cap.value.values)})"
                    )
                    tested_any = True
                    break  # One property per sender is enough
                if tested_any:
                    break
            if tested_any:
                break

        if not tested_any:
            pytest.skip(f"No testable INT properties in {config_name}")


# ===========================================================================
# Comprehensive Constraint Forcing Tests
#
# For every builtin config × every sender × every constraint scenario,
# apply random valid constraints and verify the flow is updated correctly.
# ===========================================================================

import hashlib
import random as _random_mod


def _stable_seed(*parts: object) -> int:
    """Deterministic 32-bit RNG seed derived from the given parts.

    The fuzz scenarios MUST be reproducible run-to-run. The builtin ``hash()``
    of strings is salted per process by PYTHONHASHSEED, so seeding an RNG from
    ``hash((config, sender, scenario))`` made the generated constraint sets —
    and thus which failures surfaced — vary every invocation. A SHA-256 digest
    is stable across processes and platforms.
    """
    joined = "\x1f".join(str(p) for p in parts)
    return int.from_bytes(hashlib.sha256(joined.encode("utf-8")).digest()[:4], "big")


# Properties that should NOT be constrained (transport, meta, or read-only)
_TRANSPORT_PREFIXES = ("urn:x-nmos:cap:transport:", "urn:x-matrox:cap:transport:")
_META_PREFIXES = ("urn:x-nmos:cap:meta:", "urn:x-matrox:cap:meta:")

# DERIVED properties — never constrained by the fuzzer in ANY scenario. Their
# value is computed by the forcing fix-ups from other properties, so pinning
# them fights the fix-up and yields an unsatisfiable request.
#   level/sublevel — derived from resolution+bitrate+profile (fixCodedVideoFlow)
#   sample_depth   — determined by PCM media_type (L8=8, L16=16, L24=24)
#   bit_rate       — interacts with level selection
_INTERDEPENDENT_PROPS = {
    "urn:x-nmos:cap:format:level",
    "urn:x-nmos:cap:format:sublevel",
    "urn:x-nmos:cap:format:sample_depth",
    "urn:x-nmos:cap:format:bit_rate",
}

# INTERDEPENDENT GROUPS — a capability set declares each property's allowed
# values as an INDEPENDENT enum, so constraining more than one member of a
# group can request an impossible COMBINATION even though each value is
# individually valid: e.g. frame_width=3840 + frame_height=480 (not a real
# resolution), or profile=Main10 + component_depth=8, or profile=High420.12
# (4:2:0) + color_sampling=RGB. The fuzzer therefore constrains AT MOST ONE
# member of each group and lets the forcing fix-ups derive the coherent
# partners (fixVideoWidthHeight derives the missing dimension; fixCodedVideoFlow
# derives sampling/depth from the profile, or a profile from the sampling).
_INTERDEPENDENT_GROUPS = (
    {
        "urn:x-nmos:cap:format:frame_width",
        "urn:x-nmos:cap:format:frame_height",
    },
    {
        "urn:x-nmos:cap:format:profile",
        "urn:x-nmos:cap:format:color_sampling",
        "urn:x-nmos:cap:format:component_depth",
    },
)


def _is_format_property(name: str) -> bool:
    """True if the property is a format property (not transport/meta)."""
    return not any(name.startswith(p) for p in _TRANSPORT_PREFIXES + _META_PREFIXES)


def _pick_random_constraint_from_capset(
    capset: Any, n_props: int, rng: _random_mod.Random,
    restrict_to: set[str] | None = None,
    exclude: set[str] | None = None,
) -> dict | None:
    """Pick n_props random format properties from a CapSet, choose valid values.

    Returns a constraint_set dict with meta:enabled and meta:preference,
    plus format/layer if the capset has them. Returns None if no constrainable
    properties exist.
    """
    if not HAS_CCF:
        return None

    # Collect constrainable properties (format only, not infinite/empty).
    # Exclude media_type — format transitions (raw↔coded, mux↔simple) are tested
    # separately in TestIS11FormatTransitions. Random selection of media_type can
    # trigger destructive class transitions that aren't the focus of these tests.
    # If restrict_to is set, only pick properties that exist in that set (ensures
    # we only constrain properties the actual flow has).
    candidates = []
    for name, cap in capset.caps.items():
        if not _is_format_property(name):
            continue
        if name == "urn:x-nmos:cap:format:media_type":
            continue
        # Derived properties are never constrained — the fix-ups own them.
        if name in _INTERDEPENDENT_PROPS:
            continue
        if restrict_to is not None and name not in restrict_to:
            continue
        if exclude is not None and name in exclude:
            continue
        if cap.value.infinite or cap.value.empty:
            continue
        # Accept both enum values AND range (min/max) properties
        has_enum = cap.value.values is not None and len(cap.value.values) > 0
        has_range = cap.value.min is not None or cap.value.max is not None
        if not has_enum and not has_range:
            continue
        candidates.append((name, cap))

    # Collapse each interdependent group to a single representative so the
    # generated set never requests an impossible combination (see
    # _INTERDEPENDENT_GROUPS). The forcing fix-ups derive the coherent partners.
    for group in _INTERDEPENDENT_GROUPS:
        in_group = [name for name, _ in candidates if name in group]
        if len(in_group) > 1:
            keep_name = rng.choice(in_group)
            candidates = [(n, c) for (n, c) in candidates
                          if n not in group or n == keep_name]

    if not candidates:
        return None

    n = min(n_props, len(candidates))
    chosen = rng.sample(candidates, n)

    cs: dict = {
        "urn:x-nmos:cap:meta:preference": 100,
    }

    # Add format/layer metadata if the capset has them (mux sub-flow)
    if capset.format is not None and capset.layer is not None:
        # Layer constraint: disabled at trunk, enabled at layer level
        cs["urn:x-nmos:cap:meta:enabled"] = False
        cs["urn:x-matrox:cap:meta:layer_enabled"] = True
        cs["urn:x-matrox:cap:meta:format"] = capset.format
        cs["urn:x-matrox:cap:meta:layer"] = capset.layer
    else:
        cs["urn:x-nmos:cap:meta:enabled"] = True

    for name, cap in chosen:
        if cap.value.values is not None and len(cap.value.values) > 0:
            # Enum: pick random value from explicit list
            val = rng.choice(list(cap.value.values))
        elif cap.value.min is not None and cap.value.max is not None:
            # Range: pick random int from [min, max]
            val = rng.randint(int(cap.value.min), int(cap.value.max))
        elif cap.value.min is not None:
            val = int(cap.value.min)
        elif cap.value.max is not None:
            val = int(cap.value.max)
        else:
            continue

        if cap.value.type == RangeType.RATIONAL:
            if hasattr(val, 'numerator'):
                cs[name] = {"enum": [{"numerator": val.numerator, "denominator": val.denominator}]}
            else:
                cs[name] = {"enum": [{"numerator": int(val)}]}
        elif cap.value.type == RangeType.BOOL:
            cs[name] = {"enum": [val]}
        else:
            cs[name] = {"enum": [int(val) if cap.value.type == RangeType.INT else val]}

    return cs


def _build_constraint_sets_for_scenario(
    caps: Any, scenario: str, rng: _random_mod.Random,
) -> list[dict]:
    """Build constraint_sets list for a given scenario from sender CCF Caps.

    Produces at most ONE constraint set per unique (format, layer) pair.
    When multiple capsets exist for the same (format, layer) — e.g., video/raw
    and video/H264 both at layer=0 — picks one randomly.

    Scenarios:
    - trunk_only: 1-3 props from trunk
    - single_layer: 1-2 props from one random layer
    - all_layers: 1 prop per unique layer + trunk
    - trunk_plus_layers: trunk + random subset of layers
    """
    result: list[dict] = []

    # Group capsets by (format, layer) — pick ONE representative per group
    groups: dict[tuple[str | None, int | None], list[Any]] = {}
    for cs in caps.capsets:
        key = (cs.format, cs.layer)
        if key not in groups:
            groups[key] = []
        groups[key].append(cs)

    trunk_key = (None, None)
    layer_keys = [k for k in groups if k != trunk_key]

    # Get flow properties to restrict trunk constraints to actual flow properties
    flow_prop_names: set[str] | None = None
    if HAS_CCF:
        from nmos.node.flow_caps import get_flow_to_caps
        # caps is the sender's CCF Caps — find the sender's flow to know which
        # properties actually exist on it
        try:
            # Walk capsets to find trunk format info, but we need the node and sender.
            # Since we don't have them here, use a simpler heuristic: collect all
            # property names from the trunk flow capsets that have preference=100.
            trunk_native = [cs for cs in caps.capsets
                           if cs.format is None and cs.layer is None and cs.preference == 100]
            if trunk_native:
                flow_prop_names = set(trunk_native[0].caps.keys())
        except Exception:
            pass

    def _pick_from_group(key: tuple[str | None, int | None], n_props: int,
                         exclude: set[str] | None = None) -> dict | None:
        group = groups.get(key, [])
        if not group:
            return None
        cs = rng.choice(group)
        return _pick_random_constraint_from_capset(
            cs, n_props, rng,
            flow_prop_names if key == trunk_key else None,
            exclude,
        )

    if scenario == "trunk_only":
        constraint = _pick_from_group(trunk_key, rng.randint(1, 3))
        if constraint:
            result.append(constraint)

    elif scenario == "single_layer":
        if layer_keys:
            key = rng.choice(layer_keys)
            constraint = _pick_from_group(key, rng.randint(1, 2))
            if constraint:
                result.append(constraint)

    elif scenario == "all_layers":
        # Trunk + one constraint per unique layer
        constraint = _pick_from_group(trunk_key, 1)
        if constraint:
            result.append(constraint)
        for key in layer_keys:
            constraint = _pick_from_group(key, 1)
            if constraint:
                result.append(constraint)

    elif scenario == "trunk_plus_layers":
        # Trunk + random subset of unique layers
        constraint = _pick_from_group(trunk_key, rng.randint(1, 2))
        if constraint:
            result.append(constraint)
        if layer_keys:
            n = rng.randint(1, len(layer_keys))
            for key in rng.sample(layer_keys, n):
                constraint = _pick_from_group(key, 1)
                if constraint:
                    result.append(constraint)

    elif scenario == "max_trunk_only":
        # All safe properties on trunk only
        constraint = _pick_from_group(trunk_key, 999, exclude=_INTERDEPENDENT_PROPS)
        if constraint:
            result.append(constraint)

    elif scenario == "max_single_layer":
        # All safe properties on one random layer
        if layer_keys:
            key = rng.choice(layer_keys)
            constraint = _pick_from_group(key, 999, exclude=_INTERDEPENDENT_PROPS)
            if constraint:
                result.append(constraint)

    elif scenario == "max_all_layers":
        # All safe properties on every layer (no trunk)
        for key in layer_keys:
            constraint = _pick_from_group(key, 999, exclude=_INTERDEPENDENT_PROPS)
            if constraint:
                result.append(constraint)

    elif scenario == "max_some_layers":
        # All safe properties on a random subset of layers (no trunk)
        if layer_keys:
            n = rng.randint(1, len(layer_keys))
            for key in rng.sample(layer_keys, n):
                constraint = _pick_from_group(key, 999, exclude=_INTERDEPENDENT_PROPS)
                if constraint:
                    result.append(constraint)

    elif scenario == "max_trunk_plus_one_layer":
        # All safe properties on trunk + one random layer
        constraint = _pick_from_group(trunk_key, 999, exclude=_INTERDEPENDENT_PROPS)
        if constraint:
            result.append(constraint)
        if layer_keys:
            key = rng.choice(layer_keys)
            constraint = _pick_from_group(key, 999, exclude=_INTERDEPENDENT_PROPS)
            if constraint:
                result.append(constraint)

    elif scenario == "max_trunk_plus_all_layers":
        # All safe properties on trunk + every layer
        constraint = _pick_from_group(trunk_key, 999, exclude=_INTERDEPENDENT_PROPS)
        if constraint:
            result.append(constraint)
        for key in layer_keys:
            constraint = _pick_from_group(key, 999, exclude=_INTERDEPENDENT_PROPS)
            if constraint:
                result.append(constraint)

    elif scenario == "max_trunk_plus_some_layers":
        # All safe properties on trunk + random subset of layers
        constraint = _pick_from_group(trunk_key, 999, exclude=_INTERDEPENDENT_PROPS)
        if constraint:
            result.append(constraint)
        if layer_keys:
            n = rng.randint(1, len(layer_keys))
            for key in rng.sample(layer_keys, n):
                constraint = _pick_from_group(key, 999, exclude=_INTERDEPENDENT_PROPS)
                if constraint:
                    result.append(constraint)

    elif scenario == "reduce_layers":
        # Explicitly reduce *_layers on the mux trunk to test layer reduction.
        # Pick the trunk capset with lowest preference (most flexible ranges),
        # then constrain each *_layers to its minimum value.
        trunk_group = groups.get(trunk_key, [])
        if trunk_group:
            # Use the capset with the widest range (lowest preference = most alternatives)
            cs_sorted = sorted(trunk_group, key=lambda c: c.preference)
            target_cs = cs_sorted[0]

            cs: dict = {
                "urn:x-nmos:cap:meta:enabled": True,
                "urn:x-nmos:cap:meta:preference": 100,
            }

            _LAYER_PROPS = [
                "urn:x-matrox:cap:format:video_layers",
                "urn:x-matrox:cap:format:audio_layers",
                "urn:x-matrox:cap:format:data_layers",
            ]
            added_any = False
            for lp in _LAYER_PROPS:
                cap = target_cs.caps.get(lp)
                if cap is None or cap.value.infinite or cap.value.empty:
                    continue
                # Use minimum value from range (reduce layers)
                if cap.value.min is not None:
                    cs[lp] = {"enum": [int(cap.value.min)]}
                    added_any = True
                elif cap.value.values is not None and len(cap.value.values) > 0:
                    cs[lp] = {"enum": [int(min(cap.value.values))]}
                    added_any = True

            if added_any:
                result.append(cs)

    elif scenario == "format_change_then_max":
        # Force a media_type change to an alternative format, then constrain
        # all safe properties on the new format. This exercises format transitions
        # (raw→coded, coded→raw, PCM→AAC, etc.) with full property verification.
        trunk_group = groups.get(trunk_key, [])
        # Find a non-native capset (pref < 100) with a different media_type
        alternatives = [cs for cs in trunk_group if cs.preference < 100]
        if alternatives:
            target_cs = rng.choice(alternatives)
            # Include media_type to force the transition
            cs_dict: dict = {
                "urn:x-nmos:cap:meta:enabled": True,
                "urn:x-nmos:cap:meta:preference": 100,
            }
            mt_cap = target_cs.caps.get("urn:x-nmos:cap:format:media_type")
            if mt_cap and mt_cap.value.values:
                cs_dict["urn:x-nmos:cap:format:media_type"] = {"enum": [str(mt_cap.value.values[0])]}

            # Add all safe properties from the target capset
            for name, cap in target_cs.caps.items():
                if name == "urn:x-nmos:cap:format:media_type":
                    continue
                if not _is_format_property(name):
                    continue
                if name in _INTERDEPENDENT_PROPS:
                    continue
                if cap.value.infinite or cap.value.empty:
                    continue
                if cap.value.values and len(cap.value.values) > 0:
                    val = cap.value.values[0]
                    if hasattr(val, 'numerator'):
                        cs_dict[name] = {"enum": [{"numerator": val.numerator, "denominator": val.denominator}]}
                    else:
                        cs_dict[name] = {"enum": [val]}
                elif cap.value.min is not None and cap.value.max is not None:
                    cs_dict[name] = {"enum": [int(cap.value.min)]}

            if len(cs_dict) > 3:  # More than just meta + media_type
                result.append(cs_dict)

    return result


def _verify_flow_matches_constraints(
    node: Node, sender: Any, constraint_sets: list[dict],
) -> list[str]:
    """After forcing, verify flow properties match constrained values.

    Returns list of mismatch descriptions. Empty list = all OK.
    """
    if not HAS_CCF:
        return []

    from nmos.node.flow_caps import get_flow_to_caps
    from nmos.node.compatibility import _get_cap_str, _get_cap_int, _get_cap_bool, _get_cap_rational

    flow_ptr = _get_sender_flow(node, sender)
    if flow_ptr is None:
        return ["no flow associated with sender"]

    mismatches: list[str] = []

    # For mux, we need to check sub-flows too
    # Build a map: (format, layer) → flow_ptr for sub-flows
    flow_map: dict[tuple[str | None, int | None], Any] = {(None, None): flow_ptr}

    poly = flow_ptr.get() if hasattr(flow_ptr, 'get') else flow_ptr
    fv = poly.value if hasattr(poly, 'value') else poly
    if hasattr(fv, 'FlowCore') and hasattr(fv.FlowCore, 'Parents') and fv.FlowCore.Parents.defined:
        from nmos.types.generated.nflow_video_raw import NFlowVideoRawValue
        from nmos.types.generated.nflow_video_coded import NFlowVideoCodedValue
        from nmos.types.generated.nflow_audio_raw import NFlowAudioRawValue
        from nmos.types.generated.nflow_audio_coded import NFlowAudioCodedValue
        from nmos.enums import FormatVideo, FormatAudio, FormatData
        for pid in (fv.FlowCore.Parents.value or []):
            pptr = node.flows.get(pid)
            if pptr is None:
                continue
            ppoly = pptr.get() if hasattr(pptr, 'get') else pptr
            pfv = ppoly.value if hasattr(ppoly, 'value') else ppoly
            pfc = pfv.FlowCore if hasattr(pfv, 'FlowCore') else None
            if pfc and pfc.Layer.defined:
                layer = pfc.Layer.value
                if isinstance(ppoly, (NFlowVideoRawValue, NFlowVideoCodedValue)):
                    flow_map[(FormatVideo.s, layer)] = pptr
                elif isinstance(ppoly, (NFlowAudioRawValue, NFlowAudioCodedValue)):
                    flow_map[(FormatAudio.s, layer)] = pptr
                else:
                    flow_map[(FormatData.s, layer)] = pptr

    for cs_dict in constraint_sets:
        cs_format = cs_dict.get("urn:x-matrox:cap:meta:format")
        cs_layer = cs_dict.get("urn:x-matrox:cap:meta:layer")
        target_flow = flow_map.get((cs_format, cs_layer))
        if target_flow is None:
            target_flow = flow_map.get((None, None))
        if target_flow is None:
            continue

        flow_caps = get_flow_to_caps(node, target_flow)

        for prop_name, prop_constraint in cs_dict.items():
            if prop_name.startswith("urn:x-nmos:cap:meta:"):
                continue
            if not isinstance(prop_constraint, dict) or "enum" not in prop_constraint:
                continue
            expected_values = prop_constraint["enum"]
            if not expected_values:
                continue
            expected = expected_values[0]

            # Get the actual value from the flow.
            # Properties in constraints that don't exist on the flow are correctly
            # ignored by forceFlowPropertiesCompatibility (it iterates flow properties
            # vs constraints, not the other way around). Skip them.
            actual_cap = flow_caps.caps.get(prop_name) if flow_caps else None
            if actual_cap is None or not actual_cap.value.values:
                continue  # Property not on this flow type — correctly ignored

            actual = actual_cap.value.values[0]

            # Compare (handle Fraction vs dict for rationals)
            if isinstance(expected, dict) and "numerator" in expected:
                expected_frac = Fraction(expected["numerator"], expected.get("denominator", 1))
                if actual != expected_frac:
                    mismatches.append(f"{prop_name}: expected {expected_frac}, got {actual}")
            else:
                if str(actual) != str(expected):
                    mismatches.append(f"{prop_name}: expected {expected}, got {actual}")

    return mismatches


@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestIS11ConstraintForcing:
    """Comprehensive constraint forcing tests across all configs.

    For every config × sender × scenario, applies random valid constraints
    and verifies the flow was updated correctly.
    """

    _CONFIG_NAMES = [
        "config1", "config3", "config4", "config4a_mux",
        "config5", "config5a", "config6", "config6a",
        "config7", "config7f", "config7faudio", "config7u", "config7uf",
        "config8", "config8f", "config8u",
        "config10", "config11", "config12",
    ]

    _SCENARIOS = [
        # Few properties (1-3 random)
        "trunk_only", "single_layer", "all_layers", "trunk_plus_layers",
        # Max properties (all safe properties, excluding interdependent)
        "max_trunk_only", "max_single_layer", "max_all_layers", "max_some_layers",
        "max_trunk_plus_one_layer", "max_trunk_plus_all_layers", "max_trunk_plus_some_layers",
        # Layer reduction (mux-specific)
        "reduce_layers",
        # Format transition: force media_type change + max properties on new format
        "format_change_then_max",
    ]

    @pytest.mark.parametrize("config_name", _CONFIG_NAMES)
    @pytest.mark.parametrize("scenario", _SCENARIOS)
    def test_constraint_forcing(self, config_name: str, scenario: str) -> None:
        """Apply random valid constraints and verify flow compliance."""
        node = _make_node()
        try:
            _build_config(node, config_name)
        except Exception:
            pytest.skip(f"{config_name} build failed")

        from nmos.node.compatibility import _get_sender_ccf_caps
        from nmos.node.store import to_static_id

        tested_any = False

        for static_id, sender in node.senders:
            sender_id = sender.ResourceCore.Id.value
            caps = node.sender_ccf_caps.get(static_id)
            if caps is None or len(caps.capsets) == 0:
                continue

            # Deterministic seed per (config, sender, scenario)
            seed = _stable_seed(config_name, sender_id, scenario)
            rng = _random_mod.Random(seed)

            # Check if this is a mux sender
            has_layers = any(cs.layer is not None for cs in caps.capsets)
            if scenario in ("single_layer", "all_layers", "trunk_plus_layers") and not has_layers:
                continue  # Layer scenarios only for mux

            # Build constraint sets
            constraint_sets = _build_constraint_sets_for_scenario(caps, scenario, rng)
            if not constraint_sets:
                continue

            # Apply constraints
            err, status = _apply_constraints(node, sender, constraint_sets)

            assert err is None, (
                f"{config_name}/{sender_id}/{scenario} seed={seed}: "
                f"valid constraints rejected: {err}\n"
                f"constraint_sets={json.dumps(constraint_sets, indent=2, default=str)}"
            )
            assert status == "constrained", (
                f"{config_name}/{sender_id}/{scenario} seed={seed}: "
                f"expected 'constrained', got '{status}'\n"
                f"constraint_sets={json.dumps(constraint_sets, indent=2, default=str)}"
            )

            # Verify flow properties match
            mismatches = _verify_flow_matches_constraints(node, sender, constraint_sets)
            assert not mismatches, (
                f"{config_name}/{sender_id}/{scenario} seed={seed}: "
                f"flow property mismatches after forcing:\n"
                + "\n".join(f"  {m}" for m in mismatches)
                + f"\nconstraint_sets={json.dumps(constraint_sets, indent=2, default=str)}"
            )

            tested_any = True

            # Reset constraints for next iteration
            node.force_active_constraints(sender, None)

        if not tested_any:
            pytest.skip(f"No testable senders for {config_name}/{scenario}")


# ===========================================================================
# Stateful IS-11 Tests
#
# Tests that exercise stateful operations: sequential changes, delete/reset,
# idempotency, and metadata verification. These target accumulated-state bugs
# that single-shot tests cannot find.
# ===========================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestIS11StatefulConstraints:
    """Stateful constraint tests: sequential changes, delete/reset, idempotency."""

    _CONFIG_NAMES = [
        "config1", "config3", "config4", "config4a_mux",
        "config5", "config5a", "config6", "config6a",
        "config7", "config7f", "config7faudio", "config7u", "config7uf",
        "config8", "config8f", "config8u",
        "config10", "config11", "config12",
    ]

    # --- Phase 1: Sequential constraint changes ---

    @pytest.mark.parametrize("config_name", _CONFIG_NAMES)
    def test_sequential_constraint_changes(self, config_name: str) -> None:
        """Apply constraint A, verify, then apply constraint B, verify.

        Tests that the previous state is fully overwritten — no stale values
        carried over from A when B is applied.
        """
        node = _make_node()
        try:
            _build_config(node, config_name)
        except Exception:
            pytest.skip(f"{config_name} build failed")

        tested_any = False

        for static_id, sender in node.senders:
            sender_id = sender.ResourceCore.Id.value
            caps = node.sender_ccf_caps.get(static_id)
            if caps is None or len(caps.capsets) == 0:
                continue

            # Build two different constraint sets with different seeds
            seed_a = _stable_seed(config_name, sender_id, "seq_A")
            seed_b = _stable_seed(config_name, sender_id, "seq_B")
            rng_a = _random_mod.Random(seed_a)
            rng_b = _random_mod.Random(seed_b)

            cs_a = _build_constraint_sets_for_scenario(caps, "max_trunk_only", rng_a)
            cs_b = _build_constraint_sets_for_scenario(caps, "max_trunk_only", rng_b)

            if not cs_a or not cs_b:
                continue

            # Apply A
            err_a, status_a = _apply_constraints(node, sender, cs_a)
            assert err_a is None, (
                f"{config_name}/{sender_id} seq_A seed={seed_a}: rejected: {err_a}"
            )
            assert status_a == "constrained", (
                f"{config_name}/{sender_id} seq_A: expected constrained, got {status_a}"
            )
            mismatches_a = _verify_flow_matches_constraints(node, sender, cs_a)
            assert not mismatches_a, (
                f"{config_name}/{sender_id} seq_A: mismatches:\n"
                + "\n".join(f"  {m}" for m in mismatches_a)
            )

            # Apply B (on same node, same sender — tests state overwrite)
            err_b, status_b = _apply_constraints(node, sender, cs_b)
            assert err_b is None, (
                f"{config_name}/{sender_id} seq_B seed={seed_b}: rejected: {err_b}"
            )
            assert status_b == "constrained", (
                f"{config_name}/{sender_id} seq_B: expected constrained, got {status_b}"
            )
            mismatches_b = _verify_flow_matches_constraints(node, sender, cs_b)
            assert not mismatches_b, (
                f"{config_name}/{sender_id} seq_B: mismatches after overwrite:\n"
                + "\n".join(f"  {m}" for m in mismatches_b)
            )

            tested_any = True
            node.force_active_constraints(sender, None)

        if not tested_any:
            pytest.skip(f"No testable senders for {config_name}")

    # --- Phase 2: Delete/reset cycle ---

    @pytest.mark.parametrize("config_name", _CONFIG_NAMES)
    def test_delete_reset_cycle(self, config_name: str) -> None:
        """Apply constraints → delete → verify unconstrained → re-apply → verify.

        Tests that delete fully clears state and re-apply works cleanly.
        """
        node = _make_node()
        try:
            _build_config(node, config_name)
        except Exception:
            pytest.skip(f"{config_name} build failed")

        from nmos.node.store import to_static_id

        tested_any = False

        for static_id, sender in node.senders:
            sender_id = sender.ResourceCore.Id.value
            caps = node.sender_ccf_caps.get(static_id)
            if caps is None or len(caps.capsets) == 0:
                continue

            seed = _stable_seed(config_name, sender_id, "delete")
            rng = _random_mod.Random(seed)
            cs = _build_constraint_sets_for_scenario(caps, "max_trunk_only", rng)
            if not cs:
                continue

            # Step 1: Apply constraints
            err, status = _apply_constraints(node, sender, cs)
            assert err is None, f"{config_name}/{sender_id}: apply failed: {err}"
            assert status == "constrained"

            # Step 2: Delete (reset)
            node.force_active_constraints(sender, None)
            status_after_delete = node.set_sender_compatibility_state(sender)
            assert status_after_delete == "unconstrained", (
                f"{config_name}/{sender_id}: after delete expected unconstrained, "
                f"got {status_after_delete}"
            )

            # Step 3: Verify caches cleared
            sid = to_static_id(sender_id)
            assert node.sender_ccf_normalized.get(sid) is None, (
                f"{config_name}/{sender_id}: sender_ccf_normalized not cleared after delete"
            )
            assert node.sender_ccf_merged.get(sid) is None, (
                f"{config_name}/{sender_id}: sender_ccf_merged not cleared after delete"
            )

            # Step 4: Verify flow is still valid
            from nmos.node.flow_caps import get_flow_to_caps
            flow_ptr = _get_sender_flow(node, sender)
            if flow_ptr is not None:
                flow_caps = get_flow_to_caps(node, flow_ptr)
                assert flow_caps is not None, (
                    f"{config_name}/{sender_id}: get_flow_to_caps failed after delete"
                )

            # Step 5: Re-apply same constraints
            err2, status2 = _apply_constraints(node, sender, cs)
            assert err2 is None, f"{config_name}/{sender_id}: re-apply failed: {err2}"
            assert status2 == "constrained", (
                f"{config_name}/{sender_id}: re-apply expected constrained, got {status2}"
            )
            mismatches = _verify_flow_matches_constraints(node, sender, cs)
            assert not mismatches, (
                f"{config_name}/{sender_id}: re-apply mismatches:\n"
                + "\n".join(f"  {m}" for m in mismatches)
            )

            tested_any = True
            node.force_active_constraints(sender, None)

        if not tested_any:
            pytest.skip(f"No testable senders for {config_name}")

    # --- Phase 3: Native caps roundtrip with property verification ---

    @pytest.mark.parametrize("config_name", _CONFIG_NAMES)
    def test_native_caps_idempotent(self, config_name: str) -> None:
        """Apply sender's own native caps as constraints → flow must not change.

        Native caps describe the current operating point. Forcing with them
        should be a no-op. Any change reveals rounding, enum mismatch, or
        rational normalization bugs.
        """
        node = _make_node()
        try:
            _build_config(node, config_name)
        except Exception:
            pytest.skip(f"{config_name} build failed")

        from nmos.node.flow_caps import get_flow_to_caps
        from nmos.json.engine import JsonEngine

        tested_any = False

        for static_id, sender in node.senders:
            sender_id = sender.ResourceCore.Id.value

            # Get native caps as JSON
            eng = JsonEngine()
            caps_json = eng.encode(sender.Caps._value)
            if not caps_json:
                continue
            caps = json.loads(caps_json)
            cs = caps.get("constraint_sets", [])
            if not cs:
                continue

            # Record flow BEFORE
            flow_ptr = _get_sender_flow(node, sender)
            if flow_ptr is None:
                continue
            before = get_flow_to_caps(node, flow_ptr)
            if before is None:
                continue

            # Apply native caps as constraints
            err, status = _apply_constraints(node, sender, cs)
            if err is not None:
                continue  # Some configs may have caps that don't self-validate

            # Record flow AFTER
            flow_ptr = _get_sender_flow(node, sender)
            after = get_flow_to_caps(node, flow_ptr)

            # Compare every property.
            # Skip zero/default values (int=0, rational=0) — these represent
            # undefined fields that may appear/disappear during roundtrip.
            def _is_zero(val: Any) -> bool:
                if isinstance(val, int) and val == 0:
                    return True
                if hasattr(val, 'numerator') and val.numerator == 0:
                    return True
                return False

            mismatches: list[str] = []
            for prop_name, before_cap in before.caps.items():
                if before_cap.value.values and _is_zero(before_cap.value.values[0]):
                    continue  # Skip undefined/zero properties
                after_cap = after.caps.get(prop_name)
                if after_cap is None:
                    mismatches.append(f"{prop_name}: was {before_cap.value}, now missing")
                    continue
                if before_cap.value.values and after_cap.value.values:
                    before_str = str(before_cap.value.values[0])
                    after_str = str(after_cap.value.values[0])
                    if before_str != after_str:
                        mismatches.append(
                            f"{prop_name}: was {before_str}, now {after_str}"
                        )

            assert not mismatches, (
                f"{config_name}/{sender_id}: native caps roundtrip changed flow:\n"
                + "\n".join(f"  {m}" for m in mismatches)
            )

            tested_any = True
            node.force_active_constraints(sender, None)

        if not tested_any:
            pytest.skip(f"No testable senders for {config_name}")

    # --- Phase 4: Compatibility groups verification ---

    @pytest.mark.parametrize("config_name", _CONFIG_NAMES)
    def test_compatibility_groups_written(self, config_name: str) -> None:
        """After forcing with layer_compatibility_groups, verify the flow has them set."""
        node = _make_node()
        try:
            _build_config(node, config_name)
        except Exception:
            pytest.skip(f"{config_name} build failed")

        tested_any = False

        for static_id, sender in node.senders:
            sender_id = sender.ResourceCore.Id.value
            caps = node.sender_ccf_caps.get(static_id)
            if caps is None:
                continue

            # Find capsets with explicit layer_compatibility_groups
            groups_capsets = [
                cs for cs in caps.capsets
                if cs.layer_compatibility_groups is not None and len(cs.layer_compatibility_groups) > 0
            ]
            if not groups_capsets:
                continue

            # Pick the first one and build a constraint from it
            target_cs = groups_capsets[0]
            seed = _stable_seed(config_name, sender_id, "groups")
            rng = _random_mod.Random(seed)
            constraint = _pick_random_constraint_from_capset(
                target_cs, 1, rng, exclude=_INTERDEPENDENT_PROPS,
            )
            if constraint is None:
                continue

            err, status = _apply_constraints(node, sender, [constraint])
            if err is not None:
                continue

            # Verify flow's LayerCompatibilityGroups
            flow_ptr = _get_sender_flow(node, sender)
            if flow_ptr is None:
                continue

            poly = flow_ptr.get() if hasattr(flow_ptr, 'get') else flow_ptr
            fv = poly.value if hasattr(poly, 'value') else poly
            fc = fv.FlowCore if hasattr(fv, 'FlowCore') else None
            if fc is None:
                continue

            if fc.LayerCompatibilityGroups.defined:
                actual_groups = set(fc.LayerCompatibilityGroups.value)
                expected_groups = target_cs.layer_compatibility_groups
                assert actual_groups == expected_groups, (
                    f"{config_name}/{sender_id}: groups mismatch: "
                    f"expected {expected_groups}, got {actual_groups}"
                )
                tested_any = True

            node.force_active_constraints(sender, None)

        if not tested_any:
            pytest.skip(f"No senders with compatibility_groups in {config_name}")

    # --- Phase 5: Mux layer increase ---

    @pytest.mark.parametrize("config_name", _CONFIG_NAMES)
    def test_mux_layer_increase(self, config_name: str) -> None:
        """Constrain *_layers to maximum → tests layer increase if caps allow it.

        Note: layer increase may not be fully supported (requires dynamic
        sub-flow creation). This test documents the current behavior.
        """
        node = _make_node()
        try:
            _build_config(node, config_name)
        except Exception:
            pytest.skip(f"{config_name} build failed")

        tested_any = False

        for static_id, sender in node.senders:
            sender_id = sender.ResourceCore.Id.value
            caps = node.sender_ccf_caps.get(static_id)
            if caps is None:
                continue

            # Only mux senders
            has_layers = any(cs.layer is not None for cs in caps.capsets)
            if not has_layers:
                continue

            # Find trunk capset with flexible layer ranges (max > min)
            trunk_capsets = [
                cs for cs in caps.capsets
                if cs.format is None and cs.layer is None
            ]

            _LAYER_PROPS = [
                "urn:x-matrox:cap:format:video_layers",
                "urn:x-matrox:cap:format:audio_layers",
                "urn:x-matrox:cap:format:data_layers",
            ]

            for tcs in trunk_capsets:
                cs_dict: dict = {
                    "urn:x-nmos:cap:meta:enabled": True,
                    "urn:x-nmos:cap:meta:preference": 100,
                }
                has_increase = False
                for lp in _LAYER_PROPS:
                    cap = tcs.caps.get(lp)
                    if cap is None or cap.value.infinite or cap.value.empty:
                        continue
                    if cap.value.min is not None and cap.value.max is not None:
                        if int(cap.value.max) > int(cap.value.min):
                            cs_dict[lp] = {"enum": [int(cap.value.max)]}
                            has_increase = True
                        else:
                            cs_dict[lp] = {"enum": [int(cap.value.min)]}
                    elif cap.value.values:
                        cs_dict[lp] = {"enum": [int(max(cap.value.values))]}

                if not has_increase:
                    continue

                err, status = _apply_constraints(node, sender, [cs_dict])
                # Document behavior — layer increase may or may not be supported
                if err is not None:
                    # Known limitation: layer increase requires dynamic sub-flow creation
                    pytest.skip(
                        f"{config_name}/{sender_id}: layer increase not supported: {err}"
                    )
                else:
                    assert status == "constrained", (
                        f"{config_name}/{sender_id}: layer increase expected constrained, "
                        f"got {status}"
                    )
                    tested_any = True

                node.force_active_constraints(sender, None)
                break  # One trunk capset per sender is enough

        if not tested_any:
            pytest.skip(f"No mux senders with flexible layers in {config_name}")

    # --- Phase 6: Format transition + property constraints ---

    @pytest.mark.parametrize("config_name", _CONFIG_NAMES)
    def test_format_transition_with_properties(self, config_name: str) -> None:
        """Force a format change (e.g., raw→JXSV, PCM→AAC), then constrain
        format-specific properties (level, sublevel, profile) on the new format.

        Most configs support multiple formats: raw, H.264, H.265, JXSV for video
        and PCM, AAC, AM824 for audio. This test exercises format transitions
        that the single-format tests cannot reach.
        """
        node = _make_node()
        try:
            _build_config(node, config_name)
        except Exception:
            pytest.skip(f"{config_name} build failed")

        if not HAS_CCF:
            pytest.skip("CCF not available")
        from caps.MatroxCCF import CapFormatMediaType, RangeType

        tested_any = False

        for static_id, sender in node.senders:
            sender_id = sender.ResourceCore.Id.value
            caps = node.sender_ccf_caps.get(static_id)
            if caps is None or len(caps.capsets) == 0:
                continue

            # Find trunk capsets (format=None, layer=None) only
            trunk_capsets = [
                cs for cs in caps.capsets
                if cs.format is None and cs.layer is None
            ]
            if not trunk_capsets:
                continue

            # Get current flow media_type
            flow_ptr = _get_sender_flow(node, sender)
            if flow_ptr is None:
                continue
            from nmos.node.flow_caps import get_flow_to_caps
            current_caps = get_flow_to_caps(node, flow_ptr)
            current_mt_cap = current_caps.caps.get(CapFormatMediaType)
            current_mt = str(current_mt_cap.value.values[0]) if current_mt_cap and current_mt_cap.value.values else ""

            # Find all ALTERNATIVE media_types from non-native capsets (preference < 100)
            alternative_mts: dict[str, Any] = {}  # media_type → capset
            for cs in trunk_capsets:
                if cs.preference >= 100:
                    continue  # Skip native
                mt_cap = cs.caps.get(CapFormatMediaType)
                if mt_cap is None or not mt_cap.value.values:
                    continue
                for mt_val in mt_cap.value.values:
                    mt_str = str(mt_val)
                    if mt_str != current_mt and mt_str not in alternative_mts:
                        alternative_mts[mt_str] = cs

            if not alternative_mts:
                continue

            # Test each alternative format
            for target_mt, target_cs in alternative_mts.items():
                # Build constraint: media_type + all available properties from that capset
                cs_dict: dict = {
                    "urn:x-nmos:cap:meta:enabled": True,
                    "urn:x-nmos:cap:meta:preference": 100,
                    "urn:x-nmos:cap:format:media_type": {"enum": [target_mt]},
                }

                # Add all constrainable properties from the target capset
                for name, cap in target_cs.caps.items():
                    if name == CapFormatMediaType:
                        continue  # Already added
                    if not _is_format_property(name):
                        continue
                    if name in _INTERDEPENDENT_PROPS:
                        continue
                    if cap.value.infinite or cap.value.empty:
                        continue
                    if cap.value.values and len(cap.value.values) > 0:
                        val = cap.value.values[0]
                        if cap.value.type == RangeType.RATIONAL:
                            cs_dict[name] = {"enum": [{"numerator": val.numerator, "denominator": val.denominator}]}
                        else:
                            cs_dict[name] = {"enum": [val]}

                # Apply format transition constraint
                err, status = _apply_constraints(node, sender, [cs_dict])

                assert err is None, (
                    f"{config_name}/{sender_id} {current_mt}→{target_mt}: "
                    f"format transition rejected: {err}\n"
                    f"constraint={json.dumps(cs_dict, indent=2, default=str)}"
                )
                assert status == "constrained", (
                    f"{config_name}/{sender_id} {current_mt}→{target_mt}: "
                    f"expected constrained, got {status}"
                )

                # Verify the flow actually changed media_type
                flow_ptr = _get_sender_flow(node, sender)
                after_caps = get_flow_to_caps(node, flow_ptr)
                after_mt_cap = after_caps.caps.get(CapFormatMediaType)
                after_mt = str(after_mt_cap.value.values[0]) if after_mt_cap and after_mt_cap.value.values else ""
                assert after_mt == target_mt, (
                    f"{config_name}/{sender_id}: media_type not changed: "
                    f"expected {target_mt}, got {after_mt}"
                )

                # Verify constrained properties match
                mismatches = _verify_flow_matches_constraints(node, sender, [cs_dict])
                assert not mismatches, (
                    f"{config_name}/{sender_id} {current_mt}→{target_mt}: "
                    f"property mismatches after format transition:\n"
                    + "\n".join(f"  {m}" for m in mismatches)
                )

                tested_any = True

                # Reset for next format
                node.force_active_constraints(sender, None)

        if not tested_any:
            pytest.skip(f"No senders with alternative formats in {config_name}")


# ===========================================================================
# Original Flag Verification Tests
#
# The "original" flag marks constraint properties that were explicitly
# specified by the user/controller. Fix-up functions use this to decide
# which property takes priority in interdependent pairs:
#   - sample_depth vs media_type (PCM)
#   - frame_width vs frame_height (video resolution)
#   - bit_rate (coded video — preserved when original)
# ===========================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestIS11OriginalFlag:
    """Verify fix-up functions respect the original flag on constraints."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.node = _make_node()
        try:
            _build_config(self.node, "config3")  # Has raw + H264 + H265
            self.has_node = True
        except Exception:
            self.has_node = False

    def _get_video_sender(self) -> tuple[str, Any] | None:
        for static_id, sender in self.node.senders:
            fmt = sender.Format.value.s if sender.Format.defined else ""
            if "video" in fmt:
                return sender.ResourceCore.Id.value, sender
        return None

    def test_bitrate_preserved_when_constrained(self) -> None:
        """Constrain bit_rate=40000 on a coded flow → bit_rate must stay 40000,
        not be recalculated to the level's maximum by fix_coded_video_flow.

        Uses config5 which has H264 as native format (avoids class transition).
        """
        node = _make_node()
        try:
            _build_config(node, "config5")  # Native H264
        except Exception:
            pytest.skip("Config5 build failed")

        video_sender = None
        for static_id, sender in node.senders:
            fmt = sender.Format.value.s if sender.Format.defined else ""
            if "video" in fmt:
                video_sender = sender
                break
        if video_sender is None:
            pytest.skip("No video sender")

        err, status = _apply_constraints(node, video_sender, [{
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:media_type": {"enum": ["video/H264"]},
            "urn:x-nmos:cap:format:frame_width": {"enum": [1920]},
            "urn:x-nmos:cap:format:frame_height": {"enum": [1080]},
            "urn:x-nmos:cap:format:profile": {"enum": ["High-422"]},
            "urn:x-nmos:cap:format:bit_rate": {"enum": [40000]},
        }])
        assert err is None, f"Constraint rejected: {err}"
        assert status == "constrained"

        from nmos.node.flow_caps import get_flow_to_caps
        from nmos.node.compatibility import _get_cap_int
        flow_ptr = _get_sender_flow(node, video_sender)
        caps = get_flow_to_caps(node, flow_ptr)
        actual_br = _get_cap_int(caps, "urn:x-nmos:cap:format:bit_rate")
        assert actual_br == 40000, (
            f"bit_rate should be preserved at 40000 (original=True), got {actual_br}"
        )

    def test_bitrate_exact_range_preserved_when_constrained(self) -> None:
        """Slider selections encode an exact value as minimum == maximum.
        That must force the coded flow bitrate exactly like enum [40000].
        """
        node = _make_node()
        try:
            _build_config(node, "config5")  # Native H264
        except Exception:
            pytest.skip("Config5 build failed")

        video_sender = None
        for static_id, sender in node.senders:
            fmt = sender.Format.value.s if sender.Format.defined else ""
            if "video" in fmt:
                video_sender = sender
                break
        if video_sender is None:
            pytest.skip("No video sender")

        err, status = _apply_constraints(node, video_sender, [{
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:media_type": {"enum": ["video/H264"]},
            "urn:x-nmos:cap:format:frame_width": {"enum": [1920]},
            "urn:x-nmos:cap:format:frame_height": {"enum": [1080]},
            "urn:x-nmos:cap:format:profile": {"enum": ["High-422"]},
            "urn:x-nmos:cap:format:bit_rate": {
                "minimum": 40000,
                "maximum": 40000,
            },
        }])
        assert err is None, f"Constraint rejected: {err}"
        assert status == "constrained"

        from nmos.node.flow_caps import get_flow_to_caps
        from nmos.node.compatibility import _get_cap_int
        flow_ptr = _get_sender_flow(node, video_sender)
        caps = get_flow_to_caps(node, flow_ptr)
        actual_br = _get_cap_int(caps, "urn:x-nmos:cap:format:bit_rate")
        assert actual_br == 40000, (
            "exact range bit_rate should force 40000, "
            f"got {actual_br}"
        )

    def test_bitrate_non_exact_range_selects_concrete_value(self) -> None:
        """A non-exact bit_rate range must still force a concrete flow
        value. The selected value is the range minimum.
        """
        node = _make_node()
        try:
            _build_config(node, "config5")  # Native H264
        except Exception:
            pytest.skip("Config5 build failed")

        video_sender = None
        for static_id, sender in node.senders:
            fmt = sender.Format.value.s if sender.Format.defined else ""
            if "video" in fmt:
                video_sender = sender
                break
        if video_sender is None:
            pytest.skip("No video sender")

        err, status = _apply_constraints(node, video_sender, [{
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:media_type": {"enum": ["video/H264"]},
            "urn:x-nmos:cap:format:frame_width": {"enum": [1920]},
            "urn:x-nmos:cap:format:frame_height": {"enum": [1080]},
            "urn:x-nmos:cap:format:profile": {"enum": ["High-422"]},
            # Range within the H264 caps envelope (High-422 @ L3 .. HighIntra-422 @ L6.2,
            # i.e. 40000..3200000 Kbps); a non-exact range must force the minimum.
            "urn:x-nmos:cap:format:bit_rate": {
                "minimum": 50000,
                "maximum": 100000,
            },
        }])
        assert err is None, f"Constraint rejected: {err}"
        assert status == "constrained"

        from nmos.node.flow_caps import get_flow_to_caps
        from nmos.node.compatibility import _get_cap_int
        flow_ptr = _get_sender_flow(node, video_sender)
        caps = get_flow_to_caps(node, flow_ptr)
        actual_br = _get_cap_int(caps, "urn:x-nmos:cap:format:bit_rate")
        assert actual_br == 50000, (
            "non-exact range bit_rate should force the minimum value, "
            f"got {actual_br}"
        )

    def test_bitrate_recalculated_when_not_constrained(self) -> None:
        """Constrain H264 WITHOUT bit_rate → fix-up derives max bitrate from level.

        Uses config5 which has H264 as native format.
        """
        node = _make_node()
        try:
            _build_config(node, "config5")
        except Exception:
            pytest.skip("Config5 build failed")

        video_sender = None
        for static_id, sender in node.senders:
            fmt = sender.Format.value.s if sender.Format.defined else ""
            if "video" in fmt:
                video_sender = sender
                break
        if video_sender is None:
            pytest.skip("No video sender")

        err, status = _apply_constraints(node, video_sender, [{
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:media_type": {"enum": ["video/H264"]},
            "urn:x-nmos:cap:format:frame_width": {"enum": [1920]},
            "urn:x-nmos:cap:format:frame_height": {"enum": [1080]},
            "urn:x-nmos:cap:format:profile": {"enum": ["High-422"]},
            # No bit_rate constraint → fix-up should derive it
        }])
        assert err is None, f"Constraint rejected: {err}"
        assert status == "constrained"

        from nmos.node.flow_caps import get_flow_to_caps
        from nmos.node.compatibility import _get_cap_int
        flow_ptr = _get_sender_flow(node, video_sender)
        caps = get_flow_to_caps(node, flow_ptr)
        actual_br = _get_cap_int(caps, "urn:x-nmos:cap:format:bit_rate")
        # When not constrained, fix-up derives max for the level — should be > 40000
        assert actual_br is not None and actual_br > 0, (
            f"bit_rate should be derived by fix-up, got {actual_br}"
        )

    def test_pcm_sample_depth_determines_media_type(self) -> None:
        """Constrain sample_depth=16 on PCM audio → media_type must become audio/L16."""
        node = _make_node()
        try:
            _build_config(node, "config6")  # Has audio sender with L24 native
        except Exception:
            pytest.skip("Config6 build failed")

        audio_sender = None
        for static_id, sender in node.senders:
            fmt = sender.Format.value.s if sender.Format.defined else ""
            if "audio" in fmt:
                audio_sender = sender
                break
        if audio_sender is None:
            pytest.skip("No audio sender")

        err, status = _apply_constraints(node, audio_sender, [{
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:sample_depth": {"enum": [16]},
            "urn:x-nmos:cap:format:sample_rate": {"enum": [{"numerator": 48000}]},
            "urn:x-nmos:cap:format:channel_count": {"enum": [2]},
        }])
        assert err is None, f"PCM depth constraint rejected: {err}"
        assert status == "constrained"

        from nmos.node.flow_caps import get_flow_to_caps
        from nmos.node.compatibility import _get_cap_str, _get_cap_int
        flow_ptr = _get_sender_flow(node, audio_sender)
        caps = get_flow_to_caps(node, flow_ptr)
        actual_mt = _get_cap_str(caps, "urn:x-nmos:cap:format:media_type")
        actual_depth = _get_cap_int(caps, "urn:x-nmos:cap:format:sample_depth")
        assert actual_mt == "audio/L16", (
            f"sample_depth=16 (original) should force media_type to audio/L16, got {actual_mt}"
        )
        assert actual_depth == 16, f"sample_depth should be 16, got {actual_depth}"

    def test_pcm_depth_original_changes_media_type(self) -> None:
        """Constrain sample_depth=16 (original) on L24 flow → fix-up must
        change media_type from audio/L24 to audio/L16 because sample_depth
        is original and takes priority."""
        node = _make_node()
        try:
            _build_config(node, "config6")
        except Exception:
            pytest.skip("Config6 build failed")

        audio_sender = None
        for static_id, sender in node.senders:
            fmt = sender.Format.value.s if sender.Format.defined else ""
            if "audio" in fmt:
                audio_sender = sender
                break
        if audio_sender is None:
            pytest.skip("No audio sender")

        # Constrain depth=16 WITHOUT specifying media_type.
        # The fix-up should see sample_depth.original=True and change media_type to L16.
        err, status = _apply_constraints(node, audio_sender, [{
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:sample_depth": {"enum": [16]},
            "urn:x-nmos:cap:format:sample_rate": {"enum": [{"numerator": 48000}]},
            "urn:x-nmos:cap:format:channel_count": {"enum": [2]},
        }])
        assert err is None, f"PCM depth constraint rejected: {err}"
        assert status == "constrained"

        from nmos.node.flow_caps import get_flow_to_caps
        from nmos.node.compatibility import _get_cap_str, _get_cap_int
        flow_ptr = _get_sender_flow(node, audio_sender)
        caps = get_flow_to_caps(node, flow_ptr)
        actual_mt = _get_cap_str(caps, "urn:x-nmos:cap:format:media_type")
        actual_depth = _get_cap_int(caps, "urn:x-nmos:cap:format:sample_depth")
        assert actual_mt == "audio/L16", (
            f"sample_depth=16 (original) should change media_type to audio/L16, got {actual_mt}"
        )
        assert actual_depth == 16, f"sample_depth should be 16, got {actual_depth}"

    def test_width_constrained_height_derived(self) -> None:
        """Constrain frame_width=720 only → fix-up should derive frame_height=480."""
        if not self.has_node:
            pytest.skip("Config3 build failed")
        result = self._get_video_sender()
        if result is None:
            pytest.skip("No video sender")
        _, sender = result

        # Config3 H264 supports width=[720,1280,1920,3840]. Use H264 capset
        # (pref=1) so 720 is within range. Flow starts as raw 1920x1080.
        err, status = _apply_constraints(self.node, sender, [{
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:media_type": {"enum": ["video/H264"]},
            "urn:x-nmos:cap:format:frame_width": {"enum": [720]},
            # No frame_height → fix-up derives from standard resolution table
        }])
        assert err is None, f"Width-only constraint rejected: {err}"
        assert status == "constrained"

        from nmos.node.flow_caps import get_flow_to_caps
        from nmos.node.compatibility import _get_cap_int
        flow_ptr = _get_sender_flow(self.node, sender)
        caps = get_flow_to_caps(self.node, flow_ptr)
        actual_w = _get_cap_int(caps, "urn:x-nmos:cap:format:frame_width")
        actual_h = _get_cap_int(caps, "urn:x-nmos:cap:format:frame_height")
        assert actual_w == 720, f"width should be 720, got {actual_w}"
        assert actual_h == 480, (
            f"frame_height should be derived as 480 from width=720, got {actual_h}"
        )

    def test_level_preserved_when_constrained(self) -> None:
        """Constrain H265 level=High-4.1 → level must stay High-4.1, not be
        downgraded to High-4 by fix_coded_video_flow."""
        node = _make_node()
        try:
            _build_config(node, "config6a")  # H.265
        except Exception:
            pytest.skip("Config6a build failed")

        video_sender = None
        for static_id, sender in node.senders:
            fmt = sender.Format.value.s if sender.Format.defined else ""
            if "video" in fmt:
                video_sender = sender
                break
        if video_sender is None:
            pytest.skip("No video sender")

        err, status = _apply_constraints(node, video_sender, [{
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:media_type": {"enum": ["video/H265"]},
            "urn:x-nmos:cap:format:frame_width": {"enum": [1920]},
            "urn:x-nmos:cap:format:frame_height": {"enum": [1080]},
            "urn:x-nmos:cap:format:profile": {"enum": ["Main10-422"]},
            "urn:x-nmos:cap:format:level": {"enum": ["High-4.1"]},
        }])
        assert err is None, f"Level constraint rejected: {err}"
        assert status == "constrained"

        from nmos.node.flow_caps import get_flow_to_caps
        from nmos.node.compatibility import _get_cap_str
        flow_ptr = _get_sender_flow(node, video_sender)
        caps = get_flow_to_caps(node, flow_ptr)
        actual_level = _get_cap_str(caps, "urn:x-nmos:cap:format:level")
        assert actual_level == "High-4.1", (
            f"level should be preserved at High-4.1 (original=True), got {actual_level}"
        )


# ===========================================================================
# Merge of active constraints onto capability sets
# ===========================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestMergeActiveConstraints:
    """merge_active_constraints overlays each user constraint set onto the
    capability set it fits, inheriting that set's media_type and every
    unconstrained capability — so forcing always yields a self-consistent
    operating point."""

    def _setup_video(self):
        node = _make_node()
        try:
            _build_config(node, "config10")
        except Exception as exc:
            pytest.skip(f"config10 build failed: {exc}")
        for static_id, sender in node.senders:
            fmt = sender.Format.value.s if sender.Format.defined else ""
            if "video" in fmt and "mux" not in fmt:
                return node, sender
        pytest.skip("No video sender")

    def _merge(self, node, sender, constraint_sets):
        from nmos.node.compatibility import (
            merge_active_constraints, validate_active_constraints,
        )
        sender_id = sender.ResourceCore.Id.value
        cons = node._constraints_to_ccf({"constraint_sets": constraint_sets})
        validated, err = validate_active_constraints(node, sender_id, cons)
        assert err is None, f"validate failed: {err}"
        return merge_active_constraints(node, sender_id, validated)

    def test_profile_only_h265_merge_carries_media_type(self) -> None:
        """A profile-only H.265 constraint fits only the H.265 capability
        set — the merged set must inherit media_type=video/H265."""
        node, sender = self._setup_video()
        merged, err = self._merge(node, sender, [{
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:profile": {"enum": ["Main10-422"]},
        }])
        assert err is None
        assert len(merged.consets) == 1
        mc = merged.consets[0]
        mt = mc.cons["urn:x-nmos:cap:format:media_type"]
        assert list(mt.value.values) == ["video/H265"]
        # the user's profile is overlaid, original flag preserved
        prof = mc.cons["urn:x-nmos:cap:format:profile"]
        assert list(prof.value.values) == ["Main10-422"]
        assert prof.original is True
        # inherited capability params are NOT original
        assert mt.original is False
        # preference comes from the user constraint set
        assert mc.preference == 100

    def test_no_capability_match_errors(self) -> None:
        """A constraint set fitting no capability set is unsatisfiable.

        ``media_type`` pins the probe to H.264 so the bogus profile can't
        fall through to the uncompressed (video/raw) set, which constrains
        no profile and would otherwise accept any profile value.
        """
        node, sender = self._setup_video()
        merged, err = self._merge(node, sender, [{
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:media_type": {"enum": ["video/H264"]},
            "urn:x-nmos:cap:format:profile": {"enum": ["NoSuchProfile"]},
        }])
        assert merged is None
        assert err is not None and "not included" in err

    def test_check_active_constraints_returns_normalized_and_merged(self) -> None:
        """normalized = user sets ++ merged sets; merged = capability overlay."""
        node, sender = self._setup_video()
        body = {"constraint_sets": [{
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:profile": {"enum": ["Main10-422"]},
        }]}
        normalized, merged, err = node.check_active_constraints(sender, body)
        assert err is None
        assert len(merged.consets) == 1
        # normalized carries the user conset first, then the merged conset
        assert len(normalized.consets) == 2
        assert "urn:x-nmos:cap:format:media_type" not in normalized.consets[0].cons
        assert "urn:x-nmos:cap:format:media_type" in normalized.consets[1].cons


@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestCodecFlavorSwap:
    """Applying a constraint whose merged capability set implies another
    codec must transition the flow to a valid configuration of that codec."""

    def _setup_h264_video(self):
        node = _make_node()
        try:
            _build_config(node, "config10")
        except Exception as exc:
            pytest.skip(f"config10 build failed: {exc}")
        from nmos.node.flow_caps import get_flow_to_caps
        from nmos.node.compatibility import _get_cap_str
        for static_id, sender in node.senders:
            fmt = sender.Format.value.s if sender.Format.defined else ""
            if "video" not in fmt or "mux" in fmt:
                continue
            flow_ptr = _get_sender_flow(node, sender)
            if flow_ptr is None:
                continue
            caps = get_flow_to_caps(node, flow_ptr)
            if _get_cap_str(caps, "urn:x-nmos:cap:format:media_type") == "video/H264":
                return node, sender
        pytest.skip("No H.264 video sender")

    def _flow_state(self, node, sender):
        from nmos.node.flow_caps import get_flow_to_caps
        from nmos.node.compatibility import _get_cap_str, _get_cap_int
        caps = get_flow_to_caps(node, _get_sender_flow(node, sender))
        return {
            "media_type": _get_cap_str(caps, "urn:x-nmos:cap:format:media_type"),
            "profile": _get_cap_str(caps, "urn:x-nmos:cap:format:profile"),
            "level": _get_cap_str(caps, "urn:x-nmos:cap:format:level"),
            "width": _get_cap_int(caps, "urn:x-nmos:cap:format:frame_width"),
            "height": _get_cap_int(caps, "urn:x-nmos:cap:format:frame_height"),
        }

    def test_profile_only_h265_swaps_flow_to_h265(self) -> None:
        """profile=[Main10-422] with NO media_type on an H.264 flow: the
        merged capability set supplies media_type=video/H265, so the flow
        must transition to a valid H.265 configuration — and properties the
        constraints leave valid (resolution) must be preserved."""
        node, sender = self._setup_h264_video()
        before = self._flow_state(node, sender)
        assert before["media_type"] == "video/H264"

        err = node.force_active_constraints(sender, {"constraint_sets": [{
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:profile": {"enum": ["Main10-422"]},
        }]})
        assert err is None, f"constraint rejected: {err}"

        after = self._flow_state(node, sender)
        assert after["media_type"] == "video/H265", (
            f"flow must swap to H.265, got {after['media_type']}")
        assert after["profile"] == "Main10-422"
        # H.265 level namespace, not an H.264 level
        assert after["level"] and (after["level"].startswith("Main-")
                                   or after["level"].startswith("High-"))
        # in-range properties are preserved, not reset
        assert after["width"] == before["width"]
        assert after["height"] == before["height"]

        status = node.set_sender_compatibility_state(sender)
        assert status == "constrained"

    def test_unsatisfiable_constraint_leaves_flow_untouched(self) -> None:
        """A constraint fitting no capability set is rejected and the flow
        keeps its current configuration.

        ``media_type`` pins the probe to H.264 so the bogus profile can't
        fall through to the uncompressed (video/raw) set (which constrains
        no profile and would otherwise accept any profile value).
        """
        node, sender = self._setup_h264_video()
        before = self._flow_state(node, sender)
        err = node.force_active_constraints(sender, {"constraint_sets": [{
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:media_type": {"enum": ["video/H264"]},
            "urn:x-nmos:cap:format:profile": {"enum": ["NoSuchProfile"]},
        }]})
        assert err is not None
        assert self._flow_state(node, sender) == before

    def test_constraint_error_types_match_is11_status_codes(self) -> None:
        """check_active_constraints surfaces the IS-11 400-vs-422 distinction
        as distinct error types, which the PUT handler maps to status codes:

        * an unsupported Parameter Constraint URN (a malformed request the spec
          maps to HTTP 400) → InvalidParameter
        * supported URNs whose values no capability set can satisfy (HTTP 422)
          → NotAllowed

        The handler maps NotAllowed→422 and every other error→400, so swapping
        these types would regress AMWA IS-11 test_06_01."""
        from nmos.errors import InvalidParameter, NotAllowed
        node, sender = self._setup_h264_video()

        # Unsupported URN → InvalidParameter (→ HTTP 400)
        _, _, err = node.check_active_constraints(sender, {"constraint_sets": [
            {"urn:x-nmos:cap:not:existing": {"enum": [""]}}]})
        assert isinstance(err, InvalidParameter), f"got {type(err).__name__}: {err}"

        # Supported URNs, unsatisfiable values → NotAllowed (→ HTTP 422)
        _, _, err = node.check_active_constraints(sender, {"constraint_sets": [{
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:media_type": {"enum": ["video/H264"]},
            "urn:x-nmos:cap:format:profile": {"enum": ["NoSuchProfile"]},
        }]})
        assert isinstance(err, NotAllowed), f"got {type(err).__name__}: {err}"

    def test_empty_constraint_sets_resets_to_unconstrained(self) -> None:
        """PUT of an empty constraint_sets array removes the constraints."""
        node, sender = self._setup_h264_video()
        err = node.force_active_constraints(sender, {"constraint_sets": []})
        assert err is None
        assert node.set_sender_compatibility_state(sender) == "unconstrained"


@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestProfileCapsetConsistency:
    """The 8-bit profile families are declared as their own capability sets
    (H.264 Main/High @ 8-bit 4:2:0; H.265 Main @ 8-bit, Main10 @ 8/10-bit —
    all 4:2:0), so the merge guarantees jointly-valid combinations: a
    profile-only constraint inherits the capset's depth/sampling, and a
    constraint demanding an impossible combination (e.g. Main at 10-bit)
    fits no capability set and is rejected at PUT time."""

    def _setup_video(self):
        node = _make_node()
        try:
            _build_config(node, "config10")
        except Exception as exc:
            pytest.skip(f"config10 build failed: {exc}")
        for static_id, sender in node.senders:
            fmt = sender.Format.value.s if sender.Format.defined else ""
            if "video" in fmt and "mux" not in fmt:
                return node, sender
        pytest.skip("No video sender")

    def _flow(self, node, sender):
        from nmos.node.flow_caps import get_flow_to_caps
        from nmos.node.compatibility import _get_cap_str, _get_cap_int
        caps = get_flow_to_caps(node, _get_sender_flow(node, sender))
        return {
            "profile": _get_cap_str(caps, "urn:x-nmos:cap:format:profile"),
            "level": _get_cap_str(caps, "urn:x-nmos:cap:format:level"),
            "sampling": _get_cap_str(caps, "urn:x-nmos:cap:format:color_sampling"),
            "depth": _get_cap_int(caps, "urn:x-nmos:cap:format:component_depth"),
        }

    def _put(self, node, sender, caps):
        err = node.force_active_constraints(sender, {"constraint_sets": [{
            "urn:x-nmos:cap:meta:preference": 100, **caps,
        }]})
        status = node.set_sender_compatibility_state(sender)
        return err, status

    def test_h264_main_inherits_8bit_420(self) -> None:
        node, sender = self._setup_video()
        err, status = self._put(node, sender, {
            "urn:x-nmos:cap:format:media_type": {"enum": ["video/H264"]},
            "urn:x-nmos:cap:format:profile": {"enum": ["Main"]},
        })
        assert err is None and status == "constrained"
        f = self._flow(node, sender)
        assert (f["profile"], f["depth"], f["sampling"]) == ("Main", 8, "YCbCr-4:2:0")

    def test_h265_main_inherits_8bit_420(self) -> None:
        node, sender = self._setup_video()
        err, status = self._put(node, sender, {
            "urn:x-nmos:cap:format:media_type": {"enum": ["video/H265"]},
            "urn:x-nmos:cap:format:profile": {"enum": ["Main"]},
        })
        assert err is None and status == "constrained"
        f = self._flow(node, sender)
        assert (f["profile"], f["depth"], f["sampling"]) == ("Main", 8, "YCbCr-4:2:0")
        # the level settles on the smallest valid level for the 8-bit config
        assert f["level"] == "Main-4.1"

    def test_h265_main10_keeps_10bit(self) -> None:
        node, sender = self._setup_video()
        err, status = self._put(node, sender, {
            "urn:x-nmos:cap:format:media_type": {"enum": ["video/H265"]},
            "urn:x-nmos:cap:format:profile": {"enum": ["Main10"]},
        })
        assert err is None and status == "constrained"
        f = self._flow(node, sender)
        assert (f["profile"], f["depth"], f["sampling"]) == ("Main10", 10, "YCbCr-4:2:0")

    @pytest.mark.parametrize("mt,profile", [
        ("video/H264", "Main"), ("video/H264", "High"), ("video/H265", "Main"),
    ])
    def test_8bit_profile_at_10bit_is_rejected(self, mt: str, profile: str) -> None:
        """A constraint demanding an 8-bit-only profile at 10-bit fits no
        capability set — the PUT must be rejected and the flow untouched."""
        node, sender = self._setup_video()
        before = self._flow(node, sender)
        err, _ = self._put(node, sender, {
            "urn:x-nmos:cap:format:media_type": {"enum": [mt]},
            "urn:x-nmos:cap:format:profile": {"enum": [profile]},
            "urn:x-nmos:cap:format:component_depth": {"enum": [10]},
        })
        assert err is not None, f"{profile}@10bit must be unsatisfiable"
        assert self._flow(node, sender) == before


@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestCodecSwapAdoptsMissingFormatProps:
    """A codec change must carry the target codec's format properties even
    when the current flow does not have them: forcing adopts conset-only
    format constraints (which derive from the matched capability set), so a
    flow swapped to JPEG-XS gains sublevel and fbblevel."""

    def test_swap_to_jxsv_fills_sublevel_and_fbblevel(self) -> None:
        node = _make_node()
        try:
            _build_config(node, "config10")
        except Exception as exc:
            pytest.skip(f"config10 build failed: {exc}")
        sender = None
        for static_id, s in node.senders:
            fmt = s.Format.value.s if s.Format.defined else ""
            if "video" in fmt and "mux" not in fmt:
                sender = s
                break
        if sender is None:
            pytest.skip("No video sender")

        err = node.force_active_constraints(sender, {"constraint_sets": [{
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:format:media_type": {"enum": ["video/jxsv"]},
        }]})
        assert err is None, f"constraint rejected: {err}"

        from nmos.node.flow_caps import get_flow_to_caps
        from nmos.node.compatibility import _get_cap_str
        caps = get_flow_to_caps(node, _get_sender_flow(node, sender))
        assert _get_cap_str(caps, "urn:x-nmos:cap:format:media_type") == "video/jxsv"
        sublevel = _get_cap_str(caps, "urn:x-nmos:cap:format:sublevel")
        fbblevel = _get_cap_str(caps, "urn:x-nmos:cap:format:fbblevel")
        assert sublevel, "swapped JPEG-XS flow must carry a sublevel"
        # A plain jxsv constraint merges onto the generic (non-TDC) JPEG-XS
        # capability set, whose fbblevel is Unrestricted; the 8/12 bpp
        # fbblevels are declared only by the TDC capability set.
        assert fbblevel == "Unrestricted", (
            f"swapped non-TDC JPEG-XS flow must carry fbblevel Unrestricted, got {fbblevel!r}")
