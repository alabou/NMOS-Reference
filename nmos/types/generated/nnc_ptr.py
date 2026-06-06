"""Generated NMOS type: NNcPtr. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine




class NNcPtrValue:
    """Simple value for NNcPtr."""

    __slots__ = ("_inner",)

    def __init__(self) -> None:
        self._inner: object = object()

    def get(self) -> object:
        return self._inner

    def set(self, v: object) -> None:
        self._inner = v

    def clone(self) -> NNcPtrValue:
        o = NNcPtrValue()
        o._inner = self._inner
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        inner: Any = self._inner
        if hasattr(inner, "encode"):
            inner.encode(engine, name)
        elif inner is None:
            engine.write_null(name)
        elif isinstance(inner, bool):
            engine.write_bool(name, inner)
        elif isinstance(inner, int):
            engine.write_int(name, inner)
        elif isinstance(inner, float):
            engine.write_float(name, inner)
        elif isinstance(inner, str):
            engine.write_string(name, inner)

    def decode_value(self, data: Any) -> None:
        self._inner = data


class NNcPtr:
    """Simple value type: NNcPtr."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NNcPtrValue = NNcPtrValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> object:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.get()

    @value.setter
    def value(self, v: object) -> None:
        self._defined = True
        self._value.set(v)

    def get(self, default: object | None = None) -> object | None:
        return self._value.get() if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NNcPtrValue()

    def clone(self) -> NNcPtr:
        o = NNcPtr()
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
            return f"NNcPtr({self._value.get()!r})"
        return "NNcPtr(<undefined>)"


def make_nncptr(v: object) -> NNcPtr:
    """Factory: create a defined NNcPtr with the given value."""
    o = NNcPtr()
    o.value = v
    return o

