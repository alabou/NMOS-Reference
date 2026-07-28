# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for nmos.tasks — DispatchGroup structured concurrency."""

from __future__ import annotations

import asyncio

import pytest

from nmos.errors import Done, Expired, Idle, InvalidOperation, UnexpectedError
from nmos.tasks import DispatchGroup


class TestDispatchGroupBasic:
    """Basic task dispatch and wait."""

    @pytest.mark.asyncio
    async def test_create(self) -> None:
        dg = await DispatchGroup.create()
        assert not dg.is_done

    @pytest.mark.asyncio
    async def test_dispatch_and_wait(self) -> None:
        results: list[int] = []

        async def work(n: int) -> None:
            results.append(n)

        dg = await DispatchGroup.create()
        await dg.dispatch(work(1))
        await dg.dispatch(work(2))
        await dg.dispatch(work(3))
        await dg.wait()

        assert sorted(results) == [1, 2, 3]
        assert dg.is_done

    @pytest.mark.asyncio
    async def test_wait_empty(self) -> None:
        dg = await DispatchGroup.create()
        await dg.wait()
        assert dg.is_done

    @pytest.mark.asyncio
    async def test_dispatch_async_work(self) -> None:
        result: list[str] = []

        async def slow_work() -> None:
            await asyncio.sleep(0.01)
            result.append("done")

        dg = await DispatchGroup.create()
        await dg.dispatch(slow_work())
        await dg.wait()
        assert result == ["done"]


class TestDispatchGroupCancellation:
    """Cancellation propagation."""

    @pytest.mark.asyncio
    async def test_cancel_stops_tasks(self) -> None:
        started = asyncio.Event()
        finished = False

        async def long_work() -> None:
            nonlocal finished
            started.set()
            await asyncio.sleep(10)  # will be cancelled
            finished = True

        dg = await DispatchGroup.create()
        await dg.dispatch(long_work())

        await started.wait()
        dg.cancel()
        assert dg.is_done

        # Give tasks time to process cancellation
        await asyncio.sleep(0.05)
        assert not finished

    @pytest.mark.asyncio
    async def test_error_cancels_siblings(self) -> None:
        sibling_cancelled = False
        sibling_started = asyncio.Event()

        async def failing_task() -> None:
            await sibling_started.wait()  # wait for sibling to start
            raise ValueError("test error")

        async def long_sibling() -> None:
            nonlocal sibling_cancelled
            sibling_started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                sibling_cancelled = True
                raise

        dg = await DispatchGroup.create()
        await dg.dispatch(failing_task())
        await dg.dispatch(long_sibling())

        with pytest.raises(ValueError, match="test error"):
            await dg.wait()

        assert sibling_cancelled

    @pytest.mark.asyncio
    async def test_done_error_captures_first_exception(self) -> None:
        async def fail() -> None:
            raise RuntimeError("first error")

        dg = await DispatchGroup.create()
        await dg.dispatch(fail())

        with pytest.raises(RuntimeError):
            await dg.wait()

        assert dg.done_error is not None
        assert "first error" in str(dg.done_error)


class TestDispatchGroupTimeout:
    """Timeout behavior."""

    @pytest.mark.asyncio
    async def test_timeout_cancels_tasks(self) -> None:
        async def forever() -> None:
            await asyncio.sleep(100)

        dg = await DispatchGroup.create(timeout=0.05)
        await dg.dispatch(forever())

        with pytest.raises(Expired):
            await dg.wait()

        assert dg.is_done

    @pytest.mark.asyncio
    async def test_no_timeout_when_tasks_finish_quickly(self) -> None:
        result: list[int] = []

        async def quick() -> None:
            result.append(1)

        dg = await DispatchGroup.create(timeout=5.0)
        await dg.dispatch(quick())
        await dg.wait()

        assert result == [1]


class TestDispatchGroupWatchdog:
    """Watchdog behavior."""

    @pytest.mark.asyncio
    async def test_watchdog_fires_when_not_ticked(self) -> None:
        async def worker() -> None:
            await asyncio.sleep(10)

        dg = await DispatchGroup.create(watchdog=0.05)
        assert dg.has_watchdog
        await dg.dispatch(worker())

        with pytest.raises(Idle):
            await dg.wait()

    @pytest.mark.asyncio
    async def test_watchdog_kept_alive_by_tick(self) -> None:
        tick_count = 0

        async def worker_with_ticks(dg: DispatchGroup) -> None:
            nonlocal tick_count
            for _ in range(3):
                await asyncio.sleep(0.02)
                dg.tick()
                tick_count += 1
            # Signal done — watchdog should not fire after tasks finish
            dg.cancel()

        dg = await DispatchGroup.create(watchdog=0.1)
        await dg.dispatch(worker_with_ticks(dg))

        try:
            await dg.wait()
        except asyncio.CancelledError:
            pass

        assert tick_count == 3


