"""Generated NMOS type: NQuerySubscriptionResponse. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString, NInt, NBool, NGeneric
from nmos.validators import CheckResourceIdString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NQuerySubscriptionResponseEnums:
    """JSON property name enums for NQuerySubscriptionResponse."""
    Id = EnumRegistry.get("id")
    WsHref = EnumRegistry.get("ws_href")
    MaxUpdateRate_ms = EnumRegistry.get("max_update_rate_ms")
    Persist = EnumRegistry.get("persist")
    ResourcePath = EnumRegistry.get("resource_path")
    Params = EnumRegistry.get("params")
    Secure = EnumRegistry.get("secure")
    Authorization = EnumRegistry.get("authorization")
    pass


class NQuerySubscriptionResponseValue:
    """Inner value struct for NQuerySubscriptionResponse."""

    __slots__ = (
        "Id",
        "WsHref",
        "MaxUpdateRate_ms",
        "Persist",
        "ResourcePath",
        "Params",
        "Secure",
        "Authorization",
    )

    def __init__(self) -> None:
        self.Id: NString = NString()
        self.WsHref: NString = NString()
        self.MaxUpdateRate_ms: NInt = NInt()
        self.Persist: NBool = NBool()
        self.ResourcePath: NString = NString()
        self.Params: NGeneric = NGeneric()
        self.Secure: NBool = NBool()
        self.Authorization: NBool = NBool()

    def set_to_default(self) -> None:
        self.Id.set_to_default()
        self.WsHref.set_to_default()
        _assign_value(self.MaxUpdateRate_ms, 100)
        _assign_value(self.Persist, False)
        self.ResourcePath.set_to_default()
        self.Params.set_to_default()
        self.Secure.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Id.defined:
            raise InvalidObject("missing required member Id")
        if not self.WsHref.defined:
            raise InvalidObject("missing required member WsHref")
        if not self.MaxUpdateRate_ms.defined:
            raise InvalidObject("missing required member MaxUpdateRate_ms")
        if not self.Persist.defined:
            raise InvalidObject("missing required member Persist")
        if not self.ResourcePath.defined:
            raise InvalidObject("missing required member ResourcePath")
        if not self.Params.defined:
            raise InvalidObject("missing required member Params")
        if not self.Secure.defined:
            raise InvalidObject("missing required member Secure")
        if self.Id.defined:
            CheckResourceIdString(self.Id)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Id.encode(engine, NQuerySubscriptionResponseEnums.Id)
        self.WsHref.encode(engine, NQuerySubscriptionResponseEnums.WsHref)
        self.MaxUpdateRate_ms.encode(engine, NQuerySubscriptionResponseEnums.MaxUpdateRate_ms)
        self.Persist.encode(engine, NQuerySubscriptionResponseEnums.Persist)
        self.ResourcePath.encode(engine, NQuerySubscriptionResponseEnums.ResourcePath)
        self.Params.encode(engine, NQuerySubscriptionResponseEnums.Params)
        self.Secure.encode(engine, NQuerySubscriptionResponseEnums.Secure)
        self.Authorization.encode(engine, NQuerySubscriptionResponseEnums.Authorization)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NQuerySubscriptionResponse")

        if NQuerySubscriptionResponseEnums.Id.s in data:
            self.Id.decode_value(data[NQuerySubscriptionResponseEnums.Id.s])
        if NQuerySubscriptionResponseEnums.WsHref.s in data:
            self.WsHref.decode_value(data[NQuerySubscriptionResponseEnums.WsHref.s])
        if NQuerySubscriptionResponseEnums.MaxUpdateRate_ms.s in data:
            self.MaxUpdateRate_ms.decode_value(data[NQuerySubscriptionResponseEnums.MaxUpdateRate_ms.s])
        if NQuerySubscriptionResponseEnums.Persist.s in data:
            self.Persist.decode_value(data[NQuerySubscriptionResponseEnums.Persist.s])
        if NQuerySubscriptionResponseEnums.ResourcePath.s in data:
            self.ResourcePath.decode_value(data[NQuerySubscriptionResponseEnums.ResourcePath.s])
        if NQuerySubscriptionResponseEnums.Params.s in data:
            self.Params.decode_value(data[NQuerySubscriptionResponseEnums.Params.s])
        if NQuerySubscriptionResponseEnums.Secure.s in data:
            self.Secure.decode_value(data[NQuerySubscriptionResponseEnums.Secure.s])
        if NQuerySubscriptionResponseEnums.Authorization.s in data:
            self.Authorization.decode_value(data[NQuerySubscriptionResponseEnums.Authorization.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NQuerySubscriptionResponseValue:
        o = NQuerySubscriptionResponseValue()
        o.Id = self.Id.clone()
        o.WsHref = self.WsHref.clone()
        o.MaxUpdateRate_ms = self.MaxUpdateRate_ms.clone()
        o.Persist = self.Persist.clone()
        o.ResourcePath = self.ResourcePath.clone()
        o.Params = self.Params.clone()
        o.Secure = self.Secure.clone()
        o.Authorization = self.Authorization.clone()
        return o


class NQuerySubscriptionResponse:
    """Optional object type: NQuerySubscriptionResponse."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NQuerySubscriptionResponseValue = NQuerySubscriptionResponseValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NQuerySubscriptionResponseValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NQuerySubscriptionResponseValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NQuerySubscriptionResponseValue | None = None) -> NQuerySubscriptionResponseValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Id(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Id

    def set_Id(self, v: Any) -> None:
        assert self._defined, "NQuerySubscriptionResponse must be defined before setting Id"
        _assign_value(self._value.Id, v)

    def get_WsHref(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.WsHref

    def set_WsHref(self, v: Any) -> None:
        assert self._defined, "NQuerySubscriptionResponse must be defined before setting WsHref"
        _assign_value(self._value.WsHref, v)

    def get_MaxUpdateRate_ms(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MaxUpdateRate_ms

    def set_MaxUpdateRate_ms(self, v: Any) -> None:
        assert self._defined, "NQuerySubscriptionResponse must be defined before setting MaxUpdateRate_ms"
        _assign_value(self._value.MaxUpdateRate_ms, v)

    def get_Persist(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Persist

    def set_Persist(self, v: Any) -> None:
        assert self._defined, "NQuerySubscriptionResponse must be defined before setting Persist"
        _assign_value(self._value.Persist, v)

    def get_ResourcePath(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ResourcePath

    def set_ResourcePath(self, v: Any) -> None:
        assert self._defined, "NQuerySubscriptionResponse must be defined before setting ResourcePath"
        _assign_value(self._value.ResourcePath, v)

    def get_Params(self) -> NGeneric:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Params

    def set_Params(self, v: Any) -> None:
        assert self._defined, "NQuerySubscriptionResponse must be defined before setting Params"
        _assign_value(self._value.Params, v)

    def get_Secure(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Secure

    def set_Secure(self, v: Any) -> None:
        assert self._defined, "NQuerySubscriptionResponse must be defined before setting Secure"
        _assign_value(self._value.Secure, v)

    def get_Authorization(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Authorization

    def set_Authorization(self, v: Any) -> None:
        assert self._defined, "NQuerySubscriptionResponse must be defined before setting Authorization"
        _assign_value(self._value.Authorization, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NQuerySubscriptionResponseValue()

    def clone(self) -> NQuerySubscriptionResponse:
        o = NQuerySubscriptionResponse()
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
            return f"NQuerySubscriptionResponse(defined)"
        return "NQuerySubscriptionResponse(<undefined>)"


def make_nquerysubscriptionresponse_value(v: NQuerySubscriptionResponseValue) -> NQuerySubscriptionResponseValue:
    """Factory: create a NQuerySubscriptionResponseValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nquerysubscriptionresponse(v: NQuerySubscriptionResponseValue) -> NQuerySubscriptionResponse:
    """Factory: create a defined NQuerySubscriptionResponse from a NQuerySubscriptionResponseValue."""
    o = NQuerySubscriptionResponse()
    o.set_value(v)
    return o

