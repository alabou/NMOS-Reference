"""Generated NMOS type: NSourceAudio. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NEnum
from nmos.types.generated.nsource_core import NSourceCore, NSourceCoreValue
from nmos.types.generated.narray_of_audio_channel import NArrayOfAudioChannel, NArrayOfAudioChannelValue
from nmos.validators import CheckFormat, CheckAudioChannels

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NSourceAudioEnums:
    """JSON property name enums for NSourceAudio."""
    Format = EnumRegistry.get("format")
    Channels = EnumRegistry.get("channels")
    pass


class NSourceAudioValue:
    """Inner value struct for NSourceAudio."""

    __slots__ = (
        "SourceCore",
        "Format",
        "Channels",
    )

    def __init__(self) -> None:
        self.SourceCore: NSourceCoreValue = NSourceCoreValue()
        self.Format: NEnum = NEnum()
        self.Channels: NArrayOfAudioChannel = NArrayOfAudioChannel()

    def set_to_default(self) -> None:
        self.SourceCore = NSourceCoreValue()
        self.SourceCore.set_to_default()
        self.Format.set_to_default()
        self.Channels.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Format.defined:
            raise InvalidObject("missing required member Format")
        if not self.Channels.defined:
            raise InvalidObject("missing required member Channels")
        if self.Format.defined:
            CheckFormat(self.Format)
        if self.Channels.defined:
            CheckAudioChannels(self.Channels)
        self.SourceCore.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.SourceCore.encode(engine, None)
        self.Format.encode(engine, NSourceAudioEnums.Format)
        self.Channels.encode(engine, NSourceAudioEnums.Channels)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NSourceAudio")

        self.SourceCore.decode(engine, data)
        if NSourceAudioEnums.Format.s in data:
            self.Format.decode_value(data[NSourceAudioEnums.Format.s])
        if NSourceAudioEnums.Channels.s in data:
            self.Channels.decode_value(data[NSourceAudioEnums.Channels.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NSourceAudioValue:
        o = NSourceAudioValue()
        o.SourceCore = self.SourceCore.clone()
        o.Format = self.Format.clone()
        o.Channels = self.Channels.clone()
        return o


class NSourceAudio:
    """Optional object type: NSourceAudio."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NSourceAudioValue = NSourceAudioValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NSourceAudioValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NSourceAudioValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NSourceAudioValue | None = None) -> NSourceAudioValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_SourceCore(self) -> NSourceCoreValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SourceCore

    def set_SourceCore(self, v: NSourceCoreValue) -> None:
        assert self._defined, "NSourceAudio must be defined before setting SourceCore"
        self._value.SourceCore = v.clone()  # copy to match Go's value semantics

    def get_Format(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Format

    def set_Format(self, v: Any) -> None:
        assert self._defined, "NSourceAudio must be defined before setting Format"
        _assign_value(self._value.Format, v)

    def get_Channels(self) -> NArrayOfAudioChannel:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Channels

    def set_Channels(self, v: Any) -> None:
        assert self._defined, "NSourceAudio must be defined before setting Channels"
        _assign_value(self._value.Channels, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NSourceAudioValue()

    def clone(self) -> NSourceAudio:
        o = NSourceAudio()
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
            return f"NSourceAudio(defined)"
        return "NSourceAudio(<undefined>)"


def make_nsourceaudio_value(v: NSourceAudioValue) -> NSourceAudioValue:
    """Factory: create a NSourceAudioValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nsourceaudio(v: NSourceAudioValue) -> NSourceAudio:
    """Factory: create a defined NSourceAudio from a NSourceAudioValue."""
    o = NSourceAudio()
    o.set_value(v)
    return o

