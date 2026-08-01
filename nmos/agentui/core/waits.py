# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Declarative wait conditions and the single poll loop that evaluates them.

There are no fixed sleeps anywhere in this driver. Every wait names a real
observable signal the Controller's own JavaScript produces — a class appearing, a
result cell leaving its pending state, an attribute moving away from the value it
had at page load — and the journal records *which* signal was awaited.

That naming matters more than it looks. "The activation worked" is a much weaker
claim than "``.is-working`` cleared and every result cell reached a terminal
state", and a reader auditing a demo needs to know which of those actually
happened. Declarative specs, rather than arbitrary callables, are what let the
journal describe the condition in words.

Two hazards are designed against here explicitly:

* **Vacuous success.** :class:`Every` requires at least one match by default. A
  navigation detaches the DOM, so a condition like "no button still has
  ``.is-working``" would otherwise be satisfied by the button having ceased to
  exist — a wait that passes because the page went away is worse than one that
  times out.

* **Presence mistaken for change.** Both liveness markers this UI uses
  (``data-live-active`` and ``aria-pressed``) are written by the server at page
  load. Only :class:`AttrChangedFrom` and :class:`ClassSetChangedFrom`, which
  compare against a baseline captured earlier, can evidence a live update.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .surface import ElementSnapshot, Surface
from .text import normalise_text

# ---------------------------------------------------------------------------
# Element predicates -- what must hold for one element
# ---------------------------------------------------------------------------


@runtime_checkable
class ElementPredicate(Protocol):
    """A condition on a single observed element."""

    def describe(self) -> str:
        """Render the condition for the journal."""
        ...

    def holds(self, snapshot: ElementSnapshot) -> bool:
        """Evaluate against one observation."""
        ...


@dataclass(frozen=True, slots=True)
class HasClass:
    """The element carries a CSS class."""

    name: str

    def describe(self) -> str:
        return f"has class .{self.name}"

    def holds(self, snapshot: ElementSnapshot) -> bool:
        return snapshot.has_class(self.name)


@dataclass(frozen=True, slots=True)
class LacksClass:
    """The element does not carry a CSS class."""

    name: str

    def describe(self) -> str:
        return f"lacks class .{self.name}"

    def holds(self, snapshot: ElementSnapshot) -> bool:
        return not snapshot.has_class(self.name)


@dataclass(frozen=True, slots=True)
class HasAnyClass:
    """The element carries at least one of a set of classes.

    Used for terminal-state checks where several classes are acceptable
    endings — a result cell that reached either ``ok`` or ``error`` has
    finished, and both are legitimate outcomes to record.
    """

    names: frozenset[str]

    def describe(self) -> str:
        return "has any of " + ", ".join(f".{n}" for n in sorted(self.names))

    def holds(self, snapshot: ElementSnapshot) -> bool:
        return bool(snapshot.classes & self.names)


@dataclass(frozen=True, slots=True)
class HasAttr:
    """The element has an allowlisted attribute present."""

    name: str

    def describe(self) -> str:
        return f"has attribute [{self.name}]"

    def holds(self, snapshot: ElementSnapshot) -> bool:
        return snapshot.has_attr(self.name)


@dataclass(frozen=True, slots=True)
class LacksAttr:
    """The element does not have an allowlisted attribute.

    The detail rows of a capabilities table are revealed by removing the
    ``hidden`` attribute, so this is the deterministic expand signal.
    """

    name: str

    def describe(self) -> str:
        return f"lacks attribute [{self.name}]"

    def holds(self, snapshot: ElementSnapshot) -> bool:
        return not snapshot.has_attr(self.name)


@dataclass(frozen=True, slots=True)
class AttrIs:
    """An allowlisted attribute equals a value, compared after normalising."""

    name: str
    value: str

    def describe(self) -> str:
        return f"[{self.name}] == {self.value!r}"

    def holds(self, snapshot: ElementSnapshot) -> bool:
        return normalise_text(snapshot.attr(self.name)) == normalise_text(self.value)


@dataclass(frozen=True, slots=True)
class IsChecked:
    """A checkbox or radio is ticked, read from the live property."""

    expected: bool = True

    def describe(self) -> str:
        return "is checked" if self.expected else "is unchecked"

    def holds(self, snapshot: ElementSnapshot) -> bool:
        return snapshot.checked is self.expected


