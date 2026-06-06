"""Generated NMOS type: NQueryPayloadReceiver. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString, NTime
from nmos.types.generated.nrational import NRational, NRationalValue
from nmos.types.generated.nquery_web_socket_grain_receiver import NQueryWebSocketGrainReceiver, NQueryWebSocketGrainReceiverValue
from nmos.validators import CheckResourceIdString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NQueryPayloadReceiverEnums:
    """JSON property name enums for NQueryPayloadReceiver."""
    GrainType = EnumRegistry.get("grain_type")
    SourceId = EnumRegistry.get("source_id")
    FlowId = EnumRegistry.get("flow_id")
    OriginTimestamp = EnumRegistry.get("origin_timestamp")
    SyncTimestamp = EnumRegistry.get("sync_timestamp")
    CreationTimestamp = EnumRegistry.get("creation_timestamp")
    Rate = EnumRegistry.get("rate")
    Duration = EnumRegistry.get("duration")
    Grain = EnumRegistry.get("grain")
    pass


class NQueryPayloadReceiverValue:
    """Inner value struct for NQueryPayloadReceiver."""

    __slots__ = (
        "GrainType",
        "SourceId",
        "FlowId",
        "OriginTimestamp",
        "SyncTimestamp",
        "CreationTimestamp",
        "Rate",
        "Duration",
        "Grain",
    )

    def __init__(self) -> None:
        self.GrainType: NString = NString()
        self.SourceId: NString = NString()
        self.FlowId: NString = NString()
        self.OriginTimestamp: NTime = NTime()
        self.SyncTimestamp: NTime = NTime()
        self.CreationTimestamp: NTime = NTime()
        self.Rate: NRational = NRational()
        self.Duration: NRational = NRational()
        self.Grain: NQueryWebSocketGrainReceiver = NQueryWebSocketGrainReceiver()

    def set_to_default(self) -> None:
        self.GrainType.set_to_default()
        self.SourceId.set_to_default()
        self.FlowId.set_to_default()
        self.OriginTimestamp.set_to_default()
        self.SyncTimestamp.set_to_default()
        self.CreationTimestamp.set_to_default()
        self.Rate.set_to_default()
        self.Duration.set_to_default()
        self.Grain.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.GrainType.defined:
            raise InvalidObject("missing required member GrainType")
        if not self.SourceId.defined:
            raise InvalidObject("missing required member SourceId")
        if not self.FlowId.defined:
            raise InvalidObject("missing required member FlowId")
        if not self.OriginTimestamp.defined:
            raise InvalidObject("missing required member OriginTimestamp")
        if not self.SyncTimestamp.defined:
            raise InvalidObject("missing required member SyncTimestamp")
        if not self.CreationTimestamp.defined:
            raise InvalidObject("missing required member CreationTimestamp")
        if not self.Rate.defined:
            raise InvalidObject("missing required member Rate")
        if not self.Duration.defined:
            raise InvalidObject("missing required member Duration")
        if not self.Grain.defined:
            raise InvalidObject("missing required member Grain")
        if self.SourceId.defined:
            CheckResourceIdString(self.SourceId)
        if self.FlowId.defined:
            CheckResourceIdString(self.FlowId)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.GrainType.encode(engine, NQueryPayloadReceiverEnums.GrainType)
        self.SourceId.encode(engine, NQueryPayloadReceiverEnums.SourceId)
        self.FlowId.encode(engine, NQueryPayloadReceiverEnums.FlowId)
        self.OriginTimestamp.encode(engine, NQueryPayloadReceiverEnums.OriginTimestamp)
        self.SyncTimestamp.encode(engine, NQueryPayloadReceiverEnums.SyncTimestamp)
        self.CreationTimestamp.encode(engine, NQueryPayloadReceiverEnums.CreationTimestamp)
        self.Rate.encode(engine, NQueryPayloadReceiverEnums.Rate)
        self.Duration.encode(engine, NQueryPayloadReceiverEnums.Duration)
        self.Grain.encode(engine, NQueryPayloadReceiverEnums.Grain)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NQueryPayloadReceiver")

        if NQueryPayloadReceiverEnums.GrainType.s in data:
            self.GrainType.decode_value(data[NQueryPayloadReceiverEnums.GrainType.s])
        if NQueryPayloadReceiverEnums.SourceId.s in data:
            self.SourceId.decode_value(data[NQueryPayloadReceiverEnums.SourceId.s])
        if NQueryPayloadReceiverEnums.FlowId.s in data:
            self.FlowId.decode_value(data[NQueryPayloadReceiverEnums.FlowId.s])
        if NQueryPayloadReceiverEnums.OriginTimestamp.s in data:
            self.OriginTimestamp.decode_value(data[NQueryPayloadReceiverEnums.OriginTimestamp.s])
        if NQueryPayloadReceiverEnums.SyncTimestamp.s in data:
            self.SyncTimestamp.decode_value(data[NQueryPayloadReceiverEnums.SyncTimestamp.s])
        if NQueryPayloadReceiverEnums.CreationTimestamp.s in data:
            self.CreationTimestamp.decode_value(data[NQueryPayloadReceiverEnums.CreationTimestamp.s])
        if NQueryPayloadReceiverEnums.Rate.s in data:
            self.Rate.decode_value(data[NQueryPayloadReceiverEnums.Rate.s])
        if NQueryPayloadReceiverEnums.Duration.s in data:
            self.Duration.decode_value(data[NQueryPayloadReceiverEnums.Duration.s])
        if NQueryPayloadReceiverEnums.Grain.s in data:
            self.Grain.decode_value(data[NQueryPayloadReceiverEnums.Grain.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NQueryPayloadReceiverValue:
        o = NQueryPayloadReceiverValue()
        o.GrainType = self.GrainType.clone()
        o.SourceId = self.SourceId.clone()
        o.FlowId = self.FlowId.clone()
        o.OriginTimestamp = self.OriginTimestamp.clone()
        o.SyncTimestamp = self.SyncTimestamp.clone()
        o.CreationTimestamp = self.CreationTimestamp.clone()
        o.Rate = self.Rate.clone()
        o.Duration = self.Duration.clone()
        o.Grain = self.Grain.clone()
        return o


class NQueryPayloadReceiver:
    """Optional object type: NQueryPayloadReceiver."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NQueryPayloadReceiverValue = NQueryPayloadReceiverValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NQueryPayloadReceiverValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NQueryPayloadReceiverValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NQueryPayloadReceiverValue | None = None) -> NQueryPayloadReceiverValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_GrainType(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.GrainType

    def set_GrainType(self, v: Any) -> None:
        assert self._defined, "NQueryPayloadReceiver must be defined before setting GrainType"
        _assign_value(self._value.GrainType, v)

    def get_SourceId(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SourceId

    def set_SourceId(self, v: Any) -> None:
        assert self._defined, "NQueryPayloadReceiver must be defined before setting SourceId"
        _assign_value(self._value.SourceId, v)

    def get_FlowId(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FlowId

    def set_FlowId(self, v: Any) -> None:
        assert self._defined, "NQueryPayloadReceiver must be defined before setting FlowId"
        _assign_value(self._value.FlowId, v)

    def get_OriginTimestamp(self) -> NTime:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.OriginTimestamp

    def set_OriginTimestamp(self, v: Any) -> None:
        assert self._defined, "NQueryPayloadReceiver must be defined before setting OriginTimestamp"
        _assign_value(self._value.OriginTimestamp, v)

    def get_SyncTimestamp(self) -> NTime:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SyncTimestamp

    def set_SyncTimestamp(self, v: Any) -> None:
        assert self._defined, "NQueryPayloadReceiver must be defined before setting SyncTimestamp"
        _assign_value(self._value.SyncTimestamp, v)

    def get_CreationTimestamp(self) -> NTime:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.CreationTimestamp

    def set_CreationTimestamp(self, v: Any) -> None:
        assert self._defined, "NQueryPayloadReceiver must be defined before setting CreationTimestamp"
        _assign_value(self._value.CreationTimestamp, v)

    def get_Rate(self) -> NRational:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Rate

    def set_Rate(self, v: Any) -> None:
        assert self._defined, "NQueryPayloadReceiver must be defined before setting Rate"
        _assign_value(self._value.Rate, v)

    def get_Duration(self) -> NRational:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Duration

    def set_Duration(self, v: Any) -> None:
        assert self._defined, "NQueryPayloadReceiver must be defined before setting Duration"
        _assign_value(self._value.Duration, v)

    def get_Grain(self) -> NQueryWebSocketGrainReceiver:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Grain

    def set_Grain(self, v: Any) -> None:
        assert self._defined, "NQueryPayloadReceiver must be defined before setting Grain"
        _assign_value(self._value.Grain, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NQueryPayloadReceiverValue()

    def clone(self) -> NQueryPayloadReceiver:
        o = NQueryPayloadReceiver()
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
            return f"NQueryPayloadReceiver(defined)"
        return "NQueryPayloadReceiver(<undefined>)"


def make_nquerypayloadreceiver_value(v: NQueryPayloadReceiverValue) -> NQueryPayloadReceiverValue:
    """Factory: create a NQueryPayloadReceiverValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nquerypayloadreceiver(v: NQueryPayloadReceiverValue) -> NQueryPayloadReceiver:
    """Factory: create a defined NQueryPayloadReceiver from a NQueryPayloadReceiverValue."""
    o = NQueryPayloadReceiver()
    o.set_value(v)
    return o

