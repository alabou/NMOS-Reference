"""Generated NMOS type: NSrtReceiverTransportParams. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NNullString, NNull, NString, NEnum, NInt
from nmos.validators import CheckAutoPort, CheckNullAutoPort

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NSrtReceiverTransportParamsEnums:
    """JSON property name enums for NSrtReceiverTransportParams."""
    SourceIp = EnumRegistry.get("source_ip")
    SourcePort = EnumRegistry.get("source_port")
    DestinationIp = EnumRegistry.get("destination_ip")
    DestinationPort = EnumRegistry.get("destination_port")
    Protocol = EnumRegistry.get("protocol")
    Latency = EnumRegistry.get("latency")
    StreamId = EnumRegistry.get("stream_id")
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


class NSrtReceiverTransportParamsValue:
    """Inner value struct for NSrtReceiverTransportParams."""

    __slots__ = (
        "SourceIp",
        "SourcePort",
        "DestinationIp",
        "DestinationPort",
        "Protocol",
        "Latency",
        "StreamId",
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
        self.DestinationIp: NString = NString()
        self.DestinationPort: NNull = NNull()
        self.Protocol: NEnum = NEnum()
        self.Latency: NInt = NInt()
        self.StreamId: NNullString = NNullString()
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
        _assign_value(self.StreamId, None)
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        if not self.StreamId.defined:
            _assign_value(self.StreamId, None)
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if self.SourcePort.defined:
            CheckAutoPort(self.SourcePort)
        if self.DestinationPort.defined:
            CheckNullAutoPort(self.DestinationPort)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.SourceIp.encode(engine, NSrtReceiverTransportParamsEnums.SourceIp)
        self.SourcePort.encode(engine, NSrtReceiverTransportParamsEnums.SourcePort)
        self.DestinationIp.encode(engine, NSrtReceiverTransportParamsEnums.DestinationIp)
        self.DestinationPort.encode(engine, NSrtReceiverTransportParamsEnums.DestinationPort)
        self.Protocol.encode(engine, NSrtReceiverTransportParamsEnums.Protocol)
        self.Latency.encode(engine, NSrtReceiverTransportParamsEnums.Latency)
        self.StreamId.encode(engine, NSrtReceiverTransportParamsEnums.StreamId)
        self.ExtPrivacyProtocol.encode(engine, NSrtReceiverTransportParamsEnums.ExtPrivacyProtocol)
        self.ExtPrivacyMode.encode(engine, NSrtReceiverTransportParamsEnums.ExtPrivacyMode)
        self.ExtPrivacyIV.encode(engine, NSrtReceiverTransportParamsEnums.ExtPrivacyIV)
        self.ExtPrivacyKeyGenerator.encode(engine, NSrtReceiverTransportParamsEnums.ExtPrivacyKeyGenerator)
        self.ExtPrivacyKeyId.encode(engine, NSrtReceiverTransportParamsEnums.ExtPrivacyKeyId)
        self.ExtPrivacyKeyVersion.encode(engine, NSrtReceiverTransportParamsEnums.ExtPrivacyKeyVersion)
        self.ExtPrivacyEcdhSenderPublicKey.encode(engine, NSrtReceiverTransportParamsEnums.ExtPrivacyEcdhSenderPublicKey)
        self.ExtPrivacyEcdhReceiverPublicKey.encode(engine, NSrtReceiverTransportParamsEnums.ExtPrivacyEcdhReceiverPublicKey)
        self.ExtPrivacyEcdhCurve.encode(engine, NSrtReceiverTransportParamsEnums.ExtPrivacyEcdhCurve)
        self.ExtAudioLayersMapping.encode(engine, NSrtReceiverTransportParamsEnums.ExtAudioLayersMapping)
        self.ExtVideoLayersMapping.encode(engine, NSrtReceiverTransportParamsEnums.ExtVideoLayersMapping)
        self.ExtDataLayersMapping.encode(engine, NSrtReceiverTransportParamsEnums.ExtDataLayersMapping)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NSrtReceiverTransportParams")

        if NSrtReceiverTransportParamsEnums.SourceIp.s in data:
            self.SourceIp.decode_value(data[NSrtReceiverTransportParamsEnums.SourceIp.s])
        if NSrtReceiverTransportParamsEnums.SourcePort.s in data:
            self.SourcePort.decode_value(data[NSrtReceiverTransportParamsEnums.SourcePort.s])
        if NSrtReceiverTransportParamsEnums.DestinationIp.s in data:
            self.DestinationIp.decode_value(data[NSrtReceiverTransportParamsEnums.DestinationIp.s])
        if NSrtReceiverTransportParamsEnums.DestinationPort.s in data:
            self.DestinationPort.decode_value(data[NSrtReceiverTransportParamsEnums.DestinationPort.s])
        if NSrtReceiverTransportParamsEnums.Protocol.s in data:
            self.Protocol.decode_value(data[NSrtReceiverTransportParamsEnums.Protocol.s])
        if NSrtReceiverTransportParamsEnums.Latency.s in data:
            self.Latency.decode_value(data[NSrtReceiverTransportParamsEnums.Latency.s])
        if NSrtReceiverTransportParamsEnums.StreamId.s in data:
            self.StreamId.decode_value(data[NSrtReceiverTransportParamsEnums.StreamId.s])
        if NSrtReceiverTransportParamsEnums.ExtPrivacyProtocol.s in data:
            self.ExtPrivacyProtocol.decode_value(data[NSrtReceiverTransportParamsEnums.ExtPrivacyProtocol.s])
        if NSrtReceiverTransportParamsEnums.ExtPrivacyMode.s in data:
            self.ExtPrivacyMode.decode_value(data[NSrtReceiverTransportParamsEnums.ExtPrivacyMode.s])
        if NSrtReceiverTransportParamsEnums.ExtPrivacyIV.s in data:
            self.ExtPrivacyIV.decode_value(data[NSrtReceiverTransportParamsEnums.ExtPrivacyIV.s])
        if NSrtReceiverTransportParamsEnums.ExtPrivacyKeyGenerator.s in data:
            self.ExtPrivacyKeyGenerator.decode_value(data[NSrtReceiverTransportParamsEnums.ExtPrivacyKeyGenerator.s])
        if NSrtReceiverTransportParamsEnums.ExtPrivacyKeyId.s in data:
            self.ExtPrivacyKeyId.decode_value(data[NSrtReceiverTransportParamsEnums.ExtPrivacyKeyId.s])
        if NSrtReceiverTransportParamsEnums.ExtPrivacyKeyVersion.s in data:
            self.ExtPrivacyKeyVersion.decode_value(data[NSrtReceiverTransportParamsEnums.ExtPrivacyKeyVersion.s])
        if NSrtReceiverTransportParamsEnums.ExtPrivacyEcdhSenderPublicKey.s in data:
            self.ExtPrivacyEcdhSenderPublicKey.decode_value(data[NSrtReceiverTransportParamsEnums.ExtPrivacyEcdhSenderPublicKey.s])
        if NSrtReceiverTransportParamsEnums.ExtPrivacyEcdhReceiverPublicKey.s in data:
            self.ExtPrivacyEcdhReceiverPublicKey.decode_value(data[NSrtReceiverTransportParamsEnums.ExtPrivacyEcdhReceiverPublicKey.s])
        if NSrtReceiverTransportParamsEnums.ExtPrivacyEcdhCurve.s in data:
            self.ExtPrivacyEcdhCurve.decode_value(data[NSrtReceiverTransportParamsEnums.ExtPrivacyEcdhCurve.s])
        if NSrtReceiverTransportParamsEnums.ExtAudioLayersMapping.s in data:
            self.ExtAudioLayersMapping.decode_value(data[NSrtReceiverTransportParamsEnums.ExtAudioLayersMapping.s])
        if NSrtReceiverTransportParamsEnums.ExtVideoLayersMapping.s in data:
            self.ExtVideoLayersMapping.decode_value(data[NSrtReceiverTransportParamsEnums.ExtVideoLayersMapping.s])
        if NSrtReceiverTransportParamsEnums.ExtDataLayersMapping.s in data:
            self.ExtDataLayersMapping.decode_value(data[NSrtReceiverTransportParamsEnums.ExtDataLayersMapping.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NSrtReceiverTransportParamsValue:
        o = NSrtReceiverTransportParamsValue()
        o.SourceIp = self.SourceIp.clone()
        o.SourcePort = self.SourcePort.clone()
        o.DestinationIp = self.DestinationIp.clone()
        o.DestinationPort = self.DestinationPort.clone()
        o.Protocol = self.Protocol.clone()
        o.Latency = self.Latency.clone()
        o.StreamId = self.StreamId.clone()
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


class NSrtReceiverTransportParams:
    """Optional object type: NSrtReceiverTransportParams."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NSrtReceiverTransportParamsValue = NSrtReceiverTransportParamsValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NSrtReceiverTransportParamsValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NSrtReceiverTransportParamsValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NSrtReceiverTransportParamsValue | None = None) -> NSrtReceiverTransportParamsValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_SourceIp(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SourceIp

    def set_SourceIp(self, v: Any) -> None:
        assert self._defined, "NSrtReceiverTransportParams must be defined before setting SourceIp"
        _assign_value(self._value.SourceIp, v)

    def get_SourcePort(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SourcePort

    def set_SourcePort(self, v: Any) -> None:
        assert self._defined, "NSrtReceiverTransportParams must be defined before setting SourcePort"
        _assign_value(self._value.SourcePort, v)

    def get_DestinationIp(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.DestinationIp

    def set_DestinationIp(self, v: Any) -> None:
        assert self._defined, "NSrtReceiverTransportParams must be defined before setting DestinationIp"
        _assign_value(self._value.DestinationIp, v)

    def get_DestinationPort(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.DestinationPort

    def set_DestinationPort(self, v: Any) -> None:
        assert self._defined, "NSrtReceiverTransportParams must be defined before setting DestinationPort"
        _assign_value(self._value.DestinationPort, v)

    def get_Protocol(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Protocol

    def set_Protocol(self, v: Any) -> None:
        assert self._defined, "NSrtReceiverTransportParams must be defined before setting Protocol"
        _assign_value(self._value.Protocol, v)

    def get_Latency(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Latency

    def set_Latency(self, v: Any) -> None:
        assert self._defined, "NSrtReceiverTransportParams must be defined before setting Latency"
        _assign_value(self._value.Latency, v)

    def get_StreamId(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.StreamId

    def set_StreamId(self, v: Any) -> None:
        assert self._defined, "NSrtReceiverTransportParams must be defined before setting StreamId"
        _assign_value(self._value.StreamId, v)

    def get_ExtPrivacyProtocol(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyProtocol

    def set_ExtPrivacyProtocol(self, v: Any) -> None:
        assert self._defined, "NSrtReceiverTransportParams must be defined before setting ExtPrivacyProtocol"
        _assign_value(self._value.ExtPrivacyProtocol, v)

    def get_ExtPrivacyMode(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyMode

    def set_ExtPrivacyMode(self, v: Any) -> None:
        assert self._defined, "NSrtReceiverTransportParams must be defined before setting ExtPrivacyMode"
        _assign_value(self._value.ExtPrivacyMode, v)

    def get_ExtPrivacyIV(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyIV

    def set_ExtPrivacyIV(self, v: Any) -> None:
        assert self._defined, "NSrtReceiverTransportParams must be defined before setting ExtPrivacyIV"
        _assign_value(self._value.ExtPrivacyIV, v)

    def get_ExtPrivacyKeyGenerator(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyKeyGenerator

    def set_ExtPrivacyKeyGenerator(self, v: Any) -> None:
        assert self._defined, "NSrtReceiverTransportParams must be defined before setting ExtPrivacyKeyGenerator"
        _assign_value(self._value.ExtPrivacyKeyGenerator, v)

    def get_ExtPrivacyKeyId(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyKeyId

    def set_ExtPrivacyKeyId(self, v: Any) -> None:
        assert self._defined, "NSrtReceiverTransportParams must be defined before setting ExtPrivacyKeyId"
        _assign_value(self._value.ExtPrivacyKeyId, v)

    def get_ExtPrivacyKeyVersion(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyKeyVersion

    def set_ExtPrivacyKeyVersion(self, v: Any) -> None:
        assert self._defined, "NSrtReceiverTransportParams must be defined before setting ExtPrivacyKeyVersion"
        _assign_value(self._value.ExtPrivacyKeyVersion, v)

    def get_ExtPrivacyEcdhSenderPublicKey(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyEcdhSenderPublicKey

    def set_ExtPrivacyEcdhSenderPublicKey(self, v: Any) -> None:
        assert self._defined, "NSrtReceiverTransportParams must be defined before setting ExtPrivacyEcdhSenderPublicKey"
        _assign_value(self._value.ExtPrivacyEcdhSenderPublicKey, v)

    def get_ExtPrivacyEcdhReceiverPublicKey(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyEcdhReceiverPublicKey

    def set_ExtPrivacyEcdhReceiverPublicKey(self, v: Any) -> None:
        assert self._defined, "NSrtReceiverTransportParams must be defined before setting ExtPrivacyEcdhReceiverPublicKey"
        _assign_value(self._value.ExtPrivacyEcdhReceiverPublicKey, v)

    def get_ExtPrivacyEcdhCurve(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtPrivacyEcdhCurve

    def set_ExtPrivacyEcdhCurve(self, v: Any) -> None:
        assert self._defined, "NSrtReceiverTransportParams must be defined before setting ExtPrivacyEcdhCurve"
        _assign_value(self._value.ExtPrivacyEcdhCurve, v)

    def get_ExtAudioLayersMapping(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtAudioLayersMapping

    def set_ExtAudioLayersMapping(self, v: Any) -> None:
        assert self._defined, "NSrtReceiverTransportParams must be defined before setting ExtAudioLayersMapping"
        _assign_value(self._value.ExtAudioLayersMapping, v)

    def get_ExtVideoLayersMapping(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtVideoLayersMapping

    def set_ExtVideoLayersMapping(self, v: Any) -> None:
        assert self._defined, "NSrtReceiverTransportParams must be defined before setting ExtVideoLayersMapping"
        _assign_value(self._value.ExtVideoLayersMapping, v)

    def get_ExtDataLayersMapping(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExtDataLayersMapping

    def set_ExtDataLayersMapping(self, v: Any) -> None:
        assert self._defined, "NSrtReceiverTransportParams must be defined before setting ExtDataLayersMapping"
        _assign_value(self._value.ExtDataLayersMapping, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NSrtReceiverTransportParamsValue()

    def clone(self) -> NSrtReceiverTransportParams:
        o = NSrtReceiverTransportParams()
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
            return f"NSrtReceiverTransportParams(defined)"
        return "NSrtReceiverTransportParams(<undefined>)"


def make_nsrtreceivertransportparams_value(v: NSrtReceiverTransportParamsValue) -> NSrtReceiverTransportParamsValue:
    """Factory: create a NSrtReceiverTransportParamsValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nsrtreceivertransportparams(v: NSrtReceiverTransportParamsValue) -> NSrtReceiverTransportParams:
    """Factory: create a defined NSrtReceiverTransportParams from a NSrtReceiverTransportParamsValue."""
    o = NSrtReceiverTransportParams()
    o.set_value(v)
    return o

