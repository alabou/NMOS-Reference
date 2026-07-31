# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The browser capability whitelist.

:class:`Surface` is the *only* view of a browser that adapter and session code
ever receives. What makes it load-bearing is what it leaves out: there is no
``goto``, no ``evaluate``, no ``request``, no ``route``, no ``add_init_script``,
no ``content``, and no ``new_page``.

That omission is the mechanism, not a style choice. Session code is annotated
against this protocol, so an attempt to navigate directly to a URL, execute
arbitrary JavaScript, or call the Controller's JSON API behind the UI's back is
a ``mypy --strict`` failure at author time rather than something a reviewer has
to notice. The concrete playwright-backed implementation holds its ``Page`` in a
name-mangled attribute with no accessor, so the escape hatch does not exist at
runtime either.

The methods that *are* here correspond to things a person sitting at the browser
can do: look at the page, click, type, choose from a list, drag a slider, and
take a photograph of what is on screen.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..errors import DisallowedAttribute
from .text import normalise_text

# ---------------------------------------------------------------------------
# Attribute allowlist
# ---------------------------------------------------------------------------
#
# Reading the DOM is how the driver learns anything, so an unrestricted
# attribute read is an unrestricted private channel: an adapter could pull
# server-computed state out of a ``data-`` attribute the UI never displays and
# present it as "what the operator saw".
#
# The allowlist keeps reads to three honest purposes:
#
#   identity     -- which resource/parameter/group is this element about
#   gating       -- is this control refused, and what reason was given
#   wait markers -- has the page finished doing the thing that was asked
#
# Anything outside those purposes is refused by ``ElementSnapshot.attr``. The
# list is short enough to review, which is the point: growing it is a visible
# decision rather than an accident.

ALLOWED_ATTRS: frozenset[str] = frozenset({
    # Gating and the human-readable reason for it.
    #
    # ``readonly`` matters as much as ``disabled`` here: a native constraint set
    # pins one value per parameter, and the UI renders those pinned values as
    # readonly inputs. A readonly input is *not* disabled, so checking only
    # ``disabled`` reports an unchangeable value as editable.
    "title", "disabled", "readonly", "hidden", "href",
    # Wait markers written by controller.js.
    "aria-pressed", "data-live-active", "data-privacy-locked",
    # Resource and group identity.
    "data-resource-id", "data-ids", "data-kind", "data-node-ids",
    # Action identity on configure pages.
    "data-action",
    # Parameter-widget identity: the (sender, param, part) triple.
    "data-sender-id", "data-param-urn", "data-cs-part",
    # Constraint-set identity and detail-row pairing.
    "data-caps-row", "data-caps-details-for", "data-caps-receiver",
    "data-conset-index", "data-conset-hash",
    "data-cs-meta-format", "data-cs-meta-layer",
    # Result-cell pairing on configure pages.
    "data-result-for", "data-result-for-receiver",
    # Reverse-direction group identity (the button/anchor shape-shifter).
    "data-reverse-group",
    # Privacy panel roles and availability.
    "data-role", "data-exclusivity-available",
    # Flow-match option keys.
    "data-flow-key",
    # Whether the server enabled its own client-side tracing for this document.
    "data-debug",
})


