# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for codec profile/level validation and level selection.

Covers:
- Data table integrity (entry counts, spot-check values)
- get_sdp_color_sampling helper
- Per-codec validation functions (H.264, H.265, JXSV, AAC)
- Automatic level selection from coded flow objects
"""

from __future__ import annotations

import pytest

from nmos.enums import (
    EnumRegistry,
    Progressive,
    # video components, in IS-04 flow_video_raw enum order
    Y, Cb, Cr, I, Ct, Cp, A, R, G, B,
    # H.264
    H264ProfileBaseline,
    H264ProfileHigh,
    H264ProfileHigh10,
    H264ProfileHigh_422,
    H264ProfileHighPredictive_444,
    CodecProfileMain,
    CodecLevel1,
    CodecLevel3,
    CodecLevel4,
    CodecLevel4_1,
    CodecLevel4_2,
    CodecLevel5,
    # H.265
    H265ProfileMain10,
    H265LevelMain4,
    H265LevelMain5,
    # JXSV
    JxsvProfileMain444_12,
    JxsvLevel4k1,
    JxsvSublevel4bpp,
    JxsvSublevel6bpp,
    # AAC
    AacProfileAAC,
    AacProfileScalable,
    AacProfileSpeech,
    AacProfileLowDelayAAC,
    CodecLevel2,
)
from nmos.errors import InvalidParameter, NotAllowed, NotAvailable

from nmos.codec import aac, h264, h265, jxsv
from nmos.node.codec import (
    get_sdp_color_sampling,
    check_h264_profile,
    check_h264_profile_level,
    get_h264_max_bitrate,
    select_h264_level_from_coded_flow,
    check_h265_profile,
    check_h265_profile_level,
    get_h265_max_bitrate,
    select_h265_level_from_coded_flow,
    check_jxsv_profile,
    check_jxsv_profile_level,
    get_jxsv_max_bitrate,
    select_jxsv_level_from_coded_flow,
    check_aac_profile,
    check_aac_profile_level,
    select_aac_level_from_coded_flow,
)

from nmos.types.generated.nflow_video_coded import NFlowVideoCodedValue
from nmos.types.generated.nflow_audio_coded import NFlowAudioCodedValue
from nmos.types.generated.nsource_audio import NSourceAudioValue
from nmos.types.generated.nvideo_component import NVideoComponentValue
from nmos.types.generated.nrational import NRationalValue
from nmos.types.generated.naudio_channel import NAudioChannelValue


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

InterlacedTff = EnumRegistry.get("interlaced_tff")
BT709 = EnumRegistry.get("BT709")


def _make_component(name: object, width: int, height: int, bit_depth: int) -> NVideoComponentValue:
    """Create a video component with the given properties."""
    c = NVideoComponentValue()
    c.Name.value = name
    c.Width.value = width
    c.Height.value = height
    c.BitDepth.value = bit_depth
    return c


def _make_ycbcr_444_components(bit_depth: int = 8) -> list[NVideoComponentValue]:
    """Create 3 YCbCr components at 4:4:4 sub-sampling."""
    return [
        _make_component(Y, 1920, 1080, bit_depth),
        _make_component(Cb, 1920, 1080, bit_depth),
        _make_component(Cr, 1920, 1080, bit_depth),
    ]


def _make_ycbcr_422_components(bit_depth: int = 8) -> list[NVideoComponentValue]:
    """Create 3 YCbCr components at 4:2:2 sub-sampling."""
    return [
        _make_component(Y, 1920, 1080, bit_depth),
        _make_component(Cb, 960, 1080, bit_depth),
        _make_component(Cr, 960, 1080, bit_depth),
    ]


def _make_ycbcr_420_components(bit_depth: int = 8) -> list[NVideoComponentValue]:
    """Create 3 YCbCr components at 4:2:0 sub-sampling."""
    return [
        _make_component(Y, 1920, 1080, bit_depth),
        _make_component(Cb, 960, 540, bit_depth),
        _make_component(Cr, 960, 540, bit_depth),
    ]


def _make_rgb_components(bit_depth: int = 8) -> list[NVideoComponentValue]:
    """Create 3 RGB components."""
    return [
        _make_component(R, 1920, 1080, bit_depth),
        _make_component(G, 1920, 1080, bit_depth),
        _make_component(B, 1920, 1080, bit_depth),
    ]


def _make_grain_rate(num: int, den: int = 1) -> NRationalValue:
    """Create a rational value representing a frame/sampling rate."""
    r = NRationalValue()
    r.Numerator.value = num
    r.Denominator.value = den
    return r


def _make_video_flow(
    width: int,
    height: int,
    components: list[NVideoComponentValue],
    grain_rate_num: int,
    grain_rate_den: int,
    profile: object,
    bit_rate: int,
    sublevel: object | None = None,
) -> NFlowVideoCodedValue:
    """Create a video coded flow with the given properties."""
    flow = NFlowVideoCodedValue()
    flow.FrameWidth.value = width
    flow.FrameHeight.value = height
    flow.InterlaceMode.value = Progressive
    flow.Components.value = components
    flow.FlowCore.GrainRate.set_value(_make_grain_rate(grain_rate_num, grain_rate_den))
    flow.Profile.value = profile
    flow.Bitrate.value = bit_rate
    if sublevel is not None:
        flow.Sublevel.value = sublevel
    return flow


def _make_audio_channel(label: str = "L") -> NAudioChannelValue:
    """Create an audio channel value."""
    c = NAudioChannelValue()
    c.Label.value = label
    return c


# ===========================================================================
# Data table integrity tests
# ===========================================================================

class TestH264DataTables:
    """Verify H.264 data table structure and spot-check values."""

    def test_level_count(self) -> None:
        assert len(h264.ALL_LEVELS) == 20

    def test_profile_count(self) -> None:
        assert len(h264.ALL_PROFILES) == 15

    def test_ordered_levels_count(self) -> None:
        assert len(h264.ORDERED_LEVELS) == 20

    def test_profile_bitrate_attr_keys_match_profiles(self) -> None:
        assert set(h264.PROFILE_BITRATE_ATTR.keys()) == set(h264.ALL_PROFILES.keys())

    def test_spot_check_level_4_2(self) -> None:
        info = h264.ALL_LEVELS[CodecLevel4_2]
        assert info.max_rate == 522240
        assert info.max_size == 8704

    def test_spot_check_level_1(self) -> None:
        info = h264.ALL_LEVELS[CodecLevel1]
        assert info.max_rate == 1485
        assert info.max_size == 99
        assert info.max_bitrate_baseline == 64


class TestH265DataTables:
    """Verify H.265 data table structure and spot-check values."""

    def test_level_count(self) -> None:
        assert len(h265.ALL_LEVELS) == 22

    def test_profile_count(self) -> None:
        assert len(h265.ALL_PROFILES) == 36

    def test_ordered_levels_count(self) -> None:
        assert len(h265.ORDERED_LEVELS) == 22

    def test_profile_bitrate_attr_keys_match_profiles(self) -> None:
        assert set(h265.PROFILE_BITRATE_ATTR.keys()) == set(h265.ALL_PROFILES.keys())

    def test_spot_check_level_main4(self) -> None:
        info = h265.ALL_LEVELS[H265LevelMain4]
        assert info.high_tier is False
        assert info.max_rate == 66846720
        assert info.max_size == 2228224


class TestJxsvDataTables:
    """Verify JXSV data table structure and spot-check values."""

    def test_level_count(self) -> None:
        assert len(jxsv.ALL_LEVELS) == 8

    def test_profile_count(self) -> None:
        # Main/High 420/444/4444 plus the TDC profile
        assert len(jxsv.ALL_PROFILES) == 7

    def test_ordered_levels_count(self) -> None:
        assert len(jxsv.ORDERED_LEVELS) == 8

    def test_sublevel_bitrate_attr_completeness(self) -> None:
        assert len(jxsv.SUBLEVEL_BITRATE_ATTR) == 6

    def test_spot_check_level_4k1(self) -> None:
        info = jxsv.ALL_LEVELS[JxsvLevel4k1]
        assert info.max_rate == 267386880
        assert info.max_size == 8912896
        assert info.max_bitrate_sublevel_4bpp == 1069548


class TestAacDataTables:
    """Verify AAC data table structure and spot-check values."""

    def test_profile_count(self) -> None:
        assert len(aac.ALL_PROFILES) == 14

    def test_ordered_levels_count(self) -> None:
        assert len(aac.ORDERED_LEVELS) == 8

    def test_ordered_multi_levels(self) -> None:
        assert aac.ORDERED_MULTI_LEVELS == [CodecLevel2, EnumRegistry.get("4")]

    def test_ordered_stereo_levels(self) -> None:
        assert aac.ORDERED_STEREO_LEVELS == [CodecLevel1]

    def test_speech_profile_objects(self) -> None:
        info = aac.ALL_PROFILES[AacProfileSpeech]
        assert info.objects == [0, 8, 9, 12]
        assert info.max_bitrate_per_channel == 288

    def test_speech_profile_level1(self) -> None:
        info = aac.ALL_PROFILES[AacProfileSpeech]
        level_info = info.levels[CodecLevel1]
        assert level_info.max_rate == 48000
        assert level_info.max_channels == 2


# ===========================================================================
# get_sdp_color_sampling tests
# ===========================================================================

class TestGetSdpColorSampling:
    """Test color sampling detection from video components."""

    def test_ycbcr_444(self) -> None:
        comps = _make_ycbcr_444_components()
        assert get_sdp_color_sampling(comps) == "YCbCr-4:4:4"

    def test_ycbcr_422(self) -> None:
        comps = _make_ycbcr_422_components()
        assert get_sdp_color_sampling(comps) == "YCbCr-4:2:2"

    def test_ycbcr_420(self) -> None:
        comps = _make_ycbcr_420_components()
        assert get_sdp_color_sampling(comps) == "YCbCr-4:2:0"

    def test_rgb(self) -> None:
        comps = _make_rgb_components()
        assert get_sdp_color_sampling(comps) == "RGB"

    # IS-04's flow_video_raw declares components as a plain array (minItems 1,
    # no maxItems, no tuple form), so neither order nor count is constrained and
    # an undeterminable sampling is reported as None rather than raising.

    def test_empty_components(self) -> None:
        assert get_sdp_color_sampling([]) is None

    def test_two_components(self) -> None:
        comps = _make_ycbcr_444_components()[:2]
        assert get_sdp_color_sampling(comps) is None

    def test_order_does_not_matter(self) -> None:
        y, cb, cr = _make_ycbcr_422_components()
        assert get_sdp_color_sampling([cb, y, cr]) == "YCbCr-4:2:2"

    def test_extra_component_tolerated(self) -> None:
        comps = _make_ycbcr_422_components() + [_make_component(A, 1920, 1080, 8)]
        assert get_sdp_color_sampling(comps) == "YCbCr-4:2:2"

    def test_rgb_requires_equal_planes(self) -> None:
        r, g, b = _make_rgb_components()
        assert get_sdp_color_sampling([r, _make_component(G, 960, 1080, 8), b]) is None

    def test_ycbcr_wins_over_rgb_when_both_present(self) -> None:
        comps = _make_ycbcr_422_components() + _make_rgb_components()
        assert get_sdp_color_sampling(comps) == "YCbCr-4:2:2"

    def test_mismatched_chroma_planes(self) -> None:
        y, cb, _ = _make_ycbcr_422_components()
        assert get_sdp_color_sampling([y, cb, _make_component(Cr, 480, 1080, 8)]) is None

    def test_unrecognised_colour_system(self) -> None:
        comps = [_make_component(I, 1920, 1080, 8),
                 _make_component(Ct, 960, 1080, 8),
                 _make_component(Cp, 960, 1080, 8)]
        assert get_sdp_color_sampling(comps) is None

    def test_luma_need_not_match_frame_dimensions(self) -> None:
        """The ratio is plane-to-plane; IS-04 ties luma to no frame_width."""
        comps = [_make_component(Y, 3840, 2160, 8),
                 _make_component(Cb, 1920, 2160, 8),
                 _make_component(Cr, 1920, 2160, 8)]
        assert get_sdp_color_sampling(comps) == "YCbCr-4:2:2"


# ===========================================================================
# H.264 validation tests
# ===========================================================================

class TestCheckH264Profile:
    """Test H.264 profile validation."""

    def test_valid_baseline_420(self) -> None:
        """Baseline profile with 4:2:0, 8-bit should pass."""
        check_h264_profile(
            1920, 1080, BT709, BT709, Progressive,
            _make_ycbcr_420_components(8), _make_grain_rate(60),
            H264ProfileBaseline, CodecLevel4, 0,
        )

    def test_interlaced_rejected(self) -> None:
        with pytest.raises(InvalidParameter, match="interlaced"):
            check_h264_profile(
                1920, 1080, BT709, BT709, InterlacedTff,
                _make_ycbcr_420_components(8), _make_grain_rate(30),
                H264ProfileBaseline, CodecLevel4, 0,
            )

    def test_bit_depth_too_high(self) -> None:
        """Baseline profile max is 8-bit; 10-bit should fail."""
        with pytest.raises(NotAllowed, match="bit depth"):
            check_h264_profile(
                1920, 1080, BT709, BT709, Progressive,
                _make_ycbcr_420_components(10), _make_grain_rate(60),
                H264ProfileBaseline, CodecLevel4, 0,
            )

    def test_color_sampling_mismatch(self) -> None:
        """Baseline only supports 4:2:0; 4:2:2 should fail."""
        with pytest.raises(NotAllowed, match="color sampling"):
            check_h264_profile(
                1920, 1080, BT709, BT709, Progressive,
                _make_ycbcr_422_components(8), _make_grain_rate(60),
                H264ProfileBaseline, CodecLevel4, 0,
            )

    def test_high_422_allows_422(self) -> None:
        """High-422 profile supports 4:2:2."""
        check_h264_profile(
            1920, 1080, BT709, BT709, Progressive,
            _make_ycbcr_422_components(10), _make_grain_rate(60),
            H264ProfileHigh_422, CodecLevel4, 0,
        )

    def test_rgb_rejected(self) -> None:
        """RGB is not valid for H.264."""
        with pytest.raises(InvalidParameter, match="color sampling"):
            check_h264_profile(
                1920, 1080, BT709, BT709, Progressive,
                _make_rgb_components(8), _make_grain_rate(60),
                H264ProfileBaseline, CodecLevel4, 0,
            )

    def test_invalid_profile(self) -> None:
        with pytest.raises(InvalidParameter, match="invalid profile"):
            check_h264_profile(
                1920, 1080, BT709, BT709, Progressive,
                _make_ycbcr_420_components(8), _make_grain_rate(60),
                EnumRegistry.get("NonExistentProfile"), CodecLevel4, 0,
            )


class TestCheckH264ProfileLevel:
    """Test H.264 profile+level constraint checking."""

    def test_valid(self) -> None:
        """1920×1080 @ 60fps, Baseline, Level 4.2 should pass."""
        check_h264_profile_level(
            1920, 1080, BT709, BT709, Progressive,
            _make_ycbcr_420_components(8), _make_grain_rate(60),
            H264ProfileBaseline, CodecLevel4_2, 10000,
        )

    def test_bitrate_too_high(self) -> None:
        """Level 4.2 Baseline max is 50000 Kbps."""
        with pytest.raises(NotAllowed):
            check_h264_profile_level(
                1920, 1080, BT709, BT709, Progressive,
                _make_ycbcr_420_components(8), _make_grain_rate(60),
                H264ProfileBaseline, CodecLevel4_2, 60000,
            )

    def test_frame_too_large(self) -> None:
        """Level 1 has max_size=99 macroblocks — 1920×1080 exceeds it."""
        with pytest.raises(NotAllowed):
            check_h264_profile_level(
                1920, 1080, BT709, BT709, Progressive,
                _make_ycbcr_420_components(8), _make_grain_rate(30),
                H264ProfileBaseline, CodecLevel1, 10,
            )


class TestGetH264MaxBitrate:
    """Test H.264 max bitrate retrieval."""

    def test_baseline_level_4_2(self) -> None:
        result = get_h264_max_bitrate(
            1920, 1080, BT709, BT709, Progressive,
            _make_ycbcr_420_components(8), _make_grain_rate(60),
            H264ProfileBaseline, CodecLevel4_2,
        )
        assert result == 50000

    def test_high_level_4_2(self) -> None:
        result = get_h264_max_bitrate(
            1920, 1080, BT709, BT709, Progressive,
            _make_ycbcr_420_components(8), _make_grain_rate(60),
            H264ProfileHigh, CodecLevel4_2,
        )
        assert result == 62500


class TestSelectH264Level:
    """Test automatic H.264 level selection from coded flow."""

    def test_selects_appropriate_level(self) -> None:
        """1920×1080 @ 30fps, Baseline, 10000 Kbps should select a reasonable level."""
        flow = _make_video_flow(
            1920, 1080,
            _make_ycbcr_420_components(8),
            30, 1, H264ProfileBaseline, 10000,
        )
        select_h264_level_from_coded_flow(flow)
        # Should select Level 3.1 or higher — 1920×1080 = 120×68 = 8160 macroblocks
        assert flow.Level.defined

    def test_impossible_constraints(self) -> None:
        """Extremely high bitrate with Level 1 constraints should fail."""
        flow = _make_video_flow(
            7680, 4320,
            _make_ycbcr_420_components(8),
            120, 1, H264ProfileBaseline, 999999999,
        )
        with pytest.raises(NotAllowed, match="cannot find"):
            select_h264_level_from_coded_flow(flow)


# ===========================================================================
# H.265 validation tests
# ===========================================================================

class TestCheckH265Profile:
    """Test H.265 profile validation."""

    def test_valid_main10_420(self) -> None:
        check_h265_profile(
            1920, 1080, BT709, BT709, Progressive,
            _make_ycbcr_420_components(10), _make_grain_rate(60),
            H265ProfileMain10, H265LevelMain4, 0,
        )

    def test_interlaced_rejected(self) -> None:
        with pytest.raises(InvalidParameter, match="interlaced"):
            check_h265_profile(
                1920, 1080, BT709, BT709, InterlacedTff,
                _make_ycbcr_420_components(10), _make_grain_rate(60),
                H265ProfileMain10, H265LevelMain4, 0,
            )


class TestGetH265MaxBitrate:
    """Test H.265 max bitrate retrieval."""

    def test_main10_level_main5(self) -> None:
        result = get_h265_max_bitrate(
            1920, 1080, BT709, BT709, Progressive,
            _make_ycbcr_420_components(10), _make_grain_rate(60),
            H265ProfileMain10, H265LevelMain5,
        )
        assert result == 25000


class TestSelectH265Level:
    """Test automatic H.265 level selection."""

    def test_selects_level(self) -> None:
        flow = _make_video_flow(
            1920, 1080,
            _make_ycbcr_420_components(10),
            60, 1, H265ProfileMain10, 10000,
        )
        select_h265_level_from_coded_flow(flow)
        assert flow.Level.defined


# ===========================================================================
# JXSV validation tests
# ===========================================================================

class TestCheckJxsvProfile:
    """Test JXSV profile validation."""

    def test_valid_main444(self) -> None:
        check_jxsv_profile(
            1920, 1080, BT709, BT709, Progressive,
            _make_ycbcr_444_components(10), _make_grain_rate(60),
            JxsvProfileMain444_12, JxsvLevel4k1, JxsvSublevel4bpp, 0,
        )

    def test_interlaced_rejected(self) -> None:
        with pytest.raises(InvalidParameter, match="interlaced"):
            check_jxsv_profile(
                1920, 1080, BT709, BT709, InterlacedTff,
                _make_ycbcr_444_components(10), _make_grain_rate(60),
                JxsvProfileMain444_12, JxsvLevel4k1, JxsvSublevel4bpp, 0,
            )


class TestGetJxsvMaxBitrate:
    """Test JXSV max bitrate retrieval."""

    def test_level_4k1_sublevel_4bpp(self) -> None:
        result = get_jxsv_max_bitrate(
            1920, 1080, BT709, BT709, Progressive,
            _make_ycbcr_444_components(10), _make_grain_rate(60),
            JxsvProfileMain444_12, JxsvLevel4k1, JxsvSublevel4bpp,
        )
        assert result == 1069548


class TestSelectJxsvLevel:
    """Test automatic JXSV level selection."""

    def test_selects_level(self) -> None:
        flow = _make_video_flow(
            1920, 1080,
            _make_ycbcr_444_components(10),
            60, 1, JxsvProfileMain444_12, 500000,
            sublevel=JxsvSublevel4bpp,
        )
        select_jxsv_level_from_coded_flow(flow)
        assert flow.Level.defined


# ===========================================================================
# AAC validation tests
# ===========================================================================

class TestCheckAacProfile:
    """Test AAC profile validation."""

    def test_valid_speech_stereo(self) -> None:
        check_aac_profile(
            0, 2, _make_grain_rate(48000),
            AacProfileSpeech, CodecLevel1, 400,
        )

    def test_invalid_channels(self) -> None:
        with pytest.raises(InvalidParameter, match="channels"):
            check_aac_profile(
                0, 3, _make_grain_rate(48000),
                AacProfileSpeech, CodecLevel1, 400,
            )

    def test_invalid_object_type(self) -> None:
        """Object type 99 is not in Speech profile."""
        with pytest.raises(NotAllowed, match="objects"):
            check_aac_profile(
                99, 2, _make_grain_rate(48000),
                AacProfileSpeech, CodecLevel1, 400,
            )

    def test_bitrate_too_high(self) -> None:
        """Speech profile: 288 Kbps/ch × 2 channels = 576 max."""
        with pytest.raises(NotAllowed, match="bitrate"):
            check_aac_profile(
                0, 2, _make_grain_rate(48000),
                AacProfileSpeech, CodecLevel1, 600,
            )

    def test_51_channel_bitrate(self) -> None:
        """5.1 surround: 288 × 5 = 1440 max (not 288 × 6)."""
        check_aac_profile(
            0, 6, _make_grain_rate(48000),
            AacProfileScalable, CodecLevel4, 1440,
        )

    def test_51_channel_bitrate_too_high(self) -> None:
        with pytest.raises(NotAllowed, match="bitrate"):
            check_aac_profile(
                0, 6, _make_grain_rate(48000),
                AacProfileScalable, CodecLevel4, 1500,
            )


class TestCheckAacProfileLevel:
    """Test AAC profile+level constraint checking."""

    def test_valid(self) -> None:
        check_aac_profile_level(
            0, 2, _make_grain_rate(48000),
            AacProfileSpeech, CodecLevel1, 400,
        )

    def test_channels_exceed_level(self) -> None:
        """Speech Level 1 allows max 2 channels; 6 should fail."""
        with pytest.raises(NotAllowed, match="channels"):
            check_aac_profile_level(
                0, 6, _make_grain_rate(48000),
                AacProfileSpeech, CodecLevel1, 400,
            )

    def test_sampling_rate_too_high(self) -> None:
        """Speech Level 1 allows max 48000 Hz; 96000 should fail."""
        with pytest.raises(NotAllowed, match="sampling rate"):
            check_aac_profile_level(
                0, 2, _make_grain_rate(96000),
                AacProfileSpeech, CodecLevel1, 400,
            )


class TestSelectAacLevel:
    """Test automatic AAC level selection."""

    def test_selects_level(self) -> None:
        flow = NFlowAudioCodedValue()
        flow.FlowCore.GrainRate.set_value(_make_grain_rate(48000))
        flow.Profile.value = AacProfileAAC
        flow.Bitrate.value = 400

        source = NSourceAudioValue()
        source.Channels.value = [_make_audio_channel("L"), _make_audio_channel("R")]

        select_aac_level_from_coded_flow(flow, source)
        assert flow.Level.defined

    def test_impossible_constraints(self) -> None:
        """LowDelayAAC only supports Level 1 (max 48000, 2ch).
        Requesting 96000 Hz should fail."""
        flow = NFlowAudioCodedValue()
        flow.FlowCore.GrainRate.set_value(_make_grain_rate(96000))
        flow.Profile.value = AacProfileLowDelayAAC
        flow.Bitrate.value = 100

        source = NSourceAudioValue()
        source.Channels.value = [_make_audio_channel("L"), _make_audio_channel("R")]

        with pytest.raises(NotAllowed, match="cannot find"):
            select_aac_level_from_coded_flow(flow, source)

    def test_custom_allowed_levels(self) -> None:
        """Pass a restricted set of allowed levels."""
        flow = NFlowAudioCodedValue()
        flow.FlowCore.GrainRate.set_value(_make_grain_rate(48000))
        flow.Profile.value = AacProfileAAC
        flow.Bitrate.value = 400

        source = NSourceAudioValue()
        source.Channels.value = [_make_audio_channel("L"), _make_audio_channel("R")]

        select_aac_level_from_coded_flow(flow, source, [CodecLevel1, CodecLevel2])
        assert flow.Level.defined
