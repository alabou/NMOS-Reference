"""Generated NMOS type: NOutput. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NBool, NString
from nmos.types.generated.nresource_core import NResourceCore, NResourceCoreValue
from nmos.types.generated.noutput_status import NOutputStatus, NOutputStatusValue
from nmos.types.generated.ndevice_ptr import NDevicePtr, NDevicePtrValue
from nmos.validators import CheckResourceIdString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NOutputEnums:
    """JSON property name enums for NOutput."""
    Connected = EnumRegistry.get("connected")
    EdidSupport = EnumRegistry.get("edid_support")
    Status = EnumRegistry.get("status")
    SourceId = EnumRegistry.get("source_id")
    DeviceId = EnumRegistry.get("device_id")
    pass


class NOutputValue:
    """Inner value struct for NOutput."""

    __slots__ = (
        "ResourceCore",
        "Connected",
        "EdidSupport",
        "Status",
        "SourceId",
        "DeviceId",
        "Device",
    )

    def __init__(self) -> None:
        self.ResourceCore: NResourceCoreValue = NResourceCoreValue()
        self.Connected: NBool = NBool()
        self.EdidSupport: NBool = NBool()
        self.Status: NOutputStatus = NOutputStatus()
        self.SourceId: NString = NString()
        self.DeviceId: NString = NString()
        self.Device: NDevicePtr = NDevicePtr()

    def set_to_default(self) -> None:
        self.ResourceCore = NResourceCoreValue()
        self.ResourceCore.set_to_default()
        self.Connected.set_to_default()
        self.EdidSupport.set_to_default()
        self.Status.set_to_default()
        self.Device.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Connected.defined:
            raise InvalidObject("missing required member Connected")
        if not self.EdidSupport.defined:
            raise InvalidObject("missing required member EdidSupport")
        if not self.Status.defined:
            raise InvalidObject("missing required member Status")
        if self.SourceId.defined:
            CheckResourceIdString(self.SourceId)
        if self.DeviceId.defined:
            CheckResourceIdString(self.DeviceId)
        self.ResourceCore.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.ResourceCore.encode(engine, None)
        self.Connected.encode(engine, NOutputEnums.Connected)
        self.EdidSupport.encode(engine, NOutputEnums.EdidSupport)
        self.Status.encode(engine, NOutputEnums.Status)
        self.SourceId.encode(engine, NOutputEnums.SourceId)
        self.DeviceId.encode(engine, NOutputEnums.DeviceId)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NOutput")

        self.ResourceCore.decode(engine, data)
        if NOutputEnums.Connected.s in data:
            self.Connected.decode_value(data[NOutputEnums.Connected.s])
        if NOutputEnums.EdidSupport.s in data:
            self.EdidSupport.decode_value(data[NOutputEnums.EdidSupport.s])
        if NOutputEnums.Status.s in data:
            self.Status.decode_value(data[NOutputEnums.Status.s])
        if NOutputEnums.SourceId.s in data:
            self.SourceId.decode_value(data[NOutputEnums.SourceId.s])
        if NOutputEnums.DeviceId.s in data:
            self.DeviceId.decode_value(data[NOutputEnums.DeviceId.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NOutputValue:
        o = NOutputValue()
        o.ResourceCore = self.ResourceCore.clone()
        o.Connected = self.Connected.clone()
        o.EdidSupport = self.EdidSupport.clone()
        o.Status = self.Status.clone()
        o.SourceId = self.SourceId.clone()
        o.DeviceId = self.DeviceId.clone()
        o.Device = self.Device.clone()
        return o


class NOutput:
    """Optional object type: NOutput."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NOutputValue = NOutputValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NOutputValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NOutputValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NOutputValue | None = None) -> NOutputValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_ResourceCore(self) -> NResourceCoreValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ResourceCore

    def set_ResourceCore(self, v: NResourceCoreValue) -> None:
        assert self._defined, "NOutput must be defined before setting ResourceCore"
        self._value.ResourceCore = v.clone()  # copy to match Go's value semantics

    def get_Connected(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Connected

    def set_Connected(self, v: Any) -> None:
        assert self._defined, "NOutput must be defined before setting Connected"
        _assign_value(self._value.Connected, v)

    def get_EdidSupport(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.EdidSupport

    def set_EdidSupport(self, v: Any) -> None:
        assert self._defined, "NOutput must be defined before setting EdidSupport"
        _assign_value(self._value.EdidSupport, v)

    def get_Status(self) -> NOutputStatus:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Status

    def set_Status(self, v: Any) -> None:
        assert self._defined, "NOutput must be defined before setting Status"
        _assign_value(self._value.Status, v)

    def get_SourceId(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SourceId

    def set_SourceId(self, v: Any) -> None:
        assert self._defined, "NOutput must be defined before setting SourceId"
        _assign_value(self._value.SourceId, v)

    def get_DeviceId(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.DeviceId

    def set_DeviceId(self, v: Any) -> None:
        assert self._defined, "NOutput must be defined before setting DeviceId"
        _assign_value(self._value.DeviceId, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NOutputValue()

    def clone(self) -> NOutput:
        o = NOutput()
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
            return f"NOutput(defined)"
        return "NOutput(<undefined>)"


def make_noutput_value(v: NOutputValue) -> NOutputValue:
    """Factory: create a NOutputValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_noutput(v: NOutputValue) -> NOutput:
    """Factory: create a defined NOutput from a NOutputValue."""
    o = NOutput()
    o.set_value(v)
    return o

