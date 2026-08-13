# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The namespace watch: the only thing that changes a registry's local store.

Every registry materialises its view from this stream and from nothing else --
including for its own writes. A mutation commits through a transaction and is
then applied *here*, when the watch delivers it. That is what makes locally
originated writes need no special casing and no duplicate suppression, which is
the failure the legacy dRDS spent an origin-index byte on and still got wrong for
deletes (an etcd DELETE event carries no value, so its origin byte was always
read as zero).

Three etcd behaviours this module depends on, each verified rather than assumed:

**Revisions are never split.** etcd's API guarantees say "a list of events is
guaranteed to encompass complete revisions. Updates in the same revision over
multiple keys will not be split over several lists of events." So grouping a
response's events by ``mod_revision`` yields *complete* groups, and the consumer
can apply a whole revision -- a Node delete and all of its descendants -- as one
uninterrupted step. One response may carry several revisions; none carries part
of one.

**Progress replies prove delivery, and are withheld in two cases.** etcd's
``progressIfSync`` declines to answer a ``WatchProgressRequest`` when::

    for _, w := range watchers {
        if _, ok := s.synced.watchers[w]; !ok { return false }   // lagging
        if rev < w.startRev { return false }                     // nothing yet
    }

So a progress response at revision R proves every event up to R has already been
delivered -- exactly the fence guarantee. But there is no reply at all when the
watcher is lagging, *and none when the store revision has not yet reached the
watch's start revision* -- that is, when nothing has happened since the watch
was created.

That second case is load-bearing and is easy to design a deadlock into. After a
preload at revision R the watch opens at ``R + 1``; if no write has happened
since, a progress request is silently ignored, and a fence waiting on R would
block until its deadline. The resolution is initialisation, not a retry:
``last_applied_revision`` must be seeded to ``start_revision - 1`` (the preload
revision), so any fence target at or below R is already satisfied and no
progress reply is needed. Once anything *has* been written, the store revision
is necessarily at or above ``startRev`` and progress is answered normally.

``test_watch_progress_is_withheld_until_the_start_revision_is_reached`` pins
this behaviour so it cannot regress into a hang.

**Compaction is a distinct, non-resumable failure.** It arrives as a cancelled
watch carrying ``compact_revision``. It gets its own exception so the resume
loop cannot mistake it for a dropped connection and silently skip a range of
revisions -- the one bug that would let two registries disagree about state
forever.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, AsyncIterator

from nmos.etcd.channel import EtcdChannelPool, Endpoint, stream_method
from nmos.etcd.errors import EtcdCompacted, EtcdUnavailable, classify
from nmos.etcd.generated import kv_pb2, rpc_pb2
from nmos.etcd.kv import prefix_range_end

if TYPE_CHECKING:
    import grpc

log = logging.getLogger(__name__)

_WATCH = stream_method(
    "Watch", "Watch", rpc_pb2.WatchRequest, rpc_pb2.WatchResponse,
)


@dataclass(frozen=True)
class RevisionBatch:
    """Every event belonging to one store revision, or a progress marker.

    Events are the raw ``mvccpb.Event`` messages rather than copies: the
    generated stubs type them fully, attribute access is C-speed, and a
    subtree delete can carry hundreds of events each holding a complete
    resource body in ``prev_kv``. Copying those into project dataclasses would
    double the allocation on the one path that is already the largest.
    """

    revision: int
    events: tuple[kv_pb2.Event, ...]

    @property
    def progress_only(self) -> bool:
        """True for a progress notification: no events, revision meaningful.

        The consumer may advance its fence to ``revision`` on one of these --
        that is their whole purpose -- but only after everything already
        received has been applied.
        """
        return not self.events


