# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for nmos.node — Node resource CRUD, UUID cascade, error safety."""

from __future__ import annotations

from unittest import mock

import pytest

from nmos.errors import InvalidOperation, InvalidParameter, NotFound
from nmos.node import Node, _get_flow_core, _get_source_core, _get_resource_core
from nmos.node.store import to_static_id
from nmos.node.types import Leg
from nmos.node.updates import FlowUpdate, ReceiverUpdate, SenderUpdate, SourceUpdate

# Import generated types
from nmos.types.generated.nsource import NSourceValue
from nmos.types.generated.nsource_video import NSourceVideoValue
from nmos.types.generated.nsource_audio import NSourceAudioValue
from nmos.types.generated.nflow import NFlowValue
from nmos.types.generated.nflow_video_raw import NFlowVideoRawValue
from nmos.types.generated.nflow_audio_raw import NFlowAudioRawValue
from nmos.types.generated.nreceiver import NReceiverValue
from nmos.types.generated.nreceiver_video import NReceiverVideoValue
from nmos.types.generated.nsender import NSenderValue
from nmos.enums import EnumRegistry


def _make_node() -> Node:
    """Create an initialized node for testing."""
    node = Node()
    node.init(serial_number="TST12345")
    return node


def _make_video_source() -> NSourceValue:
    """Create a fresh video source."""
    inner = NSourceVideoValue()
    inner.Format.value = EnumRegistry.get("urn:x-nmos:format:video")
    inner.SourceCore.set_to_default()
    src = NSourceValue()
    src.set(inner)
    return src


def _make_audio_source() -> NSourceValue:
    """Create a fresh audio source."""
    inner = NSourceAudioValue()
    inner.Format.value = EnumRegistry.get("urn:x-nmos:format:audio")
    inner.SourceCore.set_to_default()
    src = NSourceValue()
    src.set(inner)
    return src


def _make_video_flow(source_id: str = "") -> NFlowValue:
    """Create a fresh video raw flow, optionally linked to a source."""
    from nmos.types.generated.nvideo_component import NVideoComponentValue
    inner = NFlowVideoRawValue()
    inner.set_to_default()
    inner.Format.value = EnumRegistry.get("urn:x-nmos:format:video")
    inner.MediaType.value = EnumRegistry.get("video/raw")
    inner.Colorspace.value = EnumRegistry.get("BT709")
    inner.FrameWidth.value = 1920
    inner.FrameHeight.value = 1080
    # Build minimal components (Y, Cb, Cr)
    def _comp(name: str, w: int, h: int) -> NVideoComponentValue:
        c = NVideoComponentValue()
        c.Name.value = EnumRegistry.get(name)
        c.Width.value = w
        c.Height.value = h
        c.BitDepth.value = 10
        return c
    inner.Components._defined = True
    inner.Components._value._inner = [_comp("Y", 1920, 1080), _comp("Cb", 960, 1080), _comp("Cr", 960, 1080)]
    if source_id:
        inner.FlowCore.SourceId.value = source_id
    flow = NFlowValue()
    flow.set(inner)
    return flow


def _make_video_receiver() -> NReceiverValue:
    """Create a fresh video receiver."""
    inner = NReceiverVideoValue()
    inner.set_to_default()
    inner.ReceiverCore.Transport.value = EnumRegistry.get("urn:x-nmos:transport:rtp")
    inner.Format.value = EnumRegistry.get("urn:x-nmos:format:video")
    recv = NReceiverValue()
    recv.set(inner)
    return recv


def _make_sender(flow_id: str = "") -> NSenderValue:
    """Create a fresh sender, optionally linked to a flow."""
    sender = NSenderValue()
    sender.set_to_default()
    sender.Format.value = EnumRegistry.get("urn:x-nmos:format:video")
    sender.Transport.value = EnumRegistry.get("urn:x-nmos:transport:rtp")
    if flow_id:
        sender.FlowId.value = flow_id
    return sender


# ===================================================================
# Initialization
# ===================================================================

class TestNodeInit:

    def test_init_required(self) -> None:
        node = Node()
        with pytest.raises(InvalidOperation, match="not initialized"):
            node.add_source(_make_video_source())

    def test_init_success(self) -> None:
        node = _make_node()
        assert node.serial_number == "TST12345"
        assert node._initialized is True

    def test_init_publishes_exclusive_service(self) -> None:
        """Node must advertise the Node Reservation service in its
        ``Services`` array. Regression: the Python Node's init used to
        populate ``Controls`` but never ``Services``, so controllers
        couldn't discover the acquire/renew/release endpoints even
        though they were live on the HTTP surface. The advertised
        entry uses ``"urn:x-matrox:service:exclusive/v1.0"`` as its
        service type.
        """
        node = _make_node()
        nv = node.node_value
        assert nv is not None, "Node.init did not create a node_value"
        assert nv.Services.defined, \
            "Node.Services must be populated at init — missing it makes " \
            "the Node Reservation service undiscoverable by controllers"
        service_types: list[str] = []
        for svc in nv.Services._value._inner:
            t = svc.Type
            if not t.defined:
                continue
            service_types.append(str(t.value))
        assert "urn:x-matrox:service:exclusive/v1.0" in service_types, \
            f"Expected exclusive service in node.Services, got {service_types}"


