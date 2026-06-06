"""Generated NMOS type: NSender. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NNullString, NEnum, NString, NUrl, NArrayOfString, NBool, NTime, NInt, NArrayOfInt
from nmos.types.generated.nresource_core import NResourceCore, NResourceCoreValue
from nmos.types.generated.ndevice_ptr import NDevicePtr, NDevicePtrValue
from nmos.types.generated.nsender_subscription import NSenderSubscription, NSenderSubscriptionValue
from nmos.types.generated.nsender_capabilities import NSenderCapabilities, NSenderCapabilitiesValue
from nmos.types.generated.nsender_active_constraints import NSenderActiveConstraints, NSenderActiveConstraintsValue
from nmos.types.generated.nsource_ptr import NSourcePtr, NSourcePtrValue
from nmos.validators import CheckResourceIdNullableString, CheckTransport, CheckResourceIdString, CheckFormat

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NSenderEnums:
    """JSON property name enums for NSender."""
    FlowId = EnumRegistry.get("flow_id")
    Transport = EnumRegistry.get("transport")
    DeviceId = EnumRegistry.get("device_id")
    ManifestHref = EnumRegistry.get("manifest_href")
    InterfaceBindings = EnumRegistry.get("interface_bindings")
    Subscription = EnumRegistry.get("subscription")
    Caps = EnumRegistry.get("caps")
    Bitrate = EnumRegistry.get("bit_rate")
    SenderType = EnumRegistry.get("st2110_21_sender_type")
    PacketTransmissionMode = EnumRegistry.get("packet_transmission_mode")
    ParameterSetsTransportMode = EnumRegistry.get("parameter_sets_transport_mode")
    ParameterSetsFlowMode = EnumRegistry.get("parameter_sets_flow_mode")
    InfoBlock = EnumRegistry.get("urn:x-matrox:info_block")
    HKEP = EnumRegistry.get("hkep")
    Privacy = EnumRegistry.get("privacy")
    pass


class NSenderValue:
    """Inner value struct for NSender."""

    __slots__ = (
        "ResourceCore",
        "FlowId",
        "Transport",
        "DeviceId",
        "Device",
        "ManifestHref",
        "InterfaceBindings",
        "Subscription",
        "Caps",
        "OnDemand",
        "OnDemandExpiry",
        "Format",
        "NaturalGroupIndex",
        "NaturalGroupRoleIndex",
        "Inputs",
        "CompatibilityStatus",
        "Constraints",
        "Bitrate",
        "SenderType",
        "PacketTransmissionMode",
        "ParameterSetsTransportMode",
        "ParameterSetsFlowMode",
        "InfoBlock",
        "HKEP",
        "Privacy",
        "Monitor",
    )

    def __init__(self) -> None:
        self.ResourceCore: NResourceCoreValue = NResourceCoreValue()
        self.FlowId: NNullString = NNullString()
        self.Transport: NEnum = NEnum()
        self.DeviceId: NString = NString()
        self.Device: NDevicePtr = NDevicePtr()
        self.ManifestHref: NUrl = NUrl()
        self.InterfaceBindings: NArrayOfString = NArrayOfString()
        self.Subscription: NSenderSubscription = NSenderSubscription()
        self.Caps: NSenderCapabilities = NSenderCapabilities()
        self.OnDemand: NBool = NBool()
        self.OnDemandExpiry: NTime = NTime()
        self.Format: NEnum = NEnum()
        self.NaturalGroupIndex: NInt = NInt()
        self.NaturalGroupRoleIndex: NInt = NInt()
        self.Inputs: NArrayOfString = NArrayOfString()
        self.CompatibilityStatus: NEnum = NEnum()
        self.Constraints: NSenderActiveConstraints = NSenderActiveConstraints()
        self.Bitrate: NInt = NInt()
        self.SenderType: NEnum = NEnum()
        self.PacketTransmissionMode: NEnum = NEnum()
        self.ParameterSetsTransportMode: NEnum = NEnum()
        self.ParameterSetsFlowMode: NEnum = NEnum()
        self.InfoBlock: NArrayOfInt = NArrayOfInt()
        self.HKEP: NBool = NBool()
        self.Privacy: NBool = NBool()
        self.Monitor: NSourcePtr = NSourcePtr()

    def set_to_default(self) -> None:
        self.ResourceCore = NResourceCoreValue()
        self.ResourceCore.set_to_default()
        _assign_value(self.FlowId, None)
        self.Transport.set_to_default()
        self.DeviceId.set_to_default()
        self.Device.set_to_default()
        self.ManifestHref.set_to_default()
        self.InterfaceBindings.set_to_default()
        self.Subscription.set_to_default()
        self.OnDemand.set_to_default()
        self.OnDemandExpiry.set_to_default()
        self.Format.set_to_default()
        self.Inputs.set_to_default()
        _assign_value(self.CompatibilityStatus, EnumRegistry.get("unconstrained"))
        self.Constraints.set_to_default()
        self.Monitor.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.FlowId.defined:
            raise InvalidObject("missing required member FlowId")
        if not self.Transport.defined:
            raise InvalidObject("missing required member Transport")
        if not self.DeviceId.defined:
            raise InvalidObject("missing required member DeviceId")
        if not self.ManifestHref.defined:
            raise InvalidObject("missing required member ManifestHref")
        if not self.InterfaceBindings.defined:
            raise InvalidObject("missing required member InterfaceBindings")
        if not self.Subscription.defined:
            raise InvalidObject("missing required member Subscription")
        if self.FlowId.defined:
            CheckResourceIdNullableString(self.FlowId)
        if self.Transport.defined:
            CheckTransport(self.Transport)
        if self.DeviceId.defined:
            CheckResourceIdString(self.DeviceId)
        if self.Format.defined:
            CheckFormat(self.Format)
        self.ResourceCore.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.ResourceCore.encode(engine, None)
        self.FlowId.encode(engine, NSenderEnums.FlowId)
        self.Transport.encode(engine, NSenderEnums.Transport)
        self.DeviceId.encode(engine, NSenderEnums.DeviceId)
        self.ManifestHref.encode(engine, NSenderEnums.ManifestHref)
        self.InterfaceBindings.encode(engine, NSenderEnums.InterfaceBindings)
        self.Subscription.encode(engine, NSenderEnums.Subscription)
        self.Caps.encode(engine, NSenderEnums.Caps)
        self.Bitrate.encode(engine, NSenderEnums.Bitrate)
        self.SenderType.encode(engine, NSenderEnums.SenderType)
        self.PacketTransmissionMode.encode(engine, NSenderEnums.PacketTransmissionMode)
        self.ParameterSetsTransportMode.encode(engine, NSenderEnums.ParameterSetsTransportMode)
        self.ParameterSetsFlowMode.encode(engine, NSenderEnums.ParameterSetsFlowMode)
        self.InfoBlock.encode(engine, NSenderEnums.InfoBlock)
        self.HKEP.encode(engine, NSenderEnums.HKEP)
        self.Privacy.encode(engine, NSenderEnums.Privacy)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NSender")

        self.ResourceCore.decode(engine, data)
        if NSenderEnums.FlowId.s in data:
            self.FlowId.decode_value(data[NSenderEnums.FlowId.s])
        if NSenderEnums.Transport.s in data:
            self.Transport.decode_value(data[NSenderEnums.Transport.s])
        if NSenderEnums.DeviceId.s in data:
            self.DeviceId.decode_value(data[NSenderEnums.DeviceId.s])
        if NSenderEnums.ManifestHref.s in data:
            self.ManifestHref.decode_value(data[NSenderEnums.ManifestHref.s])
        if NSenderEnums.InterfaceBindings.s in data:
            self.InterfaceBindings.decode_value(data[NSenderEnums.InterfaceBindings.s])
        if NSenderEnums.Subscription.s in data:
            self.Subscription.decode_value(data[NSenderEnums.Subscription.s])
        if NSenderEnums.Caps.s in data:
            self.Caps.decode_value(data[NSenderEnums.Caps.s])
        if NSenderEnums.Bitrate.s in data:
            self.Bitrate.decode_value(data[NSenderEnums.Bitrate.s])
        if NSenderEnums.SenderType.s in data:
            self.SenderType.decode_value(data[NSenderEnums.SenderType.s])
        if NSenderEnums.PacketTransmissionMode.s in data:
            self.PacketTransmissionMode.decode_value(data[NSenderEnums.PacketTransmissionMode.s])
        if NSenderEnums.ParameterSetsTransportMode.s in data:
            self.ParameterSetsTransportMode.decode_value(data[NSenderEnums.ParameterSetsTransportMode.s])
        if NSenderEnums.ParameterSetsFlowMode.s in data:
            self.ParameterSetsFlowMode.decode_value(data[NSenderEnums.ParameterSetsFlowMode.s])
        if NSenderEnums.InfoBlock.s in data:
            self.InfoBlock.decode_value(data[NSenderEnums.InfoBlock.s])
        if NSenderEnums.HKEP.s in data:
            self.HKEP.decode_value(data[NSenderEnums.HKEP.s])
        if NSenderEnums.Privacy.s in data:
            self.Privacy.decode_value(data[NSenderEnums.Privacy.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NSenderValue:
        o = NSenderValue()
        o.ResourceCore = self.ResourceCore.clone()
        o.FlowId = self.FlowId.clone()
        o.Transport = self.Transport.clone()
        o.DeviceId = self.DeviceId.clone()
        o.Device = self.Device.clone()
        o.ManifestHref = self.ManifestHref.clone()
        o.InterfaceBindings = self.InterfaceBindings.clone()
        o.Subscription = self.Subscription.clone()
        o.Caps = self.Caps.clone()
        o.OnDemand = self.OnDemand.clone()
        o.OnDemandExpiry = self.OnDemandExpiry.clone()
        o.Format = self.Format.clone()
        o.NaturalGroupIndex = self.NaturalGroupIndex.clone()
        o.NaturalGroupRoleIndex = self.NaturalGroupRoleIndex.clone()
        o.Inputs = self.Inputs.clone()
        o.CompatibilityStatus = self.CompatibilityStatus.clone()
        o.Constraints = self.Constraints.clone()
        o.Bitrate = self.Bitrate.clone()
        o.SenderType = self.SenderType.clone()
        o.PacketTransmissionMode = self.PacketTransmissionMode.clone()
        o.ParameterSetsTransportMode = self.ParameterSetsTransportMode.clone()
        o.ParameterSetsFlowMode = self.ParameterSetsFlowMode.clone()
        o.InfoBlock = self.InfoBlock.clone()
        o.HKEP = self.HKEP.clone()
        o.Privacy = self.Privacy.clone()
        o.Monitor = self.Monitor.clone()
        return o


class NSender:
    """Optional object type: NSender."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NSenderValue = NSenderValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NSenderValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NSenderValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NSenderValue | None = None) -> NSenderValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_ResourceCore(self) -> NResourceCoreValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ResourceCore

    def set_ResourceCore(self, v: NResourceCoreValue) -> None:
        assert self._defined, "NSender must be defined before setting ResourceCore"
        self._value.ResourceCore = v.clone()  # copy to match Go's value semantics

    def get_FlowId(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FlowId

    def set_FlowId(self, v: Any) -> None:
        assert self._defined, "NSender must be defined before setting FlowId"
        _assign_value(self._value.FlowId, v)

    def get_Transport(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Transport

    def set_Transport(self, v: Any) -> None:
        assert self._defined, "NSender must be defined before setting Transport"
        _assign_value(self._value.Transport, v)

    def get_DeviceId(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.DeviceId

    def set_DeviceId(self, v: Any) -> None:
        assert self._defined, "NSender must be defined before setting DeviceId"
        _assign_value(self._value.DeviceId, v)

    def get_ManifestHref(self) -> NUrl:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ManifestHref

    def set_ManifestHref(self, v: Any) -> None:
        assert self._defined, "NSender must be defined before setting ManifestHref"
        _assign_value(self._value.ManifestHref, v)

    def get_InterfaceBindings(self) -> NArrayOfString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.InterfaceBindings

    def set_InterfaceBindings(self, v: Any) -> None:
        assert self._defined, "NSender must be defined before setting InterfaceBindings"
        _assign_value(self._value.InterfaceBindings, v)

    def get_Subscription(self) -> NSenderSubscription:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Subscription

    def set_Subscription(self, v: Any) -> None:
        assert self._defined, "NSender must be defined before setting Subscription"
        _assign_value(self._value.Subscription, v)

    def get_Caps(self) -> NSenderCapabilities:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Caps

    def set_Caps(self, v: Any) -> None:
        assert self._defined, "NSender must be defined before setting Caps"
        _assign_value(self._value.Caps, v)

    def get_Bitrate(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Bitrate

    def set_Bitrate(self, v: Any) -> None:
        assert self._defined, "NSender must be defined before setting Bitrate"
        _assign_value(self._value.Bitrate, v)

    def get_SenderType(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SenderType

    def set_SenderType(self, v: Any) -> None:
        assert self._defined, "NSender must be defined before setting SenderType"
        _assign_value(self._value.SenderType, v)

    def get_PacketTransmissionMode(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.PacketTransmissionMode

    def set_PacketTransmissionMode(self, v: Any) -> None:
        assert self._defined, "NSender must be defined before setting PacketTransmissionMode"
        _assign_value(self._value.PacketTransmissionMode, v)

    def get_ParameterSetsTransportMode(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ParameterSetsTransportMode

    def set_ParameterSetsTransportMode(self, v: Any) -> None:
        assert self._defined, "NSender must be defined before setting ParameterSetsTransportMode"
        _assign_value(self._value.ParameterSetsTransportMode, v)

    def get_ParameterSetsFlowMode(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ParameterSetsFlowMode

    def set_ParameterSetsFlowMode(self, v: Any) -> None:
        assert self._defined, "NSender must be defined before setting ParameterSetsFlowMode"
        _assign_value(self._value.ParameterSetsFlowMode, v)

    def get_InfoBlock(self) -> NArrayOfInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.InfoBlock

    def set_InfoBlock(self, v: Any) -> None:
        assert self._defined, "NSender must be defined before setting InfoBlock"
        _assign_value(self._value.InfoBlock, v)

    def get_HKEP(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.HKEP

    def set_HKEP(self, v: Any) -> None:
        assert self._defined, "NSender must be defined before setting HKEP"
        _assign_value(self._value.HKEP, v)

    def get_Privacy(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Privacy

    def set_Privacy(self, v: Any) -> None:
        assert self._defined, "NSender must be defined before setting Privacy"
        _assign_value(self._value.Privacy, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NSenderValue()

    def clone(self) -> NSender:
        o = NSender()
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
            return f"NSender(defined)"
        return "NSender(<undefined>)"


def make_nsender_value(v: NSenderValue) -> NSenderValue:
    """Factory: create a NSenderValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nsender(v: NSenderValue) -> NSender:
    """Factory: create a defined NSender from a NSenderValue."""
    o = NSender()
    o.set_value(v)
    return o

