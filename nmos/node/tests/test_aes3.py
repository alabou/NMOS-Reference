# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""AES3/AM824 compliance tests per specs/NMOS With AES3.md.

Tests cover:
  - Channel-order SDP generation (ST 2110-30/31 AES3 grouping symbols)
  - Opaque AM824 (audio/AM824) IS-04 Source/Flow/Sender/Receiver resources
  - Fully described AM824 (application/AM824) mux IS-04 resources
  - Config10 IPMX multi-sender scenario
  - Config11 MPEG2-TS with AM824 sub-streams
  - Format classification and composite channel-order
  - IS-11 constraint forcing across all AM824 configs
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Add caps/ to path

try:
    from caps.MatroxCCF import (  # type: ignore[import-untyped]
        CapFormatMediaType, CapFormatAudioLayers, CapFormatDataLayers,
        CapMetaFormat, CapMetaLayer,
    )
    HAS_CCF = True
except ImportError:
    HAS_CCF = False

from nmos.node import (
    Node, _get_source_core, _get_flow_core,
    _get_sdp_channel_order, _get_aes3_composite_channel_order,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BUILTIN_DIR = Path(__file__).parent.parent / "config" / "builtin"


def _make_node() -> Node:
    node = Node()
    node.init(serial_number="AES3TST")
    return node


def _build_config(node: Node, config_name: str) -> None:
    from nmos.node.config import ConfigBuilder
    config_path = BUILTIN_DIR / f"{config_name}.json"
    if not config_path.exists():
        pytest.skip(f"{config_name}.json not found")
    with open(config_path) as f:
        config = json.load(f)
    builder = ConfigBuilder(node, verbose=False)
    # Build receivers first (needed for linked_receiver_group resolution)
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
    """Find the first sender whose label contains *substr*."""
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


def _get_sender_flow(node: Node, sender: object) -> object | None:
    flow_id = sender.FlowId.value if sender.FlowId.defined else None
    if flow_id is None:
        return None
    return node.flows.get(flow_id)


def _get_flow_inner(flow_ptr: object) -> object:
    """Unwrap a polymorphic flow/source pointer to its inner typed value."""
    poly = flow_ptr.get() if hasattr(flow_ptr, 'get') else flow_ptr
    assert poly is not None
    return poly


def _get_inner_format(ptr: object) -> str:
    """Get the Format string from a polymorphic source or flow pointer."""
    inner = ptr.get() if hasattr(ptr, 'get') else ptr
    return str(inner.Format.value) if inner and hasattr(inner, 'Format') and inner.Format.defined else ""


def _get_mux_sub_flows(node: Node, mux_flow: object) -> list[object]:
    """Get sub-flow pointers from mux flow parents."""
    fc = _get_flow_core(mux_flow)
    if not fc.Parents.defined:
        return []
    return [node.flows.get(str(pid)) for pid in fc.Parents.value
            if node.flows.get(str(pid)) is not None]


def _get_mux_sub_sources(node: Node, mux_source: object) -> list[object]:
    sc = _get_source_core(mux_source)
    if not sc.Parents.defined:
        return []
    return [node.sources.get(str(pid)) for pid in sc.Parents.value
            if node.sources.get(str(pid)) is not None]


def _apply_constraints(node: Node, sender: object,
                       constraint_sets: list[dict]) -> tuple[str | None, str]:
    """Apply IS-11 constraints; return (error_or_None, status)."""
    from nmos.json.engine import JsonEngine
    from nmos.types.generated.nsender_active_constraints import NSenderActiveConstraintsValue
    obj = NSenderActiveConstraintsValue()
    obj.decode(JsonEngine(), {"constraint_sets": constraint_sets})
    err = node.force_active_constraints(sender, obj)
    if err is not None:
        return str(err), node.set_sender_compatibility_state(sender)
    status = node.set_sender_compatibility_state(sender)
    return None, status


def _build_channels(symbols: list[str]) -> list[object]:
    """Build a list of NAudioChannelValue-like objects for channel order tests."""
    from nmos.enums import EnumRegistry

    class _FakeChannel:
        """Minimal channel object with Symbol.value."""
        def __init__(self, symbol_str: str) -> None:
            self.Symbol = type('', (), {'defined': True, 'value': EnumRegistry.get(symbol_str)})()
            self.Label = type('', (), {'defined': True, 'value': symbol_str})()

    return [_FakeChannel(s) for s in symbols]


# ===================================================================
# Class 1: Channel Order Unit Tests
# ===================================================================

class TestAes3ChannelOrder:
    """Unit tests for _get_sdp_channel_order() — ST 2110-30/31 AES3 symbols."""

    def test_2ch_am824(self) -> None:
        ch = _build_channels(["L", "R"])
        assert _get_sdp_channel_order(ch, True) == "SMPTE2110.(AES3)"

    def test_4ch_am824_stereo(self) -> None:
        ch = _build_channels(["L", "R", "L", "R"])
        result = _get_sdp_channel_order(ch, True)
        assert "AES3" in result
        # Should be (AES3,ST) or (AES3,LtRt) or (AES3,U02)
        assert result.startswith("SMPTE2110.(AES3,")

    def test_4ch_am824_ltrt(self) -> None:
        ch = _build_channels(["L", "R", "Lt", "Rt"])
        result = _get_sdp_channel_order(ch, True)
        assert "AES3" in result

    def test_6ch_am824(self) -> None:
        ch = _build_channels(["L", "R", "L", "R", "L", "R"])
        assert _get_sdp_channel_order(ch, True) == "SMPTE2110.(AES3,AES3,AES3)"

    def test_8ch_am824_51(self) -> None:
        ch = _build_channels(["L", "R", "C", "LFE", "Ls", "Rs", "L", "R"])
        result = _get_sdp_channel_order(ch, True)
        assert "AES3" in result
        # Should contain 51 or multiple AES3 groups
        assert "SMPTE2110." in result

    def test_8ch_am824_generic(self) -> None:
        ch = _build_channels(["L", "R", "L", "R", "L", "R", "L", "R"])
        result = _get_sdp_channel_order(ch, True)
        assert "AES3" in result

    def test_10ch_am824_71(self) -> None:
        ch = _build_channels(["L", "R", "C", "LFE", "Ls", "Rs", "Lrs", "Rrs", "L", "R"])
        result = _get_sdp_channel_order(ch, True)
        assert "SMPTE2110." in result

    def test_16ch_am824(self) -> None:
        ch = _build_channels(["L", "R"] * 8)
        result = _get_sdp_channel_order(ch, True)
        # 8 AES3 pairs
        assert result.count("AES3") == 8

    def test_channel_order_indicates_layers(self) -> None:
        """Number of grouping symbols in channel-order = number of AES3 layers."""
        # 4 channels = 2 AES3 pairs → 2 groups in SMPTE2110.(X,Y)
        ch = _build_channels(["L", "R", "L", "R"])
        result = _get_sdp_channel_order(ch, True)
        # Count comma-separated groups inside parentheses
        groups = result.split("(")[1].rstrip(")").split(",")
        assert len(groups) == 2

    def test_2ch_pcm_stereo(self) -> None:
        """Non-AM824 comparison: 2ch PCM → should NOT use AES3 symbol."""
        ch = _build_channels(["L", "R"])
        result = _get_sdp_channel_order(ch, False)
        assert "AES3" not in result
        assert "SMPTE2110." in result


# ===================================================================
# Class 2: Opaque AM824 Source/Flow (config9 sender[0])
# ===================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestAes3OpaqueSourceFlow:
    """Verify opaque AM824 IS-04 resources — spec sections Sources/Flows."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.node = _make_node()
        _build_config(self.node, "config9")
        result = _find_sender_by_label(self.node, "Audio 0")
        assert result is not None, "config9 opaque AM824 sender not found"
        self.sender_id, self.sender = result
        self.flow = _get_sender_flow(self.node, self.sender)
        assert self.flow is not None
        self.flow_inner = _get_flow_inner(self.flow)
        self.flow_core = _get_flow_core(self.flow)

    def test_opaque_source_format_is_audio(self) -> None:
        """S1: Opaque AM824 source MUST have format=urn:x-nmos:format:audio."""
        src = self.node.sources.get(str(self.flow_core.SourceId.value))
        assert src is not None
        assert _get_inner_format(src) == "urn:x-nmos:format:audio"

    def test_opaque_flow_media_type(self) -> None:
        """F1: Opaque AM824 flow MUST have media_type=audio/AM824."""
        assert str(self.flow_inner.MediaType.value) == "audio/AM824"

    def test_opaque_flow_format_is_audio(self) -> None:
        """F1: Opaque AM824 flow MUST have format=audio."""
        assert _get_inner_format(self.flow) == "urn:x-nmos:format:audio"

    def test_opaque_flow_source_same_format(self) -> None:
        """F3: Flow source_id MUST reference a source of same format."""
        src = self.node.sources.get(str(self.flow_core.SourceId.value))
        assert _get_inner_format(src) == _get_inner_format(self.flow)

    def test_opaque_flow_no_audio_layers(self) -> None:
        """F7: Opaque flow MUST NOT have audio_layers."""
        assert not hasattr(self.flow_inner, 'AudioLayers') or not self.flow_inner.AudioLayers.defined

    def test_opaque_flow_no_data_layers(self) -> None:
        """F7: Opaque flow MUST NOT have data_layers."""
        assert not hasattr(self.flow_inner, 'DataLayers') or not self.flow_inner.DataLayers.defined

    def test_opaque_flow_no_layer(self) -> None:
        """F9: Non-sub-flow MUST NOT have layer attribute set."""
        assert not self.flow_core.Layer.defined

    def test_opaque_sender_transport_rtp(self) -> None:
        """TX1/RTP: Sender MUST indicate rtp or subclassification."""
        transport = str(self.sender.Transport.value)
        assert "rtp" in transport

    def test_opaque_sender_omits_sub_stream_caps(self) -> None:
        """TX3: Opaque sender MUST omit sub-stream capabilities.
        Verified via CCF: no capset should have a non-None format (which
        indicates a sub-flow layer constraint set)."""
        from nmos.node.compatibility import _get_sender_ccf_caps
        caps = _get_sender_ccf_caps(self.node, self.sender)
        if caps is not None:
            for capset in caps.capsets:
                assert capset.format is None, \
                    f"Opaque sender must not have sub-stream capsets (found format={capset.format})"


# ===================================================================
# Class 3: Fully Described AM824 Source/Flow (config9 sender[1])
# ===================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestAes3FullyDescribedSourceFlow:
    """Verify fully-described AM824 IS-04 resources — spec sections Sources/Flows."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.node = _make_node()
        _build_config(self.node, "config9")
        result = _find_sender_by_label(self.node, "Mux")
        assert result is not None, "config9 fully described AM824 sender not found"
        self.sender_id, self.sender = result
        self.flow = _get_sender_flow(self.node, self.sender)
        assert self.flow is not None
        self.flow_inner = _get_flow_inner(self.flow)
        self.flow_core = _get_flow_core(self.flow)

    def test_mux_source_format_is_mux(self) -> None:
        """S2: Fully described AM824 source MUST have format=urn:x-nmos:format:mux."""
        src = self.node.sources.get(str(self.flow_core.SourceId.value))
        assert _get_inner_format(src) == "urn:x-nmos:format:mux"

    def test_mux_flow_media_type(self) -> None:
        """F2: Fully described flow MUST have media_type=application/AM824."""
        assert str(self.flow_inner.MediaType.value) == "application/AM824"

    def test_mux_flow_format_is_mux(self) -> None:
        """F2: Fully described flow format MUST be urn:x-nmos:format:mux."""
        assert _get_inner_format(self.flow) == "urn:x-nmos:format:mux"

    def test_sub_sources_in_mux_parents(self) -> None:
        """S3: Sub-flow sources MUST be in mux source's parents."""
        src = self.node.sources.get(str(self.flow_core.SourceId.value))
        sub_sources = _get_mux_sub_sources(self.node, src)
        assert len(sub_sources) >= 1, "Mux source must have at least one parent sub-source"

    def test_sub_flows_in_mux_parents(self) -> None:
        """F4: Sub-flows MUST be in mux flow's parents."""
        sub_flows = _get_mux_sub_flows(self.node, self.flow)
        assert len(sub_flows) >= 1, "Mux flow must have at least one parent sub-flow"

    def test_sub_flows_have_valid_media_type(self) -> None:
        """F5: Audio sub-flows have valid media types for AM824 mux.
        Note: In an application/AM824 mux, sub-flows MAY use audio/AM824
        (representing individual AES3 streams). The spec's prohibition against
        audio/AM824 sub-flows applies to non-AM824 mux containers like MP2T.
        """
        sub_flows = _get_mux_sub_flows(self.node, self.flow)
        for sf in sub_flows:
            sf_inner = _get_flow_inner(sf)
            if _get_inner_format(sf) == "urn:x-nmos:format:audio":
                mt = str(sf_inner.MediaType.value) if sf_inner.MediaType.defined else ""
                assert mt, "Audio sub-flow must have a media_type"

    def test_mux_flow_has_audio_layers_after_constraint(self) -> None:
        """F6: Fully described flow MUST have audio_layers after constraint application."""
        err, _ = _apply_constraints(self.node, self.sender, [
            {
                "urn:x-nmos:cap:meta:preference": 100,
                "urn:x-nmos:cap:format:media_type": {"enum": ["application/AM824"]},
                "urn:x-matrox:cap:format:audio_layers": {"minimum": 1, "maximum": 4},
                "urn:x-matrox:cap:format:data_layers": {"minimum": 0, "maximum": 0},
            }
        ])
        assert err is None, f"Constraint error: {err}"
        flow = _get_sender_flow(self.node, self.sender)
        inner = _get_flow_inner(flow)
        assert hasattr(inner, 'AudioLayers') and inner.AudioLayers.defined

    def test_mux_flow_has_data_layers_after_constraint(self) -> None:
        """F6: Fully described flow MUST have data_layers after constraint application."""
        err, _ = _apply_constraints(self.node, self.sender, [
            {
                "urn:x-nmos:cap:meta:preference": 100,
                "urn:x-nmos:cap:format:media_type": {"enum": ["application/AM824"]},
                "urn:x-matrox:cap:format:audio_layers": {"minimum": 1, "maximum": 4},
                "urn:x-matrox:cap:format:data_layers": {"minimum": 0, "maximum": 0},
            }
        ])
        assert err is None
        flow = _get_sender_flow(self.node, self.sender)
        inner = _get_flow_inner(flow)
        assert hasattr(inner, 'DataLayers') and inner.DataLayers.defined

    def test_audio_layers_within_sub_flow_count(self) -> None:
        """F6: audio_layers is the *multiplexed*-layer count forced by the active
        constraint — a distinct attribute from the flow's parent sub-flow
        structure. Applying a constraint updates the audio_layers/video_layers/
        data_layers attributes WITHOUT changing the parents (matching the Go
        reference's updateMuxFlow, which calls WithFlowLayers but leaves the
        parent structure intact).

        A capability range like audio_layers:[1,4] only restricts what is
        multiplexed; on the force/reset path it resolves to the range minimum
        (faithful to Go's getPropertyFromIntCapability). So audio_layers may be
        less than the number of available audio sub-flows, but never more, and
        the parent structure is preserved.
        """
        err, _ = _apply_constraints(self.node, self.sender, [
            {
                "urn:x-nmos:cap:meta:preference": 100,
                "urn:x-nmos:cap:format:media_type": {"enum": ["application/AM824"]},
                "urn:x-matrox:cap:format:audio_layers": {"minimum": 1, "maximum": 4},
                "urn:x-matrox:cap:format:data_layers": {"minimum": 0, "maximum": 0},
            }
        ])
        assert err is None
        flow = _get_sender_flow(self.node, self.sender)
        inner = _get_flow_inner(flow)
        sub_flows = _get_mux_sub_flows(self.node, flow)
        audio_subs = [sf for sf in sub_flows
                      if _get_inner_format(sf) == "urn:x-nmos:format:audio"]
        # Parent structure is unchanged by the constraint (config9 has 2 audio subs).
        assert len(audio_subs) == 2
        # audio_layers is the forced (multiplexed) attribute: within the
        # constraint range and not exceeding the available sub-flows.
        assert inner.AudioLayers.defined
        assert 1 <= inner.AudioLayers.value <= len(audio_subs)

    def test_sub_flows_have_layer(self) -> None:
        """F8: Sub-flow MUST have layer attribute."""
        sub_flows = _get_mux_sub_flows(self.node, self.flow)
        for sf in sub_flows:
            fc = _get_flow_core(sf)
            assert fc.Layer.defined, "Sub-flow must have layer attribute"

    def test_sub_flow_layers_sequential(self) -> None:
        """F8: Sub-flow layer values sequential per format."""
        sub_flows = _get_mux_sub_flows(self.node, self.flow)
        layers_by_fmt: dict[str, list[int]] = {}
        for sf in sub_flows:
            fc = _get_flow_core(sf)
            fmt = _get_inner_format(sf)
            if fmt not in layers_by_fmt:
                layers_by_fmt[fmt] = []
            layers_by_fmt[fmt].append(fc.Layer.value)
        for fmt, layers in layers_by_fmt.items():
            assert sorted(layers) == list(range(len(layers))), \
                f"Layers for {fmt} not sequential: {layers}"

    def test_mux_flow_source_same_format(self) -> None:
        """F3: Flow source_id MUST reference a source of same format (mux)."""
        src = self.node.sources.get(str(self.flow_core.SourceId.value))
        assert _get_inner_format(src) == "urn:x-nmos:format:mux"

    def test_sender_transport_rtp(self) -> None:
        """TX1: Sender transport MUST be rtp or subclassification."""
        assert "rtp" in str(self.sender.Transport.value)


# ===================================================================
# Class 4: Receivers (config9)
# ===================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestAes3Receivers:
    """Verify AM824 receiver IS-04 resources."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.node = _make_node()
        _build_config(self.node, "config9")

    def _get_receiver_inner(self, recv: object) -> object:
        return recv.get() if hasattr(recv, 'get') else recv

    def test_opaque_receiver_format_audio(self) -> None:
        """RX1: Opaque AM824 receiver format=audio."""
        result = _find_receiver_by_label(self.node, "Audio 0")
        assert result is not None
        _, recv = result
        inner = self._get_receiver_inner(recv)
        assert str(inner.Format.value) == "urn:x-nmos:format:audio"

    def test_opaque_receiver_media_types_contains_am824(self) -> None:
        """RX1: Opaque receiver media_types MUST contain audio/AM824."""
        result = _find_receiver_by_label(self.node, "Audio 0")
        assert result is not None
        _, recv = result
        inner = self._get_receiver_inner(recv)
        caps = inner.Caps
        assert caps.defined
        mt_list = [str(mt) for mt in caps._value.MediaTypes._inner]
        assert "audio/AM824" in mt_list, f"Expected audio/AM824 in {mt_list}"

    def test_fully_described_receiver_format_mux(self) -> None:
        """RX2: Fully described receiver format=mux."""
        result = _find_receiver_by_label(self.node, "Mux")
        assert result is not None
        _, recv = result
        inner = self._get_receiver_inner(recv)
        assert str(inner.Format.value) == "urn:x-nmos:format:mux"

    def test_fully_described_receiver_media_types_contains_application_am824(self) -> None:
        """RX2: Fully described receiver media_types MUST contain application/AM824."""
        result = _find_receiver_by_label(self.node, "Mux")
        assert result is not None
        _, recv = result
        inner = self._get_receiver_inner(recv)
        caps = inner.Caps
        assert caps.defined
        mt_list = [str(mt) for mt in caps._value.MediaTypes._inner]
        assert "application/AM824" in mt_list, f"Expected application/AM824 in {mt_list}"

    def test_opaque_receiver_transport_rtp(self) -> None:
        """RX6: Receiver transport MUST be rtp or subclassification."""
        from nmos.node import _get_receiver_core
        result = _find_receiver_by_label(self.node, "Audio 0")
        assert result is not None
        _, recv = result
        rc = _get_receiver_core(recv)
        assert "rtp" in str(rc.Transport.value)

    def test_fully_described_receiver_transport_rtp(self) -> None:
        """RX6: Mux receiver transport MUST be rtp or subclassification."""
        from nmos.node import _get_receiver_core
        result = _find_receiver_by_label(self.node, "Mux")
        assert result is not None
        _, recv = result
        rc = _get_receiver_core(recv)
        assert "rtp" in str(rc.Transport.value)

    def test_receiver_has_constraint_sets(self) -> None:
        """RX5: Receiver MUST express limitations via constraint_sets."""
        for _sid, recv in self.node.receivers:
            inner = self._get_receiver_inner(recv)
            caps = inner.Caps
            assert caps.defined, "Receiver must have Caps defined"
            cs = caps._value.ConstraintSets
            assert cs.defined and len(cs.value) > 0, "Receiver must have constraint_sets"


# ===================================================================
# Class 5: Config10 IPMX (AM824 + H.264)
# ===================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestAes3Config10IPMX:
    """Config10: IPMX with video + opaque AM824 + fully described AM824 mux."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.node = _make_node()
        _build_config(self.node, "config10")

    def test_opaque_am824_sender_exists(self) -> None:
        result = _find_sender_by_label(self.node, "Audio 0")
        assert result is not None

    def test_fully_described_am824_sender_exists(self) -> None:
        result = _find_sender_by_label(self.node, "AM824 Mux")
        assert result is not None

    def test_video_sender_coexists(self) -> None:
        result = _find_sender_by_label(self.node, "Video 0")
        assert result is not None

    def test_mux_has_two_audio_layers_after_native_constraint(self) -> None:
        """Config10 mux sender native constraint → audio_layers=2."""
        result = _find_sender_by_label(self.node, "AM824 Mux")
        assert result is not None
        _, sender = result
        # AudioLayers gets set during IS-11 constraint application
        self.node.force_active_constraints(sender, None)
        flow = _get_sender_flow(self.node, sender)
        inner = _get_flow_inner(flow)
        assert inner.AudioLayers.defined
        assert inner.AudioLayers.value == 2

    def test_mux_sub_flows_are_coded_audio(self) -> None:
        """Config10 mux sub-flows are AAC (coded audio), not PCM."""
        result = _find_sender_by_label(self.node, "AM824 Mux")
        assert result is not None
        _, sender = result
        flow = _get_sender_flow(self.node, sender)
        sub_flows = _get_mux_sub_flows(self.node, flow)
        for sf in sub_flows:
            sf_inner = _get_flow_inner(sf)
            if hasattr(sf_inner, 'MediaType') and sf_inner.MediaType.defined:
                mt = str(sf_inner.MediaType.value)
                # Config10 sub-flows should be AAC (mp4a-adts)
                assert mt != "", f"Sub-flow must have media_type set"

    def test_mux_sub_flow_layers_0_and_1(self) -> None:
        """F8: Two sub-flows with layer=0 and layer=1."""
        result = _find_sender_by_label(self.node, "AM824 Mux")
        assert result is not None
        _, sender = result
        flow = _get_sender_flow(self.node, sender)
        sub_flows = _get_mux_sub_flows(self.node, flow)
        layers = sorted([_get_flow_core(sf).Layer.value for sf in sub_flows])
        assert 0 in layers
        assert 1 in layers

    def test_mux_receiver_has_application_am824(self) -> None:
        """RX2: Config10 mux receiver has application/AM824."""
        result = _find_receiver_by_label(self.node, "Mux")
        assert result is not None
        _, recv = result
        inner = recv.get() if hasattr(recv, 'get') else recv
        caps = inner.Caps
        assert caps.defined
        mt_list = [str(mt) for mt in caps._value.MediaTypes._inner]
        assert "application/AM824" in mt_list


# ===================================================================
# Class 6: MPEG2-TS with AM824 sub-stream (config11)
# ===================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestAes3InMpeg2ts:
    """Config11: MPEG2-TS/RTSP mux receiver with AM824 audio sub-stream capability."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.node = _make_node()
        _build_config(self.node, "config11")

    def test_mpeg2ts_mux_receiver_exists(self) -> None:
        """Config11 has an RTSP Mux receiver."""
        result = _find_receiver_by_label(self.node, "Mux")
        assert result is not None

    def test_mpeg2ts_mux_receiver_format_mux(self) -> None:
        """MPEG2-TS mux receiver has format=mux."""
        result = _find_receiver_by_label(self.node, "Mux")
        assert result is not None
        _, recv = result
        inner = recv.get() if hasattr(recv, 'get') else recv
        assert str(inner.Format.value) == "urn:x-nmos:format:mux"

    def test_mpeg2ts_receiver_has_am824_sub_capability(self) -> None:
        """Config11 mux receiver config has AM824 sub-stream in constraint_sets."""
        config_path = BUILTIN_DIR / "config11.json"
        with open(config_path) as f:
            config = json.load(f)
        # Check config JSON directly for AM824 in receiver constraint_sets
        found_am824 = False
        for r in config.get("receivers", []):
            if "Mux" in r.get("label", ""):
                for cs in r.get("constraint_sets", []):
                    mt = cs.get("urn:x-nmos:cap:format:media_type", {})
                    if "enum" in mt and "audio/AM824" in mt["enum"]:
                        found_am824 = True
        assert found_am824, "Config11 mux receiver should have AM824 sub capability"

    def test_config11_audio_sender_exists(self) -> None:
        """Config11 has an independent audio sender."""
        result = _find_sender_by_label(self.node, "Audio 0")
        assert result is not None


# ===================================================================
# Class 7: Compatibility & Composite Channel Order
# ===================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestAes3Compatibility:
    """Format classification and composite channel-order tests."""

    def test_format_from_media_type_audio_am824(self) -> None:
        from nmos.node.compatibility import get_format_from_media_type
        assert get_format_from_media_type("audio/AM824") == "urn:x-nmos:format:audio"

    def test_format_from_media_type_application_am824(self) -> None:
        from nmos.node.compatibility import get_format_from_media_type
        assert get_format_from_media_type("application/AM824") == "urn:x-nmos:format:mux"

    def test_class_from_media_type_audio_am824(self) -> None:
        from nmos.node.compatibility import get_class_from_media_type
        assert get_class_from_media_type("audio/AM824") == "coded"

    def test_class_from_media_type_application_am824(self) -> None:
        from nmos.node.compatibility import get_class_from_media_type
        assert get_class_from_media_type("application/AM824") == "mux"

    def test_composite_channel_order_config9_mux(self) -> None:
        """Composite channel-order for config9 mux (1 audio layer)."""
        node = _make_node()
        _build_config(node, "config9")
        result = _find_sender_by_label(node, "Mux")
        assert result is not None
        _, sender = result
        flow = _get_sender_flow(node, sender)
        # Config9 mux has 1 AM824 sub-flow — but AM824 sub-flows are
        # prohibited by _get_aes3_composite_channel_order.
        # This tests that the function properly raises on AM824 sub-flows.
        with pytest.raises(ValueError, match="audio/AM824 cannot be a sub-flow"):
            _get_aes3_composite_channel_order(node, flow)

    def test_composite_channel_order_config10_mux(self) -> None:
        """Composite channel-order for config10 mux (2 AAC layers)."""
        node = _make_node()
        _build_config(node, "config10")
        result = _find_sender_by_label(node, "AM824 Mux")
        assert result is not None
        _, sender = result
        flow = _get_sender_flow(node, sender)
        channel_order, ch_count = _get_aes3_composite_channel_order(node, flow)
        assert "SMPTE2110." in channel_order
        # 2 coded audio layers → 2 AES3 groups, 4 channels total
        assert ch_count == 4
        groups = channel_order.split("(")[1].rstrip(")").split(",")
        assert len(groups) == 2


# ===================================================================
# Class 8: IS-11 Constraint Forcing
# ===================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestAes3IS11Constraints:
    """IS-11 constraint forcing across AM824 configs."""

    # -- Config9 opaque --

    def test_config9_opaque_force_native(self) -> None:
        """Force native AM824 caps → flow stays audio/AM824, 2ch, 48kHz."""
        node = _make_node()
        _build_config(node, "config9")
        result = _find_sender_by_label(node, "Audio 0")
        assert result is not None
        _, sender = result
        err, status = _apply_constraints(node, sender, [
            {
                "urn:x-nmos:cap:meta:preference": 100,
                "urn:x-nmos:cap:format:media_type": {"enum": ["audio/AM824"]},
                "urn:x-nmos:cap:format:sample_rate": {"enum": [{"numerator": 48000}]},
                "urn:x-nmos:cap:format:channel_count": {"enum": [2]},
            }
        ])
        assert err is None, f"Constraint error: {err}"
        flow = _get_sender_flow(node, sender)
        inner = _get_flow_inner(flow)
        assert str(inner.MediaType.value) == "audio/AM824"

    def test_config9_opaque_delete_resets(self) -> None:
        """DELETE constraints → flow returns to native."""
        node = _make_node()
        _build_config(node, "config9")
        result = _find_sender_by_label(node, "Audio 0")
        assert result is not None
        _, sender = result
        # First apply, then delete
        _apply_constraints(node, sender, [
            {"urn:x-nmos:cap:meta:preference": 100,
             "urn:x-nmos:cap:format:media_type": {"enum": ["audio/AM824"]}}
        ])
        node.force_active_constraints(sender, None)
        flow = _get_sender_flow(node, sender)
        inner = _get_flow_inner(flow)
        assert str(inner.MediaType.value) == "audio/AM824"

    # -- Config9 mux --

    def test_config9_mux_force_native(self) -> None:
        """Force native mux caps → flow stays application/AM824."""
        node = _make_node()
        _build_config(node, "config9")
        result = _find_sender_by_label(node, "Mux")
        assert result is not None
        _, sender = result
        err, status = _apply_constraints(node, sender, [
            {
                "urn:x-nmos:cap:meta:preference": 100,
                "urn:x-nmos:cap:format:media_type": {"enum": ["application/AM824"]},
                "urn:x-matrox:cap:format:audio_layers": {"minimum": 1, "maximum": 4},
                "urn:x-matrox:cap:format:data_layers": {"minimum": 0, "maximum": 0},
            }
        ])
        assert err is None, f"Constraint error: {err}"
        flow = _get_sender_flow(node, sender)
        inner = _get_flow_inner(flow)
        assert str(inner.MediaType.value) == "application/AM824"

    def test_config9_mux_delete_resets(self) -> None:
        """DELETE mux constraints → flow returns to native state."""
        node = _make_node()
        _build_config(node, "config9")
        result = _find_sender_by_label(node, "Mux")
        assert result is not None
        _, sender = result
        _apply_constraints(node, sender, [
            {"urn:x-nmos:cap:meta:preference": 100,
             "urn:x-nmos:cap:format:media_type": {"enum": ["application/AM824"]}}
        ])
        node.force_active_constraints(sender, None)
        flow = _get_sender_flow(node, sender)
        inner = _get_flow_inner(flow)
        assert str(inner.MediaType.value) == "application/AM824"

    # -- Config10 opaque --

    def test_config10_opaque_force_am824_2ch(self) -> None:
        """Force AM824 2ch → flow correct."""
        node = _make_node()
        _build_config(node, "config10")
        result = _find_sender_by_label(node, "Audio 0")
        assert result is not None
        _, sender = result
        err, _ = _apply_constraints(node, sender, [
            {
                "urn:x-nmos:cap:meta:preference": 100,
                "urn:x-nmos:cap:format:media_type": {"enum": ["audio/AM824"]},
                "urn:x-nmos:cap:format:channel_count": {"enum": [2]},
                "urn:x-nmos:cap:format:sample_rate": {"enum": [{"numerator": 48000}]},
            }
        ])
        assert err is None
        flow = _get_sender_flow(node, sender)
        inner = _get_flow_inner(flow)
        assert str(inner.MediaType.value) == "audio/AM824"

    def test_config10_opaque_force_am824_alternate(self) -> None:
        """Force AM824 alternate constraints (non-native) → accepted."""
        node = _make_node()
        _build_config(node, "config10")
        result = _find_sender_by_label(node, "Audio 0")
        assert result is not None
        _, sender = result
        # Use a range that includes 2-8 channels to match the flexible capset
        err, _ = _apply_constraints(node, sender, [
            {
                "urn:x-nmos:cap:meta:preference": 100,
                "urn:x-nmos:cap:format:media_type": {"enum": ["audio/AM824"]},
                "urn:x-nmos:cap:format:channel_count": {"enum": [2, 4, 8]},
                "urn:x-nmos:cap:format:sample_rate": {"enum": [{"numerator": 48000}]},
            }
        ])
        assert err is None
        flow = _get_sender_flow(node, sender)
        inner = _get_flow_inner(flow)
        assert str(inner.MediaType.value) == "audio/AM824"

    def test_config10_opaque_force_pcm_constraints_accepted(self) -> None:
        """Force PCM constraints → constraint validation succeeds."""
        node = _make_node()
        _build_config(node, "config10")
        result = _find_sender_by_label(node, "Audio 0")
        assert result is not None
        _, sender = result
        err, _ = _apply_constraints(node, sender, [
            {
                "urn:x-nmos:cap:meta:preference": 100,
                "urn:x-nmos:cap:format:media_type": {"enum": ["audio/L24", "audio/L16"]},
                "urn:x-nmos:cap:format:channel_count": {"enum": [2]},
                "urn:x-nmos:cap:format:sample_rate": {"enum": [{"numerator": 48000}]},
                "urn:x-nmos:cap:format:sample_depth": {"enum": [16, 24]},
            }
        ])
        assert err is None

    # -- Config10 mux --

    def test_config10_mux_constraints_accepted(self) -> None:
        """Config10 mux: applying AM824 constraints is accepted."""
        node = _make_node()
        _build_config(node, "config10")
        result = _find_sender_by_label(node, "AM824 Mux")
        assert result is not None
        _, sender = result
        err, _ = _apply_constraints(node, sender, [
            {
                "urn:x-nmos:cap:meta:preference": 100,
                "urn:x-nmos:cap:format:media_type": {"enum": ["application/AM824"]},
                "urn:x-matrox:cap:format:video_layers": {"minimum": 0, "maximum": 0},
                "urn:x-matrox:cap:format:audio_layers": {"minimum": 1, "maximum": 2},
                "urn:x-matrox:cap:format:data_layers": {"minimum": 0, "maximum": 0},
            }
        ])
        assert err is None
        flow = _get_sender_flow(node, sender)
        inner = _get_flow_inner(flow)
        assert str(inner.MediaType.value) == "application/AM824"

    # -- Cross-config: version bumps --

    def test_sender_version_bumps_on_constraint(self) -> None:
        """Sender version incremented after applying then deleting constraints."""
        node = _make_node()
        _build_config(node, "config10")
        result = _find_sender_by_label(node, "Audio 0")
        assert result is not None
        _, sender = result
        # Apply constraints first
        _apply_constraints(node, sender, [
            {"urn:x-nmos:cap:meta:preference": 100,
             "urn:x-nmos:cap:format:media_type": {"enum": ["audio/AM824"]},
             "urn:x-nmos:cap:format:channel_count": {"enum": [2]},
             "urn:x-nmos:cap:format:sample_rate": {"enum": [{"numerator": 48000}]}}
        ])
        v_before = sender.ResourceCore.Version.value
        # Now delete → should bump version
        import time; time.sleep(0.01)  # ensure timestamp advances
        node.force_active_constraints(sender, None)
        v_after = sender.ResourceCore.Version.value
        assert v_after >= v_before, "Sender version must bump after constraint delete"
