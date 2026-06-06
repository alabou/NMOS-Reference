"""Generated NMOS type: NRtpTcpReceiverTransportParams. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NNullString, NString, NNull, NBool, NEnum
from nmos.validators import CheckNullPort, CheckAutoPort

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NRtpTcpReceiverTransportParamsEnums:
    """JSON property name enums for NRtpTcpReceiverTransportParams."""
    SourceIp = EnumRegistry.get("source_ip")
    InterfaceIp = EnumRegistry.get("interface_ip")
    SourcePort = EnumRegistry.get("source_port")
    RtcpEnabled = EnumRegistry.get("rtcp_enabled")
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
    ExtAudioLayersMapping = EnumRegistry.get("ext_audio_layers_mapping")
    ExtVideoLayersMapping = EnumRegistry.get("ext_video_layers_mapping")
    ExtDataLayersMapping = EnumRegistry.get("ext_data_layers_mapping")
    pass


class NRtpTcpReceiverTransportParamsValue:
    """Inner value struct for NRtpTcpReceiverTransportParams."""

    __slots__ = (
        "SourceIp",
        "InterfaceIp",
        "SourcePort",
        "RtcpEnabled",
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
        "ExtAudioLayersMapping",
        "ExtVideoLayersMapping",
        "ExtDataLayersMapping",
    )

    def __init__(self) -> None:
        self.SourceIp: NNullString = NNullString()
        self.InterfaceIp: NString = NString()
        self.SourcePort: NNull = NNull()
        self.RtcpEnabled: NBool = NBool()
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
        self.ExtAudioLayersMapping: NString = NString()
        self.ExtVideoLayersMapping: NString = NString()
        self.ExtDataLayersMapping: NString = NString()

    def set_to_default(self) -> None:
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if self.SourcePort.defined:
            CheckNullPort(self.SourcePort)
        if self.RtcpSourcePort.defined:
            CheckAutoPort(self.RtcpSourcePort)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.SourceIp.encode(engine, NRtpTcpReceiverTransportParamsEnums.SourceIp)
        self.InterfaceIp.encode(engine, NRtpTcpReceiverTransportParamsEnums.InterfaceIp)
        self.SourcePort.encode(engine, NRtpTcpReceiverTransportParamsEnums.SourcePort)
        self.RtcpEnabled.encode(engine, NRtpTcpReceiverTransportParamsEnums.RtcpEnabled)
        self.RtcpSourcePort.encode(engine, NRtpTcpReceiverTransportParamsEnums.RtcpSourcePort)
        self.RtpEnabled.encode(engine, NRtpTcpReceiverTransportParamsEnums.RtpEnabled)
        self.ExtPrivacyProtocol.encode(engine, NRtpTcpReceiverTransportParamsEnums.ExtPrivacyProtocol)
        self.ExtPrivacyMode.encode(engine, NRtpTcpReceiverTransportParamsEnums.ExtPrivacyMode)
        self.ExtPrivacyIV.encode(engine, NRtpTcpReceiverTransportParamsEnums.ExtPrivacyIV)
        self.ExtPrivacyKeyGenerator.encode(engine, NRtpTcpReceiverTransportParamsEnums.ExtPrivacyKeyGenerator)
        self.ExtPrivacyKeyId.encode(engine, NRtpTcpReceiverTransportParamsEnums.ExtPrivacyKeyId)
        self.ExtPrivacyKeyVersion.encode(engine, NRtpTcpReceiverTransportParamsEnums.ExtPrivacyKeyVersion)
        self.ExtPrivacyEcdhSenderPublicKey.encode(engine, NRtpTcpReceiverTransportParamsEnums.ExtPrivacyEcdhSenderPublicKey)
        self.ExtPrivacyEcdhReceiverPublicKey.encode(engine, NRtpTcpReceiverTransportParamsEnums.ExtPrivacyEcdhReceiverPublicKey)
        self.ExtPrivacyEcdhCurve.encode(engine, NRtpTcpReceiverTransportParamsEnums.ExtPrivacyEcdhCurve)
        self.ExtAudioLayersMapping.encode(engine, NRtpTcpReceiverTransportParamsEnums.ExtAudioLayersMapping)
        self.ExtVideoLayersMapping.encode(engine, NRtpTcpReceiverTransportParamsEnums.ExtVideoLayersMapping)
        self.ExtDataLayersMapping.encode(engine, NRtpTcpReceiverTransportParamsEnums.ExtDataLayersMapping)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NRtpTcpReceiverTransportParams")

        if NRtpTcpReceiverTransportParamsEnums.SourceIp.s in data:
            self.SourceIp.decode_value(data[NRtpTcpReceiverTransportParamsEnums.SourceIp.s])
        if NRtpTcpReceiverTransportParamsEnums.InterfaceIp.s in data:
            self.InterfaceIp.decode_value(data[NRtpTcpReceiverTransportParamsEnums.InterfaceIp.s])
        if NRtpTcpReceiverTransportParamsEnums.SourcePort.s in data:
            self.SourcePort.decode_value(data[NRtpTcpReceiverTransportParamsEnums.SourcePort.s])
        if NRtpTcpReceiverTransportParamsEnums.RtcpEnabled.s in data:
            self.RtcpEnabled.decode_value(data[NRtpTcpReceiverTransportParamsEnums.RtcpEnabled.s])
        if NRtpTcpReceiverTransportParamsEnums.RtcpSourcePort.s in data:
            self.RtcpSourcePort.decode_value(data[NRtpTcpReceiverTransportParamsEnums.RtcpSourcePort.s])
        if NRtpTcpReceiverTransportParamsEnums.RtpEnabled.s in data:
            self.RtpEnabled.decode_value(data[NRtpTcpReceiverTransportParamsEnums.RtpEnabled.s])
        if NRtpTcpReceiverTransportParamsEnums.ExtPrivacyProtocol.s in data:
            self.ExtPrivacyProtocol.decode_value(data[NRtpTcpReceiverTransportParamsEnums.ExtPrivacyProtocol.s])
        if NRtpTcpReceiverTransportParamsEnums.ExtPrivacyMode.s in data:
            self.ExtPrivacyMode.decode_value(data[NRtpTcpReceiverTransportParamsEnums.ExtPrivacyMode.s])
        if NRtpTcpReceiverTransportParamsEnums.ExtPrivacyIV.s in data:
            self.ExtPrivacyIV.decode_value(data[NRtpTcpReceiverTransportParamsEnums.ExtPrivacyIV.s])
        if NRtpTcpReceiverTransportParamsEnums.ExtPrivacyKeyGenerator.s in data:
            self.ExtPrivacyKeyGenerator.decode_value(data[NRtpTcpReceiverTransportParamsEnums.ExtPrivacyKeyGenerator.s])
        if NRtpTcpReceiverTransportParamsEnums.ExtPrivacyKeyId.s in data:
            self.ExtPrivacyKeyId.decode_value(data[NRtpTcpReceiverTransportParamsEnums.ExtPrivacyKeyId.s])
        if NRtpTcpReceiverTransportParamsEnums.ExtPrivacyKeyVersion.s in data:
            self.ExtPrivacyKeyVersion.decode_value(data[NRtpTcpReceiverTransportParamsEnums.ExtPrivacyKeyVersion.s])
        if NRtpTcpReceiverTransportParamsEnums.ExtPrivacyEcdhSenderPublicKey.s in data:
            self.ExtPrivacyEcdhSenderPublicKey.decode_value(data[NRtpTcpReceiverTransportParamsEnums.ExtPrivacyEcdhSenderPublicKey.s])
        if NRtpTcpReceiverTransportParamsEnums.ExtPrivacyEcdhReceiverPublicKey.s in data:
            self.ExtPrivacyEcdhReceiverPublicKey.decode_value(data[NRtpTcpReceiverTransportParamsEnums.ExtPrivacyEcdhReceiverPublicKey.s])
        if NRtpTcpReceiverTransportParamsEnums.ExtPrivacyEcdhCurve.s in data:
            self.ExtPrivacyEcdhCurve.decode_value(data[NRtpTcpReceiverTransportParamsEnums.ExtPrivacyEcdhCurve.s])
        if NRtpTcpReceiverTransportParamsEnums.ExtAudioLayersMapping.s in data:
            self.ExtAudioLayersMapping.decode_value(data[NRtpTcpReceiverTransportParamsEnums.ExtAudioLayersMapping.s])
        if NRtpTcpReceiverTransportParamsEnums.ExtVideoLayersMapping.s in data:
            self.ExtVideoLayersMapping.decode_value(data[NRtpTcpReceiverTransportParamsEnums.ExtVideoLayersMapping.s])
        if NRtpTcpReceiverTransportParamsEnums.ExtDataLayersMapping.s in data:
            self.ExtDataLayersMapping.decode_value(data[NRtpTcpReceiverTransportParamsEnums.ExtDataLayersMapping.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NRtpTcpReceiverTransportParamsValue:
        o = NRtpTcpReceiverTransportParamsValue()
        o.SourceIp = self.SourceIp.clone()
        o.InterfaceIp = self.InterfaceIp.clone()
        o.SourcePort = self.SourcePort.clone()
        o.RtcpEnabled = self.RtcpEnabled.clone()
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
        o.ExtAudioLayersMapping = self.ExtAudioLayersMapping.clone()
        o.ExtVideoLayersMapping = self.ExtVideoLayersMapping.clone()
        o.ExtDataLayersMapping = self.ExtDataLayersMapping.clone()
        return o


class NRtpTcpReceiverTransportParams:
    """Optional object type: NRtpTcpReceiverTransportParams."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NRtpTcpReceiverTransportParamsValue = NRtpTcpReceiverTransportParamsValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NRtpTcpReceiverTransportParamsValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NRtpTcpReceiverTransportParamsValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NRtpTcpReceiverTransportParamsValue | None = None) -> NRtpTcpReceiverTransportParamsValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_SourceIp(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SourceIp

    def set_SourceIp(self, v: Any) -> None:
        assert self._defined, "NRtpTcpReceiverTransportParams must be defined before setting SourceIp"
        _assign_value(self._value.SourceIp, v)

    def get_InterfaceIp(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.InterfaceIp

    def set_InterfaceIp(self, v: Any) -> None:
        assert self._defined, "NRtpTcpReceiverTransportParams must be defined before setting InterfaceIp"
        _assign_value(self._value.InterfaceIp, v)

    def get_SourcePort(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SourcePort

    def set_SourcePort(self, v: Any) -> None:
        assert self._defined, "NRtpTcpReceiverTransportParams must be defined before setting SourcePort"
        _assign_value(self._value.SourcePort, v)

    def get_RtcpEnabled(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.RtcpEnabled

    def set_RtcpEnabled(self, v: Any) -> None:
        assert self._defined, "NRtpTcpReceiverTransportParams must be defined before setting RtcpEnabled"
        _assign_value(self._value.RtcpEnabled, v)

    def get_RtcpSourcePort(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.RtcpSourcePort

    def set_RtcpSourcePort(self, v: Any) -> None:
        assert self._defined, "NRtpTcpReceiverTransportParams must be defined before setting RtcpSourcePort"
        _assign_value(self._value.RtcpSourcePort, v)

    def get_RtpEnabled(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.RtpEnabled

    def set_RtpEnabled(self, v: Any) -> None:
        assert self._defined, "NRtpTcpReceiverTransportParams must be defined before setting RtpEnabled"
        _assign_value(self._value.RtpEnabled, v)

    def get_ExtPrivacyProtocol(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyProtocol

    def set_ExtPrivacyProtocol(self, v: Any) -> None:
        assert self._defined, "NRtpTcpReceiverTransportParams must be defined before setting ExtPrivacyProtocol"
        _assign_value(self._value.ExtPrivacyProtocol, v)

    def get_ExtPrivacyMode(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyMode

    def set_ExtPrivacyMode(self, v: Any) -> None:
        assert self._defined, "NRtpTcpReceiverTransportParams must be defined before setting ExtPrivacyMode"
        _assign_value(self._value.ExtPrivacyMode, v)

    def get_ExtPrivacyIV(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyIV

    def set_ExtPrivacyIV(self, v: Any) -> None:
        assert self._defined, "NRtpTcpReceiverTransportParams must be defined before setting ExtPrivacyIV"
        _assign_value(self._value.ExtPrivacyIV, v)

    def get_ExtPrivacyKeyGenerator(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyKeyGenerator

    def set_ExtPrivacyKeyGenerator(self, v: Any) -> None:
        assert self._defined, "NRtpTcpReceiverTransportParams must be defined before setting ExtPrivacyKeyGenerator"
        _assign_value(self._value.ExtPrivacyKeyGenerator, v)

    def get_ExtPrivacyKeyId(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyKeyId

    def set_ExtPrivacyKeyId(self, v: Any) -> None:
        assert self._defined, "NRtpTcpReceiverTransportParams must be defined before setting ExtPrivacyKeyId"
        _assign_value(self._value.ExtPrivacyKeyId, v)

    def get_ExtPrivacyKeyVersion(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyKeyVersion

    def set_ExtPrivacyKeyVersion(self, v: Any) -> None:
        assert self._defined, "NRtpTcpReceiverTransportParams must be defined before setting ExtPrivacyKeyVersion"
        _assign_value(self._value.ExtPrivacyKeyVersion, v)

    def get_ExtPrivacyEcdhSenderPublicKey(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyEcdhSenderPublicKey

    def set_ExtPrivacyEcdhSenderPublicKey(self, v: Any) -> None:
        assert self._defined, "NRtpTcpReceiverTransportParams must be defined before setting ExtPrivacyEcdhSenderPublicKey"
        _assign_value(self._value.ExtPrivacyEcdhSenderPublicKey, v)

    def get_ExtPrivacyEcdhReceiverPublicKey(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyEcdhReceiverPublicKey

    def set_ExtPrivacyEcdhReceiverPublicKey(self, v: Any) -> None:
        assert self._defined, "NRtpTcpReceiverTransportParams must be defined before setting ExtPrivacyEcdhReceiverPublicKey"
        _assign_value(self._value.ExtPrivacyEcdhReceiverPublicKey, v)

    def get_ExtPrivacyEcdhCurve(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyEcdhCurve

    def set_ExtPrivacyEcdhCurve(self, v: Any) -> None:
        assert self._defined, "NRtpTcpReceiverTransportParams must be defined before setting ExtPrivacyEcdhCurve"
        _assign_value(self._value.ExtPrivacyEcdhCurve, v)

    def get_ExtAudioLayersMapping(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtAudioLayersMapping

    def set_ExtAudioLayersMapping(self, v: Any) -> None:
        assert self._defined, "NRtpTcpReceiverTransportParams must be defined before setting ExtAudioLayersMapping"
        _assign_value(self._value.ExtAudioLayersMapping, v)

    def get_ExtVideoLayersMapping(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtVideoLayersMapping

    def set_ExtVideoLayersMapping(self, v: Any) -> None:
        assert self._defined, "NRtpTcpReceiverTransportParams must be defined before setting ExtVideoLayersMapping"
        _assign_value(self._value.ExtVideoLayersMapping, v)

    def get_ExtDataLayersMapping(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtDataLayersMapping

    def set_ExtDataLayersMapping(self, v: Any) -> None:
        assert self._defined, "NRtpTcpReceiverTransportParams must be defined before setting ExtDataLayersMapping"
        _assign_value(self._value.ExtDataLayersMapping, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NRtpTcpReceiverTransportParamsValue()

    def clone(self) -> NRtpTcpReceiverTransportParams:
        o = NRtpTcpReceiverTransportParams()
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
            return f"NRtpTcpReceiverTransportParams(defined)"
        return "NRtpTcpReceiverTransportParams(<undefined>)"


def make_nrtptcpreceivertransportparams_value(v: NRtpTcpReceiverTransportParamsValue) -> NRtpTcpReceiverTransportParamsValue:
    """Factory: create a NRtpTcpReceiverTransportParamsValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nrtptcpreceivertransportparams(v: NRtpTcpReceiverTransportParamsValue) -> NRtpTcpReceiverTransportParams:
    """Factory: create a defined NRtpTcpReceiverTransportParams from a NRtpTcpReceiverTransportParamsValue."""
    o = NRtpTcpReceiverTransportParams()
    o.set_value(v)
    return o

