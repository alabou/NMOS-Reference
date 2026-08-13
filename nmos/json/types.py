# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Base value types for NMOS JSON serialization.

Hand-written wrappers NInt, NString, NBool, NFloat, NEnum, NNull,
NNullString, NHyperlink, and their array variants. Generated (object) types
use these as field types.

Each type follows the two-layer pattern:
- Inner value (e.g., the raw int/str/bool) stored directly
- Outer wrapper with _defined flag + Pythonic access API:
  - .defined property (LBYL check)
  - .value property (raises NotAvailable if undefined)
  - .get(default) method (safe access — returns UNDEFINED when not set)

The UNDEFINED sentinel distinguishes "field not present" from "JSON null":
  result = field.get()
  if result is UNDEFINED:  # field not in JSON
  elif result is None:     # field is JSON null
  else:                    # field has a value
"""

from __future__ import annotations

import re as _re
from typing import Any

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import InvalidData, InvalidType, NotAvailable


class _UndefinedType:
    """Singleton sentinel for 'field not present in JSON'.

    Distinct from None (which represents JSON null).
    """

    _instance: _UndefinedType | None = None

    def __new__(cls) -> _UndefinedType:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNDEFINED"

    def __bool__(self) -> bool:
        return False


UNDEFINED = _UndefinedType()
"""Sentinel value returned by .get() when a field is not defined.
Use 'is UNDEFINED' to check (not == or truthiness)."""
from nmos.json.engine import JsonEngine


# ---------------------------------------------------------------------------
# Generic dict/list encoding helpers — use engine structured methods
# so HTML mode produces properly decorated output.
# ---------------------------------------------------------------------------

def _encode_value(engine: JsonEngine, name: EnumId | None, value: Any) -> None:
    """Encode any Python value using the engine's structured methods."""
    if value is None:
        engine.write_null(name)
    elif isinstance(value, bool):
        engine.write_bool(name, value)
    elif isinstance(value, int):
        engine.write_int(name, value)
    elif isinstance(value, float):
        engine.write_float(name, value)
    elif isinstance(value, str):
        engine.write_string(name, value)
    elif isinstance(value, dict):
        _encode_dict(engine, name, value)
    elif isinstance(value, list):
        _encode_list(engine, name, value)
    else:
        engine.write_string(name, str(value))


def _encode_dict(engine: JsonEngine, name: EnumId | None, d: dict[str, Any]) -> None:
    """Encode a Python dict using the engine's open_struct/close_struct."""
    engine.open_struct(name)
    for key, value in d.items():
        key_enum = EnumRegistry.get(key)
        key_id = key_enum if key_enum is not None else EnumId(key)
        _encode_value(engine, key_id, value)
    engine.close_struct()


def _encode_list(engine: JsonEngine, name: EnumId | None, lst: list[Any]) -> None:
    """Encode a Python list using the engine's open_array/close_array."""
    engine.open_array(name)
    for item in lst:
        _encode_value(engine, None, item)
    engine.close_array()


# ---------------------------------------------------------------------------
# NString -- wraps str
# ---------------------------------------------------------------------------

class NString:
    """Optional string value."""

    __slots__ = ("_defined", "_inner")

    def __init__(self) -> None:
        self._defined: bool = False
        self._inner: str = ""

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> str:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._inner

    @value.setter
    def value(self, v: str) -> None:
        if not isinstance(v, str):
            raise InvalidType(f"NString.value requires str, got {type(v).__name__}")
        self._defined = True
        self._inner = v

    def get(self, default: str | _UndefinedType = UNDEFINED) -> str | _UndefinedType:
        return self._inner if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._inner = ""

    def set_to_zero(self) -> None:
        self._defined = False
        self._inner = ""

    def clone(self) -> NString:
        o = NString()
        o._defined = self._defined
        o._inner = self._inner
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if self._defined:
            engine.write_string(name, self._inner)

    def decode_value(self, data: Any) -> None:
        if isinstance(data, str):
            self._defined = True
            self._inner = data
        elif data is None:
            pass  # null leaves it undefined for NString
        else:
            raise InvalidData(f"expected string, got {type(data).__name__}")

    def __repr__(self) -> str:
        if self._defined:
            return f"NString({self._inner!r})"
        return "NString(<undefined>)"


# ---------------------------------------------------------------------------
# NInt -- wraps int
# ---------------------------------------------------------------------------

