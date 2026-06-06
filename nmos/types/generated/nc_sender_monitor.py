"""Generated NMOS type: NcSenderMonitor. DO NOT EDIT."""

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


class NcSenderMonitorEnums:
    """JSON property name enums for NcSenderMonitor."""
    LinkStatus = EnumRegistry.get("linkStatus")
    LinkStatusMessage = EnumRegistry.get("linkStatusMessage")
    LinkStatusTransitionCounter = EnumRegistry.get("linkStatusTransitionCounter")
    TransmissionStatus = EnumRegistry.get("transmissionStatus")
    TransmissionStatusMessage = EnumRegistry.get("transmissionStatusMessage")
    TransmissionStatusTransitionCounter = EnumRegistry.get("transmissionStatusTransitionCounter")
    ExternalSynchronizationStatus = EnumRegistry.get("externalSynchronizationStatus")
    ExternalSynchronizationStatusMessage = EnumRegistry.get("externalSynchronizationStatusMessage")
    ExternalSynchronizationStatusTransitionCounter = EnumRegistry.get("externalSynchronizationStatusTransitionCounter")
    EssenceStatus = EnumRegistry.get("essenceStatus")
    EssenceStatusMessage = EnumRegistry.get("essenceStatusMessage")
    EssenceStatusTransitionCounter = EnumRegistry.get("essenceStatusTransitionCounter")
    SynchronizationSourceId = EnumRegistry.get("synchronizationSourceId")
    AutoResetCountersAndMessages = EnumRegistry.get("autoResetCountersAndMessages")
    pass


