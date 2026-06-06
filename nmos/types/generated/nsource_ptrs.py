"""Generated NMOS type: NSourcePtrs. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine




if TYPE_CHECKING:
    from nmos.types.generated.nsource import NSourceValue
    from nmos.types.generated.nsource import NSourceValue


class NSourcePtrsValue:
    """Map value for NSourcePtrs."""

    __slots__ = ("_inner",)

    def __init__(self) -> None:
        self._inner: dict[NSourceValue, NSourceValue] = {}

    def get(self) -> dict[NSourceValue, NSourceValue]:
        return self._inner

    def set(self, v: dict[NSourceValue, NSourceValue]) -> None:
        self._inner = v

    def clone(self) -> NSourcePtrsValue:
        o = NSourcePtrsValue()
        o._inner = dict(self._inner)  # shallow copy of map
        return o


class NSourcePtrs:
    """Map type: NSourcePtrs."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NSourcePtrsValue = NSourcePtrsValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> dict[NSourceValue, NSourceValue]:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.get()

    @value.setter
    def value(self, v: dict[NSourceValue, NSourceValue]) -> None:
        self._defined = True
        self._value.set(v)

    def get(self, default: dict[NSourceValue, NSourceValue] | None = None) -> dict[NSourceValue, NSourceValue] | None:
        return self._value.get() if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._value.set({})

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NSourcePtrsValue()

    def clone(self) -> NSourcePtrs:
        o = NSourcePtrs()
        o._defined = self._defined
        o._value = self._value.clone()
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        pass  # Map types are internal; not serialized to JSON

    def decode_value(self, data: Any) -> None:
        pass  # Map types are internal; not decoded from JSON

    def __repr__(self) -> str:
        if self._defined:
            return f"NSourcePtrs({len(self._value.get())} entries)"
        return "NSourcePtrs(<undefined>)"


def make_nsourceptrs(v: dict[NSourceValue, NSourceValue]) -> NSourcePtrs:
    """Factory: create a defined NSourcePtrs with the given map."""
    o = NSourcePtrs()
    o.value = v
    return o

