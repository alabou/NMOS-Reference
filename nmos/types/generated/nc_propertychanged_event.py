"""Generated NMOS type: NcPropertychangedEvent. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NInt, NGeneric, NNull
from nmos.types.generated.nc_property_id import NcPropertyId, NcPropertyIdValue
from nmos.validators import CheckPropertyChangeType, CheckNullPositiveInteger

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcPropertychangedEventEnums:
    """JSON property name enums for NcPropertychangedEvent."""
    PropertyId = EnumRegistry.get("propertyId")
    ChangeType = EnumRegistry.get("changeType")
    GenericValue = EnumRegistry.get("value")
    SequenceItemIndex = EnumRegistry.get("sequenceItemIndex")
    pass


class NcPropertychangedEventValue:
    """Inner value struct for NcPropertychangedEvent."""

    __slots__ = (
        "PropertyId",
        "ChangeType",
        "GenericValue",
        "SequenceItemIndex",
    )

    def __init__(self) -> None:
        self.PropertyId: NcPropertyId = NcPropertyId()
        self.ChangeType: NInt = NInt()
        self.GenericValue: NGeneric = NGeneric()
        self.SequenceItemIndex: NNull = NNull()

    def set_to_default(self) -> None:
        self.PropertyId.set_to_default()
        self.ChangeType.set_to_default()
        self.GenericValue.set_to_default()
        self.SequenceItemIndex.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.PropertyId.defined:
            raise InvalidObject("missing required member PropertyId")
        if not self.ChangeType.defined:
            raise InvalidObject("missing required member ChangeType")
        if not self.GenericValue.defined:
            raise InvalidObject("missing required member GenericValue")
        if not self.SequenceItemIndex.defined:
            raise InvalidObject("missing required member SequenceItemIndex")
        if self.ChangeType.defined:
            CheckPropertyChangeType(self.ChangeType)
        if self.SequenceItemIndex.defined:
            CheckNullPositiveInteger(self.SequenceItemIndex)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.PropertyId.encode(engine, NcPropertychangedEventEnums.PropertyId)
        self.ChangeType.encode(engine, NcPropertychangedEventEnums.ChangeType)
        self.GenericValue.encode(engine, NcPropertychangedEventEnums.GenericValue)
        self.SequenceItemIndex.encode(engine, NcPropertychangedEventEnums.SequenceItemIndex)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcPropertychangedEvent")

        if NcPropertychangedEventEnums.PropertyId.s in data:
            self.PropertyId.decode_value(data[NcPropertychangedEventEnums.PropertyId.s])
        if NcPropertychangedEventEnums.ChangeType.s in data:
            self.ChangeType.decode_value(data[NcPropertychangedEventEnums.ChangeType.s])
        if NcPropertychangedEventEnums.GenericValue.s in data:
            self.GenericValue.decode_value(data[NcPropertychangedEventEnums.GenericValue.s])
        if NcPropertychangedEventEnums.SequenceItemIndex.s in data:
            self.SequenceItemIndex.decode_value(data[NcPropertychangedEventEnums.SequenceItemIndex.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcPropertychangedEventValue:
        o = NcPropertychangedEventValue()
        o.PropertyId = self.PropertyId.clone()
        o.ChangeType = self.ChangeType.clone()
        o.GenericValue = self.GenericValue.clone()
        o.SequenceItemIndex = self.SequenceItemIndex.clone()
        return o


class NcPropertychangedEvent:
    """Optional object type: NcPropertychangedEvent."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcPropertychangedEventValue = NcPropertychangedEventValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcPropertychangedEventValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcPropertychangedEventValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcPropertychangedEventValue | None = None) -> NcPropertychangedEventValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_PropertyId(self) -> NcPropertyId:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.PropertyId

    def set_PropertyId(self, v: Any) -> None:
        assert self._defined, "NcPropertychangedEvent must be defined before setting PropertyId"
        _assign_value(self._value.PropertyId, v)

    def get_ChangeType(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ChangeType

    def set_ChangeType(self, v: Any) -> None:
        assert self._defined, "NcPropertychangedEvent must be defined before setting ChangeType"
        _assign_value(self._value.ChangeType, v)

    def get_GenericValue(self) -> NGeneric:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.GenericValue

    def set_GenericValue(self, v: Any) -> None:
        assert self._defined, "NcPropertychangedEvent must be defined before setting GenericValue"
        _assign_value(self._value.GenericValue, v)

    def get_SequenceItemIndex(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SequenceItemIndex

    def set_SequenceItemIndex(self, v: Any) -> None:
        assert self._defined, "NcPropertychangedEvent must be defined before setting SequenceItemIndex"
        _assign_value(self._value.SequenceItemIndex, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcPropertychangedEventValue()

    def clone(self) -> NcPropertychangedEvent:
        o = NcPropertychangedEvent()
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
            return f"NcPropertychangedEvent(defined)"
        return "NcPropertychangedEvent(<undefined>)"


def make_ncpropertychangedevent_value(v: NcPropertychangedEventValue) -> NcPropertychangedEventValue:
    """Factory: create a NcPropertychangedEventValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ncpropertychangedevent(v: NcPropertychangedEventValue) -> NcPropertychangedEvent:
    """Factory: create a defined NcPropertychangedEvent from a NcPropertychangedEventValue."""
    o = NcPropertychangedEvent()
    o.set_value(v)
    return o

