# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for nmos.node.store — ResourceStore[T]."""

from __future__ import annotations

import pytest

from nmos.errors import NotFound
from nmos.node.store import ResourceStore, to_static_id
from nmos.uuid import ResourceType, ResourceSubType, ResourceUuid


def _make_uuid(rt: ResourceType, index: int, sn: str, uid: int) -> str:
    u = ResourceUuid()
    u.set(rt, ResourceSubType.NONE, index, sn, uid, False)
    return str(u)


def _make_static(rt: ResourceType, index: int, sn: str) -> str:
    return _make_uuid(rt, index, sn, 0)


class TestToStaticId:
    """to_static_id zeroes the uniqueId portion."""

    def test_dynamic_to_static(self) -> None:
        dynamic = _make_uuid(ResourceType.SENDER, 5, "SNX123", 0xDEADBEEF)
        static = _make_uuid(ResourceType.SENDER, 5, "SNX123", 0)
        assert to_static_id(dynamic) == static

    def test_static_is_idempotent(self) -> None:
        static = _make_static(ResourceType.FLOW, 0, "SN1")
        assert to_static_id(static) == static


class TestResourceStore:
    """ResourceStore basic CRUD operations."""

    def test_put_and_get(self) -> None:
        store: ResourceStore[str] = ResourceStore()
        static_id = _make_static(ResourceType.SENDER, 0, "SN1")
        store.put(static_id, "hello")
        assert store.get(static_id) == "hello"

    def test_get_by_dynamic_id(self) -> None:
        """Lookup by dynamic ID auto-converts to static."""
        store: ResourceStore[str] = ResourceStore()
        static_id = _make_static(ResourceType.SENDER, 0, "SN1")
        dynamic_id = _make_uuid(ResourceType.SENDER, 0, "SN1", 0xCAFE)
        store.put(static_id, "hello")
        assert store.get(dynamic_id) == "hello"

    def test_get_missing_returns_none(self) -> None:
        store: ResourceStore[str] = ResourceStore()
        assert store.get("nonexistent") is None

    def test_get_or_raise(self) -> None:
        store: ResourceStore[str] = ResourceStore()
        with pytest.raises(NotFound):
            store.get_or_raise("nonexistent")

    def test_remove(self) -> None:
        store: ResourceStore[str] = ResourceStore()
        static_id = _make_static(ResourceType.SENDER, 0, "SN1")
        store.put(static_id, "hello")
        removed = store.remove(static_id)
        assert removed == "hello"
        assert store.get(static_id) is None

    def test_remove_missing(self) -> None:
        store: ResourceStore[str] = ResourceStore()
        assert store.remove("nonexistent") is None

    def test_contains(self) -> None:
        store: ResourceStore[str] = ResourceStore()
        static_id = _make_static(ResourceType.SENDER, 0, "SN1")
        assert static_id not in store
        store.put(static_id, "hello")
        assert static_id in store

    def test_len(self) -> None:
        store: ResourceStore[str] = ResourceStore()
        assert len(store) == 0
        store.put(_make_static(ResourceType.SENDER, 0, "SN1"), "a")
        store.put(_make_static(ResourceType.SENDER, 1, "SN1"), "b")
        assert len(store) == 2

    def test_snapshot_is_independent(self) -> None:
        """Snapshot is a shallow copy — mutations don't affect it."""
        store: ResourceStore[str] = ResourceStore()
        sid = _make_static(ResourceType.SENDER, 0, "SN1")
        store.put(sid, "hello")
        snap = store.snapshot()
        store.put(_make_static(ResourceType.SENDER, 1, "SN1"), "world")
        assert len(snap) == 1
        assert len(store) == 2

    def test_iter(self) -> None:
        store: ResourceStore[str] = ResourceStore()
        sid1 = _make_static(ResourceType.SENDER, 0, "SN1")
        sid2 = _make_static(ResourceType.SENDER, 1, "SN1")
        store.put(sid1, "a")
        store.put(sid2, "b")
        items = dict(store)
        assert items == {sid1: "a", sid2: "b"}
