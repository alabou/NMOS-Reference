"""Generated NMOS type: NSourceCore. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NNullString, NString, NArrayOfString, NInt, NBool
from nmos.types.generated.nresource_core import NResourceCore, NResourceCoreValue
from nmos.types.generated.nsource_capabilities import NSourceCapabilities, NSourceCapabilitiesValue
from nmos.types.generated.ndevice_ptr import NDevicePtr, NDevicePtrValue
from nmos.types.generated.nsource_ptrs import NSourcePtrs, NSourcePtrsValue
from nmos.types.generated.nflow_ptrs import NFlowPtrs, NFlowPtrsValue
from nmos.types.generated.nrational import NRational, NRationalValue
from nmos.validators import CheckResourceIdNullableString, CheckResourceIdString, CheckArrayOfResourceIdString, CheckClockNameNullableString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NSourceCoreEnums:
    """JSON property name enums for NSourceCore."""
    Caps = EnumRegistry.get("caps")
    ReceiverId = EnumRegistry.get("urn:x-matrox:receiver_id")
    DeviceId = EnumRegistry.get("device_id")
    Parents = EnumRegistry.get("parents")
    ClockName = EnumRegistry.get("clock_name")
    GrainRate = EnumRegistry.get("grain_rate")
    Layer = EnumRegistry.get("urn:x-matrox:layer")
    SynchronousMedia = EnumRegistry.get("urn:x-matrox:synchronous_media")
    pass


class NSourceCoreValue:
    """Inner value struct for NSourceCore."""

    __slots__ = (
        "ResourceCore",
        "Caps",
        "ReceiverId",
        "DeviceId",
        "Device",
        "Parents",
        "Children",
        "Flows",
        "ClockName",
        "GrainRate",
        "Layer",
        "SynchronousMedia",
    )

    def __init__(self) -> None:
        self.ResourceCore: NResourceCoreValue = NResourceCoreValue()
        self.Caps: NSourceCapabilities = NSourceCapabilities()
        self.ReceiverId: NNullString = NNullString()
        self.DeviceId: NString = NString()
        self.Device: NDevicePtr = NDevicePtr()
        self.Parents: NArrayOfString = NArrayOfString()
        self.Children: NSourcePtrs = NSourcePtrs()
        self.Flows: NFlowPtrs = NFlowPtrs()
        self.ClockName: NNullString = NNullString()
        self.GrainRate: NRational = NRational()
        self.Layer: NInt = NInt()
        self.SynchronousMedia: NBool = NBool()

    def set_to_default(self) -> None:
        self.ResourceCore = NResourceCoreValue()
        self.ResourceCore.set_to_default()
        self.Caps.set_to_default()
        _assign_value(self.ReceiverId, None)
        self.DeviceId.set_to_default()
        self.Device.set_to_default()
        self.Parents.set_to_default()
        self.Children.set_to_default()
        self.Flows.set_to_default()
        self.ClockName.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        if not self.ReceiverId.defined:
            _assign_value(self.ReceiverId, None)
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Caps.defined:
            raise InvalidObject("missing required member Caps")
        if not self.DeviceId.defined:
            raise InvalidObject("missing required member DeviceId")
        if not self.Parents.defined:
            raise InvalidObject("missing required member Parents")
        if not self.ClockName.defined:
            raise InvalidObject("missing required member ClockName")
        if self.ReceiverId.defined:
            CheckResourceIdNullableString(self.ReceiverId)
        if self.DeviceId.defined:
            CheckResourceIdString(self.DeviceId)
        if self.Parents.defined:
            CheckArrayOfResourceIdString(self.Parents)
        if self.ClockName.defined:
            CheckClockNameNullableString(self.ClockName)
        self.ResourceCore.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        self.ResourceCore.encode(engine, None)
        self.Caps.encode(engine, NSourceCoreEnums.Caps)
        self.ReceiverId.encode(engine, NSourceCoreEnums.ReceiverId)
        self.DeviceId.encode(engine, NSourceCoreEnums.DeviceId)
        self.Parents.encode(engine, NSourceCoreEnums.Parents)
        self.ClockName.encode(engine, NSourceCoreEnums.ClockName)
        self.GrainRate.encode(engine, NSourceCoreEnums.GrainRate)
        self.Layer.encode(engine, NSourceCoreEnums.Layer)
        self.SynchronousMedia.encode(engine, NSourceCoreEnums.SynchronousMedia)

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NSourceCore")

        self.ResourceCore.decode(engine, data)
        if NSourceCoreEnums.Caps.s in data:
            self.Caps.decode_value(data[NSourceCoreEnums.Caps.s])
        if NSourceCoreEnums.ReceiverId.s in data:
            self.ReceiverId.decode_value(data[NSourceCoreEnums.ReceiverId.s])
        if NSourceCoreEnums.DeviceId.s in data:
            self.DeviceId.decode_value(data[NSourceCoreEnums.DeviceId.s])
        if NSourceCoreEnums.Parents.s in data:
            self.Parents.decode_value(data[NSourceCoreEnums.Parents.s])
        if NSourceCoreEnums.ClockName.s in data:
            self.ClockName.decode_value(data[NSourceCoreEnums.ClockName.s])
        if NSourceCoreEnums.GrainRate.s in data:
            self.GrainRate.decode_value(data[NSourceCoreEnums.GrainRate.s])
        if NSourceCoreEnums.Layer.s in data:
            self.Layer.decode_value(data[NSourceCoreEnums.Layer.s])
        if NSourceCoreEnums.SynchronousMedia.s in data:
            self.SynchronousMedia.decode_value(data[NSourceCoreEnums.SynchronousMedia.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NSourceCoreValue:
        o = NSourceCoreValue()
        o.ResourceCore = self.ResourceCore.clone()
        o.Caps = self.Caps.clone()
        o.ReceiverId = self.ReceiverId.clone()
        o.DeviceId = self.DeviceId.clone()
        o.Device = self.Device.clone()
        o.Parents = self.Parents.clone()
        o.Children = self.Children.clone()
        o.Flows = self.Flows.clone()
        o.ClockName = self.ClockName.clone()
        o.GrainRate = self.GrainRate.clone()
        o.Layer = self.Layer.clone()
        o.SynchronousMedia = self.SynchronousMedia.clone()
        return o


class NSourceCore:
    """Optional object type: NSourceCore."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NSourceCoreValue = NSourceCoreValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NSourceCoreValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NSourceCoreValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NSourceCoreValue | None = None) -> NSourceCoreValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_ResourceCore(self) -> NResourceCoreValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ResourceCore

    def set_ResourceCore(self, v: NResourceCoreValue) -> None:
        assert self._defined, "NSourceCore must be defined before setting ResourceCore"
        self._value.ResourceCore = v.clone()  # copy to match Go's value semantics

    def get_Caps(self) -> NSourceCapabilities:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Caps

    def set_Caps(self, v: Any) -> None:
        assert self._defined, "NSourceCore must be defined before setting Caps"
        _assign_value(self._value.Caps, v)

    def get_ReceiverId(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ReceiverId

    def set_ReceiverId(self, v: Any) -> None:
        assert self._defined, "NSourceCore must be defined before setting ReceiverId"
        _assign_value(self._value.ReceiverId, v)

    def get_DeviceId(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.DeviceId

    def set_DeviceId(self, v: Any) -> None:
        assert self._defined, "NSourceCore must be defined before setting DeviceId"
        _assign_value(self._value.DeviceId, v)

    def get_Parents(self) -> NArrayOfString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Parents

    def set_Parents(self, v: Any) -> None:
        assert self._defined, "NSourceCore must be defined before setting Parents"
        _assign_value(self._value.Parents, v)

    def get_ClockName(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ClockName

    def set_ClockName(self, v: Any) -> None:
        assert self._defined, "NSourceCore must be defined before setting ClockName"
        _assign_value(self._value.ClockName, v)

    def get_GrainRate(self) -> NRational:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.GrainRate

    def set_GrainRate(self, v: Any) -> None:
        assert self._defined, "NSourceCore must be defined before setting GrainRate"
        _assign_value(self._value.GrainRate, v)

    def get_Layer(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Layer

    def set_Layer(self, v: Any) -> None:
        assert self._defined, "NSourceCore must be defined before setting Layer"
        _assign_value(self._value.Layer, v)

    def get_SynchronousMedia(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SynchronousMedia

    def set_SynchronousMedia(self, v: Any) -> None:
        assert self._defined, "NSourceCore must be defined before setting SynchronousMedia"
        _assign_value(self._value.SynchronousMedia, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NSourceCoreValue()

    def clone(self) -> NSourceCore:
        o = NSourceCore()
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
            return f"NSourceCore(defined)"
        return "NSourceCore(<undefined>)"


def make_nsourcecore_value(v: NSourceCoreValue) -> NSourceCoreValue:
    """Factory: create a NSourceCoreValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nsourcecore(v: NSourceCoreValue) -> NSourceCore:
    """Factory: create a defined NSourceCore from a NSourceCoreValue."""
    o = NSourceCore()
    o.set_value(v)
    return o

