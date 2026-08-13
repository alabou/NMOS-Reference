#!/usr/bin/env python3
# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Bring a local etcd cluster up and down for the distributed registry.

    python3 etcd_cluster.py up [--members 1|3|5] [--profile wsl|linux] [--bootstrap]
    python3 etcd_cluster.py status
    python3 etcd_cluster.py endpoints
    python3 etcd_cluster.py down
    python3 etcd_cluster.py wipe --yes

Why this is Python and not a shell script
-----------------------------------------
It imports ``nmos/etcd/cluster.py`` and ``nmos/etcd/supervisor.py`` and so
derives member names, the cluster token, ``--initial-cluster`` and every URL
from **the same code the registry uses**. A hand-maintained shell script is a
second implementation of the topology that can disagree with the first, and
that is exactly what happened to the scripts this replaces: ``rm -f -r`` on the
data directory and ``member remove`` / ``member add`` on every single start,
against a binary pinned two major versions behind what the design needed.

Profiles
--------
``linux``  Each member binds its own loopback address (127.0.0.11/12/13) and
           they all share the standard ports.
``wsl``    Every member binds ``127.0.0.1`` and they differ by port block
           (2381/2382, 2391/2392, 2401/2402).

The ``wsl`` profile exists for a specific reason: WSL2 forwards Windows
``localhost`` to the distribution's loopback, but it forwards **127.0.0.1
only**. Members bound to 127.0.0.11 inside WSL are unreachable from a
native-Windows registry, so they have to share one address and separate by port.
Peer traffic stays inside the distribution, so nothing crosses the NAT boundary
and no ``netsh portproxy`` is needed.

Data
----
Data directories live in the repo-local, git-ignored ``.etcd/data/<member>/``.
``up`` **never** deletes one. The first run on an empty directory bootstraps and
leaves a marker; every later run joins as ``existing``. ``wipe`` is a separate
subcommand that names what it will delete and requires ``--yes``. Not repeating
the old scripts' ``rm -f -r`` is the entire point.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import signal
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_ROOT = REPO_ROOT / ".etcd" / "data"
BUNDLED_ETCD = REPO_ROOT / ".etcd" / "etcd"

# Records that a member's data directory has been initialised, so a second
# `up` joins as `existing` instead of bootstrapping a second cluster on top of
# an existing one.
BOOTSTRAP_MARKER = ".nmos-bootstrapped"

NAMESPACE = "/nmos-reference/registry/v1"

PROFILE_LINUX = "linux"
PROFILE_WSL = "wsl"

# Port blocks for the wsl profile: base + 10 per member, client then peer.
_WSL_PORT_STRIDE = 10
_WSL_BASE_CLIENT = 2381
_WSL_BASE_PEER = 2382


def _default_profile() -> str:
    """Pick a profile from the environment, preferring correctness on WSL.

    Detection is only ever a *default*; --profile overrides it. This is not the
    platform gate from the registry (that is a single sys.platform check with
    no heuristics) -- it is a convenience for the test rig, where guessing
    wrong costs a flag rather than a wrong deployment.
    """
    try:
        release = Path("/proc/sys/kernel/osrelease").read_text()
    except OSError:
        return PROFILE_LINUX
    return PROFILE_WSL if "microsoft" in release.lower() else PROFILE_LINUX


def _member_specs(count: int, profile: str) -> list[object]:
    from nmos.etcd.cluster import MemberSpec

    specs: list[object] = []
    for index in range(count):
        if profile == PROFILE_WSL:
            specs.append(MemberSpec(
                host="127.0.0.1",
                client_port=_WSL_BASE_CLIENT + index * _WSL_PORT_STRIDE,
                peer_port=_WSL_BASE_PEER + index * _WSL_PORT_STRIDE,
                name=f"nmos-registry-{index}",
                bind_address="127.0.0.1",
            ))
        else:
            address = f"127.0.0.{11 + index}"
            specs.append(MemberSpec(
                host=address,
                name=f"nmos-registry-{index}",
                bind_address=address,
            ))
    return specs


