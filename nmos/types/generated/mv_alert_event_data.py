"""Generated NMOS type: MvAlertEventData. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NInt
from nmos.types.generated.mv_alert_descriptor import MvAlertDescriptor, MvAlertDescriptorValue
from nmos.types.generated.mv_event_counter import MvEventCounter, MvEventCounterValue

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class MvAlertEventDataEnums:
    """JSON property name enums for MvAlertEventData."""
    AlertDescriptorIndex = EnumRegistry.get("alertDescriptorIndex")
    AlertDescriptor = EnumRegistry.get("alertDescriptor")
    EventCounter = EnumRegistry.get("eventCounter")
    pass


class MvAlertEventDataValue:
    """Inner value struct for MvAlertEventData."""

    __slots__ = (
        "AlertDescriptorIndex",
        "AlertDescriptor",
        "EventCounter",
    )

    def __init__(self) -> None:
        self.AlertDescriptorIndex: NInt = NInt()
        self.AlertDescriptor: MvAlertDescriptor = MvAlertDescriptor()
        self.EventCounter: MvEventCounter = MvEventCounter()

    def set_to_default(self) -> None:
        self.AlertDescriptorIndex.set_to_default()
        self.AlertDescriptor.set_to_default()
        self.EventCounter.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.AlertDescriptorIndex.defined:
            raise InvalidObject("missing required member AlertDescriptorIndex")
        if not self.AlertDescriptor.defined:
            raise InvalidObject("missing required member AlertDescriptor")
        if not self.EventCounter.defined:
            raise InvalidObject("missing required member EventCounter")
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.AlertDescriptorIndex.encode(engine, MvAlertEventDataEnums.AlertDescriptorIndex)
        self.AlertDescriptor.encode(engine, MvAlertEventDataEnums.AlertDescriptor)
        self.EventCounter.encode(engine, MvAlertEventDataEnums.EventCounter)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for MvAlertEventData")

        if MvAlertEventDataEnums.AlertDescriptorIndex.s in data:
            self.AlertDescriptorIndex.decode_value(data[MvAlertEventDataEnums.AlertDescriptorIndex.s])
        if MvAlertEventDataEnums.AlertDescriptor.s in data:
            self.AlertDescriptor.decode_value(data[MvAlertEventDataEnums.AlertDescriptor.s])
        if MvAlertEventDataEnums.EventCounter.s in data:
            self.EventCounter.decode_value(data[MvAlertEventDataEnums.EventCounter.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> MvAlertEventDataValue:
        o = MvAlertEventDataValue()
        o.AlertDescriptorIndex = self.AlertDescriptorIndex.clone()
        o.AlertDescriptor = self.AlertDescriptor.clone()
        o.EventCounter = self.EventCounter.clone()
        return o


class MvAlertEventData:
    """Optional object type: MvAlertEventData."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: MvAlertEventDataValue = MvAlertEventDataValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> MvAlertEventDataValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: MvAlertEventDataValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: MvAlertEventDataValue | None = None) -> MvAlertEventDataValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_AlertDescriptorIndex(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.AlertDescriptorIndex

    def set_AlertDescriptorIndex(self, v: Any) -> None:
        assert self._defined, "MvAlertEventData must be defined before setting AlertDescriptorIndex"
        _assign_value(self._value.AlertDescriptorIndex, v)

    def get_AlertDescriptor(self) -> MvAlertDescriptor:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.AlertDescriptor

    def set_AlertDescriptor(self, v: Any) -> None:
        assert self._defined, "MvAlertEventData must be defined before setting AlertDescriptor"
        _assign_value(self._value.AlertDescriptor, v)

    def get_EventCounter(self) -> MvEventCounter:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.EventCounter

    def set_EventCounter(self, v: Any) -> None:
        assert self._defined, "MvAlertEventData must be defined before setting EventCounter"
        _assign_value(self._value.EventCounter, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = MvAlertEventDataValue()

    def clone(self) -> MvAlertEventData:
        o = MvAlertEventData()
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
            return f"MvAlertEventData(defined)"
        return "MvAlertEventData(<undefined>)"


def make_mvalerteventdata_value(v: MvAlertEventDataValue) -> MvAlertEventDataValue:
    """Factory: create a MvAlertEventDataValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_mvalerteventdata(v: MvAlertEventDataValue) -> MvAlertEventData:
    """Factory: create a defined MvAlertEventData from a MvAlertEventDataValue."""
    o = MvAlertEventData()
    o.set_value(v)
    return o

