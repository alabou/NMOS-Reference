# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Fixtures for the registry tests.

The etcd server fixture is shared with ``nmos/etcd/tests`` rather than
duplicated: one definition means one place where "how do we start a test etcd"
is decided, and the distributed-backend tests need exactly the same server the
client tests do. It skips when no etcd binary is installed, so the default gate
still runs in a checkout without the optional extra.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from nmos.etcd.tests.etcd_server import etcd_endpoint  # noqa: F401


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Iterator[None]:
    """Attach the members' log tails to a failed process-level test's report.

    A process rig (``_processes.ProcessCluster``) prints what its members said
    only when it *skips*; a failure printed nothing, and pytest keeps a
    session's temporary directories for three sessions -- so the one failure
    of the paced rolling restart in 72 runs lost its logs before anyone read
    them. A fixture cannot see the outcome, and anything printed at teardown
    lands in the teardown report, which a call-phase failure does not show. So
    the capture lives in the one hook that owns the call report, and any
    fixture value with a ``tails()`` opts in.
    """
    outcome: Any = yield
    report = outcome.get_result()
    if report.when != "call" or not report.failed:
        return
    for value in getattr(item, "funcargs", {}).values():
        tails = getattr(value, "tails", None)
        if not callable(tails):
            continue
        try:
            report.sections.append(("member logs", str(tails())))
        except Exception as exc:  # the report must outlive its own diagnostics
            report.sections.append(("member logs", f"unavailable: {exc!r}"))


@pytest.fixture
def namespace() -> str:
    """A fresh etcd key namespace per test.

    The etcd server fixture is session-scoped, so without this every
    distributed test would see every other test's resources. Isolating by
    namespace rather than by wiping the keyspace also keeps the tests
    parallelisable and avoids one test's cleanup racing another's setup.
    """
    return f"/nmos-test/registry/v1/{uuid.uuid4().hex[:8]}"
