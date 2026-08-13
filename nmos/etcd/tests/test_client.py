# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for the etcd client, against a real server.

Marked ``e2e`` so they stay out of the default gate: they need an etcd binary,
which is an optional install. Run them with::

    pytest nmos/etcd/tests -m e2e

Each test names the behaviour the registry design depends on, not just the API
call, because that is what a failure here actually means.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from nmos.etcd.channel import (
    EtcdChannelPool,
    parse_endpoints,
    unary_method,
)
from nmos.etcd.errors import EtcdCompacted, EtcdError, EtcdLeaseNotFound
from nmos.etcd.generated import kv_pb2, rpc_pb2
from nmos.etcd.kv import (
    EtcdKV,
    compare_absent,
    compare_mod,
    delete_prefix_op,
    first_kv,
    prefix_range_end,
    put_op,
    range_op,
)
from nmos.etcd.lease import EtcdLease
from nmos.etcd.watch import EtcdWatch

pytestmark = pytest.mark.e2e


@pytest.fixture
async def pool(etcd_endpoint: str) -> AsyncIterator[EtcdChannelPool]:
    created = EtcdChannelPool(
        parse_endpoints([etcd_endpoint]),
        credentials=None,
        target_name=None,
        rpc_timeout=5.0,
    )
    try:
        yield created
    finally:
        await created.close()


@pytest.fixture
def kv(pool: EtcdChannelPool) -> EtcdKV:
    return EtcdKV(pool)


# ---------------------------------------------------------------------------
# Key ranges
# ---------------------------------------------------------------------------

def test_prefix_range_end_increments_last_byte() -> None:
    assert prefix_range_end(b"/a/") == b"/a0"
    assert prefix_range_end(b"/ab") == b"/ac"


def test_prefix_range_end_handles_trailing_0xff() -> None:
    """A prefix ending in 0xFF has no successor at that byte."""
    assert prefix_range_end(b"/a\xff") == b"/b"


def test_prefix_range_end_of_all_0xff_is_whole_keyspace() -> None:
    """The one case with no successor at all must scan to the end."""
    assert prefix_range_end(b"\xff\xff") == b"\0"
    assert prefix_range_end(b"") == b"\0"


# ---------------------------------------------------------------------------
# Method paths
# ---------------------------------------------------------------------------

def test_unknown_method_fails_at_declaration() -> None:
    """A typo'd RPC name must fail at import, not at first call.

    `Compaction` is the plausible-looking wrong spelling of `Compact` -- it
    matches the request message name -- and it is used only on the
    compaction-recovery path, so a runtime-only failure could easily first
    surface in production.
    """
    with pytest.raises(EtcdError, match="unknown method 'Compaction'"):
        unary_method(
            "KV", "Compaction",
            rpc_pb2.CompactionRequest, rpc_pb2.CompactionResponse,
        )


def test_unknown_service_fails_at_declaration() -> None:
    with pytest.raises(EtcdError, match="unknown etcd service"):
        unary_method(
            "Nope", "Range", rpc_pb2.RangeRequest, rpc_pb2.RangeResponse,
        )


def test_service_accepts_short_and_full_names() -> None:
    short = unary_method(
        "KV", "Range", rpc_pb2.RangeRequest, rpc_pb2.RangeResponse,
    )
    full = unary_method(
        "etcdserverpb.KV", "Range", rpc_pb2.RangeRequest, rpc_pb2.RangeResponse,
    )
    assert short.path == full.path == "/etcdserverpb.KV/Range"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def test_local_endpoint_sorts_first() -> None:
    """The co-located member is tried first, so the common case has no hop."""
    endpoints = parse_endpoints(
        ["https://b:2381", "https://a:2381", "https://c:2381"],
        local_target="c:2381",
    )
    assert [e.target for e in endpoints] == ["c:2381", "b:2381", "a:2381"]
    assert endpoints[0].local is True


def test_endpoint_scheme_is_optional() -> None:
    """Operators paste the same strings they give etcd; gRPC targets have no scheme."""
    assert parse_endpoints(["https://h:1"])[0].target == "h:1"
    assert parse_endpoints(["http://h:1"])[0].target == "h:1"
    assert parse_endpoints(["h:1"])[0].target == "h:1"


