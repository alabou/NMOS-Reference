# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for command-line parsing and node discovery.

A synthetic ``/proc`` tree is used throughout, so these run in the default gate
with no node running and no dependence on what happens to be on the machine.

The redaction test is the one to keep: provenance is copied verbatim into a
shareable artifact, and the node's command line contains its admin password.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from ..apps.nmos_controller import discovery
from ..core import proc_scan
from ..core.proc_scan import (
    CommandLine,
    ProcessInfo,
    find_by_script,
    iter_processes,
)
from ..enums import TlsPolicy
from ..errors import (
    AdminPasswordMissing,
    ControllerNotEnabled,
    NodeAmbiguous,
    NodeNotFound,
    OAuth2NotSupported,
)

#: The command line of the node this work was developed against, verbatim.
BARE_NODE_ARGV = (
    "python3.12", "nmos_node.py",
    "--nodeSerialNumber", "SNX00001",
    "--nodeAddr", "127.0.0.1",
    "--nodePort", "7051",
    "--nodeControlPort", "5050",
    "--nodeDisableTLS",
    "--controllerAdminPassword", "admin",
    "--rdsHost", "127.0.0.1",
    "--rdsRegistrationPort", "8444",
    "--rdsQueryPort", "8443",
    "--rdsDisableTLS",
    "--debug-in-depth",
    "--nodeConfig", "config10_nousb",
    "--ipmx",
)


def _proc(tmp_path: Path, entries: dict[int, tuple[str, ...]]) -> Path:
    """Build a synthetic /proc tree.

    The synthetic noise entries use a pid chosen to be outside ``entries``: an
    earlier version hardcoded pid 2 for the kernel thread and silently
    overwrote a test's own pid-2 node with an empty cmdline, which made
    multi-node fixtures look like single-node ones.
    """
    root = tmp_path / "proc"
    root.mkdir()
    for pid, argv in entries.items():
        directory = root / str(pid)
        directory.mkdir()
        (directory / "cmdline").write_bytes(
            b"\0".join(part.encode() for part in argv) + b"\0")

    # A kernel thread: empty cmdline, must be skipped rather than crash the scan.
    kthread_pid = max(entries, default=1) + 1000
    kthread = root / str(kthread_pid)
    kthread.mkdir()
    (kthread / "cmdline").write_bytes(b"")
    # A non-numeric entry, which /proc really does contain.
    (root / "self").mkdir()
    return root


