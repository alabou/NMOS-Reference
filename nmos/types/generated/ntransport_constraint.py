"""Generated NMOS type: NTransportConstraint. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NFloat, NArrayOfNull, NString
from nmos.validators import CheckTransportConstraintEnumLength

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NTransportConstraintEnums:
    """JSON property name enums for NTransportConstraint."""
    Minimum = EnumRegistry.get("minimum")
    Maximum = EnumRegistry.get("maximum")
    Enum = EnumRegistry.get("enum")
    Pattern = EnumRegistry.get("pattern")
    Description = EnumRegistry.get("description")
    pass


class NTransportConstraintValue:
    """Inner value struct for NTransportConstraint."""

    __slots__ = (
        "Minimum",
        "Maximum",
        "Enum",
        "Pattern",
        "Description",
    )

    def __init__(self) -> None:
        self.Minimum: NFloat = NFloat()
        self.Maximum: NFloat = NFloat()
        self.Enum: NArrayOfNull = NArrayOfNull()
        self.Pattern: NString = NString()
        self.Description: NString = NString()

    def set_to_default(self) -> None:
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if self.Enum.defined:
            CheckTransportConstraintEnumLength(self.Enum)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Minimum.encode(engine, NTransportConstraintEnums.Minimum)
        self.Maximum.encode(engine, NTransportConstraintEnums.Maximum)
        self.Enum.encode(engine, NTransportConstraintEnums.Enum)
        self.Pattern.encode(engine, NTransportConstraintEnums.Pattern)
        self.Description.encode(engine, NTransportConstraintEnums.Description)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NTransportConstraint")

        if NTransportConstraintEnums.Minimum.s in data:
            self.Minimum.decode_value(data[NTransportConstraintEnums.Minimum.s])
        if NTransportConstraintEnums.Maximum.s in data:
            self.Maximum.decode_value(data[NTransportConstraintEnums.Maximum.s])
        if NTransportConstraintEnums.Enum.s in data:
            self.Enum.decode_value(data[NTransportConstraintEnums.Enum.s])
        if NTransportConstraintEnums.Pattern.s in data:
            self.Pattern.decode_value(data[NTransportConstraintEnums.Pattern.s])
        if NTransportConstraintEnums.Description.s in data:
            self.Description.decode_value(data[NTransportConstraintEnums.Description.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NTransportConstraintValue:
        o = NTransportConstraintValue()
        o.Minimum = self.Minimum.clone()
        o.Maximum = self.Maximum.clone()
        o.Enum = self.Enum.clone()
        o.Pattern = self.Pattern.clone()
        o.Description = self.Description.clone()
        return o


class NTransportConstraint:
    """Optional object type: NTransportConstraint."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NTransportConstraintValue = NTransportConstraintValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NTransportConstraintValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NTransportConstraintValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NTransportConstraintValue | None = None) -> NTransportConstraintValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Minimum(self) -> NFloat:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Minimum

    def set_Minimum(self, v: Any) -> None:
        assert self._defined, "NTransportConstraint must be defined before setting Minimum"
        _assign_value(self._value.Minimum, v)

    def get_Maximum(self) -> NFloat:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Maximum

    def set_Maximum(self, v: Any) -> None:
        assert self._defined, "NTransportConstraint must be defined before setting Maximum"
        _assign_value(self._value.Maximum, v)

    def get_Enum(self) -> NArrayOfNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Enum

    def set_Enum(self, v: Any) -> None:
        assert self._defined, "NTransportConstraint must be defined before setting Enum"
        _assign_value(self._value.Enum, v)

    def get_Pattern(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Pattern

    def set_Pattern(self, v: Any) -> None:
        assert self._defined, "NTransportConstraint must be defined before setting Pattern"
        _assign_value(self._value.Pattern, v)

    def get_Description(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Description

    def set_Description(self, v: Any) -> None:
        assert self._defined, "NTransportConstraint must be defined before setting Description"
        _assign_value(self._value.Description, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NTransportConstraintValue()

    def clone(self) -> NTransportConstraint:
        o = NTransportConstraint()
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
            return f"NTransportConstraint(defined)"
        return "NTransportConstraint(<undefined>)"


def make_ntransportconstraint_value(v: NTransportConstraintValue) -> NTransportConstraintValue:
    """Factory: create a NTransportConstraintValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ntransportconstraint(v: NTransportConstraintValue) -> NTransportConstraint:
    """Factory: create a defined NTransportConstraint from a NTransportConstraintValue."""
    o = NTransportConstraint()
    o.set_value(v)
    return o

