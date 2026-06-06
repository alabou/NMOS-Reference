"""Generated NMOS type: MvAlertManager. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NInt
from nmos.types.generated.nc_manager import NcManager, NcManagerValue
from nmos.types.generated.mv_array_of_alert_capability_descriptor import MvArrayOfAlertCapabilityDescriptor, MvArrayOfAlertCapabilityDescriptorValue
from nmos.types.generated.mv_array_of_alert_descriptor import MvArrayOfAlertDescriptor, MvArrayOfAlertDescriptorValue
from nmos.types.generated.mv_alert_event_data import MvAlertEventData, MvAlertEventDataValue

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class MvAlertManagerEnums:
    """JSON property name enums for MvAlertManager."""
    AlertPeriod = EnumRegistry.get("alertPeriod")
    RefreshPeriod = EnumRegistry.get("refreshPeriod")
    ClearPeriod = EnumRegistry.get("clearPeriod")
    AlertCapabilities = EnumRegistry.get("alertCapabilities")
    AlertDescriptors = EnumRegistry.get("alertDescriptors")
    Alert = EnumRegistry.get("alert")
    pass


class MvAlertManagerValue:
    """Inner value struct for MvAlertManager."""

    __slots__ = (
        "Base",
        "AlertPeriod",
        "RefreshPeriod",
        "ClearPeriod",
        "AlertCapabilities",
        "AlertDescriptors",
        "Alert",
    )

    def __init__(self) -> None:
        self.Base: NcManagerValue = NcManagerValue()
        self.AlertPeriod: NInt = NInt()
        self.RefreshPeriod: NInt = NInt()
        self.ClearPeriod: NInt = NInt()
        self.AlertCapabilities: MvArrayOfAlertCapabilityDescriptor = MvArrayOfAlertCapabilityDescriptor()
        self.AlertDescriptors: MvArrayOfAlertDescriptor = MvArrayOfAlertDescriptor()
        self.Alert: MvAlertEventData = MvAlertEventData()

    def set_to_default(self) -> None:
        self.Base = NcManagerValue()
        self.Base.set_to_default()
        self.AlertPeriod.set_to_default()
        self.RefreshPeriod.set_to_default()
        self.ClearPeriod.set_to_default()
        self.AlertCapabilities.set_to_default()
        self.AlertDescriptors.set_to_default()
        self.Alert.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.AlertPeriod.defined:
            raise InvalidObject("missing required member AlertPeriod")
        if not self.RefreshPeriod.defined:
            raise InvalidObject("missing required member RefreshPeriod")
        if not self.ClearPeriod.defined:
            raise InvalidObject("missing required member ClearPeriod")
        if not self.AlertCapabilities.defined:
            raise InvalidObject("missing required member AlertCapabilities")
        if not self.AlertDescriptors.defined:
            raise InvalidObject("missing required member AlertDescriptors")
        if not self.Alert.defined:
            raise InvalidObject("missing required member Alert")
        self.Base.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Base.encode(engine, None)
        self.AlertPeriod.encode(engine, MvAlertManagerEnums.AlertPeriod)
        self.RefreshPeriod.encode(engine, MvAlertManagerEnums.RefreshPeriod)
        self.ClearPeriod.encode(engine, MvAlertManagerEnums.ClearPeriod)
        self.AlertCapabilities.encode(engine, MvAlertManagerEnums.AlertCapabilities)
        self.AlertDescriptors.encode(engine, MvAlertManagerEnums.AlertDescriptors)
        self.Alert.encode(engine, MvAlertManagerEnums.Alert)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for MvAlertManager")

        self.Base.decode(engine, data)
        if MvAlertManagerEnums.AlertPeriod.s in data:
            self.AlertPeriod.decode_value(data[MvAlertManagerEnums.AlertPeriod.s])
        if MvAlertManagerEnums.RefreshPeriod.s in data:
            self.RefreshPeriod.decode_value(data[MvAlertManagerEnums.RefreshPeriod.s])
        if MvAlertManagerEnums.ClearPeriod.s in data:
            self.ClearPeriod.decode_value(data[MvAlertManagerEnums.ClearPeriod.s])
        if MvAlertManagerEnums.AlertCapabilities.s in data:
            self.AlertCapabilities.decode_value(data[MvAlertManagerEnums.AlertCapabilities.s])
        if MvAlertManagerEnums.AlertDescriptors.s in data:
            self.AlertDescriptors.decode_value(data[MvAlertManagerEnums.AlertDescriptors.s])
        if MvAlertManagerEnums.Alert.s in data:
            self.Alert.decode_value(data[MvAlertManagerEnums.Alert.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> MvAlertManagerValue:
        o = MvAlertManagerValue()
        o.Base = self.Base.clone()
        o.AlertPeriod = self.AlertPeriod.clone()
        o.RefreshPeriod = self.RefreshPeriod.clone()
        o.ClearPeriod = self.ClearPeriod.clone()
        o.AlertCapabilities = self.AlertCapabilities.clone()
        o.AlertDescriptors = self.AlertDescriptors.clone()
        o.Alert = self.Alert.clone()
        return o


class MvAlertManager:
    """Optional object type: MvAlertManager."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: MvAlertManagerValue = MvAlertManagerValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> MvAlertManagerValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: MvAlertManagerValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: MvAlertManagerValue | None = None) -> MvAlertManagerValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Base(self) -> NcManagerValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Base

    def set_Base(self, v: NcManagerValue) -> None:
        assert self._defined, "MvAlertManager must be defined before setting Base"
        self._value.Base = v.clone()  # copy to match Go's value semantics

    def get_AlertPeriod(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.AlertPeriod

    def set_AlertPeriod(self, v: Any) -> None:
        assert self._defined, "MvAlertManager must be defined before setting AlertPeriod"
        _assign_value(self._value.AlertPeriod, v)

    def get_RefreshPeriod(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.RefreshPeriod

    def set_RefreshPeriod(self, v: Any) -> None:
        assert self._defined, "MvAlertManager must be defined before setting RefreshPeriod"
        _assign_value(self._value.RefreshPeriod, v)

    def get_ClearPeriod(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ClearPeriod

    def set_ClearPeriod(self, v: Any) -> None:
        assert self._defined, "MvAlertManager must be defined before setting ClearPeriod"
        _assign_value(self._value.ClearPeriod, v)

    def get_AlertCapabilities(self) -> MvArrayOfAlertCapabilityDescriptor:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.AlertCapabilities

    def set_AlertCapabilities(self, v: Any) -> None:
        assert self._defined, "MvAlertManager must be defined before setting AlertCapabilities"
        _assign_value(self._value.AlertCapabilities, v)

    def get_AlertDescriptors(self) -> MvArrayOfAlertDescriptor:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.AlertDescriptors

    def set_AlertDescriptors(self, v: Any) -> None:
        assert self._defined, "MvAlertManager must be defined before setting AlertDescriptors"
        _assign_value(self._value.AlertDescriptors, v)

    def get_Alert(self) -> MvAlertEventData:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Alert

    def set_Alert(self, v: Any) -> None:
        assert self._defined, "MvAlertManager must be defined before setting Alert"
        _assign_value(self._value.Alert, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = MvAlertManagerValue()

    def clone(self) -> MvAlertManager:
        o = MvAlertManager()
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
            return f"MvAlertManager(defined)"
        return "MvAlertManager(<undefined>)"


def make_mvalertmanager_value(v: MvAlertManagerValue) -> MvAlertManagerValue:
    """Factory: create a MvAlertManagerValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_mvalertmanager(v: MvAlertManagerValue) -> MvAlertManager:
    """Factory: create a defined MvAlertManager from a MvAlertManagerValue."""
    o = MvAlertManager()
    o.set_value(v)
    return o

