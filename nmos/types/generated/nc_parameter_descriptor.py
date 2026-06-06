"""Generated NMOS type: NcParameterDescriptor. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString, NNullString, NBool, NGeneric
from nmos.types.generated.nc_descriptor import NcDescriptor, NcDescriptorValue

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcParameterDescriptorEnums:
    """JSON property name enums for NcParameterDescriptor."""
    Name = EnumRegistry.get("name")
    TypeName = EnumRegistry.get("typeName")
    IsNullable = EnumRegistry.get("isNullable")
    IsSequence = EnumRegistry.get("isSequence")
    Constraints = EnumRegistry.get("constraints")
    pass


class NcParameterDescriptorValue:
    """Inner value struct for NcParameterDescriptor."""

    __slots__ = (
        "Base",
        "Name",
        "TypeName",
        "IsNullable",
        "IsSequence",
        "Constraints",
    )

    def __init__(self) -> None:
        self.Base: NcDescriptorValue = NcDescriptorValue()
        self.Name: NString = NString()
        self.TypeName: NNullString = NNullString()
        self.IsNullable: NBool = NBool()
        self.IsSequence: NBool = NBool()
        self.Constraints: NGeneric = NGeneric()

    def set_to_default(self) -> None:
        self.Base = NcDescriptorValue()
        self.Base.set_to_default()
        self.Name.set_to_default()
        self.TypeName.set_to_default()
        self.IsNullable.set_to_default()
        self.IsSequence.set_to_default()
        self.Constraints.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Name.defined:
            raise InvalidObject("missing required member Name")
        if not self.TypeName.defined:
            raise InvalidObject("missing required member TypeName")
        if not self.IsNullable.defined:
            raise InvalidObject("missing required member IsNullable")
        if not self.IsSequence.defined:
            raise InvalidObject("missing required member IsSequence")
        if not self.Constraints.defined:
            raise InvalidObject("missing required member Constraints")
        self.Base.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Base.encode(engine, None)
        self.Name.encode(engine, NcParameterDescriptorEnums.Name)
        self.TypeName.encode(engine, NcParameterDescriptorEnums.TypeName)
        self.IsNullable.encode(engine, NcParameterDescriptorEnums.IsNullable)
        self.IsSequence.encode(engine, NcParameterDescriptorEnums.IsSequence)
        self.Constraints.encode(engine, NcParameterDescriptorEnums.Constraints)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcParameterDescriptor")

        self.Base.decode(engine, data)
        if NcParameterDescriptorEnums.Name.s in data:
            self.Name.decode_value(data[NcParameterDescriptorEnums.Name.s])
        if NcParameterDescriptorEnums.TypeName.s in data:
            self.TypeName.decode_value(data[NcParameterDescriptorEnums.TypeName.s])
        if NcParameterDescriptorEnums.IsNullable.s in data:
            self.IsNullable.decode_value(data[NcParameterDescriptorEnums.IsNullable.s])
        if NcParameterDescriptorEnums.IsSequence.s in data:
            self.IsSequence.decode_value(data[NcParameterDescriptorEnums.IsSequence.s])
        if NcParameterDescriptorEnums.Constraints.s in data:
            self.Constraints.decode_value(data[NcParameterDescriptorEnums.Constraints.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcParameterDescriptorValue:
        o = NcParameterDescriptorValue()
        o.Base = self.Base.clone()
        o.Name = self.Name.clone()
        o.TypeName = self.TypeName.clone()
        o.IsNullable = self.IsNullable.clone()
        o.IsSequence = self.IsSequence.clone()
        o.Constraints = self.Constraints.clone()
        return o


class NcParameterDescriptor:
    """Optional object type: NcParameterDescriptor."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcParameterDescriptorValue = NcParameterDescriptorValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcParameterDescriptorValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcParameterDescriptorValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcParameterDescriptorValue | None = None) -> NcParameterDescriptorValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Base(self) -> NcDescriptorValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Base

    def set_Base(self, v: NcDescriptorValue) -> None:
        assert self._defined, "NcParameterDescriptor must be defined before setting Base"
        self._value.Base = v.clone()  # copy to match Go's value semantics

    def get_Name(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Name

    def set_Name(self, v: Any) -> None:
        assert self._defined, "NcParameterDescriptor must be defined before setting Name"
        _assign_value(self._value.Name, v)

    def get_TypeName(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.TypeName

    def set_TypeName(self, v: Any) -> None:
        assert self._defined, "NcParameterDescriptor must be defined before setting TypeName"
        _assign_value(self._value.TypeName, v)

    def get_IsNullable(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.IsNullable

    def set_IsNullable(self, v: Any) -> None:
        assert self._defined, "NcParameterDescriptor must be defined before setting IsNullable"
        _assign_value(self._value.IsNullable, v)

    def get_IsSequence(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.IsSequence

    def set_IsSequence(self, v: Any) -> None:
        assert self._defined, "NcParameterDescriptor must be defined before setting IsSequence"
        _assign_value(self._value.IsSequence, v)

    def get_Constraints(self) -> NGeneric:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Constraints

    def set_Constraints(self, v: Any) -> None:
        assert self._defined, "NcParameterDescriptor must be defined before setting Constraints"
        _assign_value(self._value.Constraints, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcParameterDescriptorValue()

    def clone(self) -> NcParameterDescriptor:
        o = NcParameterDescriptor()
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
            return f"NcParameterDescriptor(defined)"
        return "NcParameterDescriptor(<undefined>)"


def make_ncparameterdescriptor_value(v: NcParameterDescriptorValue) -> NcParameterDescriptorValue:
    """Factory: create a NcParameterDescriptorValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ncparameterdescriptor(v: NcParameterDescriptorValue) -> NcParameterDescriptor:
    """Factory: create a defined NcParameterDescriptor from a NcParameterDescriptorValue."""
    o = NcParameterDescriptor()
    o.set_value(v)
    return o

