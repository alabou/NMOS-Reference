# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Type definitions auto-generated from Go source."""

from __future__ import annotations

from nmos.codegen.descriptors import MemberDesc, TypeDesc

nnc_ptr = TypeDesc(
    package="nmos",
    name="NNcPtr",
    is_value=True,
    is_base=True,
    members=[
        MemberDesc(name="value", type_name="object", json_key="-"),
    ],
)

nc_method_id = TypeDesc(
    package="nmos",
    name="NcMethodId",
    members=[
        MemberDesc(name="Level", type_name="NInt", json_key="level", assertion="CheckPositiveInteger"),
        MemberDesc(name="Index", type_name="NInt", json_key="index", assertion="CheckPositiveInteger"),
    ],
)

nc_event_id = TypeDesc(
    package="nmos",
    name="NcEventId",
    members=[
        MemberDesc(name="Level", type_name="NInt", json_key="level", assertion="CheckPositiveInteger"),
        MemberDesc(name="Index", type_name="NInt", json_key="index", assertion="CheckPositiveInteger"),
    ],
)

nc_property_id = TypeDesc(
    package="nmos",
    name="NcPropertyId",
    members=[
        MemberDesc(name="Level", type_name="NInt", json_key="level", assertion="CheckPositiveInteger"),
        MemberDesc(name="Index", type_name="NInt", json_key="index", assertion="CheckPositiveInteger"),
    ],
)

nc_command = TypeDesc(
    package="nmos",
    name="NcCommand",
    members=[
        MemberDesc(name="Handle", type_name="NInt", json_key="handle", assertion="CheckPositiveUint16"),
        MemberDesc(name="OId", type_name="NInt", json_key="oid", optional=True, assertion="CheckPositiveInteger"),
        MemberDesc(name="Object", type_name="NString", json_key="object", optional=True),
        MemberDesc(name="MethodId", type_name="NcMethodId", json_key="methodId", optional=True),
        MemberDesc(name="Method", type_name="NString", json_key="method", optional=True),
        MemberDesc(name="Arguments", type_name="NGeneric", json_key="arguments", optional=True, assertion="CheckGenericObject"),
    ],
)

nc_array_of_command = TypeDesc(
    package="nmos",
    name="NcArrayOfCommand",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NcCommandValue]", json_key="-"),
    ],
)

nc_response = TypeDesc(
    package="nmos",
    name="NcResponse",
    members=[
        MemberDesc(name="Handle", type_name="NInt", json_key="handle", assertion="CheckPositiveUint16"),
        MemberDesc(name="Result", type_name="NcResult", json_key="result"),
    ],
)

nc_array_of_response = TypeDesc(
    package="nmos",
    name="NcArrayOfResponse",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NcResponseValue]", json_key="-"),
    ],
)

nc_result = TypeDesc(
    package="nmos",
    name="NcResult",
    members=[
        MemberDesc(name="Status", type_name="NInt", json_key="status", assertion="CheckUint16"),
        MemberDesc(name="GenericValue", type_name="NGeneric", json_key="value", optional=True),
        MemberDesc(name="ErrorMessage", type_name="NString", json_key="errorMessage", optional=True),
    ],
)

nc_notification = TypeDesc(
    package="nmos",
    name="NcNotification",
    members=[
        MemberDesc(name="OId", type_name="NInt", json_key="oid", assertion="CheckPositiveInteger"),
        MemberDesc(name="EventId", type_name="NcEventId", json_key="eventId"),
        MemberDesc(name="EventData", type_name="NGeneric", json_key="eventData"),
    ],
)

nc_array_of_notification = TypeDesc(
    package="nmos",
    name="NcArrayOfNotification",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NcNotificationValue]", json_key="-"),
    ],
)

