"""Generated NMOS type: NcResponse. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NInt
from nmos.types.generated.nc_result import NcResult, NcResultValue
from nmos.validators import CheckPositiveUint16

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcResponseEnums:
    """JSON property name enums for NcResponse."""
    Handle = EnumRegistry.get("handle")
    Result = EnumRegistry.get("result")
    pass


class NcResponseValue:
    """Inner value struct for NcResponse."""

    __slots__ = (
        "Handle",
        "Result",
    )

    def __init__(self) -> None:
        self.Handle: NInt = NInt()
        self.Result: NcResult = NcResult()

    def set_to_default(self) -> None:
        self.Handle.set_to_default()
        self.Result.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Handle.defined:
            raise InvalidObject("missing required member Handle")
        if not self.Result.defined:
            raise InvalidObject("missing required member Result")
        if self.Handle.defined:
            CheckPositiveUint16(self.Handle)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Handle.encode(engine, NcResponseEnums.Handle)
        self.Result.encode(engine, NcResponseEnums.Result)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcResponse")

        if NcResponseEnums.Handle.s in data:
            self.Handle.decode_value(data[NcResponseEnums.Handle.s])
        if NcResponseEnums.Result.s in data:
            self.Result.decode_value(data[NcResponseEnums.Result.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcResponseValue:
        o = NcResponseValue()
        o.Handle = self.Handle.clone()
        o.Result = self.Result.clone()
        return o


class NcResponse:
    """Optional object type: NcResponse."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcResponseValue = NcResponseValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcResponseValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcResponseValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcResponseValue | None = None) -> NcResponseValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Handle(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Handle

    def set_Handle(self, v: Any) -> None:
        assert self._defined, "NcResponse must be defined before setting Handle"
        _assign_value(self._value.Handle, v)

    def get_Result(self) -> NcResult:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Result

    def set_Result(self, v: Any) -> None:
        assert self._defined, "NcResponse must be defined before setting Result"
        _assign_value(self._value.Result, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcResponseValue()

    def clone(self) -> NcResponse:
        o = NcResponse()
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
            return f"NcResponse(defined)"
        return "NcResponse(<undefined>)"


def make_ncresponse_value(v: NcResponseValue) -> NcResponseValue:
    """Factory: create a NcResponseValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ncresponse(v: NcResponseValue) -> NcResponse:
    """Factory: create a defined NcResponse from a NcResponseValue."""
    o = NcResponse()
    o.set_value(v)
    return o

