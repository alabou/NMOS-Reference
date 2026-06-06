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
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any

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
_COMMON_COLORSPACES = ["BT601", "BT709", "BT2020"]

# ---------------------------------------------------------------------------
# Native video templates (single-value defaults — tip of the pyramid)
# ---------------------------------------------------------------------------

def get_native_raw_template(*, sub: bool = False) -> dict[str, Any]:
    """Native defaults for video/raw.

    Config must provide: frame_width, frame_height, grain_rate,
    component_depth, color_sampling, colorspace.
    """
    return {
        "urn:x-nmos:cap:format:media_type": {"enum": ["video/raw"]},
        "urn:x-nmos:cap:format:interlace_mode": {"enum": ["progressive"]},
        "urn:x-nmos:cap:format:transfer_characteristic": {"enum": ["SDR"]},
    }


def get_native_h264_template(*, receiver: bool = False, sub: bool = False) -> dict[str, Any]:
    """Native defaults for video/H264.

    Config must provide: frame_width, frame_height, grain_rate,
    component_depth, color_sampling, colorspace, profile, level, bit_rate.
    """
    t: dict[str, Any] = {
        "urn:x-nmos:cap:format:media_type": {"enum": ["video/H264"]},
        "urn:x-nmos:cap:format:interlace_mode": {"enum": ["progressive"]},
        "urn:x-nmos:cap:format:transfer_characteristic": {"enum": ["SDR"]},
        "urn:x-nmos:cap:format:constant_bit_rate": {"enum": [False]},
    }
    if not sub:
        t["urn:x-nmos:cap:transport:packet_transmission_mode"] = {"enum": ["non_interleaved_nal_units"]}
        t["urn:x-nmos:cap:transport:parameter_sets_transport_mode"] = {"enum": ["in_and_out_of_band"]}
        t["urn:x-nmos:cap:transport:parameter_sets_flow_mode"] = {"enum": ["strict"]}
    else:
        t["urn:x-nmos:cap:transport:packet_transmission_mode"] = {"enum": ["non_interleaved_nal_units"]}
        t["urn:x-nmos:cap:transport:parameter_sets_transport_mode"] = {"enum": ["in_band"]}
        t["urn:x-nmos:cap:transport:parameter_sets_flow_mode"] = {"enum": ["strict"]}
    return t


def get_native_h265_template(*, receiver: bool = False, sub: bool = False) -> dict[str, Any]:
    """Native defaults for video/H265.

    Config must provide: frame_width, frame_height, grain_rate,
    component_depth, color_sampling, colorspace, profile, level, bit_rate.
    """
    t: dict[str, Any] = {
        "urn:x-nmos:cap:format:media_type": {"enum": ["video/H265"]},
        "urn:x-nmos:cap:format:interlace_mode": {"enum": ["progressive"]},
        "urn:x-nmos:cap:format:transfer_characteristic": {"enum": ["SDR"]},
        "urn:x-nmos:cap:format:constant_bit_rate": {"enum": [False]},
    }
    if not sub:
        t["urn:x-nmos:cap:transport:packet_transmission_mode"] = {"enum": ["non_interleaved_nal_units"]}
        t["urn:x-nmos:cap:transport:parameter_sets_transport_mode"] = {"enum": ["in_and_out_of_band"]}
        t["urn:x-nmos:cap:transport:parameter_sets_flow_mode"] = {"enum": ["strict"]}
    else:
        t["urn:x-nmos:cap:transport:packet_transmission_mode"] = {"enum": ["non_interleaved_nal_units"]}
        t["urn:x-nmos:cap:transport:parameter_sets_transport_mode"] = {"enum": ["in_band"]}
        t["urn:x-nmos:cap:transport:parameter_sets_flow_mode"] = {"enum": ["strict"]}
    return t


def get_native_jxsv_template(*, sub: bool = False) -> dict[str, Any]:
    """Native defaults for video/jxsv.

    Config must provide: frame_width, frame_height, grain_rate,
    component_depth, color_sampling, colorspace, profile, level, sublevel.
    """
    t: dict[str, Any] = {
        "urn:x-nmos:cap:format:media_type": {"enum": ["video/jxsv"]},
        "urn:x-nmos:cap:format:interlace_mode": {"enum": ["progressive"]},
        "urn:x-nmos:cap:format:transfer_characteristic": {"enum": ["SDR"]},
    }
    if sub:
        t["urn:x-nmos:cap:transport:packet_transmission_mode"] = {"enum": ["non_interleaved_nal_units"]}
    else:
        t["urn:x-nmos:cap:transport:packet_transmission_mode"] = {"enum": ["codestream"]}
    return t


