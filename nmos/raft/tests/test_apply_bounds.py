# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""A member applies committed entries and nothing else.

Raft's state machine may only reflect entries a quorum has agreed on. The
applier used to ask its log for a whole batch from ``last_applied + 1`` and
apply whatever came back, and ``RaftLog.slice`` clamps to ``last_index`` — the
*log* — not to ``commit_index``. A follower's log routinely runs ahead of what
is committed; that is what replication in flight looks like. So whenever the
uncommitted tail was longer than the gap, the member applied entries that no
quorum had accepted.

It breaks two things at once:

* the state machine reflects operations that may never commit, and a client
  reading that member sees resources that do not exist anywhere else;
* ``last_applied`` advances past them, and because the applier resumes at
  ``last_applied + 1``, the entries that *replace* them when a new leader
  overwrites those indices are **never applied**. The member is permanently
  wrong at those indices and no later message repairs it.

The chaos soak did find it, eventually, as "index 72 applied as term 14 by one
member and term 12 by member 1" — some four hundred steps after the fact, on a
member that had been isolated for five. That is a probabilistic detector of a
deterministic bug, which is the wrong way round: these tests drive the exact
condition in a handful of milliseconds.

``go.etcd.io/raft`` asserts the same invariant at the moment it would break
rather than waiting for a divergence: ``log.go:332-334``, ``appliedTo`` panics
when ``committed < i``, and ``log.go:48`` states it outright as
``applied <= committed``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from nmos.raft.errors import RaftInvariantViolated
from nmos.raft.messages import AppendEntries, WireEntry
from nmos.raft.operations import ProposalId, RegisterOp, encode_operation
from nmos.raft.tests._harness import FAST, Cluster
from nmos.registry.tests._fixtures import make_node
from nmos.registry.types import ResourceType, TaiCursor

pytestmark = pytest.mark.asyncio


def _entry(term: int, index: int, serial: int) -> WireEntry:
    """One replicable registration, at a chosen term and index."""
    node_id = f"{serial:08d}-0000-4000-8000-000000000000"
    cursor = TaiCursor(seconds=1_600_000_000 + serial, nanoseconds=0)
    operation = RegisterOp(
        proposal=ProposalId(1, serial),
        resource_type=ResourceType.NODE,
        resource_id=node_id,
        node_id=node_id,
        body_text=json.dumps(make_node(node_id)),
        created=cursor,
        updated=cursor,
        # Zero, so nothing here is ever garbage-collected mid-test: an expiry
        # would move `last_applied` for a reason unrelated to the bound.
        health=0,
        expect_created=True,
    )
    return WireEntry(term=term, index=index, payload=encode_operation(operation))


async def _settled(cluster: Cluster) -> None:
    """Let the applier run to completion."""
    for _ in range(20):
        await asyncio.sleep(0)
    await asyncio.sleep(FAST.heartbeat)


class TestTheInvariantIsAssertedNotAssumed:
    """The bound is enforced; this is what happens if some other path breaks it.

    A bug detector rather than a safeguard. Nothing a peer sends can reach
    these numbers except through logic in ``node.py``, and nothing applied
    survives a restart -- the log and the state machine are in memory and are
    rebuilt from the leader every time. So a violation means a defect here,
    which is the same position ``go.etcd.io/raft`` takes: 32 ``Panicf`` sites
    in the library and no ``recover()`` anywhere in it.
    """

    async def test_an_impossible_applied_index_is_refused(
        self, tmp_path: Path,
    ) -> None:
        cluster = Cluster(3, tmp_path, timing=FAST)
        await cluster.start()
        try:
            node = cluster.members[1].node
            # Reach past the fix to the state it exists to prevent.
            node._machine._last_applied = 5  # noqa: SLF001
            with pytest.raises(RaftInvariantViolated) as caught:
                await node._apply_committed()  # noqa: SLF001
            assert "applied through 5" in str(caught.value)
        finally:
            await cluster.close()

    async def test_an_impossible_commit_index_is_refused(
        self, tmp_path: Path,
    ) -> None:
        cluster = Cluster(3, tmp_path, timing=FAST)
        await cluster.start()
        try:
            node = cluster.members[1].node
            node._commit_index = 99  # noqa: SLF001
            with pytest.raises(RaftInvariantViolated) as caught:
                await node._apply_committed()  # noqa: SLF001
            assert "committed through 99" in str(caught.value)
        finally:
            await cluster.close()

    async def test_the_violation_escapes_the_appliers_catch_all(
        self, tmp_path: Path,
    ) -> None:
        """The half that would silently not work.

        ``_apply_forever`` catches ``Exception`` and logs it so one bad apply
        cannot kill a member. Without an explicit re-raise the assertion would
        be caught, logged once per wake-up, and the loop would carry on with
        the invariant still broken -- a silent failure wearing the costume of a
        handled one.
        """
        cluster = Cluster(3, tmp_path, timing=FAST)
        await cluster.start()
        try:
            node = cluster.members[1].node
            node._machine._last_applied = 5  # noqa: SLF001
            node._apply_wake.set()  # noqa: SLF001

            applier = node._applier  # noqa: SLF001
            assert applier is not None
            for _ in range(50):
                if applier.done():
                    break
                await asyncio.sleep(0)
            assert applier.done(), (
                "the applier task is still running -- the violation was "
                "swallowed by the catch-all and logged in a loop"
            )
            with pytest.raises(RaftInvariantViolated):
                applier.result()
        finally:
            await cluster.close()


