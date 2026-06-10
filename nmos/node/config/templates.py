# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Codec constraint templates.

Each template is a dict of capability URN → constraint dict (NMOS BCP-004-01 format).
Templates are keyed by media_type string.

Two tiers:
- **Native templates** (get_native_*): produce single-value defaults for the native
  operating point (preference=100).  Only parameters with sensible universal defaults
  are filled — format-specific parameters (resolution, bitrate, profile, etc.) must
  come from the config.
- **Generic templates** (get_*_template): produce multi-value ranges describing the
  broad capability envelope (preference < 100).

Templates come in variants:
- sender (default): for sender constraint sets
- receiver: may have additional transport modes (e.g., SingleNalUnit for H.264)
- sub: for sub-constraint sets within mux (more restricted, e.g., in_band only)

User-specified values always take precedence over template values.

Project rule: reference values through EnumRegistry enums (EnumName.s) rather than
bare string literals.  The only values left as bare strings are the AES3
channel_order grouping symbols (ST 2110-31), which have no EnumRegistry enum and
remain plain strings by design.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any

from nmos.enums import (
    # Media types
    VideoRaw, VideoCodedH264, VideoCodedH265, VideoCodedJxsv,
    AudioRawL16, AudioRawL20, AudioRawL24, AudioCodedAm824,
    AudioCodedAac, AudioCodedAacADTS, DataUsb,
    # Capability URNs (keys)
    CapFormatMediaType, CapFormatInterlaceMode, CapFormatTransferCharacteristic,
    CapFormatConstantBitRate, CapFormatGrainRate, CapFormatFrameWidth,
    CapFormatFrameHeight, CapFormatColorspace, CapFormatColorSampling,
    CapFormatComponentDepth, CapFormatProfile, CapFormatLevel, CapFormatSublevel,
    CapFormatBitRate, CapFormatSampleDepth, CapFormatSampleRate,
    CapFormatChannelCount, CapTransportPacketTransmissionMode,
    CapTransportParameterSetsTransportMode, CapTransportParameterSetsFlowMode,
    CapTransportUsbClass, CapTransportChannelOrder, CapMetaLabel,
    # Constraint values
    Progressive, SDR, BT601, BT709, BT2020,
    SamplingYCbCr_420, SamplingYCbCr_422, SamplingYCbCr_444,
    NonInterleavedNalUnits, SingleNalUnit, CodeStream, NonInterleavedAccessUnits,
    InBand, InAndOutOfBand, OutOfBand, Strict,
    H264ProfileHigh_422, H264ProfileHighIntra_422,
    H265ProfileMain10_422, H265ProfileMain10_444,
    H265ProfileMain10Intra_422, H265ProfileMain10Intra_444,
    CodecLevel1, CodecLevel2, CodecLevel3, CodecLevel3_1, CodecLevel3_2,
    CodecLevel4, CodecLevel4_1, CodecLevel4_2, CodecLevel5, CodecLevel5_1,
    CodecLevel5_2, CodecLevel6, CodecLevel6_1, CodecLevel6_2, CodecLevel7, CodecLevel8,
    H265LevelMain3, H265LevelMain3_1, H265LevelMain4, H265LevelHigh4,
    H265LevelMain4_1, H265LevelHigh4_1, H265LevelMain5, H265LevelHigh5,
    H265LevelMain5_1, H265LevelHigh5_1, H265LevelMain5_2, H265LevelHigh5_2,
    H265LevelMain6, H265LevelHigh6, H265LevelMain6_1, H265LevelHigh6_1,
    H265LevelMain6_2, H265LevelHigh6_2,
    JxsvLevel4k1, JxsvLevel4k2, JxsvLevel4k3,
    JxsvProfileMain420_12, JxsvProfileHigh420_12,
    JxsvProfileMain444_12, JxsvProfileHigh444_12,
    JxsvSublevel3bpp, JxsvSublevel4bpp,
    CodecProfileMain, AacProfileHighQuality, AacProfileNatural, AacProfileAAC,
    AacProfileHighEfficiencyAAC, AacProfileHighEfficiencyAACv2,
    AacProfileLowDelayAAC, AacProfileLowDelayAACv2,
)