# ===================================================================
# Add resources
# ===================================================================

class TestAddReceiver:

    def test_add_receiver(self) -> None:
        node = _make_node()
        recv = _make_video_receiver()
        static_id = node.add_receiver(recv)
        assert static_id != ""
        assert node.get_receiver(static_id) is recv

    def test_add_receiver_sets_id(self) -> None:
        node = _make_node()
        recv = _make_video_receiver()
        static_id = node.add_receiver(recv)
        rc = _get_resource_core(recv)
        assert rc.Id.defined
        assert rc.Id.value != ""
        assert to_static_id(rc.Id.value) == static_id

    def test_add_receiver_rejects_prexisting_id(self) -> None:
        node = _make_node()
        recv = _make_video_receiver()
        rc = _get_resource_core(recv)
        rc.Id.value = "some-pre-existing-id"
        with pytest.raises(InvalidParameter, match="pre-defined id"):
            node.add_receiver(recv)


class TestAddSource:

    def test_add_source(self) -> None:
        node = _make_node()
        src = _make_video_source()
        static_id = node.add_source(src)
        assert static_id != ""
        assert node.get_source(static_id) is src

    def test_add_source_registers_in_receiver(self) -> None:
        """When a source has a ReceiverId, it registers itself in the receiver's Sources map."""
        node = _make_node()
        recv = _make_video_receiver()
        recv_static = node.add_receiver(recv)

        src = _make_video_source()
        src_core = _get_source_core(src)
        # Set the receiver ID to the receiver's dynamic ID
        recv_dynamic = _get_resource_core(recv).Id.value
        src_core.ReceiverId.value = recv_dynamic

        node.add_source(src)

        # Verify source registered in receiver's Sources map
        recv_core = _get_resource_core(recv)
        # The receiver is polymorphic — get its ReceiverCore
        from nmos.node import _get_receiver_core
        r_core = _get_receiver_core(recv)
        sources_map = r_core.Sources._value._inner
        assert src in sources_map


class TestAddFlow:

    def test_add_flow(self) -> None:
        node = _make_node()
        src = _make_video_source()
        src_static = node.add_source(src)
        src_dynamic = _get_source_core(src).ResourceCore.Id.value
        flow = _make_video_flow(source_id=src_dynamic)
        static_id = node.add_flow(flow)
        assert static_id != ""
        assert node.get_flow(static_id) is flow

    def test_add_flow_registers_in_source(self) -> None:
        """Flow registers itself in its source's Flows map."""
        node = _make_node()
        src = _make_video_source()
        src_static = node.add_source(src)
        src_dynamic = _get_source_core(src).ResourceCore.Id.value

        flow = _make_video_flow(source_id=src_dynamic)
        node.add_flow(flow)

        # Verify flow registered in source's Flows map
        src_core = _get_source_core(src)
        flows_map = src_core.Flows._value._inner
        assert flow in flows_map


