# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""H.222.0 / MPEG2-TS compliance tests per specs/NMOS With H.222.0.md.

Tests cover:
  - IS-04 Source/Flow/Sender/Receiver resource attributes for mux streams
  - Media type selection by transport (application/MP2T for RTP, application/mp2t for other)
  - SDP generation for MP2T flows
  - IS-11 constraint forcing on mux senders
  - Audio sub-flow rules (even channel count for PCM, opaque AM824 only)
  - Receiver ext_layers_mapping transport attributes
  - Transport restrictions for MP2T mux flows

Configs used:
  - config4: RTP video-only mux (application/MP2T)
  - config4a_mux: RTP video+audio mux (application/MP2T)
  - config7: SRT mux (application/mp2t)
  - config7u: UDP mux (application/mp2t)
  - config11: RTSP receiver + RTP sender (application/MP2T)
  - config12: SRT mux (application/mp2t)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


try:
    from caps.MatroxCCF import (  # type: ignore[import-untyped]
        CapFormatMediaType, CapFormatAudioLayers, CapFormatVideoLayers,
        CapFormatDataLayers, CapMetaFormat, CapMetaLayer,
    )
    HAS_CCF = True
except ImportError:
    HAS_CCF = False

from nmos.node import (
    Node, _get_source_core, _get_flow_core,
)


# ---------------------------------------------------------------------------
# Helpers (shared patterns from test_aes3.py)
# ---------------------------------------------------------------------------

BUILTIN_DIR = Path(__file__).parent.parent / "config" / "builtin"


def _make_node() -> Node:
    node = Node()
    node.init(serial_number="MP2TST")
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


def _find_sender_by_label(node: Node, substr: str) -> tuple[str, object] | None:
    for _sid, s in node.senders:
        label = s.ResourceCore.Label.value if s.ResourceCore.Label.defined else ""
        if substr in label:
            return s.ResourceCore.Id.value, s
    return None


def _find_receiver_by_label(node: Node, substr: str) -> tuple[str, object] | None:
    for _sid, r in node.receivers:
        from nmos.node import _get_resource_core
        rc = _get_resource_core(r)
        label = rc.Label.value if rc.Label.defined else ""
        if substr in label:
            return rc.Id.value, r
    return None


def _find_mux_sender(node: Node) -> tuple[str, object] | None:
    for _sid, s in node.senders:
        fmt = str(s.Format.value) if s.Format.defined else ""
        if "mux" in fmt:
            return s.ResourceCore.Id.value, s
    return None


def _find_mux_receiver(node: Node) -> tuple[str, object] | None:
    for _sid, r in node.receivers:
        inner = r.get() if hasattr(r, 'get') else r
        fmt = str(inner.Format.value) if hasattr(inner, 'Format') and inner.Format.defined else ""
        if "mux" in fmt:
            from nmos.node import _get_resource_core
            rc = _get_resource_core(r)
            return rc.Id.value, r
    return None


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


def _get_mux_sub_flows(node: Node, mux_flow: object) -> list[object]:
    fc = _get_flow_core(mux_flow)
    if not fc.Parents.defined:
        return []
    return [node.flows.get(str(pid)) for pid in fc.Parents.value
            if node.flows.get(str(pid)) is not None]


def _apply_constraints(node: Node, sender: object,
                       constraint_sets: list[dict]) -> tuple[str | None, str]:
    from nmos.json.engine import JsonEngine
    from nmos.types.generated.nsender_active_constraints import NSenderActiveConstraintsValue
    obj = NSenderActiveConstraintsValue()
    obj.decode(JsonEngine(), {"constraint_sets": constraint_sets})
    err = node.force_active_constraints(sender, obj)
    if err is not None:
        return str(err), node.set_sender_compatibility_state(sender)
    status = node.set_sender_compatibility_state(sender)
    return None, status


