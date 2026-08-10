# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Type definitions auto-generated from Go source."""

from __future__ import annotations

from nmos.codegen.descriptors import MemberDesc, TypeDesc

nresource_core = TypeDesc(
    package="nmos",
    name="NResourceCore",
    is_embedded=True,
    members=[
        MemberDesc(name="Id", type_name="NString", json_key="id", assertion="CheckResourceIdString"),
        MemberDesc(name="StaticId", type_name="NString", json_key="-"),
        MemberDesc(name="Version", type_name="NTime", json_key="version"),
        MemberDesc(name="Label", type_name="NString", json_key="label"),
        MemberDesc(name="Description", type_name="NString", json_key="description"),
        MemberDesc(name="Tags", type_name="NTags", json_key="tags"),
    ],
)

nerror = TypeDesc(
    package="nmos",
    name="NError",
    members=[
        MemberDesc(name="Code", type_name="NInt", json_key="code", assertion="CheckErrorCode"),
        MemberDesc(name="Error", type_name="NString", json_key="error"),
        MemberDesc(name="Debug", type_name="NNullString", json_key="debug"),
    ],
)

nclock_internal = TypeDesc(
    package="nmos",
    name="NClockInternal",
    members=[
        MemberDesc(name="Name", type_name="NString", json_key="name", assertion="CheckClockNameString"),
        MemberDesc(name="RefType", type_name="NEnum", json_key="ref_type"),
    ],
)

nclock_ptp = TypeDesc(
    package="nmos",
    name="NClockPtp",
    members=[
        MemberDesc(name="Name", type_name="NString", json_key="name", assertion="CheckClockNameString"),
        MemberDesc(name="RefType", type_name="NEnum", json_key="ref_type"),
        MemberDesc(name="Traceable", type_name="NBool", json_key="traceable"),
        MemberDesc(name="Version", type_name="NEnum", json_key="version"),
        MemberDesc(name="Gmid", type_name="NString", json_key="gmid", assertion="CheckClockGmidString"),
        MemberDesc(name="Locked", type_name="NBool", json_key="locked"),
    ],
)

narray_of_clock_internal = TypeDesc(
    package="nmos",
    name="NArrayOfClockInternal",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NClockInternalValue]", json_key="-"),
    ],
)

narray_of_clock_ptp = TypeDesc(
    package="nmos",
    name="NArrayOfClockPtp",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NClockPtpValue]", json_key="-"),
    ],
)

nclock = TypeDesc(
    package="nmos",
    name="NClock",
    is_value=True,
    is_base=True,
    poly_types=['NClockInternal', 'NClockPtp'],
    members=[
        MemberDesc(name="value", type_name="object", json_key="-"),
    ],
)

narray_of_clock = TypeDesc(
    package="nmos",
    name="NArrayOfClock",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NClockValue]", json_key="-"),
    ],
)

nsender_ptr = TypeDesc(
    package="nmos",
    name="NSenderPtr",
    is_value=True,
    is_base=True,
    members=[
        MemberDesc(name="value", type_name="NSenderValue", json_key="-"),
    ],
)

nsender_ptrs = TypeDesc(
    package="nmos",
    name="NSenderPtrs",
    is_value=True,
    members=[
        MemberDesc(name="value", type_name="dict[NSenderValue, NSenderValue]", json_key="-"),
    ],
)

nsender = TypeDesc(
    package="nmos",
    name="NSender",
    members=[
        MemberDesc(name="ResourceCore", type_name="NResourceCore", embedded=True),
        MemberDesc(name="FlowId", type_name="NNullString", json_key="flow_id", default='None', assertion="CheckResourceIdNullableString"),
        MemberDesc(name="Transport", type_name="NEnum", json_key="transport", assertion="CheckTransport"),
        MemberDesc(name="DeviceId", type_name="NString", json_key="device_id", assertion="CheckResourceIdString"),
        MemberDesc(name="Device", type_name="NDevicePtr", json_key="-"),
        MemberDesc(name="ManifestHref", type_name="NUrl", json_key="manifest_href"),
        MemberDesc(name="InterfaceBindings", type_name="NArrayOfString", json_key="interface_bindings"),
        MemberDesc(name="Subscription", type_name="NSenderSubscription", json_key="subscription"),
        MemberDesc(name="Caps", type_name="NSenderCapabilities", json_key="caps", optional=True),
        MemberDesc(name="OnDemand", type_name="NBool", json_key="-"),
        MemberDesc(name="OnDemandExpiry", type_name="NTime", json_key="-"),
        MemberDesc(name="Format", type_name="NEnum", json_key="-", assertion="CheckFormat"),
        MemberDesc(name="NaturalGroupIndex", type_name="NInt", json_key="-", optional=True),
        MemberDesc(name="NaturalGroupRoleIndex", type_name="NInt", json_key="-", optional=True),
        MemberDesc(name="Inputs", type_name="NArrayOfString", json_key="-"),
        MemberDesc(name="CompatibilityStatus", type_name="NEnum", json_key="-", default='EnumRegistry.get("unconstrained")'),
        MemberDesc(name="Constraints", type_name="NSenderActiveConstraints", json_key="-"),
        # MergedConstraints / NormalizedConstraints removed — replaced by
        # Node.sender_ccf_merged / sender_ccf_normalized (cached CCF Caps objects)
        MemberDesc(name="Bitrate", type_name="NInt", json_key="bit_rate", optional=True),
        MemberDesc(name="SenderType", type_name="NEnum", json_key="st2110_21_sender_type", optional=True),
        MemberDesc(name="PacketTransmissionMode", type_name="NEnum", json_key="packet_transmission_mode", optional=True),
        MemberDesc(name="ParameterSetsTransportMode", type_name="NEnum", json_key="parameter_sets_transport_mode", optional=True),
        MemberDesc(name="ParameterSetsFlowMode", type_name="NEnum", json_key="parameter_sets_flow_mode", optional=True),
        MemberDesc(name="InfoBlock", type_name="NArrayOfInt", json_key="urn:x-matrox:info_block", optional=True),
        MemberDesc(name="HKEP", type_name="NBool", json_key="hkep", optional=True),
        MemberDesc(name="Privacy", type_name="NBool", json_key="privacy", optional=True),
        MemberDesc(name="Monitor", type_name="NSourcePtr", json_key="-"),
    ],
)

narray_of_sender = TypeDesc(
    package="nmos",
    name="NArrayOfSender",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NSenderValue]", json_key="-"),
    ],
)

nsender_subscription = TypeDesc(
    package="nmos",
    name="NSenderSubscription",
    members=[
        MemberDesc(name="ReceiverId", type_name="NNullString", json_key="receiver_id", default='None', assertion="CheckResourceIdNullableString"),
        MemberDesc(name="Active", type_name="NBool", json_key="active", default='False'),
    ],
)

nsender_capabilities = TypeDesc(
    package="nmos",
    name="NSenderCapabilities",
    members=[
        MemberDesc(name="Version", type_name="NTime", json_key="version", optional=True),
        MemberDesc(name="ConstraintSets", type_name="NArrayOfConstraintSet", json_key="constraint_sets", optional=True),
    ],
)

nconstraint_set = TypeDesc(
    package="nmos",
    name="NConstraintSet",
    members=[
        MemberDesc(name="MetaLabel", type_name="NString", json_key="urn:x-nmos:cap:meta:label", optional=True),
        MemberDesc(name="MetaFormat", type_name="NEnum", json_key="urn:x-matrox:cap:meta:format", optional=True),
        MemberDesc(name="MetaLayer", type_name="NInt", json_key="urn:x-matrox:cap:meta:layer", optional=True),
        MemberDesc(name="MetaLayerEnabled", type_name="NBool", json_key="urn:x-matrox:cap:meta:layer_enabled", optional=True),
        MemberDesc(name="MetaLayerCompatibilityGroups", type_name="NArrayOfInt", json_key="urn:x-matrox:cap:meta:layer_compatibility_groups", optional=True),
        MemberDesc(name="MetaEnabled", type_name="NBool", json_key="urn:x-nmos:cap:meta:enabled", optional=True, default='True'),
        MemberDesc(name="MetaPreference", type_name="NInt", json_key="urn:x-nmos:cap:meta:preference", optional=True, default='0', assertion="CheckConstraintSetPreference"),
        MemberDesc(name="MetaInfoBlock", type_name="NArrayOfInt", json_key="urn:x-matrox:cap:meta:info_block", optional=True),
        MemberDesc(name="Constraints", type_name="NConstraints", embedded=True, assertion="CheckConstraintsLength"),
    ],
)

