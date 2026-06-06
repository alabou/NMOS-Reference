"""Generated NMOS type: MvEventCounter. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NInt, NString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class MvEventCounterEnums:
    """JSON property name enums for MvEventCounter."""
    Event = EnumRegistry.get("event")
    EventCounter = EnumRegistry.get("eventCounter")
    EventState = EnumRegistry.get("eventState")
    EventInfo = EnumRegistry.get("eventInfo")
    InterfaceName = EnumRegistry.get("interfaceName")
    pass


class MvEventCounterValue:
    """Inner value struct for MvEventCounter."""

    __slots__ = (
        "Event",
        "EventCounter",
        "EventState",
        "EventInfo",
        "InterfaceName",
    )

    def __init__(self) -> None:
        self.Event: NInt = NInt()
        self.EventCounter: NInt = NInt()
        self.EventState: NInt = NInt()
        self.EventInfo: NString = NString()
        self.InterfaceName: NString = NString()

    def set_to_default(self) -> None:
        self.Event.set_to_default()
        self.EventCounter.set_to_default()
        self.EventState.set_to_default()
        self.EventInfo.set_to_default()
        self.InterfaceName.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Event.defined:
            raise InvalidObject("missing required member Event")
        if not self.EventCounter.defined:
            raise InvalidObject("missing required member EventCounter")
        if not self.EventState.defined:
            raise InvalidObject("missing required member EventState")
        if not self.EventInfo.defined:
            raise InvalidObject("missing required member EventInfo")
        if not self.InterfaceName.defined:
            raise InvalidObject("missing required member InterfaceName")
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Event.encode(engine, MvEventCounterEnums.Event)
        self.EventCounter.encode(engine, MvEventCounterEnums.EventCounter)
        self.EventState.encode(engine, MvEventCounterEnums.EventState)
        self.EventInfo.encode(engine, MvEventCounterEnums.EventInfo)
        self.InterfaceName.encode(engine, MvEventCounterEnums.InterfaceName)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for MvEventCounter")

        if MvEventCounterEnums.Event.s in data:
            self.Event.decode_value(data[MvEventCounterEnums.Event.s])
        if MvEventCounterEnums.EventCounter.s in data:
            self.EventCounter.decode_value(data[MvEventCounterEnums.EventCounter.s])
        if MvEventCounterEnums.EventState.s in data:
            self.EventState.decode_value(data[MvEventCounterEnums.EventState.s])
        if MvEventCounterEnums.EventInfo.s in data:
            self.EventInfo.decode_value(data[MvEventCounterEnums.EventInfo.s])
        if MvEventCounterEnums.InterfaceName.s in data:
            self.InterfaceName.decode_value(data[MvEventCounterEnums.InterfaceName.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> MvEventCounterValue:
        o = MvEventCounterValue()
        o.Event = self.Event.clone()
        o.EventCounter = self.EventCounter.clone()
        o.EventState = self.EventState.clone()
        o.EventInfo = self.EventInfo.clone()
        o.InterfaceName = self.InterfaceName.clone()
        return o


class MvEventCounter:
    """Optional object type: MvEventCounter."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: MvEventCounterValue = MvEventCounterValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> MvEventCounterValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: MvEventCounterValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: MvEventCounterValue | None = None) -> MvEventCounterValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Event(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Event

    def set_Event(self, v: Any) -> None:
        assert self._defined, "MvEventCounter must be defined before setting Event"
        _assign_value(self._value.Event, v)

    def get_EventCounter(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.EventCounter

    def set_EventCounter(self, v: Any) -> None:
        assert self._defined, "MvEventCounter must be defined before setting EventCounter"
        _assign_value(self._value.EventCounter, v)

    def get_EventState(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.EventState

    def set_EventState(self, v: Any) -> None:
        assert self._defined, "MvEventCounter must be defined before setting EventState"
        _assign_value(self._value.EventState, v)

    def get_EventInfo(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.EventInfo

    def set_EventInfo(self, v: Any) -> None:
        assert self._defined, "MvEventCounter must be defined before setting EventInfo"
        _assign_value(self._value.EventInfo, v)

    def get_InterfaceName(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.InterfaceName

    def set_InterfaceName(self, v: Any) -> None:
        assert self._defined, "MvEventCounter must be defined before setting InterfaceName"
        _assign_value(self._value.InterfaceName, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = MvEventCounterValue()

    def clone(self) -> MvEventCounter:
        o = MvEventCounter()
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
            return f"MvEventCounter(defined)"
        return "MvEventCounter(<undefined>)"


def make_mveventcounter_value(v: MvEventCounterValue) -> MvEventCounterValue:
    """Factory: create a MvEventCounterValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_mveventcounter(v: MvEventCounterValue) -> MvEventCounter:
    """Factory: create a defined MvEventCounter from a MvEventCounterValue."""
    o = MvEventCounter()
    o.set_value(v)
    return o

