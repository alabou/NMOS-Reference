# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The KV surface: revision-pinned reads, read sets, CAS transactions, deletes.

This is the vocabulary the registry backend is written in. Four operations
carry the whole design:

``range_prefix_at``  the fixed-revision preload. Every page after the first
    reads at *exactly* the snapshot revision, so a concurrent write cannot make
    a page overlap or skip a key.
``read_set``         the pre-validation read fence. One linearizable
    transaction covering target, parent, Node and ID claim, returning both the
    values and the revisions the following CAS must compare against.
``txn``              every mutation. The compare set is what enforces
    correctness; the fast path of the registry backend relies on being able to
    submit a CAS built from *believed* revisions and have a stale belief fail
    the compare rather than commit something wrong.
``delete_prefix``    the subtree cascade.

Comparisons and operations are built with the small helpers at the bottom
rather than by assembling protobuf by hand at each call site. That is not
decoration: ``compare_absent`` in particular encodes the one non-obvious etcd
idiom this design leans on — "this key does not exist" is expressed as
*create_revision == 0*, not as a missing-key check — and getting it wrong
silently turns a create-if-absent into an unconditional overwrite.

Protobuf messages are returned as-is for bulk data (``KeyValue``) rather than
copied into project dataclasses. The generated ``.pyi`` files make them fully
typed, attribute access is C-speed, and a preload of several thousand resources
should not pay for an extra object per key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from nmos.etcd.channel import EtcdChannelPool, unary_method
from nmos.etcd.generated import kv_pb2, rpc_pb2

if TYPE_CHECKING:
    pass

# Declared once, validated against the proto descriptor at import.
_RANGE = unary_method(
    "KV", "Range", rpc_pb2.RangeRequest, rpc_pb2.RangeResponse,
)
_TXN = unary_method("KV", "Txn", rpc_pb2.TxnRequest, rpc_pb2.TxnResponse)
_DELETE_RANGE = unary_method(
    "KV", "DeleteRange",
    rpc_pb2.DeleteRangeRequest, rpc_pb2.DeleteRangeResponse,
)
_COMPACT = unary_method(
    "KV", "Compact", rpc_pb2.CompactionRequest, rpc_pb2.CompactionResponse,
)

# "Read the whole keyspace" is spelled with a single NUL for both ends.
_ALL_KEYS = b"\0"


# ---------------------------------------------------------------------------
# Key ranges
# ---------------------------------------------------------------------------

def prefix_range_end(prefix: bytes) -> bytes:
    """The exclusive upper bound covering every key under ``prefix``.

    etcd has no prefix operator; a prefix scan is a range whose end is the
    prefix with its last non-``0xFF`` byte incremented. Implemented here rather
    than borrowed so the one edge case is explicit: a prefix that is entirely
    ``0xFF`` (or empty) has no such successor, and the range must instead run to
    the end of the keyspace, which etcd spells as a single NUL byte.
    """
    end = bytearray(prefix)
    for index in range(len(end) - 1, -1, -1):
        if end[index] < 0xFF:
            end[index] += 1
            return bytes(end[: index + 1])
    return _ALL_KEYS


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RangeResult:
    """One page of a range read."""

    kvs: tuple[kv_pb2.KeyValue, ...]
    revision: int
    """Store revision the response was served at. On the first preload page
    this becomes the snapshot revision every later page is pinned to."""

    more: bool
    """True when the range was truncated by ``limit``; page again from the last
    key plus a NUL byte."""

    count: int
    """Total keys in the range, ignoring ``limit``."""


@dataclass(frozen=True)
class TxnResult:
    """Outcome of a transaction."""

    succeeded: bool
    """True when every comparison held and the success branch ran."""

    revision: int
    """Revision the transaction committed at -- the revision the post-commit
    application fence waits for. Also set when ``succeeded`` is False, in which
    case it is simply the revision the failed compare was evaluated at."""

    responses: tuple[rpc_pb2.ResponseOp, ...]
    """Results of whichever branch ran. The failure branch is what makes a lost
    CAS cheap: it carries the authoritative values, so a retry needs no second
    round trip."""


# ---------------------------------------------------------------------------
# KV client
# ---------------------------------------------------------------------------

