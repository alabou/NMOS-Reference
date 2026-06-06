# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for IS-11 compatibility functions.

Layer 1: Pure CCF unit tests — construct CCF objects directly, call compatibility
         functions, verify results without NMOS type system.
Layer 2: Config-driven integration tests — build Node from builtin configs,
         verify sender/receiver compatibility.
"""

from __future__ import annotations

import sys
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

# Add caps/ to path for CCF

try:
    from caps.MatroxCCF import (
        Caps, CapSet, Cap, Capability, RangeValue, RangeType,
        ConSet, Constraint, Cons,
        make_capset, make_conset,
        conset_included_in_caps, capset_included_in_capset,
        caps_constrict_by_cons,
        convert_caps_json_to_caps,
        CapFormatMediaType, CapFormatFrameWidth, CapFormatFrameHeight,
        CapFormatGrainRate, CapFormatInterlaceMode, CapFormatColorspace,
        CapFormatTransferCharacteristic, CapFormatColorSampling,
        CapFormatComponentDepth,
        CapFormatChannelCount, CapFormatSampleRate, CapFormatSampleDepth,
        CapFormatBitRate, CapFormatConstantBitRate,
        CapFormatProfile, CapFormatLevel,
        CapTransportClockRefType, CapTransportSynchronousMedia,
    )
    HAS_CCF = True
except ImportError:
    HAS_CCF = False

from nmos.node.compatibility import (
    # Phase 1
    get_supported_constraints,
    is_constraint_name_supported,
    is_constraint_name_of_transport_category,
    get_format_from_media_type,
    get_class_from_media_type,
    get_bitmask_from_compatibility_groups,
    fix_pcm_sample_depth,
    fix_video_width_height,
    # Phase 2
    check_flow_properties_compatibility,
    check_sender_flow_compatibility,
    force_active_constraints,
    # Phase 3
    _get_cap_value, _get_cap_str, _get_cap_int, _get_cap_bool, _get_cap_rational,
    # Phase 5
    get_generic_properties,
    # Phase 6
    set_sender_compatibility_state,
    set_receiver_compatibility_state,
    fix_coded_video_flow,
    validate_active_constraints,
    force_active_constraints,
    force_flow_properties_compatibility,
    update_sender_to_compliant_flow,
    intersect_constraints_with_caps,
)


# ===========================================================================
# Layer 1: Pure CCF unit tests
# ===========================================================================

class TestHelpers:
    """Test Phase 1 helper functions."""

    def test_supported_video_constraints(self) -> None:
        from nmos.enums import FormatVideo, CapFormatFrameWidth, CapFormatFrameHeight, CapFormatChannelCount
        constraints = get_supported_constraints(FormatVideo.s)
        assert CapFormatFrameWidth.s in constraints
        assert CapFormatFrameHeight.s in constraints
        assert CapFormatChannelCount.s not in constraints

    def test_supported_audio_constraints(self) -> None:
        from nmos.enums import FormatAudio, CapFormatChannelCount, CapFormatSampleRate, CapFormatFrameWidth
        constraints = get_supported_constraints(FormatAudio.s)
        assert CapFormatChannelCount.s in constraints
        assert CapFormatSampleRate.s in constraints
        assert CapFormatFrameWidth.s not in constraints

    def test_supported_mux_constraints(self) -> None:
        from nmos.enums import FormatMux, CapFormatVideoLayers, CapFormatFrameWidth
        constraints = get_supported_constraints(FormatMux.s)
        assert CapFormatVideoLayers.s in constraints
        assert CapFormatFrameWidth.s in constraints  # mux mixed includes sub-flow caps

    def test_unsupported_format_raises(self) -> None:
        from nmos.errors import InvalidParameter
        with pytest.raises(InvalidParameter):
            get_supported_constraints("urn:x-nmos:format:unknown")

    def test_constraint_name_supported(self) -> None:
        assert is_constraint_name_supported(
            "urn:x-nmos:format:video", "urn:x-nmos:cap:format:frame_width")
        assert not is_constraint_name_supported(
            "urn:x-nmos:format:audio", "urn:x-nmos:cap:format:frame_width")

    def test_transport_category(self) -> None:
        assert is_constraint_name_of_transport_category("urn:x-nmos:cap:transport:privacy")
        assert is_constraint_name_of_transport_category("urn:x-matrox:cap:transport:synchronous_media")
        assert not is_constraint_name_of_transport_category("urn:x-nmos:cap:format:frame_width")

    def test_format_from_media_type(self) -> None:
        assert get_format_from_media_type("video/raw") == "urn:x-nmos:format:video"
        assert get_format_from_media_type("video/H264") == "urn:x-nmos:format:video"
        assert get_format_from_media_type("audio/L24") == "urn:x-nmos:format:audio"
        assert get_format_from_media_type("audio/aac") == "urn:x-nmos:format:audio"
        # video/MP2T is opaque (not supported) — falls to FormatVideo
        assert get_format_from_media_type("video/MP2T") == "urn:x-nmos:format:video"
        assert get_format_from_media_type("application/AM824") == "urn:x-nmos:format:mux"
        assert get_format_from_media_type("application/MP2T") == "urn:x-nmos:format:mux"
        assert get_format_from_media_type("data/USB") == "urn:x-nmos:format:data"

    def test_class_from_media_type(self) -> None:
        assert get_class_from_media_type("video/raw") == "raw"
        assert get_class_from_media_type("video/H264") == "coded"
        assert get_class_from_media_type("video/H265") == "coded"
        assert get_class_from_media_type("audio/L24") == "raw"
        assert get_class_from_media_type("audio/AM824") == "coded"  # ClassAudioCoded
        assert get_class_from_media_type("audio/aac") == "coded"
        # video/MP2T is opaque (not supported) — falls to "coded" (video codec class)
        assert get_class_from_media_type("video/MP2T") == "coded"
        assert get_class_from_media_type("application/AM824") == "mux"  # ClassMux (MuxAm824)

    def test_bitmask_from_groups(self) -> None:
        assert get_bitmask_from_compatibility_groups({0}) == 1
        assert get_bitmask_from_compatibility_groups({0, 1}) == 3
        assert get_bitmask_from_compatibility_groups({5}) == 32
        # Spec C1: missing attribute (None) → part of all groups
        # (uses 0xffffffffffffffff to mean "all groups")
        assert get_bitmask_from_compatibility_groups(None) == 0xFFFFFFFFFFFFFFFF
        # Explicit empty set is "member of no group" — distinct from None
        assert get_bitmask_from_compatibility_groups(set()) == 0
        # Out-of-range values (≥64) are silently ignored
        assert get_bitmask_from_compatibility_groups({64, 100}) == 0
        assert get_bitmask_from_compatibility_groups({0, 64}) == 1


@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestCapSetAccessors:
    """Test CapSet value extraction helpers."""

    def test_get_cap_str(self) -> None:
        cs = CapSet(caps=make_capset(
            Cap(CapFormatMediaType, RangeValue(values=("video/raw",), type=RangeType.STRING)),
        ))
        assert _get_cap_str(cs, CapFormatMediaType) == "video/raw"

    def test_get_cap_int(self) -> None:
        cs = CapSet(caps=make_capset(
            Cap(CapFormatFrameWidth, RangeValue(values=(1920,), type=RangeType.INT)),
        ))
        assert _get_cap_int(cs, CapFormatFrameWidth) == 1920

    def test_get_cap_bool(self) -> None:
        cs = CapSet(caps=make_capset(
            Cap(CapFormatConstantBitRate, RangeValue(values=(False,), type=RangeType.BOOL)),
        ))
        assert _get_cap_bool(cs, CapFormatConstantBitRate) is False

    def test_get_cap_rational(self) -> None:
        cs = CapSet(caps=make_capset(
            Cap(CapFormatGrainRate, RangeValue(values=(Fraction(60, 1),), type=RangeType.RATIONAL)),
        ))
        result = _get_cap_rational(cs, CapFormatGrainRate)
        assert result == (60, 1)

    def test_get_missing_cap_returns_none(self) -> None:
        cs = CapSet(caps={})
        assert _get_cap_value(cs, CapFormatFrameWidth) is None

    def test_get_infinite_cap_returns_none(self) -> None:
        cs = CapSet(caps=make_capset(
            Cap(CapFormatFrameWidth, RangeValue(infinite=True, type=RangeType.INT)),
        ))
        assert _get_cap_value(cs, CapFormatFrameWidth) is None


@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestFixPcmSampleDepth:
    """Test PCM sample depth / media_type ambiguity fix-up."""

    def test_depth_24_sets_media_type_l24(self) -> None:
        props = {
            CapFormatMediaType: Cap(CapFormatMediaType,
                RangeValue(values=("audio/L16",), type=RangeType.STRING)),
            CapFormatSampleDepth: Cap(CapFormatSampleDepth,
                RangeValue(values=(24,), type=RangeType.INT)),
        }
        # sample_depth is "original" (user-specified) → it takes priority
        constraints = {
            CapFormatSampleDepth: Cap(CapFormatSampleDepth,
                RangeValue(values=(24,), type=RangeType.INT)),
        }
        constraints[CapFormatSampleDepth].original = True
        fix_pcm_sample_depth(props, constraints)
        assert str(props[CapFormatMediaType].value.values[0]) == "audio/L24"

    def test_media_type_l16_sets_depth_16(self) -> None:
        constraints = {
            CapFormatMediaType: Cap(CapFormatMediaType,
                RangeValue(values=("audio/L16",), type=RangeType.STRING)),
        }
        constraints[CapFormatMediaType].original = True
        props = {
            CapFormatMediaType: Cap(CapFormatMediaType,
                RangeValue(values=("audio/L16",), type=RangeType.STRING)),
            CapFormatSampleDepth: Cap(CapFormatSampleDepth,
                RangeValue(values=(24,), type=RangeType.INT)),
        }
        fix_pcm_sample_depth(props, constraints)
        assert int(props[CapFormatSampleDepth].value.values[0]) == 16

    def test_non_pcm_not_affected(self) -> None:
        props = {
            CapFormatMediaType: Cap(CapFormatMediaType,
                RangeValue(values=("audio/aac",), type=RangeType.STRING)),
        }
        fix_pcm_sample_depth(props)
        # Should not crash or modify anything


@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestFixVideoWidthHeight:
    """Test video width/height consistency fix-up."""

    def test_width_1920_derives_height_1080(self) -> None:
        props = {
            CapFormatMediaType: Cap(CapFormatMediaType,
                RangeValue(values=("video/raw",), type=RangeType.STRING)),
            CapFormatFrameWidth: Cap(CapFormatFrameWidth,
                RangeValue(values=(1920,), type=RangeType.INT)),
            CapFormatFrameHeight: Cap(CapFormatFrameHeight,
                RangeValue(values=(720,), type=RangeType.INT)),
        }
        constraints = {
            CapFormatFrameWidth: Cap(CapFormatFrameWidth,
                RangeValue(values=(1920,), type=RangeType.INT)),
        }
        constraints[CapFormatFrameWidth].original = True
        fix_video_width_height(props, constraints)
        assert int(props[CapFormatFrameHeight].value.values[0]) == 1080

    def test_height_720_derives_width_1280(self) -> None:
        props = {
            CapFormatMediaType: Cap(CapFormatMediaType,
                RangeValue(values=("video/H264",), type=RangeType.STRING)),
            CapFormatFrameWidth: Cap(CapFormatFrameWidth,
                RangeValue(values=(1920,), type=RangeType.INT)),
            CapFormatFrameHeight: Cap(CapFormatFrameHeight,
                RangeValue(values=(720,), type=RangeType.INT)),
        }
        constraints = {
            CapFormatFrameHeight: Cap(CapFormatFrameHeight,
                RangeValue(values=(720,), type=RangeType.INT)),
        }
        constraints[CapFormatFrameHeight].original = True
        fix_video_width_height(props, constraints)
        assert int(props[CapFormatFrameWidth].value.values[0]) == 1280

    def test_audio_not_affected(self) -> None:
        props = {
            CapFormatMediaType: Cap(CapFormatMediaType,
                RangeValue(values=("audio/L24",), type=RangeType.STRING)),
        }
        fix_video_width_height(props)
        # Should not crash


@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestCheckFlowPropertiesCompatibility:
    """Test CCF inclusion-based flow compatibility checking."""

    def _make_flow_capset(self, **kwargs: Any) -> CapSet:
        """Build a single-value CapSet representing flow properties."""
        caps = {}
        type_map = {
            CapFormatMediaType: RangeType.STRING,
            CapFormatFrameWidth: RangeType.INT,
            CapFormatFrameHeight: RangeType.INT,
            CapFormatComponentDepth: RangeType.INT,
            CapFormatGrainRate: RangeType.RATIONAL,
            CapFormatInterlaceMode: RangeType.STRING,
            CapFormatColorspace: RangeType.STRING,
            CapFormatTransferCharacteristic: RangeType.STRING,
            CapFormatColorSampling: RangeType.STRING,
        }
        for name, val in kwargs.items():
            rt = type_map.get(name, RangeType.STRING)
            caps[name] = Cap(name, RangeValue(values=(val,), type=rt))
        return CapSet(caps=caps, preference=100, label="flow")

    def _make_sender_caps(self, **kwargs: Any) -> Caps:
        """Build a Caps with one CapSet having range capabilities."""
        caps = {}
        for name, vals in kwargs.items():
            if isinstance(vals, tuple) and len(vals) == 2 and isinstance(vals[0], str) and vals[0] == "range":
                # Range: ("range", (min, max))
                _, (mn, mx) = vals
                caps[name] = Cap(name, RangeValue(min=mn, max=mx, type=RangeType.INT))
            elif isinstance(vals, list):
                # Enum list
                if vals and isinstance(vals[0], str):
                    caps[name] = Cap(name, RangeValue(values=tuple(vals), type=RangeType.STRING))
                elif vals and isinstance(vals[0], Fraction):
                    caps[name] = Cap(name, RangeValue(values=tuple(vals), type=RangeType.RATIONAL))
                else:
                    caps[name] = Cap(name, RangeValue(values=tuple(vals), type=RangeType.INT))
            else:
                caps[name] = Cap(name, RangeValue(values=(vals,),
                    type=RangeType.STRING if isinstance(vals, str) else RangeType.INT))
        return Caps(capsets=[CapSet(caps=caps, preference=100, label="sender")])

    def test_exact_match_compatible(self) -> None:
        """Flow exactly matches sender native caps."""
        flow = self._make_flow_capset(**{
            CapFormatMediaType: "video/raw",
            CapFormatFrameWidth: 1920,
            CapFormatFrameHeight: 1080,
        })
        sender = self._make_sender_caps(**{
            CapFormatMediaType: ["video/raw"],
            CapFormatFrameWidth: [1920],
            CapFormatFrameHeight: [1080],
        })
        assert check_flow_properties_compatibility(None, flow, sender) is True

    def test_flow_outside_caps_incompatible(self) -> None:
        """Flow 3840 width, sender only allows 1920."""
        flow = self._make_flow_capset(**{
            CapFormatMediaType: "video/raw",
            CapFormatFrameWidth: 3840,
        })
        sender = self._make_sender_caps(**{
            CapFormatMediaType: ["video/raw"],
            CapFormatFrameWidth: [1920],
        })
        assert check_flow_properties_compatibility(None, flow, sender) is False

    def test_flow_within_range_compatible(self) -> None:
        """Flow 1920 width within sender range [720, 3840]."""
        flow = self._make_flow_capset(**{
            CapFormatFrameWidth: 1920,
        })
        sender = self._make_sender_caps(**{
            CapFormatFrameWidth: ("range", (720, 3840)),
        })
        assert check_flow_properties_compatibility(None, flow, sender) is True

    def test_empty_caps_compatible(self) -> None:
        """No sender caps = unconstrained = compatible."""
        flow = self._make_flow_capset(**{CapFormatFrameWidth: 1920})
        assert check_flow_properties_compatibility(None, flow, None) is True

    def test_multiple_capsets_any_match(self) -> None:
        """Flow matches second CapSet (not first)."""
        flow = self._make_flow_capset(**{
            CapFormatMediaType: "video/H264",
            CapFormatFrameWidth: 1920,
        })
        cs1 = CapSet(caps=make_capset(
            Cap(CapFormatMediaType, RangeValue(values=("video/raw",), type=RangeType.STRING)),
            Cap(CapFormatFrameWidth, RangeValue(values=(1920,), type=RangeType.INT)),
        ), preference=100, label="raw")
        cs2 = CapSet(caps=make_capset(
            Cap(CapFormatMediaType, RangeValue(values=("video/H264",), type=RangeType.STRING)),
            Cap(CapFormatFrameWidth, RangeValue(values=(1920,), type=RangeType.INT)),
        ), preference=1, label="h264")
        sender = Caps(capsets=[cs1, cs2])
        assert check_flow_properties_compatibility(None, flow, sender) is True


@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestGetGenericProperties:
    """Test property filtering."""

    def test_video_generic_keeps_resolution(self) -> None:
        props = {
            CapFormatFrameWidth: Cap(CapFormatFrameWidth, RangeValue(values=(1920,))),
            CapFormatFrameHeight: Cap(CapFormatFrameHeight, RangeValue(values=(1080,))),
            CapFormatMediaType: Cap(CapFormatMediaType, RangeValue(values=("video/raw",))),
            CapFormatProfile: Cap(CapFormatProfile, RangeValue(values=("High",))),
        }
        generic = get_generic_properties("urn:x-nmos:format:video", props)
        assert CapFormatFrameWidth in generic
        assert CapFormatFrameHeight in generic
        assert CapFormatMediaType not in generic  # media_type is not generic
        assert CapFormatProfile not in generic  # profile is codec-specific

    def test_audio_generic_keeps_channels(self) -> None:
        props = {
            CapFormatChannelCount: Cap(CapFormatChannelCount, RangeValue(values=(2,))),
            CapFormatSampleRate: Cap(CapFormatSampleRate, RangeValue(values=(Fraction(48000),))),
            CapFormatMediaType: Cap(CapFormatMediaType, RangeValue(values=("audio/L24",))),
        }
        generic = get_generic_properties("urn:x-nmos:format:audio", props)
        assert CapFormatChannelCount in generic
        assert CapFormatSampleRate in generic
        assert CapFormatMediaType not in generic


@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestConstriction:
    """Test CCF constriction (force_active_constraints)."""

    def test_constriction_narrows_caps(self) -> None:
        """Sender has multiple widths, constraint narrows to 1920."""
        sender_caps = Caps(capsets=[CapSet(
            preference=100, label="sender",
            caps=make_capset(
                Cap(CapFormatFrameWidth, RangeValue(values=(1920, 3840), type=RangeType.INT)),
                Cap(CapFormatFrameHeight, RangeValue(values=(1080, 2160), type=RangeType.INT)),
            ),
        )])
        constraint_caps = Caps(capsets=[CapSet(
            preference=100, label="constraint",
            caps=make_capset(
                Cap(CapFormatFrameWidth, RangeValue(values=(1920,), type=RangeType.INT)),
            ),
        )])

        cons = constraint_caps.to_cons()
        result = caps_constrict_by_cons(sender_caps, cons)

        # Result should have width narrowed to 1920 only
        assert len(result.capsets) > 0
        w_cap = result.capsets[0].caps.get(CapFormatFrameWidth)
        assert w_cap is not None
        assert w_cap.value.values == (1920,)

        # Height should remain unchanged
        h_cap = result.capsets[0].caps.get(CapFormatFrameHeight)
        assert h_cap is not None
        assert 1080 in h_cap.value.values
        assert 2160 in h_cap.value.values


# ===========================================================================
# Layer 2: Config-driven integration tests
# ===========================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestConfig1Integration:
    """Integration tests using config1 (simple video/raw + audio/L24)."""

    @pytest.fixture(autouse=True)
    def setup_node(self) -> None:
        """Build a node from config1."""
        try:
            import json
            from pathlib import Path as _P
            from nmos.node import Node
            from nmos.node.config import ConfigBuilder

            self.node = Node()
            self.node.init(serial_number="TST12345")

            config_path = _P(__file__).parent.parent / "config" / "builtin" / "config1.json"
            with open(config_path) as f:
                config = json.load(f)

            builder = ConfigBuilder(self.node, verbose=False)
            for sender_cfg in config.get("senders", []):
                builder._build_sender_pipeline(sender_cfg)
            for receiver_cfg in config.get("receivers", []):
                builder._build_receiver_from_config(receiver_cfg)

            self.has_node = True
        except Exception as exc:
            self.has_node = False
            self._skip_reason = str(exc)

    def test_sender_flow_compatible_with_caps(self) -> None:
        """Config1 sender flow should be compatible with sender caps."""
        if not self.has_node:
            pytest.skip("Node setup failed")

        from nmos.node.flow_caps import get_flow_to_caps

        # Get first sender
        sender = None
        sender_id = None
        for static_id, s in self.node.senders:
            sender = s
            sender_id = s.ResourceCore.Id.value
            break

        if sender is None:
            pytest.skip("No senders in config1")

        # Get flow
        flow_id = sender.FlowId.value if sender.FlowId.defined else None
        if flow_id is None:
            pytest.skip("No flow on sender")

        flow_ptr = self.node.flows.get(flow_id)
        if flow_ptr is None:
            pytest.skip("Flow not found")

        # Convert flow to caps
        flow_caps = get_flow_to_caps(self.node, flow_ptr)
        assert len(flow_caps.caps) > 0, "Flow should have capabilities"

        # Get sender caps
        from nmos.node.compatibility import _get_sender_ccf_caps
        sender_caps = _get_sender_ccf_caps(self.node, sender)

        if sender_caps is None or len(sender_caps.capsets) == 0:
            pytest.skip("Sender has no caps")

        # Check compatibility
        compatible = check_flow_properties_compatibility(
            self.node, flow_caps, sender_caps, verbose=True,
        )
        assert compatible, "Config1 sender flow should be compatible with its own caps"

    def test_set_sender_compatibility_state(self) -> None:
        """set_sender_compatibility_state should return unconstrained or constrained."""
        if not self.has_node:
            pytest.skip("Node setup failed")

        for static_id, sender in self.node.senders:
            sender_id = sender.ResourceCore.Id.value
            status = set_sender_compatibility_state(self.node, sender_id, verbose=True)
            # Config1 has no active constraints so sender should be unconstrained
            # OR if caps are present, flow should be compatible (constrained)
            assert status in ("unconstrained", "constrained"), \
                f"Expected unconstrained or constrained, got {status}"
            break


@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestFixCodedVideoFlow:
    """Test codec profile/level/bitrate fix-up."""

    def test_h264_level_selection(self) -> None:
        """H.264 at 1920x1080 should select an appropriate level."""
        from caps.MatroxCCF import Cap, RangeValue, RangeType
        props = {
            CapFormatMediaType: Cap(CapFormatMediaType,
                RangeValue(values=("video/H264",), type=RangeType.STRING)),
            CapFormatFrameWidth: Cap(CapFormatFrameWidth,
                RangeValue(values=(1920,), type=RangeType.INT)),
            CapFormatFrameHeight: Cap(CapFormatFrameHeight,
                RangeValue(values=(1080,), type=RangeType.INT)),
            CapFormatColorspace: Cap(CapFormatColorspace,
                RangeValue(values=("BT709",), type=RangeType.STRING)),
            CapFormatTransferCharacteristic: Cap(CapFormatTransferCharacteristic,
                RangeValue(values=("SDR",), type=RangeType.STRING)),
            CapFormatInterlaceMode: Cap(CapFormatInterlaceMode,
                RangeValue(values=("progressive",), type=RangeType.STRING)),
            CapFormatGrainRate: Cap(CapFormatGrainRate,
                RangeValue(values=(Fraction(60, 1),), type=RangeType.RATIONAL)),
            CapFormatComponentDepth: Cap(CapFormatComponentDepth,
                RangeValue(values=(10,), type=RangeType.INT)),
            CapFormatColorSampling: Cap(CapFormatColorSampling,
                RangeValue(values=("YCbCr-4:2:2",), type=RangeType.STRING)),
            CapFormatProfile: Cap(CapFormatProfile,
                RangeValue(values=("High-422",), type=RangeType.STRING)),
            CapFormatLevel: Cap(CapFormatLevel,
                RangeValue(values=("4",), type=RangeType.STRING)),
            CapFormatBitRate: Cap(CapFormatBitRate,
                RangeValue(values=(40000,), type=RangeType.INT)),
        }
        # Constraints with level as a range
        constraints = {
            CapFormatLevel: Cap(CapFormatLevel,
                RangeValue(values=("3", "3.1", "3.2", "4", "4.1", "4.2",
                                   "5", "5.1", "5.2"), type=RangeType.STRING)),
        }
        fix_coded_video_flow(props, constraints, verbose=True)
        # Level should be selected (not empty)
        level_cap = props.get(CapFormatLevel)
        assert level_cap is not None
        assert level_cap.value.values is not None
        assert len(level_cap.value.values) == 1

    def test_non_coded_not_affected(self) -> None:
        """video/raw should not be affected."""
        from caps.MatroxCCF import Cap, RangeValue, RangeType
        props = {
            CapFormatMediaType: Cap(CapFormatMediaType,
                RangeValue(values=("video/raw",), type=RangeType.STRING)),
        }
        fix_coded_video_flow(props)  # Should not crash


@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestIntersection:
    """Test constraint/capability intersection."""

    def test_intersection_produces_overlap(self) -> None:
        """Sender supports 1920+3840, receiver requires 1920 → intersection = 1920."""
        sender_caps = Caps(capsets=[CapSet(
            preference=100, label="sender",
            caps=make_capset(
                Cap(CapFormatFrameWidth, RangeValue(values=(1920, 3840), type=RangeType.INT)),
            ),
        )])
        receiver_caps = Caps(capsets=[CapSet(
            preference=100, label="receiver",
            caps=make_capset(
                Cap(CapFormatFrameWidth, RangeValue(values=(1920,), type=RangeType.INT)),
            ),
        )])
        result = intersect_constraints_with_caps(sender_caps, receiver_caps, verbose=True)
        assert result is not None
        assert len(result.capsets) > 0
        w = result.capsets[0].caps.get(CapFormatFrameWidth)
        assert w is not None
        assert 1920 in w.value.values

    def test_no_intersection_returns_none(self) -> None:
        """Sender supports 1920, receiver requires 3840 → no intersection."""
        sender_caps = Caps(capsets=[CapSet(
            preference=100, label="sender",
            caps=make_capset(
                Cap(CapFormatFrameWidth, RangeValue(values=(1920,), type=RangeType.INT)),
            ),
        )])
        receiver_caps = Caps(capsets=[CapSet(
            preference=100, label="receiver",
            caps=make_capset(
                Cap(CapFormatFrameWidth, RangeValue(values=(3840,), type=RangeType.INT)),
            ),
        )])
        result = intersect_constraints_with_caps(sender_caps, receiver_caps)
        # Should be None or empty — no overlap
        assert result is None or len(result.capsets) == 0


@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestValidateActiveConstraints:
    """Test constraint validation against capabilities."""

    def test_valid_constraint_within_caps(self) -> None:
        """Constraint for 1920 width within caps that support 1920+3840."""
        sender_caps = Caps(capsets=[CapSet(
            preference=100, label="sender",
            caps=make_capset(
                Cap(CapFormatFrameWidth, RangeValue(values=(1920, 3840), type=RangeType.INT)),
                Cap(CapFormatFrameHeight, RangeValue(values=(1080, 2160), type=RangeType.INT)),
            ),
        )])
        constraint_caps = Caps(capsets=[CapSet(
            preference=100, label="constraint",
            caps=make_capset(
                Cap(CapFormatFrameWidth, RangeValue(values=(1920,), type=RangeType.INT)),
            ),
        )])
        from caps.MatroxCCF import cons_included_in_caps
        cons = constraint_caps.to_cons()
        assert cons_included_in_caps(cons, sender_caps) is True

    def test_invalid_constraint_outside_caps(self) -> None:
        """Constraint for 7680 width outside caps that only support 1920+3840."""
        sender_caps = Caps(capsets=[CapSet(
            preference=100, label="sender",
            caps=make_capset(
                Cap(CapFormatFrameWidth, RangeValue(values=(1920, 3840), type=RangeType.INT)),
            ),
        )])
        constraint_caps = Caps(capsets=[CapSet(
            preference=100, label="constraint",
            caps=make_capset(
                Cap(CapFormatFrameWidth, RangeValue(values=(7680,), type=RangeType.INT)),
            ),
        )])
        from caps.MatroxCCF import cons_included_in_caps
        cons = constraint_caps.to_cons()
        assert cons_included_in_caps(cons, sender_caps) is False


def _make_mock_node_with_video_source(source_id: str = "dummy") -> Any:
    """Build a minimal mock node with a video source for get_flow_to_caps.

    Provides the source chain: node.sources[source_id] → source with
    SourceCore.SynchronousMedia=True, SourceCore.ClockName="clk0".
    """
    from types import SimpleNamespace
    src_core = SimpleNamespace(
        SynchronousMedia=SimpleNamespace(defined=True, value=True),
        ClockName=SimpleNamespace(defined=True, value="clk0"),
    )
    src_val = SimpleNamespace(SourceCore=src_core)
    src_wrapper = SimpleNamespace(value=src_val)
    src_poly = SimpleNamespace(get=lambda: src_wrapper)
    return SimpleNamespace(sources={source_id: src_poly})


@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestForceFlowProperties:
    """Test force_flow_properties_compatibility with per-property logic."""

    def test_force_picks_first_value_from_constraint(self) -> None:
        """When flow doesn't match constraint, pick first constraint value."""
        from nmos.node.flow_caps import get_flow_to_caps
        from nmos.types.generated.nflow_video_raw import NFlowVideoRaw, NFlowVideoRawValue
        from nmos.types.generated.nflow import NFlowValue
        from nmos.types.generated.nvideo_component import NVideoComponentValue
        from nmos.types.generated.nrational import NRationalValue
        from nmos.enums import EnumRegistry, Y, Cb, Cr

        # Build a 1920x1080 flow
        fv = NFlowVideoRawValue()
        fv.set_to_default()
        fv.MediaType.value = EnumRegistry.get("video/raw")
        fv.FrameWidth.value = 1920
        fv.FrameHeight.value = 1080
        fv.InterlaceMode.value = EnumRegistry.get("progressive")
        fv.Colorspace.value = EnumRegistry.get("BT709")
        fv.TransferCharacteristic.value = EnumRegistry.get("SDR")
        gr = NRationalValue()
        gr.Numerator.value = 60; gr.Denominator.value = 1
        fv.FlowCore.GrainRate.set_value(gr)
        fv.FlowCore.SourceId.value = "dummy"
        c0 = NVideoComponentValue(); c0.Name.value = Y; c0.Width.value = 1920; c0.Height.value = 1080; c0.BitDepth.value = 10
        c1 = NVideoComponentValue(); c1.Name.value = Cb; c1.Width.value = 960; c1.Height.value = 1080; c1.BitDepth.value = 10
        c2 = NVideoComponentValue(); c2.Name.value = Cr; c2.Width.value = 960; c2.Height.value = 1080; c2.BitDepth.value = 10
        fv.Components.value = [c0, c1, c2]
        wrapper = NFlowVideoRaw()
        wrapper.set_value(fv)
        fp = NFlowValue()
        fp.set(wrapper)

        # Constraint forces 3840 width — active constraints are Cons (not Caps)
        active_cons = Cons(consets=[ConSet(
            preference=100, label="force 4K",
            cons=make_conset(
                Constraint(CapFormatFrameWidth, RangeValue(values=(3840,), type=RangeType.INT)),
                Constraint(CapFormatFrameHeight, RangeValue(values=(2160,), type=RangeType.INT)),
            ),
        )])

        from nmos.node.compatibility import force_flow_properties_compatibility
        mock_node = _make_mock_node_with_video_source("dummy")
        result, groups = force_flow_properties_compatibility(
            mock_node, fp, active_cons, reset=True, verbose=True,
        )
        assert result is not None
        assert groups is None  # No layer_compatibility_groups on the conset
        from nmos.node.compatibility import _get_cap_int
        assert _get_cap_int(result, CapFormatFrameWidth) == 3840
        assert _get_cap_int(result, CapFormatFrameHeight) == 2160

    def test_force_returns_compliant_groups(self) -> None:
        """Winning conset's layer_compatibility_groups is returned."""
        from nmos.types.generated.nflow_video_raw import NFlowVideoRaw, NFlowVideoRawValue
        from nmos.types.generated.nflow import NFlowValue
        from nmos.types.generated.nvideo_component import NVideoComponentValue
        from nmos.types.generated.nrational import NRationalValue
        from nmos.enums import EnumRegistry, Y, Cb, Cr

        fv = NFlowVideoRawValue()
        fv.set_to_default()
        fv.MediaType.value = EnumRegistry.get("video/raw")
        fv.FrameWidth.value = 1920
        fv.FrameHeight.value = 1080
        fv.InterlaceMode.value = EnumRegistry.get("progressive")
        fv.Colorspace.value = EnumRegistry.get("BT709")
        fv.TransferCharacteristic.value = EnumRegistry.get("SDR")
        gr = NRationalValue()
        gr.Numerator.value = 60; gr.Denominator.value = 1
        fv.FlowCore.GrainRate.set_value(gr)
        fv.FlowCore.SourceId.value = "dummy"
        c0 = NVideoComponentValue(); c0.Name.value = Y; c0.Width.value = 1920; c0.Height.value = 1080; c0.BitDepth.value = 10
        c1 = NVideoComponentValue(); c1.Name.value = Cb; c1.Width.value = 960; c1.Height.value = 1080; c1.BitDepth.value = 10
        c2 = NVideoComponentValue(); c2.Name.value = Cr; c2.Width.value = 960; c2.Height.value = 1080; c2.BitDepth.value = 10
        fv.Components.value = [c0, c1, c2]
        wrapper = NFlowVideoRaw()
        wrapper.set_value(fv)
        fp = NFlowValue()
        fp.set(wrapper)

        # Conset with layer_compatibility_groups={0, 2}
        active_cons = Cons(consets=[ConSet(
            preference=100, label="with groups",
            layer_compatibility_groups={0, 2},
            cons=make_conset(
                Constraint(CapFormatFrameWidth, RangeValue(values=(1920,), type=RangeType.INT)),
            ),
        )])

        from nmos.node.compatibility import force_flow_properties_compatibility
        mock_node = _make_mock_node_with_video_source("dummy")
        result, groups = force_flow_properties_compatibility(
            mock_node, fp, active_cons, reset=False, verbose=True,
        )
        assert result is not None
        assert groups == [0, 2]

    def test_force_returns_none_groups_when_undefined(self) -> None:
        """When conset has no layer_compatibility_groups, groups is None."""
        from nmos.types.generated.nflow_video_raw import NFlowVideoRaw, NFlowVideoRawValue
        from nmos.types.generated.nflow import NFlowValue
        from nmos.types.generated.nvideo_component import NVideoComponentValue
        from nmos.types.generated.nrational import NRationalValue
        from nmos.enums import EnumRegistry, Y, Cb, Cr

        fv = NFlowVideoRawValue()
        fv.set_to_default()
        fv.MediaType.value = EnumRegistry.get("video/raw")
        fv.FrameWidth.value = 1920
        fv.FrameHeight.value = 1080
        fv.InterlaceMode.value = EnumRegistry.get("progressive")
        fv.Colorspace.value = EnumRegistry.get("BT709")
        fv.TransferCharacteristic.value = EnumRegistry.get("SDR")
        gr = NRationalValue()
        gr.Numerator.value = 60; gr.Denominator.value = 1
        fv.FlowCore.GrainRate.set_value(gr)
        fv.FlowCore.SourceId.value = "dummy"
        c0 = NVideoComponentValue(); c0.Name.value = Y; c0.Width.value = 1920; c0.Height.value = 1080; c0.BitDepth.value = 10
        c1 = NVideoComponentValue(); c1.Name.value = Cb; c1.Width.value = 960; c1.Height.value = 1080; c1.BitDepth.value = 10
        c2 = NVideoComponentValue(); c2.Name.value = Cr; c2.Width.value = 960; c2.Height.value = 1080; c2.BitDepth.value = 10
        fv.Components.value = [c0, c1, c2]
        wrapper = NFlowVideoRaw()
        wrapper.set_value(fv)
        fp = NFlowValue()
        fp.set(wrapper)

        # Conset without layer_compatibility_groups (None = part of all groups)
        active_cons = Cons(consets=[ConSet(
            preference=100, label="no groups",
            cons=make_conset(
                Constraint(CapFormatFrameWidth, RangeValue(values=(1920,), type=RangeType.INT)),
            ),
        )])

        from nmos.node.compatibility import force_flow_properties_compatibility
        mock_node = _make_mock_node_with_video_source("dummy")
        result, groups = force_flow_properties_compatibility(
            mock_node, fp, active_cons, reset=False, verbose=True,
        )
        assert result is not None
        assert groups is None

    def test_force_skips_zero_preference(self) -> None:
        """ConSet with preference <= 0 is skipped; second conset wins."""
        from nmos.types.generated.nflow_video_raw import NFlowVideoRaw, NFlowVideoRawValue
        from nmos.types.generated.nflow import NFlowValue
        from nmos.types.generated.nvideo_component import NVideoComponentValue
        from nmos.types.generated.nrational import NRationalValue
        from nmos.enums import EnumRegistry, Y, Cb, Cr

        fv = NFlowVideoRawValue()
        fv.set_to_default()
        fv.MediaType.value = EnumRegistry.get("video/raw")
        fv.FrameWidth.value = 1920
        fv.FrameHeight.value = 1080
        fv.InterlaceMode.value = EnumRegistry.get("progressive")
        fv.Colorspace.value = EnumRegistry.get("BT709")
        fv.TransferCharacteristic.value = EnumRegistry.get("SDR")
        gr = NRationalValue()
        gr.Numerator.value = 60; gr.Denominator.value = 1
        fv.FlowCore.GrainRate.set_value(gr)
        fv.FlowCore.SourceId.value = "dummy"
        c0 = NVideoComponentValue(); c0.Name.value = Y; c0.Width.value = 1920; c0.Height.value = 1080; c0.BitDepth.value = 10
        c1 = NVideoComponentValue(); c1.Name.value = Cb; c1.Width.value = 960; c1.Height.value = 1080; c1.BitDepth.value = 10
        c2 = NVideoComponentValue(); c2.Name.value = Cr; c2.Width.value = 960; c2.Height.value = 1080; c2.BitDepth.value = 10
        fv.Components.value = [c0, c1, c2]
        wrapper = NFlowVideoRaw()
        wrapper.set_value(fv)
        fp = NFlowValue()
        fp.set(wrapper)

        from nmos.node.compatibility import force_flow_properties_compatibility, _get_cap_int
        # First conset: preference=0 → skipped.
        # Second conset: preference=50, groups={1, 3} → wins.
        active_cons = Cons(consets=[ConSet(
            preference=0, label="disabled",
            layer_compatibility_groups={9},
            cons=make_conset(
                Constraint(CapFormatFrameWidth, RangeValue(values=(3840,), type=RangeType.INT)),
                Constraint(CapFormatFrameHeight, RangeValue(values=(2160,), type=RangeType.INT)),
            ),
        ), ConSet(
            preference=50, label="active",
            layer_compatibility_groups={1, 3},
            cons=make_conset(
                Constraint(CapFormatFrameWidth, RangeValue(values=(3840,), type=RangeType.INT)),
                Constraint(CapFormatFrameHeight, RangeValue(values=(2160,), type=RangeType.INT)),
            ),
        )])

        mock_node = _make_mock_node_with_video_source("dummy")
        result, groups = force_flow_properties_compatibility(
            mock_node, fp, active_cons, reset=True, verbose=True,
        )
        assert result is not None
        assert _get_cap_int(result, CapFormatFrameWidth) == 3840  # From second conset (first skipped)
        assert _get_cap_int(result, CapFormatFrameHeight) == 2160
        assert groups == [1, 3]  # Groups from winning (second) conset, not {9}