nc_propertychanged_event = TypeDesc(
    package="nmos",
    name="NcPropertychangedEvent",
    members=[
        MemberDesc(name="PropertyId", type_name="NcPropertyId", json_key="propertyId"),
        MemberDesc(name="ChangeType", type_name="NInt", json_key="changeType", assertion="CheckPropertyChangeType"),
        MemberDesc(name="GenericValue", type_name="NGeneric", json_key="value"),
        MemberDesc(name="SequenceItemIndex", type_name="NNull", json_key="sequenceItemIndex", assertion="CheckNullPositiveInteger"),
    ],
)

nc_property_changed_notification = TypeDesc(
    package="nmos",
    name="NcPropertyChangedNotification",
    members=[
        MemberDesc(name="OId", type_name="NInt", json_key="oid", assertion="CheckPositiveInteger"),
        MemberDesc(name="EventId", type_name="NcEventId", json_key="eventId"),
        MemberDesc(name="EventData", type_name="NcPropertychangedEvent", json_key="eventData"),
    ],
)

nc_array_of_property_changed_notification = TypeDesc(
    package="nmos",
    name="NcArrayOfPropertyChangedNotification",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NcPropertyChangedNotificationValue]", json_key="-"),
    ],
)

nc_command_message = TypeDesc(
    package="nmos",
    name="NcCommandMessage",
    members=[
        MemberDesc(name="MessageType", type_name="NInt", json_key="messageType", assertion="CheckCommandMessageType"),
        MemberDesc(name="Commands", type_name="NcArrayOfCommand", json_key="commands"),
    ],
)

nc_command_response_message = TypeDesc(
    package="nmos",
    name="NcCommandResponseMessage",
    members=[
        MemberDesc(name="MessageType", type_name="NInt", json_key="messageType", assertion="CheckCommandResponseMessageType"),
        MemberDesc(name="Responses", type_name="NcArrayOfResponse", json_key="responses"),
    ],
)

nc_notification_message = TypeDesc(
    package="nmos",
    name="NcNotificationMessage",
    members=[
        MemberDesc(name="MessageType", type_name="NInt", json_key="messageType", assertion="CheckNotificationMessageType"),
        MemberDesc(name="Notifications", type_name="NcArrayOfNotification", json_key="notifications"),
    ],
)

nc_subscription_message = TypeDesc(
    package="nmos",
    name="NcSubscriptionMessage",
    members=[
        MemberDesc(name="MessageType", type_name="NInt", json_key="messageType", assertion="CheckSubscriptionMessageType"),
        MemberDesc(name="Subscriptions", type_name="NArrayOfNull", json_key="subscriptions"),
    ],
)

nc_subscription_response_message = TypeDesc(
    package="nmos",
    name="NcSubscriptionResponseMessage",
    members=[
        MemberDesc(name="MessageType", type_name="NInt", json_key="messageType", assertion="CheckSubscriptionResponseMessageType"),
        MemberDesc(name="Subscriptions", type_name="NArrayOfInt", json_key="subscriptions"),
    ],
)

nc_error_message = TypeDesc(
    package="nmos",
    name="NcErrorMessage",
    members=[
        MemberDesc(name="MessageType", type_name="NInt", json_key="messageType", assertion="CheckErrorMessageType"),
        MemberDesc(name="Status", type_name="NInt", json_key="status", assertion="CheckUint16"),
        MemberDesc(name="ErrorMessage", type_name="NString", json_key="errorMessage"),
    ],
)

nc_message = TypeDesc(
    package="nmos",
    name="NcMessage",
    is_value=True,
    is_base=True,
    poly_types=['NcCommandMessage', 'NcCommandResponseMessage', 'NcNotificationMessage', 'NcSubscriptionMessage', 'NcSubscriptionResponseMessage', 'NcErrorMessage'],
    members=[
        MemberDesc(name="value", type_name="object", json_key="-"),
    ],
)

