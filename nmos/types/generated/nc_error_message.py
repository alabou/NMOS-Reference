"""Generated NMOS type: NcErrorMessage. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NInt, NString
from nmos.validators import CheckErrorMessageType, CheckUint16

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcErrorMessageEnums:
    """JSON property name enums for NcErrorMessage."""
    MessageType = EnumRegistry.get("messageType")
    Status = EnumRegistry.get("status")
    ErrorMessage = EnumRegistry.get("errorMessage")
    pass


class NcErrorMessageValue:
    """Inner value struct for NcErrorMessage."""

    __slots__ = (
        "MessageType",
        "Status",
        "ErrorMessage",
    )

    def __init__(self) -> None:
        self.MessageType: NInt = NInt()
        self.Status: NInt = NInt()
        self.ErrorMessage: NString = NString()

    def set_to_default(self) -> None:
        self.MessageType.set_to_default()
        self.Status.set_to_default()
        self.ErrorMessage.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.MessageType.defined:
            raise InvalidObject("missing required member MessageType")
        if not self.Status.defined:
            raise InvalidObject("missing required member Status")
        if not self.ErrorMessage.defined:
            raise InvalidObject("missing required member ErrorMessage")
        if self.MessageType.defined:
            CheckErrorMessageType(self.MessageType)
        if self.Status.defined:
            CheckUint16(self.Status)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.MessageType.encode(engine, NcErrorMessageEnums.MessageType)
        self.Status.encode(engine, NcErrorMessageEnums.Status)
        self.ErrorMessage.encode(engine, NcErrorMessageEnums.ErrorMessage)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcErrorMessage")

        if NcErrorMessageEnums.MessageType.s in data:
            self.MessageType.decode_value(data[NcErrorMessageEnums.MessageType.s])
        if NcErrorMessageEnums.Status.s in data:
            self.Status.decode_value(data[NcErrorMessageEnums.Status.s])
        if NcErrorMessageEnums.ErrorMessage.s in data:
            self.ErrorMessage.decode_value(data[NcErrorMessageEnums.ErrorMessage.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcErrorMessageValue:
        o = NcErrorMessageValue()
        o.MessageType = self.MessageType.clone()
        o.Status = self.Status.clone()
        o.ErrorMessage = self.ErrorMessage.clone()
        return o


class NcErrorMessage:
    """Optional object type: NcErrorMessage."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcErrorMessageValue = NcErrorMessageValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcErrorMessageValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcErrorMessageValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcErrorMessageValue | None = None) -> NcErrorMessageValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_MessageType(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MessageType

    def set_MessageType(self, v: Any) -> None:
        assert self._defined, "NcErrorMessage must be defined before setting MessageType"
        _assign_value(self._value.MessageType, v)

    def get_Status(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Status

    def set_Status(self, v: Any) -> None:
        assert self._defined, "NcErrorMessage must be defined before setting Status"
        _assign_value(self._value.Status, v)

    def get_ErrorMessage(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ErrorMessage

    def set_ErrorMessage(self, v: Any) -> None:
        assert self._defined, "NcErrorMessage must be defined before setting ErrorMessage"
        _assign_value(self._value.ErrorMessage, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcErrorMessageValue()

    def clone(self) -> NcErrorMessage:
        o = NcErrorMessage()
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
            return f"NcErrorMessage(defined)"
        return "NcErrorMessage(<undefined>)"


def make_ncerrormessage_value(v: NcErrorMessageValue) -> NcErrorMessageValue:
    """Factory: create a NcErrorMessageValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ncerrormessage(v: NcErrorMessageValue) -> NcErrorMessage:
    """Factory: create a defined NcErrorMessage from a NcErrorMessageValue."""
    o = NcErrorMessage()
    o.set_value(v)
    return o