class EtcdKV:
    """Typed KV operations over a channel pool."""

    __slots__ = ("_pool",)

    def __init__(self, pool: EtcdChannelPool) -> None:
        self._pool = pool

    async def range_at(
        self,
        key: bytes,
        *,
        range_end: bytes | None = None,
        revision: int = 0,
        limit: int = 0,
        sort_by_key: bool = False,
        timeout: float | None = None,
    ) -> RangeResult:
        """Read a key or range.

        Always linearizable: ``serializable`` is left false so the read goes
        through the leader. A serializable read is faster and would be tempting
        for Query, but Query never reads etcd at all — every read here is either
        building the authoritative snapshot or feeding a fence, and both are
        exactly the places where a stale local read would be wrong.

        Args:
            revision: Pin the read to this store revision. Zero reads the
                latest. Raises ``EtcdCompacted`` if the revision is gone.
        """
        request = rpc_pb2.RangeRequest(
            key=key,
            range_end=range_end or b"",
            revision=revision,
            limit=limit,
            serializable=False,
        )
        if sort_by_key:
            request.sort_order = rpc_pb2.RangeRequest.ASCEND
            request.sort_target = rpc_pb2.RangeRequest.KEY

        response = await self._pool.call(_RANGE, request, timeout=timeout)
        return RangeResult(
            kvs=tuple(response.kvs),
            revision=response.header.revision,
            more=response.more,
            count=response.count,
        )

    async def range_prefix_at(
        self,
        prefix: bytes,
        *,
        revision: int = 0,
        limit: int = 0,
        start_after: bytes | None = None,
        timeout: float | None = None,
    ) -> RangeResult:
        """Read one sorted page of everything under ``prefix``.

        Args:
            start_after: Resume paging above this key. The caller passes the
                last key of the previous page; a NUL byte is appended here to
                make the bound exclusive, which is the standard etcd paging
                idiom and avoids re-reading the boundary key into the snapshot
                twice.
        """
        key = prefix if start_after is None else start_after + b"\0"
        return await self.range_at(
            key,
            range_end=prefix_range_end(prefix),
            revision=revision,
            limit=limit,
            sort_by_key=True,
            timeout=timeout,
        )

    async def read_set(
        self, keys: Sequence[bytes], *, timeout: float | None = None,
    ) -> TxnResult:
        """Read several keys atomically at one revision.

        A transaction with no comparisons: the success branch always runs, so
        this is a multi-key linearizable read that returns a single revision
        covering all of them. Issuing separate reads instead would give each key
        its own revision, and the fence would have nothing coherent to wait for.
        """
        return await self.txn(
            compare=(),
            success=tuple(range_op(key) for key in keys),
            timeout=timeout,
        )

    async def txn(
        self,
        *,
        compare: Sequence[rpc_pb2.Compare] = (),
        success: Sequence[rpc_pb2.RequestOp] = (),
        failure: Sequence[rpc_pb2.RequestOp] = (),
        timeout: float | None = None,
    ) -> TxnResult:
        """Run a compare-and-swap transaction."""
        response = await self._pool.call(
            _TXN,
            rpc_pb2.TxnRequest(
                compare=list(compare),
                success=list(success),
                failure=list(failure),
            ),
            timeout=timeout,
        )
        return TxnResult(
            succeeded=response.succeeded,
            revision=response.header.revision,
            responses=tuple(response.responses),
        )

    async def delete_prefix(
        self, prefix: bytes, *, timeout: float | None = None,
    ) -> int:
        """Delete every key under ``prefix``. Returns how many were removed."""
        response = await self._pool.call(
            _DELETE_RANGE,
            rpc_pb2.DeleteRangeRequest(
                key=prefix, range_end=prefix_range_end(prefix),
            ),
            timeout=timeout,
        )
        return int(response.deleted)

    async def compact(
        self, revision: int, *, physical: bool = False,
        timeout: float | None = None,
    ) -> None:
        """Discard history below ``revision``.

        Not used on any registry path — the registry is a *victim* of
        compaction, not a driver of it, and reacts by resnapshotting. Provided
        because the test suite has to be able to force the compaction-recovery
        path deliberately rather than wait for a real one.
        """
        await self._pool.call(
            _COMPACT,
            rpc_pb2.CompactionRequest(revision=revision, physical=physical),
            timeout=timeout,
        )


