# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Canonical cluster derivation: the reason registries never talk to each other.

Every member is handed the same list of advertised hosts. Everything else --
member names, the cluster token, every client and peer URL -- is *derived* from
that list here, by sorting it and applying fixed rules. Identical input produces
identical output on every host, computed independently, so there is nothing for
the registries to agree on at runtime and no peer channel between them.

That is the whole design. Coordination between registries goes through the
storage layer; coordination *about* the storage layer is settled before any
process starts, by configuration that is checked rather than negotiated.

Two consequences worth stating plainly:

**Determinism is a correctness property, not a tidiness one.** If two members
derived different cluster tokens or different member names from the same list,
they would form two clusters that each believed they were the whole thing. So
the sort is total and explicit, the token is a pure function of the canonical
list, and nothing here reads the environment, the clock, or the local hostname
except to locate *which* member is us.

**Only odd sizes are permitted.** 1, 3 or 5 members tolerate 0, 1 and 2
failures. An even-sized cluster tolerates no more failures than the odd size
below it while needing more machines and, worse, invites the belief that four
members tolerate two. The check is here rather than in the CLI so that every
entry point gets it.

Why this module is backend-neutral
----------------------------------
Both distributed backends -- ``nmos/etcd/`` and ``nmos/raft/`` -- need exactly
this derivation, and they must agree about it in the strong sense: the same
member list must yield the same names, the same ordering and the same quorum
arithmetic on every host, whichever backend is running. Two copies of that rule
that drifted would produce precisely the split cluster the determinism argument
above exists to prevent -- and the drift would be invisible, because each copy's
own tests would keep passing.

So it lives here, once, and ``nmos/etcd/cluster.py`` re-exports it unchanged.
The one thing that remains etcd vocabulary is ``ClusterLayout.initial_cluster``;
see its docstring for why it stays.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Sequence

# 0 failures, 1 failure, 2 failures. See the module docstring for why even
# sizes are refused rather than merely discouraged.
PERMITTED_SIZES = (1, 3, 5)

# Repository convention, clear of etcd's own 2379/2380 defaults so a stock etcd
# and a registry-managed one can coexist on a developer's machine.
DEFAULT_CLIENT_PORT = 2381
DEFAULT_PEER_PORT = 2382

MEMBER_NAME_PREFIX = "nmos-registry"

# The SAN every member's storage-layer certificate shares, and therefore the
# single string that is both the gRPC target-name override and etcd's
# --client-cert-allowed-hostname / --peer-cert-allowed-hostname.
#
# It lives here, next to the rest of the topology constants, because more than
# one entry point needs it -- each backend's --*CertificateName default and the
# rig's secured cluster -- and a second copy that drifted would not fail
# loudly: it would leave one side accepting certificates the other rejects.
DEFAULT_CERTIFICATE_NAME = "Example.Company.Device.Etcd.ABC.example.com"

# Member names accept almost anything, but a name also becomes part of a
# data-directory path and of log lines, so it is restricted to characters that
# need no quoting anywhere.
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


class ClusterConfigError(Exception):
    """The configured member set is not a usable cluster.

    Always fatal at startup. Every condition it reports is one where continuing
    would produce a cluster that looks healthy locally while being wrong
    globally -- a split cluster, a member nobody else expects, or a size whose
    failure tolerance is not what the operator thinks.

    Based on ``Exception`` rather than on either backend's error hierarchy: the
    condition is a configuration mistake discovered before any storage layer
    exists, so inheriting from one backend's error type would be a lie in the
    other's. Every catcher names this class directly, so the base is free.
    """


def _sanitise(host: str) -> str:
    return _UNSAFE_NAME_CHARS.sub("-", host).strip("-")


@dataclass(frozen=True)
class MemberSpec:
    """One configured member, before derivation.

    Args:
        host: Advertised hostname. Must be a SAN of that member's storage-layer
            certificate -- it is the name every peer and every registry client
            verifies it against.
        client_port: Port its client listener advertises.
        peer_port: Port its peer listener advertises.
        name: Explicit member name. Normally left unset and derived from the
            host. It exists for the same-machine test rigs, where several
            members share one address and differ only by port, so a host-derived
            name would collide.
        bind_address: Address to listen on, when it differs from the advertised
            host -- a member advertising a routable name but binding a specific
            loopback address, as the Linux test rig does.
    """

    host: str
    client_port: int = DEFAULT_CLIENT_PORT
    peer_port: int = DEFAULT_PEER_PORT
    name: str | None = None
    bind_address: str | None = None

    def derived_name(self) -> str:
        return self.name or f"{MEMBER_NAME_PREFIX}-{_sanitise(self.host)}"


