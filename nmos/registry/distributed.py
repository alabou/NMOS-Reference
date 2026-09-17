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

import ipaddress
import logging
import os
import socket
import sys
from dataclasses import dataclass
from enum import Enum
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


class DistributedBackend(Enum):
    """Which storage layer backs ``--distributed``.

    Both are supported and neither is deprecated. They differ in durability and
    platform, not in maturity: etcd persists to disk, survives the loss of every
    member at once, and can be resized while running; raft is in-process, needs
    nothing installed beyond this checkout, and runs natively on Windows, where
    etcd is Tier 3 and therefore client-only here.

    An enum rather than bare strings so the two arms cannot drift apart by a
    typo, and so ``isinstance`` narrowing on the config has something to be
    checked against.
    """

    ETCD = "etcd"
    RAFT = "raft"


@dataclass(frozen=True)
class DistributedConfig:
    """What every distributed backend is configured with.

    Deliberately a base class with a concrete arm per backend, rather than one
    dataclass carrying every field either might need. The banner, the TLS
    refusals and the mutation timeouts all read the *same* ten fields whichever
    backend is running; the alternative is an ``if backend ==`` ladder in each
    of them, and a field like ``rpc_timeout`` quietly meaning two things.

    **No field here may acquire a default.** A dataclass base with a defaulted
    field forces defaults onto every subclass field too, and the test rigs
    rebuild these from ``config.__dict__``, which only works while every field
    is constructible by keyword.
    """

    layout: ClusterLayout
    endpoints: tuple[str, ...]
    """Where the peers are. etcd: the client endpoints this registry dials.
    raft: the peer transport targets, local first."""
    namespace: str

    tls: bool
    certificate: str
    key: str
    trusted_root_ca: tuple[str, ...]
    certificate_name: str

    rpc_timeout: float
    mutation_timeout: float

    @property
    def backend(self) -> DistributedBackend:
        raise NotImplementedError


@dataclass(frozen=True)
class EtcdConfig(DistributedConfig):
    """Configuration for the etcd-backed distributed registry."""

    external: bool
    """True when no etcd process is managed by this registry -- either
    ``--etcdExternal`` was given, or this is native Windows."""

    binary: str
    data_dir: Path
    bootstrap: bool

    client_crl_file: str
    peer_crl_file: str

    @property
    def backend(self) -> DistributedBackend:
        return DistributedBackend.ETCD

    @property
    def manages_process(self) -> bool:
        return not self.external


@dataclass(frozen=True)
class RaftConfig(DistributedConfig):
    """Configuration for the in-process raft-backed distributed registry."""

    state_dir: Path
    """Where the term/vote file lives.

    Not a database directory. The raft backend keeps its log in memory; what
    reaches the disk is about 24 bytes of ``{term, voted_for, incarnation}``,
    written when the election term changes. Deleting it is not equivalent to
    deleting an etcd data directory -- it is equivalent to telling this member
    it has never voted, which is exactly the state election safety depends on
    it not being in. See ``nmos/raft/persist.py``.
    """

    crl_file: str
    """One CRL, not two.

    etcd has separate client and peer listeners with separate certificate
    roles; raft members talk only to each other, so there is one relationship
    to revoke against.
    """

    peer_port: int

    @property
    def backend(self) -> DistributedBackend:
        return DistributedBackend.RAFT


