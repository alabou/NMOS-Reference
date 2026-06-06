# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Type definitions auto-generated from Go source."""

from __future__ import annotations

from nmos.codegen.descriptors import MemberDesc, TypeDesc

nrtp_transport_constraints = TypeDesc(
    package="nmos",
    name="NRtpTransportConstraints",
    members=[
        MemberDesc(name="Constraints", type_name="NTransportConstraints", embedded=True, assertion="CheckRtpTransportConstraints"),
    ],
)

narray_of_rtp_transport_constraints = TypeDesc(
    package="nmos",
    name="NArrayOfRtpTransportConstraints",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NRtpTransportConstraintsValue]", json_key="-"),
    ],
)

nrtp_tcp_transport_constraints = TypeDesc(
    package="nmos",
    name="NRtpTcpTransportConstraints",
    members=[
        MemberDesc(name="Constraints", type_name="NTransportConstraints", embedded=True, assertion="CheckRtpTcpTransportConstraints"),
    ],
)

narray_of_rtp_tcp_transport_constraints = TypeDesc(
    package="nmos",
    name="NArrayOfRtpTcpTransportConstraints",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NRtpTcpTransportConstraintsValue]", json_key="-"),
    ],
)

nmqtt_transport_constraints = TypeDesc(
    package="nmos",
    name="NMqttTransportConstraints",
    members=[
        MemberDesc(name="Constraints", type_name="NTransportConstraints", embedded=True, assertion="CheckMqttTransportConstraints"),
    ],
)

narray_of_mqtt_transport_constraints = TypeDesc(
    package="nmos",
    name="NArrayOfMqttTransportConstraints",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NMqttTransportConstraintsValue]", json_key="-"),
    ],
)

nweb_socket_transport_constraints = TypeDesc(
    package="nmos",
    name="NWebSocketTransportConstraints",
    members=[
        MemberDesc(name="Constraints", type_name="NTransportConstraints", embedded=True, assertion="CheckWebSocketTransportConstraints"),
    ],
)

narray_of_web_socket_transport_constraints = TypeDesc(
    package="nmos",
    name="NArrayOfWebSocketTransportConstraints",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NWebSocketTransportConstraintsValue]", json_key="-"),
    ],
)

nndi_transport_constraints = TypeDesc(
    package="nmos",
    name="NNdiTransportConstraints",
    members=[
        MemberDesc(name="Constraints", type_name="NTransportConstraints", embedded=True, assertion="CheckNdiTransportConstraints"),
    ],
)

narray_of_ndi_transport_constraints = TypeDesc(
    package="nmos",
    name="NArrayOfNdiTransportConstraints",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NNdiTransportConstraintsValue]", json_key="-"),
    ],
)

nsrt_transport_constraints = TypeDesc(
    package="nmos",
    name="NSrtTransportConstraints",
    members=[
        MemberDesc(name="Constraints", type_name="NTransportConstraints", embedded=True, assertion="CheckSrtTransportConstraints"),
    ],
)

narray_of_srt_transport_constraints = TypeDesc(
    package="nmos",
    name="NArrayOfSrtTransportConstraints",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NSrtTransportConstraintsValue]", json_key="-"),
    ],
)

nusb_transport_constraints = TypeDesc(
    package="nmos",
    name="NUsbTransportConstraints",
    members=[
        MemberDesc(name="Constraints", type_name="NTransportConstraints", embedded=True, assertion="CheckUsbTransportConstraints"),
    ],
)

narray_of_usb_transport_constraints = TypeDesc(
    package="nmos",
    name="NArrayOfUsbTransportConstraints",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NUsbTransportConstraintsValue]", json_key="-"),
    ],
)

nrtsp_transport_constraints = TypeDesc(
    package="nmos",
    name="NRtspTransportConstraints",
    members=[
        MemberDesc(name="Constraints", type_name="NTransportConstraints", embedded=True, assertion="CheckRtspTransportConstraints"),
    ],
)

narray_of_rtsp_transport_constraints = TypeDesc(
    package="nmos",
    name="NArrayOfRtspTransportConstraints",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NRtspTransportConstraintsValue]", json_key="-"),
    ],
)

nudp_transport_constraints = TypeDesc(
    package="nmos",
    name="NUdpTransportConstraints",
    members=[
        MemberDesc(name="Constraints", type_name="NTransportConstraints", embedded=True, assertion="CheckUdpTransportConstraints"),
    ],
)

narray_of_udp_transport_constraints = TypeDesc(
    package="nmos",
    name="NArrayOfUdpTransportConstraints",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NUdpTransportConstraintsValue]", json_key="-"),
    ],
)

nrtp_sender_transport_params = TypeDesc(
    package="nmos",
    name="NRtpSenderTransportParams",
    members=[
        MemberDesc(name="SourceIp", type_name="NString", json_key="source_ip", optional=True),
        MemberDesc(name="DestinationIp", type_name="NString", json_key="destination_ip", optional=True),
        MemberDesc(name="SourcePort", type_name="NNull", json_key="source_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="DestinationPort", type_name="NNull", json_key="destination_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="FecEnabled", type_name="NBool", json_key="fec_enabled", optional=True),
        MemberDesc(name="FecDestinationIp", type_name="NString", json_key="fec_destination_ip", optional=True),
        MemberDesc(name="FecType", type_name="NEnum", json_key="fec_type", optional=True),
        MemberDesc(name="FecMode", type_name="NEnum", json_key="fec_mode", optional=True),
        MemberDesc(name="FecBlockWidth", type_name="NInt", json_key="fec_block_width", optional=True),
        MemberDesc(name="FecBlockHeight", type_name="NInt", json_key="fec_block_height", optional=True),
        MemberDesc(name="Fec1DDestinationPort", type_name="NNull", json_key="fec1D_destination_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="Fec2DDestinationPort", type_name="NNull", json_key="fec2D_destination_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="Fec1DSourcePort", type_name="NNull", json_key="fec1D_source_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="Fec2DSourcePort", type_name="NNull", json_key="fec2D_source_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="RtcpEnabled", type_name="NBool", json_key="rtcp_enabled", optional=True),
        MemberDesc(name="RtcpDestinationIp", type_name="NString", json_key="rtcp_destination_ip", optional=True),
        MemberDesc(name="RtcpDestinationPort", type_name="NNull", json_key="rtcp_destination_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="RtcpSourcePort", type_name="NNull", json_key="rtcp_source_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="RtpEnabled", type_name="NBool", json_key="rtp_enabled", optional=True),
        MemberDesc(name="ExtPrivacyProtocol", type_name="NEnum", json_key="ext_privacy_protocol", optional=True),
        MemberDesc(name="ExtPrivacyMode", type_name="NEnum", json_key="ext_privacy_mode", optional=True),
        MemberDesc(name="ExtPrivacyIV", type_name="NString", json_key="ext_privacy_iv", optional=True),
        MemberDesc(name="ExtPrivacyKeyGenerator", type_name="NString", json_key="ext_privacy_key_generator", optional=True),
        MemberDesc(name="ExtPrivacyKeyId", type_name="NString", json_key="ext_privacy_key_id", optional=True),
        MemberDesc(name="ExtPrivacyKeyVersion", type_name="NString", json_key="ext_privacy_key_version", optional=True),
        MemberDesc(name="ExtPrivacyEcdhSenderPublicKey", type_name="NString", json_key="ext_privacy_ecdh_sender_public_key", optional=True),
        MemberDesc(name="ExtPrivacyEcdhReceiverPublicKey", type_name="NString", json_key="ext_privacy_ecdh_receiver_public_key", optional=True),
        MemberDesc(name="ExtPrivacyEcdhCurve", type_name="NEnum", json_key="ext_privacy_ecdh_curve", optional=True),
    ],
)