class TestTheApplierStopsAtTheCommitIndex:
    async def test_a_follower_does_not_apply_an_uncommitted_tail(
        self, tmp_path: Path,
    ) -> None:
        """The bug, driven directly.

        Ten entries are replicated with ``leader_commit`` at three. The follower
        must apply exactly three, however large its apply batch is.
        """
        cluster = Cluster(3, tmp_path, timing=FAST)
        await cluster.start()
        try:
            follower = cluster.members[1].node
            term = follower.term + 1

            follower.on_append_entries(
                0,
                AppendEntries(
                    term=term,
                    leader=0,
                    prev_log_index=0,
                    prev_log_term=0,
                    # Three of the ten, which is the whole point.
                    leader_commit=3,
                    request_id=1,
                    entries=tuple(
                        _entry(term, index, index) for index in range(1, 11)
                    ),
                ),
            )
            await _settled(cluster)

            assert follower.log.last_index == 10, "the entries did not replicate"
            assert follower.commit_index == 3, (
                "the follower committed beyond what the leader vouched for"
            )
            assert follower.last_applied == 3, (
                f"the follower applied through {follower.last_applied} with only "
                f"{follower.commit_index} committed -- it applied entries no "
                f"quorum had accepted"
            )
        finally:
            await cluster.close()

    async def test_applied_never_runs_past_committed_as_commit_advances(
        self, tmp_path: Path,
    ) -> None:
        """The invariant holds at every step, not only at the end.

        Checked after each advance because the failure is transient by nature:
        a member that overshoots and is then caught up by a later heartbeat
        looks correct at rest and was wrong in between -- and in between is when
        a client can read it.
        """
        cluster = Cluster(3, tmp_path, timing=FAST)
        await cluster.start()
        try:
            follower = cluster.members[1].node
            term = follower.term + 1

            follower.on_append_entries(
                0,
                AppendEntries(
                    term=term, leader=0, prev_log_index=0, prev_log_term=0,
                    leader_commit=0, request_id=1,
                    entries=tuple(
                        _entry(term, index, index) for index in range(1, 11)
                    ),
                ),
            )
            await _settled(cluster)

            for commit in (2, 5, 9, 10):
                # A heartbeat: no entries, so it vouches only as far as
                # `prev_log_index`, which is what lets the commit index move
                # without the log changing.
                follower.on_append_entries(
                    0,
                    AppendEntries(
                        term=term, leader=0, prev_log_index=commit,
                        prev_log_term=term, leader_commit=commit,
                        request_id=0, entries=(),
                    ),
                )
                await _settled(cluster)
                assert follower.last_applied <= follower.commit_index, (
                    f"applied {follower.last_applied} > committed "
                    f"{follower.commit_index} after advancing to {commit}"
                )
                assert follower.commit_index == commit
                assert follower.last_applied == commit, (
                    "the follower did not catch up to what was committed"
                )
        finally:
            await cluster.close()

    async def test_a_batch_smaller_than_the_gap_still_applies_everything(
        self, tmp_path: Path,
    ) -> None:
        """The bound must not become a cap.

        Clamping to ``commit_index - start + 1`` is only correct if the loop
        still iterates: a version that took the minimum and then stopped would
        pass the two tests above and silently apply one batch per heartbeat.
        """
        timing = FAST.__class__(
            **{
                **{
                    field: getattr(FAST, field)
                    for field in FAST.__dataclass_fields__
                },
                "max_apply_batch": 2,
            },
        )
        cluster = Cluster(3, tmp_path, timing=timing)
        await cluster.start()
        try:
            follower = cluster.members[1].node
            term = follower.term + 1
            follower.on_append_entries(
                0,
                AppendEntries(
                    term=term, leader=0, prev_log_index=0, prev_log_term=0,
                    leader_commit=9, request_id=1,
                    entries=tuple(
                        _entry(term, index, index) for index in range(1, 11)
                    ),
                ),
            )
            await _settled(cluster)

            assert follower.commit_index == 9
            assert follower.last_applied == 9, (
                f"a batch of 2 applied only through {follower.last_applied} of "
                f"9 committed -- the loop stopped after one batch"
            )
        finally:
            await cluster.close()