nc_object = TypeDesc(
    package="nmos",
    name="NcObject",
    is_embedded=True,
    members=[
        MemberDesc(name="Id", type_name="NArrayOfInt", json_key="id"),
        MemberDesc(name="OId", type_name="NInt", json_key="oid"),
        MemberDesc(name="ConstantOId", type_name="NBool", json_key="constantOid"),
        MemberDesc(name="Owner", type_name="NNull", json_key="owner", assertion="CheckNullInteger"),
        MemberDesc(name="Role", type_name="NString", json_key="role"),
        MemberDesc(name="UserLabel", type_name="NNullString", json_key="userLabel"),
        MemberDesc(name="Touchpoints", type_name="NArrayOfGeneric", json_key="touchpoints"),
        MemberDesc(name="RuntimePropertyConstraints", type_name="NArrayOfGeneric", json_key="runtimePropertyConstraints"),
        MemberDesc(name="NcPtr", type_name="NNcPtr", json_key="-"),
    ],
)

nc_block = TypeDesc(
    package="nmos",
    name="NcBlock",
    members=[
        MemberDesc(name="Base", type_name="NcObject", json_key="base", embedded=True),
        MemberDesc(name="Enabled", type_name="NBool", json_key="enabled"),
        MemberDesc(name="Members", type_name="NcArrayOfBlockMemberDescriptor", json_key="members"),
    ],
)

nc_descriptor = TypeDesc(
    package="nmos",
    name="NcDescriptor",
    is_embedded=True,
    members=[
        MemberDesc(name="Description", type_name="NNullString", json_key="description"),
    ],
)

nc_block_member_descriptor = TypeDesc(
    package="nmos",
    name="NcBlockMemberDescriptor",
    members=[
        MemberDesc(name="Base", type_name="NcDescriptor", embedded=True),
        MemberDesc(name="Role", type_name="NString", json_key="role"),
        MemberDesc(name="OId", type_name="NInt", json_key="oid"),
        MemberDesc(name="ConstantOId", type_name="NBool", json_key="constantOid"),
        MemberDesc(name="ClassId", type_name="NArrayOfInt", json_key="classId"),
        MemberDesc(name="UserLabel", type_name="NNullString", json_key="userLabel"),
        MemberDesc(name="Owner", type_name="NInt", json_key="owner"),
    ],
)

nc_array_of_block_member_descriptor = TypeDesc(
    package="nmos",
    name="NcArrayOfBlockMemberDescriptor",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NcBlockMemberDescriptorValue]", json_key="-"),
    ],
)

nc_worker = TypeDesc(
    package="nmos",
    name="NcWorker",
    is_embedded=True,
    members=[
        MemberDesc(name="Base", type_name="NcObject", embedded=True),
        MemberDesc(name="Enabled", type_name="NBool", json_key="enabled"),
    ],
)

nc_manager = TypeDesc(
    package="nmos",
    name="NcManager",
    is_embedded=True,
    members=[
        MemberDesc(name="Base", type_name="NcObject", embedded=True),
    ],
)

nc_device_manager = TypeDesc(
    package="nmos",
    name="NcDeviceManager",
    members=[
        MemberDesc(name="Base", type_name="NcManager", embedded=True),
        MemberDesc(name="NcVersion", type_name="NString", json_key="ncVersion"),
        MemberDesc(name="Manufacturer", type_name="NcManufacturer", json_key="manufacturer"),
        MemberDesc(name="Product", type_name="NcProduct", json_key="product"),
        MemberDesc(name="SerialNumber", type_name="NString", json_key="serialNumber"),
        MemberDesc(name="UserInventoryCode", type_name="NNullString", json_key="userInventoryCode"),
        MemberDesc(name="DeviceName", type_name="NNullString", json_key="deviceName"),
        MemberDesc(name="DeviceRole", type_name="NNullString", json_key="deviceRole"),
        MemberDesc(name="OperationalState", type_name="NcDeviceOperationalState", json_key="operationalState"),
        MemberDesc(name="ResetCause", type_name="NInt", json_key="resetCause", assertion="CheckResetCause"),
        MemberDesc(name="Message", type_name="NNullString", json_key="message"),
    ],
)