# ---------------------------------------------------------------------------
# Native audio templates (single-value defaults — tip of the pyramid)
# ---------------------------------------------------------------------------

def get_native_pcm_template(*, sub: bool = False) -> dict[str, Any]:
    """Native defaults for audio/L24.

    Config must provide: sample_rate, channel_count.
    """
    return {
        "urn:x-nmos:cap:format:media_type": {"enum": ["audio/L24"]},
        "urn:x-nmos:cap:format:sample_depth": {"enum": [24]},
    }


def get_native_aac_template(*, sub: bool = False) -> dict[str, Any]:
    """Native defaults for audio/aac.

    Config must provide: sample_rate, channel_count, profile, level, bit_rate.
    """
    t: dict[str, Any] = {
        "urn:x-nmos:cap:format:constant_bit_rate": {"enum": [False]},
    }
    if not sub:
        t["urn:x-nmos:cap:format:media_type"] = {"enum": ["audio/aac"]}
        t["urn:x-nmos:cap:transport:packet_transmission_mode"] = {"enum": ["non_interleaved_access_units"]}
        t["urn:x-nmos:cap:transport:parameter_sets_transport_mode"] = {"enum": ["out_of_band"]}
        t["urn:x-nmos:cap:transport:parameter_sets_flow_mode"] = {"enum": ["strict"]}
    else:
        t["urn:x-nmos:cap:format:media_type"] = {"enum": ["audio/aac-adts"]}
        t["urn:x-nmos:cap:transport:packet_transmission_mode"] = {"enum": ["non_interleaved_access_units"]}
        t["urn:x-nmos:cap:transport:parameter_sets_transport_mode"] = {"enum": ["in_band"]}
        t["urn:x-nmos:cap:transport:parameter_sets_flow_mode"] = {"enum": ["strict"]}
    return t


def get_native_am824_template(*, sub: bool = False) -> dict[str, Any]:
    """Native defaults for audio/AM824.

    AM824 always uses 48 kHz and stereo pairs (even channel counts).
    """
    return {
        "urn:x-nmos:cap:format:media_type": {"enum": ["audio/AM824"]},
        "urn:x-nmos:cap:format:sample_rate": {"enum": [{"numerator": 48000}]},
        "urn:x-nmos:cap:format:channel_count": {"enum": [2, 4, 8, 10]},
    }


# ---------------------------------------------------------------------------
# Native data templates
# ---------------------------------------------------------------------------

def get_native_usb_template() -> dict[str, Any]:
    """Native defaults for data/USB.

    Trivially native — only 2 fixed parameters.
    """
    return {
        "urn:x-nmos:cap:format:media_type": {"enum": ["data/USB"]},
        "urn:x-nmos:cap:transport:usb_class": {"enum": [3]},
    }


# ---------------------------------------------------------------------------
# Generic video templates (multi-value ranges — broad capability envelope)
# ---------------------------------------------------------------------------

def get_raw_template(*, sub: bool = False) -> dict[str, Any]:
    """Template for video/raw."""
    t: dict[str, Any] = {
        "urn:x-nmos:cap:format:media_type": {"enum": ["video/raw"]},
        "urn:x-nmos:cap:format:interlace_mode": {"enum": ["progressive"]},
        "urn:x-nmos:cap:format:grain_rate": {"enum": _COMMON_VIDEO_RATES},
        "urn:x-nmos:cap:format:frame_width": {"enum": _COMMON_VIDEO_WIDTHS},
        "urn:x-nmos:cap:format:frame_height": {"enum": _COMMON_VIDEO_HEIGHTS},
        "urn:x-nmos:cap:format:transfer_characteristic": {"enum": ["SDR"]},
        "urn:x-nmos:cap:format:colorspace": {"enum": _COMMON_COLORSPACES},
    }
    if sub:
        t["urn:x-nmos:cap:format:color_sampling"] = {"enum": ["YCbCr-4:2:2"]}
        t["urn:x-nmos:cap:format:component_depth"] = {"enum": [8]}
    else:
        t["urn:x-nmos:cap:format:color_sampling"] = {"enum": ["YCbCr-4:2:0", "YCbCr-4:2:2", "YCbCr-4:4:4"]}
        t["urn:x-nmos:cap:format:component_depth"] = {"enum": [8, 10]}
    return t