def test_endpoint_without_port_is_rejected() -> None:
    with pytest.raises(EtcdError, match="no port"):
        parse_endpoints(["https://h"])


# ---------------------------------------------------------------------------
# Range and revision pinning
# ---------------------------------------------------------------------------

async def test_range_pinned_to_revision_ignores_later_writes(
    kv: EtcdKV,
) -> None:
    """The preload's core guarantee: pages cannot see concurrent writes.

    Without this, a snapshot taken page-by-page could contain a resource that
    was deleted before the last page and miss one created after the first.
    """
    await kv.txn(success=[put_op(b"/pin/a", b"1")])
    snapshot = await kv.range_at(b"/pin/a")
    revision = snapshot.revision

    await kv.txn(success=[put_op(b"/pin/a", b"2")])
    await kv.txn(success=[put_op(b"/pin/b", b"new")])

    pinned = await kv.range_prefix_at(b"/pin/", revision=revision)
    assert [(k.key, k.value) for k in pinned.kvs] == [(b"/pin/a", b"1")]

    latest = await kv.range_prefix_at(b"/pin/")
    assert {k.key for k in latest.kvs} == {b"/pin/a", b"/pin/b"}


async def test_range_prefix_pages_without_repeating_the_boundary(
    kv: EtcdKV,
) -> None:
    """Paging must not re-read the last key of the previous page."""
    for index in range(5):
        await kv.txn(success=[put_op(f"/page/{index}".encode(), b"x")])

    first = await kv.range_prefix_at(b"/page/", limit=2)
    assert [k.key for k in first.kvs] == [b"/page/0", b"/page/1"]
    assert first.more is True

    second = await kv.range_prefix_at(
        b"/page/", limit=2, start_after=first.kvs[-1].key,
    )
    assert [k.key for k in second.kvs] == [b"/page/2", b"/page/3"]


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

async def test_cas_failure_branch_returns_authoritative_values(
    kv: EtcdKV,
) -> None:
    """The fast path's fallback must cost no extra round trip.

    A speculative CAS built from a stale believed revision fails its compare;
    the failure branch carries the current value and revision, so the
    revalidation that follows needs no second read.
    """
    await kv.txn(success=[put_op(b"/cas/a", b"1")])
    stale = await kv.range_at(b"/cas/a")
    stale_revision = stale.kvs[0].mod_revision

    await kv.txn(success=[put_op(b"/cas/a", b"2")])

    result = await kv.txn(
        compare=[compare_mod(b"/cas/a", stale_revision)],
        success=[put_op(b"/cas/a", b"3")],
        failure=[range_op(b"/cas/a")],
    )

    assert result.succeeded is False
    recovered = first_kv(result.responses[0])
    assert recovered is not None
    assert recovered.value == b"2"
    assert recovered.mod_revision > stale_revision


async def test_create_if_absent_uses_create_revision_zero(kv: EtcdKV) -> None:
    """Absence is create_revision == 0; getting this wrong silently overwrites."""
    first = await kv.txn(
        compare=[compare_absent(b"/claim/x")],
        success=[put_op(b"/claim/x", b"owner-1")],
    )
    assert first.succeeded is True

    second = await kv.txn(
        compare=[compare_absent(b"/claim/x")],
        success=[put_op(b"/claim/x", b"owner-2")],
        failure=[range_op(b"/claim/x")],
    )
    assert second.succeeded is False
    held = first_kv(second.responses[0])
    assert held is not None
    assert held.value == b"owner-1"


async def test_read_set_returns_one_revision_for_every_key(kv: EtcdKV) -> None:
    """The read fence needs a single coherent revision across all its keys."""
    await kv.txn(success=[put_op(b"/rs/a", b"1"), put_op(b"/rs/b", b"2")])

    result = await kv.read_set([b"/rs/a", b"/rs/b", b"/rs/missing"])

    assert result.succeeded is True
    assert result.revision > 0
    assert first_kv(result.responses[0]) is not None
    assert first_kv(result.responses[1]) is not None
    # An absent key is a meaningful answer, not an error.
    assert first_kv(result.responses[2]) is None


