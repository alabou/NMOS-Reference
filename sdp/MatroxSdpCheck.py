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

try:
    from .MatroxSdp import MediaDescriptor, MatroxSdpEnums
except ImportError:
    from MatroxSdp import MediaDescriptor, MatroxSdpEnums

class SdpCheckError(Exception):
    """Custom exception for SDP validation errors."""
    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)


def check_sdp_rfc4175(md: MediaDescriptor) -> None:
    """Check SDP compliance with RFC 4175 (video/raw)."""
    if md.type != MatroxSdpEnums.Video:
        raise SdpCheckError("RFC4175 requires video media type")
    
    if md.encoding_name != MatroxSdpEnums.EncodingRaw:
        raise SdpCheckError("RFC4175 requires raw media subtype")
    
    if md.clock_rate == 0:
        raise SdpCheckError("RFC4175 requires rate")
    
    # REQUIRED parameters
    valid_samplings = {
        MatroxSdpEnums.SamplingRGB, MatroxSdpEnums.SamplingRGBA,
        MatroxSdpEnums.SamplingBGR, MatroxSdpEnums.SamplingBGRA,
        MatroxSdpEnums.SamplingYCbCr_444, MatroxSdpEnums.SamplingYCbCr_422,
        MatroxSdpEnums.SamplingYCbCr_420, MatroxSdpEnums.SamplingYCbCr_411
    }
    if md.sampling is None:
        raise SdpCheckError("RFC4175 requires sampling")
    elif md.sampling not in valid_samplings:
        # New samplings may be registered, so no error, just a note
        print(f"Note: RFC4175 unknown sampling {md.sampling}")
    
    if md.width == 0 or md.width > 32767:
        raise SdpCheckError("RFC4175 invalid width")
    
    if md.height == 0 or md.height > 32767:
        raise SdpCheckError("RFC4175 invalid height")
    
    if md.depth == 0:
        raise SdpCheckError("RFC4175 requires depth")
    
    valid_colorimetries = {
        MatroxSdpEnums.ColorimetryBT601_5, MatroxSdpEnums.ColorimetryBT709_2,
        MatroxSdpEnums.ColorimetrySmpte240M
    }
    if md.colorimetry is None:
        raise SdpCheckError("RFC4175 requires colorimetry")
    elif md.colorimetry not in valid_colorimetries:
        # New colorimetries may be registered
        print(f"Note: RFC4175 unknown colorimetry {md.colorimetry}")