narray_of_constraint_set = TypeDesc(
    package="nmos",
    name="NArrayOfConstraintSet",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NConstraintSetValue]", json_key="-"),
    ],
)

nreceiver_core = TypeDesc(
    package="nmos",
    name="NReceiverCore",
    is_embedded=True,
    members=[
        MemberDesc(name="ResourceCore", type_name="NResourceCore", embedded=True),
        MemberDesc(name="Transport", type_name="NEnum", json_key="transport", assertion="CheckTransport"),
        MemberDesc(name="DeviceId", type_name="NString", json_key="device_id", assertion="CheckResourceIdString"),
        MemberDesc(name="Device", type_name="NDevicePtr", json_key="-"),
        MemberDesc(name="Sources", type_name="NSourcePtrs", json_key="-"),
        MemberDesc(name="InterfaceBindings", type_name="NArrayOfString", json_key="interface_bindings"),
        MemberDesc(name="Subscription", type_name="NReceiverSubscription", json_key="subscription"),
        MemberDesc(name="OnDemand", type_name="NBool", json_key="-"),
        MemberDesc(name="OnDemandExpiry", type_name="NTime", json_key="-"),
        MemberDesc(name="NaturalGroupIndex", type_name="NInt", json_key="-", optional=True),
        MemberDesc(name="NaturalGroupRoleIndex", type_name="NInt", json_key="-", optional=True),
        MemberDesc(name="Outputs", type_name="NArrayOfString", json_key="-"),
        MemberDesc(name="CompatibilityStatus", type_name="NEnum", json_key="-", default='EnumRegistry.get("unknown")'),
        MemberDesc(name="Static", type_name="NBool", json_key="-"),
        MemberDesc(name="Monitor", type_name="NSourcePtr", json_key="-"),
    ],
)

nreceiver_video = TypeDesc(
    package="nmos",
    name="NReceiverVideo",
    members=[
        MemberDesc(name="ReceiverCore", type_name="NReceiverCore", embedded=True),
        MemberDesc(name="Format", type_name="NEnum", json_key="format", assertion="CheckFormat"),
        MemberDesc(name="Caps", type_name="NReceiverVideoCapabilities", json_key="caps"),
    ],
)

nreceiver_audio = TypeDesc(
    package="nmos",
    name="NReceiverAudio",
    members=[
        MemberDesc(name="ReceiverCore", type_name="NReceiverCore", embedded=True),
        MemberDesc(name="Format", type_name="NEnum", json_key="format", assertion="CheckFormat"),
        MemberDesc(name="Caps", type_name="NReceiverAudioCapabilities", json_key="caps"),
    ],
)

nreceiver_data = TypeDesc(
    package="nmos",
    name="NReceiverData",
    members=[
        MemberDesc(name="ReceiverCore", type_name="NReceiverCore", embedded=True),
        MemberDesc(name="Format", type_name="NEnum", json_key="format", assertion="CheckFormat"),
        MemberDesc(name="Caps", type_name="NReceiverDataCapabilities", json_key="caps"),
    ],
)

nreceiver_mux = TypeDesc(
    package="nmos",
    name="NReceiverMux",
    members=[
        MemberDesc(name="ReceiverCore", type_name="NReceiverCore", embedded=True),
        MemberDesc(name="Format", type_name="NEnum", json_key="format", assertion="CheckFormat"),
        MemberDesc(name="Caps", type_name="NReceiverMuxCapabilities", json_key="caps"),
    ],
)

nreceiver_ptr = TypeDesc(
    package="nmos",
    name="NReceiverPtr",
    is_value=True,
    is_base=True,
    members=[
        MemberDesc(name="value", type_name="NReceiverValue", json_key="-"),
    ],
)

nreceiver = TypeDesc(
    package="nmos",
    name="NReceiver",
    is_value=True,
    is_base=True,
    poly_types=['NReceiverVideo', 'NReceiverAudio', 'NReceiverData', 'NReceiverMux'],
    members=[
        MemberDesc(name="value", type_name="object", json_key="-"),
    ],
)

narray_of_receiver = TypeDesc(
    package="nmos",
    name="NArrayOfReceiver",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NReceiverValue]", json_key="-"),
    ],
)

nreceiver_subscription = TypeDesc(
    package="nmos",
    name="NReceiverSubscription",
    members=[
        MemberDesc(name="SenderId", type_name="NNullString", json_key="sender_id", default='None', assertion="CheckResourceIdNullableString"),
        MemberDesc(name="Active", type_name="NBool", json_key="active", default='False'),
    ],
)

nreceiver_video_capabilities = TypeDesc(
    package="nmos",
    name="NReceiverVideoCapabilities",
    members=[
        MemberDesc(name="MediaTypes", type_name="NArrayOfEnum", json_key="media_types", optional=True, assertion="CheckVideoMediaTypes"),
        MemberDesc(name="Version", type_name="NTime", json_key="version", optional=True),
        MemberDesc(name="ConstraintSets", type_name="NArrayOfConstraintSet", json_key="constraint_sets", optional=True),
    ],
)

nreceiver_audio_capabilities = TypeDesc(
    package="nmos",
    name="NReceiverAudioCapabilities",
    members=[
        MemberDesc(name="MediaTypes", type_name="NArrayOfEnum", json_key="media_types", optional=True, assertion="CheckAudioMediaTypes"),
        MemberDesc(name="Version", type_name="NTime", json_key="version", optional=True),
        MemberDesc(name="ConstraintSets", type_name="NArrayOfConstraintSet", json_key="constraint_sets", optional=True),
    ],
)

nreceiver_data_capabilities = TypeDesc(
    package="nmos",
    name="NReceiverDataCapabilities",
    members=[
        MemberDesc(name="MediaTypes", type_name="NArrayOfEnum", json_key="media_types", optional=True, assertion="CheckDataMediaTypes"),
        MemberDesc(name="EventTypes", type_name="NArrayOfEnum", json_key="event_types", optional=True, assertion="CheckDataEventTypes"),
        MemberDesc(name="Version", type_name="NTime", json_key="version", optional=True),
        MemberDesc(name="ConstraintSets", type_name="NArrayOfConstraintSet", json_key="constraint_sets", optional=True),
    ],
)

nreceiver_mux_capabilities = TypeDesc(
    package="nmos",
    name="NReceiverMuxCapabilities",
    members=[
        MemberDesc(name="MediaTypes", type_name="NArrayOfEnum", json_key="media_types", optional=True, assertion="CheckMuxMediaTypes"),
        MemberDesc(name="Version", type_name="NTime", json_key="version", optional=True),
        MemberDesc(name="ConstraintSets", type_name="NArrayOfConstraintSet", json_key="constraint_sets", optional=True),
    ],
)