narray_of_rtp_sender_transport_params = TypeDesc(
    package="nmos",
    name="NArrayOfRtpSenderTransportParams",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NRtpSenderTransportParamsValue]", json_key="-"),
    ],
)

nrtp_receiver_transport_params = TypeDesc(
    package="nmos",
    name="NRtpReceiverTransportParams",
    members=[
        MemberDesc(name="SourceIp", type_name="NNullString", json_key="source_ip", optional=True),
        MemberDesc(name="MulticastIp", type_name="NNullString", json_key="multicast_ip", optional=True),
        MemberDesc(name="InterfaceIp", type_name="NString", json_key="interface_ip", optional=True),
        MemberDesc(name="DestinationPort", type_name="NNull", json_key="destination_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="FecEnabled", type_name="NBool", json_key="fec_enabled", optional=True),
        MemberDesc(name="FecDestinationIp", type_name="NString", json_key="fec_destination_ip", optional=True),
        MemberDesc(name="FecType", type_name="NEnum", json_key="fec_type", optional=True),
        MemberDesc(name="FecMode", type_name="NEnum", json_key="fec_mode", optional=True),
        MemberDesc(name="FecBlockWidth", type_name="NInt", json_key="fec_block_width", optional=True),
        MemberDesc(name="FecBlockHeight", type_name="NInt", json_key="fec_block_height", optional=True),
        MemberDesc(name="Fec1DDestinationPort", type_name="NNull", json_key="fec1D_destination_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="Fec2DDestinationPort", type_name="NNull", json_key="fec2D_destination_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="RtcpEnabled", type_name="NBool", json_key="rtcp_enabled", optional=True),
        MemberDesc(name="RtcpDestinationIp", type_name="NString", json_key="rtcp_destination_ip", optional=True),
        MemberDesc(name="RtcpDestinationPort", type_name="NNull", json_key="rtcp_destination_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="RtpEnabled", type_name="NBool", json_key="rtp_enabled", optional=True),
        MemberDesc(name="ExtPrivacyProtocol", type_name="NEnum", json_key="ext_privacy_protocol", optional=True),
        MemberDesc(name="ExtPrivacyMode", type_name="NEnum", json_key="ext_privacy_mode", optional=True),
        MemberDesc(name="ExtPrivacyIV", type_name="NString", json_key="ext_privacy_iv", optional=True),
        MemberDesc(name="ExtPrivacyKeyGenerator", type_name="NString", json_key="ext_privacy_key_generator", optional=True),
        MemberDesc(name="ExtPrivacyKeyId", type_name="NString", json_key="ext_privacy_key_id", optional=True),
        MemberDesc(name="ExtPrivacyKeyVersion", type_name="NString", json_key="ext_privacy_key_version", optional=True),
        MemberDesc(name="ExtPrivacyEcdhSenderPublicKey", type_name="NString", json_key="ext_privacy_ecdh_sender_public_key", optional=True),
        MemberDesc(name="ExtPrivacyEcdhReceiverPublicKey", type_name="NString", json_key="ext_privacy_ecdh_receiver_public_key", optional=True),
        MemberDesc(name="ExtPrivacyEcdhCurve", type_name="NEnum", json_key="ext_privacy_ecdh_curve", optional=True),
        MemberDesc(name="ExtAudioLayersMapping", type_name="NString", json_key="ext_audio_layers_mapping", optional=True),
        MemberDesc(name="ExtVideoLayersMapping", type_name="NString", json_key="ext_video_layers_mapping", optional=True),
        MemberDesc(name="ExtDataLayersMapping", type_name="NString", json_key="ext_data_layers_mapping", optional=True),
    ],
)

narray_of_rtp_receiver_transport_params = TypeDesc(
    package="nmos",
    name="NArrayOfRtpReceiverTransportParams",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NRtpReceiverTransportParamsValue]", json_key="-"),
    ],
)

nrtp_tcp_sender_transport_params = TypeDesc(
    package="nmos",
    name="NRtpTcpSenderTransportParams",
    members=[
        MemberDesc(name="SourceIp", type_name="NString", json_key="source_ip", optional=True),
        MemberDesc(name="SourcePort", type_name="NNull", json_key="source_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="RtcpEnabled", type_name="NBool", json_key="rtcp_enabled", optional=True),
        MemberDesc(name="RtcpSourcePort", type_name="NNull", json_key="rtcp_source_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="RtpEnabled", type_name="NBool", json_key="rtp_enabled", optional=True),
        MemberDesc(name="ExtPrivacyProtocol", type_name="NEnum", json_key="ext_privacy_protocol", optional=True),
        MemberDesc(name="ExtPrivacyMode", type_name="NEnum", json_key="ext_privacy_mode", optional=True),
        MemberDesc(name="ExtPrivacyIV", type_name="NString", json_key="ext_privacy_iv", optional=True),
        MemberDesc(name="ExtPrivacyKeyGenerator", type_name="NString", json_key="ext_privacy_key_generator", optional=True),
        MemberDesc(name="ExtPrivacyKeyId", type_name="NString", json_key="ext_privacy_key_id", optional=True),
        MemberDesc(name="ExtPrivacyKeyVersion", type_name="NString", json_key="ext_privacy_key_version", optional=True),
        MemberDesc(name="ExtPrivacyEcdhSenderPublicKey", type_name="NString", json_key="ext_privacy_ecdh_sender_public_key", optional=True),
        MemberDesc(name="ExtPrivacyEcdhReceiverPublicKey", type_name="NString", json_key="ext_privacy_ecdh_receiver_public_key", optional=True),
        MemberDesc(name="ExtPrivacyEcdhCurve", type_name="NEnum", json_key="ext_privacy_ecdh_curve", optional=True),
    ],
)

narray_of_rtp_tcp_sender_transport_params = TypeDesc(
    package="nmos",
    name="NArrayOfRtpTcpSenderTransportParams",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NRtpTcpSenderTransportParamsValue]", json_key="-"),
    ],
)

