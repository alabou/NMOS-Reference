"""Generated NMOS type: NClock. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.types.generated.nclock_internal import NClockInternal, NClockInternalValue
from nmos.types.generated.nclock_ptp import NClockPtp, NClockPtpValue




# --- Type predicates (ported from Go TypePredicate* functions) ---

def _predicate_NClockInternal(data: dict[str, Any]) -> bool:
    """Check if data matches NClockInternal."""
    if data.get("ref_type") != "internal":
        return False
    return True

def _predicate_NClockPtp(data: dict[str, Any]) -> bool:
    """Check if data matches NClockPtp."""
    if data.get("ref_type") != "ptp":
        return False
    return True


class NClockValue:
    """Polymorphic value for NClock. Holds one of: NClockInternal, NClockPtp."""

    __slots__ = ("_inner",)

    def __init__(self) -> None:
        self._inner: Any = None

    def get(self) -> Any:
        return self._inner

    def set(self, v: Any) -> None:
        self._inner = v

    def clone(self) -> NClockValue:
        o = NClockValue()
        if self._inner is not None and hasattr(self._inner, "clone"):
            o._inner = self._inner.clone()
        else:
            o._inner = self._inner
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if self._inner is None:
            return
        # Type-switch: delegate to concrete type's encode
        if isinstance(self._inner, NClockInternal):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NClockPtp):
            self._inner.encode(engine, name)
            return

    def decode(self, engine: JsonEngine, data: Any) -> None:
        """Polymorphic decode using predicates (mirrors Go TypePredicate pattern).

        Predicates are checked in the same order as Go's T field.
        First matching predicate determines the concrete type to decode.
        """
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for polymorphic NClock")

        if _predicate_NClockInternal(data):
            obj_0 = NClockInternal()
            obj_0.decode(engine, data)
            self._inner = obj_0
            return
        if _predicate_NClockPtp(data):
            obj_1 = NClockPtp()
            obj_1.decode(engine, data)
            self._inner = obj_1
            return
        raise InvalidData("no matching type for polymorphic NClock")

    def decode_value(self, data: Any) -> None:
        self.decode(JsonEngine(), data)


class NClock:
    """Polymorphic type: NClock. Wraps one of NClockInternal, NClockPtp."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NClockValue = NClockValue()

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

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NClockValue()

    def clone(self) -> NClock:
        o = NClock()
        o._defined = self._defined
        o._value = self._value.clone()
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if self._defined:
            self._value.encode(engine, name)

    def decode(self, engine: JsonEngine, data: Any) -> None:
        self._value.decode(engine, data)
        self._defined = True

    def decode_value(self, data: Any) -> None:
        self._value.decode(JsonEngine(), data)
        self._defined = True

    def __repr__(self) -> str:
        if self._defined:
            inner = self._value.get()
            return f"NClock({type(inner).__name__})"
        return "NClock(<undefined>)"


def make_nclock(v: Any) -> NClock:
    """Factory: create a defined NClock with the given concrete value."""
    o = NClock()
    o.value = v
    return o

