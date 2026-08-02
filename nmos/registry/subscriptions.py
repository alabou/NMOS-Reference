# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Query API subscriptions and WebSocket grain generation.

Implements ``Behaviour - Querying.md`` — subscription lifecycle, the four
event shapes, the initial synchronisation burst, filtered subscriptions, and
``max_update_rate_ms`` coalescing.

Event classification
--------------------
``Behaviour - Querying.md:85-210`` defines the four shapes by which of ``pre``
and ``post`` are present: ``post`` only is an add, ``pre`` only is a remove,
both-and-different is a modify, both-and-identical is a sync.

Filtered subscriptions add a requirement that looks like it needs bookkeeping
but does not (``:242-245``):

    Subscriptions MUST inform clients when resources begin to match, or no
    longer match, the given query parameters. [...] If a Flow has a tag
    removed causing it to no longer match [...] the client MUST be issued a
    'Resource Removed Event' as if this resource had been deleted.

Evaluating the subscription's filter against ``pre`` and against ``post``
independently produces all of it from one rule:

======================  ======================  =================
``pre`` matches         ``post`` matches        Event emitted
======================  ======================  =================
no                      yes                     added
yes                     yes                     modified
yes                     no                      removed
no                      no                      nothing
======================  ======================  =================

A genuine add has no ``pre`` and a genuine delete has no ``post``, so those
fall on the same table without a special case, and no per-subscription "which
resources currently match" set has to be maintained or kept consistent. The
AMWA mock implements no filter transitions at all.

Grains
------
One grain per connected WebSocket, matching nmos-cpp's model where a grain is
itself a resource (which is what the ``grains`` counter in the status line
counts). Each connection owns its pending-event buffer, so a slow client
cannot delay delivery to a fast one, and the initial sync burst goes to the
connecting client alone rather than being broadcast — the AMWA mock re-syncs
every connected client whenever any new one arrives.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from nmos.json.engine import JsonEngine
from nmos.json.types import NTime
from nmos.registry.query_filter import matches
from nmos.registry.types import (
    EventKind,
    RegisteredResource,
    ResourceEvent,
    ResourceType,
    TaiCursor,
)

if TYPE_CHECKING:
    from nmos.registry.registry import Registry

log = logging.getLogger(__name__)

# ``Behaviour - Querying.md:70`` -- the grain type URN for registry events.
GRAIN_TYPE = "urn:x-nmos:format:data.event"
GRAIN_TYPE_EVENT = "event"

# ``:47`` -- "The rate and duration attributes MAY be ignored by clients as
# the messages being exchanged represent events in the registry and do not
# adhere to a defined rate or duration." The example at :61-68 uses 0/1.
GRAIN_RATE = (0, 1)


class SubscriptionError(Exception):
    """A subscription request was invalid. The caller answers 400."""


@dataclass
class Subscription:
    """One entry in ``/subscriptions``.

    Fields mirror ``queryapi-subscription-response.json``. ``resource_type``
    is derived from ``resource_path`` at construction so every event does not
    have to re-parse it.
    """

    id: str
    ws_href: str
    resource_path: str
    resource_type: ResourceType
    params: dict[str, str]
    max_update_rate_ms: int
    persist: bool
    secure: bool
    authorization: bool
    created: TaiCursor

    #: Host header the subscription was created for. Part of the match key --
    #: ``ws_href`` is host-derived, so two clients reaching the registry by
    #: different names must not be handed each other's WebSocket URL.
    host: str = ""

    def to_json(self) -> dict[str, Any]:
        """Render as ``queryapi-subscription-response.json``."""
        return {
            "id": self.id,
            "ws_href": self.ws_href,
            "max_update_rate_ms": self.max_update_rate_ms,
            "persist": self.persist,
            "secure": self.secure,
            "authorization": self.authorization,
            "resource_path": self.resource_path,
            "params": dict(self.params),
        }

    def filters(self) -> list[tuple[str, str]]:
        """The subscription's filters, in basic-query form.

        ``:214`` -- "Query parameters are specified in a params attribute
        rather than the query string", but they mean the same thing, so the
        same matcher serves both.
        """
        return list(self.params.items())

    def matches(self, raw: Mapping[str, Any] | None) -> bool:
        """Does a resource representation satisfy this subscription?"""
        if raw is None:
            return False
        return matches(raw, self.filters())


