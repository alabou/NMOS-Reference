# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Locate the exact source text of a member inside a JSON object.

Parsing throws away spelling. ``{"x": 1e3}`` and ``{"x": 1000.0}`` parse to the
same float, ``"caf\\u00e9"`` and ``"café"`` to the same string, and no encoder
can tell afterwards which one arrived. That is fine for meaning and wrong for
fidelity: a registry that promises to serve a Node's registration unchanged has
to keep the bytes, not re-derive them.

The registry needs this because the thing it must preserve is nested. A Node
POSTs ``{"type": "node", "data": {...}}`` and it is the ``data`` value alone
that gets stored and served back, so slicing it out of the request text is the
only way to keep it verbatim. The same function reads it back out of the
storage envelope, which embeds the body the same way.

Implemented on ``json.decoder``'s own primitives -- ``scanstring`` for keys and
``JSONDecoder.raw_decode`` for values -- rather than on a hand-written parser.
Both are the exact routines ``json.loads`` uses, so anything the standard
library accepts is accepted here identically, and ``raw_decode`` returns the
index it stopped at, which *is* the end of the span.
"""

from __future__ import annotations

import json
# ``scanstring`` is the C-accelerated key scanner ``json.loads`` itself uses.
# It is real at runtime but absent from typeshed's stubs, hence the ignore.
from json.decoder import scanstring  # type: ignore[attr-defined]
from typing import Any, Iterator

__all__ = ["member_text", "member_spans", "JsonSpanError"]

_DECODER = json.JSONDecoder()
_WHITESPACE = " \t\n\r"


class JsonSpanError(ValueError):
    """The text is not a JSON object, or ends part-way through one."""


def _skip_ws(source: str, index: int) -> int:
    while index < len(source) and source[index] in _WHITESPACE:
        index += 1
    return index


def _require(source: str, index: int, expected: str) -> None:
    if index >= len(source):
        raise JsonSpanError(f"expected {expected!r} but the text ended")
    if source[index] != expected:
        raise JsonSpanError(
            f"expected {expected!r} at offset {index}, found {source[index]!r}",
        )


def _scan(source: str) -> Iterator[tuple[str, str, Any]]:
    """Yield ``(name, span_text, value)`` for each top-level member, in order.

    One pass. ``raw_decode`` has to build each value anyway in order to find
    where it ends, so the value is yielded rather than discarded -- which is
    what lets a caller that needs both the span and the parsed form get them
    without parsing the document twice.
    """
    index = _skip_ws(source, 0)
    _require(source, index, "{")
    index = _skip_ws(source, index + 1)

    if index < len(source) and source[index] == "}":
        return

    while True:
        _require(source, index, '"')
        try:
            name, index = scanstring(source, index + 1)
        except ValueError as exc:
            raise JsonSpanError(f"bad member name at offset {index}: {exc}") from exc

        index = _skip_ws(source, index)
        _require(source, index, ":")
        index = _skip_ws(source, index + 1)

        start = index
        try:
            value, index = _DECODER.raw_decode(source, index)
        except ValueError as exc:
            raise JsonSpanError(
                f"bad value for {name!r} at offset {start}: {exc}",
            ) from exc

        yield name, source[start:index], value

        index = _skip_ws(source, index)
        if index >= len(source):
            raise JsonSpanError("object ended without '}'")
        if source[index] == ",":
            index = _skip_ws(source, index + 1)
            continue
        if source[index] == "}":
            return
        raise JsonSpanError(
            f"expected ',' or '}}' at offset {index}, found {source[index]!r}",
        )


def member_text(source: str, key: str) -> str | None:
    """Return the exact source text of ``key``'s value, or None if absent.

    Args:
        source: Text of a JSON **object**. Anything else is an error rather
            than a None, because a caller asking for a member of a non-object
            has already gone wrong somewhere earlier.
        key: The member to locate, at the top level only. Nested members of the
            same name are skipped, because ``raw_decode`` consumes each value
            whole -- which is also what makes this safe against a ``"data"``
            appearing as a *string* somewhere inside another member.

    Returns:
        The substring spanning the value, byte-for-byte as it appears in
        ``source``.

    Raises:
        JsonSpanError: ``source`` is not a well-formed JSON object.

    On duplicate keys the **last** occurrence wins, matching ``json.loads``, so
    the text this returns always describes the value the parsed form holds.

    Prefer ``member_spans`` when the decoded value is wanted as well: this
    throws away everything ``raw_decode`` built, so pairing it with a separate
    ``json.loads`` of the same document parses that document twice.
    """
    found: str | None = None
    for name, span, _value in _scan(source):
        if name == key:
            found = span
    return found


def member_spans(source: str, /) -> dict[str, tuple[str, Any]]:
    """Every top-level member as ``{name: (span_text, value)}``, in one pass.

    The point is to parse the document **once** where a caller needs both
    representations -- the exact text of one member to store verbatim, and the
    decoded values to validate against. Doing that as ``json.loads`` plus
    ``member_text`` parses everything twice, which on the registration path
    meant a second full parse of every resource body.

    Duplicate keys resolve to the last occurrence, as ``json.loads`` does.

    Raises:
        JsonSpanError: ``source`` is not a well-formed JSON object.
    """
    return {name: (span, value) for name, span, value in _scan(source)}
