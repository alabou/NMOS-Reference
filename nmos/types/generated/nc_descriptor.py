"""Generated NMOS type: NcDescriptor. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NNullString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcDescriptorEnums:
    """JSON property name enums for NcDescriptor."""
    Description = EnumRegistry.get("description")
    pass


class NcDescriptorValue:
    """Inner value struct for NcDescriptor."""

    __slots__ = (
        "Description",
    )

    def __init__(self) -> None:
        self.Description: NNullString = NNullString()

    def set_to_default(self) -> None:
        self.Description.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Description.defined:
            raise InvalidObject("missing required member Description")
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        self.Description.encode(engine, NcDescriptorEnums.Description)

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcDescriptor")

        if NcDescriptorEnums.Description.s in data:
            self.Description.decode_value(data[NcDescriptorEnums.Description.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcDescriptorValue:
        o = NcDescriptorValue()
        o.Description = self.Description.clone()
        return o


class NcDescriptor:
    """Optional object type: NcDescriptor."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcDescriptorValue = NcDescriptorValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcDescriptorValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcDescriptorValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcDescriptorValue | None = None) -> NcDescriptorValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Description(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Description

    def set_Description(self, v: Any) -> None:
        assert self._defined, "NcDescriptor must be defined before setting Description"
        _assign_value(self._value.Description, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcDescriptorValue()

    def clone(self) -> NcDescriptor:
        o = NcDescriptor()
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
            return f"NcDescriptor(defined)"
        return "NcDescriptor(<undefined>)"


def make_ncdescriptor_value(v: NcDescriptorValue) -> NcDescriptorValue:
    """Factory: create a NcDescriptorValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ncdescriptor(v: NcDescriptorValue) -> NcDescriptor:
    """Factory: create a defined NcDescriptor from a NcDescriptorValue."""
    o = NcDescriptor()
    o.set_value(v)
    return o

