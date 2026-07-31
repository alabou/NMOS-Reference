# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The optional-dependency gate for the agent-UI driver.

Two separate things have to be present before a browser can be launched, and
only one of them is installable by pip:

1. the ``playwright`` Python package, and
2. a Chromium **build**, fetched by ``playwright install chromium``.

A partially-installed environment is the common case — the package installs in
seconds, the browser is a 656 MB download that is easy to skip — so both halves
are checked up front and every failure produces one actionable message naming
the exact command that fixes it. Callers never see a bare ``ImportError`` nor
playwright's own multi-line install banner.

This module deliberately does **not** import playwright at module scope. It is
imported by ``nmos.agentui`` itself, which must stay importable on a node that
has no optional extra installed.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from .errors import DependencyMissing

#: Where the repo-local browser download is expected when the caller has not set
#: ``PLAYWRIGHT_BROWSERS_PATH``. Kept relative to the current working directory
#: because the driver is always run from the project root, exactly like
#: ``nmos_node.py``.
DEFAULT_BROWSERS_DIRNAME = ".playwright"

#: Playwright's own default when the environment variable is unset.
_PLAYWRIGHT_DEFAULT_CACHE = Path.home() / ".cache" / "ms-playwright"

#: Directory-name prefixes a usable Chromium download can appear under. The full
#: browser and the headless shell are separate downloads; either can serve a
#: headless run, so the presence of one is enough to proceed.
_CHROMIUM_PREFIXES = ("chromium-", "chromium_headless_shell-")

#: Executable basenames that indicate a complete download rather than a
#: half-extracted directory.
_CHROMIUM_BINARIES = ("chrome", "headless_shell")

INSTALL_PACKAGE = 'pip install -r requirements-agentui.txt   (or: pip install -e ".[agentui]")'
INSTALL_BROWSER = 'PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright" python -m playwright install chromium'


def browsers_path() -> Path:
    """Return the directory the Chromium download is expected in.

    Honours ``PLAYWRIGHT_BROWSERS_PATH`` because that is what the operator sets
    to keep the browser inside the repo. Falls back to the repo-local default if
    it exists, and only then to playwright's own home-cache location — so a
    correctly provisioned checkout works without any environment variable, while
    an operator who has set one is always obeyed.
    """
    configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if configured:
        return Path(configured)

    local = Path.cwd() / DEFAULT_BROWSERS_DIRNAME
    if local.is_dir():
        return local
    return _PLAYWRIGHT_DEFAULT_CACHE


def _chromium_build_dirs(root: Path) -> list[Path]:
    """Return complete Chromium build directories found under ``root``."""
    if not root.is_dir():
        return []

    found: list[Path] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if not child.name.startswith(_CHROMIUM_PREFIXES):
            continue
        # A directory alone is not proof: an interrupted download leaves the
        # folder behind without the executable. Require the binary itself.
        for binary in _CHROMIUM_BINARIES:
            if any(child.rglob(binary)):
                found.append(child)
                break
    return found


def playwright_available() -> bool:
    """Report whether the ``playwright`` package can be imported.

    Uses ``find_spec`` rather than a ``try: import`` so that merely asking the
    question does not pull the package — and its own subprocess machinery — into
    a process that may not need it.
    """
    return importlib.util.find_spec("playwright") is not None


def require_playwright() -> Path:
    """Assert both halves of the dependency are usable; return the browsers path.

    Raises :class:`DependencyMissing` carrying both exact remedies. Called once
    by the attach entry point, before any browser work begins, so a missing
    dependency fails immediately rather than part-way through a scenario.
    """
    if not playwright_available():
        raise DependencyMissing(
            "the optional 'playwright' package is not installed, so the "
            "agent-UI driver cannot start",
            remedies=(INSTALL_PACKAGE, INSTALL_BROWSER),
        )

    root = browsers_path()
    builds = _chromium_build_dirs(root)
    if not builds:
        raise DependencyMissing(
            f"playwright is installed but no complete Chromium build was found "
            f"under {root} -- pip cannot install a browser, so this is a "
            f"separate step",
            remedies=(INSTALL_BROWSER,),
        )
    return root


def describe_environment() -> dict[str, str]:
    """Return provenance for the run manifest.

    Recorded so a journal reader can tell which browser build produced the
    screenshots. A run whose Chromium revision is unknown cannot be reproduced,
    and a stale build is one of the ways a demo can quietly stop reflecting the
    current UI.
    """
    root = browsers_path()
    builds = _chromium_build_dirs(root)
    info = {
        "browsers_path": str(root),
        "chromium_builds": ", ".join(b.name for b in builds) or "(none)",
    }

    if playwright_available():
        # Imported lazily and only for its version string: this function is safe
        # to call for diagnostics even when the caller never launches a browser.
        from importlib.metadata import PackageNotFoundError, version
        try:
            info["playwright_version"] = version("playwright")
        except PackageNotFoundError:      # pragma: no cover - defensive
            info["playwright_version"] = "(unknown)"
    else:
        info["playwright_version"] = "(not installed)"
    return info