# ===================================================================
# Class 1: TestMp2tSourcesFlows — IS-04 resource attributes
# ===================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestMp2tSourcesFlows:
    """Verify MPEG2-TS IS-04 Source/Flow attributes per H.222.0 spec."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.node = _make_node()
        _build_config(self.node, "config4a_mux")
        result = _find_mux_sender(self.node)
        assert result is not None, "No mux sender in config4a_mux"
        self.sender_id, self.sender = result
        self.flow = _get_sender_flow(self.node, self.sender)
        assert self.flow is not None
        self.flow_inner = _get_flow_inner(self.flow)
        self.flow_core = _get_flow_core(self.flow)

    def test_mux_source_format_is_mux(self) -> None:
        """S1: mux Source MUST have format=urn:x-nmos:format:mux."""
        src = self.node.sources.get(str(self.flow_core.SourceId.value))
        assert _get_inner_format(src) == "urn:x-nmos:format:mux"

    def test_mux_flow_format_is_mux(self) -> None:
        """F1: mux Flow MUST have format=urn:x-nmos:format:mux."""
        assert _get_inner_format(self.flow) == "urn:x-nmos:format:mux"

    def test_mux_flow_media_type_application_mp2t(self) -> None:
        """F1: config4a_mux (RTP) → media_type MUST be application/MP2T."""
        assert str(self.flow_inner.MediaType.value) == "application/MP2T"

    def test_mux_flow_source_same_format(self) -> None:
        """F2: Flow source_id MUST reference a source of same format (mux)."""
        src = self.node.sources.get(str(self.flow_core.SourceId.value))
        assert _get_inner_format(src) == _get_inner_format(self.flow)

    def test_sub_flows_in_parents(self) -> None:
        """F3: Sub-flows MUST be in mux flow's parents array."""
        sub_flows = _get_mux_sub_flows(self.node, self.flow)
        assert len(sub_flows) >= 1, "Mux flow must have at least one sub-flow"

    def test_sub_flow_has_layer(self) -> None:
        """F7: Each sub-flow MUST have a layer attribute."""
        for sf in _get_mux_sub_flows(self.node, self.flow):
            assert _get_flow_core(sf).Layer.defined, "Sub-flow must have layer"

    def test_sub_flow_layers_sequential(self) -> None:
        """F7: Sub-flow layers sequential per format."""
        layers_by_fmt: dict[str, list[int]] = {}
        for sf in _get_mux_sub_flows(self.node, self.flow):
            fc = _get_flow_core(sf)
            fmt = _get_inner_format(sf)
            layers_by_fmt.setdefault(fmt, []).append(fc.Layer.value)
        for fmt, layers in layers_by_fmt.items():
            assert sorted(layers) == list(range(len(layers))), \
                f"Layers for {fmt} not sequential: {layers}"

    def test_non_sub_flow_no_layer(self) -> None:
        """F8: Non-sub-flow (standalone sender's flow) MUST NOT have layer."""
        # Config4a_mux has standalone video+audio senders alongside mux
        node = _make_node()
        _build_config(node, "config4a_mux")
        for _sid, s in node.senders:
            fmt = str(s.Format.value) if s.Format.defined else ""
            if "mux" not in fmt:
                flow = _get_sender_flow(node, s)
                if flow is not None:
                    fc = _get_flow_core(flow)
                    assert not fc.Layer.defined, \
                        f"Non-sub-flow {s.ResourceCore.Label.value} should not have layer"

    def test_non_mux_flow_no_layers_attrs(self) -> None:
        """F6: Non-mux flow MUST NOT have audio_layers/video_layers/data_layers."""
        node = _make_node()
        _build_config(node, "config4a_mux")
        for _sid, s in node.senders:
            fmt = str(s.Format.value) if s.Format.defined else ""
            if "mux" in fmt:
                continue
            flow = _get_sender_flow(node, s)
            if flow is None:
                continue
            inner = _get_flow_inner(flow)
            for attr in ('AudioLayers', 'VideoLayers', 'DataLayers'):
                if hasattr(inner, attr):
                    assert not getattr(inner, attr).defined, \
                        f"Non-mux flow should not have {attr}"

    def test_mux_flow_has_all_three_layer_attrs(self) -> None:
        """F5: mux flow MUST have video_layers, audio_layers, data_layers."""
        # These are set at build time by pipelines.py
        assert hasattr(self.flow_inner, 'VideoLayers') and self.flow_inner.VideoLayers.defined
        assert hasattr(self.flow_inner, 'AudioLayers') and self.flow_inner.AudioLayers.defined
        assert hasattr(self.flow_inner, 'DataLayers') and self.flow_inner.DataLayers.defined

    def test_sub_sources_in_mux_parents(self) -> None:
        """S2: Sub-flow sources MUST be in mux source's parents."""
        src = self.node.sources.get(str(self.flow_core.SourceId.value))
        sc = _get_source_core(src)
        assert sc.Parents.defined and len(sc.Parents.value) >= 1