def check_sdp_rfc9134(md: MediaDescriptor) -> None:
    """Check SDP compliance with RFC 9134 (video/jxsv)."""
    if md.type != MatroxSdpEnums.Video:
        raise SdpCheckError("RFC9134 requires video media type")
    
    if md.encoding_name != MatroxSdpEnums.EncodingJxsv:
        raise SdpCheckError("RFC9134 requires jxsv media subtype")
    
    if md.clock_rate != 90000:
        raise SdpCheckError("RFC9134 requires rate of 90 KHz")
    
    # REQUIRED parameters
    if md.jxsv_packet_mode is None or md.jxsv_packet_mode not in {MatroxSdpEnums.CodeStream, MatroxSdpEnums.Slice}:
        raise SdpCheckError("RFC9134 invalid packetmode")
    
    # OPTIONAL parameters
    if md.jxsv_trans_mode is None:
        md.jxsv_trans_mode = MatroxSdpEnums.SequentialOnly  # set default
    elif md.jxsv_trans_mode not in {MatroxSdpEnums.OutOfOrderAllowed, MatroxSdpEnums.SequentialOnly}:
        raise SdpCheckError("RFC9134 invalid transmode")
    
    if md.width != 0 and md.width > 32767:
        raise SdpCheckError("RFC9134 invalid width")
    
    if md.height != 0 and md.height > 32767:
        raise SdpCheckError("RFC9134 invalid height")
    
    if md.segmented and not md.interlaced:
        raise SdpCheckError("RFC9134 invalid interlace, segmented combination")
    
    valid_samplings = {
        MatroxSdpEnums.SamplingYCbCr_444, MatroxSdpEnums.SamplingYCbCr_422, MatroxSdpEnums.SamplingYCbCr_420,
        MatroxSdpEnums.SamplingCLYCbCr_444, MatroxSdpEnums.SamplingCLYCbCr_422, MatroxSdpEnums.SamplingCLYCbCr_420,
        MatroxSdpEnums.SamplingICtCp_444, MatroxSdpEnums.SamplingICtCp_422, MatroxSdpEnums.SamplingICtCp_420,
        MatroxSdpEnums.SamplingRGB, MatroxSdpEnums.SamplingXYZ, MatroxSdpEnums.SamplingKey, MatroxSdpEnums.SamplingUnspecified
    }
    if md.sampling is not None and md.sampling not in valid_samplings:
        raise SdpCheckError("RFC9134 invalid sampling")
    
    valid_colorimetries = {
        MatroxSdpEnums.ColorimetryBT601_5, MatroxSdpEnums.ColorimetryBT709_2, MatroxSdpEnums.ColorimetrySmpte240M,
        MatroxSdpEnums.ColorimetryBT601, MatroxSdpEnums.ColorimetryBT709, MatroxSdpEnums.ColorimetryBT2020,
        MatroxSdpEnums.ColorimetryBT2100, MatroxSdpEnums.ColorimetryST2065_1, MatroxSdpEnums.ColorimetryST2065_3,
        MatroxSdpEnums.ColorimetryXYZ, MatroxSdpEnums.ColorimetryUnspecified
    }
    if md.colorimetry is not None and md.colorimetry not in valid_colorimetries:
        raise SdpCheckError("RFC9134 invalid colorimetry")
    
    valid_transfers = {MatroxSdpEnums.TransferSDR, MatroxSdpEnums.TransferPQ, MatroxSdpEnums.TransferHLG, MatroxSdpEnums.TransferUnspecified}
    if md.transfer_characteristic is not None and md.transfer_characteristic not in valid_transfers:
        raise SdpCheckError("RFC9134 invalid TCS")
    
    if md.color_range == MatroxSdpEnums.RangeFullProtect and md.colorimetry == MatroxSdpEnums.ColorimetryBT2100:
        raise SdpCheckError("RFC9134 invalid RANGE for BT2100")
    elif md.color_range is None:
        md.color_range = MatroxSdpEnums.RangeNarrow if md.colorimetry != MatroxSdpEnums.ColorimetryUnspecified else MatroxSdpEnums.RangeFull


def check_sdp_rfc3551(md: MediaDescriptor) -> None:
    """Check SDP compliance with RFC 3551 (audio/L*)."""
    if md.type != MatroxSdpEnums.Audio:
        raise SdpCheckError("RFC3551 requires audio media type")
    
    valid_encodings = {MatroxSdpEnums.EncodingL8, MatroxSdpEnums.EncodingL16, MatroxSdpEnums.EncodingL20, MatroxSdpEnums.EncodingL24, MatroxSdpEnums.EncodingAM824}
    if md.encoding_name not in valid_encodings:
        raise SdpCheckError("RFC3551 requires L8, L16, L20, L24, or AM824 media subtype")
    
    if md.sample_rate == 0:
        raise SdpCheckError("RFC3551 requires rate of samples")
    
    if md.channels == 0:
        md.channels = 1  # set default
    
    if md.emphasis != "" and md.emphasis != "50-15":
        raise SdpCheckError("RFC3551 invalid emphasis")


def check_sdp_rfc3640(md: MediaDescriptor) -> None:
    """Check SDP compliance with RFC 3640 (audio/mpeg4-generic)."""
    if md.type != MatroxSdpEnums.Audio:
        raise SdpCheckError("RFC3640 requires audio media type")
    
    if md.encoding_name != MatroxSdpEnums.EncodingAAC:
        raise SdpCheckError("RFC3640 requires AAC media subtype")
    
    if md.sample_rate == 0:
        raise SdpCheckError("RFC3640 requires rate of samples")
    
    if md.channels == 0:
        md.channels = 1  # set default
    
    if md.aac_stream_type != 5:
        raise SdpCheckError("RFC3640 requires streamType 5 for audio")
    
    if not md.codec_profile_level_id:
        raise SdpCheckError("RFC3640 requires profile-level-id")
    
    try:
        profile_level_id = int(md.codec_profile_level_id, 10)
        if profile_level_id == 0 or profile_level_id in (254, 255):
            raise ValueError
    except ValueError:
        raise SdpCheckError("RFC3640 invalid profile-level-id")
    
    if not md.aac_config:
        raise SdpCheckError("RFC3640 requires config")
    
    if md.aac_config == '""':
        md.aac_config = ""  # Matrox flavor
    
    if md.aac_mode == "AAC=hbr":
        raise SdpCheckError("RFC3640 requires mode=AAC-hbr (this implementation requires)")
    
    if md.aac_size_length == 0:
        raise SdpCheckError("RFC3640 requires mode=AAC-hbr and sizeLength")
    if md.aac_index_length == 0:
        raise SdpCheckError("RFC3640 requires mode=AAC-hbr and indexLength")
    if md.aac_index_delta_length == 0:
        raise SdpCheckError("RFC3640 requires mode=AAC-hbr and indexDeltaLength")
    if md.aac_constant_duration == 0 and md.aac_max_displacement > 0:
        raise SdpCheckError("RFC3640 requires mode=AAC-hbr and both constantDuration and maxDisplacement in interleaved mode")


