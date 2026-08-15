#!/usr/bin/env python3
# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Bring a local etcd cluster up and down for the distributed registry.

    python3 etcd_cluster.py up [--members 1|3|5] [--profile wsl|linux] [--bootstrap]
    python3 etcd_cluster.py --secure up            # mutual TLS, client and peer
    python3 etcd_cluster.py status
    python3 etcd_cluster.py endpoints
    python3 etcd_cluster.py down
    python3 etcd_cluster.py wipe --yes

``--secure`` is a top-level flag rather than an ``up`` flag on purpose: every
subcommand needs it. ``status`` has to present a client certificate to a
secured cluster, ``endpoints`` prints https-reachable names, and ``wipe`` has to
find the secured data root. Being top-level also means ``--detach`` forwards it
for free, since that re-executes this script with its own argv minus the flag.

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
import os
import shutil
import signal
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_ROOT = REPO_ROOT / ".etcd" / "data"
BUNDLED_ETCD = REPO_ROOT / ".etcd" / "etcd"

# A secured cluster advertises its members by DNS name where a plaintext one
# advertises loopback addresses (see _member_specs), so the two describe the
# same member with different peer URLs. etcd records a member's peer URL in its
# WAL, so re-using one data directory across the two modes makes the second run
# fail with a peer-URL mismatch on a database that is otherwise fine. Separate
# roots keep the modes independent and make `wipe` mode-specific too.
SECURE_DATA_ROOT = REPO_ROOT / ".etcd" / "data-secure"

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

# --- secured cluster identities -------------------------------------------
#
# A secured member is addressed by the name its certificate attests, because
# the shipped etcd certificates carry DNS SANs and no IP SAN. Two separate
# checks depend on that name, and only the first is obvious:
#
#   1. The member DIALLING a peer verifies the certificate it gets back against
#      the host in the peer URL. A name in the certificate satisfies this; a
#      bare loopback address does not.
#
#   2. The member being dialled verifies the certificate the caller PRESENTS
#      against the address the connection arrives from -- resolving each DNS
#      name in that certificate and looking for the source address among the
#      results.
#
# Check 2 is why every member shares one bind address and separates by port
# block instead of taking a loopback address each. Connections between
# addresses on `lo` are sourced from 127.0.0.1 whatever the destination
# (`ip route get 127.0.0.11` -> `src 127.0.0.1`), so per-member addresses make
# the source address match no member's name and every peer handshake is
# rejected. One address that every member name resolves to satisfies both
# checks with no certificate change and nothing disabled.
#
# The same shape deploys across machines unchanged: there each name resolves to
# its own host's address, which is also the source address of that host's peer
# traffic, so the ports need not differ. (It breaks only where the source
# address cannot match the advertised one -- behind NAT, or on a multi-homed
# host that routes out of a different interface.)
_SECURE_HOST_TEMPLATE = "XYZ-SNX1000{index}"
_SECURE_BIND_ADDRESS = "127.0.0.1"
_SECURE_SERIAL_TEMPLATE = "SNX1000{index}"

# Certificate flavour, spelled the way start-registry.sh spells it so one rig
# uses one vocabulary. Both flavours ship for every serial.
TCT_RSA = 0
TCT_ECDSA = 1
_TCT_INFIX = {TCT_RSA: "", TCT_ECDSA: ".ec"}

# Probe used to recognise a usable Certificates/ tree, mirroring the resolution
# start-registry.sh performs: IPMX_CERT_ROOT, then this checkout, then the
# workspace tree one level up.
_CERT_PROBE = "build.0.etcd/pem/ExampleDeviceServer.ABC.SNX10000.etcd.chain.pem"


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


def _cert_root() -> Path:
    """Locate a Certificates/ tree carrying the etcd identities.

    Same resolution order as start-registry.sh -- IPMX_CERT_ROOT, this
    checkout, then the workspace tree one level up -- so a rig driven from
    outside this repository finds the same PKI both halves of it use. Matching
    nothing anywhere names every directory searched rather than failing later
    inside etcd with a path it cannot read.
    """
    override = os.environ.get("IPMX_CERT_ROOT", "")
    if override:
        return Path(override)

    searched = [REPO_ROOT / "Certificates", REPO_ROOT.parent / "Certificates"]
    for candidate in searched:
        if (candidate / _CERT_PROBE).is_file():
            return candidate

    raise SystemExit(
        f"--secure needs the etcd certificate set, and {_CERT_PROBE} is in "
        f"none of the trees searched:\n"
        + "".join(f"  {path}\n" for path in searched)
        + "  Set IPMX_CERT_ROOT to a Certificates/ tree that carries it.",
    )


