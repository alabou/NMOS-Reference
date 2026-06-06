# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Structured concurrency for NMOS via a DispatchGroup wrapper.

Provides a DispatchGroup class that wraps asyncio.TaskGroup with a stable
lifecycle API:

    dg = await DispatchGroup.create()
    await dg.dispatch(some_coro())
    await dg.wait()
    dg.cancel()
    await dg.done()      # blocking wait until finished
    dg.is_done           # non-blocking poll

All standard asyncio primitives (Queue, sleep, Lock, etc.) work freely
inside dispatched tasks. The wrapper only standardizes lifecycle management.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from nmos.errors import Done, Expired, Idle


class DispatchGroup:
    """Structured concurrency group for managing async tasks.

    Manages a set of async tasks with:
    - Automatic cancellation on first error (errgroup-style behavior)
    - Parent→child cancellation cascade (context hierarchy)
    - Optional timeout
    - Optional watchdog ticker
    """

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task[Any]] = []
        self._cancel_event: asyncio.Event = asyncio.Event()
        self._timeout: float = 0
        self._watchdog: float = 0
        self._watchdog_event: asyncio.Event | None = None
        self._parent: DispatchGroup | None = None
        self._children: list[DispatchGroup] = []
        self._done: bool = False
        self._error: BaseException | None = None

    @staticmethod
    async def create(
        timeout: float = 0,
        watchdog: float = 0,
        parent: DispatchGroup | None = None,
    ) -> DispatchGroup:
        """Create a new DispatchGroup.

        Args:
            timeout: Timeout in seconds. 0 means infinite (no timeout).
            watchdog: Watchdog period in seconds. 0 means no watchdog.
                      If set, tick() must be called within this period
                      or the group is cancelled with Idle error.
            parent: Parent group. Cancellation cascades from parent to children.
        """
        dg = DispatchGroup()
        dg._timeout = timeout
        dg._watchdog = watchdog
        dg._parent = parent
        if parent is not None:
            parent._children.append(dg)
        if watchdog > 0:
            dg._watchdog_event = asyncio.Event()
        return dg

    async def dispatch(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Launch a coroutine as a managed task.

        If the coroutine raises an exception, all tasks in this group
        and child groups are cancelled.
        """
        task = asyncio.create_task(self._run_task(coro))
        self._tasks.append(task)
        return task

    async def wait(self) -> None:
        """Wait for all dispatched tasks to complete.

        Blocks until all tasks finish, timeout expires, or a task raises
        an exception.

        Raises the first exception from any task (after cancelling siblings).
        """
        all_tasks = list(self._tasks)

        # Add watchdog task if configured
        if self._watchdog > 0 and self._watchdog_event is not None:
            watchdog_task = asyncio.create_task(self._watchdog_loop())
            all_tasks.append(watchdog_task)

        try:
            if self._timeout > 0:
                async with asyncio.timeout(self._timeout):
                    await self._wait_tasks(all_tasks)
            else:
                await self._wait_tasks(all_tasks)
        except TimeoutError:
            self._done = True
            self._error = Expired("dispatch group timeout")
            self._cancel_all()
            raise Expired("dispatch group timeout") from None
        except asyncio.CancelledError:
            self._done = True
            self._error = Done("dispatch group cancelled")
            self._cancel_all()
            raise
        finally:
            self._done = True
            self._cancel_event.set()

    def cancel(self) -> None:
        """Cancel all tasks in this group and all child groups.

        Cancellation propagates through the parent/child hierarchy.
        """
        self._done = True
        self._cancel_all()

    @property
    def is_done(self) -> bool:
        """True if group has been cancelled, timed out, or completed.

        Use this for non-blocking polling of the group state.
        """
        return self._done

    async def done(self) -> None:
        """Block until this group is done (cancelled, timed out, or completed).

        Suspends the caller until the group finishes. For non-blocking
        polling, use the is_done property instead.
        """
        await self._cancel_event.wait()

    @property
    def done_error(self) -> BaseException | None:
        """The error that caused the group to terminate, or None."""
        return self._error

    def tick(self) -> None:
        """Reset the watchdog timer.

        Must be called within the watchdog period or the group is
        cancelled with Idle error.
        """
        if self._watchdog_event is not None:
            self._watchdog_event.set()

    @property
    def has_watchdog(self) -> bool:
        """True if this group has a watchdog configured."""
        return self._watchdog > 0

    async def new_child(
        self, timeout: float = 0, watchdog: float = 0,
    ) -> DispatchGroup:
        """Create a child DispatchGroup.

        Parent cancel cascades to child.
        """
        return await DispatchGroup.create(
            timeout=timeout, watchdog=watchdog, parent=self,
        )

    # --- Internal ---

    async def _run_task(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Run a single task. On exception, cancel all siblings."""
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._error = exc
            self._cancel_all()
            raise

    async def _wait_tasks(self, tasks: list[asyncio.Task[Any]]) -> None:
        """Wait for all tasks, re-raising the first exception."""
        if not tasks:
            return

        # Wait for all tasks, collecting exceptions
        done, pending = await asyncio.wait(tasks)

        # Cancel any remaining pending tasks
        for task in pending:
            task.cancel()

        # Collect and re-raise the first exception
        for task in done:
            if task.cancelled():
                continue
            exc = task.exception()
            if exc is not None:
                raise exc

    async def _watchdog_loop(self) -> None:
        """Watchdog task: cancels the group if tick() is not called in time."""
        assert self._watchdog_event is not None
        while not self._done:
            self._watchdog_event.clear()
            try:
                async with asyncio.timeout(self._watchdog):
                    await self._watchdog_event.wait()
            except TimeoutError:
                self._error = Idle("watchdog timeout")
                self._cancel_all()
                raise Idle("watchdog timeout") from None

    def _cancel_all(self) -> None:
        """Cancel all tasks in this group and all child groups."""
        self._done = True
        self._cancel_event.set()
        for task in self._tasks:
            if not task.done():
                task.cancel()
        for child in self._children:
            child._cancel_all()