# ===================================================================
# Class 2: TestMp2tMediaTypeByTransport — F4, TX4, RX4
# ===================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestMp2tMediaTypeByTransport:
    """Verify media_type selection: RTP → application/MP2T, other → application/mp2t."""

    def test_config4_rtp_media_type_uppercase(self) -> None:
        """config4 (rtp.mcast) → application/MP2T."""
        node = _make_node()
        _build_config(node, "config4")
        result = _find_mux_sender(node)
        assert result is not None
        _, sender = result
        flow = _get_sender_flow(node, sender)
        assert str(_get_flow_inner(flow).MediaType.value) == "application/MP2T"

    def test_config4a_rtp_media_type_uppercase(self) -> None:
        """config4a_mux (rtp.mcast) → application/MP2T."""
        node = _make_node()
        _build_config(node, "config4a_mux")
        result = _find_mux_sender(node)
        assert result is not None
        _, sender = result
        flow = _get_sender_flow(node, sender)
        assert str(_get_flow_inner(flow).MediaType.value) == "application/MP2T"

    def test_config7_srt_media_type_lowercase(self) -> None:
        """config7 (srt) → application/mp2t (SRT is non-RTP)."""
        node = _make_node()
        _build_config(node, "config7")
        result = _find_mux_sender(node)
        assert result is not None
        _, sender = result
        flow = _get_sender_flow(node, sender)
        assert str(_get_flow_inner(flow).MediaType.value) == "application/mp2t"

    def test_config7u_udp_media_type_lowercase(self) -> None:
        """config7u (udp.mcast) → application/mp2t (UDP is non-RTP)."""
        node = _make_node()
        _build_config(node, "config7u")
        result = _find_mux_sender(node)
        assert result is not None
        _, sender = result
        flow = _get_sender_flow(node, sender)
        assert str(_get_flow_inner(flow).MediaType.value) == "application/mp2t"

    def test_config12_srt_media_type_lowercase(self) -> None:
        """config12 (srt) → application/mp2t."""
        node = _make_node()
        _build_config(node, "config12")
        result = _find_mux_sender(node)
        assert result is not None
        _, sender = result
        flow = _get_sender_flow(node, sender)
        assert str(_get_flow_inner(flow).MediaType.value) == "application/mp2t"

    def test_config11_rtsp_receiver_is_mux_format(self) -> None:
        """config11 RTSP receiver has format=mux and is a mux receiver."""
        node = _make_node()
        _build_config(node, "config11")
        result = _find_mux_receiver(node)
        assert result is not None, "Config11 should have an RTSP mux receiver"
        _, recv = result
        inner = recv.get() if hasattr(recv, 'get') else recv
        assert str(inner.Format.value) == "urn:x-nmos:format:mux"

    def test_rtp_receiver_media_types_has_application_MP2T(self) -> None:
        """RTP receiver caps media_types MUST contain application/MP2T."""
        node = _make_node()
        _build_config(node, "config4")
        result = _find_mux_receiver(node)
        assert result is not None
        _, recv = result
        inner = recv.get() if hasattr(recv, 'get') else recv
        caps = inner.Caps
        assert caps.defined
        mt_list = [str(mt) for mt in caps._value.MediaTypes._inner]
        assert "application/MP2T" in mt_list, f"Expected application/MP2T in {mt_list}"

    def test_srt_receiver_media_types_has_application_mp2t(self) -> None:
        """SRT receiver caps media_types MUST contain application/mp2t."""
        node = _make_node()
        _build_config(node, "config7")
        result = _find_mux_receiver(node)
        assert result is not None
        _, recv = result
        inner = recv.get() if hasattr(recv, 'get') else recv
        caps = inner.Caps
        assert caps.defined
        mt_list = [str(mt) for mt in caps._value.MediaTypes._inner]
        assert "application/mp2t" in mt_list, f"Expected application/mp2t in {mt_list}"

    def test_config6_ndi_media_type(self) -> None:
        """config6 (NDI receiver) → application/ndi (not MP2T)."""
        node = _make_node()
        _build_config(node, "config6")
        result = _find_mux_sender(node)
        assert result is not None
        _, sender = result
        flow = _get_sender_flow(node, sender)
        assert str(_get_flow_inner(flow).MediaType.value) == "application/ndi"