def check_sdp_rfc6416(md: MediaDescriptor) -> None:
    """Check SDP compliance with RFC 6416 (audio/MP4A-LATM, audio/MP4A-ADTS)."""
    if md.type != MatroxSdpEnums.Audio:
        raise SdpCheckError("RFC6416 requires audio media type")
    
    valid_encodings = {MatroxSdpEnums.EncodingAAC_LATM, MatroxSdpEnums.EncodingAAC_ADTS}
    if md.encoding_name not in valid_encodings:
        raise SdpCheckError("RFC6416 requires AAC_LATM or AAC_ADTS media subtype")
    
    if md.sample_rate == 0:
        raise SdpCheckError("RFC6416 requires rate of samples")
    
    if md.channels == 0:
        md.channels = 1  # set default
    
    if not md.codec_profile_level_id:
        md.codec_profile_level_id = "30"  # Natural Audio Profile/Level 1
    
    if not md.aac_config_present and not md.aac_config:
        raise SdpCheckError("RFC6416 requires config when cpresent is 0")


def check_sdp_rfc8331(md: MediaDescriptor) -> None:
    """Check SDP compliance with RFC 8331 (ST-291-1). Placeholder."""
    pass  # No specific checks implemented


def check_sdp_rfc6184(md: MediaDescriptor) -> None:
    """Check SDP compliance with RFC 6184 (video/H264)."""
    if md.type != MatroxSdpEnums.Video:
        raise SdpCheckError("RFC6184 requires video media type")
    
    if md.encoding_name != MatroxSdpEnums.EncodingH264:
        raise SdpCheckError("RFC6184 requires H264 media subtype")
    
    if md.clock_rate != 90000:
        raise SdpCheckError("RFC6184 requires rate of 90 KHz")
    
    if not md.codec_profile_level_id:
        md.codec_profile_level_id = "42000A"  # Baseline, Level 1
    else:
        try:
            int(md.codec_profile_level_id, 16)
        except ValueError:
            raise SdpCheckError("RFC6184 profile-level_id must be a valid base 16 value")
    
    if md.h264_packetization_mode not in (0, 1, 2):
        raise SdpCheckError("RFC6184 packetization-mode must be 0, 1, or 2")
    
    if md.h264_packetization_mode in (0, 1):
        if md.h264_interleaving_depth != 0:
            raise SdpCheckError("RFC6184 sprop-interleaving-depth must not be used when packetization-mode is 0 or 1")
        if md.h264_deint_buf_req != 0:
            raise SdpCheckError("RFC6184 sprop-deint-buf-req must not be used when packetization-mode is 0 or 1")
        if md.h264_init_buf_time != 0:
            raise SdpCheckError("RFC6184 sprop-init-buf-time must not be used when packetization-mode is 0 or 1")
        if md.h26x_max_don_diff != 0:
            raise SdpCheckError("RFC6184 sprop-max-don-diff must not be used when packetization-mode is 0 or 1")
    elif md.h264_packetization_mode == 2:
        if md.h264_interleaving_depth > 32767:
            raise SdpCheckError("RFC6184 sprop-interleaving-depth must be <= 32767 when packetization-mode is 2")
        if md.h26x_max_don_diff > 32767:
            raise SdpCheckError("RFC6184 sprop-max-don-diff must be <= 32767 when packetization-mode is 2")


