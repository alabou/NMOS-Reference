"""Generated NMOS type: NRtspReceiverTransportParams. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NNullString, NNull, NString, NEnum
from nmos.validators import CheckNullPort

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NRtspReceiverTransportParamsEnums:
    """JSON property name enums for NRtspReceiverTransportParams."""
    SourceIp = EnumRegistry.get("source_ip")
    SourcePort = EnumRegistry.get("source_port")
    InterfaceIp = EnumRegistry.get("interface_ip")
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


class NRtspReceiverTransportParamsValue:
    """Inner value struct for NRtspReceiverTransportParams."""

    __slots__ = (
        "SourceIp",
        "SourcePort",
        "InterfaceIp",
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
        self.SourcePort: NNull = NNull()
        self.InterfaceIp: NString = NString()
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
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.SourceIp.encode(engine, NRtspReceiverTransportParamsEnums.SourceIp)
        self.SourcePort.encode(engine, NRtspReceiverTransportParamsEnums.SourcePort)
        self.InterfaceIp.encode(engine, NRtspReceiverTransportParamsEnums.InterfaceIp)
        self.ExtPrivacyProtocol.encode(engine, NRtspReceiverTransportParamsEnums.ExtPrivacyProtocol)
        self.ExtPrivacyMode.encode(engine, NRtspReceiverTransportParamsEnums.ExtPrivacyMode)
        self.ExtPrivacyIV.encode(engine, NRtspReceiverTransportParamsEnums.ExtPrivacyIV)
        self.ExtPrivacyKeyGenerator.encode(engine, NRtspReceiverTransportParamsEnums.ExtPrivacyKeyGenerator)
        self.ExtPrivacyKeyId.encode(engine, NRtspReceiverTransportParamsEnums.ExtPrivacyKeyId)
        self.ExtPrivacyKeyVersion.encode(engine, NRtspReceiverTransportParamsEnums.ExtPrivacyKeyVersion)
        self.ExtPrivacyEcdhSenderPublicKey.encode(engine, NRtspReceiverTransportParamsEnums.ExtPrivacyEcdhSenderPublicKey)
        self.ExtPrivacyEcdhReceiverPublicKey.encode(engine, NRtspReceiverTransportParamsEnums.ExtPrivacyEcdhReceiverPublicKey)
        self.ExtPrivacyEcdhCurve.encode(engine, NRtspReceiverTransportParamsEnums.ExtPrivacyEcdhCurve)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NRtspReceiverTransportParams")

        if NRtspReceiverTransportParamsEnums.SourceIp.s in data:
            self.SourceIp.decode_value(data[NRtspReceiverTransportParamsEnums.SourceIp.s])
        if NRtspReceiverTransportParamsEnums.SourcePort.s in data:
            self.SourcePort.decode_value(data[NRtspReceiverTransportParamsEnums.SourcePort.s])
        if NRtspReceiverTransportParamsEnums.InterfaceIp.s in data:
            self.InterfaceIp.decode_value(data[NRtspReceiverTransportParamsEnums.InterfaceIp.s])
        if NRtspReceiverTransportParamsEnums.ExtPrivacyProtocol.s in data:
            self.ExtPrivacyProtocol.decode_value(data[NRtspReceiverTransportParamsEnums.ExtPrivacyProtocol.s])
        if NRtspReceiverTransportParamsEnums.ExtPrivacyMode.s in data:
            self.ExtPrivacyMode.decode_value(data[NRtspReceiverTransportParamsEnums.ExtPrivacyMode.s])
        if NRtspReceiverTransportParamsEnums.ExtPrivacyIV.s in data:
            self.ExtPrivacyIV.decode_value(data[NRtspReceiverTransportParamsEnums.ExtPrivacyIV.s])
        if NRtspReceiverTransportParamsEnums.ExtPrivacyKeyGenerator.s in data:
            self.ExtPrivacyKeyGenerator.decode_value(data[NRtspReceiverTransportParamsEnums.ExtPrivacyKeyGenerator.s])
        if NRtspReceiverTransportParamsEnums.ExtPrivacyKeyId.s in data:
            self.ExtPrivacyKeyId.decode_value(data[NRtspReceiverTransportParamsEnums.ExtPrivacyKeyId.s])
        if NRtspReceiverTransportParamsEnums.ExtPrivacyKeyVersion.s in data:
            self.ExtPrivacyKeyVersion.decode_value(data[NRtspReceiverTransportParamsEnums.ExtPrivacyKeyVersion.s])
        if NRtspReceiverTransportParamsEnums.ExtPrivacyEcdhSenderPublicKey.s in data:
            self.ExtPrivacyEcdhSenderPublicKey.decode_value(data[NRtspReceiverTransportParamsEnums.ExtPrivacyEcdhSenderPublicKey.s])
        if NRtspReceiverTransportParamsEnums.ExtPrivacyEcdhReceiverPublicKey.s in data:
            self.ExtPrivacyEcdhReceiverPublicKey.decode_value(data[NRtspReceiverTransportParamsEnums.ExtPrivacyEcdhReceiverPublicKey.s])
        if NRtspReceiverTransportParamsEnums.ExtPrivacyEcdhCurve.s in data:
            self.ExtPrivacyEcdhCurve.decode_value(data[NRtspReceiverTransportParamsEnums.ExtPrivacyEcdhCurve.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NRtspReceiverTransportParamsValue:
        o = NRtspReceiverTransportParamsValue()
        o.SourceIp = self.SourceIp.clone()
        o.SourcePort = self.SourcePort.clone()
        o.InterfaceIp = self.InterfaceIp.clone()
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


class NRtspReceiverTransportParams:
    """Optional object type: NRtspReceiverTransportParams."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NRtspReceiverTransportParamsValue = NRtspReceiverTransportParamsValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NRtspReceiverTransportParamsValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NRtspReceiverTransportParamsValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NRtspReceiverTransportParamsValue | None = None) -> NRtspReceiverTransportParamsValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_SourceIp(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SourceIp

    def set_SourceIp(self, v: Any) -> None:
        assert self._defined, "NRtspReceiverTransportParams must be defined before setting SourceIp"
        _assign_value(self._value.SourceIp, v)

    def get_SourcePort(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SourcePort

    def set_SourcePort(self, v: Any) -> None:
        assert self._defined, "NRtspReceiverTransportParams must be defined before setting SourcePort"
        _assign_value(self._value.SourcePort, v)

    def get_InterfaceIp(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.InterfaceIp

    def set_InterfaceIp(self, v: Any) -> None:
        assert self._defined, "NRtspReceiverTransportParams must be defined before setting InterfaceIp"
        _assign_value(self._value.InterfaceIp, v)

    def get_ExtPrivacyProtocol(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyProtocol

    def set_ExtPrivacyProtocol(self, v: Any) -> None:
        assert self._defined, "NRtspReceiverTransportParams must be defined before setting ExtPrivacyProtocol"
        _assign_value(self._value.ExtPrivacyProtocol, v)

    def get_ExtPrivacyMode(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyMode

    def set_ExtPrivacyMode(self, v: Any) -> None:
        assert self._defined, "NRtspReceiverTransportParams must be defined before setting ExtPrivacyMode"
        _assign_value(self._value.ExtPrivacyMode, v)

    def get_ExtPrivacyIV(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyIV

    def set_ExtPrivacyIV(self, v: Any) -> None:
        assert self._defined, "NRtspReceiverTransportParams must be defined before setting ExtPrivacyIV"
        _assign_value(self._value.ExtPrivacyIV, v)

    def get_ExtPrivacyKeyGenerator(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyKeyGenerator

    def set_ExtPrivacyKeyGenerator(self, v: Any) -> None:
        assert self._defined, "NRtspReceiverTransportParams must be defined before setting ExtPrivacyKeyGenerator"
        _assign_value(self._value.ExtPrivacyKeyGenerator, v)

    def get_ExtPrivacyKeyId(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyKeyId

    def set_ExtPrivacyKeyId(self, v: Any) -> None:
        assert self._defined, "NRtspReceiverTransportParams must be defined before setting ExtPrivacyKeyId"
        _assign_value(self._value.ExtPrivacyKeyId, v)

    def get_ExtPrivacyKeyVersion(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyKeyVersion

    def set_ExtPrivacyKeyVersion(self, v: Any) -> None:
        assert self._defined, "NRtspReceiverTransportParams must be defined before setting ExtPrivacyKeyVersion"
        _assign_value(self._value.ExtPrivacyKeyVersion, v)

    def get_ExtPrivacyEcdhSenderPublicKey(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyEcdhSenderPublicKey

    def set_ExtPrivacyEcdhSenderPublicKey(self, v: Any) -> None:
        assert self._defined, "NRtspReceiverTransportParams must be defined before setting ExtPrivacyEcdhSenderPublicKey"
        _assign_value(self._value.ExtPrivacyEcdhSenderPublicKey, v)

    def get_ExtPrivacyEcdhReceiverPublicKey(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyEcdhReceiverPublicKey

    def set_ExtPrivacyEcdhReceiverPublicKey(self, v: Any) -> None:
        assert self._defined, "NRtspReceiverTransportParams must be defined before setting ExtPrivacyEcdhReceiverPublicKey"
        _assign_value(self._value.ExtPrivacyEcdhReceiverPublicKey, v)

    def get_ExtPrivacyEcdhCurve(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyEcdhCurve

    def set_ExtPrivacyEcdhCurve(self, v: Any) -> None:
        assert self._defined, "NRtspReceiverTransportParams must be defined before setting ExtPrivacyEcdhCurve"
        _assign_value(self._value.ExtPrivacyEcdhCurve, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NRtspReceiverTransportParamsValue()

    def clone(self) -> NRtspReceiverTransportParams:
        o = NRtspReceiverTransportParams()
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
            return f"NRtspReceiverTransportParams(defined)"
        return "NRtspReceiverTransportParams(<undefined>)"


def make_nrtspreceivertransportparams_value(v: NRtspReceiverTransportParamsValue) -> NRtspReceiverTransportParamsValue:
    """Factory: create a NRtspReceiverTransportParamsValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nrtspreceivertransportparams(v: NRtspReceiverTransportParamsValue) -> NRtspReceiverTransportParams:
    """Factory: create a defined NRtspReceiverTransportParams from a NRtspReceiverTransportParamsValue."""
    o = NRtspReceiverTransportParams()
    o.set_value(v)
    return o