nsource_core = TypeDesc(
    package="nmos",
    name="NSourceCore",
    is_embedded=True,
    members=[
        MemberDesc(name="ResourceCore", type_name="NResourceCore", embedded=True),
        MemberDesc(name="Caps", type_name="NSourceCapabilities", json_key="caps"),
        MemberDesc(name="ReceiverId", type_name="NNullString", json_key="urn:x-matrox:receiver_id", optional=True, default='None', assertion="CheckResourceIdNullableString"),
        MemberDesc(name="DeviceId", type_name="NString", json_key="device_id", assertion="CheckResourceIdString"),
        MemberDesc(name="Device", type_name="NDevicePtr", json_key="-"),
        MemberDesc(name="Parents", type_name="NArrayOfString", json_key="parents", assertion="CheckArrayOfResourceIdString"),
        MemberDesc(name="Children", type_name="NSourcePtrs", json_key="-"),
        MemberDesc(name="Flows", type_name="NFlowPtrs", json_key="-"),
        MemberDesc(name="ClockName", type_name="NNullString", json_key="clock_name", assertion="CheckClockNameNullableString"),
        MemberDesc(name="GrainRate", type_name="NRational", json_key="grain_rate", optional=True),
        MemberDesc(name="Layer", type_name="NInt", json_key="urn:x-matrox:layer", optional=True),
        MemberDesc(name="SynchronousMedia", type_name="NBool", json_key="urn:x-matrox:synchronous_media", optional=True),
    ],
)

nsource_video = TypeDesc(
    package="nmos",
    name="NSourceVideo",
    members=[
        MemberDesc(name="SourceCore", type_name="NSourceCore", embedded=True),
        MemberDesc(name="Format", type_name="NEnum", json_key="format", assertion="CheckFormat"),
    ],
)

nsource_audio = TypeDesc(
    package="nmos",
    name="NSourceAudio",
    members=[
        MemberDesc(name="SourceCore", type_name="NSourceCore", embedded=True),
        MemberDesc(name="Format", type_name="NEnum", json_key="format", assertion="CheckFormat"),
        MemberDesc(name="Channels", type_name="NArrayOfAudioChannel", json_key="channels", assertion="CheckAudioChannels"),
    ],
)

nmonitor_state = TypeDesc(
    package="nmos",
    name="NMonitorState",
    members=[
        MemberDesc(name="MonitorOverallStatus", type_name="NInt", json_key="overall_status", optional=True),
        # ``overall_message``, not ``overall_status_message``. The IS-04
        # binding in "NMOS With Status Reporting.md" names this attribute
        # ``overall_message`` — in the prose ("It MAY have an
        # ``overall_message`` attribute"), in the BCP-008 property mapping
        # table (overallStatusMessage → overall_message) and in the
        # specification's own example JSON. Publishing
        # ``overall_status_message`` meant a controller implementing the
        # published binding looked for ``overall_message``, found nothing,
        # and silently showed no status message at all — losing exactly the
        # diagnostic text BCP-008 recommends populating ("receiver socket
        # timeout", "Previously: ...").
        MemberDesc(name="MonitorOverallStatusMessage", type_name="NString", json_key="overall_message", optional=True),
        MemberDesc(name="MonitorLinkStatus", type_name="NInt", json_key="link_status", optional=True),
        MemberDesc(name="MonitorSynchronizationStatus", type_name="NInt", json_key="synchronization_status", optional=True),
        MemberDesc(name="MonitorTransmissionStatus", type_name="NInt", json_key="transmission_status", optional=True),
        MemberDesc(name="MonitorConnectionStatus", type_name="NInt", json_key="connection_status", optional=True),
        MemberDesc(name="MonitorEssenceStatus", type_name="NInt", json_key="essence_status", optional=True),
        MemberDesc(name="MonitorStreamStatus", type_name="NInt", json_key="stream_status", optional=True),
        MemberDesc(name="MonitorLinkStatusCounter", type_name="NInt", json_key="link_counter", optional=True),
        MemberDesc(name="MonitorSynchronizationStatusCounter", type_name="NInt", json_key="synchronization_counter", optional=True),
        MemberDesc(name="MonitorTransmissionStatusCounter", type_name="NInt", json_key="transmission_counter", optional=True),
        MemberDesc(name="MonitorConnectionStatusCounter", type_name="NInt", json_key="connection_counter", optional=True),
        MemberDesc(name="MonitorEssenceStatusCounter", type_name="NInt", json_key="essence_counter", optional=True),
        MemberDesc(name="MonitorStreamStatusCounter", type_name="NInt", json_key="stream_counter", optional=True),
    ],
)

nsource_data = TypeDesc(
    package="nmos",
    name="NSourceData",
    members=[
        MemberDesc(name="SourceCore", type_name="NSourceCore", embedded=True),
        MemberDesc(name="Format", type_name="NEnum", json_key="format", assertion="CheckFormat"),
        MemberDesc(name="EventType", type_name="NString", json_key="event_type", optional=True),
        MemberDesc(name="MonitorType", type_name="NString", json_key="monitor_type", optional=True),
        MemberDesc(name="MonitorSiblingId", type_name="NString", json_key="monitor_sibling_id", optional=True, assertion="CheckResourceIdString"),
        MemberDesc(name="MonitorAutoResetCounters", type_name="NBool", json_key="monitor_auto_reset_counters", optional=True),
        MemberDesc(name="MonitorStatusReportingDelay", type_name="NInt", json_key="monitor_reporting_delay", optional=True),
        MemberDesc(name="MonitorState", type_name="NMonitorState", json_key="monitor_state", optional=True),
    ],
)

nsource_mux = TypeDesc(
    package="nmos",
    name="NSourceMux",
    members=[
        MemberDesc(name="SourceCore", type_name="NSourceCore", embedded=True),
        MemberDesc(name="Format", type_name="NEnum", json_key="format", assertion="CheckFormat"),
    ],
)

nsource_ptr = TypeDesc(
    package="nmos",
    name="NSourcePtr",
    is_value=True,
    is_base=True,
    members=[
        MemberDesc(name="value", type_name="NSourceValue", json_key="-"),
    ],
)

nsource_ptrs = TypeDesc(
    package="nmos",
    name="NSourcePtrs",
    is_value=True,
    members=[
        MemberDesc(name="value", type_name="dict[NSourceValue, NSourceValue]", json_key="-"),
    ],
)

nsource = TypeDesc(
    package="nmos",
    name="NSource",
    is_value=True,
    is_base=True,
    poly_types=['NSourceVideo', 'NSourceAudio', 'NSourceData', 'NSourceMux'],
    members=[
        MemberDesc(name="value", type_name="object", json_key="-"),
    ],
)

narray_of_source = TypeDesc(
    package="nmos",
    name="NArrayOfSource",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NSourceValue]", json_key="-"),
    ],
)

nsource_capabilities = TypeDesc(
    package="nmos",
    name="NSourceCapabilities",
    members=[
        MemberDesc(name="Version", type_name="NTime", json_key="version", optional=True),
        MemberDesc(name="ConstraintSets", type_name="NArrayOfConstraintSet", json_key="constraint_sets", optional=True),
    ],
)

naudio_channel = TypeDesc(
    package="nmos",
    name="NAudioChannel",
    members=[
        MemberDesc(name="Label", type_name="NString", json_key="label"),
        MemberDesc(name="Symbol", type_name="NEnum", json_key="symbol", optional=True),
    ],
)

narray_of_audio_channel = TypeDesc(
    package="nmos",
    name="NArrayOfAudioChannel",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NAudioChannelValue]", json_key="-"),
    ],
)

nflow_core = TypeDesc(
    package="nmos",
    name="NFlowCore",
    is_embedded=True,
    members=[
        MemberDesc(name="ResourceCore", type_name="NResourceCore", embedded=True),
        MemberDesc(name="SourceId", type_name="NString", json_key="source_id", assertion="CheckResourceIdString"),
        MemberDesc(name="DeviceId", type_name="NString", json_key="device_id", assertion="CheckResourceIdString"),
        MemberDesc(name="Device", type_name="NDevicePtr", json_key="-"),
        MemberDesc(name="Parents", type_name="NArrayOfString", json_key="parents", assertion="CheckArrayOfResourceIdString"),
        MemberDesc(name="Children", type_name="NFlowPtrs", json_key="-"),
        MemberDesc(name="Senders", type_name="NSenderPtrs", json_key="-"),
        MemberDesc(name="GrainRate", type_name="NRational", json_key="grain_rate", optional=True),
        MemberDesc(name="Layer", type_name="NInt", json_key="urn:x-matrox:layer", optional=True),
        MemberDesc(name="LayerCompatibilityGroups", type_name="NArrayOfInt", json_key="urn:x-matrox:layer_compatibility_groups", optional=True),
        MemberDesc(name="Static", type_name="NBool", json_key="-"),
        MemberDesc(name="RawFlow", type_name="NFlowPtr", json_key="-"),
        MemberDesc(name="CodedFlow", type_name="NFlowPtr", json_key="-"),
    ],
)

