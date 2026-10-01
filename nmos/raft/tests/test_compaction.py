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
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from nmos.raft import snapshot as snapshot_module
from nmos.raft.messages import AppendEntries, InstallSnapshot, InstallSnapshotReply
from nmos.raft.node import RaftTiming, Role
from nmos.raft.snapshot import SnapshotMeta, decode_snapshot
from nmos.raft.operations import ProposalId, RegisterOp
from nmos.raft.tests._harness import FAST, Cluster
from nmos.raft.wire import Stream
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

    async def test_a_member_caught_up_by_snapshot_holds_one_it_can_serve(
        self, tmp_path: Path,
    ) -> None:
        """The compacted prefix of a member's log is recoverable from its own snapshot.

        A member that caught up by *installing* a snapshot starts its log at the
        snapshot's boundary, so the entries below it exist on that member only
        as the snapshot -- and should it ever lead, they are exactly what a
        stranded follower needs. It used to keep none: only compaction set a
        member's snapshot, so such a leader could send its stranded followers
        nothing but keepalives for as long as it led -- the Rust chaos soak's
        commonest liveness failure, measured in Python too
        (``has_snapshot_payload=False`` after installing through 12).

        Nothing is proposed after the install, so the member cannot come by a
        snapshot through its own compaction and pass this vacuously.
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
            assert leader.node.log.snapshot_index > 0

            cluster.network.resume(outcast.index)
            await cluster.settle(60)

            node = outcast.node
            assert node.log.snapshot_index > 0, (
                "the returning member never installed a snapshot, so this "
                "proves nothing"
            )
            meta = node._snapshot_meta
            assert node._snapshot and meta is not None, (
                f"member {outcast.index} installed a snapshot through "
                f"{node.log.snapshot_index} and kept none: as leader it could "
                f"send a follower below that index nothing but keepalives"
            )
            assert meta.last_index >= node.log.snapshot_index
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


class TestAMemberSnapshotsTheStoreItServes:
    async def test_after_an_install_a_member_snapshots_its_live_store(
        self, tmp_path: Path,
    ) -> None:
        """A member caught up by snapshot later snapshots what it serves.

        An install replaces the registry's store (``Registry.swap_store``), and
        the snapshot store kept the one it was built with -- so every snapshot
        such a member took afterwards described its registry as it stood before
        the install. Measured: a member caught up through 30 Nodes that then
        applied 30 more took a snapshot of its 60 live Nodes holding none of
        them, and leading, would have handed it to any follower it had to catch
        up -- which would then serve an empty registry, silently.
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
            cluster.network.resume(outcast.index)
            await cluster.settle(60)
            node = outcast.node
            installed_through = node.log.snapshot_index
            assert installed_through > 0, (
                "the returning member never installed a snapshot, so this "
                "proves nothing"
            )

            # Applied into the store the install swapped in, and enough of
            # them that the member compacts on its own.
            for index in range(30, 60):
                await asyncio.wait_for(
                    leader.node.propose(_register(index, leader.index)), 5.0,
                )
            await cluster.settle(40)
            meta = node._snapshot_meta
            assert meta is not None and meta.last_index > installed_through, (
                "the member took no snapshot of its own after the install, so "
                "this proves nothing"
            )

            _, _, records = decode_snapshot(node._snapshot)
            held = [r.id for r in records if r.resource_type is ResourceType.NODE]
            live = {
                r.id for r in outcast.registry.store.iter_extant(ResourceType.NODE)
            }
            # Registered one entry each, in order, so a snapshot through any
            # index holds exactly a prefix of them -- at least the 30 the
            # install brought, since it is through a later index.
            assert len(held) >= 30 and sorted(held) == sorted(
                _node_id(index) for index in range(len(held))
            ), (
                f"member {outcast.index} snapshotted through {meta.last_index} "
                f"and its snapshot holds {len(held)} Nodes of the {len(live)} "
                f"it serves; one taken after installing through "
                f"{installed_through} must hold at least the 30 installed"
            )
            assert set(held) <= live
        finally:
            await cluster.close()


