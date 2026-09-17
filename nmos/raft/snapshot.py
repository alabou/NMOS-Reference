# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Snapshots: capturing a consistent registry image without stalling the loop.

Why a snapshot is needed at all
-------------------------------
The log is compacted, or it grows forever. Compaction means a follower that
falls behind the leader's oldest retained entry can no longer be caught up by
replication -- the entries it needs are gone -- and must be handed the *state*
instead. The two are halves of one mechanism: a log that is compacted without a
snapshot transfer to fall back on is a log that can strand a member permanently.

The hard part: the store is alive
---------------------------------
``apply_committed`` mutates ``RegisteredResource`` records **in place**. So
neither a shallow copy of the store nor a leisurely walk of it produces an
image of any single moment: by the time the walk reaches the last resource, the
first may have been updated, and the result is a state that never existed.

Stopping the world would work and is not available -- a registry that froze for
the length of a snapshot would stall every registration and, worse, the
heartbeat timer.

**Copy-on-write, captured from apply.** While a capture is open, ``apply``
hands this module every record it is *about to* mutate, before mutating it. The
capture keeps that pre-image if it has not already got one. Serialisation then
walks the live store in chunks, yielding between them, and emits the captured
pre-image wherever one exists and the live record otherwise. The result is
exactly the state at the pinned index.

The cost on the hot path is one dictionary membership test per mutated
resource, plus one serialisation the first time each is touched -- and only
while a capture is open, which is rare. The cost of the alternative is a
registry that pauses.

Why it lives here and not in ``store.py``
------------------------------------------
Apply already knows which resources an operation touches. Pushing a hook into
the store would spread snapshot awareness across the one module whose
invariants are most worth keeping narrow, to learn something the caller
already knew.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Iterator

from nmos.raft.errors import RaftProtocolError
from nmos.raft.ownership import OwnershipTable
from nmos.raft.wire import Reader, Writer
from nmos.registry.store import RegistryStore
from nmos.registry.types import (
    Body,
    RegisteredResource,
    ResourceType,
    TaiCursor,
)

SNAPSHOT_VERSION = 1

# How many resources are serialised between yields. Small enough that the loop
# is never held for long, large enough that the yield overhead is noise against
# the work.
CHUNK_RESOURCES = 256


def _encode_resource(resource: RegisteredResource) -> bytes:
    """One record, with ``body.text`` verbatim.

    Verbatim because the registry's guarantee is that what a client registered
    is what every member serves, byte for byte. A snapshot that re-encoded
    bodies would break that on exactly the members that were caught up by one,
    and the difference would only ever show up as two members disagreeing about
    a vendor extension.
    """
    return (
        Writer()
        .string(1, resource.resource_type.value)
        .string(2, resource.id)
        .string(3, resource.version)
        .uint(4, resource.created.seconds)
        .uint(5, resource.created.nanoseconds)
        .uint(6, resource.updated.seconds)
        .uint(7, resource.updated.nanoseconds)
        .bool_(8, resource.extant)
        .uint(9, max(0, resource.health))
        .string(10, resource.parent_id or "")
        .string(11, resource.body.text)
        .take()
    )


def _decode_resource(payload: bytes) -> RegisteredResource:
    type_name = ""
    resource_id = version = parent_id = body_text = ""
    created_s = created_ns = updated_s = updated_ns = health = 0
    extant = True

    reader = Reader(payload)
    for number, wire in reader:
        if number == 1:
            type_name = reader.string()
        elif number == 2:
            resource_id = reader.string()
        elif number == 3:
            version = reader.string()
        elif number == 4:
            created_s = reader.uint()
        elif number == 5:
            created_ns = reader.uint()
        elif number == 6:
            updated_s = reader.uint()
        elif number == 7:
            updated_ns = reader.uint()
        elif number == 8:
            extant = reader.bool_()
        elif number == 9:
            health = reader.uint()
        elif number == 10:
            parent_id = reader.string()
        elif number == 11:
            body_text = reader.string()
        else:
            reader.skip(wire)

    try:
        resource_type = ResourceType(type_name)
    except ValueError as exc:
        raise RaftProtocolError(
            f"snapshot names unknown resource type {type_name!r}",
        ) from exc

    return RegisteredResource(
        resource_type=resource_type,
        id=resource_id,
        body=Body(body_text),
        version=version,
        created=TaiCursor(created_s, created_ns),
        updated=TaiCursor(updated_s, updated_ns),
        parent_id=parent_id or None,
        extant=extant,
        health=health,
    )