nflow_video_raw = TypeDesc(
    package="nmos",
    name="NFlowVideoRaw",
    members=[
        MemberDesc(name="FlowCore", type_name="NFlowCore", embedded=True),
        MemberDesc(name="Format", type_name="NEnum", json_key="format", assertion="CheckFormat"),
        MemberDesc(name="MediaType", type_name="NEnum", json_key="media_type"),
        MemberDesc(name="FrameWidth", type_name="NInt", json_key="frame_width"),
        MemberDesc(name="FrameHeight", type_name="NInt", json_key="frame_height"),
        MemberDesc(name="Colorspace", type_name="NEnum", json_key="colorspace", assertion="CheckColorspace"),
        MemberDesc(name="InterlaceMode", type_name="NEnum", json_key="interlace_mode", optional=True, default='EnumRegistry.get("progressive")', assertion="CheckInterlaceMode"),
        MemberDesc(name="TransferCharacteristic", type_name="NEnum", json_key="transfer_characteristic", optional=True, default='EnumRegistry.get("SDR")', assertion="CheckTransferCharacteristic"),
        MemberDesc(name="Components", type_name="NArrayOfVideoComponent", json_key="components", assertion="CheckVideoComponents"),
    ],
)

nflow_video_coded = TypeDesc(
    package="nmos",
    name="NFlowVideoCoded",
    members=[
        MemberDesc(name="FlowCore", type_name="NFlowCore", embedded=True),
        MemberDesc(name="Format", type_name="NEnum", json_key="format", assertion="CheckFormat"),
        MemberDesc(name="MediaType", type_name="NEnum", json_key="media_type"),
        MemberDesc(name="FrameWidth", type_name="NInt", json_key="frame_width"),
        MemberDesc(name="FrameHeight", type_name="NInt", json_key="frame_height"),
        MemberDesc(name="Colorspace", type_name="NEnum", json_key="colorspace", assertion="CheckColorspace"),
        MemberDesc(name="InterlaceMode", type_name="NEnum", json_key="interlace_mode", optional=True, default='EnumRegistry.get("progressive")', assertion="CheckInterlaceMode"),
        MemberDesc(name="TransferCharacteristic", type_name="NEnum", json_key="transfer_characteristic", optional=True, default='EnumRegistry.get("SDR")', assertion="CheckTransferCharacteristic"),
        MemberDesc(name="Components", type_name="NArrayOfVideoComponent", json_key="components", assertion="CheckVideoComponents"),
        MemberDesc(name="Profile", type_name="NEnum", json_key="profile", optional=True),
        MemberDesc(name="Level", type_name="NEnum", json_key="level", optional=True),
        MemberDesc(name="Sublevel", type_name="NEnum", json_key="sublevel", optional=True),
        MemberDesc(name="Fbblevel", type_name="NEnum", json_key="fbblevel", optional=True),
        MemberDesc(name="Bitrate", type_name="NInt", json_key="bit_rate", optional=True),
        MemberDesc(name="ConstantBitrate", type_name="NBool", json_key="constant_bit_rate", optional=True),
    ],
)

nflow_audio_raw = TypeDesc(
    package="nmos",
    name="NFlowAudioRaw",
    members=[
        MemberDesc(name="FlowCore", type_name="NFlowCore", embedded=True),
        MemberDesc(name="Format", type_name="NEnum", json_key="format", assertion="CheckFormat"),
        MemberDesc(name="MediaType", type_name="NEnum", json_key="media_type"),
        MemberDesc(name="BitDepth", type_name="NInt", json_key="bit_depth"),
        MemberDesc(name="SampleRate", type_name="NRational", json_key="sample_rate"),
    ],
)

nflow_audio_coded = TypeDesc(
    package="nmos",
    name="NFlowAudioCoded",
    members=[
        MemberDesc(name="FlowCore", type_name="NFlowCore", embedded=True),
        MemberDesc(name="Format", type_name="NEnum", json_key="format", assertion="CheckFormat"),
        MemberDesc(name="MediaType", type_name="NEnum", json_key="media_type"),
        MemberDesc(name="SampleRate", type_name="NRational", json_key="sample_rate"),
        MemberDesc(name="Profile", type_name="NEnum", json_key="profile", optional=True),
        MemberDesc(name="Level", type_name="NEnum", json_key="level", optional=True),
        MemberDesc(name="Bitrate", type_name="NInt", json_key="bit_rate", optional=True),
        MemberDesc(name="ConstantBitrate", type_name="NBool", json_key="constant_bit_rate", optional=True),
    ],
)

nflow_data = TypeDesc(
    package="nmos",
    name="NFlowData",
    members=[
        MemberDesc(name="FlowCore", type_name="NFlowCore", embedded=True),
        MemberDesc(name="Format", type_name="NEnum", json_key="format", assertion="CheckFormat"),
        MemberDesc(name="MediaType", type_name="NEnum", json_key="media_type"),
    ],
)

nflow_data_sdianc = TypeDesc(
    package="nmos",
    name="NFlowDataSdianc",
    members=[
        MemberDesc(name="FlowCore", type_name="NFlowCore", embedded=True),
        MemberDesc(name="Format", type_name="NEnum", json_key="format", assertion="CheckFormat"),
        MemberDesc(name="MediaType", type_name="NEnum", json_key="media_type"),
        MemberDesc(name="DidSdid", type_name="NArrayOfDidSdid", json_key="DID_SDID", optional=True, assertion="CheckDidSdid"),
    ],
)

ndid_sdid = TypeDesc(
    package="nmos",
    name="NDidSdid",
    members=[
        MemberDesc(name="Did", type_name="NString", json_key="DID", optional=True, assertion="CheckDid"),
        MemberDesc(name="Sdid", type_name="NString", json_key="SDID", optional=True, assertion="CheckSdid"),
    ],
)

narray_of_did_sdid = TypeDesc(
    package="nmos",
    name="NArrayOfDidSdid",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NDidSdidValue]", json_key="-"),
    ],
)

nflow_data_json = TypeDesc(
    package="nmos",
    name="NFlowDataJson",
    members=[
        MemberDesc(name="FlowCore", type_name="NFlowCore", embedded=True),
        MemberDesc(name="Format", type_name="NEnum", json_key="format", assertion="CheckFormat"),
        MemberDesc(name="MediaType", type_name="NEnum", json_key="media_type"),
    ],
)

nflow_mux = TypeDesc(
    package="nmos",
    name="NFlowMux",
    members=[
        MemberDesc(name="FlowCore", type_name="NFlowCore", embedded=True),
        MemberDesc(name="Format", type_name="NEnum", json_key="format", assertion="CheckFormat"),
        MemberDesc(name="MediaType", type_name="NEnum", json_key="media_type"),
        MemberDesc(name="VideoLayers", type_name="NInt", json_key="urn:x-matrox:video_layers", optional=True),
        MemberDesc(name="AudioLayers", type_name="NInt", json_key="urn:x-matrox:audio_layers", optional=True),
        MemberDesc(name="DataLayers", type_name="NInt", json_key="urn:x-matrox:data_layers", optional=True),
    ],
)

nflow_ptr = TypeDesc(
    package="nmos",
    name="NFlowPtr",
    is_value=True,
    is_base=True,
    members=[
        MemberDesc(name="value", type_name="NFlowValue", json_key="-"),
    ],
)

nflow_ptrs = TypeDesc(
    package="nmos",
    name="NFlowPtrs",
    is_value=True,
    members=[
        MemberDesc(name="value", type_name="dict[NFlowValue, NFlowValue]", json_key="-"),
    ],
)

