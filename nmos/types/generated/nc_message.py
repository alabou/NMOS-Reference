"""Generated NMOS type: NcMessage. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.types.generated.nc_command_message import NcCommandMessage, NcCommandMessageValue
from nmos.types.generated.nc_command_response_message import NcCommandResponseMessage, NcCommandResponseMessageValue
from nmos.types.generated.nc_notification_message import NcNotificationMessage, NcNotificationMessageValue
from nmos.types.generated.nc_subscription_message import NcSubscriptionMessage, NcSubscriptionMessageValue
from nmos.types.generated.nc_subscription_response_message import NcSubscriptionResponseMessage, NcSubscriptionResponseMessageValue
from nmos.types.generated.nc_error_message import NcErrorMessage, NcErrorMessageValue




# --- Type predicates (ported from Go TypePredicate* functions) ---

def _predicate_NcCommandMessage(data: dict[str, Any]) -> bool:
    """Check if data matches NcCommandMessage."""
    if data.get("message_type") != 0:
        return False
    return True

def _predicate_NcCommandResponseMessage(data: dict[str, Any]) -> bool:
    """Check if data matches NcCommandResponseMessage."""
    if data.get("message_type") != 1:
        return False
    return True

def _predicate_NcNotificationMessage(data: dict[str, Any]) -> bool:
    """Check if data matches NcNotificationMessage."""
    if data.get("message_type") != 2:
        return False
    return True

def _predicate_NcSubscriptionMessage(data: dict[str, Any]) -> bool:
    """Check if data matches NcSubscriptionMessage."""
    if data.get("message_type") != 3:
        return False
    return True

def _predicate_NcSubscriptionResponseMessage(data: dict[str, Any]) -> bool:
    """Check if data matches NcSubscriptionResponseMessage."""
    if data.get("message_type") != 4:
        return False
    return True

def _predicate_NcErrorMessage(data: dict[str, Any]) -> bool:
    """Check if data matches NcErrorMessage."""
    if data.get("message_type") != 5:
        return False
    return True


class NcMessageValue:
    """Polymorphic value for NcMessage. Holds one of: NcCommandMessage, NcCommandResponseMessage, NcNotificationMessage, NcSubscriptionMessage, NcSubscriptionResponseMessage, NcErrorMessage."""

    __slots__ = ("_inner",)

    def __init__(self) -> None:
        self._inner: Any = None

    def get(self) -> Any:
        return self._inner

    def set(self, v: Any) -> None:
        self._inner = v

    def clone(self) -> NcMessageValue:
        o = NcMessageValue()
        if self._inner is not None and hasattr(self._inner, "clone"):
            o._inner = self._inner.clone()
        else:
            o._inner = self._inner
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if self._inner is None:
            return
        # Type-switch: delegate to concrete type's encode
        if isinstance(self._inner, NcCommandMessage):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NcCommandResponseMessage):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NcNotificationMessage):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NcSubscriptionMessage):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NcSubscriptionResponseMessage):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NcErrorMessage):
            self._inner.encode(engine, name)
            return

    def decode(self, engine: JsonEngine, data: Any) -> None:
        """Polymorphic decode using predicates (mirrors Go TypePredicate pattern).

        Predicates are checked in the same order as Go's T field.
        First matching predicate determines the concrete type to decode.
        """
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for polymorphic NcMessage")

        if _predicate_NcCommandMessage(data):
            obj_0 = NcCommandMessage()
            obj_0.decode(engine, data)
            self._inner = obj_0
            return
        if _predicate_NcCommandResponseMessage(data):
            obj_1 = NcCommandResponseMessage()
            obj_1.decode(engine, data)
            self._inner = obj_1
            return
        if _predicate_NcNotificationMessage(data):
            obj_2 = NcNotificationMessage()
            obj_2.decode(engine, data)
            self._inner = obj_2
            return
        if _predicate_NcSubscriptionMessage(data):
            obj_3 = NcSubscriptionMessage()
            obj_3.decode(engine, data)
            self._inner = obj_3
            return
        if _predicate_NcSubscriptionResponseMessage(data):
            obj_4 = NcSubscriptionResponseMessage()
            obj_4.decode(engine, data)
            self._inner = obj_4
            return
        if _predicate_NcErrorMessage(data):
            obj_5 = NcErrorMessage()
            obj_5.decode(engine, data)
            self._inner = obj_5
            return
        raise InvalidData("no matching type for polymorphic NcMessage")

    def decode_value(self, data: Any) -> None:
        self.decode(JsonEngine(), data)


class NcMessage:
    """Polymorphic type: NcMessage. Wraps one of NcCommandMessage, NcCommandResponseMessage, NcNotificationMessage, NcSubscriptionMessage, NcSubscriptionResponseMessage, NcErrorMessage."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NcMessageValue = NcMessageValue()

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> Any:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._value.get()

    @value.setter
    def value(self, v: Any) -> None:
        self._defined = True
        self._value.set(v)

    def get(self, default: Any = None) -> Any:
        return self._value.get() if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True

    def set_to_zero(self) -> None:
        self._defined = False
        self._value = NcMessageValue()

    def clone(self) -> NcMessage:
        o = NcMessage()
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
        self._value.decode(JsonEngine(), data)
        self._defined = True

    def __repr__(self) -> str:
        if self._defined:
            inner = self._value.get()
            return f"NcMessage({type(inner).__name__})"
        return "NcMessage(<undefined>)"


def make_ncmessage(v: Any) -> NcMessage:
    """Factory: create a defined NcMessage with the given concrete value."""
    o = NcMessage()
    o.value = v
    return o

