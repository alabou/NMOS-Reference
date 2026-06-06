"""Generated NMOS type: NFlowAudioRaw. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NEnum, NInt
from nmos.types.generated.nflow_core import NFlowCore, NFlowCoreValue
from nmos.types.generated.nrational import NRational, NRationalValue
from nmos.validators import CheckFormat

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NFlowAudioRawEnums:
    """JSON property name enums for NFlowAudioRaw."""
    Format = EnumRegistry.get("format")
    MediaType = EnumRegistry.get("media_type")
    BitDepth = EnumRegistry.get("bit_depth")
    SampleRate = EnumRegistry.get("sample_rate")
    pass


class NFlowAudioRawValue:
    """Inner value struct for NFlowAudioRaw."""

    __slots__ = (
        "FlowCore",
        "Format",
        "MediaType",
        "BitDepth",
        "SampleRate",
    )

    def __init__(self) -> None:
        self.FlowCore: NFlowCoreValue = NFlowCoreValue()
        self.Format: NEnum = NEnum()
        self.MediaType: NEnum = NEnum()
        self.BitDepth: NInt = NInt()
        self.SampleRate: NRational = NRational()

    def set_to_default(self) -> None:
        self.FlowCore = NFlowCoreValue()
        self.FlowCore.set_to_default()
        self.Format.set_to_default()
        self.MediaType.set_to_default()
        self.BitDepth.set_to_default()
        self.SampleRate.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Format.defined:
            raise InvalidObject("missing required member Format")
        if not self.MediaType.defined:
            raise InvalidObject("missing required member MediaType")
        if not self.BitDepth.defined:
            raise InvalidObject("missing required member BitDepth")
        if not self.SampleRate.defined:
            raise InvalidObject("missing required member SampleRate")
        if self.Format.defined:
            CheckFormat(self.Format)
        self.FlowCore.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.FlowCore.encode(engine, None)
        self.Format.encode(engine, NFlowAudioRawEnums.Format)
        self.MediaType.encode(engine, NFlowAudioRawEnums.MediaType)
        self.BitDepth.encode(engine, NFlowAudioRawEnums.BitDepth)
        self.SampleRate.encode(engine, NFlowAudioRawEnums.SampleRate)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NFlowAudioRaw")

        self.FlowCore.decode(engine, data)
        if NFlowAudioRawEnums.Format.s in data:
            self.Format.decode_value(data[NFlowAudioRawEnums.Format.s])
        if NFlowAudioRawEnums.MediaType.s in data:
            self.MediaType.decode_value(data[NFlowAudioRawEnums.MediaType.s])
        if NFlowAudioRawEnums.BitDepth.s in data:
            self.BitDepth.decode_value(data[NFlowAudioRawEnums.BitDepth.s])
        if NFlowAudioRawEnums.SampleRate.s in data:
            self.SampleRate.decode_value(data[NFlowAudioRawEnums.SampleRate.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NFlowAudioRawValue:
        o = NFlowAudioRawValue()
        o.FlowCore = self.FlowCore.clone()
        o.Format = self.Format.clone()
        o.MediaType = self.MediaType.clone()
        o.BitDepth = self.BitDepth.clone()
        o.SampleRate = self.SampleRate.clone()
        return o


class NFlowAudioRaw:
    """Optional object type: NFlowAudioRaw."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NFlowAudioRawValue = NFlowAudioRawValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NFlowAudioRawValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NFlowAudioRawValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NFlowAudioRawValue | None = None) -> NFlowAudioRawValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_FlowCore(self) -> NFlowCoreValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FlowCore

    def set_FlowCore(self, v: NFlowCoreValue) -> None:
        assert self._defined, "NFlowAudioRaw must be defined before setting FlowCore"
        self._value.FlowCore = v.clone()  # copy to match Go's value semantics

    def get_Format(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Format

    def set_Format(self, v: Any) -> None:
        assert self._defined, "NFlowAudioRaw must be defined before setting Format"
        _assign_value(self._value.Format, v)

    def get_MediaType(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MediaType

    def set_MediaType(self, v: Any) -> None:
        assert self._defined, "NFlowAudioRaw must be defined before setting MediaType"
        _assign_value(self._value.MediaType, v)

    def get_BitDepth(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.BitDepth

    def set_BitDepth(self, v: Any) -> None:
        assert self._defined, "NFlowAudioRaw must be defined before setting BitDepth"
        _assign_value(self._value.BitDepth, v)

    def get_SampleRate(self) -> NRational:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SampleRate

    def set_SampleRate(self, v: Any) -> None:
        assert self._defined, "NFlowAudioRaw must be defined before setting SampleRate"
        _assign_value(self._value.SampleRate, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NFlowAudioRawValue()

    def clone(self) -> NFlowAudioRaw:
        o = NFlowAudioRaw()
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
            return f"NFlowAudioRaw(defined)"
        return "NFlowAudioRaw(<undefined>)"


def make_nflowaudioraw_value(v: NFlowAudioRawValue) -> NFlowAudioRawValue:
    """Factory: create a NFlowAudioRawValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nflowaudioraw(v: NFlowAudioRawValue) -> NFlowAudioRaw:
    """Factory: create a defined NFlowAudioRaw from a NFlowAudioRawValue."""
    o = NFlowAudioRaw()
    o.set_value(v)
    return o

