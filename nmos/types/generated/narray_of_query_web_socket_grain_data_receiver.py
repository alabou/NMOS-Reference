"""Generated NMOS type: NArrayOfQueryWebSocketGrainDataReceiver. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.types.generated.nquery_web_socket_grain_data_receiver import NQueryWebSocketGrainDataReceiver, NQueryWebSocketGrainDataReceiverValue




class NArrayOfQueryWebSocketGrainDataReceiverValue:
    """Array value for NArrayOfQueryWebSocketGrainDataReceiver."""

    __slots__ = ("_inner",)

    def __init__(self) -> None:
        self._inner: list[NQueryWebSocketGrainDataReceiver] = []

    def get(self) -> list[NQueryWebSocketGrainDataReceiver]:
        return self._inner

    def set(self, v: list[NQueryWebSocketGrainDataReceiver]) -> None:
        self._inner = v

    def append(self, v: list[NQueryWebSocketGrainDataReceiver]) -> None:
        self._inner = self._inner + v

    def clone(self) -> NArrayOfQueryWebSocketGrainDataReceiverValue:
        o = NArrayOfQueryWebSocketGrainDataReceiverValue()
        o._inner = [item.clone() for item in self._inner]
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        engine.open_array(name)
        for item in self._inner:
            item.encode(engine, None)
        engine.close_array()

    def decode_value(self, data: Any) -> None:
        if not isinstance(data, list):
            raise InvalidData("expected array for NArrayOfQueryWebSocketGrainDataReceiver")
        self._inner = []
        for item_data in data:
            elem = NQueryWebSocketGrainDataReceiver()
            elem.decode(JsonEngine(), item_data)
            self._inner.append(elem)


class NArrayOfQueryWebSocketGrainDataReceiver:
    """Array type: NArrayOfQueryWebSocketGrainDataReceiver."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NArrayOfQueryWebSocketGrainDataReceiverValue = NArrayOfQueryWebSocketGrainDataReceiverValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> list[NQueryWebSocketGrainDataReceiver]:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.get()

    @value.setter
    def value(self, v: list[NQueryWebSocketGrainDataReceiver]) -> None:
        self._defined = True
        self._value.set(v)

    def get(self, default: list[NQueryWebSocketGrainDataReceiver] | None = None) -> list[NQueryWebSocketGrainDataReceiver] | None:
        return self._value.get() if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._value.set([])

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NArrayOfQueryWebSocketGrainDataReceiverValue()

    def clone(self) -> NArrayOfQueryWebSocketGrainDataReceiver:
        o = NArrayOfQueryWebSocketGrainDataReceiver()
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
            return f"NArrayOfQueryWebSocketGrainDataReceiver({len(self._value.get())} items)"
        return "NArrayOfQueryWebSocketGrainDataReceiver(<undefined>)"


def make_narrayofquerywebsocketgraindatareceiver(v: list[NQueryWebSocketGrainDataReceiver]) -> NArrayOfQueryWebSocketGrainDataReceiver:
    """Factory: create a defined NArrayOfQueryWebSocketGrainDataReceiver with the given list."""
    o = NArrayOfQueryWebSocketGrainDataReceiver()
    o.value = v
    return o