def check_sdp_rfc7798(md: MediaDescriptor) -> None:
    """Check SDP compliance with RFC 7798 (video/H265)."""
    if md.type != MatroxSdpEnums.Video:
        raise SdpCheckError("RFC7798 requires video media type")
    
    if md.encoding_name != MatroxSdpEnums.EncodingH265:
        raise SdpCheckError("RFC7798 requires H265 media subtype")
    
    if md.clock_rate != 90000:
        raise SdpCheckError("RFC7798 requires rate of 90 KHz")
    
    if md.h265_profile_space != 0 and md.h265_profile_space > 3:
        raise SdpCheckError("RFC7798 profile-space must be <= 3")
    
    if md.h265_profile_id != 0 and md.h265_profile_id > 31:
        raise SdpCheckError("RFC7798 profile-id must be <= 31")
    
    if md.h265_profile_id == 0:
        md.h265_profile_id = 1  # default
    
    if md.h265_level_id == 0:
        md.h265_level_id = 93  # default to level 3.1
    
    if not md.h265_interop_constraints:
        md.h265_interop_constraints = "B00000000000"
    else:
        try:
            int(md.h265_interop_constraints, 16)
        except ValueError:
            raise SdpCheckError("RFC7798 interop-constraints must be a valid base 16 value")
    
    if not md.h265_profile_compatibility_indicator:
        md.h265_profile_compatibility_indicator = f"{1 << md.h265_profile_id:08X}"
    else:
        try:
            int(md.h265_profile_compatibility_indicator, 16)
        except ValueError:
            raise SdpCheckError("RFC7798 profile-compatibility-indicator must be a valid base 16 value")
    
    if md.h265_tx_mode is None:
        md.h265_tx_mode = MatroxSdpEnums.H265TxModeSRST
    
    if md.h26x_max_don_diff > 32767:
        raise SdpCheckError("RFC7798 sprop-max-don-diff must be <= 32767")
    
    if md.h265_depack_buf_nalus > 32767:
        raise SdpCheckError("RFC7798 sprop-depack-buf-nalus must be <= 32767")
    
    if md.h26x_max_don_diff > 0 and md.h265_depack_buf_nalus == 0:
        raise SdpCheckError("RFC7798 sprop-depack-buf-nalus must be > 0 when sprop-max-don-diff is > 0")
    
    if md.h26x_max_don_diff > 0 and md.h265_depack_buf_bytes == 0:
        raise SdpCheckError("RFC7798 sprop-depack-buf-bytes must be > 0 when sprop-max-don-diff is > 0")
    
    if md.h265_segmentation_id != 0 and md.h265_segmentation_id > 3:
        raise SdpCheckError("RFC7798 sprop-segmentation-id must be <= 3")
    
    if md.h265_spatial_segmentation_idc:
        try:
            int(md.h265_spatial_segmentation_idc, 16)
        except ValueError:
            raise SdpCheckError("RFC7798 sprop-spatial-segmentation-idc must be a valid base 16 value")


def check_sdp_rfc2250(md: MediaDescriptor) -> None:
    """Check SDP compliance with RFC 2250 (video/MP2T)."""
    if md.type != MatroxSdpEnums.Video:
        raise SdpCheckError("RFC2250 requires video media type")
    
    if md.encoding_name != MatroxSdpEnums.EncodingMP2T:
        raise SdpCheckError("RFC2250 requires MP2T media subtype")
    
    if md.clock_rate != 90000:
        raise SdpCheckError("RFC2250 requires rate")


def check_sdp_st2110_10(md: MediaDescriptor) -> None:
    """Check SDP compliance with ST 2110-10."""
    if md.media_clock_type is None:
        raise SdpCheckError("ST2110-10 requires mediaclk")
    
    if not md.ipmx:
        if md.media_clock_type != MatroxSdpEnums.Direct or md.media_clock_offset != 0:
            raise SdpCheckError("ST2110-10 requires mediaclk direct=0")
    else:
        if (md.media_clock_type != MatroxSdpEnums.Direct and md.media_clock_type != MatroxSdpEnums.Sender) or (md.media_clock_type == MatroxSdpEnums.Direct and md.media_clock_offset != 0):
            raise SdpCheckError("IPMX requires mediaclk sender or direct=0")
    
    if md.ts_ref_clock_source is None:
        raise SdpCheckError("ST2110-10 requires ts-refclk")
    
    if md.max_udp == 0:
        md.max_udp = 1460  # set default