@dataclass(frozen=True)
class _StorageFlags:
    """One backend's storage-layer flag family, by name and by value.

    The security refusals below -- plaintext off the loopback, plaintext under a
    secured registry, and the certificate-set check -- are identical arguments
    whichever storage layer is running, and they are the most dangerous code in
    this module to duplicate. A second copy written for a new backend that
    happened to omit one would ship an unencrypted registry database on a LAN,
    while the refusal that should have caught it sat ten lines away, working
    perfectly, for the other backend.

    So there is one implementation, and this record is what tells it which
    flags to name in its message and which values to inspect. ``flag`` builds
    the flag name from the prefix, so ``--etcdDisableTLS`` and
    ``--raftDisableTLS`` come out of the same format string.
    """

    backend: DistributedBackend
    prefix: str
    """Flag-name stem: ``etcd`` gives ``--etcdCertificate``."""

    noun: str
    """How the storage layer is referred to in prose, mid-sentence."""

    certificate_hint: str
    """Where this backend's shipped certificate set lives."""

    roles_hint: str
    """What the one shared certificate covers, for the 'why one cert' line."""

    name_hint: str
    """What ``--*CertificateName`` is enforced as, and what it prevents."""

    certificate: str
    key: str
    trusted_root_ca: tuple[str, ...]
    certificate_name: str
    crls: tuple[tuple[str, str], ...]
    """``(flag, path)`` for each CRL that was actually supplied."""

    client_port: int
    peer_port: int

    def flag(self, suffix: str) -> str:
        return f"--{self.prefix}{suffix}"


def _etcd_flags(args: Any) -> _StorageFlags:
    """The etcd flag family, as the shared validators see it."""
    crls = [
        (flag, path) for flag, path in (
            ("--etcdClientCrlFile", getattr(args, "etcdClientCrlFile", "")),
            ("--etcdPeerCrlFile", getattr(args, "etcdPeerCrlFile", "")),
        ) if path
    ]
    return _StorageFlags(
        backend=DistributedBackend.ETCD,
        prefix="etcd",
        noun="etcd",
        certificate_hint="Certificates/build.0.etcd/",
        roles_hint="all four etcd roles",
        name_hint=(
            "it is both the gRPC target-name override and etcd's "
            "--client/peer-cert-allowed-hostname, which is what stops any "
            "device certificate signed by the same Product CA from writing to "
            "the registry database"
        ),
        certificate=getattr(args, "etcdCertificate", ""),
        key=getattr(args, "etcdKey", ""),
        trusted_root_ca=tuple(getattr(args, "etcdTrustedRootCA", None) or ()),
        certificate_name=getattr(args, "etcdCertificateName", ""),
        crls=tuple(crls),
        client_port=getattr(args, "etcdClientPort", 0),
        peer_port=getattr(args, "etcdPeerPort", 0),
    )


def _raft_flags(args: Any) -> _StorageFlags:
    """The raft flag family, as the shared validators see it."""
    crl = getattr(args, "raftCrlFile", "")
    return _StorageFlags(
        backend=DistributedBackend.RAFT,
        prefix="raft",
        noun="the raft cluster",
        certificate_hint="Certificates/build.0.etcd/",
        roles_hint="both raft roles -- listening and dialling",
        name_hint=(
            "it is the SAN every peer is verified against, which is what "
            "stops any device certificate signed by the same Product CA from "
            "joining the cluster and writing to the registry database"
        ),
        certificate=getattr(args, "raftCertificate", ""),
        key=getattr(args, "raftKey", ""),
        trusted_root_ca=tuple(getattr(args, "raftTrustedRootCA", None) or ()),
        certificate_name=getattr(args, "raftCertificateName", ""),
        crls=((("--raftCrlFile", crl),) if crl else ()),
        client_port=getattr(args, "raftClientPort", 0),
        peer_port=getattr(args, "raftPeerPort", 0),
    )


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

    backend = DistributedBackend(
        getattr(args, "distributedBackend", DistributedBackend.RAFT.value),
    )
    _reject_flags_for_the_other_backend(args, backend)

    if backend is DistributedBackend.ETCD:
        return _resolve_etcd(args)
    return _resolve_raft(args)


