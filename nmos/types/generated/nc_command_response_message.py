"""Generated NMOS type: NcCommandResponseMessage. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NInt
from nmos.types.generated.nc_array_of_response import NcArrayOfResponse, NcArrayOfResponseValue
from nmos.validators import CheckCommandResponseMessageType

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcCommandResponseMessageEnums:
    """JSON property name enums for NcCommandResponseMessage."""
    MessageType = EnumRegistry.get("messageType")
    Responses = EnumRegistry.get("responses")
    pass


class NcCommandResponseMessageValue:
    """Inner value struct for NcCommandResponseMessage."""

    __slots__ = (
        "MessageType",
        "Responses",
    )

    def __init__(self) -> None:
        self.MessageType: NInt = NInt()
        self.Responses: NcArrayOfResponse = NcArrayOfResponse()

    def set_to_default(self) -> None:
        self.MessageType.set_to_default()
        self.Responses.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.MessageType.defined:
            raise InvalidObject("missing required member MessageType")
        if not self.Responses.defined:
            raise InvalidObject("missing required member Responses")
        if self.MessageType.defined:
            CheckCommandResponseMessageType(self.MessageType)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.MessageType.encode(engine, NcCommandResponseMessageEnums.MessageType)
        self.Responses.encode(engine, NcCommandResponseMessageEnums.Responses)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcCommandResponseMessage")

        if NcCommandResponseMessageEnums.MessageType.s in data:
            self.MessageType.decode_value(data[NcCommandResponseMessageEnums.MessageType.s])
        if NcCommandResponseMessageEnums.Responses.s in data:
            self.Responses.decode_value(data[NcCommandResponseMessageEnums.Responses.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcCommandResponseMessageValue:
        o = NcCommandResponseMessageValue()
        o.MessageType = self.MessageType.clone()
        o.Responses = self.Responses.clone()
        return o


class NcCommandResponseMessage:
    """Optional object type: NcCommandResponseMessage."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcCommandResponseMessageValue = NcCommandResponseMessageValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcCommandResponseMessageValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcCommandResponseMessageValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcCommandResponseMessageValue | None = None) -> NcCommandResponseMessageValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_MessageType(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MessageType

    def set_MessageType(self, v: Any) -> None:
        assert self._defined, "NcCommandResponseMessage must be defined before setting MessageType"
        _assign_value(self._value.MessageType, v)

    def get_Responses(self) -> NcArrayOfResponse:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Responses

    def set_Responses(self, v: Any) -> None:
        assert self._defined, "NcCommandResponseMessage must be defined before setting Responses"
        _assign_value(self._value.Responses, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcCommandResponseMessageValue()

    def clone(self) -> NcCommandResponseMessage:
        o = NcCommandResponseMessage()
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
            return f"NcCommandResponseMessage(defined)"
        return "NcCommandResponseMessage(<undefined>)"


def make_nccommandresponsemessage_value(v: NcCommandResponseMessageValue) -> NcCommandResponseMessageValue:
    """Factory: create a NcCommandResponseMessageValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nccommandresponsemessage(v: NcCommandResponseMessageValue) -> NcCommandResponseMessage:
    """Factory: create a defined NcCommandResponseMessage from a NcCommandResponseMessageValue."""
    o = NcCommandResponseMessage()
    o.set_value(v)
    return o

