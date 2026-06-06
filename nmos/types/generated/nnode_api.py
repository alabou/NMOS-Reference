"""Generated NMOS type: NNodeApi. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NArrayOfString
from nmos.types.generated.narray_of_node_endpoint import NArrayOfNodeEndpoint, NArrayOfNodeEndpointValue
from nmos.validators import CheckNodeApiVersions

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NNodeApiEnums:
    """JSON property name enums for NNodeApi."""
    Versions = EnumRegistry.get("versions")
    Endpoints = EnumRegistry.get("endpoints")
    pass


class NNodeApiValue:
    """Inner value struct for NNodeApi."""

    __slots__ = (
        "Versions",
        "Endpoints",
    )

    def __init__(self) -> None:
        self.Versions: NArrayOfString = NArrayOfString()
        self.Endpoints: NArrayOfNodeEndpoint = NArrayOfNodeEndpoint()

    def set_to_default(self) -> None:
        self.Versions.set_to_default()
        self.Endpoints.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Versions.defined:
            raise InvalidObject("missing required member Versions")
        if not self.Endpoints.defined:
            raise InvalidObject("missing required member Endpoints")
        if self.Versions.defined:
            CheckNodeApiVersions(self.Versions)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Versions.encode(engine, NNodeApiEnums.Versions)
        self.Endpoints.encode(engine, NNodeApiEnums.Endpoints)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NNodeApi")

        if NNodeApiEnums.Versions.s in data:
            self.Versions.decode_value(data[NNodeApiEnums.Versions.s])
        if NNodeApiEnums.Endpoints.s in data:
            self.Endpoints.decode_value(data[NNodeApiEnums.Endpoints.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NNodeApiValue:
        o = NNodeApiValue()
        o.Versions = self.Versions.clone()
        o.Endpoints = self.Endpoints.clone()
        return o


class NNodeApi:
    """Optional object type: NNodeApi."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NNodeApiValue = NNodeApiValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NNodeApiValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NNodeApiValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NNodeApiValue | None = None) -> NNodeApiValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Versions(self) -> NArrayOfString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Versions

    def set_Versions(self, v: Any) -> None:
        assert self._defined, "NNodeApi must be defined before setting Versions"
        _assign_value(self._value.Versions, v)

    def get_Endpoints(self) -> NArrayOfNodeEndpoint:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Endpoints

    def set_Endpoints(self, v: Any) -> None:
        assert self._defined, "NNodeApi must be defined before setting Endpoints"
        _assign_value(self._value.Endpoints, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NNodeApiValue()

    def clone(self) -> NNodeApi:
        o = NNodeApi()
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
            return f"NNodeApi(defined)"
        return "NNodeApi(<undefined>)"


def make_nnodeapi_value(v: NNodeApiValue) -> NNodeApiValue:
    """Factory: create a NNodeApiValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nnodeapi(v: NNodeApiValue) -> NNodeApi:
    """Factory: create a defined NNodeApi from a NNodeApiValue."""
    o = NNodeApi()
    o.set_value(v)
    return o