def _secure_identity(index: int, tct: int) -> tuple[str, str, str]:
    """The (certificate, key, trusted root CA) triple for one secured member.

    One certificate per member serves every role it has -- etcd's client
    listener, its peer listener, its outbound peer connections, and the
    registry's own client channel -- which is what the dual serverAuth,
    clientAuth EKU on these certificates is for.
    """
    root = _cert_root()
    infix = _TCT_INFIX[tct]
    serial = _SECURE_SERIAL_TEMPLATE.format(index=index)
    stem = f"ExampleDeviceServer.ABC.{serial}.etcd"

    certificate = root / "build.0.etcd" / "pem" / f"{stem}{infix}.chain.pem"
    key = root / "build.0.etcd" / "key" / f"{stem}{infix}.key"

    # One file holding both generations of the root CA, so either certificate
    # flavour validates against a single trust anchor. The supervisor would
    # combine separate roots itself, but handing it one file that already is
    # the trust store keeps what etcd receives identical to what is on disk.
    bundle = root / "build.0" / "ExampleRootCA-bundle.pem"
    flavoured = root / "build.0" / f"ExampleRootCA{infix}.pem"
    ca = bundle if bundle.is_file() else flavoured

    for role, path in (
        ("certificate", certificate), ("key", key), ("trusted root CA", ca),
    ):
        if not path.is_file():
            raise SystemExit(
                f"--secure: member {index}'s {role} is missing: {path}",
            )

    return str(certificate), str(key), str(ca)


def _check_secure_resolution(layouts: list[object]) -> None:
    """Refuse to start a secured cluster whose member names resolve elsewhere.

    Without this the misconfiguration surfaces as etcd rejecting every peer
    handshake with a TLS error naming a certificate that is in fact perfectly
    valid -- an hour of reading handshake logs to discover a hosts-file entry.
    Both checks in _SECURE_HOST_TEMPLATE depend on this one fact, so it is
    worth one getaddrinfo per member at start-up.
    """
    import socket

    wrong: list[str] = []
    for layout in layouts:
        member = layout.local  # type: ignore[attr-defined]
        try:
            resolved: set[str] = {
                str(info[4][0])
                for info in socket.getaddrinfo(
                    member.host, member.client_port, socket.AF_INET,
                )
            }
        except OSError as exc:
            wrong.append(f"  {member.host}: does not resolve ({exc.strerror})")
            continue
        if member.bind_address not in resolved:
            wrong.append(
                f"  {member.host}: resolves to {', '.join(sorted(resolved))}, "
                f"but the member listens on {member.bind_address}",
            )

    if wrong:
        raise SystemExit(
            "--secure: member names must resolve to the address the members "
            "listen on.\n"
            + "\n".join(wrong)
            + f"\n  Map every member name to {_SECURE_BIND_ADDRESS} in "
            f"/etc/hosts (and in the Windows hosts file, if a registry there "
            f"drives this cluster).",
        )


def _member_specs(count: int, profile: str, secure: bool = False) -> list[object]:
    from nmos.etcd.cluster import MemberSpec

    if secure:
        # Addressed by name and separated by port, all on one bind address --
        # see _SECURE_HOST_TEMPLATE for why the addresses cannot differ. The
        # port block is the wsl profile's, so a secured cluster is reachable
        # from a native-Windows registry through localhost forwarding too.
        return [
            MemberSpec(
                host=_SECURE_HOST_TEMPLATE.format(index=index),
                client_port=_WSL_BASE_CLIENT + index * _WSL_PORT_STRIDE,
                peer_port=_WSL_BASE_PEER + index * _WSL_PORT_STRIDE,
                name=f"nmos-registry-{index}",
                bind_address=_SECURE_BIND_ADDRESS,
            )
            for index in range(count)
        ]

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