nflow = TypeDesc(
    package="nmos",
    name="NFlow",
    is_value=True,
    is_base=True,
    poly_types=['NFlowVideoRaw', 'NFlowVideoCoded', 'NFlowAudioRaw', 'NFlowAudioCoded', 'NFlowData', 'NFlowDataSdianc', 'NFlowDataJson', 'NFlowMux'],
    members=[
        MemberDesc(name="value", type_name="object", json_key="-"),
    ],
)

narray_of_flow = TypeDesc(
    package="nmos",
    name="NArrayOfFlow",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NFlowValue]", json_key="-"),
    ],
)

nvideo_component = TypeDesc(
    package="nmos",
    name="NVideoComponent",
    members=[
        MemberDesc(name="Name", type_name="NEnum", json_key="name"),
        MemberDesc(name="Width", type_name="NInt", json_key="width"),
        MemberDesc(name="Height", type_name="NInt", json_key="height"),
        MemberDesc(name="BitDepth", type_name="NInt", json_key="bit_depth"),
    ],
)

narray_of_video_component = TypeDesc(
    package="nmos",
    name="NArrayOfVideoComponent",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NVideoComponentValue]", json_key="-"),
    ],
)

ndevice_ptr = TypeDesc(
    package="nmos",
    name="NDevicePtr",
    is_value=True,
    is_base=True,
    members=[
        MemberDesc(name="value", type_name="NDeviceValue", json_key="-"),
    ],
)

ndevice = TypeDesc(
    package="nmos",
    name="NDevice",
    members=[
        MemberDesc(name="ResourceCore", type_name="NResourceCore", embedded=True),
        MemberDesc(name="Type", type_name="NEnum", json_key="type", default='EnumRegistry.get("urn:x-nmos:device:generic")', assertion="CheckDeviceType"),
        MemberDesc(name="NodeId", type_name="NString", json_key="node_id", assertion="CheckResourceIdString"),
        MemberDesc(name="Node", type_name="NNodePtr", json_key="-"),
        MemberDesc(name="Senders", type_name="NArrayOfString", json_key="senders", assertion="CheckArrayOfResourceIdString"),
        MemberDesc(name="Receivers", type_name="NArrayOfString", json_key="receivers", assertion="CheckArrayOfResourceIdString"),
        MemberDesc(name="Controls", type_name="NArrayOfDeviceControl", json_key="controls"),
    ],
)

narray_of_device = TypeDesc(
    package="nmos",
    name="NArrayOfDevice",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NDeviceValue]", json_key="-"),
    ],
)

ndevice_control = TypeDesc(
    package="nmos",
    name="NDeviceControl",
    members=[
        MemberDesc(name="Href", type_name="NUrl", json_key="href"),
        MemberDesc(name="Type", type_name="NString", json_key="type"),
        MemberDesc(name="Authorization", type_name="NBool", json_key="authorization", optional=True, default='False'),
    ],
)

narray_of_device_control = TypeDesc(
    package="nmos",
    name="NArrayOfDeviceControl",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NDeviceControlValue]", json_key="-"),
    ],
)

nnode_ptr = TypeDesc(
    package="nmos",
    name="NNodePtr",
    is_value=True,
    is_base=True,
    members=[
        MemberDesc(name="value", type_name="NNodeValue", json_key="-"),
    ],
)

nnode = TypeDesc(
    package="nmos",
    name="NNode",
    members=[
        MemberDesc(name="ResourceCore", type_name="NResourceCore", embedded=True),
        MemberDesc(name="Href", type_name="NUrl", json_key="href"),
        MemberDesc(name="Caps", type_name="NEmpty", json_key="caps"),
        MemberDesc(name="Api", type_name="NNodeApi", json_key="api"),
        MemberDesc(name="Services", type_name="NArrayOfNodeService", json_key="services"),
        MemberDesc(name="Clocks", type_name="NArrayOfClock", json_key="clocks"),
        MemberDesc(name="Interfaces", type_name="NArrayOfNodeInterface", json_key="interfaces"),
    ],
)

nempty = TypeDesc(
    package="nmos",
    name="NEmpty",
)

narray_of_node = TypeDesc(
    package="nmos",
    name="NArrayOfNode",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NNodeValue]", json_key="-"),
    ],
)

nnode_api = TypeDesc(
    package="nmos",
    name="NNodeApi",
    members=[
        MemberDesc(name="Versions", type_name="NArrayOfString", json_key="versions", assertion="CheckNodeApiVersions"),
        MemberDesc(name="Endpoints", type_name="NArrayOfNodeEndpoint", json_key="endpoints"),
    ],
)

nnode_endpoint = TypeDesc(
    package="nmos",
    name="NNodeEndpoint",
    members=[
        MemberDesc(name="Host", type_name="NString", json_key="host", assertion="CheckEndpointHostString"),
        MemberDesc(name="Port", type_name="NInt", json_key="port", assertion="CheckEndpointPort"),
        MemberDesc(name="Protocol", type_name="NEnum", json_key="protocol", assertion="CheckEndpointProtocol"),
        MemberDesc(name="Authorization", type_name="NBool", json_key="authorization", optional=True, default='False'),
    ],
)

narray_of_node_endpoint = TypeDesc(
    package="nmos",
    name="NArrayOfNodeEndpoint",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NNodeEndpointValue]", json_key="-"),
    ],
)

nnode_service = TypeDesc(
    package="nmos",
    name="NNodeService",
    members=[
        MemberDesc(name="Href", type_name="NUrl", json_key="href"),
        MemberDesc(name="Type", type_name="NEnum", json_key="type", assertion="CheckServiceType"),
        MemberDesc(name="Authorization", type_name="NBool", json_key="authorization", optional=True, default='False'),
    ],
)

narray_of_node_service = TypeDesc(
    package="nmos",
    name="NArrayOfNodeService",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NNodeServiceValue]", json_key="-"),
    ],
)

nnode_interface = TypeDesc(
    package="nmos",
    name="NNodeInterface",
    members=[
        MemberDesc(name="ChassisId", type_name="NNullString", json_key="chassis_id", assertion="CheckChassisIdNullableString"),
        MemberDesc(name="PortId", type_name="NString", json_key="port_id", assertion="CheckPortIdString"),
        MemberDesc(name="Name", type_name="NString", json_key="name"),
        MemberDesc(name="AttachedNetworkDevice", type_name="NNetworkDevice", json_key="attached_network_device", optional=True),
    ],
)

narray_of_node_interface = TypeDesc(
    package="nmos",
    name="NArrayOfNodeInterface",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NNodeInterfaceValue]", json_key="-"),
    ],
)

nnetwork_device = TypeDesc(
    package="nmos",
    name="NNetworkDevice",
    members=[
        MemberDesc(name="ChassisId", type_name="NNullString", json_key="chassis_id", assertion="CheckChassisIdNullableString"),
        MemberDesc(name="PortId", type_name="NString", json_key="port_id"),
    ],
)

# ``params`` is declared by both subscription schemas as a bare
# ``{"type": "object"}`` -- a free-form filter object, "Object containing
# attributes to filter the resource on as per the Query Parameters
# specification. Can be empty." It was previously typed ``NEmpty``, which has
# ZERO members, so every filter the client sent was silently discarded on
# re-encode and filtered subscriptions could not work at all. ``NGeneric``
# carries an arbitrary JSON value through encode/decode untouched, which is
# what a free-form object requires; the filter engine coerces values to
# strings at comparison time, matching basic-query semantics where every
# query-string value is a string.
nquery_subscription_request = TypeDesc(
    package="nmos",
    name="NQuerySubscriptionRequest",
    members=[
        MemberDesc(name="MaxUpdateRate_ms", type_name="NInt", json_key="max_update_rate_ms", default='100'),
        MemberDesc(name="Persist", type_name="NBool", json_key="persist", default='False'),
        MemberDesc(name="ResourcePath", type_name="NString", json_key="resource_path"),
        MemberDesc(name="Params", type_name="NGeneric", json_key="params"),
        # ``secure`` is NOT in the POST request's ``required`` list -- the
        # server assigns it from its own scheme when the client omits it.
        MemberDesc(name="Secure", type_name="NBool", json_key="secure", optional=True),
        MemberDesc(name="Authorization", type_name="NBool", json_key="authorization", optional=True),
    ],
)

