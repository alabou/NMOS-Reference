"""Generated NMOS type: NConstraintBool. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NArrayOfBool, NBool

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NConstraintBoolEnums:
    """JSON property name enums for NConstraintBool."""
    Enum = EnumRegistry.get("enum")
    pass


class NConstraintBoolValue:
    """Inner value struct for NConstraintBool."""

    __slots__ = (
        "Enum",
        "Original",
    )

    def __init__(self) -> None:
        self.Enum: NArrayOfBool = NArrayOfBool()
        self.Original: NBool = NBool()

    def set_to_default(self) -> None:
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Enum.encode(engine, NConstraintBoolEnums.Enum)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NConstraintBool")

        if NConstraintBoolEnums.Enum.s in data:
            self.Enum.decode_value(data[NConstraintBoolEnums.Enum.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NConstraintBoolValue:
        o = NConstraintBoolValue()
        o.Enum = self.Enum.clone()
        o.Original = self.Original.clone()
        return o


class NConstraintBool:
    """Optional object type: NConstraintBool."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NConstraintBoolValue = NConstraintBoolValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NConstraintBoolValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NConstraintBoolValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NConstraintBoolValue | None = None) -> NConstraintBoolValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Enum(self) -> NArrayOfBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Enum

    def set_Enum(self, v: Any) -> None:
        assert self._defined, "NConstraintBool must be defined before setting Enum"
        _assign_value(self._value.Enum, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NConstraintBoolValue()

    def clone(self) -> NConstraintBool:
        o = NConstraintBool()
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
            return f"NConstraintBool(defined)"
        return "NConstraintBool(<undefined>)"


def make_nconstraintbool_value(v: NConstraintBoolValue) -> NConstraintBoolValue:
    """Factory: create a NConstraintBoolValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nconstraintbool(v: NConstraintBoolValue) -> NConstraintBool:
    """Factory: create a defined NConstraintBool from a NConstraintBoolValue."""
    o = NConstraintBool()
    o.set_value(v)
    return o