class NInt:
    """Optional integer value."""

    __slots__ = ("_defined", "_inner")

    def __init__(self) -> None:
        self._defined: bool = False
        self._inner: int = 0

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> int:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._inner

    @value.setter
    def value(self, v: int) -> None:
        if not isinstance(v, int) or isinstance(v, bool):
            raise InvalidType(f"NInt.value requires int, got {type(v).__name__}")
        self._defined = True
        self._inner = v

    def get(self, default: int | _UndefinedType = UNDEFINED) -> int | _UndefinedType:
        return self._inner if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._inner = 0

    def set_to_zero(self) -> None:
        self._defined = False
        self._inner = 0

    def clone(self) -> NInt:
        o = NInt()
        o._defined = self._defined
        o._inner = self._inner
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if self._defined:
            engine.write_int(name, self._inner)

    def decode_value(self, data: Any) -> None:
        if isinstance(data, int) and not isinstance(data, bool):
            self._defined = True
            self._inner = data
        elif isinstance(data, float):
            self._defined = True
            self._inner = int(data)
        else:
            raise InvalidData(f"expected int, got {type(data).__name__}")

    def __repr__(self) -> str:
        if self._defined:
            return f"NInt({self._inner})"
        return "NInt(<undefined>)"


# ---------------------------------------------------------------------------
# NFloat -- wraps float
# ---------------------------------------------------------------------------

class NFloat:
    """Optional float value."""

    __slots__ = ("_defined", "_inner")

    def __init__(self) -> None:
        self._defined: bool = False
        self._inner: float = 0.0

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> float:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._inner

    @value.setter
    def value(self, v: float) -> None:
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise InvalidType(f"NFloat.value requires float, got {type(v).__name__}")
        self._defined = True
        self._inner = float(v)

    def get(self, default: float | _UndefinedType = UNDEFINED) -> float | _UndefinedType:
        return self._inner if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._inner = 0.0

    def set_to_zero(self) -> None:
        self._defined = False
        self._inner = 0.0

    def clone(self) -> NFloat:
        o = NFloat()
        o._defined = self._defined
        o._inner = self._inner
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if self._defined:
            engine.write_float(name, self._inner)

    def decode_value(self, data: Any) -> None:
        if isinstance(data, (int, float)) and not isinstance(data, bool):
            self._defined = True
            self._inner = float(data)
        else:
            raise InvalidData(f"expected float, got {type(data).__name__}")

    def __repr__(self) -> str:
        if self._defined:
            return f"NFloat({self._inner})"
        return "NFloat(<undefined>)"


# ---------------------------------------------------------------------------
# NBool -- wraps bool
# ---------------------------------------------------------------------------

class NBool:
    """Optional boolean value."""

    __slots__ = ("_defined", "_inner")

    def __init__(self) -> None:
        self._defined: bool = False
        self._inner: bool = False

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> bool:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._inner

    @value.setter
    def value(self, v: bool) -> None:
        if not isinstance(v, bool):
            raise InvalidType(f"NBool.value requires bool, got {type(v).__name__}")
        self._defined = True
        self._inner = v

    def get(self, default: bool | _UndefinedType = UNDEFINED) -> bool | _UndefinedType:
        return self._inner if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._inner = False

    def set_to_zero(self) -> None:
        self._defined = False
        self._inner = False

    def clone(self) -> NBool:
        o = NBool()
        o._defined = self._defined
        o._inner = self._inner
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if self._defined:
            engine.write_bool(name, self._inner)

    def decode_value(self, data: Any) -> None:
        if isinstance(data, bool):
            self._defined = True
            self._inner = data
        else:
            raise InvalidData(f"expected bool, got {type(data).__name__}")

    def __repr__(self) -> str:
        if self._defined:
            return f"NBool({self._inner})"
        return "NBool(<undefined>)"


# ---------------------------------------------------------------------------
# NEnum -- wraps EnumId (string-based enum)
# ---------------------------------------------------------------------------

class NEnum:
    """Optional enum value.

    Stored as EnumId internally, serialized as string in JSON.
    """

    __slots__ = ("_defined", "_inner")

    def __init__(self) -> None:
        self._defined: bool = False
        self._inner: EnumId | None = None

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> EnumId:
        if not self._defined or self._inner is None:
            raise NotAvailable("undefined value")
        return self._inner

    @value.setter
    def value(self, v: EnumId) -> None:
        if not isinstance(v, EnumId):
            raise InvalidType(f"NEnum.value requires EnumId, got {type(v).__name__}")
        self._defined = True
        self._inner = v

    def get(self, default: EnumId | _UndefinedType = UNDEFINED) -> EnumId | _UndefinedType | None:
        return self._inner if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._inner = EnumRegistry.get("")

    def set_to_zero(self) -> None:
        self._defined = False
        self._inner = None

    def clone(self) -> NEnum:
        o = NEnum()
        o._defined = self._defined
        o._inner = self._inner  # EnumId is a singleton, no deep copy needed
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if self._defined and self._inner is not None:
            engine.write_string(name, str(self._inner))

    def decode_value(self, data: Any) -> None:
        if isinstance(data, str):
            self._defined = True
            self._inner = EnumRegistry.auto_lookup(data)
        else:
            raise InvalidData(f"expected string for enum, got {type(data).__name__}")

    def __repr__(self) -> str:
        if self._defined and self._inner is not None:
            return f"NEnum({self._inner!r})"
        return "NEnum(<undefined>)"


