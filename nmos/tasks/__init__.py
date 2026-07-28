# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Structured concurrency for NMOS via a DispatchGroup wrapper.

Provides a DispatchGroup class that wraps asyncio.TaskGroup with a stable
lifecycle API:

    dg = await DispatchGroup.create()
    await dg.dispatch(some_coro())
    await dg.wait()
    dg.cancel()          # request termination, return immediately
    await dg.close()     # request termination AND wait for it to complete
    await dg.done()      # blocking wait until finished
    dg.is_done           # non-blocking poll

All standard asyncio primitives (Queue, sleep, Lock, etc.) work freely
inside dispatched tasks. The wrapper only standardizes lifecycle management.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from nmos.errors import Done, Expired, Idle, InvalidOperation, is_recoverable


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
        # The watchdog runs as a task of its own. It is kept here, not in
        # _tasks, because it is infrastructure rather than caller work: it must
        # never make wait() look busy, but cancel()/close() must still reach it.
        self._watchdog_task: asyncio.Task[None] | None = None
        self._parent: DispatchGroup | None = None
        self._children: list[DispatchGroup] = []
        self._done: bool = False
        self._closed: bool = False
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

        Raises:
            InvalidOperation: the group has been closed. A closed group is
                finished for good -- its tasks have been cancelled and joined,
                so a task dispatched into it would never be supervised or
                cancelled by anyone. Create a new group instead.
        """
        if self._closed:
            raise InvalidOperation("cannot dispatch into a closed dispatch group")
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

        # Add watchdog task if configured. Held on self as well as in the local
        # list so that cancel() and close() can stop it -- otherwise it would
        # outlive the group it is supposed to be watching.
        if self._watchdog > 0 and self._watchdog_event is not None:
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())
            all_tasks.append(self._watchdog_task)

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

    async def close(self) -> None:
        """Cancel this group and wait until its tasks have actually finished.

        ``cancel()`` only *requests* termination: it returns immediately, while
        the tasks are still suspended at whatever ``await`` they were on. They
        do not stop until the event loop resumes them. ``close()`` is the
        complete shutdown -- it cancels, then waits for every task in this
        group and in all child groups to finish, then detaches the group from
        its parent so it can be discarded.

        Use ``close()`` whenever the group must be gone before the caller
        continues: tearing down a server, replacing one group with another, or
        a test that must not leak a running task into the next test. Use
        ``cancel()`` only where the caller genuinely cannot wait -- a signal
        handler, for instance, which must not block the loop.

        Closing an already-closed group does nothing, so it is safe to call
        from a ``finally`` that may run twice. A closed group cannot be reused:
        ``dispatch()`` and ``new_child()`` on it raise ``InvalidOperation``.

        Safe to call from inside one of the group's own tasks -- that task is
        skipped when joining, because a task cannot wait for itself to finish.

        Raises:
            The first unrecoverable exception raised by a task in this group.
            Cancellation, timeout and watchdog errors are the expected
            outcomes of closing and are not re-raised; read ``done_error`` if
            the reason matters.
        """
        if self._closed:
            return
        self._closed = True

        self._cancel_all()

        # Children first: this group is not finished while a child still has a
        # task running. Iterate a copy -- each child detaches itself as it goes.
        for child in list(self._children):
            await child.close()

        # A task cannot await itself, so drop the caller's own task from the
        # join set. Its cancellation has still been requested above.
        current = asyncio.current_task()
        pending = [task for task in self._tasks if task is not current]
        if self._watchdog_task is not None and self._watchdog_task is not current:
            pending.append(self._watchdog_task)
        self._tasks = []
        self._watchdog_task = None

        # return_exceptions keeps one failing task from hiding the rest; the
        # failure has already been recorded in _error by _run_task.
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        # Detach from the parent. Without this a long-lived parent keeps every
        # group ever created under it, and its cancel walk keeps revisiting
        # groups that finished long ago.
        parent = self._parent
        if parent is not None:
            if self in parent._children:
                parent._children.remove(self)
            self._parent = None

        # Cancelling is what close() asked for, so a cancellation error is not
        # a failure. Anything else is the caller's problem and must surface.
        error = self._error
        if error is not None and not is_recoverable(error):
            raise error

    @property
    def is_done(self) -> bool:
        """True if group has been cancelled, timed out, or completed.

        Use this for non-blocking polling of the group state.
        """
        return self._done

    @property
    def is_closed(self) -> bool:
        """True if close() has completed on this group.

        A closed group is finished for good and cannot accept new work.
        """
        return self._closed

    @property
    def child_count(self) -> int:
        """Number of child groups still attached to this group.

        A group that keeps creating children should see this return to zero as
        they are closed; a number that only ever grows means children are being
        abandoned rather than closed.
        """
        return len(self._children)

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

        Raises:
            InvalidOperation: the parent has been closed, so nothing would ever
                cancel the child -- it would outlive the group meant to own it.
        """
        if self._closed:
            raise InvalidOperation("cannot create a child of a closed dispatch group")
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
        # The watchdog is not in _tasks but must stop with the group.
        if self._watchdog_task is not None and not self._watchdog_task.done():
            self._watchdog_task.cancel()
        for child in self._children:
            child._cancel_all()
