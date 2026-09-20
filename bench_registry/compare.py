#!/usr/bin/env python3
# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Run the load generator across every target and separate the taxes.

    python3 bench_registry/compare.py --quiet
    python3 bench_registry/compare.py --targets cpp,standalone,rust
    python3 bench_registry/compare.py --targets cpp,standalone,dist1,dist3
    python3 bench_registry/compare.py --targets cpp,standalone,raft1,raft3,raft5
    python3 bench_registry/compare.py --targets rust,rustraft1,rustraft3,rustraft5
    python3 bench_registry/compare.py --targets standalone,rust,dist1,rustdist1,dist3,rustdist3

Matched observability comes first
---------------------------------
Both sides must log the same amount or this measures logging rather than
registries. nmos-cpp's ``logging_level`` runs 40 (least verbose, fatal only) to
**-40 (most verbose)** -- and every config bundled under ``nmos-registry/`` is
pinned at -40, the most verbose setting that exists, with an access log on top.

So this script writes its **own** config copies rather than touching the
operator's working files, and it counts the bytes each target writes during an
identical workload. If they differ by more than an order of magnitude the run is
reported as not comparable, because settings that claim to match are not
evidence that they do.

Separating the taxes
--------------------
A single side-by-side number would conflate unrelated costs::

    nmos-cpp    -> Python standalone   the PYTHON tax     (what Rust recovers)
    standalone  -> rust                how much of it the port actually recovers
    standalone  -> dist1  / raft1      the BACKEND tax    (no quorum involved)
    dist1 -> dist3 / raft1 -> raft3    the CONSENSUS tax  (what resilience costs)

The 1-member distributed configurations exist purely to split the last two,
which are otherwise indistinguishable. Both backends are measured the same way,
so "raft is faster" is a comparison of like with like rather than of a ratio
against a number.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
BENCH = REPO / "bench_registry"
WORK = BENCH / ".work"

# Default assumes this repo sits beside nmos-registry/ in the IPMX workspace.
# Overridable, because a committed benchmark that only runs on one machine is
# not much of a benchmark -- anyone re-checking the README's numbers needs to
# point it at their own baseline binary.
NMOS_CPP = Path(
    os.environ.get(
        "NMOS_CPP_REGISTRY",
        str(REPO.parent / "nmos-registry" / "nmos-cpp-registry"),
    )
)

# The Rust registry, which is the whole point of separating the PYTHON tax:
# ``nmos-cpp -> standalone`` measures what an interpreter costs, and
# ``standalone -> rust`` measures how much of it a port recovers.
#
# **Release, deliberately, with no debug fallback.** A debug build of this
# workspace runs several times slower, and a benchmark that silently measured
# one would not be wrong by a little -- it would invert the result this table
# exists to establish. Overridable for the same reason ``NMOS_CPP_REGISTRY``
# is: anyone re-checking a number needs to point it at their own binary.
RUST_REGISTRY = Path(
    os.environ.get(
        "NMOS_RUST_REGISTRY",
        str(REPO / "rust" / "target" / "release" / "nmos-registry"),
    )
)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def _wait_http(url: str, timeout: float = 60.0) -> bool:
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0):
                return True
        except urllib.error.HTTPError:
            return True  # answered, even if not 200
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(0.2)
    return False


@dataclass
class Target:
    """One registry under test, and where its output goes."""

    name: str
    registration: str
    query: str
    websocket: str = ""
    process: subprocess.Popen[bytes] | None = None
    log_paths: list[Path] = field(default_factory=list)
    stdout_path: Path | None = None
    extra: list[subprocess.Popen[bytes]] = field(default_factory=list)

    def log_bytes(self) -> int:
        """Total bytes this target wrote, for the matched-logging check."""
        total = 0
        for path in [*self.log_paths, *( [self.stdout_path] if self.stdout_path else [] )]:
            try:
                total += path.stat().st_size
            except OSError:
                pass
        return total

    def stop(self) -> None:
        for process in [self.process, *self.extra]:
            if process is None or process.poll() is not None:
                continue
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


# ---------------------------------------------------------------------------
# nmos-cpp
# ---------------------------------------------------------------------------

