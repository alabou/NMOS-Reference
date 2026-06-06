"""Generated NMOS type: NcNotificationMessage. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NInt
from nmos.types.generated.nc_array_of_notification import NcArrayOfNotification, NcArrayOfNotificationValue
from nmos.validators import CheckNotificationMessageType

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcNotificationMessageEnums:
    """JSON property name enums for NcNotificationMessage."""
    MessageType = EnumRegistry.get("messageType")
    Notifications = EnumRegistry.get("notifications")
    pass


class NcNotificationMessageValue:
    """Inner value struct for NcNotificationMessage."""

    __slots__ = (
        "MessageType",
        "Notifications",
    )

    def __init__(self) -> None:
        self.MessageType: NInt = NInt()
        self.Notifications: NcArrayOfNotification = NcArrayOfNotification()

    def set_to_default(self) -> None:
        self.MessageType.set_to_default()
        self.Notifications.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.MessageType.defined:
            raise InvalidObject("missing required member MessageType")
        if not self.Notifications.defined:
            raise InvalidObject("missing required member Notifications")
        if self.MessageType.defined:
            CheckNotificationMessageType(self.MessageType)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.MessageType.encode(engine, NcNotificationMessageEnums.MessageType)
        self.Notifications.encode(engine, NcNotificationMessageEnums.Notifications)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcNotificationMessage")

        if NcNotificationMessageEnums.MessageType.s in data:
            self.MessageType.decode_value(data[NcNotificationMessageEnums.MessageType.s])
        if NcNotificationMessageEnums.Notifications.s in data:
            self.Notifications.decode_value(data[NcNotificationMessageEnums.Notifications.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcNotificationMessageValue:
        o = NcNotificationMessageValue()
        o.MessageType = self.MessageType.clone()
        o.Notifications = self.Notifications.clone()
        return o


class NcNotificationMessage:
    """Optional object type: NcNotificationMessage."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcNotificationMessageValue = NcNotificationMessageValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcNotificationMessageValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcNotificationMessageValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcNotificationMessageValue | None = None) -> NcNotificationMessageValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_MessageType(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.MessageType

    def set_MessageType(self, v: Any) -> None:
        assert self._defined, "NcNotificationMessage must be defined before setting MessageType"
        _assign_value(self._value.MessageType, v)

    def get_Notifications(self) -> NcArrayOfNotification:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.Notifications

    def set_Notifications(self, v: Any) -> None:
        assert self._defined, "NcNotificationMessage must be defined before setting Notifications"
        _assign_value(self._value.Notifications, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcNotificationMessageValue()

    def clone(self) -> NcNotificationMessage:
        o = NcNotificationMessage()
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
            return f"NcNotificationMessage(defined)"
        return "NcNotificationMessage(<undefined>)"


def make_ncnotificationmessage_value(v: NcNotificationMessageValue) -> NcNotificationMessageValue:
    """Factory: create a NcNotificationMessageValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ncnotificationmessage(v: NcNotificationMessageValue) -> NcNotificationMessage:
    """Factory: create a defined NcNotificationMessage from a NcNotificationMessageValue."""
    o = NcNotificationMessage()
    o.set_value(v)
    return o

