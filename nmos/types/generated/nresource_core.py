"""Generated NMOS type: NResourceCore. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NString, NTime, NTags
from nmos.validators import CheckResourceIdString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NResourceCoreEnums:
    """JSON property name enums for NResourceCore."""
    Id = EnumRegistry.get("id")
    Version = EnumRegistry.get("version")
    Label = EnumRegistry.get("label")
    Description = EnumRegistry.get("description")
    Tags = EnumRegistry.get("tags")
    pass


class NResourceCoreValue:
    """Inner value struct for NResourceCore."""

    __slots__ = (
        "Id",
        "StaticId",
        "Version",
        "Label",
        "Description",
        "Tags",
    )

    def __init__(self) -> None:
        self.Id: NString = NString()
        self.StaticId: NString = NString()
        self.Version: NTime = NTime()
        self.Label: NString = NString()
        self.Description: NString = NString()
        self.Tags: NTags = NTags()

    def set_to_default(self) -> None:
        self.Id.set_to_default()
        self.StaticId.set_to_default()
        self.Version.set_to_default()
        self.Label.set_to_default()
        self.Description.set_to_default()
        self.Tags.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Id.defined:
            raise InvalidObject("missing required member Id")
        if not self.Version.defined:
            raise InvalidObject("missing required member Version")
        if not self.Label.defined:
            raise InvalidObject("missing required member Label")
        if not self.Description.defined:
            raise InvalidObject("missing required member Description")
        if not self.Tags.defined:
            raise InvalidObject("missing required member Tags")
        if self.Id.defined:
            CheckResourceIdString(self.Id)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        self.Id.encode(engine, NResourceCoreEnums.Id)
        self.Version.encode(engine, NResourceCoreEnums.Version)
        self.Label.encode(engine, NResourceCoreEnums.Label)
        self.Description.encode(engine, NResourceCoreEnums.Description)
        self.Tags.encode(engine, NResourceCoreEnums.Tags)

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NResourceCore")

        if NResourceCoreEnums.Id.s in data:
            self.Id.decode_value(data[NResourceCoreEnums.Id.s])
        if NResourceCoreEnums.Version.s in data:
            self.Version.decode_value(data[NResourceCoreEnums.Version.s])
        if NResourceCoreEnums.Label.s in data:
            self.Label.decode_value(data[NResourceCoreEnums.Label.s])
        if NResourceCoreEnums.Description.s in data:
            self.Description.decode_value(data[NResourceCoreEnums.Description.s])
        if NResourceCoreEnums.Tags.s in data:
            self.Tags.decode_value(data[NResourceCoreEnums.Tags.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NResourceCoreValue:
        o = NResourceCoreValue()
        o.Id = self.Id.clone()
        o.StaticId = self.StaticId.clone()
        o.Version = self.Version.clone()
        o.Label = self.Label.clone()
        o.Description = self.Description.clone()
        o.Tags = self.Tags.clone()
        return o


class NResourceCore:
    """Optional object type: NResourceCore."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NResourceCoreValue = NResourceCoreValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NResourceCoreValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NResourceCoreValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NResourceCoreValue | None = None) -> NResourceCoreValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Id(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Id

    def set_Id(self, v: Any) -> None:
        assert self._defined, "NResourceCore must be defined before setting Id"
        _assign_value(self._value.Id, v)

    def get_Version(self) -> NTime:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Version

    def set_Version(self, v: Any) -> None:
        assert self._defined, "NResourceCore must be defined before setting Version"
        _assign_value(self._value.Version, v)

    def get_Label(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Label

    def set_Label(self, v: Any) -> None:
        assert self._defined, "NResourceCore must be defined before setting Label"
        _assign_value(self._value.Label, v)

    def get_Description(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Description

    def set_Description(self, v: Any) -> None:
        assert self._defined, "NResourceCore must be defined before setting Description"
        _assign_value(self._value.Description, v)

    def get_Tags(self) -> NTags:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Tags

    def set_Tags(self, v: Any) -> None:
        assert self._defined, "NResourceCore must be defined before setting Tags"
        _assign_value(self._value.Tags, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NResourceCoreValue()

    def clone(self) -> NResourceCore:
        o = NResourceCore()
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
            return f"NResourceCore(defined)"
        return "NResourceCore(<undefined>)"


def make_nresourcecore_value(v: NResourceCoreValue) -> NResourceCoreValue:
    """Factory: create a NResourceCoreValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nresourcecore(v: NResourceCoreValue) -> NResourceCore:
    """Factory: create a defined NResourceCore from a NResourceCoreValue."""
    o = NResourceCore()
    o.set_value(v)
    return o

