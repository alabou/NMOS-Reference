# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Convert an NMOS flow to CCF capabilities (CapSet).

Extracts all format and transport properties from a polymorphic NFlowValue
and converts them to a CCF CapSet where each property becomes a
single-value Capability.

This is the inverse of the config→caps path: instead of going from
JSON constraint_sets → CCF Caps, we go from a live NMOS flow → CCF CapSet.
The resulting CapSet can be used for compatibility checking (is the flow's
operating point included in a receiver's capabilities?).
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any

from nmos.node.codec import get_sdp_color_sampling


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_flow_to_caps(node: Any, flow_ptr: Any) -> Any:
    """Convert an NMOS flow to a CCF CapSet.

    Extracts properties from the polymorphic NFlowValue and its associated
    source, then wraps each property as a single-value CCF Capability.

    Args:
        node: The Node instance (needed to look up sources for clock/sync info).
        flow_ptr: An NFlowValue (polymorphic flow wrapper) from the flow store.

    Returns:
        A CCF CapSet with one Capability per flow property, or an empty CapSet
        if the flow is None/undefined.
    """
    try:
        from caps.MatroxCCF import (
            Caps, CapSet, Cap, RangeValue, RangeType,
            CapFormatMediaType, CapFormatGrainRate,
            CapFormatFrameWidth, CapFormatFrameHeight,
            CapFormatInterlaceMode, CapFormatColorspace,
            CapFormatTransferCharacteristic, CapFormatColorSampling,
            CapFormatComponentDepth,
            CapFormatChannelCount, CapFormatSampleRate, CapFormatSampleDepth,
            CapFormatBitRate, CapFormatConstantBitRate,
            CapFormatProfile, CapFormatLevel, CapFormatSublevel, CapFormatFbblevel,
            CapFormatVideoLayers, CapFormatAudioLayers, CapFormatDataLayers,
            CapTransportClockRefType, CapTransportSynchronousMedia,
        )
    except ImportError:
        # CCF not available — return a plain dict as fallback
        return {}

    caps: dict[str, Cap] = {}

    if flow_ptr is None:
        return CapSet(caps=caps, preference=100, label="Flow properties")

    # Get the polymorphic inner (NFlowVideoRaw, NFlowAudioCoded, etc.)
    poly = flow_ptr.get() if hasattr(flow_ptr, 'get') else flow_ptr
    if poly is None:
        return CapSet(caps=caps, preference=100, label="Flow properties")

    # Get the concrete value from the wrapper
    flow_val = poly.value if hasattr(poly, 'value') else poly

    # Determine flow type by checking which attributes exist.
    # The flow store may return either wrapper types (NFlowVideoRaw) or
    # value types (NFlowVideoRawValue) depending on context.
    from nmos.types.generated.nflow_video_raw import NFlowVideoRaw, NFlowVideoRawValue
    from nmos.types.generated.nflow_video_coded import NFlowVideoCoded, NFlowVideoCodedValue
    from nmos.types.generated.nflow_audio_raw import NFlowAudioRaw, NFlowAudioRawValue
    from nmos.types.generated.nflow_audio_coded import NFlowAudioCoded, NFlowAudioCodedValue
    from nmos.types.generated.nflow_data import NFlowData, NFlowDataValue
    from nmos.types.generated.nflow_data_json import NFlowDataJson, NFlowDataJsonValue
    from nmos.types.generated.nflow_data_sdianc import NFlowDataSdianc, NFlowDataSdiancValue
    from nmos.types.generated.nflow_mux import NFlowMux, NFlowMuxValue

    # Helper to make a single-value Cap from different value types
    def _cap_str(name: str, val: str) -> Cap:
        return Cap(name=name, value=RangeValue(values=(val,), type=RangeType.STRING))

    def _cap_int(name: str, val: int) -> Cap:
        return Cap(name=name, value=RangeValue(values=(val,), type=RangeType.INT))

    def _cap_bool(name: str, val: bool) -> Cap:
        return Cap(name=name, value=RangeValue(values=(val,), type=RangeType.BOOL))

    def _cap_rational(name: str, num: int, den: int = 1) -> Cap:
        return Cap(name=name, value=RangeValue(values=(Fraction(num, den),), type=RangeType.RATIONAL))

    def _cap_from_enum(name: str, field: Any) -> Cap | None:
        """Create a string Cap from an NEnum field."""
        if not hasattr(field, 'defined') or not field.defined:
            return None
        return _cap_str(name, str(field.value))

    def _cap_from_int(name: str, field: Any) -> Cap | None:
        """Create an int Cap from an NInt field."""
        if not hasattr(field, 'defined') or not field.defined:
            return None
        return _cap_int(name, field.value)

    def _cap_from_bool(name: str, field: Any) -> Cap | None:
        """Create a bool Cap from an NBool field."""
        if not hasattr(field, 'defined') or not field.defined:
            return None
        return _cap_bool(name, field.value)

    def _cap_from_rational(name: str, field: Any) -> Cap | None:
        """Create a rational Cap from an NRational field."""
        if not hasattr(field, 'defined') or not field.defined:
            return None
        rv = field.value  # NRationalValue
        num = rv.Numerator.value if rv.Numerator.defined else 0
        den = rv.Denominator.value if rv.Denominator.defined else 1
        return _cap_rational(name, num, den)

    def _add(cap: Cap | None) -> None:
        if cap is not None:
            caps[cap.name] = cap

    # --- Extract source-level properties (clock, sync) ---
    def _get_source_sync(flow_core: Any) -> tuple[str, bool | None]:
        """Get clock name and synchronous_media from the flow's source.

        ``clock_name`` is a REQUIRED IS-04 source member, read as-is (an
        undefined value is a genuine spec violation and stays fatal).

        ``synchronous_media`` (``urn:x-matrox:synchronous_media``) is an
        OPTIONAL source member, so it is returned as ``None`` when absent
        rather than read raw. The node's own pipeline-built sources always
        set it (node behaviour unchanged), but this function is reused by the
        controller on arbitrary registry sources from other products/vendors
        that may legitimately omit it — an unguarded ``.value`` would raise
        ``NotAvailable`` and abort the whole flow→caps conversion, leaving the
        resource with no caps and no flow-match.

        Returns (clock_name, synchronous_media | None).
        """
        source_id = flow_core.SourceId.value
        source_ptr = node.sources.get(source_id)
        assert source_ptr is not None, f"source {source_id} not found"

        src_inner = source_ptr.get() if hasattr(source_ptr, 'get') else source_ptr
        assert src_inner is not None, f"source {source_id} has no value"

        src_val = src_inner.value if hasattr(src_inner, 'value') else src_inner
        src_core = getattr(src_val, 'SourceCore', src_val)

        clk_name: str = str(src_core.ClockName.value)
        sync_media: bool | None = (
            src_core.SynchronousMedia.value
            if src_core.SynchronousMedia.defined else None
        )
        return clk_name, sync_media

    def _add_transport_caps(flow_core: Any) -> None:
        """Add transport caps (clock ref, sync) if layer < 0 or undefined.

        Only adds these for top-level flows (not mux sub-flows): the layer
        value must be either undefined or negative.
        """
        layer = flow_core.Layer.value if flow_core.Layer.defined else -1
        if layer < 0:
            clk_name, sync_media = _get_source_sync(flow_core)
            ptp_str = "ptp" if clk_name == "clk0" else "internal"
            _add(_cap_str(CapTransportClockRefType, ptp_str))
            # Optional source member — omit the cap when the source doesn't
            # declare it (rather than inventing a value).
            if sync_media is not None:
                _add(_cap_bool(CapTransportSynchronousMedia, sync_media))

    # --- Dispatch by flow type ---

    # If poly is already a Value type, use it directly as flow_val
    if isinstance(poly, (NFlowVideoRawValue, NFlowVideoCodedValue,
                         NFlowAudioRawValue, NFlowAudioCodedValue,
                         NFlowDataValue, NFlowDataJsonValue, NFlowDataSdiancValue,
                         NFlowMuxValue)):
        flow_val = poly

    def _get_audio_channel_count(flow_core: Any) -> int:
        """Get channel count from the audio source.

        Checks source type (NSourceAudio) before accessing Channels.
        Returns 0 if source is not an audio source (e.g., NSourceMux).
        """
        source_id = flow_core.SourceId.value
        source_ptr = node.sources.get(source_id)
        assert source_ptr is not None, f"source {source_id} not found"

        src_inner = source_ptr.get() if hasattr(source_ptr, 'get') else source_ptr
        src_val = src_inner.value if hasattr(src_inner, 'value') else src_inner
        if not hasattr(src_val, 'Channels') or not src_val.Channels.defined:
            return 0
        return len(src_val.Channels.value)

    if isinstance(poly, (NFlowAudioRaw, NFlowAudioRawValue)):
        # NFlowAudioRaw branch
        _add(_cap_from_enum(CapFormatMediaType, flow_val.MediaType))
        _add(_cap_int(CapFormatChannelCount, _get_audio_channel_count(flow_val.FlowCore)))

        # Favor SampleRate over GrainRate for audio
        if hasattr(flow_val, 'SampleRate'):
            _add(_cap_from_rational(CapFormatSampleRate, flow_val.SampleRate))
        else:
            _add(_cap_from_rational(CapFormatSampleRate, flow_val.FlowCore.GrainRate))
        _add(_cap_from_int(CapFormatSampleDepth, flow_val.BitDepth))
        _add_transport_caps(flow_val.FlowCore)

    elif isinstance(poly, (NFlowAudioCoded, NFlowAudioCodedValue)):
        # NFlowAudioCoded branch
        _add(_cap_from_enum(CapFormatMediaType, flow_val.MediaType))
        _add(_cap_int(CapFormatChannelCount, _get_audio_channel_count(flow_val.FlowCore)))

        # Favor SampleRate over GrainRate for coded audio
        if hasattr(flow_val, 'SampleRate'):
            _add(_cap_from_rational(CapFormatSampleRate, flow_val.SampleRate))
        else:
            _add(_cap_from_rational(CapFormatSampleRate, flow_val.FlowCore.GrainRate))

        _add(_cap_from_int(CapFormatBitRate, flow_val.Bitrate))
        _add(_cap_from_bool(CapFormatConstantBitRate, flow_val.ConstantBitrate))
        _add(_cap_from_enum(CapFormatProfile, flow_val.Profile))
        _add(_cap_from_enum(CapFormatLevel, flow_val.Level))
        _add_transport_caps(flow_val.FlowCore)

    elif isinstance(poly, (NFlowVideoRaw, NFlowVideoRawValue)):
        # NFlowVideoRaw branch
        _add(_cap_from_enum(CapFormatMediaType, flow_val.MediaType))
        _add(_cap_from_rational(CapFormatGrainRate, flow_val.FlowCore.GrainRate))
        _add(_cap_from_int(CapFormatFrameWidth, flow_val.FrameWidth))
        _add(_cap_from_int(CapFormatFrameHeight, flow_val.FrameHeight))
        _add(_cap_from_enum(CapFormatInterlaceMode, flow_val.InterlaceMode))
        _add(_cap_from_enum(CapFormatColorspace, flow_val.Colorspace))
        _add(_cap_from_enum(CapFormatTransferCharacteristic, flow_val.TransferCharacteristic))

        # Color sampling and component depth from components.
        # An undefined Components is treated as a fatal error — internal data from pipeline builder.
        components = flow_val.Components.value
        sampling_str = get_sdp_color_sampling(components)
        _add(_cap_str(CapFormatColorSampling, sampling_str))
        _add(_cap_int(CapFormatComponentDepth, components[0].BitDepth.value))

        _add_transport_caps(flow_val.FlowCore)

    elif isinstance(poly, (NFlowVideoCoded, NFlowVideoCodedValue)):
        # NFlowVideoCoded branch
        _add(_cap_from_enum(CapFormatMediaType, flow_val.MediaType))
        _add(_cap_from_rational(CapFormatGrainRate, flow_val.FlowCore.GrainRate))
        _add(_cap_from_int(CapFormatFrameWidth, flow_val.FrameWidth))
        _add(_cap_from_int(CapFormatFrameHeight, flow_val.FrameHeight))
        _add(_cap_from_enum(CapFormatInterlaceMode, flow_val.InterlaceMode))
        _add(_cap_from_enum(CapFormatColorspace, flow_val.Colorspace))
        _add(_cap_from_enum(CapFormatTransferCharacteristic, flow_val.TransferCharacteristic))

        # Color sampling and component depth from components.
        components = flow_val.Components.value
        sampling_str = get_sdp_color_sampling(components)
        _add(_cap_str(CapFormatColorSampling, sampling_str))
        _add(_cap_int(CapFormatComponentDepth, components[0].BitDepth.value))

        _add(_cap_from_int(CapFormatBitRate, flow_val.Bitrate))
        _add(_cap_from_bool(CapFormatConstantBitRate, flow_val.ConstantBitrate))
        _add(_cap_from_enum(CapFormatProfile, flow_val.Profile))
        _add(_cap_from_enum(CapFormatLevel, flow_val.Level))
        _add(_cap_from_enum(CapFormatSublevel, flow_val.Sublevel))
        _add(_cap_from_enum(CapFormatFbblevel, flow_val.Fbblevel))
        _add_transport_caps(flow_val.FlowCore)

    elif isinstance(poly, (NFlowData, NFlowDataValue)):
        # NFlowData branch
        _add(_cap_from_enum(CapFormatMediaType, flow_val.MediaType))
        _add_transport_caps(flow_val.FlowCore)

    elif isinstance(poly, (NFlowDataJson, NFlowDataJsonValue)):
        # NFlowDataJson branch
        _add(_cap_from_enum(CapFormatMediaType, flow_val.MediaType))

    elif isinstance(poly, (NFlowDataSdianc, NFlowDataSdiancValue)):
        # NFlowDataSdianc branch
        _add(_cap_from_enum(CapFormatMediaType, flow_val.MediaType))

    elif isinstance(poly, (NFlowMux, NFlowMuxValue)):
        # NFlowMux branch
        _add(_cap_from_enum(CapFormatMediaType, flow_val.MediaType))
        _add(_cap_from_int(CapFormatVideoLayers, flow_val.VideoLayers))
        _add(_cap_from_int(CapFormatAudioLayers, flow_val.AudioLayers))
        _add(_cap_from_int(CapFormatDataLayers, flow_val.DataLayers))
        _add_transport_caps(flow_val.FlowCore)

    # Extract format and layer from the flow for CCF part matching.
    # Only sub-flows (mux children with Layer set) carry format/layer — standalone
    # flows use format=None, layer=None to match trunk capsets.
    flow_format: str | None = None
    flow_layer: int | None = None

    flow_core = None
    if hasattr(flow_val, 'FlowCore'):
        flow_core = flow_val.FlowCore
    if flow_core is not None and hasattr(flow_core, 'Layer') and flow_core.Layer.defined:
        flow_layer = flow_core.Layer.value
        # Only set format when layer is set (sub-flow in mux hierarchy)
        if hasattr(flow_val, 'Format') and flow_val.Format.defined:
            flow_format = str(flow_val.Format.value)

    return CapSet(caps=caps, preference=100, label="Flow properties",
                  format=flow_format, layer=flow_layer)
