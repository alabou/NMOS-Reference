# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Pipeline templates — define HOW to construct NMOS resource graphs.

Four pipeline topologies:
1. SIMPLE: Source → Flow → Sender/Receiver
2. RAW_CODED_PAIR: Source → (raw Flow + coded Flow linked) → Sender
3. MUX: (sub-Sources + sub-Flows) → Mux Source + Mux Flow → Sender
4. MUX_DATA: Same as MUX but includes data layer

These describe the resource topology, not the capability values.
"""

from __future__ import annotations

import enum
from typing import Any, cast

from nmos.enums import (
    CapFormatMediaType, CapMetaFormat, CapMetaLayer, CapTransportPrivacy,
)

from nmos.node.config.extract import (
    extract_params_from_capset,
    build_video_source,
    build_audio_source,
    build_data_source,
    build_mux_source,
    build_video_flow,
    build_audio_flow,
    build_data_flow,
    build_mux_flow,
    build_sender,
    build_receiver,
)


class PipelineType(enum.Enum):
    SIMPLE = "simple"
    RAW_CODED_PAIR = "raw_coded_pair"
    MUX = "mux"
    MUX_DATA = "mux_data"


def select_pipeline(format_urn: str, caps: Any) -> PipelineType:
    """Select pipeline type based on format and capabilities.

    - Mux format → MUX or MUX_DATA
    - Video with both raw and coded media_types → RAW_CODED_PAIR
    - Otherwise → SIMPLE
    """
    if "mux" in format_urn:
        # Check for data layers
        for capset in caps.capsets:
            if capset.format is None:  # trunk
                from caps.MatroxCCF import CapFormatDataLayers
                dl = capset.caps.get(CapFormatDataLayers)
                if dl and dl.value.enumerated:
                    count = next(iter(dl.value.enumerated))
                    if count and int(count) > 0:
                        return PipelineType.MUX_DATA
        return PipelineType.MUX

    # Check for raw + coded pair
    if "video" in format_urn:
        media_types: set[str] = set()
        for capset in caps.capsets:
            if capset.format is None:  # trunk capsets only
                from caps.MatroxCCF import CapFormatMediaType
                mt = capset.caps.get(CapFormatMediaType)
                if mt and mt.value.enumerated:
                    media_types.update(str(v) for v in mt.value.enumerated)

        from nmos.enums import VideoRaw
        has_raw = any("raw" in mt for mt in media_types)
        has_coded = any(
            mt not in (VideoRaw.s,) and "video/" in mt
            for mt in media_types
        )
        if has_raw and has_coded:
            return PipelineType.RAW_CODED_PAIR

    return PipelineType.SIMPLE


def build_pipeline(
    node: Any,
    config: dict[str, Any],
    caps: Any,
    pipeline_type: PipelineType,
    is_sender: bool,
    verbose: bool = False,
    linked_receiver_id: str | None = None,
    linked_receiver_is_mux: bool = False,
) -> str:
    """Build the complete resource pipeline on the node.

    Args:
        linked_receiver_id: Dynamic UUID of the receiver this sender's
            source(s) should link to via SourceCore.ReceiverId. None if
            no link. Resolved by ConfigBuilder from linked_receiver_group.
        linked_receiver_is_mux: True if the linked receiver is a mux receiver.
            When True, SourceCore.Layer is set (demux case). When False,
            Layer is not set (independent 1:1 link).

    Returns the static ID of the sender or receiver.
    """
    if not is_sender:
        return _build_receiver_pipeline(node, config, caps, verbose)

    if pipeline_type == PipelineType.SIMPLE:
        return _build_simple_sender(node, config, caps, verbose, linked_receiver_id=linked_receiver_id, linked_receiver_is_mux=linked_receiver_is_mux)
    elif pipeline_type == PipelineType.RAW_CODED_PAIR:
        return _build_raw_coded_sender(node, config, caps, verbose, linked_receiver_id=linked_receiver_id, linked_receiver_is_mux=linked_receiver_is_mux)
    elif pipeline_type in (PipelineType.MUX, PipelineType.MUX_DATA):
        return _build_mux_sender(node, config, caps, pipeline_type, verbose, linked_receiver_id=linked_receiver_id)
    else:
        raise ValueError(f"unknown pipeline type: {pipeline_type}")


# ---------------------------------------------------------------------------
# Simple pipeline: Source → Flow → Sender
# ---------------------------------------------------------------------------

def _build_simple_sender(
    node: Any, config: dict[str, Any], caps: Any, verbose: bool,
    linked_receiver_id: str | None = None,
    linked_receiver_is_mux: bool = False,
) -> str:
    """Build: Source → Flow → Sender."""
    format_urn = config["format"]

    # Select operating point (highest preference trunk CapSet)
    operating_capset = _select_operating_point(caps)
    params = extract_params_from_capset(operating_capset)

    # Build source (auto-generated from sender config + constraint params)
    if "video" in format_urn:
        source = build_video_source(params, config)
    elif "audio" in format_urn:
        source = build_audio_source(params, config)
    else:
        source = build_data_source(params, config)

    source_static = node.add_source(source)

    # Link source to receiver if specified (SourceCore.ReceiverId + Layer)
    if linked_receiver_id is not None:
        from nmos.node import _get_source_core
        sc = _get_source_core(source)
        sc.ReceiverId.value = linked_receiver_id
        # Layer is only set when linking to a mux receiver (demux case).
        # For independent receivers (Pattern B), no Layer — the 1:1 link
        # doesn't need a sub-stream identifier.
        if linked_receiver_is_mux:
            sc.Layer.value = 0

    # Build flow
    from nmos.node import _get_source_core
    source_dynamic = _get_source_core(source).ResourceCore.Id.value

    if "video" in format_urn:
        flow = build_video_flow(params, source_dynamic, config)
    elif "audio" in format_urn:
        flow = build_audio_flow(params, source_dynamic, config)
    else:
        flow = build_data_flow(params, source_dynamic, config)

    flow_static = node.add_flow(flow)

    # Build sender
    from nmos.node import _get_flow_core
    flow_dynamic = _get_flow_core(flow).ResourceCore.Id.value

    # Add privacy capability to constraint_sets JSON before building
    _add_privacy_to_constraint_sets(config.get("constraint_sets", []), node.privacy_enabled)

    sender = build_sender(config, flow_dynamic, caps)

    # IS-04 sender Privacy attribute — senderValue.Privacy.SetValue(HasTransportPrivacyEncryption)
    if node.privacy_enabled and hasattr(sender, 'Privacy'):
        sender.Privacy.value = True

    sender_static = node.add_sender(sender)

    # Cache the CCF Caps — compatibility.py reads these directly (no conversion needed)
    node.sender_ccf_caps[sender_static] = caps

    return cast(str, sender_static)


# ---------------------------------------------------------------------------
# Raw+Coded pair: Source → (raw Flow + coded Flow) → Sender
# ---------------------------------------------------------------------------

def _build_raw_coded_sender(
    node: Any, config: dict[str, Any], caps: Any, verbose: bool,
    linked_receiver_id: str | None = None,
    linked_receiver_is_mux: bool = False,
) -> str:
    """Build: Source → raw Flow + coded Flow (linked) → Sender.

    The native (highest preference) determines which is "primary".
    """
    operating_capset = _select_operating_point(caps)
    params = extract_params_from_capset(operating_capset)

    # Determine if native is raw or coded
    media_type = params.get(CapFormatMediaType.s, "")
    native_is_raw = "raw" in str(media_type)

    # Build source (auto-generated from sender config)
    source = build_video_source(params, config)
    source_static = node.add_source(source)

    # Link source to receiver if specified
    if linked_receiver_id is not None:
        from nmos.node import _get_source_core
        _get_source_core(source).ReceiverId.value = linked_receiver_id
        if linked_receiver_is_mux:
            _get_source_core(source).Layer.value = 0

    from nmos.node import _get_source_core
    source_dynamic = _get_source_core(source).ResourceCore.Id.value

    # Find raw and coded capsets
    raw_capset = None
    coded_capset = None
    for capset in caps.capsets:
        if capset.format is not None:
            continue  # skip layer capsets
        mt_cap = capset.caps.get(CapFormatMediaType.s)
        if mt_cap and mt_cap.value.enumerated:
            mt_str = str(next(iter(mt_cap.value.enumerated)))
            if "raw" in mt_str:
                raw_capset = capset
            elif raw_capset is None or coded_capset is None:
                if coded_capset is None:
                    coded_capset = capset

    # Build both flows
    if raw_capset is not None:
        raw_params = extract_params_from_capset(raw_capset)
        raw_flow = build_video_flow(raw_params, source_dynamic, config)
    else:
        raw_flow = build_video_flow(params, source_dynamic, config)

    if coded_capset is not None:
        coded_params = extract_params_from_capset(coded_capset)
        coded_flow = build_video_flow(coded_params, source_dynamic, config)
    else:
        coded_flow = build_video_flow(params, source_dynamic, config)

    # Add flows (raw + coded linked)
    # For now, add them separately and link
    if native_is_raw:
        node.add_flow(raw_flow)
        node.add_flow(coded_flow)
    else:
        node.add_flow(coded_flow)
        node.add_flow(raw_flow)

    # Use the native flow for the sender
    from nmos.node import _get_flow_core
    native_flow = raw_flow if native_is_raw else coded_flow
    flow_dynamic = _get_flow_core(native_flow).ResourceCore.Id.value

    _add_privacy_to_constraint_sets(config.get("constraint_sets", []), node.privacy_enabled)

    sender = build_sender(config, flow_dynamic, caps)

    if node.privacy_enabled and hasattr(sender, 'Privacy'):
        sender.Privacy.value = True

    sender_static = node.add_sender(sender)

    # Cache the CCF Caps — compatibility.py reads these directly (no conversion needed)
    node.sender_ccf_caps[sender_static] = caps

    return cast(str, sender_static)


# ---------------------------------------------------------------------------
# Mux pipeline: sub-Sources + sub-Flows → Mux Source + Mux Flow → Sender
# ---------------------------------------------------------------------------

def _build_mux_sender(
    node: Any, config: dict[str, Any], caps: Any,
    pipeline_type: PipelineType, verbose: bool,
    linked_receiver_id: str | None = None,
) -> str:
    """Build mux hierarchy from hierarchical constraint sets."""
    from caps.MatroxCCF import FormatVideo, FormatAudio, FormatData

    # Collect leaf capsets by format
    leaf_capsets: dict[str, list[Any]] = {}
    trunk_capset = None
    for capset in caps.capsets:
        if capset.format is None:
            if trunk_capset is None or capset.preference > trunk_capset.preference:
                trunk_capset = capset
        else:
            fmt = capset.format
            if fmt not in leaf_capsets:
                leaf_capsets[fmt] = []
            leaf_capsets[fmt].append(capset)

    # Build sub-sources and sub-flows
    # Layer is set on each sub-source and sub-flow.
    sub_source_ids: list[str] = []
    sub_flow_ids: list[str] = []
    layer_counter: dict[Any, int] = {}  # format → next layer index

    from nmos.node import _get_source_core, _get_flow_core

    # Count sub-flows per format from config's "sub_flows" or default to 1.
    # The config determines this explicitly (e.g., maxAudioLayers=2).
    sub_flow_counts: dict[Any, int] = {}
    for cs in config.get("constraint_sets", []):
        cs_fmt = cs.get(CapMetaFormat.s)
        cs_layer = cs.get(CapMetaLayer.s)
        if cs_fmt is not None and cs_layer is not None:
            current = sub_flow_counts.get(cs_fmt, 0)
            sub_flow_counts[cs_fmt] = max(current, int(cs_layer) + 1)

    for fmt, capsets in leaf_capsets.items():
        # Pick highest preference leaf for operating point
        capsets.sort(key=lambda cs: cs.preference, reverse=True)
        operating_leaf = capsets[0]
        params = extract_params_from_capset(operating_leaf)

        # Number of sub-flows: from config layer count, or 1
        fmt_str = str(fmt)
        n_layers = sub_flow_counts.get(fmt_str, 1)

        for layer in range(n_layers):
            sub_config = {"label": config.get("label", ""), "format": fmt}

            if fmt == FormatVideo:
                sub_src = build_video_source(params, sub_config)
                sub_src_static = node.add_source(sub_src)
                _get_source_core(sub_src).Layer.value = layer
                if linked_receiver_id is not None:
                    _get_source_core(sub_src).ReceiverId.value = linked_receiver_id
                sub_src_dynamic = _get_source_core(sub_src).ResourceCore.Id.value

                sub_flow = build_video_flow(params, sub_src_dynamic, sub_config)
                sub_flow_static = node.add_flow(sub_flow)
                _get_flow_core(sub_flow).Layer.value = layer

            elif fmt == FormatAudio:
                sub_src = build_audio_source(params, sub_config)
                sub_src_static = node.add_source(sub_src)
                _get_source_core(sub_src).Layer.value = layer
                if linked_receiver_id is not None:
                    _get_source_core(sub_src).ReceiverId.value = linked_receiver_id
                sub_src_dynamic = _get_source_core(sub_src).ResourceCore.Id.value

                sub_flow = build_audio_flow(params, sub_src_dynamic, sub_config)
                sub_flow_static = node.add_flow(sub_flow)
                _get_flow_core(sub_flow).Layer.value = layer

            elif fmt == FormatData:
                sub_src = build_data_source(params, sub_config)
                sub_src_static = node.add_source(sub_src)
                _get_source_core(sub_src).Layer.value = layer
                if linked_receiver_id is not None:
                    _get_source_core(sub_src).ReceiverId.value = linked_receiver_id
                sub_src_dynamic = _get_source_core(sub_src).ResourceCore.Id.value

                sub_flow = build_data_flow(params, sub_src_dynamic, sub_config)
                sub_flow_static = node.add_flow(sub_flow)
                _get_flow_core(sub_flow).Layer.value = layer

            else:
                continue

            sub_source_ids.append(sub_src_static)
            sub_flow_ids.append(sub_flow_static)

    # Build mux source (parents = sub-sources)
    mux_source = build_mux_source(config, sub_source_ids, node)
    mux_source_static = node.add_source(mux_source)

    # Build mux flow (parents = sub-flows)
    from nmos.node import _get_source_core
    mux_src_dynamic = _get_source_core(mux_source).ResourceCore.Id.value

    # Get media_type from first constraint_set
    mux_params: dict[str, Any] = {}
    cs_list = config.get("constraint_sets", [])
    if cs_list:
        mt_cap = cs_list[0].get(CapFormatMediaType.s, {})
        mt_enum = mt_cap.get("enum", [])
        if mt_enum:
            mux_params[CapFormatMediaType.s] = mt_enum[0]
    mux_flow = build_mux_flow(mux_params, mux_src_dynamic, sub_flow_ids, node, config)
    mux_flow_static = node.add_flow(mux_flow)

    # Set layer counts on mux flow — these are metadata about how many layers
    # participate in the mux transmission.  WithFlowLayers() sets these at
    # config time.  Without these, IS-11 constraint matching fails because
    # get_flow_to_caps() can't extract audio_layers/data_layers from the flow.
    mux_inner = mux_flow.get() if hasattr(mux_flow, 'get') else mux_flow
    video_count = sub_flow_counts.get(str(FormatVideo), 0)
    audio_count = sub_flow_counts.get(str(FormatAudio), 0)
    data_count = sub_flow_counts.get(str(FormatData), 0)
    mux_inner.VideoLayers.value = video_count
    mux_inner.AudioLayers.value = audio_count
    mux_inner.DataLayers.value = data_count

    # Build sender
    from nmos.node import _get_flow_core
    mux_flow_dynamic = _get_flow_core(mux_flow).ResourceCore.Id.value

    _add_privacy_to_constraint_sets(config.get("constraint_sets", []), node.privacy_enabled)

    sender = build_sender(config, mux_flow_dynamic, caps)

    if node.privacy_enabled and hasattr(sender, 'Privacy'):
        sender.Privacy.value = True

    sender_static = node.add_sender(sender)

    # Cache the CCF Caps — compatibility.py reads these directly (no conversion needed)
    node.sender_ccf_caps[sender_static] = caps

    return cast(str, sender_static)


# ---------------------------------------------------------------------------
# Receiver pipeline
# ---------------------------------------------------------------------------

def _build_receiver_pipeline(
    node: Any, config: dict[str, Any], caps: Any, verbose: bool,
) -> str:
    """Build a receiver from config."""
    # Add privacy capability to constraint_sets JSON before decoding
    _add_privacy_to_constraint_sets(config.get("constraint_sets", []), node.privacy_enabled)

    receiver = build_receiver(config, caps)

    receiver_static = node.add_receiver(receiver)

    # Cache the CCF Caps — compatibility.py reads these directly (no conversion needed)
    node.receiver_ccf_caps[receiver_static] = caps

    return cast(str, receiver_static)


def _add_privacy_to_constraint_sets(
    constraint_sets: list[dict[str, Any]], privacy_enabled: bool,
) -> None:
    """Add urn:x-nmos:cap:transport:privacy to each JSON constraint_set dict.

    AddIpmxSenderConstraints / AddIpmxReceiverConstraints add
    CapTransportPrivacy = NConstraintBool{Enum: [{HasTransportPrivacyEncryption}]}
    Always present — reflects the node's privacy state (true or false).
    """
    for cs in constraint_sets:
        cs[CapTransportPrivacy.s] = {"enum": [privacy_enabled]}


def _enforce_label_consistency(constraint_sets: list[dict[str, Any]]) -> None:
    """Ensure constraint sets either ALL have a label or NONE do.

    BCP-004-01 / IS-11 rule: urn:x-nmos:cap:meta:label must be present on
    all constraint sets or absent from all. If some have labels and some
    don't, auto-generate labels for the unlabeled ones from their media_type.

    Applies to both sender and receiver capabilities.
    """
    if not constraint_sets:
        return

    from caps.MatroxCCF import CapMetaLabel, CapFormatMediaType
    label_key = CapMetaLabel
    has_any_label = any(cs.get(label_key) for cs in constraint_sets)
    if not has_any_label:
        return  # None have labels — that's valid

    for cs in constraint_sets:
        if cs.get(label_key):
            continue  # Already has a label
        # Auto-generate from media_type
        mt_cap = cs.get(CapFormatMediaType, {})
        mt_enum = mt_cap.get("enum", [])
        if mt_enum:
            cs[label_key] = str(mt_enum[0])
        else:
            cs[label_key] = "Constraint Set"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _select_operating_point(caps: Any) -> Any:
    """Select the highest-preference trunk CapSet as operating point."""
    trunk_capsets = [cs for cs in caps.capsets if cs.format is None]
    if not trunk_capsets:
        return caps.capsets[0] if caps.capsets else None
    trunk_capsets.sort(key=lambda cs: cs.preference, reverse=True)
    return trunk_capsets[0]
