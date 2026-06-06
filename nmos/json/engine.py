# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""JSON encoder/decoder engine.

Encoder: implements a level-based buffered JSON writer, supporting
plain JSON and HTML-decorated output with configurable indentation.

Decoder: uses Python's json.loads() to parse into dicts/lists, then generated
decode() methods walk the dict directly (no callbacks). The engine manages
decode context for polymorphic type predicates and progressive/streaming mode.
"""

from __future__ import annotations

import html
import io
import json as stdlib_json
from typing import TYPE_CHECKING, Any, Callable, Protocol

from nmos.enums import EnumId, EnumRegistry
from nmos.errors import Full, InvalidData, InvalidObject, InvalidParameter

if TYPE_CHECKING:
    pass

MAX_NESTED_LEVELS = 256


class JsonEncoder(Protocol):
    """Protocol for types that can encode themselves to JSON."""

    def encode(self, engine: JsonEngine, name: EnumId | None) -> None: ...


class JsonDecoder(Protocol):
    """Protocol for types that can decode themselves from a parsed dict/value."""

    def decode(self, engine: JsonEngine, data: Any) -> None: ...


# ---------------------------------------------------------------------------
# Encoder level state
# ---------------------------------------------------------------------------

class _Level:
    """Per-nesting-level encoder state."""

    __slots__ = ("content", "indent", "count", "text", "array_name")

    def __init__(self) -> None:
        self.content: bool = False
        self.indent: int = 0
        self.count: int = 0
        self.text: list[str] = []
        self.array_name: str | None = None  # parent array's field name (for link resolution)


# ---------------------------------------------------------------------------
# JSON Engine
# ---------------------------------------------------------------------------

class JsonEngine:
    """JSON encoder/decoder engine.

    Encoder side: open_struct/close_struct, open_array/close_array,
    write_string/int/float/bool/null -- writes to an io.StringIO or similar.

    Decoder side: decode() parses JSON and delegates to the top-level
    decoder's decode method with the parsed dict/list.
    """

    def __init__(self) -> None:
        self.generate_html: bool = False
        self.level_indentation: int = 0
        self.progressive: bool = False
        self.progressive_count: int = 0
        self.link_resolver: Callable[[str, str], str | None] | None = None
        """Optional callback: (field_name, value) -> URL or None.
        When set and generate_html is True, string values for which the
        resolver returns a URL are rendered as clickable <a> links."""

        self._output: io.StringIO = io.StringIO()
        self._current_level: int = 0
        self._levels: list[_Level] = []

    def reset(self) -> None:
        """Reset engine state, preserving configuration."""
        saved_html = self.generate_html
        saved_indent = self.level_indentation
        saved_progressive = self.progressive
        saved_count = self.progressive_count

        self._output = io.StringIO()
        self._current_level = 0
        self._levels = [_Level() for _ in range(MAX_NESTED_LEVELS)]

        self.generate_html = saved_html
        self.level_indentation = saved_indent
        self.progressive = saved_progressive
        self.progressive_count = saved_count

    # -----------------------------------------------------------------------
    # Encoder -- public API
    # -----------------------------------------------------------------------

    def encode(self, top: JsonEncoder, name: EnumId | None = None) -> str:
        """Encode a top-level object to a JSON string."""
        self.reset()
        top.encode(self, name)
        return self._output.getvalue()

    @staticmethod
    def parse_any(data: str | bytes) -> Any:
        """Parse arbitrary JSON payload into Python values.

        Centralized JSON parsing helper used by API/middleware code that
        accepts dynamic request bodies (not generated typed decoders).
        """
        return stdlib_json.loads(data)

    @staticmethod
    def dump_any(
        data: Any,
        *,
        indent: int | None = None,
        ensure_ascii: bool = False,
        default: Any | None = None,
    ) -> str:
        """Serialize arbitrary Python values to JSON.

        Centralized JSON serialization helper used by API response code
        when data is not a generated typed encoder.
        """
        kwargs: dict[str, Any] = {
            "ensure_ascii": ensure_ascii,
        }
        if indent is not None:
            kwargs["indent"] = indent
        if default is not None:
            kwargs["default"] = default
        return stdlib_json.dumps(data, **kwargs)

    def get_output(self) -> str:
        """Get the current encoder output."""
        return self._output.getvalue()

    # -----------------------------------------------------------------------
    # Encoder -- struct/array open/close
    # -----------------------------------------------------------------------

    def open_struct(self, name: EnumId | None, omit_empty: bool = False) -> None:
        """Open a JSON object '{'. name is the property name (None for root)."""
        self._open_aggregate(name, array=False, omit_empty=omit_empty)

    def close_struct(self) -> None:
        """Close a JSON object '}'."""
        self._close_aggregate(array=False)

    def open_array(self, name: EnumId | None, omit_empty: bool = False) -> None:
        """Open a JSON array '['. name is the property name (None for root)."""
        self._open_aggregate(name, array=True, omit_empty=omit_empty)

    def close_array(self) -> None:
        """Close a JSON array ']'."""
        self._close_aggregate(array=True)

    # -----------------------------------------------------------------------
    # Encoder -- value writers
    # -----------------------------------------------------------------------

    def write_string(self, name: EnumId | None, value: str | None) -> None:
        """Write a string value (or null if value is None)."""
        self._flush()
        self._write_name(0, name)
        self._write_string_value(0, value, name)
        self._levels[self._current_level].count += 1

    def write_int(self, name: EnumId | None, value: int) -> None:
        """Write an integer value."""
        self._flush()
        self._write_name(0, name)
        self._write_int_value(0, value)
        self._levels[self._current_level].count += 1

    def write_float(self, name: EnumId | None, value: float) -> None:
        """Write a floating-point value."""
        self._flush()
        self._write_name(0, name)
        self._write_float_value(0, value)
        self._levels[self._current_level].count += 1

    def write_bool(self, name: EnumId | None, value: bool) -> None:
        """Write a boolean value."""
        self._flush()
        self._write_name(0, name)
        self._write_bool_value(0, value)
        self._levels[self._current_level].count += 1

    def write_null(self, name: EnumId | None) -> None:
        """Write a null value."""
        self._flush()
        self._write_name(0, name)
        self._write_null_value(0)
        self._levels[self._current_level].count += 1

    def write_raw(self, name: EnumId | None, raw_json: str) -> None:
        """Write a pre-formatted raw JSON value (for dicts, lists, etc.)."""
        self._flush()
        self._write_name(0, name)
        self._append_to_level(0, raw_json)
        self._levels[self._current_level].count += 1

    def write_hyperlink(
        self, name: EnumId | None, value: str | None, link: str | None = None,
    ) -> None:
        """Write a hyperlink value (HTML mode shows as <a> tag)."""
        self._flush()
        self._write_name(0, name)
        self._write_hyperlink_value(0, value, link)
        self._levels[self._current_level].count += 1

    # -----------------------------------------------------------------------
    # Decoder -- public API
    # -----------------------------------------------------------------------

    def decode(self, top: JsonDecoder, data: str | bytes) -> None:
        """Decode JSON data into a top-level object.

        Uses json.loads() to parse, then delegates to the object's decode().
        """
        parsed = self.parse_any(data)
        top.decode(self, parsed)

    def decode_progressive(
        self, top: JsonDecoder, data: str,
    ) -> tuple[int, bool]:
        """Decode one JSON document from a stream, returning bytes consumed.

        For progressive/streaming mode (websocket, NMOS subscriptions).
        Uses json.JSONDecoder.raw_decode() to consume one object at a time.

        Returns:
            (chars_consumed, success) tuple. success is False if no complete
            JSON document was found in data.
        """
        decoder = stdlib_json.JSONDecoder()
        try:
            # Skip leading whitespace
            idx = 0
            while idx < len(data) and data[idx] in " \t\n\r":
                idx += 1
            if idx >= len(data):
                return 0, False

            parsed, end_idx = decoder.raw_decode(data, idx)
            top.decode(self, parsed)
            self.progressive_count += 1
            return end_idx, True
        except stdlib_json.JSONDecodeError:
            return 0, False

    # -----------------------------------------------------------------------
    # Encoder -- internal helpers
    # -----------------------------------------------------------------------

    def _append(self, *parts: str) -> None:
        """Write strings directly to output."""
        for p in parts:
            self._output.write(p)

    def _append_to_level(self, level: int, *parts: str) -> None:
        """Write strings to a level buffer (or direct if level 0)."""
        if level >= MAX_NESTED_LEVELS:
            raise InvalidObject("object is too complex")
        if level == 0:
            for p in parts:
                self._output.write(p)
        else:
            for p in parts:
                self._levels[level].text.append(p)

    def _escape_string(self, s: str) -> str:
        """Escape a string for JSON output, with quotes."""
        # Use stdlib json for correct escaping
        return stdlib_json.dumps(s, ensure_ascii=False)

    def _escape_string_html(self, s: str) -> str:
        """Escape a string for JSON-in-HTML output, with quotes."""
        escaped = stdlib_json.dumps(s, ensure_ascii=False)
        return html.escape(escaped)

    def _output_newline(self, prefix: str = "") -> str:
        """Generate a newline + indentation string."""
        if self.level_indentation == 0:
            return prefix
        indent = " " * self._levels[self._current_level].indent
        if prefix:
            return prefix + "\n" + indent
        return "\n" + indent

    def _write_name(self, level: int, name: EnumId | None) -> None:
        """Write a JSON property name."""
        if name is None:
            return

        name_str = str(name)
        if not self.generate_html:
            self._append_to_level(level, self._escape_string(name_str), ":")
        else:
            self._append_to_level(
                level,
                '<span class="name">',
                self._escape_string_html(name_str),
                "</span>: ",
            )

    def _write_string_value(
        self, level: int, value: str | None, name: EnumId | None = None,
    ) -> None:
        """Write a string or null value."""
        if not self.generate_html:
            if value is None:
                self._append_to_level(level, "null")
            else:
                self._append_to_level(level, self._escape_string(value))
        else:
            if value is None:
                self._append_to_level(
                    level,
                    '<span class="value"><span class="null">null</span></span>',
                )
            else:
                # Check if value should be rendered as a clickable link
                href: str | None = None
                if self.link_resolver is not None and value:
                    # Use field name, or parent array name for array elements
                    resolve_name = str(name) if name is not None else self._levels[self._current_level].array_name
                    if resolve_name is not None:
                        href = self.link_resolver(resolve_name, value)

                escaped = self._escape_string_html(value)
                if href is not None:
                    self._append_to_level(
                        level,
                        '<span class="value"><span class="string">',
                        f'<a href="{html.escape(href)}">{escaped}</a>',
                        "</span></span>",
                    )
                else:
                    self._append_to_level(
                        level,
                        '<span class="value"><span class="string">',
                        escaped,
                        "</span></span>",
                    )

    def _write_null_value(self, level: int) -> None:
        """Write a null value."""
        self._write_string_value(level, None)

    def _write_bool_value(self, level: int, value: bool) -> None:
        """Write a boolean value."""
        s = "true" if value else "false"
        if not self.generate_html:
            self._append_to_level(level, s)
        else:
            self._append_to_level(
                level,
                '<span class="value"><span class="boolean">',
                s,
                "</span></span>",
            )

    def _write_int_value(self, level: int, value: int) -> None:
        """Write an integer value."""
        s = str(value)
        if not self.generate_html:
            self._append_to_level(level, s)
        else:
            self._append_to_level(
                level,
                '<span class="value"><span class="number">',
                s,
                "</span></span>",
            )

    def _write_float_value(self, level: int, value: float) -> None:
        """Write a floating-point value."""
        # Use %g formatting: no trailing zeros, no unnecessary decimal
        s = f"{value:g}"
        if not self.generate_html:
            self._append_to_level(level, s)
        else:
            self._append_to_level(
                level,
                '<span class="value"><span class="number">',
                s,
                "</span></span>",
            )

    def _write_hyperlink_value(
        self, level: int, value: str | None, link: str | None,
    ) -> None:
        """Write a hyperlink value."""
        if not self.generate_html:
            if value is None:
                self._append_to_level(level, "null")
            else:
                self._append_to_level(level, self._escape_string(value))
        else:
            if link is None:
                link = value
            if value is None:
                self._append_to_level(
                    level,
                    '<span class="value"><span class="null">null</span></span>',
                )
            else:
                assert link is not None
                self._append_to_level(
                    level,
                    '&quot;<a href=',
                    html.escape(link),
                    ">",
                    html.escape(value),
                    "</a>&quot;",
                )

    def _write_header(self, level: int) -> None:
        """Write separator before a content element."""
        if self._levels[self._current_level].count != 0:
            if not self.generate_html:
                self._append_to_level(level, self._output_newline(","))
            else:
                self._append_to_level(level, ",</li>")

        if self.generate_html and self._current_level > 0:
            self._append_to_level(level, "<li>")

    def _flush(self) -> None:
        """Flush all level buffers up to current level, then write header."""
        for i in range(self._current_level + 1):
            if self._levels[i].text:
                self._append("".join(self._levels[i].text))
                self._levels[i].text.clear()
                self._levels[i].content = True

        self._write_header(0)

    def _open_aggregate(
        self, name: EnumId | None, array: bool, omit_empty: bool,
    ) -> None:
        """Open a JSON object or array."""
        c = "[" if array else "{"

        if self._current_level + 1 >= MAX_NESTED_LEVELS:
            raise InvalidObject("too many nested levels")

        # Initialize next level's buffer
        self._levels[self._current_level + 1].text = []

        if self._levels[self._current_level].content:
            self._write_header(self._current_level + 1)

        self._current_level += 1
        lvl = self._levels[self._current_level]
        lvl.indent = self._levels[self._current_level - 1].indent + self.level_indentation
        lvl.count = 0
        lvl.content = False
        lvl.array_name = str(name) if array and name is not None else None

        self._write_name(self._current_level, name)

        if not self.generate_html:
            self._append_to_level(self._current_level, self._output_newline(c))
        else:
            self._append_to_level(
                self._current_level,
                '<span class="object">',
                c,
                "<ol>",
            )

        if not omit_empty:
            self._flush()

    def _close_aggregate(self, array: bool) -> None:
        """Close a JSON object or array."""
        c = "]" if array else "}"

        if self._current_level == 0:
            raise InvalidObject("invalid nested levels")

        if self._levels[self._current_level].content:
            self._levels[self._current_level].content = False
            self._current_level -= 1
            self._levels[self._current_level].count += 1

            if not self.generate_html:
                self._append(self._output_newline(""))
                self._append(c)
            else:
                self._append("</li></ol></span>", c)
        else:
            self._current_level -= 1
