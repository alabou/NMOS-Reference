# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The revision fence: waiting until the local view has caught up to etcd.

A distributed registry validates against its **local** store but commits to
**etcd**, and the local store is updated asynchronously by a watch. Between
those two facts sits every correctness problem in the design: a registration
validated against a store that has not yet seen a Node's parent would be
rejected for a parent that demonstrably exists.

The fence closes that gap. It tracks one number -- the highest revision whose
events have been fully applied locally -- and lets a caller wait until that
number reaches a revision it has read from etcd.

The invariant
-------------
``last_applied`` advances **only after** the store mutation *and* the grain
queueing for that revision are complete. Advancing it earlier would let a
waiter proceed while the change it was waiting for was still half-applied,
which is precisely the bug the fence exists to prevent. See ``advance``.

Seeding, and the deadlock it avoids
-----------------------------------
After a preload at revision ``R`` the watch opens at ``R + 1``, so the fence
must be seeded to ``R`` -- not to zero.

This is not tidiness. etcd answers a ``WatchProgressRequest`` only when the
watcher is synced *and* the store revision has reached the watch's start
revision::

    for _, w := range watchers {
        if _, ok := s.synced.watchers[w]; !ok { return false }
        if rev < w.startRev { return false }
    }

On a quiet cluster -- nothing written since the preload -- the store revision is
still ``R`` while the watch's start revision is ``R + 1``, so a progress request
is silently ignored and *no reply ever arrives*. A fence seeded to zero and
waiting for ``R`` would then block until its deadline on every startup. Seeded
to ``R``, the wait is already satisfied and nothing is asked for.

``nmos/etcd/tests/test_client.py::test_watch_progress_is_withheld_until_the_start_revision_is_reached``
pins that etcd behaviour so this reasoning cannot silently stop being true.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)


class FenceTimeout(Exception):
    """The local view did not catch up within the deadline.

    The caller answers 503: the registry is behind, not broken, and the Node
    should retry rather than treat its registration as rejected.
    """


class RevisionFence:
    """Tracks the highest fully-applied etcd revision, and waits on it.

    Args:
        applied: The revision already reflected in the store. After a preload
            at revision ``R`` this is ``R`` -- see the module docstring for why
            seeding it correctly is what keeps startup from deadlocking.
    """

    __slots__ = ("_applied", "_condition", "_waiters")

    def __init__(self, applied: int = 0) -> None:
        self._applied = applied
        self._condition = asyncio.Condition()
        self._waiters = 0

    @property
    def applied(self) -> int:
        """The highest revision whose events are fully applied locally."""
        return self._applied

    @property
    def waiters(self) -> int:
        """How many callers are currently blocked. Diagnostic only."""
        return self._waiters

    def satisfied(self, revision: int) -> bool:
        """Whether a wait for ``revision`` would return immediately."""
        return self._applied >= revision

    async def advance(self, revision: int) -> None:
        """Record that everything through ``revision`` is applied, and wake waiters.

        Call this *after* the store mutation and the grain queueing for the
        revision, never before. The whole value of the fence is that a waiter
        which returns can rely on the change being visible; advancing early
        turns it into a hint.

        Never moves backwards. A watch reconnect can redeliver a revision at or
        below the applied one, and treating that as regression would either
        crash the watch loop or, worse, let a later wait succeed against a view
        that had gone backwards.
        """
        async with self._condition:
            if revision <= self._applied:
                return
            self._applied = revision
            self._condition.notify_all()

    async def wait(self, revision: int, *, timeout: float) -> None:
        """Block until ``revision`` has been applied locally.

        Returns immediately when the fence is already at or past it, which is
        the common case: most mutations read a revision their own member has
        already seen.

        Raises:
            FenceTimeout: The deadline elapsed first.
        """
        if self._applied >= revision:
            return

        self._waiters += 1
        try:
            async with self._condition:
                # A loop, not a single wait: notify_all wakes every waiter, and
                # a waiter for a higher revision must go back to sleep rather
                # than proceed on someone else's wake-up.
                await asyncio.wait_for(
                    self._condition.wait_for(
                        lambda: self._applied >= revision,
                    ),
                    timeout=timeout,
                )
        except asyncio.TimeoutError as exc:
            raise FenceTimeout(
                f"local view is at revision {self._applied}, still waiting "
                f"for {revision} after {timeout:.1f}s",
            ) from exc
        finally:
            self._waiters -= 1

    async def reset(self, applied: int) -> None:
        """Re-seed after a resnapshot, waking everyone.

        A compaction resync replaces the whole store, so the fence is
        repositioned rather than advanced -- the new snapshot's revision may be
        anywhere relative to the old one. Waiters are woken unconditionally
        because their target may now be unreachable in the ordinary way, and
        letting them time out with a clear message beats leaving them parked
        against a fence that no longer describes the same history.
        """
        async with self._condition:
            self._applied = applied
            self._condition.notify_all()
