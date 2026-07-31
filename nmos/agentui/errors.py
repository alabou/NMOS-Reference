# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Error hierarchy for the agent-driven UI driver.

Everything here derives from :class:`nmos.errors.NmosError` so the project's
existing ``recoverable`` convention and ``is_recoverable()`` helper apply
unchanged.

Two design rules run through this module:

1. **Distinct causes get distinct types.** A control that the server refused to
   render is not the same thing as a control it rendered and disabled, and a
   live update that never arrived is not the same thing as a generic timeout.
   Collapsing either pair would let a scenario report a misleading outcome.

2. **Errors carry evidence, not just prose.** A failure that a reader cannot
   audit is barely better than no failure at all, so the classes below hold the
   verbatim server-supplied reason, the element the driver looked at, and the
   screenshot taken at the moment of failure.
"""

from __future__ import annotations

from pathlib import Path

from nmos.errors import NmosError

from .enums import Affordance, ControlKind, PageId


class AgentUiError(NmosError):
    """Base class for every agent-UI failure."""


# ---------------------------------------------------------------------------
# Environment and attach-time errors
# ---------------------------------------------------------------------------

class DependencyMissing(AgentUiError):
    """The optional playwright package or its Chromium build is unavailable.

    Carries the exact commands that fix it. Every half-installed state — no
    package, no browsers directory, no binary inside it — funnels here, so a
    caller never sees a raw ``ImportError`` traceback or playwright's own
    multi-line install banner.
    """

    def __init__(self, msg: str, *, remedies: tuple[str, ...] = ()) -> None:
        super().__init__(msg)
        self.remedies = remedies


class NodeNotFound(AgentUiError):
    """No running ``nmos_node.py`` process could be found to attach to."""


class NodeAmbiguous(AgentUiError):
    """Several candidate nodes are running and none was selected.

    Picking one silently would risk driving the wrong rig, which is why this is
    an error rather than a heuristic. ``candidates`` lists ``(pid, control
    port)`` so the caller can disambiguate.
    """

    def __init__(self, msg: str, *, candidates: tuple[tuple[int, int], ...] = ()) -> None:
        super().__init__(msg)
        self.candidates = candidates


class ControllerNotEnabled(AgentUiError):
    """The node is running but was started without ``--nodeControlPort``."""


class AdminPasswordMissing(AgentUiError):
    """The admin password environment variable is unset or empty.

    The password is deliberately *not* harvested from the node's command line.
    It is visible there, so reading it would not be a new disclosure — but a
    tool that quietly scrapes credentials out of process state is a habit worth
    not forming.
    """


class OAuth2NotSupported(AgentUiError):
    """The node requires the OAuth 2.0 sign-in stage, which is not yet driven.

    Raised up front at discovery rather than after the browser has already
    followed a redirect to an authorization server and stalled there.
    """


class TlsPinError(AgentUiError):
    """The Controller UI certificate could not be verified or pinned."""


# ---------------------------------------------------------------------------
# Session and page-model errors
# ---------------------------------------------------------------------------

class ControllerJsNotLoaded(AgentUiError):
    """``controller.js`` never announced itself for the current document.

    The driver waits on class and attribute changes that only that script
    produces. If it did not run — a stale cached asset, a CSP failure — then
    every one of those wait signals is fiction and would time out with a
    misleading message. Failing here instead makes the real cause obvious.
    """


class LoginRejected(AgentUiError):
    """The sign-in form came back with an error message.

    Note the driver never inspects the HTTP status to decide this: a rejected
    sign-in returns 401 *with a fully rendered page*, so status-based logic
    would misclassify it as a missing page. What is observed is the same thing
    the operator sees — still on the login page, with an alert visible.
    """


class SessionLost(AgentUiError):
    """The session stopped being valid mid-run and pages became the login page.

    The middleware looks the session token up rather than recreating it, so a
    node restart invalidates a structurally valid cookie. Without this check
    every subsequent verb would quietly operate on the login page and report
    plausible nonsense.
    """


class TargetUnreachable(AgentUiError):
    """The browser could not load a page at all.

    Distinct from :class:`PageModelMismatch`, which means "a page loaded but not the
    one expected". Here nothing loaded: the browser is sitting on its own error page,
    which is what a node restarting mid-run looks like. Reporting that as an
    unexpected *page* sends a reader looking for a UI problem when the cause is that
    the target went away — routine on a test rig, and worth naming plainly.
    """


class PageModelMismatch(AgentUiError):
    """The current page is not one this verb can operate on."""

    def __init__(self, msg: str, *, expected: tuple[PageId, ...] = (),
                 actual: PageId = PageId.UNKNOWN) -> None:
        super().__init__(msg)
        self.expected = expected
        self.actual = actual


# ---------------------------------------------------------------------------
# Locating and control-state errors
# ---------------------------------------------------------------------------

class AmbiguousTarget(AgentUiError):
    """A human-facing description matched more than one control.

    Acting on "the first match" would make the journal's claim about what was
    clicked unverifiable, so the ambiguity is surfaced with the candidates.
    """

    def __init__(self, msg: str, *, selector: str = "", matches: int = 0,
                 candidates: tuple[str, ...] = ()) -> None:
        super().__init__(msg)
        self.selector = selector
        self.matches = matches
        self.candidates = candidates


class NoSuchOption(AgentUiError):
    """A requested value is not among the options the page actually offers.

    ``offered`` is included because the useful question after this failure is
    always "well, what *was* available?".
    """

    def __init__(self, msg: str, *, offered: tuple[str, ...] = ()) -> None:
        super().__init__(msg)
        self.offered = offered


class ControlNotAvailable(AgentUiError):
    """Base for the three ways a control can be unusable.

    Sharing a base lets a scenario catch "I could not do that" broadly while
    still allowing the precise cases to be distinguished when it matters.
    """

    affordance: Affordance = Affordance.ABSENT

    def __init__(self, msg: str, *, selector: str = "", control_text: str = "",
                 page_id: PageId = PageId.UNKNOWN,
                 screenshot: Path | None = None) -> None:
        super().__init__(msg)
        self.selector = selector
        self.control_text = control_text
        self.page_id = page_id
        self.screenshot = screenshot


class ControlAbsent(ControlNotAvailable):
    """The server did not render the control at all — it does not apply here."""

    recoverable = True
    affordance = Affordance.ABSENT


class ControlHidden(ControlNotAvailable):
    """The control exists but is not visible, so no operator could click it."""

    recoverable = True
    affordance = Affordance.HIDDEN


class BlockedControl(ControlNotAvailable):
    """The control is rendered and visible but refused by policy.

    ``reason`` is the control's ``title`` **verbatim** — the same sentence the
    server computed and the same one an operator sees on hover. It is carried as
    text because native tooltips do not appear in screenshots, so a picture
    alone can never evidence *why* something was blocked.

    ``rendered_as`` records which gating idiom fired, which distinguishes "this
    row action does not apply" (a ``SPAN``) from "policy disabled a live
    control" (a ``BUTTON``).
    """

    recoverable = True
    affordance = Affordance.BLOCKED

    def __init__(self, msg: str, *, reason: str = "",
                 rendered_as: ControlKind = ControlKind.OTHER,
                 selector: str = "", control_text: str = "",
                 page_id: PageId = PageId.UNKNOWN,
                 screenshot: Path | None = None) -> None:
        super().__init__(msg, selector=selector, control_text=control_text,
                         page_id=page_id, screenshot=screenshot)
        self.reason = reason
        self.rendered_as = rendered_as


# ---------------------------------------------------------------------------
# Action-outcome errors
# ---------------------------------------------------------------------------

class SelectionGuard(AgentUiError):
    """The page's own client-side guard rejected a submission via ``alert()``.

    This is a legitimate, operator-visible outcome rather than a driver fault,
    so it is recoverable and carries the alert's exact wording.
    """

    recoverable = True

    def __init__(self, msg: str, *, alert_text: str = "") -> None:
        super().__init__(msg)
        self.alert_text = alert_text


class ActionFailed(AgentUiError):
    """At least one resource reported an error for the attempted action.

    ``failures`` maps resource id to the message the result cell showed. The
    button's own colour is deliberately not consulted: it only flips when
    *every* resource succeeded, so a partial failure and an untouched button
    look identical.
    """

    def __init__(self, msg: str, *, failures: tuple[tuple[str, str], ...] = ()) -> None:
        super().__init__(msg)
        self.failures = failures


class WaitTimeout(AgentUiError):
    """A named wait signal did not arrive in time."""

    def __init__(self, msg: str, *, signal: str = "", spec: str = "",
                 waited_ms: int = 0, observed: str = "") -> None:
        super().__init__(msg)
        self.signal = signal
        self.spec = spec
        self.waited_ms = waited_ms
        self.observed = observed


class LiveUpdateNotObserved(AgentUiError):
    """No live status change was seen within the timeout.

    Kept distinct from :class:`WaitTimeout` so a scenario can record an honest
    "live updates unconfirmed" verdict instead of either failing the run or —
    much worse — reporting a live update it never actually witnessed. Both of
    this UI's liveness markers are present at page load, so only a delta
    against a baseline proves anything.
    """

    recoverable = True


# ---------------------------------------------------------------------------
# Fidelity errors -- the driver caught itself cheating
# ---------------------------------------------------------------------------

class DisallowedAttribute(AgentUiError):
    """A snapshot asked for an attribute outside the allowlist.

    The allowlist is what stops an adapter from quietly growing a private data
    channel out of the DOM and calling it "reading the page".
    """


class FidelityViolation(AgentUiError):
    """The run did something an operator could not have done.

    Raised for a second entry navigation, an extra browser page, a navigation
    no step claimed responsibility for, driver-issued HTTP, or a blanket TLS
    bypass. Any of these invalidates the run's central claim, so the run fails
    rather than producing a journal that overstates what it proved.
    """