# AES3 channel_order grouping symbols (ST 2110-31): no EnumRegistry enum — plain strings by design.
_AM824_CHANNEL_ORDER = ["SMPTE2110.(AES3)", "SMPTE2110.(AES3,ST)",
                        "SMPTE2110.(AES3,51)", "SMPTE2110.(AES3,71)"]

# ---------------------------------------------------------------------------
# Common video values shared across templates
# ---------------------------------------------------------------------------

_COMMON_VIDEO_RATES = [
    {"numerator": 24}, {"numerator": 25}, {"numerator": 30},
    {"numerator": 30000, "denominator": 1001},
    {"numerator": 50}, {"numerator": 60},
    {"numerator": 60000, "denominator": 1001},
]

_COMMON_VIDEO_WIDTHS = [720, 1280, 1920, 3840]
_COMMON_VIDEO_HEIGHTS = [480, 720, 1080, 2160]
_COMMON_COLORSPACES = [BT601.s, BT709.s, BT2020.s]

# ---------------------------------------------------------------------------
# Native video templates (single-value defaults — tip of the pyramid)
# ---------------------------------------------------------------------------

def get_native_raw_template(*, sub: bool = False) -> dict[str, Any]:
    """Native defaults for video/raw.

    Config must provide: frame_width, frame_height, grain_rate,
    component_depth, color_sampling, colorspace.
    """
    return {
        CapFormatMediaType.s: {"enum": [VideoRaw.s]},
        CapFormatInterlaceMode.s: {"enum": [Progressive.s]},
        CapFormatTransferCharacteristic.s: {"enum": [SDR.s]},
    }


def get_native_h264_template(*, receiver: bool = False, sub: bool = False) -> dict[str, Any]:
    """Native defaults for video/H264.

    Config must provide: frame_width, frame_height, grain_rate,
    component_depth, color_sampling, colorspace, profile, level, bit_rate.
    """
    t: dict[str, Any] = {
        CapFormatMediaType.s: {"enum": [VideoCodedH264.s]},
        CapFormatInterlaceMode.s: {"enum": [Progressive.s]},
        CapFormatTransferCharacteristic.s: {"enum": [SDR.s]},
        CapFormatConstantBitRate.s: {"enum": [False]},
    }

    if not sub:
        if receiver:
            t[CapTransportPacketTransmissionMode.s] = {"enum": [NonInterleavedNalUnits.s]}
            t[CapTransportParameterSetsTransportMode.s] = {"enum": [InBand.s, InAndOutOfBand.s, OutOfBand.s]}
            t[CapTransportParameterSetsFlowMode.s] = {"enum": [Strict.s]}
        else:
            t[CapTransportPacketTransmissionMode.s] = {"enum": [NonInterleavedNalUnits.s]}
            t[CapTransportParameterSetsTransportMode.s] = {"enum": [InBand.s]}
            t[CapTransportParameterSetsFlowMode.s] = {"enum": [Strict.s]}

    return t


def get_native_h265_template(*, receiver: bool = False, sub: bool = False) -> dict[str, Any]:
    """Native defaults for video/H265.

    Config must provide: frame_width, frame_height, grain_rate,
    component_depth, color_sampling, colorspace, profile, level, bit_rate.
    """
    t: dict[str, Any] = {
        CapFormatMediaType.s: {"enum": [VideoCodedH265.s]},
        CapFormatInterlaceMode.s: {"enum": [Progressive.s]},
        CapFormatTransferCharacteristic.s: {"enum": [SDR.s]},
        CapFormatConstantBitRate.s: {"enum": [False]},
    }

    if not sub:
        if receiver:
            t[CapTransportPacketTransmissionMode.s] = {"enum": [NonInterleavedNalUnits.s]}
            t[CapTransportParameterSetsTransportMode.s] = {"enum": [InBand.s, InAndOutOfBand.s, OutOfBand.s]}
            t[CapTransportParameterSetsFlowMode.s] = {"enum": [Strict.s]}
        else:
            t[CapTransportPacketTransmissionMode.s] = {"enum": [NonInterleavedNalUnits.s]}
            t[CapTransportParameterSetsTransportMode.s] = {"enum": [InBand.s]}
            t[CapTransportParameterSetsFlowMode.s] = {"enum": [Strict.s]}

    return t


