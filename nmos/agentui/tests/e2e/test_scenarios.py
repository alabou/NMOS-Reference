# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The read-only scenarios, run against a live node.

These assert on the *manifest and journal*, not on the UI's business behaviour.
The distinction matters: the Controller's own 2000-plus unit tests already cover
what it does. What is unproven without a browser is that this driver reports
honestly — that its fidelity ledger stays clean, that a refusal really costs
nothing, and that a liveness claim is never made without evidence.

Mutating scenarios are deliberately not run here. They issue real IS-05/IS-11
calls and, by design, perform no teardown, so they are not something a test suite
should do to a rig on its own initiative.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ...attach import attach_controller
from ...enums import PageId, SseVerdict, StepOutcome
from ...scenarios import SCENARIOS

#: Every test in this module needs a browser and a running node. Declared at
#: module level because a conftest's ``pytestmark`` does not propagate to tests.
pytestmark = pytest.mark.e2e

#: Scenarios safe for an unattended test run: none of them writes anything.
READ_ONLY = ("attach-and-look", "inspect-one-sender", "selection-guard",
             "blocked-controls", "session-lost")


def _run(name: str, artifacts_root: Path) -> dict[str, object]:
    """Run one scenario and return its manifest."""
    scenario = SCENARIOS[name]
    assert not scenario.mutating, f"{name} writes; not for an unattended run"

    with attach_controller(scenario=scenario.name,
                           artifacts_root=artifacts_root,
                           mutating=scenario.mutating) as session:
        journal = session.journal
        scenario.run(session)

    manifest = json.loads(Path(journal.manifest_path).read_text())
    assert isinstance(manifest, dict)
    return manifest


def _steps(manifest_path: Path) -> list[dict[str, object]]:
    """Read the step records beside a manifest."""
    jsonl = manifest_path.parent / "journal.jsonl"
    return [json.loads(line) for line in jsonl.read_text().splitlines() if line]


class TestReadOnlyScenarios:
    """Every read-only scenario completes with a clean fidelity ledger."""

    @pytest.mark.parametrize("name", READ_ONLY)
    def test_runs_clean(self, name: str, running_node: object,
                        artifacts_root: Path) -> None:
        manifest = _run(name, artifacts_root)

        assert manifest["error"] == ""
        fidelity = manifest["fidelity"]
        assert isinstance(fidelity, dict)
        # The four claims the run makes about having behaved like an operator.
        assert fidelity["unattributed"] == 0
        assert fidelity["extra_pages"] == 0
        assert fidelity["driver_requests"] == 0
        assert fidelity["blanket_tls_bypass"] is False
        assert manifest["fidelity_clean"] is True
        assert manifest["mutating"] is False

    @pytest.mark.parametrize("name", READ_ONLY)
    def test_no_step_failed(self, name: str, running_node: object,
                            artifacts_root: Path) -> None:
        manifest = _run(name, artifacts_root)
        outcomes = manifest["outcomes"]
        assert isinstance(outcomes, dict)
        # Blocked steps are expected -- they are what a gating demo records. A
        # *failed* step means the driver could not do what it set out to.
        assert outcomes.get(StepOutcome.FAILED.value, 0) == 0


class TestEvidenceQuality:
    """The journal has to be usable as evidence, not merely present."""

    def test_every_step_has_before_and_after_images(
        self, running_node: object, artifacts_root: Path,
    ) -> None:
        manifest = _run("attach-and-look", artifacts_root)
        root = artifacts_root / str(manifest["run_id"])
        for step in _steps(root / "manifest.json"):
            artifacts = step["artifacts"]
            assert isinstance(artifacts, dict)
            for key in ("before", "after", "state"):
                assert key in artifacts, f"step {step['seq']} lost its {key}"
                assert (root / str(artifacts[key])).is_file()

    def test_javascript_version_recorded(self, running_node: object,
                                         artifacts_root: Path) -> None:
        # A stale cached script would invalidate every wait signal the driver
        # relies on, so which version actually ran is part of the provenance.
        manifest = _run("attach-and-look", artifacts_root)
        assert str(manifest["controller_js_version"]).strip() != ""

    def test_named_waits_recorded(self, running_node: object,
                                  artifacts_root: Path) -> None:
        manifest = _run("attach-and-look", artifacts_root)
        root = artifacts_root / str(manifest["run_id"])
        waited = [
            wait for step in _steps(root / "manifest.json")
            for wait in step["waited_on"]  # type: ignore[union-attr]
        ]
        assert waited, "no step recorded what it waited on"
        # Every wait names its signal, so a reader can tell what the success of a
        # step actually rested on.
        assert all(w["signal"] for w in waited)


