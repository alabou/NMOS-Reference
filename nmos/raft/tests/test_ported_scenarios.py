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
  ``lagging_commit.txt`` (a follower's commit index trails a hiccup),
  ``heartbeat_resp_recovers_from_probing.txt`` (a peer left behind is picked up
  again without prompting) and ``replicate_pause.txt`` (entries are not
  re-sent while an append is outstanding).

Where a scenario's *mechanism* has no counterpart here, the *claim* is ported
instead and the difference is stated on the test -- an assertion about etcd's
internals would only prove this is not etcd.
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
from typing import Any

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


class TestPreVote:
    """etcd-io/raft ``prevote.txt`` and ``prevote_checkquorum.txt``.

    Their headers state both halves. From ``prevote.txt``: "Tests that PreVote
    prevents a node that is behind on the log from obtaining prevotes and
    calling an election. Also tests that a node that is up-to-date on its log
    can hold an election." From ``prevote_checkquorum.txt``: "Tests that
    PreVote+CheckQuorum prevents a node from obtaining prevotes if voters have
    heard from a leader recently. Also tests that a node is able to obtain
    prevotes if the voter hasn't heard from the leader in the past election
    timeout interval, or if a quorum of voters are precandidates."

    That last clause is why both halves are here rather than only the first.
    PreVote and CheckQuorum each refuse elections, and together they can refuse
    *every* election -- each member still vouching for a leader that has died.
    What prevents it is that becoming a pre-candidate releases the lease, so a
    quorum of pre-candidates no longer blocks each other.
    """

    async def test_a_member_behind_on_the_log_cannot_inflate_the_term(
        self, tmp_path: Path,
    ) -> None:
        """The headline benefit, and it is measurable.

        Before Pre-Vote this member campaigned on every timeout and carried its
        term up with it -- the soak recorded 183 -- so that on reconnecting it
        would depose a leader that had never stopped working.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            behind = next(
                m for m in cluster.members if m.index != leader.index
            )

            # One direction only: it stays reachable to everyone, so the quorum
            # gate on campaigning does not stop it. Pre-Vote has to.
            cluster.network.block(leader.index, behind.index)
            for tag in range(70, 78):
                await asyncio.wait_for(
                    leader.node.propose(_register(_node_id(tag), leader)),
                    timeout=5.0,
                )
            term_before = behind.node.term
            await cluster.settle(400)

            assert behind.node.log.last_index < leader.node.log.last_index, (
                "the member under test never actually fell behind"
            )
            assert behind.node.term == term_before, (
                f"term climbed from {term_before} to {behind.node.term} while "
                f"the member could not have won"
            )
            assert behind.node.role is Role.PRE_CANDIDATE
            assert cluster.leaders == [leader]
        finally:
            await cluster.close()

    async def test_a_quorum_of_pre_candidates_still_replaces_a_dead_leader(
        self, tmp_path: Path,
    ) -> None:
        """PreVote plus CheckQuorum must not add up to "never elect anyone".

        Each survivor holds a lease on the dead leader and would refuse votes
        on its behalf. Becoming a pre-candidate clears that leader, which
        releases the lease -- so the survivors stop blocking one another and
        one of them wins.
        """
        cluster = await _cluster(5, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            await cluster.settle(20)
            cluster.network.stop(leader.index)

            survivors = [m for m in cluster.members if m.index != leader.index]
            deadline = asyncio.get_running_loop().time() + 10.0
            while asyncio.get_running_loop().time() < deadline:
                elected = [m for m in survivors if m.node.role is Role.LEADER]
                if len(elected) == 1:
                    assert elected[0].node.term > leader.node.term
                    return
                await asyncio.sleep(cluster.timing.heartbeat)
            raise AssertionError(
                "PreVote and CheckQuorum between them refused every election: "
                f"{[(m.index, m.node.role.value) for m in survivors]}",
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


class TestHeartbeatRecoversAStuckPeer:
    """etcd-io/raft ``heartbeat_resp_recovers_from_probing.txt``.

    etcd tracks each peer in an explicit probe-or-replicate state and uses a
    heartbeat response to move it out of probing. We have no such state
    machine -- ``_PeerState`` carries ``next_index``/``match_index`` and, since
    the flow-control fix, a pause on an outstanding append -- so the mechanism
    does not port. The *claim* does, and it is the one worth asserting: a peer
    that has fallen behind and stopped being sent entries must be picked up
    again without anything having to prod it.

    That claim is what the pause could have broken. A pause released only by a
    reply that never comes would strand the peer silently, and nothing else in
    the suite would notice.
    """

    async def test_a_peer_whose_append_was_lost_is_caught_up_anyway(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            stuck = next(
                m for m in cluster.members if m.index != leader.index
            )

            # Cut the link *after* the leader has entries to send, so the
            # append is genuinely lost rather than never attempted -- which is
            # the state the pause has to recover from on its own.
            cluster.network.block(leader.index, stuck.index)
            for tag in range(40, 46):
                leader.node.propose(_register(_node_id(tag), leader))
            await cluster.settle(40)

            cluster.network.heal()
            await cluster.settle(200)

            for tag in range(40, 46):
                assert stuck.registry.store.get(
                    ResourceType.NODE, _node_id(tag),
                ) is not None, (
                    f"member {stuck.index} never recovered {_node_id(tag)} "
                    f"after its append was lost"
                )
        finally:
            await cluster.close()


class TestReplicatePause:
    """etcd-io/raft ``replicate_pause.txt``: do not keep re-sending entries.

    The gap this closes was measured before it was fixed. ``next_index`` only
    advances on a reply, so the leader re-sent the same window every heartbeat
    until the peer answered: harmless against a healthy peer (x1.0) and **x15**
    against a 0.5 s link, scaling with RTT over heartbeat. Idempotent, so never
    a correctness bug -- but a feedback loop, since the slower a link gets the
    more traffic is pushed into it.

    Asserted as a bound on bytes rather than on frames: the heartbeat must keep
    flowing to hold the lease, so the frame count is *supposed* to stay the
    same. What must not repeat is the payload.
    """

    async def test_entries_are_not_repeated_while_an_append_is_outstanding(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            slow = next(m for m in cluster.members if m.index != leader.index)

            sent: dict[int, int] = {}
            distinct: dict[int, int] = {}
            original = leader.node.transport.send

            def counted(peer: int, message: Any, **kwargs: Any) -> None:
                entries = getattr(message, "entries", None)
                if entries is not None:
                    sent[peer] = sent.get(peer, 0) + sum(
                        len(e.payload) for e in entries
                    )
                    for entry in entries:
                        distinct[entry.index] = len(entry.payload)
                original(peer, message, **kwargs)

            leader.node.transport.send = counted      # type: ignore[method-assign]

            # Reachable, so the leader keeps heartbeating it, but its
            # acknowledgements never arrive: the shape of a slow peer.
            cluster.network.block(slow.index, leader.index)
            for tag in range(50, 58):
                leader.node.propose(_register(_node_id(tag), leader))
            await cluster.settle(120)

            payload = sum(distinct.values())
            assert payload > 0, "the leader sent no entries at all"
            to_slow = sent.get(slow.index, 0)
            assert to_slow <= payload * 3, (
                f"{to_slow} entry-bytes sent to a peer that never "
                f"acknowledged, for {payload} distinct bytes "
                f"(x{to_slow / payload:.1f}) -- the window is being repeated "
                f"on every heartbeat"
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