nrtp_tcp_receiver_transport_params = TypeDesc(
    package="nmos",
    name="NRtpTcpReceiverTransportParams",
    members=[
        MemberDesc(name="SourceIp", type_name="NNullString", json_key="source_ip", optional=True),
        MemberDesc(name="InterfaceIp", type_name="NString", json_key="interface_ip", optional=True),
        MemberDesc(name="SourcePort", type_name="NNull", json_key="source_port", optional=True, assertion="CheckNullPort"),
        MemberDesc(name="RtcpEnabled", type_name="NBool", json_key="rtcp_enabled", optional=True),
        MemberDesc(name="RtcpSourcePort", type_name="NNull", json_key="rtcp_source_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="RtpEnabled", type_name="NBool", json_key="rtp_enabled", optional=True),
        MemberDesc(name="ExtPrivacyProtocol", type_name="NEnum", json_key="ext_privacy_protocol", optional=True),
        MemberDesc(name="ExtPrivacyMode", type_name="NEnum", json_key="ext_privacy_mode", optional=True),
        MemberDesc(name="ExtPrivacyIV", type_name="NString", json_key="ext_privacy_iv", optional=True),
        MemberDesc(name="ExtPrivacyKeyGenerator", type_name="NString", json_key="ext_privacy_key_generator", optional=True),
        MemberDesc(name="ExtPrivacyKeyId", type_name="NString", json_key="ext_privacy_key_id", optional=True),
        MemberDesc(name="ExtPrivacyKeyVersion", type_name="NString", json_key="ext_privacy_key_version", optional=True),
        MemberDesc(name="ExtPrivacyEcdhSenderPublicKey", type_name="NString", json_key="ext_privacy_ecdh_sender_public_key", optional=True),
        MemberDesc(name="ExtPrivacyEcdhReceiverPublicKey", type_name="NString", json_key="ext_privacy_ecdh_receiver_public_key", optional=True),
        MemberDesc(name="ExtPrivacyEcdhCurve", type_name="NEnum", json_key="ext_privacy_ecdh_curve", optional=True),
        MemberDesc(name="ExtAudioLayersMapping", type_name="NString", json_key="ext_audio_layers_mapping", optional=True),
        MemberDesc(name="ExtVideoLayersMapping", type_name="NString", json_key="ext_video_layers_mapping", optional=True),
        MemberDesc(name="ExtDataLayersMapping", type_name="NString", json_key="ext_data_layers_mapping", optional=True),
    ],
)

narray_of_rtp_tcp_receiver_transport_params = TypeDesc(
    package="nmos",
    name="NArrayOfRtpTcpReceiverTransportParams",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NRtpTcpReceiverTransportParamsValue]", json_key="-"),
    ],
)

nudp_sender_transport_params = TypeDesc(
    package="nmos",
    name="NUdpSenderTransportParams",
    members=[
        MemberDesc(name="SourceIp", type_name="NString", json_key="source_ip", optional=True),
        MemberDesc(name="DestinationIp", type_name="NString", json_key="destination_ip", optional=True),
        MemberDesc(name="SourcePort", type_name="NNull", json_key="source_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="DestinationPort", type_name="NNull", json_key="destination_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="FecEnabled", type_name="NBool", json_key="fec_enabled", optional=True),
        MemberDesc(name="FecDestinationIp", type_name="NString", json_key="fec_destination_ip", optional=True),
        MemberDesc(name="FecType", type_name="NEnum", json_key="fec_type", optional=True),
        MemberDesc(name="FecMode", type_name="NEnum", json_key="fec_mode", optional=True),
        MemberDesc(name="FecBlockWidth", type_name="NInt", json_key="fec_block_width", optional=True),
        MemberDesc(name="FecBlockHeight", type_name="NInt", json_key="fec_block_height", optional=True),
        MemberDesc(name="Fec1DDestinationPort", type_name="NNull", json_key="fec1D_destination_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="Fec2DDestinationPort", type_name="NNull", json_key="fec2D_destination_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="Fec1DSourcePort", type_name="NNull", json_key="fec1D_source_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="Fec2DSourcePort", type_name="NNull", json_key="fec2D_source_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="Enabled", type_name="NBool", json_key="enabled", optional=True),
        MemberDesc(name="ExtPrivacyProtocol", type_name="NEnum", json_key="ext_privacy_protocol", optional=True),
        MemberDesc(name="ExtPrivacyMode", type_name="NEnum", json_key="ext_privacy_mode", optional=True),
        MemberDesc(name="ExtPrivacyIV", type_name="NString", json_key="ext_privacy_iv", optional=True),
        MemberDesc(name="ExtPrivacyKeyGenerator", type_name="NString", json_key="ext_privacy_key_generator", optional=True),
        MemberDesc(name="ExtPrivacyKeyId", type_name="NString", json_key="ext_privacy_key_id", optional=True),
        MemberDesc(name="ExtPrivacyKeyVersion", type_name="NString", json_key="ext_privacy_key_version", optional=True),
        MemberDesc(name="ExtPrivacyEcdhSenderPublicKey", type_name="NString", json_key="ext_privacy_ecdh_sender_public_key", optional=True),
        MemberDesc(name="ExtPrivacyEcdhReceiverPublicKey", type_name="NString", json_key="ext_privacy_ecdh_receiver_public_key", optional=True),
        MemberDesc(name="ExtPrivacyEcdhCurve", type_name="NEnum", json_key="ext_privacy_ecdh_curve", optional=True),
    ],
)

narray_of_udp_sender_transport_params = TypeDesc(
    package="nmos",
    name="NArrayOfUdpSenderTransportParams",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NUdpSenderTransportParamsValue]", json_key="-"),
    ],
)

nudp_receiver_transport_params = TypeDesc(
    package="nmos",
    name="NUdpReceiverTransportParams",
    members=[
        MemberDesc(name="SourceIp", type_name="NNullString", json_key="source_ip", optional=True),
        MemberDesc(name="MulticastIp", type_name="NNullString", json_key="multicast_ip", optional=True),
        MemberDesc(name="InterfaceIp", type_name="NString", json_key="interface_ip", optional=True),
        MemberDesc(name="DestinationPort", type_name="NNull", json_key="destination_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="FecEnabled", type_name="NBool", json_key="fec_enabled", optional=True),
        MemberDesc(name="FecDestinationIp", type_name="NString", json_key="fec_destination_ip", optional=True),
        MemberDesc(name="FecType", type_name="NEnum", json_key="fec_type", optional=True),
        MemberDesc(name="FecMode", type_name="NEnum", json_key="fec_mode", optional=True),
        MemberDesc(name="FecBlockWidth", type_name="NInt", json_key="fec_block_width", optional=True),
        MemberDesc(name="FecBlockHeight", type_name="NInt", json_key="fec_block_height", optional=True),
        MemberDesc(name="Fec1DDestinationPort", type_name="NNull", json_key="fec1D_destination_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="Fec2DDestinationPort", type_name="NNull", json_key="fec2D_destination_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="Enabled", type_name="NBool", json_key="enabled", optional=True),
        MemberDesc(name="ExtPrivacyProtocol", type_name="NEnum", json_key="ext_privacy_protocol", optional=True),
        MemberDesc(name="ExtPrivacyMode", type_name="NEnum", json_key="ext_privacy_mode", optional=True),
        MemberDesc(name="ExtPrivacyIV", type_name="NString", json_key="ext_privacy_iv", optional=True),
        MemberDesc(name="ExtPrivacyKeyGenerator", type_name="NString", json_key="ext_privacy_key_generator", optional=True),
        MemberDesc(name="ExtPrivacyKeyId", type_name="NString", json_key="ext_privacy_key_id", optional=True),
        MemberDesc(name="ExtPrivacyKeyVersion", type_name="NString", json_key="ext_privacy_key_version", optional=True),
        MemberDesc(name="ExtPrivacyEcdhSenderPublicKey", type_name="NString", json_key="ext_privacy_ecdh_sender_public_key", optional=True),
        MemberDesc(name="ExtPrivacyEcdhReceiverPublicKey", type_name="NString", json_key="ext_privacy_ecdh_receiver_public_key", optional=True),
        MemberDesc(name="ExtPrivacyEcdhCurve", type_name="NEnum", json_key="ext_privacy_ecdh_curve", optional=True),
        MemberDesc(name="ExtAudioLayersMapping", type_name="NString", json_key="ext_audio_layers_mapping", optional=True),
        MemberDesc(name="ExtVideoLayersMapping", type_name="NString", json_key="ext_video_layers_mapping", optional=True),
        MemberDesc(name="ExtDataLayersMapping", type_name="NString", json_key="ext_data_layers_mapping", optional=True),
    ],
)

narray_of_udp_receiver_transport_params = TypeDesc(
    package="nmos",
    name="NArrayOfUdpReceiverTransportParams",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NUdpReceiverTransportParamsValue]", json_key="-"),
    ],
)

