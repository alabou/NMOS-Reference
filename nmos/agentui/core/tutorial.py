# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tutorial mode: the same run, written for someone learning the interface.

The journal exists to *prove* what happened — it is an audit trail, full of
selectors, wait signals and fidelity counters. That makes it exactly the wrong
document to hand a person who wants to learn the interface, because the evidence
that makes it trustworthy is also what buries the lesson.

A tutorial is the same run told the other way round: what you do, what you should
see, and only then — if you ask — why. Three levels of disclosure:

**Level 1, always visible.** The action in the interface and the observable result.
No selectors, no internals. If a reader follows only this, they can reproduce the
run by hand.

**Level 2, on request.** The resource state and NMOS data behind what just
appeared: the flow, the transport parameters, the subscription. For a reader who
believes the screen and now wants the model underneath it.

**Level 3, on request.** What the Controller and the Node actually did — the API
calls the page issued and the node's own trace of handling them. For a reader
debugging or extending the implementation.

Levels 2 and 3 are rendered inside collapsed ``<details>`` blocks, which is how a
static document offers "tell me more" without making the first read heavier. The
evidence in every level comes from the run that produced it; nothing here is
composed by hand after the fact.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .text import normalise_text


@dataclass(frozen=True, slots=True)
class Lesson:
    """One teaching step: an action, its visible result, and optional depth."""

    heading: str
    do: str
    see: str
    detail: str = ""
    internals: str = ""
    images: tuple[str, ...] = ()
    #: Journal sequence numbers this lesson covers, used to pull the API calls
    #: and node trace that belong to it.
    first_seq: int = 0
    last_seq: int = 0
    #: Structured state to show at level 2, rendered as a small table.
    state: Mapping[str, str] = field(default_factory=dict)


class Tutorial:
    """Accumulates lessons and renders ``tutorial.md``."""

    def __init__(
        self,
        root: Path,
        *,
        title: str,
        goal: str,
        audience: str = "",
    ) -> None:
        self.root = root
        self.title = title
        self.goal = goal
        self.audience = audience
        self._lessons: list[Lesson] = []

    def add(self, lesson: Lesson) -> None:
        """Record a teaching step."""
        self._lessons.append(lesson)

    @property
    def lessons(self) -> Sequence[Lesson]:
        return tuple(self._lessons)

    # -- rendering ---------------------------------------------------------

    def write(
        self,
        *,
        records: Sequence[Mapping[str, object]] = (),
        summary: str = "",
    ) -> Path:
        """Write ``tutorial.md``.

        ``records`` are the journal's step records; the level-3 section of each
        lesson is built from the ones in that lesson's sequence range, so the
        internals shown are the calls that actually occurred rather than a
        description of what usually happens.
        """
        path = self.root / "tutorial.md"
        out: list[str] = [
            f"# {self.title}\n\n",
            f"**Goal.** {self.goal}\n\n",
        ]
        if self.audience:
            out.append(f"{self.audience}\n\n")
        out.append(
            "Each step below shows **what to do** in the interface and **what you "
            "should see** as a result. Two expandable sections per step go "
            "deeper: *the data behind it* for the NMOS resources involved, and "
            "*under the hood* for what the Controller and Node actually did. "
            "Skip them entirely on a first read.\n\n"
        )
        out.append("---\n\n")

        for number, lesson in enumerate(self._lessons, start=1):
            out.append(self._render(number, lesson, records))

        if summary:
            out.append("---\n\n## What you just proved\n\n")
            out.append(summary + "\n")

        path.write_text("".join(out), encoding="utf-8")
        return path

    def _render(
        self,
        number: int,
        lesson: Lesson,
        records: Sequence[Mapping[str, object]],
    ) -> str:
        out: list[str] = [f"## Step {number} — {lesson.heading}\n\n"]
        out.append(f"**Do this.** {lesson.do}\n\n")
        out.append(f"**You should see.** {lesson.see}\n\n")

        for image in lesson.images:
            out.append(f"![Step {number}]({image})\n\n")

        if lesson.state:
            out.append("<details>\n<summary><b>The data behind it</b> — "
                       "resource state and NMOS values</summary>\n\n")
            out.append("| | |\n|---|---|\n")
            for key, value in lesson.state.items():
                out.append(f"| {key} | `{_clip(value)}` |\n")
            if lesson.detail:
                out.append(f"\n{lesson.detail}\n")
            out.append("\n</details>\n\n")
        elif lesson.detail:
            out.append("<details>\n<summary><b>The data behind it</b></summary>\n\n")
            out.append(f"{lesson.detail}\n\n</details>\n\n")

        internals = self._internals(lesson, records)
        if internals or lesson.internals:
            out.append("<details>\n<summary><b>Under the hood</b> — what the "
                       "Controller and Node did</summary>\n\n")
            if lesson.internals:
                out.append(f"{lesson.internals}\n\n")
            out.append(internals)
            out.append("\n</details>\n\n")

        return "".join(out)

    def _internals(
        self,
        lesson: Lesson,
        records: Sequence[Mapping[str, object]],
    ) -> str:
        """Render the calls and trace recorded for this lesson's steps."""
        calls: list[str] = []
        traced: list[str] = []

        for record in records:
            seq = record.get("seq")
            if not isinstance(seq, int):
                continue
            if not (lesson.first_seq <= seq <= lesson.last_seq):
                continue

            for request in _as_list(record.get("requests")):
                method = request.get("method", "?")
                path = request.get("path", "?")
                if "/api/" not in str(path):
                    continue
                if "debug/client-event" in str(path):
                    continue
                trace = request.get("trace_id")
                calls.append(
                    f"- `{method} {path}`" + (f"  (trace `{trace}`)" if trace else "")
                )

            for entry in _as_list(record.get("node_trace")):
                kind = str(entry.get("kind", ""))
                if kind.startswith("client."):
                    continue
                traced.append(f"- `{kind}` {_clip(json.dumps(entry), 150)}")

        out: list[str] = []
        if calls:
            out.append("The page issued these calls to the Controller:\n\n")
            # Newline-terminated: an earlier version extended the list with bare
            # strings and the bullets ran together into one unreadable line.
            out.extend(line + "\n" for line in dict.fromkeys(calls))
            out.append("\n")
        if traced:
            out.append("\nThe Node recorded:\n\n")
            out.extend(line + "\n" for line in dict.fromkeys(traced[:8]))
            out.append("\n")
        if not out:
            out.append(
                "_No Controller API calls were needed for this step — it is "
                "navigation or reading only._\n"
            )
        return "".join(out)


def _as_list(value: object) -> list[Mapping[str, object]]:
    """Narrow a journal field to a list of records."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _clip(value: object, limit: int = 110) -> str:
    """Normalise and shorten a value for a table cell."""
    text = normalise_text(str(value))
    return text if len(text) <= limit else text[: limit - 1] + "…"
