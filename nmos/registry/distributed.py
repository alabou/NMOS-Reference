# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Turning ``--distributed`` and its flags into a validated configuration.

Everything here runs **before** the event loop, so a misconfigured cluster is a
clear ``CONFIG:`` message rather than a TLS handshake failure at the first
registration or, worse, a registry that comes up serving a cluster it was never
meant to join.

Two rules dominate this module.

The optional dependency
-----------------------
``nmos.etcd`` is imported lazily, from inside functions, and never at module
scope. That is what lets a checkout without the etcd extra import this module,
run the standalone registry, and pass ``mypy --strict`` -- while ``--distributed``
without the extra produces a message naming ``requirements-etcd.txt`` instead of
a bare ``ModuleNotFoundError``.

The platform rule
-----------------
etcd classifies windows/amd64 as **Tier 3** -- "considered unstable", no
maintainers, and not covered by the functional and robustness suites that verify
Raft/WAL/fsync durability. Those are precisely the guarantees that justify
putting the registry's authoritative state in etcd, so this project never runs
an etcd member on Windows. There, ``--distributed`` implies ``--etcdExternal``:
the registry is a *client* of a cluster managed elsewhere, and the supervisor is
not constructed at all.

WSL needs no detection and gets none: under WSL ``sys.platform`` is ``"linux"``,
so a registry inside WSL2 is an ordinary POSIX member with the full supervisor
and a Tier 1 etcd. The gate is one platform check with no heuristics.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nmos.etcd.cluster import ClusterLayout

log = logging.getLogger(__name__)

# Flags that only mean something when this process manages an etcd child.
# Passing one on Windows is rejected rather than ignored, so nobody believes
# they configured a managed member and quietly got a client.
_PROCESS_MANAGEMENT_FLAGS = (
    ("etcdBinary", "--etcdBinary"),
    ("etcdDataDir", "--etcdDataDir"),
    ("etcdBootstrap", "--etcdBootstrap"),
)

# Where ./install-etcd.sh puts the pinned binary.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BUNDLED_ETCD = _REPO_ROOT / ".etcd" / "etcd"


class DistributedConfigError(SystemExit):
    """A fatal configuration problem, phrased for the console.

    Subclasses ``SystemExit`` so it reads the same as every other startup
    refusal in ``nmos_registry.py`` and exits non-zero without a traceback.
    """

    def __init__(self, message: str) -> None:
        super().__init__(f"CONFIG: {message}")


@dataclass(frozen=True)
class DistributedConfig:
    """A validated distributed-registry configuration."""

    layout: ClusterLayout
    endpoints: tuple[str, ...]
    namespace: str

    external: bool
    """True when no etcd process is managed by this registry -- either
    ``--etcdExternal`` was given, or this is native Windows."""

    binary: str
    data_dir: Path
    bootstrap: bool

    tls: bool
    certificate: str
    key: str
    trusted_root_ca: tuple[str, ...]
    certificate_name: str
    client_crl_file: str
    peer_crl_file: str

    rpc_timeout: float
    mutation_timeout: float

    @property
    def manages_process(self) -> bool:
        return not self.external


def etcd_extra_available() -> bool:
    """Whether the optional etcd dependencies and generated stubs are present."""
    try:
        import grpc  # noqa: F401

        from nmos.etcd.generated import rpc_pb2  # noqa: F401
    except ImportError:
        return False
    return True


def require_etcd_extra() -> None:
    """Fail with instructions when the optional extra is missing.

    Three separate things have to be installed and only one of them is pip's,
    so the message lists all three rather than reporting whichever import
    happened to fail first.
    """
    try:
        import grpc  # noqa: F401
    except ImportError as exc:
        raise DistributedConfigError(
            "--distributed needs the optional etcd dependencies, which are "
            "not installed.\n"
            "  pip install -r requirements-etcd.txt\n"
            "  python -m nmos.etcd.generate      # protobuf stubs\n"
            "  ./install-etcd.sh                 # the etcd binary itself",
        ) from exc

    try:
        from nmos.etcd.generated import rpc_pb2  # noqa: F401
    except ImportError as exc:
        raise DistributedConfigError(
            "the etcd protobuf stubs are missing.\n"
            "  python -m nmos.etcd.generate",
        ) from exc

    # The stubs are committed, so the usual reason they are wrong is not that
    # they are absent but that the vendored protos moved and nobody
    # regenerated. That failure is otherwise silent and expensive: this member
    # would write records against a schema its peers no longer use.
    from nmos.etcd.generate import GeneratedOutOfDate, check_generated_current

    try:
        check_generated_current()
    except GeneratedOutOfDate as exc:
        raise DistributedConfigError(str(exc)) from exc