nmqtt_sender_transport_params = TypeDesc(
    package="nmos",
    name="NMqttSenderTransportParams",
    members=[
        MemberDesc(name="DestinationHost", type_name="NNullString", json_key="destination_host", optional=True),
        MemberDesc(name="DestinationPort", type_name="NNull", json_key="destination_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="BrokerProtocol", type_name="NEnum", json_key="broker_protocol", optional=True),
        MemberDesc(name="BrokerAuthorization", type_name="NNull", json_key="broker_authorization", optional=True, assertion="CheckAutoBool"),
        MemberDesc(name="BrokerTopic", type_name="NNullString", json_key="broker_topic", optional=True),
        MemberDesc(name="ConnectionStatusBrokerTopic", type_name="NNullString", json_key="connection_status_broker_topic", optional=True),
        MemberDesc(name="InterfaceIp", type_name="NString", json_key="-", optional=True),
    ],
)

narray_of_mqtt_sender_transport_params = TypeDesc(
    package="nmos",
    name="NArrayOfMqttSenderTransportParams",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NMqttSenderTransportParamsValue]", json_key="-"),
    ],
)

nmqtt_receiver_transport_params = TypeDesc(
    package="nmos",
    name="NMqttReceiverTransportParams",
    members=[
        MemberDesc(name="SourceHost", type_name="NNullString", json_key="source_host", optional=True),
        MemberDesc(name="SourcePort", type_name="NNull", json_key="source_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="BrokerProtocol", type_name="NEnum", json_key="broker_protocol", optional=True),
        MemberDesc(name="BrokerAuthorization", type_name="NNull", json_key="broker_authorization", optional=True, assertion="CheckAutoBool"),
        MemberDesc(name="BrokerTopic", type_name="NNullString", json_key="broker_topic", optional=True),
        MemberDesc(name="ConnectionStatusBrokerTopic", type_name="NNullString", json_key="connection_status_broker_topic", optional=True),
        MemberDesc(name="InterfaceIp", type_name="NString", json_key="-", optional=True),
    ],
)

narray_of_mqtt_receiver_transport_params = TypeDesc(
    package="nmos",
    name="NArrayOfMqttReceiverTransportParams",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NMqttReceiverTransportParamsValue]", json_key="-"),
    ],
)

nweb_socket_sender_transport_params = TypeDesc(
    package="nmos",
    name="NWebSocketSenderTransportParams",
    members=[
        MemberDesc(name="ConnectionUri", type_name="NNullString", json_key="connection_uri", optional=True),
        MemberDesc(name="ConnectionAuthorization", type_name="NNull", json_key="connection_authorization", optional=True, assertion="CheckAutoBool"),
    ],
)

narray_of_web_socket_sender_transport_params = TypeDesc(
    package="nmos",
    name="NArrayOfWebSocketSenderTransportParams",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NWebSocketSenderTransportParamsValue]", json_key="-"),
    ],
)

nweb_socket_receiver_transport_params = TypeDesc(
    package="nmos",
    name="NWebSocketReceiverTransportParams",
    members=[
        MemberDesc(name="ConnectionUri", type_name="NNullString", json_key="connection_uri", optional=True),
        MemberDesc(name="ConnectionAuthorization", type_name="NNull", json_key="connection_authorization", optional=True, assertion="CheckAutoBool"),
    ],
)

narray_of_web_socket_receiver_transport_params = TypeDesc(
    package="nmos",
    name="NArrayOfWebSocketReceiverTransportParams",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NWebSocketReceiverTransportParamsValue]", json_key="-"),
    ],
)

nusb_sender_transport_params = TypeDesc(
    package="nmos",
    name="NUsbSenderTransportParams",
    members=[
        MemberDesc(name="SourceIp", type_name="NString", json_key="source_ip", optional=True),
        MemberDesc(name="SourcePort", type_name="NNull", json_key="source_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="ExtPrivacyProtocol", type_name="NEnum", json_key="ext_privacy_protocol", optional=True),
        MemberDesc(name="ExtPrivacyMode", type_name="NEnum", json_key="ext_privacy_mode", optional=True),
        MemberDesc(name="ExtPrivacyIV", type_name="NString", json_key="ext_privacy_iv", optional=True),
        MemberDesc(name="ExtPrivacyKeyGenerator", type_name="NString", json_key="ext_privacy_key_generator", optional=True),
        MemberDesc(name="ExtPrivacyKeyId", type_name="NString", json_key="ext_privacy_key_id", optional=True),
        MemberDesc(name="ExtPrivacyKeyVersion", type_name="NString", json_key="ext_privacy_key_version", optional=True),
        MemberDesc(name="ExtPrivacyEcdhSenderPublicKey", type_name="NString", json_key="ext_privacy_ecdh_sender_public_key", optional=True),
        MemberDesc(name="ExtPrivacyEcdhReceiverPublicKey", type_name="NString", json_key="ext_privacy_ecdh_receiver_public_key", optional=True),
        MemberDesc(name="ExtPrivacyEcdhCurve", type_name="NEnum", json_key="ext_privacy_ecdh_curve", optional=True),
    ],
)

narray_of_usb_sender_transport_params = TypeDesc(
    package="nmos",
    name="NArrayOfUsbSenderTransportParams",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NUsbSenderTransportParamsValue]", json_key="-"),
    ],
)

nusb_receiver_transport_params = TypeDesc(
    package="nmos",
    name="NUsbReceiverTransportParams",
    members=[
        MemberDesc(name="SourceIp", type_name="NNullString", json_key="source_ip", optional=True),
        MemberDesc(name="SourcePort", type_name="NNull", json_key="source_port", optional=True, assertion="CheckNullPort"),
        MemberDesc(name="InterfaceIp", type_name="NString", json_key="interface_ip", optional=True),
        MemberDesc(name="ExtPrivacyProtocol", type_name="NEnum", json_key="ext_privacy_protocol", optional=True),
        MemberDesc(name="ExtPrivacyMode", type_name="NEnum", json_key="ext_privacy_mode", optional=True),
        MemberDesc(name="ExtPrivacyIV", type_name="NString", json_key="ext_privacy_iv", optional=True),
        MemberDesc(name="ExtPrivacyKeyGenerator", type_name="NString", json_key="ext_privacy_key_generator", optional=True),
        MemberDesc(name="ExtPrivacyKeyId", type_name="NString", json_key="ext_privacy_key_id", optional=True),
        MemberDesc(name="ExtPrivacyKeyVersion", type_name="NString", json_key="ext_privacy_key_version", optional=True),
        MemberDesc(name="ExtPrivacyEcdhSenderPublicKey", type_name="NString", json_key="ext_privacy_ecdh_sender_public_key", optional=True),
        MemberDesc(name="ExtPrivacyEcdhReceiverPublicKey", type_name="NString", json_key="ext_privacy_ecdh_receiver_public_key", optional=True),
        MemberDesc(name="ExtPrivacyEcdhCurve", type_name="NEnum", json_key="ext_privacy_ecdh_curve", optional=True),
        MemberDesc(name="ExtAudioLayersMapping", type_name="NString", json_key="-", optional=True),
        MemberDesc(name="ExtVideoLayersMapping", type_name="NString", json_key="-", optional=True),
        MemberDesc(name="ExtDataLayersMapping", type_name="NString", json_key="-", optional=True),
    ],
)