# ---------------------------------------------------------------------------
# NNull -- wraps Any (can be null, int, float, string, bool)
# ---------------------------------------------------------------------------

class NNull:
    """Optional nullable value (generic JSON value).

    Can hold None (JSON null), int, float, string, or bool.
    """

    __slots__ = ("_defined", "_inner")

    def __init__(self) -> None:
        self._defined: bool = False
        self._inner: Any = None

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> Any:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._inner

    @value.setter
    def value(self, v: Any) -> None:
        self._defined = True
        self._inner = self._cast(v)

    def get(self, default: Any = UNDEFINED) -> Any:
        return self._inner if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._inner = None

    def set_to_zero(self) -> None:
        self._defined = False
        self._inner = None

    @staticmethod
    def _cast(v: Any) -> Any:
        """Normalize the value to match NNull casting behavior.

        Accepts all JSON-representable types: None, bool, int, float, str,
        dict, and list. A generic JSON value accepts anything.
        """
        if v is None:
            return None
        if isinstance(v, (bool, int, float, str, dict, list)):
            return v
        raise InvalidData(f"NNull cannot hold {type(v).__name__}")

    def clone(self) -> NNull:
        o = NNull()
        o._defined = self._defined
        o._inner = self._inner  # primitives are immutable
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if not self._defined:
            return
        if self._inner is None:
            engine.write_null(name)
        elif isinstance(self._inner, bool):
            engine.write_bool(name, self._inner)
        elif isinstance(self._inner, int):
            engine.write_int(name, self._inner)
        elif isinstance(self._inner, float):
            engine.write_float(name, self._inner)
        elif isinstance(self._inner, str):
            engine.write_string(name, self._inner)
        elif isinstance(self._inner, dict):
            _encode_dict(engine, name, self._inner)
        elif isinstance(self._inner, list):
            _encode_list(engine, name, self._inner)

    def decode_value(self, data: Any) -> None:
        self._defined = True
        self._inner = self._cast(data)

    def __repr__(self) -> str:
        if self._defined:
            return f"NNull({self._inner!r})"
        return "NNull(<undefined>)"


# ---------------------------------------------------------------------------
# NNullString -- wraps str | None (can be JSON null or string)
# ---------------------------------------------------------------------------

class NNullString:
    """Optional nullable string.

    Unlike NString, this can represent JSON null as a defined value.
    """

    __slots__ = ("_defined", "_inner")

    def __init__(self) -> None:
        self._defined: bool = False
        self._inner: str | None = None

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> str | None:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._inner

    @value.setter
    def value(self, v: str | None) -> None:
        if v is not None and not isinstance(v, str):
            raise InvalidType(f"NNullString.value requires str or None, got {type(v).__name__}")
        self._defined = True
        self._inner = v

    def get(self, default: str | _UndefinedType = UNDEFINED) -> str | None | _UndefinedType:
        return self._inner if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._inner = None

    def set_to_zero(self) -> None:
        self._defined = False
        self._inner = None

    def clone(self) -> NNullString:
        o = NNullString()
        o._defined = self._defined
        o._inner = self._inner
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if not self._defined:
            return
        if self._inner is None:
            engine.write_null(name)
        else:
            engine.write_string(name, self._inner)

    def decode_value(self, data: Any) -> None:
        self._defined = True
        if data is None:
            self._inner = None
        elif isinstance(data, str):
            self._inner = data
        else:
            raise InvalidData(f"expected string or null, got {type(data).__name__}")

    def __repr__(self) -> str:
        if self._defined:
            return f"NNullString({self._inner!r})"
        return "NNullString(<undefined>)"


# ---------------------------------------------------------------------------
# NHyperlink -- wraps (display_text, url) pair
# ---------------------------------------------------------------------------

class NHyperlink:
    """Optional hyperlink value, stored as a (display_text, url) string pair."""

    __slots__ = ("_defined", "_text", "_link")

    def __init__(self) -> None:
        self._defined: bool = False
        self._text: str = ""
        self._link: str = ""

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> tuple[str, str]:
        if not self._defined:
            raise NotAvailable("undefined value")
        return (self._text, self._link)

    @value.setter
    def value(self, v: tuple[str, str]) -> None:
        self._defined = True
        self._text, self._link = v

    def get(self, default: tuple[str, str] | _UndefinedType = UNDEFINED) -> tuple[str, str] | _UndefinedType:
        return (self._text, self._link) if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._text = ""
        self._link = ""

    def set_to_zero(self) -> None:
        self._defined = False
        self._text = ""
        self._link = ""

    def clone(self) -> NHyperlink:
        o = NHyperlink()
        o._defined = self._defined
        o._text = self._text
        o._link = self._link
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if self._defined:
            engine.write_hyperlink(name, self._text, self._link)

    def decode_value(self, data: Any) -> None:
        if isinstance(data, str):
            self._defined = True
            self._text = data
            self._link = data
        else:
            raise InvalidData(f"expected string for hyperlink, got {type(data).__name__}")

    def __repr__(self) -> str:
        if self._defined:
            return f"NHyperlink({self._text!r}, {self._link!r})"
        return "NHyperlink(<undefined>)"


