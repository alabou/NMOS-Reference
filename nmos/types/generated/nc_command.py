"""Generated NMOS type: NcCommand. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NInt, NString, NGeneric
from nmos.types.generated.nc_method_id import NcMethodId, NcMethodIdValue
from nmos.validators import CheckPositiveUint16, CheckPositiveInteger, CheckGenericObject

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcCommandEnums:
    """JSON property name enums for NcCommand."""
    Handle = EnumRegistry.get("handle")
    OId = EnumRegistry.get("oid")
    Object = EnumRegistry.get("object")
    MethodId = EnumRegistry.get("methodId")
    Method = EnumRegistry.get("method")
    Arguments = EnumRegistry.get("arguments")
    pass


class NcCommandValue:
    """Inner value struct for NcCommand."""

    __slots__ = (
        "Handle",
        "OId",
        "Object",
        "MethodId",
        "Method",
        "Arguments",
    )

    def __init__(self) -> None:
        self.Handle: NInt = NInt()
        self.OId: NInt = NInt()
        self.Object: NString = NString()
        self.MethodId: NcMethodId = NcMethodId()
        self.Method: NString = NString()
        self.Arguments: NGeneric = NGeneric()

    def set_to_default(self) -> None:
        self.Handle.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.Handle.defined:
            raise InvalidObject("missing required member Handle")
        if self.Handle.defined:
            CheckPositiveUint16(self.Handle)
        if self.OId.defined:
            CheckPositiveInteger(self.OId)
        if self.Arguments.defined:
            CheckGenericObject(self.Arguments)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.Handle.encode(engine, NcCommandEnums.Handle)
        self.OId.encode(engine, NcCommandEnums.OId)
        self.Object.encode(engine, NcCommandEnums.Object)
        self.MethodId.encode(engine, NcCommandEnums.MethodId)
        self.Method.encode(engine, NcCommandEnums.Method)
        self.Arguments.encode(engine, NcCommandEnums.Arguments)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcCommand")

        if NcCommandEnums.Handle.s in data:
            self.Handle.decode_value(data[NcCommandEnums.Handle.s])
        if NcCommandEnums.OId.s in data:
            self.OId.decode_value(data[NcCommandEnums.OId.s])
        if NcCommandEnums.Object.s in data:
            self.Object.decode_value(data[NcCommandEnums.Object.s])
        if NcCommandEnums.MethodId.s in data:
            self.MethodId.decode_value(data[NcCommandEnums.MethodId.s])
        if NcCommandEnums.Method.s in data:
            self.Method.decode_value(data[NcCommandEnums.Method.s])
        if NcCommandEnums.Arguments.s in data:
            self.Arguments.decode_value(data[NcCommandEnums.Arguments.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcCommandValue:
        o = NcCommandValue()
        o.Handle = self.Handle.clone()
        o.OId = self.OId.clone()
        o.Object = self.Object.clone()
        o.MethodId = self.MethodId.clone()
        o.Method = self.Method.clone()
        o.Arguments = self.Arguments.clone()
        return o


class NcCommand:
    """Optional object type: NcCommand."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcCommandValue = NcCommandValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcCommandValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcCommandValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcCommandValue | None = None) -> NcCommandValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_Handle(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Handle

    def set_Handle(self, v: Any) -> None:
        assert self._defined, "NcCommand must be defined before setting Handle"
        _assign_value(self._value.Handle, v)

    def get_OId(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.OId

    def set_OId(self, v: Any) -> None:
        assert self._defined, "NcCommand must be defined before setting OId"
        _assign_value(self._value.OId, v)

    def get_Object(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Object

    def set_Object(self, v: Any) -> None:
        assert self._defined, "NcCommand must be defined before setting Object"
        _assign_value(self._value.Object, v)

    def get_MethodId(self) -> NcMethodId:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MethodId

    def set_MethodId(self, v: Any) -> None:
        assert self._defined, "NcCommand must be defined before setting MethodId"
        _assign_value(self._value.MethodId, v)

    def get_Method(self) -> NString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Method

    def set_Method(self, v: Any) -> None:
        assert self._defined, "NcCommand must be defined before setting Method"
        _assign_value(self._value.Method, v)

    def get_Arguments(self) -> NGeneric:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Arguments

    def set_Arguments(self, v: Any) -> None:
        assert self._defined, "NcCommand must be defined before setting Arguments"
        _assign_value(self._value.Arguments, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcCommandValue()

    def clone(self) -> NcCommand:
        o = NcCommand()
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
            return f"NcCommand(defined)"
        return "NcCommand(<undefined>)"


def make_nccommand_value(v: NcCommandValue) -> NcCommandValue:
    """Factory: create a NcCommandValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nccommand(v: NcCommandValue) -> NcCommand:
    """Factory: create a defined NcCommand from a NcCommandValue."""
    o = NcCommand()
    o.set_value(v)
    return o

