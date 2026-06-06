"""Generated NMOS type: NArrayOfSrtTransportConstraints. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.types.generated.nsrt_transport_constraints import NSrtTransportConstraintsValue




class NArrayOfSrtTransportConstraintsValue:
    """Array value for NArrayOfSrtTransportConstraints."""

    __slots__ = ("_inner",)

    def __init__(self) -> None:
        self._inner: list[NSrtTransportConstraintsValue] = []

    def get(self) -> list[NSrtTransportConstraintsValue]:
        return self._inner

    def set(self, v: list[NSrtTransportConstraintsValue]) -> None:
        self._inner = v

    def append(self, v: list[NSrtTransportConstraintsValue]) -> None:
        self._inner = self._inner + v

    def clone(self) -> NArrayOfSrtTransportConstraintsValue:
        o = NArrayOfSrtTransportConstraintsValue()
        o._inner = [item.clone() for item in self._inner]
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        engine.open_array(name)
        for item in self._inner:
            item.encode(engine, None)
        engine.close_array()

    def decode_value(self, data: Any) -> None:
        if not isinstance(data, list):
            raise InvalidData("expected array for NArrayOfSrtTransportConstraints")
        self._inner = []
        for item_data in data:
            elem = NSrtTransportConstraintsValue()
            elem.decode(JsonEngine(), item_data)
            self._inner.append(elem)


class NArrayOfSrtTransportConstraints:
    """Array type: NArrayOfSrtTransportConstraints."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NArrayOfSrtTransportConstraintsValue = NArrayOfSrtTransportConstraintsValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> list[NSrtTransportConstraintsValue]:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.get()

    @value.setter
    def value(self, v: list[NSrtTransportConstraintsValue]) -> None:
        self._defined = True
        self._value.set(v)

    def get(self, default: list[NSrtTransportConstraintsValue] | None = None) -> list[NSrtTransportConstraintsValue] | None:
        return self._value.get() if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._value.set([])

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NArrayOfSrtTransportConstraintsValue()

    def clone(self) -> NArrayOfSrtTransportConstraints:
        o = NArrayOfSrtTransportConstraints()
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
            return f"NArrayOfSrtTransportConstraints({len(self._value.get())} items)"
        return "NArrayOfSrtTransportConstraints(<undefined>)"


def make_narrayofsrttransportconstraints(v: list[NSrtTransportConstraintsValue]) -> NArrayOfSrtTransportConstraints:
    """Factory: create a defined NArrayOfSrtTransportConstraints with the given list."""
    o = NArrayOfSrtTransportConstraints()
    o.value = v
    return o

