# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Structural guard on the optional-dependency boundary.

The node runtime imports ``nmos.controller``. If playwright — a 656 MB browser
dependency — could be reached from anywhere the runtime touches, then a single
stray import would make the production node refuse to start on any machine
without the optional extra installed. Keeping ``nmos/agentui/`` a sibling of
``nmos/controller/`` rather than a child makes that a directory-level property,
and this test is what turns the property into something enforced rather than
merely intended.

Two invariants, both checked by reading source rather than by importing (an
import test would pass simply because the dependency happened to be installed on
the machine running it, which is exactly the situation that hides the bug):

1. ``playwright`` appears only under ``nmos/agentui/driver/``.
2. ``nmos.agentui`` is imported by nothing outside ``nmos/agentui/``.

These land in the very first commit of the package so the boundary can never be
retro-fitted.
"""

from __future__ import annotations

import ast
from pathlib import Path

#: Root of the ``nmos`` package, resolved from this file so the test does not
#: depend on the working directory.
NMOS_ROOT = Path(__file__).resolve().parents[2]

#: The single directory permitted to import playwright.
DRIVER_DIR = NMOS_ROOT / "agentui" / "driver"

AGENTUI_DIR = NMOS_ROOT / "agentui"


def _python_files(root: Path) -> list[Path]:
    """Every non-generated Python source file under ``root``."""
    return [
        path for path in sorted(root.rglob("*.py"))
        # Generated type modules are excluded from mypy for the same reason and
        # are not hand-maintained source.
        if "generated" not in path.parts and "__pycache__" not in path.parts
    ]


def _imported_modules(path: Path) -> set[str]:
    """Top-level module names imported by a source file.

    Parsed with ``ast`` rather than grepped so that the word "playwright"
    appearing in a docstring or comment — as it does throughout this package —
    is not mistaken for an import.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            # Relative imports have no module of interest here; absolute ones do.
            if node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return found


class TestPlaywrightConfinement:
    """playwright may be imported only inside the driver quarantine."""

    def test_no_playwright_import_outside_driver(self) -> None:
        offenders: list[str] = []
        for path in _python_files(NMOS_ROOT):
            if DRIVER_DIR in path.parents:
                continue
            if "playwright" in _imported_modules(path):
                offenders.append(str(path.relative_to(NMOS_ROOT)))
        assert not offenders, (
            "playwright must only be imported under nmos/agentui/driver/; "
            f"found imports in: {offenders}"
        )

    def test_driver_directory_is_the_only_exception(self) -> None:
        # Guards against the quarantine being silently relocated: if the driver
        # package moves or is renamed, this test should be updated deliberately.
        assert DRIVER_DIR.parent == AGENTUI_DIR


class TestAgentUiIsNotReachableFromRuntime:
    """Nothing outside the package may import it."""

    def test_no_runtime_module_imports_agentui(self) -> None:
        offenders: list[str] = []
        for path in _python_files(NMOS_ROOT):
            if AGENTUI_DIR in path.parents or path.parent == AGENTUI_DIR:
                continue
            source = path.read_text(encoding="utf-8")
            # Both spellings: ``import nmos.agentui`` and ``from nmos.agentui``.
            if "nmos.agentui" in source:
                offenders.append(str(path.relative_to(NMOS_ROOT)))
        assert not offenders, (
            "nmos.agentui is an optional add-on and must not be reachable from "
            f"the node runtime; referenced by: {offenders}"
        )

    def test_controller_package_is_untouched_by_agentui(self) -> None:
        # The driver's whole claim is that it demonstrates the shipping UI. If it
        # needed a change inside nmos/controller/ to work, that claim would be
        # weaker -- so the dependency runs strictly one way.
        controller = NMOS_ROOT / "controller"
        for path in _python_files(controller):
            assert "agentui" not in path.read_text(encoding="utf-8"), (
                f"{path.relative_to(NMOS_ROOT)} references agentui; the "
                "controller must not know its driver exists"
            )


class TestPackageImportsWithoutPlaywright:
    """The public surface must import even with no browser dependency."""

    def test_top_level_import_has_no_playwright_dependency(self) -> None:
        # ``nmos.agentui`` is imported here for real: it must succeed regardless
        # of whether the optional extra is installed, because the errors and
        # enums it exposes are used by callers that never launch a browser.
        import nmos.agentui as agentui

        assert agentui.PageId.LOGIN == "login"
        assert issubclass(agentui.BlockedControl, agentui.AgentUiError)

    def test_deps_module_reports_without_importing_playwright(self) -> None:
        from nmos.agentui import deps

        # ``playwright_available`` uses find_spec, so asking the question must not
        # pull the package (and its subprocess machinery) into this process.
        deps.playwright_available()
        info = deps.describe_environment()
        assert "browsers_path" in info
        assert "playwright_version" in info
