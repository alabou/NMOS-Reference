"""Generated NMOS type: NNodeEndpoint. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString, NInt, NEnum, NBool
from nmos.validators import CheckEndpointHostString, CheckEndpointPort, CheckEndpointProtocol

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NNodeEndpointEnums:
    """JSON property name enums for NNodeEndpoint."""
    Host = EnumRegistry.get("host")
    Port = EnumRegistry.get("port")
    Protocol = EnumRegistry.get("protocol")
    Authorization = EnumRegistry.get("authorization")
    pass


class NNodeEndpointValue:
    """Inner value struct for NNodeEndpoint."""

    __slots__ = (
        "Host",
        "Port",
        "Protocol",
        "Authorization",
    )

    def __init__(self) -> None:
        self.Host: NString = NString()
        self.Port: NInt = NInt()
        self.Protocol: NEnum = NEnum()
        self.Authorization: NBool = NBool()

    def set_to_default(self) -> None:
        self.Host.set_to_default()
        self.Port.set_to_default()
        self.Protocol.set_to_default()
        _assign_value(self.Authorization, False)
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        if not self.Authorization.defined:
            _assign_value(self.Authorization, False)
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Host.defined:
            raise InvalidObject("missing required member Host")
        if not self.Port.defined:
            raise InvalidObject("missing required member Port")
        if not self.Protocol.defined:
            raise InvalidObject("missing required member Protocol")
        if self.Host.defined:
            CheckEndpointHostString(self.Host)
        if self.Port.defined:
            CheckEndpointPort(self.Port)
        if self.Protocol.defined:
            CheckEndpointProtocol(self.Protocol)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Host.encode(engine, NNodeEndpointEnums.Host)
        self.Port.encode(engine, NNodeEndpointEnums.Port)
        self.Protocol.encode(engine, NNodeEndpointEnums.Protocol)
        self.Authorization.encode(engine, NNodeEndpointEnums.Authorization)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NNodeEndpoint")

        if NNodeEndpointEnums.Host.s in data:
            self.Host.decode_value(data[NNodeEndpointEnums.Host.s])
        if NNodeEndpointEnums.Port.s in data:
            self.Port.decode_value(data[NNodeEndpointEnums.Port.s])
        if NNodeEndpointEnums.Protocol.s in data:
            self.Protocol.decode_value(data[NNodeEndpointEnums.Protocol.s])
        if NNodeEndpointEnums.Authorization.s in data:
            self.Authorization.decode_value(data[NNodeEndpointEnums.Authorization.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NNodeEndpointValue:
        o = NNodeEndpointValue()
        o.Host = self.Host.clone()
        o.Port = self.Port.clone()
        o.Protocol = self.Protocol.clone()
        o.Authorization = self.Authorization.clone()
        return o


class NNodeEndpoint:
    """Optional object type: NNodeEndpoint."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NNodeEndpointValue = NNodeEndpointValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NNodeEndpointValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NNodeEndpointValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NNodeEndpointValue | None = None) -> NNodeEndpointValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Host(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Host

    def set_Host(self, v: Any) -> None:
        assert self._defined, "NNodeEndpoint must be defined before setting Host"
        _assign_value(self._value.Host, v)

    def get_Port(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Port

    def set_Port(self, v: Any) -> None:
        assert self._defined, "NNodeEndpoint must be defined before setting Port"
        _assign_value(self._value.Port, v)

    def get_Protocol(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Protocol

    def set_Protocol(self, v: Any) -> None:
        assert self._defined, "NNodeEndpoint must be defined before setting Protocol"
        _assign_value(self._value.Protocol, v)

    def get_Authorization(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Authorization

    def set_Authorization(self, v: Any) -> None:
        assert self._defined, "NNodeEndpoint must be defined before setting Authorization"
        _assign_value(self._value.Authorization, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NNodeEndpointValue()

    def clone(self) -> NNodeEndpoint:
        o = NNodeEndpoint()
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
            return f"NNodeEndpoint(defined)"
        return "NNodeEndpoint(<undefined>)"


def make_nnodeendpoint_value(v: NNodeEndpointValue) -> NNodeEndpointValue:
    """Factory: create a NNodeEndpointValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nnodeendpoint(v: NNodeEndpointValue) -> NNodeEndpoint:
    """Factory: create a defined NNodeEndpoint from a NNodeEndpointValue."""
    o = NNodeEndpoint()
    o.set_value(v)
    return o