class WatchStream:
    """One watch connection, positioned at a revision.

    Deliberately a *single* connection rather than a self-healing one. The
    caller owns ``last_applied_revision``, so only the caller knows where a
    replacement stream must resume from; a stream that silently reconnected
    itself would resume from wherever it happened to be and could skip
    revisions. Failure is reported, and the backend re-opens at
    ``last_applied_revision + 1``.

    Use as an async context manager, iterate for batches::

        async with watch.open(prefix, start_revision=r + 1) as stream:
            async for batch in stream:
                apply(batch)
    """

    __slots__ = (
        "_call", "_prefix", "_start_revision", "_prev_kv", "_watch_id",
        "_closed",
    )

    def __init__(
        self,
        call: grpc.aio.StreamStreamCall,
        *,
        prefix: bytes,
        start_revision: int,
        prev_kv: bool = False,
    ) -> None:
        self._call = call
        self._prefix = prefix
        self._start_revision = start_revision
        self._prev_kv = prev_kv
        self._watch_id: int | None = None
        self._closed = False

    async def __aenter__(self) -> WatchStream:
        await self._create()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def _create(self) -> None:
        """Send the create request and wait for etcd to confirm it.

        The confirmation is awaited rather than assumed so that a watch which
        cannot be established -- most importantly one whose start revision has
        already been compacted -- fails here, before the backend believes it has
        a live stream and starts trusting an empty event flow as "nothing has
        changed".
        """
        import grpc

        create = rpc_pb2.WatchCreateRequest(
            key=self._prefix,
            range_end=prefix_range_end(self._prefix),
            start_revision=self._start_revision,
            # Off by default, and the registry leaves it off.
            #
            # It looks as though removal grains need it -- a DELETE event
            # carries no value, and Behaviour - Querying.md requires the
            # removal event to carry the resource's final content. They do not:
            # the registry builds that grain from its OWN copy, via
            # ``store.remove_one`` -> ``ResourceEvent.removed(resource)``,
            # which it must have, because a resource it never materialised has
            # nothing to remove and emits no grain either way.
            #
            # Requesting it anyway made etcd fetch and transmit the previous
            # value of every key on every event, for a field no production code
            # path reads. On the BCP-008 update-churn path that is a second
            # full resource body per update. Exposed as a parameter rather than
            # deleted because it is a real etcd feature this client should be
            # able to offer.
            prev_kv=self._prev_kv,
            # etcd splits oversized responses instead of failing them. A Node
            # delete cascades to every descendant in ONE revision, each event
            # carrying a full resource body in prev_kv, so the response can be
            # large; without this it would arrive as RESOURCE_EXHAUSTED.
            fragment=True,
            # Periodic progress even when idle, so a fence waiter is not the
            # only thing that can advance the applied revision and a dead
            # connection is distinguishable from a quiet one.
            progress_notify=True,
        )
        try:
            await self._call.write(rpc_pb2.WatchRequest(create_request=create))
            response = await self._call.read()
        except grpc.aio.AioRpcError as exc:
            # Without this the raw gRPC error escapes and the caller's
            # `except EtcdError` never matches, so a dead member looks like an
            # unexpected bug rather than the ordinary reconnect it is.
            raise classify(exc) from exc
        if response is grpc.aio.EOF or not isinstance(
            response, rpc_pb2.WatchResponse,
        ):
            raise EtcdUnavailable("watch stream closed before confirmation")
        self._raise_if_cancelled(response)
        if not response.created:
            raise EtcdUnavailable(
                "watch stream did not confirm creation",
            )
        self._watch_id = response.watch_id

    def _raise_if_cancelled(self, response: rpc_pb2.WatchResponse) -> None:
        """Turn a cancellation into the right exception.

        Compaction is separated from every other cancellation because it is the
        only one that cannot be fixed by reconnecting at the same revision --
        the history is gone, and the only recovery is a fresh snapshot.
        """
        if not response.canceled:
            return
        if response.compact_revision > 0:
            raise EtcdCompacted(
                f"watch cancelled: history compacted to revision "
                f"{response.compact_revision}",
                compact_revision=response.compact_revision,
            )
        raise EtcdUnavailable(
            f"watch cancelled: {response.cancel_reason or 'no reason given'}",
        )

    async def request_progress(self) -> None:
        """Ask etcd to emit a progress notification on this stream.

        Answered only when the watcher is synced, which is precisely what makes
        the reply meaningful: receiving progress at revision R proves everything
        through R has been delivered. A lagging watcher gets no reply at all, so
        a caller must never block on this alone.
        """
        if self._closed:
            raise EtcdUnavailable("watch stream is closed")
        import grpc

        try:
            await self._call.write(
                rpc_pb2.WatchRequest(
                    progress_request=rpc_pb2.WatchProgressRequest(),
                ),
            )
        except grpc.aio.AioRpcError as exc:
            raise classify(exc) from exc

    async def __aiter__(self) -> AsyncIterator[RevisionBatch]:
        """Yield one batch per revision, in order, plus progress markers."""
        import grpc

        # Fragmented responses are reassembled here: etcd sets fragment=True on
        # every piece but the last, and all pieces share one revision. Yielding
        # a fragment on its own would hand the consumer a partial revision --
        # the exact thing the completeness guarantee otherwise rules out.
        pending: list[kv_pb2.Event] = []

        while True:
            try:
                response = await self._call.read()
            except grpc.aio.AioRpcError as exc:
                raise classify(exc) from exc
            if response is grpc.aio.EOF:
                if pending:
                    raise EtcdUnavailable(
                        "watch stream ended mid-fragment; "
                        "a revision was delivered incompletely",
                    )
                return
            if not isinstance(response, rpc_pb2.WatchResponse):
                raise EtcdUnavailable(
                    f"watch stream produced {type(response).__name__}",
                )

            self._raise_if_cancelled(response)
            if response.created:
                continue

            pending.extend(response.events)
            if response.fragment:
                continue

            events = pending
            pending = []

            if not events:
                # Progress notification: no events, but the revision is
                # authoritative and lets a fence advance during quiet periods.
                yield RevisionBatch(
                    revision=response.header.revision, events=(),
                )
                continue

            for batch in _group_by_revision(events):
                yield batch

    async def close(self) -> None:
        """Cancel the stream. Safe to call more than once."""
        if self._closed:
            return
        self._closed = True
        self._call.cancel()


