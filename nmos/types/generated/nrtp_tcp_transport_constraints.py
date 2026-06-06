"""Generated NMOS type: NRtpTcpTransportConstraints. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.types.generated.ntransport_constraints import NTransportConstraints, NTransportConstraintsValue
from nmos.validators import CheckRtpTcpTransportConstraints

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NRtpTcpTransportConstraintsEnums:
    """JSON property name enums for NRtpTcpTransportConstraints."""
    pass


class NRtpTcpTransportConstraintsValue:
    """Inner value struct for NRtpTcpTransportConstraints."""

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
            raise InvalidData("expected JSON object for NRtpTcpTransportConstraints")

        self.Constraints.decode(engine, data)

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NRtpTcpTransportConstraintsValue:
        o = NRtpTcpTransportConstraintsValue()
        o.Constraints = self.Constraints.clone()
        return o


class NRtpTcpTransportConstraints:
    """Optional object type: NRtpTcpTransportConstraints."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NRtpTcpTransportConstraintsValue = NRtpTcpTransportConstraintsValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NRtpTcpTransportConstraintsValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NRtpTcpTransportConstraintsValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NRtpTcpTransportConstraintsValue | None = None) -> NRtpTcpTransportConstraintsValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Constraints(self) -> NTransportConstraintsValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Constraints

    def set_Constraints(self, v: NTransportConstraintsValue) -> None:
        assert self._defined, "NRtpTcpTransportConstraints must be defined before setting Constraints"
        self._value.Constraints = v.clone()  # copy to match Go's value semantics


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NRtpTcpTransportConstraintsValue()

    def clone(self) -> NRtpTcpTransportConstraints:
        o = NRtpTcpTransportConstraints()
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
            return f"NRtpTcpTransportConstraints(defined)"
        return "NRtpTcpTransportConstraints(<undefined>)"


def make_nrtptcptransportconstraints_value(v: NRtpTcpTransportConstraintsValue) -> NRtpTcpTransportConstraintsValue:
    """Factory: create a NRtpTcpTransportConstraintsValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nrtptcptransportconstraints(v: NRtpTcpTransportConstraintsValue) -> NRtpTcpTransportConstraints:
    """Factory: create a defined NRtpTcpTransportConstraints from a NRtpTcpTransportConstraintsValue."""
    o = NRtpTcpTransportConstraints()
    o.set_value(v)
    return o