@dataclass
class _PendingEvent:
    """One coalesced change awaiting delivery to a connection.

    Coalescing keeps the first ``pre`` and the latest ``post`` for a given
    resource, so a client rate-limited to one grain per window sees the net
    change over that window rather than a replay of every intermediate state.
    """

    path: str
    pre: dict[str, Any] | None
    post: dict[str, Any] | None

    def merge(self, newer: _PendingEvent) -> None:
        # `pre` is the state before the FIRST change in this window, so it is
        # never overwritten; `post` is the state after the LAST one.
        self.post = newer.post


class SubscriptionConnection:
    """One connected WebSocket, and the grain buffer feeding it.

    This is the "grain" of nmos-cpp's resource model: it exists for as long as
    the socket does and holds that client's pending events.
    """

    __slots__ = ("subscription", "_pending", "_wake", "_closed", "_shutdown")

    def __init__(self, subscription: Subscription) -> None:
        self.subscription = subscription
        self._pending: dict[str, _PendingEvent] = {}
        self._wake = asyncio.Event()
        self._closed = False
        # Distinct from ``_wake``: that one means "there may be work", this
        # one means "this connection is finished". The WebSocket handler waits
        # on it so a server-side close -- DELETE of a persistent subscription
        # (``:19``: connected clients "SHOULD be forcibly closed by the
        # server") -- tears the socket down. Without it the handler would sit
        # in its reader loop until the *client* chose to disconnect, and a
        # deleted subscription would keep serving.
        self._shutdown = asyncio.Event()

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        self._closed = True
        self._wake.set()
        self._shutdown.set()

    async def wait_closed(self) -> None:
        """Block until this connection is closed from the server side."""
        await self._shutdown.wait()

    def enqueue(self, event: _PendingEvent) -> None:
        """Buffer one event, coalescing with any pending change to the same id."""
        existing = self._pending.get(event.path)
        if existing is None:
            self._pending[event.path] = event
        else:
            existing.merge(event)
        self._wake.set()

    def enqueue_all(self, events: Iterable[_PendingEvent]) -> None:
        for event in events:
            self.enqueue(event)

    def drain(self) -> list[_PendingEvent]:
        """Take everything buffered, leaving the buffer empty."""
        drained = list(self._pending.values())
        self._pending.clear()
        self._wake.clear()
        return drained

    async def wait(self) -> None:
        """Block until there is something to send, or the socket closed."""
        await self._wake.wait()


