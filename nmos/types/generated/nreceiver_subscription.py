"""Generated NMOS type: NReceiverSubscription. DO NOT EDIT."""

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


class NReceiverSubscriptionEnums:
    """JSON property name enums for NReceiverSubscription."""
    SenderId = EnumRegistry.get("sender_id")
    Active = EnumRegistry.get("active")
    pass


class NReceiverSubscriptionValue:
    """Inner value struct for NReceiverSubscription."""

    __slots__ = (
        "SenderId",
        "Active",
    )

    def __init__(self) -> None:
        self.SenderId: NNullString = NNullString()
        self.Active: NBool = NBool()

    def set_to_default(self) -> None:
        _assign_value(self.SenderId, None)
        _assign_value(self.Active, False)
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.SenderId.defined:
            raise InvalidObject("missing required member SenderId")
        if not self.Active.defined:
            raise InvalidObject("missing required member Active")
        if self.SenderId.defined:
            CheckResourceIdNullableString(self.SenderId)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.SenderId.encode(engine, NReceiverSubscriptionEnums.SenderId)
        self.Active.encode(engine, NReceiverSubscriptionEnums.Active)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NReceiverSubscription")

        if NReceiverSubscriptionEnums.SenderId.s in data:
            self.SenderId.decode_value(data[NReceiverSubscriptionEnums.SenderId.s])
        if NReceiverSubscriptionEnums.Active.s in data:
            self.Active.decode_value(data[NReceiverSubscriptionEnums.Active.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NReceiverSubscriptionValue:
        o = NReceiverSubscriptionValue()
        o.SenderId = self.SenderId.clone()
        o.Active = self.Active.clone()
        return o


class NReceiverSubscription:
    """Optional object type: NReceiverSubscription."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NReceiverSubscriptionValue = NReceiverSubscriptionValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NReceiverSubscriptionValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NReceiverSubscriptionValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NReceiverSubscriptionValue | None = None) -> NReceiverSubscriptionValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_SenderId(self) -> NNullString:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.SenderId

    def set_SenderId(self, v: Any) -> None:
        assert self._defined, "NReceiverSubscription must be defined before setting SenderId"
        _assign_value(self._value.SenderId, v)

    def get_Active(self) -> NBool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Active

    def set_Active(self, v: Any) -> None:
        assert self._defined, "NReceiverSubscription must be defined before setting Active"
        _assign_value(self._value.Active, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NReceiverSubscriptionValue()

    def clone(self) -> NReceiverSubscription:
        o = NReceiverSubscription()
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
            return f"NReceiverSubscription(defined)"
        return "NReceiverSubscription(<undefined>)"


def make_nreceiversubscription_value(v: NReceiverSubscriptionValue) -> NReceiverSubscriptionValue:
    """Factory: create a NReceiverSubscriptionValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_nreceiversubscription(v: NReceiverSubscriptionValue) -> NReceiverSubscription:
    """Factory: create a defined NReceiverSubscription from a NReceiverSubscriptionValue."""
    o = NReceiverSubscription()
    o.set_value(v)
    return o

