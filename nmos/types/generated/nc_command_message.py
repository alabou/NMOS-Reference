"""Generated NMOS type: NcCommandMessage. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NInt
from nmos.types.generated.nc_array_of_command import NcArrayOfCommand, NcArrayOfCommandValue
from nmos.validators import CheckCommandMessageType

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcCommandMessageEnums:
    """JSON property name enums for NcCommandMessage."""
    MessageType = EnumRegistry.get("messageType")
    Commands = EnumRegistry.get("commands")
    pass


class NcCommandMessageValue:
    """Inner value struct for NcCommandMessage."""

    __slots__ = (
        "MessageType",
        "Commands",
    )

    def __init__(self) -> None:
        self.MessageType: NInt = NInt()
        self.Commands: NcArrayOfCommand = NcArrayOfCommand()

    def set_to_default(self) -> None:
        self.MessageType.set_to_default()
        self.Commands.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.MessageType.defined:
            raise InvalidObject("missing required member MessageType")
        if not self.Commands.defined:
            raise InvalidObject("missing required member Commands")
        if self.MessageType.defined:
            CheckCommandMessageType(self.MessageType)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.MessageType.encode(engine, NcCommandMessageEnums.MessageType)
        self.Commands.encode(engine, NcCommandMessageEnums.Commands)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcCommandMessage")

        if NcCommandMessageEnums.MessageType.s in data:
            self.MessageType.decode_value(data[NcCommandMessageEnums.MessageType.s])
        if NcCommandMessageEnums.Commands.s in data:
            self.Commands.decode_value(data[NcCommandMessageEnums.Commands.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcCommandMessageValue:
        o = NcCommandMessageValue()
        o.MessageType = self.MessageType.clone()
        o.Commands = self.Commands.clone()
        return o


class NcCommandMessage:
    """Optional object type: NcCommandMessage."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcCommandMessageValue = NcCommandMessageValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcCommandMessageValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcCommandMessageValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcCommandMessageValue | None = None) -> NcCommandMessageValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_MessageType(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MessageType

    def set_MessageType(self, v: Any) -> None:
        assert self._defined, "NcCommandMessage must be defined before setting MessageType"
        _assign_value(self._value.MessageType, v)

    def get_Commands(self) -> NcArrayOfCommand:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Commands

    def set_Commands(self, v: Any) -> None:
        assert self._defined, "NcCommandMessage must be defined before setting Commands"
        _assign_value(self._value.Commands, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcCommandMessageValue()

    def clone(self) -> NcCommandMessage:
        o = NcCommandMessage()
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
            return f"NcCommandMessage(defined)"
        return "NcCommandMessage(<undefined>)"


def make_nccommandmessage_value(v: NcCommandMessageValue) -> NcCommandMessageValue:
    """Factory: create a NcCommandMessageValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nccommandmessage(v: NcCommandMessageValue) -> NcCommandMessage:
    """Factory: create a defined NcCommandMessage from a NcCommandMessageValue."""
    o = NcCommandMessage()
    o.set_value(v)
    return o

