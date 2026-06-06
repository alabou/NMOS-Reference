"""Generated NMOS type: NFlowDataSdianc. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NEnum
from nmos.types.generated.nflow_core import NFlowCore, NFlowCoreValue
from nmos.types.generated.narray_of_did_sdid import NArrayOfDidSdid, NArrayOfDidSdidValue
from nmos.validators import CheckFormat, CheckDidSdid

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NFlowDataSdiancEnums:
    """JSON property name enums for NFlowDataSdianc."""
    Format = EnumRegistry.get("format")
    MediaType = EnumRegistry.get("media_type")
    DidSdid = EnumRegistry.get("DID_SDID")
    pass


class NFlowDataSdiancValue:
    """Inner value struct for NFlowDataSdianc."""

    __slots__ = (
        "FlowCore",
        "Format",
        "MediaType",
        "DidSdid",
    )

    def __init__(self) -> None:
        self.FlowCore: NFlowCoreValue = NFlowCoreValue()
        self.Format: NEnum = NEnum()
        self.MediaType: NEnum = NEnum()
        self.DidSdid: NArrayOfDidSdid = NArrayOfDidSdid()

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
        if self.DidSdid.defined:
            CheckDidSdid(self.DidSdid)
        self.FlowCore.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.FlowCore.encode(engine, None)
        self.Format.encode(engine, NFlowDataSdiancEnums.Format)
        self.MediaType.encode(engine, NFlowDataSdiancEnums.MediaType)
        self.DidSdid.encode(engine, NFlowDataSdiancEnums.DidSdid)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NFlowDataSdianc")

        self.FlowCore.decode(engine, data)
        if NFlowDataSdiancEnums.Format.s in data:
            self.Format.decode_value(data[NFlowDataSdiancEnums.Format.s])
        if NFlowDataSdiancEnums.MediaType.s in data:
            self.MediaType.decode_value(data[NFlowDataSdiancEnums.MediaType.s])
        if NFlowDataSdiancEnums.DidSdid.s in data:
            self.DidSdid.decode_value(data[NFlowDataSdiancEnums.DidSdid.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NFlowDataSdiancValue:
        o = NFlowDataSdiancValue()
        o.FlowCore = self.FlowCore.clone()
        o.Format = self.Format.clone()
        o.MediaType = self.MediaType.clone()
        o.DidSdid = self.DidSdid.clone()
        return o


class NFlowDataSdianc:
    """Optional object type: NFlowDataSdianc."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NFlowDataSdiancValue = NFlowDataSdiancValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NFlowDataSdiancValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NFlowDataSdiancValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NFlowDataSdiancValue | None = None) -> NFlowDataSdiancValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_FlowCore(self) -> NFlowCoreValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FlowCore

    def set_FlowCore(self, v: NFlowCoreValue) -> None:
        assert self._defined, "NFlowDataSdianc must be defined before setting FlowCore"
        self._value.FlowCore = v.clone()  # copy to match Go's value semantics

    def get_Format(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Format

    def set_Format(self, v: Any) -> None:
        assert self._defined, "NFlowDataSdianc must be defined before setting Format"
        _assign_value(self._value.Format, v)

    def get_MediaType(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MediaType

    def set_MediaType(self, v: Any) -> None:
        assert self._defined, "NFlowDataSdianc must be defined before setting MediaType"
        _assign_value(self._value.MediaType, v)

    def get_DidSdid(self) -> NArrayOfDidSdid:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.DidSdid

    def set_DidSdid(self, v: Any) -> None:
        assert self._defined, "NFlowDataSdianc must be defined before setting DidSdid"
        _assign_value(self._value.DidSdid, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NFlowDataSdiancValue()

    def clone(self) -> NFlowDataSdianc:
        o = NFlowDataSdianc()
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
            return f"NFlowDataSdianc(defined)"
        return "NFlowDataSdianc(<undefined>)"


def make_nflowdatasdianc_value(v: NFlowDataSdiancValue) -> NFlowDataSdiancValue:
    """Factory: create a NFlowDataSdiancValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nflowdatasdianc(v: NFlowDataSdiancValue) -> NFlowDataSdianc:
    """Factory: create a defined NFlowDataSdianc from a NFlowDataSdiancValue."""
    o = NFlowDataSdianc()
    o.set_value(v)
    return o