class TestDispatchGroupHierarchy:
    """Parent-child group relationships."""

    @pytest.mark.asyncio
    async def test_child_group(self) -> None:
        results: list[str] = []

        async def child_work() -> None:
            results.append("child")

        dg = await DispatchGroup.create()
        child = await dg.new_child()

        await child.dispatch(child_work())
        await child.wait()
        assert results == ["child"]

    @pytest.mark.asyncio
    async def test_parent_cancel_cascades_to_child(self) -> None:
        child_cancelled = False
        child_started = asyncio.Event()

        async def child_work() -> None:
            nonlocal child_cancelled
            child_started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                child_cancelled = True
                raise

        dg = await DispatchGroup.create()
        child = await dg.new_child()
        await child.dispatch(child_work())

        # Wait for child to start, then cancel parent
        await child_started.wait()
        dg.cancel()
        await asyncio.sleep(0.05)

        assert child.is_done
        assert child_cancelled


class TestDispatchGroupClose:
    """close() — cancel and wait for the tasks to actually finish."""

    @pytest.mark.asyncio
    async def test_cancel_returns_before_tasks_stop(self) -> None:
        """The gap close() exists to fill: cancel() does not join."""
        stopped = False
        started = asyncio.Event()

        async def work() -> None:
            nonlocal stopped
            started.set()
            try:
                await asyncio.sleep(10)
            finally:
                stopped = True

        dg = await DispatchGroup.create()
        task = await dg.dispatch(work())
        await started.wait()

        dg.cancel()
        # Cancellation has only been requested; the task has not run since.
        assert not task.done()
        assert not stopped

        await dg.close()

    @pytest.mark.asyncio
    async def test_close_cancels_and_joins(self) -> None:
        stopped = False
        started = asyncio.Event()

        async def work() -> None:
            nonlocal stopped
            started.set()
            try:
                await asyncio.sleep(10)
            finally:
                stopped = True

        dg = await DispatchGroup.create()
        task = await dg.dispatch(work())
        await started.wait()

        await dg.close()

        assert task.done()
        assert stopped
        assert dg.is_done
        assert dg.is_closed

    @pytest.mark.asyncio
    async def test_close_empty_group(self) -> None:
        dg = await DispatchGroup.create()
        await dg.close()
        assert dg.is_closed

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self) -> None:
        """Safe in a finally that may run more than once."""
        dg = await DispatchGroup.create()
        await dg.dispatch(asyncio.sleep(10))
        await asyncio.sleep(0)         # let the task reach its first await
        await dg.close()
        await dg.close()
        assert dg.is_closed

    @pytest.mark.asyncio
    async def test_close_detaches_from_parent(self) -> None:
        """A long-lived parent must not accumulate finished children."""
        dg = await DispatchGroup.create()

        for _ in range(5):
            child = await dg.new_child()
            await child.dispatch(asyncio.sleep(10))
            await asyncio.sleep(0)     # let the task reach its first await
            await child.close()

        assert dg.child_count == 0
        await dg.close()

    @pytest.mark.asyncio
    async def test_close_cascades_to_children(self) -> None:
        child_stopped = False
        grandchild_stopped = False
        both_started = asyncio.Event()
        started_count = 0

        async def work(which: str) -> None:
            nonlocal child_stopped, grandchild_stopped, started_count
            started_count += 1
            if started_count == 2:
                both_started.set()
            try:
                await asyncio.sleep(10)
            finally:
                if which == "child":
                    child_stopped = True
                else:
                    grandchild_stopped = True

        dg = await DispatchGroup.create()
        child = await dg.new_child()
        grandchild = await child.new_child()
        await child.dispatch(work("child"))
        await grandchild.dispatch(work("grandchild"))
        await both_started.wait()

        await dg.close()

        assert child_stopped
        assert grandchild_stopped
        assert child.is_closed
        assert grandchild.is_closed

    @pytest.mark.asyncio
    async def test_dispatch_after_close_raises(self) -> None:
        dg = await DispatchGroup.create()
        await dg.close()

        coro = asyncio.sleep(0)
        with pytest.raises(InvalidOperation):
            await dg.dispatch(coro)
        coro.close()

    @pytest.mark.asyncio
    async def test_new_child_after_close_raises(self) -> None:
        dg = await DispatchGroup.create()
        await dg.close()

        with pytest.raises(InvalidOperation):
            await dg.new_child()

    @pytest.mark.asyncio
    async def test_close_raises_unrecoverable_task_error(self) -> None:
        ran = asyncio.Event()

        async def failing() -> None:
            ran.set()
            raise UnexpectedError("task blew up")

        dg = await DispatchGroup.create()
        await dg.dispatch(failing())
        await ran.wait()               # the task must run before we close

        with pytest.raises(UnexpectedError):
            await dg.close()

    @pytest.mark.asyncio
    async def test_close_does_not_raise_recoverable_task_error(self) -> None:
        """Cancellation-class errors are the expected outcome of closing."""
        ran = asyncio.Event()

        async def finishing() -> None:
            ran.set()
            raise Done("context is done")

        dg = await DispatchGroup.create()
        await dg.dispatch(finishing())
        await ran.wait()

        await dg.close()

        assert isinstance(dg.done_error, Done)

    @pytest.mark.asyncio
    # The un-awaited coroutine is the point of this test, not a defect.
    @pytest.mark.filterwarnings("ignore::RuntimeWarning")
    async def test_close_ignores_a_task_that_never_started(self) -> None:
        """Cancelled before its first step, a task contributes no error."""
        async def failing() -> None:
            raise UnexpectedError("never reached")

        dg = await DispatchGroup.create()
        await dg.dispatch(failing())

        await dg.close()               # must not raise

        assert dg.done_error is None

    @pytest.mark.asyncio
    async def test_close_from_inside_own_task(self) -> None:
        """A task closing its own group must not wait for itself."""
        closed = False

        async def self_closing(dg: DispatchGroup) -> None:
            nonlocal closed
            await dg.close()
            closed = True

        dg = await DispatchGroup.create()
        await dg.dispatch(self_closing(dg))

        await asyncio.wait_for(dg.done(), timeout=1.0)
        await asyncio.sleep(0)

        assert closed
        assert dg.is_closed

    @pytest.mark.asyncio
    async def test_close_stops_the_watchdog(self) -> None:
        """The watchdog must not outlive the group it watches."""
        dg = await DispatchGroup.create(watchdog=0.05)
        await dg.dispatch(asyncio.sleep(10))

        waiter = asyncio.create_task(dg.wait())
        await asyncio.sleep(0.01)      # let wait() spawn the watchdog

        await dg.close()

        # Well past the watchdog period: a surviving watchdog would fire Idle.
        await asyncio.sleep(0.15)
        assert not isinstance(dg.done_error, Idle)

        waiter.cancel()
        try:
            await waiter
        except (asyncio.CancelledError, Done, Idle):
            pass


