"""Generated NMOS type: NcObject. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NArrayOfInt, NInt, NBool, NNull, NString, NNullString, NArrayOfGeneric
from nmos.types.generated.nnc_ptr import NNcPtr, NNcPtrValue
from nmos.validators import CheckNullInteger

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcObjectEnums:
    """JSON property name enums for NcObject."""
    Id = EnumRegistry.get("id")
    OId = EnumRegistry.get("oid")
    ConstantOId = EnumRegistry.get("constantOid")
    Owner = EnumRegistry.get("owner")
    Role = EnumRegistry.get("role")
    UserLabel = EnumRegistry.get("userLabel")
    Touchpoints = EnumRegistry.get("touchpoints")
    RuntimePropertyConstraints = EnumRegistry.get("runtimePropertyConstraints")
    pass


class NcObjectValue:
    """Inner value struct for NcObject."""

    __slots__ = (
        "Id",
        "OId",
        "ConstantOId",
        "Owner",
        "Role",
        "UserLabel",
        "Touchpoints",
        "RuntimePropertyConstraints",
        "NcPtr",
    )

    def __init__(self) -> None:
        self.Id: NArrayOfInt = NArrayOfInt()
        self.OId: NInt = NInt()
        self.ConstantOId: NBool = NBool()
        self.Owner: NNull = NNull()
        self.Role: NString = NString()
        self.UserLabel: NNullString = NNullString()
        self.Touchpoints: NArrayOfGeneric = NArrayOfGeneric()
        self.RuntimePropertyConstraints: NArrayOfGeneric = NArrayOfGeneric()
        self.NcPtr: NNcPtr = NNcPtr()

    def set_to_default(self) -> None:
        self.Id.set_to_default()
        self.OId.set_to_default()
        self.ConstantOId.set_to_default()
        self.Owner.set_to_default()
        self.Role.set_to_default()
        self.UserLabel.set_to_default()
        self.Touchpoints.set_to_default()
        self.RuntimePropertyConstraints.set_to_default()
        self.NcPtr.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Id.defined:
            raise InvalidObject("missing required member Id")
        if not self.OId.defined:
            raise InvalidObject("missing required member OId")
        if not self.ConstantOId.defined:
            raise InvalidObject("missing required member ConstantOId")
        if not self.Owner.defined:
            raise InvalidObject("missing required member Owner")
        if not self.Role.defined:
            raise InvalidObject("missing required member Role")
        if not self.UserLabel.defined:
            raise InvalidObject("missing required member UserLabel")
        if not self.Touchpoints.defined:
            raise InvalidObject("missing required member Touchpoints")
        if not self.RuntimePropertyConstraints.defined:
            raise InvalidObject("missing required member RuntimePropertyConstraints")
        if self.Owner.defined:
            CheckNullInteger(self.Owner)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        self.Id.encode(engine, NcObjectEnums.Id)
        self.OId.encode(engine, NcObjectEnums.OId)
        self.ConstantOId.encode(engine, NcObjectEnums.ConstantOId)
        self.Owner.encode(engine, NcObjectEnums.Owner)
        self.Role.encode(engine, NcObjectEnums.Role)
        self.UserLabel.encode(engine, NcObjectEnums.UserLabel)
        self.Touchpoints.encode(engine, NcObjectEnums.Touchpoints)
        self.RuntimePropertyConstraints.encode(engine, NcObjectEnums.RuntimePropertyConstraints)

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcObject")

        if NcObjectEnums.Id.s in data:
            self.Id.decode_value(data[NcObjectEnums.Id.s])
        if NcObjectEnums.OId.s in data:
            self.OId.decode_value(data[NcObjectEnums.OId.s])
        if NcObjectEnums.ConstantOId.s in data:
            self.ConstantOId.decode_value(data[NcObjectEnums.ConstantOId.s])
        if NcObjectEnums.Owner.s in data:
            self.Owner.decode_value(data[NcObjectEnums.Owner.s])
        if NcObjectEnums.Role.s in data:
            self.Role.decode_value(data[NcObjectEnums.Role.s])
        if NcObjectEnums.UserLabel.s in data:
            self.UserLabel.decode_value(data[NcObjectEnums.UserLabel.s])
        if NcObjectEnums.Touchpoints.s in data:
            self.Touchpoints.decode_value(data[NcObjectEnums.Touchpoints.s])
        if NcObjectEnums.RuntimePropertyConstraints.s in data:
            self.RuntimePropertyConstraints.decode_value(data[NcObjectEnums.RuntimePropertyConstraints.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcObjectValue:
        o = NcObjectValue()
        o.Id = self.Id.clone()
        o.OId = self.OId.clone()
        o.ConstantOId = self.ConstantOId.clone()
        o.Owner = self.Owner.clone()
        o.Role = self.Role.clone()
        o.UserLabel = self.UserLabel.clone()
        o.Touchpoints = self.Touchpoints.clone()
        o.RuntimePropertyConstraints = self.RuntimePropertyConstraints.clone()
        o.NcPtr = self.NcPtr.clone()
        return o


class NcObject:
    """Optional object type: NcObject."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcObjectValue = NcObjectValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcObjectValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcObjectValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcObjectValue | None = None) -> NcObjectValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Id(self) -> NArrayOfInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Id

    def set_Id(self, v: Any) -> None:
        assert self._defined, "NcObject must be defined before setting Id"
        _assign_value(self._value.Id, v)

    def get_OId(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.OId

    def set_OId(self, v: Any) -> None:
        assert self._defined, "NcObject must be defined before setting OId"
        _assign_value(self._value.OId, v)

    def get_ConstantOId(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ConstantOId

    def set_ConstantOId(self, v: Any) -> None:
        assert self._defined, "NcObject must be defined before setting ConstantOId"
        _assign_value(self._value.ConstantOId, v)

    def get_Owner(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Owner

    def set_Owner(self, v: Any) -> None:
        assert self._defined, "NcObject must be defined before setting Owner"
        _assign_value(self._value.Owner, v)

    def get_Role(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Role

    def set_Role(self, v: Any) -> None:
        assert self._defined, "NcObject must be defined before setting Role"
        _assign_value(self._value.Role, v)

    def get_UserLabel(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.UserLabel

    def set_UserLabel(self, v: Any) -> None:
        assert self._defined, "NcObject must be defined before setting UserLabel"
        _assign_value(self._value.UserLabel, v)

    def get_Touchpoints(self) -> NArrayOfGeneric:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Touchpoints

    def set_Touchpoints(self, v: Any) -> None:
        assert self._defined, "NcObject must be defined before setting Touchpoints"
        _assign_value(self._value.Touchpoints, v)

    def get_RuntimePropertyConstraints(self) -> NArrayOfGeneric:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.RuntimePropertyConstraints

    def set_RuntimePropertyConstraints(self, v: Any) -> None:
        assert self._defined, "NcObject must be defined before setting RuntimePropertyConstraints"
        _assign_value(self._value.RuntimePropertyConstraints, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcObjectValue()

    def clone(self) -> NcObject:
        o = NcObject()
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
            return f"NcObject(defined)"
        return "NcObject(<undefined>)"


def make_ncobject_value(v: NcObjectValue) -> NcObjectValue:
    """Factory: create a NcObjectValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ncobject(v: NcObjectValue) -> NcObject:
    """Factory: create a defined NcObject from a NcObjectValue."""
    o = NcObject()
    o.set_value(v)
    return o

