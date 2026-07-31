# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Adapter for the embedded NMOS Controller UI.

The only application-specific code in the driver. Its parts:

``discovery``
    Finds a running node and works out how to reach its Controller UI.
``pages``
    The single home for every CSS selector. Nothing outside this module knows
    what the markup looks like, so a UI change has one place to be reflected.
``adapter``
    Implements :class:`~nmos.agentui.core.adapter.AppAdapter`.
``session``
    The operator-level verbs a scenario calls.
``trace_join``
    Joins journal steps to the node's own debug trace on shared trace ids.
"""

from __future__ import annotations
