# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for the gating classification rules.

The fixtures here mirror the Controller's three real refusal idioms verbatim,
because the whole value of this classifier is that it keeps them distinguishable.
Each test names the template idiom it stands for so a future change to the UI can
be traced back to the rule it breaks.
"""

from __future__ import annotations

import pytest

from ..core.affordance import classify, control_kind
from ..core.surface import snapshot_of
from ..enums import Affordance, ControlKind
from ..errors import DisallowedAttribute


class TestAbsent:
    """A control the server did not render at all."""

    def test_none_snapshot_is_absent(self) -> None:
        control = classify("a[href$='/flow']", None)
        assert control.affordance is Affordance.ABSENT
        assert not control.usable

    def test_absent_is_not_conflated_with_blocked(self) -> None:
        # "Not applicable" and "present but forbidden" are different claims about
        # the operator's experience, and a gating demo that merged them would be
        # dishonest about what the UI communicated.
        absent = classify("x", None)
        blocked = classify("x", snapshot_of(
            selector="x", tag="span", text="flow",
            classes=("btn", "disabled"),
            attrs={"title": "Receiver is not subscribed to a sender"},
        ))
        assert absent.affordance is not blocked.affordance


class TestSpanIdiom:
    """``<span class="btn ... disabled">`` -- the row-action idiom.

    A span cannot carry a meaningful ``disabled`` attribute, so the CSS class is
    the only available signal. See ``partials/device_block.html``, where the
    "flow" action for an unsubscribed receiver is rendered this way.
    """

    def test_disabled_span_is_blocked(self) -> None:
        control = classify("tr[data-resource-id='r1'] span.btn", snapshot_of(
            selector="tr[data-resource-id='r1'] span.btn",
            tag="span",
            text="flow",
            classes=("btn", "btn-outline-secondary", "disabled"),
            # Note: enabled=True, because a span's disabled *property* is always
            # True regardless of the class. That is exactly why the class matters.
            enabled=True,
            attrs={"title": "Receiver is not subscribed to a sender"},
        ))
        assert control.affordance is Affordance.BLOCKED
        assert control.kind is ControlKind.SPAN
        assert control.reason == "Receiver is not subscribed to a sender"

    def test_span_without_disabled_class_is_enabled(self) -> None:
        control = classify("span.btn", snapshot_of(
            selector="span.btn", tag="span", text="flow", classes=("btn",),
        ))
        assert control.affordance is Affordance.ENABLED


class TestAnchorIdiom:
    """``<a href>`` row actions, and the defensive disabled-anchor case."""

    def test_anchor_with_href_is_enabled(self) -> None:
        control = classify("a", snapshot_of(
            selector="a", tag="a", text="transport",
            classes=("btn", "btn-outline-secondary"),
            attrs={"href": "/controller/senders/s1/transport",
                   "title": "Transport parameters (live)"},
        ))
        assert control.affordance is Affordance.ENABLED
        assert control.kind is ControlKind.ANCHOR
        # A title on an *enabled* control is a helpful hint, not a refusal.
        assert control.reason == "Transport parameters (live)"

    def test_disabled_class_on_anchor_is_blocked(self) -> None:
        control = classify("a", snapshot_of(
            selector="a", tag="a", text="flow", classes=("btn", "disabled"),
            attrs={"href": "/controller/receivers/r1/flow"},
        ))
        assert control.affordance is Affordance.BLOCKED


class TestNativeDisabled:
    """``<button disabled title>`` -- policy disabling a live control.

    Mirrors the configure pages' master toggles, whose ``title`` carries the
    server-computed block reason.
    """

    def test_disabled_button_is_blocked_with_reason(self) -> None:
        reason = ("Device ABC.SNX00001 does not expose the IS-11 "
                  "stream-compatibility API")
        control = classify("button[data-action='constrain']", snapshot_of(
            selector="button[data-action='constrain']",
            tag="button",
            text="Constrain",
            classes=("btn", "btn-toggle", "btn-toggle-off"),
            enabled=False,
            attrs={"data-action": "constrain", "aria-pressed": "false",
                   "disabled": "", "title": reason},
        ))
        assert control.affordance is Affordance.BLOCKED
        assert control.kind is ControlKind.BUTTON
        assert control.reason == reason

    def test_enabled_button_is_usable(self) -> None:
        control = classify("button[data-action='activate']", snapshot_of(
            selector="button[data-action='activate']",
            tag="button", text="Activate",
            classes=("btn", "btn-toggle", "btn-toggle-off"),
            enabled=True,
            attrs={"data-action": "activate", "aria-pressed": "false"},
        ))
        assert control.affordance is Affordance.ENABLED
        assert control.usable

    def test_disabled_select_is_blocked(self) -> None:
        control = classify("#privacy-mode", snapshot_of(
            selector="#privacy-mode", tag="select", enabled=False,
            attrs={"title": "Locked while a resource is active"},
        ))
        assert control.affordance is Affordance.BLOCKED
        assert control.kind is ControlKind.SELECT


class TestReadonlyPinnedValue:
    """``<input readonly>`` -- a value pinned by a native constraint set.

    The Controller renders a parameter that a native set fixes to one value as a
    readonly input. Such an input is *not* disabled, so a classifier that only
    consults the disabled property calls an unchangeable value editable — which is
    the wrong answer for a scenario asking whether it has any choice to make.
    """

    def test_readonly_input_is_blocked(self) -> None:
        control = classify(".param-input", snapshot_of(
            selector=".param-input", tag="input", value="1",
            classes=("param-input", "param-single", "flow-match"),
            # Readonly, but genuinely enabled -- exactly as the page renders it.
            enabled=True,
            attrs={"readonly": "", "data-param-urn":
                   "urn:x-nmos:cap:format:channel_count"},
        ))
        assert control.affordance is Affordance.BLOCKED
        assert control.kind is ControlKind.INPUT

    def test_editable_input_is_enabled(self) -> None:
        control = classify(".param-input", snapshot_of(
            selector=".param-input", tag="input", value="2",
            classes=("param-input",), enabled=True))
        assert control.affordance is Affordance.ENABLED

    def test_readonly_is_ignored_where_meaningless(self) -> None:
        # readonly has no effect on a button, so it must not be read as a refusal.
        control = classify("button", snapshot_of(
            selector="button", tag="button", text="Constrain",
            enabled=True, attrs={"readonly": ""}))
        assert control.affordance is Affordance.ENABLED


class TestShapeShifter:
    """Reverse-direction links change tag with their state.

    ``receivers_configure.html`` renders ``<button disabled>`` when the group
    cannot be resolved and ``<a href>`` when it can. Both carry
    ``data-reverse-group``, so the group is locatable either way -- but the
    classification must follow the tag.
    """

    def test_blocked_form_is_button(self) -> None:
        control = classify("[data-reverse-group='g1']", snapshot_of(
            selector="[data-reverse-group='g1']", tag="button",
            text="USB reverse", enabled=False,
            attrs={"data-reverse-group": "g1", "disabled": "",
                   "title": "No reverse-direction pair could be resolved"},
        ))
        assert control.affordance is Affordance.BLOCKED
        assert control.kind is ControlKind.BUTTON

    def test_available_form_is_anchor(self) -> None:
        control = classify("[data-reverse-group='g2']", snapshot_of(
            selector="[data-reverse-group='g2']", tag="a", text="USB reverse",
            attrs={"data-reverse-group": "g2",
                   "href": "/controller/receivers/configure?ids=x"},
        ))
        assert control.affordance is Affordance.ENABLED
        assert control.kind is ControlKind.ANCHOR


class TestVisibility:
    """Invisibility outranks refusal, because it is the truthful report."""

    def test_hidden_takes_precedence_over_disabled(self) -> None:
        # Claiming BLOCKED here would assert the operator was shown a greyed
        # control and given a reason. They were shown nothing.
        control = classify("[data-role='privacy-locked-note']", snapshot_of(
            selector="[data-role='privacy-locked-note']", tag="button",
            text="hidden thing", visible=False, enabled=False,
            attrs={"hidden": "", "title": "a reason nobody can see"},
        ))
        assert control.affordance is Affordance.HIDDEN

    def test_hidden_span_is_hidden_not_blocked(self) -> None:
        control = classify("span", snapshot_of(
            selector="span", tag="span", classes=("btn", "disabled"),
            visible=False,
        ))
        assert control.affordance is Affordance.HIDDEN


class TestControlKindMapping:
    """Tag-to-kind mapping, including the catch-all."""

    @pytest.mark.parametrize(("tag", "expected"), [
        ("a", ControlKind.ANCHOR),
        ("A", ControlKind.ANCHOR),
        ("button", ControlKind.BUTTON),
        ("input", ControlKind.INPUT),
        ("select", ControlKind.SELECT),
        ("textarea", ControlKind.TEXTAREA),
        ("span", ControlKind.SPAN),
        ("tr", ControlKind.OTHER),
    ])
    def test_mapping(self, tag: str, expected: ControlKind) -> None:
        assert control_kind(tag) is expected


class TestAttributeAllowlist:
    """The allowlist is enforced at construction and on read."""

    def test_construction_refuses_unlisted_attribute(self) -> None:
        # Refusing at construction matters: guarding only reads would still let
        # an off-list value be collected into the process.
        with pytest.raises(DisallowedAttribute, match="data-secret-payload"):
            snapshot_of(selector="x", tag="div",
                        attrs={"data-secret-payload": "leak"})

    def test_read_refuses_unlisted_attribute(self) -> None:
        snapshot = snapshot_of(selector="x", tag="div",
                               attrs={"data-resource-id": "r1"})
        with pytest.raises(DisallowedAttribute, match="onclick"):
            snapshot.attr("onclick")

    def test_allowed_attribute_round_trips(self) -> None:
        snapshot = snapshot_of(selector="x", tag="tr",
                               attrs={"data-resource-id": "abc-123"})
        assert snapshot.attr("data-resource-id") == "abc-123"
        assert snapshot.has_attr("data-resource-id")

    def test_absent_allowed_attribute_is_none(self) -> None:
        snapshot = snapshot_of(selector="x", tag="tr")
        assert snapshot.attr("title") is None
        assert not snapshot.has_attr("title")

    def test_boolean_attribute_presence_not_truthiness(self) -> None:
        # ``disabled=""`` is set-but-empty. Testing truthiness would read it as
        # absent, which is how a blocked control gets misreported as available.
        snapshot = snapshot_of(selector="x", tag="button", attrs={"disabled": ""})
        assert snapshot.has_attr("disabled")
        assert snapshot.attr("disabled") == ""


class TestReasonNormalisation:
    """Block reasons are normalised, because templates wrap them."""

    def test_multiline_title_is_collapsed(self) -> None:
        # ``_block_reason`` is built in a multi-line Jinja ``{% set %}`` block, so
        # the raw attribute arrives with newlines and indentation in it.
        control = classify("button", snapshot_of(
            selector="button", tag="button", enabled=False,
            attrs={"disabled": "", "title": (
                "\n        Device ABC.SNX00001 does not expose\n"
                "        the IS-11 stream-compatibility API\n      "
            )},
        ))
        assert control.reason == (
            "Device ABC.SNX00001 does not expose the IS-11 "
            "stream-compatibility API"
        )

    def test_nbsp_in_label_is_normalised(self) -> None:
        # The navigation bar renders ``Sign&nbsp;out``; a scenario matching on
        # "Sign out" must find it.
        control = classify("a.nav-link", snapshot_of(
            selector="a.nav-link", tag="a", text="Sign out",
            classes=("nav-link",), attrs={"href": "/controller/logout"},
        ))
        assert control.text == "Sign out"
