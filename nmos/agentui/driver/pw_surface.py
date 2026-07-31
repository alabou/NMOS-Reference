# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The playwright-backed :class:`Surface`.

The ``Page`` is held in a name-mangled attribute with no accessor and no
``__getattr__``, so the capability whitelist is not merely a type-level
convention — there is no runtime path from a session object back to the browser's
full API either.

On the one use of JavaScript evaluation
--------------------------------------
Reading an element's tag, classes, allowlisted attributes, and live properties
one call at a time would cost roughly twenty round trips per element; a page with
fifty rows would need thousands. So there is exactly one JavaScript snippet in
this driver, :data:`_READ_ELEMENTS`, and it is a module-level constant rather than
anything a caller can influence.

That snippet only ever **reads**, and it filters attributes against the allowlist
inside the browser before anything crosses back (the Python side then validates
again in ``snapshot_of``). It issues no requests and mutates nothing. Being a
single auditable constant is the point: "the driver executes no arbitrary
JavaScript" is checkable by reading one string, and ``evaluate`` is deliberately
absent from the :class:`Surface` protocol so no adapter can reach it.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, Request

from ..core.surface import (
    ALLOWED_ATTRS,
    ConsoleRecord,
    DialogRecord,
    ElementSnapshot,
    RequestRecord,
    SelectOption,
    snapshot_of,
)
from ..core.text import normalise_text

#: The single read-only DOM reader. Returns one compact record per matched
#: element. Attributes are filtered against the caller-supplied allowlist here,
#: in the browser, so off-list values never cross the boundary at all.
#:
#: ``checkVisibility`` accounts for the things no attribute reveals — an ancestor
#: with ``display: none``, zero size, ``visibility: hidden`` — which is what makes
#: the difference between "the operator was shown a greyed control and told why"
#: and "the operator was shown nothing".
_READ_ELEMENTS = """
(elements, allowed) => elements.map((el) => {
  const attrs = {};
  for (const name of allowed) {
    if (el.hasAttribute(name)) {
      attrs[name] = el.getAttribute(name) ?? "";
    }
  }
  return {
    tag: el.tagName.toLowerCase(),
    text: el.innerText ?? el.textContent ?? "",
    classes: Array.from(el.classList),
    attrs: attrs,
    visible: el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }),
    // Live properties, not attributes. For a checkbox the ``checked`` attribute
    // records the server's initial state while the property records what the
    // operator has since done.
    enabled: el.disabled !== true,
    checked: typeof el.checked === "boolean" ? el.checked : null,
    value: typeof el.value === "string" ? el.value : null,
  };
})
"""

#: Reader for a ``<select>``'s options, including the flow-match marking the
#: application applies to the option matching a sender's current flow.
_READ_OPTIONS = """
(el) => Array.from(el.options ?? []).map((opt) => ({
  value: opt.value,
  label: opt.label || opt.textContent || "",
  selected: opt.selected,
  disabled: opt.disabled,
  classes: Array.from(opt.classList),
  flowKey: opt.getAttribute("data-flow-key"),
}))
"""

#: Timeout for the no-side-effect actionability probe. Short on purpose: the
#: question is "would a click land right now", and waiting on it would just
#: reintroduce the auto-wait behaviour that disguises policy as flakiness.
_ACTIONABLE_TIMEOUT_MS = 500

#: Timeout for real interactions. Also short, because every verb has already
#: classified the control and confirmed it is actionable — a long wait here would
#: only ever delay the reporting of a genuine problem.
_INTERACT_TIMEOUT_MS = 5_000