nquery_subscription_response = TypeDesc(
    package="nmos",
    name="NQuerySubscriptionResponse",
    members=[
        MemberDesc(name="Id", type_name="NString", json_key="id", assertion="CheckResourceIdString"),
        MemberDesc(name="WsHref", type_name="NString", json_key="ws_href"),
        MemberDesc(name="MaxUpdateRate_ms", type_name="NInt", json_key="max_update_rate_ms", default='100'),
        MemberDesc(name="Persist", type_name="NBool", json_key="persist", default='False'),
        MemberDesc(name="ResourcePath", type_name="NString", json_key="resource_path"),
        MemberDesc(name="Params", type_name="NGeneric", json_key="params"),
        # Unlike the request, ``secure`` IS in the response's ``required``
        # list (queryapi-subscription-response.json), so it is not optional
        # here -- the server has always resolved it by the time it answers.
        MemberDesc(name="Secure", type_name="NBool", json_key="secure"),
        MemberDesc(name="Authorization", type_name="NBool", json_key="authorization", optional=True),
    ],
)

narray_of_query_subscription_response = TypeDesc(
    package="nmos",
    name="NArrayOfQuerySubscriptionResponse",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NQuerySubscriptionResponseValue]", json_key="-"),
    ],
)

nquery_payload_node = TypeDesc(
    package="nmos",
    name="NQueryPayloadNode",
    members=[
        MemberDesc(name="GrainType", type_name="NString", json_key="grain_type"),
        MemberDesc(name="SourceId", type_name="NString", json_key="source_id", assertion="CheckResourceIdString"),
        MemberDesc(name="FlowId", type_name="NString", json_key="flow_id", assertion="CheckResourceIdString"),
        MemberDesc(name="OriginTimestamp", type_name="NTime", json_key="origin_timestamp"),
        MemberDesc(name="SyncTimestamp", type_name="NTime", json_key="sync_timestamp"),
        MemberDesc(name="CreationTimestamp", type_name="NTime", json_key="creation_timestamp"),
        MemberDesc(name="Rate", type_name="NRational", json_key="rate"),
        MemberDesc(name="Duration", type_name="NRational", json_key="duration"),
        MemberDesc(name="Grain", type_name="NQueryWebSocketGrainNode", json_key="grain"),
    ],
)

nquery_payload_device = TypeDesc(
    package="nmos",
    name="NQueryPayloadDevice",
    members=[
        MemberDesc(name="GrainType", type_name="NString", json_key="grain_type"),
        MemberDesc(name="SourceId", type_name="NString", json_key="source_id", assertion="CheckResourceIdString"),
        MemberDesc(name="FlowId", type_name="NString", json_key="flow_id", assertion="CheckResourceIdString"),
        MemberDesc(name="OriginTimestamp", type_name="NTime", json_key="origin_timestamp"),
        MemberDesc(name="SyncTimestamp", type_name="NTime", json_key="sync_timestamp"),
        MemberDesc(name="CreationTimestamp", type_name="NTime", json_key="creation_timestamp"),
        MemberDesc(name="Rate", type_name="NRational", json_key="rate"),
        MemberDesc(name="Duration", type_name="NRational", json_key="duration"),
        MemberDesc(name="Grain", type_name="NQueryWebSocketGrainDevice", json_key="grain"),
    ],
)

nquery_payload_source = TypeDesc(
    package="nmos",
    name="NQueryPayloadSource",
    members=[
        MemberDesc(name="GrainType", type_name="NString", json_key="grain_type"),
        MemberDesc(name="SourceId", type_name="NString", json_key="source_id", assertion="CheckResourceIdString"),
        MemberDesc(name="FlowId", type_name="NString", json_key="flow_id", assertion="CheckResourceIdString"),
        MemberDesc(name="OriginTimestamp", type_name="NTime", json_key="origin_timestamp"),
        MemberDesc(name="SyncTimestamp", type_name="NTime", json_key="sync_timestamp"),
        MemberDesc(name="CreationTimestamp", type_name="NTime", json_key="creation_timestamp"),
        MemberDesc(name="Rate", type_name="NRational", json_key="rate"),
        MemberDesc(name="Duration", type_name="NRational", json_key="duration"),
        MemberDesc(name="Grain", type_name="NQueryWebSocketGrainSource", json_key="grain"),
    ],
)

nquery_payload_flow = TypeDesc(
    package="nmos",
    name="NQueryPayloadFlow",
    members=[
        MemberDesc(name="GrainType", type_name="NString", json_key="grain_type"),
        MemberDesc(name="SourceId", type_name="NString", json_key="source_id", assertion="CheckResourceIdString"),
        MemberDesc(name="FlowId", type_name="NString", json_key="flow_id", assertion="CheckResourceIdString"),
        MemberDesc(name="OriginTimestamp", type_name="NTime", json_key="origin_timestamp"),
        MemberDesc(name="SyncTimestamp", type_name="NTime", json_key="sync_timestamp"),
        MemberDesc(name="CreationTimestamp", type_name="NTime", json_key="creation_timestamp"),
        MemberDesc(name="Rate", type_name="NRational", json_key="rate"),
        MemberDesc(name="Duration", type_name="NRational", json_key="duration"),
        MemberDesc(name="Grain", type_name="NQueryWebSocketGrainFlow", json_key="grain"),
    ],
)

nquery_payload_sender = TypeDesc(
    package="nmos",
    name="NQueryPayloadSender",
    members=[
        MemberDesc(name="GrainType", type_name="NString", json_key="grain_type"),
        MemberDesc(name="SourceId", type_name="NString", json_key="source_id", assertion="CheckResourceIdString"),
        MemberDesc(name="FlowId", type_name="NString", json_key="flow_id", assertion="CheckResourceIdString"),
        MemberDesc(name="OriginTimestamp", type_name="NTime", json_key="origin_timestamp"),
        MemberDesc(name="SyncTimestamp", type_name="NTime", json_key="sync_timestamp"),
        MemberDesc(name="CreationTimestamp", type_name="NTime", json_key="creation_timestamp"),
        MemberDesc(name="Rate", type_name="NRational", json_key="rate"),
        MemberDesc(name="Duration", type_name="NRational", json_key="duration"),
        MemberDesc(name="Grain", type_name="NQueryWebSocketGrainSender", json_key="grain"),
    ],
)

nquery_payload_receiver = TypeDesc(
    package="nmos",
    name="NQueryPayloadReceiver",
    members=[
        MemberDesc(name="GrainType", type_name="NString", json_key="grain_type"),
        MemberDesc(name="SourceId", type_name="NString", json_key="source_id", assertion="CheckResourceIdString"),
        MemberDesc(name="FlowId", type_name="NString", json_key="flow_id", assertion="CheckResourceIdString"),
        MemberDesc(name="OriginTimestamp", type_name="NTime", json_key="origin_timestamp"),
        MemberDesc(name="SyncTimestamp", type_name="NTime", json_key="sync_timestamp"),
        MemberDesc(name="CreationTimestamp", type_name="NTime", json_key="creation_timestamp"),
        MemberDesc(name="Rate", type_name="NRational", json_key="rate"),
        MemberDesc(name="Duration", type_name="NRational", json_key="duration"),
        MemberDesc(name="Grain", type_name="NQueryWebSocketGrainReceiver", json_key="grain"),
    ],
)

nquery_web_socket_grain_node = TypeDesc(
    package="nmos",
    name="NQueryWebSocketGrainNode",
    members=[
        MemberDesc(name="Type", type_name="NString", json_key="type"),
        MemberDesc(name="Topic", type_name="NString", json_key="topic"),
        MemberDesc(name="Data", type_name="NArrayOfQueryWebSocketGrainDataNode", json_key="data"),
    ],
)

