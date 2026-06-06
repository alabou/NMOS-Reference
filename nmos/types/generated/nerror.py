"""Generated NMOS type: NError. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NInt, NString, NNullString
from nmos.validators import CheckErrorCode

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NErrorEnums:
    """JSON property name enums for NError."""
    Code = EnumRegistry.get("code")
    Error = EnumRegistry.get("error")
    Debug = EnumRegistry.get("debug")
    pass


class NErrorValue:
    """Inner value struct for NError."""

    __slots__ = (
        "Code",
        "Error",
        "Debug",
    )

    def __init__(self) -> None:
        self.Code: NInt = NInt()
        self.Error: NString = NString()
        self.Debug: NNullString = NNullString()

    def set_to_default(self) -> None:
        self.Code.set_to_default()
        self.Error.set_to_default()
        self.Debug.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Code.defined:
            raise InvalidObject("missing required member Code")
        if not self.Error.defined:
            raise InvalidObject("missing required member Error")
        if not self.Debug.defined:
            raise InvalidObject("missing required member Debug")
        if self.Code.defined:
            CheckErrorCode(self.Code)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Code.encode(engine, NErrorEnums.Code)
        self.Error.encode(engine, NErrorEnums.Error)
        self.Debug.encode(engine, NErrorEnums.Debug)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NError")

        if NErrorEnums.Code.s in data:
            self.Code.decode_value(data[NErrorEnums.Code.s])
        if NErrorEnums.Error.s in data:
            self.Error.decode_value(data[NErrorEnums.Error.s])
        if NErrorEnums.Debug.s in data:
            self.Debug.decode_value(data[NErrorEnums.Debug.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NErrorValue:
        o = NErrorValue()
        o.Code = self.Code.clone()
        o.Error = self.Error.clone()
        o.Debug = self.Debug.clone()
        return o


class NError:
    """Optional object type: NError."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NErrorValue = NErrorValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NErrorValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NErrorValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NErrorValue | None = None) -> NErrorValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Code(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Code

    def set_Code(self, v: Any) -> None:
        assert self._defined, "NError must be defined before setting Code"
        _assign_value(self._value.Code, v)

    def get_Error(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Error

    def set_Error(self, v: Any) -> None:
        assert self._defined, "NError must be defined before setting Error"
        _assign_value(self._value.Error, v)

    def get_Debug(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Debug

    def set_Debug(self, v: Any) -> None:
        assert self._defined, "NError must be defined before setting Debug"
        _assign_value(self._value.Debug, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NErrorValue()

    def clone(self) -> NError:
        o = NError()
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
            return f"NError(defined)"
        return "NError(<undefined>)"


def make_nerror_value(v: NErrorValue) -> NErrorValue:
    """Factory: create a NErrorValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nerror(v: NErrorValue) -> NError:
    """Factory: create a defined NError from a NErrorValue."""
    o = NError()
    o.set_value(v)
    return o

