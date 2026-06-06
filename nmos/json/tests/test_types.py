# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for nmos.json.types -- base value types."""

from __future__ import annotations

import json as stdlib_json

import pytest

from nmos.enums import EnumRegistry
from nmos.errors import InvalidData, NotAvailable
from nmos.json.engine import JsonEngine
from nmos.json.types import (
    UNDEFINED,
    NArrayOfBool,
    NArrayOfEnum,
    NArrayOfFloat,
    NArrayOfInt,
    NArrayOfString,
    NBool,
    NEnum,
    NFloat,
    NHyperlink,
    NInt,
    NNull,
    NNullString,
    NString,
    make_narrayofint,
    make_narrayofstring,
    make_nbool,
    make_nenum,
    make_nfloat,
    make_nhyperlink,
    make_nint,
    make_nnull,
    make_nnullstring,
    make_nstring,
)


@pytest.fixture(autouse=True)
def _clean_registry() -> None:  # type: ignore[misc]
    """Save and restore enum registry around each test."""
    saved = dict(EnumRegistry._entries)
    yield  # type: ignore[misc]
    EnumRegistry._entries.clear()
    EnumRegistry._entries.update(saved)


def _encode_in_struct(field_name: str, obj: object) -> dict[str, object]:
    """Helper: encode a single field inside a JSON object and parse result."""
    engine = JsonEngine()
    engine.reset()
    name = EnumRegistry.get(field_name)
    engine.open_struct(None)
    obj.encode(engine, name)  # type: ignore[attr-defined]
    engine.close_struct()
    return stdlib_json.loads(engine.get_output())  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# NString
# ---------------------------------------------------------------------------

class TestNString:
    def test_undefined_by_default(self) -> None:
        s = NString()
        assert not s.defined
        assert s.get() is UNDEFINED
        assert s.get("fallback") == "fallback"

    def test_value_raises_when_undefined(self) -> None:
        s = NString()
        with pytest.raises(NotAvailable):
            _ = s.value

    def test_set_and_get(self) -> None:
        s = NString()
        s.value = "hello"
        assert s.defined
        assert s.value == "hello"
        assert s.get() == "hello"

    def test_set_to_default(self) -> None:
        s = NString()
        s.set_to_default()
        assert s.defined
        assert s.value == ""

    def test_clone(self) -> None:
        s = NString()
        s.value = "original"
        c = s.clone()
        assert c.value == "original"
        c.value = "modified"
        assert s.value == "original"

    def test_encode_undefined_produces_nothing(self) -> None:
        result = _encode_in_struct("label", NString())
        assert result == {}

    def test_encode_defined(self) -> None:
        s = NString()
        s.value = "test"
        result = _encode_in_struct("label", s)
        assert result == {"label": "test"}

    def test_decode_string(self) -> None:
        s = NString()
        s.decode_value("hello")
        assert s.defined
        assert s.value == "hello"

    def test_decode_invalid_type(self) -> None:
        s = NString()
        with pytest.raises(InvalidData):
            s.decode_value(42)

    def test_repr(self) -> None:
        s = NString()
        assert "undefined" in repr(s)
        s.value = "x"
        assert "x" in repr(s)


# ---------------------------------------------------------------------------
# NInt
# ---------------------------------------------------------------------------

class TestNInt:
    def test_undefined_by_default(self) -> None:
        n = NInt()
        assert not n.defined
        with pytest.raises(NotAvailable):
            _ = n.value

    def test_set_and_get(self) -> None:
        n = NInt()
        n.value = 42
        assert n.value == 42
        assert n.get() == 42

    def test_encode_decode_roundtrip(self) -> None:
        n = NInt()
        n.value = -100
        result = _encode_in_struct("count", n)
        assert result == {"count": -100}

        n2 = NInt()
        n2.decode_value(result["count"])
        assert n2.value == -100

    def test_decode_float_truncates(self) -> None:
        n = NInt()
        n.decode_value(3.7)
        assert n.value == 3

    def test_clone(self) -> None:
        n = NInt()
        n.value = 99
        c = n.clone()
        c.value = 0
        assert n.value == 99


# ---------------------------------------------------------------------------
# NFloat
# ---------------------------------------------------------------------------

