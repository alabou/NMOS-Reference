"""Generated NMOS type: NSenderSupportedConstraints. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NArrayOfEnum

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NSenderSupportedConstraintsEnums:
    """JSON property name enums for NSenderSupportedConstraints."""
    ParameterConstraints = EnumRegistry.get("parameter_constraints")
    pass


class NSenderSupportedConstraintsValue:
    """Inner value struct for NSenderSupportedConstraints."""

    __slots__ = (
        "ParameterConstraints",
    )

    def __init__(self) -> None:
        self.ParameterConstraints: NArrayOfEnum = NArrayOfEnum()

    def set_to_default(self) -> None:
        self.ParameterConstraints.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.ParameterConstraints.defined:
            raise InvalidObject("missing required member ParameterConstraints")
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.ParameterConstraints.encode(engine, NSenderSupportedConstraintsEnums.ParameterConstraints)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NSenderSupportedConstraints")

        if NSenderSupportedConstraintsEnums.ParameterConstraints.s in data:
            self.ParameterConstraints.decode_value(data[NSenderSupportedConstraintsEnums.ParameterConstraints.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NSenderSupportedConstraintsValue:
        o = NSenderSupportedConstraintsValue()
        o.ParameterConstraints = self.ParameterConstraints.clone()
        return o


class NSenderSupportedConstraints:
    """Optional object type: NSenderSupportedConstraints."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NSenderSupportedConstraintsValue = NSenderSupportedConstraintsValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NSenderSupportedConstraintsValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NSenderSupportedConstraintsValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NSenderSupportedConstraintsValue | None = None) -> NSenderSupportedConstraintsValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_ParameterConstraints(self) -> NArrayOfEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ParameterConstraints

    def set_ParameterConstraints(self, v: Any) -> None:
        assert self._defined, "NSenderSupportedConstraints must be defined before setting ParameterConstraints"
        _assign_value(self._value.ParameterConstraints, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NSenderSupportedConstraintsValue()

    def clone(self) -> NSenderSupportedConstraints:
        o = NSenderSupportedConstraints()
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
            return f"NSenderSupportedConstraints(defined)"
        return "NSenderSupportedConstraints(<undefined>)"


def make_nsendersupportedconstraints_value(v: NSenderSupportedConstraintsValue) -> NSenderSupportedConstraintsValue:
    """Factory: create a NSenderSupportedConstraintsValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nsendersupportedconstraints(v: NSenderSupportedConstraintsValue) -> NSenderSupportedConstraints:
    """Factory: create a defined NSenderSupportedConstraints from a NSenderSupportedConstraintsValue."""
    o = NSenderSupportedConstraints()
    o.set_value(v)
    return o