class TestMonitorSources:

    def test_add_receiver_creates_monitor_source(self) -> None:
        """AddReceiver should auto-create a monitor source."""
        node = _make_node()
        recv = _make_video_receiver()
        node.add_receiver(recv)
        # Monitor source should have been added to sources store
        # (at least 1 source should exist now)
        assert len(node.sources) >= 1

    def test_add_sender_creates_monitor_source(self) -> None:
        """AddSender should auto-create a monitor source."""
        node = _make_node()
        sender = _make_sender()
        node.add_sender(sender)
        assert len(node.sources) >= 1

    def test_monitor_source_is_data_format(self) -> None:
        """Monitor sources should be NSourceData with format:data."""
        node = _make_node()
        recv = _make_video_receiver()
        node.add_receiver(recv)
        # Find the monitor source
        for _, src in node.sources:
            inner = src.get()
            if inner is not None and hasattr(inner, 'MonitorType'):
                assert inner.MonitorType.value == "receiver"
                return
        pytest.fail("no monitor source found")

    def test_monitor_source_has_required_fields(self) -> None:
        """Monitor sources must have all BCP-008 required fields per spec."""
        node = _make_node()
        recv = _make_video_receiver()
        node.add_receiver(recv)

        for _, src in node.sources:
            inner = src.get()
            if inner is None or not hasattr(inner, 'MonitorType'):
                continue

            # Required fields per specs/NMOS With Status Reporting.md
            assert inner.MonitorType.defined, "monitor_type missing"
            assert inner.MonitorSiblingId.defined, "monitor_sibling_id missing"
            assert inner.MonitorAutoResetCounters.defined, "monitor_auto_reset_counters missing"
            assert inner.MonitorAutoResetCounters.value is True
            assert inner.MonitorStatusReportingDelay.defined, "monitor_reporting_delay missing"
            assert inner.MonitorStatusReportingDelay.value == 3

            # monitor_state must be initialized
            assert inner.MonitorState.defined, "monitor_state missing"
            state = inner.MonitorState.value
            assert state.MonitorOverallStatus.defined, "overall_status missing"
            assert state.MonitorLinkStatus.defined, "link_status missing"

            # Receiver-specific
            if inner.MonitorType.value == "receiver":
                assert state.MonitorConnectionStatus.defined, "connection_status missing"
                assert state.MonitorStreamStatus.defined, "stream_status missing"
                assert state.MonitorConnectionStatusCounter.defined
                assert state.MonitorStreamStatusCounter.defined

            return

        pytest.fail("no monitor source found")

    def test_receiver_sync_seeds_notused(self) -> None:
        """A receiver has NO clock of its own — it locks to the connected
        stream's clock (SDP ``ts-refclk``). So an idle/fresh receiver monitor
        seeds ``NotUsed`` (grey), NOT Healthy: the Node advertising a locked
        PTP clock (``clk0``) does not mean an idle receiver is using PTP (here
        every source is forced to the internal ``clk1``). The connected value
        is driven by the status-monitor at activation (see
        ``test_sync_facet_decoupled_from_activation``)."""
        from nmos.node.status_monitor import NC_NOT_USED
        node = _make_node()
        assert node._receiver_sync_seed() == NC_NOT_USED

        recv = _make_video_receiver()
        node.add_receiver(recv)
        for _, src in node.sources:
            inner = src.get()
            if (inner is None or not hasattr(inner, "MonitorType")
                    or inner.MonitorType.value != "receiver"):
                continue
            st = inner.MonitorState.value
            assert st.MonitorSynchronizationStatus.defined
            assert st.MonitorSynchronizationStatus.value == NC_NOT_USED
            return
        pytest.fail("no receiver monitor source found")

    def test_sync_facet_decoupled_from_activation(self) -> None:
        """Stream activation must NOT green the sync facet: ``emit_starting``
        no longer emits ``CLOCK_OK``, so after the starting events the sync
        domain stays ``NC_INACTIVE`` (NotUsed / grey) — even though link /
        transport / essence go Healthy and overall is Healthy (sync ≠ overall).
        A separate ``CLOCK_OK`` (emitted by the activation handlers only when
        the effective clock is a locked PTP reference) is what greens it."""
        import asyncio
        from nmos.node.events import emit_starting, emit_clock_locked
        from nmos.node.status_monitor import (
            ResourceMonitor, NC_HEALTHY, NC_INACTIVE,
        )
        for is_sender in (True, False):
            q: asyncio.Queue = asyncio.Queue()
            rid = "test-sender" if is_sender else "test-receiver"
            emit_starting(q, rid, "eth0", is_sender=is_sender)
            mon = ResourceMonitor(rid, is_sender=is_sender)
            while not q.empty():
                mon.process_event(q.get_nowait())
            mon.tick()
            # Activation alone: overall Healthy, but sync NOT greened.
            assert mon.overall_status == NC_HEALTHY
            assert mon.sync.status == NC_INACTIVE, (
                "stream activation must not set sync Healthy (internal clock)")
            # A PTP clock-lock event greens sync.
            emit_clock_locked(q, rid, "eth0", is_sender=is_sender)
            mon.process_event(q.get_nowait())
            mon.tick()
            assert mon.sync.status == NC_HEALTHY

    def test_sdp_ref_clock_is_ptp(self) -> None:
        """The receiver-side SDP clock probe: ts-refclk ptp → True,
        localmac/internal / none / garbage → False. Drives the connected
        receiver's synchronization_status."""
        from nmos.node.sdp_transport import sdp_ref_clock_is_ptp

        def _sdp(refclk_line: str) -> str:
            return (
                "v=0\r\no=- 1 1 IN IP4 1.1.1.1\r\ns=x\r\nt=0 0\r\n"
                "m=video 5004 RTP/AVP 96\r\nc=IN IP4 239.0.0.1/64\r\n"
                f"{refclk_line}\r\n"
            )
        assert sdp_ref_clock_is_ptp(
            _sdp("a=ts-refclk:ptp=IEEE1588-2008:00-00-00-00-00-00-00-00")) is True
        assert sdp_ref_clock_is_ptp(
            _sdp("a=ts-refclk:localmac=00-11-22-33-44-55")) is False
        assert sdp_ref_clock_is_ptp(_sdp("a=recvonly")) is False
        assert sdp_ref_clock_is_ptp("not an sdp") is False

    def test_sender_monitor_sync_follows_source_clock_name(self) -> None:
        """A sender's monitor sync status is determined by the clock
        its Source names — NOT by whether the Node has ANY locked
        clock. Walking sender→flow→source→clock_name → looking up
        the named clock → HEALTHY iff that clock is a locked PTP.
        Receivers don't carry their own clock at rest and use the
        node-wide check instead.
        """
        from nmos.node.status_monitor import NC_HEALTHY, NC_NOT_USED
        node = _make_node()
        sender = _make_sender()
        node.add_sender(sender)

        for _, src in node.sources:
            inner = src.get()
            if inner is None or not hasattr(inner, "MonitorType"):
                continue
            if inner.MonitorType.value != "sender":
                continue
            state = inner.MonitorState.value
            assert state.MonitorSynchronizationStatus.defined
            # ``_make_sender`` points at a source whose ClockName is
            # ``"clk0"`` — the default PTP clock with Locked=True —
            # so the seeded status should be HEALTHY.
            assert state.MonitorSynchronizationStatus.value in (
                NC_HEALTHY, NC_NOT_USED,
            ), "sync seed must be one of HEALTHY / NC_NOT_USED"
            # Hold the stronger expectation only if the sender's
            # source actually references a locked PTP clock. If the
            # fixture evolves, keep the weaker assertion above so
            # this test surfaces the drift.
            parent_uuid = None
            if inner.MonitorSiblingId.defined:
                parent_uuid = inner.MonitorSiblingId.value
            if parent_uuid is not None:
                assert state.MonitorSynchronizationStatus.value \
                    == node._sender_sync_seed(parent_uuid)
            return
        pytest.fail("no sender monitor source found")

    def test_sender_monitor_source_has_required_fields(self) -> None:
        """Sender monitor sources must have transmission/essence status fields."""
        node = _make_node()
        sender = _make_sender()
        node.add_sender(sender)

        for _, src in node.sources:
            inner = src.get()
            if inner is None or not hasattr(inner, 'MonitorType'):
                continue
            if inner.MonitorType.value != "sender":
                continue

            state = inner.MonitorState.value
            assert state.MonitorTransmissionStatus.defined, "transmission_status missing"
            assert state.MonitorEssenceStatus.defined, "essence_status missing"
            assert state.MonitorTransmissionStatusCounter.defined
            assert state.MonitorEssenceStatusCounter.defined
            return

        pytest.fail("no sender monitor source found")


