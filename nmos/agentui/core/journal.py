# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The run journal: the artifact a demo is actually judged on.

Three files, each with a different reader in mind:

``journal.jsonl``
    One object per step, flushed as it is written. A run that crashes still
    leaves every completed step on disk, which matters because the interesting
    step is usually the last one.

``journal.md``
    The human artifact. Per step: what was intended, the before and after
    images, the visible text that was read, and — where a control was refused —
    the server's reason as *text*. Native tooltips are drawn by the operating
    system and never appear in a screenshot, so a picture alone can show that a
    button was greyed out but never why. Putting the reason beside the image is
    what makes the gating demo evidence rather than assertion.

``manifest.json``
    Run-level provenance and the fidelity verdict: how many navigations
    happened, whether any were unaccounted for, whether a second page appeared,
    whether the driver itself issued HTTP, and whether a live update was
    genuinely observed or merely hoped for.

The manifest is what stops a passing-looking run from overstating itself. A demo
whose ``sse`` verdict is ``unconfirmed`` still passed as a demo — it just did not
prove the thing a careless reader might assume it did.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..enums import CorrelationKind, PageId, SseVerdict, StepOutcome, WaitSignal
from .text import normalise_text, truncate
from .waits import WaitOutcome


@dataclass(frozen=True, slots=True)
class WaitRecord:
    """One awaited signal, named, with what was observed."""

    signal: WaitSignal
    spec: str
    waited_ms: int
    satisfied: bool
    observed: str = ""
    branch: str | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "signal": self.signal,
            "spec": self.spec,
            "waited_ms": self.waited_ms,
            "satisfied": self.satisfied,
            "observed": truncate(self.observed, 300),
            "branch": self.branch,
        }


@dataclass(frozen=True, slots=True)
class RequestNote:
    """A request the page issued during a step, as recorded for the journal."""

    method: str
    path: str
    resource_type: str
    trace_id: str | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "method": self.method,
            "path": self.path,
            "resource_type": self.resource_type,
            "trace_id": self.trace_id,
        }


@dataclass(frozen=True, slots=True)
class FidelityLedger:
    """Per-run tally of everything that would invalidate the run's claim."""

    navigations: int = 0
    unattributed_navigations: int = 0
    extra_pages: int = 0
    driver_requests: int = 0
    blanket_tls_bypass: bool = False

    def to_json(self) -> dict[str, object]:
        return {
            "navigations": self.navigations,
            "unattributed": self.unattributed_navigations,
            "extra_pages": self.extra_pages,
            "driver_requests": self.driver_requests,
            "blanket_tls_bypass": self.blanket_tls_bypass,
        }

    @property
    def clean(self) -> bool:
        """Whether the run may claim to have acted only as an operator could."""
        return (
            self.unattributed_navigations == 0
            and self.extra_pages == 0
            and self.driver_requests == 0
            and not self.blanket_tls_bypass
        )


@dataclass(slots=True)
class StepRecord:
    """One journaled step.

    Mutable while the step is executing, then written once and not revisited.
    """

    seq: int
    verb: str
    intent: str = ""
    args: Mapping[str, str] = field(default_factory=dict)
    page_id: PageId = PageId.UNKNOWN
    url: str = ""
    preconditions: tuple[str, ...] = ()
    waited_on: tuple[WaitRecord, ...] = ()
    observed: Mapping[str, object] = field(default_factory=dict)
    requests: tuple[RequestNote, ...] = ()
    dialogs: tuple[str, ...] = ()
    trace_ids: tuple[str, ...] = ()
    node_trace: tuple[Mapping[str, object], ...] = ()
    correlation: CorrelationKind = CorrelationKind.UNAVAILABLE
    state_text: str = ""
    artifacts: Mapping[str, str] = field(default_factory=dict)
    outcome: StepOutcome = StepOutcome.OK
    error_type: str = ""
    error_message: str = ""
    block_reason: str = ""
    duration_ms: int = 0

    def to_json(self) -> dict[str, object]:
        return {
            "seq": self.seq,
            "verb": self.verb,
            "intent": self.intent,
            "args": dict(self.args),
            "page_id": self.page_id,
            "url": self.url,
            "preconditions": list(self.preconditions),
            "waited_on": [w.to_json() for w in self.waited_on],
            "observed": dict(self.observed),
            "requests": [r.to_json() for r in self.requests],
            "dialogs": list(self.dialogs),
            "trace_ids": list(self.trace_ids),
            "node_trace": [dict(entry) for entry in self.node_trace],
            "correlation": self.correlation,
            "state_text": self.state_text,
            "artifacts": dict(self.artifacts),
            "outcome": self.outcome,
            "error": {
                "type": self.error_type,
                "message": self.error_message,
                "reason": self.block_reason,
            } if self.error_type else None,
            "duration_ms": self.duration_ms,
        }


def new_run_id(scenario: str, *, now: datetime | None = None) -> str:
    """Build a sortable, unique run identifier.

    Timestamp first so a directory listing is chronological, then the scenario so
    a run is recognisable at a glance, then randomness so two runs started in the
    same second cannot collide.
    """
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    suffix = os.urandom(3).hex()
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in scenario)
    return f"{stamp}-{safe}-{suffix}"


