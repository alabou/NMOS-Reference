# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Scenarios taken from the Raft paper and from established implementations.

There are no standard Raft conformance vectors -- nothing like the test vectors
that come with a cipher. What does exist is two decades of accumulated
*scenarios*: the paper's own worked counterexamples, and the regression cases
that mature implementations added after something went wrong in production.
Those are portable even though their code is not, and they are worth more than
scenarios invented here, because they encode failures nobody predicted.

Sources, each named on the test that came from it:

* **Ongaro & Ousterhout, "In Search of an Understandable Consensus Algorithm
  (Extended Version)"** -- Figure 8, the sequence showing why a leader may not
  commit an earlier term's entry on a count of replicas alone.
* **etcd-io/raft ``testdata/``** -- data-driven regression scenarios.
  ``checkquorum.txt`` (a leader steps down without a quorum, *and* votes are
  rejected while a leader is active), ``slow_follower_after_compaction.txt``
  (a probe meets a gap and turns into a snapshot transfer),
  ``lagging_commit.txt`` (a follower's commit index trails a hiccup).
* **MIT 6.5840 Lab 2** -- ``TestRejoin``, ``TestBackup``: a deposed leader
  rejoins carrying entries that were never committed, and they must be
  overwritten rather than resurrected.

Deliberately not ported: the ``confchange_*`` family, which is the whole of
dynamic cluster membership. This backend does not implement it -- see
``nmos/raft/__init__.py`` -- so those scenarios have nothing here to run
against.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path

import pytest

from nmos.raft.messages import RequestVote
from nmos.raft.node import Role
from nmos.raft.operations import ProposalId, RegisterOp
from nmos.raft.tests._harness import FAST, Cluster, Member
from nmos.raft.tests._invariants import SafetyMonitor
from nmos.registry.tests._fixtures import make_node
from nmos.registry.types import ResourceType


def _register(node_id: str, member: Member) -> RegisterOp:
    """A registration, with cursors from the proposing member's allocator.

    Not a fixed ``TaiCursor``: cursors are the Query API's paging keys and must
    be globally unique, so two resources sharing one is a defect the invariant
    monitor is entitled to report. Borrowing the real allocator keeps these
    scenarios from manufacturing that defect themselves.
    """
    raw = make_node(node_id)
    cursor = member.node.cursors.allocate(ResourceType.NODE)
    return RegisterOp(
        proposal=ProposalId(member.index, 0),
        resource_type=ResourceType.NODE,
        resource_id=node_id,
        node_id=node_id,
        body_text=json.dumps(raw),
        created=cursor,
        updated=cursor,
        health=0,
        expect_created=True,
        claim_owner=member.index,
    )


def _node_id(tag: int) -> str:
    return f"{tag:08x}-0000-4000-8000-00000000000a"


async def _cluster(size: int, tmp_path: Path) -> Cluster:
    cluster = Cluster(size, tmp_path)
    await cluster.start()
    return cluster


class TestDisruptionByAStaleCandidate:
    """etcd-io/raft ``checkquorum.txt``: "votes are rejected when there is a
    current leader".

    The half of check-quorum we did not have. A leader standing down when it
    loses contact was already implemented and tested; the other half -- a
    *follower* refusing to help depose a leader that is serving it -- was not,
    and without it one member with an inflated term forces an election the
    cluster had no reason to hold.
    """

    async def test_a_follower_refuses_to_depose_a_healthy_leader(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            await cluster.settle(20)

            follower = next(
                m for m in cluster.members if m.index != leader.index
            )
            candidate = next(
                m for m in cluster.members
                if m.index not in (leader.index, follower.index)
            )
            term_before = follower.node.term

            reply = follower.node.on_request_vote(
                candidate.index,
                RequestVote(
                    term=term_before + 5, candidate=candidate.index,
                    last_log_index=follower.node.log.last_index,
                    last_log_term=follower.node.log.last_term,
                ),
            )

            assert reply.granted is False
            # The term must not move either. Adopting it is the disruption --
            # it clears the leader and the recorded vote, which is exactly
            # what an unnecessary election is made of.
            assert follower.node.term == term_before
            assert cluster.leaders == [leader]
        finally:
            await cluster.close()

    async def test_the_lease_expires_so_a_dead_leader_is_replaced(
        self, tmp_path: Path,
    ) -> None:
        """The other side of the trade, and the reason the lease is bounded.

        A refusal that outlived the leader would be worse than the disruption
        it prevents: the cluster would refuse to elect anyone. The lease is
        ``election_min``, always shorter than the shortest interval after which
        a member legitimately campaigns.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            cluster.network.stop(leader.index)

            # Waiting on the *survivors*, not on ``elect``: a stopped member
            # goes on believing it leads until check-quorum makes it stand
            # down, so a test that asked the cluster for "the leader" could be
            # handed the corpse.
            survivors = [m for m in cluster.members if m.index != leader.index]
            deadline = asyncio.get_running_loop().time() + 5.0
            while asyncio.get_running_loop().time() < deadline:
                elected = [
                    m for m in survivors if m.node.role is Role.LEADER
                ]
                if len(elected) == 1:
                    break
                await asyncio.sleep(cluster.timing.heartbeat)
            else:
                raise AssertionError(
                    "the lease outlived the leader: no replacement was "
                    f"elected among {[m.index for m in survivors]}",
                )
        finally:
            await cluster.close()


class TestFigureEight:
    """The paper's Figure 8, the reason for the current-term commit rule.

    Quoting the caption: "In (c) S5 crashes; S1 restarts, is elected leader,
    and continues replication. At this point, the log entry from term 2 has
    been replicated on a majority of the servers, but it is not committed. If
    S1 crashes as in (d), S5 could be elected leader ... and overwrite the
    entry with its own entry from term 3."

    The property under test is the one that prevents it: a leader may only
    advance its commit index to an entry of its **own** term, never to an
    earlier one on a count of replicas alone. ``_advance_commit`` implements
    that, and this asserts nothing observes a commit index that ran ahead of
    it during a churn of leaders.
    """

    async def test_no_member_ever_commits_beyond_its_current_term(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(5, tmp_path)
        monitor = SafetyMonitor(cluster=cluster)
        try:
            for round_index in range(4):
                leader = await cluster.elect(timeout=5.0)
                await asyncio.wait_for(
                    leader.node.propose(
                        _register(_node_id(round_index), leader),
                    ),
                    timeout=5.0,
                )
                monitor.check()

                # Depose the leader mid-stream, which is what leaves entries
                # from an older term sitting on a majority but uncommitted.
                cluster.network.isolate(leader.index)
                await cluster.settle(30)
                monitor.check()
                cluster.network.heal()
                await cluster.settle(30)
                monitor.check()

            await cluster.settle(60)
            monitor.check()
            # Leader Completeness is the Figure 8 property stated as an
            # invariant, and the monitor has been evaluating it throughout.
            assert not monitor.violations
        finally:
            await cluster.close()


class TestADeposedLeaderRejoins:
    """MIT 6.5840 ``TestRejoin`` / ``TestBackup``.

    A leader accepts entries, is partitioned away before they commit, and the
    majority elects someone else and moves on. When the old leader returns it
    is carrying entries that were never committed and that conflict with the
    cluster's history. They must be discarded, not merged and not resurrected.
    """

    async def test_uncommitted_entries_from_a_deposed_leader_are_overwritten(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(5, tmp_path)
        monitor = SafetyMonitor(cluster=cluster)
        try:
            old = await cluster.elect(timeout=5.0)
            await asyncio.wait_for(
                old.node.propose(_register(_node_id(1), old)), 5.0,
            )

            # Cut the old leader off with a minority, so anything it accepts
            # from here cannot commit.
            minority = {old.index}
            majority = {
                m.index for m in cluster.members if m.index != old.index
            }
            cluster.network.partition(minority, majority)

            # It keeps accepting proposals it can never commit.
            for tag in range(10, 13):
                old.node.propose(_register(_node_id(tag), old))
            await cluster.settle(30)
            stranded = old.node.log.last_index
            assert stranded > 0

            # The majority elects a replacement and makes progress.
            await cluster.settle(40)
            replacement = next(
                m for m in cluster.members
                if m.index in majority and m.node.role is Role.LEADER
            )
            await asyncio.wait_for(
                replacement.node.propose(
                    _register(_node_id(2), replacement),
                ),
                timeout=5.0,
            )

            cluster.network.heal()
            await cluster.settle(120)
            monitor.check()

            # The returning member must agree with the cluster, not keep its
            # own version of history.
            reference = [
                (e.index, e.term)
                for e in old.node.log.slice(old.node.log.first_index, 1000)
            ]
            for member in cluster.members:
                if member.index == old.index:
                    continue
                theirs = [
                    (e.index, e.term)
                    for e in member.node.log.slice(
                        member.node.log.first_index, 1000,
                    )
                ]
                shared = min(len(reference), len(theirs))
                assert reference[:shared] == theirs[:shared], (
                    f"member {old.index} and member {member.index} disagree "
                    f"about history after the rejoin"
                )

            # And the entry the cluster really committed is present on it.
            assert old.registry.store.get(
                ResourceType.NODE, _node_id(2),
            ) is not None
        finally:
            await cluster.close()


class TestSlowFollowerAfterCompaction:
    """etcd-io/raft ``slow_follower_after_compaction.txt``.

    Quoting its comment: "Trigger a round of empty MsgApp 'probe' from leader.
    It will reach node 3 which will reply with a rejection MsgApp because it
    sees a gap in the log. Node 1 will reset the MsgApp flow and send a
    snapshot to catch node 3 up."

    ``test_compaction.py`` already covers a stranded member being caught up by
    state. What this adds is the *transition*: the leader discovers the gap
    from a rejection rather than being told in advance, and must switch from
    replication to snapshot transfer without looping.
    """

    async def test_a_gap_discovered_by_probing_becomes_a_snapshot(
        self, tmp_path: Path,
    ) -> None:
        # A threshold low enough that ordinary traffic compacts the log, so
        # the follower's gap is real rather than contrived. RaftTiming is
        # frozen, so it is built rather than mutated.
        cluster = Cluster(3, tmp_path, timing=dataclasses.replace(
            FAST, compaction_threshold=4,
        ))
        await cluster.start()
        monitor = SafetyMonitor(cluster=cluster)
        try:
            leader = await cluster.elect(timeout=5.0)
            stranded = next(
                m for m in cluster.members if m.index != leader.index
            )
            cluster.network.stop(stranded.index)

            for tag in range(20, 32):
                await asyncio.wait_for(
                    leader.node.propose(_register(_node_id(tag), leader)),
                    timeout=5.0,
                )
            await cluster.settle(40)
            assert leader.node.log.first_index > 1, (
                "the leader should have compacted past the stranded member"
            )

            cluster.network.resume(stranded.index)
            await cluster.settle(200)
            monitor.check()

            for tag in range(20, 32):
                assert stranded.registry.store.get(
                    ResourceType.NODE, _node_id(tag),
                ) is not None, (
                    f"member {stranded.index} never received {_node_id(tag)}"
                )
        finally:
            await cluster.close()


class TestLaggingCommit:
    """etcd-io/raft ``lagging_commit.txt``.

    Its subject: "the effect of delayed commit on a follower node after a
    network hiccup between the leader and this follower". The leader commits,
    the follower does not learn for a further round, and the client waiting at
    that follower waits with it.

    This is the regression test for a bug measured in this implementation: the
    commit index rode only on the next heartbeat, putting a full heartbeat
    interval -- 45.6 ms p50 against a 50 ms heartbeat -- on the critical path
    of every mutation that did not arrive at the leader. ``_advance_commit``
    now replicates as soon as it advances.
    """

    async def test_a_follower_learns_of_a_commit_without_waiting_for_a_tick(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            follower = next(
                m for m in cluster.members if m.index != leader.index
            )
            await cluster.settle(20)

            await asyncio.wait_for(
                leader.node.propose(_register(_node_id(7), leader)),
                timeout=5.0,
            )

            # Strictly less than one heartbeat's worth of settling. If the
            # commit index only travelled on the heartbeat, this would not be
            # enough and the follower would still be behind.
            for _ in range(3):
                await asyncio.sleep(0)
            await asyncio.sleep(cluster.timing.heartbeat / 4)

            assert follower.node.commit_index >= leader.node.commit_index - 1, (
                f"follower commit {follower.node.commit_index} trails leader "
                f"{leader.node.commit_index} by more than one entry within a "
                f"quarter of a heartbeat"
            )
        finally:
            await cluster.close()