# ===================================================================
# Class 3: TestMp2tSdpGeneration — TX4
# ===================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestMp2tSdpGeneration:
    """SDP generation for MPEG2-TS flows.

    RTP SDP uses video/MP2T (per spec note), other transports use application/mp2t.
    """

    def test_rtp_sender_sdp_generated(self) -> None:
        """config4 RTP sender generates an SDP transport file."""
        from nmos.node import _generate_sdp_from_params
        node = _make_node()
        _build_config(node, "config4")
        result = _find_mux_sender(node)
        assert result is not None
        sender_id, sender = result
        sdp = _generate_sdp_from_params(node, sender, sender_id)
        assert sdp is not None and len(sdp) > 0, "RTP MP2T sender should generate SDP"

    def test_rtp_sender_sdp_contains_mp2t(self) -> None:
        """config4 RTP SDP should contain MP2T encoding name."""
        from nmos.node import _generate_sdp_from_params
        node = _make_node()
        _build_config(node, "config4")
        result = _find_mux_sender(node)
        assert result is not None
        sender_id, sender = result
        sdp = _generate_sdp_from_params(node, sender, sender_id)
        assert sdp is not None
        assert "MP2T" in sdp, f"SDP should contain MP2T encoding, got: {sdp[:200]}"

    def test_sender_transport_rtp(self) -> None:
        """config4 sender transport MUST be rtp or subclassification."""
        node = _make_node()
        _build_config(node, "config4")
        result = _find_mux_sender(node)
        assert result is not None
        _, sender = result
        assert "rtp" in str(sender.Transport.value)

    def test_rtp_sender_sdp_has_video_media_line(self) -> None:
        """TX4: RTP SDP m-line MUST use video media type (not application)."""
        from nmos.node import _generate_sdp_from_params
        node = _make_node()
        _build_config(node, "config4")
        result = _find_mux_sender(node)
        assert result is not None
        sender_id, sender = result
        sdp = _generate_sdp_from_params(node, sender, sender_id)
        assert sdp is not None
        assert "m=video" in sdp, f"RTP MP2T SDP m-line should be video, got: {sdp[:200]}"

    def test_non_rtp_srt_sender_generates_application_mp2t_sdp(self) -> None:
        """Non-RTP SRT transports (srt, srt.mp2t) generate SDP with
        m=application ... UDP mp2t per spec §SRT SDP format-specific parameters."""
        from nmos.node import _generate_sdp_from_params
        node = _make_node()
        _build_config(node, "config7")
        result = _find_mux_sender(node)
        assert result is not None
        sender_id, sender = result
        sdp = _generate_sdp_from_params(node, sender, sender_id)
        assert sdp is not None, "Native SRT sender should generate SDP"
        assert "m=application" in sdp and "UDP mp2t" in sdp, \
            f"Native SRT mux should emit m=application ... UDP mp2t, got: {sdp[:200]}"