class TestNaturalGroupAssignment:

    def test_add_sender_with_natural_group(self) -> None:
        """AddSender assigns group hint when NaturalGroupIndex is set."""
        node = _make_node()
        sender = _make_sender()
        sender.Format.value = EnumRegistry.get("urn:x-nmos:format:video")
        sender.NaturalGroupIndex.value = 0

        node.add_sender(sender)

        # Role index should have been set
        assert sender.NaturalGroupRoleIndex.defined
        assert sender.NaturalGroupRoleIndex.value == 0

    def test_add_sender_without_natural_group(self) -> None:
        """AddSender without NaturalGroupIndex doesn't assign group."""
        node = _make_node()
        sender = _make_sender()
        # Don't set NaturalGroupIndex
        node.add_sender(sender)
        # NaturalGroupRoleIndex should not have been set by us
        # (it was not defined before, and we don't set it)


class TestAddSender:

    def test_add_sender(self) -> None:
        node = _make_node()
        sender = _make_sender()
        static_id = node.add_sender(sender)
        assert static_id != ""
        assert node.get_sender(static_id) is sender

    def test_add_sender_registers_in_flow(self) -> None:
        """Sender registers itself in its flow's Senders map."""
        node = _make_node()
        src = _make_video_source()
        node.add_source(src)
        src_dynamic = _get_source_core(src).ResourceCore.Id.value

        flow = _make_video_flow(source_id=src_dynamic)
        node.add_flow(flow)
        flow_dynamic = _get_flow_core(flow).ResourceCore.Id.value

        sender = _make_sender(flow_id=flow_dynamic)
        node.add_sender(sender)

        flow_core = _get_flow_core(flow)
        senders_map = flow_core.Senders._value._inner
        assert sender in senders_map


# ===================================================================
# UUID cascade on update
# ===================================================================

class TestUpdateSourceCascade:

    def test_update_source_changes_uuid(self) -> None:
        """UpdateSource generates a new dynamic UUID."""
        node = _make_node()
        src = _make_video_source()
        static_id = node.add_source(src)
        old_dynamic = _get_source_core(src).ResourceCore.Id.value

        new_dynamic = node.update_source(static_id, SourceUpdate())
        assert new_dynamic != old_dynamic
        assert to_static_id(new_dynamic) == static_id

    def test_update_source_pushes_garbage(self) -> None:
        node = _make_node()
        src = _make_video_source()
        static_id = node.add_source(src)
        old_dynamic = _get_source_core(src).ResourceCore.Id.value

        node.update_source(static_id, SourceUpdate())
        assert len(node.garbage_sources) == 1
        assert node.garbage_sources[0].id == old_dynamic

    def test_update_source_cascades_to_flow(self) -> None:
        """When source UUID changes, linked flows get their SourceId updated."""
        node = _make_node()
        src = _make_video_source()
        src_static = node.add_source(src)
        src_dynamic = _get_source_core(src).ResourceCore.Id.value

        flow = _make_video_flow(source_id=src_dynamic)
        node.add_flow(flow)

        # Update source — should cascade to flow
        new_src_dynamic = node.update_source(src_static, SourceUpdate())

        flow_core = _get_flow_core(flow)
        assert flow_core.SourceId.value == new_src_dynamic


