"""Generated NMOS type: NMonitorState. DO NOT EDIT."""

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


class NMonitorStateEnums:
    """JSON property name enums for NMonitorState."""
    MonitorOverallStatus = EnumRegistry.get("overall_status")
    MonitorOverallStatusMessage = EnumRegistry.get("overall_message")
    MonitorLinkStatus = EnumRegistry.get("link_status")
    MonitorSynchronizationStatus = EnumRegistry.get("synchronization_status")
    MonitorTransmissionStatus = EnumRegistry.get("transmission_status")
    MonitorConnectionStatus = EnumRegistry.get("connection_status")
    MonitorEssenceStatus = EnumRegistry.get("essence_status")
    MonitorStreamStatus = EnumRegistry.get("stream_status")
    MonitorLinkStatusCounter = EnumRegistry.get("link_counter")
    MonitorSynchronizationStatusCounter = EnumRegistry.get("synchronization_counter")
    MonitorTransmissionStatusCounter = EnumRegistry.get("transmission_counter")
    MonitorConnectionStatusCounter = EnumRegistry.get("connection_counter")
    MonitorEssenceStatusCounter = EnumRegistry.get("essence_counter")
    MonitorStreamStatusCounter = EnumRegistry.get("stream_counter")
    pass


class NMonitorStateValue:
    """Inner value struct for NMonitorState."""

    __slots__ = (
        "MonitorOverallStatus",
        "MonitorOverallStatusMessage",
        "MonitorLinkStatus",
        "MonitorSynchronizationStatus",
        "MonitorTransmissionStatus",
        "MonitorConnectionStatus",
        "MonitorEssenceStatus",
        "MonitorStreamStatus",
        "MonitorLinkStatusCounter",
        "MonitorSynchronizationStatusCounter",
        "MonitorTransmissionStatusCounter",
        "MonitorConnectionStatusCounter",
        "MonitorEssenceStatusCounter",
        "MonitorStreamStatusCounter",
    )

    def __init__(self) -> None:
        self.MonitorOverallStatus: NInt = NInt()
        self.MonitorOverallStatusMessage: NString = NString()
        self.MonitorLinkStatus: NInt = NInt()
        self.MonitorSynchronizationStatus: NInt = NInt()
        self.MonitorTransmissionStatus: NInt = NInt()
        self.MonitorConnectionStatus: NInt = NInt()
        self.MonitorEssenceStatus: NInt = NInt()
        self.MonitorStreamStatus: NInt = NInt()
        self.MonitorLinkStatusCounter: NInt = NInt()
        self.MonitorSynchronizationStatusCounter: NInt = NInt()
        self.MonitorTransmissionStatusCounter: NInt = NInt()
        self.MonitorConnectionStatusCounter: NInt = NInt()
        self.MonitorEssenceStatusCounter: NInt = NInt()
        self.MonitorStreamStatusCounter: NInt = NInt()

    def set_to_default(self) -> None:
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.MonitorOverallStatus.encode(engine, NMonitorStateEnums.MonitorOverallStatus)
        self.MonitorOverallStatusMessage.encode(engine, NMonitorStateEnums.MonitorOverallStatusMessage)
        self.MonitorLinkStatus.encode(engine, NMonitorStateEnums.MonitorLinkStatus)
        self.MonitorSynchronizationStatus.encode(engine, NMonitorStateEnums.MonitorSynchronizationStatus)
        self.MonitorTransmissionStatus.encode(engine, NMonitorStateEnums.MonitorTransmissionStatus)
        self.MonitorConnectionStatus.encode(engine, NMonitorStateEnums.MonitorConnectionStatus)
        self.MonitorEssenceStatus.encode(engine, NMonitorStateEnums.MonitorEssenceStatus)
        self.MonitorStreamStatus.encode(engine, NMonitorStateEnums.MonitorStreamStatus)
        self.MonitorLinkStatusCounter.encode(engine, NMonitorStateEnums.MonitorLinkStatusCounter)
        self.MonitorSynchronizationStatusCounter.encode(engine, NMonitorStateEnums.MonitorSynchronizationStatusCounter)
        self.MonitorTransmissionStatusCounter.encode(engine, NMonitorStateEnums.MonitorTransmissionStatusCounter)
        self.MonitorConnectionStatusCounter.encode(engine, NMonitorStateEnums.MonitorConnectionStatusCounter)
        self.MonitorEssenceStatusCounter.encode(engine, NMonitorStateEnums.MonitorEssenceStatusCounter)
        self.MonitorStreamStatusCounter.encode(engine, NMonitorStateEnums.MonitorStreamStatusCounter)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NMonitorState")

        if NMonitorStateEnums.MonitorOverallStatus.s in data:
            self.MonitorOverallStatus.decode_value(data[NMonitorStateEnums.MonitorOverallStatus.s])
        if NMonitorStateEnums.MonitorOverallStatusMessage.s in data:
            self.MonitorOverallStatusMessage.decode_value(data[NMonitorStateEnums.MonitorOverallStatusMessage.s])
        if NMonitorStateEnums.MonitorLinkStatus.s in data:
            self.MonitorLinkStatus.decode_value(data[NMonitorStateEnums.MonitorLinkStatus.s])
        if NMonitorStateEnums.MonitorSynchronizationStatus.s in data:
            self.MonitorSynchronizationStatus.decode_value(data[NMonitorStateEnums.MonitorSynchronizationStatus.s])
        if NMonitorStateEnums.MonitorTransmissionStatus.s in data:
            self.MonitorTransmissionStatus.decode_value(data[NMonitorStateEnums.MonitorTransmissionStatus.s])
        if NMonitorStateEnums.MonitorConnectionStatus.s in data:
            self.MonitorConnectionStatus.decode_value(data[NMonitorStateEnums.MonitorConnectionStatus.s])
        if NMonitorStateEnums.MonitorEssenceStatus.s in data:
            self.MonitorEssenceStatus.decode_value(data[NMonitorStateEnums.MonitorEssenceStatus.s])
        if NMonitorStateEnums.MonitorStreamStatus.s in data:
            self.MonitorStreamStatus.decode_value(data[NMonitorStateEnums.MonitorStreamStatus.s])
        if NMonitorStateEnums.MonitorLinkStatusCounter.s in data:
            self.MonitorLinkStatusCounter.decode_value(data[NMonitorStateEnums.MonitorLinkStatusCounter.s])
        if NMonitorStateEnums.MonitorSynchronizationStatusCounter.s in data:
            self.MonitorSynchronizationStatusCounter.decode_value(data[NMonitorStateEnums.MonitorSynchronizationStatusCounter.s])
        if NMonitorStateEnums.MonitorTransmissionStatusCounter.s in data:
            self.MonitorTransmissionStatusCounter.decode_value(data[NMonitorStateEnums.MonitorTransmissionStatusCounter.s])
        if NMonitorStateEnums.MonitorConnectionStatusCounter.s in data:
            self.MonitorConnectionStatusCounter.decode_value(data[NMonitorStateEnums.MonitorConnectionStatusCounter.s])
        if NMonitorStateEnums.MonitorEssenceStatusCounter.s in data:
            self.MonitorEssenceStatusCounter.decode_value(data[NMonitorStateEnums.MonitorEssenceStatusCounter.s])
        if NMonitorStateEnums.MonitorStreamStatusCounter.s in data:
            self.MonitorStreamStatusCounter.decode_value(data[NMonitorStateEnums.MonitorStreamStatusCounter.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NMonitorStateValue:
        o = NMonitorStateValue()
        o.MonitorOverallStatus = self.MonitorOverallStatus.clone()
        o.MonitorOverallStatusMessage = self.MonitorOverallStatusMessage.clone()
        o.MonitorLinkStatus = self.MonitorLinkStatus.clone()
        o.MonitorSynchronizationStatus = self.MonitorSynchronizationStatus.clone()
        o.MonitorTransmissionStatus = self.MonitorTransmissionStatus.clone()
        o.MonitorConnectionStatus = self.MonitorConnectionStatus.clone()
        o.MonitorEssenceStatus = self.MonitorEssenceStatus.clone()
        o.MonitorStreamStatus = self.MonitorStreamStatus.clone()
        o.MonitorLinkStatusCounter = self.MonitorLinkStatusCounter.clone()
        o.MonitorSynchronizationStatusCounter = self.MonitorSynchronizationStatusCounter.clone()
        o.MonitorTransmissionStatusCounter = self.MonitorTransmissionStatusCounter.clone()
        o.MonitorConnectionStatusCounter = self.MonitorConnectionStatusCounter.clone()
        o.MonitorEssenceStatusCounter = self.MonitorEssenceStatusCounter.clone()
        o.MonitorStreamStatusCounter = self.MonitorStreamStatusCounter.clone()
        return o


class NMonitorState:
    """Optional object type: NMonitorState."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NMonitorStateValue = NMonitorStateValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NMonitorStateValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NMonitorStateValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NMonitorStateValue | None = None) -> NMonitorStateValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_MonitorOverallStatus(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MonitorOverallStatus

    def set_MonitorOverallStatus(self, v: Any) -> None:
        assert self._defined, "NMonitorState must be defined before setting MonitorOverallStatus"
        _assign_value(self._value.MonitorOverallStatus, v)

    def get_MonitorOverallStatusMessage(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MonitorOverallStatusMessage

    def set_MonitorOverallStatusMessage(self, v: Any) -> None:
        assert self._defined, "NMonitorState must be defined before setting MonitorOverallStatusMessage"
        _assign_value(self._value.MonitorOverallStatusMessage, v)

    def get_MonitorLinkStatus(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MonitorLinkStatus

    def set_MonitorLinkStatus(self, v: Any) -> None:
        assert self._defined, "NMonitorState must be defined before setting MonitorLinkStatus"
        _assign_value(self._value.MonitorLinkStatus, v)

    def get_MonitorSynchronizationStatus(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MonitorSynchronizationStatus

    def set_MonitorSynchronizationStatus(self, v: Any) -> None:
        assert self._defined, "NMonitorState must be defined before setting MonitorSynchronizationStatus"
        _assign_value(self._value.MonitorSynchronizationStatus, v)

    def get_MonitorTransmissionStatus(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MonitorTransmissionStatus

    def set_MonitorTransmissionStatus(self, v: Any) -> None:
        assert self._defined, "NMonitorState must be defined before setting MonitorTransmissionStatus"
        _assign_value(self._value.MonitorTransmissionStatus, v)

    def get_MonitorConnectionStatus(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MonitorConnectionStatus

    def set_MonitorConnectionStatus(self, v: Any) -> None:
        assert self._defined, "NMonitorState must be defined before setting MonitorConnectionStatus"
        _assign_value(self._value.MonitorConnectionStatus, v)

    def get_MonitorEssenceStatus(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MonitorEssenceStatus

    def set_MonitorEssenceStatus(self, v: Any) -> None:
        assert self._defined, "NMonitorState must be defined before setting MonitorEssenceStatus"
        _assign_value(self._value.MonitorEssenceStatus, v)

    def get_MonitorStreamStatus(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MonitorStreamStatus

    def set_MonitorStreamStatus(self, v: Any) -> None:
        assert self._defined, "NMonitorState must be defined before setting MonitorStreamStatus"
        _assign_value(self._value.MonitorStreamStatus, v)

    def get_MonitorLinkStatusCounter(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MonitorLinkStatusCounter

    def set_MonitorLinkStatusCounter(self, v: Any) -> None:
        assert self._defined, "NMonitorState must be defined before setting MonitorLinkStatusCounter"
        _assign_value(self._value.MonitorLinkStatusCounter, v)

    def get_MonitorSynchronizationStatusCounter(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MonitorSynchronizationStatusCounter

    def set_MonitorSynchronizationStatusCounter(self, v: Any) -> None:
        assert self._defined, "NMonitorState must be defined before setting MonitorSynchronizationStatusCounter"
        _assign_value(self._value.MonitorSynchronizationStatusCounter, v)

    def get_MonitorTransmissionStatusCounter(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MonitorTransmissionStatusCounter

    def set_MonitorTransmissionStatusCounter(self, v: Any) -> None:
        assert self._defined, "NMonitorState must be defined before setting MonitorTransmissionStatusCounter"
        _assign_value(self._value.MonitorTransmissionStatusCounter, v)

    def get_MonitorConnectionStatusCounter(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MonitorConnectionStatusCounter

    def set_MonitorConnectionStatusCounter(self, v: Any) -> None:
        assert self._defined, "NMonitorState must be defined before setting MonitorConnectionStatusCounter"
        _assign_value(self._value.MonitorConnectionStatusCounter, v)

    def get_MonitorEssenceStatusCounter(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MonitorEssenceStatusCounter

    def set_MonitorEssenceStatusCounter(self, v: Any) -> None:
        assert self._defined, "NMonitorState must be defined before setting MonitorEssenceStatusCounter"
        _assign_value(self._value.MonitorEssenceStatusCounter, v)

    def get_MonitorStreamStatusCounter(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MonitorStreamStatusCounter

    def set_MonitorStreamStatusCounter(self, v: Any) -> None:
        assert self._defined, "NMonitorState must be defined before setting MonitorStreamStatusCounter"
        _assign_value(self._value.MonitorStreamStatusCounter, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NMonitorStateValue()

    def clone(self) -> NMonitorState:
        o = NMonitorState()
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
            return f"NMonitorState(defined)"
        return "NMonitorState(<undefined>)"


def make_nmonitorstate_value(v: NMonitorStateValue) -> NMonitorStateValue:
    """Factory: create a NMonitorStateValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nmonitorstate(v: NMonitorStateValue) -> NMonitorState:
    """Factory: create a defined NMonitorState from a NMonitorStateValue."""
    o = NMonitorState()
    o.set_value(v)
    return o

