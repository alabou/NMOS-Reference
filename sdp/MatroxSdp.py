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

import re
from io import StringIO
from typing import List, Dict, Optional, Tuple
import math
from enum import Enum

# Constants
MAX_MEDIAS = 2
MAX_HKEPS = 2
MAX_EXTMAPS = 8

# Simulate Go's uint types with Python integers (no strict bit width in Python)
uint8 = int
uint16 = int
uint = int
uint64 = int
float64 = float

# H.265 Constraint flags (48-bit values)
GENERAL_PROGRESSIVE_SOURCE_FLAG = 1 << 47           # used to express interlaced versus progressive
GENERAL_INTERLACED_SOURCE_FLAG = 1 << 46            # used to express interlaced versus progressive  
GENERAL_NON_PACKED_CONSTRAINT_FLAG = 1 << 45        # set to 1 by default
GENERAL_FRAME_ONLY_CONSTRAINT_FLAG = 1 << 44        # set to 1 if progressive_source_flag is 1, 0 otherwise

GENERAL_MAX_12BIT_CONSTRAINT_FLAG = 1 << 43
GENERAL_MAX_10BIT_CONSTRAINT_FLAG = 1 << 42
GENERAL_MAX_8BIT_CONSTRAINT_FLAG = 1 << 41
GENERAL_MAX_422CHROMA_CONSTRAINT_FLAG = 1 << 40
GENERAL_MAX_420CHROMA_CONSTRAINT_FLAG = 1 << 39
GENERAL_MAX_MONOCHROME_CONSTRAINT_FLAG = 1 << 38
GENERAL_INTRA_CONSTRAINT_FLAG = 1 << 37
GENERAL_ONE_PICTURE_ONLY_CONSTRAINT_FLAG = 1 << 36
GENERAL_LOWER_BIT_RATE_CONSTRAINT_FLAG = 1 << 35

GENERAL_MAX_14BIT_CONSTRAINT_FLAG = 1 << 34
GENERAL_INBLD_FLAG = 1 << 0

PROFILE_CONSTRAINTS_MASK = (GENERAL_MAX_14BIT_CONSTRAINT_FLAG |
                           GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                           GENERAL_MAX_10BIT_CONSTRAINT_FLAG |
                           GENERAL_MAX_8BIT_CONSTRAINT_FLAG |
                           GENERAL_MAX_422CHROMA_CONSTRAINT_FLAG |
                           GENERAL_MAX_420CHROMA_CONSTRAINT_FLAG |
                           GENERAL_MAX_MONOCHROME_CONSTRAINT_FLAG |
                           GENERAL_INTRA_CONSTRAINT_FLAG |
                           GENERAL_ONE_PICTURE_ONLY_CONSTRAINT_FLAG |
                           GENERAL_LOWER_BIT_RATE_CONSTRAINT_FLAG)

PROFILE_CONSTRAINTS_INTRA_MASK = (GENERAL_MAX_14BIT_CONSTRAINT_FLAG |
                                 GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                 GENERAL_MAX_10BIT_CONSTRAINT_FLAG |
                                 GENERAL_MAX_8BIT_CONSTRAINT_FLAG |
                                 GENERAL_MAX_422CHROMA_CONSTRAINT_FLAG |
                                 GENERAL_MAX_420CHROMA_CONSTRAINT_FLAG |
                                 GENERAL_MAX_MONOCHROME_CONSTRAINT_FLAG |
                                 GENERAL_INTRA_CONSTRAINT_FLAG |
                                 GENERAL_ONE_PICTURE_ONLY_CONSTRAINT_FLAG)
                                 # GENERAL_LOWER_BIT_RATE_CONSTRAINT_FLAG excluded

PROFILE_CONSTRAINTS_STILL_MASK = (GENERAL_MAX_14BIT_CONSTRAINT_FLAG |
                                 GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                 GENERAL_MAX_10BIT_CONSTRAINT_FLAG |
                                 GENERAL_MAX_8BIT_CONSTRAINT_FLAG |
                                 GENERAL_MAX_422CHROMA_CONSTRAINT_FLAG |
                                 GENERAL_MAX_420CHROMA_CONSTRAINT_FLAG |
                                 GENERAL_MAX_MONOCHROME_CONSTRAINT_FLAG |
                                 GENERAL_INTRA_CONSTRAINT_FLAG |
                                 GENERAL_ONE_PICTURE_ONLY_CONSTRAINT_FLAG)
                                 # GENERAL_LOWER_BIT_RATE_CONSTRAINT_FLAG excluded

# TargetSpecification Enum (simulating Go's iota-based enum)
class TargetSpecification(Enum):
    NONE = 0
    RFC4175 = 1      # video/raw
    RFC9134 = 2      # video/jxsv
    RFC3551 = 3      # audio/L*
    RFC8331 = 4      # ST-291-1
    RFC6184 = 5      # video/H264
    RFC7798 = 6      # video/H265
    RFC3640 = 7      # audio/mpeg4-generic
    RFC6416 = 8      # audio/MP4A-LATM, audio/MP4A-ADTS
    RFC2250 = 9      # video/MP2T
    ST2110_10 = 10
    ST2110_20 = 11
    ST2110_21 = 12
    ST2110_22 = 13
    ST2110_30 = 14
    ST2110_31 = 15
    ST2110_40 = 16
    IPMX = 17
    NMOS = 18

# AAC Object Types
AAC_OBJECT_TYPES = {
    "Main": 1,
    "LC": 2,
    "SSR": 3,
    "LTP": 4,
    "SBR": 5,
    "ER_LC": 17,
    "ER_LTP": 18,
    "ER_LD": 23,
    "PS": 29,
    "ER_ESCAPE": 31,
    "ER_ELD": 39,
}

# Enum simulation using a dictionary
ALL_ENUMS: Dict[str, str] = {}
PANIC_ON_DUPLICATE_ENUM = False

class EnumId:
    def __init__(self, s: str):
        self.s = s

    def __str__(self) -> str:
        return self.s

    def __hash__(self):
        return hash(self.s)

    def __eq__(self, other) -> bool:

        if isinstance(other, EnumId):
            return self.s == other.s
        if isinstance(other, str):
            return self.s == other
        if isinstance(other, Enum):
            return self.s == other.value
        
        return False


