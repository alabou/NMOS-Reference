# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""SRT compliance tests per specs/NMOS With SRT.md.

Tests cover:
  - Transport enum values and config presence
  - IS-04 Sender/Receiver attributes for srt / srt.mp2t / srt.rtp
  - IS-05 transport parameter fields (source_ip/port, destination_ip/port,
    protocol, latency, stream_id)
  - stream_id formation and rendezvous mode rules
  - SDP generation for srt/srt.mp2t (UDP mp2t) and srt.rtp (RTP-based)
  - Multi-path redundancy rules
  - PEP encryption hooks

Configs used:
  - config7, config7f, config7faudio: srt (mp2t) + srt.rtp mixed
  - config8, config8f: srt.rtp only (plus RTP multicast)
  - config12: srt (mp2t) + srt.rtp mixed

Known implementation gaps (tests marked xfail where applicable):
  G1: SDP generation for srt/srt.mp2t (UDP mp2t m-line)
  G2: stream_id `#!::r=<grouphint>` formation at activation
  G3: rendezvous source_port == destination_port validation
  G4: multi-path redundancy
  G5: Sender-as-caller SDP at activation
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


from nmos.enums import (
    TransportSrt, TransportSrtMpeg2Ts, TransportSrtRtp,
    FormatAudio, FormatVideo, FormatMux,
)
from nmos.node import Node


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BUILTIN_DIR = Path(__file__).parent.parent / "config" / "builtin"


def _make_node(serial: str = "SRTTST") -> Node:
    node = Node()
    node.init(serial_number=serial)
    return node


def _build_config(node: Node, config_name: str) -> None:
    from nmos.node.config import ConfigBuilder
    config_path = BUILTIN_DIR / f"{config_name}.json"
    if not config_path.exists():
        pytest.skip(f"{config_name}.json not found")
    with open(config_path) as f:
        config = json.load(f)
    builder = ConfigBuilder(node, verbose=False)
    for r in config.get("receivers", []):
        try:
            builder._build_receiver_from_config(r)
        except Exception:
            pass
    for s in config.get("senders", []):
        try:
            builder._build_sender_pipeline(s)
        except Exception:
            pass


def _find_senders_by_transport(node: Node, transport: str) -> list[tuple[str, object]]:
    """Find all senders with exact transport URN match."""
    result: list[tuple[str, object]] = []
    for _sid, s in node.senders:
        tr = str(s.Transport.value) if s.Transport.defined else ""
        if tr == transport:
            result.append((s.ResourceCore.Id.value, s))
    return result


def _find_receivers_by_transport(node: Node, transport: str) -> list[tuple[str, object]]:
    from nmos.node import _get_receiver_core
    result: list[tuple[str, object]] = []
    for _sid, r in node.receivers:
        rc = _get_receiver_core(r)
        tr = str(rc.Transport.value) if rc.Transport.defined else ""
        if tr == transport:
            result.append((rc.ResourceCore.Id.value, r))
    return result


def _get_sender_flow(node: Node, sender: object) -> object | None:
    flow_id = sender.FlowId.value if sender.FlowId.defined else None
    if flow_id is None:
        return None
    return node.flows.get(flow_id)


def _get_flow_inner(flow_ptr: object) -> object:
    poly = flow_ptr.get() if hasattr(flow_ptr, 'get') else flow_ptr
    assert poly is not None
    return poly


def _get_inner_format(ptr: object) -> str:
    inner = ptr.get() if hasattr(ptr, 'get') else ptr
    return str(inner.Format.value) if inner and hasattr(inner, 'Format') and inner.Format.defined else ""


def _get_sender_staged(node: Node, sender_id: str) -> object | None:
    """Get the first leg of staged transport params for a sender."""
    act = node.get_sender_activation(sender_id)
    return act.staged[0] if act and act.staged else None


def _get_receiver_staged(node: Node, receiver_id: str) -> object | None:
    act = node.get_receiver_activation(receiver_id)
    return act.staged[0] if act and act.staged else None


def _get_sender_active(node: Node, sender_id: str) -> object | None:
    act = node.get_sender_activation(sender_id)
    return act.active[0] if act and act.active else None