class TestProcScan:
    """Reading /proc."""

    def test_finds_node_by_basename(self, tmp_path: Path) -> None:
        root = _proc(tmp_path, {4242: BARE_NODE_ARGV})
        found = find_by_script("nmos_node.py", proc_root=root)
        assert len(found) == 1
        assert found[0].pid == 4242

    def test_matches_absolute_script_path(self, tmp_path: Path) -> None:
        root = _proc(tmp_path, {7: ("python3", "/opt/app/nmos_node.py", "--x")})
        assert len(find_by_script("nmos_node.py", proc_root=root)) == 1

    def test_skips_empty_and_non_numeric_entries(self, tmp_path: Path) -> None:
        root = _proc(tmp_path, {10: BARE_NODE_ARGV})
        pids = {p.pid for p in iter_processes(root)}
        assert pids == {10}

    def test_missing_proc_root_yields_nothing(self, tmp_path: Path) -> None:
        assert list(iter_processes(tmp_path / "absent")) == []

    def test_exclude_pid(self, tmp_path: Path) -> None:
        root = _proc(tmp_path, {5: BARE_NODE_ARGV})
        assert find_by_script("nmos_node.py", proc_root=root, exclude_pid=5) == ()

    def test_windows_default_uses_native_process_scan(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        expected = ProcessInfo(pid=42, argv=BARE_NODE_ARGV)
        monkeypatch.setattr(proc_scan.sys, "platform", "win32")
        monkeypatch.setattr(
            proc_scan,
            "_iter_windows_processes",
            lambda: iter((expected,)),
        )

        assert list(iter_processes()) == [expected]


#: A Windows venv invocation. The base interpreter child reports the same script
#: and arguments but replaces the venv launcher at ``argv[0]``.
WIN_NODE_ARGV = (
    r"C:\repo\.venv\Scripts\python.exe", "nmos_node.py",
    "--nodeControlPort", "5050",
)
BASE_NODE_ARGV = (
    r"C:\Python312\python.exe", *WIN_NODE_ARGV[1:],
)


def _scanned(
    pid: int,
    *,
    parent_pid: int | None = None,
    argv: tuple[str, ...] = WIN_NODE_ARGV,
    launcher: bool = False,
) -> proc_scan._ScannedProcess:
    return proc_scan._ScannedProcess(
        process=ProcessInfo(pid=pid, argv=argv),
        parent_pid=parent_pid,
        is_venv_launcher=launcher,
    )


class TestCollapseVenvLaunchers:
    """Telling a venv launcher/base pair apart from two separate nodes.

    The distinction matters beyond tidiness: ``discover`` refuses to choose
    between candidates, so merging two real nodes would not surface as an
    ambiguity — it would silently attach to one of them.
    """

    def test_launcher_is_superseded_by_its_own_base_interpreter(self) -> None:
        scanned = [
            _scanned(100, launcher=True),
            _scanned(101, parent_pid=100, argv=BASE_NODE_ARGV),
        ]
        assert [p.pid for p in proc_scan._collapse_venv_launchers(scanned)] == [101]

    def test_identical_command_lines_from_separate_rigs_both_survive(self) -> None:
        # The regression this guards: keying the collapse on argv alone erased
        # one of these, and the ambiguity error never fired.
        scanned = [
            _scanned(100, launcher=True),
            _scanned(101, parent_pid=100, argv=BASE_NODE_ARGV),
            _scanned(200, launcher=True),
            _scanned(201, parent_pid=200, argv=BASE_NODE_ARGV),
        ]
        assert [p.pid for p in proc_scan._collapse_venv_launchers(scanned)] == [
            101, 201,
        ]

    def test_launcher_without_its_child_in_the_snapshot_is_kept(self) -> None:
        # Dropping it would lose the only evidence the node is running.
        scanned = [_scanned(100, launcher=True)]
        assert [p.pid for p in proc_scan._collapse_venv_launchers(scanned)] == [100]

    def test_child_running_a_different_command_does_not_collapse_it(self) -> None:
        scanned = [
            _scanned(100, launcher=True),
            _scanned(101, parent_pid=100,
                     argv=(r"C:\repo\.venv\Scripts\python.exe", "-m", "pip")),
        ]
        assert [p.pid for p in proc_scan._collapse_venv_launchers(scanned)] == [
            100, 101,
        ]

    def test_absent_parent_data_never_collapses(self) -> None:
        scanned = [
            _scanned(100, launcher=True),
            _scanned(101, parent_pid=None),
        ]
        assert [p.pid for p in proc_scan._collapse_venv_launchers(scanned)] == [
            100, 101,
        ]

    def test_a_non_launcher_parent_is_never_dropped(self) -> None:
        # A plain interpreter that spawned an identical child is not a launcher
        # pair; both are real processes.
        scanned = [
            _scanned(100),
            _scanned(101, parent_pid=100),
        ]
        assert [p.pid for p in proc_scan._collapse_venv_launchers(scanned)] == [
            100, 101,
        ]

    def test_output_is_ordered_by_pid(self) -> None:
        scanned = [_scanned(300), _scanned(100), _scanned(200)]
        assert [p.pid for p in proc_scan._collapse_venv_launchers(scanned)] == [
            100, 200, 300,
        ]


class TestCommandLine:
    """Parsing the three option shapes argparse produces."""

    def test_value_and_flag(self) -> None:
        cli = CommandLine(BARE_NODE_ARGV)
        assert cli.value("--nodeAddr") == "127.0.0.1"
        assert cli.int_value("--nodeControlPort", 0) == 5050
        assert cli.has_flag("--nodeDisableTLS")
        assert cli.has_flag("--debug-in-depth")
        assert not cli.has_flag("--oauth2")

    def test_equals_form(self) -> None:
        cli = CommandLine(("nmos_node.py", "--nodeControlPort=5051"))
        assert cli.int_value("--nodeControlPort", 0) == 5051

    def test_repeatable_option_collects_all(self) -> None:
        # --nodeTrustedRootCA is action="append".
        cli = CommandLine((
            "nmos_node.py",
            "--nodeTrustedRootCA", "/a.pem",
            "--nodeTrustedRootCA", "/b.pem",
        ))
        assert cli.values("--nodeTrustedRootCA") == ("/a.pem", "/b.pem")

    def test_last_value_wins_for_scalar(self) -> None:
        # Matches argparse for a non-appending option.
        cli = CommandLine(("x", "--nodeAddr", "a", "--nodeAddr", "b"))
        assert cli.value("--nodeAddr") == "b"

    def test_missing_returns_default(self) -> None:
        cli = CommandLine(("nmos_node.py",))
        assert cli.value("--nodeAddr", "127.0.0.1") == "127.0.0.1"
        assert cli.int_value("--nodeControlPort", 0) == 0

    def test_unparseable_int_falls_back(self) -> None:
        cli = CommandLine(("x", "--nodeControlPort", "not-a-number"))
        assert cli.int_value("--nodeControlPort", 0) == 0

    def test_bare_flag_before_another_flag(self) -> None:
        cli = CommandLine(("x", "--nodeDisableTLS", "--ipmx"))
        assert cli.has_flag("--nodeDisableTLS")
        assert cli.has_flag("--ipmx")
        assert cli.value("--nodeDisableTLS") is None


class TestRedaction:
    """Secrets must not reach an artifact file."""

    def test_admin_password_is_redacted(self) -> None:
        # The journal and manifest are meant to be shareable. /proc being
        # world-readable makes reading the password harmless; copying it into a
        # file that gets passed around does not.
        redacted = CommandLine(BARE_NODE_ARGV).redacted(discovery.SENSITIVE_OPTIONS)
        assert "admin" not in redacted.split()
        assert "--controllerAdminPassword ***" in redacted
        # Everything non-sensitive survives, so provenance stays useful.
        assert "--nodeConfig config10_nousb" in redacted
        assert "--nodeControlPort 5050" in redacted

    def test_equals_form_is_redacted(self) -> None:
        cli = CommandLine(("x", "--controllerAdminPassword=s3cret"))
        out = cli.redacted(discovery.SENSITIVE_OPTIONS)
        assert "s3cret" not in out
        assert "--controllerAdminPassword=***" in out

    def test_oauth2_client_secret_is_redacted(self) -> None:
        cli = CommandLine(("x", "--oauth2ClientSecret", "hunter2"))
        assert "hunter2" not in cli.redacted(discovery.SENSITIVE_OPTIONS)

    def test_provenance_carries_no_password(self, tmp_path: Path) -> None:
        root = _proc(tmp_path, {4242: BARE_NODE_ARGV})
        found = discovery.discover(proc_root=root)
        rendered = str(found.target.to_json())
        assert "admin" not in rendered


class TestPasswordFromEnvironment:
    """The password comes from the environment, never from /proc."""

    def test_missing_env_var_names_the_remedy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(discovery.PASSWORD_ENV, raising=False)
        with pytest.raises(AdminPasswordMissing, match=discovery.PASSWORD_ENV):
            discovery.read_password()

    def test_present_env_var_is_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(discovery.PASSWORD_ENV, "admin")
        assert discovery.read_password().password == "admin"

    def test_credentials_repr_redacts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Guards against a secret escaping through an f-string, a log call, or
        # pytest's own assertion rendering.
        monkeypatch.setenv(discovery.PASSWORD_ENV, "s3cret")
        creds = discovery.read_password()
        assert "s3cret" not in repr(creds)
        assert "s3cret" not in f"{creds}"
        assert "***" in repr(creds)


class TestDiscovery:
    """Turning a process into a target."""

    def test_plaintext_node(self, tmp_path: Path) -> None:
        root = _proc(tmp_path, {4242: BARE_NODE_ARGV})
        found = discovery.discover(proc_root=root)

        assert found.pid == 4242
        assert found.target.scheme == "http"
        assert found.target.host == "127.0.0.1"
        assert found.target.port == 5050
        assert found.target.tls is TlsPolicy.PLAINTEXT
        assert found.entry_url == "http://127.0.0.1:5050/controller/"
        assert found.target.spki_pins == ()

    def test_debug_log_path_matches_the_node_derivation(self, tmp_path: Path) -> None:
        # Derived rather than fetched from the diagnostic API, which the fidelity
        # rules put out of reach.
        root = _proc(tmp_path, {4242: BARE_NODE_ARGV})
        found = discovery.discover(proc_root=root)
        assert found.debug_log_path == str(
            Path(tempfile.gettempdir()) /
            "nmos-controller-127.0.0.1-5050.log"
        )
        assert found.debug_tracing

    def test_debug_tracing_absent_without_the_flag(self, tmp_path: Path) -> None:
        argv = tuple(a for a in BARE_NODE_ARGV if a != "--debug-in-depth")
        root = _proc(tmp_path, {1: argv})
        found = discovery.discover(proc_root=root)
        assert found.debug_log_path is None
        assert not found.debug_tracing

    def test_ipv6_addr_colons_become_hyphens(self) -> None:
        assert discovery.derive_debug_log_path("::1", 5050) == str(
            Path(tempfile.gettempdir()) / "nmos-controller---1-5050.log"
        )

    def test_wildcard_bind_is_substituted_and_recorded(self, tmp_path: Path) -> None:
        argv = ("python3.12", "nmos_node.py", "--nodeAddr", "0.0.0.0",
                "--nodeControlPort", "5050", "--nodeDisableTLS",
                "--debug-in-depth")
        root = _proc(tmp_path, {9: argv})
        found = discovery.discover(proc_root=root)
        assert found.target.host == "127.0.0.1"
        # The substitution is visible in the journal rather than silent.
        assert found.target.provenance["host_substituted"] == "0.0.0.0 -> 127.0.0.1"
        # But the debug log path uses the address the node itself used.
        assert found.debug_log_path == str(
            Path(tempfile.gettempdir()) /
            "nmos-controller-0.0.0.0-5050.log"
        )

    def test_provenance_records_the_rig(self, tmp_path: Path) -> None:
        root = _proc(tmp_path, {4242: BARE_NODE_ARGV})
        found = discovery.discover(proc_root=root)
        provenance = found.target.provenance
        assert provenance["pid"] == "4242"
        assert provenance["node_config"] == "config10_nousb"
        assert provenance["node_serial"] == "SNX00001"
        assert provenance["ipmx"] == "True"


class TestDiscoveryRefusals:
    """Every way discovery declines, and whether the message is actionable."""

    def test_no_node_at_all(self, tmp_path: Path) -> None:
        root = _proc(tmp_path, {})
        with pytest.raises(NodeNotFound, match="attaches to a node you started"):
            discovery.discover(proc_root=root)

    def test_node_without_control_port(self, tmp_path: Path) -> None:
        # Distinguished from "no node" because the remedies differ.
        argv = ("python3.12", "nmos_node.py", "--nodeAddr", "127.0.0.1",
                "--nodePort", "7051", "--nodeDisableTLS")
        root = _proc(tmp_path, {11: argv})
        with pytest.raises(ControllerNotEnabled, match="--nodeControlPort"):
            discovery.discover(proc_root=root)

    def test_multiple_nodes_refuse_to_guess(self, tmp_path: Path) -> None:
        # start-node2/3 exist, so several nodes is a normal situation. Picking
        # one would mean demonstrating a rig the operator did not choose.
        second = tuple(
            "5051" if a == "5050" else a for a in BARE_NODE_ARGV)
        root = _proc(tmp_path, {1: BARE_NODE_ARGV, 2: second})
        with pytest.raises(NodeAmbiguous, match="Pass --controlPort") as caught:
            discovery.discover(proc_root=root)
        assert sorted(caught.value.candidates) == [(1, 5050), (2, 5051)]

    def test_control_port_disambiguates(self, tmp_path: Path) -> None:
        second = tuple("5051" if a == "5050" else a for a in BARE_NODE_ARGV)
        root = _proc(tmp_path, {1: BARE_NODE_ARGV, 2: second})
        found = discovery.discover(proc_root=root, control_port=5051)
        assert found.pid == 2
        assert found.target.port == 5051

    def test_unknown_control_port(self, tmp_path: Path) -> None:
        root = _proc(tmp_path, {1: BARE_NODE_ARGV})
        with pytest.raises(NodeNotFound, match="9999"):
            discovery.discover(proc_root=root, control_port=9999)

    def test_oauth2_node_is_discovered_and_flagged(self, tmp_path: Path) -> None:
        """An OAuth 2.0 rig is a supported target, not a refusal.

        Discovery used to reject these outright because the sign-in stage
        against an Authorization Server was not driven. It is now, so the flag
        is carried on the target instead — ``ControllerSession.sign_in`` uses
        where the browser actually lands to decide whether a second stage is
        needed.
        """
        argv = (*BARE_NODE_ARGV, "--oauth2")
        root = _proc(tmp_path, {1: argv})
        found = discovery.discover(proc_root=root)
        assert found.oauth2 is True

    def test_node_without_oauth2_is_not_flagged(self, tmp_path: Path) -> None:
        root = _proc(tmp_path, {1: BARE_NODE_ARGV})
        assert discovery.discover(proc_root=root).oauth2 is False


class TestSelfExclusion:
    """The driver must not discover itself."""

    def test_own_pid_excluded(self, tmp_path: Path) -> None:
        # A driver process whose command line mentions the script name would
        # otherwise be a candidate to attach to.
        root = _proc(tmp_path, {os.getpid(): BARE_NODE_ARGV})
        with pytest.raises(NodeNotFound):
            discovery.discover(proc_root=root)