def get_native_jxsv_template(*, sub: bool = False) -> dict[str, Any]:
    """Native defaults for video/jxsv.

    Config must provide: frame_width, frame_height, grain_rate,
    component_depth, color_sampling, colorspace, profile, level, sublevel.
    """
    t: dict[str, Any] = {
        CapFormatMediaType.s: {"enum": [VideoCodedJxsv.s]},
        CapFormatInterlaceMode.s: {"enum": [Progressive.s]},
        CapFormatTransferCharacteristic.s: {"enum": [SDR.s]},
    }
    if sub:
        t[CapTransportPacketTransmissionMode.s] = {"enum": [NonInterleavedNalUnits.s]}
    else:
        t[CapTransportPacketTransmissionMode.s] = {"enum": [CodeStream.s]}
    return t


# ---------------------------------------------------------------------------
# Native audio templates (single-value defaults — tip of the pyramid)
# ---------------------------------------------------------------------------

def get_native_pcm_template(*, sub: bool = False) -> dict[str, Any]:
    """Native defaults for audio/L24.

    Config must provide: sample_rate, channel_count.
    """
    return {
        CapFormatMediaType.s: {"enum": [AudioRawL24.s]},
        CapFormatSampleDepth.s: {"enum": [24]},
    }


def get_native_aac_template(*, sub: bool = False) -> dict[str, Any]:
    """Native defaults for AAC.

    Non-sub uses audio/mpeg4-generic (AAC over RTP, RFC 3640); sub (within an
    MPEG2-TS mux) uses audio/MP4A-ADTS.

    Config must provide: sample_rate, channel_count, profile, level, bit_rate.
    """
    t: dict[str, Any] = {
        CapFormatConstantBitRate.s: {"enum": [False]},
    }

    if not sub:
        t[CapFormatMediaType.s] = {"enum": [AudioCodedAac.s]}
        t[CapTransportPacketTransmissionMode.s] = {"enum": [NonInterleavedAccessUnits.s]}
        t[CapTransportParameterSetsTransportMode.s] = {"enum": [OutOfBand.s]}
        t[CapTransportParameterSetsFlowMode.s] = {"enum": [Strict.s]}
    else:
        t[CapFormatMediaType.s] = {"enum": [AudioCodedAacADTS.s]}
    return t


def get_native_am824_template(*, sub: bool = False) -> dict[str, Any]:
    """Native defaults for audio/AM824.

    AM824 always uses 48 kHz and stereo pairs (even channel counts).
    """
    return {
        CapFormatMediaType.s: {"enum": [AudioCodedAm824.s]},
        CapFormatSampleRate.s: {"enum": [{"numerator": 48000}]},
        CapFormatChannelCount.s: {"enum": [2, 4, 8, 10]},
    }


# ---------------------------------------------------------------------------
# Native data templates
# ---------------------------------------------------------------------------

def get_native_usb_template() -> dict[str, Any]:
    """Native defaults for application/usb.

    Trivially native — only 2 fixed parameters.
    """
    return {
        CapFormatMediaType.s: {"enum": [DataUsb.s]},
        CapTransportUsbClass.s: {"enum": [3]},
    }


# ---------------------------------------------------------------------------
# Generic video templates (multi-value ranges — broad capability envelope)
# ---------------------------------------------------------------------------

