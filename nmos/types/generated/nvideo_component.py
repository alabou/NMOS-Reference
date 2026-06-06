"""Generated NMOS type: NVideoComponent. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NEnum, NInt

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NVideoComponentEnums:
    """JSON property name enums for NVideoComponent."""
    Name = EnumRegistry.get("name")
    Width = EnumRegistry.get("width")
    Height = EnumRegistry.get("height")
    BitDepth = EnumRegistry.get("bit_depth")
    pass


class NVideoComponentValue:
    """Inner value struct for NVideoComponent."""

    __slots__ = (
        "Name",
        "Width",
        "Height",
        "BitDepth",
    )

    def __init__(self) -> None:
        self.Name: NEnum = NEnum()
        self.Width: NInt = NInt()
        self.Height: NInt = NInt()
        self.BitDepth: NInt = NInt()

    def set_to_default(self) -> None:
        self.Name.set_to_default()
        self.Width.set_to_default()
        self.Height.set_to_default()
        self.BitDepth.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Name.defined:
            raise InvalidObject("missing required member Name")
        if not self.Width.defined:
            raise InvalidObject("missing required member Width")
        if not self.Height.defined:
            raise InvalidObject("missing required member Height")
        if not self.BitDepth.defined:
            raise InvalidObject("missing required member BitDepth")
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Name.encode(engine, NVideoComponentEnums.Name)
        self.Width.encode(engine, NVideoComponentEnums.Width)
        self.Height.encode(engine, NVideoComponentEnums.Height)
        self.BitDepth.encode(engine, NVideoComponentEnums.BitDepth)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NVideoComponent")

        if NVideoComponentEnums.Name.s in data:
            self.Name.decode_value(data[NVideoComponentEnums.Name.s])
        if NVideoComponentEnums.Width.s in data:
            self.Width.decode_value(data[NVideoComponentEnums.Width.s])
        if NVideoComponentEnums.Height.s in data:
            self.Height.decode_value(data[NVideoComponentEnums.Height.s])
        if NVideoComponentEnums.BitDepth.s in data:
            self.BitDepth.decode_value(data[NVideoComponentEnums.BitDepth.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NVideoComponentValue:
        o = NVideoComponentValue()
        o.Name = self.Name.clone()
        o.Width = self.Width.clone()
        o.Height = self.Height.clone()
        o.BitDepth = self.BitDepth.clone()
        return o


class NVideoComponent:
    """Optional object type: NVideoComponent."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NVideoComponentValue = NVideoComponentValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NVideoComponentValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NVideoComponentValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NVideoComponentValue | None = None) -> NVideoComponentValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Name(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Name

    def set_Name(self, v: Any) -> None:
        assert self._defined, "NVideoComponent must be defined before setting Name"
        _assign_value(self._value.Name, v)

    def get_Width(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Width

    def set_Width(self, v: Any) -> None:
        assert self._defined, "NVideoComponent must be defined before setting Width"
        _assign_value(self._value.Width, v)

    def get_Height(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Height

    def set_Height(self, v: Any) -> None:
        assert self._defined, "NVideoComponent must be defined before setting Height"
        _assign_value(self._value.Height, v)

    def get_BitDepth(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.BitDepth

    def set_BitDepth(self, v: Any) -> None:
        assert self._defined, "NVideoComponent must be defined before setting BitDepth"
        _assign_value(self._value.BitDepth, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NVideoComponentValue()

    def clone(self) -> NVideoComponent:
        o = NVideoComponent()
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
            return f"NVideoComponent(defined)"
        return "NVideoComponent(<undefined>)"


def make_nvideocomponent_value(v: NVideoComponentValue) -> NVideoComponentValue:
    """Factory: create a NVideoComponentValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nvideocomponent(v: NVideoComponentValue) -> NVideoComponent:
    """Factory: create a defined NVideoComponent from a NVideoComponentValue."""
    o = NVideoComponent()
    o.set_value(v)
    return o