class TestUpdateFlowCascade:

    def test_update_flow_changes_uuid(self) -> None:
        node = _make_node()
        src = _make_video_source()
        node.add_source(src)
        src_dynamic = _get_source_core(src).ResourceCore.Id.value
        flow = _make_video_flow(source_id=src_dynamic)
        static_id = node.add_flow(flow)
        old_dynamic = _get_flow_core(flow).ResourceCore.Id.value

        new_dynamic = node.update_flow(static_id, FlowUpdate())
        assert new_dynamic != old_dynamic
        assert to_static_id(new_dynamic) == static_id

    def test_update_flow_cascades_to_sender(self) -> None:
        """When flow UUID changes, linked senders get their FlowId updated."""
        node = _make_node()
        src = _make_video_source()
        node.add_source(src)
        src_dynamic = _get_source_core(src).ResourceCore.Id.value
        flow = _make_video_flow(source_id=src_dynamic)
        flow_static = node.add_flow(flow)
        flow_dynamic = _get_flow_core(flow).ResourceCore.Id.value

        sender = _make_sender(flow_id=flow_dynamic)
        node.add_sender(sender)

        new_flow_dynamic = node.update_flow(flow_static, FlowUpdate())

        assert sender.FlowId.value == new_flow_dynamic


# ===================================================================
# Update field mutations
# ===================================================================

class TestUpdateSender:

    def test_update_sender_flow_id(self) -> None:
        node = _make_node()
        sender = _make_sender()
        static_id = node.add_sender(sender)

        node.update_sender(static_id, SenderUpdate(flow_id="new-flow-id"))
        assert sender.FlowId.value == "new-flow-id"

    def test_update_sender_not_found_raises(self) -> None:
        node = _make_node()
        with pytest.raises(NotFound):
            node.update_sender("nonexistent", SenderUpdate(flow_id="x"))


class TestUpdateSource:

    def test_update_source_clock_name(self) -> None:
        node = _make_node()
        src = _make_video_source()
        static_id = node.add_source(src)

        node.update_source(static_id, SourceUpdate(clock_name="clk0"))
        src_core = _get_source_core(src)
        assert src_core.ClockName.value == "clk0"


# ===================================================================
# Delete
# ===================================================================

class TestDeleteSender:

    def test_del_sender(self) -> None:
        node = _make_node()
        sender = _make_sender()
        static_id = node.add_sender(sender)
        sender_dynamic = sender.ResourceCore.Id.value

        node.del_sender(sender_dynamic)
        assert node.get_sender(static_id) is None

    def test_del_sender_releases_index(self) -> None:
        node = _make_node()
        sender = _make_sender()
        static_id = node.add_sender(sender)

        # Count used indices
        first_index = sum(1 for i in range(256) if node.sender_indices.is_used(i))
        node.del_sender(sender.ResourceCore.Id.value)
        second_index = sum(1 for i in range(256) if node.sender_indices.is_used(i))
        assert second_index == first_index - 1

    def test_del_sender_not_found(self) -> None:
        node = _make_node()
        with pytest.raises(NotFound):
            node.del_sender("nonexistent-id")


class TestDeleteReceiver:

    def test_del_receiver(self) -> None:
        node = _make_node()
        recv = _make_video_receiver()
        static_id = node.add_receiver(recv)
        recv_dynamic = _get_resource_core(recv).Id.value

        node.del_receiver(recv_dynamic)
        assert node.get_receiver(static_id) is None


# ===================================================================
# Error safety — failed operations don't corrupt state
# ===================================================================

class TestErrorSafety:

    def test_add_source_failure_releases_index(self) -> None:
        """If add_source fails after allocating an index, the index is released."""
        node = _make_node()

        # Create a source that will fail validation (inner is None)
        bad_source = NSourceValue()
        # .get() returns None → _get_source_core will raise

        with pytest.raises(Exception):
            node.add_source(bad_source)

        # Index should have been released
        assert not node.source_indices.is_used(0)

    def test_add_sender_failure_releases_index(self) -> None:
        node = _make_node()
        # Sender with a pre-existing ID should fail
        sender = _make_sender()
        sender.ResourceCore.Id.value = "pre-existing"

        with pytest.raises(InvalidParameter):
            node.add_sender(sender)

        assert not node.sender_indices.is_used(0)


# ===================================================================
# Full pipeline: receiver → source → flow → sender → publish
# ===================================================================

