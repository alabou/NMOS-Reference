"""Generated NMOS type: NReceiverStatus. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NEnum, NNullString
from nmos.validators import CheckReceiverStatusState

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NReceiverStatusEnums:
    """JSON property name enums for NReceiverStatus."""
    State = EnumRegistry.get("state")
    Debug = EnumRegistry.get("debug")
    pass


class NReceiverStatusValue:
    """Inner value struct for NReceiverStatus."""

    __slots__ = (
        "State",
        "Debug",
    )

    def __init__(self) -> None:
        self.State: NEnum = NEnum()
        self.Debug: NNullString = NNullString()

    def set_to_default(self) -> None:
        self.State.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.State.defined:
            raise InvalidObject("missing required member State")
        if self.State.defined:
            CheckReceiverStatusState(self.State)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.State.encode(engine, NReceiverStatusEnums.State)
        self.Debug.encode(engine, NReceiverStatusEnums.Debug)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NReceiverStatus")

        if NReceiverStatusEnums.State.s in data:
            self.State.decode_value(data[NReceiverStatusEnums.State.s])
        if NReceiverStatusEnums.Debug.s in data:
            self.Debug.decode_value(data[NReceiverStatusEnums.Debug.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NReceiverStatusValue:
        o = NReceiverStatusValue()
        o.State = self.State.clone()
        o.Debug = self.Debug.clone()
        return o


class NReceiverStatus:
    """Optional object type: NReceiverStatus."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NReceiverStatusValue = NReceiverStatusValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NReceiverStatusValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NReceiverStatusValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NReceiverStatusValue | None = None) -> NReceiverStatusValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_State(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.State

    def set_State(self, v: Any) -> None:
        assert self._defined, "NReceiverStatus must be defined before setting State"
        _assign_value(self._value.State, v)

    def get_Debug(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Debug

    def set_Debug(self, v: Any) -> None:
        assert self._defined, "NReceiverStatus must be defined before setting Debug"
        _assign_value(self._value.Debug, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NReceiverStatusValue()

    def clone(self) -> NReceiverStatus:
        o = NReceiverStatus()
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
            return f"NReceiverStatus(defined)"
        return "NReceiverStatus(<undefined>)"


def make_nreceiverstatus_value(v: NReceiverStatusValue) -> NReceiverStatusValue:
    """Factory: create a NReceiverStatusValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nreceiverstatus(v: NReceiverStatusValue) -> NReceiverStatus:
    """Factory: create a defined NReceiverStatus from a NReceiverStatusValue."""
    o = NReceiverStatus()
    o.set_value(v)
    return o

