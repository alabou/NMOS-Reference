# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Browser-driven scenario tests.

Excluded from the default gate by the project's existing ``addopts``, which
deselects the ``e2e`` marker. They need three things the default gate does not:
the optional playwright extra, a downloaded Chromium, and a node already running
with a Controller UI.

Run them with::

    export NMOS_CONTROLLER_ADMIN_PASSWORD=admin
    export PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright"
    python -m pytest -m e2e nmos/agentui/tests/e2e/
"""

from __future__ import annotations