def get_h264_template(*, receiver: bool = False, sub: bool = False) -> dict[str, Any]:
    """Template for video/H264."""
    t: dict[str, Any] = {
        "urn:x-nmos:cap:format:media_type": {"enum": ["video/H264"]},
        "urn:x-nmos:cap:format:interlace_mode": {"enum": ["progressive"]},
        "urn:x-nmos:cap:format:grain_rate": {"enum": _COMMON_VIDEO_RATES},
        "urn:x-nmos:cap:format:frame_width": {"enum": _COMMON_VIDEO_WIDTHS},
        "urn:x-nmos:cap:format:frame_height": {"enum": _COMMON_VIDEO_HEIGHTS},
        "urn:x-nmos:cap:format:color_sampling": {"enum": ["YCbCr-4:2:0", "YCbCr-4:2:2"]},
        "urn:x-nmos:cap:format:transfer_characteristic": {"enum": ["SDR"]},
        "urn:x-nmos:cap:format:colorspace": {"enum": _COMMON_COLORSPACES},
        "urn:x-nmos:cap:format:component_depth": {"enum": [8, 10]},
        "urn:x-nmos:cap:format:profile": {"enum": ["High-422", "HighIntra-422"]},
        "urn:x-nmos:cap:format:level": {"enum": [
            "3", "3.1", "3.2", "4", "4.1", "4.2", "5", "5.1", "5.2", "6", "6.1", "6.2",
        ]},
        "urn:x-nmos:cap:format:bit_rate": {"minimum": 10000, "maximum": 2000000},
        "urn:x-nmos:cap:format:constant_bit_rate": {"enum": [False, True]},
    }
    if not sub:
        ptm = ["non_interleaved_nal_units"]
        if receiver:
            ptm = ["non_interleaved_nal_units", "single_nal_unit"]
        t["urn:x-nmos:cap:transport:packet_transmission_mode"] = {"enum": ptm}

        pstm = ["in_and_out_of_band"]
        if sub:
            pstm = ["in_band"]
        elif receiver:
            pstm = ["in_and_out_of_band", "in_band", "out_of_band"]
        t["urn:x-nmos:cap:transport:parameter_sets_transport_mode"] = {"enum": pstm}
        t["urn:x-nmos:cap:transport:parameter_sets_flow_mode"] = {"enum": ["strict"]}
    else:
        t["urn:x-nmos:cap:transport:packet_transmission_mode"] = {"enum": ["non_interleaved_nal_units"]}
        t["urn:x-nmos:cap:transport:parameter_sets_transport_mode"] = {"enum": ["in_band"]}
        t["urn:x-nmos:cap:transport:parameter_sets_flow_mode"] = {"enum": ["strict"]}
    return t


