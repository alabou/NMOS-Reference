"""Generated NMOS type: NcBlock. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NBool
from nmos.types.generated.nc_object import NcObject, NcObjectValue
from nmos.types.generated.nc_array_of_block_member_descriptor import NcArrayOfBlockMemberDescriptor, NcArrayOfBlockMemberDescriptorValue

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcBlockEnums:
    """JSON property name enums for NcBlock."""
    Enabled = EnumRegistry.get("enabled")
    Members = EnumRegistry.get("members")
    pass


class NcBlockValue:
    """Inner value struct for NcBlock."""

    __slots__ = (
        "Base",
        "Enabled",
        "Members",
    )

    def __init__(self) -> None:
        self.Base: NcObjectValue = NcObjectValue()
        self.Enabled: NBool = NBool()
        self.Members: NcArrayOfBlockMemberDescriptor = NcArrayOfBlockMemberDescriptor()

    def set_to_default(self) -> None:
        self.Base = NcObjectValue()
        self.Base.set_to_default()
        self.Enabled.set_to_default()
        self.Members.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Enabled.defined:
            raise InvalidObject("missing required member Enabled")
        if not self.Members.defined:
            raise InvalidObject("missing required member Members")
        self.Base.assert_valid()
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Base.encode(engine, None)
        self.Enabled.encode(engine, NcBlockEnums.Enabled)
        self.Members.encode(engine, NcBlockEnums.Members)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcBlock")

        self.Base.decode(engine, data)
        if NcBlockEnums.Enabled.s in data:
            self.Enabled.decode_value(data[NcBlockEnums.Enabled.s])
        if NcBlockEnums.Members.s in data:
            self.Members.decode_value(data[NcBlockEnums.Members.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcBlockValue:
        o = NcBlockValue()
        o.Base = self.Base.clone()
        o.Enabled = self.Enabled.clone()
        o.Members = self.Members.clone()
        return o


class NcBlock:
    """Optional object type: NcBlock."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcBlockValue = NcBlockValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcBlockValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcBlockValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcBlockValue | None = None) -> NcBlockValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Base(self) -> NcObjectValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Base

    def set_Base(self, v: NcObjectValue) -> None:
        assert self._defined, "NcBlock must be defined before setting Base"
        self._value.Base = v.clone()  # copy to match Go's value semantics

    def get_Enabled(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Enabled

    def set_Enabled(self, v: Any) -> None:
        assert self._defined, "NcBlock must be defined before setting Enabled"
        _assign_value(self._value.Enabled, v)

    def get_Members(self) -> NcArrayOfBlockMemberDescriptor:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Members

    def set_Members(self, v: Any) -> None:
        assert self._defined, "NcBlock must be defined before setting Members"
        _assign_value(self._value.Members, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcBlockValue()

    def clone(self) -> NcBlock:
        o = NcBlock()
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
            return f"NcBlock(defined)"
        return "NcBlock(<undefined>)"


def make_ncblock_value(v: NcBlockValue) -> NcBlockValue:
    """Factory: create a NcBlockValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ncblock(v: NcBlockValue) -> NcBlock:
    """Factory: create a defined NcBlock from a NcBlockValue."""
    o = NcBlock()
    o.set_value(v)
    return o