class TestBlockedControlsEvidence:
    """The gating demonstration's specific guarantees."""

    def test_a_refusal_is_recorded_and_costs_nothing(
        self, running_node: object, artifacts_root: Path,
    ) -> None:
        manifest = _run("blocked-controls", artifacts_root)
        root = artifacts_root / str(manifest["run_id"])
        steps = _steps(root / "manifest.json")

        blocked = [s for s in steps if s["outcome"] == StepOutcome.BLOCKED.value]
        if not blocked:
            pytest.skip(
                "this rig currently offers every control, so no refusal was "
                "available to demonstrate")

        for step in blocked:
            error = step["error"]
            assert isinstance(error, dict)
            # Distinguishable cause: "does not apply" is not "forbidden".
            assert error["type"] in ("BlockedControl", "ControlAbsent",
                                     "ControlHidden")
            # The machine-checkable form of "blocked really means blocked".
            assert step["requests"] == [], (
                f"step {step['seq']} reported a refusal but issued "
                f"{step['requests']}")

    def test_reason_is_text_not_only_a_tooltip(
        self, running_node: object, artifacts_root: Path,
    ) -> None:
        # Native tooltips are drawn by the operating system and never appear in a
        # screenshot, so a reason that exists only as a title attribute could not
        # be evidenced at all.
        manifest = _run("blocked-controls", artifacts_root)
        root = artifacts_root / str(manifest["run_id"])
        markdown = (root / "journal.md").read_text(encoding="utf-8")

        reasons = [
            str(s["error"]["reason"])  # type: ignore[index]
            for s in _steps(root / "manifest.json")
            if s["outcome"] == StepOutcome.BLOCKED.value
            and isinstance(s["error"], dict) and s["error"].get("reason")
        ]
        if not reasons:
            pytest.skip("no refusal with a stated reason on this rig")
        for reason in reasons:
            assert reason in markdown


class TestLivenessHonesty:
    """A live update is never claimed without evidence."""

    def test_read_only_run_never_claims_confirmed_sse(
        self, running_node: object, artifacts_root: Path,
    ) -> None:
        # None of the read-only scenarios waits for a status change, so the only
        # honest verdict is "not exercised". Reporting "confirmed" here would mean
        # the driver was crediting server-rendered markers as live updates.
        manifest = _run("attach-and-look", artifacts_root)
        assert manifest["sse"] == SseVerdict.NOT_EXERCISED.value


