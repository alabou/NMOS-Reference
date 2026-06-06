"""Generated NMOS type: NSourceVideo. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NEnum
from nmos.types.generated.nsource_core import NSourceCore, NSourceCoreValue
from nmos.validators import CheckFormat

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NSourceVideoEnums:
    """JSON property name enums for NSourceVideo."""
    Format = EnumRegistry.get("format")
    pass


class NSourceVideoValue:
    """Inner value struct for NSourceVideo."""

    __slots__ = (
        "SourceCore",
        "Format",
    )

    def __init__(self) -> None:
        self.SourceCore: NSourceCoreValue = NSourceCoreValue()
        self.Format: NEnum = NEnum()

    def set_to_default(self) -> None:
        self.SourceCore = NSourceCoreValue()
        self.SourceCore.set_to_default()
        self.Format.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Format.defined:
            raise InvalidObject("missing required member Format")
        if self.Format.defined:
            CheckFormat(self.Format)
        self.SourceCore.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.SourceCore.encode(engine, None)
        self.Format.encode(engine, NSourceVideoEnums.Format)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NSourceVideo")

        self.SourceCore.decode(engine, data)
        if NSourceVideoEnums.Format.s in data:
            self.Format.decode_value(data[NSourceVideoEnums.Format.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NSourceVideoValue:
        o = NSourceVideoValue()
        o.SourceCore = self.SourceCore.clone()
        o.Format = self.Format.clone()
        return o


class NSourceVideo:
    """Optional object type: NSourceVideo."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NSourceVideoValue = NSourceVideoValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NSourceVideoValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NSourceVideoValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NSourceVideoValue | None = None) -> NSourceVideoValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_SourceCore(self) -> NSourceCoreValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SourceCore

    def set_SourceCore(self, v: NSourceCoreValue) -> None:
        assert self._defined, "NSourceVideo must be defined before setting SourceCore"
        self._value.SourceCore = v.clone()  # copy to match Go's value semantics

    def get_Format(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Format

    def set_Format(self, v: Any) -> None:
        assert self._defined, "NSourceVideo must be defined before setting Format"
        _assign_value(self._value.Format, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NSourceVideoValue()

    def clone(self) -> NSourceVideo:
        o = NSourceVideo()
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
            return f"NSourceVideo(defined)"
        return "NSourceVideo(<undefined>)"


def make_nsourcevideo_value(v: NSourceVideoValue) -> NSourceVideoValue:
    """Factory: create a NSourceVideoValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nsourcevideo(v: NSourceVideoValue) -> NSourceVideo:
    """Factory: create a defined NSourceVideo from a NSourceVideoValue."""
    o = NSourceVideo()
    o.set_value(v)
    return o

