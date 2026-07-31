# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for the journal and the step wrapper.

The invariant tests here are the ones that matter: they are what stops a run from
producing a journal that reads like a successful demonstration of something it
never did.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ..core.adapter import Precondition, Target
from ..core.affordance import classify
from ..core.journal import FidelityLedger, Journal, new_run_id, wait_record
from ..core.step import Recorder
from ..core.surface import RequestRecord, Surface, snapshot_of
from ..core.waits import WaitOutcome
from ..enums import (
    CorrelationKind,
    PageId,
    SseVerdict,
    StepOutcome,
    TlsPolicy,
    WaitSignal,
)
from ..errors import BlockedControl, FidelityViolation, SelectionGuard
from .fake_surface import FakeSurface


class StubAdapter:
    """Minimal :class:`AppAdapter` for exercising the recorder."""

    name = "stub"
    main_selector = "main"

    def discover(self) -> Target:
        return Target(app="stub", scheme="http", host="127.0.0.1", port=5050)

    def entry_url(self, target: Target) -> str:
        return target.origin + "/"

    def identify_page(self, url: str) -> PageId:
        if url.endswith("/login"):
            return PageId.LOGIN
        if url.endswith("/senders"):
            return PageId.SENDERS
        return PageId.INDEX

    def authenticate(self, surface: Surface, credentials: object) -> None:
        """Not exercised by these tests."""

    def preconditions(self) -> tuple[Precondition, ...]:
        return ()


def _recorder(tmp_path: Path, surface: FakeSurface) -> Recorder:
    journal = Journal(tmp_path, scenario="unit", run_id="run-under-test")
    return Recorder(surface, journal, StubAdapter())


class TestRunId:
    """Run identifiers sort chronologically and never collide."""

    def test_scenario_and_timestamp_present(self) -> None:
        run_id = new_run_id("blocked-controls")
        assert "blocked-controls" in run_id
        assert run_id.endswith(tuple("0123456789abcdef"))

    def test_unsafe_characters_are_replaced(self) -> None:
        assert "/" not in new_run_id("a/b")

    def test_two_runs_differ(self) -> None:
        assert new_run_id("x") != new_run_id("x")


class TestJournalFiles:
    """The three artifacts and their layout."""

    def test_files_created(self, tmp_path: Path) -> None:
        journal = Journal(tmp_path, scenario="unit", run_id="r1")
        assert journal.markdown_path.exists()
        assert (tmp_path / "r1" / "steps").is_dir()

    def test_step_written_to_both_files(self, tmp_path: Path) -> None:
        surface = FakeSurface(texts={"main": "Senders"})
        recorder = _recorder(tmp_path, surface)
        with recorder.step("open_senders", intent="look at senders") as step:
            step.note("rows", 3)

        lines = recorder.journal.jsonl_path.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["verb"] == "open_senders"
        assert record["intent"] == "look at senders"
        assert record["observed"]["rows"] == 3
        assert record["outcome"] == StepOutcome.OK

        markdown = recorder.journal.markdown_path.read_text()
        assert "open_senders" in markdown
        assert "look at senders" in markdown

    def test_jsonl_flushed_per_step(self, tmp_path: Path) -> None:
        # A crash must not cost completed steps -- and the step that crashed is
        # usually the one worth reading.
        surface = FakeSurface()
        recorder = _recorder(tmp_path, surface)
        with recorder.step("first"):
            pass
        assert len(recorder.journal.jsonl_path.read_text().strip().splitlines()) == 1

    def test_screenshots_and_state_written(self, tmp_path: Path) -> None:
        surface = FakeSurface(texts={"main": "  Administrator  sign-in \n"})
        recorder = _recorder(tmp_path, surface)
        with recorder.step("sign_in"):
            pass
        record = recorder.journal.records[0]
        assert record.artifacts["before"].endswith("before.png")
        assert record.artifacts["after"].endswith("after.png")
        # State text is normalised on the way in, so the journal and any
        # comparison against it agree.
        assert record.state_text == "Administrator sign-in"


class TestFailureIsRecorded:
    """A step that raises is journaled with the same rigour as one that does not."""

    def test_failed_step_recorded_then_reraised(self, tmp_path: Path) -> None:
        recorder = _recorder(tmp_path, FakeSurface())
        with pytest.raises(ValueError, match="boom"):
            with recorder.step("explode"):
                raise ValueError("boom")

        record = recorder.journal.records[0]
        assert record.outcome is StepOutcome.FAILED
        assert record.error_type == "ValueError"
        assert record.error_message == "boom"
        # The after-image still exists: evidence is captured in a finally block.
        assert record.artifacts["after"].endswith("after.png")