class Journal:
    """Writes the three artifact files and owns the run directory layout."""

    def __init__(
        self,
        root: Path,
        *,
        scenario: str,
        run_id: str | None = None,
        title: str = "Agent-driven UI run",
    ) -> None:
        self.scenario = scenario
        self.run_id = run_id or new_run_id(scenario)
        self.root = root / self.run_id
        self.steps_dir = self.root / "steps"
        self.steps_dir.mkdir(parents=True, exist_ok=True)

        self._jsonl = self.root / "journal.jsonl"
        self._markdown = self.root / "journal.md"
        self._manifest = self.root / "manifest.json"
        self._records: list[StepRecord] = []

        self._markdown.write_text(
            f"# {title}\n\n"
            f"- **Scenario:** `{scenario}`\n"
            f"- **Run:** `{self.run_id}`\n\n"
            f"Every step below was produced by clicking, typing, or choosing "
            f"through the application's own interface. Where a control was "
            f"refused, the reason shown is the server's own wording, quoted "
            f"verbatim — it is reproduced as text because tooltips do not "
            f"appear in screenshots.\n\n",
            encoding="utf-8",
        )

    # -- per-step artifacts -------------------------------------------------

    def step_dir(self, seq: int, verb: str) -> Path:
        """Create and return the directory for one step's artifacts."""
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in verb)
        path = self.steps_dir / f"{seq:04d}-{safe}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_png(self, directory: Path, name: str, data: bytes) -> Path:
        """Write a screenshot and return its path."""
        path = directory / f"{name}.png"
        path.write_bytes(data)
        return path

    def write_text_file(self, directory: Path, name: str, text: str) -> Path:
        """Write a text artifact and return its path."""
        path = directory / f"{name}.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def relative(self, path: Path) -> str:
        """Render a path relative to the run root, for portable artifacts."""
        try:
            return str(path.relative_to(self.root))
        except ValueError:                     # pragma: no cover - defensive
            return str(path)

    # -- step records -------------------------------------------------------

    def append(self, record: StepRecord) -> None:
        """Append a completed step to both the JSONL and the markdown.

        Flushed immediately. A crash mid-run must not cost the record of the step
        that caused it.
        """
        self._records.append(record)
        with self._jsonl.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_json(), default=str) + "\n")
            handle.flush()
        with self._markdown.open("a", encoding="utf-8") as handle:
            handle.write(self._render_step(record))
            handle.flush()

    def _render_step(self, record: StepRecord) -> str:
        """Render one step as markdown."""
        marker = {
            StepOutcome.OK: "✅",
            StepOutcome.BLOCKED: "🚫",
            StepOutcome.GUARDED: "⚠️",
            StepOutcome.FAILED: "❌",
        }[record.outcome]

        lines: list[str] = [
            f"## {record.seq}. {marker} `{record.verb}`"
            f"{' — ' + record.intent if record.intent else ''}\n",
        ]
        if record.args:
            rendered = ", ".join(f"`{k}={v}`" for k, v in record.args.items())
            lines.append(f"**Arguments:** {rendered}\n")
        lines.append(f"**Page:** `{record.page_id}` — <{record.url}>\n")

        if record.preconditions:
            lines.append("\n**Controls examined before acting:**\n")
            lines.extend(f"- {p}\n" for p in record.preconditions)

        # The block reason goes immediately under the heading area and again
        # beside the image, because it is the one piece of evidence a reader
        # cannot recover from the screenshot.
        if record.block_reason:
            lines.append(
                f"\n> **Refused by the server:** {record.block_reason}\n"
            )

        if record.dialogs:
            lines.append("\n**Dialogs shown to the operator:**\n")
            lines.extend(f"- “{d}”\n" for d in record.dialogs)

        if record.waited_on:
            lines.append("\n**Waited on:**\n\n")
            lines.append("| signal | condition | ms | met | observed |\n")
            lines.append("|---|---|---:|:---:|---|\n")
            for wait in record.waited_on:
                met = "yes" if wait.satisfied else "**no**"
                lines.append(
                    f"| `{wait.signal}` | `{wait.spec}` | {wait.waited_ms} | "
                    f"{met} | {truncate(wait.observed, 80)} |\n"
                )

        images = [(k, v) for k, v in record.artifacts.items()
                  if v.endswith(".png")]
        if images:
            lines.append("\n")
            for label, rel in images:
                lines.append(f"**{label}**\n\n![{label}]({rel})\n\n")

        if record.state_text:
            lines.append("\n**What was on screen:**\n\n```\n")
            lines.append(truncate(record.state_text, 1500) + "\n")
            lines.append("```\n")

        if record.requests:
            lines.append("\n**Requests the page made:**\n\n")
            for req in record.requests:
                trace = f" `trace={req.trace_id}`" if req.trace_id else ""
                lines.append(
                    f"- `{req.method} {req.path}` "
                    f"({req.resource_type}){trace}\n"
                )

        if record.node_trace:
            lines.append("\n**Correlated server trace:**\n\n")
            for entry in record.node_trace:
                kind = entry.get("kind", "?")
                lines.append(f"- `{kind}` {truncate(str(entry), 160)}\n")
        elif record.correlation is CorrelationKind.SERVER_ONLY:
            lines.append(
                "\n_No client-side trace for this step: the sign-in page loads "
                "no JavaScript, so only the server's own request records exist._\n"
            )

        if record.error_type:
            lines.append(
                f"\n**Outcome:** `{record.error_type}` — "
                f"{record.error_message}\n"
            )

        lines.append(f"\n_Took {record.duration_ms} ms._\n\n---\n\n")
        return "".join(lines)

    # -- manifest -----------------------------------------------------------

    def finalise(
        self,
        *,
        target: Mapping[str, object],
        environment: Mapping[str, str],
        fidelity: FidelityLedger,
        sse: SseVerdict,
        mutating: bool,
        debug_tracing: bool,
        diagnostic_api_used: bool = False,
        controller_js_version: str = "",
        error: str = "",
    ) -> Path:
        """Write ``manifest.json`` and return its path."""
        counts: dict[str, int] = {}
        for record in self._records:
            counts[record.outcome] = counts.get(record.outcome, 0) + 1

        manifest: dict[str, object] = {
            "run_id": self.run_id,
            "scenario": self.scenario,
            "target": dict(target),
            "environment": dict(environment),
            "controller_js_version": controller_js_version,
            "debug_tracing": debug_tracing,
            "diagnostic_api_used": diagnostic_api_used,
            "mutating": mutating,
            "fidelity": fidelity.to_json(),
            "fidelity_clean": fidelity.clean,
            "sse": sse,
            "steps": len(self._records),
            "outcomes": counts,
            "error": error,
        }
        self._manifest.write_text(
            json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8"
        )
        self._append_summary(manifest, fidelity, sse, mutating,
                             diagnostic_api_used, error)
        return self._manifest

    def _append_summary(
        self,
        manifest: Mapping[str, object],
        fidelity: FidelityLedger,
        sse: SseVerdict,
        mutating: bool,
        diagnostic_api_used: bool,
        error: str,
    ) -> None:
        """Close the markdown with the honesty section."""
        lines = ["## Run summary\n\n"]
        lines.append(f"- Steps: **{manifest['steps']}** {manifest['outcomes']}\n")
        lines.append(
            f"- Acted only as an operator could: "
            f"**{'yes' if fidelity.clean else 'NO'}** "
            f"({fidelity.to_json()})\n"
        )

        # State the SSE verdict in words, because "confirmed" and "unconfirmed"
        # are easy to skim past in JSON and they mean very different things about
        # what this run actually demonstrated.
        if sse is SseVerdict.CONFIRMED:
            lines.append(
                "- Live status updates: **confirmed** — an attribute or class "
                "moved away from the value captured at page load, so a "
                "server-sent update genuinely arrived.\n"
            )
        elif sse is SseVerdict.UNCONFIRMED:
            lines.append(
                "- Live status updates: **unconfirmed** — the run waited and saw "
                "no change against the page-load baseline. This is not a "
                "failure, but nothing here evidences live updating.\n"
            )
        else:
            lines.append("- Live status updates: not exercised by this scenario.\n")

        if mutating:
            lines.append(
                "- **This run made changes.** Real IS-05/IS-11 calls were "
                "issued and the rig was left in the state it reached, for "
                "inspection.\n"
            )
        if diagnostic_api_used:
            lines.append(
                "- ⚠️ A diagnostic API endpoint was read directly. Those reads "
                "are not something the interface offers an operator.\n"
            )
        if error:
            lines.append(f"- Run ended with: `{error}`\n")

        with self._markdown.open("a", encoding="utf-8") as handle:
            handle.write("".join(lines))

    # -- accessors used by tests and callers --------------------------------

    @property
    def records(self) -> Sequence[StepRecord]:
        """Steps written so far."""
        return tuple(self._records)

    @property
    def jsonl_path(self) -> Path:
        return self._jsonl

    @property
    def markdown_path(self) -> Path:
        return self._markdown

    @property
    def manifest_path(self) -> Path:
        return self._manifest


def wait_record(signal: WaitSignal, outcome: WaitOutcome) -> WaitRecord:
    """Adapt a :class:`WaitOutcome` into its journal form.

    A free function rather than a method on either type: ``core.waits`` has no
    reason to know the journal exists, and the journal has no reason to own wait
    semantics. The dependency runs one way, from journal to waits.
    """
    return WaitRecord(
        signal=signal,
        spec=outcome.spec,
        waited_ms=outcome.waited_ms,
        satisfied=outcome.satisfied,
        observed=normalise_text(outcome.observed),
        branch=outcome.branch,
    )


def wait_records(
    outcomes: Iterable[tuple[WaitSignal, WaitOutcome]],
) -> tuple[WaitRecord, ...]:
    """Adapt several ``(signal, outcome)`` pairs at once."""
    return tuple(wait_record(signal, outcome) for signal, outcome in outcomes)
