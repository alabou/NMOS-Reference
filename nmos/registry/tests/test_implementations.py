# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The process rigs' choice of registry, and the one log line they read.

In the default gate, because what these guard breaks only in the e2e suites:
a binary lookup that silently passes without a binary, or a leader-line
pattern that stops matching a log the other implementation writes, would turn
the ``[rust]`` half of every process-level test into a no-op.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from nmos.registry.tests import _implementations
from nmos.registry.tests._implementations import (
    BUILD_HINT,
    ENVIRONMENT_VARIABLE,
    Implementation,
    find_rust_registry,
    launcher_flags,
    registry_command,
    registry_environment,
)
from nmos.registry.tests._processes import ProcessCluster


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


class TestFindingTheRustRegistry:
    """``registry-runtime.sh``'s order, and never a silent pass."""

    @pytest.fixture(autouse=True)
    def _nowhere(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv(ENVIRONMENT_VARIABLE, raising=False)
        monkeypatch.setattr(_implementations, "RUST_RELEASE", tmp_path / "release" / "nmos-registry")
        monkeypatch.setattr(_implementations, "RUST_DEBUG", tmp_path / "debug" / "nmos-registry")

    def test_the_variable_wins_when_it_names_an_executable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        named = _executable(tmp_path / "elsewhere")
        monkeypatch.setenv(ENVIRONMENT_VARIABLE, str(named))
        assert find_rust_registry() == named

    def test_a_variable_naming_nothing_executable_skips(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setenv(ENVIRONMENT_VARIABLE, str(tmp_path / "missing"))
        with pytest.raises(pytest.skip.Exception, match="is not executable"):
            find_rust_registry()

    def test_the_release_build_is_preferred(self, tmp_path: Path) -> None:
        release = _executable(tmp_path / "release" / "nmos-registry")
        _executable(tmp_path / "debug" / "nmos-registry")
        assert find_rust_registry() == release

    def test_the_debug_build_is_taken_with_a_warning(self, tmp_path: Path) -> None:
        debug = _executable(tmp_path / "debug" / "nmos-registry")
        with pytest.warns(RuntimeWarning, match="DEBUG build"):
            assert find_rust_registry() == debug

    def test_no_build_skips_with_the_build_hint(self) -> None:
        with pytest.raises(pytest.skip.Exception) as skipped:
            find_rust_registry()
        assert BUILD_HINT in str(skipped.value)
        assert ENVIRONMENT_VARIABLE in str(skipped.value)


class TestWhatAProcessIsGiven:
    def test_the_python_starts_through_the_interpreter(self) -> None:
        command = registry_command(Implementation.PYTHON)
        assert command[0] == sys.executable
        assert command[1].endswith("nmos_registry.py")
        assert launcher_flags(Implementation.PYTHON) == []

    def test_the_rust_starts_as_the_binary_alone(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        named = _executable(tmp_path / "nmos-registry")
        monkeypatch.setenv(ENVIRONMENT_VARIABLE, str(named))
        assert registry_command(Implementation.RUST) == [str(named)]
        assert launcher_flags(Implementation.RUST) == ["--rust"]

    def test_the_environment_carries_what_each_needs(self) -> None:
        base = {"PATH": "/usr/bin:/bin", "PYTHONPATH": "/stale"}
        python = registry_environment(Implementation.PYTHON, base)
        assert python["PATH"] == "/usr/bin:/bin"
        assert python["PYTHONPATH"].endswith("nmos-reference")
        assert "NO_COLOR" not in python
        rust = registry_environment(Implementation.RUST, base)
        assert rust["PATH"] == "/usr/bin:/bin"
        assert "PYTHONPATH" not in rust
        assert rust["NO_COLOR"] == "1"
        assert base == {"PATH": "/usr/bin:/bin", "PYTHONPATH": "/stale"}, "the base was changed"


class TestTheLeaderLine:
    """Both shapes, and the rotated files, read without a process."""

    def test_both_implementations_announcements_are_read(self, tmp_path: Path) -> None:
        cluster = ProcessCluster(tmp_path, [Implementation.PYTHON, Implementation.RUST])
        (tmp_path / "m0.log").write_text(
            "2026-10-01 03:12:45.123 INFO raft: nmos-registry-127.0.0.1-40001 is leader for term 2\n"
        )
        assert cluster.leader_index() == 0
        (tmp_path / "m1.log").write_text(
            '2026-10-01T03:12:46.123456Z  INFO raft: is leader member="nmos-registry-127.0.0.1-40003" term=3\n'
        )
        assert cluster.leader_index() == 1

    def test_a_rotated_file_still_counts(self, tmp_path: Path) -> None:
        cluster = ProcessCluster(tmp_path, [Implementation.RUST, Implementation.RUST])
        (tmp_path / "m0.log").write_text("nothing here\n")
        (tmp_path / "m0.log.1").write_text(
            '2026-10-01T03:12:46.123456Z  INFO raft: is leader member="m0" term=9\n'
        )
        (tmp_path / "m1.log").write_text(
            '2026-10-01T03:12:47.123456Z  INFO raft: is leader member="m1" term=4\n'
        )
        assert cluster.leader_index() == 0

    def test_no_announcement_is_no_leader(self, tmp_path: Path) -> None:
        cluster = ProcessCluster(tmp_path, [Implementation.PYTHON])
        assert cluster.leader_index() is None
        assert os.path.basename(cluster.tails().splitlines()[0]) == os.path.basename(str(tmp_path))