nc_manufacturer = TypeDesc(
    package="nmos",
    name="NcManufacturer",
    members=[
        MemberDesc(name="Name", type_name="NString", json_key="name"),
        MemberDesc(name="OrganizationId", type_name="NNull", json_key="organizationId", assertion="CheckNullInteger"),
        MemberDesc(name="WebSite", type_name="NNullString", json_key="website"),
    ],
)

nc_product = TypeDesc(
    package="nmos",
    name="NcProduct",
    members=[
        MemberDesc(name="Name", type_name="NString", json_key="name"),
        MemberDesc(name="Key", type_name="NString", json_key="key"),
        MemberDesc(name="RevisionLevel", type_name="NString", json_key="revisionLevel"),
        MemberDesc(name="BrandName", type_name="NNullString", json_key="brandName"),
        MemberDesc(name="Uuid", type_name="NString", json_key="uuid"),
        MemberDesc(name="Description", type_name="NNullString", json_key="description"),
    ],
)

nc_device_operational_state = TypeDesc(
    package="nmos",
    name="NcDeviceOperationalState",
    members=[
        MemberDesc(name="Generic", type_name="NInt", json_key="generic", assertion="CheckDeviceGenericState"),
        MemberDesc(name="DeviceSpecificDetails", type_name="NNullString", json_key="deviceSpecificDetails"),
    ],
)

nc_class_manager = TypeDesc(
    package="nmos",
    name="NcClassManager",
    members=[
        MemberDesc(name="Base", type_name="NcManager", embedded=True),
        MemberDesc(name="ControlClasses", type_name="NArrayOfGeneric", json_key="controlClasses"),
        MemberDesc(name="DataTypes", type_name="NArrayOfGeneric", json_key="datatypes"),
    ],
)

nc_class_descriptor = TypeDesc(
    package="nmos",
    name="NcClassDescriptor",
    members=[
        MemberDesc(name="Base", type_name="NcDescriptor", embedded=True),
        MemberDesc(name="ClassId", type_name="NArrayOfInt", json_key="classId"),
        MemberDesc(name="Name", type_name="NString", json_key="name"),
        MemberDesc(name="FixedRole", type_name="NNullString", json_key="fixedRole"),
        MemberDesc(name="Properties", type_name="NcArrayOfPropertyDescriptor", json_key="properties"),
        MemberDesc(name="Methods", type_name="NcArrayOfMethodDescriptor", json_key="methods"),
        MemberDesc(name="Events", type_name="NcArrayOfEventDescriptor", json_key="events"),
    ],
)

nc_array_of_class_descriptor = TypeDesc(
    package="nmos",
    name="NcArrayOfClassDescriptor",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NcClassDescriptorValue]", json_key="-"),
    ],
)

nc_property_descriptor = TypeDesc(
    package="nmos",
    name="NcPropertyDescriptor",
    members=[
        MemberDesc(name="Base", type_name="NcDescriptor", embedded=True),
        MemberDesc(name="Id", type_name="NcPropertyId", json_key="id"),
        MemberDesc(name="Name", type_name="NString", json_key="name"),
        MemberDesc(name="TypeName", type_name="NNullString", json_key="typeName"),
        MemberDesc(name="IsReadOnly", type_name="NBool", json_key="isReadOnly"),
        MemberDesc(name="IsNullable", type_name="NBool", json_key="isNullable"),
        MemberDesc(name="IsSequence", type_name="NBool", json_key="isSequence"),
        MemberDesc(name="IsDeprecated", type_name="NBool", json_key="isDeprecated"),
        MemberDesc(name="Constraints", type_name="NGeneric", json_key="constraints"),
    ],
)

