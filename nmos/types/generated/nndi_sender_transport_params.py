"""Generated NMOS type: NNdiSenderTransportParams. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString, NNull
from nmos.validators import CheckAutoPort

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NNdiSenderTransportParamsEnums:
    """JSON property name enums for NNdiSenderTransportParams."""
    SourceIp = EnumRegistry.get("source_ip")
    SourcePort = EnumRegistry.get("source_port")
    SourceName = EnumRegistry.get("source_name")
    MachineName = EnumRegistry.get("machine_name")
    pass


class NNdiSenderTransportParamsValue:
    """Inner value struct for NNdiSenderTransportParams."""

    __slots__ = (
        "SourceIp",
        "SourcePort",
        "SourceName",
        "MachineName",
    )

    def __init__(self) -> None:
        self.SourceIp: NString = NString()
        self.SourcePort: NNull = NNull()
        self.SourceName: NString = NString()
        self.MachineName: NString = NString()

    def set_to_default(self) -> None:
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if self.SourcePort.defined:
            CheckAutoPort(self.SourcePort)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.SourceIp.encode(engine, NNdiSenderTransportParamsEnums.SourceIp)
        self.SourcePort.encode(engine, NNdiSenderTransportParamsEnums.SourcePort)
        self.SourceName.encode(engine, NNdiSenderTransportParamsEnums.SourceName)
        self.MachineName.encode(engine, NNdiSenderTransportParamsEnums.MachineName)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NNdiSenderTransportParams")

        if NNdiSenderTransportParamsEnums.SourceIp.s in data:
            self.SourceIp.decode_value(data[NNdiSenderTransportParamsEnums.SourceIp.s])
        if NNdiSenderTransportParamsEnums.SourcePort.s in data:
            self.SourcePort.decode_value(data[NNdiSenderTransportParamsEnums.SourcePort.s])
        if NNdiSenderTransportParamsEnums.SourceName.s in data:
            self.SourceName.decode_value(data[NNdiSenderTransportParamsEnums.SourceName.s])
        if NNdiSenderTransportParamsEnums.MachineName.s in data:
            self.MachineName.decode_value(data[NNdiSenderTransportParamsEnums.MachineName.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NNdiSenderTransportParamsValue:
        o = NNdiSenderTransportParamsValue()
        o.SourceIp = self.SourceIp.clone()
        o.SourcePort = self.SourcePort.clone()
        o.SourceName = self.SourceName.clone()
        o.MachineName = self.MachineName.clone()
        return o


class NNdiSenderTransportParams:
    """Optional object type: NNdiSenderTransportParams."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NNdiSenderTransportParamsValue = NNdiSenderTransportParamsValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NNdiSenderTransportParamsValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NNdiSenderTransportParamsValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NNdiSenderTransportParamsValue | None = None) -> NNdiSenderTransportParamsValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_SourceIp(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SourceIp

    def set_SourceIp(self, v: Any) -> None:
        assert self._defined, "NNdiSenderTransportParams must be defined before setting SourceIp"
        _assign_value(self._value.SourceIp, v)

    def get_SourcePort(self) -> NNull:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SourcePort

    def set_SourcePort(self, v: Any) -> None:
        assert self._defined, "NNdiSenderTransportParams must be defined before setting SourcePort"
        _assign_value(self._value.SourcePort, v)

    def get_SourceName(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SourceName

    def set_SourceName(self, v: Any) -> None:
        assert self._defined, "NNdiSenderTransportParams must be defined before setting SourceName"
        _assign_value(self._value.SourceName, v)

    def get_MachineName(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MachineName

    def set_MachineName(self, v: Any) -> None:
        assert self._defined, "NNdiSenderTransportParams must be defined before setting MachineName"
        _assign_value(self._value.MachineName, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NNdiSenderTransportParamsValue()

    def clone(self) -> NNdiSenderTransportParams:
        o = NNdiSenderTransportParams()
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
            return f"NNdiSenderTransportParams(defined)"
        return "NNdiSenderTransportParams(<undefined>)"


def make_nndisendertransportparams_value(v: NNdiSenderTransportParamsValue) -> NNdiSenderTransportParamsValue:
    """Factory: create a NNdiSenderTransportParamsValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nndisendertransportparams(v: NNdiSenderTransportParamsValue) -> NNdiSenderTransportParams:
    """Factory: create a defined NNdiSenderTransportParams from a NNdiSenderTransportParamsValue."""
    o = NNdiSenderTransportParams()
    o.set_value(v)
    return o

