"""Generated NMOS type: NArrayOfUsbReceiverTransportParams. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.types.generated.nusb_receiver_transport_params import NUsbReceiverTransportParamsValue




class NArrayOfUsbReceiverTransportParamsValue:
    """Array value for NArrayOfUsbReceiverTransportParams."""

    __slots__ = ("_inner",)

    def __init__(self) -> None:
        self._inner: list[NUsbReceiverTransportParamsValue] = []

    def get(self) -> list[NUsbReceiverTransportParamsValue]:
        return self._inner

    def set(self, v: list[NUsbReceiverTransportParamsValue]) -> None:
        self._inner = v

    def append(self, v: list[NUsbReceiverTransportParamsValue]) -> None:
        self._inner = self._inner + v

    def clone(self) -> NArrayOfUsbReceiverTransportParamsValue:
        o = NArrayOfUsbReceiverTransportParamsValue()
        o._inner = [item.clone() for item in self._inner]
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        engine.open_array(name)
        for item in self._inner:
            item.encode(engine, None)
        engine.close_array()

    def decode_value(self, data: Any) -> None:
        if not isinstance(data, list):
            raise InvalidData("expected array for NArrayOfUsbReceiverTransportParams")
        self._inner = []
        for item_data in data:
            elem = NUsbReceiverTransportParamsValue()
            elem.decode(JsonEngine(), item_data)
            self._inner.append(elem)


class NArrayOfUsbReceiverTransportParams:
    """Array type: NArrayOfUsbReceiverTransportParams."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NArrayOfUsbReceiverTransportParamsValue = NArrayOfUsbReceiverTransportParamsValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> list[NUsbReceiverTransportParamsValue]:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.get()

    @value.setter
    def value(self, v: list[NUsbReceiverTransportParamsValue]) -> None:
        self._defined = True
        self._value.set(v)

    def get(self, default: list[NUsbReceiverTransportParamsValue] | None = None) -> list[NUsbReceiverTransportParamsValue] | None:
        return self._value.get() if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._value.set([])

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NArrayOfUsbReceiverTransportParamsValue()

    def clone(self) -> NArrayOfUsbReceiverTransportParams:
        o = NArrayOfUsbReceiverTransportParams()
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
            return f"NArrayOfUsbReceiverTransportParams({len(self._value.get())} items)"
        return "NArrayOfUsbReceiverTransportParams(<undefined>)"


def make_narrayofusbreceivertransportparams(v: list[NUsbReceiverTransportParamsValue]) -> NArrayOfUsbReceiverTransportParams:
    """Factory: create a defined NArrayOfUsbReceiverTransportParams with the given list."""
    o = NArrayOfUsbReceiverTransportParams()
    o.value = v
    return o

