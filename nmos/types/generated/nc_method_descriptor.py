"""Generated NMOS type: NcMethodDescriptor. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString, NBool
from nmos.types.generated.nc_descriptor import NcDescriptor, NcDescriptorValue
from nmos.types.generated.nc_method_id import NcMethodId, NcMethodIdValue
from nmos.types.generated.nc_array_of_parameter_descriptor import NcArrayOfParameterDescriptor, NcArrayOfParameterDescriptorValue

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcMethodDescriptorEnums:
    """JSON property name enums for NcMethodDescriptor."""
    Id = EnumRegistry.get("id")
    Name = EnumRegistry.get("name")
    ResultDataType = EnumRegistry.get("resultDatatype")
    IsDeprecated = EnumRegistry.get("isDeprecated")
    Parameters = EnumRegistry.get("parameters")
    pass


class NcMethodDescriptorValue:
    """Inner value struct for NcMethodDescriptor."""

    __slots__ = (
        "Base",
        "Id",
        "Name",
        "ResultDataType",
        "IsDeprecated",
        "Parameters",
    )

    def __init__(self) -> None:
        self.Base: NcDescriptorValue = NcDescriptorValue()
        self.Id: NcMethodId = NcMethodId()
        self.Name: NString = NString()
        self.ResultDataType: NString = NString()
        self.IsDeprecated: NBool = NBool()
        self.Parameters: NcArrayOfParameterDescriptor = NcArrayOfParameterDescriptor()

    def set_to_default(self) -> None:
        self.Base = NcDescriptorValue()
        self.Base.set_to_default()
        self.Id.set_to_default()
        self.Name.set_to_default()
        self.ResultDataType.set_to_default()
        self.IsDeprecated.set_to_default()
        self.Parameters.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Id.defined:
            raise InvalidObject("missing required member Id")
        if not self.Name.defined:
            raise InvalidObject("missing required member Name")
        if not self.ResultDataType.defined:
            raise InvalidObject("missing required member ResultDataType")
        if not self.IsDeprecated.defined:
            raise InvalidObject("missing required member IsDeprecated")
        if not self.Parameters.defined:
            raise InvalidObject("missing required member Parameters")
        self.Base.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Base.encode(engine, None)
        self.Id.encode(engine, NcMethodDescriptorEnums.Id)
        self.Name.encode(engine, NcMethodDescriptorEnums.Name)
        self.ResultDataType.encode(engine, NcMethodDescriptorEnums.ResultDataType)
        self.IsDeprecated.encode(engine, NcMethodDescriptorEnums.IsDeprecated)
        self.Parameters.encode(engine, NcMethodDescriptorEnums.Parameters)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcMethodDescriptor")

        self.Base.decode(engine, data)
        if NcMethodDescriptorEnums.Id.s in data:
            self.Id.decode_value(data[NcMethodDescriptorEnums.Id.s])
        if NcMethodDescriptorEnums.Name.s in data:
            self.Name.decode_value(data[NcMethodDescriptorEnums.Name.s])
        if NcMethodDescriptorEnums.ResultDataType.s in data:
            self.ResultDataType.decode_value(data[NcMethodDescriptorEnums.ResultDataType.s])
        if NcMethodDescriptorEnums.IsDeprecated.s in data:
            self.IsDeprecated.decode_value(data[NcMethodDescriptorEnums.IsDeprecated.s])
        if NcMethodDescriptorEnums.Parameters.s in data:
            self.Parameters.decode_value(data[NcMethodDescriptorEnums.Parameters.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcMethodDescriptorValue:
        o = NcMethodDescriptorValue()
        o.Base = self.Base.clone()
        o.Id = self.Id.clone()
        o.Name = self.Name.clone()
        o.ResultDataType = self.ResultDataType.clone()
        o.IsDeprecated = self.IsDeprecated.clone()
        o.Parameters = self.Parameters.clone()
        return o


class NcMethodDescriptor:
    """Optional object type: NcMethodDescriptor."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcMethodDescriptorValue = NcMethodDescriptorValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcMethodDescriptorValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcMethodDescriptorValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcMethodDescriptorValue | None = None) -> NcMethodDescriptorValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Base(self) -> NcDescriptorValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Base

    def set_Base(self, v: NcDescriptorValue) -> None:
        assert self._defined, "NcMethodDescriptor must be defined before setting Base"
        self._value.Base = v.clone()  # copy to match Go's value semantics

    def get_Id(self) -> NcMethodId:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Id

    def set_Id(self, v: Any) -> None:
        assert self._defined, "NcMethodDescriptor must be defined before setting Id"
        _assign_value(self._value.Id, v)

    def get_Name(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Name

    def set_Name(self, v: Any) -> None:
        assert self._defined, "NcMethodDescriptor must be defined before setting Name"
        _assign_value(self._value.Name, v)

    def get_ResultDataType(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ResultDataType

    def set_ResultDataType(self, v: Any) -> None:
        assert self._defined, "NcMethodDescriptor must be defined before setting ResultDataType"
        _assign_value(self._value.ResultDataType, v)

    def get_IsDeprecated(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.IsDeprecated

    def set_IsDeprecated(self, v: Any) -> None:
        assert self._defined, "NcMethodDescriptor must be defined before setting IsDeprecated"
        _assign_value(self._value.IsDeprecated, v)

    def get_Parameters(self) -> NcArrayOfParameterDescriptor:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Parameters

    def set_Parameters(self, v: Any) -> None:
        assert self._defined, "NcMethodDescriptor must be defined before setting Parameters"
        _assign_value(self._value.Parameters, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcMethodDescriptorValue()

    def clone(self) -> NcMethodDescriptor:
        o = NcMethodDescriptor()
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
            return f"NcMethodDescriptor(defined)"
        return "NcMethodDescriptor(<undefined>)"


def make_ncmethoddescriptor_value(v: NcMethodDescriptorValue) -> NcMethodDescriptorValue:
    """Factory: create a NcMethodDescriptorValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ncmethoddescriptor(v: NcMethodDescriptorValue) -> NcMethodDescriptor:
    """Factory: create a defined NcMethodDescriptor from a NcMethodDescriptorValue."""
    o = NcMethodDescriptor()
    o.set_value(v)
    return o

