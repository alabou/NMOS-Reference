"""Generated NMOS type: NcResult. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NInt, NGeneric, NString
from nmos.validators import CheckUint16

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcResultEnums:
    """JSON property name enums for NcResult."""
    Status = EnumRegistry.get("status")
    GenericValue = EnumRegistry.get("value")
    ErrorMessage = EnumRegistry.get("errorMessage")
    pass


class NcResultValue:
    """Inner value struct for NcResult."""

    __slots__ = (
        "Status",
        "GenericValue",
        "ErrorMessage",
    )

    def __init__(self) -> None:
        self.Status: NInt = NInt()
        self.GenericValue: NGeneric = NGeneric()
        self.ErrorMessage: NString = NString()

    def set_to_default(self) -> None:
        self.Status.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Status.defined:
            raise InvalidObject("missing required member Status")
        if self.Status.defined:
            CheckUint16(self.Status)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Status.encode(engine, NcResultEnums.Status)
        self.GenericValue.encode(engine, NcResultEnums.GenericValue)
        self.ErrorMessage.encode(engine, NcResultEnums.ErrorMessage)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcResult")

        if NcResultEnums.Status.s in data:
            self.Status.decode_value(data[NcResultEnums.Status.s])
        if NcResultEnums.GenericValue.s in data:
            self.GenericValue.decode_value(data[NcResultEnums.GenericValue.s])
        if NcResultEnums.ErrorMessage.s in data:
            self.ErrorMessage.decode_value(data[NcResultEnums.ErrorMessage.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcResultValue:
        o = NcResultValue()
        o.Status = self.Status.clone()
        o.GenericValue = self.GenericValue.clone()
        o.ErrorMessage = self.ErrorMessage.clone()
        return o


class NcResult:
    """Optional object type: NcResult."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcResultValue = NcResultValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcResultValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcResultValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcResultValue | None = None) -> NcResultValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Status(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Status

    def set_Status(self, v: Any) -> None:
        assert self._defined, "NcResult must be defined before setting Status"
        _assign_value(self._value.Status, v)

    def get_GenericValue(self) -> NGeneric:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.GenericValue

    def set_GenericValue(self, v: Any) -> None:
        assert self._defined, "NcResult must be defined before setting GenericValue"
        _assign_value(self._value.GenericValue, v)

    def get_ErrorMessage(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ErrorMessage

    def set_ErrorMessage(self, v: Any) -> None:
        assert self._defined, "NcResult must be defined before setting ErrorMessage"
        _assign_value(self._value.ErrorMessage, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcResultValue()

    def clone(self) -> NcResult:
        o = NcResult()
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
            return f"NcResult(defined)"
        return "NcResult(<undefined>)"


def make_ncresult_value(v: NcResultValue) -> NcResultValue:
    """Factory: create a NcResultValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ncresult(v: NcResultValue) -> NcResult:
    """Factory: create a defined NcResult from a NcResultValue."""
    o = NcResult()
    o.set_value(v)
    return o

