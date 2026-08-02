"""Generated NMOS type: NRegistrationResourcePost. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.types.generated.nregistration_post_node import NRegistrationPostNode, NRegistrationPostNodeValue
from nmos.types.generated.nregistration_post_device import NRegistrationPostDevice, NRegistrationPostDeviceValue
from nmos.types.generated.nregistration_post_source import NRegistrationPostSource, NRegistrationPostSourceValue
from nmos.types.generated.nregistration_post_flow import NRegistrationPostFlow, NRegistrationPostFlowValue
from nmos.types.generated.nregistration_post_sender import NRegistrationPostSender, NRegistrationPostSenderValue
from nmos.types.generated.nregistration_post_receiver import NRegistrationPostReceiver, NRegistrationPostReceiverValue




# --- Type predicates (ported from Go TypePredicate* functions) ---

def _predicate_NRegistrationPostNode(data: dict[str, Any]) -> bool:
    """Check if data matches NRegistrationPostNode."""
    if data.get("type") != "node":
        return False
    return True

def _predicate_NRegistrationPostDevice(data: dict[str, Any]) -> bool:
    """Check if data matches NRegistrationPostDevice."""
    if data.get("type") != "device":
        return False
    return True

def _predicate_NRegistrationPostSource(data: dict[str, Any]) -> bool:
    """Check if data matches NRegistrationPostSource."""
    if data.get("type") != "source":
        return False
    return True

def _predicate_NRegistrationPostFlow(data: dict[str, Any]) -> bool:
    """Check if data matches NRegistrationPostFlow."""
    if data.get("type") != "flow":
        return False
    return True

def _predicate_NRegistrationPostSender(data: dict[str, Any]) -> bool:
    """Check if data matches NRegistrationPostSender."""
    if data.get("type") != "sender":
        return False
    return True

def _predicate_NRegistrationPostReceiver(data: dict[str, Any]) -> bool:
    """Check if data matches NRegistrationPostReceiver."""
    if data.get("type") != "receiver":
        return False
    return True


class NRegistrationResourcePostValue:
    """Polymorphic value for NRegistrationResourcePost. Holds one of: NRegistrationPostNode, NRegistrationPostDevice, NRegistrationPostSource, NRegistrationPostFlow, NRegistrationPostSender, NRegistrationPostReceiver."""

    __slots__ = ("_inner",)

    def __init__(self) -> None:
        self._inner: Any = None

    def get(self) -> Any:
        return self._inner

    def set(self, v: Any) -> None:
        self._inner = v

    def clone(self) -> NRegistrationResourcePostValue:
        o = NRegistrationResourcePostValue()
        if self._inner is not None and hasattr(self._inner, "clone"):
            o._inner = self._inner.clone()
        else:
            o._inner = self._inner
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if self._inner is None:
            return
        # Type-switch: delegate to concrete type's encode
        if isinstance(self._inner, NRegistrationPostNode):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NRegistrationPostDevice):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NRegistrationPostSource):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NRegistrationPostFlow):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NRegistrationPostSender):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NRegistrationPostReceiver):
            self._inner.encode(engine, name)
            return

    def decode(self, engine: JsonEngine, data: Any) -> None:
        """Polymorphic decode using predicates (mirrors Go TypePredicate pattern).

        Predicates are checked in the same order as Go's T field.
        First matching predicate determines the concrete type to decode.
        """
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for polymorphic NRegistrationResourcePost")

        if _predicate_NRegistrationPostNode(data):
            obj_0 = NRegistrationPostNode()
            obj_0.decode(engine, data)
            self._inner = obj_0
            return
        if _predicate_NRegistrationPostDevice(data):
            obj_1 = NRegistrationPostDevice()
            obj_1.decode(engine, data)
            self._inner = obj_1
            return
        if _predicate_NRegistrationPostSource(data):
            obj_2 = NRegistrationPostSource()
            obj_2.decode(engine, data)
            self._inner = obj_2
            return
        if _predicate_NRegistrationPostFlow(data):
            obj_3 = NRegistrationPostFlow()
            obj_3.decode(engine, data)
            self._inner = obj_3
            return
        if _predicate_NRegistrationPostSender(data):
            obj_4 = NRegistrationPostSender()
            obj_4.decode(engine, data)
            self._inner = obj_4
            return
        if _predicate_NRegistrationPostReceiver(data):
            obj_5 = NRegistrationPostReceiver()
            obj_5.decode(engine, data)
            self._inner = obj_5
            return
        raise InvalidData("no matching type for polymorphic NRegistrationResourcePost")

    def decode_value(self, data: Any) -> None:
        self.decode(JsonEngine(), data)


class NRegistrationResourcePost:
    """Polymorphic type: NRegistrationResourcePost. Wraps one of NRegistrationPostNode, NRegistrationPostDevice, NRegistrationPostSource, NRegistrationPostFlow, NRegistrationPostSender, NRegistrationPostReceiver."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NRegistrationResourcePostValue = NRegistrationResourcePostValue()

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
        self._value = NRegistrationResourcePostValue()

    def clone(self) -> NRegistrationResourcePost:
        o = NRegistrationResourcePost()
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
            return f"NRegistrationResourcePost({type(inner).__name__})"
        return "NRegistrationResourcePost(<undefined>)"


def make_nregistrationresourcepost(v: Any) -> NRegistrationResourcePost:
    """Factory: create a defined NRegistrationResourcePost with the given concrete value."""
    o = NRegistrationResourcePost()
    o.value = v
    return o