@dataclass
class SnapshotMeta:
    """What a snapshot covers."""

    last_index: int
    last_term: int
    resources: int


class SnapshotCapture:
    """Pre-images of records mutated since the capture opened.

    Open for the duration of one serialisation and no longer: every open
    capture adds a membership test to the apply path, and holds pre-images
    alive that would otherwise be garbage.
    """

    __slots__ = ("index", "term", "ownership", "_pre", "_gone")

    def __init__(
        self, *, index: int, term: int, ownership: bytes,
    ) -> None:
        self.index = index
        self.term = term
        self.ownership = ownership
        self._pre: dict[tuple[ResourceType, str], bytes] = {}
        # Resources that did not exist at the pinned index. A record created
        # after the capture opened must NOT appear in it, or the snapshot
        # describes a future the index it claims had not reached.
        self._gone: set[tuple[ResourceType, str]] = set()

    def capture(self, resource: RegisteredResource) -> None:
        """Record a pre-image, if this resource has not been captured yet.

        Called from apply *before* the mutation. Idempotent, so a resource
        updated repeatedly during one capture keeps its earliest state -- which
        is the state at the pinned index.
        """
        key = (resource.resource_type, resource.id)
        if key in self._pre or key in self._gone:
            return
        self._pre[key] = _encode_resource(resource)

    def capture_created(
        self, resource_type: ResourceType, resource_id: str,
    ) -> None:
        """Note a resource that did not exist when the capture opened."""
        key = (resource_type, resource_id)
        if key not in self._pre:
            self._gone.add(key)

    def image_of(
        self, resource: RegisteredResource,
    ) -> bytes | None:
        """The bytes this resource had at the pinned index, or None to skip."""
        key = (resource.resource_type, resource.id)
        if key in self._gone:
            return None
        captured = self._pre.get(key)
        return captured if captured is not None else _encode_resource(resource)

    def orphans(
        self, live: set[tuple[ResourceType, str]],
    ) -> list[tuple[tuple[str, str], bytes]]:
        """Captured pre-images whose resource the live walk did not reach.

        Deleted, in other words. Keyed by ``(type value, id)`` rather than by
        the enum so the sort is total and identical on every member.
        """
        return [
            ((key[0].value, key[1]), image)
            for key, image in self._pre.items() if key not in live
        ]

    @property
    def held(self) -> int:
        return len(self._pre)


class SnapshotStore:
    """Takes and installs snapshots for one member."""

    __slots__ = ("_registry_store", "_capture")

    def __init__(self, store: RegistryStore) -> None:
        self._registry_store = store
        self._capture: SnapshotCapture | None = None

    @property
    def capture(self) -> SnapshotCapture | None:
        """The open capture, if any. ``machine.py`` consults this per mutation."""
        return self._capture

    def begin(
        self, *, index: int, term: int, ownership: OwnershipTable,
    ) -> SnapshotCapture:
        if self._capture is not None:
            raise RuntimeError("a snapshot capture is already open")
        self._capture = SnapshotCapture(
            index=index, term=term, ownership=ownership.encode(),
        )
        return self._capture

    async def finish(self, capture: SnapshotCapture) -> bytes:
        """Serialise the pinned image, yielding between chunks.

        The yields are what keep a snapshot of a large registry from stalling
        the event loop -- and therefore the heartbeat timer, which is how a
        snapshot would otherwise cause the election it has no business causing.
        """
        if capture is not self._capture:
            raise RuntimeError("that capture is not the open one")
        try:
            records: list[bytes] = []
            live: set[tuple[ResourceType, str]] = set()
            seen = 0
            for resource in self._walk():
                live.add((resource.resource_type, resource.id))
                image = capture.image_of(resource)
                if image is not None:
                    records.append(image)
                seen += 1
                if seen % CHUNK_RESOURCES == 0:
                    await asyncio.sleep(0)

            # Resources that were *deleted* while the capture was open are no
            # longer in the walk, but they existed at the pinned index, so the
            # snapshot has to carry them. Without this a member caught up
            # during a cascading delete would be missing every resource that
            # cascade removed -- silently, and only on that member.
            for key, image in sorted(capture.orphans(live)):
                records.append(image)

            writer = (
                Writer()
                .uint(1, SNAPSHOT_VERSION)
                .uint(2, capture.index)
                .uint(3, capture.term)
                .uint(4, len(records))
                .bytes_(5, capture.ownership)
            )
            for record in records:
                writer.bytes_(6, record)
            return writer.take()
        finally:
            self._capture = None

    def abandon(self) -> None:
        """Drop an open capture without producing a snapshot."""
        self._capture = None

    def _walk(self) -> Iterator[RegisteredResource]:
        """Every extant resource, in a fixed order.

        **Tombstones are deliberately excluded**, matching what the etcd
        backend's preload produces -- there, deleted resources are gone from
        the keyspace entirely, so a member that preloads has none either.

        The consequence, stated rather than buried: a member caught up by a
        snapshot holds no tombstones, so for up to one forget interval its
        ``_type_of`` is narrower than its peers'. It would accept a
        re-registration of a recently-deleted id under a *different* type that
        the others refuse. The window is bounded by the replicated ``ForgetOp``
        -- which every member applies at the same log index, and which is a
        harmless no-op on a member that never had the tombstone.

        Sorted, so two members with equal stores produce byte-identical
        snapshots, which is what makes one comparable or checksummable at all.
        """
        store = self._registry_store
        for resource_type in ResourceType:
            for resource in sorted(
                store.iter_extant(resource_type), key=lambda r: r.id,
            ):
                yield resource


