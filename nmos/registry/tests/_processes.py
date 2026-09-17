# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""A raft cluster of real OS processes, killable in ways objects cannot be.

The third rung, and deliberately the least observable
-----------------------------------------------------
``nmos/raft/tests/_harness.py`` makes members objects and the transport method
calls, so every one of Raft's five safety properties can be evaluated after
every step. ``_sockets.py`` keeps the objects and makes the transport real TCP.
Both see inside the members, and that is what lets them assert the things that
matter most -- which is why neither is being replaced.

Here the members are separate processes, so the logs, the terms and the commit
indices are behind a process boundary and the only honest view is the one an
operator has: the HTTP APIs, and the log file. Assertions accordingly drop from
"Log Matching holds across five members after every step" to "these registries
eventually agree on what exists". That is weaker, and it is the price of the
one thing this rung can do that the others structurally cannot:

* **SIGKILL**, which is not ``node.close()``. A graceful close cancels tasks
  and shuts the transport down in order; a crash does none of it. The term file
  in ``persist.py`` is the single piece of durable state the whole
  election-safety argument rests on, written with an atomic rename and a
  directory fsync -- and nothing anywhere else kills a process mid-write and
  then asks whether that file is still readable.
* **SIGSTOP**, which is not unreachability. A frozen process keeps its TCP
  connections open: peers see no RST and no FIN, the kernel goes on ACKing into
  the receive buffer, and the member looks perfectly alive while answering
  nothing. Neither of the other rigs can produce that -- ``stop()`` there means
  *unreachable*, which is the opposite signal -- and it is precisely the case
  check-quorum, the leader lease and the new replication pause interact over.
* **Independent scheduling.** In one process every member shares an event loop,
  so one member being busy is impossible. Here they are genuinely separate, so
  the bounded-apply yielding in ``node.py`` faces schedulers it does not
  control.