def _layouts(count: int, profile: str) -> list[object]:
    """One layout per member -- identical membership, differing only in `local`."""
    from nmos.etcd.cluster import derive_cluster

    specs = _member_specs(count, profile)
    layouts = []
    for spec in specs:
        layouts.append(derive_cluster(
            specs,                      # type: ignore[arg-type]
            local_host=spec.host,       # type: ignore[attr-defined]
            local_peer_port=spec.peer_port,  # type: ignore[attr-defined]
            namespace=NAMESPACE,
            tls=False,
        ))
    return layouts


def _binary(explicit: str | None) -> str:
    if explicit:
        return explicit
    if BUNDLED_ETCD.is_file():
        return str(BUNDLED_ETCD)
    found = shutil.which("etcd")
    if found is None:
        raise SystemExit(
            "etcd not found. Install it with ./install-etcd.sh, or pass "
            "--binary.",
        )
    return found


def _data_dir(root: Path, name: str) -> Path:
    return root / name


# ---------------------------------------------------------------------------
# up
# ---------------------------------------------------------------------------

async def _up(args: argparse.Namespace) -> int:
    from nmos.etcd.supervisor import EtcdSupervisor, SupervisorError

    if sys.platform == "win32":
        raise SystemExit(
            "etcd_cluster.py runs the etcd SERVER, which this project runs on "
            "POSIX only (etcd rates windows/amd64 Tier 3: unstable, "
            "unmaintained). Run this inside WSL, then point a native-Windows "
            "registry at it with --etcdExternal --etcdEndpoints.",
        )

    binary = _binary(args.binary)
    root = Path(args.data_root)
    root.mkdir(parents=True, exist_ok=True)

    layouts = _layouts(args.members, args.profile)
    supervisors: list[EtcdSupervisor] = []

    for layout in layouts:
        local = layout.local  # type: ignore[attr-defined]
        data_dir = _data_dir(root, local.name)
        marker = data_dir / BOOTSTRAP_MARKER

        # Bootstrap only the first time. A second `up` on an initialised
        # directory joins as `existing`; bootstrapping again would fork the
        # cluster, which is the single most damaging thing a rig script can do.
        bootstrap = args.bootstrap or not marker.exists()
        if bootstrap and marker.exists():
            raise SystemExit(
                f"--bootstrap was given but {data_dir} is already "
                f"initialised. Use `wipe --yes` first if you really mean to "
                f"discard it.",
            )

        supervisors.append(EtcdSupervisor(
            layout=layout,  # type: ignore[arg-type]
            binary=binary,
            data_dir=data_dir,
            bootstrap=bootstrap,
            tls=False,
            startup_timeout=args.timeout,
        ))

    print(
        f"Starting {args.members} member(s), profile {args.profile}, "
        f"data root {root}",
    )

    started: list[EtcdSupervisor] = []
    try:
        # Started concurrently on purpose: with initial-cluster-state=new every
        # member blocks until it can reach a quorum of peers, so starting them
        # one at a time and waiting for each to be ready would deadlock on the
        # first.
        results = await asyncio.gather(
            *(supervisor.start() for supervisor in supervisors),
            return_exceptions=True,
        )
        for supervisor, result in zip(supervisors, results):
            if isinstance(result, BaseException):
                raise result
            started.append(supervisor)
            marker = supervisor.data_dir / BOOTSTRAP_MARKER
            marker.touch(exist_ok=True)
    except (SupervisorError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        for supervisor in started:
            await supervisor.stop()
        return 1

    for layout in layouts:
        local = layout.local  # type: ignore[attr-defined]
        print(f"  {local.name:24} client {local.client_target}")

    print("\nPoint a registry at it with:\n")
    print(f"  --distributed --etcdExternal --etcdDisableTLS \\")
    print(f"  --etcdEndpoints {_endpoint_string(layouts[0], args.profile)}")

    print("\nRunning in the foreground. Ctrl-C stops every member.")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))

    await stop.wait()
    print("\nStopping...")
    for supervisor in reversed(started):
        await supervisor.stop()
    return 0


