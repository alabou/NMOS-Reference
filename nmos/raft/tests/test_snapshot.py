# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Snapshots: a consistent image of a store that is still being written to.

The property under test is easy to state and easy to get wrong: the bytes must
describe the store **as it was at one index**, even though the store kept
changing while they were produced. A walk that simply read the live records
would describe a state that never existed -- early resources as they were,
later ones as they became -- and the member it caught up would hold a registry
no member ever had.
"""

from __future__ import annotations

import json

import pytest

from nmos.raft.errors import RaftProtocolError
from nmos.raft.ownership import OwnershipTable
from nmos.raft.snapshot import (
    SNAPSHOT_VERSION,
    SnapshotStore,
    decode_snapshot,
    install,
)
from nmos.raft.wire import Writer
from nmos.registry.store import RegistryStore
from nmos.registry.tests._fixtures import (
    DEVICE_ID,
    NODE_ID,
    SENDER_ID,
    make_device,
    make_node,
    make_sender,
)
from nmos.registry.types import Body, RegistrationResult, ResourceType, TaiCursor


def _fixture_set() -> list[tuple[ResourceType, dict]]:
    """One set of resource bodies, generated once.

    ``make_node`` and friends stamp ``version`` from the clock, so calling them
    twice produces resources that differ -- which is fine everywhere except a
    test about two *equal* stores serialising identically.
    """
    return [
        (ResourceType.NODE, make_node()),
        (ResourceType.DEVICE, make_device()),
        (ResourceType.SENDER, make_sender()),
    ]


def _seeded(
    offset: int = 0, fixtures: list[tuple[ResourceType, dict]] | None = None,
) -> RegistryStore:
    store = RegistryStore()
    for index, (resource_type, raw) in enumerate(
        fixtures if fixtures is not None else _fixture_set()
    ):
        body = Body(json.dumps(raw))
        prepared = store.prepare(resource_type, body.data)
        assert not isinstance(prepared, RegistrationResult), prepared
        store.apply_committed(
            prepared, body,
            created=TaiCursor(1000 + index + offset, 8),
            updated=TaiCursor(1000 + index + offset, 8),
            health=500 + index,
        )
    return store


def _update(store: RegistryStore, raw: dict, cursor: TaiCursor) -> None:
    body = Body(json.dumps(raw))
    prepared = store.prepare(ResourceType.SENDER, body.data)
    assert not isinstance(prepared, RegistrationResult), prepared
    store.apply_committed(
        prepared, body, created=TaiCursor(1002, 8), updated=cursor, health=999,
    )


class TestRoundTrip:
    async def test_a_snapshot_restores_an_equivalent_store(self) -> None:
        store = _seeded()
        snapshots = SnapshotStore(store)
        capture = snapshots.begin(index=42, term=3, ownership=OwnershipTable())
        payload = await snapshots.finish(capture)

        meta, _ownership, records = decode_snapshot(payload)
        assert meta.last_index == 42
        assert meta.last_term == 3
        assert meta.resources == 3

        restored = install(records, gc_interval=12.0, forget_interval=60.0)
        for resource_type, resource_id in (
            (ResourceType.NODE, NODE_ID),
            (ResourceType.DEVICE, DEVICE_ID),
            (ResourceType.SENDER, SENDER_ID),
        ):
            original = store.get(resource_type, resource_id)
            copy = restored.get(resource_type, resource_id)
            assert original is not None and copy is not None
            assert copy.body.text == original.body.text
            assert copy.created == original.created
            assert copy.updated == original.updated
            assert copy.health == original.health
            assert copy.parent_id == original.parent_id

    async def test_the_body_survives_verbatim(self) -> None:
        """Byte-for-byte, or a snapshotted member serves something else."""
        store = RegistryStore()
        raw = make_node()
        text = json.dumps(raw, indent=4)
        body = Body(text)
        prepared = store.prepare(ResourceType.NODE, body.data)
        assert not isinstance(prepared, RegistrationResult)
        store.apply_committed(
            prepared, body, created=TaiCursor(1, 0), updated=TaiCursor(1, 0),
            health=1,
        )

        snapshots = SnapshotStore(store)
        payload = await snapshots.finish(
            snapshots.begin(index=1, term=1, ownership=OwnershipTable()),
        )
        _meta, _own, records = decode_snapshot(payload)
        assert records[0].body.text == text

    async def test_ownership_travels_with_it(self) -> None:
        store = _seeded()
        table = OwnershipTable()
        table.claim(NODE_ID, owner=2, epoch=7)

        snapshots = SnapshotStore(store)
        payload = await snapshots.finish(
            snapshots.begin(index=9, term=1, ownership=table),
        )
        _meta, restored, _records = decode_snapshot(payload)
        assert restored.is_owned_by(NODE_ID, 2)

    async def test_an_empty_store_round_trips(self) -> None:
        snapshots = SnapshotStore(RegistryStore())
        payload = await snapshots.finish(
            snapshots.begin(index=0, term=0, ownership=OwnershipTable()),
        )
        meta, _own, records = decode_snapshot(payload)
        assert meta.resources == 0
        assert records == []


class TestCopyOnWrite:
    """The image must be of one moment, not of the walk."""

    async def test_a_record_mutated_during_the_capture_keeps_its_pre_image(
        self,
    ) -> None:
        store = _seeded()
        original = store.get(ResourceType.SENDER, SENDER_ID)
        assert original is not None
        before_text = original.body.text

        snapshots = SnapshotStore(store)
        capture = snapshots.begin(index=10, term=1, ownership=OwnershipTable())

        # Apply hands the pre-image over *before* mutating, which is the whole
        # contract. Then the record changes underneath the capture.
        capture.capture(original)
        changed = make_sender()
        changed["label"] = "renamed after the capture opened"
        _update(store, changed, TaiCursor(2000, 8))

        payload = await snapshots.finish(capture)
        _meta, _own, records = decode_snapshot(payload)

        sender = next(r for r in records if r.id == SENDER_ID)
        assert sender.body.text == before_text
        assert "renamed after" not in sender.body.text

    async def test_capture_is_idempotent(self) -> None:
        """Repeated updates keep the earliest state: the pinned one."""
        store = _seeded()
        snapshots = SnapshotStore(store)
        capture = snapshots.begin(index=10, term=1, ownership=OwnershipTable())

        first = store.get(ResourceType.SENDER, SENDER_ID)
        assert first is not None
        capture.capture(first)
        _update(store, make_sender(), TaiCursor(2000, 8))

        second = store.get(ResourceType.SENDER, SENDER_ID)
        assert second is not None
        capture.capture(second)

        assert capture.held == 1

    async def test_a_record_created_after_the_capture_is_excluded(self) -> None:
        """Otherwise the snapshot describes a future its index had not reached."""
        store = _seeded()
        snapshots = SnapshotStore(store)
        capture = snapshots.begin(index=10, term=1, ownership=OwnershipTable())

        newcomer = make_sender("99999999-0000-4000-8000-000000000000")
        capture.capture_created(ResourceType.SENDER, newcomer["id"])
        body = Body(json.dumps(newcomer))
        prepared = store.prepare(ResourceType.SENDER, body.data)
        assert not isinstance(prepared, RegistrationResult)
        store.apply_committed(
            prepared, body, created=TaiCursor(3000, 8),
            updated=TaiCursor(3000, 8), health=1,
        )

        payload = await snapshots.finish(capture)
        _meta, _own, records = decode_snapshot(payload)
        assert all(r.id != newcomer["id"] for r in records)

    async def test_untouched_records_come_from_the_live_store(self) -> None:
        """No pre-image means nothing changed, so the live record is the image."""
        store = _seeded()
        snapshots = SnapshotStore(store)
        capture = snapshots.begin(index=10, term=1, ownership=OwnershipTable())
        payload = await snapshots.finish(capture)

        _meta, _own, records = decode_snapshot(payload)
        assert capture.held == 0
        assert len(records) == 3


class TestDeterminism:
    async def test_equal_stores_produce_identical_bytes(self) -> None:
        """What makes a snapshot comparable or checksummable at all."""
        fixtures = _fixture_set()
        left = SnapshotStore(_seeded(fixtures=fixtures))
        right = SnapshotStore(_seeded(fixtures=fixtures))
        left_bytes = await left.finish(
            left.begin(index=1, term=1, ownership=OwnershipTable()),
        )
        right_bytes = await right.finish(
            right.begin(index=1, term=1, ownership=OwnershipTable()),
        )
        assert left_bytes == right_bytes


class TestRejections:
    async def test_an_unknown_version_is_refused(self) -> None:
        payload = (
            Writer().uint(1, SNAPSHOT_VERSION + 1).uint(2, 1).uint(3, 1)
            .uint(4, 0).bytes_(5, b"").take()
        )
        with pytest.raises(RaftProtocolError, match="snapshot version"):
            decode_snapshot(payload)

    async def test_a_truncated_transfer_is_refused(self) -> None:
        """It parsed, and it is still missing resources its peers hold."""
        store = _seeded()
        snapshots = SnapshotStore(store)
        payload = await snapshots.finish(
            snapshots.begin(index=1, term=1, ownership=OwnershipTable()),
        )
        # Claim four resources while carrying three.
        tampered = payload.replace(
            Writer().uint(4, 3).take(), Writer().uint(4, 4).take(), 1,
        )
        with pytest.raises(RaftProtocolError, match="claims 4 resources"):
            decode_snapshot(tampered)

    async def test_two_captures_at_once_are_refused(self) -> None:
        snapshots = SnapshotStore(_seeded())
        snapshots.begin(index=1, term=1, ownership=OwnershipTable())
        with pytest.raises(RuntimeError, match="already open"):
            snapshots.begin(index=2, term=1, ownership=OwnershipTable())

    async def test_the_capture_is_released_after_finishing(self) -> None:
        """An open capture taxes every apply; it must not outlive its use."""
        snapshots = SnapshotStore(_seeded())
        capture = snapshots.begin(index=1, term=1, ownership=OwnershipTable())
        assert snapshots.capture is capture
        await snapshots.finish(capture)
        assert snapshots.capture is None

    async def test_abandoning_releases_it_too(self) -> None:
        snapshots = SnapshotStore(_seeded())
        snapshots.begin(index=1, term=1, ownership=OwnershipTable())
        snapshots.abandon()
        assert snapshots.capture is None


class TestInstallOrdering:
    async def test_parents_are_restored_before_their_children(self) -> None:
        """A child whose parent is absent is refused outright."""
        store = _seeded()
        snapshots = SnapshotStore(store)
        payload = await snapshots.finish(
            snapshots.begin(index=1, term=1, ownership=OwnershipTable()),
        )
        _meta, _own, records = decode_snapshot(payload)

        # Reversed: install must sort, not trust the order it is handed.
        restored = install(
            list(reversed(records)), gc_interval=12.0, forget_interval=60.0,
        )
        assert restored.get(ResourceType.SENDER, SENDER_ID) is not None
        assert restored.count_extant(ResourceType.SENDER) == 1