Spawned directly rather than through the launcher
-------------------------------------------------
``test_config_c_raft_e2e.py`` covers the launcher, on its fixed ports. This
needs free ports so it can run beside anything else, and needs to vary the
member count -- so it invokes ``nmos_registry.py`` itself. The launcher is
still what an operator types; this is what a fault injector needs.
"""

from __future__ import annotations

import json
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]


def free_pair() -> tuple[int, int]:
    """A free port ``P`` whose neighbour ``P+1`` is free too.

    ``--registryAdvertisedHost host:client_port`` derives the peer port as
    ``client_port + 1``, so co-located members need consecutive pairs rather
    than two independent ports.
    """
    for _attempt in range(200):
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", 0))
            first = int(probe.getsockname()[1])
        with socket.socket() as neighbour:
            neighbour.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                neighbour.bind(("127.0.0.1", first + 1))
            except OSError:
                continue
        return first, first + 1
    raise AssertionError("could not find a consecutive free port pair")


def free_port() -> int:
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class ProcessMember:
    """One registry process and the ports it answers on."""

    def __init__(self, index: int, root: Path) -> None:
        self.index = index
        self.root = root
        self.raft_client, self.raft_peer = free_pair()
        self.registration = free_port()
        self.query = free_port()
        self.websocket = free_port()
        self.state_dir = root / f"m{index}"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = root / f"m{index}.log"
        self.stdout_path = root / f"m{index}.out"
        self.process: subprocess.Popen[bytes] | None = None
        self.frozen = False

    def command(self, peers: list[ProcessMember]) -> list[str]:
        argv = [
            sys.executable, str(REPO_ROOT / "nmos_registry.py"),
            "--registryDisableTLS",
            "--registryAddr", "127.0.0.1",
            "--registrationPort", str(self.registration),
            "--queryPort", str(self.query),
            "--queryWebSocketPort", str(self.websocket),
            "--logFile", str(self.log_path),
            "--statusInterval", "0",
            "--distributed",
            "--distributedBackend", "raft",
            "--raftDisableTLS",
            "--raftStateDir", str(self.state_dir),
            "--raftNamespace", "/process-faults",
            "--registryAdvertisedHost", f"127.0.0.1:{self.raft_client}",
        ]
        for peer in peers:
            if peer.index != self.index:
                argv += [
                    "--registryNeighbour", f"127.0.0.1:{peer.raft_client}",
                ]
        return argv

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None


class ProcessCluster:
    """``size`` registry processes that can be killed, frozen and restarted."""

    def __init__(self, size: int, root: Path) -> None:
        self.root = root
        self.members = [ProcessMember(index, root) for index in range(size)]

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        for member in self.members:
            self._spawn(member)

    def _spawn(self, member: ProcessMember) -> None:
        handle = member.stdout_path.open("ab")
        member.process = subprocess.Popen(
            member.command(self.members), cwd=str(REPO_ROOT),
            stdout=handle, stderr=subprocess.STDOUT,
            env={"PYTHONPATH": str(REPO_ROOT), "PATH": "/usr/bin:/bin"},
        )
        member.frozen = False

    def stop_all(self) -> None:
        """Tear everything down, including anything left frozen.

        SIGCONT first: a stopped process never sees SIGTERM, so a rig that
        only terminated would leak a frozen registry holding its ports, and
        the next test would fail somewhere else entirely.
        """
        for member in self.members:
            if member.process is None:
                continue
            if member.frozen:
                self._signal(member, signal.SIGCONT)
            member.process.terminate()
        for member in self.members:
            if member.process is None:
                continue
            try:
                member.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                member.process.kill()
                member.process.wait(timeout=10)
            member.process = None

    # -- faults ----------------------------------------------------------

    def kill(self, index: int) -> None:
        """SIGKILL: no cleanup, no flush, no chance to close anything."""
        member = self.members[index]
        if member.process is None:
            return
        if member.frozen:
            self._signal(member, signal.SIGCONT)
        member.process.kill()
        member.process.wait(timeout=10)
        member.process = None
        member.frozen = False

    def freeze(self, index: int) -> None:
        """SIGSTOP: still connected, still listening, answering nothing."""
        member = self.members[index]
        self._signal(member, signal.SIGSTOP)
        member.frozen = True

    def thaw(self, index: int) -> None:
        member = self.members[index]
        self._signal(member, signal.SIGCONT)
        member.frozen = False

    def restart(self, index: int) -> None:
        """Kill and respawn, keeping the same ports and state directory.

        The state directory is deliberately kept: the term file surviving a
        crash is what the restart is meant to exercise.
        """
        self.kill(index)
        self._await_port_free(self.members[index].raft_peer)
        self._spawn(self.members[index])

    def _signal(self, member: ProcessMember, number: int) -> None:
        if member.process is not None and member.process.poll() is None:
            member.process.send_signal(number)

    @staticmethod
    def _await_port_free(port: int, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    probe.bind(("127.0.0.1", port))
                except OSError:
                    time.sleep(0.05)
                    continue
            return
        raise AssertionError(f"port {port} never became free")

    # -- observation, from outside -----------------------------------------

    def leader_index(self) -> int | None:
        """Whoever most recently announced itself leader, from the logs.

        The only view available from out here, and the one an operator has.
        Read afresh every time rather than cached: the point of this rig is
        that leadership changes underneath it.
        """
        best: tuple[int, int] | None = None
        for member in self.members:
            try:
                text = member.log_path.read_text(errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                if "is leader for term" in line:
                    term = int(line.rsplit("term", 1)[1].strip().rstrip("."))
                    if best is None or term > best[0]:
                        best = (term, member.index)
        return None if best is None else best[1]

    def register(self, index: int, node: dict[str, Any]) -> int:
        member = self.members[index]
        request = urllib.request.Request(
            f"http://127.0.0.1:{member.registration}"
            f"/x-nmos/registration/v1.3/resource",
            data=json.dumps({"type": "node", "data": node}).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as answer:
            return int(answer.status)

    def node_ids(self, index: int) -> list[str]:
        member = self.members[index]
        with urllib.request.urlopen(
            f"http://127.0.0.1:{member.query}/x-nmos/query/v1.3/nodes",
            timeout=20,
        ) as answer:
            return [entry["id"] for entry in json.load(answer)]

    def answers(self, index: int, timeout: float = 20.0) -> bool:
        """Does this member's Query API answer within ``timeout``?

        The timeout is a parameter because a *frozen* member is the one case
        where it matters: its listening socket is still open and the kernel
        still completes the handshake, so a request neither refuses nor
        succeeds -- it hangs. Distinguishing "stopped" from "running" therefore
        needs a short deadline, and using the default twenty seconds would make
        a liveness check take twenty seconds to say no.
        """
        member = self.members[index]
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{member.query}/x-nmos/query/v1.3/nodes",
                timeout=timeout,
            ):
                return True
        except Exception:
            return False

    def await_writable(
        self, index: int, node: dict[str, Any], timeout: float = 60.0,
    ) -> bool:
        """Poll a registration until it is accepted, or give up.

        503 means "not ready", exactly as it does for a Node, and a Node's
        answer to it is to retry. Anything else is a real failure and is
        raised rather than swallowed.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.register(index, node) == 201:
                    return True
            except urllib.error.HTTPError as exc:
                if exc.code != 503:
                    raise
            except (urllib.error.URLError, OSError, TimeoutError):
                pass
            time.sleep(0.25)
        return False
