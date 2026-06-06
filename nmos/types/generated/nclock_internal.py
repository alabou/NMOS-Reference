"""Generated NMOS type: NClockInternal. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString, NEnum
from nmos.validators import CheckClockNameString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NClockInternalEnums:
    """JSON property name enums for NClockInternal."""
    Name = EnumRegistry.get("name")
    RefType = EnumRegistry.get("ref_type")
    pass


class NClockInternalValue:
    """Inner value struct for NClockInternal."""

    __slots__ = (
        "Name",
        "RefType",
    )

    def __init__(self) -> None:
        self.Name: NString = NString()
        self.RefType: NEnum = NEnum()

    def set_to_default(self) -> None:
        self.Name.set_to_default()
        self.RefType.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Name.defined:
            raise InvalidObject("missing required member Name")
        if not self.RefType.defined:
            raise InvalidObject("missing required member RefType")
        if self.Name.defined:
            CheckClockNameString(self.Name)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Name.encode(engine, NClockInternalEnums.Name)
        self.RefType.encode(engine, NClockInternalEnums.RefType)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NClockInternal")

        if NClockInternalEnums.Name.s in data:
            self.Name.decode_value(data[NClockInternalEnums.Name.s])
        if NClockInternalEnums.RefType.s in data:
            self.RefType.decode_value(data[NClockInternalEnums.RefType.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NClockInternalValue:
        o = NClockInternalValue()
        o.Name = self.Name.clone()
        o.RefType = self.RefType.clone()
        return o


class NClockInternal:
    """Optional object type: NClockInternal."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NClockInternalValue = NClockInternalValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NClockInternalValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NClockInternalValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NClockInternalValue | None = None) -> NClockInternalValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Name(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Name

    def set_Name(self, v: Any) -> None:
        assert self._defined, "NClockInternal must be defined before setting Name"
        _assign_value(self._value.Name, v)

    def get_RefType(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.RefType

    def set_RefType(self, v: Any) -> None:
        assert self._defined, "NClockInternal must be defined before setting RefType"
        _assign_value(self._value.RefType, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NClockInternalValue()

    def clone(self) -> NClockInternal:
        o = NClockInternal()
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
            return f"NClockInternal(defined)"
        return "NClockInternal(<undefined>)"


def make_nclockinternal_value(v: NClockInternalValue) -> NClockInternalValue:
    """Factory: create a NClockInternalValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nclockinternal(v: NClockInternalValue) -> NClockInternal:
    """Factory: create a defined NClockInternal from a NClockInternalValue."""
    o = NClockInternal()
    o.set_value(v)
    return o