# ---------------------------------------------------------------------------
# Comparison and operation builders
# ---------------------------------------------------------------------------

def compare_mod(key: bytes, mod_revision: int) -> rpc_pb2.Compare:
    """"This key was last modified at exactly ``mod_revision``".

    The workhorse of the CAS path: it fails if anyone changed the key since it
    was read or since the local watch last applied it, which is what makes a
    speculative transaction safe to submit from a possibly-stale belief.
    """
    return rpc_pb2.Compare(
        result=rpc_pb2.Compare.EQUAL,
        target=rpc_pb2.Compare.MOD,
        key=key,
        mod_revision=mod_revision,
    )


def compare_create(key: bytes, create_revision: int) -> rpc_pb2.Compare:
    """"This key was created at exactly ``create_revision``".

    Used for parent and Node keys. Creation revision rather than modification
    revision on purpose: a parent being *updated* concurrently must not
    invalidate a child's registration, but a parent being deleted and recreated
    must, and only the creation revision distinguishes those.
    """
    return rpc_pb2.Compare(
        result=rpc_pb2.Compare.EQUAL,
        target=rpc_pb2.Compare.CREATE,
        key=key,
        create_revision=create_revision,
    )


def compare_exists(key: bytes) -> rpc_pb2.Compare:
    """"This key exists", however many times it has been rewritten.

    Used for parent and Node keys on the registration path. Existence rather
    than a specific revision is the right predicate there: a parent being
    *updated* concurrently must not invalidate a child's registration, and a
    parent being deleted and recreated is indistinguishable from a re-register,
    which the store already treats as valid.

    The dangerous case -- a parent deleted while a child is being written -- is
    covered by the child's own compare instead: a Node delete ranges over the
    whole subtree, so the child key goes with it and its ``compare_mod`` fails.
    """
    return rpc_pb2.Compare(
        result=rpc_pb2.Compare.GREATER,
        target=rpc_pb2.Compare.CREATE,
        key=key,
        create_revision=0,
    )


def compare_absent(key: bytes) -> rpc_pb2.Compare:
    """"This key does not exist".

    etcd has no absence predicate; absence is *create_revision == 0*, because a
    key that has never existed has no creation revision. This is the idiom
    behind create-if-absent and behind reclaiming a stale ID claim only once the
    resource it points at is really gone.
    """
    return compare_create(key, 0)


def put_op(key: bytes, value: bytes, *, lease: int = 0) -> rpc_pb2.RequestOp:
    """Write a key, optionally attached to a lease.

    Attaching to a Node's lease is the whole of distributed garbage collection:
    when the Node stops heartbeating, etcd removes every key on that lease
    without anyone running a collection pass.
    """
    return rpc_pb2.RequestOp(
        request_put=rpc_pb2.PutRequest(key=key, value=value, lease=lease),
    )


def range_op(key: bytes, *, range_end: bytes | None = None) -> rpc_pb2.RequestOp:
    """Read a key or range inside a transaction."""
    return rpc_pb2.RequestOp(
        request_range=rpc_pb2.RangeRequest(
            key=key, range_end=range_end or b"",
        ),
    )


def delete_op(
    key: bytes, *, range_end: bytes | None = None,
) -> rpc_pb2.RequestOp:
    """Delete a key or range inside a transaction."""
    return rpc_pb2.RequestOp(
        request_delete_range=rpc_pb2.DeleteRangeRequest(
            key=key, range_end=range_end or b"",
        ),
    )


def delete_prefix_op(prefix: bytes) -> rpc_pb2.RequestOp:
    """Delete a whole subtree inside a transaction."""
    return delete_op(prefix, range_end=prefix_range_end(prefix))


def first_kv(response: rpc_pb2.ResponseOp) -> kv_pb2.KeyValue | None:
    """The single KeyValue from a range response op, or None if absent.

    Reading a transaction's results positionally is easy to get subtly wrong --
    an empty ``kvs`` means the key does not exist, which is a meaningful answer
    and not an error -- so the unwrapping lives here instead of at every call
    site.
    """
    kvs = response.response_range.kvs
    return kvs[0] if kvs else None
