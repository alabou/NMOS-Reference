"""Generated NMOS type: NConstraint. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.types.generated.nconstraint_bool import NConstraintBool, NConstraintBoolValue
from nmos.types.generated.nconstraint_int import NConstraintInt, NConstraintIntValue
from nmos.types.generated.nconstraint_float import NConstraintFloat, NConstraintFloatValue
from nmos.types.generated.nconstraint_string import NConstraintString, NConstraintStringValue
from nmos.types.generated.nconstraint_rational import NConstraintRational, NConstraintRationalValue




# --- Type predicates (ported from Go TypePredicate* functions) ---

def _predicate_NConstraintBool(data: dict[str, Any]) -> bool:
    """Check if data matches NConstraintBool."""
    _enum_val = data.get("enum")
    if isinstance(_enum_val, list) and _enum_val:
        if not isinstance(_enum_val[0], bool):
            return False
    else:
        return False
    return True

def _predicate_NConstraintInt(data: dict[str, Any]) -> bool:
    """Check if data matches NConstraintInt."""
    _v = data.get("enum", data.get("minimum", data.get("maximum")))
    if isinstance(_v, list) and _v:
        if not isinstance(_v[0], int) or isinstance(_v[0], bool):
            return False
    elif isinstance(_v, int) and not isinstance(_v, bool):
        pass
    else:
        return False
    return True

def _predicate_NConstraintFloat(data: dict[str, Any]) -> bool:
    """Check if data matches NConstraintFloat."""
    _v = data.get("enum", data.get("minimum", data.get("maximum")))
    if isinstance(_v, list) and _v:
        if not isinstance(_v[0], float):
            return False
    elif isinstance(_v, float):
        pass
    else:
        return False
    return True

def _predicate_NConstraintString(data: dict[str, Any]) -> bool:
    """Check if data matches NConstraintString."""
    _enum_val = data.get("enum")
    if isinstance(_enum_val, list) and _enum_val:
        if not isinstance(_enum_val[0], str):
            return False
    else:
        return False
    return True

def _predicate_NConstraintRational(data: dict[str, Any]) -> bool:
    """Check if data matches NConstraintRational."""
    pass  # fallback always matches
    return True


class NConstraintValue:
    """Polymorphic value for NConstraint. Holds one of: NConstraintBool, NConstraintInt, NConstraintFloat, NConstraintString, NConstraintRational."""

    __slots__ = ("_inner",)

    def __init__(self) -> None:
        self._inner: Any = None

    def get(self) -> Any:
        return self._inner

    def set(self, v: Any) -> None:
        self._inner = v

    def clone(self) -> NConstraintValue:
        o = NConstraintValue()
        if self._inner is not None and hasattr(self._inner, "clone"):
            o._inner = self._inner.clone()
        else:
            o._inner = self._inner
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if self._inner is None:
            return
        # Type-switch: delegate to concrete type's encode
        if isinstance(self._inner, NConstraintBool):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NConstraintInt):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NConstraintFloat):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NConstraintString):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NConstraintRational):
            self._inner.encode(engine, name)
            return

    def decode(self, engine: JsonEngine, data: Any) -> None:
        """Polymorphic decode using predicates (mirrors Go TypePredicate pattern).

        Predicates are checked in the same order as Go's T field.
        First matching predicate determines the concrete type to decode.
        """
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for polymorphic NConstraint")

        if _predicate_NConstraintBool(data):
            obj_0 = NConstraintBool()
            obj_0.decode(engine, data)
            self._inner = obj_0
            return
        if _predicate_NConstraintInt(data):
            obj_1 = NConstraintInt()
            obj_1.decode(engine, data)
            self._inner = obj_1
            return
        if _predicate_NConstraintFloat(data):
            obj_2 = NConstraintFloat()
            obj_2.decode(engine, data)
            self._inner = obj_2
            return
        if _predicate_NConstraintString(data):
            obj_3 = NConstraintString()
            obj_3.decode(engine, data)
            self._inner = obj_3
            return
        if _predicate_NConstraintRational(data):
            obj_4 = NConstraintRational()
            obj_4.decode(engine, data)
            self._inner = obj_4
            return
        raise InvalidData("no matching type for polymorphic NConstraint")

    def decode_value(self, data: Any) -> None:
        self.decode(JsonEngine(), data)


class NConstraint:
    """Polymorphic type: NConstraint. Wraps one of NConstraintBool, NConstraintInt, NConstraintFloat, NConstraintString, NConstraintRational."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NConstraintValue = NConstraintValue()

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
        self._value = NConstraintValue()

    def clone(self) -> NConstraint:
        o = NConstraint()
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
            return f"NConstraint({type(inner).__name__})"
        return "NConstraint(<undefined>)"


def make_nconstraint(v: Any) -> NConstraint:
    """Factory: create a defined NConstraint with the given concrete value."""
    o = NConstraint()
    o.value = v
    return o

