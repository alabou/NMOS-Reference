"""Hand-written NConstraints map type.

Mirrors Go's NConstraints in types.go. This is an embedded map type where
the keys are EnumId (capability URNs like "urn:x-nmos:cap:format:media_type")
and values are NConstraint (polymorphic constraint objects).

When embedded in NConstraintSet, this map receives the same dict as its parent.
It skips keys that the parent already handles (the meta keys) and decodes the
remaining keys as NConstraint entries.

Hand-written because:
- Go's NConstraintsValue.Decode is hand-written in types.go (not generated)
- The embedded decode needs to know which keys the parent handles (meta keys)
- The generic map template cannot express this filtering logic
"""

from __future__ import annotations

from typing import Any

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import NotAvailable
from nmos.json.engine import JsonEngine


# Meta keys handled by the parent NConstraintSet — skip these in map decode.
# These correspond to the named members of NConstraintSet.
_META_KEYS = frozenset({
    "urn:x-nmos:cap:meta:label",
    "urn:x-matrox:cap:meta:format",
    "urn:x-matrox:cap:meta:layer",
    "urn:x-matrox:cap:meta:layer_enabled",
    "urn:x-matrox:cap:meta:layer_compatibility_groups",
    "urn:x-nmos:cap:meta:enabled",
    "urn:x-nmos:cap:meta:preference",
    "urn:x-matrox:cap:meta:info_block",
})


class NConstraintsValue:
    """Map value for NConstraints: dict[EnumId, NConstraint]."""

    __slots__ = ("_inner",)

    def __init__(self) -> None:
        from nmos.types.generated.nconstraint import NConstraint
        self._inner: dict[EnumId, NConstraint] = {}

    def get(self) -> dict[EnumId, Any]:
        return self._inner

    def set(self, v: dict[EnumId, Any]) -> None:
        self._inner = v

    def set_to_default(self) -> None:
        self._inner = {}

    def assert_valid(self) -> None:
        pass  # no validation needed for maps

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        """Encode map entries inline into the parent struct (embedded)."""
        for k, v in self._inner.items():
            v.encode(engine, k)

    def decode(self, engine: JsonEngine, data: Any) -> None:
        """Decode from parent dict, skipping meta keys handled by NConstraintSet."""
        from nmos.types.generated.nconstraint import NConstraint

        if not isinstance(data, dict):
            return

        self._inner = {}
        for key, val in data.items():
            if key in _META_KEYS:
                continue
            # Remaining keys are constraint URNs
            enum_key = EnumRegistry.get(key)
            constraint = NConstraint()
            constraint.decode_value(val)
            self._inner[enum_key] = constraint

    def clone(self) -> NConstraintsValue:
        o = NConstraintsValue()
        o._inner = dict(self._inner)  # shallow copy of map
        return o


class NConstraints:
    """Map type: NConstraints — dict[EnumId, NConstraint] with embedded semantics."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NConstraintsValue = NConstraintsValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> dict[EnumId, Any]:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.get()

    @value.setter
    def value(self, v: dict[EnumId, Any]) -> None:
        self._defined = True
        self._value.set(v)

    def get(self, default: dict[EnumId, Any] | None = None) -> dict[EnumId, Any] | None:
        return self._value.get() if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._value.set({})

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NConstraintsValue()

    def clone(self) -> NConstraints:
        o = NConstraints()
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
            return f"NConstraints({len(self._value.get())} entries)"
        return "NConstraints(<undefined>)"


def make_nconstraints(v: dict[EnumId, Any]) -> NConstraints:
    """Factory: create a defined NConstraints with the given map."""
    o = NConstraints()
    o.value = v
    return o
