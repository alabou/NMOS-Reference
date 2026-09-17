# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The raft backend through the ``RegistryBackend`` seam, and what it costs.

Two things are being checked. The first is that the backend behaves like a
registry -- registers, rejects, deletes, expires, degrades -- through exactly
the four methods the handlers use, with no knowledge of what is underneath.

The second is the number this whole design exists to change: **round trips per
mutation**, read from the metrics buffer rather than inferred from latency.
Every distributed benchmark in this repository runs on loopback, where a round
trip costs almost nothing, so latency cannot tell a design that takes one from
a design that takes three. The count can.

The baseline to beat, measured from the etcd backend in
``test_etcd_round_trips.py``:

    steady-state registration   2
    first registration of Node  3
    heartbeat                   1
    locally-decided rejection   0
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from nmos.raft.node import Role
from nmos.raft.tests._harness import Cluster
from nmos.registry.backend import BackendState, MutationUnavailable
from nmos.registry.metrics import Event
from nmos.registry.tests._fixtures import (
    DEVICE_ID,
    NODE_ID,
    SENDER_ID,
    make_device,
    make_node,
    make_sender,
)
from nmos.registry.types import Body, ResourceType


def _body(raw: dict) -> Body:
    return Body(json.dumps(raw))


async def _started(tmp_path: Path, size: int = 3) -> Cluster:
    cluster = Cluster(size, tmp_path)
    cluster.attach_backends()
    await cluster.start()
    await cluster.elect()
    return cluster


def _leader(cluster: Cluster):  # type: ignore[no-untyped-def]
    index = next(
        m.index for m in cluster.members if m.node.role is Role.LEADER
    )
    return cluster.backends[index], cluster.members[index]


def _spent(backend, before: int) -> int:  # type: ignore[no-untyped-def]
    return backend.metrics.counter(Event.MUTATION).total_units - before


def _units(backend) -> int:  # type: ignore[no-untyped-def]
    return backend.metrics.counter(Event.MUTATION).total_units