def check_sdp_st2110_20(md: MediaDescriptor) -> None:
    """Check SDP compliance with ST 2110-20."""
    if md.type != MatroxSdpEnums.Video:
        raise SdpCheckError("ST2110-20 requires video media type")
    
    if md.encoding_name != MatroxSdpEnums.EncodingRaw:
        raise SdpCheckError("ST2110-20 requires raw media subtype")
    
    if md.clock_rate != 90000:
        raise SdpCheckError("ST2110-20 requires rate of 90 KHz")
    
    valid_samplings = {
        MatroxSdpEnums.SamplingYCbCr_444, MatroxSdpEnums.SamplingYCbCr_422, MatroxSdpEnums.SamplingYCbCr_420,
        MatroxSdpEnums.SamplingCLYCbCr_444, MatroxSdpEnums.SamplingCLYCbCr_422, MatroxSdpEnums.SamplingCLYCbCr_420,
        MatroxSdpEnums.SamplingICtCp_444, MatroxSdpEnums.SamplingICtCp_422, MatroxSdpEnums.SamplingICtCp_420,
        MatroxSdpEnums.SamplingRGB, MatroxSdpEnums.SamplingXYZ, MatroxSdpEnums.SamplingKey
    }
    if md.sampling not in valid_samplings:
        raise SdpCheckError("ST2110-20 invalid sampling")
    
    if md.depth not in (8, 10, 12, 16):
        raise SdpCheckError("ST2110-20 invalid depth")
    
    if md.width == 0 or md.width > 32767:
        raise SdpCheckError("ST2110-20 invalid width")
    
    if md.height == 0 or md.height > 32767:
        raise SdpCheckError("ST2110-20 invalid height")
    
    if md.exact_frame_rate_numerator == 0 or md.exact_frame_rate_denominator == 0:
        raise SdpCheckError("ST2110-20 invalid exactframerate")
    
    valid_colorimetries = {
        MatroxSdpEnums.ColorimetryBT601, MatroxSdpEnums.ColorimetryBT709, MatroxSdpEnums.ColorimetryBT2020,
        MatroxSdpEnums.ColorimetryBT2100, MatroxSdpEnums.ColorimetryST2065_1, MatroxSdpEnums.ColorimetryST2065_3,
        MatroxSdpEnums.ColorimetryUnspecified, MatroxSdpEnums.ColorimetryXYZ, MatroxSdpEnums.ColorimetryALPHA
    }
    if md.colorimetry not in valid_colorimetries:
        raise SdpCheckError("ST2110-20 invalid colorimetry")
    
    if md.packing_mode not in {MatroxSdpEnums.PackingMode2110GPM, MatroxSdpEnums.PackingMode2110BPM}:
        raise SdpCheckError("ST2110-20 invalid PM")
    
    # The ST2110-20 document changed the version tobe used for SSN very late in the approval process and a
    # number of vendors use vesions of the document withthe 2021 value and some other with the 2022 one. 
    # We allow for both values in order not to penalize those early adopters of the specification.
    if md.smpte_standard_number == "ST2110-20:2017":
        if md.transfer_characteristic == MatroxSdpEnums.TransferST2115LOGS3 or md.colorimetry == MatroxSdpEnums.ColorimetryALPHA:
            raise SdpCheckError("ST2110-20 invalid SSN, ST2110-20:2017 cannot be used with ALPHA or ST2115LOGS3")
    elif md.smpte_standard_number == "ST2110-20:2021":
        if md.transfer_characteristic != MatroxSdpEnums.TransferST2115LOGS3 and md.colorimetry != MatroxSdpEnums.ColorimetryALPHA:
            raise SdpCheckError("ST2110-20 invalid SSN, ST2110-20:2021 cannot be used without ALPHA or ST2115LOGS3")
    elif md.smpte_standard_number == "ST2110-20:2022":
        if md.transfer_characteristic != MatroxSdpEnums.TransferST2115LOGS3 and md.colorimetry != MatroxSdpEnums.ColorimetryALPHA:
            raise SdpCheckError("ST2110-20 invalid SSN, ST2110-20:2022 cannot be used without ALPHA or ST2115LOGS3")
    else:
        raise SdpCheckError("ST2110-20 invalid SSN")
    
    valid_transfers = {
        MatroxSdpEnums.TransferSDR, MatroxSdpEnums.TransferPQ, MatroxSdpEnums.TransferHLG, MatroxSdpEnums.TransferLinear,
        MatroxSdpEnums.TransferBT2100LINPQ, MatroxSdpEnums.TransferBT2100LINHLG, MatroxSdpEnums.TransferST2065_1,
        MatroxSdpEnums.TransferST248_1, MatroxSdpEnums.TransferDensity, MatroxSdpEnums.TransferUnspecified,
        MatroxSdpEnums.TransferST2115LOGS3
    }
    if md.transfer_characteristic is None:
        md.transfer_characteristic = MatroxSdpEnums.TransferSDR
    elif md.transfer_characteristic not in valid_transfers:
        raise SdpCheckError("ST2110-20 invalid TCS")
    
    if md.color_range == MatroxSdpEnums.RangeFullProtect and md.colorimetry == MatroxSdpEnums.ColorimetryBT2100:
        raise SdpCheckError("ST2110-20 invalid RANGE for BT2100")
    elif md.color_range is None:
        md.color_range = MatroxSdpEnums.RangeNarrow
    
    if md.max_udp == 0:
        md.max_udp = 1460
    
    if md.picture_aspect_ratio_width == 0 or md.picture_aspect_ratio_height == 0:
        md.picture_aspect_ratio_width = 1
        md.picture_aspect_ratio_height = 1


