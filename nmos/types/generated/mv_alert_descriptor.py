"""Generated NMOS type: MvAlertDescriptor. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NBool, NInt, NArrayOfString, NArrayOfInt
from nmos.types.generated.mv_array_of_event_counter import MvArrayOfEventCounter, MvArrayOfEventCounterValue

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class MvAlertDescriptorEnums:
    """JSON property name enums for MvAlertDescriptor."""
    Enabled = EnumRegistry.get("enabled")
    AlertDomain = EnumRegistry.get("alertDomain")
    AlertScope = EnumRegistry.get("alertScope")
    ResourceIds = EnumRegistry.get("resourceIds")
    InterfaceNames = EnumRegistry.get("interfaceNames")
    Events = EnumRegistry.get("events")
    pass


class MvAlertDescriptorValue:
    """Inner value struct for MvAlertDescriptor."""

    __slots__ = (
        "Enabled",
        "AlertDomain",
        "AlertScope",
        "ResourceIds",
        "InterfaceNames",
        "Events",
        "EventCounters",
        "Active",
    )

    def __init__(self) -> None:
        self.Enabled: NBool = NBool()
        self.AlertDomain: NInt = NInt()
        self.AlertScope: NInt = NInt()
        self.ResourceIds: NArrayOfString = NArrayOfString()
        self.InterfaceNames: NArrayOfString = NArrayOfString()
        self.Events: NArrayOfInt = NArrayOfInt()
        self.EventCounters: MvArrayOfEventCounter = MvArrayOfEventCounter()
        self.Active: NBool = NBool()

    def set_to_default(self) -> None:
        self.Enabled.set_to_default()
        self.AlertDomain.set_to_default()
        self.AlertScope.set_to_default()
        self.ResourceIds.set_to_default()
        self.InterfaceNames.set_to_default()
        self.Events.set_to_default()
        self.EventCounters.set_to_default()
        self.Active.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Enabled.defined:
            raise InvalidObject("missing required member Enabled")
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
        self.Enabled.encode(engine, MvAlertDescriptorEnums.Enabled)
        self.AlertDomain.encode(engine, MvAlertDescriptorEnums.AlertDomain)
        self.AlertScope.encode(engine, MvAlertDescriptorEnums.AlertScope)
        self.ResourceIds.encode(engine, MvAlertDescriptorEnums.ResourceIds)
        self.InterfaceNames.encode(engine, MvAlertDescriptorEnums.InterfaceNames)
        self.Events.encode(engine, MvAlertDescriptorEnums.Events)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for MvAlertDescriptor")

        if MvAlertDescriptorEnums.Enabled.s in data:
            self.Enabled.decode_value(data[MvAlertDescriptorEnums.Enabled.s])
        if MvAlertDescriptorEnums.AlertDomain.s in data:
            self.AlertDomain.decode_value(data[MvAlertDescriptorEnums.AlertDomain.s])
        if MvAlertDescriptorEnums.AlertScope.s in data:
            self.AlertScope.decode_value(data[MvAlertDescriptorEnums.AlertScope.s])
        if MvAlertDescriptorEnums.ResourceIds.s in data:
            self.ResourceIds.decode_value(data[MvAlertDescriptorEnums.ResourceIds.s])
        if MvAlertDescriptorEnums.InterfaceNames.s in data:
            self.InterfaceNames.decode_value(data[MvAlertDescriptorEnums.InterfaceNames.s])
        if MvAlertDescriptorEnums.Events.s in data:
            self.Events.decode_value(data[MvAlertDescriptorEnums.Events.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> MvAlertDescriptorValue:
        o = MvAlertDescriptorValue()
        o.Enabled = self.Enabled.clone()
        o.AlertDomain = self.AlertDomain.clone()
        o.AlertScope = self.AlertScope.clone()
        o.ResourceIds = self.ResourceIds.clone()
        o.InterfaceNames = self.InterfaceNames.clone()
        o.Events = self.Events.clone()
        o.EventCounters = self.EventCounters.clone()
        o.Active = self.Active.clone()
        return o


class MvAlertDescriptor:
    """Optional object type: MvAlertDescriptor."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: MvAlertDescriptorValue = MvAlertDescriptorValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> MvAlertDescriptorValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: MvAlertDescriptorValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: MvAlertDescriptorValue | None = None) -> MvAlertDescriptorValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Enabled(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Enabled

    def set_Enabled(self, v: Any) -> None:
        assert self._defined, "MvAlertDescriptor must be defined before setting Enabled"
        _assign_value(self._value.Enabled, v)

    def get_AlertDomain(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.AlertDomain

    def set_AlertDomain(self, v: Any) -> None:
        assert self._defined, "MvAlertDescriptor must be defined before setting AlertDomain"
        _assign_value(self._value.AlertDomain, v)

    def get_AlertScope(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.AlertScope

    def set_AlertScope(self, v: Any) -> None:
        assert self._defined, "MvAlertDescriptor must be defined before setting AlertScope"
        _assign_value(self._value.AlertScope, v)

    def get_ResourceIds(self) -> NArrayOfString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ResourceIds

    def set_ResourceIds(self, v: Any) -> None:
        assert self._defined, "MvAlertDescriptor must be defined before setting ResourceIds"
        _assign_value(self._value.ResourceIds, v)

    def get_InterfaceNames(self) -> NArrayOfString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.InterfaceNames

    def set_InterfaceNames(self, v: Any) -> None:
        assert self._defined, "MvAlertDescriptor must be defined before setting InterfaceNames"
        _assign_value(self._value.InterfaceNames, v)

    def get_Events(self) -> NArrayOfInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Events

    def set_Events(self, v: Any) -> None:
        assert self._defined, "MvAlertDescriptor must be defined before setting Events"
        _assign_value(self._value.Events, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = MvAlertDescriptorValue()

    def clone(self) -> MvAlertDescriptor:
        o = MvAlertDescriptor()
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
            return f"MvAlertDescriptor(defined)"
        return "MvAlertDescriptor(<undefined>)"


def make_mvalertdescriptor_value(v: MvAlertDescriptorValue) -> MvAlertDescriptorValue:
    """Factory: create a MvAlertDescriptorValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_mvalertdescriptor(v: MvAlertDescriptorValue) -> MvAlertDescriptor:
    """Factory: create a defined MvAlertDescriptor from a MvAlertDescriptorValue."""
    o = MvAlertDescriptor()
    o.set_value(v)
    return o

