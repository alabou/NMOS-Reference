"""Generated NMOS type: NActivation. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NNullString
from nmos.validators import CheckActivationMode

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NActivationEnums:
    """JSON property name enums for NActivation."""
    Mode = EnumRegistry.get("mode")
    RequestedTime = EnumRegistry.get("requested_time")
    ActivationTime = EnumRegistry.get("activation_time")
    pass


class NActivationValue:
    """Inner value struct for NActivation."""

    __slots__ = (
        "Mode",
        "RequestedTime",
        "ActivationTime",
    )

    def __init__(self) -> None:
        self.Mode: NNullString = NNullString()
        self.RequestedTime: NNullString = NNullString()
        self.ActivationTime: NNullString = NNullString()

    def set_to_default(self) -> None:
        self.Mode.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Mode.defined:
            raise InvalidObject("missing required member Mode")
        if self.Mode.defined:
            CheckActivationMode(self.Mode)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Mode.encode(engine, NActivationEnums.Mode)
        self.RequestedTime.encode(engine, NActivationEnums.RequestedTime)
        self.ActivationTime.encode(engine, NActivationEnums.ActivationTime)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NActivation")

        if NActivationEnums.Mode.s in data:
            self.Mode.decode_value(data[NActivationEnums.Mode.s])
        if NActivationEnums.RequestedTime.s in data:
            self.RequestedTime.decode_value(data[NActivationEnums.RequestedTime.s])
        if NActivationEnums.ActivationTime.s in data:
            self.ActivationTime.decode_value(data[NActivationEnums.ActivationTime.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NActivationValue:
        o = NActivationValue()
        o.Mode = self.Mode.clone()
        o.RequestedTime = self.RequestedTime.clone()
        o.ActivationTime = self.ActivationTime.clone()
        return o


class NActivation:
    """Optional object type: NActivation."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NActivationValue = NActivationValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NActivationValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NActivationValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NActivationValue | None = None) -> NActivationValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Mode(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Mode

    def set_Mode(self, v: Any) -> None:
        assert self._defined, "NActivation must be defined before setting Mode"
        _assign_value(self._value.Mode, v)

    def get_RequestedTime(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.RequestedTime

    def set_RequestedTime(self, v: Any) -> None:
        assert self._defined, "NActivation must be defined before setting RequestedTime"
        _assign_value(self._value.RequestedTime, v)

    def get_ActivationTime(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ActivationTime

    def set_ActivationTime(self, v: Any) -> None:
        assert self._defined, "NActivation must be defined before setting ActivationTime"
        _assign_value(self._value.ActivationTime, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NActivationValue()

    def clone(self) -> NActivation:
        o = NActivation()
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
            return f"NActivation(defined)"
        return "NActivation(<undefined>)"


def make_nactivation_value(v: NActivationValue) -> NActivationValue:
    """Factory: create a NActivationValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nactivation(v: NActivationValue) -> NActivation:
    """Factory: create a defined NActivation from a NActivationValue."""
    o = NActivation()
    o.set_value(v)
    return o

