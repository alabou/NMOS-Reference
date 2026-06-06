"""Generated NMOS type: NNodeService. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NUrl, NEnum, NBool
from nmos.validators import CheckServiceType

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NNodeServiceEnums:
    """JSON property name enums for NNodeService."""
    Href = EnumRegistry.get("href")
    Type = EnumRegistry.get("type")
    Authorization = EnumRegistry.get("authorization")
    pass


class NNodeServiceValue:
    """Inner value struct for NNodeService."""

    __slots__ = (
        "Href",
        "Type",
        "Authorization",
    )

    def __init__(self) -> None:
        self.Href: NUrl = NUrl()
        self.Type: NEnum = NEnum()
        self.Authorization: NBool = NBool()

    def set_to_default(self) -> None:
        self.Href.set_to_default()
        self.Type.set_to_default()
        _assign_value(self.Authorization, False)
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        if not self.Authorization.defined:
            _assign_value(self.Authorization, False)
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Href.defined:
            raise InvalidObject("missing required member Href")
        if not self.Type.defined:
            raise InvalidObject("missing required member Type")
        if self.Type.defined:
            CheckServiceType(self.Type)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Href.encode(engine, NNodeServiceEnums.Href)
        self.Type.encode(engine, NNodeServiceEnums.Type)
        self.Authorization.encode(engine, NNodeServiceEnums.Authorization)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NNodeService")

        if NNodeServiceEnums.Href.s in data:
            self.Href.decode_value(data[NNodeServiceEnums.Href.s])
        if NNodeServiceEnums.Type.s in data:
            self.Type.decode_value(data[NNodeServiceEnums.Type.s])
        if NNodeServiceEnums.Authorization.s in data:
            self.Authorization.decode_value(data[NNodeServiceEnums.Authorization.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NNodeServiceValue:
        o = NNodeServiceValue()
        o.Href = self.Href.clone()
        o.Type = self.Type.clone()
        o.Authorization = self.Authorization.clone()
        return o


class NNodeService:
    """Optional object type: NNodeService."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NNodeServiceValue = NNodeServiceValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NNodeServiceValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NNodeServiceValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NNodeServiceValue | None = None) -> NNodeServiceValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Href(self) -> NUrl:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Href

    def set_Href(self, v: Any) -> None:
        assert self._defined, "NNodeService must be defined before setting Href"
        _assign_value(self._value.Href, v)

    def get_Type(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Type

    def set_Type(self, v: Any) -> None:
        assert self._defined, "NNodeService must be defined before setting Type"
        _assign_value(self._value.Type, v)

    def get_Authorization(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Authorization

    def set_Authorization(self, v: Any) -> None:
        assert self._defined, "NNodeService must be defined before setting Authorization"
        _assign_value(self._value.Authorization, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NNodeServiceValue()

    def clone(self) -> NNodeService:
        o = NNodeService()
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
            return f"NNodeService(defined)"
        return "NNodeService(<undefined>)"


def make_nnodeservice_value(v: NNodeServiceValue) -> NNodeServiceValue:
    """Factory: create a NNodeServiceValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nnodeservice(v: NNodeServiceValue) -> NNodeService:
    """Factory: create a defined NNodeService from a NNodeServiceValue."""
    o = NNodeService()
    o.set_value(v)
    return o

