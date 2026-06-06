"""Generated NMOS type: NcReceiverMonitor. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NInt, NNullString, NBool, NTime
from nmos.types.generated.nc_status_monitor import NcStatusMonitor, NcStatusMonitorValue

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcReceiverMonitorEnums:
    """JSON property name enums for NcReceiverMonitor."""
    LinkStatus = EnumRegistry.get("linkStatus")
    LinkStatusMessage = EnumRegistry.get("linkStatusMessage")
    LinkStatusTransitionCounter = EnumRegistry.get("linkStatusTransitionCounter")
    ConnectionStatus = EnumRegistry.get("connectionStatus")
    ConnectionStatusMessage = EnumRegistry.get("connectionStatusMessage")
    ConnectionStatusTransitionCounter = EnumRegistry.get("connectionStatusTransitionCounter")
    ExternalSynchronizationStatus = EnumRegistry.get("externalSynchronizationStatus")
    ExternalSynchronizationStatusMessage = EnumRegistry.get("externalSynchronizationStatusMessage")
    ExternalSynchronizationStatusTransitionCounter = EnumRegistry.get("externalSynchronizationStatusTransitionCounter")
    StreamStatus = EnumRegistry.get("streamStatus")
    StreamStatusMessage = EnumRegistry.get("streamStatusMessage")
    StreamStatusTransitionCounter = EnumRegistry.get("streamStatusTransitionCounter")
    SynchronizationSourceId = EnumRegistry.get("synchronizationSourceId")
    AutoResetCountersAndMessages = EnumRegistry.get("autoResetCountersAndMessages")
    pass


class NcReceiverMonitorValue:
    """Inner value struct for NcReceiverMonitor."""

    __slots__ = (
        "Base",
        "LinkStatus",
        "LinkStatusMessage",
        "LinkStatusTransitionCounter",
        "ConnectionStatus",
        "ConnectionStatusMessage",
        "ConnectionStatusTransitionCounter",
        "ExternalSynchronizationStatus",
        "ExternalSynchronizationStatusMessage",
        "ExternalSynchronizationStatusTransitionCounter",
        "StreamStatus",
        "StreamStatusMessage",
        "StreamStatusTransitionCounter",
        "SynchronizationSourceId",
        "AutoResetCountersAndMessages",
        "InternalLinkStatus",
        "InternalConnectionStatus",
        "InternalExternalSynchronizationStatus",
        "InternalStreamStatus",
        "InternalLinkStatusTime",
        "InternalConnectionStatusTime",
        "InternalExternalSynchronizationStatusTime",
        "InternalStreamStatusTime",
    )

    def __init__(self) -> None:
        self.Base: NcStatusMonitorValue = NcStatusMonitorValue()
        self.LinkStatus: NInt = NInt()
        self.LinkStatusMessage: NNullString = NNullString()
        self.LinkStatusTransitionCounter: NInt = NInt()
        self.ConnectionStatus: NInt = NInt()
        self.ConnectionStatusMessage: NNullString = NNullString()
        self.ConnectionStatusTransitionCounter: NInt = NInt()
        self.ExternalSynchronizationStatus: NInt = NInt()
        self.ExternalSynchronizationStatusMessage: NNullString = NNullString()
        self.ExternalSynchronizationStatusTransitionCounter: NInt = NInt()
        self.StreamStatus: NInt = NInt()
        self.StreamStatusMessage: NNullString = NNullString()
        self.StreamStatusTransitionCounter: NInt = NInt()
        self.SynchronizationSourceId: NNullString = NNullString()
        self.AutoResetCountersAndMessages: NBool = NBool()
        self.InternalLinkStatus: NInt = NInt()
        self.InternalConnectionStatus: NInt = NInt()
        self.InternalExternalSynchronizationStatus: NInt = NInt()
        self.InternalStreamStatus: NInt = NInt()
        self.InternalLinkStatusTime: NTime = NTime()
        self.InternalConnectionStatusTime: NTime = NTime()
        self.InternalExternalSynchronizationStatusTime: NTime = NTime()
        self.InternalStreamStatusTime: NTime = NTime()

    def set_to_default(self) -> None:
        self.Base = NcStatusMonitorValue()
        self.Base.set_to_default()
        self.LinkStatus.set_to_default()
        self.LinkStatusMessage.set_to_default()
        self.LinkStatusTransitionCounter.set_to_default()
        self.ConnectionStatus.set_to_default()
        self.ConnectionStatusMessage.set_to_default()
        self.ConnectionStatusTransitionCounter.set_to_default()
        self.ExternalSynchronizationStatus.set_to_default()
        self.ExternalSynchronizationStatusMessage.set_to_default()
        self.ExternalSynchronizationStatusTransitionCounter.set_to_default()
        self.StreamStatus.set_to_default()
        self.StreamStatusMessage.set_to_default()
        self.StreamStatusTransitionCounter.set_to_default()
        self.SynchronizationSourceId.set_to_default()
        _assign_value(self.AutoResetCountersAndMessages, True)
        self.InternalLinkStatus.set_to_default()
        self.InternalConnectionStatus.set_to_default()
        self.InternalExternalSynchronizationStatus.set_to_default()
        self.InternalStreamStatus.set_to_default()
        self.InternalLinkStatusTime.set_to_default()
        self.InternalConnectionStatusTime.set_to_default()
        self.InternalExternalSynchronizationStatusTime.set_to_default()
        self.InternalStreamStatusTime.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.LinkStatus.defined:
            raise InvalidObject("missing required member LinkStatus")
        if not self.LinkStatusMessage.defined:
            raise InvalidObject("missing required member LinkStatusMessage")
        if not self.LinkStatusTransitionCounter.defined:
            raise InvalidObject("missing required member LinkStatusTransitionCounter")
        if not self.ConnectionStatus.defined:
            raise InvalidObject("missing required member ConnectionStatus")
        if not self.ConnectionStatusMessage.defined:
            raise InvalidObject("missing required member ConnectionStatusMessage")
        if not self.ConnectionStatusTransitionCounter.defined:
            raise InvalidObject("missing required member ConnectionStatusTransitionCounter")
        if not self.ExternalSynchronizationStatus.defined:
            raise InvalidObject("missing required member ExternalSynchronizationStatus")
        if not self.ExternalSynchronizationStatusMessage.defined:
            raise InvalidObject("missing required member ExternalSynchronizationStatusMessage")
        if not self.ExternalSynchronizationStatusTransitionCounter.defined:
            raise InvalidObject("missing required member ExternalSynchronizationStatusTransitionCounter")
        if not self.StreamStatus.defined:
            raise InvalidObject("missing required member StreamStatus")
        if not self.StreamStatusMessage.defined:
            raise InvalidObject("missing required member StreamStatusMessage")
        if not self.StreamStatusTransitionCounter.defined:
            raise InvalidObject("missing required member StreamStatusTransitionCounter")
        if not self.SynchronizationSourceId.defined:
            raise InvalidObject("missing required member SynchronizationSourceId")
        if not self.AutoResetCountersAndMessages.defined:
            raise InvalidObject("missing required member AutoResetCountersAndMessages")
        self.Base.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        self.Base.encode(engine, None)
        self.LinkStatus.encode(engine, NcReceiverMonitorEnums.LinkStatus)
        self.LinkStatusMessage.encode(engine, NcReceiverMonitorEnums.LinkStatusMessage)
        self.LinkStatusTransitionCounter.encode(engine, NcReceiverMonitorEnums.LinkStatusTransitionCounter)
        self.ConnectionStatus.encode(engine, NcReceiverMonitorEnums.ConnectionStatus)
        self.ConnectionStatusMessage.encode(engine, NcReceiverMonitorEnums.ConnectionStatusMessage)
        self.ConnectionStatusTransitionCounter.encode(engine, NcReceiverMonitorEnums.ConnectionStatusTransitionCounter)
        self.ExternalSynchronizationStatus.encode(engine, NcReceiverMonitorEnums.ExternalSynchronizationStatus)
        self.ExternalSynchronizationStatusMessage.encode(engine, NcReceiverMonitorEnums.ExternalSynchronizationStatusMessage)
        self.ExternalSynchronizationStatusTransitionCounter.encode(engine, NcReceiverMonitorEnums.ExternalSynchronizationStatusTransitionCounter)
        self.StreamStatus.encode(engine, NcReceiverMonitorEnums.StreamStatus)
        self.StreamStatusMessage.encode(engine, NcReceiverMonitorEnums.StreamStatusMessage)
        self.StreamStatusTransitionCounter.encode(engine, NcReceiverMonitorEnums.StreamStatusTransitionCounter)
        self.SynchronizationSourceId.encode(engine, NcReceiverMonitorEnums.SynchronizationSourceId)
        self.AutoResetCountersAndMessages.encode(engine, NcReceiverMonitorEnums.AutoResetCountersAndMessages)

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcReceiverMonitor")

        self.Base.decode(engine, data)
        if NcReceiverMonitorEnums.LinkStatus.s in data:
            self.LinkStatus.decode_value(data[NcReceiverMonitorEnums.LinkStatus.s])
        if NcReceiverMonitorEnums.LinkStatusMessage.s in data:
            self.LinkStatusMessage.decode_value(data[NcReceiverMonitorEnums.LinkStatusMessage.s])
        if NcReceiverMonitorEnums.LinkStatusTransitionCounter.s in data:
            self.LinkStatusTransitionCounter.decode_value(data[NcReceiverMonitorEnums.LinkStatusTransitionCounter.s])
        if NcReceiverMonitorEnums.ConnectionStatus.s in data:
            self.ConnectionStatus.decode_value(data[NcReceiverMonitorEnums.ConnectionStatus.s])
        if NcReceiverMonitorEnums.ConnectionStatusMessage.s in data:
            self.ConnectionStatusMessage.decode_value(data[NcReceiverMonitorEnums.ConnectionStatusMessage.s])
        if NcReceiverMonitorEnums.ConnectionStatusTransitionCounter.s in data:
            self.ConnectionStatusTransitionCounter.decode_value(data[NcReceiverMonitorEnums.ConnectionStatusTransitionCounter.s])
        if NcReceiverMonitorEnums.ExternalSynchronizationStatus.s in data:
            self.ExternalSynchronizationStatus.decode_value(data[NcReceiverMonitorEnums.ExternalSynchronizationStatus.s])
        if NcReceiverMonitorEnums.ExternalSynchronizationStatusMessage.s in data:
            self.ExternalSynchronizationStatusMessage.decode_value(data[NcReceiverMonitorEnums.ExternalSynchronizationStatusMessage.s])
        if NcReceiverMonitorEnums.ExternalSynchronizationStatusTransitionCounter.s in data:
            self.ExternalSynchronizationStatusTransitionCounter.decode_value(data[NcReceiverMonitorEnums.ExternalSynchronizationStatusTransitionCounter.s])
        if NcReceiverMonitorEnums.StreamStatus.s in data:
            self.StreamStatus.decode_value(data[NcReceiverMonitorEnums.StreamStatus.s])
        if NcReceiverMonitorEnums.StreamStatusMessage.s in data:
            self.StreamStatusMessage.decode_value(data[NcReceiverMonitorEnums.StreamStatusMessage.s])
        if NcReceiverMonitorEnums.StreamStatusTransitionCounter.s in data:
            self.StreamStatusTransitionCounter.decode_value(data[NcReceiverMonitorEnums.StreamStatusTransitionCounter.s])
        if NcReceiverMonitorEnums.SynchronizationSourceId.s in data:
            self.SynchronizationSourceId.decode_value(data[NcReceiverMonitorEnums.SynchronizationSourceId.s])
        if NcReceiverMonitorEnums.AutoResetCountersAndMessages.s in data:
            self.AutoResetCountersAndMessages.decode_value(data[NcReceiverMonitorEnums.AutoResetCountersAndMessages.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcReceiverMonitorValue:
        o = NcReceiverMonitorValue()
        o.Base = self.Base.clone()
        o.LinkStatus = self.LinkStatus.clone()
        o.LinkStatusMessage = self.LinkStatusMessage.clone()
        o.LinkStatusTransitionCounter = self.LinkStatusTransitionCounter.clone()
        o.ConnectionStatus = self.ConnectionStatus.clone()
        o.ConnectionStatusMessage = self.ConnectionStatusMessage.clone()
        o.ConnectionStatusTransitionCounter = self.ConnectionStatusTransitionCounter.clone()
        o.ExternalSynchronizationStatus = self.ExternalSynchronizationStatus.clone()
        o.ExternalSynchronizationStatusMessage = self.ExternalSynchronizationStatusMessage.clone()
        o.ExternalSynchronizationStatusTransitionCounter = self.ExternalSynchronizationStatusTransitionCounter.clone()
        o.StreamStatus = self.StreamStatus.clone()
        o.StreamStatusMessage = self.StreamStatusMessage.clone()
        o.StreamStatusTransitionCounter = self.StreamStatusTransitionCounter.clone()
        o.SynchronizationSourceId = self.SynchronizationSourceId.clone()
        o.AutoResetCountersAndMessages = self.AutoResetCountersAndMessages.clone()
        o.InternalLinkStatus = self.InternalLinkStatus.clone()
        o.InternalConnectionStatus = self.InternalConnectionStatus.clone()
        o.InternalExternalSynchronizationStatus = self.InternalExternalSynchronizationStatus.clone()
        o.InternalStreamStatus = self.InternalStreamStatus.clone()
        o.InternalLinkStatusTime = self.InternalLinkStatusTime.clone()
        o.InternalConnectionStatusTime = self.InternalConnectionStatusTime.clone()
        o.InternalExternalSynchronizationStatusTime = self.InternalExternalSynchronizationStatusTime.clone()
        o.InternalStreamStatusTime = self.InternalStreamStatusTime.clone()
        return o


class NcReceiverMonitor:
    """Optional object type: NcReceiverMonitor."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcReceiverMonitorValue = NcReceiverMonitorValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcReceiverMonitorValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcReceiverMonitorValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcReceiverMonitorValue | None = None) -> NcReceiverMonitorValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Base(self) -> NcStatusMonitorValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Base

    def set_Base(self, v: NcStatusMonitorValue) -> None:
        assert self._defined, "NcReceiverMonitor must be defined before setting Base"
        self._value.Base = v.clone()  # copy to match Go's value semantics

    def get_LinkStatus(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.LinkStatus

    def set_LinkStatus(self, v: Any) -> None:
        assert self._defined, "NcReceiverMonitor must be defined before setting LinkStatus"
        _assign_value(self._value.LinkStatus, v)

    def get_LinkStatusMessage(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.LinkStatusMessage

    def set_LinkStatusMessage(self, v: Any) -> None:
        assert self._defined, "NcReceiverMonitor must be defined before setting LinkStatusMessage"
        _assign_value(self._value.LinkStatusMessage, v)

    def get_LinkStatusTransitionCounter(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.LinkStatusTransitionCounter

    def set_LinkStatusTransitionCounter(self, v: Any) -> None:
        assert self._defined, "NcReceiverMonitor must be defined before setting LinkStatusTransitionCounter"
        _assign_value(self._value.LinkStatusTransitionCounter, v)

    def get_ConnectionStatus(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ConnectionStatus

    def set_ConnectionStatus(self, v: Any) -> None:
        assert self._defined, "NcReceiverMonitor must be defined before setting ConnectionStatus"
        _assign_value(self._value.ConnectionStatus, v)

    def get_ConnectionStatusMessage(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ConnectionStatusMessage

    def set_ConnectionStatusMessage(self, v: Any) -> None:
        assert self._defined, "NcReceiverMonitor must be defined before setting ConnectionStatusMessage"
        _assign_value(self._value.ConnectionStatusMessage, v)

    def get_ConnectionStatusTransitionCounter(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ConnectionStatusTransitionCounter

    def set_ConnectionStatusTransitionCounter(self, v: Any) -> None:
        assert self._defined, "NcReceiverMonitor must be defined before setting ConnectionStatusTransitionCounter"
        _assign_value(self._value.ConnectionStatusTransitionCounter, v)

    def get_ExternalSynchronizationStatus(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExternalSynchronizationStatus

    def set_ExternalSynchronizationStatus(self, v: Any) -> None:
        assert self._defined, "NcReceiverMonitor must be defined before setting ExternalSynchronizationStatus"
        _assign_value(self._value.ExternalSynchronizationStatus, v)

    def get_ExternalSynchronizationStatusMessage(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExternalSynchronizationStatusMessage

    def set_ExternalSynchronizationStatusMessage(self, v: Any) -> None:
        assert self._defined, "NcReceiverMonitor must be defined before setting ExternalSynchronizationStatusMessage"
        _assign_value(self._value.ExternalSynchronizationStatusMessage, v)

    def get_ExternalSynchronizationStatusTransitionCounter(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExternalSynchronizationStatusTransitionCounter

    def set_ExternalSynchronizationStatusTransitionCounter(self, v: Any) -> None:
        assert self._defined, "NcReceiverMonitor must be defined before setting ExternalSynchronizationStatusTransitionCounter"
        _assign_value(self._value.ExternalSynchronizationStatusTransitionCounter, v)

    def get_StreamStatus(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.StreamStatus

    def set_StreamStatus(self, v: Any) -> None:
        assert self._defined, "NcReceiverMonitor must be defined before setting StreamStatus"
        _assign_value(self._value.StreamStatus, v)

    def get_StreamStatusMessage(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.StreamStatusMessage

    def set_StreamStatusMessage(self, v: Any) -> None:
        assert self._defined, "NcReceiverMonitor must be defined before setting StreamStatusMessage"
        _assign_value(self._value.StreamStatusMessage, v)

    def get_StreamStatusTransitionCounter(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.StreamStatusTransitionCounter

    def set_StreamStatusTransitionCounter(self, v: Any) -> None:
        assert self._defined, "NcReceiverMonitor must be defined before setting StreamStatusTransitionCounter"
        _assign_value(self._value.StreamStatusTransitionCounter, v)

    def get_SynchronizationSourceId(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SynchronizationSourceId

    def set_SynchronizationSourceId(self, v: Any) -> None:
        assert self._defined, "NcReceiverMonitor must be defined before setting SynchronizationSourceId"
        _assign_value(self._value.SynchronizationSourceId, v)

    def get_AutoResetCountersAndMessages(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.AutoResetCountersAndMessages

    def set_AutoResetCountersAndMessages(self, v: Any) -> None:
        assert self._defined, "NcReceiverMonitor must be defined before setting AutoResetCountersAndMessages"
        _assign_value(self._value.AutoResetCountersAndMessages, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcReceiverMonitorValue()

    def clone(self) -> NcReceiverMonitor:
        o = NcReceiverMonitor()
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
            return f"NcReceiverMonitor(defined)"
        return "NcReceiverMonitor(<undefined>)"


def make_ncreceivermonitor_value(v: NcReceiverMonitorValue) -> NcReceiverMonitorValue:
    """Factory: create a NcReceiverMonitorValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ncreceivermonitor(v: NcReceiverMonitorValue) -> NcReceiverMonitor:
    """Factory: create a defined NcReceiverMonitor from a NcReceiverMonitorValue."""
    o = NcReceiverMonitor()
    o.set_value(v)
    return o

