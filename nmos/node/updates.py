# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Typed update dataclasses for Node resource mutations.

Each resource type has a corresponding Update dataclass where all fields
default to UNSET. Only fields explicitly set are applied during update.
This provides a type-safe kwargs pattern for resource mutations.

Usage:
    node.update_sender(sender_id, SenderUpdate(flow_id="abc-123"))
    node.update_source(source_id, SourceUpdate(grain_rate=rate, clock_name="clk0"))
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum, auto
from typing import Any, Iterator


# ---------------------------------------------------------------------------
# UNSET sentinel — distinguishes "not provided" from None/False/0/""
# ---------------------------------------------------------------------------

class _UnsetType:
    """Sentinel for unset update fields. Singleton."""

    _instance: _UnsetType | None = None

    def __new__(cls) -> _UnsetType:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET = _UnsetType()
"""Sentinel value for unset update fields."""


def iter_set_fields(update: Any) -> Iterator[tuple[str, Any]]:
    """Iterate over (field_name, value) for all fields that are not UNSET.

    Used by Node update methods to apply only the provided fields.
    """
    for f in fields(update):
        val = getattr(update, f.name)
        if val is not UNSET:
            yield f.name, val


# ---------------------------------------------------------------------------
# Codec type enums
# ---------------------------------------------------------------------------

class VideoCodec(Enum):
    """Video codec types for flow update."""
    JXSV = auto()
    H264 = auto()
    H265 = auto()


class AudioCodec(Enum):
    """Audio codec types for flow update."""
    AM824 = auto()
    AAC = auto()
    AAC_LATM = auto()
    AAC_ADTS = auto()


# ---------------------------------------------------------------------------
# Video / Audio / Codec parameter groups for flow updates
# ---------------------------------------------------------------------------

@dataclass
class VideoParams:
    """Parameters for WithFlowVideo."""
    frame_width: int = 0
    frame_height: int = 0
    colorspace: Any = UNSET       # EnumId
    transfer_characteristic: Any = UNSET  # EnumId
    interlace_mode: Any = UNSET   # EnumId
    components: Any = UNSET       # list[NVideoComponentValue]


@dataclass
class VideoCodecParams:
    """Parameters for WithFlowVideoJxsvCodec / H264 / H265."""
    codec: VideoCodec = VideoCodec.JXSV
    profile: Any = UNSET          # EnumId
    level: Any = UNSET            # EnumId
    sublevel: Any = UNSET         # EnumId (jxsv only)
    fbblevel: Any = UNSET         # EnumId (jxsv only)
    bitrate: int = 0
    cbr: bool = False


@dataclass
class AudioParams:
    """Parameters for WithFlowAudio."""
    bit_depth: int = 0


@dataclass
class AudioCodecParams:
    """Parameters for WithFlowAudioAm824/Aac/AacLatm/AacAdts."""
    codec: AudioCodec = AudioCodec.AM824
    profile: Any = UNSET          # EnumId
    level: Any = UNSET            # EnumId
    bitrate: int = 0
    cbr: bool = False


@dataclass
class LayerParams:
    """Parameters for WithFlowLayers."""
    video_layers: int = 0
    audio_layers: int = 0
    data_layers: int = 0
    media_type: Any = UNSET       # EnumId


# ---------------------------------------------------------------------------
# Monitor source update parameters
# ---------------------------------------------------------------------------

@dataclass
class MonitorSenderInfo:
    """Parameters for WithSourceMonitorSenderInfo.

    Status values are IS-12 NcStatusIndicator enum integers.
    """
    auto_reset: bool = False
    overall_status: int = 0
    overall_status_message: str = ""
    link_status: int = 0
    transmission_status: int = 0
    synchronization_status: int = 0
    essence_status: int = 0
    link_counter: int = 0
    transmission_counter: int = 0
    synchronization_counter: int = 0
    essence_counter: int = 0


@dataclass
class MonitorReceiverInfo:
    """Parameters for WithSourceMonitorReceiverInfo.

    Status values are IS-12 NcStatusIndicator enum integers.
    """
    auto_reset: bool = False
    overall_status: int = 0
    overall_status_message: str = ""
    link_status: int = 0
    connection_status: int = 0
    synchronization_status: int = 0
    stream_status: int = 0
    link_counter: int = 0
    connection_counter: int = 0
    synchronization_counter: int = 0
    stream_counter: int = 0


# ---------------------------------------------------------------------------
# Resource update dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SenderUpdate:
    """Typed update fields for Node.update_sender().

    Only fields set to non-UNSET values are applied. UNSET fields are skipped.
    """
    flow_id: Any = UNSET                    # str — link to flow
    subscription_active: Any = UNSET        # bool
    subscription_receiver_id: Any = UNSET   # str


@dataclass
class ReceiverUpdate:
    """Typed update fields for Node.update_receiver()."""
    subscription_active: Any = UNSET        # bool
    subscription_sender_id: Any = UNSET     # str


@dataclass
class SourceUpdate:
    """Typed update fields for Node.update_source()."""
    receiver_id: Any = UNSET                # str — link to receiver
    grain_rate: Any = UNSET                 # NRationalValue
    channels: Any = UNSET                   # list[NAudioChannelValue]
    synchronous_media: Any = UNSET          # bool
    clock_name: Any = UNSET                 # str
    # Monitor-specific updates
    monitor_sender_info: Any = UNSET        # MonitorSenderInfo
    monitor_receiver_info: Any = UNSET      # MonitorReceiverInfo


@dataclass
class FlowUpdate:
    """Typed update fields for Node.update_flow()."""
    grain_rate: Any = UNSET                 # NRationalValue
    video: Any = UNSET                      # VideoParams
    video_codec: Any = UNSET                # VideoCodecParams
    audio: Any = UNSET                      # AudioParams
    audio_codec: Any = UNSET                # AudioCodecParams
    layers: Any = UNSET                     # LayerParams
    raw_flavor: Any = UNSET                 # bool — set to True to apply WithFlowRawFlavor
    coded_flavor: Any = UNSET               # bool — set to True to apply WithFlowCodedFlavor
