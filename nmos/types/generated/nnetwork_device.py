"""Generated NMOS type: NNetworkDevice. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NNullString, NString
from nmos.validators import CheckChassisIdNullableString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NNetworkDeviceEnums:
    """JSON property name enums for NNetworkDevice."""
    ChassisId = EnumRegistry.get("chassis_id")
    PortId = EnumRegistry.get("port_id")
    pass


class NNetworkDeviceValue:
    """Inner value struct for NNetworkDevice."""

    __slots__ = (
        "ChassisId",
        "PortId",
    )

    def __init__(self) -> None:
        self.ChassisId: NNullString = NNullString()
        self.PortId: NString = NString()

    def set_to_default(self) -> None:
        self.ChassisId.set_to_default()
        self.PortId.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.ChassisId.defined:
            raise InvalidObject("missing required member ChassisId")
        if not self.PortId.defined:
            raise InvalidObject("missing required member PortId")
        if self.ChassisId.defined:
            CheckChassisIdNullableString(self.ChassisId)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.ChassisId.encode(engine, NNetworkDeviceEnums.ChassisId)
        self.PortId.encode(engine, NNetworkDeviceEnums.PortId)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NNetworkDevice")

        if NNetworkDeviceEnums.ChassisId.s in data:
            self.ChassisId.decode_value(data[NNetworkDeviceEnums.ChassisId.s])
        if NNetworkDeviceEnums.PortId.s in data:
            self.PortId.decode_value(data[NNetworkDeviceEnums.PortId.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NNetworkDeviceValue:
        o = NNetworkDeviceValue()
        o.ChassisId = self.ChassisId.clone()
        o.PortId = self.PortId.clone()
        return o


class NNetworkDevice:
    """Optional object type: NNetworkDevice."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NNetworkDeviceValue = NNetworkDeviceValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NNetworkDeviceValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NNetworkDeviceValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NNetworkDeviceValue | None = None) -> NNetworkDeviceValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_ChassisId(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ChassisId

    def set_ChassisId(self, v: Any) -> None:
        assert self._defined, "NNetworkDevice must be defined before setting ChassisId"
        _assign_value(self._value.ChassisId, v)

    def get_PortId(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.PortId

    def set_PortId(self, v: Any) -> None:
        assert self._defined, "NNetworkDevice must be defined before setting PortId"
        _assign_value(self._value.PortId, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NNetworkDeviceValue()

    def clone(self) -> NNetworkDevice:
        o = NNetworkDevice()
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
            return f"NNetworkDevice(defined)"
        return "NNetworkDevice(<undefined>)"


def make_nnetworkdevice_value(v: NNetworkDeviceValue) -> NNetworkDeviceValue:
    """Factory: create a NNetworkDeviceValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nnetworkdevice(v: NNetworkDeviceValue) -> NNetworkDevice:
    """Factory: create a defined NNetworkDevice from a NNetworkDeviceValue."""
    o = NNetworkDevice()
    o.set_value(v)
    return o

