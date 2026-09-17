# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Coalescing proposals into one quorum round per event-loop tick.

This is where the throughput claim comes from, and it is a smaller piece of
code than the claim suggests.

The mechanism
-------------
``loop.call_soon`` schedules a callback to run after the current one finishes
but **before** the loop returns to the selector. So every request handler that
was resumed from one ``epoll`` wakeup -- which is every request that arrived in
the same burst -- gets to submit its proposal before the drain runs, and they
all land in one batch.

That gives batching with no timer, no configured window, and no added latency:
the batch closes exactly when there is nothing left to add to it, which is the
earliest moment it could possibly close. A timer-based batcher trades latency
for batch size; this one does not trade anything.

Why the future is created synchronously
---------------------------------------
``submit`` is not a coroutine. It appends and returns a future in one
uninterrupted step, so the caller's interest is registered before anything can
await. If it were async, the entry could commit and apply in the window between
"the operation was accepted" and "the waiter exists" -- and the result would
be delivered to nobody while the caller waited forever for something that had
already happened.

What it deliberately does not do
--------------------------------
No retry, no timeout, no reordering. A batcher that retried would duplicate
operations across terms; a batcher that reordered would break the per-Node
serialisation the ownership design relies on. Both belong to the layer that
knows about terms and leadership, which is ``node.py``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

T = TypeVar("T")
R = TypeVar("R")


@dataclass
class Pending(Generic[T, R]):
    """One submitted operation and the future waiting on its outcome."""

    operation: T
    future: asyncio.Future[R]


class ProposalBatcher(Generic[T, R]):
    """Collects operations within a tick and hands them to ``drain`` as one batch.

    Args:
        drain: Called with the accumulated batch, synchronously, from the
            event loop. It takes ownership of every future in the batch and
            must eventually resolve or fail each one -- nothing here will.
        max_batch: Upper bound on one batch. Reached only under sustained
            load heavier than one quorum round can absorb, where the excess
            simply forms the next batch; it exists so a single
            ``AppendEntries`` cannot grow past the frame cap.
    """

    __slots__ = ("_drain", "_max_batch", "_pending", "_scheduled")

    def __init__(
        self,
        drain: Callable[[list[Pending[T, R]]], None],
        *,
        max_batch: int = 1024,
    ) -> None:
        self._drain = drain
        self._max_batch = max_batch
        self._pending: list[Pending[T, R]] = []
        self._scheduled = False

    @property
    def pending(self) -> int:
        return len(self._pending)

    def submit(self, operation: T) -> asyncio.Future[R]:
        """Queue an operation for the next drain. Returns its future.

        Synchronous and non-awaiting, for the reason in the module docstring.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[R] = loop.create_future()
        self._pending.append(Pending(operation=operation, future=future))

        if len(self._pending) >= self._max_batch:
            # Full: drain now rather than waiting for the tick to end, so a
            # burst larger than the cap becomes several full batches rather
            # than one oversized one.
            self._flush()
        elif not self._scheduled:
            self._scheduled = True
            loop.call_soon(self._flush)
        return future

    def _flush(self) -> None:
        self._scheduled = False
        if not self._pending:
            return
        batch = self._pending
        self._pending = []
        self._drain(batch)

    def fail_all(self, error: BaseException) -> None:
        """Fail every queued proposal. Used on shutdown and on losing leadership.

        Leaves the batcher usable afterwards: a member that loses leadership
        and regains it does not get a new batcher, and one that refused to
        accept anything after a single failure would stop serving until it
        restarted.
        """
        batch = self._pending
        self._pending = []
        self._scheduled = False
        for item in batch:
            if not item.future.done():
                item.future.set_exception(error)
