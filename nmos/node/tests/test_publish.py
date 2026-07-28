# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for nmos.node.publish — copy-on-write snapshots + tracker dedup."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, dataclass

import pytest

from nmos.node.publish import PublishManager, PublishState
from nmos.types.generated.ndevice import NDeviceValue
from nmos.types.generated.nnode import NNodeValue


@dataclass
class _Cloneable:
    """Stand-in for a resource: the snapshot only ever calls clone() on it."""
    value: int

    def clone(self) -> _Cloneable:
        return _Cloneable(value=self.value)


def _maps(value: int = 1) -> dict[str, dict[str, _Cloneable]]:
    return {
        "receivers": {"r1": _Cloneable(value)},
        "sources": {"s1": _Cloneable(value)},
        "flows": {"f1": _Cloneable(value)},
        "senders": {"x1": _Cloneable(value)},
    }


class TestSnapshotIsolation:
    """A published snapshot must not track later node mutations."""

    def test_starts_unpublished(self) -> None:
        pm = PublishManager()
        assert not pm.is_published
        assert pm.get_items().senders == {}

    def test_publish_marks_published(self) -> None:
        pm = PublishManager()
        pm.publish(**_maps())
        assert pm.is_published
        assert pm.get_items().published

    def test_resources_are_cloned_not_referenced(self) -> None:
        pm = PublishManager()
        maps = _maps(value=1)
        pm.publish(**maps)

        snapshot = pm.get_items()
        assert snapshot.senders["x1"] is not maps["senders"]["x1"]

        # Mutating the node's own object must not reach the snapshot.
        maps["senders"]["x1"].value = 99
        assert snapshot.senders["x1"].value == 1

    def test_adding_a_resource_after_publish_is_not_visible(self) -> None:
        pm = PublishManager()
        maps = _maps()
        pm.publish(**maps)
        snapshot = pm.get_items()

        maps["senders"]["x2"] = _Cloneable(2)
        assert "x2" not in snapshot.senders

    def test_old_snapshot_survives_a_new_publish(self) -> None:
        """The whole point: a consumer mid-cycle keeps a coherent view."""
        pm = PublishManager()
        pm.publish(**_maps(value=1))
        first = pm.get_items()

        pm.publish(**_maps(value=2))
        second = pm.get_items()

        assert first is not second
        assert first.senders["x1"].value == 1
        assert second.senders["x1"].value == 2

    def test_state_is_frozen(self) -> None:
        pm = PublishManager()
        pm.publish(**_maps())
        with pytest.raises(FrozenInstanceError):
            pm.get_items().published = False       # type: ignore[misc]


class TestNodeAndDeviceInSnapshot:
    """node/device belong in the snapshot, not read live by consumers."""

    def test_node_and_device_default_to_none(self) -> None:
        pm = PublishManager()
        pm.publish(**_maps())
        state = pm.get_items()
        assert state.node is None
        assert state.device is None

    def test_node_and_device_are_cloned(self) -> None:
        pm = PublishManager()
        node_value = NNodeValue()
        device_value = NDeviceValue()

        pm.publish(**_maps(), node=node_value, device=device_value)
        state = pm.get_items()

        assert state.node is not None
        assert state.device is not None
        assert state.node is not node_value
        assert state.device is not device_value

    def test_node_snapshot_survives_a_new_publish(self) -> None:
        pm = PublishManager()
        node_value = NNodeValue()

        pm.publish(**_maps(), node=node_value)
        first = pm.get_items()

        pm.publish(**_maps(), node=node_value)
        assert pm.get_items().node is not first.node


class TestPublishEvent:
    """The publish notification, and how it must be cleared."""

    def test_publish_sets_the_event(self) -> None:
        pm = PublishManager()
        assert not pm.event.is_set()
        pm.publish(**_maps())
        assert pm.event.is_set()

    @pytest.mark.asyncio
    async def test_waiter_is_woken_by_publish(self) -> None:
        pm = PublishManager()
        woken = asyncio.Event()

        async def consumer() -> None:
            await pm.event.wait()
            woken.set()

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0)                     # let it park in wait()

        pm.publish(**_maps())

        await asyncio.wait_for(woken.wait(), timeout=1.0)
        await task

    @pytest.mark.asyncio
    async def test_reset_trackers_does_not_strand_a_waiter(self) -> None:
        """reset_trackers must clear the event, not replace the object.

        Replacing it leaves a suspended consumer parked on an object nobody
        will ever set again, so it never learns about any later publish.
        """
        pm = PublishManager()
        woken = asyncio.Event()

        async def consumer() -> None:
            await pm.event.wait()
            woken.set()

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0)                     # park in wait() first

        pm.reset_trackers()                        # must not swap the Event
        pm.publish(**_maps())

        await asyncio.wait_for(woken.wait(), timeout=1.0)
        await task

    def test_reset_trackers_clears_the_event(self) -> None:
        pm = PublishManager()
        pm.publish(**_maps())
        assert pm.event.is_set()

        pm.reset_trackers()
        assert not pm.event.is_set()

    def test_reset_trackers_keeps_the_same_event_object(self) -> None:
        pm = PublishManager()
        event = pm.event
        pm.reset_trackers()
        assert pm.event is event


class TestTrackerDeduplication:
    """check_tracker suppresses re-POSTing an unchanged version."""

    def test_first_version_is_new(self) -> None:
        pm = PublishManager()
        assert pm.check_tracker("s1", (100, 0))

    def test_same_version_is_a_duplicate(self) -> None:
        pm = PublishManager()
        pm.check_tracker("s1", (100, 0))
        assert not pm.check_tracker("s1", (100, 0))

    def test_changed_version_is_new_again(self) -> None:
        pm = PublishManager()
        pm.check_tracker("s1", (100, 0))
        assert pm.check_tracker("s1", (200, 0))

    def test_resources_are_tracked_independently(self) -> None:
        pm = PublishManager()
        pm.check_tracker("s1", (100, 0))
        assert pm.check_tracker("s2", (100, 0))

    def test_reset_trackers_forces_a_full_resend(self) -> None:
        pm = PublishManager()
        pm.check_tracker("s1", (100, 0))
        pm.reset_trackers()
        assert pm.check_tracker("s1", (100, 0))


class TestDefaultState:
    def test_default_state_is_empty_and_unpublished(self) -> None:
        state = PublishState()
        assert state.receivers == {}
        assert state.sources == {}
        assert state.flows == {}
        assert state.senders == {}
        assert state.node is None
        assert state.device is None
        assert not state.published