def _group_by_revision(
    events: list[kv_pb2.Event],
) -> list[RevisionBatch]:
    """Split a response's events into one batch per revision, in order.

    Safe because etcd never splits a revision across responses, so every group
    formed here is complete. Events arrive ordered by revision, so a single
    pass suffices and the grouping preserves etcd's ordering -- which for a
    transaction is the order of its operations, the property the registry
    relies on to apply parents before children.
    """
    batches: list[RevisionBatch] = []
    current: list[kv_pb2.Event] = []
    revision = 0

    for event in events:
        # Both PUT and DELETE record the revision that produced them in
        # kv.mod_revision; for a delete the kv carries no value and the old
        # content is in prev_kv.
        event_revision = event.kv.mod_revision
        if current and event_revision != revision:
            batches.append(
                RevisionBatch(revision=revision, events=tuple(current)),
            )
            current = []
        revision = event_revision
        current.append(event)

    if current:
        batches.append(RevisionBatch(revision=revision, events=tuple(current)))
    return batches


class EtcdWatch:
    """Opens watch streams against a chosen member."""

    __slots__ = ("_pool",)

    def __init__(self, pool: EtcdChannelPool) -> None:
        self._pool = pool

    def open(
        self,
        prefix: bytes,
        *,
        start_revision: int,
        endpoint: Endpoint | None = None,
        prev_kv: bool = False,
    ) -> WatchStream:
        """Create a watch over ``prefix`` starting at ``start_revision``.

        Args:
            start_revision: The first revision to receive. The backend passes
                ``last_applied_revision + 1``; passing the applied revision
                itself would redeliver events it has already applied.
            endpoint: Which member to watch. Defaults to the first -- the local
                member when one is configured -- because a watch is a long-lived
                stream and keeping it on the co-located member avoids a network
                hop for every change in the cluster.
            prev_kv: Ask etcd to attach each key's previous value to its event.
                Off by default because it doubles the payload of every update
                and delete; see the note at the create request.
        """
        target = endpoint or self._pool.endpoints[0]
        return WatchStream(
            self._pool.open_stream(_WATCH, target),
            prefix=prefix,
            start_revision=start_revision,
            prev_kv=prev_kv,
        )