def decode_snapshot(
    payload: bytes,
) -> tuple[SnapshotMeta, OwnershipTable, list[RegisteredResource]]:
    """Parse a snapshot into the pieces an installer needs."""
    version = index = term = count = 0
    ownership_blob = b""
    records: list[RegisteredResource] = []

    reader = Reader(payload)
    for number, wire in reader:
        if number == 1:
            version = reader.uint()
        elif number == 2:
            index = reader.uint()
        elif number == 3:
            term = reader.uint()
        elif number == 4:
            count = reader.uint()
        elif number == 5:
            ownership_blob = reader.bytes_()
        elif number == 6:
            records.append(_decode_resource(reader.bytes_()))
        else:
            reader.skip(wire)

    if version != SNAPSHOT_VERSION:
        raise RaftProtocolError(
            f"snapshot version {version}, this member understands "
            f"{SNAPSHOT_VERSION}",
        )
    if count != len(records):
        # A truncated transfer that still parsed. Installing it would leave
        # this member silently missing resources its peers hold.
        raise RaftProtocolError(
            f"snapshot claims {count} resources and carries {len(records)}",
        )

    return (
        SnapshotMeta(last_index=index, last_term=term, resources=len(records)),
        OwnershipTable.decode(ownership_blob),
        records,
    )


def install(
    records: list[RegisteredResource], *, gc_interval: float,
    forget_interval: float,
) -> RegistryStore:
    """Build a fresh store from a snapshot's records.

    Off to the side, deliberately: the caller swaps it in once it is complete,
    so Query never sees a half-loaded store. A member that served an empty view
    for the length of an install would look, to a Controller, exactly like a
    member whose registry had been wiped.

    Restored through ``prepare`` + ``apply_committed`` with the cursors and
    health passed explicitly -- the same path ``machine.py`` uses, and for the
    same reason. ``insert_or_update`` would re-allocate cursors from this
    member's own clock, so a member caught up by a snapshot would page
    differently from every peer.

    Raises:
        RaftProtocolError: A record failed validation, or a child arrived
            without its parent. Both mean the snapshot does not describe a
            state the cluster was ever in, and installing part of it would
            leave this member quietly serving a subset.
    """
    from nmos.registry.types import RegistrationResult

    store = RegistryStore(
        gc_interval=gc_interval, forget_interval=forget_interval,
    )
    # Parents first: the store maintains a parent/child index, and a child
    # whose parent is absent is refused outright.
    for resource in sorted(records, key=_depth_key):
        prepared = store.prepare(resource.resource_type, resource.body.data)
        if isinstance(prepared, RegistrationResult):
            raise RaftProtocolError(
                f"snapshot record {resource.resource_type.value} "
                f"{resource.id} was refused: "
                f"{prepared.error.value if prepared.error else 'unknown'}",
            )
        store.apply_committed(
            prepared,
            resource.body,
            created=resource.created,
            updated=resource.updated,
            health=resource.health,
        )
    return store


def _depth_key(resource: RegisteredResource) -> tuple[int, str]:
    if resource.resource_type is ResourceType.NODE:
        return 0, resource.id
    if resource.resource_type is ResourceType.DEVICE:
        return 1, resource.id
    return 2, resource.id
