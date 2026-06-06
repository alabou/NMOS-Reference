"""Hand-written NTransportConstraints map type.

Mirrors Go's NTransportConstraints in types.go. This is an embedded map type
where keys are EnumId (transport parameter names like "source_ip") and values
are NTransportConstraint objects.

Hand-written because Go's NTransportConstraintsValue.Decode is hand-written
in types.go, and the generic map template cannot express embedded decode logic.
"""

from __future__ import annotations

from typing import Any

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import NotAvailable
from nmos.json.engine import JsonEngine


class NTransportConstraintsValue:
    """Map value for NTransportConstraints: dict[EnumId, NTransportConstraint]."""

    __slots__ = ("_inner",)

    def __init__(self) -> None:
        from nmos.types.generated.ntransport_constraint import NTransportConstraint
        self._inner: dict[EnumId, NTransportConstraint] = {}

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
        """Decode all keys from parent dict as NTransportConstraint entries."""
        from nmos.types.generated.ntransport_constraint import NTransportConstraint

        if not isinstance(data, dict):
            return

        self._inner = {}
        for key, val in data.items():
            enum_key = EnumRegistry.get(key)
            tc = NTransportConstraint()
            tc.decode_value(val)
            self._inner[enum_key] = tc

    def clone(self) -> NTransportConstraintsValue:
        o = NTransportConstraintsValue()
        o._inner = dict(self._inner)  # shallow copy of map
        return o


class NTransportConstraints:
    """Map type: NTransportConstraints — dict[EnumId, NTransportConstraint]."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NTransportConstraintsValue = NTransportConstraintsValue()

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
        self._value = NTransportConstraintsValue()

    def clone(self) -> NTransportConstraints:
        o = NTransportConstraints()
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
            return f"NTransportConstraints({len(self._value.get())} entries)"
        return "NTransportConstraints(<undefined>)"


def make_ntransportconstraints(v: dict[EnumId, Any]) -> NTransportConstraints:
    """Factory: create a defined NTransportConstraints with the given map."""
    o = NTransportConstraints()
    o.value = v
    return o