nc_array_of_property_descriptor = TypeDesc(
    package="nmos",
    name="NcArrayOfPropertyDescriptor",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NcPropertyDescriptorValue]", json_key="-"),
    ],
)

nc_method_descriptor = TypeDesc(
    package="nmos",
    name="NcMethodDescriptor",
    members=[
        MemberDesc(name="Base", type_name="NcDescriptor", embedded=True),
        MemberDesc(name="Id", type_name="NcMethodId", json_key="id"),
        MemberDesc(name="Name", type_name="NString", json_key="name"),
        MemberDesc(name="ResultDataType", type_name="NString", json_key="resultDatatype"),
        MemberDesc(name="IsDeprecated", type_name="NBool", json_key="isDeprecated"),
        MemberDesc(name="Parameters", type_name="NcArrayOfParameterDescriptor", json_key="parameters"),
    ],
)

nc_array_of_method_descriptor = TypeDesc(
    package="nmos",
    name="NcArrayOfMethodDescriptor",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NcMethodDescriptorValue]", json_key="-"),
    ],
)

nc_parameter_descriptor = TypeDesc(
    package="nmos",
    name="NcParameterDescriptor",
    members=[
        MemberDesc(name="Base", type_name="NcDescriptor", embedded=True),
        MemberDesc(name="Name", type_name="NString", json_key="name"),
        MemberDesc(name="TypeName", type_name="NNullString", json_key="typeName"),
        MemberDesc(name="IsNullable", type_name="NBool", json_key="isNullable"),
        MemberDesc(name="IsSequence", type_name="NBool", json_key="isSequence"),
        MemberDesc(name="Constraints", type_name="NGeneric", json_key="constraints"),
    ],
)

nc_array_of_parameter_descriptor = TypeDesc(
    package="nmos",
    name="NcArrayOfParameterDescriptor",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NcParameterDescriptorValue]", json_key="-"),
    ],
)

nc_event_descriptor = TypeDesc(
    package="nmos",
    name="NcEventDescriptor",
    members=[
        MemberDesc(name="Base", type_name="NcDescriptor", embedded=True),
        MemberDesc(name="Id", type_name="NcPropertyId", json_key="id"),
        MemberDesc(name="Name", type_name="NString", json_key="name"),
        MemberDesc(name="EventDataType", type_name="NString", json_key="eventDatatype"),
        MemberDesc(name="IsDeprecated", type_name="NBool", json_key="isDeprecated"),
    ],
)

nc_array_of_event_descriptor = TypeDesc(
    package="nmos",
    name="NcArrayOfEventDescriptor",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NcEventDescriptorValue]", json_key="-"),
    ],
)

mv_alert_manager = TypeDesc(
    package="nmos",
    name="MvAlertManager",
    members=[
        MemberDesc(name="Base", type_name="NcManager", embedded=True),
        MemberDesc(name="AlertPeriod", type_name="NInt", json_key="alertPeriod"),
        MemberDesc(name="RefreshPeriod", type_name="NInt", json_key="refreshPeriod"),
        MemberDesc(name="ClearPeriod", type_name="NInt", json_key="clearPeriod"),
        MemberDesc(name="AlertCapabilities", type_name="MvArrayOfAlertCapabilityDescriptor", json_key="alertCapabilities"),
        MemberDesc(name="AlertDescriptors", type_name="MvArrayOfAlertDescriptor", json_key="alertDescriptors"),
        MemberDesc(name="Alert", type_name="MvAlertEventData", json_key="alert"),
    ],
)

