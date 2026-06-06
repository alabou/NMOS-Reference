"""Generated NMOS type: NcArrayOfBlockMemberDescriptor. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.types.generated.nc_block_member_descriptor import NcBlockMemberDescriptorValue




class NcArrayOfBlockMemberDescriptorValue:
    """Array value for NcArrayOfBlockMemberDescriptor."""

    __slots__ = ("_inner",)

    def __init__(self) -> None:
        self._inner: list[NcBlockMemberDescriptorValue] = []

    def get(self) -> list[NcBlockMemberDescriptorValue]:
        return self._inner

    def set(self, v: list[NcBlockMemberDescriptorValue]) -> None:
        self._inner = v

    def append(self, v: list[NcBlockMemberDescriptorValue]) -> None:
        self._inner = self._inner + v

    def clone(self) -> NcArrayOfBlockMemberDescriptorValue:
        o = NcArrayOfBlockMemberDescriptorValue()
        o._inner = [item.clone() for item in self._inner]
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        engine.open_array(name)
        for item in self._inner:
            item.encode(engine, None)
        engine.close_array()

    def decode_value(self, data: Any) -> None:
        if not isinstance(data, list):
            raise InvalidData("expected array for NcArrayOfBlockMemberDescriptor")
        self._inner = []
        for item_data in data:
            elem = NcBlockMemberDescriptorValue()
            elem.decode(JsonEngine(), item_data)
            self._inner.append(elem)


class NcArrayOfBlockMemberDescriptor:
    """Array type: NcArrayOfBlockMemberDescriptor."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcArrayOfBlockMemberDescriptorValue = NcArrayOfBlockMemberDescriptorValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> list[NcBlockMemberDescriptorValue]:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.get()

    @value.setter
    def value(self, v: list[NcBlockMemberDescriptorValue]) -> None:
        self._defined = True
        self._value.set(v)

    def get(self, default: list[NcBlockMemberDescriptorValue] | None = None) -> list[NcBlockMemberDescriptorValue] | None:
        return self._value.get() if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._value.set([])

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcArrayOfBlockMemberDescriptorValue()

    def clone(self) -> NcArrayOfBlockMemberDescriptor:
        o = NcArrayOfBlockMemberDescriptor()
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
            return f"NcArrayOfBlockMemberDescriptor({len(self._value.get())} items)"
        return "NcArrayOfBlockMemberDescriptor(<undefined>)"


def make_ncarrayofblockmemberdescriptor(v: list[NcBlockMemberDescriptorValue]) -> NcArrayOfBlockMemberDescriptor:
    """Factory: create a defined NcArrayOfBlockMemberDescriptor with the given list."""
    o = NcArrayOfBlockMemberDescriptor()
    o.value = v
    return o