nquery_web_socket_grain_device = TypeDesc(
    package="nmos",
    name="NQueryWebSocketGrainDevice",
    members=[
        MemberDesc(name="Type", type_name="NString", json_key="type"),
        MemberDesc(name="Topic", type_name="NString", json_key="topic"),
        MemberDesc(name="Data", type_name="NArrayOfQueryWebSocketGrainDataDevice", json_key="data"),
    ],
)

nquery_web_socket_grain_source = TypeDesc(
    package="nmos",
    name="NQueryWebSocketGrainSource",
    members=[
        MemberDesc(name="Type", type_name="NString", json_key="type"),
        MemberDesc(name="Topic", type_name="NString", json_key="topic"),
        MemberDesc(name="Data", type_name="NArrayOfQueryWebSocketGrainDataSource", json_key="data"),
    ],
)

nquery_web_socket_grain_flow = TypeDesc(
    package="nmos",
    name="NQueryWebSocketGrainFlow",
    members=[
        MemberDesc(name="Type", type_name="NString", json_key="type"),
        MemberDesc(name="Topic", type_name="NString", json_key="topic"),
        MemberDesc(name="Data", type_name="NArrayOfQueryWebSocketGrainDataFlow", json_key="data"),
    ],
)

nquery_web_socket_grain_sender = TypeDesc(
    package="nmos",
    name="NQueryWebSocketGrainSender",
    members=[
        MemberDesc(name="Type", type_name="NString", json_key="type"),
        MemberDesc(name="Topic", type_name="NString", json_key="topic"),
        MemberDesc(name="Data", type_name="NArrayOfQueryWebSocketGrainDataSender", json_key="data"),
    ],
)

nquery_web_socket_grain_receiver = TypeDesc(
    package="nmos",
    name="NQueryWebSocketGrainReceiver",
    members=[
        MemberDesc(name="Type", type_name="NString", json_key="type"),
        MemberDesc(name="Topic", type_name="NString", json_key="topic"),
        MemberDesc(name="Data", type_name="NArrayOfQueryWebSocketGrainDataReceiver", json_key="data"),
    ],
)

nquery_web_socket_grain_data_node = TypeDesc(
    package="nmos",
    name="NQueryWebSocketGrainDataNode",
    members=[
        MemberDesc(name="Path", type_name="NString", json_key="path"),
        MemberDesc(name="Pre", type_name="NNode", json_key="pre", optional=True),
        MemberDesc(name="Post", type_name="NNode", json_key="post", optional=True),
    ],
)

nquery_web_socket_grain_data_device = TypeDesc(
    package="nmos",
    name="NQueryWebSocketGrainDataDevice",
    members=[
        MemberDesc(name="Path", type_name="NString", json_key="path"),
        MemberDesc(name="Pre", type_name="NDevice", json_key="pre", optional=True),
        MemberDesc(name="Post", type_name="NDevice", json_key="post", optional=True),
    ],
)

nquery_web_socket_grain_data_source = TypeDesc(
    package="nmos",
    name="NQueryWebSocketGrainDataSource",
    members=[
        MemberDesc(name="Path", type_name="NString", json_key="path"),
        MemberDesc(name="Pre", type_name="NSource", json_key="pre", optional=True),
        MemberDesc(name="Post", type_name="NSource", json_key="post", optional=True),
    ],
)

nquery_web_socket_grain_data_flow = TypeDesc(
    package="nmos",
    name="NQueryWebSocketGrainDataFlow",
    members=[
        MemberDesc(name="Path", type_name="NString", json_key="path"),
        MemberDesc(name="Pre", type_name="NFlow", json_key="pre", optional=True),
        MemberDesc(name="Post", type_name="NFlow", json_key="post", optional=True),
    ],
)

nquery_web_socket_grain_data_sender = TypeDesc(
    package="nmos",
    name="NQueryWebSocketGrainDataSender",
    members=[
        MemberDesc(name="Path", type_name="NString", json_key="path"),
        MemberDesc(name="Pre", type_name="NSender", json_key="pre", optional=True),
        MemberDesc(name="Post", type_name="NSender", json_key="post", optional=True),
    ],
)

nquery_web_socket_grain_data_receiver = TypeDesc(
    package="nmos",
    name="NQueryWebSocketGrainDataReceiver",
    members=[
        MemberDesc(name="Path", type_name="NString", json_key="path"),
        MemberDesc(name="Pre", type_name="NReceiver", json_key="pre", optional=True),
        MemberDesc(name="Post", type_name="NReceiver", json_key="post", optional=True),
    ],
)

narray_of_query_web_socket_grain_data_node = TypeDesc(
    package="nmos",
    name="NArrayOfQueryWebSocketGrainDataNode",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NQueryWebSocketGrainDataNode]", json_key="-"),
    ],
)

narray_of_query_web_socket_grain_data_device = TypeDesc(
    package="nmos",
    name="NArrayOfQueryWebSocketGrainDataDevice",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NQueryWebSocketGrainDataDevice]", json_key="-"),
    ],
)

narray_of_query_web_socket_grain_data_source = TypeDesc(
    package="nmos",
    name="NArrayOfQueryWebSocketGrainDataSource",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NQueryWebSocketGrainDataSource]", json_key="-"),
    ],
)

narray_of_query_web_socket_grain_data_flow = TypeDesc(
    package="nmos",
    name="NArrayOfQueryWebSocketGrainDataFlow",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NQueryWebSocketGrainDataFlow]", json_key="-"),
    ],
)

narray_of_query_web_socket_grain_data_sender = TypeDesc(
    package="nmos",
    name="NArrayOfQueryWebSocketGrainDataSender",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NQueryWebSocketGrainDataSender]", json_key="-"),
    ],
)

narray_of_query_web_socket_grain_data_receiver = TypeDesc(
    package="nmos",
    name="NArrayOfQueryWebSocketGrainDataReceiver",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NQueryWebSocketGrainDataReceiver]", json_key="-"),
    ],
)

# ---------------------------------------------------------------------------
# Query API WebSocket grains -- resource-agnostic ("generic") family
# ---------------------------------------------------------------------------
#
# The six families above (…GrainDataNode, …GrainDataDevice, …) type ``pre`` and
# ``post`` as the concrete resource type (NNode, NDevice, …). That is the right
# shape for a CLIENT that wants a typed view of what it received.
#
# A registry emitting grains has the opposite requirement. It must reproduce the
# registered resource EXACTLY as it was registered, including any vendor
# extension a third-party Node sent that our generated types do not model --
# otherwise the resource a client sees over the WebSocket would differ from the
# one it sees over ``GET /x-nmos/query/v1.3/<collection>``. Routing pre/post
# through a concrete type would silently drop those keys.
#
# This family keeps the whole grain envelope typed (grain_type, source_id,
# flow_id, the three timestamps, rate, duration, grain.type, grain.topic) while
# ``pre``/``post`` are NGeneric, which carries an arbitrary JSON object through
# encode/decode untouched. The wire format is identical to the six typed
# families -- only the static typing of the payload differs.

nquery_web_socket_grain_data_generic = TypeDesc(
    package="nmos",
    name="NQueryWebSocketGrainDataGeneric",
    members=[
        MemberDesc(name="Path", type_name="NString", json_key="path"),
        MemberDesc(name="Pre", type_name="NGeneric", json_key="pre", optional=True),
        MemberDesc(name="Post", type_name="NGeneric", json_key="post", optional=True),
    ],
)

narray_of_query_web_socket_grain_data_generic = TypeDesc(
    package="nmos",
    name="NArrayOfQueryWebSocketGrainDataGeneric",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NQueryWebSocketGrainDataGeneric]", json_key="-"),
    ],
)

nquery_web_socket_grain_generic = TypeDesc(
    package="nmos",
    name="NQueryWebSocketGrainGeneric",
    members=[
        MemberDesc(name="Type", type_name="NString", json_key="type"),
        MemberDesc(name="Topic", type_name="NString", json_key="topic"),
        MemberDesc(name="Data", type_name="NArrayOfQueryWebSocketGrainDataGeneric", json_key="data"),
    ],
)

