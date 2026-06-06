"""Generated NMOS type: NFlowMux. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NEnum, NInt
from nmos.types.generated.nflow_core import NFlowCore, NFlowCoreValue
from nmos.validators import CheckFormat

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NFlowMuxEnums:
    """JSON property name enums for NFlowMux."""
    Format = EnumRegistry.get("format")
    MediaType = EnumRegistry.get("media_type")
    VideoLayers = EnumRegistry.get("urn:x-matrox:video_layers")
    AudioLayers = EnumRegistry.get("urn:x-matrox:audio_layers")
    DataLayers = EnumRegistry.get("urn:x-matrox:data_layers")
    pass


class NFlowMuxValue:
    """Inner value struct for NFlowMux."""

    __slots__ = (
        "FlowCore",
        "Format",
        "MediaType",
        "VideoLayers",
        "AudioLayers",
        "DataLayers",
    )

    def __init__(self) -> None:
        self.FlowCore: NFlowCoreValue = NFlowCoreValue()
        self.Format: NEnum = NEnum()
        self.MediaType: NEnum = NEnum()
        self.VideoLayers: NInt = NInt()
        self.AudioLayers: NInt = NInt()
        self.DataLayers: NInt = NInt()

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
        self.Format.encode(engine, NFlowMuxEnums.Format)
        self.MediaType.encode(engine, NFlowMuxEnums.MediaType)
        self.VideoLayers.encode(engine, NFlowMuxEnums.VideoLayers)
        self.AudioLayers.encode(engine, NFlowMuxEnums.AudioLayers)
        self.DataLayers.encode(engine, NFlowMuxEnums.DataLayers)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NFlowMux")

        self.FlowCore.decode(engine, data)
        if NFlowMuxEnums.Format.s in data:
            self.Format.decode_value(data[NFlowMuxEnums.Format.s])
        if NFlowMuxEnums.MediaType.s in data:
            self.MediaType.decode_value(data[NFlowMuxEnums.MediaType.s])
        if NFlowMuxEnums.VideoLayers.s in data:
            self.VideoLayers.decode_value(data[NFlowMuxEnums.VideoLayers.s])
        if NFlowMuxEnums.AudioLayers.s in data:
            self.AudioLayers.decode_value(data[NFlowMuxEnums.AudioLayers.s])
        if NFlowMuxEnums.DataLayers.s in data:
            self.DataLayers.decode_value(data[NFlowMuxEnums.DataLayers.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NFlowMuxValue:
        o = NFlowMuxValue()
        o.FlowCore = self.FlowCore.clone()
        o.Format = self.Format.clone()
        o.MediaType = self.MediaType.clone()
        o.VideoLayers = self.VideoLayers.clone()
        o.AudioLayers = self.AudioLayers.clone()
        o.DataLayers = self.DataLayers.clone()
        return o


class NFlowMux:
    """Optional object type: NFlowMux."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NFlowMuxValue = NFlowMuxValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NFlowMuxValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NFlowMuxValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NFlowMuxValue | None = None) -> NFlowMuxValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_FlowCore(self) -> NFlowCoreValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FlowCore

    def set_FlowCore(self, v: NFlowCoreValue) -> None:
        assert self._defined, "NFlowMux must be defined before setting FlowCore"
        self._value.FlowCore = v.clone()  # copy to match Go's value semantics

    def get_Format(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Format

    def set_Format(self, v: Any) -> None:
        assert self._defined, "NFlowMux must be defined before setting Format"
        _assign_value(self._value.Format, v)

    def get_MediaType(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MediaType

    def set_MediaType(self, v: Any) -> None:
        assert self._defined, "NFlowMux must be defined before setting MediaType"
        _assign_value(self._value.MediaType, v)

    def get_VideoLayers(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.VideoLayers

    def set_VideoLayers(self, v: Any) -> None:
        assert self._defined, "NFlowMux must be defined before setting VideoLayers"
        _assign_value(self._value.VideoLayers, v)

    def get_AudioLayers(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.AudioLayers

    def set_AudioLayers(self, v: Any) -> None:
        assert self._defined, "NFlowMux must be defined before setting AudioLayers"
        _assign_value(self._value.AudioLayers, v)

    def get_DataLayers(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.DataLayers

    def set_DataLayers(self, v: Any) -> None:
        assert self._defined, "NFlowMux must be defined before setting DataLayers"
        _assign_value(self._value.DataLayers, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NFlowMuxValue()

    def clone(self) -> NFlowMux:
        o = NFlowMux()
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
            return f"NFlowMux(defined)"
        return "NFlowMux(<undefined>)"


def make_nflowmux_value(v: NFlowMuxValue) -> NFlowMuxValue:
    """Factory: create a NFlowMuxValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nflowmux(v: NFlowMuxValue) -> NFlowMux:
    """Factory: create a defined NFlowMux from a NFlowMuxValue."""
    o = NFlowMux()
    o.set_value(v)
    return o

