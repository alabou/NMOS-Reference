"""Generated NMOS type: NMqttSenderTransportParams. DO NOT EDIT."""

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


class NMqttSenderTransportParamsEnums:
    """JSON property name enums for NMqttSenderTransportParams."""
    DestinationHost = EnumRegistry.get("destination_host")
    DestinationPort = EnumRegistry.get("destination_port")
    BrokerProtocol = EnumRegistry.get("broker_protocol")
    BrokerAuthorization = EnumRegistry.get("broker_authorization")
    BrokerTopic = EnumRegistry.get("broker_topic")
    ConnectionStatusBrokerTopic = EnumRegistry.get("connection_status_broker_topic")
    pass


class NMqttSenderTransportParamsValue:
    """Inner value struct for NMqttSenderTransportParams."""

    __slots__ = (
        "DestinationHost",
        "DestinationPort",
        "BrokerProtocol",
        "BrokerAuthorization",
        "BrokerTopic",
        "ConnectionStatusBrokerTopic",
        "InterfaceIp",
    )

    def __init__(self) -> None:
        self.DestinationHost: NNullString = NNullString()
        self.DestinationPort: NNull = NNull()
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
        if self.DestinationPort.defined:
            CheckAutoPort(self.DestinationPort)
        if self.BrokerAuthorization.defined:
            CheckAutoBool(self.BrokerAuthorization)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.DestinationHost.encode(engine, NMqttSenderTransportParamsEnums.DestinationHost)
        self.DestinationPort.encode(engine, NMqttSenderTransportParamsEnums.DestinationPort)
        self.BrokerProtocol.encode(engine, NMqttSenderTransportParamsEnums.BrokerProtocol)
        self.BrokerAuthorization.encode(engine, NMqttSenderTransportParamsEnums.BrokerAuthorization)
        self.BrokerTopic.encode(engine, NMqttSenderTransportParamsEnums.BrokerTopic)
        self.ConnectionStatusBrokerTopic.encode(engine, NMqttSenderTransportParamsEnums.ConnectionStatusBrokerTopic)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NMqttSenderTransportParams")

        if NMqttSenderTransportParamsEnums.DestinationHost.s in data:
            self.DestinationHost.decode_value(data[NMqttSenderTransportParamsEnums.DestinationHost.s])
        if NMqttSenderTransportParamsEnums.DestinationPort.s in data:
            self.DestinationPort.decode_value(data[NMqttSenderTransportParamsEnums.DestinationPort.s])
        if NMqttSenderTransportParamsEnums.BrokerProtocol.s in data:
            self.BrokerProtocol.decode_value(data[NMqttSenderTransportParamsEnums.BrokerProtocol.s])
        if NMqttSenderTransportParamsEnums.BrokerAuthorization.s in data:
            self.BrokerAuthorization.decode_value(data[NMqttSenderTransportParamsEnums.BrokerAuthorization.s])
        if NMqttSenderTransportParamsEnums.BrokerTopic.s in data:
            self.BrokerTopic.decode_value(data[NMqttSenderTransportParamsEnums.BrokerTopic.s])
        if NMqttSenderTransportParamsEnums.ConnectionStatusBrokerTopic.s in data:
            self.ConnectionStatusBrokerTopic.decode_value(data[NMqttSenderTransportParamsEnums.ConnectionStatusBrokerTopic.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NMqttSenderTransportParamsValue:
        o = NMqttSenderTransportParamsValue()
        o.DestinationHost = self.DestinationHost.clone()
        o.DestinationPort = self.DestinationPort.clone()
        o.BrokerProtocol = self.BrokerProtocol.clone()
        o.BrokerAuthorization = self.BrokerAuthorization.clone()
        o.BrokerTopic = self.BrokerTopic.clone()
        o.ConnectionStatusBrokerTopic = self.ConnectionStatusBrokerTopic.clone()
        o.InterfaceIp = self.InterfaceIp.clone()
        return o


class NMqttSenderTransportParams:
    """Optional object type: NMqttSenderTransportParams."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NMqttSenderTransportParamsValue = NMqttSenderTransportParamsValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NMqttSenderTransportParamsValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NMqttSenderTransportParamsValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NMqttSenderTransportParamsValue | None = None) -> NMqttSenderTransportParamsValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_DestinationHost(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.DestinationHost

    def set_DestinationHost(self, v: Any) -> None:
        assert self._defined, "NMqttSenderTransportParams must be defined before setting DestinationHost"
        _assign_value(self._value.DestinationHost, v)

    def get_DestinationPort(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.DestinationPort

    def set_DestinationPort(self, v: Any) -> None:
        assert self._defined, "NMqttSenderTransportParams must be defined before setting DestinationPort"
        _assign_value(self._value.DestinationPort, v)

    def get_BrokerProtocol(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.BrokerProtocol

    def set_BrokerProtocol(self, v: Any) -> None:
        assert self._defined, "NMqttSenderTransportParams must be defined before setting BrokerProtocol"
        _assign_value(self._value.BrokerProtocol, v)

    def get_BrokerAuthorization(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.BrokerAuthorization

    def set_BrokerAuthorization(self, v: Any) -> None:
        assert self._defined, "NMqttSenderTransportParams must be defined before setting BrokerAuthorization"
        _assign_value(self._value.BrokerAuthorization, v)

    def get_BrokerTopic(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.BrokerTopic

    def set_BrokerTopic(self, v: Any) -> None:
        assert self._defined, "NMqttSenderTransportParams must be defined before setting BrokerTopic"
        _assign_value(self._value.BrokerTopic, v)

    def get_ConnectionStatusBrokerTopic(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ConnectionStatusBrokerTopic

    def set_ConnectionStatusBrokerTopic(self, v: Any) -> None:
        assert self._defined, "NMqttSenderTransportParams must be defined before setting ConnectionStatusBrokerTopic"
        _assign_value(self._value.ConnectionStatusBrokerTopic, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NMqttSenderTransportParamsValue()

    def clone(self) -> NMqttSenderTransportParams:
        o = NMqttSenderTransportParams()
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
            return f"NMqttSenderTransportParams(defined)"
        return "NMqttSenderTransportParams(<undefined>)"


def make_nmqttsendertransportparams_value(v: NMqttSenderTransportParamsValue) -> NMqttSenderTransportParamsValue:
    """Factory: create a NMqttSenderTransportParamsValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nmqttsendertransportparams(v: NMqttSenderTransportParamsValue) -> NMqttSenderTransportParams:
    """Factory: create a defined NMqttSenderTransportParams from a NMqttSenderTransportParamsValue."""
    o = NMqttSenderTransportParams()
    o.set_value(v)
    return o