narray_of_usb_receiver_transport_params = TypeDesc(
    package="nmos",
    name="NArrayOfUsbReceiverTransportParams",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NUsbReceiverTransportParamsValue]", json_key="-"),
    ],
)

nrtsp_sender_transport_params = TypeDesc(
    package="nmos",
    name="NRtspSenderTransportParams",
    members=[
        MemberDesc(name="SourceIp", type_name="NString", json_key="source_ip", optional=True),
        MemberDesc(name="SourcePort", type_name="NNull", json_key="source_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="ExtPrivacyProtocol", type_name="NEnum", json_key="ext_privacy_protocol", optional=True),
        MemberDesc(name="ExtPrivacyMode", type_name="NEnum", json_key="ext_privacy_mode", optional=True),
        MemberDesc(name="ExtPrivacyIV", type_name="NString", json_key="ext_privacy_iv", optional=True),
        MemberDesc(name="ExtPrivacyKeyGenerator", type_name="NString", json_key="ext_privacy_key_generator", optional=True),
        MemberDesc(name="ExtPrivacyKeyId", type_name="NString", json_key="ext_privacy_key_id", optional=True),
        MemberDesc(name="ExtPrivacyKeyVersion", type_name="NString", json_key="ext_privacy_key_version", optional=True),
        MemberDesc(name="ExtPrivacyEcdhSenderPublicKey", type_name="NString", json_key="ext_privacy_ecdh_sender_public_key", optional=True),
        MemberDesc(name="ExtPrivacyEcdhReceiverPublicKey", type_name="NString", json_key="ext_privacy_ecdh_receiver_public_key", optional=True),
        MemberDesc(name="ExtPrivacyEcdhCurve", type_name="NEnum", json_key="ext_privacy_ecdh_curve", optional=True),
    ],
)

narray_of_rtsp_sender_transport_params = TypeDesc(
    package="nmos",
    name="NArrayOfRtspSenderTransportParams",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NRtspSenderTransportParamsValue]", json_key="-"),
    ],
)

nrtsp_receiver_transport_params = TypeDesc(
    package="nmos",
    name="NRtspReceiverTransportParams",
    members=[
        MemberDesc(name="SourceIp", type_name="NNullString", json_key="source_ip", optional=True),
        MemberDesc(name="SourcePort", type_name="NNull", json_key="source_port", optional=True, assertion="CheckNullPort"),
        MemberDesc(name="InterfaceIp", type_name="NString", json_key="interface_ip", optional=True),
        MemberDesc(name="ExtPrivacyProtocol", type_name="NEnum", json_key="ext_privacy_protocol", optional=True),
        MemberDesc(name="ExtPrivacyMode", type_name="NEnum", json_key="ext_privacy_mode", optional=True),
        MemberDesc(name="ExtPrivacyIV", type_name="NString", json_key="ext_privacy_iv", optional=True),
        MemberDesc(name="ExtPrivacyKeyGenerator", type_name="NString", json_key="ext_privacy_key_generator", optional=True),
        MemberDesc(name="ExtPrivacyKeyId", type_name="NString", json_key="ext_privacy_key_id", optional=True),
        MemberDesc(name="ExtPrivacyKeyVersion", type_name="NString", json_key="ext_privacy_key_version", optional=True),
        MemberDesc(name="ExtPrivacyEcdhSenderPublicKey", type_name="NString", json_key="ext_privacy_ecdh_sender_public_key", optional=True),
        MemberDesc(name="ExtPrivacyEcdhReceiverPublicKey", type_name="NString", json_key="ext_privacy_ecdh_receiver_public_key", optional=True),
        MemberDesc(name="ExtPrivacyEcdhCurve", type_name="NEnum", json_key="ext_privacy_ecdh_curve", optional=True),
        MemberDesc(name="ExtAudioLayersMapping", type_name="NString", json_key="-", optional=True),
        MemberDesc(name="ExtVideoLayersMapping", type_name="NString", json_key="-", optional=True),
        MemberDesc(name="ExtDataLayersMapping", type_name="NString", json_key="-", optional=True),
    ],
)

narray_of_rtsp_receiver_transport_params = TypeDesc(
    package="nmos",
    name="NArrayOfRtspReceiverTransportParams",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NRtspReceiverTransportParamsValue]", json_key="-"),
    ],
)

nsrt_sender_transport_params = TypeDesc(
    package="nmos",
    name="NSrtSenderTransportParams",
    members=[
        MemberDesc(name="SourceIp", type_name="NString", json_key="source_ip", optional=True),
        MemberDesc(name="SourcePort", type_name="NNull", json_key="source_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="DestinationIp", type_name="NNullString", json_key="destination_ip", optional=True),
        MemberDesc(name="DestinationPort", type_name="NNull", json_key="destination_port", optional=True, assertion="CheckNullAutoPort"),
        MemberDesc(name="Protocol", type_name="NEnum", json_key="protocol", optional=True),
        MemberDesc(name="Latency", type_name="NInt", json_key="latency", optional=True),
        MemberDesc(name="StreamId", type_name="NNullString", json_key="stream_id", optional=True, default='None'),
        MemberDesc(name="ExtPrivacyProtocol", type_name="NEnum", json_key="ext_privacy_protocol", optional=True),
        MemberDesc(name="ExtPrivacyMode", type_name="NEnum", json_key="ext_privacy_mode", optional=True),
        MemberDesc(name="ExtPrivacyIV", type_name="NString", json_key="ext_privacy_iv", optional=True),
        MemberDesc(name="ExtPrivacyKeyGenerator", type_name="NString", json_key="ext_privacy_key_generator", optional=True),
        MemberDesc(name="ExtPrivacyKeyId", type_name="NString", json_key="ext_privacy_key_id", optional=True),
        MemberDesc(name="ExtPrivacyKeyVersion", type_name="NString", json_key="ext_privacy_key_version", optional=True),
        MemberDesc(name="ExtPrivacyEcdhSenderPublicKey", type_name="NString", json_key="ext_privacy_ecdh_sender_public_key", optional=True),
        MemberDesc(name="ExtPrivacyEcdhReceiverPublicKey", type_name="NString", json_key="ext_privacy_ecdh_receiver_public_key", optional=True),
        MemberDesc(name="ExtPrivacyEcdhCurve", type_name="NEnum", json_key="ext_privacy_ecdh_curve", optional=True),
    ],
)

narray_of_srt_sender_transport_params = TypeDesc(
    package="nmos",
    name="NArrayOfSrtSenderTransportParams",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NSrtSenderTransportParamsValue]", json_key="-"),
    ],
)

