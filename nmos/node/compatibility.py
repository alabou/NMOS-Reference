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

Compliance audit (2026-04-10):
  Audited: getFlowProperties, getSdpProperties, getGenericProperties,
    checkFlowPropertiesCompatibility, checkReceiverCompatibility,
    validateActiveConstraints, forceFlowPropertiesCompatibility,
    forceActiveConstraints, updateFlowToCompliantFlow,
    updateSenderToCompliantFlow, checkSenderFlowCompatibility,
    checkStreamCompatibility, updateReceiverNativePropertiesCompatibility.

  NOT YET PORTED (deferred until receiver→sender pipelines):
    - checkReceiverNativePropertiesCompatibility
        Checks receiver native properties against non-native constraint sets.
        Only called from updateReceiverConstraintsToFlowProperties.
    - updateReceiverConstraintsToFlowProperties
        Propagates compliant flow properties to connected receiver's constraint
        sets. Recursive for mux. Called from updateFlowToCompliantFlow.
    - update_receiver_native_properties is PARTIAL: missing layer/format
        parameters needed for mux sub-flow receiver constraint updates.
    - update_flow_to_compliant is PARTIAL: missing the
        updateReceiverConstraintsToFlowProperties call.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from caps.MatroxCCF import Cons

from nmos.enums import (
    EnumRegistry,
    # Formats
    FormatVideo, FormatAudio, FormatData, FormatMux,
    # Capabilities — format
    CapFormatMediaType, CapFormatEventType, CapFormatGrainRate,
    CapFormatFrameWidth, CapFormatFrameHeight, CapFormatInterlaceMode,
    CapFormatColorspace, CapFormatTransferCharacteristic, CapFormatColorSampling,
    CapFormatComponentDepth, CapFormatChannelCount, CapFormatSampleRate,
    CapFormatSampleDepth, CapFormatBitRate, CapFormatConstantBitRate,
    CapFormatProfile, CapFormatLevel, CapFormatSublevel,
    CapFormatVideoLayers, CapFormatAudioLayers, CapFormatDataLayers,
    # Capabilities — transport
    CapTransportBitRate, CapTransportPacketTime, CapTransportMaxPacketTtime,
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
    CapTransportSynchronousMedia.s, CapTransportClockRefType.s,
]

SUPPORTED_AUDIO_CONSTRAINTS: list[str] = _META_CONSTRAINTS + [
    CapFormatMediaType.s,
    CapFormatChannelCount.s, CapFormatSampleRate.s, CapFormatSampleDepth.s,
    CapFormatBitRate.s, CapFormatConstantBitRate.s,
    CapFormatProfile.s, CapFormatLevel.s,
    CapTransportSynchronousMedia.s, CapTransportClockRefType.s,
]

SUPPORTED_DATA_CONSTRAINTS: list[str] = _META_CONSTRAINTS + [
    CapFormatMediaType.s,
    CapTransportSynchronousMedia.s, CapTransportClockRefType.s,
    CapTransportUsbClass.s,
]

SUPPORTED_DATA_EVENT_CONSTRAINTS: list[str] = [
    CapMetaEnabled.s, CapMetaLabel.s, CapMetaPreference.s,
    CapMetaLayerEnabled.s, CapMetaLayer.s, CapMetaFormat.s,
    CapMetaLayerCompatibilityGroups.s,
    CapFormatMediaType.s, CapFormatEventType.s,
]

SUPPORTED_MUX_CONSTRAINTS: list[str] = [
    CapMetaEnabled.s, CapMetaLabel.s, CapMetaPreference.s,
    CapMetaLayerCompatibilityGroups.s, CapMetaInfoBlock.s,
    CapFormatMediaType.s,
    CapFormatVideoLayers.s, CapFormatAudioLayers.s, CapFormatDataLayers.s,
    CapTransportSynchronousMedia.s, CapTransportClockRefType.s,
]

# Mux mixed = mux trunk + all sub-flow constraints (supportedMuxMixedConstraints)
SUPPORTED_MUX_MIXED_CONSTRAINTS: list[str] = [
    CapMetaEnabled.s, CapMetaLabel.s, CapMetaPreference.s,
    CapMetaLayerEnabled.s, CapMetaLayer.s, CapMetaFormat.s,
    CapMetaLayerCompatibilityGroups.s, CapMetaInfoBlock.s,
    CapFormatMediaType.s,
    CapFormatVideoLayers.s, CapFormatAudioLayers.s, CapFormatDataLayers.s,
    CapTransportSynchronousMedia.s, CapTransportClockRefType.s,
    # Video sub-constraints
    CapFormatGrainRate.s, CapFormatFrameWidth.s, CapFormatFrameHeight.s,
    CapFormatInterlaceMode.s, CapFormatColorspace.s,
    CapFormatTransferCharacteristic.s, CapFormatColorSampling.s,
    CapFormatComponentDepth.s,
    CapFormatBitRate.s, CapFormatConstantBitRate.s,
    CapFormatProfile.s, CapFormatLevel.s, CapFormatSublevel.s,
    # Audio sub-constraints
    CapFormatChannelCount.s, CapFormatSampleRate.s, CapFormatSampleDepth.s,
]

