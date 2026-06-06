"""Generated NMOS type: NArrayOfRtspReceiverTransportParams. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.types.generated.nrtsp_receiver_transport_params import NRtspReceiverTransportParamsValue




class NArrayOfRtspReceiverTransportParamsValue:
    """Array value for NArrayOfRtspReceiverTransportParams."""

    __slots__ = ("_inner",)

    def __init__(self) -> None:
        self._inner: list[NRtspReceiverTransportParamsValue] = []

    def get(self) -> list[NRtspReceiverTransportParamsValue]:
        return self._inner

    def set(self, v: list[NRtspReceiverTransportParamsValue]) -> None:
        self._inner = v

    def append(self, v: list[NRtspReceiverTransportParamsValue]) -> None:
        self._inner = self._inner + v

    def clone(self) -> NArrayOfRtspReceiverTransportParamsValue:
        o = NArrayOfRtspReceiverTransportParamsValue()
        o._inner = [item.clone() for item in self._inner]
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        engine.open_array(name)
        for item in self._inner:
            item.encode(engine, None)
        engine.close_array()

    def decode_value(self, data: Any) -> None:
        if not isinstance(data, list):
            raise InvalidData("expected array for NArrayOfRtspReceiverTransportParams")
        self._inner = []
        for item_data in data:
            elem = NRtspReceiverTransportParamsValue()
            elem.decode(JsonEngine(), item_data)
            self._inner.append(elem)


class NArrayOfRtspReceiverTransportParams:
    """Array type: NArrayOfRtspReceiverTransportParams."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NArrayOfRtspReceiverTransportParamsValue = NArrayOfRtspReceiverTransportParamsValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> list[NRtspReceiverTransportParamsValue]:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.get()

    @value.setter
    def value(self, v: list[NRtspReceiverTransportParamsValue]) -> None:
        self._defined = True
        self._value.set(v)

    def get(self, default: list[NRtspReceiverTransportParamsValue] | None = None) -> list[NRtspReceiverTransportParamsValue] | None:
        return self._value.get() if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._value.set([])

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NArrayOfRtspReceiverTransportParamsValue()

    def clone(self) -> NArrayOfRtspReceiverTransportParams:
        o = NArrayOfRtspReceiverTransportParams()
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
            return f"NArrayOfRtspReceiverTransportParams({len(self._value.get())} items)"
        return "NArrayOfRtspReceiverTransportParams(<undefined>)"


def make_narrayofrtspreceivertransportparams(v: list[NRtspReceiverTransportParamsValue]) -> NArrayOfRtspReceiverTransportParams:
    """Factory: create a defined NArrayOfRtspReceiverTransportParams with the given list."""
    o = NArrayOfRtspReceiverTransportParams()
    o.value = v
    return o

