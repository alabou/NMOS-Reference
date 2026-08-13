# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""In-memory resource store backing both the Registration and Query APIs.

This module owns every piece of registry state that outlives a single request:
the resources themselves, the parent/child graph, the registry-assigned paging
cursors, per-resource health, and the two-stage deletion lifecycle.

Design notes worth reading before changing anything here
--------------------------------------------------------

**The store validates; the handlers translate.** All five of the 400-yielding
conditions in ``Behaviour - Registration.md:98-104`` are decided here and
returned as a ``RegistrationError``. Handlers map those to HTTP. Keeping the
decisions in one place is what stops the "which layer checks referential
integrity?" question from having two answers.

**Health is inherited, not per-resource.** Only Nodes heartbeat, but
``heartbeat()`` refreshes the Node *and every descendant*, exactly as
nmos-cpp's ``set_resource_health`` does. Garbage collection then simply
expires anything whose health has fallen behind, and the cascade falls out for
free. See ``RegisteredResource.health`` for why the recursion is load-bearing.

**Deletion is two-stage.** ``delete()`` and GC mark resources *non-extant*
rather than dropping them; ``_forget()`` drops them once the forget interval
has elapsed. The intermediate state is what lets a removal grain carry the
resource's final content and keeps paging cursors monotonic across a delete.

**No locking.** This is single-threaded asyncio. Every public method here
completes without awaiting, so no other coroutine can observe a half-applied
mutation. That invariant is the reason there are no locks — preserve it: if
you ever need to await inside one of these methods, the concurrency model
changes and callers that assume atomicity will break.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterator

from nmos.node.types import utc_to_tai
from nmos.registry.paging import ORDER_CREATE, ORDER_UPDATE
from nmos.registry.types import (
    PARENT_KEY,
    PARENT_TYPE,
    PreparedRegistration,
    RegisteredResource,
    RegistrationError,
    RegistrationResult,
    RegistryStatistics,
    ResourceEvent,
    ResourceType,
    TaiCursor,
)

log = logging.getLogger(__name__)


def health_now() -> int:
    """Current time in whole TAI seconds — the unit health is measured in.

    Health deliberately has one-second resolution: the heartbeat interval is
    5 s and the collection interval 12 s (``Behaviour - Registration.md:45,
    47``), so sub-second precision would carry no information, and integer
    seconds is also what the ``health`` string on the wire must be
    (``registrationapi-health-response.json``: ``^[0-9]+$``).
    """
    seconds, _ = utc_to_tai(time.time())
    return seconds


