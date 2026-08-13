#!/usr/bin/env python3
# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Run the load generator across every target and separate the three taxes.

    python3 bench_registry/compare.py --quiet
    python3 bench_registry/compare.py --targets cpp,standalone,dist1,dist3

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
A single side-by-side number would conflate three unrelated costs::

    nmos-cpp      -> Python standalone    the PYTHON tax     (what Rust recovers)
    standalone    -> distributed 1        the ETCD tax       (no quorum involved)
    distributed 1 -> 3 -> 5               the CONSENSUS tax  (what resilience costs)

The 1-member distributed configuration exists purely to split the last two,
which are otherwise indistinguishable.
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
        cluster_ports = [(_free_port(), _free_port()) for _ in range(members)]
        endpoints = ",".join(f"127.0.0.1:{c}" for c, _ in cluster_ports)
        # Durability knobs, env-only because they exist to answer one
        # question -- "how much of the etcd tax is the disk?" -- and must
        # never be reachable from a normal run.
        #
        #   NMOS_BENCH_ETCD_DATA_ROOT=/dev/shm/...  put the data dir on tmpfs,
        #       so the WAL and bbolt file never reach a block device. This is
        #       the closest thing etcd has to "memory only": there is no
        #       in-memory backend, the storage engine is always bbolt + WAL.
        #   NMOS_BENCH_ETCD_NO_FSYNC=1              pass --unsafe-no-fsync,
        #       which etcd documents as "unsafe, will cause data loss". It
        #       isolates the fsync SYSCALL from the write itself.
        #
        # Neither is a supported deployment option. A registry whose etcd
        # loses its WAL on power failure has no authoritative state to recover
        # from, which is the one thing adopting etcd was meant to provide.
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

        initial = ",".join(
            f"m{i}=http://127.0.0.1:{p}" for i, (_c, p) in enumerate(cluster_ports)
        )
        for index, (client, peer) in enumerate(cluster_ports):
            extra.append(subprocess.Popen(
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
                for process in extra:
                    process.kill()
                raise SystemExit("bench etcd cluster did not start")

        command += [
            "--distributed", "--etcdExternal", "--etcdDisableTLS",
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


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

TAXES = [
    ("python tax", "nmos-cpp", "standalone",
     "JSON, generated-type decode, event loop -- what a Rust port recovers"),
    ("etcd tax", "standalone", "dist1",
     "client, serialization and fence overhead, with no quorum involved"),
    ("consensus tax", "dist1", "dist3",
     "Raft fsync and quorum breadth -- what resilience actually costs"),
]


def _report(results: dict[str, dict[str, Any]], log_bytes: dict[str, int]) -> None:
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
    print("THE THREE TAXES  (p50 ratio, registration chain)")
    print("=" * 78)
    for label, faster, slower, why in TAXES:
        if faster not in results or slower not in results:
            continue
        for phase in ("node_online", "cold_sender", "update_churn", "query"):
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

    for name in wanted:
        target: Target | None = None
        try:
            if name == "cpp":
                target = start_nmos_cpp(args.quiet)
                key = "nmos-cpp"
            elif name == "standalone":
                target = start_python("standalone", args.quiet)
                key = "standalone"
            elif name.startswith("dist"):
                members = int(name[4:] or "1")
                target = start_python(name, args.quiet, members=members)
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
        finally:
            if target is not None:
                target.stop()

    _report(results, log_bytes)

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {"log_bytes": log_bytes, "results": results}, indent=2,
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
    parser.add_argument("--json", default="")
    return parser


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    sys.exit(main())
