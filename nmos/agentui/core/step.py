# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The step wrapper: the only path through which the UI may be touched.

Every verb in every adapter runs inside :meth:`Recorder.step`. That is what makes
the journal complete rather than best-effort — there is no code path that
interacts with the page and forgets to record it, because interacting *is*
opening a step.

The wrapper also owns the fidelity invariants, and this is the right place for
them: a navigation only makes sense as "expected" or "unaccounted for" relative
to a step that either did or did not ask for one, and "a blocked control issued
no HTTP" is only checkable if something knows which requests belong to which
attempt.

Invariants enforced per step
----------------------------
* **Attributed navigation.** A main-frame navigation must belong to a step that
  declared it expected one. A stray ``location.assign``, an unforeseen redirect,
  or a direct navigation slipped in some other way is caught here.
* **Single page.** The page count must stay at one. The application closes its
  status stream on ``visibilitychange``, so a second page covering the demo page
  would freeze live updates while leaving stale values on screen looking current
  — the most convincing way this driver could lie.
* **No driver-issued HTTP.** Requests to the application's JSON API must carry a
  page-originated resource type, and a *state-changing* one must sit inside a step
  that actually interacted. A write appearing in a step that clicked nothing is
  the signature of the driver taking a short cut. Reads are not held to that,
  because a page fetches and streams on its own schedule.
* **A refusal costs nothing.** A step ending in a blocked control must have
  issued no requests at all. This is the machine-checkable form of the claim
  that "blocked" really means blocked.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

from ..enums import CorrelationKind, PageId, StepOutcome, WaitSignal
from ..errors import (
    AgentUiError,
    BlockedControl,
    ControlNotAvailable,
    FidelityViolation,
    SelectionGuard,
)
from .adapter import AppAdapter
from .affordance import Control
from .journal import (
    FidelityLedger,
    Journal,
    RequestNote,
    StepRecord,
    WaitRecord,
    wait_record,
)
from .surface import Surface
from .text import normalise_text
from .waits import WaitOutcome

#: Path fragment that identifies a call to the application's own JSON API. Used
#: only to decide whether a recorded request needs provenance checking.
_API_MARKER = "/api/"

#: Resource types that only a page's own scripts can produce.
#:
#: All four are the page acting, not the driver — and each of the last three was
#: learned by having the invariant fire on the application's own perfectly ordinary
#: behaviour:
#:
#: ``fetch``, ``xhr``
#:     the obvious cases.
#: ``eventsource``
#:     the Controller opens a server-sent-events stream for live status, issued by
#:     its own script exactly like any other call.
#: ``ping``
#:     how Chromium reports ``navigator.sendBeacon``. The Controller uses a beacon
#:     on ``beforeunload`` to release its privacy reservation, precisely so the call
#:     outlives the page teardown — so it appears during a *navigation* step, which
#:     is when leaving the page is the whole point.
#:
#: A request the driver issued itself would carry none of these, which is what makes
#: this check the real anti-cheating guard rather than the interaction rule below.
_PAGE_ORIGINATED = frozenset({"fetch", "xhr", "eventsource", "ping"})

#: Resolves a step's trace ids to the application's own trace records.
#:
#: A callable rather than an interface the core imports, so ``core/`` never learns
#: where a particular application keeps its trace or what its records look like.
TraceResolver = Callable[
    [Sequence[str]],
    tuple[tuple[Mapping[str, object], ...], CorrelationKind],
]

