# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The Controller's :class:`AppAdapter` implementation.

Small on purpose. Everything application-specific that the *core* needs is here —
where to attach, which URL is the entry point, how to sign in, how to name a page,
and what to check before trusting any wait signal. The operator-level verbs live
in :mod:`session`, and every selector lives in :mod:`pages`.
"""

from __future__ import annotations

from ...core.adapter import Credentials, Precondition, Target
from ...core.surface import Surface
from ...core.text import contains_text
from ...enums import PageId
from ...errors import OAuth2FormNotRecognised
from . import discovery, pages
from .discovery import ControllerTarget


class ControllerAdapter:
    """Drives the embedded NMOS Controller UI."""

    name = discovery.APP_NAME
    main_selector = pages.BODY

    def __init__(self, found: ControllerTarget) -> None:
        self._found = found

    @property
    def found(self) -> ControllerTarget:
        """The discovered node, for the manifest and trace correlation."""
        return self._found

    def discover(self) -> Target:
        """Return the already-discovered target.

        Discovery runs before the browser starts — a node that cannot be found is
        not worth launching Chromium for — so this hands back the earlier result
        rather than scanning again and risking a different answer.
        """
        return self._found.target

    def entry_url(self, target: Target) -> str:
        """The single URL the driver may navigate to directly."""
        return f"{target.origin}{pages.PREFIX}/"

    def identify_page(self, url: str) -> PageId:
        """Classify a page by its path, or as the Authorization Server by origin.

        The Controller's own origin is passed through so that anything served
        from elsewhere is recognised as the Authorization Server's sign-in page
        rather than falling out as ``UNKNOWN``. See :func:`pages.identify`.
        """
        return pages.identify(url, origin=self._found.target.origin)

    def authenticate_oauth2(
        self, surface: Surface, credentials: Credentials,
    ) -> None:
        """Sign in at the Authorization Server's form.

        The second half of the two-stage login: the Controller's password gate
        establishes a local session, then the browser is redirected to the
        Authorization Server to authenticate a person and authorise this client.

        Each field is located from a candidate list because this markup belongs
        to the Authorization Server, not to this project — see the selector
        block in :mod:`.pages`. A field that matches nothing raises here rather
        than typing into the void, so an unrecognised sign-in form is reported
        as itself instead of as a later, more confusing timeout.
        """
        username = _first_present(surface, pages.OAUTH2_USERNAME_CANDIDATES)
        password = _first_present(surface, pages.OAUTH2_PASSWORD_CANDIDATES)
        submit = _first_present(surface, pages.OAUTH2_SUBMIT_CANDIDATES)
        missing = [
            name for name, found in (
                ("username field", username),
                ("password field", password),
                ("submit button", submit),
            ) if not found
        ]
        if missing:
            raise OAuth2FormNotRecognised(
                f"the Authorization Server's sign-in form at {surface.url} has "
                f"no {', no '.join(missing)}. The driver matches the candidate "
                f"selectors in nmos/agentui/apps/nmos_controller/pages.py; add "
                f"this server's markup there if it is one you need to support."
            )

        surface.type_text(username, credentials.operator_username)
        surface.type_text(password, credentials.oauth2_password)
        surface.click(submit)
        surface.wait_for_load_state()

    def authenticate(self, surface: Surface, credentials: Credentials) -> None:
        """Sign in through the real form.

        Password only — the form has no username field. Performed with the same
        verbs a scenario uses so the sign-in is an ordinary audited interaction
        rather than a privileged short cut.

        The HTTP status is deliberately never consulted. A rejected sign-in
        returns 401 *with a fully rendered page*, so status-based logic would
        misread it as a missing page; what is observed instead is what the operator
        sees — still on the login page, with an alert visible.
        """
        surface.type_text(pages.LOGIN_PASSWORD, credentials.password)
        surface.click(pages.LOGIN_SUBMIT)
        surface.wait_for_load_state()

    def preconditions(self) -> tuple[Precondition, ...]:
        """Checks run once the first authenticated page has loaded."""
        return (
            Precondition(name="controller.js loaded", check=_check_js_beacon),
        )


def _check_js_beacon(surface: Surface) -> str | None:
    """Confirm ``controller.js`` announced itself for this document.

    Every DOM-based wait signal this driver uses — the working class on an action
    button, the terminal result-cell classes, the revealed detail row — is produced
    by that script. If it never ran, those signals do not exist and every wait
    would time out with a message pointing at the wrong thing. Checking the
    beacon turns a confusing cascade into one accurate failure.

    Uses the cumulative console history rather than the per-step drain, because the
    beacon is emitted at page load and the drain would already have consumed it.
    """
    for record in surface.console_history():
        if contains_text(record.text, pages.JS_BEACON_PREFIX):
            return None
    return (
        f"no {pages.JS_BEACON_PREFIX}NN] loaded message was seen on the console. "
        f"controller.js did not run for this document, so the class and "
        f"attribute changes this driver waits on will never appear. A stale "
        f"cached asset is the usual cause."
    )


def _first_present(surface: Surface, candidates: tuple[str, ...]) -> str:
    """The first candidate selector that matches something on the page.

    Returns an empty string when none match, so the caller can report every
    missing field at once instead of failing on the first.
    """
    for selector in candidates:
        if surface.count(selector):
            return selector
    return ""
