"""Generated NMOS type: NSourceData. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NEnum, NString, NBool, NInt
from nmos.types.generated.nsource_core import NSourceCore, NSourceCoreValue
from nmos.types.generated.nmonitor_state import NMonitorState, NMonitorStateValue
from nmos.validators import CheckFormat, CheckResourceIdString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NSourceDataEnums:
    """JSON property name enums for NSourceData."""
    Format = EnumRegistry.get("format")
    EventType = EnumRegistry.get("event_type")
    MonitorType = EnumRegistry.get("monitor_type")
    MonitorSiblingId = EnumRegistry.get("monitor_sibling_id")
    MonitorAutoResetCounters = EnumRegistry.get("monitor_auto_reset_counters")
    MonitorStatusReportingDelay = EnumRegistry.get("monitor_reporting_delay")
    MonitorState = EnumRegistry.get("monitor_state")
    pass


class NSourceDataValue:
    """Inner value struct for NSourceData."""

    __slots__ = (
        "SourceCore",
        "Format",
        "EventType",
        "MonitorType",
        "MonitorSiblingId",
        "MonitorAutoResetCounters",
        "MonitorStatusReportingDelay",
        "MonitorState",
    )

    def __init__(self) -> None:
        self.SourceCore: NSourceCoreValue = NSourceCoreValue()
        self.Format: NEnum = NEnum()
        self.EventType: NString = NString()
        self.MonitorType: NString = NString()
        self.MonitorSiblingId: NString = NString()
        self.MonitorAutoResetCounters: NBool = NBool()
        self.MonitorStatusReportingDelay: NInt = NInt()
        self.MonitorState: NMonitorState = NMonitorState()

    def set_to_default(self) -> None:
        self.SourceCore = NSourceCoreValue()
        self.SourceCore.set_to_default()
        self.Format.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Format.defined:
            raise InvalidObject("missing required member Format")
        if self.Format.defined:
            CheckFormat(self.Format)
        if self.MonitorSiblingId.defined:
            CheckResourceIdString(self.MonitorSiblingId)
        self.SourceCore.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.SourceCore.encode(engine, None)
        self.Format.encode(engine, NSourceDataEnums.Format)
        self.EventType.encode(engine, NSourceDataEnums.EventType)
        self.MonitorType.encode(engine, NSourceDataEnums.MonitorType)
        self.MonitorSiblingId.encode(engine, NSourceDataEnums.MonitorSiblingId)
        self.MonitorAutoResetCounters.encode(engine, NSourceDataEnums.MonitorAutoResetCounters)
        self.MonitorStatusReportingDelay.encode(engine, NSourceDataEnums.MonitorStatusReportingDelay)
        self.MonitorState.encode(engine, NSourceDataEnums.MonitorState)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NSourceData")

        self.SourceCore.decode(engine, data)
        if NSourceDataEnums.Format.s in data:
            self.Format.decode_value(data[NSourceDataEnums.Format.s])
        if NSourceDataEnums.EventType.s in data:
            self.EventType.decode_value(data[NSourceDataEnums.EventType.s])
        if NSourceDataEnums.MonitorType.s in data:
            self.MonitorType.decode_value(data[NSourceDataEnums.MonitorType.s])
        if NSourceDataEnums.MonitorSiblingId.s in data:
            self.MonitorSiblingId.decode_value(data[NSourceDataEnums.MonitorSiblingId.s])
        if NSourceDataEnums.MonitorAutoResetCounters.s in data:
            self.MonitorAutoResetCounters.decode_value(data[NSourceDataEnums.MonitorAutoResetCounters.s])
        if NSourceDataEnums.MonitorStatusReportingDelay.s in data:
            self.MonitorStatusReportingDelay.decode_value(data[NSourceDataEnums.MonitorStatusReportingDelay.s])
        if NSourceDataEnums.MonitorState.s in data:
            self.MonitorState.decode_value(data[NSourceDataEnums.MonitorState.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NSourceDataValue:
        o = NSourceDataValue()
        o.SourceCore = self.SourceCore.clone()
        o.Format = self.Format.clone()
        o.EventType = self.EventType.clone()
        o.MonitorType = self.MonitorType.clone()
        o.MonitorSiblingId = self.MonitorSiblingId.clone()
        o.MonitorAutoResetCounters = self.MonitorAutoResetCounters.clone()
        o.MonitorStatusReportingDelay = self.MonitorStatusReportingDelay.clone()
        o.MonitorState = self.MonitorState.clone()
        return o


class NSourceData:
    """Optional object type: NSourceData."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NSourceDataValue = NSourceDataValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NSourceDataValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NSourceDataValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NSourceDataValue | None = None) -> NSourceDataValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_SourceCore(self) -> NSourceCoreValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SourceCore

    def set_SourceCore(self, v: NSourceCoreValue) -> None:
        assert self._defined, "NSourceData must be defined before setting SourceCore"
        self._value.SourceCore = v.clone()  # copy to match Go's value semantics

    def get_Format(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Format

    def set_Format(self, v: Any) -> None:
        assert self._defined, "NSourceData must be defined before setting Format"
        _assign_value(self._value.Format, v)

    def get_EventType(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.EventType

    def set_EventType(self, v: Any) -> None:
        assert self._defined, "NSourceData must be defined before setting EventType"
        _assign_value(self._value.EventType, v)

    def get_MonitorType(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MonitorType

    def set_MonitorType(self, v: Any) -> None:
        assert self._defined, "NSourceData must be defined before setting MonitorType"
        _assign_value(self._value.MonitorType, v)

    def get_MonitorSiblingId(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MonitorSiblingId

    def set_MonitorSiblingId(self, v: Any) -> None:
        assert self._defined, "NSourceData must be defined before setting MonitorSiblingId"
        _assign_value(self._value.MonitorSiblingId, v)

    def get_MonitorAutoResetCounters(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MonitorAutoResetCounters

    def set_MonitorAutoResetCounters(self, v: Any) -> None:
        assert self._defined, "NSourceData must be defined before setting MonitorAutoResetCounters"
        _assign_value(self._value.MonitorAutoResetCounters, v)

    def get_MonitorStatusReportingDelay(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MonitorStatusReportingDelay

    def set_MonitorStatusReportingDelay(self, v: Any) -> None:
        assert self._defined, "NSourceData must be defined before setting MonitorStatusReportingDelay"
        _assign_value(self._value.MonitorStatusReportingDelay, v)

    def get_MonitorState(self) -> NMonitorState:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MonitorState

    def set_MonitorState(self, v: Any) -> None:
        assert self._defined, "NSourceData must be defined before setting MonitorState"
        _assign_value(self._value.MonitorState, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NSourceDataValue()

    def clone(self) -> NSourceData:
        o = NSourceData()
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
            return f"NSourceData(defined)"
        return "NSourceData(<undefined>)"


def make_nsourcedata_value(v: NSourceDataValue) -> NSourceDataValue:
    """Factory: create a NSourceDataValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nsourcedata(v: NSourceDataValue) -> NSourceData:
    """Factory: create a defined NSourceData from a NSourceDataValue."""
    o = NSourceData()
    o.set_value(v)
    return o

