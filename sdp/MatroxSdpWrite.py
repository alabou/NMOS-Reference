# Copyright (C) 2025 Matrox Graphics Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""SDP transport file encoder for MatroxSdp.

Translates the Go Sdp.Encode / Sdp.Emit* methods into Python, operating
on the same MatroxSdp / MediaDescriptor objects used by the decoder.
The structure, ordering and conditional logic mirror the Go implementation
so that changes can be ported between the two languages with minimal diff.
"""

from __future__ import annotations

from io import StringIO
from typing import Optional

try:
    from .MatroxSdp import (
        MatroxSdp,
        MatroxSdpEnums,
        MediaDescriptor,
        SdpError,
        ExtmapDescriptor,
        HkepDescriptor,
        PrivacyDescriptor,
        EnumId,
        MAX_HKEPS,
        MAX_EXTMAPS,
    )
except ImportError:
    from MatroxSdp import (
        MatroxSdp,
        MatroxSdpEnums,
        MediaDescriptor,
        SdpError,
        ExtmapDescriptor,
        HkepDescriptor,
        PrivacyDescriptor,
        EnumId,
        MAX_HKEPS,
        MAX_EXTMAPS,
    )


# ---------------------------------------------------------------------------
# Encode entry point
# ---------------------------------------------------------------------------

def encode(sdp: MatroxSdp) -> str:
    """Encode a MatroxSdp object into an SDP transport file string.

    Mirrors Go ``Sdp.Encode``.  Returns the SDP text on success or
    raises ``SdpError`` on validation / encoding failure.
    """
    # Check primary and secondary media pointers
    if sdp.has_group_attribute:

        sdp.media_count = 2

        if sdp.primary_media is not sdp.medias[0]:
            raise SdpError("invalid primary media")

        if sdp.primary_media.media_name != sdp.primary_media_name:
            raise SdpError("invalid primary media name")

        if sdp.secondary_media is not sdp.medias[1]:
            raise SdpError("invalid secondary media")

        if sdp.secondary_media.media_name != sdp.secondary_media_name:
            raise SdpError("invalid secondary media name")

        if sdp.primary_media_name == sdp.secondary_media_name:
            raise SdpError("invalid group media names")

        if sdp.primary_media is sdp.secondary_media:
            raise SdpError("invalid group")

    else:

        sdp.media_count = 1

        if sdp.primary_media_name != sdp.primary_media.media_name:
            raise SdpError("invalid primary media names")

        if sdp.primary_media is not sdp.medias[0]:
            raise SdpError("invalid primary media")

    err = sdp.check_sdp_base_requirements()
    if err:
        raise SdpError(err)

    out = StringIO()
    _emit_lines(sdp, out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Emit helpers
# ---------------------------------------------------------------------------

def _emit_lines(sdp: MatroxSdp, out: StringIO) -> None:
    """Mirrors Go ``Sdp.EmitLines``."""
    _emit_version(sdp, out)
    _emit_origin(sdp, out)
    _emit_session_name(sdp, out)
    _emit_information(sdp, out, None)       # session
    _emit_connection(sdp, out, None)        # session
    _emit_bitrate(sdp, out, None)           # session
    _emit_timing(sdp, out)
    _emit_attribute(sdp, out, None)         # session
    _emit_media(sdp, out, sdp.primary_media)
    if sdp.has_group_attribute:
        _emit_media(sdp, out, sdp.secondary_media)


def _emit_version(sdp: MatroxSdp, out: StringIO) -> None:
    out.write("v=0\r\n")


def _emit_origin(sdp: MatroxSdp, out: StringIO) -> None:
    addr_type = "IP6" if sdp.is_origin_ipv6 else "IP4"
    out.write(f"o={sdp.username} {sdp.session_id} {sdp.session_version} IN {addr_type} {sdp.origin_address}\r\n")


def _emit_session_name(sdp: MatroxSdp, out: StringIO) -> None:
    if sdp.session_name == "":
        out.write("s=-\r\n")
    else:
        out.write(f"s={sdp.session_name}\r\n")


def _emit_session_control(sdp: MatroxSdp, out: StringIO) -> None:
    if sdp.session_control:
        out.write(f"a=control:{sdp.session_control}\r\n")


def _emit_information(sdp: MatroxSdp, out: StringIO, m: Optional[MediaDescriptor]) -> None:
    if m is not None:
        info = m.media_information
    else:
        info = sdp.session_information
    if info:
        out.write(f"i={info}\r\n")


def _emit_sub_stream_control(sdp: MatroxSdp, out: StringIO, m: MediaDescriptor) -> None:
    if m.sub_stream_control:
        out.write(f"a=control:{m.sub_stream_control}\r\n")


def _emit_connection(sdp: MatroxSdp, out: StringIO, m: Optional[MediaDescriptor]) -> None:

    if m is not None:
        address = m.connection_address
        count = m.connection_count
        is_ipv6 = m.is_connection_ipv6
        ttl = m.connection_ttl
        protocol = m.protocol
    else:
        address = sdp.connection_address
        count = sdp.connection_count
        is_ipv6 = sdp.is_connection_ipv6
        ttl = sdp.connection_ttl
        protocol = None

    if not address:
        return

    if count > 1:
        raise SdpError("multiple connection addresses is not supported")

    if is_ipv6:
        if ttl != 0:
            raise SdpError("IPv6 does not support TTL")
        out.write(f"c=IN IP6 {address}\r\n")
    else:
        if ttl == 0:
            ttl = 128
        if protocol is not None and protocol.s in ("TCP", "TCP/RTP/AVP"):
            out.write(f"c=IN IP4 {address}\r\n")
        else:
            out.write(f"c=IN IP4 {address}/{ttl}\r\n")


def _emit_bitrate(sdp: MatroxSdp, out: StringIO, m: Optional[MediaDescriptor]) -> None:
    bits = m.bitrate_kbits if m is not None else sdp.bitrate_kbits
    if bits:
        out.write(f"b=AS:{bits}\r\n")


def _emit_timing(sdp: MatroxSdp, out: StringIO) -> None:
    out.write(f"t={sdp.start} {sdp.stop}\r\n")


# ---------------------------------------------------------------------------
# Attribute emitters
# ---------------------------------------------------------------------------

def _emit_attribute(sdp: MatroxSdp, out: StringIO, m: Optional[MediaDescriptor]) -> None:
    if m is not None:
        _emit_media_attribute(sdp, out, m)
    else:
        _emit_session_attribute(sdp, out)


def _emit_session_attribute(sdp: MatroxSdp, out: StringIO) -> None:

    if sdp.has_group_attribute:
        out.write(f"a=group:DUP {sdp.primary_media_name} {sdp.secondary_media_name}\r\n")

    if sdp.ts_ref_clock_source is not None:
        _emit_ts_ref_clock(sdp, out, None)

    if sdp.media_clock_type is not None:
        _emit_media_clock(sdp, out, None)

    if sdp.session_control:
        _emit_session_control(sdp, out)

    _emit_ext_map(sdp, out, None)
    _emit_hkep(sdp, out, None)


def _emit_media_attribute(sdp: MatroxSdp, out: StringIO, m: MediaDescriptor) -> None:

    if sdp.has_group_attribute:
        out.write(f"a=mid:{m.media_name}\r\n")

    E = MatroxSdpEnums

    # RTP map for video and audio when payload type is used
    if m.type == E.Video:

        if m.payload_type != 0:

            if m.payload_type != m.format_code:
                raise SdpError("invalid payload-type")

            if m.encoding_name is None or m.clock_rate == 0:
                raise SdpError("invalid encoding name or clock rate")

            out.write(f"a=rtpmap:{m.payload_type} {m.encoding_name}/{m.clock_rate}\r\n")

        _emit_video_fmtp(sdp, out, m)

        if m.frame_rate_numerator != 0:
            if m.frame_rate_denominator > 1:
                out.write(f"a=framerate:{m.frame_rate_numerator / m.frame_rate_denominator:f}\r\n")
            else:
                out.write(f"a=framerate:{m.frame_rate_numerator}\r\n")

    elif m.type == E.Audio:

        if m.payload_type != 0:

            if m.payload_type != m.format_code:
                raise SdpError("invalid payload-type")

            if m.encoding_name is None or m.sample_rate == 0 or m.channels == 0:
                raise SdpError("invalid encoding name, sample rate or number of channels")

            out.write(f"a=rtpmap:{m.payload_type} {m.encoding_name}/{m.sample_rate}/{m.channels}\r\n")

        _emit_audio_fmtp(sdp, out, m)

        if m.p_time_us != 0:
            if m.p_time_us % 1000 != 0:
                out.write(f"a=ptime:{m.p_time_us / 1000:.3f}\r\n")
            else:
                out.write(f"a=ptime:{m.p_time_us // 1000}\r\n")

        if m.max_p_time_us != 0:
            if m.max_p_time_us % 1000 != 0:
                out.write(f"a=maxptime:{m.max_p_time_us / 1000:.3f}\r\n")
            else:
                out.write(f"a=maxptime:{m.max_p_time_us // 1000}\r\n")

        if m.frame_count != 0:
            out.write(f"a=framecount:{m.frame_count}\r\n")

    if m.ts_ref_clock_source is not None:
        _emit_ts_ref_clock(sdp, out, m)

    if m.media_clock_type is not None:
        _emit_media_clock(sdp, out, m)

    # Multicast source filters
    if m.source_filter_src_address or m.source_filter_dst_address:

        if m.is_source_filter_ipv6 != m.is_connection_ipv6:
            raise SdpError("multicast source filter address type not matching with connection")

        addr_type = "IP6" if m.is_source_filter_ipv6 else "IP4"
        out.write(f"a=source-filter: incl IN {addr_type} {m.source_filter_dst_address} {m.source_filter_src_address}\r\n")

    if m.sub_stream_control:
        _emit_sub_stream_control(sdp, out, m)

    _emit_ext_map(sdp, out, m)
    _emit_hkep(sdp, out, m)
    _emit_privacy(sdp, out, m)

    # For TCP protocol we must indicate that we are passive
    if m.protocol is not None and m.protocol.s == "TCP":
        out.write("a=setup:passive\r\n")

    # RTCP port
    if m.rtcp_port != 0:
        if not m.rtcp_connection_address:
            out.write(f"a=rtcp:{m.rtcp_port}\r\n")
        else:
            if m.rtcp_connection_count > 1:
                raise SdpError("multiple connection addresses is not supported")

            if m.rtcp_is_connection_ipv6:
                if m.rtcp_connection_ttl != 0:
                    raise SdpError("IPv6 does not support TTL")
                out.write(f"a=rtcp:{m.rtcp_port} IN IP6 {m.rtcp_connection_address}\r\n")
            else:
                ttl = m.rtcp_connection_ttl if m.rtcp_connection_ttl != 0 else 128
                out.write(f"a=rtcp:{m.rtcp_port} IP4 {m.rtcp_connection_address}/{ttl}\r\n")


# ---------------------------------------------------------------------------
# ts-refclk / mediaclk
# ---------------------------------------------------------------------------

def _emit_ts_ref_clock(sdp: MatroxSdp, out: StringIO, m: Optional[MediaDescriptor]) -> None:

    E = MatroxSdpEnums

    if m is not None:
        source = m.ts_ref_clock_source
        local_mac = m.ts_ref_clock_local_mac_address
        ntp_addr = m.ts_ref_clock_ntp_address
        ptp_ver = m.ts_ref_clock_ptp_version
        ptp_gmid = m.ts_ref_clock_ptp_gmid
        ptp_domain = m.ts_ref_clock_ptp_domain
    else:
        source = sdp.ts_ref_clock_source
        local_mac = sdp.ts_ref_clock_local_mac_address
        ntp_addr = sdp.ts_ref_clock_ntp_address
        ptp_ver = sdp.ts_ref_clock_ptp_version
        ptp_gmid = sdp.ts_ref_clock_ptp_gmid
        ptp_domain = sdp.ts_ref_clock_ptp_domain

    if source == E.Local:
        out.write("a=ts-refclk:local\r\n")
    elif source == E.LocalMac:
        out.write(f"a=ts-refclk:localmac={local_mac}\r\n")
    elif source == E.NTP:
        out.write(f"a=ts-refclk:ntp={ntp_addr}\r\n")
    elif source == E.PTP:
        if not ptp_domain:
            out.write(f"a=ts-refclk:ptp={ptp_ver}:{ptp_gmid}\r\n")
        else:
            out.write(f"a=ts-refclk:ptp={ptp_ver}:{ptp_gmid}:{ptp_domain}\r\n")
    elif source is None:
        out.write("a=ts-refclk:local\r\n")
    else:
        raise SdpError(f"invalid ts-refclk source '{source}'")


def _emit_media_clock(sdp: MatroxSdp, out: StringIO, m: Optional[MediaDescriptor]) -> None:

    E = MatroxSdpEnums

    if m is not None:
        clock_type = m.media_clock_type
        offset = m.media_clock_offset
        rate_num = m.media_clock_rate_numerator
        rate_den = m.media_clock_rate_denominator
    else:
        clock_type = sdp.media_clock_type
        offset = sdp.media_clock_offset
        rate_num = sdp.media_clock_rate_numerator
        rate_den = sdp.media_clock_rate_denominator

    if clock_type == E.Direct:
        if rate_num == 0:
            out.write(f"a=mediaclk:direct={offset}\r\n")
        else:
            if rate_den > 1:
                out.write(f"a=mediaclk:direct={offset} rate={rate_num}/{rate_den}\r\n")
            else:
                out.write(f"a=mediaclk:direct={offset} rate={rate_num}\r\n")
    elif clock_type == E.Sender:
        out.write("a=mediaclk:sender\r\n")
    elif clock_type is None:
        out.write("a=mediaclk:direct=0\r\n")
    else:
        raise SdpError(f"invalid mediaclk type '{clock_type}'")


# ---------------------------------------------------------------------------
# extmap / hkep / privacy
# ---------------------------------------------------------------------------

def _emit_ext_map(sdp: MatroxSdp, out: StringIO, m: Optional[MediaDescriptor]) -> None:
    extmap = m.ext_map if m is not None else sdp.ext_map
    for entry in extmap:
        if entry.uri:
            out.write(f"a=extmap:{entry.id}/{entry.direction} {entry.uri}\r\n")


def _emit_hkep(sdp: MatroxSdp, out: StringIO, m: Optional[MediaDescriptor]) -> None:
    hkep = m.hkep_desc if m is not None else sdp.hkep_desc
    for entry in hkep:
        if entry.address:
            addr_type = "IP6" if entry.is_ipv6 else "IP4"
            out.write(f"a=hkep:{entry.port} IN {addr_type} {entry.address} {entry.node_id} {entry.port_id}\r\n")


def _emit_privacy(sdp: MatroxSdp, out: StringIO, m: Optional[MediaDescriptor]) -> None:
    if m is None:
        return
    p = m.privacy_desc
    if p.protocol is not None and p.protocol.s != "NULL":
        out.write(
            f"a=privacy:protocol={p.protocol}; mode={p.mode}; iv={p.iv}; "
            f"key_generator={p.key_generator}; key_version={p.key_version}; key_id={p.key_id}\r\n"
        )


# ---------------------------------------------------------------------------
# Media line
# ---------------------------------------------------------------------------

def _emit_media(sdp: MatroxSdp, out: StringIO, m: MediaDescriptor) -> None:

    E = MatroxSdpEnums

    if m.type == E.Application or m.format_code == 0:
        if m.port_count > 1:
            out.write(f"m={m.type} {m.port}/{m.port_count} {m.protocol} {m.format_string}\r\n")
        else:
            out.write(f"m={m.type} {m.port} {m.protocol} {m.format_string}\r\n")
    else:
        if m.port_count > 1:
            out.write(f"m={m.type} {m.port}/{m.port_count} {m.protocol} {m.format_code}\r\n")
        else:
            out.write(f"m={m.type} {m.port} {m.protocol} {m.format_code}\r\n")

    _emit_information(sdp, out, m)
    _emit_connection(sdp, out, m)
    _emit_bitrate(sdp, out, m)
    _emit_attribute(sdp, out, m)


# ---------------------------------------------------------------------------
# Video fmtp
# ---------------------------------------------------------------------------

def _emit_video_fmtp(sdp: MatroxSdp, out: StringIO, m: MediaDescriptor) -> None:

    _check_payload_and_format(m)

    E = MatroxSdpEnums
    semi = ""

    fmt = str(m.format_code) if m.format_string is None else str(m.format_string)
    out.write(f"a=fmtp:{fmt}")

    if m.encoding_name == E.EncodingSmpte291:

        if m.smpte_standard_number:
            out.write(f"{semi} SSN={m.smpte_standard_number}")
            semi = ";"

        if m.did_sdid:
            out.write(f"{semi} DID_SDID={m.did_sdid}")
            semi = ";"

        if m.vpid_code != 0:
            out.write(f"{semi} VPID_Code={m.vpid_code}")
            semi = ";"

        if m.ts_mode is not None:
            out.write(f"{semi} TSMODE={m.ts_mode}")
            semi = ";"

        if m.ts_delay != 0:
            out.write(f"{semi} TSDELAY={m.ts_delay}")
            semi = ";"

    else:

        if m.width != 0 or m.height != 0 or m.exact_frame_rate_numerator != 0 or m.depth != 0:
            if m.exact_frame_rate_denominator > 1:
                out.write(f"{semi} width={m.width}; height={m.height}; depth={m.depth}; exactframerate={m.exact_frame_rate_numerator}/{m.exact_frame_rate_denominator}")
            else:
                out.write(f"{semi} width={m.width}; height={m.height}; depth={m.depth}; exactframerate={m.exact_frame_rate_numerator}")
            semi = ";"

        if m.sampling is not None:
            out.write(f"{semi} sampling={m.sampling}")
            semi = ";"

        if m.colorimetry is not None:
            out.write(f"{semi} colorimetry={m.colorimetry}")
            semi = ";"

        if m.smpte_standard_number:
            out.write(f"{semi} SSN={m.smpte_standard_number}")
            semi = ";"

        if m.sender_type is not None:
            out.write(f"{semi} TP={m.sender_type}")
            semi = ";"

        if m.packing_mode is not None:
            out.write(f"{semi} PM={m.packing_mode}")
            semi = ";"

        if m.troff != 0:
            out.write(f"{semi} TROFF={m.troff}")
            semi = ";"

        if m.cmax != 0:
            out.write(f"{semi} CMAX={m.cmax}")
            semi = ";"

        if m.max_udp != 0:
            out.write(f"{semi} MAXUDP={m.max_udp}")
            semi = ";"

        if m.transfer_characteristic is not None:
            out.write(f"{semi} TCS={m.transfer_characteristic}")
            semi = ";"

        if m.color_range is not None:
            out.write(f"{semi} RANGE={m.color_range}")
            semi = ";"

        if m.interlaced:
            out.write(f"{semi} interlace")
            semi = ";"
            if m.segmented:
                out.write(f"{semi} segmented")
                semi = ";"
            if m.top_field_first:
                out.write(f"{semi} top-field-first")
                semi = ";"

        if m.chroma_position_cr != 0 or m.chroma_position_cb != 0:
            if m.chroma_position_cr != m.chroma_position_cb:
                out.write(f"{semi} chroma-position={m.chroma_position_cb},{m.chroma_position_cr}")
            else:
                out.write(f"{semi} chroma-position={m.chroma_position_cb}")
            semi = ";"

        if m.gamma != 0:
            out.write(f"{semi} gamma={m.gamma:f}")
            semi = ";"

        if m.picture_aspect_ratio_width != 0 or m.picture_aspect_ratio_height != 0:
            out.write(f"{semi} PAR={m.picture_aspect_ratio_width}:{m.picture_aspect_ratio_height}")
            semi = ";"

        if m.ipmx:
            if m.measured_pix_clk != 0 or m.v_total != 0 or m.h_total != 0:
                out.write(f"{semi} measuredpixclk={m.measured_pix_clk}; vtotal={m.v_total}; htotal={m.h_total}; IPMX")
            else:
                out.write(f"{semi} IPMX")
            semi = ";"

        # JXSV
        if m.encoding_name == E.EncodingJxsv:

            if m.profile is not None:
                out.write(f"{semi} profile={m.profile}")
                semi = ";"

            if m.level is not None:
                out.write(f"{semi} level={m.level}")
                semi = ";"

            if m.sub_level is not None:
                out.write(f"{semi} sublevel={m.sub_level}")
                semi = ";"

            if m.jxsv_packet_mode is not None:
                value = 1 if m.jxsv_packet_mode == E.Slice else 0
                out.write(f"{semi} packetmode={value}")
                semi = ";"

            if m.jxsv_trans_mode is not None:
                value = 1 if m.jxsv_trans_mode == E.SequentialOnly else 0
                out.write(f"{semi} transmode={value}")
                semi = ";"

        # H.264
        if m.encoding_name == E.EncodingH264:

            if m.codec_profile_level_id:
                out.write(f"{semi} profile-level-id={m.codec_profile_level_id}")
                semi = ";"

            if m.h264_parameter_sets:
                out.write(f"{semi} sprop-parameter-sets={m.h264_parameter_sets}")
                semi = ";"

            if m.h264_packetization_mode != 0:
                out.write(f"{semi} packetization-mode={m.h264_packetization_mode}")
                semi = ";"

            if m.h264_packetization_mode == 2:

                if m.h264_interleaving_depth != 0:
                    out.write(f"{semi} sprop-interleaving-depth={m.h264_interleaving_depth}")
                    semi = ";"

                if m.h264_deint_buf_req != 0:
                    out.write(f"{semi} sprop-deint-buf-req={m.h264_deint_buf_req}")
                    semi = ";"

                if m.h264_init_buf_time != 0:
                    out.write(f"{semi} sprop-init-buf-time={m.h264_init_buf_time}")
                    semi = ";"

                if m.h26x_max_don_diff != 0:
                    out.write(f"{semi} sprop-max-don-diff={m.h26x_max_don_diff}")
                    semi = ";"

        # H.265
        if m.encoding_name == E.EncodingH265:

            if m.h265_profile_space != 0:
                out.write(f"{semi} profile-space={m.h265_profile_space}")
                semi = ";"

            if m.h265_profile_id != 0:
                out.write(f"{semi} profile-id={m.h265_profile_id}")
                semi = ";"

            if m.h265_tier_flag:
                out.write(f"{semi} tier-flag=1")
                semi = ";"

            if m.h265_level_id != 0:
                out.write(f"{semi} level-id={m.h265_level_id}")
                semi = ";"

            if m.h265_interop_constraints:
                out.write(f"{semi} interop-constraints={m.h265_interop_constraints}")
                semi = ";"

            if m.h265_profile_compatibility_indicator:
                out.write(f"{semi} profile-compatibility-indicator={m.h265_profile_compatibility_indicator}")
                semi = ";"

            if m.h265_tx_mode is not None:
                out.write(f"{semi} tx-mode={m.h265_tx_mode}")
                semi = ";"

            if m.h265_vps:
                out.write(f"{semi} sprop-vps={m.h265_vps}")
                semi = ";"

            if m.h265_sps:
                out.write(f"{semi} sprop-sps={m.h265_sps}")
                semi = ";"

            if m.h265_pps:
                out.write(f"{semi} sprop-pps={m.h265_pps}")
                semi = ";"

            if m.h26x_max_don_diff != 0:
                out.write(f"{semi} sprop-max-don-diff={m.h26x_max_don_diff}")
                semi = ";"

            if m.h26x_max_don_diff > 0:

                if m.h265_depack_buf_nalus != 0:
                    out.write(f"{semi} sprop-depack-buf-nalus={m.h265_depack_buf_nalus}")
                    semi = ";"

                if m.h265_depack_buf_bytes != 0:
                    out.write(f"{semi} sprop-depack-buf-bytes={m.h265_depack_buf_bytes}")
                    semi = ";"

            if m.h265_segmentation_id != 0:
                out.write(f"{semi} sprop-segmentation-id={m.h265_segmentation_id}")
                semi = ";"

            if m.h265_spatial_segmentation_idc:
                out.write(f"{semi} sprop-spatial-segmentation-idc={m.h265_spatial_segmentation_idc}")
                semi = ";"

        if m.ts_mode is not None:
            out.write(f"{semi} TSMODE={m.ts_mode}")
            semi = ";"

        if m.ts_delay != 0:
            out.write(f"{semi} TSDELAY={m.ts_delay}")
            semi = ";"

    out.write("\r\n")


# ---------------------------------------------------------------------------
# Audio fmtp
# ---------------------------------------------------------------------------

def _emit_audio_fmtp(sdp: MatroxSdp, out: StringIO, m: MediaDescriptor) -> None:

    _check_payload_and_format(m)

    E = MatroxSdpEnums
    semi = ""

    fmt = str(m.format_code) if m.format_string is None else str(m.format_string)
    out.write(f"a=fmtp:{fmt}")

    if m.smpte_standard_number:
        out.write(f"{semi} SSN={m.smpte_standard_number}")
        semi = ";"

    if m.emphasis:
        out.write(f"{semi} emphasis={m.emphasis}")
        semi = ";"

    if m.channel_order:
        out.write(f"{semi} channel-order={m.channel_order}")
        semi = ";"

    if m.ipmx:
        if m.measured_sample_rate != 0:
            out.write(f"{semi} measuredsamplerate={m.measured_sample_rate}; IPMX")
        else:
            out.write(f"{semi} IPMX")
        semi = ";"

    if m.cmax != 0:
        out.write(f"{semi} CMAX={m.cmax}")
        semi = ";"

    if m.ts_mode is not None:
        out.write(f"{semi} TSMODE={m.ts_mode}")
        semi = ";"

    if m.ts_delay != 0:
        out.write(f"{semi} TSDELAY={m.ts_delay}")
        semi = ";"

    # AAC parameters
    if m.aac_stream_type != 0:
        out.write(f"{semi} streamType={m.aac_stream_type}")
        semi = ";"

    if m.aac_mode:
        out.write(f"{semi} mode={m.aac_mode}")
        semi = ";"

    if m.codec_profile_level_id:
        out.write(f"{semi} profile-level-id={m.codec_profile_level_id}")
        semi = ";"

    if m.aac_config:
        out.write(f"{semi} config={m.aac_config}")
        semi = ";"

    if m.encoding_name == E.EncodingAAC_LATM:
        cpresent = 1 if m.aac_config_present else 0
        out.write(f"{semi} cpresent={cpresent}")
        semi = ";"

    if m.aac_object_type != 0:
        if m.encoding_name == E.EncodingAAC_LATM:
            out.write(f"{semi} object={m.aac_object_type}")
        else:
            out.write(f"{semi} objectType={m.aac_object_type}")
        semi = ";"

    if m.aac_constant_duration != 0:
        out.write(f"{semi} constantDuration={m.aac_constant_duration}")
        semi = ";"

    if m.aac_max_displacement != 0:
        out.write(f"{semi} maxDisplacement={m.aac_max_displacement}")
        semi = ";"

    if m.aac_de_interleave_buffer_size != 0:
        out.write(f"{semi} de-interleaveBufferSize={m.aac_de_interleave_buffer_size}")
        semi = ";"

    if m.aac_size_length != 0:
        out.write(f"{semi} sizeLength={m.aac_size_length}")
        semi = ";"

    if m.aac_index_length != 0:
        out.write(f"{semi} indexLength={m.aac_index_length}")
        semi = ";"

    if m.aac_index_delta_length != 0:
        out.write(f"{semi} indexDeltaLength={m.aac_index_delta_length}")
        semi = ";"

    if m.aac_cts_delta_length != 0:
        out.write(f"{semi} CTSDeltaLength={m.aac_cts_delta_length}")
        semi = ";"

    if m.aac_dts_delta_length != 0:
        out.write(f"{semi} DTSDeltaLength={m.aac_dts_delta_length}")
        semi = ";"

    if m.aac_random_access_indication:
        out.write(f"{semi} randomAccessIndication=1")
        semi = ";"

    if m.aac_bitrate != 0:
        out.write(f"{semi} bitrate={m.aac_bitrate}")
        semi = ";"

    out.write("\r\n")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_payload_and_format(m: MediaDescriptor) -> None:
    if m.payload_type == 0:
        return
    if m.payload_type != m.format_code:
        raise SdpError("payload-type must match format-code")
    if m.format_string is not None:
        raise SdpError("payload-type implies a format-code, not a format-string")
