"""Generated NMOS type: NcDeviceManager. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString, NNullString, NInt
from nmos.types.generated.nc_manager import NcManager, NcManagerValue
from nmos.types.generated.nc_manufacturer import NcManufacturer, NcManufacturerValue
from nmos.types.generated.nc_product import NcProduct, NcProductValue
from nmos.types.generated.nc_device_operational_state import NcDeviceOperationalState, NcDeviceOperationalStateValue
from nmos.validators import CheckResetCause

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcDeviceManagerEnums:
    """JSON property name enums for NcDeviceManager."""
    NcVersion = EnumRegistry.get("ncVersion")
    Manufacturer = EnumRegistry.get("manufacturer")
    Product = EnumRegistry.get("product")
    SerialNumber = EnumRegistry.get("serialNumber")
    UserInventoryCode = EnumRegistry.get("userInventoryCode")
    DeviceName = EnumRegistry.get("deviceName")
    DeviceRole = EnumRegistry.get("deviceRole")
    OperationalState = EnumRegistry.get("operationalState")
    ResetCause = EnumRegistry.get("resetCause")
    Message = EnumRegistry.get("message")
    pass


class NcDeviceManagerValue:
    """Inner value struct for NcDeviceManager."""

    __slots__ = (
        "Base",
        "NcVersion",
        "Manufacturer",
        "Product",
        "SerialNumber",
        "UserInventoryCode",
        "DeviceName",
        "DeviceRole",
        "OperationalState",
        "ResetCause",
        "Message",
    )

    def __init__(self) -> None:
        self.Base: NcManagerValue = NcManagerValue()
        self.NcVersion: NString = NString()
        self.Manufacturer: NcManufacturer = NcManufacturer()
        self.Product: NcProduct = NcProduct()
        self.SerialNumber: NString = NString()
        self.UserInventoryCode: NNullString = NNullString()
        self.DeviceName: NNullString = NNullString()
        self.DeviceRole: NNullString = NNullString()
        self.OperationalState: NcDeviceOperationalState = NcDeviceOperationalState()
        self.ResetCause: NInt = NInt()
        self.Message: NNullString = NNullString()

    def set_to_default(self) -> None:
        self.Base = NcManagerValue()
        self.Base.set_to_default()
        self.NcVersion.set_to_default()
        self.Manufacturer.set_to_default()
        self.Product.set_to_default()
        self.SerialNumber.set_to_default()
        self.UserInventoryCode.set_to_default()
        self.DeviceName.set_to_default()
        self.DeviceRole.set_to_default()
        self.OperationalState.set_to_default()
        self.ResetCause.set_to_default()
        self.Message.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.NcVersion.defined:
            raise InvalidObject("missing required member NcVersion")
        if not self.Manufacturer.defined:
            raise InvalidObject("missing required member Manufacturer")
        if not self.Product.defined:
            raise InvalidObject("missing required member Product")
        if not self.SerialNumber.defined:
            raise InvalidObject("missing required member SerialNumber")
        if not self.UserInventoryCode.defined:
            raise InvalidObject("missing required member UserInventoryCode")
        if not self.DeviceName.defined:
            raise InvalidObject("missing required member DeviceName")
        if not self.DeviceRole.defined:
            raise InvalidObject("missing required member DeviceRole")
        if not self.OperationalState.defined:
            raise InvalidObject("missing required member OperationalState")
        if not self.ResetCause.defined:
            raise InvalidObject("missing required member ResetCause")
        if not self.Message.defined:
            raise InvalidObject("missing required member Message")
        if self.ResetCause.defined:
            CheckResetCause(self.ResetCause)
        self.Base.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Base.encode(engine, None)
        self.NcVersion.encode(engine, NcDeviceManagerEnums.NcVersion)
        self.Manufacturer.encode(engine, NcDeviceManagerEnums.Manufacturer)
        self.Product.encode(engine, NcDeviceManagerEnums.Product)
        self.SerialNumber.encode(engine, NcDeviceManagerEnums.SerialNumber)
        self.UserInventoryCode.encode(engine, NcDeviceManagerEnums.UserInventoryCode)
        self.DeviceName.encode(engine, NcDeviceManagerEnums.DeviceName)
        self.DeviceRole.encode(engine, NcDeviceManagerEnums.DeviceRole)
        self.OperationalState.encode(engine, NcDeviceManagerEnums.OperationalState)
        self.ResetCause.encode(engine, NcDeviceManagerEnums.ResetCause)
        self.Message.encode(engine, NcDeviceManagerEnums.Message)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcDeviceManager")

        self.Base.decode(engine, data)
        if NcDeviceManagerEnums.NcVersion.s in data:
            self.NcVersion.decode_value(data[NcDeviceManagerEnums.NcVersion.s])
        if NcDeviceManagerEnums.Manufacturer.s in data:
            self.Manufacturer.decode_value(data[NcDeviceManagerEnums.Manufacturer.s])
        if NcDeviceManagerEnums.Product.s in data:
            self.Product.decode_value(data[NcDeviceManagerEnums.Product.s])
        if NcDeviceManagerEnums.SerialNumber.s in data:
            self.SerialNumber.decode_value(data[NcDeviceManagerEnums.SerialNumber.s])
        if NcDeviceManagerEnums.UserInventoryCode.s in data:
            self.UserInventoryCode.decode_value(data[NcDeviceManagerEnums.UserInventoryCode.s])
        if NcDeviceManagerEnums.DeviceName.s in data:
            self.DeviceName.decode_value(data[NcDeviceManagerEnums.DeviceName.s])
        if NcDeviceManagerEnums.DeviceRole.s in data:
            self.DeviceRole.decode_value(data[NcDeviceManagerEnums.DeviceRole.s])
        if NcDeviceManagerEnums.OperationalState.s in data:
            self.OperationalState.decode_value(data[NcDeviceManagerEnums.OperationalState.s])
        if NcDeviceManagerEnums.ResetCause.s in data:
            self.ResetCause.decode_value(data[NcDeviceManagerEnums.ResetCause.s])
        if NcDeviceManagerEnums.Message.s in data:
            self.Message.decode_value(data[NcDeviceManagerEnums.Message.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcDeviceManagerValue:
        o = NcDeviceManagerValue()
        o.Base = self.Base.clone()
        o.NcVersion = self.NcVersion.clone()
        o.Manufacturer = self.Manufacturer.clone()
        o.Product = self.Product.clone()
        o.SerialNumber = self.SerialNumber.clone()
        o.UserInventoryCode = self.UserInventoryCode.clone()
        o.DeviceName = self.DeviceName.clone()
        o.DeviceRole = self.DeviceRole.clone()
        o.OperationalState = self.OperationalState.clone()
        o.ResetCause = self.ResetCause.clone()
        o.Message = self.Message.clone()
        return o


class NcDeviceManager:
    """Optional object type: NcDeviceManager."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcDeviceManagerValue = NcDeviceManagerValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcDeviceManagerValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcDeviceManagerValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcDeviceManagerValue | None = None) -> NcDeviceManagerValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Base(self) -> NcManagerValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Base

    def set_Base(self, v: NcManagerValue) -> None:
        assert self._defined, "NcDeviceManager must be defined before setting Base"
        self._value.Base = v.clone()  # copy to match Go's value semantics

    def get_NcVersion(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.NcVersion

    def set_NcVersion(self, v: Any) -> None:
        assert self._defined, "NcDeviceManager must be defined before setting NcVersion"
        _assign_value(self._value.NcVersion, v)

    def get_Manufacturer(self) -> NcManufacturer:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Manufacturer

    def set_Manufacturer(self, v: Any) -> None:
        assert self._defined, "NcDeviceManager must be defined before setting Manufacturer"
        _assign_value(self._value.Manufacturer, v)

    def get_Product(self) -> NcProduct:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Product

    def set_Product(self, v: Any) -> None:
        assert self._defined, "NcDeviceManager must be defined before setting Product"
        _assign_value(self._value.Product, v)

    def get_SerialNumber(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SerialNumber

    def set_SerialNumber(self, v: Any) -> None:
        assert self._defined, "NcDeviceManager must be defined before setting SerialNumber"
        _assign_value(self._value.SerialNumber, v)

    def get_UserInventoryCode(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.UserInventoryCode

    def set_UserInventoryCode(self, v: Any) -> None:
        assert self._defined, "NcDeviceManager must be defined before setting UserInventoryCode"
        _assign_value(self._value.UserInventoryCode, v)

    def get_DeviceName(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.DeviceName

    def set_DeviceName(self, v: Any) -> None:
        assert self._defined, "NcDeviceManager must be defined before setting DeviceName"
        _assign_value(self._value.DeviceName, v)

    def get_DeviceRole(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.DeviceRole

    def set_DeviceRole(self, v: Any) -> None:
        assert self._defined, "NcDeviceManager must be defined before setting DeviceRole"
        _assign_value(self._value.DeviceRole, v)

    def get_OperationalState(self) -> NcDeviceOperationalState:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.OperationalState

    def set_OperationalState(self, v: Any) -> None:
        assert self._defined, "NcDeviceManager must be defined before setting OperationalState"
        _assign_value(self._value.OperationalState, v)

    def get_ResetCause(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ResetCause

    def set_ResetCause(self, v: Any) -> None:
        assert self._defined, "NcDeviceManager must be defined before setting ResetCause"
        _assign_value(self._value.ResetCause, v)

    def get_Message(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Message

    def set_Message(self, v: Any) -> None:
        assert self._defined, "NcDeviceManager must be defined before setting Message"
        _assign_value(self._value.Message, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcDeviceManagerValue()

    def clone(self) -> NcDeviceManager:
        o = NcDeviceManager()
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
            return f"NcDeviceManager(defined)"
        return "NcDeviceManager(<undefined>)"


def make_ncdevicemanager_value(v: NcDeviceManagerValue) -> NcDeviceManagerValue:
    """Factory: create a NcDeviceManagerValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ncdevicemanager(v: NcDeviceManagerValue) -> NcDeviceManager:
    """Factory: create a defined NcDeviceManager from a NcDeviceManagerValue."""
    o = NcDeviceManager()
    o.set_value(v)
    return o