# ---------------------------------------------------------------------------
# Leases
# ---------------------------------------------------------------------------

async def test_lease_expiry_removes_the_whole_subtree(
    pool: EtcdChannelPool, kv: EtcdKV,
) -> None:
    """Distributed GC in one assertion: one lease, whole Node subtree."""
    lease = EtcdLease(pool)
    granted = await lease.grant(2)

    await kv.txn(success=[
        put_op(b"/lease/node", b"n", lease=granted.id),
        put_op(b"/lease/node/dev", b"d", lease=granted.id),
        put_op(b"/lease/ids/abc", b"claim", lease=granted.id),
    ])
    assert (await kv.range_prefix_at(b"/lease/")).count == 3

    status = await lease.time_to_live(granted.id, with_keys=True)
    assert set(status.keys) == {
        b"/lease/node", b"/lease/node/dev", b"/lease/ids/abc",
    }

    await lease.revoke(granted.id)
    assert (await kv.range_prefix_at(b"/lease/")).count == 0


async def test_keepalive_on_dead_lease_raises_not_found(
    pool: EtcdChannelPool,
) -> None:
    """A renewal for a collected Node must be authoritative, not transient.

    etcd answers TTL 0 rather than failing the RPC. Treating that as success
    would keep a Node the cluster already collected alive in the local view.
    """
    lease = EtcdLease(pool)
    granted = await lease.grant(2)
    await lease.revoke(granted.id)

    with pytest.raises(EtcdLeaseNotFound):
        await lease.keepalive_once(granted.id)


async def test_keepalive_extends_a_live_lease(pool: EtcdChannelPool) -> None:
    lease = EtcdLease(pool)
    granted = await lease.grant(5)
    assert await lease.keepalive_once(granted.id) > 0
    await lease.revoke(granted.id)


async def test_revoking_a_dead_lease_is_not_an_error(
    pool: EtcdChannelPool,
) -> None:
    """Node deletion revokes best-effort after the subtree transaction."""
    lease = EtcdLease(pool)
    granted = await lease.grant(2)
    await lease.revoke(granted.id)
    await lease.revoke(granted.id)


# ---------------------------------------------------------------------------
# Watch
# ---------------------------------------------------------------------------

async def _collect(
    stream: object, count: int, timeout: float = 10.0,
) -> list[object]:
    """Read ``count`` batches, failing loudly rather than hanging forever."""
    batches: list[object] = []

    async def drain() -> None:
        async for batch in stream:  # type: ignore[attr-defined]
            batches.append(batch)
            if len(batches) >= count:
                return

    await asyncio.wait_for(drain(), timeout=timeout)
    return batches


async def test_watch_groups_a_transaction_into_one_revision(
    pool: EtcdChannelPool, kv: EtcdKV,
) -> None:
    """A multi-key transaction must arrive as ONE batch.

    This is what lets the registry apply a Node delete and every descendant as
    a single uninterrupted step, and it rests on etcd's guarantee that a
    revision is never split across responses.
    """
    head = await kv.range_at(b"/w1/none")
    stream = EtcdWatch(pool).open(b"/w1/", start_revision=head.revision + 1)

    async with stream:
        await kv.txn(success=[
            put_op(b"/w1/a", b"1"),
            put_op(b"/w1/b", b"2"),
            put_op(b"/w1/c", b"3"),
        ])
        batches = await _collect(stream, 1)

    batch = batches[0]
    assert batch.events  # type: ignore[attr-defined]
    assert len(batch.events) == 3  # type: ignore[attr-defined]
    assert {e.kv.key for e in batch.events} == {  # type: ignore[attr-defined]
        b"/w1/a", b"/w1/b", b"/w1/c",
    }
    revisions = {e.kv.mod_revision for e in batch.events}  # type: ignore[attr-defined]
    assert len(revisions) == 1


