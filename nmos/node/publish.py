# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Copy-on-write publish system + tracker deduplication.

The publish system creates deep-cloned snapshots of every resource the
registry needs. External consumers (NMOS registry) read a snapshot that is
unaffected by subsequent mutations to the node's internal state.

No lock is involved, and none is needed. publish() builds the entire new
snapshot first and only then rebinds it, and it does so without awaiting, so
a consumer either sees the previous snapshot or the new one -- never a
half-built one. Consumers that need a consistent view must therefore read
get_items() ONCE and use that object throughout; calling it again mid-cycle,
or reading live node state alongside it, reintroduces exactly the
inconsistency the snapshot exists to prevent.

Tracker deduplication prevents duplicate registry updates by comparing
resource version timestamps.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PublishState:
    """Snapshot of published resources.

    Created by Node.publish() via deep-clone of every resource. External
    consumers read this without locks — the node can continue mutating its
    internal maps without affecting a snapshot already handed out.

    Frozen so the fields cannot be rebound. That is as far as the language
    goes: the dicts and the resource objects inside them are ordinary mutable
    objects, and every holder of this snapshot shares them. Treat the whole
    structure as read-only — mutating anything reachable from here corrupts
    the snapshot for every other consumer.

    ``node`` and ``device`` are part of the snapshot for the same reason the
    maps are: a consumer that reached for the Node's live attributes instead
    would be mixing two different points in time into one registry update.
    """
    receivers: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, Any] = field(default_factory=dict)
    flows: dict[str, Any] = field(default_factory=dict)
    senders: dict[str, Any] = field(default_factory=dict)
    node: Any = None            # NNodeValue | None
    device: Any = None          # NDeviceValue | None
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
        node: Any = None,
        device: Any = None,
    ) -> None:
        """Create a deep-cloned snapshot of every registrable resource.

        Each resource is cloned via .clone() so the snapshot is fully
        independent of the node's internal state. New maps are created with
        cloned values.

        The whole snapshot is built before it is rebound, and this function
        never awaits, so a consumer reading get_items() concurrently sees
        either the previous snapshot or this one, complete — which is what
        makes a lock unnecessary here.
        """
        self._state = PublishState(
            receivers={k: v.clone() for k, v in receivers.items()},
            sources={k: v.clone() for k, v in sources.items()},
            flows={k: v.clone() for k, v in flows.items()},
            senders={k: v.clone() for k, v in senders.items()},
            node=node.clone() if node is not None else None,
            device=device.clone() if device is not None else None,
            published=True,
        )
        # Non-blocking signal to consumers
        self._event.set()

    def get_items(self) -> PublishState:
        """Return the current published snapshot.

        Safe to call from any coroutine. Call it once per cycle and use the
        returned object throughout: two calls can return two different
        snapshots, and mixing resources from both produces a registry update
        that describes no state the node was ever actually in.
        """
        return self._state

    @property
    def event(self) -> asyncio.Event:
        """Event that is set when new items are published.

        Consumers await this to be notified of changes. Clear it BEFORE
        waiting, never after reading:

            node.publish_manager.event.clear()
            await node.publish_manager.event.wait()
            state = node.publish_manager.get_items()

        Clearing after the read would discard a publish that landed in
        between, and that notification is the only thing that would have
        told the consumer to look again.
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
        """Forget every tracked version, so the next cycle re-sends everything.

        Used when the registry connection has to start over. The pending
        publish notification is dropped along with the trackers: everything
        will be re-sent regardless, so the wakeup carries no information.

        The event is cleared rather than replaced. Replacing it would leave
        any consumer already suspended in ``event.wait()`` parked on an object
        nobody will ever set again.
        """
        self._trackers.clear()
        self._event.clear()
