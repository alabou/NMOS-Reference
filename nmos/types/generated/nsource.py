"""Generated NMOS type: NSource. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.types.generated.nsource_video import NSourceVideo, NSourceVideoValue
from nmos.types.generated.nsource_audio import NSourceAudio, NSourceAudioValue
from nmos.types.generated.nsource_data import NSourceData, NSourceDataValue
from nmos.types.generated.nsource_mux import NSourceMux, NSourceMuxValue




# --- Type predicates (ported from Go TypePredicate* functions) ---

def _predicate_NSourceVideo(data: dict[str, Any]) -> bool:
    """Check if data matches NSourceVideo."""
    if data.get("format") != "urn:x-nmos:format:video":
        return False
    return True

def _predicate_NSourceAudio(data: dict[str, Any]) -> bool:
    """Check if data matches NSourceAudio."""
    if data.get("format") != "urn:x-nmos:format:audio":
        return False
    return True

def _predicate_NSourceData(data: dict[str, Any]) -> bool:
    """Check if data matches NSourceData."""
    if data.get("format") != "urn:x-nmos:format:data":
        return False
    return True

def _predicate_NSourceMux(data: dict[str, Any]) -> bool:
    """Check if data matches NSourceMux."""
    if data.get("format") != "urn:x-nmos:format:mux":
        return False
    return True


class NSourceValue:
    """Polymorphic value for NSource. Holds one of: NSourceVideo, NSourceAudio, NSourceData, NSourceMux."""

    __slots__ = ("_inner",)

    def __init__(self) -> None:
        self._inner: Any = None

    def get(self) -> Any:
        return self._inner

    def set(self, v: Any) -> None:
        self._inner = v

    def clone(self) -> NSourceValue:
        o = NSourceValue()
        if self._inner is not None and hasattr(self._inner, "clone"):
            o._inner = self._inner.clone()
        else:
            o._inner = self._inner
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if self._inner is None:
            return
        # Type-switch: delegate to concrete type's encode
        if isinstance(self._inner, NSourceVideo):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NSourceAudio):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NSourceData):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NSourceMux):
            self._inner.encode(engine, name)
            return

    def decode(self, engine: JsonEngine, data: Any) -> None:
        """Polymorphic decode using predicates (mirrors Go TypePredicate pattern).

        Predicates are checked in the same order as Go's T field.
        First matching predicate determines the concrete type to decode.
        """
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for polymorphic NSource")

        if _predicate_NSourceVideo(data):
            obj_0 = NSourceVideo()
            obj_0.decode(engine, data)
            self._inner = obj_0
            return
        if _predicate_NSourceAudio(data):
            obj_1 = NSourceAudio()
            obj_1.decode(engine, data)
            self._inner = obj_1
            return
        if _predicate_NSourceData(data):
            obj_2 = NSourceData()
            obj_2.decode(engine, data)
            self._inner = obj_2
            return
        if _predicate_NSourceMux(data):
            obj_3 = NSourceMux()
            obj_3.decode(engine, data)
            self._inner = obj_3
            return
        raise InvalidData("no matching type for polymorphic NSource")

    def decode_value(self, data: Any) -> None:
        self.decode(JsonEngine(), data)


class NSource:
    """Polymorphic type: NSource. Wraps one of NSourceVideo, NSourceAudio, NSourceData, NSourceMux."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NSourceValue = NSourceValue()

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
        self._value = NSourceValue()

    def clone(self) -> NSource:
        o = NSource()
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
            return f"NSource({type(inner).__name__})"
        return "NSource(<undefined>)"


def make_nsource(v: Any) -> NSource:
    """Factory: create a defined NSource with the given concrete value."""
    o = NSource()
    o.value = v
    return o