def get_raw_template(*, sub: bool = False) -> dict[str, Any]:
    """Template for video/raw."""
    t: dict[str, Any] = {
        CapFormatMediaType.s: {"enum": [VideoRaw.s]},
        CapFormatInterlaceMode.s: {"enum": [Progressive.s]},
        CapFormatGrainRate.s: {"enum": _COMMON_VIDEO_RATES},
        CapFormatFrameWidth.s: {"enum": _COMMON_VIDEO_WIDTHS},
        CapFormatFrameHeight.s: {"enum": _COMMON_VIDEO_HEIGHTS},
        CapFormatTransferCharacteristic.s: {"enum": [SDR.s]},
        CapFormatColorspace.s: {"enum": _COMMON_COLORSPACES},
    }
    if sub:
        t[CapFormatColorSampling.s] = {"enum": [SamplingYCbCr_422.s]}
        t[CapFormatComponentDepth.s] = {"enum": [8]}
    else:
        t[CapFormatColorSampling.s] = {"enum": [SamplingYCbCr_420.s, SamplingYCbCr_422.s, SamplingYCbCr_444.s]}
        t[CapFormatComponentDepth.s] = {"enum": [8, 10]}
    return t


def get_h264_template(*, receiver: bool = False, sub: bool = False) -> dict[str, Any]:
    """Template for video/H264."""
    t: dict[str, Any] = {
        CapFormatMediaType.s: {"enum": [VideoCodedH264.s]},
        CapFormatInterlaceMode.s: {"enum": [Progressive.s]},
        CapFormatGrainRate.s: {"enum": _COMMON_VIDEO_RATES},
        CapFormatFrameWidth.s: {"enum": _COMMON_VIDEO_WIDTHS},
        CapFormatFrameHeight.s: {"enum": _COMMON_VIDEO_HEIGHTS},
        CapFormatColorSampling.s: {"enum": [SamplingYCbCr_420.s, SamplingYCbCr_422.s]},
        CapFormatTransferCharacteristic.s: {"enum": [SDR.s]},
        CapFormatColorspace.s: {"enum": _COMMON_COLORSPACES},
        CapFormatComponentDepth.s: {"enum": [8, 10]},
        CapFormatProfile.s: {"enum": [H264ProfileHigh_422.s, H264ProfileHighIntra_422.s]},
        CapFormatLevel.s: {"enum": [
            CodecLevel3.s, CodecLevel3_1.s, CodecLevel3_2.s, CodecLevel4.s,
            CodecLevel4_1.s, CodecLevel4_2.s, CodecLevel5.s, CodecLevel5_1.s,
            CodecLevel5_2.s, CodecLevel6.s, CodecLevel6_1.s, CodecLevel6_2.s,
        ]},
        CapFormatBitRate.s: {"minimum": 10000, "maximum": 2000000},
        CapFormatConstantBitRate.s: {"enum": [False, True]},
    }
    if not sub:
        ptm = [NonInterleavedNalUnits.s]
        if receiver:
            ptm = [NonInterleavedNalUnits.s, SingleNalUnit.s]
        t[CapTransportPacketTransmissionMode.s] = {"enum": ptm}

        pstm = [InBand.s]
        if receiver:
            pstm = [InAndOutOfBand.s, InBand.s, OutOfBand.s]

        t[CapTransportParameterSetsTransportMode.s] = {"enum": pstm}
        t[CapTransportParameterSetsFlowMode.s] = {"enum": [Strict.s]}

    return t