class TestBlockedIsNotFailure:
    """A refused control is an expected observation, not a broken run."""

    def test_blocked_control_records_reason_verbatim(self, tmp_path: Path) -> None:
        reason = "Receiver is not subscribed to a sender"
        recorder = _recorder(tmp_path, FakeSurface())
        with pytest.raises(BlockedControl):
            with recorder.step("open_row_action") as step:
                control = classify("span.btn", snapshot_of(
                    selector="span.btn", tag="span", text="flow",
                    classes=("btn", "disabled"), attrs={"title": reason}))
                step.examined(control)
                raise BlockedControl("refused", reason=reason,
                                     rendered_as=control.kind)

        record = recorder.journal.records[0]
        assert record.outcome is StepOutcome.BLOCKED
        assert record.block_reason == reason

        # The reason must be legible in the human artifact, because a tooltip
        # cannot be photographed.
        markdown = recorder.journal.markdown_path.read_text()
        assert reason in markdown

    def test_guard_alert_recorded_as_guarded(self, tmp_path: Path) -> None:
        surface = FakeSurface()
        surface.raise_dialog("Please pick exactly 1 sender.")
        recorder = _recorder(tmp_path, surface)
        with pytest.raises(SelectionGuard):
            with recorder.step("submit_selection") as step:
                step.touched()
                raise SelectionGuard("guarded",
                                     alert_text="Please pick exactly 1 sender.")

        record = recorder.journal.records[0]
        assert record.outcome is StepOutcome.GUARDED
        assert "Please pick exactly 1 sender." in record.dialogs


class TestNavigationAttribution:
    """A navigation must belong to a step that asked for one."""

    def test_expected_navigation_is_clean(self, tmp_path: Path) -> None:
        surface = FakeSurface()
        recorder = _recorder(tmp_path, surface)
        with recorder.step("open_senders", expects_navigation=True) as step:
            step.touched()
            surface.set_url("http://127.0.0.1:5050/controller/senders")
        assert recorder.ledger.unattributed_navigations == 0
        assert recorder.ledger.navigations == 1

    def test_unexpected_navigation_fails_the_run(self, tmp_path: Path) -> None:
        # This is the guard against a direct navigation being slipped in, and
        # against an unforeseen redirect being silently absorbed.
        surface = FakeSurface()
        recorder = _recorder(tmp_path, surface)
        with pytest.raises(FidelityViolation, match="unattributed navigation"):
            with recorder.step("read_rows"):
                surface.set_url("http://elsewhere/")

        assert recorder.ledger.unattributed_navigations == 1
        # The record is written before the violation is raised, so the evidence
        # for the violation survives it.
        record = recorder.journal.records[0]
        assert record.outcome is StepOutcome.FAILED
        assert "unattributed" in record.error_message


class TestSinglePageInvariant:
    """A second browser page silently kills live updates."""

    def test_extra_page_fails_the_run(self, tmp_path: Path) -> None:
        surface = FakeSurface()
        recorder = _recorder(tmp_path, surface)
        with pytest.raises(FidelityViolation, match="browser pages"):
            with recorder.step("read_page"):
                surface.open_extra_page()
        assert recorder.ledger.extra_pages >= 1