nsrt_receiver_transport_params = TypeDesc(
    package="nmos",
    name="NSrtReceiverTransportParams",
    members=[
        MemberDesc(name="SourceIp", type_name="NNullString", json_key="source_ip", optional=True),
        MemberDesc(name="SourcePort", type_name="NNull", json_key="source_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="DestinationIp", type_name="NString", json_key="destination_ip", optional=True),
        MemberDesc(name="DestinationPort", type_name="NNull", json_key="destination_port", optional=True, assertion="CheckNullAutoPort"),
        MemberDesc(name="Protocol", type_name="NEnum", json_key="protocol", optional=True),
        MemberDesc(name="Latency", type_name="NInt", json_key="latency", optional=True),
        MemberDesc(name="StreamId", type_name="NNullString", json_key="stream_id", optional=True, default='None'),
        MemberDesc(name="ExtPrivacyProtocol", type_name="NEnum", json_key="ext_privacy_protocol", optional=True),
        MemberDesc(name="ExtPrivacyMode", type_name="NEnum", json_key="ext_privacy_mode", optional=True),
        MemberDesc(name="ExtPrivacyIV", type_name="NString", json_key="ext_privacy_iv", optional=True),
        MemberDesc(name="ExtPrivacyKeyGenerator", type_name="NString", json_key="ext_privacy_key_generator", optional=True),
        MemberDesc(name="ExtPrivacyKeyId", type_name="NString", json_key="ext_privacy_key_id", optional=True),
        MemberDesc(name="ExtPrivacyKeyVersion", type_name="NString", json_key="ext_privacy_key_version", optional=True),
        MemberDesc(name="ExtPrivacyEcdhSenderPublicKey", type_name="NString", json_key="ext_privacy_ecdh_sender_public_key", optional=True),
        MemberDesc(name="ExtPrivacyEcdhReceiverPublicKey", type_name="NString", json_key="ext_privacy_ecdh_receiver_public_key", optional=True),
        MemberDesc(name="ExtPrivacyEcdhCurve", type_name="NEnum", json_key="ext_privacy_ecdh_curve", optional=True),
        MemberDesc(name="ExtAudioLayersMapping", type_name="NString", json_key="ext_audio_layers_mapping", optional=True),
        MemberDesc(name="ExtVideoLayersMapping", type_name="NString", json_key="ext_video_layers_mapping", optional=True),
        MemberDesc(name="ExtDataLayersMapping", type_name="NString", json_key="ext_data_layers_mapping", optional=True),
    ],
)

narray_of_srt_receiver_transport_params = TypeDesc(
    package="nmos",
    name="NArrayOfSrtReceiverTransportParams",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NSrtReceiverTransportParamsValue]", json_key="-"),
    ],
)

nndi_sender_transport_params = TypeDesc(
    package="nmos",
    name="NNdiSenderTransportParams",
    members=[
        MemberDesc(name="ServerIp", type_name="NString", json_key="server_ip", optional=True),
        MemberDesc(name="ServerPort", type_name="NNull", json_key="server_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="SourceName", type_name="NString", json_key="source_name", optional=True),
        MemberDesc(name="GroupName", type_name="NNullString", json_key="group_name", optional=True),
    ],
)

nndi_sender_transport_params = TypeDesc(
    package="nmos",
    name="NNdiSenderTransportParams",
    members=[
        MemberDesc(name="SourceIp", type_name="NString", json_key="source_ip", optional=True),
        MemberDesc(name="SourcePort", type_name="NNull", json_key="source_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="SourceName", type_name="NString", json_key="source_name", optional=True),
        MemberDesc(name="MachineName", type_name="NString", json_key="machine_name", optional=True),
    ],
)

narray_of_ndi_sender_transport_params = TypeDesc(
    package="nmos",
    name="NArrayOfNdiSenderTransportParams",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NNdiSenderTransportParamsValue]", json_key="-"),
    ],
)

nndi_receiver_transport_params = TypeDesc(
    package="nmos",
    name="NNdiReceiverTransportParams",
    members=[
        MemberDesc(name="InterfaceIp", type_name="NString", json_key="interface_ip", optional=True),
        MemberDesc(name="ServerHost", type_name="NNullString", json_key="server_host", optional=True),
        MemberDesc(name="ServerPort", type_name="NNull", json_key="server_port", optional=True, assertion="CheckAutoPort"),
        MemberDesc(name="SourceName", type_name="NString", json_key="source_name", optional=True),
        MemberDesc(name="GroupName", type_name="NNullString", json_key="group_name", optional=True),
    ],
)

nndi_receiver_transport_params = TypeDesc(
    package="nmos",
    name="NNdiReceiverTransportParams",
    members=[
        MemberDesc(name="InterfaceIp", type_name="NString", json_key="interface_ip", optional=True),
        MemberDesc(name="SourceIp", type_name="NNullString", json_key="source_ip", optional=True),
        MemberDesc(name="SourcePort", type_name="NNull", json_key="source_port", optional=True, assertion="CheckNullPort"),
        MemberDesc(name="SourceName", type_name="NNullString", json_key="source_name", optional=True),
        MemberDesc(name="MachineName", type_name="NNullString", json_key="machine_name", optional=True),
    ],
)

narray_of_ndi_receiver_transport_params = TypeDesc(
    package="nmos",
    name="NArrayOfNdiReceiverTransportParams",
    is_array=True,
    members=[
        MemberDesc(name="value", type_name="list[NNdiReceiverTransportParamsValue]", json_key="-"),
    ],
)

nactivation = TypeDesc(
    package="nmos",
    name="NActivation",
    members=[
        MemberDesc(name="Mode", type_name="NNullString", json_key="mode", assertion="CheckActivationMode"),
        MemberDesc(name="RequestedTime", type_name="NNullString", json_key="requested_time", optional=True),
        MemberDesc(name="ActivationTime", type_name="NNullString", json_key="activation_time", optional=True),
    ],
)

nrtp_sender_activation = TypeDesc(
    package="nmos",
    name="NRtpSenderActivation",
    is_sealed=True,
    members=[
        MemberDesc(name="ReceiverId", type_name="NNullString", json_key="receiver_id", optional=True, assertion="CheckResourceIdNullableString"),
        MemberDesc(name="MasterEnable", type_name="NBool", json_key="master_enable", optional=True),
        MemberDesc(name="Activation", type_name="NActivation", json_key="activation", optional=True),
        MemberDesc(name="TransportParams", type_name="NArrayOfRtpSenderTransportParams", json_key="transport_params", optional=True),
    ],
)

nrtp_tcp_sender_activation = TypeDesc(
    package="nmos",
    name="NRtpTcpSenderActivation",
    is_sealed=True,
    members=[
        MemberDesc(name="ReceiverId", type_name="NNullString", json_key="receiver_id", optional=True, assertion="CheckResourceIdNullableString"),
        MemberDesc(name="MasterEnable", type_name="NBool", json_key="master_enable", optional=True),
        MemberDesc(name="Activation", type_name="NActivation", json_key="activation", optional=True),
        MemberDesc(name="TransportParams", type_name="NArrayOfRtpTcpSenderTransportParams", json_key="transport_params", optional=True),
    ],
)

