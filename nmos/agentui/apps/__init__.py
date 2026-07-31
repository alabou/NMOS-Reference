# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Application adapters.

One adapter per driveable application. Each contributes a
:class:`~nmos.agentui.core.adapter.AppAdapter` implementation, a selector module,
and a session facade exposing that application's verbs — and nothing else. The
core's powers are fixed; an adapter describes an application in terms the core
already permits rather than extending them.
"""

from __future__ import annotations