class TestCompatibleSendersShapes:
    """The compatible-senders page must never look empty when it is not.

    These are the checks that were missing. ``FakeSurface`` is a
    ``selector -> snapshots`` map, so no unit test can tell whether a selector
    matches the real markup; and the read-only scenarios above never visit this
    page, because every scenario that routes is mutating and deliberately
    excluded. Between those two facts, a verb that returned empty on a page
    full of selectable groups was invisible to the whole suite — and the
    scenarios then reported "no compatible senders" for a receiver that had
    several.

    Navigating here writes nothing: the page is reached with GET requests and
    no toggle is pressed, so this belongs in the unattended run.
    """

    @staticmethod
    def _first_receiver_of_a_sole_member_group(session: object) -> str | None:
        """A receiver that is the only member of its natural group.

        That is the case which promotes the submit to ``group`` mode -- the
        group radio goes on as soon as *all* of a group's members are ticked --
        and so produces the collapsed rendering.
        """
        for group in session.read_groups():  # type: ignore[attr-defined]
            if len(group.member_ids) == 1:
                return group.member_ids[0]
        return None

    def test_group_mode_is_reported_as_groups_not_as_nothing(
        self, running_node: object, artifacts_root: Path,
    ) -> None:
        from ...errors import GroupOnlyRendering

        with attach_controller(scenario="e2e-compatible-senders-group",
                               artifacts_root=artifacts_root) as session:
            session.open_receivers()
            session.clear_selection()
            sole = self._first_receiver_of_a_sole_member_group(session)
            if sole is None:
                pytest.skip("no sole-member receiver group on this rig")

            session.select_resource(resource_id=sole)
            page = session.submit_selection()
            if "mode=group" not in page.url:
                pytest.skip(f"selection did not promote to group mode: {page.url}")

            # The regression: this used to return () and every caller read that
            # as "no compatible sender exists".
            try:
                rows = session.read_rows()
            except GroupOnlyRendering as collapsed:
                assert collapsed.group_count > 0
                groups = session.read_groups()
                assert groups, (
                    "read_rows reported a collapsed page but read_groups found "
                    "nothing -- one of the two selectors is wrong")
                return
            # The other legal outcome: the page really did render member rows.
            assert rows, (
                "the page rendered neither member rows nor group radios, yet "
                "reported no collapse")

    def test_something_selectable_is_always_visible(
        self, running_node: object, artifacts_root: Path,
    ) -> None:
        """Whatever the shape, the two readers must not BOTH come back empty.

        The single cheapest guard against this class of bug: if the operator can
        see something to pick, at least one read verb has to be able to say so.
        """
        from ...errors import GroupOnlyRendering

        with attach_controller(scenario="e2e-compatible-senders-nonempty",
                               artifacts_root=artifacts_root) as session:
            session.open_receivers()
            session.clear_selection()
            receivers = session.read_rows()
            if not receivers:
                pytest.skip("no receivers on this rig")

            session.select_resource(resource_id=receivers[0].resource_id)
            page = session.submit_selection()
            assert page.page_id is PageId.RECEIVERS_COMPATIBLE_SENDERS

            try:
                rows = session.read_rows()
            except GroupOnlyRendering:
                rows = ()
            groups = session.read_groups()
            # An empty page is legitimate (no compatible sender). What is not
            # legitimate is the page showing groups while both readers say
            # nothing -- so tie the assertion to what is actually rendered.
            if "Configure capabilities" in page.text:
                assert rows or groups, (
                    "the page offers a submit button for a selection, yet "
                    "neither read_rows() nor read_groups() found anything to "
                    f"select. URL: {page.url}")


class TestSelectionFieldsAreTruthful:
    """``read_selection`` must describe what a submit would actually send.

    OPERATING-THE-CONTROLLER.md §4 tells readers the hidden fields are the
    truth and worth reading before a submit that matters. That only holds
    because ``initSelection`` now re-syncs them on every change; previously
    ``submitSelection`` was the sole writer, so before the click
    ``#receiver_ids`` was empty and ``#selection_mode`` still said ``single``
    while the submit went out as ``group``.
    """

    def test_hidden_fields_match_the_submitted_url(
        self, running_node: object, artifacts_root: Path,
    ) -> None:
        with attach_controller(scenario="e2e-selection-truthful",
                               artifacts_root=artifacts_root) as session:
            session.open_receivers()
            session.clear_selection()
            rows = session.read_rows()
            if not rows:
                pytest.skip("no receivers on this rig")

            session.select_resource(resource_id=rows[0].resource_id)
            before = session.read_selection()
            page = session.submit_selection()

            # What the page said it would send, before it sent anything.
            assert before.submitted_receiver_ids, (
                "#receiver_ids was empty while a receiver was ticked: the "
                "hidden fields are not being kept in sync")
            for resource_id in before.submitted_receiver_ids:
                assert resource_id in page.url
            assert f"mode={before.mode}" in page.url, (
                f"reported mode {before.mode!r} but submitted {page.url}")


class TestEntryLatch:
    """Only one direct navigation is permitted per run."""

    def test_second_entry_navigation_is_refused(self, running_node: object) -> None:
        from ...driver.launcher import BrowserRun
        from ...errors import FidelityViolation

        # Constructed without starting a browser: the latch is checked before the
        # context is touched, which is what this asserts.
        run = BrowserRun(pin=running_node.pin)  # type: ignore[attr-defined]
        run._entered = True                      # noqa: SLF001 - the latch itself
        with pytest.raises(FidelityViolation, match="second direct navigation"):
            run.enter("http://127.0.0.1:5050/controller/senders")