@dataclass(frozen=True, slots=True)
class ValueIs:
    """An input or select's live value equals a string."""

    value: str

    def describe(self) -> str:
        return f"value == {self.value!r}"

    def holds(self, snapshot: ElementSnapshot) -> bool:
        return normalise_text(snapshot.value) == normalise_text(self.value)


# ---------------------------------------------------------------------------
# Wait specifications
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SpecResult:
    """The evaluation of a spec at one poll iteration."""

    satisfied: bool
    observed: str = ""
    branch: str | None = None


@runtime_checkable
class WaitSpec(Protocol):
    """A condition the poll loop can evaluate against a surface."""

    def describe(self) -> str:
        """Render the condition for the journal."""
        ...

    def evaluate(self, surface: Surface) -> SpecResult:
        """Evaluate once, reporting what was observed."""
        ...


@dataclass(frozen=True, slots=True)
class Appears:
    """At least ``count`` elements match the selector."""

    selector: str
    count: int = 1

    def describe(self) -> str:
        return f"Appears({self.selector!r}, count>={self.count})"

    def evaluate(self, surface: Surface) -> SpecResult:
        seen = surface.count(self.selector)
        return SpecResult(seen >= self.count, observed=f"{seen} match(es)")


@dataclass(frozen=True, slots=True)
class Disappears:
    """Nothing matches the selector."""

    selector: str

    def describe(self) -> str:
        return f"Disappears({self.selector!r})"

    def evaluate(self, surface: Surface) -> SpecResult:
        seen = surface.count(self.selector)
        return SpecResult(seen == 0, observed=f"{seen} match(es)")


@dataclass(frozen=True, slots=True)
class Every:
    """Every match satisfies the predicate, and there is at least ``min_count``.

    The ``min_count`` floor is deliberate and defaults to one: without it, an
    element set that became empty would satisfy any predicate vacuously, so a
    wait could "succeed" precisely because the page navigated away.
    """

    selector: str
    predicate: ElementPredicate
    min_count: int = 1

    def describe(self) -> str:
        return (f"Every({self.selector!r} {self.predicate.describe()}, "
                f"min={self.min_count})")

    def evaluate(self, surface: Surface) -> SpecResult:
        seen = surface.snapshot_all(self.selector)
        if len(seen) < self.min_count:
            return SpecResult(False, observed=f"only {len(seen)} match(es)")
        failing = [s for s in seen if not self.predicate.holds(s)]
        if failing:
            return SpecResult(
                False,
                observed=f"{len(failing)}/{len(seen)} still failing",
            )
        return SpecResult(True, observed=f"all {len(seen)} match(es)")


@dataclass(frozen=True, slots=True)
class Any_:
    """At least one match satisfies the predicate.

    Named with a trailing underscore to avoid shadowing the builtin ``any`` for
    readers of this module.
    """

    selector: str
    predicate: ElementPredicate

    def describe(self) -> str:
        return f"Any({self.selector!r} {self.predicate.describe()})"

    def evaluate(self, surface: Surface) -> SpecResult:
        seen = surface.snapshot_all(self.selector)
        holding = [s for s in seen if self.predicate.holds(s)]
        return SpecResult(
            bool(holding),
            observed=f"{len(holding)}/{len(seen)} match(es)",
        )


@dataclass(frozen=True, slots=True)
class First:
    """The first match satisfies the predicate; absence is not satisfaction."""

    selector: str
    predicate: ElementPredicate

    def describe(self) -> str:
        return f"First({self.selector!r} {self.predicate.describe()})"

    def evaluate(self, surface: Surface) -> SpecResult:
        snapshot = surface.snapshot(self.selector)
        if snapshot is None:
            return SpecResult(False, observed="no match")
        held = self.predicate.holds(snapshot)
        return SpecResult(held, observed=f"classes={sorted(snapshot.classes)}")


