"""Generated NMOS type: NFlowCore. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString, NArrayOfString, NInt, NArrayOfInt, NBool
from nmos.types.generated.nresource_core import NResourceCore, NResourceCoreValue
from nmos.types.generated.ndevice_ptr import NDevicePtr, NDevicePtrValue
from nmos.types.generated.nflow_ptrs import NFlowPtrs, NFlowPtrsValue
from nmos.types.generated.nsender_ptrs import NSenderPtrs, NSenderPtrsValue
from nmos.types.generated.nrational import NRational, NRationalValue
from nmos.types.generated.nflow_ptr import NFlowPtr, NFlowPtrValue
from nmos.validators import CheckResourceIdString, CheckArrayOfResourceIdString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NFlowCoreEnums:
    """JSON property name enums for NFlowCore."""
    SourceId = EnumRegistry.get("source_id")
    DeviceId = EnumRegistry.get("device_id")
    Parents = EnumRegistry.get("parents")
    GrainRate = EnumRegistry.get("grain_rate")
    Layer = EnumRegistry.get("urn:x-matrox:layer")
    LayerCompatibilityGroups = EnumRegistry.get("urn:x-matrox:layer_compatibility_groups")
    pass


class NFlowCoreValue:
    """Inner value struct for NFlowCore."""

    __slots__ = (
        "ResourceCore",
        "SourceId",
        "DeviceId",
        "Device",
        "Parents",
        "Children",
        "Senders",
        "GrainRate",
        "Layer",
        "LayerCompatibilityGroups",
        "Static",
        "RawFlow",
        "CodedFlow",
    )

    def __init__(self) -> None:
        self.ResourceCore: NResourceCoreValue = NResourceCoreValue()
        self.SourceId: NString = NString()
        self.DeviceId: NString = NString()
        self.Device: NDevicePtr = NDevicePtr()
        self.Parents: NArrayOfString = NArrayOfString()
        self.Children: NFlowPtrs = NFlowPtrs()
        self.Senders: NSenderPtrs = NSenderPtrs()
        self.GrainRate: NRational = NRational()
        self.Layer: NInt = NInt()
        self.LayerCompatibilityGroups: NArrayOfInt = NArrayOfInt()
        self.Static: NBool = NBool()
        self.RawFlow: NFlowPtr = NFlowPtr()
        self.CodedFlow: NFlowPtr = NFlowPtr()

    def set_to_default(self) -> None:
        self.ResourceCore = NResourceCoreValue()
        self.ResourceCore.set_to_default()
        self.SourceId.set_to_default()
        self.DeviceId.set_to_default()
        self.Device.set_to_default()
        self.Parents.set_to_default()
        self.Children.set_to_default()
        self.Senders.set_to_default()
        self.Static.set_to_default()
        self.RawFlow.set_to_default()
        self.CodedFlow.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.SourceId.defined:
            raise InvalidObject("missing required member SourceId")
        if not self.DeviceId.defined:
            raise InvalidObject("missing required member DeviceId")
        if not self.Parents.defined:
            raise InvalidObject("missing required member Parents")
        if self.SourceId.defined:
            CheckResourceIdString(self.SourceId)
        if self.DeviceId.defined:
            CheckResourceIdString(self.DeviceId)
        if self.Parents.defined:
            CheckArrayOfResourceIdString(self.Parents)
        self.ResourceCore.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        self.ResourceCore.encode(engine, None)
        self.SourceId.encode(engine, NFlowCoreEnums.SourceId)
        self.DeviceId.encode(engine, NFlowCoreEnums.DeviceId)
        self.Parents.encode(engine, NFlowCoreEnums.Parents)
        self.GrainRate.encode(engine, NFlowCoreEnums.GrainRate)
        self.Layer.encode(engine, NFlowCoreEnums.Layer)
        self.LayerCompatibilityGroups.encode(engine, NFlowCoreEnums.LayerCompatibilityGroups)

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NFlowCore")

        self.ResourceCore.decode(engine, data)
        if NFlowCoreEnums.SourceId.s in data:
            self.SourceId.decode_value(data[NFlowCoreEnums.SourceId.s])
        if NFlowCoreEnums.DeviceId.s in data:
            self.DeviceId.decode_value(data[NFlowCoreEnums.DeviceId.s])
        if NFlowCoreEnums.Parents.s in data:
            self.Parents.decode_value(data[NFlowCoreEnums.Parents.s])
        if NFlowCoreEnums.GrainRate.s in data:
            self.GrainRate.decode_value(data[NFlowCoreEnums.GrainRate.s])
        if NFlowCoreEnums.Layer.s in data:
            self.Layer.decode_value(data[NFlowCoreEnums.Layer.s])
        if NFlowCoreEnums.LayerCompatibilityGroups.s in data:
            self.LayerCompatibilityGroups.decode_value(data[NFlowCoreEnums.LayerCompatibilityGroups.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NFlowCoreValue:
        o = NFlowCoreValue()
        o.ResourceCore = self.ResourceCore.clone()
        o.SourceId = self.SourceId.clone()
        o.DeviceId = self.DeviceId.clone()
        o.Device = self.Device.clone()
        o.Parents = self.Parents.clone()
        o.Children = self.Children.clone()
        o.Senders = self.Senders.clone()
        o.GrainRate = self.GrainRate.clone()
        o.Layer = self.Layer.clone()
        o.LayerCompatibilityGroups = self.LayerCompatibilityGroups.clone()
        o.Static = self.Static.clone()
        o.RawFlow = self.RawFlow.clone()
        o.CodedFlow = self.CodedFlow.clone()
        return o


class NFlowCore:
    """Optional object type: NFlowCore."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NFlowCoreValue = NFlowCoreValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NFlowCoreValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NFlowCoreValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NFlowCoreValue | None = None) -> NFlowCoreValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_ResourceCore(self) -> NResourceCoreValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ResourceCore

    def set_ResourceCore(self, v: NResourceCoreValue) -> None:
        assert self._defined, "NFlowCore must be defined before setting ResourceCore"
        self._value.ResourceCore = v.clone()  # copy to match Go's value semantics

    def get_SourceId(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SourceId

    def set_SourceId(self, v: Any) -> None:
        assert self._defined, "NFlowCore must be defined before setting SourceId"
        _assign_value(self._value.SourceId, v)

    def get_DeviceId(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.DeviceId

    def set_DeviceId(self, v: Any) -> None:
        assert self._defined, "NFlowCore must be defined before setting DeviceId"
        _assign_value(self._value.DeviceId, v)

    def get_Parents(self) -> NArrayOfString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Parents

    def set_Parents(self, v: Any) -> None:
        assert self._defined, "NFlowCore must be defined before setting Parents"
        _assign_value(self._value.Parents, v)

    def get_GrainRate(self) -> NRational:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.GrainRate

    def set_GrainRate(self, v: Any) -> None:
        assert self._defined, "NFlowCore must be defined before setting GrainRate"
        _assign_value(self._value.GrainRate, v)

    def get_Layer(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Layer

    def set_Layer(self, v: Any) -> None:
        assert self._defined, "NFlowCore must be defined before setting Layer"
        _assign_value(self._value.Layer, v)

    def get_LayerCompatibilityGroups(self) -> NArrayOfInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.LayerCompatibilityGroups

    def set_LayerCompatibilityGroups(self, v: Any) -> None:
        assert self._defined, "NFlowCore must be defined before setting LayerCompatibilityGroups"
        _assign_value(self._value.LayerCompatibilityGroups, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NFlowCoreValue()

    def clone(self) -> NFlowCore:
        o = NFlowCore()
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
            return f"NFlowCore(defined)"
        return "NFlowCore(<undefined>)"


def make_nflowcore_value(v: NFlowCoreValue) -> NFlowCoreValue:
    """Factory: create a NFlowCoreValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nflowcore(v: NFlowCoreValue) -> NFlowCore:
    """Factory: create a defined NFlowCore from a NFlowCoreValue."""
    o = NFlowCore()
    o.set_value(v)
    return o