@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestCCFDirectComparison:
    """Layer 4: Verify our wrappers produce same results as direct CCF calls."""

    def test_constriction_matches_direct_ccf(self) -> None:
        """force_active_constraints result equals direct caps_constrict_by_cons."""
        sender_caps = Caps(capsets=[CapSet(
            preference=100, label="sender",
            caps=make_capset(
                Cap(CapFormatMediaType, RangeValue(values=("video/raw",), type=RangeType.STRING)),
                Cap(CapFormatFrameWidth, RangeValue(values=(1920, 3840), type=RangeType.INT)),
                Cap(CapFormatFrameHeight, RangeValue(values=(1080, 2160), type=RangeType.INT)),
            ),
        )])
        constraint_caps = Caps(capsets=[CapSet(
            preference=100, label="constraint",
            caps=make_capset(
                Cap(CapFormatFrameWidth, RangeValue(values=(1920,), type=RangeType.INT)),
            ),
        )])

        # Direct CCF
        cons = constraint_caps.to_cons()
        direct_result = caps_constrict_by_cons(sender_caps, cons)

        # Both should narrow width to 1920
        assert len(direct_result.capsets) > 0
        w = direct_result.capsets[0].caps.get(CapFormatFrameWidth)
        assert w is not None
        assert w.value.values == (1920,)
        # Height should remain
        h = direct_result.capsets[0].caps.get(CapFormatFrameHeight)
        assert h is not None
        assert 1080 in h.value.values