_ETCD_TO_RAFT: dict[str, str | None] = {
    "--etcdNamespace": "--raftNamespace",
    "--etcdClientPort": "--raftClientPort",
    "--etcdPeerPort": "--raftPeerPort",
    "--etcdCertificate": "--raftCertificate",
    "--etcdKey": "--raftKey",
    "--etcdTrustedRootCA": "--raftTrustedRootCA",
    "--etcdCertificateName": "--raftCertificateName",
    "--etcdPeerCrlFile": "--raftCrlFile",
    "--etcdDisableTLS": "--raftDisableTLS",
    "--etcdRpcTimeout": "--raftRpcTimeout",
    "--etcdMutationTimeout": "--raftMutationTimeout",
    "--etcdDataDir": "--raftStateDir",
    # None means the concept does not exist on the other side. That is a
    # sanctioned divergence, not an omission, so the message says so rather
    # than leaving the operator hunting for a flag that was never written.
    "--etcdEndpoints": None,
    "--etcdExternal": None,
    "--etcdBinary": None,
    "--etcdBootstrap": None,
    "--etcdClientCrlFile": None,
}

_RAFT_TO_ETCD = {
    raft: etcd for etcd, raft in _ETCD_TO_RAFT.items() if raft is not None
}


def _reject_flags_for_the_other_backend(
    args: Any, backend: DistributedBackend,
) -> None:
    """Refuse backend flags the selected backend will never read.

    The failure this exists to make impossible: a command line full of
    ``--etcd*`` flags quietly coming up on raft because ``--distributedBackend``
    was left at its default. That registry would start, form a cluster, and
    serve -- and its operator, reading back their own command line, would
    believe they had joined an etcd cluster. Nothing later would contradict
    them until the two halves of the deployment failed to see each other.

    Refused, never reinterpreted, and never silently ignored.
    """
    supplied = set(getattr(args, "suppliedFlags", ()) or ())
    explicit = "--distributedBackend" in supplied

    if backend is DistributedBackend.RAFT:
        offending = sorted(supplied & set(_ETCD_TO_RAFT))
        mapping: dict[str, str | None] = _ETCD_TO_RAFT
        other = "etcd"
    else:
        offending = sorted(supplied & set(_RAFT_TO_ETCD))
        mapping = dict(_RAFT_TO_ETCD)
        other = "raft"

    if not offending:
        return

    equivalents = [
        mapping[flag] for flag in offending if mapping.get(flag) is not None
    ]
    instead = (
        f"pass {', '.join(str(e) for e in equivalents)} instead"
        if equivalents else
        f"{backend.value} has no equivalent -- it manages no separate "
        f"process, so there is nothing to point at, launch, store or bootstrap"
    )
    named = ", ".join(offending)

    if not explicit:
        raise DistributedConfigError(
            f"{named} cannot be used without --distributedBackend {other}.\n"
            f"  --distributedBackend defaults to {backend.value}, a different "
            f"storage layer with its own flags, so the flag(s) above would be "
            f"read by nothing at all -- and a registry that came up on "
            f"{backend.value} while its operator believed it had joined "
            f"{'an etcd' if other == 'etcd' else 'a raft'} cluster is exactly "
            f"the failure this refusal exists to prevent.\n"
            f"  Add --distributedBackend {other} to use them"
            f"{' (that backend needs `pip install -r requirements-etcd.txt` '
               'and `./install-etcd.sh`)' if other == 'etcd' else ''}, "
            f"or {instead}.",
        )

    raise DistributedConfigError(
        f"{named} cannot be used with --distributedBackend {backend.value}.\n"
        f"  Both backends are supported and neither is deprecated, but they "
        f"are configured separately: a flag named for one is never read by "
        f"the other.\n"
        f"  Either change --distributedBackend, or {instead}.",
    )


