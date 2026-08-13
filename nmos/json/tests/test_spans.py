# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for member_text -- the byte-fidelity primitive.

Everything downstream trusts this to return a span that is (a) exactly what the
sender wrote and (b) parseable back to the same value the enclosing document
parsed to. Both properties are asserted here rather than assumed by callers.
"""

from __future__ import annotations

import json

import pytest

from nmos.json.spans import JsonSpanError, member_text


class TestExactSpans:
    @pytest.mark.parametrize(
        "source,expected",
        [
            ('{"data": {"a": 1}}', '{"a": 1}'),
            ('{"data":{"a":1}}', '{"a":1}'),
            ('{ "data" : { "a" : 1 } }', '{ "a" : 1 }'),
            ('{"a": 1, "data": [1, 2], "b": 2}', "[1, 2]"),
            ('{"data": "x"}', '"x"'),
            ('{"data": null}', "null"),
            ('{"data": true}', "true"),
            ('{"data": -0.0}', "-0.0"),
        ],
    )
    def test_span_is_verbatim(self, source: str, expected: str) -> None:
        assert member_text(source, "data") == expected

    @pytest.mark.parametrize(
        "spelling",
        ["1e3", "1.500", "1E+3", "0.1", "10000000000000000000000", "-0"],
    )
    def test_number_spelling_survives(self, spelling: str) -> None:
        """The whole point: a parse would normalise these, a span does not."""
        source = '{"data": {"x": ' + spelling + "}}"
        assert member_text(source, "data") == '{"x": ' + spelling + "}"

    @pytest.mark.parametrize(
        "spelling",
        [r'"café"', '"café"', r'"a\/b"', '"a/b"', r'"tab\there"'],
    )
    def test_string_escaping_survives(self, spelling: str) -> None:
        source = '{"data": {"s": ' + spelling + "}}"
        assert member_text(source, "data") == '{"s": ' + spelling + "}"

    def test_key_order_and_whitespace_survive(self) -> None:
        source = '{"data": {"b":1,\n  "a":  2}}'
        assert member_text(source, "data") == '{"b":1,\n  "a":  2}'


class TestSelectionRules:
    def test_absent_member_is_none(self) -> None:
        assert member_text('{"type": "node"}', "data") is None

    def test_empty_object_is_none(self) -> None:
        assert member_text("{}", "data") is None

    def test_nested_member_of_the_same_name_is_not_matched(self) -> None:
        """``raw_decode`` consumes each value whole, so nesting cannot fool it."""
        source = '{"type": {"data": "inner"}, "data": "outer"}'
        assert member_text(source, "data") == '"outer"'

    def test_the_name_appearing_as_a_string_value_is_not_matched(self) -> None:
        source = '{"type": "data", "data": 7}'
        assert member_text(source, "data") == "7"

    def test_a_key_containing_the_name_is_not_matched(self) -> None:
        source = '{"metadata": 1, "data_x": 2, "data": 3}'
        assert member_text(source, "data") == "3"

    def test_duplicate_keys_take_the_last_like_json_loads(self) -> None:
        """The span must describe the value the parsed form actually holds."""
        source = '{"data": 1, "data": 2}'
        assert member_text(source, "data") == "2"
        assert json.loads(source)["data"] == 2

    def test_escaped_key_is_matched_by_its_decoded_name(self) -> None:
        source = r'{"data": 5}'
        assert member_text(source, "data") == "5"


class TestRoundTrip:
    @pytest.mark.parametrize(
        "body",
        [
            {"id": "x", "nested": {"a": [1, 2, {"b": None}]}},
            {"unicode": "café ✓", "empty": {}, "list": []},
            {"n": 1.5, "big": 10**25, "neg": -3},
        ],
    )
    def test_span_parses_back_to_the_same_value(self, body: dict) -> None:
        source = json.dumps({"type": "node", "data": body})
        span = member_text(source, "data")
        assert span is not None
        assert json.loads(span) == body


class TestMalformed:
    @pytest.mark.parametrize(
        "source",
        ['["not", "an", "object"]', '"a string"', "", "   ", "42"],
    )
    def test_non_object_is_an_error(self, source: str) -> None:
        with pytest.raises(JsonSpanError):
            member_text(source, "data")

    @pytest.mark.parametrize(
        "source",
        ['{"data": 1', '{"data" 1}', '{"data": }', '{data: 1}', '{"data": 1,'],
    )
    def test_malformed_object_is_an_error(self, source: str) -> None:
        with pytest.raises(JsonSpanError):
            member_text(source, "data")
