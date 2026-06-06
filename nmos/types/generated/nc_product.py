"""Generated NMOS type: NcProduct. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString, NNullString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcProductEnums:
    """JSON property name enums for NcProduct."""
    Name = EnumRegistry.get("name")
    Key = EnumRegistry.get("key")
    RevisionLevel = EnumRegistry.get("revisionLevel")
    BrandName = EnumRegistry.get("brandName")
    Uuid = EnumRegistry.get("uuid")
    Description = EnumRegistry.get("description")
    pass


class NcProductValue:
    """Inner value struct for NcProduct."""

    __slots__ = (
        "Name",
        "Key",
        "RevisionLevel",
        "BrandName",
        "Uuid",
        "Description",
    )

    def __init__(self) -> None:
        self.Name: NString = NString()
        self.Key: NString = NString()
        self.RevisionLevel: NString = NString()
        self.BrandName: NNullString = NNullString()
        self.Uuid: NString = NString()
        self.Description: NNullString = NNullString()

    def set_to_default(self) -> None:
        self.Name.set_to_default()
        self.Key.set_to_default()
        self.RevisionLevel.set_to_default()
        self.BrandName.set_to_default()
        self.Uuid.set_to_default()
        self.Description.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Name.defined:
            raise InvalidObject("missing required member Name")
        if not self.Key.defined:
            raise InvalidObject("missing required member Key")
        if not self.RevisionLevel.defined:
            raise InvalidObject("missing required member RevisionLevel")
        if not self.BrandName.defined:
            raise InvalidObject("missing required member BrandName")
        if not self.Uuid.defined:
            raise InvalidObject("missing required member Uuid")
        if not self.Description.defined:
            raise InvalidObject("missing required member Description")
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Name.encode(engine, NcProductEnums.Name)
        self.Key.encode(engine, NcProductEnums.Key)
        self.RevisionLevel.encode(engine, NcProductEnums.RevisionLevel)
        self.BrandName.encode(engine, NcProductEnums.BrandName)
        self.Uuid.encode(engine, NcProductEnums.Uuid)
        self.Description.encode(engine, NcProductEnums.Description)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcProduct")

        if NcProductEnums.Name.s in data:
            self.Name.decode_value(data[NcProductEnums.Name.s])
        if NcProductEnums.Key.s in data:
            self.Key.decode_value(data[NcProductEnums.Key.s])
        if NcProductEnums.RevisionLevel.s in data:
            self.RevisionLevel.decode_value(data[NcProductEnums.RevisionLevel.s])
        if NcProductEnums.BrandName.s in data:
            self.BrandName.decode_value(data[NcProductEnums.BrandName.s])
        if NcProductEnums.Uuid.s in data:
            self.Uuid.decode_value(data[NcProductEnums.Uuid.s])
        if NcProductEnums.Description.s in data:
            self.Description.decode_value(data[NcProductEnums.Description.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcProductValue:
        o = NcProductValue()
        o.Name = self.Name.clone()
        o.Key = self.Key.clone()
        o.RevisionLevel = self.RevisionLevel.clone()
        o.BrandName = self.BrandName.clone()
        o.Uuid = self.Uuid.clone()
        o.Description = self.Description.clone()
        return o


class NcProduct:
    """Optional object type: NcProduct."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcProductValue = NcProductValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcProductValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcProductValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcProductValue | None = None) -> NcProductValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Name(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Name

    def set_Name(self, v: Any) -> None:
        assert self._defined, "NcProduct must be defined before setting Name"
        _assign_value(self._value.Name, v)

    def get_Key(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Key

    def set_Key(self, v: Any) -> None:
        assert self._defined, "NcProduct must be defined before setting Key"
        _assign_value(self._value.Key, v)

    def get_RevisionLevel(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.RevisionLevel

    def set_RevisionLevel(self, v: Any) -> None:
        assert self._defined, "NcProduct must be defined before setting RevisionLevel"
        _assign_value(self._value.RevisionLevel, v)

    def get_BrandName(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.BrandName

    def set_BrandName(self, v: Any) -> None:
        assert self._defined, "NcProduct must be defined before setting BrandName"
        _assign_value(self._value.BrandName, v)

    def get_Uuid(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Uuid

    def set_Uuid(self, v: Any) -> None:
        assert self._defined, "NcProduct must be defined before setting Uuid"
        _assign_value(self._value.Uuid, v)

    def get_Description(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Description

    def set_Description(self, v: Any) -> None:
        assert self._defined, "NcProduct must be defined before setting Description"
        _assign_value(self._value.Description, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcProductValue()

    def clone(self) -> NcProduct:
        o = NcProduct()
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
            return f"NcProduct(defined)"
        return "NcProduct(<undefined>)"


def make_ncproduct_value(v: NcProductValue) -> NcProductValue:
    """Factory: create a NcProductValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ncproduct(v: NcProductValue) -> NcProduct:
    """Factory: create a defined NcProduct from a NcProductValue."""
    o = NcProduct()
    o.set_value(v)
    return o

