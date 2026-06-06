"""Generated NMOS type: NRational. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NInt

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NRationalEnums:
    """JSON property name enums for NRational."""
    Numerator = EnumRegistry.get("numerator")
    Denominator = EnumRegistry.get("denominator")
    pass


class NRationalValue:
    """Inner value struct for NRational."""

    __slots__ = (
        "Numerator",
        "Denominator",
    )

    def __init__(self) -> None:
        self.Numerator: NInt = NInt()
        self.Denominator: NInt = NInt()

    def set_to_default(self) -> None:
        self.Numerator.set_to_default()
        _assign_value(self.Denominator, 1)
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        if not self.Denominator.defined:
            _assign_value(self.Denominator, 1)
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Numerator.defined:
            raise InvalidObject("missing required member Numerator")
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Numerator.encode(engine, NRationalEnums.Numerator)
        self.Denominator.encode(engine, NRationalEnums.Denominator)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NRational")

        if NRationalEnums.Numerator.s in data:
            self.Numerator.decode_value(data[NRationalEnums.Numerator.s])
        if NRationalEnums.Denominator.s in data:
            self.Denominator.decode_value(data[NRationalEnums.Denominator.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NRationalValue:
        o = NRationalValue()
        o.Numerator = self.Numerator.clone()
        o.Denominator = self.Denominator.clone()
        return o


class NRational:
    """Optional object type: NRational."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NRationalValue = NRationalValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NRationalValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NRationalValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NRationalValue | None = None) -> NRationalValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Numerator(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Numerator

    def set_Numerator(self, v: Any) -> None:
        assert self._defined, "NRational must be defined before setting Numerator"
        _assign_value(self._value.Numerator, v)

    def get_Denominator(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Denominator

    def set_Denominator(self, v: Any) -> None:
        assert self._defined, "NRational must be defined before setting Denominator"
        _assign_value(self._value.Denominator, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NRationalValue()

    def clone(self) -> NRational:
        o = NRational()
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
            return f"NRational(defined)"
        return "NRational(<undefined>)"


def make_nrational_value(v: NRationalValue) -> NRationalValue:
    """Factory: create a NRationalValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nrational(v: NRationalValue) -> NRational:
    """Factory: create a defined NRational from a NRationalValue."""
    o = NRational()
    o.set_value(v)
    return o

