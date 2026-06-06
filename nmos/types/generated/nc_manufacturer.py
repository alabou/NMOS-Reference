"""Generated NMOS type: NcManufacturer. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString, NNull, NNullString
from nmos.validators import CheckNullInteger

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcManufacturerEnums:
    """JSON property name enums for NcManufacturer."""
    Name = EnumRegistry.get("name")
    OrganizationId = EnumRegistry.get("organizationId")
    WebSite = EnumRegistry.get("website")
    pass


class NcManufacturerValue:
    """Inner value struct for NcManufacturer."""

    __slots__ = (
        "Name",
        "OrganizationId",
        "WebSite",
    )

    def __init__(self) -> None:
        self.Name: NString = NString()
        self.OrganizationId: NNull = NNull()
        self.WebSite: NNullString = NNullString()

    def set_to_default(self) -> None:
        self.Name.set_to_default()
        self.OrganizationId.set_to_default()
        self.WebSite.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Name.defined:
            raise InvalidObject("missing required member Name")
        if not self.OrganizationId.defined:
            raise InvalidObject("missing required member OrganizationId")
        if not self.WebSite.defined:
            raise InvalidObject("missing required member WebSite")
        if self.OrganizationId.defined:
            CheckNullInteger(self.OrganizationId)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Name.encode(engine, NcManufacturerEnums.Name)
        self.OrganizationId.encode(engine, NcManufacturerEnums.OrganizationId)
        self.WebSite.encode(engine, NcManufacturerEnums.WebSite)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcManufacturer")

        if NcManufacturerEnums.Name.s in data:
            self.Name.decode_value(data[NcManufacturerEnums.Name.s])
        if NcManufacturerEnums.OrganizationId.s in data:
            self.OrganizationId.decode_value(data[NcManufacturerEnums.OrganizationId.s])
        if NcManufacturerEnums.WebSite.s in data:
            self.WebSite.decode_value(data[NcManufacturerEnums.WebSite.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcManufacturerValue:
        o = NcManufacturerValue()
        o.Name = self.Name.clone()
        o.OrganizationId = self.OrganizationId.clone()
        o.WebSite = self.WebSite.clone()
        return o


class NcManufacturer:
    """Optional object type: NcManufacturer."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcManufacturerValue = NcManufacturerValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcManufacturerValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcManufacturerValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcManufacturerValue | None = None) -> NcManufacturerValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Name(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Name

    def set_Name(self, v: Any) -> None:
        assert self._defined, "NcManufacturer must be defined before setting Name"
        _assign_value(self._value.Name, v)

    def get_OrganizationId(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.OrganizationId

    def set_OrganizationId(self, v: Any) -> None:
        assert self._defined, "NcManufacturer must be defined before setting OrganizationId"
        _assign_value(self._value.OrganizationId, v)

    def get_WebSite(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.WebSite

    def set_WebSite(self, v: Any) -> None:
        assert self._defined, "NcManufacturer must be defined before setting WebSite"
        _assign_value(self._value.WebSite, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcManufacturerValue()

    def clone(self) -> NcManufacturer:
        o = NcManufacturer()
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
            return f"NcManufacturer(defined)"
        return "NcManufacturer(<undefined>)"


def make_ncmanufacturer_value(v: NcManufacturerValue) -> NcManufacturerValue:
    """Factory: create a NcManufacturerValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ncmanufacturer(v: NcManufacturerValue) -> NcManufacturer:
    """Factory: create a defined NcManufacturer from a NcManufacturerValue."""
    o = NcManufacturer()
    o.set_value(v)
    return o

