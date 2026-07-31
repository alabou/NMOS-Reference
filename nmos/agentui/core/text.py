# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The single text normaliser used for every comparison and every journal entry.

There is exactly one of these on purpose. Text reaching the driver from Jinja
output is full of incidental whitespace — template indentation, newlines inside
``{% set %}`` blocks, and non-breaking spaces written deliberately to stop
labels wrapping. The Controller's own navigation bar renders ``Sign&nbsp;out``,
so a naive equality test against ``"Sign out"`` silently finds nothing and the
driver reports "control absent" for a control that is plainly on screen.

Normalising in one place, and applying it to *both* sides of every comparison as
well as to everything written into the journal, means a lookup can never
succeed while the journal records a differently-spaced version of the same text.
"""

from __future__ import annotations

# U+00A0 NO-BREAK SPACE is the one that actually bites, but the same reasoning
# applies to the whole family of fixed-width spaces a template might contain,
# and to the zero-width joiners that occasionally arrive with pasted content.
_SPACE_LIKE = (
    " "  # no-break space -- rendered by &nbsp;
    " "  # figure space
    " "  # narrow no-break space
    "⁠"  # word joiner
    "﻿"  # zero-width no-break space / BOM
)

_TRANSLATION = {ord(ch): " " for ch in _SPACE_LIKE}


def normalise_text(raw: str | None) -> str:
    """Collapse a rendered string to its comparable form.

    Maps every space-like code point to an ordinary space, collapses runs of
    whitespace to one, and strips the ends. ``None`` becomes ``""`` so callers
    handling a possibly-absent attribute do not each need a guard.
    """
    if raw is None:
        return ""
    return " ".join(raw.translate(_TRANSLATION).split())


def texts_match(left: str | None, right: str | None) -> bool:
    """Compare two rendered strings after normalising both sides.

    Case-sensitive: button labels are authored deliberately, and matching
    case-insensitively would let a scenario claim it clicked "activate" when the
    page offers "Activate Receivers".
    """
    return normalise_text(left) == normalise_text(right)


def contains_text(haystack: str | None, needle: str | None) -> bool:
    """Substring test performed on the normalised forms of both arguments."""
    return normalise_text(needle) in normalise_text(haystack)


def truncate(raw: str | None, limit: int = 200) -> str:
    """Normalise and shorten for a one-line journal field.

    Used only for display; assertions always run against the untruncated value,
    because a truncated comparison could pass on a prefix and mislead.
    """
    text = normalise_text(raw)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
