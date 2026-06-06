"""Generated NMOS type: NRtpReceiverTransportParams. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NNullString, NString, NNull, NBool, NEnum, NInt
from nmos.validators import CheckAutoPort

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NRtpReceiverTransportParamsEnums:
    """JSON property name enums for NRtpReceiverTransportParams."""
    SourceIp = EnumRegistry.get("source_ip")
    MulticastIp = EnumRegistry.get("multicast_ip")
    InterfaceIp = EnumRegistry.get("interface_ip")
    DestinationPort = EnumRegistry.get("destination_port")
    FecEnabled = EnumRegistry.get("fec_enabled")
    FecDestinationIp = EnumRegistry.get("fec_destination_ip")
    FecType = EnumRegistry.get("fec_type")
    FecMode = EnumRegistry.get("fec_mode")
    FecBlockWidth = EnumRegistry.get("fec_block_width")
    FecBlockHeight = EnumRegistry.get("fec_block_height")
    Fec1DDestinationPort = EnumRegistry.get("fec1D_destination_port")
    Fec2DDestinationPort = EnumRegistry.get("fec2D_destination_port")
    RtcpEnabled = EnumRegistry.get("rtcp_enabled")
    RtcpDestinationIp = EnumRegistry.get("rtcp_destination_ip")
    RtcpDestinationPort = EnumRegistry.get("rtcp_destination_port")
    RtpEnabled = EnumRegistry.get("rtp_enabled")
    ExtPrivacyProtocol = EnumRegistry.get("ext_privacy_protocol")
    ExtPrivacyMode = EnumRegistry.get("ext_privacy_mode")
    ExtPrivacyIV = EnumRegistry.get("ext_privacy_iv")
    ExtPrivacyKeyGenerator = EnumRegistry.get("ext_privacy_key_generator")
    ExtPrivacyKeyId = EnumRegistry.get("ext_privacy_key_id")
    ExtPrivacyKeyVersion = EnumRegistry.get("ext_privacy_key_version")
    ExtPrivacyEcdhSenderPublicKey = EnumRegistry.get("ext_privacy_ecdh_sender_public_key")
    ExtPrivacyEcdhReceiverPublicKey = EnumRegistry.get("ext_privacy_ecdh_receiver_public_key")
    ExtPrivacyEcdhCurve = EnumRegistry.get("ext_privacy_ecdh_curve")
    ExtAudioLayersMapping = EnumRegistry.get("ext_audio_layers_mapping")
    ExtVideoLayersMapping = EnumRegistry.get("ext_video_layers_mapping")
    ExtDataLayersMapping = EnumRegistry.get("ext_data_layers_mapping")
    pass


class NRtpReceiverTransportParamsValue:
    """Inner value struct for NRtpReceiverTransportParams."""

    __slots__ = (
        "SourceIp",
        "MulticastIp",
        "InterfaceIp",
        "DestinationPort",
        "FecEnabled",
        "FecDestinationIp",
        "FecType",
        "FecMode",
        "FecBlockWidth",
        "FecBlockHeight",
        "Fec1DDestinationPort",
        "Fec2DDestinationPort",
        "RtcpEnabled",
        "RtcpDestinationIp",
        "RtcpDestinationPort",
        "RtpEnabled",
        "ExtPrivacyProtocol",
        "ExtPrivacyMode",
        "ExtPrivacyIV",
        "ExtPrivacyKeyGenerator",
        "ExtPrivacyKeyId",
        "ExtPrivacyKeyVersion",
        "ExtPrivacyEcdhSenderPublicKey",
        "ExtPrivacyEcdhReceiverPublicKey",
        "ExtPrivacyEcdhCurve",
        "ExtAudioLayersMapping",
        "ExtVideoLayersMapping",
        "ExtDataLayersMapping",
    )

    def __init__(self) -> None:
        self.SourceIp: NNullString = NNullString()
        self.MulticastIp: NNullString = NNullString()
        self.InterfaceIp: NString = NString()
        self.DestinationPort: NNull = NNull()
        self.FecEnabled: NBool = NBool()
        self.FecDestinationIp: NString = NString()
        self.FecType: NEnum = NEnum()
        self.FecMode: NEnum = NEnum()
        self.FecBlockWidth: NInt = NInt()
        self.FecBlockHeight: NInt = NInt()
        self.Fec1DDestinationPort: NNull = NNull()
        self.Fec2DDestinationPort: NNull = NNull()
        self.RtcpEnabled: NBool = NBool()
        self.RtcpDestinationIp: NString = NString()
        self.RtcpDestinationPort: NNull = NNull()
        self.RtpEnabled: NBool = NBool()
        self.ExtPrivacyProtocol: NEnum = NEnum()
        self.ExtPrivacyMode: NEnum = NEnum()
        self.ExtPrivacyIV: NString = NString()
        self.ExtPrivacyKeyGenerator: NString = NString()
        self.ExtPrivacyKeyId: NString = NString()
        self.ExtPrivacyKeyVersion: NString = NString()
        self.ExtPrivacyEcdhSenderPublicKey: NString = NString()
        self.ExtPrivacyEcdhReceiverPublicKey: NString = NString()
        self.ExtPrivacyEcdhCurve: NEnum = NEnum()
        self.ExtAudioLayersMapping: NString = NString()
        self.ExtVideoLayersMapping: NString = NString()
        self.ExtDataLayersMapping: NString = NString()

    def set_to_default(self) -> None:
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if self.DestinationPort.defined:
            CheckAutoPort(self.DestinationPort)
        if self.Fec1DDestinationPort.defined:
            CheckAutoPort(self.Fec1DDestinationPort)
        if self.Fec2DDestinationPort.defined:
            CheckAutoPort(self.Fec2DDestinationPort)
        if self.RtcpDestinationPort.defined:
            CheckAutoPort(self.RtcpDestinationPort)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.SourceIp.encode(engine, NRtpReceiverTransportParamsEnums.SourceIp)
        self.MulticastIp.encode(engine, NRtpReceiverTransportParamsEnums.MulticastIp)
        self.InterfaceIp.encode(engine, NRtpReceiverTransportParamsEnums.InterfaceIp)
        self.DestinationPort.encode(engine, NRtpReceiverTransportParamsEnums.DestinationPort)
        self.FecEnabled.encode(engine, NRtpReceiverTransportParamsEnums.FecEnabled)
        self.FecDestinationIp.encode(engine, NRtpReceiverTransportParamsEnums.FecDestinationIp)
        self.FecType.encode(engine, NRtpReceiverTransportParamsEnums.FecType)
        self.FecMode.encode(engine, NRtpReceiverTransportParamsEnums.FecMode)
        self.FecBlockWidth.encode(engine, NRtpReceiverTransportParamsEnums.FecBlockWidth)
        self.FecBlockHeight.encode(engine, NRtpReceiverTransportParamsEnums.FecBlockHeight)
        self.Fec1DDestinationPort.encode(engine, NRtpReceiverTransportParamsEnums.Fec1DDestinationPort)
        self.Fec2DDestinationPort.encode(engine, NRtpReceiverTransportParamsEnums.Fec2DDestinationPort)
        self.RtcpEnabled.encode(engine, NRtpReceiverTransportParamsEnums.RtcpEnabled)
        self.RtcpDestinationIp.encode(engine, NRtpReceiverTransportParamsEnums.RtcpDestinationIp)
        self.RtcpDestinationPort.encode(engine, NRtpReceiverTransportParamsEnums.RtcpDestinationPort)
        self.RtpEnabled.encode(engine, NRtpReceiverTransportParamsEnums.RtpEnabled)
        self.ExtPrivacyProtocol.encode(engine, NRtpReceiverTransportParamsEnums.ExtPrivacyProtocol)
        self.ExtPrivacyMode.encode(engine, NRtpReceiverTransportParamsEnums.ExtPrivacyMode)
        self.ExtPrivacyIV.encode(engine, NRtpReceiverTransportParamsEnums.ExtPrivacyIV)
        self.ExtPrivacyKeyGenerator.encode(engine, NRtpReceiverTransportParamsEnums.ExtPrivacyKeyGenerator)
        self.ExtPrivacyKeyId.encode(engine, NRtpReceiverTransportParamsEnums.ExtPrivacyKeyId)
        self.ExtPrivacyKeyVersion.encode(engine, NRtpReceiverTransportParamsEnums.ExtPrivacyKeyVersion)
        self.ExtPrivacyEcdhSenderPublicKey.encode(engine, NRtpReceiverTransportParamsEnums.ExtPrivacyEcdhSenderPublicKey)
        self.ExtPrivacyEcdhReceiverPublicKey.encode(engine, NRtpReceiverTransportParamsEnums.ExtPrivacyEcdhReceiverPublicKey)
        self.ExtPrivacyEcdhCurve.encode(engine, NRtpReceiverTransportParamsEnums.ExtPrivacyEcdhCurve)
        self.ExtAudioLayersMapping.encode(engine, NRtpReceiverTransportParamsEnums.ExtAudioLayersMapping)
        self.ExtVideoLayersMapping.encode(engine, NRtpReceiverTransportParamsEnums.ExtVideoLayersMapping)
        self.ExtDataLayersMapping.encode(engine, NRtpReceiverTransportParamsEnums.ExtDataLayersMapping)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NRtpReceiverTransportParams")

        if NRtpReceiverTransportParamsEnums.SourceIp.s in data:
            self.SourceIp.decode_value(data[NRtpReceiverTransportParamsEnums.SourceIp.s])
        if NRtpReceiverTransportParamsEnums.MulticastIp.s in data:
            self.MulticastIp.decode_value(data[NRtpReceiverTransportParamsEnums.MulticastIp.s])
        if NRtpReceiverTransportParamsEnums.InterfaceIp.s in data:
            self.InterfaceIp.decode_value(data[NRtpReceiverTransportParamsEnums.InterfaceIp.s])
        if NRtpReceiverTransportParamsEnums.DestinationPort.s in data:
            self.DestinationPort.decode_value(data[NRtpReceiverTransportParamsEnums.DestinationPort.s])
        if NRtpReceiverTransportParamsEnums.FecEnabled.s in data:
            self.FecEnabled.decode_value(data[NRtpReceiverTransportParamsEnums.FecEnabled.s])
        if NRtpReceiverTransportParamsEnums.FecDestinationIp.s in data:
            self.FecDestinationIp.decode_value(data[NRtpReceiverTransportParamsEnums.FecDestinationIp.s])
        if NRtpReceiverTransportParamsEnums.FecType.s in data:
            self.FecType.decode_value(data[NRtpReceiverTransportParamsEnums.FecType.s])
        if NRtpReceiverTransportParamsEnums.FecMode.s in data:
            self.FecMode.decode_value(data[NRtpReceiverTransportParamsEnums.FecMode.s])
        if NRtpReceiverTransportParamsEnums.FecBlockWidth.s in data:
            self.FecBlockWidth.decode_value(data[NRtpReceiverTransportParamsEnums.FecBlockWidth.s])
        if NRtpReceiverTransportParamsEnums.FecBlockHeight.s in data:
            self.FecBlockHeight.decode_value(data[NRtpReceiverTransportParamsEnums.FecBlockHeight.s])
        if NRtpReceiverTransportParamsEnums.Fec1DDestinationPort.s in data:
            self.Fec1DDestinationPort.decode_value(data[NRtpReceiverTransportParamsEnums.Fec1DDestinationPort.s])
        if NRtpReceiverTransportParamsEnums.Fec2DDestinationPort.s in data:
            self.Fec2DDestinationPort.decode_value(data[NRtpReceiverTransportParamsEnums.Fec2DDestinationPort.s])
        if NRtpReceiverTransportParamsEnums.RtcpEnabled.s in data:
            self.RtcpEnabled.decode_value(data[NRtpReceiverTransportParamsEnums.RtcpEnabled.s])
        if NRtpReceiverTransportParamsEnums.RtcpDestinationIp.s in data:
            self.RtcpDestinationIp.decode_value(data[NRtpReceiverTransportParamsEnums.RtcpDestinationIp.s])
        if NRtpReceiverTransportParamsEnums.RtcpDestinationPort.s in data:
            self.RtcpDestinationPort.decode_value(data[NRtpReceiverTransportParamsEnums.RtcpDestinationPort.s])
        if NRtpReceiverTransportParamsEnums.RtpEnabled.s in data:
            self.RtpEnabled.decode_value(data[NRtpReceiverTransportParamsEnums.RtpEnabled.s])
        if NRtpReceiverTransportParamsEnums.ExtPrivacyProtocol.s in data:
            self.ExtPrivacyProtocol.decode_value(data[NRtpReceiverTransportParamsEnums.ExtPrivacyProtocol.s])
        if NRtpReceiverTransportParamsEnums.ExtPrivacyMode.s in data:
            self.ExtPrivacyMode.decode_value(data[NRtpReceiverTransportParamsEnums.ExtPrivacyMode.s])
        if NRtpReceiverTransportParamsEnums.ExtPrivacyIV.s in data:
            self.ExtPrivacyIV.decode_value(data[NRtpReceiverTransportParamsEnums.ExtPrivacyIV.s])
        if NRtpReceiverTransportParamsEnums.ExtPrivacyKeyGenerator.s in data:
            self.ExtPrivacyKeyGenerator.decode_value(data[NRtpReceiverTransportParamsEnums.ExtPrivacyKeyGenerator.s])
        if NRtpReceiverTransportParamsEnums.ExtPrivacyKeyId.s in data:
            self.ExtPrivacyKeyId.decode_value(data[NRtpReceiverTransportParamsEnums.ExtPrivacyKeyId.s])
        if NRtpReceiverTransportParamsEnums.ExtPrivacyKeyVersion.s in data:
            self.ExtPrivacyKeyVersion.decode_value(data[NRtpReceiverTransportParamsEnums.ExtPrivacyKeyVersion.s])
        if NRtpReceiverTransportParamsEnums.ExtPrivacyEcdhSenderPublicKey.s in data:
            self.ExtPrivacyEcdhSenderPublicKey.decode_value(data[NRtpReceiverTransportParamsEnums.ExtPrivacyEcdhSenderPublicKey.s])
        if NRtpReceiverTransportParamsEnums.ExtPrivacyEcdhReceiverPublicKey.s in data:
            self.ExtPrivacyEcdhReceiverPublicKey.decode_value(data[NRtpReceiverTransportParamsEnums.ExtPrivacyEcdhReceiverPublicKey.s])
        if NRtpReceiverTransportParamsEnums.ExtPrivacyEcdhCurve.s in data:
            self.ExtPrivacyEcdhCurve.decode_value(data[NRtpReceiverTransportParamsEnums.ExtPrivacyEcdhCurve.s])
        if NRtpReceiverTransportParamsEnums.ExtAudioLayersMapping.s in data:
            self.ExtAudioLayersMapping.decode_value(data[NRtpReceiverTransportParamsEnums.ExtAudioLayersMapping.s])
        if NRtpReceiverTransportParamsEnums.ExtVideoLayersMapping.s in data:
            self.ExtVideoLayersMapping.decode_value(data[NRtpReceiverTransportParamsEnums.ExtVideoLayersMapping.s])
        if NRtpReceiverTransportParamsEnums.ExtDataLayersMapping.s in data:
            self.ExtDataLayersMapping.decode_value(data[NRtpReceiverTransportParamsEnums.ExtDataLayersMapping.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NRtpReceiverTransportParamsValue:
        o = NRtpReceiverTransportParamsValue()
        o.SourceIp = self.SourceIp.clone()
        o.MulticastIp = self.MulticastIp.clone()
        o.InterfaceIp = self.InterfaceIp.clone()
        o.DestinationPort = self.DestinationPort.clone()
        o.FecEnabled = self.FecEnabled.clone()
        o.FecDestinationIp = self.FecDestinationIp.clone()
        o.FecType = self.FecType.clone()
        o.FecMode = self.FecMode.clone()
        o.FecBlockWidth = self.FecBlockWidth.clone()
        o.FecBlockHeight = self.FecBlockHeight.clone()
        o.Fec1DDestinationPort = self.Fec1DDestinationPort.clone()
        o.Fec2DDestinationPort = self.Fec2DDestinationPort.clone()
        o.RtcpEnabled = self.RtcpEnabled.clone()
        o.RtcpDestinationIp = self.RtcpDestinationIp.clone()
        o.RtcpDestinationPort = self.RtcpDestinationPort.clone()
        o.RtpEnabled = self.RtpEnabled.clone()
        o.ExtPrivacyProtocol = self.ExtPrivacyProtocol.clone()
        o.ExtPrivacyMode = self.ExtPrivacyMode.clone()
        o.ExtPrivacyIV = self.ExtPrivacyIV.clone()
        o.ExtPrivacyKeyGenerator = self.ExtPrivacyKeyGenerator.clone()
        o.ExtPrivacyKeyId = self.ExtPrivacyKeyId.clone()
        o.ExtPrivacyKeyVersion = self.ExtPrivacyKeyVersion.clone()
        o.ExtPrivacyEcdhSenderPublicKey = self.ExtPrivacyEcdhSenderPublicKey.clone()
        o.ExtPrivacyEcdhReceiverPublicKey = self.ExtPrivacyEcdhReceiverPublicKey.clone()
        o.ExtPrivacyEcdhCurve = self.ExtPrivacyEcdhCurve.clone()
        o.ExtAudioLayersMapping = self.ExtAudioLayersMapping.clone()
        o.ExtVideoLayersMapping = self.ExtVideoLayersMapping.clone()
        o.ExtDataLayersMapping = self.ExtDataLayersMapping.clone()
        return o


class NRtpReceiverTransportParams:
    """Optional object type: NRtpReceiverTransportParams."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NRtpReceiverTransportParamsValue = NRtpReceiverTransportParamsValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NRtpReceiverTransportParamsValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NRtpReceiverTransportParamsValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NRtpReceiverTransportParamsValue | None = None) -> NRtpReceiverTransportParamsValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_SourceIp(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SourceIp

    def set_SourceIp(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting SourceIp"
        _assign_value(self._value.SourceIp, v)

    def get_MulticastIp(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MulticastIp

    def set_MulticastIp(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting MulticastIp"
        _assign_value(self._value.MulticastIp, v)

    def get_InterfaceIp(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.InterfaceIp

    def set_InterfaceIp(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting InterfaceIp"
        _assign_value(self._value.InterfaceIp, v)

    def get_DestinationPort(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.DestinationPort

    def set_DestinationPort(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting DestinationPort"
        _assign_value(self._value.DestinationPort, v)

    def get_FecEnabled(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FecEnabled

    def set_FecEnabled(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting FecEnabled"
        _assign_value(self._value.FecEnabled, v)

    def get_FecDestinationIp(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FecDestinationIp

    def set_FecDestinationIp(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting FecDestinationIp"
        _assign_value(self._value.FecDestinationIp, v)

    def get_FecType(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FecType

    def set_FecType(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting FecType"
        _assign_value(self._value.FecType, v)

    def get_FecMode(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FecMode

    def set_FecMode(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting FecMode"
        _assign_value(self._value.FecMode, v)

    def get_FecBlockWidth(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FecBlockWidth

    def set_FecBlockWidth(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting FecBlockWidth"
        _assign_value(self._value.FecBlockWidth, v)

    def get_FecBlockHeight(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FecBlockHeight

    def set_FecBlockHeight(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting FecBlockHeight"
        _assign_value(self._value.FecBlockHeight, v)

    def get_Fec1DDestinationPort(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Fec1DDestinationPort

    def set_Fec1DDestinationPort(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting Fec1DDestinationPort"
        _assign_value(self._value.Fec1DDestinationPort, v)

    def get_Fec2DDestinationPort(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Fec2DDestinationPort

    def set_Fec2DDestinationPort(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting Fec2DDestinationPort"
        _assign_value(self._value.Fec2DDestinationPort, v)

    def get_RtcpEnabled(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.RtcpEnabled

    def set_RtcpEnabled(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting RtcpEnabled"
        _assign_value(self._value.RtcpEnabled, v)

    def get_RtcpDestinationIp(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.RtcpDestinationIp

    def set_RtcpDestinationIp(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting RtcpDestinationIp"
        _assign_value(self._value.RtcpDestinationIp, v)

    def get_RtcpDestinationPort(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.RtcpDestinationPort

    def set_RtcpDestinationPort(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting RtcpDestinationPort"
        _assign_value(self._value.RtcpDestinationPort, v)

    def get_RtpEnabled(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.RtpEnabled

    def set_RtpEnabled(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting RtpEnabled"
        _assign_value(self._value.RtpEnabled, v)

    def get_ExtPrivacyProtocol(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyProtocol

    def set_ExtPrivacyProtocol(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting ExtPrivacyProtocol"
        _assign_value(self._value.ExtPrivacyProtocol, v)

    def get_ExtPrivacyMode(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyMode

    def set_ExtPrivacyMode(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting ExtPrivacyMode"
        _assign_value(self._value.ExtPrivacyMode, v)

    def get_ExtPrivacyIV(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyIV

    def set_ExtPrivacyIV(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting ExtPrivacyIV"
        _assign_value(self._value.ExtPrivacyIV, v)

    def get_ExtPrivacyKeyGenerator(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyKeyGenerator

    def set_ExtPrivacyKeyGenerator(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting ExtPrivacyKeyGenerator"
        _assign_value(self._value.ExtPrivacyKeyGenerator, v)

    def get_ExtPrivacyKeyId(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyKeyId

    def set_ExtPrivacyKeyId(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting ExtPrivacyKeyId"
        _assign_value(self._value.ExtPrivacyKeyId, v)

    def get_ExtPrivacyKeyVersion(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyKeyVersion

    def set_ExtPrivacyKeyVersion(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting ExtPrivacyKeyVersion"
        _assign_value(self._value.ExtPrivacyKeyVersion, v)

    def get_ExtPrivacyEcdhSenderPublicKey(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyEcdhSenderPublicKey

    def set_ExtPrivacyEcdhSenderPublicKey(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting ExtPrivacyEcdhSenderPublicKey"
        _assign_value(self._value.ExtPrivacyEcdhSenderPublicKey, v)

    def get_ExtPrivacyEcdhReceiverPublicKey(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyEcdhReceiverPublicKey

    def set_ExtPrivacyEcdhReceiverPublicKey(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting ExtPrivacyEcdhReceiverPublicKey"
        _assign_value(self._value.ExtPrivacyEcdhReceiverPublicKey, v)

    def get_ExtPrivacyEcdhCurve(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyEcdhCurve

    def set_ExtPrivacyEcdhCurve(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting ExtPrivacyEcdhCurve"
        _assign_value(self._value.ExtPrivacyEcdhCurve, v)

    def get_ExtAudioLayersMapping(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtAudioLayersMapping

    def set_ExtAudioLayersMapping(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting ExtAudioLayersMapping"
        _assign_value(self._value.ExtAudioLayersMapping, v)

    def get_ExtVideoLayersMapping(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtVideoLayersMapping

    def set_ExtVideoLayersMapping(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting ExtVideoLayersMapping"
        _assign_value(self._value.ExtVideoLayersMapping, v)

    def get_ExtDataLayersMapping(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtDataLayersMapping

    def set_ExtDataLayersMapping(self, v: Any) -> None:
        assert self._defined, "NRtpReceiverTransportParams must be defined before setting ExtDataLayersMapping"
        _assign_value(self._value.ExtDataLayersMapping, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NRtpReceiverTransportParamsValue()

    def clone(self) -> NRtpReceiverTransportParams:
        o = NRtpReceiverTransportParams()
        o._defined = self._defined
        o._value = self._value.clone()
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if self._defined:
            self._value.encode(engine, name)

    def decode(self, engine: JsonEngine, data: Any) -> None:
        self._value.decode(engine, data)
        self._defined = True

    def decode_value(self, data: Any) -> None:
        """Decode from a parent dict value. Creates minimal engine context."""
        self._value.decode(JsonEngine(), data)
        self._defined = True

    def __repr__(self) -> str:
        if self._defined:
            return f"NRtpReceiverTransportParams(defined)"
        return "NRtpReceiverTransportParams(<undefined>)"


def make_nrtpreceivertransportparams_value(v: NRtpReceiverTransportParamsValue) -> NRtpReceiverTransportParamsValue:
    """Factory: create a NRtpReceiverTransportParamsValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nrtpreceivertransportparams(v: NRtpReceiverTransportParamsValue) -> NRtpReceiverTransportParams:
    """Factory: create a defined NRtpReceiverTransportParams from a NRtpReceiverTransportParamsValue."""
    o = NRtpReceiverTransportParams()
    o.set_value(v)
    return o

