"""Generated NMOS type: NSenderActiveConstraints. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.types.generated.narray_of_constraint_set import NArrayOfConstraintSet, NArrayOfConstraintSetValue

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NSenderActiveConstraintsEnums:
    """JSON property name enums for NSenderActiveConstraints."""
    ConstraintSets = EnumRegistry.get("constraint_sets")
    pass


class NSenderActiveConstraintsValue:
    """Inner value struct for NSenderActiveConstraints."""

    __slots__ = (
        "ConstraintSets",
    )

    def __init__(self) -> None:
        self.ConstraintSets: NArrayOfConstraintSet = NArrayOfConstraintSet()

    def set_to_default(self) -> None:
        self.ConstraintSets.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.ConstraintSets.defined:
            raise InvalidObject("missing required member ConstraintSets")
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.ConstraintSets.encode(engine, NSenderActiveConstraintsEnums.ConstraintSets)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NSenderActiveConstraints")

        if NSenderActiveConstraintsEnums.ConstraintSets.s in data:
            self.ConstraintSets.decode_value(data[NSenderActiveConstraintsEnums.ConstraintSets.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NSenderActiveConstraintsValue:
        o = NSenderActiveConstraintsValue()
        o.ConstraintSets = self.ConstraintSets.clone()
        return o


class NSenderActiveConstraints:
    """Optional object type: NSenderActiveConstraints."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NSenderActiveConstraintsValue = NSenderActiveConstraintsValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NSenderActiveConstraintsValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NSenderActiveConstraintsValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NSenderActiveConstraintsValue | None = None) -> NSenderActiveConstraintsValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_ConstraintSets(self) -> NArrayOfConstraintSet:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ConstraintSets

    def set_ConstraintSets(self, v: Any) -> None:
        assert self._defined, "NSenderActiveConstraints must be defined before setting ConstraintSets"
        _assign_value(self._value.ConstraintSets, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NSenderActiveConstraintsValue()

    def clone(self) -> NSenderActiveConstraints:
        o = NSenderActiveConstraints()
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
            return f"NSenderActiveConstraints(defined)"
        return "NSenderActiveConstraints(<undefined>)"


def make_nsenderactiveconstraints_value(v: NSenderActiveConstraintsValue) -> NSenderActiveConstraintsValue:
    """Factory: create a NSenderActiveConstraintsValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nsenderactiveconstraints(v: NSenderActiveConstraintsValue) -> NSenderActiveConstraints:
    """Factory: create a defined NSenderActiveConstraints from a NSenderActiveConstraintsValue."""
    o = NSenderActiveConstraints()
    o.set_value(v)
    return o