def start_nmos_cpp(quiet: bool) -> Target:
    """Start nmos-cpp with a harness-owned config.

    Never edits ``nmos-registry/my-config-*.json``: those are the operator's
    working files, and a benchmark that silently rewrites them is a benchmark
    that changes what it measures for everyone else too.
    """
    if not NMOS_CPP.is_file():
        raise SystemExit(f"nmos-cpp-registry not found at {NMOS_CPP}")

    query_port, registration_port, ws_port = _free_port(), _free_port(), _free_port()
    access_log = WORK / "nmos-cpp-access.log"
    error_log = WORK / "nmos-cpp-error.log"

    config = {
        # 40 = least verbose (fatal only); -40 = most verbose. The bundled
        # configs all use -40, which would make this a logging benchmark.
        "logging_level": 40 if quiet else 0,
        "access_log": "" if quiet else str(access_log),
        "error_log": "" if quiet else str(error_log),
        "host_address": "127.0.0.1",
        "query_port": query_port,
        "registration_port": registration_port,
        "query_ws_port": ws_port,
        "server_secure": False,
        "system_port": -1,
        "node_port": -1,
        "admin_port": -1,
        "mdns_port": -1,
        "schemas_port": -1,
        "settings_port": -1,
        "logging_port": -1,
        "pri": 2147483647,
    }
    config_path = WORK / ("nmos-cpp-quiet.json" if quiet else "nmos-cpp-default.json")
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    stdout_path = WORK / f"nmos-cpp-{'quiet' if quiet else 'default'}.out"
    handle = stdout_path.open("wb")
    process = subprocess.Popen(
        [str(NMOS_CPP), str(config_path)],
        cwd=str(NMOS_CPP.parent),
        stdout=handle,
        stderr=subprocess.STDOUT,
    )

    registration = f"http://127.0.0.1:{registration_port}"
    query = f"http://127.0.0.1:{query_port}"
    if not _wait_http(f"{query}/x-nmos/query/v1.3/"):
        process.kill()
        raise SystemExit(
            f"nmos-cpp did not start; see {stdout_path}",
        )

    return Target(
        name=f"nmos-cpp ({'quiet' if quiet else 'default'})",
        registration=registration,
        query=query,
        websocket=f"ws://127.0.0.1:{ws_port}",
        process=process,
        log_paths=[p for p in (access_log, error_log) if not quiet],
        stdout_path=stdout_path,
    )


# ---------------------------------------------------------------------------
# This registry
# ---------------------------------------------------------------------------

