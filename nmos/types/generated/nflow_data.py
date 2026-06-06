"""Generated NMOS type: NFlowData. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NEnum
from nmos.types.generated.nflow_core import NFlowCore, NFlowCoreValue
from nmos.validators import CheckFormat

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NFlowDataEnums:
    """JSON property name enums for NFlowData."""
    Format = EnumRegistry.get("format")
    MediaType = EnumRegistry.get("media_type")
    pass


class NFlowDataValue:
    """Inner value struct for NFlowData."""

    __slots__ = (
        "FlowCore",
        "Format",
        "MediaType",
    )

    def __init__(self) -> None:
        self.FlowCore: NFlowCoreValue = NFlowCoreValue()
        self.Format: NEnum = NEnum()
        self.MediaType: NEnum = NEnum()

    def set_to_default(self) -> None:
        self.FlowCore = NFlowCoreValue()
        self.FlowCore.set_to_default()
        self.Format.set_to_default()
        self.MediaType.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Format.defined:
            raise InvalidObject("missing required member Format")
        if not self.MediaType.defined:
            raise InvalidObject("missing required member MediaType")
        if self.Format.defined:
            CheckFormat(self.Format)
        self.FlowCore.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.FlowCore.encode(engine, None)
        self.Format.encode(engine, NFlowDataEnums.Format)
        self.MediaType.encode(engine, NFlowDataEnums.MediaType)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NFlowData")

        self.FlowCore.decode(engine, data)
        if NFlowDataEnums.Format.s in data:
            self.Format.decode_value(data[NFlowDataEnums.Format.s])
        if NFlowDataEnums.MediaType.s in data:
            self.MediaType.decode_value(data[NFlowDataEnums.MediaType.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NFlowDataValue:
        o = NFlowDataValue()
        o.FlowCore = self.FlowCore.clone()
        o.Format = self.Format.clone()
        o.MediaType = self.MediaType.clone()
        return o


class NFlowData:
    """Optional object type: NFlowData."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NFlowDataValue = NFlowDataValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NFlowDataValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NFlowDataValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NFlowDataValue | None = None) -> NFlowDataValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_FlowCore(self) -> NFlowCoreValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FlowCore

    def set_FlowCore(self, v: NFlowCoreValue) -> None:
        assert self._defined, "NFlowData must be defined before setting FlowCore"
        self._value.FlowCore = v.clone()  # copy to match Go's value semantics

    def get_Format(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Format

    def set_Format(self, v: Any) -> None:
        assert self._defined, "NFlowData must be defined before setting Format"
        _assign_value(self._value.Format, v)

    def get_MediaType(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MediaType

    def set_MediaType(self, v: Any) -> None:
        assert self._defined, "NFlowData must be defined before setting MediaType"
        _assign_value(self._value.MediaType, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NFlowDataValue()

    def clone(self) -> NFlowData:
        o = NFlowData()
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
            return f"NFlowData(defined)"
        return "NFlowData(<undefined>)"


def make_nflowdata_value(v: NFlowDataValue) -> NFlowDataValue:
    """Factory: create a NFlowDataValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nflowdata(v: NFlowDataValue) -> NFlowData:
    """Factory: create a defined NFlowData from a NFlowDataValue."""
    o = NFlowData()
    o.set_value(v)
    return o