def check_sdp_st2110_21(md: MediaDescriptor) -> None:
    """Check SDP compliance with ST 2110-21."""
    if md.type != MatroxSdpEnums.Video:
        raise SdpCheckError("ST2110-21 requires video media type")
    
    valid_senders = {MatroxSdpEnums.SenderType2110TPN, MatroxSdpEnums.SenderType2110TPNL, MatroxSdpEnums.SenderType2110TPW}
    if md.sender_type not in valid_senders:
        raise SdpCheckError("ST2110-21 invalid45@0 TP")


def check_sdp_st2110_22(md: MediaDescriptor) -> None:
    """Check SDP compliance with ST 2110-22."""
    if md.type != MatroxSdpEnums.Video:
        raise SdpCheckError("ST2110-22 requires video media type")
    
    if md.encoding_name == MatroxSdpEnums.EncodingRaw:
        raise SdpCheckError("ST2110-22 requires other than raw media subtype")
    
    if md.clock_rate != 90000:
        raise SdpCheckError("ST2110-22 requires rate of 90 KHz")
    
    if md.width == 0 or md.width > 32767:
        raise SdpCheckError("ST2110-22 invalid width")
    
    if md.height == 0 or md.height > 32767:
        raise SdpCheckError("ST2110-22 invalid height")
    
    if md.smpte_standard_number == "ST2110-22:2022":
        valid_senders = {MatroxSdpEnums.SenderType2110TPNL, MatroxSdpEnums.SenderType2110TPW, MatroxSdpEnums.SenderType2110TPN}
    else:
        valid_senders = {MatroxSdpEnums.SenderType2110TPNL, MatroxSdpEnums.SenderType2110TPW}

    if md.sender_type not in valid_senders:
        raise SdpCheckError("ST2110-22 invalid TP")
    
    if md.bitrate_kbits == 0:
        raise SdpCheckError("ST2110-22 invalid bitrate in b=")
    
    if (md.frame_rate_numerator == 0 or md.frame_rate_denominator == 0) and (md.exact_frame_rate_numerator == 0 or md.exact_frame_rate_denominator == 0):
        raise SdpCheckError("ST2110-22 invalid framerate (framerate and exactframerate not specified)")
    
    if md.exact_frame_rate_numerator == 0 or md.exact_frame_rate_denominator == 0:
        md.exact_frame_rate_numerator = md.frame_rate_numerator
        md.exact_frame_rate_denominator = md.frame_rate_denominator


def check_sdp_st2110_30(md: MediaDescriptor) -> None:
    """Check SDP compliance with ST 2110-30 (AES67)."""
    if md.type != MatroxSdpEnums.Audio:
        raise SdpCheckError("ST2110-30 requires audio media type")
    
    valid_encodings = {MatroxSdpEnums.EncodingL8, MatroxSdpEnums.EncodingL16, MatroxSdpEnums.EncodingL20, MatroxSdpEnums.EncodingL24}
    if md.encoding_name not in valid_encodings:
        raise SdpCheckError("ST2110-30 requires L8, L16, L20, or L24 media subtype")
    
    if md.sample_rate == 0:
        raise SdpCheckError("ST2110-30 requires rate of samples")
    
    if not md.channel_order.startswith("SMPTE2110."):
        raise SdpCheckError("ST2110-30 invalid channel-order convention")
    
    if md.p_time_us == 0:
        raise SdpCheckError("ST2110-30 invalid ptime")
    
    valid_ptimes = {125, 120, 250, 333, 330, 1000, 4000, 272, 270, 363, 360, 1088, 1090, 4354, 4350}
    if md.p_time_us not in valid_ptimes:
        raise SdpCheckError("ST2110-30 unexpected ptime")
    
    if md.max_p_time_us != 0 and (md.max_p_time_us not in valid_ptimes or md.max_p_time_us < md.p_time_us):
        raise SdpCheckError("ST2110-30 invalid maxptime")
    
    if md.ts_ref_clock_source is None:
        raise SdpCheckError("ST2110-30 requires ts-refclk")
    
    if not md.ipmx:
        if md.media_clock_type != MatroxSdpEnums.Direct:
            raise SdpCheckError("ST2110-30 requires mediaclk direct")
    else:
        if md.media_clock_type != MatroxSdpEnums.Sender and md.media_clock_type != MatroxSdpEnums.Direct:
            raise SdpCheckError("ST2110-30 requires mediaclk sender or direct")


