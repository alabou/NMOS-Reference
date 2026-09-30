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
import dataclasses
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from nmos.raft.errors import (
    RaftCursorReservationFailed,
    RaftInvariantViolated,
    RaftUnavailable,
)
from nmos.raft.messages import (
    AppendEntries,
    AppendEntriesReply,
    InstallSnapshot,
    WireEntry,
)
from nmos.raft.node import RaftNode, RaftTiming, Role, _Assembly, _PeerState
from nmos.raft.operations import (
    ProposalId,
    RegisterOp,
    UnregisterOp,
    encode_operation,
)
from nmos.raft.persist import PersistentState, PersistentStateError, TermStore
from nmos.raft.tests._harness import FAST, Cluster
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

    async def test_a_non_voting_member_votes_as_one_that_has_forgotten(
        self, tmp_path: Path,
    ) -> None:
        """It grants on log currency, says it cannot vote, and votes once.

        Its grant counts only where the candidate's own round proves that no
        quorum of voters can exist (``TestForgottenEvidenceIsBinding``). It
        used to refuse unless *it* judged the cluster had forgotten, from
        observations of other members' past state -- which went stale, and
        elected leaders without committed entries (E1).
        """
        from nmos.raft.messages import RequestVote

        cluster = await _cluster(3, tmp_path)
        try:
            await cluster.elect()
            restarted = await cluster.restart(1)
            # Out of reach, and past its lease, so what answers below is the
            # vote rule and not the lease.
            cluster.network.isolate(restarted.index)
            await asyncio.sleep(cluster.timing.election_min * 2)

            # Exactly as current as the member, however much it caught up
            # before it was cut off: the grant then turns on the vote rule
            # alone, not on the harness's timing.
            last = restarted.node.log.last_index
            last_term = restarted.node.log.last_term
            term = restarted.node.term + 5
            reply = restarted.node.on_request_vote(2, RequestVote(
                term=term, candidate=2,
                last_log_index=last, last_log_term=last_term,
            ))
            assert reply.voting is False
            assert reply.term == term, "the reply is not binding for the round"
            assert reply.granted is True, (
                "a non-voting member refused a candidate its log does not "
                "outrank; a whole-cluster restart could never gather a vote "
                "quorum"
            )
            again = restarted.node.on_request_vote(0, RequestVote(
                term=term, candidate=0,
                last_log_index=last, last_log_term=last_term,
            ))
            assert again.granted is False, "it voted twice in one term"
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

    async def test_a_member_catching_up_is_not_promoted_below_the_leaders_last_index(
        self, tmp_path: Path,
    ) -> None:
        """The promotion bar is the leader's whole log, not its commit index.

        A leader cannot tell an entry an earlier leader committed -- which it
        holds, by Leader Completeness, but has not yet learned is committed --
        from one nobody has committed: both sit above its commit index, inside
        its log. So the bar has to cover the log. Here the entry above the
        commit index is one the leader appended while cut off, which is the
        cheapest way to put one there; what matters is that the leader's view
        of it is the same as of an earlier leader's committed entry.
        """
        cluster = await _cluster(3, tmp_path)
        pending: asyncio.Future[Any] | None = None
        try:
            leader = await cluster.elect(timeout=5.0)
            node = leader.node
            await asyncio.wait_for(
                node.propose(_register(NODE_ID, leader.index)), timeout=5.0,
            )
            await cluster.settle(10)
            peer = min(node._peers)
            committed = node.commit_index
            assert committed == node.log.last_index, (
                "the leader has uncommitted entries already, so the one "
                "appended below would not be the only one"
            )

            cluster.network.isolate(leader.index)
            pending = node.propose(_register(NODE_ID_2, leader.index))
            for _ in range(200):
                if node.log.last_index > committed:
                    break
                await asyncio.sleep(0)
            holding = node.log.last_index
            assert holding > committed, "the leader never appended the entry"

            # A restarted member, back and caught up exactly to the commit
            # index.
            node.on_peer_state(peer, up=True, incarnation=2**62)
            state = node._peers[peer]
            assert node.role is Role.LEADER, "stepped down before the reply"
            node.on_append_entries_reply(peer, AppendEntriesReply(
                term=node.term, success=True, match_index=committed,
                conflict_index=0, conflict_term=0,
                catching_up=True, request_id=state.reply_floor + 1,
            ))

            assert state.catching_up, (
                f"member {peer} was promoted back into the electorate at index "
                f"{state.match_index}, the leader's commit index, while the "
                f"leader holds entries through {holding}: had one of them "
                f"been committed by an earlier leader, the member would now "
                f"vote without it"
            )
            assert state.promote_through == holding, (
                f"the bar is {state.promote_through}, not the leader's last "
                f"index {holding}"
            )
        finally:
            if pending is not None:
                pending.cancel()
            await cluster.close()

    async def test_a_restarted_member_is_not_promoted_below_an_earlier_commit(
        self, tmp_path: Path,
    ) -> None:
        """The module docstring's scenario, reached through the promotion.

        A commits E with B and restarts before B hears that E committed. B wins
        the next term -- it holds E, as Leader Completeness says it must -- but
        its commit index is still below E, because a leader learns an earlier
        term's entry is committed only once an entry of its own term commits
        above it. A rejoins with an empty log. Barred at B's commit index, A
        was promoted without E, voted again, and with C -- which never had E
        -- elected a leader that wrote over it: an acknowledged write gone,
        with one member ever down. Measured exactly so, by running it, before
        the bar became the leader's last index.

        The network carries everything up to the promotion exchange, which is
        delivered by hand so that what A holds when B decides is fixed by the
        test rather than by scheduling.
        """
        cluster = await _cluster(3, tmp_path)
        net = cluster.network
        pending: asyncio.Future[Any] | None = None
        try:
            a = await cluster.elect(timeout=5.0)
            for tag in range(3):
                await asyncio.wait_for(
                    a.node.propose(_register(_stale_id(tag), a.index)),
                    timeout=5.0,
                )
            await cluster.settle(10)
            b, c = [m for m in cluster.members if m is not a]
            committed = a.node.commit_index
            assert all(
                m.node.commit_index == committed for m in cluster.members
            ), "the cluster had not settled, so this proves nothing"

            # E commits on A and B. C never receives it, and B is never told
            # it committed: A -> B is cut the moment A commits, which drops
            # the append carrying the news (the harness re-checks
            # reachability at dispatch).
            net.block(a.index, c.index)
            pending = a.node.propose(_register(_stale_id(99), a.index))
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 5.0
            while a.node.commit_index == committed and loop.time() < deadline:
                await asyncio.sleep(0)
            net.block(a.index, b.index)
            e_index = a.node.commit_index
            assert e_index == committed + 1, "E never committed"
            assert b.node.log.last_index >= e_index, "B never received E"
            assert b.node.commit_index == committed, (
                "B learned that E committed, so its leadership below would "
                "not lag -- the scenario was not established"
            )

            # A restarts, and is kept apart. B wins the next term with C's
            # vote; B <-> C is cut the moment it leads, so its no-op cannot
            # commit and its commit index stays below E.
            restarted = await cluster.restart(a.index)
            for source, target in [
                (b.index, a.index), (c.index, a.index),
                (a.index, b.index), (a.index, c.index),
            ]:
                net.block(source, target)
            deadline = loop.time() + 5.0
            while b.node.role is not Role.LEADER and loop.time() < deadline:
                await asyncio.sleep(0)
            net.block(b.index, c.index)
            net.block(c.index, b.index)
            assert b.node.role is Role.LEADER, "B never led"
            assert b.node.commit_index < e_index, (
                "B learned that E committed after all"
            )

            # The promotion exchange, by hand: B sends the restarted member
            # everything up to its commit index, and it answers catching up.
            entries = b.node.log.slice(1, committed)
            reply = restarted.node.on_append_entries(b.index, AppendEntries(
                term=b.node.term, leader=b.index,
                prev_log_index=0, prev_log_term=0,
                leader_commit=b.node.commit_index, request_id=10**9,
                entries=tuple(
                    WireEntry(term=e.term, index=e.index, payload=e.payload)
                    for e in entries
                ),
            ))
            assert reply.success and reply.catching_up, (
                f"the restarted member answered {reply}"
            )
            b.node.on_append_entries_reply(restarted.index, reply)

            state = b.node._peers[restarted.index]
            assert state.catching_up, (
                f"B promoted the restarted member back into the electorate at "
                f"index {state.match_index} (bar {state.promote_through}) "
                f"although E, at index {e_index}, was committed before it "
                f"restarted and it does not hold it -- with its vote, C, which "
                f"never had E, can be elected and overwrite it"
            )
            assert state.promote_through >= e_index
        finally:
            if pending is not None:
                pending.cancel()
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

    async def test_a_member_catching_up_still_counts_against_the_majority(
        self, tmp_path: Path,
    ) -> None:
        """Figure 2: "a majority of matchIndex[i] >= N" -- of the voting configuration.

        A member still catching up after a restart contributes no
        acknowledgement, but it is still one of the three, so the leader needs
        one *other* countable member at N before N commits. Taking the
        majority of the members that happen to be countable instead lets a
        leader commit on its own whenever one of three is catching up. The Rust
        port did exactly that until the chaos soak's commit audit measured it,
        and in the runs that went on long enough a later leader that never had
        the entry wrote over it. This is the same scenario, so that the two
        implementations are held to the same answer.
        """
        cluster = await _cluster(3, tmp_path)
        pending: asyncio.Future[Any] | None = None
        try:
            leader = await cluster.elect(timeout=5.0)
            node = leader.node
            for tag in range(3):
                await asyncio.wait_for(
                    node.propose(_register(_stale_id(tag), leader.index)),
                    timeout=5.0,
                )
            restarted, countable = sorted(node._peers)
            for _ in range(200):
                if node._peers[countable].match_index >= node.log.last_index:
                    break
                await asyncio.sleep(cluster.timing.heartbeat)
            assert node._peers[countable].match_index >= node.log.last_index, (
                f"member {countable} never acknowledged everything the leader "
                f"holds"
            )
            committed = node.commit_index
            assert committed == node.log.last_index, (
                "the leader has uncommitted entries already, so the one "
                "appended below would not be the only one"
            )

            # Nothing real reaches the leader from here on: every reply it
            # handles is the one this test gives it.
            cluster.network.isolate(leader.index)

            # ``restarted`` comes back with an empty log, as a restart leaves
            # it, and the leader forgets what it knew of that member's log...
            node.on_peer_state(restarted, up=True, incarnation=2**62)
            # ...then appends an entry that only it holds.
            pending = asyncio.ensure_future(
                node.propose(_register(_stale_id(99), leader.index)),
            )
            for _ in range(200):
                if node.log.last_index > committed:
                    break
                await asyncio.sleep(0)
            only_on_the_leader = node.log.last_index
            assert only_on_the_leader > committed, (
                "the leader never appended the entry"
            )

            # The restarted member answers: catching up, far below its bar.
            state = node._peers[restarted]
            assert node.role is Role.LEADER, "stepped down before the reply"
            node.on_append_entries_reply(restarted, AppendEntriesReply(
                term=node.term, success=True, match_index=1,
                conflict_index=0, conflict_term=0,
                catching_up=True, request_id=state.reply_floor + 1,
            ))

            assert state.catching_up and (
                state.match_index < state.promote_through
            ), (
                f"this needs member {restarted} catching up below its bar, "
                f"and it is at {state.match_index} with a bar of "
                f"{state.promote_through} (catching up: {state.catching_up})"
            )
            assert node.commit_index == committed, (
                f"the leader committed through {node.commit_index} on its own: "
                f"index {only_on_the_leader} is held by the leader alone -- "
                f"member {restarted} is catching up at {state.match_index} and "
                f"member {countable} holds "
                f"{node._peers[countable].match_index} -- and a majority of "
                f"three is two"
            )
        finally:
            if pending is not None:
                pending.cancel()
            await cluster.close()


