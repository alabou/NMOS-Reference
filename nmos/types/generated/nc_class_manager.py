"""Generated NMOS type: NcClassManager. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NArrayOfGeneric
from nmos.types.generated.nc_manager import NcManager, NcManagerValue

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcClassManagerEnums:
    """JSON property name enums for NcClassManager."""
    ControlClasses = EnumRegistry.get("controlClasses")
    DataTypes = EnumRegistry.get("datatypes")
    pass


class NcClassManagerValue:
    """Inner value struct for NcClassManager."""

    __slots__ = (
        "Base",
        "ControlClasses",
        "DataTypes",
    )

    def __init__(self) -> None:
        self.Base: NcManagerValue = NcManagerValue()
        self.ControlClasses: NArrayOfGeneric = NArrayOfGeneric()
        self.DataTypes: NArrayOfGeneric = NArrayOfGeneric()

    def set_to_default(self) -> None:
        self.Base = NcManagerValue()
        self.Base.set_to_default()
        self.ControlClasses.set_to_default()
        self.DataTypes.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.ControlClasses.defined:
            raise InvalidObject("missing required member ControlClasses")
        if not self.DataTypes.defined:
            raise InvalidObject("missing required member DataTypes")
        self.Base.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Base.encode(engine, None)
        self.ControlClasses.encode(engine, NcClassManagerEnums.ControlClasses)
        self.DataTypes.encode(engine, NcClassManagerEnums.DataTypes)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcClassManager")

        self.Base.decode(engine, data)
        if NcClassManagerEnums.ControlClasses.s in data:
            self.ControlClasses.decode_value(data[NcClassManagerEnums.ControlClasses.s])
        if NcClassManagerEnums.DataTypes.s in data:
            self.DataTypes.decode_value(data[NcClassManagerEnums.DataTypes.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcClassManagerValue:
        o = NcClassManagerValue()
        o.Base = self.Base.clone()
        o.ControlClasses = self.ControlClasses.clone()
        o.DataTypes = self.DataTypes.clone()
        return o


class NcClassManager:
    """Optional object type: NcClassManager."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcClassManagerValue = NcClassManagerValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcClassManagerValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcClassManagerValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcClassManagerValue | None = None) -> NcClassManagerValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Base(self) -> NcManagerValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Base

    def set_Base(self, v: NcManagerValue) -> None:
        assert self._defined, "NcClassManager must be defined before setting Base"
        self._value.Base = v.clone()  # copy to match Go's value semantics

    def get_ControlClasses(self) -> NArrayOfGeneric:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ControlClasses

    def set_ControlClasses(self, v: Any) -> None:
        assert self._defined, "NcClassManager must be defined before setting ControlClasses"
        _assign_value(self._value.ControlClasses, v)

    def get_DataTypes(self) -> NArrayOfGeneric:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.DataTypes

    def set_DataTypes(self, v: Any) -> None:
        assert self._defined, "NcClassManager must be defined before setting DataTypes"
        _assign_value(self._value.DataTypes, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcClassManagerValue()

    def clone(self) -> NcClassManager:
        o = NcClassManager()
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
            return f"NcClassManager(defined)"
        return "NcClassManager(<undefined>)"


def make_ncclassmanager_value(v: NcClassManagerValue) -> NcClassManagerValue:
    """Factory: create a NcClassManagerValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ncclassmanager(v: NcClassManagerValue) -> NcClassManager:
    """Factory: create a defined NcClassManager from a NcClassManagerValue."""
    o = NcClassManager()
    o.set_value(v)
    return o

