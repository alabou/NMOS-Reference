# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Codec profile/level validation and automatic level selection.

Provides functions to:
- Validate that a codec profile is compatible with flow properties
- Validate that a profile+level combination meets rate/size/bitrate constraints
- Retrieve the maximum bitrate allowed for a given profile/level
- Automatically select the lowest suitable level for a coded flow

Each codec family (H.264, H.265, JXSV, AAC) has its own set of functions.
The video codecs share the same overall pattern:
  1. check_*_profile  — validates bit depth, color sampling, interlace mode
  2. check_*_profile_level — adds macroblock/sample size, rate, and bitrate checks
  3. get_*_max_bitrate — returns the max allowed bitrate for a profile/level
  4. select_*_level_from_coded_flow — iterates ordered levels to find the first match

The AAC functions follow a similar pattern but use audio-specific parameters
(object types, channel count, sampling rate).
"""

from __future__ import annotations

import math
from typing import Any, cast

from nmos.enums import EnumId, Progressive, Y, Cb, Cr, R, G, B
from nmos.errors import InvalidParameter, NotAllowed, NotAvailable

from nmos.codec import aac, h264, h265, jxsv


# ---------------------------------------------------------------------------
# Helper: derive SDP color sampling from video components
# ---------------------------------------------------------------------------

def get_sdp_color_sampling(components: list[Any]) -> str:
    """Derive the SDP color sampling string from a video component array.

    Examines the component names (Y/Cb/Cr or R/G/B) and the width/height
    ratios between luma and chroma planes to determine the sub-sampling.

    Args:
        components: list of NVideoComponentValue (must be exactly 3 elements).

    Returns:
        One of "RGB", "YCbCr-4:4:4", "YCbCr-4:2:2", or "YCbCr-4:2:0".

    Raises:
        InvalidParameter: If the component array is invalid or the sampling
            pattern cannot be determined.
    """
    if len(components) != 3:
        raise InvalidParameter("invalid array of video components")

    try:
        name0 = components[0].Name.value
        name1 = components[1].Name.value
        name2 = components[2].Name.value
    except NotAvailable:
        raise InvalidParameter("invalid array of video components — missing name")

    # --- RGB path ---
    if name0 is R and name1 is G and name2 is B:
        try:
            w0 = components[0].Width.value
            w1 = components[1].Width.value
            w2 = components[2].Width.value
            h0 = components[0].Height.value
            h1 = components[1].Height.value
            h2 = components[2].Height.value
        except NotAvailable:
            raise InvalidParameter("invalid array of video components")

        if w0 == w1 == w2 and h0 == h1 == h2:
            return "RGB"

    # --- YCbCr path ---
    if name0 is Y and name1 is Cb and name2 is Cr:
        try:
            w0 = components[0].Width.value
            w1 = components[1].Width.value
            w2 = components[2].Width.value
            h0 = components[0].Height.value
            h1 = components[1].Height.value
            h2 = components[2].Height.value
        except NotAvailable:
            raise InvalidParameter("invalid array of video components")

        if w0 == w1 == w2 and h0 == h1 == h2:
            return "YCbCr-4:4:4"

        if w0 == 2 * w1 == 2 * w2 and h0 == h1 == h2:
            return "YCbCr-4:2:2"

        if w0 == 2 * w1 == 2 * w2 and h0 == 2 * h1 == 2 * h2:
            return "YCbCr-4:2:0"

    raise InvalidParameter("invalid array of video components")


# ---------------------------------------------------------------------------
# Helper: validate color sampling against a profile's allowed list
# Shared by H.264, H.265, and JXSV check_*_profile functions.
# ---------------------------------------------------------------------------

def _check_video_color_sampling(
    sampling: str,
    profile_color_sampling: list[str],
) -> None:
    """Validate that a color sampling mode is supported by a codec profile.

    The video codecs (H.264, H.265, JXSV) all use the same YCbCr-only
    check.  RGB is never valid for coded video flows.

    Raises:
        InvalidParameter: If the sampling format is RGB or unrecognised.
        NotAllowed: If the profile does not support the detected sampling.
    """
    # Map SDP sampling strings to the short form stored in profile tables
    _SAMPLING_MAP: dict[str, str | None] = {
        "YCbCr-4:4:4": "4:4:4",
        "YCbCr-4:2:2": "4:2:2",
        "YCbCr-4:2:0": "4:2:0",
    }
    short = _SAMPLING_MAP.get(sampling)
    if short is None:
        # RGB or unknown — coded video codecs do not support RGB
        raise InvalidParameter("invalid color sampling")
    if short not in profile_color_sampling:
        raise NotAllowed("profile not matching color sampling requirements")


# ---------------------------------------------------------------------------
# Helper: extract rational value as numerator / denominator
# ---------------------------------------------------------------------------

def _get_rate(rational_value: Any) -> tuple[int, int]:
    """Extract numerator and denominator from an NRationalValue.

    Returns (numerator, denominator) where denominator defaults to 1 if
    undefined.  Reads Denominator (not Numerator) for the denominator field.
    """
    try:
        num = rational_value.Numerator.value
    except NotAvailable:
        raise InvalidParameter("invalid rate — missing numerator")
    try:
        den = rational_value.Denominator.value
    except NotAvailable:
        den = 1
    return num, den


# ===========================================================================
# H.264 functions
# ===========================================================================

def check_h264_profile(
    frame_width: int,
    frame_height: int,
    colorspace: EnumId,
    transfer_characteristic: EnumId,
    interlace_mode: EnumId,
    components: list[Any],
    grain_rate: Any,
    profile: EnumId,
    level: EnumId,
    bit_rate: int,
) -> None:
    """Validate that an H.264 profile is compatible with flow properties.

    Checks:
    - Interlace mode must be progressive (H.264 does not support interlaced)
    - Bit depth must not exceed the profile's maximum
    - Color sampling must be supported by the profile

    Raises:
        InvalidParameter: If interlace mode is not progressive or data is missing.
        NotAllowed: If bit depth or color sampling violates the profile.
    """
    if interlace_mode is not Progressive:
        raise InvalidParameter("H.264 does not support interlaced flows")

    try:
        bit_depth = components[0].BitDepth.value
    except NotAvailable:
        raise InvalidParameter("missing bit depth")

    sampling = get_sdp_color_sampling(components)

    profile_info = h264.ALL_PROFILES.get(profile)
    if profile_info is None:
        raise InvalidParameter("invalid profile")

    if bit_depth > profile_info.max_bit_depth:
        raise NotAllowed("profile not matching bit depth requirements")

    _check_video_color_sampling(sampling, profile_info.color_sampling)


def check_h264_profile_level(
    frame_width: int,
    frame_height: int,
    colorspace: EnumId,
    transfer_characteristic: EnumId,
    interlace_mode: EnumId,
    components: list[Any],
    grain_rate: Any,
    profile: EnumId,
    level: EnumId,
    bit_rate: int,
) -> None:
    """Validate H.264 profile and level constraints.

    First validates the profile (via check_h264_profile), then checks:
    - Macroblock count (ceil(w/16) × ceil(h/16)) ≤ level MaxSize
    - Macroblock rate (macroblocks × fps) ≤ level MaxRate
    - Bitrate ≤ level's per-profile max bitrate (Kbps)

    Raises:
        InvalidParameter: If profile/level is invalid or data is missing.
        NotAllowed: If constraints are violated.
    """
    check_h264_profile(
        frame_width, frame_height, colorspace, transfer_characteristic,
        interlace_mode, components, grain_rate, profile, level, bit_rate,
    )

    if interlace_mode is not Progressive:
        raise InvalidParameter("H.264 does not support interlaced flows")

    frame_rate_num, frame_rate_den = _get_rate(grain_rate)

    level_info = h264.ALL_LEVELS.get(level)
    if level_info is None:
        raise InvalidParameter("invalid level")

    macro_blocks = int(math.ceil(frame_width / 16) * math.ceil(frame_height / 16))

    if macro_blocks > level_info.max_size:
        raise NotAllowed("invalid level")

    macro_blocks_rate = macro_blocks * frame_rate_num // frame_rate_den

    if macro_blocks_rate > level_info.max_rate:
        raise NotAllowed("invalid level")

    # Look up the profile-specific bitrate limit for this level
    attr = h264.PROFILE_BITRATE_ATTR.get(profile)
    if attr is None:
        raise InvalidParameter("invalid profile")
    max_bitrate = getattr(level_info, attr)

    if bit_rate > max_bitrate:
        raise NotAllowed("invalid level")


def get_h264_max_bitrate(
    frame_width: int,
    frame_height: int,
    colorspace: EnumId,
    transfer_characteristic: EnumId,
    interlace_mode: EnumId,
    components: list[Any],
    grain_rate: Any,
    profile: EnumId,
    level: EnumId,
) -> int:
    """Return the maximum allowed bitrate (Kbps) for an H.264 profile/level.

    Validates the profile first, then looks up the per-profile bitrate
    limit from the level table.

    Raises:
        InvalidParameter: If profile or level is invalid.
    """
    check_h264_profile(
        frame_width, frame_height, colorspace, transfer_characteristic,
        interlace_mode, components, grain_rate, profile, level, 0,
    )

    level_info = h264.ALL_LEVELS.get(level)
    if level_info is None:
        raise InvalidParameter("invalid level")

    attr = h264.PROFILE_BITRATE_ATTR.get(profile)
    if attr is None:
        raise InvalidParameter("invalid profile")

    return cast(int, getattr(level_info, attr))


def select_h264_level_from_coded_flow(flow: Any) -> None:
    """Select the appropriate H.264 level for a coded video flow.

    Iterates through H.264 ordered levels from lowest to highest, finding
    the first level whose macroblock size, macroblock rate, and per-profile
    bitrate constraints all accommodate the flow's properties.

    Mutates flow.Level to the selected level.

    Raises:
        InvalidParameter: If required flow properties are missing/invalid.
        NotAllowed: If no level can accommodate the flow.
    """
    try:
        interlace_mode = flow.InterlaceMode.value
    except NotAvailable:
        raise InvalidParameter("missing interlace mode")

    if interlace_mode is not Progressive:
        raise InvalidParameter("H.264 does not support interlaced flows")

    try:
        components = flow.Components.value
    except NotAvailable:
        raise InvalidParameter("missing components")

    try:
        bit_depth = components[0].BitDepth.value
    except NotAvailable:
        raise InvalidParameter("missing bit depth")

    sampling = get_sdp_color_sampling(components)

    try:
        width = flow.FrameWidth.value
    except NotAvailable:
        raise InvalidParameter("missing width")

    try:
        height = flow.FrameHeight.value
    except NotAvailable:
        raise InvalidParameter("missing height")

    try:
        frame_rate = flow.FlowCore.GrainRate.value
    except NotAvailable:
        raise InvalidParameter("missing grain rate")

    frame_rate_num, frame_rate_den = _get_rate(frame_rate)

    try:
        bit_rate = flow.Bitrate.value
    except NotAvailable:
        raise InvalidParameter("missing bit rate")

    try:
        profile = flow.Profile.value
    except NotAvailable:
        raise InvalidParameter("missing profile")

    # Validate profile against flow properties
    profile_info = h264.ALL_PROFILES.get(profile)
    if profile_info is None:
        raise InvalidParameter("invalid profile")

    if bit_depth > profile_info.max_bit_depth:
        raise NotAllowed("profile not matching bit depth requirements")

    _check_video_color_sampling(sampling, profile_info.color_sampling)

    # Find the first level that satisfies all constraints
    attr = h264.PROFILE_BITRATE_ATTR.get(profile)
    if attr is None:
        raise InvalidParameter("invalid profile")

    selected_level: EnumId | None = None

    for check_level in h264.ORDERED_LEVELS:
        level_info = h264.ALL_LEVELS[check_level]

        macro_blocks = int(math.ceil(width / 16) * math.ceil(height / 16))

        if macro_blocks > level_info.max_size:
            continue

        macro_blocks_rate = macro_blocks * frame_rate_num // frame_rate_den

        if macro_blocks_rate > level_info.max_rate:
            continue

        max_bitrate = getattr(level_info, attr)

        if bit_rate > max_bitrate:
            continue

        selected_level = check_level
        break

    if selected_level is None:
        raise NotAllowed("cannot find an appropriate level")

    flow.Level.value = selected_level


# ===========================================================================
# H.265 functions
# ===========================================================================

def check_h265_profile(
    frame_width: int,
    frame_height: int,
    colorspace: EnumId,
    transfer_characteristic: EnumId,
    interlace_mode: EnumId,
    components: list[Any],
    grain_rate: Any,
    profile: EnumId,
    level: EnumId,
    bit_rate: int,
) -> None:
    """Validate that an H.265 profile is compatible with flow properties.

    Same checks as H.264: progressive-only, bit depth, color sampling.

    Raises:
        InvalidParameter: If interlace mode is not progressive or data is missing.
        NotAllowed: If bit depth or color sampling violates the profile.
    """
    if interlace_mode is not Progressive:
        raise InvalidParameter("H.265 does not support interlaced flows")

    try:
        bit_depth = components[0].BitDepth.value
    except NotAvailable:
        raise InvalidParameter("missing bit depth")

    sampling = get_sdp_color_sampling(components)

    profile_info = h265.ALL_PROFILES.get(profile)
    if profile_info is None:
        raise InvalidParameter("invalid profile")

    if bit_depth > profile_info.max_bit_depth:
        raise NotAllowed("profile not matching bit depth requirements")

    _check_video_color_sampling(sampling, profile_info.color_sampling)


def check_h265_profile_level(
    frame_width: int,
    frame_height: int,
    colorspace: EnumId,
    transfer_characteristic: EnumId,
    interlace_mode: EnumId,
    components: list[Any],
    grain_rate: Any,
    profile: EnumId,
    level: EnumId,
    bit_rate: int,
) -> None:
    """Validate H.265 profile and level constraints.

    H.265 uses sample counts (width × height) instead of macroblocks:
    - Sample count ≤ level MaxSize
    - Sample rate (samples × fps) ≤ level MaxRate
    - Bitrate ≤ level's per-profile max bitrate (Kbps)

    Raises:
        InvalidParameter: If profile/level is invalid or data is missing.
        NotAllowed: If constraints are violated.
    """
    check_h265_profile(
        frame_width, frame_height, colorspace, transfer_characteristic,
        interlace_mode, components, grain_rate, profile, level, bit_rate,
    )

    frame_rate_num, frame_rate_den = _get_rate(grain_rate)

    level_info = h265.ALL_LEVELS.get(level)
    if level_info is None:
        raise InvalidParameter("invalid level")

    # NOTE: H.265 sample count here uses a macroblock-based calculation
    # (ceil(w/16)*ceil(h/16)).
    samples = int(math.ceil(frame_width / 16) * math.ceil(frame_height / 16))

    if samples > level_info.max_size:
        raise NotAllowed("invalid level")

    samples_rate = samples * frame_rate_num // frame_rate_den

    if samples_rate > level_info.max_rate:
        raise NotAllowed("invalid level")

    attr = h265.PROFILE_BITRATE_ATTR.get(profile)
    if attr is None:
        raise InvalidParameter("invalid profile")
    max_bitrate = getattr(level_info, attr)

    if bit_rate > max_bitrate:
        raise NotAllowed("invalid level")


def get_h265_max_bitrate(
    frame_width: int,
    frame_height: int,
    colorspace: EnumId,
    transfer_characteristic: EnumId,
    interlace_mode: EnumId,
    components: list[Any],
    grain_rate: Any,
    profile: EnumId,
    level: EnumId,
) -> int:
    """Return the maximum allowed bitrate (Kbps) for an H.265 profile/level.

    Raises:
        InvalidParameter: If profile or level is invalid.
    """
    check_h265_profile(
        frame_width, frame_height, colorspace, transfer_characteristic,
        interlace_mode, components, grain_rate, profile, level, 0,
    )

    level_info = h265.ALL_LEVELS.get(level)
    if level_info is None:
        raise InvalidParameter("invalid level")

    attr = h265.PROFILE_BITRATE_ATTR.get(profile)
    if attr is None:
        raise InvalidParameter("invalid profile")

    return cast(int, getattr(level_info, attr))


def select_h265_level_from_coded_flow(flow: Any) -> None:
    """Select the appropriate H.265 level for a coded video flow.

    Uses sample-based constraints (width × height) instead of macroblocks.
    Mutates flow.Level to the selected level.

    Raises:
        InvalidParameter: If required flow properties are missing/invalid.
        NotAllowed: If no level can accommodate the flow.
    """
    try:
        interlace_mode = flow.InterlaceMode.value
    except NotAvailable:
        raise InvalidParameter("missing interlace mode")

    if interlace_mode is not Progressive:
        raise InvalidParameter("H.265 does not support interlaced flows")

    try:
        components = flow.Components.value
    except NotAvailable:
        raise InvalidParameter("missing components")

    try:
        bit_depth = components[0].BitDepth.value
    except NotAvailable:
        raise InvalidParameter("missing bit depth")

    sampling = get_sdp_color_sampling(components)

    try:
        width = flow.FrameWidth.value
    except NotAvailable:
        raise InvalidParameter("missing width")

    try:
        height = flow.FrameHeight.value
    except NotAvailable:
        raise InvalidParameter("missing height")

    try:
        frame_rate = flow.FlowCore.GrainRate.value
    except NotAvailable:
        raise InvalidParameter("missing grain rate")

    frame_rate_num, frame_rate_den = _get_rate(frame_rate)

    try:
        bit_rate = flow.Bitrate.value
    except NotAvailable:
        raise InvalidParameter("missing bit rate")

    try:
        profile = flow.Profile.value
    except NotAvailable:
        raise InvalidParameter("missing profile")

    profile_info = h265.ALL_PROFILES.get(profile)
    if profile_info is None:
        raise InvalidParameter("invalid profile")

    if bit_depth > profile_info.max_bit_depth:
        raise NotAllowed("profile not matching bit depth requirements")

    _check_video_color_sampling(sampling, profile_info.color_sampling)

    attr = h265.PROFILE_BITRATE_ATTR.get(profile)
    if attr is None:
        raise InvalidParameter("invalid profile")

    selected_level: EnumId | None = None

    for check_level in h265.ORDERED_LEVELS:
        level_info = h265.ALL_LEVELS[check_level]

        samples = int(width * height)

        if samples > level_info.max_size:
            continue

        samples_rate = samples * frame_rate_num // frame_rate_den

        if samples_rate > level_info.max_rate:
            continue

        max_bitrate = getattr(level_info, attr)

        if bit_rate > max_bitrate:
            continue

        selected_level = check_level
        break

    if selected_level is None:
        raise NotAllowed("cannot find an appropriate level")

    flow.Level.value = selected_level


# ===========================================================================
# JXSV functions
# ===========================================================================

def check_jxsv_profile(
    frame_width: int,
    frame_height: int,
    colorspace: EnumId,
    transfer_characteristic: EnumId,
    interlace_mode: EnumId,
    components: list[Any],
    grain_rate: Any,
    profile: EnumId,
    level: EnumId,
    sublevel: EnumId,
    bit_rate: int,
) -> None:
    """Validate that a JXSV profile is compatible with flow properties.

    Same pattern as H.264/H.265: progressive-only, bit depth, color sampling.

    Raises:
        InvalidParameter: If interlace mode is not progressive or data is missing.
        NotAllowed: If bit depth or color sampling violates the profile.
    """
    if interlace_mode is not Progressive:
        raise InvalidParameter("jxsv does not support interlaced flows")

    try:
        bit_depth = components[0].BitDepth.value
    except NotAvailable:
        raise InvalidParameter("missing bit depth")

    sampling = get_sdp_color_sampling(components)

    profile_info = jxsv.ALL_PROFILES.get(profile)
    if profile_info is None:
        raise InvalidParameter("invalid profile")

    if bit_depth > profile_info.max_bit_depth:
        raise NotAllowed("profile not matching bit depth requirements")

    _check_video_color_sampling(sampling, profile_info.color_sampling)


def check_jxsv_profile_level(
    frame_width: int,
    frame_height: int,
    colorspace: EnumId,
    transfer_characteristic: EnumId,
    interlace_mode: EnumId,
    components: list[Any],
    grain_rate: Any,
    profile: EnumId,
    level: EnumId,
    sublevel: EnumId,
    bit_rate: int,
) -> None:
    """Validate JXSV profile, level, and sublevel constraints.

    JXSV uses sample counts (width × height) like H.265, but the bitrate
    constraint is keyed by sublevel (bpp budget), not by profile.

    Raises:
        InvalidParameter: If profile/level/sublevel is invalid or data is missing.
        NotAllowed: If constraints are violated.
    """
    check_jxsv_profile(
        frame_width, frame_height, colorspace, transfer_characteristic,
        interlace_mode, components, grain_rate, profile, level, sublevel, bit_rate,
    )

    frame_rate_num, frame_rate_den = _get_rate(grain_rate)

    level_info = jxsv.ALL_LEVELS.get(level)
    if level_info is None:
        raise InvalidParameter("invalid level")

    samples = int(frame_width * frame_height)

    if samples > level_info.max_size:
        raise NotAllowed("invalid level")

    samples_rate = samples * frame_rate_num // frame_rate_den

    if samples_rate > level_info.max_rate:
        raise NotAllowed("invalid level")

    # JXSV: bitrate is keyed by sublevel, not profile
    attr = jxsv.SUBLEVEL_BITRATE_ATTR.get(sublevel)
    if attr is None:
        raise InvalidParameter("invalid sublevel")
    max_bitrate = getattr(level_info, attr)

    if bit_rate > max_bitrate:
        raise NotAllowed("invalid level, sublevel combination")


def get_jxsv_max_bitrate(
    frame_width: int,
    frame_height: int,
    colorspace: EnumId,
    transfer_characteristic: EnumId,
    interlace_mode: EnumId,
    components: list[Any],
    grain_rate: Any,
    profile: EnumId,
    level: EnumId,
    sublevel: EnumId,
) -> int:
    """Return the maximum allowed bitrate (Kbps) for a JXSV level/sublevel.

    Raises:
        InvalidParameter: If profile, level, or sublevel is invalid.
    """
    check_jxsv_profile(
        frame_width, frame_height, colorspace, transfer_characteristic,
        interlace_mode, components, grain_rate, profile, level, sublevel, 0,
    )

    level_info = jxsv.ALL_LEVELS.get(level)
    if level_info is None:
        raise InvalidParameter("invalid level")

    attr = jxsv.SUBLEVEL_BITRATE_ATTR.get(sublevel)
    if attr is None:
        raise InvalidParameter("invalid sublevel")

    return cast(int, getattr(level_info, attr))


def select_jxsv_level_from_coded_flow(flow: Any) -> None:
    """Select the appropriate JXSV level for a coded video flow.

    Uses sample-based constraints.  Bitrate is checked against the
    flow's sublevel (not profile).  Mutates flow.Level.

    Raises:
        InvalidParameter: If required flow properties are missing/invalid.
        NotAllowed: If no level can accommodate the flow.
    """
    try:
        interlace_mode = flow.InterlaceMode.value
    except NotAvailable:
        raise InvalidParameter("missing interlace mode")

    if interlace_mode is not Progressive:
        raise InvalidParameter("H.264 does not support interlaced flows")

    try:
        components = flow.Components.value
    except NotAvailable:
        raise InvalidParameter("missing components")

    try:
        bit_depth = components[0].BitDepth.value
    except NotAvailable:
        raise InvalidParameter("missing bit depth")

    sampling = get_sdp_color_sampling(components)

    try:
        width = flow.FrameWidth.value
    except NotAvailable:
        raise InvalidParameter("missing width")

    try:
        height = flow.FrameHeight.value
    except NotAvailable:
        raise InvalidParameter("missing height")

    try:
        frame_rate = flow.FlowCore.GrainRate.value
    except NotAvailable:
        raise InvalidParameter("missing grain rate")

    frame_rate_num, frame_rate_den = _get_rate(frame_rate)

    try:
        bit_rate = flow.Bitrate.value
    except NotAvailable:
        raise InvalidParameter("missing bit rate")

    try:
        profile = flow.Profile.value
    except NotAvailable:
        raise InvalidParameter("missing profile")

    try:
        sublevel = flow.Sublevel.value
    except NotAvailable:
        raise InvalidParameter("missing sublevel")

    profile_info = jxsv.ALL_PROFILES.get(profile)
    if profile_info is None:
        raise InvalidParameter("invalid profile")

    if bit_depth > profile_info.max_bit_depth:
        raise NotAllowed("profile not matching bit depth requirements")

    _check_video_color_sampling(sampling, profile_info.color_sampling)

    attr = jxsv.SUBLEVEL_BITRATE_ATTR.get(sublevel)
    if attr is None:
        raise InvalidParameter("invalid sublevel")

    selected_level: EnumId | None = None

    for check_level in jxsv.ORDERED_LEVELS:
        level_info = jxsv.ALL_LEVELS[check_level]

        samples = int(width * height)

        if samples > level_info.max_size:
            continue

        samples_rate = samples * frame_rate_num // frame_rate_den

        if samples_rate > level_info.max_rate:
            continue

        max_bitrate = getattr(level_info, attr)

        if bit_rate > max_bitrate:
            continue

        selected_level = check_level
        break

    if selected_level is None:
        raise NotAllowed("cannot find an appropriate level")

    flow.Level.value = selected_level


# ===========================================================================
# AAC functions
# ===========================================================================

def check_aac_profile(
    object_type: int,
    channels: int,
    sampling_rate_value: Any,
    profile: EnumId,
    level: EnumId,
    bit_rate: int,
) -> None:
    """Validate an AAC profile against audio flow properties.

    Checks:
    - Channel count is 1, 2, or 6 (5.1 surround)
    - Profile exists and supports the given object type
    - Bitrate does not exceed per-channel maximum (5.1 counts as 5 channels
      for bitrate, but 6 for channel limit)

    Raises:
        InvalidParameter: If channels or profile is invalid.
        NotAllowed: If object type or bitrate violates the profile.
    """
    if channels not in (1, 2, 6):
        raise InvalidParameter("invalid number of channels")

    profile_info = aac.ALL_PROFILES.get(profile)
    if profile_info is None:
        raise InvalidParameter("invalid profile")

    # Check if the AAC object type is supported by the profile
    if object_type not in profile_info.objects:
        raise NotAllowed("profile not matching objects requirements")

    # Calculate max bitrate: 5.1 surround uses 5 channels for bitrate calc
    if channels == 6:
        max_bitrate = profile_info.max_bitrate_per_channel * 5
    else:
        max_bitrate = profile_info.max_bitrate_per_channel * channels

    if bit_rate > max_bitrate:
        raise NotAllowed("profile/level not matching bitrate requirements")


def check_aac_profile_level(
    object_type: int,
    channels: int,
    sampling_rate_value: Any,
    profile: EnumId,
    level: EnumId,
    bit_rate: int,
) -> None:
    """Validate AAC profile and level constraints.

    First validates the profile, then checks level-specific constraints:
    - Channel count ≤ level MaxChannels
    - Sampling rate ≤ level MaxRate

    Raises:
        InvalidParameter: If profile or level is invalid.
        NotAllowed: If constraints are violated.
    """
    check_aac_profile(object_type, channels, sampling_rate_value, profile, level, bit_rate)

    sampling_rate_num, sampling_rate_den = _get_rate(sampling_rate_value)
    sampling_rate = sampling_rate_num // sampling_rate_den

    profile_info = aac.ALL_PROFILES.get(profile)
    if profile_info is None:
        raise InvalidParameter("invalid profile")

    level_info = profile_info.levels.get(level)
    if level_info is None:
        raise InvalidParameter("invalid level")

    if channels > level_info.max_channels:
        raise NotAllowed("profile/level not matching channels requirements")

    if sampling_rate > level_info.max_rate:
        raise NotAllowed("profile/level not matching sampling rate requirements")


def select_aac_level_from_coded_flow(
    flow: Any,
    source: Any,
    allowed_ordered_levels: list[EnumId] | None = None,
) -> None:
    """Select the appropriate AAC level for a coded audio flow.

    Iterates through the allowed ordered levels (defaulting to the full
    ORDERED_LEVELS list) and selects the first level whose channel and
    sampling rate constraints are satisfied.

    Mutates flow.Level to the selected level.

    Args:
        flow: NFlowAudioCodedValue — the coded audio flow to update.
        source: NSourceAudioValue — the audio source (for channel count).
        allowed_ordered_levels: Optional subset of levels to consider.
            Defaults to aac.ORDERED_LEVELS if None or empty.

    Raises:
        InvalidParameter: If required properties are missing/invalid.
        NotAllowed: If no level can accommodate the flow.
    """
    if not allowed_ordered_levels:
        allowed_ordered_levels = aac.ORDERED_LEVELS

    try:
        array_of_channels = source.Channels.value
    except NotAvailable:
        raise InvalidParameter("missing source channels")

    if len(array_of_channels) == 0:
        raise InvalidParameter("missing source channels")

    channels = len(array_of_channels)

    if channels not in (1, 2, 6):
        raise InvalidParameter("invalid number of channels")

    try:
        sampling_rate_value = flow.FlowCore.GrainRate.value
    except NotAvailable:
        raise InvalidParameter("missing sampling rate")

    sampling_rate_num, sampling_rate_den = _get_rate(sampling_rate_value)
    sampling_rate = sampling_rate_num // sampling_rate_den

    try:
        bit_rate = flow.Bitrate.value
    except NotAvailable:
        raise InvalidParameter("missing bit rate")

    try:
        profile = flow.Profile.value
    except NotAvailable:
        raise InvalidParameter("missing profile")

    profile_info = aac.ALL_PROFILES.get(profile)
    if profile_info is None:
        raise InvalidParameter("invalid profile")

    # Calculate max bitrate: 5.1 surround uses 5 channels for bitrate calc
    if channels == 6:
        max_bitrate = profile_info.max_bitrate_per_channel * 5
    else:
        max_bitrate = profile_info.max_bitrate_per_channel * channels

    if bit_rate > max_bitrate:
        raise InvalidParameter("invalid profile")

    selected_level: EnumId | None = None

    for check_level in allowed_ordered_levels:
        level_info = profile_info.levels[check_level]

        if channels > level_info.max_channels:
            continue

        if sampling_rate > level_info.max_rate:
            continue

        selected_level = check_level
        break

    if selected_level is None:
        raise NotAllowed("cannot find an appropriate level")

    flow.Level.value = selected_level
