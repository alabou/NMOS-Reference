# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The test fixtures' own contract.

``tai_version`` decides the ``version`` on nearly every resource the registry
suite registers, and IS-04 rejects a registration whose version is earlier than
the stored one. So a fixture that can go backwards makes a whole family of tests
fail for reasons that have nothing to do with what they are testing.

That is not hypothetical. While it read ``time.time()`` per call, a full-suite
run produced::

    version 1789680515:229146719 is earlier than the registered
    version 1789680516:396116257

in a test that had built two Nodes in a row -- an NTP correction under WSL2,
nothing more. These tests pin the properties that close it.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from nmos.registry.tests._fixtures import make_node, tai_version
from nmos.registry.types import TaiCursor


def _cursor(version: str) -> Any:
    parsed = TaiCursor.parse(version)
    assert parsed is not None, f"{version!r} is not a valid TAI version"
    return parsed


def test_successive_versions_never_go_backwards() -> None:
    """The property the registry actually depends on."""
    cursors = [_cursor(tai_version()) for _ in range(2000)]
    regressions = [
        (a, b) for a, b in zip(cursors, cursors[1:]) if b < a
    ]
    assert not regressions, f"{len(regressions)} version regressions"


def test_an_offset_still_means_seconds_relative_to_now() -> None:
    """Anchoring changed where "now" is measured from, not what an offset means.

    Twenty-one call sites pass an offset to say "older than" or "newer than",
    including negative ones, so this has to keep working.
    """
    base = _cursor(tai_version())
    assert _cursor(tai_version(-10)) < base
    assert _cursor(tai_version(+10)) > base


def test_a_backwards_wall_clock_does_not_produce_a_regression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact failure mode, reproduced.

    A 1.17-second backward jump is what was observed. Reading the wall clock
    per call turns that into a version regression; anchoring once and advancing
    on the monotonic clock does not.
    """
    real_time = time.time
    remaining = [real_time(), real_time() - 1.17]
    monkeypatch.setattr(
        time, "time", lambda: remaining.pop(0) if remaining else real_time(),
    )

    first = _cursor(tai_version())
    second = _cursor(tai_version())
    assert not (second < first), "a backward wall clock reached the version"


def test_two_nodes_built_in_a_row_can_both_be_registered() -> None:
    """The shape of the test that actually failed.

    Guards the guard: the three tests above check ``tai_version`` directly, but
    what broke was a builder calling it. If ``make_node`` ever stopped routing
    through it, those would still pass while this would not.
    """
    first = make_node()["version"]
    second = make_node()["version"]
    assert not (_cursor(second) < _cursor(first))
