"""Generated NMOS type: NAudioChannel. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString, NEnum

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NAudioChannelEnums:
    """JSON property name enums for NAudioChannel."""
    Label = EnumRegistry.get("label")
    Symbol = EnumRegistry.get("symbol")
    pass


class NAudioChannelValue:
    """Inner value struct for NAudioChannel."""

    __slots__ = (
        "Label",
        "Symbol",
    )

    def __init__(self) -> None:
        self.Label: NString = NString()
        self.Symbol: NEnum = NEnum()

    def set_to_default(self) -> None:
        self.Label.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Label.defined:
            raise InvalidObject("missing required member Label")
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Label.encode(engine, NAudioChannelEnums.Label)
        self.Symbol.encode(engine, NAudioChannelEnums.Symbol)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NAudioChannel")

        if NAudioChannelEnums.Label.s in data:
            self.Label.decode_value(data[NAudioChannelEnums.Label.s])
        if NAudioChannelEnums.Symbol.s in data:
            self.Symbol.decode_value(data[NAudioChannelEnums.Symbol.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NAudioChannelValue:
        o = NAudioChannelValue()
        o.Label = self.Label.clone()
        o.Symbol = self.Symbol.clone()
        return o


class NAudioChannel:
    """Optional object type: NAudioChannel."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NAudioChannelValue = NAudioChannelValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NAudioChannelValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NAudioChannelValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NAudioChannelValue | None = None) -> NAudioChannelValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Label(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Label

    def set_Label(self, v: Any) -> None:
        assert self._defined, "NAudioChannel must be defined before setting Label"
        _assign_value(self._value.Label, v)

    def get_Symbol(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Symbol

    def set_Symbol(self, v: Any) -> None:
        assert self._defined, "NAudioChannel must be defined before setting Symbol"
        _assign_value(self._value.Symbol, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NAudioChannelValue()

    def clone(self) -> NAudioChannel:
        o = NAudioChannel()
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
            return f"NAudioChannel(defined)"
        return "NAudioChannel(<undefined>)"


def make_naudiochannel_value(v: NAudioChannelValue) -> NAudioChannelValue:
    """Factory: create a NAudioChannelValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_naudiochannel(v: NAudioChannelValue) -> NAudioChannel:
    """Factory: create a defined NAudioChannel from a NAudioChannelValue."""
    o = NAudioChannel()
    o.set_value(v)
    return o