def get_h265_template(*, receiver: bool = False, sub: bool = False) -> dict[str, Any]:
    """Template for video/H265."""
    t: dict[str, Any] = {
        CapFormatMediaType.s: {"enum": [VideoCodedH265.s]},
        CapFormatInterlaceMode.s: {"enum": [Progressive.s]},
        CapFormatGrainRate.s: {"enum": _COMMON_VIDEO_RATES},
        CapFormatFrameWidth.s: {"enum": _COMMON_VIDEO_WIDTHS},
        CapFormatFrameHeight.s: {"enum": _COMMON_VIDEO_HEIGHTS},
        CapFormatColorSampling.s: {"enum": [SamplingYCbCr_420.s, SamplingYCbCr_422.s, SamplingYCbCr_444.s]},
        CapFormatTransferCharacteristic.s: {"enum": [SDR.s]},
        CapFormatColorspace.s: {"enum": _COMMON_COLORSPACES},
        CapFormatComponentDepth.s: {"enum": [8, 10]},
        CapFormatProfile.s: {"enum": [
            H265ProfileMain10_422.s, H265ProfileMain10_444.s,
            H265ProfileMain10Intra_422.s, H265ProfileMain10Intra_444.s,
        ]},
        CapFormatLevel.s: {"enum": [
            H265LevelMain3.s, H265LevelMain3_1.s, H265LevelMain4.s, H265LevelHigh4.s,
            H265LevelMain4_1.s, H265LevelHigh4_1.s,
            H265LevelMain5.s, H265LevelHigh5.s, H265LevelMain5_1.s, H265LevelHigh5_1.s,
            H265LevelMain5_2.s, H265LevelHigh5_2.s,
            H265LevelMain6.s, H265LevelHigh6.s, H265LevelMain6_1.s, H265LevelHigh6_1.s,
            H265LevelMain6_2.s, H265LevelHigh6_2.s,
        ]},
        CapFormatBitRate.s: {"minimum": 6000, "maximum": 4800000},
        CapFormatConstantBitRate.s: {"enum": [False, True]},
    }
    if not sub:
        ptm = [NonInterleavedNalUnits.s]
        if receiver:
            ptm = [NonInterleavedNalUnits.s, SingleNalUnit.s]
        t[CapTransportPacketTransmissionMode.s] = {"enum": ptm}

        pstm = [InBand.s]
        if receiver:
            pstm = [InAndOutOfBand.s, InBand.s, OutOfBand.s]

        t[CapTransportParameterSetsTransportMode.s] = {"enum": pstm}
        t[CapTransportParameterSetsFlowMode.s] = {"enum": [Strict.s]}

    return t


def get_jxsv_template(*, sub: bool = False) -> dict[str, Any]:
    """Template for video/jxsv."""
    t: dict[str, Any] = {
        CapFormatMediaType.s: {"enum": [VideoCodedJxsv.s]},
        CapFormatInterlaceMode.s: {"enum": [Progressive.s]},
        CapFormatGrainRate.s: {"enum": _COMMON_VIDEO_RATES},
        CapFormatFrameWidth.s: {"enum": _COMMON_VIDEO_WIDTHS},
        CapFormatFrameHeight.s: {"enum": _COMMON_VIDEO_HEIGHTS},
        CapFormatColorSampling.s: {"enum": [SamplingYCbCr_420.s, SamplingYCbCr_422.s, SamplingYCbCr_444.s]},
        CapFormatTransferCharacteristic.s: {"enum": [SDR.s]},
        CapFormatColorspace.s: {"enum": _COMMON_COLORSPACES},
        CapFormatComponentDepth.s: {"enum": [8, 10]},
        CapFormatProfile.s: {"enum": [
            JxsvProfileMain420_12.s, JxsvProfileHigh420_12.s,
            JxsvProfileMain444_12.s, JxsvProfileHigh444_12.s,
        ]},
        CapFormatLevel.s: {"enum": [JxsvLevel4k1.s, JxsvLevel4k2.s, JxsvLevel4k3.s]},
        CapFormatSublevel.s: {"enum": [JxsvSublevel3bpp.s, JxsvSublevel4bpp.s]},
    }
    if sub:
        t[CapTransportPacketTransmissionMode.s] = {"enum": [NonInterleavedNalUnits.s]}
    else:
        t[CapTransportPacketTransmissionMode.s] = {"enum": [CodeStream.s]}
    return t


# ---------------------------------------------------------------------------
# Audio templates
# ---------------------------------------------------------------------------