class TestNFloat:
    def test_set_and_get(self) -> None:
        f = NFloat()
        f.value = 3.14
        assert f.value == pytest.approx(3.14)

    def test_encode_decode_roundtrip(self) -> None:
        f = NFloat()
        f.value = 29.97
        result = _encode_in_struct("rate", f)
        assert result["rate"] == pytest.approx(29.97)

        f2 = NFloat()
        f2.decode_value(result["rate"])
        assert f2.value == pytest.approx(29.97)

    def test_decode_int_to_float(self) -> None:
        f = NFloat()
        f.decode_value(42)
        assert f.value == 42.0


# ---------------------------------------------------------------------------
# NBool
# ---------------------------------------------------------------------------

class TestNBool:
    def test_set_and_get(self) -> None:
        b = NBool()
        b.value = True
        assert b.value is True

    def test_encode_decode_roundtrip(self) -> None:
        b = NBool()
        b.value = False
        result = _encode_in_struct("enabled", b)
        assert result == {"enabled": False}

        b2 = NBool()
        b2.decode_value(result["enabled"])
        assert b2.value is False

    def test_decode_invalid(self) -> None:
        b = NBool()
        with pytest.raises(InvalidData):
            b.decode_value(1)


# ---------------------------------------------------------------------------
# NEnum
# ---------------------------------------------------------------------------

class TestNEnum:
    def test_set_and_get(self) -> None:
        e = NEnum()
        fmt = EnumRegistry.get("urn:x-nmos:format:video")
        e.value = fmt
        assert e.value is fmt

    def test_encode_as_string(self) -> None:
        e = NEnum()
        e.value = EnumRegistry.get("urn:x-nmos:format:video")
        result = _encode_in_struct("format", e)
        assert result == {"format": "urn:x-nmos:format:video"}

    def test_decode_from_string(self) -> None:
        e = NEnum()
        e.decode_value("urn:x-nmos:format:audio")
        assert e.defined
        assert str(e.value) == "urn:x-nmos:format:audio"
        # Should be the canonical EnumId
        assert e.value is EnumRegistry.get("urn:x-nmos:format:audio")

    def test_clone(self) -> None:
        e = NEnum()
        e.value = EnumRegistry.get("test")
        c = e.clone()
        assert c.value is e.value  # same EnumId singleton


# ---------------------------------------------------------------------------
# NNull
# ---------------------------------------------------------------------------

class TestNNull:
    def test_null_value(self) -> None:
        n = NNull()
        n.value = None
        assert n.defined
        assert n.value is None

    def test_int_value(self) -> None:
        n = NNull()
        n.value = 42
        assert n.value == 42

    def test_string_value(self) -> None:
        n = NNull()
        n.value = "hello"
        assert n.value == "hello"

    def test_encode_null(self) -> None:
        n = NNull()
        n.value = None
        result = _encode_in_struct("data", n)
        assert result == {"data": None}

    def test_encode_int(self) -> None:
        n = NNull()
        n.value = 42
        result = _encode_in_struct("data", n)
        assert result == {"data": 42}

    def test_decode_various(self) -> None:
        for val in [None, 42, 3.14, "hello", True]:
            n = NNull()
            n.decode_value(val)
            assert n.defined
            assert n.value == val


# ---------------------------------------------------------------------------
# NNullString
# ---------------------------------------------------------------------------

class TestNNullString:
    def test_null_vs_string(self) -> None:
        ns = NNullString()
        ns.value = None
        assert ns.defined
        assert ns.value is None

        ns.value = "hello"
        assert ns.value == "hello"

    def test_encode_null(self) -> None:
        ns = NNullString()
        ns.value = None
        result = _encode_in_struct("opt", ns)
        assert result == {"opt": None}

    def test_encode_string(self) -> None:
        ns = NNullString()
        ns.value = "test"
        result = _encode_in_struct("opt", ns)
        assert result == {"opt": "test"}


# ---------------------------------------------------------------------------
# Array types
# ---------------------------------------------------------------------------