# ---------------------------------------------------------------------------
# Array types -- wraps list[T] for primitive types
# ---------------------------------------------------------------------------

class NArrayOfString:
    """Optional array of strings."""

    __slots__ = ("_defined", "_inner")

    def __init__(self) -> None:
        self._defined: bool = False
        self._inner: list[str] = []

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> list[str]:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._inner

    @value.setter
    def value(self, v: list[str]) -> None:
        if not isinstance(v, list):
            raise InvalidType(f"NArrayOfString.value requires list, got {type(v).__name__}")
        self._defined = True
        self._inner = list(v)  # copy to preserve value semantics

    def get(self, default: list[str] | _UndefinedType = UNDEFINED) -> list[str] | _UndefinedType:
        return self._inner if self._defined else default

    def append(self, v: str) -> None:
        self._defined = True
        self._inner.append(v)

    def set_to_default(self) -> None:
        self._defined = True
        self._inner = []

    def set_to_zero(self) -> None:
        self._defined = False
        self._inner = []

    def clone(self) -> NArrayOfString:
        o = NArrayOfString()
        o._defined = self._defined
        o._inner = list(self._inner)
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if not self._defined:
            return
        engine.open_array(name)
        for v in self._inner:
            engine.write_string(None, v)
        engine.close_array()

    def decode_value(self, data: Any) -> None:
        if isinstance(data, list):
            self._defined = True
            self._inner = []
            for item in data:
                if isinstance(item, str):
                    self._inner.append(item)
                else:
                    raise InvalidData(f"expected string in array, got {type(item).__name__}")
        else:
            raise InvalidData(f"expected array, got {type(data).__name__}")

    def __repr__(self) -> str:
        if self._defined:
            return f"NArrayOfString({self._inner!r})"
        return "NArrayOfString(<undefined>)"


class NArrayOfInt:
    """Optional array of integers."""

    __slots__ = ("_defined", "_inner")

    def __init__(self) -> None:
        self._defined: bool = False
        self._inner: list[int] = []

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> list[int]:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._inner

    @value.setter
    def value(self, v: list[int]) -> None:
        if not isinstance(v, list):
            raise InvalidType(f"NArrayOfInt.value requires list, got {type(v).__name__}")
        self._defined = True
        self._inner = list(v)

    def get(self, default: list[int] | _UndefinedType = UNDEFINED) -> list[int] | _UndefinedType:
        return self._inner if self._defined else default

    def append(self, v: int) -> None:
        self._defined = True
        self._inner.append(v)

    def set_to_default(self) -> None:
        self._defined = True
        self._inner = []

    def set_to_zero(self) -> None:
        self._defined = False
        self._inner = []

    def clone(self) -> NArrayOfInt:
        o = NArrayOfInt()
        o._defined = self._defined
        o._inner = list(self._inner)
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if not self._defined:
            return
        engine.open_array(name)
        for v in self._inner:
            engine.write_int(None, v)
        engine.close_array()

    def decode_value(self, data: Any) -> None:
        if isinstance(data, list):
            self._defined = True
            self._inner = []
            for item in data:
                if isinstance(item, int) and not isinstance(item, bool):
                    self._inner.append(item)
                elif isinstance(item, float):
                    self._inner.append(int(item))
                else:
                    raise InvalidData(f"expected int in array, got {type(item).__name__}")
        else:
            raise InvalidData(f"expected array, got {type(data).__name__}")

    def __repr__(self) -> str:
        if self._defined:
            return f"NArrayOfInt({self._inner!r})"
        return "NArrayOfInt(<undefined>)"


class NArrayOfFloat:
    """Optional array of floats."""

    __slots__ = ("_defined", "_inner")

    def __init__(self) -> None:
        self._defined: bool = False
        self._inner: list[float] = []

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> list[float]:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._inner

    @value.setter
    def value(self, v: list[float]) -> None:
        if not isinstance(v, list):
            raise InvalidType(f"NArrayOfFloat.value requires list, got {type(v).__name__}")
        self._defined = True
        self._inner = list(v)

    def get(self, default: list[float] | _UndefinedType = UNDEFINED) -> list[float] | _UndefinedType:
        return self._inner if self._defined else default

    def append(self, v: float) -> None:
        self._defined = True
        self._inner.append(v)

    def set_to_default(self) -> None:
        self._defined = True
        self._inner = []

    def set_to_zero(self) -> None:
        self._defined = False
        self._inner = []

    def clone(self) -> NArrayOfFloat:
        o = NArrayOfFloat()
        o._defined = self._defined
        o._inner = list(self._inner)
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if not self._defined:
            return
        engine.open_array(name)
        for v in self._inner:
            engine.write_float(None, v)
        engine.close_array()

    def decode_value(self, data: Any) -> None:
        if isinstance(data, list):
            self._defined = True
            self._inner = []
            for item in data:
                if isinstance(item, (int, float)) and not isinstance(item, bool):
                    self._inner.append(float(item))
                else:
                    raise InvalidData(f"expected float in array, got {type(item).__name__}")
        else:
            raise InvalidData(f"expected array, got {type(data).__name__}")

    def __repr__(self) -> str:
        if self._defined:
            return f"NArrayOfFloat({self._inner!r})"
        return "NArrayOfFloat(<undefined>)"