class TestDispatchGroupGoPatterns:
    """Verify common usage patterns work correctly."""

    @pytest.mark.asyncio
    async def test_go_server_pattern(self) -> None:
        """main() launching multiple server tasks."""
        started: list[str] = []

        async def run_node_server() -> None:
            started.append("node")
            await asyncio.sleep(0.01)

        async def run_nc_server() -> None:
            started.append("nc")
            await asyncio.sleep(0.01)

        async def run_registration() -> None:
            started.append("reg")
            await asyncio.sleep(0.01)

        dg = await DispatchGroup.create()
        await dg.dispatch(run_node_server())
        await dg.dispatch(run_nc_server())
        await dg.dispatch(run_registration())
        await dg.wait()

        assert sorted(started) == ["nc", "node", "reg"]

    @pytest.mark.asyncio
    async def test_go_select_done_pattern(self) -> None:
        """Polling dg.is_done in a loop to terminate on cancellation."""
        iterations = 0

        async def periodic_work(dg: DispatchGroup) -> None:
            nonlocal iterations
            while not dg.is_done:
                iterations += 1
                await asyncio.sleep(0.01)
                if iterations >= 5:
                    return

        dg = await DispatchGroup.create()
        await dg.dispatch(periodic_work(dg))
        await dg.wait()

        assert iterations == 5
