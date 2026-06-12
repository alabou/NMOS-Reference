# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""JPEG XS (JXSV) codec profile and level specification tables.

JXSV differs from H.264/H.265 in that maximum bitrate is determined by
the sublevel (bits-per-pixel budget: 2 bpp, 3 bpp, … 12 bpp) rather
than by the profile.  The SUBLEVEL_BITRATE_ATTR dict maps sublevel enums
to the corresponding LevelInfo attribute name.
"""

from __future__ import annotations

from dataclasses import dataclass

from nmos.enums import (
    EnumId,
    EnumRegistry,
    # JXSV profiles
    JxsvProfileMain420_12,
    JxsvProfileHigh420_12,
    JxsvProfileMain444_12,
    JxsvProfileMain4444_12,
    JxsvProfileHigh444_12,
    JxsvProfileHigh4444_12,
    JxsvProfileTDC444_12,
    # JXSV levels
    JxsvLevel1k1,
    JxsvLevel2k1,
    JxsvLevel4k1,
    JxsvLevel4k2,
    JxsvLevel4k3,
    JxsvLevel8k1,
    JxsvLevel8k2,
    JxsvLevel8k3,
    # JXSV sublevels
    JxsvSublevel2bpp,
    JxsvSublevel3bpp,
    JxsvSublevel4bpp,
    JxsvSublevel6bpp,
    JxsvSublevel9bpp,
    JxsvSublevel12bpp,
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LevelInfo:
    """JXSV level constraints.

    max_rate and max_size are in pixel units.
    All max_bitrate_sublevel_* fields are in Kbps.  Each field corresponds
    to the maximum bitrate allowed at a specific bits-per-pixel sublevel.
    """

    max_rate: int                       # pixels / second
    max_size: int                       # pixels
    max_bitrate_sublevel_2bpp: int      # 2 bits per pixel
    max_bitrate_sublevel_3bpp: int      # 3 bits per pixel
    max_bitrate_sublevel_4bpp: int      # 4 bits per pixel
    max_bitrate_sublevel_6bpp: int      # 6 bits per pixel
    max_bitrate_sublevel_9bpp: int      # 9 bits per pixel
    max_bitrate_sublevel_12bpp: int     # 12 bits per pixel


@dataclass(frozen=True)
class ProfileInfo:
    """JXSV profile constraints.

    color_sampling lists the chroma sub-sampling modes the profile supports.
    max_bit_depth is the maximum luma/chroma bit depth the profile allows.
    """

    color_sampling: list[str]
    max_bit_depth: int


# ---------------------------------------------------------------------------
# Ordered level list — lowest to highest resolution
# ---------------------------------------------------------------------------

ORDERED_LEVELS: list[EnumId] = [
    JxsvLevel1k1,
    JxsvLevel2k1,
    JxsvLevel4k1,
    JxsvLevel4k2,
    JxsvLevel4k3,
    JxsvLevel8k1,
    JxsvLevel8k2,
    JxsvLevel8k3,
]


# ---------------------------------------------------------------------------
# Level specification table
# Fields per entry:
#   max_rate, max_size, 2bpp, 3bpp, 4bpp, 6bpp, 9bpp, 12bpp
# ---------------------------------------------------------------------------

ALL_LEVELS: dict[EnumId, LevelInfo] = {
    JxsvLevel1k1: LevelInfo(83558400, 2621440, 167117, 250675, 334234, 501350, 752026, 1002701),
    JxsvLevel2k1: LevelInfo(133693440, 4194304, 267387, 401080, 534774, 802161, 1203241, 1604321),
    JxsvLevel4k1: LevelInfo(267386880, 8912896, 534774, 802161, 1069548, 1604321, 2406482, 3208643),
    JxsvLevel4k2: LevelInfo(534773760, 16777216, 1069548, 1604321, 2139095, 3208643, 4812964, 6417285),
    JxsvLevel4k3: LevelInfo(1069547520, 16777216, 2139095, 3208643, 4278190, 6417285, 9625928, 12834570),
    JxsvLevel8k1: LevelInfo(1069547520, 35651584, 2139095, 3208643, 4278190, 6417285, 9625928, 12834570),
    JxsvLevel8k2: LevelInfo(2139095040, 67108864, 4278190, 6417285, 8556380, 12834570, 19251855, 25669140),
    JxsvLevel8k3: LevelInfo(4278190080, 67108864, 8556380, 12834570, 17112760, 25669140, 38503711, 51338281),
}


# ---------------------------------------------------------------------------
# Profile specification table
# ---------------------------------------------------------------------------

ALL_PROFILES: dict[EnumId, ProfileInfo] = {
    JxsvProfileMain420_12:  ProfileInfo(["4:2:0"], 12),
    JxsvProfileHigh420_12:  ProfileInfo(["4:2:0"], 12),
    JxsvProfileMain444_12:  ProfileInfo(["4:0:0", "4:2:2", "4:4:4"], 12),
    JxsvProfileMain4444_12: ProfileInfo(["4:0:0", "4:2:2", "4:4:4", "4:2:2:4", "4:4:4:4"], 12),
    JxsvProfileHigh444_12:  ProfileInfo(["4:0:0", "4:2:2", "4:4:4"], 12),
    JxsvProfileHigh4444_12: ProfileInfo(["4:0:0", "4:2:2", "4:4:4", "4:2:2:4", "4:4:4:4"], 12),
    JxsvProfileTDC444_12:   ProfileInfo(["4:0:0", "4:2:2", "4:4:4"], 12),
}


# ---------------------------------------------------------------------------
# Sublevel → LevelInfo bitrate attribute mapping
#
# JXSV bitrate constraints are keyed by sublevel (bpp budget), not by
# profile.  Used by CheckJxsvProfileLevel, GetJxsvMaxBitrate, and
# SelectJxsvLevelFromCodedFlow to look up the correct bitrate field
# without a 6-case dispatch.
# ---------------------------------------------------------------------------

SUBLEVEL_BITRATE_ATTR: dict[EnumId, str] = {
    JxsvSublevel2bpp:  "max_bitrate_sublevel_2bpp",
    JxsvSublevel3bpp:  "max_bitrate_sublevel_3bpp",
    JxsvSublevel4bpp:  "max_bitrate_sublevel_4bpp",
    JxsvSublevel6bpp:  "max_bitrate_sublevel_6bpp",
    JxsvSublevel9bpp:  "max_bitrate_sublevel_9bpp",
    JxsvSublevel12bpp: "max_bitrate_sublevel_12bpp",
}