class PwSurface:
    """Implements :class:`~nmos.agentui.core.surface.Surface` over a ``Page``."""

    def __init__(self, page: Page) -> None:
        # Name-mangled to ``_PwSurface__page``. There is no property, no getter,
        # and no __getattr__, so a session holding this object cannot reach the
        # browser's wider API even by trying.
        self.__page = page
        self._dialogs: list[DialogRecord] = []
        self._requests: list[RequestRecord] = []
        self._console: list[ConsoleRecord] = []
        self._console_history: list[ConsoleRecord] = []
        self._navigations = 0
        self._pages = 1
        self._allowed = sorted(ALLOWED_ATTRS)

    # -- listener wiring, called once by the launcher -----------------------

    def attach_listeners(self) -> None:
        """Subscribe to the page events the fidelity ledger and journal need."""
        page = self.__page
        page.on("dialog", self._on_dialog)
        page.on("request", self._on_request)
        page.on("console", self._on_console)
        page.on("framenavigated", self._on_frame_navigated)
        page.context.on("page", self._on_new_page)

    def _on_dialog(self, dialog: Any) -> None:
        """Record and dismiss a native dialog.

        Dismissing is mandatory, not a policy choice: an unhandled ``alert()``
        blocks the page indefinitely, so a driver that merely observed one would
        hang. The message is captured first because it is exactly what the
        operator would have read.
        """
        self._dialogs.append(DialogRecord(
            kind=str(dialog.type), message=normalise_text(str(dialog.message))))
        dialog.accept()

    def _on_request(self, request: Request) -> None:
        """Record a request the page issued, lifting any trace id it carries.

        Observation, not interception: no route handler is installed and nothing
        is modified. The trace id is read from the application's own
        ``X-Trace-Id`` header, or from the body of the client-event post it makes
        — it is never generated here, so a journal can only ever claim
        correlation that genuinely exists.
        """
        path = urlsplit(request.url).path
        trace_id: str | None = None
        try:
            headers = request.headers
            trace_id = headers.get("x-trace-id") or None
            if trace_id is None and path.endswith("/api/debug/client-event"):
                trace_id = _trace_id_from_body(request.post_data)
        except PlaywrightError:
            # A request can be gone by the time it is inspected; losing one trace
            # id degrades correlation but must never break the run.
            trace_id = None

        self._requests.append(RequestRecord(
            method=request.method,
            url=request.url,
            path=path,
            resource_type=request.resource_type,
            trace_id=trace_id,
        ))

    def _on_console(self, message: Any) -> None:
        record = ConsoleRecord(kind=str(message.type),
                               text=normalise_text(str(message.text)))
        self._console.append(record)
        self._console_history.append(record)

    def _on_frame_navigated(self, frame: Any) -> None:
        # Main frame only: sub-frame navigation is not the browser moving.
        if frame == self.__page.main_frame:
            self._navigations += 1

    def _on_new_page(self, page: Any) -> None:
        self._pages += 1

    # -- Surface: observation ---------------------------------------------

    @property
    def url(self) -> str:
        return str(self.__page.url)

    def title(self) -> str:
        return str(self.__page.title())

    def count(self, selector: str) -> int:
        return int(self.__page.locator(selector).count())

    def snapshot(self, selector: str) -> ElementSnapshot | None:
        found = self.snapshot_all(selector)
        return found[0] if found else None

    def snapshot_all(self, selector: str) -> tuple[ElementSnapshot, ...]:
        """Observe every match in one round trip."""
        try:
            raw = self.__page.locator(selector).evaluate_all(
                _READ_ELEMENTS, self._allowed)
        except PlaywrightError:
            # A detached DOM mid-navigation reads as "nothing matched", which is
            # the truthful answer and is exactly why Every() refuses an empty set.
            return ()

        records: list[ElementSnapshot] = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            records.append(snapshot_of(
                selector=selector,
                tag=str(item.get("tag", "")),
                text=str(item.get("text") or ""),
                classes=[str(c) for c in item.get("classes", [])],
                visible=bool(item.get("visible")),
                enabled=bool(item.get("enabled", True)),
                checked=_as_optional_bool(item.get("checked")),
                value=_as_optional_str(item.get("value")),
                attrs={str(k): str(v) for k, v in (item.get("attrs") or {}).items()},
            ))
        return tuple(records)

    def options(self, selector: str) -> tuple[SelectOption, ...]:
        locator = self.__page.locator(selector).first
        if locator.count() == 0:
            return ()
        try:
            raw = locator.evaluate(_READ_OPTIONS)
        except PlaywrightError:
            return ()

        built: list[SelectOption] = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            built.append(SelectOption(
                value=str(item.get("value", "")),
                label=normalise_text(str(item.get("label") or "")),
                selected=bool(item.get("selected")),
                disabled=bool(item.get("disabled")),
                classes=frozenset(str(c) for c in item.get("classes", [])),
                flow_key=_as_optional_str(item.get("flowKey")),
            ))
        return tuple(built)

    def visible_text(self, selector: str) -> str:
        """The rendered text of a region, or ``""`` when it is not present."""
        locator = self.__page.locator(selector).first
        try:
            if locator.count() == 0:
                return ""
            return str(locator.inner_text(timeout=_INTERACT_TIMEOUT_MS))
        except PlaywrightError:
            return ""

    def is_actionable(self, selector: str) -> bool:
        """Whether a click would land, performed as a trial with no side effect."""
        try:
            self.__page.locator(selector).first.click(
                trial=True, timeout=_ACTIONABLE_TIMEOUT_MS)
        except PlaywrightError:
            return False
        return True

    # -- Surface: interaction ---------------------------------------------

    def click(self, selector: str) -> None:
        self.__page.locator(selector).first.click(timeout=_INTERACT_TIMEOUT_MS)

    def type_text(self, selector: str, text: str) -> None:
        self.__page.locator(selector).first.fill(text, timeout=_INTERACT_TIMEOUT_MS)

    def select_options(self, selector: str, values: tuple[str, ...]) -> None:
        self.__page.locator(selector).first.select_option(
            list(values), timeout=_INTERACT_TIMEOUT_MS)

    def set_range(self, selector: str, value: str) -> None:
        """Move a range input and fire the events a real drag would.

        ``fill`` is what playwright offers for an ``<input type=range>``; the
        application listens for ``input`` to update its live value mirror, and that
        mirror updating is the deterministic signal a verb waits on.
        """
        self.__page.locator(selector).first.fill(value, timeout=_INTERACT_TIMEOUT_MS)

    def check(self, selector: str) -> None:
        self.__page.locator(selector).first.check(timeout=_INTERACT_TIMEOUT_MS)

    def uncheck(self, selector: str) -> None:
        self.__page.locator(selector).first.uncheck(timeout=_INTERACT_TIMEOUT_MS)

    # -- Surface: synchronisation -----------------------------------------

    def wait_for_load_state(self) -> None:
        try:
            self.__page.wait_for_load_state()
        except PlaywrightError:
            # Navigating away mid-wait is not an error worth failing a step over;
            # the step's own page-identity wait is what actually decides.
            return

    def sleep_ms(self, milliseconds: int) -> None:
        self.__page.wait_for_timeout(milliseconds)

    # -- Surface: evidence -------------------------------------------------

    def screenshot_png(self) -> bytes:
        """Capture the page.

        Full-page rather than viewport-only: an operator can scroll, so the whole
        document is content they have access to, and cropping evidence at the fold
        would hide exactly the result cell or notice a reader wants to check.
        """
        try:
            return bytes(self.__page.screenshot(full_page=True))
        except PlaywrightError:
            # Never let evidence capture break a run -- an empty image is a
            # visible absence, whereas a raised exception would discard the step.
            return b""

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


# ---------------------------------------------------------------------------
# Helpers for narrowing values that cross the JavaScript boundary
# ---------------------------------------------------------------------------
#
# Everything arriving from ``evaluate`` is untyped. These keep the ``Any`` from
# leaking past this module, which is what lets the rest of the package stay
# meaningfully checked under ``mypy --strict``.

def _as_optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _as_optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _trace_id_from_body(post_data: str | None) -> str | None:
    """Extract ``trace_id`` from a client-event post body.

    Parsed defensively: this is diagnostic enrichment, and a malformed body must
    cost at most one correlation rather than the run.
    """
    if not post_data:
        return None
    import json
    try:
        parsed = json.loads(post_data)
    except ValueError:
        return None
    if isinstance(parsed, dict):
        value = parsed.get("trace_id")
        if isinstance(value, str) and value:
            return value
    return None
