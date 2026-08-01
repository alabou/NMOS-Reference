# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for the agent-UI optional dependency gate."""

from pathlib import Path

from nmos.agentui import deps


def test_windows_default_cache_uses_local_app_data(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(deps.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert deps._playwright_default_cache() == tmp_path / "ms-playwright"


def test_windows_chromium_executable_is_a_complete_build(tmp_path: Path) -> None:
    build = tmp_path / "chromium-1234"
    executable = build / "chrome-win64" / "chrome.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()

    assert deps._chromium_build_dirs(tmp_path) == [build]
