# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for nmos.json.engine -- JSON encoder and decoder."""

from __future__ import annotations

import json as stdlib_json
from typing import Any

import pytest

from nmos.enums import EnumId, EnumRegistry
from nmos.json.engine import JsonEngine


@pytest.fixture(autouse=True)
def _clean_registry() -> None:  # type: ignore[misc]
    """Save and restore enum registry around each test."""
    saved = dict(EnumRegistry._entries)
    yield  # type: ignore[misc]
    EnumRegistry._entries.clear()
    EnumRegistry._entries.update(saved)


# ---------------------------------------------------------------------------
# Encoder tests
# ---------------------------------------------------------------------------


class TestEncoderPrimitives:
    """Write individual primitive values."""

    def test_write_string(self) -> None:
        engine = JsonEngine()
        engine.reset()
        engine.open_struct(None)
        name = EnumRegistry.get("label")
        engine.write_string(name, "hello")
        engine.close_struct()
        result = stdlib_json.loads(engine.get_output())
        assert result == {"label": "hello"}

    def test_write_string_null(self) -> None:
        engine = JsonEngine()
        engine.reset()
        engine.open_struct(None)
        name = EnumRegistry.get("value")
        engine.write_string(name, None)
        engine.close_struct()
        result = stdlib_json.loads(engine.get_output())
        assert result == {"value": None}

    def test_write_int(self) -> None:
        engine = JsonEngine()
        engine.reset()
        engine.open_struct(None)
        name = EnumRegistry.get("count")
        engine.write_int(name, 42)
        engine.close_struct()
        result = stdlib_json.loads(engine.get_output())
        assert result == {"count": 42}

    def test_write_negative_int(self) -> None:
        engine = JsonEngine()
        engine.reset()
        engine.open_struct(None)
        name = EnumRegistry.get("offset")
        engine.write_int(name, -100)
        engine.close_struct()
        result = stdlib_json.loads(engine.get_output())
        assert result == {"offset": -100}

    def test_write_float(self) -> None:
        engine = JsonEngine()
        engine.reset()
        engine.open_struct(None)
        name = EnumRegistry.get("rate")
        engine.write_float(name, 29.97)
        engine.close_struct()
        result = stdlib_json.loads(engine.get_output())
        assert result["rate"] == pytest.approx(29.97)

    def test_write_bool_true(self) -> None:
        engine = JsonEngine()
        engine.reset()
        engine.open_struct(None)
        name = EnumRegistry.get("enabled")
        engine.write_bool(name, True)
        engine.close_struct()
        result = stdlib_json.loads(engine.get_output())
        assert result == {"enabled": True}

    def test_write_bool_false(self) -> None:
        engine = JsonEngine()
        engine.reset()
        engine.open_struct(None)
        name = EnumRegistry.get("enabled")
        engine.write_bool(name, False)
        engine.close_struct()
        result = stdlib_json.loads(engine.get_output())
        assert result == {"enabled": False}

    def test_write_null(self) -> None:
        engine = JsonEngine()
        engine.reset()
        engine.open_struct(None)
        name = EnumRegistry.get("data")
        engine.write_null(name)
        engine.close_struct()
        result = stdlib_json.loads(engine.get_output())
        assert result == {"data": None}