@dataclass(frozen=True)
class Member:
    """One member of a derived cluster."""

    name: str
    host: str
    client_port: int
    peer_port: int
    bind_address: str

    @property
    def client_url(self) -> str:
        """Advertised client URL. Always https -- see ``ClusterLayout.tls``."""
        return f"https://{self.host}:{self.client_port}"

    @property
    def peer_url(self) -> str:
        return f"https://{self.host}:{self.peer_port}"

    @property
    def client_target(self) -> str:
        """``host:port`` as gRPC dials it, without a scheme."""
        return f"{self.host}:{self.client_port}"

    def listen_client_url(self, *, tls: bool) -> str:
        scheme = "https" if tls else "http"
        return f"{scheme}://{self.bind_address}:{self.client_port}"

    def listen_peer_url(self, *, tls: bool) -> str:
        scheme = "https" if tls else "http"
        return f"{scheme}://{self.bind_address}:{self.peer_port}"

    def advertise_client_url(self, *, tls: bool) -> str:
        scheme = "https" if tls else "http"
        return f"{scheme}://{self.host}:{self.client_port}"

    def advertise_peer_url(self, *, tls: bool) -> str:
        scheme = "https" if tls else "http"
        return f"{scheme}://{self.host}:{self.peer_port}"


@dataclass(frozen=True)
class ClusterLayout:
    """A validated, fully derived cluster.

    Every field is a pure function of the canonical member list, so two members
    given the same list produce equal layouts.
    """

    members: tuple[Member, ...]
    local: Member
    token: str
    namespace: str
    tls: bool

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def quorum(self) -> int:
        return self.size // 2 + 1

    @property
    def failures_tolerated(self) -> int:
        return self.size - self.quorum

    def initial_cluster(self, *, tls: bool | None = None) -> str:
        """etcd's ``--initial-cluster`` string, identical on every member.

        The one backend-specific formatter left in this module. It stays here
        rather than moving to ``nmos/etcd/`` because moving it would force
        ``DistributedConfig.layout`` to be narrowed to an etcd-specific layout
        subclass, and mypy treats dataclass fields as invariant -- narrowing a
        base field in a subclass is an ``[assignment]`` error. Working around
        that needs either a generic base or a ``cast`` in a property, and both
        cost more clarity than one honestly-named method does.

        The raft backend never calls it: its member set travels in the
        transport handshake and is checked against the cluster token, rather
        than being formatted into a child process's command line.
        """
        secure = self.tls if tls is None else tls
        return ",".join(
            f"{m.name}={m.advertise_peer_url(tls=secure)}" for m in self.members
        )

    def client_endpoints(self) -> tuple[str, ...]:
        """Every member's client endpoint, local first.

        Local first because the registry's channel pool tries them in order and
        the co-located member costs no network hop. The rest keep canonical
        order so failover is deterministic and reproducible in a test.
        """
        others = tuple(
            m.client_target for m in self.members if m.name != self.local.name
        )
        return (self.local.client_target, *others)

    def member_by_name(self, name: str) -> Member | None:
        return next((m for m in self.members if m.name == name), None)


def derive_cluster(
    specs: Sequence[MemberSpec],
    *,
    local_host: str,
    local_peer_port: int | None = None,
    namespace: str,
    tls: bool = True,
    flavour: str = "",
) -> ClusterLayout:
    """Validate a member set and derive everything else from it.

    Args:
        specs: Every member, in any order. Sorted here, so the caller may pass
            ``[local] + neighbours`` without thinking about ordering.
        local_host: This registry's advertised host, used to find which member
            is us. Must appear in ``specs``.
        local_peer_port: Disambiguates when several members share ``local_host``
            -- only the same-machine rigs.
        namespace: The key namespace. Part of the cluster token, so two
            clusters configured with the same hosts but different namespaces
            cannot accidentally join each other.
        flavour: Which storage layer this layout is for. Also part of the
            token, so an etcd cluster and a raft cluster configured with the
            same hosts and the same namespace cannot derive the same token and
            mistake each other for peers. Empty -- the default, and what the
            etcd path passes -- reproduces the original token exactly, so
            existing deployments keep their data directories.

    Raises:
        ClusterConfigError: The set is not 1/3/5 members, contains duplicate
            names or endpoints, or does not contain ``local_host``.
    """
    if not specs:
        raise ClusterConfigError(
            "no members configured; --distributed needs "
            "--registryAdvertisedHost plus --registryNeighbour",
        )

    if len(specs) not in PERMITTED_SIZES:
        permitted = ", ".join(str(size) for size in PERMITTED_SIZES)
        raise ClusterConfigError(
            f"a cluster must have {permitted} members, got {len(specs)}. "
            f"An even-sized cluster tolerates no more failures than the odd "
            f"size below it.",
        )

    for spec in specs:
        _validate_spec(spec)

    # Total order over (host, peer_port): every member sorts the same list the
    # same way, which is what makes the derivation agree across hosts.
    ordered = sorted(specs, key=lambda s: (s.host, s.peer_port, s.client_port))

    # Members that share a host are told apart by their peer port, because the
    # host alone no longer identifies them. Co-location is not exotic: members
    # on one machine must share its address (etcd checks a peer's certificate
    # against the address its connection arrives from), so the port is the only
    # thing left that differs. Names are still a pure function of the member
    # list, so every member derives the same ones.
    #
    # A host appearing once keeps the plain `nmos-registry-<host>`: the name
    # becomes a data-directory path, and changing it for existing deployments
    # would orphan their databases.
    shared = {
        spec.host for spec in ordered
        if sum(1 for other in ordered if other.host == spec.host) > 1
    }

    members = tuple(
        Member(
            name=(
                spec.name or (
                    f"{spec.derived_name()}-{spec.peer_port}"
                    if spec.host in shared else spec.derived_name()
                )
            ),
            host=spec.host,
            client_port=spec.client_port,
            peer_port=spec.peer_port,
            bind_address=spec.bind_address or spec.host,
        )
        for spec in ordered
    )

    _reject_duplicates(members)

    local = _find_local(members, local_host, local_peer_port)

    return ClusterLayout(
        members=members,
        local=local,
        token=cluster_token(members, namespace=namespace, flavour=flavour),
        namespace=namespace,
        tls=tls,
    )