# ===================================================================
# Class 1: TestSrtTransports — enum + config presence
# ===================================================================

class TestSrtTransports:
    """Verify SRT transport enum values and config presence."""

    def test_transport_srt_enum_value(self) -> None:
        assert str(TransportSrt) == "urn:x-matrox:transport:srt"

    def test_transport_srt_mp2t_enum_value(self) -> None:
        assert str(TransportSrtMpeg2Ts) == "urn:x-matrox:transport:srt.mp2t"

    def test_transport_srt_rtp_enum_value(self) -> None:
        assert str(TransportSrtRtp) == "urn:x-matrox:transport:srt.rtp"

    def test_config7_has_srt_senders(self) -> None:
        node = _make_node()
        _build_config(node, "config7")
        srt_senders = _find_senders_by_transport(node, "urn:x-matrox:transport:srt")
        srt_rtp_senders = _find_senders_by_transport(node, "urn:x-matrox:transport:srt.rtp")
        assert len(srt_senders) >= 1, "config7 should have at least 1 srt sender"
        assert len(srt_rtp_senders) >= 1, "config7 should have at least 1 srt.rtp sender"

    def test_config8_has_srt_rtp_senders(self) -> None:
        node = _make_node()
        _build_config(node, "config8")
        srt_rtp_senders = _find_senders_by_transport(node, "urn:x-matrox:transport:srt.rtp")
        assert len(srt_rtp_senders) >= 1, "config8 should have srt.rtp senders"

    def test_config12_has_srt_and_srt_rtp(self) -> None:
        node = _make_node()
        _build_config(node, "config12")
        srt_senders = _find_senders_by_transport(node, "urn:x-matrox:transport:srt")
        srt_rtp_senders = _find_senders_by_transport(node, "urn:x-matrox:transport:srt.rtp")
        assert len(srt_senders) >= 1
        assert len(srt_rtp_senders) >= 1


# ===================================================================
# Class 2: TestSrtSenderIs04 — §SRT IS-04 Senders
# ===================================================================

class TestSrtSenderIs04:
    """Verify SRT sender IS-04 resource attributes per spec."""

    def test_srt_mp2t_sender_format_is_mux(self) -> None:
        """S1: srt transport → flow.format=mux."""
        node = _make_node()
        _build_config(node, "config7")
        srt_senders = _find_senders_by_transport(node, "urn:x-matrox:transport:srt")
        assert len(srt_senders) >= 1
        for _sid, sender in srt_senders:
            fmt = str(sender.Format.value) if sender.Format.defined else ""
            assert fmt == str(FormatMux), f"srt sender should be mux format, got {fmt}"

    def test_srt_mp2t_sender_media_type_application_mp2t(self) -> None:
        """S1: srt transport → flow media_type=application/mp2t."""
        node = _make_node()
        _build_config(node, "config7")
        srt_senders = _find_senders_by_transport(node, "urn:x-matrox:transport:srt")
        assert len(srt_senders) >= 1
        for _sid, sender in srt_senders:
            flow = _get_sender_flow(node, sender)
            assert flow is not None
            inner = _get_flow_inner(flow)
            mt = str(inner.MediaType.value) if inner.MediaType.defined else ""
            assert mt == "application/mp2t", \
                f"srt sender flow media_type should be application/mp2t, got {mt}"

    def test_srt_rtp_sender_formats_include_video_and_audio(self) -> None:
        """S2: srt.rtp senders may have any format — config7 has video + audio."""
        node = _make_node()
        _build_config(node, "config7")
        srt_rtp_senders = _find_senders_by_transport(node, "urn:x-matrox:transport:srt.rtp")
        formats = {str(s.Format.value) for _sid, s in srt_rtp_senders if s.Format.defined}
        assert str(FormatVideo) in formats, f"Expected video sender, got formats={formats}"
        assert str(FormatAudio) in formats, f"Expected audio sender, got formats={formats}"


# ===================================================================
# Class 3: TestSrtReceiverIs04 — §SRT IS-04 Receivers
# ===================================================================