class NArrayOfBool:
    """Optional array of booleans."""

    __slots__ = ("_defined", "_inner")

    def __init__(self) -> None:
        self._defined: bool = False
        self._inner: list[bool] = []

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> list[bool]:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._inner

    @value.setter
    def value(self, v: list[bool]) -> None:
        if not isinstance(v, list):
            raise InvalidType(f"NArrayOfBool.value requires list, got {type(v).__name__}")
        self._defined = True
        self._inner = list(v)

    def get(self, default: list[bool] | _UndefinedType = UNDEFINED) -> list[bool] | _UndefinedType:
        return self._inner if self._defined else default

    def append(self, v: bool) -> None:
        self._defined = True
        self._inner.append(v)

    def set_to_default(self) -> None:
        self._defined = True
        self._inner = []

    def set_to_zero(self) -> None:
        self._defined = False
        self._inner = []

    def clone(self) -> NArrayOfBool:
        o = NArrayOfBool()
        o._defined = self._defined
        o._inner = list(self._inner)
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if not self._defined:
            return
        engine.open_array(name)
        for v in self._inner:
            engine.write_bool(None, v)
        engine.close_array()

    def decode_value(self, data: Any) -> None:
        if isinstance(data, list):
            self._defined = True
            self._inner = []
            for item in data:
                if isinstance(item, bool):
                    self._inner.append(item)
                else:
                    raise InvalidData(f"expected bool in array, got {type(item).__name__}")
        else:
            raise InvalidData(f"expected array, got {type(data).__name__}")

    def __repr__(self) -> str:
        if self._defined:
            return f"NArrayOfBool({self._inner!r})"
        return "NArrayOfBool(<undefined>)"


class NArrayOfEnum:
    """Optional array of enum values."""

    __slots__ = ("_defined", "_inner")

    def __init__(self) -> None:
        self._defined: bool = False
        self._inner: list[EnumId] = []

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> list[EnumId]:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._inner

    @value.setter
    def value(self, v: list[EnumId]) -> None:
        if not isinstance(v, list):
            raise InvalidType(f"NArrayOfEnum.value requires list, got {type(v).__name__}")
        self._defined = True
        self._inner = list(v)

    def get(self, default: list[EnumId] | _UndefinedType = UNDEFINED) -> list[EnumId] | _UndefinedType:
        return self._inner if self._defined else default

    def append(self, v: EnumId) -> None:
        self._defined = True
        self._inner.append(v)

    def set_to_default(self) -> None:
        self._defined = True
        self._inner = []

    def set_to_zero(self) -> None:
        self._defined = False
        self._inner = []

    def clone(self) -> NArrayOfEnum:
        o = NArrayOfEnum()
        o._defined = self._defined
        o._inner = list(self._inner)  # EnumIds are singletons, shallow copy ok
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if not self._defined:
            return
        engine.open_array(name)
        for v in self._inner:
            engine.write_string(None, str(v))
        engine.close_array()

    def decode_value(self, data: Any) -> None:
        if isinstance(data, list):
            self._defined = True
            self._inner = []
            for item in data:
                if isinstance(item, str):
                    self._inner.append(EnumRegistry.auto_lookup(item))
                else:
                    raise InvalidData(f"expected string in enum array, got {type(item).__name__}")
        else:
            raise InvalidData(f"expected array, got {type(data).__name__}")

    def __repr__(self) -> str:
        if self._defined:
            return f"NArrayOfEnum({self._inner!r})"
        return "NArrayOfEnum(<undefined>)"


# ---------------------------------------------------------------------------
# NUrl -- wraps str (URL serialized as string in JSON)
# ---------------------------------------------------------------------------

