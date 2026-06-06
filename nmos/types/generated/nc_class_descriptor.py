"""Generated NMOS type: NcClassDescriptor. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NArrayOfInt, NString, NNullString
from nmos.types.generated.nc_descriptor import NcDescriptor, NcDescriptorValue
from nmos.types.generated.nc_array_of_property_descriptor import NcArrayOfPropertyDescriptor, NcArrayOfPropertyDescriptorValue
from nmos.types.generated.nc_array_of_method_descriptor import NcArrayOfMethodDescriptor, NcArrayOfMethodDescriptorValue
from nmos.types.generated.nc_array_of_event_descriptor import NcArrayOfEventDescriptor, NcArrayOfEventDescriptorValue

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcClassDescriptorEnums:
    """JSON property name enums for NcClassDescriptor."""
    ClassId = EnumRegistry.get("classId")
    Name = EnumRegistry.get("name")
    FixedRole = EnumRegistry.get("fixedRole")
    Properties = EnumRegistry.get("properties")
    Methods = EnumRegistry.get("methods")
    Events = EnumRegistry.get("events")
    pass


class NcClassDescriptorValue:
    """Inner value struct for NcClassDescriptor."""

    __slots__ = (
        "Base",
        "ClassId",
        "Name",
        "FixedRole",
        "Properties",
        "Methods",
        "Events",
    )

    def __init__(self) -> None:
        self.Base: NcDescriptorValue = NcDescriptorValue()
        self.ClassId: NArrayOfInt = NArrayOfInt()
        self.Name: NString = NString()
        self.FixedRole: NNullString = NNullString()
        self.Properties: NcArrayOfPropertyDescriptor = NcArrayOfPropertyDescriptor()
        self.Methods: NcArrayOfMethodDescriptor = NcArrayOfMethodDescriptor()
        self.Events: NcArrayOfEventDescriptor = NcArrayOfEventDescriptor()

    def set_to_default(self) -> None:
        self.Base = NcDescriptorValue()
        self.Base.set_to_default()
        self.ClassId.set_to_default()
        self.Name.set_to_default()
        self.FixedRole.set_to_default()
        self.Properties.set_to_default()
        self.Methods.set_to_default()
        self.Events.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.ClassId.defined:
            raise InvalidObject("missing required member ClassId")
        if not self.Name.defined:
            raise InvalidObject("missing required member Name")
        if not self.FixedRole.defined:
            raise InvalidObject("missing required member FixedRole")
        if not self.Properties.defined:
            raise InvalidObject("missing required member Properties")
        if not self.Methods.defined:
            raise InvalidObject("missing required member Methods")
        if not self.Events.defined:
            raise InvalidObject("missing required member Events")
        self.Base.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Base.encode(engine, None)
        self.ClassId.encode(engine, NcClassDescriptorEnums.ClassId)
        self.Name.encode(engine, NcClassDescriptorEnums.Name)
        self.FixedRole.encode(engine, NcClassDescriptorEnums.FixedRole)
        self.Properties.encode(engine, NcClassDescriptorEnums.Properties)
        self.Methods.encode(engine, NcClassDescriptorEnums.Methods)
        self.Events.encode(engine, NcClassDescriptorEnums.Events)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcClassDescriptor")

        self.Base.decode(engine, data)
        if NcClassDescriptorEnums.ClassId.s in data:
            self.ClassId.decode_value(data[NcClassDescriptorEnums.ClassId.s])
        if NcClassDescriptorEnums.Name.s in data:
            self.Name.decode_value(data[NcClassDescriptorEnums.Name.s])
        if NcClassDescriptorEnums.FixedRole.s in data:
            self.FixedRole.decode_value(data[NcClassDescriptorEnums.FixedRole.s])
        if NcClassDescriptorEnums.Properties.s in data:
            self.Properties.decode_value(data[NcClassDescriptorEnums.Properties.s])
        if NcClassDescriptorEnums.Methods.s in data:
            self.Methods.decode_value(data[NcClassDescriptorEnums.Methods.s])
        if NcClassDescriptorEnums.Events.s in data:
            self.Events.decode_value(data[NcClassDescriptorEnums.Events.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcClassDescriptorValue:
        o = NcClassDescriptorValue()
        o.Base = self.Base.clone()
        o.ClassId = self.ClassId.clone()
        o.Name = self.Name.clone()
        o.FixedRole = self.FixedRole.clone()
        o.Properties = self.Properties.clone()
        o.Methods = self.Methods.clone()
        o.Events = self.Events.clone()
        return o


class NcClassDescriptor:
    """Optional object type: NcClassDescriptor."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcClassDescriptorValue = NcClassDescriptorValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcClassDescriptorValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcClassDescriptorValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcClassDescriptorValue | None = None) -> NcClassDescriptorValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Base(self) -> NcDescriptorValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Base

    def set_Base(self, v: NcDescriptorValue) -> None:
        assert self._defined, "NcClassDescriptor must be defined before setting Base"
        self._value.Base = v.clone()  # copy to match Go's value semantics

    def get_ClassId(self) -> NArrayOfInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ClassId

    def set_ClassId(self, v: Any) -> None:
        assert self._defined, "NcClassDescriptor must be defined before setting ClassId"
        _assign_value(self._value.ClassId, v)

    def get_Name(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Name

    def set_Name(self, v: Any) -> None:
        assert self._defined, "NcClassDescriptor must be defined before setting Name"
        _assign_value(self._value.Name, v)

    def get_FixedRole(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FixedRole

    def set_FixedRole(self, v: Any) -> None:
        assert self._defined, "NcClassDescriptor must be defined before setting FixedRole"
        _assign_value(self._value.FixedRole, v)

    def get_Properties(self) -> NcArrayOfPropertyDescriptor:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Properties

    def set_Properties(self, v: Any) -> None:
        assert self._defined, "NcClassDescriptor must be defined before setting Properties"
        _assign_value(self._value.Properties, v)

    def get_Methods(self) -> NcArrayOfMethodDescriptor:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Methods

    def set_Methods(self, v: Any) -> None:
        assert self._defined, "NcClassDescriptor must be defined before setting Methods"
        _assign_value(self._value.Methods, v)

    def get_Events(self) -> NcArrayOfEventDescriptor:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Events

    def set_Events(self, v: Any) -> None:
        assert self._defined, "NcClassDescriptor must be defined before setting Events"
        _assign_value(self._value.Events, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcClassDescriptorValue()

    def clone(self) -> NcClassDescriptor:
        o = NcClassDescriptor()
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
            return f"NcClassDescriptor(defined)"
        return "NcClassDescriptor(<undefined>)"


def make_ncclassdescriptor_value(v: NcClassDescriptorValue) -> NcClassDescriptorValue:
    """Factory: create a NcClassDescriptorValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ncclassdescriptor(v: NcClassDescriptorValue) -> NcClassDescriptor:
    """Factory: create a defined NcClassDescriptor from a NcClassDescriptorValue."""
    o = NcClassDescriptor()
    o.set_value(v)
    return o

