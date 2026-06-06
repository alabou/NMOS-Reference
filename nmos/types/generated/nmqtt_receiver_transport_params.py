"""Generated NMOS type: NMqttReceiverTransportParams. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NNullString, NNull, NEnum, NString
from nmos.validators import CheckAutoPort, CheckAutoBool

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NMqttReceiverTransportParamsEnums:
    """JSON property name enums for NMqttReceiverTransportParams."""
    SourceHost = EnumRegistry.get("source_host")
    SourcePort = EnumRegistry.get("source_port")
    BrokerProtocol = EnumRegistry.get("broker_protocol")
    BrokerAuthorization = EnumRegistry.get("broker_authorization")
    BrokerTopic = EnumRegistry.get("broker_topic")
    ConnectionStatusBrokerTopic = EnumRegistry.get("connection_status_broker_topic")
    pass


class NMqttReceiverTransportParamsValue:
    """Inner value struct for NMqttReceiverTransportParams."""

    __slots__ = (
        "SourceHost",
        "SourcePort",
        "BrokerProtocol",
        "BrokerAuthorization",
        "BrokerTopic",
        "ConnectionStatusBrokerTopic",
        "InterfaceIp",
    )

    def __init__(self) -> None:
        self.SourceHost: NNullString = NNullString()
        self.SourcePort: NNull = NNull()
        self.BrokerProtocol: NEnum = NEnum()
        self.BrokerAuthorization: NNull = NNull()
        self.BrokerTopic: NNullString = NNullString()
        self.ConnectionStatusBrokerTopic: NNullString = NNullString()
        self.InterfaceIp: NString = NString()

    def set_to_default(self) -> None:
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if self.SourcePort.defined:
            CheckAutoPort(self.SourcePort)
        if self.BrokerAuthorization.defined:
            CheckAutoBool(self.BrokerAuthorization)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.SourceHost.encode(engine, NMqttReceiverTransportParamsEnums.SourceHost)
        self.SourcePort.encode(engine, NMqttReceiverTransportParamsEnums.SourcePort)
        self.BrokerProtocol.encode(engine, NMqttReceiverTransportParamsEnums.BrokerProtocol)
        self.BrokerAuthorization.encode(engine, NMqttReceiverTransportParamsEnums.BrokerAuthorization)
        self.BrokerTopic.encode(engine, NMqttReceiverTransportParamsEnums.BrokerTopic)
        self.ConnectionStatusBrokerTopic.encode(engine, NMqttReceiverTransportParamsEnums.ConnectionStatusBrokerTopic)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NMqttReceiverTransportParams")

        if NMqttReceiverTransportParamsEnums.SourceHost.s in data:
            self.SourceHost.decode_value(data[NMqttReceiverTransportParamsEnums.SourceHost.s])
        if NMqttReceiverTransportParamsEnums.SourcePort.s in data:
            self.SourcePort.decode_value(data[NMqttReceiverTransportParamsEnums.SourcePort.s])
        if NMqttReceiverTransportParamsEnums.BrokerProtocol.s in data:
            self.BrokerProtocol.decode_value(data[NMqttReceiverTransportParamsEnums.BrokerProtocol.s])
        if NMqttReceiverTransportParamsEnums.BrokerAuthorization.s in data:
            self.BrokerAuthorization.decode_value(data[NMqttReceiverTransportParamsEnums.BrokerAuthorization.s])
        if NMqttReceiverTransportParamsEnums.BrokerTopic.s in data:
            self.BrokerTopic.decode_value(data[NMqttReceiverTransportParamsEnums.BrokerTopic.s])
        if NMqttReceiverTransportParamsEnums.ConnectionStatusBrokerTopic.s in data:
            self.ConnectionStatusBrokerTopic.decode_value(data[NMqttReceiverTransportParamsEnums.ConnectionStatusBrokerTopic.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NMqttReceiverTransportParamsValue:
        o = NMqttReceiverTransportParamsValue()
        o.SourceHost = self.SourceHost.clone()
        o.SourcePort = self.SourcePort.clone()
        o.BrokerProtocol = self.BrokerProtocol.clone()
        o.BrokerAuthorization = self.BrokerAuthorization.clone()
        o.BrokerTopic = self.BrokerTopic.clone()
        o.ConnectionStatusBrokerTopic = self.ConnectionStatusBrokerTopic.clone()
        o.InterfaceIp = self.InterfaceIp.clone()
        return o


class NMqttReceiverTransportParams:
    """Optional object type: NMqttReceiverTransportParams."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NMqttReceiverTransportParamsValue = NMqttReceiverTransportParamsValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NMqttReceiverTransportParamsValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NMqttReceiverTransportParamsValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NMqttReceiverTransportParamsValue | None = None) -> NMqttReceiverTransportParamsValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_SourceHost(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SourceHost

    def set_SourceHost(self, v: Any) -> None:
        assert self._defined, "NMqttReceiverTransportParams must be defined before setting SourceHost"
        _assign_value(self._value.SourceHost, v)

    def get_SourcePort(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SourcePort

    def set_SourcePort(self, v: Any) -> None:
        assert self._defined, "NMqttReceiverTransportParams must be defined before setting SourcePort"
        _assign_value(self._value.SourcePort, v)

    def get_BrokerProtocol(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.BrokerProtocol

    def set_BrokerProtocol(self, v: Any) -> None:
        assert self._defined, "NMqttReceiverTransportParams must be defined before setting BrokerProtocol"
        _assign_value(self._value.BrokerProtocol, v)

    def get_BrokerAuthorization(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.BrokerAuthorization

    def set_BrokerAuthorization(self, v: Any) -> None:
        assert self._defined, "NMqttReceiverTransportParams must be defined before setting BrokerAuthorization"
        _assign_value(self._value.BrokerAuthorization, v)

    def get_BrokerTopic(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.BrokerTopic

    def set_BrokerTopic(self, v: Any) -> None:
        assert self._defined, "NMqttReceiverTransportParams must be defined before setting BrokerTopic"
        _assign_value(self._value.BrokerTopic, v)

    def get_ConnectionStatusBrokerTopic(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ConnectionStatusBrokerTopic

    def set_ConnectionStatusBrokerTopic(self, v: Any) -> None:
        assert self._defined, "NMqttReceiverTransportParams must be defined before setting ConnectionStatusBrokerTopic"
        _assign_value(self._value.ConnectionStatusBrokerTopic, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NMqttReceiverTransportParamsValue()

    def clone(self) -> NMqttReceiverTransportParams:
        o = NMqttReceiverTransportParams()
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
            return f"NMqttReceiverTransportParams(defined)"
        return "NMqttReceiverTransportParams(<undefined>)"


def make_nmqttreceivertransportparams_value(v: NMqttReceiverTransportParamsValue) -> NMqttReceiverTransportParamsValue:
    """Factory: create a NMqttReceiverTransportParamsValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nmqttreceivertransportparams(v: NMqttReceiverTransportParamsValue) -> NMqttReceiverTransportParams:
    """Factory: create a defined NMqttReceiverTransportParams from a NMqttReceiverTransportParamsValue."""
    o = NMqttReceiverTransportParams()
    o.set_value(v)
    return o