class TestRegistrationThroughTheSeam:
    async def test_a_registration_reaches_every_member(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _started(tmp_path)
        try:
            backend, _ = _leader(cluster)
            result = await backend.register(
                ResourceType.NODE, _body(make_node()),
            )
            assert result.ok and result.created

            await cluster.settle(10)
            for member in cluster.members:
                assert member.registry.store.get(
                    ResourceType.NODE, NODE_ID,
                ) is not None
        finally:
            await cluster.close()

    async def test_a_second_registration_is_an_update(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _started(tmp_path)
        try:
            backend, _ = _leader(cluster)
            assert (await backend.register(
                ResourceType.NODE, _body(make_node()),
            )).created is True
            assert (await backend.register(
                ResourceType.NODE, _body(make_node()),
            )).created is False
        finally:
            await cluster.close()

    async def test_the_created_cursor_is_stable_across_updates(
        self, tmp_path: Path,
    ) -> None:
        """A client paging by creation order must not see it move."""
        cluster = await _started(tmp_path)
        try:
            backend, member = _leader(cluster)
            await backend.register(ResourceType.NODE, _body(make_node()))
            first = member.registry.store.get(ResourceType.NODE, NODE_ID)
            assert first is not None
            created = first.created

            await backend.register(ResourceType.NODE, _body(make_node()))
            again = member.registry.store.get(ResourceType.NODE, NODE_ID)
            assert again is not None
            assert again.created == created
            assert again.updated > created
        finally:
            await cluster.close()

    async def test_a_child_whose_parent_is_absent_is_refused(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _started(tmp_path)
        try:
            backend, _ = _leader(cluster)
            result = await backend.register(
                ResourceType.SENDER, _body(make_sender()),
            )
            assert not result.ok
        finally:
            await cluster.close()

    async def test_a_full_tree_registers_and_replicates(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _started(tmp_path)
        try:
            backend, _ = _leader(cluster)
            for resource_type, raw in (
                (ResourceType.NODE, make_node()),
                (ResourceType.DEVICE, make_device()),
                (ResourceType.SENDER, make_sender()),
            ):
                assert (await backend.register(
                    resource_type, _body(raw),
                )).ok

            await cluster.settle(10)
            for member in cluster.members:
                assert member.registry.store.get(
                    ResourceType.SENDER, SENDER_ID,
                ) is not None
        finally:
            await cluster.close()


class TestRoundTripCost:
    """The measurement the whole design is for."""

    async def test_a_registration_on_the_owning_leader_costs_one(
        self, tmp_path: Path,
    ) -> None:
        """One. The etcd backend's best case is two.

        There is no read to fence against -- ownership makes the local view
        authoritative -- and no wait for the commit to come back, because this
        member applied it and resolved the caller from inside that apply.
        """
        cluster = await _started(tmp_path)
        try:
            backend, _ = _leader(cluster)
            await backend.register(ResourceType.NODE, _body(make_node()))
            await backend.register(ResourceType.DEVICE, _body(make_device()))

            before = _units(backend)
            for index in range(8):
                raw = make_sender(f"{index:08x}-0000-4000-8000-00000000000b")
                assert (await backend.register(
                    ResourceType.SENDER, _body(raw),
                )).ok
            assert _spent(backend, before) == 8
        finally:
            await cluster.close()

    async def test_a_nodes_first_registration_also_costs_one(
        self, tmp_path: Path,
    ) -> None:
        """The fused ownership claim: no separate round trip to take the Node.

        The etcd backend pays three here -- a lease grant, the CAS, and the
        wait for its own commit.
        """
        cluster = await _started(tmp_path)
        try:
            backend, _ = _leader(cluster)
            before = _units(backend)
            await backend.register(ResourceType.NODE, _body(make_node()))
            assert _spent(backend, before) == 1
        finally:
            await cluster.close()

    async def test_a_heartbeat_on_the_owner_costs_nothing(
        self, tmp_path: Path,
    ) -> None:
        """Not a lease renewal. Not an entry. Nothing on the wire at all."""
        cluster = await _started(tmp_path)
        try:
            backend, _ = _leader(cluster)
            await backend.register(ResourceType.NODE, _body(make_node()))

            before = _units(backend)
            assert await backend.heartbeat(NODE_ID) is not None
            assert _spent(backend, before) == 0
        finally:
            await cluster.close()

    async def test_a_locally_decided_rejection_costs_nothing(
        self, tmp_path: Path,
    ) -> None:
        """And is still counted, so it cannot flatter the average."""
        cluster = await _started(tmp_path)
        try:
            backend, _ = _leader(cluster)
            counter = backend.metrics.counter(Event.MUTATION)
            seen, spent = counter.count, counter.total_units

            assert not (await backend.register(
                ResourceType.SENDER, _body(make_sender()),
            )).ok

            assert backend.metrics.counter(Event.MUTATION).count == seen + 1
            assert backend.metrics.counter(Event.MUTATION).total_units == spent
        finally:
            await cluster.close()

    async def test_the_headline_ratio_beats_the_etcd_baseline(
        self, tmp_path: Path,
    ) -> None:
        """The acceptance number, stated as a comparison.

        etcd measures 7/3 for the same three registrations
        (``test_etcd_round_trips.py``). One per mutation is the target.
        """
        cluster = await _started(tmp_path)
        try:
            backend, _ = _leader(cluster)
            for resource_type, raw in (
                (ResourceType.NODE, make_node()),
                (ResourceType.DEVICE, make_device()),
                (ResourceType.SENDER, make_sender()),
            ):
                await backend.register(resource_type, _body(raw))

            measured = backend.metrics.round_trips_per_mutation
            assert measured == pytest.approx(1.0)
            assert measured < 7 / 3
        finally:
            await cluster.close()


class TestDeletion:
    async def test_deleting_cascades_across_the_cluster(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _started(tmp_path)
        try:
            backend, _ = _leader(cluster)
            for resource_type, raw in (
                (ResourceType.NODE, make_node()),
                (ResourceType.DEVICE, make_device()),
                (ResourceType.SENDER, make_sender()),
            ):
                await backend.register(resource_type, _body(raw))
            await cluster.settle(5)

            assert await backend.unregister(
                ResourceType.DEVICE, DEVICE_ID,
            ) is True
            await cluster.settle(10)

            for member in cluster.members:
                assert member.registry.store.get(
                    ResourceType.SENDER, SENDER_ID,
                ) is None
                assert member.registry.store.get(
                    ResourceType.NODE, NODE_ID,
                ) is not None
        finally:
            await cluster.close()

    async def test_deleting_something_absent_is_free_and_false(
        self, tmp_path: Path,
    ) -> None:
        """The local store is a complete replica, so "not here" is not a guess."""
        cluster = await _started(tmp_path)
        try:
            backend, _ = _leader(cluster)
            before = _units(backend)
            assert await backend.unregister(
                ResourceType.NODE, NODE_ID,
            ) is False
            assert _spent(backend, before) == 0
        finally:
            await cluster.close()


class TestState:
    async def test_a_healthy_cluster_reports_ready(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _started(tmp_path)
        try:
            backend, _ = _leader(cluster)
            assert backend.state is BackendState.READY
            assert backend.state.accepts_mutations is True
            assert backend.state.serves_queries is True
        finally:
            await cluster.close()

    async def test_losing_quorum_degrades_without_stopping_queries(
        self, tmp_path: Path,
    ) -> None:
        """Refusing reads because writes are impossible makes an outage total."""
        cluster = await _started(tmp_path)
        try:
            backend, member = _leader(cluster)
            await backend.register(ResourceType.NODE, _body(make_node()))
            await cluster.settle(5)

            cluster.network.isolate(member.index)
            await cluster.settle(40)

            with pytest.raises((MutationUnavailable, asyncio.TimeoutError)):
                await backend.register(
                    ResourceType.DEVICE, _body(make_device()),
                )

            assert backend.state.serves_queries is True
            assert member.registry.store.get(
                ResourceType.NODE, NODE_ID,
            ) is not None
        finally:
            await cluster.close()

    async def test_a_single_member_cluster_works(self, tmp_path: Path) -> None:
        """Quorum of one: the degenerate case must still be a real registry."""
        cluster = await _started(tmp_path, size=1)
        try:
            backend = cluster.backends[0]
            assert backend.state is BackendState.READY
            assert (await backend.register(
                ResourceType.NODE, _body(make_node()),
            )).ok
        finally:
            await cluster.close()


class TestExpiry:
    async def test_a_silent_node_is_expired_and_the_removal_replicates(
        self, tmp_path: Path,
    ) -> None:
        """Owner-decided and replicated, not evaluated per member.

        Independent evaluation is how members end up disagreeing about which
        Nodes are alive, with the slowest clock resurrecting what the others
        collected.
        """
        cluster = await _started(tmp_path)
        try:
            backend, member = _leader(cluster)
            await backend.register(ResourceType.NODE, _body(make_node()))
            await backend.register(ResourceType.DEVICE, _body(make_device()))
            await cluster.settle(5)

            # Age the Node past the collection interval without sleeping for it.
            stored = member.registry.store.get(ResourceType.NODE, NODE_ID)
            assert stored is not None
            stored.health -= 120

            expired = await backend.collect_garbage()
            assert expired == 1
            await cluster.settle(10)

            for peer in cluster.members:
                assert peer.registry.store.get(
                    ResourceType.NODE, NODE_ID,
                ) is None
                assert peer.registry.store.get(
                    ResourceType.DEVICE, DEVICE_ID,
                ) is None
        finally:
            await cluster.close()

    async def test_a_member_does_not_expire_nodes_it_does_not_own(
        self, tmp_path: Path,
    ) -> None:
        cluster = await _started(tmp_path)
        try:
            backend, member = _leader(cluster)
            await backend.register(ResourceType.NODE, _body(make_node()))
            await cluster.settle(5)

            other_index = next(
                m.index for m in cluster.members if m.index != member.index
            )
            other = cluster.backends[other_index]
            stale = cluster.members[other_index].registry.store.get(
                ResourceType.NODE, NODE_ID,
            )
            assert stale is not None
            stale.health -= 120

            assert await other.collect_garbage() == 0
            await cluster.settle(5)
            assert member.registry.store.get(
                ResourceType.NODE, NODE_ID,
            ) is not None
        finally:
            await cluster.close()
