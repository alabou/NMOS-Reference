"""Generated NMOS type: NcDeviceOperationalState. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NInt, NNullString
from nmos.validators import CheckDeviceGenericState

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcDeviceOperationalStateEnums:
    """JSON property name enums for NcDeviceOperationalState."""
    Generic = EnumRegistry.get("generic")
    DeviceSpecificDetails = EnumRegistry.get("deviceSpecificDetails")
    pass


class NcDeviceOperationalStateValue:
    """Inner value struct for NcDeviceOperationalState."""

    __slots__ = (
        "Generic",
        "DeviceSpecificDetails",
    )

    def __init__(self) -> None:
        self.Generic: NInt = NInt()
        self.DeviceSpecificDetails: NNullString = NNullString()

    def set_to_default(self) -> None:
        self.Generic.set_to_default()
        self.DeviceSpecificDetails.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Generic.defined:
            raise InvalidObject("missing required member Generic")
        if not self.DeviceSpecificDetails.defined:
            raise InvalidObject("missing required member DeviceSpecificDetails")
        if self.Generic.defined:
            CheckDeviceGenericState(self.Generic)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Generic.encode(engine, NcDeviceOperationalStateEnums.Generic)
        self.DeviceSpecificDetails.encode(engine, NcDeviceOperationalStateEnums.DeviceSpecificDetails)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcDeviceOperationalState")

        if NcDeviceOperationalStateEnums.Generic.s in data:
            self.Generic.decode_value(data[NcDeviceOperationalStateEnums.Generic.s])
        if NcDeviceOperationalStateEnums.DeviceSpecificDetails.s in data:
            self.DeviceSpecificDetails.decode_value(data[NcDeviceOperationalStateEnums.DeviceSpecificDetails.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcDeviceOperationalStateValue:
        o = NcDeviceOperationalStateValue()
        o.Generic = self.Generic.clone()
        o.DeviceSpecificDetails = self.DeviceSpecificDetails.clone()
        return o


class NcDeviceOperationalState:
    """Optional object type: NcDeviceOperationalState."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcDeviceOperationalStateValue = NcDeviceOperationalStateValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcDeviceOperationalStateValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcDeviceOperationalStateValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcDeviceOperationalStateValue | None = None) -> NcDeviceOperationalStateValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Generic(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Generic

    def set_Generic(self, v: Any) -> None:
        assert self._defined, "NcDeviceOperationalState must be defined before setting Generic"
        _assign_value(self._value.Generic, v)

    def get_DeviceSpecificDetails(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.DeviceSpecificDetails

    def set_DeviceSpecificDetails(self, v: Any) -> None:
        assert self._defined, "NcDeviceOperationalState must be defined before setting DeviceSpecificDetails"
        _assign_value(self._value.DeviceSpecificDetails, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcDeviceOperationalStateValue()

    def clone(self) -> NcDeviceOperationalState:
        o = NcDeviceOperationalState()
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
            return f"NcDeviceOperationalState(defined)"
        return "NcDeviceOperationalState(<undefined>)"


def make_ncdeviceoperationalstate_value(v: NcDeviceOperationalStateValue) -> NcDeviceOperationalStateValue:
    """Factory: create a NcDeviceOperationalStateValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ncdeviceoperationalstate(v: NcDeviceOperationalStateValue) -> NcDeviceOperationalState:
    """Factory: create a defined NcDeviceOperationalState from a NcDeviceOperationalStateValue."""
    o = NcDeviceOperationalState()
    o.set_value(v)
    return o

