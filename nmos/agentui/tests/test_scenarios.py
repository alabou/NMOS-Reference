# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for scenario decisions that do not need a browser."""

from __future__ import annotations

from typing import cast

from ..apps.nmos_controller.session import ControllerSession
from ..apps.nmos_controller.views import PageView, StatusView
from ..core.affordance import Control
from ..core.surface import ElementSnapshot
from ..enums import Affordance, ControlKind, PageId, ToggleAction
from ..errors import SelectionGuard
from ..scenarios import (
    _confirm_receiver_status,
    _ensure_toggle,
    _selection_guard,
    _toggle_state,
)


def _toggle_control(state: str) -> Control:
    snapshot = ElementSnapshot(
        selector='button[data-action="constrain"]',
        tag="button",
        text="Constrain",
        classes=frozenset(("btn", "btn-toggle", f"btn-toggle-{state}")),
        visible=True,
        enabled=True,
        _attrs={"data-action": "constrain", "aria-pressed": state},
    )
    return Control(
        Affordance.ENABLED,
        ControlKind.BUTTON,
        snapshot.selector,
        text=snapshot.text,
        snapshot=snapshot,
    )


class _ToggleSession:
    def __init__(self, state: str) -> None:
        self.state = state
        self.presses = 0
        self.notes: list[str] = []

    def read_toggles(self) -> dict[ToggleAction, Control]:
        return {ToggleAction.CONSTRAIN: _toggle_control(self.state)}

    def press_toggle(self, action: ToggleAction) -> None:
        assert action is ToggleAction.CONSTRAIN
        self.presses += 1
        # Browser contract: false -> true; true/mixed -> false.
        self.state = "true" if self.state == "false" else "false"

    def note(self, message: str) -> None:
        self.notes.append(message)


def test_toggle_state_preserves_mixed() -> None:
    assert _toggle_state(_toggle_control("true")) is True
    assert _toggle_state(_toggle_control("false")) is False
    assert _toggle_state(_toggle_control("mixed")) is None


def test_ensure_toggle_normalises_mixed_before_turning_all_on() -> None:
    fake = _ToggleSession("mixed")

    reached = _ensure_toggle(
        cast(ControllerSession, fake),
        ToggleAction.CONSTRAIN,
        True,
        why="apply one desired state to the selection",
    )

    assert reached is True
    assert fake.state == "true"
    assert fake.presses == 2


def test_ensure_toggle_normalises_mixed_to_off_in_one_press() -> None:
    fake = _ToggleSession("mixed")

    reached = _ensure_toggle(
        cast(ControllerSession, fake),
        ToggleAction.CONSTRAIN,
        False,
        why="make the selection safe to reconfigure",
    )

    assert reached is True
    assert fake.state == "false"
    assert fake.presses == 1


class _GuardSession:
    def __init__(self) -> None:
        self.submissions = 0
        self.notes: list[str] = []
        self.page = PageView(
            PageId.RECEIVERS,
            "http://controller/receivers",
        )

    def open_receivers(self) -> None:
        pass

    def clear_selection(self) -> None:
        pass

    def submit_selection(self) -> PageView:
        self.submissions += 1
        raise SelectionGuard(
            "empty selection", alert_text="Please select at least one receiver",
        )

    def read_page(self) -> PageView:
        return self.page

    def note(self, message: str) -> None:
        self.notes.append(message)


def test_selection_guard_exercises_a_reachable_empty_selection() -> None:
    fake = _GuardSession()

    _selection_guard(cast(ControllerSession, fake))

    assert fake.submissions == 1
    assert any("Please select at least one receiver" in note for note in fake.notes)


class _StatusSession:
    def __init__(self) -> None:
        self.awaited: list[str] = []
        self.notes: list[str] = []

    def read_status(self, resource_id: str) -> StatusView:
        return StatusView(resource_id, badge_text="idle")

    def await_live_status_change(self, *, resource_id: str) -> StatusView:
        self.awaited.append(resource_id)
        return StatusView(resource_id, badge_text="active")

    def note(self, message: str) -> None:
        self.notes.append(message)


def test_receiver_liveness_wait_targets_the_receiver_marker() -> None:
    fake = _StatusSession()

    status = _confirm_receiver_status(
        cast(ControllerSession, fake), "receiver-1", expected_active=True,
    )

    assert fake.awaited == ["receiver-1"]
    assert status.resource_id == "receiver-1"
    assert status.badge_text == "active"
