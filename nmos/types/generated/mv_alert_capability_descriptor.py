"""Generated NMOS type: MvAlertCapabilityDescriptor. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NInt, NArrayOfString, NArrayOfInt

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class MvAlertCapabilityDescriptorEnums:
    """JSON property name enums for MvAlertCapabilityDescriptor."""
    AlertDomain = EnumRegistry.get("alertDomain")
    AlertScope = EnumRegistry.get("alertScope")
    ResourceIds = EnumRegistry.get("resourceIds")
    InterfaceNames = EnumRegistry.get("interfaceNames")
    Events = EnumRegistry.get("events")
    pass


class MvAlertCapabilityDescriptorValue:
    """Inner value struct for MvAlertCapabilityDescriptor."""

    __slots__ = (
        "AlertDomain",
        "AlertScope",
        "ResourceIds",
        "InterfaceNames",
        "Events",
    )

    def __init__(self) -> None:
        self.AlertDomain: NInt = NInt()
        self.AlertScope: NInt = NInt()
        self.ResourceIds: NArrayOfString = NArrayOfString()
        self.InterfaceNames: NArrayOfString = NArrayOfString()
        self.Events: NArrayOfInt = NArrayOfInt()

    def set_to_default(self) -> None:
        self.AlertDomain.set_to_default()
        self.AlertScope.set_to_default()
        self.ResourceIds.set_to_default()
        self.InterfaceNames.set_to_default()
        self.Events.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.AlertDomain.defined:
            raise InvalidObject("missing required member AlertDomain")
        if not self.AlertScope.defined:
            raise InvalidObject("missing required member AlertScope")
        if not self.ResourceIds.defined:
            raise InvalidObject("missing required member ResourceIds")
        if not self.InterfaceNames.defined:
            raise InvalidObject("missing required member InterfaceNames")
        if not self.Events.defined:
            raise InvalidObject("missing required member Events")
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.AlertDomain.encode(engine, MvAlertCapabilityDescriptorEnums.AlertDomain)
        self.AlertScope.encode(engine, MvAlertCapabilityDescriptorEnums.AlertScope)
        self.ResourceIds.encode(engine, MvAlertCapabilityDescriptorEnums.ResourceIds)
        self.InterfaceNames.encode(engine, MvAlertCapabilityDescriptorEnums.InterfaceNames)
        self.Events.encode(engine, MvAlertCapabilityDescriptorEnums.Events)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for MvAlertCapabilityDescriptor")

        if MvAlertCapabilityDescriptorEnums.AlertDomain.s in data:
            self.AlertDomain.decode_value(data[MvAlertCapabilityDescriptorEnums.AlertDomain.s])
        if MvAlertCapabilityDescriptorEnums.AlertScope.s in data:
            self.AlertScope.decode_value(data[MvAlertCapabilityDescriptorEnums.AlertScope.s])
        if MvAlertCapabilityDescriptorEnums.ResourceIds.s in data:
            self.ResourceIds.decode_value(data[MvAlertCapabilityDescriptorEnums.ResourceIds.s])
        if MvAlertCapabilityDescriptorEnums.InterfaceNames.s in data:
            self.InterfaceNames.decode_value(data[MvAlertCapabilityDescriptorEnums.InterfaceNames.s])
        if MvAlertCapabilityDescriptorEnums.Events.s in data:
            self.Events.decode_value(data[MvAlertCapabilityDescriptorEnums.Events.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> MvAlertCapabilityDescriptorValue:
        o = MvAlertCapabilityDescriptorValue()
        o.AlertDomain = self.AlertDomain.clone()
        o.AlertScope = self.AlertScope.clone()
        o.ResourceIds = self.ResourceIds.clone()
        o.InterfaceNames = self.InterfaceNames.clone()
        o.Events = self.Events.clone()
        return o


class MvAlertCapabilityDescriptor:
    """Optional object type: MvAlertCapabilityDescriptor."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: MvAlertCapabilityDescriptorValue = MvAlertCapabilityDescriptorValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> MvAlertCapabilityDescriptorValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: MvAlertCapabilityDescriptorValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: MvAlertCapabilityDescriptorValue | None = None) -> MvAlertCapabilityDescriptorValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_AlertDomain(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.AlertDomain

    def set_AlertDomain(self, v: Any) -> None:
        assert self._defined, "MvAlertCapabilityDescriptor must be defined before setting AlertDomain"
        _assign_value(self._value.AlertDomain, v)

    def get_AlertScope(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.AlertScope

    def set_AlertScope(self, v: Any) -> None:
        assert self._defined, "MvAlertCapabilityDescriptor must be defined before setting AlertScope"
        _assign_value(self._value.AlertScope, v)

    def get_ResourceIds(self) -> NArrayOfString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ResourceIds

    def set_ResourceIds(self, v: Any) -> None:
        assert self._defined, "MvAlertCapabilityDescriptor must be defined before setting ResourceIds"
        _assign_value(self._value.ResourceIds, v)

    def get_InterfaceNames(self) -> NArrayOfString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.InterfaceNames

    def set_InterfaceNames(self, v: Any) -> None:
        assert self._defined, "MvAlertCapabilityDescriptor must be defined before setting InterfaceNames"
        _assign_value(self._value.InterfaceNames, v)

    def get_Events(self) -> NArrayOfInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Events

    def set_Events(self, v: Any) -> None:
        assert self._defined, "MvAlertCapabilityDescriptor must be defined before setting Events"
        _assign_value(self._value.Events, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = MvAlertCapabilityDescriptorValue()

    def clone(self) -> MvAlertCapabilityDescriptor:
        o = MvAlertCapabilityDescriptor()
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
            return f"MvAlertCapabilityDescriptor(defined)"
        return "MvAlertCapabilityDescriptor(<undefined>)"


def make_mvalertcapabilitydescriptor_value(v: MvAlertCapabilityDescriptorValue) -> MvAlertCapabilityDescriptorValue:
    """Factory: create a MvAlertCapabilityDescriptorValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_mvalertcapabilitydescriptor(v: MvAlertCapabilityDescriptorValue) -> MvAlertCapabilityDescriptor:
    """Factory: create a defined MvAlertCapabilityDescriptor from a MvAlertCapabilityDescriptorValue."""
    o = MvAlertCapabilityDescriptor()
    o.set_value(v)
    return o

