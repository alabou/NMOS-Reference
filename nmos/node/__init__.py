# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""NMOS Node — central resource manager.

Manages 4 resource types (receivers, sources, flows, senders) with atomic
CRUD operations, bidirectional linking, UUID cascade on update, transport
activation init, natural groups, privacy/ECDH, copy-on-write publish, and
OAuth2.

Resource creation ordering (IS-04 requirement):
    Receiver (#1) → Source (#2) → Flow (#3) → Sender (#4)

Static vs Dynamic UUIDs:
    - Static ID (uniqueId=0): map key, never changes, identifies the resource slot
    - Dynamic ID (random uniqueId): published identity, changes on every update

Error safety:
    If an error occurs during resource processing, the original resource state
    is not modified. Add operations use deferred index cleanup. Update operations
    on sources/flows generate new UUIDs and cascade to linked resources.
"""

from __future__ import annotations

import asyncio
import os
import struct
import time
from collections import deque
from typing import Any

from nmos.errors import (
    InvalidObject,
    InvalidOperation,
    InvalidParameter,
    NotAllowed,
    NotFound,
    UnexpectedError,
)
from nmos.node.publish import PublishManager, PublishState
from nmos.node.store import ResourceStore, to_static_id
from nmos.node.types import (
    MAX_INTERFACES,
    MAX_LEGS,
    Activation,
    GarbageResource,
    Interface,
    Leg,
    NaturalGroups,
    PoolOfIndices,
    PreSharedKey,
    Privacy,
    PrivacyPreSharedKeys,
    Tracker,
)
from nmos.node.updates import (
    UNSET,
    FlowUpdate,
    ReceiverUpdate,
    SenderUpdate,
    SourceUpdate,
    iter_set_fields,
)
from nmos.enums import (
    # Formats
    FormatAudio, FormatData,
    # Media types
    VideoCodedH264, VideoCodedH265, VideoCodedJxsv,
    AudioRawL8, AudioRawL16, AudioRawL20, AudioRawL24, AudioCodedAm824,
    AudioCodedAac, AudioCodedAacLATM, AudioCodedAacADTS,
    MuxAm824, MuxMpeg2TS, MuxGeneric,
    # Transport
    TransportUsb, TransportRtsp, TransportRtspTcp, TransportSrt, TransportSrtMpeg2Ts,
    TransportUdp, TransportUdpUnicast, TransportUdpMulticast,
    TransportUdpMpeg2Ts, TransportUdpMpeg2TsUnicast, TransportUdpMpeg2TsMulticast,
    # Tags
    TagGroupHint, TagAssetManufacturer, TagAssetProduct, TagAssetInstance, TagAssetFunction,
    # Colorspace / transfer / interlace
    BT601, BT709, BT2020, BT2100, SDR, HLG, PQ,
    InterlacedTff, InterlacedBff, InterlacedPsf,
    # Audio channels / video components
    L, R, C, LFE, Ls, Rs, Lrs, Rrs, Lt, Rt, M1, M2, B, G,
    # H.264 profiles / shared codec levels
    CodecProfileMain, H264ProfileBaselineConstrained, H264ProfileBaseline, H264ProfileExtended, H264ProfileHigh,
    H264ProfileHighProgressive, H264ProfileHighConstrained, H264ProfileHigh10,
    H264ProfileHigh10Progressive, H264ProfileHigh_422, H264ProfileHighPredictive_444,
    H264ProfileHigh10Intra, H264ProfileHighIntra_422, H264ProfileHighIntra_444,
    H264ProfileCAVLCIntra_444,
    CodecLevel1, CodecLevel1b, CodecLevel1_1, CodecLevel1_2, CodecLevel1_3,
    CodecLevel2, CodecLevel2_1, CodecLevel2_2, CodecLevel3, CodecLevel3_1, CodecLevel3_2,
    CodecLevel4, CodecLevel4_1, CodecLevel4_2, CodecLevel5, CodecLevel5_1, CodecLevel5_2,
    CodecLevel6, CodecLevel6_1, CodecLevel6_2,
    # Packet transmission / parameter-set modes
    CodeStream, NonInterleavedNalUnits, NonInterleavedAccessUnits,
    InAndOutOfBand, OutOfBand, Strict,
    # Compatibility status
    Unconstrained, Constrained, ActiveConstraintsViolation, Unknown,
    CompliantStream, NonCompliantStream,
    # Protocols / clock / transport params / sender type
    Http, Https, Ptp, Internal, IEEE1588_2008, SourceIp, SenderType2110TPW,
)
from nmos.uuid import (
    ResourceSubType,
    ResourceType,
    ResourceUuid,
    update_resource_unique_id,
)


# Persistent sender/receiver/source IDs — HasPersistentSenderReceiverId = true.
# When True, uniqueId is 0, making UUIDs deterministic (serial + index only).
# This ensures sender/receiver IDs don't change across node restarts.
HAS_PERSISTENT_SENDER_RECEIVER_ID: bool = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _random_unique_id() -> int:
    """Generate a random 32-bit unique ID for resource UUIDs."""
    result: int = struct.unpack(">I", os.urandom(4))[0]
    return result


def _get_sdp_channel_order(channels: list[Any], is_am824: bool) -> str:
    """Derive SMPTE 2110 channel order from source audio channels.

    Reads channel Symbol fields to determine the standard grouping name.
    """
    n = len(channels)
    if n == 0:
        return "SMPTE2110.(U01)"

    def _sym(ch: Any) -> str:
        """Get channel symbol string, empty if undefined."""
        if hasattr(ch, 'Symbol') and ch.Symbol.defined:
            return str(ch.Symbol.value)
        return ""

    if n == 1:
        if _sym(channels[0]) == M1.s:
            return "SMPTE2110.(M)"
        return "SMPTE2110.(U01)"

    if n == 2:
        if is_am824:
            return "SMPTE2110.(AES3)"
        s0, s1 = _sym(channels[0]), _sym(channels[1])
        if s0 == M1.s and s1 == M2.s:
            return "SMPTE2110.(DM)"
        if s0 == L.s and s1 == R.s:
            return "SMPTE2110.(ST)"
        if s0 == Lt.s and s1 == Rt.s:
            return "SMPTE2110.(LtRt)"
        return "SMPTE2110.(U02)"

    if n == 4 and is_am824:
        s2, s3 = _sym(channels[2]), _sym(channels[3])
        if s2 == L.s and s3 == R.s:
            return "SMPTE2110.(AES3,ST)"
        if s2 == Lt.s and s3 == Rt.s:
            return "SMPTE2110.(AES3,LtRt)"
        return "SMPTE2110.(AES3,U02)"

    if n == 6:
        if is_am824:
            return "SMPTE2110.(AES3,AES3,AES3)"
        syms = [_sym(c) for c in channels]
        if syms == [L.s, R.s, C.s, LFE.s, Ls.s, Rs.s]:
            return "SMPTE2110.(51)"
        return f"SMPTE2110.(U{n:02d})"

    if n == 8:
        if is_am824:
            syms = [_sym(c) for c in channels[2:8]]
            if syms == [L.s, R.s, C.s, LFE.s, Ls.s, Rs.s]:
                return "SMPTE2110.(AES3,51)"
            return "SMPTE2110.(AES3,U06)"
        syms = [_sym(c) for c in channels]
        if syms == [L.s, R.s, C.s, LFE.s, Ls.s, Rs.s, Lrs.s, Rrs.s]:
            return "SMPTE2110.(71)"
        return f"SMPTE2110.(U{n:02d})"

    if n == 10 and is_am824:
        syms = [_sym(c) for c in channels[2:10]]
        if syms == [L.s, R.s, C.s, LFE.s, Ls.s, Rs.s, Lrs.s, Rrs.s]:
            return "SMPTE2110.(AES3,71)"
        return "SMPTE2110.(AES3,U08)"

    if n == 12 and is_am824:
        return "SMPTE2110.(AES3,AES3,AES3,AES3,AES3,AES3)"

    if n == 14 and is_am824:
        return "SMPTE2110.(AES3,AES3,AES3,AES3,AES3,AES3,AES3)"

    if n == 16 and is_am824:
        return "SMPTE2110.(AES3,AES3,AES3,AES3,AES3,AES3,AES3,AES3)"

    if is_am824 and n % 2 == 0:
        pairs = ",".join(["AES3"] * (n // 2))
        return f"SMPTE2110.({pairs})"

    return f"SMPTE2110.(U{n:02d})"


def _get_aes3_composite_channel_order(node: Any, mux_flow: Any) -> tuple[str, int]:
    """Composite channel-order and channel count for a fully-described AM824 mux.

    Iterates the mux flow's parent audio flows and builds a combined
    ``SMPTE2110.(group1,group2,...)`` string.

    Rules per sub-flow:
      - PCM (L8/L16/L20/L24): uses actual channel count → 2ch="ST", 6ch="51", 8ch="71"
      - Coded audio (AAC etc.): counts as 2 channels (one AES3 pair) → "AES3"
      - audio/AM824: PROHIBITED as sub-flow (raises ValueError)
    """
    _PCM_TYPES = {AudioRawL8.s, AudioRawL16.s, AudioRawL20.s, AudioRawL24.s}

    flow_core = _get_flow_core(mux_flow)
    parent_ids = flow_core.Parents.value if flow_core.Parents.defined else []

    groups: list[str] = []
    total_channels = 0

    for parent_id in parent_ids:
        parent_flow = node.flows.get(str(parent_id))
        if parent_flow is None:
            continue
        parent_inner = parent_flow.get() if hasattr(parent_flow, 'get') else parent_flow
        if parent_inner is None:
            continue

        # Only consider audio sub-flows
        fmt_str = str(parent_inner.Format.value) if hasattr(parent_inner, 'Format') and parent_inner.Format.defined else ""
        if fmt_str != FormatAudio.s:
            continue

        parent_fc = _get_flow_core(parent_flow)

        # Get media type
        mt = ""
        if hasattr(parent_inner, 'MediaType') and parent_inner.MediaType.defined:
            mt = str(parent_inner.MediaType.value)

        # AM824 cannot be a sub-flow — raise here
        if mt == AudioCodedAm824.s:
            raise ValueError(
                "audio/AM824 cannot be a sub-flow of a mux flow — "
                "AES3 spec: audio sub-flows MUST NOT use the audio/AM824 media type"
            )

        is_pcm = mt in _PCM_TYPES

        # Get channel count from source
        ch_count = 2  # default: one AES3 pair
        try:
            source = node.sources.get(str(parent_fc.SourceId.value))
            if source is not None:
                src_inner = source.get() if hasattr(source, 'get') else source
                if src_inner and hasattr(src_inner, 'Channels') and src_inner.Channels.defined:
                    ch_count = len(src_inner.Channels.value)
        except (AttributeError, InvalidObject):
            pass

        if is_pcm:
            # PCM uses actual channel count for grouping
            if ch_count <= 2:
                groups.append("ST")
                total_channels += 2
            elif ch_count <= 6:
                groups.append("51")
                total_channels += 6
            elif ch_count <= 8:
                groups.append("71")
                total_channels += 8
            else:
                groups.append(f"U{ch_count:02d}")
                total_channels += ch_count
        else:
            # Coded audio (AAC, etc.): one AES3 pair = 2 channels
            groups.append("AES3")
            total_channels += 2

    if not groups:
        return "SMPTE2110.(AES3)", 2

    return f"SMPTE2110.({','.join(groups)})", total_channels


def _select_packet_time_us(sample_rate: int, channels: int) -> int:
    """Select audio packet time in microseconds.

    Uses 1ms if it fits in a packet, else 125us.
    """
    if (3 * sample_rate * channels * 1000) // 1000000 <= 1400:
        return 1000
    else:
        return 125


def _get_h264_profile_level_id(flow_inner: Any) -> str:
    """Compute H.264 profile-level-id hex string from flow Profile and Level.

    Format: profile_idc (1 byte) + profile_iop (1 byte) + level_idc (1 byte) = 6 hex chars.
    """
    profile = str(flow_inner.Profile.value) if hasattr(flow_inner, 'Profile') and flow_inner.Profile.defined else ""
    level = str(flow_inner.Level.value) if hasattr(flow_inner, 'Level') and flow_inner.Level.defined else ""

    # Profile → profile_idc + profile_iop
    _PROFILE_MAP: dict[str, tuple[int, int, int]] = {
        # profile_str: (profile_idc, profile_iop, default_level_idc)
        H264ProfileBaselineConstrained.s: (0x42, 0x40, 11),
        H264ProfileBaseline.s: (0x42, 0x80, 11),
        CodecProfileMain.s: (0x4d, 0x00, 11),
        H264ProfileExtended.s: (0x58, 0x20, 11),
        H264ProfileHigh.s: (0x64, 0x00, 9),
        H264ProfileHighProgressive.s: (0x64, 0x08, 9),
        H264ProfileHighConstrained.s: (0x64, 0x0c, 9),
        H264ProfileHigh10.s: (0x6e, 0x00, 9),
        H264ProfileHigh10Progressive.s: (0x6e, 0x08, 9),
        H264ProfileHigh_422.s: (0x7a, 0x00, 9),
        H264ProfileHighPredictive_444.s: (0xf4, 0x00, 9),
        H264ProfileHigh10Intra.s: (0x6e, 0x10, 9),
        H264ProfileHighIntra_422.s: (0x7a, 0x10, 9),
        H264ProfileHighIntra_444.s: (0xf4, 0x10, 9),
        H264ProfileCAVLCIntra_444.s: (0x2c, 0x00, 9),
    }

    profile_idc, profile_iop, level_idc = _PROFILE_MAP.get(profile, (0x64, 0x00, 9))

    # Level → level_idc
    _LEVEL_MAP: dict[str, int] = {
        CodecLevel1.s: 10, CodecLevel1b.s: 9, CodecLevel1_1.s: 11, CodecLevel1_2.s: 12, CodecLevel1_3.s: 13,
        CodecLevel2.s: 20, CodecLevel2_1.s: 21, CodecLevel2_2.s: 22,
        CodecLevel3.s: 30, CodecLevel3_1.s: 31, CodecLevel3_2.s: 32,
        CodecLevel4.s: 40, CodecLevel4_1.s: 41, CodecLevel4_2.s: 42,
        CodecLevel5.s: 50, CodecLevel5_1.s: 51, CodecLevel5_2.s: 52,
        CodecLevel6.s: 60, CodecLevel6_1.s: 61, CodecLevel6_2.s: 62,
    }
    if level:
        level_idc = _LEVEL_MAP.get(level, level_idc)

    return f"{profile_idc:02x}{profile_iop:02x}{level_idc:02x}"


def _set_ipmx_timing(media: Any) -> None:
    """Set IPMX timing parameters (htotal, vtotal, measured_pix_clk) from video format.

    Standard blanking values from SMPTE for common formats.
    """
    w = media.width if media.width else 0
    h = media.height if media.height else 0
    num = media.exact_frame_rate_numerator if media.exact_frame_rate_numerator else 0
    den = media.exact_frame_rate_denominator if media.exact_frame_rate_denominator else 1
    fps = num / den if den else 0

    # Standard total sizes (active + blanking) for common formats
    # SMPTE ST 274 / ST 296 / ST 2036 / CTA-861
    _TIMING_TABLE: dict[tuple[int, int], tuple[int, int]] = {
        (720, 480): (858, 525),
        (720, 576): (864, 625),
        (1280, 720): (1650, 750),
        (1920, 1080): (2200, 1125),
        (2048, 1080): (2200, 1125),
        (3840, 2160): (4400, 2250),
        (4096, 2160): (4400, 2250),
        (7680, 4320): (8800, 4500),
    }

    htotal, vtotal = _TIMING_TABLE.get((w, h), (w + 280, h + 45))

    if media.interlaced:
        # Interlaced: double the frame rate for field rate
        pix_clk = htotal * vtotal * fps * 2
    else:
        pix_clk = htotal * vtotal * fps

    media.h_total = htotal
    media.v_total = vtotal
    media.measured_pix_clk = int(pix_clk)


_RTSP_TRANSPORT_URNS = {
    TransportRtsp.s,
    TransportRtspTcp.s,
}
_USB_TRANSPORT_URNS = {
    TransportUsb.s,
    "urn:x-matrox:transport:usb",
}
_SRT_UDP_MP2T_TRANSPORT_URNS = {
    TransportSrt.s,
    TransportSrtMpeg2Ts.s,
    TransportUdp.s,
    TransportUdpUnicast.s,
    TransportUdpMulticast.s,
    TransportUdpMpeg2Ts.s,
    TransportUdpMpeg2TsUnicast.s,
    TransportUdpMpeg2TsMulticast.s,
}


def _sdp_transport_category(transport: str) -> tuple[str, Any] | None:
    """Classify a transport URN for SDP generation.

    Returns ``(category, extra)`` where *category* is one of:
      - ``"tcp-control"`` — RTSP / USB manifest SDP (TCP control endpoint);
        *extra* is the SDP format-string enum value ("rtsp" or "usb").
      - ``"srt-udp-mp2t"`` — MPEG2-TS over SRT / UDP; media section is
        ``m=application <port> UDP mp2t`` using the sender's listener port.
      - ``"rtp"`` — any RTP-family transport; media section is
        ``m=<type> <dest_port> RTP/AVP <pt>`` populated from the flow.
    Returns ``None`` for unsupported transports.
    """
    if transport in _RTSP_TRANSPORT_URNS:
        return "tcp-control", "rtsp"
    if transport in _USB_TRANSPORT_URNS:
        return "tcp-control", "usb"
    if transport in _SRT_UDP_MP2T_TRANSPORT_URNS:
        return "srt-udp-mp2t", None
    if "rtp" in transport:
        return "rtp", None
    return None


def _leg_is_enabled_for_sdp(active_params: Any) -> bool:
    """Return True if the leg's active transport params carry a real IP.

    ``init_sender_activation`` (activation.py:128) writes "0.0.0.0" to
    disabled legs; SDP emission filters the same way — entries where
    ``bindings[mediaIndex] != leg.Name`` are skipped.
    """
    if active_params is None:
        return False
    src_ip_field = getattr(active_params, "SourceIp", None)
    if src_ip_field is None or not src_ip_field.defined:
        return False
    v = src_ip_field.value
    if v is None or v == "" or v == "0.0.0.0":
        return False
    return True


def _resolve_leg_ips_and_ports(active_params: Any, sender_index: int,
                                interface_ip: str) -> dict[str, Any]:
    """Resolve a leg's source_ip/source_port/dest_ip/dest_port from its
    active transport params, applying the same ``"auto"`` expansion the
    single-leg path historically used.
    """
    source_ip = interface_ip
    src_port = 27500 + 2 * sender_index
    dest_ip = "0.0.0.0"
    dest_port = 27500 + 2 * sender_index
    if active_params is None:
        return {
            SourceIp.s: source_ip, "src_port": src_port,
            "dest_ip": dest_ip, "dest_port": dest_port,
        }
    if hasattr(active_params, "SourceIp") and active_params.SourceIp.defined:
        v = active_params.SourceIp.value
        source_ip = interface_ip if v == "auto" or not v else str(v)
    if hasattr(active_params, "SourcePort") and active_params.SourcePort.defined:
        try:
            sp = active_params.SourcePort.value
            if sp is not None and sp != "auto":
                src_port = int(sp)
        except (ValueError, TypeError):
            pass
    if hasattr(active_params, "DestinationIp") and active_params.DestinationIp.defined:
        v = active_params.DestinationIp.value
        dest_ip = "0.0.0.0" if v == "auto" or not v else str(v)
    if hasattr(active_params, "DestinationPort") and active_params.DestinationPort.defined:
        try:
            dp = active_params.DestinationPort.value
            if dp is not None and dp != "auto":
                dest_port = int(dp)
        except (AttributeError, ValueError, TypeError):
            pass
    return {
        SourceIp.s: source_ip, "src_port": src_port,
        "dest_ip": dest_ip, "dest_port": dest_port,
    }


def _apply_privacy_to_media(media: Any, active_params: Any, E: Any,
                              emit_ext_map: bool) -> None:
    """Populate ``media.privacy_desc`` + ``media.privacy`` from a leg's
    active transport params. ``emit_ext_map`` controls whether the two PEP
    extmap entries are added (RTP branch only — TCP-control manifest does
    not emit rtp-hdrext extmaps).
    """
    if active_params is None:
        return
    media.privacy = True
    pd = media.privacy_desc
    from sdp.MatroxSdp import EnumId as SdpEnumId
    _proto = getattr(active_params, "ExtPrivacyProtocol", None)
    if _proto is not None and hasattr(_proto, "defined") and _proto.defined:
        pd.protocol = SdpEnumId(str(_proto.value))
    _mode = getattr(active_params, "ExtPrivacyMode", None)
    if _mode is not None and hasattr(_mode, "defined") and _mode.defined:
        pd.mode = SdpEnumId(str(_mode.value))
    for src_name, dst_name in (
        ("ExtPrivacyIV", "iv"),
        ("ExtPrivacyKeyGenerator", "key_generator"),
        ("ExtPrivacyKeyId", "key_id"),
        ("ExtPrivacyKeyVersion", "key_version"),
    ):
        field = getattr(active_params, src_name, None)
        if field is not None and hasattr(field, "defined") and field.defined:
            setattr(pd, dst_name, str(field.value))
    if emit_ext_map:
        # PEP extmap entries — RTP only
        media.ext_map[0].id = 1
        media.ext_map[0].direction = "sendonly"
        media.ext_map[0].uri = "urn:ietf:params:rtp-hdrext:PEP-Full-IV-Counter"
        media.ext_map[1].id = 2
        media.ext_map[1].direction = "sendonly"
        media.ext_map[1].uri = "urn:ietf:params:rtp-hdrext:PEP-Short-IV-Counter"


def _populate_media_for_leg(*, media: Any, transport: str, category: str,
                              extra: Any, active_params: Any,
                              flow_inner: Any, node: Any, sender: Any,
                              interface_ip: str, sender_index: int,
                              ptp_gmid: str, ptp_version: str,
                              found_ptp: bool, E: Any) -> None:
    """Populate one MediaDescriptor for a single leg.

    Branches on transport category:
      - ``tcp-control``: RTSP/USB manifest (m=application <port> TCP <fmt>)
      - ``srt-udp-mp2t``: MPEG2-TS over UDP (m=application <port> UDP mp2t)
      - ``rtp``: full flow-derived RTP/AVP media section
    """
    leg_addrs = _resolve_leg_ips_and_ports(active_params, sender_index, interface_ip)
    source_ip = leg_addrs[SourceIp.s]
    src_port = leg_addrs["src_port"]
    dest_ip = leg_addrs["dest_ip"]
    dest_port = leg_addrs["dest_port"]

    # --- TCP-control (RTSP / USB) manifest -------------------------------
    if category == "tcp-control":
        media.media_name = "application"
        media.type = E.Application.value
        media.protocol = E.ProtocolTCP.value
        media.format_string = E.FormatRtsp.value if extra == "rtsp" else E.FormatUsb.value
        media.format_code = 0
        media.port = src_port
        media.port_count = 1
        media.connection_address = source_ip
        media.connection_count = 1
        if node.privacy_enabled:
            _apply_privacy_to_media(media, active_params, E, emit_ext_map=False)
        return

    # --- Common RTP/SRT-UDP-MP2T media fields ----------------------------
    _is_srt_udp_mp2t = (category == "srt-udp-mp2t")
    if _is_srt_udp_mp2t:
        # SRT/UDP non-RTP — use LISTENER address (source_ip, source_port).
        # Per spec §SDP format-specific parameters.
        media.port = src_port
        media.connection_address = source_ip
    else:
        media.port = dest_port
        media.connection_address = dest_ip
    media.port_count = 1
    media.protocol = E.ProtocolRTP_AVP.value
    media.format_code = 96
    media.payload_type = 96
    media.clock_rate = 90000
    media.connection_count = 1
    media.rtcp_port = dest_port + 1

    # Source filter — always set from the leg's active params
    media.source_filter_src_address = source_ip
    media.source_filter_dst_address = dest_ip

    # PTP clock (session-level data, but lives on the media descriptor)
    if found_ptp:
        media.ts_ref_clock_source = E.PTP.value
        media.ts_ref_clock_ptp_version = ptp_version
        media.ts_ref_clock_ptp_gmid = ptp_gmid
        media.ts_ref_clock_ptp_domain = "0"
    else:
        media.ts_ref_clock_source = E.LocalMac.value
        media.ts_ref_clock_local_mac_address = "00-00-00-00-00-00"

    media.media_clock_type = E.Sender.value

    # --- Populate from flow (video / audio / coded / mux branches) -------
    if flow_inner is not None:
        type_name = type(flow_inner).__name__

        if "VideoRaw" in type_name:
            media.media_name = "video"
            media.type = E.Video.value
            media.encoding_name = E.EncodingRaw.value
            media.clock_rate = 90000

            if hasattr(flow_inner, 'FrameWidth') and flow_inner.FrameWidth.defined:
                media.width = flow_inner.FrameWidth.value
            if hasattr(flow_inner, 'FrameHeight') and flow_inner.FrameHeight.defined:
                media.height = flow_inner.FrameHeight.value
            if hasattr(flow_inner, 'Components') and flow_inner.Components.defined:
                comps = flow_inner.Components.value
                if comps and comps[0].BitDepth.defined:
                    media.depth = comps[0].BitDepth.value
                if comps:
                    names = [str(c.Name.value) if c.Name.defined else "" for c in comps]
                    if set(names) & {R.s, G.s, B.s}:
                        media.sampling = E.SamplingRGB.value
                    elif len(comps) >= 3:
                        w0 = comps[0].Width.value if comps[0].Width.defined else 0
                        w1 = comps[1].Width.value if comps[1].Width.defined else 0
                        h0 = comps[0].Height.value if comps[0].Height.defined else 0
                        h1 = comps[1].Height.value if comps[1].Height.defined else 0
                        if w0 == w1 and h0 == h1:
                            media.sampling = E.SamplingYCbCr_444.value
                        elif w0 == w1 * 2 and h0 == h1 * 2:
                            media.sampling = E.SamplingYCbCr_420.value
                        else:
                            media.sampling = E.SamplingYCbCr_422.value

            try:
                flow_core = _get_flow_core(node.flows.get(sender.FlowId.value))
                if flow_core.GrainRate.defined:
                    gr = flow_core.GrainRate.value
                    media.exact_frame_rate_numerator = gr.Numerator.value if gr.Numerator.defined else 0
                    media.exact_frame_rate_denominator = gr.Denominator.value if gr.Denominator.defined else 1
            except InvalidObject:
                pass

            if hasattr(flow_inner, 'Colorspace') and flow_inner.Colorspace.defined:
                cs = str(flow_inner.Colorspace.value)
                _colorimetry_map = {
                    BT601.s: E.ColorimetryBT601,
                    BT709.s: E.ColorimetryBT709,
                    BT2020.s: E.ColorimetryBT2020,
                    BT2100.s: E.ColorimetryBT2100,
                }
                media.colorimetry = _colorimetry_map.get(cs, E.ColorimetryBT709).value

            if hasattr(flow_inner, 'TransferCharacteristic') and flow_inner.TransferCharacteristic.defined:
                tc = str(flow_inner.TransferCharacteristic.value)
                _tcs_map = {SDR.s: E.TransferSDR, PQ.s: E.TransferPQ, HLG.s: E.TransferHLG}
                if tc in _tcs_map:
                    media.transfer_characteristic = _tcs_map[tc].value

            if hasattr(flow_inner, 'InterlaceMode') and flow_inner.InterlaceMode.defined:
                im = str(flow_inner.InterlaceMode.value)
                if im == InterlacedTff.s:
                    media.interlaced = True
                    media.top_field_first = True
                elif im == InterlacedBff.s:
                    media.interlaced = True
                elif im == InterlacedPsf.s:
                    media.interlaced = True
                    media.segmented = True

            media.sender_type = E.SenderType2110TPW.value
            media.packing_mode = E.PackingMode2110GPM.value
            media.smpte_standard_number = "ST2110-20:2017"

        elif "Audio" in type_name:
            media.media_name = "audio"
            media.type = E.Audio.value

            mt = ""
            if hasattr(flow_inner, 'MediaType') and flow_inner.MediaType.defined:
                mt = str(flow_inner.MediaType.value)
                _audio_enc = {
                    AudioRawL8.s: E.EncodingL8,
                    AudioRawL16.s: E.EncodingL16,
                    AudioRawL20.s: E.EncodingL20,
                    AudioRawL24.s: E.EncodingL24,
                    AudioCodedAm824.s: E.EncodingAM824,
                    AudioCodedAac.s: E.EncodingAAC,
                    AudioCodedAacLATM.s: E.EncodingAAC_LATM,
                    AudioCodedAacLATM.s.lower(): E.EncodingAAC_LATM,
                    AudioCodedAacADTS.s: E.EncodingAAC_ADTS,
                    AudioCodedAacADTS.s.lower(): E.EncodingAAC_ADTS,
                }
                enc = _audio_enc.get(mt)
                if enc is None:
                    raise ValueError(
                        f"unsupported audio media_type '{mt}' for SDP encoding — "
                        f"add it to _audio_enc map"
                    )
                media.encoding_name = enc.value

            try:
                flow_core = _get_flow_core(node.flows.get(sender.FlowId.value))
                if flow_core.GrainRate.defined:
                    gr = flow_core.GrainRate.value
                    media.sample_rate = gr.Numerator.value if gr.Numerator.defined else 48000
                    media.clock_rate = media.sample_rate
            except (AttributeError, InvalidObject):
                media.sample_rate = 48000
                media.clock_rate = 48000

            try:
                source_id = _get_flow_core(node.flows.get(sender.FlowId.value)).SourceId.value
                source = node.sources.get(source_id)
                if source is not None:
                    src_inner = source.get() if hasattr(source, 'get') else source
                    if src_inner and hasattr(src_inner, 'Channels') and src_inner.Channels.defined:
                        media.channels = len(src_inner.Channels.value)
            except InvalidObject:
                pass
            if not media.channels:
                media.channels = 2

            channel_order = ""
            is_am824 = mt == AudioCodedAm824.s
            try:
                source_id = _get_flow_core(node.flows.get(sender.FlowId.value)).SourceId.value
                source = node.sources.get(source_id)
                if source is not None:
                    src_inner = source.get() if hasattr(source, 'get') else source
                    if src_inner and hasattr(src_inner, 'Channels') and src_inner.Channels.defined:
                        channel_order = _get_sdp_channel_order(src_inner.Channels.value, is_am824)
            except InvalidObject:
                pass
            if not channel_order:
                channel_order = f"SMPTE2110.(U{media.channels:02d})"
            media.channel_order = channel_order

            media.p_time_us = _select_packet_time_us(media.sample_rate, media.channels)
            media.max_p_time_us = media.p_time_us
            media.frame_count = int((media.p_time_us * media.sample_rate) / 1000000)

        elif "VideoCoded" in type_name:
            media.media_name = "video"
            media.type = E.Video.value
            media.clock_rate = 90000

            if hasattr(flow_inner, 'MediaType') and flow_inner.MediaType.defined:
                mt = str(flow_inner.MediaType.value)
                _video_enc = {
                    VideoCodedJxsv.s: E.EncodingJxsv,
                    VideoCodedH264.s: E.EncodingH264,
                    VideoCodedH265.s: E.EncodingH265,
                }
                enc = _video_enc.get(mt)
                if enc is None:
                    raise ValueError(
                        f"unsupported coded video media_type '{mt}' for SDP encoding — "
                        f"add it to _video_enc map"
                    )
                media.encoding_name = enc.value

            if hasattr(flow_inner, 'FrameWidth') and flow_inner.FrameWidth.defined:
                media.width = flow_inner.FrameWidth.value
            if hasattr(flow_inner, 'FrameHeight') and flow_inner.FrameHeight.defined:
                media.height = flow_inner.FrameHeight.value
            if hasattr(flow_inner, 'Colorspace') and flow_inner.Colorspace.defined:
                cs = str(flow_inner.Colorspace.value)
                _colorimetry_map = {
                    BT601.s: E.ColorimetryBT601, BT709.s: E.ColorimetryBT709,
                    BT2020.s: E.ColorimetryBT2020, BT2100.s: E.ColorimetryBT2100,
                }
                media.colorimetry = _colorimetry_map.get(cs, E.ColorimetryBT709).value

            try:
                flow_core = _get_flow_core(node.flows.get(sender.FlowId.value))
                if flow_core.GrainRate.defined:
                    gr = flow_core.GrainRate.value
                    media.exact_frame_rate_numerator = gr.Numerator.value if gr.Numerator.defined else 0
                    media.exact_frame_rate_denominator = gr.Denominator.value if gr.Denominator.defined else 1
            except InvalidObject:
                pass

            if hasattr(flow_inner, 'Components') and flow_inner.Components.defined:
                comps = flow_inner.Components.value
                if comps and comps[0].BitDepth.defined:
                    media.depth = comps[0].BitDepth.value
                if comps:
                    names = [str(c.Name.value) if c.Name.defined else "" for c in comps]
                    if set(names) & {R.s, G.s, B.s}:
                        media.sampling = E.SamplingRGB.value
                    elif len(comps) >= 3:
                        w0 = comps[0].Width.value if comps[0].Width.defined else 0
                        w1 = comps[1].Width.value if comps[1].Width.defined else 0
                        if w0 == w1:
                            media.sampling = E.SamplingYCbCr_444.value
                        else:
                            media.sampling = E.SamplingYCbCr_422.value

            if hasattr(flow_inner, 'Bitrate') and flow_inner.Bitrate.defined:
                media.bitrate_kbits = flow_inner.Bitrate.value
            elif hasattr(flow_inner, 'BitRate') and flow_inner.BitRate.defined:
                media.bitrate_kbits = flow_inner.BitRate.value

            mt = str(flow_inner.MediaType.value) if flow_inner.MediaType.defined else ""
            if mt == VideoCodedH264.s:
                media.codec_profile_level_id = _get_h264_profile_level_id(flow_inner)
                media.h264_packetization_mode = 1  # Non-interleaved
                media.sender_type = E.SenderType2110TPW.value

        elif "Mux" in type_name or "FlowMux" in type_name:
            mt = ""
            if hasattr(flow_inner, 'MediaType') and flow_inner.MediaType.defined:
                mt = str(flow_inner.MediaType.value)

            if mt == MuxAm824.s:
                _BLOCKED_TRANSPORTS = {
                    TransportSrt.s,
                    "urn:x-matrox:transport:srt.mpeg2ts",
                    TransportUdp.s,
                    TransportUdpUnicast.s,
                    TransportUdpMulticast.s,
                    "urn:x-matrox:transport:udp.mpeg2ts",
                    "urn:x-matrox:transport:udp.mpeg2ts.ucast",
                    "urn:x-matrox:transport:udp.mpeg2ts.mcast",
                }
                if transport in _BLOCKED_TRANSPORTS:
                    raise ValueError(
                        f"AM824 mux cannot use transport '{transport}' — "
                        "requires RTP or RTSP (not MPEG2-TS aware)"
                    )

                media.media_name = "audio"
                media.type = E.Audio.value
                media.encoding_name = E.EncodingAM824.value

                try:
                    flow_core = _get_flow_core(node.flows.get(sender.FlowId.value))
                    if flow_core.GrainRate.defined:
                        gr = flow_core.GrainRate.value
                        media.sample_rate = gr.Numerator.value if gr.Numerator.defined else 48000
                        media.clock_rate = media.sample_rate
                except (AttributeError, InvalidObject):
                    media.sample_rate = 48000
                    media.clock_rate = 48000

                flow_ptr = node.flows.get(sender.FlowId.value)
                channel_order, ch_count = _get_aes3_composite_channel_order(node, flow_ptr)
                media.channels = ch_count
                media.channel_order = channel_order

                media.p_time_us = _select_packet_time_us(media.sample_rate, media.channels)
                media.max_p_time_us = media.p_time_us
                media.frame_count = int((media.p_time_us * media.sample_rate) / 1000000)

            elif mt in (MuxMpeg2TS.s, MuxGeneric.s):
                if "rtp" in transport:
                    media.media_name = "video"
                    media.type = E.Video.value
                    media.clock_rate = 90000
                    if hasattr(E, 'EncodingMP2T'):
                        media.encoding_name = E.EncodingMP2T.value
                    else:
                        media.encoding_name = "MP2T"
                else:
                    media.media_name = "application"
                    media.type = E.Application.value
                    media.protocol = E.ProtocolUDP.value
                    media.format_string = E.FormatMpeg2TS.value
                    media.format_code = 0
                    media.clock_rate = 90000
            else:
                media.media_name = "video"
                media.type = E.Video.value
                media.encoding_name = E.EncodingRaw.value

        else:
            media.media_name = "video"
            media.type = E.Video.value
            media.encoding_name = E.EncodingRaw.value
    else:
        media.media_name = "video"
        media.type = E.Video.value
        media.encoding_name = E.EncodingRaw.value

    # IPMX flag — node-level property, applies to ALL media types
    # Sets MeasuredSampleRate + MeasuredPixClk/VTotal/HTotal
    if node.ipmx:
        media.ipmx = True
        if media.sample_rate:
            media.measured_sample_rate = media.sample_rate
        if media.width and media.height:
            _set_ipmx_timing(media)

    # Privacy — per-leg
    if node.privacy_enabled:
        _apply_privacy_to_media(media, active_params, E, emit_ext_map=True)


def _generate_sdp_from_params(node: Any, sender: Any, sender_id: str,
                               activation: Any = None) -> str | None:
    """Generate SDP transport file using the sdp/ module (MatroxSdp + MatroxSdpWrite).

    Structure:
      1. Classify the transport → category + optional extra.
      2. Resolve session-level context (interface_ip, flow, PTP).
      3. Build the SDP session fields (o=, s=, t=).
      4. Per-leg loop — for each enabled leg, populate a MediaDescriptor
         using the per-transport switch. Iterates legs, incrementing
         ``mediaIndex`` only for enabled legs.
      5. Finalize: when two legs were emitted, set ``has_group_attribute``
         and the primary/secondary pointers so the encoder emits
         ``a=group:DUP primary secondary`` + ``a=mid:<name>`` per
         NMOS With Redundancy.md line 29.
      6. Encode and return.
    """
    from sdp.MatroxSdp import MatroxSdp, MatroxSdpEnums as E
    from sdp.MatroxSdpWrite import encode as sdp_encode

    # Auto-fetch activation if caller didn't provide one (test convenience)
    if activation is None and sender_id:
        try:
            activation = node.get_sender_activation(sender_id)
        except Exception:
            pass

    # --- Step 1: classify transport ------------------------------------
    transport = str(sender.Transport.value) if sender.Transport.defined else ""
    classification = _sdp_transport_category(transport)
    if classification is None:
        return None
    category, extra = classification

    # --- Step 2: session-level resolution -------------------------------
    interface_ip = "0.0.0.0"
    if node.node_value is not None:
        try:
            ep = node.node_value.Api.value.Endpoints
            if ep.defined and len(ep.value) > 0:
                interface_ip = ep.value[0].Host.value
        except AttributeError:
            pass

    # Origin IP — uses leg 0's interface.
    origin_ip = interface_ip
    if activation is not None and activation.active:
        leg0_addrs = _resolve_leg_ips_and_ports(
            activation.active[0],
            activation.sender_index if hasattr(activation, 'sender_index') else 0,
            interface_ip,
        )
        origin_ip = leg0_addrs[SourceIp.s]

    flow_inner = None
    if sender.FlowId.defined and sender.FlowId.value is not None:
        flow = node.flows.get(sender.FlowId.value)
        if flow is not None:
            if hasattr(flow, 'get') and callable(flow.get):
                got = flow.get()
                if got is not None:
                    flow_inner = got

    # PTP clock lookup (session-level fact)
    ptp_gmid = "00-00-00-00-00-00-00-00"
    ptp_version = IEEE1588_2008.s
    found_ptp = False
    try:
        if node.node_value is not None and node.node_value.Clocks.defined:
            for clock_val in node.node_value.Clocks._value._inner:
                wrapper = clock_val._inner if hasattr(clock_val, '_inner') else None
                if wrapper is None:
                    continue
                ptp_val = wrapper._value if hasattr(wrapper, '_value') else None
                if ptp_val is not None and hasattr(ptp_val, 'Gmid') and ptp_val.Gmid.defined:
                    ptp_gmid = ptp_val.Gmid.value
                    ptp_version = str(ptp_val.Version.value) if ptp_val.Version.defined else ptp_version
                    found_ptp = True
                    break
    except AttributeError:
        pass

    # --- Step 3: SDP session-level fields -------------------------------
    sdp = MatroxSdp()
    sess_id = sender.ResourceCore.Id.value.replace("-", "")[:16]
    sdp.username = "-"
    sdp.session_id = int(sess_id, 16) if sess_id else 1
    sdp.session_version = sdp.session_id
    sdp.origin_address = origin_ip
    sdp.session_name = sender.ResourceCore.Label.value if sender.ResourceCore.Label.defined else "-"
    sdp.start = 0
    sdp.stop = 0

    # --- Step 4: per-leg loop --------------------------------------------
    sender_index = activation.sender_index if activation is not None and hasattr(activation, 'sender_index') else 0
    media_index = 0
    for leg_index in range(MAX_LEGS):
        active_params = (activation.active[leg_index]
                         if activation is not None and activation.active
                         and leg_index < len(activation.active)
                         else None)
        if not _leg_is_enabled_for_sdp(active_params):
            continue
        if media_index >= MAX_LEGS:
            break
        _populate_media_for_leg(
            media=sdp.medias[media_index],
            transport=transport,
            category=category,
            extra=extra,
            active_params=active_params,
            flow_inner=flow_inner,
            node=node,
            sender=sender,
            interface_ip=interface_ip,
            sender_index=sender_index,
            ptp_gmid=ptp_gmid,
            ptp_version=ptp_version,
            found_ptp=found_ptp,
            E=E,
        )
        media_index += 1

    # Fallback: if no leg was classified as enabled (test fixtures may
    # bypass init_sender_activation), emit a single-leg SDP using the
    # first active-params slot so downstream callers get a valid SDP.
    if media_index == 0:
        fallback_params = (activation.active[0]
                           if activation is not None and activation.active
                           else None)
        _populate_media_for_leg(
            media=sdp.medias[0],
            transport=transport,
            category=category,
            extra=extra,
            active_params=fallback_params,
            flow_inner=flow_inner,
            node=node,
            sender=sender,
            interface_ip=interface_ip,
            sender_index=sender_index,
            ptp_gmid=ptp_gmid,
            ptp_version=ptp_version,
            found_ptp=found_ptp,
            E=E,
        )
        media_index = 1

    # --- Step 5: finalize group attributes / primary-secondary pointers -
    if media_index >= 2:
        # Spec NMOS With Redundancy.md:29 + MatroxSdpWrite.py:206 —
        # two-leg SDP must emit `a=group:DUP primary secondary` + two
        # `m=` sections with `a=mid:primary` / `a=mid:secondary`.
        sdp.has_group_attribute = True
        sdp.primary_media_name = "primary"
        sdp.secondary_media_name = "secondary"
        sdp.medias[0].media_name = "primary"
        sdp.medias[1].media_name = "secondary"
        sdp.primary_media = sdp.medias[0]
        sdp.secondary_media = sdp.medias[1]
        sdp.media_count = 2
    else:
        sdp.primary_media = sdp.medias[0]
        sdp.primary_media_name = sdp.medias[0].media_name
        sdp.media_count = 1

    # --- Step 6: encode --------------------------------------------------
    try:
        result: str | None = sdp_encode(sdp)
        return result
    except Exception as _sdp_err:
        import logging
        logging.warning(f"SDP encode failed: {_sdp_err}")
        return None



def _nmos_version_now() -> tuple[int, int]:
    """Generate an NMOS version timestamp as (utc_seconds, nanoseconds).

    Returns current UTC time.
    NTime handles TAI conversion internally on encode/decode.
    """
    import time as _time
    t = _time.time_ns()
    sec = t // 1_000_000_000
    nsec = t % 1_000_000_000
    return (sec, nsec)


def _set_version_now(resource_core: Any) -> None:
    """Set ResourceCore.Version to current time."""
    resource_core.Version.value = _nmos_version_now()


def _extract_group_hint(resource: Any) -> str:
    """Extract first urn:x-nmos:tag:grouphint/v1.0 value from ResourceCore.Tags."""
    try:
        tags_field = resource.ResourceCore.Tags
        if not tags_field.defined:
            return ""
        tags = tags_field.value
        if not isinstance(tags, dict):
            return ""
        for key, values in tags.items():
            if str(key) == TagGroupHint.s and values:
                first = values[0]
                return str(first) if first is not None else ""
    except (AttributeError, TypeError):
        return ""
    return ""


def _extract_receiver_format(receiver: Any) -> Any:
    """Extract receiver format enum from inner receiver value.

    A receiver MUST have a format — this raises if it doesn't.
    """
    inner = receiver.get() if hasattr(receiver, 'get') else None
    if inner is None:
        raise InvalidObject("receiver has no inner value")
    return inner.Format.value


def _get_source_core(source: Any) -> Any:
    """Get the SourceCore from a polymorphic NSourceValue.

    NSourceValue._inner is NSourceVideoValue/NSourceAudioValue/etc.
    Each has a .SourceCore attribute of type NSourceCoreValue.
    """
    inner = source.get() if hasattr(source, 'get') else source
    if inner is None:
        raise InvalidObject("source has no inner value")
    if hasattr(inner, 'SourceCore'):
        return inner.SourceCore
    raise InvalidObject("source inner value has no SourceCore")


def _get_flow_core(flow: Any) -> Any:
    """Get the FlowCore from a polymorphic NFlowValue.

    NFlowValue._inner is NFlowVideoRawValue/NFlowAudioRawValue/etc.
    Each has a .FlowCore attribute of type NFlowCoreValue.
    """
    inner = flow.get() if hasattr(flow, 'get') else flow
    if inner is None:
        raise InvalidObject("flow has no inner value")
    if hasattr(inner, 'FlowCore'):
        return inner.FlowCore
    raise InvalidObject("flow inner value has no FlowCore")


def _get_receiver_core(receiver: Any) -> Any:
    """Get the ReceiverCore from a polymorphic NReceiverValue.

    NReceiverValue._inner is NReceiverVideoValue/etc.
    Each has a .ReceiverCore attribute of type NReceiverCoreValue.
    """
    inner = receiver.get() if hasattr(receiver, 'get') else receiver
    if inner is None:
        raise InvalidObject("receiver has no inner value")
    if hasattr(inner, 'ReceiverCore'):
        return inner.ReceiverCore
    raise InvalidObject("receiver inner value has no ReceiverCore")


def _get_resource_core(resource: Any) -> Any:
    """Get ResourceCore from any resource type.

    Senders have it directly. Polymorphic types (source, flow, receiver)
    have it nested inside their core.
    """
    # Direct access (NSenderValue)
    if hasattr(resource, 'ResourceCore'):
        return resource.ResourceCore

    # Polymorphic: try inner type's core
    inner = resource.get() if hasattr(resource, 'get') else None
    if inner is not None:
        # Source → SourceCore.ResourceCore
        if hasattr(inner, 'SourceCore'):
            return inner.SourceCore.ResourceCore
        # Flow → FlowCore.ResourceCore
        if hasattr(inner, 'FlowCore'):
            return inner.FlowCore.ResourceCore
        # Receiver → ReceiverCore.ResourceCore
        if hasattr(inner, 'ReceiverCore'):
            return inner.ReceiverCore.ResourceCore
        # Direct ResourceCore on inner
        if hasattr(inner, 'ResourceCore'):
            return inner.ResourceCore

    raise InvalidObject("cannot find ResourceCore on resource")


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

class Node:
    """NMOS Node — central resource and lifecycle manager.

    Holds 4 resource types (receivers, sources, flows, senders) in
    ResourceStore maps keyed by static IDs. Provides atomic CRUD
    operations with bidirectional linking and UUID cascade on update.
    """

    def __init__(self) -> None:
        # --- Configuration ---
        self.serial_number: str = ""
        self.ipmx: bool = False
        self.rfc: bool = True
        self.hkep: bool = False
        self.privacy_enabled: bool = False
        self.streaming: bool = False
        self.wall_group: int = 0
        self.oauth2: bool = False
        self.oauth2_keys: Any = None
        # CN + DNS-SAN list extracted from the Node's TLS server cert.
        # Used by the IS-10 audience check (both serial-number and
        # DNS-name rules — see ``aud_entry_allows_current_node``).
        # Populated by ``nmos_node.py`` at startup from the cert
        # mounted via ``--nodeCertificate``. Empty list when TLS is
        # disabled or the cert isn't readable; the IS-10 aud check
        # then fails closed in production.
        self.tls_server_cert_names: list[str] = []
        # TLS posture: set when nmos_node.py was launched with --tls (or
        # --oauth2 / --mutual-tls which both imply --tls). Drives the
        # ``controls[]`` href scheme and is mirrored to the IS-04 payload
        # so downstream peers can adapt their connection strategy.
        self.tls_enabled: bool = False
        # Method-aware mTLS — same boolean already consumed by the
        # ``client_auth_middleware`` in [nmos/api/middleware.py]. When
        # True the SSL context comes up with verify_mode=CERT_OPTIONAL
        # (so cert-less reads still complete the handshake) and the
        # middleware rejects state-changing methods unless a peer cert
        # was presented and chain-validated. Set via ``--mutual-tls``
        # in nmos_node.py and the IS-04 ``controls[]`` extension
        # ``urn:x-matrox:control:mutual_tls_writes`` (Phase 4).
        self.client_auth_required: bool = False
        # Populated by the main process when Reservation is enabled
        # (``nmos_node.py`` sets this during startup). Left unset the
        # middleware's ``getattr(node, "exclusive_session", None)`` treats
        # Reservation as disabled.
        self.exclusive_session: Any = None

        # --- Network ---
        self.legs: list[Leg] = [Leg() for _ in range(MAX_LEGS)]
        self.interfaces: list[Interface] = [Interface() for _ in range(MAX_INTERFACES)]

        # --- Node and Device (generated types, set during init) ---
        self.node_value: Any = None       # NNodeValue
        self.device_value: Any = None     # NDeviceValue

        # --- Resource stores ---
        self.receivers: ResourceStore[Any] = ResourceStore()
        self.sources: ResourceStore[Any] = ResourceStore()
        self.flows: ResourceStore[Any] = ResourceStore()
        self.senders: ResourceStore[Any] = ResourceStore()

        # --- Associated state ---
        self.sender_activation: ResourceStore[Activation] = ResourceStore()
        self.receiver_activation: ResourceStore[Activation] = ResourceStore()
        self.constraints: ResourceStore[Any] = ResourceStore()
        self.sdp: ResourceStore[Any] = ResourceStore()  # static_id → parsed MatroxSdp

        # --- CCF caps cache (avoids NMOS→JSON→CCF conversion on every request) ---
        # Keyed by static resource ID. Set at pipeline build time, updated on
        # PUT/DELETE active constraints. compatibility.py reads these directly.
        self.sender_ccf_caps: dict[str, Any] = {}       # static_id → CCF Caps (native capabilities)
        self.sender_ccf_normalized: dict[str, Any] = {}  # static_id → CCF Cons (normalized active constraints)
        self.sender_ccf_merged: dict[str, Any] = {}      # static_id → CCF Caps (after merge)
        self.receiver_ccf_caps: dict[str, Any] = {}      # static_id → CCF Caps (native capabilities)

        # --- Streaming engine event queue ---
        # Shared by all sender/receiver streaming tasks. Events use the
        # engineEvent structure (see nmos/node/events.py). Future consumers:
        # IS-12 Events API, console monitoring, logging.
        import asyncio as _asyncio
        from nmos.node.events import EngineEvent
        self.event_queue: _asyncio.Queue[EngineEvent] = _asyncio.Queue(maxsize=100)
        self.monitor_lock: _asyncio.Lock = _asyncio.Lock()

        # --- Index pools ---
        self.source_indices: PoolOfIndices = PoolOfIndices()
        self.flow_indices: PoolOfIndices = PoolOfIndices()
        self.sender_indices: PoolOfIndices = PoolOfIndices()
        self.receiver_indices: PoolOfIndices = PoolOfIndices()

        # --- Natural groups ---
        self.sender_natural_groups: NaturalGroups = NaturalGroups()
        self.receiver_natural_groups: NaturalGroups = NaturalGroups()

        # --- Publish ---
        self.publish_manager: PublishManager = PublishManager()

        # --- Garbage tracking ---
        self.garbage_sources: deque[GarbageResource] = deque(maxlen=256)
        self.garbage_flows: deque[GarbageResource] = deque(maxlen=256)

        # --- Initialization state ---
        self._initialized: bool = False

    # ===================================================================
    # Init
    # ===================================================================

    def init(
        self,
        *,
        serial_number: str,
        host: str = "",
        port: int = 5050,
        control_port: int = 0,
        ipmx: bool = False,
        privacy: bool = True,   # const HasTransportPrivacyEncryption = true
        streaming: bool = False,
        hkep: bool = False,
        wall_group: int = 0,
        oauth2: bool = False,
        tls_enabled: bool = False,
        client_auth_required: bool = False,
        security_tags: dict[str, list[str]] | None = None,
        legs: list[Leg] | None = None,
        interfaces: list[Interface] | None = None,
        node_label: str = "",
        node_description: str = "",
        device_label: str = "",
        device_description: str = "",
    ) -> None:
        """Initialize the node with configuration.

        Uses keyword arguments rather than a builder pattern.
        Must be called before any resource operations.
        """
        # Register alternate namespace aliases for decode tolerance.
        # Called here because generated types are now importable (they register
        # their enum keys on import). Safe to call multiple times.
        from nmos.enums import register_namespace_aliases
        register_namespace_aliases()

        self.serial_number = serial_number
        self.ipmx = ipmx
        self.privacy_enabled = privacy
        self.streaming = streaming
        self.hkep = hkep
        self.wall_group = wall_group
        self.oauth2 = oauth2
        self.tls_enabled = tls_enabled
        self.client_auth_required = client_auth_required
        # VSF TR-10-SEC §8 security configuration tags. Built in
        # nmos_node.py from the launch-time argparse namespace via
        # ``nmos.node.security_tags.compute_security_tags(args).to_tags()``
        # and merged into the Node's ``tags`` attribute below.
        self._security_tags: dict[str, list[str]] = dict(security_tags or {})

        if legs is not None:
            if len(legs) > MAX_LEGS:
                raise InvalidParameter(f"max {MAX_LEGS} legs, got {len(legs)}")
            for i, leg in enumerate(legs):
                self.legs[i] = leg

        if interfaces is not None:
            if len(interfaces) > MAX_INTERFACES:
                raise InvalidParameter(
                    f"max {MAX_INTERFACES} interfaces, got {len(interfaces)}"
                )
            for i, iface in enumerate(interfaces):
                self.interfaces[i] = iface

        # Resolve host if not provided
        if not host:
            import socket
            try:
                host = socket.gethostbyname(socket.gethostname())
            except OSError:
                host = "127.0.0.1"

        # Default leg with host interface name if no legs provided
        # (always provides at least one enabled leg with the interface name)
        if legs is None:
            iface_name = self._resolve_interface_name(host)
            self.legs[0] = Leg(name=iface_name, enable=True)

        # Create Node and Device resource values
        self._init_node_device(serial_number, host, port,
                               control_port,
                               node_label, node_description,
                               device_label, device_description)

        self._initialized = True

    def _init_node_device(
        self, serial_number: str,
        host: str, port: int,
        control_port: int,
        node_label: str, node_description: str,
        device_label: str, device_description: str,
    ) -> None:
        """Create NNodeValue and NDeviceValue for IS-04.

        These are the top-level NMOS resources returned by GET /self and GET /devices.
        """
        try:
            from nmos.types.generated.nnode import NNodeValue
            from nmos.types.generated.ndevice import NDeviceValue
            from nmos.uuid import ResourceType, ResourceSubType, ResourceUuid, update_resource_unique_id

            # BCP-002-02 asset distinguishing information constants
            _MANUFACTURER = "Example Company"
            _PRODUCT = "MY-NMOSNODE-AB2026"
            _FUNCTION = "Generic"

            # Node
            nv = NNodeValue()
            nv.set_to_default()
            nv.ResourceCore.Label.value = node_label or f"NMOS Node {serial_number}"
            nv.ResourceCore.Description.value = node_description or f"NMOS Node {serial_number}"
            node_uuid = ResourceUuid()
            node_uuid.set(ResourceType.NODE, ResourceSubType.NONE, 0, serial_number, 0, False)
            nv.ResourceCore.Id.value = str(node_uuid)
            nv.ResourceCore.StaticId.value = update_resource_unique_id(str(node_uuid), 0)
            _set_version_now(nv.ResourceCore)

            # BCP-002-02: Node asset tags. VSF TR-10-SEC §8 security
            # configuration tags are merged in afterwards — both name
            # spaces coexist in the same ``tags`` dict per the spec.
            _node_tags: dict[str, list[str]] = {
                TagAssetManufacturer.s: [_MANUFACTURER],
                TagAssetProduct.s: [_PRODUCT],
                TagAssetInstance.s: [serial_number],
            }
            _node_tags.update(self._security_tags)
            nv.ResourceCore.Tags.value = _node_tags

            # Node href (required)
            nv.Href.value = f"http://{host}:{port}/"

            # API versions and endpoints (required by NNodeApi validator)
            from nmos.types.generated.nnode_endpoint import NNodeEndpointValue
            from nmos.enums import EnumRegistry as _ER
            api = nv.Api.value  # NNodeApiValue (set_to_default already called)
            api.Versions.value = ["v1.3"]
            ep = NNodeEndpointValue()
            ep.set_to_default()
            ep.Host.value = host
            ep.Port.value = port
            ep.Protocol.value = _ER.get(Http.s)
            api.Endpoints.value = [ep]

            # Default clocks: PTP "clk0" + Internal "clk1"
            try:
                from nmos.types.generated.nclock_ptp import NClockPtp, NClockPtpValue
                from nmos.types.generated.nclock_internal import NClockInternal, NClockInternalValue
                from nmos.types.generated.nclock import NClockValue

                ptp_val = NClockPtpValue()
                ptp_val.set_to_default()
                ptp_val.Name.value = "clk0"
                ptp_val.RefType.value = _ER.get(Ptp.s)
                ptp_val.Traceable.value = False
                ptp_val.Version.value = _ER.get(IEEE1588_2008.s)
                ptp_val.Gmid.value = "00-00-00-00-00-00-00-00"
                ptp_val.Locked.value = True
                ptp_wrapper = NClockPtp()
                ptp_wrapper._defined = True
                ptp_wrapper._value = ptp_val

                int_val = NClockInternalValue()
                int_val.set_to_default()
                int_val.Name.value = "clk1"
                int_val.RefType.value = _ER.get(Internal.s)
                int_wrapper = NClockInternal()
                int_wrapper._defined = True
                int_wrapper._value = int_val

                clock0 = NClockValue()
                clock0.set(ptp_wrapper)
                clock1 = NClockValue()
                clock1.set(int_wrapper)
                nv.Clocks._defined = True
                nv.Clocks._value._inner = [clock0, clock1]
            except ImportError:
                pass

            # Node interfaces — populated from configured legs
            try:
                from nmos.types.generated.nnode_interface import NNodeInterfaceValue
                ifaces: list[NNodeInterfaceValue] = []
                for leg in self.legs:
                    if leg.enable and leg.name:
                        nif = NNodeInterfaceValue()
                        nif.set_to_default()
                        nif.Name.value = leg.name
                        # MAC address: use a placeholder (real impl reads from OS)
                        nif.ChassisId.value = None
                        nif.PortId.value = f"00-00-00-00-00-00"
                        ifaces.append(nif)
                if ifaces:
                    nv.Interfaces._defined = True
                    nv.Interfaces._value._inner = ifaces
            except ImportError:
                pass

            # Node services (the Node Reservation service advertised at
            # ``urn:x-matrox:service:exclusive/v1.0``). Controllers
            # discover the acquire/renew/release/keepalive base URL by
            # walking ``node.services`` for this URN — see
            # ``GetNodeManufactuerApi``. Without this entry the
            # controller's Privacy panel renders with Exclusivity
            # disabled even though the endpoints are live.
            try:
                from nmos.types.generated.nnode_service import NNodeServiceValue
                from nmos.enums import EnumRegistry
                # Scheme follows the Node's TLS posture so peers
                # consuming the IS-04 services array know whether to
                # talk plain HTTP or HTTPS.
                scheme = Https.s if self.tls_enabled else Http.s
                services: list[NNodeServiceValue] = []

                svc_exclusive = NNodeServiceValue()
                svc_exclusive.Href.value = (
                    f"{scheme}://{host}:{port}/x-manufacturer/exclusive/v1.0/"
                )
                # Service Type is NEnum — register/lookup the URN
                # through the enum registry (pattern used elsewhere in
                # the node for enum-typed URNs).
                svc_exclusive.Type.value = EnumRegistry.get(
                    "urn:x-matrox:service:exclusive/v1.0",
                )
                svc_exclusive.Authorization.value = self.oauth2
                services.append(svc_exclusive)

                nv.Services._defined = True
                nv.Services._value._inner = services
            except ImportError:
                pass

            self.node_value = nv

            # Device
            dv = NDeviceValue()
            dv.set_to_default()
            dv.ResourceCore.Label.value = device_label or f"NMOS Device {serial_number}"
            dv.ResourceCore.Description.value = device_description or f"NMOS Device {serial_number}"
            dev_uuid = ResourceUuid()
            dev_uuid.set(ResourceType.DEVICE, ResourceSubType.NONE, 0, serial_number, 0, False)
            dv.ResourceCore.Id.value = str(dev_uuid)
            dv.ResourceCore.StaticId.value = update_resource_unique_id(str(dev_uuid), 0)
            _set_version_now(dv.ResourceCore)
            dv.NodeId.value = nv.ResourceCore.Id.value

            # BCP-002-02: Device asset tags
            dv.ResourceCore.Tags.value = {
                TagAssetManufacturer.s: [_MANUFACTURER],
                TagAssetProduct.s: [_PRODUCT],
                TagAssetFunction.s: [_FUNCTION],
                TagAssetInstance.s: [serial_number],
            }

            # Device controls
            try:
                from nmos.types.generated.ndevice_control import NDeviceControlValue
                # Scheme follows the Node's TLS posture; the Authorization
                # field below mirrors ``self.oauth2`` so a controller can
                # decide per-control whether to inject a bearer token.
                #
                # The mTLS-writes signal (``self.client_auth_required``)
                # is NOT yet emitted on each control — adding the
                # ``urn:x-matrox:control:mutual_tls_writes`` field here
                # requires regenerating ``NDeviceControlValue`` (its
                # __slots__ are fixed). Wired in Phase 4 alongside the
                # controller's per-control gating; the Node-side
                # enforcement already runs through the existing
                # ``client_auth_middleware`` in nmos/api/middleware.py.
                scheme = Https.s if self.tls_enabled else Http.s
                # When ``--controlTrustedRootCA`` is set, the operator
                # has split IS-05 / IS-11 onto ``--controlPort`` so the
                # advertised hrefs MUST point there — otherwise remote
                # controllers would call the Node-API port, which no
                # longer serves those routes. ``control_port == 0``
                # means no split; fall back to the Node-API port.
                is05_port = control_port if control_port else port
                controls: list[NDeviceControlValue] = []

                # IS-05 Connection API v1.1
                ctrl_conn = NDeviceControlValue()
                ctrl_conn.Href.value = f"{scheme}://{host}:{is05_port}/x-nmos/connection/v1.1/"
                ctrl_conn.Type.value = "urn:x-nmos:control:sr-ctrl/v1.1"
                ctrl_conn.Authorization.value = self.oauth2
                controls.append(ctrl_conn)

                # IS-05 Connection API v1.2 (HasTransportControlVersion1_2)
                # No functional difference — internally served by the same v1.1 handlers
                ctrl_conn_v12 = NDeviceControlValue()
                ctrl_conn_v12.Href.value = f"{scheme}://{host}:{is05_port}/x-nmos/connection/v1.2/"
                ctrl_conn_v12.Type.value = "urn:x-nmos:control:sr-ctrl/v1.2"
                ctrl_conn_v12.Authorization.value = self.oauth2
                controls.append(ctrl_conn_v12)

                # IS-11 Stream Compatibility API v1.0
                ctrl_compat = NDeviceControlValue()
                ctrl_compat.Href.value = f"{scheme}://{host}:{is05_port}/x-nmos/streamcompatibility/v1.0/"
                ctrl_compat.Type.value = "urn:x-nmos:control:stream-compat/v1.0"
                ctrl_compat.Authorization.value = self.oauth2
                controls.append(ctrl_compat)

                # Manifest base URL — points at the same connection-API
                # base as the IS-05 v1.1 control above, so follows the
                # same split.
                ctrl_manifest = NDeviceControlValue()
                ctrl_manifest.Href.value = f"{scheme}://{host}:{is05_port}/x-nmos/connection/v1.1/"
                ctrl_manifest.Type.value = "urn:x-nmos:control:manifest-base/v1.0"
                ctrl_manifest.Authorization.value = self.oauth2
                controls.append(ctrl_manifest)

                dv.Controls._defined = True
                dv.Controls._value._inner = controls
            except ImportError:
                pass

            self.device_value = dv

        except ImportError:
            # Generated types not available — leave as None
            pass

    # ===================================================================
    # Receiver (creation order #1)
    # ===================================================================

    def add_receiver(
        self,
        receiver: Any,
        privacy_keys: PrivacyPreSharedKeys | None = None,
    ) -> str:
        """Add a new receiver to the node. Returns static receiver ID.

        Allocates index, generates UUID, links to device, initializes activation.
        """
        self._check_initialized()
        rc = _get_resource_core(receiver)

        # Must not have pre-existing ID
        if rc.Id.defined and rc.Id.value != "":
            raise InvalidParameter("receiver cannot have pre-defined id")

        # Validate format and transport are defined
        receiver_core = _get_receiver_core(receiver)
        format_enum = _extract_receiver_format(receiver)
        if format_enum is None:
            raise InvalidParameter("receiver not having an associated format")
        if not receiver_core.Transport.defined:
            raise InvalidParameter("receiver not having an associated transport")

        receiver_index = self.receiver_indices.get_index()
        try:
            unique_id = 0 if HAS_PERSISTENT_SENDER_RECEIVER_ID else _random_unique_id()
            uuid = ResourceUuid()
            uuid.set(
                ResourceType.RECEIVER, ResourceSubType.NONE,
                receiver_index, self.serial_number, unique_id, False,
            )
            receiver_id = str(uuid)
            static_id = to_static_id(receiver_id)

            rc.Id.value = receiver_id
            rc.StaticId.value = static_id
            _set_version_now(rc)
            # Bump node version
            if self.node_value is not None:
                _set_version_now(self.node_value.ResourceCore)

            # Natural group assignment
            transport_enum = receiver_core.Transport.value
            role_index = self._assign_natural_group(
                receiver,
                is_sender=False,
                format_enum=format_enum,
                transport_enum=transport_enum,
            )

            # Link to device
            if self.device_value is not None:
                device_id = self.device_value.ResourceCore.Id.value
                receiver_core.DeviceId.value = device_id

            # Default interface bindings from enabled legs
            if not receiver_core.InterfaceBindings.defined or \
               len(receiver_core.InterfaceBindings.value) == 0:
                bindings = [leg.name for leg in self.legs if leg.enable]
                receiver_core.InterfaceBindings.value = bindings
            else:
                # Validate specified bindings
                bindings = receiver_core.InterfaceBindings.value
                if len(bindings) > MAX_LEGS:
                    raise InvalidParameter("too many legs in binding")
                binding_idx = 0
                for leg in self.legs:
                    if leg.enable and binding_idx < len(bindings):
                        if bindings[binding_idx] != leg.name:
                            raise InvalidParameter(
                                f"invalid binding, expecting {leg.name}, "
                                f"found {bindings[binding_idx]}"
                            )
                        binding_idx += 1
                if len(bindings) != binding_idx:
                    raise InvalidParameter("invalid binding, missing legs")

            # Initialize transport activation
            transport_enum = receiver_core.Transport.value

            from nmos.node.activation import (
                get_transport_descriptor,
                init_receiver_activation,
            )
            try:
                descriptor = get_transport_descriptor(transport_enum)
            except KeyError as exc:
                raise InvalidParameter(f"unsupported receiver transport: {transport_enum}") from exc

            if (descriptor.receiver_params_type is None
                    or descriptor.receiver_constraints_type is None
                    or descriptor.receiver_activation_type is None):
                raise InvalidParameter(f"transport does not support receiver activation: {transport_enum}")

            activation = Activation(
                receiver_index=receiver_index,
                enabled_legs=sum(1 for leg in self.legs if leg.enable),
                staged=[descriptor.receiver_params_type() for _ in range(MAX_LEGS)],
                active=[descriptor.receiver_params_type() for _ in range(MAX_LEGS)],
                constraints=[descriptor.receiver_constraints_type() for _ in range(MAX_LEGS)],
                staged_state=descriptor.receiver_activation_type(),
                active_state=descriptor.receiver_activation_type(),
            )
            if privacy_keys is not None:
                activation.privacy_keys = privacy_keys
            elif self.privacy_enabled:
                # Default PSK: KeyId={0..7}, PSK={0..15}
                activation.privacy_keys = PrivacyPreSharedKeys(keys=[
                    PreSharedKey(
                        key_id=bytes(range(8)),
                        psk=bytes(range(16)),
                    ),
                ])

            # Generate privacy parameters
            if self.privacy_enabled:
                from nmos.node.privacy import generate_receiver_privacy_parameters
                generate_receiver_privacy_parameters(activation.privacy)

            init_receiver_activation(
                activation,
                self.legs,
                transport_enum,
                _extract_receiver_format(receiver),
                descriptor,
                privacy_enabled=self.privacy_enabled,
                group_hint=_extract_group_hint(receiver_core),
            )
            self.receiver_activation.put(static_id, activation)

            # Add receiver to device's receivers list
            if self.device_value is not None:
                receivers_list = list(self.device_value.Receivers.value)
                receivers_list.append(receiver_id)
                self.device_value.Receivers.value = receivers_list
                _set_version_now(self.device_value.ResourceCore)

            # Validate receiver before committing
            try:
                inner = receiver
                if hasattr(receiver, 'get') and callable(receiver.get):
                    got = receiver.get()
                    if got is not None:
                        inner = got
                if hasattr(inner, 'assert_valid'):
                    inner.assert_valid()
            except Exception as exc:
                raise InvalidParameter(f"invalid receiver: {exc}") from exc

            self.receivers.put(static_id, receiver)

            # Create monitor source for this receiver
            monitor_static = self._add_monitor_source(
                is_sender=False,
                resource_index=receiver_index,
                parent_uuid=uuid,
            )
            if monitor_static is not None:
                monitor_src = self.sources.get(monitor_static)
                if monitor_src is not None:
                    receiver_core.Monitor._value.set(monitor_src)
                    receiver_core.Monitor._defined = True

            return static_id

        except Exception:
            self.receiver_indices.put_index(receiver_index)
            raise

    def update_receiver(self, receiver_id: str, update: ReceiverUpdate) -> None:
        """Update a receiver's properties. NO UUID change."""
        receiver = self.receivers.get_or_raise(receiver_id)
        receiver_core = _get_receiver_core(receiver)

        for name, value in iter_set_fields(update):
            if name == "subscription_active":
                if receiver_core.Subscription.defined:
                    receiver_core.Subscription.value.Active.value = value
            elif name == "subscription_sender_id":
                if receiver_core.Subscription.defined:
                    receiver_core.Subscription.value.SenderId.value = value
            else:
                raise InvalidParameter(f"unknown receiver update field: {name}")

        _set_version_now(_get_resource_core(receiver))

    def del_receiver(self, receiver_id: str) -> None:
        """Delete a receiver.

        Validates, removes from device list,
        releases natural group index, cleans up activation and monitor.
        """
        static_id = to_static_id(receiver_id)
        receiver = self.receivers.get(static_id)
        if receiver is None:
            raise NotFound(f"receiver not found: {receiver_id}")

        rc = _get_resource_core(receiver)
        dynamic_id = rc.Id.value

        # Release index
        uuid = ResourceUuid()
        uuid.set_from_string(dynamic_id)
        self.receiver_indices.put_index(uuid.index)

        # Release natural group role index (PutRoleIndex(groupIndex, format, roleIndex))
        receiver_core = _get_receiver_core(receiver)
        format_enum = _extract_receiver_format(receiver)
        if receiver_core.NaturalGroupIndex.defined and format_enum is not None:
            ng = receiver_core.NaturalGroupIndex.value
            if hasattr(receiver_core, 'NaturalGroupRoleIndex') and \
               receiver_core.NaturalGroupRoleIndex.defined:
                role_idx = receiver_core.NaturalGroupRoleIndex.value
                self.receiver_natural_groups.put_role_index(
                    ng, format_enum, role_idx
                )

        # Remove from device's receivers list
        if self.device_value is not None:
            current = list(self.device_value.Receivers.value)
            self.device_value.Receivers.value = [
                rid for rid in current if rid != dynamic_id
            ]
            _set_version_now(self.device_value.ResourceCore)

        # Remove from stores
        self.receivers.remove(static_id)
        self.receiver_activation.remove(static_id)

    # ===================================================================
    # Source (creation order #2)
    # ===================================================================

    def add_source(self, source: Any) -> str:
        """Add a new source. Returns static source ID.

        Links source to device, registers in receiver's source list
        if applicable, sets up parent-child relationships.
        """
        self._check_initialized()
        source_core = _get_source_core(source)
        rc = source_core.ResourceCore

        if rc.Id.defined and rc.Id.value != "":
            raise InvalidParameter("source cannot have pre-defined id")

        source_index = self.source_indices.get_index()
        try:
            # Sources are IMMUTABLE (Atomic State Changes spec) — always use
            # random unique_id so each source instance gets a unique ID.
            # Only Senders/Receivers use persistent IDs (they are mutable).
            unique_id = _random_unique_id()
            uuid = ResourceUuid()
            uuid.set(
                ResourceType.SOURCE, ResourceSubType.NONE,
                source_index, self.serial_number, unique_id, False,
            )
            source_id = str(uuid)
            static_id = to_static_id(source_id)

            rc.Id.value = source_id
            rc.StaticId.value = static_id
            _set_version_now(rc)

            # Link to device
            if self.device_value is not None:
                source_core.DeviceId.value = self.device_value.ResourceCore.Id.value

            # Register source in its receiver's Sources map
            if source_core.ReceiverId.defined:
                recv_id = source_core.ReceiverId.value
                if recv_id is not None:
                    recv = self.receivers.get(recv_id)
                    if recv is not None:
                        recv_core = _get_receiver_core(recv)
                        sources_map = recv_core.Sources._value._inner
                        sources_map[source] = source

            # Register in parent sources' Children maps
            if source_core.Parents.defined:
                for parent_id in source_core.Parents.value:
                    parent = self.sources.get(parent_id)
                    if parent is not None:
                        parent_sc = _get_source_core(parent)
                        children_map = parent_sc.Children._value._inner
                        children_map[source] = source

            # Validate source before committing
            try:
                inner = source
                if hasattr(source, 'get') and callable(source.get):
                    got = source.get()
                    if got is not None:
                        inner = got
                if hasattr(inner, 'assert_valid'):
                    inner.assert_valid()
            except Exception as exc:
                raise InvalidParameter(f"invalid source: {exc}") from exc

            self.sources.put(static_id, source)
            return static_id

        except Exception:
            self.source_indices.put_index(source_index)
            raise

    def update_source(self, source_id: str, update: SourceUpdate) -> str:
        """Update a source and cascade UUID change to linked flows/children.

        Returns the NEW dynamic source ID.
        """
        static_id = to_static_id(source_id)
        source = self.sources.get_or_raise(static_id)
        source_core = _get_source_core(source)
        rc = source_core.ResourceCore

        # Apply mutations
        for name, value in iter_set_fields(update):
            if name == "receiver_id":
                source_core.ReceiverId.value = value
            elif name == "grain_rate":
                source_core.GrainRate.set_value(value)
            elif name == "channels":
                pass  # Audio-specific: inner.Channels
            elif name == "synchronous_media":
                source_core.SynchronousMedia.value = value
            elif name == "clock_name":
                source_core.ClockName.value = value
            elif name in ("monitor_sender_info", "monitor_receiver_info"):
                pass  # Monitor-specific: handled by caller
            else:
                raise InvalidParameter(f"unknown source update field: {name}")

        # Generate new dynamic ID
        old_dynamic_id = rc.Id.value
        new_unique_id = _random_unique_id()
        new_dynamic_id = update_resource_unique_id(static_id, new_unique_id)

        self.garbage_sources.append(GarbageResource(id=old_dynamic_id))

        rc.Id.value = new_dynamic_id
        _set_version_now(rc)

        # CASCADE: Update all flows referencing this source
        if source_core.Flows.defined:
            for flow_ptr in source_core.Flows._value._inner.values():
                flow_core = _get_flow_core(flow_ptr)
                flow_core.SourceId.value = new_dynamic_id
                _set_version_now(flow_core.ResourceCore)

        # CASCADE: Update child sources' Parents lists
        if source_core.Children.defined:
            for child_ptr in source_core.Children._value._inner.values():
                child_sc = _get_source_core(child_ptr)
                if child_sc.Parents.defined:
                    new_parents = [
                        new_dynamic_id if p == old_dynamic_id else p
                        for p in child_sc.Parents.value
                    ]
                    child_sc.Parents.value = new_parents
                    _set_version_now(child_sc.ResourceCore)

        return new_dynamic_id

    def update_source_monitor_sender(
        self, source: Any, info: Any,
    ) -> None:
        """Apply sender monitor info to a monitor source.

        Sets monitor state fields on the NSourceData inner value.
        """
        from nmos.node.updates import MonitorSenderInfo
        assert isinstance(info, MonitorSenderInfo)
        self._apply_monitor_state(source, info, is_sender=True)

    def update_source_monitor_receiver(
        self, source: Any, info: Any,
    ) -> None:
        """Apply receiver monitor info to a monitor source.

        Sets monitor state fields on the NSourceData inner value.
        """
        from nmos.node.updates import MonitorReceiverInfo
        assert isinstance(info, MonitorReceiverInfo)
        self._apply_monitor_state(source, info, is_sender=False)

    def _apply_monitor_state(
        self, source: Any, info: Any, *, is_sender: bool,
    ) -> None:
        """Apply monitor status/counters to an NSourceData monitor source."""
        # Unwrap polymorphic source to get NSourceData inner
        inner = source
        if hasattr(source, 'get') and callable(source.get):
            got = source.get()
            if got is not None:
                inner = got

        type_name = type(inner).__name__
        if "SourceData" not in type_name:
            raise InvalidParameter(f"expected NSourceData, got {type_name}")

        # Set auto-reset and reporting delay (CANNOT CHANGE = 3)
        inner.MonitorAutoResetCounters.value = info.auto_reset
        inner.MonitorStatusReportingDelay.value = 3

        # Build NMonitorStateValue
        from nmos.types.generated.nmonitor_state import NMonitorStateValue
        state = NMonitorStateValue()
        state.set_to_default()

        state.MonitorOverallStatus.value = info.overall_status
        state.MonitorOverallStatusMessage.value = info.overall_status_message
        state.MonitorLinkStatus.value = info.link_status
        state.MonitorSynchronizationStatus.value = info.synchronization_status

        if is_sender:
            state.MonitorTransmissionStatus.value = info.transmission_status
            state.MonitorEssenceStatus.value = info.essence_status
            state.MonitorLinkStatusCounter.value = info.link_counter
            state.MonitorTransmissionStatusCounter.value = info.transmission_counter
            state.MonitorSynchronizationStatusCounter.value = info.synchronization_counter
            state.MonitorEssenceStatusCounter.value = info.essence_counter
        else:
            state.MonitorConnectionStatus.value = info.connection_status
            state.MonitorStreamStatus.value = info.stream_status
            state.MonitorLinkStatusCounter.value = info.link_counter
            state.MonitorConnectionStatusCounter.value = info.connection_counter
            state.MonitorSynchronizationStatusCounter.value = info.synchronization_counter
            state.MonitorStreamStatusCounter.value = info.stream_counter

        inner.MonitorState.set_value(state)

        # Bump version
        source_core = _get_source_core(source)
        _set_version_now(source_core.ResourceCore)

        # Publish to registry
        self.publish()

    # ===================================================================
    # Flow (creation order #3)
    # ===================================================================

    def add_flow(self, flow: Any) -> str:
        """Add a new flow. Returns static flow ID.

        Links flow to device and source. Registers flow in source's flow list.
        """
        self._check_initialized()
        flow_core = _get_flow_core(flow)
        rc = flow_core.ResourceCore

        if rc.Id.defined and rc.Id.value != "":
            raise InvalidParameter("flow cannot have pre-defined id")

        flow_index = self.flow_indices.get_index()
        try:
            unique_id = _random_unique_id()
            uuid = ResourceUuid()
            uuid.set(
                ResourceType.FLOW, ResourceSubType.NONE,
                flow_index, self.serial_number, unique_id, False,
            )
            flow_id = str(uuid)
            static_id = to_static_id(flow_id)

            rc.Id.value = flow_id
            rc.StaticId.value = static_id
            _set_version_now(rc)

            # Link to device
            if self.device_value is not None:
                flow_core.DeviceId.value = self.device_value.ResourceCore.Id.value

            # Register flow in its source's Flows map
            if flow_core.SourceId.defined:
                src = self.sources.get(flow_core.SourceId.value)
                if src is not None:
                    src_core = _get_source_core(src)
                    flows_map = src_core.Flows._value._inner
                    flows_map[flow] = flow

            # Register in parent flows' Children maps
            if flow_core.Parents.defined:
                for parent_id in flow_core.Parents.value:
                    parent = self.flows.get(parent_id)
                    if parent is not None:
                        parent_fc = _get_flow_core(parent)
                        children_map = parent_fc.Children._value._inner
                        children_map[flow] = flow

            # Validate flow before committing
            try:
                inner = flow
                if hasattr(flow, 'get') and callable(flow.get):
                    got = flow.get()
                    if got is not None:
                        inner = got
                if hasattr(inner, 'assert_valid'):
                    inner.assert_valid()
            except Exception as exc:
                raise InvalidParameter(f"invalid flow: {exc}") from exc

            self.flows.put(static_id, flow)
            return static_id

        except Exception:
            self.flow_indices.put_index(flow_index)
            raise

    def update_flow(self, flow_id: str, update: FlowUpdate) -> str:
        """Update a flow and cascade UUID change to linked senders/children.

        Returns the NEW dynamic flow ID.
        """
        static_id = to_static_id(flow_id)
        flow = self.flows.get_or_raise(static_id)
        flow_core = _get_flow_core(flow)
        rc = flow_core.ResourceCore

        # Apply mutations
        poly = flow.get() if hasattr(flow, 'get') else flow
        fv = poly.value if hasattr(poly, 'value') else poly

        for name, value in iter_set_fields(update):
            if name == "grain_rate":
                flow_core.GrainRate.set_value(value)

            elif name == "layers":
                # Layers: videoLayers, audioLayers, dataLayers, mediaType
                from nmos.node.updates import LayerParams
                lp: LayerParams = value
                fv.VideoLayers.value = lp.video_layers
                fv.AudioLayers.value = lp.audio_layers
                fv.DataLayers.value = lp.data_layers
                if lp.media_type is not UNSET:
                    fv.MediaType.value = lp.media_type

            elif name == "video":
                # Video: width, height, colorspace, tc, im, components
                from nmos.node.updates import VideoParams
                vp: VideoParams = value
                if vp.frame_width:
                    fv.FrameWidth.value = vp.frame_width
                if vp.frame_height:
                    fv.FrameHeight.value = vp.frame_height
                if vp.colorspace is not UNSET:
                    fv.Colorspace.value = vp.colorspace
                if vp.transfer_characteristic is not UNSET:
                    fv.TransferCharacteristic.value = vp.transfer_characteristic
                if vp.interlace_mode is not UNSET:
                    fv.InterlaceMode.value = vp.interlace_mode
                if vp.components is not UNSET:
                    fv.Components.value = vp.components

            elif name == "video_codec":
                # Video codec: Jxsv / H264 / H265
                from nmos.node.updates import VideoCodecParams
                vc: VideoCodecParams = value
                if vc.profile is not UNSET:
                    fv.Profile.value = vc.profile
                if vc.level is not UNSET:
                    fv.Level.value = vc.level
                if vc.sublevel is not UNSET and hasattr(fv, 'Sublevel'):
                    fv.Sublevel.value = vc.sublevel
                if vc.bitrate:
                    fv.Bitrate.value = vc.bitrate
                fv.ConstantBitrate.value = vc.cbr

            elif name == "audio":
                # Audio: bitDepth
                from nmos.node.updates import AudioParams
                ap: AudioParams = value
                if ap.bit_depth:
                    fv.BitDepth.value = ap.bit_depth

            elif name == "audio_codec":
                # Audio codec: Am824 / Aac / etc.
                from nmos.node.updates import AudioCodecParams, AudioCodec
                ac: AudioCodecParams = value
                if ac.codec == AudioCodec.AM824:
                    # AM824 has no profile/level/bitrate — zero them out
                    fv.Profile.set_to_zero()
                    fv.Level.set_to_zero()
                    fv.Bitrate.set_to_zero()
                    fv.ConstantBitrate.set_to_zero()
                else:
                    if ac.profile is not UNSET:
                        fv.Profile.value = ac.profile
                    if ac.level is not UNSET:
                        fv.Level.value = ac.level
                    if ac.bitrate:
                        fv.Bitrate.value = ac.bitrate
                    fv.ConstantBitrate.value = ac.cbr

            elif name in ("raw_flavor", "coded_flavor"):
                pass  # Flavor flags — no direct flow mutation needed

            else:
                raise InvalidParameter(f"unknown flow update field: {name}")

        # Generate new dynamic ID
        old_dynamic_id = rc.Id.value
        new_unique_id = _random_unique_id()
        new_dynamic_id = update_resource_unique_id(static_id, new_unique_id)

        self.garbage_flows.append(GarbageResource(id=old_dynamic_id))

        rc.Id.value = new_dynamic_id
        _set_version_now(rc)

        # CASCADE: Update all senders referencing this flow
        if flow_core.Senders.defined:
            for sender_ptr in flow_core.Senders._value._inner.values():
                sender_ptr.FlowId.value = new_dynamic_id
                _set_version_now(sender_ptr.ResourceCore)

        # CASCADE: Update child flows' Parents lists
        if flow_core.Children.defined:
            for child_ptr in flow_core.Children._value._inner.values():
                child_fc = _get_flow_core(child_ptr)
                if child_fc.Parents.defined:
                    new_parents = [
                        new_dynamic_id if p == old_dynamic_id else p
                        for p in child_fc.Parents.value
                    ]
                    child_fc.Parents.value = new_parents
                    _set_version_now(child_fc.ResourceCore)

        return new_dynamic_id

    # ===================================================================
    # Sender (creation order #4)
    # ===================================================================

    def add_sender(
        self,
        sender: Any,
        privacy_keys: PrivacyPreSharedKeys | None = None,
    ) -> str:
        """Add a new sender. Returns static sender ID.

        Validates format/transport, allocates index, generates UUID,
        links to device and flow, sets up natural group hint.
        """
        self._check_initialized()

        if sender.ResourceCore.Id.defined and sender.ResourceCore.Id.value != "":
            raise InvalidParameter("sender cannot have pre-defined id")

        # Validate format is defined
        if not sender.Format.defined:
            raise InvalidParameter("sender not having an associated format")

        # Validate transport is defined
        if not sender.Transport.defined:
            raise InvalidParameter("sender not having an associated transport")

        # Validate flow format matches sender format (if flow is linked)
        if sender.FlowId.defined:
            flow_id_val = sender.FlowId.value
            if flow_id_val is not None:
                linked_flow = self.flows.get(flow_id_val)
                if linked_flow is not None:
                    # Format is on the inner polymorphic type, not FlowCore
                    flow_inner = linked_flow
                    if hasattr(linked_flow, 'get') and callable(linked_flow.get):
                        got = linked_flow.get()
                        if got is not None:
                            flow_inner = got
                    if hasattr(flow_inner, 'Format') and flow_inner.Format.defined:
                        if sender.Format.defined:
                            if flow_inner.Format.value is not sender.Format.value:
                                raise InvalidParameter(
                                    "sender format does not match flow format"
                                )

        sender_index = self.sender_indices.get_index()
        try:
            unique_id = 0 if HAS_PERSISTENT_SENDER_RECEIVER_ID else _random_unique_id()
            uuid = ResourceUuid()
            uuid.set(
                ResourceType.SENDER, ResourceSubType.NONE,
                sender_index, self.serial_number, unique_id, False,
            )
            sender_id = str(uuid)
            static_id = to_static_id(sender_id)

            sender.ResourceCore.Id.value = sender_id
            sender.ResourceCore.StaticId.value = static_id
            _set_version_now(sender.ResourceCore)
            # Bump node version
            if self.node_value is not None:
                _set_version_now(self.node_value.ResourceCore)

            # Natural group assignment
            if sender.Format.defined and sender.Transport.defined:
                role_index = self._assign_natural_group(
                    sender,
                    is_sender=True,
                    format_enum=sender.Format.value,
                    transport_enum=sender.Transport.value,
                )
                # Deferred cleanup: if add fails, release the role index
                if role_index is not None and sender.NaturalGroupIndex.defined:
                    _allocated_role = (
                        sender.NaturalGroupIndex.value,
                        sender.Format.value,
                        role_index,
                    )

            # Link to device and add sender ID to device's senders list
            if self.device_value is not None:
                device_id = self.device_value.ResourceCore.Id.value
                sender.DeviceId.value = device_id

            # Default interface bindings from enabled legs
            if not sender.InterfaceBindings.defined or len(sender.InterfaceBindings.value) == 0:
                bindings = [leg.name for leg in self.legs if leg.enable]
                sender.InterfaceBindings.value = bindings
            else:
                # Validate specified bindings match leg ordering
                bindings = sender.InterfaceBindings.value
                if len(bindings) > MAX_LEGS:
                    raise InvalidParameter("too many legs in binding")
                binding_idx = 0
                for leg in self.legs:
                    if leg.enable and binding_idx < len(bindings):
                        if bindings[binding_idx] != leg.name:
                            raise InvalidParameter(
                                f"invalid binding, expecting {leg.name}, "
                                f"found {bindings[binding_idx]}"
                            )
                        binding_idx += 1
                if len(bindings) != binding_idx:
                    raise InvalidParameter("invalid binding, missing legs")

            # Add optional format attributes
            self._add_sender_optional_format_attributes(sender)

            # Register sender in its flow's Senders map
            if sender.FlowId.defined:
                flow_id = sender.FlowId.value
                if flow_id is not None:
                    flow = self.flows.get(flow_id)
                    if flow is not None:
                        flow_core = _get_flow_core(flow)
                        senders_map = flow_core.Senders._value._inner
                        senders_map[sender] = sender

            # Initialize transport activation
            transport_enum = sender.Transport.value

            from nmos.node.activation import (
                get_transport_descriptor,
                init_sender_activation,
            )
            try:
                descriptor = get_transport_descriptor(transport_enum)
            except KeyError as exc:
                raise InvalidParameter(f"unsupported sender transport: {transport_enum}") from exc

            activation = Activation(
                sender_index=sender_index,
                enabled_legs=sum(1 for leg in self.legs if leg.enable),
                staged=[descriptor.sender_params_type() for _ in range(MAX_LEGS)],
                active=[descriptor.sender_params_type() for _ in range(MAX_LEGS)],
                constraints=[descriptor.sender_constraints_type() for _ in range(MAX_LEGS)],
                staged_state=descriptor.sender_activation_type(),
                active_state=descriptor.sender_activation_type(),
                sender_name=self.serial_number,
            )
            if privacy_keys is not None:
                activation.privacy_keys = privacy_keys
            elif self.privacy_enabled:
                # Default PSK: KeyId={0..7}, PSK={0..15}
                activation.privacy_keys = PrivacyPreSharedKeys(keys=[
                    PreSharedKey(
                        key_id=bytes(range(8)),
                        psk=bytes(range(16)),
                    ),
                ])

            # Generate privacy parameters
            if self.privacy_enabled:
                from nmos.node.privacy import generate_sender_privacy_parameters
                generate_sender_privacy_parameters(
                    activation.privacy, self.sender_activation._items,
                )

            init_sender_activation(
                activation,
                self.legs,
                transport_enum,
                descriptor,
                privacy_enabled=self.privacy_enabled,
                group_hint=_extract_group_hint(sender),
            )
            self.sender_activation.put(static_id, activation)

            # Set manifest_href to IS-05 transport file URL
            if self.node_value is not None:
                ep = self.node_value.Api.value.Endpoints
                if ep.defined and len(ep.value) > 0:
                    endpoint = ep.value[0]
                    scheme = str(endpoint.Protocol.value) if endpoint.Protocol.defined else Http.s
                    ep_host = endpoint.Host.value if endpoint.Host.defined else "127.0.0.1"
                    ep_port = endpoint.Port.value if endpoint.Port.defined else 5050
                    sender.ManifestHref.value = (
                        f"{scheme}://{ep_host}:{ep_port}"
                        f"/x-nmos/connection/v1.1/single/senders/{sender_id}/transportfile"
                    )

            # Add sender to device's senders list
            if self.device_value is not None:
                senders_list = list(self.device_value.Senders.value)
                senders_list.append(sender_id)
                self.device_value.Senders.value = senders_list
                _set_version_now(self.device_value.ResourceCore)

            # Validate sender before committing
            try:
                sender.assert_valid()
            except Exception as exc:
                raise InvalidParameter(f"invalid sender: {exc}") from exc

            self.senders.put(static_id, sender)

            # Generate initial SDP transport file
            # Store as parsed MatroxSdp object (cached parsed SDP)
            sdp_text = _generate_sdp_from_params(self, sender, sender_id, activation)
            if sdp_text is not None:
                self._store_parsed_sdp(static_id, sdp_text)

            # Create monitor source for this sender
            monitor_static = self._add_monitor_source(
                is_sender=True,
                resource_index=sender_index,
                parent_uuid=uuid,
            )
            if monitor_static is not None:
                monitor_src = self.sources.get(monitor_static)
                if monitor_src is not None and hasattr(sender, 'Monitor'):
                    sender.Monitor._value.set(monitor_src)
                    sender.Monitor._defined = True

            return static_id

        except Exception:
            self.sender_indices.put_index(sender_index)
            raise

    def update_sender(self, sender_id: str, update: SenderUpdate) -> None:
        """Update a sender's properties. NO UUID change, NO cascade."""
        sender = self.senders.get_or_raise(sender_id)

        for name, value in iter_set_fields(update):
            if name == "flow_id":
                sender.FlowId.value = value
            elif name == "subscription_active":
                if sender.Subscription.defined:
                    sender.Subscription.value.Active.value = value
            elif name == "subscription_receiver_id":
                if sender.Subscription.defined:
                    sender.Subscription.value.ReceiverId.value = value
            else:
                raise InvalidParameter(f"unknown sender update field: {name}")

        _set_version_now(sender.ResourceCore)

    def del_sender(self, sender_id: str) -> None:
        """Delete a sender.

        Validates, removes from flow's senders map,
        removes from device list, releases natural group, cleans up activation.
        """
        static_id = to_static_id(sender_id)
        sender = self.senders.get(static_id)
        if sender is None:
            raise NotFound(f"sender not found: {sender_id}")

        dynamic_id = sender.ResourceCore.Id.value

        # Release index
        uuid = ResourceUuid()
        uuid.set_from_string(dynamic_id)
        self.sender_indices.put_index(uuid.index)

        # Release natural group role index (PutRoleIndex(groupIndex, format, roleIndex))
        if sender.NaturalGroupIndex.defined and sender.Format.defined:
            ng = sender.NaturalGroupIndex.value
            if hasattr(sender, 'NaturalGroupRoleIndex') and sender.NaturalGroupRoleIndex.defined:
                role_idx = sender.NaturalGroupRoleIndex.value
                self.sender_natural_groups.put_role_index(
                    ng, sender.Format.value, role_idx
                )

        # Remove sender from flow's Senders map
        if sender.FlowId.defined:
            flow_id = sender.FlowId.value
            if flow_id is not None:
                flow_static = to_static_id(flow_id)
                flow = self.flows.get(flow_static)
                if flow is not None:
                    flow_core = _get_flow_core(flow)
                    if flow_core.Senders.defined:
                        smap = flow_core.Senders._value._inner
                        # Remove sender ptr from map by matching value
                        to_remove = [k for k, v in smap.items()
                                     if v is not None and hasattr(v, '_inner')
                                     and v._inner is sender]
                        for k in to_remove:
                            del smap[k]

        # Remove from device's senders list
        if self.device_value is not None:
            current = list(self.device_value.Senders.value)
            self.device_value.Senders.value = [
                sid for sid in current if sid != dynamic_id
            ]
            _set_version_now(self.device_value.ResourceCore)

        # Remove from stores
        self.senders.remove(static_id)
        self.sender_activation.remove(static_id)
        self.constraints.remove(static_id)
        self.sdp.remove(static_id)

    # ===================================================================
    # Publish
    # ===================================================================

    def publish(self) -> None:
        """Create deep-cloned snapshot of all resources for registry consumers."""
        self.publish_manager.publish(
            receivers=dict(self.receivers),
            sources=dict(self.sources),
            flows=dict(self.flows),
            senders=dict(self.senders),
        )

    def get_publish_event(self) -> asyncio.Event:
        return self.publish_manager.event

    def get_items_to_publish(self) -> PublishState:
        return self.publish_manager.get_items()

    def check_tracker(self, resource_id: str, version: Any) -> bool:
        return self.publish_manager.check_tracker(resource_id, version)

    # ===================================================================
    # Natural groups
    # ===================================================================

    def get_sender_natural_group_description(self, index: int) -> str:
        return self.sender_natural_groups.get_description(index)

    def set_sender_natural_group_name(self, index: int, name: str) -> None:
        self.sender_natural_groups.set_name(index, name)

    def get_receiver_natural_group_description(self, index: int) -> str:
        return self.receiver_natural_groups.get_description(index)

    def set_receiver_natural_group_name(self, index: int, name: str) -> None:
        self.receiver_natural_groups.set_name(index, name)

    # ===================================================================
    # OAuth2
    # ===================================================================

    def set_oauth2_public_keys(self, keys: Any) -> None:
        """Store the JWKS the Node uses to validate inbound bearer tokens.

        Accepts three shapes:

        * a parsed :class:`nmos.oauth2.JWKS` dataclass — stored as-is;
        * a JSON-serialised JWKS dict (e.g. the body of Keycloak's
          ``/realms/<realm>/protocol/openid-connect/certs`` response) —
          parsed into a ``JWKS`` via :func:`_parse_jwks` before storage;
        * ``None`` — clears any previously-stored keys.

        Why the tolerance: the bearer-validation middleware iterates
        ``jwks.keys`` expecting a ``list[JSONWebKey]``. Storing a raw
        dict would route through ``dict.keys`` (the bound method),
        crashing every authenticated request with
        ``TypeError: 'builtin_function_or_method' object is not iterable``.
        Coercing here means production callers (which read JSON over
        HTTP) and test callers (which build the dataclass directly)
        both work.
        """
        if keys is None:
            self.oauth2_keys = None
            return
        from nmos.oauth2 import JWKS, _parse_jwks
        if isinstance(keys, JWKS):
            self.oauth2_keys = keys
            return
        if isinstance(keys, dict):
            self.oauth2_keys = _parse_jwks(keys)
            return
        # Unknown shape — pass through untouched. The middleware will
        # raise on access; better that than silently swallow.
        self.oauth2_keys = keys

    # ===================================================================
    # Getters (convenience wrappers around ResourceStore)
    # ===================================================================

    def get_receiver(self, receiver_id: str) -> Any | None:
        return self.receivers.get(receiver_id)

    def get_source(self, source_id: str) -> Any | None:
        return self.sources.get(source_id)

    def get_flow(self, flow_id: str) -> Any | None:
        return self.flows.get(flow_id)

    def get_sender(self, sender_id: str) -> Any | None:
        return self.senders.get(sender_id)

    def get_sender_activation(self, sender_id: str) -> Activation | None:
        return self.sender_activation.get(sender_id)

    def get_receiver_activation(self, receiver_id: str) -> Activation | None:
        return self.receiver_activation.get(receiver_id)

    # ===================================================================
    # Internal helpers
    # ===================================================================

    # ===================================================================
    # IS-11 Stream Compatibility Management
    # ===================================================================

    def _constraints_to_ccf(self, constraints: Any) -> Any:
        """Convert NSenderActiveConstraints (or constraint_sets list) to CCF Caps.

        Encodes constraints to JSON, parses via CCF convert_caps_json_to_caps(),
        then immediately converts to Cons via .to_cons().
        Active constraints are semantically constraints (Cons), not capabilities (Caps).
        Returns CCF Cons object, or None if conversion fails.
        """
        try:
            from caps.MatroxCCF import convert_caps_json_to_caps
            import json as _json
        except ImportError:
            return None

        if constraints is None:
            return None

        from nmos.json.engine import JsonEngine as _JE

        data = None

        # Case 1: has ConstraintSets attribute (NSenderActiveConstraintsValue)
        if hasattr(constraints, 'ConstraintSets') and hasattr(constraints.ConstraintSets, 'encode'):
            engine = _JE()
            engine.reset()
            constraints.ConstraintSets.encode(engine, None)
            cs_list = _json.loads(engine.get_output())
            if isinstance(cs_list, list):
                data = {"constraint_sets": cs_list}

        # Case 2: has encode() directly (wrapper type)
        elif hasattr(constraints, 'encode'):
            engine = _JE()
            engine.reset()
            constraints.encode(engine, None)
            data = _json.loads(engine.get_output())

        # Case 3: plain dict
        elif isinstance(constraints, dict):
            data = constraints

        # Case 4: plain list
        elif isinstance(constraints, list):
            data = {"constraint_sets": constraints}

        if data is None:
            return None

        if isinstance(data, dict) and "constraint_sets" in data:
            caps = convert_caps_json_to_caps(data)
            return caps.to_cons() if caps else None
        elif isinstance(data, list):
            caps = convert_caps_json_to_caps({"constraint_sets": data})
            return caps.to_cons() if caps else None
        return None

    def validate_active_constraints(
        self, sender: Any, constraints: Any,
    ) -> tuple[Any, Any | None]:
        """Validate active constraints against sender capabilities.

        Uses CCF normalize + inclusion checking.

        Returns (constraints, error) — error is None if valid.
        """
        from nmos.node.compatibility import validate_active_constraints as _validate
        sender_id = sender.ResourceCore.Id.value if hasattr(sender, 'ResourceCore') else ""
        active_cons = self._constraints_to_ccf(constraints)
        normalized_cons, err = _validate(self, sender_id, active_cons, verbose=True)
        if err is not None:
            return constraints, InvalidParameter(f"constraints violate sender capabilities: {err}")
        return constraints, None

    def check_active_constraints(
        self, sender: Any, active_constraints: Any,
    ) -> tuple[Any, Any, Any | None]:
        """Validate, normalize, and merge constraints against capabilities.

        Returns (normalized_cons, merged_caps, error).
        - normalized_cons: CCF Cons = active constraints + auto-generated defaults for missing layers
        - merged_caps: CCF Caps = sender capabilities constricted by constraints
        - error: NotAllowed if constraints don't fit any capability set
        """
        from nmos.node.compatibility import (
            validate_active_constraints as _validate,
            force_active_constraints as _force,
        )
        from nmos.node.store import to_static_id

        sender_id = sender.ResourceCore.Id.value if hasattr(sender, 'ResourceCore') else ""
        static_id = to_static_id(sender_id)
        active_cons = self._constraints_to_ccf(active_constraints)

        # Step 1: Validate and normalize (includes mux layer validation)
        normalized_cons, err = _validate(self, sender_id, active_cons, verbose=True)
        if err is not None:
            return None, None, NotAllowed(f"active constraints not compliant: {err}")

        # Step 2: Merge = constrict sender caps (Caps) by normalized constraints (Cons)
        sender_caps = self.sender_ccf_caps.get(static_id)
        merged_caps = _force(self, sender_id, normalized_cons, verbose=True) if sender_caps else None

        return normalized_cons, merged_caps, None

    def set_sender_compatibility_state(self, sender: Any) -> str:
        """Compute and set IS-11 sender compatibility status.

        Checks flow against sender caps/constraints, sets CompatibilityStatus on sender.

        Returns: "unconstrained", "constrained", or "active_constraints_violation".

        On a transition edge between violation
        and non-violation, emit a vendor-essence event on the Node's
        event queue so the BCP-008 monitor source's ``essence_status``
        flips to ``NC_UNHEALTHY`` / ``NC_HEALTHY``. Transition-only
        (not every-call) because the status-monitor's 3-second
        "worse" hysteresis resets its ``activation_time`` on every
        same-state event, which would keep postponing the UNHEALTHY
        publish. See ``status_monitor.process_one_domain``.
        """
        from nmos.node.compatibility import check_sender_flow_compatibility
        from nmos.enums import EnumRegistry

        sender_id = sender.ResourceCore.Id.value if hasattr(sender, 'ResourceCore') else ""

        # Snapshot the previous state BEFORE recomputation so the
        # transition check below compares pre/post correctly. If the
        # sender has never had a state computed we treat it as "not
        # violated" — the first transition to violation still fires,
        # but the first compute-to-healthy is silent (matches the
        # "back to healthy" wording in the user's brief).
        prev_was_violated = False
        if (hasattr(sender, 'CompatibilityStatus')
                and sender.CompatibilityStatus.defined):
            prev_was_violated = sender.CompatibilityStatus.value is (
                EnumRegistry.get(ActiveConstraintsViolation.s)
            )

        status = check_sender_flow_compatibility(self, sender_id, verbose=True)

        if status == "compatible":
            result = Constrained.s
        elif status == "incompatible":
            result = ActiveConstraintsViolation.s
        else:
            result = Unconstrained.s

        if hasattr(sender, 'CompatibilityStatus'):
            sender.CompatibilityStatus.value = EnumRegistry.get(result)

        self._emit_is11_transition_if_needed(
            sender_id, is_sender=True,
            prev_was_violated=prev_was_violated,
            new_result=result,
            violation_states=(ActiveConstraintsViolation.s,),
            healthy_states=(Constrained.s, Unconstrained.s),
            role="sender",
        )

        return result

    def _emit_is11_transition_if_needed(
        self, resource_id: Any, *, is_sender: bool,
        prev_was_violated: bool, new_result: str,
        violation_states: tuple[str, ...],
        healthy_states: tuple[str, ...],
        role: str,
    ) -> None:
        """Emit the IS-11 compatibility transition edge (sender or
        receiver) on the Node's ``event_queue`` when the state just
        crossed the violation / non-violation line.

        Called from both ``set_sender_compatibility_state`` and
        ``set_receiver_compatibility_state`` — the only differences
        between the two paths are the allowed state-name tuples and
        the ``is_sender`` flag (which picks ``AlertScope.SENDER`` or
        ``AlertScope.RECEIVER`` in the emitter).

        No-op if the Node was constructed without an event queue
        (e.g. some unit tests) — debug instrumentation must never
        bubble into the control path.
        """
        from nmos.node.events import emit_is11_compatibility_event

        queue = getattr(self, "event_queue", None)
        if queue is None:
            return
        resource_id_str = str(resource_id) if resource_id is not None else ""
        if not resource_id_str:
            return

        if new_result in violation_states and not prev_was_violated:
            emit_is11_compatibility_event(
                queue, resource_id_str, is_sender=is_sender, violated=True,
                info=(
                    f"active constraints violation on {role} "
                    f"{resource_id_str}"
                ) if is_sender else (
                    f"non-compliant stream on {role} {resource_id_str}"
                ),
            )
        elif new_result in healthy_states and prev_was_violated:
            emit_is11_compatibility_event(
                queue, resource_id_str, is_sender=is_sender, violated=False,
                info=(
                    f"{role} constraints satisfied on {role} "
                    f"{resource_id_str}"
                ) if is_sender else (
                    f"stream compliant on {role} {resource_id_str}"
                ),
            )

    def check_sender_flow_compatibility(self, sender: Any) -> Any:
        """Check if sender's flow is compatible with its capabilities.

        Returns None if compatible, raises NotAllowed if not.
        """
        from nmos.node.compatibility import check_sender_flow_compatibility
        sender_id = sender.ResourceCore.Id.value if hasattr(sender, 'ResourceCore') else ""
        status = check_sender_flow_compatibility(self, sender_id, verbose=True)
        if status == "incompatible":
            return NotAllowed("sender flow not compatible with capabilities")
        return None

    def force_active_constraints(
        self, sender: Any, constraints: Any,
    ) -> Any:
        """Apply active constraints to sender: store on sender, update flow.

        - If constraints is None: DELETE (reset to unconstrained)
        - Otherwise: store constraints, force flow update
        """
        from nmos.node.compatibility import update_sender_to_compliant_flow
        from nmos.node.store import to_static_id

        sender_id = sender.ResourceCore.Id.value if hasattr(sender, 'ResourceCore') else ""
        static_id = to_static_id(sender_id)

        if constraints is None:
            # DELETE: reset to defaults (SetToDefault on all constraint fields)
            if hasattr(sender, 'Constraints') and hasattr(sender.Constraints, 'set_to_default'):
                sender.Constraints.set_to_default()
            self.sender_ccf_normalized.pop(static_id, None)
            self.sender_ccf_merged.pop(static_id, None)
            if hasattr(sender, 'CompatibilityStatus'):
                from nmos.enums import EnumRegistry
                sender.CompatibilityStatus.value = EnumRegistry.get(Unconstrained.s)
            return None

        # Store raw NMOS Constraints on sender (for IS-04 encoding)
        if hasattr(sender, 'Constraints') and hasattr(constraints, 'clone'):
            sender.Constraints.set_value(constraints.clone() if hasattr(constraints, 'clone') else constraints)

        # Validate, normalize, and merge using check_active_constraints
        normalized_cons, merged_caps, err = self.check_active_constraints(sender, constraints)
        if err is not None:
            return err

        # Cache CCF versions:
        # - normalized_cons (Cons): active constraints + defaults for unconstrained layers.
        #   Used for flow compatibility checks. Empty consets mean "unconstrained".
        # - merged_caps (Caps): sender caps constricted by constraints.
        #   Used for updateSenderToCompliantFlow to narrow the flow to compliant values.
        if normalized_cons is not None:
            self.sender_ccf_normalized[static_id] = normalized_cons
        if merged_caps is not None:
            self.sender_ccf_merged[static_id] = merged_caps

        # --- Force flow update ---
        # Step 1: Force main flow (trunk, layer=-1, reset=True)
        # Pass activeConstraints (normalized), NOT merged caps.
        # normalized_cons preserves the original=True flags on constraints,
        # which fix-up functions use to decide whether to override values.
        if normalized_cons is not None:
            if not update_sender_to_compliant_flow(self, sender_id, normalized_cons, layer=-1, reset=True, verbose=True):
                return NotAllowed("cannot force main flow to comply with constraints")

        # Step 2: Re-verify main flow
        # "Redo the verification because of possible properties that cannot be changed"
        from nmos.node.compatibility import (
            check_flow_properties_compatibility,
            force_flow_properties_compatibility,
            update_flow_to_compliant,
        )
        from nmos.node.flow_caps import get_flow_to_caps

        flow_id = sender.FlowId.value if hasattr(sender, 'FlowId') and sender.FlowId.defined and sender.FlowId.value else None
        if flow_id is None:
            return None  # No associated flow

        flow_ptr = self.flows.get(flow_id)
        if flow_ptr is None:
            return None

        if normalized_cons is not None:
            flow_caps = get_flow_to_caps(self, flow_ptr)
            if not check_flow_properties_compatibility(self, flow_caps, normalized_cons, verbose=True):
                return NotAllowed("main flow not compatible after forcing")

        # Step 3: Mux sub-flow forcing loop
        from nmos.enums import FormatMux, FormatVideo, FormatAudio, FormatData
        format_urn = sender.Format.value.s if hasattr(sender, 'Format') and sender.Format.defined else ""
        if format_urn == FormatMux.s and normalized_cons is not None:
            from nmos.types.generated.nflow_mux import NFlowMux, NFlowMuxValue
            from nmos.types.generated.nflow_video_raw import NFlowVideoRawValue
            from nmos.types.generated.nflow_video_coded import NFlowVideoCodedValue
            from nmos.types.generated.nflow_audio_raw import NFlowAudioRawValue
            from nmos.types.generated.nflow_audio_coded import NFlowAudioCodedValue

            poly = flow_ptr.get() if hasattr(flow_ptr, 'get') else flow_ptr
            if poly is not None:
                fv = poly.value if hasattr(poly, 'value') else poly
                fc = fv.FlowCore if hasattr(fv, 'FlowCore') else None
                if fc is not None and fc.Parents.defined:
                    parents = fc.Parents.value or []

                    check_layers: dict[str, int] = {
                        FormatVideo.s: -1,
                        FormatAudio.s: -1,
                        FormatData.s: -1,
                    }

                    for parent_flow_id in parents:
                        parent_ptr = self.flows.get(parent_flow_id)
                        assert parent_ptr is not None, f"missing parent flow {parent_flow_id}"

                        parent_poly = parent_ptr.get() if hasattr(parent_ptr, 'get') else parent_ptr
                        assert parent_poly is not None, f"parent flow {parent_flow_id} has no value"

                        # Reject circular mux
                        if isinstance(parent_poly, (NFlowMux, NFlowMuxValue)):
                            return NotAllowed("mux cannot have parent mux")

                        parent_fv = parent_poly.value if hasattr(parent_poly, 'value') else parent_poly

                        # Determine format
                        if isinstance(parent_poly, (NFlowVideoRawValue, NFlowVideoCodedValue)):
                            fmt = FormatVideo.s
                        elif isinstance(parent_poly, (NFlowAudioRawValue, NFlowAudioCodedValue)):
                            fmt = FormatAudio.s
                        else:
                            fmt = FormatData.s

                        # Validate sequential layers
                        parent_fc = parent_fv.FlowCore if hasattr(parent_fv, 'FlowCore') else None
                        assert parent_fc is not None and parent_fc.Layer.defined, f"parent flow {parent_flow_id} missing Layer"
                        layer = parent_fc.Layer.value
                        assert layer >= 0, f"parent flow {parent_flow_id} has invalid Layer={layer}"
                        assert check_layers.get(fmt, -1) + 1 == layer, \
                            f"non-sequential layer: {fmt} expected={check_layers.get(fmt, -1) + 1} got={layer}"
                        check_layers[fmt] = layer

                        # Force sub-flow (updateFlowToCompliantFlow)
                        compliant, compliant_groups = force_flow_properties_compatibility(
                            self, parent_ptr, normalized_cons,
                            layer=layer, format_urn=fmt, reset=True, verbose=True,
                        )
                        if compliant is not None:
                            update_flow_to_compliant(self, parent_ptr, compliant, compliant_groups, verbose=True)
                        else:
                            return NotAllowed(f"cannot force sub-flow layer={layer} format={fmt}")

                        # Re-verify sub-flow
                        parent_caps = get_flow_to_caps(self, parent_ptr)
                        if not check_flow_properties_compatibility(
                            self, parent_caps, normalized_cons, layer=layer, format_urn=fmt, verbose=True,
                        ):
                            return NotAllowed(f"sub-flow layer={layer} format={fmt} not compatible after forcing")

        # --- Atomic State Changes: UUID cascade ---
        # Each update*Flow call propagates a new UUID and cascades to
        # senders/children.
        # We cascade the main sender flow + its source here (after all
        # mutations are complete).  Sub-flow cascades are handled by the
        # main flow's update_flow() which propagates to children's Parents.
        if flow_ptr is not None:
            from nmos.node.updates import SourceUpdate, FlowUpdate

            poly = flow_ptr.get() if hasattr(flow_ptr, 'get') else flow_ptr
            fv = poly.value if hasattr(poly, 'value') else poly
            fc = fv.FlowCore if hasattr(fv, 'FlowCore') else None

            if fc is not None:
                # 1. Cascade source UUID
                source_id = fc.SourceId.value if fc.SourceId.defined else None
                if source_id:
                    try:
                        new_src_id = self.update_source(str(source_id), SourceUpdate())
                        fc.SourceId.value = new_src_id
                    except Exception:
                        pass

                # 2. Cascade flow UUID (updates sender FlowId + child Parents)
                flow_dynamic_id = fc.ResourceCore.Id.value if fc.ResourceCore.Id.defined else None
                if flow_dynamic_id:
                    try:
                        self.update_flow(str(flow_dynamic_id), FlowUpdate())
                    except Exception:
                        pass

        return None

    def set_receiver_compatibility_state(self, receiver: Any) -> str:
        """Compute and set IS-11 receiver compatibility status.

        Checks stream (SDP) against receiver capabilities, sets CompatibilityStatus.

        Returns: "unknown", "compliant_stream", or "non_compliant_stream".

        On a transition edge between
        ``non_compliant_stream`` and ``compliant_stream``/``unknown``,
        emit a vendor-essence event on the Node's event queue so the
        BCP-008 receiver monitor's ``stream_status`` flips to
        ``NC_UNHEALTHY`` / ``NC_HEALTHY``. Same transition-only
        rationale as ``set_sender_compatibility_state`` — see that
        method for the detailed hysteresis-interaction notes.
        ``unknown`` is treated as the healthy/neutral state — the
        Node uses it where IS-11 would otherwise return
        ``awaiting_stream`` (no stream yet to evaluate).
        """
        from nmos.node.compatibility import check_stream_compatibility
        from nmos.enums import EnumRegistry

        receiver_id = ""
        core = None
        try:
            inner = receiver.get() if hasattr(receiver, 'get') else receiver
            rv = inner.value if hasattr(inner, 'value') else inner
            core = getattr(rv, 'ReceiverCore', rv)
            # Id lives on the embedded ResourceCore, not on ReceiverCore
            # directly. A previous version read ``core.Id`` which
            # silently returned "" — the id was never surfaced to
            # callers (tests, transitional event emitters).
            rc = getattr(core, 'ResourceCore', None)
            if rc is not None and rc.Id.defined:
                receiver_id = rc.Id.value
        except Exception:
            pass

        # Snapshot previous state BEFORE recomputation — see sender
        # counterpart for the transition-only rationale.
        prev_was_noncompliant = False
        if (core is not None
                and hasattr(core, 'CompatibilityStatus')
                and core.CompatibilityStatus.defined):
            prev_was_noncompliant = core.CompatibilityStatus.value is (
                EnumRegistry.get(NonCompliantStream.s)
            )

        status = check_stream_compatibility(self, receiver_id, verbose=True)

        if status == "compliant":
            result = CompliantStream.s
        elif status == "non_compliant":
            result = NonCompliantStream.s
        else:
            result = Unknown.s

        try:
            if core is not None:
                core.CompatibilityStatus.value = EnumRegistry.get(result)
        except Exception:
            pass

        self._emit_is11_transition_if_needed(
            receiver_id, is_sender=False,
            prev_was_violated=prev_was_noncompliant,
            new_result=result,
            violation_states=(NonCompliantStream.s,),
            healthy_states=(CompliantStream.s, Unknown.s),
            role="receiver",
        )

        return result

    def check_stream_compatibility(self, receiver: Any) -> Any:
        """Check if receiver's stream is compatible with its capabilities.

        Returns None if compatible, raises NotAllowed if not.
        """
        from nmos.node.compatibility import check_stream_compatibility
        receiver_id = ""
        try:
            inner = receiver.get() if hasattr(receiver, 'get') else receiver
            rv = inner.value if hasattr(inner, 'value') else inner
            core = getattr(rv, 'ReceiverCore', rv)
            receiver_id = core.Id.value if hasattr(core, 'Id') and core.Id.defined else ""
        except Exception:
            pass

        status = check_stream_compatibility(self, receiver_id)
        if status == "non_compliant":
            return NotAllowed("stream not compatible with receiver capabilities")
        return None

    # ===================================================================
    # Internal helpers
    # ===================================================================

    def _store_parsed_sdp(self, static_id: str, sdp_text: str) -> None:
        """Parse SDP text and store the parsed MatroxSdp object.

        Caches parsed SDP transport files in ``self.sdp`` for downstream
        consumers. Errors during parsing are discovered here (fail-fast)
        rather than deferred to compatibility checking time.
        """
        from sdp.MatroxSdp import MatroxSdp
        sdp_obj = MatroxSdp()
        err = sdp_obj.decode(sdp_text)
        if err is not None:
            raise UnexpectedError(f"SDP parse failed: {err}")
        self.sdp.put(static_id, sdp_obj)

    def _check_initialized(self) -> None:
        if not self._initialized:
            raise InvalidOperation("node not initialized — call init() first")

    def _count_enabled_legs(self) -> int:
        return sum(1 for leg in self.legs if leg.enable)

    @staticmethod
    def _resolve_interface_name(host: str) -> str:
        """Resolve the network interface name for the given host address.

        Falls back to 'eth0' if the interface cannot be determined.
        """
        try:
            from nmos.netifaces_compat import find_interface_name_for_address

            iface = find_interface_name_for_address(host)
            if iface is not None:
                return iface
        except ImportError:
            pass
        # Fallback: try psutil or just return eth0
        try:
            import psutil
            for iface, addrs in psutil.net_if_addrs().items():
                for addr in addrs:
                    if addr.address == host:
                        return iface
        except ImportError:
            pass
        return "eth0"

    # --- Monitor source creation ---

    def _lookup_clock_by_name(self, name: str) -> Any:
        """Find a ``node.clocks[]`` entry by its ``name`` (``"clk0"``
        etc). Returns the inner clock value object (NClockPtpValue /
        NClockInternalValue) or ``None`` if not found.
        """
        try:
            if self.node_value is None or not self.node_value.Clocks.defined:
                return None
            for clock_val in self.node_value.Clocks._value._inner:
                wrapper = (
                    clock_val._inner if hasattr(clock_val, "_inner") else None
                )
                if wrapper is None:
                    continue
                inner = wrapper._value if hasattr(wrapper, "_value") else None
                if inner is None:
                    continue
                if (hasattr(inner, "Name") and inner.Name.defined
                        and inner.Name.value == name):
                    return inner
        except (AttributeError, TypeError):
            pass
        return None

    def _clock_is_locked_ptp(self, clock_inner: Any) -> bool:
        """True when ``clock_inner`` is a PTP clock whose ``locked``
        flag is True. Internal clocks (no PTP ``Locked`` field) return
        False — they're local oscillators, not a *synchronization*
        source in the BCP-008 sense; their sync status is ``NotUsed``.
        """
        if clock_inner is None:
            return False
        if not hasattr(clock_inner, "Locked"):
            return False  # internal clock → no external sync
        try:
            return (
                clock_inner.Locked.defined
                and clock_inner.Locked.value is True
            )
        except AttributeError:
            return False

    def _sender_sync_seed(self, parent_uuid: Any) -> int:
        """Return the initial ``MonitorSynchronizationStatus`` for a
        newly-created sender monitor source.

        Walks sender → flow → source → ``clock_name`` and looks up
        the named clock in ``node.clocks``. If that specific clock is
        a PTP clock with ``Locked=True`` the sender's sync seed is
        ``NC_HEALTHY`` (green); otherwise ``NC_NOT_USED`` (grey).

        This mirrors the link facet's "seed from physical reality"
        pattern — sync is a property of the reference clock, not of
        whether the sender is currently transmitting.
        """
        from nmos.node.status_monitor import NC_HEALTHY, NC_NOT_USED
        try:
            sender_ptr = self.senders.get(str(parent_uuid))
            if sender_ptr is None:
                return NC_NOT_USED
            sender = sender_ptr.get() if hasattr(sender_ptr, "get") else sender_ptr
            if sender is None or not sender.FlowId.defined:
                return NC_NOT_USED
            flow_ptr = self.flows.get(sender.FlowId.value)
            if flow_ptr is None:
                return NC_NOT_USED
            flow_core = _get_flow_core(flow_ptr)
            if flow_core is None or not flow_core.SourceId.defined:
                return NC_NOT_USED
            source_ptr = self.sources.get(flow_core.SourceId.value)
            if source_ptr is None:
                return NC_NOT_USED
            source_core = _get_source_core(source_ptr)
            if source_core is None or not source_core.ClockName.defined:
                return NC_NOT_USED
            clock_name = source_core.ClockName.value
            if clock_name is None:
                return NC_NOT_USED
            clock = self._lookup_clock_by_name(str(clock_name))
            return NC_HEALTHY if self._clock_is_locked_ptp(clock) else NC_NOT_USED
        except (AttributeError, TypeError, KeyError):
            return NC_NOT_USED

    def _receiver_sync_seed(self) -> int:
        """Return the initial ``MonitorSynchronizationStatus`` for a
        newly-created receiver monitor source.

        Unlike a sender, a receiver has no pre-configured clock
        reference — it locks to the sender's clock at activation
        time. So at monitor-init the most honest signal is the
        Node's own reference-clock state: if the Node publishes any
        locked PTP clock the receiver WILL be able to synchronise
        when a stream arrives (green); otherwise there's no external
        sync source available and NotUsed is correct (grey).
        """
        from nmos.node.status_monitor import NC_HEALTHY, NC_NOT_USED
        try:
            if self.node_value is None or not self.node_value.Clocks.defined:
                return NC_NOT_USED
            for clock_val in self.node_value.Clocks._value._inner:
                wrapper = (
                    clock_val._inner if hasattr(clock_val, "_inner") else None
                )
                if wrapper is None:
                    continue
                inner = wrapper._value if hasattr(wrapper, "_value") else None
                if self._clock_is_locked_ptp(inner):
                    return NC_HEALTHY
        except (AttributeError, TypeError):
            pass
        return NC_NOT_USED

    def _add_monitor_source(
        self, is_sender: bool, resource_index: int, parent_uuid: ResourceUuid,
    ) -> str | None:
        """Create a monitor source for a sender or receiver.

        Monitor sources are
        NSourceData resources with SENDER_MONITOR or RECEIVER_MONITOR
        subtype that store monitoring/debug info about the associated
        sender or receiver.

        Returns the static source ID, or None if creation fails.
        """
        try:
            from nmos.types.generated.nsource_data import NSourceDataValue
            from nmos.types.generated.nsource import NSourceValue
            from nmos.enums import EnumRegistry

            format_data = EnumRegistry.get(FormatData.s)

            inner = NSourceDataValue()
            inner.SourceCore.set_to_default()
            inner.Format.value = format_data
            inner.SourceCore.ResourceCore.Label.value = "Source Monitor"
            inner.MonitorType.value = "sender" if is_sender else "receiver"
            inner.MonitorSiblingId.value = str(parent_uuid)
            inner.MonitorAutoResetCounters.value = True
            inner.MonitorStatusReportingDelay.value = 3  # BCP-008: fixed at 3 seconds

            # Initialize monitor_state. Link & sync are network / clock
            # facts independent of whether the resource is currently
            # active, so they're seeded from Node-level state at
            # monitor creation time — NOT from activation events. The
            # conn / essence facets DO follow activation and stay
            # NC_INACTIVE until the streaming engine fires START /
            # STOP events for this monitor.
            from nmos.node.status_monitor import NC_INACTIVE, NC_HEALTHY
            from nmos.types.generated.nmonitor_state import NMonitorStateValue
            state = NMonitorStateValue()
            state.MonitorOverallStatus.value = NC_INACTIVE
            state.MonitorOverallStatusMessage.value = ""
            state.MonitorLinkStatus.value = NC_HEALTHY  # Link starts as AllUp
            # Sync seed is per-resource: senders walk
            # sender→flow→source→clock_name and check that specific
            # clock's lock state; receivers have no pre-configured
            # clock reference so they check whether the Node
            # advertises *any* locked PTP clock they could lock to
            # at activation. Either way the seed reflects physical
            # reality — HEALTHY when a usable PTP reference exists,
            # NC_NOT_USED (the sync-facet zero value) otherwise.
            state.MonitorSynchronizationStatus.value = (
                self._sender_sync_seed(parent_uuid) if is_sender
                else self._receiver_sync_seed()
            )
            if is_sender:
                state.MonitorTransmissionStatus.value = NC_INACTIVE
                state.MonitorEssenceStatus.value = NC_INACTIVE
                state.MonitorTransmissionStatusCounter.value = 0
                state.MonitorEssenceStatusCounter.value = 0
            else:
                state.MonitorConnectionStatus.value = NC_INACTIVE
                state.MonitorStreamStatus.value = NC_INACTIVE
                state.MonitorConnectionStatusCounter.value = 0
                state.MonitorStreamStatusCounter.value = 0
            state.MonitorLinkStatusCounter.value = 0
            state.MonitorSynchronizationStatusCounter.value = 0
            inner.MonitorState.set_value(state)

            source = NSourceValue()
            source.set(inner)

            # Use the same index as the parent sender/receiver
            source_index = resource_index

            unique_id = 0 if HAS_PERSISTENT_SENDER_RECEIVER_ID else _random_unique_id()
            sub_type = (
                ResourceSubType.SENDER_MONITOR if is_sender
                else ResourceSubType.RECEIVER_MONITOR
            )
            uuid = ResourceUuid()
            uuid.set(
                ResourceType.SOURCE, sub_type,
                source_index, self.serial_number, unique_id, False,
            )
            source_id = str(uuid)
            static_id = to_static_id(source_id)

            rc = inner.SourceCore.ResourceCore
            rc.Id.value = source_id
            rc.StaticId.value = static_id
            _set_version_now(rc)

            # Link to device
            if self.device_value is not None:
                inner.SourceCore.DeviceId.value = (
                    self.device_value.ResourceCore.Id.value
                )

            self.sources.put(static_id, source)
            return static_id

        except ImportError:
            return None

    # --- Optional format attributes ---

    def _add_sender_optional_format_attributes(self, sender: Any) -> None:
        """Set optional sender attributes based on flow media type.

        Sets Bitrate, PacketTransmissionMode, SenderType, ParameterSetsTransportMode,
        ParameterSetsFlowMode based on the linked flow's codec type.
        """
        from nmos.enums import EnumRegistry

        # Clear all optional attributes first
        for attr in ("Bitrate", "PacketTransmissionMode", "SenderType",
                     "ParameterSetsTransportMode", "ParameterSetsFlowMode"):
            if hasattr(sender, attr):
                field = getattr(sender, attr)
                if hasattr(field, 'set_to_zero'):
                    field.set_to_zero()

        # Get linked flow
        if not sender.FlowId.defined or sender.FlowId.value is None:
            return
        flow = self.flows.get(sender.FlowId.value)
        if flow is None:
            return

        # Determine flow type from its inner value
        inner = flow
        if hasattr(flow, 'get') and callable(flow.get):
            got = flow.get()
            if got is not None:
                inner = got

        type_name = type(inner).__name__

        def _set_if_exists(name: str, val: Any) -> None:
            if hasattr(sender, name):
                getattr(sender, name).value = val

        def _get_transport_bitrate(bitrate: int) -> int:
            """Apply 8% overhead for transport bitrate."""
            return int(bitrate * 108 / 100)

        if "VideoCoded" in type_name:
            media_type = str(inner.MediaType.value) if inner.MediaType.defined else ""
            bitrate = inner.Bitrate.value if inner.Bitrate.defined else 0

            if media_type == VideoCodedJxsv.s:
                _set_if_exists("Bitrate", _get_transport_bitrate(bitrate))
                _set_if_exists("PacketTransmissionMode", EnumRegistry.get(CodeStream.s))
                if self.ipmx or not self.rfc:
                    _set_if_exists("SenderType", EnumRegistry.get(SenderType2110TPW.s))
            elif media_type in (VideoCodedH264.s, VideoCodedH265.s):
                _set_if_exists("Bitrate", _get_transport_bitrate(bitrate))
                _set_if_exists("PacketTransmissionMode",
                               EnumRegistry.get(NonInterleavedNalUnits.s))
                _set_if_exists("ParameterSetsTransportMode",
                               EnumRegistry.get(InAndOutOfBand.s))
                _set_if_exists("ParameterSetsFlowMode",
                               EnumRegistry.get(Strict.s))
                if self.ipmx or not self.rfc:
                    _set_if_exists("SenderType", EnumRegistry.get(SenderType2110TPW.s))

        elif "AudioCoded" in type_name:
            media_type = str(inner.MediaType.value) if inner.MediaType.defined else ""
            bitrate = inner.Bitrate.value if inner.Bitrate.defined else 0

            if media_type == AudioCodedAm824.s:
                pass  # No optional attributes for AM824
            elif media_type == AudioCodedAac.s:
                _set_if_exists("Bitrate", _get_transport_bitrate(bitrate))
                _set_if_exists("PacketTransmissionMode",
                               EnumRegistry.get(NonInterleavedAccessUnits.s))
                _set_if_exists("ParameterSetsTransportMode",
                               EnumRegistry.get(OutOfBand.s))
                _set_if_exists("ParameterSetsFlowMode",
                               EnumRegistry.get(Strict.s))
            elif media_type in (AudioCodedAacLATM.s, AudioCodedAacADTS.s):
                _set_if_exists("Bitrate", _get_transport_bitrate(bitrate))
                _set_if_exists("PacketTransmissionMode",
                               EnumRegistry.get(NonInterleavedAccessUnits.s))
                _set_if_exists("ParameterSetsTransportMode",
                               EnumRegistry.get(InAndOutOfBand.s))
                _set_if_exists("ParameterSetsFlowMode",
                               EnumRegistry.get(Strict.s))

    # --- Natural group assignment ---

    def _assign_natural_group(
        self,
        resource: Any,
        is_sender: bool,
        format_enum: Any,
        transport_enum: Any,
    ) -> int | None:
        """Assign a natural group hint to a sender or receiver.

        Reads NaturalGroupIndex from the resource, allocates a role index,
        sets the group hint tag and NaturalGroupRoleIndex.

        Returns the allocated role_index, or None if no group assigned.
        """
        groups = (
            self.sender_natural_groups if is_sender
            else self.receiver_natural_groups
        )

        # For senders: resource is NSenderValue (flat)
        # For receivers: resource is NReceiverValue (polymorphic)
        if is_sender:
            group_idx_field = resource.NaturalGroupIndex
            role_idx_field = resource.NaturalGroupRoleIndex
            tags_field = resource.ResourceCore.Tags
        else:
            recv_core = _get_receiver_core(resource)
            group_idx_field = recv_core.NaturalGroupIndex
            role_idx_field = recv_core.NaturalGroupRoleIndex
            tags_field = recv_core.ResourceCore.Tags

        if not group_idx_field.defined:
            return None

        group_index = group_idx_field.value
        if not isinstance(group_index, int):
            return None

        try:
            hint, role_index = groups.get_group_hint(
                group_index, format_enum, transport_enum,
            )
        except (ValueError, KeyError, AttributeError):
            return None

        role_idx_field.value = role_index

        # Set the group hint tag
        tag_dict: dict[str, list[str]]
        if tags_field.defined:
            tag_dict = tags_field.value
        else:
            tag_dict = {}

        from nmos.enums import EnumRegistry
        tag_key = EnumRegistry.get(TagGroupHint.s)
        if tag_key is not None:
            tag_dict[str(tag_key)] = [hint]
        tags_field.value = tag_dict

        return role_index