class MatroxSdpEnums(Enum):

    def __hash__(self):
        return hash(self.value.s)

    Audio                                = EnumId("audio")                # media type
    Video                                = EnumId("video")                # media type
    Text                                 = EnumId("text")                 # media type
    Application                          = EnumId("application")          # media type
    Message                              = EnumId("message")              # media type
    Local                                = EnumId("local")                # clock source
    LocalMac                             = EnumId("localmac")             # clock source
    NTP                                  = EnumId("ntp")                  # clock source
    PTP                                  = EnumId("ptp")                  # clock source
    Sender                               = EnumId("sender")               # mediaclk type
    Direct                               = EnumId("direct")               # mediaclk type
    OutOfOrderAllowed                    = EnumId("out-of-order-allowed") # jxsv transmode
    SequentialOnly                       = EnumId("sequential-only")      # jxsv transmode
    CodeStream                           = EnumId("codestream")           # jxsv packetmode
    Slice                                = EnumId("slice")                # jxsv packetmode
    EncodingRaw                          = EnumId("raw")
    EncodingJxsv                         = EnumId("jxsv")
    EncodingSmpte291                     = EnumId("smpte291")
    EncodingL8                           = EnumId("L8")
    EncodingL16                          = EnumId("L16")
    EncodingL20                          = EnumId("L20")
    EncodingL24                          = EnumId("L24")
    EncodingAM824                        = EnumId("AM824")
    EncodingH264                         = EnumId("H264")
    EncodingH265                         = EnumId("H265")
    EncodingAAC                          = EnumId("mpeg4-generic")
    EncodingAAC_LATM                     = EnumId("MP4A-LATM")
    EncodingAAC_ADTS                     = EnumId("MP4A-ADTS")
    EncodingMP2T                         = EnumId("MP2T")
    SamplingRGB                          = EnumId("RGB")
    SamplingRGBA                         = EnumId("RGBA")
    SamplingBGR                          = EnumId("BGR")
    SamplingBGRA                         = EnumId("BGRA")
    SamplingYCbCr_444                    = EnumId("YCbCr-4:4:4")
    SamplingYCbCr_422                    = EnumId("YCbCr-4:2:2")
    SamplingYCbCr_420                    = EnumId("YCbCr-4:2:0")
    SamplingYCbCr_411                    = EnumId("YCbCr-4:1:1")
    SamplingCLYCbCr_444                  = EnumId("CLYCbCr-4:4:4")
    SamplingCLYCbCr_422                  = EnumId("CLYCbCr-4:2:2")
    SamplingCLYCbCr_420                  = EnumId("CLYCbCr-4:2:0")
    SamplingICtCp_444                    = EnumId("ICtCp-4:4:4")
    SamplingICtCp_422                    = EnumId("ICtCp-4:2:2")
    SamplingICtCp_420                    = EnumId("ICtCp-4:2:0")
    SamplingXYZ                          = EnumId("XYZ")
    SamplingKey                          = EnumId("KEY")
    SamplingUnspecified                  = EnumId("UNSPECIFIED")
    ColorimetryBT601_5                   = EnumId("BT601-5")
    ColorimetryBT709_2                   = EnumId("BT709-2")
    ColorimetrySmpte240M                 = EnumId("SMPTE240M")
    ColorimetryBT601                     = EnumId("BT601")
    ColorimetryBT709                     = EnumId("BT709")
    ColorimetryBT2020                    = EnumId("BT2020")
    ColorimetryBT2100                    = EnumId("BT2100")
    ColorimetryST2065_1                  = EnumId("ST2065-1")
    ColorimetryST2065_3                  = EnumId("ST2065-3")
    ColorimetryXYZ                       = EnumId("XYZ")
    ColorimetryALPHA                     = EnumId("ALPHA")
    ColorimetryUnspecified               = EnumId("UNSPECIFIED")
    TransferSDR                          = EnumId("SDR")
    TransferPQ                           = EnumId("PQ")
    TransferHLG                          = EnumId("HLG")
    TransferUnspecified                  = EnumId("UNSPECIFIED")
    TransferLinear                       = EnumId("LINEAR")
    TransferBT2100LINPQ                  = EnumId("BT2100LINPQ")
    TransferBT2100LINHLG                 = EnumId("BT2100LINHLG")
    TransferST2065_1                     = EnumId("ST2065-1")
    TransferST248_1                      = EnumId("ST248-1")
    TransferDensity                      = EnumId("DENSITY")
    TransferST2115LOGS3                  = EnumId("ST2115LOGS3")
    RangeNarrow                          = EnumId("NARROW")
    RangeFull                            = EnumId("FULL")
    RangeFullProtect                     = EnumId("FULLPROTECT")
    RangeUnspecified                     = EnumId("UNSPECIFIED")
    PackingMode2110GPM                   = EnumId("2110GPM")
    PackingMode2110BPM                   = EnumId("2110BPM")
    SenderType2110TPN                    = EnumId("2110TPN")
    SenderType2110TPNL                   = EnumId("2110TPNL")
    SenderType2110TPW                    = EnumId("2110TPW")
    ProtocolTCP                          = EnumId("TCP")
    ProtocolUDP                          = EnumId("UDP")
    ProtocolTCP_RTP_AVP                  = EnumId("TCP/RTP/AVP")
    ProtocolRTP_AVP                      = EnumId("RTP/AVP")
    FormatJson                           = EnumId("json")
    FormatUsb                            = EnumId("usb")
    FormatMpeg2TS                        = EnumId("mp2t") # special lowercase one for using as application/mp2t
    FormatRtsp                           = EnumId("rtsp")
    PrivacyProtocolRTP                   = EnumId("RTP")
    PrivacyProtocolRTP_KV                = EnumId("RTP_KV")
    PrivacyProtocolSRT                   = EnumId("SRT")
    PrivacyProtocolSRTP                  = EnumId("SRTP")
    PrivacyProtocolRTSP                  = EnumId("RTSP")
    PrivacyProtocolRTSP_KV               = EnumId("RTSP_KV")
    PrivacyProtocolUDP                   = EnumId("UDP")
    PrivacyProtocolUDP_KV                = EnumId("UDP_KV")
    PrivacyProtocolUSB                   = EnumId("USB")
    PrivacyProtocolUSB_KV                = EnumId("USB_KV")
    PrivacyProtocolNULL                  = EnumId("NULL")
    PrivacyModeAES128CTR                 = EnumId("AES-128-CTR")
    PrivacyModeAES256CTR                 = EnumId("AES-256-CTR")
    PrivacyModeAES128CTR_CMAC64          = EnumId("AES-128-CTR_CMAC-64")
    PrivacyModeAES256CTR_CMAC64          = EnumId("AES-256-CTR_CMAC-64")
    PrivacyModeAES128CTR_CMAC64_AAD      = EnumId("AES-128-CTR_CMAC-64-AAD")
    PrivacyModeAES256CTR_CMAC64_AAD      = EnumId("AES-256-CTR_CMAC-64-AAD")
    PrivacyModeAES128_GCM128             = EnumId("AES-128-GMAC-128")
    PrivacyModeAES256_GCM128             = EnumId("AES-256-GMAC-128")
    PrivacyModeECDH_AES128CTR            = EnumId("ECDH_AES-128-CTR")
    PrivacyModeECDH_AES256CTR            = EnumId("ECDH_AES-256-CTR")
    PrivacyModeECDH_AES128CTR_CMAC64     = EnumId("ECDH_AES-128-CTR_CMAC-64")
    PrivacyModeECDH_AES256CTR_CMAC64     = EnumId("ECDH_AES-256-CTR_CMAC-64")
    PrivacyModeECDH_AES128CTR_CMAC64_AAD = EnumId("ECDH_AES-128-CTR_CMAC-64-AAD")
    PrivacyModeECDH_AES256CTR_CMAC64_AAD = EnumId("ECDH_AES-256-CTR_CMAC-64-AAD")
    PrivacyModeECDH_AES128_GCM128        = EnumId("ECDH_AES-128-GMAC-128")
    PrivacyModeECDH_AES256_GCM128        = EnumId("ECDH_AES-256-GMAC-128")
    TsModeSample    = EnumId("SAMP")
    TsModeNew       = EnumId("NEW")
    TsModePreserved = EnumId("PRES")
    JxsvProfileMain420_12  = EnumId("Main420.12")
    JxsvProfileHigh420_12  = EnumId("High420.12")
    JxsvProfileMain444_12  = EnumId("Main444.12")
    JxsvProfileMain4444_12 = EnumId("Main4444.12")
    JxsvProfileHigh444_12  = EnumId("High444.12")
    JxsvProfileTDC444_12   = EnumId("TDC444.12")
    JxsvProfileHigh4444_12 = EnumId("High4444.12")
    JxsvLevel1k1           = EnumId("1k-1")
    JxsvLevel2k1           = EnumId("2k-1")
    JxsvLevel4k1           = EnumId("4k-1")
    JxsvLevel4k2           = EnumId("4k-2")
    JxsvLevel4k3           = EnumId("4k-3")
    JxsvLevel8k1           = EnumId("8k-1")
    JxsvLevel8k2           = EnumId("8k-2")
    JxsvLevel8k3           = EnumId("8k-3")
    JxsvSublevel2bpp       = EnumId("Sublev2bpp")
    JxsvSublevel3bpp       = EnumId("Sublev3bpp")
    JxsvSublevel4bpp       = EnumId("Sublev4bpp")
    JxsvSublevel6bpp       = EnumId("Sublev6bpp")
    JxsvSublevel9bpp       = EnumId("Sublev9bpp")
    JxsvSublevel12bpp      = EnumId("Sublev12bpp")
    # JPEG XS FBB (frame buffer) levels (ISO/IEC 21122-2,
    # draft-ietf-avtcore-rtp-jpegxs-3ed-02 §7.1)
    JxsvFbblevelUnrestricted = EnumId("Unrestricted")
    JxsvFbblevelFull         = EnumId("FbblevFull")
    JxsvFbblevel3bpp         = EnumId("Fbblev3bpp")
    JxsvFbblevel4_5bpp       = EnumId("Fbblev4.5bpp")
    JxsvFbblevel8bpp         = EnumId("Fbblev8bpp")
    JxsvFbblevel12bpp        = EnumId("Fbblev12bpp")

    H265TxModeSRST = EnumId("SRST")
    H265TxModeMRST = EnumId("MRST")
    H265TxModeMRMT = EnumId("MRMT")
    
    # H.265 Profiles
    H265ProfileMain10 = EnumId("Main10")
    H265ProfileMain10StillPicture = EnumId("Main10StillPicture")
    H265ProfileMainStillPicture = EnumId("MainStill")
    H265ProfileMonochrome = EnumId("Monochrome")
    H265ProfileMonochrome10 = EnumId("Monochrome10")
    H265ProfileMonochrome12 = EnumId("Monochrome12")
    H265ProfileMonochrome16 = EnumId("Monochrome16")
    H265ProfileMain12 = EnumId("Main12")
    H265ProfileMain10_422 = EnumId("Main10-422")
    H265ProfileMain12_422 = EnumId("Main12-422")
    H265ProfileMain_444 = EnumId("Main-444")
    H265ProfileMain10_444 = EnumId("Main10-444")
    H265ProfileMain12_444 = EnumId("Main12-444")
    H265ProfileMainIntra = EnumId("MainIntra")
    H265ProfileMain10Intra = EnumId("Main10Intra")
    H265ProfileMain12Intra = EnumId("Main12Intra")
    H265ProfileMain10Intra_422 = EnumId("Main10Intra-422")
    H265ProfileMain12Intra_422 = EnumId("Main12Intra-422")
    H265ProfileMainIntra_444 = EnumId("MainIntra-444")
    H265ProfileMain10Intra_444 = EnumId("Main10Intra-444")
    H265ProfileMain12Intra_444 = EnumId("Main12Intra-444")
    H265ProfileMain16Intra_444 = EnumId("Main16Intra-444")
    H265ProfileMainStillPicture_444 = EnumId("MainStillPicture-444")
    H265ProfileMain16StillPicture_444 = EnumId("Main16StillPicture-444")
    H265ProfileHighThroughput_444 = EnumId("HighThroughput-444")
    H265ProfileHighThroughput10_444 = EnumId("HighThroughput10-444")
    H265ProfileHighThroughput14_444 = EnumId("HighThroughput14-444")
    H265ProfileHighThroughput16Intra_444 = EnumId("HighThroughput16Intra-444")
    H265ProfileScreenExtendedMain = EnumId("ScreenExtendedMain")
    H265ProfileScreenExtendedMain10 = EnumId("ScreenExtendedMain10")
    H265ProfileScreenExtendedMain_444 = EnumId("ScreenExtendedMain-444")
    H265ProfileScreenExtendedMain10_444 = EnumId("ScreenExtendedMain10-444")
    H265ProfileScreenExtendedHighThroughput_444 = EnumId("ScreenExtendedHighThroughput-444")
    H265ProfileScreenExtendedHighThroughput10_444 = EnumId("ScreenExtendedHighThroughput10-444")
    H265ProfileScreenExtendedHighThroughput14_444 = EnumId("ScreenExtendedHighThroughput14-444")
    
    # H.265 Levels
    H265LevelMain1 = EnumId("Main-1")
    H265LevelMain2 = EnumId("Main-2")
    H265LevelMain2_1 = EnumId("Main-2.1")
    H265LevelMain3 = EnumId("Main-3")
    H265LevelMain3_1 = EnumId("Main-3.1")
    H265LevelMain4 = EnumId("Main-4")
    H265LevelMain4_1 = EnumId("Main-4.1")
    H265LevelMain5 = EnumId("Main-5")
    H265LevelMain5_1 = EnumId("Main-5.1")
    H265LevelMain5_2 = EnumId("Main-5.2")
    H265LevelMain6 = EnumId("Main-6")
    H265LevelMain6_1 = EnumId("Main-6.1")
    H265LevelMain6_2 = EnumId("Main-6.2")
    H265LevelHigh1 = EnumId("High-1")
    H265LevelHigh2 = EnumId("High-2")
    H265LevelHigh2_1 = EnumId("High-2.1")
    H265LevelHigh3 = EnumId("High-3")
    H265LevelHigh3_1 = EnumId("High-3.1")
    H265LevelHigh4 = EnumId("High-4")
    H265LevelHigh4_1 = EnumId("High-4.1")
    H265LevelHigh5 = EnumId("High-5")
    H265LevelHigh5_1 = EnumId("High-5.1")
    H265LevelHigh5_2 = EnumId("High-5.2")
    H265LevelHigh6 = EnumId("High-6")
    H265LevelHigh6_1 = EnumId("High-6.1")
    H265LevelHigh6_2 = EnumId("High-6.2")
    H265LevelHigh8_5 = EnumId("High-8.5")
    
    # H.264 Profiles
    H264ProfileBaseline = EnumId("Baseline")
    H264ProfileBaselineConstrained = EnumId("BaselineConstrained")
    CodecProfileMain = EnumId("Main")
    H264ProfileExtended = EnumId("Extended")
    H264ProfileHigh = EnumId("High")
    H264ProfileHighProgressive = EnumId("HighProgressive")
    H264ProfileHighConstrained = EnumId("HighConstrained")
    H264ProfileHigh10 = EnumId("High10")
    H264ProfileHigh10Progressive = EnumId("High10Progressive")
    H264ProfileHigh10Intra = EnumId("High10Intra")
    H264ProfileHigh_422 = EnumId("High-422")
    H264ProfileHighIntra_422 = EnumId("HighIntra-422")
    H264ProfileHighPredictive_444 = EnumId("HighPredictive-444")
    H264ProfileHighIntra_444 = EnumId("HighIntra-444")
    H264ProfileCAVLCIntra_444 = EnumId("CAVLCIntra-444")
    
    # AAC Profiles
    AacProfileSpeech = EnumId("Speech")
    AacProfileSynthetic = EnumId("Synthetic")
    AacProfileScalable = EnumId("Scalable")
    AacProfileHighQuality = EnumId("HighQuality")
    AacProfileLowDelay = EnumId("LowDelay")
    AacProfileNatural = EnumId("Natural")
    AacProfileMobile = EnumId("Mobile")
    AacProfileAAC = EnumId("AAC")
    AacProfileHighEfficiencyAAC = EnumId("HighEfficiencyAAC")
    AacProfileHighEfficiencyAACv2 = EnumId("HighEfficiencyAACv2")
    AacProfileLowDelayAAC = EnumId("LowDelayAAC")
    AacProfileLowDelayAACv2 = EnumId("LowDelayAACv2")
    AacProfileExtendedHighEfficiencyAAC = EnumId("ExtendedHighEfficiencyAAC")
    
    # H.264/H.265 Codec Levels
    CodecLevel1b = EnumId("1b")
    CodecLevel1 = EnumId("1")
    CodecLevel1_1 = EnumId("1.1")
    CodecLevel1_2 = EnumId("1.2")
    CodecLevel1_3 = EnumId("1.3")
    CodecLevel2 = EnumId("2")
    CodecLevel2_1 = EnumId("2.1")
    CodecLevel2_2 = EnumId("2.2")
    CodecLevel3 = EnumId("3")
    CodecLevel3_1 = EnumId("3.1")
    CodecLevel3_2 = EnumId("3.2")
    CodecLevel4 = EnumId("4")
    CodecLevel4_1 = EnumId("4.1")
    CodecLevel4_2 = EnumId("4.2")
    CodecLevel5 = EnumId("5")
    CodecLevel5_1 = EnumId("5.1")
    CodecLevel5_2 = EnumId("5.2")
    CodecLevel6 = EnumId("6")
    CodecLevel6_1 = EnumId("6.1")
    CodecLevel6_2 = EnumId("6.2")
    CodecLevel7 = EnumId("7")
    CodecLevel8 = EnumId("8")

def init_enums():
    global ALL_ENUMS
    for e in MatroxSdpEnums:
        if e.value.s in ALL_ENUMS and PANIC_ON_DUPLICATE_ENUM and e.value.s != "":
            raise ValueError(f"duplicate enum {e.value}")
        ALL_ENUMS[e.value.s] = e.value

init_enums()

def lookup_enum(s: str, auto_enum: bool) -> Tuple[Optional[EnumId], Optional[str]]:
    if s in ALL_ENUMS:
        return ALL_ENUMS[s], None
    if auto_enum:
        enum_id = EnumId(s)
        ALL_ENUMS[s] = enum_id
        return enum_id, None
    return None, f"enum string not found: {s}"

def auto_lookup_enum(s: str) -> EnumId:
    if s in ALL_ENUMS:
        return ALL_ENUMS[s]
    enum_id = EnumId(s)
    ALL_ENUMS[s] = enum_id
    return enum_id

# Helper class for error simulation
class SdpError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)

class HkepDescriptor:
    def __init__(self):
        self.is_ipv6: bool = False
        self.address: str = ""
        self.port: uint16 = 0
        self.node_id: str = ""
        self.port_id: str = ""

class PrivacyDescriptor:
    def __init__(self):
        self.protocol: Optional[EnumId] = None
        self.mode: Optional[EnumId] = None
        self.iv: str = ""
        self.key_generator: str = ""
        self.key_id: str = ""
        self.key_version: str = ""

class ExtmapDescriptor:
    def __init__(self):
        self.id: uint = 0
        self.uri: str = ""
        self.direction: str = ""  # "sendonly", "recvonly", "sendrecv", "inactive"

