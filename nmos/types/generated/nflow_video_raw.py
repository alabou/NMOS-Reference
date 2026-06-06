"""Generated NMOS type: NFlowVideoRaw. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NEnum, NInt
from nmos.types.generated.nflow_core import NFlowCore, NFlowCoreValue
from nmos.types.generated.narray_of_video_component import NArrayOfVideoComponent, NArrayOfVideoComponentValue
from nmos.validators import CheckFormat, CheckColorspace, CheckInterlaceMode, CheckTransferCharacteristic, CheckVideoComponents

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NFlowVideoRawEnums:
    """JSON property name enums for NFlowVideoRaw."""
    Format = EnumRegistry.get("format")
    MediaType = EnumRegistry.get("media_type")
    FrameWidth = EnumRegistry.get("frame_width")
    FrameHeight = EnumRegistry.get("frame_height")
    Colorspace = EnumRegistry.get("colorspace")
    InterlaceMode = EnumRegistry.get("interlace_mode")
    TransferCharacteristic = EnumRegistry.get("transfer_characteristic")
    Components = EnumRegistry.get("components")
    pass


class NFlowVideoRawValue:
    """Inner value struct for NFlowVideoRaw."""

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
        self.Format.encode(engine, NFlowVideoRawEnums.Format)
        self.MediaType.encode(engine, NFlowVideoRawEnums.MediaType)
        self.FrameWidth.encode(engine, NFlowVideoRawEnums.FrameWidth)
        self.FrameHeight.encode(engine, NFlowVideoRawEnums.FrameHeight)
        self.Colorspace.encode(engine, NFlowVideoRawEnums.Colorspace)
        self.InterlaceMode.encode(engine, NFlowVideoRawEnums.InterlaceMode)
        self.TransferCharacteristic.encode(engine, NFlowVideoRawEnums.TransferCharacteristic)
        self.Components.encode(engine, NFlowVideoRawEnums.Components)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NFlowVideoRaw")

        self.FlowCore.decode(engine, data)
        if NFlowVideoRawEnums.Format.s in data:
            self.Format.decode_value(data[NFlowVideoRawEnums.Format.s])
        if NFlowVideoRawEnums.MediaType.s in data:
            self.MediaType.decode_value(data[NFlowVideoRawEnums.MediaType.s])
        if NFlowVideoRawEnums.FrameWidth.s in data:
            self.FrameWidth.decode_value(data[NFlowVideoRawEnums.FrameWidth.s])
        if NFlowVideoRawEnums.FrameHeight.s in data:
            self.FrameHeight.decode_value(data[NFlowVideoRawEnums.FrameHeight.s])
        if NFlowVideoRawEnums.Colorspace.s in data:
            self.Colorspace.decode_value(data[NFlowVideoRawEnums.Colorspace.s])
        if NFlowVideoRawEnums.InterlaceMode.s in data:
            self.InterlaceMode.decode_value(data[NFlowVideoRawEnums.InterlaceMode.s])
        if NFlowVideoRawEnums.TransferCharacteristic.s in data:
            self.TransferCharacteristic.decode_value(data[NFlowVideoRawEnums.TransferCharacteristic.s])
        if NFlowVideoRawEnums.Components.s in data:
            self.Components.decode_value(data[NFlowVideoRawEnums.Components.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NFlowVideoRawValue:
        o = NFlowVideoRawValue()
        o.FlowCore = self.FlowCore.clone()
        o.Format = self.Format.clone()
        o.MediaType = self.MediaType.clone()
        o.FrameWidth = self.FrameWidth.clone()
        o.FrameHeight = self.FrameHeight.clone()
        o.Colorspace = self.Colorspace.clone()
        o.InterlaceMode = self.InterlaceMode.clone()
        o.TransferCharacteristic = self.TransferCharacteristic.clone()
        o.Components = self.Components.clone()
        return o


class NFlowVideoRaw:
    """Optional object type: NFlowVideoRaw."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NFlowVideoRawValue = NFlowVideoRawValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NFlowVideoRawValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NFlowVideoRawValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NFlowVideoRawValue | None = None) -> NFlowVideoRawValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_FlowCore(self) -> NFlowCoreValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FlowCore

    def set_FlowCore(self, v: NFlowCoreValue) -> None:
        assert self._defined, "NFlowVideoRaw must be defined before setting FlowCore"
        self._value.FlowCore = v.clone()  # copy to match Go's value semantics

    def get_Format(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Format

    def set_Format(self, v: Any) -> None:
        assert self._defined, "NFlowVideoRaw must be defined before setting Format"
        _assign_value(self._value.Format, v)

    def get_MediaType(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MediaType

    def set_MediaType(self, v: Any) -> None:
        assert self._defined, "NFlowVideoRaw must be defined before setting MediaType"
        _assign_value(self._value.MediaType, v)

    def get_FrameWidth(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FrameWidth

    def set_FrameWidth(self, v: Any) -> None:
        assert self._defined, "NFlowVideoRaw must be defined before setting FrameWidth"
        _assign_value(self._value.FrameWidth, v)

    def get_FrameHeight(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.FrameHeight

    def set_FrameHeight(self, v: Any) -> None:
        assert self._defined, "NFlowVideoRaw must be defined before setting FrameHeight"
        _assign_value(self._value.FrameHeight, v)

    def get_Colorspace(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Colorspace

    def set_Colorspace(self, v: Any) -> None:
        assert self._defined, "NFlowVideoRaw must be defined before setting Colorspace"
        _assign_value(self._value.Colorspace, v)

    def get_InterlaceMode(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.InterlaceMode

    def set_InterlaceMode(self, v: Any) -> None:
        assert self._defined, "NFlowVideoRaw must be defined before setting InterlaceMode"
        _assign_value(self._value.InterlaceMode, v)

    def get_TransferCharacteristic(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.TransferCharacteristic

    def set_TransferCharacteristic(self, v: Any) -> None:
        assert self._defined, "NFlowVideoRaw must be defined before setting TransferCharacteristic"
        _assign_value(self._value.TransferCharacteristic, v)

    def get_Components(self) -> NArrayOfVideoComponent:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Components

    def set_Components(self, v: Any) -> None:
        assert self._defined, "NFlowVideoRaw must be defined before setting Components"
        _assign_value(self._value.Components, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NFlowVideoRawValue()

    def clone(self) -> NFlowVideoRaw:
        o = NFlowVideoRaw()
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
            return f"NFlowVideoRaw(defined)"
        return "NFlowVideoRaw(<undefined>)"


def make_nflowvideoraw_value(v: NFlowVideoRawValue) -> NFlowVideoRawValue:
    """Factory: create a NFlowVideoRawValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nflowvideoraw(v: NFlowVideoRawValue) -> NFlowVideoRaw:
    """Factory: create a defined NFlowVideoRaw from a NFlowVideoRawValue."""
    o = NFlowVideoRaw()
    o.set_value(v)
    return o

