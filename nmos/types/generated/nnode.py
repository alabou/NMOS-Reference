"""Generated NMOS type: NNode. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NUrl
from nmos.types.generated.nresource_core import NResourceCore, NResourceCoreValue
from nmos.types.generated.nempty import NEmpty, NEmptyValue
from nmos.types.generated.nnode_api import NNodeApi, NNodeApiValue
from nmos.types.generated.narray_of_node_service import NArrayOfNodeService, NArrayOfNodeServiceValue
from nmos.types.generated.narray_of_clock import NArrayOfClock, NArrayOfClockValue
from nmos.types.generated.narray_of_node_interface import NArrayOfNodeInterface, NArrayOfNodeInterfaceValue

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NNodeEnums:
    """JSON property name enums for NNode."""
    Href = EnumRegistry.get("href")
    Caps = EnumRegistry.get("caps")
    Api = EnumRegistry.get("api")
    Services = EnumRegistry.get("services")
    Clocks = EnumRegistry.get("clocks")
    Interfaces = EnumRegistry.get("interfaces")
    pass


class NNodeValue:
    """Inner value struct for NNode."""

    __slots__ = (
        "ResourceCore",
        "Href",
        "Caps",
        "Api",
        "Services",
        "Clocks",
        "Interfaces",
    )

    def __init__(self) -> None:
        self.ResourceCore: NResourceCoreValue = NResourceCoreValue()
        self.Href: NUrl = NUrl()
        self.Caps: NEmpty = NEmpty()
        self.Api: NNodeApi = NNodeApi()
        self.Services: NArrayOfNodeService = NArrayOfNodeService()
        self.Clocks: NArrayOfClock = NArrayOfClock()
        self.Interfaces: NArrayOfNodeInterface = NArrayOfNodeInterface()

    def set_to_default(self) -> None:
        self.ResourceCore = NResourceCoreValue()
        self.ResourceCore.set_to_default()
        self.Href.set_to_default()
        self.Caps.set_to_default()
        self.Api.set_to_default()
        self.Services.set_to_default()
        self.Clocks.set_to_default()
        self.Interfaces.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Href.defined:
            raise InvalidObject("missing required member Href")
        if not self.Caps.defined:
            raise InvalidObject("missing required member Caps")
        if not self.Api.defined:
            raise InvalidObject("missing required member Api")
        if not self.Services.defined:
            raise InvalidObject("missing required member Services")
        if not self.Clocks.defined:
            raise InvalidObject("missing required member Clocks")
        if not self.Interfaces.defined:
            raise InvalidObject("missing required member Interfaces")
        self.ResourceCore.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.ResourceCore.encode(engine, None)
        self.Href.encode(engine, NNodeEnums.Href)
        self.Caps.encode(engine, NNodeEnums.Caps)
        self.Api.encode(engine, NNodeEnums.Api)
        self.Services.encode(engine, NNodeEnums.Services)
        self.Clocks.encode(engine, NNodeEnums.Clocks)
        self.Interfaces.encode(engine, NNodeEnums.Interfaces)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NNode")

        self.ResourceCore.decode(engine, data)
        if NNodeEnums.Href.s in data:
            self.Href.decode_value(data[NNodeEnums.Href.s])
        if NNodeEnums.Caps.s in data:
            self.Caps.decode_value(data[NNodeEnums.Caps.s])
        if NNodeEnums.Api.s in data:
            self.Api.decode_value(data[NNodeEnums.Api.s])
        if NNodeEnums.Services.s in data:
            self.Services.decode_value(data[NNodeEnums.Services.s])
        if NNodeEnums.Clocks.s in data:
            self.Clocks.decode_value(data[NNodeEnums.Clocks.s])
        if NNodeEnums.Interfaces.s in data:
            self.Interfaces.decode_value(data[NNodeEnums.Interfaces.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NNodeValue:
        o = NNodeValue()
        o.ResourceCore = self.ResourceCore.clone()
        o.Href = self.Href.clone()
        o.Caps = self.Caps.clone()
        o.Api = self.Api.clone()
        o.Services = self.Services.clone()
        o.Clocks = self.Clocks.clone()
        o.Interfaces = self.Interfaces.clone()
        return o


class NNode:
    """Optional object type: NNode."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NNodeValue = NNodeValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NNodeValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NNodeValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NNodeValue | None = None) -> NNodeValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_ResourceCore(self) -> NResourceCoreValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ResourceCore

    def set_ResourceCore(self, v: NResourceCoreValue) -> None:
        assert self._defined, "NNode must be defined before setting ResourceCore"
        self._value.ResourceCore = v.clone()  # copy to match Go's value semantics

    def get_Href(self) -> NUrl:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Href

    def set_Href(self, v: Any) -> None:
        assert self._defined, "NNode must be defined before setting Href"
        _assign_value(self._value.Href, v)

    def get_Caps(self) -> NEmpty:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Caps

    def set_Caps(self, v: Any) -> None:
        assert self._defined, "NNode must be defined before setting Caps"
        _assign_value(self._value.Caps, v)

    def get_Api(self) -> NNodeApi:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Api

    def set_Api(self, v: Any) -> None:
        assert self._defined, "NNode must be defined before setting Api"
        _assign_value(self._value.Api, v)

    def get_Services(self) -> NArrayOfNodeService:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Services

    def set_Services(self, v: Any) -> None:
        assert self._defined, "NNode must be defined before setting Services"
        _assign_value(self._value.Services, v)

    def get_Clocks(self) -> NArrayOfClock:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Clocks

    def set_Clocks(self, v: Any) -> None:
        assert self._defined, "NNode must be defined before setting Clocks"
        _assign_value(self._value.Clocks, v)

    def get_Interfaces(self) -> NArrayOfNodeInterface:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Interfaces

    def set_Interfaces(self, v: Any) -> None:
        assert self._defined, "NNode must be defined before setting Interfaces"
        _assign_value(self._value.Interfaces, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NNodeValue()

    def clone(self) -> NNode:
        o = NNode()
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
            return f"NNode(defined)"
        return "NNode(<undefined>)"


def make_nnode_value(v: NNodeValue) -> NNodeValue:
    """Factory: create a NNodeValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nnode(v: NNodeValue) -> NNode:
    """Factory: create a defined NNode from a NNodeValue."""
    o = NNode()
    o.set_value(v)
    return o

