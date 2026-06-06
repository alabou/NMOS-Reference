"""Generated NMOS type: NcWorker. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NBool
from nmos.types.generated.nc_object import NcObject, NcObjectValue

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcWorkerEnums:
    """JSON property name enums for NcWorker."""
    Enabled = EnumRegistry.get("enabled")
    pass


class NcWorkerValue:
    """Inner value struct for NcWorker."""

    __slots__ = (
        "Base",
        "Enabled",
    )

    def __init__(self) -> None:
        self.Base: NcObjectValue = NcObjectValue()
        self.Enabled: NBool = NBool()

    def set_to_default(self) -> None:
        self.Base = NcObjectValue()
        self.Base.set_to_default()
        self.Enabled.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Enabled.defined:
            raise InvalidObject("missing required member Enabled")
        self.Base.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        self.Base.encode(engine, None)
        self.Enabled.encode(engine, NcWorkerEnums.Enabled)

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcWorker")

        self.Base.decode(engine, data)
        if NcWorkerEnums.Enabled.s in data:
            self.Enabled.decode_value(data[NcWorkerEnums.Enabled.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcWorkerValue:
        o = NcWorkerValue()
        o.Base = self.Base.clone()
        o.Enabled = self.Enabled.clone()
        return o


class NcWorker:
    """Optional object type: NcWorker."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcWorkerValue = NcWorkerValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcWorkerValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcWorkerValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcWorkerValue | None = None) -> NcWorkerValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Base(self) -> NcObjectValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Base

    def set_Base(self, v: NcObjectValue) -> None:
        assert self._defined, "NcWorker must be defined before setting Base"
        self._value.Base = v.clone()  # copy to match Go's value semantics

    def get_Enabled(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Enabled

    def set_Enabled(self, v: Any) -> None:
        assert self._defined, "NcWorker must be defined before setting Enabled"
        _assign_value(self._value.Enabled, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcWorkerValue()

    def clone(self) -> NcWorker:
        o = NcWorker()
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
            return f"NcWorker(defined)"
        return "NcWorker(<undefined>)"


def make_ncworker_value(v: NcWorkerValue) -> NcWorkerValue:
    """Factory: create a NcWorkerValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ncworker(v: NcWorkerValue) -> NcWorker:
    """Factory: create a defined NcWorker from a NcWorkerValue."""
    o = NcWorker()
    o.set_value(v)
    return o

