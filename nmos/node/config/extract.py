# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""CapSet → concrete value extraction + Flow/Source/Sender/Receiver builders.

Extracts concrete parameter values from CCF CapSet objects and uses them
to construct the generated NMOS resource types (NSourceVideoValue,
NFlowVideoRawValue, NSenderValue, etc.).
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any

from nmos.json.engine import JsonEngine
from nmos.enums import (
    # Formats
    FormatVideo, FormatAudio, FormatData, FormatMux,
    # Media types
    VideoRaw, VideoCodedH264, VideoCodedH265, VideoCodedJxsv,
    AudioRawL8, AudioRawL16, AudioRawL20, AudioRawL24, MuxMpeg2TS,
    # Capability URNs
    CapFormatMediaType, CapFormatChannelCount, CapFormatSampleRate, CapFormatSampleDepth,
    CapFormatGrainRate, CapFormatFrameWidth, CapFormatFrameHeight, CapFormatComponentDepth,
    CapFormatColorspace, CapFormatColorSampling, CapFormatProfile, CapFormatLevel,
    CapFormatBitRate, CapFormatConstantBitRate, CapMetaFormat,
    # Colorspace / sampling
    BT709, SamplingYCbCr_422,
    # Audio channels / video components
    L, R, C, LFE, Ls, Rs, Lss, Rss, Y, Cb, Cr, G, B,
)


# ---------------------------------------------------------------------------
# CapSet → parameter dict extraction
# ---------------------------------------------------------------------------

def extract_params_from_capset(capset: Any) -> dict[str, Any]:
    """Extract concrete values from a CCF CapSet.

    For each capability:
    - Enum with one value: use that value
    - Enum with multiple: use first (will be the preferred one)
    - Range [min..max]: use min (conservative)
    - Infinite: skip (no concrete value)

    Returns dict of capability URN → concrete value.
    """
    params: dict[str, Any] = {}
    if capset is None:
        return params

    for name, cap in capset.caps.items():
        rv = cap.value
        if rv.empty:
            continue
        if rv.infinite:
            continue
        if rv.enumerated:
            # Use first enumerated value
            val = next(iter(rv.enumerated))
            params[name] = val
        elif rv.min is not None:
            params[name] = rv.min
    return params


# ---------------------------------------------------------------------------
# Source builders
# ---------------------------------------------------------------------------

def build_video_source(params: dict[str, Any], sender_config: dict[str, Any]) -> Any:
    """Build an NSourceValue (video), auto-generated from sender config + params."""
    from nmos.types.generated.nsource_video import NSourceVideoValue
    from nmos.types.generated.nsource import NSourceValue
    from nmos.enums import EnumRegistry

    inner = NSourceVideoValue()
    inner.set_to_default()
    inner.Format.value = EnumRegistry.get(FormatVideo.s)

    _set_source_core(inner.SourceCore, params, sender_config)

    source = NSourceValue()
    source.set(inner)
    return source


def build_audio_source(params: dict[str, Any], sender_config: dict[str, Any]) -> Any:
    """Build an NSourceValue (audio), auto-generated from sender config + params."""
    from nmos.types.generated.nsource_audio import NSourceAudioValue
    from nmos.types.generated.nsource import NSourceValue
    from nmos.enums import EnumRegistry

    inner = NSourceAudioValue()
    inner.set_to_default()
    inner.Format.value = EnumRegistry.get(FormatAudio.s)

    _set_source_core(inner.SourceCore, params, sender_config)

    # Audio channels from constraint set or default
    from nmos.types.generated.naudio_channel import NAudioChannelValue
    channel_count = params.get(CapFormatChannelCount.s, 2)
    if isinstance(channel_count, int) and channel_count > 0:
        # Standard channel symbols for common configurations
        _CHANNEL_SYMBOLS = [L.s, R.s, C.s, LFE.s, Ls.s, Rs.s, Lss.s, Rss.s]
        channels: list[NAudioChannelValue] = []
        for i in range(channel_count):
            ch = NAudioChannelValue()
            ch.Label.value = f"Channel {i}"
            if i < len(_CHANNEL_SYMBOLS):
                ch.Symbol.value = EnumRegistry.get(_CHANNEL_SYMBOLS[i])
            channels.append(ch)
        inner.Channels._defined = True
        inner.Channels._value._inner = channels

    source = NSourceValue()
    source.set(inner)
    return source