class NUrl:
    """Optional URL value, serialized as a string in JSON."""

    __slots__ = ("_defined", "_inner")

    def __init__(self) -> None:
        self._defined: bool = False
        self._inner: str = ""

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> str:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._inner

    @value.setter
    def value(self, v: str) -> None:
        self._defined = True
        self._inner = v

    def get(self, default: str | _UndefinedType = UNDEFINED) -> str | _UndefinedType:
        return self._inner if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._inner = ""

    def set_to_zero(self) -> None:
        self._defined = False
        self._inner = ""

    def clone(self) -> NUrl:
        o = NUrl()
        o._defined = self._defined
        o._inner = self._inner
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if self._defined:
            # Empty URL encodes as null (IS-04 nullable URI fields)
            if self._inner == "":
                engine.write_string(name, None)
            else:
                engine.write_string(name, self._inner)

    def decode_value(self, data: Any) -> None:
        if isinstance(data, str):
            self._defined = True
            self._inner = data
        elif data is None:
            self._defined = True
            self._inner = ""
        elif data is None:
            pass
        else:
            raise InvalidData(f"expected string for URL, got {type(data).__name__}")

    def __repr__(self) -> str:
        if self._defined:
            return f"NUrl({self._inner!r})"
        return "NUrl(<undefined>)"


# ---------------------------------------------------------------------------
# NTime -- wraps str (NMOS timestamp as "seconds:nanoseconds" string)
# ---------------------------------------------------------------------------

class NTime:
    """Optional NMOS timestamp.

    Behavior:
    - Internally stores a UTC datetime
    - JSON wire format is TAI "seconds:nanoseconds" string
    - Encode: UTC → TAI, then format as "sec:nsec"
    - Decode: parse "sec:nsec", TAI → UTC, then store

    TAI offset: 37 seconds (as of 1 January 2017).
    This constant is hardcoded — no leap second table.
    """

    __slots__ = ("_defined", "_sec", "_nsec")

    # TAI-UTC offset in seconds (TAI_UTC_Offset = 37 seconds)
    TAI_UTC_OFFSET: int = 37

    # Validation regex: "seconds:nanoseconds"
    _VALID_TAI = _re.compile(r"^([0-9]+):([0-9]+)$")

    def __init__(self) -> None:
        self._defined: bool = False
        self._sec: int = 0    # UTC seconds (Unix epoch)
        self._nsec: int = 0   # UTC nanoseconds within the second

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> tuple[int, int]:
        """Return (utc_seconds, utc_nanoseconds) — the internal UTC time."""
        if not self._defined:
            raise NotAvailable("undefined value")
        return (self._sec, self._nsec)

    @value.setter
    def value(self, v: tuple[int, int]) -> None:
        """Set from (utc_seconds, utc_nanoseconds)."""
        self._defined = True
        self._sec, self._nsec = v

    def get(self, default: Any = UNDEFINED) -> Any:
        return (self._sec, self._nsec) if self._defined else default

    def now(self) -> None:
        """Set to current UTC time."""
        import time
        t = time.time_ns()
        self._defined = True
        self._sec = t // 1_000_000_000
        self._nsec = t % 1_000_000_000

    def set_to_default(self) -> None:
        self._defined = True
        self._sec = 0
        self._nsec = 0

    def set_to_zero(self) -> None:
        self._defined = False
        self._sec = 0
        self._nsec = 0

    def clone(self) -> NTime:
        o = NTime()
        o._defined = self._defined
        o._sec = self._sec
        o._nsec = self._nsec
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        """Encode as TAI "sec:nsec" string."""
        if self._defined:
            # UTC → TAI: add offset
            tai_sec = self._sec + self.TAI_UTC_OFFSET
            tai_nsec = self._nsec
            engine.write_string(name, f"{tai_sec}:{tai_nsec}")

    def decode_value(self, data: Any) -> None:
        """Decode from TAI "sec:nsec" string."""
        if not isinstance(data, str):
            raise InvalidData(f"expected string for time, got {type(data).__name__}")
        m = self._VALID_TAI.match(data)
        if not m:
            raise InvalidData(f"invalid TAI timestamp: {data!r}")
        tai_sec = int(m.group(1))
        tai_nsec = int(m.group(2))
        # TAI → UTC: subtract offset
        self._defined = True
        self._sec = tai_sec - self.TAI_UTC_OFFSET
        self._nsec = tai_nsec

    def __repr__(self) -> str:
        if self._defined:
            tai_sec = self._sec + self.TAI_UTC_OFFSET
            return f"NTime(tai={tai_sec}:{self._nsec}, utc_sec={self._sec})"
        return "NTime(<undefined>)"


# ---------------------------------------------------------------------------
# NTags -- wraps dict[str, list[str]] (NMOS tags)
# ---------------------------------------------------------------------------

