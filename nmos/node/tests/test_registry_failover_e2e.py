# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""End-to-end registry failover, with real processes and a real kill.

Unit tests cover the selector's arithmetic and the client's decisions. They
cannot cover what actually breaks here: sockets refusing connections, a
registry dying mid-heartbeat, and six WebSocket subscribers noticing at
different moments. When this was first exercised by hand it exposed a defect
the 3000+ unit tests did not -- and then a badly written assertion reported a
false pass, so the behaviour was briefly believed broken and briefly believed
proven on bad evidence. Both are why it lives here rather than in a scratch
script.

Deliberately INDEPENDENT registries (no ``--rdsDistributed``), the harder case:
registry #2 has never heard of this Node, so the heartbeat probe mandated by
``Behaviour - Registration.md:124`` must answer 404 and drive a full
re-registration. The clustered case is the easy one -- 200, nothing to do.

A **second Node** registers with registry #1 alone and has no list to fail over
along, so everything it published exists in that registry and nowhere else. It
is what makes "the cache became the union of both registries" observable rather
than theoretical: once the Controller has moved, anything of PHANTOM02's still
on a page exists in no live registry at all. Driving the UI by hand found
exactly that -- three of its senders still listed, with a green health dot,
seven minutes after the only registry holding them had died.

Marked ``e2e``: excluded from the default gate, run with ``-m "e2e or slow"``.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Iterator

import pytest

pytestmark = pytest.mark.e2e

REPO = Path(__file__).resolve().parents[3]
QV = "v1.3"
ADMIN_PASSWORD = "failover-e2e"

# The Node switches within a heartbeat cycle or two. The Controller's
# subscribers need FAILOVER_AFTER attempts, each costing up to
# (connect timeout + backoff), so they are allowed considerably longer.
NODE_FAILOVER_TIMEOUT = 90.0
CONTROLLER_FAILOVER_TIMEOUT = 120.0


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def _get(url: str, timeout: float = 2.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode())


