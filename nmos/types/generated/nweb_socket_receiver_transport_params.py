"""Generated NMOS type: NWebSocketReceiverTransportParams. DO NOT EDIT."""

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


class NWebSocketReceiverTransportParamsEnums:
    """JSON property name enums for NWebSocketReceiverTransportParams."""
    ConnectionUri = EnumRegistry.get("connection_uri")
    ConnectionAuthorization = EnumRegistry.get("connection_authorization")
    pass


class NWebSocketReceiverTransportParamsValue:
    """Inner value struct for NWebSocketReceiverTransportParams."""

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
        self.ConnectionUri.encode(engine, NWebSocketReceiverTransportParamsEnums.ConnectionUri)
        self.ConnectionAuthorization.encode(engine, NWebSocketReceiverTransportParamsEnums.ConnectionAuthorization)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NWebSocketReceiverTransportParams")

        if NWebSocketReceiverTransportParamsEnums.ConnectionUri.s in data:
            self.ConnectionUri.decode_value(data[NWebSocketReceiverTransportParamsEnums.ConnectionUri.s])
        if NWebSocketReceiverTransportParamsEnums.ConnectionAuthorization.s in data:
            self.ConnectionAuthorization.decode_value(data[NWebSocketReceiverTransportParamsEnums.ConnectionAuthorization.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NWebSocketReceiverTransportParamsValue:
        o = NWebSocketReceiverTransportParamsValue()
        o.ConnectionUri = self.ConnectionUri.clone()
        o.ConnectionAuthorization = self.ConnectionAuthorization.clone()
        return o


class NWebSocketReceiverTransportParams:
    """Optional object type: NWebSocketReceiverTransportParams."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NWebSocketReceiverTransportParamsValue = NWebSocketReceiverTransportParamsValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NWebSocketReceiverTransportParamsValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NWebSocketReceiverTransportParamsValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NWebSocketReceiverTransportParamsValue | None = None) -> NWebSocketReceiverTransportParamsValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_ConnectionUri(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ConnectionUri

    def set_ConnectionUri(self, v: Any) -> None:
        assert self._defined, "NWebSocketReceiverTransportParams must be defined before setting ConnectionUri"
        _assign_value(self._value.ConnectionUri, v)

    def get_ConnectionAuthorization(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ConnectionAuthorization

    def set_ConnectionAuthorization(self, v: Any) -> None:
        assert self._defined, "NWebSocketReceiverTransportParams must be defined before setting ConnectionAuthorization"
        _assign_value(self._value.ConnectionAuthorization, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NWebSocketReceiverTransportParamsValue()

    def clone(self) -> NWebSocketReceiverTransportParams:
        o = NWebSocketReceiverTransportParams()
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
            return f"NWebSocketReceiverTransportParams(defined)"
        return "NWebSocketReceiverTransportParams(<undefined>)"


def make_nwebsocketreceivertransportparams_value(v: NWebSocketReceiverTransportParamsValue) -> NWebSocketReceiverTransportParamsValue:
    """Factory: create a NWebSocketReceiverTransportParamsValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nwebsocketreceivertransportparams(v: NWebSocketReceiverTransportParamsValue) -> NWebSocketReceiverTransportParams:
    """Factory: create a defined NWebSocketReceiverTransportParams from a NWebSocketReceiverTransportParamsValue."""
    o = NWebSocketReceiverTransportParams()
    o.set_value(v)
    return o

