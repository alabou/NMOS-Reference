"""Generated NMOS type: NQueryWebSocketGrainDataGeneric. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString, NGeneric

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NQueryWebSocketGrainDataGenericEnums:
    """JSON property name enums for NQueryWebSocketGrainDataGeneric."""
    Path = EnumRegistry.get("path")
    Pre = EnumRegistry.get("pre")
    Post = EnumRegistry.get("post")
    pass


class NQueryWebSocketGrainDataGenericValue:
    """Inner value struct for NQueryWebSocketGrainDataGeneric."""

    __slots__ = (
        "Path",
        "Pre",
        "Post",
    )

    def __init__(self) -> None:
        self.Path: NString = NString()
        self.Pre: NGeneric = NGeneric()
        self.Post: NGeneric = NGeneric()

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
        self.Path.encode(engine, NQueryWebSocketGrainDataGenericEnums.Path)
        self.Pre.encode(engine, NQueryWebSocketGrainDataGenericEnums.Pre)
        self.Post.encode(engine, NQueryWebSocketGrainDataGenericEnums.Post)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NQueryWebSocketGrainDataGeneric")

        if NQueryWebSocketGrainDataGenericEnums.Path.s in data:
            self.Path.decode_value(data[NQueryWebSocketGrainDataGenericEnums.Path.s])
        if NQueryWebSocketGrainDataGenericEnums.Pre.s in data:
            self.Pre.decode_value(data[NQueryWebSocketGrainDataGenericEnums.Pre.s])
        if NQueryWebSocketGrainDataGenericEnums.Post.s in data:
            self.Post.decode_value(data[NQueryWebSocketGrainDataGenericEnums.Post.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NQueryWebSocketGrainDataGenericValue:
        o = NQueryWebSocketGrainDataGenericValue()
        o.Path = self.Path.clone()
        o.Pre = self.Pre.clone()
        o.Post = self.Post.clone()
        return o


class NQueryWebSocketGrainDataGeneric:
    """Optional object type: NQueryWebSocketGrainDataGeneric."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NQueryWebSocketGrainDataGenericValue = NQueryWebSocketGrainDataGenericValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NQueryWebSocketGrainDataGenericValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NQueryWebSocketGrainDataGenericValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NQueryWebSocketGrainDataGenericValue | None = None) -> NQueryWebSocketGrainDataGenericValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Path(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Path

    def set_Path(self, v: Any) -> None:
        assert self._defined, "NQueryWebSocketGrainDataGeneric must be defined before setting Path"
        _assign_value(self._value.Path, v)

    def get_Pre(self) -> NGeneric:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Pre

    def set_Pre(self, v: Any) -> None:
        assert self._defined, "NQueryWebSocketGrainDataGeneric must be defined before setting Pre"
        _assign_value(self._value.Pre, v)

    def get_Post(self) -> NGeneric:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Post

    def set_Post(self, v: Any) -> None:
        assert self._defined, "NQueryWebSocketGrainDataGeneric must be defined before setting Post"
        _assign_value(self._value.Post, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NQueryWebSocketGrainDataGenericValue()

    def clone(self) -> NQueryWebSocketGrainDataGeneric:
        o = NQueryWebSocketGrainDataGeneric()
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
            return f"NQueryWebSocketGrainDataGeneric(defined)"
        return "NQueryWebSocketGrainDataGeneric(<undefined>)"


def make_nquerywebsocketgraindatageneric_value(v: NQueryWebSocketGrainDataGenericValue) -> NQueryWebSocketGrainDataGenericValue:
    """Factory: create a NQueryWebSocketGrainDataGenericValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nquerywebsocketgraindatageneric(v: NQueryWebSocketGrainDataGenericValue) -> NQueryWebSocketGrainDataGeneric:
    """Factory: create a defined NQueryWebSocketGrainDataGeneric from a NQueryWebSocketGrainDataGenericValue."""
    o = NQueryWebSocketGrainDataGeneric()
    o.set_value(v)
    return o