class TestForgottenEvidenceIsBinding:
    """Who has forgotten is proved inside the round, by the members themselves.

    A member counts as forgotten for the election of term T only on its own
    reply to that candidacy, saying ``voting=False`` at term T. Such a reply is
    binding: the member has adopted T, and a promotion needs a leader of T or
    later, which cannot exist while T is being decided. Observations carried
    between rounds could not be trusted that way -- a member is promoted
    without the observer hearing of it -- and a stale one elected a leader
    that lacked a committed entry (E1: seeds 59925, 110797, 111540).

    The candidates below are driven by hand: isolated, so their requests go
    nowhere, and handed the replies each test is about.
    """

    @staticmethod
    async def _candidate(cluster: Cluster, index: int) -> Any:
        from nmos.raft.messages import RequestVoteReply

        member = next(m for m in cluster.members if m.index == index)
        cluster.network.isolate(index)
        member.node._campaign()
        assert member.node.role is Role.CANDIDATE
        peers = [m.index for m in cluster.members if m.index != index]
        return member, peers, RequestVoteReply

    async def test_a_non_voting_grant_does_not_count_toward_an_ordinary_election(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect()
            index = next(m.index for m in cluster.members if m is not leader)
            member, peers, Reply = await self._candidate(cluster, index)
            term = member.node.term

            member.node.on_request_vote_reply(peers[0], Reply(
                term=term, granted=True, voting=False,
            ))

            assert member.node.role is not Role.LEADER, (
                "elected on one voter's vote and one grant from a member that "
                "said it cannot vote; nothing in this round proves a quorum of "
                "voters impossible"
            )
        finally:
            await cluster.close()

    async def test_a_reply_from_an_earlier_term_proves_nothing(
        self, tmp_path: Path,
    ) -> None:
        # A lease refusal keeps the voter's own term -- a leader exists, which
        # is the opposite of evidence that the cluster has forgotten.
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect()
            index = next(m.index for m in cluster.members if m is not leader)
            member, peers, Reply = await self._candidate(cluster, index)
            term = member.node.term

            member.node.on_request_vote_reply(peers[0], Reply(
                term=term - 1, granted=False, voting=False,
            ))
            member.node.on_request_vote_reply(peers[1], Reply(
                term=term, granted=True, voting=False,
            ))

            assert member.node.role is not Role.LEADER, (
                "an earlier term's refusal was taken as proof that its sender "
                "had forgotten, and made a recovery election of an ordinary one"
            )
        finally:
            await cluster.close()

    async def test_a_pre_vote_reply_proves_nothing_about_the_election(
        self, tmp_path: Path,
    ) -> None:
        # The E1 trigger: a reply that arrived after its round was over, kept
        # as evidence for a later one.
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect()
            index = next(m.index for m in cluster.members if m is not leader)
            member, peers, Reply = await self._candidate(cluster, index)
            term = member.node.term

            member.node.on_request_vote_reply(peers[0], Reply(
                term=term, granted=False, voting=False, pre_vote=True,
            ))
            member.node.on_request_vote_reply(peers[1], Reply(
                term=term, granted=True, voting=False,
            ))

            assert member.node.role is not Role.LEADER, (
                "a pre-vote reply was taken as proof for the real election"
            )
        finally:
            await cluster.close()

    async def test_a_round_that_proves_the_cluster_has_forgotten_elects(
        self, tmp_path: Path,
    ) -> None:
        """The recovery the clause exists for, on binding evidence alone.

        A non-voting candidate whose every peer answers, in its term, that it
        cannot vote either: no quorum of voters can exist, a quorum granted,
        and every member not proven forgotten -- none -- granted.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect()
            index = next(m.index for m in cluster.members if m is not leader)
            await cluster.restart(index)
            member, peers, Reply = await self._candidate(cluster, index)
            assert member.node.voting is False
            term = member.node.term

            for peer in peers:
                member.node.on_request_vote_reply(peer, Reply(
                    term=term, granted=True, voting=False,
                ))

            assert member.node.role is Role.LEADER, (
                "a round proving that every member has forgotten did not elect "
                "anyone; a whole-cluster restart would never recover"
            )
        finally:
            await cluster.close()

    async def test_a_recovery_election_is_refused_by_a_member_that_remembers(
        self, tmp_path: Path,
    ) -> None:
        """Five members, three proven forgotten, one that remembers more.

        The worked example in ``node.py``: a committed entry survives on one of
        the two members that remember. The candidate lacks it, so that member
        refuses -- and since its approval is required, the candidate cannot
        win, however many forgotten members grant.
        """
        cluster = await _cluster(5, tmp_path)
        try:
            leader = await cluster.elect()
            index = next(m.index for m in cluster.members if m is not leader)
            member, peers, Reply = await self._candidate(cluster, index)
            term = member.node.term

            for peer in peers[:3]:
                member.node.on_request_vote_reply(peer, Reply(
                    term=term, granted=True, voting=False,
                ))
            member.node.on_request_vote_reply(peers[3], Reply(
                term=term, granted=False, voting=True,
            ))

            assert member.node.role is not Role.LEADER, (
                "a recovery election was won without the member that still "
                "remembers -- the only one whose up-to-dateness check still "
                "protects anything"
            )
        finally:
            await cluster.close()

    async def test_a_recovery_election_is_won_with_every_member_that_remembers(
        self, tmp_path: Path,
    ) -> None:
        """The same five, with the member that remembers granting."""
        cluster = await _cluster(5, tmp_path)
        try:
            leader = await cluster.elect()
            index = next(m.index for m in cluster.members if m is not leader)
            member, peers, Reply = await self._candidate(cluster, index)
            term = member.node.term

            for peer in peers[:3]:
                member.node.on_request_vote_reply(peer, Reply(
                    term=term, granted=True, voting=False,
                ))
            member.node.on_request_vote_reply(peers[3], Reply(
                term=term, granted=True, voting=True,
            ))

            assert member.node.role is Role.LEADER, (
                "every member that remembers granted and three were proven "
                "forgotten, yet no one was elected"
            )
        finally:
            await cluster.close()

    async def test_stale_evidence_cannot_elect_a_leader_without_a_committed_entry(
        self, tmp_path: Path,
    ) -> None:
        """E1, end to end, on the real network but for one late reply.

        P leads; R restarts; P receives R's late pre-vote reply ("cannot
        vote") -- in the soak it arrived one millisecond after P had won --
        and then promotes R. P is cut off while Q and R commit E. Q restarts;
        P and Q meet with R, the only member still holding E, out of reach. P
        used to win with Q's vote alone, counting R as forgotten on the old
        reply although P itself had promoted R, and overwrote E.
        """
        from nmos.raft.messages import RequestVoteReply

        async def until(ready: Any, seconds: float) -> bool:
            deadline = asyncio.get_running_loop().time() + seconds
            while asyncio.get_running_loop().time() < deadline:
                if ready():
                    return True
                await asyncio.sleep(cluster.timing.heartbeat)
            return False

        cluster = await _cluster(3, tmp_path)
        try:
            p = await cluster.elect()
            for tag in range(3):
                await asyncio.wait_for(p.node.propose(_register(
                    f"{tag:08x}-dead-4000-8000-00000000000b", p.index,
                )), 5.0)
            await cluster.settle(10)
            q, r = [m for m in cluster.members if m is not p]

            r = await cluster.restart(r.index)
            p.node.on_request_vote_reply(r.index, RequestVoteReply(
                term=p.node.term + 1, granted=False, voting=False,
                pre_vote=True,
            ))
            assert await until(lambda: r.node.voting, 5.0), (
                "R was never promoted, so this proves nothing"
            )

            cluster.network.partition({p.index}, {q.index, r.index})
            assert await until(
                lambda: any(m.node.role is Role.LEADER for m in (q, r)), 5.0,
            ), "Q and R never elected a leader, so this proves nothing"
            head = next(m for m in (q, r) if m.node.role is Role.LEADER)
            await asyncio.wait_for(head.node.propose(_register(
                "00000099-dead-4000-8000-00000000000b", head.index,
            )), 5.0)
            await cluster.settle(10)
            e_index = head.node.commit_index
            e_term = head.node.log.term_at(e_index)
            assert r.node.log.last_index >= e_index, (
                "R does not hold E, so this proves nothing"
            )
            assert p.node.log.last_index < e_index
            # Stood down, as check-quorum makes a leader cut off from a
            # majority do. Still leading, P would meet Q as the leader of a term
            # it already held -- unable to commit anything, and no election.
            assert await until(lambda: p.node.role is not Role.LEADER, 5.0), (
                "P never stood down while cut off, so this proves nothing"
            )

            q = await cluster.restart(q.index)
            cluster.network.partition({p.index, q.index}, {r.index})
            # Elected, that is, in a term after E's: a leader lacking E there
            # would overwrite it.
            elected = await until(
                lambda: p.node.role is Role.LEADER and p.node.term > e_term,
                3.0,
            )

            assert not elected, (
                f"P was elected in term {p.node.term} with only Q's vote -- Q "
                f"restarted and cannot vote, and R, voting and holding "
                f"E=({e_index}, t{e_term}), was out of reach -- so E, "
                f"committed, is lost: P's log ends at {p.node.log.last_index}"
            )
        finally:
            await cluster.close()


class TestAWaiterDoesNotOutliveItsCaller:
    """A proposal whose caller has stopped waiting leaves no waiter behind.

    A waiter was removed only when its entry applied (or the member closed).
    A forwarded proposal that never becomes an entry this member applies --
    its ``Propose`` never delivered, refused by a member that was no longer
    leader, or accepted and then overwritten -- kept its waiter for the life of
    the member. Measured by the Rust chaos soak (40 runs, 1,046 leaked
    waiters): 85% never delivered, 11% refused, 4% accepted and never applied
    here, every one forwarded, and in every run its client had already given
    up. etcd removes the waiter when the client's context ends
    (``v3_server.go:1117``, ``:1129``, "GC wait").
    """

    async def test_a_forwarded_proposal_that_never_arrives_leaves_no_waiter(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            member = next(
                m for m in cluster.members if m.index != leader.index
            )
            # Known, or the proposal is refused at once ("no leader elected")
            # and no waiter is ever registered: a leader is elected before
            # every follower has heard from it.
            for _ in range(200):
                if member.node.leader == leader.index:
                    break
                await asyncio.sleep(cluster.timing.heartbeat)
            assert member.node.leader == leader.index, (
                f"member {member.index} never learned its leader"
            )
            # Its way to the leader is cut; the leader's heartbeats still
            # arrive, so it keeps forwarding to it -- into nothing.
            cluster.network.block(member.index, leader.index)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    member.node.propose(_register(NODE_ID, member.index)), 0.2,
                )
            cluster.network.unblock(member.index, leader.index)
            await cluster.settle(20)

            waiting = list(member.node._waiters)      # noqa: SLF001
            assert not waiting, (
                f"its caller gave up, and member {member.index} still holds "
                f"a waiter for {waiting} -- for as long as it lives"
            )
        finally:
            await cluster.close()


class TestLosingLeadershipReleasesNothing:
    """A leader that stops leading fails none of its callers.

    etcd releases nothing when a leader steps down: a queued proposal is
    routed by the member's role when it drains, and an appended one waits for
    its entry -- which a later leader may commit -- or for its caller. The two
    implementations had each diverged from that, and from each other: this one
    failed its queued batch on relinquish and on step-down, the Rust one every
    registered waiter on relinquish.
    """

    async def test_a_proposal_queued_as_a_leader_relinquishes_is_not_failed(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            node = leader.node
            # Queued: the batcher drains on the next turn of the loop, and
            # nothing here awaits before leadership is lost.
            future = node.propose(_register(NODE_ID, leader.index))
            node._relinquish("the test took it away")      # noqa: SLF001

            assert not future.done(), (
                f"relinquishing failed a caller whose proposal had not even "
                f"been routed: {future.exception()!r}"
            )
            future.cancel()
        finally:
            await cluster.close()

    async def test_a_proposal_queued_as_a_leader_steps_down_is_not_failed(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            node = leader.node
            future = node.propose(_register(NODE_ID, leader.index))
            node._step_down(node.term + 1)      # noqa: SLF001

            assert not future.done(), (
                f"stepping down failed a caller whose proposal had not even "
                f"been routed: {future.exception()!r}"
            )
            future.cancel()
        finally:
            await cluster.close()


async def _knows_its_leader(cluster: Cluster, member: Any, leader: Any) -> None:
    for _ in range(200):
        if member.node.leader == leader.index:
            return
        await asyncio.sleep(cluster.timing.heartbeat)
    raise AssertionError(f"member {member.index} never learned its leader")


class TestReadIndex:
    """etcd's ReadIndex, quorum-confirmed (``ReadOnlySafe``).

    The index a read must have applied before it may answer from its own
    store: the commit index when the read began, released once a quorum has
    confirmed, after that moment, that the leader giving it still leads. Each
    test is one condition etcd's raft imposes on it (``raft.go:1354-1368``,
    ``:1600-1609``, ``:1764-1770``, ``:2146-2156``).
    """

    async def test_a_leader_confirms_a_read_with_a_quorum(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            await leader.node.propose(_register(NODE_ID, leader.index))
            committed = leader.node.commit_index

            index = await leader.node.read_index(timeout=1.0)
            assert index >= committed, (
                f"a read begun after index {committed} committed was given "
                f"index {index}"
            )
        finally:
            await cluster.close()

    async def test_a_leader_cut_off_from_its_quorum_confirms_no_read(
        self, tmp_path: Path,
    ) -> None:
        """Its commit index may already be stale: a majority can have elected
        a leader and committed without it, and it cannot tell."""
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            await leader.node.propose(_register(NODE_ID, leader.index))
            cluster.network.isolate(leader.index)

            with pytest.raises(RaftUnavailable):
                index = await leader.node.read_index(timeout=1.0)
                pytest.fail(f"a leader nobody could hear confirmed index {index}")
        finally:
            await cluster.close()

    async def test_a_new_leader_confirms_no_read_below_what_it_inherited(
        self, tmp_path: Path,
    ) -> None:
        """Nothing is read until an entry of its own term commits.

        A new leader holds every committed entry but may not know an earlier
        leader committed it (Raft §8), so its commit index can sit below what
        a client has been told is done. etcd postpones reads until then
        (``committedEntryInCurrentTerm``, ``raft.go:1363-1367``). Built here
        exactly: an old leader commits E with one follower and is cut off
        before telling it; that follower wins the next term knowing E only as
        uncommitted, and is asked for a read before its first entry commits.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            old = await cluster.elect(timeout=5.0)
            heir, other = (m for m in cluster.members if m.index != old.index)
            await _knows_its_leader(cluster, heir, old)
            await _knows_its_leader(cluster, other, old)

            # ``other`` hears nothing more from the old leader, and ``heir``
            # nothing after acknowledging E: the commit is never passed on.
            cluster.network.block(old.index, other.index)
            e_index = old.node._log.last_index + 1      # noqa: SLF001
            answered = old.node.on_append_entries_reply

            def acknowledged(peer: int, message: AppendEntriesReply) -> None:
                if peer == heir.index and message.match_index >= e_index:
                    # Silently: announced, the old leader would take it as the
                    # heir reconnecting and disown this very acknowledgement.
                    cluster.network.lose(old.index, heir.index)
                answered(peer, message)

            old.node.on_append_entries_reply = acknowledged  # type: ignore[method-assign]
            await old.node.propose(_register(NODE_ID, old.index))
            assert old.node.commit_index >= e_index
            assert heir.node.commit_index < e_index, (
                "the heir learned that E committed; the scenario needs it not to"
            )

            cluster.network.stop(old.index)
            deadline = asyncio.get_running_loop().time() + 5.0
            while heir.node.role is not Role.LEADER:
                assert asyncio.get_running_loop().time() < deadline, (
                    f"the heir never won: {heir.node.role.value}"
                )
                await asyncio.sleep(0)
            # Its own first entry must not commit yet: hold back the only
            # acknowledgements that could commit it.
            cluster.network.block(other.index, heir.index)
            assert heir.node.commit_index < e_index

            read = asyncio.ensure_future(heir.node.read_index(timeout=2.0))
            for _ in range(10):
                await asyncio.sleep(0)
            cluster.network.unblock(other.index, heir.index)

            index = await read
            assert index >= e_index, (
                f"a read on the new leader was given index {index}, below E at "
                f"{e_index} -- committed and acknowledged before the read began"
            )
        finally:
            await cluster.close()

    async def test_a_follower_is_given_its_leaders_read_index(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            follower = next(m for m in cluster.members if m.index != leader.index)
            await _knows_its_leader(cluster, follower, leader)
            await leader.node.propose(_register(NODE_ID, leader.index))
            committed = leader.node.commit_index

            index = await follower.node.read_index(timeout=1.0)
            assert index >= committed, (
                f"a read begun at a follower after index {committed} committed "
                f"was given index {index}"
            )
        finally:
            await cluster.close()

    async def test_a_member_with_no_leader_is_told_at_once(
        self, tmp_path: Path,
    ) -> None:
        """etcd drops the request with no leader (``raft.go:1764-1768``);
        a caller here is told at once rather than left to its deadline.

        One member of three, started alone: it can never elect anyone, so it
        has no leader for as long as the test cares to look. (A member cut off
        after hearing from its leader keeps naming it -- it cannot campaign
        without a quorum -- and is refused by its link instead.)
        """
        cluster = Cluster(3, tmp_path)
        alone = cluster.members[0]
        await alone.node.start()
        try:
            await cluster.settle(5)
            assert alone.node.leader is None

            loop = asyncio.get_running_loop()
            started = loop.time()
            with pytest.raises(RaftUnavailable, match="no leader"):
                await alone.node.read_index(timeout=5.0)
            assert loop.time() - started < 1.0
        finally:
            await alone.node.close()
            await cluster.network.drain()

    async def test_a_lone_voter_reads_at_its_commit_index(
        self, tmp_path: Path,
    ) -> None:
        """Answered at once, as etcd answers one (``raft.go:1355-1361``)."""
        cluster = await _cluster(1, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            await leader.node.propose(_register(NODE_ID, leader.index))

            assert await leader.node.read_index(timeout=1.0) == (
                leader.node.commit_index
            )
        finally:
            await cluster.close()

    async def test_a_reply_that_says_catching_up_confirms_no_read(
        self, tmp_path: Path,
    ) -> None:
        """Only a voter's answer confirms a read, judged by the reply itself.

        etcd counts voters' acknowledgements alone (``raft.go:1604-1605``),
        as this leader counts only voters toward a commit and check-quorum. It
        judged a reply by the flag the leader held *before* reading it, so the
        first reply of a member that had restarted with nothing -- the one
        that says it is catching up -- was counted as a voter's.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            await leader.node.propose(_register(NODE_ID, leader.index))
            restarted, gone = (
                m for m in cluster.members if m.index != leader.index
            )
            # Nobody else can answer: one member is gone, and the other's own
            # replies are lost -- silently, so the leader goes on sending it
            # heartbeats -- and the one below is its first.
            cluster.network.stop(gone.index)
            cluster.network.lose(restarted.index, leader.index)
            read = asyncio.ensure_future(leader.node.read_index(timeout=1.0))
            await asyncio.sleep(0)
            (recorded,) = leader.node._reads      # noqa: SLF001
            heartbeat = leader.node._append_sequence      # noqa: SLF001
            assert recorded.index is not None and heartbeat > recorded.after, (
                "no heartbeat was sent after the read was recorded; the reply "
                "below would answer nothing it waits for"
            )

            leader.node.on_append_entries_reply(restarted.index, AppendEntriesReply(
                term=leader.node.term, success=True, match_index=0,
                conflict_index=0, conflict_term=0, catching_up=True,
                request_id=heartbeat,
            ))
            for _ in range(10):
                await asyncio.sleep(0)
            assert not read.done(), (
                f"a member that said it was catching up confirmed a read: "
                f"{read.result() if not read.exception() else read.exception()!r}"
            )
            read.cancel()
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


def _ahead_of_the_clock() -> TaiCursor:
    """A cursor the log holds, a minute past this member's clock.

    What a member is left with when its wall clock steps back, or while a
    peer's clock runs fast: every cursor it has applied is in its future, so
    each allocation is pushed above the log rather than read from the clock.
    """
    return TaiCursor(TaiCursor.now().seconds + 60, 0)


class TestCursorsAcrossRestarts:
    """A paging cursor must be unique over a member's history, not its run.

    The cursor counterpart of ``TestProposalIdentityAcrossRestarts``, and found
    the same way: the chaos soak reported two Nodes holding one cursor, five
    times in about 12,000 runs, every pair minted by consecutive incarnations of
    one member, each time while that machine's wall clock had stepped back.
    Owner bits keep members apart but not incarnations; once the log is ahead
    of the clock an allocation depends only on the log prefix applied, and a
    restarted member that replays the same prefix mints the same cursor.
    """

    async def test_a_cursor_handed_out_before_a_restart_is_never_handed_out_again(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            await cluster.elect(timeout=5.0)
            member = cluster[1]
            ahead = _ahead_of_the_clock()

            member.node.cursors.observe(ResourceType.NODE, ahead)
            before = member.node.allocate_cursor(ResourceType.NODE)

            replacement = await cluster.restart(member.index)
            # The new incarnation replays the log it rejoins -- the same prefix,
            # so the same high-water mark -- before it allocates.
            replacement.node.cursors.observe(ResourceType.NODE, ahead)
            after = replacement.node.allocate_cursor(ResourceType.NODE)

            assert after > before, (
                f"incarnation {replacement.node.incarnation} handed out {after}, "
                f"and incarnation {replacement.node.incarnation - 1} had "
                f"already handed out {before}: two resources can now share a "
                f"paging cursor"
            )
        finally:
            await cluster.close()

    async def test_a_term_change_carries_the_reservation_forward(
        self, tmp_path: Path,
    ) -> None:
        """A save for the vote rewrites the whole file, reservation included.

        Every save replaces the file, so a term change that wrote only the term
        and vote would erase the bound the last allocation recorded, and the
        next incarnation would resume below cursors already handed out.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            member = next(
                m for m in cluster.members if m.index != leader.index
            )
            ahead = _ahead_of_the_clock()
            member.node.cursors.observe(ResourceType.NODE, ahead)
            before = member.node.allocate_cursor(ResourceType.NODE)
            term = member.node.term

            # A new election: the member adopts a later term and saves it.
            await cluster.restart(leader.index)
            await cluster.elect(timeout=5.0)
            assert member.node.term > term, "no term change reached the member"

            replacement = await cluster.restart(member.index)
            replacement.node.cursors.observe(ResourceType.NODE, ahead)
            assert replacement.node.allocate_cursor(ResourceType.NODE) > before
        finally:
            await cluster.close()

    async def test_a_reservation_that_cannot_be_written_hands_out_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The cursor stays inside the member, and the allocator moves past it.

        Returning it anyway would reopen the defect exactly when the disk is
        already misbehaving; refusing makes the registration a retryable 503.
        """
        cluster = await _cluster(1, tmp_path)
        try:
            member = cluster[0]
            member.node.cursors.observe(ResourceType.NODE, _ahead_of_the_clock())
            durable = member.node.cursors.reservation

            def refuse(_store: TermStore, _state: PersistentState) -> None:
                raise OSError(28, "No space left on device")

            monkeypatch.setattr(TermStore, "save", refuse)
            with pytest.raises(RaftCursorReservationFailed, match="No space left"):
                member.node.allocate_cursor(ResourceType.NODE)
            assert member.node.cursors.reservation == durable

            monkeypatch.undo()
            refused = member.node.cursors.high_water(ResourceType.NODE)
            assert refused is not None
            assert member.node.allocate_cursor(ResourceType.NODE) > refused
        finally:
            await cluster.close()


_UNUSABLE = (
    pytest.param(
        '{\n  "version": 2,\n  "term": 7,\n  "voted_for": 0,\n'
        '  "incarnation": 3,\n  "cursor_reservation": null\n}',
        id="a newer state version",
    ),
    pytest.param(
        '{\n  "version": 1,\n  "term": 7,\n  "voted_fo',
        id="a torn file",
    ),
)
"""A term file recording a vote for member 0 in term 7, in two unusable forms:
written by a build with a newer state version, which is what a rollback leaves
behind, and torn mid-write."""


def _grants_member_two(node: RaftNode, term: int) -> bool:
    """Whether ``node`` grants member 2 its vote in ``term``."""
    from nmos.raft.messages import RequestVote

    reply = node.on_request_vote(2, RequestVote(
        term=term, candidate=2, last_log_index=0, last_log_term=0,
        pre_vote=False,
    ))
    return reply.granted


class TestAnUnusableTermFileRefusesToStart:
    """A member that cannot read its term file must not start.

    The term file is what makes a vote durable, so a member that cannot read it
    cannot know whether it has already voted. The constructor raises the
    refusal and nothing catches it, so the process exits. The Rust port once
    logged it and carried on as a brand-new member -- term 0, no vote,
    incarnation 1 -- and, measured, then granted a second vote in a term its
    file had recorded a vote in, and overwrote the file with the second. Its
    ``a_member_whose_term_file_is_unusable_refuses_to_start`` and
    ``a_member_that_cannot_write_its_first_term_file_refuses_to_start`` are
    these two tests.
    """

    @pytest.mark.parametrize("text", _UNUSABLE)
    async def test_a_member_whose_term_file_is_unusable_refuses_to_start(
        self, tmp_path: Path, text: str,
    ) -> None:
        path = tmp_path / "m1-state.json"
        path.write_text(text, encoding="utf-8")

        try:
            cluster = Cluster(3, tmp_path)
        except PersistentStateError as exc:
            refusal = exc
        else:
            node = cluster[1].node
            term, incarnation = node.term, node.incarnation
            granted = _grants_member_two(node, 7)
            pytest.fail(
                f"member 1 started over an unusable term file, as term {term} "
                f"incarnation {incarnation}, and "
                f"{'granted' if granted else 'refused'} member 2 a vote in "
                f"term 7 -- the term its file had recorded a vote for member "
                f"0 in",
            )

        # The store's own refusal, word for word: it names the file and says
        # what to do about it.
        with pytest.raises(PersistentStateError) as expected:
            TermStore(path).load()
        assert str(refusal) == str(expected.value)
        # Left as found: it is the only record of the vote.
        assert path.read_text(encoding="utf-8") == text

    async def test_a_member_that_cannot_write_its_first_term_file_refuses_to_start(
        self, tmp_path: Path,
    ) -> None:
        """A member with no term file yet must be able to write its first.

        It is new, and starts from nothing -- once that nothing is on disk.
        Started without it, every vote it granted would be one a restart
        forgets. Raised as the ``OSError`` it is: ``PersistentStateError`` is
        for a file that is unreadable or not ours. (The Rust port reports both
        as one error type, the store's.)
        """
        try:
            Cluster(3, tmp_path / "gone")
        except FileNotFoundError as exc:
            refusal = exc
        else:
            pytest.fail("member 1 started with nowhere to record a vote")
        # The first write, not anything else that could be missing.
        assert ".raft-state-" in str(refusal.filename), refusal


def _break_saves(root: Path, member: int) -> None:
    """Make every later save of ``member`` fail.

    Its term file becomes a directory, so the replace that publishes a save
    fails -- for root too, which a read-only directory would not stop.
    """
    path = root / f"m{member}-state.json"
    path.unlink()
    (path / "in-the-way").mkdir(parents=True)


def _vote_for(node: RaftNode, candidate: int, term: int) -> Any:
    """``candidate`` asking for a vote in ``term``, with a log nothing is ahead of."""
    from nmos.raft.messages import RequestVote

    return node.on_request_vote(candidate, RequestVote(
        term=term, candidate=candidate, last_log_index=0, last_log_term=0,
    ))


def _append_in(node: RaftNode, term: int) -> AppendEntriesReply:
    """A heartbeat from member 0 as leader of ``term``."""
    return node.on_append_entries(0, AppendEntries(
        term=term, leader=0, prev_log_index=0, prev_log_term=0,
        leader_commit=0, request_id=0,
    ))


def _snapshot_in(node: RaftNode, term: int) -> Any:
    """A first snapshot chunk from member 0 as leader of ``term``."""
    return node.on_install_snapshot(0, InstallSnapshot(
        term=term, leader=0, last_index=0, last_term=0, offset=0, data=b"",
        done=False,
    ))


def _refused_in(node: RaftNode, term: int, *, pre_vote: bool) -> None:
    """Member 0 refusing this member its vote -- or its pre-vote -- in a reply
    that carries ``term``."""
    from nmos.raft.messages import RequestVoteReply

    node.on_request_vote_reply(0, RequestVoteReply(
        term=term, granted=False, voting=True, pre_vote=pre_vote,
    ))


def _append_reply_in(node: RaftNode, term: int) -> None:
    """An append reply from member 0 that carries ``term``."""
    node.on_append_entries_reply(0, AppendEntriesReply(
        term=term, success=False, match_index=0, conflict_index=0,
        conflict_term=0, catching_up=False, request_id=0,
    ))


def _snapshot_reply_in(node: RaftNode, term: int) -> None:
    """A snapshot reply from member 0 that carries ``term``."""
    from nmos.raft.messages import InstallSnapshotReply

    node.on_install_snapshot_reply(0, InstallSnapshotReply(
        term=term, bytes_received=0, done=False,
    ))


def _goes_through(probe: Callable[[], object]) -> bool:
    """Whether ``probe`` ran to the end rather than raising its failed save."""
    try:
        probe()
    except OSError:
        return False
    return True


class TestADecisionWhoseSaveFailedIsNotSent:
    """A term or vote that could not be saved never leaves the member.

    Granted and then forgotten at a restart, it is the double vote the term file
    exists to prevent. The save raises out of whatever made the decision, so
    nothing that rests on it is sent: the transport closes the connection the
    message came by (``_serve``) or drops the link it came back on
    (``_maintain``), and a campaign ends before its requests
    (``_tick_forever``). The Rust port logged the failed save and carried on --
    measured, with every save failing it granted a vote, answered a newer
    term's append and snapshot, and, alone, made itself leader. Its four tests
    of the same names are these.
    """

    async def test_a_vote_that_cannot_be_saved_is_not_granted(
        self, tmp_path: Path,
    ) -> None:
        cluster = Cluster(3, tmp_path)
        try:
            node = cluster[1].node
            # Into term 1 while saves still work, by a refusal that carries it:
            # the member steps up with no leader, so no lease stands in the way,
            # and the vote below is the only thing left to save.
            _refused_in(node, 1, pre_vote=False)
            _break_saves(tmp_path, 1)

            try:
                reply = _vote_for(node, 2, 1)
            except OSError:
                pass
            else:
                pytest.fail(
                    f"member 1 answered member 2's request for its vote in "
                    f"term 1 (granted={reply.granted}) with nothing saved",
                )
            # Kept in memory: while it runs, this member gives nobody else its
            # vote in term 1.
            assert not _vote_for(node, 0, 1).granted, (
                "member 1 granted member 0 its vote in term 1, having given it "
                "to member 2"
            )
        finally:
            await cluster.close()

    async def test_a_newer_term_that_cannot_be_saved_is_not_answered(
        self, tmp_path: Path,
    ) -> None:
        cluster = Cluster(3, tmp_path)
        try:
            node = cluster[1].node
            _break_saves(tmp_path, 1)

            probes: list[tuple[str, Callable[[], object]]] = [
                ("RequestVote", lambda: _vote_for(node, 2, 1)),
                ("AppendEntries", lambda: _append_in(node, 2)),
                ("InstallSnapshot", lambda: _snapshot_in(node, 3)),
            ]
            answered = [kind for kind, probe in probes if _goes_through(probe)]
            assert not answered, (
                f"member 1 answered {answered} in terms it could not save"
            )
            # Adopted in memory, so nothing older is taken for current while
            # the member runs.
            assert node.term == 3
        finally:
            await cluster.close()

    async def test_a_reply_whose_newer_term_cannot_be_saved_ends_there(
        self, tmp_path: Path,
    ) -> None:
        cluster = Cluster(3, tmp_path)
        try:
            node = cluster[1].node
            _break_saves(tmp_path, 1)

            probes: list[tuple[str, Callable[[], object]]] = [
                ("RequestVoteReply", lambda: _refused_in(node, 1, pre_vote=False)),
                ("RequestVoteReply (pre-vote)",
                 lambda: _refused_in(node, 2, pre_vote=True)),
                ("AppendEntriesReply", lambda: _append_reply_in(node, 3)),
                ("InstallSnapshotReply", lambda: _snapshot_reply_in(node, 4)),
            ]
            went_on = [kind for kind, probe in probes if _goes_through(probe)]
            assert not went_on, (
                f"member 1 went on from {went_on} in terms it could not save"
            )
            assert node.term == 4
        finally:
            await cluster.close()

    async def test_a_member_that_cannot_save_its_vote_for_itself_never_leads(
        self, tmp_path: Path,
    ) -> None:
        """Alone, it wins every election it holds -- once its vote for itself
        is on disk. ``_campaign`` raises before counting that vote, and each
        election timeout tries again with the next term, in memory only."""
        lone = Cluster(1, tmp_path)
        _break_saves(tmp_path, 0)
        await lone.start()
        try:
            node = lone[0].node
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 5.0
            led = False
            while node.term < 5 and loop.time() < deadline:
                led = led or node.role is Role.LEADER
                await asyncio.sleep(lone.timing.heartbeat)
            led = led or node.role is Role.LEADER
            term = node.term
        finally:
            await lone.close()
        assert not led, (
            f"member 0 led on a vote for itself it never saved (now term {term})"
        )
        assert term >= 5, f"member 0 stopped campaigning at term {term}"


def _catching_up_timing() -> RaftTiming:
    """``FAST``, compacting early and sending snapshots in small chunks.

    A member that restarts then needs a snapshot of many chunks, one round trip
    each, to catch up -- far longer than the tick check-quorum runs on.
    """
    return dataclasses.replace(FAST, compaction_threshold=8, snapshot_chunk=64)


async def _two_of_three_restart_together(
    tmp_path: Path,
) -> tuple[Cluster, Any, list[int]]:
    """A cluster whose leader and one follower restart together.

    The third member -- the survivor -- is left the only one holding the log,
    so it is the only member that can lead, and the only one that can bring
    the other two back to voting.
    """
    cluster = Cluster(3, tmp_path, timing=_catching_up_timing())
    await cluster.start()
    leader = await cluster.elect(timeout=5.0)
    await asyncio.gather(*(
        leader.node.propose(_register(str(uuid.uuid4()), leader.index))
        for _ in range(40)
    ))
    await cluster.settle(10)
    survivor = cluster[(leader.index + 2) % 3]
    restarted = [leader.index, (leader.index + 1) % 3]
    for index in restarted:
        await cluster.restart(index)
    return cluster, survivor, restarted


class TestAMajorityCatchingUp:
    """Two of three members restart together: the survivor must lead them back.

    Found on Windows and measured here: the survivor won the election, then
    stepped down one tick later -- "lost contact with a quorum" -- although both
    peers were answering, because check-quorum left out members catching up
    while the quorum stayed two of three. Its campaign check left them out too,
    so it never campaigned again; the restarted members could not win either,
    their logs being shorter. No leader, for good.

    A member catching up that answers proves the leader is not cut off, so
    check-quorum counts it, and so does the campaign check, whose election the
    round itself then decides; commits, read confirmations and votes still
    leave it out (``_advance_commit``, ``_confirm_reads``, ``_won``), so
    nothing that decides safety changes -- only when a leader gives up and
    when a member tries. etcd leaves learners out of ``QuorumActive`` because
    they are outside its voter set, and so outside the quorum too. The Rust
    tests of the same names are these three.
    """

    async def test_the_survivor_leads_two_restarted_members_back_to_voting(
        self, tmp_path: Path,
    ) -> None:
        cluster, survivor, restarted = await _two_of_three_restart_together(
            tmp_path,
        )
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 10.0
            while loop.time() < deadline and not all(
                cluster[index].node.voting for index in restarted
            ):
                await asyncio.sleep(cluster.timing.heartbeat)
            voting = [cluster[index].node.voting for index in restarted]
            leaders = [m.index for m in cluster.members if m.node.role is Role.LEADER]
            assert all(voting), (
                f"10 s after two of three restarted they are still catching up "
                f"(voting {voting}), the survivor m{survivor.index} is "
                f"{survivor.node.role.value}, leaders {leaders}: nothing can "
                f"be written"
            )
            # And the cluster writes again.
            leader = await cluster.elect(timeout=5.0)
            await asyncio.wait_for(
                leader.node.propose(_register(str(uuid.uuid4()), leader.index)),
                5.0,
            )
        finally:
            await cluster.close()

    async def test_a_leader_whose_peers_answer_as_catching_up_keeps_leading(
        self, tmp_path: Path,
    ) -> None:
        """Check-quorum asks whether this leader is cut off, and it is not.

        Peers that answered within the window are reachable, catching up or
        not; with a majority of the cluster answering, no other member can
        gather one, so standing down would only leave nobody to catch them up.
        """
        cluster = Cluster(3, tmp_path)
        try:
            node = cluster[0].node
            now = asyncio.get_running_loop().time()
            for peer in node._peers.values():
                peer.catching_up = True
                peer.last_heard_at = now

            assert node._quorum_is_answering(now), (
                "both peers answered just now, catching up, and this leader "
                "counts itself cut off: it stands down with nobody else able "
                "to lead them"
            )
        finally:
            await cluster.close()

    async def test_a_member_whose_reachable_peers_are_catching_up_still_campaigns(
        self, tmp_path: Path,
    ) -> None:
        """Whether an election could be won is for the round to decide.

        The flags are what this member learned leading them -- they outlive the
        leadership, cleared only by a promotion, a new leadership or a link
        going down -- and a campaign check that left them out made the member
        that had to lead them unable ever to try again.
        """
        cluster = Cluster(3, tmp_path)
        try:
            node = cluster[0].node
            for peer in node._peers.values():
                peer.up = True
                peer.catching_up = True
            node._deadline = 0.0

            node._tick()

            assert node.role is Role.PRE_CANDIDATE, (
                f"its election timer ran out with both peers reachable and it "
                f"stayed a {node.role.value}: a member whose peers are catching "
                f"up never campaigns"
            )
        finally:
            await cluster.close()

    async def test_a_leader_whose_majority_is_catching_up_reports_no_quorum(
        self, tmp_path: Path,
    ) -> None:
        """Readiness asks whether a write could commit, and it could not."""
        cluster = Cluster(3, tmp_path)
        try:
            node = cluster[0].node
            for peer in node._peers.values():
                peer.up = True
                peer.catching_up = True

            assert not node.has_quorum, (
                "both peers are catching up, so nothing can commit, yet the "
                "member reports a quorum -- the backend would call itself READY"
            )
        finally:
            await cluster.close()


class TestALeaderReinitialisesWhatItKnowsAboutItsFollowers:
    """Figure 2: nextIndex and matchIndex are "reinitialized after election".

    This implementation tracks more per follower than the paper does, and every
    one of those fields describes *this leader's* relationship with the peer,
    so all of them have the same lifetime. Carrying any of them across a term
    is a leader acting on something a previous leadership observed.

    One of them was a safety bug, and it is the reason this class exists.
    ``promote_through`` -- the index a restarted member must reach before its
    vote counts again -- is only ever assigned on a False-to-True transition of
    ``catching_up``. A stale ``catching_up=True`` therefore means a new leader
    never re-decides the bar, and promotes the member back into the electorate
    at whatever index some earlier term happened to be at.

    A voter that is missing committed entries can grant a vote the election
    restriction exists to refuse, and the next leader is then elected without
    an entry that was committed. That is Leader Completeness, and the chaos
    soak reported exactly it.
    """

    async def test_a_new_term_re_decides_how_far_a_peer_must_catch_up(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            node = leader.node
            peer = next(p for p in node._peers if p != node.index)

            for tag in range(5):
                await asyncio.wait_for(
                    node.propose(_register(_stale_id(tag), 0)), timeout=5.0,
                )
            await cluster.settle(20)
            committed = node.commit_index
            assert committed > 1, "nothing was committed, so this proves nothing"

            # What a previous leadership would have left behind: this peer was
            # catching up then, and the bar was set against that term's commit
            # index rather than this one's.
            state = node._peers[peer]
            state.catching_up = True
            state.promote_through = 1

            node._become_leader()

            assert state.catching_up is False, (
                "a new leader inherited 'catching up' from an earlier term, so "
                "the promotion bar below can never be re-decided"
            )

            # The peer reports it is catching up, as a restarted member does.
            holding = node.log.last_index
            node.on_append_entries_reply(peer, AppendEntriesReply(
                term=node.term, success=True, match_index=2,
                conflict_index=0, conflict_term=0,
                catching_up=True, request_id=state.reply_floor + 1,
            ))

            # The bar is this leader's whole log as it stands, not its commit
            # index: every committed entry is in it, including ones an earlier
            # leader committed that this one has not yet learned are committed.
            # See `test_a_restarted_member_is_not_promoted_below_an_earlier_commit`
            # for what barring at the commit index cost.
            assert state.promote_through == holding, (
                f"this peer will be promoted back to voting at index "
                f"{state.promote_through}, but this leader holds entries "
                f"through {holding} (committed through {committed}) -- it "
                f"could rejoin the electorate missing committed entries"
            )
        finally:
            await cluster.close()

    async def test_in_flight_bookkeeping_does_not_survive_the_term(
        self, tmp_path: Path,
    ) -> None:
        """Not safety, but the same lifetime mistake.

        A correlation id from an append this member sent while previously
        leader makes ``_carrying_entries_would_repeat_them`` report an outstanding request that
        no longer exists, which suppresses replication to that peer until the
        ``election_min`` backstop expires. A snapshot offset carried over is
        worse in kind: the leader resumes a stream the peer is not expecting.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            node = leader.node
            peer = next(p for p in node._peers if p != node.index)

            state = node._peers[peer]
            state.pending_request = 4242
            state.pending_through = 99
            state.pending_since = 1.0
            state.snapshot_offset = 512
            state.snapshot_request = 4343

            node._become_leader()

            # Asserted as "not the stale value" rather than "zero", because
            # becoming leader replicates immediately and legitimately arms a
            # *new* append before this line runs. Zero would be testing the
            # instant between the reset and the first send, which is not an
            # instant any caller observes.
            assert state.pending_request != 4242, (
                "a correlation id from an earlier leadership survived; "
                "_carrying_entries_would_repeat_them would suppress replication to this peer "
                "until the election_min backstop expires"
            )
            assert state.pending_through != 99
            assert state.pending_since != 1.0
            assert state.snapshot_offset == 0, (
                "a snapshot offset from an earlier term survived, so this "
                "leader would resume a stream the peer is not expecting"
            )
            assert state.snapshot_request != 4343, (
                "a chunk id from an earlier leadership survived, so the "
                "reply to a chunk this term never sent would be awaited"
            )
        finally:
            await cluster.close()

    async def test_what_is_about_the_peer_rather_than_the_term_is_kept(
        self, tmp_path: Path,
    ) -> None:
        """The other half of the rule, so the reset does not become "clear all".

        ``up`` and ``incarnation`` are observations about the peer itself, not
        about this leadership. Clearing them would make a healthy cluster look
        down for a tick and would lose the boot identity that tells a leader a
        peer has restarted.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            node = leader.node
            peer = next(p for p in node._peers if p != node.index)

            state = node._peers[peer]
            state.up = True
            state.incarnation = 7

            node._become_leader()

            assert state.up is True, "liveness is not a property of the term"
            assert state.incarnation == 7, "the peer did not reboot on our election"
        finally:
            await cluster.close()


class TestTheFollowerCommitRule:
    """Figure 2, AppendEntries receiver rule 5, and the word that matters.

        "If leaderCommit > commitIndex, set commitIndex =
         min(leaderCommit, index of last new entry)"

    *New entry* -- what this message delivered -- not the follower's own last
    index. An earlier version used the latter, and the chaos soak caught the
    difference as a State Machine Safety violation: index 3 applied from term 1
    on an isolated member while the majority held a term 2 entry there.

    The gap opens whenever a follower's log runs ahead of what the leader has
    vouched for, which is precisely the situation a heartbeat describes -- it
    carries a commit index and no entries, so it says nothing about anything
    past ``prev_log_index``.
    """

    async def test_a_heartbeat_does_not_commit_entries_it_said_nothing_about(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            follower = cluster.members[1]
            term = follower.node.term or 1

            # Three entries, none of them yet declared committed.
            entries = tuple(
                WireEntry(
                    term=term, index=index,
                    payload=encode_operation(
                        _register(_stale_id(index), 0),
                    ),
                )
                for index in (1, 2, 3)
            )
            first = follower.node.on_append_entries(0, AppendEntries(
                term=term, leader=0, prev_log_index=0, prev_log_term=0,
                leader_commit=0, request_id=0, entries=entries,
            ))
            assert first.success
            assert follower.node.log.last_index == 3
            assert follower.node.commit_index == 0

            # A heartbeat vouching only for index 1, but carrying a commit
            # index of 3. The leader is saying "I have committed through 3" --
            # about *its* log, which at index 2 and 3 may hold something else
            # entirely. This follower must not take that as permission to
            # commit the entries it happens to be holding.
            second = follower.node.on_append_entries(0, AppendEntries(
                term=term, leader=0, prev_log_index=1, prev_log_term=term,
                leader_commit=3, request_id=0, entries=(),
            ))
            assert second.success
            assert follower.node.commit_index <= 1, (
                f"committed through {follower.node.commit_index} on a "
                f"heartbeat that vouched only for index 1"
            )
        finally:
            await cluster.close()

    async def test_the_commit_index_never_moves_backwards(
        self, tmp_path: Path,
    ) -> None:
        """The other half of the same rule, and the reason for the comparison.

        ``min(leaderCommit, vouched_for)`` can land *below* where this member
        already is -- a short heartbeat after a long append. Assigning it would
        un-apply committed state, which is the one thing a state machine may
        never do, so the new value is compared before it is taken.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            follower = cluster.members[1]
            term = follower.node.term or 1

            entries = tuple(
                WireEntry(
                    term=term, index=index,
                    payload=encode_operation(_register(_stale_id(index), 0)),
                )
                for index in (1, 2, 3)
            )
            follower.node.on_append_entries(0, AppendEntries(
                term=term, leader=0, prev_log_index=0, prev_log_term=0,
                leader_commit=3, request_id=0, entries=entries,
            ))
            committed = follower.node.commit_index
            assert committed == 3

            follower.node.on_append_entries(0, AppendEntries(
                term=term, leader=0, prev_log_index=1, prev_log_term=term,
                leader_commit=3, request_id=0, entries=(),
            ))
            assert follower.node.commit_index == committed, (
                "a short heartbeat pulled the commit index backwards"
            )
        finally:
            await cluster.close()

    async def test_a_follower_vouches_only_for_what_the_leader_sent(
        self, tmp_path: Path,
    ) -> None:
        """The same window, seen from the reply rather than from the commit.

        Rule 5 stops this follower committing entries the message said nothing
        about. It does not stop it *telling the leader it has them*, and that
        is a second way into the same violation -- through the other member's
        log rather than through this one's.

        Figure 2 has the leader set ``matchIndex = prevLogIndex +
        entries.length`` from what it sent. This implementation has the
        follower compute that quantity and return it, which is equivalent so
        long as the follower returns the same number. Returning its own
        ``log.last_index`` instead overstates by exactly the stale suffix, and
        ``on_append_entries_reply`` stores the answer verbatim, so
        ``_advance_commit`` then counts a member toward the quorum at an index
        where it holds something else entirely.

        Found by ``test_churn_over_real_sockets`` as index 7 applied at term 2
        by one member and at term 3 by another, about one run in ten.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            follower = cluster.members[1]
            term = follower.node.term or 1

            entries = tuple(
                WireEntry(
                    term=term, index=index,
                    payload=encode_operation(_register(_stale_id(index), 0)),
                )
                for index in (1, 2, 3)
            )
            first = follower.node.on_append_entries(0, AppendEntries(
                term=term, leader=0, prev_log_index=0, prev_log_term=0,
                leader_commit=0, request_id=0, entries=entries,
            ))
            assert first.success
            assert first.match_index == 3, (
                "an append that delivered three entries matches at three"
            )
            assert follower.node.log.last_index == 3

            # A heartbeat vouching only for index 1. Entries 2 and 3 are this
            # follower's own, uncommitted, and unknown to this leader.
            second = follower.node.on_append_entries(0, AppendEntries(
                term=term, leader=0, prev_log_index=1, prev_log_term=term,
                leader_commit=1, request_id=0, entries=(),
            ))
            assert second.success
            assert second.match_index == 1, (
                f"claimed to match at {second.match_index} on a heartbeat that "
                f"vouched only for index 1; the leader stores this verbatim and "
                f"counts it toward the commit quorum"
            )

            # And it must still be exact when entries ride along with a
            # prev_log_index behind the follower's tail -- the retry case,
            # where the leader resends what it already sent.
            third = follower.node.on_append_entries(0, AppendEntries(
                term=term, leader=0, prev_log_index=1, prev_log_term=term,
                leader_commit=1, request_id=0, entries=entries[1:2],
            ))
            assert third.success
            assert third.match_index == 2, (
                f"a resend of one entry from index 1 matches at 2, not "
                f"{third.match_index}"
            )
        finally:
            await cluster.close()


class TestTheLeaderRecordsOnlyWhatItActuallyTold:
    """``sent_commit`` is what the *window* delivered, not what was committed.

    A follower adopts ``min(leader_commit, prev_log_index + len(entries))``, so
    a send whose entries were suppressed -- because an append to that peer is
    still in flight -- carries a window ending at ``prev_log_index`` no matter
    how far the leader has committed. Recording the full commit index there is
    the leader telling itself it has passed on something the peer could not
    take, and ``_should_send_now`` then finds nothing left to say and leaves
    the peer behind until the next tick.

    That is the same stall the eager send exists to remove, re-entering through
    the bookkeeping rather than through the missing send. Measured on an
    in-memory five-member cluster with a link slower than the tick: 2,444 of
    6,273 sends recorded a commit index above their own window.

    ``go.etcd.io/raft`` avoids it by splitting the message types -- MsgApp
    records ``committed`` because its window always reaches ``Next-1``
    (``raft.go:660``), MsgHeartbeat records ``min(pr.Match, committed)``
    (``raft.go:709``). One message type here, so the cap is by window.
    """

    async def test_a_suppressed_append_records_only_its_own_window(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            node = leader.node
            peer = next(p for p in node._peers if p != node.index)

            for tag in range(6):
                await asyncio.wait_for(
                    node.propose(_register(_stale_id(tag), 0)), timeout=5.0,
                )
            await cluster.settle(20)
            assert node.commit_index >= 4, "nothing committed, so this proves nothing"

            # The state the suppression exists for: this peer is behind, and an
            # append carrying what it is missing is already on the link.
            state = node._peers[peer]
            state.next_index = 2
            state.match_index = 1
            state.pending_request = 4242
            state.pending_through = node.log.last_index
            state.pending_since = asyncio.get_running_loop().time()
            assert node._carrying_entries_would_repeat_them(state), (
                "the append is not considered in flight, so the send below "
                "would carry entries and this tests nothing"
            )

            node._send_append(peer, state)

            # prev_log_index is next_index - 1 == 1, and no entries ride along,
            # so index 1 is every commit index this message can deliver.
            assert state.sent_commit == 1, (
                f"recorded having told member {peer} the commit index "
                f"{state.sent_commit}, but the message it just sent vouches "
                f"only through index 1 -- the peer cannot adopt more than that"
            )

            # And the consequence: the in-flight append lands, the peer is now
            # caught up on entries, and the only thing left to give it is the
            # commit index. An overstated record hides exactly this.
            state.pending_request = 0
            state.match_index = node.log.last_index
            state.next_index = node.log.last_index + 1
            assert node._should_send_now(state), (
                f"member {peer} holds every entry but has been told the commit "
                f"index only through {state.sent_commit} of "
                f"{node.commit_index}, and this leader sees nothing to send -- "
                f"so it learns the rest at the next heartbeat"
            )
        finally:
            await cluster.close()

    async def test_a_rejection_takes_back_what_the_window_no_longer_covers(
        self, tmp_path: Path,
    ) -> None:
        """``tracker/progress.go:142``: when ``Next`` regresses, so must this.

        A rejected append is one the peer did not take, so any commit index
        recorded as delivered through it was not delivered.

        Usually invisible, because the rejection handler resends immediately
        and that send re-vouches honestly for whatever window it carries. It
        becomes visible exactly where it matters: when the entries the peer
        needs have been compacted away, so the resend diverts to a snapshot and
        vouches for nothing at all. Left high, the record then suppresses every
        eager send to that peer for the whole length of the transfer.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            node = leader.node
            peer = next(p for p in node._peers if p != node.index)

            for tag in range(6):
                await asyncio.wait_for(
                    node.propose(_register(_stale_id(tag), 0)), timeout=5.0,
                )
            await cluster.settle(20)
            committed = node.commit_index
            assert committed >= 4, "nothing committed, so this proves nothing"

            # Everything this peer is about to ask for is gone, so the resend
            # cannot carry entries -- and with no snapshot built yet it carries
            # nothing whatsoever.
            node.log.discard_through(committed, node.log.term_at(committed))
            assert node._snapshot_meta is None, (
                "a snapshot exists, so the resend below is a transfer rather "
                "than a no-op and this tests the wrong path"
            )

            state = node._peers[peer]
            state.sent_commit = committed

            node.on_append_entries_reply(peer, AppendEntriesReply(
                term=node.term, success=False, match_index=0,
                conflict_index=2, conflict_term=node.term,
                catching_up=False, request_id=state.reply_floor + 1,
            ))

            assert state.sent_commit <= state.next_index - 1, (
                f"member {peer} rejected back to index {state.next_index} and "
                f"was sent nothing in reply, yet this leader still records "
                f"having told it the commit index {state.sent_commit} -- "
                f"through the very message it refused"
            )
        finally:
            await cluster.close()


class TestWhatTheLeaderWillBelieveAboutAPeer:
    """Bookkeeping that ``go.etcd.io/raft`` guards and this did not.

    All three come from reading the two implementations side by side rather
    than from a failing test, which is why each carries the number that made
    the case.
    """

    async def test_a_peer_is_never_recorded_as_holding_less_than_it_did(
        self, tmp_path: Path,
    ) -> None:
        """etcd's ``MaybeUpdate``: ``if n <= pr.Match { return false }``.

        A follower vouches for the window of the message it is answering, so
        an entries-less send draws a reply vouching for ``prev_log_index``
        alone -- less than a preceding append's reply vouched for. Taking that
        as news walks the peer backwards and re-sends entries it already holds.

        Measured before the guard: **418 of 2,744 replies on an idle
        five-member cluster, 15.2%**, and 23.6% over a slow link. Every one of
        them carried request id 0, which is the entries-less send.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            node = leader.node
            peer = next(p for p in node._peers if p != node.index)

            for tag in range(5):
                await asyncio.wait_for(
                    node.propose(_register(_stale_id(tag), 0)), timeout=5.0,
                )
            await cluster.settle(20)

            state = node._peers[peer]
            state.match_index = 5
            state.next_index = 6

            # What an entries-less send anchored at index 2 draws back.
            node.on_append_entries_reply(peer, AppendEntriesReply(
                term=node.term, success=True, match_index=2,
                conflict_index=0, conflict_term=0,
                catching_up=False, request_id=state.reply_floor + 1,
            ))

            assert state.match_index == 5, (
                f"member {peer} was recorded as holding only "
                f"{state.match_index} because a heartbeat vouched for that "
                f"much; it had already acknowledged 5"
            )
            assert state.next_index >= 6, (
                f"next_index fell to {state.next_index}, so this leader will "
                f"re-send entries member {peer} already holds"
            )
        finally:
            await cluster.close()

    async def test_the_next_index_stays_above_the_match_index(
        self, tmp_path: Path,
    ) -> None:
        """``Match < Next``, which etcd states where it advances them.

        The two are moved by different messages -- a rejection lowers
        ``next_index`` alone -- so a success can leave ``match_index`` above
        it. A leader in that state anchors its next append below what the peer
        acknowledged, and if that anchor is under the peer's commit index the
        peer answers without taking the entries. Neither side moves.

        Measured as exactly that: a leader stuck at ``next=1 match=2``
        re-sending ``prev=0`` until the term ended.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            node = leader.node
            peer = next(p for p in node._peers if p != node.index)

            for tag in range(5):
                await asyncio.wait_for(
                    node.propose(_register(_stale_id(tag), 0)), timeout=5.0,
                )
            await cluster.settle(20)

            state = node._peers[peer]
            state.match_index = 2
            state.next_index = 1

            node.on_append_entries_reply(peer, AppendEntriesReply(
                term=node.term, success=True, match_index=2,
                conflict_index=0, conflict_term=0,
                catching_up=False, request_id=state.reply_floor + 1,
            ))

            assert state.next_index > state.match_index, (
                f"next_index {state.next_index} is not above match_index "
                f"{state.match_index}, so the next append is anchored below "
                f"what this peer has already acknowledged"
            )
        finally:
            await cluster.close()

    async def test_a_peer_that_has_stopped_answering_does_not_hold_the_quorum(
        self, tmp_path: Path,
    ) -> None:
        """Check-quorum runs on replies, not on the socket.

        etcd sets ``RecentActive`` only when a peer answers
        (``raft.go:1388``, ``:1580``) and clears it every election interval.
        Counting connections instead lets a stopped or stalled peer -- whose
        socket the kernel holds open for minutes -- keep a leader believing it
        has a quorum while a majority is answering nothing. Writes accepted
        there can never commit.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            node = leader.node
            now = asyncio.get_running_loop().time()

            assert node._quorum_is_answering(now), (
                "a healthy leader does not believe it has a quorum"
            )
            assert node.has_quorum, "the links are not up, so this proves nothing"

            # Every peer still connected, none of them answering -- SIGSTOP, a
            # stalled event loop, a deadlocked process.
            for state in node._peers.values():
                state.last_heard_at = now - node._timing.election_max * 2

            assert node.has_quorum, (
                "the test no longer distinguishes the two: the links went "
                "down, which check-quorum already noticed"
            )
            assert not node._quorum_is_answering(now), (
                "every peer has been silent for two election windows and this "
                "leader still counts them, so it goes on accepting writes "
                "that can never commit"
            )
        finally:
            await cluster.close()


class TestRepliesFromAnIncarnationThatIsGone:
    """The safety bug the etcd comparison turned up on its way past.

    A reply carries no incarnation, and an append reply is not a correlated
    request whose future the transport fails when a link drops. So a reply the
    dying member had already put on the wire arrives looking exactly like a
    current one -- and is applied to the member that replaced it.
    """

    async def test_a_reply_from_the_previous_incarnation_is_not_believed(
        self, tmp_path: Path,
    ) -> None:
        """Measured before the fence, on three members:

        * the leader credited the restarted member with index 6 while it held
          nothing;
        * it took ``catching_up=False`` from the same reply, which put that
          member back into ``_advance_commit``'s tally.

        Leader plus phantom is a quorum of three, so the leader could commit an
        index only it held. That is Leader Completeness, from one stale
        message.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            node = leader.node
            for tag in range(5):
                await asyncio.wait_for(
                    node.propose(_register(_stale_id(tag), 0)), timeout=5.0,
                )
            await cluster.settle(20)

            peer = next(
                m.index for m in cluster.members if m.index != leader.index
            )
            state = node._peers[peer]
            held = state.match_index
            assert held > 0, "the peer acknowledged nothing, so this proves nothing"

            # What the dying incarnation had already put on the wire.
            in_flight = AppendEntriesReply(
                term=node.term, success=True, match_index=held,
                conflict_index=0, conflict_term=0,
                catching_up=False, request_id=state.reply_floor + 1,
            )

            replacement = await cluster.restart(peer)
            state = node._peers[peer]
            assert state.match_index == 0, (
                "the reconnect did not reset this leader's view, so the "
                "scenario below is not the one being tested"
            )

            node.on_append_entries_reply(peer, in_flight)

            really_holds = replacement.node.log.last_index
            assert state.match_index <= really_holds, (
                f"member {peer} is credited with index {state.match_index} "
                f"while holding through {really_holds}; counted toward the "
                f"commit quorum, that lets this leader commit an index no "
                f"majority has"
            )
        finally:
            await cluster.close()

    async def test_a_stale_rejection_does_not_move_a_peer_backwards(
        self, tmp_path: Path,
    ) -> None:
        """etcd's ``MaybeDecrTo``, which the fence above makes portable.

        Within one leadership a peer that acknowledged ``match_index`` holds
        every index up to it, identical to this leader's, so a genuine conflict
        must lie above it. Refusing one below is therefore sound -- but only
        once no phantom ``match_index`` can be left by a previous incarnation,
        which is what made the first attempt strand a rejoining member.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            node = leader.node
            for tag in range(5):
                await asyncio.wait_for(
                    node.propose(_register(_stale_id(tag), 0)), timeout=5.0,
                )
            await cluster.settle(20)

            peer = next(
                m.index for m in cluster.members if m.index != leader.index
            )
            state = node._peers[peer]
            state.match_index = 5
            state.next_index = 6

            node.on_append_entries_reply(peer, AppendEntriesReply(
                term=node.term, success=False, match_index=0,
                conflict_index=2, conflict_term=node.term,
                catching_up=False, request_id=state.reply_floor + 1,
            ))

            assert state.next_index > state.match_index, (
                f"a rejection pointing at index 2 moved this peer back to "
                f"{state.next_index}, below the {state.match_index} it has "
                f"already acknowledged"
            )
        finally:
            await cluster.close()


class TestARefusalThatMovesNothingWaitsForTheNextHeartbeat:
    """A rejection that leaves ``next_index`` where it was is not re-sent at once.

    etcd re-sends after a rejection only when the rejection lowers ``Next``
    (``MaybeDecrTo``, ``tracker/progress.go:226-254``: a stale one returns
    false and nothing is sent). This re-sent every rejection at once, whatever
    it did to ``next_index`` -- so a follower whose hint pointed where the
    leader already was drew the same append straight back, forever. Such a
    follower holds a committed snapshot the leader's log contradicts, which
    only lost committed data can produce (amnesia past the budget): the Rust
    soak's seed 111504 measured 124,991 appends inside one millisecond of
    cluster time.
    """

    async def test_a_refusal_pointing_where_the_leader_is_draws_no_resend(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            node = leader.node
            await node.propose(_register(NODE_ID, leader.index))
            peer = next(p for p in node._peers if p != node.index)      # noqa: SLF001
            state = node._peers[peer]      # noqa: SLF001
            probing = state.next_index

            sent: list[int] = []
            send = node._send_append      # noqa: SLF001

            def counted(target: int, tracked: _PeerState) -> None:
                sent.append(target)
                send(target, tracked)

            node._send_append = counted  # type: ignore[assignment]      # noqa: SLF001
            node.on_append_entries_reply(peer, AppendEntriesReply(
                term=node.term, success=False, match_index=0,
                conflict_index=probing, conflict_term=node.term,
                catching_up=False, request_id=state.reply_floor + 1,
            ))

            assert state.next_index == probing
            assert sent.count(peer) == 0, (
                f"a refusal that left next_index at {probing} was re-sent at "
                f"once, to draw the same refusal"
            )
        finally:
            await cluster.close()

    async def test_a_follower_that_can_never_accept_is_probed_once_a_heartbeat(
        self, tmp_path: Path,
    ) -> None:
        """The whole exchange, with a follower refusing as seed 111504's did.

        That follower's committed snapshot contradicted the leader's log at its
        boundary, so every append drew ``conflict=(snapshot + 1, t)`` -- the
        index the leader was already sending from.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            node = leader.node
            await node.propose(_register(NODE_ID, leader.index))
            refusing = next(m for m in cluster.members if m.index != leader.index)

            def refuse(peer: int, message: AppendEntries) -> AppendEntriesReply:
                return AppendEntriesReply(
                    term=message.term, success=False, match_index=0,
                    conflict_index=message.prev_log_index + 1, conflict_term=1,
                    catching_up=False, request_id=message.request_id,
                )

            refusing.node.on_append_entries = refuse  # type: ignore[method-assign]
            before = cluster.network.delivered
            heartbeats = 20
            await cluster.settle(heartbeats)
            delivered = cluster.network.delivered - before

            # The healthy follower's exchanges, a heartbeat each, plus at most
            # a couple of messages per heartbeat for the refusing one.
            assert delivered <= 8 * heartbeats, (
                f"{delivered} messages in {heartbeats} heartbeats: the leader "
                f"and a follower that can never accept are re-triggering each "
                f"other with no delay between steps"
            )
        finally:
            await cluster.close()


class TestWhatAFollowerWillAccept:
    async def test_a_snapshot_at_or_below_the_commit_index_is_refused(
        self, tmp_path: Path,
    ) -> None:
        """etcd: ``if s.Metadata.Index <= r.raftLog.committed { return false }``.

        Comparing against the compaction boundary instead let through every
        snapshot landing between it and the commit index -- and installing one
        replaces the state machine with older state while the commit index
        correctly stays put, leaving committed entries un-applied.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            follower = next(
                m for m in cluster.members if m.index != leader.index
            )
            for tag in range(5):
                await asyncio.wait_for(
                    leader.node.propose(_register(_stale_id(tag), 0)),
                    timeout=5.0,
                )
            await cluster.settle(20)

            node = follower.node
            committed = node.commit_index
            assert committed > node.log.snapshot_index, (
                "nothing is committed above the compaction boundary, so the "
                "window this guards does not exist here"
            )

            last_index, last_term = node.log.last_index, node.log.last_term
            reply = node.on_install_snapshot(leader.index, InstallSnapshot(
                term=node.term, leader=leader.index, last_index=committed,
                last_term=node.log.term_at(committed), offset=0,
                data=b"a snapshot this member already holds", done=True,
                ownership=b"", request_id=3,
            ))

            assert (node.log.last_index, node.log.last_term) == (
                last_index, last_term,
            ), (
                f"a snapshot ending at index {committed} was installed, but "
                f"this member has committed through {committed} -- installing "
                f"it would un-apply committed state"
            )
            assert reply.bytes_received == 0 and not reply.done
            assert reply.commit_index == committed, (
                "refused without saying how far this member has committed, "
                "so the leader can only send it again"
            )
        finally:
            await cluster.close()

    async def test_an_append_below_the_commit_index_is_answered_with_it(
        self, tmp_path: Path,
    ) -> None:
        """etcd returns early here, replying with its own commit index.

        A delayed or duplicated append anchored below what this member has
        committed would otherwise be answered with ``prev_log_index +
        len(entries)`` -- a match below our commit index, which walks the
        leader's view of us backwards and makes it re-send what we hold.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            follower = next(
                m for m in cluster.members if m.index != leader.index
            )
            for tag in range(5):
                await asyncio.wait_for(
                    leader.node.propose(_register(_stale_id(tag), 0)),
                    timeout=5.0,
                )
            await cluster.settle(20)

            node = follower.node
            committed = node.commit_index
            assert committed > 1, "nothing committed, so this proves nothing"

            reply = node.on_append_entries(leader.index, AppendEntries(
                term=node.term, leader=leader.index,
                prev_log_index=1, prev_log_term=node.log.term_at(1),
                leader_commit=committed, request_id=9, entries=(),
            ))

            assert reply.success, "a stale append was rejected outright"
            assert reply.match_index == committed, (
                f"answered with {reply.match_index} for a message anchored at "
                f"index 1, though this member has committed through "
                f"{committed} -- the leader stores that verbatim"
            )
        finally:
            await cluster.close()


def _stale_id(index: int) -> str:
    return f"{index:08x}-dead-4000-8000-00000000000a"


class TestWhatEtcdGatesThatThisDidNot:
    """Two things `go.etcd.io/raft` does that this had to be told to do.

    Both found by walking etcd's state transitions rather than its mechanisms,
    after the first comparison pass missed one by comparing only the mechanisms
    it thought to compare. The first of them -- not campaigning while a snapshot
    is pending -- turned out to be a mis-mapping: etcd's pending snapshot is a
    complete one, and gating on a *partial* buffer stranded clusters without a
    leader. Its test now asserts the corrected rule.
    """

    async def test_an_abandoned_partial_snapshot_does_not_stop_a_campaign(
        self, tmp_path: Path,
    ) -> None:
        """A partial snapshot is not etcd's pending snapshot, and gates nothing.

        This test once asserted the opposite -- that a member holding a partly
        received snapshot does not campaign -- modelled on etcd's
        ``promotable()`` and its ``hasNextOrInProgressSnapshot()``. That
        condition is a *complete* snapshot awaiting application (``log.go:287-291``,
        set only by ``restore``); etcd has no partial transfers at all, and this
        member installs a completed one synchronously, so the state etcd gates
        on never exists here. Every chunk and every keepalive from a live leader
        resets the election timer, so by the time the timer has run out the
        transfer has stopped, and the gate could only block the campaign the
        silence called for. The Rust chaos soak measured the cost: all 27
        liveness failures of one 533-run soak were clusters with no leader whose
        one viable candidate held an abandoned buffer and never campaigned.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            follower = cluster.members[1]
            node = follower.node
            node._role = Role.FOLLOWER

            # A transfer that stopped: one partly-assembled snapshot buffer.
            node._installing[0] = _Assembly(
                (node.term, 0, 5, node.term), bytearray(b"half a snapshot"),
            )
            # And an election timer that has run out, because the leader went
            # silent -- a live one resets it with every chunk and keepalive.
            node._deadline = asyncio.get_running_loop().time() - 1.0

            node._tick()

            assert node.role is not Role.FOLLOWER, (
                "a member whose leader had gone silent did not campaign, because "
                "it held a partial snapshot that could no longer finish"
            )
        finally:
            await cluster.close()

    async def test_a_partial_snapshot_is_dropped_when_it_can_no_longer_complete(
        self, tmp_path: Path,
    ) -> None:
        """A partial buffer lives only as long as its transfer can.

        Nothing removed one but a chunk at offset 0, a completion or a refusal
        -- never its sender's link going, or a term passing -- so an abandoned
        transfer held its memory for the life of the member (and, while the
        campaign gate stood, its ability to campaign too).
        """
        cluster = await _cluster(3, tmp_path)
        try:
            follower = cluster.members[1]
            node = follower.node

            node._installing[0] = _Assembly(
                (node.term, 0, 5, node.term), bytearray(b"half a snapshot"),
            )
            node.on_peer_state(0, up=False, incarnation=0)
            assert 0 not in node._installing, (
                "the buffer outlived its sender's link"
            )

            node._installing[0] = _Assembly(
                (node.term, 0, 5, node.term), bytearray(b"half a snapshot"),
            )
            node._step_down(node.term + 1)
            assert not node._installing, (
                "the buffer outlived the term it was sent in"
            )
        finally:
            await cluster.close()

    async def test_a_peer_that_can_be_sent_nothing_still_hears_from_us(
        self, tmp_path: Path,
    ) -> None:
        """A heartbeat here *is* an ``AppendEntries``, so silence is total.

        ``go.etcd.io/raft`` carries heartbeats as their own message type and
        ``bcastHeartbeat`` reaches every peer whatever its replication state,
        so a follower waiting for a snapshot still hears from its leader.

        Here the same message carries both, so a peer below the compaction
        boundary goes through ``_send_snapshot`` -- and when there is no chunk
        to send, returning silently sent that peer **nothing at all**: no
        entries and no liveness. Its election timer expires and it campaigns,
        for as long as the condition lasts.
        """
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect(timeout=5.0)
            node = leader.node
            peer = next(p for p in node._peers if p != node.index)
            state = node._peers[peer]

            # The state the silence came from: this peer needs a snapshot and
            # there is none to give it.
            assert node._snapshot_meta is None, (
                "a snapshot exists, so this is not the path being tested"
            )
            sent: list[Any] = []
            node._transport.send = (  # type: ignore[method-assign]
                lambda p, m, **kw: sent.append((p, m))
            )

            node._send_snapshot(peer, state)

            assert sent, (
                f"member {peer} needs a snapshot this leader does not have, "
                f"and was sent nothing at all -- not even a heartbeat, because "
                f"here they are the same message. It will time out and "
                f"campaign for as long as that lasts."
            )
            _target, message = sent[-1]
            assert message.prev_log_index == 0, (
                "a keepalive must be anchored where every log matches, or the "
                "peer rejects it and the leader learns nothing"
            )
            assert not message.entries, "a keepalive carries no entries"
            assert message.leader_commit == 0, (
                "a keepalive that carried a commit index would vouch for "
                "entries it did not send"
            )
        finally:
            await cluster.close()


class TestABrokenInvariantStopsTheMember:
    """A member that finds its own state impossible stops, as it is documented to.

    ``RaftInvariantViolated`` is "not recovered from anywhere": the member stops,
    and a restart brings it back with nothing, to be caught up as a non-voting
    learner. It used to end the task that found it and nothing else. Nobody
    awaited that task before ``close``, so the member went on leading, voting
    and replicating while its store never moved again -- and nothing was logged.
    """

    @staticmethod
    def _break(member: Any) -> None:
        # Applied beyond committed: the first thing
        # ``_check_applied_within_committed`` refuses, found on the next apply.
        member.node._machine._last_applied = member.node.commit_index + 100
        member.node._schedule_apply()

    @staticmethod
    async def _until(condition: Any, *, seconds: float) -> bool:
        deadline = asyncio.get_running_loop().time() + seconds
        while not condition():
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.01)
        return True

    async def test_a_leader_that_breaks_stops_leading_and_is_replaced(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            broken = await cluster.elect()
            self._break(broken)

            replaced = await self._until(
                lambda: [m.index for m in cluster.leaders if m is not broken]
                != [],
                seconds=20 * cluster.timing.election_max,
            )
            assert replaced and broken.node.role is not Role.LEADER, (
                "the leader whose invariant broke went on leading: its "
                "heartbeats kept the others from electing anyone, from a member "
                "that had stopped applying"
            )
            failure = getattr(broken.node, "failure", None)
            assert isinstance(failure, RaftInvariantViolated), failure
            assert await asyncio.wait_for(
                broken.node.wait_for_failure(), 1.0,
            ) is failure
        finally:
            await cluster.close()

    async def test_a_caller_waiting_on_it_is_answered_at_once(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            broken = await cluster.elect()
            # Cut off, so the proposal cannot commit and its caller waits.
            cluster.network.isolate(broken.index)
            waiting = broken.node.propose(_register(NODE_ID, broken.index))
            await asyncio.sleep(cluster.timing.heartbeat)
            assert not waiting.done()

            self._break(broken)
            try:
                await asyncio.wait_for(waiting, 1.0)
            except RaftUnavailable:
                pass
            except asyncio.TimeoutError:
                pytest.fail(
                    "a caller waiting on a member that stopped applying was "
                    "left waiting for an entry that member will never apply",
                )
            else:
                pytest.fail("a proposal to a stopped member was answered as done")
        finally:
            await cluster.close()

    async def test_an_append_that_would_discard_committed_state_stops_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # No message reaches this (the handler answers ``prev_log_index <
        # commit_index`` first), so the log is made to find it -- the wiring is
        # what is under test: the violation used to fail the link the append
        # came by, and the next append repeated it.
        from nmos.raft.log import RaftLog

        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect()
            broken = next(m for m in cluster.members if m is not leader)
            original = RaftLog.append_replicated

            def refuse(log: Any, entries: Any, *, committed: int) -> None:
                if log is not broken.node._log:
                    original(log, entries, committed=committed)
                    return
                raise RaftInvariantViolated(
                    "planted: an entry conflicting at or below the commit index",
                )

            # On the class: ``RaftLog`` has slots, so an instance cannot be
            # given a method of its own. Only ``broken``'s log refuses.
            monkeypatch.setattr(RaftLog, "append_replicated", refuse)
            await asyncio.wait_for(
                leader.node.propose(_register(NODE_ID, leader.index)), 5.0,
            )

            stopped = await self._until(
                lambda: getattr(broken.node, "failure", None) is not None,
                seconds=2.0,
            )
            assert stopped, (
                "a member whose log refused to discard committed state went on "
                "as if a link had failed"
            )
            gone = await self._until(
                lambda: broken.index not in leader.node._transport.live,
                seconds=2.0,
            )
            assert gone, "the stopped member's peers still see it"
        finally:
            await cluster.close()


class TestALeaderContradictingACommittedEntryStopsTheFollower:
    """A follower holding a committed entry its leader contradicts stops (L2).

    Raft makes it impossible, so it proves committed data lost from the
    cluster; such a follower accepted nothing its leader sent and served its
    stale store until the leader's snapshot passed its commit index. The chaos
    soak's seed 111504 was one: a snapshot through (4, t10) committed, and a
    term-13 leader whose log never had it. Driven by hand on a member that
    hears nothing else (``_cluster_with_a_quiet_follower``).
    """

    @staticmethod
    def _committed_snapshot(node: Any, index: int, term: int) -> None:
        # What installing a snapshot through (index, term) leaves behind.
        node._log.reset_to_snapshot(index, term)
        node._commit_index = index
        node._machine._last_applied = index

    @staticmethod
    async def _committed_log(cluster: Cluster, node: Any, term: int) -> None:
        from nmos.raft.tests.test_apply_bounds import _entry, _settled

        reply = node.on_append_entries(0, AppendEntries(
            term=term, leader=0, prev_log_index=0, prev_log_term=0,
            entries=tuple(_entry(term, index, index) for index in (1, 2, 3)),
            leader_commit=3, request_id=1,
        ))
        assert reply.success and node.commit_index == 3
        await _settled(cluster)

    async def test_an_append_anchored_on_a_committed_entry_it_contradicts(
        self, tmp_path: Path,
    ) -> None:
        from nmos.raft.tests.test_apply_bounds import (
            _cluster_with_a_quiet_follower,
        )

        cluster = await _cluster_with_a_quiet_follower(tmp_path)
        try:
            node = cluster.members[1].node
            self._committed_snapshot(node, 4, 10)
            reply = node.on_append_entries(0, AppendEntries(
                term=13, leader=0, prev_log_index=4, prev_log_term=13,
                entries=(), leader_commit=4, request_id=5,
            ))
            assert node.failure is not None, (
                f"a follower that committed (4, t10) was told (4, t13) and went "
                f"on as if it could catch up: answered {reply}"
            )
            assert "index 4" in str(node.failure)
            assert not reply.success and reply.request_id == 0
        finally:
            await cluster.close()

    async def test_an_append_carrying_an_entry_it_contradicts(
        self, tmp_path: Path,
    ) -> None:
        # Anchored below the commit point, where it matches: the answer to such
        # an append credits ``match = commit`` without looking at what it
        # carries.
        from nmos.raft.tests.test_apply_bounds import (
            _cluster_with_a_quiet_follower,
            _entry,
        )

        cluster = await _cluster_with_a_quiet_follower(tmp_path)
        try:
            node = cluster.members[1].node
            term = node.term + 1
            await self._committed_log(cluster, node, term)
            reply = node.on_append_entries(0, AppendEntries(
                term=term + 1, leader=0, prev_log_index=1, prev_log_term=term,
                entries=(_entry(term + 1, 2, 20),), leader_commit=3,
                request_id=2,
            ))
            assert node.failure is not None, (
                f"a follower was sent a different entry at committed index 2 "
                f"and credited the leader with its commit index: {reply}"
            )
            assert "index 2" in str(node.failure)
        finally:
            await cluster.close()

    async def test_an_entry_at_the_snapshot_boundary_it_contradicts(
        self, tmp_path: Path,
    ) -> None:
        # Seed 111504's shape: the anchor lies below the boundary, where
        # nothing is left to compare, and the boundary's own term is kept.
        from nmos.raft.tests.test_apply_bounds import (
            _cluster_with_a_quiet_follower,
            _entry,
        )

        cluster = await _cluster_with_a_quiet_follower(tmp_path)
        try:
            node = cluster.members[1].node
            self._committed_snapshot(node, 4, 10)
            reply = node.on_append_entries(0, AppendEntries(
                term=13, leader=0, prev_log_index=3, prev_log_term=13,
                entries=(_entry(13, 4, 4),), leader_commit=4, request_id=5,
            ))
            assert node.failure is not None, (
                f"a follower that committed (4, t10) was sent (4, t13) and "
                f"credited the leader with its commit index: {reply}"
            )
        finally:
            await cluster.close()

    async def test_a_snapshot_ending_on_a_committed_entry_it_contradicts(
        self, tmp_path: Path,
    ) -> None:
        from nmos.raft.tests.test_apply_bounds import (
            _cluster_with_a_quiet_follower,
        )

        cluster = await _cluster_with_a_quiet_follower(tmp_path)
        try:
            node = cluster.members[1].node
            self._committed_snapshot(node, 4, 10)
            reply = node.on_install_snapshot(0, InstallSnapshot(
                term=13, leader=0, last_index=4, last_term=13, offset=0,
                data=b"the first chunk", done=False, request_id=5,
            ))
            assert node.failure is not None, (
                f"a follower that committed (4, t10) was sent a snapshot "
                f"through (4, t13) and took it as one it already held: {reply}"
            )
            assert reply.commit_index == 0 and reply.request_id == 0
        finally:
            await cluster.close()

    async def test_what_agrees_or_cannot_be_checked_is_answered_as_before(
        self, tmp_path: Path,
    ) -> None:
        from nmos.raft.tests.test_apply_bounds import (
            _cluster_with_a_quiet_follower,
            _entry,
        )

        cluster = await _cluster_with_a_quiet_follower(tmp_path)
        try:
            node = cluster.members[1].node
            term = node.term + 1
            await self._committed_log(cluster, node, term)

            # A delayed append that agrees: credited with the commit index.
            reply = node.on_append_entries(0, AppendEntries(
                term=term, leader=0, prev_log_index=1, prev_log_term=term,
                entries=(_entry(term, 2, 2),), leader_commit=3, request_id=2,
            ))
            assert reply.success and reply.match_index == 3
            # An ordinary conflict beyond the commit index: a refusal with a
            # hint, the path that resolves it.
            reply = node.on_append_entries(0, AppendEntries(
                term=term, leader=0, prev_log_index=5, prev_log_term=term,
                entries=(), leader_commit=3, request_id=3,
            ))
            assert not reply.success and reply.conflict_index == 4
            assert node.failure is None

            # Anchored below a snapshot boundary: nothing left to compare.
            self._committed_snapshot(node, 4, 10)
            reply = node.on_append_entries(0, AppendEntries(
                term=13, leader=0, prev_log_index=2, prev_log_term=13,
                entries=(), leader_commit=4, request_id=4,
            ))
            assert reply.success and reply.match_index == 4
            assert node.failure is None
        finally:
            await cluster.close()


class TestALeaderCreditsNoMoreThanItsOwnLog:
    """A reply cannot put a follower beyond the leader's own log.

    In a correct run no reply does -- a follower vouches for a window the
    leader sent, or for its commit index, which Leader Completeness puts inside
    the leader's log. Only after committed data has been lost can a follower's
    commit index lie beyond it, and the unbounded credit then put
    ``next_index`` past the end of the log: ``_send_append`` raised out of every
    tick, and the follower was never sent an anchor it could check.
    """

    async def test_a_reply_vouching_past_the_leaders_log_is_bounded(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _cluster(3, tmp_path)
        try:
            leader = await cluster.elect()
            peer = next(m.index for m in cluster.members if m is not leader)
            state = leader.node._peers[peer]
            last = leader.node._log.last_index

            leader.node.on_append_entries_reply(peer, AppendEntriesReply(
                term=leader.node.term, success=True, match_index=last + 5,
                conflict_index=0, conflict_term=0, catching_up=False,
                request_id=state.reply_floor + 1,
            ))
            assert state.match_index <= last and state.next_index <= last + 1, (
                f"a reply vouching for {last + 5} left match={state.match_index} "
                f"next={state.next_index} against a log ending at {last}"
            )
            leader.node._send_append(peer, state)
        finally:
            await cluster.close()


class TestAnAppendThatCannotBeTakenIsRefusedInTheReply:
    """An append a follower cannot take is refused in its reply, not by the link.

    An undecodable entry, or entries that do not follow what the log holds:
    each escaped ``on_append_entries``, and the transport dropped the whole
    connection -- logging the second as a failed connection -- so the leader
    reconnected, reset the follower's progress and sent it again. The Rust
    member refuses both in its reply with a warning, keeping the link and the
    heartbeats, votes and reads it carries; the leader then probes as for any
    refusal. No correct leader sends either.
    """

    async def test_an_undecodable_entry(self, tmp_path: Path) -> None:
        from nmos.raft.tests.test_apply_bounds import (
            _cluster_with_a_quiet_follower,
        )

        cluster = await _cluster_with_a_quiet_follower(tmp_path)
        try:
            node = cluster.members[1].node
            term = node.term + 1
            reply = node.on_append_entries(0, AppendEntries(
                term=term, leader=0, prev_log_index=0, prev_log_term=0,
                entries=(WireEntry(term=term, index=1, payload=b""),),
                leader_commit=0, request_id=9,
            ))
            assert not reply.success and reply.request_id == 9, reply
            assert node._log.last_index == 0 and node.failure is None
        finally:
            await cluster.close()

    async def test_entries_that_do_not_follow_the_log(
        self, tmp_path: Path,
    ) -> None:
        from nmos.raft.tests.test_apply_bounds import (
            _cluster_with_a_quiet_follower,
            _entry,
        )

        cluster = await _cluster_with_a_quiet_follower(tmp_path)
        try:
            node = cluster.members[1].node
            term = node.term + 1
            reply = node.on_append_entries(0, AppendEntries(
                term=term, leader=0, prev_log_index=0, prev_log_term=0,
                entries=(_entry(term, 2, 2),), leader_commit=0, request_id=9,
            ))
            assert not reply.success and reply.request_id == 9, reply
            assert node._log.last_index == 0 and node.failure is None
        finally:
            await cluster.close()
