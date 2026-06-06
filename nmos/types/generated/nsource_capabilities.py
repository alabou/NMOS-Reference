"""Generated NMOS type: NSourceCapabilities. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NTime
from nmos.types.generated.narray_of_constraint_set import NArrayOfConstraintSet, NArrayOfConstraintSetValue

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NSourceCapabilitiesEnums:
    """JSON property name enums for NSourceCapabilities."""
    Version = EnumRegistry.get("version")
    ConstraintSets = EnumRegistry.get("constraint_sets")
    pass


class NSourceCapabilitiesValue:
    """Inner value struct for NSourceCapabilities."""

    __slots__ = (
        "Version",
        "ConstraintSets",
    )

    def __init__(self) -> None:
        self.Version: NTime = NTime()
        self.ConstraintSets: NArrayOfConstraintSet = NArrayOfConstraintSet()

    def set_to_default(self) -> None:
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Version.encode(engine, NSourceCapabilitiesEnums.Version)
        self.ConstraintSets.encode(engine, NSourceCapabilitiesEnums.ConstraintSets)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NSourceCapabilities")

        if NSourceCapabilitiesEnums.Version.s in data:
            self.Version.decode_value(data[NSourceCapabilitiesEnums.Version.s])
        if NSourceCapabilitiesEnums.ConstraintSets.s in data:
            self.ConstraintSets.decode_value(data[NSourceCapabilitiesEnums.ConstraintSets.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NSourceCapabilitiesValue:
        o = NSourceCapabilitiesValue()
        o.Version = self.Version.clone()
        o.ConstraintSets = self.ConstraintSets.clone()
        return o


class NSourceCapabilities:
    """Optional object type: NSourceCapabilities."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NSourceCapabilitiesValue = NSourceCapabilitiesValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NSourceCapabilitiesValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NSourceCapabilitiesValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NSourceCapabilitiesValue | None = None) -> NSourceCapabilitiesValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Version(self) -> NTime:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Version

    def set_Version(self, v: Any) -> None:
        assert self._defined, "NSourceCapabilities must be defined before setting Version"
        _assign_value(self._value.Version, v)

    def get_ConstraintSets(self) -> NArrayOfConstraintSet:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ConstraintSets

    def set_ConstraintSets(self, v: Any) -> None:
        assert self._defined, "NSourceCapabilities must be defined before setting ConstraintSets"
        _assign_value(self._value.ConstraintSets, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NSourceCapabilitiesValue()

    def clone(self) -> NSourceCapabilities:
        o = NSourceCapabilities()
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
            return f"NSourceCapabilities(defined)"
        return "NSourceCapabilities(<undefined>)"


def make_nsourcecapabilities_value(v: NSourceCapabilitiesValue) -> NSourceCapabilitiesValue:
    """Factory: create a NSourceCapabilitiesValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nsourcecapabilities(v: NSourceCapabilitiesValue) -> NSourceCapabilities:
    """Factory: create a defined NSourceCapabilities from a NSourceCapabilitiesValue."""
    o = NSourceCapabilities()
    o.set_value(v)
    return o