class NTags:
    """Optional NMOS tags — a mapping of string keys to lists of string values."""

    __slots__ = ("_defined", "_inner")

    def __init__(self) -> None:
        self._defined: bool = False
        self._inner: dict[str, list[str]] = {}

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> dict[str, list[str]]:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._inner

    @value.setter
    def value(self, v: dict[str, list[str]]) -> None:
        self._defined = True
        self._inner = v

    def get(self, default: dict[str, list[str]] | _UndefinedType = UNDEFINED) -> dict[str, list[str]] | _UndefinedType:
        return self._inner if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._inner = {}

    def set_to_zero(self) -> None:
        self._defined = False
        self._inner = {}

    def clone(self) -> NTags:
        o = NTags()
        o._defined = self._defined
        o._inner = {k: list(v) for k, v in self._inner.items()}
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if not self._defined:
            return
        # Use engine's structured methods for proper HTML decoration
        engine.open_struct(name)
        for tag_key, tag_values in self._inner.items():
            tag_enum = EnumRegistry.get(tag_key)
            key_id = tag_enum if tag_enum is not None else EnumId(tag_key)
            engine.open_array(key_id)
            for val in tag_values:
                engine.write_string(None, val)
            engine.close_array()
        engine.close_struct()

    def decode_value(self, data: Any) -> None:
        if isinstance(data, dict):
            self._defined = True
            self._inner = {}
            for k, v in data.items():
                if isinstance(v, list):
                    self._inner[str(k)] = [str(item) for item in v]
                else:
                    self._inner[str(k)] = []
        else:
            raise InvalidData(f"expected dict for tags, got {type(data).__name__}")

    def __repr__(self) -> str:
        if self._defined:
            return f"NTags({self._inner!r})"
        return "NTags(<undefined>)"


# ---------------------------------------------------------------------------
# NGeneric -- wraps Any (generic JSON value)
# ---------------------------------------------------------------------------

class RawJson(str):
    """JSON source text to be emitted verbatim rather than re-encoded.

    A ``str`` subclass so it can be carried anywhere a value is expected, but
    ``NGeneric.encode`` tests for it *before* the ``str`` branch and writes it
    through ``JsonEngine.write_raw`` -- so it lands as the JSON value it already
    is, not as a quoted string containing JSON.

    This is what lets a resource body reach the WebSocket grain with the exact
    spelling it arrived with. Without it the HTTP view (which serves the stored
    text) and the WebSocket view (which would re-encode a parsed dict) would
    disagree byte-for-byte on the same resource -- the very property the
    registry promises.

    The contents are trusted to be well-formed JSON: they are written into the
    output stream unexamined. Only construct this from text that has already
    parsed successfully -- in this project, from ``nmos.json.spans.member_text``
    over a document the decoder accepted.
    """

    __slots__ = ()


class NGeneric:
    """Optional generic JSON value."""

    __slots__ = ("_defined", "_inner")

    def __init__(self) -> None:
        self._defined: bool = False
        self._inner: Any = None

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> Any:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._inner

    @value.setter
    def value(self, v: Any) -> None:
        self._defined = True
        self._inner = v

    def get(self, default: Any = UNDEFINED) -> Any:
        return self._inner if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._inner = None

    def set_to_zero(self) -> None:
        self._defined = False
        self._inner = None

    def clone(self) -> NGeneric:
        import copy
        o = NGeneric()
        o._defined = self._defined
        o._inner = copy.deepcopy(self._inner)
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if not self._defined:
            return
        if self._inner is None:
            engine.write_null(name)
        elif isinstance(self._inner, RawJson):
            # Tested before the ``str`` branch below: RawJson IS a str, and
            # quoting it would turn a JSON object into a string containing one.
            engine.write_raw(name, self._inner)
        elif isinstance(self._inner, bool):
            engine.write_bool(name, self._inner)
        elif isinstance(self._inner, int):
            engine.write_int(name, self._inner)
        elif isinstance(self._inner, float):
            engine.write_float(name, self._inner)
        elif isinstance(self._inner, str):
            engine.write_string(name, self._inner)
        elif isinstance(self._inner, dict):
            _encode_dict(engine, name, self._inner)
        elif isinstance(self._inner, list):
            _encode_list(engine, name, self._inner)

    def decode_value(self, data: Any) -> None:
        self._defined = True
        self._inner = data

    def __repr__(self) -> str:
        if self._defined:
            return f"NGeneric({self._inner!r})"
        return "NGeneric(<undefined>)"


# ---------------------------------------------------------------------------
# Remaining array types for completeness
# ---------------------------------------------------------------------------

class NArrayOfNull(NNull):
    """Alias - NArrayOfNull behaves like NNull holding a list."""

    def clone(self) -> NArrayOfNull:
        o = NArrayOfNull()
        o._defined = self._defined
        o._inner = self._inner
        return o


class NArrayOfNullString(NNullString):
    """Alias - NArrayOfNullString behaves like NNullString holding a list."""

    def clone(self) -> NArrayOfNullString:
        o = NArrayOfNullString()
        o._defined = self._defined
        o._inner = self._inner
        return o


