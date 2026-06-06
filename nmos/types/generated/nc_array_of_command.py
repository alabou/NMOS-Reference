"""Generated NMOS type: NcArrayOfCommand. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.types.generated.nc_command import NcCommandValue




class NcArrayOfCommandValue:
    """Array value for NcArrayOfCommand."""

    __slots__ = ("_inner",)

    def __init__(self) -> None:
        self._inner: list[NcCommandValue] = []

    def get(self) -> list[NcCommandValue]:
        return self._inner

    def set(self, v: list[NcCommandValue]) -> None:
        self._inner = v

    def append(self, v: list[NcCommandValue]) -> None:
        self._inner = self._inner + v

    def clone(self) -> NcArrayOfCommandValue:
        o = NcArrayOfCommandValue()
        o._inner = [item.clone() for item in self._inner]
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        engine.open_array(name)
        for item in self._inner:
            item.encode(engine, None)
        engine.close_array()

    def decode_value(self, data: Any) -> None:
        if not isinstance(data, list):
            raise InvalidData("expected array for NcArrayOfCommand")
        self._inner = []
        for item_data in data:
            elem = NcCommandValue()
            elem.decode(JsonEngine(), item_data)
            self._inner.append(elem)


class NcArrayOfCommand:
    """Array type: NcArrayOfCommand."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcArrayOfCommandValue = NcArrayOfCommandValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> list[NcCommandValue]:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.get()

    @value.setter
    def value(self, v: list[NcCommandValue]) -> None:
        self._defined = True
        self._value.set(v)

    def get(self, default: list[NcCommandValue] | None = None) -> list[NcCommandValue] | None:
        return self._value.get() if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._value.set([])

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcArrayOfCommandValue()

    def clone(self) -> NcArrayOfCommand:
        o = NcArrayOfCommand()
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
            return f"NcArrayOfCommand({len(self._value.get())} items)"
        return "NcArrayOfCommand(<undefined>)"


def make_ncarrayofcommand(v: list[NcCommandValue]) -> NcArrayOfCommand:
    """Factory: create a defined NcArrayOfCommand with the given list."""
    o = NcArrayOfCommand()
    o.value = v
    return o

