# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Repo-wide guard against Jinja whitespace control gluing markup together.

The controller's environment runs with ``trim_blocks`` and ``lstrip_blocks``
(see :func:`nmos.controller.app.create_controller_app`). Together they mean:

* ``trim_blocks`` — the newline immediately after ``%}`` is removed;
* ``lstrip_blocks`` — whitespace from the start of a line up to a block tag
  is removed.

So two conditional fragments written on adjacent lines, in the natural and
readable style::

    <input
       {% if a %}checked{% endif %}
       {% if b %}disabled{% endif %}>

render with **nothing at all** between them when both conditions hold:
``checkeddisabled``. The browser parses that as a single unknown attribute and
both booleans are silently lost. It shipped that way on the exclusivity switch
and made a held Node Reservation render as un-held, which in turn made the
reservation impossible to release from the page.

The failure is invisible in review (the template looks right), invisible to a
substring assertion (``'checked' in html`` passes), and only shows up when both
branches are taken at once — which for that switch meant "reservation held AND
something active", a combination no test covered.

Hence this check, which is about the *shape* rather than any one template: a
line consisting solely of an inline ``{% if %}…{% endif %}`` whose rendered
body would be glued directly onto the next line's rendered body must carry its
own separator. Putting a space inside the block body is the fix, because the
body is template data and neither flag touches it.

**Scoped to the inside of an HTML start tag on purpose.** Gluing is not a
defect in general — it is frequently the point. The query-string macros in
``senders_caps.html`` and friends build ``&byFormat=…&byLayer=…`` from exactly
this construct, and inserting a separator there would corrupt the URL. Inside a
start tag, though, whitespace is the *only* thing separating one attribute from
the next, so a missing separator always changes meaning. That is the one
context this test judges.

Nothing here needs updating when templates are added — new files are picked up
automatically.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from nmos.controller import app as controller_app


TEMPLATES_DIR = Path(controller_app.__file__).parent / "templates"

#: A line that opens with an inline conditional emitting ``BODY``, plus
#: whatever follows the ``{% endif %}`` as ``TAIL``.
#:
#: ``TAIL`` matters: the emitting line must have an *empty* tail for the
#: hazard to exist, because only then is ``BODY``'s last character the last
#: thing on the line before ``trim_blocks`` deletes the newline. The line
#: being glued *onto*, by contrast, routinely has a tail — the exclusivity
#: switch's ``{% if excl_locked %}disabled{% endif %}>`` closes the tag on
#: the same line. Anchoring the whole line (an earlier version of this
#: pattern) therefore skipped the one case that shipped broken.
_INLINE_IF = re.compile(
    r"^(?P<indent>\s*)"
    r"\{%-?\s*if\b.*?-?%\}"
    r"(?P<body>.*?)"
    r"\{%-?\s*endif\s*-?%\}"
    r"(?P<tail>.*)$"
)

#: A line whose first non-whitespace content is a block tag — the case where
#: ``lstrip_blocks`` deletes the indentation, so the previous line's output is
#: followed immediately by this line's output.
_STARTS_WITH_BLOCK = re.compile(r"^\s*\{%-?\s*\w")

#: Jinja constructs, skipped when deciding whether we are inside an HTML tag —
#: a ``>`` in a comment or expression is not a tag terminator.
_JINJA_SPAN = re.compile(r"\{#.*?#\}|\{%.*?%\}|\{\{.*?\}\}", re.DOTALL)


def _inside_start_tag(text: str) -> bool:
    """Whether ``text`` ends part-way through an HTML start tag.

    Jinja spans are blanked first so that a ``>`` inside ``{# … #}`` or an
    expression cannot look like the end of a tag. Then a single pass tracks
    ``<name`` … ``>``; the answer is the state left at the end of the slice.
    """
    cleaned = _JINJA_SPAN.sub(lambda m: " " * len(m.group(0)), text)
    in_tag = False
    for i, ch in enumerate(cleaned):
        if not in_tag:
            if ch == "<" and i + 1 < len(cleaned) and (
                cleaned[i + 1].isalpha() or cleaned[i + 1] == "/"
            ):
                in_tag = True
        elif ch == ">":
            in_tag = False
    return in_tag


def _template_files() -> list[Path]:
    return sorted(TEMPLATES_DIR.rglob("*.html"))


def test_templates_directory_is_found() -> None:
    """Guard the premise: an empty glob would make every test below vacuous."""
    files = _template_files()
    assert files, f"no templates found under {TEMPLATES_DIR}"
    assert any(f.name == "privacy_section.html" for f in files)


@pytest.mark.parametrize(
    "template", _template_files(), ids=lambda p: p.name,
)
def test_no_glued_conditional_markup(template: Path) -> None:
    """No conditional attribute may abut the next attribute's output.

    Reported as ``file:line`` with both offending lines so the fix — add a
    leading space inside the ``{% if %}`` body — is obvious from the failure.
    """
    source = template.read_text()
    lines = source.splitlines()
    # Byte offset of the start of each line, for the in-tag test.
    offsets: list[int] = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1

    offences: list[str] = []

    for i, line in enumerate(lines):
        match = _INLINE_IF.match(line)
        if match is None:
            continue
        if match.group("tail"):
            continue          # emits more after {% endif %}; no newline eaten
        body = match.group("body")
        if not body or body[-1].isspace():
            continue          # this fragment ends in its own separator
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if not _STARTS_WITH_BLOCK.match(nxt):
            continue          # the next line keeps its indentation
        # Only an attribute list is judged: elsewhere, concatenation is
        # usually deliberate (see this module's docstring).
        if not _inside_start_tag(source[: offsets[i]]):
            continue
        # The next line's output must itself be attribute-ish. An inline
        # conditional contributes its body; anything else (``{% for %}``,
        # ``{% endmacro %}``) emits no text of its own here.
        nxt_match = _INLINE_IF.match(nxt)
        if nxt_match is None:
            continue
        following = nxt_match.group("body")
        if not following or following[:1].isspace():
            continue          # separator supplied by the following fragment
        offences.append(
            f"{template.relative_to(TEMPLATES_DIR.parent)}:{i + 1}\n"
            f"      {line.strip()}\n"
            f"      {nxt.strip()}\n"
            f"    -> inside a start tag, renders as "
            f"{body + following!r} — one attribute, not two"
        )

    assert not offences, (
        "Jinja trim_blocks/lstrip_blocks will glue these attributes "
        "together.\nAdd a leading space inside the {% if %} body:\n\n"
        + "\n\n".join(offences)
    )