class RegistryStore:
    """The registry's resource database.

    Args:
        gc_interval: Seconds of heartbeat silence after which a Node and its
            sub-resources are collected. Defaults to the 12 s of
            ``Behaviour - Registration.md:47``.
        forget_interval: Seconds a non-extant resource is retained before
            being dropped entirely.
    """

    __slots__ = (
        "_by_type",
        "_type_of",
        "_children",
        "_last_cursor",
        "_gc_interval",
        "_forget_interval",
        "_order",
        "_order_dirty",
    )

    def __init__(
        self,
        *,
        gc_interval: float = 12.0,
        forget_interval: float = 60.0,
    ) -> None:
        # Resources bucketed by type. Both extant and non-extant records live
        # here; ``extant`` on the record is the discriminator. Query paths
        # must filter on it — see ``iter_extant``.
        self._by_type: dict[ResourceType, dict[str, RegisteredResource]] = {
            rt: {} for rt in ResourceType
        }
        # id -> type, so an id collision across types can be detected without
        # scanning all six buckets (``Behaviour - Registration.md:101``).
        self._type_of: dict[str, ResourceType] = {}
        # parent id -> child ids. Maintained alongside the resources so
        # cascade delete and recursive health are O(subtree) rather than
        # O(registry).
        self._children: dict[str, set[str]] = {}
        # Last cursor handed out per type, to enforce the "no duplicate
        # creation/update timestamps within a type" rule of
        # ``APIs - Query Parameters.md:17``.
        self._last_cursor: dict[ResourceType, TaiCursor] = {}
        # Resource ids per (type, order), held in ascending cursor order.
        #
        # Python dicts preserve insertion order, and cursors are allocated
        # strictly increasing per type, so "append on create, move to end on
        # update" keeps these sorted for O(1) per mutation -- which is what
        # lets a Query page without sorting the whole collection first.
        self._order: dict[
            tuple[ResourceType, str], dict[str, None]
        ] = {}
        # Set when a cursor arrives OUT of order, which happens whenever the
        # cursors are not ours to choose: the distributed backend applies a
        # preload in key order, not cursor order. Rather than sort on every
        # mutation, the index is re-sorted lazily on the next read and the flag
        # cleared -- so a preload costs one sort in total, not one per resource.
        self._order_dirty: dict[tuple[ResourceType, str], bool] = {}
        self._gc_interval = gc_interval
        self._forget_interval = forget_interval

    # -----------------------------------------------------------------------
    # Cursor-ordered indexes
    # -----------------------------------------------------------------------

    def _index(self, resource_type: ResourceType, order: str) -> dict[str, None]:
        return self._order.setdefault((resource_type, order), {})

    def _touch_order(
        self,
        resource_type: ResourceType,
        order: str,
        resource_id: str,
        cursor: TaiCursor,
    ) -> None:
        """Move a resource to the end of one index, or mark the index dirty.

        The end is the correct place only while the total order keeps
        increasing. Two things can break that, and both must be caught here or
        the index silently disagrees with the sort it stands in for:

        * a cursor arriving *lower* than the tail's -- a preload applying
          resources in key order rather than cursor order, since there the
          cursors come from etcd and are not ours to choose;
        * a cursor arriving *equal* to the tail's. The order is ``(cursor,
          id)``, not the cursor alone, precisely so that colliding cursors
          order identically on every cluster member; appending on a tie would
          reintroduce the local-insertion-order dependency that tie-break
          exists to remove.

        Either way the index is flagged for a single lazy re-sort rather than
        sorted on the spot.
        """
        index = self._index(resource_type, order)
        key = (resource_type, order)

        # Removed BEFORE the tail is inspected, not after. If this resource is
        # already the tail, comparing it against itself would compare the new
        # cursor with the new cursor and always look ordered -- so a cursor
        # that moved *backwards* on the tail element (a revive with an
        # etcd-supplied cursor lower than the one it replaces) would be
        # appended right back where it was and silently mis-order the index.
        index.pop(resource_id, None)

        if not self._order_dirty.get(key, False) and index:
            last_id = next(reversed(index))
            last = self._by_type[resource_type].get(last_id)
            if last is not None and (cursor, resource_id) < (
                self._cursor_of(last, order), last_id,
            ):
                self._order_dirty[key] = True

        index[resource_id] = None

    @staticmethod
    def _cursor_of(resource: RegisteredResource, order: str) -> TaiCursor:
        return resource.created if order == ORDER_CREATE else resource.updated

    def _drop_from_order(
        self, resource_type: ResourceType, resource_id: str,
    ) -> None:
        for order in (ORDER_CREATE, ORDER_UPDATE):
            self._order.get((resource_type, order), {}).pop(resource_id, None)

    def iter_ordered(
        self, resource_type: ResourceType, order: str,
    ) -> Iterator[RegisteredResource]:
        """Extant resources of one type, ascending by the ``order`` cursor.

        This is what makes a Query page without sorting: the caller filters
        this stream, and a filtered subsequence of a sorted sequence is still
        sorted. Ties break on resource id, exactly as an explicit sort would,
        so two registries that received the same resources in different orders
        still page identically.

        Like ``iter_extant``, this iterates live state -- a caller that mutates
        the store mid-iteration must wrap it in ``list()`` first.
        """
        key = (resource_type, order)
        index = self._index(resource_type, order)

        if self._order_dirty.get(key, False):
            bucket = self._by_type[resource_type]
            ordered = sorted(
                (rid for rid in index if rid in bucket),
                key=lambda rid: (self._cursor_of(bucket[rid], order), rid),
            )
            self._order[key] = {rid: None for rid in ordered}
            self._order_dirty[key] = False
            index = self._order[key]

        bucket = self._by_type[resource_type]
        for resource_id in index:
            resource = bucket.get(resource_id)
            if resource is not None and resource.extant:
                yield resource

    @property
    def gc_interval(self) -> float:
        """Seconds of silence after which a Node is collected."""
        return self._gc_interval

    @property
    def forget_interval(self) -> float:
        """Seconds a non-extant resource is retained before being dropped."""
        return self._forget_interval

    # -----------------------------------------------------------------------
    # Cursor allocation
    # -----------------------------------------------------------------------

    def _next_cursor(self, resource_type: ResourceType) -> TaiCursor:
        """Allocate a strictly-increasing cursor for ``resource_type``.

        Wall-clock normally supplies it, but two registrations of the same
        type within one clock tick would otherwise share a cursor, and a
        client paging with ``paging.since=<that cursor>`` would then skip
        whichever record sorted second. Falling forward by one nanosecond on
        collision keeps cursors unique and monotonic per type, which is what
        ``APIs - Query Parameters.md:17`` asks for.
        """
        cursor = TaiCursor.now()
        previous = self._last_cursor.get(resource_type)
        if previous is not None and cursor <= previous:
            cursor = previous.next()
        self._last_cursor[resource_type] = cursor
        return cursor

    def next_cursor(self, resource_type: ResourceType) -> TaiCursor:
        """Allocate a cursor without applying anything.

        The distributed backend needs the cursor *before* the write, because it
        goes into the envelope that etcd stores -- the value has to be
        authoritative, so it cannot be chosen after the fact. Uniqueness within
        a type still comes from the same allocator, which is what keeps paging
        from skipping a record.
        """
        return self._next_cursor(resource_type)

    # -----------------------------------------------------------------------
    # Lookup
    # -----------------------------------------------------------------------

    def get(
        self,
        resource_type: ResourceType,
        resource_id: str,
        *,
        include_non_extant: bool = False,
    ) -> RegisteredResource | None:
        """Fetch one resource, or None.

        Non-extant resources are hidden by default: to every API client a
        deleted resource is simply gone, and only the internal lifecycle
        machinery has a reason to see the tombstoned record.
        """
        found = self._by_type[resource_type].get(resource_id)
        if found is None:
            return None
        if not found.extant and not include_non_extant:
            return None
        return found

    def find_any(self, resource_id: str) -> RegisteredResource | None:
        """Fetch a resource by id regardless of type, or None."""
        resource_type = self._type_of.get(resource_id)
        if resource_type is None:
            return None
        return self.get(resource_type, resource_id)

    def iter_extant(
        self, resource_type: ResourceType,
    ) -> Iterator[RegisteredResource]:
        """Iterate the live resources of one type, in no particular order."""
        for resource in self._by_type[resource_type].values():
            if resource.extant:
                yield resource

    def count_extant(self, resource_type: ResourceType) -> int:
        return sum(1 for _ in self.iter_extant(resource_type))

    # -----------------------------------------------------------------------
    # Registration
    # -----------------------------------------------------------------------

    def prepare(
        self,
        resource_type: ResourceType,
        raw: dict[str, Any],
    ) -> PreparedRegistration | RegistrationResult:
        """Validate a registration against current state without mutating.

        Splitting validation from application is what lets the distributed
        backend do them at different times and against different authorities:
        it validates here, commits to etcd, and only applies once the change
        comes back through the watch. Standalone runs both immediately, which
        is why ``insert_or_update`` is now a thin wrapper over this plus
        ``apply_committed``.

        Returns:
            ``PreparedRegistration`` describing what *would* happen, or a
            ``RegistrationResult`` carrying the failure. The union return is
            deliberate: every caller has to handle the failure anyway, and an
            exception would make the ordinary 400 path exceptional.
        """
        resource_id = raw.get("id")
        if not isinstance(resource_id, str) or not resource_id:
            return RegistrationResult.failure(
                RegistrationError.SCHEMA, "resource has no 'id' attribute",
            )

        version = raw.get("version")
        if not isinstance(version, str):
            return RegistrationResult.failure(
                RegistrationError.SCHEMA, "resource has no 'version' attribute",
            )

        # --- :101 the id must not already name a different type ---
        existing_type = self._type_of.get(resource_id)
        if existing_type is not None and existing_type is not resource_type:
            return RegistrationResult.failure(
                RegistrationError.ID_TYPE_CONFLICT,
                f"id {resource_id} is already registered as a "
                f"{existing_type.value}, cannot re-register as a "
                f"{resource_type.value}",
            )

        parent_id = self._parent_id_of(resource_type, raw)

        # --- :104 the parent must exist and be of the right type ---
        parent_check = self._check_parent(resource_type, parent_id)
        if parent_check is not None:
            return parent_check

        previous = self._by_type[resource_type].get(resource_id)
        # A non-extant record is treated as absent for registration purposes:
        # re-registering an id that was deleted or collected is a *create*,
        # and must answer 201 so the Node's own state machine stays in step.
        reviving = previous is not None and not previous.extant

        if previous is not None and previous.extant:
            failure = self._check_update(previous, version, parent_id)
            if failure is not None:
                return failure

        return PreparedRegistration(
            resource_type=resource_type,
            resource_id=resource_id,
            version=version,
            parent_id=parent_id,
            creates=previous is None or reviving,
            reviving=reviving,
        )

    def apply_committed(
        self,
        prepared: PreparedRegistration,
        raw: dict[str, Any],
        *,
        created: TaiCursor | None = None,
        updated: TaiCursor | None = None,
        health: int | None = None,
    ) -> RegistrationResult:
        """Apply a registration that has already been validated and committed.

        Args:
            created / updated: Authoritative paging cursors. Standalone leaves
                them None and cursors are allocated locally; the distributed
                backend supplies the values stored in etcd, so every registry
                pages identically. See ``_next_cursor`` for why they must be
                unique within a type.
            health: Authoritative health, or None to stamp the local clock.

        Completes without awaiting, like every other public method here. The
        distributed backend calls it from inside its watch-application step,
        where the whole revision must be applied and its grains queued in one
        uninterrupted block.
        """
        resource_id = prepared.resource_id
        resource_type = prepared.resource_type

        now_health = health_now() if health is None else health
        cursor = updated if updated is not None else self._next_cursor(
            resource_type,
        )
        # Keep the per-type high-water mark ahead of any authoritative cursor
        # we are handed, so a later locally allocated cursor cannot collide
        # with one that already came from etcd.
        previous_cursor = self._last_cursor.get(resource_type)
        if previous_cursor is None or cursor > previous_cursor:
            self._last_cursor[resource_type] = cursor

        previous = self._by_type[resource_type].get(resource_id)
        pre_parent = previous.parent_id if previous is not None else None

        if previous is not None and not prepared.reviving:
            pre_raw = previous.raw
            previous.raw = raw
            previous.version = prepared.version
            previous.updated = cursor
            previous.parent_id = prepared.parent_id
            previous.health = now_health
            self._reparent(
                resource_id, pre_parent=pre_parent,
                new_parent=prepared.parent_id,
            )
            # ``created`` did not move, so only the update index reorders.
            self._touch_order(resource_type, ORDER_UPDATE, resource_id, cursor)
            return RegistrationResult(
                created=False,
                events=[ResourceEvent.modified(pre_raw, previous)],
            )

        resource = RegisteredResource(
            resource_type=resource_type,
            id=resource_id,
            raw=raw,
            version=prepared.version,
            created=created if created is not None else cursor,
            updated=cursor,
            parent_id=prepared.parent_id,
            extant=True,
            health=now_health,
        )
        self._by_type[resource_type][resource_id] = resource
        self._type_of[resource_id] = resource_type
        self._reparent(
            resource_id, pre_parent=pre_parent, new_parent=prepared.parent_id,
        )
        # A revived id may still be listed as a parent of resources that were
        # erased alongside it. Those children are non-extant and on their own
        # forget timer; the fresh record must not adopt them, or a later
        # cascade delete would resurrect-then-re-erase records the client was
        # already told were gone.
        if prepared.reviving:
            self._children.pop(resource_id, None)

        # A create — or a revive, which assigns a fresh ``created`` — moves the
        # resource to the end of BOTH indexes. A revive is exactly why the
        # create index cannot simply be "insertion order into the dict": the
        # record is replaced in place, so the dict keeps the old position while
        # the cursor has moved to the front of the queue.
        self._touch_order(
            resource_type, ORDER_CREATE, resource_id, resource.created,
        )
        self._touch_order(resource_type, ORDER_UPDATE, resource_id, cursor)

        return RegistrationResult(
            created=True, events=[ResourceEvent.added(resource)],
        )

    def insert_or_update(
        self,
        resource_type: ResourceType,
        raw: dict[str, Any],
    ) -> RegistrationResult:
        """Apply a ``POST /resource`` for an already-validated resource.

        The caller has already decoded ``raw`` against the generated type for
        ``resource_type``; that decode is the schema validation of
        ``Behaviour - Registration.md:100`` and any failure has already become
        a ``RegistrationError.SCHEMA`` upstream. The decoded object itself is
        not passed on — nothing downstream reads it. What is left are the four
        conditions that need registry state to decide.

        Args:
            resource_type: Type named by the POST envelope's ``type`` field.
            raw: The resource JSON exactly as received. Stored verbatim and
                served back by the Query API — see ``RegisteredResource.raw``.

        Returns:
            A ``RegistrationResult``: on success ``created`` distinguishes 201
            from 200 and ``events`` carries the grain events to publish; on
            failure ``error`` names the condition and ``detail`` explains it.
        """
        prepared = self.prepare(resource_type, raw)
        if isinstance(prepared, RegistrationResult):
            return prepared
        return self.apply_committed(prepared, raw)

    def _parent_id_of(
        self, resource_type: ResourceType, raw: dict[str, Any],
    ) -> str | None:
        """Read this type's parent-reference attribute out of the raw JSON."""
        key = PARENT_KEY[resource_type]
        if key is None:
            return None
        value = raw.get(key)
        return value if isinstance(value, str) else None

    def _check_parent(
        self, resource_type: ResourceType, parent_id: str | None,
    ) -> RegistrationResult | None:
        """Referential integrity: ``Behaviour - Registration.md:55, :104``.

        "In order to permit garbage collection, resources MUST only be
        accepted by a Registration API where the registry already has a
        record of the corresponding parent resource." The AMWA mock registry
        skips this check entirely; without it a Sender can outlive every
        Node and never be collected.
        """
        expected = PARENT_TYPE[resource_type]
        if expected is None:
            return None

        key = PARENT_KEY[resource_type]
        if parent_id is None:
            return RegistrationResult.failure(
                RegistrationError.SCHEMA,
                f"{resource_type.value} is missing its '{key}' attribute",
            )

        actual_type = self._type_of.get(parent_id)
        parent = self._by_type[actual_type].get(parent_id) if actual_type else None
        if parent is None or not parent.extant:
            return RegistrationResult.failure(
                RegistrationError.PARENT_MISSING,
                f"parent {expected.value} {parent_id} is not registered",
            )
        if actual_type is not expected:
            return RegistrationResult.failure(
                RegistrationError.PARENT_MISSING,
                f"{key} {parent_id} names a {actual_type.value if actual_type else 'unknown'}, "
                f"expected a {expected.value}",
            )
        return None

    def _check_update(
        self,
        previous: RegisteredResource,
        version: str,
        parent_id: str | None,
    ) -> RegistrationResult | None:
        """The two update-only 400 conditions.

        ``:102`` the version must not go backwards, and ``:103`` a parent id
        must not be modified by an update. Re-POSTing an unchanged version is
        explicitly *not* an error — a Node that re-registers after a failed
        heartbeat replays its resources verbatim, and rejecting that would
        break the documented recovery path at ``:114``.
        """
        new_cursor = TaiCursor.parse(version)
        old_cursor = previous.version_cursor()
        if new_cursor is None:
            return RegistrationResult.failure(
                RegistrationError.SCHEMA,
                f"version {version!r} is not '<seconds>:<nanoseconds>'",
            )
        if old_cursor is not None and new_cursor < old_cursor:
            return RegistrationResult.failure(
                RegistrationError.VERSION_REGRESSION,
                f"version {version} is earlier than the registered "
                f"version {previous.version}",
            )
        if previous.parent_id is not None and parent_id != previous.parent_id:
            key = PARENT_KEY[previous.resource_type]
            return RegistrationResult.failure(
                RegistrationError.PARENT_CHANGED,
                f"{key} cannot be modified by an update "
                f"({previous.parent_id} -> {parent_id})",
            )
        return None

    def _reparent(
        self, resource_id: str, *, pre_parent: str | None, new_parent: str | None,
    ) -> None:
        """Maintain the parent -> children index."""
        if pre_parent is not None:
            siblings = self._children.get(pre_parent)
            if siblings is not None:
                siblings.discard(resource_id)
        if new_parent is not None:
            self._children.setdefault(new_parent, set()).add(resource_id)

    # -----------------------------------------------------------------------
    # Deletion
    # -----------------------------------------------------------------------

    def delete(
        self, resource_type: ResourceType, resource_id: str,
    ) -> list[ResourceEvent] | None:
        """Delete a resource and, cascading, all of its descendants.

        ``Behaviour - Registration.md:68`` — "Where a DELETE is issued against
        a parent resource, all child resources MUST be removed from the
        registry immediately" — and ``:74``, which requires the registry to
        clean up children even when a Node unregisters out of order. The AMWA
        mock removes only the addressed resource, which leaves orphans that
        nothing will ever collect.

        Returns:
            The removal events, deepest descendant first, or None if the
            resource was not registered (the caller answers 404).
        """
        resource = self.get(resource_type, resource_id)
        if resource is None:
            return None
        return self._erase_subtree(resource)

    def _erase_subtree(
        self, resource: RegisteredResource,
    ) -> list[ResourceEvent]:
        """Mark a resource and its descendants non-extant, depth-first.

        Children are erased before their parent so that a client replaying
        the events in order never sees a parent disappear while its children
        are still present — the mirror image of the registration ordering
        rule at ``Behaviour - Registration.md:57-64``.
        """
        events: list[ResourceEvent] = []
        for child_id in list(self._children.get(resource.id, ())):
            child = self.find_any(child_id)
            if child is not None:
                events.extend(self._erase_subtree(child))
        events.append(ResourceEvent.removed(resource))
        resource.extant = False
        resource.health = health_now()
        return events

    def remove_one(
        self, resource_type: ResourceType, resource_id: str,
    ) -> ResourceEvent | None:
        """Mark a single resource non-extant, WITHOUT cascading to children.

        The distributed counterpart of ``delete``. There, the cascade has
        already happened in etcd -- deleting a Node ranges over its whole
        subtree -- and the watch delivers a separate event for every key that
        went, all within one revision. Cascading again locally would erase
        descendants a second time and emit duplicate removal grains for them.

        The caller is responsible for applying a revision's removals
        descendants-first, so a subscriber never sees a parent disappear while
        its children are still present.

        Returns None if the resource is already absent or already non-extant,
        which is normal on a watch replay after reconnection.
        """
        resource = self._by_type[resource_type].get(resource_id)
        if resource is None or not resource.extant:
            return None
        event = ResourceEvent.removed(resource)
        resource.extant = False
        resource.health = health_now()
        return event

    def _forget(self, resource: RegisteredResource) -> None:
        """Drop a non-extant resource entirely.

        Stage two of the lifecycle (nmos-cpp's ``forget_erased_resources``).
        Only after this point is the id free for reuse and the record gone
        from the statistics.
        """
        self._by_type[resource.resource_type].pop(resource.id, None)
        self._drop_from_order(resource.resource_type, resource.id)
        if self._type_of.get(resource.id) is resource.resource_type:
            del self._type_of[resource.id]
        self._children.pop(resource.id, None)
        if resource.parent_id is not None:
            siblings = self._children.get(resource.parent_id)
            if siblings is not None:
                siblings.discard(resource.id)

    # -----------------------------------------------------------------------
    # Health and garbage collection
    # -----------------------------------------------------------------------

    def heartbeat(self, node_id: str) -> int | None:
        """Record a heartbeat for a Node. Returns the new health, or None.

        None means the Node is not registered, and the caller answers 404 —
        ``Behaviour - Registration.md:112-114``, on which the Node must
        re-register all of its resources in order.

        The refresh is recursive over the Node's descendants; see
        ``RegisteredResource.health`` for why that is required rather than
        merely tidy.
        """
        node = self.get(ResourceType.NODE, node_id)
        if node is None:
            return None
        health = health_now()
        self._set_health_recursive(node, health)
        return health

    def _set_health_recursive(
        self, resource: RegisteredResource, health: int,
    ) -> None:
        """nmos-cpp ``set_resource_health``: the resource and all descendants."""
        for child_id in self._children.get(resource.id, ()):
            child = self.find_any(child_id)
            if child is not None:
                self._set_health_recursive(child, health)
        resource.health = health

    def node_health(self, node_id: str) -> int | None:
        """Current health of a Node, or None if it is not registered."""
        node = self.get(ResourceType.NODE, node_id)
        return None if node is None else node.health

    def collect_garbage(self) -> list[ResourceEvent]:
        """Expire silent Nodes and forget long-dead records.

        ``Behaviour - Registration.md:51``: "If heartbeats fail over a period
        greater than the garbage collection interval, both the Node and all
        registered sub-resources SHOULD be removed from the registry
        automatically."

        Because a heartbeat refreshes descendants too, expiry can be decided
        per resource on health alone; erasing the Node then cascades over a
        subtree whose members were all going to expire in the same tick
        anyway. Iterating Nodes explicitly (rather than every resource) keeps
        the emitted events in a sensible parent/child order.

        Returns:
            Removal events for everything collected in this pass.
        """
        now = health_now()
        expire_before = now - int(self._gc_interval)
        forget_before = now - int(self._forget_interval)

        events: list[ResourceEvent] = []

        for node in list(self.iter_extant(ResourceType.NODE)):
            if node.health < expire_before:
                log.info(
                    "registry: garbage collecting node %s "
                    "(health %d, expiry threshold %d)",
                    node.id, node.health, expire_before,
                )
                events.extend(self._erase_subtree(node))

        # Stage two: drop tombstones whose forget interval has elapsed.
        for bucket in self._by_type.values():
            for resource in list(bucket.values()):
                if not resource.extant and resource.health < forget_before:
                    self._forget(resource)

        return events

    # -----------------------------------------------------------------------
    # Statistics
    # -----------------------------------------------------------------------

    def statistics(
        self, *, subscriptions: int = 0, grains: int = 0,
    ) -> RegistryStatistics:
        """Snapshot the counters behind the periodic status line.

        Mirrors nmos-cpp's ``put_resources_statistics``: ``total`` is every
        extant resource across all eight kinds (subscriptions and grains
        included), the per-type counts are extant-only, and ``non_extant`` is
        reported alongside rather than deducted. ``least_health`` is the
        minimum over extant resources, defaulting to the current health when
        there is nothing to minimise over.
        """
        per_type: dict[ResourceType, int] = {}
        non_extant = 0
        most_recent = TaiCursor.min()
        least_health: int | None = None

        for resource_type, bucket in self._by_type.items():
            live = 0
            for resource in bucket.values():
                if resource.extant:
                    live += 1
                    if resource.updated > most_recent:
                        most_recent = resource.updated
                    if least_health is None or resource.health < least_health:
                        least_health = resource.health
                else:
                    non_extant += 1
            per_type[resource_type] = live

        total = sum(per_type.values()) + subscriptions + grains

        return RegistryStatistics(
            total=total,
            per_type=per_type,
            subscriptions=subscriptions,
            grains=grains,
            most_recent_update=most_recent,
            least_health=health_now() if least_health is None else least_health,
            non_extant=non_extant,
        )