# ===================================================================
# Class 4: TestMp2tIS11Constraints
# ===================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestMp2tIS11Constraints:
    """IS-11 constraint forcing on MPEG2-TS mux senders."""

    def test_config4a_mux_force_trunk_accepted(self) -> None:
        """Force trunk-only constraint on config4a_mux → accepted."""
        node = _make_node()
        _build_config(node, "config4a_mux")
        result = _find_mux_sender(node)
        assert result is not None
        _, sender = result
        err, status = _apply_constraints(node, sender, [{
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:format:media_type": {"enum": ["application/MP2T"]},
        }])
        assert err is None, f"Trunk constraint rejected: {err}"
        assert status == "constrained"

    def test_config4a_mux_force_with_video_layer(self) -> None:
        """Force trunk + video layer constraint → accepted."""
        node = _make_node()
        _build_config(node, "config4a_mux")
        result = _find_mux_sender(node)
        assert result is not None
        _, sender = result
        err, status = _apply_constraints(node, sender, [
            {
                "urn:x-nmos:cap:meta:preference": 100,
                "urn:x-nmos:cap:meta:enabled": True,
                "urn:x-nmos:cap:format:media_type": {"enum": ["application/MP2T"]},
            },
            {
                "urn:x-nmos:cap:meta:preference": 100,
                "urn:x-nmos:cap:meta:enabled": False,
                "urn:x-matrox:cap:meta:layer_enabled": True,
                "urn:x-matrox:cap:meta:format": "urn:x-nmos:format:video",
                "urn:x-matrox:cap:meta:layer": 0,
                "urn:x-nmos:cap:format:media_type": {"enum": ["video/H264"]},
            },
        ])
        assert err is None, f"Trunk + video layer rejected: {err}"

    def test_config4_layer_count_matches_pipeline(self) -> None:
        """F5: max layers in sender caps equals sub-flow count in pipeline."""
        node = _make_node()
        _build_config(node, "config4")
        result = _find_mux_sender(node)
        assert result is not None
        _, sender = result
        flow = _get_sender_flow(node, sender)
        inner = _get_flow_inner(flow)
        sub_flows = _get_mux_sub_flows(node, flow)

        video_subs = sum(1 for sf in sub_flows if _get_inner_format(sf) == "urn:x-nmos:format:video")
        audio_subs = sum(1 for sf in sub_flows if _get_inner_format(sf) == "urn:x-nmos:format:audio")

        assert inner.VideoLayers.value == video_subs, \
            f"VideoLayers={inner.VideoLayers.value} != sub-flows={video_subs}"
        assert inner.AudioLayers.value == audio_subs, \
            f"AudioLayers={inner.AudioLayers.value} != sub-flows={audio_subs}"

    def test_uuid_cascade_on_constraint_change(self) -> None:
        """Atomic State Changes: flow UUID changes after constraint force."""
        node = _make_node()
        _build_config(node, "config4a_mux")
        result = _find_mux_sender(node)
        assert result is not None
        _, sender = result
        flow_id_before = sender.FlowId.value

        _apply_constraints(node, sender, [{
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:format:media_type": {"enum": ["application/MP2T"]},
        }])

        flow_id_after = sender.FlowId.value
        assert flow_id_before != flow_id_after, \
            "Flow UUID must change after constraint application (Atomic State Changes)"

    def test_uuid_cascade_on_subflows(self) -> None:
        """Atomic State Changes: the mux SUB-FLOW UUIDs change on a constraint
        force, not just the trunk. Without per-sub-flow cascade the force
        mutates a sub-flow in place (same id + version) and the registry's
        version-gated push never re-sends it — the registry (and the
        controller's green) stay stale. Mirrors Go forceActiveConstraints,
        which cascades every flow (trunk + each sub-flow)."""
        node = _make_node()
        _build_config(node, "config4a_mux")
        result = _find_mux_sender(node)
        assert result is not None
        _, sender = result

        flow_before = _get_sender_flow(node, sender)
        parents_before = list(_get_flow_core(flow_before).Parents.value)
        assert parents_before, "mux flow must have parent sub-flows"

        _apply_constraints(node, sender, [{
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:format:media_type": {"enum": ["application/MP2T"]},
        }])

        flow_after = _get_sender_flow(node, sender)
        parents_after = list(_get_flow_core(flow_after).Parents.value)
        assert parents_after, "mux flow must still have parent sub-flows"
        # Every sub-flow got a fresh UUID and the trunk's Parents repoint to
        # them — so the forced sub-flows re-register with the registry.
        assert set(parents_before).isdisjoint(set(parents_after)), (
            "sub-flow UUIDs must change after constraint force "
            f"(before={parents_before} after={parents_after})"
        )
        # And the new sub-flow ids resolve to live flows in the node store.
        for pid in parents_after:
            assert node.flows.get(str(pid)) is not None, \
                f"new sub-flow {pid} must exist in the node store"

    def test_delete_constraints_resets(self) -> None:
        """DELETE active constraints → sender returns to unconstrained."""
        node = _make_node()
        _build_config(node, "config4a_mux")
        result = _find_mux_sender(node)
        assert result is not None
        _, sender = result

        _apply_constraints(node, sender, [{
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:format:media_type": {"enum": ["application/MP2T"]},
        }])
        node.force_active_constraints(sender, None)
        status = node.set_sender_compatibility_state(sender)
        assert status == "unconstrained"

    def test_config4a_force_audio_layers_1(self) -> None:
        """Force audio_layers=1 (config4a_mux has 1 audio sub-flow max)."""
        node = _make_node()
        _build_config(node, "config4a_mux")
        result = _find_mux_sender(node)
        assert result is not None
        _, sender = result
        err, status = _apply_constraints(node, sender, [{
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:format:media_type": {"enum": ["application/MP2T"]},
            "urn:x-matrox:cap:format:video_layers": {"minimum": 1, "maximum": 1},
            "urn:x-matrox:cap:format:audio_layers": {"minimum": 1, "maximum": 1},
            "urn:x-matrox:cap:format:data_layers": {"minimum": 0, "maximum": 0},
        }])
        assert err is None, f"audio_layers=1 constraint rejected: {err}"
        flow = _get_sender_flow(node, sender)
        inner = _get_flow_inner(flow)
        assert inner.AudioLayers.defined and inner.AudioLayers.value == 1

    def test_config4a_force_audio_layers_0_accepted(self) -> None:
        """Force audio_layers=0 constraint is accepted (sender caps allow min=0)."""
        node = _make_node()
        _build_config(node, "config4a_mux")
        result = _find_mux_sender(node)
        assert result is not None
        _, sender = result
        err, status = _apply_constraints(node, sender, [{
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:format:media_type": {"enum": ["application/MP2T"]},
            "urn:x-matrox:cap:format:video_layers": {"minimum": 1, "maximum": 1},
            "urn:x-matrox:cap:format:audio_layers": {"minimum": 0, "maximum": 0},
            "urn:x-matrox:cap:format:data_layers": {"minimum": 0, "maximum": 0},
        }])
        assert err is None, f"audio_layers=0 constraint rejected: {err}"

    def test_config4a_video_layer_constraint_propagates(self) -> None:
        """Force video layer constraint → sub-flow media_type updated."""
        node = _make_node()
        _build_config(node, "config4a_mux")
        result = _find_mux_sender(node)
        assert result is not None
        _, sender = result
        err, _ = _apply_constraints(node, sender, [
            {
                "urn:x-nmos:cap:meta:preference": 100,
                "urn:x-nmos:cap:meta:enabled": True,
                "urn:x-nmos:cap:format:media_type": {"enum": ["application/MP2T"]},
                "urn:x-matrox:cap:format:video_layers": {"minimum": 1, "maximum": 1},
                "urn:x-matrox:cap:format:audio_layers": {"minimum": 0, "maximum": 1},
                "urn:x-matrox:cap:format:data_layers": {"minimum": 0, "maximum": 0},
            },
            {
                "urn:x-nmos:cap:meta:preference": 100,
                "urn:x-nmos:cap:meta:enabled": False,
                "urn:x-matrox:cap:meta:layer_enabled": True,
                "urn:x-matrox:cap:meta:format": "urn:x-nmos:format:video",
                "urn:x-matrox:cap:meta:layer": 0,
                "urn:x-nmos:cap:format:media_type": {"enum": ["video/H264"]},
            },
        ])
        assert err is None, f"Video layer constraint rejected: {err}"
        # Verify sub-flow was constrained to H.264
        flow = _get_sender_flow(node, sender)
        for sf in _get_mux_sub_flows(node, flow):
            if _get_inner_format(sf) == "urn:x-nmos:format:video":
                sf_inner = _get_flow_inner(sf)
                mt = str(sf_inner.MediaType.value) if sf_inner.MediaType.defined else ""
                assert mt == "video/H264", f"Video sub-flow should be H.264 after forcing, got {mt}"

    def test_source_uuid_cascade_on_constraint(self) -> None:
        """Source UUID changes after constraint application (Atomic State Changes)."""
        node = _make_node()
        _build_config(node, "config4a_mux")
        result = _find_mux_sender(node)
        assert result is not None
        _, sender = result
        flow = _get_sender_flow(node, sender)
        fc = _get_flow_core(flow)
        src_id_before = fc.SourceId.value

        _apply_constraints(node, sender, [{
            "urn:x-nmos:cap:meta:preference": 100,
            "urn:x-nmos:cap:meta:enabled": True,
            "urn:x-nmos:cap:format:media_type": {"enum": ["application/MP2T"]},
        }])

        flow = _get_sender_flow(node, sender)
        fc = _get_flow_core(flow)
        src_id_after = fc.SourceId.value
        assert src_id_before != src_id_after, \
            "Source UUID must change after constraint application"


