# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Compaction, and catching up a member the log can no longer reach.

These are halves of one mechanism. A log that is never compacted grows for the
life of the cluster and every member holds all of it in memory -- which for an
in-memory log is not an optimisation but a precondition. And the moment
entries are discarded, a follower that falls behind them can no longer be
caught up by replication at all, so the snapshot transfer stops being a
nicety and becomes the only path back.

Testing either one alone would prove nothing useful: compaction without
transfer strands members, and transfer without compaction never runs.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from nmos.raft.node import RaftTiming, Role
from nmos.raft.operations import ProposalId, RegisterOp
from nmos.raft.tests._harness import FAST, Cluster
from nmos.registry.tests._fixtures import make_node
from nmos.registry.types import ResourceType, TaiCursor

# Small enough that a handful of registrations trips it. The production
# default is 4096; the mechanism is identical and only the arithmetic differs.
EAGER = RaftTiming(
    heartbeat=FAST.heartbeat,
    election_min=FAST.election_min,
    election_max=FAST.election_max,
    compaction_threshold=4,
    max_log_entries=8,
    snapshot_chunk=256,
)


def _node_id(index: int) -> str:
    return f"{index:08x}-0000-4000-8000-0000000000aa"


def _register(index: int, owner: int) -> RegisterOp:
    raw = make_node(_node_id(index))
    return RegisterOp(
        proposal=ProposalId(0, 0),
        resource_type=ResourceType.NODE,
        resource_id=raw["id"],
        node_id=raw["id"],
        body_text=json.dumps(raw),
        created=TaiCursor(1000 + index, 8),
        updated=TaiCursor(1000 + index, 8),
        health=7000 + index,
        expect_created=True,
        claim_owner=owner,
    )


async def _fill(cluster: Cluster, leader: object, count: int) -> list[str]:
    ids = []
    for index in range(count):
        await asyncio.wait_for(
            leader.node.propose(_register(index, leader.index)), 5.0,  # type: ignore[attr-defined]
        )
        ids.append(_node_id(index))
    return ids


class TestCompaction:
    async def test_an_applied_log_is_compacted(self, tmp_path: Path) -> None:
        cluster = Cluster(3, tmp_path, timing=EAGER)
        await cluster.start()
        try:
            leader = await cluster.elect()
            await _fill(cluster, leader, 12)
            await cluster.settle(20)

            assert leader.node.log.snapshot_index > 0, "nothing was compacted"
            assert leader.node.log.entries_held < 12
        finally:
            await cluster.close()

    async def test_compaction_does_not_lose_anything(
        self, tmp_path: Path,
    ) -> None:
        """The store is the snapshot; discarding entries must not touch it."""
        cluster = Cluster(3, tmp_path, timing=EAGER)
        await cluster.start()
        try:
            leader = await cluster.elect()
            ids = await _fill(cluster, leader, 12)
            await cluster.settle(20)

            for member in cluster.members:
                for node_id in ids:
                    assert member.registry.store.get(
                        ResourceType.NODE, node_id,
                    ) is not None, (
                        f"member {member.index} lost {node_id} to compaction"
                    )
        finally:
            await cluster.close()

    async def test_a_reachable_follower_is_not_compacted_past(
        self, tmp_path: Path,
    ) -> None:
        """Discarding an entry a follower has not received strands it.

        So in the normal case the leader compacts only as far as its slowest
        *reachable* follower has confirmed -- which is what ``min(matchIndex)``
        is for.
        """
        cluster = Cluster(3, tmp_path, timing=EAGER)
        await cluster.start()
        try:
            leader = await cluster.elect()
            await _fill(cluster, leader, 12)
            await cluster.settle(20)

            confirmed = min(
                m.node.last_applied for m in cluster.members
                if m is not leader
            )
            assert leader.node.log.snapshot_index <= confirmed
        finally:
            await cluster.close()

    async def test_an_unreachable_member_cannot_grow_the_log_forever(
        self, tmp_path: Path,
    ) -> None:
        """The hard cap. A partial outage must not become an OOM.

        One member being unreachable is a condition the cluster is designed to
        survive; it must not also be a condition under which memory grows
        without bound until the survivors fail too.
        """
        cluster = Cluster(3, tmp_path, timing=EAGER)
        await cluster.start()
        try:
            leader = await cluster.elect()
            outcast = next(m for m in cluster.members if m is not leader)
            cluster.network.stop(outcast.index)
            await cluster.settle(10)

            await _fill(cluster, leader, 30)
            await cluster.settle(20)

            assert leader.node.log.entries_held <= EAGER.max_log_entries, (
                "the log grew past its cap while a member was unreachable"
            )
        finally:
            await cluster.close()


