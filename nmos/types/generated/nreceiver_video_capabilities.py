"""Generated NMOS type: NReceiverVideoCapabilities. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NArrayOfEnum, NTime
from nmos.types.generated.narray_of_constraint_set import NArrayOfConstraintSet, NArrayOfConstraintSetValue
from nmos.validators import CheckVideoMediaTypes

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NReceiverVideoCapabilitiesEnums:
    """JSON property name enums for NReceiverVideoCapabilities."""
    MediaTypes = EnumRegistry.get("media_types")
    Version = EnumRegistry.get("version")
    ConstraintSets = EnumRegistry.get("constraint_sets")
    pass


class NReceiverVideoCapabilitiesValue:
    """Inner value struct for NReceiverVideoCapabilities."""

    __slots__ = (
        "MediaTypes",
        "Version",
        "ConstraintSets",
    )

    def __init__(self) -> None:
        self.MediaTypes: NArrayOfEnum = NArrayOfEnum()
        self.Version: NTime = NTime()
        self.ConstraintSets: NArrayOfConstraintSet = NArrayOfConstraintSet()

    def set_to_default(self) -> None:
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if self.MediaTypes.defined:
            CheckVideoMediaTypes(self.MediaTypes)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.MediaTypes.encode(engine, NReceiverVideoCapabilitiesEnums.MediaTypes)
        self.Version.encode(engine, NReceiverVideoCapabilitiesEnums.Version)
        self.ConstraintSets.encode(engine, NReceiverVideoCapabilitiesEnums.ConstraintSets)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NReceiverVideoCapabilities")

        if NReceiverVideoCapabilitiesEnums.MediaTypes.s in data:
            self.MediaTypes.decode_value(data[NReceiverVideoCapabilitiesEnums.MediaTypes.s])
        if NReceiverVideoCapabilitiesEnums.Version.s in data:
            self.Version.decode_value(data[NReceiverVideoCapabilitiesEnums.Version.s])
        if NReceiverVideoCapabilitiesEnums.ConstraintSets.s in data:
            self.ConstraintSets.decode_value(data[NReceiverVideoCapabilitiesEnums.ConstraintSets.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NReceiverVideoCapabilitiesValue:
        o = NReceiverVideoCapabilitiesValue()
        o.MediaTypes = self.MediaTypes.clone()
        o.Version = self.Version.clone()
        o.ConstraintSets = self.ConstraintSets.clone()
        return o


class NReceiverVideoCapabilities:
    """Optional object type: NReceiverVideoCapabilities."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NReceiverVideoCapabilitiesValue = NReceiverVideoCapabilitiesValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NReceiverVideoCapabilitiesValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NReceiverVideoCapabilitiesValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NReceiverVideoCapabilitiesValue | None = None) -> NReceiverVideoCapabilitiesValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_MediaTypes(self) -> NArrayOfEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MediaTypes

    def set_MediaTypes(self, v: Any) -> None:
        assert self._defined, "NReceiverVideoCapabilities must be defined before setting MediaTypes"
        _assign_value(self._value.MediaTypes, v)

    def get_Version(self) -> NTime:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Version

    def set_Version(self, v: Any) -> None:
        assert self._defined, "NReceiverVideoCapabilities must be defined before setting Version"
        _assign_value(self._value.Version, v)

    def get_ConstraintSets(self) -> NArrayOfConstraintSet:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ConstraintSets

    def set_ConstraintSets(self, v: Any) -> None:
        assert self._defined, "NReceiverVideoCapabilities must be defined before setting ConstraintSets"
        _assign_value(self._value.ConstraintSets, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NReceiverVideoCapabilitiesValue()

    def clone(self) -> NReceiverVideoCapabilities:
        o = NReceiverVideoCapabilities()
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
            return f"NReceiverVideoCapabilities(defined)"
        return "NReceiverVideoCapabilities(<undefined>)"


def make_nreceivervideocapabilities_value(v: NReceiverVideoCapabilitiesValue) -> NReceiverVideoCapabilitiesValue:
    """Factory: create a NReceiverVideoCapabilitiesValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nreceivervideocapabilities(v: NReceiverVideoCapabilitiesValue) -> NReceiverVideoCapabilities:
    """Factory: create a defined NReceiverVideoCapabilities from a NReceiverVideoCapabilitiesValue."""
    o = NReceiverVideoCapabilities()
    o.set_value(v)
    return o

