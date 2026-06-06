# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Generic resource store with static-ID keying.

Provides a single generic class reused by all resource types, replacing
the need for per-resource-type Get*PtrFromId / Get*PtrFromNullableId /
Get*IdFromStaticId methods.

All map keys are **static IDs** (uniqueId=0). Lookups accept any form
of ID (static or dynamic) and auto-convert to static before lookup.
"""

from __future__ import annotations

from typing import Generic, Iterator, TypeVar

from nmos.errors import NotFound
from nmos.uuid import update_resource_unique_id

T = TypeVar("T")


def to_static_id(any_id: str) -> str:
    """Convert any resource ID (static or dynamic) to its static form.

    Static IDs have uniqueId=0 — they identify the resource slot and
    never change for the lifetime of a resource.
    """
    return update_resource_unique_id(any_id, 0)


class ResourceStore(Generic[T]):
    """Typed resource map keyed by static resource IDs.

    Resources are stored by their static ID (uniqueId=0), which never
    changes. The resource object itself contains the dynamic ID (with
    a random uniqueId) that changes on every update.

    This replaces per-resource-type getter methods:
        GetReceiverPtrFromId(id)            → store.get(id)
        GetReceiverPtrFromNullableId(nid)   → caller unwraps nullable, then store.get()
        GetReceiverIdFromStaticId(sid)      → store.get_dynamic_id(sid)
    """

    __slots__ = ("_items",)

    def __init__(self) -> None:
        self._items: dict[str, T] = {}

    def get(self, any_id: str) -> T | None:
        """Lookup resource by any form of ID (static or dynamic).

        Returns None if not found. The ID is auto-converted to static
        form before lookup.
        """
        return self._items.get(to_static_id(any_id))

    def get_or_raise(self, any_id: str) -> T:
        """Lookup resource, raising NotFound if absent."""
        item = self.get(any_id)
        if item is None:
            raise NotFound(f"resource not found: {any_id}")
        return item

    def put(self, static_id: str, item: T) -> None:
        """Store a resource by its static ID."""
        self._items[static_id] = item

    def remove(self, any_id: str) -> T | None:
        """Remove and return a resource. Returns None if not found."""
        return self._items.pop(to_static_id(any_id), None)

    def contains(self, any_id: str) -> bool:
        """Check if a resource exists by any form of ID."""
        return to_static_id(any_id) in self._items

    def snapshot(self) -> dict[str, T]:
        """Shallow copy of the internal dict — used by publish system.

        The returned dict is a new object, so mutations to the store
        after snapshot don't affect the snapshot (copy-on-write publish).
        """
        return dict(self._items)

    def __iter__(self) -> Iterator[tuple[str, T]]:
        """Iterate over (static_id, resource) pairs."""
        return iter(self._items.items())

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, any_id: str) -> bool:
        return self.contains(any_id)