def resolve_distributed_config(args: Any) -> DistributedConfig | None:
    """Validate the distributed flags. Returns None when not distributed.

    Raises:
        DistributedConfigError: Any problem that would make the cluster wrong.
    """
    if not getattr(args, "distributed", False):
        _reject_stray_flags(args)
        return None

    require_etcd_extra()

    from nmos.etcd.cluster import (
        ClusterConfigError,
        MemberSpec,
        derive_cluster,
    )

    external = bool(args.etcdExternal)
    windows = sys.platform == "win32"

    if windows:
        external = _apply_windows_rule(args)

    tls = not args.etcdDisableTLS
    _validate_tls_inputs(args, tls=tls)

    explicit_endpoints = _explicit_endpoints(args)

    if external and explicit_endpoints and not args.registryNeighbour:
        # In external mode the cluster is someone else's, and the endpoints are
        # the only truthful description of it we have. Deriving the layout from
        # an empty neighbour list instead would make this a "1 member" cluster
        # that reports "tolerates 0 failures" while actually talking to three --
        # an operator reading that would believe they had no resilience.
        specs = [
            MemberSpec(
                host=host,
                client_port=port,
                peer_port=port + 1,
                name=f"external-{index}",
                bind_address=host,
            )
            for index, (host, port) in enumerate(
                _split_endpoints(explicit_endpoints)
            )
        ]
        local_host, local_peer = specs[0].host, specs[0].peer_port
    else:
        specs = [
            MemberSpec(
                host=host,
                client_port=args.etcdClientPort,
                peer_port=args.etcdPeerPort,
            )
            for host in _canonical_hosts(args)
        ]
        local_host, local_peer = args.registryAdvertisedHost, None

    try:
        layout = derive_cluster(
            specs,
            local_host=local_host,
            local_peer_port=local_peer,
            namespace=args.etcdNamespace,
            tls=tls,
        )
    except ClusterConfigError as exc:
        raise DistributedConfigError(str(exc)) from exc

    endpoints = _resolve_endpoints(args, layout, external=external)

    binary = _resolve_binary(args) if not external else ""
    data_dir = Path(args.etcdDataDir) if not external else Path()

    if not external and args.etcdBootstrap:
        # Not an error -- forming a cluster genuinely requires every member to
        # bootstrap once -- but worth saying out loud, because leaving the flag
        # in place is the mistake that forks the cluster on a later restart.
        log.warning(
            "registry: --etcdBootstrap is set. This is a ONE-TIME cluster "
            "initialization; remove the flag once all %d members have formed "
            "the cluster, or a later restart on an emptied data directory "
            "will create a second cluster.",
            layout.size,
        )

    return DistributedConfig(
        layout=layout,
        endpoints=endpoints,
        namespace=args.etcdNamespace,
        external=external,
        binary=binary,
        data_dir=data_dir,
        bootstrap=bool(args.etcdBootstrap) and not external,
        tls=tls,
        certificate=args.etcdCertificate,
        key=args.etcdKey,
        trusted_root_ca=tuple(args.etcdTrustedRootCA),
        certificate_name=args.etcdCertificateName,
        client_crl_file=args.etcdClientCrlFile,
        peer_crl_file=args.etcdPeerCrlFile,
        rpc_timeout=args.etcdRpcTimeout,
        mutation_timeout=args.etcdMutationTimeout,
    )


# ---------------------------------------------------------------------------
# Individual rules
# ---------------------------------------------------------------------------

def _reject_stray_flags(args: Any) -> None:
    """Refuse distributed flags that cannot do anything without --distributed.

    Silently ignoring them is how an operator ends up believing a standalone
    registry is part of a cluster.
    """
    if getattr(args, "registryNeighbour", None):
        raise DistributedConfigError(
            "--registryNeighbour was given without --distributed; the "
            "registry would run standalone and share nothing.",
        )
    if getattr(args, "etcdBootstrap", False):
        raise DistributedConfigError(
            "--etcdBootstrap was given without --distributed.",
        )


def _apply_windows_rule(args: Any) -> bool:
    """Force --etcdExternal on native Windows and reject process-management flags.

    Returns True: on Windows the registry is always a client.
    """
    offending = [
        flag for attribute, flag in _PROCESS_MANAGEMENT_FLAGS
        if _was_supplied(args, attribute)
    ]
    if offending:
        raise DistributedConfigError(
            f"{', '.join(offending)} cannot be used on native Windows.\n"
            f"  etcd classifies windows/amd64 as Tier 3 ('considered "
            f"unstable', unmaintained, and not covered by the functional and "
            f"robustness suites that verify Raft/WAL/fsync durability), so "
            f"this project never runs an etcd member there.\n"
            f"  --distributed on Windows implies --etcdExternal: point "
            f"--etcdEndpoints at a cluster managed elsewhere (WSL2, or a Linux "
            f"host), or run the whole rig under WSL.",
        )

    if not args.etcdEndpoints:
        raise DistributedConfigError(
            "--distributed on native Windows requires --etcdEndpoints, "
            "because no etcd member is started locally. Bring a cluster up "
            "under WSL with `python3 etcd_cluster.py up --members 3` and use "
            "the endpoints it prints.",
        )
    return True


