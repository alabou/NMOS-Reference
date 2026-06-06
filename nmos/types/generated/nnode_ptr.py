"""Generated NMOS type: NNodePtr. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine




class NNodePtrValue:
    """Pointer value for NNodePtr. Holds a reference to NNodeValue."""

    __slots__ = ("_inner",)

    def __init__(self) -> None:
        self._inner: Any = None

    def get(self) -> Any:
        return self._inner

    def set(self, v: Any) -> None:
        self._inner = v

    def clone(self) -> NNodePtrValue:
        o = NNodePtrValue()
        o._inner = self._inner  # pointer: shared reference (not deep copy)
        return o


class NNodePtr:
    """Pointer type: NNodePtr. Wraps a reference to NNodeValue."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NNodePtrValue = NNodePtrValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> Any:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.get()

    @value.setter
    def value(self, v: Any) -> None:
        self._defined = True
        self._value.set(v)

    def get(self, default: Any = None) -> Any:
        return self._value.get() if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._value.set(None)

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NNodePtrValue()

    def clone(self) -> NNodePtr:
        o = NNodePtr()
        o._defined = self._defined
        o._value = self._value.clone()
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        pass  # Pointer types are internal; not serialized to JSON

    def decode_value(self, data: Any) -> None:
        pass  # Pointer types are internal; not decoded from JSON

    def __repr__(self) -> str:
        if self._defined and self._value.get() is not None:
            return f"NNodePtr(->NNodeValue)"
        return "NNodePtr(None)"


def make_nnodeptr(v: Any) -> NNodePtr:
    """Factory: create a defined NNodePtr pointing to v."""
    o = NNodePtr()
    o.value = v
    return o

