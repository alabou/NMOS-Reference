# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The operator-level verbs a scenario calls.

Every public method here is something a person sitting at the Controller can do:
follow a link, tick a box, choose an option, press a button, read what is on
screen. There is no ``goto``, no ``evaluate``, no way to reach the JSON API, and
no way to obtain the underlying browser — the session is typed against
:class:`~nmos.agentui.core.surface.Surface`, so those are author-time errors.

Each verb follows the same shape, and it is the shape that carries the guarantees:

1. Open a journal step, so the interaction cannot go unrecorded.
2. Assert the page is one this verb belongs on.
3. Locate the control and **classify it before acting** — refusing with the
   server's own stated reason rather than blundering into a timeout.
4. Act.
5. Wait on a *named* observable signal, never a fixed sleep.
6. Capture the outcome immediately, because in this UI an action's result can be
   overwritten by the status update that follows it.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit

from ...core.adapter import Credentials
from ...core.affordance import Control, classify
from ...core.journal import Journal
from ...core.step import Recorder, Step
from ...core.surface import ElementSnapshot, Surface
from ...core.text import normalise_text
from ...core.tutorial import Lesson, Tutorial
from ...core.waits import (
    AnyOf,
    AttrChangedFrom,
    ClassSetChangedFrom,
    DialogRaised,
    First,
    NavigationSince,
    TextContains,
    UrlChangedFrom,
    ValueIs,
    WaitOutcome,
    WaitSpec,
    all_terminal,
    attr_absent,
    checked,
    class_absent,
    class_present,
    wait_until,
)
from ...enums import (
    Affordance,
    ControlKind,
    CorrelationKind,
    DeviceAccess,
    Health,
    PageId,
    RowAction,
    SseVerdict,
    ToggleAction,
    WaitSignal,
)
from ...errors import (
    ActionFailed,
    AmbiguousTarget,
    BlockedControl,
    ControlAbsent,
    ControlHidden,
    ControllerJsNotLoaded,
    LiveUpdateNotObserved,
    LoginRejected,
    NoSuchOption,
    PageModelMismatch,
    SelectionGuard,
    SessionLost,
    TargetUnreachable,
    WaitTimeout,
)
from . import pages
from .adapter import ControllerAdapter
from .views import (
    ActionOutcome,
    ConstraintSetRow,
    DeviceView,
    GroupView,
    PageView,
    ParamWidget,
    PrivacyView,
    ResourceRow,
    ResultCell,
    SelectionView,
    StatusView,
)

#: Pages that carry a resource-selection form.
_SELECTION_PAGES = (
    PageId.SENDERS,
    PageId.RECEIVERS,
    PageId.RECEIVERS_COMPATIBLE_SENDERS,
)

#: Pages that carry a capabilities table.
_CAPS_PAGES = (PageId.SENDERS_CAPS, PageId.RECEIVERS_CAPS,
               PageId.RECEIVERS_VIEW_CAPS)

#: Pages that carry the configure controls.
_CONFIGURE_PAGES = (PageId.SENDERS_CONFIGURE, PageId.RECEIVERS_CONFIGURE)

#: Which selection form belongs to which page.
_PAGE_FORM = {
    PageId.SENDERS: pages.SENDERS_FORM,
    PageId.RECEIVERS: pages.RECEIVERS_FORM,
    PageId.RECEIVERS_COMPATIBLE_SENDERS: pages.COMPATIBLE_SENDERS_FORM,
    PageId.SENDERS_CAPS: pages.CAPS_FORM,
    PageId.RECEIVERS_CAPS: pages.CAPS_FORM,
    PageId.RECEIVERS_VIEW_CAPS: pages.CAPS_FORM,
}

#: How long to wait for a live status update before recording it unconfirmed.
_LIVE_TIMEOUT_MS = 20_000

#: Budget for the diagnostic "the action started" probe. Short on purpose --
#: see press_toggle. Non-observation is normal for a single fast request.
_TOGGLE_START_PROBE_MS = 1_000


