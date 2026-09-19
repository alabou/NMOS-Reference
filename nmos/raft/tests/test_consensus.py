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
from nmos.raft.messages import AppendEntries, AppendEntriesReply, WireEntry
from nmos.raft.node import Role
from nmos.raft.snapshot import SnapshotMeta
from nmos.raft.operations import (
    ProposalId,
    RegisterOp,
    UnregisterOp,
    encode_operation,
)
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
            node.on_append_entries_reply(peer, AppendEntriesReply(
                term=node.term, success=True, match_index=2,
                conflict_index=0, conflict_term=0,
                catching_up=True, request_id=state.reply_floor + 1,
            ))

            assert state.promote_through == committed, (
                f"this peer will be promoted back to voting at index "
                f"{state.promote_through}, but this leader has committed "
                f"through {committed} -- it would rejoin the electorate "
                f"missing committed entries"
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
            state.snapshot_in_flight = True

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
            assert state.snapshot_in_flight is False
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

            meta = SnapshotMeta(
                last_index=committed, last_term=node.term, resources=0,
            )
            assert node._why_the_snapshot_cannot_be_real(meta, node.term), (
                f"a snapshot ending at index {committed} was accepted, but "
                f"this member has committed through {committed} -- installing "
                f"it would un-apply committed state"
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
