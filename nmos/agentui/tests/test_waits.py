# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for the wait specifications and the poll loop.

The cases that matter most here are the two that guard against a demo lying:

* a wait must not succeed because the elements it was watching *vanished*, and
* a wait must not report a live update on the strength of a marker that the
  server already rendered at page load.
"""

from __future__ import annotations

from ..core.surface import snapshot_of
from ..core.waits import (
    AllOf,
    AnyOf,
    Any_,
    Appears,
    AttrChangedFrom,
    ClassSetChangedFrom,
    DialogRaised,
    Disappears,
    Every,
    First,
    HasAnyClass,
    HasClass,
    IsChecked,
    LacksAttr,
    LacksClass,
    TextIs,
    UrlChangedFrom,
    all_terminal,
    attr_absent,
    checked,
    class_absent,
    class_present,
    wait_until,
)
from .fake_surface import FakeSurface

TOGGLE = "button.btn-toggle[data-action='activate']"
CELLS = ".result-cell[data-result-for]"


def _cell(resource: str, *classes: str, text: str = "",
          live: str = "false") -> object:
    """A result cell as the configure page renders it."""
    return snapshot_of(
        selector=CELLS, tag="td", text=text,
        classes=("result-cell", *classes),
        attrs={"data-result-for": resource, "data-live-active": live},
    )


class TestPresence:
    """Appears and Disappears."""

    def test_appears_satisfied_when_present(self) -> None:
        surface = FakeSurface({TOGGLE: [snapshot_of(selector=TOGGLE, tag="button")]})
        assert Appears(TOGGLE).evaluate(surface).satisfied

    def test_appears_unsatisfied_when_missing(self) -> None:
        assert not Appears(TOGGLE).evaluate(FakeSurface()).satisfied

    def test_appears_honours_count(self) -> None:
        surface = FakeSurface({CELLS: [_cell("a"), _cell("b")]})
        assert Appears(CELLS, count=2).evaluate(surface).satisfied
        assert not Appears(CELLS, count=3).evaluate(surface).satisfied

    def test_disappears(self) -> None:
        assert Disappears(TOGGLE).evaluate(FakeSurface()).satisfied


class TestVacuousSuccessGuard:
    """An empty match set must never satisfy a condition.

    This is the single most dangerous failure mode in the whole wait layer. A
    navigation detaches the DOM, so "no element still has ``.is-working``" is
    trivially true once the page has gone -- and a driver that accepted it would
    report an action as complete precisely because the evidence disappeared.
    """

    def test_every_rejects_empty_match_set(self) -> None:
        spec = Every(CELLS, HasAnyClass(frozenset({"ok", "error"})))
        result = spec.evaluate(FakeSurface())
        assert not result.satisfied
        assert "only 0" in result.observed

    def test_every_min_count_is_configurable(self) -> None:
        surface = FakeSurface({CELLS: [_cell("x", "ok")]})
        assert Every(CELLS, HasClass("ok"), min_count=1).evaluate(surface).satisfied
        assert not Every(CELLS, HasClass("ok"), min_count=2).evaluate(surface).satisfied

    def test_class_absent_requires_presence(self) -> None:
        # "The button no longer says is-working" must mean the button is there
        # and idle -- not that the button is gone.
        empty = FakeSurface()
        assert not class_absent(TOGGLE, "is-working").evaluate(empty).satisfied

        idle = FakeSurface({TOGGLE: [snapshot_of(
            selector=TOGGLE, tag="button", classes=("btn", "btn-toggle"))]})
        assert class_absent(TOGGLE, "is-working").evaluate(idle).satisfied

    def test_first_rejects_absence(self) -> None:
        assert not First(TOGGLE, LacksClass("x")).evaluate(FakeSurface()).satisfied

    def test_attr_absent_requires_presence(self) -> None:
        detail = "tr[data-caps-details-for='s1-0']"
        assert not attr_absent(detail, "hidden").evaluate(FakeSurface()).satisfied

        revealed = FakeSurface({detail: [snapshot_of(
            selector=detail, tag="tr", attrs={"data-caps-details-for": "s1-0"})]})
        assert attr_absent(detail, "hidden").evaluate(revealed).satisfied


class TestTerminalResults:
    """The toggle action's real completion signal."""

    def test_pending_cells_are_not_terminal(self) -> None:
        surface = FakeSurface({CELLS: [
            _cell("a", "ok", text="OK (200)"),
            _cell("b", "pending", text="…"),
        ]})
        result = all_terminal(CELLS, ("ok", "error")).evaluate(surface)
        assert not result.satisfied
        assert "1/2" in result.observed

    def test_mixed_ok_and_error_is_terminal(self) -> None:
        # An error is a legitimate ending: the action finished and the operator
        # can see what happened. Only "still pending" means keep waiting.
        surface = FakeSurface({CELLS: [
            _cell("a", "ok", text="OK (200)"),
            _cell("b", "error", text="HTTP 500"),
        ]})
        assert all_terminal(CELLS, ("ok", "error")).evaluate(surface).satisfied