class TestCatchUpBySnapshot:
    async def test_a_stranded_member_is_caught_up_by_state(
        self, tmp_path: Path,
    ) -> None:
        """The end-to-end path: compact past a member, then hand it the store.

        This is the case replication cannot serve. The entries the returning
        member needs no longer exist anywhere, so either it receives the state
        or it never catches up at all.
        """
        cluster = Cluster(3, tmp_path, timing=EAGER)
        await cluster.start()
        try:
            leader = await cluster.elect()
            outcast = next(m for m in cluster.members if m is not leader)

            cluster.network.stop(outcast.index)
            await cluster.settle(10)

            ids = await _fill(cluster, leader, 30)
            await cluster.settle(20)
            assert leader.node.log.snapshot_index > 0

            # It returns to find the entries it needs long gone.
            cluster.network.resume(outcast.index)
            await cluster.settle(60)

            for node_id in ids:
                assert outcast.registry.store.get(
                    ResourceType.NODE, node_id,
                ) is not None, (
                    f"the returning member never received {node_id}; it was "
                    f"stranded below the leader's first retained index"
                )
        finally:
            await cluster.close()

    async def test_a_caught_up_member_matches_the_leader_exactly(
        self, tmp_path: Path,
    ) -> None:
        """Cursors and health included -- not just presence.

        A member restored with locally-allocated cursors would serve a
        different paging order from every peer, and the difference would only
        surface as a client skipping a record.
        """
        cluster = Cluster(3, tmp_path, timing=EAGER)
        await cluster.start()
        try:
            leader = await cluster.elect()
            outcast = next(m for m in cluster.members if m is not leader)
            cluster.network.stop(outcast.index)
            await cluster.settle(10)

            ids = await _fill(cluster, leader, 30)
            await cluster.settle(20)
            cluster.network.resume(outcast.index)
            await cluster.settle(60)

            for node_id in ids:
                mine = outcast.registry.store.get(ResourceType.NODE, node_id)
                theirs = leader.registry.store.get(ResourceType.NODE, node_id)
                assert mine is not None and theirs is not None
                assert mine.created == theirs.created
                assert mine.updated == theirs.updated
                assert mine.health == theirs.health
                assert mine.body.text == theirs.body.text
        finally:
            await cluster.close()

    async def test_ownership_survives_the_transfer(
        self, tmp_path: Path,
    ) -> None:
        """Otherwise the returning member believes every Node is unowned.

        It would then start claiming Nodes that already have owners, which is
        exactly the state the ownership table exists to make impossible.
        """
        cluster = Cluster(3, tmp_path, timing=EAGER)
        await cluster.start()
        try:
            leader = await cluster.elect()
            outcast = next(m for m in cluster.members if m is not leader)
            cluster.network.stop(outcast.index)
            await cluster.settle(10)

            ids = await _fill(cluster, leader, 30)
            await cluster.settle(20)
            cluster.network.resume(outcast.index)
            await cluster.settle(60)

            for node_id in ids:
                assert outcast.ownership.owner_of(node_id) == (
                    leader.ownership.owner_of(node_id)
                )
        finally:
            await cluster.close()

    async def test_the_cluster_keeps_serving_throughout(
        self, tmp_path: Path,
    ) -> None:
        """A snapshot transfer must not cost leadership.

        It travels on BULK precisely so it cannot stall the heartbeats that
        keep the leader's term alive -- otherwise installing a snapshot would
        cause an election, which would cause more members to fall behind.
        """
        cluster = Cluster(3, tmp_path, timing=EAGER)
        await cluster.start()
        try:
            leader = await cluster.elect()
            term_before = leader.node.term
            outcast = next(m for m in cluster.members if m is not leader)

            cluster.network.stop(outcast.index)
            await cluster.settle(10)
            await _fill(cluster, leader, 30)
            cluster.network.resume(outcast.index)
            await cluster.settle(60)

            assert leader.node.role is Role.LEADER
            assert leader.node.term == term_before
        finally:
            await cluster.close()
