"""Generated NMOS type: NReceiverMux. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NEnum
from nmos.types.generated.nreceiver_core import NReceiverCore, NReceiverCoreValue
from nmos.types.generated.nreceiver_mux_capabilities import NReceiverMuxCapabilities, NReceiverMuxCapabilitiesValue
from nmos.validators import CheckFormat

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NReceiverMuxEnums:
    """JSON property name enums for NReceiverMux."""
    Format = EnumRegistry.get("format")
    Caps = EnumRegistry.get("caps")
    pass


class NReceiverMuxValue:
    """Inner value struct for NReceiverMux."""

    __slots__ = (
        "ReceiverCore",
        "Format",
        "Caps",
    )

    def __init__(self) -> None:
        self.ReceiverCore: NReceiverCoreValue = NReceiverCoreValue()
        self.Format: NEnum = NEnum()
        self.Caps: NReceiverMuxCapabilities = NReceiverMuxCapabilities()

    def set_to_default(self) -> None:
        self.ReceiverCore = NReceiverCoreValue()
        self.ReceiverCore.set_to_default()
        self.Format.set_to_default()
        self.Caps.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Format.defined:
            raise InvalidObject("missing required member Format")
        if not self.Caps.defined:
            raise InvalidObject("missing required member Caps")
        if self.Format.defined:
            CheckFormat(self.Format)
        self.ReceiverCore.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.ReceiverCore.encode(engine, None)
        self.Format.encode(engine, NReceiverMuxEnums.Format)
        self.Caps.encode(engine, NReceiverMuxEnums.Caps)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NReceiverMux")

        self.ReceiverCore.decode(engine, data)
        if NReceiverMuxEnums.Format.s in data:
            self.Format.decode_value(data[NReceiverMuxEnums.Format.s])
        if NReceiverMuxEnums.Caps.s in data:
            self.Caps.decode_value(data[NReceiverMuxEnums.Caps.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NReceiverMuxValue:
        o = NReceiverMuxValue()
        o.ReceiverCore = self.ReceiverCore.clone()
        o.Format = self.Format.clone()
        o.Caps = self.Caps.clone()
        return o


class NReceiverMux:
    """Optional object type: NReceiverMux."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NReceiverMuxValue = NReceiverMuxValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NReceiverMuxValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NReceiverMuxValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NReceiverMuxValue | None = None) -> NReceiverMuxValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_ReceiverCore(self) -> NReceiverCoreValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ReceiverCore

    def set_ReceiverCore(self, v: NReceiverCoreValue) -> None:
        assert self._defined, "NReceiverMux must be defined before setting ReceiverCore"
        self._value.ReceiverCore = v.clone()  # copy to match Go's value semantics

    def get_Format(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Format

    def set_Format(self, v: Any) -> None:
        assert self._defined, "NReceiverMux must be defined before setting Format"
        _assign_value(self._value.Format, v)

    def get_Caps(self) -> NReceiverMuxCapabilities:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Caps

    def set_Caps(self, v: Any) -> None:
        assert self._defined, "NReceiverMux must be defined before setting Caps"
        _assign_value(self._value.Caps, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NReceiverMuxValue()

    def clone(self) -> NReceiverMux:
        o = NReceiverMux()
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
            return f"NReceiverMux(defined)"
        return "NReceiverMux(<undefined>)"


def make_nreceivermux_value(v: NReceiverMuxValue) -> NReceiverMuxValue:
    """Factory: create a NReceiverMuxValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nreceivermux(v: NReceiverMuxValue) -> NReceiverMux:
    """Factory: create a defined NReceiverMux from a NReceiverMuxValue."""
    o = NReceiverMux()
    o.set_value(v)
    return o

