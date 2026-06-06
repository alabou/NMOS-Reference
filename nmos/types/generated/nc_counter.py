"""Generated NMOS type: NcCounter. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString, NNullString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcCounterEnums:
    """JSON property name enums for NcCounter."""
    Name = EnumRegistry.get("name")
    Count = EnumRegistry.get("value")
    Description = EnumRegistry.get("description")
    pass


class NcCounterValue:
    """Inner value struct for NcCounter."""

    __slots__ = (
        "Name",
        "Count",
        "Description",
    )

    def __init__(self) -> None:
        self.Name: NString = NString()
        self.Count: NString = NString()
        self.Description: NNullString = NNullString()

    def set_to_default(self) -> None:
        self.Name.set_to_default()
        self.Count.set_to_default()
        self.Description.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Name.defined:
            raise InvalidObject("missing required member Name")
        if not self.Count.defined:
            raise InvalidObject("missing required member Count")
        if not self.Description.defined:
            raise InvalidObject("missing required member Description")
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Name.encode(engine, NcCounterEnums.Name)
        self.Count.encode(engine, NcCounterEnums.Count)
        self.Description.encode(engine, NcCounterEnums.Description)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcCounter")

        if NcCounterEnums.Name.s in data:
            self.Name.decode_value(data[NcCounterEnums.Name.s])
        if NcCounterEnums.Count.s in data:
            self.Count.decode_value(data[NcCounterEnums.Count.s])
        if NcCounterEnums.Description.s in data:
            self.Description.decode_value(data[NcCounterEnums.Description.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcCounterValue:
        o = NcCounterValue()
        o.Name = self.Name.clone()
        o.Count = self.Count.clone()
        o.Description = self.Description.clone()
        return o


class NcCounter:
    """Optional object type: NcCounter."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcCounterValue = NcCounterValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcCounterValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcCounterValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcCounterValue | None = None) -> NcCounterValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Name(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Name

    def set_Name(self, v: Any) -> None:
        assert self._defined, "NcCounter must be defined before setting Name"
        _assign_value(self._value.Name, v)

    def get_Count(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Count

    def set_Count(self, v: Any) -> None:
        assert self._defined, "NcCounter must be defined before setting Count"
        _assign_value(self._value.Count, v)

    def get_Description(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Description

    def set_Description(self, v: Any) -> None:
        assert self._defined, "NcCounter must be defined before setting Description"
        _assign_value(self._value.Description, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcCounterValue()

    def clone(self) -> NcCounter:
        o = NcCounter()
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
            return f"NcCounter(defined)"
        return "NcCounter(<undefined>)"


def make_nccounter_value(v: NcCounterValue) -> NcCounterValue:
    """Factory: create a NcCounterValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nccounter(v: NcCounterValue) -> NcCounter:
    """Factory: create a defined NcCounter from a NcCounterValue."""
    o = NcCounter()
    o.set_value(v)
    return o