def _resolve_raft(args: Any) -> RaftConfig:
    """The raft arm. No optional extra, no child process, no platform gate."""
    from nmos.cluster.layout import ClusterConfigError, MemberSpec, derive_cluster
    from nmos.raft.cluster import RAFT_FLAVOUR

    flags = _raft_flags(args)
    tls = not args.raftDisableTLS
    _reject_plaintext_storage_under_a_secure_registry(args, flags, tls=tls)
    _reject_plaintext_storage_off_the_loopback(args, flags, tls=tls)
    _validate_tls_inputs(flags, tls=tls)

    members = _canonical_members(args, flags)
    specs = [
        MemberSpec(
            host=host, client_port=client, peer_port=peer,
            bind_address=_resolve_host(host),
        )
        for host, client, peer in members
    ]
    try:
        layout = derive_cluster(
            specs,
            local_host=members[0][0],
            local_peer_port=members[0][2],
            namespace=args.raftNamespace,
            tls=tls,
            flavour=RAFT_FLAVOUR,
        )
    except ClusterConfigError as exc:
        raise DistributedConfigError(str(exc)) from exc

    return RaftConfig(
        layout=layout,
        endpoints=tuple(
            f"{m.host}:{m.peer_port}" for m in layout.members
        ),
        namespace=args.raftNamespace,
        tls=tls,
        certificate=args.raftCertificate,
        key=args.raftKey,
        trusted_root_ca=tuple(args.raftTrustedRootCA),
        certificate_name=args.raftCertificateName,
        rpc_timeout=args.raftRpcTimeout,
        mutation_timeout=args.raftMutationTimeout,
        state_dir=Path(args.raftStateDir),
        crl_file=args.raftCrlFile,
        peer_port=layout.local.peer_port,
    )