# ---------------------------------------------------------------------------
# Read models
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ElementSnapshot:
    """An immutable observation of one element at one instant.

    Snapshots are values, not handles. A verb takes one, reasons about it, and
    if it needs a fresh view it takes another. That avoids the classic
    automation bug where a decision is made against a stale live handle whose
    underlying node has since been replaced by a navigation.

    ``enabled``, ``checked``, and ``value`` come from the browser's **live
    properties**, not from the corresponding HTML attributes. For a checkbox the
    ``checked`` attribute records the server's initial state while the property
    records what the operator has since done, so an attribute read would happily
    report the wrong answer after a click.
    """

    selector: str
    tag: str
    text: str
    classes: frozenset[str] = frozenset()
    visible: bool = False
    enabled: bool = True
    checked: bool | None = None
    value: str | None = None
    _attrs: Mapping[str, str] = field(default_factory=dict)

    def attr(self, name: str) -> str | None:
        """Return an allowlisted attribute, or ``None`` when it is absent.

        Raises :class:`DisallowedAttribute` for anything outside
        :data:`ALLOWED_ATTRS`. This is the structural guard: an adapter cannot
        read its way around the whitelist even by knowing the attribute name.
        """
        if name not in ALLOWED_ATTRS:
            raise DisallowedAttribute(
                f"attribute {name!r} is not in the agent-UI allowlist; reading "
                f"it would create a data channel the operator does not have"
            )
        return self._attrs.get(name)

    def has_attr(self, name: str) -> bool:
        """Report presence of an allowlisted attribute, including empty ones.

        Boolean HTML attributes such as ``disabled`` and ``hidden`` carry an
        empty value when set, so presence — not truthiness — is the question.
        """
        if name not in ALLOWED_ATTRS:
            raise DisallowedAttribute(
                f"attribute {name!r} is not in the agent-UI allowlist"
            )
        return name in self._attrs

    def has_class(self, name: str) -> bool:
        """Report whether the element carries a CSS class."""
        return name in self.classes

    @property
    def reason(self) -> str:
        """The control's ``title``, normalised — the server's stated reason.

        Empty when there is no title. This is surfaced as a property because it
        is read on essentially every gating decision.
        """
        return normalise_text(self._attrs.get("title"))


def snapshot_of(
    *,
    selector: str,
    tag: str,
    text: str = "",
    classes: Iterable[str] = (),
    visible: bool = True,
    enabled: bool = True,
    checked: bool | None = None,
    value: str | None = None,
    attrs: Mapping[str, str] | None = None,
) -> ElementSnapshot:
    """Build an :class:`ElementSnapshot`, validating attributes at the source.

    The allowlist is enforced here as well as on read, and that redundancy is
    deliberate. Guarding only reads would still let the driver *collect* an
    off-list attribute into the object, where it would sit in memory and in any
    debug dump — a channel that exists but happens not to be used yet. Refusing
    it at construction means the value never enters the process.
    """
    raw = dict(attrs or {})
    disallowed = sorted(set(raw) - ALLOWED_ATTRS)
    if disallowed:
        raise DisallowedAttribute(
            f"refusing to snapshot attribute(s) {', '.join(disallowed)} on "
            f"{selector!r}: not in the agent-UI allowlist"
        )
    return ElementSnapshot(
        selector=selector,
        tag=tag.lower(),
        text=normalise_text(text),
        classes=frozenset(classes),
        visible=visible,
        enabled=enabled,
        checked=checked,
        value=value,
        _attrs=raw,
    )


@dataclass(frozen=True, slots=True)
class SelectOption:
    """One ``<option>`` of a ``<select>``, as rendered.

    Carries classes and the flow-match key because the Controller marks options
    that correspond to a sender's current flow, and a scenario that reports
    "the current value was offered" needs to see that marking.
    """

    value: str
    label: str
    selected: bool = False
    disabled: bool = False
    classes: frozenset[str] = frozenset()
    flow_key: str | None = None


@dataclass(frozen=True, slots=True)
class DialogRecord:
    """A native browser dialog the page raised.

    The Controller uses ``window.alert()`` in two selection-guard paths. These
    block until dismissed, so a handler must be registered *before* any click
    that could trigger one; the text is recorded because it is exactly what the
    operator would have read.
    """

    kind: str
    message: str
    accepted: bool = True


@dataclass(frozen=True, slots=True)
class RequestRecord:
    """An HTTP request the *page* issued.

    Observed passively. ``resource_type`` is what proves provenance: a request
    the page's own JavaScript made is ``fetch``/``xhr``, while a top-level
    navigation is ``document``. A driver-issued request would appear as neither
    inside a step that clicked nothing, which is how the no-cheating invariant
    is checked after the fact.

    ``trace_id`` is lifted from the ``X-Trace-Id`` header the Controller's own
    ``apiFetch`` stamps, or from the client-event body it posts — never
    generated here, so a journal can only claim correlation that genuinely
    exists.
    """

    method: str
    url: str
    path: str
    resource_type: str
    trace_id: str | None = None


@dataclass(frozen=True, slots=True)
class ConsoleRecord:
    """A console message, used to confirm ``controller.js`` actually ran."""

    kind: str
    text: str


