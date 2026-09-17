# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Elections, replication, and the safety property a volatile log endangers.

The centrepiece is :class:`TestElectionSafety`, which reproduces the exact
scenario that makes the non-voting rejoin mandatory rather than defensive. It
is worth reading before the rest: everything else here is ordinary Raft, and
that one is the reason this implementation is not ordinary Raft.

All of it runs on the in-memory harness, so a partition is a method call and a
restart is deterministic. Over real sockets these scenarios are reproduced by
sleeping and hoping, which means in practice they are not reproduced at all.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from nmos.raft.errors import RaftUnavailable
from nmos.raft.node import Role
from nmos.raft.operations import ProposalId, RegisterOp, UnregisterOp
from nmos.raft.tests._harness import Cluster
from nmos.registry.tests._fixtures import (
    NODE_ID,
    NODE_ID_2,
    make_node,
)
from nmos.registry.types import ResourceType, TaiCursor


def _register(node_id: str, owner: int, *, health: int = 42) -> RegisterOp:
    raw = make_node(node_id)
    return RegisterOp(
        proposal=ProposalId(0, 0),
        resource_type=ResourceType.NODE,
        resource_id=node_id,
        node_id=node_id,
        body_text=json.dumps(raw),
        created=TaiCursor(1000, 8),
        updated=TaiCursor(1000, 8),
        health=health,
        expect_created=True,
        claim_owner=owner,
    )


async def _cluster(size: int, tmp_path: Path) -> Cluster:
    cluster = Cluster(size, tmp_path)
    await cluster.start()
    return cluster