class TestBaselineDeltas:
    """Only a change against a baseline evidences a server-sent update."""

    def test_unchanged_attribute_is_not_a_live_update(self) -> None:
        # ``data-live-active`` is rendered by Jinja at page load, so its mere
        # presence -- even with the value "true" -- proves nothing at all.
        surface = FakeSurface({CELLS: [_cell("a", live="true")]})
        spec = AttrChangedFrom(CELLS, "data-live-active", baseline="true")
        assert not spec.evaluate(surface).satisfied

    def test_changed_attribute_is_a_live_update(self) -> None:
        surface = FakeSurface({CELLS: [_cell("a", live="true")]})
        spec = AttrChangedFrom(CELLS, "data-live-active", baseline="false")
        result = spec.evaluate(surface)
        assert result.satisfied
        assert "true" in result.observed

    def test_absent_element_is_not_a_change(self) -> None:
        spec = AttrChangedFrom(CELLS, "data-live-active", baseline="false")
        assert not spec.evaluate(FakeSurface()).satisfied

    def test_health_class_delta_ignores_unrelated_classes(self) -> None:
        badge = ".status-badge[data-resource-id='r1']"
        surface = FakeSurface({badge: [snapshot_of(
            selector=badge, tag="span", text="idle",
            classes=("status-badge", "is-inactive", "some-layout-class"),
            attrs={"data-resource-id": "r1", "data-kind": "overall"},
        )]})
        # Same health, extra decoration -- not a change.
        same = ClassSetChangedFrom(badge, "is-", frozenset({"is-inactive"}))
        assert not same.evaluate(surface).satisfied

        # Different health -- a change.
        moved = ClassSetChangedFrom(badge, "is-", frozenset({"is-healthy"}))
        assert moved.evaluate(surface).satisfied


class TestLiveProperties:
    """Checked state is read from the live property, not the attribute."""

    def test_checked_true(self) -> None:
        box = "input.member-check[data-ids='r1']"
        surface = FakeSurface({box: [snapshot_of(
            selector=box, tag="input", checked=True, attrs={"data-ids": "r1"})]})
        assert checked(box).evaluate(surface).satisfied
        assert not checked(box, False).evaluate(surface).satisfied

    def test_unset_checked_is_neither(self) -> None:
        # A non-checkable element reports None, which must not read as False and
        # accidentally satisfy an "is unchecked" wait.
        plain = "div"
        surface = FakeSurface({plain: [snapshot_of(selector=plain, tag="div")]})
        assert not IsChecked(True).holds(surface.snapshot_all(plain)[0])
        assert not IsChecked(False).holds(surface.snapshot_all(plain)[0])


class TestCombinators:
    """AllOf, AnyOf, and branch reporting."""

    def test_all_of_requires_every_branch(self) -> None:
        surface = FakeSurface({TOGGLE: [snapshot_of(
            selector=TOGGLE, tag="button", classes=("btn-toggle",))]})
        spec = AllOf((Appears(TOGGLE), class_absent(TOGGLE, "is-working")))
        assert spec.evaluate(surface).satisfied

        spec_fail = AllOf((Appears(TOGGLE), class_present(TOGGLE, "is-working")))
        assert not spec_fail.evaluate(surface).satisfied

    def test_any_of_reports_winning_branch(self) -> None:
        # The submit race: either the browser moved, or a guard alert fired.
        # Which one won determines how the step is recorded, so the name matters.
        surface = FakeSurface(url="http://host/controller/senders")
        surface.raise_dialog("Please pick exactly 1 sender.")
        spec = AnyOf((
            ("navigated", UrlChangedFrom("http://host/controller/senders")),
            ("guarded", DialogRaised()),
        ))
        result = spec.evaluate(surface)
        assert result.satisfied
        assert result.branch == "guarded"

    def test_any_of_prefers_earlier_branch(self) -> None:
        surface = FakeSurface(url="http://host/after")
        surface.raise_dialog("also fired")
        spec = AnyOf((
            ("navigated", UrlChangedFrom("http://host/before")),
            ("guarded", DialogRaised()),
        ))
        assert spec.evaluate(surface).branch == "navigated"

    def test_any_of_unsatisfied_reports_all_observations(self) -> None:
        surface = FakeSurface(url="http://host/same")
        spec = AnyOf((
            ("navigated", UrlChangedFrom("http://host/same")),
            ("guarded", DialogRaised()),
        ))
        result = spec.evaluate(surface)
        assert not result.satisfied
        assert "navigated" in result.observed and "guarded" in result.observed


