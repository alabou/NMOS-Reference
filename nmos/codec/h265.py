# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""H.265/HEVC codec profile and level specification tables.

H.265 levels come in Main-tier and High-tier variants.  The ordered level
list interleaves them so that automatic level selection picks the lowest
tier/level combination that satisfies the constraints.

Each LevelInfo carries 36 per-profile bitrate fields (Kbps), one for each
of the 36 H.265 profiles defined in ALL_PROFILES.
"""

from __future__ import annotations

from dataclasses import dataclass

from nmos.enums import (
    EnumId,
    EnumRegistry,
    # Shared profile (used by both H.264 and H.265)
    CodecProfileMain,
    # H.265 profiles
    H265ProfileMain10,
    H265ProfileMain10StillPicture,
    H265ProfileMainStillPicture,
    H265ProfileMonochrome,
    H265ProfileMonochrome10,
    H265ProfileMonochrome12,
    H265ProfileMonochrome16,
    H265ProfileMain12,
    H265ProfileMain10_422,
    H265ProfileMain12_422,
    H265ProfileMain_444,
    H265ProfileMain10_444,
    H265ProfileMain12_444,
    H265ProfileMainIntra,
    H265ProfileMain10Intra,
    H265ProfileMain12Intra,
    H265ProfileMain10Intra_422,
    H265ProfileMain12Intra_422,
    H265ProfileMainIntra_444,
    H265ProfileMain10Intra_444,
    H265ProfileMain12Intra_444,
    H265ProfileMain16Intra_444,
    H265ProfileMainStillPicture_444,
    H265ProfileMain16StillPicture_444,
    H265ProfileHighThroughput_444,
    H265ProfileHighThroughput10_444,
    H265ProfileHighThroughput14_444,
    H265ProfileHighThroughput16Intra_444,
    H265ProfileScreenExtendedMain,
    H265ProfileScreenExtendedMain10,
    H265ProfileScreenExtendedMain_444,
    H265ProfileScreenExtendedMain10_444,
    H265ProfileScreenExtendedHighThroughput_444,
    H265ProfileScreenExtendedHighThroughput10_444,
    H265ProfileScreenExtendedHighThroughput14_444,
    # H.265 levels
    H265LevelMain1,
    H265LevelMain2,
    H265LevelMain2_1,
    H265LevelMain3,
    H265LevelMain3_1,
    H265LevelMain4,
    H265LevelMain4_1,
    H265LevelMain5,
    H265LevelMain5_1,
    H265LevelMain5_2,
    H265LevelMain6,
    H265LevelMain6_1,
    H265LevelMain6_2,
    H265LevelHigh4,
    H265LevelHigh4_1,
    H265LevelHigh5,
    H265LevelHigh5_1,
    H265LevelHigh5_2,
    H265LevelHigh6,
    H265LevelHigh6_1,
    H265LevelHigh6_2,
    H265LevelHigh8_5,
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LevelInfo:
    """H.265 level constraints.

    max_rate and max_size are in sample (pixel) units.
    All max_bitrate_* fields are in Kbps.  Each field corresponds to the
    maximum bitrate allowed for a specific H.265 profile at this level.
    high_tier indicates whether this is a High-tier level (vs Main-tier).
    """

    high_tier: bool
    max_rate: int                                           # samples / second
    max_size: int                                           # samples
    # Per-profile maximum bitrate fields (Kbps) — 36 fields
    max_bitrate_main: int
    max_bitrate_main10: int
    max_bitrate_main10_still: int
    max_bitrate_main_still: int
    max_bitrate_monochrome: int
    max_bitrate_monochrome10: int
    max_bitrate_monochrome12: int
    max_bitrate_monochrome16: int
    max_bitrate_main12: int
    max_bitrate_main10_422: int
    max_bitrate_main12_422: int
    max_bitrate_main_444: int
    max_bitrate_main10_444: int
    max_bitrate_main12_444: int
    max_bitrate_main_intra: int
    max_bitrate_main10_intra: int
    max_bitrate_main12_intra: int
    max_bitrate_main10_intra_422: int
    max_bitrate_main12_intra_422: int
    max_bitrate_main_intra_444: int
    max_bitrate_main10_intra_444: int
    max_bitrate_main12_intra_444: int
    max_bitrate_main16_intra_444: int
    max_bitrate_main_still_444: int
    max_bitrate_main16_still_444: int
    max_bitrate_high_throughput_444: int
    max_bitrate_high_throughput10_444: int
    max_bitrate_high_throughput14_444: int
    max_bitrate_high_throughput16_intra_444: int
    max_bitrate_screen_extended_main: int
    max_bitrate_screen_extended_main10: int
    max_bitrate_screen_extended_main_444: int
    max_bitrate_screen_extended_main10_444: int
    max_bitrate_screen_extended_high_throughput_444: int
    max_bitrate_screen_extended_high_throughput10_444: int
    max_bitrate_screen_extended_high_throughput14_444: int


@dataclass(frozen=True)
class ProfileInfo:
    """H.265 profile constraints.

    color_sampling lists the chroma sub-sampling modes the profile supports.
    max_bit_depth is the maximum luma/chroma bit depth the profile allows.
    """

    color_sampling: list[str]
    max_bit_depth: int


# ---------------------------------------------------------------------------
# Ordered level list — lowest to highest, interleaving Main/High tiers
# ---------------------------------------------------------------------------

ORDERED_LEVELS: list[EnumId] = [
    H265LevelMain1,
    H265LevelMain2,
    H265LevelMain2_1,
    H265LevelMain3,
    H265LevelMain3_1,
    H265LevelMain4,
    H265LevelHigh4,
    H265LevelMain4_1,
    H265LevelHigh4_1,
    H265LevelMain5,
    H265LevelHigh5,
    H265LevelMain5_1,
    H265LevelHigh5_1,
    H265LevelMain5_2,
    H265LevelHigh5_2,
    H265LevelMain6,
    H265LevelHigh6,
    H265LevelMain6_1,
    H265LevelHigh6_1,
    H265LevelMain6_2,
    H265LevelHigh6_2,
    H265LevelHigh8_5,
]


# ---------------------------------------------------------------------------
# Level specification table
#
# Fields per entry (in order):
#   high_tier, max_rate, max_size,
#   main, main10, main10_still, main_still,
#   monochrome, monochrome10, monochrome12, monochrome16,
#   main12, main10_422, main12_422,
#   main_444, main10_444, main12_444,
#   main_intra, main10_intra, main12_intra,
#   main10_intra_422, main12_intra_422,
#   main_intra_444, main10_intra_444, main12_intra_444, main16_intra_444,
#   main_still_444, main16_still_444,
#   high_throughput_444, high_throughput10_444, high_throughput14_444,
#   high_throughput16_intra_444,
#   screen_extended_main, screen_extended_main10,
#   screen_extended_main_444, screen_extended_main10_444,
#   screen_extended_high_throughput_444, screen_extended_high_throughput10_444,
#   screen_extended_high_throughput14_444
# ---------------------------------------------------------------------------

ALL_LEVELS: dict[EnumId, LevelInfo] = {
    # fmt: off
    H265LevelMain1:   LevelInfo(False, 552960, 36864, 128, 128, 128, 128, 85, 107, 128, 171, 192, 213, 256, 256, 320, 384, 256, 256, 3840, 427, 512, 512, 640, 768, 1024, 512, 1024, 1536, 1920, 2688, 12288, 128, 128, 256, 320, 1536, 1920, 2688),
    H265LevelMain2:   LevelInfo(False, 3686400, 122880, 1500, 1500, 1500, 1500, 1001, 1250, 1500, 2000, 2250, 2501, 3000, 3000, 3750, 4500, 3000, 3000, 45000, 5001, 6000, 6000, 7500, 9000, 12000, 6000, 12000, 18000, 22500, 31500, 144000, 1500, 1500, 3000, 3750, 18000, 22500, 31500),
    H265LevelMain2_1: LevelInfo(False, 7372800, 245760, 3000, 3000, 3000, 3000, 2001, 2499, 3000, 3999, 4500, 5001, 6000, 6000, 7500, 9000, 6000, 6000, 90000, 10002, 12000, 12000, 15000, 18000, 24000, 12000, 24000, 36000, 45000, 63000, 288000, 3000, 3000, 6000, 7500, 36000, 45000, 63000),
    H265LevelMain3:   LevelInfo(False, 16588800, 552960, 6000, 6000, 6000, 6000, 4002, 4998, 6000, 7998, 9000, 10002, 12000, 12000, 15000, 18000, 12000, 12000, 180000, 20004, 24000, 24000, 30000, 36000, 48000, 24000, 48000, 72000, 90000, 126000, 576000, 6000, 6000, 12000, 15000, 72000, 90000, 126000),
    H265LevelMain3_1: LevelInfo(False, 33177600, 983040, 10000, 10000, 10000, 10000, 6670, 8330, 10000, 13330, 15000, 16670, 20000, 20000, 25000, 30000, 20000, 20000, 300000, 33340, 40000, 40000, 50000, 60000, 80000, 40000, 80000, 120000, 150000, 210000, 960000, 10000, 10000, 20000, 25000, 120000, 150000, 210000),
    H265LevelMain4:   LevelInfo(False, 66846720, 2228224, 12000, 12000, 12000, 12000, 8004, 9996, 12000, 15996, 18000, 20004, 24000, 24000, 30000, 36000, 24000, 24000, 360000, 40008, 48000, 48000, 60000, 72000, 96000, 48000, 96000, 144000, 180000, 252000, 1152000, 12000, 12000, 24000, 30000, 144000, 180000, 252000),
    H265LevelMain4_1: LevelInfo(False, 133693440, 2228224, 20000, 20000, 20000, 20000, 13340, 16660, 20000, 26660, 30000, 33340, 40000, 40000, 50000, 60000, 40000, 40000, 600000, 66680, 80000, 80000, 100000, 120000, 160000, 80000, 160000, 240000, 300000, 420000, 1920000, 20000, 20000, 40000, 50000, 240000, 300000, 420000),
    H265LevelMain5:   LevelInfo(False, 267386880, 8912896, 25000, 25000, 25000, 25000, 16675, 20825, 25000, 33325, 37500, 41675, 50000, 50000, 62500, 75000, 50000, 50000, 750000, 83350, 100000, 100000, 125000, 150000, 200000, 100000, 200000, 300000, 375000, 525000, 2400000, 25000, 25000, 50000, 62500, 300000, 375000, 525000),
    H265LevelMain5_1: LevelInfo(False, 534773760, 8912896, 40000, 40000, 40000, 40000, 26680, 33320, 40000, 53320, 60000, 66680, 80000, 80000, 100000, 120000, 80000, 80000, 1200000, 133360, 160000, 160000, 200000, 240000, 320000, 160000, 320000, 480000, 600000, 840000, 3840000, 40000, 40000, 80000, 100000, 480000, 600000, 840000),
    H265LevelMain5_2: LevelInfo(False, 1069547520, 8912896, 60000, 60000, 60000, 60000, 40020, 49980, 60000, 79980, 90000, 100020, 120000, 120000, 150000, 180000, 120000, 120000, 1800000, 200040, 240000, 240000, 300000, 360000, 480000, 240000, 480000, 720000, 900000, 1260000, 5760000, 60000, 60000, 120000, 150000, 720000, 900000, 1260000),
    H265LevelMain6:   LevelInfo(False, 1069547520, 35651584, 60000, 60000, 60000, 60000, 40020, 49980, 60000, 79980, 90000, 100020, 120000, 120000, 150000, 180000, 120000, 120000, 1800000, 200040, 240000, 240000, 300000, 360000, 480000, 240000, 480000, 720000, 900000, 1260000, 5760000, 60000, 60000, 120000, 150000, 720000, 900000, 1260000),
    H265LevelMain6_1: LevelInfo(False, 2139095040, 35651584, 120000, 120000, 120000, 120000, 80040, 99960, 120000, 159960, 180000, 200040, 240000, 240000, 300000, 360000, 240000, 240000, 3600000, 400080, 480000, 480000, 600000, 720000, 960000, 480000, 960000, 1440000, 1800000, 2520000, 11520000, 120000, 120000, 240000, 300000, 1440000, 1800000, 2520000),
    H265LevelMain6_2: LevelInfo(False, 4278190080, 35651584, 240000, 240000, 240000, 240000, 160080, 199920, 240000, 319920, 360000, 400080, 480000, 480000, 600000, 720000, 480000, 480000, 7200000, 800160, 960000, 960000, 1200000, 1440000, 1920000, 960000, 1920000, 2880000, 3600000, 5040000, 23040000, 240000, 240000, 480000, 600000, 2880000, 3600000, 5040000),
    H265LevelHigh4:   LevelInfo(True, 66846720, 2228224, 30000, 30000, 30000, 30000, 20010, 24990, 30000, 39990, 45000, 50010, 60000, 60000, 75000, 90000, 60000, 60000, 900000, 100020, 120000, 120000, 150000, 180000, 240000, 120000, 240000, 360000, 450000, 630000, 2880000, 30000, 30000, 60000, 75000, 360000, 450000, 630000),
    H265LevelHigh4_1: LevelInfo(True, 133693440, 2228224, 50000, 50000, 50000, 50000, 33350, 41650, 50000, 66650, 75000, 83350, 100000, 100000, 125000, 150000, 100000, 100000, 1500000, 166700, 200000, 200000, 250000, 300000, 400000, 200000, 400000, 600000, 750000, 1050000, 4800000, 50000, 50000, 100000, 125000, 600000, 750000, 1050000),
    H265LevelHigh5:   LevelInfo(True, 267386880, 8912896, 100000, 100000, 100000, 100000, 66700, 83300, 100000, 133300, 150000, 166700, 200000, 200000, 250000, 300000, 200000, 200000, 3000000, 333400, 400000, 400000, 500000, 600000, 800000, 400000, 800000, 1200000, 1500000, 2100000, 9600000, 100000, 100000, 200000, 250000, 1200000, 1500000, 2100000),
    H265LevelHigh5_1: LevelInfo(True, 534773760, 8912896, 160000, 160000, 160000, 160000, 106720, 133280, 160000, 213280, 240000, 266720, 320000, 320000, 400000, 480000, 320000, 320000, 4800000, 533440, 640000, 640000, 800000, 960000, 1280000, 640000, 1280000, 1920000, 2400000, 3360000, 15360000, 160000, 160000, 320000, 400000, 1920000, 2400000, 3360000),
    H265LevelHigh5_2: LevelInfo(True, 1069547520, 8912896, 240000, 240000, 240000, 240000, 160080, 199920, 240000, 319920, 360000, 400080, 480000, 480000, 600000, 720000, 480000, 480000, 7200000, 800160, 960000, 960000, 1200000, 1440000, 1920000, 960000, 1920000, 2880000, 3600000, 5040000, 23040000, 240000, 240000, 480000, 600000, 2880000, 3600000, 5040000),
    H265LevelHigh6:   LevelInfo(True, 1069547520, 35651584, 240000, 240000, 240000, 240000, 160080, 199920, 240000, 319920, 360000, 400080, 480000, 480000, 600000, 720000, 480000, 480000, 7200000, 800160, 960000, 960000, 1200000, 1440000, 1920000, 960000, 1920000, 2880000, 3600000, 5040000, 23040000, 240000, 240000, 480000, 600000, 2880000, 3600000, 5040000),
    H265LevelHigh6_1: LevelInfo(True, 2139095040, 35651584, 480000, 480000, 480000, 480000, 320160, 399840, 480000, 639840, 720000, 800160, 960000, 960000, 1200000, 1440000, 960000, 960000, 14400000, 1600320, 1920000, 1920000, 2400000, 2880000, 3840000, 1920000, 3840000, 5760000, 7200000, 10080000, 46080000, 480000, 480000, 960000, 1200000, 5760000, 7200000, 10080000),
    H265LevelHigh6_2: LevelInfo(True, 4278190080, 35651584, 800000, 800000, 800000, 800000, 533600, 666400, 800000, 1066400, 1200000, 1333600, 1600000, 1600000, 2000000, 2400000, 1600000, 1600000, 24000000, 2667200, 3200000, 3200000, 4000000, 4800000, 6400000, 3200000, 6400000, 9600000, 12000000, 16800000, 76800000, 800000, 800000, 1600000, 2000000, 9600000, 12000000, 16800000),
    H265LevelHigh8_5: LevelInfo(True, 4278190080, 35651584, 800000, 800000, 800000, 800000, 533600, 666400, 800000, 1066400, 1200000, 1333600, 1600000, 1600000, 2000000, 2400000, 1600000, 1600000, 24000000, 2667200, 3200000, 3200000, 4000000, 4800000, 6400000, 3200000, 6400000, 9600000, 12000000, 16800000, 76800000, 800000, 800000, 1600000, 2000000, 9600000, 12000000, 16800000),
    # fmt: on
}


# ---------------------------------------------------------------------------
# Profile specification table
# ---------------------------------------------------------------------------

ALL_PROFILES: dict[EnumId, ProfileInfo] = {
    CodecProfileMain:                               ProfileInfo(["4:2:0"], 8),
    H265ProfileMain10:                              ProfileInfo(["4:2:0"], 10),
    H265ProfileMain10StillPicture:                  ProfileInfo(["4:2:0"], 10),
    H265ProfileMainStillPicture:                    ProfileInfo(["4:2:0"], 8),
    H265ProfileMonochrome:                          ProfileInfo(["monochrome"], 8),
    H265ProfileMonochrome10:                        ProfileInfo(["monochrome"], 10),
    H265ProfileMonochrome12:                        ProfileInfo(["monochrome"], 12),
    H265ProfileMonochrome16:                        ProfileInfo(["monochrome"], 16),
    H265ProfileMain12:                              ProfileInfo(["monochrome", "4:2:0"], 12),
    H265ProfileMain10_422:                          ProfileInfo(["monochrome", "4:2:0", "4:2:2"], 10),
    H265ProfileMain12_422:                          ProfileInfo(["monochrome", "4:2:0", "4:2:2"], 12),
    H265ProfileMain_444:                            ProfileInfo(["monochrome", "4:2:0", "4:2:2"], 8),
    H265ProfileMain10_444:                          ProfileInfo(["monochrome", "4:2:0", "4:2:2", "4:4:4"], 10),
    H265ProfileMain12_444:                          ProfileInfo(["monochrome", "4:2:0", "4:2:2", "4:4:4"], 12),
    H265ProfileMainIntra:                           ProfileInfo(["monochrome", "4:2:0"], 8),
    H265ProfileMain10Intra:                         ProfileInfo(["monochrome", "4:2:0"], 10),
    H265ProfileMain12Intra:                         ProfileInfo(["monochrome", "4:2:0"], 12),
    H265ProfileMain10Intra_422:                     ProfileInfo(["monochrome", "4:2:0", "4:2:2"], 10),
    H265ProfileMain12Intra_422:                     ProfileInfo(["monochrome", "4:2:0", "4:2:2"], 12),
    H265ProfileMainIntra_444:                       ProfileInfo(["monochrome", "4:2:0", "4:2:2", "4:4:4"], 8),
    H265ProfileMain10Intra_444:                     ProfileInfo(["monochrome", "4:2:0", "4:2:2", "4:4:4"], 10),
    H265ProfileMain12Intra_444:                     ProfileInfo(["monochrome", "4:2:0", "4:2:2", "4:4:4"], 12),
    H265ProfileMain16Intra_444:                     ProfileInfo(["monochrome", "4:2:0", "4:2:2", "4:4:4"], 16),
    H265ProfileMainStillPicture_444:                ProfileInfo(["monochrome", "4:2:0", "4:2:2", "4:4:4"], 8),
    H265ProfileMain16StillPicture_444:              ProfileInfo(["monochrome", "4:2:0", "4:2:2", "4:4:4"], 16),
    H265ProfileHighThroughput_444:                  ProfileInfo(["monochrome", "4:2:0", "4:2:2", "4:4:4"], 8),
    H265ProfileHighThroughput10_444:                ProfileInfo(["monochrome", "4:2:0", "4:2:2", "4:4:4"], 10),
    H265ProfileHighThroughput14_444:                ProfileInfo(["monochrome", "4:2:0", "4:2:2", "4:4:4"], 14),
    H265ProfileHighThroughput16Intra_444:           ProfileInfo(["monochrome", "4:2:0", "4:2:2", "4:4:4"], 16),
    H265ProfileScreenExtendedMain:                  ProfileInfo(["4:2:0"], 8),
    H265ProfileScreenExtendedMain10:                ProfileInfo(["4:2:0"], 10),
    H265ProfileScreenExtendedMain_444:              ProfileInfo(["4:2:0", "4:2:1", "4:4:4 planes"], 8),
    H265ProfileScreenExtendedMain10_444:            ProfileInfo(["4:2:0", "4:2:1", "4:4:4 planes"], 10),
    H265ProfileScreenExtendedHighThroughput_444:    ProfileInfo(["monochrome", "4:2:0", "14:2:2", "4:4:4"], 8),
    H265ProfileScreenExtendedHighThroughput10_444:  ProfileInfo(["monochrome", "4:2:0", "4:2:2", "4:4:4"], 10),
    H265ProfileScreenExtendedHighThroughput14_444:  ProfileInfo(["monochrome", "4:2:0", "4:2:2", "4:4:4"], 14),
}


# ---------------------------------------------------------------------------
# Profile → LevelInfo bitrate attribute mapping
#
# Replaces the 36-case switch statement in CheckH265ProfileLevel,
# GetH265MaxBitrate, and SelectH265LevelFromCodedFlow.
# ---------------------------------------------------------------------------

PROFILE_BITRATE_ATTR: dict[EnumId, str] = {
    CodecProfileMain:                               "max_bitrate_main",
    H265ProfileMain10:                              "max_bitrate_main10",
    H265ProfileMain10StillPicture:                  "max_bitrate_main10_still",
    H265ProfileMainStillPicture:                    "max_bitrate_main_still",
    H265ProfileMonochrome:                          "max_bitrate_monochrome",
    H265ProfileMonochrome10:                        "max_bitrate_monochrome10",
    H265ProfileMonochrome12:                        "max_bitrate_monochrome12",
    H265ProfileMonochrome16:                        "max_bitrate_monochrome16",
    H265ProfileMain12:                              "max_bitrate_main12",
    H265ProfileMain10_422:                          "max_bitrate_main10_422",
    H265ProfileMain12_422:                          "max_bitrate_main12_422",
    H265ProfileMain_444:                            "max_bitrate_main_444",
    H265ProfileMain10_444:                          "max_bitrate_main10_444",
    H265ProfileMain12_444:                          "max_bitrate_main12_444",
    H265ProfileMainIntra:                           "max_bitrate_main_intra",
    H265ProfileMain10Intra:                         "max_bitrate_main10_intra",
    H265ProfileMain12Intra:                         "max_bitrate_main12_intra",
    H265ProfileMain10Intra_422:                     "max_bitrate_main10_intra_422",
    H265ProfileMain12Intra_422:                     "max_bitrate_main12_intra_422",
    H265ProfileMainIntra_444:                       "max_bitrate_main_intra_444",
    H265ProfileMain10Intra_444:                     "max_bitrate_main10_intra_444",
    H265ProfileMain12Intra_444:                     "max_bitrate_main12_intra_444",
    H265ProfileMain16Intra_444:                     "max_bitrate_main16_intra_444",
    H265ProfileMainStillPicture_444:                "max_bitrate_main_still_444",
    H265ProfileMain16StillPicture_444:              "max_bitrate_main16_still_444",
    H265ProfileHighThroughput_444:                  "max_bitrate_high_throughput_444",
    H265ProfileHighThroughput10_444:                "max_bitrate_high_throughput10_444",
    H265ProfileHighThroughput14_444:                "max_bitrate_high_throughput14_444",
    H265ProfileHighThroughput16Intra_444:           "max_bitrate_high_throughput16_intra_444",
    H265ProfileScreenExtendedMain:                  "max_bitrate_screen_extended_main",
    H265ProfileScreenExtendedMain10:                "max_bitrate_screen_extended_main10",
    H265ProfileScreenExtendedMain_444:              "max_bitrate_screen_extended_main_444",
    H265ProfileScreenExtendedMain10_444:            "max_bitrate_screen_extended_main10_444",
    H265ProfileScreenExtendedHighThroughput_444:    "max_bitrate_screen_extended_high_throughput_444",
    H265ProfileScreenExtendedHighThroughput10_444:  "max_bitrate_screen_extended_high_throughput10_444",
    H265ProfileScreenExtendedHighThroughput14_444:  "max_bitrate_screen_extended_high_throughput14_444",
}