def check_sdp_st2110_31(md: MediaDescriptor) -> None:
    """Check SDP compliance with ST 2110-31."""
    if md.type != MatroxSdpEnums.Audio:
        raise SdpCheckError("ST2110-31 requires audio media type")
    
    if md.encoding_name != MatroxSdpEnums.EncodingAM824:
        raise SdpCheckError("ST2110-31 requires AM824 media subtype")
    
    if md.sample_rate not in (44100, 48000, 96000):
        raise SdpCheckError("ST2110-31 invalid samples rate")
    
    if md.channels % 2 != 0:
        raise SdpCheckError("ST2110-31 invalid number of channels")
    
    if md.channel_order and not md.channel_order.startswith("SMPTE2110."):
        raise SdpCheckError("ST2110-31 invalid channel-order convention")
    
    valid_ptimes = {83, 80, 125, 120, 1000, 1088, 1090, 136, 140, 91, 90}
    if md.p_time_us not in valid_ptimes:
        raise SdpCheckError("ST2110-31 invalid ptime")
    
    if md.ts_ref_clock_source is None:
        raise SdpCheckError("ST2110-31 requires ts-refclk")
    
    if not md.ipmx:
        if md.media_clock_type != MatroxSdpEnums.Direct or md.media_clock_offset != 0:
            raise SdpCheckError("ST2110-31 requires mediaclk direct=0")
    else:
        if (md.media_clock_type != MatroxSdpEnums.Direct and md.media_clock_type != MatroxSdpEnums.Sender) or (md.media_clock_type == MatroxSdpEnums.Direct and md.media_clock_offset != 0):
            raise SdpCheckError("IPMX requires mediaclk sender or direct=0")


def check_sdp_st2110_40(md: MediaDescriptor) -> None:
    """Check SDP compliance with ST 2110-40. Placeholder."""
    pass


def check_sdp_ipmx(md: MediaDescriptor) -> None:
    """Check SDP compliance with IPMX. Placeholder."""
    pass


def check_sdp_nmos(md: MediaDescriptor) -> None:
    """Check SDP compliance with NMOS. Placeholder."""
    pass

def check_sdp_ipmx_usb(md: MediaDescriptor) -> None:
    """Check SDP compliance with IPMX/USB (application/usb)."""
    if md.type != MatroxSdpEnums.Application:
        raise SdpCheckError("IPMX/USB requires application media type")
    
    if md.format_string != MatroxSdpEnums.FormatUsb:
        raise SdpCheckError("IPMX/USB requires usb media subtype")
    
    if md.protocol != MatroxSdpEnums.ProtocolTCP:
        raise SdpCheckError("IPMX/USB requires TCP protocol")
    
    # a=setup:passive is not verified here but it will be verified by the NMOS BCP-007-02 test suite.
    

# Example usage
# if __name__ == "__main__":
#     md = MediaDescriptor()
#     md.type = MatroxSdpEnums.Video
#     md.encoding_name = MatroxSdpEnums.EncodingRaw
#     md.clock_rate = 90000
#     md.sampling = MatroxSdpEnums.SamplingYCbCr_422
#     md.width = 1920
#     md.height = 1080
#     md.depth = 10
#     md.colorimetry = MatroxSdpEnums.ColorimetryBT709
    
#     try:
#         check_sdp_rfc4175(md)
#         print("RFC 4175 check passed")
#     except SdpCheckError as e:
#         print(f"RFC 4175 check failed: {e.message}")
