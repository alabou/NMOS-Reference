"""Generated NMOS type: NReceiverCore. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NEnum, NString, NArrayOfString, NBool, NTime, NInt
from nmos.types.generated.nresource_core import NResourceCore, NResourceCoreValue
from nmos.types.generated.ndevice_ptr import NDevicePtr, NDevicePtrValue
from nmos.types.generated.nsource_ptrs import NSourcePtrs, NSourcePtrsValue
from nmos.types.generated.nreceiver_subscription import NReceiverSubscription, NReceiverSubscriptionValue
from nmos.types.generated.nsource_ptr import NSourcePtr, NSourcePtrValue
from nmos.validators import CheckTransport, CheckResourceIdString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NReceiverCoreEnums:
    """JSON property name enums for NReceiverCore."""
    Transport = EnumRegistry.get("transport")
    DeviceId = EnumRegistry.get("device_id")
    InterfaceBindings = EnumRegistry.get("interface_bindings")
    Subscription = EnumRegistry.get("subscription")
    pass


class NReceiverCoreValue:
    """Inner value struct for NReceiverCore."""

    __slots__ = (
        "ResourceCore",
        "Transport",
        "DeviceId",
        "Device",
        "Sources",
        "InterfaceBindings",
        "Subscription",
        "OnDemand",
        "OnDemandExpiry",
        "NaturalGroupIndex",
        "NaturalGroupRoleIndex",
        "Outputs",
        "CompatibilityStatus",
        "Static",
        "Monitor",
    )

    def __init__(self) -> None:
        self.ResourceCore: NResourceCoreValue = NResourceCoreValue()
        self.Transport: NEnum = NEnum()
        self.DeviceId: NString = NString()
        self.Device: NDevicePtr = NDevicePtr()
        self.Sources: NSourcePtrs = NSourcePtrs()
        self.InterfaceBindings: NArrayOfString = NArrayOfString()
        self.Subscription: NReceiverSubscription = NReceiverSubscription()
        self.OnDemand: NBool = NBool()
        self.OnDemandExpiry: NTime = NTime()
        self.NaturalGroupIndex: NInt = NInt()
        self.NaturalGroupRoleIndex: NInt = NInt()
        self.Outputs: NArrayOfString = NArrayOfString()
        self.CompatibilityStatus: NEnum = NEnum()
        self.Static: NBool = NBool()
        self.Monitor: NSourcePtr = NSourcePtr()

    def set_to_default(self) -> None:
        self.ResourceCore = NResourceCoreValue()
        self.ResourceCore.set_to_default()
        self.Transport.set_to_default()
        self.DeviceId.set_to_default()
        self.Device.set_to_default()
        self.Sources.set_to_default()
        self.InterfaceBindings.set_to_default()
        self.Subscription.set_to_default()
        self.OnDemand.set_to_default()
        self.OnDemandExpiry.set_to_default()
        self.Outputs.set_to_default()
        _assign_value(self.CompatibilityStatus, EnumRegistry.get("unknown"))
        self.Static.set_to_default()
        self.Monitor.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Transport.defined:
            raise InvalidObject("missing required member Transport")
        if not self.DeviceId.defined:
            raise InvalidObject("missing required member DeviceId")
        if not self.InterfaceBindings.defined:
            raise InvalidObject("missing required member InterfaceBindings")
        if not self.Subscription.defined:
            raise InvalidObject("missing required member Subscription")
        if self.Transport.defined:
            CheckTransport(self.Transport)
        if self.DeviceId.defined:
            CheckResourceIdString(self.DeviceId)
        self.ResourceCore.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        self.ResourceCore.encode(engine, None)
        self.Transport.encode(engine, NReceiverCoreEnums.Transport)
        self.DeviceId.encode(engine, NReceiverCoreEnums.DeviceId)
        self.InterfaceBindings.encode(engine, NReceiverCoreEnums.InterfaceBindings)
        self.Subscription.encode(engine, NReceiverCoreEnums.Subscription)

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NReceiverCore")

        self.ResourceCore.decode(engine, data)
        if NReceiverCoreEnums.Transport.s in data:
            self.Transport.decode_value(data[NReceiverCoreEnums.Transport.s])
        if NReceiverCoreEnums.DeviceId.s in data:
            self.DeviceId.decode_value(data[NReceiverCoreEnums.DeviceId.s])
        if NReceiverCoreEnums.InterfaceBindings.s in data:
            self.InterfaceBindings.decode_value(data[NReceiverCoreEnums.InterfaceBindings.s])
        if NReceiverCoreEnums.Subscription.s in data:
            self.Subscription.decode_value(data[NReceiverCoreEnums.Subscription.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NReceiverCoreValue:
        o = NReceiverCoreValue()
        o.ResourceCore = self.ResourceCore.clone()
        o.Transport = self.Transport.clone()
        o.DeviceId = self.DeviceId.clone()
        o.Device = self.Device.clone()
        o.Sources = self.Sources.clone()
        o.InterfaceBindings = self.InterfaceBindings.clone()
        o.Subscription = self.Subscription.clone()
        o.OnDemand = self.OnDemand.clone()
        o.OnDemandExpiry = self.OnDemandExpiry.clone()
        o.NaturalGroupIndex = self.NaturalGroupIndex.clone()
        o.NaturalGroupRoleIndex = self.NaturalGroupRoleIndex.clone()
        o.Outputs = self.Outputs.clone()
        o.CompatibilityStatus = self.CompatibilityStatus.clone()
        o.Static = self.Static.clone()
        o.Monitor = self.Monitor.clone()
        return o


class NReceiverCore:
    """Optional object type: NReceiverCore."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NReceiverCoreValue = NReceiverCoreValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NReceiverCoreValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NReceiverCoreValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NReceiverCoreValue | None = None) -> NReceiverCoreValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_ResourceCore(self) -> NResourceCoreValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ResourceCore

    def set_ResourceCore(self, v: NResourceCoreValue) -> None:
        assert self._defined, "NReceiverCore must be defined before setting ResourceCore"
        self._value.ResourceCore = v.clone()  # copy to match Go's value semantics

    def get_Transport(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Transport

    def set_Transport(self, v: Any) -> None:
        assert self._defined, "NReceiverCore must be defined before setting Transport"
        _assign_value(self._value.Transport, v)

    def get_DeviceId(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.DeviceId

    def set_DeviceId(self, v: Any) -> None:
        assert self._defined, "NReceiverCore must be defined before setting DeviceId"
        _assign_value(self._value.DeviceId, v)

    def get_InterfaceBindings(self) -> NArrayOfString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.InterfaceBindings

    def set_InterfaceBindings(self, v: Any) -> None:
        assert self._defined, "NReceiverCore must be defined before setting InterfaceBindings"
        _assign_value(self._value.InterfaceBindings, v)

    def get_Subscription(self) -> NReceiverSubscription:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Subscription

    def set_Subscription(self, v: Any) -> None:
        assert self._defined, "NReceiverCore must be defined before setting Subscription"
        _assign_value(self._value.Subscription, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NReceiverCoreValue()

    def clone(self) -> NReceiverCore:
        o = NReceiverCore()
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
            return f"NReceiverCore(defined)"
        return "NReceiverCore(<undefined>)"


def make_nreceivercore_value(v: NReceiverCoreValue) -> NReceiverCoreValue:
    """Factory: create a NReceiverCoreValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nreceivercore(v: NReceiverCoreValue) -> NReceiverCore:
    """Factory: create a defined NReceiverCore from a NReceiverCoreValue."""
    o = NReceiverCore()
    o.set_value(v)
    return o

