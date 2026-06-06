"""Generated NMOS type: NcPropertyDescriptor. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString, NNullString, NBool, NGeneric
from nmos.types.generated.nc_descriptor import NcDescriptor, NcDescriptorValue
from nmos.types.generated.nc_property_id import NcPropertyId, NcPropertyIdValue

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcPropertyDescriptorEnums:
    """JSON property name enums for NcPropertyDescriptor."""
    Id = EnumRegistry.get("id")
    Name = EnumRegistry.get("name")
    TypeName = EnumRegistry.get("typeName")
    IsReadOnly = EnumRegistry.get("isReadOnly")
    IsNullable = EnumRegistry.get("isNullable")
    IsSequence = EnumRegistry.get("isSequence")
    IsDeprecated = EnumRegistry.get("isDeprecated")
    Constraints = EnumRegistry.get("constraints")
    pass


class NcPropertyDescriptorValue:
    """Inner value struct for NcPropertyDescriptor."""

    __slots__ = (
        "Base",
        "Id",
        "Name",
        "TypeName",
        "IsReadOnly",
        "IsNullable",
        "IsSequence",
        "IsDeprecated",
        "Constraints",
    )

    def __init__(self) -> None:
        self.Base: NcDescriptorValue = NcDescriptorValue()
        self.Id: NcPropertyId = NcPropertyId()
        self.Name: NString = NString()
        self.TypeName: NNullString = NNullString()
        self.IsReadOnly: NBool = NBool()
        self.IsNullable: NBool = NBool()
        self.IsSequence: NBool = NBool()
        self.IsDeprecated: NBool = NBool()
        self.Constraints: NGeneric = NGeneric()

    def set_to_default(self) -> None:
        self.Base = NcDescriptorValue()
        self.Base.set_to_default()
        self.Id.set_to_default()
        self.Name.set_to_default()
        self.TypeName.set_to_default()
        self.IsReadOnly.set_to_default()
        self.IsNullable.set_to_default()
        self.IsSequence.set_to_default()
        self.IsDeprecated.set_to_default()
        self.Constraints.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Id.defined:
            raise InvalidObject("missing required member Id")
        if not self.Name.defined:
            raise InvalidObject("missing required member Name")
        if not self.TypeName.defined:
            raise InvalidObject("missing required member TypeName")
        if not self.IsReadOnly.defined:
            raise InvalidObject("missing required member IsReadOnly")
        if not self.IsNullable.defined:
            raise InvalidObject("missing required member IsNullable")
        if not self.IsSequence.defined:
            raise InvalidObject("missing required member IsSequence")
        if not self.IsDeprecated.defined:
            raise InvalidObject("missing required member IsDeprecated")
        if not self.Constraints.defined:
            raise InvalidObject("missing required member Constraints")
        self.Base.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Base.encode(engine, None)
        self.Id.encode(engine, NcPropertyDescriptorEnums.Id)
        self.Name.encode(engine, NcPropertyDescriptorEnums.Name)
        self.TypeName.encode(engine, NcPropertyDescriptorEnums.TypeName)
        self.IsReadOnly.encode(engine, NcPropertyDescriptorEnums.IsReadOnly)
        self.IsNullable.encode(engine, NcPropertyDescriptorEnums.IsNullable)
        self.IsSequence.encode(engine, NcPropertyDescriptorEnums.IsSequence)
        self.IsDeprecated.encode(engine, NcPropertyDescriptorEnums.IsDeprecated)
        self.Constraints.encode(engine, NcPropertyDescriptorEnums.Constraints)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcPropertyDescriptor")

        self.Base.decode(engine, data)
        if NcPropertyDescriptorEnums.Id.s in data:
            self.Id.decode_value(data[NcPropertyDescriptorEnums.Id.s])
        if NcPropertyDescriptorEnums.Name.s in data:
            self.Name.decode_value(data[NcPropertyDescriptorEnums.Name.s])
        if NcPropertyDescriptorEnums.TypeName.s in data:
            self.TypeName.decode_value(data[NcPropertyDescriptorEnums.TypeName.s])
        if NcPropertyDescriptorEnums.IsReadOnly.s in data:
            self.IsReadOnly.decode_value(data[NcPropertyDescriptorEnums.IsReadOnly.s])
        if NcPropertyDescriptorEnums.IsNullable.s in data:
            self.IsNullable.decode_value(data[NcPropertyDescriptorEnums.IsNullable.s])
        if NcPropertyDescriptorEnums.IsSequence.s in data:
            self.IsSequence.decode_value(data[NcPropertyDescriptorEnums.IsSequence.s])
        if NcPropertyDescriptorEnums.IsDeprecated.s in data:
            self.IsDeprecated.decode_value(data[NcPropertyDescriptorEnums.IsDeprecated.s])
        if NcPropertyDescriptorEnums.Constraints.s in data:
            self.Constraints.decode_value(data[NcPropertyDescriptorEnums.Constraints.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcPropertyDescriptorValue:
        o = NcPropertyDescriptorValue()
        o.Base = self.Base.clone()
        o.Id = self.Id.clone()
        o.Name = self.Name.clone()
        o.TypeName = self.TypeName.clone()
        o.IsReadOnly = self.IsReadOnly.clone()
        o.IsNullable = self.IsNullable.clone()
        o.IsSequence = self.IsSequence.clone()
        o.IsDeprecated = self.IsDeprecated.clone()
        o.Constraints = self.Constraints.clone()
        return o


class NcPropertyDescriptor:
    """Optional object type: NcPropertyDescriptor."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcPropertyDescriptorValue = NcPropertyDescriptorValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcPropertyDescriptorValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcPropertyDescriptorValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcPropertyDescriptorValue | None = None) -> NcPropertyDescriptorValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Base(self) -> NcDescriptorValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Base

    def set_Base(self, v: NcDescriptorValue) -> None:
        assert self._defined, "NcPropertyDescriptor must be defined before setting Base"
        self._value.Base = v.clone()  # copy to match Go's value semantics

    def get_Id(self) -> NcPropertyId:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Id

    def set_Id(self, v: Any) -> None:
        assert self._defined, "NcPropertyDescriptor must be defined before setting Id"
        _assign_value(self._value.Id, v)

    def get_Name(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Name

    def set_Name(self, v: Any) -> None:
        assert self._defined, "NcPropertyDescriptor must be defined before setting Name"
        _assign_value(self._value.Name, v)

    def get_TypeName(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.TypeName

    def set_TypeName(self, v: Any) -> None:
        assert self._defined, "NcPropertyDescriptor must be defined before setting TypeName"
        _assign_value(self._value.TypeName, v)

    def get_IsReadOnly(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.IsReadOnly

    def set_IsReadOnly(self, v: Any) -> None:
        assert self._defined, "NcPropertyDescriptor must be defined before setting IsReadOnly"
        _assign_value(self._value.IsReadOnly, v)

    def get_IsNullable(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.IsNullable

    def set_IsNullable(self, v: Any) -> None:
        assert self._defined, "NcPropertyDescriptor must be defined before setting IsNullable"
        _assign_value(self._value.IsNullable, v)

    def get_IsSequence(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.IsSequence

    def set_IsSequence(self, v: Any) -> None:
        assert self._defined, "NcPropertyDescriptor must be defined before setting IsSequence"
        _assign_value(self._value.IsSequence, v)

    def get_IsDeprecated(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.IsDeprecated

    def set_IsDeprecated(self, v: Any) -> None:
        assert self._defined, "NcPropertyDescriptor must be defined before setting IsDeprecated"
        _assign_value(self._value.IsDeprecated, v)

    def get_Constraints(self) -> NGeneric:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Constraints

    def set_Constraints(self, v: Any) -> None:
        assert self._defined, "NcPropertyDescriptor must be defined before setting Constraints"
        _assign_value(self._value.Constraints, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcPropertyDescriptorValue()

    def clone(self) -> NcPropertyDescriptor:
        o = NcPropertyDescriptor()
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
            return f"NcPropertyDescriptor(defined)"
        return "NcPropertyDescriptor(<undefined>)"


def make_ncpropertydescriptor_value(v: NcPropertyDescriptorValue) -> NcPropertyDescriptorValue:
    """Factory: create a NcPropertyDescriptorValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ncpropertydescriptor(v: NcPropertyDescriptorValue) -> NcPropertyDescriptor:
    """Factory: create a defined NcPropertyDescriptor from a NcPropertyDescriptorValue."""
    o = NcPropertyDescriptor()
    o.set_value(v)
    return o

