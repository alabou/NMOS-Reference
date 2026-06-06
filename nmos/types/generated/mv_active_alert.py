"""Generated NMOS type: MvActiveAlert. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NInt
from nmos.types.generated.mv_alert_descriptor import MvAlertDescriptor, MvAlertDescriptorValue
from nmos.types.generated.mv_array_of_event_counter import MvArrayOfEventCounter, MvArrayOfEventCounterValue

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class MvActiveAlertEnums:
    """JSON property name enums for MvActiveAlert."""
    AlertDescriptorIndex = EnumRegistry.get("alertDescriptorIndex")
    AlertDescriptor = EnumRegistry.get("alertDescriptor")
    EventCounters = EnumRegistry.get("eventCounters")
    pass


class MvActiveAlertValue:
    """Inner value struct for MvActiveAlert."""

    __slots__ = (
        "AlertDescriptorIndex",
        "AlertDescriptor",
        "EventCounters",
    )

    def __init__(self) -> None:
        self.AlertDescriptorIndex: NInt = NInt()
        self.AlertDescriptor: MvAlertDescriptor = MvAlertDescriptor()
        self.EventCounters: MvArrayOfEventCounter = MvArrayOfEventCounter()

    def set_to_default(self) -> None:
        self.AlertDescriptorIndex.set_to_default()
        self.AlertDescriptor.set_to_default()
        self.EventCounters.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.AlertDescriptorIndex.defined:
            raise InvalidObject("missing required member AlertDescriptorIndex")
        if not self.AlertDescriptor.defined:
            raise InvalidObject("missing required member AlertDescriptor")
        if not self.EventCounters.defined:
            raise InvalidObject("missing required member EventCounters")
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.AlertDescriptorIndex.encode(engine, MvActiveAlertEnums.AlertDescriptorIndex)
        self.AlertDescriptor.encode(engine, MvActiveAlertEnums.AlertDescriptor)
        self.EventCounters.encode(engine, MvActiveAlertEnums.EventCounters)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for MvActiveAlert")

        if MvActiveAlertEnums.AlertDescriptorIndex.s in data:
            self.AlertDescriptorIndex.decode_value(data[MvActiveAlertEnums.AlertDescriptorIndex.s])
        if MvActiveAlertEnums.AlertDescriptor.s in data:
            self.AlertDescriptor.decode_value(data[MvActiveAlertEnums.AlertDescriptor.s])
        if MvActiveAlertEnums.EventCounters.s in data:
            self.EventCounters.decode_value(data[MvActiveAlertEnums.EventCounters.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> MvActiveAlertValue:
        o = MvActiveAlertValue()
        o.AlertDescriptorIndex = self.AlertDescriptorIndex.clone()
        o.AlertDescriptor = self.AlertDescriptor.clone()
        o.EventCounters = self.EventCounters.clone()
        return o


class MvActiveAlert:
    """Optional object type: MvActiveAlert."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: MvActiveAlertValue = MvActiveAlertValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> MvActiveAlertValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: MvActiveAlertValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: MvActiveAlertValue | None = None) -> MvActiveAlertValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_AlertDescriptorIndex(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.AlertDescriptorIndex

    def set_AlertDescriptorIndex(self, v: Any) -> None:
        assert self._defined, "MvActiveAlert must be defined before setting AlertDescriptorIndex"
        _assign_value(self._value.AlertDescriptorIndex, v)

    def get_AlertDescriptor(self) -> MvAlertDescriptor:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.AlertDescriptor

    def set_AlertDescriptor(self, v: Any) -> None:
        assert self._defined, "MvActiveAlert must be defined before setting AlertDescriptor"
        _assign_value(self._value.AlertDescriptor, v)

    def get_EventCounters(self) -> MvArrayOfEventCounter:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.EventCounters

    def set_EventCounters(self, v: Any) -> None:
        assert self._defined, "MvActiveAlert must be defined before setting EventCounters"
        _assign_value(self._value.EventCounters, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = MvActiveAlertValue()

    def clone(self) -> MvActiveAlert:
        o = MvActiveAlert()
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
            return f"MvActiveAlert(defined)"
        return "MvActiveAlert(<undefined>)"


def make_mvactivealert_value(v: MvActiveAlertValue) -> MvActiveAlertValue:
    """Factory: create a MvActiveAlertValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_mvactivealert(v: MvActiveAlertValue) -> MvActiveAlert:
    """Factory: create a defined MvActiveAlert from a MvActiveAlertValue."""
    o = MvActiveAlert()
    o.set_value(v)
    return o

