"""Generated NMOS type: NQueryWebSocketGrainDataSender. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString
from nmos.types.generated.nsender import NSender, NSenderValue

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NQueryWebSocketGrainDataSenderEnums:
    """JSON property name enums for NQueryWebSocketGrainDataSender."""
    Path = EnumRegistry.get("path")
    Pre = EnumRegistry.get("pre")
    Post = EnumRegistry.get("post")
    pass


class NQueryWebSocketGrainDataSenderValue:
    """Inner value struct for NQueryWebSocketGrainDataSender."""

    __slots__ = (
        "Path",
        "Pre",
        "Post",
    )

    def __init__(self) -> None:
        self.Path: NString = NString()
        self.Pre: NSender = NSender()
        self.Post: NSender = NSender()

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
        self.Path.encode(engine, NQueryWebSocketGrainDataSenderEnums.Path)
        self.Pre.encode(engine, NQueryWebSocketGrainDataSenderEnums.Pre)
        self.Post.encode(engine, NQueryWebSocketGrainDataSenderEnums.Post)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NQueryWebSocketGrainDataSender")

        if NQueryWebSocketGrainDataSenderEnums.Path.s in data:
            self.Path.decode_value(data[NQueryWebSocketGrainDataSenderEnums.Path.s])
        if NQueryWebSocketGrainDataSenderEnums.Pre.s in data:
            self.Pre.decode_value(data[NQueryWebSocketGrainDataSenderEnums.Pre.s])
        if NQueryWebSocketGrainDataSenderEnums.Post.s in data:
            self.Post.decode_value(data[NQueryWebSocketGrainDataSenderEnums.Post.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NQueryWebSocketGrainDataSenderValue:
        o = NQueryWebSocketGrainDataSenderValue()
        o.Path = self.Path.clone()
        o.Pre = self.Pre.clone()
        o.Post = self.Post.clone()
        return o


class NQueryWebSocketGrainDataSender:
    """Optional object type: NQueryWebSocketGrainDataSender."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NQueryWebSocketGrainDataSenderValue = NQueryWebSocketGrainDataSenderValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NQueryWebSocketGrainDataSenderValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NQueryWebSocketGrainDataSenderValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NQueryWebSocketGrainDataSenderValue | None = None) -> NQueryWebSocketGrainDataSenderValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Path(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Path

    def set_Path(self, v: Any) -> None:
        assert self._defined, "NQueryWebSocketGrainDataSender must be defined before setting Path"
        _assign_value(self._value.Path, v)

    def get_Pre(self) -> NSender:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Pre

    def set_Pre(self, v: Any) -> None:
        assert self._defined, "NQueryWebSocketGrainDataSender must be defined before setting Pre"
        _assign_value(self._value.Pre, v)

    def get_Post(self) -> NSender:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Post

    def set_Post(self, v: Any) -> None:
        assert self._defined, "NQueryWebSocketGrainDataSender must be defined before setting Post"
        _assign_value(self._value.Post, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NQueryWebSocketGrainDataSenderValue()

    def clone(self) -> NQueryWebSocketGrainDataSender:
        o = NQueryWebSocketGrainDataSender()
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
            return f"NQueryWebSocketGrainDataSender(defined)"
        return "NQueryWebSocketGrainDataSender(<undefined>)"


def make_nquerywebsocketgraindatasender_value(v: NQueryWebSocketGrainDataSenderValue) -> NQueryWebSocketGrainDataSenderValue:
    """Factory: create a NQueryWebSocketGrainDataSenderValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nquerywebsocketgraindatasender(v: NQueryWebSocketGrainDataSenderValue) -> NQueryWebSocketGrainDataSender:
    """Factory: create a defined NQueryWebSocketGrainDataSender from a NQueryWebSocketGrainDataSenderValue."""
    o = NQueryWebSocketGrainDataSender()
    o.set_value(v)
    return o

