"""Generated NMOS type: NQueryWebSocketGrainDataDevice. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString
from nmos.types.generated.ndevice import NDevice, NDeviceValue

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NQueryWebSocketGrainDataDeviceEnums:
    """JSON property name enums for NQueryWebSocketGrainDataDevice."""
    Path = EnumRegistry.get("path")
    Pre = EnumRegistry.get("pre")
    Post = EnumRegistry.get("post")
    pass


class NQueryWebSocketGrainDataDeviceValue:
    """Inner value struct for NQueryWebSocketGrainDataDevice."""

    __slots__ = (
        "Path",
        "Pre",
        "Post",
    )

    def __init__(self) -> None:
        self.Path: NString = NString()
        self.Pre: NDevice = NDevice()
        self.Post: NDevice = NDevice()

    def set_to_default(self) -> None:
        self.Path.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Path.defined:
            raise InvalidObject("missing required member Path")
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Path.encode(engine, NQueryWebSocketGrainDataDeviceEnums.Path)
        self.Pre.encode(engine, NQueryWebSocketGrainDataDeviceEnums.Pre)
        self.Post.encode(engine, NQueryWebSocketGrainDataDeviceEnums.Post)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NQueryWebSocketGrainDataDevice")

        if NQueryWebSocketGrainDataDeviceEnums.Path.s in data:
            self.Path.decode_value(data[NQueryWebSocketGrainDataDeviceEnums.Path.s])
        if NQueryWebSocketGrainDataDeviceEnums.Pre.s in data:
            self.Pre.decode_value(data[NQueryWebSocketGrainDataDeviceEnums.Pre.s])
        if NQueryWebSocketGrainDataDeviceEnums.Post.s in data:
            self.Post.decode_value(data[NQueryWebSocketGrainDataDeviceEnums.Post.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NQueryWebSocketGrainDataDeviceValue:
        o = NQueryWebSocketGrainDataDeviceValue()
        o.Path = self.Path.clone()
        o.Pre = self.Pre.clone()
        o.Post = self.Post.clone()
        return o


class NQueryWebSocketGrainDataDevice:
    """Optional object type: NQueryWebSocketGrainDataDevice."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NQueryWebSocketGrainDataDeviceValue = NQueryWebSocketGrainDataDeviceValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NQueryWebSocketGrainDataDeviceValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NQueryWebSocketGrainDataDeviceValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NQueryWebSocketGrainDataDeviceValue | None = None) -> NQueryWebSocketGrainDataDeviceValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Path(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Path

    def set_Path(self, v: Any) -> None:
        assert self._defined, "NQueryWebSocketGrainDataDevice must be defined before setting Path"
        _assign_value(self._value.Path, v)

    def get_Pre(self) -> NDevice:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Pre

    def set_Pre(self, v: Any) -> None:
        assert self._defined, "NQueryWebSocketGrainDataDevice must be defined before setting Pre"
        _assign_value(self._value.Pre, v)

    def get_Post(self) -> NDevice:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Post

    def set_Post(self, v: Any) -> None:
        assert self._defined, "NQueryWebSocketGrainDataDevice must be defined before setting Post"
        _assign_value(self._value.Post, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NQueryWebSocketGrainDataDeviceValue()

    def clone(self) -> NQueryWebSocketGrainDataDevice:
        o = NQueryWebSocketGrainDataDevice()
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
            return f"NQueryWebSocketGrainDataDevice(defined)"
        return "NQueryWebSocketGrainDataDevice(<undefined>)"


def make_nquerywebsocketgraindatadevice_value(v: NQueryWebSocketGrainDataDeviceValue) -> NQueryWebSocketGrainDataDeviceValue:
    """Factory: create a NQueryWebSocketGrainDataDeviceValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nquerywebsocketgraindatadevice(v: NQueryWebSocketGrainDataDeviceValue) -> NQueryWebSocketGrainDataDevice:
    """Factory: create a defined NQueryWebSocketGrainDataDevice from a NQueryWebSocketGrainDataDeviceValue."""
    o = NQueryWebSocketGrainDataDevice()
    o.set_value(v)
    return o

