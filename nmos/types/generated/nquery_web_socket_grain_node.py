"""Generated NMOS type: NQueryWebSocketGrainNode. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString
from nmos.types.generated.narray_of_query_web_socket_grain_data_node import NArrayOfQueryWebSocketGrainDataNode, NArrayOfQueryWebSocketGrainDataNodeValue

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NQueryWebSocketGrainNodeEnums:
    """JSON property name enums for NQueryWebSocketGrainNode."""
    Type = EnumRegistry.get("type")
    Topic = EnumRegistry.get("topic")
    Data = EnumRegistry.get("data")
    pass


class NQueryWebSocketGrainNodeValue:
    """Inner value struct for NQueryWebSocketGrainNode."""

    __slots__ = (
        "Type",
        "Topic",
        "Data",
    )

    def __init__(self) -> None:
        self.Type: NString = NString()
        self.Topic: NString = NString()
        self.Data: NArrayOfQueryWebSocketGrainDataNode = NArrayOfQueryWebSocketGrainDataNode()

    def set_to_default(self) -> None:
        self.Type.set_to_default()
        self.Topic.set_to_default()
        self.Data.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Type.defined:
            raise InvalidObject("missing required member Type")
        if not self.Topic.defined:
            raise InvalidObject("missing required member Topic")
        if not self.Data.defined:
            raise InvalidObject("missing required member Data")
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Type.encode(engine, NQueryWebSocketGrainNodeEnums.Type)
        self.Topic.encode(engine, NQueryWebSocketGrainNodeEnums.Topic)
        self.Data.encode(engine, NQueryWebSocketGrainNodeEnums.Data)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NQueryWebSocketGrainNode")

        if NQueryWebSocketGrainNodeEnums.Type.s in data:
            self.Type.decode_value(data[NQueryWebSocketGrainNodeEnums.Type.s])
        if NQueryWebSocketGrainNodeEnums.Topic.s in data:
            self.Topic.decode_value(data[NQueryWebSocketGrainNodeEnums.Topic.s])
        if NQueryWebSocketGrainNodeEnums.Data.s in data:
            self.Data.decode_value(data[NQueryWebSocketGrainNodeEnums.Data.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NQueryWebSocketGrainNodeValue:
        o = NQueryWebSocketGrainNodeValue()
        o.Type = self.Type.clone()
        o.Topic = self.Topic.clone()
        o.Data = self.Data.clone()
        return o


class NQueryWebSocketGrainNode:
    """Optional object type: NQueryWebSocketGrainNode."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NQueryWebSocketGrainNodeValue = NQueryWebSocketGrainNodeValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NQueryWebSocketGrainNodeValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NQueryWebSocketGrainNodeValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NQueryWebSocketGrainNodeValue | None = None) -> NQueryWebSocketGrainNodeValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Type(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Type

    def set_Type(self, v: Any) -> None:
        assert self._defined, "NQueryWebSocketGrainNode must be defined before setting Type"
        _assign_value(self._value.Type, v)

    def get_Topic(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Topic

    def set_Topic(self, v: Any) -> None:
        assert self._defined, "NQueryWebSocketGrainNode must be defined before setting Topic"
        _assign_value(self._value.Topic, v)

    def get_Data(self) -> NArrayOfQueryWebSocketGrainDataNode:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Data

    def set_Data(self, v: Any) -> None:
        assert self._defined, "NQueryWebSocketGrainNode must be defined before setting Data"
        _assign_value(self._value.Data, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NQueryWebSocketGrainNodeValue()

    def clone(self) -> NQueryWebSocketGrainNode:
        o = NQueryWebSocketGrainNode()
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
            return f"NQueryWebSocketGrainNode(defined)"
        return "NQueryWebSocketGrainNode(<undefined>)"


def make_nquerywebsocketgrainnode_value(v: NQueryWebSocketGrainNodeValue) -> NQueryWebSocketGrainNodeValue:
    """Factory: create a NQueryWebSocketGrainNodeValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nquerywebsocketgrainnode(v: NQueryWebSocketGrainNodeValue) -> NQueryWebSocketGrainNode:
    """Factory: create a defined NQueryWebSocketGrainNode from a NQueryWebSocketGrainNodeValue."""
    o = NQueryWebSocketGrainNode()
    o.set_value(v)
    return o