class TestEncoderNestedObjects:
    """Encode nested structures."""

    def test_nested_struct(self) -> None:
        engine = JsonEngine()
        engine.reset()
        parent = EnumRegistry.get("parent")
        child_name = EnumRegistry.get("child")
        value = EnumRegistry.get("value")

        engine.open_struct(None)
        engine.open_struct(parent)
        engine.write_string(value, "inner")
        engine.close_struct()
        engine.write_string(child_name, "outer")
        engine.close_struct()

        result = stdlib_json.loads(engine.get_output())
        assert result == {"parent": {"value": "inner"}, "child": "outer"}

    def test_array_of_ints(self) -> None:
        engine = JsonEngine()
        engine.reset()
        items = EnumRegistry.get("items")

        engine.open_struct(None)
        engine.open_array(items)
        engine.write_int(None, 1)
        engine.write_int(None, 2)
        engine.write_int(None, 3)
        engine.close_array()
        engine.close_struct()

        result = stdlib_json.loads(engine.get_output())
        assert result == {"items": [1, 2, 3]}

    def test_array_of_strings(self) -> None:
        engine = JsonEngine()
        engine.reset()
        tags = EnumRegistry.get("tags")

        engine.open_struct(None)
        engine.open_array(tags)
        engine.write_string(None, "a")
        engine.write_string(None, "b")
        engine.close_array()
        engine.close_struct()

        result = stdlib_json.loads(engine.get_output())
        assert result == {"tags": ["a", "b"]}

    def test_array_of_objects(self) -> None:
        engine = JsonEngine()
        engine.reset()
        items = EnumRegistry.get("items")
        name = EnumRegistry.get("name")

        engine.open_struct(None)
        engine.open_array(items)
        engine.open_struct(None)
        engine.write_string(name, "first")
        engine.close_struct()
        engine.open_struct(None)
        engine.write_string(name, "second")
        engine.close_struct()
        engine.close_array()
        engine.close_struct()

        result = stdlib_json.loads(engine.get_output())
        assert result == {"items": [{"name": "first"}, {"name": "second"}]}

    def test_empty_struct(self) -> None:
        engine = JsonEngine()
        engine.reset()
        engine.open_struct(None, omit_empty=True)
        engine.close_struct()
        # Omit-empty struct that has no content should produce nothing
        output = engine.get_output()
        assert output == ""

    def test_empty_array(self) -> None:
        engine = JsonEngine()
        engine.reset()
        items = EnumRegistry.get("items")
        engine.open_struct(None)
        engine.open_array(items, omit_empty=True)
        engine.close_array()
        engine.close_struct()
        # Omit-empty array with no items produces no "items" key
        result = stdlib_json.loads(engine.get_output())
        assert result == {}

    def test_multiple_members(self) -> None:
        engine = JsonEngine()
        engine.reset()
        id_ = EnumRegistry.get("id")
        label = EnumRegistry.get("label")
        version = EnumRegistry.get("version")

        engine.open_struct(None)
        engine.write_string(id_, "abc-123")
        engine.write_string(label, "My Source")
        engine.write_string(version, "1617723456:0")
        engine.close_struct()

        result = stdlib_json.loads(engine.get_output())
        assert result == {
            "id": "abc-123",
            "label": "My Source",
            "version": "1617723456:0",
        }


class TestEncoderStringEscaping:
    """Strings are properly escaped."""

    def test_escape_quotes(self) -> None:
        engine = JsonEngine()
        engine.reset()
        engine.open_struct(None)
        engine.write_string(EnumRegistry.get("s"), 'hello "world"')
        engine.close_struct()
        result = stdlib_json.loads(engine.get_output())
        assert result["s"] == 'hello "world"'

    def test_escape_backslash(self) -> None:
        engine = JsonEngine()
        engine.reset()
        engine.open_struct(None)
        engine.write_string(EnumRegistry.get("s"), "path\\to\\file")
        engine.close_struct()
        result = stdlib_json.loads(engine.get_output())
        assert result["s"] == "path\\to\\file"

    def test_escape_newline(self) -> None:
        engine = JsonEngine()
        engine.reset()
        engine.open_struct(None)
        engine.write_string(EnumRegistry.get("s"), "line1\nline2")
        engine.close_struct()
        result = stdlib_json.loads(engine.get_output())
        assert result["s"] == "line1\nline2"

    def test_unicode(self) -> None:
        engine = JsonEngine()
        engine.reset()
        engine.open_struct(None)
        engine.write_string(EnumRegistry.get("s"), "caf\u00e9")
        engine.close_struct()
        result = stdlib_json.loads(engine.get_output())
        assert result["s"] == "caf\u00e9"


class TestEncoderIndentation:
    """Level indentation produces formatted output."""

    def test_indented_output(self) -> None:
        engine = JsonEngine()
        engine.level_indentation = 2
        engine.reset()

        engine.open_struct(None)
        engine.write_string(EnumRegistry.get("key"), "value")
        engine.close_struct()

        output = engine.get_output()
        # Should contain newlines when indentation is set
        assert "\n" in output
        result = stdlib_json.loads(output)
        assert result == {"key": "value"}


