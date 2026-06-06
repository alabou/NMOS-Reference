# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Copy-on-write publish system + tracker deduplication.

The publish system creates deep-cloned snapshots of all resource maps.
External consumers (NMOS registry) get immutable snapshots that are not
affected by subsequent mutations to the node's internal state.

Tracker deduplication prevents duplicate registry updates by comparing
resource version timestamps.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PublishState:
    """Immutable snapshot of published resources.

    Created by Node.publish() via deep-clone of all resource maps.
    External consumers read this without locks — the node can continue
    mutating its internal maps without affecting published snapshots.
    """
    receivers: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, Any] = field(default_factory=dict)
    flows: dict[str, Any] = field(default_factory=dict)
    senders: dict[str, Any] = field(default_factory=dict)
    published: bool = False


class PublishManager:
    """Manages the copy-on-write publish lifecycle.

    Implements the publish() + get_items() + publish event pattern.
    """

    __slots__ = ("_state", "_event", "_trackers")

    def __init__(self) -> None:
        self._state: PublishState = PublishState()
        self._event: asyncio.Event = asyncio.Event()
        self._trackers: dict[str, Any] = {}  # static_id → NTimeValue

    def publish(
        self,
        receivers: dict[str, Any],
        sources: dict[str, Any],
        flows: dict[str, Any],
        senders: dict[str, Any],
    ) -> None:
        """Create a deep-cloned snapshot of all resource maps.

        Each resource is cloned via .clone() to ensure the snapshot is
        fully independent of the node's internal state. New maps are
        created with cloned values.
        """
        self._state = PublishState(
            receivers={k: v.clone() for k, v in receivers.items()},
            sources={k: v.clone() for k, v in sources.items()},
            flows={k: v.clone() for k, v in flows.items()},
            senders={k: v.clone() for k, v in senders.items()},
            published=True,
        )
        # Non-blocking signal to consumers
        self._event.set()

    def get_items(self) -> PublishState:
        """Return the current published snapshot.

        Safe to call from any coroutine — returns an immutable snapshot.
        """
        return self._state

    @property
    def event(self) -> asyncio.Event:
        """Event that is set when new items are published.

        Consumers can await this to be notified of changes:
            await node.publish_manager.event.wait()
            state = node.publish_manager.get_items()
            node.publish_manager.event.clear()
        """
        return self._event

    @property
    def is_published(self) -> bool:
        return self._state.published

    # --- Tracker deduplication ---

    def check_tracker(self, resource_id: str, version: Any) -> bool:
        """Check if a resource version has already been published.

        Returns True if this is a NEW version (not yet tracked).
        Returns False if this version was already seen (duplicate).

        Used by the NMOS registry client to avoid sending duplicate
        registration updates.
        """
        static_id = resource_id  # caller should pass static ID
        prev = self._trackers.get(static_id)
        if prev is not None and prev == version:
            return False  # already seen
        self._trackers[static_id] = version
        return True  # new version

    def reset_trackers(self) -> None:
        """Clear all tracked versions. Used on reconnect to registry."""
        self._trackers.clear()
        self._event = asyncio.Event()
