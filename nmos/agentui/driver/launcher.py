# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Launching Chromium and performing the one permitted navigation.

Three things live here that nothing above can reach:

**The entry latch.** Exactly one direct navigation is allowed per run — the
equivalent of a person typing an address. A second call raises, and the method is
not reachable from a session object, so everything after the first page must be
arrived at by clicking.

**The TLS negatives.** The assembled argument list is checked to contain no
blanket certificate bypass. This matters because a run with verification disabled
produces screenshots identical to one where it works, so the failure mode is
invisible. The narrow SPKI pin is the only certificate flag that can appear, and
the browser context is created with ``ignore_https_errors`` explicitly false
rather than merely left unset.

**The stable-profile guarantee.** Each run gets a fresh browser profile. The
application remembers selections in ``sessionStorage`` and parameter edits in
``localStorage``, so a reused profile would let a previous run's state silently
become this run's starting point.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Playwright, sync_playwright

from ..core.tls_pin import PinResult, chromium_args
from ..enums import TlsPolicy
from ..errors import DependencyMissing, FidelityViolation
from .pw_surface import PwSurface

#: Argument fragments that would switch off certificate verification wholesale.
#: Asserted absent rather than simply not added, so that a future "fix a flake by
#: adding a flag" change fails loudly instead of quietly ending the run's ability
#: to prove anything about the server's identity.
_FORBIDDEN_ARG_FRAGMENTS = (
    "--ignore-certificate-errors=",
    "--allow-insecure-localhost",
    "--disable-web-security",
    "--allow-running-insecure-content",
)

#: Exact flag that must never appear on its own. Checked separately from the
#: fragments above because the legitimate pinning flag shares its prefix.
_FORBIDDEN_EXACT = ("--ignore-certificate-errors",)

#: Viewport wide enough that the configure page's tables do not collapse into
#: their responsive layout, so a screenshot shows what an operator at a normal
#: window size sees.
_VIEWPORT = {"width": 1600, "height": 1000}


@dataclass(frozen=True, slots=True)
class BrowserInfo:
    """Provenance for the manifest."""

    browser_version: str
    chromium_args: tuple[str, ...]
    headless: bool
    viewport: str


class BrowserRun:
    """A launched browser, its surface, and the one-shot entry navigation."""

    def __init__(
        self,
        *,
        pin: PinResult,
        headless: bool = True,
    ) -> None:
        self._pin = pin
        self._headless = headless
        self._playwright: Playwright | None = None
        self._browser: Any = None
        self._context: Any = None
        self._surface: PwSurface | None = None
        self._entered = False
        self._args: tuple[str, ...] = ()

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> PwSurface:
        """Launch Chromium and return the surface.

        The argument list is assembled and then audited before the browser is
        started, so a forbidden flag can never reach a running process.
        """
        args = chromium_args(self._pin)
        self._assert_no_blanket_bypass(args)
        self._args = args

        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(
                headless=self._headless,
                args=list(args),
            )
        except Exception as exc:
            # Playwright raises its own multi-line install banner when the browser
            # revision is absent. Funnel it into the one actionable message the
            # rest of the driver uses.
            self._playwright.stop()
            self._playwright = None
            raise DependencyMissing(
                f"Chromium failed to launch: {exc}",
                remedies=(
                    'PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright" '
                    "python -m playwright install chromium",
                ),
            ) from exc

        self._context = self._browser.new_context(
            viewport=_VIEWPORT,
            # Explicitly false rather than defaulted: this is the setting whose
            # accidental flip would silently end all certificate validation.
            ignore_https_errors=False,
        )
        page = self._context.new_page()
        surface = PwSurface(page)
        surface.attach_listeners()
        self._surface = surface
        return surface

    def enter(self, url: str) -> None:
        """Perform the single direct navigation of the run.

        Latched: a second call is a fidelity violation, because a driver that can
        navigate at will is no longer constrained to what the interface offers.
        """
        if self._entered:
            raise FidelityViolation(
                f"refusing a second direct navigation to {url!r}: exactly one "
                f"entry URL is permitted per run, and every page after it must "
                f"be reached by clicking"
            )
        if self._context is None or self._surface is None:  # pragma: no cover
            raise FidelityViolation("browser was not started before enter()")

        self._entered = True
        page = self._context.pages[0]
        page.goto(url, wait_until="domcontentloaded")

    def close(self) -> None:
        """Tear down the browser and its temporary profile."""
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    # -- provenance and invariants ----------------------------------------

    def info(self) -> BrowserInfo:
        """Record which browser produced the run's screenshots."""
        version = ""
        if self._browser is not None:
            version = str(self._browser.version)
        return BrowserInfo(
            browser_version=version,
            chromium_args=self._args,
            headless=self._headless,
            viewport=f"{_VIEWPORT['width']}x{_VIEWPORT['height']}",
        )

    @property
    def tls_policy(self) -> TlsPolicy:
        return self._pin.policy

    @staticmethod
    def _assert_no_blanket_bypass(args: tuple[str, ...]) -> None:
        """Refuse to launch with certificate verification switched off."""
        for arg in args:
            if arg in _FORBIDDEN_EXACT:
                raise FidelityViolation(
                    f"refusing to launch with {arg!r}: it disables certificate "
                    f"verification entirely, and a run that does so looks "
                    f"identical to one that validates correctly"
                )
            for fragment in _FORBIDDEN_ARG_FRAGMENTS:
                if arg.startswith(fragment):
                    raise FidelityViolation(
                        f"refusing to launch with {arg!r}: {fragment} weakens or "
                        f"disables security checks the run is meant to exercise"
                    )