class TestRequestProvenance:
    """API traffic must come from the page, inside a step that interacted."""

    def test_page_issued_fetch_in_interacting_step_is_clean(self, tmp_path: Path) -> None:
        surface = FakeSurface()
        recorder = _recorder(tmp_path, surface)
        with recorder.step("press_toggle") as step:
            step.touched()
            surface.record_request(RequestRecord(
                method="POST",
                url="http://127.0.0.1:5050/controller/api/senders/s1/activate",
                path="/controller/api/senders/s1/activate",
                resource_type="fetch", trace_id="37d79dc05148"))

        assert recorder.ledger.driver_requests == 0
        record = recorder.journal.records[0]
        assert record.trace_ids == ("37d79dc05148",)

    def test_write_in_read_only_step_is_a_violation(self, tmp_path: Path) -> None:
        # A write during a verb that touched nothing is the signature of the driver
        # acting on its own rather than through the interface.
        surface = FakeSurface()
        recorder = _recorder(tmp_path, surface)
        with pytest.raises(FidelityViolation, match="never interacted"):
            with recorder.step("read_rows"):
                surface.record_request(RequestRecord(
                    method="POST",
                    url="http://127.0.0.1:5050/controller/api/senders/s1/activate",
                    path="/controller/api/senders/s1/activate",
                    resource_type="fetch"))
        assert recorder.ledger.driver_requests == 1

    def test_page_initiated_get_during_a_read_is_allowed(self, tmp_path: Path) -> None:
        # A page issues GETs on its own schedule, so requiring an interaction for
        # them would flag the application's normal behaviour as cheating.
        surface = FakeSurface()
        recorder = _recorder(tmp_path, surface)
        with recorder.step("read_rows"):
            surface.record_request(RequestRecord(
                method="GET",
                url="http://127.0.0.1:5050/controller/api/senders",
                path="/controller/api/senders",
                resource_type="fetch"))
        assert recorder.ledger.driver_requests == 0

    def test_unload_beacon_is_page_originated(self, tmp_path: Path) -> None:
        # The Controller releases its privacy reservation with navigator.sendBeacon
        # on beforeunload, which Chromium reports as resource_type "ping". It
        # therefore lands in whichever step navigates away -- and a state-changing
        # POST during a navigation is exactly what it is supposed to be.
        surface = FakeSurface()
        recorder = _recorder(tmp_path, surface)
        with recorder.step("open_senders", expects_navigation=True) as step:
            step.touched()
            surface.set_url("http://127.0.0.1:5050/controller/senders")
            surface.record_request(RequestRecord(
                method="POST",
                url="http://127.0.0.1:5050/controller/api/privacy/release?all=true",
                path="/controller/api/privacy/release",
                resource_type="ping"))
        assert recorder.ledger.driver_requests == 0
        assert recorder.ledger.clean

    def test_event_stream_is_page_originated(self, tmp_path: Path) -> None:
        # The Controller's live-status stream is opened by its own script. An
        # earlier version of this rule allowed only fetch/xhr and so reported the
        # target's own status stream as driver-issued traffic.
        surface = FakeSurface()
        recorder = _recorder(tmp_path, surface)
        with recorder.step("open_senders", expects_navigation=True) as step:
            step.touched()
            surface.set_url("http://127.0.0.1:5050/controller/senders")
            surface.record_request(RequestRecord(
                method="GET",
                url="http://127.0.0.1:5050/controller/api/status-events?ids=a",
                path="/controller/api/status-events",
                resource_type="eventsource"))
        assert recorder.ledger.driver_requests == 0
        assert recorder.ledger.clean

    def test_non_page_resource_type_is_a_violation(self, tmp_path: Path) -> None:
        surface = FakeSurface()
        recorder = _recorder(tmp_path, surface)
        with pytest.raises(FidelityViolation, match="must originate from"):
            with recorder.step("press_toggle") as step:
                step.touched()
                surface.record_request(RequestRecord(
                    method="POST",
                    url="http://127.0.0.1:5050/controller/api/x/activate",
                    path="/controller/api/x/activate",
                    resource_type="other"))

    def test_non_api_requests_are_ignored(self, tmp_path: Path) -> None:
        # Documents, stylesheets and images are how a page loads; only API calls
        # carry the provenance question.
        surface = FakeSurface()
        recorder = _recorder(tmp_path, surface)
        with recorder.step("open_senders", expects_navigation=True) as step:
            step.touched()
            surface.set_url("http://127.0.0.1:5050/controller/senders")
            surface.record_request(RequestRecord(
                method="GET", url="http://127.0.0.1:5050/controller/senders",
                path="/controller/senders", resource_type="document"))
            surface.record_request(RequestRecord(
                method="GET",
                url="http://127.0.0.1:5050/controller/static/controller.js",
                path="/controller/static/controller.js",
                resource_type="script"))
        assert recorder.ledger.driver_requests == 0


class TestRefusalCostsNothing:
    """The machine-checkable form of "blocked really means blocked"."""

    def test_blocked_step_with_requests_is_a_violation(self, tmp_path: Path) -> None:
        surface = FakeSurface()
        recorder = _recorder(tmp_path, surface)
        with pytest.raises(FidelityViolation, match="refusal must cost nothing"):
            with recorder.step("press_toggle") as step:
                step.touched()
                surface.record_request(RequestRecord(
                    method="POST", url="http://h/controller/api/x/activate",
                    path="/controller/api/x/activate", resource_type="fetch"))
                raise BlockedControl("refused", reason="no IS-11")

    def test_blocked_step_without_requests_is_fine(self, tmp_path: Path) -> None:
        recorder = _recorder(tmp_path, FakeSurface())
        with pytest.raises(BlockedControl):
            with recorder.step("press_toggle"):
                raise BlockedControl("refused", reason="no IS-11")
        assert recorder.ledger.clean


class TestWaitRecords:
    """Awaited signals are named in the journal."""

    def test_wait_recorded_with_signal_name(self, tmp_path: Path) -> None:
        recorder = _recorder(tmp_path, FakeSurface())
        with recorder.step("press_toggle") as step:
            step.touched()
            step.waited(WaitSignal.TOGGLE_STARTED,
                        WaitOutcome(True, "ClassPresent(is-working)", 14,
                                    "1/1 match(es)"))
            step.waited(WaitSignal.RESULTS_TERMINAL,
                        WaitOutcome(True, "Every(.result-cell ...)", 640,
                                    "all 2 match(es)"))

        record = recorder.journal.records[0]
        assert [w.signal for w in record.waited_on] == [
            WaitSignal.TOGGLE_STARTED, WaitSignal.RESULTS_TERMINAL]
        markdown = recorder.journal.markdown_path.read_text()
        assert "toggle_started" in markdown

    def test_wait_record_adapts_outcome(self) -> None:
        record = wait_record(WaitSignal.PAGE_LOADED,
                            WaitOutcome(False, "Appears(x)", 5000, "0 match(es)"))
        assert not record.satisfied
        assert record.waited_ms == 5000