# Transport constraint URNs (isConstraintNameOfTransportCategory)
_TRANSPORT_CONSTRAINTS: set[str] = {
    CapTransportBitRate.s, CapTransportPacketTime.s,
    CapTransportMaxPacketTtime.s, CapTransportSenderType.s,
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
        "data/USB"   → FormatData.s
    """
    # application/AM824, application/MP2T, etc. are mux formats.
    # NOTE: video/MP2T is OPAQUE (not supported in this implementation).
    # There is no MuxVideoMp2t enum — video/MP2T falls to FormatVideo.
    _MUX_MEDIA_TYPES = {
        "application/am824", "application/mp2t",
        "application/ndi", "application/rtsp", "application/generic",
    }
    mt = media_type.lower() if media_type else ""
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
    # application/AM824, application/MP2T, etc. are mux class.
    # NOTE: video/MP2T is OPAQUE (not supported) — not in this set.
    _MUX_CLASS = {
        "application/am824", "application/mp2t",
        "application/ndi", "application/rtsp", "application/generic",
    }
    mt = media_type.lower() if media_type else ""

    # Mux (check before video/ prefix since application/* types need priority)
    if mt in _MUX_CLASS:
        return "mux"

    # Video
    if mt == "video/raw":
        return "raw"
    elif mt.startswith("video/"):
        return "coded"

    # Audio — audio/AM824 is ClassAudioCoded (not raw)
    if mt in ("audio/l8", "audio/l16", "audio/l20", "audio/l24"):
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
    _PCM_TYPES = {"audio/L8", "audio/L16", "audio/L20", "audio/L24"}
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

    _DEPTH_TO_MT = {8: "audio/L8", 16: "audio/L16", 20: "audio/L20", 24: "audio/L24"}
    _MT_TO_DEPTH = {"audio/L8": 8, "audio/L16": 16, "audio/L20": 20, "audio/L24": 24}

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
        return "unconstrained"

    from nmos.node.flow_caps import get_flow_to_caps

    # Get sender
    sender = node.senders.get(sender_id)
    if sender is None:
        return "unconstrained"

    # Get flow
    flow_id = sender.FlowId.value if sender.FlowId.defined and sender.FlowId.value else None
    if flow_id is None:
        return "unconstrained"

    flow_ptr = node.flows.get(flow_id)
    if flow_ptr is None:
        return "unconstrained"

    # Get flow caps (via get_flow_to_caps)
    flow_caps = get_flow_to_caps(node, flow_ptr)

    # Check against NormalizedConstraints (IS-11 active constraints).
    # When no active constraints → NormalizedConstraints is empty → "unconstrained".
    # When active constraints → checks flow against them.
    sender_cons = _get_sender_normalized_ccf_cons(node, sender)
    if sender_cons is None or len(sender_cons.consets) == 0:
        return "unconstrained"

    if verbose:
        print(f"  [check_sender_flow] sender={sender_id}")

    # Check main flow compatibility
    compatible = check_flow_properties_compatibility(
        node, flow_caps, sender_cons, verbose=verbose,
    )

    if not compatible:
        # Attempt to fix the flow, then recheck
        fix_ok = update_sender_to_compliant_flow(
            node, sender_id, sender_cons, layer=-1, reset=False, verbose=verbose,
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
            # First force compliance (Cons → CapSet), then write back to flow
            compliant, compliant_groups = force_flow_properties_compatibility(
                node, parent_ptr, sender_cons,
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

    Returns None if no active constraints (unconstrained state).
    The check runs against NormalizedConstraints — these are Cons, not Caps.
    """
    static_id = _get_sender_static_id(sender)
    return node.sender_ccf_normalized.get(static_id)


def _get_sender_ccf_caps(node: Any, sender: Any) -> Any:
    """Get sender's IS-04 capabilities as cached CCF Caps.

    Stored at pipeline build time — no conversion needed.
    """
    static_id = _get_sender_static_id(sender)
    return node.sender_ccf_caps.get(static_id)


def force_active_constraints(
    node: Any,
    sender_id: str,
    active_cons: Cons,
    verbose: bool = False,
) -> Any:
    """Apply active constraints to narrow sender capabilities.

    Uses CCF constriction: sender_caps << active_constraints.

    Args:
        node: Node instance.
        sender_id: Sender resource ID.
        active_cons: CCF Caps of active constraints.
        verbose: Print CCF state for debugging.

    Returns:
        Constricted CCF Caps, or None on failure.
    """
    try:
        from caps.MatroxCCF import caps_constrict_by_cons, Caps
    except ImportError:
        return None

    sender = node.senders.get(sender_id)
    if sender is None:
        return None

    sender_caps = _get_sender_ccf_caps(node, sender)
    if sender_caps is None:
        return None

    # active_cons is already Cons. Filter out empty consets (unconstrained layers
    # from normalize()) — they should NOT participate in constriction.
    if active_cons is None or len(active_cons.consets) == 0:
        return sender_caps  # No constraints = unconstrained

    from caps.MatroxCCF import Cons as _Cons, ConSet as _ConSet
    non_empty = _Cons(consets=[
        _ConSet(cons=dict(cs.cons), preference=cs.preference, label=cs.label,
                format=cs.format, layer=cs.layer,
                layer_compatibility_groups=cs.layer_compatibility_groups)
        for cs in active_cons.consets if len(cs.cons) > 0
    ])
    if len(non_empty.consets) == 0:
        return sender_caps  # All consets empty = unconstrained

    if verbose:
        print(f"  [force_active_constraints] Sender caps: {len(sender_caps.capsets)} capsets")
        print(f"  [force_active_constraints] Constraints: {len(non_empty.consets)} consets")

    try:
        result = caps_constrict_by_cons(sender_caps, non_empty)
        if verbose:
            print(f"  [force_active_constraints] Constricted: {len(result.capsets)} capsets")
        return result
    except ValueError as exc:
        if verbose:
            print(f"  [force_active_constraints] FAILED: {exc}")
        return None


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
        _AM824_SYMBOLS = ["L", "R", "L", "R", "C", "LFE", "Ls", "Rs", "Lrs", "Rrs"]
        symbols = _AM824_SYMBOLS
    else:
        # GetAudioChannels — standard SMPTE ordering
        _SYMBOLS = ["L", "R", "C", "LFE", "Ls", "Rs", "Lrs", "Rrs"]
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
    if clk_ref == "ptp":
        clk_name = "clk0"
    elif clk_ref == "internal":
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
    if clk_ref == "ptp":
        clk_name = "clk0"
    elif clk_ref == "internal":
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
    _DEPTH_TO_MT = {8: "audio/L8", 16: "audio/L16", 20: "audio/L20", 24: "audio/L24"}
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

    sync_media = _get_cap_bool(compliant_caps, CapTransportSynchronousMedia.s)
    clk_ref = _get_cap_str(compliant_caps, CapTransportClockRefType.s)
    clk_name = ""
    if clk_ref == "ptp":
        clk_name = "clk0"
    elif clk_ref == "internal":
        clk_name = "clk1"

    if frame_width is None or frame_height is None or media_type is None:
        raise NotAllowed("missing video properties in constricted caps")

    if verbose:
        print(f"    [update_coded_video] {media_type} {frame_width}x{frame_height} "
              f"profile={profile} level={level} bitrate={bit_rate}")

    # Build components
    from nmos.enums import Y, Cb, Cr
    bit_depth = depth if depth else 8
    if sampling and "4:4:4" in sampling:
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
    if clk_ref == "ptp":
        clk_name = "clk0"
    elif clk_ref == "internal":
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
    if clk_ref == "ptp":
        clk_name = "clk0"
    elif clk_ref == "internal":
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
    if clk_ref == "ptp":
        clk_name = "clk0"
    elif clk_ref == "internal":
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

    if media_type not in ("video/H264", "video/H265", "video/jxsv"):
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

    # Helper: check if a string value is within a constraint's range
    def _value_in_constraint(value: str, constraint_name: str) -> bool:
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

    if media_type == "video/jxsv":
        _PROFILE_TO_SAMPLING: dict[str, list[str]] = {
            "Main420.12": ["YCbCr-4:2:0"],
            "High420.12": ["YCbCr-4:2:0"],
            "Main444.12": ["YCbCr-4:4:4", "YCbCr-4:2:2", "YCbCr-4:2:0"],
            "High444.12": ["YCbCr-4:4:4", "YCbCr-4:2:2", "YCbCr-4:2:0"],
        }
        _SAMPLING_TO_PROFILE: dict[str, list[str]] = {
            "YCbCr-4:2:0": ["High420.12", "Main420.12"],
            "YCbCr-4:2:2": ["High444.12", "Main444.12"],
            "YCbCr-4:4:4": ["High444.12", "Main444.12"],
        }
        try_levels = ["4k-1", "4k-2", "4k-3"]

        from nmos.node.codec import get_jxsv_max_bitrate, check_jxsv_profile_level

    elif media_type == "video/H264":
        _PROFILE_TO_SAMPLING = {
            "High-422": ["YCbCr-4:2:2", "YCbCr-4:2:0"],
            "HighIntra-422": ["YCbCr-4:2:2", "YCbCr-4:2:0"],
            "High10": ["YCbCr-4:2:0"],
            "High10Intra": ["YCbCr-4:2:0"],
        }
        _SAMPLING_TO_PROFILE = {
            "YCbCr-4:2:2": ["High-422", "HighIntra-422"],
            "YCbCr-4:2:0": ["High-422", "HighIntra-422", "High10", "High10Intra"],
        }
        try_levels = ["3", "3.1", "3.2", "4", "4.1", "4.2",
                      "5", "5.1", "5.2", "6", "6.1", "6.2"]

        from nmos.node.codec import get_h264_max_bitrate, check_h264_profile_level

    elif media_type == "video/H265":
        _PROFILE_TO_SAMPLING = {
            "Main10-422": ["YCbCr-4:2:2", "YCbCr-4:2:0"],
            "Main10Intra-422": ["YCbCr-4:2:2", "YCbCr-4:2:0"],
            "Main10": ["YCbCr-4:2:0"],
            "Main10Intra": ["YCbCr-4:2:0"],
            "Main10-444": ["YCbCr-4:4:4", "YCbCr-4:2:2", "YCbCr-4:2:0"],
            "Main10Intra-444": ["YCbCr-4:4:4", "YCbCr-4:2:2", "YCbCr-4:2:0"],
        }
        _SAMPLING_TO_PROFILE = {
            "YCbCr-4:2:0": ["Main10", "Main10Intra"],
            "YCbCr-4:2:2": ["Main10-422", "Main10Intra-422"],
            "YCbCr-4:4:4": ["Main10-444", "Main10Intra-444"],
        }
        try_levels = [
            "Main-3", "Main-3.1", "Main-4", "High-4", "Main-4.1", "High-4.1",
            "Main-5", "High-5", "Main-5.1", "High-5.1", "Main-5.2", "High-5.2",
            "Main-6", "High-6", "Main-6.1", "High-6.1", "Main-6.2", "High-6.2",
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

    # --- Level selection ---
    # Try resolutions from current down to smaller standard sizes
    try_widths = [frame_width, 3840, 1920, 1280, 720]
    try_heights = [frame_height, 2160, 1080, 720, 480]

    # Build components for codec check
    from nmos.enums import Y, Cb, Cr, Progressive
    from nmos.types.generated.nvideo_component import NVideoComponentValue
    from nmos.types.generated.nrational import NRationalValue

    depth = _get_int(CapFormatComponentDepth.s) or 10
    colorspace_e = EnumRegistry.get(_get_str(CapFormatColorspace.s) or "BT709")
    transfer_e = EnumRegistry.get(_get_str(CapFormatTransferCharacteristic.s) or "SDR")
    interlace_e = Progressive
    if not profile:
        return  # Cannot fix coded flow without a profile
    profile_e = EnumRegistry.get(profile)
    sublevel_e = EnumRegistry.get(sublevel) if sublevel else None

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

        # Try current level first — preserves the flow's level if it's valid.
        # This prevents the fix-up from downgrading a valid level to the minimum.
        ordered_levels = list(try_levels)
        if level and level in ordered_levels:
            ordered_levels.remove(level)
            ordered_levels.insert(0, level)

        for try_level in ordered_levels:
            if not _value_in_constraint(try_level, CapFormatLevel.s):
                continue

            level_e = EnumRegistry.get(try_level)
            try_bitrate = bit_rate or 0

            # If bitrate not user-constrained, compute max for this level
            if not original_bitrate:
                try:
                    if media_type == "video/jxsv" and sublevel_e:
                        try_bitrate = get_jxsv_max_bitrate(
                            tw, th, colorspace_e, transfer_e, interlace_e,
                            comps, gr_val, profile_e, level_e, sublevel_e)
                    elif media_type == "video/H264":
                        try_bitrate = get_h264_max_bitrate(
                            tw, th, colorspace_e, transfer_e, interlace_e,
                            comps, gr_val, profile_e, level_e)
                    elif media_type == "video/H265":
                        try_bitrate = get_h265_max_bitrate(
                            tw, th, colorspace_e, transfer_e, interlace_e,
                            comps, gr_val, profile_e, level_e)
                except Exception:
                    continue

            # Validate the complete configuration
            try:
                if media_type == "video/jxsv" and sublevel_e:
                    check_jxsv_profile_level(
                        tw, th, colorspace_e, transfer_e, interlace_e,
                        comps, gr_val, profile_e, level_e, sublevel_e, try_bitrate)
                elif media_type == "video/H264":
                    check_h264_profile_level(
                        tw, th, colorspace_e, transfer_e, interlace_e,
                        comps, gr_val, profile_e, level_e, try_bitrate)
                elif media_type == "video/H265":
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
        new_inner.Format.value = EnumRegistry.get("urn:x-nmos:format:video")
        new_inner.MediaType.value = EnumRegistry.get(mt)
    elif target_class == "raw" and "audio" in mt:
        new_inner = NFlowAudioRawValue()
        new_inner.set_to_default()
        new_inner.Format.value = EnumRegistry.get("urn:x-nmos:format:audio")
        new_inner.MediaType.value = EnumRegistry.get(mt)
    elif target_class == "coded" and "video" in mt:
        new_inner = NFlowVideoCodedValue()
        new_inner.set_to_default()
        new_inner.Format.value = EnumRegistry.get("urn:x-nmos:format:video")
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

    Handles raw↔coded class transitions by detecting if the compliant media_type
    class differs from the current flow class.

    PARTIAL: Missing updateReceiverConstraintsToFlowProperties call which
    propagates compliant flow properties to connected receiver constraints.
    TODO: Add when receiver→sender pipelines are implemented.
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

    # --- Gate 1: media_types check ---
    # Layers are not subject to media_types constraints — trunk only.
    receiver_media_types = _get_receiver_media_types(receiver)
    if receiver_media_types:
        stream_mt = stream_caps.caps.get(CapFormatMediaType)
        if stream_mt is not None and stream_mt.value.values:
            mt_str = str(stream_mt.value.values[0])
            if mt_str not in receiver_media_types:
                if verbose:
                    print(f"  [check_receiver] REJECTED by media_types: {mt_str} not in {receiver_media_types}")
                return False

    # --- Gate 2: event_types check ---
    receiver_event_types = _get_receiver_event_types(receiver)
    if receiver_event_types:
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
        return True  # No constraint_sets = accepts anything

    if verbose:
        print(f"  [check_receiver] Receiver caps: {len(receiver_caps.capsets)} capsets")

    stream_conset = stream_caps.to_conset()
    is_included: bool = conset_included_in_caps(stream_conset, receiver_caps)

    if verbose:
        print(f"  [check_receiver] Result: {'COMPATIBLE' if is_included else 'INCOMPATIBLE'}")

    return is_included


def _get_receiver_media_types(receiver: Any) -> list[str]:
    """Extract media_types from receiver's IS-04 capabilities.

    Returns a list of media type strings, or empty list if undefined.
    Reads caps.MediaTypes.GetValue().
    """
    try:
        poly = receiver.get() if hasattr(receiver, 'get') else receiver
        rv = poly.value if hasattr(poly, 'value') else poly
        core = getattr(rv, 'ReceiverCore', rv)
        caps = core.Caps
        if not caps.defined:
            return []
        cv = caps.value
        if not hasattr(cv, 'MediaTypes') or not cv.MediaTypes.defined:
            return []
        return [str(mt) for mt in cv.MediaTypes.value]
    except Exception:
        return []


def _get_receiver_event_types(receiver: Any) -> list[str]:
    """Extract event_types from receiver's IS-04 capabilities.

    Returns a list of event type strings, or empty list if undefined.
    Reads caps.EventTypes.GetValue(). Only data receivers have event_types.
    """
    try:
        poly = receiver.get() if hasattr(receiver, 'get') else receiver
        rv = poly.value if hasattr(poly, 'value') else poly
        core = getattr(rv, 'ReceiverCore', rv)
        caps = core.Caps
        if not caps.defined:
            return []
        cv = caps.value
        if not hasattr(cv, 'EventTypes') or not cv.EventTypes.defined:
            return []
        return [str(et) for et in cv.EventTypes.value]
    except Exception:
        return []


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
            CapTransportMaxPacketTime,
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

    # --- Helper: colorspace from SDP colorimetry + color_range ---
    def _colorspace_from_sdp(colorimetry: Any, color_range: Any) -> str | None:
        """Map SDP colorimetry to NMOS colorspace (getColorspaceFromSdp)."""
        if color_range is not None and str(color_range).lower() == "full":
            return "UNSPECIFIED"
        c = str(colorimetry).upper() if colorimetry else ""
        _MAP = {
            "BT601": "BT601", "BT709": "BT709", "BT2020": "BT2020",
            "BT2100": "BT2100", "BT601-5": "BT601_5", "BT709-2": "BT709_2",
            "ST2065-1": "ST2065_1", "ST2065-3": "ST2065_3", "XYZ": "XYZ",
        }
        return _MAP.get(c, "UNSPECIFIED")

    def _transfer_from_sdp(transfer: Any) -> str | None:
        """Map SDP transfer characteristic to NMOS (getTransferCharacteristicFromSdp)."""
        t = str(transfer).upper() if transfer else ""
        _MAP = {
            "SDR": "SDR", "HLG": "HLG", "PQ": "PQ", "LINEAR": "LINEAR",
            "BT2100LINPQ": "BT2100LINPQ", "BT2100LINHLG": "BT2100LINHLG",
            "ST2065-1": "ST2065_1", "ST428-1": "ST428_1", "DENSITY": "DENSITY",
            "ST2115LOGS3": "ST2115LOGS3",
        }
        return _MAP.get(t, "UNSPECIFIED")

    # --- Common video property extraction ---
    def _extract_video_common() -> None:
        if media.width:
            _i(CapFormatFrameWidth.s, media.width)
        if media.height:
            _i(CapFormatFrameHeight.s, media.height)
        if media.colorimetry is not None and media.color_range is not None:
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
        if not media.interlaced:
            _s(CapFormatInterlaceMode.s, "progressive")
        else:
            _s(CapFormatInterlaceMode.s, "interlace_tff" if media.top_field_first else "interlace_bff")

    # --- Common audio transport ---
    def _extract_audio_transport() -> None:
        if media.bitrate_kbits:
            _i(CapTransportBitRate.s, media.bitrate_kbits)
        ptime = getattr(media, 'ptime_us', 0) or getattr(media, 'ptime', 0)
        if ptime:
            _i(CapTransportPacketTime.s, ptime)
        max_ptime = getattr(media, 'max_ptime_us', 0) or getattr(media, 'max_ptime', 0)
        if max_ptime:
            _i(CapTransportMaxPacketTime.s, max_ptime)

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

    # --- Dispatch by media type ---
    media_type_enum = getattr(media, 'media_type', None)
    encoding = media.encoding_name

    if media_type_enum is not None and str(media_type_enum) == "video":
        # VIDEO
        enc_str = str(encoding).lower() if encoding else ""

        if enc_str == "raw":
            if not _check(check_sdp_rfc4175, check_sdp_st2110_10, check_sdp_st2110_21, check_sdp_st2110_20):
                return None
            _s(CapFormatMediaType.s, "video/raw")
            _extract_video_common()

        elif enc_str == "jxsv":
            if not _check(check_sdp_rfc9134, check_sdp_st2110_10, check_sdp_st2110_21, check_sdp_st2110_22):
                return None
            _s(CapFormatMediaType.s, "video/jxsv")
            _extract_video_common()
            if media.profile is not None:
                _s(CapFormatProfile.s, str(media.profile))
            if media.level is not None:
                _s(CapFormatLevel.s, str(media.level))
            if media.sub_level is not None:
                _s(CapFormatSublevel.s, str(media.sub_level))
            # Packet mode
            if media.jxsv_packet_mode is not None and str(media.jxsv_packet_mode).lower() == "codestream":
                _s(CapTransportPacketTransmissionMode.s, "codestream")
            else:
                jxsv_trans = getattr(media, 'jxsv_trans_mode', None)
                if jxsv_trans is not None and str(jxsv_trans).lower() == "sequential":
                    _s(CapTransportPacketTransmissionMode.s, "slice_sequential")
                else:
                    _s(CapTransportPacketTransmissionMode.s, "slice_out_of_order")
            if media.bitrate_kbits:
                _i(CapTransportBitRate.s, media.bitrate_kbits)

        elif enc_str == "smpte291":
            if not _check(check_sdp_rfc8331, check_sdp_st2110_10, check_sdp_st2110_40):
                return None
            if encoding is not None:
                _s(CapFormatMediaType.s, "video/" + str(encoding))

        elif enc_str == "h264":
            if not _check(check_sdp_rfc6184, check_sdp_st2110_10, check_sdp_st2110_22):
                return None
            _s(CapFormatMediaType.s, "video/H264")
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
                _s(CapTransportPacketTransmissionMode.s, "single_nal_unit")
            elif pm == 1:
                _s(CapTransportPacketTransmissionMode.s, "non_interleaved_nal_units")
            elif pm == 2:
                _s(CapTransportPacketTransmissionMode.s, "interleaved_nal_units")
            # Parameter sets transport mode
            ps = media.h264_parameter_sets
            if not ps:
                _s(CapTransportParameterSetsTransportMode.s, "in_band")
            elif ps.endswith(","):
                _s(CapTransportParameterSetsTransportMode.s, "in_and_out_of_band")
            else:
                _s(CapTransportParameterSetsTransportMode.s, "out_of_band")
            if media.bitrate_kbits:
                _i(CapTransportBitRate.s, media.bitrate_kbits)

        elif enc_str == "h265":
            if not _check(check_sdp_rfc7798, check_sdp_st2110_10, check_sdp_st2110_22):
                return None
            _s(CapFormatMediaType.s, "video/H265")
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
                    _s(CapFormatInterlaceMode.s, "progressive")
            except Exception:
                return None
            # DON diff → packet mode
            if media.h26x_max_don_diff > 0:
                _s(CapTransportPacketTransmissionMode.s, "interleaved_nal_units")
            else:
                _s(CapTransportPacketTransmissionMode.s, "non_interleaved_nal_units")
            # VPS/SPS/PPS → parameter sets transport mode
            vps = media.h265_vps
            sps = media.h265_sps
            pps = media.h265_pps
            if not vps and not sps and not pps:
                _s(CapTransportParameterSetsTransportMode.s, "in_band")
            elif (vps and vps.endswith(",")) or (sps and sps.endswith(",")) or (pps and pps.endswith(",")):
                _s(CapTransportParameterSetsTransportMode.s, "in_and_out_of_band")
            else:
                _s(CapTransportParameterSetsTransportMode.s, "out_of_band")
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
                _s(CapTransportPacketTransmissionMode.s, "interleaved_access_units")
            else:
                _s(CapTransportPacketTransmissionMode.s, "non_interleaved_access_units")
            # Config presence → parameter sets transport
            if not media.aac_config:
                _s(CapTransportParameterSetsTransportMode.s, "in_band")
            else:
                _s(CapTransportParameterSetsTransportMode.s, "out_of_band")
            _extract_audio_transport()
            # RFC 3640: constant duration overrides ptime
            if media.aac_constant_duration and media.sample_rate:
                ptime_us = (media.aac_constant_duration * 1000000) // media.sample_rate
                _i(CapTransportPacketTime.s, ptime_us)
                _i(CapTransportMaxPacketTime.s, ptime_us)

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
                _s(CapTransportPacketTransmissionMode.s, "interleaved_access_units")
            else:
                _s(CapTransportPacketTransmissionMode.s, "non_interleaved_access_units")
            # LATM/ADTS parameter sets logic
            if media.aac_config_present:
                if not media.aac_config:
                    _s(CapTransportParameterSetsTransportMode.s, "in_band")
                else:
                    _s(CapTransportParameterSetsTransportMode.s, "in_and_out_of_band")
            else:
                if not media.aac_config:
                    return None  # Error: no config available
                else:
                    _s(CapTransportParameterSetsTransportMode.s, "out_of_band")
            _extract_audio_transport()
            if media.aac_constant_duration and media.sample_rate:
                ptime_us = (media.aac_constant_duration * 1000000) // media.sample_rate
                _i(CapTransportPacketTime.s, ptime_us)
                _i(CapTransportMaxPacketTime.s, ptime_us)

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
    if ts_ref is not None and str(ts_ref).lower() == "ptp":
        _s(CapTransportClockRefType.s, "ptp")
    else:
        _s(CapTransportClockRefType.s, "internal")

    privacy_val = getattr(media, 'privacy', False)
    _b(CapTransportPrivacy.s, bool(privacy_val))

    hkep_val = getattr(media, 'hkep', False)
    _b(CapTransportHkep.s, bool(hkep_val))

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
    """Validate active constraints (Cons) against sender capabilities (Caps).

    Implements validateActiveConstraints + checkActiveConstraints.

    Steps:
    1. Mark constraints as original
    2. Normalize constraints (CCF handles mux layer/format validation, namespace filtering,
       trunk/layer creation for missing layers — equivalent to the mux branch)
    3. Check each constraint set is included in at least one sender CapSet

    Returns:
        (normalized_caps, None) on success — normalized includes auto-generated defaults for
        missing mux layers.
        (None, error_message) on failure.
    """
    try:
        from caps.MatroxCCF import conset_included_in_caps, Cons as _Cons, ConSet as _ConSet
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

    # CCF inclusion check: each constraint conset must fit in at least one sender CapSet
    if verbose:
        print(f"  [validate_constraints] sender={sender_id} format={format_urn}")
        print(f"  [validate_constraints] Constraints: {len(normalized.consets)} consets")
        print(f"  [validate_constraints] Sender caps: {len(sender_caps.capsets)} capsets")

    for conset in normalized.consets:
        if not conset_included_in_caps(conset, sender_caps):
            if verbose:
                print(f"  [validate_constraints] ConSet '{conset.label}' NOT included in any sender CapSet")
            return None, f"constraint set '{conset.label}' not included in sender capabilities"

    if verbose:
        print(f"  [validate_constraints] Result: VALID")

    return normalized, None


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

        for prop_name, prop_cap in flow_caps.caps.items():
            # Default: keep flow's current value
            compliant[prop_name] = prop_cap

            # Check if constraint has this property
            constraint = conset.cons.get(prop_name)
            if constraint is None or constraint.value.infinite:
                continue  # No constraint or unconstrained

            # Check if current value satisfies constraint
            current_ok = False
            if prop_cap.value.values and not reset:
                try:
                    current_ok = value_included_in_range(prop_cap.value.values[0], constraint.value)
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

        if failed:
            continue  # Try next constraint set

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

    # NOTE: UUID cascade (Atomic State Changes) is NOT done here.
    # It is done in force_active_constraints() AFTER all mutations
    # (trunk + sub-flows) are complete, to avoid stale ID references
    # during the mux sub-flow forcing loop.

    # Propagate compliant properties to linked receiver's constraint sets
    # via updateReceiverCapabilitiesCompatibility
    try:
        _propagate_to_linked_receiver(node, flow_ptr, compliant_caps, compliant_groups, verbose)
    except Exception as exc:
        if verbose:
            print(f"  [update_sender_flow] Receiver propagation skipped: {exc}")

    # Update sender's optional format attributes via AddSenderOptionalFormatAttributes
    if hasattr(node, '_add_sender_optional_format_attributes'):
        node._add_sender_optional_format_attributes(sender)

    return True


# ---------------------------------------------------------------------------
# Generic property filtering (getGenericProperties)
# ---------------------------------------------------------------------------

# Generic video constraints (RAW ones without media_type)
_GENERIC_VIDEO_PROPS: set[str] = {
    CapFormatGrainRate.s, CapFormatFrameWidth.s, CapFormatFrameHeight.s,
    CapFormatInterlaceMode.s, CapFormatColorspace.s,
    CapFormatTransferCharacteristic.s, CapFormatColorSampling.s,
    CapFormatComponentDepth.s,
}

# Generic audio constraints (PCM ones without media_type)
_GENERIC_AUDIO_PROPS: set[str] = {
    CapFormatChannelCount.s, CapFormatSampleRate.s, CapFormatSampleDepth.s,
}


def _get_generic_properties(
    flow_format: str, compliant_caps: Any,
) -> dict[str, Any]:
    """Filter compliant properties to generic (non-transport) ones only.

    Only properties that can be meaningfully propagated to a receiver's
    native constraint sets are kept.
    """
    if compliant_caps is None:
        return {}

    if "video" in flow_format:
        allowed = _GENERIC_VIDEO_PROPS
    elif "audio" in flow_format:
        allowed = _GENERIC_AUDIO_PROPS
    else:
        return {}

    # Extract matching properties from the compliant CapSet
    result: dict[str, Any] = {}
    caps = compliant_caps.caps if hasattr(compliant_caps, 'caps') else {}
    for prop_name, cap in caps.items():
        if prop_name in allowed:
            result[prop_name] = cap
    return result


def _update_receiver_native_constraints(
    constraint_sets_field: Any,
    generic_props: dict[str, Any],
    source_layer: int,
    source_format: str,
    verbose: bool = False,
) -> bool:
    """Update receiver's native constraint sets with compliant property values.

    Finds the preference=100 constraint set matching the source layer/format,
    then replaces each property's constraint with a single-value enum.

    Returns True if a constraint set was updated.
    """
    from caps.MatroxCCF import Cap, RangeValue, RangeType

    cs_list = constraint_sets_field.value
    if not cs_list:
        return False

    items = cs_list._inner if hasattr(cs_list, '_inner') else cs_list
    if not items:
        return False

    for cs in items:
        # Check enabled
        enabled = True
        if hasattr(cs, 'MetaEnabled') and cs.MetaEnabled.defined:
            enabled = cs.MetaEnabled.value
        if not enabled:
            layer_enabled = False
            if hasattr(cs, 'MetaLayerEnabled') and cs.MetaLayerEnabled.defined:
                layer_enabled = cs.MetaLayerEnabled.value
            if not layer_enabled:
                continue

        # Only preference=100
        pref = 0
        if hasattr(cs, 'MetaPreference') and cs.MetaPreference.defined:
            pref = cs.MetaPreference.value
        if pref != 100:
            continue

        # Layer/format matching
        if source_layer >= 0:
            cs_layer = -1
            if hasattr(cs, 'MetaLayer') and cs.MetaLayer.defined:
                cs_layer = cs.MetaLayer.value
            if cs_layer != source_layer:
                continue
            cs_format = ""
            if hasattr(cs, 'MetaFormat') and cs.MetaFormat.defined:
                cs_format = str(cs.MetaFormat.value)
            if source_format and cs_format and source_format not in cs_format and cs_format not in source_format:
                continue
        else:
            # No layer specified — skip constraint sets that have a layer
            if hasattr(cs, 'MetaLayer') and cs.MetaLayer.defined:
                continue

        # Update constraints with generic properties
        if not hasattr(cs, 'Constraints'):
            continue

        constraints = cs.Constraints
        # NConstraintsValue wraps a dict — access via .Get() or ._inner
        constraint_dict = None
        if hasattr(constraints, 'Get'):
            constraint_dict = constraints.Get()
        elif hasattr(constraints, '_inner') and isinstance(constraints._inner, dict):
            constraint_dict = constraints._inner
        elif hasattr(constraints, 'value') and isinstance(constraints.value, dict):
            constraint_dict = constraints.value
        if constraint_dict is None:
            continue

        for prop_name, cap in generic_props.items():
            # Extract the value from the Cap and create a single-value enum constraint
            if not hasattr(cap, 'value') or cap.value is None:
                continue

            val = cap.value
            # Get the first value from the Cap's enumerated/range values
            first_val = None
            if hasattr(val, 'enumerated') and val.enumerated:
                first_val = next(iter(val.enumerated))
            elif hasattr(val, 'values') and val.values:
                first_val = val.values[0]
            elif hasattr(val, 'min') and val.min is not None:
                first_val = val.min

            if first_val is None:
                continue

            # Build a new constraint from the NConstraintSet's decode infrastructure
            # For simplicity, update existing constraint's enum if it exists,
            # or create a new one via the constraint set's JSON decode path
            from nmos.enums import EnumRegistry
            enum_id = EnumRegistry.get(prop_name) if isinstance(prop_name, str) else prop_name

            # Build a constraint dict and decode it onto the constraint set
            # This is the simplest way to update without knowing the exact NConstraint type
            from fractions import Fraction
            if isinstance(first_val, bool):
                constraint_dict[enum_id] = {"enum": [first_val]}
            elif isinstance(first_val, int):
                constraint_dict[enum_id] = {"enum": [first_val]}
            elif isinstance(first_val, float):
                constraint_dict[enum_id] = {"enum": [first_val]}
            elif isinstance(first_val, str):
                constraint_dict[enum_id] = {"enum": [first_val]}
            elif isinstance(first_val, Fraction):
                constraint_dict[enum_id] = {"enum": [{"numerator": first_val.numerator, "denominator": first_val.denominator}]}
            elif hasattr(first_val, 's'):  # EnumId
                constraint_dict[enum_id] = {"enum": [str(first_val)]}

        if verbose:
            print(f"  [update_native] Updated preference=100 CS with {len(generic_props)} properties")

        return True

    return False


def _propagate_to_linked_receiver(
    node: Any,
    flow_ptr: Any,
    compliant_caps: Any,
    compliant_groups: list[int] | None,
    verbose: bool = False,
) -> None:
    """Propagate sender's compliant flow properties to the linked receiver's
    native constraint sets.

    Steps:
    1. Get the flow's source → read SourceCore.ReceiverId and SourceCore.Layer
    2. If no linked receiver, skip
    3. Find the receiver
    4. Bump the receiver's version (the constraint sets would be updated here
       in a full implementation; for now we just bump the version to signal
       that the receiver's state has changed)
    """
    from nmos.node import _get_flow_core, _get_source_core, _set_version_now
    from nmos.node.store import to_static_id

    flow_core = _get_flow_core(flow_ptr)

    # Get the source
    if not flow_core.SourceId.defined or flow_core.SourceId.value is None:
        return
    source_id = flow_core.SourceId.value
    source_ptr = node.sources.get(source_id)
    if source_ptr is None:
        return

    source_core = _get_source_core(source_ptr)

    # Check if source has a linked receiver
    if not source_core.ReceiverId.defined or source_core.ReceiverId.value is None:
        return  # No linked receiver — nothing to propagate

    receiver_id = source_core.ReceiverId.value
    source_layer = source_core.Layer.value if source_core.Layer.defined else -1

    if verbose:
        print(f"  [propagate_to_receiver] Source links to receiver {receiver_id}, layer={source_layer}")

    # Find receiver by dynamic ID
    receiver_ptr = node.receivers.get(receiver_id)
    if receiver_ptr is None:
        # Try static ID lookup
        static_id = to_static_id(receiver_id)
        receiver_ptr = node.receivers.get(static_id)
    if receiver_ptr is None:
        if verbose:
            print(f"  [propagate_to_receiver] Receiver {receiver_id} not found, skipping")
        return

    # Get flow format for property filtering
    flow_inner = flow_ptr.get() if hasattr(flow_ptr, 'get') else flow_ptr
    fv = flow_inner.value if hasattr(flow_inner, 'value') else flow_inner
    flow_format = str(fv.Format.value) if hasattr(fv, 'Format') and fv.Format.defined else ""

    # Filter to generic properties only (getGenericProperties)
    generic_props = _get_generic_properties(flow_format, compliant_caps)

    if not generic_props:
        if verbose:
            print(f"  [propagate_to_receiver] No generic properties to propagate")
        return

    inner = receiver_ptr.get() if hasattr(receiver_ptr, 'get') else receiver_ptr
    rv = inner.value if hasattr(inner, 'value') else inner
    core = getattr(rv, 'ReceiverCore', rv)

    # Update receiver's native constraint sets (updateReceiverNativePropertiesCompatibility)
    if hasattr(rv, 'Caps') and rv.Caps.defined:
        caps_val = rv.Caps.value
        if hasattr(caps_val, 'ConstraintSets') and caps_val.ConstraintSets.defined:
            updated = _update_receiver_native_constraints(
                caps_val.ConstraintSets, generic_props, source_layer,
                flow_format, verbose,
            )
            if updated:
                # Bump caps version (capsValue.Version.Now())
                from nmos.node import _nmos_version_now
                if hasattr(caps_val, 'Version'):
                    caps_val.Version.value = _nmos_version_now()

    # Bump receiver version (receiver.UpdateVersion())
    _set_version_now(core.ResourceCore)

    if verbose:
        print(f"  [propagate_to_receiver] Receiver caps+version bumped, {len(generic_props)} properties propagated")


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
        result = "constrained"
    elif status == "incompatible":
        result = "active_constraints_violation"
    else:
        result = "unconstrained"

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
        return "unknown"

    compatible = check_receiver_compatibility(
        node, receiver_id, stream_caps, verbose=verbose,
    )

    return "compliant" if compatible else "non_compliant"


def update_receiver_native_properties(
    node: Any,
    receiver_id: str,
    compliant_caps: Any,
    verbose: bool = False,
) -> None:
    """Update receiver's native (preference=100) constraint set with new properties.

    PARTIAL: Missing layer/format filtering (needed for mux sub-flows).
    TODO: Add layer/format parameters and filtering when receiver→sender
    pipelines are implemented. Also implement checkReceiverNativePropertiesCompatibility
    and updateReceiverConstraintsToFlowProperties which depend on this function.

    Writes single-value capabilities from compliant_caps into the native CapSet.
    """
    try:
        from caps.MatroxCCF import CapSet, Cap, RangeValue, RangeType
    except ImportError:
        return

    if compliant_caps is None:
        return

    receiver_caps = _get_receiver_ccf_caps(node, node.receivers.get(receiver_id))
    if receiver_caps is None:
        return

    # Find the native CapSet (preference=100)
    for cs in receiver_caps.capsets:
        if cs.preference == 100:
            # Update each cap in the native set with values from compliant_caps
            for name, cap in compliant_caps.caps.items():
                if cap.value.values and len(cap.value.values) == 1:
                    cs.caps[name] = Cap(name, RangeValue(
                        values=cap.value.values, type=cap.value.type))
            if verbose:
                print(f"  [update_native] Updated native CapSet for receiver {receiver_id}")
            return


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
        return "unknown"

    compatible = check_receiver_compatibility(
        node, receiver_id, stream_caps, verbose=verbose,
    )

    result = "compliant_stream" if compatible else "non_compliant_stream"

    if verbose:
        print(f"  [set_receiver_state] {receiver_id} → {result}")

    return result
