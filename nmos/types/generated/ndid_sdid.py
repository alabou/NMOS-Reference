"""Generated NMOS type: NDidSdid. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString
from nmos.validators import CheckDid, CheckSdid

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NDidSdidEnums:
    """JSON property name enums for NDidSdid."""
    Did = EnumRegistry.get("DID")
    Sdid = EnumRegistry.get("SDID")
    pass


class NDidSdidValue:
    """Inner value struct for NDidSdid."""

    __slots__ = (
        "Did",
        "Sdid",
    )

    def __init__(self) -> None:
        self.Did: NString = NString()
        self.Sdid: NString = NString()

    def set_to_default(self) -> None:
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if self.Did.defined:
            CheckDid(self.Did)
        if self.Sdid.defined:
            CheckSdid(self.Sdid)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Did.encode(engine, NDidSdidEnums.Did)
        self.Sdid.encode(engine, NDidSdidEnums.Sdid)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NDidSdid")

        if NDidSdidEnums.Did.s in data:
            self.Did.decode_value(data[NDidSdidEnums.Did.s])
        if NDidSdidEnums.Sdid.s in data:
            self.Sdid.decode_value(data[NDidSdidEnums.Sdid.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NDidSdidValue:
        o = NDidSdidValue()
        o.Did = self.Did.clone()
        o.Sdid = self.Sdid.clone()
        return o


class NDidSdid:
    """Optional object type: NDidSdid."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NDidSdidValue = NDidSdidValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NDidSdidValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NDidSdidValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NDidSdidValue | None = None) -> NDidSdidValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Did(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Did

    def set_Did(self, v: Any) -> None:
        assert self._defined, "NDidSdid must be defined before setting Did"
        _assign_value(self._value.Did, v)

    def get_Sdid(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Sdid

    def set_Sdid(self, v: Any) -> None:
        assert self._defined, "NDidSdid must be defined before setting Sdid"
        _assign_value(self._value.Sdid, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NDidSdidValue()

    def clone(self) -> NDidSdid:
        o = NDidSdid()
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
            return f"NDidSdid(defined)"
        return "NDidSdid(<undefined>)"


def make_ndidsdid_value(v: NDidSdidValue) -> NDidSdidValue:
    """Factory: create a NDidSdidValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ndidsdid(v: NDidSdidValue) -> NDidSdid:
    """Factory: create a defined NDidSdid from a NDidSdidValue."""
    o = NDidSdid()
    o.set_value(v)
    return o

