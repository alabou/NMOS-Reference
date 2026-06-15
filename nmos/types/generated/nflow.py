"""Generated NMOS type: NFlow. DO NOT EDIT."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidObject, NotAvailable, NotMatching
from nmos.json.engine import JsonEngine
from nmos.types.generated.nflow_video_raw import NFlowVideoRaw, NFlowVideoRawValue
from nmos.types.generated.nflow_video_coded import NFlowVideoCoded, NFlowVideoCodedValue
from nmos.types.generated.nflow_audio_raw import NFlowAudioRaw, NFlowAudioRawValue
from nmos.types.generated.nflow_audio_coded import NFlowAudioCoded, NFlowAudioCodedValue
from nmos.types.generated.nflow_data import NFlowData, NFlowDataValue
from nmos.types.generated.nflow_data_sdianc import NFlowDataSdianc, NFlowDataSdiancValue
from nmos.types.generated.nflow_data_json import NFlowDataJson, NFlowDataJsonValue
from nmos.types.generated.nflow_mux import NFlowMux, NFlowMuxValue




# --- Type predicates (ported from Go TypePredicate* functions) ---

def _predicate_NFlowVideoRaw(data: dict[str, Any]) -> bool:
    """Check if data matches NFlowVideoRaw."""
    if data.get("format") != "urn:x-nmos:format:video":
        return False
    if data.get("media_type") != "video/raw":
        return False
    return True

def _predicate_NFlowVideoCoded(data: dict[str, Any]) -> bool:
    """Check if data matches NFlowVideoCoded."""
    if data.get("format") != "urn:x-nmos:format:video":
        return False
    if data.get("media_type") == "video/raw":
        return False
    if "media_type" not in data:
        return False
    return True

def _predicate_NFlowAudioRaw(data: dict[str, Any]) -> bool:
    """Check if data matches NFlowAudioRaw."""
    if data.get("format") != "urn:x-nmos:format:audio":
        return False
    if data.get("media_type") not in {
        "audio/L8",
        "audio/L16",
        "audio/L20",
        "audio/L24",
    }:
        return False
    return True

def _predicate_NFlowAudioCoded(data: dict[str, Any]) -> bool:
    """Check if data matches NFlowAudioCoded."""
    if data.get("format") != "urn:x-nmos:format:audio":
        return False
    if data.get("media_type") in {
        "audio/L8",
        "audio/L16",
        "audio/L20",
        "audio/L24",
    }:
        return False
    if "media_type" not in data:
        return False
    return True

def _predicate_NFlowData(data: dict[str, Any]) -> bool:
    """Check if data matches NFlowData."""
    if data.get("format") != "urn:x-nmos:format:data":
        return False
    if data.get("media_type") in {
        "video/smpte291",
        "application/json",
    }:
        return False
    if "media_type" not in data:
        return False
    return True

def _predicate_NFlowDataSdianc(data: dict[str, Any]) -> bool:
    """Check if data matches NFlowDataSdianc."""
    if data.get("format") != "urn:x-nmos:format:data":
        return False
    if data.get("media_type") != "video/smpte291":
        return False
    return True

def _predicate_NFlowDataJson(data: dict[str, Any]) -> bool:
    """Check if data matches NFlowDataJson."""
    if data.get("format") != "urn:x-nmos:format:data":
        return False
    if data.get("media_type") != "application/json":
        return False
    return True

def _predicate_NFlowMux(data: dict[str, Any]) -> bool:
    """Check if data matches NFlowMux."""
    if data.get("format") != "urn:x-nmos:format:mux":
        return False
    return True


class NFlowValue:
    """Polymorphic value for NFlow. Holds one of: NFlowVideoRaw, NFlowVideoCoded, NFlowAudioRaw, NFlowAudioCoded, NFlowData, NFlowDataSdianc, NFlowDataJson, NFlowMux."""

    __slots__ = ("_inner",)

    def __init__(self) -> None:
        self._inner: Any = None

    def get(self) -> Any:
        return self._inner

    def set(self, v: Any) -> None:
        self._inner = v

    def clone(self) -> NFlowValue:
        o = NFlowValue()
        if self._inner is not None and hasattr(self._inner, "clone"):
            o._inner = self._inner.clone()
        else:
            o._inner = self._inner
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if self._inner is None:
            return
        # Type-switch: delegate to concrete type's encode
        if isinstance(self._inner, NFlowVideoRaw):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NFlowVideoCoded):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NFlowAudioRaw):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NFlowAudioCoded):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NFlowData):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NFlowDataSdianc):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NFlowDataJson):
            self._inner.encode(engine, name)
            return
        if isinstance(self._inner, NFlowMux):
            self._inner.encode(engine, name)
            return

    def decode(self, engine: JsonEngine, data: Any) -> None:
        """Polymorphic decode using predicates (mirrors Go TypePredicate pattern).

        Predicates are checked in the same order as Go's T field.
        First matching predicate determines the concrete type to decode.
        """
        if not isinstance(data, dict):
            raise InvalidData("expected JSON object for polymorphic NFlow")

        if _predicate_NFlowVideoRaw(data):
            obj_0 = NFlowVideoRaw()
            obj_0.decode(engine, data)
            self._inner = obj_0
            return
        if _predicate_NFlowVideoCoded(data):
            obj_1 = NFlowVideoCoded()
            obj_1.decode(engine, data)
            self._inner = obj_1
            return
        if _predicate_NFlowAudioRaw(data):
            obj_2 = NFlowAudioRaw()
            obj_2.decode(engine, data)
            self._inner = obj_2
            return
        if _predicate_NFlowAudioCoded(data):
            obj_3 = NFlowAudioCoded()
            obj_3.decode(engine, data)
            self._inner = obj_3
            return
        if _predicate_NFlowData(data):
            obj_4 = NFlowData()
            obj_4.decode(engine, data)
            self._inner = obj_4
            return
        if _predicate_NFlowDataSdianc(data):
            obj_5 = NFlowDataSdianc()
            obj_5.decode(engine, data)
            self._inner = obj_5
            return
        if _predicate_NFlowDataJson(data):
            obj_6 = NFlowDataJson()
            obj_6.decode(engine, data)
            self._inner = obj_6
            return
        if _predicate_NFlowMux(data):
            obj_7 = NFlowMux()
            obj_7.decode(engine, data)
            self._inner = obj_7
            return
        raise InvalidData("no matching type for polymorphic NFlow")

    def decode_value(self, data: Any) -> None:
        self.decode(JsonEngine(), data)


class NFlow:
    """Polymorphic type: NFlow. Wraps one of NFlowVideoRaw, NFlowVideoCoded, NFlowAudioRaw, NFlowAudioCoded, NFlowData, NFlowDataSdianc, NFlowDataJson, NFlowMux."""

    __slots__ = ("_defined", "_value")

    def __init__(self) -> None:
        self._defined: bool = False
        self._value: NFlowValue = NFlowValue()

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
        self._value = NFlowValue()

    def clone(self) -> NFlow:
        o = NFlow()
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
            return f"NFlow({type(inner).__name__})"
        return "NFlow(<undefined>)"


def make_nflow(v: Any) -> NFlow:
    """Factory: create a defined NFlow with the given concrete value."""
    o = NFlow()
    o.value = v
    return o