class TestDialogRaisedDoesNotConsume:
    """Polling must not eat the evidence it is polling for."""

    def test_repeated_evaluation_keeps_the_dialog(self) -> None:
        surface = FakeSurface()
        surface.raise_dialog("Please select one group or one or more senders.")
        spec = DialogRaised()
        assert spec.evaluate(surface).satisfied
        assert spec.evaluate(surface).satisfied
        # Only the owning step drains it, and the text is still intact.
        drained = surface.take_dialogs()
        assert len(drained) == 1
        assert "one group" in drained[0].message

    def test_since_baseline_ignores_earlier_dialogs(self) -> None:
        surface = FakeSurface()
        surface.raise_dialog("from an earlier step")
        assert not DialogRaised(since=1).evaluate(surface).satisfied
        surface.raise_dialog("from this step")
        assert DialogRaised(since=1).evaluate(surface).satisfied


class TestText:
    """Text comparison goes through the normaliser on both sides."""

    def test_text_is_normalises(self) -> None:
        surface = FakeSurface(texts={"h1": "  Administrator sign-in \n"})
        assert TextIs("h1", "Administrator sign-in").evaluate(surface).satisfied


class TestPollLoop:
    """wait_until: timing, early exit, and never raising."""

    def test_satisfied_immediately_does_not_sleep(self) -> None:
        # Server-rendered conditions are usually already true, so an
        # unconditional first sleep would tax every step in the run.
        surface = FakeSurface({TOGGLE: [snapshot_of(selector=TOGGLE, tag="button")]})
        outcome = wait_until(surface, Appears(TOGGLE),
                             timeout_ms=1000, clock=surface.clock)
        assert outcome.satisfied
        assert surface.polls == 0
        assert outcome.waited_ms == 0

    def test_becomes_true_partway_through(self) -> None:
        surface = FakeSurface({TOGGLE: [snapshot_of(
            selector=TOGGLE, tag="button", classes=("btn-toggle", "is-working"))]})

        def clear_working(s: FakeSurface) -> None:
            s.set_elements(TOGGLE, [snapshot_of(
                selector=TOGGLE, tag="button", classes=("btn-toggle",))])

        surface.at_poll(3, clear_working)
        outcome = wait_until(surface, class_absent(TOGGLE, "is-working"),
                             timeout_ms=5000, poll_ms=50, clock=surface.clock)
        assert outcome.satisfied
        assert surface.polls == 3
        assert outcome.waited_ms == 150

    def test_timeout_returns_unsatisfied_rather_than_raising(self) -> None:
        # The caller decides what an unmet wait means: a missing page marker is a
        # failure, an unobserved status update is an honest "unconfirmed".
        surface = FakeSurface()
        outcome = wait_until(surface, Appears(TOGGLE),
                             timeout_ms=200, poll_ms=50, clock=surface.clock)
        assert not outcome.satisfied
        assert outcome.waited_ms >= 200

    def test_outcome_records_spec_and_observation(self) -> None:
        surface = FakeSurface({CELLS: [_cell("a", "pending", text="…")]})
        outcome = wait_until(surface, all_terminal(CELLS, ("ok", "error")),
                             timeout_ms=100, poll_ms=50, clock=surface.clock)
        assert not outcome.satisfied
        assert "Every" in outcome.spec
        assert "failing" in outcome.observed

    def test_branch_is_carried_into_the_outcome(self) -> None:
        surface = FakeSurface(url="http://host/same")
        surface.at_poll(2, lambda s: s.raise_dialog("guard fired"))
        outcome = wait_until(
            surface,
            AnyOf((("navigated", UrlChangedFrom("http://host/same")),
                   ("guarded", DialogRaised()))),
            timeout_ms=5000, poll_ms=50, clock=surface.clock,
        )
        assert outcome.satisfied
        assert outcome.branch == "guarded"


class TestFakeSurfaceSatisfiesProtocol:
    """The fake must remain a structural Surface as the protocol grows."""

    def test_isinstance_check(self) -> None:
        from ..core.surface import Surface
        assert isinstance(FakeSurface(), Surface)


class TestAnyPredicate:
    """Any_ is satisfied by a single holding element."""

    def test_any_matches_one_of_several(self) -> None:
        surface = FakeSurface({CELLS: [
            _cell("a", "pending"), _cell("b", "error"),
        ]})
        assert Any_(CELLS, HasClass("error")).evaluate(surface).satisfied
        assert not Any_(CELLS, HasClass("ok")).evaluate(surface).satisfied

    def test_lacks_attr_predicate(self) -> None:
        snapshot = snapshot_of(selector="tr", tag="tr",
                               attrs={"data-caps-details-for": "s1-0"})
        assert LacksAttr("hidden").holds(snapshot)
        assert not LacksAttr("data-caps-details-for").holds(snapshot)