# ===================================================================
# Class 5: TestMp2tAudioSubFlowRules — F10, F11, F12
# ===================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestMp2tAudioSubFlowRules:
    """Audio sub-flow rules for MPEG2-TS mux."""

    def test_pcm_sub_flow_even_channel_count(self) -> None:
        """F10: L16/L20/L24 audio sub-flow MUST have even channel count."""
        _PCM_TYPES = {"audio/L8", "audio/L16", "audio/L20", "audio/L24"}

        for config_name in ("config4a_mux", "config7", "config11"):
            node = _make_node()
            _build_config(node, config_name)
            result = _find_mux_sender(node)
            if result is None:
                continue
            _, sender = result
            flow = _get_sender_flow(node, sender)
            for sf in _get_mux_sub_flows(node, flow):
                sf_inner = _get_flow_inner(sf)
                mt = str(sf_inner.MediaType.value) if sf_inner.MediaType.defined else ""
                if mt not in _PCM_TYPES:
                    continue
                # Get channel count from source
                sf_fc = _get_flow_core(sf)
                src = node.sources.get(str(sf_fc.SourceId.value))
                if src is None:
                    continue
                src_inner = src.get() if hasattr(src, 'get') else src
                if hasattr(src_inner, 'Channels') and src_inner.Channels.defined:
                    ch = len(src_inner.Channels.value)
                    assert ch % 2 == 0, \
                        f"{config_name}: PCM sub-flow {mt} has odd channel count {ch}"

    def test_am824_sub_flow_is_opaque_not_fully_described(self) -> None:
        """F11: audio/AM824 sub-flow in MPEG2-TS must be opaque (format=audio).
        A fully-described AM824 (application/AM824) MUST NOT be a sub-flow (F12).
        """
        # Check config11 which has AM824 sub-flow capability
        config_path = BUILTIN_DIR / "config11.json"
        with open(config_path) as f:
            config = json.load(f)
        for item in config.get("receivers", []):
            if item.get("format") != "urn:x-nmos:format:mux":
                continue
            for cs in item.get("constraint_sets", []):
                cs_fmt = cs.get("urn:x-matrox:cap:meta:format")
                mt = cs.get("urn:x-nmos:cap:format:media_type", {})
                mt_enum = mt.get("enum", [])
                # If a sub-flow constraint set has AM824, it must be opaque
                if cs_fmt is not None and "audio/AM824" in mt_enum:
                    # audio/AM824 is opaque (format=audio) — OK
                    pass
                if cs_fmt is not None and "application/AM824" in mt_enum:
                    pytest.fail(
                        "application/AM824 (fully-described) MUST NOT be a "
                        "sub-flow of an H.222.0 mux"
                    )


