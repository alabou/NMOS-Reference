"""Generated NMOS type: NcEventId. DO NOT EDIT."""

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


class NcEventIdEnums:
    """JSON property name enums for NcEventId."""
    Level = EnumRegistry.get("level")
    Index = EnumRegistry.get("index")
    pass


class NcEventIdValue:
    """Inner value struct for NcEventId."""

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
        self.Level.encode(engine, NcEventIdEnums.Level)
        self.Index.encode(engine, NcEventIdEnums.Index)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcEventId")

        if NcEventIdEnums.Level.s in data:
            self.Level.decode_value(data[NcEventIdEnums.Level.s])
        if NcEventIdEnums.Index.s in data:
            self.Index.decode_value(data[NcEventIdEnums.Index.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcEventIdValue:
        o = NcEventIdValue()
        o.Level = self.Level.clone()
        o.Index = self.Index.clone()
        return o


class NcEventId:
    """Optional object type: NcEventId."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcEventIdValue = NcEventIdValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcEventIdValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcEventIdValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcEventIdValue | None = None) -> NcEventIdValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Level(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Level

    def set_Level(self, v: Any) -> None:
        assert self._defined, "NcEventId must be defined before setting Level"
        _assign_value(self._value.Level, v)

    def get_Index(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Index

    def set_Index(self, v: Any) -> None:
        assert self._defined, "NcEventId must be defined before setting Index"
        _assign_value(self._value.Index, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcEventIdValue()

    def clone(self) -> NcEventId:
        o = NcEventId()
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
            return f"NcEventId(defined)"
        return "NcEventId(<undefined>)"


def make_nceventid_value(v: NcEventIdValue) -> NcEventIdValue:
    """Factory: create a NcEventIdValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nceventid(v: NcEventIdValue) -> NcEventId:
    """Factory: create a defined NcEventId from a NcEventIdValue."""
    o = NcEventId()
    o.set_value(v)
    return o

