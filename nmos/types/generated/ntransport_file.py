"""Generated NMOS type: NTransportFile. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NNullString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NTransportFileEnums:
    """JSON property name enums for NTransportFile."""
    Data = EnumRegistry.get("data")
    Type = EnumRegistry.get("type")
    pass


class NTransportFileValue:
    """Inner value struct for NTransportFile."""

    __slots__ = (
        "Data",
        "Type",
    )

    def __init__(self) -> None:
        self.Data: NNullString = NNullString()
        self.Type: NNullString = NNullString()

    def set_to_default(self) -> None:
        self.Data.set_to_default()
        self.Type.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Data.defined:
            raise InvalidObject("missing required member Data")
        if not self.Type.defined:
            raise InvalidObject("missing required member Type")
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Data.encode(engine, NTransportFileEnums.Data)
        self.Type.encode(engine, NTransportFileEnums.Type)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NTransportFile")

        if NTransportFileEnums.Data.s in data:
            self.Data.decode_value(data[NTransportFileEnums.Data.s])
        if NTransportFileEnums.Type.s in data:
            self.Type.decode_value(data[NTransportFileEnums.Type.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NTransportFileValue:
        o = NTransportFileValue()
        o.Data = self.Data.clone()
        o.Type = self.Type.clone()
        return o


class NTransportFile:
    """Optional object type: NTransportFile."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NTransportFileValue = NTransportFileValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NTransportFileValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NTransportFileValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NTransportFileValue | None = None) -> NTransportFileValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Data(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Data

    def set_Data(self, v: Any) -> None:
        assert self._defined, "NTransportFile must be defined before setting Data"
        _assign_value(self._value.Data, v)

    def get_Type(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Type

    def set_Type(self, v: Any) -> None:
        assert self._defined, "NTransportFile must be defined before setting Type"
        _assign_value(self._value.Type, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NTransportFileValue()

    def clone(self) -> NTransportFile:
        o = NTransportFile()
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
            return f"NTransportFile(defined)"
        return "NTransportFile(<undefined>)"


def make_ntransportfile_value(v: NTransportFileValue) -> NTransportFileValue:
    """Factory: create a NTransportFileValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ntransportfile(v: NTransportFileValue) -> NTransportFile:
    """Factory: create a defined NTransportFile from a NTransportFileValue."""
    o = NTransportFile()
    o.set_value(v)
    return o