@dataclass(frozen=True, slots=True)
class AttrChangedFrom:
    """An attribute now differs from a baseline captured earlier.

    This is the only honest way to evidence a server-sent status update in this
    UI, because the attributes that carry liveness are also rendered at page
    load. Comparing against a baseline turns "the marker is present" — which
    proves nothing — into "the marker moved".
    """

    selector: str
    name: str
    baseline: str | None

    def describe(self) -> str:
        return (f"AttrChangedFrom({self.selector!r}, [{self.name}] "
                f"was {self.baseline!r})")

    def evaluate(self, surface: Surface) -> SpecResult:
        snapshot = surface.snapshot(self.selector)
        if snapshot is None:
            return SpecResult(False, observed="no match")
        current = snapshot.attr(self.name)
        changed = normalise_text(current) != normalise_text(self.baseline)
        return SpecResult(changed, observed=f"[{self.name}]={current!r}")


@dataclass(frozen=True, slots=True)
class ClassSetChangedFrom:
    """The element's classes, restricted to a prefix, differ from a baseline.

    Restricting to a prefix keeps transient decoration out of the comparison:
    health is carried in ``is-*`` classes, while ``is-working`` style markers and
    layout classes come and go for unrelated reasons.
    """

    selector: str
    prefix: str
    baseline: frozenset[str]

    def describe(self) -> str:
        return (f"ClassSetChangedFrom({self.selector!r}, {self.prefix}* "
                f"was {sorted(self.baseline)})")

    def evaluate(self, surface: Surface) -> SpecResult:
        snapshot = surface.snapshot(self.selector)
        if snapshot is None:
            return SpecResult(False, observed="no match")
        current = frozenset(c for c in snapshot.classes if c.startswith(self.prefix))
        return SpecResult(current != self.baseline,
                          observed=f"{self.prefix}*={sorted(current)}")


@dataclass(frozen=True, slots=True)
class TextIs:
    """A region's visible text equals a string after normalisation."""

    selector: str
    expected: str

    def describe(self) -> str:
        return f"TextIs({self.selector!r} == {self.expected!r})"

    def evaluate(self, surface: Surface) -> SpecResult:
        actual = normalise_text(surface.visible_text(self.selector))
        return SpecResult(actual == normalise_text(self.expected),
                          observed=actual[:120])


@dataclass(frozen=True, slots=True)
class TextContains:
    """A region's visible text contains a fragment, after normalisation.

    Used to race a success condition against a page's own failure text, so a
    refusal is reported with the reason on screen rather than as a timeout.
    """

    selector: str
    fragment: str

    def describe(self) -> str:
        return f"TextContains({self.selector!r} ~ {self.fragment!r})"

    def evaluate(self, surface: Surface) -> SpecResult:
        actual = normalise_text(surface.visible_text(self.selector))
        return SpecResult(normalise_text(self.fragment) in actual,
                          observed=actual[:120])


@dataclass(frozen=True, slots=True)
class UrlChangedFrom:
    """The document URL differs from a baseline.

    Used as the navigation-started branch of a race, where the question is
    whether the click moved the browser at all.
    """

    baseline: str

    def describe(self) -> str:
        return f"UrlChangedFrom({self.baseline!r})"

    def evaluate(self, surface: Surface) -> SpecResult:
        return SpecResult(surface.url != self.baseline, observed=surface.url)


@dataclass(frozen=True, slots=True)
class NavigationSince:
    """The browser has navigated since a recorded count.

    Needed for actions that reload the *same* URL — a page reload triggered by a
    reset button changes nothing about the address, so a URL comparison would
    never notice it happened.
    """

    baseline: int

    def describe(self) -> str:
        return f"NavigationSince({self.baseline})"

    def evaluate(self, surface: Surface) -> SpecResult:
        seen = surface.navigation_count()
        return SpecResult(seen > self.baseline, observed=f"{seen} navigation(s)")


@dataclass(frozen=True, slots=True)
class DialogRaised:
    """The page raised a native dialog.

    Deliberately evaluated through the surface's *non-draining*
    ``dialog_count()``. Polling must not consume evidence: the alert's exact
    wording is the whole point of this branch winning, and the step that owns
    the wait drains it once, afterwards, with ``take_dialogs()``.
    """

    since: int = 0

    def describe(self) -> str:
        return f"DialogRaised(since={self.since})"

    def evaluate(self, surface: Surface) -> SpecResult:
        seen = surface.dialog_count()
        return SpecResult(seen > self.since, observed=f"{seen} dialog(s)")


