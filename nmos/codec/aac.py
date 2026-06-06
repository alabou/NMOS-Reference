# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""AAC audio codec profile and level specification tables.

AAC differs structurally from the video codecs:
- Levels are nested inside each profile (ProfileInfo.levels), because
  different profiles support different subsets of levels.
- There is no separate ALL_LEVELS map at the module level.
- Bitrate limits are per-channel (MaxBitratePerChannel × channel_count),
  with a special rule for 5.1 surround: 6 channels count as 5 for
  bitrate calculation, but 6 for MaxChannels.
- Profiles list supported AAC object type IDs (MPEG-4 Audio Object Types).

Three ordered level lists serve different selection contexts:
- ORDERED_LEVELS: full set (levels 1-8)
- ORDERED_MULTI_LEVELS: subset for multi-channel selection (levels 2, 4)
- ORDERED_STEREO_LEVELS: subset for stereo selection (level 1)
"""

from __future__ import annotations

from dataclasses import dataclass

from nmos.enums import (
    EnumId,
    EnumRegistry,
    # Shared codec levels (AAC reuses the generic codec level enums)
    CodecLevel1,
    CodecLevel2,
    CodecLevel3,
    CodecLevel4,
    CodecLevel5,
    CodecLevel6,
    CodecLevel7,
    CodecLevel8,
    # Shared profile
    CodecProfileMain,
    # AAC profiles
    AacProfileSpeech,
    AacProfileSynthetic,
    AacProfileScalable,
    AacProfileHighQuality,
    AacProfileLowDelay,
    AacProfileNatural,
    AacProfileMobile,
    AacProfileAAC,
    AacProfileHighEfficiencyAAC,
    AacProfileHighEfficiencyAACv2,
    AacProfileLowDelayAAC,
    AacProfileLowDelayAACv2,
    AacProfileExtendedHighEfficiencyAAC,
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LevelInfo:
    """AAC level constraints within a specific profile.

    max_rate is the maximum sampling rate in Hz.  A value of 0 means the
    level is not supported by the containing profile.
    max_channels is the maximum number of audio channels.  A value of 0
    means the level is not supported.
    """

    max_rate: int       # Hz (0 = not supported)
    max_channels: int   # (0 = not supported)


@dataclass(frozen=True)
class ProfileInfo:
    """AAC profile constraints.

    objects lists the MPEG-4 Audio Object Type IDs supported by this profile.
    max_bitrate_per_channel is in Kbps per channel.  For 5.1 surround
    (6 physical channels), the bitrate calculation uses 5 channels.
    levels maps codec level enums to per-level constraints.
    """

    objects: list[int]
    max_bitrate_per_channel: int
    levels: dict[EnumId, LevelInfo]


# ---------------------------------------------------------------------------
# Ordered level lists for automatic level selection
# ---------------------------------------------------------------------------

ORDERED_LEVELS: list[EnumId] = [
    CodecLevel1,
    CodecLevel2,
    CodecLevel3,
    CodecLevel4,
    CodecLevel5,
    CodecLevel6,
    CodecLevel7,
    CodecLevel8,
]

# Reduced subset for multi-channel (5.1) capability matching
ORDERED_MULTI_LEVELS: list[EnumId] = [
    CodecLevel2,
    CodecLevel4,
]

# Reduced subset for stereo capability matching
ORDERED_STEREO_LEVELS: list[EnumId] = [
    CodecLevel1,
]


# ---------------------------------------------------------------------------
# Profile specification table (levels nested within each profile)
# ---------------------------------------------------------------------------

ALL_PROFILES: dict[EnumId, ProfileInfo] = {
    AacProfileSpeech: ProfileInfo(
        objects=[0, 8, 9, 12],
        max_bitrate_per_channel=288,
        levels={
            CodecLevel1: LevelInfo(48000, 2),
            CodecLevel2: LevelInfo(48000, 2),
            CodecLevel3: LevelInfo(0, 0),
            CodecLevel4: LevelInfo(0, 0),
            CodecLevel5: LevelInfo(0, 0),
            CodecLevel6: LevelInfo(0, 0),
            CodecLevel7: LevelInfo(0, 0),
            CodecLevel8: LevelInfo(0, 0),
        },
    ),
    AacProfileSynthetic: ProfileInfo(
        objects=[0, 12, 13, 14, 15, 16],
        max_bitrate_per_channel=288,
        levels={
            CodecLevel1: LevelInfo(48000, 2),
            CodecLevel2: LevelInfo(48000, 2),
            CodecLevel3: LevelInfo(48000, 2),
            CodecLevel4: LevelInfo(0, 0),
            CodecLevel5: LevelInfo(0, 0),
            CodecLevel6: LevelInfo(0, 0),
            CodecLevel7: LevelInfo(0, 0),
            CodecLevel8: LevelInfo(0, 0),
        },
    ),
    AacProfileScalable: ProfileInfo(
        objects=[0, 2, 4, 6, 7, 8, 9, 12],
        max_bitrate_per_channel=288,
        levels={
            CodecLevel1: LevelInfo(24000, 1),
            CodecLevel2: LevelInfo(24000, 2),
            CodecLevel3: LevelInfo(48000, 2),
            CodecLevel4: LevelInfo(48000, 6),
            CodecLevel5: LevelInfo(0, 0),
            CodecLevel6: LevelInfo(0, 0),
            CodecLevel7: LevelInfo(0, 0),
            CodecLevel8: LevelInfo(0, 0),
        },
    ),
    CodecProfileMain: ProfileInfo(
        objects=[0, 1, 2, 3, 4, 6, 7, 8, 9, 12, 13, 14, 15, 16],
        max_bitrate_per_channel=288,
        levels={
            CodecLevel1: LevelInfo(48000, 2),
            CodecLevel2: LevelInfo(48000, 2),
            CodecLevel3: LevelInfo(48000, 2),
            CodecLevel4: LevelInfo(48000, 2),
            CodecLevel5: LevelInfo(0, 0),
            CodecLevel6: LevelInfo(0, 0),
            CodecLevel7: LevelInfo(0, 0),
            CodecLevel8: LevelInfo(0, 0),
        },
    ),
    AacProfileHighQuality: ProfileInfo(
        objects=[0, 2, 4, 6, 8, 17, 19, 20, 24],
        max_bitrate_per_channel=288,
        levels={
            CodecLevel1: LevelInfo(22050, 2),
            CodecLevel2: LevelInfo(48000, 2),
            CodecLevel3: LevelInfo(48000, 6),
            CodecLevel4: LevelInfo(48000, 6),
            CodecLevel5: LevelInfo(0, 0),
            CodecLevel6: LevelInfo(0, 0),
            CodecLevel7: LevelInfo(0, 0),
            CodecLevel8: LevelInfo(0, 0),
        },
    ),
    AacProfileLowDelay: ProfileInfo(
        objects=[0, 8, 9, 12, 23, 24, 25],
        max_bitrate_per_channel=288,
        levels={
            CodecLevel1: LevelInfo(8000, 1),
            CodecLevel2: LevelInfo(16000, 1),
            CodecLevel3: LevelInfo(48000, 1),
            CodecLevel4: LevelInfo(48000, 2),
            CodecLevel5: LevelInfo(0, 0),
            CodecLevel6: LevelInfo(0, 0),
            CodecLevel7: LevelInfo(0, 0),
            CodecLevel8: LevelInfo(0, 0),
        },
    ),
    AacProfileNatural: ProfileInfo(
        objects=[0, 1, 2, 3, 4, 6, 7, 8, 9, 12, 17, 19, 20, 21, 22, 23, 24, 25, 26, 27],
        max_bitrate_per_channel=288,
        levels={
            CodecLevel1: LevelInfo(48000, 2),
            CodecLevel2: LevelInfo(96000, 2),
            CodecLevel3: LevelInfo(0, 0),
            CodecLevel4: LevelInfo(0, 0),
            CodecLevel5: LevelInfo(0, 0),
            CodecLevel6: LevelInfo(0, 0),
            CodecLevel7: LevelInfo(0, 0),
            CodecLevel8: LevelInfo(0, 0),
        },
    ),
    AacProfileMobile: ProfileInfo(
        objects=[0, 17, 20, 21, 22, 23],
        max_bitrate_per_channel=288,
        levels={
            CodecLevel1: LevelInfo(24000, 1),
            CodecLevel2: LevelInfo(48000, 2),
            CodecLevel3: LevelInfo(48000, 6),
            CodecLevel4: LevelInfo(0, 0),
            CodecLevel5: LevelInfo(0, 0),
            CodecLevel6: LevelInfo(0, 0),
            CodecLevel7: LevelInfo(0, 0),
            CodecLevel8: LevelInfo(0, 0),
        },
    ),
    AacProfileAAC: ProfileInfo(
        objects=[0, 2],
        max_bitrate_per_channel=288,
        levels={
            CodecLevel1: LevelInfo(24000, 2),
            CodecLevel2: LevelInfo(48000, 2),
            CodecLevel3: LevelInfo(0, 0),
            CodecLevel4: LevelInfo(48000, 6),
            CodecLevel5: LevelInfo(96000, 6),
            CodecLevel6: LevelInfo(0, 0),
            CodecLevel7: LevelInfo(0, 0),
            CodecLevel8: LevelInfo(0, 0),
        },
    ),
    AacProfileHighEfficiencyAAC: ProfileInfo(
        objects=[0, 2, 5],
        max_bitrate_per_channel=288,
        levels={
            CodecLevel1: LevelInfo(0, 0),
            CodecLevel2: LevelInfo(48000, 2),
            CodecLevel3: LevelInfo(48000, 2),
            CodecLevel4: LevelInfo(48000, 6),
            CodecLevel5: LevelInfo(96000, 6),
            CodecLevel6: LevelInfo(0, 0),
            CodecLevel7: LevelInfo(0, 0),
            CodecLevel8: LevelInfo(0, 0),
        },
    ),
    AacProfileHighEfficiencyAACv2: ProfileInfo(
        objects=[0, 2, 5, 29],
        max_bitrate_per_channel=288,
        levels={
            CodecLevel1: LevelInfo(0, 0),
            CodecLevel2: LevelInfo(48000, 2),
            CodecLevel3: LevelInfo(48000, 2),
            CodecLevel4: LevelInfo(48000, 6),
            CodecLevel5: LevelInfo(96000, 6),
            CodecLevel6: LevelInfo(0, 0),
            CodecLevel7: LevelInfo(0, 0),
            CodecLevel8: LevelInfo(0, 0),
        },
    ),
    AacProfileLowDelayAAC: ProfileInfo(
        objects=[0, 23],
        max_bitrate_per_channel=288,
        levels={
            CodecLevel1: LevelInfo(48000, 2),
            CodecLevel2: LevelInfo(0, 0),
            CodecLevel3: LevelInfo(0, 0),
            CodecLevel4: LevelInfo(0, 0),
            CodecLevel5: LevelInfo(0, 0),
            CodecLevel6: LevelInfo(0, 0),
            CodecLevel7: LevelInfo(0, 0),
            CodecLevel8: LevelInfo(0, 0),
        },
    ),
    AacProfileLowDelayAACv2: ProfileInfo(
        objects=[0, 39],
        max_bitrate_per_channel=288,
        levels={
            CodecLevel1: LevelInfo(48000, 2),
            CodecLevel2: LevelInfo(0, 0),
            CodecLevel3: LevelInfo(0, 0),
            CodecLevel4: LevelInfo(0, 0),
            CodecLevel5: LevelInfo(0, 0),
            CodecLevel6: LevelInfo(0, 0),
            CodecLevel7: LevelInfo(0, 0),
            CodecLevel8: LevelInfo(0, 0),
        },
    ),
    AacProfileExtendedHighEfficiencyAAC: ProfileInfo(
        objects=[],
        max_bitrate_per_channel=288,
        levels={
            CodecLevel1: LevelInfo(0, 0),
            CodecLevel2: LevelInfo(0, 0),
            CodecLevel3: LevelInfo(0, 0),
            CodecLevel4: LevelInfo(0, 0),
            CodecLevel5: LevelInfo(0, 0),
            CodecLevel6: LevelInfo(0, 0),
            CodecLevel7: LevelInfo(0, 0),
            CodecLevel8: LevelInfo(0, 0),
        },
    ),
}
