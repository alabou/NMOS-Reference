# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""IS-11 Stream Compatibility Management.

Uses CCF (MatroxCCF) operators for constraint algebra:
- Inclusion (<=) for validation and compatibility checks
- Constriction (<<) for applying active constraints
- Constriction with adjustment (<&) for intersection
- Normalize for namespace alignment across mux layers

Flow → CCF conversion via get_flow_to_caps() in flow_caps.py.
CCF → Flow write-back via update_*_flow() functions here.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from caps.MatroxCCF import Cons

from nmos.enums import (
    EnumRegistry,
    # Formats
    FormatVideo, FormatAudio, FormatData, FormatDataEvent, FormatMux,
    # Media types
    MuxGeneric, MuxNdi, MuxRtsp, MuxMpeg2TS, MuxAm824,
    VideoRaw, VideoCodedH264, VideoCodedH265, VideoCodedJxsv,
    AudioRawL8, AudioRawL16, AudioRawL20, AudioRawL24,
    # Clock ref
    Ptp, Internal,
    # Interlace / colorspace / transfer characteristic
    Progressive, InterlacedTff, InterlacedBff, InterlacedPsf,
    BT601, BT709, BT2020, BT2100, XYZ, UNSPECIFIED,
    BT601_5, BT709_2, ST2065_1, ST2065_3, ST428_1,
    SDR, HLG, PQ, LINEAR, BT2100LINPQ, BT2100LINHLG, DENSITY, ST2115LOGS3,
    # Color sampling
    SamplingYCbCr_420, SamplingYCbCr_422, SamplingYCbCr_444, SamplingRGB,
    # Packet transmission / parameter-set modes
    CodeStream, SliceSequential, SliceOutOfOrder, SingleNalUnit,
    NonInterleavedNalUnits, InterleavedNalUnits,
    NonInterleavedAccessUnits, InterleavedAccessUnits,
    InBand, InAndOutOfBand, OutOfBand,
    # Codec profiles / levels
    JxsvProfileMain420_12, JxsvProfileHigh420_12,
    JxsvProfileMain444_12, JxsvProfileHigh444_12, JxsvProfileTDC444_12,
    JxsvFbblevelUnrestricted, JxsvFbblevel8bpp, JxsvFbblevel12bpp,
    JxsvLevel4k1, JxsvLevel4k2, JxsvLevel4k3,
    CodecProfileMain, H264ProfileHigh,
    H264ProfileHigh_422, H264ProfileHighIntra_422, H264ProfileHigh10, H264ProfileHigh10Intra,
    H265ProfileMain10_422, H265ProfileMain10Intra_422, H265ProfileMain10,
    H265ProfileMain10Intra, H265ProfileMain10_444, H265ProfileMain10Intra_444,
    CodecLevel3, CodecLevel3_1, CodecLevel3_2, CodecLevel4, CodecLevel4_1, CodecLevel4_2,
    CodecLevel5, CodecLevel5_1, CodecLevel5_2, CodecLevel6, CodecLevel6_1, CodecLevel6_2,
    H265LevelMain3, H265LevelMain3_1, H265LevelMain4, H265LevelHigh4,
    H265LevelMain4_1, H265LevelHigh4_1, H265LevelMain5, H265LevelHigh5,
    H265LevelMain5_1, H265LevelHigh5_1, H265LevelMain5_2, H265LevelHigh5_2,
    H265LevelMain6, H265LevelHigh6, H265LevelMain6_1, H265LevelHigh6_1,
    H265LevelMain6_2, H265LevelHigh6_2,
    # Compatibility status
    Unconstrained, Constrained, ActiveConstraintsViolation, Unknown,
    CompliantStream, NonCompliantStream,
    # Audio channel symbols
    L, R, C, LFE, Ls, Rs, Lrs, Rrs,
    # Capabilities — format
    CapFormatMediaType, CapFormatEventType, CapFormatGrainRate,
    CapFormatFrameWidth, CapFormatFrameHeight, CapFormatInterlaceMode,
    CapFormatColorspace, CapFormatTransferCharacteristic, CapFormatColorSampling,
    CapFormatComponentDepth, CapFormatChannelCount, CapFormatSampleRate,
    CapFormatSampleDepth, CapFormatBitRate, CapFormatConstantBitRate,
    CapFormatProfile, CapFormatLevel, CapFormatSublevel, CapFormatFbblevel,
    CapFormatVideoLayers, CapFormatAudioLayers, CapFormatDataLayers,
    # Capabilities — transport
    CapTransportBitRate, CapTransportPacketTime, CapTransportMaxPacketTime,
    CapTransportSenderType, CapTransportPacketTransmissionMode,
    CapTransportParameterSetsFlowMode, CapTransportParameterSetsTransportMode,
    CapTransportChannelOrder, CapTransportHkep, CapTransportPrivacy,
    CapTransportClockRefType, CapTransportSynchronousMedia,
    CapTransportInfoBlock, CapTransportUsbClass,
    # Capabilities — meta
    CapMetaEnabled, CapMetaLabel, CapMetaPreference,
    CapMetaLayerEnabled, CapMetaLayer, CapMetaFormat,
    CapMetaLayerCompatibilityGroups, CapMetaInfoBlock,
)
from nmos.errors import InvalidParameter, NotAllowed, NotAvailable, UnexpectedError

# ---------------------------------------------------------------------------
# Supported constraint name lists
#
# These define which constraint URNs a sender of each format supports.
# Constraints not in this list are silently ignored if they're transport
# constraints, or rejected if they're format constraints.
# ---------------------------------------------------------------------------

# Supported constraints per format.
# All values use EnumId.s — str directly compatible with CCF.
_META_CONSTRAINTS = [
    CapMetaEnabled.s, CapMetaLabel.s, CapMetaPreference.s,
    CapMetaLayerEnabled.s, CapMetaLayer.s, CapMetaFormat.s,
    CapMetaLayerCompatibilityGroups.s, CapMetaInfoBlock.s,
]

SUPPORTED_VIDEO_CONSTRAINTS: list[str] = _META_CONSTRAINTS + [
    CapFormatMediaType.s, CapFormatGrainRate.s,
    CapFormatFrameWidth.s, CapFormatFrameHeight.s,
    CapFormatInterlaceMode.s, CapFormatColorspace.s,
    CapFormatTransferCharacteristic.s, CapFormatColorSampling.s,
    CapFormatComponentDepth.s,
    CapFormatBitRate.s, CapFormatConstantBitRate.s,
    CapFormatProfile.s, CapFormatLevel.s, CapFormatSublevel.s,
    CapFormatFbblevel.s,
]

SUPPORTED_AUDIO_CONSTRAINTS: list[str] = _META_CONSTRAINTS + [
    CapFormatMediaType.s,
    CapFormatChannelCount.s, CapFormatSampleRate.s, CapFormatSampleDepth.s,
    CapFormatBitRate.s, CapFormatConstantBitRate.s,
    CapFormatProfile.s, CapFormatLevel.s,
]

SUPPORTED_DATA_CONSTRAINTS: list[str] = _META_CONSTRAINTS + [
    CapFormatMediaType.s,
]

SUPPORTED_DATA_EVENT_CONSTRAINTS: list[str] = _META_CONSTRAINTS + [
    CapFormatMediaType.s, CapFormatEventType.s,
]

SUPPORTED_MUX_CONSTRAINTS: list[str] = [
    CapMetaEnabled.s, CapMetaLabel.s, CapMetaPreference.s,
    CapMetaLayerCompatibilityGroups.s, CapMetaInfoBlock.s,
    CapFormatMediaType.s,
    CapFormatVideoLayers.s, CapFormatAudioLayers.s, CapFormatDataLayers.s,
]

# Mux mixed = mux trunk + all sub-flow constraints (supportedMuxMixedConstraints)
SUPPORTED_MUX_MIXED_CONSTRAINTS: list[str] = _META_CONSTRAINTS + [
    CapFormatMediaType.s,
    CapFormatVideoLayers.s, CapFormatAudioLayers.s, CapFormatDataLayers.s,
    # Video sub-constraints
    CapFormatGrainRate.s, CapFormatFrameWidth.s, CapFormatFrameHeight.s,
    CapFormatInterlaceMode.s, CapFormatColorspace.s,
    CapFormatTransferCharacteristic.s, CapFormatColorSampling.s,
    CapFormatComponentDepth.s,
    CapFormatBitRate.s, CapFormatConstantBitRate.s,
    CapFormatProfile.s, CapFormatLevel.s, CapFormatSublevel.s,
    CapFormatFbblevel.s,
    # Audio sub-constraints
    CapFormatChannelCount.s, CapFormatSampleRate.s, CapFormatSampleDepth.s,
]