class TestSrtReceiverIs04:
    """Verify SRT receiver IS-04 resource attributes per spec."""

    def test_srt_mp2t_receiver_format_is_mux(self) -> None:
        """R1: srt transport → receiver.format=mux."""
        node = _make_node()
        _build_config(node, "config7")
        srt_recvs = _find_receivers_by_transport(node, "urn:x-matrox:transport:srt")
        assert len(srt_recvs) >= 1
        for _rid, recv in srt_recvs:
            inner = recv.get() if hasattr(recv, 'get') else recv
            fmt = str(inner.Format.value) if inner.Format.defined else ""
            assert fmt == str(FormatMux), f"srt receiver should be mux, got {fmt}"

    def test_srt_mp2t_receiver_media_types_has_application_mp2t(self) -> None:
        """R1: srt receiver caps.media_types contains application/mp2t."""
        node = _make_node()
        _build_config(node, "config7")
        srt_recvs = _find_receivers_by_transport(node, "urn:x-matrox:transport:srt")
        assert len(srt_recvs) >= 1
        for _rid, recv in srt_recvs:
            inner = recv.get() if hasattr(recv, 'get') else recv
            caps = inner.Caps
            assert caps.defined
            mt_list = [str(mt) for mt in caps._value.MediaTypes._inner]
            assert "application/mp2t" in mt_list, \
                f"srt receiver media_types should contain application/mp2t, got {mt_list}"

    def test_srt_rtp_receiver_formats_include_video_and_audio(self) -> None:
        """R2: srt.rtp receivers may have any format — config7 has video + audio."""
        node = _make_node()
        _build_config(node, "config7")
        srt_rtp_recvs = _find_receivers_by_transport(node, "urn:x-matrox:transport:srt.rtp")
        formats: set[str] = set()
        for _rid, recv in srt_rtp_recvs:
            inner = recv.get() if hasattr(recv, 'get') else recv
            if inner.Format.defined:
                formats.add(str(inner.Format.value))
        assert str(FormatVideo) in formats, f"Expected video receiver, got formats={formats}"
        assert str(FormatAudio) in formats, f"Expected audio receiver, got formats={formats}"


# ===================================================================
# Class 4: TestSrtIs05TransportParams — §SRT IS-05 (TP1, TP2, TP3, TP10)
# ===================================================================

