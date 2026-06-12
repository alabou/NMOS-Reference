"""Generated NMOS type: NFlowVideoCoded. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NEnum, NInt, NBool
from nmos.types.generated.nflow_core import NFlowCore, NFlowCoreValue
from nmos.types.generated.narray_of_video_component import NArrayOfVideoComponent, NArrayOfVideoComponentValue
from nmos.validators import CheckFormat, CheckColorspace, CheckInterlaceMode, CheckTransferCharacteristic, CheckVideoComponents

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NFlowVideoCodedEnums:
    """JSON property name enums for NFlowVideoCoded."""
    Format = EnumRegistry.get("format")
    MediaType = EnumRegistry.get("media_type")
    FrameWidth = EnumRegistry.get("frame_width")
    FrameHeight = EnumRegistry.get("frame_height")
    Colorspace = EnumRegistry.get("colorspace")
    InterlaceMode = EnumRegistry.get("interlace_mode")
    TransferCharacteristic = EnumRegistry.get("transfer_characteristic")
    Components = EnumRegistry.get("components")
    Profile = EnumRegistry.get("profile")
    Level = EnumRegistry.get("level")
    Sublevel = EnumRegistry.get("sublevel")
    Fbblevel = EnumRegistry.get("fbblevel")
    Bitrate = EnumRegistry.get("bit_rate")
    ConstantBitrate = EnumRegistry.get("constant_bit_rate")
    pass


class NFlowVideoCodedValue:
    """Inner value struct for NFlowVideoCoded."""

    __slots__ = (
        "FlowCore",
        "Format",
        "MediaType",
        "FrameWidth",
        "FrameHeight",
        "Colorspace",
        "InterlaceMode",
        "TransferCharacteristic",
        "Components",
        "Profile",
        "Level",
        "Sublevel",
        "Fbblevel",
        "Bitrate",
        "ConstantBitrate",
    )

    def __init__(self) -> None:
        self.FlowCore: NFlowCoreValue = NFlowCoreValue()
        self.Format: NEnum = NEnum()
        self.MediaType: NEnum = NEnum()
        self.FrameWidth: NInt = NInt()
        self.FrameHeight: NInt = NInt()
        self.Colorspace: NEnum = NEnum()
        self.InterlaceMode: NEnum = NEnum()
        self.TransferCharacteristic: NEnum = NEnum()
        self.Components: NArrayOfVideoComponent = NArrayOfVideoComponent()
        self.Profile: NEnum = NEnum()
        self.Level: NEnum = NEnum()
        self.Sublevel: NEnum = NEnum()
        self.Fbblevel: NEnum = NEnum()
        self.Bitrate: NInt = NInt()
        self.ConstantBitrate: NBool = NBool()

    def set_to_default(self) -> None:
        self.FlowCore = NFlowCoreValue()
        self.FlowCore.set_to_default()
        self.Format.set_to_default()
        self.MediaType.set_to_default()
        self.FrameWidth.set_to_default()
        self.FrameHeight.set_to_default()
        self.Colorspace.set_to_default()
        _assign_value(self.InterlaceMode, EnumRegistry.get("progressive"))
        _assign_value(self.TransferCharacteristic, EnumRegistry.get("SDR"))
        self.Components.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        if not self.InterlaceMode.defined:
            _assign_value(self.InterlaceMode, EnumRegistry.get("progressive"))
        if not self.TransferCharacteristic.defined:
            _assign_value(self.TransferCharacteristic, EnumRegistry.get("SDR"))
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Format.defined:
            raise InvalidObject("missing required member Format")
        if not self.MediaType.defined:
            raise InvalidObject("missing required member MediaType")
        if not self.FrameWidth.defined:
            raise InvalidObject("missing required member FrameWidth")
        if not self.FrameHeight.defined:
            raise InvalidObject("missing required member FrameHeight")
        if not self.Colorspace.defined:
            raise InvalidObject("missing required member Colorspace")
        if not self.Components.defined:
            raise InvalidObject("missing required member Components")
        if self.Format.defined:
            CheckFormat(self.Format)
        if self.Colorspace.defined:
            CheckColorspace(self.Colorspace)
        if self.InterlaceMode.defined:
            CheckInterlaceMode(self.InterlaceMode)
        if self.TransferCharacteristic.defined:
            CheckTransferCharacteristic(self.TransferCharacteristic)
        if self.Components.defined:
            CheckVideoComponents(self.Components)
        self.FlowCore.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.FlowCore.encode(engine, None)
        self.Format.encode(engine, NFlowVideoCodedEnums.Format)
        self.MediaType.encode(engine, NFlowVideoCodedEnums.MediaType)
        self.FrameWidth.encode(engine, NFlowVideoCodedEnums.FrameWidth)
        self.FrameHeight.encode(engine, NFlowVideoCodedEnums.FrameHeight)
        self.Colorspace.encode(engine, NFlowVideoCodedEnums.Colorspace)
        self.InterlaceMode.encode(engine, NFlowVideoCodedEnums.InterlaceMode)
        self.TransferCharacteristic.encode(engine, NFlowVideoCodedEnums.TransferCharacteristic)
        self.Components.encode(engine, NFlowVideoCodedEnums.Components)
        self.Profile.encode(engine, NFlowVideoCodedEnums.Profile)
        self.Level.encode(engine, NFlowVideoCodedEnums.Level)
        self.Sublevel.encode(engine, NFlowVideoCodedEnums.Sublevel)
        self.Fbblevel.encode(engine, NFlowVideoCodedEnums.Fbblevel)
        self.Bitrate.encode(engine, NFlowVideoCodedEnums.Bitrate)
        self.ConstantBitrate.encode(engine, NFlowVideoCodedEnums.ConstantBitrate)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NFlowVideoCoded")

        self.FlowCore.decode(engine, data)
        if NFlowVideoCodedEnums.Format.s in data:
            self.Format.decode_value(data[NFlowVideoCodedEnums.Format.s])
        if NFlowVideoCodedEnums.MediaType.s in data:
            self.MediaType.decode_value(data[NFlowVideoCodedEnums.MediaType.s])
        if NFlowVideoCodedEnums.FrameWidth.s in data:
            self.FrameWidth.decode_value(data[NFlowVideoCodedEnums.FrameWidth.s])
        if NFlowVideoCodedEnums.FrameHeight.s in data:
            self.FrameHeight.decode_value(data[NFlowVideoCodedEnums.FrameHeight.s])
        if NFlowVideoCodedEnums.Colorspace.s in data:
            self.Colorspace.decode_value(data[NFlowVideoCodedEnums.Colorspace.s])
        if NFlowVideoCodedEnums.InterlaceMode.s in data:
            self.InterlaceMode.decode_value(data[NFlowVideoCodedEnums.InterlaceMode.s])
        if NFlowVideoCodedEnums.TransferCharacteristic.s in data:
            self.TransferCharacteristic.decode_value(data[NFlowVideoCodedEnums.TransferCharacteristic.s])
        if NFlowVideoCodedEnums.Components.s in data:
            self.Components.decode_value(data[NFlowVideoCodedEnums.Components.s])
        if NFlowVideoCodedEnums.Profile.s in data:
            self.Profile.decode_value(data[NFlowVideoCodedEnums.Profile.s])
        if NFlowVideoCodedEnums.Level.s in data:
            self.Level.decode_value(data[NFlowVideoCodedEnums.Level.s])
        if NFlowVideoCodedEnums.Sublevel.s in data:
            self.Sublevel.decode_value(data[NFlowVideoCodedEnums.Sublevel.s])
        if NFlowVideoCodedEnums.Fbblevel.s in data:
            self.Fbblevel.decode_value(data[NFlowVideoCodedEnums.Fbblevel.s])
        if NFlowVideoCodedEnums.Bitrate.s in data:
            self.Bitrate.decode_value(data[NFlowVideoCodedEnums.Bitrate.s])
        if NFlowVideoCodedEnums.ConstantBitrate.s in data:
            self.ConstantBitrate.decode_value(data[NFlowVideoCodedEnums.ConstantBitrate.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NFlowVideoCodedValue:
        o = NFlowVideoCodedValue()
        o.FlowCore = self.FlowCore.clone()
        o.Format = self.Format.clone()
        o.MediaType = self.MediaType.clone()
        o.FrameWidth = self.FrameWidth.clone()
        o.FrameHeight = self.FrameHeight.clone()
        o.Colorspace = self.Colorspace.clone()
        o.InterlaceMode = self.InterlaceMode.clone()
        o.TransferCharacteristic = self.TransferCharacteristic.clone()
        o.Components = self.Components.clone()
        o.Profile = self.Profile.clone()
        o.Level = self.Level.clone()
        o.Sublevel = self.Sublevel.clone()
        o.Fbblevel = self.Fbblevel.clone()
        o.Bitrate = self.Bitrate.clone()
        o.ConstantBitrate = self.ConstantBitrate.clone()
        return o


class NFlowVideoCoded:
    """Optional object type: NFlowVideoCoded."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NFlowVideoCodedValue = NFlowVideoCodedValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NFlowVideoCodedValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NFlowVideoCodedValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NFlowVideoCodedValue | None = None) -> NFlowVideoCodedValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_FlowCore(self) -> NFlowCoreValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FlowCore

    def set_FlowCore(self, v: NFlowCoreValue) -> None:
        assert self._defined, "NFlowVideoCoded must be defined before setting FlowCore"
        self._value.FlowCore = v.clone()  # copy to match Go's value semantics

    def get_Format(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Format

    def set_Format(self, v: Any) -> None:
        assert self._defined, "NFlowVideoCoded must be defined before setting Format"
        _assign_value(self._value.Format, v)

    def get_MediaType(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MediaType

    def set_MediaType(self, v: Any) -> None:
        assert self._defined, "NFlowVideoCoded must be defined before setting MediaType"
        _assign_value(self._value.MediaType, v)

    def get_FrameWidth(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FrameWidth

    def set_FrameWidth(self, v: Any) -> None:
        assert self._defined, "NFlowVideoCoded must be defined before setting FrameWidth"
        _assign_value(self._value.FrameWidth, v)

    def get_FrameHeight(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FrameHeight

    def set_FrameHeight(self, v: Any) -> None:
        assert self._defined, "NFlowVideoCoded must be defined before setting FrameHeight"
        _assign_value(self._value.FrameHeight, v)

    def get_Colorspace(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Colorspace

    def set_Colorspace(self, v: Any) -> None:
        assert self._defined, "NFlowVideoCoded must be defined before setting Colorspace"
        _assign_value(self._value.Colorspace, v)

    def get_InterlaceMode(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.InterlaceMode

    def set_InterlaceMode(self, v: Any) -> None:
        assert self._defined, "NFlowVideoCoded must be defined before setting InterlaceMode"
        _assign_value(self._value.InterlaceMode, v)

    def get_TransferCharacteristic(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.TransferCharacteristic

    def set_TransferCharacteristic(self, v: Any) -> None:
        assert self._defined, "NFlowVideoCoded must be defined before setting TransferCharacteristic"
        _assign_value(self._value.TransferCharacteristic, v)

    def get_Components(self) -> NArrayOfVideoComponent:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Components

    def set_Components(self, v: Any) -> None:
        assert self._defined, "NFlowVideoCoded must be defined before setting Components"
        _assign_value(self._value.Components, v)

    def get_Profile(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Profile

    def set_Profile(self, v: Any) -> None:
        assert self._defined, "NFlowVideoCoded must be defined before setting Profile"
        _assign_value(self._value.Profile, v)

    def get_Level(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Level

    def set_Level(self, v: Any) -> None:
        assert self._defined, "NFlowVideoCoded must be defined before setting Level"
        _assign_value(self._value.Level, v)

    def get_Sublevel(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Sublevel

    def set_Sublevel(self, v: Any) -> None:
        assert self._defined, "NFlowVideoCoded must be defined before setting Sublevel"
        _assign_value(self._value.Sublevel, v)

    def get_Fbblevel(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Fbblevel

    def set_Fbblevel(self, v: Any) -> None:
        assert self._defined, "NFlowVideoCoded must be defined before setting Fbblevel"
        _assign_value(self._value.Fbblevel, v)

    def get_Bitrate(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Bitrate

    def set_Bitrate(self, v: Any) -> None:
        assert self._defined, "NFlowVideoCoded must be defined before setting Bitrate"
        _assign_value(self._value.Bitrate, v)

    def get_ConstantBitrate(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ConstantBitrate

    def set_ConstantBitrate(self, v: Any) -> None:
        assert self._defined, "NFlowVideoCoded must be defined before setting ConstantBitrate"
        _assign_value(self._value.ConstantBitrate, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NFlowVideoCodedValue()

    def clone(self) -> NFlowVideoCoded:
        o = NFlowVideoCoded()
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
            return f"NFlowVideoCoded(defined)"
        return "NFlowVideoCoded(<undefined>)"


def make_nflowvideocoded_value(v: NFlowVideoCodedValue) -> NFlowVideoCodedValue:
    """Factory: create a NFlowVideoCodedValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nflowvideocoded(v: NFlowVideoCodedValue) -> NFlowVideoCoded:
    """Factory: create a defined NFlowVideoCoded from a NFlowVideoCodedValue."""
    o = NFlowVideoCoded()
    o.set_value(v)
    return o

