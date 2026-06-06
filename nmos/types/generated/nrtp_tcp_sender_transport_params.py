"""Generated NMOS type: NRtpTcpSenderTransportParams. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString, NNull, NBool, NEnum
from nmos.validators import CheckAutoPort

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NRtpTcpSenderTransportParamsEnums:
    """JSON property name enums for NRtpTcpSenderTransportParams."""
    SourceIp = EnumRegistry.get("source_ip")
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
    pass


class NRtpTcpSenderTransportParamsValue:
    """Inner value struct for NRtpTcpSenderTransportParams."""

    __slots__ = (
        "SourceIp",
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
    )

    def __init__(self) -> None:
        self.SourceIp: NString = NString()
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

    def set_to_default(self) -> None:
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if self.SourcePort.defined:
            CheckAutoPort(self.SourcePort)
        if self.RtcpSourcePort.defined:
            CheckAutoPort(self.RtcpSourcePort)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.SourceIp.encode(engine, NRtpTcpSenderTransportParamsEnums.SourceIp)
        self.SourcePort.encode(engine, NRtpTcpSenderTransportParamsEnums.SourcePort)
        self.RtcpEnabled.encode(engine, NRtpTcpSenderTransportParamsEnums.RtcpEnabled)
        self.RtcpSourcePort.encode(engine, NRtpTcpSenderTransportParamsEnums.RtcpSourcePort)
        self.RtpEnabled.encode(engine, NRtpTcpSenderTransportParamsEnums.RtpEnabled)
        self.ExtPrivacyProtocol.encode(engine, NRtpTcpSenderTransportParamsEnums.ExtPrivacyProtocol)
        self.ExtPrivacyMode.encode(engine, NRtpTcpSenderTransportParamsEnums.ExtPrivacyMode)
        self.ExtPrivacyIV.encode(engine, NRtpTcpSenderTransportParamsEnums.ExtPrivacyIV)
        self.ExtPrivacyKeyGenerator.encode(engine, NRtpTcpSenderTransportParamsEnums.ExtPrivacyKeyGenerator)
        self.ExtPrivacyKeyId.encode(engine, NRtpTcpSenderTransportParamsEnums.ExtPrivacyKeyId)
        self.ExtPrivacyKeyVersion.encode(engine, NRtpTcpSenderTransportParamsEnums.ExtPrivacyKeyVersion)
        self.ExtPrivacyEcdhSenderPublicKey.encode(engine, NRtpTcpSenderTransportParamsEnums.ExtPrivacyEcdhSenderPublicKey)
        self.ExtPrivacyEcdhReceiverPublicKey.encode(engine, NRtpTcpSenderTransportParamsEnums.ExtPrivacyEcdhReceiverPublicKey)
        self.ExtPrivacyEcdhCurve.encode(engine, NRtpTcpSenderTransportParamsEnums.ExtPrivacyEcdhCurve)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NRtpTcpSenderTransportParams")

        if NRtpTcpSenderTransportParamsEnums.SourceIp.s in data:
            self.SourceIp.decode_value(data[NRtpTcpSenderTransportParamsEnums.SourceIp.s])
        if NRtpTcpSenderTransportParamsEnums.SourcePort.s in data:
            self.SourcePort.decode_value(data[NRtpTcpSenderTransportParamsEnums.SourcePort.s])
        if NRtpTcpSenderTransportParamsEnums.RtcpEnabled.s in data:
            self.RtcpEnabled.decode_value(data[NRtpTcpSenderTransportParamsEnums.RtcpEnabled.s])
        if NRtpTcpSenderTransportParamsEnums.RtcpSourcePort.s in data:
            self.RtcpSourcePort.decode_value(data[NRtpTcpSenderTransportParamsEnums.RtcpSourcePort.s])
        if NRtpTcpSenderTransportParamsEnums.RtpEnabled.s in data:
            self.RtpEnabled.decode_value(data[NRtpTcpSenderTransportParamsEnums.RtpEnabled.s])
        if NRtpTcpSenderTransportParamsEnums.ExtPrivacyProtocol.s in data:
            self.ExtPrivacyProtocol.decode_value(data[NRtpTcpSenderTransportParamsEnums.ExtPrivacyProtocol.s])
        if NRtpTcpSenderTransportParamsEnums.ExtPrivacyMode.s in data:
            self.ExtPrivacyMode.decode_value(data[NRtpTcpSenderTransportParamsEnums.ExtPrivacyMode.s])
        if NRtpTcpSenderTransportParamsEnums.ExtPrivacyIV.s in data:
            self.ExtPrivacyIV.decode_value(data[NRtpTcpSenderTransportParamsEnums.ExtPrivacyIV.s])
        if NRtpTcpSenderTransportParamsEnums.ExtPrivacyKeyGenerator.s in data:
            self.ExtPrivacyKeyGenerator.decode_value(data[NRtpTcpSenderTransportParamsEnums.ExtPrivacyKeyGenerator.s])
        if NRtpTcpSenderTransportParamsEnums.ExtPrivacyKeyId.s in data:
            self.ExtPrivacyKeyId.decode_value(data[NRtpTcpSenderTransportParamsEnums.ExtPrivacyKeyId.s])
        if NRtpTcpSenderTransportParamsEnums.ExtPrivacyKeyVersion.s in data:
            self.ExtPrivacyKeyVersion.decode_value(data[NRtpTcpSenderTransportParamsEnums.ExtPrivacyKeyVersion.s])
        if NRtpTcpSenderTransportParamsEnums.ExtPrivacyEcdhSenderPublicKey.s in data:
            self.ExtPrivacyEcdhSenderPublicKey.decode_value(data[NRtpTcpSenderTransportParamsEnums.ExtPrivacyEcdhSenderPublicKey.s])
        if NRtpTcpSenderTransportParamsEnums.ExtPrivacyEcdhReceiverPublicKey.s in data:
            self.ExtPrivacyEcdhReceiverPublicKey.decode_value(data[NRtpTcpSenderTransportParamsEnums.ExtPrivacyEcdhReceiverPublicKey.s])
        if NRtpTcpSenderTransportParamsEnums.ExtPrivacyEcdhCurve.s in data:
            self.ExtPrivacyEcdhCurve.decode_value(data[NRtpTcpSenderTransportParamsEnums.ExtPrivacyEcdhCurve.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NRtpTcpSenderTransportParamsValue:
        o = NRtpTcpSenderTransportParamsValue()
        o.SourceIp = self.SourceIp.clone()
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
        return o


class NRtpTcpSenderTransportParams:
    """Optional object type: NRtpTcpSenderTransportParams."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NRtpTcpSenderTransportParamsValue = NRtpTcpSenderTransportParamsValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NRtpTcpSenderTransportParamsValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NRtpTcpSenderTransportParamsValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NRtpTcpSenderTransportParamsValue | None = None) -> NRtpTcpSenderTransportParamsValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_SourceIp(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SourceIp

    def set_SourceIp(self, v: Any) -> None:
        assert self._defined, "NRtpTcpSenderTransportParams must be defined before setting SourceIp"
        _assign_value(self._value.SourceIp, v)

    def get_SourcePort(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SourcePort

    def set_SourcePort(self, v: Any) -> None:
        assert self._defined, "NRtpTcpSenderTransportParams must be defined before setting SourcePort"
        _assign_value(self._value.SourcePort, v)

    def get_RtcpEnabled(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.RtcpEnabled

    def set_RtcpEnabled(self, v: Any) -> None:
        assert self._defined, "NRtpTcpSenderTransportParams must be defined before setting RtcpEnabled"
        _assign_value(self._value.RtcpEnabled, v)

    def get_RtcpSourcePort(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.RtcpSourcePort

    def set_RtcpSourcePort(self, v: Any) -> None:
        assert self._defined, "NRtpTcpSenderTransportParams must be defined before setting RtcpSourcePort"
        _assign_value(self._value.RtcpSourcePort, v)

    def get_RtpEnabled(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.RtpEnabled

    def set_RtpEnabled(self, v: Any) -> None:
        assert self._defined, "NRtpTcpSenderTransportParams must be defined before setting RtpEnabled"
        _assign_value(self._value.RtpEnabled, v)

    def get_ExtPrivacyProtocol(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyProtocol

    def set_ExtPrivacyProtocol(self, v: Any) -> None:
        assert self._defined, "NRtpTcpSenderTransportParams must be defined before setting ExtPrivacyProtocol"
        _assign_value(self._value.ExtPrivacyProtocol, v)

    def get_ExtPrivacyMode(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyMode

    def set_ExtPrivacyMode(self, v: Any) -> None:
        assert self._defined, "NRtpTcpSenderTransportParams must be defined before setting ExtPrivacyMode"
        _assign_value(self._value.ExtPrivacyMode, v)

    def get_ExtPrivacyIV(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyIV

    def set_ExtPrivacyIV(self, v: Any) -> None:
        assert self._defined, "NRtpTcpSenderTransportParams must be defined before setting ExtPrivacyIV"
        _assign_value(self._value.ExtPrivacyIV, v)

    def get_ExtPrivacyKeyGenerator(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyKeyGenerator

    def set_ExtPrivacyKeyGenerator(self, v: Any) -> None:
        assert self._defined, "NRtpTcpSenderTransportParams must be defined before setting ExtPrivacyKeyGenerator"
        _assign_value(self._value.ExtPrivacyKeyGenerator, v)

    def get_ExtPrivacyKeyId(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyKeyId

    def set_ExtPrivacyKeyId(self, v: Any) -> None:
        assert self._defined, "NRtpTcpSenderTransportParams must be defined before setting ExtPrivacyKeyId"
        _assign_value(self._value.ExtPrivacyKeyId, v)

    def get_ExtPrivacyKeyVersion(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyKeyVersion

    def set_ExtPrivacyKeyVersion(self, v: Any) -> None:
        assert self._defined, "NRtpTcpSenderTransportParams must be defined before setting ExtPrivacyKeyVersion"
        _assign_value(self._value.ExtPrivacyKeyVersion, v)

    def get_ExtPrivacyEcdhSenderPublicKey(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyEcdhSenderPublicKey

    def set_ExtPrivacyEcdhSenderPublicKey(self, v: Any) -> None:
        assert self._defined, "NRtpTcpSenderTransportParams must be defined before setting ExtPrivacyEcdhSenderPublicKey"
        _assign_value(self._value.ExtPrivacyEcdhSenderPublicKey, v)

    def get_ExtPrivacyEcdhReceiverPublicKey(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyEcdhReceiverPublicKey

    def set_ExtPrivacyEcdhReceiverPublicKey(self, v: Any) -> None:
        assert self._defined, "NRtpTcpSenderTransportParams must be defined before setting ExtPrivacyEcdhReceiverPublicKey"
        _assign_value(self._value.ExtPrivacyEcdhReceiverPublicKey, v)

    def get_ExtPrivacyEcdhCurve(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyEcdhCurve

    def set_ExtPrivacyEcdhCurve(self, v: Any) -> None:
        assert self._defined, "NRtpTcpSenderTransportParams must be defined before setting ExtPrivacyEcdhCurve"
        _assign_value(self._value.ExtPrivacyEcdhCurve, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NRtpTcpSenderTransportParamsValue()

    def clone(self) -> NRtpTcpSenderTransportParams:
        o = NRtpTcpSenderTransportParams()
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
            return f"NRtpTcpSenderTransportParams(defined)"
        return "NRtpTcpSenderTransportParams(<undefined>)"


def make_nrtptcpsendertransportparams_value(v: NRtpTcpSenderTransportParamsValue) -> NRtpTcpSenderTransportParamsValue:
    """Factory: create a NRtpTcpSenderTransportParamsValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nrtptcpsendertransportparams(v: NRtpTcpSenderTransportParamsValue) -> NRtpTcpSenderTransportParams:
    """Factory: create a defined NRtpTcpSenderTransportParams from a NRtpTcpSenderTransportParamsValue."""
    o = NRtpTcpSenderTransportParams()
    o.set_value(v)
    return o

