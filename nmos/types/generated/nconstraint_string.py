"""Generated NMOS type: NConstraintString. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NArrayOfEnum, NBool

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NConstraintStringEnums:
    """JSON property name enums for NConstraintString."""
    Enum = EnumRegistry.get("enum")
    pass


class NConstraintStringValue:
    """Inner value struct for NConstraintString."""

    __slots__ = (
        "Enum",
        "Original",
    )

    def __init__(self) -> None:
        self.Enum: NArrayOfEnum = NArrayOfEnum()
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
        self.Enum.encode(engine, NConstraintStringEnums.Enum)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NConstraintString")

        if NConstraintStringEnums.Enum.s in data:
            self.Enum.decode_value(data[NConstraintStringEnums.Enum.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NConstraintStringValue:
        o = NConstraintStringValue()
        o.Enum = self.Enum.clone()
        o.Original = self.Original.clone()
        return o


class NConstraintString:
    """Optional object type: NConstraintString."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NConstraintStringValue = NConstraintStringValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NConstraintStringValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NConstraintStringValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NConstraintStringValue | None = None) -> NConstraintStringValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Enum(self) -> NArrayOfEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Enum

    def set_Enum(self, v: Any) -> None:
        assert self._defined, "NConstraintString must be defined before setting Enum"
        _assign_value(self._value.Enum, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NConstraintStringValue()

    def clone(self) -> NConstraintString:
        o = NConstraintString()
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
            return f"NConstraintString(defined)"
        return "NConstraintString(<undefined>)"


def make_nconstraintstring_value(v: NConstraintStringValue) -> NConstraintStringValue:
    """Factory: create a NConstraintStringValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nconstraintstring(v: NConstraintStringValue) -> NConstraintString:
    """Factory: create a defined NConstraintString from a NConstraintStringValue."""
    o = NConstraintString()
    o.set_value(v)
    return o