def get_pcm_template(*, sub: bool = False) -> dict[str, Any]:
    """Template for audio/L16, audio/L20, audio/L24."""
    return {
        CapFormatMediaType.s: {"enum": [AudioRawL16.s, AudioRawL20.s, AudioRawL24.s]},
        CapFormatSampleRate.s: {"enum": [
            {"numerator": 48000}, {"numerator": 96000},
        ]},
        CapFormatChannelCount.s: {"enum": [2, 4, 6, 8]},
        CapFormatSampleDepth.s: {"enum": [16, 20, 24]},
    }


def get_aac_template(*, sub: bool = False, stereo_only: bool = False) -> dict[str, Any]:
    """Template for AAC (multi-channel or stereo-only via stereo_only flag).

    Non-sub uses audio/mpeg4-generic (RFC 3640); sub (MPEG2-TS) uses audio/MP4A-ADTS.
    """
    channels = [2] if stereo_only else [2, 6]
    t: dict[str, Any] = {
        CapFormatSampleRate.s: {"enum": [{"numerator": 48000}]},
        CapFormatChannelCount.s: {"enum": channels},
        CapFormatProfile.s: {"enum": [
            CodecProfileMain.s, AacProfileHighQuality.s, AacProfileNatural.s, AacProfileAAC.s,
            AacProfileHighEfficiencyAAC.s, AacProfileHighEfficiencyAACv2.s,
            AacProfileLowDelayAAC.s, AacProfileLowDelayAACv2.s,
        ]},
        CapFormatLevel.s: {"enum": [
            CodecLevel1.s, CodecLevel2.s, CodecLevel3.s, CodecLevel4.s,
            CodecLevel5.s, CodecLevel6.s, CodecLevel7.s, CodecLevel8.s,
        ]},
        CapFormatBitRate.s: {"maximum": 1728},
        CapFormatConstantBitRate.s: {"enum": [False, True]},
    }

    if not sub:
        t[CapFormatMediaType.s] = {"enum": [AudioCodedAac.s]}
        t[CapTransportPacketTransmissionMode.s] = {"enum": [NonInterleavedAccessUnits.s]}
        t[CapTransportParameterSetsTransportMode.s] = {"enum": [OutOfBand.s]}
        t[CapTransportParameterSetsFlowMode.s] = {"enum": [Strict.s]}
    else:
        t[CapFormatMediaType.s] = {"enum": [AudioCodedAacADTS.s]}

    return t


def get_am824_template(*, sub: bool = False) -> dict[str, Any]:
    """Template for audio/AM824.

    Non-sub: multiple sample rates + channel_order constraint.
    Sub (within MPEG2-TS): 48 kHz only per SMPTE 302M, no channel_order.
    """
    t: dict[str, Any] = {
        CapFormatMediaType.s: {"enum": [AudioCodedAm824.s]},
        CapFormatChannelCount.s: {"enum": [2, 4, 8, 10]},
    }
    if sub:
        # SMPTE 302M restricts to 48 kHz
        t[CapFormatSampleRate.s] = {"enum": [{"numerator": 48000}]}
    else:
        t[CapFormatSampleRate.s] = {"enum": [
            {"numerator": 48000},
            {"numerator": 96000},
            {"numerator": 44100},
            {"numerator": 88200},
        ]}
        # Channel order uses AES3 grouping symbols per ST 2110-31 (no enum — plain strings by design)
        t[CapTransportChannelOrder.s] = {"enum": list(_AM824_CHANNEL_ORDER)}
    return t


# ---------------------------------------------------------------------------
# Template registry — keyed by media_type string
# ---------------------------------------------------------------------------