def get_h265_template(*, receiver: bool = False, sub: bool = False) -> dict[str, Any]:
    """Template for video/H265."""
    t: dict[str, Any] = {
        "urn:x-nmos:cap:format:media_type": {"enum": ["video/H265"]},
        "urn:x-nmos:cap:format:interlace_mode": {"enum": ["progressive"]},
        "urn:x-nmos:cap:format:grain_rate": {"enum": _COMMON_VIDEO_RATES},
        "urn:x-nmos:cap:format:frame_width": {"enum": _COMMON_VIDEO_WIDTHS},
        "urn:x-nmos:cap:format:frame_height": {"enum": _COMMON_VIDEO_HEIGHTS},
        "urn:x-nmos:cap:format:color_sampling": {"enum": ["YCbCr-4:2:0", "YCbCr-4:2:2", "YCbCr-4:4:4"]},
        "urn:x-nmos:cap:format:transfer_characteristic": {"enum": ["SDR"]},
        "urn:x-nmos:cap:format:colorspace": {"enum": _COMMON_COLORSPACES},
        "urn:x-nmos:cap:format:component_depth": {"enum": [8, 10]},
        "urn:x-nmos:cap:format:profile": {"enum": [
            "Main10-422", "Main10-444", "Main10Intra-422", "Main10Intra-444",
        ]},
        "urn:x-nmos:cap:format:level": {"enum": [
            "Main-3", "Main-3.1", "Main-4", "High-4", "Main-4.1", "High-4.1",
            "Main-5", "High-5", "Main-5.1", "High-5.1", "Main-5.2", "High-5.2",
            "Main-6", "High-6", "Main-6.1", "High-6.1", "Main-6.2", "High-6.2",
        ]},
        "urn:x-nmos:cap:format:bit_rate": {"minimum": 6000, "maximum": 4800000},
        "urn:x-nmos:cap:format:constant_bit_rate": {"enum": [False, True]},
    }
    if not sub:
        ptm = ["non_interleaved_nal_units"]
        if receiver:
            ptm = ["non_interleaved_nal_units", "single_nal_unit"]
        t["urn:x-nmos:cap:transport:packet_transmission_mode"] = {"enum": ptm}

        pstm = ["in_and_out_of_band"]
        if sub:
            pstm = ["in_band"]
        elif receiver:
            pstm = ["in_and_out_of_band", "in_band", "out_of_band"]
        t["urn:x-nmos:cap:transport:parameter_sets_transport_mode"] = {"enum": pstm}
        t["urn:x-nmos:cap:transport:parameter_sets_flow_mode"] = {"enum": ["strict"]}
    else:
        t["urn:x-nmos:cap:transport:packet_transmission_mode"] = {"enum": ["non_interleaved_nal_units"]}
        t["urn:x-nmos:cap:transport:parameter_sets_transport_mode"] = {"enum": ["in_band"]}
        t["urn:x-nmos:cap:transport:parameter_sets_flow_mode"] = {"enum": ["strict"]}
    return t


def get_jxsv_template(*, sub: bool = False) -> dict[str, Any]:
    """Template for video/jxsv."""
    t: dict[str, Any] = {
        "urn:x-nmos:cap:format:media_type": {"enum": ["video/jxsv"]},
        "urn:x-nmos:cap:format:interlace_mode": {"enum": ["progressive"]},
        "urn:x-nmos:cap:format:grain_rate": {"enum": _COMMON_VIDEO_RATES},
        "urn:x-nmos:cap:format:frame_width": {"enum": _COMMON_VIDEO_WIDTHS},
        "urn:x-nmos:cap:format:frame_height": {"enum": _COMMON_VIDEO_HEIGHTS},
        "urn:x-nmos:cap:format:color_sampling": {"enum": ["YCbCr-4:2:0", "YCbCr-4:2:2", "YCbCr-4:4:4"]},
        "urn:x-nmos:cap:format:transfer_characteristic": {"enum": ["SDR"]},
        "urn:x-nmos:cap:format:colorspace": {"enum": _COMMON_COLORSPACES},
        "urn:x-nmos:cap:format:component_depth": {"enum": [8, 10]},
        "urn:x-nmos:cap:format:profile": {"enum": [
            "Main 4:2:0 12-bit", "High 4:2:0 12-bit",
            "Main 4:4:4 12-bit", "High 4:4:4 12-bit",
        ]},
        "urn:x-nmos:cap:format:level": {"enum": ["4k-1", "4k-2", "4k-3"]},
        "urn:x-nmos:cap:format:sublevel": {"enum": ["3bpp", "4bpp"]},
    }
    if sub:
        t["urn:x-nmos:cap:transport:packet_transmission_mode"] = {"enum": ["non_interleaved_nal_units"]}
    else:
        t["urn:x-nmos:cap:transport:packet_transmission_mode"] = {"enum": ["codestream"]}
    return t


# ---------------------------------------------------------------------------
# Audio templates
# ---------------------------------------------------------------------------

def get_pcm_template(*, sub: bool = False) -> dict[str, Any]:
    """Template for audio/L16, audio/L20, audio/L24."""
    return {
        "urn:x-nmos:cap:format:media_type": {"enum": ["audio/L16", "audio/L20", "audio/L24"]},
        "urn:x-nmos:cap:format:sample_rate": {"enum": [
            {"numerator": 48000}, {"numerator": 96000},
        ]},
        "urn:x-nmos:cap:format:channel_count": {"enum": [2, 4, 6, 8]},
        "urn:x-nmos:cap:format:sample_depth": {"enum": [16, 20, 24]},
    }


