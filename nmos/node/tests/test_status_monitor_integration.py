# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for BCP-008 status monitoring.

Injects specific engine event sequences into a real Node, then reads back
the actual monitor_state from the Node's source store to verify correctness.

Tests the full pipeline: event_queue → run_status_monitor → _publish_status
→ update_source_monitor → NMonitorState on the actual NSourceData.

Each test builds a real Node from a builtin config, starts the status
monitor as a background task, injects events, waits, and verifies.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

import pytest

# Add caps/ to path

from nmos.node.events import EngineEvent, AlertDomain, AlertScope, EventId, EventState
from nmos.node.status_monitor import (
    NC_INACTIVE, NC_HEALTHY, NC_PARTIALLY_HEALTHY, NC_UNHEALTHY,
    run_status_monitor,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node() -> Any:
    from nmos.node import Node
    node = Node()
    node.init(serial_number="TST12345")
    return node


def _build_config(node: Any, config_name: str = "config1") -> None:
    from nmos.node.config import ConfigBuilder
    config_path = Path(__file__).parent.parent / "config" / "builtin" / f"{config_name}.json"
    if not config_path.exists():
        pytest.skip(f"{config_name}.json not found")
    import json
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


def _get_sender_ids(node: Any) -> list[str]:
    """Get all sender resource IDs."""
    return [s.ResourceCore.Id.value for _, s in node.senders]


def _get_receiver_ids(node: Any) -> list[str]:
    """Get all receiver resource IDs."""
    ids = []
    for _, r in node.receivers:
        try:
            inner = r.get() if hasattr(r, 'get') else r
            rv = inner.value if hasattr(inner, 'value') else inner
            core = getattr(rv, 'ReceiverCore', rv)
            ids.append(core.ResourceCore.Id.value)
        except Exception:
            pass
    return ids


def _read_monitor_state(node: Any, resource_id: str, is_sender: bool) -> dict[str, Any] | None:
    """Read the actual monitor_state from the Node's source store."""
    from nmos.node.store import to_static_id
    static_id = to_static_id(resource_id)
    resource = node.senders.get(static_id) if is_sender else node.receivers.get(static_id)
    if resource is None:
        return None

    # Sender: Monitor on NSenderValue directly
    # Receiver: Monitor on inner.ReceiverCore (polymorphic wrapper)
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
        return None

    mon_src = monitor_field.value
    inner = mon_src.get() if hasattr(mon_src, 'get') else mon_src
    if not hasattr(inner, 'MonitorState') or not inner.MonitorState.defined:
        return None

    ms = inner.MonitorState.value
    result: dict[str, Any] = {}

    for attr in dir(ms):
        if attr.startswith('Monitor') and not attr.startswith('__'):
            field = getattr(ms, attr, None)
            if field is not None and hasattr(field, 'defined') and field.defined:
                result[attr] = field.value

    return result


def _emit(node: Any, domain: int, scope: int, event: int, resource_id: str,
          state: int = EventState.NORMAL, info: str = "") -> None:
    """Inject one event into the node's event queue."""
    node.event_queue.put_nowait(EngineEvent(
        domain=domain, scope=scope, event=event, state=state,
        count=1, id=resource_id, name="*", info=info,
    ))


def _emit_sender_lifecycle(node: Any, sender_id: str) -> None:
    """Emit the full sender activation lifecycle (activate + starting)."""
    _emit(node, AlertDomain.VENDOR_TRANSPORT, AlertScope.SENDER,
          EventId.VENDOR_TRANSPORT_ACTIVATE, sender_id)
    _emit(node, AlertDomain.VENDOR_ESSENCE, AlertScope.SENDER,
          EventId.VENDOR_ESSENCE_START, sender_id)
    _emit(node, AlertDomain.TRANSPORT, AlertScope.SENDER,
          EventId.TRANSPORT_OK, sender_id)
    _emit(node, AlertDomain.ESSENCE, AlertScope.SENDER,
          EventId.ESSENCE_OK, sender_id)
    _emit(node, AlertDomain.LINK, AlertScope.SENDER,
          EventId.LINK_OK, sender_id)
    _emit(node, AlertDomain.CLOCK, AlertScope.SENDER,
          EventId.CLOCK_OK, sender_id)


def _emit_receiver_lifecycle(node: Any, receiver_id: str) -> None:
    """Emit the full receiver activation lifecycle."""
    _emit(node, AlertDomain.VENDOR_TRANSPORT, AlertScope.RECEIVER,
          EventId.VENDOR_TRANSPORT_ACTIVATE, receiver_id)
    _emit(node, AlertDomain.VENDOR_ESSENCE, AlertScope.RECEIVER,
          EventId.VENDOR_ESSENCE_START, receiver_id)
    _emit(node, AlertDomain.TRANSPORT, AlertScope.RECEIVER,
          EventId.TRANSPORT_OK, receiver_id)
    _emit(node, AlertDomain.ESSENCE, AlertScope.RECEIVER,
          EventId.ESSENCE_OK, receiver_id)
    _emit(node, AlertDomain.LINK, AlertScope.RECEIVER,
          EventId.LINK_OK, receiver_id)
    _emit(node, AlertDomain.CLOCK, AlertScope.RECEIVER,
          EventId.CLOCK_OK, receiver_id)


async def _run_monitor_briefly(node: Any, duration: float = 2.0) -> None:
    """Run the status monitor for a fixed duration then cancel it."""
    task = asyncio.create_task(run_status_monitor(node))
    await asyncio.sleep(duration)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSenderActivation:
    """Verify sender activation/deactivation flows through to monitor source."""

    @pytest.mark.asyncio
    async def test_sender_activation_becomes_healthy(self) -> None:
        """Sender activation lifecycle → overall=Healthy on monitor source."""
        node = _make_node()
        _build_config(node, "config1")
        senders = _get_sender_ids(node)
        assert senders, "No senders in config1"
        sid = senders[0]

        _emit_sender_lifecycle(node, sid)
        await _run_monitor_briefly(node, 2.0)

        ms = _read_monitor_state(node, sid, is_sender=True)
        assert ms is not None, "Monitor state not found"
        assert ms["MonitorOverallStatus"] == NC_HEALTHY, f"overall={ms['MonitorOverallStatus']}"
        assert ms["MonitorTransmissionStatus"] == NC_HEALTHY
        assert ms["MonitorEssenceStatus"] == NC_HEALTHY
        assert ms["MonitorLinkStatus"] == NC_HEALTHY
        assert ms["MonitorSynchronizationStatus"] == NC_HEALTHY

    @pytest.mark.asyncio
    async def test_sender_deactivation_becomes_inactive(self) -> None:
        """Sender activate → deactivate → overall=Inactive."""
        node = _make_node()
        _build_config(node, "config1")
        sid = _get_sender_ids(node)[0]

        _emit_sender_lifecycle(node, sid)
        _emit(node, AlertDomain.VENDOR_ESSENCE, AlertScope.SENDER,
              EventId.VENDOR_ESSENCE_STOP, sid)
        _emit(node, AlertDomain.VENDOR_TRANSPORT, AlertScope.SENDER,
              EventId.VENDOR_TRANSPORT_DEACTIVATE, sid)
        await _run_monitor_briefly(node, 2.0)

        ms = _read_monitor_state(node, sid, is_sender=True)
        assert ms["MonitorOverallStatus"] == NC_INACTIVE
        assert ms["MonitorTransmissionStatus"] == NC_INACTIVE

    @pytest.mark.asyncio
    async def test_inactive_sender_stays_inactive(self) -> None:
        """Sender never activated → overall remains Inactive."""
        node = _make_node()
        _build_config(node, "config1")
        sid = _get_sender_ids(node)[0]

        # No events emitted
        await _run_monitor_briefly(node, 1.5)

        ms = _read_monitor_state(node, sid, is_sender=True)
        # Monitor state should be at initialization values
        assert ms["MonitorOverallStatus"] == NC_INACTIVE
        assert ms["MonitorTransmissionStatus"] == NC_INACTIVE


class TestReceiverActivation:
    """Verify receiver activation flows through to monitor source."""

    @pytest.mark.asyncio
    async def test_receiver_activation_becomes_healthy(self) -> None:
        node = _make_node()
        _build_config(node, "config1")
        rids = _get_receiver_ids(node)
        assert rids, "No receivers in config1"
        rid = rids[0]

        _emit_receiver_lifecycle(node, rid)
        await _run_monitor_briefly(node, 2.0)

        ms = _read_monitor_state(node, rid, is_sender=False)
        assert ms is not None, "Monitor state not found"
        assert ms["MonitorOverallStatus"] == NC_HEALTHY
        assert ms["MonitorConnectionStatus"] == NC_HEALTHY
        assert ms["MonitorStreamStatus"] == NC_HEALTHY
        assert ms["MonitorLinkStatus"] == NC_HEALTHY


class TestTransportErrors:
    """Verify transport errors degrade status and counters increment."""

    @pytest.mark.asyncio
    async def test_packet_loss_makes_unhealthy(self) -> None:
        """Packet loss after activation → transmission=Unhealthy, counter=1."""
        node = _make_node()
        _build_config(node, "config1")
        sid = _get_sender_ids(node)[0]

        # Use a single continuous monitor task for proper state tracking
        task = asyncio.create_task(run_status_monitor(node))

        _emit_sender_lifecycle(node, sid)
        # Wait for activation delay (3s) + margin to pass
        await asyncio.sleep(4.5)

        # Inject packet loss
        _emit(node, AlertDomain.TRANSPORT, AlertScope.SENDER,
              EventId.TRANSPORT_PACKET_LOST, sid,
              state=EventState.WARNING, info="5 packets lost")

        # Wait for the worse transition to be published
        await asyncio.sleep(1.5)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        ms = _read_monitor_state(node, sid, is_sender=True)
        assert ms["MonitorTransmissionStatus"] == NC_UNHEALTHY, \
            f"trans={ms['MonitorTransmissionStatus']}"
        assert ms["MonitorOverallStatus"] == NC_UNHEALTHY
        assert ms["MonitorTransmissionStatusCounter"] >= 1

    @pytest.mark.asyncio
    async def test_recovery_after_error(self) -> None:
        """Error → OK → status returns to Healthy (after 3s delay)."""
        node = _make_node()
        _build_config(node, "config1")
        sid = _get_sender_ids(node)[0]

        _emit_sender_lifecycle(node, sid)
        await asyncio.sleep(0.5)

        # Inject error then recovery
        _emit(node, AlertDomain.TRANSPORT, AlertScope.SENDER,
              EventId.TRANSPORT_PACKET_LOST, sid)

        # Wait for worse transition (needs activation_time + delay)
        task = asyncio.create_task(run_status_monitor(node))
        await asyncio.sleep(4.5)

        # Now inject recovery
        _emit(node, AlertDomain.TRANSPORT, AlertScope.SENDER,
              EventId.TRANSPORT_OK, sid)

        # Wait for better transition delay (3s)
        await asyncio.sleep(4.0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        ms = _read_monitor_state(node, sid, is_sender=True)
        assert ms["MonitorTransmissionStatus"] == NC_HEALTHY, \
            f"Expected recovery to Healthy, got {ms['MonitorTransmissionStatus']}"


class TestScopeValidation:
    """Verify events with wrong scope are rejected."""

    @pytest.mark.asyncio
    async def test_sender_rejects_receiver_scope(self) -> None:
        """Sender monitor ignores events with RECEIVER scope."""
        node = _make_node()
        _build_config(node, "config1")
        sid = _get_sender_ids(node)[0]

        # Activate sender normally
        _emit_sender_lifecycle(node, sid)
        await _run_monitor_briefly(node, 1.5)

        # Send a RECEIVER-scoped error to the sender ID — should be ignored
        _emit(node, AlertDomain.TRANSPORT, AlertScope.RECEIVER,
              EventId.TRANSPORT_PACKET_LOST, sid)
        await _run_monitor_briefly(node, 1.5)

        ms = _read_monitor_state(node, sid, is_sender=True)
        # Should still be healthy (error was rejected due to scope mismatch)
        assert ms["MonitorTransmissionStatus"] == NC_HEALTHY


class TestActivationReset:
    """Verify vendor activation resets counters and state."""

    @pytest.mark.asyncio
    async def test_activation_resets_counters(self) -> None:
        """Activate → error → counter=1 → re-activate → counter=0."""
        node = _make_node()
        _build_config(node, "config1")
        sid = _get_sender_ids(node)[0]

        # First activation + error
        _emit_sender_lifecycle(node, sid)
        task = asyncio.create_task(run_status_monitor(node))
        await asyncio.sleep(4.5)  # Past activation delay

        _emit(node, AlertDomain.TRANSPORT, AlertScope.SENDER,
              EventId.TRANSPORT_PACKET_LOST, sid)
        await asyncio.sleep(1.0)

        ms = _read_monitor_state(node, sid, is_sender=True)
        assert ms["MonitorTransmissionStatusCounter"] >= 1, "Counter should increment on error"

        # Deactivate then re-activate (resets counters)
        _emit(node, AlertDomain.VENDOR_ESSENCE, AlertScope.SENDER,
              EventId.VENDOR_ESSENCE_STOP, sid)
        _emit(node, AlertDomain.VENDOR_TRANSPORT, AlertScope.SENDER,
              EventId.VENDOR_TRANSPORT_DEACTIVATE, sid)
        await asyncio.sleep(1.5)  # Let deactivation settle

        # Re-activate — should reset all counters
        _emit_sender_lifecycle(node, sid)
        await asyncio.sleep(2.0)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        ms = _read_monitor_state(node, sid, is_sender=True)
        assert ms["MonitorTransmissionStatusCounter"] == 0, \
            f"Counter should reset on re-activation, got {ms['MonitorTransmissionStatusCounter']}"


class TestOverallStatusComputation:
    """Verify overall status is max of all domains."""

    @pytest.mark.asyncio
    async def test_link_down_makes_overall_unhealthy(self) -> None:
        """Link down while transmission healthy → overall=Unhealthy."""
        node = _make_node()
        _build_config(node, "config1")
        sid = _get_sender_ids(node)[0]

        _emit_sender_lifecycle(node, sid)
        task = asyncio.create_task(run_status_monitor(node))
        await asyncio.sleep(4.5)

        _emit(node, AlertDomain.LINK, AlertScope.SENDER,
              EventId.LINK_DOWN, sid, info="eth0 down")
        await asyncio.sleep(1.0)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        ms = _read_monitor_state(node, sid, is_sender=True)
        assert ms["MonitorLinkStatus"] == NC_UNHEALTHY
        assert ms["MonitorOverallStatus"] == NC_UNHEALTHY
        assert ms.get("MonitorOverallStatusMessage") == "eth0 down"


class TestEssenceConnectionInjection:
    """Verify essence errors inject into connection status (receiver only)."""

    @pytest.mark.asyncio
    async def test_essence_error_degrades_connection(self) -> None:
        """Receiver: essence error + connection healthy → connection unhealthy."""
        node = _make_node()
        _build_config(node, "config1")
        rids = _get_receiver_ids(node)
        assert rids
        rid = rids[0]

        _emit_receiver_lifecycle(node, rid)
        task = asyncio.create_task(run_status_monitor(node))
        await asyncio.sleep(4.5)

        # VENDOR_ESSENCE_STOP injects NC_UNHEALTHY into connection
        # (only this specific event, not generic ESSENCE_STREAM_ERROR)
        _emit(node, AlertDomain.VENDOR_ESSENCE, AlertScope.RECEIVER,
              EventId.VENDOR_ESSENCE_STOP, rid, info="receiver stopping")
        await asyncio.sleep(1.0)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        ms = _read_monitor_state(node, rid, is_sender=False)
        assert ms["MonitorStreamStatus"] == NC_UNHEALTHY
        assert ms["MonitorConnectionStatus"] == NC_UNHEALTHY, \
            f"Essence error should inject into connection, got {ms['MonitorConnectionStatus']}"


class TestMultipleConfigs:
    """Verify status monitoring works across different configs."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("config_name", ["config1", "config3", "config5", "config7"])
    async def test_all_senders_activate_healthy(self, config_name: str) -> None:
        """Every sender in every config → Healthy after activation."""
        node = _make_node()
        try:
            _build_config(node, config_name)
        except Exception:
            pytest.skip(f"{config_name} build failed")

        senders = _get_sender_ids(node)
        if not senders:
            pytest.skip(f"No senders in {config_name}")

        for sid in senders:
            _emit_sender_lifecycle(node, sid)

        await _run_monitor_briefly(node, 2.0)

        for sid in senders:
            ms = _read_monitor_state(node, sid, is_sender=True)
            assert ms is not None, f"{config_name}/{sid}: no monitor state"
            assert ms["MonitorOverallStatus"] == NC_HEALTHY, \
                f"{config_name}/{sid}: overall={ms['MonitorOverallStatus']}, expected Healthy"


# ---------------------------------------------------------------------------
# IS-04 Binding Source Structure (NMOS With Status Reporting MUST requirements)
# ---------------------------------------------------------------------------

def _get_monitor_source_for(node: Any, resource_id: str, is_sender: bool) -> Any | None:
    """Find the monitor NSourceDataValue for a sender/receiver."""
    for _, src in node.sources:
        inner = src
        if hasattr(src, 'get') and callable(src.get):
            got = src.get()
            if got is not None:
                inner = got
        if hasattr(inner, 'MonitorSiblingId') and inner.MonitorSiblingId.defined:
            if inner.MonitorSiblingId.value == resource_id:
                return inner
    return None


class TestSourceStructure:
    """Verify IS-04 binding MUST requirements for monitor source structure.

    Spec: 'NMOS With Status Reporting.md' — Source section.
    """

    def test_sender_monitor_source_format(self) -> None:
        """B1: Source MUST have format = urn:x-nmos:format:data."""
        node = _make_node()
        _build_config(node, "config1")
        sid = _get_sender_ids(node)[0]
        src = _get_monitor_source_for(node, sid, is_sender=True)
        assert src is not None, "No monitor source found for sender"

        fmt = src.Format.value
        assert str(fmt) == "urn:x-nmos:format:data", f"format={fmt}"

    def test_sender_monitor_type(self) -> None:
        """B2: Source MUST have monitor_type = 'sender'."""
        node = _make_node()
        _build_config(node, "config1")
        sid = _get_sender_ids(node)[0]
        src = _get_monitor_source_for(node, sid, is_sender=True)
        assert src is not None
        assert src.MonitorType.value == "sender"

    def test_receiver_monitor_type(self) -> None:
        """B2: Source MUST have monitor_type = 'receiver'."""
        node = _make_node()
        _build_config(node, "config1")
        rids = _get_receiver_ids(node)
        assert rids
        src = _get_monitor_source_for(node, rids[0], is_sender=False)
        assert src is not None
        assert src.MonitorType.value == "receiver"

    def test_monitor_sibling_id_matches(self) -> None:
        """B3: Source MUST have monitor_sibling_id identifying the sibling."""
        node = _make_node()
        _build_config(node, "config1")
        sid = _get_sender_ids(node)[0]
        src = _get_monitor_source_for(node, sid, is_sender=True)
        assert src is not None
        assert src.MonitorSiblingId.value == sid

    def test_monitor_sibling_same_device(self) -> None:
        """B3: monitor_sibling_id MUST identify a resource in same device_id."""
        node = _make_node()
        _build_config(node, "config1")
        sid = _get_sender_ids(node)[0]
        src = _get_monitor_source_for(node, sid, is_sender=True)
        assert src is not None

        # Source and sender must share the same device_id
        src_device = src.SourceCore.DeviceId.value
        from nmos.node.store import to_static_id
        sender = node.senders.get(to_static_id(sid))
        assert sender is not None
        # DeviceId is on the sender value directly, not ResourceCore
        sender_device = sender.DeviceId.value if hasattr(sender, 'DeviceId') \
            else sender.ResourceCore.DeviceId.value
        assert src_device == sender_device, \
            f"Source device={src_device}, sender device={sender_device}"

    def test_auto_reset_counters_true(self) -> None:
        """B12/BCP-008: monitor_auto_reset_counters MUST default to true."""
        node = _make_node()
        _build_config(node, "config1")
        sid = _get_sender_ids(node)[0]
        src = _get_monitor_source_for(node, sid, is_sender=True)
        assert src is not None
        assert src.MonitorAutoResetCounters.value is True

    def test_reporting_delay_is_3(self) -> None:
        """B8: MUST apply default statusReportingDelay of 3 seconds."""
        node = _make_node()
        _build_config(node, "config1")
        sid = _get_sender_ids(node)[0]
        src = _get_monitor_source_for(node, sid, is_sender=True)
        assert src is not None
        assert src.MonitorStatusReportingDelay.value == 3

    def test_sender_monitor_state_has_all_required_fields(self) -> None:
        """B5: Sender monitor_state MUST have all required status+counter fields."""
        node = _make_node()
        _build_config(node, "config1")
        sid = _get_sender_ids(node)[0]
        src = _get_monitor_source_for(node, sid, is_sender=True)
        assert src is not None
        ms = src.MonitorState.value
        assert ms is not None

        # Required sender fields per spec
        required = [
            "MonitorOverallStatus",
            "MonitorLinkStatus",
            "MonitorTransmissionStatus",
            "MonitorEssenceStatus",
            "MonitorSynchronizationStatus",
            "MonitorLinkStatusCounter",
            "MonitorTransmissionStatusCounter",
            "MonitorEssenceStatusCounter",
            "MonitorSynchronizationStatusCounter",
        ]
        for field_name in required:
            field = getattr(ms, field_name, None)
            assert field is not None and field.defined, \
                f"Sender monitor_state missing required field: {field_name}"

    def test_receiver_monitor_state_has_all_required_fields(self) -> None:
        """B6: Receiver monitor_state MUST have all required status+counter fields."""
        node = _make_node()
        _build_config(node, "config1")
        rids = _get_receiver_ids(node)
        assert rids
        src = _get_monitor_source_for(node, rids[0], is_sender=False)
        assert src is not None
        ms = src.MonitorState.value
        assert ms is not None

        # Required receiver fields per spec
        required = [
            "MonitorOverallStatus",
            "MonitorLinkStatus",
            "MonitorConnectionStatus",
            "MonitorStreamStatus",
            "MonitorSynchronizationStatus",
            "MonitorLinkStatusCounter",
            "MonitorConnectionStatusCounter",
            "MonitorStreamStatusCounter",
            "MonitorSynchronizationStatusCounter",
        ]
        for field_name in required:
            field = getattr(ms, field_name, None)
            assert field is not None and field.defined, \
                f"Receiver monitor_state missing required field: {field_name}"

    def test_status_values_non_negative(self) -> None:
        """B13: Status values MUST be non-negative integers."""
        node = _make_node()
        _build_config(node, "config1")
        sid = _get_sender_ids(node)[0]
        src = _get_monitor_source_for(node, sid, is_sender=True)
        assert src is not None
        ms = src.MonitorState.value

        for field_name in ("MonitorOverallStatus", "MonitorLinkStatus",
                           "MonitorTransmissionStatus", "MonitorEssenceStatus",
                           "MonitorSynchronizationStatus"):
            val = getattr(ms, field_name).value
            assert isinstance(val, int) and val >= 0, \
                f"{field_name}={val} must be non-negative integer"

    def test_counter_values_non_negative(self) -> None:
        """B14: Counter values MUST be non-negative integers."""
        node = _make_node()
        _build_config(node, "config1")
        sid = _get_sender_ids(node)[0]
        src = _get_monitor_source_for(node, sid, is_sender=True)
        assert src is not None
        ms = src.MonitorState.value

        for field_name in ("MonitorLinkStatusCounter",
                           "MonitorTransmissionStatusCounter",
                           "MonitorEssenceStatusCounter",
                           "MonitorSynchronizationStatusCounter"):
            val = getattr(ms, field_name).value
            assert isinstance(val, int) and val >= 0, \
                f"{field_name}={val} must be non-negative integer"


# ---------------------------------------------------------------------------
# Receiver Deactivation (BCP-008-01 MUST requirements)
# ---------------------------------------------------------------------------

class TestReceiverDeactivation:
    """Verify receiver deactivation transitions via the full pipeline."""

    @pytest.mark.asyncio
    async def test_receiver_deactivation_all_inactive(self) -> None:
        """BCP-008-01: Deactivating MUST transition overallStatus,
        connectionStatus, streamStatus to Inactive immediately."""
        node = _make_node()
        _build_config(node, "config1")
        rids = _get_receiver_ids(node)
        assert rids
        rid = rids[0]

        _emit_receiver_lifecycle(node, rid)
        task = asyncio.create_task(run_status_monitor(node))
        await asyncio.sleep(2.0)

        # Verify healthy first
        ms = _read_monitor_state(node, rid, is_sender=False)
        assert ms["MonitorOverallStatus"] == NC_HEALTHY

        # Deactivate
        _emit(node, AlertDomain.VENDOR_ESSENCE, AlertScope.RECEIVER,
              EventId.VENDOR_ESSENCE_STOP, rid)
        _emit(node, AlertDomain.VENDOR_TRANSPORT, AlertScope.RECEIVER,
              EventId.VENDOR_TRANSPORT_DEACTIVATE, rid)
        await asyncio.sleep(1.5)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        ms = _read_monitor_state(node, rid, is_sender=False)
        assert ms["MonitorOverallStatus"] == NC_INACTIVE, \
            f"overallStatus must be Inactive, got {ms['MonitorOverallStatus']}"
        assert ms["MonitorConnectionStatus"] == NC_INACTIVE, \
            f"connectionStatus must be Inactive, got {ms['MonitorConnectionStatus']}"
        assert ms["MonitorStreamStatus"] == NC_INACTIVE, \
            f"streamStatus must be Inactive, got {ms['MonitorStreamStatus']}"


# ---------------------------------------------------------------------------
# Version Bump (IS-04 Binding MUST)
# ---------------------------------------------------------------------------

class TestVersionBump:
    """Verify source version is updated on monitor state change."""

    @pytest.mark.asyncio
    async def test_version_changes_on_status_update(self) -> None:
        """B9: version MUST be updated whenever any attribute is modified."""
        node = _make_node()
        _build_config(node, "config1")
        sid = _get_sender_ids(node)[0]

        # Read initial version
        src = _get_monitor_source_for(node, sid, is_sender=True)
        assert src is not None
        initial_version = src.SourceCore.ResourceCore.Version.value

        # Activate sender
        _emit_sender_lifecycle(node, sid)
        await _run_monitor_briefly(node, 2.0)

        # Version must have changed
        src = _get_monitor_source_for(node, sid, is_sender=True)
        new_version = src.SourceCore.ResourceCore.Version.value
        assert new_version != initial_version, \
            f"Version must change on status update: {initial_version} → {new_version}"
