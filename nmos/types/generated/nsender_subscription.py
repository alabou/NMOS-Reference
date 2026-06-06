"""Generated NMOS type: NSenderSubscription. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NNullString, NBool
from nmos.validators import CheckResourceIdNullableString

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NSenderSubscriptionEnums:
    """JSON property name enums for NSenderSubscription."""
    ReceiverId = EnumRegistry.get("receiver_id")
    Active = EnumRegistry.get("active")
    pass


class NSenderSubscriptionValue:
    """Inner value struct for NSenderSubscription."""

    __slots__ = (
        "ReceiverId",
        "Active",
    )

    def __init__(self) -> None:
        self.ReceiverId: NNullString = NNullString()
        self.Active: NBool = NBool()

    def set_to_default(self) -> None:
        _assign_value(self.ReceiverId, None)
        _assign_value(self.Active, False)
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.ReceiverId.defined:
            raise InvalidObject("missing required member ReceiverId")
        if not self.Active.defined:
            raise InvalidObject("missing required member Active")
        if self.ReceiverId.defined:
            CheckResourceIdNullableString(self.ReceiverId)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.ReceiverId.encode(engine, NSenderSubscriptionEnums.ReceiverId)
        self.Active.encode(engine, NSenderSubscriptionEnums.Active)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NSenderSubscription")

        if NSenderSubscriptionEnums.ReceiverId.s in data:
            self.ReceiverId.decode_value(data[NSenderSubscriptionEnums.ReceiverId.s])
        if NSenderSubscriptionEnums.Active.s in data:
            self.Active.decode_value(data[NSenderSubscriptionEnums.Active.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NSenderSubscriptionValue:
        o = NSenderSubscriptionValue()
        o.ReceiverId = self.ReceiverId.clone()
        o.Active = self.Active.clone()
        return o


class NSenderSubscription:
    """Optional object type: NSenderSubscription."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NSenderSubscriptionValue = NSenderSubscriptionValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NSenderSubscriptionValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NSenderSubscriptionValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NSenderSubscriptionValue | None = None) -> NSenderSubscriptionValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_ReceiverId(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.ReceiverId

    def set_ReceiverId(self, v: Any) -> None:
        assert self._defined, "NSenderSubscription must be defined before setting ReceiverId"
        _assign_value(self._value.ReceiverId, v)

    def get_Active(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Active

    def set_Active(self, v: Any) -> None:
        assert self._defined, "NSenderSubscription must be defined before setting Active"
        _assign_value(self._value.Active, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NSenderSubscriptionValue()

    def clone(self) -> NSenderSubscription:
        o = NSenderSubscription()
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
            return f"NSenderSubscription(defined)"
        return "NSenderSubscription(<undefined>)"


def make_nsendersubscription_value(v: NSenderSubscriptionValue) -> NSenderSubscriptionValue:
    """Factory: create a NSenderSubscriptionValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nsendersubscription(v: NSenderSubscriptionValue) -> NSenderSubscription:
    """Factory: create a defined NSenderSubscription from a NSenderSubscriptionValue."""
    o = NSenderSubscription()
    o.set_value(v)
    return o

