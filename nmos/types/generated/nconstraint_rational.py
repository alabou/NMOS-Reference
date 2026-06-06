"""Generated NMOS type: NConstraintRational. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NBool
from nmos.types.generated.narray_of_rational import NArrayOfRational, NArrayOfRationalValue
from nmos.types.generated.nrational import NRational, NRationalValue

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NConstraintRationalEnums:
    """JSON property name enums for NConstraintRational."""
    Enum = EnumRegistry.get("enum")
    Minimum = EnumRegistry.get("minimum")
    Maximum = EnumRegistry.get("maximum")
    pass


class NConstraintRationalValue:
    """Inner value struct for NConstraintRational."""

    __slots__ = (
        "Enum",
        "Minimum",
        "Maximum",
        "Original",
    )

    def __init__(self) -> None:
        self.Enum: NArrayOfRational = NArrayOfRational()
        self.Minimum: NRational = NRational()
        self.Maximum: NRational = NRational()
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
        self.Enum.encode(engine, NConstraintRationalEnums.Enum)
        self.Minimum.encode(engine, NConstraintRationalEnums.Minimum)
        self.Maximum.encode(engine, NConstraintRationalEnums.Maximum)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NConstraintRational")

        if NConstraintRationalEnums.Enum.s in data:
            self.Enum.decode_value(data[NConstraintRationalEnums.Enum.s])
        if NConstraintRationalEnums.Minimum.s in data:
            self.Minimum.decode_value(data[NConstraintRationalEnums.Minimum.s])
        if NConstraintRationalEnums.Maximum.s in data:
            self.Maximum.decode_value(data[NConstraintRationalEnums.Maximum.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NConstraintRationalValue:
        o = NConstraintRationalValue()
        o.Enum = self.Enum.clone()
        o.Minimum = self.Minimum.clone()
        o.Maximum = self.Maximum.clone()
        o.Original = self.Original.clone()
        return o


class NConstraintRational:
    """Optional object type: NConstraintRational."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NConstraintRationalValue = NConstraintRationalValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NConstraintRationalValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NConstraintRationalValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NConstraintRationalValue | None = None) -> NConstraintRationalValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Enum(self) -> NArrayOfRational:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Enum

    def set_Enum(self, v: Any) -> None:
        assert self._defined, "NConstraintRational must be defined before setting Enum"
        _assign_value(self._value.Enum, v)

    def get_Minimum(self) -> NRational:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Minimum

    def set_Minimum(self, v: Any) -> None:
        assert self._defined, "NConstraintRational must be defined before setting Minimum"
        _assign_value(self._value.Minimum, v)

    def get_Maximum(self) -> NRational:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Maximum

    def set_Maximum(self, v: Any) -> None:
        assert self._defined, "NConstraintRational must be defined before setting Maximum"
        _assign_value(self._value.Maximum, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NConstraintRationalValue()

    def clone(self) -> NConstraintRational:
        o = NConstraintRational()
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
            return f"NConstraintRational(defined)"
        return "NConstraintRational(<undefined>)"


def make_nconstraintrational_value(v: NConstraintRationalValue) -> NConstraintRationalValue:
    """Factory: create a NConstraintRationalValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nconstraintrational(v: NConstraintRationalValue) -> NConstraintRational:
    """Factory: create a defined NConstraintRational from a NConstraintRationalValue."""
    o = NConstraintRational()
    o.set_value(v)
    return o