@dataclass(frozen=True, slots=True)
class AllOf:
    """Every child spec is satisfied simultaneously."""

    specs: tuple[WaitSpec, ...]

    def describe(self) -> str:
        return "AllOf(" + " & ".join(s.describe() for s in self.specs) + ")"

    def evaluate(self, surface: Surface) -> SpecResult:
        observations: list[str] = []
        for spec in self.specs:
            result = spec.evaluate(surface)
            observations.append(f"{spec.describe()}->{result.observed}")
            if not result.satisfied:
                return SpecResult(False, observed="; ".join(observations))
        return SpecResult(True, observed="; ".join(observations))


@dataclass(frozen=True, slots=True)
class AnyOf:
    """The first satisfied branch wins, and the journal records which one.

    This is how the submit race is resolved deterministically: a click on a
    selection form either navigates or is refused by a client-side guard that
    raises an alert, and those two outcomes demand different handling. Reporting
    the winning branch by name removes the guesswork.
    """

    branches: tuple[tuple[str, WaitSpec], ...] = field(default_factory=tuple)

    def describe(self) -> str:
        return "AnyOf(" + " | ".join(f"{n}:{s.describe()}" for n, s in self.branches) + ")"

    def evaluate(self, surface: Surface) -> SpecResult:
        observations: list[str] = []
        for name, spec in self.branches:
            result = spec.evaluate(surface)
            observations.append(f"{name}->{result.observed}")
            if result.satisfied:
                return SpecResult(True, observed=result.observed, branch=name)
        return SpecResult(False, observed="; ".join(observations))


# ---------------------------------------------------------------------------
# Convenience constructors for the idioms this UI actually uses
# ---------------------------------------------------------------------------


def class_present(selector: str, name: str) -> WaitSpec:
    """The first match carries a class -- e.g. an action having started."""
    return First(selector, HasClass(name))


def class_absent(selector: str, name: str) -> WaitSpec:
    """The element is present and no longer carries a class.

    Requires presence, so a detached DOM cannot be mistaken for completion.
    """
    return First(selector, LacksClass(name))


def all_terminal(selector: str, terminal_classes: Iterable[str]) -> WaitSpec:
    """Every match has reached one of the acceptable terminal classes."""
    return Every(selector, HasAnyClass(frozenset(terminal_classes)))


def attr_absent(selector: str, name: str) -> WaitSpec:
    """The element is present and lacks an attribute -- e.g. revealed detail."""
    return First(selector, LacksAttr(name))


def checked(selector: str, expected: bool = True) -> WaitSpec:
    """A checkbox or radio has reached a tick state, read live."""
    return First(selector, IsChecked(expected))


# ---------------------------------------------------------------------------
# The poll loop
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WaitOutcome:
    """What a wait actually did, recorded verbatim in the journal."""

    satisfied: bool
    spec: str
    waited_ms: int
    observed: str = ""
    branch: str | None = None


def wait_until(
    surface: Surface,
    spec: WaitSpec,
    *,
    timeout_ms: int,
    poll_ms: int = 50,
    clock: Callable[[], float] = time.monotonic,
) -> WaitOutcome:
    """Poll ``spec`` until satisfied or the timeout elapses.

    Never raises on timeout. The caller decides what an unmet condition means,
    because the answer differs: a missing page marker is a failure, whereas an
    unobserved live status update is a legitimate "unconfirmed" verdict that
    must not be reported as either success or breakage.

    The clock is injectable so wait behaviour can be unit-tested against a
    virtual clock rather than by actually waiting.
    """
    started = clock()
    deadline = started + (timeout_ms / 1000.0)

    # Evaluate once before sleeping: most conditions in a server-rendered UI are
    # already true by the time a verb looks, and an unconditional first sleep
    # would add its own latency to every single step.
    while True:
        result = spec.evaluate(surface)
        elapsed_ms = int((clock() - started) * 1000)
        if result.satisfied:
            return WaitOutcome(True, spec.describe(), elapsed_ms,
                               result.observed, result.branch)
        if clock() >= deadline:
            return WaitOutcome(False, spec.describe(), elapsed_ms,
                               result.observed, result.branch)
        surface.sleep_ms(poll_ms)