def _start_etcd_cluster(
    name: str, members: int,
) -> tuple[str, list[subprocess.Popen[bytes]]]:
    """Bring up a bench etcd cluster, returning its endpoints and processes.

    Shared by the Python and Rust distributed targets rather than written
    twice. The two must measure the *same* storage layer or the comparison
    between them is between two clusters, not two registries -- and a second
    copy that drifted in, say, its fsync setting would make one implementation
    look faster for a reason that has nothing to do with it.

    Durability knobs, env-only because they exist to answer one question --
    "how much of the etcd tax is the disk?" -- and must never be reachable
    from a normal run.

      NMOS_BENCH_ETCD_DATA_ROOT=/dev/shm/...  put the data dir on tmpfs, so the
          WAL and bbolt file never reach a block device. This is the closest
          thing etcd has to "memory only": there is no in-memory backend, the
          storage engine is always bbolt + WAL.
      NMOS_BENCH_ETCD_NO_FSYNC=1              pass --unsafe-no-fsync, which
          etcd documents as "unsafe, will cause data loss". It isolates the
          fsync SYSCALL from the write itself.

    Neither is a supported deployment option. A registry whose etcd loses its
    WAL on power failure has no authoritative state to recover from, which is
    the one thing adopting etcd was meant to provide.
    """
    cluster_ports = [(_free_port(), _free_port()) for _ in range(members)]
    endpoints = ",".join(f"127.0.0.1:{c}" for c, _ in cluster_ports)

    data_override = os.environ.get("NMOS_BENCH_ETCD_DATA_ROOT")
    data_root = (
        Path(data_override) / f"{name}-etcd" if data_override
        else WORK / f"{name}-etcd"
    )
    if data_root.exists():
        shutil.rmtree(data_root)
    data_root.mkdir(parents=True)

    binary = REPO / ".etcd" / "etcd"
    if not binary.is_file():
        raise SystemExit("etcd not installed; run ./install-etcd.sh")

    processes: list[subprocess.Popen[bytes]] = []
    initial = ",".join(
        f"m{i}=http://127.0.0.1:{p}" for i, (_c, p) in enumerate(cluster_ports)
    )
    for index, (client, peer) in enumerate(cluster_ports):
        processes.append(subprocess.Popen(
            [
                str(binary),
                "--name", f"m{index}",
                "--data-dir", str(data_root / f"m{index}"),
                "--listen-client-urls", f"http://127.0.0.1:{client}",
                "--advertise-client-urls", f"http://127.0.0.1:{client}",
                "--listen-peer-urls", f"http://127.0.0.1:{peer}",
                "--initial-advertise-peer-urls", f"http://127.0.0.1:{peer}",
                "--initial-cluster", initial,
                "--initial-cluster-state", "new",
                "--initial-cluster-token", f"bench-{name}",
                "--log-level", "error",
            ] + (
                ["--unsafe-no-fsync"]
                if os.environ.get("NMOS_BENCH_ETCD_NO_FSYNC") == "1" else []
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ))
    for client, _peer in cluster_ports:
        if not _wait_http(f"http://127.0.0.1:{client}/health"):
            for process in processes:
                process.kill()
            raise SystemExit("bench etcd cluster did not start")
    return endpoints, processes


def start_python(
    name: str, quiet: bool, *, members: int = 0,
) -> Target:
    """Start nmos_registry.py, standalone or distributed."""
    registration_port, query_port, ws_port = (
        _free_port(), _free_port(), _free_port(),
    )
    log_file = WORK / f"{name}.log"
    stdout_path = WORK / f"{name}.out"

    command = [
        sys.executable, str(REPO / "nmos_registry.py"),
        "--registryDisableTLS",
        "--registryAddr", "127.0.0.1",
        "--registrationPort", str(registration_port),
        "--queryPort", str(query_port),
        "--queryWebSocketPort", str(ws_port),
        "--logFile", "" if quiet else str(log_file),
        # Status lines are the analogue of nmos-cpp's per-POST status log.
        "--statusInterval", "0" if quiet else "5",
    ]

    extra: list[subprocess.Popen[bytes]] = []
    if members:
        endpoints, extra = _start_etcd_cluster(name, members)
        command += [
            # --distributedBackend defaults to raft, and naming --etcd* flags
            # without it is refused rather than reinterpreted. Spelled out
            # here for the same reason the launch scripts spell it out: the
            # benchmark must measure the backend it says it is measuring.
            "--distributed", "--distributedBackend", "etcd",
            "--etcdExternal", "--etcdDisableTLS",
            "--registryAdvertisedHost", "127.0.0.1",
            "--etcdEndpoints", endpoints,
            "--etcdNamespace", f"/bench/{name}",
        ]

    handle = stdout_path.open("wb")
    environment = dict(os.environ, PYTHONPATH=str(REPO))
    if os.environ.get("NMOS_ETCD_FAST_PATH"):
        environment["NMOS_ETCD_FAST_PATH"] = os.environ["NMOS_ETCD_FAST_PATH"]
    if quiet:
        # --logFile "" silences the FILE handler only; the console handler and
        # aiohttp's per-request access log both still write to stdout. Left
        # alone, this registry wrote ~159 KB during a run where nmos-cpp at
        # logging_level 40 wrote nothing at all.
        environment["NMOS_LOG_LEVEL"] = "WARNING"
    process = subprocess.Popen(
        command, cwd=str(REPO), stdout=handle, stderr=subprocess.STDOUT,
        env=environment,
    )

    registration = f"http://127.0.0.1:{registration_port}"
    query = f"http://127.0.0.1:{query_port}"
    if not _wait_http(f"{query}/x-nmos/query/v1.3/", timeout=90.0):
        process.kill()
        for child in extra:
            child.kill()
        raise SystemExit(f"{name} did not start; see {stdout_path}")

    return Target(
        name=name,
        registration=registration,
        query=query,
        websocket=f"ws://127.0.0.1:{ws_port}",
        process=process,
        log_paths=[log_file] if not quiet else [],
        stdout_path=stdout_path,
        extra=extra,
    )



def start_rust(name: str, quiet: bool) -> Target:
    """Start the Rust registry over plain HTTP.

    Deliberately the same flags ``start_python`` passes, because the comparison
    is only meaningful if the two are configured identically -- the command line
    is shared, and ``cli_parity.rs`` is what keeps it so.

    The observability flags are the ones that matter here. ``--logFile ""``
    silences the file sink, ``--statusInterval 0`` silences the periodic status
    line, and ``NMOS_LOG_LEVEL`` quietens the console; the Rust registry honours
    all three exactly as the Python one does. A target that ignored any of them
    would be measured while writing what the other was not, which is what
    ``log_bytes`` exists to catch.
    """
    if not RUST_REGISTRY.is_file():
        raise SystemExit(
            f"rust registry not built at {RUST_REGISTRY}\n"
            f"  (cd rust && cargo build --release -p nmos-registry-bin)\n"
            f"  or set NMOS_RUST_REGISTRY to a release binary"
        )

    registration_port, query_port, ws_port = (
        _free_port(), _free_port(), _free_port(),
    )
    log_file = WORK / f"{name}.log"
    stdout_path = WORK / f"{name}.out"

    command = [
        str(RUST_REGISTRY),
        "--registryDisableTLS",
        "--registryAddr", "127.0.0.1",
        "--registrationPort", str(registration_port),
        "--queryPort", str(query_port),
        "--queryWebSocketPort", str(ws_port),
        "--logFile", "" if quiet else str(log_file),
        "--statusInterval", "0" if quiet else "5",
    ]

    handle = stdout_path.open("wb")
    environment = dict(os.environ)
    if quiet:
        environment["NMOS_LOG_LEVEL"] = "WARNING"
    process = subprocess.Popen(
        command, cwd=str(REPO), stdout=handle, stderr=subprocess.STDOUT,
        env=environment,
    )

    registration = f"http://127.0.0.1:{registration_port}"
    query = f"http://127.0.0.1:{query_port}"
    if not _wait_http(f"{query}/x-nmos/query/v1.3/", timeout=90.0):
        process.kill()
        raise SystemExit(f"{name} did not start; see {stdout_path}")

    return Target(
        name=name,
        registration=registration,
        query=query,
        websocket=f"ws://127.0.0.1:{ws_port}",
        process=process,
        log_paths=[log_file] if not quiet else [],
        stdout_path=stdout_path,
    )


def _free_pair() -> tuple[int, int]:
    """A free port ``P`` whose neighbour ``P+1`` is free too.

    ``--registryAdvertisedHost host:client_port`` derives the peer port as
    ``client_port + 1`` (see ``_split_member``), so members co-located on one
    machine need consecutive pairs rather than two independent ports.
    """
    for _attempt in range(200):
        first = _free_port()
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", first + 1))
            except OSError:
                continue
        return first, first + 1
    raise SystemExit("could not find a consecutive free port pair")


def start_rust_dist(name: str, quiet: bool, *, members: int) -> Target:
    """Start the Rust registry against a bench etcd cluster.

    The Rust counterpart of ``start_python(name, members=N)``, and deliberately
    the same shape: the SAME etcd cluster helper, the same flags, the same
    observability switches. The comparison between them is only about the
    registry if everything underneath it is identical -- which is why the
    cluster is brought up by shared code rather than by a second copy here.

    ``--etcdExternal`` because the cluster is already running: the benchmark
    owns its lifetime, not the registry, and a managed member would make the
    Python and Rust targets differ in who spawns etcd as well as in what
    speaks to it.
    """
    if not RUST_REGISTRY.is_file():
        raise SystemExit(
            f"rust registry not built at {RUST_REGISTRY}\n"
            f"  (cd rust && cargo build --release -p nmos-registry-bin)\n"
            f"  or set NMOS_RUST_REGISTRY to a release binary"
        )

    registration_port, query_port, ws_port = (
        _free_port(), _free_port(), _free_port(),
    )
    log_file = WORK / f"{name}.log"
    stdout_path = WORK / f"{name}.out"

    endpoints, extra = _start_etcd_cluster(name, members)

    command = [
        str(RUST_REGISTRY),
        "--registryDisableTLS",
        "--registryAddr", "127.0.0.1",
        "--registrationPort", str(registration_port),
        "--queryPort", str(query_port),
        "--queryWebSocketPort", str(ws_port),
        "--logFile", "" if quiet else str(log_file),
        "--statusInterval", "0" if quiet else "5",
        "--distributed", "--distributedBackend", "etcd",
        "--etcdExternal", "--etcdDisableTLS",
        "--registryAdvertisedHost", "127.0.0.1",
        "--etcdEndpoints", endpoints,
        "--etcdNamespace", f"/bench/{name}",
    ]

    handle = stdout_path.open("wb")
    environment = dict(os.environ)
    if os.environ.get("NMOS_ETCD_FAST_PATH"):
        environment["NMOS_ETCD_FAST_PATH"] = os.environ["NMOS_ETCD_FAST_PATH"]
    if quiet:
        environment["NMOS_LOG_LEVEL"] = "WARNING"
    process = subprocess.Popen(
        command, cwd=str(REPO), stdout=handle, stderr=subprocess.STDOUT,
        env=environment,
    )

    registration = f"http://127.0.0.1:{registration_port}"
    query = f"http://127.0.0.1:{query_port}"
    if not _wait_http(f"{query}/x-nmos/query/v1.3/", timeout=90.0):
        process.kill()
        for child in extra:
            child.kill()
        raise SystemExit(f"{name} did not start; see {stdout_path}")

    return Target(
        name=name,
        registration=registration,
        query=query,
        websocket=f"ws://127.0.0.1:{ws_port}",
        process=process,
        log_paths=[log_file] if not quiet else [],
        stdout_path=stdout_path,
        extra=extra,
    )


def start_raft(name: str, quiet: bool, *, members: int) -> Target:
    """Start ``members`` registry processes forming one raft cluster.

    Structurally different from the etcd path, and the difference is the whole
    point of the backend: there, one registry is a *client* of a separate
    cluster of ``members`` etcd processes, so the benchmark starts 1 + N
    processes. Here the registries **are** the cluster, so it starts N -- and
    the load generator drives one of them, exactly as a Node would.

    That also means this measures the honest thing. A registration driven at
    member 0 is owned by member 0, which is what a real deployment looks like:
    a Node registers with one registry and stays there.
    """
    pairs = [_free_pair() for _ in range(members)]
    advertised = [f"127.0.0.1:{client}" for client, _peer in pairs]

    processes: list[subprocess.Popen[bytes]] = []
    fronts: list[tuple[int, int, int]] = []

    for index in range(members):
        registration_port, query_port, ws_port = (
            _free_port(), _free_port(), _free_port(),
        )
        fronts.append((registration_port, query_port, ws_port))
        state_dir = WORK / f"{name}-raft" / f"m{index}"
        if state_dir.exists():
            shutil.rmtree(state_dir)
        state_dir.mkdir(parents=True)

        command = [
            sys.executable, str(REPO / "nmos_registry.py"),
            "--registryDisableTLS",
            "--registryAddr", "127.0.0.1",
            "--registrationPort", str(registration_port),
            "--queryPort", str(query_port),
            "--queryWebSocketPort", str(ws_port),
            "--logFile", "" if quiet else str(WORK / f"{name}-m{index}.log"),
            "--statusInterval", "0" if quiet else "5",
            "--distributed",
            "--distributedBackend", "raft",
            "--raftDisableTLS",
            "--raftStateDir", str(state_dir),
            "--raftNamespace", f"/bench/{name}",
            "--registryAdvertisedHost", advertised[index],
        ]
        for other in advertised:
            if other != advertised[index]:
                command += ["--registryNeighbour", other]

        environment = dict(os.environ, PYTHONPATH=str(REPO))
        if quiet:
            environment["NMOS_LOG_LEVEL"] = "WARNING"
        stdout_path = WORK / f"{name}-m{index}.out"
        processes.append(subprocess.Popen(
            command, cwd=str(REPO),
            stdout=stdout_path.open("wb"), stderr=subprocess.STDOUT,
            env=environment,
        ))

    registration_port, query_port, ws_port = fronts[0]
    query = f"http://127.0.0.1:{query_port}"
    for _registration, member_query, _ws in fronts:
        if not _wait_http(f"http://127.0.0.1:{member_query}/x-nmos/query/v1.3/",
                          timeout=90.0):
            for process in processes:
                process.kill()
            raise SystemExit(f"{name} did not start; see {WORK}/{name}-m*.out")

    return Target(
        name=name,
        registration=f"http://127.0.0.1:{registration_port}",
        query=query,
        websocket=f"ws://127.0.0.1:{ws_port}",
        process=processes[0],
        log_paths=[],
        stdout_path=WORK / f"{name}-m0.out",
        extra=processes[1:],
    )


def start_rust_raft(name: str, quiet: bool, *, members: int) -> Target:
    """Start ``members`` **Rust** registries forming one consensus cluster.

    Deliberately the same shape as ``start_raft``: the same flags, the same
    member arithmetic, the same one-registry-per-member structure, and the load
    driven at member 0 so a registration is owned by the member that receives
    it. Only the executable differs.

    That is the whole reason this function exists rather than a parameter on
    ``start_raft``: the two command lines are asserted identical by
    ``cli_parity.rs``, so writing them out side by side is what makes a
    difference between them visible rather than a shared helper hiding one.
    """
    if not RUST_REGISTRY.is_file():
        raise SystemExit(
            f"rust registry not built at {RUST_REGISTRY}\n"
            f"  (cd rust && cargo build --release -p nmos-registry-bin)"
        )

    pairs = [_free_pair() for _ in range(members)]
    advertised = [f"127.0.0.1:{client}" for client, _peer in pairs]

    processes: list[subprocess.Popen[bytes]] = []
    fronts: list[tuple[int, int, int]] = []

    for index in range(members):
        registration_port, query_port, ws_port = (
            _free_port(), _free_port(), _free_port(),
        )
        fronts.append((registration_port, query_port, ws_port))
        state_dir = WORK / f"{name}-raft" / f"m{index}"
        if state_dir.exists():
            shutil.rmtree(state_dir)
        state_dir.mkdir(parents=True)

        command = [
            str(RUST_REGISTRY),
            "--registryDisableTLS",
            "--registryAddr", "127.0.0.1",
            "--registrationPort", str(registration_port),
            "--queryPort", str(query_port),
            "--queryWebSocketPort", str(ws_port),
            "--logFile", "" if quiet else str(WORK / f"{name}-m{index}.log"),
            "--statusInterval", "0" if quiet else "5",
            "--distributed",
            "--distributedBackend", "raft",
            "--raftDisableTLS",
            "--raftStateDir", str(state_dir),
            "--raftNamespace", f"/bench/{name}",
            "--registryAdvertisedHost", advertised[index],
        ]
        for other in advertised:
            if other != advertised[index]:
                command += ["--registryNeighbour", other]

        environment = dict(os.environ)
        if quiet:
            environment["NMOS_LOG_LEVEL"] = "WARNING"
        stdout_path = WORK / f"{name}-m{index}.out"
        processes.append(subprocess.Popen(
            command, cwd=str(REPO),
            stdout=stdout_path.open("wb"), stderr=subprocess.STDOUT,
            env=environment,
        ))

    registration_port, query_port, ws_port = fronts[0]
    for _registration, member_query, _ws in fronts:
        if not _wait_http(f"http://127.0.0.1:{member_query}/x-nmos/query/v1.3/",
                          timeout=90.0):
            for process in processes:
                process.kill()
            raise SystemExit(f"{name} did not start; see {WORK}/{name}-m*.out")

    return Target(
        name=name,
        registration=f"http://127.0.0.1:{registration_port}",
        query=f"http://127.0.0.1:{query_port}",
        websocket=f"ws://127.0.0.1:{ws_port}",
        process=processes[0],
        log_paths=[],
        stdout_path=WORK / f"{name}-m0.out",
        extra=processes[1:],
    )


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

TAXES = [
    ("python tax", "nmos-cpp", "standalone",
     "JSON, generated-type decode, event loop -- what a Rust port recovers"),
    # Read the same way as every other row: the ratio is the second column
    # divided by the first, so a value below 1.0 means the Rust registry was
    # faster. This is the row the whole port exists to move.
    ("rust recovery", "standalone", "rust",
     "the same registry in Rust -- multi-threaded, no interpreter"),
    ("etcd tax", "standalone", "dist1",
     "client, serialization and fence overhead, with no quorum involved"),
    ("etcd consensus tax", "dist1", "dist3",
     "Raft fsync and quorum breadth -- what resilience actually costs"),
    # The same two questions asked of the native backend, so the comparison is
    # between like measurements rather than between a ratio and a number. The
    # first row is the one the backend exists to shrink: no fence, no
    # read-before-write, no fsync, so a one-member cluster should cost almost
    # nothing over standalone.
    ("raft tax", "standalone", "raft1",
     "log, framing and apply -- no fence, no fsync, no quorum involved"),
    # The same three questions asked of the Rust cluster. The first is what a
    # deployment actually chooses between -- one Rust registry or a resilient
    # cluster of them -- and the second and third are what each added pair of
    # members costs.
    ("rust raft tax", "rust", "rustraft1",
     "consensus machinery on the Rust registry, with no quorum involved"),
    ("rust consensus tax (3)", "rustraft1", "rustraft3",
     "what tolerating one member failure costs"),
    ("rust consensus tax (5)", "rustraft3", "rustraft5",
     "what tolerating two costs on top of that"),
    ("raft consensus tax", "raft1", "raft3",
     "quorum breadth and a second apply, without the fsync"),
    # The etcd backend asked the same three questions as the raft one, so the
    # two storage layers are compared on like measurements. The first row is
    # the one a deployment choosing etcd actually pays.
    ("rust etcd tax", "rust", "rustdist1",
     "client, serialization and fence overhead on the Rust registry"),
    ("rust etcd consensus tax", "rustdist1", "rustdist3",
     "etcd's own Raft fsync and quorum breadth, under the Rust registry"),
    # And the port's own question, asked of the etcd backend: how much of the
    # Python etcd cost was the registry rather than the database?
    ("rust recovery (etcd 1)", "dist1", "rustdist1",
     "the same etcd backend in Rust, one member"),
    ("rust recovery (etcd 3)", "dist3", "rustdist3",
     "the same etcd backend in Rust, three members"),
]


def _report(results: dict[str, dict[str, Any]], log_bytes: dict[str, int],
            capacities: dict[str, dict[str, Any]] | None = None) -> None:
    print("\n" + "=" * 78)
    print("MATCHED OBSERVABILITY")
    print("=" * 78)
    for name, written in sorted(log_bytes.items()):
        print(f"  {name:34} {written:>12,} bytes written")

    # Zeros are NOT filtered out. A target writing nothing while another writes
    # 159 KB is the single worst mismatch this check exists to catch, and an
    # earlier version of it excluded zeros -- which made exactly that case
    # invisible and silently reported an unmatched run as comparable.
    values = list(log_bytes.values())
    if len(values) >= 2 and max(values) > 10 * max(min(values), 1):
        loudest = max(log_bytes, key=lambda name: log_bytes[name])
        quietest = min(log_bytes, key=lambda name: log_bytes[name])
        print(
            f"\n  *** NOT COMPARABLE ***\n"
            f"  {loudest} wrote {log_bytes[loudest]:,} bytes; "
            f"{quietest} wrote {log_bytes[quietest]:,}.\n"
            f"  These numbers measure logging as much as they measure "
            f"registries.\n"
            f"  Re-check logging_level / NMOS_LOG_LEVEL / --logFile before "
            f"trusting anything below.",
        )

    print("\n" + "=" * 78)
    print("PHASES  (p50 / p95, milliseconds)")
    print("=" * 78)

    phases = sorted({phase for run in results.values() for phase in run})
    header = f"{'phase':26}" + "".join(f"{name:>18}" for name in results)
    print(header)
    for phase in phases:
        row = f"{phase:26}"
        for name in results:
            entry = results[name].get(phase)
            row += (
                f"{entry['p50_ms']:>8.2f}/{entry['p95_ms']:<9.2f}"
                if entry else f"{'-':>18}"
            )
        print(row)

    print("\n" + "=" * 78)
    if capacities:
        print("\n" + "=" * 78)
        print("CAPACITY  (heartbeat, offered load from separate client processes)")
        print("=" * 78)
        print(
            "  The phase table above is one client's p50. A single client\n"
            "  saturates near the rate of the registries it measures, so on the\n"
            "  cheap endpoints it reports its own limit rather than theirs --\n"
            "  and the targets come out level or ranked backwards. These two\n"
            "  points show that directly: `1 client` is the regime above, and\n"
            "  `nproc` is as much load as this machine can offer without the\n"
            "  clients taking cores the server needs.\n",
        )
        print(f"  {'target':<14} {'1 client':>12} {'nproc':>12} "
              f"{'hidden':>8} {'CPU/req':>10}")
        for name, capacity in sorted(capacities.items()):
            print(
                f"  {name:<14} {capacity['single_client_rate']:>9.0f}/s "
                f"{capacity['peak_rate']:>9.0f}/s "
                f"{capacity['client_ceiling_factor']:>7.1f}x "
                f"{capacity['cpu_us_per_request']:>8.1f}us",
            )
        print(
            "\n  `hidden` is how much capacity the one-client column was\n"
            "  concealing. Near 1.0 means the client was not the limit and the\n"
            "  phase table is trustworthy for that target; a large value means\n"
            "  it was measuring itself.",
        )

    print("\n" + "=" * 78)
    print("THE TAXES  (p50 ratio, registration chain)")
    print("=" * 78)
    for label, faster, slower, why in TAXES:
        if faster not in results or slower not in results:
            continue
        # `amwa_scale` first: it is the published reference workload (2500
        # Nodes x 6 resources) and the one the CPU argument was always about.
        # The others are the small-scale chain, where every implementation
        # looks similar and the tax is easy to talk yourself out of.
        for phase in (
            "amwa_scale", "node_online", "cold_sender", "update_churn", "query",
        ):
            a = results[faster].get(phase)
            b = results[slower].get(phase)
            if not a or not b or a["p50_ms"] <= 0:
                continue
            print(
                f"  {label:16} {phase:16} "
                f"{a['p50_ms']:>7.2f} -> {b['p50_ms']:>7.2f} ms  "
                f"x{b['p50_ms'] / a['p50_ms']:.2f}",
            )
        print(f"    {why}")


async def main_async(args: argparse.Namespace) -> int:
    from bench_registry import loadgen

    WORK.mkdir(parents=True, exist_ok=True)
    if args.no_fast_path:
        os.environ["NMOS_ETCD_FAST_PATH"] = "0"
    wanted = [name.strip() for name in args.targets.split(",") if name.strip()]

    results: dict[str, dict[str, Any]] = {}
    log_bytes: dict[str, int] = {}
    capacities: dict[str, dict[str, Any]] = {}

    for name in wanted:
        target: Target | None = None
        try:
            if name == "cpp":
                target = start_nmos_cpp(args.quiet)
                key = "nmos-cpp"
            elif name == "standalone":
                target = start_python("standalone", args.quiet)
                key = "standalone"
            elif name == "rust":
                target = start_rust("rust", args.quiet)
                key = "rust"
            elif name.startswith("rustdist"):
                members = int(name[8:] or "1")
                target = start_rust_dist(name, args.quiet, members=members)
                key = name
            elif name.startswith("dist"):
                members = int(name[4:] or "1")
                target = start_python(name, args.quiet, members=members)
                key = name
            elif name.startswith("rustraft"):
                members = int(name[8:] or "1")
                target = start_rust_raft(name, args.quiet, members=members)
                key = name
            elif name.startswith("raft"):
                members = int(name[4:] or "1")
                target = start_raft(name, args.quiet, members=members)
                key = name
            else:
                print(f"unknown target {name!r}", file=sys.stderr)
                continue

            before = target.log_bytes()
            phase_args = loadgen.build_parser().parse_args([
                "--target", target.registration,
                "--query", target.query,
                "--ws", target.websocket,
                "--label", target.name,
                "--nodes", str(args.nodes),
                "--devices", str(args.devices),
                "--pairs", str(args.pairs),
                "--rounds", str(args.rounds),
                "--concurrency", str(args.concurrency),
                "--query-iterations", str(args.query_iterations),
                "--subscribers", str(args.subscribers),
                "--fanout-updates", str(args.fanout_updates),
                "--amwa-nodes", str(args.amwa_nodes),
                "--soak-seconds", str(args.soak_seconds),
                "--soak-nodes", str(args.soak_nodes),
            ])
            results[key] = await loadgen.run(phase_args)
            log_bytes[target.name] = target.log_bytes() - before

            # After the phases, deliberately, for two reasons. The probe has to
            # register a Node of its own to heartbeat, and doing that first
            # would put an extra resource into every collection the phases then
            # query. And a registry that has just absorbed the workload is the
            # more representative thing to measure -- the heartbeat cost is
            # independent of registry size (1.4us flat from 60 to 15,000
            # resources), but nothing else about the process is.
            if args.capacity:
                from bench_registry import capacity as capacity_probe
                print(f"\n{target.name}: heartbeat capacity")
                capacities[key] = capacity_probe.measure(
                    target.registration,
                    pid=target.process.pid if target.process else None,
                )
        finally:
            if target is not None:
                target.stop()

    _report(results, log_bytes, capacities)

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "log_bytes": log_bytes,
                    "results": results,
                    # Recorded whether or not it was measured, so a consumer can
                    # tell "the probe was skipped" from "the probe found
                    # nothing" -- an absent key and a zero mean different things.
                    "capacity": capacities,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nwrote {args.json}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare this registry against nmos-cpp",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--targets", default="cpp,standalone,dist1,dist3")
    parser.add_argument("--quiet", action="store_true", default=True,
                        help="Matched minimal logging (the headline numbers)")
    parser.add_argument("--default-logging", dest="quiet", action="store_false",
                        help="Matched as-shipped logging")
    parser.add_argument("--nodes", type=int, default=10)
    parser.add_argument("--devices", type=int, default=2)
    parser.add_argument("--pairs", type=int, default=5)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--query-iterations", type=int, default=200)
    parser.add_argument("--subscribers", type=int, default=0,
                        help="Query WebSocket subscribers for the status "
                             "fan-out phase (0 disables)")
    parser.add_argument("--fanout-updates", type=int, default=20)
    parser.add_argument("--amwa-nodes", type=int, default=0,
                        help="Nodes for the AMWA six-resource scale phase "
                             "(published reference: 2500)")
    parser.add_argument("--soak-seconds", type=float, default=0.0)
    parser.add_argument("--soak-nodes", type=int, default=0)
    parser.add_argument("--no-fast-path", action="store_true",
                        help="Force every mutation down the fenced path, to "
                             "measure what the §10.2.1 fast path is worth")
    parser.add_argument("--capacity", action="store_true", default=True,
                        help="Also measure heartbeat capacity at 1 and nproc "
                             "clients, which is what the one-client phase "
                             "table cannot show")
    parser.add_argument("--no-capacity", dest="capacity", action="store_false",
                        help="Skip the capacity probe")
    parser.add_argument("--json", default="")
    return parser


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    sys.exit(main())
