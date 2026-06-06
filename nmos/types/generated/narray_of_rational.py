"""Generated NMOS type: NArrayOfRational. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.types.generated.nrational import NRationalValue




class NArrayOfRationalValue:
    """Array value for NArrayOfRational."""

    __slots__ = ("_inner",)

    def __init__(self) -> None:
        self._inner: list[NRationalValue] = []

    def get(self) -> list[NRationalValue]:
        return self._inner

    def set(self, v: list[NRationalValue]) -> None:
        self._inner = v

    def append(self, v: list[NRationalValue]) -> None:
        self._inner = self._inner + v

    def clone(self) -> NArrayOfRationalValue:
        o = NArrayOfRationalValue()
        o._inner = [item.clone() for item in self._inner]
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        engine.open_array(name)
        for item in self._inner:
            item.encode(engine, None)
        engine.close_array()

    def decode_value(self, data: Any) -> None:
        if not isinstance(data, list):
            raise InvalidData("expected array for NArrayOfRational")
        self._inner = []
        for item_data in data:
            elem = NRationalValue()
            elem.decode(JsonEngine(), item_data)
            self._inner.append(elem)


class NArrayOfRational:
    """Array type: NArrayOfRational."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NArrayOfRationalValue = NArrayOfRationalValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> list[NRationalValue]:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.get()

    @value.setter
    def value(self, v: list[NRationalValue]) -> None:
        self._defined = True
        self._value.set(v)

    def get(self, default: list[NRationalValue] | None = None) -> list[NRationalValue] | None:
        return self._value.get() if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._value.set([])

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NArrayOfRationalValue()

    def clone(self) -> NArrayOfRational:
        o = NArrayOfRational()
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
            return f"NArrayOfRational({len(self._value.get())} items)"
        return "NArrayOfRational(<undefined>)"


def make_narrayofrational(v: list[NRationalValue]) -> NArrayOfRational:
    """Factory: create a defined NArrayOfRational with the given list."""
    o = NArrayOfRational()
    o.value = v
    return o

