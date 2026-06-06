"""Generated NMOS type: NClockPtp. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString, NEnum, NBool
from nmos.validators import CheckClockNameString, CheckClockGmidString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NClockPtpEnums:
    """JSON property name enums for NClockPtp."""
    Name = EnumRegistry.get("name")
    RefType = EnumRegistry.get("ref_type")
    Traceable = EnumRegistry.get("traceable")
    Version = EnumRegistry.get("version")
    Gmid = EnumRegistry.get("gmid")
    Locked = EnumRegistry.get("locked")
    pass


class NClockPtpValue:
    """Inner value struct for NClockPtp."""

    __slots__ = (
        "Name",
        "RefType",
        "Traceable",
        "Version",
        "Gmid",
        "Locked",
    )

    def __init__(self) -> None:
        self.Name: NString = NString()
        self.RefType: NEnum = NEnum()
        self.Traceable: NBool = NBool()
        self.Version: NEnum = NEnum()
        self.Gmid: NString = NString()
        self.Locked: NBool = NBool()

    def set_to_default(self) -> None:
        self.Name.set_to_default()
        self.RefType.set_to_default()
        self.Traceable.set_to_default()
        self.Version.set_to_default()
        self.Gmid.set_to_default()
        self.Locked.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Name.defined:
            raise InvalidObject("missing required member Name")
        if not self.RefType.defined:
            raise InvalidObject("missing required member RefType")
        if not self.Traceable.defined:
            raise InvalidObject("missing required member Traceable")
        if not self.Version.defined:
            raise InvalidObject("missing required member Version")
        if not self.Gmid.defined:
            raise InvalidObject("missing required member Gmid")
        if not self.Locked.defined:
            raise InvalidObject("missing required member Locked")
        if self.Name.defined:
            CheckClockNameString(self.Name)
        if self.Gmid.defined:
            CheckClockGmidString(self.Gmid)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Name.encode(engine, NClockPtpEnums.Name)
        self.RefType.encode(engine, NClockPtpEnums.RefType)
        self.Traceable.encode(engine, NClockPtpEnums.Traceable)
        self.Version.encode(engine, NClockPtpEnums.Version)
        self.Gmid.encode(engine, NClockPtpEnums.Gmid)
        self.Locked.encode(engine, NClockPtpEnums.Locked)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NClockPtp")

        if NClockPtpEnums.Name.s in data:
            self.Name.decode_value(data[NClockPtpEnums.Name.s])
        if NClockPtpEnums.RefType.s in data:
            self.RefType.decode_value(data[NClockPtpEnums.RefType.s])
        if NClockPtpEnums.Traceable.s in data:
            self.Traceable.decode_value(data[NClockPtpEnums.Traceable.s])
        if NClockPtpEnums.Version.s in data:
            self.Version.decode_value(data[NClockPtpEnums.Version.s])
        if NClockPtpEnums.Gmid.s in data:
            self.Gmid.decode_value(data[NClockPtpEnums.Gmid.s])
        if NClockPtpEnums.Locked.s in data:
            self.Locked.decode_value(data[NClockPtpEnums.Locked.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NClockPtpValue:
        o = NClockPtpValue()
        o.Name = self.Name.clone()
        o.RefType = self.RefType.clone()
        o.Traceable = self.Traceable.clone()
        o.Version = self.Version.clone()
        o.Gmid = self.Gmid.clone()
        o.Locked = self.Locked.clone()
        return o


class NClockPtp:
    """Optional object type: NClockPtp."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NClockPtpValue = NClockPtpValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NClockPtpValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NClockPtpValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NClockPtpValue | None = None) -> NClockPtpValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Name(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Name

    def set_Name(self, v: Any) -> None:
        assert self._defined, "NClockPtp must be defined before setting Name"
        _assign_value(self._value.Name, v)

    def get_RefType(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.RefType

    def set_RefType(self, v: Any) -> None:
        assert self._defined, "NClockPtp must be defined before setting RefType"
        _assign_value(self._value.RefType, v)

    def get_Traceable(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Traceable

    def set_Traceable(self, v: Any) -> None:
        assert self._defined, "NClockPtp must be defined before setting Traceable"
        _assign_value(self._value.Traceable, v)

    def get_Version(self) -> NEnum:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Version

    def set_Version(self, v: Any) -> None:
        assert self._defined, "NClockPtp must be defined before setting Version"
        _assign_value(self._value.Version, v)

    def get_Gmid(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Gmid

    def set_Gmid(self, v: Any) -> None:
        assert self._defined, "NClockPtp must be defined before setting Gmid"
        _assign_value(self._value.Gmid, v)

    def get_Locked(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Locked

    def set_Locked(self, v: Any) -> None:
        assert self._defined, "NClockPtp must be defined before setting Locked"
        _assign_value(self._value.Locked, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NClockPtpValue()

    def clone(self) -> NClockPtp:
        o = NClockPtp()
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
            return f"NClockPtp(defined)"
        return "NClockPtp(<undefined>)"


def make_nclockptp_value(v: NClockPtpValue) -> NClockPtpValue:
    """Factory: create a NClockPtpValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nclockptp(v: NClockPtpValue) -> NClockPtp:
    """Factory: create a defined NClockPtp from a NClockPtpValue."""
    o = NClockPtp()
    o.set_value(v)
    return o

