"""Generated NMOS type: NQueryWebSocketGrainFlow. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString
from nmos.types.generated.narray_of_query_web_socket_grain_data_flow import NArrayOfQueryWebSocketGrainDataFlow, NArrayOfQueryWebSocketGrainDataFlowValue

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NQueryWebSocketGrainFlowEnums:
    """JSON property name enums for NQueryWebSocketGrainFlow."""
    Type = EnumRegistry.get("type")
    Topic = EnumRegistry.get("topic")
    Data = EnumRegistry.get("data")
    pass


class NQueryWebSocketGrainFlowValue:
    """Inner value struct for NQueryWebSocketGrainFlow."""

    __slots__ = (
        "Type",
        "Topic",
        "Data",
    )

    def __init__(self) -> None:
        self.Type: NString = NString()
        self.Topic: NString = NString()
        self.Data: NArrayOfQueryWebSocketGrainDataFlow = NArrayOfQueryWebSocketGrainDataFlow()

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
        self.Type.encode(engine, NQueryWebSocketGrainFlowEnums.Type)
        self.Topic.encode(engine, NQueryWebSocketGrainFlowEnums.Topic)
        self.Data.encode(engine, NQueryWebSocketGrainFlowEnums.Data)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NQueryWebSocketGrainFlow")

        if NQueryWebSocketGrainFlowEnums.Type.s in data:
            self.Type.decode_value(data[NQueryWebSocketGrainFlowEnums.Type.s])
        if NQueryWebSocketGrainFlowEnums.Topic.s in data:
            self.Topic.decode_value(data[NQueryWebSocketGrainFlowEnums.Topic.s])
        if NQueryWebSocketGrainFlowEnums.Data.s in data:
            self.Data.decode_value(data[NQueryWebSocketGrainFlowEnums.Data.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NQueryWebSocketGrainFlowValue:
        o = NQueryWebSocketGrainFlowValue()
        o.Type = self.Type.clone()
        o.Topic = self.Topic.clone()
        o.Data = self.Data.clone()
        return o


class NQueryWebSocketGrainFlow:
    """Optional object type: NQueryWebSocketGrainFlow."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NQueryWebSocketGrainFlowValue = NQueryWebSocketGrainFlowValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NQueryWebSocketGrainFlowValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NQueryWebSocketGrainFlowValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NQueryWebSocketGrainFlowValue | None = None) -> NQueryWebSocketGrainFlowValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Type(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Type

    def set_Type(self, v: Any) -> None:
        assert self._defined, "NQueryWebSocketGrainFlow must be defined before setting Type"
        _assign_value(self._value.Type, v)

    def get_Topic(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Topic

    def set_Topic(self, v: Any) -> None:
        assert self._defined, "NQueryWebSocketGrainFlow must be defined before setting Topic"
        _assign_value(self._value.Topic, v)

    def get_Data(self) -> NArrayOfQueryWebSocketGrainDataFlow:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Data

    def set_Data(self, v: Any) -> None:
        assert self._defined, "NQueryWebSocketGrainFlow must be defined before setting Data"
        _assign_value(self._value.Data, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NQueryWebSocketGrainFlowValue()

    def clone(self) -> NQueryWebSocketGrainFlow:
        o = NQueryWebSocketGrainFlow()
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
            return f"NQueryWebSocketGrainFlow(defined)"
        return "NQueryWebSocketGrainFlow(<undefined>)"


def make_nquerywebsocketgrainflow_value(v: NQueryWebSocketGrainFlowValue) -> NQueryWebSocketGrainFlowValue:
    """Factory: create a NQueryWebSocketGrainFlowValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nquerywebsocketgrainflow(v: NQueryWebSocketGrainFlowValue) -> NQueryWebSocketGrainFlow:
    """Factory: create a defined NQueryWebSocketGrainFlow from a NQueryWebSocketGrainFlowValue."""
    o = NQueryWebSocketGrainFlow()
    o.set_value(v)
    return o

