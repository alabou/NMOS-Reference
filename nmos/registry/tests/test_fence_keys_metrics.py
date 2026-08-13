# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for the revision fence, the etcd key layout, and the metric buffer."""

from __future__ import annotations

import asyncio

import pytest

from nmos.registry.fence import FenceTimeout, RevisionFence
from nmos.registry.keys import (
    ENVELOPE_VERSION,
    Envelope,
    KeyError_,
    Namespace,
)
from nmos.registry.metrics import Event, RegistryMetrics
from nmos.registry.tests._fixtures import make_node
from nmos.registry.types import Body, ResourceType, TaiCursor

NS = Namespace("/nmos-reference/registry/v1")
NODE = "11111111-1111-1111-8111-111111111111"
DEVICE = "22222222-2222-2222-8222-222222222222"
SENDER = "33333333-3333-3333-8333-333333333333"


# ---------------------------------------------------------------------------
# Fence
# ---------------------------------------------------------------------------

async def test_wait_returns_immediately_when_already_applied() -> None:
    fence = RevisionFence(applied=10)
    await fence.wait(10, timeout=0.1)
    await fence.wait(5, timeout=0.1)


async def test_wait_blocks_until_advanced() -> None:
    fence = RevisionFence(applied=1)
    waiter = asyncio.create_task(fence.wait(5, timeout=5.0))
    await asyncio.sleep(0)
    assert not waiter.done()

    await fence.advance(4)
    await asyncio.sleep(0)
    assert not waiter.done()

    await fence.advance(5)
    await waiter


async def test_wait_times_out_with_both_revisions_named() -> None:
    """The message has to name what it waited for AND what it got."""
    fence = RevisionFence(applied=3)
    with pytest.raises(FenceTimeout, match="at revision 3.*waiting for 9"):
        await fence.wait(9, timeout=0.1)


async def test_many_waiters_wake_at_their_own_revisions() -> None:
    """notify_all wakes everyone; each must re-check its own target."""
    fence = RevisionFence(applied=0)
    waiters = {
        revision: asyncio.create_task(fence.wait(revision, timeout=5.0))
        for revision in (2, 4, 6)
    }

    await fence.advance(4)
    await asyncio.sleep(0.01)
    assert waiters[2].done() and waiters[4].done()
    assert not waiters[6].done()

    await fence.advance(6)
    await asyncio.gather(*waiters.values())


async def test_fence_never_moves_backwards() -> None:
    """A watch reconnect can redeliver revisions at or below the applied one."""
    fence = RevisionFence(applied=10)
    await fence.advance(4)
    assert fence.applied == 10


async def test_reset_repositions_after_a_resnapshot() -> None:
    fence = RevisionFence(applied=100)
    await fence.reset(7)
    assert fence.applied == 7


async def test_waiter_count_is_released_on_timeout() -> None:
    fence = RevisionFence(applied=0)
    with pytest.raises(FenceTimeout):
        await fence.wait(5, timeout=0.05)
    assert fence.waiters == 0


async def test_seeding_avoids_the_quiet_cluster_deadlock() -> None:
    """The rule the etcd watch-progress behaviour forces.

    After a preload at revision R the watch opens at R+1. etcd answers a
    progress request only when `rev >= w.startRev`, so on a cluster with no
    writes since the preload NO progress reply ever arrives. A fence seeded to
    zero would block for its full deadline on every startup; seeded to R, the
    recovery fence is already satisfied and never asks.
    """
    preload_revision = 42

    seeded = RevisionFence(applied=preload_revision)
    assert seeded.satisfied(preload_revision)
    await seeded.wait(preload_revision, timeout=0.05)

    unseeded = RevisionFence(applied=0)
    assert not unseeded.satisfied(preload_revision)
    with pytest.raises(FenceTimeout):
        await unseeded.wait(preload_revision, timeout=0.05)


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

def test_namespace_rejects_malformed_prefixes() -> None:
    with pytest.raises(KeyError_, match="must start with"):
        Namespace("nmos/registry")
    with pytest.raises(KeyError_, match="must not end with"):
        Namespace("/nmos/registry/")


def test_a_nodes_whole_subtree_shares_one_prefix() -> None:
    """The property the entire GC and delete design rests on."""
    subtree = NS.node_subtree(NODE)
    for key in (
        NS.node(NODE),
        NS.device(NODE, DEVICE),
        NS.child(ResourceType.SENDER, NODE, DEVICE, SENDER),
    ):
        assert key.startswith(subtree)


