# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Fixtures for the browser-driven scenario tests.

Everything in this directory is marked ``e2e``, which the project's existing
``addopts`` already deselects — so no configuration change is needed to keep the
default gate fast and dependency-free.

Each prerequisite is checked separately and skipped with a message naming what to
do about it. A single "e2e unavailable" skip would leave the reader guessing which
of three quite different things was missing.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from ...apps.nmos_controller import discovery
from ...deps import INSTALL_BROWSER, browsers_path, playwright_available
from ...errors import AgentUiError

#: This directory, used to scope the marking hook below.
_HERE = Path(__file__).resolve().parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark tests **in this directory** as ``e2e``.

    The path check is essential, not defensive. ``pytest_collection_modifyitems``
    in any ``conftest.py`` is handed the *entire session's* item list, not just the
    items beneath that conftest — so an unscoped loop here marks every test in the
    project as ``e2e``, and the project's ``addopts`` then deselects all of them.
    That is exactly what happened when this hook was first written: the full gate
    reported "2859 deselected" and ran nothing at all.

    A module-level ``pytestmark`` cannot do this job either: in a ``conftest.py``
    it does not propagate to tests. Hence the hook, scoped by path, plus a
    ``pytestmark`` in each test module.
    """
    for item in items:
        try:
            path = Path(str(item.fspath)).resolve()
        except (OSError, ValueError):           # pragma: no cover - defensive
            continue
        if path.is_relative_to(_HERE):
            item.add_marker(pytest.mark.e2e)


@pytest.fixture(scope="session")
def playwright_installed() -> None:
    """Skip unless the optional extra and a browser build are both present."""
    if not playwright_available():
        pytest.skip("playwright is not installed (pip install -r "
                    "requirements-agentui.txt)")
    root = browsers_path()
    if not any(root.glob("chromium*")):
        pytest.skip(f"no Chromium build under {root} ({INSTALL_BROWSER})")


@pytest.fixture(scope="session")
def admin_password() -> str:
    """Skip unless the admin password is available in the environment."""
    value = os.environ.get(discovery.PASSWORD_ENV, "")
    if not value:
        pytest.skip(f"{discovery.PASSWORD_ENV} is not set")
    return value


@pytest.fixture(scope="session")
def running_node(playwright_installed: None, admin_password: str) -> object:
    """Skip unless a node is serving a Controller UI, and report which one."""
    try:
        return discovery.discover()
    except AgentUiError as exc:
        pytest.skip(f"no attachable node: {exc.msg}")


@pytest.fixture
def artifacts_root(tmp_path: Path) -> Iterator[Path]:
    """Write each test's journal into its own temporary directory.

    Keeps test runs out of the shared ``artifacts/`` tree so a test can assert on
    exactly the journal it produced.
    """
    root = tmp_path / "agentui"
    root.mkdir()
    yield root