async def test_watch_delete_carries_previous_value(
    pool: EtcdChannelPool, kv: EtcdKV,
) -> None:
    """Removal grains must carry the resource's final content.

    A DELETE event has no value of its own; only prev_kv has it. Without this
    the registry could not publish what was removed -- and the legacy dRDS's
    origin-index byte, read from that empty value, was always zero.
    """
    await kv.txn(success=[put_op(b"/w2/a", b"body")])
    head = await kv.range_at(b"/w2/a")

    stream = EtcdWatch(pool).open(b"/w2/", start_revision=head.revision + 1)
    async with stream:
        await kv.txn(success=[delete_prefix_op(b"/w2/")])
        batches = await _collect(stream, 1)

    events = batches[0].events  # type: ignore[attr-defined]
    assert len(events) == 1
    assert events[0].type == kv_pb2.Event.DELETE
    assert events[0].kv.value == b""
    assert events[0].prev_kv.value == b"body"


async def test_watch_progress_request_yields_a_progress_batch(
    pool: EtcdChannelPool, kv: EtcdKV,
) -> None:
    """The fence's liveness mechanism.

    A progress reply proves everything through its revision was delivered --
    etcd only answers when the watcher is synced.
    """
    head = await kv.range_at(b"/w3/none")
    stream = EtcdWatch(pool).open(b"/w3/", start_revision=head.revision + 1)

    async with stream:
        # The store revision must have reached the watch's start revision
        # before etcd will answer a progress request at all -- see the next
        # test, and the module docstring, for why.
        await kv.txn(success=[put_op(b"/w3/a", b"1")])
        written = await _collect(stream, 1)
        assert written[0].events  # type: ignore[attr-defined]

        await stream.request_progress()
        batches = await _collect(stream, 1)

    assert batches[0].progress_only is True  # type: ignore[attr-defined]
    assert batches[0].revision >= written[0].revision  # type: ignore[attr-defined]


async def test_watch_progress_is_withheld_until_the_start_revision_is_reached(
    pool: EtcdChannelPool, kv: EtcdKV,
) -> None:
    """etcd answers no progress request while `rev < startRev`.

    This is the deadlock the fence must be designed around, not retried into:
    a watch opened at `preload_revision + 1` with no writes since gets NOTHING
    back from a progress request, because etcd's progressIfSync bails on
    `rev < w.startRev`.

    The resolution is initialisation -- seed `last_applied_revision` to
    `start_revision - 1`, so a fence target at or below the preload revision is
    already satisfied and never asks. If this test ever starts passing a
    progress batch through, the fence's seeding rule can be revisited; until
    then it is mandatory.
    """
    head = await kv.range_at(b"/w6/none")
    stream = EtcdWatch(pool).open(b"/w6/", start_revision=head.revision + 1)

    async with stream:
        await stream.request_progress()
        with pytest.raises(asyncio.TimeoutError):
            await _collect(stream, 1, timeout=2.0)


async def test_watch_on_compacted_revision_raises_etcd_compacted(
    pool: EtcdChannelPool, kv: EtcdKV,
) -> None:
    """Compaction must be distinguishable from a dropped connection.

    Resuming a watch at a compacted revision cannot succeed; treating it as a
    transient reconnect would silently skip a range of revisions and leave two
    registries permanently disagreeing.
    """
    await kv.txn(success=[put_op(b"/w4/a", b"1")])
    await kv.txn(success=[put_op(b"/w4/a", b"2")])
    head = await kv.range_at(b"/w4/a")

    await kv.compact(head.revision)

    with pytest.raises(EtcdCompacted) as caught:
        stream = EtcdWatch(pool).open(b"/w4/", start_revision=1)
        async with stream:
            await _collect(stream, 1, timeout=5.0)

    assert caught.value.compact_revision > 0


async def test_range_below_compaction_raises_etcd_compacted(
    kv: EtcdKV,
) -> None:
    """The same failure on the read path, so preload can react identically."""
    await kv.txn(success=[put_op(b"/w5/a", b"1")])
    head = await kv.range_at(b"/w5/a")
    await kv.compact(head.revision)

    with pytest.raises(EtcdCompacted):
        await kv.range_prefix_at(b"/w5/", revision=1)