def build_data_source(params: dict[str, Any], sender_config: dict[str, Any]) -> Any:
    """Build an NSourceValue (data), auto-generated from sender config + params."""
    from nmos.types.generated.nsource_data import NSourceDataValue
    from nmos.types.generated.nsource import NSourceValue
    from nmos.enums import EnumRegistry

    inner = NSourceDataValue()
    inner.set_to_default()
    inner.Format.value = EnumRegistry.get(FormatData.s)

    _set_source_core(inner.SourceCore, params, sender_config)

    source = NSourceValue()
    source.set(inner)
    return source


def build_mux_source(
    sender_config: dict[str, Any],
    parent_source_ids: list[str],
    node: Any,
) -> Any:
    """Build a mux NSourceValue with parent source references.

    Inherits grain_rate from the first sub-source that has one.
    """
    from nmos.types.generated.nsource_mux import NSourceMuxValue
    from nmos.types.generated.nsource import NSourceValue
    from nmos.enums import EnumRegistry

    inner = NSourceMuxValue()
    inner.set_to_default()
    inner.Format.value = EnumRegistry.get(FormatMux.s)

    _set_source_core(inner.SourceCore, {}, sender_config)

    # Set parent source IDs and inherit grain_rate from first sub-source
    if parent_source_ids:
        from nmos.node import _get_source_core
        dynamic_ids = []
        for static_id in parent_source_ids:
            src = node.sources.get(static_id)
            if src is not None:
                sc = _get_source_core(src)
                dynamic_ids.append(sc.ResourceCore.Id.value)
                # Inherit grain_rate from first sub-source that has one
                if not inner.SourceCore.GrainRate.defined and sc.GrainRate.defined:
                    inner.SourceCore.GrainRate.set_value(sc.GrainRate.value.clone())
        inner.SourceCore.Parents.value = dynamic_ids

    source = NSourceValue()
    source.set(inner)
    return source


def _set_rational(field: Any, value: Fraction) -> None:
    """Set an NRational field from a Fraction value.

    NRational wraps NRationalValue which has Numerator: NInt, Denominator: NInt.
    """
    field._defined = True
    field._value.Numerator.value = value.numerator
    field._value.Denominator.value = value.denominator


def _set_source_core(
    source_core: Any,
    params: dict[str, Any],
    sender_config: dict[str, Any],
    source_index: int = 0,
) -> None:
    """Set common source core fields, auto-generated from sender config + params.

    Source information is inferred:
    - Label: derived from sender label + "Source" prefix
    - ClockName: "clk1" — the internal clock. This node's sources do not
      use PTP, so they reference the internal clock and their SDP carries
      ts-refclk:localmac.
    - SynchronousMedia: False — this node only produces asynchronous
      signals (SDP mediaclk:sender).
    - GrainRate: from constraint_sets grain_rate capability
    """
    sender_label = sender_config.get("label", "")
    format_urn = sender_config.get("format", "")

    # Auto-generate source label from sender label
    if "video" in format_urn:
        fmt_name = "Video"
    elif "audio" in format_urn:
        fmt_name = "Audio"
    elif "mux" in format_urn:
        fmt_name = "Mux"
    else:
        fmt_name = "Data"
    source_core.ResourceCore.Label.value = f"Source {fmt_name} {source_index}"
    if sender_label:
        source_core.ResourceCore.Description.value = f"Source for {sender_label}"

    # Defaults: internal clock, asynchronous signal (no PTP on this node)
    source_core.ClockName.value = "clk1"
    source_core.SynchronousMedia.value = False

    # Grain rate from params (extracted from highest-preference constraint set)
    # For video: grain_rate. For audio: sample_rate maps to GrainRate (GrainRate is used for both).
    grain_rate = params.get(CapFormatGrainRate.s)
    if grain_rate is not None and isinstance(grain_rate, Fraction):
        _set_rational(source_core.GrainRate, grain_rate)
    elif grain_rate is None:
        sample_rate = params.get(CapFormatSampleRate.s)
        if sample_rate is not None and isinstance(sample_rate, Fraction):
            _set_rational(source_core.GrainRate, sample_rate)


