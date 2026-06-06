"""Generated NMOS type: NReceiver. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.types.generated.nreceiver_video import NReceiverVideo, NReceiverVideoValue
from nmos.types.generated.nreceiver_audio import NReceiverAudio, NReceiverAudioValue
from nmos.types.generated.nreceiver_data import NReceiverData, NReceiverDataValue
from nmos.types.generated.nreceiver_mux import NReceiverMux, NReceiverMuxValue




# --- Type predicates (ported from Go TypePredicate* functions) ---

def _predicate_NReceiverVideo(data: dict[str, Any]) -> bool:
    """Check if data matches NReceiverVideo."""
    if data.get("format") != "urn:x-nmos:format:video":
        return False
    return True

def _predicate_NReceiverAudio(data: dict[str, Any]) -> bool:
    """Check if data matches NReceiverAudio."""
    if data.get("format") != "urn:x-nmos:format:audio":
        return False
    return True

def _predicate_NReceiverData(data: dict[str, Any]) -> bool:
    """Check if data matches NReceiverData."""
    if data.get("format") != "urn:x-nmos:format:data":
        return False
    return True

def _predicate_NReceiverMux(data: dict[str, Any]) -> bool:
    """Check if data matches NReceiverMux."""
    if data.get("format") != "urn:x-nmos:format:mux":
        return False
    return True


class NReceiverValue:
    """Polymorphic value for NReceiver. Holds one of: NReceiverVideo, NReceiverAudio, NReceiverData, NReceiverMux."""

    __slots__ = ("_inner",)

    def __init__(self) -> None:
        self._inner: Any = None

    def get(self) -> Any:
        return self._inner

    def set(self, v: Any) -> None:
        self._inner = v

    def clone(self) -> NReceiverValue:
        o = NReceiverValue()
        if self._inner is not None and hasattr(self._inner, "clone"):
            o._inner = self._inner.clone()
        else:
            o._inner = self._inner
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if self._inner is None:
            return
        # Type-switch: delegate to concrete type's encode
        if isinstance(self._inner, NReceiverVideo):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NReceiverAudio):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NReceiverData):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NReceiverMux):
            self._inner.encode(engine, name)
            return

    def decode(self, engine: JsonEngine, data: Any) -> None:
        """Polymorphic decode using predicates (mirrors Go TypePredicate pattern).

        Predicates are checked in the same order as Go's T field.
        First matching predicate determines the concrete type to decode.
        """
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for polymorphic NReceiver")

        if _predicate_NReceiverVideo(data):
            obj_0 = NReceiverVideo()
            obj_0.decode(engine, data)
            self._inner = obj_0
            return
        if _predicate_NReceiverAudio(data):
            obj_1 = NReceiverAudio()
            obj_1.decode(engine, data)
            self._inner = obj_1
            return
        if _predicate_NReceiverData(data):
            obj_2 = NReceiverData()
            obj_2.decode(engine, data)
            self._inner = obj_2
            return
        if _predicate_NReceiverMux(data):
            obj_3 = NReceiverMux()
            obj_3.decode(engine, data)
            self._inner = obj_3
            return
        raise InvalidData("no matching type for polymorphic NReceiver")

    def decode_value(self, data: Any) -> None:
        self.decode(JsonEngine(), data)


class NReceiver:
    """Polymorphic type: NReceiver. Wraps one of NReceiverVideo, NReceiverAudio, NReceiverData, NReceiverMux."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NReceiverValue = NReceiverValue()

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
        self._value = NReceiverValue()

    def clone(self) -> NReceiver:
        o = NReceiver()
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
            return f"NReceiver({type(inner).__name__})"
        return "NReceiver(<undefined>)"


def make_nreceiver(v: Any) -> NReceiver:
    """Factory: create a defined NReceiver with the given concrete value."""
    o = NReceiver()
    o.value = v
    return o

