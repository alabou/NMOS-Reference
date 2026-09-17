# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Shared guards for the raft tests.

The leak assertion below exists because of a real failure, not a hypothetical
one. An earlier version of ``node.py`` spawned a fire-and-forget task on every
commit-index advance and never awaited them at shutdown. The raft tests all
passed; what broke was a *timing-sensitive test in another package*, which
started failing only when the raft suite ran before it, and passed in
isolation. Attribution took a run of the whole gate with ``--ignore=nmos/raft``
to establish.

An unowned task is not a tidiness problem. It outlives the test that created
it, perturbs whatever runs next, and points at the wrong place when it does.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest


@pytest.fixture(autouse=True)
async def no_leaked_tasks() -> AsyncIterator[None]:
    """Fail a test that leaves tasks running on the loop.

    Compares against the tasks that existed before the test, so pytest-asyncio's
    own machinery is not mistaken for a leak.
    """
    before = {t for t in asyncio.all_tasks() if not t.done()}
    yield

    # One turn for cancellations already requested to actually land.
    await asyncio.sleep(0)
    leaked = {
        task for task in asyncio.all_tasks()
        if not task.done() and task not in before
        and task is not asyncio.current_task()
    }
    if leaked:
        names = ", ".join(sorted(task.get_name() for task in leaked))
        for task in leaked:
            task.cancel()
        pytest.fail(
            f"test leaked {len(leaked)} running task(s): {names}. An unowned "
            f"task outlives this test and perturbs whatever runs next.",
        )
