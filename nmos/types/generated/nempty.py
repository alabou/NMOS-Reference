"""Generated NMOS type: NEmpty. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NEmptyEnums:
    """JSON property name enums for NEmpty."""
    pass


class NEmptyValue:
    """Inner value struct for NEmpty."""

    __slots__ = (
    )

    def __init__(self) -> None:
        pass

    def set_to_default(self) -> None:
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NEmpty")


        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NEmptyValue:
        o = NEmptyValue()
        return o


class NEmpty:
    """Optional object type: NEmpty."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NEmptyValue = NEmptyValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NEmptyValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NEmptyValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NEmptyValue | None = None) -> NEmptyValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---

    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NEmptyValue()

    def clone(self) -> NEmpty:
        o = NEmpty()
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
            return f"NEmpty(defined)"
        return "NEmpty(<undefined>)"


def make_nempty_value(v: NEmptyValue) -> NEmptyValue:
    """Factory: create a NEmptyValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nempty(v: NEmptyValue) -> NEmpty:
    """Factory: create a defined NEmpty from a NEmptyValue."""
    o = NEmpty()
    o.set_value(v)
    return o

