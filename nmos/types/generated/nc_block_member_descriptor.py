"""Generated NMOS type: NcBlockMemberDescriptor. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString, NInt, NBool, NArrayOfInt, NNullString
from nmos.types.generated.nc_descriptor import NcDescriptor, NcDescriptorValue

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcBlockMemberDescriptorEnums:
    """JSON property name enums for NcBlockMemberDescriptor."""
    Role = EnumRegistry.get("role")
    OId = EnumRegistry.get("oid")
    ConstantOId = EnumRegistry.get("constantOid")
    ClassId = EnumRegistry.get("classId")
    UserLabel = EnumRegistry.get("userLabel")
    Owner = EnumRegistry.get("owner")
    pass


class NcBlockMemberDescriptorValue:
    """Inner value struct for NcBlockMemberDescriptor."""

    __slots__ = (
        "Base",
        "Role",
        "OId",
        "ConstantOId",
        "ClassId",
        "UserLabel",
        "Owner",
    )

    def __init__(self) -> None:
        self.Base: NcDescriptorValue = NcDescriptorValue()
        self.Role: NString = NString()
        self.OId: NInt = NInt()
        self.ConstantOId: NBool = NBool()
        self.ClassId: NArrayOfInt = NArrayOfInt()
        self.UserLabel: NNullString = NNullString()
        self.Owner: NInt = NInt()

    def set_to_default(self) -> None:
        self.Base = NcDescriptorValue()
        self.Base.set_to_default()
        self.Role.set_to_default()
        self.OId.set_to_default()
        self.ConstantOId.set_to_default()
        self.ClassId.set_to_default()
        self.UserLabel.set_to_default()
        self.Owner.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Role.defined:
            raise InvalidObject("missing required member Role")
        if not self.OId.defined:
            raise InvalidObject("missing required member OId")
        if not self.ConstantOId.defined:
            raise InvalidObject("missing required member ConstantOId")
        if not self.ClassId.defined:
            raise InvalidObject("missing required member ClassId")
        if not self.UserLabel.defined:
            raise InvalidObject("missing required member UserLabel")
        if not self.Owner.defined:
            raise InvalidObject("missing required member Owner")
        self.Base.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Base.encode(engine, None)
        self.Role.encode(engine, NcBlockMemberDescriptorEnums.Role)
        self.OId.encode(engine, NcBlockMemberDescriptorEnums.OId)
        self.ConstantOId.encode(engine, NcBlockMemberDescriptorEnums.ConstantOId)
        self.ClassId.encode(engine, NcBlockMemberDescriptorEnums.ClassId)
        self.UserLabel.encode(engine, NcBlockMemberDescriptorEnums.UserLabel)
        self.Owner.encode(engine, NcBlockMemberDescriptorEnums.Owner)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcBlockMemberDescriptor")

        self.Base.decode(engine, data)
        if NcBlockMemberDescriptorEnums.Role.s in data:
            self.Role.decode_value(data[NcBlockMemberDescriptorEnums.Role.s])
        if NcBlockMemberDescriptorEnums.OId.s in data:
            self.OId.decode_value(data[NcBlockMemberDescriptorEnums.OId.s])
        if NcBlockMemberDescriptorEnums.ConstantOId.s in data:
            self.ConstantOId.decode_value(data[NcBlockMemberDescriptorEnums.ConstantOId.s])
        if NcBlockMemberDescriptorEnums.ClassId.s in data:
            self.ClassId.decode_value(data[NcBlockMemberDescriptorEnums.ClassId.s])
        if NcBlockMemberDescriptorEnums.UserLabel.s in data:
            self.UserLabel.decode_value(data[NcBlockMemberDescriptorEnums.UserLabel.s])
        if NcBlockMemberDescriptorEnums.Owner.s in data:
            self.Owner.decode_value(data[NcBlockMemberDescriptorEnums.Owner.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcBlockMemberDescriptorValue:
        o = NcBlockMemberDescriptorValue()
        o.Base = self.Base.clone()
        o.Role = self.Role.clone()
        o.OId = self.OId.clone()
        o.ConstantOId = self.ConstantOId.clone()
        o.ClassId = self.ClassId.clone()
        o.UserLabel = self.UserLabel.clone()
        o.Owner = self.Owner.clone()
        return o


class NcBlockMemberDescriptor:
    """Optional object type: NcBlockMemberDescriptor."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcBlockMemberDescriptorValue = NcBlockMemberDescriptorValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcBlockMemberDescriptorValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcBlockMemberDescriptorValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcBlockMemberDescriptorValue | None = None) -> NcBlockMemberDescriptorValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Base(self) -> NcDescriptorValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Base

    def set_Base(self, v: NcDescriptorValue) -> None:
        assert self._defined, "NcBlockMemberDescriptor must be defined before setting Base"
        self._value.Base = v.clone()  # copy to match Go's value semantics

    def get_Role(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Role

    def set_Role(self, v: Any) -> None:
        assert self._defined, "NcBlockMemberDescriptor must be defined before setting Role"
        _assign_value(self._value.Role, v)

    def get_OId(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.OId

    def set_OId(self, v: Any) -> None:
        assert self._defined, "NcBlockMemberDescriptor must be defined before setting OId"
        _assign_value(self._value.OId, v)

    def get_ConstantOId(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ConstantOId

    def set_ConstantOId(self, v: Any) -> None:
        assert self._defined, "NcBlockMemberDescriptor must be defined before setting ConstantOId"
        _assign_value(self._value.ConstantOId, v)

    def get_ClassId(self) -> NArrayOfInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ClassId

    def set_ClassId(self, v: Any) -> None:
        assert self._defined, "NcBlockMemberDescriptor must be defined before setting ClassId"
        _assign_value(self._value.ClassId, v)

    def get_UserLabel(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.UserLabel

    def set_UserLabel(self, v: Any) -> None:
        assert self._defined, "NcBlockMemberDescriptor must be defined before setting UserLabel"
        _assign_value(self._value.UserLabel, v)

    def get_Owner(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Owner

    def set_Owner(self, v: Any) -> None:
        assert self._defined, "NcBlockMemberDescriptor must be defined before setting Owner"
        _assign_value(self._value.Owner, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcBlockMemberDescriptorValue()

    def clone(self) -> NcBlockMemberDescriptor:
        o = NcBlockMemberDescriptor()
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
            return f"NcBlockMemberDescriptor(defined)"
        return "NcBlockMemberDescriptor(<undefined>)"


def make_ncblockmemberdescriptor_value(v: NcBlockMemberDescriptorValue) -> NcBlockMemberDescriptorValue:
    """Factory: create a NcBlockMemberDescriptorValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ncblockmemberdescriptor(v: NcBlockMemberDescriptorValue) -> NcBlockMemberDescriptor:
    """Factory: create a defined NcBlockMemberDescriptor from a NcBlockMemberDescriptorValue."""
    o = NcBlockMemberDescriptor()
    o.set_value(v)
    return o

