"""Generated NMOS type: NcManager. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.types.generated.nc_object import NcObject, NcObjectValue

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcManagerEnums:
    """JSON property name enums for NcManager."""
    pass


class NcManagerValue:
    """Inner value struct for NcManager."""

    __slots__ = (
        "Base",
    )

    def __init__(self) -> None:
        self.Base: NcObjectValue = NcObjectValue()

    def set_to_default(self) -> None:
        self.Base = NcObjectValue()
        self.Base.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        self.Base.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        self.Base.encode(engine, None)

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcManager")

        self.Base.decode(engine, data)

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcManagerValue:
        o = NcManagerValue()
        o.Base = self.Base.clone()
        return o


class NcManager:
    """Optional object type: NcManager."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcManagerValue = NcManagerValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcManagerValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcManagerValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcManagerValue | None = None) -> NcManagerValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Base(self) -> NcObjectValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Base

    def set_Base(self, v: NcObjectValue) -> None:
        assert self._defined, "NcManager must be defined before setting Base"
        self._value.Base = v.clone()  # copy to match Go's value semantics


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcManagerValue()

    def clone(self) -> NcManager:
        o = NcManager()
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
            return f"NcManager(defined)"
        return "NcManager(<undefined>)"


def make_ncmanager_value(v: NcManagerValue) -> NcManagerValue:
    """Factory: create a NcManagerValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ncmanager(v: NcManagerValue) -> NcManager:
    """Factory: create a defined NcManager from a NcManagerValue."""
    o = NcManager()
    o.set_value(v)
    return o