nquery_payload_generic = TypeDesc(
    package="nmos",
    name="NQueryPayloadGeneric",
    members=[
        MemberDesc(name="GrainType", type_name="NString", json_key="grain_type"),
        MemberDesc(name="SourceId", type_name="NString", json_key="source_id", assertion="CheckResourceIdString"),
        MemberDesc(name="FlowId", type_name="NString", json_key="flow_id", assertion="CheckResourceIdString"),
        MemberDesc(name="OriginTimestamp", type_name="NTime", json_key="origin_timestamp"),
        MemberDesc(name="SyncTimestamp", type_name="NTime", json_key="sync_timestamp"),
        MemberDesc(name="CreationTimestamp", type_name="NTime", json_key="creation_timestamp"),
        MemberDesc(name="Rate", type_name="NRational", json_key="rate"),
        MemberDesc(name="Duration", type_name="NRational", json_key="duration"),
        MemberDesc(name="Grain", type_name="NQueryWebSocketGrainGeneric", json_key="grain"),
    ],
)


# ---------------------------------------------------------------------------
# Registration API
# ---------------------------------------------------------------------------
#
# ``registrationapi-health-response.json`` types ``health`` as
# ``{"type": "string", "pattern": "^[0-9]+$"}`` -- a STRING holding the
# TAI seconds, not a JSON number. (nmos-cpp agrees: make_health_response_body
# emits json::value::string. The AMWA test-suite mock returns an int, which is
# the outlier.)
nregistration_health_response = TypeDesc(
    package="nmos",
    name="NRegistrationHealthResponse",
    members=[
        MemberDesc(name="Health", type_name="NString", json_key="health", assertion="CheckHealthString"),
    ],
)

# ``registrationapi-resource-post-request.json`` is a ``oneOf`` over six
# ``{"type": <singular>, "data": <resource>}`` envelopes. Each concrete
# envelope is declared separately and NRegistrationResourcePost dispatches on
# the ``type`` discriminator (see NREGISTRATION_RESOURCE_POST_PREDICATES in
# predicates.py).
#
# These also give the Node-side Registration client a typed body to encode,
# replacing the raw f-string concatenation it used to build by hand.

nregistration_post_node = TypeDesc(
    package="nmos",
    name="NRegistrationPostNode",
    members=[
        MemberDesc(name="Type", type_name="NString", json_key="type"),
        MemberDesc(name="Data", type_name="NNode", json_key="data"),
    ],
)

nregistration_post_device = TypeDesc(
    package="nmos",
    name="NRegistrationPostDevice",
    members=[
        MemberDesc(name="Type", type_name="NString", json_key="type"),
        MemberDesc(name="Data", type_name="NDevice", json_key="data"),
    ],
)

nregistration_post_source = TypeDesc(
    package="nmos",
    name="NRegistrationPostSource",
    members=[
        MemberDesc(name="Type", type_name="NString", json_key="type"),
        MemberDesc(name="Data", type_name="NSource", json_key="data"),
    ],
)

nregistration_post_flow = TypeDesc(
    package="nmos",
    name="NRegistrationPostFlow",
    members=[
        MemberDesc(name="Type", type_name="NString", json_key="type"),
        MemberDesc(name="Data", type_name="NFlow", json_key="data"),
    ],
)

nregistration_post_sender = TypeDesc(
    package="nmos",
    name="NRegistrationPostSender",
    members=[
        MemberDesc(name="Type", type_name="NString", json_key="type"),
        MemberDesc(name="Data", type_name="NSender", json_key="data"),
    ],
)

nregistration_post_receiver = TypeDesc(
    package="nmos",
    name="NRegistrationPostReceiver",
    members=[
        MemberDesc(name="Type", type_name="NString", json_key="type"),
        MemberDesc(name="Data", type_name="NReceiver", json_key="data"),
    ],
)

nregistration_resource_post = TypeDesc(
    package="nmos",
    name="NRegistrationResourcePost",
    is_value=True,
    is_base=True,
    poly_types=[
        'NRegistrationPostNode',
        'NRegistrationPostDevice',
        'NRegistrationPostSource',
        'NRegistrationPostFlow',
        'NRegistrationPostSender',
        'NRegistrationPostReceiver',
    ],
    members=[
        MemberDesc(name="value", type_name="object", json_key="-"),
    ],
)

ALL_TYPES = [
    nresource_core,
    nerror,
    nclock_internal,
    nclock_ptp,
    narray_of_clock_internal,
    narray_of_clock_ptp,
    nclock,
    narray_of_clock,
    nsender_ptr,
    nsender_ptrs,
    nsender,
    narray_of_sender,
    nsender_subscription,
    nsender_capabilities,
    nconstraint_set,
    narray_of_constraint_set,
    nreceiver_core,
    nreceiver_video,
    nreceiver_audio,
    nreceiver_data,
    nreceiver_mux,
    nreceiver_ptr,
    nreceiver,
    narray_of_receiver,
    nreceiver_subscription,
    nreceiver_video_capabilities,
    nreceiver_audio_capabilities,
    nreceiver_data_capabilities,
    nreceiver_mux_capabilities,
    nsource_core,
    nsource_video,
    nsource_audio,
    nmonitor_state,
    nsource_data,
    nsource_mux,
    nsource_ptr,
    nsource_ptrs,
    nsource,
    narray_of_source,
    nsource_capabilities,
    naudio_channel,
    narray_of_audio_channel,
    nflow_core,
    nflow_video_raw,
    nflow_video_coded,
    nflow_audio_raw,
    nflow_audio_coded,
    nflow_data,
    nflow_data_sdianc,
    ndid_sdid,
    narray_of_did_sdid,
    nflow_data_json,
    nflow_mux,
    nflow_ptr,
    nflow_ptrs,
    nflow,
    narray_of_flow,
    nvideo_component,
    narray_of_video_component,
    ndevice_ptr,
    ndevice,
    narray_of_device,
    ndevice_control,
    narray_of_device_control,
    nnode_ptr,
    nnode,
    nempty,
    narray_of_node,
    nnode_api,
    nnode_endpoint,
    narray_of_node_endpoint,
    nnode_service,
    narray_of_node_service,
    nnode_interface,
    narray_of_node_interface,
    nnetwork_device,
    nquery_subscription_request,
    nquery_subscription_response,
    narray_of_query_subscription_response,
    nquery_payload_node,
    nquery_payload_device,
    nquery_payload_source,
    nquery_payload_flow,
    nquery_payload_sender,
    nquery_payload_receiver,
    nquery_web_socket_grain_node,
    nquery_web_socket_grain_device,
    nquery_web_socket_grain_source,
    nquery_web_socket_grain_flow,
    nquery_web_socket_grain_sender,
    nquery_web_socket_grain_receiver,
    nquery_web_socket_grain_data_node,
    nquery_web_socket_grain_data_device,
    nquery_web_socket_grain_data_source,
    nquery_web_socket_grain_data_flow,
    nquery_web_socket_grain_data_sender,
    nquery_web_socket_grain_data_receiver,
    narray_of_query_web_socket_grain_data_node,
    narray_of_query_web_socket_grain_data_device,
    narray_of_query_web_socket_grain_data_source,
    narray_of_query_web_socket_grain_data_flow,
    narray_of_query_web_socket_grain_data_sender,
    narray_of_query_web_socket_grain_data_receiver,
    # Query API WebSocket grains -- resource-agnostic family (registry side)
    nquery_web_socket_grain_data_generic,
    narray_of_query_web_socket_grain_data_generic,
    nquery_web_socket_grain_generic,
    nquery_payload_generic,
    # Registration API
    nregistration_health_response,
    nregistration_post_node,
    nregistration_post_device,
    nregistration_post_source,
    nregistration_post_flow,
    nregistration_post_sender,
    nregistration_post_receiver,
    nregistration_resource_post,
]