def test_different_nodes_do_not_share_a_prefix() -> None:
    """Why unrelated registrations never contend."""
    other = "44444444-4444-4444-8444-444444444444"
    assert not NS.node_subtree(NODE).startswith(NS.node_subtree(other))


def test_round_trip_node_key() -> None:
    parsed = NS.parse(NS.node(NODE))
    assert parsed is not None
    assert parsed.resource_type is ResourceType.NODE
    assert parsed.resource_id == NODE
    assert parsed.node_id == NODE
    assert parsed.device_id is None
    assert parsed.depth == 0


def test_round_trip_device_key() -> None:
    parsed = NS.parse(NS.device(NODE, DEVICE))
    assert parsed is not None
    assert parsed.resource_type is ResourceType.DEVICE
    assert parsed.resource_id == DEVICE
    assert parsed.node_id == NODE
    assert parsed.device_id == DEVICE
    assert parsed.depth == 1


@pytest.mark.parametrize(
    "resource_type",
    [
        ResourceType.SOURCE, ResourceType.FLOW,
        ResourceType.SENDER, ResourceType.RECEIVER,
    ],
)
def test_round_trip_child_keys(resource_type: ResourceType) -> None:
    key = NS.child(resource_type, NODE, DEVICE, SENDER)
    parsed = NS.parse(key)
    assert parsed is not None
    assert parsed.resource_type is resource_type
    assert parsed.resource_id == SENDER
    assert parsed.node_id == NODE
    assert parsed.device_id == DEVICE
    assert parsed.depth == 2


def test_depth_orders_parents_before_children() -> None:
    """Registration order is normative; a revision creating a Device and its
    Senders together must apply in that order or referential integrity fails."""
    keys = [
        NS.child(ResourceType.SENDER, NODE, DEVICE, SENDER),
        NS.node(NODE),
        NS.device(NODE, DEVICE),
    ]
    parsed = [NS.parse(key) for key in keys]
    ordered = sorted(
        [p for p in parsed if p is not None], key=lambda p: p.depth,
    )
    assert [p.resource_type for p in ordered] == [
        ResourceType.NODE, ResourceType.DEVICE, ResourceType.SENDER,
    ]


def test_bookkeeping_keys_parse_to_none_rather_than_raising() -> None:
    """The watch sees every key; meta and id claims have no store representation."""
    assert NS.parse(NS.meta_config) is None
    assert NS.parse(NS.id_claim(NODE)) is None


def test_key_outside_the_namespace_is_an_error() -> None:
    with pytest.raises(KeyError_, match="outside namespace"):
        NS.parse(b"/somewhere/else/nodes/x/self")


def test_malformed_resource_key_is_an_error() -> None:
    with pytest.raises(KeyError_, match="malformed"):
        NS.parse(f"{NS.prefix}/nodes/{NODE}".encode())


def test_unknown_collection_is_an_error() -> None:
    with pytest.raises(KeyError_, match="unknown collection"):
        NS.parse(
            f"{NS.prefix}/nodes/{NODE}/devices/{DEVICE}/widgets/{SENDER}".encode(),
        )


def test_node_and_device_reject_the_generic_child_builder() -> None:
    with pytest.raises(KeyError_, match="own key function"):
        NS.child(ResourceType.NODE, NODE, DEVICE, NODE)


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------

def test_envelope_round_trips_the_resource_verbatim() -> None:
    """The Query API serves what was registered, byte for byte -- including
    attributes the generated types do not model."""
    raw = make_node()
    raw["x-vendor-extension"] = {"kept": True}

    envelope = Envelope(
        version=ENVELOPE_VERSION,
        resource_type=ResourceType.NODE,
        body=Body.from_data(raw),
        created=TaiCursor(100, 5),
        updated=TaiCursor(200, 7),
        health=1234,
    )
    decoded = Envelope.decode(envelope.encode())

    assert decoded.raw == raw
    assert decoded.raw["x-vendor-extension"] == {"kept": True}
    assert decoded.resource_type is ResourceType.NODE
    assert decoded.created == TaiCursor(100, 5)
    assert decoded.updated == TaiCursor(200, 7)
    assert decoded.health == 1234


