# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""H.264/AVC codec profile and level specification tables.

Profiles define which color sampling modes and bit depths are allowed.
Levels define maximum macroblock rates, sizes, and per-profile bitrates.

The PROFILE_BITRATE_ATTR dict maps each profile enum to the corresponding
LevelInfo attribute name, allowing bitrate lookups via a single getattr()
call instead of a large switch over profile values.
"""

from __future__ import annotations

from dataclasses import dataclass

from nmos.enums import (
    EnumId,
    EnumRegistry,
    # H.264 profiles
    H264ProfileBaseline,
    H264ProfileBaselineConstrained,
    CodecProfileMain,
    H264ProfileExtended,
    H264ProfileHigh,
    H264ProfileHighProgressive,
    H264ProfileHighConstrained,
    H264ProfileHigh10,
    H264ProfileHigh10Progressive,
    H264ProfileHigh_422,
    H264ProfileHighPredictive_444,
    H264ProfileHigh10Intra,
    H264ProfileHighIntra_422,
    H264ProfileHighIntra_444,
    H264ProfileCAVLCIntra_444,
    # Shared codec levels
    CodecLevel1,
    CodecLevel1b,
    CodecLevel1_1,
    CodecLevel1_2,
    CodecLevel1_3,
    CodecLevel2,
    CodecLevel2_1,
    CodecLevel2_2,
    CodecLevel3,
    CodecLevel3_1,
    CodecLevel3_2,
    CodecLevel4,
    CodecLevel4_1,
    CodecLevel4_2,
    CodecLevel5,
    CodecLevel5_1,
    CodecLevel5_2,
    CodecLevel6,
    CodecLevel6_1,
    CodecLevel6_2,
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LevelInfo:
    """H.264 level constraints.

    max_rate and max_size are in macroblock units.
    All max_bitrate_* fields are in Kbps.  Each field corresponds to the
    maximum bitrate allowed for a specific H.264 profile at this level.
    """

    max_rate: int                           # macroblocks / second
    max_size: int                           # macroblocks
    max_bitrate_baseline: int
    max_bitrate_constrained_baseline: int
    max_bitrate_main: int
    max_bitrate_extended: int
    max_bitrate_high: int
    max_bitrate_high_progressive: int
    max_bitrate_constrained_high: int
    max_bitrate_high10: int
    max_bitrate_high10_progressive: int
    max_bitrate_high_422: int
    max_bitrate_high_predictive_444: int
    max_bitrate_high10_intra: int
    max_bitrate_high_intra_422: int
    max_bitrate_high_intra_444: int
    max_bitrate_cavlc_intra_444: int


@dataclass(frozen=True)
class ProfileInfo:
    """H.264 profile constraints.

    color_sampling lists the chroma sub-sampling modes the profile supports
    (e.g. ["4:2:0"], ["monochrome", "4:2:0", "4:2:2", "4:4:4"]).
    max_bit_depth is the maximum luma/chroma bit depth the profile allows.
    """

    color_sampling: list[str]
    max_bit_depth: int


# ---------------------------------------------------------------------------
# Ordered level list — iteration order for automatic level selection
# ---------------------------------------------------------------------------

ORDERED_LEVELS: list[EnumId] = [
    CodecLevel1,
    CodecLevel1b,
    CodecLevel1_1,
    CodecLevel1_2,
    CodecLevel1_3,
    CodecLevel2,
    CodecLevel2_1,
    CodecLevel2_2,
    CodecLevel3,
    CodecLevel3_1,
    CodecLevel3_2,
    CodecLevel4,
    CodecLevel4_1,
    CodecLevel4_2,
    CodecLevel5,
    CodecLevel5_1,
    CodecLevel5_2,
    CodecLevel6,
    CodecLevel6_1,
    CodecLevel6_2,
]


# ---------------------------------------------------------------------------
# Level specification table
# Fields per entry (in order):
#   max_rate, max_size,
#   baseline, constrained_baseline, main, extended,
#   high, high_progressive, constrained_high,
#   high10, high10_progressive, high_422, high_predictive_444,
#   high10_intra, high_intra_422, high_intra_444, cavlc_intra_444
# ---------------------------------------------------------------------------

ALL_LEVELS: dict[EnumId, LevelInfo] = {
    CodecLevel1:   LevelInfo(1485, 99, 64, 64, 64, 64, 80, 80, 80, 192, 192, 256, 256, 192, 256, 256, 256),
    CodecLevel1b:  LevelInfo(1485, 99, 128, 128, 128, 128, 160, 160, 160, 384, 384, 512, 512, 384, 512, 512, 512),
    CodecLevel1_1: LevelInfo(3000, 396, 192, 192, 192, 192, 240, 240, 240, 576, 576, 768, 768, 576, 768, 768, 768),
    CodecLevel1_2: LevelInfo(6000, 396, 384, 384, 384, 384, 480, 480, 480, 1152, 1152, 1536, 1536, 1152, 1536, 1536, 1536),
    CodecLevel1_3: LevelInfo(11880, 396, 768, 768, 768, 768, 960, 960, 960, 2304, 2304, 3072, 3072, 2304, 3072, 3072, 3072),
    CodecLevel2:   LevelInfo(11880, 396, 2000, 2000, 2000, 2000, 2500, 2500, 2500, 6000, 6000, 8000, 8000, 6000, 8000, 8000, 8000),
    CodecLevel2_1: LevelInfo(19800, 792, 4000, 4000, 4000, 4000, 5000, 5000, 5000, 12000, 12000, 16000, 16000, 12000, 16000, 16000, 16000),
    CodecLevel2_2: LevelInfo(20250, 1620, 4000, 4000, 4000, 4000, 5000, 5000, 5000, 12000, 12000, 16000, 16000, 12000, 16000, 16000, 16000),
    CodecLevel3:   LevelInfo(40500, 1620, 10000, 10000, 10000, 10000, 12500, 12500, 12500, 30000, 30000, 40000, 40000, 30000, 40000, 40000, 40000),
    CodecLevel3_1: LevelInfo(108000, 3600, 14000, 14000, 14000, 14000, 17500, 17500, 17500, 42000, 42000, 56000, 56000, 42000, 56000, 56000, 56000),
    CodecLevel3_2: LevelInfo(216000, 5120, 20000, 20000, 20000, 20000, 25000, 25000, 25000, 60000, 60000, 80000, 80000, 60000, 80000, 80000, 80000),
    CodecLevel4:   LevelInfo(245760, 8192, 20000, 20000, 20000, 20000, 25000, 25000, 25000, 60000, 60000, 80000, 80000, 60000, 80000, 80000, 80000),
    CodecLevel4_1: LevelInfo(245760, 8192, 50000, 50000, 50000, 50000, 62500, 62500, 62500, 150000, 150000, 200000, 200000, 150000, 200000, 200000, 200000),
    CodecLevel4_2: LevelInfo(522240, 8704, 50000, 50000, 50000, 50000, 62500, 62500, 62500, 150000, 150000, 200000, 200000, 150000, 200000, 200000, 200000),
    CodecLevel5:   LevelInfo(589824, 22080, 135000, 135000, 135000, 135000, 168750, 168750, 168750, 405000, 405000, 540000, 540000, 405000, 540000, 540000, 540000),
    CodecLevel5_1: LevelInfo(983040, 36864, 240000, 240000, 240000, 240000, 300000, 300000, 300000, 720000, 720000, 960000, 960000, 720000, 960000, 960000, 960000),
    CodecLevel5_2: LevelInfo(2073600, 36864, 240000, 240000, 240000, 240000, 300000, 300000, 300000, 720000, 720000, 960000, 960000, 720000, 960000, 960000, 960000),
    CodecLevel6:   LevelInfo(4177920, 139264, 240000, 240000, 240000, 240000, 300000, 300000, 300000, 720000, 720000, 960000, 960000, 720000, 960000, 960000, 960000),
    CodecLevel6_1: LevelInfo(8355840, 139264, 480000, 480000, 480000, 480000, 600000, 600000, 600000, 1440000, 1440000, 1920000, 1920000, 1440000, 1920000, 1920000, 1920000),
    CodecLevel6_2: LevelInfo(16711680, 139264, 800000, 800000, 800000, 800000, 1000000, 1000000, 1000000, 2400000, 2400000, 3200000, 3200000, 2400000, 3200000, 3200000, 3200000),
}


# ---------------------------------------------------------------------------
# Profile specification table
# ---------------------------------------------------------------------------

ALL_PROFILES: dict[EnumId, ProfileInfo] = {
    H264ProfileBaseline:            ProfileInfo(["4:2:0"], 8),
    H264ProfileBaselineConstrained: ProfileInfo(["4:2:0"], 8),
    CodecProfileMain:               ProfileInfo(["4:2:0"], 8),
    H264ProfileExtended:            ProfileInfo(["4:2:0"], 8),
    H264ProfileHigh:                ProfileInfo(["monochrome", "4:2:0"], 8),
    H264ProfileHighProgressive:     ProfileInfo(["monochrome", "4:2:0"], 8),
    H264ProfileHighConstrained:     ProfileInfo(["monochrome", "4:2:0"], 8),
    H264ProfileHigh10:              ProfileInfo(["monochrome", "4:2:0"], 10),
    H264ProfileHigh10Progressive:   ProfileInfo(["monochrome", "4:2:0"], 10),
    H264ProfileHigh_422:            ProfileInfo(["monochrome", "4:2:0", "4:2:2"], 10),
    H264ProfileHighPredictive_444:  ProfileInfo(["monochrome", "4:2:0", "4:2:2", "4:4:4"], 14),
    H264ProfileHigh10Intra:         ProfileInfo(["monochrome", "4:2:0"], 10),
    H264ProfileHighIntra_422:       ProfileInfo(["monochrome", "4:2:0", "4:2:2"], 10),
    H264ProfileHighIntra_444:       ProfileInfo(["monochrome", "4:2:0", "4:2:2", "4:4:4"], 14),
    H264ProfileCAVLCIntra_444:      ProfileInfo(["monochrome", "4:2:0", "4:2:2", "4:4:4"], 14),
}


# ---------------------------------------------------------------------------
# Profile → LevelInfo bitrate attribute mapping
#
# Provides a 15-entry table used by CheckH264ProfileLevel, GetH264MaxBitrate,
# and SelectH264LevelFromCodedFlow.  Usage:
#   attr = PROFILE_BITRATE_ATTR[profile]
#   max_bitrate = getattr(level_info, attr)
# ---------------------------------------------------------------------------

PROFILE_BITRATE_ATTR: dict[EnumId, str] = {
    H264ProfileBaselineConstrained: "max_bitrate_constrained_baseline",
    H264ProfileBaseline:            "max_bitrate_baseline",
    CodecProfileMain:               "max_bitrate_main",
    H264ProfileExtended:            "max_bitrate_extended",
    H264ProfileHigh:                "max_bitrate_high",
    H264ProfileHighProgressive:     "max_bitrate_high_progressive",
    H264ProfileHighConstrained:     "max_bitrate_constrained_high",
    H264ProfileHigh10:              "max_bitrate_high10",
    H264ProfileHigh10Progressive:   "max_bitrate_high10_progressive",
    H264ProfileHigh_422:            "max_bitrate_high_422",
    H264ProfileHighPredictive_444:  "max_bitrate_high_predictive_444",
    H264ProfileHigh10Intra:         "max_bitrate_high10_intra",
    H264ProfileHighIntra_422:       "max_bitrate_high_intra_422",
    H264ProfileHighIntra_444:       "max_bitrate_high_intra_444",
    H264ProfileCAVLCIntra_444:      "max_bitrate_cavlc_intra_444",
}