mv_alert_descriptor = TypeDesc(
    package="nmos",
    name="MvAlertDescriptor",
    members=[
        MemberDesc(name="Enabled", type_name="NBool", json_key="enabled"),
        MemberDesc(name="AlertDomain", type_name="NInt", json_key="alertDomain"),
        MemberDesc(name="AlertScope", type_name="NInt", json_key="alertScope"),
        MemberDesc(name="ResourceIds", type_name="NArrayOfString", json_key="resourceIds"),
        MemberDesc(name="InterfaceNames", type_name="NArrayOfString", json_key="interfaceNames"),
        MemberDesc(name="Events", type_name="NArrayOfInt", json_key="events"),
        MemberDesc(name="EventCounters", type_name="MvArrayOfEventCounter", json_key="-"),
        MemberDesc(name="Active", type_name="NBool", json_key="-"),
    ],
)

mv_array_of_alert_descriptor = TypeDesc(
    package="nmos",
    name="MvArrayOfAlertDescriptor",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[MvAlertDescriptorValue]", json_key="-"),
    ],
)

mv_alert_capability_descriptor = TypeDesc(
    package="nmos",
    name="MvAlertCapabilityDescriptor",
    members=[
        MemberDesc(name="AlertDomain", type_name="NInt", json_key="alertDomain"),
        MemberDesc(name="AlertScope", type_name="NInt", json_key="alertScope"),
        MemberDesc(name="ResourceIds", type_name="NArrayOfString", json_key="resourceIds"),
        MemberDesc(name="InterfaceNames", type_name="NArrayOfString", json_key="interfaceNames"),
        MemberDesc(name="Events", type_name="NArrayOfInt", json_key="events"),
    ],
)

mv_array_of_alert_capability_descriptor = TypeDesc(
    package="nmos",
    name="MvArrayOfAlertCapabilityDescriptor",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[MvAlertCapabilityDescriptorValue]", json_key="-"),
    ],
)

mv_alert_event_data = TypeDesc(
    package="nmos",
    name="MvAlertEventData",
    members=[
        MemberDesc(name="AlertDescriptorIndex", type_name="NInt", json_key="alertDescriptorIndex"),
        MemberDesc(name="AlertDescriptor", type_name="MvAlertDescriptor", json_key="alertDescriptor"),
        MemberDesc(name="EventCounter", type_name="MvEventCounter", json_key="eventCounter"),
    ],
)

mv_event_counter = TypeDesc(
    package="nmos",
    name="MvEventCounter",
    members=[
        MemberDesc(name="Event", type_name="NInt", json_key="event"),
        MemberDesc(name="EventCounter", type_name="NInt", json_key="eventCounter"),
        MemberDesc(name="EventState", type_name="NInt", json_key="eventState"),
        MemberDesc(name="EventInfo", type_name="NString", json_key="eventInfo"),
        MemberDesc(name="InterfaceName", type_name="NString", json_key="interfaceName"),
    ],
)

mv_array_of_event_counter = TypeDesc(
    package="nmos",
    name="MvArrayOfEventCounter",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[MvEventCounterValue]", json_key="-"),
    ],
)

mv_active_alert = TypeDesc(
    package="nmos",
    name="MvActiveAlert",
    members=[
        MemberDesc(name="AlertDescriptorIndex", type_name="NInt", json_key="alertDescriptorIndex"),
        MemberDesc(name="AlertDescriptor", type_name="MvAlertDescriptor", json_key="alertDescriptor"),
        MemberDesc(name="EventCounters", type_name="MvArrayOfEventCounter", json_key="eventCounters"),
    ],
)

mv_array_of_active_alert = TypeDesc(
    package="nmos",
    name="MvArrayOfActiveAlert",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[MvActiveAlertValue]", json_key="-"),
    ],
)

nc_counter = TypeDesc(
    package="nmos",
    name="NcCounter",
    members=[
        MemberDesc(name="Name", type_name="NString", json_key="name"),
        MemberDesc(name="Count", type_name="NString", json_key="value"),
        MemberDesc(name="Description", type_name="NNullString", json_key="description"),
    ],
)

nc_array_of_counter = TypeDesc(
    package="nmos",
    name="NcArrayOfCounter",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NcCounterValue]", json_key="-"),
    ],
)