def _endpoint_string(layout: object, profile: str) -> str:
    """Endpoints as a registry should be given them.

    On the wsl profile the addresses are rewritten to ``localhost`` because
    that is how a native-Windows registry reaches them -- through WSL2's
    localhost forwarding, which covers 127.0.0.1 only.
    """
    members = layout.members  # type: ignore[attr-defined]
    if profile == PROFILE_WSL:
        return ",".join(f"localhost:{m.client_port}" for m in members)
    return ",".join(m.client_target for m in members)


# ---------------------------------------------------------------------------
# status / endpoints
# ---------------------------------------------------------------------------

async def _status(args: argparse.Namespace) -> int:
    """Report cluster health. A pure client operation.

    Works unchanged from native Windows against a cluster running in WSL2,
    which is what keeps the split rig diagnosable from the side the operator is
    sitting on.
    """
    from nmos.etcd.channel import EtcdChannelPool, parse_endpoints, unary_method
    from nmos.etcd.errors import EtcdError
    from nmos.etcd.generated import rpc_pb2

    status_rpc = unary_method(
        "Maintenance", "Status", rpc_pb2.StatusRequest, rpc_pb2.StatusResponse,
    )
    member_list = unary_method(
        "Cluster", "MemberList",
        rpc_pb2.MemberListRequest, rpc_pb2.MemberListResponse,
    )

    layouts = _layouts(args.members, args.profile)
    # `endpoints` only exists on the `status` subparser; --detach reuses this
    # function with an `up` namespace to poll for readiness.
    explicit = getattr(args, "endpoints", "")
    targets = (
        explicit.split(",") if explicit
        else [m.client_target for m in layouts[0].members]  # type: ignore[attr-defined]
    )

    healthy = 0
    for target in targets:
        pool = EtcdChannelPool(
            parse_endpoints([target]),
            credentials=None, target_name=None, rpc_timeout=3.0,
        )
        try:
            status = await pool.call(status_rpc, rpc_pb2.StatusRequest())
            leader = "leader" if status.leader == status.header.member_id else "follower"
            print(
                f"  {target:24} healthy  v{status.version}  "
                f"rev {status.header.revision}  {leader}",
            )
            healthy += 1
        except EtcdError as exc:
            print(f"  {target:24} UNREACHABLE  {exc}")
        finally:
            await pool.close()

    if healthy:
        pool = EtcdChannelPool(
            parse_endpoints(list(targets)),
            credentials=None, target_name=None, rpc_timeout=3.0,
        )
        try:
            members = await pool.call(member_list, rpc_pb2.MemberListRequest())
            print(f"\n  cluster members: {len(members.members)}")
            for member in members.members:
                print(f"    {member.name:24} {list(member.clientURLs)}")
        finally:
            await pool.close()

    quorum = len(targets) // 2 + 1
    print(f"\n  {healthy}/{len(targets)} healthy, quorum needs {quorum}")
    return 0 if healthy >= quorum else 1


def _endpoints(args: argparse.Namespace) -> int:
    layouts = _layouts(args.members, args.profile)
    print(_endpoint_string(layouts[0], args.profile))
    return 0


# ---------------------------------------------------------------------------
# down / wipe
# ---------------------------------------------------------------------------

def _down(args: argparse.Namespace) -> int:
    """Stop a cluster started by `up --detach`.

    Signals the detached *supervisor*, not the etcd processes. Killing the
    members directly would be worse than useless: the supervisor treats an
    exited child as a crash and restarts it with backoff, so the cluster would
    come straight back. Stopping the supervisor makes it stop its own children
    through the ordinary ownership path -- stop what you started.

    Deliberately does not touch data directories. That is `wipe`, and the
    separation is the whole difference between this and the scripts it
    replaces.
    """
    import socket
    import subprocess
    import time

    result = subprocess.run(
        ["pkill", "-f", r"etcd_cluster\.py .*\bup\b"], capture_output=True,
    )
    if result.returncode != 0:
        print("  no detached cluster supervisor found")
    else:
        print("  signalled the cluster supervisor")

    layouts = _layouts(args.members, args.profile)
    members = layouts[0].members  # type: ignore[attr-defined]

    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        still_up = []
        for member in members:
            with socket.socket() as probe:
                probe.settimeout(0.25)
                if probe.connect_ex(
                    (member.bind_address, member.client_port),
                ) == 0:
                    still_up.append(member)
        if not still_up:
            print("  all members stopped")
            return 0
        time.sleep(0.5)

    print(
        "  warning: some members are still listening. They may have been "
        "started outside this script, in which case stopping them is not "
        "this script's business.",
    )
    return 1


