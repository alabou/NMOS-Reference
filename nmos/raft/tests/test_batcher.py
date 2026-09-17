# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Batching: the throughput claim, tested as a property rather than a timing.

The claim is "everything proposed within one event-loop tick commits in a
single quorum round". That is checkable without measuring anything -- submit
several operations without awaiting between them, and assert the drain saw one
batch -- which is the only kind of concurrency test worth having, because a
timing-based one would pass or fail with machine load.
"""

from __future__ import annotations

import asyncio

import pytest

from nmos.raft.batcher import Pending, ProposalBatcher


class _Recorder:
    """Captures batches and resolves their futures, standing in for the leader."""

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def drain(self, batch: list[Pending[str, str]]) -> None:
        self.batches.append([item.operation for item in batch])
        for item in batch:
            item.future.set_result(f"applied:{item.operation}")


class TestCoalescing:
    async def test_everything_in_one_tick_forms_one_batch(self) -> None:
        """The property the design rests on.

        Handlers resumed from a single epoll wakeup all submit before the
        drain runs, because ``call_soon`` runs after the current callback but
        before the loop returns to the selector.
        """
        recorder = _Recorder()
        batcher: ProposalBatcher[str, str] = ProposalBatcher(recorder.drain)

        futures = [batcher.submit(f"op{index}") for index in range(50)]
        assert recorder.batches == [], "drained before the tick ended"

        results = await asyncio.gather(*futures)

        assert len(recorder.batches) == 1
        assert len(recorder.batches[0]) == 50
        assert results[0] == "applied:op0"

    async def test_separate_ticks_form_separate_batches(self) -> None:
        recorder = _Recorder()
        batcher: ProposalBatcher[str, str] = ProposalBatcher(recorder.drain)

        await batcher.submit("first")
        await batcher.submit("second")

        assert recorder.batches == [["first"], ["second"]]

    async def test_a_full_batch_drains_immediately(self) -> None:
        """A burst larger than the cap becomes several full batches.

        Not one oversized one: a single ``AppendEntries`` has to stay under the
        frame cap.
        """
        recorder = _Recorder()
        batcher: ProposalBatcher[str, str] = ProposalBatcher(
            recorder.drain, max_batch=4,
        )

        futures = [batcher.submit(f"op{index}") for index in range(10)]
        # Two full batches already went, synchronously, without yielding.
        assert [len(batch) for batch in recorder.batches] == [4, 4]

        await asyncio.gather(*futures)
        assert [len(batch) for batch in recorder.batches] == [4, 4, 2]

    async def test_an_empty_tick_drains_nothing(self) -> None:
        recorder = _Recorder()
        ProposalBatcher(recorder.drain)
        await asyncio.sleep(0)
        assert recorder.batches == []


class TestFutureRegistration:
    async def test_submit_is_synchronous(self) -> None:
        """The window this closes would deliver a result to nobody.

        If ``submit`` awaited, the entry could commit and apply between "the
        operation was accepted" and "a waiter exists" -- and the caller would
        wait forever for something that had already happened.
        """
        recorder = _Recorder()
        batcher: ProposalBatcher[str, str] = ProposalBatcher(recorder.drain)

        future = batcher.submit("op")
        assert isinstance(future, asyncio.Future)
        assert not future.done()
        assert batcher.pending == 1

    async def test_the_result_reaches_the_right_waiter(self) -> None:
        recorder = _Recorder()
        batcher: ProposalBatcher[str, str] = ProposalBatcher(recorder.drain)

        futures = {op: batcher.submit(op) for op in ("a", "b", "c")}
        await asyncio.gather(*futures.values())

        for op, future in futures.items():
            assert future.result() == f"applied:{op}"

    async def test_order_is_preserved_within_a_batch(self) -> None:
        """Per-Node serialisation depends on it; reordering would break it."""
        recorder = _Recorder()
        batcher: ProposalBatcher[str, str] = ProposalBatcher(recorder.drain)

        futures = [batcher.submit(f"op{index}") for index in range(20)]
        await asyncio.gather(*futures)

        assert recorder.batches[0] == [f"op{index}" for index in range(20)]


class TestFailure:
    async def test_fail_all_rejects_every_queued_proposal(self) -> None:
        recorder = _Recorder()
        batcher: ProposalBatcher[str, str] = ProposalBatcher(recorder.drain)

        futures = [batcher.submit(f"op{index}") for index in range(3)]
        batcher.fail_all(RuntimeError("lost leadership"))

        for future in futures:
            with pytest.raises(RuntimeError, match="lost leadership"):
                await future
        assert recorder.batches == []

    async def test_the_batcher_stays_usable_after_a_failure(self) -> None:
        """A member that loses leadership and regains it keeps its batcher.

        One that refused everything after a single failure would stop serving
        until the process restarted.
        """
        recorder = _Recorder()
        batcher: ProposalBatcher[str, str] = ProposalBatcher(recorder.drain)

        doomed = batcher.submit("doomed")
        batcher.fail_all(RuntimeError("lost leadership"))
        with pytest.raises(RuntimeError):
            await doomed

        assert await batcher.submit("later") == "applied:later"

    async def test_failing_an_empty_batcher_is_harmless(self) -> None:
        recorder = _Recorder()
        batcher: ProposalBatcher[str, str] = ProposalBatcher(recorder.drain)
        batcher.fail_all(RuntimeError("nothing queued"))
        assert batcher.pending == 0

    async def test_a_scheduled_drain_after_fail_all_does_nothing(self) -> None:
        """The pending list was emptied; the scheduled callback must cope."""
        recorder = _Recorder()
        batcher: ProposalBatcher[str, str] = ProposalBatcher(recorder.drain)

        future = batcher.submit("doomed")
        batcher.fail_all(RuntimeError("gone"))
        with pytest.raises(RuntimeError):
            await future

        await asyncio.sleep(0)
        assert recorder.batches == []