def _resolve_etcd(args: Any) -> EtcdConfig:
    """The etcd arm: an optional extra, a child process, a platform gate."""
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

    flags = _etcd_flags(args)
    tls = not args.etcdDisableTLS
    _reject_plaintext_storage_under_a_secure_registry(args, flags, tls=tls)
    _reject_plaintext_storage_off_the_loopback(args, flags, tls=tls)
    _validate_tls_inputs(flags, tls=tls)

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
        members = _canonical_members(args, flags)
        specs = [
            MemberSpec(
                host=host, client_port=client, peer_port=peer,
                # A member is NAMED for its certificate but must LISTEN on an
                # address: etcd refuses a hostname in --listen-*-urls outright
                # ("expected IP in URL for binding"), so a managed member whose
                # bind address defaulted to its own name could never start.
                # Resolution failure is left as None -- derive_cluster then
                # falls back to the name, and the resulting error is about the
                # name not resolving, which is the actual problem.
                bind_address=_resolve_host(host),
            )
            for host, client, peer in members
        ]
        # The advertised host is always first, and its peer port disambiguates
        # it from any co-located member sharing the same host.
        local_host, local_peer = members[0][0], members[0][2]

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

    return EtcdConfig(
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


def _split_member(value: str, flags: _StorageFlags) -> tuple[str, int, int]:
    """``host`` or ``host:client_port`` -> (host, client_port, peer_port).

    Members carry their own ports because they do not always have an address to
    themselves. When several members share one machine -- the single-host rig,
    and any co-located deployment -- they must share its address as well: etcd
    verifies the certificate a peer presents against the address the connection
    arrives *from*, and connections between loopback addresses are all sourced
    from 127.0.0.1 whatever the destination. One address that every member name
    resolves to satisfies that check; per-member addresses do not. What then
    separates the members is the port.

    The peer port is the client port plus one, which is both the relationship
    between the --etcdClientPort/--etcdPeerPort defaults (2381/2382) and what
    --etcdEndpoints already assumes in the external path below.
    """
    host, separator, port = value.rpartition(":")
    if not separator:
        return value, flags.client_port, flags.peer_port
    if not host or not port.isdigit():
        raise DistributedConfigError(
            f"member {value!r} is not host or host:client_port",
        )
    return host, int(port), int(port) + 1


def _canonical_members(
    args: Any, flags: _StorageFlags,
) -> list[tuple[str, int, int]]:
    """The canonical member list: this member first, then its neighbours."""
    local = args.registryAdvertisedHost
    if not local:
        raise DistributedConfigError(
            "--distributed requires --registryAdvertisedHost naming this "
            "member. It must be a SAN of this member's etcd certificate.",
        )

    members = [
        _split_member(value, flags)
        for value in (local, *(h.strip() for h in args.registryNeighbour))
        if value
    ]

    # Keyed on host AND port: co-located members legitimately share a host and
    # are distinguished by port, so refusing a repeated host outright would
    # refuse the single-machine cluster this exists to support.
    endpoints = [(host, client) for host, client, _ in members]
    duplicates = {e for e in endpoints if endpoints.count(e) > 1}
    if duplicates:
        raise DistributedConfigError(
            f"duplicate member(s) in the list: "
            f"{', '.join(f'{h}:{p}' for h, p in sorted(duplicates))}. Each "
            f"member needs its own host, or its own port on a shared host.",
        )
    return members


def _reject_plaintext_storage_off_the_loopback(
    args: Any, flags: _StorageFlags, *, tls: bool,
) -> None:
    """An unsecured cluster may exist on one machine and nowhere else.

    A distributed registry whose members are on separate machines has its
    storage-layer traffic on a wire by definition, and that traffic carries
    every registered resource plus every write that changes them. There is no
    configuration in which that should be in the clear, and "we were only
    testing" is exactly how it ends up deployed, so the refusal lives here
    rather than in a comment.

    Loopback is the one case where plaintext is defensible: the packets cannot
    leave the host, so ``--*DisableTLS`` keeps the development rig it was added
    for. Anything else -- a private LAN address included, since reachable is
    reachable -- is refused.

    Names that do not resolve are left alone. That is a different failure, it
    has its own diagnosis further on, and guessing about it here would turn a
    DNS problem into a confusing security message.
    """
    if tls:
        return

    exposed: list[str] = []
    for host in _configured_hosts(args, flags):
        address = _resolve_host(host)
        if address is None:
            continue
        if not ipaddress.ip_address(address).is_loopback:
            exposed.append(f"{host} ({address})")

    if not exposed:
        return

    raise DistributedConfigError(
        f"{flags.flag('DisableTLS')} is only available to a cluster confined "
        f"to one machine, and these members are not:\n"
        + "".join(f"  {entry}\n" for entry in exposed)
        + f"  {flags.noun} holds every registered resource, so off the "
        f"loopback this would put the whole registry database on the network "
        f"unencrypted and unauthenticated.\n"
        f"  Secure it with {flags.flag('Certificate')}, {flags.flag('Key')} "
        f"and {flags.flag('TrustedRootCA')}; this repository ships a set in "
        f"{flags.certificate_hint}.",
    )


def _configured_hosts(args: Any, flags: _StorageFlags) -> list[str]:
    """Every host this configuration names, from whichever source describes it.

    ``--etcdEndpoints`` when given, because in external mode that is the only
    truthful description of the cluster; the member list otherwise. Returns
    empty rather than raising when neither is usable: the missing pieces have
    their own diagnostics, and a security refusal should not pre-empt them with
    a message about a different problem.
    """
    endpoints = _explicit_endpoints(args)
    if endpoints:
        try:
            return [host for host, _ in _split_endpoints(endpoints)]
        except DistributedConfigError:
            return []

    if not getattr(args, "registryAdvertisedHost", ""):
        return []
    try:
        return [host for host, _, _ in _canonical_members(args, flags)]
    except DistributedConfigError:
        return []


def _resolve_host(host: str) -> str | None:
    """The IPv4 address ``host`` resolves to, or None if it does not resolve."""
    try:
        return str(socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0])
    except (OSError, IndexError):
        return None