# ---------------------------------------------------------------------------
# The protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class Surface(Protocol):
    """Everything the driver is permitted to do to a browser.

    Implementations: the playwright-backed ``PwSurface``, and an in-memory fake
    used by the unit tests. Because the fake satisfies the same protocol, the
    affordance rules, wait evaluation, and journal writing are all testable
    without launching a browser at all.
    """

    # -- observation --------------------------------------------------------

    @property
    def url(self) -> str:
        """The current document's URL."""
        ...

    def title(self) -> str:
        """The current document's title."""
        ...

    def count(self, selector: str) -> int:
        """How many elements match ``selector``."""
        ...

    def snapshot(self, selector: str) -> ElementSnapshot | None:
        """Observe the first match, or ``None`` when nothing matches."""
        ...

    def snapshot_all(self, selector: str) -> tuple[ElementSnapshot, ...]:
        """Observe every match, in document order."""
        ...

    def options(self, selector: str) -> tuple[SelectOption, ...]:
        """List the options a ``<select>`` currently offers."""
        ...

    def visible_text(self, selector: str) -> str:
        """The rendered, human-visible text of a region.

        This is the source for assertion-bearing state. Reading what is on
        screen — rather than the JSON that produced it — is what makes a claim
        about the operator's experience honest.
        """
        ...

    def is_actionable(self, selector: str) -> bool:
        """Whether a real click would land, without performing one.

        Backed by playwright's trial-click actionability check, so it accounts
        for the things that stop a click in practice — zero size, ``visibility:
        hidden``, ``pointer-events: none`` — which no attribute reveals.
        """
        ...

    # -- interaction -------------------------------------------------------

    def click(self, selector: str) -> None:
        """Click an element, as a person would."""
        ...

    def type_text(self, selector: str, text: str) -> None:
        """Replace a field's contents by typing."""
        ...

    def select_options(self, selector: str, values: tuple[str, ...]) -> None:
        """Choose one or more options in a ``<select>``."""
        ...

    def set_range(self, selector: str, value: str) -> None:
        """Move a range slider to a value."""
        ...

    def check(self, selector: str) -> None:
        """Tick a checkbox or radio."""
        ...

    def uncheck(self, selector: str) -> None:
        """Untick a checkbox."""
        ...

    # -- synchronisation ---------------------------------------------------

    def wait_for_load_state(self) -> None:
        """Block until the current document has finished loading."""
        ...

    def sleep_ms(self, milliseconds: int) -> None:
        """Pause between poll iterations.

        Provided by the surface rather than called directly so the in-memory
        fake can advance a virtual clock, keeping the wait tests instant and
        deterministic.
        """
        ...

    # -- evidence ----------------------------------------------------------

    def screenshot_png(self) -> bytes:
        """Photograph the current viewport."""
        ...

    def dialog_count(self) -> int:
        """How many dialogs have been raised, without draining them.

        Separate from :meth:`take_dialogs` because a poll loop must be able to
        ask "has an alert appeared yet?" repeatedly without consuming the
        message. The wording of a guard alert is the evidence a scenario reports,
        so exactly one consumer — the step that owns the wait — drains it.
        """
        ...

    def take_dialogs(self) -> tuple[DialogRecord, ...]:
        """Drain dialogs seen since the last call."""
        ...

    def take_requests(self) -> tuple[RequestRecord, ...]:
        """Drain page-issued requests seen since the last call."""
        ...

    def take_console(self) -> tuple[ConsoleRecord, ...]:
        """Drain console messages seen since the last call."""
        ...

    def console_history(self) -> tuple[ConsoleRecord, ...]:
        """Every console message seen this run, without draining.

        Needed separately from :meth:`take_console` because the "did the
        application's JavaScript actually run" check looks for a message emitted
        at page load, which the per-step drain would already have consumed by the
        time the check runs.
        """
        ...

    # -- fidelity accounting -----------------------------------------------

    def navigation_count(self) -> int:
        """Total main-frame navigations since the browser opened.

        Compared before and after each step so that a navigation no step
        expected — a stray ``location.assign``, an unforeseen redirect — is
        detected rather than silently absorbed.
        """
        ...

    def page_count(self) -> int:
        """Total browser pages ever opened.

        Must stay at one. The Controller closes its status stream on
        ``visibilitychange``, so a second page covering the demo page would
        freeze live updates while leaving the stale values on screen looking
        current.
        """
        ...