class NArrayOfHyperlink:
    """Optional array of hyperlinks."""

    __slots__ = ("_defined", "_inner")

    def __init__(self) -> None:
        self._defined: bool = False
        self._inner: list[tuple[str, str]] = []

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> list[tuple[str, str]]:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._inner

    @value.setter
    def value(self, v: list[tuple[str, str]]) -> None:
        self._defined = True
        self._inner = v

    def get(self, default: list[tuple[str, str]] | _UndefinedType = UNDEFINED) -> list[tuple[str, str]] | _UndefinedType:
        return self._inner if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._inner = []

    def set_to_zero(self) -> None:
        self._defined = False
        self._inner = []

    def clone(self) -> NArrayOfHyperlink:
        o = NArrayOfHyperlink()
        o._defined = self._defined
        o._inner = list(self._inner)
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if not self._defined:
            return
        engine.open_array(name)
        for text, link in self._inner:
            engine.write_hyperlink(None, text, link)
        engine.close_array()

    def decode_value(self, data: Any) -> None:
        if isinstance(data, list):
            self._defined = True
            self._inner = [(str(item), str(item)) for item in data]
        else:
            raise InvalidData(f"expected array, got {type(data).__name__}")

    def __repr__(self) -> str:
        if self._defined:
            return f"NArrayOfHyperlink({self._inner!r})"
        return "NArrayOfHyperlink(<undefined>)"


class NArrayOfUrl(NArrayOfString):
    """Array of URLs (serialized as array of strings)."""
    pass


class NArrayOfTime(NArrayOfString):
    """Array of NMOS timestamps (serialized as array of strings)."""
    pass


class NArrayOfGeneric:
    """Optional array of generic JSON values."""

    __slots__ = ("_defined", "_inner")

    def __init__(self) -> None:
        self._defined: bool = False
        self._inner: list[Any] = []

    @property
    def defined(self) -> bool:
        return self._defined

    @property
    def value(self) -> list[Any]:
        if not self._defined:
            raise NotAvailable("undefined value")
        return self._inner

    @value.setter
    def value(self, v: list[Any]) -> None:
        self._defined = True
        self._inner = v

    def get(self, default: list[Any] | _UndefinedType = UNDEFINED) -> list[Any] | _UndefinedType:
        return self._inner if self._defined else default

    def set_to_default(self) -> None:
        self._defined = True
        self._inner = []

    def set_to_zero(self) -> None:
        self._defined = False
        self._inner = []

    def clone(self) -> NArrayOfGeneric:
        import copy
        o = NArrayOfGeneric()
        o._defined = self._defined
        o._inner = copy.deepcopy(self._inner)
        return o

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None:
        if not self._defined:
            return
        _encode_list(engine, name, self._inner)

    def decode_value(self, data: Any) -> None:
        if isinstance(data, list):
            self._defined = True
            self._inner = list(data)
        else:
            raise InvalidData(f"expected array, got {type(data).__name__}")

    def __repr__(self) -> str:
        if self._defined:
            return f"NArrayOfGeneric({self._inner!r})"
        return "NArrayOfGeneric(<undefined>)"


# ---------------------------------------------------------------------------
# Factory functions
#
# Each make_* helper creates a defined wrapper with the given inner value
# already set.
# ---------------------------------------------------------------------------

def make_nstring(v: str) -> NString:
    """Create a defined NString with the given value."""
    o = NString()
    o.value = v
    return o


def make_nint(v: int) -> NInt:
    """Create a defined NInt with the given value."""
    o = NInt()
    o.value = v
    return o


def make_nfloat(v: float) -> NFloat:
    """Create a defined NFloat with the given value."""
    o = NFloat()
    o.value = v
    return o


def make_nbool(v: bool) -> NBool:
    """Create a defined NBool with the given value."""
    o = NBool()
    o.value = v
    return o


def make_nenum(v: EnumId) -> NEnum:
    """Create a defined NEnum with the given value."""
    o = NEnum()
    o.value = v
    return o


def make_nnull(v: Any) -> NNull:
    """Create a defined NNull with the given value (None, int, float, str, bool)."""
    o = NNull()
    o.value = v
    return o


def make_nnullstring(v: str | None) -> NNullString:
    """Create a defined NNullString with the given value."""
    o = NNullString()
    o.value = v
    return o


def make_nhyperlink(text: str, link: str) -> NHyperlink:
    """Create a defined NHyperlink with the given display text and URL."""
    o = NHyperlink()
    o.value = (text, link)
    return o


def make_narrayofstring(v: list[str]) -> NArrayOfString:
    """Create a defined NArrayOfString with the given values."""
    o = NArrayOfString()
    o.value = v
    return o


def make_narrayofint(v: list[int]) -> NArrayOfInt:
    """Create a defined NArrayOfInt with the given values."""
    o = NArrayOfInt()
    o.value = v
    return o


def make_narrayoffloat(v: list[float]) -> NArrayOfFloat:
    """Create a defined NArrayOfFloat with the given values."""
    o = NArrayOfFloat()
    o.value = v
    return o


def make_narrayofbool(v: list[bool]) -> NArrayOfBool:
    """Create a defined NArrayOfBool with the given values."""
    o = NArrayOfBool()
    o.value = v
    return o


def make_narrayofenum(v: list[EnumId]) -> NArrayOfEnum:
    """Create a defined NArrayOfEnum with the given values."""
    o = NArrayOfEnum()
    o.value = v
    return o