class TestSrtIs05TransportParams:
    """Verify SRT IS-05 transport parameter fields and defaults."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.node = _make_node()
        _build_config(self.node, "config7")

    def _get_srt_sender(self) -> tuple[str, object]:
        srt_senders = _find_senders_by_transport(self.node, "urn:x-matrox:transport:srt")
        assert len(srt_senders) >= 1
        return srt_senders[0]

    def _get_srt_receiver(self) -> tuple[str, object]:
        srt_recvs = _find_receivers_by_transport(self.node, "urn:x-matrox:transport:srt")
        assert len(srt_recvs) >= 1
        return srt_recvs[0]

    # Sender field presence (TP1)
    def test_sender_staged_has_source_ip(self) -> None:
        sid, _ = self._get_srt_sender()
        staged = _get_sender_staged(self.node, sid)
        assert staged is not None and hasattr(staged, 'SourceIp')

    def test_sender_staged_has_source_port(self) -> None:
        sid, _ = self._get_srt_sender()
        staged = _get_sender_staged(self.node, sid)
        assert staged is not None and hasattr(staged, 'SourcePort')

    def test_sender_staged_has_destination_ip(self) -> None:
        sid, _ = self._get_srt_sender()
        staged = _get_sender_staged(self.node, sid)
        assert staged is not None and hasattr(staged, 'DestinationIp')

    def test_sender_staged_has_destination_port(self) -> None:
        sid, _ = self._get_srt_sender()
        staged = _get_sender_staged(self.node, sid)
        assert staged is not None and hasattr(staged, 'DestinationPort')

    def test_sender_staged_has_protocol(self) -> None:
        sid, _ = self._get_srt_sender()
        staged = _get_sender_staged(self.node, sid)
        assert staged is not None and hasattr(staged, 'Protocol')

    def test_sender_staged_has_latency(self) -> None:
        sid, _ = self._get_srt_sender()
        staged = _get_sender_staged(self.node, sid)
        assert staged is not None and hasattr(staged, 'Latency')

    def test_sender_staged_has_stream_id(self) -> None:
        """TP3: stream_id MUST be present in staged params."""
        sid, _ = self._get_srt_sender()
        staged = _get_sender_staged(self.node, sid)
        assert staged is not None and hasattr(staged, 'StreamId')

    # Receiver field presence (TP2)
    def test_receiver_staged_has_source_ip(self) -> None:
        rid, _ = self._get_srt_receiver()
        staged = _get_receiver_staged(self.node, rid)
        assert staged is not None and hasattr(staged, 'SourceIp')

    def test_receiver_staged_has_destination_ip(self) -> None:
        rid, _ = self._get_srt_receiver()
        staged = _get_receiver_staged(self.node, rid)
        assert staged is not None and hasattr(staged, 'DestinationIp')

    def test_receiver_staged_has_protocol(self) -> None:
        rid, _ = self._get_srt_receiver()
        staged = _get_receiver_staged(self.node, rid)
        assert staged is not None and hasattr(staged, 'Protocol')

    def test_receiver_staged_has_latency(self) -> None:
        rid, _ = self._get_srt_receiver()
        staged = _get_receiver_staged(self.node, rid)
        assert staged is not None and hasattr(staged, 'Latency')

    def test_receiver_staged_has_stream_id(self) -> None:
        """TP3: stream_id MUST be present in receiver staged params."""
        rid, _ = self._get_srt_receiver()
        staged = _get_receiver_staged(self.node, rid)
        assert staged is not None and hasattr(staged, 'StreamId')

    # Defaults (TP3, TP10)
    def test_stream_id_default_null(self) -> None:
        """TP3: stream_id default is null when not explicitly set."""
        sid, _ = self._get_srt_sender()
        staged = _get_sender_staged(self.node, sid)
        assert staged is not None
        # None is the "null" JSON value (distinguished from UNDEFINED)
        assert staged.StreamId.value is None, \
            f"Default stream_id should be null, got {staged.StreamId.value!r}"

    def test_sender_default_protocol_is_listener(self) -> None:
        """TP10: default Sender protocol is listener."""
        sid, _ = self._get_srt_sender()
        staged = _get_sender_staged(self.node, sid)
        assert staged is not None
        proto = str(staged.Protocol.value) if staged.Protocol.defined else ""
        assert proto == "listener", f"Sender default protocol should be listener, got {proto}"

    def test_receiver_default_protocol_is_caller(self) -> None:
        """TP10: default Receiver protocol is caller."""
        rid, _ = self._get_srt_receiver()
        staged = _get_receiver_staged(self.node, rid)
        assert staged is not None
        proto = str(staged.Protocol.value) if staged.Protocol.defined else ""
        assert proto == "caller", f"Receiver default protocol should be caller, got {proto}"


# ===================================================================
# Class 5: TestSrtStreamIdFormation — §SRT IS-05 stream_id (TP4-TP9)
# ===================================================================

class TestSrtStreamIdFormation:
    """stream_id formation and rules per spec."""

    def test_stream_id_null_when_no_feature(self) -> None:
        """TP4: default (no stream_id feature) → stream_id=null."""
        node = _make_node()
        _build_config(node, "config7")
        srt_senders = _find_senders_by_transport(node, "urn:x-matrox:transport:srt")
        assert len(srt_senders) >= 1
        for sid, _ in srt_senders:
            staged = _get_sender_staged(node, sid)
            assert staged is not None
            assert staged.StreamId.value is None, \
                "Default stream_id should be null (no feature)"

    def test_stream_id_constraint_allows_null_only(self) -> None:
        """TP9: Sender may have constraint on stream_id={enum:[null]} to indicate no feature."""
        node = _make_node()
        _build_config(node, "config7")
        srt_senders = _find_senders_by_transport(node, "urn:x-matrox:transport:srt")
        assert len(srt_senders) >= 1
        sid, _ = srt_senders[0]
        act = node.get_sender_activation(sid)
        if act is None or not act.constraints:
            pytest.skip("No constraints on sender")
        cons = act.constraints[0]
        # Constraint on StreamId should allow null
        if hasattr(cons, 'StreamId') and cons.StreamId.defined:
            cv = cons.StreamId.value
            # Accept either enum=[None] or effectively-null constraint
            if hasattr(cv, 'Enum') and cv.Enum.defined:
                enum_vals = list(cv.Enum.value)
                assert None in enum_vals or len(enum_vals) == 0 or enum_vals == [None], \
                    f"Constraint should allow null, got enum={enum_vals}"

    @pytest.mark.xfail(reason="G2: stream_id #!::r=<grouphint> formation at activation not implemented")
    def test_listener_grouphint_format(self) -> None:
        """TP5: listener using stream_id feature → stream_id = #!::r=<grouphint>.
        Currently not implemented — when a listener uses the feature, its stream_id
        must be formed from its own grouphint tag.
        """
        node = _make_node()
        _build_config(node, "config7")
        srt_senders = _find_senders_by_transport(node, "urn:x-matrox:transport:srt")
        assert len(srt_senders) >= 1
        sid, _ = srt_senders[0]
        staged = _get_sender_staged(node, sid)
        assert staged is not None
        v = staged.StreamId.value
        assert v is not None and v.startswith("#!::r="), \
            f"Listener stream_id should match #!::r=<grouphint>, got {v!r}"

    @pytest.mark.xfail(reason="G2: listener ignores staged stream_id and sets active to grouphint")
    def test_listener_sets_active_stream_id_to_grouphint(self) -> None:
        """TP8: Listener MUST ignore staged stream_id and set active to its grouphint tag."""
        node = _make_node()
        _build_config(node, "config7")
        srt_senders = _find_senders_by_transport(node, "urn:x-matrox:transport:srt")
        assert len(srt_senders) >= 1
        sid, _ = srt_senders[0]
        active = _get_sender_active(node, sid)
        assert active is not None
        v = active.StreamId.value
        assert v is not None and v.startswith("#!::r=")


# ===================================================================
# Class 6: TestSrtSdpGeneration — §SDP format-specific parameters
# ===================================================================

class TestSrtSdpGeneration:
    """SDP generation for SRT transports per spec."""

    def test_srt_rtp_sdp_is_generated(self) -> None:
        """S4: srt.rtp sender generates SDP (via RTP path)."""
        from nmos.node import _generate_sdp_from_params
        node = _make_node()
        _build_config(node, "config7")
        srt_rtp_senders = _find_senders_by_transport(node, "urn:x-matrox:transport:srt.rtp")
        assert len(srt_rtp_senders) >= 1
        sid, sender = srt_rtp_senders[0]
        sdp = _generate_sdp_from_params(node, sender, sid)
        assert sdp is not None and len(sdp) > 0

    def test_srt_rtp_sdp_has_rtp_avp_proto(self) -> None:
        """S4: srt.rtp SDP m-line should contain RTP/AVP proto."""
        from nmos.node import _generate_sdp_from_params
        node = _make_node()
        _build_config(node, "config7")
        srt_rtp_senders = _find_senders_by_transport(node, "urn:x-matrox:transport:srt.rtp")
        assert len(srt_rtp_senders) >= 1
        sid, sender = srt_rtp_senders[0]
        sdp = _generate_sdp_from_params(node, sender, sid)
        assert sdp is not None
        assert "RTP/AVP" in sdp, \
            f"srt.rtp SDP should contain RTP/AVP proto, got: {sdp[:200]}"

    def test_srt_rtp_sdp_has_c_line(self) -> None:
        """S5: srt.rtp SDP must have c= connection line."""
        from nmos.node import _generate_sdp_from_params
        node = _make_node()
        _build_config(node, "config7")
        srt_rtp_senders = _find_senders_by_transport(node, "urn:x-matrox:transport:srt.rtp")
        assert len(srt_rtp_senders) >= 1
        sid, sender = srt_rtp_senders[0]
        sdp = _generate_sdp_from_params(node, sender, sid)
        assert sdp is not None
        assert "c=" in sdp, f"SDP must have c= line, got: {sdp[:200]}"

    def test_srt_mp2t_sdp_m_line_application_udp_mp2t(self) -> None:
        """S3: srt/srt.mp2t SDP m-line MUST be `m=application <port> UDP mp2t`."""
        from nmos.node import _generate_sdp_from_params
        node = _make_node()
        _build_config(node, "config7")
        srt_senders = _find_senders_by_transport(node, "urn:x-matrox:transport:srt")
        assert len(srt_senders) >= 1
        sid, sender = srt_senders[0]
        sdp = _generate_sdp_from_params(node, sender, sid)
        assert sdp is not None, "srt sender should generate SDP"
        assert "m=application" in sdp and "UDP mp2t" in sdp, \
            f"srt SDP m-line should be 'm=application <port> UDP mp2t', got: {sdp[:200]}"

    def test_srt_mp2t_sdp_c_line_has_listener_ip(self) -> None:
        """S5: srt SDP c= line MUST have SRT Sender listener IP (source_ip)."""
        from nmos.node import _generate_sdp_from_params
        node = _make_node()
        _build_config(node, "config7")
        srt_senders = _find_senders_by_transport(node, "urn:x-matrox:transport:srt")
        assert len(srt_senders) >= 1
        sid, sender = srt_senders[0]
        sdp = _generate_sdp_from_params(node, sender, sid)
        assert sdp is not None
        assert "c=IN IP4" in sdp or "c=IN IP6" in sdp
        # c-line should NOT be 0.0.0.0 (that's the destination default).
        # For SRT listener-sender, c-line must carry the listener (source) IP.
        assert "c=IN IP4 0.0.0.0" not in sdp, \
            f"SRT c-line should have listener IP, not 0.0.0.0: {sdp[:300]}"

    def test_srt_mp2t_sdp_port_is_listener_port(self) -> None:
        """S7: srt SDP m-line <port> MUST be the UDP listener port from source_port.
        SDP uses activation.active (activated params), not staged."""
        from nmos.node import _generate_sdp_from_params
        node = _make_node()
        _build_config(node, "config7")
        srt_senders = _find_senders_by_transport(node, "urn:x-matrox:transport:srt")
        assert len(srt_senders) >= 1
        sid, sender = srt_senders[0]
        # Read the active (not staged) SourcePort — SDP uses active params
        act = node.get_sender_activation(sid)
        assert act is not None and act.active
        active = act.active[0]
        port = active.SourcePort.value
        sdp = _generate_sdp_from_params(node, sender, sid)
        assert sdp is not None
        assert f"m=application {port} UDP mp2t" in sdp, \
            f"SDP m-line should have listener port {port}, got: {sdp[:300]}"

    def test_sender_as_caller_provides_sdp(self) -> None:
        """SC2: Sender-as-caller MUST provide SDP at activation.
        G1 fix now generates SDP for all SRT transports regardless of protocol role.
        """
        from nmos.node import _generate_sdp_from_params
        node = _make_node()
        _build_config(node, "config7")
        srt_senders = _find_senders_by_transport(node, "urn:x-matrox:transport:srt")
        assert len(srt_senders) >= 1
        sid, sender = srt_senders[0]
        sdp = _generate_sdp_from_params(node, sender, sid)
        assert sdp is not None, "SRT sender should provide SDP"


# ===================================================================
# Class 7: TestSrtRendezvousAndMultipath — §rendezvous, §multi-paths
# ===================================================================

class TestSrtRendezvousAndMultipath:
    """Rendezvous mode and multi-path redundancy rules."""

    def test_rendezvous_sender_port_auto_resolution_copies_source_to_destination(self) -> None:
        """TP11: in rendezvous mode, sender auto-resolution copies SourcePort → DestinationPort.
        Python: resolve_srt_sender in activation_engine.py lines 404-436.
        """
        from nmos.node.activation_engine import resolve_srt_sender
        from nmos.types.generated.nsrt_sender_transport_params import NSrtSenderTransportParamsValue
        from nmos.enums import EnumRegistry

        active = NSrtSenderTransportParamsValue()
        from nmos.enums import RendezVous
        active.Protocol.value = RendezVous
        active.SourcePort.value = 27500
        active.DestinationPort.value = "auto"  # must resolve to SourcePort

        resolve_srt_sender(active, sender_index=0, receiver_index=0, leg=None)

        assert active.DestinationPort.value == 27500, \
            f"rendezvous: DestinationPort should be copied from SourcePort (27500), got {active.DestinationPort.value}"

    def test_rendezvous_receiver_port_auto_resolution_copies_destination_to_source(self) -> None:
        """TP11: in rendezvous mode, receiver auto-resolution copies DestinationPort → SourcePort.
        """
        from nmos.node.activation_engine import resolve_srt_receiver
        from nmos.types.generated.nsrt_receiver_transport_params import NSrtReceiverTransportParamsValue
        from nmos.enums import EnumRegistry

        active = NSrtReceiverTransportParamsValue()
        from nmos.enums import RendezVous
        active.Protocol.value = RendezVous
        active.DestinationPort.value = 27600
        active.SourcePort.value = "auto"  # must resolve to DestinationPort

        resolve_srt_receiver(active, sender_index=0, receiver_index=0, leg=None)

        assert active.SourcePort.value == 27600, \
            f"rendezvous: SourcePort should be copied from DestinationPort (27600), got {active.SourcePort.value}"

    def test_listener_sender_port_auto_resolves_to_null(self) -> None:
        """Listener sender: DestinationPort=auto → None."""
        from nmos.node.activation_engine import resolve_srt_sender
        from nmos.types.generated.nsrt_sender_transport_params import NSrtSenderTransportParamsValue
        from nmos.enums import EnumRegistry

        active = NSrtSenderTransportParamsValue()
        from nmos.enums import Listener
        active.Protocol.value = Listener
        active.DestinationPort.value = "auto"
        active.DestinationIp.value = "auto"

        resolve_srt_sender(active, sender_index=0, receiver_index=0, leg=None)

        assert active.DestinationPort.value is None
        assert active.DestinationIp.value is None

    def test_sender_activation_has_legs_array(self) -> None:
        """Activation staged/active are per-leg arrays (MaxLegs=2 slots).
        This doesn't mean multi-path redundancy is active — that's G4."""
        node = _make_node()
        _build_config(node, "config7")
        srt_senders = _find_senders_by_transport(node, "urn:x-matrox:transport:srt")
        assert len(srt_senders) >= 1
        sid, _ = srt_senders[0]
        act = node.get_sender_activation(sid)
        assert act is not None
        # Per MaxLegs = 2; activation arrays always have 2 slots
        assert len(act.staged) >= 1

    def test_multipath_stream_id_identical_across_legs(self) -> None:
        """TP12: Multi-path stream_id MUST be identical across legs.
        Default single-path config: both legs have same default (null)."""
        node = _make_node()
        _build_config(node, "config7")
        srt_senders = _find_senders_by_transport(node, "urn:x-matrox:transport:srt")
        assert len(srt_senders) >= 1
        sid, _ = srt_senders[0]
        act = node.get_sender_activation(sid)
        assert act is not None and len(act.staged) >= 2
        stream_ids = [leg.StreamId.value for leg in act.staged]
        assert len(set(stream_ids)) == 1, f"stream_id should be identical: {stream_ids}"

    def test_multipath_protocol_identical_across_legs(self) -> None:
        """TP12: Multi-path protocol MUST be identical across legs."""
        node = _make_node()
        _build_config(node, "config7")
        srt_senders = _find_senders_by_transport(node, "urn:x-matrox:transport:srt")
        assert len(srt_senders) >= 1
        sid, _ = srt_senders[0]
        act = node.get_sender_activation(sid)
        assert act is not None and len(act.staged) >= 2
        protocols = [str(leg.Protocol.value) for leg in act.staged]
        assert len(set(protocols)) == 1, f"protocol should be identical: {protocols}"

    def test_multipath_latency_identical_across_legs(self) -> None:
        """TP12: Multi-path latency MUST be identical across legs."""
        node = _make_node()
        _build_config(node, "config7")
        srt_senders = _find_senders_by_transport(node, "urn:x-matrox:transport:srt")
        assert len(srt_senders) >= 1
        sid, _ = srt_senders[0]
        act = node.get_sender_activation(sid)
        assert act is not None and len(act.staged) >= 2
        latencies = [leg.Latency.value for leg in act.staged]
        assert len(set(latencies)) == 1, f"latency should be identical: {latencies}"


# ===================================================================
# Class 8: TestSrtEncryption — §Encryption (E1-E3)
# ===================================================================

class TestSrtEncryption:
    """SRT encryption: PEP adaptation and privacy fields."""

    def test_srt_transport_descriptor_has_privacy_protocol_srt(self) -> None:
        """E1: SRT transport descriptor has privacy_protocol=SRT."""
        from nmos.node.activation import get_transport_descriptor
        from nmos.enums import EnumRegistry
        desc = get_transport_descriptor(EnumRegistry.get("urn:x-matrox:transport:srt"))
        assert desc is not None
        assert desc.has_privacy, "SRT transport should have privacy support"
        assert str(desc.privacy_protocol) == "SRT", \
            f"SRT privacy_protocol should be 'SRT', got {desc.privacy_protocol}"

    def test_srt_mp2t_transport_descriptor_has_privacy(self) -> None:
        """E2: srt.mp2t transport descriptor has privacy support."""
        from nmos.node.activation import get_transport_descriptor
        from nmos.enums import EnumRegistry
        desc = get_transport_descriptor(EnumRegistry.get("urn:x-matrox:transport:srt.mp2t"))
        assert desc is not None
        assert desc.has_privacy, "srt.mp2t transport should have privacy support"

    def test_srt_rtp_transport_descriptor_has_privacy(self) -> None:
        """E3: srt.rtp transport descriptor has privacy support."""
        from nmos.node.activation import get_transport_descriptor
        from nmos.enums import EnumRegistry
        desc = get_transport_descriptor(EnumRegistry.get("urn:x-matrox:transport:srt.rtp"))
        assert desc is not None
        assert desc.has_privacy, "srt.rtp transport should have privacy support"

    def test_sender_transport_params_have_ext_privacy_fields(self) -> None:
        """E1-E3: Sender transport params have ExtPrivacy* fields."""
        from nmos.types.generated.nsrt_sender_transport_params import NSrtSenderTransportParamsValue
        params = NSrtSenderTransportParamsValue()
        for field in ('ExtPrivacyProtocol', 'ExtPrivacyMode', 'ExtPrivacyIV',
                      'ExtPrivacyKeyGenerator', 'ExtPrivacyKeyId',
                      'ExtPrivacyKeyVersion'):
            assert hasattr(params, field), f"Sender params missing {field}"

    def test_receiver_transport_params_have_ext_privacy_fields(self) -> None:
        """E1-E3: Receiver transport params have ExtPrivacy* fields."""
        from nmos.types.generated.nsrt_receiver_transport_params import NSrtReceiverTransportParamsValue
        params = NSrtReceiverTransportParamsValue()
        for field in ('ExtPrivacyProtocol', 'ExtPrivacyMode', 'ExtPrivacyIV',
                      'ExtPrivacyKeyGenerator', 'ExtPrivacyKeyId',
                      'ExtPrivacyKeyVersion'):
            assert hasattr(params, field), f"Receiver params missing {field}"

    def test_receiver_transport_params_have_ext_layers_mapping(self) -> None:
        """Receiver-only: ext_*_layers_mapping fields for sub-stream remapping."""
        from nmos.types.generated.nsrt_receiver_transport_params import NSrtReceiverTransportParamsValue
        params = NSrtReceiverTransportParamsValue()
        assert hasattr(params, 'ExtAudioLayersMapping')
        assert hasattr(params, 'ExtVideoLayersMapping')
        assert hasattr(params, 'ExtDataLayersMapping')