class TestEncoderHtmlMode:
    """HTML rendering mode wraps values in span tags."""

    def test_html_string(self) -> None:
        engine = JsonEngine()
        engine.generate_html = True
        engine.reset()

        engine.open_struct(None)
        engine.write_string(EnumRegistry.get("name"), "test")
        engine.close_struct()

        output = engine.get_output()
        assert '<span class="name">' in output
        assert '<span class="string">' in output

    def test_html_number(self) -> None:
        engine = JsonEngine()
        engine.generate_html = True
        engine.reset()

        engine.open_struct(None)
        engine.write_int(EnumRegistry.get("count"), 42)
        engine.close_struct()

        output = engine.get_output()
        assert '<span class="number">' in output

    def test_html_boolean(self) -> None:
        engine = JsonEngine()
        engine.generate_html = True
        engine.reset()

        engine.open_struct(None)
        engine.write_bool(EnumRegistry.get("flag"), True)
        engine.close_struct()

        output = engine.get_output()
        assert '<span class="boolean">' in output
        assert "true" in output

    def test_html_null(self) -> None:
        engine = JsonEngine()
        engine.generate_html = True
        engine.reset()

        engine.open_struct(None)
        engine.write_null(EnumRegistry.get("data"))
        engine.close_struct()

        output = engine.get_output()
        assert '<span class="null">' in output

    def test_html_object_structure(self) -> None:
        engine = JsonEngine()
        engine.generate_html = True
        engine.reset()

        engine.open_struct(None)
        engine.write_string(EnumRegistry.get("x"), "y")
        engine.close_struct()

        output = engine.get_output()
        assert '<span class="object">' in output
        assert "<ol>" in output
        assert "<li>" in output

    def test_html_hyperlink(self) -> None:
        engine = JsonEngine()
        engine.generate_html = True
        engine.reset()

        engine.open_struct(None)
        engine.write_hyperlink(EnumRegistry.get("url"), "click here", "http://example.com")
        engine.close_struct()

        output = engine.get_output()
        assert "<a href=" in output
        assert "click here" in output


# ---------------------------------------------------------------------------
# Decoder tests
# ---------------------------------------------------------------------------


class _SimpleDecoder:
    """Test decoder that stores the parsed data."""

    def __init__(self) -> None:
        self.data: Any = None

    def decode(self, engine: JsonEngine, data: Any) -> None:
        self.data = data


class TestDecoder:
    """Basic decode via json.loads."""

    def test_decode_object(self) -> None:
        engine = JsonEngine()
        dec = _SimpleDecoder()
        engine.decode(dec, '{"key": "value"}')
        assert dec.data == {"key": "value"}

    def test_decode_array(self) -> None:
        engine = JsonEngine()
        dec = _SimpleDecoder()
        engine.decode(dec, "[1, 2, 3]")
        assert dec.data == [1, 2, 3]

    def test_decode_nested(self) -> None:
        engine = JsonEngine()
        dec = _SimpleDecoder()
        engine.decode(dec, '{"a": {"b": [1, true, null]}}')
        assert dec.data == {"a": {"b": [1, True, None]}}

    def test_decode_invalid_json_raises(self) -> None:
        engine = JsonEngine()
        dec = _SimpleDecoder()
        with pytest.raises(Exception):  # noqa: B017 -- json.JSONDecodeError
            engine.decode(dec, "{invalid}")


class TestProgressiveDecode:
    """Progressive/streaming decode for concatenated JSON documents."""

    def test_decode_single_object(self) -> None:
        engine = JsonEngine()
        engine.progressive = True
        dec = _SimpleDecoder()
        consumed, ok = engine.decode_progressive(dec, '{"a": 1}')
        assert ok is True
        assert consumed > 0
        assert dec.data == {"a": 1}

    def test_decode_with_leading_whitespace(self) -> None:
        engine = JsonEngine()
        engine.progressive = True
        dec = _SimpleDecoder()
        consumed, ok = engine.decode_progressive(dec, '  \n  {"a": 1}')
        assert ok is True
        assert dec.data == {"a": 1}

    def test_decode_empty_string(self) -> None:
        engine = JsonEngine()
        engine.progressive = True
        dec = _SimpleDecoder()
        consumed, ok = engine.decode_progressive(dec, "")
        assert ok is False
        assert consumed == 0

    def test_decode_incomplete_json(self) -> None:
        engine = JsonEngine()
        engine.progressive = True
        dec = _SimpleDecoder()
        consumed, ok = engine.decode_progressive(dec, '{"a": ')
        assert ok is False

    def test_progressive_count_increments(self) -> None:
        engine = JsonEngine()
        engine.progressive = True
        engine.progressive_count = 0
        dec = _SimpleDecoder()

        engine.decode_progressive(dec, '{"a": 1}')
        assert engine.progressive_count == 1

        engine.decode_progressive(dec, '{"b": 2}')
        assert engine.progressive_count == 2

    def test_decode_two_objects_from_stream(self) -> None:
        engine = JsonEngine()
        engine.progressive = True
        dec = _SimpleDecoder()
        stream = '{"a": 1}  {"b": 2}'

        consumed1, ok1 = engine.decode_progressive(dec, stream)
        assert ok1 is True
        assert dec.data == {"a": 1}

        consumed2, ok2 = engine.decode_progressive(dec, stream[consumed1:])
        assert ok2 is True
        assert dec.data == {"b": 2}