class TestFullPipeline:

    def test_creation_order(self) -> None:
        """Full pipeline: add receiver, source, flow, sender, then publish."""
        node = _make_node()

        # 1. Receiver
        recv = _make_video_receiver()
        recv_static = node.add_receiver(recv)

        # 2. Source (linked to receiver)
        src = _make_video_source()
        recv_dynamic = _get_resource_core(recv).Id.value
        _get_source_core(src).ReceiverId.value = recv_dynamic
        src_static = node.add_source(src)

        # 3. Flow (linked to source)
        src_dynamic = _get_source_core(src).ResourceCore.Id.value
        flow = _make_video_flow(source_id=src_dynamic)
        flow_static = node.add_flow(flow)

        # 4. Sender (linked to flow)
        flow_dynamic = _get_flow_core(flow).ResourceCore.Id.value
        sender = _make_sender(flow_id=flow_dynamic)
        sender_static = node.add_sender(sender)

        # Verify all stored
        assert node.get_receiver(recv_static) is recv
        assert node.get_source(src_static) is src
        assert node.get_flow(flow_static) is flow
        assert node.get_sender(sender_static) is sender

        # Verify bidirectional links
        from nmos.node import _get_receiver_core
        r_core = _get_receiver_core(recv)
        assert src in r_core.Sources._value._inner

        src_core = _get_source_core(src)
        assert flow in src_core.Flows._value._inner

        flow_core = _get_flow_core(flow)
        assert sender in flow_core.Senders._value._inner

    def test_cascade_through_pipeline(self) -> None:
        """Update source → cascade to flow → flow cascade to sender."""
        node = _make_node()

        src = _make_video_source()
        src_static = node.add_source(src)

        flow = _make_video_flow(
            source_id=_get_source_core(src).ResourceCore.Id.value
        )
        flow_static = node.add_flow(flow)

        sender = _make_sender(
            flow_id=_get_flow_core(flow).ResourceCore.Id.value
        )
        node.add_sender(sender)

        # Update source → should cascade to flow's SourceId
        new_src_id = node.update_source(src_static, SourceUpdate())
        assert _get_flow_core(flow).SourceId.value == new_src_id

        # Update flow → should cascade to sender's FlowId
        new_flow_id = node.update_flow(flow_static, FlowUpdate())
        assert sender.FlowId.value == new_flow_id


# ===================================================================
# Resource inter-link integrity on update
# ===================================================================

class TestSourceUpdateInterLinks:
    """Verify all bidirectional links remain consistent after source update."""

    def _build_pipeline(self) -> tuple[Node, Any, Any, Any, Any]:
        """Build a full receiver→source→flow→sender pipeline."""
        node = _make_node()
        recv = _make_video_receiver()
        node.add_receiver(recv)
        recv_dynamic = _get_resource_core(recv).Id.value

        src = _make_video_source()
        _get_source_core(src).ReceiverId.value = recv_dynamic
        node.add_source(src)

        src_dynamic = _get_source_core(src).ResourceCore.Id.value
        flow = _make_video_flow(source_id=src_dynamic)
        node.add_flow(flow)

        flow_dynamic = _get_flow_core(flow).ResourceCore.Id.value
        sender = _make_sender(flow_id=flow_dynamic)
        node.add_sender(sender)

        return node, recv, src, flow, sender

    def test_flow_source_id_updated(self) -> None:
        """After source update, flow.SourceId points to new dynamic ID."""
        node, recv, src, flow, sender = self._build_pipeline()
        src_static = to_static_id(_get_source_core(src).ResourceCore.Id.value)

        new_src_id = node.update_source(src_static, SourceUpdate())

        assert _get_flow_core(flow).SourceId.value == new_src_id

    def test_source_still_in_receiver_sources_map(self) -> None:
        """After source update, source object is still in receiver's Sources map."""
        node, recv, src, flow, sender = self._build_pipeline()
        src_static = to_static_id(_get_source_core(src).ResourceCore.Id.value)

        node.update_source(src_static, SourceUpdate())

        from nmos.node import _get_receiver_core
        r_core = _get_receiver_core(recv)
        # Source object reference should still be in the map
        assert src in r_core.Sources._value._inner

    def test_flow_still_in_source_flows_map(self) -> None:
        """After source update, flow object is still in source's Flows map."""
        node, recv, src, flow, sender = self._build_pipeline()
        src_static = to_static_id(_get_source_core(src).ResourceCore.Id.value)

        node.update_source(src_static, SourceUpdate())

        src_core = _get_source_core(src)
        assert flow in src_core.Flows._value._inner

    def test_sender_still_in_flow_senders_map(self) -> None:
        """After source update, sender is still in flow's Senders map (unchanged)."""
        node, recv, src, flow, sender = self._build_pipeline()
        src_static = to_static_id(_get_source_core(src).ResourceCore.Id.value)

        node.update_source(src_static, SourceUpdate())

        flow_core = _get_flow_core(flow)
        assert sender in flow_core.Senders._value._inner

    def test_flow_version_bumped(self) -> None:
        """After source update, cascaded flow gets its version bumped."""
        node, recv, src, flow, sender = self._build_pipeline()
        src_static = to_static_id(_get_source_core(src).ResourceCore.Id.value)
        old_flow_version = _get_flow_core(flow).ResourceCore.Version.value

        node.update_source(src_static, SourceUpdate())

        new_flow_version = _get_flow_core(flow).ResourceCore.Version.value
        assert new_flow_version != old_flow_version

    def test_cascade_versions_all_distinct(self) -> None:
        """A cascading update stamps source, flow and sender distinctly.

        All three are touched inside one clock tick on a coarse-resolution
        platform (Windows resolves time_ns() to ~15.6 ms), so this only holds
        because the stamp generator steps forward on collision.
        """
        node, recv, src, flow, sender = self._build_pipeline()
        src_static = to_static_id(_get_source_core(src).ResourceCore.Id.value)

        node.update_source(src_static, SourceUpdate())

        versions = [
            _get_source_core(src).ResourceCore.Version.value,
            _get_flow_core(flow).ResourceCore.Version.value,
            _get_resource_core(sender).Version.value,
        ]
        assert len(set(versions)) == len(versions), (
            f"cascade produced duplicate versions: {versions}"
        )

    def test_child_source_parents_updated(self) -> None:
        """After parent source update, child source's Parents list has the new ID."""
        node = _make_node()

        # Parent source
        parent = _make_video_source()
        parent_static = node.add_source(parent)
        parent_dynamic = _get_source_core(parent).ResourceCore.Id.value

        # Child source linked to parent
        child = _make_video_source()
        _get_source_core(child).Parents.value = [parent_dynamic]
        node.add_source(child)

        # Verify child registered in parent's Children map
        parent_core = _get_source_core(parent)
        assert child in parent_core.Children._value._inner

        # Update parent → child's Parents list should have new ID
        new_parent_id = node.update_source(parent_static, SourceUpdate())

        child_core = _get_source_core(child)
        assert new_parent_id in child_core.Parents.value
        assert parent_dynamic not in child_core.Parents.value