class ControllerSession:
    """Operator-level control of the Controller UI."""

    def __init__(
        self,
        surface: Surface,
        recorder: Recorder,
        adapter: ControllerAdapter,
        credentials: Credentials,
        *,
        step_timeout_ms: int = 15_000,
    ) -> None:
        self._surface = surface
        self._recorder = recorder
        self._adapter = adapter
        # Held here rather than on the recorder: the recorder writes artifacts, and
        # a credential in the object that owns file output is one refactor away
        # from being serialised into one.
        self._credentials = credentials
        self._timeout = step_timeout_ms
        #: Liveness markers as they stood when the current page loaded. Refreshed
        #: on every navigation. Without these, no live update can be evidenced:
        #: the server renders the same markers itself at page load.
        self._live_baseline: dict[str, str | None] = {}
        self._health_baseline: dict[str, frozenset[str]] = {}
        self._sse_verdict = SseVerdict.NOT_EXERCISED
        #: Set when the run is in tutorial mode. Lessons cover the journal steps
        #: since the previous lesson, which is how "under the hood" shows the calls
        #: belonging to each teaching step rather than the whole run's.
        self._tutorial: Tutorial | None = None
        self._lesson_mark = 1

    # -- accessors ---------------------------------------------------------

    @property
    def journal(self) -> Journal:
        """The run journal, read-only to callers."""
        return self._recorder.journal

    @property
    def tutorial(self) -> Tutorial | None:
        """The tutorial being built, when the run is in tutorial mode."""
        return self._tutorial

    def start_tutorial(self, tutorial: Tutorial) -> None:
        """Turn on tutorial mode for this session."""
        self._tutorial = tutorial
        self._lesson_mark = self._recorder.sequence + 1

    @property
    def sse_verdict(self) -> SseVerdict:
        """Whether a live update was ever actually observed."""
        return self._sse_verdict

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _control(self, selector: str) -> Control:
        """Locate and classify, without acting."""
        return classify(selector, self._surface.snapshot(selector))

    def _require(self, step: Step, control: Control, *, what: str) -> Control:
        """Record the classification and refuse anything not actionable.

        The three refusals stay distinct because they say different things about
        what the operator was shown, and a gating demonstration that merged them
        would misrepresent the interface.
        """
        step.examined(control)
        page_id = self._recorder.page_id()
        shot = step.directory / "after.png"

        if control.affordance is Affordance.ABSENT:
            raise ControlAbsent(
                f"{what} is not present on this page, so the action does not "
                f"apply here",
                selector=control.selector, page_id=page_id, screenshot=shot)
        if control.affordance is Affordance.HIDDEN:
            raise ControlHidden(
                f"{what} is present but not visible, so no operator could act "
                f"on it without first revealing it",
                selector=control.selector, control_text=control.text,
                page_id=page_id, screenshot=shot)
        if control.affordance is Affordance.BLOCKED:
            raise BlockedControl(
                f"{what} is refused by the server: {control.reason or '(no reason given)'}",
                reason=control.reason, rendered_as=control.kind,
                selector=control.selector, control_text=control.text,
                page_id=page_id, screenshot=shot)

        # Physical reachability is a separate question from policy: an overlay or a
        # zero-sized box is not the server refusing anything, and no attribute
        # reveals it.
        if not self._surface.is_actionable(control.target):
            raise ControlHidden(
                f"{what} is rendered and enabled but a click would not land on "
                f"it (obscured, zero-sized, or pointer-events disabled)",
                selector=control.selector, control_text=control.text,
                page_id=page_id, screenshot=shot)
        return control

    def _wait(
        self,
        step: Step,
        signal: WaitSignal,
        spec: WaitSpec,
        *,
        timeout_ms: int | None = None,
    ) -> WaitOutcome:
        """Await a named signal and record it, without raising on timeout."""
        outcome = wait_until(
            self._surface, spec,
            timeout_ms=timeout_ms if timeout_ms is not None else self._timeout)
        step.waited(signal, outcome)
        return outcome

    def _require_wait(
        self,
        step: Step,
        signal: WaitSignal,
        spec: WaitSpec,
        *,
        timeout_ms: int | None = None,
    ) -> WaitOutcome:
        """Await a signal that must arrive."""
        outcome = self._wait(step, signal, spec, timeout_ms=timeout_ms)
        if not outcome.satisfied:
            raise WaitTimeout(
                f"{signal} never arrived: {outcome.spec} — observed "
                f"{outcome.observed or '(nothing)'}",
                signal=str(signal), spec=outcome.spec,
                waited_ms=outcome.waited_ms, observed=outcome.observed)
        return outcome

    def _settle_page(self, step: Step, *, expect: tuple[PageId, ...] = ()) -> PageView:
        """Wait for the destination page and refresh the liveness baselines.

        Landing unexpectedly on the sign-in page is checked explicitly. The
        session store is looked up rather than recreated, so a node restart
        invalidates a structurally valid cookie — and without this check every
        later verb would quietly operate on the login page and report entirely
        plausible nonsense.
        """
        self._surface.wait_for_load_state()

        # Nothing loaded at all? The browser parks on an internal error page whose
        # URL is not http(s) -- what a node restarting mid-run looks like. Checked
        # by scheme rather than by any browser-specific URL, and before page
        # classification, so the report names the real cause instead of blaming the
        # UI for serving an unexpected page.
        scheme = urlsplit(self._surface.url).scheme
        if scheme not in ("http", "https"):
            raise TargetUnreachable(
                f"the browser could not load the Controller: it is on "
                f"{self._surface.url!r}. The node is most likely restarting or "
                f"gone — check it is still listening before re-running"
            )

        view = self._page_view()

        if view.page_id is PageId.LOGIN and PageId.LOGIN not in expect:
            raise SessionLost(
                "landed on the sign-in page unexpectedly; the admin session is "
                "no longer valid (a node restart invalidates it, because the "
                "session is looked up rather than recreated)")
        if expect and view.page_id not in expect:
            raise PageModelMismatch(
                f"expected one of {[str(p) for p in expect]} but this is "
                f"{view.page_id} ({view.url})",
                expected=expect, actual=view.page_id)

        self._capture_baselines()
        step.note("page", view.to_json())
        return view

    def _capture_baselines(self) -> None:
        """Snapshot the liveness markers as the server rendered them.

        Everything about live-update detection rests on this. Both markers the
        Controller uses are written by Jinja at page load, so their presence
        proves nothing; only a departure from these values does.
        """
        self._live_baseline.clear()
        self._health_baseline.clear()

        for selector, side in ((pages.RESULT_CELLS_SENDER, "sender"),
                               (pages.RESULT_CELLS_RECEIVER, "receiver")):
            attribute = ("data-result-for" if side == "sender"
                         else "data-result-for-receiver")
            for snapshot in self._surface.snapshot_all(selector):
                resource = snapshot.attr(attribute)
                if resource:
                    key = f"{side}:{resource}"
                    self._live_baseline[key] = snapshot.attr("data-live-active")

        for snapshot in self._surface.snapshot_all(
                ".status-badge[data-resource-id]"):
            resource = snapshot.attr("data-resource-id")
            if resource:
                self._health_baseline[resource] = frozenset(
                    c for c in snapshot.classes
                    if c.startswith(pages.HEALTH_CLASS_PREFIX))

    def _page_view(self) -> PageView:
        """Read the current page as the operator sees it."""
        heading = normalise_text(self._surface.visible_text(pages.HEADING))
        alerts = tuple(
            normalise_text(s.text)
            for s in self._surface.snapshot_all(pages.ALERTS)
            if s.visible and normalise_text(s.text)
        )
        return PageView(
            page_id=self._recorder.page_id(),
            url=self._surface.url,
            heading=heading,
            alerts=alerts,
            text=normalise_text(self._surface.visible_text(pages.MAIN)),
        )

    def _assert_on(self, verb: str, allowed: tuple[PageId, ...]) -> PageId:
        """Refuse to operate on a page this verb does not belong on."""
        page_id = self._recorder.page_id()
        if page_id not in allowed:
            raise PageModelMismatch(
                f"{verb} needs one of {[str(p) for p in allowed]}, but this is "
                f"{page_id}", expected=allowed, actual=page_id)
        return page_id

    def _current_form(self) -> str:
        """The selection form belonging to the current page."""
        page_id = self._recorder.page_id()
        form = _PAGE_FORM.get(page_id)
        if form is None:
            raise PageModelMismatch(
                f"{page_id} has no selection form", actual=page_id)
        return form

    def _hidden_csv(self, selector: str) -> tuple[str, ...]:
        """Read a hidden field's comma-separated ids, as rendered."""
        snapshot = self._surface.snapshot(selector)
        if snapshot is None or not snapshot.value:
            return ()
        return tuple(part for part in snapshot.value.split(",") if part)

    def _read_selection(self) -> SelectionView:
        """Read what the page will submit, from its own fields."""
        checked_ids: list[str] = []
        for snapshot in self._surface.snapshot_all(pages.MEMBER_CHECKS):
            if snapshot.checked:
                ids = snapshot.attr("data-ids") or ""
                checked_ids.extend(p for p in ids.split(",") if p)

        group_ids: list[str] = []
        for snapshot in self._surface.snapshot_all(pages.GROUP_RADIOS):
            if snapshot.checked:
                ids = snapshot.attr("data-ids") or ""
                group_ids.extend(p for p in ids.split(",") if p)

        mode_snapshot = self._surface.snapshot(pages.SELECTION_MODE)
        return SelectionView(
            checked_ids=tuple(checked_ids),
            group_ids=tuple(group_ids),
            submitted_sender_ids=self._hidden_csv(pages.SENDER_IDS),
            submitted_receiver_ids=self._hidden_csv(pages.RECEIVER_IDS),
            mode=normalise_text(mode_snapshot.value) if mode_snapshot else "",
        )

    # ------------------------------------------------------------------
    # Sign-in and run preconditions
    # ------------------------------------------------------------------

    def sign_in(self) -> PageView:
        """Sign in through the real form.

        The HTTP status is never consulted: a rejected sign-in returns 401 with a
        fully rendered page, so status-based logic would call that a missing page.
        What is observed is what the operator sees — still on the login page, with
        an alert visible.
        """
        with self._recorder.step(
            "sign_in",
            intent="sign in as administrator",
            expects_navigation=True,
        ) as step:
            step.touched()
            # A known and permanent gap: login.html extends no base template and
            # loads no JavaScript, so this click produces no client-side trace
            # record. Declaring it here keeps the journal honest about why this one
            # step correlates only to the server's own request pair.
            step.correlation = CorrelationKind.SERVER_ONLY
            self._assert_on("sign_in", (PageId.LOGIN,))
            field = self._require(step, self._control(pages.LOGIN_PASSWORD),
                                  what="the password field")
            step.note("password_field", field.describe())
            button = self._require(step, self._control(pages.LOGIN_SUBMIT),
                                   what="the sign-in button")
            step.note("submit_button", button.describe())

            url_before = self._surface.url
            self._adapter.authenticate(self._surface, self._credentials)
            self._wait(step, WaitSignal.PAGE_LOADED, UrlChangedFrom(url_before))

            view = self._page_view()
            if view.page_id is PageId.LOGIN:
                raise LoginRejected(
                    "the Controller rejected the password: still on the sign-in "
                    f"page with {view.alerts or ('no visible alert',)}")
            step.note("landed_on", str(view.page_id))

        # On an OAuth 2.0 rig the password gate is only the first of two. The
        # Controller has redirected the browser to the Authorization Server and
        # the operator is now looking at *its* form, on a different origin.
        # Detected from where the browser actually is rather than from the
        # node's flags: that way a rig whose Authorization Server is already
        # holding a session, and so redirects straight back, is handled by the
        # same code path with no second sign-in.
        if self._page_view().page_id is PageId.OAUTH2_SIGNIN:
            view = self._sign_in_at_authorization_server()

        self._capture_baselines()
        return view

    def _sign_in_at_authorization_server(self) -> PageView:
        """Authenticate at the Authorization Server and return to the Controller.

        A separate recorded step from :meth:`sign_in`, not a continuation of it,
        because it is a distinct act by the operator against a distinct system —
        and because the journal should show a reader that their password went to
        the Controller and their user account went to the Authorization Server.

        Like the Controller's own gate this produces no client-side trace: the
        Authorization Server is not instrumented by this project, so the step is
        declared ``SERVER_ONLY`` rather than left looking like a correlation
        failure.
        """
        with self._recorder.step(
            "sign_in_oauth2",
            intent="authorise this Controller at the Authorization Server",
            expects_navigation=True,
        ) as step:
            step.touched()
            step.correlation = CorrelationKind.SERVER_ONLY
            before = self._page_view()
            step.note("authorization_server", _origin_of(before.url))
            step.note("operator", self._credentials.operator_username)

            url_before = self._surface.url
            self._adapter.authenticate_oauth2(self._surface, self._credentials)
            self._wait(step, WaitSignal.PAGE_LOADED, UrlChangedFrom(url_before))

            view = self._page_view()
            if view.page_id is PageId.OAUTH2_SIGNIN:
                # Still on the Authorization Server: it rejected the operator.
                # Reported with its own alert text where there is one, because
                # the reason belongs to that server and not to us.
                raise LoginRejected(
                    "the Authorization Server rejected the operator "
                    f"{self._credentials.operator_username!r}: still on its "
                    f"sign-in page at {view.url} with "
                    f"{view.alerts or ('no visible alert',)}")
            if view.page_id is PageId.LOGIN:
                # Back at the Controller's own gate means the local session was
                # lost while the browser was away — a different fault, and one
                # that would otherwise present as a confusing success.
                raise LoginRejected(
                    "returned from the Authorization Server to the Controller's "
                    "password gate, so the local session did not survive the "
                    "redirect")
            step.note("landed_on", str(view.page_id))
            return view

    def check_preconditions(self) -> None:
        """Run the adapter's health checks on the first authenticated page.

        Confirms the application's own JavaScript ran. If it did not, every class
        and attribute change this driver waits on simply never appears, and each
        wait would time out pointing at the wrong cause.
        """
        with self._recorder.step(
            "check_preconditions",
            intent="confirm the page's own scripts are running",
        ) as step:
            for precondition in self._adapter.preconditions():
                problem = precondition.check(self._surface)
                step.note(precondition.name, problem or "ok")
                if problem:
                    raise ControllerJsNotLoaded(problem)
            debug = self._surface.snapshot(pages.HTML_ROOT)
            step.note("debug_tracing",
                      bool(debug and debug.attr("data-debug") == "1"))

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _navigate(self, verb: str, selector: str, intent: str,
                  expect: tuple[PageId, ...]) -> PageView:
        """Follow a navigation link by clicking it.

        The wait is on the navigation *counter*, not on the URL. An operator may
        click "Receivers" while already on the receivers page — the natural move
        after coming back from a detail page, and the one §15 of
        OPERATING-THE-CONTROLLER.md asks for so ``clear_selection()`` runs on a
        fresh list. That reloads the same address, so a URL comparison would
        never be satisfied and the verb would time out on a click that worked.
        ``press_refresh`` already waits this way for the same reason.
        """
        with self._recorder.step(verb, intent=intent,
                                 expects_navigation=True) as step:
            step.touched()
            control = self._require(step, self._control(selector),
                                    what=f"the {verb.replace('_', ' ')} link")
            navigations_before = self._surface.navigation_count()
            self._surface.click(control.selector)
            self._require_wait(step, WaitSignal.PAGE_LOADED,
                               NavigationSince(navigations_before))
            return self._settle_page(step, expect=expect)

    def open_senders(self) -> PageView:
        """Follow the Senders navigation link."""
        return self._navigate("open_senders", pages.NAV_SENDERS,
                              "list the senders", (PageId.SENDERS,))

    def open_receivers(self) -> PageView:
        """Follow the Receivers navigation link."""
        return self._navigate("open_receivers", pages.NAV_RECEIVERS,
                              "list the receivers", (PageId.RECEIVERS,))

    def sign_out(self) -> PageView:
        """Sign out, releasing any reservations the session holds."""
        with self._recorder.step("sign_out", intent="sign out",
                                 expects_navigation=True) as step:
            step.touched()
            control = self._require(step, self._control(pages.NAV_SIGN_OUT),
                                    what="the sign-out link")
            url_before = self._surface.url
            self._surface.click(control.selector)
            self._require_wait(step, WaitSignal.PAGE_LOADED,
                               UrlChangedFrom(url_before))
            return self._settle_page(step, expect=(PageId.LOGIN,))

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def read_page(self) -> PageView:
        """Read the current page without touching it."""
        with self._recorder.step("read_page",
                                 intent="read what is on screen") as step:
            view = self._page_view()
            step.note("page", view.to_json())
            return view

    def read_rows(self) -> tuple[ResourceRow, ...]:
        """List the resources on a selection page, with each row's affordances."""
        with self._recorder.step("read_rows",
                                 intent="list the resources on this page") as step:
            self._assert_on("read_rows", _SELECTION_PAGES)
            rows: list[ResourceRow] = []

            # Which device a row sits under is on screen -- the page groups rows
            # beneath a device header -- so it is recorded rather than left to be
            # guessed from id patterns. It is what identifies a cross-node route.
            row_device: dict[str, str] = {}
            current_serial = ""
            for index in range(self._surface.count(pages.TABLE_BODIES)):
                scope = pages.table_body_nth(index)
                body = self._surface.snapshot(scope)
                if body is None:
                    continue
                if body.has_class(pages.DEVICE_TITLE_CLASS):
                    current_serial = normalise_text(self._surface.visible_text(
                        pages.within(scope, pages.DEVICE_SERIAL)))
                    continue
                for member in self._surface.snapshot_all(
                        pages.within(scope, pages.MEMBER_ROWS)):
                    member_id = member.attr("data-resource-id") or ""
                    if member_id:
                        row_device[member_id] = current_serial

            for snapshot in self._surface.snapshot_all(pages.MEMBER_ROWS):
                resource_id = snapshot.attr("data-resource-id") or ""
                if not resource_id:
                    continue
                box = self._surface.snapshot(pages.member_check(resource_id))
                row_scope = pages.member_row(resource_id)

                actions: dict[RowAction, Affordance] = {}
                for action in RowAction:
                    enabled = self._control(pages.row_action(resource_id, action))
                    if enabled.affordance is Affordance.ENABLED:
                        actions[action] = Affordance.ENABLED
                        continue
                    # A refused row action renders as a disabled span; record that
                    # it exists and is blocked rather than reporting it absent.
                    blocked = self._control(
                        pages.row_action_blocked(resource_id, action))
                    actions[action] = (
                        Affordance.BLOCKED
                        if blocked.affordance is Affordance.BLOCKED
                        else enabled.affordance)

                rows.append(ResourceRow(
                    resource_id=resource_id,
                    device_serial=row_device.get(resource_id, ""),
                    label=normalise_text(
                        self._surface.visible_text(f"{row_scope} {pages.ROW_LABEL}")),
                    role=normalise_text(
                        self._surface.visible_text(f"{row_scope} {pages.ROW_ROLE}")),
                    checked=bool(box and box.checked),
                    status=self._read_status(resource_id),
                    actions=actions,
                ))

            step.note("rows", [r.to_json() for r in rows])
            return tuple(rows)

    def read_devices(self) -> tuple[DeviceView, ...]:
        """Read each device's header: serial, transport security, and access.

        Two indicators sit beside every node serial and both are easy to overlook:

        * a **padlock** — closed when every control on the device is reached over
          https, open when at least one is plain HTTP;
        * an **authorisation mark** — a green check-circle when the Controller can
          read and write, a red or amber triangle when it cannot.

        Reading these first turns "the action failed" into "the Controller is not
        authorised to write to this device", which the interface was saying all
        along.
        """
        with self._recorder.step(
            "read_devices",
            intent="read each device's transport security and access state",
        ) as step:
            self._assert_on("read_devices", _SELECTION_PAGES)
            found: list[DeviceView] = []

            # Indexed rather than iterated-and-read, because a descendant
            # selector built from a plain CSS join resolves against the whole
            # page: every device would report the first block's serial.
            for index in range(self._surface.count(pages.DEVICE_BLOCKS)):
                scope = pages.device_block_nth(index)
                block = self._surface.snapshot(scope)
                if block is None:
                    continue

                secure = self._surface.snapshot(
                    pages.within(scope, pages.DEVICE_TLS_SECURE))
                insecure = self._surface.snapshot(
                    pages.within(scope, pages.DEVICE_TLS_INSECURE))
                blocked = self._surface.snapshot(
                    pages.within(scope, pages.DEVICE_AUTH_READS_BLOCKED))
                warned = self._surface.snapshot(
                    pages.within(scope, pages.DEVICE_AUTH_WRITES_BLOCKED))
                ok = self._surface.snapshot(
                    pages.within(scope, pages.DEVICE_AUTH_OK))

                if blocked is not None:
                    access, reason = DeviceAccess.READS_BLOCKED, blocked.reason
                elif warned is not None:
                    access, reason = DeviceAccess.WRITES_BLOCKED, warned.reason
                elif ok is not None:
                    access, reason = DeviceAccess.AUTHORIZED, ok.reason
                else:
                    access, reason = DeviceAccess.UNKNOWN, ""

                found.append(DeviceView(
                    serial=normalise_text(self._surface.visible_text(
                        pages.within(scope, pages.DEVICE_SERIAL))),
                    address=normalise_text(self._surface.visible_text(
                        pages.within(scope, pages.DEVICE_ADDRESS))),
                    transports=tuple(
                        t.strip() for t in normalise_text(
                            self._surface.visible_text(
                                pages.within(scope, pages.DEVICE_TRANSPORTS))
                        ).split("·") if t.strip()),
                    tls_secure=secure is not None,
                    # Exactly one padlock is rendered per device, so whichever is
                    # present carries the explanation.
                    tls_reason=_first_reason(secure, insecure),
                    access=access,
                    access_reason=reason,
                    inaccessible=block.has_class(pages.DEVICE_INACCESSIBLE_CLASS),
                ))

            step.note("devices", [d.to_json() for d in found])
            return tuple(found)

    def press_refresh(self) -> PageView:
        """Click Refresh to pull the latest values from the Node and Registry.

        The NMOS detail pages deliberately do **not** poll: what you are looking at
        is a stable snapshot from when the page loaded, which is what makes it
        usable for comparison. Nothing changes underneath you until this is clicked.

        Distinct from :meth:`press_reset`, which discards local *edits* on a
        configure page. This fetches newer server-side *data*.
        """
        with self._recorder.step(
            "press_refresh",
            intent="pull the latest published values from the Node and Registry",
            expects_navigation=True,
        ) as step:
            step.touched()
            control = self._require(step, self._control(pages.REFRESH_LINK),
                                    what="the Refresh link")
            page_before = self._recorder.page_id()
            navigations_before = self._surface.navigation_count()
            # Refresh re-requests the same URL, so a URL comparison would see
            # nothing; the navigation counter is what moves.
            self._surface.click(control.selector)
            self._require_wait(step, WaitSignal.PAGE_LOADED,
                               NavigationSince(navigations_before))
            return self._settle_page(step, expect=(page_before,))

    def read_groups(self) -> tuple[GroupView, ...]:
        """List the selectable natural groups, with the node each belongs to.

        A separate read from :meth:`read_rows` because the compatible-senders page
        renders group-only: individual member rows are hidden, so there is nothing
        for ``read_rows`` to return and the group radio is the only way to choose.
        Each group's node comes from the device header it sits under, walked the
        same way as for rows.
        """
        with self._recorder.step(
            "read_groups",
            intent="list the selectable groups and which node each is on",
        ) as step:
            self._assert_on("read_groups", _SELECTION_PAGES)
            found: list[GroupView] = []
            current_serial = ""

            for index in range(self._surface.count(pages.TABLE_BODIES)):
                scope = pages.table_body_nth(index)
                body = self._surface.snapshot(scope)
                if body is None:
                    continue
                if body.has_class(pages.DEVICE_TITLE_CLASS):
                    current_serial = normalise_text(self._surface.visible_text(
                        pages.within(scope, pages.DEVICE_SERIAL)))
                    continue
                for radio in self._surface.snapshot_all(
                        pages.within(scope, pages.GROUP_RADIOS)):
                    ids = tuple(
                        m for m in (radio.attr("data-ids") or "").split(",") if m)
                    if not ids:
                        continue
                    found.append(GroupView(
                        member_ids=ids,
                        label=normalise_text(self._surface.visible_text(
                            pages.within(scope, pages.GROUP_NAME))),
                        device_serial=current_serial,
                        checked=bool(radio.checked),
                    ))

            step.note("groups", [g.to_json() for g in found])
            return tuple(found)

    def read_selection(self) -> SelectionView:
        """Read what the page would submit right now."""
        with self._recorder.step("read_selection",
                                 intent="read the current selection") as step:
            self._assert_on("read_selection", _SELECTION_PAGES)
            view = self._read_selection()
            step.note("selection", view.to_json())
            return view

    def _read_status(self, resource_id: str) -> StatusView:
        """Read one resource's health from its badge and dots."""
        badge = self._surface.snapshot(pages.status_badge(resource_id))
        overall = Health.UNKNOWN
        badge_text = ""
        if badge is not None:
            badge_text = normalise_text(badge.text)
            overall = _health_from_classes(badge.classes)

        facets: dict[str, Health] = {}
        for kind in pages.STATUS_DOT_KINDS:
            dot = self._surface.snapshot(pages.status_dot(resource_id, kind))
            if dot is not None:
                facets[kind] = _health_from_classes(dot.classes)

        return StatusView(resource_id=resource_id, badge_text=badge_text,
                          overall=overall, facets=facets)

    def read_status(self, resource_id: str) -> StatusView:
        """Read a resource's health indicators."""
        with self._recorder.step("read_status",
                                 intent=f"read status of {resource_id}",
                                 args={"resource_id": resource_id}) as step:
            status = self._read_status(resource_id)
            step.note("status", status.to_json())
            return status

    def await_live_status_change(
        self,
        resource_id: str,
        *,
        timeout_ms: int = _LIVE_TIMEOUT_MS,
    ) -> StatusView:
        """Wait for a server-sent status update, measured against the baseline.

        A change is only credited if a marker differs from what the server rendered
        at page load — both of this UI's liveness markers are present from the
        start, so presence alone proves nothing.

        Which marker is watched depends on the page, because they do not all exist
        everywhere. The list pages carry health classes on a status badge; the
        configure pages carry ``data-live-active`` on the per-resource result cells
        and have no badge at all. Watching the badge on a configure page waits on
        something that cannot ever change, then reports "classes [] were unchanged"
        — technically true and thoroughly misleading, since the result cells had in
        fact been updated by the stream.

        With no marker for this resource on this page, that is stated plainly
        rather than dressed up as a failed observation.

        Timing out raises :class:`LiveUpdateNotObserved` — deliberately distinct
        from a generic timeout — so a scenario can record an honest "unconfirmed"
        instead of failing, or worse, claiming an update it never saw.
        """
        with self._recorder.step(
            "await_live_status_change",
            intent=f"wait for a live status update for {resource_id}",
            args={"resource_id": resource_id},
        ) as step:
            badge_baseline = self._health_baseline.get(resource_id)
            cell_key = next(
                (k for k in (f"sender:{resource_id}", f"receiver:{resource_id}")
                 if k in self._live_baseline), None)

            if badge_baseline:
                spec: WaitSpec = ClassSetChangedFrom(
                    pages.status_badge(resource_id),
                    pages.HEALTH_CLASS_PREFIX, badge_baseline)
                step.note("watching", "status badge health classes")
                step.note("baseline", sorted(badge_baseline))
            elif cell_key is not None:
                side = cell_key.split(":", 1)[0]
                baseline = self._live_baseline[cell_key]
                spec = AttrChangedFrom(
                    pages.result_cell(resource_id,
                                      receiver_side=(side == "receiver")),
                    "data-live-active", baseline)
                step.note("watching", f"{side} result cell data-live-active")
                step.note("baseline", baseline)
            else:
                self._sse_verdict = SseVerdict.NOT_EXERCISED
                raise LiveUpdateNotObserved(
                    f"this page carries no liveness marker for {resource_id}, so "
                    f"there is nothing here that could evidence a server-sent "
                    f"update. Not recorded as unconfirmed, because nothing was "
                    f"actually watched")

            outcome = self._wait(step, WaitSignal.LIVE_STATUS_CHANGED, spec,
                                 timeout_ms=timeout_ms)

            if not outcome.satisfied:
                self._sse_verdict = SseVerdict.UNCONFIRMED
                raise LiveUpdateNotObserved(
                    f"no live status change for {resource_id} within "
                    f"{timeout_ms} ms against the page-load baseline, so nothing "
                    f"here evidences a server-sent update — recorded as "
                    f"unconfirmed rather than as a pass")

            self._sse_verdict = SseVerdict.CONFIRMED
            status = self._read_status(resource_id)
            step.note("status", status.to_json())
            return status

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def clear_selection(self) -> SelectionView:
        """Untick everything, and confirm the page agrees it is empty.

        Mandatory on arrival at any selection page. The page restores the previous
        selection from session storage, so a scenario that skipped this would
        submit whatever a *previous* run happened to leave behind while looking
        entirely deliberate.
        """
        with self._recorder.step(
            "clear_selection",
            intent="clear any selection restored from a previous visit",
        ) as step:
            self._assert_on("clear_selection", _SELECTION_PAGES)
            step.touched()

            cleared: list[str] = []
            for snapshot in self._surface.snapshot_all(pages.MEMBER_CHECKS):
                if not snapshot.checked:
                    continue
                ids = snapshot.attr("data-ids") or ""
                selector = pages.member_check(ids)
                self._surface.uncheck(selector)
                cleared.append(ids)

            for snapshot in self._surface.snapshot_all(pages.GROUP_RADIOS):
                if snapshot.checked:
                    # A radio cannot be unticked directly; unticking its members is
                    # what the page's own handler responds to.
                    for member in (snapshot.attr("data-ids") or "").split(","):
                        if member:
                            self._surface.uncheck(pages.member_check(member))
                            cleared.append(member)

            step.note("cleared", cleared)
            view = self._read_selection()
            step.note("selection", view.to_json())
            if not view.empty:
                raise WaitTimeout(
                    f"selection is still {view.checked_ids} after unticking "
                    f"everything visible", signal=str(WaitSignal.NONE))
            return view

    def select_resource(self, *, resource_id: str) -> SelectionView:
        """Tick one resource's checkbox.

        Reports ``dropped_ids`` when the page silently unticked something else.
        Choosing a member of a different group causes that, with no change event,
        so the difference between what was asked for and what is now selected has
        to be looked for rather than assumed away.
        """
        with self._recorder.step(
            "select_resource",
            intent=f"select {resource_id}",
            args={"resource_id": resource_id},
        ) as step:
            self._assert_on("select_resource", _SELECTION_PAGES)
            step.touched()

            before = self._read_selection()
            control = self._require(
                step, self._control(pages.member_check(resource_id)),
                what=f"the selection checkbox for {resource_id}")
            self._surface.check(control.selector)
            self._require_wait(step, WaitSignal.RADIO_SELECTED,
                               checked(control.selector, True))

            after = self._read_selection()
            dropped = tuple(
                sorted(set(before.checked_ids) - set(after.checked_ids)))
            view = SelectionView(
                checked_ids=after.checked_ids,
                group_ids=after.group_ids,
                submitted_sender_ids=after.submitted_sender_ids,
                submitted_receiver_ids=after.submitted_receiver_ids,
                mode=after.mode,
                dropped_ids=dropped,
            )
            step.note("selection", view.to_json())
            if dropped:
                step.note("warning",
                          f"the page silently unticked {list(dropped)} when "
                          f"{resource_id} was selected")
            return view

    def deselect_resource(self, *, resource_id: str) -> SelectionView:
        """Untick one resource's checkbox."""
        with self._recorder.step(
            "deselect_resource",
            intent=f"deselect {resource_id}",
            args={"resource_id": resource_id},
        ) as step:
            self._assert_on("deselect_resource", _SELECTION_PAGES)
            step.touched()
            control = self._require(
                step, self._control(pages.member_check(resource_id)),
                what=f"the selection checkbox for {resource_id}")
            self._surface.uncheck(control.selector)
            self._require_wait(step, WaitSignal.RADIO_SELECTED,
                               checked(control.selector, False))
            view = self._read_selection()
            step.note("selection", view.to_json())
            return view

    def select_group(self, *, member_id: str) -> SelectionView:
        """Choose the whole group containing a member, via its radio."""
        with self._recorder.step(
            "select_group",
            intent=f"select the group containing {member_id}",
            args={"member_id": member_id},
        ) as step:
            self._assert_on("select_group", _SELECTION_PAGES)
            step.touched()

            matches = [
                snapshot for snapshot in self._surface.snapshot_all(pages.GROUP_RADIOS)
                if member_id in (snapshot.attr("data-ids") or "").split(",")
            ]
            if not matches:
                raise ControlAbsent(
                    f"no group radio on this page contains {member_id}",
                    selector=pages.GROUP_RADIOS,
                    page_id=self._recorder.page_id())
            if len(matches) > 1:
                raise AmbiguousTarget(
                    f"{member_id} appears in {len(matches)} groups",
                    selector=pages.GROUP_RADIOS, matches=len(matches))

            ids = matches[0].attr("data-ids") or ""
            selector = f'input[type="radio"][name="_group"][data-ids="{ids}"]'
            control = self._require(step, self._control(selector),
                                    what=f"the group radio for {member_id}")
            self._surface.check(control.selector)
            self._require_wait(step, WaitSignal.RADIO_SELECTED,
                               checked(control.selector, True))
            view = self._read_selection()
            step.note("selection", view.to_json())
            return view

    def submit_selection(self, *, secondary: bool = False) -> PageView:
        """Submit the selection form, racing navigation against the page's guard.

        The page validates the selection in JavaScript and refuses invalid ones
        with a native alert *before* navigating. Those two outcomes need different
        handling, so the wait names both branches and reports which won — the
        alternative is mistaking a refusal for a slow page.

        The submitting marker is deliberately not treated as success. It clears
        itself after four seconds, so its absence is indistinguishable from never
        having clicked.
        """
        with self._recorder.step(
            "submit_selection",
            intent="submit the current selection",
            expects_navigation=True,
            args={"secondary": secondary},
        ) as step:
            page_id = self._assert_on("submit_selection", _SELECTION_PAGES + _CAPS_PAGES)
            step.touched()
            form = self._current_form()
            selector = (pages.form_secondary_submit(form) if secondary
                        else pages.form_submit(form))
            control = self._require(step, self._control(selector),
                                    what="the form's submit button")

            selection = self._read_selection()
            step.note("submitting", selection.to_json())

            url_before = self._surface.url
            dialogs_before = self._recorder.dialogs_seen
            self._surface.click(control.selector)

            outcome = self._require_wait(
                step, WaitSignal.SELECTION_SUBMITTED,
                AnyOf((
                    ("navigated", UrlChangedFrom(url_before)),
                    ("guarded", DialogRaised(since=dialogs_before)),
                )))

            if outcome.branch == "guarded":
                messages = [d.message for d in self._surface.take_dialogs()]
                step.dialogs.extend(messages)
                # The browser must not have moved: the guard runs before submit.
                if self._surface.url != url_before:
                    raise WaitTimeout(
                        "a guard alert fired but the page navigated anyway",
                        signal=str(WaitSignal.SELECTION_SUBMITTED))
                raise SelectionGuard(
                    f"the page refused this selection: "
                    f"{messages[0] if messages else '(no message captured)'}",
                    alert_text=messages[0] if messages else "")

            return self._settle_page(step)

    # ------------------------------------------------------------------
    # Row actions
    # ------------------------------------------------------------------

    def open_row_action(self, *, resource_id: str, action: RowAction) -> PageView:
        """Follow one of a row's action links.

        Distinguishes the three ways the server can decline: an enabled anchor, a
        disabled span meaning the action does not apply, and outright absence.
        """
        with self._recorder.step(
            "open_row_action",
            intent=f"open the {action} view for {resource_id}",
            expects_navigation=True,
            args={"resource_id": resource_id, "action": action},
        ) as step:
            self._assert_on("open_row_action", _SELECTION_PAGES)
            enabled = self._control(pages.row_action(resource_id, action))

            if enabled.affordance is not Affordance.ENABLED:
                # Look for the refused rendering before concluding absence, so
                # "does not apply here" is reported as a block with its reason
                # rather than as a missing control.
                blocked = self._control(
                    pages.row_action_blocked(resource_id, action))
                if blocked.affordance is Affordance.BLOCKED:
                    step.examined(blocked)
                    raise BlockedControl(
                        f"the {action} action for {resource_id} is refused: "
                        f"{blocked.reason or '(no reason given)'}",
                        reason=blocked.reason, rendered_as=blocked.kind,
                        selector=blocked.selector, control_text=blocked.text,
                        page_id=self._recorder.page_id(),
                        screenshot=step.directory / "after.png")

            step.touched()
            control = self._require(step, enabled,
                                    what=f"the {action} action for {resource_id}")
            url_before = self._surface.url
            self._surface.click(control.selector)
            self._require_wait(step, WaitSignal.PAGE_LOADED,
                               UrlChangedFrom(url_before))
            return self._settle_page(step)

    def open_sdp(self) -> PageView:
        """Follow the SDP link from a transport-detail page.

        The SDP transport file sits one click below the transport view rather than
        beside it, and the link is rendered only when the resource has an SDP at
        all — so an absent link means "nothing to show for this resource", not a
        gap in the interface. That distinction is why this is
        :class:`ControlAbsent` rather than a failure.
        """
        with self._recorder.step(
            "open_sdp",
            intent="view the SDP transport file",
            expects_navigation=True,
        ) as step:
            self._assert_on("open_sdp", (PageId.TRANSPORT_DETAIL,))
            step.touched()
            control = self._require(step, self._control(pages.SDP_LINK),
                                    what="the SDP transport file link")
            url_before = self._surface.url
            self._surface.click(control.selector)
            self._require_wait(step, WaitSignal.PAGE_LOADED,
                               UrlChangedFrom(url_before))
            return self._settle_page(step, expect=(PageId.SDP_VIEW,))

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def read_constraint_sets(self) -> tuple[ConstraintSetRow, ...]:
        """List the constraint sets a capabilities page offers."""
        with self._recorder.step(
            "read_constraint_sets",
            intent="list the offered constraint sets",
        ) as step:
            self._assert_on("read_constraint_sets", _CAPS_PAGES)
            found: list[ConstraintSetRow] = []

            for snapshot in self._surface.snapshot_all(pages.CAPS_ROWS):
                key = snapshot.attr("data-caps-row") or ""
                resource_id, index = _split_caps_key(key)
                if resource_id is None or index is None:
                    continue

                found.append(self._one_constraint_set(resource_id, index))

            step.note("constraint_sets", [r.to_json() for r in found])
            return tuple(found)

    def choose_constraint_set(self, *, resource_id: str, index: int) -> ConstraintSetRow:
        """Choose a constraint set without expanding its detail.

        Clicks the radio input directly. The row-level click handler returns early
        for input targets, so this selects without also toggling the panel —
        keeping the journal's account of what happened exact.
        """
        with self._recorder.step(
            "choose_constraint_set",
            intent=f"choose constraint set {index} of {resource_id}",
            args={"resource_id": resource_id, "index": index},
        ) as step:
            self._assert_on("choose_constraint_set", _CAPS_PAGES)
            step.touched()
            selector = pages.caps_row_radio(resource_id, index)
            control = self._require(step, self._control(selector),
                                    what=f"constraint set {index} of {resource_id}")
            self._surface.check(control.selector)
            self._require_wait(step, WaitSignal.RADIO_SELECTED,
                               checked(control.selector, True))
            row = self._one_constraint_set(resource_id, index)
            step.note("constraint_set", row.to_json())
            return row

    def set_constraint_set_expanded(
        self, *, resource_id: str, index: int, expanded: bool,
    ) -> ConstraintSetRow:
        """Expand or collapse a constraint set's detail panel.

        Clicking a cell also selects the row's radio, because the page's handler
        does both. That second effect is recorded rather than glossed over — a
        journal that reported only the expansion would be describing something the
        operator did not actually cause.
        """
        with self._recorder.step(
            "set_constraint_set_expanded",
            intent=f"{'expand' if expanded else 'collapse'} constraint set "
                   f"{index} of {resource_id}",
            args={"resource_id": resource_id, "index": index,
                  "expanded": expanded},
        ) as step:
            self._assert_on("set_constraint_set_expanded", _CAPS_PAGES)
            step.touched()
            control = self._require(
                step, self._control(pages.caps_row_cell(resource_id, index)),
                what=f"constraint set {index}'s summary cell")

            detail = pages.caps_details(resource_id, index)
            before = self._surface.snapshot(detail)
            already = bool(before and not before.has_attr("hidden"))
            if already is expanded:
                step.note("no_change", f"already {'expanded' if expanded else 'collapsed'}")
                return self._one_constraint_set(resource_id, index)

            self._surface.click(control.selector)
            spec = (attr_absent(detail, "hidden") if expanded
                    else class_absent(pages.caps_row(resource_id, index),
                                      "is-expanded"))
            self._require_wait(step, WaitSignal.CAPS_DETAIL_TOGGLED, spec)

            row = self._one_constraint_set(resource_id, index)
            step.note("constraint_set", row.to_json())
            step.note("side_effect",
                      "clicking the summary cell also selects this row's radio")
            return row

    def _one_constraint_set(self, resource_id: str, index: int) -> ConstraintSetRow:
        """Read one constraint-set row from the cells the template emits.

        The name lives in ``td.cs-label`` and the preference in the last column —
        not in ``data-`` attributes, and not in the disclosure cell, which holds
        only "▸ #N". Preference is what identifies a native set.

        Format, layer and preference carry no class, so they are addressed by
        position — and the position depends on the page. The read-only
        ``receivers/view-caps`` table has nothing to submit and so emits no
        leading radio cell, putting every one of those three columns one place
        earlier than on the selectable tables. Deriving the layout from the page
        is what keeps a shifted read from passing itself off as real data.
        """
        columns = pages.caps_columns(self._recorder.page_id())
        row = pages.caps_row(resource_id, index)
        snapshot = self._surface.snapshot(row)
        radio = self._surface.snapshot(pages.caps_row_radio(resource_id, index))
        detail = self._surface.snapshot(pages.caps_details(resource_id, index))
        label_cell = self._surface.snapshot(
            pages.within(row, pages.CAPS_CELL_LABEL))

        def cell(selector: str) -> str:
            text = normalise_text(
                self._surface.visible_text(pages.within(row, selector)))
            # The template renders an em dash for "not applicable".
            return "" if text in ("—", "-") else text

        return ConstraintSetRow(
            resource_id=resource_id,
            index=index,
            label=normalise_text(label_cell.text) if label_cell else "",
            media_type=cell(pages.CAPS_CELL_MEDIA_TYPE),
            meta_format=cell(columns.meta_format),
            meta_layer=cell(columns.meta_layer),
            preference=_as_int(cell(columns.preference)),
            part=(snapshot.attr("data-cs-part") or "") if snapshot else "",
            chosen=bool(radio and radio.checked),
            expanded=bool(detail and not detail.has_attr("hidden")),
            # The green flow-match class sits on the label cell, not the row.
            flow_match=bool(label_cell
                            and label_cell.has_class(pages.FLOW_MATCH_CLASS)),
            selectable=bool(radio and radio.enabled),
        )

    def continue_to_configuration(self) -> PageView:
        """Submit the capabilities form to reach the configure page."""
        with self._recorder.step(
            "continue_to_configuration",
            intent="continue to configuration",
            expects_navigation=True,
        ) as step:
            self._assert_on("continue_to_configuration", _CAPS_PAGES)
            step.touched()
            control = self._require(
                step, self._control(pages.form_submit(pages.CAPS_FORM)),
                what="the continue button")

            url_before = self._surface.url
            dialogs_before = self._recorder.dialogs_seen
            self._surface.click(control.selector)
            outcome = self._require_wait(
                step, WaitSignal.SELECTION_SUBMITTED,
                AnyOf((
                    ("navigated", UrlChangedFrom(url_before)),
                    ("guarded", DialogRaised(since=dialogs_before)),
                )))
            if outcome.branch == "guarded":
                messages = [d.message for d in self._surface.take_dialogs()]
                step.dialogs.extend(messages)
                raise SelectionGuard(
                    f"the page refused to continue: "
                    f"{messages[0] if messages else '(no message)'}",
                    alert_text=messages[0] if messages else "")
            return self._settle_page(step, expect=_CONFIGURE_PAGES)

    # ------------------------------------------------------------------
    # Configure
    # ------------------------------------------------------------------

    def read_parameters(self, *, sender_id: str | None = None) -> tuple[ParamWidget, ...]:
        """List the editable parameters on a configure page."""
        with self._recorder.step(
            "read_parameters",
            intent="list the editable parameters",
            args={"sender_id": sender_id or "(all)"},
        ) as step:
            self._assert_on("read_parameters", _CONFIGURE_PAGES)
            widgets: list[ParamWidget] = []

            for snapshot in self._surface.snapshot_all(pages.PARAM_INPUTS):
                owner = snapshot.attr("data-sender-id") or ""
                if sender_id is not None and owner != sender_id:
                    continue
                urn = snapshot.attr("data-param-urn") or ""
                part = snapshot.attr("data-cs-part") or ""
                selector = pages.param_widget(owner, urn, part)
                control = classify(selector, snapshot)

                options = self._surface.options(selector)
                widgets.append(ParamWidget(
                    sender_id=owner,
                    urn=urn,
                    part=part,
                    kind=_widget_kind(snapshot.tag, snapshot.classes),
                    value=snapshot.value or "",
                    options=tuple(o.value for o in options),
                    flow_matched_options=tuple(
                        o.value for o in options
                        if pages.FLOW_MATCH_CLASS in o.classes),
                    affordance=control.affordance,
                    reason=control.reason,
                ))

            step.note("parameters", [w.to_json() for w in widgets])
            return tuple(widgets)

    def set_parameter(
        self, *, sender_id: str, urn: str, value: str, part: str = "trunk",
    ) -> ParamWidget:
        """Set one parameter, then confirm the page reflects the new value."""
        with self._recorder.step(
            "set_parameter",
            intent=f"set {urn} to {value}",
            args={"sender_id": sender_id, "urn": urn, "value": value,
                  "part": part},
        ) as step:
            self._assert_on("set_parameter", _CONFIGURE_PAGES)
            step.touched()
            selector = pages.param_widget(sender_id, urn, part)
            control = self._require(step, self._control(selector),
                                    what=f"the widget for {urn}")

            snapshot = control.snapshot
            kind = _widget_kind(snapshot.tag if snapshot else "",
                                snapshot.classes if snapshot else frozenset())

            if kind == "select":
                offered = tuple(o.value for o in self._surface.options(selector))
                if value not in offered:
                    raise NoSuchOption(
                        f"{value!r} is not offered for {urn}; the page offers "
                        f"{list(offered)}", offered=offered)
                self._surface.select_options(selector, (value,))
            elif kind == "range":
                self._surface.set_range(selector, value)
            else:
                self._surface.type_text(selector, value)

            self._require_wait(step, WaitSignal.PARAM_APPLIED,
                               First(selector, ValueIs(value)))

            widgets = [w for w in self._read_parameters_raw()
                       if w.sender_id == sender_id and w.urn == urn
                       and w.part == part]
            widget = widgets[0] if widgets else ParamWidget(
                sender_id=sender_id, urn=urn, part=part, value=value)
            step.note("parameter", widget.to_json())
            return widget

    def _read_parameters_raw(self) -> tuple[ParamWidget, ...]:
        """Read parameter widgets without opening a step."""
        widgets: list[ParamWidget] = []
        for snapshot in self._surface.snapshot_all(pages.PARAM_INPUTS):
            owner = snapshot.attr("data-sender-id") or ""
            urn = snapshot.attr("data-param-urn") or ""
            part = snapshot.attr("data-cs-part") or ""
            selector = pages.param_widget(owner, urn, part)
            control = classify(selector, snapshot)
            widgets.append(ParamWidget(
                sender_id=owner, urn=urn, part=part,
                kind=_widget_kind(snapshot.tag, snapshot.classes),
                value=snapshot.value or "",
                affordance=control.affordance, reason=control.reason,
            ))
        return tuple(widgets)

    def read_toggles(self) -> dict[ToggleAction, Control]:
        """Inspect the master toggles without pressing any of them.

        The equivalent of looking at the buttons and hovering for their tooltips.
        This exists so a gating demonstration can report *why* an action is refused
        without ever attempting it — pressing a toggle that turned out to be
        available would issue real IS-05/IS-11 calls, which is not something a
        read-only walkthrough may do.
        """
        with self._recorder.step(
            "read_toggles",
            intent="inspect the action buttons without pressing them",
        ) as step:
            self._assert_on("read_toggles", _CONFIGURE_PAGES)
            found: dict[ToggleAction, Control] = {}
            for action in ToggleAction:
                control = self._control(pages.toggle(action))
                step.examined(control)
                found[action] = control
            step.note("toggles", {str(a): c.describe() for a, c in found.items()})
            return found

    def press_toggle(self, action: ToggleAction) -> ActionOutcome:
        """Press a master toggle and read the per-resource outcome.

        The outcome is captured the instant the working marker clears, and that
        timing is not incidental. The next status frame rewrites each result cell's
        class, replaces its text with ``active``/``idle``, and deletes the tooltip
        holding the full error body — so a capture even slightly later shows a
        believable screen from which the result has silently disappeared.

        Success is read from the result cells only. The button's own colour is a
        three-state summary — on, off, or amber/``mixed`` after a partial success —
        so it says whether the resources now agree, not which ones failed or why.
        Only the cells carry the per-resource verdict this method returns.
        """
        with self._recorder.step(
            "press_toggle",
            intent=f"press {action}",
            args={"action": action},
        ) as step:
            self._assert_on("press_toggle", _CONFIGURE_PAGES)
            selector = pages.toggle(action)
            control = self._require(step, self._control(selector),
                                    what=f"the {action} button")

            receiver_side = action is ToggleAction.ACTIVATE_RECEIVERS
            cells_selector = (pages.RESULT_CELLS_RECEIVER if receiver_side
                              else pages.RESULT_CELLS_SENDER)
            aria_before = control.snapshot.attr("aria-pressed") if control.snapshot else None

            step.touched()
            self._surface.click(selector)

            # The working class spans the sequential per-resource request loop, so
            # its *departure* is the reliable completion signal. Its arrival is
            # only diagnostic, and deliberately given a short timeout: a single
            # fast request can be finished before the first poll looks, and
            # spending the full step budget confirming that would add fifteen
            # seconds per action while proving nothing. A miss here is recorded
            # rather than treated as a problem — the result cells are the evidence.
            self._wait(step, WaitSignal.TOGGLE_STARTED,
                       class_present(selector, pages.WORKING_CLASS),
                       timeout_ms=_TOGGLE_START_PROBE_MS)
            self._require_wait(step, WaitSignal.TOGGLE_FINISHED,
                               class_absent(selector, pages.WORKING_CLASS),
                               timeout_ms=max(self._timeout, 60_000))

            # Captured immediately, with no intervening wait. The page sets every
            # result cell to its terminal state *inside* the action loop and only
            # then removes the working class, so by this point the outcomes are
            # already there. Waiting for them again would be redundant and, worse,
            # would race the next status frame: that frame resets the cell's class,
            # replaces its text with active/idle, and deletes the tooltip holding
            # the error body. An earlier version did wait here and lost the result
            # to exactly that race.
            cells = self._read_results_raw(receiver_side=receiver_side)

            # A cell still pending means the loop genuinely has not finished for it.
            pending = [c for c in cells if c.kind == pages.RESULT_PENDING]
            if pending:
                self._require_wait(
                    step, WaitSignal.RESULTS_TERMINAL,
                    all_terminal(cells_selector, pages.RESULT_TERMINAL),
                    timeout_ms=self._timeout)
                cells = self._read_results_raw(receiver_side=receiver_side)

            # A cell reading plain state means the status stream overwrote the
            # outcome before it could be read. Reported rather than hidden: the
            # action did something, but this run cannot say what.
            overwritten = [c for c in cells if c.kind == "state"]
            if overwritten:
                step.note(
                    "results_overwritten_by_status_stream",
                    [c.resource_id for c in overwritten])
            after = self._surface.snapshot(selector)
            outcome = ActionOutcome(
                action=action,
                cells=cells,
                aria_pressed_before=aria_before,
                aria_pressed_after=after.attr("aria-pressed") if after else None,
                confirmed_by="result cells captured at the is-working edge",
            )
            step.note("outcome", outcome.to_json())

            if outcome.failures:
                raise ActionFailed(
                    f"{action} failed for "
                    f"{[c.resource_id for c in outcome.failures]}",
                    failures=tuple((c.resource_id, c.text)
                                   for c in outcome.failures))
            return outcome

    def _read_results_raw(self, *, receiver_side: bool = False) -> tuple[ResultCell, ...]:
        """Read the result cells, pairing each with its page-load baseline."""
        selector = (pages.RESULT_CELLS_RECEIVER if receiver_side
                    else pages.RESULT_CELLS_SENDER)
        attribute = ("data-result-for-receiver" if receiver_side
                     else "data-result-for")
        side = "receiver" if receiver_side else "sender"

        cells: list[ResultCell] = []
        for snapshot in self._surface.snapshot_all(selector):
            resource = snapshot.attr(attribute) or ""
            kind = "state"
            for candidate in (*pages.RESULT_TERMINAL, pages.RESULT_PENDING):
                if snapshot.has_class(candidate):
                    kind = candidate
                    break
            cells.append(ResultCell(
                resource_id=resource,
                side=side,
                kind=kind,
                text=normalise_text(snapshot.text),
                detail=snapshot.reason,
                live_active=snapshot.attr("data-live-active"),
                baseline_live_active=self._live_baseline.get(f"{side}:{resource}"),
            ))
        return tuple(cells)

    def read_results(self, *, receiver_side: bool = False) -> tuple[ResultCell, ...]:
        """Read the result cells as they stand now."""
        with self._recorder.step(
            "read_results",
            intent="read the per-resource result cells",
            args={"receiver_side": receiver_side},
        ) as step:
            self._assert_on("read_results", _CONFIGURE_PAGES)
            cells = self._read_results_raw(receiver_side=receiver_side)
            step.note("results", [c.to_json() for c in cells])
            return cells

    def press_reset(self) -> PageView:
        """Discard local edits and reload, returning to the current settings.

        Mechanically a reload: the handler clears the values persisted in
        ``localStorage`` for the senders on this page and then reloads
        (``controller.js:1090-1092``), so what comes back is whatever the server
        currently renders.

        Note the button's own tooltip says "revert to the constraint-set defaults",
        which is looser than what happens — nothing is reset to a default, the page
        is simply re-rendered from current server state. The distinction matters to
        a scenario that asserts on what it sees afterwards.
        """
        with self._recorder.step(
            "press_reset",
            intent="discard edits and revert to constraint-set defaults",
            expects_navigation=True,
        ) as step:
            page_id = self._assert_on("press_reset", _CONFIGURE_PAGES)
            step.touched()
            control = self._require(step, self._control(pages.RESET_BUTTON),
                                    what="the reset button")
            # Reset reloads the same URL, so a URL comparison would never see it.
            navigations_before = self._surface.navigation_count()
            self._surface.click(control.selector)
            self._require_wait(step, WaitSignal.PAGE_LOADED,
                               NavigationSince(navigations_before))
            view = self._settle_page(step, expect=(page_id,))
            step.note("after_reset",
                      "local edits discarded; the page now shows current settings")
            return view

    def read_reverse_links(self) -> dict[str, Control]:
        """Inspect the reverse-direction companion controls without following any.

        Returns one entry per ``data-reverse-group``. These are the shape-shifters:
        an ``<a href>`` when the companion pair resolves across the two nodes, a
        ``<button disabled>`` with an explanation when it does not — so reading them
        first is how a scenario reports *why* a companion path is unavailable
        instead of merely finding nothing to click.
        """
        with self._recorder.step(
            "read_reverse_links",
            intent="inspect the reverse-direction companion controls",
        ) as step:
            self._assert_on("read_reverse_links", _CONFIGURE_PAGES)
            found: dict[str, Control] = {}
            for snapshot in self._surface.snapshot_all(pages.REVERSE_LINKS):
                group = snapshot.attr("data-reverse-group") or ""
                if not group:
                    continue
                control = classify(pages.reverse_link(group), snapshot)
                step.examined(control)
                found[group] = control
            step.note("reverse_links",
                      {g: c.describe() for g, c in found.items()})
            return found

    def open_reverse_direction(self, *, group: str) -> PageView:
        """Follow a reverse-direction link.

        The control changes tag with its state — a disabled button when the group
        cannot be resolved, an anchor when it can — so one selector finds either
        and the classification follows what was actually rendered.
        """
        with self._recorder.step(
            "open_reverse_direction",
            intent=f"open reverse-direction setup for group {group}",
            expects_navigation=True,
            args={"group": group},
        ) as step:
            self._assert_on("open_reverse_direction", _CONFIGURE_PAGES)
            control = self._control(pages.reverse_link(group))
            step.note("rendered_as", str(control.kind))
            step.touched()
            usable = self._require(step, control,
                                   what=f"the reverse-direction link for {group}")
            url_before = self._surface.url
            self._surface.click(usable.selector)
            self._require_wait(step, WaitSignal.PAGE_LOADED,
                               UrlChangedFrom(url_before))
            return self._settle_page(step)

    # ------------------------------------------------------------------
    # Privacy
    # ------------------------------------------------------------------

    def _read_privacy_raw(self) -> PrivacyView | None:
        """Read the privacy panel, or ``None`` when the selection has none."""
        panel = self._surface.snapshot(pages.PRIVACY_PANEL)
        if panel is None:
            return None

        exclusivity = self._exclusivity_control()
        protocol = self._surface.snapshot(pages.PRIVACY_PROTOCOL)
        mode = self._surface.snapshot(pages.PRIVACY_MODE)
        curve = self._surface.snapshot(pages.PRIVACY_CURVE)
        locked_note = self._surface.snapshot(pages.PRIVACY_LOCKED_NOTE)

        return PrivacyView(
            pep_indicator=normalise_text(
                self._surface.visible_text(pages.PRIVACY_PEP_INDICATOR)),
            reservation_status=normalise_text(
                self._surface.visible_text(pages.PRIVACY_RESERVATION_STATUS)),
            locked=bool(locked_note and locked_note.visible),
            pending=panel.has_class(pages.PRIVACY_PENDING_CLASS),
            reserved=panel.has_class(pages.PRIVACY_RESERVED_CLASS),
            protocol=protocol.value or "" if protocol else "",
            mode=mode.value or "" if mode else "",
            curve=curve.value or "" if curve else "",
            protocol_options=tuple(
                o.value for o in self._surface.options(pages.PRIVACY_PROTOCOL)),
            mode_options=tuple(
                o.value for o in self._surface.options(pages.PRIVACY_MODE)),
            curve_options=tuple(
                o.value for o in self._surface.options(pages.PRIVACY_CURVE)),
            exclusivity_checked=bool(
                exclusivity.snapshot and exclusivity.snapshot.checked),
            exclusivity_affordance=exclusivity.affordance,
            exclusivity_reason=exclusivity.reason,
        )

    def read_privacy(self) -> PrivacyView | None:
        """Read the privacy panel, if this selection has one."""
        with self._recorder.step("read_privacy",
                                 intent="read the privacy panel") as step:
            self._assert_on("read_privacy", _CONFIGURE_PAGES)
            view = self._read_privacy_raw()
            step.note("privacy", view.to_json() if view else None)
            return view

    def set_privacy(
        self, *, protocol: str | None = None, mode: str | None = None,
        curve: str | None = None,
    ) -> PrivacyView:
        """Change the privacy selectors that are offered and unlocked."""
        with self._recorder.step(
            "set_privacy",
            intent="change the privacy settings",
            args={"protocol": protocol or "-", "mode": mode or "-",
                  "curve": curve or "-"},
        ) as step:
            self._assert_on("set_privacy", _CONFIGURE_PAGES)
            step.touched()

            for selector, value, label in (
                (pages.PRIVACY_PROTOCOL, protocol, "protocol"),
                (pages.PRIVACY_MODE, mode, "mode"),
                (pages.PRIVACY_CURVE, curve, "curve"),
            ):
                if value is None:
                    continue
                control = self._require(step, self._control(selector),
                                        what=f"the privacy {label} selector")
                offered = tuple(o.value for o in self._surface.options(selector))
                if value not in offered:
                    raise NoSuchOption(
                        f"privacy {label} {value!r} is not offered; the page "
                        f"offers {list(offered)}", offered=offered)
                self._surface.select_options(control.selector, (value,))
                self._require_wait(step, WaitSignal.PRIVACY_SETTLED,
                                   First(selector, ValueIs(value)))

            view = self._read_privacy_raw()
            if view is None:
                raise ControlAbsent("the privacy panel disappeared",
                                    selector=pages.PRIVACY_PANEL)
            step.note("privacy", view.to_json())
            return view

    def acquire_exclusivity(self) -> PrivacyView:
        """Acquire the exclusive-access reservation."""
        return self._toggle_exclusivity(acquire=True)

    def release_exclusivity(self) -> PrivacyView:
        """Release the exclusive-access reservation."""
        return self._toggle_exclusivity(acquire=False)

    def _exclusivity_control(self) -> Control:
        """Classify the exclusivity switch, which is split across two elements.

        A Bootstrap ``custom-switch`` is one logical control rendered as two: the
        ``<input>`` holds the real state (``checked``, ``disabled``) but is made
        invisible by the switch styling, while the wrapping ``<label>`` is the
        visible, clickable affordance and carries the ``title`` explaining a lock.

        The composite is assembled from the correct half of each, and deliberately
        does **not** route the input through the generic classifier. That
        classifier checks visibility before refusal — correct for a single-element
        control, where reporting "blocked" for something invisible would claim the
        operator was shown a greyed control and told why. Here the input's
        invisibility is a styling artifact rather than a statement about what the
        operator sees, so applying that rule reported ``HIDDEN``, fell through to
        the label, and produced ``ENABLED`` for a control the server had disabled —
        losing the refusal and its reason entirely.

        So: gating comes from the input, appearance and reason from the label.
        """
        input_snapshot = self._surface.snapshot(pages.PRIVACY_EXCLUSIVITY)
        if input_snapshot is None:
            return Control(Affordance.ABSENT, ControlKind.INPUT,
                           pages.PRIVACY_EXCLUSIVITY)

        label = self._surface.snapshot(pages.PRIVACY_EXCLUSIVITY_LABEL)
        label_selector = pages.PRIVACY_EXCLUSIVITY_LABEL
        text = normalise_text(label.text) if label else normalise_text(input_snapshot.text)
        # The reason exists only on the label; the input has no title at all.
        reason = label.reason if label else ""

        def _built(affordance: Affordance) -> Control:
            return Control(affordance, ControlKind.INPUT,
                           pages.PRIVACY_EXCLUSIVITY, text, reason,
                           input_snapshot, action_selector=label_selector)

        # Gating first, read straight from the input's live property.
        if not input_snapshot.enabled:
            return _built(Affordance.BLOCKED)
        # Then appearance, judged on the element the operator actually sees.
        if label is not None and not label.visible:
            return _built(Affordance.HIDDEN)
        return _built(Affordance.ENABLED)

    def _toggle_exclusivity(self, *, acquire: bool) -> PrivacyView:
        verb = "acquire_exclusivity" if acquire else "release_exclusivity"
        with self._recorder.step(
            verb,
            intent=f"{'acquire' if acquire else 'release'} exclusive access",
        ) as step:
            self._assert_on(verb, _CONFIGURE_PAGES)
            step.touched()
            control = self._require(step, self._exclusivity_control(),
                                    what="the exclusivity switch")
            already = bool(control.snapshot and control.snapshot.checked)
            if already is acquire:
                step.note("no_change",
                          f"exclusivity already {'held' if acquire else 'released'}")
                view = self._read_privacy_raw()
                if view is None:
                    raise ControlAbsent("the privacy panel disappeared",
                                        selector=pages.PRIVACY_PANEL)
                return view

            # Clicked on the label, which is the affordance the operator uses;
            # ``check``/``uncheck`` target the input and would be refused because
            # the switch styling makes it unclickable. The resulting state is then
            # confirmed from the input's live ``checked`` property.
            self._surface.click(control.target)
            # Diagnostic only, deliberately not required: on refusal the page
            # reverts the checkbox, so demanding it flip would time out here and
            # hide the reason waiting one step further on.
            self._wait(step, WaitSignal.RADIO_SELECTED,
                       checked(pages.PRIVACY_EXCLUSIVITY, acquire),
                       timeout_ms=_TOGGLE_START_PROBE_MS)

            self._wait(step, WaitSignal.PRIVACY_PENDING,
                       class_present(pages.PRIVACY_PANEL,
                                     pages.PRIVACY_PENDING_CLASS))
            terminal = (class_present(pages.PRIVACY_PANEL,
                                      pages.PRIVACY_RESERVED_CLASS)
                        if acquire else
                        class_absent(pages.PRIVACY_PANEL,
                                     pages.PRIVACY_RESERVED_CLASS))
            # A refused acquire never reaches the reserved state, so waiting only
            # for success turned "someone else holds this" into a bare timeout —
            # hiding the reason the page was displaying all along. Race the
            # success condition against the page's own failure text instead.
            outcome = self._require_wait(
                step, WaitSignal.PRIVACY_SETTLED,
                AnyOf((
                    ("settled", terminal),
                    ("refused", TextContains(pages.PRIVACY_RESERVATION_STATUS,
                                             "failed")),
                )))
            if outcome.branch == "refused":
                reason = normalise_text(self._surface.visible_text(
                    pages.PRIVACY_RESERVATION_STATUS))
                step.note("refusal", reason)
                # ActionFailed, not BlockedControl. The switch was enabled and we
                # pressed it; the *server* declined — typically because another
                # controller holds the reservation. BlockedControl means the
                # interface refused before acting, and asserting that here would
                # be false: the attempt really was made and really did cost a
                # request. The fidelity invariant catches the difference.
                raise ActionFailed(
                    f"the Controller could not "
                    f"{'take' if acquire else 'release'} exclusive access: "
                    f"{reason}",
                    failures=(("exclusivity", reason),))

            view = self._read_privacy_raw()
            if view is None:
                raise ControlAbsent("the privacy panel disappeared",
                                    selector=pages.PRIVACY_PANEL)
            step.note("privacy", view.to_json())
            return view

    # ------------------------------------------------------------------
    # Evidence
    # ------------------------------------------------------------------

    def teach(
        self,
        heading: str,
        *,
        do: str,
        see: str,
        detail: str = "",
        internals: str = "",
        state: dict[str, str] | None = None,
        sources: tuple[tuple[str, str], ...] = (),
        specs: tuple[tuple[str, str], ...] = (),
    ) -> None:
        """Record a tutorial step describing what was just done and observed.

        Called *after* the action it describes, so the screenshot it captures is
        the result a reader should expect to see. The journal keeps its own
        audit-oriented record either way; this adds the teaching layer on top
        rather than replacing it.

        ``state`` and ``detail`` become the "data behind it" section. ``internals``,
        ``specs`` and ``sources`` become "under the hood": the concept, the
        specification it comes from, and the files that implement it here. All are
        collapsed in the rendered tutorial, and the API calls shown alongside them
        come from what this run actually issued.

        Citing the spec alongside the code is deliberate. A reader learning NMOS
        needs to know both what everyone agreed to and what this project chose;
        showing only the source teaches one implementation, and showing only the
        spec teaches nothing about how it is really built.
        """
        if self._tutorial is None:
            return

        first = self._lesson_mark
        with self._recorder.step("teach", intent=heading,
                                 args={"heading": heading}) as step:
            path = self.journal.write_png(step.directory, "screen",
                                          self._surface.screenshot_png())
            image = self.journal.relative(path)
            step.artifacts["screen"] = image
            step.note("do", do)
            step.note("see", see)

        self._tutorial.add(Lesson(
            heading=heading,
            do=normalise_text(do),
            see=normalise_text(see),
            detail=detail,
            internals=internals,
            images=(image,),
            first_seq=first,
            last_seq=self._recorder.sequence,
            state=dict(state or {}),
            sources=sources,
            specs=specs,
        ))
        self._lesson_mark = self._recorder.sequence + 1

    def note(self, text: str) -> None:
        """Record the agent's own reasoning as a journal step.

        Makes the narrative auditable alongside the screenshots: a reader can see
        not only what was clicked but why the agent believed it should be.
        """
        with self._recorder.step("note", intent=text) as step:
            step.note("text", normalise_text(text))

    def snap(self, label: str) -> Path:
        """Take an extra screenshot with a caption."""
        with self._recorder.step("snap", intent=label,
                                 args={"label": label}) as step:
            path = self.journal.write_png(step.directory, "snap",
                                          self._surface.screenshot_png())
            step.artifacts["snap"] = self.journal.relative(path)
            return path


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------

