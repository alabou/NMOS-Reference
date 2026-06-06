"""Generated NMOS type: NNodeInterface. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NNullString, NString
from nmos.types.generated.nnetwork_device import NNetworkDevice, NNetworkDeviceValue
from nmos.validators import CheckChassisIdNullableString, CheckPortIdString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NNodeInterfaceEnums:
    """JSON property name enums for NNodeInterface."""
    ChassisId = EnumRegistry.get("chassis_id")
    PortId = EnumRegistry.get("port_id")
    Name = EnumRegistry.get("name")
    AttachedNetworkDevice = EnumRegistry.get("attached_network_device")
    pass


class NNodeInterfaceValue:
    """Inner value struct for NNodeInterface."""

    __slots__ = (
        "ChassisId",
        "PortId",
        "Name",
        "AttachedNetworkDevice",
    )

    def __init__(self) -> None:
        self.ChassisId: NNullString = NNullString()
        self.PortId: NString = NString()
        self.Name: NString = NString()
        self.AttachedNetworkDevice: NNetworkDevice = NNetworkDevice()

    def set_to_default(self) -> None:
        self.ChassisId.set_to_default()
        self.PortId.set_to_default()
        self.Name.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.ChassisId.defined:
            raise InvalidObject("missing required member ChassisId")
        if not self.PortId.defined:
            raise InvalidObject("missing required member PortId")
        if not self.Name.defined:
            raise InvalidObject("missing required member Name")
        if self.ChassisId.defined:
            CheckChassisIdNullableString(self.ChassisId)
        if self.PortId.defined:
            CheckPortIdString(self.PortId)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.ChassisId.encode(engine, NNodeInterfaceEnums.ChassisId)
        self.PortId.encode(engine, NNodeInterfaceEnums.PortId)
        self.Name.encode(engine, NNodeInterfaceEnums.Name)
        self.AttachedNetworkDevice.encode(engine, NNodeInterfaceEnums.AttachedNetworkDevice)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NNodeInterface")

        if NNodeInterfaceEnums.ChassisId.s in data:
            self.ChassisId.decode_value(data[NNodeInterfaceEnums.ChassisId.s])
        if NNodeInterfaceEnums.PortId.s in data:
            self.PortId.decode_value(data[NNodeInterfaceEnums.PortId.s])
        if NNodeInterfaceEnums.Name.s in data:
            self.Name.decode_value(data[NNodeInterfaceEnums.Name.s])
        if NNodeInterfaceEnums.AttachedNetworkDevice.s in data:
            self.AttachedNetworkDevice.decode_value(data[NNodeInterfaceEnums.AttachedNetworkDevice.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NNodeInterfaceValue:
        o = NNodeInterfaceValue()
        o.ChassisId = self.ChassisId.clone()
        o.PortId = self.PortId.clone()
        o.Name = self.Name.clone()
        o.AttachedNetworkDevice = self.AttachedNetworkDevice.clone()
        return o


class NNodeInterface:
    """Optional object type: NNodeInterface."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NNodeInterfaceValue = NNodeInterfaceValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NNodeInterfaceValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NNodeInterfaceValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NNodeInterfaceValue | None = None) -> NNodeInterfaceValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_ChassisId(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ChassisId

    def set_ChassisId(self, v: Any) -> None:
        assert self._defined, "NNodeInterface must be defined before setting ChassisId"
        _assign_value(self._value.ChassisId, v)

    def get_PortId(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.PortId

    def set_PortId(self, v: Any) -> None:
        assert self._defined, "NNodeInterface must be defined before setting PortId"
        _assign_value(self._value.PortId, v)

    def get_Name(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Name

    def set_Name(self, v: Any) -> None:
        assert self._defined, "NNodeInterface must be defined before setting Name"
        _assign_value(self._value.Name, v)

    def get_AttachedNetworkDevice(self) -> NNetworkDevice:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.AttachedNetworkDevice

    def set_AttachedNetworkDevice(self, v: Any) -> None:
        assert self._defined, "NNodeInterface must be defined before setting AttachedNetworkDevice"
        _assign_value(self._value.AttachedNetworkDevice, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NNodeInterfaceValue()

    def clone(self) -> NNodeInterface:
        o = NNodeInterface()
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
            return f"NNodeInterface(defined)"
        return "NNodeInterface(<undefined>)"


def make_nnodeinterface_value(v: NNodeInterfaceValue) -> NNodeInterfaceValue:
    """Factory: create a NNodeInterfaceValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nnodeinterface(v: NNodeInterfaceValue) -> NNodeInterface:
    """Factory: create a defined NNodeInterface from a NNodeInterfaceValue."""
    o = NNodeInterface()
    o.set_value(v)
    return o