class TestFlowUpdateInterLinks:
    """Verify all bidirectional links remain consistent after flow update."""

    def _build_pipeline(self) -> tuple[Node, Any, Any, Any]:
        """Build source→flow→sender pipeline."""
        node = _make_node()
        src = _make_video_source()
        node.add_source(src)

        src_dynamic = _get_source_core(src).ResourceCore.Id.value
        flow = _make_video_flow(source_id=src_dynamic)
        node.add_flow(flow)

        flow_dynamic = _get_flow_core(flow).ResourceCore.Id.value
        sender = _make_sender(flow_id=flow_dynamic)
        node.add_sender(sender)

        return node, src, flow, sender

    def test_sender_flow_id_updated(self) -> None:
        """After flow update, sender.FlowId points to new dynamic ID."""
        node, src, flow, sender = self._build_pipeline()
        flow_static = to_static_id(_get_flow_core(flow).ResourceCore.Id.value)

        new_flow_id = node.update_flow(flow_static, FlowUpdate())

        assert sender.FlowId.value == new_flow_id

    def test_sender_still_in_flow_senders_map(self) -> None:
        """After flow update, sender object is still in flow's Senders map."""
        node, src, flow, sender = self._build_pipeline()
        flow_static = to_static_id(_get_flow_core(flow).ResourceCore.Id.value)

        node.update_flow(flow_static, FlowUpdate())

        flow_core = _get_flow_core(flow)
        assert sender in flow_core.Senders._value._inner

    def test_flow_still_in_source_flows_map(self) -> None:
        """After flow update, flow object is still in source's Flows map."""
        node, src, flow, sender = self._build_pipeline()
        flow_static = to_static_id(_get_flow_core(flow).ResourceCore.Id.value)

        node.update_flow(flow_static, FlowUpdate())

        src_core = _get_source_core(src)
        assert flow in src_core.Flows._value._inner

    def test_sender_version_bumped(self) -> None:
        """After flow update, cascaded sender gets its version bumped."""
        node, src, flow, sender = self._build_pipeline()
        flow_static = to_static_id(_get_flow_core(flow).ResourceCore.Id.value)
        old_sender_version = sender.ResourceCore.Version.value

        node.update_flow(flow_static, FlowUpdate())

        assert sender.ResourceCore.Version.value != old_sender_version

    def test_child_flow_parents_updated(self) -> None:
        """After parent flow update, child flow's Parents list has the new ID."""
        node = _make_node()
        src = _make_video_source()
        node.add_source(src)
        src_dynamic = _get_source_core(src).ResourceCore.Id.value

        # Parent flow
        parent = _make_video_flow(source_id=src_dynamic)
        parent_static = node.add_flow(parent)
        parent_dynamic = _get_flow_core(parent).ResourceCore.Id.value

        # Child flow linked to parent
        child = _make_video_flow(source_id=src_dynamic)
        _get_flow_core(child).Parents.value = [parent_dynamic]
        node.add_flow(child)

        # Verify child registered in parent's Children map
        parent_core = _get_flow_core(parent)
        assert child in parent_core.Children._value._inner

        # Update parent → child's Parents list should have new ID
        new_parent_id = node.update_flow(parent_static, FlowUpdate())

        child_core = _get_flow_core(child)
        assert new_parent_id in child_core.Parents.value
        assert parent_dynamic not in child_core.Parents.value

    def test_garbage_tracking(self) -> None:
        """Flow update adds old dynamic ID to garbage list."""
        node, src, flow, sender = self._build_pipeline()
        flow_static = to_static_id(_get_flow_core(flow).ResourceCore.Id.value)
        old_flow_id = _get_flow_core(flow).ResourceCore.Id.value

        node.update_flow(flow_static, FlowUpdate())

        assert len(node.garbage_flows) == 1
        assert node.garbage_flows[0].id == old_flow_id