nudp_sender_activation = TypeDesc(
    package="nmos",
    name="NUdpSenderActivation",
    is_sealed=True,
    members=[
        MemberDesc(name="ReceiverId", type_name="NNullString", json_key="receiver_id", optional=True, assertion="CheckResourceIdNullableString"),
        MemberDesc(name="MasterEnable", type_name="NBool", json_key="master_enable", optional=True),
        MemberDesc(name="Activation", type_name="NActivation", json_key="activation", optional=True),
        MemberDesc(name="TransportParams", type_name="NArrayOfUdpSenderTransportParams", json_key="transport_params", optional=True),
    ],
)

nmqtt_sender_activation = TypeDesc(
    package="nmos",
    name="NMqttSenderActivation",
    is_sealed=True,
    members=[
        MemberDesc(name="ReceiverId", type_name="NNullString", json_key="receiver_id", optional=True, assertion="CheckResourceIdNullableString"),
        MemberDesc(name="MasterEnable", type_name="NBool", json_key="master_enable", optional=True),
        MemberDesc(name="Activation", type_name="NActivation", json_key="activation", optional=True),
        MemberDesc(name="TransportParams", type_name="NArrayOfMqttSenderTransportParams", json_key="transport_params", optional=True),
    ],
)

nweb_socket_sender_activation = TypeDesc(
    package="nmos",
    name="NWebSocketSenderActivation",
    is_sealed=True,
    members=[
        MemberDesc(name="ReceiverId", type_name="NNullString", json_key="receiver_id", optional=True, assertion="CheckResourceIdNullableString"),
        MemberDesc(name="MasterEnable", type_name="NBool", json_key="master_enable", optional=True),
        MemberDesc(name="Activation", type_name="NActivation", json_key="activation", optional=True),
        MemberDesc(name="TransportParams", type_name="NArrayOfWebSocketSenderTransportParams", json_key="transport_params", optional=True),
    ],
)

nndi_sender_activation = TypeDesc(
    package="nmos",
    name="NNdiSenderActivation",
    is_sealed=True,
    members=[
        MemberDesc(name="ReceiverId", type_name="NNullString", json_key="receiver_id", optional=True, assertion="CheckResourceIdNullableString"),
        MemberDesc(name="MasterEnable", type_name="NBool", json_key="master_enable", optional=True),
        MemberDesc(name="Activation", type_name="NActivation", json_key="activation", optional=True),
        MemberDesc(name="TransportParams", type_name="NArrayOfNdiSenderTransportParams", json_key="transport_params", optional=True),
    ],
)

nsrt_sender_activation = TypeDesc(
    package="nmos",
    name="NSrtSenderActivation",
    is_sealed=True,
    members=[
        MemberDesc(name="ReceiverId", type_name="NNullString", json_key="receiver_id", optional=True, assertion="CheckResourceIdNullableString"),
        MemberDesc(name="MasterEnable", type_name="NBool", json_key="master_enable", optional=True),
        MemberDesc(name="Activation", type_name="NActivation", json_key="activation", optional=True),
        MemberDesc(name="TransportParams", type_name="NArrayOfSrtSenderTransportParams", json_key="transport_params", optional=True),
    ],
)

nusb_sender_activation = TypeDesc(
    package="nmos",
    name="NUsbSenderActivation",
    is_sealed=True,
    members=[
        MemberDesc(name="ReceiverId", type_name="NNullString", json_key="receiver_id", optional=True, assertion="CheckResourceIdNullableString"),
        MemberDesc(name="MasterEnable", type_name="NBool", json_key="master_enable", optional=True),
        MemberDesc(name="Activation", type_name="NActivation", json_key="activation", optional=True),
        MemberDesc(name="TransportParams", type_name="NArrayOfUsbSenderTransportParams", json_key="transport_params", optional=True),
    ],
)

nrtsp_sender_activation = TypeDesc(
    package="nmos",
    name="NRtspSenderActivation",
    is_sealed=True,
    members=[
        MemberDesc(name="ReceiverId", type_name="NNullString", json_key="receiver_id", optional=True, assertion="CheckResourceIdNullableString"),
        MemberDesc(name="MasterEnable", type_name="NBool", json_key="master_enable", optional=True),
        MemberDesc(name="Activation", type_name="NActivation", json_key="activation", optional=True),
        MemberDesc(name="TransportParams", type_name="NArrayOfRtspSenderTransportParams", json_key="transport_params", optional=True),
    ],
)

ntransport_file = TypeDesc(
    package="nmos",
    name="NTransportFile",
    members=[
        MemberDesc(name="Data", type_name="NNullString", json_key="data"),
        MemberDesc(name="Type", type_name="NNullString", json_key="type"),
    ],
)

nrtp_receiver_activation = TypeDesc(
    package="nmos",
    name="NRtpReceiverActivation",
    is_sealed=True,
    members=[
        MemberDesc(name="SenderId", type_name="NNullString", json_key="sender_id", optional=True, assertion="CheckResourceIdNullableString"),
        MemberDesc(name="MasterEnable", type_name="NBool", json_key="master_enable", optional=True),
        MemberDesc(name="Activation", type_name="NActivation", json_key="activation", optional=True),
        MemberDesc(name="TransportFile", type_name="NTransportFile", json_key="transport_file", optional=True),
        MemberDesc(name="TransportParams", type_name="NArrayOfRtpReceiverTransportParams", json_key="transport_params", optional=True),
    ],
)

nrtp_tcp_receiver_activation = TypeDesc(
    package="nmos",
    name="NRtpTcpReceiverActivation",
    is_sealed=True,
    members=[
        MemberDesc(name="SenderId", type_name="NNullString", json_key="sender_id", optional=True, assertion="CheckResourceIdNullableString"),
        MemberDesc(name="MasterEnable", type_name="NBool", json_key="master_enable", optional=True),
        MemberDesc(name="Activation", type_name="NActivation", json_key="activation", optional=True),
        MemberDesc(name="TransportFile", type_name="NTransportFile", json_key="transport_file", optional=True),
        MemberDesc(name="TransportParams", type_name="NArrayOfRtpTcpReceiverTransportParams", json_key="transport_params", optional=True),
    ],
)

nudp_receiver_activation = TypeDesc(
    package="nmos",
    name="NUdpReceiverActivation",
    is_sealed=True,
    members=[
        MemberDesc(name="SenderId", type_name="NNullString", json_key="sender_id", optional=True, assertion="CheckResourceIdNullableString"),
        MemberDesc(name="MasterEnable", type_name="NBool", json_key="master_enable", optional=True),
        MemberDesc(name="Activation", type_name="NActivation", json_key="activation", optional=True),
        MemberDesc(name="TransportFile", type_name="NTransportFile", json_key="transport_file", optional=True),
        MemberDesc(name="TransportParams", type_name="NArrayOfUdpReceiverTransportParams", json_key="transport_params", optional=True),
    ],
)