def _wipe(args: argparse.Namespace) -> int:
    """Delete every member's data directory. Requires --yes.

    Separate, explicit and confirming, because this is the operation the old
    scripts performed silently on every start.
    """
    root = Path(args.data_root)
    layouts = _layouts(args.members, args.profile)
    targets = [
        _data_dir(root, layout.local.name)  # type: ignore[attr-defined]
        for layout in layouts
    ]
    present = [path for path in targets if path.exists()]

    if not present:
        print("nothing to wipe")
        return 0

    print("This will PERMANENTLY DELETE:")
    for path in present:
        print(f"  {path}")

    if not args.yes:
        print("\nRefusing without --yes.")
        return 1

    for path in present:
        shutil.rmtree(path)
        print(f"  deleted {path}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a local etcd cluster for the distributed registry",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--members", type=int, default=3, choices=[1, 3, 5])
    parser.add_argument(
        "--profile", default=_default_profile(),
        choices=[PROFILE_LINUX, PROFILE_WSL],
        help="linux: one loopback address per member. wsl: one address, one "
             "port block per member, so Windows localhost forwarding reaches "
             "all of them.",
    )
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    parser.add_argument("--binary", default="")

    sub = parser.add_subparsers(dest="command", required=True)

    up = sub.add_parser("up", help="start the cluster")
    up.add_argument("--bootstrap", action="store_true",
                    help="force a one-time bootstrap (refused if already "
                         "initialised)")
    up.add_argument("--detach", action="store_true",
                    help="return instead of holding the members in the "
                         "foreground")
    up.add_argument("--timeout", type=float, default=60.0)

    status = sub.add_parser("status", help="report member health")
    status.add_argument("--endpoints", default="")

    sub.add_parser("endpoints", help="print the --etcdEndpoints line")
    sub.add_parser("down", help="stop members started by `up --detach`")

    wipe = sub.add_parser("wipe", help="delete every member's data directory")
    wipe.add_argument("--yes", action="store_true", required=False)

    return parser


def _spawn_detached(argv: list[str]) -> int:
    """Run `up` in a new session, then wait for the cluster to become healthy.

    Detaching cannot be done by simply returning from an async `up`: asyncio's
    BaseSubprocessTransport.close() kills any still-running child when the
    event loop tears down, so every member would die the moment the command
    exited. (It does -- that was the first thing this rig got wrong.)

    So `--detach` re-executes this script *without* the flag in a new session.
    The detached copy holds the members in the foreground exactly as an
    interactive run would, which keeps one code path for supervision and one
    owner for the processes: kill that copy and its members stop with it.
    """
    import subprocess
    import time

    forwarded = [arg for arg in argv if arg != "--detach"]
    command = [sys.executable, str(Path(__file__).resolve()), *forwarded]

    child = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    parser = build_parser()
    args = parser.parse_args(forwarded)
    deadline = time.monotonic() + args.timeout

    while time.monotonic() < deadline:
        if child.poll() is not None:
            print(
                f"error: detached cluster exited with {child.returncode}; "
                f"re-run without --detach to see why",
                file=sys.stderr,
            )
            return 1
        if asyncio.run(_status(args)) == 0:
            print(f"\nDetached (pid {child.pid}). Stop it with `down`.")
            return 0
        time.sleep(1.0)

    child.terminate()
    print("error: cluster did not become healthy", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(raw)

    if args.command == "up":
        if args.detach:
            return _spawn_detached(raw)
        return asyncio.run(_up(args))
    if args.command == "status":
        return asyncio.run(_status(args))
    if args.command == "endpoints":
        return _endpoints(args)
    if args.command == "down":
        return _down(args)
    if args.command == "wipe":
        return _wipe(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