class MediaDescriptor:
    def __init__(self):
        # Grouping
        self.media_name: str = ""
        # Information
        self.media_information: str = ""
        # Base
        self.port: uint16 = 0
        self.port_count: uint = 0
        self.rtcp_port: uint16 = 0
        self.rtcp_connection_address: str = ""
        self.rtcp_is_connection_ipv6: bool = False
        self.rtcp_connection_ttl: uint8 = 0
        self.rtcp_connection_count: uint8 = 0
        # Type and Protocol
        self.type: Optional[EnumId] = None
        self.protocol: Optional[EnumId] = None
        self.format_code: uint8 = 0
        self.format_string: Optional[EnumId] = None
        # Connection
        self.connection_address: str = ""
        self.is_connection_ipv6: bool = False
        self.connection_ttl: uint8 = 0
        self.connection_count: uint8 = 0
        self.bitrate_kbits: uint = 0
        # Source Filter
        self.source_filter_dst_address: str = ""
        self.source_filter_src_address: str = ""
        self.is_source_filter_ipv6: bool = False
        # RTP Map / FMTP
        self.payload_type: uint8 = 0
        self.encoding_name: Optional[EnumId] = None
        self.clock_rate: uint64 = 0
        self.sample_rate: uint64 = 0
        self.channels: uint = 0
        # FMTP Generic
        self.ipmx: bool = False
        self.hkep: bool = False
        self.privacy: bool = False
        # FMTP Video
        self.sampling: Optional[EnumId] = None
        self.depth: uint = 0
        self.width: uint = 0
        self.height: uint = 0
        self.exact_frame_rate_numerator: uint64 = 0
        self.exact_frame_rate_denominator: uint64 = 0
        self.colorimetry: Optional[EnumId] = None
        self.color_range: Optional[EnumId] = None
        self.transfer_characteristic: Optional[EnumId] = None
        self.chroma_position_cb: uint = 0
        self.chroma_position_cr: uint = 0
        self.gamma: float64 = 0.0
        self.interlaced: bool = False
        self.segmented: bool = False
        self.top_field_first: bool = False
        self.picture_aspect_ratio_width: uint = 0
        self.picture_aspect_ratio_height: uint = 0
        self.h_total: uint = 0
        self.v_total: uint = 0
        self.measured_pix_clk: uint64 = 0
        self.measured_sample_rate: uint64 = 0
        self.smpte_standard_number: str = ""
        self.sender_type: Optional[EnumId] = None
        self.packing_mode: Optional[EnumId] = None
        self.max_udp: uint = 0
        self.troff: uint = 0
        self.cmax: uint = 0
        # JXSV Specific
        self.profile: Optional[EnumId] = None
        self.level: Optional[EnumId] = None
        self.sub_level: Optional[EnumId] = None
        self.fbb_level: Optional[EnumId] = None
        self.jxsv_trans_mode: Optional[EnumId] = None
        self.jxsv_packet_mode: Optional[EnumId] = None
        # H264/H265 Shared
        self.codec_profile_level_id: str = ""
        # H264 Specific
        self.h264_parameter_sets: str = ""
        self.h264_packetization_mode: uint8 = 0
        self.h264_interleaving_depth: uint = 0
        self.h264_deint_buf_req: uint = 0
        self.h264_init_buf_time: uint = 0
        self.h26x_max_don_diff: uint = 0
        # H265 Specific
        self.h265_profile_space: uint8 = 0
        self.h265_profile_id: uint8 = 0
        self.h265_level_id: uint8 = 0
        self.h265_interop_constraints: str = ""
        self.h265_profile_compatibility_indicator: str = ""
        self.h265_tier_flag: bool = False
        self.h265_tx_mode: Optional[EnumId] = None
        self.h265_vps: str = ""
        self.h265_sps: str = ""
        self.h265_pps: str = ""
        self.h265_depack_buf_nalus: uint = 0
        self.h265_depack_buf_bytes: uint = 0
        self.h265_segmentation_id: uint8 = 0
        self.h265_spatial_segmentation_idc: str = ""
        # ST-2110-40
        self.did_sdid: str = ""
        self.vpid_code: uint = 0
        # FMTP Audio
        self.channel_order: str = ""
        self.p_time_us: uint64 = 0
        self.max_p_time_us: uint64 = 0
        self.frame_count: uint = 0
        self.emphasis: str = ""
        # AAC Specific
        self.aac_stream_type: uint8 = 0
        self.aac_mode: str = ""
        self.aac_config: str = ""
        self.aac_config_present: bool = False
        self.aac_object_type: uint8 = 0
        self.aac_constant_duration: uint = 0
        self.aac_max_displacement: uint = 0
        self.aac_de_interleave_buffer_size: uint = 0
        self.aac_size_length: uint8 = 0
        self.aac_index_length: uint8 = 0
        self.aac_index_delta_length: uint8 = 0
        self.aac_cts_delta_length: uint8 = 0
        self.aac_dts_delta_length: uint8 = 0
        self.aac_random_access_indication: bool = False
        self.aac_bitrate: uint64 = 0
        # HKEP
        self.hkep_desc: List[HkepDescriptor] = [HkepDescriptor() for _ in range(MAX_HKEPS)]
        # Privacy
        self.privacy_desc: PrivacyDescriptor = PrivacyDescriptor()
        # Media Clock
        self.media_clock_type: Optional[EnumId] = None
        self.media_clock_offset: uint64 = 0
        self.media_clock_rate_numerator: uint64 = 0
        self.media_clock_rate_denominator: uint64 = 0
        # TS Reference Clock
        self.ts_ref_clock_source: Optional[EnumId] = None
        self.ts_ref_clock_ptp_version: str = ""
        self.ts_ref_clock_ptp_traceable: bool = False
        self.ts_ref_clock_ptp_gmid: str = ""
        self.ts_ref_clock_ptp_domain: str = ""
        self.ts_ref_clock_ntp_address: str = ""
        self.ts_ref_clock_local_mac_address: str = ""
        # TS Mode
        self.ts_mode: Optional[EnumId] = None
        self.ts_delay: uint64 = 0
        # Extmap
        self.ext_map: List[ExtmapDescriptor] = [ExtmapDescriptor() for _ in range(MAX_EXTMAPS)]
        # Framerate
        self.frame_rate_numerator: uint64 = 0
        self.frame_rate_denominator: uint64 = 0
        # RTSP Sub-stream Control
        self.sub_stream_control: str = ""

