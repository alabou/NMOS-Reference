"""Generated NMOS type: NConstraintFloat. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NArrayOfFloat, NFloat, NBool

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NConstraintFloatEnums:
    """JSON property name enums for NConstraintFloat."""
    Enum = EnumRegistry.get("enum")
    Minimum = EnumRegistry.get("minimum")
    Maximum = EnumRegistry.get("maximum")
    pass


class NConstraintFloatValue:
    """Inner value struct for NConstraintFloat."""

    __slots__ = (
        "Enum",
        "Minimum",
        "Maximum",
        "Original",
    )

    def __init__(self) -> None:
        self.Enum: NArrayOfFloat = NArrayOfFloat()
        self.Minimum: NFloat = NFloat()
        self.Maximum: NFloat = NFloat()
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
        self.Enum.encode(engine, NConstraintFloatEnums.Enum)
        self.Minimum.encode(engine, NConstraintFloatEnums.Minimum)
        self.Maximum.encode(engine, NConstraintFloatEnums.Maximum)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NConstraintFloat")

        if NConstraintFloatEnums.Enum.s in data:
            self.Enum.decode_value(data[NConstraintFloatEnums.Enum.s])
        if NConstraintFloatEnums.Minimum.s in data:
            self.Minimum.decode_value(data[NConstraintFloatEnums.Minimum.s])
        if NConstraintFloatEnums.Maximum.s in data:
            self.Maximum.decode_value(data[NConstraintFloatEnums.Maximum.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NConstraintFloatValue:
        o = NConstraintFloatValue()
        o.Enum = self.Enum.clone()
        o.Minimum = self.Minimum.clone()
        o.Maximum = self.Maximum.clone()
        o.Original = self.Original.clone()
        return o


class NConstraintFloat:
    """Optional object type: NConstraintFloat."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NConstraintFloatValue = NConstraintFloatValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NConstraintFloatValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NConstraintFloatValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NConstraintFloatValue | None = None) -> NConstraintFloatValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Enum(self) -> NArrayOfFloat:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Enum

    def set_Enum(self, v: Any) -> None:
        assert self._defined, "NConstraintFloat must be defined before setting Enum"
        _assign_value(self._value.Enum, v)

    def get_Minimum(self) -> NFloat:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Minimum

    def set_Minimum(self, v: Any) -> None:
        assert self._defined, "NConstraintFloat must be defined before setting Minimum"
        _assign_value(self._value.Minimum, v)

    def get_Maximum(self) -> NFloat:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Maximum

    def set_Maximum(self, v: Any) -> None:
        assert self._defined, "NConstraintFloat must be defined before setting Maximum"
        _assign_value(self._value.Maximum, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NConstraintFloatValue()

    def clone(self) -> NConstraintFloat:
        o = NConstraintFloat()
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
            return f"NConstraintFloat(defined)"
        return "NConstraintFloat(<undefined>)"


def make_nconstraintfloat_value(v: NConstraintFloatValue) -> NConstraintFloatValue:
    """Factory: create a NConstraintFloatValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nconstraintfloat(v: NConstraintFloatValue) -> NConstraintFloat:
    """Factory: create a defined NConstraintFloat from a NConstraintFloatValue."""
    o = NConstraintFloat()
    o.set_value(v)
    return o

