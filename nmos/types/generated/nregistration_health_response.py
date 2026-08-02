"""Generated NMOS type: NRegistrationHealthResponse. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString
from nmos.validators import CheckHealthString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NRegistrationHealthResponseEnums:
    """JSON property name enums for NRegistrationHealthResponse."""
    Health = EnumRegistry.get("health")
    pass


class NRegistrationHealthResponseValue:
    """Inner value struct for NRegistrationHealthResponse."""

    __slots__ = (
        "Health",
    )

    def __init__(self) -> None:
        self.Health: NString = NString()

    def set_to_default(self) -> None:
        self.Health.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Health.defined:
            raise InvalidObject("missing required member Health")
        if self.Health.defined:
            CheckHealthString(self.Health)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Health.encode(engine, NRegistrationHealthResponseEnums.Health)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NRegistrationHealthResponse")

        if NRegistrationHealthResponseEnums.Health.s in data:
            self.Health.decode_value(data[NRegistrationHealthResponseEnums.Health.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NRegistrationHealthResponseValue:
        o = NRegistrationHealthResponseValue()
        o.Health = self.Health.clone()
        return o


class NRegistrationHealthResponse:
    """Optional object type: NRegistrationHealthResponse."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NRegistrationHealthResponseValue = NRegistrationHealthResponseValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NRegistrationHealthResponseValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NRegistrationHealthResponseValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NRegistrationHealthResponseValue | None = None) -> NRegistrationHealthResponseValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Health(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Health

    def set_Health(self, v: Any) -> None:
        assert self._defined, "NRegistrationHealthResponse must be defined before setting Health"
        _assign_value(self._value.Health, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NRegistrationHealthResponseValue()

    def clone(self) -> NRegistrationHealthResponse:
        o = NRegistrationHealthResponse()
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
            return f"NRegistrationHealthResponse(defined)"
        return "NRegistrationHealthResponse(<undefined>)"


def make_nregistrationhealthresponse_value(v: NRegistrationHealthResponseValue) -> NRegistrationHealthResponseValue:
    """Factory: create a NRegistrationHealthResponseValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nregistrationhealthresponse(v: NRegistrationHealthResponseValue) -> NRegistrationHealthResponse:
    """Factory: create a defined NRegistrationHealthResponse from a NRegistrationHealthResponseValue."""
    o = NRegistrationHealthResponse()
    o.set_value(v)
    return o

