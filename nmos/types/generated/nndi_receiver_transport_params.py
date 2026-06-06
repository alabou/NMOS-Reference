"""Generated NMOS type: NNdiReceiverTransportParams. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString, NNullString, NNull
from nmos.validators import CheckNullPort

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NNdiReceiverTransportParamsEnums:
    """JSON property name enums for NNdiReceiverTransportParams."""
    InterfaceIp = EnumRegistry.get("interface_ip")
    SourceIp = EnumRegistry.get("source_ip")
    SourcePort = EnumRegistry.get("source_port")
    SourceName = EnumRegistry.get("source_name")
    MachineName = EnumRegistry.get("machine_name")
    pass


class NNdiReceiverTransportParamsValue:
    """Inner value struct for NNdiReceiverTransportParams."""

    __slots__ = (
        "InterfaceIp",
        "SourceIp",
        "SourcePort",
        "SourceName",
        "MachineName",
    )

    def __init__(self) -> None:
        self.InterfaceIp: NString = NString()
        self.SourceIp: NNullString = NNullString()
        self.SourcePort: NNull = NNull()
        self.SourceName: NNullString = NNullString()
        self.MachineName: NNullString = NNullString()

    def set_to_default(self) -> None:
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if self.SourcePort.defined:
            CheckNullPort(self.SourcePort)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.InterfaceIp.encode(engine, NNdiReceiverTransportParamsEnums.InterfaceIp)
        self.SourceIp.encode(engine, NNdiReceiverTransportParamsEnums.SourceIp)
        self.SourcePort.encode(engine, NNdiReceiverTransportParamsEnums.SourcePort)
        self.SourceName.encode(engine, NNdiReceiverTransportParamsEnums.SourceName)
        self.MachineName.encode(engine, NNdiReceiverTransportParamsEnums.MachineName)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NNdiReceiverTransportParams")

        if NNdiReceiverTransportParamsEnums.InterfaceIp.s in data:
            self.InterfaceIp.decode_value(data[NNdiReceiverTransportParamsEnums.InterfaceIp.s])
        if NNdiReceiverTransportParamsEnums.SourceIp.s in data:
            self.SourceIp.decode_value(data[NNdiReceiverTransportParamsEnums.SourceIp.s])
        if NNdiReceiverTransportParamsEnums.SourcePort.s in data:
            self.SourcePort.decode_value(data[NNdiReceiverTransportParamsEnums.SourcePort.s])
        if NNdiReceiverTransportParamsEnums.SourceName.s in data:
            self.SourceName.decode_value(data[NNdiReceiverTransportParamsEnums.SourceName.s])
        if NNdiReceiverTransportParamsEnums.MachineName.s in data:
            self.MachineName.decode_value(data[NNdiReceiverTransportParamsEnums.MachineName.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NNdiReceiverTransportParamsValue:
        o = NNdiReceiverTransportParamsValue()
        o.InterfaceIp = self.InterfaceIp.clone()
        o.SourceIp = self.SourceIp.clone()
        o.SourcePort = self.SourcePort.clone()
        o.SourceName = self.SourceName.clone()
        o.MachineName = self.MachineName.clone()
        return o


class NNdiReceiverTransportParams:
    """Optional object type: NNdiReceiverTransportParams."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NNdiReceiverTransportParamsValue = NNdiReceiverTransportParamsValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NNdiReceiverTransportParamsValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NNdiReceiverTransportParamsValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NNdiReceiverTransportParamsValue | None = None) -> NNdiReceiverTransportParamsValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_InterfaceIp(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.InterfaceIp

    def set_InterfaceIp(self, v: Any) -> None:
        assert self._defined, "NNdiReceiverTransportParams must be defined before setting InterfaceIp"
        _assign_value(self._value.InterfaceIp, v)

    def get_SourceIp(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SourceIp

    def set_SourceIp(self, v: Any) -> None:
        assert self._defined, "NNdiReceiverTransportParams must be defined before setting SourceIp"
        _assign_value(self._value.SourceIp, v)

    def get_SourcePort(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SourcePort

    def set_SourcePort(self, v: Any) -> None:
        assert self._defined, "NNdiReceiverTransportParams must be defined before setting SourcePort"
        _assign_value(self._value.SourcePort, v)

    def get_SourceName(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SourceName

    def set_SourceName(self, v: Any) -> None:
        assert self._defined, "NNdiReceiverTransportParams must be defined before setting SourceName"
        _assign_value(self._value.SourceName, v)

    def get_MachineName(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MachineName

    def set_MachineName(self, v: Any) -> None:
        assert self._defined, "NNdiReceiverTransportParams must be defined before setting MachineName"
        _assign_value(self._value.MachineName, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NNdiReceiverTransportParamsValue()

    def clone(self) -> NNdiReceiverTransportParams:
        o = NNdiReceiverTransportParams()
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
            return f"NNdiReceiverTransportParams(defined)"
        return "NNdiReceiverTransportParams(<undefined>)"


def make_nndireceivertransportparams_value(v: NNdiReceiverTransportParamsValue) -> NNdiReceiverTransportParamsValue:
    """Factory: create a NNdiReceiverTransportParamsValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nndireceivertransportparams(v: NNdiReceiverTransportParamsValue) -> NNdiReceiverTransportParams:
    """Factory: create a defined NNdiReceiverTransportParams from a NNdiReceiverTransportParamsValue."""
    o = NNdiReceiverTransportParams()
    o.set_value(v)
    return o

