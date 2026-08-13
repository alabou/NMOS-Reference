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

import pytest

from nmos.etcd.tests.etcd_server import etcd_endpoint  # noqa: F401


@pytest.fixture
def namespace() -> str:
    """A fresh etcd key namespace per test.

    The etcd server fixture is session-scoped, so without this every
    distributed test would see every other test's resources. Isolating by
    namespace rather than by wiping the keyspace also keeps the tests
    parallelisable and avoids one test's cleanup racing another's setup.
    """
    return f"/nmos-test/registry/v1/{uuid.uuid4().hex[:8]}"