def _validate_spec(spec: MemberSpec) -> None:
    if not spec.host or spec.host != spec.host.strip():
        raise ClusterConfigError(
            f"member host {spec.host!r} is empty or has surrounding whitespace",
        )
    for label, port in (
        ("client", spec.client_port), ("peer", spec.peer_port),
    ):
        if not 1 <= port <= 65535:
            raise ClusterConfigError(
                f"{spec.host}: {label} port {port} is out of range",
            )
    if spec.client_port == spec.peer_port:
        raise ClusterConfigError(
            f"{spec.host}: client and peer ports are both {spec.client_port}; "
            f"they are separate listeners and cannot share a port",
        )
    if spec.name is not None and not spec.name:
        raise ClusterConfigError(f"{spec.host}: explicit member name is empty")


def _reject_duplicates(members: Sequence[Member]) -> None:
    """Refuse a set with a repeated name or endpoint.

    A duplicate name makes the join fail; a duplicate endpoint is worse,
    because it can look like a working smaller cluster while one member's
    traffic silently lands on another's listener.
    """
    seen_names: set[str] = set()
    seen_peers: set[tuple[str, int]] = set()
    seen_clients: set[tuple[str, int]] = set()

    for member in members:
        if member.name in seen_names:
            raise ClusterConfigError(
                f"duplicate member name {member.name!r}. Members sharing a "
                f"host must be given explicit distinct names.",
            )
        seen_names.add(member.name)

        peer = (member.host, member.peer_port)
        if peer in seen_peers:
            raise ClusterConfigError(
                f"duplicate peer endpoint {member.host}:{member.peer_port}",
            )
        seen_peers.add(peer)

        client = (member.host, member.client_port)
        if client in seen_clients:
            raise ClusterConfigError(
                f"duplicate client endpoint {member.client_target}",
            )
        seen_clients.add(client)


def _find_local(
    members: Sequence[Member], local_host: str, local_peer_port: int | None,
) -> Member:
    """Locate this registry's own member entry.

    A configuration whose member list does not contain the local host is
    always a mistake, and a dangerous one: the registry would start, join a
    cluster it is not a member of, and serve from it while no other member
    expected it to exist.
    """
    candidates = [m for m in members if m.host == local_host]
    if not candidates:
        known = ", ".join(m.host for m in members)
        raise ClusterConfigError(
            f"local host {local_host!r} is not in the member list ({known}). "
            f"--registryAdvertisedHost must name this member, and the same "
            f"canonical set must be configured on every member.",
        )

    if local_peer_port is not None:
        exact = [m for m in candidates if m.peer_port == local_peer_port]
        if not exact:
            ports = ", ".join(str(m.peer_port) for m in candidates)
            raise ClusterConfigError(
                f"no member at {local_host!r} has peer port {local_peer_port} "
                f"(have: {ports})",
            )
        return exact[0]

    if len(candidates) > 1:
        ports = ", ".join(str(m.peer_port) for m in candidates)
        raise ClusterConfigError(
            f"{local_host!r} matches {len(candidates)} members (peer ports: "
            f"{ports}); specify which one this is",
        )
    return candidates[0]


def cluster_token(
    members: Sequence[Member], *, namespace: str, flavour: str = "",
) -> str:
    """A cluster token every member derives identically.

    The token keeps unrelated clusters from joining each other, so it must be
    *stable* across restarts and *identical* across members. A random or
    timestamped token would make every restart a new cluster; a constant one
    would let two separate deployments on the same network merge.

    Hashing the canonical member list plus the key namespace gives both
    properties, and makes a mismatched member list fail at the storage layer --
    with a cluster-ID mismatch -- rather than silently forming a split cluster.

    ``flavour`` distinguishes the storage layers. Without it, an etcd cluster
    and a raft cluster deployed on the same hosts under the same namespace
    would derive the same token, and a member of one could present credentials
    that looked, to the other, like a peer it was expecting. Empty is the
    default and hashes byte-identically to the original, so an existing etcd
    deployment's token -- and therefore its data directories -- are unchanged.
    """
    material = "|".join(
        f"{m.name}={m.host}:{m.peer_port}" for m in members
    )
    digest = hashlib.sha256(
        f"{flavour}{namespace}\n{material}".encode(),
    ).hexdigest()
    # The token only has to be a string; 16 hex characters is ample separation
    # and keeps log lines readable.
    return f"{MEMBER_NAME_PREFIX}-{digest[:16]}"
