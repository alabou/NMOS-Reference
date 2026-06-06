"""Generated NMOS type: NSenderPtrs. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine




if TYPE_CHECKING:
    from nmos.types.generated.nsender import NSenderValue
    from nmos.types.generated.nsender import NSenderValue


class NSenderPtrsValue:
    """Map value for NSenderPtrs."""

    __slots__ = ("_inner",)

    def __init__(self) -> None:
        self._inner: dict[NSenderValue, NSenderValue] = {}

    def get(self) -> dict[NSenderValue, NSenderValue]:
        return self._inner

    def set(self, v: dict[NSenderValue, NSenderValue]) -> None:
        self._inner = v

    def clone(self) -> NSenderPtrsValue:
        o = NSenderPtrsValue()
        o._inner = dict(self._inner)  # shallow copy of map
        return o


class NSenderPtrs:
    """Map type: NSenderPtrs."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NSenderPtrsValue = NSenderPtrsValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> dict[NSenderValue, NSenderValue]:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.get()

    @value.setter
    def value(self, v: dict[NSenderValue, NSenderValue]) -> None:
        self._defined = True
        self._value.set(v)

    def get(self, default: dict[NSenderValue, NSenderValue] | None = None) -> dict[NSenderValue, NSenderValue] | None:
        return self._value.get() if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._value.set({})

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NSenderPtrsValue()

    def clone(self) -> NSenderPtrs:
        o = NSenderPtrs()
        o._defined = self._defined
        o._value = self._value.clone()
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        pass  # Map types are internal; not serialized to JSON

    def decode_value(self, data: Any) -> None:
        pass  # Map types are internal; not decoded from JSON

    def __repr__(self) -> str:
        if self._defined:
            return f"NSenderPtrs({len(self._value.get())} entries)"
        return "NSenderPtrs(<undefined>)"


def make_nsenderptrs(v: dict[NSenderValue, NSenderValue]) -> NSenderPtrs:
    """Factory: create a defined NSenderPtrs with the given map."""
    o = NSenderPtrs()
    o.value = v
    return o

