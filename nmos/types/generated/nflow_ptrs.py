"""Generated NMOS type: NFlowPtrs. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine




if TYPE_CHECKING:
    from nmos.types.generated.nflow import NFlowValue
    from nmos.types.generated.nflow import NFlowValue


class NFlowPtrsValue:
    """Map value for NFlowPtrs."""

    __slots__ = ("_inner",)

    def __init__(self) -> None:
        self._inner: dict[NFlowValue, NFlowValue] = {}

    def get(self) -> dict[NFlowValue, NFlowValue]:
        return self._inner

    def set(self, v: dict[NFlowValue, NFlowValue]) -> None:
        self._inner = v

    def clone(self) -> NFlowPtrsValue:
        o = NFlowPtrsValue()
        o._inner = dict(self._inner)  # shallow copy of map
        return o


class NFlowPtrs:
    """Map type: NFlowPtrs."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NFlowPtrsValue = NFlowPtrsValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> dict[NFlowValue, NFlowValue]:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.get()

    @value.setter
    def value(self, v: dict[NFlowValue, NFlowValue]) -> None:
        self._defined = True
        self._value.set(v)

    def get(self, default: dict[NFlowValue, NFlowValue] | None = None) -> dict[NFlowValue, NFlowValue] | None:
        return self._value.get() if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._value.set({})

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NFlowPtrsValue()

    def clone(self) -> NFlowPtrs:
        o = NFlowPtrs()
        o._defined = self._defined
        o._value = self._value.clone()
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        pass  # Map types are internal; not serialized to JSON

    def decode_value(self, data: Any) -> None:
        pass  # Map types are internal; not decoded from JSON

    def __repr__(self) -> str:
        if self._defined:
            return f"NFlowPtrs({len(self._value.get())} entries)"
        return "NFlowPtrs(<undefined>)"


def make_nflowptrs(v: dict[NFlowValue, NFlowValue]) -> NFlowPtrs:
    """Factory: create a defined NFlowPtrs with the given map."""
    o = NFlowPtrs()
    o.value = v
    return o