# ---------------------------------------------------------------------------
# Flow builders
# ---------------------------------------------------------------------------

def build_video_flow(
    params: dict[str, Any], source_id: str, config: dict[str, Any],
) -> Any:
    """Build an NFlowValue (video raw or coded) from extracted params."""
    from nmos.types.generated.nflow import NFlowValue
    from nmos.enums import EnumRegistry

    media_type = str(params.get(CapFormatMediaType.s, VideoRaw.s))

    if media_type == VideoRaw.s:
        return _build_video_raw_flow(params, source_id, config)
    else:
        return _build_video_coded_flow(params, source_id, config, media_type)


def _build_video_raw_flow(
    params: dict[str, Any], source_id: str, config: dict[str, Any],
) -> Any:
    """Build a raw video flow."""
    from nmos.types.generated.nflow_video_raw import NFlowVideoRawValue
    from nmos.types.generated.nflow import NFlowValue
    from nmos.enums import EnumRegistry

    inner = NFlowVideoRawValue()
    inner.set_to_default()
    inner.Format.value = EnumRegistry.get(FormatVideo.s)
    inner.MediaType.value = EnumRegistry.get(VideoRaw.s)
    inner.FlowCore.SourceId.value = source_id

    _set_flow_core(inner.FlowCore, params, config)
    _set_video_fields(inner, params)

    flow = NFlowValue()
    flow.set(inner)
    return flow


def _build_video_coded_flow(
    params: dict[str, Any], source_id: str, config: dict[str, Any],
    media_type: str,
) -> Any:
    """Build a coded video flow (H264, H265, JXSV)."""
    from nmos.types.generated.nflow_video_coded import NFlowVideoCodedValue
    from nmos.types.generated.nflow import NFlowValue
    from nmos.enums import EnumRegistry

    inner = NFlowVideoCodedValue()
    inner.set_to_default()
    inner.Format.value = EnumRegistry.get(FormatVideo.s)

    mt_enum = EnumRegistry.get(media_type)
    if mt_enum is not None:
        inner.MediaType.value = mt_enum

    inner.FlowCore.SourceId.value = source_id

    _set_flow_core(inner.FlowCore, params, config)
    _set_video_fields(inner, params)

    # Coded-specific fields
    profile = params.get(CapFormatProfile.s)
    if profile is not None:
        p_enum = EnumRegistry.get(str(profile))
        if p_enum is not None:
            inner.Profile.value = p_enum

    level = params.get(CapFormatLevel.s)
    if level is not None:
        l_enum = EnumRegistry.get(str(level))
        if l_enum is not None:
            inner.Level.value = l_enum

    bitrate = params.get(CapFormatBitRate.s)
    if bitrate is not None:
        inner.Bitrate.value = int(bitrate)

    # constant_bit_rate is a defining property of a coded video flow, so
    # always publish it: take the config's value when present, else default
    # to False (VBR). Without this the flow omits constant_bit_rate and the
    # controller can't reflect it. Mirrors the audio coded builder below.
    cbr = params.get(CapFormatConstantBitRate.s)
    inner.ConstantBitrate.value = bool(cbr) if cbr is not None else False

    # Auto-select level if not explicitly set
    if not inner.Level.defined:
        from nmos.node.codec import (
            select_h264_level_from_coded_flow,
            select_h265_level_from_coded_flow,
            select_jxsv_level_from_coded_flow,
        )
        if media_type == VideoCodedH264.s:
            select_h264_level_from_coded_flow(inner)
        elif media_type == VideoCodedH265.s:
            select_h265_level_from_coded_flow(inner)
        elif media_type == VideoCodedJxsv.s:
            select_jxsv_level_from_coded_flow(inner)

    flow = NFlowValue()
    flow.set(inner)
    return flow