class SubscriptionManager:
    """Owns every subscription and every connection.

    Args:
        registry: Used to read current resource state for the sync burst and
            to stamp grains with the Query API instance id.
    """

    __slots__ = ("_registry", "_subscriptions", "_connections")

    def __init__(self, registry: Registry) -> None:
        self._registry = registry
        self._subscriptions: dict[str, Subscription] = {}
        self._connections: dict[str, list[SubscriptionConnection]] = {}

    # -----------------------------------------------------------------------
    # Introspection
    # -----------------------------------------------------------------------

    def count(self) -> int:
        return len(self._subscriptions)

    def grain_count(self) -> int:
        """Live grains -- one per connected WebSocket, as in nmos-cpp."""
        return sum(len(conns) for conns in self._connections.values())

    def get(self, subscription_id: str) -> Subscription | None:
        return self._subscriptions.get(subscription_id)

    def all(self) -> list[Subscription]:
        return list(self._subscriptions.values())

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def create_or_match(
        self,
        *,
        resource_path: str,
        params: dict[str, str],
        max_update_rate_ms: int,
        persist: bool,
        secure: bool,
        authorization: bool,
        host: str,
        ws_scheme: str,
        ws_host: str,
    ) -> tuple[Subscription, bool]:
        """Return an existing matching subscription, or create a new one.

        ``:25`` -- "Upon receiving a request for a new Subscription, the Query
        API MAY return an existing Subscription to the user, if it matches the
        requested attributes. If a relevant Subscription does not exist, a new
        one SHOULD be created." Reusing is what keeps a Controller that opens
        one subscription per resource kind from accumulating duplicates across
        reconnects.

        Returns:
            ``(subscription, created)`` -- ``created`` selects 201 over 200.
        """
        resource_type = _resource_type_of(resource_path)

        for existing in self._subscriptions.values():
            if (
                existing.resource_path == resource_path
                and existing.params == params
                and existing.max_update_rate_ms == max_update_rate_ms
                and existing.persist == persist
                and existing.secure == secure
                and existing.authorization == authorization
                and existing.host == host
            ):
                return existing, False

        subscription_id = str(uuid.uuid4())
        subscription = Subscription(
            id=subscription_id,
            ws_href=(
                f"{ws_scheme}://{ws_host}"
                f"/x-nmos/query/v1.3/subscriptions/{subscription_id}"
            ),
            resource_path=resource_path,
            resource_type=resource_type,
            params=params,
            max_update_rate_ms=max_update_rate_ms,
            persist=persist,
            secure=secure,
            authorization=authorization,
            created=TaiCursor.now(),
            host=host,
        )
        self._subscriptions[subscription_id] = subscription
        self._connections[subscription_id] = []
        log.info(
            "registry: created subscription %s for %s (persist=%s)",
            subscription_id, resource_path, persist,
        )
        return subscription, True

    def delete(self, subscription_id: str) -> None:
        """Remove a persistent subscription and close its clients.

        ``:19`` -- "If an HTTP DELETE is issued prior to all WebSocket
        connections being closed, they SHOULD be forcibly closed by the
        server."

        The caller is responsible for having rejected a non-persistent
        subscription with 403 first (``:18``); this method does not re-check,
        because garbage collection of a non-persistent subscription goes
        through the same path legitimately.
        """
        self._subscriptions.pop(subscription_id, None)
        for connection in self._connections.pop(subscription_id, []):
            connection.close()

    # -----------------------------------------------------------------------
    # Connections
    # -----------------------------------------------------------------------

    def connect(self, subscription: Subscription) -> SubscriptionConnection:
        """Attach a WebSocket and queue its synchronisation burst.

        ``:166`` -- the sync events carry identical ``pre`` and ``post`` and
        exist so "the client has received all data for a given topic".

        The burst is queued on this connection only. If nothing currently
        matches, nothing is queued: an empty grain would violate
        ``queryapi-subscriptions-websocket.json``, whose ``data`` array has
        ``minItems: 1``. The AMWA mock sends the empty grain anyway, and
        broadcasts it to every client rather than the new one.
        """
        connection = SubscriptionConnection(subscription)
        self._connections.setdefault(subscription.id, []).append(connection)

        sync = [
            _PendingEvent(path=resource.id, pre=resource.raw, post=resource.raw)
            for resource in self._matching_resources(subscription)
        ]
        if sync:
            connection.enqueue_all(sync)
        return connection

    def disconnect(self, connection: SubscriptionConnection) -> None:
        """Detach a WebSocket, reaping the subscription if it was transient.

        ``:18`` -- "The Query API MAY remove any Subscriptions with persist
        set to false that no longer have WebSocket connections."
        """
        connection.close()
        subscription = connection.subscription
        connections = self._connections.get(subscription.id)
        if connections is None:
            return
        if connection in connections:
            connections.remove(connection)

        if not connections and not subscription.persist:
            log.info(
                "registry: reaping non-persistent subscription %s",
                subscription.id,
            )
            self._subscriptions.pop(subscription.id, None)
            self._connections.pop(subscription.id, None)

    def _matching_resources(
        self, subscription: Subscription,
    ) -> list[RegisteredResource]:
        store = self._registry.store
        return [
            resource
            for resource in store.iter_extant(subscription.resource_type)
            if subscription.matches(resource.raw)
        ]

    # -----------------------------------------------------------------------
    # Publication
    # -----------------------------------------------------------------------

    def publish(self, events: Iterable[ResourceEvent]) -> None:
        """Route resource changes to every interested connection.

        Synchronous and non-awaiting by design: the caller has just mutated
        the store, and queueing happens in the same uninterrupted step so no
        coroutine can observe a change whose grain has not been buffered.
        Delivery itself is asynchronous and rate-limited.
        """
        for event in events:
            for subscription in self._subscriptions.values():
                if subscription.resource_type is not event.resource_type:
                    continue
                pending = self._classify(subscription, event)
                if pending is None:
                    continue
                for connection in self._connections.get(subscription.id, []):
                    connection.enqueue(pending)

    def _classify(
        self, subscription: Subscription, event: ResourceEvent,
    ) -> _PendingEvent | None:
        """Decide what this subscription should see for one change.

        Implements the four-row table in the module docstring. Filter
        transitions (``:242-245``) need no extra state: a resource that stops
        matching has ``pre`` matching and ``post`` not, which is exactly the
        removed row.
        """
        pre_matches = subscription.matches(event.pre)
        post_matches = subscription.matches(event.post)

        if post_matches and pre_matches:
            return _PendingEvent(event.resource_id, event.pre, event.post)
        if post_matches:
            # Either a genuine add, or a resource that has just begun to match
            # -- both are reported as a Resource Added Event.
            return _PendingEvent(event.resource_id, None, event.post)
        if pre_matches:
            # Either a genuine delete, or a resource that has stopped
            # matching -- both are reported as a Resource Removed Event.
            return _PendingEvent(event.resource_id, event.pre, None)
        return None

    # -----------------------------------------------------------------------
    # Grain construction
    # -----------------------------------------------------------------------

    def build_grain(
        self, subscription: Subscription, events: list[_PendingEvent],
    ) -> str:
        """Encode pending events as one WebSocket grain message.

        Built from the generated ``NQueryPayloadGeneric`` family, so the
        envelope is type-checked while ``pre``/``post`` carry the stored raw
        JSON verbatim — see ``nmos/codegen/definitions/is04_types.py`` for why
        the resource-agnostic grain family exists.
        """
        from nmos.types.generated.narray_of_query_web_socket_grain_data_generic import (
            NArrayOfQueryWebSocketGrainDataGenericValue,
        )
        from nmos.types.generated.nquery_payload_generic import (
            NQueryPayloadGenericValue,
        )
        from nmos.types.generated.nquery_web_socket_grain_data_generic import (
            NQueryWebSocketGrainDataGeneric,
        )
        from nmos.types.generated.nquery_web_socket_grain_generic import (
            NQueryWebSocketGrainGenericValue,
        )
        from nmos.types.generated.nrational import NRationalValue

        # The array holds the OUTER wrapper type, not the inner ...Value.
        # Members reached through the wrapper are set on ``.value``.
        entries = []
        for event in events:
            entry = NQueryWebSocketGrainDataGeneric()
            entry.set_to_default()
            entry.value.Path.value = event.path
            # Only the members that apply are set: an undefined member is
            # omitted from the encoding entirely, which is how the presence
            # or absence of pre/post carries the event type.
            if event.pre is not None:
                entry.value.Pre.value = event.pre
            if event.post is not None:
                entry.value.Post.value = event.post
            entries.append(entry)

        data = NArrayOfQueryWebSocketGrainDataGenericValue()
        data.set(entries)

        grain = NQueryWebSocketGrainGenericValue()
        grain.Type.value = GRAIN_TYPE
        grain.Topic.value = subscription.resource_type.topic
        grain.Data.value = data.get()

        payload = NQueryPayloadGenericValue()
        payload.GrainType.value = GRAIN_TYPE_EVENT
        # ``:37`` -- source_id identifies the Query API instance; flow_id is
        # the id of the subscription under /subscriptions.
        payload.SourceId.value = self._registry.query_id
        payload.FlowId.value = subscription.id

        # ``:39-45`` -- the three timestamps MAY be identical. They are here:
        # a registry event has no capture time distinct from its creation
        # time, so inventing a difference would be fiction.
        now = TaiCursor.now()
        _set_tai(payload.OriginTimestamp, now)
        _set_tai(payload.SyncTimestamp, now)
        _set_tai(payload.CreationTimestamp, now)

        # Object-typed members are assigned with set_value(), which clones
        # the argument -- the value-semantics rule the generated types follow
        # so a later mutation of ``grain`` cannot reach back into the payload.
        payload.Rate.set_value(_rational(NRationalValue, *GRAIN_RATE))
        payload.Duration.set_value(_rational(NRationalValue, *GRAIN_RATE))
        payload.Grain.set_value(grain)

        return JsonEngine().encode(payload)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resource_type_of(resource_path: str) -> ResourceType:
    """Map a subscription's ``resource_path`` to a resource type.

    Raises:
        SubscriptionError: The path is not one of the six collections. The
            caller turns this into a 400 — ``QueryAPI.raml:432`` covers a
            request that is "incorrectly formatted [or] an attribute is
            invalid given the API's configuration".
    """
    resource_type = ResourceType.from_plural(resource_path.strip("/"))
    if resource_type is None:
        permitted = ", ".join(f"/{rt.plural}" for rt in ResourceType)
        raise SubscriptionError(
            f"resource_path {resource_path!r} is not subscribable; "
            f"expected one of: {permitted}",
        )
    return resource_type


def _set_tai(field_: NTime, cursor: TaiCursor) -> None:
    """Set an NTime from a TAI cursor.

    ``NTime`` stores UTC internally and adds the TAI offset when encoding, so
    a TAI value must have the offset removed on the way in. Assigning the TAI
    seconds directly would put every grain timestamp 37 seconds into the
    future.
    """
    field_.value = (cursor.seconds - NTime.TAI_UTC_OFFSET, cursor.nanoseconds)


def _rational(factory: Any, numerator: int, denominator: int) -> Any:
    """Build an ``NRationalValue``."""
    rational = factory()
    rational.Numerator.value = numerator
    rational.Denominator.value = denominator
    return rational
