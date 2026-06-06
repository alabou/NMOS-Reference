"""Generated NMOS type: NcPropertyId. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NInt
from nmos.validators import CheckPositiveInteger

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcPropertyIdEnums:
    """JSON property name enums for NcPropertyId."""
    Level = EnumRegistry.get("level")
    Index = EnumRegistry.get("index")
    pass


class NcPropertyIdValue:
    """Inner value struct for NcPropertyId."""

    __slots__ = (
        "Level",
        "Index",
    )

    def __init__(self) -> None:
        self.Level: NInt = NInt()
        self.Index: NInt = NInt()

    def set_to_default(self) -> None:
        self.Level.set_to_default()
        self.Index.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Level.defined:
            raise InvalidObject("missing required member Level")
        if not self.Index.defined:
            raise InvalidObject("missing required member Index")
        if self.Level.defined:
            CheckPositiveInteger(self.Level)
        if self.Index.defined:
            CheckPositiveInteger(self.Index)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Level.encode(engine, NcPropertyIdEnums.Level)
        self.Index.encode(engine, NcPropertyIdEnums.Index)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcPropertyId")

        if NcPropertyIdEnums.Level.s in data:
            self.Level.decode_value(data[NcPropertyIdEnums.Level.s])
        if NcPropertyIdEnums.Index.s in data:
            self.Index.decode_value(data[NcPropertyIdEnums.Index.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcPropertyIdValue:
        o = NcPropertyIdValue()
        o.Level = self.Level.clone()
        o.Index = self.Index.clone()
        return o


class NcPropertyId:
    """Optional object type: NcPropertyId."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcPropertyIdValue = NcPropertyIdValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcPropertyIdValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcPropertyIdValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcPropertyIdValue | None = None) -> NcPropertyIdValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Level(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Level

    def set_Level(self, v: Any) -> None:
        assert self._defined, "NcPropertyId must be defined before setting Level"
        _assign_value(self._value.Level, v)

    def get_Index(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Index

    def set_Index(self, v: Any) -> None:
        assert self._defined, "NcPropertyId must be defined before setting Index"
        _assign_value(self._value.Index, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcPropertyIdValue()

    def clone(self) -> NcPropertyId:
        o = NcPropertyId()
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
            return f"NcPropertyId(defined)"
        return "NcPropertyId(<undefined>)"


def make_ncpropertyid_value(v: NcPropertyIdValue) -> NcPropertyIdValue:
    """Factory: create a NcPropertyIdValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ncpropertyid(v: NcPropertyIdValue) -> NcPropertyId:
    """Factory: create a defined NcPropertyId from a NcPropertyIdValue."""
    o = NcPropertyId()
    o.set_value(v)
    return o

