# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The playwright quarantine.

This is the **only** package in the repository permitted to import playwright,
and ``tests/test_no_playwright_leak.py`` enforces that by parsing every module
under ``nmos/`` rather than by trusting the convention.

Two reasons the boundary is drawn here:

*Optionality.* The node runtime imports ``nmos.controller``. Keeping the browser
dependency inside a leaf package that nothing else imports means a node with no
optional extra installed still starts.

*Type safety.* Playwright objects arriving through an ``ignore_missing_imports``
override are typed ``Any`` when the package is absent. Confining them here — and
returning only project types across the boundary — keeps ``mypy --strict`` honest
in the rest of the codebase instead of quietly checking nothing.

Nothing here escapes upward except a :class:`~nmos.agentui.core.surface.Surface`.
"""

from __future__ import annotations
