"""Generated NMOS type: NRtpSenderTransportParams. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString, NNull, NBool, NEnum, NInt
from nmos.validators import CheckAutoPort

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NRtpSenderTransportParamsEnums:
    """JSON property name enums for NRtpSenderTransportParams."""
    SourceIp = EnumRegistry.get("source_ip")
    DestinationIp = EnumRegistry.get("destination_ip")
    SourcePort = EnumRegistry.get("source_port")
    DestinationPort = EnumRegistry.get("destination_port")
    FecEnabled = EnumRegistry.get("fec_enabled")
    FecDestinationIp = EnumRegistry.get("fec_destination_ip")
    FecType = EnumRegistry.get("fec_type")
    FecMode = EnumRegistry.get("fec_mode")
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
    ExtPrivacyProtocol = EnumRegistry.get("ext_privacy_protocol")
    ExtPrivacyMode = EnumRegistry.get("ext_privacy_mode")
    ExtPrivacyIV = EnumRegistry.get("ext_privacy_iv")
    ExtPrivacyKeyGenerator = EnumRegistry.get("ext_privacy_key_generator")
    ExtPrivacyKeyId = EnumRegistry.get("ext_privacy_key_id")
    ExtPrivacyKeyVersion = EnumRegistry.get("ext_privacy_key_version")
    ExtPrivacyEcdhSenderPublicKey = EnumRegistry.get("ext_privacy_ecdh_sender_public_key")
    ExtPrivacyEcdhReceiverPublicKey = EnumRegistry.get("ext_privacy_ecdh_receiver_public_key")
    ExtPrivacyEcdhCurve = EnumRegistry.get("ext_privacy_ecdh_curve")
    pass


class NRtpSenderTransportParamsValue:
    """Inner value struct for NRtpSenderTransportParams."""

    __slots__ = (
        "SourceIp",
        "DestinationIp",
        "SourcePort",
        "DestinationPort",
        "FecEnabled",
        "FecDestinationIp",
        "FecType",
        "FecMode",
        "FecBlockWidth",
        "FecBlockHeight",
        "Fec1DDestinationPort",
        "Fec2DDestinationPort",
        "Fec1DSourcePort",
        "Fec2DSourcePort",
        "RtcpEnabled",
        "RtcpDestinationIp",
        "RtcpDestinationPort",
        "RtcpSourcePort",
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
    )

    def __init__(self) -> None:
        self.SourceIp: NString = NString()
        self.DestinationIp: NString = NString()
        self.SourcePort: NNull = NNull()
        self.DestinationPort: NNull = NNull()
        self.FecEnabled: NBool = NBool()
        self.FecDestinationIp: NString = NString()
        self.FecType: NEnum = NEnum()
        self.FecMode: NEnum = NEnum()
        self.FecBlockWidth: NInt = NInt()
        self.FecBlockHeight: NInt = NInt()
        self.Fec1DDestinationPort: NNull = NNull()
        self.Fec2DDestinationPort: NNull = NNull()
        self.Fec1DSourcePort: NNull = NNull()
        self.Fec2DSourcePort: NNull = NNull()
        self.RtcpEnabled: NBool = NBool()
        self.RtcpDestinationIp: NString = NString()
        self.RtcpDestinationPort: NNull = NNull()
        self.RtcpSourcePort: NNull = NNull()
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

    def set_to_default(self) -> None:
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if self.SourcePort.defined:
            CheckAutoPort(self.SourcePort)
        if self.DestinationPort.defined:
            CheckAutoPort(self.DestinationPort)
        if self.Fec1DDestinationPort.defined:
            CheckAutoPort(self.Fec1DDestinationPort)
        if self.Fec2DDestinationPort.defined:
            CheckAutoPort(self.Fec2DDestinationPort)
        if self.Fec1DSourcePort.defined:
            CheckAutoPort(self.Fec1DSourcePort)
        if self.Fec2DSourcePort.defined:
            CheckAutoPort(self.Fec2DSourcePort)
        if self.RtcpDestinationPort.defined:
            CheckAutoPort(self.RtcpDestinationPort)
        if self.RtcpSourcePort.defined:
            CheckAutoPort(self.RtcpSourcePort)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.SourceIp.encode(engine, NRtpSenderTransportParamsEnums.SourceIp)
        self.DestinationIp.encode(engine, NRtpSenderTransportParamsEnums.DestinationIp)
        self.SourcePort.encode(engine, NRtpSenderTransportParamsEnums.SourcePort)
        self.DestinationPort.encode(engine, NRtpSenderTransportParamsEnums.DestinationPort)
        self.FecEnabled.encode(engine, NRtpSenderTransportParamsEnums.FecEnabled)
        self.FecDestinationIp.encode(engine, NRtpSenderTransportParamsEnums.FecDestinationIp)
        self.FecType.encode(engine, NRtpSenderTransportParamsEnums.FecType)
        self.FecMode.encode(engine, NRtpSenderTransportParamsEnums.FecMode)
        self.FecBlockWidth.encode(engine, NRtpSenderTransportParamsEnums.FecBlockWidth)
        self.FecBlockHeight.encode(engine, NRtpSenderTransportParamsEnums.FecBlockHeight)
        self.Fec1DDestinationPort.encode(engine, NRtpSenderTransportParamsEnums.Fec1DDestinationPort)
        self.Fec2DDestinationPort.encode(engine, NRtpSenderTransportParamsEnums.Fec2DDestinationPort)
        self.Fec1DSourcePort.encode(engine, NRtpSenderTransportParamsEnums.Fec1DSourcePort)
        self.Fec2DSourcePort.encode(engine, NRtpSenderTransportParamsEnums.Fec2DSourcePort)
        self.RtcpEnabled.encode(engine, NRtpSenderTransportParamsEnums.RtcpEnabled)
        self.RtcpDestinationIp.encode(engine, NRtpSenderTransportParamsEnums.RtcpDestinationIp)
        self.RtcpDestinationPort.encode(engine, NRtpSenderTransportParamsEnums.RtcpDestinationPort)
        self.RtcpSourcePort.encode(engine, NRtpSenderTransportParamsEnums.RtcpSourcePort)
        self.RtpEnabled.encode(engine, NRtpSenderTransportParamsEnums.RtpEnabled)
        self.ExtPrivacyProtocol.encode(engine, NRtpSenderTransportParamsEnums.ExtPrivacyProtocol)
        self.ExtPrivacyMode.encode(engine, NRtpSenderTransportParamsEnums.ExtPrivacyMode)
        self.ExtPrivacyIV.encode(engine, NRtpSenderTransportParamsEnums.ExtPrivacyIV)
        self.ExtPrivacyKeyGenerator.encode(engine, NRtpSenderTransportParamsEnums.ExtPrivacyKeyGenerator)
        self.ExtPrivacyKeyId.encode(engine, NRtpSenderTransportParamsEnums.ExtPrivacyKeyId)
        self.ExtPrivacyKeyVersion.encode(engine, NRtpSenderTransportParamsEnums.ExtPrivacyKeyVersion)
        self.ExtPrivacyEcdhSenderPublicKey.encode(engine, NRtpSenderTransportParamsEnums.ExtPrivacyEcdhSenderPublicKey)
        self.ExtPrivacyEcdhReceiverPublicKey.encode(engine, NRtpSenderTransportParamsEnums.ExtPrivacyEcdhReceiverPublicKey)
        self.ExtPrivacyEcdhCurve.encode(engine, NRtpSenderTransportParamsEnums.ExtPrivacyEcdhCurve)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NRtpSenderTransportParams")

        if NRtpSenderTransportParamsEnums.SourceIp.s in data:
            self.SourceIp.decode_value(data[NRtpSenderTransportParamsEnums.SourceIp.s])
        if NRtpSenderTransportParamsEnums.DestinationIp.s in data:
            self.DestinationIp.decode_value(data[NRtpSenderTransportParamsEnums.DestinationIp.s])
        if NRtpSenderTransportParamsEnums.SourcePort.s in data:
            self.SourcePort.decode_value(data[NRtpSenderTransportParamsEnums.SourcePort.s])
        if NRtpSenderTransportParamsEnums.DestinationPort.s in data:
            self.DestinationPort.decode_value(data[NRtpSenderTransportParamsEnums.DestinationPort.s])
        if NRtpSenderTransportParamsEnums.FecEnabled.s in data:
            self.FecEnabled.decode_value(data[NRtpSenderTransportParamsEnums.FecEnabled.s])
        if NRtpSenderTransportParamsEnums.FecDestinationIp.s in data:
            self.FecDestinationIp.decode_value(data[NRtpSenderTransportParamsEnums.FecDestinationIp.s])
        if NRtpSenderTransportParamsEnums.FecType.s in data:
            self.FecType.decode_value(data[NRtpSenderTransportParamsEnums.FecType.s])
        if NRtpSenderTransportParamsEnums.FecMode.s in data:
            self.FecMode.decode_value(data[NRtpSenderTransportParamsEnums.FecMode.s])
        if NRtpSenderTransportParamsEnums.FecBlockWidth.s in data:
            self.FecBlockWidth.decode_value(data[NRtpSenderTransportParamsEnums.FecBlockWidth.s])
        if NRtpSenderTransportParamsEnums.FecBlockHeight.s in data:
            self.FecBlockHeight.decode_value(data[NRtpSenderTransportParamsEnums.FecBlockHeight.s])
        if NRtpSenderTransportParamsEnums.Fec1DDestinationPort.s in data:
            self.Fec1DDestinationPort.decode_value(data[NRtpSenderTransportParamsEnums.Fec1DDestinationPort.s])
        if NRtpSenderTransportParamsEnums.Fec2DDestinationPort.s in data:
            self.Fec2DDestinationPort.decode_value(data[NRtpSenderTransportParamsEnums.Fec2DDestinationPort.s])
        if NRtpSenderTransportParamsEnums.Fec1DSourcePort.s in data:
            self.Fec1DSourcePort.decode_value(data[NRtpSenderTransportParamsEnums.Fec1DSourcePort.s])
        if NRtpSenderTransportParamsEnums.Fec2DSourcePort.s in data:
            self.Fec2DSourcePort.decode_value(data[NRtpSenderTransportParamsEnums.Fec2DSourcePort.s])
        if NRtpSenderTransportParamsEnums.RtcpEnabled.s in data:
            self.RtcpEnabled.decode_value(data[NRtpSenderTransportParamsEnums.RtcpEnabled.s])
        if NRtpSenderTransportParamsEnums.RtcpDestinationIp.s in data:
            self.RtcpDestinationIp.decode_value(data[NRtpSenderTransportParamsEnums.RtcpDestinationIp.s])
        if NRtpSenderTransportParamsEnums.RtcpDestinationPort.s in data:
            self.RtcpDestinationPort.decode_value(data[NRtpSenderTransportParamsEnums.RtcpDestinationPort.s])
        if NRtpSenderTransportParamsEnums.RtcpSourcePort.s in data:
            self.RtcpSourcePort.decode_value(data[NRtpSenderTransportParamsEnums.RtcpSourcePort.s])
        if NRtpSenderTransportParamsEnums.RtpEnabled.s in data:
            self.RtpEnabled.decode_value(data[NRtpSenderTransportParamsEnums.RtpEnabled.s])
        if NRtpSenderTransportParamsEnums.ExtPrivacyProtocol.s in data:
            self.ExtPrivacyProtocol.decode_value(data[NRtpSenderTransportParamsEnums.ExtPrivacyProtocol.s])
        if NRtpSenderTransportParamsEnums.ExtPrivacyMode.s in data:
            self.ExtPrivacyMode.decode_value(data[NRtpSenderTransportParamsEnums.ExtPrivacyMode.s])
        if NRtpSenderTransportParamsEnums.ExtPrivacyIV.s in data:
            self.ExtPrivacyIV.decode_value(data[NRtpSenderTransportParamsEnums.ExtPrivacyIV.s])
        if NRtpSenderTransportParamsEnums.ExtPrivacyKeyGenerator.s in data:
            self.ExtPrivacyKeyGenerator.decode_value(data[NRtpSenderTransportParamsEnums.ExtPrivacyKeyGenerator.s])
        if NRtpSenderTransportParamsEnums.ExtPrivacyKeyId.s in data:
            self.ExtPrivacyKeyId.decode_value(data[NRtpSenderTransportParamsEnums.ExtPrivacyKeyId.s])
        if NRtpSenderTransportParamsEnums.ExtPrivacyKeyVersion.s in data:
            self.ExtPrivacyKeyVersion.decode_value(data[NRtpSenderTransportParamsEnums.ExtPrivacyKeyVersion.s])
        if NRtpSenderTransportParamsEnums.ExtPrivacyEcdhSenderPublicKey.s in data:
            self.ExtPrivacyEcdhSenderPublicKey.decode_value(data[NRtpSenderTransportParamsEnums.ExtPrivacyEcdhSenderPublicKey.s])
        if NRtpSenderTransportParamsEnums.ExtPrivacyEcdhReceiverPublicKey.s in data:
            self.ExtPrivacyEcdhReceiverPublicKey.decode_value(data[NRtpSenderTransportParamsEnums.ExtPrivacyEcdhReceiverPublicKey.s])
        if NRtpSenderTransportParamsEnums.ExtPrivacyEcdhCurve.s in data:
            self.ExtPrivacyEcdhCurve.decode_value(data[NRtpSenderTransportParamsEnums.ExtPrivacyEcdhCurve.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NRtpSenderTransportParamsValue:
        o = NRtpSenderTransportParamsValue()
        o.SourceIp = self.SourceIp.clone()
        o.DestinationIp = self.DestinationIp.clone()
        o.SourcePort = self.SourcePort.clone()
        o.DestinationPort = self.DestinationPort.clone()
        o.FecEnabled = self.FecEnabled.clone()
        o.FecDestinationIp = self.FecDestinationIp.clone()
        o.FecType = self.FecType.clone()
        o.FecMode = self.FecMode.clone()
        o.FecBlockWidth = self.FecBlockWidth.clone()
        o.FecBlockHeight = self.FecBlockHeight.clone()
        o.Fec1DDestinationPort = self.Fec1DDestinationPort.clone()
        o.Fec2DDestinationPort = self.Fec2DDestinationPort.clone()
        o.Fec1DSourcePort = self.Fec1DSourcePort.clone()
        o.Fec2DSourcePort = self.Fec2DSourcePort.clone()
        o.RtcpEnabled = self.RtcpEnabled.clone()
        o.RtcpDestinationIp = self.RtcpDestinationIp.clone()
        o.RtcpDestinationPort = self.RtcpDestinationPort.clone()
        o.RtcpSourcePort = self.RtcpSourcePort.clone()
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
        return o


class NRtpSenderTransportParams:
    """Optional object type: NRtpSenderTransportParams."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NRtpSenderTransportParamsValue = NRtpSenderTransportParamsValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NRtpSenderTransportParamsValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NRtpSenderTransportParamsValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NRtpSenderTransportParamsValue | None = None) -> NRtpSenderTransportParamsValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_SourceIp(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SourceIp

    def set_SourceIp(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting SourceIp"
        _assign_value(self._value.SourceIp, v)

    def get_DestinationIp(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.DestinationIp

    def set_DestinationIp(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting DestinationIp"
        _assign_value(self._value.DestinationIp, v)

    def get_SourcePort(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SourcePort

    def set_SourcePort(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting SourcePort"
        _assign_value(self._value.SourcePort, v)

    def get_DestinationPort(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.DestinationPort

    def set_DestinationPort(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting DestinationPort"
        _assign_value(self._value.DestinationPort, v)

    def get_FecEnabled(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FecEnabled

    def set_FecEnabled(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting FecEnabled"
        _assign_value(self._value.FecEnabled, v)

    def get_FecDestinationIp(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FecDestinationIp

    def set_FecDestinationIp(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting FecDestinationIp"
        _assign_value(self._value.FecDestinationIp, v)

    def get_FecType(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FecType

    def set_FecType(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting FecType"
        _assign_value(self._value.FecType, v)

    def get_FecMode(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FecMode

    def set_FecMode(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting FecMode"
        _assign_value(self._value.FecMode, v)

    def get_FecBlockWidth(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FecBlockWidth

    def set_FecBlockWidth(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting FecBlockWidth"
        _assign_value(self._value.FecBlockWidth, v)

    def get_FecBlockHeight(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FecBlockHeight

    def set_FecBlockHeight(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting FecBlockHeight"
        _assign_value(self._value.FecBlockHeight, v)

    def get_Fec1DDestinationPort(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Fec1DDestinationPort

    def set_Fec1DDestinationPort(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting Fec1DDestinationPort"
        _assign_value(self._value.Fec1DDestinationPort, v)

    def get_Fec2DDestinationPort(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Fec2DDestinationPort

    def set_Fec2DDestinationPort(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting Fec2DDestinationPort"
        _assign_value(self._value.Fec2DDestinationPort, v)

    def get_Fec1DSourcePort(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Fec1DSourcePort

    def set_Fec1DSourcePort(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting Fec1DSourcePort"
        _assign_value(self._value.Fec1DSourcePort, v)

    def get_Fec2DSourcePort(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Fec2DSourcePort

    def set_Fec2DSourcePort(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting Fec2DSourcePort"
        _assign_value(self._value.Fec2DSourcePort, v)

    def get_RtcpEnabled(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.RtcpEnabled

    def set_RtcpEnabled(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting RtcpEnabled"
        _assign_value(self._value.RtcpEnabled, v)

    def get_RtcpDestinationIp(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.RtcpDestinationIp

    def set_RtcpDestinationIp(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting RtcpDestinationIp"
        _assign_value(self._value.RtcpDestinationIp, v)

    def get_RtcpDestinationPort(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.RtcpDestinationPort

    def set_RtcpDestinationPort(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting RtcpDestinationPort"
        _assign_value(self._value.RtcpDestinationPort, v)

    def get_RtcpSourcePort(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.RtcpSourcePort

    def set_RtcpSourcePort(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting RtcpSourcePort"
        _assign_value(self._value.RtcpSourcePort, v)

    def get_RtpEnabled(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.RtpEnabled

    def set_RtpEnabled(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting RtpEnabled"
        _assign_value(self._value.RtpEnabled, v)

    def get_ExtPrivacyProtocol(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyProtocol

    def set_ExtPrivacyProtocol(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting ExtPrivacyProtocol"
        _assign_value(self._value.ExtPrivacyProtocol, v)

    def get_ExtPrivacyMode(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyMode

    def set_ExtPrivacyMode(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting ExtPrivacyMode"
        _assign_value(self._value.ExtPrivacyMode, v)

    def get_ExtPrivacyIV(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyIV

    def set_ExtPrivacyIV(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting ExtPrivacyIV"
        _assign_value(self._value.ExtPrivacyIV, v)

    def get_ExtPrivacyKeyGenerator(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyKeyGenerator

    def set_ExtPrivacyKeyGenerator(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting ExtPrivacyKeyGenerator"
        _assign_value(self._value.ExtPrivacyKeyGenerator, v)

    def get_ExtPrivacyKeyId(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyKeyId

    def set_ExtPrivacyKeyId(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting ExtPrivacyKeyId"
        _assign_value(self._value.ExtPrivacyKeyId, v)

    def get_ExtPrivacyKeyVersion(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyKeyVersion

    def set_ExtPrivacyKeyVersion(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting ExtPrivacyKeyVersion"
        _assign_value(self._value.ExtPrivacyKeyVersion, v)

    def get_ExtPrivacyEcdhSenderPublicKey(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyEcdhSenderPublicKey

    def set_ExtPrivacyEcdhSenderPublicKey(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting ExtPrivacyEcdhSenderPublicKey"
        _assign_value(self._value.ExtPrivacyEcdhSenderPublicKey, v)

    def get_ExtPrivacyEcdhReceiverPublicKey(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyEcdhReceiverPublicKey

    def set_ExtPrivacyEcdhReceiverPublicKey(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting ExtPrivacyEcdhReceiverPublicKey"
        _assign_value(self._value.ExtPrivacyEcdhReceiverPublicKey, v)

    def get_ExtPrivacyEcdhCurve(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyEcdhCurve

    def set_ExtPrivacyEcdhCurve(self, v: Any) -> None:
        assert self._defined, "NRtpSenderTransportParams must be defined before setting ExtPrivacyEcdhCurve"
        _assign_value(self._value.ExtPrivacyEcdhCurve, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NRtpSenderTransportParamsValue()

    def clone(self) -> NRtpSenderTransportParams:
        o = NRtpSenderTransportParams()
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
            return f"NRtpSenderTransportParams(defined)"
        return "NRtpSenderTransportParams(<undefined>)"


def make_nrtpsendertransportparams_value(v: NRtpSenderTransportParamsValue) -> NRtpSenderTransportParamsValue:
    """Factory: create a NRtpSenderTransportParamsValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nrtpsendertransportparams(v: NRtpSenderTransportParamsValue) -> NRtpSenderTransportParams:
    """Factory: create a defined NRtpSenderTransportParams from a NRtpSenderTransportParamsValue."""
    o = NRtpSenderTransportParams()
    o.set_value(v)
    return o