nmqtt_receiver_activation = TypeDesc(
    package="nmos",
    name="NMqttReceiverActivation",
    is_sealed=True,
    members=[
        MemberDesc(name="SenderId", type_name="NNullString", json_key="sender_id", optional=True, assertion="CheckResourceIdNullableString"),
        MemberDesc(name="MasterEnable", type_name="NBool", json_key="master_enable", optional=True),
        MemberDesc(name="Activation", type_name="NActivation", json_key="activation", optional=True),
        MemberDesc(name="TransportFile", type_name="NTransportFile", json_key="transport_file", optional=True),
        MemberDesc(name="TransportParams", type_name="NArrayOfMqttReceiverTransportParams", json_key="transport_params", optional=True),
    ],
)

nweb_socket_receiver_activation = TypeDesc(
    package="nmos",
    name="NWebSocketReceiverActivation",
    is_sealed=True,
    members=[
        MemberDesc(name="SenderId", type_name="NNullString", json_key="sender_id", optional=True, assertion="CheckResourceIdNullableString"),
        MemberDesc(name="MasterEnable", type_name="NBool", json_key="master_enable", optional=True),
        MemberDesc(name="Activation", type_name="NActivation", json_key="activation", optional=True),
        MemberDesc(name="TransportFile", type_name="NTransportFile", json_key="transport_file", optional=True),
        MemberDesc(name="TransportParams", type_name="NArrayOfWebSocketReceiverTransportParams", json_key="transport_params", optional=True),
    ],
)

nndi_receiver_activation = TypeDesc(
    package="nmos",
    name="NNdiReceiverActivation",
    is_sealed=True,
    members=[
        MemberDesc(name="SenderId", type_name="NNullString", json_key="sender_id", optional=True, assertion="CheckResourceIdNullableString"),
        MemberDesc(name="MasterEnable", type_name="NBool", json_key="master_enable", optional=True),
        MemberDesc(name="Activation", type_name="NActivation", json_key="activation", optional=True),
        MemberDesc(name="TransportFile", type_name="NTransportFile", json_key="transport_file", optional=True),
        MemberDesc(name="TransportParams", type_name="NArrayOfNdiReceiverTransportParams", json_key="transport_params", optional=True),
    ],
)

nsrt_receiver_activation = TypeDesc(
    package="nmos",
    name="NSrtReceiverActivation",
    is_sealed=True,
    members=[
        MemberDesc(name="SenderId", type_name="NNullString", json_key="sender_id", optional=True, assertion="CheckResourceIdNullableString"),
        MemberDesc(name="MasterEnable", type_name="NBool", json_key="master_enable", optional=True),
        MemberDesc(name="Activation", type_name="NActivation", json_key="activation", optional=True),
        MemberDesc(name="TransportFile", type_name="NTransportFile", json_key="transport_file", optional=True),
        MemberDesc(name="TransportParams", type_name="NArrayOfSrtReceiverTransportParams", json_key="transport_params", optional=True),
    ],
)

nusb_receiver_activation = TypeDesc(
    package="nmos",
    name="NUsbReceiverActivation",
    is_sealed=True,
    members=[
        MemberDesc(name="SenderId", type_name="NNullString", json_key="sender_id", optional=True, assertion="CheckResourceIdNullableString"),
        MemberDesc(name="MasterEnable", type_name="NBool", json_key="master_enable", optional=True),
        MemberDesc(name="Activation", type_name="NActivation", json_key="activation", optional=True),
        MemberDesc(name="TransportFile", type_name="NTransportFile", json_key="transport_file", optional=True),
        MemberDesc(name="TransportParams", type_name="NArrayOfUsbReceiverTransportParams", json_key="transport_params", optional=True),
    ],
)

nrtsp_receiver_activation = TypeDesc(
    package="nmos",
    name="NRtspReceiverActivation",
    is_sealed=True,
    members=[
        MemberDesc(name="SenderId", type_name="NNullString", json_key="sender_id", optional=True, assertion="CheckResourceIdNullableString"),
        MemberDesc(name="MasterEnable", type_name="NBool", json_key="master_enable", optional=True),
        MemberDesc(name="Activation", type_name="NActivation", json_key="activation", optional=True),
        MemberDesc(name="TransportFile", type_name="NTransportFile", json_key="transport_file", optional=True),
        MemberDesc(name="TransportParams", type_name="NArrayOfRtspReceiverTransportParams", json_key="transport_params", optional=True),
    ],
)

ALL_TYPES = [
    nrtp_transport_constraints,
    narray_of_rtp_transport_constraints,
    nrtp_tcp_transport_constraints,
    narray_of_rtp_tcp_transport_constraints,
    nmqtt_transport_constraints,
    narray_of_mqtt_transport_constraints,
    nweb_socket_transport_constraints,
    narray_of_web_socket_transport_constraints,
    nndi_transport_constraints,
    narray_of_ndi_transport_constraints,
    nsrt_transport_constraints,
    narray_of_srt_transport_constraints,
    nusb_transport_constraints,
    narray_of_usb_transport_constraints,
    nrtsp_transport_constraints,
    narray_of_rtsp_transport_constraints,
    nudp_transport_constraints,
    narray_of_udp_transport_constraints,
    nrtp_sender_transport_params,
    narray_of_rtp_sender_transport_params,
    nrtp_receiver_transport_params,
    narray_of_rtp_receiver_transport_params,
    nrtp_tcp_sender_transport_params,
    narray_of_rtp_tcp_sender_transport_params,
    nrtp_tcp_receiver_transport_params,
    narray_of_rtp_tcp_receiver_transport_params,
    nudp_sender_transport_params,
    narray_of_udp_sender_transport_params,
    nudp_receiver_transport_params,
    narray_of_udp_receiver_transport_params,
    nmqtt_sender_transport_params,
    narray_of_mqtt_sender_transport_params,
    nmqtt_receiver_transport_params,
    narray_of_mqtt_receiver_transport_params,
    nweb_socket_sender_transport_params,
    narray_of_web_socket_sender_transport_params,
    nweb_socket_receiver_transport_params,
    narray_of_web_socket_receiver_transport_params,
    nusb_sender_transport_params,
    narray_of_usb_sender_transport_params,
    nusb_receiver_transport_params,
    narray_of_usb_receiver_transport_params,
    nrtsp_sender_transport_params,
    narray_of_rtsp_sender_transport_params,
    nrtsp_receiver_transport_params,
    narray_of_rtsp_receiver_transport_params,
    nsrt_sender_transport_params,
    narray_of_srt_sender_transport_params,
    nsrt_receiver_transport_params,
    narray_of_srt_receiver_transport_params,
    nndi_sender_transport_params,
    nndi_sender_transport_params,
    narray_of_ndi_sender_transport_params,
    nndi_receiver_transport_params,
    nndi_receiver_transport_params,
    narray_of_ndi_receiver_transport_params,
    nactivation,
    nrtp_sender_activation,
    nrtp_tcp_sender_activation,
    nudp_sender_activation,
    nmqtt_sender_activation,
    nweb_socket_sender_activation,
    nndi_sender_activation,
    nsrt_sender_activation,
    nusb_sender_activation,
    nrtsp_sender_activation,
    ntransport_file,
    nrtp_receiver_activation,
    nrtp_tcp_receiver_activation,
    nudp_receiver_activation,
    nmqtt_receiver_activation,
    nweb_socket_receiver_activation,
    nndi_receiver_activation,
    nsrt_receiver_activation,
    nusb_receiver_activation,
    nrtsp_receiver_activation,
]