def _wait_http(url: str, seconds: float = 60.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            _get(url)
            return True
        except Exception:
            time.sleep(0.25)
    return False


def _nodes_on(query_port: int) -> list[str]:
    try:
        return [n["id"] for n in
                _get(f"http://127.0.0.1:{query_port}/x-nmos/query/{QV}/nodes")]
    except Exception:
        return []


def _wait_node_on(query_port: int, seconds: float) -> float | None:
    """Seconds until a Node is registered here, or None if it never is."""
    start = time.time()
    while time.time() - start < seconds:
        if _nodes_on(query_port):
            return time.time() - start
        time.sleep(0.25)
    return None


class _Rig:
    """Two independent registries plus a Node with an embedded Controller."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.procs: list[subprocess.Popen[bytes]] = []
        self.r1 = {k: _free_port() for k in ("reg", "q", "ws")}
        self.r2 = {k: _free_port() for k in ("reg", "q", "ws")}
        self.node_port = _free_port()
        self.ctrl_port = _free_port()
        self.phantom_port = _free_port()
        self.registry1: subprocess.Popen[bytes] | None = None

    def _spawn(self, cmd: list[str], name: str) -> subprocess.Popen[bytes]:
        handle = (self.tmp / f"{name}.log").open("wb")
        # Its own session, so the whole tree can be signalled on teardown even
        # if a child spawns further children.
        proc = subprocess.Popen(
            cmd, cwd=str(REPO), stdout=handle, stderr=subprocess.STDOUT,
            env=dict(os.environ, PYTHONPATH=str(REPO), NMOS_LOG_LEVEL="INFO"),
            start_new_session=True,
        )
        self.procs.append(proc)
        return proc

    def start(self) -> None:
        for tag, ports in (("reg1", self.r1), ("reg2", self.r2)):
            proc = self._spawn([
                sys.executable, "nmos_registry.py",
                "--registryDisableTLS", "--registryAddr", "127.0.0.1",
                "--registrationPort", str(ports["reg"]),
                "--queryPort", str(ports["q"]),
                "--queryWebSocketPort", str(ports["ws"]),
                "--logFile", "", "--statusInterval", "0",
            ], tag)
            if tag == "reg1":
                self.registry1 = proc

        for ports in (self.r1, self.r2):
            assert _wait_http(
                f"http://127.0.0.1:{ports['q']}/x-nmos/query/{QV}/",
            ), "registry did not start"

        def spec(ports: dict[str, int]) -> str:
            return (f"host=127.0.0.1,registrationPort={ports['reg']},"
                    f"queryPort={ports['q']},wsPort={ports['ws']},disableTLS")

        self._spawn([
            sys.executable, "nmos_node.py",
            "--nodeDisableTLS", "--nodeAddr", "127.0.0.1",
            "--nodePort", str(self.node_port),
            "--nodeControlPort", str(self.ctrl_port),
            "--nodeSerialNumber", "FAILOVER01",
            # Without this the Controller UI silently does not start, and the
            # WebSocket subscribers under test never run.
            "--controllerAdminPassword", ADMIN_PASSWORD,
            "--rdsDisableTLS", "--rdsHost", "127.0.0.1",
            "--rdsRegistrationPort", str(self.r1["reg"]),
            "--rdsQueryPort", str(self.r1["q"]),
            "--rdsWebSocketPort", str(self.r1["ws"]),
            "--rds", spec(self.r1),
            "--rds", spec(self.r2),
            "--logFile", "",
        ], "node")

        # Pinned to registry #1 with no --rds list, so it has nowhere to go.
        # No --nodeControlPort either: it serves no UI and cannot be confused
        # with the Node under test.
        self._spawn([
            sys.executable, "nmos_node.py",
            "--nodeDisableTLS", "--nodeAddr", "127.0.0.1",
            "--nodePort", str(self.phantom_port),
            "--nodeSerialNumber", "PHANTOM02",
            "--rdsDisableTLS", "--rdsHost", "127.0.0.1",
            "--rdsRegistrationPort", str(self.r1["reg"]),
            "--rdsQueryPort", str(self.r1["q"]),
            "--rdsWebSocketPort", str(self.r1["ws"]),
            "--logFile", "",
        ], "phantom")

    def controller_html(self, path: str) -> str:
        """One Controller page, as a signed-in operator would be served it.

        Read through the operator surface rather than the Controller's JSON
        API on purpose: what this asserts is what somebody would actually be
        looking at.
        """
        import http.cookiejar
        import urllib.parse

        from nmos.controller.app import LOGIN_PATH

        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        )
        base = f"http://127.0.0.1:{self.ctrl_port}"
        form = urllib.parse.urlencode({"password": ADMIN_PASSWORD}).encode()
        with opener.open(f"{base}{LOGIN_PATH}", form, timeout=15) as response:
            response.read()
        with opener.open(f"{base}{path}", timeout=15) as response:
            return str(response.read().decode(errors="replace"))

    def kill_registry1(self) -> None:
        assert self.registry1 is not None
        os.killpg(os.getpgid(self.registry1.pid), signal.SIGKILL)
        self.registry1.wait(timeout=15)

    def node_log(self) -> str:
        return (self.tmp / "node.log").read_text(errors="replace")

    def stop(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            for proc in self.procs:
                try:
                    os.killpg(os.getpgid(proc.pid), sig)
                except Exception:
                    pass
            time.sleep(1.0)


@pytest.fixture(scope="module")
def rig(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Rig]:
    r = _Rig(tmp_path_factory.mktemp("failover"))
    try:
        r.start()
        yield r
    finally:
        r.stop()


def _await_line(rig: _Rig, needles: tuple[str, ...], seconds: float) -> str:
    """First log line containing every needle, or "" on timeout.

    Every needle must be on ONE line. An earlier version of this check tested
    the needles against the whole file, where they matched two unrelated lines
    and reported a false pass -- ending the run before the subscribers had
    taken their attempts.
    """
    deadline = time.time() + seconds
    while time.time() < deadline:
        for line in rig.node_log().splitlines():
            if all(n in line for n in needles):
                return line.strip()
        time.sleep(1.0)
    return ""


class TestRegistryFailover:
    """Ordered: each step depends on the previous one having happened."""

    def test_1_both_nodes_register_with_the_best_priority_registry(
        self, rig: _Rig,
    ) -> None:
        """TR-10-9 section 15: select the service with the best priority.

        Both must be present before the kill, or the phantom half of the
        experiment never reaches the Controller's cache to begin with and
        test 8 would pass without proving anything.
        """
        elapsed = _wait_node_on(rig.r1["q"], 90.0)
        assert elapsed is not None, "Node never registered with registry #1"

        deadline = time.time() + 60.0
        while time.time() < deadline and len(_nodes_on(rig.r1["q"])) < 2:
            time.sleep(0.25)
        assert len(_nodes_on(rig.r1["q"])) == 2, (
            "PHANTOM02 never registered with registry #1"
        )

    def test_2_the_second_registry_is_untouched(self, rig: _Rig) -> None:
        """Proves these really are independent, so step 4 exercises the 404."""
        assert _nodes_on(rig.r2["q"]) == []

    def test_3_kill_the_first_registry(self, rig: _Rig) -> None:
        rig.kill_registry1()
        assert not _nodes_on(rig.r1["q"])

    def test_4_node_fails_over_and_re_registers(self, rig: _Rig) -> None:
        """The registration half, end to end.

        Must land inside the garbage-collection interval of the last good
        heartbeat (``Behaviour - Registration.md:128``) or the Node is
        collected; the generous timeout here is to diagnose failure, not to
        license slowness.
        """
        elapsed = _wait_node_on(rig.r2["q"], NODE_FAILOVER_TIMEOUT)
        assert elapsed is not None, "Node never re-registered on registry #2"

    def test_5_the_switch_used_the_mandated_heartbeat_probe(
        self, rig: _Rig,
    ) -> None:
        """``:124`` -- probe first, then act on the answer.

        An independent registry has never heard of this Node, so the probe
        must 404 and drive a full registration. A 200 here would mean the
        probe was skipped and the old delete-and-re-POST path had run.
        """
        assert _await_line(rig, ("switching to",), 30.0), "no switch logged"
        assert _await_line(
            rig, ("does not hold this Node",), 30.0,
        ), "the heartbeat probe did not report 404"

    def test_6_controller_subscribers_follow(self, rig: _Rig) -> None:
        """Only ONE subscriber logs the switch, and that is correct.

        ``RegistrySelector.fail()`` advances only while the reported target is
        still current, so the first of the six to reach the threshold moves the
        selection and the other five follow by re-reading ``current``.

        That is also why the reaction to the switch cannot live in this branch
        per subscriber: for five kinds in six it never runs. It is delegated to
        ``_on_selection_moved``, which acts once for all of them -- which is
        what the next test measures the result of.
        """
        line = _await_line(
            rig, ("rds_ws[", "switching to"), CONTROLLER_FAILOVER_TIMEOUT,
        )
        assert line, "no Controller subscriber switched"
        assert "reloading all kinds" in line, line

    def test_7_controller_ui_still_serves(self, rig: _Rig) -> None:
        """The operator-facing surface survives losing its registry."""
        from nmos.controller.app import LOGIN_PATH
        with urllib.request.urlopen(
            f"http://127.0.0.1:{rig.ctrl_port}{LOGIN_PATH}", timeout=10,
        ) as response:
            assert response.status == 200

    def test_8_nothing_from_the_abandoned_registry_survives(
        self, rig: _Rig,
    ) -> None:
        """The phantom test, and the reason the second Node exists.

        PHANTOM02 lives only in the registry that died and has no list to fail
        over along, so after the switch it exists nowhere. Grains only upsert,
        so anything the switch does not delete stays on screen for the life of
        the process -- which is what a by-hand run found: its senders listed,
        healthy-looking, long after the registry holding them had gone.

        Both pages are checked. Checking one would have passed before this was
        fixed: the single kind that did get cleared was, by luck of the draw,
        ``receiver``, so the Receivers page looked perfect while the Senders
        page carried three resources that existed nowhere.
        """
        assert _nodes_on(rig.r2["q"]) and len(_nodes_on(rig.r2["q"])) == 1, (
            "ground truth: the surviving registry holds exactly one Node"
        )

        deadline = time.time() + CONTROLLER_FAILOVER_TIMEOUT
        pages: dict[str, str] = {}
        while time.time() < deadline:
            pages = {
                path: rig.controller_html(f"/controller/{path}")
                for path in ("senders", "receivers")
            }
            # The reload is what repopulates FAILOVER01; waiting for it also
            # rules out passing on a page that is merely still empty.
            if all("FAILOVER01" in html for html in pages.values()):
                break
            time.sleep(2.0)

        for path, html in pages.items():
            assert "FAILOVER01" in html, f"{path}: surviving node not listed"
            assert "PHANTOM02" not in html, (
                f"{path}: still shows a resource that exists in no registry"
            )