class NcSenderMonitorValue:
    """Inner value struct for NcSenderMonitor."""

    __slots__ = (
        "Base",
        "LinkStatus",
        "LinkStatusMessage",
        "LinkStatusTransitionCounter",
        "TransmissionStatus",
        "TransmissionStatusMessage",
        "TransmissionStatusTransitionCounter",
        "ExternalSynchronizationStatus",
        "ExternalSynchronizationStatusMessage",
        "ExternalSynchronizationStatusTransitionCounter",
        "EssenceStatus",
        "EssenceStatusMessage",
        "EssenceStatusTransitionCounter",
        "SynchronizationSourceId",
        "AutoResetCountersAndMessages",
        "InternalLinkStatus",
        "InternalTransmissionStatus",
        "InternalExternalSynchronizationStatus",
        "InternalEssenceStatus",
        "InternalLinkStatusTime",
        "InternalTransmissionStatusTime",
        "InternalExternalSynchronizationStatusTime",
        "InternalEssenceStatusTime",
    )

    def __init__(self) -> None:
        self.Base: NcStatusMonitorValue = NcStatusMonitorValue()
        self.LinkStatus: NInt = NInt()
        self.LinkStatusMessage: NNullString = NNullString()
        self.LinkStatusTransitionCounter: NInt = NInt()
        self.TransmissionStatus: NInt = NInt()
        self.TransmissionStatusMessage: NNullString = NNullString()
        self.TransmissionStatusTransitionCounter: NInt = NInt()
        self.ExternalSynchronizationStatus: NInt = NInt()
        self.ExternalSynchronizationStatusMessage: NNullString = NNullString()
        self.ExternalSynchronizationStatusTransitionCounter: NInt = NInt()
        self.EssenceStatus: NInt = NInt()
        self.EssenceStatusMessage: NNullString = NNullString()
        self.EssenceStatusTransitionCounter: NInt = NInt()
        self.SynchronizationSourceId: NNullString = NNullString()
        self.AutoResetCountersAndMessages: NBool = NBool()
        self.InternalLinkStatus: NInt = NInt()
        self.InternalTransmissionStatus: NInt = NInt()
        self.InternalExternalSynchronizationStatus: NInt = NInt()
        self.InternalEssenceStatus: NInt = NInt()
        self.InternalLinkStatusTime: NTime = NTime()
        self.InternalTransmissionStatusTime: NTime = NTime()
        self.InternalExternalSynchronizationStatusTime: NTime = NTime()
        self.InternalEssenceStatusTime: NTime = NTime()

    def set_to_default(self) -> None:
        self.Base = NcStatusMonitorValue()
        self.Base.set_to_default()
        self.LinkStatus.set_to_default()
        self.LinkStatusMessage.set_to_default()
        self.LinkStatusTransitionCounter.set_to_default()
        self.TransmissionStatus.set_to_default()
        self.TransmissionStatusMessage.set_to_default()
        self.TransmissionStatusTransitionCounter.set_to_default()
        self.ExternalSynchronizationStatus.set_to_default()
        self.ExternalSynchronizationStatusMessage.set_to_default()
        self.ExternalSynchronizationStatusTransitionCounter.set_to_default()
        self.EssenceStatus.set_to_default()
        self.EssenceStatusMessage.set_to_default()
        self.EssenceStatusTransitionCounter.set_to_default()
        self.SynchronizationSourceId.set_to_default()
        _assign_value(self.AutoResetCountersAndMessages, True)
        self.InternalLinkStatus.set_to_default()
        self.InternalTransmissionStatus.set_to_default()
        self.InternalExternalSynchronizationStatus.set_to_default()
        self.InternalEssenceStatus.set_to_default()
        self.InternalLinkStatusTime.set_to_default()
        self.InternalTransmissionStatusTime.set_to_default()
        self.InternalExternalSynchronizationStatusTime.set_to_default()
        self.InternalEssenceStatusTime.set_to_default()
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
        if not self.TransmissionStatus.defined:
            raise InvalidObject("missing required member TransmissionStatus")
        if not self.TransmissionStatusMessage.defined:
            raise InvalidObject("missing required member TransmissionStatusMessage")
        if not self.TransmissionStatusTransitionCounter.defined:
            raise InvalidObject("missing required member TransmissionStatusTransitionCounter")
        if not self.ExternalSynchronizationStatus.defined:
            raise InvalidObject("missing required member ExternalSynchronizationStatus")
        if not self.ExternalSynchronizationStatusMessage.defined:
            raise InvalidObject("missing required member ExternalSynchronizationStatusMessage")
        if not self.ExternalSynchronizationStatusTransitionCounter.defined:
            raise InvalidObject("missing required member ExternalSynchronizationStatusTransitionCounter")
        if not self.EssenceStatus.defined:
            raise InvalidObject("missing required member EssenceStatus")
        if not self.EssenceStatusMessage.defined:
            raise InvalidObject("missing required member EssenceStatusMessage")
        if not self.EssenceStatusTransitionCounter.defined:
            raise InvalidObject("missing required member EssenceStatusTransitionCounter")
        if not self.SynchronizationSourceId.defined:
            raise InvalidObject("missing required member SynchronizationSourceId")
        if not self.AutoResetCountersAndMessages.defined:
            raise InvalidObject("missing required member AutoResetCountersAndMessages")
        self.Base.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        self.Base.encode(engine, None)
        self.LinkStatus.encode(engine, NcSenderMonitorEnums.LinkStatus)
        self.LinkStatusMessage.encode(engine, NcSenderMonitorEnums.LinkStatusMessage)
        self.LinkStatusTransitionCounter.encode(engine, NcSenderMonitorEnums.LinkStatusTransitionCounter)
        self.TransmissionStatus.encode(engine, NcSenderMonitorEnums.TransmissionStatus)
        self.TransmissionStatusMessage.encode(engine, NcSenderMonitorEnums.TransmissionStatusMessage)
        self.TransmissionStatusTransitionCounter.encode(engine, NcSenderMonitorEnums.TransmissionStatusTransitionCounter)
        self.ExternalSynchronizationStatus.encode(engine, NcSenderMonitorEnums.ExternalSynchronizationStatus)
        self.ExternalSynchronizationStatusMessage.encode(engine, NcSenderMonitorEnums.ExternalSynchronizationStatusMessage)
        self.ExternalSynchronizationStatusTransitionCounter.encode(engine, NcSenderMonitorEnums.ExternalSynchronizationStatusTransitionCounter)
        self.EssenceStatus.encode(engine, NcSenderMonitorEnums.EssenceStatus)
        self.EssenceStatusMessage.encode(engine, NcSenderMonitorEnums.EssenceStatusMessage)
        self.EssenceStatusTransitionCounter.encode(engine, NcSenderMonitorEnums.EssenceStatusTransitionCounter)
        self.SynchronizationSourceId.encode(engine, NcSenderMonitorEnums.SynchronizationSourceId)
        self.AutoResetCountersAndMessages.encode(engine, NcSenderMonitorEnums.AutoResetCountersAndMessages)

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcSenderMonitor")

        self.Base.decode(engine, data)
        if NcSenderMonitorEnums.LinkStatus.s in data:
            self.LinkStatus.decode_value(data[NcSenderMonitorEnums.LinkStatus.s])
        if NcSenderMonitorEnums.LinkStatusMessage.s in data:
            self.LinkStatusMessage.decode_value(data[NcSenderMonitorEnums.LinkStatusMessage.s])
        if NcSenderMonitorEnums.LinkStatusTransitionCounter.s in data:
            self.LinkStatusTransitionCounter.decode_value(data[NcSenderMonitorEnums.LinkStatusTransitionCounter.s])
        if NcSenderMonitorEnums.TransmissionStatus.s in data:
            self.TransmissionStatus.decode_value(data[NcSenderMonitorEnums.TransmissionStatus.s])
        if NcSenderMonitorEnums.TransmissionStatusMessage.s in data:
            self.TransmissionStatusMessage.decode_value(data[NcSenderMonitorEnums.TransmissionStatusMessage.s])
        if NcSenderMonitorEnums.TransmissionStatusTransitionCounter.s in data:
            self.TransmissionStatusTransitionCounter.decode_value(data[NcSenderMonitorEnums.TransmissionStatusTransitionCounter.s])
        if NcSenderMonitorEnums.ExternalSynchronizationStatus.s in data:
            self.ExternalSynchronizationStatus.decode_value(data[NcSenderMonitorEnums.ExternalSynchronizationStatus.s])
        if NcSenderMonitorEnums.ExternalSynchronizationStatusMessage.s in data:
            self.ExternalSynchronizationStatusMessage.decode_value(data[NcSenderMonitorEnums.ExternalSynchronizationStatusMessage.s])
        if NcSenderMonitorEnums.ExternalSynchronizationStatusTransitionCounter.s in data:
            self.ExternalSynchronizationStatusTransitionCounter.decode_value(data[NcSenderMonitorEnums.ExternalSynchronizationStatusTransitionCounter.s])
        if NcSenderMonitorEnums.EssenceStatus.s in data:
            self.EssenceStatus.decode_value(data[NcSenderMonitorEnums.EssenceStatus.s])
        if NcSenderMonitorEnums.EssenceStatusMessage.s in data:
            self.EssenceStatusMessage.decode_value(data[NcSenderMonitorEnums.EssenceStatusMessage.s])
        if NcSenderMonitorEnums.EssenceStatusTransitionCounter.s in data:
            self.EssenceStatusTransitionCounter.decode_value(data[NcSenderMonitorEnums.EssenceStatusTransitionCounter.s])
        if NcSenderMonitorEnums.SynchronizationSourceId.s in data:
            self.SynchronizationSourceId.decode_value(data[NcSenderMonitorEnums.SynchronizationSourceId.s])
        if NcSenderMonitorEnums.AutoResetCountersAndMessages.s in data:
            self.AutoResetCountersAndMessages.decode_value(data[NcSenderMonitorEnums.AutoResetCountersAndMessages.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcSenderMonitorValue:
        o = NcSenderMonitorValue()
        o.Base = self.Base.clone()
        o.LinkStatus = self.LinkStatus.clone()
        o.LinkStatusMessage = self.LinkStatusMessage.clone()
        o.LinkStatusTransitionCounter = self.LinkStatusTransitionCounter.clone()
        o.TransmissionStatus = self.TransmissionStatus.clone()
        o.TransmissionStatusMessage = self.TransmissionStatusMessage.clone()
        o.TransmissionStatusTransitionCounter = self.TransmissionStatusTransitionCounter.clone()
        o.ExternalSynchronizationStatus = self.ExternalSynchronizationStatus.clone()
        o.ExternalSynchronizationStatusMessage = self.ExternalSynchronizationStatusMessage.clone()
        o.ExternalSynchronizationStatusTransitionCounter = self.ExternalSynchronizationStatusTransitionCounter.clone()
        o.EssenceStatus = self.EssenceStatus.clone()
        o.EssenceStatusMessage = self.EssenceStatusMessage.clone()
        o.EssenceStatusTransitionCounter = self.EssenceStatusTransitionCounter.clone()
        o.SynchronizationSourceId = self.SynchronizationSourceId.clone()
        o.AutoResetCountersAndMessages = self.AutoResetCountersAndMessages.clone()
        o.InternalLinkStatus = self.InternalLinkStatus.clone()
        o.InternalTransmissionStatus = self.InternalTransmissionStatus.clone()
        o.InternalExternalSynchronizationStatus = self.InternalExternalSynchronizationStatus.clone()
        o.InternalEssenceStatus = self.InternalEssenceStatus.clone()
        o.InternalLinkStatusTime = self.InternalLinkStatusTime.clone()
        o.InternalTransmissionStatusTime = self.InternalTransmissionStatusTime.clone()
        o.InternalExternalSynchronizationStatusTime = self.InternalExternalSynchronizationStatusTime.clone()
        o.InternalEssenceStatusTime = self.InternalEssenceStatusTime.clone()
        return o


class NcSenderMonitor:
    """Optional object type: NcSenderMonitor."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcSenderMonitorValue = NcSenderMonitorValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcSenderMonitorValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcSenderMonitorValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcSenderMonitorValue | None = None) -> NcSenderMonitorValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Base(self) -> NcStatusMonitorValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Base

    def set_Base(self, v: NcStatusMonitorValue) -> None:
        assert self._defined, "NcSenderMonitor must be defined before setting Base"
        self._value.Base = v.clone()  # copy to match Go's value semantics

    def get_LinkStatus(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.LinkStatus

    def set_LinkStatus(self, v: Any) -> None:
        assert self._defined, "NcSenderMonitor must be defined before setting LinkStatus"
        _assign_value(self._value.LinkStatus, v)

    def get_LinkStatusMessage(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.LinkStatusMessage

    def set_LinkStatusMessage(self, v: Any) -> None:
        assert self._defined, "NcSenderMonitor must be defined before setting LinkStatusMessage"
        _assign_value(self._value.LinkStatusMessage, v)

    def get_LinkStatusTransitionCounter(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.LinkStatusTransitionCounter

    def set_LinkStatusTransitionCounter(self, v: Any) -> None:
        assert self._defined, "NcSenderMonitor must be defined before setting LinkStatusTransitionCounter"
        _assign_value(self._value.LinkStatusTransitionCounter, v)

    def get_TransmissionStatus(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.TransmissionStatus

    def set_TransmissionStatus(self, v: Any) -> None:
        assert self._defined, "NcSenderMonitor must be defined before setting TransmissionStatus"
        _assign_value(self._value.TransmissionStatus, v)

    def get_TransmissionStatusMessage(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.TransmissionStatusMessage

    def set_TransmissionStatusMessage(self, v: Any) -> None:
        assert self._defined, "NcSenderMonitor must be defined before setting TransmissionStatusMessage"
        _assign_value(self._value.TransmissionStatusMessage, v)

    def get_TransmissionStatusTransitionCounter(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.TransmissionStatusTransitionCounter

    def set_TransmissionStatusTransitionCounter(self, v: Any) -> None:
        assert self._defined, "NcSenderMonitor must be defined before setting TransmissionStatusTransitionCounter"
        _assign_value(self._value.TransmissionStatusTransitionCounter, v)

    def get_ExternalSynchronizationStatus(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExternalSynchronizationStatus

    def set_ExternalSynchronizationStatus(self, v: Any) -> None:
        assert self._defined, "NcSenderMonitor must be defined before setting ExternalSynchronizationStatus"
        _assign_value(self._value.ExternalSynchronizationStatus, v)

    def get_ExternalSynchronizationStatusMessage(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExternalSynchronizationStatusMessage

    def set_ExternalSynchronizationStatusMessage(self, v: Any) -> None:
        assert self._defined, "NcSenderMonitor must be defined before setting ExternalSynchronizationStatusMessage"
        _assign_value(self._value.ExternalSynchronizationStatusMessage, v)

    def get_ExternalSynchronizationStatusTransitionCounter(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ExternalSynchronizationStatusTransitionCounter

    def set_ExternalSynchronizationStatusTransitionCounter(self, v: Any) -> None:
        assert self._defined, "NcSenderMonitor must be defined before setting ExternalSynchronizationStatusTransitionCounter"
        _assign_value(self._value.ExternalSynchronizationStatusTransitionCounter, v)

    def get_EssenceStatus(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.EssenceStatus

    def set_EssenceStatus(self, v: Any) -> None:
        assert self._defined, "NcSenderMonitor must be defined before setting EssenceStatus"
        _assign_value(self._value.EssenceStatus, v)

    def get_EssenceStatusMessage(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.EssenceStatusMessage

    def set_EssenceStatusMessage(self, v: Any) -> None:
        assert self._defined, "NcSenderMonitor must be defined before setting EssenceStatusMessage"
        _assign_value(self._value.EssenceStatusMessage, v)

    def get_EssenceStatusTransitionCounter(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.EssenceStatusTransitionCounter

    def set_EssenceStatusTransitionCounter(self, v: Any) -> None:
        assert self._defined, "NcSenderMonitor must be defined before setting EssenceStatusTransitionCounter"
        _assign_value(self._value.EssenceStatusTransitionCounter, v)

    def get_SynchronizationSourceId(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SynchronizationSourceId

    def set_SynchronizationSourceId(self, v: Any) -> None:
        assert self._defined, "NcSenderMonitor must be defined before setting SynchronizationSourceId"
        _assign_value(self._value.SynchronizationSourceId, v)

    def get_AutoResetCountersAndMessages(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.AutoResetCountersAndMessages

    def set_AutoResetCountersAndMessages(self, v: Any) -> None:
        assert self._defined, "NcSenderMonitor must be defined before setting AutoResetCountersAndMessages"
        _assign_value(self._value.AutoResetCountersAndMessages, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcSenderMonitorValue()

    def clone(self) -> NcSenderMonitor:
        o = NcSenderMonitor()
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
            return f"NcSenderMonitor(defined)"
        return "NcSenderMonitor(<undefined>)"


def make_ncsendermonitor_value(v: NcSenderMonitorValue) -> NcSenderMonitorValue:
    """Factory: create a NcSenderMonitorValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ncsendermonitor(v: NcSenderMonitorValue) -> NcSenderMonitor:
    """Factory: create a defined NcSenderMonitor from a NcSenderMonitorValue."""
    o = NcSenderMonitor()
    o.set_value(v)
    return o

