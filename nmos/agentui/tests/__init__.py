# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for the agent-driven UI driver.

Everything directly under this package runs in the default gate and needs
neither playwright nor a running node. The browser-dependent scenarios live in
``tests/e2e/`` and are marked ``e2e``, which the project's existing ``addopts``
already excludes.
"""

from __future__ import annotations
