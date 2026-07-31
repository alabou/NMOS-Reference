# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""An in-memory :class:`Surface` for testing core logic without a browser.

The fake is backed by an explicit ``selector -> snapshots`` mapping rather than
by parsed HTML with a CSS-selector engine. That is a deliberate choice: matching
selectors against a document is precisely the job the *browser* already does
correctly, and reimplementing it here would create a second, subtly different
selector engine — so a test could pass against the fake's interpretation of a
selector while the real page disagreed. Tests would then be evidence about the
fake rather than about the driver.

What the fake exists to test is the logic layered *above* selector matching: the
affordance rules, the wait semantics, the vacuous-success guard, and the journal
format. Real selectors are covered by the ``e2e`` scenarios against a live node.

The clock is virtual: :meth:`sleep_ms` advances it rather than sleeping, so wait
tests are instantaneous and deterministic.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from ..core.surface import (
    ConsoleRecord,
    DialogRecord,
    ElementSnapshot,
    RequestRecord,
    SelectOption,
)


class FakeSurface:
    """A scripted, browser-free surface.

    Satisfies :class:`~nmos.agentui.core.surface.Surface` structurally. State is
    mutated either directly via :meth:`set_elements` or on a schedule via
    :meth:`at_poll`, which is how a condition that only becomes true partway
    through a poll loop is simulated.
    """

    def __init__(
        self,
        elements: Mapping[str, Iterable[ElementSnapshot]] | None = None,
        *,
        url: str = "http://127.0.0.1:5050/controller/",
        title: str = "NMOS Controller",
        texts: Mapping[str, str] | None = None,
    ) -> None:
        self._elements: dict[str, tuple[ElementSnapshot, ...]] = {
            sel: tuple(snaps) for sel, snaps in (elements or {}).items()
        }
        self._texts: dict[str, str] = dict(texts or {})
        self._options: dict[str, tuple[SelectOption, ...]] = {}
        self._url = url
        self._title = title

        self._now = 0.0
        self._polls = 0
        self._scheduled: dict[int, Callable[[FakeSurface], None]] = {}

        self._dialogs: list[DialogRecord] = []
        self._requests: list[RequestRecord] = []
        self._console: list[ConsoleRecord] = []
        self._console_history: list[ConsoleRecord] = []
        self._navigations = 0
        self._pages = 1

        #: Every interaction performed, so a test can assert that a verb which
        #: refused a control genuinely issued no click.
        self.actions: list[tuple[str, str, str]] = []

    # -- test scripting ----------------------------------------------------

    def set_elements(self, selector: str, snapshots: Iterable[ElementSnapshot]) -> None:
        """Replace the matches for a selector."""
        self._elements[selector] = tuple(snapshots)

    def set_text(self, selector: str, text: str) -> None:
        """Set the visible text of a region."""
        self._texts[selector] = text

    def set_options(self, selector: str, options: Iterable[SelectOption]) -> None:
        """Set the options a select offers."""
        self._options[selector] = tuple(options)

    def set_url(self, url: str) -> None:
        """Move the fake to a new URL, counting it as a navigation."""
        self._url = url
        self._navigations += 1

    def at_poll(self, n: int, mutate: Callable[[FakeSurface], None]) -> None:
        """Run ``mutate`` just before the ``n``-th poll's sleep completes.

        Lets a test express "the class clears on the third poll" without any real
        time passing, which is how the poll loop's timing behaviour is checked.
        """
        self._scheduled[n] = mutate

    def raise_dialog(self, message: str, *, kind: str = "alert") -> None:
        """Record that the page raised a native dialog."""
        self._dialogs.append(DialogRecord(kind=kind, message=message))

    def record_request(self, record: RequestRecord) -> None:
        """Record a request the page issued."""
        self._requests.append(record)

    def record_console(self, text: str, *, kind: str = "log") -> None:
        """Record a console message."""
        record = ConsoleRecord(kind=kind, text=text)
        self._console.append(record)
        self._console_history.append(record)

    def open_extra_page(self) -> None:
        """Simulate a second browser page opening, which must fail a run."""
        self._pages += 1

    def clock(self) -> float:
        """The virtual monotonic clock, in seconds, for injection into waits."""
        return self._now

    @property
    def polls(self) -> int:
        """How many times a wait loop has slept on this surface."""
        return self._polls

    # -- Surface: observation ----------------------------------------------

    @property
    def url(self) -> str:
        return self._url

    def title(self) -> str:
        return self._title

    def count(self, selector: str) -> int:
        return len(self._elements.get(selector, ()))

    def snapshot(self, selector: str) -> ElementSnapshot | None:
        matches = self._elements.get(selector, ())
        return matches[0] if matches else None

    def snapshot_all(self, selector: str) -> tuple[ElementSnapshot, ...]:
        return self._elements.get(selector, ())

    def options(self, selector: str) -> tuple[SelectOption, ...]:
        return self._options.get(selector, ())

    def visible_text(self, selector: str) -> str:
        return self._texts.get(selector, "")

    def is_actionable(self, selector: str) -> bool:
        snapshot = self.snapshot(selector)
        return snapshot is not None and snapshot.visible and snapshot.enabled

    # -- Surface: interaction ----------------------------------------------

    def click(self, selector: str) -> None:
        self.actions.append(("click", selector, ""))

    def type_text(self, selector: str, text: str) -> None:
        self.actions.append(("type_text", selector, text))

    def select_options(self, selector: str, values: tuple[str, ...]) -> None:
        self.actions.append(("select_options", selector, ",".join(values)))

    def set_range(self, selector: str, value: str) -> None:
        self.actions.append(("set_range", selector, value))

    def check(self, selector: str) -> None:
        self.actions.append(("check", selector, ""))

    def uncheck(self, selector: str) -> None:
        self.actions.append(("uncheck", selector, ""))

    # -- Surface: synchronisation -----------------------------------------

    def wait_for_load_state(self) -> None:
        """No-op: the fake has no asynchronous document loading."""

    def sleep_ms(self, milliseconds: int) -> None:
        """Advance the virtual clock and apply any mutation scheduled for now."""
        self._polls += 1
        self._now += milliseconds / 1000.0
        mutate = self._scheduled.pop(self._polls, None)
        if mutate is not None:
            mutate(self)

    # -- Surface: evidence -------------------------------------------------

    def screenshot_png(self) -> bytes:
        # A minimal valid PNG signature is enough: tests assert bytes were
        # written and never decode the image.
        return b"\x89PNG\r\n\x1a\n<fake>"

    def dialog_count(self) -> int:
        return len(self._dialogs)

    def take_dialogs(self) -> tuple[DialogRecord, ...]:
        drained = tuple(self._dialogs)
        self._dialogs.clear()
        return drained

    def take_requests(self) -> tuple[RequestRecord, ...]:
        drained = tuple(self._requests)
        self._requests.clear()
        return drained

    def take_console(self) -> tuple[ConsoleRecord, ...]:
        drained = tuple(self._console)
        self._console.clear()
        return drained

    def console_history(self) -> tuple[ConsoleRecord, ...]:
        return tuple(self._console_history)

    # -- Surface: fidelity accounting -------------------------------------

    def navigation_count(self) -> int:
        return self._navigations

    def page_count(self) -> int:
        return self._pages