def _registry_listeners_are_tls(args: Any) -> bool:
    """Whether the Registration and Query listeners run over TLS.

    Deliberately the same three inputs ``classify_registry_rap`` uses in
    ``nmos_registry.py`` to tell RAP 0 from RAP 1 and 2: TLS is on when it was
    not disabled *and* a certificate/key pair was actually supplied. Recomputed
    here rather than imported because ``nmos_registry`` imports this module, and
    kept to those three inputs so the two can never disagree about whether a
    given command line describes a secured registry.
    """
    return not getattr(args, "registryDisableTLS", False) and bool(
        getattr(args, "registryCertificate", "")
        and getattr(args, "registryKey", ""),
    )


def _reject_plaintext_storage_under_a_secure_registry(
    args: Any, flags: _StorageFlags, *, tls: bool,
) -> None:
    """A secured registry may not keep its database on a plaintext storage layer.

    The storage layer holds *every* registered resource, so this combination is
    strictly worse than a plain-HTTP registry: it encrypts the interface an
    operator can see while leaving the entire database readable, and writable,
    by anyone who can reach the port.

    It also fails silently rather than loudly. ``tls`` is derived from
    ``--*DisableTLS`` alone, so a command line carrying both that flag and a
    full certificate set is accepted with the certificates **ignored** -- the
    operator reads back their own secured command line and believes it took
    effect. Refusing here is what makes "secured registry implies secured
    storage" a property of the program rather than a property of whichever
    launch script was used.
    """
    if tls or not _registry_listeners_are_tls(args):
        return

    supplied = [
        flag for flag, value in (
            (flags.flag("Certificate"), flags.certificate),
            (flags.flag("Key"), flags.key),
            (flags.flag("TrustedRootCA"), flags.trusted_root_ca),
        ) if value
    ]
    ignored = (
        f"\n  {', '.join(supplied)} would be IGNORED: "
        f"{flags.flag('DisableTLS')} is the only input that decides this, so "
        f"the certificates you passed would never reach {flags.noun}."
        if supplied else ""
    )

    raise DistributedConfigError(
        f"{flags.flag('DisableTLS')} cannot be combined with a TLS "
        f"Registration/Query interface.\n"
        f"  {flags.noun} holds every registered resource, so a secured "
        f"registry over a plaintext {flags.noun} leaves the whole database "
        f"readable and writable by anyone who can reach the port -- while the "
        f"interface an operator inspects looks secure."
        f"{ignored}\n"
        f"  Either secure {flags.noun} as well ({flags.flag('Certificate')}, "
        f"{flags.flag('Key')}, {flags.flag('TrustedRootCA')}; this repository "
        f"ships a set in {flags.certificate_hint}), or run the whole rig "
        f"unsecured with --registryDisableTLS.",
    )


def _validate_tls_inputs(flags: _StorageFlags, *, tls: bool) -> None:
    """Check the certificate set before anything tries to use it."""
    if not tls:
        return

    if not flags.certificate or not flags.key:
        raise DistributedConfigError(
            f"--distributed requires {flags.flag('Certificate')} and "
            f"{flags.flag('Key')} (or {flags.flag('DisableTLS')} for testing "
            f"only). One shared certificate serves {flags.roles_hint}; this "
            f"repository ships a set in {flags.certificate_hint} -- pass the "
            f"*.etcd.chain.pem and its matching key, verified against "
            f"Certificates/build.0/ExampleRootCA.ec.pem.",
        )
    if not flags.trusted_root_ca:
        raise DistributedConfigError(
            f"--distributed requires {flags.flag('TrustedRootCA')} to verify "
            f"{flags.noun} client and peer certificates.",
        )
    if not flags.certificate_name:
        raise DistributedConfigError(
            f"{flags.flag('CertificateName')} must not be empty: "
            f"{flags.name_hint}.",
        )

    required: list[tuple[str, str]] = [
        (flags.flag("Certificate"), flags.certificate),
        (flags.flag("Key"), flags.key),
    ]
    required += [
        (flags.flag("TrustedRootCA"), ca) for ca in flags.trusted_root_ca
    ]
    required += list(flags.crls)

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
