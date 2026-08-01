# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Reading a running process's command line, and parsing it back into options.

The driver attaches to an application someone else started. Rather than being
told where it is, it reads the answer from the process itself — which means a run
provably targets the instance the operator launched, in the configuration they
chose, with no second source of truth to drift.

One thing this module is careful about: a command line frequently contains
secrets. ``/proc/<pid>/cmdline`` is world-readable and the same text is visible in
``ps``, so reading it is not a fresh disclosure — but *copying* it into a run
artifact that gets shared would be. :meth:`CommandLine.redacted` exists so
provenance can be recorded without carrying credentials into a file, and the
caller names which options are sensitive rather than this module guessing.
"""

from __future__ import annotations

from collections.abc import Iterator
import ctypes
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys

#: Placeholder written in place of a sensitive option's value.
REDACTED = "***"

_DEFAULT_PROC_ROOT = Path("/proc")


@dataclass(frozen=True, slots=True)
class ProcessInfo:
    """A process and the argument vector it was started with."""

    pid: int
    argv: tuple[str, ...]

    @property
    def command_line(self) -> str:
        """The argv joined for display. May contain secrets — prefer redaction."""
        return " ".join(self.argv)


def iter_processes(proc_root: Path = Path("/proc")) -> Iterator[ProcessInfo]:
    """Yield every process whose command line can be read.

    Processes that vanish mid-scan or belong to another user are skipped rather
    than raising: a scan is a snapshot of something inherently in motion, and one
    unreadable entry says nothing about the rest.
    """
    if sys.platform == "win32" and proc_root == _DEFAULT_PROC_ROOT:
        yield from _iter_windows_processes()
        return

    if not proc_root.is_dir():
        return

    for entry in sorted(proc_root.iterdir()):
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except (OSError, PermissionError):
            # Exited between listing and reading, or owned by another user.
            continue
        if not raw:
            # Kernel threads have an empty cmdline.
            continue
        argv = tuple(part for part in raw.split(b"\0") if part)
        yield ProcessInfo(
            pid=int(entry.name),
            argv=tuple(part.decode("utf-8", "replace") for part in argv),
        )


def _split_windows_command_line(command_line: str) -> tuple[str, ...]:
    """Parse a Win32 command line using the same rules as a native process."""
    argc = ctypes.c_int()
    command_line_to_argv = ctypes.windll.shell32.CommandLineToArgvW
    command_line_to_argv.argtypes = (
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_int),
    )
    command_line_to_argv.restype = ctypes.POINTER(ctypes.c_wchar_p)
    pointer = command_line_to_argv(command_line, ctypes.byref(argc))
    if not pointer:
        return ()
    try:
        return tuple(pointer[index] for index in range(argc.value))
    finally:
        ctypes.windll.kernel32.LocalFree(pointer)


def _iter_windows_processes() -> Iterator[ProcessInfo]:
    """Yield Windows processes from CIM, collapsing the venv launcher pair.

    A Windows virtual-environment Python launcher starts the base interpreter as
    a child and waits for it. Both processes expose the same command line, but
    only the base interpreter owns the sockets. Prefer that process so the
    recorded PID identifies the actual NMOS server.
    """
    powershell = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    )
    script = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine } | "
        "Select-Object ProcessId,ExecutablePath,CommandLine | "
        "ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            [
                str(powershell),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return
    if result.returncode != 0 or not result.stdout.strip():
        return
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return

    rows = payload if isinstance(payload, list) else [payload]
    unique: dict[tuple[str, ...], tuple[ProcessInfo, bool]] = {}
    for row in rows:
        command_line = row.get("CommandLine") or ""
        argv = _split_windows_command_line(command_line)
        if not argv:
            continue
        executable = row.get("ExecutablePath") or ""
        executable_path = Path(executable) if executable else Path(argv[0])
        is_launcher = (
            executable_path.parent.name.casefold() == "scripts"
            and (executable_path.parent.parent / "pyvenv.cfg").is_file()
        )
        process = ProcessInfo(pid=int(row["ProcessId"]), argv=argv)
        # The venv launcher and base interpreter differ only in argv[0].
        # Key Python script invocations from the script path onward.
        key = argv[1:] if len(argv) > 1 and argv[1].endswith(".py") else argv
        previous = unique.get(key)
        if previous is None or (previous[1] and not is_launcher):
            unique[key] = (process, is_launcher)

    for process, _is_launcher in unique.values():
        yield process


def find_by_script(
    script_name: str,
    *,
    proc_root: Path = Path("/proc"),
    exclude_pid: int | None = None,
) -> tuple[ProcessInfo, ...]:
    """Find processes running a given Python script.

    Matched on the basename of any argv element so that both ``python3
    nmos_node.py`` and ``python3 /abs/path/nmos_node.py`` are found.

    ``exclude_pid`` lets a caller leave itself out — otherwise a driver whose own
    command line happens to mention the script name would find itself.
    """
    found: list[ProcessInfo] = []
    for process in iter_processes(proc_root):
        if exclude_pid is not None and process.pid == exclude_pid:
            continue
        for element in process.argv:
            if Path(element).name == script_name:
                found.append(process)
                break
    return tuple(found)


class CommandLine:
    """An argv parsed into the option shapes argparse produces.

    Deliberately not a general argparse reimplementation: it understands exactly
    the three forms the target application uses — a flag with a following value, a
    flag with ``=value`` attached, and a bare boolean flag — because guessing at
    anything more would risk misreading a configuration and silently attaching to
    the wrong endpoint.
    """

    def __init__(self, argv: tuple[str, ...]) -> None:
        self.argv = argv

    def _pairs(self) -> Iterator[tuple[str, str | None]]:
        """Yield ``(option, value)``; value is ``None`` for a bare flag."""
        index = 0
        while index < len(self.argv):
            token = self.argv[index]
            if not token.startswith("--"):
                index += 1
                continue
            if "=" in token:
                name, _, value = token.partition("=")
                yield name, value
                index += 1
                continue
            following = self.argv[index + 1] if index + 1 < len(self.argv) else None
            if following is not None and not following.startswith("--"):
                yield token, following
                index += 2
            else:
                yield token, None
                index += 1

    def has_flag(self, name: str) -> bool:
        """Whether a boolean option is present."""
        return any(option == name for option, _ in self._pairs())

    def value(self, name: str, default: str | None = None) -> str | None:
        """The last value given for an option, or ``default``.

        Last rather than first, because that is what argparse does for a
        non-appending option: a later occurrence overwrites an earlier one.
        """
        result = default
        for option, value in self._pairs():
            if option == name and value is not None:
                result = value
        return result

    def values(self, name: str) -> tuple[str, ...]:
        """Every value given for a repeatable (``action="append"``) option."""
        return tuple(
            value for option, value in self._pairs()
            if option == name and value is not None
        )

    def int_value(self, name: str, default: int) -> int:
        """An integer option's value, or ``default`` if absent or unparseable."""
        raw = self.value(name)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    def redacted(self, sensitive: frozenset[str]) -> str:
        """The command line with the named options' values replaced.

        Used for the run manifest. The caller names the sensitive options because
        only the caller knows which of its own flags carry secrets — a hardcoded
        list here would silently fail to cover a newly added one.
        """
        parts: list[str] = []
        index = 0
        while index < len(self.argv):
            token = self.argv[index]
            if "=" in token and token.startswith("--"):
                name, _, _value = token.partition("=")
                parts.append(f"{name}={REDACTED}" if name in sensitive else token)
                index += 1
                continue
            if token in sensitive:
                parts.append(token)
                if index + 1 < len(self.argv) and not self.argv[index + 1].startswith("--"):
                    parts.append(REDACTED)
                    index += 2
                else:
                    index += 1
                continue
            parts.append(token)
            index += 1
        return " ".join(parts)
