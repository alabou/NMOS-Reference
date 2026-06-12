# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Enum system for NMOS JSON property names and constants.

Provides identity-comparable enum entries via a singleton registry.

Key design:
- EnumId instances are interned: EnumRegistry.get("source_id") always returns
  the same object for the same string.
- Comparison uses 'is' (identity), not '==' (equality). Identity comparison
  is O(1).
- Thread-safe: the registry uses a lock for concurrent access.
- The empty string "" is a valid enum (used for unknown/auto members).

Usage:
    from nmos.enums import EnumId, EnumRegistry

    # Register constants (module level)
    SourceId = EnumRegistry.get("source_id")
    Format = EnumRegistry.get("format")

    # Compare by identity
    if name is SourceId:
        ...

    # Lookup from JSON string (auto-creates if unknown)
    enum_id = EnumRegistry.lookup("source_id", auto=True)
"""

from __future__ import annotations

import threading


class EnumId:
    """Identity-comparable enum entry wrapping a JSON property name string.

    Two EnumId objects with the same string are guaranteed to be the same
    object (via EnumRegistry), so comparison uses 'is'.

    Do not instantiate directly -- use EnumRegistry.get() instead.
    """

    __slots__ = ("_s",)

    def __init__(self, s: str) -> None:
        self._s = s

    @property
    def s(self) -> str:
        """The underlying string value."""
        return self._s

    def __str__(self) -> str:
        return self._s

    def __repr__(self) -> str:
        return f"EnumId({self._s!r})"

    # Hash by string value so that 'is' and '==' both work correctly.
    # Two EnumIds with the same string are the same object (via registry),
    # so identity ('is') is the fast path. But '==' also works as a
    # safety net for code that uses equality instead of identity.
    def __hash__(self) -> int:
        return hash(self._s)

    def __eq__(self, other: object) -> bool:
        if self is other:
            return True
        if isinstance(other, EnumId):
            return self._s == other._s
        return NotImplemented


class EnumRegistry:
    """Thread-safe global registry ensuring one EnumId per string.

    Uses an entries map plus a mutex. Retargeting is implicit: get() always
    returns the canonical instance, so no separate retarget step is needed.
    """

    _lock = threading.Lock()
    _entries: dict[str, EnumId] = {}

    @classmethod
    def get(cls, s: str) -> EnumId:
        """Get or create the canonical EnumId for string s.

        Always returns the same object for the same string. This is the
        primary way to create enum constants at module level.
        """
        with cls._lock:
            entry = cls._entries.get(s)
            if entry is not None:
                return entry
            entry = EnumId(s)
            cls._entries[s] = entry
            return entry

    @classmethod
    def lookup(cls, s: str, auto: bool = False) -> EnumId | None:
        """Look up an existing EnumId by string.

        Args:
            s: The string to look up.
            auto: If True, create a new entry if not found. If False,
                  return None when not found.

        Returns:
            The canonical EnumId, or None if not found and auto is False.
        """
        with cls._lock:
            entry = cls._entries.get(s)
            if entry is not None:
                return entry
            if auto:
                entry = EnumId(s)
                cls._entries[s] = entry
                return entry
            return None

    @classmethod
    def auto_lookup(cls, s: str) -> EnumId:
        """Look up or auto-create an EnumId. Never returns None."""
        result = cls.lookup(s, auto=True)
        assert result is not None  # auto=True guarantees non-None
        return result

    @classmethod
    def alias(cls, primary: str, alternate: str) -> None:
        """Register an alternate string that resolves to the same EnumId as primary.

        Used for dual-namespace decode tolerance: when primary="urn:x-matrox:layer",
        alternate="urn:x-nmos:layer" maps the alternate to the same EnumId.
        Encoding always uses the primary. Decoding accepts both.
        """
        with cls._lock:
            primary_entry = cls._entries.get(primary)
            if primary_entry is None:
                primary_entry = EnumId(primary)
                cls._entries[primary] = primary_entry
            # Map the alternate string to the same EnumId object
            cls._entries[alternate] = primary_entry

    @classmethod
    def reset(cls) -> None:
        """Clear all registered enums. For testing only."""
        with cls._lock:
            cls._entries.clear()

    @classmethod
    def count(cls) -> int:
        """Return the number of registered enums."""
        with cls._lock:
            return len(cls._entries)


# ==========================================================================
# NMOS enum constants
#
# Usage:
#   from nmos import enums
#   sender.Transport.value = enums.TransportRtpMulticast
#
#   from nmos.enums import FormatVideo, DeviceGeneric
#   if device.Type.value is DeviceGeneric: ...
# ==========================================================================

# === Primitives ===
Internal = EnumRegistry.get("internal")
Ptp = EnumRegistry.get("ptp")
IEEE1588_2008 = EnumRegistry.get("IEEE1588-2008")
Instance = EnumRegistry.get("instance")

# === Format ===
FormatVideo = EnumRegistry.get("urn:x-nmos:format:video")
FormatAudio = EnumRegistry.get("urn:x-nmos:format:audio")
FormatData = EnumRegistry.get("urn:x-nmos:format:data")
FormatDataEvent = EnumRegistry.get("urn:x-nmos:format:data.event")
FormatMux = EnumRegistry.get("urn:x-nmos:format:mux")

# === Media types ===
VideoRaw = EnumRegistry.get("video/raw")
VideoCodedJxsv = EnumRegistry.get("video/jxsv")
VideoCodedH264 = EnumRegistry.get("video/H264")
VideoCodedH265 = EnumRegistry.get("video/H265")
AudioRawL8 = EnumRegistry.get("audio/L8")
AudioRawL16 = EnumRegistry.get("audio/L16")
AudioRawL20 = EnumRegistry.get("audio/L20")
AudioRawL24 = EnumRegistry.get("audio/L24")
AudioCodedAm824 = EnumRegistry.get("audio/AM824")
AudioCodedAac = EnumRegistry.get("audio/mpeg4-generic")
AudioCodedAacLATM = EnumRegistry.get("audio/MP4A-LATM")
AudioCodedAacADTS = EnumRegistry.get("audio/MP4A-ADTS")
DataSmpte291 = EnumRegistry.get("video/smpte291")
DataJson = EnumRegistry.get("application/json")
DataUsb = EnumRegistry.get("application/usb")
DataSdp = EnumRegistry.get("application/sdp")
MuxMpeg2TS = EnumRegistry.get("application/MP2T")
MuxAm824 = EnumRegistry.get("application/AM824")
MuxNdi = EnumRegistry.get("application/ndi")
MuxGeneric = EnumRegistry.get("application/mp2t")
MuxRtsp = EnumRegistry.get("application/rtsp")

# === Interlace mode ===
Progressive = EnumRegistry.get("progressive")
InterlacedTff = EnumRegistry.get("interlaced_tff")
InterlacedBff = EnumRegistry.get("interlaced_bff")
InterlacedPsf = EnumRegistry.get("interlaced_psf")

# === Colorimetry ===
BT601 = EnumRegistry.get("BT601")
BT709 = EnumRegistry.get("BT709")
BT2020 = EnumRegistry.get("BT2020")
BT2100 = EnumRegistry.get("BT2100")
ST2065_1 = EnumRegistry.get("ST2065-1")
ST2065_3 = EnumRegistry.get("ST2065-3")
BT601_5 = EnumRegistry.get("BT601-5")
BT709_2 = EnumRegistry.get("BT709-2")
UNSPECIFIED = EnumRegistry.get("UNSPECIFIED")
XYZ = EnumRegistry.get("XYZ")
ALPHA = EnumRegistry.get("ALPHA")

# === Transfer characteristic ===
SDR = EnumRegistry.get("SDR")
HLG = EnumRegistry.get("HLG")
PQ = EnumRegistry.get("PQ")
LINEAR = EnumRegistry.get("LINEAR")
BT2100LINPQ = EnumRegistry.get("BT2100LINPQ")
BT2100LINHLG = EnumRegistry.get("BT2100LINHLG")
ST428_1 = EnumRegistry.get("ST428-1")
DENSITY = EnumRegistry.get("DENSITY")
ST2115LOGS3 = EnumRegistry.get("ST2115LOGS3")

# === Video components ===
Y = EnumRegistry.get("Y")
Cb = EnumRegistry.get("Cb")
Cr = EnumRegistry.get("Cr")
Ct = EnumRegistry.get("Ct")
Cp = EnumRegistry.get("Cp")
R = EnumRegistry.get("R")
G = EnumRegistry.get("G")
B = EnumRegistry.get("B")
A = EnumRegistry.get("A")
DepthMap = EnumRegistry.get("DepthMap")

# === Audio channels ===
L = EnumRegistry.get("L")
C = EnumRegistry.get("C")
LFE = EnumRegistry.get("LFE")
Ls = EnumRegistry.get("Ls")
Rs = EnumRegistry.get("Rs")
Lss = EnumRegistry.get("Lss")
Rss = EnumRegistry.get("Rss")
Lrs = EnumRegistry.get("Lrs")
Rrs = EnumRegistry.get("Rrs")
Lc = EnumRegistry.get("Lc")
Rc = EnumRegistry.get("Rc")
Cs = EnumRegistry.get("Cs")
HI = EnumRegistry.get("HI")
VIN = EnumRegistry.get("VIN")
M1 = EnumRegistry.get("M1")
M2 = EnumRegistry.get("M2")
Lt = EnumRegistry.get("Lt")
Rt = EnumRegistry.get("Rt")
Lst = EnumRegistry.get("Lst")
Rst = EnumRegistry.get("Rst")
S = EnumRegistry.get("S")

# === Color sampling ===
SamplingRGB = EnumRegistry.get("RGB")
SamplingRGBA = EnumRegistry.get("RGBA")
SamplingBGR = EnumRegistry.get("BGR")
SamplingBGRA = EnumRegistry.get("BGRA")
SamplingYCbCr_444 = EnumRegistry.get("YCbCr-4:4:4")
SamplingYCbCr_422 = EnumRegistry.get("YCbCr-4:2:2")
SamplingYCbCr_420 = EnumRegistry.get("YCbCr-4:2:0")
SamplingYCbCr_411 = EnumRegistry.get("YCbCr-4:1:1")
SamplingCLYCbCr_444 = EnumRegistry.get("CLYCbCr-4:4:4")
SamplingCLYCbCr_422 = EnumRegistry.get("CLYCbCr-4:2:2")
SamplingCLYCbCr_420 = EnumRegistry.get("CLYCbCr-4:2:0")
SamplingICtCp_444 = EnumRegistry.get("ICtCp-4:4:4")
SamplingICtCp_422 = EnumRegistry.get("ICtCp-4:2:2")
SamplingICtCp_420 = EnumRegistry.get("ICtCp-4:2:0")
SamplingKey = EnumRegistry.get("KEY")

# === Transport ===
TransportRtp = EnumRegistry.get("urn:x-nmos:transport:rtp")
TransportRtpUnicast = EnumRegistry.get("urn:x-nmos:transport:rtp.ucast")
TransportRtpMulticast = EnumRegistry.get("urn:x-nmos:transport:rtp.mcast")
TransportRtpTcp = EnumRegistry.get("urn:x-matrox:transport:rtp.tcp")
TransportMqtt = EnumRegistry.get("urn:x-nmos:transport:mqtt")
TransportWebSocket = EnumRegistry.get("urn:x-nmos:transport:websocket")
TransportTcp = EnumRegistry.get("urn:x-matrox:transport:tcp")
TransportUdp = EnumRegistry.get("urn:x-matrox:transport:udp")
TransportUdpUnicast = EnumRegistry.get("urn:x-matrox:transport:udp.ucast")
TransportUdpMulticast = EnumRegistry.get("urn:x-matrox:transport:udp.mcast")
TransportUdpMpeg2Ts = EnumRegistry.get("urn:x-matrox:transport:udp.mp2t")
TransportUdpMpeg2TsUnicast = EnumRegistry.get("urn:x-matrox:transport:udp.mp2t.ucast")
TransportUdpMpeg2TsMulticast = EnumRegistry.get("urn:x-matrox:transport:udp.mp2t.mcast")
TransportRtsp = EnumRegistry.get("urn:x-matrox:transport:rtsp")
TransportRtspTcp = EnumRegistry.get("urn:x-matrox:transport:rtsp.tcp")
# Namespace-dependent transports (from namespaces.py)
from nmos.codegen.namespaces import (
    SRT_TRANSPORT_NAMESPACE as _SRT_TR,
    NDI_TRANSPORT_NAMESPACE as _NDI_TR,
    USB_TRANSPORT_NAMESPACE as _USB_TR,
)
TransportSrt = EnumRegistry.get(_SRT_TR + "transport:srt")
TransportSrtMpeg2Ts = EnumRegistry.get(_SRT_TR + "transport:srt.mp2t")
TransportSrtRtp = EnumRegistry.get(_SRT_TR + "transport:srt.rtp")
TransportNdi = EnumRegistry.get(_NDI_TR + "transport:ndi")
TransportUsb = EnumRegistry.get(_USB_TR + "transport:usb")

# === Device type ===
DeviceGeneric = EnumRegistry.get("urn:x-nmos:device:generic")
DevicePipeline = EnumRegistry.get("urn:x-nmos:device:pipeline")

# === Activation mode ===
ActivateImmediate = EnumRegistry.get("activate_immediate")
ActivateScheduledAbsolute = EnumRegistry.get("activate_scheduled_absolute")
ActivateScheduledRelative = EnumRegistry.get("activate_scheduled_relative")

# === Signal status ===
NoSignal = EnumRegistry.get("no_signal")
AwaitingSignal = EnumRegistry.get("awaiting_signal")
SignalPresent = EnumRegistry.get("signal_present")

# === Compatibility status ===
Unconstrained = EnumRegistry.get("unconstrained")
Constrained = EnumRegistry.get("constrained")
ActiveConstraintsViolation = EnumRegistry.get("active_constraints_violation")
NoEssence = EnumRegistry.get("no_essence")
AwaitingEssence = EnumRegistry.get("awaiting_essence")
Unknown = EnumRegistry.get("unknown")
CompliantStream = EnumRegistry.get("compliant_stream")
NonCompliantStream = EnumRegistry.get("non_compliant_stream")

# === Sender type (ST2110-21) ===
SenderType2110TPN = EnumRegistry.get("2110TPN")
SenderType2110TPNL = EnumRegistry.get("2110TPNL")
SenderType2110TPW = EnumRegistry.get("2110TPW")

# === Protocols ===
Http = EnumRegistry.get("http")
Https = EnumRegistry.get("https")
Ws = EnumRegistry.get("ws")
Wss = EnumRegistry.get("wss")
ProtocolMqtt = EnumRegistry.get("mqtt")
ProtocolSecureMqtt = EnumRegistry.get("secure-mqtt")

# === JXSV codec ===
CodeStream = EnumRegistry.get("codestream")
SliceSequential = EnumRegistry.get("slice_sequential")
SliceOutOfOrder = EnumRegistry.get("slice_out_of_order")
JxsvProfileMain420_12 = EnumRegistry.get("Main420.12")
JxsvProfileHigh420_12 = EnumRegistry.get("High420.12")
JxsvProfileMain444_12 = EnumRegistry.get("Main444.12")
JxsvProfileMain4444_12 = EnumRegistry.get("Main4444.12")
JxsvProfileHigh444_12 = EnumRegistry.get("High444.12")
JxsvProfileHigh4444_12 = EnumRegistry.get("High4444.12")
JxsvProfileTDC444_12 = EnumRegistry.get("TDC444.12")
JxsvLevel1k1 = EnumRegistry.get("1k-1")
JxsvLevel2k1 = EnumRegistry.get("2k-1")
JxsvLevel4k1 = EnumRegistry.get("4k-1")
JxsvLevel4k2 = EnumRegistry.get("4k-2")
JxsvLevel4k3 = EnumRegistry.get("4k-3")
JxsvLevel8k1 = EnumRegistry.get("8k-1")
JxsvLevel8k2 = EnumRegistry.get("8k-2")
JxsvLevel8k3 = EnumRegistry.get("8k-3")
JxsvSublevel2bpp = EnumRegistry.get("Sublev2bpp")
JxsvSublevel3bpp = EnumRegistry.get("Sublev3bpp")
JxsvSublevel4bpp = EnumRegistry.get("Sublev4bpp")
JxsvSublevel6bpp = EnumRegistry.get("Sublev6bpp")
JxsvSublevel9bpp = EnumRegistry.get("Sublev9bpp")
JxsvSublevel12bpp = EnumRegistry.get("Sublev12bpp")
JxsvFbblevelUnrestricted = EnumRegistry.get("Unrestricted")
JxsvFbblevelFull = EnumRegistry.get("FbblevFull")
JxsvFbblevel3bpp = EnumRegistry.get("Fbblev3bpp")
JxsvFbblevel4_5bpp = EnumRegistry.get("Fbblev4.5bpp")
JxsvFbblevel8bpp = EnumRegistry.get("Fbblev8bpp")
JxsvFbblevel12bpp = EnumRegistry.get("Fbblev12bpp")

# === H.264 codec ===
H264ProfileBaselineConstrained = EnumRegistry.get("BaselineConstrained")
H264ProfileBaseline = EnumRegistry.get("Baseline")
CodecProfileMain = EnumRegistry.get("Main")
H264ProfileExtended = EnumRegistry.get("Extended")
H264ProfileHigh = EnumRegistry.get("High")
H264ProfileHighProgressive = EnumRegistry.get("HighProgressive")
H264ProfileHighConstrained = EnumRegistry.get("HighConstrained")
H264ProfileHigh10 = EnumRegistry.get("High10")
H264ProfileHigh10Progressive = EnumRegistry.get("High10Progressive")
H264ProfileHigh_422 = EnumRegistry.get("High-422")
H264ProfileHighPredictive_444 = EnumRegistry.get("HighPredictive-444")
H264ProfileHigh10Intra = EnumRegistry.get("High10Intra")
H264ProfileHighIntra_422 = EnumRegistry.get("HighIntra-422")
H264ProfileHighIntra_444 = EnumRegistry.get("HighIntra-444")
H264ProfileCAVLCIntra_444 = EnumRegistry.get("CAVLCIntra-444")

# === Codec levels (shared H.264/H.265) ===
CodecLevel1 = EnumRegistry.get("1")
CodecLevel1b = EnumRegistry.get("1b")
CodecLevel1_1 = EnumRegistry.get("1.1")
CodecLevel1_2 = EnumRegistry.get("1.2")
CodecLevel1_3 = EnumRegistry.get("1.3")
CodecLevel2 = EnumRegistry.get("2")
CodecLevel2_1 = EnumRegistry.get("2.1")
CodecLevel2_2 = EnumRegistry.get("2.2")
CodecLevel3 = EnumRegistry.get("3")
CodecLevel3_1 = EnumRegistry.get("3.1")
CodecLevel3_2 = EnumRegistry.get("3.2")
CodecLevel4 = EnumRegistry.get("4")
CodecLevel4_1 = EnumRegistry.get("4.1")
CodecLevel4_2 = EnumRegistry.get("4.2")
CodecLevel5 = EnumRegistry.get("5")
CodecLevel5_1 = EnumRegistry.get("5.1")
CodecLevel5_2 = EnumRegistry.get("5.2")
CodecLevel6 = EnumRegistry.get("6")
CodecLevel6_1 = EnumRegistry.get("6.1")
CodecLevel6_2 = EnumRegistry.get("6.2")
CodecLevel7 = EnumRegistry.get("7")
CodecLevel8 = EnumRegistry.get("8")

# === H.265 codec ===
H265ProfileMain10 = EnumRegistry.get("Main10")
H265ProfileMain10StillPicture = EnumRegistry.get("Main10StillPicture")
H265ProfileMainStillPicture = EnumRegistry.get("MainStill")
H265ProfileMonochrome = EnumRegistry.get("Monochrome")
H265ProfileMonochrome10 = EnumRegistry.get("Monochrome10")
H265ProfileMonochrome12 = EnumRegistry.get("Monochrome12")
H265ProfileMonochrome16 = EnumRegistry.get("Monochrome16")
H265ProfileMain12 = EnumRegistry.get("Main12")
H265ProfileMain10_422 = EnumRegistry.get("Main10-422")
H265ProfileMain12_422 = EnumRegistry.get("Main12-422")
H265ProfileMain_444 = EnumRegistry.get("Main444")
H265ProfileMain10_444 = EnumRegistry.get("Main10-444")
H265ProfileMain12_444 = EnumRegistry.get("Main12-444")
H265ProfileMainIntra = EnumRegistry.get("MainIntra")
H265ProfileMain10Intra = EnumRegistry.get("Main10Intra")
H265ProfileMain12Intra = EnumRegistry.get("Main12Intra")
H265ProfileMain10Intra_422 = EnumRegistry.get("Main10Intra-422")
H265ProfileMain12Intra_422 = EnumRegistry.get("Main12Intra-422")
H265ProfileMainIntra_444 = EnumRegistry.get("MainIntra-444")
H265ProfileMain10Intra_444 = EnumRegistry.get("Main10Intra-444")
H265ProfileMain12Intra_444 = EnumRegistry.get("Main12Intra-444")
H265ProfileMain16Intra_444 = EnumRegistry.get("Main16Intra-444")
H265ProfileMainStillPicture_444 = EnumRegistry.get("MainStillPicture-444")
H265ProfileMain16StillPicture_444 = EnumRegistry.get("Main16StillPicture-444")
H265ProfileHighThroughput_444 = EnumRegistry.get("HighThroughput-444")
H265ProfileHighThroughput10_444 = EnumRegistry.get("HighThroughput10-444")
H265ProfileHighThroughput14_444 = EnumRegistry.get("HighThroughput14-444")
H265ProfileHighThroughput16Intra_444 = EnumRegistry.get("HighThroughput16Intra-444")
H265ProfileScreenExtendedMain = EnumRegistry.get("ScreenExtendedMain")
H265ProfileScreenExtendedMain10 = EnumRegistry.get("ScreenExtendedMain10")
H265ProfileScreenExtendedMain_444 = EnumRegistry.get("ScreenExtendedMain-444")
H265ProfileScreenExtendedMain10_444 = EnumRegistry.get("ScreenExtendedMain10-444")
H265ProfileScreenExtendedHighThroughput_444 = EnumRegistry.get("ScreenExtendedHighThroughput-444")
H265ProfileScreenExtendedHighThroughput10_444 = EnumRegistry.get("ScreenExtendedHighThroughput10-444")
H265ProfileScreenExtendedHighThroughput14_444 = EnumRegistry.get("ScreenExtendedHighThroughput14-444")
H265LevelMain1 = EnumRegistry.get("Main-1")
H265LevelMain2 = EnumRegistry.get("Main-2")
H265LevelMain2_1 = EnumRegistry.get("Main-2.1")
H265LevelMain3 = EnumRegistry.get("Main-3")
H265LevelMain3_1 = EnumRegistry.get("Main-3.1")
H265LevelMain4 = EnumRegistry.get("Main-4")
H265LevelMain4_1 = EnumRegistry.get("Main-4.1")
H265LevelMain5 = EnumRegistry.get("Main-5")
H265LevelMain5_1 = EnumRegistry.get("Main-5.1")
H265LevelMain5_2 = EnumRegistry.get("Main-5.2")
H265LevelMain6 = EnumRegistry.get("Main-6")
H265LevelMain6_1 = EnumRegistry.get("Main-6.1")
H265LevelMain6_2 = EnumRegistry.get("Main-6.2")
H265LevelHigh1 = EnumRegistry.get("High-1")
H265LevelHigh2 = EnumRegistry.get("High-2")
H265LevelHigh2_1 = EnumRegistry.get("High-2.1")
H265LevelHigh3 = EnumRegistry.get("High-3")
H265LevelHigh3_1 = EnumRegistry.get("High-3.1")
H265LevelHigh4 = EnumRegistry.get("High-4")
H265LevelHigh4_1 = EnumRegistry.get("High-4.1")
H265LevelHigh5 = EnumRegistry.get("High-5")
H265LevelHigh5_1 = EnumRegistry.get("High-5.1")
H265LevelHigh5_2 = EnumRegistry.get("High-5.2")
H265LevelHigh6 = EnumRegistry.get("High-6")
H265LevelHigh6_1 = EnumRegistry.get("High-6.1")
H265LevelHigh6_2 = EnumRegistry.get("High-6.2")
H265LevelHigh8_5 = EnumRegistry.get("High-8.5")

# === AAC codec ===
AacProfileSpeech = EnumRegistry.get("Speech")
AacProfileSynthetic = EnumRegistry.get("Synthetic")
AacProfileScalable = EnumRegistry.get("Scalable")
AacProfileHighQuality = EnumRegistry.get("HighQuality")
AacProfileLowDelay = EnumRegistry.get("LowDelay")
AacProfileNatural = EnumRegistry.get("Natural")
AacProfileMobile = EnumRegistry.get("Mobile")
AacProfileAAC = EnumRegistry.get("AAC")
AacProfileHighEfficiencyAAC = EnumRegistry.get("HighEfficiencyAAC")
AacProfileHighEfficiencyAACv2 = EnumRegistry.get("HighEfficiencyAACv2")
AacProfileLowDelayAAC = EnumRegistry.get("LowDelayAAC")
AacProfileLowDelayAACv2 = EnumRegistry.get("LowDelayAACv2")
AacProfileExtendedHighEfficiencyAAC = EnumRegistry.get("ExtendedHighEfficiencyAAC")

# === H.26x parameter sets ===
InBand = EnumRegistry.get("in_band")
InAndOutOfBand = EnumRegistry.get("in_and_out_of_band")
OutOfBand = EnumRegistry.get("out_of_band")
Strict = EnumRegistry.get("strict")
Static = EnumRegistry.get("static")
Dynamic = EnumRegistry.get("dynamic")
SingleNalUnit = EnumRegistry.get("single_nal_unit")
NonInterleavedNalUnits = EnumRegistry.get("non_interleaved_nal_units")
InterleavedNalUnits = EnumRegistry.get("interleaved_nal_units")
NonInterleavedAccessUnits = EnumRegistry.get("non_interleaved_access_units")
InterleavedAccessUnits = EnumRegistry.get("interleaved_access_units")

# === IS-05 transport params ===
MulticastIp = EnumRegistry.get("multicast_ip")
DestinationIp = EnumRegistry.get("destination_ip")
DestinationPort = EnumRegistry.get("destination_port")
SourceIp = EnumRegistry.get("source_ip")
InterfaceIp = EnumRegistry.get("interface_ip")
SourcePort = EnumRegistry.get("source_port")
FecEnabled = EnumRegistry.get("fec_enabled")
FecDestinationIp = EnumRegistry.get("fec_destination_ip")
FecMode = EnumRegistry.get("fec_mode")
FecType = EnumRegistry.get("fec_type")
FecBlockWidth = EnumRegistry.get("fec_block_width")
FecBlockHeight = EnumRegistry.get("fec_block_height")
Fec1DDestinationPort = EnumRegistry.get("fec1D_destination_port")
Fec2DDestinationPort = EnumRegistry.get("fec2D_destination_port")
Fec1DSourcePort = EnumRegistry.get("fec1D_source_port")
Fec2DSourcePort = EnumRegistry.get("fec2D_source_port")
RtcpEnabled = EnumRegistry.get("rtcp_enabled")
RtcpDestinationIp = EnumRegistry.get("rtcp_destination_ip")
RtcpDestinationPort = EnumRegistry.get("rtcp_destination_port")
RtcpSourcePort = EnumRegistry.get("rtcp_source_port")
RtpEnabled = EnumRegistry.get("rtp_enabled")
Enabled = EnumRegistry.get("enabled")
Protocol = EnumRegistry.get("protocol")
Caller = EnumRegistry.get("caller")
Listener = EnumRegistry.get("listener")
RendezVous = EnumRegistry.get("rendezvous")
Latency = EnumRegistry.get("latency")
StreamId = EnumRegistry.get("stream_id")
SourceName = EnumRegistry.get("source_name")
MachineName = EnumRegistry.get("machine_name")
ServerIp = EnumRegistry.get("server_ip")
ServerPort = EnumRegistry.get("server_port")
ServerHost = EnumRegistry.get("server_host")
DestinationHost = EnumRegistry.get("destination_host")
SourceHost = EnumRegistry.get("source_host")
BrokerTopic = EnumRegistry.get("broker_topic")
BrokerProtocol = EnumRegistry.get("broker_protocol")
BrokerAuthorization = EnumRegistry.get("broker_authorization")
ConnectionStatusBrokerTopic = EnumRegistry.get("connection_status_broker_topic")
ConnectionUri = EnumRegistry.get("connection_uri")
ConnectionAuthorization = EnumRegistry.get("connection_authorization")
ExtAudioLayersMapping = EnumRegistry.get("ext_audio_layers_mapping")
ExtVideoLayersMapping = EnumRegistry.get("ext_video_layers_mapping")
ExtDataLayersMapping = EnumRegistry.get("ext_data_layers_mapping")
ExtPrivacyProtocol = EnumRegistry.get("ext_privacy_protocol")
ExtPrivacyMode = EnumRegistry.get("ext_privacy_mode")
ExtPrivacyIV = EnumRegistry.get("ext_privacy_iv")
ExtPrivacyKeyGenerator = EnumRegistry.get("ext_privacy_key_generator")
ExtPrivacyKeyId = EnumRegistry.get("ext_privacy_key_id")
ExtPrivacyKeyVersion = EnumRegistry.get("ext_privacy_key_version")
ExtPrivacyEcdhSenderPublicKey = EnumRegistry.get("ext_privacy_ecdh_sender_public_key")
ExtPrivacyEcdhReceiverPublicKey = EnumRegistry.get("ext_privacy_ecdh_receiver_public_key")
ExtPrivacyEcdhCurve = EnumRegistry.get("ext_privacy_ecdh_curve")
RTP = EnumRegistry.get("RTP")
RTP_KV = EnumRegistry.get("RTP_KV")
SRT = EnumRegistry.get("SRT")
SRTP = EnumRegistry.get("SRTP")
TCP = EnumRegistry.get("TCP")
TCP_KV = EnumRegistry.get("TCP_KV")
UDP = EnumRegistry.get("UDP")
UDP_KV = EnumRegistry.get("UDP_KV")
USB = EnumRegistry.get("USB")
USB_KV = EnumRegistry.get("USB_KV")
RTSP = EnumRegistry.get("RTSP")
RTSP_KV = EnumRegistry.get("RTSP_KV")
NULL = EnumRegistry.get("NULL")
AES128CTR = EnumRegistry.get("AES-128-CTR")
AES256CTR = EnumRegistry.get("AES-256-CTR")
AES128CTR_CMAC64 = EnumRegistry.get("AES-128-CTR_CMAC-64")
AES256CTR_CMAC64 = EnumRegistry.get("AES-256-CTR_CMAC-64")
AES128CTR_CMAC64_AAD = EnumRegistry.get("AES-128-CTR_CMAC-64-AAD")
AES256CTR_CMAC64_AAD = EnumRegistry.get("AES-256-CTR_CMAC-64-AAD")
AES128_GCM128 = EnumRegistry.get("AES-128-GMAC-128")
AES256_GCM128 = EnumRegistry.get("AES-256-GMAC-128")
ECDH_AES128CTR = EnumRegistry.get("ECDH_AES-128-CTR")
ECDH_AES256CTR = EnumRegistry.get("ECDH_AES-256-CTR")
ECDH_AES128CTR_CMAC64 = EnumRegistry.get("ECDH_AES-128-CTR_CMAC-64")
ECDH_AES256CTR_CMAC64 = EnumRegistry.get("ECDH_AES-256-CTR_CMAC-64")
ECDH_AES128CTR_CMAC64_AAD = EnumRegistry.get("ECDH_AES-128-CTR_CMAC-64-AAD")
ECDH_AES256CTR_CMAC64_AAD = EnumRegistry.get("ECDH_AES-256-CTR_CMAC-64-AAD")
ECDH_AES128_GCM128 = EnumRegistry.get("ECDH_AES-128-GMAC-128")
ECDH_AES256_GCM128 = EnumRegistry.get("ECDH_AES-256-GMAC-128")
Curve_secp256r1 = EnumRegistry.get("secp256r1")
Curve_secp521r1 = EnumRegistry.get("secp521r1")
Curve_25519 = EnumRegistry.get("25519")
Curve_448 = EnumRegistry.get("448")

# === Capabilities ===
CapFormatMediaType = EnumRegistry.get("urn:x-nmos:cap:format:media_type")
CapFormatEventType = EnumRegistry.get("urn:x-nmos:cap:format:event_type")
CapFormatGrainRate = EnumRegistry.get("urn:x-nmos:cap:format:grain_rate")
CapFormatFrameWidth = EnumRegistry.get("urn:x-nmos:cap:format:frame_width")
CapFormatFrameHeight = EnumRegistry.get("urn:x-nmos:cap:format:frame_height")
CapFormatInterlaceMode = EnumRegistry.get("urn:x-nmos:cap:format:interlace_mode")
CapFormatColorspace = EnumRegistry.get("urn:x-nmos:cap:format:colorspace")
CapFormatTransferCharacteristic = EnumRegistry.get("urn:x-nmos:cap:format:transfer_characteristic")
CapFormatColorSampling = EnumRegistry.get("urn:x-nmos:cap:format:color_sampling")
CapFormatComponentDepth = EnumRegistry.get("urn:x-nmos:cap:format:component_depth")
CapFormatChannelCount = EnumRegistry.get("urn:x-nmos:cap:format:channel_count")
CapFormatSampleRate = EnumRegistry.get("urn:x-nmos:cap:format:sample_rate")
CapFormatSampleDepth = EnumRegistry.get("urn:x-nmos:cap:format:sample_depth")
CapFormatBitRate = EnumRegistry.get("urn:x-nmos:cap:format:bit_rate")
CapFormatProfile = EnumRegistry.get("urn:x-nmos:cap:format:profile")
CapFormatLevel = EnumRegistry.get("urn:x-nmos:cap:format:level")
CapFormatSublevel = EnumRegistry.get("urn:x-nmos:cap:format:sublevel")
CapFormatFbblevel = EnumRegistry.get("urn:x-nmos:cap:format:fbblevel")
# === Namespace-dependent capability enums ===
# Built from nmos/codegen/namespaces.py — changing a namespace there and
# reloading changes all affected enum URNs.
from nmos.codegen.namespaces import (
    SYNCMEDIA_CAP_NAMESPACE as _SYNC_CAP,
    INFOBLOCK_CAP_NAMESPACE as _INFO_CAP,
    H26x_CAP_NAMESPACE as _H26x_CAP,
    CLOCKREF_CAP_NAMESPACE as _CLOCK_CAP,
    CHANORDER_CAP_NAMESPACE as _CHAN_CAP,
    HKEP_CAP_NAMESPACE as _HKEP_CAP,
    PRIVACY_CAP_NAMESPACE as _PRIV_CAP,
    USB_CAP_NAMESPACE as _USB_CAP,
)
# Format caps — layers use Matrox-specific namespace
CapFormatVideoLayers = EnumRegistry.get(_SYNC_CAP + "cap:format:video_layers")
CapFormatAudioLayers = EnumRegistry.get(_SYNC_CAP + "cap:format:audio_layers")
CapFormatDataLayers = EnumRegistry.get(_SYNC_CAP + "cap:format:data_layers")
CapFormatConstantBitRate = EnumRegistry.get(_H26x_CAP + "cap:format:constant_bit_rate")
# Transport caps
CapTransportBitRate = EnumRegistry.get("urn:x-nmos:cap:transport:bit_rate")
CapTransportPacketTime = EnumRegistry.get("urn:x-nmos:cap:transport:packet_time")
CapTransportMaxPacketTime = EnumRegistry.get("urn:x-nmos:cap:transport:max_packet_time")
CapTransportSenderType = EnumRegistry.get("urn:x-nmos:cap:transport:st2110_21_sender_type")
CapTransportPacketTransmissionMode = EnumRegistry.get("urn:x-nmos:cap:transport:packet_transmission_mode")
CapTransportParameterSetsFlowMode = EnumRegistry.get(_H26x_CAP + "cap:transport:parameter_sets_flow_mode")
CapTransportParameterSetsTransportMode = EnumRegistry.get(_H26x_CAP + "cap:transport:parameter_sets_transport_mode")
CapTransportChannelOrder = EnumRegistry.get(_CHAN_CAP + "cap:transport:channel_order")
CapTransportHkep = EnumRegistry.get(_HKEP_CAP + "cap:transport:hkep")
CapTransportPrivacy = EnumRegistry.get(_PRIV_CAP + "cap:transport:privacy")
CapTransportClockRefType = EnumRegistry.get(_CLOCK_CAP + "cap:transport:clock_ref_type")
CapTransportSynchronousMedia = EnumRegistry.get(_SYNC_CAP + "cap:transport:synchronous_media")
CapTransportInfoBlock = EnumRegistry.get(_INFO_CAP + "cap:transport:info_block")
CapTransportUsbClass = EnumRegistry.get(_USB_CAP + "cap:transport:usb_class")
# Meta caps
CapMetaEnabled = EnumRegistry.get("urn:x-nmos:cap:meta:enabled")
CapMetaLabel = EnumRegistry.get("urn:x-nmos:cap:meta:label")
CapMetaPreference = EnumRegistry.get("urn:x-nmos:cap:meta:preference")
CapMetaLayerEnabled = EnumRegistry.get(_SYNC_CAP + "cap:meta:layer_enabled")
CapMetaLayer = EnumRegistry.get(_SYNC_CAP + "cap:meta:layer")
CapMetaFormat = EnumRegistry.get(_SYNC_CAP + "cap:meta:format")
CapMetaLayerCompatibilityGroups = EnumRegistry.get(_SYNC_CAP + "cap:meta:layer_compatibility_groups")
CapMetaInfoBlock = EnumRegistry.get(_INFO_CAP + "cap:meta:info_block")

# === Tags ===
TagGroupHint = EnumRegistry.get("urn:x-nmos:tag:grouphint/v1.0")
TagWallHint = EnumRegistry.get("urn:x-nmos:tag:wallhint/v1.0")
TagAssetManufacturer = EnumRegistry.get("urn:x-nmos:tag:asset:manufacturer/v1.0")
TagAssetProduct = EnumRegistry.get("urn:x-nmos:tag:asset:product/v1.0")
TagAssetInstance = EnumRegistry.get("urn:x-nmos:tag:asset:instance-id/v1.0")
TagAssetFunction = EnumRegistry.get("urn:x-nmos:tag:asset:function/v1.0")


# ---------------------------------------------------------------------------
# Dual-namespace decode tolerance
# ---------------------------------------------------------------------------
# Register alternate namespace aliases so that JSON decoding accepts BOTH
# urn:x-matrox: and urn:x-nmos: for all namespace-switchable fields.
# Encoding always uses the configured namespace (from namespaces.py).

_namespace_aliases_registered = False


def register_namespace_aliases() -> None:
    """Register alternate namespace aliases for all registered enums.

    Called after all types are loaded (e.g., from nmos_node.py or test setup)
    to ensure generated type enum keys get aliases too.

    Safe to call multiple times — only runs once.
    """
    global _namespace_aliases_registered
    if _namespace_aliases_registered:
        return
    _namespace_aliases_registered = True

    from nmos.codegen.namespaces import alternate_namespaces
    # Snapshot current entries to avoid mutating dict during iteration
    for primary_str in list(EnumRegistry._entries.keys()):
        for alt in alternate_namespaces(primary_str):
            if alt not in EnumRegistry._entries:
                EnumRegistry.alias(primary_str, alt)