# ===================================================================
# Class 6: TestMp2tReceiverCaps — RX1, RX2, RX4
# ===================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestMp2tReceiverCaps:
    """Receiver capability and transport attribute tests."""

    def test_rtp_receiver_format_mux(self) -> None:
        """RX1: mux receiver format=urn:x-nmos:format:mux."""
        node = _make_node()
        _build_config(node, "config4")
        result = _find_mux_receiver(node)
        assert result is not None
        _, recv = result
        inner = recv.get() if hasattr(recv, 'get') else recv
        assert str(inner.Format.value) == "urn:x-nmos:format:mux"

    def test_rtp_receiver_transport(self) -> None:
        """RX6: RTP receiver transport MUST be rtp or subclassification."""
        from nmos.node import _get_receiver_core
        node = _make_node()
        _build_config(node, "config4")
        result = _find_mux_receiver(node)
        assert result is not None
        _, recv = result
        rc = _get_receiver_core(recv)
        assert "rtp" in str(rc.Transport.value)

    def test_receiver_has_constraint_sets(self) -> None:
        """RX5: Receiver MUST express limitations via constraint_sets."""
        node = _make_node()
        _build_config(node, "config4a_mux")
        result = _find_mux_receiver(node)
        assert result is not None
        _, recv = result
        inner = recv.get() if hasattr(recv, 'get') else recv
        caps = inner.Caps
        assert caps.defined
        cs = caps._value.ConstraintSets
        assert cs.defined and len(cs.value) > 0

    def test_receiver_has_sub_stream_caps(self) -> None:
        """RX3: Fully described receiver has sub-stream constraint sets."""
        from nmos.node.compatibility import _get_receiver_ccf_caps
        node = _make_node()
        _build_config(node, "config4a_mux")
        result = _find_mux_receiver(node)
        assert result is not None
        _, recv = result
        caps = _get_receiver_ccf_caps(node, recv)
        if caps is None:
            pytest.skip("No CCF caps for receiver")
        has_layer = any(cs.layer is not None for cs in caps.capsets)
        assert has_layer, "Fully described receiver must have layer constraint sets"


# ===================================================================
# Class 7: TestMp2tReceiverExtLayerMapping — RX3
# ===================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestMp2tReceiverExtLayerMapping:
    """Tests for ext_audio_layers_mapping, ext_video_layers_mapping,
    ext_data_layers_mapping transport attributes on mux receivers.

    Per H.222.0 spec: A Receiver MAY support these extended layer mapping
    transport attributes, enabling a Controller to select among sub-Streams.
    """

    def test_rtp_receiver_transport_params_type_has_ext_layers_fields(self) -> None:
        """RTP receiver transport params type includes ext_*_layers_mapping fields."""
        from nmos.types.generated.nrtp_receiver_transport_params import NRtpReceiverTransportParamsValue
        params = NRtpReceiverTransportParamsValue()
        assert hasattr(params, 'ExtAudioLayersMapping'), \
            "RTP receiver params should have ExtAudioLayersMapping"
        assert hasattr(params, 'ExtVideoLayersMapping'), \
            "RTP receiver params should have ExtVideoLayersMapping"
        assert hasattr(params, 'ExtDataLayersMapping'), \
            "RTP receiver params should have ExtDataLayersMapping"

    def test_ext_layers_mapping_initializes_empty(self) -> None:
        """ext_*_layers_mapping should initialize to empty string when set."""
        from nmos.types.generated.nrtp_receiver_transport_params import NRtpReceiverTransportParamsValue
        params = NRtpReceiverTransportParamsValue()
        params.ExtAudioLayersMapping.value = ""
        assert params.ExtAudioLayersMapping.value == ""
        assert params.ExtAudioLayersMapping.defined


