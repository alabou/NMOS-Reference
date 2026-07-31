# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Application-agnostic core of the agent-driven UI driver.

Nothing in this package imports playwright, and nothing in it mentions NMOS.
That separation is what lets the fidelity rules, the wait semantics, the
affordance classification, and the journal format be unit-tested with no browser
and no node running — and it is what a second application adapter would reuse
unchanged.
"""

from __future__ import annotations