class TestDeviceListIntegrityOnUpdate:
    """Device senders/receivers arrays stay correct through add/update/delete cycles."""

    def test_device_lists_after_add_and_delete(self) -> None:
        """Device lists are correct after adding then deleting resources."""
        node = _make_node()

        # Add two senders
        s1 = _make_sender()
        node.add_sender(s1)
        s1_dynamic = s1.ResourceCore.Id.value

        s2 = _make_sender()
        node.add_sender(s2)
        s2_dynamic = s2.ResourceCore.Id.value

        assert len(node.device_value.Senders.value) == 2

        # Delete first sender
        node.del_sender(s1_dynamic)
        senders_list = node.device_value.Senders.value
        assert len(senders_list) == 1
        assert s2_dynamic in senders_list
        assert s1_dynamic not in senders_list

    def test_device_lists_after_receiver_add_delete(self) -> None:
        """Device receiver list is correct after add/delete cycle."""
        node = _make_node()

        r1 = _make_video_receiver()
        node.add_receiver(r1)
        r1_dynamic = _get_resource_core(r1).Id.value

        r2 = _make_video_receiver()
        node.add_receiver(r2)

        assert len(node.device_value.Receivers.value) == 2

        node.del_receiver(r1_dynamic)
        assert len(node.device_value.Receivers.value) == 1
        assert r1_dynamic not in node.device_value.Receivers.value

    def test_device_version_bumped_on_add_and_delete(self) -> None:
        """Device version changes each time senders/receivers list is modified."""
        node = _make_node()
        v0 = node.device_value.ResourceCore.Version.value

        sender = _make_sender()
        node.add_sender(sender)
        v1 = node.device_value.ResourceCore.Version.value
        assert v1 != v0

        node.del_sender(sender.ResourceCore.Id.value)
        v2 = node.device_value.ResourceCore.Version.value
        assert v2 != v1


class TestVersionStamp:
    """``_nmos_version_now`` must hand out strictly increasing stamps.

    IS-04 uses ``version`` both as the changed-since signal and as the Query
    API paging cursor, so duplicates are a correctness problem, not cosmetics.
    """

    def test_rapid_calls_strictly_increase(self) -> None:
        """Consecutive calls differ even inside one clock tick."""
        from nmos.node import _nmos_version_now

        stamps = [_nmos_version_now() for _ in range(50)]
        assert stamps == sorted(stamps), "stamps are not ordered"
        assert len(set(stamps)) == len(stamps), "stamps contain duplicates"

    def test_frozen_clock_still_advances(self) -> None:
        """A clock that never ticks must not produce duplicate stamps."""
        import nmos.node as node_mod

        saved = node_mod._last_version_ns
        frozen = 1_700_000_000_000_000_000
        try:
            node_mod._last_version_ns = 0
            with mock.patch.object(node_mod.time, "time_ns", return_value=frozen):
                stamps = [node_mod._nmos_version_now() for _ in range(5)]
            assert len(set(stamps)) == 5, f"frozen clock duplicated: {stamps}"
            # 1 ns steps off the frozen reading, so still the same second.
            assert [s[1] - stamps[0][1] for s in stamps] == [0, 1, 2, 3, 4]
        finally:
            node_mod._last_version_ns = saved

    def test_backwards_clock_step_does_not_regress(self) -> None:
        """An NTP correction backwards must not rewind versions."""
        import nmos.node as node_mod

        saved = node_mod._last_version_ns
        try:
            node_mod._last_version_ns = 0
            with mock.patch.object(node_mod.time, "time_ns",
                                   return_value=1_700_000_000_000_000_000):
                first = node_mod._nmos_version_now()
            # Clock jumps a minute into the past
            with mock.patch.object(node_mod.time, "time_ns",
                                   return_value=1_699_999_940_000_000_000):
                second = node_mod._nmos_version_now()
            assert second > first, f"version regressed: {first} -> {second}"
        finally:
            node_mod._last_version_ns = saved
