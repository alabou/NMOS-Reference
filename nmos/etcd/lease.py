# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Leases: the whole of distributed garbage collection.

One lease per Node, with every key belonging to that Node — its own record, its
Devices, their Sources/Flows/Senders/Receivers, and all the ID claims — attached
to it. A heartbeat renews the lease; silence lets it expire; etcd then removes
the entire subtree atomically on every member at once. No registry runs a
collection pass, and no registry can disagree with another about whether a Node
is alive.

Why nothing is written per heartbeat
------------------------------------
The obvious implementation writes a health key on each beat. The legacy dRDS did
exactly that (``/health_nodes/<id>``) and every member watched it, so 100 Nodes
beating at the 5 s default produced 100 Raft writes per second fanning out to
500 watch events per second across a 5-member cluster — to record liveness that
the lease already records, and that the lease records *more* reliably, since a
lease cannot be renewed by a member that has lost quorum.

Renewal here writes nothing to the keyspace. It is a lease refresh, which is
cheap and does not wake a single watcher. That is the largest single efficiency
difference between this design and the reference it replaces, and §13 of the
plan measures it: etcd write rate should be flat in Node count where the legacy
design was linear.

Renewal shape
-------------
etcd exposes renewal only as a bidirectional stream, but one renewal is
logically a unary call, so ``keepalive_once`` opens a stream, sends once, reads
once and closes. That is what etcd's own client library does for ``KeepAliveOnce``, and
on an established HTTP/2 channel it is one round trip with no new connection.

A persistent multiplexed keepalive stream shared by every Node would save that
stream setup. It is deliberately *not* done here: it adds response-routing and
reconnect state, and whether it is worth that is a question for the heartbeat
benchmark, not for a guess made while writing the client.
"""

from __future__ import annotations

from dataclasses import dataclass

from nmos.etcd.channel import EtcdChannelPool, stream_method, unary_method
from nmos.etcd.errors import EtcdLeaseNotFound
from nmos.etcd.generated import rpc_pb2

_GRANT = unary_method(
    "Lease", "LeaseGrant",
    rpc_pb2.LeaseGrantRequest, rpc_pb2.LeaseGrantResponse,
)
_REVOKE = unary_method(
    "Lease", "LeaseRevoke",
    rpc_pb2.LeaseRevokeRequest, rpc_pb2.LeaseRevokeResponse,
)
_TIME_TO_LIVE = unary_method(
    "Lease", "LeaseTimeToLive",
    rpc_pb2.LeaseTimeToLiveRequest, rpc_pb2.LeaseTimeToLiveResponse,
)
_KEEP_ALIVE = stream_method(
    "Lease", "LeaseKeepAlive",
    rpc_pb2.LeaseKeepAliveRequest, rpc_pb2.LeaseKeepAliveResponse,
)


@dataclass(frozen=True)
class Lease:
    """A granted lease."""

    id: int
    ttl: int
    """Seconds etcd actually granted, which may exceed the requested TTL —
    etcd enforces a minimum. Callers must renew against *this*, not against
    what they asked for."""


@dataclass(frozen=True)
class LeaseStatus:
    """What etcd currently knows about a lease."""

    id: int
    granted_ttl: int
    remaining_ttl: int
    """Seconds left before expiry. Negative when the lease is already gone."""

    keys: tuple[bytes, ...]
    """Keys attached to the lease, when they were requested. Used by the test
    suite to assert that a Node's whole subtree really is on one lease — the
    property the entire GC design rests on."""

    @property
    def alive(self) -> bool:
        return self.remaining_ttl >= 0


class EtcdLease:
    """Lease operations over a channel pool."""

    __slots__ = ("_pool",)

    def __init__(self, pool: EtcdChannelPool) -> None:
        self._pool = pool

    async def grant(
        self, ttl_seconds: int, *, timeout: float | None = None,
    ) -> Lease:
        """Create a lease.

        Args:
            ttl_seconds: Requested TTL. The registry passes
                ``ceil(--garbageCollectionInterval)`` — 12 s by default, the
                interval of ``Behaviour - Registration.md:47``, and deliberately
                not the 15 s the legacy dRDS used against the same 12 s registry
                interval, which left a collected Node alive in etcd for several
                seconds after the registry had already dropped it.
        """
        response = await self._pool.call(
            _GRANT, rpc_pb2.LeaseGrantRequest(TTL=ttl_seconds), timeout=timeout,
        )
        # etcd reports a grant failure in-band rather than as an RPC error.
        if response.error:
            raise EtcdLeaseNotFound(f"lease grant failed: {response.error}")
        return Lease(id=response.ID, ttl=response.TTL)

    async def keepalive_once(
        self, lease_id: int, *, timeout: float | None = None,
    ) -> int:
        """Renew a lease. Returns the new TTL in seconds.

        Raises:
            EtcdLeaseNotFound: The lease has expired or been revoked. This is
                authoritative, not transient: the Node is gone as far as the
                cluster is concerned, so the heartbeat handler answers 404 and
                the Node re-registers everything in order, exactly as
                ``Behaviour - Registration.md:112-114`` prescribes.
        """
        response = await self._pool.call_stream_once(
            _KEEP_ALIVE,
            rpc_pb2.LeaseKeepAliveRequest(ID=lease_id),
            timeout=timeout,
        )
        # A renewal for a dead lease is not an RPC error -- etcd answers with
        # TTL 0. Treating that as success would keep a Node that the cluster has
        # already collected alive in the local view forever.
        if response.TTL <= 0:
            raise EtcdLeaseNotFound(
                f"lease {lease_id:x} has expired or been revoked",
            )
        return int(response.TTL)

    async def time_to_live(
        self,
        lease_id: int,
        *,
        with_keys: bool = False,
        timeout: float | None = None,
    ) -> LeaseStatus:
        """Inspect a lease without renewing it.

        Used by the debug ``GET /health/nodes/{id}`` route, which must not
        refresh anything: a diagnostic read that silently kept a Node alive
        would mask exactly the garbage-collection problem someone would be
        using it to investigate.
        """
        response = await self._pool.call(
            _TIME_TO_LIVE,
            rpc_pb2.LeaseTimeToLiveRequest(ID=lease_id, keys=with_keys),
            timeout=timeout,
        )
        return LeaseStatus(
            id=response.ID,
            granted_ttl=response.grantedTTL,
            remaining_ttl=response.TTL,
            keys=tuple(response.keys),
        )

    async def revoke(
        self, lease_id: int, *, timeout: float | None = None,
    ) -> None:
        """Revoke a lease, deleting every key attached to it.

        Idempotent by design: revoking an already-gone lease is success, not an
        error. Node deletion revokes the emptied lease best-effort *after* the
        subtree transaction, and racing that against natural expiry is entirely
        normal — both outcomes leave the cluster in the state the caller wanted.
        """
        try:
            await self._pool.call(
                _REVOKE, rpc_pb2.LeaseRevokeRequest(ID=lease_id),
                timeout=timeout,
            )
        except EtcdLeaseNotFound:
            return
