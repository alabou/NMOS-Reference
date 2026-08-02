"""Generated NMOS type: NQuerySubscriptionRequest. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NInt, NBool, NString, NGeneric

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NQuerySubscriptionRequestEnums:
    """JSON property name enums for NQuerySubscriptionRequest."""
    MaxUpdateRate_ms = EnumRegistry.get("max_update_rate_ms")
    Persist = EnumRegistry.get("persist")
    ResourcePath = EnumRegistry.get("resource_path")
    Params = EnumRegistry.get("params")
    Secure = EnumRegistry.get("secure")
    Authorization = EnumRegistry.get("authorization")
    pass


class NQuerySubscriptionRequestValue:
    """Inner value struct for NQuerySubscriptionRequest."""

    __slots__ = (
        "MaxUpdateRate_ms",
        "Persist",
        "ResourcePath",
        "Params",
        "Secure",
        "Authorization",
    )

    def __init__(self) -> None:
        self.MaxUpdateRate_ms: NInt = NInt()
        self.Persist: NBool = NBool()
        self.ResourcePath: NString = NString()
        self.Params: NGeneric = NGeneric()
        self.Secure: NBool = NBool()
        self.Authorization: NBool = NBool()

    def set_to_default(self) -> None:
        _assign_value(self.MaxUpdateRate_ms, 100)
        _assign_value(self.Persist, False)
        self.ResourcePath.set_to_default()
        self.Params.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.MaxUpdateRate_ms.defined:
            raise InvalidObject("missing required member MaxUpdateRate_ms")
        if not self.Persist.defined:
            raise InvalidObject("missing required member Persist")
        if not self.ResourcePath.defined:
            raise InvalidObject("missing required member ResourcePath")
        if not self.Params.defined:
            raise InvalidObject("missing required member Params")
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.MaxUpdateRate_ms.encode(engine, NQuerySubscriptionRequestEnums.MaxUpdateRate_ms)
        self.Persist.encode(engine, NQuerySubscriptionRequestEnums.Persist)
        self.ResourcePath.encode(engine, NQuerySubscriptionRequestEnums.ResourcePath)
        self.Params.encode(engine, NQuerySubscriptionRequestEnums.Params)
        self.Secure.encode(engine, NQuerySubscriptionRequestEnums.Secure)
        self.Authorization.encode(engine, NQuerySubscriptionRequestEnums.Authorization)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NQuerySubscriptionRequest")

        if NQuerySubscriptionRequestEnums.MaxUpdateRate_ms.s in data:
            self.MaxUpdateRate_ms.decode_value(data[NQuerySubscriptionRequestEnums.MaxUpdateRate_ms.s])
        if NQuerySubscriptionRequestEnums.Persist.s in data:
            self.Persist.decode_value(data[NQuerySubscriptionRequestEnums.Persist.s])
        if NQuerySubscriptionRequestEnums.ResourcePath.s in data:
            self.ResourcePath.decode_value(data[NQuerySubscriptionRequestEnums.ResourcePath.s])
        if NQuerySubscriptionRequestEnums.Params.s in data:
            self.Params.decode_value(data[NQuerySubscriptionRequestEnums.Params.s])
        if NQuerySubscriptionRequestEnums.Secure.s in data:
            self.Secure.decode_value(data[NQuerySubscriptionRequestEnums.Secure.s])
        if NQuerySubscriptionRequestEnums.Authorization.s in data:
            self.Authorization.decode_value(data[NQuerySubscriptionRequestEnums.Authorization.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NQuerySubscriptionRequestValue:
        o = NQuerySubscriptionRequestValue()
        o.MaxUpdateRate_ms = self.MaxUpdateRate_ms.clone()
        o.Persist = self.Persist.clone()
        o.ResourcePath = self.ResourcePath.clone()
        o.Params = self.Params.clone()
        o.Secure = self.Secure.clone()
        o.Authorization = self.Authorization.clone()
        return o


class NQuerySubscriptionRequest:
    """Optional object type: NQuerySubscriptionRequest."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NQuerySubscriptionRequestValue = NQuerySubscriptionRequestValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NQuerySubscriptionRequestValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NQuerySubscriptionRequestValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NQuerySubscriptionRequestValue | None = None) -> NQuerySubscriptionRequestValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_MaxUpdateRate_ms(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MaxUpdateRate_ms

    def set_MaxUpdateRate_ms(self, v: Any) -> None:
        assert self._defined, "NQuerySubscriptionRequest must be defined before setting MaxUpdateRate_ms"
        _assign_value(self._value.MaxUpdateRate_ms, v)

    def get_Persist(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Persist

    def set_Persist(self, v: Any) -> None:
        assert self._defined, "NQuerySubscriptionRequest must be defined before setting Persist"
        _assign_value(self._value.Persist, v)

    def get_ResourcePath(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ResourcePath

    def set_ResourcePath(self, v: Any) -> None:
        assert self._defined, "NQuerySubscriptionRequest must be defined before setting ResourcePath"
        _assign_value(self._value.ResourcePath, v)

    def get_Params(self) -> NGeneric:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Params

    def set_Params(self, v: Any) -> None:
        assert self._defined, "NQuerySubscriptionRequest must be defined before setting Params"
        _assign_value(self._value.Params, v)

    def get_Secure(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Secure

    def set_Secure(self, v: Any) -> None:
        assert self._defined, "NQuerySubscriptionRequest must be defined before setting Secure"
        _assign_value(self._value.Secure, v)

    def get_Authorization(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Authorization

    def set_Authorization(self, v: Any) -> None:
        assert self._defined, "NQuerySubscriptionRequest must be defined before setting Authorization"
        _assign_value(self._value.Authorization, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NQuerySubscriptionRequestValue()

    def clone(self) -> NQuerySubscriptionRequest:
        o = NQuerySubscriptionRequest()
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
            return f"NQuerySubscriptionRequest(defined)"
        return "NQuerySubscriptionRequest(<undefined>)"


def make_nquerysubscriptionrequest_value(v: NQuerySubscriptionRequestValue) -> NQuerySubscriptionRequestValue:
    """Factory: create a NQuerySubscriptionRequestValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nquerysubscriptionrequest(v: NQuerySubscriptionRequestValue) -> NQuerySubscriptionRequest:
    """Factory: create a defined NQuerySubscriptionRequest from a NQuerySubscriptionRequestValue."""
    o = NQuerySubscriptionRequest()
    o.set_value(v)
    return o

