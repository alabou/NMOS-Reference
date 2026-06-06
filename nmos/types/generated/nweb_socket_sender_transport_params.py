"""Generated NMOS type: NWebSocketSenderTransportParams. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NNullString, NNull
from nmos.validators import CheckAutoBool

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NWebSocketSenderTransportParamsEnums:
    """JSON property name enums for NWebSocketSenderTransportParams."""
    ConnectionUri = EnumRegistry.get("connection_uri")
    ConnectionAuthorization = EnumRegistry.get("connection_authorization")
    pass


class NWebSocketSenderTransportParamsValue:
    """Inner value struct for NWebSocketSenderTransportParams."""

    __slots__ = (
        "ConnectionUri",
        "ConnectionAuthorization",
    )

    def __init__(self) -> None:
        self.ConnectionUri: NNullString = NNullString()
        self.ConnectionAuthorization: NNull = NNull()

    def set_to_default(self) -> None:
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if self.ConnectionAuthorization.defined:
            CheckAutoBool(self.ConnectionAuthorization)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.ConnectionUri.encode(engine, NWebSocketSenderTransportParamsEnums.ConnectionUri)
        self.ConnectionAuthorization.encode(engine, NWebSocketSenderTransportParamsEnums.ConnectionAuthorization)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NWebSocketSenderTransportParams")

        if NWebSocketSenderTransportParamsEnums.ConnectionUri.s in data:
            self.ConnectionUri.decode_value(data[NWebSocketSenderTransportParamsEnums.ConnectionUri.s])
        if NWebSocketSenderTransportParamsEnums.ConnectionAuthorization.s in data:
            self.ConnectionAuthorization.decode_value(data[NWebSocketSenderTransportParamsEnums.ConnectionAuthorization.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NWebSocketSenderTransportParamsValue:
        o = NWebSocketSenderTransportParamsValue()
        o.ConnectionUri = self.ConnectionUri.clone()
        o.ConnectionAuthorization = self.ConnectionAuthorization.clone()
        return o


class NWebSocketSenderTransportParams:
    """Optional object type: NWebSocketSenderTransportParams."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NWebSocketSenderTransportParamsValue = NWebSocketSenderTransportParamsValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NWebSocketSenderTransportParamsValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NWebSocketSenderTransportParamsValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NWebSocketSenderTransportParamsValue | None = None) -> NWebSocketSenderTransportParamsValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_ConnectionUri(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ConnectionUri

    def set_ConnectionUri(self, v: Any) -> None:
        assert self._defined, "NWebSocketSenderTransportParams must be defined before setting ConnectionUri"
        _assign_value(self._value.ConnectionUri, v)

    def get_ConnectionAuthorization(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ConnectionAuthorization

    def set_ConnectionAuthorization(self, v: Any) -> None:
        assert self._defined, "NWebSocketSenderTransportParams must be defined before setting ConnectionAuthorization"
        _assign_value(self._value.ConnectionAuthorization, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NWebSocketSenderTransportParamsValue()

    def clone(self) -> NWebSocketSenderTransportParams:
        o = NWebSocketSenderTransportParams()
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
            return f"NWebSocketSenderTransportParams(defined)"
        return "NWebSocketSenderTransportParams(<undefined>)"


def make_nwebsocketsendertransportparams_value(v: NWebSocketSenderTransportParamsValue) -> NWebSocketSenderTransportParamsValue:
    """Factory: create a NWebSocketSenderTransportParamsValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nwebsocketsendertransportparams(v: NWebSocketSenderTransportParamsValue) -> NWebSocketSenderTransportParams:
    """Factory: create a defined NWebSocketSenderTransportParams from a NWebSocketSenderTransportParamsValue."""
    o = NWebSocketSenderTransportParams()
    o.set_value(v)
    return o

