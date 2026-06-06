"""Generated NMOS type: NDevice. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NEnum, NString, NArrayOfString
from nmos.types.generated.nresource_core import NResourceCore, NResourceCoreValue
from nmos.types.generated.nnode_ptr import NNodePtr, NNodePtrValue
from nmos.types.generated.narray_of_device_control import NArrayOfDeviceControl, NArrayOfDeviceControlValue
from nmos.validators import CheckDeviceType, CheckResourceIdString, CheckArrayOfResourceIdString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NDeviceEnums:
    """JSON property name enums for NDevice."""
    Type = EnumRegistry.get("type")
    NodeId = EnumRegistry.get("node_id")
    Senders = EnumRegistry.get("senders")
    Receivers = EnumRegistry.get("receivers")
    Controls = EnumRegistry.get("controls")
    pass


class NDeviceValue:
    """Inner value struct for NDevice."""

    __slots__ = (
        "ResourceCore",
        "Type",
        "NodeId",
        "Node",
        "Senders",
        "Receivers",
        "Controls",
    )

    def __init__(self) -> None:
        self.ResourceCore: NResourceCoreValue = NResourceCoreValue()
        self.Type: NEnum = NEnum()
        self.NodeId: NString = NString()
        self.Node: NNodePtr = NNodePtr()
        self.Senders: NArrayOfString = NArrayOfString()
        self.Receivers: NArrayOfString = NArrayOfString()
        self.Controls: NArrayOfDeviceControl = NArrayOfDeviceControl()

    def set_to_default(self) -> None:
        self.ResourceCore = NResourceCoreValue()
        self.ResourceCore.set_to_default()
        _assign_value(self.Type, EnumRegistry.get("urn:x-nmos:device:generic"))
        self.NodeId.set_to_default()
        self.Node.set_to_default()
        self.Senders.set_to_default()
        self.Receivers.set_to_default()
        self.Controls.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Type.defined:
            raise InvalidObject("missing required member Type")
        if not self.NodeId.defined:
            raise InvalidObject("missing required member NodeId")
        if not self.Senders.defined:
            raise InvalidObject("missing required member Senders")
        if not self.Receivers.defined:
            raise InvalidObject("missing required member Receivers")
        if not self.Controls.defined:
            raise InvalidObject("missing required member Controls")
        if self.Type.defined:
            CheckDeviceType(self.Type)
        if self.NodeId.defined:
            CheckResourceIdString(self.NodeId)
        if self.Senders.defined:
            CheckArrayOfResourceIdString(self.Senders)
        if self.Receivers.defined:
            CheckArrayOfResourceIdString(self.Receivers)
        self.ResourceCore.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.ResourceCore.encode(engine, None)
        self.Type.encode(engine, NDeviceEnums.Type)
        self.NodeId.encode(engine, NDeviceEnums.NodeId)
        self.Senders.encode(engine, NDeviceEnums.Senders)
        self.Receivers.encode(engine, NDeviceEnums.Receivers)
        self.Controls.encode(engine, NDeviceEnums.Controls)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NDevice")

        self.ResourceCore.decode(engine, data)
        if NDeviceEnums.Type.s in data:
            self.Type.decode_value(data[NDeviceEnums.Type.s])
        if NDeviceEnums.NodeId.s in data:
            self.NodeId.decode_value(data[NDeviceEnums.NodeId.s])
        if NDeviceEnums.Senders.s in data:
            self.Senders.decode_value(data[NDeviceEnums.Senders.s])
        if NDeviceEnums.Receivers.s in data:
            self.Receivers.decode_value(data[NDeviceEnums.Receivers.s])
        if NDeviceEnums.Controls.s in data:
            self.Controls.decode_value(data[NDeviceEnums.Controls.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NDeviceValue:
        o = NDeviceValue()
        o.ResourceCore = self.ResourceCore.clone()
        o.Type = self.Type.clone()
        o.NodeId = self.NodeId.clone()
        o.Node = self.Node.clone()
        o.Senders = self.Senders.clone()
        o.Receivers = self.Receivers.clone()
        o.Controls = self.Controls.clone()
        return o


class NDevice:
    """Optional object type: NDevice."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NDeviceValue = NDeviceValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NDeviceValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NDeviceValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NDeviceValue | None = None) -> NDeviceValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_ResourceCore(self) -> NResourceCoreValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ResourceCore

    def set_ResourceCore(self, v: NResourceCoreValue) -> None:
        assert self._defined, "NDevice must be defined before setting ResourceCore"
        self._value.ResourceCore = v.clone()  # copy to match Go's value semantics

    def get_Type(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Type

    def set_Type(self, v: Any) -> None:
        assert self._defined, "NDevice must be defined before setting Type"
        _assign_value(self._value.Type, v)

    def get_NodeId(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.NodeId

    def set_NodeId(self, v: Any) -> None:
        assert self._defined, "NDevice must be defined before setting NodeId"
        _assign_value(self._value.NodeId, v)

    def get_Senders(self) -> NArrayOfString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Senders

    def set_Senders(self, v: Any) -> None:
        assert self._defined, "NDevice must be defined before setting Senders"
        _assign_value(self._value.Senders, v)

    def get_Receivers(self) -> NArrayOfString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Receivers

    def set_Receivers(self, v: Any) -> None:
        assert self._defined, "NDevice must be defined before setting Receivers"
        _assign_value(self._value.Receivers, v)

    def get_Controls(self) -> NArrayOfDeviceControl:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Controls

    def set_Controls(self, v: Any) -> None:
        assert self._defined, "NDevice must be defined before setting Controls"
        _assign_value(self._value.Controls, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NDeviceValue()

    def clone(self) -> NDevice:
        o = NDevice()
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
            return f"NDevice(defined)"
        return "NDevice(<undefined>)"


def make_ndevice_value(v: NDeviceValue) -> NDeviceValue:
    """Factory: create a NDeviceValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ndevice(v: NDeviceValue) -> NDevice:
    """Factory: create a defined NDevice from a NDeviceValue."""
    o = NDevice()
    o.set_value(v)
    return o