# Transport constraint URNs (isConstraintNameOfTransportCategory)
_TRANSPORT_CONSTRAINTS: set[str] = {
    CapTransportBitRate.s, CapTransportPacketTime.s,
    CapTransportMaxPacketTime.s, CapTransportSenderType.s,
    CapTransportPacketTransmissionMode.s,
    CapTransportParameterSetsFlowMode.s,
    CapTransportParameterSetsTransportMode.s,
    CapTransportChannelOrder.s,
    CapTransportHkep.s, CapTransportPrivacy.s,
    CapTransportClockRefType.s, CapTransportSynchronousMedia.s,
    CapTransportInfoBlock.s, CapTransportUsbClass.s,
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def get_supported_constraints(format_urn: str) -> list[str]:
    """Return the list of supported constraint URNs for a sender format."""
    if format_urn == FormatVideo.s:
        return SUPPORTED_VIDEO_CONSTRAINTS
    elif format_urn == FormatAudio.s:
        return SUPPORTED_AUDIO_CONSTRAINTS
    elif format_urn == FormatData.s:
        return SUPPORTED_DATA_CONSTRAINTS
    elif format_urn == FormatDataEvent.s:
        return SUPPORTED_DATA_EVENT_CONSTRAINTS
    elif format_urn == FormatMux.s:
        return SUPPORTED_MUX_MIXED_CONSTRAINTS
    else:
        raise InvalidParameter(f"unsupported format: {format_urn}")


def is_constraint_name_supported(format_urn: str, name: str) -> bool:
    """Check if a constraint URN is supported for a given format."""
    return name in get_supported_constraints(format_urn)


def is_constraint_name_of_transport_category(name: str) -> bool:
    """Check if a constraint URN is a transport constraint."""
    return name in _TRANSPORT_CONSTRAINTS


def get_format_from_media_type(media_type: str) -> str:
    """Derive format URN from a media_type string.

    Examples:
        "video/raw"  → FormatVideo.s
        "audio/L24"  → FormatAudio.s
        "video/MP2T" → FormatMux.s
        "application/usb" → FormatData.s
    """
    # NOTE: video/MP2T is OPAQUE (not supported in this implementation).
    # Media types are matched EXACTLY against the canonical enum values —
    # case is significant (application/MP2T is MPEG2-TS over RTP while
    # application/mp2t is the generic/UDP variant, two distinct types).
    _MUX_MEDIA_TYPES = {
        MuxMpeg2TS.s, MuxAm824.s, MuxGeneric.s, MuxNdi.s, MuxRtsp.s,
    }
    mt = media_type or ""
    if mt in _MUX_MEDIA_TYPES:
        return FormatMux.s
    elif mt.startswith("video/"):
        return FormatVideo.s
    elif mt.startswith("audio/"):
        return FormatAudio.s
    elif mt.startswith("data/") or mt.startswith("application/"):
        return FormatData.s
    else:
        return ""


def get_class_from_media_type(media_type: str) -> str:
    """Derive media class from a media_type string.

    Returns "raw", "coded", or "" for the media class.
    """
    # NOTE: video/MP2T is OPAQUE (not supported) — not in this set.
    # Media types are matched EXACTLY against the canonical enum values —
    # case is significant (see get_format_from_media_type).
    _MUX_CLASS = {
        MuxMpeg2TS.s, MuxAm824.s, MuxGeneric.s, MuxNdi.s, MuxRtsp.s,
    }
    mt = media_type or ""

    # Mux (check before video/ prefix since application/* types need priority)
    if mt in _MUX_CLASS:
        return "mux"

    # Video
    if mt == VideoRaw.s:
        return "raw"
    elif mt.startswith("video/"):
        return "coded"

    # Audio — audio/AM824 is ClassAudioCoded (not raw)
    if mt in (AudioRawL8.s, AudioRawL16.s, AudioRawL20.s, AudioRawL24.s):
        return "raw"
    elif mt.startswith("audio/"):
        return "coded"

    # Data
    if mt.startswith("data/") or mt.startswith("application/"):
        return "data"

    return ""


def get_bitmask_from_compatibility_groups(groups: set[int] | None) -> int:
    """Convert compatibility groups to a 64-bit bitmask.

    Per SenderCapabilities.md/ReceiverCapabilities.md: "A Constraint Set without a
    urn:x-matrox:cap:meta:layer_compatibility_groups attribute MUST be assumed as being
    part of all groups." So a missing attribute (None) maps to all-bits-set
    (`0xffffffffffffffff`). An explicit empty set remains 0 (member of no group).
    """
    if groups is None:
        return 0xFFFFFFFFFFFFFFFF
    mask = 0
    for g in groups:
        if 0 <= g <= 63:
            mask |= 1 << g
    return mask


# ---------------------------------------------------------------------------
# Fix-up functions (NMOS-specific corrections)
#
# These operate on a dict of cap_urn → value, adjusting values to produce
# a valid configuration. Called AFTER CCF operations to resolve ambiguities
# between related parameters (e.g., audio/L24 ↔ sample_depth=24).
# ---------------------------------------------------------------------------

def fix_pcm_sample_depth(
    properties: dict[str, Any],
    constraints: dict[str, Any] | None = None,
    verbose: bool = False,
) -> None:
    """Fix PCM sample depth / media_type ambiguity.

    When media_type is audio/L* and sample_depth is specified, one determines
    the other. If sample_depth was explicitly constrained (original=True), it
    takes priority and adjusts media_type. Otherwise media_type determines
    sample_depth.
    """
    try:
        from caps.MatroxCCF import RangeValue, RangeType, Cap
    except ImportError:
        return

    # Get media_type
    mt_cap = properties.get(CapFormatMediaType.s)
    if mt_cap is None:
        return
    mt_values = mt_cap.value.values if mt_cap.value.values else ()
    if not mt_values:
        return
    media_type = str(mt_values[0])

    # Only applies to PCM types
    _PCM_TYPES = {AudioRawL8.s, AudioRawL16.s, AudioRawL20.s, AudioRawL24.s}
    if media_type not in _PCM_TYPES:
        return

    # Check if sample_depth and media_type constraints were "original" (user-specified)
    original_sample_depth = False
    original_media_type = False
    if constraints is not None:
        sd_con = constraints.get(CapFormatSampleDepth.s)
        if sd_con is not None and hasattr(sd_con, 'original'):
            original_sample_depth = sd_con.original
        mt_con = constraints.get(CapFormatMediaType.s)
        if mt_con is not None and hasattr(mt_con, 'original'):
            original_media_type = mt_con.original

    # Get sample_depth value
    sd_cap = properties.get(CapFormatSampleDepth.s)
    sample_depth = 0
    if sd_cap is not None and sd_cap.value.values:
        sample_depth = int(sd_cap.value.values[0])

    _DEPTH_TO_MT = {8: AudioRawL8.s, 16: AudioRawL16.s, 20: AudioRawL20.s, 24: AudioRawL24.s}
    _MT_TO_DEPTH = {AudioRawL8.s: 8, AudioRawL16.s: 16, AudioRawL20.s: 20, AudioRawL24.s: 24}

    if (original_sample_depth or not original_media_type) and sample_depth != 0:
        # sample_depth takes priority → adjust media_type
        new_mt = _DEPTH_TO_MT.get(sample_depth)
        if new_mt is not None:
            properties[CapFormatMediaType.s] = Cap(
                CapFormatMediaType.s,
                RangeValue(values=(new_mt,), type=RangeType.STRING),
            )
            if verbose:
                print(f"    [fix_pcm] sample_depth={sample_depth} → media_type={new_mt}")
    else:
        # media_type takes priority → adjust sample_depth
        new_depth = _MT_TO_DEPTH.get(media_type)
        if new_depth is not None:
            properties[CapFormatSampleDepth.s] = Cap(
                CapFormatSampleDepth.s,
                RangeValue(values=(new_depth,), type=RangeType.INT),
            )
            if verbose:
                print(f"    [fix_pcm] media_type={media_type} → sample_depth={new_depth}")


# ---------------------------------------------------------------------------
# Phase 2: CCF wrapper functions — core compatibility operations
#
# These use CCF operators (inclusion, constriction) to implement IS-11
# compatibility checking and constraint application.
# ---------------------------------------------------------------------------


def _get_cap_value(capset: Any, cap_name: str) -> Any:
    """Extract the first value from a capability in a CapSet.

    Returns None if the capability doesn't exist or has no values.
    Always returns values[0].
    """
    cap = capset.caps.get(cap_name)
    if cap is None:
        return None
    rv = cap.value
    if rv.infinite or rv.empty:
        return None
    if rv.values and len(rv.values) > 0:
        return rv.values[0]
    return None


def _get_cap_str(capset: Any, cap_name: str) -> str | None:
    """Extract a string value from a CapSet capability."""
    v = _get_cap_value(capset, cap_name)
    return str(v) if v is not None else None


def _get_cap_int(capset: Any, cap_name: str) -> int | None:
    """Extract an integer value from a CapSet capability."""
    v = _get_cap_value(capset, cap_name)
    return int(v) if v is not None else None


def _get_cap_bool(capset: Any, cap_name: str) -> bool | None:
    """Extract a boolean value from a CapSet capability."""
    v = _get_cap_value(capset, cap_name)
    return bool(v) if v is not None else None


def _get_cap_rational(capset: Any, cap_name: str) -> tuple[int, int] | None:
    """Extract a rational value as (numerator, denominator) from a CapSet capability."""
    v = _get_cap_value(capset, cap_name)
    if v is None:
        return None
    if isinstance(v, Fraction):
        return (v.numerator, v.denominator)
    return (int(v), 1)


def check_flow_properties_compatibility(
    node: Any,
    flow_caps: Any,
    target_caps_or_cons: Any,
    layer: int = -1,
    format_urn: str = "",
    verbose: bool = False,
) -> bool:
    """Check if flow properties are compatible with capabilities or constraints.

    Uses CCF inclusion: flow_caps (as ConSet) <= target (Caps or Cons).

    The flow_caps CapSet is converted to a ConSet (changes interpretation:
    unspecified = "don't care") and checked for inclusion in the target.
    The target can be either Caps (receiver/sender capabilities) or
    Cons (normalized active constraints).

    Args:
        node: Node instance (unused here, kept for API consistency).
        flow_caps: CCF CapSet representing the flow's operating point.
        target_caps_or_cons: CCF Caps or Cons to check against.
        layer: -1 for main flow, or layer number for sub-flows.
        format_urn: format URN for layer filtering (e.g., FormatVideo.s).
        verbose: Print CCF state for debugging.

    Returns:
        True if flow is within at least one CapSet/ConSet, False otherwise.
    """
    try:
        from caps.MatroxCCF import conset_included_in_caps, Caps, Cons
    except ImportError:
        return True  # If CCF not available, assume compatible

    if flow_caps is None or target_caps_or_cons is None:
        return True

    # Flow-point-in-space check always uses conset_included_in_caps semantics:
    # only the flow's properties need to be within the target's ranges.
    # If target is Cons, convert to Caps (ConSet→CapSet conversion preserves values).
    if isinstance(target_caps_or_cons, Cons):
        target_caps = Caps(capsets=[cs.to_capset() for cs in target_caps_or_cons.consets])
    else:
        target_caps = target_caps_or_cons

    if verbose:
        print(f"  [check_flow_compatibility] Flow caps:\n    {flow_caps}")
        print(f"  [check_flow_compatibility] Target caps ({len(target_caps.capsets)} capsets)")

    # Filter target caps by layer/format if specified, and sort by preference
    target = target_caps
    if layer >= 0 and format_urn:
        target = target.get(format=format_urn, layer=layer)
    else:
        target = target.get()

    # Convert flow CapSet to ConSet for inclusion check
    flow_conset = flow_caps.to_conset()

    # Empty target capsets (from normalize's auto-generated defaults) mean the layer
    # is unconstrained — any flow is compatible. Unconstrained properties are skipped.
    if target.capsets and all(len(cs.caps) == 0 for cs in target.capsets):
        return True

    # Check inclusion against caps (preference-sorted by CCF)
    is_included: bool = conset_included_in_caps(flow_conset, target)

    if verbose:
        print(f"  [check_flow_compatibility] Result: {'COMPATIBLE' if is_included else 'INCOMPATIBLE'}")
        if not is_included:
            # Print first non-matching parameter for debugging
            for cap_name, cap in flow_caps.caps.items():
                if cap.value.values:
                    for target_cs in target.capsets:
                        target_cap = target_cs.caps.get(cap_name)
                        if target_cap is not None and not target_cap.value.infinite:
                            from caps.MatroxCCF import value_included_in_range
                            for v in cap.value.values:
                                try:
                                    if not value_included_in_range(v, target_cap.value):
                                        print(f"    MISMATCH: {cap_name}: {v} not in {target_cap.value}")
                                        break
                                except (ValueError, TypeError):
                                    pass

    return is_included


def check_sender_flow_compatibility(
    node: Any,
    sender_id: str,
    verbose: bool = False,
) -> str:
    """Check if a sender's flow is compatible with its capabilities.

    Returns:
        "compatible" if flow is within sender caps.
        "incompatible" if not.
        "unconstrained" if sender has no capabilities.
    """
    try:
        from caps.MatroxCCF import Caps, convert_caps_json_to_caps
    except ImportError:
        return Unconstrained.s

    from nmos.node.flow_caps import get_flow_to_caps

    # Get sender
    sender = node.senders.get(sender_id)
    if sender is None:
        return Unconstrained.s

    # Get flow
    flow_id = sender.FlowId.value if sender.FlowId.defined and sender.FlowId.value else None
    if flow_id is None:
        return Unconstrained.s

    flow_ptr = node.flows.get(flow_id)
    if flow_ptr is None:
        return Unconstrained.s

    # Get flow caps (via get_flow_to_caps)
    flow_caps = get_flow_to_caps(node, flow_ptr)

    # Check against the normalized constraints (IS-11 active constraints).
    # When no active constraints → normalized is empty → "unconstrained".
    # When active constraints → checks flow against them.
    sender_cons = _get_sender_normalized_ccf_cons(node, sender)
    if sender_cons is None or len(sender_cons.consets) == 0:
        return Unconstrained.s

    # Repairs FORCE from the merged constraints (capability-derived, so the
    # repaired flow is self-consistent); checks run against the normalized
    # constraints. Fall back to normalized if no merged cache exists.
    merged_cons = _get_sender_merged_ccf_cons(node, sender)
    if merged_cons is None or len(merged_cons.consets) == 0:
        merged_cons = sender_cons

    if verbose:
        print(f"  [check_sender_flow] sender={sender_id}")

    # Check main flow compatibility
    compatible = check_flow_properties_compatibility(
        node, flow_caps, sender_cons, verbose=verbose,
    )

    if not compatible:
        # Attempt to fix the flow, then recheck
        fix_ok = update_sender_to_compliant_flow(
            node, sender_id, merged_cons, layer=-1, reset=False, verbose=verbose,
        )
        if fix_ok:
            # Re-fetch flow properties after fix
            flow_ptr = node.flows.get(flow_id)
            if flow_ptr is not None:
                flow_caps = get_flow_to_caps(node, flow_ptr)
                compatible = check_flow_properties_compatibility(
                    node, flow_caps, sender_cons, verbose=verbose,
                )
        if not compatible:
            return "incompatible"

    # --- Mux sub-flow handling ---
    from nmos.types.generated.nflow_mux import NFlowMux, NFlowMuxValue

    poly = flow_ptr.get() if hasattr(flow_ptr, 'get') else flow_ptr
    if not isinstance(poly, (NFlowMux, NFlowMuxValue)):
        return "compatible"  # Not a mux — done

    fv = poly.value if hasattr(poly, 'value') else poly
    flow_core = fv.FlowCore

    if not flow_core.Parents.defined:
        return "compatible"

    parents = flow_core.Parents.value
    if not parents or len(parents) == 0:
        return "compatible"

    # Track expected layer sequence and compatibility group bitmask
    check_layers: dict[str, int] = {
        FormatVideo.s: -1,
        FormatAudio.s: -1,
        FormatData.s: -1,
    }
    compat_mask = 0xFFFFFFFFFFFFFFFF

    for parent_flow_id in parents:
        parent_ptr = node.flows.get(parent_flow_id)
        assert parent_ptr is not None, f"missing parent flow {parent_flow_id}"

        parent_poly = parent_ptr.get() if hasattr(parent_ptr, 'get') else parent_ptr
        assert parent_poly is not None, f"parent flow {parent_flow_id} has no value"

        # Reject circular mux
        if isinstance(parent_poly, (NFlowMux, NFlowMuxValue)):
            raise UnexpectedError("circular mux reference")

        parent_fv = parent_poly.value if hasattr(parent_poly, 'value') else parent_poly
        parent_core = parent_fv.FlowCore

        assert parent_core.Layer.defined, f"parent flow {parent_flow_id} missing Layer"
        layer = parent_core.Layer.value
        assert layer >= 0, f"parent flow {parent_flow_id} has invalid Layer={layer}"

        # Determine format
        from nmos.types.generated.nflow_video_raw import NFlowVideoRawValue
        from nmos.types.generated.nflow_video_coded import NFlowVideoCodedValue
        from nmos.types.generated.nflow_audio_raw import NFlowAudioRawValue
        from nmos.types.generated.nflow_audio_coded import NFlowAudioCodedValue

        if isinstance(parent_poly, (NFlowVideoRawValue, NFlowVideoCodedValue)):
            fmt = FormatVideo.s
        elif isinstance(parent_poly, (NFlowAudioRawValue, NFlowAudioCodedValue)):
            fmt = FormatAudio.s
        else:
            fmt = FormatData.s

        # Validate sequential layers
        if layer != check_layers.get(fmt, -1) + 1:
            if verbose:
                print(f"    [mux] Non-sequential layer: format={fmt} expected={check_layers.get(fmt, -1) + 1} got={layer}")
            raise UnexpectedError(f"non-sequential mux layer: {fmt} layer={layer}")
        check_layers[fmt] = layer

        # Check sub-flow compatibility
        parent_caps = get_flow_to_caps(node, parent_ptr)
        sub_compatible = check_flow_properties_compatibility(
            node, parent_caps, sender_cons, layer=layer, format_urn=fmt, verbose=verbose,
        )

        if not sub_compatible:
            # Attempt to fix parent flow, then recheck
            # First force compliance (Cons → CapSet) from the merged
            # constraints, then write back to flow
            compliant, compliant_groups = force_flow_properties_compatibility(
                node, parent_ptr, merged_cons,
                layer=layer, format_urn=fmt, verbose=verbose,
            )
            if compliant is not None:
                update_flow_to_compliant(node, parent_ptr, compliant, compliant_groups, verbose=verbose)
                # Re-verify after fix
                parent_caps = get_flow_to_caps(node, parent_ptr)
                sub_compatible = check_flow_properties_compatibility(
                    node, parent_caps, sender_cons, layer=layer, format_urn=fmt, verbose=verbose,
                )

            if not sub_compatible:
                if verbose:
                    print(f"    [mux] Sub-flow layer={layer} format={fmt} INCOMPATIBLE")
                return "incompatible"

        # Update compatibility mask from parent's groups
        if parent_core.LayerCompatibilityGroups.defined:
            parent_groups = set(parent_core.LayerCompatibilityGroups.value)
            compat_mask &= get_bitmask_from_compatibility_groups(parent_groups)
            if compat_mask == 0:
                if verbose:
                    print(f"    [mux] No compatible group overlap after layer={layer}")
                return "incompatible"

    return "compatible"


def _get_sender_static_id(sender: Any) -> str:
    """Get the static resource ID from a sender."""
    if hasattr(sender, 'ResourceCore'):
        return sender.ResourceCore.StaticId.value if sender.ResourceCore.StaticId.defined else ""
    return ""


def _get_sender_normalized_ccf_cons(node: Any, sender: Any) -> Any:
    """Get sender's normalized active constraints as cached CCF Cons.

    Normalized = the user's constraint sets (plus auto-generated defaults
    for missing mux layers) followed by the merged sets. The flow is
    CHECKED against these. Returns None if unconstrained.
    """
    static_id = _get_sender_static_id(sender)
    return node.sender_ccf_normalized.get(static_id)


def _get_sender_merged_ccf_cons(node: Any, sender: Any) -> Any:
    """Get sender's merged active constraints as cached CCF Cons.

    Merged = each user constraint set overlaid onto the capability set it
    fits, inheriting that set's media_type and every unconstrained
    capability. The flow is FORCED from these so the result is always a
    self-consistent, capability-compliant operating point. Returns None
    if unconstrained.
    """
    static_id = _get_sender_static_id(sender)
    return node.sender_ccf_merged.get(static_id)


def _get_sender_ccf_caps(node: Any, sender: Any) -> Any:
    """Get sender's IS-04 capabilities as cached CCF Caps.

    Stored at pipeline build time — no conversion needed.
    """
    static_id = _get_sender_static_id(sender)
    return node.sender_ccf_caps.get(static_id)


# ---------------------------------------------------------------------------
# Phase 3: Flow write-back functions
#
# Read properties from a constricted CCF CapSet and write them back to
# NMOS flow objects.  Each function handles a specific flow type.
# ---------------------------------------------------------------------------

def _write_layer_compatibility_groups(
    flow_ptr: Any,
    compliant_groups: list[int] | None,
) -> None:
    """Write LayerCompatibilityGroups to a flow's FlowCore.

    Follows the pattern used by all update*Flow functions.
    If compliant_groups is provided, sets it; otherwise clears it.
    """
    try:
        inner = flow_ptr.get() if hasattr(flow_ptr, 'get') else flow_ptr
        if inner is None:
            return
        fv = inner.value if hasattr(inner, 'value') else inner
        fc = fv.FlowCore
        if compliant_groups is not None:
            fc.LayerCompatibilityGroups.value = compliant_groups
        else:
            # Clear (set to empty/undefined)
            if hasattr(fc.LayerCompatibilityGroups, '_defined'):
                fc.LayerCompatibilityGroups._defined = False
    except Exception:
        pass


def _write_source_channels(
    node: Any,
    source_id: str,
    channel_count: int,
    am824: bool = False,
    verbose: bool = False,
) -> None:
    """Update audio source channel count.

    Builds SMPTE-ordered channel array via WithSourceChannels(GetAudioChannels(channelCount))
    or WithSourceChannels(GetAm824AudioChannels(channelCount)).

    AM824 uses stereo pairs (L,R,L,R,...) for the first 4 channels,
    then surround channels. Standard audio uses L,R,C,LFE,Ls,Rs,...
    """
    from nmos.types.generated.naudio_channel import NAudioChannelValue
    from nmos.enums import EnumRegistry

    if am824:
        # GetAm824AudioChannels — stereo pairs first
        _AM824_SYMBOLS = [L.s, R.s, L.s, R.s, C.s, LFE.s, Ls.s, Rs.s, Lrs.s, Rrs.s]
        symbols = _AM824_SYMBOLS
    else:
        # GetAudioChannels — standard SMPTE ordering
        _SYMBOLS = [L.s, R.s, C.s, LFE.s, Ls.s, Rs.s, Lrs.s, Rrs.s]
        symbols = _SYMBOLS

    channels: list[Any] = []
    for i in range(channel_count):
        ch = NAudioChannelValue()
        ch.Label.value = symbols[i].lower() if i < len(symbols) else f"ch{i}"
        if i < len(symbols):
            ch.Symbol.value = EnumRegistry.get(symbols[i])
        channels.append(ch)

    source_ptr = node.sources.get(source_id)
    if source_ptr is None:
        return
    src_inner = source_ptr.get() if hasattr(source_ptr, 'get') else source_ptr
    src_val = src_inner.value if hasattr(src_inner, 'value') else src_inner
    if hasattr(src_val, 'Channels'):
        src_val.Channels._defined = True
        src_val.Channels._value._inner = channels
        if verbose:
            print(f"    [write_source_channels] source={source_id} channels={channel_count}")


def _write_source_clock(
    node: Any,
    source_id: str,
    clk_name: str | None,
    synchronous_media: bool | None,
    verbose: bool = False,
) -> None:
    """Update source clock and sync properties if clk_name is provided.

    Follows the pattern used by the update*Flow functions.
    """
    if clk_name is None or clk_name == "":
        return
    source_ptr = node.sources.get(source_id)
    if source_ptr is None:
        return
    src_inner = source_ptr.get() if hasattr(source_ptr, 'get') else source_ptr
    src_val = src_inner.value if hasattr(src_inner, 'value') else src_inner
    src_core = getattr(src_val, 'SourceCore', src_val)
    src_core.SynchronousMedia.value = synchronous_media if synchronous_media is not None else True
    src_core.ClockName.value = clk_name
    if verbose:
        print(f"    [write_source_clock] source={source_id} clk={clk_name} sync={synchronous_media}")


def update_raw_video_flow(
    node: Any,
    flow_ptr: Any,
    compliant_caps: Any,
    compliant_groups: list[int] | None = None,
    verbose: bool = False,
) -> None:
    """Write constricted CapSet values back to an NFlowVideoRawValue.

    Reads values from compliant_caps (CCF CapSet) and writes them to the flow.
    """

    # Extract all required properties from the CapSet
    frame_width = _get_cap_int(compliant_caps, CapFormatFrameWidth.s)
    frame_height = _get_cap_int(compliant_caps, CapFormatFrameHeight.s)
    colorspace = _get_cap_str(compliant_caps, CapFormatColorspace.s)
    transfer = _get_cap_str(compliant_caps, CapFormatTransferCharacteristic.s)
    interlace = _get_cap_str(compliant_caps, CapFormatInterlaceMode.s)
    grain_rate = _get_cap_rational(compliant_caps, CapFormatGrainRate.s)
    depth = _get_cap_int(compliant_caps, CapFormatComponentDepth.s)
    sampling = _get_cap_str(compliant_caps, CapFormatColorSampling.s)

    # Optional transport
    sync_media = _get_cap_bool(compliant_caps, CapTransportSynchronousMedia.s)
    clk_ref = _get_cap_str(compliant_caps, CapTransportClockRefType.s)
    clk_name = ""
    if clk_ref == Ptp.s:
        clk_name = "clk0"
    elif clk_ref == Internal.s:
        clk_name = "clk1"

    if frame_width is None or frame_height is None:
        raise NotAllowed("missing frame dimensions in constricted caps")

    if verbose:
        print(f"    [update_raw_video] {frame_width}x{frame_height} {sampling} "
              f"depth={depth} rate={grain_rate}")

    # Build components from color_sampling
    from nmos.enums import Y, Cb, Cr, R, G, B
    from nmos.types.generated.nvideo_component import NVideoComponentValue

    components: list[NVideoComponentValue] = []
    bit_depth = depth if depth else 8

    if sampling and "4:4:4" in sampling:
        components = _make_ycbcr_components(Y, Cb, Cr, frame_width, frame_height,
                                            frame_width, frame_height, bit_depth)
    elif sampling and "4:2:2" in sampling:
        components = _make_ycbcr_components(Y, Cb, Cr, frame_width, frame_height,
                                            frame_width // 2, frame_height, bit_depth)
    elif sampling and "4:2:0" in sampling:
        components = _make_ycbcr_components(Y, Cb, Cr, frame_width, frame_height,
                                            frame_width // 2, frame_height // 2, bit_depth)
    elif sampling and "RGB" in sampling:
        components = _make_ycbcr_components(R, G, B, frame_width, frame_height,
                                            frame_width, frame_height, bit_depth)
    else:
        # Default to 4:2:2
        components = _make_ycbcr_components(Y, Cb, Cr, frame_width, frame_height,
                                            frame_width // 2, frame_height, bit_depth)

    # Write to flow
    inner = flow_ptr.get() if hasattr(flow_ptr, 'get') else flow_ptr
    if inner is None:
        return
    fv = inner.value if hasattr(inner, 'value') else inner

    if colorspace:
        fv.Colorspace.value = EnumRegistry.get(colorspace)
    if transfer:
        fv.TransferCharacteristic.value = EnumRegistry.get(transfer)
    if interlace:
        fv.InterlaceMode.value = EnumRegistry.get(interlace)
    fv.FrameWidth.value = frame_width
    fv.FrameHeight.value = frame_height
    fv.Components.value = components

    if grain_rate:
        from nmos.types.generated.nrational import NRationalValue
        gr = NRationalValue()
        gr.Numerator.value = grain_rate[0]
        gr.Denominator.value = grain_rate[1]
        fv.FlowCore.GrainRate.set_value(gr)

    # Update source clock
    source_id = fv.FlowCore.SourceId.value if fv.FlowCore.SourceId.defined else None
    if source_id and clk_name:
        _write_source_clock(node, source_id, clk_name, sync_media, verbose)

    # Write LayerCompatibilityGroups
    _write_layer_compatibility_groups(flow_ptr, compliant_groups)


def _make_ycbcr_components(
    name0: Any, name1: Any, name2: Any,
    luma_w: int, luma_h: int,
    chroma_w: int, chroma_h: int,
    bit_depth: int,
) -> list[Any]:
    """Build 3 video components (Y/Cb/Cr or R/G/B)."""
    from nmos.types.generated.nvideo_component import NVideoComponentValue

    def _make(name: Any, w: int, h: int) -> NVideoComponentValue:
        c = NVideoComponentValue()
        c.Name.value = name
        c.Width.value = w
        c.Height.value = h
        c.BitDepth.value = bit_depth
        return c

    return [_make(name0, luma_w, luma_h), _make(name1, chroma_w, chroma_h), _make(name2, chroma_w, chroma_h)]


def update_raw_audio_flow(
    node: Any,
    flow_ptr: Any,
    compliant_caps: Any,
    compliant_groups: list[int] | None = None,
    verbose: bool = False,
) -> None:
    """Write constricted CapSet values back to an NFlowAudioRawValue."""

    sample_depth = _get_cap_int(compliant_caps, CapFormatSampleDepth.s)
    sample_rate = _get_cap_rational(compliant_caps, CapFormatSampleRate.s)
    channel_count = _get_cap_int(compliant_caps, CapFormatChannelCount.s)

    sync_media = _get_cap_bool(compliant_caps, CapTransportSynchronousMedia.s)
    clk_ref = _get_cap_str(compliant_caps, CapTransportClockRefType.s)
    clk_name = ""
    if clk_ref == Ptp.s:
        clk_name = "clk0"
    elif clk_ref == Internal.s:
        clk_name = "clk1"

    if sample_depth is None or sample_rate is None or channel_count is None:
        raise NotAllowed("missing audio properties in constricted caps")

    if verbose:
        print(f"    [update_raw_audio] depth={sample_depth} rate={sample_rate} ch={channel_count}")

    inner = flow_ptr.get() if hasattr(flow_ptr, 'get') else flow_ptr
    if inner is None:
        return
    fv = inner.value if hasattr(inner, 'value') else inner

    # Set MediaType from sample_depth via WithFlowAudio
    _DEPTH_TO_MT = {8: AudioRawL8.s, 16: AudioRawL16.s, 20: AudioRawL20.s, 24: AudioRawL24.s}
    mt_str = _DEPTH_TO_MT.get(sample_depth)
    if mt_str:
        fv.MediaType.value = EnumRegistry.get(mt_str)

    fv.BitDepth.value = sample_depth
    if hasattr(fv, 'SampleRate'):
        from nmos.types.generated.nrational import NRationalValue
        sr = NRationalValue()
        sr.Numerator.value = sample_rate[0]
        sr.Denominator.value = sample_rate[1]
        fv.SampleRate.set_value(sr)

    # GrainRate = SampleRate for audio
    from nmos.types.generated.nrational import NRationalValue
    gr = NRationalValue()
    gr.Numerator.value = sample_rate[0]
    gr.Denominator.value = sample_rate[1]
    fv.FlowCore.GrainRate.set_value(gr)

    # Update source channels + clock via WithSourceChannels + clock
    source_id = fv.FlowCore.SourceId.value if fv.FlowCore.SourceId.defined else None
    if source_id:
        _write_source_channels(node, source_id, channel_count, verbose)
        _write_source_clock(node, source_id, clk_name, sync_media, verbose)

    _write_layer_compatibility_groups(flow_ptr, compliant_groups)


def update_coded_video_flow(
    node: Any,
    flow_ptr: Any,
    compliant_caps: Any,
    compliant_groups: list[int] | None = None,
    verbose: bool = False,
) -> None:
    """Write constricted CapSet values back to an NFlowVideoCodedValue.

    Includes codec profile/level validation via nmos.node.codec.
    """

    media_type = _get_cap_str(compliant_caps, CapFormatMediaType.s)
    frame_width = _get_cap_int(compliant_caps, CapFormatFrameWidth.s)
    frame_height = _get_cap_int(compliant_caps, CapFormatFrameHeight.s)
    colorspace = _get_cap_str(compliant_caps, CapFormatColorspace.s)
    transfer = _get_cap_str(compliant_caps, CapFormatTransferCharacteristic.s)
    interlace = _get_cap_str(compliant_caps, CapFormatInterlaceMode.s)
    grain_rate = _get_cap_rational(compliant_caps, CapFormatGrainRate.s)
    depth = _get_cap_int(compliant_caps, CapFormatComponentDepth.s)
    sampling = _get_cap_str(compliant_caps, CapFormatColorSampling.s)
    bit_rate = _get_cap_int(compliant_caps, CapFormatBitRate.s)
    cbr = _get_cap_bool(compliant_caps, CapFormatConstantBitRate.s)
    profile = _get_cap_str(compliant_caps, CapFormatProfile.s)
    level = _get_cap_str(compliant_caps, CapFormatLevel.s)
    sublevel = _get_cap_str(compliant_caps, CapFormatSublevel.s)
    fbblevel = _get_cap_str(compliant_caps, CapFormatFbblevel.s)

    sync_media = _get_cap_bool(compliant_caps, CapTransportSynchronousMedia.s)
    clk_ref = _get_cap_str(compliant_caps, CapTransportClockRefType.s)
    clk_name = ""
    if clk_ref == Ptp.s:
        clk_name = "clk0"
    elif clk_ref == Internal.s:
        clk_name = "clk1"

    if frame_width is None or frame_height is None or media_type is None:
        raise NotAllowed("missing video properties in constricted caps")

    if verbose:
        print(f"    [update_coded_video] {media_type} {frame_width}x{frame_height} "
              f"profile={profile} level={level} bitrate={bit_rate}")

    # Build components
    from nmos.enums import Y, Cb, Cr, R, G, B
    bit_depth = depth if depth else 8
    if sampling and "RGB" in sampling:
        # RGB is three full-resolution components (structurally 4:4:4).
        components = _make_ycbcr_components(R, G, B, frame_width, frame_height,
                                            frame_width, frame_height, bit_depth)
    elif sampling and "4:4:4" in sampling:
        components = _make_ycbcr_components(Y, Cb, Cr, frame_width, frame_height,
                                            frame_width, frame_height, bit_depth)
    elif sampling and "4:2:0" in sampling:
        components = _make_ycbcr_components(Y, Cb, Cr, frame_width, frame_height,
                                            frame_width // 2, frame_height // 2, bit_depth)
    else:  # Default 4:2:2
        components = _make_ycbcr_components(Y, Cb, Cr, frame_width, frame_height,
                                            frame_width // 2, frame_height, bit_depth)

    # Write to flow
    inner = flow_ptr.get() if hasattr(flow_ptr, 'get') else flow_ptr
    if inner is None:
        return
    fv = inner.value if hasattr(inner, 'value') else inner

    fv.MediaType.value = EnumRegistry.get(media_type)
    fv.FrameWidth.value = frame_width
    fv.FrameHeight.value = frame_height
    if colorspace:
        fv.Colorspace.value = EnumRegistry.get(colorspace)
    if transfer:
        fv.TransferCharacteristic.value = EnumRegistry.get(transfer)
    if interlace:
        fv.InterlaceMode.value = EnumRegistry.get(interlace)
    fv.Components.value = components

    if grain_rate:
        from nmos.types.generated.nrational import NRationalValue
        gr = NRationalValue()
        gr.Numerator.value = grain_rate[0]
        gr.Denominator.value = grain_rate[1]
        fv.FlowCore.GrainRate.set_value(gr)

    if bit_rate is not None:
        fv.Bitrate.value = bit_rate
    if cbr is not None:
        fv.ConstantBitrate.value = cbr
    if profile:
        fv.Profile.value = EnumRegistry.get(profile)
    if level:
        fv.Level.value = EnumRegistry.get(level)
    if sublevel:
        fv.Sublevel.value = EnumRegistry.get(sublevel)
    if fbblevel:
        fv.Fbblevel.value = EnumRegistry.get(fbblevel)

    # Update source clock
    source_id = fv.FlowCore.SourceId.value if fv.FlowCore.SourceId.defined else None
    if source_id and clk_name:
        _write_source_clock(node, source_id, clk_name, sync_media, verbose)

    _write_layer_compatibility_groups(flow_ptr, compliant_groups)


def update_coded_audio_flow(
    node: Any,
    flow_ptr: Any,
    compliant_caps: Any,
    compliant_groups: list[int] | None = None,
    verbose: bool = False,
) -> None:
    """Write constricted CapSet values back to an NFlowAudioCodedValue."""

    media_type = _get_cap_str(compliant_caps, CapFormatMediaType.s)
    sample_rate = _get_cap_rational(compliant_caps, CapFormatSampleRate.s)
    channel_count = _get_cap_int(compliant_caps, CapFormatChannelCount.s)
    bit_rate = _get_cap_int(compliant_caps, CapFormatBitRate.s)
    cbr = _get_cap_bool(compliant_caps, CapFormatConstantBitRate.s)
    profile = _get_cap_str(compliant_caps, CapFormatProfile.s)
    level = _get_cap_str(compliant_caps, CapFormatLevel.s)

    sync_media = _get_cap_bool(compliant_caps, CapTransportSynchronousMedia.s)
    clk_ref = _get_cap_str(compliant_caps, CapTransportClockRefType.s)
    clk_name = ""
    if clk_ref == Ptp.s:
        clk_name = "clk0"
    elif clk_ref == Internal.s:
        clk_name = "clk1"

    if media_type is None or sample_rate is None or channel_count is None:
        raise NotAllowed("missing audio properties in constricted caps")

    if verbose:
        print(f"    [update_coded_audio] {media_type} rate={sample_rate} ch={channel_count} "
              f"profile={profile} level={level}")

    inner = flow_ptr.get() if hasattr(flow_ptr, 'get') else flow_ptr
    if inner is None:
        return
    fv = inner.value if hasattr(inner, 'value') else inner

    fv.MediaType.value = EnumRegistry.get(media_type)

    # SampleRate + GrainRate for coded audio (get_flow_to_caps reads SampleRate)
    from nmos.types.generated.nrational import NRationalValue
    sr = NRationalValue()
    sr.Numerator.value = sample_rate[0]
    sr.Denominator.value = sample_rate[1]
    if hasattr(fv, 'SampleRate'):
        fv.SampleRate.set_value(sr)
    gr = NRationalValue()
    gr.Numerator.value = sample_rate[0]
    gr.Denominator.value = sample_rate[1]
    fv.FlowCore.GrainRate.set_value(gr)

    if bit_rate is not None:
        fv.Bitrate.value = bit_rate
    if cbr is not None:
        fv.ConstantBitrate.value = cbr
    if profile:
        fv.Profile.value = EnumRegistry.get(profile)
    if level:
        fv.Level.value = EnumRegistry.get(level)

    # Update source channels + clock via WithSourceChannels + clock.
    # AM824 uses different channel layout (GetAm824AudioChannels vs GetAudioChannels)
    source_id = fv.FlowCore.SourceId.value if fv.FlowCore.SourceId.defined else None
    if source_id:
        is_am824 = media_type is not None and "am824" in media_type.lower()
        _write_source_channels(node, source_id, channel_count, am824=is_am824, verbose=verbose)
        _write_source_clock(node, source_id, clk_name, sync_media, verbose)

    _write_layer_compatibility_groups(flow_ptr, compliant_groups)


def update_data_flow(
    node: Any,
    flow_ptr: Any,
    compliant_caps: Any,
    compliant_groups: list[int] | None = None,
    verbose: bool = False,
) -> None:
    """Write constricted CapSet values back to an NFlowDataValue."""

    sync_media = _get_cap_bool(compliant_caps, CapTransportSynchronousMedia.s)
    clk_ref = _get_cap_str(compliant_caps, CapTransportClockRefType.s)
    clk_name = ""
    if clk_ref == Ptp.s:
        clk_name = "clk0"
    elif clk_ref == Internal.s:
        clk_name = "clk1"

    inner = flow_ptr.get() if hasattr(flow_ptr, 'get') else flow_ptr
    if inner is None:
        return
    fv = inner.value if hasattr(inner, 'value') else inner

    source_id = fv.FlowCore.SourceId.value if fv.FlowCore.SourceId.defined else None
    if source_id and clk_name:
        _write_source_clock(node, source_id, clk_name, sync_media, verbose)

    _write_layer_compatibility_groups(flow_ptr, compliant_groups)


def update_mux_flow(
    node: Any,
    flow_ptr: Any,
    compliant_caps: Any,
    compliant_groups: list[int] | None = None,
    verbose: bool = False,
) -> None:
    """Write constricted CapSet values back to an NFlowMuxValue."""

    media_type = _get_cap_str(compliant_caps, CapFormatMediaType.s)
    video_layers = _get_cap_int(compliant_caps, CapFormatVideoLayers.s)
    audio_layers = _get_cap_int(compliant_caps, CapFormatAudioLayers.s)
    data_layers = _get_cap_int(compliant_caps, CapFormatDataLayers.s)

    sync_media = _get_cap_bool(compliant_caps, CapTransportSynchronousMedia.s)
    clk_ref = _get_cap_str(compliant_caps, CapTransportClockRefType.s)
    clk_name = ""
    if clk_ref == Ptp.s:
        clk_name = "clk0"
    elif clk_ref == Internal.s:
        clk_name = "clk1"

    if verbose:
        print(f"    [update_mux] {media_type} v={video_layers} a={audio_layers} d={data_layers}")

    inner = flow_ptr.get() if hasattr(flow_ptr, 'get') else flow_ptr
    if inner is None:
        return
    fv = inner.value if hasattr(inner, 'value') else inner

    if media_type:
        fv.MediaType.value = EnumRegistry.get(media_type)
    if video_layers is not None:
        fv.VideoLayers.value = video_layers
    if audio_layers is not None:
        fv.AudioLayers.value = audio_layers
    if data_layers is not None:
        fv.DataLayers.value = data_layers

    source_id = fv.FlowCore.SourceId.value if fv.FlowCore.SourceId.defined else None
    if source_id and clk_name:
        _write_source_clock(node, source_id, clk_name, sync_media, verbose)

        # Propagate clock to parent sources
        source_ptr = node.sources.get(source_id)
        if source_ptr is not None:
            src_inner = source_ptr.get() if hasattr(source_ptr, 'get') else source_ptr
            src_val = src_inner.value if hasattr(src_inner, 'value') else src_inner
            src_core = getattr(src_val, 'SourceCore', src_val)
            if hasattr(src_core, 'Parents') and src_core.Parents.defined:
                for parent_source_id in (src_core.Parents.value or []):
                    _write_source_clock(node, parent_source_id, clk_name, sync_media, verbose)

    _write_layer_compatibility_groups(flow_ptr, compliant_groups)


# ---------------------------------------------------------------------------
# Phase 4: Fix-ups and orchestrators
# ---------------------------------------------------------------------------

def fix_video_width_height(
    properties: dict[str, Any],
    constraints: dict[str, Any] | None = None,
    verbose: bool = False,
) -> None:
    """Fix video width/height to be consistent.

    If only width or height is explicitly constrained, derive the other
    from a standard resolution lookup table.
    """
    try:
        from caps.MatroxCCF import RangeValue, RangeType, Cap
    except ImportError:
        return

    # Only applies to video types
    mt_cap = properties.get(CapFormatMediaType.s)
    if mt_cap is None:
        return
    mt = str(mt_cap.value.values[0]) if mt_cap.value.values else ""
    mt_lower = mt.lower()
    if not mt_lower.startswith("video/"):
        return

    _W_TO_H = {720: 480, 1280: 720, 1920: 1080, 3840: 2160}
    _H_TO_W = {480: 720, 720: 1280, 1080: 1920, 2160: 3840}

    # Check original flags
    original_width = False
    original_height = False
    if constraints:
        w_con = constraints.get(CapFormatFrameWidth.s)
        if w_con is not None and hasattr(w_con, 'original'):
            original_width = w_con.original
        h_con = constraints.get(CapFormatFrameHeight.s)
        if h_con is not None and hasattr(h_con, 'original'):
            original_height = h_con.original

    w_cap = properties.get(CapFormatFrameWidth.s)
    h_cap = properties.get(CapFormatFrameHeight.s)
    width = int(w_cap.value.values[0]) if w_cap and w_cap.value.values else 0
    height = int(h_cap.value.values[0]) if h_cap and h_cap.value.values else 0

    if original_width and not original_height:
        new_h = _W_TO_H.get(width)
        if new_h is not None:
            properties[CapFormatFrameHeight.s] = Cap(
                CapFormatFrameHeight.s, RangeValue(values=(new_h,), type=RangeType.INT))
            if verbose:
                print(f"    [fix_wh] width={width} → height={new_h}")
    elif not original_width:
        new_w = _H_TO_W.get(height)
        if new_w is not None:
            properties[CapFormatFrameWidth.s] = Cap(
                CapFormatFrameWidth.s, RangeValue(values=(new_w,), type=RangeType.INT))
            if verbose:
                print(f"    [fix_wh] height={height} → width={new_w}")


def fix_coded_video_flow(
    properties: dict[str, Any],
    constraints: dict[str, Any] | None = None,
    verbose: bool = False,
) -> None:
    """Fix coded video profile/level/bitrate to produce a valid configuration.

    Handles H.264, H.265, and JPEG-XS. Adjusts profile↔sampling relationship
    and selects a valid level based on resolution/bitrate using the codec
    packages in nmos.node.codec.

    Uses CCF value_included_in_range() to check if candidate values are
    within constraint ranges (the CCF replacement for isPropertyInStringCapability()).
    """
    try:
        from caps.MatroxCCF import (
            Cap,
            RangeValue,
            RangeType,
            value_included_in_range,
        )
    except ImportError:
        return

    # Extract media type
    mt_cap = properties.get(CapFormatMediaType.s)
    if mt_cap is None or not mt_cap.value.values:
        return
    media_type = str(mt_cap.value.values[0])

    if media_type not in (VideoCodedH264.s, VideoCodedH265.s, VideoCodedJxsv.s):
        return

    # Extract all required properties
    def _get_str(name: str) -> str | None:
        c = properties.get(name)
        return str(c.value.values[0]) if c and c.value.values else None

    def _get_int(name: str) -> int | None:
        c = properties.get(name)
        return int(c.value.values[0]) if c and c.value.values else None

    frame_width = _get_int(CapFormatFrameWidth.s)
    frame_height = _get_int(CapFormatFrameHeight.s)
    profile = _get_str(CapFormatProfile.s)
    level = _get_str(CapFormatLevel.s)
    sublevel = _get_str(CapFormatSublevel.s)
    fbblevel = _get_str(CapFormatFbblevel.s)
    bit_rate = _get_int(CapFormatBitRate.s)
    sampling = _get_str(CapFormatColorSampling.s)

    if profile is None or level is None:
        return  # Transitioning between raw and coded

    if frame_width is None or frame_height is None:
        return

    # Check "original" flags on constraints
    def _is_original(name: str) -> bool:
        if constraints is None:
            return False
        c = constraints.get(name)
        return bool(getattr(c, 'original', False)) if c is not None else False

    original_profile = _is_original(CapFormatProfile.s)
    original_sampling = _is_original(CapFormatColorSampling.s)
    original_width = _is_original(CapFormatFrameWidth.s)
    original_height = _is_original(CapFormatFrameHeight.s)
    original_bitrate = _is_original(CapFormatBitRate.s)
    original_depth = _is_original(CapFormatComponentDepth.s)
    original_fbblevel = _is_original(CapFormatFbblevel.s)

    # Helper: check if a string value is within a constraint's range
    # `value` is whatever kind of value the capability holds — a string for
    # enumerated caps like sampling or profile, an integer for numeric ones like
    # component depth. value_included_in_range accepts both.
    def _value_in_constraint(value: str | int, constraint_name: str) -> bool:
        if constraints is None:
            return True
        con = constraints.get(constraint_name)
        if con is None:
            return True
        try:
            return bool(value_included_in_range(value, con.value))
        except (ValueError, TypeError):
            return False

    # Helper: set a property
    def _set_str(name: str, value: str) -> None:
        properties[name] = Cap(name, RangeValue(values=(value,), type=RangeType.STRING))

    def _set_int(name: str, value: int) -> None:
        properties[name] = Cap(name, RangeValue(values=(value,), type=RangeType.INT))

    # --- Profile/Sampling fix-up (per codec) ---

    if media_type == VideoCodedJxsv.s:
        _PROFILE_TO_SAMPLING: dict[str, list[str]] = {
            JxsvProfileMain420_12.s: [SamplingYCbCr_420.s],
            JxsvProfileHigh420_12.s: [SamplingYCbCr_420.s],
            JxsvProfileMain444_12.s: [SamplingYCbCr_444.s, SamplingYCbCr_422.s, SamplingRGB.s],
            JxsvProfileHigh444_12.s: [SamplingYCbCr_444.s, SamplingYCbCr_422.s, SamplingRGB.s],
            JxsvProfileTDC444_12.s: [SamplingYCbCr_444.s, SamplingYCbCr_422.s, SamplingYCbCr_420.s, SamplingRGB.s],
        }
        _SAMPLING_TO_PROFILE: dict[str, list[str]] = {
            SamplingYCbCr_420.s: [JxsvProfileHigh420_12.s, JxsvProfileMain420_12.s, JxsvProfileTDC444_12.s],
            SamplingYCbCr_422.s: [JxsvProfileHigh444_12.s, JxsvProfileMain444_12.s, JxsvProfileTDC444_12.s],
            SamplingYCbCr_444.s: [JxsvProfileHigh444_12.s, JxsvProfileMain444_12.s, JxsvProfileTDC444_12.s],
            SamplingRGB.s: [JxsvProfileHigh444_12.s, JxsvProfileMain444_12.s, JxsvProfileTDC444_12.s],
        }
        try_levels = [JxsvLevel4k1.s, JxsvLevel4k2.s, JxsvLevel4k3.s]

        from nmos.node.codec import get_jxsv_max_bitrate, check_jxsv_profile_level

    elif media_type == VideoCodedH264.s:
        _PROFILE_TO_SAMPLING = {
            H264ProfileHigh_422.s: [SamplingYCbCr_422.s, SamplingYCbCr_420.s],
            H264ProfileHighIntra_422.s: [SamplingYCbCr_422.s, SamplingYCbCr_420.s],
            H264ProfileHigh10.s: [SamplingYCbCr_420.s],
            H264ProfileHigh10Intra.s: [SamplingYCbCr_420.s],
            H264ProfileHigh.s: [SamplingYCbCr_420.s],
            CodecProfileMain.s: [SamplingYCbCr_420.s],
        }
        _SAMPLING_TO_PROFILE = {
            SamplingYCbCr_422.s: [H264ProfileHigh_422.s, H264ProfileHighIntra_422.s],
            SamplingYCbCr_420.s: [H264ProfileHigh_422.s, H264ProfileHighIntra_422.s, H264ProfileHigh10.s, H264ProfileHigh10Intra.s,
                                  H264ProfileHigh.s, CodecProfileMain.s],
        }
        try_levels = [CodecLevel3.s, CodecLevel3_1.s, CodecLevel3_2.s, CodecLevel4.s, CodecLevel4_1.s, CodecLevel4_2.s,
                      CodecLevel5.s, CodecLevel5_1.s, CodecLevel5_2.s, CodecLevel6.s, CodecLevel6_1.s, CodecLevel6_2.s]

        from nmos.node.codec import get_h264_max_bitrate, check_h264_profile_level

    elif media_type == VideoCodedH265.s:
        _PROFILE_TO_SAMPLING = {
            H265ProfileMain10_422.s: [SamplingYCbCr_422.s, SamplingYCbCr_420.s],
            H265ProfileMain10Intra_422.s: [SamplingYCbCr_422.s, SamplingYCbCr_420.s],
            H265ProfileMain10.s: [SamplingYCbCr_420.s],
            H265ProfileMain10Intra.s: [SamplingYCbCr_420.s],
            H265ProfileMain10_444.s: [SamplingYCbCr_444.s, SamplingYCbCr_422.s, SamplingYCbCr_420.s],
            H265ProfileMain10Intra_444.s: [SamplingYCbCr_444.s, SamplingYCbCr_422.s, SamplingYCbCr_420.s],
            CodecProfileMain.s: [SamplingYCbCr_420.s],
        }
        _SAMPLING_TO_PROFILE = {
            SamplingYCbCr_420.s: [H265ProfileMain10.s, H265ProfileMain10Intra.s, CodecProfileMain.s],
            SamplingYCbCr_422.s: [H265ProfileMain10_422.s, H265ProfileMain10Intra_422.s],
            SamplingYCbCr_444.s: [H265ProfileMain10_444.s, H265ProfileMain10Intra_444.s],
        }
        try_levels = [
            H265LevelMain3.s, H265LevelMain3_1.s, H265LevelMain4.s, H265LevelHigh4.s, H265LevelMain4_1.s, H265LevelHigh4_1.s,
            H265LevelMain5.s, H265LevelHigh5.s, H265LevelMain5_1.s, H265LevelHigh5_1.s, H265LevelMain5_2.s, H265LevelHigh5_2.s,
            H265LevelMain6.s, H265LevelHigh6.s, H265LevelMain6_1.s, H265LevelHigh6_1.s, H265LevelMain6_2.s, H265LevelHigh6_2.s,
        ]

        from nmos.node.codec import get_h265_max_bitrate, check_h265_profile_level
    else:
        return

    # fixSampling: given current profile, find a valid sampling within constraints
    def fix_sampling() -> bool:
        candidates = _PROFILE_TO_SAMPLING.get(profile, []) if profile else []
        for s in candidates:
            if _value_in_constraint(s, CapFormatColorSampling.s):
                _set_str(CapFormatColorSampling.s, s)
                nonlocal sampling
                sampling = s
                if verbose:
                    print(f"    [fix_coded] profile={profile} → sampling={s}")
                return True
        return False

    # fixProfile: given current sampling, find a valid profile within constraints
    def fix_profile() -> bool:
        candidates = _SAMPLING_TO_PROFILE.get(sampling, []) if sampling else []
        for p in candidates:
            if _value_in_constraint(p, CapFormatProfile.s):
                _set_str(CapFormatProfile.s, p)
                nonlocal profile
                profile = p
                if verbose:
                    print(f"    [fix_coded] sampling={sampling} → profile={p}")
                return True
        return False

    # Apply profile/sampling fix based on original flags
    if original_profile and not original_sampling:
        fix_sampling()
    elif not original_profile:
        fix_profile()
    elif original_profile and original_sampling:
        if not fix_sampling():
            fix_profile()

    # --- Depth fix-up ---
    # A profile bounds the component bit depth (codec profile tables) — e.g.
    # H.264 Main/High and H.265 Main are 8-bit only. When the (possibly
    # fixed) profile cannot carry the current depth, lower the depth to the
    # profile's maximum — provided the constraint set allows that value and
    # the user did not pin the depth (a pinned depth never moves). Runs
    # before level selection so the codec level checks validate components
    # built with the corrected depth.
    cur_depth = _get_int(CapFormatComponentDepth.s)
    if cur_depth is not None and profile and not original_depth:
        # Each codec module declares its own ProfileInfo type, so the profile
        # tables cannot share a variable. Only the depth bound is needed here,
        # so pull that single value out of whichever table applies.
        max_bit_depth: int | None = None
        if media_type == VideoCodedH264.s:
            from nmos.codec import h264
            h264_info = h264.ALL_PROFILES.get(EnumRegistry.get(profile))
            if h264_info is not None:
                max_bit_depth = h264_info.max_bit_depth
        elif media_type == VideoCodedH265.s:
            from nmos.codec import h265
            h265_info = h265.ALL_PROFILES.get(EnumRegistry.get(profile))
            if h265_info is not None:
                max_bit_depth = h265_info.max_bit_depth
        else:
            from nmos.codec import jxsv
            jxsv_info = jxsv.ALL_PROFILES.get(EnumRegistry.get(profile))
            if jxsv_info is not None:
                max_bit_depth = jxsv_info.max_bit_depth

        if (max_bit_depth is not None and cur_depth > max_bit_depth
                and _value_in_constraint(max_bit_depth,
                                         CapFormatComponentDepth.s)):
            _set_int(CapFormatComponentDepth.s, max_bit_depth)
            if verbose:
                print(f"    [fix_coded] profile={profile} → depth={max_bit_depth}")

    # --- FBB level fix-up (JPEG-XS only) ---
    # The frame-buffer-budget level depends on the profile family: TDC
    # profiles may use the 8/12 bpp budgets, every other profile must be
    # Unrestricted. The fbblevel has no impact on the streaming bitrate, so
    # it does not participate in the level/bitrate selection below. A
    # pinned fbblevel never moves.
    if media_type == VideoCodedJxsv.s and profile and not original_fbblevel:
        if profile == JxsvProfileTDC444_12.s:
            allowed = [JxsvFbblevelUnrestricted.s, JxsvFbblevel8bpp.s,
                       JxsvFbblevel12bpp.s]
        else:
            allowed = [JxsvFbblevelUnrestricted.s]
        cur_fbblevel = _get_str(CapFormatFbblevel.s)
        if cur_fbblevel not in allowed:
            for candidate in allowed:
                if _value_in_constraint(candidate, CapFormatFbblevel.s):
                    _set_str(CapFormatFbblevel.s, candidate)
                    if verbose:
                        print(f"    [fix_coded] profile={profile} → fbblevel={candidate}")
                    break

    # --- Level selection ---
    # Runs only when the constraint set constrains the level: forcing seeds
    # the level with the highest allowed value, and this pass settles on the
    # smallest allowed level that validates the complete configuration. An
    # unconstrained level is left untouched.
    if constraints is None or constraints.get(CapFormatLevel.s) is None:
        return

    # Try resolutions from current down to smaller standard sizes
    try_widths = [frame_width, 3840, 1920, 1280, 720]
    try_heights = [frame_height, 2160, 1080, 720, 480]

    # Build components for codec check
    from nmos.enums import Y, Cb, Cr, Progressive
    from nmos.types.generated.nvideo_component import NVideoComponentValue
    from nmos.types.generated.nrational import NRationalValue

    depth = _get_int(CapFormatComponentDepth.s) or 10
    colorspace_e = EnumRegistry.get(_get_str(CapFormatColorspace.s) or BT709.s)
    transfer_e = EnumRegistry.get(_get_str(CapFormatTransferCharacteristic.s) or SDR.s)
    interlace_e = Progressive
    if not profile:
        return  # Cannot fix coded flow without a profile
    profile_e = EnumRegistry.get(profile)
    sublevel_e = EnumRegistry.get(sublevel) if sublevel else None
    fbblevel_e = EnumRegistry.get(fbblevel) if fbblevel else None

    grain_rate_val = properties.get(CapFormatGrainRate.s)
    gr_val = NRationalValue()
    if grain_rate_val and grain_rate_val.value.values:
        fr = grain_rate_val.value.values[0]
        if isinstance(fr, Fraction):
            gr_val.Numerator.value = fr.numerator
            gr_val.Denominator.value = fr.denominator
        else:
            gr_val.Numerator.value = int(fr)
            gr_val.Denominator.value = 1

    def _make_components(w: int, h: int) -> list[NVideoComponentValue]:
        """Build Y/Cb/Cr components based on current sampling."""
        def _c(name: Any, cw: int, ch: int) -> NVideoComponentValue:
            c = NVideoComponentValue()
            c.Name.value = name; c.Width.value = cw; c.Height.value = ch; c.BitDepth.value = depth
            return c
        if sampling and "4:4:4" in sampling:
            return [_c(Y, w, h), _c(Cb, w, h), _c(Cr, w, h)]
        elif sampling and "4:2:0" in sampling:
            return [_c(Y, w, h), _c(Cb, w // 2, h // 2), _c(Cr, w // 2, h // 2)]
        else:  # 4:2:2
            return [_c(Y, w, h), _c(Cb, w // 2, h), _c(Cr, w // 2, h)]

    for i in range(len(try_widths)):
        tw, th = try_widths[i], try_heights[i]
        comps = _make_components(tw, th)

        # Levels are tried in ascending order so the configuration settles
        # on the smallest allowed level that validates.
        for try_level in try_levels:
            if not _value_in_constraint(try_level, CapFormatLevel.s):
                continue

            level_e = EnumRegistry.get(try_level)
            try_bitrate = bit_rate or 0

            # If bitrate not user-constrained, compute max for this level
            if not original_bitrate:
                try:
                    if media_type == VideoCodedJxsv.s and sublevel_e:
                        try_bitrate = get_jxsv_max_bitrate(
                            tw, th, colorspace_e, transfer_e, interlace_e,
                            comps, gr_val, profile_e, level_e, sublevel_e)
                    elif media_type == VideoCodedH264.s:
                        try_bitrate = get_h264_max_bitrate(
                            tw, th, colorspace_e, transfer_e, interlace_e,
                            comps, gr_val, profile_e, level_e)
                    elif media_type == VideoCodedH265.s:
                        try_bitrate = get_h265_max_bitrate(
                            tw, th, colorspace_e, transfer_e, interlace_e,
                            comps, gr_val, profile_e, level_e)
                except Exception:
                    continue

            # Validate the complete configuration
            try:
                if media_type == VideoCodedJxsv.s and sublevel_e:
                    check_jxsv_profile_level(
                        tw, th, colorspace_e, transfer_e, interlace_e,
                        comps, gr_val, profile_e, level_e, sublevel_e, try_bitrate)
                elif media_type == VideoCodedH264.s:
                    check_h264_profile_level(
                        tw, th, colorspace_e, transfer_e, interlace_e,
                        comps, gr_val, profile_e, level_e, try_bitrate)
                elif media_type == VideoCodedH265.s:
                    check_h265_profile_level(
                        tw, th, colorspace_e, transfer_e, interlace_e,
                        comps, gr_val, profile_e, level_e, try_bitrate)

                # Success — update properties
                _set_str(CapFormatLevel.s, try_level)
                _set_int(CapFormatBitRate.s, try_bitrate)
                if verbose:
                    print(f"    [fix_coded] {media_type} level={try_level} "
                          f"bitrate={try_bitrate} ({tw}x{th})")
                return

            except Exception:
                continue

        # If user constrained width/height, don't try other resolutions
        if original_width or original_height:
            break


def _swap_flow_flavor(
    node: Any,
    flow_ptr: Any,
    current_inner: Any,
    target_class: str,
    target_media_type: str | None,
    verbose: bool = False,
) -> Any:
    """Swap the polymorphic flow type (raw↔coded) when media class changes.

    Implements WithFlowRawFlavor / WithFlowCodedFlavor: creates a new inner
    flow of the target class, copies FlowCore, and replaces the flow in the
    node's store.
    """
    from nmos.types.generated.nflow_video_raw import NFlowVideoRawValue
    from nmos.types.generated.nflow_video_coded import NFlowVideoCodedValue
    from nmos.types.generated.nflow_audio_raw import NFlowAudioRawValue
    from nmos.types.generated.nflow_audio_coded import NFlowAudioCodedValue
    from nmos.types.generated.nflow import NFlowValue
    from nmos.enums import EnumRegistry

    # Get the FlowCore from the current flow
    flow_core = None
    if hasattr(current_inner, 'FlowCore'):
        flow_core = current_inner.FlowCore

    # Determine the new type based on target class and media type
    mt = target_media_type or ""
    new_inner: Any = None

    if target_class == "raw" and "video" in mt:
        new_inner = NFlowVideoRawValue()
        new_inner.set_to_default()
        new_inner.Format.value = FormatVideo
        new_inner.MediaType.value = EnumRegistry.get(mt)
    elif target_class == "raw" and "audio" in mt:
        new_inner = NFlowAudioRawValue()
        new_inner.set_to_default()
        new_inner.Format.value = EnumRegistry.get("urn:x-nmos:format:audio")
        new_inner.MediaType.value = EnumRegistry.get(mt)
    elif target_class == "coded" and "video" in mt:
        new_inner = NFlowVideoCodedValue()
        new_inner.set_to_default()
        new_inner.Format.value = FormatVideo
        new_inner.MediaType.value = EnumRegistry.get(mt)
    elif target_class == "coded" and "audio" in mt:
        new_inner = NFlowAudioCodedValue()
        new_inner.set_to_default()
        new_inner.Format.value = EnumRegistry.get("urn:x-nmos:format:audio")
        new_inner.MediaType.value = EnumRegistry.get(mt)

    if new_inner is None:
        return flow_ptr  # Can't swap — keep current

    # Transfer FlowCore (ID, SourceId, version, etc.)
    if flow_core is not None and hasattr(new_inner, 'FlowCore'):
        new_inner.FlowCore = flow_core.clone()

    # Replace in the polymorphic wrapper
    if hasattr(flow_ptr, 'set'):
        flow_ptr.set(new_inner)
    elif hasattr(flow_ptr, '_value'):
        flow_ptr._value._inner = new_inner

    if verbose:
        print(f"    [swap_flavor] Swapped to {type(new_inner).__name__} ({mt})")

    return flow_ptr


def update_flow_to_compliant(
    node: Any,
    flow_ptr: Any,
    compliant_caps: Any,
    compliant_groups: list[int] | None = None,
    verbose: bool = False,
) -> None:
    """Dispatch to the correct update_*_flow based on flow type.

    (Plays the role of updating a flow to its compliant operating point —
    the write-back half of update_sender_to_compliant_flow.)

    Handles raw↔coded class transitions by detecting if the compliant media_type
    class differs from the current flow class. Within-class media_type changes
    (e.g. one coded video codec to another) are written directly by the
    per-class update functions, which set MediaType plus the codec-specific
    properties from the compliant caps.

    Scope: writes the flow only. Propagation of the compliant flow properties to
    a linked receiver's native constraints is handled one level up by
    update_sender_to_compliant_flow() via _propagate_to_linked_receiver(), not
    here.
    """
    from nmos.types.generated.nflow_video_raw import NFlowVideoRaw, NFlowVideoRawValue
    from nmos.types.generated.nflow_video_coded import NFlowVideoCoded, NFlowVideoCodedValue
    from nmos.types.generated.nflow_audio_raw import NFlowAudioRaw, NFlowAudioRawValue
    from nmos.types.generated.nflow_audio_coded import NFlowAudioCoded, NFlowAudioCodedValue
    from nmos.types.generated.nflow_data import NFlowData, NFlowDataValue
    from nmos.types.generated.nflow_data_json import NFlowDataJson, NFlowDataJsonValue
    from nmos.types.generated.nflow_data_sdianc import NFlowDataSdianc, NFlowDataSdiancValue
    from nmos.types.generated.nflow_mux import NFlowMux, NFlowMuxValue

    poly = flow_ptr.get() if hasattr(flow_ptr, 'get') else flow_ptr
    if poly is None:
        return

    # Determine current flow class
    _RAW_TYPES = (NFlowVideoRaw, NFlowVideoRawValue, NFlowAudioRaw, NFlowAudioRawValue)
    _CODED_TYPES = (NFlowVideoCoded, NFlowVideoCodedValue, NFlowAudioCoded, NFlowAudioCodedValue)

    current_class = ""
    if isinstance(poly, _RAW_TYPES):
        current_class = "raw"
    elif isinstance(poly, _CODED_TYPES):
        current_class = "coded"
    elif isinstance(poly, (NFlowMux, NFlowMuxValue)):
        current_class = "mux"
    else:
        current_class = "data"

    # Determine compliant class from compliant_caps media_type
    compliant_mt = _get_cap_str(compliant_caps, CapFormatMediaType.s) if compliant_caps else None
    compliant_class = get_class_from_media_type(compliant_mt) if compliant_mt else current_class

    # Detect class transition via WithFlowRawFlavor/WithFlowCodedFlavor
    if current_class != compliant_class:
        if verbose:
            print(f"    [update_flow] Class transition: {current_class} → {compliant_class}")
        flow_ptr = _swap_flow_flavor(node, flow_ptr, poly, compliant_class, compliant_mt, verbose)
        poly = flow_ptr.get() if hasattr(flow_ptr, 'get') else flow_ptr

    # Dispatch based on compliant class (what the flow SHOULD become)
    if compliant_class == "raw" and compliant_mt and compliant_mt.startswith("video/"):
        update_raw_video_flow(node, flow_ptr, compliant_caps, compliant_groups, verbose)
    elif compliant_class == "raw" and compliant_mt and compliant_mt.startswith("audio/"):
        update_raw_audio_flow(node, flow_ptr, compliant_caps, compliant_groups, verbose)
    elif compliant_class == "coded" and compliant_mt and compliant_mt.startswith("video/"):
        update_coded_video_flow(node, flow_ptr, compliant_caps, compliant_groups, verbose)
    elif compliant_class == "coded" and compliant_mt and compliant_mt.startswith("audio/"):
        update_coded_audio_flow(node, flow_ptr, compliant_caps, compliant_groups, verbose)
    elif compliant_class == "mux":
        update_mux_flow(node, flow_ptr, compliant_caps, compliant_groups, verbose)
    elif compliant_class == "data":
        update_data_flow(node, flow_ptr, compliant_caps, compliant_groups, verbose)
    else:
        # Fallback: dispatch by current type
        if isinstance(poly, (NFlowVideoRaw, NFlowVideoRawValue)):
            update_raw_video_flow(node, flow_ptr, compliant_caps, compliant_groups, verbose)
        elif isinstance(poly, (NFlowVideoCoded, NFlowVideoCodedValue)):
            update_coded_video_flow(node, flow_ptr, compliant_caps, compliant_groups, verbose)
        elif isinstance(poly, (NFlowAudioRaw, NFlowAudioRawValue)):
            update_raw_audio_flow(node, flow_ptr, compliant_caps, compliant_groups, verbose)
        elif isinstance(poly, (NFlowAudioCoded, NFlowAudioCodedValue)):
            update_coded_audio_flow(node, flow_ptr, compliant_caps, compliant_groups, verbose)
        elif isinstance(poly, (NFlowData, NFlowDataValue, NFlowDataJson, NFlowDataJsonValue,
                               NFlowDataSdianc, NFlowDataSdiancValue)):
            update_data_flow(node, flow_ptr, compliant_caps, compliant_groups, verbose)
        elif isinstance(poly, (NFlowMux, NFlowMuxValue)):
            update_mux_flow(node, flow_ptr, compliant_caps, compliant_groups, verbose)

    # Propagate the compliant flow properties to the linked receiver's native
    # constraints. Placed here — not in update_sender_to_compliant_flow — so 
    # that the trunk flow and every mux sub-flow (which call update_flow_to_compliant
    # directly) both propagate. Uses the possibly-rebound flow_ptr (class transitions
    # above).
    _update_receiver_constraints_to_flow_properties(
        node, flow_ptr, compliant_caps, compliant_groups, verbose,
    )



# ---------------------------------------------------------------------------
# Phase 5: Receiver compatibility
# ---------------------------------------------------------------------------

def check_receiver_compatibility(
    node: Any,
    receiver_id: str,
    stream_caps: Any = None,
    verbose: bool = False,
) -> bool:
    """Check if a stream is compatible with receiver capabilities.

    Three independent gates:
    1. media_types: stream's media_type must be in receiver's allowed media_types
    2. event_types: stream's event_type must be in receiver's allowed event_types
    3. constraint_sets: stream properties must satisfy at least one constraint set

    All three must pass. Gates 1 & 2 only apply to trunk (layer < 0).

    Args:
        node: Node instance.
        receiver_id: Receiver resource ID.
        stream_caps: CCF CapSet from SDP or flow. If None, returns True (no stream).
        verbose: Print CCF state for debugging.

    Returns:
        True if stream is within receiver caps, False otherwise.
    """
    try:
        from caps.MatroxCCF import (
            conset_included_in_caps,
            CapFormatMediaType, CapFormatEventType,
        )
    except ImportError:
        return True

    if stream_caps is None:
        return True

    # Get receiver
    receiver = node.receivers.get(receiver_id)
    if receiver is None:
        return True

    if verbose:
        print(f"  [check_receiver] receiver={receiver_id}")
        print(f"  [check_receiver] Stream caps:\n    {stream_caps}")

    # Capability-matching contract: to be compatible a stream must satisfy
    # ALL the capability members that are DEFINED (media_types, event_types,
    # constraint_sets). A member that is ABSENT does not constrain (the
    # receiver is universal on it); a member that is DEFINED-BUT-EMPTY
    # constrains to nothing (the receiver accepts nothing). The two are
    # told apart by the generated types' ``.defined`` flag — the helpers
    # below return ``None`` for absent and a (possibly empty) list / bool
    # for defined.

    # --- Gate 1: media_types check ---
    # Layers are not subject to media_types constraints — trunk only.
    receiver_media_types = _get_receiver_media_types(receiver)
    if receiver_media_types is not None:  # defined (empty or not)
        stream_mt = stream_caps.caps.get(CapFormatMediaType)
        if stream_mt is not None and stream_mt.value.values:
            mt_str = str(stream_mt.value.values[0])
            # Empty list ⇒ nothing matches ⇒ reject (accept-nothing).
            if mt_str not in receiver_media_types:
                if verbose:
                    print(f"  [check_receiver] REJECTED by media_types: {mt_str} not in {receiver_media_types}")
                return False

    # --- Gate 2: event_types check ---
    receiver_event_types = _get_receiver_event_types(receiver)
    if receiver_event_types is not None:  # defined (empty or not)
        stream_et = stream_caps.caps.get(CapFormatEventType)
        if stream_et is not None and stream_et.value.values:
            et_str = str(stream_et.value.values[0])
            if et_str not in receiver_event_types:
                if verbose:
                    print(f"  [check_receiver] REJECTED by event_types: {et_str} not in {receiver_event_types}")
                return False

    # --- Gate 3: constraint_sets check ---
    receiver_caps = _get_receiver_ccf_caps(node, receiver)
    if receiver_caps is None or len(receiver_caps.capsets) == 0:
        # No usable constraint sets. Distinguish absent from defined-empty:
        #   constraint_sets: []  (defined-empty) ⇒ accept nothing ⇒ reject
        #   absent               ⇒ unconstrained ⇒ accept
        if _receiver_constraint_sets_defined(receiver):
            if verbose:
                print("  [check_receiver] REJECTED: constraint_sets defined empty (accepts nothing)")
            return False
        return True  # No constraint_sets = accepts anything

    if verbose:
        print(f"  [check_receiver] Receiver caps: {len(receiver_caps.capsets)} capsets")

    stream_conset = stream_caps.to_conset()
    is_included: bool = conset_included_in_caps(stream_conset, receiver_caps)

    if verbose:
        print(f"  [check_receiver] Result: {'COMPATIBLE' if is_included else 'INCOMPATIBLE'}")

    return is_included


def _get_receiver_media_types(receiver: Any) -> list[str] | None:
    """Extract media_types from receiver's IS-04 capabilities.

    Distinguishes absent from defined-empty via the generated type's
    ``.defined`` flag (per the capability-matching contract):

      * ``None``  — ``media_types`` ABSENT → does not constrain (universal).
      * ``[]``    — ``media_types: []`` DEFINED-EMPTY → accepts nothing.
      * ``[...]`` — defined with values → must match one.
    """
    try:
        poly = receiver.get() if hasattr(receiver, 'get') else receiver
        rv = poly.value if hasattr(poly, 'value') else poly
        # Caps is a sibling of ReceiverCore on the format-specific value
        # (e.g. NReceiverVideoValue), NOT a member of ReceiverCore.
        caps = getattr(rv, 'Caps', None)
        if caps is None or not caps.defined:
            return None
        cv = caps.value
        if not hasattr(cv, 'MediaTypes') or not cv.MediaTypes.defined:
            return None
        return [str(mt) for mt in cv.MediaTypes.value]
    except Exception:
        return None


def _get_receiver_event_types(receiver: Any) -> list[str] | None:
    """Extract event_types from receiver's IS-04 capabilities.

    Same absent (``None``) vs defined-empty (``[]``) distinction as
    :func:`_get_receiver_media_types`. Only data receivers have event_types.
    """
    try:
        poly = receiver.get() if hasattr(receiver, 'get') else receiver
        rv = poly.value if hasattr(poly, 'value') else poly
        caps = getattr(rv, 'Caps', None)
        if caps is None or not caps.defined:
            return None
        cv = caps.value
        if not hasattr(cv, 'EventTypes') or not cv.EventTypes.defined:
            return None
        return [str(et) for et in cv.EventTypes.value]
    except Exception:
        return None


def _receiver_constraint_sets_defined(receiver: Any) -> bool:
    """True when the receiver's ``caps.constraint_sets`` member is present
    (even if an empty array); False when absent.

    Lets ``check_receiver_compatibility`` tell ``constraint_sets: []``
    (defined-empty → accept nothing) from an absent member (unconstrained),
    which the cached CCF caps alone cannot — both yield zero capsets.
    """
    try:
        poly = receiver.get() if hasattr(receiver, 'get') else receiver
        rv = poly.value if hasattr(poly, 'value') else poly
        caps = getattr(rv, 'Caps', None)
        if caps is None or not caps.defined:
            return False
        cv = caps.value
        cs = getattr(cv, 'ConstraintSets', None)
        return bool(cs is not None and cs.defined)
    except Exception:
        return False


def _get_receiver_ccf_caps(node: Any, receiver: Any) -> Any:
    """Get receiver's IS-04 capabilities as cached CCF Caps.

    Stored at pipeline build time — no conversion needed.
    """
    from nmos.node import _get_resource_core
    try:
        rc = _get_resource_core(receiver)
        static_id = rc.StaticId.value if rc.StaticId.defined else ""
        return node.receiver_ccf_caps.get(static_id)
    except Exception:
        return None


def get_sdp_to_caps(
    node: Any,
    receiver_id: str,
    mux: bool = False,
    verbose: bool = False,
) -> Any:
    """Extract capabilities from a receiver's SDP transport file.

    Parses the cached SDP, extracts all format and transport properties
    per encoding type, and returns them as a CCF CapSet.

    Args:
        node: Node instance (for SDP cache access).
        receiver_id: Receiver resource ID.
        mux: True if the receiver is a mux receiver (affects AM824/MP2T media_type).
        verbose: Print extraction details.

    Returns a CCF CapSet, or None if no SDP is available or parsing fails.
    """
    try:
        from caps.MatroxCCF import (
            CapSet,
            Cap,
            RangeValue,
            RangeType,
        )
    except ImportError:
        return None

    from nmos.node.store import to_static_id

    static_id = to_static_id(receiver_id)

    # node.sdp stores pre-parsed MatroxSdp objects (o.sdp cache).
    # Parsing happens once at storage time, not here.
    sdp_obj = node.sdp.get(static_id) if hasattr(node, 'sdp') else None
    if sdp_obj is None:
        return None

    media = sdp_obj.primary_media if hasattr(sdp_obj, 'primary_media') else None
    if media is None:
        return None

    caps: dict[str, Cap] = {}

    def _s(name: str, val: str) -> None:
        caps[name] = Cap(name, RangeValue(values=(val,), type=RangeType.STRING))

    def _i(name: str, val: int) -> None:
        caps[name] = Cap(name, RangeValue(values=(val,), type=RangeType.INT))

    def _r(name: str, num: int, den: int = 1) -> None:
        caps[name] = Cap(name, RangeValue(values=(Fraction(num, den),), type=RangeType.RATIONAL))

    def _b(name: str, val: bool) -> None:
        caps[name] = Cap(name, RangeValue(values=(val,), type=RangeType.BOOL))

    def _f(name: str, val: float) -> None:
        caps[name] = Cap(name, RangeValue(values=(val,), type=RangeType.FLOAT))

    # --- Helper: colorspace from SDP colorimetry + color_range ---
    def _colorspace_from_sdp(colorimetry: Any, color_range: Any) -> str | None:
        """Map SDP colorimetry to NMOS colorspace (getColorspaceFromSdp)."""
        if color_range is not None and str(color_range).lower() == "full":
            return UNSPECIFIED.s
        c = str(colorimetry).upper() if colorimetry else ""
        _MAP = {
            BT601.s: BT601.s, BT709.s: BT709.s, BT2020.s: BT2020.s,
            BT2100.s: BT2100.s, BT601_5.s: BT601_5.s, BT709_2.s: BT709_2.s,
            ST2065_1.s: ST2065_1.s, ST2065_3.s: ST2065_3.s, XYZ.s: XYZ.s,
        }
        return _MAP.get(c, UNSPECIFIED.s)

    def _transfer_from_sdp(transfer: Any) -> str | None:
        """Map SDP transfer characteristic to NMOS (getTransferCharacteristicFromSdp)."""
        t = str(transfer).upper() if transfer else ""
        # Keys are SDP spellings, values NMOS ones; the two vocabularies agree on
        # every transfer characteristic. Anything else maps to UNSPECIFIED.
        _MAP = {
            SDR.s: SDR.s, HLG.s: HLG.s, PQ.s: PQ.s, LINEAR.s: LINEAR.s,
            BT2100LINPQ.s: BT2100LINPQ.s, BT2100LINHLG.s: BT2100LINHLG.s,
            ST2065_1.s: ST2065_1.s, ST428_1.s: ST428_1.s, DENSITY.s: DENSITY.s,
            ST2115LOGS3.s: ST2115LOGS3.s,
        }
        return _MAP.get(t, UNSPECIFIED.s)

    # --- Common video property extraction ---
    def _extract_video_common() -> None:
        if media.width:
            _i(CapFormatFrameWidth.s, media.width)
        if media.height:
            _i(CapFormatFrameHeight.s, media.height)
        # RANGE is optional in ST 2110-20 -- absent means NARROW -- and only FULL
        # changes the mapping, so colorimetry alone determines the colorspace.
        # Requiring RANGE suppressed a determinable value, and an omitted
        # capability is not checked by the inclusion test at all.
        if media.colorimetry is not None:
            cs = _colorspace_from_sdp(media.colorimetry, media.color_range)
            if cs:
                _s(CapFormatColorspace.s, cs)
        if media.transfer_characteristic is not None:
            tc = _transfer_from_sdp(media.transfer_characteristic)
            if tc:
                _s(CapFormatTransferCharacteristic.s, tc)
        if media.sampling is not None:
            _s(CapFormatColorSampling.s, str(media.sampling))
        if media.depth:
            _i(CapFormatComponentDepth.s, media.depth)
        if media.exact_frame_rate_numerator and media.exact_frame_rate_denominator:
            _r(CapFormatGrainRate.s, media.exact_frame_rate_numerator, media.exact_frame_rate_denominator)
        # ST 2110-20 signals PsF as "interlace; segmented" -- segmented qualifies
        # interlace rather than replacing it, so it must be tested inside the
        # interlaced branch or a PsF stream reads back as interlaced_bff.
        if not media.interlaced:
            _s(CapFormatInterlaceMode.s, Progressive.s)
        elif getattr(media, 'segmented', False):
            _s(CapFormatInterlaceMode.s, InterlacedPsf.s)
        else:
            _s(CapFormatInterlaceMode.s, InterlacedTff.s if media.top_field_first else InterlacedBff.s)

    # --- Common audio transport ---
    def _extract_audio_transport() -> None:
        """Transport capabilities common to every audio encoding.

        transport:bit_rate is deliberately NOT here. For uncompressed essence the
        bit rate is fully determined by sample_rate x channels x sample_depth, so
        stating it constrains nothing a receiver cannot already derive from the
        format capabilities -- the same reason video/raw does not report it while
        jxsv/H.264/H.265 do. Only the AAC family, whose bit rate is an independent
        parameter, reports it; those branches emit it themselves.
        """
        # packet_time capabilities are expressed in MILLISECONDS (float);
        # the SDP layer stores ptime/maxptime in microseconds.
        ptime_us = getattr(media, 'p_time_us', 0)
        if ptime_us:
            _f(CapTransportPacketTime.s, ptime_us / 1000.0)
        max_ptime_us = getattr(media, 'max_p_time_us', 0)
        if max_ptime_us:
            _f(CapTransportMaxPacketTime.s, max_ptime_us / 1000.0)

    # --- SDP specification checks (CheckSpecification per encoding) ---
    try:
        from sdp.MatroxSdpCheck import (
            SdpCheckError,
            check_sdp_rfc4175, check_sdp_rfc9134, check_sdp_rfc8331,
            check_sdp_rfc6184, check_sdp_rfc7798, check_sdp_rfc2250,
            check_sdp_rfc3551, check_sdp_rfc3640, check_sdp_rfc6416,
            check_sdp_st2110_10, check_sdp_st2110_20, check_sdp_st2110_21,
            check_sdp_st2110_22, check_sdp_st2110_30, check_sdp_st2110_31,
            check_sdp_st2110_40,
        )
        _has_checks = True
    except ImportError:
        _has_checks = False

    def _check(*check_fns: Any) -> bool:
        """Run SDP spec checks on media. Returns False if any check fails."""
        if not _has_checks:
            return True
        for fn in check_fns:
            try:
                fn(media)
            except SdpCheckError:
                return False
        return True

    # --- Dispatch by media type (the SDP m= line type: video/audio/application) ---
    media_type_enum = getattr(media, 'type', None)
    encoding = media.encoding_name

    if media_type_enum is not None and str(media_type_enum) == "video":
        # VIDEO
        enc_str = str(encoding).lower() if encoding else ""

        if enc_str == "raw":
            if not _check(check_sdp_rfc4175, check_sdp_st2110_10, check_sdp_st2110_21, check_sdp_st2110_20):
                return None
            _s(CapFormatMediaType.s, VideoRaw.s)
            _extract_video_common()

        elif enc_str == "jxsv":
            if not _check(check_sdp_rfc9134, check_sdp_st2110_10, check_sdp_st2110_21, check_sdp_st2110_22):
                return None
            _s(CapFormatMediaType.s, VideoCodedJxsv.s)
            _extract_video_common()
            if media.profile is not None:
                _s(CapFormatProfile.s, str(media.profile))
            if media.level is not None:
                _s(CapFormatLevel.s, str(media.level))
            if media.sub_level is not None:
                _s(CapFormatSublevel.s, str(media.sub_level))
            if media.fbb_level is not None:
                _s(CapFormatFbblevel.s, str(media.fbb_level))
            # Packet mode
            if media.jxsv_packet_mode is not None and str(media.jxsv_packet_mode).lower() == CodeStream.s:
                _s(CapTransportPacketTransmissionMode.s, CodeStream.s)
            else:
                jxsv_trans = getattr(media, 'jxsv_trans_mode', None)
                if jxsv_trans is not None and str(jxsv_trans).lower() == "sequential":
                    _s(CapTransportPacketTransmissionMode.s, SliceSequential.s)
                else:
                    _s(CapTransportPacketTransmissionMode.s, SliceOutOfOrder.s)
            if media.bitrate_kbits:
                _i(CapTransportBitRate.s, media.bitrate_kbits)

        elif enc_str == "smpte291":
            if not _check(check_sdp_rfc8331, check_sdp_st2110_10, check_sdp_st2110_40):
                return None
            if encoding is not None:
                _s(CapFormatMediaType.s, "video/" + str(encoding))

        elif enc_str == "h264":
            # H.264 over RTP is RFC 6184 on its own; the ST 2110 constraints only
            # bind when the stream declares itself IPMX. Checking them
            # unconditionally would reject conformant non-IPMX senders.
            if not _check(check_sdp_rfc6184):
                return None
            if getattr(media, 'ipmx', False) and not _check(check_sdp_st2110_10, check_sdp_st2110_22):
                return None
            _s(CapFormatMediaType.s, VideoCodedH264.s)
            # Only an IPMX stream is obliged to be ST 2110-22 conformant, so only
            # then does its fmtp carry the geometry parameters. A plain RFC 6184
            # SDP has no standing to declare them, and any it did carry would be
            # unverifiable, so they are ignored rather than trusted.
            if getattr(media, 'ipmx', False):
                _extract_video_common()
            if not media.codec_profile_level_id:
                return None
            try:
                from sdp.MatroxSdp import get_h264_profile_level_from_sdp
                h264_profile, h264_level = get_h264_profile_level_from_sdp(media.codec_profile_level_id)
                _s(CapFormatProfile.s, str(h264_profile))
                _s(CapFormatLevel.s, str(h264_level))
            except Exception:
                return None
            # Packetization mode
            pm = media.h264_packetization_mode
            if pm == 0:
                _s(CapTransportPacketTransmissionMode.s, SingleNalUnit.s)
            elif pm == 1:
                _s(CapTransportPacketTransmissionMode.s, NonInterleavedNalUnits.s)
            elif pm == 2:
                _s(CapTransportPacketTransmissionMode.s, InterleavedNalUnits.s)
            # Parameter sets transport mode
            ps = media.h264_parameter_sets
            if not ps:
                _s(CapTransportParameterSetsTransportMode.s, InBand.s)
            elif ps.endswith(","):
                _s(CapTransportParameterSetsTransportMode.s, InAndOutOfBand.s)
            else:
                _s(CapTransportParameterSetsTransportMode.s, OutOfBand.s)
            if media.bitrate_kbits:
                _i(CapTransportBitRate.s, media.bitrate_kbits)

        elif enc_str == "h265":
            # As for H.264: RFC 7798 always, ST 2110 only for IPMX streams.
            if not _check(check_sdp_rfc7798):
                return None
            if getattr(media, 'ipmx', False) and not _check(check_sdp_st2110_10, check_sdp_st2110_22):
                return None
            _s(CapFormatMediaType.s, VideoCodedH265.s)
            # As for H.264: geometry only from an IPMX (ST 2110-22) stream.
            if getattr(media, 'ipmx', False):
                _extract_video_common()
            if not media.h265_interop_constraints or not media.h265_profile_compatibility_indicator:
                return None
            try:
                from sdp.MatroxSdp import get_h265_profile_level_from_sdp
                tier_flag = 1 if media.h265_tier_flag else 0
                h265_profile, h265_level, progressive = get_h265_profile_level_from_sdp(
                    media.h265_profile_space, media.h265_profile_id,
                    tier_flag, media.h265_level_id,
                    media.h265_profile_compatibility_indicator,
                    media.h265_interop_constraints)
                _s(CapFormatProfile.s, str(h265_profile))
                _s(CapFormatLevel.s, str(h265_level))
                if progressive:
                    _s(CapFormatInterlaceMode.s, Progressive.s)
            except Exception:
                return None
            # DON diff → packet mode
            if media.h26x_max_don_diff > 0:
                _s(CapTransportPacketTransmissionMode.s, InterleavedNalUnits.s)
            else:
                _s(CapTransportPacketTransmissionMode.s, NonInterleavedNalUnits.s)
            # VPS/SPS/PPS → parameter sets transport mode
            vps = media.h265_vps
            sps = media.h265_sps
            pps = media.h265_pps
            if not vps and not sps and not pps:
                _s(CapTransportParameterSetsTransportMode.s, InBand.s)
            elif (vps and vps.endswith(",")) or (sps and sps.endswith(",")) or (pps and pps.endswith(",")):
                _s(CapTransportParameterSetsTransportMode.s, InAndOutOfBand.s)
            else:
                _s(CapTransportParameterSetsTransportMode.s, OutOfBand.s)
            if media.bitrate_kbits:
                _i(CapTransportBitRate.s, media.bitrate_kbits)

        elif enc_str == "mp2t":
            if not _check(check_sdp_rfc2250):
                return None
            if encoding is not None:
                prefix = "video/" if not mux else "application/"
                _s(CapFormatMediaType.s, prefix + str(encoding))

    elif media_type_enum is not None and str(media_type_enum) == "audio":
        # AUDIO
        enc_str = str(encoding) if encoding else ""
        enc_lower = enc_str.lower()

        # L8, L16, L20, L24 — 4 separate cases, each with RFC3551 + ST2110_10 + ST2110_30
        _DEPTH_MAP = {"l8": 8, "l16": 16, "l20": 20, "l24": 24}
        if enc_lower in _DEPTH_MAP:
            if not _check(check_sdp_rfc3551, check_sdp_st2110_10, check_sdp_st2110_30):
                return None
            _s(CapFormatMediaType.s, "audio/" + enc_str)
            if media.channels:
                _i(CapFormatChannelCount.s, media.channels)
            _i(CapFormatSampleDepth.s, _DEPTH_MAP[enc_lower])
            if media.sample_rate:
                _r(CapFormatSampleRate.s, media.sample_rate)
            _extract_audio_transport()

        elif enc_lower == "am824":
            if not _check(check_sdp_rfc3551, check_sdp_st2110_10, check_sdp_st2110_31):
                return None
            prefix = "audio/" if not mux else "application/"
            _s(CapFormatMediaType.s, prefix + enc_str)
            if media.channels:
                _i(CapFormatChannelCount.s, media.channels)
            if media.sample_rate:
                _r(CapFormatSampleRate.s, media.sample_rate)
            _extract_audio_transport()

        elif enc_lower == "mpeg4-generic":
            # AAC (RFC 3640)
            if not _check(check_sdp_rfc3640):
                return None
            _s(CapFormatMediaType.s, "audio/" + enc_str)
            if media.channels:
                _i(CapFormatChannelCount.s, media.channels)
            if media.sample_rate:
                _r(CapFormatSampleRate.s, media.sample_rate)
            try:
                from sdp.MatroxSdp import get_aac_profile_level_from_sdp
                aac_profile, aac_level = get_aac_profile_level_from_sdp(media.codec_profile_level_id)
                _s(CapFormatProfile.s, str(aac_profile))
                _s(CapFormatLevel.s, str(aac_level))
            except Exception:
                return None
            if media.aac_bitrate:
                _i(CapFormatBitRate.s, media.aac_bitrate // 1000)  # bps → Kbps
            # Interleaving
            if media.aac_max_displacement > 0:
                _s(CapTransportPacketTransmissionMode.s, InterleavedAccessUnits.s)
            else:
                _s(CapTransportPacketTransmissionMode.s, NonInterleavedAccessUnits.s)
            # Config presence → parameter sets transport
            if not media.aac_config:
                _s(CapTransportParameterSetsTransportMode.s, InBand.s)
            else:
                _s(CapTransportParameterSetsTransportMode.s, OutOfBand.s)
            # AAC bit rate is an independent parameter, so the transport rate
            # from b=AS is worth reporting (unlike L-PCM / AM824).
            if media.bitrate_kbits:
                _i(CapTransportBitRate.s, media.bitrate_kbits)
            _extract_audio_transport()
            # RFC 3640: constant duration overrides ptime
            if media.aac_constant_duration and media.sample_rate:
                ptime_us = (media.aac_constant_duration * 1000000) // media.sample_rate
                _f(CapTransportPacketTime.s, ptime_us / 1000.0)
                _f(CapTransportMaxPacketTime.s, ptime_us / 1000.0)

        elif enc_lower in ("mp4a-latm", "mp4a-adts"):
            # AAC-LATM / AAC-ADTS (RFC 6416)
            if not _check(check_sdp_rfc6416):
                return None
            _s(CapFormatMediaType.s, "audio/" + enc_str)
            if media.channels:
                _i(CapFormatChannelCount.s, media.channels)
            if media.sample_rate:
                _r(CapFormatSampleRate.s, media.sample_rate)
            try:
                from sdp.MatroxSdp import get_aac_profile_level_from_sdp
                aac_profile, aac_level = get_aac_profile_level_from_sdp(media.codec_profile_level_id)
                _s(CapFormatProfile.s, str(aac_profile))
                _s(CapFormatLevel.s, str(aac_level))
            except Exception:
                return None
            if media.aac_bitrate:
                _i(CapFormatBitRate.s, media.aac_bitrate // 1000)
            if media.aac_max_displacement > 0:
                _s(CapTransportPacketTransmissionMode.s, InterleavedAccessUnits.s)
            else:
                _s(CapTransportPacketTransmissionMode.s, NonInterleavedAccessUnits.s)
            # LATM/ADTS parameter sets logic
            if media.aac_config_present:
                if not media.aac_config:
                    _s(CapTransportParameterSetsTransportMode.s, InBand.s)
                else:
                    _s(CapTransportParameterSetsTransportMode.s, InAndOutOfBand.s)
            else:
                if not media.aac_config:
                    return None  # Error: no config available
                else:
                    _s(CapTransportParameterSetsTransportMode.s, OutOfBand.s)
            # AAC bit rate is an independent parameter, so the transport rate
            # from b=AS is worth reporting (unlike L-PCM / AM824).
            if media.bitrate_kbits:
                _i(CapTransportBitRate.s, media.bitrate_kbits)
            _extract_audio_transport()
            if media.aac_constant_duration and media.sample_rate:
                ptime_us = (media.aac_constant_duration * 1000000) // media.sample_rate
                _f(CapTransportPacketTime.s, ptime_us / 1000.0)
                _f(CapTransportMaxPacketTime.s, ptime_us / 1000.0)

    elif media_type_enum is not None and str(media_type_enum) == "application":
        # APPLICATION — validate against known formats (Json, Usb, Mpeg2TS, Rtsp)
        _KNOWN_APP_FORMATS = {"json", "usb", "mp2t", "mpeg2ts", "rtsp"}
        fmt_str = media.format_string
        if fmt_str is not None:
            if str(fmt_str).lower() not in _KNOWN_APP_FORMATS:
                return None  # Unknown application format — reject
            _s(CapFormatMediaType.s, "application/" + str(fmt_str))

    # --- Common transport properties ---
    media_clock = getattr(media, 'media_clock_type', None)
    if media_clock is not None and str(media_clock).lower() == "sender":
        _b(CapTransportSynchronousMedia.s, False)
    else:
        _b(CapTransportSynchronousMedia.s, True)

    ts_ref = getattr(media, 'ts_ref_clock_source', None)
    if ts_ref is not None and str(ts_ref).lower() == Ptp.s:
        _s(CapTransportClockRefType.s, Ptp.s)
    else:
        _s(CapTransportClockRefType.s, Internal.s)

    # privacy and hkep are reported only when true, matching get_flow_to_caps and
    # both test-suite converters. A stream that is not protected simply says
    # nothing, rather than asserting false: the SDP carries no attribute for the
    # negative case either, so there is nothing to state.
    privacy_val = getattr(media, 'privacy', False)
    if privacy_val:
        _b(CapTransportPrivacy.s, True)

    hkep_val = getattr(media, 'hkep', False)
    if hkep_val:
        _b(CapTransportHkep.s, True)

    if verbose:
        print(f"  [get_sdp_props] Extracted {len(caps)} properties from SDP")

    if not caps:
        return None

    return CapSet(caps=caps, preference=100, label="SDP properties")


def get_generic_properties(format_urn: str, properties: dict[str, Any]) -> dict[str, Any]:
    """Filter properties to keep only format-independent generic ones."""

    _GENERIC_KEYS: dict[str, list[str]] = {
        FormatVideo.s: [
            CapFormatGrainRate.s, CapFormatFrameWidth.s, CapFormatFrameHeight.s,
            CapFormatInterlaceMode.s, CapFormatColorspace.s,
            CapFormatTransferCharacteristic.s, CapFormatColorSampling.s,
            CapFormatComponentDepth.s,
        ],
        FormatAudio.s: [
            CapFormatChannelCount.s, CapFormatSampleRate.s, CapFormatSampleDepth.s,
        ],
        FormatMux.s: [
            CapFormatVideoLayers.s, CapFormatAudioLayers.s, CapFormatDataLayers.s,
        ],
    }

    allowed = _GENERIC_KEYS.get(format_urn, [])
    return {k: v for k, v in properties.items() if k in allowed}


# ---------------------------------------------------------------------------
# Phase 6: State management
# ---------------------------------------------------------------------------

def validate_active_constraints(
    node: Any,
    sender_id: str,
    active_cons: Cons,
    verbose: bool = False,
) -> tuple[Any, str | None]:
    """Validate active constraints (Cons): names, metadata, and mux layers.

    Steps:
    1. Mark every user constraint as original (so downstream fix-ups can
       distinguish user-pinned parameters from inherited ones)
    2. Validate constraint names against the sender format's namespace
    3. Normalize constraints (CCF handles mux layer/format validation,
       namespace filtering, trunk/layer creation for missing layers)

    Capability-inclusion checking is NOT done here — it happens in
    merge_active_constraints, where each constraint set must fit at
    least one capability set (and is merged onto the one it fits).

    Returns:
        (normalized_cons, None) on success — normalized includes
        auto-generated defaults for missing mux layers.
        (None, error_message) on failure.
    """
    try:
        from caps.MatroxCCF import Cons as _Cons, ConSet as _ConSet
    except ImportError:
        return active_cons, None

    sender = node.senders.get(sender_id)
    if sender is None:
        return active_cons, None

    if active_cons is None:
        return None, None

    sender_caps = _get_sender_ccf_caps(node, sender)
    if sender_caps is None or len(sender_caps.capsets) == 0:
        return active_cons, None

    format_urn = sender.Format.value.s if sender.Format.defined else ""

    # Mark constraints as original and validate names
    for cs in active_cons.consets:
        for con_name, con in cs.cons.items():
            con.original = True

        # Validate constraint names — unsupported names are rejected (not just filtered)
        for con_name in list(cs.cons.keys()):
            if not is_constraint_name_supported(format_urn, str(con_name)):
                if is_constraint_name_of_transport_category(str(con_name)):
                    continue  # Transport constraints silently ignored
                if verbose:
                    print(f"  [validate_constraints] Unsupported constraint: {con_name}")
                return None, f"unsupported constraint: {con_name}"

    # Normalize constraints using CCF Cons.normalize() directly (Cons → Cons).
    # For non-mux: validates no format/layer metadata, filters namespace
    # For mux: validates layers against parent flows, generates defaults for missing layers,
    #          filters constraint names per sub-format namespace
    try:
        if format_urn == FormatMux.s:
            # Count sub-flow layers per format from the mux flow's parents
            video_layers, audio_layers, data_layers = _count_mux_layers(node, sender)
            normalized = active_cons.normalize(
                video_layers=video_layers,
                audio_layers=audio_layers,
                data_layers=data_layers,
                trunk_namespace=set(SUPPORTED_MUX_CONSTRAINTS),
                video_namespace=set(SUPPORTED_VIDEO_CONSTRAINTS),
                audio_namespace=set(SUPPORTED_AUDIO_CONSTRAINTS),
                data_namespace=set(SUPPORTED_DATA_CONSTRAINTS),
            )
        elif format_urn == FormatVideo.s:
            normalized = active_cons.normalize(
                trunk_namespace=set(SUPPORTED_VIDEO_CONSTRAINTS),
            )
        elif format_urn == FormatAudio.s:
            normalized = active_cons.normalize(
                trunk_namespace=set(SUPPORTED_AUDIO_CONSTRAINTS),
            )
        elif format_urn == FormatData.s:
            normalized = active_cons.normalize(
                trunk_namespace=set(SUPPORTED_DATA_CONSTRAINTS),
            )
        else:
            normalized = active_cons
    except ValueError as e:
        return None, str(e)

    if verbose:
        print(f"  [validate_constraints] sender={sender_id} format={format_urn}")
        print(f"  [validate_constraints] Constraints: {len(normalized.consets)} consets")
        print(f"  [validate_constraints] Result: VALID")

    return normalized, None


def merge_active_constraints(
    node: Any,
    sender_id: str,
    normalized_cons: Any,
    verbose: bool = False,
) -> tuple[Any, str | None]:
    """Merge each constraint set onto the capability set it fits.

    For each (non-empty) constraint set, scan the sender's capability
    sets of the same part (format/layer) in preference order; the first
    capability set that fully includes the constraint set is cloned to
    a ConSet, given the constraint set's preference/label/groups, and
    overlaid with the user's constrained parameters. The merged set
    therefore inherits every capability the user did not constrain —
    notably media_type — so forcing the flow from it always yields a
    self-consistent, capability-compliant operating point.

    Empty constraint sets (the auto-generated defaults for unconstrained
    mux layers) are carried through unchanged: they constrain nothing, so
    forcing a sub-flow from one keeps the flow's current properties — but
    they must be present in the merged result for the layer to be
    force-able at all.

    Returns:
        (merged_cons, None) on success — a Cons of capability-derived
        constraint sets with user parameters overlaid, plus the
        unconstrained-layer defaults.
        (None, error_message) when a constraint set fits no capability
        set (the constraints are unsatisfiable).
    """
    try:
        from caps.MatroxCCF import Cons as _Cons, conset_included_in_capset
    except ImportError:
        return normalized_cons, None

    sender = node.senders.get(sender_id)
    if sender is None:
        return normalized_cons, None

    if normalized_cons is None:
        return None, None

    sender_caps = _get_sender_ccf_caps(node, sender)
    if sender_caps is None or len(sender_caps.capsets) == 0:
        # Unconstrained sender: nothing to merge onto.
        return _Cons(consets=[]), None

    merged_consets = []
    for conset in normalized_cons.consets:
        # Auto-generated defaults for unconstrained mux layers carry no
        # constraints — pass them through so the layer remains force-able
        # (forcing from one keeps the sub-flow's current properties).
        if len(conset.cons) == 0:
            merged_consets.append(conset)
            continue

        # Candidate capability sets of the same part, preference-sorted.
        if conset.format is not None:
            candidates = sender_caps.get(format=conset.format, layer=conset.layer)
        else:
            candidates = sender_caps.get()

        matched = None
        for capset in candidates.capsets:
            if conset_included_in_capset(conset, capset):
                matched = capset
                break

        if matched is None:
            if verbose:
                print(f"  [merge_constraints] ConSet '{conset.label}' NOT included in any sender CapSet")
            return None, f"constraint set '{conset.label}' not included in sender capabilities"

        # Clone the matched capability set as a ConSet (inherits media_type
        # and every other capability), then overlay the user's parameters.
        merged = matched.to_conset()
        merged.preference = conset.preference
        merged.label = conset.label
        merged.layer_compatibility_groups = conset.layer_compatibility_groups

        for name, con in conset.cons.items():
            # An unconstrained parameter keeps the capability's value.
            if con.value.infinite:
                continue
            # Overlay by reference: preserves original=True on user params,
            # which the codec/PCM fix-ups read to decide what may change.
            merged.cons[name] = con

        if verbose:
            print(f"  [merge_constraints] ConSet '{conset.label}' merged onto CapSet '{matched.label}'")

        merged_consets.append(merged)

    return _Cons(consets=merged_consets), None


def _count_mux_layers(node: Any, sender: Any) -> tuple[int, int, int]:
    """Count video/audio/data sub-flow layers from a mux sender's flow parents.

    Mirrors the parent flow enumeration in validateActiveConstraints.
    """
    video_layers = audio_layers = data_layers = 0

    flow_id = sender.FlowId.value if sender.FlowId.defined else None
    if flow_id is None:
        return 0, 0, 0

    flow_ptr = node.flows.get(flow_id)
    if flow_ptr is None:
        return 0, 0, 0

    from nmos.node import _get_flow_core
    flow_core = _get_flow_core(flow_ptr)
    if not flow_core.Parents.defined:
        return 0, 0, 0

    parents = flow_core.Parents.value
    from nmos.node import _get_flow_core as _gfc

    # Collect layers per format and validate
    layers_per_format: dict[str, list[int]] = {}

    for parent_id in parents:
        parent_ptr = node.flows.get(parent_id)
        if parent_ptr is None:
            raise UnexpectedError(f"mux parent flow {parent_id} not found")
        parent_inner = parent_ptr.get() if hasattr(parent_ptr, 'get') else parent_ptr
        if parent_inner is None:
            raise UnexpectedError(f"mux parent flow {parent_id} has no inner value")

        # Validate Layer attribute
        parent_fc = _gfc(parent_ptr)
        if not parent_fc.Layer.defined:
            raise UnexpectedError(f"mux parent flow {parent_id} missing Layer attribute")
        parent_layer = parent_fc.Layer.value
        if parent_layer < 0:
            raise UnexpectedError(f"mux parent flow {parent_id} has invalid Layer={parent_layer}")

        parent_format = parent_inner.Format.value.s if hasattr(parent_inner, 'Format') and parent_inner.Format.defined else ""
        if parent_format not in layers_per_format:
            layers_per_format[parent_format] = []
        layers_per_format[parent_format].append(parent_layer)

    # Validate sequential layers per format
    for fmt, layers in layers_per_format.items():
        for expected, actual in enumerate(sorted(layers)):
            if actual != expected:
                raise UnexpectedError(
                    f"mux sub-flow layers not sequential for {fmt}: "
                    f"expected {expected}, got {actual}"
                )

    video_layers = len(layers_per_format.get(FormatVideo.s, []))
    audio_layers = len(layers_per_format.get(FormatAudio.s, []))
    data_layers = len(layers_per_format.get(FormatData.s, []))

    return video_layers, audio_layers, data_layers


def force_flow_properties_compatibility(
    node: Any,
    flow_ptr: Any,
    active_cons: Cons,
    layer: int = -1,
    format_urn: str = "",
    reset: bool = False,
    verbose: bool = False,
) -> tuple[Any, list[int] | None]:
    """Constrain flow properties to match active constraints and apply fix-ups.

    Args:
        active_cons: CCF Cons. Active constraints are semantically constraints,
            not capabilities. Callers must convert to Cons before calling.

    For each constraint set (sorted by preference), tries to produce a
    valid set of compliant properties:
    1. For each flow property, if a matching constraint exists and the
       current value doesn't satisfy it (or reset=True), take the first
       value from the constraint.
    2. Special cases: CapFormatMediaType.s iterates candidates matching format;
       CapFormatLevel.s picks the highest value.
    3. Apply fix-ups (PCM sample depth, video width/height, coded video).

    Returns (compliant_caps, compliant_groups):
        compliant_caps: CapSet with compliant properties, or None if no match.
        compliant_groups: layer_compatibility_groups from the winning ConSet,
            or None if undefined (meaning: part of all groups).
    """
    try:
        from caps.MatroxCCF import (
            CapSet,
            Cap,
            RangeValue,
            RangeType,
            value_included_in_range,
        )
    except ImportError:
        return None, None

    from nmos.node.flow_caps import get_flow_to_caps

    if active_cons is None or len(active_cons.consets) == 0:
        return None, None

    # Get flow caps
    flow_caps = get_flow_to_caps(node, flow_ptr)

    if verbose:
        print(f"  [force_flow] Flow caps:\n    {flow_caps}")

    # Iterate constraint sets by preference (CCF sorts via .get())
    target = active_cons
    if layer >= 0 and format_urn:
        target = target.get(format=format_urn, layer=layer)
    else:
        target = target.get()

    for conset in target.consets:
        # MetaEnabled is checked elsewhere — CCF strips meta:enabled during
        # conversion. CCF consets with meta:enabled=false should have been
        # excluded before reaching this function, or handled by CCF's preference sort.

        # Only a constraint set with explicit positive preference can force properties
        if conset.preference <= 0:
            continue

        # Build compliant properties: start from flow, override from constraint
        compliant: dict[str, Cap] = {}
        failed = False

        def _constraint_replacement_value(constraint: Any) -> Any:
            """Concrete value to write for a forced/violated (non-enum) range constraint.

            The caller handles enums; for a range pick the minimum bound, else the 
            maximum. The current value is never preserved — on the force path 
            (reset=True) the result must depend only on the constraint, and on the
            repair path the current value is out of range.
            """
            rv = constraint.value
            if rv.min is not None:
                return rv.min
            if rv.max is not None:
                return rv.max
            return None

        for prop_name, prop_cap in flow_caps.caps.items():
            # Default: keep flow's current value
            compliant[prop_name] = prop_cap

            # Check if constraint has this property
            constraint = conset.cons.get(prop_name)
            if constraint is None or constraint.value.infinite:
                continue  # No constraint or unconstrained

            # Check if current value satisfies constraint
            current_value = None
            current_ok = False
            if prop_cap.value.values:
                current_value = prop_cap.value.values[0]
                try:
                    current_ok = value_included_in_range(current_value, constraint.value)
                except (ValueError, TypeError):
                    current_ok = False

            if not current_ok or reset:
                # Take value from constraint
                if constraint.value.values and len(constraint.value.values) > 0:
                    # Special case: MediaType — find first matching format
                    if prop_name == CapFormatMediaType.s:
                        found = False
                        for candidate in constraint.value.values:
                            candidate_format = get_format_from_media_type(str(candidate))
                            if candidate_format == format_urn or format_urn == "":
                                compliant[prop_name] = Cap(prop_name, RangeValue(
                                    values=(candidate,), type=constraint.value.type))
                                found = True
                                break
                        if not found:
                            failed = True
                            break
                    # Special case: Level — pick last (highest)
                    elif prop_name == CapFormatLevel.s:
                        compliant[prop_name] = Cap(prop_name, RangeValue(
                            values=(constraint.value.values[-1],), type=constraint.value.type))
                    else:
                        # Default: first value
                        compliant[prop_name] = Cap(prop_name, RangeValue(
                            values=(constraint.value.values[0],), type=constraint.value.type))
                else:
                    replacement = _constraint_replacement_value(constraint)
                    if replacement is not None:
                        compliant[prop_name] = Cap(prop_name, RangeValue(
                            values=(replacement,), type=constraint.value.type))

        if failed:
            continue  # Try next constraint set

        # Format properties the flow does not yet carry are adopted from the
        # constraint set, the same way a defined-but-violating value is
        # replaced. This is what carries the codec-specific properties across
        # a codec change — e.g. a flow transitioning to JPEG-XS has no
        # sublevel/fbblevel to violate, and the merged constraint sets derive
        # from the capability sets, so the adopted value is the capability's.
        # Transport constraints never describe flow properties and are
        # excluded.
        for con_name, constraint in conset.cons.items():
            if con_name in compliant:
                continue
            if ":cap:format:" not in con_name:
                continue
            if constraint.value.infinite:
                continue
            if constraint.value.values:
                if con_name == CapFormatLevel.s:
                    # Same convention as the replacement path: the level
                    # seeds at the highest allowed value.
                    value = constraint.value.values[-1]
                else:
                    value = constraint.value.values[0]
                compliant[con_name] = Cap(con_name, RangeValue(
                    values=(value,), type=constraint.value.type))
            else:
                replacement = _constraint_replacement_value(constraint)
                if replacement is not None:
                    compliant[con_name] = Cap(con_name, RangeValue(
                        values=(replacement,), type=constraint.value.type))

        # Apply fix-ups (conset.cons is Dict[str, Constraint], read-only here)
        fix_pcm_sample_depth(compliant, conset.cons, verbose=verbose)
        fix_video_width_height(compliant, conset.cons, verbose=verbose)
        fix_coded_video_flow(compliant, conset.cons, verbose=verbose)

        result = CapSet(caps=compliant, preference=conset.preference, label="compliant")

        # Extract layer_compatibility_groups from the winning conset.
        # None means undefined = part of all groups.
        compliant_groups: list[int] | None = None
        if conset.layer_compatibility_groups is not None:
            compliant_groups = sorted(conset.layer_compatibility_groups)

        if verbose:
            print(f"  [force_flow] Compliant result:\n    {result}")
            print(f"  [force_flow] Compliant groups: {compliant_groups}")

        return result, compliant_groups

    if verbose:
        print(f"  [force_flow] No constraint set matched")
    return None, None


def update_sender_to_compliant_flow(
    node: Any,
    sender_id: str,
    active_cons: Cons,
    layer: int = -1,
    reset: bool = False,
    verbose: bool = False,
) -> bool:
    """Update sender's flow to be compliant with constraints.

    1. Force flow properties compatibility (constrict + fix-ups)
    2. Write back to flow via update_flow_to_compliant
    3. Return success/failure.
    """
    sender = node.senders.get(sender_id)
    if sender is None:
        return False

    flow_id = sender.FlowId.value if sender.FlowId.defined and sender.FlowId.value else None
    if flow_id is None:
        return True  # No flow = nothing to update

    flow_ptr = node.flows.get(flow_id)
    if flow_ptr is None:
        return False

    # Force compatibility
    compliant_caps, compliant_groups = force_flow_properties_compatibility(
        node, flow_ptr, active_cons, layer, "", reset, verbose,
    )

    if compliant_caps is None:
        return False

    # Write back to flow (including layer_compatibility_groups from winning conset)
    try:
        update_flow_to_compliant(node, flow_ptr, compliant_caps, compliant_groups, verbose=verbose)
    except Exception as exc:
        if verbose:
            print(f"  [update_sender_flow] Write-back FAILED: {exc}")
        return False

    # NOTE: UUID cascade (Atomic State Changes) is NOT done here. Forced
    # properties are written IN PLACE (Phase 1); the cascade is deferred to
    # force_active_constraints() AFTER all mutations (trunk + sub-flows) are
    # complete, to avoid stale ID references during the mux sub-flow forcing
    # loop. This two-phase split — a deferred cascade rather than one
    # intrinsic to each per-write — has error-recovery implications; see
    # nmos/node/ATOMIC_STATE_CHANGES.md.
    #
    # NOTE: receiver constraint propagation (updateReceiverConstraintsToFlowProperties)
    # is NOT triggered here. It is invoked at the end of update_flow_to_compliant()so
    # that both the trunk flow and every mux sub-flow propagate.

    # Update sender's optional format attributes via AddSenderOptionalFormatAttributes
    if hasattr(node, '_add_sender_optional_format_attributes'):
        node._add_sender_optional_format_attributes(sender)

    return True


# ---------------------------------------------------------------------------
# Generic property filtering (getGenericProperties)
# ---------------------------------------------------------------------------

# Generic property filtering is provided by the public get_generic_properties()
# above (port of getGenericProperties), keyed by exact format URN and including
# the mux VideoLayers/AudioLayers/DataLayers branch. It operates on a
# ``{cap_name -> Cap}`` map; callers pass ``compliant_caps.caps``.


def _nconstraint_to_range(nconstraint: Any) -> Any:
    """Convert a receiver ``NConstraint`` into a CCF ``RangeValue`` (capability).

    Mirrors the receiver-side constraint shapes ``isPropertyIn*Capability`` helpers.
    An unconstrained constraint (no ``Enum``/``Minimum``/``Maximum``) maps to an 
    infinite range, matching ``isConstraintUnconstrained`` accept-all behaviour.
    Returns ``None`` if the constraint cannot be interpreted.
    """
    from caps.MatroxCCF import RangeValue, RangeType
    from fractions import Fraction

    try:
        inner = nconstraint.value       # concrete NConstraint* wrapper
        leaf = inner.value              # *Value with Enum / Minimum / Maximum
    except Exception:
        return None

    cls = type(inner).__name__

    def _enum_list(field: Any) -> list[Any]:
        return list(field.value) if field.defined else []

    if cls == "NConstraintBool":
        enum = _enum_list(leaf.Enum)
        return RangeValue(values=tuple(enum), type=RangeType.BOOL) if enum \
            else RangeValue(type=RangeType.BOOL)

    if cls == "NConstraintString":
        enum = _enum_list(leaf.Enum)  # list[EnumId]
        return RangeValue(values=tuple(str(e) for e in enum), type=RangeType.STRING) \
            if enum else RangeValue(type=RangeType.STRING)

    if cls in ("NConstraintInt", "NConstraintFloat"):
        rtype = RangeType.INT if cls == "NConstraintInt" else RangeType.FLOAT
        enum = _enum_list(leaf.Enum)
        if enum:
            return RangeValue(values=tuple(enum), type=rtype)
        mn = leaf.Minimum.value if leaf.Minimum.defined else None
        mx = leaf.Maximum.value if leaf.Maximum.defined else None
        if mn is None and mx is None:
            return RangeValue(type=rtype)
        return RangeValue(min=mn, max=mx, type=rtype)

    if cls == "NConstraintRational":
        def _frac(nr: Any) -> Fraction:
            num = nr.Numerator.value if nr.Numerator.defined else 0
            den = nr.Denominator.value if nr.Denominator.defined else 1
            return Fraction(num, den)
        enum = _enum_list(leaf.Enum)  # list[NRationalValue]
        if enum:
            return RangeValue(values=tuple(_frac(r) for r in enum), type=RangeType.RATIONAL)
        mn = _frac(leaf.Minimum.value) if leaf.Minimum.defined else None
        mx = _frac(leaf.Maximum.value) if leaf.Maximum.defined else None
        if mn is None and mx is None:
            return RangeValue(type=RangeType.RATIONAL)
        return RangeValue(min=mn, max=mx, type=RangeType.RATIONAL)

    return None


def _constraint_set_items(constraint_sets_field: Any) -> list[Any]:
    """Return the list of constraint-set objects from an NArrayOfConstraintSet."""
    if constraint_sets_field is None or not constraint_sets_field.defined:
        return []
    cs_list = constraint_sets_field.value
    if not cs_list:
        return []
    items = cs_list._inner if hasattr(cs_list, '_inner') else cs_list
    return items or []


def _cs_meta_enabled(cs: Any) -> bool:
    """Enabled gate: enabled unless both MetaEnabled and MetaLayerEnabled are false."""
    enabled = cs.MetaEnabled.value if (hasattr(cs, 'MetaEnabled') and cs.MetaEnabled.defined) else True
    if enabled:
        return True
    return bool(cs.MetaLayerEnabled.value) if (hasattr(cs, 'MetaLayerEnabled') and cs.MetaLayerEnabled.defined) else False


def _cs_preference(cs: Any) -> int:
    return cs.MetaPreference.value if (hasattr(cs, 'MetaPreference') and cs.MetaPreference.defined) else 0


def _cs_layer_format_matches(cs: Any, source_layer: int, source_format: str) -> bool:
    """Layer/format gate (EXACT). For source_layer>=0 require MetaLayer==layer and
    MetaFormat==format; for source_layer<0 reject any set carrying a MetaLayer."""
    if source_layer >= 0:
        cs_layer = cs.MetaLayer.value if (hasattr(cs, 'MetaLayer') and cs.MetaLayer.defined) else None
        if cs_layer != source_layer:
            return False
        cs_format = str(cs.MetaFormat.value) if (hasattr(cs, 'MetaFormat') and cs.MetaFormat.defined) else ""
        return cs_format == source_format
    return not (hasattr(cs, 'MetaLayer') and cs.MetaLayer.defined)


def _check_receiver_native_properties_compatibility(
    generic_props: dict[str, Any],
    compliant_groups: list[int] | None,
    constraint_sets_field: Any,
    source_layer: int,
    source_format: str,
    verbose: bool = False,
) -> bool:
    """
    Validate the compliant generic properties against the receiver's NON-native
    (preference != 100) constraint sets. Returns True if at least one matching
    non-native set accepts every property; False otherwise.
    """
    from nmos.enums import EnumRegistry

    items = _constraint_set_items(constraint_sets_field)
    if not items:
        return False

    cg = set(compliant_groups or [])

    for cs in items:
        if not _cs_meta_enabled(cs):
            continue
        # We check the NON-native sets; native (==100) is produced, not tested.
        if _cs_preference(cs) == 100:
            continue
        if not _cs_layer_format_matches(cs, source_layer, source_format):
            continue
        # Group guard for PROPAGATION (distinct from mux sub-flow compliance).
        # Propagating compliant properties to a receiver's native constraints
        # while still honouring layer compatibility groups is only tractable when
        # the groups match exactly; partial overlap is intentionally not handled
        # (too complex). So we propagate into a non-native set only when its
        # compatibility groups EXACTLY equal the properties' compliant groups.
        #
        # Per the Matrox ReceiverCapabilities spec, "A Constraint Set without a
        # layer_compatibility_groups attribute MUST be assumed as being part of all
        # groups." An absent/empty group set therefore means ALL groups and acts as
        # a wildcard that satisfies the match on either side.
        if hasattr(cs, 'MetaLayerCompatibilityGroups') and cs.MetaLayerCompatibilityGroups.defined:
            cs_groups = set(cs.MetaLayerCompatibilityGroups.value)
            if cs_groups and cg and cg != cs_groups:
                continue

        constraint_dict = cs.Constraints.get() if hasattr(cs, 'Constraints') else {}
        if constraint_dict is None:
            constraint_dict = {}

        ok = True
        for prop_name, cap in generic_props.items():
            enum_id = EnumRegistry.get(prop_name) if isinstance(prop_name, str) else prop_name
            nconstraint = constraint_dict.get(enum_id)
            if nconstraint is None:
                # Property not constrained by this set → accept it.
                continue
            rng = _nconstraint_to_range(nconstraint)
            if rng is None:
                continue
            try:
                included = rng.includes_range(cap.value)
            except Exception:
                included = False
            if not included:
                ok = False
                break

        if ok:
            return True

    return False


def _compliant_value_to_json(cap: Any) -> Any:
    """Extract a single concrete value from a compliant CCF Cap and render it as
    the JSON scalar used by NConstraint enum decoding. Returns ``None`` if no
    value can be extracted."""
    from fractions import Fraction

    val = getattr(cap, 'value', None)
    if val is None:
        return None

    first_val = None
    if getattr(val, 'values', None):
        first_val = val.values[0]
    elif getattr(val, 'enumerated', None):
        first_val = next(iter(val.enumerated))
    elif getattr(val, 'min', None) is not None:
        first_val = val.min
    if first_val is None:
        return None

    # bool must precede int (bool is a subclass of int)
    if isinstance(first_val, bool):
        return first_val
    if isinstance(first_val, int):
        return first_val
    if isinstance(first_val, float):
        return first_val
    if isinstance(first_val, Fraction):
        return {"numerator": first_val.numerator, "denominator": first_val.denominator}
    if isinstance(first_val, str):
        return first_val
    if hasattr(first_val, 's'):  # EnumId
        return str(first_val)
    return None


def _update_receiver_native_properties_compatibility(
    generic_props: dict[str, Any],
    constraint_sets_field: Any,
    source_layer: int,
    source_format: str,
    verbose: bool = False,
) -> bool:
    """
    Find the native (preference==100) constraint set matching the source
    layer/format and overwrite each generic property with a single-value enum
    constraint (a proper NConstraint object — not a raw JSON dict). Returns True
    if a native set was updated, False otherwise (NOT_ALLOWED).
    """
    from nmos.enums import EnumRegistry
    from nmos.types.generated.nconstraint import NConstraint

    items = _constraint_set_items(constraint_sets_field)
    if not items:
        return False

    # byPreference (descending) — sorts before selecting the native set.
    items = sorted(items, key=_cs_preference, reverse=True)

    for cs in items:
        if not _cs_meta_enabled(cs):
            continue
        if _cs_preference(cs) != 100:
            continue
        if not _cs_layer_format_matches(cs, source_layer, source_format):
            continue

        if not hasattr(cs, 'Constraints'):
            continue
        constraint_dict = cs.Constraints.get()  # live dict[EnumId, NConstraint]
        if constraint_dict is None:
            cs.Constraints.set_to_default()
            constraint_dict = cs.Constraints.get()

        for prop_name, cap in generic_props.items():
            json_val = _compliant_value_to_json(cap)
            if json_val is None:
                continue
            enum_id = EnumRegistry.get(prop_name) if isinstance(prop_name, str) else prop_name
            constraint = NConstraint()
            constraint.decode_value({"enum": [json_val]})
            constraint_dict[enum_id] = constraint

        if verbose:
            print(f"  [update_native] Updated preference=100 CS with {len(generic_props)} properties")

        return True

    return False


def _update_receiver_constraints_to_flow_properties(
    node: Any,
    flow_ptr: Any,
    compliant_caps: Any,
    compliant_groups: list[int] | None,
    verbose: bool = False,
) -> None:
    """
    Propagate a sender's compliant flow properties to the native
    (preference=100) constraint set of the receiver linked to the flow's source.
    Recursive over parent (sub/derived) flows. Only generic, format-independent
    properties are propagated.
    """
    from nmos.node import (
        _get_flow_core, _get_source_core, _set_version_now, _nmos_version_now,
    )

    flow_core = _get_flow_core(flow_ptr)
    flow_inner = flow_ptr.get() if hasattr(flow_ptr, 'get') else flow_ptr
    fv = flow_inner.value if hasattr(flow_inner, 'value') else flow_inner
    flow_format = str(fv.Format.value) if hasattr(fv, 'Format') and fv.Format.defined else ""

    # Only propagate from video, audio and data Flows.
    if flow_format not in (FormatVideo.s, FormatAudio.s, FormatData.s):
        return

    caps_map = compliant_caps.caps if hasattr(compliant_caps, 'caps') else {}

    # --- Parents branch: update and recurse into parent flows ---
    if flow_core.Parents.defined and flow_core.Parents.value:
        from caps.MatroxCCF import CapSet
        for parent_id in flow_core.Parents.value:
            parent_ptr = node.flows.get(parent_id)
            if parent_ptr is None:
                raise UnexpectedError(f"missing parent flow {parent_id}")

            # DEFECT 1 corrected: derive all state from the PARENT flow.
            parent_core = _get_flow_core(parent_ptr)
            parent_inner = parent_ptr.get() if hasattr(parent_ptr, 'get') else parent_ptr
            parent_fv = parent_inner.value if hasattr(parent_inner, 'value') else parent_inner

            # Skip static parent flows.
            if parent_core.Static.defined and parent_core.Static.value:
                continue

            parent_format = str(parent_fv.Format.value) if hasattr(parent_fv, 'Format') and parent_fv.Format.defined else ""
            parent_media_type = str(parent_fv.MediaType.value) if hasattr(parent_fv, 'MediaType') and parent_fv.MediaType.defined else ""
            parent_class = get_class_from_media_type(parent_media_type)

            parent_generic = get_generic_properties(parent_format, caps_map)
            parent_caps = CapSet(caps=dict(parent_generic))

            if parent_format == FormatVideo.s:
                if parent_class != "raw":
                    raise UnexpectedError("unexpected parent coded video Flow")
                update_raw_video_flow(node, parent_ptr, parent_caps, compliant_groups, verbose)
            elif parent_format == FormatAudio.s:
                if parent_class != "raw":
                    raise UnexpectedError("unexpected parent coded audio Flow")
                update_raw_audio_flow(node, parent_ptr, parent_caps, compliant_groups, verbose)
            elif parent_format == FormatData.s:
                update_data_flow(node, parent_ptr, parent_caps, compliant_groups, verbose)
            else:
                raise UnexpectedError(f"unexpected parent format {parent_format}")

            _update_receiver_constraints_to_flow_properties(
                node, parent_ptr, parent_caps, compliant_groups, verbose,
            )
        return

    # --- Leaf branch: propagate to the linked receiver ---
    if not flow_core.SourceId.defined or flow_core.SourceId.value is None:
        raise UnexpectedError("a Flow without parent Flows must reference a Source")
    source_id = flow_core.SourceId.value
    source_ptr = node.sources.get(source_id)
    if source_ptr is None:
        raise UnexpectedError(f"missing source {source_id}")

    source_core = _get_source_core(source_ptr)
    source_inner = source_ptr.get() if hasattr(source_ptr, 'get') else source_ptr
    sv = source_inner.value if hasattr(source_inner, 'value') else source_inner
    source_format = str(sv.Format.value) if hasattr(sv, 'Format') and sv.Format.defined else ""
    if source_format != flow_format:
        raise UnexpectedError("a Flow without parent Flows must reference a Source of the same format")

    source_layer = source_core.Layer.value if source_core.Layer.defined else -1

    if not source_core.ReceiverId.defined or source_core.ReceiverId.value is None:
        return  # No linked receiver — nothing to propagate.
    receiver_id = source_core.ReceiverId.value
    receiver_ptr = node.receivers.get(receiver_id)
    if receiver_ptr is None:
        return  # Linked receiver not present here — skip.

    # The store holds an NReceiverValue whose get() yields the concrete *Value
    # (e.g. NReceiverMuxValue), which exposes ReceiverCore/Caps directly.
    from nmos.types.generated.nreceiver_video import NReceiverVideoValue
    from nmos.types.generated.nreceiver_audio import NReceiverAudioValue
    from nmos.types.generated.nreceiver_data import NReceiverDataValue
    from nmos.types.generated.nreceiver_mux import NReceiverMuxValue

    poly = receiver_ptr.get() if hasattr(receiver_ptr, 'get') else receiver_ptr
    rv = poly.value if hasattr(poly, 'value') else poly
    if isinstance(rv, NReceiverMuxValue):
        mux_receiver = True
    elif isinstance(rv, (NReceiverVideoValue, NReceiverAudioValue, NReceiverDataValue)):
        mux_receiver = False
    else:
        raise UnexpectedError("invalid receiver type")
    receiver_core = getattr(rv, 'ReceiverCore', rv)

    # Skip static receivers.
    if hasattr(receiver_core, 'Static') and receiver_core.Static.defined and receiver_core.Static.value:
        return

    # Source/receiver layer invariant.
    if (mux_receiver and source_layer < 0) or (not mux_receiver and source_layer >= 0):
        raise UnexpectedError("source layer is invalid for the linked receiver")

    if not (hasattr(rv, 'Caps') and rv.Caps.defined):
        return
    caps_val = rv.Caps.value
    if not (hasattr(caps_val, 'ConstraintSets') and caps_val.ConstraintSets.defined):
        return

    # Only generic properties can be propagated (transport ones depend on format).
    generic_props = get_generic_properties(flow_format, caps_map)

    # Validate against the receiver's NON-native constraint sets first.
    if not _check_receiver_native_properties_compatibility(
        generic_props, compliant_groups, caps_val.ConstraintSets,
        source_layer, source_format, verbose,
    ):
        if verbose:
            print(f"  [propagate_to_receiver] {receiver_id}: generic props not compatible with non-native sets — skipped")
        return

    # Here the native update is all-or-nothing: it returns False *before* mutating
    # anything when no native set matches the source layer/format, and otherwise 
    # updates exactly one set in place and returns True. So no clone/restore is
    # needed on the False path.
    updated = _update_receiver_native_properties_compatibility(
        generic_props, caps_val.ConstraintSets, source_layer, source_format, verbose,
    )
    if not updated:
        return

    # Success: bump caps version, then receiver resource version.
    if hasattr(caps_val, 'Version'):
        caps_val.Version.value = _nmos_version_now()
    _set_version_now(receiver_core.ResourceCore)

    if verbose:
        print(f"  [propagate_to_receiver] {receiver_id}: {len(generic_props)} generic properties propagated")


def intersect_constraints_with_caps(
    sender_caps: Any,
    receiver_constraints: Any,
    verbose: bool = False,
) -> Any:
    """Intersect receiver constraints with sender capabilities.

    Uses CCF constriction with adjustment: sender_caps <& receiver_constraints.
    This computes the overlap (intersection) between what the sender supports
    and what the receiver requires.

    Args:
        sender_caps: CCF Caps of sender capabilities.
        receiver_constraints: CCF Caps of receiver constraints (converted to Cons).
        verbose: Print CCF state for debugging.

    Returns:
        CCF Caps with the intersected result, or None if no intersection exists.
    """
    try:
        from caps.MatroxCCF import caps_constrict_adjust_by_cons, Caps, Cons
    except ImportError:
        return None

    if sender_caps is None or receiver_constraints is None:
        return sender_caps

    # Sort sender capsets by preference DESC — intersectConstraintsWithCapabilities
    # sorts inside the function. In Python the sort is externalized
    # to each caller; Caps.get(no_filter=True) delegates to the CCF API.
    sender_caps = sender_caps.get(no_filter=True)

    # receiver_constraints may be Caps (receiver capabilities) or Cons (already converted).
    # CCF constriction requires Cons, so convert Caps if needed.
    cons = receiver_constraints if isinstance(receiver_constraints, Cons) else receiver_constraints.to_cons()

    if verbose:
        print(f"  [intersect] Sender: {len(sender_caps.capsets)} capsets")
        print(f"  [intersect] Receiver: {len(cons.consets)} consets")

    try:
        result = caps_constrict_adjust_by_cons(sender_caps, cons)
        if verbose:
            print(f"  [intersect] Result: {len(result.capsets)} capsets")
        return result
    except (ValueError, Exception) as exc:
        if verbose:
            print(f"  [intersect] EMPTY (no intersection): {exc}")
        return None


def set_sender_compatibility_state(
    node: Any,
    sender_id: str,
    verbose: bool = False,
) -> str:
    """Check sender flow compatibility and set IS-11 status.

    Returns the compatibility status string:
    - "unconstrained": no active constraints
    - "constrained": flow is compatible with constraints
    - "active_constraints_violation": flow violates constraints
    """
    status = check_sender_flow_compatibility(node, sender_id, verbose=verbose)

    # Map to IS-11 status
    if status == "compatible":
        result = Constrained.s
    elif status == "incompatible":
        result = ActiveConstraintsViolation.s
    else:
        result = Unconstrained.s

    if verbose:
        print(f"  [set_sender_state] {sender_id} → {result}")

    return result


def check_stream_compatibility(
    node: Any,
    receiver_id: str,
    verbose: bool = False,
) -> str:
    """Check if the current stream (SDP) is compatible with receiver.

    Returns:
        "compliant": stream is within receiver caps
        "non_compliant": stream violates receiver caps
        "unknown": no stream/SDP to check
    """
    # Determine if receiver is mux via the NReceiverMux type predicate.
    # This affects AM824/MP2T media_type prefix in SDP property extraction.
    receiver = node.receivers.get(receiver_id)
    is_mux = False
    if receiver is not None:
        from nmos.types.generated.nreceiver_mux import NReceiverMux
        poly = receiver.get() if hasattr(receiver, 'get') else receiver
        is_mux = isinstance(poly, NReceiverMux)

    # Get SDP properties as CapSet
    stream_caps = get_sdp_to_caps(node, receiver_id, mux=is_mux, verbose=verbose)
    if stream_caps is None:
        return Unknown.s

    compatible = check_receiver_compatibility(
        node, receiver_id, stream_caps, verbose=verbose,
    )

    return "compliant" if compatible else "non_compliant"


def set_receiver_compatibility_state(
    node: Any,
    receiver_id: str,
    stream_caps: Any = None,
    verbose: bool = False,
) -> str:
    """Check receiver stream compatibility and set IS-11 status.

    Returns:
    - "compliant_stream": stream is compatible
    - "non_compliant_stream": stream is not compatible
    - "unknown": no stream to check
    """
    if stream_caps is None:
        return Unknown.s

    compatible = check_receiver_compatibility(
        node, receiver_id, stream_caps, verbose=verbose,
    )

    result = CompliantStream.s if compatible else NonCompliantStream.s

    if verbose:
        print(f"  [set_receiver_state] {receiver_id} → {result}")

    return result
