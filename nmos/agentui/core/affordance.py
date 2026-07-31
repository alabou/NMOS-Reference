# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Deciding what a located control offers the operator, before touching it.

Every verb classifies before acting. That ordering is not defensive
programming — it is what keeps a policy decision distinguishable from a bug.

Playwright's own auto-waiting actively works against this. Clicking a
``button[disabled]`` blocks for the full timeout and then raises a generic
timeout, and clicking a ``span.btn.disabled`` raises a hit-target error because
Bootstrap sets ``pointer-events: none`` on it. Both surface as flakiness. Neither
mentions that the server deliberately refused the action, or why. Classifying
first turns "the automation is flaky" into "the Controller says: *this receiver
is not subscribed to a sender*".

The Controller expresses refusal in three different ways, and this module's job
is to read all three without flattening them:

===========================  ==========================================
``<button disabled title>``  policy disabled a live control
``<span class="disabled">``  the action does not apply to this row --
                             a ``<span>`` cannot carry ``disabled``
``<input readonly>``         a value pinned by a native constraint set:
                             visible, enabled, and unchangeable
absent from the DOM          not applicable at all
===========================  ==========================================

plus one element that changes tag depending on its state: the reverse-direction
links render as ``<button disabled>`` when the group cannot be resolved and
``<a href>`` when it can.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..enums import Affordance, ControlKind
from .surface import ElementSnapshot
from .text import normalise_text

#: The CSS class Bootstrap uses to render a non-interactive link. Applied to
#: ``<span>`` and occasionally ``<a>``; it is the *only* signal available for
#: those, since neither can carry a meaningful ``disabled`` attribute.
DISABLED_CLASS = "disabled"

#: Tags whose ``disabled`` property is real and authoritative.
_NATIVELY_DISABLEABLE = frozenset({"button", "input", "select", "textarea", "option"})

#: Tags where ``readonly`` genuinely prevents the operator changing the value.
#: Meaningless on a button or a checkbox, so it is only consulted for these.
_READONLY_MEANINGFUL = frozenset({"input", "textarea"})

_TAG_TO_KIND = {
    "a": ControlKind.ANCHOR,
    "button": ControlKind.BUTTON,
    "input": ControlKind.INPUT,
    "select": ControlKind.SELECT,
    "textarea": ControlKind.TEXTAREA,
    "span": ControlKind.SPAN,
}


def control_kind(tag: str) -> ControlKind:
    """Map an HTML tag name to the recorded control kind."""
    return _TAG_TO_KIND.get(tag.lower(), ControlKind.OTHER)


@dataclass(frozen=True, slots=True)
class Control:
    """A classified control, ready to act on or to refuse.

    ``reason`` is the server's ``title`` verbatim. It is carried as text rather
    than left to the screenshot because native tooltips are drawn by the
    operating system and never appear in a captured image — a picture can show
    that a button is greyed out but can never show *why*.
    """

    affordance: Affordance
    kind: ControlKind
    selector: str
    text: str = ""
    reason: str = ""
    snapshot: ElementSnapshot | None = None
    #: Where a click should actually land, when that is not the element carrying
    #: the state. Empty means "the same element".
    #:
    #: Needed because some styled controls are split in two. A Bootstrap
    #: ``custom-switch`` keeps ``checked``/``disabled`` on an ``<input>`` that the
    #: styling deliberately makes unclickable, and puts the visible, clickable
    #: affordance on the wrapping ``<label>``. Probing or clicking the input would
    #: fail on a control the operator uses without difficulty.
    action_selector: str = ""

    @property
    def target(self) -> str:
        """The selector to click, type into, or probe for actionability."""
        return self.action_selector or self.selector

    @property
    def usable(self) -> bool:
        """Whether acting on this control is legitimate."""
        return self.affordance is Affordance.ENABLED

    def describe(self) -> str:
        """One-line rendering for the journal's precondition list."""
        label = self.text or self.selector
        if self.reason:
            return f"{label} [{self.kind}:{self.affordance}] — {self.reason}"
        return f"{label} [{self.kind}:{self.affordance}]"


def classify(selector: str, snapshot: ElementSnapshot | None) -> Control:
    """Decide what ``snapshot`` offers, without touching the browser.

    Pure over the observation, so the whole rule set is unit-testable against
    fixtures with no browser involved.

    Ordering, and why it is this order:

    1. **Absent** first — nothing was rendered, so there is nothing to say
       about state.
    2. **Not visible** next, ahead of the refusal checks. This departs from the
       obvious "check disabled first" instinct on purpose: reporting ``BLOCKED``
       for something the operator cannot even see would claim they were shown a
       greyed-out control and told why, when in fact they were shown nothing.
       ``HIDDEN`` is the truthful answer, and truthfulness about what the
       operator perceived is the entire point of this driver.
    3. **Class-based refusal** for ``<span>`` and ``<a>``, which have no usable
       ``disabled`` property.
    4. **Property-based refusal** for genuinely disableable elements, read from
       the live property so a JavaScript-applied ``disabled`` counts.
    5. Otherwise enabled.

    Physical reachability — obscured by an overlay, zero-sized, or
    ``pointer-events: none`` for reasons other than the disabled class — is
    deliberately *not* decided here. It is not a policy statement, and it needs
    a live browser to determine; callers fold in ``Surface.is_actionable`` as a
    final pre-click check and report a failure there as ``HIDDEN``.
    """
    if snapshot is None:
        return Control(Affordance.ABSENT, ControlKind.OTHER, selector)

    kind = control_kind(snapshot.tag)
    text = normalise_text(snapshot.text)
    reason = snapshot.reason

    if not snapshot.visible:
        return Control(Affordance.HIDDEN, kind, selector, text, reason, snapshot)

    # A span or anchor marked ``disabled`` by class. This is how the Controller
    # renders an inapplicable row action, and it is the only signal available:
    # neither element type honours a ``disabled`` attribute, so the live
    # ``enabled`` property reports True for both regardless.
    if kind in (ControlKind.SPAN, ControlKind.ANCHOR):
        if snapshot.has_class(DISABLED_CLASS):
            return Control(Affordance.BLOCKED, kind, selector, text, reason, snapshot)
        return Control(Affordance.ENABLED, kind, selector, text, reason, snapshot)

    # Genuinely disableable elements: trust the live property, which reflects
    # both the server's rendered attribute and any later JavaScript assignment.
    if snapshot.tag.lower() in _NATIVELY_DISABLEABLE and not snapshot.enabled:
        return Control(Affordance.BLOCKED, kind, selector, text, reason, snapshot)

    # A readonly text control: visible, enabled, and impossible to change. This is
    # how the Controller renders a parameter pinned to a single value by a native
    # constraint set. It is not "disabled" in any sense the DOM reports, so
    # checking only the disabled property calls an unchangeable value editable —
    # which is precisely the wrong answer for a scenario asking whether it has any
    # choice to make.
    if snapshot.tag.lower() in _READONLY_MEANINGFUL and snapshot.has_attr("readonly"):
        return Control(Affordance.BLOCKED, kind, selector, text, reason, snapshot)

    return Control(Affordance.ENABLED, kind, selector, text, reason, snapshot)
