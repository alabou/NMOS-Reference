"""Generated NMOS type: MvArrayOfAlertCapabilityDescriptor. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.types.generated.mv_alert_capability_descriptor import MvAlertCapabilityDescriptorValue




class MvArrayOfAlertCapabilityDescriptorValue:
    """Array value for MvArrayOfAlertCapabilityDescriptor."""

    __slots__ = ("_inner",)

    def __init__(self) -> None:
        self._inner: list[MvAlertCapabilityDescriptorValue] = []

    def get(self) -> list[MvAlertCapabilityDescriptorValue]:
        return self._inner

    def set(self, v: list[MvAlertCapabilityDescriptorValue]) -> None:
        self._inner = v

    def append(self, v: list[MvAlertCapabilityDescriptorValue]) -> None:
        self._inner = self._inner + v

    def clone(self) -> MvArrayOfAlertCapabilityDescriptorValue:
        o = MvArrayOfAlertCapabilityDescriptorValue()
        o._inner = [item.clone() for item in self._inner]
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        engine.open_array(name)
        for item in self._inner:
            item.encode(engine, None)
        engine.close_array()

    def decode_value(self, data: Any) -> None:
        if not isinstance(data, list):
            raise InvalidData("expected array for MvArrayOfAlertCapabilityDescriptor")
        self._inner = []
        for item_data in data:
            elem = MvAlertCapabilityDescriptorValue()
            elem.decode(JsonEngine(), item_data)
            self._inner.append(elem)


class MvArrayOfAlertCapabilityDescriptor:
    """Array type: MvArrayOfAlertCapabilityDescriptor."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: MvArrayOfAlertCapabilityDescriptorValue = MvArrayOfAlertCapabilityDescriptorValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> list[MvAlertCapabilityDescriptorValue]:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.get()

    @value.setter
    def value(self, v: list[MvAlertCapabilityDescriptorValue]) -> None:
        self._defined = True
        self._value.set(v)

    def get(self, default: list[MvAlertCapabilityDescriptorValue] | None = None) -> list[MvAlertCapabilityDescriptorValue] | None:
        return self._value.get() if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._value.set([])

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = MvArrayOfAlertCapabilityDescriptorValue()

    def clone(self) -> MvArrayOfAlertCapabilityDescriptor:
        o = MvArrayOfAlertCapabilityDescriptor()
        o._defined = self._defined
        o._value = self._value.clone()
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if not self._defined:
            return
        self._value.encode(engine, name)

    def decode_value(self, data: Any) -> None:
        self._value.decode_value(data)
        self._defined = True

    def __repr__(self) -> str:
        if self._defined:
            return f"MvArrayOfAlertCapabilityDescriptor({len(self._value.get())} items)"
        return "MvArrayOfAlertCapabilityDescriptor(<undefined>)"


def make_mvarrayofalertcapabilitydescriptor(v: list[MvAlertCapabilityDescriptorValue]) -> MvArrayOfAlertCapabilityDescriptor:
    """Factory: create a defined MvArrayOfAlertCapabilityDescriptor with the given list."""
    o = MvArrayOfAlertCapabilityDescriptor()
    o.value = v
    return o