class TestManifest:
    """The manifest is what keeps a run from overstating itself."""

    def test_clean_run_manifest(self, tmp_path: Path) -> None:
        recorder = _recorder(tmp_path, FakeSurface())
        with recorder.step("read_page"):
            pass
        path = recorder.journal.finalise(
            target=Target(app="stub", scheme="http", host="127.0.0.1",
                          port=5050, tls=TlsPolicy.PLAINTEXT).to_json(),
            environment={"playwright_version": "1.62.0"},
            fidelity=recorder.ledger,
            sse=SseVerdict.NOT_EXERCISED,
            mutating=False,
            debug_tracing=True,
        )
        manifest = json.loads(path.read_text())
        assert manifest["fidelity_clean"] is True
        assert manifest["fidelity"]["unattributed"] == 0
        assert manifest["fidelity"]["driver_requests"] == 0
        assert manifest["sse"] == SseVerdict.NOT_EXERCISED
        assert manifest["steps"] == 1

    def test_unconfirmed_sse_is_stated_in_words(self, tmp_path: Path) -> None:
        # "unconfirmed" in JSON is easy to skim past, and the difference between
        # it and "confirmed" is the difference between proving live updating and
        # merely hoping for it.
        recorder = _recorder(tmp_path, FakeSurface())
        recorder.journal.finalise(
            target={}, environment={}, fidelity=recorder.ledger,
            sse=SseVerdict.UNCONFIRMED, mutating=False, debug_tracing=True)
        markdown = recorder.journal.markdown_path.read_text()
        assert "unconfirmed" in markdown
        assert "nothing here evidences live updating" in markdown

    def test_mutating_run_is_announced(self, tmp_path: Path) -> None:
        recorder = _recorder(tmp_path, FakeSurface())
        recorder.journal.finalise(
            target={}, environment={}, fidelity=recorder.ledger,
            sse=SseVerdict.CONFIRMED, mutating=True, debug_tracing=True)
        markdown = recorder.journal.markdown_path.read_text()
        assert "This run made changes" in markdown
        assert "left in the state it reached" in markdown

    def test_diagnostic_api_use_is_flagged(self, tmp_path: Path) -> None:
        recorder = _recorder(tmp_path, FakeSurface())
        recorder.journal.finalise(
            target={}, environment={}, fidelity=recorder.ledger,
            sse=SseVerdict.NOT_EXERCISED, mutating=False, debug_tracing=True,
            diagnostic_api_used=True)
        assert "diagnostic API" in recorder.journal.markdown_path.read_text()

    def test_tls_bypass_marks_ledger_unclean(self, tmp_path: Path) -> None:
        recorder = _recorder(tmp_path, FakeSurface())
        recorder.record_tls_bypass()
        assert not recorder.ledger.clean
        assert recorder.ledger.blanket_tls_bypass


class TestFidelityLedger:
    """The ledger's cleanliness rule."""

    def test_clean_by_default(self) -> None:
        assert FidelityLedger().clean

    def test_navigations_alone_do_not_dirty_it(self) -> None:
        # Navigating is what an operator does; it is only *unattributed*
        # navigation that is a problem.
        assert FidelityLedger(navigations=12).clean

    @pytest.mark.parametrize("kwargs", [
        {"unattributed_navigations": 1},
        {"extra_pages": 1},
        {"driver_requests": 1},
        {"blanket_tls_bypass": True},
    ])
    def test_any_violation_dirties_it(self, kwargs: dict[str, object]) -> None:
        assert not FidelityLedger(**kwargs).clean  # type: ignore[arg-type]


class TestCorrelationKind:
    """The known-gap case is representable."""

    def test_server_only_is_explained_in_markdown(self, tmp_path: Path) -> None:
        recorder = _recorder(tmp_path, FakeSurface())
        with recorder.step("sign_in") as step:
            step.touched()
            step.correlation = CorrelationKind.SERVER_ONLY
        markdown = recorder.journal.markdown_path.read_text()
        assert "loads no JavaScript" in markdown


class TestStubAdapterSatisfiesProtocol:
    """The stub must remain a structural AppAdapter as the protocol grows."""

    def test_isinstance(self) -> None:
        from ..core.adapter import AppAdapter
        assert isinstance(StubAdapter(), AppAdapter)
