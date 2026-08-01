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
calls the page issued, the node's own trace of handling them, the NMOS technology
underneath (hierarchical capabilities, the CCF, IS-11 negotiation, BCP-008 status
over IS-04, privacy encryption, node reservation), **the specification it
implements**, and **the files that implement it**. This is the level that turns a
tutorial into an entry point to the codebase: a reader who has just watched a
constraint set apply should be able to go and read the code that applied it.

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

#: Where a reader goes next. The Matrox tutorials cover the specification corpus
#: this implementation follows; a generated tutorial shows one worked example
#: against a live rig, so the two complement rather than duplicate each other.
DEFAULT_FURTHER_READING: tuple[tuple[str, str], ...] = (
    ("NMOS-MatroxOnly — tutorials",
     "https://github.com/alabou/NMOS-MatroxOnly/tree/main/tutorials"),
    ("NMOS-MatroxOnly — the Matrox NMOS extensions corpus",
     "https://github.com/alabou/NMOS-MatroxOnly"),
    ("AMWA NMOS specifications (IS-04, IS-05, IS-11, the BCPs)",
     "https://specs.amwa.tv/"),
    ("VSF Technical Recommendations (TR-10 series)",
     "https://vsf.tv/technical-recommendations/"),
)


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
    #: ``(path, what you will find there)`` pairs closing the tutorial's third
    #: level. A reader who has understood the step and wants the implementation
    #: should not have to guess which of 380 source files to open.
    sources: tuple[tuple[str, str], ...] = ()
    #: ``(name, url)`` pairs naming the specification the step exercised. Code
    #: shows *how this project* does it; the spec shows what everyone has agreed
    #: to. A reader learning NMOS needs both, and neither substitutes.
    specs: tuple[tuple[str, str], ...] = ()


class Tutorial:
    """Accumulates lessons and renders ``tutorial.md``."""

    def __init__(
        self,
        root: Path,
        *,
        title: str,
        goal: str,
        audience: str = "",
        further_reading: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.root = root
        self.title = title
        self.goal = goal
        self.audience = audience
        #: ``(name, url)`` closing links — the specification corpus and the
        #: existing tutorials this one sits alongside. Defaulted by the CLI so
        #: every generated tutorial ends somewhere a reader can continue.
        self.further_reading = further_reading or DEFAULT_FURTHER_READING
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

        if self.further_reading:
            out.append("\n---\n\n## Where to go next\n\n")
            out.append(
                "This tutorial is one worked example driven against a live rig. "
                "For the specifications behind it, and for tutorials covering the "
                "wider corpus:\n\n"
            )
            for name, url in self.further_reading:
                out.append(f"- [{name}]({url})\n")
            out.append(
                "\nThe *under the hood* sections above link into this "
                "repository's own source for each concept as it appears.\n"
            )

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
            if lesson.specs:
                out.append("\n**The specifications this implements:**\n\n")
                for name, url in lesson.specs:
                    out.append(f"- [{name}]({url})\n")
            if lesson.sources:
                out.append("\n**Where this lives in the project:**\n\n")
                for path, what in lesson.sources:
                    out.append(f"- [`{path}`]({_repo_link(path)}) — {what}\n")
            out.append("\n</details>\n\n")

        return "".join(out)

    def _internals(
        self,
        lesson: Lesson,
        records: Sequence[Mapping[str, object]],
    ) -> str:
        """Render the calls and trace recorded for this lesson's steps.

        Action-driven traffic and page-lifecycle traffic are listed separately,
        and that distinction matters for honesty rather than tidiness. A page
        opens its status stream on load and fires a best-effort privacy release
        on unload; both are attributed to whichever step navigated, so listing
        them plainly as "what the Controller did" invites the reader to conclude
        that, say, *checking a receiver's status releases a privacy reservation*.
        It does not. They are still shown — hiding real traffic would be worse —
        but labelled as what they are.
        """
        actions: list[str] = []
        lifecycle: list[str] = []
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
                line = (f"- `{method} {path}`"
                        + (f"  (trace `{trace}`)" if trace else ""))
                # fetch/xhr is the page acting on the operator's click; an
                # eventsource opens with the page and a ping is a beforeunload
                # beacon, so neither was caused by the action being taught.
                if str(request.get("resource_type")) in _LIFECYCLE_TYPES:
                    lifecycle.append(line)
                else:
                    actions.append(line)

            for entry in _as_list(record.get("node_trace")):
                kind = str(entry.get("kind", ""))
                if kind.startswith("client."):
                    continue
                traced.append(f"- `{kind}` {_clip(json.dumps(entry), 150)}")

        out: list[str] = []
        if actions:
            out.append("This action caused the page to call:\n\n")
            out.extend(line + "\n" for line in dict.fromkeys(actions))
            out.append("\n")
        if traced:
            out.append("\nThe Node recorded:\n\n")
            out.extend(line + "\n" for line in dict.fromkeys(traced[:8]))
            out.append("\n")
        if lifecycle:
            out.append(
                "\nAlso seen, but *not* caused by this action — traffic the page "
                "generates on its own schedule, such as opening its live-status "
                "stream when it loads:\n\n"
            )
            out.extend(line + "\n" for line in dict.fromkeys(lifecycle))
            out.append("\n")
        if not out:
            out.append(
                "_No Controller API calls were needed for this step — it is "
                "navigation or reading only._\n"
            )
        return "".join(out)


#: How a source path is turned into a link. Relative to the run directory, which
#: sits several levels below the project root — so a reader opening the tutorial
#: from the artifacts tree can still follow the link to the file.
_REPO_DEPTH = "../../../"

#: Resource types a page produces on its own schedule rather than in response to
#: an operator action: the live-status EventSource, and sendBeacon on unload
#: (which Chromium reports as ``ping``).
_LIFECYCLE_TYPES = frozenset({"eventsource", "ping"})


def _repo_link(path: str) -> str:
    """Link a project-relative source path from inside the artifacts tree."""
    return _REPO_DEPTH + path


def _as_list(value: object) -> list[Mapping[str, object]]:
    """Narrow a journal field to a list of records."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _clip(value: object, limit: int = 110) -> str:
    """Normalise and shorten a value for a table cell."""
    text = normalise_text(str(value))
    return text if len(text) <= limit else text[: limit - 1] + "…"
