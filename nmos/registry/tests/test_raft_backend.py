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
    rejection the body decides  0
    rejection the store decides 1
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from pathlib import Path

import pytest

from nmos.raft.errors import RaftInvariantViolated, RaftUnexpectedError
from nmos.raft.node import Role
from nmos.raft.persist import PersistentState, TermStore
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
from nmos.registry.raft_backend import RaftRegistryBackend
from nmos.registry.types import Body, ResourceType


def _body(raw: dict[str, Any]) -> Body:
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


def _break(member) -> None:  # type: ignore[no-untyped-def]
    """Put a member beyond Raft: applied past what it has committed.

    The first thing ``_check_applied_within_committed`` refuses, found on the
    next apply.
    """
    member.node._machine._last_applied = member.node.commit_index + 100
    member.node._schedule_apply()


def _spent(backend: RaftRegistryBackend, before: int) -> int:
    return backend.metrics.counter(Event.MUTATION).total_units - before


def _units(backend: RaftRegistryBackend) -> int:
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


class TestCursorReservation:
    """A cursor whose reservation cannot be written never reaches the log."""

    async def test_a_registration_whose_cursor_cannot_be_reserved_is_a_503(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Retryable, and with nothing proposed for the retry to duplicate.

        One member, so no election can fall inside the window in which every
        write fails: the refusal measured is the reservation's alone.
        """
        cluster = await _started(tmp_path, size=1)
        try:
            backend, member = _leader(cluster)

            def refuse(_store: TermStore, _state: PersistentState) -> None:
                raise OSError(28, "No space left on device")

            monkeypatch.setattr(TermStore, "save", refuse)
            with pytest.raises(
                MutationUnavailable, match="could not reserve paging cursors",
            ):
                await backend.register(ResourceType.NODE, _body(make_node()))
            monkeypatch.undo()

            assert member.registry.store.get(ResourceType.NODE, NODE_ID) is None
            retried = await backend.register(
                ResourceType.NODE, _body(make_node()),
            )
            assert retried.ok and retried.created
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

    async def test_a_rejection_the_body_decides_costs_nothing(
        self, tmp_path: Path,
    ) -> None:
        """And is still counted, so it cannot flatter the average.

        A Sender with no ``device_id`` is malformed whatever the store holds,
        so nothing needs confirming before saying so.
        """
        cluster = await _started(tmp_path)
        try:
            backend, _ = _leader(cluster)
            counter = backend.metrics.counter(Event.MUTATION)
            seen, spent = counter.count, counter.total_units

            raw = make_sender()
            del raw["device_id"]
            assert not (await backend.register(
                ResourceType.SENDER, _body(raw),
            )).ok

            assert backend.metrics.counter(Event.MUTATION).count == seen + 1
            assert backend.metrics.counter(Event.MUTATION).total_units == spent
        finally:
            await cluster.close()

    async def test_a_rejection_the_store_decides_costs_one_read_barrier(
        self, tmp_path: Path,
    ) -> None:
        """A missing parent is only as true as the store it was read from.

        So before a Sender whose Device is absent is refused, this member
        learns a read index and applies through it -- one quorum round on the
        leader -- and the round is counted. It once cost nothing, on the
        belief that an owner's store is authoritative; an owner's store is
        current only as of what it has applied, and a member behind it
        answered 400s the protocol forbids retrying (see
        ``test_forwarding_conformance``, "A member behind what is committed").
        """
        cluster = await _started(tmp_path)
        try:
            backend, _ = _leader(cluster)
            counter = backend.metrics.counter(Event.MUTATION)
            seen, spent = counter.count, counter.total_units

            assert not (await backend.register(
                ResourceType.SENDER, _body(make_sender()),
            )).ok

            assert backend.metrics.counter(Event.MUTATION).count == seen + 1
            assert backend.metrics.counter(Event.MUTATION).total_units == spent + 1
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

    async def test_deleting_something_absent_is_false_once_current(
        self, tmp_path: Path,
    ) -> None:
        """"Not here" is true only of a store that is current.

        The local store is a complete replica only of what this member has
        applied, so the 404 is given after a read barrier -- one quorum round
        on the leader -- and not before (see ``test_forwarding_conformance``,
        "A member behind what is committed").
        """
        cluster = await _started(tmp_path)
        try:
            backend, _ = _leader(cluster)
            before = _units(backend)
            assert await backend.unregister(
                ResourceType.NODE, NODE_ID,
            ) is False
            assert _spent(backend, before) == 1
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

    async def test_a_member_that_stops_itself_reports_stopping(
        self, tmp_path: Path,
    ) -> None:
        """A member stopped on a broken invariant is going away (``RaftNode._fail``)."""
        cluster = await _started(tmp_path)
        try:
            backend, member = _leader(cluster)
            # Not `is READY`: that narrows `state` for the checker, which then
            # takes the change this test is about for an impossibility.
            assert backend.state.accepts_mutations, backend.state
            _break(member)
            await cluster.settle(2)

            assert backend.state is BackendState.STOPPING, (
                f"a member that stopped on a broken invariant reported "
                f"{backend.state.name}: its Registration API would take "
                f"mutations the member can never apply"
            )
            assert backend.state.accepts_mutations is False
        finally:
            await cluster.close()

    async def test_the_process_ends_when_its_member_stops(
        self, tmp_path: Path,
    ) -> None:
        """What ``main`` runs in its task group: the member's failure ends it.

        Raised into the group, the failure cancels every other task, is logged
        and exits the process with status 1 -- the status a service manager
        restarts on.
        """
        from nmos_registry import _exit_when_the_member_stops

        cluster = await _started(tmp_path)
        try:
            _, member = _leader(cluster)
            watching = asyncio.create_task(
                _exit_when_the_member_stops(member.node),
            )
            await cluster.settle(2)
            assert not watching.done()

            _break(member)
            with pytest.raises(RaftInvariantViolated):
                await asyncio.wait_for(watching, 5.0)
        finally:
            await cluster.close()

    async def test_the_process_ends_when_its_member_stops_on_an_unexpected_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The other way a member stops: an exception nothing in it expected.

        Same exit, same status; the backend reports STOPPING meanwhile so its
        Registration API answers 503 rather than taking mutations the member
        can never apply.
        """
        from nmos_registry import _exit_when_the_member_stops

        cluster = await _started(tmp_path)
        try:
            backend, member = _leader(cluster)
            watching = asyncio.create_task(
                _exit_when_the_member_stops(member.node),
            )
            await cluster.settle(2)
            assert not watching.done()

            def planted() -> None:
                raise RuntimeError("planted: a tick that raises")

            monkeypatch.setattr(member.node, "_tick", planted)
            with pytest.raises(RaftUnexpectedError):
                await asyncio.wait_for(watching, 5.0)
            assert backend.state is BackendState.STOPPING
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