class MatroxSdp:
    def __init__(self):
        # Version
        self.version: uint8 = 0
        # Originator
        self.username: str = ""
        self.session_id: uint64 = 0
        self.session_version: uint64 = 0
        self.is_origin_ipv6: bool = False
        self.origin_address: str = ""
        # Session Name
        self.session_name: Optional[str] = None
        # Session Information
        self.session_information: str = ""
        # Timing
        self.start: uint64 = 0
        self.stop: uint64 = 0
        # Connection (session level)
        self.connection_address: str = ""
        self.is_connection_ipv6: bool = False
        self.connection_ttl: uint8 = 0
        self.connection_count: uint8 = 0
        # Bandwidth (session level)
        self.bitrate_kbits: uint = 0
        # HKEP (session level)
        self.hkep_desc: List[HkepDescriptor] = [HkepDescriptor() for _ in range(MAX_HKEPS)]
        # Privacy
        self.privacy_desc: PrivacyDescriptor = PrivacyDescriptor()
        # Media Clock
        self.media_clock_type: Optional[EnumId] = None
        self.media_clock_offset: uint64 = 0
        self.media_clock_rate_numerator: uint64 = 0
        self.media_clock_rate_denominator: uint64 = 0
        # TS Reference Clock
        self.ts_ref_clock_source: Optional[EnumId] = None
        self.ts_ref_clock_ptp_version: str = ""
        self.ts_ref_clock_ptp_traceable: bool = False
        self.ts_ref_clock_ptp_gmid: str = ""
        self.ts_ref_clock_ptp_domain: str = ""
        self.ts_ref_clock_ntp_address: str = ""
        self.ts_ref_clock_local_mac_address: str = ""
        # Extmap (session level)
        self.ext_map: List[ExtmapDescriptor] = [ExtmapDescriptor() for _ in range(MAX_EXTMAPS)]
        # Group Attribute
        self.has_group_attribute: bool = False
        self.primary_media_name: str = ""
        self.primary_media: Optional[MediaDescriptor] = None
        self.secondary_media_name: str = ""
        self.secondary_media: Optional[MediaDescriptor] = None
        # RTSP Session Control
        self.session_control: str = ""
        # Internal Members
        self.current_input: Optional[List[str]] = None
        self.current_output: Optional[StringIO] = None
        self.current_media: Optional[MediaDescriptor] = None
        self.in_media_section: bool = False
        self.media_count: uint = 0
        self.medias: List[MediaDescriptor] = [MediaDescriptor() for _ in range(MAX_MEDIAS)]

    def reset(self):
        # Reset all fields to their zero values
        self.__init__()
        self.primary_media = self.medias[0]
        self.secondary_media = self.medias[1]

    def decode(self, reader: str) -> Optional[str]:
        self.reset()
        self.current_input = reader.splitlines()
        err = self.process_lines()
        if err:
            return err

        # Setup primary and secondary media pointers
        if self.has_group_attribute:
            if self.media_count != 2:
                return f"invalid media count: {self.media_count}"
            if self.medias[0].media_name == self.primary_media_name:
                self.primary_media = self.medias[0]
            elif self.medias[1].media_name == self.primary_media_name:
                self.primary_media = self.medias[1]
            else:
                return "invalid primary group name"
            if self.medias[0].media_name == self.secondary_media_name:
                self.secondary_media = self.medias[0]
            elif self.medias[1].media_name == self.secondary_media_name:
                self.secondary_media = self.medias[1]
            else:
                return "invalid secondary group name"
            if self.primary_media == self.secondary_media:
                return "invalid group"
        else:
            if self.media_count != 1:
                return f"invalid media count: {self.media_count}"
            self.primary_media = self.medias[0]
            self.primary_media_name = self.medias[0].media_name
            self.secondary_media = self.medias[0]
            self.secondary_media_name = self.medias[0].media_name

        err = self.check_sdp_base_requirements()
        if err:
            return err
        return None

    def process_lines(self) -> Optional[str]:
        if not self.current_input:
            return None
        for line in self.current_input:
            line = line.strip()
            if len(line) < 2:
                continue
            if line[1] != '=':
                return "missing = character after line type"
            line_type = line[0]
            line_content = line[2:].encode('utf-8')
            if line_type == 'v':
                err = self.process_version(line_content)
            elif line_type == 'o':
                err = self.process_origin(line_content)
            elif line_type == 's':
                err = self.process_session_name(line_content)
            elif line_type == 'i':
                err = self.process_information(line_content)
            elif line_type == 'c':
                err = self.process_connection(line_content)
            elif line_type == 'b':
                err = self.process_bitrate(line_content)
            elif line_type == 't':
                err = self.process_timing(line_content)
            elif line_type == 'a':
                err = self.process_attribute(line_content)
            elif line_type == 'm':
                err = self.process_media(line_content)
            elif line_type in ('k', 'z', 'u', 'e', 'p', 'r'):
                print(f"Warning: line type '{line_type}' not supported and ignored")
                err = None
            else:
                print(f"Warning: line type '{line_type}' unknown and ignored")
                err = None
            if err:
                return err
        return None

    def process_version(self, line: bytes) -> Optional[str]:
        if line.decode('utf-8')[0] != '0':
            return "invalid protocol version"
        return None

    def process_origin(self, line: bytes) -> Optional[str]:
        split = line.split(b' ')
        if len(split) != 6:
            return "invalid origin line"
        self.username = split[0].decode('utf-8')
        try:
            self.session_id = int(split[1])
            self.session_version = int(split[2])
        except ValueError:
            return "invalid origin session-id or session-version"
        if split[3] != b"IN":
            return "invalid origin nettype"
        if split[4] not in (b"IP4", b"IP6"):
            return "invalid origin addrtype"
        self.is_origin_ipv6 = split[4] == b"IP6"
        self.origin_address = split[5].decode('utf-8')
        return None

    def process_session_name(self, line: bytes) -> Optional[str]:
        self.session_name = line.decode('utf-8')
        return None

    def process_information(self, line: bytes) -> Optional[str]:
        info = line.decode('utf-8')
        if self.in_media_section:
            self.current_media.media_information = info
        else:
            self.session_information = info
        return None

    def process_connection(self, line: bytes) -> Optional[str]:
        split = line.split(b' ')
        if len(split) != 3:
            return "invalid connection line"
        if split[0] != b"IN":
            return "invalid connection nettype"
        if split[1] not in (b"IP4", b"IP6"):
            return "invalid connection addrtype"
        is_ipv6 = split[1] == b"IP6"
        split_address = split[2].split(b'/')
        if not split_address:
            return "invalid connection-address"
        address = split_address[0].decode('utf-8')
        count = 1
        ttl = 0
        if is_ipv6:
            if len(split_address) > 1:
                try:
                    count = int(split_address[1])
                except ValueError:
                    return "invalid connection-address number of addresses"
        else:
            if len(split_address) > 2:
                try:
                    ttl = int(split_address[1])
                    count = int(split_address[2])
                except ValueError:
                    return "invalid connection-address TTL or number of addresses"
            elif len(split_address) > 1:
                try:
                    ttl = int(split_address[1])
                    count = 1
                except ValueError:
                    return "invalid connection-address TTL"
            else:
                count = 1
        if self.in_media_section:
            self.current_media.connection_address = address
            self.current_media.is_connection_ipv6 = is_ipv6
            self.current_media.connection_ttl = ttl
            self.current_media.connection_count = count
        else:
            self.connection_address = address
            self.is_connection_ipv6 = is_ipv6
            self.connection_ttl = ttl
            self.connection_count = count
        return None

    def process_bitrate(self, line: bytes) -> Optional[str]:
        split = line.split(b':')
        if len(split) != 2:
            return "invalid bandwidth line"
        if split[0] != b"AS":
            return "invalid bandwidth type"
        try:
            value = int(split[1])
        except ValueError:
            return "invalid bandwidth"
        if self.in_media_section:
            self.current_media.bitrate_kbits = value
        else:
            self.bitrate_kbits = value
        return None

    def process_timing(self, line: bytes) -> Optional[str]:
        split = line.split(b' ')
        if len(split) != 2:
            return "invalid timing line"
        try:
            self.start = int(split[0])
            self.stop = int(split[1])
        except ValueError:
            return "invalid start-time or stop-time"
        return None

    def process_attribute(self, line: bytes) -> Optional[str]:
        if self.in_media_section:
            return self.process_media_attribute(line)
        return self.process_session_attribute(line)

    def process_session_attribute(self, line: bytes) -> Optional[str]:
        attr, value = line.split(b':', 1) if b':' in line else (line, None)
        attr_str = attr.decode('utf-8')
        if attr_str == "group":
            if value is None:
                return "invalid session attribute line"
            split = value.split(b' ')
            if len(split) != 3 or split[0] != b"DUP":
                return "invalid group attribute"
            self.primary_media_name = split[1].decode('utf-8')
            self.secondary_media_name = split[2].decode('utf-8')
            self.has_group_attribute = True
        elif attr_str == "ts-refclk":
            return self.process_ts_ref_clk(value)
        elif attr_str == "mediaclk":
            return self.process_media_clk(value)
        elif attr_str == "hkep":
            return self.process_hkep(value)
        elif attr_str == "privacy":
            return self.process_privacy(value)
        elif attr_str == "control":
            return self.process_session_control(value)
        elif attr_str == "extmap":
            return self.process_extmap(value)
        elif attr_str == "charset":
            return "invalid charset attribute"
        else:
            print(f"Warning: attribute '{attr_str}' unknown and ignored")
        return None

    def process_media_attribute(self, line: bytes) -> Optional[str]:
        attr, value = line.split(b':', 1) if b':' in line else (line, None)
        attr_str = attr.decode('utf-8')
        if attr_str == "source-filter":
            return self.process_source_filter(value)
        elif attr_str == "rtcp":
            return self.process_rtcp(value)
        elif attr_str == "rtpmap":
            return self.process_rtp_map(value)
        elif attr_str == "fmtp":
            return self.process_fmtp(value)
        elif attr_str == "ts-refclk":
            return self.process_ts_ref_clk(value)
        elif attr_str == "mediaclk":
            return self.process_media_clk(value)
        elif attr_str == "hkep":
            return self.process_hkep(value)
        elif attr_str == "privacy":
            return self.process_privacy(value)
        elif attr_str == "control":
            return self.process_session_control(value)
        elif attr_str == "mid":
            return self.process_mid(value)
        elif attr_str == "ptime":
            return self.process_p_time(value)
        elif attr_str == "maxptime":
            return self.process_max_p_time(value)
        elif attr_str == "framecount":
            return self.process_frame_count(value)
        elif attr_str == "framerate":
            return self.process_frame_rate(value)
        elif attr_str == "extmap":
            return self.process_extmap(value)
        elif attr_str == "setup":
            return self.process_setup(value)
        else:
            print(f"Warning: attribute '{attr_str}' unknown and ignored")
        return None

    def process_media(self, line: bytes) -> Optional[str]:
        if self.media_count >= MAX_MEDIAS:
            return "too many medias"
        self.medias[self.media_count] = MediaDescriptor()
        self.current_media = self.medias[self.media_count]
        self.media_count += 1
        self.in_media_section = True
        self.copy_session_level_to_media_level()
        split = line.split(b' ')
        if len(split) != 4:
            return "invalid media line"
        enum, err = lookup_enum(split[0].decode('utf-8'), True)
        if err:
            return err
        self.current_media.type = enum
        if self.current_media.type.s not in ("audio", "video", "text", "application", "message"):
            return "invalid media type"
        split_port = split[1].split(b'/')
        if len(split_port) == 2:
            try:
                self.current_media.port = int(split_port[0])
                self.current_media.port_count = int(split_port[1])
            except ValueError:
                return "invalid transport port or port count"
        else:
            try:
                self.current_media.port = int(split[1])
                self.current_media.port_count = 1
            except ValueError:
                return "invalid transport port"
        enum, err = lookup_enum(split[2].decode('utf-8'), True)
        if err:
            return err
        self.current_media.protocol = enum
        enum, err = lookup_enum(split[3].decode('utf-8'), True)
        if err:
            return err
        self.current_media.format_string = enum
        self.current_media.format_code = 0
        try:
            code = int(self.current_media.format_string.s)
            self.current_media.format_code = code
            self.current_media.format_string = None
        except ValueError:
            pass
        return None

    def copy_session_level_to_media_level(self):
        self.current_media.media_information = self.session_information
        if self.connection_address:
            self.current_media.connection_address = self.connection_address
            self.current_media.connection_ttl = self.connection_ttl
            self.current_media.connection_count = self.connection_count
            self.current_media.is_connection_ipv6 = self.is_connection_ipv6
        if self.bitrate_kbits:
            self.current_media.bitrate_kbits = self.bitrate_kbits

        for i in range(MAX_HKEPS):
            if self.hkep_desc[i].address:
                self.current_media.hkep_desc[i].address = self.hkep_desc[i].address
                self.current_media.hkep_desc[i].is_ipv6 = self.hkep_desc[i].is_ipv6
                self.current_media.hkep_desc[i].port = self.hkep_desc[i].port
                self.current_media.hkep_desc[i].node_id = self.hkep_desc[i].node_id
                self.current_media.hkep_desc[i].port_id = self.hkep_desc[i].port_id
                self.current_media.hkep = True

        if self.privacy_desc.protocol:
            self.current_media.privacy_desc = self.privacy_desc
            self.current_media.privacy = True

        if self.session_control:
            self.current_media.sub_stream_control = self.session_control
        for i in range(MAX_EXTMAPS):
            if self.ext_map[i].uri:
                self.current_media.ext_map[i].uri = self.ext_map[i].uri
                self.current_media.ext_map[i].direction = self.ext_map[i].direction
                self.current_media.ext_map[i].id = self.ext_map[i].id
        if self.media_clock_type:
            self.current_media.media_clock_type = self.media_clock_type
            self.current_media.media_clock_offset = self.media_clock_offset
            self.current_media.media_clock_rate_numerator = self.media_clock_rate_numerator
            self.current_media.media_clock_rate_denominator = self.media_clock_rate_denominator
        if self.ts_ref_clock_source:
            self.current_media.ts_ref_clock_source = self.ts_ref_clock_source
            self.current_media.ts_ref_clock_local_mac_address = self.ts_ref_clock_local_mac_address
            self.current_media.ts_ref_clock_ntp_address = self.ts_ref_clock_ntp_address
            self.current_media.ts_ref_clock_ptp_version = self.ts_ref_clock_ptp_version
            self.current_media.ts_ref_clock_ptp_traceable = self.ts_ref_clock_ptp_traceable
            self.current_media.ts_ref_clock_ptp_gmid = self.ts_ref_clock_ptp_gmid
            self.current_media.ts_ref_clock_ptp_domain = self.ts_ref_clock_ptp_domain

    def process_source_filter(self, value: bytes) -> Optional[str]:
        if not self.in_media_section:
            return "unexpected source-filter attribute"
        value = value.strip()
        if value.startswith(b' '):
            value = value[1:]
        split = value.split(b' ')
        if len(split) < 5:
            return "invalid source-filter attribute"
        if len(split) > 5:
            print("Warning: using only the first src-address")
        if split[0] != b"incl":
            return "invalid source-filter mode"
        if split[1] != b"IN":
            return "invalid source-filter nettype"
        if split[2] not in (b"IP4", b"IP6"):
            return "invalid source-filter addrtype"
        self.current_media.is_source_filter_ipv6 = split[2] == b"IP6"
        self.current_media.source_filter_dst_address = split[3].decode('utf-8')
        self.current_media.source_filter_src_address = split[4].decode('utf-8')
        return None

    def process_rtcp(self, value: bytes) -> Optional[str]:
        if not self.in_media_section:
            return "unexpected rtcp attribute"
        split = value.split(b' ')
        if len(split) not in (1, 4):
            return "rtcp attribute must contain either only a port or a port, nettype, addrtype and connection-address"
        try:
            self.current_media.rtcp_port = int(split[0])
        except ValueError:
            return "invalid rtcp port"
        if len(split) == 4:
            if split[1] != b"IN":
                return "invalid connection nettype"
            if split[2] not in (b"IP4", b"IP6"):
                return "invalid connection addrtype"
            is_ipv6 = split[2] == b"IP6"
            split_address = split[3].split(b'/')
            if not split_address:
                return "invalid connection-address"
            address = split_address[0].decode('utf-8')
            count = 1
            ttl = 0
            if is_ipv6:
                if len(split_address) > 1:
                    try:
                        count = int(split_address[1])
                    except ValueError:
                        return "invalid connection-address number of addresses"
            else:
                if len(split_address) > 2:
                    try:
                        ttl = int(split_address[1])
                        count = int(split_address[2])
                    except ValueError:
                        return "invalid connection-address TTL or number of addresses"
                elif len(split_address) > 1:
                    try:
                        ttl = int(split_address[1])
                    except ValueError:
                        return "invalid connection-address TTL"
            self.current_media.rtcp_connection_address = address
            self.current_media.rtcp_is_connection_ipv6 = is_ipv6
            self.current_media.rtcp_connection_ttl = ttl
            self.current_media.rtcp_connection_count = count
        return None

    def process_rtp_map(self, value: bytes) -> Optional[str]:
        if not self.in_media_section:
            return "unexpected rtpmap attribute"
        if self.current_media.format_string:
            return "invalid rtpmap attribute"
        split = value.split(b' ')
        if len(split) != 2:
            return "invalid rtpmap attribute"
        try:
            code = int(split[0])
            if code > 127:
                raise ValueError
        except ValueError:
            return "invalid rtpmap payload-type"
        self.current_media.payload_type = code
        if self.current_media.payload_type != self.current_media.format_code:
            return "invalid rtpmap payload-type"
        split_encoding = split[1].split(b'/')
        if len(split_encoding) < 2:
            return "invalid rtpmap encoding/rate/params"
        enum, err = lookup_enum(split_encoding[0].decode('utf-8'), True)
        if err:
            return err
        self.current_media.encoding_name = enum
        if self.current_media.type.s == "audio":
            try:
                self.current_media.sample_rate = int(split_encoding[1])
            except ValueError:
                return "invalid rtpmap clock-rate"
            if len(split_encoding) > 2:
                try:
                    self.current_media.channels = int(split_encoding[2])
                except ValueError:
                    return "invalid rtpmap encoding-params"
                if len(split_encoding) > 3:
                    print("Warning: ignoring extra encoding-params")
            else:
                self.current_media.channels = 1
        else:
            try:
                self.current_media.clock_rate = int(split_encoding[1])
            except ValueError:
                return "invalid rtpmap clock-rate"
            if len(split_encoding) > 2:
                print("Warning: ignoring extra encoding-params")
        return None

    def process_fmtp(self, value: bytes) -> Optional[str]:
        if not self.in_media_section:
            return "unexpected fmtp attribute"
        split = value.split(b' ', 1)
        if len(split) < 2:
            return None
        if self.current_media.format_string is None:
            try:
                code = int(split[0])
                if code > 127:
                    raise ValueError
            except ValueError:
                return "invalid fmtp payload-type"
            if self.current_media.payload_type == 0:
                self.current_media.payload_type = code
            if self.current_media.payload_type != self.current_media.format_code:
                return "invalid fmtp payload-type"
        else:
            enum, err = lookup_enum(split[0].decode('utf-8'), True)
            if err:
                return err
            if self.current_media.format_string != enum:
                return "invalid fmtp format"
        split_params = split[1].split(b';')
        for pair in split_params:
            param, val = pair.split(b'=', 1) if b'=' in pair else (pair, None)
            param = param.strip().decode('utf-8')
            val = val.strip().decode('utf-8') if val else None
            method_name = f"process_parameter_{param.replace('-', '_').lower()}"
            if hasattr(self, method_name):
                err = getattr(self, method_name)(val.encode('utf-8') if val else None)
                if err:
                    return err
            else:
                print(f"Warning: ignoring unknown parameter '{param}'")
        return None

    def process_ts_ref_clk(self, value: bytes) -> Optional[str]:
        src, val = value.split(b'=', 1) if b'=' in value else (value, None)
        src = src.strip().decode('utf-8')
        val = val.strip().decode('utf-8') if val else ""
        enum, err = lookup_enum(src, True)
        if err:
            return err
        ts_ref_clock_source = enum
        ts_ref_clock_local_mac_address = ""
        ts_ref_clock_ntp_address = ""
        ts_ref_clock_ptp_version = ""
        ts_ref_clock_ptp_traceable = False
        ts_ref_clock_ptp_gmid = ""
        ts_ref_clock_ptp_domain = ""
        if ts_ref_clock_source.s == "local":
            pass
        elif ts_ref_clock_source.s == "localmac":
            ts_ref_clock_local_mac_address = val
        elif ts_ref_clock_source.s == "ntp":
            ts_ref_clock_ntp_address = val
        elif ts_ref_clock_source.s == "ptp":
            split_ptp = val.split(':')
            if len(split_ptp) < 2:
                return "invalid ts-refclk ptp clock value"
            ts_ref_clock_ptp_version = split_ptp[0]

            if split_ptp[1] == "traceable":
                ts_ref_clock_ptp_traceable = True
            else:
                ts_ref_clock_ptp_gmid = split_ptp[1]

            if len(split_ptp) > 2:
                ts_ref_clock_ptp_domain = split_ptp[2]
        else:
            print(f"Warning: unknown ts-refclk source '{ts_ref_clock_source.s}' ignored")
        if self.in_media_section:
            self.current_media.ts_ref_clock_source = ts_ref_clock_source
            self.current_media.ts_ref_clock_local_mac_address = ts_ref_clock_local_mac_address
            self.current_media.ts_ref_clock_ntp_address = ts_ref_clock_ntp_address
            self.current_media.ts_ref_clock_ptp_version = ts_ref_clock_ptp_version
            self.current_media.ts_ref_clock_ptp_traceable = ts_ref_clock_ptp_traceable
            self.current_media.ts_ref_clock_ptp_gmid = ts_ref_clock_ptp_gmid
            self.current_media.ts_ref_clock_ptp_domain = ts_ref_clock_ptp_domain
        else:
            self.ts_ref_clock_source = ts_ref_clock_source
            self.ts_ref_clock_local_mac_address = ts_ref_clock_local_mac_address
            self.ts_ref_clock_ntp_address = ts_ref_clock_ntp_address
            self.ts_ref_clock_ptp_version = ts_ref_clock_ptp_version
            self.ts_ref_clock_ptp_traceable = ts_ref_clock_ptp_traceable
            self.ts_ref_clock_ptp_gmid = ts_ref_clock_ptp_gmid
            self.ts_ref_clock_ptp_domain = ts_ref_clock_ptp_domain
        return None

    def process_media_clk(self, value: bytes) -> Optional[str]:
        kind, val = value.split(b'=', 1) if b'=' in value else (value, None)
        kind = kind.strip().decode('utf-8')
        val = val.strip().decode('utf-8') if val else ""
        enum, err = lookup_enum(kind, True)
        if err:
            return err
        media_clock_type = enum
        media_clock_offset = 0
        media_clock_rate_numerator = 0
        media_clock_rate_denominator = 0
        if media_clock_type.s == "sender":
            pass
        elif media_clock_type.s == "direct":
            split_value = val.split()
            if not split_value:
                return "invalid mediaclk direct attribute"
            try:
                media_clock_offset = int(split_value[0])
            except ValueError:
                return "invalid mediaclk direct offset"
            if len(split_value) > 1:
                split_rate = split_value[1].split('=')
                if len(split_rate) != 2 or split_rate[0] != "rate":
                    return "invalid mediaclk direct attribute"
                split_ratio = split_rate[1].split('/')
                if not split_ratio:
                    return "invalid mediaclk direct rate"
                try:
                    media_clock_rate_numerator = int(split_ratio[0])
                    media_clock_rate_denominator = int(split_ratio[1]) if len(split_ratio) > 1 else 1
                except ValueError:
                    return "invalid mediaclk direct rate numerator or denominator"
        else:
            print(f"Warning: unknown mediaclk type '{media_clock_type.s}' ignored")
        if self.in_media_section:
            self.current_media.media_clock_type = media_clock_type
            self.current_media.media_clock_offset = media_clock_offset
            self.current_media.media_clock_rate_numerator = media_clock_rate_numerator
            self.current_media.media_clock_rate_denominator = media_clock_rate_denominator
        else:
            self.media_clock_type = media_clock_type
            self.media_clock_offset = media_clock_offset
            self.media_clock_rate_numerator = media_clock_rate_numerator
            self.media_clock_rate_denominator = media_clock_rate_denominator
        return None

    def process_hkep(self, value: bytes) -> Optional[str]:
        split = value.split(b' ')
        if len(split) != 6:
            return "invalid hkep attribute line"
        try:
            port = int(split[0])
        except ValueError:
            return "invalid hkep port"
        if split[1] != b"IN":
            return "invalid hkep nettype"
        if split[2] not in (b"IP4", b"IP6"):
            return "invalid hkep addrtype"
        is_ipv6 = split[2] == b"IP6"
        address = split[3].decode('utf-8')
        node_id = split[4].decode('utf-8')
        port_id = split[5].decode('utf-8')
        hkep_desc = HkepDescriptor()
        hkep_desc.is_ipv6 = is_ipv6
        hkep_desc.address = address
        hkep_desc.port = port
        hkep_desc.node_id = node_id
        hkep_desc.port_id = port_id
        target = self.current_media.hkep_desc if self.in_media_section else self.hkep_desc
        for i in range(MAX_HKEPS):
            if not target[i].address:
                target[i] = hkep_desc
                break
        else:
            print("Warning: too many hkep entries")
        if self.in_media_section:
            self.current_media.hkep = True
        return None

    def process_privacy(self, value: bytes) -> Optional[str]:
        split = value.split(b';')
        if len(split) != 6:
            return "invalid privacy attribute line"
        privacy = PrivacyDescriptor()
        for pair in split:
            key, val = pair.split(b'=')
            key = key.strip().decode('utf-8')
            val = val.strip().decode('utf-8')
            if key == "protocol":
                enum, err = lookup_enum(val, False)
                if err or enum.s not in (
                    "RTP", "RTP_KV", "UDP", "UDP_KV", "RTSP", "RTSP_KV",
                    "SRT", "SRTP", "USB", "USB_KV"
                ):
                    return "invalid privacy attribute line"
                privacy.protocol = enum
            elif key == "mode":
                enum, err = lookup_enum(val, False)
                if err or enum.s not in (
                    "AES-128-CTR", "AES-128-CTR_CMAC-64", "AES-128-CTR_CMAC-64-AAD",
                    "ECDH_AES-128-CTR", "ECDH_AES-128-CTR_CMAC-64", "ECDH_AES-128-CTR_CMAC-64-AAD",
                    "AES-256-CTR", "AES-256-CTR_CMAC-64", "AES-256-CTR_CMAC-64-AAD",
                    "ECDH_AES-256-CTR", "ECDH_AES-256-CTR_CMAC-64", "ECDH_AES-256-CTR_CMAC-64-AAD",
                    "AES-128-GMAC-128", "AES-256-GMAC-128",
                    "ECDH_AES-128-GMAC-128", "ECDH_AES-256-GMAC-128"
                ):
                    return "invalid privacy attribute line"
                privacy.mode = enum
            elif key == "iv":
                if len(val) != 16 or not re.match(r'^[0-9A-Fa-f]+$', val):
                    return "invalid privacy attribute line"
                privacy.iv = val
            elif key == "key_generator":
                if len(val) != 32 or not re.match(r'^[0-9A-Fa-f]+$', val):
                    return "invalid privacy attribute line"
                privacy.key_generator = val
            elif key == "key_version":
                if len(val) != 8 or not re.match(r'^[0-9A-Fa-f]+$', val):
                    return "invalid privacy attribute line"
                privacy.key_version = val
            elif key == "key_id":
                if len(val) != 16 or not re.match(r'^[0-9A-Fa-f]+$', val):
                    return "invalid privacy attribute line"
                privacy.key_id = val
            else:
                return "invalid privacy attribute line"

        if self.in_media_section:
            self.current_media.privacy_desc = privacy
            self.current_media.privacy = True
        else:
            self.privacy_desc = privacy

        return None

    def process_session_control(self, value: bytes) -> Optional[str]:
        val = value.decode('utf-8')
        if self.in_media_section:
            self.current_media.sub_stream_control = val
        else:
            self.session_control = val
        return None

    def process_extmap(self, value: bytes) -> Optional[str]:
        split = value.split(b' ')
        if len(split) < 2:
            return "invalid extmap line"
        split_id = split[0].split(b'/')
        if not split_id:
            return "invalid extmap id"
        if len(split_id) > 1:
            try:
                id_val = int(split_id[0])
                if id_val > 256:
                    raise ValueError
                direction = split_id[1].decode('utf-8')
            except ValueError:
                return "invalid extmap id"
            uri = split[1].decode('utf-8')
        else:
            try:
                id_val = int(split_id[0])
                if id_val > 256:
                    raise ValueError
            except ValueError:
                return "invalid extmap id"
            direction = "sendonly"
            uri = split[1].decode('utf-8')
        if direction != "sendonly":
            return "invalid extmap direction"
        extmap = ExtmapDescriptor()
        extmap.id = id_val
        extmap.direction = direction
        extmap.uri = uri
        target = self.ext_map if not self.in_media_section else self.current_media.ext_map
        for i in range(MAX_EXTMAPS):
            if not target[i].uri:
                target[i] = extmap
                break
        else:
            return "too many extmap entries"
        return None

    def process_mid(self, value: bytes) -> Optional[str]:
        if not self.in_media_section:
            return "unexpected mid attribute"
        self.current_media.media_name = value.decode('utf-8')
        return None

    def process_setup(self, value: Optional[bytes]) -> Optional[str]:
        """a=setup: RFC 4145 connection role. The writer emits "passive" for
        TCP-based transports (e.g. USB, RTSP); accept the standard roles. There
        is no model field - the node is always the passive (listening) side -
        so this validates the role and otherwise ignores it."""
        role = value.decode('utf-8') if value else ""
        if role not in ("active", "passive", "actpass", "holdconn"):
            return f"invalid setup attribute '{role}'"
        return None

    def process_p_time(self, value: bytes) -> Optional[str]:
        if not self.in_media_section:
            return "unexpected ptime attribute"
        try:
            f = float(value)
            if f == 0.0:
                raise ValueError
        except ValueError:
            return "invalid ptime value"
        self.current_media.p_time_us = int(f * 1000)
        return None

    def process_max_p_time(self, value: bytes) -> Optional[str]:
        if not self.in_media_section:
            return "unexpected maxptime attribute"
        try:
            f = float(value)
            if f == 0.0:
                raise ValueError
        except ValueError:
            return "invalid maxptime value"
        self.current_media.max_p_time_us = int(f * 1000)
        return None

    def process_frame_count(self, value: bytes) -> Optional[str]:
        if not self.in_media_section:
            return "unexpected framecount attribute"
        try:
            count = int(value)
            if count == 0:
                raise ValueError
        except ValueError:
            return "invalid framecount value"
        self.current_media.frame_count = count
        return None

    def process_frame_rate(self, value: bytes) -> Optional[str]:
        if not self.in_media_section:
            return "unexpected framerate attribute"
        try:
            f = float(value)
        except ValueError:
            return "invalid framerate value"
        if b'.' in value:
            self.current_media.frame_rate_numerator = int(math.trunc(f * 1001.0 + 0.5))
            self.current_media.frame_rate_denominator = 1001
        else:
            self.current_media.frame_rate_numerator = int(f)
            self.current_media.frame_rate_denominator = 1
        return None

    # Parameter Processing Methods
    def process_parameter_sampling(self, value: bytes) -> Optional[str]:
        enum, err = lookup_enum(value.decode('utf-8'), True)
        if err:
            return err
        self.current_media.sampling = enum
        return None

    def process_parameter_width(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.width = int(value)
        except ValueError:
            return "invalid width value"
        return None

    def process_parameter_height(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.height = int(value)
        except ValueError:
            return "invalid height value"
        return None

    def process_parameter_depth(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.depth = int(value)
        except ValueError:
            return "invalid depth value"
        return None

    def process_parameter_colorimetry(self, value: bytes) -> Optional[str]:
        enum, err = lookup_enum(value.decode('utf-8'), True)
        if err:
            return err
        self.current_media.colorimetry = enum
        return None

    def process_parameter_range(self, value: bytes) -> Optional[str]:
        enum, err = lookup_enum(value.decode('utf-8'), True)
        if err:
            return err
        self.current_media.color_range = enum
        return None

    def process_parameter_exactframerate(self, value: bytes) -> Optional[str]:
        split = value.split(b'/')
        if len(split) > 2:
            return "invalid exactframerate value"
        try:
            n = int(split[0])
            d = int(split[1]) if len(split) == 2 else 1
        except ValueError:
            return "invalid numerator or denominator value"
        self.current_media.exact_frame_rate_numerator = n
        self.current_media.exact_frame_rate_denominator = d
        return None

    def process_parameter_pm(self, value: bytes) -> Optional[str]:
        enum, err = lookup_enum(value.decode('utf-8'), True)
        if err:
            return err
        self.current_media.packing_mode = enum
        return None

    def process_parameter_ssn(self, value: bytes) -> Optional[str]:
        self.current_media.smpte_standard_number = value.decode('utf-8')
        return None

    def process_parameter_ipmx(self, value: bytes) -> Optional[str]:
        if value is not None:
            return "invalid IPMX value"
        self.current_media.ipmx = True
        return None

    def process_parameter_tp(self, value: bytes) -> Optional[str]:
        enum, err = lookup_enum(value.decode('utf-8'), True)
        if err:
            return err
        self.current_media.sender_type = enum
        return None

    def process_parameter_chroma_position(self, value: bytes) -> Optional[str]:
        split = value.split(b',')
        if len(split) > 2:
            return "invalid chroma position value"
        try:
            cb = int(split[0])
            cr = int(split[1]) if len(split) == 2 else cb
        except ValueError:
            return "invalid chroma position Cb or Cr value"
        self.current_media.chroma_position_cb = cb
        self.current_media.chroma_position_cr = cr
        return None

    def process_parameter_top_field_first(self, value: bytes) -> Optional[str]:
        if value is not None:
            return "invalid top-field-first value"
        self.current_media.top_field_first = True
        return None

    def process_parameter_interlace(self, value: bytes) -> Optional[str]:
        if value is not None:
            return "invalid interlace value"
        self.current_media.interlaced = True
        return None

    def process_parameter_gamma(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.gamma = float(value)
        except ValueError:
            return "invalid gamma value"
        return None

    def process_parameter_htotal(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.h_total = int(value)
        except ValueError:
            return "invalid HTotal value"
        return None

    def process_parameter_vtotal(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.v_total = int(value)
        except ValueError:
            return "invalid VTotal value"
        return None

    def process_parameter_measuredpixclk(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.measured_pix_clk = int(value)
        except ValueError:
            return "invalid measuredpixclk value"
        return None

    def process_parameter_measuredsamplerate(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.measured_sample_rate = int(value)
        except ValueError:
            return "invalid measuredsamplerate value"
        return None

    def process_parameter_segmented(self, value: bytes) -> Optional[str]:
        if value is not None:
            return "invalid segmented value"
        self.current_media.segmented = True
        return None

    def process_parameter_maxudp(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.max_udp = int(value)
        except ValueError:
            return "invalid MAXUDP value"
        return None

    def process_parameter_par(self, value: bytes) -> Optional[str]:
        value = value.strip(b'"')
        split = value.split(b':')
        if len(split) > 2:
            return "invalid PAR value"
        try:
            w = int(split[0])
            h = int(split[1]) if len(split) == 2 else 1
        except ValueError:
            return "invalid PAR width or height value"
        self.current_media.picture_aspect_ratio_width = w
        self.current_media.picture_aspect_ratio_height = h
        return None

    def process_parameter_tcs(self, value: bytes) -> Optional[str]:
        enum, err = lookup_enum(value.decode('utf-8'), True)
        if err:
            return err
        self.current_media.transfer_characteristic = enum
        return None

    def process_parameter_troff(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.troff = int(value)
        except ValueError:
            return "invalid TROFF value"
        return None

    def process_parameter_cmax(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.cmax = int(value)
        except ValueError:
            return "invalid CMAX value"
        return None

    def process_parameter_transmode(self, value: bytes) -> Optional[str]:
        try:
            v = int(value)
            self.current_media.jxsv_trans_mode = EnumId("out-of-order-allowed" if v == 0 else "sequential-only")
        except ValueError:
            return "invalid transmode value"
        return None

    def process_parameter_packetmode(self, value: bytes) -> Optional[str]:
        try:
            v = int(value)
            self.current_media.jxsv_packet_mode = EnumId("codestream" if v == 0 else "slice")
        except ValueError:
            return "invalid packetmode value"
        return None

    def process_parameter_profile(self, value: bytes) -> Optional[str]:
        enum, err = lookup_enum(value.decode('utf-8'), True)
        if err:
            return err
        self.current_media.profile = enum
        return None

    def process_parameter_level(self, value: bytes) -> Optional[str]:
        enum, err = lookup_enum(value.decode('utf-8'), True)
        if err:
            return err
        self.current_media.level = enum
        return None

    def process_parameter_sublevel(self, value: bytes) -> Optional[str]:
        enum, err = lookup_enum(value.decode('utf-8'), True)
        if err:
            return err
        self.current_media.sub_level = enum
        return None

    def process_parameter_fbblevel(self, value: bytes) -> Optional[str]:
        enum, err = lookup_enum(value.decode('utf-8'), True)
        if err:
            return err
        self.current_media.fbb_level = enum
        return None

    def process_parameter_did_sdid(self, value: bytes) -> Optional[str]:
        self.current_media.did_sdid = value.decode('utf-8')
        return None

    def process_parameter_vpid_code(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.vpid_code = int(value)
        except ValueError:
            return "invalid VPID_Code value"
        return None

    def process_parameter_channel_order(self, value: bytes) -> Optional[str]:
        self.current_media.channel_order = value.decode('utf-8')
        return None

    def process_parameter_emphasis(self, value: bytes) -> Optional[str]:
        self.current_media.emphasis = value.decode('utf-8')
        return None

    def process_parameter_streamtype(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.aac_stream_type = int(value)
        except ValueError:
            return "invalid streamType value"
        return None

    def process_parameter_mode(self, value: bytes) -> Optional[str]:
        self.current_media.aac_mode = value.decode('utf-8')
        return None

    def process_parameter_config(self, value: bytes) -> Optional[str]:
        self.current_media.aac_config = value.decode('utf-8')
        return None

    def process_parameter_cpresent(self, value: bytes) -> Optional[str]:
        try:
            v = int(value)
            self.current_media.aac_config_present = v != 0
        except ValueError:
            return "invalid cpresent value"
        return None

    def process_parameter_objecttype(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.aac_object_type = int(value)
        except ValueError:
            return "invalid objectType/object value"
        return None

    def process_parameter_object(self, value: bytes) -> Optional[str]:
        return self.process_parameter_objecttype(value)

    def process_parameter_constantduration(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.aac_constant_duration = int(value)
        except ValueError:
            return "invalid constantDuration value"
        return None

    def process_parameter_maxdisplacement(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.aac_max_displacement = int(value)
        except ValueError:
            return "invalid maxDisplacement value"
        return None

    def process_parameter_de_interleavebuffersize(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.aac_de_interleave_buffer_size = int(value)
        except ValueError:
            return "invalid de-interleaveBufferSize value"
        return None

    def process_parameter_sizelength(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.aac_size_length = int(value)
        except ValueError:
            return "invalid sizeLength value"
        return None

    def process_parameter_indexlength(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.aac_index_length = int(value)
        except ValueError:
            return "invalid indexLength value"
        return None

    def process_parameter_indexdeltalength(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.aac_index_delta_length = int(value)
        except ValueError:
            return "invalid indexDeltaLength value"
        return None

    def process_parameter_ctsdeltalength(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.aac_cts_delta_length = int(value)
        except ValueError:
            return "invalid CTSDeltaLength value"
        return None

    def process_parameter_dtsdeltalength(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.aac_dts_delta_length = int(value)
        except ValueError:
            return "invalid DTSDeltaLength value"
        return None

    def process_parameter_randomaccessindication(self, value: bytes) -> Optional[str]:
        try:
            v = int(value)
            self.current_media.aac_random_access_indication = v != 0
        except ValueError:
            return "invalid randomAccessIndication value"
        return None

    def process_parameter_bitrate(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.aac_bitrate = int(value)
        except ValueError:
            return "invalid bitrate value"
        return None

    def process_parameter_tsmode(self, value: bytes) -> Optional[str]:
        enum, err = lookup_enum(value.decode('utf-8'), True)
        if err:
            return err
        self.current_media.ts_mode = enum
        return None

    def process_parameter_tsdelay(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.ts_delay = int(value)
        except ValueError:
            return "invalid TSDELAY value"
        return None

    def process_parameter_profile_level_id(self, value: bytes) -> Optional[str]:
        self.current_media.codec_profile_level_id = value.decode('utf-8')
        return None

    def process_parameter_sprop_parameter_sets(self, value: bytes) -> Optional[str]:
        self.current_media.h264_parameter_sets = value.decode('utf-8') if value else ""
        return None

    def process_parameter_packetization_mode(self, value: bytes) -> Optional[str]:
        try:
            v = int(value)
            if v not in (0, 1, 2):
                raise ValueError
            self.current_media.h264_packetization_mode = v
        except ValueError:
            return "invalid packetization-mode value"
        return None

    def process_parameter_sprop_interleaving_depth(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.h264_interleaving_depth = int(value)
        except ValueError:
            return "invalid sprop-interleaving-depth value"
        return None

    def process_parameter_sprop_deint_buf_req(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.h264_deint_buf_req = int(value)
        except ValueError:
            return "invalid sprop-deint-buf-req value"
        return None

    def process_parameter_sprop_init_buf_time(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.h264_init_buf_time = int(value)
        except ValueError:
            return "invalid sprop-init-buf-time value"
        return None

    def process_parameter_sprop_max_don_diff(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.h26x_max_don_diff = int(value)
        except ValueError:
            return "invalid sprop-max-don-diff value"
        return None

    def process_parameter_profile_space(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.h265_profile_space = int(value)
        except ValueError:
            return "invalid profile-space value"
        return None

    def process_parameter_profile_id(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.h265_profile_id = int(value)
        except ValueError:
            return "invalid profile-id value"
        return None

    def process_parameter_tier_flag(self, value: bytes) -> Optional[str]:
        try:
            v = int(value)
            self.current_media.h265_tier_flag = v == 1
        except ValueError:
            return "invalid tier-flag value"
        return None

    def process_parameter_level_id(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.h265_level_id = int(value)
        except ValueError:
            return "invalid level-id value"
        return None

    def process_parameter_interop_constraints(self, value: bytes) -> Optional[str]:
        self.current_media.h265_interop_constraints = value.decode('utf-8')
        return None

    def process_parameter_profile_compatibility_indicator(self, value: bytes) -> Optional[str]:
        self.current_media.h265_profile_compatibility_indicator = value.decode('utf-8')
        return None

    def process_parameter_tx_mode(self, value: bytes) -> Optional[str]:
        enum, err = lookup_enum(value.decode('utf-8'), False)
        if err:
            return "invalid tx-mode value"
        self.current_media.h265_tx_mode = enum
        return None

    def process_parameter_sprop_vps(self, value: bytes) -> Optional[str]:
        self.current_media.h265_vps = value.decode('utf-8') if value else ""
        return None

    def process_parameter_sprop_sps(self, value: bytes) -> Optional[str]:
        self.current_media.h265_sps = value.decode('utf-8') if value else ""
        return None

    def process_parameter_sprop_pps(self, value: bytes) -> Optional[str]:
        self.current_media.h265_pps = value.decode('utf-8') if value else ""
        return None

    def process_parameter_sprop_depack_buf_nalus(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.h265_depack_buf_nalus = int(value)
        except ValueError:
            return "invalid sprop-depack-buf-nalus value"
        return None

    def process_parameter_sprop_depack_buf_bytes(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.h265_depack_buf_bytes = int(value)
        except ValueError:
            return "invalid sprop-depack-buf-bytes value"
        return None

    def process_parameter_sprop_segmentation_id(self, value: bytes) -> Optional[str]:
        try:
            self.current_media.h265_segmentation_id = int(value)
        except ValueError:
            return "invalid sprop-segmentation-id value"
        return None

    def process_parameter_sprop_spatial_segmentation_idc(self, value: bytes) -> Optional[str]:
        self.current_media.h265_spatial_segmentation_idc = value.decode('utf-8')
        return None

    def check_sdp_base_requirements(self) -> Optional[str]:
        if not self.username or not self.session_id or not self.session_version or not self.origin_address:
            return "missing o= line"
        if self.session_name is None:
            return "missing s= line"
        if self.primary_media.protocol and self.primary_media.protocol.s in ("RTP/AVP", "TCP/RTP/AVP"):
            if (self.primary_media.port % 2) != 0 and not self.primary_media.rtcp_port:
                return "missing a=rtcp: line with odd RTP port"
            if self.secondary_media is not None:
                if (self.secondary_media.port % 2) != 0 and not self.secondary_media.rtcp_port:
                    return "missing a=rtcp: line with odd RTP port"
            if self.primary_media.port_count != 1 and self.primary_media.rtcp_port:
                return "invalid a=rtcp: line with multiple ports"
            if self.secondary_media is not None:
                if self.secondary_media.port_count != 1 and self.secondary_media.rtcp_port:
                    return "invalid a=rtcp: line with multiple ports"
            if not self.primary_media.rtcp_port and self.primary_media.port:
                self.primary_media.rtcp_port = self.primary_media.port + 1
            if self.secondary_media is not None:
                if not self.secondary_media.rtcp_port and self.secondary_media.port:
                    self.secondary_media.rtcp_port = self.secondary_media.port + 1
        return None


def get_h264_profile_level_from_sdp(profile_level_id: str) -> Tuple[EnumId, EnumId]:
    """
    Convert H.264 profile-level-id string to profile and level enums.
    
    Args:
        profile_level_id: Hexadecimal string representing profile-level-id
        
    Returns:
        Tuple of (profile, level) as EnumId objects
        
    Raises:
        SdpError: If profile_level_id is invalid
    """
    try:
        # Parse as 24-bit hexadecimal value
        value = int(profile_level_id, 16)
        if value > 0xFFFFFF:  # Ensure it fits in 24 bits
            raise ValueError("Value exceeds 24-bit range")
    except ValueError as e:
        raise SdpError(f"invalid profile-level-id value: {e}")
    
    # Extract components: profile_idc : profile-iop : level_idc
    profile_idc = uint8((value >> 16) & 255)
    profile_iop = uint8((value >> 8) & 255)  # 0x80(set0), 0x40(set1), 0x20(set2), 0x10(set3), 0x08(set4), 0x04(set5)
    level_idc = uint8(value & 255)
    
    # Determine level based on level_idc
    if level_idc == 9:
        level = MatroxSdpEnums.CodecLevel1b
    elif level_idc == 10:
        level = MatroxSdpEnums.CodecLevel1
    elif level_idc == 11:
        if profile_idc == 0x42 or profile_idc == 0x4d or profile_idc == 0x58:
            level = MatroxSdpEnums.CodecLevel1b
        else:
            level = MatroxSdpEnums.CodecLevel1_1
    elif level_idc == 12:
        level = MatroxSdpEnums.CodecLevel1_2
    elif level_idc == 13:
        level = MatroxSdpEnums.CodecLevel1_3
    elif level_idc == 20:
        level = MatroxSdpEnums.CodecLevel2
    elif level_idc == 21:
        level = MatroxSdpEnums.CodecLevel2_1
    elif level_idc == 22:
        level = MatroxSdpEnums.CodecLevel2_2
    elif level_idc == 30:
        level = MatroxSdpEnums.CodecLevel3
    elif level_idc == 31:
        level = MatroxSdpEnums.CodecLevel3_1
    elif level_idc == 32:
        level = MatroxSdpEnums.CodecLevel3_2
    elif level_idc == 40:
        level = MatroxSdpEnums.CodecLevel4
    elif level_idc == 41:
        level = MatroxSdpEnums.CodecLevel4_1
    elif level_idc == 42:
        level = MatroxSdpEnums.CodecLevel4_2
    elif level_idc == 50:
        level = MatroxSdpEnums.CodecLevel5
    elif level_idc == 51:
        level = MatroxSdpEnums.CodecLevel5_1
    elif level_idc == 52:
        level = MatroxSdpEnums.CodecLevel5_2
    elif level_idc == 60:
        level = MatroxSdpEnums.CodecLevel6
    elif level_idc == 61:
        level = MatroxSdpEnums.CodecLevel6_1
    elif level_idc == 62:
        level = MatroxSdpEnums.CodecLevel6_2
    else:
        raise SdpError("invalid profile-level-id value: unknown level_idc")
    
    # Determine profile based on profile_idc and profile_iop
    if profile_idc == 0x42:
        if profile_iop == 0x40:
            profile = MatroxSdpEnums.H264ProfileBaselineConstrained
        else:
            profile = MatroxSdpEnums.H264ProfileBaseline
    elif profile_idc == 0x4d:
        profile = MatroxSdpEnums.CodecProfileMain
    elif profile_idc == 0x58:
        profile = MatroxSdpEnums.H264ProfileExtended
    elif profile_idc == 0x64:
        if profile_iop == 0:
            profile = MatroxSdpEnums.H264ProfileHigh
        elif profile_iop == 0x08:
            profile = MatroxSdpEnums.H264ProfileHighProgressive
        elif profile_iop == (0x08 | 0x04):
            profile = MatroxSdpEnums.H264ProfileHighConstrained
        else:
            raise SdpError("invalid profile-level-id value: unknown profile_iop for High profile")
    elif profile_idc == 0x6e:
        if profile_iop == 0:
            profile = MatroxSdpEnums.H264ProfileHigh10
        elif profile_iop == 0x08:
            profile = MatroxSdpEnums.H264ProfileHigh10Progressive
        elif profile_iop == 0x10:
            profile = MatroxSdpEnums.H264ProfileHigh10Intra
        else:
            raise SdpError("invalid profile-level-id value: unknown profile_iop for High 10 profile")
    elif profile_idc == 0x7a:
        if profile_iop == 0:
            profile = MatroxSdpEnums.H264ProfileHigh_422
        elif profile_iop == 0x10:
            profile = MatroxSdpEnums.H264ProfileHighIntra_422
        else:
            raise SdpError("invalid profile-level-id value: unknown profile_iop for High 4:2:2 profile")
    elif profile_idc == 0xf4:
        if profile_iop == 0:
            profile = MatroxSdpEnums.H264ProfileHighPredictive_444
        elif profile_iop == 0x10:
            profile = MatroxSdpEnums.H264ProfileHighIntra_444
        else:
            raise SdpError("invalid profile-level-id value: unknown profile_iop for High 4:4:4 profile")
    elif profile_idc == 0x2c:
        profile = MatroxSdpEnums.H264ProfileCAVLCIntra_444
    else:
        raise SdpError("invalid profile-level-id value: unknown profile_idc")
    
    return profile.value, level.value


def get_h265_profile_level_from_sdp(profile_space: uint8, profile_id: uint8, tier_flag: uint8, level_id: uint8, 
                                   profile_compatibility: str, interop_constraints: str) -> Tuple[EnumId, EnumId, bool]:
    """
    Convert H.265 profile parameters to profile and level enums.
    
    Args:
        profile_space: Profile space (must be 0)
        profile_id: Profile ID (1-11)  
        tier_flag: Tier flag (0=Main, 1=High)
        level_id: Level ID (30-255)
        profile_compatibility: Profile compatibility indicator (hex string)
        interop_constraints: Interop constraints (48-bit hex string)
        
    Returns:
        Tuple of (profile, level, progressive) as (EnumId, EnumId, bool)
        
    Raises:
        SdpError: If any parameter is invalid
    """
    progressive = False
    
    if profile_space != 0:
        raise SdpError("invalid profile_space value")
    
    # Parse interop constraints as 48-bit hex value
    try:
        constraints = int(interop_constraints, 16)
        if constraints > 0xFFFFFFFFFFFF:  # Ensure it fits in 48 bits
            raise ValueError("Value exceeds 48-bit range")
    except ValueError as e:
        raise SdpError(f"invalid interop_constraints value: {e}")
    
    # Check for progressive content
    if ((constraints & GENERAL_PROGRESSIVE_SOURCE_FLAG) != 0 and 
        (constraints & GENERAL_FRAME_ONLY_CONSTRAINT_FLAG) != 0):
        progressive = True
    
    # Determine profile based on profile_id and constraint flags
    if profile_id == 1:
        profile = MatroxSdpEnums.CodecProfileMain  # do not check compatibility flags
        
    elif profile_id == 2:
        if (constraints & GENERAL_ONE_PICTURE_ONLY_CONSTRAINT_FLAG) != 0:
            profile = MatroxSdpEnums.H265ProfileMain10StillPicture
        else:
            profile = MatroxSdpEnums.H265ProfileMain10
            
    elif profile_id == 3:
        profile = MatroxSdpEnums.H265ProfileMainStillPicture
        
    elif profile_id == 4:
        # Complex constraint-based profile determination for profile_id == 4
        constraints_masked = constraints & PROFILE_CONSTRAINTS_MASK
        constraints_intra_masked = constraints & PROFILE_CONSTRAINTS_INTRA_MASK
        constraints_still_masked = constraints & PROFILE_CONSTRAINTS_STILL_MASK
        
        # Check each profile pattern - order matters for fallback logic
        if constraints_masked == (GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                 GENERAL_MAX_10BIT_CONSTRAINT_FLAG |
                                 GENERAL_MAX_8BIT_CONSTRAINT_FLAG |
                                 GENERAL_MAX_422CHROMA_CONSTRAINT_FLAG |
                                 GENERAL_MAX_420CHROMA_CONSTRAINT_FLAG |
                                 GENERAL_MAX_MONOCHROME_CONSTRAINT_FLAG |
                                 GENERAL_LOWER_BIT_RATE_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileMonochrome
            
        elif constraints_masked == (GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                   GENERAL_MAX_10BIT_CONSTRAINT_FLAG |
                                   GENERAL_MAX_422CHROMA_CONSTRAINT_FLAG |
                                   GENERAL_MAX_420CHROMA_CONSTRAINT_FLAG |
                                   GENERAL_MAX_MONOCHROME_CONSTRAINT_FLAG |
                                   GENERAL_LOWER_BIT_RATE_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileMonochrome10
            
        elif constraints_masked == (GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                   GENERAL_MAX_422CHROMA_CONSTRAINT_FLAG |
                                   GENERAL_MAX_420CHROMA_CONSTRAINT_FLAG |
                                   GENERAL_MAX_MONOCHROME_CONSTRAINT_FLAG |
                                   GENERAL_LOWER_BIT_RATE_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileMonochrome12
            
        elif constraints_masked == (GENERAL_MAX_422CHROMA_CONSTRAINT_FLAG |
                                   GENERAL_MAX_420CHROMA_CONSTRAINT_FLAG |
                                   GENERAL_MAX_MONOCHROME_CONSTRAINT_FLAG |
                                   GENERAL_LOWER_BIT_RATE_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileMonochrome16
            
        elif constraints_masked == (GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                   GENERAL_MAX_422CHROMA_CONSTRAINT_FLAG |
                                   GENERAL_MAX_420CHROMA_CONSTRAINT_FLAG |
                                   GENERAL_LOWER_BIT_RATE_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileMain12
            
        elif constraints_masked == (GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                   GENERAL_MAX_10BIT_CONSTRAINT_FLAG |
                                   GENERAL_MAX_422CHROMA_CONSTRAINT_FLAG |
                                   GENERAL_LOWER_BIT_RATE_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileMain10_422
            
        elif constraints_masked == (GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                   GENERAL_MAX_422CHROMA_CONSTRAINT_FLAG |
                                   GENERAL_LOWER_BIT_RATE_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileMain12_422
            
        elif constraints_masked == (GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                   GENERAL_MAX_10BIT_CONSTRAINT_FLAG |
                                   GENERAL_MAX_8BIT_CONSTRAINT_FLAG |
                                   GENERAL_LOWER_BIT_RATE_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileMain_444
            
        elif constraints_masked == (GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                   GENERAL_MAX_10BIT_CONSTRAINT_FLAG |
                                   GENERAL_LOWER_BIT_RATE_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileMain10_444
            
        elif constraints_masked == (GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                   GENERAL_LOWER_BIT_RATE_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileMain12_444
            
        elif constraints_intra_masked == (GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                         GENERAL_MAX_10BIT_CONSTRAINT_FLAG |
                                         GENERAL_MAX_8BIT_CONSTRAINT_FLAG |
                                         GENERAL_MAX_422CHROMA_CONSTRAINT_FLAG |
                                         GENERAL_MAX_420CHROMA_CONSTRAINT_FLAG |
                                         GENERAL_INTRA_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileMainIntra
            
        elif constraints_intra_masked == (GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                         GENERAL_MAX_10BIT_CONSTRAINT_FLAG |
                                         GENERAL_MAX_422CHROMA_CONSTRAINT_FLAG |
                                         GENERAL_MAX_420CHROMA_CONSTRAINT_FLAG |
                                         GENERAL_INTRA_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileMain10Intra
            
        elif constraints_intra_masked == (GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                         GENERAL_MAX_422CHROMA_CONSTRAINT_FLAG |
                                         GENERAL_MAX_420CHROMA_CONSTRAINT_FLAG |
                                         GENERAL_INTRA_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileMain12Intra
            
        elif constraints_intra_masked == (GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                         GENERAL_MAX_10BIT_CONSTRAINT_FLAG |
                                         GENERAL_MAX_422CHROMA_CONSTRAINT_FLAG |
                                         GENERAL_INTRA_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileMain10Intra_422
            
        elif constraints_intra_masked == (GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                         GENERAL_MAX_422CHROMA_CONSTRAINT_FLAG |
                                         GENERAL_INTRA_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileMain12Intra_422
            
        elif constraints_intra_masked == (GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                         GENERAL_MAX_10BIT_CONSTRAINT_FLAG |
                                         GENERAL_MAX_8BIT_CONSTRAINT_FLAG |
                                         GENERAL_INTRA_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileMainIntra_444
            
        elif constraints_intra_masked == (GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                         GENERAL_MAX_10BIT_CONSTRAINT_FLAG |
                                         GENERAL_INTRA_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileMain10Intra_444
            
        elif constraints_intra_masked == (GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                         GENERAL_INTRA_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileMain12Intra_444
            
        elif constraints_intra_masked == GENERAL_INTRA_CONSTRAINT_FLAG:
            profile = MatroxSdpEnums.H265ProfileMain16Intra_444
            
        elif constraints_still_masked == (GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                         GENERAL_MAX_10BIT_CONSTRAINT_FLAG |
                                         GENERAL_MAX_8BIT_CONSTRAINT_FLAG |
                                         GENERAL_INTRA_CONSTRAINT_FLAG |
                                         GENERAL_ONE_PICTURE_ONLY_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileMainStillPicture_444
            
        elif constraints_still_masked == (GENERAL_INTRA_CONSTRAINT_FLAG |
                                         GENERAL_ONE_PICTURE_ONLY_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileMain16StillPicture_444
        else:
            raise SdpError("invalid interop_constraints value")
            
    elif profile_id == 5:
        constraints_masked = constraints & PROFILE_CONSTRAINTS_MASK
        constraints_intra_masked = constraints & PROFILE_CONSTRAINTS_INTRA_MASK
        
        if constraints_masked == (GENERAL_MAX_14BIT_CONSTRAINT_FLAG |
                                 GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                 GENERAL_MAX_10BIT_CONSTRAINT_FLAG |
                                 GENERAL_MAX_8BIT_CONSTRAINT_FLAG |
                                 GENERAL_LOWER_BIT_RATE_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileHighThroughput_444
            
        elif constraints_masked == (GENERAL_MAX_14BIT_CONSTRAINT_FLAG |
                                   GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                   GENERAL_MAX_10BIT_CONSTRAINT_FLAG |
                                   GENERAL_LOWER_BIT_RATE_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileHighThroughput10_444
            
        elif constraints_masked == (GENERAL_MAX_14BIT_CONSTRAINT_FLAG |
                                   GENERAL_LOWER_BIT_RATE_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileHighThroughput14_444
            
        elif constraints_intra_masked == GENERAL_INTRA_CONSTRAINT_FLAG:
            profile = MatroxSdpEnums.H265ProfileHighThroughput16Intra_444
        else:
            raise SdpError("invalid interop_constraints value")
            
    elif profile_id == 9:
        constraints_masked = constraints & PROFILE_CONSTRAINTS_MASK
        
        if constraints_masked == (GENERAL_MAX_14BIT_CONSTRAINT_FLAG |
                                 GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                 GENERAL_MAX_10BIT_CONSTRAINT_FLAG |
                                 GENERAL_MAX_8BIT_CONSTRAINT_FLAG |
                                 GENERAL_MAX_422CHROMA_CONSTRAINT_FLAG |
                                 GENERAL_MAX_420CHROMA_CONSTRAINT_FLAG |
                                 GENERAL_LOWER_BIT_RATE_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileScreenExtendedMain
            
        elif constraints_masked == (GENERAL_MAX_14BIT_CONSTRAINT_FLAG |
                                   GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                   GENERAL_MAX_10BIT_CONSTRAINT_FLAG |
                                   GENERAL_MAX_422CHROMA_CONSTRAINT_FLAG |
                                   GENERAL_MAX_420CHROMA_CONSTRAINT_FLAG |
                                   GENERAL_LOWER_BIT_RATE_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileScreenExtendedMain10
            
        elif constraints_masked == (GENERAL_MAX_14BIT_CONSTRAINT_FLAG |
                                   GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                   GENERAL_MAX_10BIT_CONSTRAINT_FLAG |
                                   GENERAL_MAX_8BIT_CONSTRAINT_FLAG |
                                   GENERAL_LOWER_BIT_RATE_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileScreenExtendedMain_444
            
        elif constraints_masked == (GENERAL_MAX_14BIT_CONSTRAINT_FLAG |
                                   GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                   GENERAL_MAX_10BIT_CONSTRAINT_FLAG |
                                   GENERAL_LOWER_BIT_RATE_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileScreenExtendedMain10_444
        else:
            raise SdpError("invalid interop_constraints value")
            
    elif profile_id == 11:
        constraints_masked = constraints & PROFILE_CONSTRAINTS_MASK
        
        if constraints_masked == (GENERAL_MAX_14BIT_CONSTRAINT_FLAG |
                                 GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                 GENERAL_MAX_10BIT_CONSTRAINT_FLAG |
                                 GENERAL_MAX_8BIT_CONSTRAINT_FLAG |
                                 GENERAL_LOWER_BIT_RATE_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileScreenExtendedHighThroughput_444
            
        elif constraints_masked == (GENERAL_MAX_14BIT_CONSTRAINT_FLAG |
                                   GENERAL_MAX_12BIT_CONSTRAINT_FLAG |
                                   GENERAL_MAX_10BIT_CONSTRAINT_FLAG |
                                   GENERAL_LOWER_BIT_RATE_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileScreenExtendedHighThroughput10_444
            
        elif constraints_masked == (GENERAL_MAX_14BIT_CONSTRAINT_FLAG |
                                   GENERAL_LOWER_BIT_RATE_CONSTRAINT_FLAG):
            profile = MatroxSdpEnums.H265ProfileScreenExtendedHighThroughput14_444
        else:
            raise SdpError("invalid interop_constraints value")
    else:
        raise SdpError("invalid profile")
    
    # Determine level based on tier_flag and level_id
    if tier_flag == 0:  # Main tier
        if level_id == 30:
            level = MatroxSdpEnums.H265LevelMain1
        elif level_id == 60:
            level = MatroxSdpEnums.H265LevelMain2
        elif level_id == 63:
            level = MatroxSdpEnums.H265LevelMain2_1
        elif level_id == 90:
            level = MatroxSdpEnums.H265LevelMain3
        elif level_id == 93:
            level = MatroxSdpEnums.H265LevelMain3_1
        elif level_id == 120:
            level = MatroxSdpEnums.H265LevelMain4
        elif level_id == 123:
            level = MatroxSdpEnums.H265LevelMain4_1
        elif level_id == 150:
            level = MatroxSdpEnums.H265LevelMain5
        elif level_id == 153:
            level = MatroxSdpEnums.H265LevelMain5_1
        elif level_id == 156:
            level = MatroxSdpEnums.H265LevelMain5_2
        elif level_id == 180:
            level = MatroxSdpEnums.H265LevelMain6
        elif level_id == 183:
            level = MatroxSdpEnums.H265LevelMain6_1
        elif level_id == 186:
            level = MatroxSdpEnums.H265LevelMain6_2
        else:
            raise SdpError("invalid tier_flag or level_id")
    else:  # High tier
        if level_id == 30:
            level = MatroxSdpEnums.H265LevelHigh1
        elif level_id == 60:
            level = MatroxSdpEnums.H265LevelHigh2
        elif level_id == 63:
            level = MatroxSdpEnums.H265LevelHigh2_1
        elif level_id == 90:
            level = MatroxSdpEnums.H265LevelHigh3
        elif level_id == 93:
            level = MatroxSdpEnums.H265LevelHigh3_1
        elif level_id == 120:
            level = MatroxSdpEnums.H265LevelHigh4
        elif level_id == 123:
            level = MatroxSdpEnums.H265LevelHigh4_1
        elif level_id == 150:
            level = MatroxSdpEnums.H265LevelHigh5
        elif level_id == 153:
            level = MatroxSdpEnums.H265LevelHigh5_1
        elif level_id == 156:
            level = MatroxSdpEnums.H265LevelHigh5_2
        elif level_id == 180:
            level = MatroxSdpEnums.H265LevelHigh6
        elif level_id == 183:
            level = MatroxSdpEnums.H265LevelHigh6_1
        elif level_id == 186:
            level = MatroxSdpEnums.H265LevelHigh6_2
        elif level_id == 255:
            level = MatroxSdpEnums.H265LevelHigh8_5
        else:
            raise SdpError("invalid tier_flag or level_id")
    
    return profile.value, level.value, progressive


def get_aac_profile_level_from_sdp(profile_level_id: str) -> Tuple[EnumId, EnumId]:
    """
    Convert AAC profile-level-id string to profile and level enums.
    
    Args:
        profile_level_id: Decimal string representing profile-level-id (1-52)
        
    Returns:
        Tuple of (profile, level) as EnumId objects
        
    Raises:
        SdpError: If profile_level_id is invalid
    """
    try:
        # Parse as decimal value (24-bit max)
        value = int(profile_level_id, 10)
        if value > 0xFFFFFF:  # Ensure it fits in 24 bits
            raise ValueError("Value exceeds 24-bit range")
    except ValueError as e:
        raise SdpError(f"invalid profile-level-id value: {e}")
    
    # Map profile-level-id values to profile and level combinations
    if value == 1:
        profile = MatroxSdpEnums.CodecProfileMain
        level = MatroxSdpEnums.CodecLevel1
    elif value == 2:
        profile = MatroxSdpEnums.CodecProfileMain
        level = MatroxSdpEnums.CodecLevel2
    elif value == 3:
        profile = MatroxSdpEnums.CodecProfileMain
        level = MatroxSdpEnums.CodecLevel3
    elif value == 4:
        profile = MatroxSdpEnums.CodecProfileMain
        level = MatroxSdpEnums.CodecLevel4
    elif value == 5:
        profile = MatroxSdpEnums.AacProfileScalable
        level = MatroxSdpEnums.CodecLevel1
    elif value == 6:
        profile = MatroxSdpEnums.AacProfileScalable
        level = MatroxSdpEnums.CodecLevel2
    elif value == 7:
        profile = MatroxSdpEnums.AacProfileScalable
        level = MatroxSdpEnums.CodecLevel3
    elif value == 8:
        profile = MatroxSdpEnums.AacProfileScalable
        level = MatroxSdpEnums.CodecLevel4
    elif value == 9:
        profile = MatroxSdpEnums.AacProfileSpeech
        level = MatroxSdpEnums.CodecLevel1
    elif value == 10:
        profile = MatroxSdpEnums.AacProfileSpeech
        level = MatroxSdpEnums.CodecLevel2
    elif value == 11:
        profile = MatroxSdpEnums.AacProfileSynthetic
        level = MatroxSdpEnums.CodecLevel1
    elif value == 12:
        profile = MatroxSdpEnums.AacProfileSynthetic
        level = MatroxSdpEnums.CodecLevel2
    elif value == 13:
        profile = MatroxSdpEnums.AacProfileSynthetic
        level = MatroxSdpEnums.CodecLevel3
    elif value == 14:
        profile = MatroxSdpEnums.AacProfileHighQuality
        level = MatroxSdpEnums.CodecLevel1
    elif value == 15:
        profile = MatroxSdpEnums.AacProfileHighQuality
        level = MatroxSdpEnums.CodecLevel2
    elif value == 16:
        profile = MatroxSdpEnums.AacProfileHighQuality
        level = MatroxSdpEnums.CodecLevel3
    elif value == 17:
        profile = MatroxSdpEnums.AacProfileHighQuality
        level = MatroxSdpEnums.CodecLevel4
    elif value == 18:
        profile = MatroxSdpEnums.AacProfileHighQuality
        level = MatroxSdpEnums.CodecLevel5
    elif value == 19:
        profile = MatroxSdpEnums.AacProfileHighQuality
        level = MatroxSdpEnums.CodecLevel6
    elif value == 20:
        profile = MatroxSdpEnums.AacProfileHighQuality
        level = MatroxSdpEnums.CodecLevel7
    elif value == 21:
        profile = MatroxSdpEnums.AacProfileHighQuality
        level = MatroxSdpEnums.CodecLevel8
    elif value == 22:
        profile = MatroxSdpEnums.AacProfileLowDelay
        level = MatroxSdpEnums.CodecLevel1
    elif value == 23:
        profile = MatroxSdpEnums.AacProfileLowDelay
        level = MatroxSdpEnums.CodecLevel2
    elif value == 24:
        profile = MatroxSdpEnums.AacProfileLowDelay
        level = MatroxSdpEnums.CodecLevel3
    elif value == 25:
        profile = MatroxSdpEnums.AacProfileLowDelay
        level = MatroxSdpEnums.CodecLevel4
    elif value == 26:
        profile = MatroxSdpEnums.AacProfileLowDelay
        level = MatroxSdpEnums.CodecLevel5
    elif value == 27:
        profile = MatroxSdpEnums.AacProfileLowDelay
        level = MatroxSdpEnums.CodecLevel6
    elif value == 28:
        profile = MatroxSdpEnums.AacProfileLowDelay
        level = MatroxSdpEnums.CodecLevel7
    elif value == 29:
        profile = MatroxSdpEnums.AacProfileLowDelay
        level = MatroxSdpEnums.CodecLevel8
    elif value == 30:
        profile = MatroxSdpEnums.AacProfileNatural
        level = MatroxSdpEnums.CodecLevel1
    elif value == 31:
        profile = MatroxSdpEnums.AacProfileNatural
        level = MatroxSdpEnums.CodecLevel2
    elif value == 32:
        profile = MatroxSdpEnums.AacProfileNatural
        level = MatroxSdpEnums.CodecLevel3
    elif value == 33:
        profile = MatroxSdpEnums.AacProfileNatural
        level = MatroxSdpEnums.CodecLevel4
    elif value == 34:
        profile = MatroxSdpEnums.AacProfileMobile
        level = MatroxSdpEnums.CodecLevel1
    elif value == 35:
        profile = MatroxSdpEnums.AacProfileMobile
        level = MatroxSdpEnums.CodecLevel2
    elif value == 36:
        profile = MatroxSdpEnums.AacProfileMobile
        level = MatroxSdpEnums.CodecLevel3
    elif value == 37:
        profile = MatroxSdpEnums.AacProfileMobile
        level = MatroxSdpEnums.CodecLevel4
    elif value == 38:
        profile = MatroxSdpEnums.AacProfileMobile
        level = MatroxSdpEnums.CodecLevel5
    elif value == 39:
        profile = MatroxSdpEnums.AacProfileMobile
        level = MatroxSdpEnums.CodecLevel6
    elif value == 40:
        profile = MatroxSdpEnums.AacProfileAAC
        level = MatroxSdpEnums.CodecLevel1
    elif value == 41:
        profile = MatroxSdpEnums.AacProfileAAC
        level = MatroxSdpEnums.CodecLevel2
    elif value == 42:
        profile = MatroxSdpEnums.AacProfileAAC
        level = MatroxSdpEnums.CodecLevel4
    elif value == 43:
        profile = MatroxSdpEnums.AacProfileAAC
        level = MatroxSdpEnums.CodecLevel5
    elif value == 44:
        profile = MatroxSdpEnums.AacProfileHighEfficiencyAAC
        level = MatroxSdpEnums.CodecLevel2
    elif value == 45:
        profile = MatroxSdpEnums.AacProfileHighEfficiencyAAC
        level = MatroxSdpEnums.CodecLevel3
    elif value == 46:
        profile = MatroxSdpEnums.AacProfileHighEfficiencyAAC
        level = MatroxSdpEnums.CodecLevel4
    elif value == 47:
        profile = MatroxSdpEnums.AacProfileHighEfficiencyAAC
        level = MatroxSdpEnums.CodecLevel5
    elif value == 48:
        profile = MatroxSdpEnums.AacProfileHighEfficiencyAAC
        level = MatroxSdpEnums.CodecLevel2
    elif value == 49:
        profile = MatroxSdpEnums.AacProfileHighEfficiencyAAC
        level = MatroxSdpEnums.CodecLevel3
    elif value == 50:
        profile = MatroxSdpEnums.AacProfileHighEfficiencyAAC
        level = MatroxSdpEnums.CodecLevel4
    elif value == 51:
        profile = MatroxSdpEnums.AacProfileHighEfficiencyAAC
        level = MatroxSdpEnums.CodecLevel5
    elif value == 52:
        profile = MatroxSdpEnums.AacProfileLowDelay
        level = MatroxSdpEnums.CodecLevel1
    else:
        raise SdpError("invalid profile-level-id value")
    
    return profile.value, level.value