def _was_supplied(args: Any, attribute: str) -> bool:
    """Whether a flag was actually passed, as opposed to left at its default."""
    value = getattr(args, attribute, None)
    if isinstance(value, bool):
        return value
    if attribute == "etcdDataDir":
        return bool(value) and value != "/var/lib/nmos-registry/etcd"
    return bool(value)


def _canonical_hosts(args: Any) -> list[str]:
    """The canonical member list: this host plus its neighbours."""
    local = args.registryAdvertisedHost
    if not local:
        raise DistributedConfigError(
            "--distributed requires --registryAdvertisedHost naming this "
            "member. It must be a SAN of this member's etcd certificate.",
        )

    hosts = [local, *[h.strip() for h in args.registryNeighbour if h.strip()]]

    duplicates = {h for h in hosts if hosts.count(h) > 1}
    if duplicates:
        raise DistributedConfigError(
            f"duplicate host(s) in the member list: "
            f"{', '.join(sorted(duplicates))}. --registryAdvertisedHost must "
            f"not be repeated as a --registryNeighbour.",
        )
    return hosts


def _validate_tls_inputs(args: Any, *, tls: bool) -> None:
    """Check the etcd certificate set before anything tries to hand it to etcd."""
    if not tls:
        return

    if not args.etcdCertificate or not args.etcdKey:
        raise DistributedConfigError(
            "--distributed requires --etcdCertificate and --etcdKey (or "
            "--etcdDisableTLS for testing only). One shared certificate serves "
            "all four etcd roles; use the *.etcd.chain.pem generated by "
            "Certificates/genEtcdCerts.sh.",
        )
    if not args.etcdTrustedRootCA:
        raise DistributedConfigError(
            "--distributed requires --etcdTrustedRootCA to verify etcd client "
            "and peer certificates.",
        )
    if not args.etcdCertificateName:
        raise DistributedConfigError(
            "--etcdCertificateName must not be empty: it is both the gRPC "
            "target-name override and etcd's --client/peer-cert-allowed-"
            "hostname, which is what stops any device certificate signed by "
            "the same Product CA from writing to the registry database.",
        )

    required: list[tuple[str, str]] = [
        ("--etcdCertificate", args.etcdCertificate),
        ("--etcdKey", args.etcdKey),
    ]
    required += [("--etcdTrustedRootCA", ca) for ca in args.etcdTrustedRootCA]
    if args.etcdClientCrlFile:
        required.append(("--etcdClientCrlFile", args.etcdClientCrlFile))
    if args.etcdPeerCrlFile:
        required.append(("--etcdPeerCrlFile", args.etcdPeerCrlFile))

    for role, path in required:
        if not os.path.isfile(path):
            raise DistributedConfigError(f"{role} is not accessible: {path!r}")


def _explicit_endpoints(args: Any) -> tuple[str, ...]:
    """Endpoints exactly as configured, or empty."""
    if not args.etcdEndpoints:
        return ()
    return tuple(
        part.strip() for part in args.etcdEndpoints.split(",") if part.strip()
    )


def _split_endpoints(endpoints: tuple[str, ...]) -> list[tuple[str, int]]:
    """Split ``host:port`` endpoints, rejecting anything malformed."""
    split: list[tuple[str, int]] = []
    for endpoint in endpoints:
        target = endpoint
        for scheme in ("https://", "http://"):
            if target.startswith(scheme):
                target = target[len(scheme):]
        host, _, port = target.rstrip("/").rpartition(":")
        if not host or not port.isdigit():
            raise DistributedConfigError(
                f"--etcdEndpoints entry {endpoint!r} is not host:port",
            )
        split.append((host, int(port)))
    return split


def _resolve_endpoints(
    args: Any, layout: ClusterLayout, *, external: bool,
) -> tuple[str, ...]:
    """Explicit endpoints when given, otherwise derived from the member list."""
    if args.etcdEndpoints:
        endpoints = tuple(
            part.strip() for part in args.etcdEndpoints.split(",")
            if part.strip()
        )
        if not endpoints:
            raise DistributedConfigError("--etcdEndpoints is empty")
        return endpoints

    if external:
        raise DistributedConfigError(
            "--etcdExternal requires --etcdEndpoints; without a managed "
            "member there is nothing to derive them from.",
        )
    return layout.client_endpoints()


def _resolve_binary(args: Any) -> str:
    """The etcd executable: explicit, else repo-local, else PATH.

    Repo-local first follows the ``.playwright/`` convention: a version-pinned
    dependency fetched into the checkout by ``./install-etcd.sh`` should be
    preferred over whatever unrelated etcd happens to be on the system PATH.
    """
    if args.etcdBinary:
        return str(args.etcdBinary)
    if _BUNDLED_ETCD.is_file():
        return str(_BUNDLED_ETCD)
    return "etcd"
