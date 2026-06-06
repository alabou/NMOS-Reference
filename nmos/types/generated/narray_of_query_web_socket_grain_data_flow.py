"""Generated NMOS type: NArrayOfQueryWebSocketGrainDataFlow. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.types.generated.nquery_web_socket_grain_data_flow import NQueryWebSocketGrainDataFlow, NQueryWebSocketGrainDataFlowValue




class NArrayOfQueryWebSocketGrainDataFlowValue:
    """Array value for NArrayOfQueryWebSocketGrainDataFlow."""

    __slots__ = ("_inner",)

    def __init__(self) -> None:
        self._inner: list[NQueryWebSocketGrainDataFlow] = []

    def get(self) -> list[NQueryWebSocketGrainDataFlow]:
        return self._inner

    def set(self, v: list[NQueryWebSocketGrainDataFlow]) -> None:
        self._inner = v

    def append(self, v: list[NQueryWebSocketGrainDataFlow]) -> None:
        self._inner = self._inner + v

    def clone(self) -> NArrayOfQueryWebSocketGrainDataFlowValue:
        o = NArrayOfQueryWebSocketGrainDataFlowValue()
        o._inner = [item.clone() for item in self._inner]
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        engine.open_array(name)
        for item in self._inner:
            item.encode(engine, None)
        engine.close_array()

    def decode_value(self, data: Any) -> None:
        if not isinstance(data, list):
            raise InvalidData("expected array for NArrayOfQueryWebSocketGrainDataFlow")
        self._inner = []
        for item_data in data:
            elem = NQueryWebSocketGrainDataFlow()
            elem.decode(JsonEngine(), item_data)
            self._inner.append(elem)


class NArrayOfQueryWebSocketGrainDataFlow:
    """Array type: NArrayOfQueryWebSocketGrainDataFlow."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NArrayOfQueryWebSocketGrainDataFlowValue = NArrayOfQueryWebSocketGrainDataFlowValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> list[NQueryWebSocketGrainDataFlow]:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.get()

    @value.setter
    def value(self, v: list[NQueryWebSocketGrainDataFlow]) -> None:
        self._defined = True
        self._value.set(v)

    def get(self, default: list[NQueryWebSocketGrainDataFlow] | None = None) -> list[NQueryWebSocketGrainDataFlow] | None:
        return self._value.get() if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._value.set([])

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NArrayOfQueryWebSocketGrainDataFlowValue()

    def clone(self) -> NArrayOfQueryWebSocketGrainDataFlow:
        o = NArrayOfQueryWebSocketGrainDataFlow()
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
            return f"NArrayOfQueryWebSocketGrainDataFlow({len(self._value.get())} items)"
        return "NArrayOfQueryWebSocketGrainDataFlow(<undefined>)"


def make_narrayofquerywebsocketgraindataflow(v: list[NQueryWebSocketGrainDataFlow]) -> NArrayOfQueryWebSocketGrainDataFlow:
    """Factory: create a defined NArrayOfQueryWebSocketGrainDataFlow with the given list."""
    o = NArrayOfQueryWebSocketGrainDataFlow()
    o.value = v
    return o