def build_audio_flow(
    params: dict[str, Any], source_id: str, config: dict[str, Any],
) -> Any:
    """Build an NFlowValue (audio) from extracted params.

    Creates NFlowAudioRawValue for PCM (L8/L16/L20/L24) or
    NFlowAudioCodedValue for coded audio (AM824, AAC, etc.).
    """
    from nmos.types.generated.nflow_audio_raw import NFlowAudioRawValue
    from nmos.types.generated.nflow_audio_coded import NFlowAudioCodedValue
    from nmos.types.generated.nflow import NFlowValue
    from nmos.enums import EnumRegistry

    media_type = str(params.get(CapFormatMediaType.s, AudioRawL24.s))

    # Determine raw vs coded: PCM types (L8/L16/L20/L24) are raw, everything else coded
    _RAW_AUDIO = {AudioRawL8.s, AudioRawL16.s, AudioRawL20.s, AudioRawL24.s}
    is_coded = media_type not in _RAW_AUDIO

    inner: Any
    if is_coded:
        inner = NFlowAudioCodedValue()
    else:
        inner = NFlowAudioRawValue()
    inner.set_to_default()
    inner.Format.value = EnumRegistry.get(FormatAudio.s)

    mt_enum = EnumRegistry.get(media_type)
    if mt_enum is not None:
        inner.MediaType.value = mt_enum

    inner.FlowCore.SourceId.value = source_id

    _set_flow_core(inner.FlowCore, params, config)

    # Audio-specific: sample rate
    sample_rate = params.get(CapFormatSampleRate.s)
    if sample_rate is not None and isinstance(sample_rate, Fraction):
        _set_rational(inner.SampleRate, sample_rate)

    if is_coded:
        # Coded audio properties
        profile = params.get(CapFormatProfile.s)
        level = params.get(CapFormatLevel.s)
        bit_rate = params.get(CapFormatBitRate.s)
        cbr = params.get(CapFormatConstantBitRate.s)

        if "am824" in media_type.lower():
            # AM824 has no profile/level/bitrate — zero out
            # Profile, Level, Bitrate, ConstantBitrate
            inner.Profile.set_to_zero()
            inner.Level.set_to_zero()
            inner.Bitrate.set_to_zero()
            inner.ConstantBitrate.set_to_zero()
        else:
            # AAC and other codecs: set profile/level/bitrate from params
            if profile is not None:
                inner.Profile.value = EnumRegistry.get(str(profile))
            if level is not None:
                inner.Level.value = EnumRegistry.get(str(level))
            if bit_rate is not None:
                inner.Bitrate.value = int(bit_rate)
            if cbr is not None:
                inner.ConstantBitrate.value = bool(cbr)
    else:
        # Raw audio: bit depth
        bit_depth = params.get(CapFormatComponentDepth.s) or \
                    params.get(CapFormatSampleDepth.s)
        if bit_depth is not None:
            inner.BitDepth.value = int(bit_depth)

    flow = NFlowValue()
    flow.set(inner)
    return flow


def build_data_flow(
    params: dict[str, Any], source_id: str, config: dict[str, Any],
) -> Any:
    """Build an NFlowValue (data) from extracted params."""
    from nmos.types.generated.nflow_data import NFlowDataValue
    from nmos.types.generated.nflow import NFlowValue
    from nmos.enums import EnumRegistry

    inner = NFlowDataValue()
    inner.set_to_default()
    inner.Format.value = EnumRegistry.get(FormatData.s)

    # Set media_type from the extracted params when available — mirrors
    # build_video_flow / build_audio_flow / build_mux_flow. Required for
    # data flows like USB (application/usb) to satisfy IS-04 FL1.
    mt = params.get(CapFormatMediaType.s)
    if mt is not None:
        mt_enum = EnumRegistry.get(str(mt))
        if mt_enum is not None:
            inner.MediaType.value = mt_enum

    inner.FlowCore.SourceId.value = source_id

    _set_flow_core(inner.FlowCore, params, config)

    flow = NFlowValue()
    flow.set(inner)
    return flow


