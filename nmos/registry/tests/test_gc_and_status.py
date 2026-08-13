# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for the background garbage-collection and status-reporting tasks.

The store-level expiry rules are covered in ``test_store.py``; these check the
task wrapper — that it runs on its clock, survives a failing pass, publishes
removal grains through the registry, and stops when the dispatch group is
cancelled.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from nmos.registry.decode import decode_resource
from nmos.registry.gc import run_garbage_collection, run_status_reporting
from nmos.registry.registry import Registry
from nmos.registry.store import RegistryStore, health_now
from nmos.registry.subscriptions import SubscriptionManager
from nmos.registry.tests._fixtures import (
    NODE_ID,
    make_device,
    make_node,
    make_sender,
    make_source,
    make_flow,
)
from nmos.registry.types import Body, ResourceType


class FakeDispatchGroup:
    """Minimal stand-in for ``nmos.tasks.DispatchGroup``.

    The tasks only ever read ``is_done``, so a full DispatchGroup would add a
    task-group lifecycle these tests do not exercise.
    """

    def __init__(self) -> None:
        self.is_done = False

    def cancel(self) -> None:
        self.is_done = True


@pytest.fixture
def registry() -> Registry:
    registry = Registry(
        RegistryStore(gc_interval=12.0, forget_interval=60.0),
        query_id="8c4d1e70-6b3a-4f52-9d81-2e7c5a0b3f14",
    )
    registry.attach_subscriptions(SubscriptionManager(registry))
    return registry


def seed_tree(registry: Registry) -> None:
    for resource_type, raw in (
        (ResourceType.NODE, make_node()),
        (ResourceType.DEVICE, make_device()),
        (ResourceType.SOURCE, make_source()),
        (ResourceType.FLOW, make_flow()),
        (ResourceType.SENDER, make_sender()),
    ):
        typed = decode_resource(resource_type, raw)
        assert registry.register(resource_type, Body.from_data(raw)).ok


def age_everything(registry: Registry, seconds: int) -> None:
    """Backdate every resource's health, simulating missed heartbeats."""
    stale = health_now() - seconds
    for resource_type in ResourceType:
        for resource in registry.store.iter_extant(resource_type):
            resource.health = stale


# ---------------------------------------------------------------------------
# Garbage collection task
# ---------------------------------------------------------------------------

class TestGarbageCollectionTask:
    async def test_collects_an_expired_node(self, registry: Registry) -> None:
        seed_tree(registry)
        age_everything(registry, 13)

        dg = FakeDispatchGroup()
        task = asyncio.create_task(run_garbage_collection(dg, registry))
        await asyncio.sleep(1.4)  # one tick
        dg.cancel()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        for resource_type in ResourceType:
            assert registry.store.count_extant(resource_type) == 0

    async def test_leaves_a_healthy_node_alone(self, registry: Registry) -> None:
        seed_tree(registry)

        dg = FakeDispatchGroup()
        task = asyncio.create_task(run_garbage_collection(dg, registry))
        await asyncio.sleep(1.4)
        dg.cancel()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        assert registry.store.count_extant(ResourceType.NODE) == 1

    async def test_collection_publishes_removal_grains(
        self, registry: Registry,
    ) -> None:
        """A subscriber must learn that a collected Node's Sender is gone.

        Without this, a Controller keeps showing a Sender whose Node was
        unplugged, which is exactly the stale-state problem garbage collection
        exists to prevent.
        """
        seed_tree(registry)

        subscription, _created = registry.subscriptions.create_or_match(
            resource_path="/senders",
            params={},
            max_update_rate_ms=0,
            persist=True,
            secure=False,
            authorization=False,
            host="localhost",
            ws_scheme="ws",
            ws_host="localhost:8448",
        )
        connection = registry.subscriptions.connect(subscription)
        connection.drain()  # discard the sync burst

        age_everything(registry, 13)
        assert registry.collect_garbage() == 5

        pending = connection.drain()
        assert len(pending) == 1
        assert pending[0].pre is not None
        assert pending[0].post is None, "must be reported as a removal"

    async def test_survives_a_failing_pass(
        self, registry: Registry, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """One bad pass must not kill the collector.

        It is the only thing standing between an ungracefully-disconnected
        Node and a permanently stale registry, so it has to try again on the
        next tick rather than die silently.
        """
        calls = {"count": 0}
        original = RegistryStore.collect_garbage

        # Patched on the class, not the instance: RegistryStore defines
        # __slots__, so it has no instance __dict__ to hold an override.
        def flaky(self: RegistryStore) -> list:  # type: ignore[type-arg]
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("simulated collection failure")
            return original(self)

        monkeypatch.setattr(RegistryStore, "collect_garbage", flaky)

        dg = FakeDispatchGroup()
        task = asyncio.create_task(run_garbage_collection(dg, registry))
        await asyncio.sleep(2.4)  # two ticks
        dg.cancel()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        assert calls["count"] >= 2, "collector stopped after the failure"

    async def test_stops_when_cancelled(self, registry: Registry) -> None:
        dg = FakeDispatchGroup()
        task = asyncio.create_task(run_garbage_collection(dg, registry))
        await asyncio.sleep(0.1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert task.done()


# ---------------------------------------------------------------------------
# Status reporting task
# ---------------------------------------------------------------------------

class TestStatusReporting:
    async def test_emits_the_status_line(
        self, registry: Registry, caplog: pytest.LogCaptureFixture,
    ) -> None:
        seed_tree(registry)

        dg = FakeDispatchGroup()
        with caplog.at_level(logging.INFO, logger="nmos.registry.gc"):
            task = asyncio.create_task(
                run_status_reporting(dg, registry, interval=0.2),
            )
            await asyncio.sleep(0.5)
            dg.cancel()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        lines = [r.getMessage() for r in caplog.records]
        assert any("the registry contains" in line for line in lines), lines
        assert any("5 resources (1 nodes, 1 devices" in line for line in lines), lines

    async def test_zero_interval_disables_reporting(
        self, registry: Registry, caplog: pytest.LogCaptureFixture,
    ) -> None:
        dg = FakeDispatchGroup()
        with caplog.at_level(logging.INFO, logger="nmos.registry.gc"):
            await run_status_reporting(dg, registry, interval=0.0)

        lines = [r.getMessage() for r in caplog.records]
        assert any("disabled" in line for line in lines), lines
        assert not any("the registry contains" in line for line in lines)

    def test_status_line_prefix_matches_nmos_cpp(
        self, registry: Registry,
    ) -> None:
        """nmos-cpp emits ``"At " << make_version(tai_now()) << ", the registry
        contains " << put_resources_statistics(resources)``."""
        line = registry.status_line()
        assert line.startswith("At ")
        assert ", the registry contains " in line
        assert line.endswith(" non-extant resources")

    def test_status_line_counts_subscriptions_and_grains(
        self, registry: Registry,
    ) -> None:
        """Subscriptions and grains appear in the per-kind list and in the
        leading total, matching nmos-cpp's ``by_type.count(true)``."""
        seed_tree(registry)

        subscription, _created = registry.subscriptions.create_or_match(
            resource_path="/senders",
            params={},
            max_update_rate_ms=0,
            persist=True,
            secure=False,
            authorization=False,
            host="localhost",
            ws_scheme="ws",
            ws_host="localhost:8448",
        )
        registry.subscriptions.connect(subscription)

        line = registry.status_line()
        assert "1 subscriptions" in line
        assert "1 grains" in line
        # 5 resources + 1 subscription + 1 grain.
        assert "7 resources (" in line