def get_aac_template(*, sub: bool = False, stereo_only: bool = False) -> dict[str, Any]:
    """Template for audio/aac (multi-channel or stereo-only via stereo_only flag)."""
    channels = [2] if stereo_only else [2, 6]
    t: dict[str, Any] = {
        "urn:x-nmos:cap:format:media_type": {"enum": ["audio/aac"]},
        "urn:x-nmos:cap:format:sample_rate": {"enum": [{"numerator": 48000}]},
        "urn:x-nmos:cap:format:channel_count": {"enum": channels},
        "urn:x-nmos:cap:format:profile": {"enum": [
            "Main", "High Quality", "Natural", "AAC",
            "High Efficiency AAC", "High Efficiency AAC v2",
            "Low Delay AAC", "Low Delay AAC v2",
        ]},
        "urn:x-nmos:cap:format:level": {"enum": ["1", "2", "3", "4", "5", "6", "7", "8"]},
        "urn:x-nmos:cap:format:bit_rate": {"maximum": 1728},
        "urn:x-nmos:cap:format:constant_bit_rate": {"enum": [False, True]},
    }
    if sub:
        t["urn:x-nmos:cap:transport:packet_transmission_mode"] = {"enum": ["non_interleaved_access_units"]}
        t["urn:x-nmos:cap:transport:parameter_sets_transport_mode"] = {"enum": ["in_band"]}
        t["urn:x-nmos:cap:transport:parameter_sets_flow_mode"] = {"enum": ["strict"]}
    return t


def get_am824_template(*, sub: bool = False) -> dict[str, Any]:
    """Template for audio/AM824.

    Non-sub: multiple sample rates + channel_order constraint.
    Sub (within MPEG2-TS): 48 kHz only per SMPTE 302M, no channel_order.
    """
    t: dict[str, Any] = {
        "urn:x-nmos:cap:format:media_type": {"enum": ["audio/AM824"]},
        "urn:x-nmos:cap:format:channel_count": {"enum": [2, 4, 8, 10]},
    }
    if sub:
        # SMPTE 302M restricts to 48 kHz
        t["urn:x-nmos:cap:format:sample_rate"] = {"enum": [{"numerator": 48000}]}
    else:
        t["urn:x-nmos:cap:format:sample_rate"] = {"enum": [
            {"numerator": 48000},
            {"numerator": 96000},
            {"numerator": 44100},
            {"numerator": 88200},
        ]}
        # Channel order uses AES3 grouping symbols per ST 2110-31
        t["urn:x-matrox:cap:transport:channel_order"] = {"enum": [
            "SMPTE2110.(AES3)",
            "SMPTE2110.(AES3,ST)",
            "SMPTE2110.(AES3,51)",
            "SMPTE2110.(AES3,71)",
        ]}
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

    if mt == "video/raw":
        return get_native_raw_template(sub=sub)
    elif mt == "video/h264":
        return get_native_h264_template(receiver=receiver, sub=sub)
    elif mt == "video/h265":
        return get_native_h265_template(receiver=receiver, sub=sub)
    elif mt == "video/jxsv":
        return get_native_jxsv_template(sub=sub)
    elif mt in ("audio/l16", "audio/l20", "audio/l24"):
        return get_native_pcm_template(sub=sub)
    elif mt == "audio/aac" or mt == "audio/aac-adts":
        return get_native_aac_template(sub=sub)
    elif mt == "audio/am824":
        return get_native_am824_template(sub=sub)
    elif mt == "data/usb":
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

    if mt == "video/raw":
        return get_raw_template(sub=sub)
    elif mt == "video/h264":
        return get_h264_template(receiver=receiver, sub=sub)
    elif mt == "video/h265":
        return get_h265_template(receiver=receiver, sub=sub)
    elif mt == "video/jxsv":
        return get_jxsv_template(sub=sub)
    elif mt in ("audio/l16", "audio/l20", "audio/l24"):
        return get_pcm_template(sub=sub)
    elif mt == "audio/aac" or mt == "audio/aac-adts":
        return get_aac_template(sub=sub)
    elif mt == "audio/am824":
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
    mt_constraint = constraint_set.get("urn:x-nmos:cap:format:media_type")
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
        label = constraint_set.get("urn:x-nmos:cap:meta:label", media_type)
        print(f"    + {template_kind.capitalize()} template '{media_type}' added "
              f"{len(added)} capabilities to '{label}':")
        for cap in added:
            print(f"      + {cap}")

    return constraint_set