def build_mux_flow(
    params: dict[str, Any], source_id: str,
    parent_flow_ids: list[str], node: Any,
    config: dict[str, Any] | None = None,
) -> Any:
    """Build a mux NFlowValue with parent flow references.

    Sets label from config, derives grain_rate from the first sub-flow
    that has one (the mux inherits timing from its sub-flows).
    """
    from nmos.types.generated.nflow_mux import NFlowMuxValue
    from nmos.types.generated.nflow import NFlowValue
    from nmos.enums import EnumRegistry

    inner = NFlowMuxValue()
    inner.set_to_default()
    inner.Format.value = EnumRegistry.get(FormatMux.s)

    # Set media_type from params or default
    mt = params.get(CapFormatMediaType.s, MuxMpeg2TS.s)
    mt_enum = EnumRegistry.get(str(mt))
    if mt_enum is not None:
        inner.MediaType.value = mt_enum

    inner.FlowCore.SourceId.value = source_id

    # Set label from config (same as other flow types via _set_flow_core)
    if config:
        label = config.get("label", "")
        if label:
            inner.FlowCore.ResourceCore.Label.value = label

    # Set parent flow IDs and inherit grain_rate from first sub-flow
    if parent_flow_ids:
        from nmos.node import _get_flow_core
        dynamic_ids = []
        for static_id in parent_flow_ids:
            flow = node.flows.get(static_id)
            if flow is not None:
                fc = _get_flow_core(flow)
                dynamic_ids.append(fc.ResourceCore.Id.value)
                # Inherit grain_rate from first sub-flow that has one
                if not inner.FlowCore.GrainRate.defined and fc.GrainRate.defined:
                    inner.FlowCore.GrainRate.set_value(fc.GrainRate.value.clone())
        inner.FlowCore.Parents.value = dynamic_ids

    flow = NFlowValue()
    flow.set(inner)
    return flow


def _set_flow_core(flow_core: Any, params: dict[str, Any], config: dict[str, Any]) -> None:
    """Set common flow core fields."""
    label = config.get("label", "")
    if label:
        flow_core.ResourceCore.Label.value = label

    grain_rate = params.get(CapFormatGrainRate.s)
    if grain_rate is not None and isinstance(grain_rate, Fraction):
        _set_rational(flow_core.GrainRate, grain_rate)

    # For audio flows: sample_rate maps to GrainRate (GrainRate is used for both)
    if grain_rate is None:
        sample_rate = params.get(CapFormatSampleRate.s)
        if sample_rate is not None and isinstance(sample_rate, Fraction):
            _set_rational(flow_core.GrainRate, sample_rate)


def _set_video_fields(inner: Any, params: dict[str, Any]) -> None:
    """Set video-specific fields (width, height, depth, colorspace, components)."""
    from nmos.enums import EnumRegistry

    width = params.get(CapFormatFrameWidth.s)
    if width is not None:
        inner.FrameWidth.value = int(width)

    height = params.get(CapFormatFrameHeight.s)
    if height is not None:
        inner.FrameHeight.value = int(height)

    colorspace = params.get(CapFormatColorspace.s)
    cs_enum = EnumRegistry.get(str(colorspace)) if colorspace is not None else None
    # Always set Colorspace — default to BT709
    inner.Colorspace.value = cs_enum if cs_enum is not None else EnumRegistry.get(BT709.s)

    depth = params.get(CapFormatComponentDepth.s)
    if depth is not None:
        depth_int = int(depth)
        # Build components (Y, Cb, Cr for 4:2:2)
        w = int(width) if width else 1920
        h = int(height) if height else 1080
        _set_video_components(inner, w, h, depth_int, params)