def _first_reason(*snapshots: ElementSnapshot | None) -> str:
    """The ``title`` of the first snapshot that is present."""
    for snapshot in snapshots:
        if snapshot is not None:
            return snapshot.reason
    return ""


def _as_int(text: str) -> int | None:
    """Parse a rendered integer, or ``None`` when the cell is not one."""
    try:
        return int(text)
    except ValueError:
        return None


def _health_from_classes(classes: frozenset[str]) -> Health:
    """Map an ``is-*`` CSS class to a health value."""
    for member in Health:
        if f"{pages.HEALTH_CLASS_PREFIX}{member.value}" in classes:
            return member
    return Health.UNKNOWN


def _split_caps_key(key: str) -> tuple[str | None, int | None]:
    """Split ``"<resource-id>-<index>"``.

    The resource id is a UUID and therefore contains hyphens, so only the trailing
    ``-<digits>`` may be stripped — splitting on the first hyphen would truncate
    every id.
    """
    if not key:
        return None, None
    head, _, tail = key.rpartition("-")
    if not head or not tail.isdigit():
        return None, None
    return head, int(tail)


def _widget_kind(tag: str, classes: frozenset[str]) -> str:
    """Classify a parameter widget so the right interaction is used."""
    if pages.PARAM_SINGLE_CLASS in classes:
        return "single"
    if tag == "select":
        return "select"
    if "param-range-value" in classes:
        return "range"
    return "text"


def selected_ids(view: SelectionView) -> Sequence[str]:
    """The resource ids a selection will submit, group or individual."""
    return view.group_ids or view.checked_ids


def _origin_of(url: str) -> str:
    """``scheme://host:port`` of a URL, for noting which server a step touched.

    Recorded on the Authorization Server sign-in step so a journal reader can
    see that the operator's credentials went somewhere other than the
    Controller, and exactly where.
    """
    parts = urlsplit(url)
    if not parts.hostname:
        return url
    return f"{parts.scheme}://{parts.netloc}"
