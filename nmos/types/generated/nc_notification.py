"""Generated NMOS type: NcNotification. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.json.types import NInt, NGeneric
from nmos.types.generated.nc_event_id import NcEventId, NcEventIdValue
from nmos.validators import CheckPositiveInteger

def _assign_value(field: Any, value: Any) -> None:
    if hasattr(field, "set_value"):
        field.set_value(value)
    else:
        field.value = value


class NcNotificationEnums:
    """JSON property name enums for NcNotification."""
    OId = EnumRegistry.get("oid")
    EventId = EnumRegistry.get("eventId")
    EventData = EnumRegistry.get("eventData")
    pass


class NcNotificationValue:
    """Inner value struct for NcNotification."""

    __slots__ = (
        "OId",
        "EventId",
        "EventData",
    )

    def __init__(self) -> None:
        self.OId: NInt = NInt()
        self.EventId: NcEventId = NcEventId()
        self.EventData: NGeneric = NGeneric()

    def set_to_default(self) -> None:
        self.OId.set_to_default()
        self.EventId.set_to_default()
        self.EventData.set_to_default()
        pass  # may have no members

    def set_optional_to_default(self) -> None:
        pass  # ensure method body is not empty

    def assert_valid(self) -> None:
        if not self.OId.defined:
            raise InvalidObject("missing required member OId")
        if not self.EventId.defined:
            raise InvalidObject("missing required member EventId")
        if not self.EventData.defined:
            raise InvalidObject("missing required member EventData")
        if self.OId.defined:
            CheckPositiveInteger(self.OId)
        pass  # may have no validations

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        self.assert_valid()
        engine.open_struct(name, False)
        self.OId.encode(engine, NcNotificationEnums.OId)
        self.EventId.encode(engine, NcNotificationEnums.EventId)
        self.EventData.encode(engine, NcNotificationEnums.EventData)
        engine.close_struct()

    def decode(self, engine: JsonEngine, data: Any) -> None:
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for NcNotification")

        if NcNotificationEnums.OId.s in data:
            self.OId.decode_value(data[NcNotificationEnums.OId.s])
        if NcNotificationEnums.EventId.s in data:
            self.EventId.decode_value(data[NcNotificationEnums.EventId.s])
        if NcNotificationEnums.EventData.s in data:
            self.EventData.decode_value(data[NcNotificationEnums.EventData.s])

        self.set_optional_to_default()
        self.assert_valid()

    def clone(self) -> NcNotificationValue:
        o = NcNotificationValue()
        o.OId = self.OId.clone()
        o.EventId = self.EventId.clone()
        o.EventData = self.EventData.clone()
        return o


class NcNotification:
    """Optional object type: NcNotification."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcNotificationValue = NcNotificationValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> NcNotificationValue:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value

    def set_value(self, v: NcNotificationValue) -> None:
        self._defined = True
        self._value = v.clone()  # copy to match Go's value semantics
        self._value.set_optional_to_default()

    def get(self, default: NcNotificationValue | None = None) -> NcNotificationValue | None:
        return self._value if self._defined else default

    # --- Per-field convenience accessors ---
    def get_OId(self) -> NInt:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.OId

    def set_OId(self, v: Any) -> None:
        assert self._defined, "NcNotification must be defined before setting OId"
        _assign_value(self._value.OId, v)

    def get_EventId(self) -> NcEventId:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.EventId

    def set_EventId(self, v: Any) -> None:
        assert self._defined, "NcNotification must be defined before setting EventId"
        _assign_value(self._value.EventId, v)

    def get_EventData(self) -> NGeneric:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.EventData

    def set_EventData(self, v: Any) -> None:
        assert self._defined, "NcNotification must be defined before setting EventData"
        _assign_value(self._value.EventData, v)


    def set_to_default(self) -> None:
        self._defined = True
        self._value.set_to_default()

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcNotificationValue()

    def clone(self) -> NcNotification:
        o = NcNotification()
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
            return f"NcNotification(defined)"
        return "NcNotification(<undefined>)"


def make_ncnotification_value(v: NcNotificationValue) -> NcNotificationValue:
    """Factory: create a NcNotificationValue with optional defaults applied."""
    v.set_optional_to_default()
    return v


def make_ncnotification(v: NcNotificationValue) -> NcNotification:
    """Factory: create a defined NcNotification from a NcNotificationValue."""
    o = NcNotification()
    o.set_value(v)
    return o