def _set_video_components(
    inner: Any, width: int, height: int, depth: int,
    params: dict[str, Any],
) -> None:
    """Set video components based on sampling and dimensions."""
    from nmos.enums import EnumRegistry

    sampling = str(params.get(CapFormatColorSampling.s, SamplingYCbCr_422.s))

    if hasattr(inner, 'Components'):
        from nmos.types.generated.nvideo_component import NVideoComponentValue

        def _make_comp(name: str, w: int, h: int, d: int) -> NVideoComponentValue:
            c = NVideoComponentValue()
            c.Name.value = EnumRegistry.get(name)
            c.Width.value = w
            c.Height.value = h
            c.BitDepth.value = d
            return c

        components: list[NVideoComponentValue] = []
        if sampling.startswith("RGB"):
            components = [
                _make_comp(R.s, width, height, depth),
                _make_comp(G.s, width, height, depth),
                _make_comp(B.s, width, height, depth),
            ]
        elif "4:4:4" in sampling:
            components = [
                _make_comp(Y.s, width, height, depth),
                _make_comp(Cb.s, width, height, depth),
                _make_comp(Cr.s, width, height, depth),
            ]
        elif "4:2:0" in sampling:
            components = [
                _make_comp(Y.s, width, height, depth),
                _make_comp(Cb.s, width // 2, height // 2, depth),
                _make_comp(Cr.s, width // 2, height // 2, depth),
            ]
        else:
            # Default: YCbCr-4:2:2
            components = [
                _make_comp(Y.s, width, height, depth),
                _make_comp(Cb.s, width // 2, height, depth),
                _make_comp(Cr.s, width // 2, height, depth),
            ]

        inner.Components._defined = True
        inner.Components._value._inner = components


# ---------------------------------------------------------------------------
# Sender / Receiver builders
# ---------------------------------------------------------------------------

def build_sender(config: dict[str, Any], flow_id: str, caps: Any) -> Any:
    """Build an NSenderValue from config."""
    from nmos.types.generated.nsender import NSenderValue
    from nmos.enums import EnumRegistry

    sender = NSenderValue()
    sender.set_to_default()

    label = config.get("label", "")
    if label:
        sender.ResourceCore.Label.value = label

    description = config.get("description", "")
    if description:
        sender.ResourceCore.Description.value = description

    format_enum = EnumRegistry.get(config["format"])
    if format_enum is not None:
        sender.Format.value = format_enum

    transport_enum = EnumRegistry.get(config["transport"])
    if transport_enum is not None:
        sender.Transport.value = transport_enum

    sender.FlowId.value = flow_id

    # Natural group index (used to group related senders, e.g., video + audio)
    ng = config.get("natural_group_index")
    if ng is not None:
        sender.NaturalGroupIndex.value = ng

    # Attach capabilities from the constraint sets
    _attach_sender_caps(sender, config.get("constraint_sets", []))

    return sender


def _attach_sender_caps(sender: Any, constraint_sets: list[dict[str, Any]]) -> None:
    """Build NSenderCapabilitiesValue from constraint_sets and attach to sender."""
    try:
        from nmos.types.generated.nsender_capabilities import NSenderCapabilitiesValue
        from nmos.node import _nmos_version_now

        caps_val = NSenderCapabilitiesValue()
        caps_val.Version.value = _nmos_version_now()

        # Build constraint set array from the JSON constraint sets
        # The constraint_sets are in NMOS BCP-004-01 JSON format
        # Store them for the Caps to encode
        if constraint_sets:
            _populate_constraint_sets(caps_val.ConstraintSets, constraint_sets)

        sender.Caps._defined = True
        sender.Caps._value = caps_val

    except ImportError:
        pass


def _populate_constraint_sets(array_field: Any, constraint_sets: list[dict[str, Any]]) -> None:
    """Populate an NArrayOfConstraintSet from JSON constraint_set dicts.

    Enforces label consistency before decoding: if any constraint set has
    a urn:x-nmos:cap:meta:label, all must have one. Auto-generates missing
    labels from media_type. This applies regardless of how the constraint
    sets were produced (config JSON, IS-11 operations, programmatic).
    """
    from nmos.node.config.pipelines import _enforce_label_consistency
    _enforce_label_consistency(constraint_sets)

    try:
        from nmos.types.generated.nconstraint_set import NConstraintSetValue

        items = []
        for cs_dict in constraint_sets:
            csv = NConstraintSetValue()
            csv.decode(JsonEngine(), cs_dict)
            items.append(csv)

        array_field._defined = True
        array_field._value._inner = items

    except (ImportError, AttributeError, ValueError) as exc:
        # If decode fails, leave constraint_sets empty but still defined
        import logging
        logging.warning("Failed to populate constraint sets: %s", exc)
        array_field._defined = True
        array_field._value._inner = []


def build_receiver(config: dict[str, Any], caps: Any) -> Any:
    """Build an NReceiverValue from config."""
    from nmos.types.generated.nreceiver_video import NReceiverVideoValue
    from nmos.types.generated.nreceiver_audio import NReceiverAudioValue
    from nmos.types.generated.nreceiver_data import NReceiverDataValue
    from nmos.types.generated.nreceiver_mux import NReceiverMuxValue
    from nmos.types.generated.nreceiver import NReceiverValue
    from nmos.enums import EnumRegistry

    format_urn = config["format"]

    inner: Any
    if "video" in format_urn:
        inner = NReceiverVideoValue()
    elif "audio" in format_urn:
        inner = NReceiverAudioValue()
    elif "mux" in format_urn:
        inner = NReceiverMuxValue()
    else:
        inner = NReceiverDataValue()

    inner.set_to_default()

    # Set the format field — each receiver variant requires this
    inner.Format.value = EnumRegistry.get(format_urn)

    label = config.get("label", "")
    if label:
        inner.ReceiverCore.ResourceCore.Label.value = label

    description = config.get("description", "")
    if description:
        inner.ReceiverCore.ResourceCore.Description.value = description

    transport_enum = EnumRegistry.get(config["transport"])
    if transport_enum is not None:
        inner.ReceiverCore.Transport.value = transport_enum

    # Natural group index
    ng = config.get("natural_group_index")
    if ng is not None:
        inner.ReceiverCore.NaturalGroupIndex.value = ng

    # Attach capabilities
    _attach_receiver_caps(inner, config.get("constraint_sets", []))

    receiver = NReceiverValue()
    receiver.set(inner)
    return receiver


def _attach_receiver_caps(inner: Any, constraint_sets: list[dict[str, Any]]) -> None:
    """Build receiver capabilities and attach to the inner receiver value."""
    try:
        from nmos.node import _nmos_version_now
        from nmos.enums import EnumRegistry

        caps = inner.Caps
        caps._defined = True
        caps._value.Version.value = _nmos_version_now()

        # Set MediaTypes from the union of every trunk constraint set's
        # media_type enum.  A "trunk" constraint set is one without
        # urn:x-matrox:cap:meta:format (i.e. it describes the top-level
        # stream, not a sub-flow layer).  A multi-codec receiver must list
        # every media_type it accepts (e.g. H264 + H265), not just the
        # native's — this field is the receiver's media_types gate.
        if hasattr(caps._value, 'MediaTypes'):
            media_types: list[str] = []
            for cs in constraint_sets:
                # Skip sub-flow (layer) constraint sets
                if CapMetaFormat.s in cs:
                    continue
                mt_cap = cs.get(CapFormatMediaType.s)
                if mt_cap and "enum" in mt_cap:
                    for v in mt_cap["enum"]:
                        s = str(v)
                        if s not in media_types:
                            media_types.append(s)
            if not media_types:
                raise ValueError(
                    "Receiver constraint_sets must contain at least one "
                    "trunk constraint set with urn:x-nmos:cap:format:media_type"
                )
            caps._value.MediaTypes._defined = True
            caps._value.MediaTypes._inner = [
                EnumRegistry.get(mt) for mt in media_types
            ]

        if constraint_sets:
            _populate_constraint_sets(caps._value.ConstraintSets, constraint_sets)

    except ImportError:
        pass