#: Methods that change server state. Only these need to sit inside a step that
#: interacted with the page.
#:
#: The distinction matters because a page issues GETs on its own schedule — the
#: status stream reconnects, a keepalive lands — and those legitimately occur
#: during a read-only verb. A *write* during a verb that touched nothing is the
#: thing actually worth catching.
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class Step:
    """The mutable accumulator for one interaction.

    A verb receives this, tells it what it examined and what it waited for, and
    the wrapper turns that into exactly one journal record.
    """

    def __init__(self, seq: int, verb: str, directory: Path) -> None:
        self.seq = seq
        self.verb = verb
        self.directory = directory
        self.intent = ""
        self.args: dict[str, str] = {}
        self.preconditions: list[str] = []
        self.waits: list[WaitRecord] = []
        self.observed: dict[str, object] = {}
        self.artifacts: dict[str, str] = {}
        self.interacted = False
        self.correlation = CorrelationKind.UNAVAILABLE
        #: Dialog messages a verb drained itself. A verb that races
        #: navigation-against-guard must read the alert text to report it, which
        #: consumes it; recording it here keeps it in the journal rather than
        #: trading the evidence for the exception message.
        self.dialogs: list[str] = []

    def describe(self, intent: str) -> None:
        """Record what this step is trying to achieve, in the agent's words."""
        self.intent = normalise_text(intent)

    def arg(self, name: str, value: object) -> None:
        """Record an argument the verb was called with."""
        self.args[name] = normalise_text(str(value))

    def examined(self, control: Control) -> None:
        """Record a control that was classified before acting.

        Every verb does this, so the journal shows not just what was clicked but
        what was looked at and what state it was in — including the controls that
        were found refused.
        """
        self.preconditions.append(control.describe())

    def note(self, key: str, value: object) -> None:
        """Record an observation for the journal's ``observed`` block."""
        self.observed[key] = value

    def waited(self, signal: WaitSignal, outcome: WaitOutcome) -> None:
        """Record a named wait and what it saw."""
        self.waits.append(wait_record(signal, outcome))

    def touched(self) -> None:
        """Declare that this step actually interacted with the page.

        Requests are only legitimate inside a step that did. Verbs that merely
        read never call this, so any traffic during them is a violation.
        """
        self.interacted = True