nc_status_monitor = TypeDesc(
    package="nmos",
    name="NcStatusMonitor",
    is_embedded=True,
    members=[
        MemberDesc(name="Base", type_name="NcWorker", embedded=True),
        MemberDesc(name="OverallStatus", type_name="NInt", json_key="overallStatus"),
        MemberDesc(name="OverallStatusMessage", type_name="NNullString", json_key="overallStatusMessage"),
        MemberDesc(name="StatusReportingDelay", type_name="NInt", json_key="statusReportingDelay"),
    ],
)

nc_receiver_monitor = TypeDesc(
    package="nmos",
    name="NcReceiverMonitor",
    is_embedded=True,
    members=[
        MemberDesc(name="Base", type_name="NcStatusMonitor", embedded=True),
        MemberDesc(name="LinkStatus", type_name="NInt", json_key="linkStatus"),
        MemberDesc(name="LinkStatusMessage", type_name="NNullString", json_key="linkStatusMessage"),
        MemberDesc(name="LinkStatusTransitionCounter", type_name="NInt", json_key="linkStatusTransitionCounter"),
        MemberDesc(name="ConnectionStatus", type_name="NInt", json_key="connectionStatus"),
        MemberDesc(name="ConnectionStatusMessage", type_name="NNullString", json_key="connectionStatusMessage"),
        MemberDesc(name="ConnectionStatusTransitionCounter", type_name="NInt", json_key="connectionStatusTransitionCounter"),
        MemberDesc(name="ExternalSynchronizationStatus", type_name="NInt", json_key="externalSynchronizationStatus"),
        MemberDesc(name="ExternalSynchronizationStatusMessage", type_name="NNullString", json_key="externalSynchronizationStatusMessage"),
        MemberDesc(name="ExternalSynchronizationStatusTransitionCounter", type_name="NInt", json_key="externalSynchronizationStatusTransitionCounter"),
        MemberDesc(name="StreamStatus", type_name="NInt", json_key="streamStatus"),
        MemberDesc(name="StreamStatusMessage", type_name="NNullString", json_key="streamStatusMessage"),
        MemberDesc(name="StreamStatusTransitionCounter", type_name="NInt", json_key="streamStatusTransitionCounter"),
        MemberDesc(name="SynchronizationSourceId", type_name="NNullString", json_key="synchronizationSourceId"),
        MemberDesc(name="AutoResetCountersAndMessages", type_name="NBool", json_key="autoResetCountersAndMessages", default='True'),
        MemberDesc(name="InternalLinkStatus", type_name="NInt", json_key="-"),
        MemberDesc(name="InternalConnectionStatus", type_name="NInt", json_key="-"),
        MemberDesc(name="InternalExternalSynchronizationStatus", type_name="NInt", json_key="-"),
        MemberDesc(name="InternalStreamStatus", type_name="NInt", json_key="-"),
        MemberDesc(name="InternalLinkStatusTime", type_name="NTime", json_key="-"),
        MemberDesc(name="InternalConnectionStatusTime", type_name="NTime", json_key="-"),
        MemberDesc(name="InternalExternalSynchronizationStatusTime", type_name="NTime", json_key="-"),
        MemberDesc(name="InternalStreamStatusTime", type_name="NTime", json_key="-"),
    ],
)