class TestNArrayOfString:
    def test_set_and_get(self) -> None:
        a = NArrayOfString()
        a.value = ["a", "b", "c"]
        assert a.value == ["a", "b", "c"]

    def test_append(self) -> None:
        a = NArrayOfString()
        a.append("x")
        assert a.defined
        assert a.value == ["x"]

    def test_encode_decode_roundtrip(self) -> None:
        a = NArrayOfString()
        a.value = ["hello", "world"]
        result = _encode_in_struct("tags", a)
        assert result == {"tags": ["hello", "world"]}

        a2 = NArrayOfString()
        a2.decode_value(result["tags"])
        assert a2.value == ["hello", "world"]

    def test_clone_is_deep(self) -> None:
        a = NArrayOfString()
        a.value = ["a", "b"]
        c = a.clone()
        c.value.append("c")
        assert a.value == ["a", "b"]


class TestNArrayOfInt:
    def test_encode_decode_roundtrip(self) -> None:
        a = NArrayOfInt()
        a.value = [1, 2, 3]
        result = _encode_in_struct("numbers", a)
        assert result == {"numbers": [1, 2, 3]}

        a2 = NArrayOfInt()
        a2.decode_value(result["numbers"])
        assert a2.value == [1, 2, 3]


class TestNArrayOfFloat:
    def test_encode_decode_roundtrip(self) -> None:
        a = NArrayOfFloat()
        a.value = [1.1, 2.2]
        result = _encode_in_struct("values", a)

        a2 = NArrayOfFloat()
        a2.decode_value(result["values"])
        assert a2.value[0] == pytest.approx(1.1)
        assert a2.value[1] == pytest.approx(2.2)


class TestNArrayOfBool:
    def test_encode_decode_roundtrip(self) -> None:
        a = NArrayOfBool()
        a.value = [True, False, True]
        result = _encode_in_struct("flags", a)
        assert result == {"flags": [True, False, True]}

        a2 = NArrayOfBool()
        a2.decode_value(result["flags"])
        assert a2.value == [True, False, True]


class TestNArrayOfEnum:
    def test_encode_decode_roundtrip(self) -> None:
        a = NArrayOfEnum()
        a.value = [
            EnumRegistry.get("urn:x-nmos:format:video"),
            EnumRegistry.get("urn:x-nmos:format:audio"),
        ]
        result = _encode_in_struct("formats", a)
        assert result == {"formats": ["urn:x-nmos:format:video", "urn:x-nmos:format:audio"]}

        a2 = NArrayOfEnum()
        a2.decode_value(result["formats"])
        assert len(a2.value) == 2
        assert a2.value[0] is EnumRegistry.get("urn:x-nmos:format:video")
        assert a2.value[1] is EnumRegistry.get("urn:x-nmos:format:audio")


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------

class TestFactories:
    """make_* factory functions create defined wrappers."""

    def test_make_nstring(self) -> None:
        s = make_nstring("hello")
        assert s.defined
        assert s.value == "hello"

    def test_make_nint(self) -> None:
        n = make_nint(42)
        assert n.defined
        assert n.value == 42

    def test_make_nfloat(self) -> None:
        f = make_nfloat(3.14)
        assert f.defined
        assert f.value == pytest.approx(3.14)

    def test_make_nbool(self) -> None:
        b = make_nbool(True)
        assert b.defined
        assert b.value is True

    def test_make_nenum(self) -> None:
        fmt = EnumRegistry.get("video")
        e = make_nenum(fmt)
        assert e.defined
        assert e.value is fmt

    def test_make_nnull(self) -> None:
        n = make_nnull(None)
        assert n.defined
        assert n.value is None

    def test_make_nnullstring(self) -> None:
        ns = make_nnullstring("test")
        assert ns.defined
        assert ns.value == "test"

    def test_make_nhyperlink(self) -> None:
        h = make_nhyperlink("click", "http://example.com")
        assert h.defined
        assert h.value == ("click", "http://example.com")

    def test_make_narrayofstring(self) -> None:
        a = make_narrayofstring(["a", "b"])
        assert a.defined
        assert a.value == ["a", "b"]

    def test_make_narrayofint(self) -> None:
        a = make_narrayofint([1, 2, 3])
        assert a.defined
        assert a.value == [1, 2, 3]

    def test_factory_encode_roundtrip(self) -> None:
        s = make_nstring("test")
        result = _encode_in_struct("label", s)
        assert result == {"label": "test"}
