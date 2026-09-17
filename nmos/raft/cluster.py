# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Raft's view of the cluster, derived from the shared topology.

Reuse, not reimplementation. ``nmos/cluster/layout.py`` already decides the
member set, their canonical order, their names, the quorum arithmetic and the
cluster token, and it does so identically on every host from the same
configured list. That determinism is a correctness property -- two members
deriving different member names or different tokens would form two clusters
that each believed they were the whole thing -- so there is exactly one
implementation of it and this module sits on top.

What raft adds is small and specific:

* **A member index.** Position in the canonical sorted order, which every
  member computes identically. It is what messages carry instead of names, and
  it is the ``owner`` in ``cursors.py``'s lane allocation -- so it has to be
  stable and distinct, which the total order guarantees.
* **A port.** Raft's peer transport, clear of etcd's 2381/2382 so an etcd rig
  and a raft rig can share a developer's machine.
* **A flavoured token.** ``flavour="raft\\n"`` so a raft cluster and an etcd
  cluster configured on the same hosts under the same namespace cannot derive
  the same identity and mistake each other for peers.
"""

from __future__ import annotations

from dataclasses import dataclass

from nmos.cluster.layout import ClusterLayout, cluster_token

# Clear of --etcdClientPort (2381) and --etcdPeerPort (2382), so both backends
# can be exercised on one machine without a port collision that would surface
# as a mysterious failure to form a cluster.
DEFAULT_RAFT_CLIENT_PORT = 2481
DEFAULT_RAFT_PEER_PORT = 2482

# Hashed into the cluster token. The trailing newline keeps it from being a
# prefix of any namespace, so two deployments cannot collide by one namespace
# happening to begin with the other's flavour.
RAFT_FLAVOUR = "raft\n"


@dataclass(frozen=True)
class RaftMember:
    """One member, as raft addresses it."""

    index: int
    """Position in the canonical order. Stable, distinct, and derived
    identically on every member -- which is what lets it serve as both the
    wire identity and the cursor lane."""

    name: str
    host: str
    port: int

    @property
    def target(self) -> tuple[str, int]:
        return self.host, self.port


@dataclass(frozen=True)
class RaftLayout:
    """The cluster as raft sees it."""

    members: tuple[RaftMember, ...]
    local: RaftMember
    cluster_id: str

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def quorum(self) -> int:
        return self.size // 2 + 1

    @property
    def failures_tolerated(self) -> int:
        return self.size - self.quorum

    def peers(self) -> tuple[RaftMember, ...]:
        return tuple(
            member for member in self.members
            if member.index != self.local.index
        )

    def peer_targets(self) -> dict[int, tuple[str, int]]:
        return {member.index: member.target for member in self.peers()}

    def member_by_index(self, index: int) -> RaftMember | None:
        return next(
            (member for member in self.members if member.index == index), None,
        )


def derive_raft_layout(layout: ClusterLayout) -> RaftLayout:
    """Project a shared cluster layout onto raft's addressing.

    ``layout.members`` is already in canonical sorted order, so enumerating it
    assigns every member the same index on every host -- the same argument that
    makes the member names agree, applied to one more derived value.

    The peer port comes from the member, never from a default chosen here:
    ``--raftPeerPort`` has already been folded into the shared layout by the
    time this runs, and members co-located on one host are distinguished by
    port alone. Substituting ``DEFAULT_RAFT_PEER_PORT`` for a uniform
    configured port would silently ignore the operator's ``--raftPeerPort``,
    and two members on one host would both claim to be reachable at it.
    """
    members = tuple(
        RaftMember(
            index=index,
            name=member.name,
            host=member.host,
            port=member.peer_port,
        )
        for index, member in enumerate(layout.members)
    )

    local = next(
        member for member in members if member.name == layout.local.name
    )
    return RaftLayout(
        members=members,
        local=local,
        cluster_id=cluster_token(
            layout.members, namespace=layout.namespace, flavour=RAFT_FLAVOUR,
        ),
    )
