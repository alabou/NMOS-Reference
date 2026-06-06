"""Generated NMOS type: NcEventDescriptor. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString, NBool
from nmos.types.generated.nc_descriptor import NcDescriptor, NcDescriptorValue
from nmos.types.generated.nc_property_id import NcPropertyId, NcPropertyIdValue

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcEventDescriptorEnums:
    """JSON property name enums for NcEventDescriptor."""
    Id = EnumRegistry.get("id")
    Name = EnumRegistry.get("name")
    EventDataType = EnumRegistry.get("eventDatatype")
    IsDeprecated = EnumRegistry.get("isDeprecated")
    pass


class NcEventDescriptorValue:
    """Inner value struct for NcEventDescriptor."""

    __slots__ = (
        "Base",
        "Id",
        "Name",
        "EventDataType",
        "IsDeprecated",
    )

    def __init__(self) -> None:
        self.Base: NcDescriptorValue = NcDescriptorValue()
        self.Id: NcPropertyId = NcPropertyId()
        self.Name: NString = NString()
        self.EventDataType: NString = NString()
        self.IsDeprecated: NBool = NBool()

    def set_to_default(self) -> None:
        self.Base = NcDescriptorValue()
        self.Base.set_to_default()
        self.Id.set_to_default()
        self.Name.set_to_default()
        self.EventDataType.set_to_default()
        self.IsDeprecated.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Id.defined:
            raise InvalidObject("missing required member Id")
        if not self.Name.defined:
            raise InvalidObject("missing required member Name")
        if not self.EventDataType.defined:
            raise InvalidObject("missing required member EventDataType")
        if not self.IsDeprecated.defined:
            raise InvalidObject("missing required member IsDeprecated")
        self.Base.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Base.encode(engine, None)
        self.Id.encode(engine, NcEventDescriptorEnums.Id)
        self.Name.encode(engine, NcEventDescriptorEnums.Name)
        self.EventDataType.encode(engine, NcEventDescriptorEnums.EventDataType)
        self.IsDeprecated.encode(engine, NcEventDescriptorEnums.IsDeprecated)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcEventDescriptor")

        self.Base.decode(engine, data)
        if NcEventDescriptorEnums.Id.s in data:
            self.Id.decode_value(data[NcEventDescriptorEnums.Id.s])
        if NcEventDescriptorEnums.Name.s in data:
            self.Name.decode_value(data[NcEventDescriptorEnums.Name.s])
        if NcEventDescriptorEnums.EventDataType.s in data:
            self.EventDataType.decode_value(data[NcEventDescriptorEnums.EventDataType.s])
        if NcEventDescriptorEnums.IsDeprecated.s in data:
            self.IsDeprecated.decode_value(data[NcEventDescriptorEnums.IsDeprecated.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcEventDescriptorValue:
        o = NcEventDescriptorValue()
        o.Base = self.Base.clone()
        o.Id = self.Id.clone()
        o.Name = self.Name.clone()
        o.EventDataType = self.EventDataType.clone()
        o.IsDeprecated = self.IsDeprecated.clone()
        return o


class NcEventDescriptor:
    """Optional object type: NcEventDescriptor."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcEventDescriptorValue = NcEventDescriptorValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcEventDescriptorValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcEventDescriptorValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcEventDescriptorValue | None = None) -> NcEventDescriptorValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Base(self) -> NcDescriptorValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Base

    def set_Base(self, v: NcDescriptorValue) -> None:
        assert self._defined, "NcEventDescriptor must be defined before setting Base"
        self._value.Base = v.clone()  # copy to match Go's value semantics

    def get_Id(self) -> NcPropertyId:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Id

    def set_Id(self, v: Any) -> None:
        assert self._defined, "NcEventDescriptor must be defined before setting Id"
        _assign_value(self._value.Id, v)

    def get_Name(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Name

    def set_Name(self, v: Any) -> None:
        assert self._defined, "NcEventDescriptor must be defined before setting Name"
        _assign_value(self._value.Name, v)

    def get_EventDataType(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.EventDataType

    def set_EventDataType(self, v: Any) -> None:
        assert self._defined, "NcEventDescriptor must be defined before setting EventDataType"
        _assign_value(self._value.EventDataType, v)

    def get_IsDeprecated(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.IsDeprecated

    def set_IsDeprecated(self, v: Any) -> None:
        assert self._defined, "NcEventDescriptor must be defined before setting IsDeprecated"
        _assign_value(self._value.IsDeprecated, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcEventDescriptorValue()

    def clone(self) -> NcEventDescriptor:
        o = NcEventDescriptor()
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
            return f"NcEventDescriptor(defined)"
        return "NcEventDescriptor(<undefined>)"


def make_nceventdescriptor_value(v: NcEventDescriptorValue) -> NcEventDescriptorValue:
    """Factory: create a NcEventDescriptorValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nceventdescriptor(v: NcEventDescriptorValue) -> NcEventDescriptor:
    """Factory: create a defined NcEventDescriptor from a NcEventDescriptorValue."""
    o = NcEventDescriptor()
    o.set_value(v)
    return o

