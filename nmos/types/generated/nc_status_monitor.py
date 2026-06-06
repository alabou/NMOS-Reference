"""Generated NMOS type: NcStatusMonitor. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NInt, NNullString
from nmos.types.generated.nc_worker import NcWorker, NcWorkerValue

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcStatusMonitorEnums:
    """JSON property name enums for NcStatusMonitor."""
    OverallStatus = EnumRegistry.get("overallStatus")
    OverallStatusMessage = EnumRegistry.get("overallStatusMessage")
    StatusReportingDelay = EnumRegistry.get("statusReportingDelay")
    pass


class NcStatusMonitorValue:
    """Inner value struct for NcStatusMonitor."""

    __slots__ = (
        "Base",
        "OverallStatus",
        "OverallStatusMessage",
        "StatusReportingDelay",
    )

    def __init__(self) -> None:
        self.Base: NcWorkerValue = NcWorkerValue()
        self.OverallStatus: NInt = NInt()
        self.OverallStatusMessage: NNullString = NNullString()
        self.StatusReportingDelay: NInt = NInt()

    def set_to_default(self) -> None:
        self.Base = NcWorkerValue()
        self.Base.set_to_default()
        self.OverallStatus.set_to_default()
        self.OverallStatusMessage.set_to_default()
        self.StatusReportingDelay.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.OverallStatus.defined:
            raise InvalidObject("missing required member OverallStatus")
        if not self.OverallStatusMessage.defined:
            raise InvalidObject("missing required member OverallStatusMessage")
        if not self.StatusReportingDelay.defined:
            raise InvalidObject("missing required member StatusReportingDelay")
        self.Base.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        self.Base.encode(engine, None)
        self.OverallStatus.encode(engine, NcStatusMonitorEnums.OverallStatus)
        self.OverallStatusMessage.encode(engine, NcStatusMonitorEnums.OverallStatusMessage)
        self.StatusReportingDelay.encode(engine, NcStatusMonitorEnums.StatusReportingDelay)

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcStatusMonitor")

        self.Base.decode(engine, data)
        if NcStatusMonitorEnums.OverallStatus.s in data:
            self.OverallStatus.decode_value(data[NcStatusMonitorEnums.OverallStatus.s])
        if NcStatusMonitorEnums.OverallStatusMessage.s in data:
            self.OverallStatusMessage.decode_value(data[NcStatusMonitorEnums.OverallStatusMessage.s])
        if NcStatusMonitorEnums.StatusReportingDelay.s in data:
            self.StatusReportingDelay.decode_value(data[NcStatusMonitorEnums.StatusReportingDelay.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcStatusMonitorValue:
        o = NcStatusMonitorValue()
        o.Base = self.Base.clone()
        o.OverallStatus = self.OverallStatus.clone()
        o.OverallStatusMessage = self.OverallStatusMessage.clone()
        o.StatusReportingDelay = self.StatusReportingDelay.clone()
        return o


class NcStatusMonitor:
    """Optional object type: NcStatusMonitor."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcStatusMonitorValue = NcStatusMonitorValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcStatusMonitorValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcStatusMonitorValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcStatusMonitorValue | None = None) -> NcStatusMonitorValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Base(self) -> NcWorkerValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Base

    def set_Base(self, v: NcWorkerValue) -> None:
        assert self._defined, "NcStatusMonitor must be defined before setting Base"
        self._value.Base = v.clone()  # copy to match Go's value semantics

    def get_OverallStatus(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.OverallStatus

    def set_OverallStatus(self, v: Any) -> None:
        assert self._defined, "NcStatusMonitor must be defined before setting OverallStatus"
        _assign_value(self._value.OverallStatus, v)

    def get_OverallStatusMessage(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.OverallStatusMessage

    def set_OverallStatusMessage(self, v: Any) -> None:
        assert self._defined, "NcStatusMonitor must be defined before setting OverallStatusMessage"
        _assign_value(self._value.OverallStatusMessage, v)

    def get_StatusReportingDelay(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.StatusReportingDelay

    def set_StatusReportingDelay(self, v: Any) -> None:
        assert self._defined, "NcStatusMonitor must be defined before setting StatusReportingDelay"
        _assign_value(self._value.StatusReportingDelay, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcStatusMonitorValue()

    def clone(self) -> NcStatusMonitor:
        o = NcStatusMonitor()
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
            return f"NcStatusMonitor(defined)"
        return "NcStatusMonitor(<undefined>)"


def make_ncstatusmonitor_value(v: NcStatusMonitorValue) -> NcStatusMonitorValue:
    """Factory: create a NcStatusMonitorValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ncstatusmonitor(v: NcStatusMonitorValue) -> NcStatusMonitor:
    """Factory: create a defined NcStatusMonitor from a NcStatusMonitorValue."""
    o = NcStatusMonitor()
    o.set_value(v)
    return o