def get_native_template(
    media_type: str,
    *,
    receiver: bool = False,
    sub: bool = False,
) -> dict[str, Any] | None:
    """Look up the native template for a media_type.

    Returns single-value defaults for the native operating point (preference=100).
    """
    mt = media_type.lower() if media_type else ""

    if mt == VideoRaw.s.lower():
        return get_native_raw_template(sub=sub)
    elif mt == VideoCodedH264.s.lower():
        return get_native_h264_template(receiver=receiver, sub=sub)
    elif mt == VideoCodedH265.s.lower():
        return get_native_h265_template(receiver=receiver, sub=sub)
    elif mt == VideoCodedJxsv.s.lower():
        return get_native_jxsv_template(sub=sub)
    elif mt in (AudioRawL16.s.lower(), AudioRawL20.s.lower(), AudioRawL24.s.lower()):
        return get_native_pcm_template(sub=sub)
    elif mt in (AudioCodedAac.s.lower(), AudioCodedAacADTS.s.lower()):
        return get_native_aac_template(sub=sub)
    elif mt == AudioCodedAm824.s.lower():
        return get_native_am824_template(sub=sub)
    elif mt == DataUsb.s.lower():
        return get_native_usb_template()

    return None


def get_generic_template(
    media_type: str,
    *,
    receiver: bool = False,
    sub: bool = False,
) -> dict[str, Any] | None:
    """Look up the generic template for a media_type.

    Returns multi-value ranges for the broad capability envelope (preference < 100).
    """
    mt = media_type.lower() if media_type else ""

    if mt == VideoRaw.s.lower():
        return get_raw_template(sub=sub)
    elif mt == VideoCodedH264.s.lower():
        return get_h264_template(receiver=receiver, sub=sub)
    elif mt == VideoCodedH265.s.lower():
        return get_h265_template(receiver=receiver, sub=sub)
    elif mt == VideoCodedJxsv.s.lower():
        return get_jxsv_template(sub=sub)
    elif mt in (AudioRawL16.s.lower(), AudioRawL20.s.lower(), AudioRawL24.s.lower()):
        return get_pcm_template(sub=sub)
    elif mt in (AudioCodedAac.s.lower(), AudioCodedAacADTS.s.lower()):
        return get_aac_template(sub=sub)
    elif mt == AudioCodedAm824.s.lower():
        return get_am824_template(sub=sub)

    return None


# Keep backward-compatible alias
get_template = get_generic_template


def apply_template_to_constraint_set(
    constraint_set: dict[str, Any],
    *,
    receiver: bool = False,
    sub: bool = False,
    native: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Apply template defaults to a constraint set via inheritance.

    If native=True, uses the native template (single-value defaults for the
    tip of the capability pyramid).  Otherwise uses the generic template
    (multi-value ranges for the broad capability envelope).

    If the constraint set has a media_type, look up the template and add
    any missing capabilities. User-specified values always take precedence.

    Returns the enriched constraint set (same dict, modified in place).
    """
    # Extract media_type from constraint set
    mt_constraint = constraint_set.get(CapFormatMediaType.s)
    if mt_constraint is None:
        return constraint_set

    mt_enum = mt_constraint.get("enum")
    if not mt_enum or not isinstance(mt_enum, list):
        return constraint_set

    # Use first media_type for template lookup
    media_type = str(mt_enum[0])

    if native:
        template = get_native_template(media_type, receiver=receiver, sub=sub)
        template_kind = "native"
    else:
        template = get_generic_template(media_type, receiver=receiver, sub=sub)
        template_kind = "generic"

    if template is None:
        return constraint_set

    # Inheritance: add template entries that are missing in user's constraint set
    added = []
    for cap_urn, cap_value in template.items():
        if cap_urn.startswith("urn:x-nmos:cap:meta:"):
            continue  # Don't inherit meta fields
        if cap_urn not in constraint_set:
            constraint_set[cap_urn] = cap_value
            added.append(cap_urn)

    if verbose and added:
        label = constraint_set.get(CapMetaLabel.s, media_type)
        print(f"    + {template_kind.capitalize()} template '{media_type}' added "
              f"{len(added)} capabilities to '{label}':")
        for cap in added:
            print(f"      + {cap}")

    return constraint_set