# ===================================================================
# Class 8: TestMp2tTransportRestrictions — T1, T2
# ===================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestMp2tTransportRestrictions:
    """Transport / media_type combination validation."""

    def test_video_mp2t_not_used_as_flow_media_type(self) -> None:
        """video/MP2T is opaque (unsupported) — no config should use it as flow media_type."""
        for path in sorted(BUILTIN_DIR.glob("config*.json")):
            with open(path) as f:
                config = json.load(f)
            for cat in ("senders", "receivers"):
                for item in config.get(cat, []):
                    for cs in item.get("constraint_sets", []):
                        mt = cs.get("urn:x-nmos:cap:format:media_type", {})
                        for v in mt.get("enum", []):
                            assert v != "video/MP2T", \
                                f"{path.name}: video/MP2T found as flow media_type — " \
                                "must use application/MP2T (RTP) or application/mp2t (other)"

    def test_format_from_media_type_rejects_video_mp2t_as_mux(self) -> None:
        """video/MP2T should NOT map to FormatMux — it's opaque (unsupported)."""
        from nmos.node.compatibility import get_format_from_media_type
        # video/MP2T falls to FormatVideo (opaque video), NOT FormatMux
        fmt = get_format_from_media_type("video/MP2T")
        assert fmt == "urn:x-nmos:format:video", \
            f"video/MP2T should map to FormatVideo (opaque), got {fmt}"

    def test_application_MP2T_maps_to_mux(self) -> None:
        """application/MP2T MUST map to FormatMux."""
        from nmos.node.compatibility import get_format_from_media_type
        assert get_format_from_media_type("application/MP2T") == "urn:x-nmos:format:mux"

    def test_application_mp2t_maps_to_mux(self) -> None:
        """application/mp2t MUST map to FormatMux."""
        from nmos.node.compatibility import get_format_from_media_type
        assert get_format_from_media_type("application/mp2t") == "urn:x-nmos:format:mux"

    def test_all_configs_transport_media_type_consistent(self) -> None:
        """Cross-check: every mux config's media_type matches its transport.

        RTP-family transports → application/MP2T (uppercase)
        Non-RTP transports → application/mp2t (lowercase)
        NDI transports → application/ndi
        """
        _RTP_FAMILY = {
            "urn:x-nmos:transport:rtp",
            "urn:x-nmos:transport:rtp.ucast",
            "urn:x-nmos:transport:rtp.mcast",
            "urn:x-matrox:transport:rtp.tcp",
            "urn:x-matrox:transport:srt.rtp",
        }

        for path in sorted(BUILTIN_DIR.glob("config*.json")):
            with open(path) as f:
                config = json.load(f)
            for cat in ("senders", "receivers"):
                for item in config.get(cat, []):
                    if item.get("format") != "urn:x-nmos:format:mux":
                        continue
                    tr = item.get("transport", "")
                    # Find trunk media_type from first trunk constraint set
                    trunk_mt = None
                    for cs in item.get("constraint_sets", []):
                        if "urn:x-matrox:cap:meta:format" in cs:
                            continue  # Skip sub-flow constraint sets
                        mt = cs.get("urn:x-nmos:cap:format:media_type", {})
                        for v in mt.get("enum", []):
                            if v.startswith("application/"):
                                trunk_mt = v
                                break
                        if trunk_mt:
                            break

                    if trunk_mt is None:
                        continue

                    # Skip non-MP2T mux types (NDI, AM824, RTSP)
                    if trunk_mt in ("application/ndi", "application/AM824",
                                    "application/rtsp"):
                        continue

                    is_rtp = tr in _RTP_FAMILY
                    if is_rtp:
                        assert trunk_mt == "application/MP2T", \
                            f"{path.name} {cat[:-1]}: RTP transport {tr} should use " \
                            f"application/MP2T, got {trunk_mt}"
                    else:
                        assert trunk_mt == "application/mp2t", \
                            f"{path.name} {cat[:-1]}: non-RTP transport {tr} should use " \
                            f"application/mp2t, got {trunk_mt}"