def _layouts(count: int, profile: str, secure: bool = False) -> list[object]:
    """One layout per member -- identical membership, differing only in `local`.

    Every subcommand routes through here, which makes it the one place that has
    to agree with itself about what the cluster looks like -- including whether
    its URLs are https.
    """
    from nmos.etcd.cluster import derive_cluster

    specs = _member_specs(count, profile, secure)
    layouts: list[object] = []
    for spec in specs:
        layouts.append(derive_cluster(
            specs,                      # type: ignore[arg-type]
            local_host=spec.host,       # type: ignore[attr-defined]
            local_peer_port=spec.peer_port,  # type: ignore[attr-defined]
            namespace=NAMESPACE,
            tls=secure,
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


def _data_root(args: argparse.Namespace) -> Path:
    """Where this mode's member databases live.

    An explicit --data-root always wins. Otherwise secured and plaintext
    clusters get separate roots, for the peer-URL reason SECURE_DATA_ROOT
    documents.
    """
    if args.data_root:
        return Path(args.data_root)
    return SECURE_DATA_ROOT if args.secure else DEFAULT_DATA_ROOT


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

    from nmos.etcd.cluster import DEFAULT_ETCD_CERTIFICATE_NAME

    binary = _binary(args.binary)
    root = _data_root(args)
    root.mkdir(parents=True, exist_ok=True)

    layouts = _layouts(args.members, args.profile, args.secure)
    if args.secure:
        _check_secure_resolution(layouts)
    supervisors: list[EtcdSupervisor] = []

    for index, layout in enumerate(layouts):
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

        certificate, key, trusted_ca = (
            _secure_identity(index, args.tct) if args.secure else ("", "", "")
        )

        supervisors.append(EtcdSupervisor(
            layout=layout,  # type: ignore[arg-type]
            binary=binary,
            data_dir=data_dir,
            bootstrap=bootstrap,
            tls=args.secure,
            certificate=certificate or None,
            key=key or None,
            trusted_root_ca=(trusted_ca,) if trusted_ca else (),
            certificate_name=(
                DEFAULT_ETCD_CERTIFICATE_NAME if args.secure else None
            ),
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
    if args.secure:
        certificate, key, trusted_ca = _secure_identity(0, args.tct)
        print("  --distributed --etcdExternal \\")
        print(f"  --etcdCertificate {certificate} \\")
        print(f"  --etcdKey {key} \\")
        print(f"  --etcdTrustedRootCA {trusted_ca} \\")
        print(f"  --etcdEndpoints {_endpoint_string(layouts[0], args.profile, args.secure)}")
        print(
            "\n  (member 0's identity shown; each registry member passes its "
            "own, matching the etcd member it talks to)",
        )
    else:
        print("  --distributed --etcdExternal --etcdDisableTLS \\")
        print(f"  --etcdEndpoints {_endpoint_string(layouts[0], args.profile, args.secure)}")

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


def _endpoint_string(layout: object, profile: str, secure: bool = False) -> str:
    """Endpoints as a registry should be given them.

    On the wsl profile the addresses are rewritten to ``localhost`` because
    that is how a native-Windows registry reaches them -- through WSL2's
    localhost forwarding, which covers 127.0.0.1 only.

    A secured cluster keeps its member names on every profile. They already
    resolve to the one address the members share, which is what makes them
    reachable from Windows too, and naming them keeps the endpoint line
    readable against the certificate each member presents.
    """
    members = layout.members  # type: ignore[attr-defined]
    if secure:
        return ",".join(m.client_target for m in members)
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
    from nmos.etcd.channel import (
        EtcdChannelPool,
        build_credentials,
        parse_endpoints,
        unary_method,
    )
    from nmos.etcd.cluster import DEFAULT_ETCD_CERTIFICATE_NAME
    from nmos.etcd.errors import EtcdError
    from nmos.etcd.generated import rpc_pb2

    # A secured cluster answers nobody who cannot present a client certificate
    # carrying the shared etcd SAN, so `status` has to authenticate exactly as
    # a registry member does. Any member's identity serves: the certificates
    # differ per serial, but the SAN etcd checks is the same on all of them.
    credentials = None
    target_name = None
    if args.secure:
        certificate, key, trusted_ca = _secure_identity(0, args.tct)
        credentials = build_credentials(
            trusted_root_ca=[trusted_ca], certificate=certificate, key=key,
        )
        target_name = DEFAULT_ETCD_CERTIFICATE_NAME

    status_rpc = unary_method(
        "Maintenance", "Status", rpc_pb2.StatusRequest, rpc_pb2.StatusResponse,
    )
    member_list = unary_method(
        "Cluster", "MemberList",
        rpc_pb2.MemberListRequest, rpc_pb2.MemberListResponse,
    )

    layouts = _layouts(args.members, args.profile, args.secure)
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
            credentials=credentials, target_name=target_name, rpc_timeout=3.0,
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
            credentials=credentials, target_name=target_name, rpc_timeout=3.0,
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
    layouts = _layouts(args.members, args.profile, args.secure)
    print(_endpoint_string(layouts[0], args.profile, args.secure))
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

    layouts = _layouts(args.members, args.profile, args.secure)
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
    root = _data_root(args)
    layouts = _layouts(args.members, args.profile, args.secure)
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
        "--profile", default=None,
        choices=[PROFILE_LINUX, PROFILE_WSL],
        help=f"linux: one loopback address per member. wsl: one address, one "
             f"port block per member, so Windows localhost forwarding reaches "
             f"all of them. Default: {_default_profile()} here, or linux "
             f"under --secure.",
    )
    _add_security_options(parser, default_secure=False, default_tct=TCT_RSA)
    parser.add_argument(
        "--data-root", default="",
        help=f"Member databases. Defaults to {DEFAULT_DATA_ROOT}, or "
             f"{SECURE_DATA_ROOT} under --secure.",
    )
    parser.add_argument("--binary", default="")

    # The security options are accepted on BOTH sides of the subcommand.
    # `--secure` belongs to the cluster rather than to one verb, so it reads
    # naturally before it -- but the wrapper scripts append their extra
    # arguments after the subcommand, and a flag that works in only one
    # position is a flag that fails in a shell script for no reason the user
    # can see. SUPPRESS on the subparser copies is what makes both orders work:
    # without it the subparser's own default would overwrite a value the
    # top-level parser had already set.
    common = argparse.ArgumentParser(add_help=False)
    _add_security_options(
        common, default_secure=argparse.SUPPRESS, default_tct=argparse.SUPPRESS,
    )

    sub = parser.add_subparsers(dest="command", required=True)

    up = sub.add_parser("up", help="start the cluster", parents=[common])
    up.add_argument("--bootstrap", action="store_true",
                    help="force a one-time bootstrap (refused if already "
                         "initialised)")
    up.add_argument("--detach", action="store_true",
                    help="return instead of holding the members in the "
                         "foreground")
    up.add_argument("--timeout", type=float, default=60.0)

    status = sub.add_parser(
        "status", help="report member health", parents=[common],
    )
    status.add_argument("--endpoints", default="")

    sub.add_parser(
        "endpoints", help="print the --etcdEndpoints line", parents=[common],
    )
    sub.add_parser(
        "down", help="stop members started by `up --detach`", parents=[common],
    )

    wipe = sub.add_parser(
        "wipe", help="delete every member's data directory", parents=[common],
    )
    wipe.add_argument("--yes", action="store_true", required=False)

    return parser


def _add_security_options(
    parser: argparse.ArgumentParser, *, default_secure: Any, default_tct: Any,
) -> None:
    """The --secure/--tct pair, added to the top-level parser and each verb."""
    parser.add_argument(
        "--secure", action="store_true", default=default_secure,
        help="Run the cluster with mutual TLS on both the client and the peer "
             "listeners, using the etcd certificate set in "
             "Certificates/build.0.etcd/. Members are then addressed as "
             "XYZ-SNX1000n, which their certificates attest and bare loopback "
             "addresses do not, and every such name must resolve to "
             f"{_SECURE_BIND_ADDRESS}.",
    )
    parser.add_argument(
        "--tct", type=int, default=default_tct, choices=[TCT_RSA, TCT_ECDSA],
        help="TLS Certificate Type for --secure: 0=RSA, 1=ECDSA. Spelled as "
             "start-registry.sh spells it, and it must match what the "
             "registry members present.",
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse and normalise, so every entry point sees the same namespace.

    ``--detach`` re-parses its own argv rather than going through main(), so
    profile resolution has to live here and not there -- otherwise a detached
    secured cluster would be polled for readiness against the wrong topology.
    """
    args = build_parser().parse_args(argv)
    if args.profile is None:
        args.profile = _default_profile()
    return args


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

    args = parse_args(forwarded)
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
    args = parse_args(raw)

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
