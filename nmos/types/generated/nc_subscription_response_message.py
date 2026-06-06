"""Generated NMOS type: NcSubscriptionResponseMessage. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NInt, NArrayOfInt
from nmos.validators import CheckSubscriptionResponseMessageType

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcSubscriptionResponseMessageEnums:
    """JSON property name enums for NcSubscriptionResponseMessage."""
    MessageType = EnumRegistry.get("messageType")
    Subscriptions = EnumRegistry.get("subscriptions")
    pass


class NcSubscriptionResponseMessageValue:
    """Inner value struct for NcSubscriptionResponseMessage."""

    __slots__ = (
        "MessageType",
        "Subscriptions",
    )

    def __init__(self) -> None:
        self.MessageType: NInt = NInt()
        self.Subscriptions: NArrayOfInt = NArrayOfInt()

    def set_to_default(self) -> None:
        self.MessageType.set_to_default()
        self.Subscriptions.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.MessageType.defined:
            raise InvalidObject("missing required member MessageType")
        if not self.Subscriptions.defined:
            raise InvalidObject("missing required member Subscriptions")
        if self.MessageType.defined:
            CheckSubscriptionResponseMessageType(self.MessageType)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.MessageType.encode(engine, NcSubscriptionResponseMessageEnums.MessageType)
        self.Subscriptions.encode(engine, NcSubscriptionResponseMessageEnums.Subscriptions)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcSubscriptionResponseMessage")

        if NcSubscriptionResponseMessageEnums.MessageType.s in data:
            self.MessageType.decode_value(data[NcSubscriptionResponseMessageEnums.MessageType.s])
        if NcSubscriptionResponseMessageEnums.Subscriptions.s in data:
            self.Subscriptions.decode_value(data[NcSubscriptionResponseMessageEnums.Subscriptions.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcSubscriptionResponseMessageValue:
        o = NcSubscriptionResponseMessageValue()
        o.MessageType = self.MessageType.clone()
        o.Subscriptions = self.Subscriptions.clone()
        return o


class NcSubscriptionResponseMessage:
    """Optional object type: NcSubscriptionResponseMessage."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcSubscriptionResponseMessageValue = NcSubscriptionResponseMessageValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcSubscriptionResponseMessageValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcSubscriptionResponseMessageValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcSubscriptionResponseMessageValue | None = None) -> NcSubscriptionResponseMessageValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_MessageType(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MessageType

    def set_MessageType(self, v: Any) -> None:
        assert self._defined, "NcSubscriptionResponseMessage must be defined before setting MessageType"
        _assign_value(self._value.MessageType, v)

    def get_Subscriptions(self) -> NArrayOfInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Subscriptions

    def set_Subscriptions(self, v: Any) -> None:
        assert self._defined, "NcSubscriptionResponseMessage must be defined before setting Subscriptions"
        _assign_value(self._value.Subscriptions, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcSubscriptionResponseMessageValue()

    def clone(self) -> NcSubscriptionResponseMessage:
        o = NcSubscriptionResponseMessage()
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
            return f"NcSubscriptionResponseMessage(defined)"
        return "NcSubscriptionResponseMessage(<undefined>)"


def make_ncsubscriptionresponsemessage_value(v: NcSubscriptionResponseMessageValue) -> NcSubscriptionResponseMessageValue:
    """Factory: create a NcSubscriptionResponseMessageValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ncsubscriptionresponsemessage(v: NcSubscriptionResponseMessageValue) -> NcSubscriptionResponseMessage:
    """Factory: create a defined NcSubscriptionResponseMessage from a NcSubscriptionResponseMessageValue."""
    o = NcSubscriptionResponseMessage()
    o.set_value(v)
    return o