class Recorder:
    """Owns the surface, the journal, and the fidelity ledger for one run."""

    def __init__(
        self,
        surface: Surface,
        journal: Journal,
        adapter: AppAdapter,
        *,
        capture_before: bool = True,
        trace_resolver: TraceResolver | None = None,
    ) -> None:
        self._surface = surface
        self._journal = journal
        self._adapter = adapter
        self._capture_before = capture_before
        # Supplied by the application, which knows where its own trace lives. The
        # core stays ignorant of that: it only knows it can hand over a set of ids
        # and may get records back.
        self._trace_resolver = trace_resolver
        self._seq = 0
        self._ledger = FidelityLedger()
        self._dialogs_seen = 0

    # -- accessors ---------------------------------------------------------

    @property
    def journal(self) -> Journal:
        return self._journal

    @property
    def ledger(self) -> FidelityLedger:
        return self._ledger

    @property
    def sequence(self) -> int:
        """Number of steps recorded so far, for grouping tutorial lessons."""
        return self._seq

    @property
    def dialogs_seen(self) -> int:
        """Dialog count at the start of the current step, for race baselines."""
        return self._dialogs_seen

    def page_id(self) -> PageId:
        """Classify the current page through the adapter."""
        return self._adapter.identify_page(self._surface.url)

    # -- the wrapper -------------------------------------------------------

    @contextmanager
    def step(
        self,
        verb: str,
        *,
        intent: str = "",
        expects_navigation: bool = False,
        args: Mapping[str, object] | None = None,
    ) -> Iterator[Step]:
        """Run one interaction, recording it whatever the outcome.

        ``expects_navigation`` is how a step takes responsibility for moving the
        browser. It is not a hint — a navigation without it, or an absence of one
        with it, is reported.
        """
        self._seq += 1
        directory = self._journal.step_dir(self._seq, verb)
        step = Step(self._seq, verb, directory)
        if intent:
            step.describe(intent)
        for key, value in (args or {}).items():
            step.arg(key, value)

        nav_before = self._surface.navigation_count()
        pages_before = self._surface.page_count()
        self._dialogs_seen = self._surface.dialog_count()
        url_before = self._surface.url

        # Drain any residue so this step's evidence is genuinely this step's.
        self._surface.take_requests()
        self._surface.take_console()

        if self._capture_before:
            path = self._journal.write_png(
                directory, "before", self._surface.screenshot_png())
            step.artifacts["before"] = self._journal.relative(path)

        started = time.monotonic()
        outcome = StepOutcome.OK
        error_type = ""
        error_message = ""
        block_reason = ""

        try:
            yield step
        except ControlNotAvailable as exc:
            # A control the interface did not offer is a legitimate observation,
            # not a fault: it is precisely what a gating demonstration sets out to
            # record. All three cases land here — absent, hidden, and refused —
            # and ``error_type`` keeps them distinguishable, because "does not
            # apply" and "forbidden" say different things about what the operator
            # was shown.
            outcome = StepOutcome.BLOCKED
            error_type = type(exc).__name__
            error_message = exc.msg
            block_reason = exc.reason if isinstance(exc, BlockedControl) else ""
            raise
        except SelectionGuard as exc:
            outcome = StepOutcome.GUARDED
            error_type = type(exc).__name__
            error_message = exc.msg
            block_reason = exc.alert_text
            raise
        except AgentUiError as exc:
            outcome = StepOutcome.FAILED
            error_type = type(exc).__name__
            error_message = exc.msg
            raise
        except Exception as exc:                # noqa: BLE001 - recorded then re-raised
            outcome = StepOutcome.FAILED
            error_type = type(exc).__name__
            error_message = str(exc)
            raise
        finally:
            duration_ms = int((time.monotonic() - started) * 1000)
            self._finish(
                step,
                directory=directory,
                outcome=outcome,
                error_type=error_type,
                error_message=error_message,
                block_reason=block_reason,
                duration_ms=duration_ms,
                nav_before=nav_before,
                pages_before=pages_before,
                url_before=url_before,
                expects_navigation=expects_navigation,
            )

    # -- completion --------------------------------------------------------

    def _finish(
        self,
        step: Step,
        *,
        directory: Path,
        outcome: StepOutcome,
        error_type: str,
        error_message: str,
        block_reason: str,
        duration_ms: int,
        nav_before: int,
        pages_before: int,
        url_before: str,
        expects_navigation: bool,
    ) -> None:
        """Capture evidence, check invariants, and write the record.

        Runs in a ``finally``, so a failing step is journaled with the same rigour
        as a passing one — the failing step is usually the interesting one.
        """
        # Evidence first, and in this order. The after-image and the visible text
        # are captured before anything else because in this application an
        # action's own result can be overwritten by the next server-sent status
        # frame: the text and any error detail are transient, so a late capture
        # can show a plausible screen that no longer contains what happened.
        after_png = self._journal.write_png(
            directory, "after", self._surface.screenshot_png())
        step.artifacts["after"] = self._journal.relative(after_png)

        state_text = normalise_text(
            self._surface.visible_text(self._adapter.main_selector))
        state_path = self._journal.write_text_file(directory, "state", state_text)
        step.artifacts["state"] = self._journal.relative(state_path)

        if step.preconditions:
            controls_path = self._journal.write_text_file(
                directory, "controls", "\n".join(step.preconditions))
            step.artifacts["controls"] = self._journal.relative(controls_path)

        # Merge dialogs the verb already drained with any still buffered, so the
        # journal records them regardless of which side consumed them.
        dialogs = tuple(step.dialogs) + tuple(
            d.message for d in self._surface.take_dialogs())
        requests = self._surface.take_requests()
        console = self._surface.take_console()
        if console:
            console_path = self._journal.write_text_file(
                directory, "console", "\n".join(f"[{c.kind}] {c.text}" for c in console))
            step.artifacts["console"] = self._journal.relative(console_path)

        notes = tuple(
            RequestNote(method=r.method, path=r.path,
                        resource_type=r.resource_type, trace_id=r.trace_id)
            for r in requests
        )
        trace_ids = tuple(dict.fromkeys(
            r.trace_id for r in requests if r.trace_id))

        # Join to the application's own trace on the ids the page itself carried.
        # Only ever an exact id match: a time-window join looks convincing and
        # quietly attributes unrelated activity to this step.
        node_trace: tuple[Mapping[str, object], ...] = ()
        if self._trace_resolver is not None:
            node_trace, correlation = self._trace_resolver(trace_ids)
            # A verb may already have declared a known gap (a page with no
            # scripts cannot produce a client-side record); do not overwrite it.
            if step.correlation is CorrelationKind.UNAVAILABLE:
                step.correlation = correlation

        violations = self._check_invariants(
            step,
            outcome=outcome,
            requests=notes,
            nav_before=nav_before,
            pages_before=pages_before,
            url_before=url_before,
            expects_navigation=expects_navigation,
        )

        record = StepRecord(
            seq=step.seq,
            verb=step.verb,
            intent=step.intent,
            args=dict(step.args),
            page_id=self.page_id(),
            url=self._surface.url,
            preconditions=tuple(step.preconditions),
            waited_on=tuple(step.waits),
            observed=dict(step.observed),
            requests=notes,
            dialogs=dialogs,
            trace_ids=trace_ids,
            node_trace=node_trace,
            correlation=step.correlation,
            state_text=state_text,
            artifacts=dict(step.artifacts),
            outcome=StepOutcome.FAILED if violations else outcome,
            error_type=error_type or ("FidelityViolation" if violations else ""),
            error_message=error_message or "; ".join(violations),
            block_reason=block_reason,
            duration_ms=duration_ms,
        )
        self._journal.append(record)

        # Raised after the record is safely on disk, so the evidence for the
        # violation survives the exception that reports it.
        if violations:
            raise FidelityViolation("; ".join(violations))

    def _check_invariants(
        self,
        step: Step,
        *,
        outcome: StepOutcome,
        requests: tuple[RequestNote, ...],
        nav_before: int,
        pages_before: int,
        url_before: str,
        expects_navigation: bool,
    ) -> tuple[str, ...]:
        """Update the ledger and return any violations found in this step."""
        problems: list[str] = []

        navigations = self._surface.navigation_count() - nav_before
        unattributed = 0
        if navigations and not expects_navigation:
            unattributed = navigations
            problems.append(
                f"{navigations} unattributed navigation(s) during {step.verb!r}: "
                f"{url_before} -> {self._surface.url}. Only a step that declares "
                f"expects_navigation may move the browser."
            )

        extra_pages = self._surface.page_count() - pages_before
        if self._surface.page_count() > 1:
            problems.append(
                f"{self._surface.page_count()} browser pages exist; exactly one "
                f"is allowed. A backgrounded page silently stops receiving "
                f"status updates while still showing stale values."
            )

        # Provenance of any traffic to the application's own API. This is the real
        # anti-cheating check: a request the driver issued itself would not carry a
        # page-originated resource type at all.
        driver_requests = 0
        for request in requests:
            if _API_MARKER not in request.path:
                continue
            if request.resource_type not in _PAGE_ORIGINATED:
                driver_requests += 1
                problems.append(
                    f"request {request.method} {request.path} has resource_type "
                    f"{request.resource_type!r}; API traffic must originate from "
                    f"the page's own scripts"
                )
            elif request.method in _MUTATING_METHODS and not step.interacted:
                driver_requests += 1
                problems.append(
                    f"state-changing request {request.method} {request.path} "
                    f"occurred during {step.verb!r}, which never interacted with "
                    f"the page"
                )

        # A refusal must be free. If the driver managed to send something while
        # reporting that the control was blocked, then "blocked" was not true.
        if outcome is StepOutcome.BLOCKED and requests:
            problems.append(
                f"{step.verb!r} reported a blocked control but issued "
                f"{len(requests)} request(s); a refusal must cost nothing"
            )

        self._ledger = FidelityLedger(
            navigations=self._ledger.navigations + navigations,
            unattributed_navigations=(
                self._ledger.unattributed_navigations + unattributed),
            extra_pages=max(self._ledger.extra_pages, max(extra_pages, 0)),
            driver_requests=self._ledger.driver_requests + driver_requests,
            blanket_tls_bypass=self._ledger.blanket_tls_bypass,
        )
        return tuple(problems)

    def record_tls_bypass(self) -> None:
        """Mark that certificate verification was globally disabled.

        Exists so the launcher can report the condition rather than the ledger
        having to trust that it never happens. A run with this set cannot claim to
        have validated anything about the server's identity.
        """
        self._ledger = FidelityLedger(
            navigations=self._ledger.navigations,
            unattributed_navigations=self._ledger.unattributed_navigations,
            extra_pages=self._ledger.extra_pages,
            driver_requests=self._ledger.driver_requests,
            blanket_tls_bypass=True,
        )
