# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The etcd implementation of :class:`ClusterRig`.

Consolidates what ``etcd_server.py``, ``test_etcd_outage.py::Killable`` and
``test_etcd_cluster_e2e.py::Cluster`` each did separately: N members on
loopback differing by port, started concurrently, killable and restartable
individually, with data directories that survive a kill so a restart rejoins
rather than forming a second cluster.

The port-reservation trick is carried over verbatim from ``Cluster`` and is
load-bearing -- see ``_reserve``.
"""

from __future__ import annotations

import dataclasses
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nmos.registry.tests.rigs.protocol import RigUnavailable

# Long enough for a cold three-member formation on a loaded machine, short
# enough that a genuinely broken rig fails inside a test run rather than at
# the suite timeout.
_FORMATION_TIMEOUT_S = 90.0


@dataclass
class _Member:
    name: str
    client_port: int
    peer_port: int
    data_dir: Path
    process: subprocess.Popen[bytes] | None = field(default=None, repr=False)

    @property
    def peer_url(self) -> str:
        return f"http://127.0.0.1:{self.peer_port}"

    @property
    def client_url(self) -> str:
        return f"http://127.0.0.1:{self.client_port}"

    @property
    def endpoint(self) -> str:
        return f"127.0.0.1:{self.client_port}"


class EtcdRig:
    """``size`` etcd members on loopback, no TLS.

    No TLS by design, matching ``etcd_server.py``: securing the rig would test
    the certificate set rather than the registry, and the secured path has its
    own dedicated suite in ``test_etcd_mtls_e2e.py``.
    """

    def __init__(self, *, size: int, root: Path) -> None:
        from nmos.etcd.tests.etcd_server import BUNDLED_ETCD

        binary = (
            str(BUNDLED_ETCD) if BUNDLED_ETCD.is_file() else shutil.which("etcd")
        )
        if binary is None:
            raise RigUnavailable("etcd not installed; run ./install-etcd.sh")

        self._binary = binary
        self._token = f"nmos-rig-{root.name}"
        self._backends: dict[int, Any] = {}
        self._reserved: list[socket.socket] = []
        self._members = [
            _Member(
                name=f"m{index}",
                client_port=self._reserve(),
                peer_port=self._reserve(),
                data_dir=root / f"m{index}",
            )
            for index in range(size)
        ]

    # -- ports ----------------------------------------------------------

    def _reserve(self) -> int:
        """Take a port by HOLDING the socket until the instant before spawning.

        The usual bind-and-close idiom leaves a window in which another test --
        and the e2e suite starts a lot of servers -- can take the port before
        etcd binds it, which surfaces as a cluster that mysteriously fails to
        form, far from the test that stole it.
        """
        held = socket.socket()
        held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        held.bind(("127.0.0.1", 0))
        self._reserved.append(held)
        port: int = held.getsockname()[1]
        return port

    def _release(self) -> None:
        for held in self._reserved:
            held.close()
        self._reserved.clear()

    # -- ClusterRig -----------------------------------------------------

    @property
    def size(self) -> int:
        return len(self._members)

    @property
    def quorum(self) -> int:
        return self.size // 2 + 1

    @property
    def failures_tolerated(self) -> int:
        return self.size - self.quorum

    @property
    def endpoints(self) -> tuple[str, ...]:
        return tuple(member.endpoint for member in self._members)

    async def start_all(self) -> None:
        # Concurrently: with initial-cluster-state=new each member blocks until
        # it can reach a quorum of peers, so starting them one at a time and
        # waiting for each would deadlock on the first.
        self._release()
        for member in self._members:
            self._spawn(member, new=True)
        self._await_formation()

    async def stop_all(self) -> None:
        for backend in self._backends.values():
            await backend.close()
        self._backends.clear()
        # Teardown does not wait for ports: nothing is going to connect to them
        # again, and the per-member wait below would add seconds to every test.
        for member in self._members:
            self._terminate(member)
        self._release()

    async def backend_for(
        self, index: int, registry: Any, namespace: str, **overrides: Any,
    ) -> Any:
        """An ``EtcdRegistryBackend`` pointed at this cluster.

        Every member's registry dials *all* of them, local first, because that
        is what a real deployment does and what makes the failover assertions
        meaningful.
        """
        from nmos.registry.etcd_backend import EtcdRegistryBackend

        backend = EtcdRegistryBackend(
            registry, self._config_for(index, namespace, **overrides),
        )
        await backend.start()
        self._backends[index] = backend
        return backend

    async def kill(self, index: int) -> None:
        """Kill the etcd member. The registry in front of it keeps running.

        That asymmetry is real and is the point: with etcd the registry is a
        *client* of the storage layer, so losing a member costs it a failover
        rather than its existence.
        """
        self._kill_member(index)

    async def lose_quorum(self) -> None:
        survivors = self.size // 2 + 1
        for index in range(self.size - 1, survivors - 2, -1):
            self._kill_member(index)

    def _kill_member(self, index: int) -> None:
        member = self._members[index]
        if member.process is None:
            return
        self._terminate(member)
        # Not returning until the listener is genuinely gone: a test that kills
        # a member and expects the next connection to fail must not race a
        # socket that is still accepting.
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            # SO_REUSEADDR deliberately: the question is "is anything still
            # LISTENING here", not "has TIME_WAIT drained". Without it a port
            # left in TIME_WAIT by the dead member fails to bind for up to a
            # minute, and the rig spins the whole deadline on a member that
            # died promptly -- which is how a correct kill turns into a
            # three-minute test suite.
            probe = socket.socket()
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", member.client_port))
                return
            except OSError:
                time.sleep(0.05)
            finally:
                probe.close()

    def restart_member(self, index: int) -> None:
        # `existing`, not `new`: the data directory survived the kill, so this
        # member rejoins the cluster it was part of. Starting it as `new` on a
        # surviving data directory is how one dead member becomes two clusters.
        self._spawn(self._members[index], new=False)
        self._await_member(self._members[index])

    def _config_for(self, index: int, namespace: str, **overrides: Any) -> Any:
        from nmos.cluster.layout import MemberSpec, derive_cluster
        from nmos.registry.distributed import EtcdConfig

        local = self._members[index]
        layout = derive_cluster(
            [
                MemberSpec(
                    host="127.0.0.1",
                    client_port=member.client_port,
                    peer_port=member.peer_port,
                    name=member.name,
                )
                for member in self._members
            ],
            local_host="127.0.0.1",
            local_peer_port=local.peer_port,
            namespace=namespace,
            tls=False,
        )
        # Local endpoint first, so the channel pool prefers the co-located
        # member exactly as a real deployment does.
        endpoints = (local.endpoint, *(
            member.endpoint for member in self._members if member is not local
        ))
        config = EtcdConfig(
            layout=layout,
            endpoints=endpoints,
            namespace=namespace,
            tls=False,
            certificate="",
            key="",
            trusted_root_ca=(),
            certificate_name="",
            rpc_timeout=5.0,
            mutation_timeout=10.0,
            external=True,
            binary="",
            data_dir=Path(),
            bootstrap=False,
            client_crl_file="",
            peer_crl_file="",
        )
        return dataclasses.replace(config, **overrides) if overrides else config

    # -- internals ------------------------------------------------------

    def _terminate(self, member: _Member) -> None:
        if member.process is None:
            return
        member.process.kill()
        member.process.wait(timeout=10)
        member.process = None

    def _initial_cluster(self) -> str:
        return ",".join(f"{m.name}={m.peer_url}" for m in self._members)

    def _spawn(self, member: _Member, *, new: bool) -> None:
        member.process = subprocess.Popen(
            [
                self._binary,
                "--name", member.name,
                "--data-dir", str(member.data_dir),
                "--listen-client-urls", member.client_url,
                "--advertise-client-urls", member.client_url,
                "--listen-peer-urls", member.peer_url,
                "--initial-advertise-peer-urls", member.peer_url,
                "--initial-cluster", self._initial_cluster(),
                "--initial-cluster-state", "new" if new else "existing",
                "--initial-cluster-token", self._token,
                "--log-level", "error",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _await_formation(self) -> None:
        deadline = time.monotonic() + _FORMATION_TIMEOUT_S
        while time.monotonic() < deadline:
            dead = [
                m for m in self._members
                if m.process and m.process.poll() is not None
            ]
            if dead:
                raise RuntimeError(
                    "etcd member(s) "
                    + ", ".join(
                        f"{m.name} (exit {m.process.returncode})"
                        for m in dead if m.process
                    )
                    + " exited during startup",
                )
            if all(self._healthy(m) for m in self._members):
                return
            time.sleep(0.2)
        raise RuntimeError(
            "cluster did not form: "
            + ", ".join(
                f"{m.name} {'up' if self._healthy(m) else 'down'}"
                for m in self._members
            ),
        )

    def _await_member(self, member: _Member) -> None:
        deadline = time.monotonic() + _FORMATION_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._healthy(member):
                return
            time.sleep(0.1)
        raise RuntimeError(f"{member.name} did not come back up")

    def _healthy(self, member: _Member) -> bool:
        """Serving, not merely listening -- the port opens before etcd answers."""
        try:
            with urllib.request.urlopen(
                f"{member.client_url}/health", timeout=1.0,
            ) as reply:
                return b'"health":"true"' in reply.read()
        except (urllib.error.URLError, OSError, TimeoutError):
            return False
