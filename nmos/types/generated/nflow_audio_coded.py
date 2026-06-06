"""Generated NMOS type: NFlowAudioCoded. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NEnum, NInt, NBool
from nmos.types.generated.nflow_core import NFlowCore, NFlowCoreValue
from nmos.types.generated.nrational import NRational, NRationalValue
from nmos.validators import CheckFormat

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NFlowAudioCodedEnums:
    """JSON property name enums for NFlowAudioCoded."""
    Format = EnumRegistry.get("format")
    MediaType = EnumRegistry.get("media_type")
    SampleRate = EnumRegistry.get("sample_rate")
    Profile = EnumRegistry.get("profile")
    Level = EnumRegistry.get("level")
    Bitrate = EnumRegistry.get("bit_rate")
    ConstantBitrate = EnumRegistry.get("constant_bit_rate")
    pass


class NFlowAudioCodedValue:
    """Inner value struct for NFlowAudioCoded."""

    __slots__ = (
        "FlowCore",
        "Format",
        "MediaType",
        "SampleRate",
        "Profile",
        "Level",
        "Bitrate",
        "ConstantBitrate",
    )

    def __init__(self) -> None:
        self.FlowCore: NFlowCoreValue = NFlowCoreValue()
        self.Format: NEnum = NEnum()
        self.MediaType: NEnum = NEnum()
        self.SampleRate: NRational = NRational()
        self.Profile: NEnum = NEnum()
        self.Level: NEnum = NEnum()
        self.Bitrate: NInt = NInt()
        self.ConstantBitrate: NBool = NBool()

    def set_to_default(self) -> None:
        self.FlowCore = NFlowCoreValue()
        self.FlowCore.set_to_default()
        self.Format.set_to_default()
        self.MediaType.set_to_default()
        self.SampleRate.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Format.defined:
            raise InvalidObject("missing required member Format")
        if not self.MediaType.defined:
            raise InvalidObject("missing required member MediaType")
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
        self.Format.encode(engine, NFlowAudioCodedEnums.Format)
        self.MediaType.encode(engine, NFlowAudioCodedEnums.MediaType)
        self.SampleRate.encode(engine, NFlowAudioCodedEnums.SampleRate)
        self.Profile.encode(engine, NFlowAudioCodedEnums.Profile)
        self.Level.encode(engine, NFlowAudioCodedEnums.Level)
        self.Bitrate.encode(engine, NFlowAudioCodedEnums.Bitrate)
        self.ConstantBitrate.encode(engine, NFlowAudioCodedEnums.ConstantBitrate)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NFlowAudioCoded")

        self.FlowCore.decode(engine, data)
        if NFlowAudioCodedEnums.Format.s in data:
            self.Format.decode_value(data[NFlowAudioCodedEnums.Format.s])
        if NFlowAudioCodedEnums.MediaType.s in data:
            self.MediaType.decode_value(data[NFlowAudioCodedEnums.MediaType.s])
        if NFlowAudioCodedEnums.SampleRate.s in data:
            self.SampleRate.decode_value(data[NFlowAudioCodedEnums.SampleRate.s])
        if NFlowAudioCodedEnums.Profile.s in data:
            self.Profile.decode_value(data[NFlowAudioCodedEnums.Profile.s])
        if NFlowAudioCodedEnums.Level.s in data:
            self.Level.decode_value(data[NFlowAudioCodedEnums.Level.s])
        if NFlowAudioCodedEnums.Bitrate.s in data:
            self.Bitrate.decode_value(data[NFlowAudioCodedEnums.Bitrate.s])
        if NFlowAudioCodedEnums.ConstantBitrate.s in data:
            self.ConstantBitrate.decode_value(data[NFlowAudioCodedEnums.ConstantBitrate.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NFlowAudioCodedValue:
        o = NFlowAudioCodedValue()
        o.FlowCore = self.FlowCore.clone()
        o.Format = self.Format.clone()
        o.MediaType = self.MediaType.clone()
        o.SampleRate = self.SampleRate.clone()
        o.Profile = self.Profile.clone()
        o.Level = self.Level.clone()
        o.Bitrate = self.Bitrate.clone()
        o.ConstantBitrate = self.ConstantBitrate.clone()
        return o


class NFlowAudioCoded:
    """Optional object type: NFlowAudioCoded."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NFlowAudioCodedValue = NFlowAudioCodedValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NFlowAudioCodedValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NFlowAudioCodedValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NFlowAudioCodedValue | None = None) -> NFlowAudioCodedValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_FlowCore(self) -> NFlowCoreValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FlowCore

    def set_FlowCore(self, v: NFlowCoreValue) -> None:
        assert self._defined, "NFlowAudioCoded must be defined before setting FlowCore"
        self._value.FlowCore = v.clone()  # copy to match Go's value semantics

    def get_Format(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Format

    def set_Format(self, v: Any) -> None:
        assert self._defined, "NFlowAudioCoded must be defined before setting Format"
        _assign_value(self._value.Format, v)

    def get_MediaType(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MediaType

    def set_MediaType(self, v: Any) -> None:
        assert self._defined, "NFlowAudioCoded must be defined before setting MediaType"
        _assign_value(self._value.MediaType, v)

    def get_SampleRate(self) -> NRational:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SampleRate

    def set_SampleRate(self, v: Any) -> None:
        assert self._defined, "NFlowAudioCoded must be defined before setting SampleRate"
        _assign_value(self._value.SampleRate, v)

    def get_Profile(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Profile

    def set_Profile(self, v: Any) -> None:
        assert self._defined, "NFlowAudioCoded must be defined before setting Profile"
        _assign_value(self._value.Profile, v)

    def get_Level(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Level

    def set_Level(self, v: Any) -> None:
        assert self._defined, "NFlowAudioCoded must be defined before setting Level"
        _assign_value(self._value.Level, v)

    def get_Bitrate(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Bitrate

    def set_Bitrate(self, v: Any) -> None:
        assert self._defined, "NFlowAudioCoded must be defined before setting Bitrate"
        _assign_value(self._value.Bitrate, v)

    def get_ConstantBitrate(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ConstantBitrate

    def set_ConstantBitrate(self, v: Any) -> None:
        assert self._defined, "NFlowAudioCoded must be defined before setting ConstantBitrate"
        _assign_value(self._value.ConstantBitrate, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NFlowAudioCodedValue()

    def clone(self) -> NFlowAudioCoded:
        o = NFlowAudioCoded()
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
            return f"NFlowAudioCoded(defined)"
        return "NFlowAudioCoded(<undefined>)"


def make_nflowaudiocoded_value(v: NFlowAudioCodedValue) -> NFlowAudioCodedValue:
    """Factory: create a NFlowAudioCodedValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nflowaudiocoded(v: NFlowAudioCodedValue) -> NFlowAudioCoded:
    """Factory: create a defined NFlowAudioCoded from a NFlowAudioCodedValue."""
    o = NFlowAudioCoded()
    o.set_value(v)
    return o