nc_sender_monitor = TypeDesc(
    package="nmos",
    name="NcSenderMonitor",
    is_embedded=True,
    members=[
        MemberDesc(name="Base", type_name="NcStatusMonitor", embedded=True),
        MemberDesc(name="LinkStatus", type_name="NInt", json_key="linkStatus"),
        MemberDesc(name="LinkStatusMessage", type_name="NNullString", json_key="linkStatusMessage"),
        MemberDesc(name="LinkStatusTransitionCounter", type_name="NInt", json_key="linkStatusTransitionCounter"),
        MemberDesc(name="TransmissionStatus", type_name="NInt", json_key="transmissionStatus"),
        MemberDesc(name="TransmissionStatusMessage", type_name="NNullString", json_key="transmissionStatusMessage"),
        MemberDesc(name="TransmissionStatusTransitionCounter", type_name="NInt", json_key="transmissionStatusTransitionCounter"),
        MemberDesc(name="ExternalSynchronizationStatus", type_name="NInt", json_key="externalSynchronizationStatus"),
        MemberDesc(name="ExternalSynchronizationStatusMessage", type_name="NNullString", json_key="externalSynchronizationStatusMessage"),
        MemberDesc(name="ExternalSynchronizationStatusTransitionCounter", type_name="NInt", json_key="externalSynchronizationStatusTransitionCounter"),
        MemberDesc(name="EssenceStatus", type_name="NInt", json_key="essenceStatus"),
        MemberDesc(name="EssenceStatusMessage", type_name="NNullString", json_key="essenceStatusMessage"),
        MemberDesc(name="EssenceStatusTransitionCounter", type_name="NInt", json_key="essenceStatusTransitionCounter"),
        MemberDesc(name="SynchronizationSourceId", type_name="NNullString", json_key="synchronizationSourceId"),
        MemberDesc(name="AutoResetCountersAndMessages", type_name="NBool", json_key="autoResetCountersAndMessages", default='True'),
        MemberDesc(name="InternalLinkStatus", type_name="NInt", json_key="-"),
        MemberDesc(name="InternalTransmissionStatus", type_name="NInt", json_key="-"),
        MemberDesc(name="InternalExternalSynchronizationStatus", type_name="NInt", json_key="-"),
        MemberDesc(name="InternalEssenceStatus", type_name="NInt", json_key="-"),
        MemberDesc(name="InternalLinkStatusTime", type_name="NTime", json_key="-"),
        MemberDesc(name="InternalTransmissionStatusTime", type_name="NTime", json_key="-"),
        MemberDesc(name="InternalExternalSynchronizationStatusTime", type_name="NTime", json_key="-"),
        MemberDesc(name="InternalEssenceStatusTime", type_name="NTime", json_key="-"),
    ],
)

ALL_TYPES = [
    nnc_ptr,
    nc_method_id,
    nc_event_id,
    nc_property_id,
    nc_command,
    nc_array_of_command,
    nc_response,
    nc_array_of_response,
    nc_result,
    nc_notification,
    nc_array_of_notification,
    nc_propertychanged_event,
    nc_property_changed_notification,
    nc_array_of_property_changed_notification,
    nc_command_message,
    nc_command_response_message,
    nc_notification_message,
    nc_subscription_message,
    nc_subscription_response_message,
    nc_error_message,
    nc_message,
    nc_object,
    nc_block,
    nc_descriptor,
    nc_block_member_descriptor,
    nc_array_of_block_member_descriptor,
    nc_worker,
    nc_manager,
    nc_device_manager,
    nc_manufacturer,
    nc_product,
    nc_device_operational_state,
    nc_class_manager,
    nc_class_descriptor,
    nc_array_of_class_descriptor,
    nc_property_descriptor,
    nc_array_of_property_descriptor,
    nc_method_descriptor,
    nc_array_of_method_descriptor,
    nc_parameter_descriptor,
    nc_array_of_parameter_descriptor,
    nc_event_descriptor,
    nc_array_of_event_descriptor,
    mv_alert_manager,
    mv_alert_descriptor,
    mv_array_of_alert_descriptor,
    mv_alert_capability_descriptor,
    mv_array_of_alert_capability_descriptor,
    mv_alert_event_data,
    mv_event_counter,
    mv_array_of_event_counter,
    mv_active_alert,
    mv_array_of_active_alert,
    nc_counter,
    nc_array_of_counter,
    nc_status_monitor,
    nc_receiver_monitor,
    nc_sender_monitor,
]