class TestAnInstallSupersedesACompactionInFlight:
    async def test_a_compaction_begun_before_an_install_does_not_replace_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The installed snapshot is newer; the compaction that was mid-walk stops.

        A compaction pins its image at ``last_applied`` and serialises it across
        yields. An install landing in one of those yields replaces the store and
        moves the log's boundary past the pinned index -- and the compaction,
        resuming, used to finish anyway and overwrite the installed snapshot
        with its own older one: a member holding a snapshot through the pinned
        index below a log discarded through the installed one, with no way to
        serve the entries between. The Rust chaos soak measured exactly that
        (seed 100423: a snapshot through 1046 below a log discarded through
        1120).

        Deterministic rather than raced: the follower's threshold is lowered for
        the one compaction this test starts, and ``CHUNK_RESOURCES`` is 1 so
        that compaction yields after every resource.
        """
        # No member compacts on its own; the one compaction here is the test's.
        timing = replace(EAGER, compaction_threshold=10_000, max_log_entries=20_000)
        cluster = Cluster(3, tmp_path, timing=timing)
        await cluster.start()
        try:
            leader = await cluster.elect()
            follower = next(m for m in cluster.members if m is not leader)
            await _fill(cluster, leader, 6)
            await cluster.settle(20)

            # From here the follower hears only what this test hands it, and
            # falls behind the leader by six registrations.
            cluster.network.stop(follower.index)
            await cluster.settle(5)
            for index in range(6, 12):
                await asyncio.wait_for(
                    leader.node.propose(_register(index, leader.index)), 5.0,
                )
            through = leader.node.last_applied
            term = leader.node.log.term_at(through)
            capture = leader.snapshots.begin(
                index=through, term=term, ownership=leader.machine.ownership,
            )
            payload = await leader.snapshots.finish(capture)

            node = follower.node
            monkeypatch.setattr(snapshot_module, "CHUNK_RESOURCES", 1)
            monkeypatch.setattr(node, "_timing", replace(timing, compaction_threshold=1))
            compaction = asyncio.create_task(node._maybe_compact())
            await asyncio.sleep(0)
            open_capture = follower.snapshots.capture
            assert open_capture is not None, (
                "the compaction is not mid-walk, so this proves nothing"
            )
            pinned = open_capture.index
            assert pinned < through, (pinned, through)

            reply = node.on_install_snapshot(leader.index, InstallSnapshot(
                term=node.term, leader=leader.index, last_index=through,
                last_term=term, offset=0, data=payload, done=True,
            ))
            assert reply.done, "the install was refused, so this proves nothing"
            await compaction

            meta = node._snapshot_meta
            assert meta is not None and meta.last_index == through, (
                f"a compaction pinned at {pinned}, begun before the install "
                f"through {through} and finished after it, replaced the "
                f"installed snapshot: the member holds one through "
                f"{meta.last_index if meta else None} below a log discarded "
                f"through {node.log.snapshot_index}"
            )
            assert node._snapshot == payload
            assert node.log.snapshot_index == through
            assert follower.snapshots.capture is None
        finally:
            await cluster.close()


class TestWhatASnapshotReplyIsEvidenceOf:
    """A snapshot acknowledgement is evidence only about the exchange it answers."""

    async def test_a_reply_from_an_earlier_term_is_not_credited(
        self, tmp_path: Path,
    ) -> None:
        """A completion acknowledged in an earlier term, arriving now.

        Fenced twice, the term first. The reply carries the id of the chunk it
        answers (S9), and one from an earlier term is at or below the
        ``reply_floor`` this leadership raised on beginning; but the term is
        what ``on_install_snapshot_reply`` checks first, as
        ``on_append_entries_reply`` does. When this was written the reply
        carried no id, and the term was its only fence. This reply's id is past
        any floor and its commit index credits something, so the term alone
        stops it here: the guard this test is about. With an id under the
        floor, or a commit index of 0, it passed with the term check removed.
        Believing a stale ``done=True`` credits the peer with *this* leader's
        current snapshot: measured in the Rust chaos soak as a member credited
        with index 504 from a reply sent in term 78 about a snapshot through
        500, whose every genuine rejection afterwards was then discarded as
        stale, so it never caught up. etcd drops every lower-term message before
        it reaches the progress tracker (``raft.go:1133-1186``).
        """
        cluster = Cluster(3, tmp_path, timing=EAGER)
        await cluster.start()
        try:
            leader = await cluster.elect()
            await _fill(cluster, leader, 8)
            await cluster.settle(10)
            node = leader.node
            assert node._snapshot_meta is not None, (
                "the leader never compacted, so this proves nothing"
            )
            peer = min(node._peers)

            # Nothing real reaches the leader from here on, and its view of the
            # peer starts from nothing, as after a reconnect.
            cluster.network.isolate(leader.index)
            node.on_peer_state(peer, up=True, incarnation=2**62)
            state = node._peers[peer]
            assert node.role is Role.LEADER, "stepped down before the reply"
            assert state.match_index == 0

            node.on_install_snapshot_reply(peer, InstallSnapshotReply(
                term=node.term - 1, bytes_received=0, done=True,
                # Past any floor -- the largest id the wire carries -- and
                # crediting something: were the term not checked, nothing else
                # would stop this reply.
                commit_index=node._log.last_index, request_id=2**64 - 1,
            ))

            assert state.match_index == 0, (
                f"a snapshot acknowledgement from term {node.term - 1} credited "
                f"member {peer} with index {state.match_index} in term "
                f"{node.term}: the snapshot it acknowledged is not the one this "
                f"leader holds, and every genuine rejection below that index "
                f"will now be discarded as stale"
            )
        finally:
            await cluster.close()


async def _mid_transfer(
    cluster: Cluster, *, keep_quorum: bool = False,
) -> tuple[Any, int, Any, list[Any]]:
    """A leader holding a snapshot of several chunks, about to transfer it.

    Isolated, so nothing it sends arrives and every reply is the test's; the
    peer's view reset as a reconnect resets it; every ``InstallSnapshot`` and
    ``AppendEntries`` to that peer recorded, in order.

    With ``keep_quorum`` only the peer's links are lost -- silently, and both
    ways -- so the leader keeps its quorum, and a test can let it tick while a
    chunk is in flight without it standing down.
    """
    leader = await cluster.elect()
    await _fill(cluster, leader, 8)
    await cluster.settle(10)
    node = leader.node
    assert node._snapshot_meta is not None, "the leader never compacted"
    assert len(node._snapshot) > 3 * EAGER.snapshot_chunk, (
        "a snapshot of so few chunks proves nothing about a transfer"
    )
    peer = min(node._peers)
    sent: list[Any] = []
    real_send = node.transport.send

    def recording(target: int, message: Any, **kwargs: Any) -> None:
        if target == peer and isinstance(message, (InstallSnapshot, AppendEntries)):
            sent.append(message)
        real_send(target, message, **kwargs)

    node.transport.send = recording  # type: ignore[method-assign, assignment]
    if keep_quorum:
        cluster.network.lose(leader.index, peer)
        cluster.network.lose(peer, leader.index)
    else:
        cluster.network.isolate(leader.index)
    node.on_peer_state(peer, up=True, incarnation=2**62)
    return node, peer, node._peers[peer], sent


def _chunks(sent: list[Any]) -> list[InstallSnapshot]:
    return [m for m in sent if isinstance(m, InstallSnapshot)]


class TestASnapshotAlreadyHeldEndsTheTransfer:
    """A follower that already holds a snapshot says so, and the leader stops.

    It refused such a snapshot -- one at or below its commit index -- with the
    same answer it gives when it has thrown a transfer away, so the leader
    started again from zero, and was refused again: a ping-pong at network
    speed, logged at ERROR on every round (42 runs of one soak). etcd answers an
    ignored snapshot with an ordinary append response carrying its commit index
    (``raft.go:1840-1854``), and so does this now.
    """

    async def test_a_follower_answers_a_snapshot_it_already_holds_with_its_commit_index(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        cluster = Cluster(3, tmp_path, timing=EAGER)
        await cluster.start()
        try:
            leader = await cluster.elect()
            await _fill(cluster, leader, 3)
            await cluster.settle(10)
            follower = next(m for m in cluster.members if m is not leader)
            node = follower.node
            commit = node.commit_index
            assert commit > 0

            with caplog.at_level("INFO", logger="nmos.raft.node"):
                reply = node.on_install_snapshot(leader.index, InstallSnapshot(
                    term=node.term, leader=leader.index, last_index=commit,
                    last_term=node.log.term_at(commit), offset=0,
                    data=b"the first chunk of a snapshot this member holds",
                    done=False, ownership=b"", request_id=7,
                ))

            assert reply.commit_index == commit, (
                f"a follower committed through {commit} did not say so; the "
                f"leader can only start the transfer again"
            )
            assert reply.request_id == 7
            assert reply.bytes_received == 0 and not reply.done
            assert not node._installing, "it began assembling a snapshot it holds"
            assert not [r for r in caplog.records if r.levelname == "ERROR"], (
                "a snapshot the member already holds is not an error"
            )
        finally:
            await cluster.close()

    async def test_a_snapshot_the_follower_already_holds_ends_the_transfer(
        self, tmp_path: Path,
    ) -> None:
        cluster = Cluster(3, tmp_path, timing=EAGER)
        await cluster.start()
        try:
            node, peer, state, sent = await _mid_transfer(cluster)
            meta = node._snapshot_meta
            # Past the snapshot and within this leader's log: crediting the
            # follower's statement is then distinguishable from crediting the
            # pin, and is a statement a complete leader can receive.
            held = node.log.last_index
            assert held > meta.last_index, (
                "the leader holds nothing past its snapshot, so this proves "
                "nothing"
            )
            node._send_snapshot(peer, state)
            first = _chunks(sent)[0]

            node.on_install_snapshot_reply(peer, InstallSnapshotReply(
                term=node.term, bytes_received=0, done=False,
                commit_index=held, request_id=first.request_id,
            ))

            assert len(_chunks(sent)) == 1, (
                "the leader started the transfer again for a follower that "
                "already holds everything it covers"
            )
            assert state.sending is None
            assert state.match_index == held, (
                "the follower's own statement of what it holds was not credited"
            )
            assert state.next_index == held + 1
            assert isinstance(sent[-1], AppendEntries), (
                "the leader did not return to replication"
            )
        finally:
            await cluster.close()


class TestATransferIsCorrelated:
    """Only the reply to the chunk in flight drives a transfer.

    Chunks travel on the BULK connection and a reconnect is reported for the
    CONTROL one, so a reconnect reset the transfer while its last chunk was
    still in flight -- and answered. The leader started again from zero, the
    old chunk's reply sent another chunk too, and every reply thereafter sent
    one more: two streams, for ever, the follower discarding its buffer at
    every out-of-order chunk (seed 60195: a member stuck 20 entries behind for
    400 x 10 heartbeats). Chunks now carry a correlation id, fenced by the same
    ``reply_floor`` as appends.

    A chunk lost with its BULK connection draws no answer, and nothing else ever
    cleared it while CONTROL stayed up (S6). It is sent again once that
    connection has ended -- and only then: sent again because its answer was
    late, it stalled every transfer over a link whose round trip outlasts
    ``election_min``, each answer arriving already superseded by the next copy
    (part 17 of the fix record).
    """

    async def test_a_reconnect_does_not_fork_the_transfer(
        self, tmp_path: Path,
    ) -> None:
        cluster = Cluster(3, tmp_path, timing=EAGER)
        await cluster.start()
        try:
            node, peer, state, sent = await _mid_transfer(cluster)
            node._send_snapshot(peer, state)
            zero = _chunks(sent)[0]
            node.on_install_snapshot_reply(peer, InstallSnapshotReply(
                term=node.term, bytes_received=len(zero.data), done=False,
                commit_index=0, request_id=zero.request_id,
            ))
            one = _chunks(sent)[1]

            # CONTROL reconnects; the chunk on BULK is still in flight.
            node.on_peer_state(peer, up=True, incarnation=2**62)
            node._send_snapshot(peer, state)
            restart = _chunks(sent)[2]
            assert restart.offset == 0

            # The old chunk's reply arrives, true about its own stream.
            node.on_install_snapshot_reply(peer, InstallSnapshotReply(
                term=node.term, bytes_received=one.offset + len(one.data),
                done=False, commit_index=0, request_id=one.request_id,
            ))
            assert len(_chunks(sent)) == 3, (
                "a reply to a chunk sent before the reconnect drove the "
                "transfer: a second stream now runs beside the first"
            )

            node.on_install_snapshot_reply(peer, InstallSnapshotReply(
                term=node.term, bytes_received=len(restart.data), done=False,
                commit_index=0, request_id=restart.request_id,
            ))
            chunks = _chunks(sent)
            assert len(chunks) == 4 and chunks[3].offset == len(restart.data)
        finally:
            await cluster.close()

    async def test_a_chunk_lost_with_its_bulk_connection_is_sent_again(
        self, tmp_path: Path,
    ) -> None:
        cluster = Cluster(3, tmp_path, timing=EAGER)
        await cluster.start()
        try:
            node, peer, state, sent = await _mid_transfer(cluster)
            node._send_snapshot(peer, state)
            node._send_snapshot(peer, state)
            assert len(_chunks(sent)) == 1, "a second chunk while one is in flight"

            # Its BULK connection ends -- the chunk, or its answer, lost with
            # it -- and CONTROL stays up, so nothing reports it.
            cluster.network.drop_connection(node.index, peer, Stream.BULK)
            node._send_snapshot(peer, state)

            chunks = _chunks(sent)
            assert len(chunks) == 2, (
                "a chunk lost with its BULK connection was not sent again on "
                "the connection that replaced it: the transfer stalls for as "
                "long as CONTROL stays up"
            )
            assert chunks[1].offset == chunks[0].offset
            assert chunks[1].request_id != chunks[0].request_id

            # Should the first copy's answer turn up after all, it is fenced.
            node.on_install_snapshot_reply(peer, InstallSnapshotReply(
                term=node.term, bytes_received=len(chunks[0].data), done=False,
                commit_index=0, request_id=chunks[0].request_id,
            ))
            assert len(_chunks(sent)) == 2
        finally:
            await cluster.close()

    async def test_a_chunk_slower_than_election_min_is_not_sent_again(
        self, tmp_path: Path,
    ) -> None:
        # The stall. Sent again under a new id once its answer was overdue, a
        # chunk whose round trip outlasted ``election_min`` had every answer
        # arrive already superseded -- only the newest copy's drove the
        # transfer -- so the transfer never passed its first chunk, and every
        # copy queued behind the first. Measured over real sockets (part 17 of
        # the fix record): 183 copies of chunk 0 in 30 s at 16 KiB/s with 4 KiB
        # chunks and a 150 ms ``election_min``.
        cluster = Cluster(3, tmp_path, timing=EAGER)
        await cluster.start()
        try:
            node, peer, state, sent = await _mid_transfer(cluster, keep_quorum=True)
            node._send_snapshot(peer, state)
            first = _chunks(sent)[0]

            # Slow, not lost: the connection it went out on is still in place,
            # however long the chunk takes, while ticks come and go.
            await asyncio.sleep(3 * EAGER.election_max)
            for _ in range(3):
                node._send_snapshot(peer, state)
            copies = len(_chunks(sent)) - 1
            assert copies == 0, (
                f"a chunk still in flight on its connection was sent {copies} "
                f"more time(s): only its connection ending can lose it, and a "
                f"copy can only queue behind it"
            )

            # Its answer arrives, late, and drives the transfer on.
            node.on_install_snapshot_reply(peer, InstallSnapshotReply(
                term=node.term, bytes_received=len(first.data), done=False,
                commit_index=0, request_id=first.request_id,
            ))
            chunks = _chunks(sent)
            assert len(chunks) == 2 and chunks[1].offset == len(first.data), (
                "the answer to a chunk slower than election_min did not drive "
                "the transfer on"
            )
        finally:
            await cluster.close()


async def _a_member_that_needs_the_snapshot(
    cluster: Cluster,
) -> tuple[Any, SnapshotMeta, bytes, Any]:
    """A leader holding a snapshot of several chunks, and a member restarted
    empty that needs it -- cut off, so all that reaches it is what a test hands
    it."""
    leader = await cluster.elect()
    await _fill(cluster, leader, 8)
    await cluster.settle(10)
    meta, payload = leader.node._snapshot_meta, leader.node._snapshot
    assert meta is not None, "the leader never compacted"
    assert len(payload) > 3 * EAGER.snapshot_chunk, (
        "a snapshot of so few chunks proves nothing about a transfer"
    )
    victim = next(m for m in cluster.members if m is not leader)
    cluster.network.isolate(victim.index)
    fresh = await cluster.restart(victim.index)
    assert fresh.node.commit_index < meta.last_index, (
        "the restarted member already holds the snapshot"
    )
    return leader, meta, payload, fresh


def _chunk_of(
    leader: Any, meta: SnapshotMeta, payload: bytes, number: int, request_id: int,
) -> InstallSnapshot:
    """Chunk ``number`` of ``payload``, as ``leader`` sends it."""
    size = EAGER.snapshot_chunk
    offset = number * size
    return InstallSnapshot(
        term=leader.node.term, leader=leader.index, last_index=meta.last_index,
        last_term=meta.last_term, offset=offset,
        data=payload[offset:offset + size],
        done=offset + size >= len(payload), ownership=b"", request_id=request_id,
    )


class TestAChunkSentAgainIsACopy:
    """A chunk the leader sends again must not cost the transfer.

    A chunk's delivery is at-least-once: when its answer is lost with the BULK
    connection that carried both, the member holds the chunk and the leader
    cannot know it, so the chunk goes out again on the next connection; and a
    CONTROL reconnect starts a transfer again while BULK may still carry the
    old one's chunk. The follower threw its whole buffer away on such a copy --
    its offset no longer matched what it had assembled -- and answered zero,
    which is the answer to the chunk in flight, so the leader started the
    transfer again from nothing. Measured in 16 runs of chaos-soak seed 140692,
    when copies came of the overdue re-send since removed (part 17 of the fix
    record): 343 of the 362 follower resets were copies of a chunk already
    held, and 350 of the 486 restarts followed one; members needing a snapshot
    never finished one. A copy is now answered with what is assembled, and the
    transfer goes on.
    """

    async def test_a_copy_of_a_chunk_already_held_is_answered_not_thrown_away(
        self, tmp_path: Path,
    ) -> None:
        cluster = Cluster(3, tmp_path, timing=EAGER)
        await cluster.start()
        try:
            leader, meta, payload, fresh = await _a_member_that_needs_the_snapshot(
                cluster,
            )
            node = fresh.node
            for number in range(3):
                reply = node.on_install_snapshot(
                    leader.index, _chunk_of(leader, meta, payload, number, number + 1),
                )
            held = reply.bytes_received
            assert held == 3 * EAGER.snapshot_chunk

            # The third chunk once more, as the leader sends it when the first
            # copy's answer was lost with its connection: the first copy
            # arrived, and so does this.
            again = node.on_install_snapshot(
                leader.index, _chunk_of(leader, meta, payload, 2, 99),
            )

            assert again.bytes_received == held, (
                f"a copy of a chunk this member already holds was answered with "
                f"{again.bytes_received}: it threw away {held} bytes of the "
                f"transfer, and the leader starts again from zero"
            )
            assert again.request_id == 99 and not again.done
            number = 3
            while not reply.done:
                reply = node.on_install_snapshot(
                    leader.index,
                    _chunk_of(leader, meta, payload, number, 100 + number),
                )
                assert reply.bytes_received > 0, f"chunk {number} was refused"
                number += 1
            assert node.commit_index >= meta.last_index, "the snapshot never installed"
        finally:
            await cluster.close()

    async def test_a_chunk_that_disagrees_with_what_is_held_still_restarts(
        self, tmp_path: Path,
    ) -> None:
        # The boundary of the rule above: a copy is recognised by its bytes,
        # not by its offset alone. Different bytes at an offset already passed
        # are no copy, and keeping the buffer would be the splice that "restart
        # rather than splice" exists to prevent.
        cluster = Cluster(3, tmp_path, timing=EAGER)
        await cluster.start()
        try:
            leader, meta, payload, fresh = await _a_member_that_needs_the_snapshot(
                cluster,
            )
            node = fresh.node
            for number in range(3):
                node.on_install_snapshot(
                    leader.index, _chunk_of(leader, meta, payload, number, number + 1),
                )
            copy = _chunk_of(leader, meta, payload, 2, 99)
            other = replace(copy, data=bytes(byte ^ 0xFF for byte in copy.data))

            reply = node.on_install_snapshot(leader.index, other)

            assert reply.bytes_received == 0 and not reply.done, (
                f"bytes that disagree with those held at offset {copy.offset} "
                f"were accepted as a copy ({reply.bytes_received} acknowledged)"
            )
            assert leader.index not in node._installing, "the buffer was kept"
        finally:
            await cluster.close()

    async def test_a_chunk_sent_again_after_its_answer_was_lost_does_not_restart(
        self, tmp_path: Path,
    ) -> None:
        # End to end: the leader's own resend, and the member's own answers.
        cluster = Cluster(3, tmp_path, timing=EAGER)
        await cluster.start()
        try:
            leader, meta, payload, fresh = await _a_member_that_needs_the_snapshot(
                cluster,
            )
            node, peer = leader.node, fresh.index
            sent: list[InstallSnapshot] = []
            real_send = node.transport.send

            def recording(target: int, message: Any, **kwargs: Any) -> None:
                if target == peer and isinstance(message, InstallSnapshot):
                    sent.append(message)
                real_send(target, message, **kwargs)

            node.transport.send = recording
            cluster.network.isolate(leader.index)
            node.on_peer_state(peer, up=True, incarnation=2**62)
            state = node._peers[peer]

            def deliver(chunk: InstallSnapshot) -> InstallSnapshotReply:
                reply: InstallSnapshotReply = fresh.node.on_install_snapshot(
                    leader.index, chunk,
                )
                return reply

            node._send_snapshot(peer, state)
            for _ in range(2):
                node.on_install_snapshot_reply(peer, deliver(sent[-1]))
            held = sent[-1]
            # Delivered, and its answer lost with the BULK connection that
            # carried both: the member holds the chunk, the leader cannot know
            # it, and it sends the chunk again on the next connection.
            first = deliver(held)
            cluster.network.drop_connection(leader.index, peer, Stream.BULK)
            node._send_snapshot(peer, state)
            again = sent[-1]
            assert again.offset == held.offset and again.request_id != held.request_id

            second = deliver(again)
            assert second.bytes_received == first.bytes_received, (
                f"the copy was answered {second.bytes_received} after the chunk "
                f"was answered {first.bytes_received}: the member threw the "
                f"transfer away, and the leader starts it again from zero"
            )
            node.on_install_snapshot_reply(peer, second)
            for _ in range(len(payload)):
                reply = deliver(sent[-1])
                node.on_install_snapshot_reply(peer, reply)
                if reply.done:
                    break
            starts = [chunk for chunk in sent if chunk.offset == 0]
            assert len(starts) == 1, f"the transfer started {len(starts)} times"
            assert fresh.node.commit_index >= meta.last_index, (
                "the snapshot never installed"
            )
        finally:
            await cluster.close()


class TestATransferIsOfOneSnapshot:
    """An offset means something only relative to one snapshot's bytes.

    Both ends reassembled or sliced by offset alone. The leader sliced whatever
    snapshot it held *now* at the offset it had reached, and compaction
    replaces that snapshot whenever it likes; the follower appended any chunk
    whose offset lined up. The chaos soak's splice detector measured a follower
    fed the head of one snapshot and the tail of the next -- and when the result
    happened to decode, it installed, and the replicas that had installed it
    lost acknowledged writes while reporting themselves caught up.
    """

    async def test_a_chunk_of_another_snapshot_does_not_continue_a_transfer(
        self, tmp_path: Path,
    ) -> None:
        cluster = Cluster(3, tmp_path, timing=EAGER)
        await cluster.start()
        try:
            leader = await cluster.elect()
            follower = next(m for m in cluster.members if m is not leader)
            node = follower.node
            first = InstallSnapshot(
                term=node.term, leader=leader.index, last_index=40, last_term=1,
                offset=0, data=b"head of the snapshot through 40", done=False,
                ownership=b"",
            )
            reply = node.on_install_snapshot(leader.index, first)
            assert reply.bytes_received == len(first.data)

            other = InstallSnapshot(
                term=node.term, leader=leader.index, last_index=56, last_term=1,
                offset=len(first.data), data=b"tail of the snapshot through 56",
                done=False, ownership=b"",
            )
            reply = node.on_install_snapshot(leader.index, other)

            assert reply.bytes_received == 0 and not reply.done, (
                f"the follower acknowledged {reply.bytes_received} bytes of a "
                f"transfer that began as the snapshot through 40 and went on "
                f"with the snapshot through 56 -- a splice"
            )
            assert leader.index not in node._installing, (
                "the spliced buffer was kept"
            )
        finally:
            await cluster.close()

    async def test_a_snapshot_whose_contents_disagree_with_its_transfer_is_refused(
        self, tmp_path: Path,
    ) -> None:
        cluster = Cluster(3, tmp_path, timing=EAGER)
        await cluster.start()
        try:
            leader = await cluster.elect()
            await _fill(cluster, leader, 8)
            await cluster.settle(10)
            meta, payload = leader.node._snapshot_meta, leader.node._snapshot
            assert meta is not None and payload, "the leader never compacted"
            victim = next(m for m in cluster.members if m is not leader)
            cluster.network.isolate(victim.index)
            fresh = await cluster.restart(victim.index)

            # The bytes of the snapshot through ``meta.last_index``, sent as if
            # they were a later one.
            reply = fresh.node.on_install_snapshot(leader.index, InstallSnapshot(
                term=leader.node.term, leader=leader.index,
                last_index=meta.last_index + 3, last_term=meta.last_term,
                offset=0, data=payload, done=True, ownership=b"",
            ))

            assert not reply.done, (
                f"installed a snapshot whose contents end at {meta.last_index} "
                f"under a transfer claiming {meta.last_index + 3}: the leader "
                f"now credits an index this member does not hold"
            )
            assert fresh.node.last_applied == 0
        finally:
            await cluster.close()

    async def test_a_leader_sends_one_snapshot_per_transfer_and_credits_it(
        self, tmp_path: Path,
    ) -> None:
        cluster = Cluster(3, tmp_path, timing=EAGER)
        await cluster.start()
        try:
            leader = await cluster.elect()
            await _fill(cluster, leader, 8)
            await cluster.settle(10)
            node = leader.node
            meta, payload = node._snapshot_meta, node._snapshot
            assert meta is not None, "the leader never compacted"
            assert len(payload) > 2 * EAGER.snapshot_chunk, (
                "a snapshot of one chunk cannot be spliced"
            )
            peer = min(node._peers)

            sent: list[InstallSnapshot] = []
            real_send = node.transport.send

            def recording(target: int, message: Any, **kwargs: Any) -> None:
                if isinstance(message, InstallSnapshot) and target == peer:
                    sent.append(message)
                real_send(target, message, **kwargs)

            node.transport.send = recording  # type: ignore[method-assign, assignment]
            cluster.network.isolate(leader.index)
            node.on_peer_state(peer, up=True, incarnation=2**62)
            state = node._peers[peer]

            node._send_snapshot(peer, state)
            assert len(sent) == 1 and sent[0].offset == 0

            # The leader compacts before the next chunk goes out: a newer
            # snapshot, with different bytes, replaces the one being sent.
            node._snapshot = bytes(reversed(payload)) + b"newer"
            node._snapshot_meta = SnapshotMeta(
                last_index=meta.last_index + 5, last_term=meta.last_term,
                resources=0,
            )
            node.on_install_snapshot_reply(peer, InstallSnapshotReply(
                term=node.term, bytes_received=len(sent[0].data), done=False,
                request_id=sent[0].request_id,
            ))

            assert len(sent) == 2
            second = sent[1]
            assert (second.last_index, second.last_term) == (
                meta.last_index, meta.last_term,
            ), (
                f"the transfer began as the snapshot through {meta.last_index} "
                f"and its second chunk claims the one through "
                f"{second.last_index} -- a splice"
            )
            assert second.data == payload[
                second.offset:second.offset + len(second.data)
            ], "the second chunk's bytes are not the pinned snapshot's"

            # Answered as a completed install is: the follower now commits
            # through the snapshot it installed, and says so.
            node.on_install_snapshot_reply(peer, InstallSnapshotReply(
                term=node.term, bytes_received=len(payload), done=True,
                commit_index=meta.last_index, request_id=second.request_id,
            ))
            assert state.match_index == meta.last_index, (
                f"a completed transfer of the snapshot through "
                f"{meta.last_index} credited index {state.match_index}"
            )
        finally:
            await cluster.close()

