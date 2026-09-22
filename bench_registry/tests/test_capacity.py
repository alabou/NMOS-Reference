# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Process CPU accounting for the capacity benchmark, on both platforms.

The number this feeds is the benchmark's cost-per-request column, and a cost
column that is quietly wrong is worse than one that is absent -- so the two
implementations are pinned separately.

Linux is the deployment target and the platform whose numbers get published;
its ``/proc/<pid>/stat`` arithmetic is the one that has to be right, and the
field offsets in it are the kind of thing that survives a rewrite by looking
plausible. Windows has no ``/proc`` and no ``os.sysconf`` at all, so it reads
the same two counters through psutil; that path is here to pin that it stays a
Windows-only branch, because psutil is a dev extra and nothing Ubuntu deploys
may come to depend on it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

# psutil is a dev extra, and this is the only module in ``testpaths`` that
# needs it. Imported plainly it is not a skipped test but a *collection*
# error, and pytest aborts the whole session on one of those: a checkout
# installed from ``requirements.txt`` rather than ``.[dev]`` -- which is the
# second setup the README documents -- collected nothing at all and reported
# zero tests run, on the platform that deploys. Skipping is the honest
# outcome, because what these five pin is the Windows branch, and a machine
# without psutil is a machine that cannot take it anyway.
psutil = pytest.importorskip(
    "psutil", reason="psutil is a dev extra (pip install -e .[dev])",
)

from bench_registry import capacity  # noqa: E402


class TestServerCpuSeconds:
    @pytest.mark.skipif(sys.platform != "win32", reason="Win32 only")
    def test_windows_measures_the_current_process(self) -> None:
        """Through the real psutil call, so the stubbing below cannot be the
        only thing that ever agrees with itself."""
        assert capacity._server_cpu_seconds(os.getpid()) > 0.0

    def test_windows_sums_user_and_system_time(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Both halves, and for the pid asked about rather than for self."""
        observed: list[int] = []

        class Process:
            def __init__(self, pid: int) -> None:
                observed.append(pid)

            def cpu_times(self) -> Any:
                return SimpleNamespace(user=1.25, system=0.75)

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(psutil, "Process", Process)

        assert capacity._server_cpu_seconds(1234) == pytest.approx(2.0)
        assert observed == [1234]

    def test_windows_returns_zero_if_the_process_disappears(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A server that exited mid-run must drop the cost column, not
        fabricate a zero-cost request."""
        def missing(pid: int) -> Any:
            raise psutil.NoSuchProcess(pid)

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(psutil, "Process", missing)

        assert capacity._server_cpu_seconds(1234) == 0.0

    def test_linux_keeps_the_proc_calculation(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """utime and stime are fields 14 and 15 of ``proc(5)``, counted from
        after the comm parenthesis -- which a command name containing a space
        or a bracket is exactly why they cannot be counted from the left.

        200 + 50 jiffies at 100 Hz is 2.5 seconds.
        """
        fields = ["0"] * 13
        fields[11] = "200"
        fields[12] = "50"
        contents = f"123 (registry server) {' '.join(fields)}"

        def read_text(_path: Path, **_kwargs: Any) -> str:
            return contents

        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(Path, "read_text", read_text)
        monkeypatch.setattr(os, "sysconf", lambda _name: 100, raising=False)
        monkeypatch.setattr(
            psutil,
            "Process",
            lambda _pid: pytest.fail("the Linux path reached for psutil"),
        )

        assert capacity._server_cpu_seconds(1234) == pytest.approx(2.5)

    def test_linux_returns_zero_when_proc_is_unreadable(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Same contract as the Windows branch: absent, never fabricated."""
        def unreadable(_path: Path, **_kwargs: Any) -> str:
            raise FileNotFoundError

        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(Path, "read_text", unreadable)

        assert capacity._server_cpu_seconds(1234) == 0.0