class TestElections:
    async def test_a_three_member_cluster_elects_one_leader(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect()
            # Settle first: ``elect`` returns as soon as a leader exists, and
            # another member may still be mid-campaign until the first
            # heartbeat reaches it.
            await cluster.settle(5)

            assert leader.node.role is Role.LEADER
            assert len(cluster.leaders) == 1
            followers = [m for m in cluster.members if m is not leader]
            assert all(m.node.role is Role.FOLLOWER for m in followers)
            assert all(m.node.leader == leader.index for m in followers)
        finally:
            await cluster.close()

    async def test_a_single_member_cluster_elects_itself(
        self, tmp_path: Path,
    ) -> None:
        """Quorum of one. It must not wait for a promotion that cannot come."""
        cluster = await _cluster(1, tmp_path)
        try:
            leader = await cluster.elect()
            assert leader.index == 0
            assert leader.node.voting is True
        finally:
            await cluster.close()

    async def test_the_term_is_persisted_across_the_election(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect()
            assert leader.terms.load().term == leader.node.term
        finally:
            await cluster.close()

    async def test_a_leader_that_loses_its_quorum_stands_down(
        self, tmp_path: Path,
    ) -> None:
        """Check-quorum. Without it a partitioned leader answers forever.

        It cannot commit anything -- no quorum -- but it would keep reporting
        itself as leader, so the backend above it would keep reporting READY
        and accepting writes that can never land.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            first = await cluster.elect()
            term_before = first.node.term
            cluster.network.isolate(first.index)
            await cluster.settle(40)

            assert first.node.role is not Role.LEADER
            assert first.node.has_quorum is False
            # Term and vote untouched: it gave up leading, not its vote.
            assert first.node.term == term_before
        finally:
            await cluster.close()

    async def test_losing_the_leader_elects_another(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            first = await cluster.elect()
            term_before = first.node.term
            cluster.network.stop(first.index)
            # Long enough for check-quorum to retire the old leader and for the
            # survivors to run an election; ``elect`` alone would return the
            # stale leader, which is still claiming the role for a few ticks.
            await cluster.settle(40)
            assert first.node.role is not Role.LEADER

            second = await cluster.elect(timeout=5.0)
            assert second.index != first.index
            assert second.node.term > term_before
        finally:
            await cluster.close()

    async def test_a_minority_cannot_elect(self, tmp_path: Path) -> None:
        """One of three is not a quorum, however long it campaigns."""
        cluster = await _cluster(3, tmp_path)
        try:
            await cluster.elect()
            cluster.network.isolate(0)
            await cluster.settle(20)

            assert cluster[0].node.role is not Role.LEADER
        finally:
            await cluster.close()

    async def test_an_isolated_member_does_not_let_its_term_run_away(
        self, tmp_path: Path,
    ) -> None:
        """The disruptive-server problem, and why campaigning is quorum-gated.

        Without the guard this member times out, campaigns, fails, times out
        again, and climbs a term per election window -- for as long as the
        partition lasts. When it healed it would arrive with a term far above
        everyone else's, force a perfectly healthy leader to step down, and
        cost the cluster an election it had no reason to hold.

        Found by testing rather than by reasoning: the first version of this
        file asserted a new leader's term exceeded the old leader's, and the
        old leader's term had climbed to 4 while the cluster was on 2.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            await cluster.elect()
            outcast = next(
                m for m in cluster.members if m.node.role is not Role.LEADER
            )
            cluster.network.isolate(outcast.index)
            await cluster.settle(10)
            term_after_isolation = outcast.node.term

            await cluster.settle(60)
            assert outcast.node.term == term_after_isolation, (
                "an isolated member climbed terms; on healing it would "
                "depose a healthy leader"
            )
        finally:
            await cluster.close()

    async def test_a_healed_member_rejoins_without_deposing_the_leader(
        self, tmp_path: Path,
    ) -> None:
        """The payoff: healing a partition must not cost an election."""
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect()
            outcast = next(
                m for m in cluster.members if m is not leader
            )
            cluster.network.isolate(outcast.index)
            await cluster.settle(30)

            term_before = leader.node.term
            cluster.network.heal()
            await cluster.settle(30)

            assert leader.node.role is Role.LEADER
            assert leader.node.term == term_before
            assert outcast.node.leader == leader.index
        finally:
            await cluster.close()


class TestReplication:
    async def test_a_committed_entry_reaches_every_member(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect()
            outcome = await asyncio.wait_for(
                leader.node.propose(_register(NODE_ID, leader.index)), 5.0,
            )
            assert outcome.result.ok and outcome.result.created

            await cluster.settle(10)
            for member in cluster.members:
                stored = member.registry.store.get(ResourceType.NODE, NODE_ID)
                assert stored is not None, f"member {member.index} missing it"
                assert stored.health == 42
                assert stored.created == TaiCursor(1000, 8)
        finally:
            await cluster.close()

    async def test_every_member_reaches_the_same_indices(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect()
            await asyncio.wait_for(
                leader.node.propose(_register(NODE_ID, leader.index)), 5.0,
            )
            await cluster.settle(10)

            applied = {m.node.last_applied for m in cluster.members}
            assert len(applied) == 1, f"members applied differently: {applied}"
        finally:
            await cluster.close()

    async def test_ownership_is_replicated_with_the_registration(
        self, tmp_path: Path,
    ) -> None:
        """The fused claim: one entry, one round trip, every member agrees."""
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect()
            await asyncio.wait_for(
                leader.node.propose(_register(NODE_ID, leader.index)), 5.0,
            )
            await cluster.settle(10)

            owners = {
                member.ownership.owner_of(NODE_ID) for member in cluster.members
            }
            assert len(owners) == 1
            assert next(iter(owners)) is not None
        finally:
            await cluster.close()

    async def test_a_follower_forwards_its_proposals_to_the_leader(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect()
            follower = next(m for m in cluster.members if m is not leader)

            outcome = await asyncio.wait_for(
                follower.node.propose(_register(NODE_ID_2, follower.index)),
                5.0,
            )
            assert outcome.result.ok
            await cluster.settle(10)
            assert leader.registry.store.get(
                ResourceType.NODE, NODE_ID_2,
            ) is not None
        finally:
            await cluster.close()

    async def test_a_batch_commits_in_one_round(self, tmp_path: Path) -> None:
        """Several proposals in one tick must not cost several rounds."""
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect()
            before = cluster.network.delivered

            futures = [
                leader.node.propose(
                    _register(f"{index:08x}-0000-4000-8000-000000000000",
                              leader.index),
                )
                for index in range(1, 9)
            ]
            await asyncio.wait_for(asyncio.gather(*futures), 5.0)
            spent = cluster.network.delivered - before

            # Two peers, one AppendEntries each plus their replies, and a
            # little heartbeat traffic -- but nowhere near eight rounds.
            assert spent < 8 * 4, f"8 proposals cost {spent} messages"
        finally:
            await cluster.close()


class TestQuorum:
    async def test_writes_stop_without_a_quorum(self, tmp_path: Path) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect()
            others = [m.index for m in cluster.members if m is not leader]
            cluster.network.partition({leader.index}, set(others))
            await cluster.settle(10)

            assert leader.node.has_quorum is False
            with pytest.raises((RaftUnavailable, asyncio.TimeoutError)):
                await asyncio.wait_for(
                    leader.node.propose(_register(NODE_ID, leader.index)), 0.5,
                )
        finally:
            await cluster.close()

    async def test_reads_keep_working_without_a_quorum(
        self, tmp_path: Path,
    ) -> None:
        """Refusing reads because writes are impossible makes an outage total."""
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect()
            await asyncio.wait_for(
                leader.node.propose(_register(NODE_ID, leader.index)), 5.0,
            )
            await cluster.settle(5)

            cluster.network.isolate(leader.index)
            await cluster.settle(10)

            assert leader.registry.store.get(
                ResourceType.NODE, NODE_ID,
            ) is not None
        finally:
            await cluster.close()


class TestElectionSafety:
    """The scenario a volatile log breaks, and the mechanism that repairs it.

    Read ``node.py``'s module docstring alongside this. In short: Raft's
    up-to-dateness check stops a candidate missing a committed entry from being
    elected, and it works because a voter holding the entry refuses. A member
    that restarts with an empty log refuses nothing -- so it will vote for a
    candidate whose log is behind, and an acknowledged write is lost after a
    single non-simultaneous failure.
    """

    async def test_a_restarted_member_comes_back_non_voting(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            await cluster.elect()
            restarted = await cluster.restart(1)
            assert restarted.node.voting is False
        finally:
            await cluster.close()

    async def test_a_fresh_member_votes_immediately(
        self, tmp_path: Path,
    ) -> None:
        """Otherwise a cold start deadlocks: nobody could ever be promoted."""
        cluster = await _cluster(3, tmp_path)
        try:
            assert all(m.node.voting for m in cluster.members)
            await cluster.elect()
        finally:
            await cluster.close()

    async def test_a_non_voting_member_refuses_to_grant_a_vote(
        self, tmp_path: Path,
    ) -> None:
        from nmos.raft.messages import RequestVote

        cluster = await _cluster(3, tmp_path)
        try:
            await cluster.elect()
            restarted = await cluster.restart(1)

            reply = restarted.node.on_request_vote(2, RequestVote(
                term=restarted.node.term + 5, candidate=2,
                last_log_index=0, last_log_term=0,
            ))
            assert reply.granted is False
            assert reply.voting is False
        finally:
            await cluster.close()

    async def test_a_restarted_member_does_not_campaign(
        self, tmp_path: Path,
    ) -> None:
        """It would only disrupt: it cannot win, but it would bump terms."""
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect()
            restarted = await cluster.restart(1)
            cluster.network.isolate(restarted.index)

            term_before = restarted.node.term
            await cluster.settle(20)
            assert restarted.node.role is not Role.CANDIDATE
            assert restarted.node.term == term_before
        finally:
            await cluster.close()

    async def test_an_acknowledged_write_survives_a_restart_and_a_campaign(
        self, tmp_path: Path,
    ) -> None:
        """The §0.1 scenario, end to end.

        A commits an entry with B while C is partitioned away. B restarts with
        an empty log. A is isolated. C campaigns with a log that is missing the
        entry -- and must not win, because B refuses to vote while catching up.

        Without the non-voting rejoin, C wins here and the entry is gone while
        the client has already been told 201.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect()
            behind = next(
                m for m in cluster.members
                if m is not leader and m.index != 1
            )
            witness = next(
                m for m in cluster.members
                if m is not leader and m is not behind
            )

            # Keep the eventual candidate out of the commit.
            cluster.network.partition(
                {leader.index, witness.index}, {behind.index},
            )
            await asyncio.wait_for(
                leader.node.propose(_register(NODE_ID, leader.index)), 5.0,
            )
            await cluster.settle(5)

            assert witness.registry.store.get(
                ResourceType.NODE, NODE_ID,
            ) is not None, "the witness never received the committed entry"
            assert behind.registry.store.get(
                ResourceType.NODE, NODE_ID,
            ) is None, "the candidate was supposed to be partitioned away"

            # The witness restarts, losing its log but keeping its vote.
            restarted = await cluster.restart(witness.index)
            assert restarted.node.voting is False

            # The leader goes away; only the restarted member and the
            # behind-the-times candidate remain.
            cluster.network.partition(
                {leader.index}, {restarted.index, behind.index},
            )
            await cluster.settle(30)

            assert behind.node.role is not Role.LEADER, (
                "a candidate missing a committed entry was elected; the "
                "restarted member voted for it with an empty log"
            )
            # And the entry is still where it was committed.
            assert leader.registry.store.get(
                ResourceType.NODE, NODE_ID,
            ) is not None
        finally:
            await cluster.close()

    async def test_a_catching_up_member_is_promoted_and_votes_again(
        self, tmp_path: Path,
    ) -> None:
        """Non-voting is a state to leave, not a state to be stuck in."""
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect()
            await asyncio.wait_for(
                leader.node.propose(_register(NODE_ID, leader.index)), 5.0,
            )
            await cluster.settle(5)

            target = next(m.index for m in cluster.members if m is not leader)
            restarted = await cluster.restart(target)
            assert restarted.node.voting is False

            await cluster.settle(30)
            assert restarted.node.voting is True, (
                "a caught-up member was never promoted; it would sit out "
                "every election from now on"
            )
        finally:
            await cluster.close()

    async def test_a_rolling_restart_loses_nothing(
        self, tmp_path: Path,
    ) -> None:
        """The procedure this design documents for resizing and upgrading.

        Each member restarts in turn with a full catch-up between. Every
        acknowledged registration must still be present, on every member, at
        the end.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            registered: list[str] = []
            for round_index in range(3):
                leader = await cluster.elect(timeout=5.0)
                node_id = f"{round_index:08x}-0000-4000-8000-00000000000a"
                outcome = await asyncio.wait_for(
                    leader.node.propose(_register(node_id, leader.index)), 5.0,
                )
                assert outcome.result.ok
                registered.append(node_id)

                await cluster.restart(round_index)
                await cluster.settle(30)

            await cluster.settle(20)
            for member in cluster.members:
                for node_id in registered:
                    assert member.registry.store.get(
                        ResourceType.NODE, node_id,
                    ) is not None, (
                        f"member {member.index} lost {node_id} across a "
                        f"rolling restart"
                    )
        finally:
            await cluster.close()


class TestCommitRule:
    async def test_the_commit_index_never_exceeds_the_log(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect()
            await asyncio.wait_for(
                leader.node.propose(_register(NODE_ID, leader.index)), 5.0,
            )
            await cluster.settle(10)
            for member in cluster.members:
                assert member.node.commit_index <= member.node.log.last_index
                assert member.node.last_applied <= member.node.commit_index
        finally:
            await cluster.close()

    async def test_a_new_leader_appends_a_noop(self, tmp_path: Path) -> None:
        """Raft §8: presence on a majority is not commitment.

        A leader cannot know what earlier terms committed until it commits an
        entry of its own term, so it appends one that does nothing.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect()
            assert leader.node.log.last_index >= 1
            assert leader.node.log.term_at(
                leader.node.log.last_index,
            ) == leader.node.term
        finally:
            await cluster.close()


class TestProposalIdentityAcrossRestarts:
    """A proposal id must be unique over a member's history, not just its run.

    Found by the chaos soak, which reported a registration future resolving to
    a bool. The cause was that ``_sequence`` restarted at zero: a member's
    entries outlive the member, so a new incarnation minting the same ids has
    an old entry's outcome delivered to a new caller's future.

    Client-visible, and in the worst direction -- a Node could be told its
    registration failed when it had succeeded, because the answer belonged to
    somebody else's operation.
    """

    async def test_a_restarted_member_does_not_reuse_proposal_ids(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            member = next(
                m for m in cluster.members if m.index != leader.index
            )

            before = member.node.propose(_register(NODE_ID, member.index))
            await asyncio.sleep(0)      # let the batcher mint the id
            old_ids = set(member.node._waiters)      # noqa: SLF001
            assert old_ids

            replacement = await cluster.restart(member.index)
            replacement.node.propose(_register(NODE_ID_2, replacement.index))
            await asyncio.sleep(0)
            new_ids = set(replacement.node._waiters)     # noqa: SLF001
            assert new_ids

            assert not (old_ids & new_ids), (
                f"the restarted member reused {sorted(old_ids & new_ids)}, so "
                f"an entry from its previous incarnation will resolve a "
                f"waiter belonging to this one"
            )
            before.cancel()
        finally:
            await cluster.close()

    async def test_an_outcome_reaches_the_caller_that_asked_for_it(
        self, tmp_path: Path,
    ) -> None:
        """The client-visible form of the same claim.

        Two operations whose results are different *types* -- an unregister
        answers with a bool, a register with a result object -- so a crossed
        future is unambiguous rather than a plausible-looking wrong value.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            member = next(
                m for m in cluster.members if m.index != leader.index
            )

            stale = member.node.propose(UnregisterOp(
                proposal=ProposalId(member.index, 0),
                resource_type=ResourceType.NODE, resource_id=NODE_ID,
            ))
            await asyncio.sleep(0)

            replacement = await cluster.restart(member.index)
            fresh = replacement.node.propose(
                _register(NODE_ID_2, replacement.index),
            )
            outcome = await asyncio.wait_for(fresh, timeout=5.0)

            assert not isinstance(outcome.result, bool), (
                "a registration was answered with an unregistration's result"
            )
            assert outcome.result.ok
            stale.cancel()
        finally:
            await cluster.close()
