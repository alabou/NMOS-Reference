"""Generated NMOS type: NRtpTransportConstraints. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.types.generated.ntransport_constraints import NTransportConstraints, NTransportConstraintsValue
from nmos.validators import CheckRtpTransportConstraints

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NRtpTransportConstraintsEnums:
    """JSON property name enums for NRtpTransportConstraints."""
    pass


class NRtpTransportConstraintsValue:
    """Inner value struct for NRtpTransportConstraints."""

    __slots__ = (
        "Constraints",
    )

    def __init__(self) -> None:
        self.Constraints: NTransportConstraintsValue = NTransportConstraintsValue()

    def set_to_default(self) -> None:
        self.Constraints = NTransportConstraintsValue()
        self.Constraints.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        self.Constraints.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Constraints.encode(engine, None)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NRtpTransportConstraints")

        self.Constraints.decode(engine, data)

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NRtpTransportConstraintsValue:
        o = NRtpTransportConstraintsValue()
        o.Constraints = self.Constraints.clone()
        return o


class NRtpTransportConstraints:
    """Optional object type: NRtpTransportConstraints."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NRtpTransportConstraintsValue = NRtpTransportConstraintsValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NRtpTransportConstraintsValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NRtpTransportConstraintsValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NRtpTransportConstraintsValue | None = None) -> NRtpTransportConstraintsValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Constraints(self) -> NTransportConstraintsValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Constraints

    def set_Constraints(self, v: NTransportConstraintsValue) -> None:
        assert self._defined, "NRtpTransportConstraints must be defined before setting Constraints"
        self._value.Constraints = v.clone()  # copy to match Go's value semantics


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NRtpTransportConstraintsValue()

    def clone(self) -> NRtpTransportConstraints:
        o = NRtpTransportConstraints()
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
        """Decode from a parent dict value. Creates minimal engine context."""
        self._value.decode(JsonEngine(), data)
        self._defined = True

    def __repr__(self) -> str:
        if self._defined:
            return f"NRtpTransportConstraints(defined)"
        return "NRtpTransportConstraints(<undefined>)"


def make_nrtptransportconstraints_value(v: NRtpTransportConstraintsValue) -> NRtpTransportConstraintsValue:
    """Factory: create a NRtpTransportConstraintsValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nrtptransportconstraints(v: NRtpTransportConstraintsValue) -> NRtpTransportConstraints:
    """Factory: create a defined NRtpTransportConstraints from a NRtpTransportConstraintsValue."""
    o = NRtpTransportConstraints()
    o.set_value(v)
    return o