def test_envelope_from_a_newer_schema_is_refused() -> None:
    """Better to refuse than to serve a view that differs from peers'."""
    envelope = Envelope(
        version=ENVELOPE_VERSION + 1,
        resource_type=ResourceType.NODE,
        body=Body.from_data(make_node()),
        created=TaiCursor(1, 0),
        updated=TaiCursor(1, 0),
        health=1,
    )
    with pytest.raises(KeyError_, match="newer than this registry"):
        Envelope.decode(envelope.encode())


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"not json", "not valid JSON"),
        (b"[]", "not a JSON object"),
        (b'{"type":"node"}', "no integer 'v'"),
        (b'{"v":1,"type":"nope","data":{}}', "unknown type"),
        (b'{"v":1,"type":"node"}', "no 'data' object"),
        (b'{"v":1,"type":"node","data":{}}', "no string 'created'"),
    ],
)
def test_malformed_envelopes_are_refused(payload: bytes, message: str) -> None:
    with pytest.raises(KeyError_, match=message):
        Envelope.decode(payload)


def test_envelope_rejects_a_malformed_cursor() -> None:
    payload = (
        b'{"v":1,"type":"node","data":{},"created":"nope",'
        b'"updated":"1:0","health":1}'
    )
    with pytest.raises(KeyError_, match="not '<sec>:<nsec>'"):
        Envelope.decode(payload)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def test_counter_tracks_count_mean_and_max() -> None:
    metrics = RegistryMetrics()
    for seconds in (0.001, 0.002, 0.003):
        metrics.record(Event.CAS, seconds)

    counter = metrics.counter(Event.CAS)
    assert counter.count == 3
    assert counter.max_seconds == pytest.approx(0.003)
    assert counter.mean_seconds == pytest.approx(0.002)


def test_percentile_returns_a_value_that_actually_occurred() -> None:
    """Nearest-rank, not interpolated: an interpolated p99 is a number the
    system never produced."""
    metrics = RegistryMetrics()
    for seconds in [0.001] * 99 + [0.500]:
        metrics.record(Event.CAS, seconds)
    assert metrics.counter(Event.CAS).percentile(1.0) == pytest.approx(0.500)
    assert metrics.counter(Event.CAS).percentile(0.50) == pytest.approx(0.001)


def test_trace_records_the_decision_driving_inputs() -> None:
    """A trace saying "CAS retried" is useless; one naming the failed compare
    and the revisions is not."""
    metrics = RegistryMetrics()
    metrics.record(
        Event.CAS_RETRY, 0.004,
        compare="mod_revision", believed=41, actual=47,
    )

    sample = metrics.recent(Event.CAS_RETRY)[-1]
    assert sample.detail["believed"] == 41
    assert sample.detail["actual"] == 47
    assert "believed=41" in sample.render()


def test_trace_is_bounded() -> None:
    """An unbounded trace during a registration storm is itself a problem."""
    metrics = RegistryMetrics(trace_capacity=16)
    for index in range(100):
        metrics.record(Event.WATCH_BATCH, 0.001, index=index)
    recent = metrics.recent(limit=1000)
    assert len(recent) == 16
    assert recent[-1].detail["index"] == 99


def test_timer_records_duration_and_late_inputs() -> None:
    metrics = RegistryMetrics()
    with metrics.timer(Event.FENCE_WAIT, target=50) as timer:
        timer.note(applied=50)

    sample = metrics.recent(Event.FENCE_WAIT)[-1]
    assert sample.detail == {"target": 50, "applied": 50}
    assert sample.seconds is not None


def test_timer_records_on_failure_too() -> None:
    """A path that is slow only when it errors is real and easily missed."""
    metrics = RegistryMetrics()
    with pytest.raises(ValueError):
        with metrics.timer(Event.CAS):
            raise ValueError("boom")

    sample = metrics.recent(Event.CAS)[-1]
    assert sample.detail["failed"] == "ValueError"
    assert metrics.counter(Event.CAS).count == 1


def test_fast_path_hit_rate() -> None:
    """The number that most directly predicts registration latency."""
    metrics = RegistryMetrics()
    assert metrics.fast_path_hit_rate == 0.0

    for _ in range(9):
        metrics.record(Event.FAST_PATH_HIT, 0.001)
    metrics.record(Event.FAST_PATH_MISS, 0.004)
    assert metrics.fast_path_hit_rate == pytest.approx(0.9)


def test_snapshot_is_machine_readable() -> None:
    metrics = RegistryMetrics()
    metrics.record(Event.QUERY, 0.002)
    snapshot = metrics.snapshot()
    assert snapshot["query"]["count"] == 1
    assert snapshot["query"]["p50_ms"] == pytest.approx(2.0)
