# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""What a mutation costs on the wire, counted rather than inferred.

Every distributed benchmark in this repository runs on loopback, where a round
trip costs almost nothing. That makes latency a poor witness: a design that
takes three network traversals per registration and one that takes one look
nearly identical here, and diverge by a factor of three on a real switch. So
the traversals are counted directly, and this file pins what the etcd backend
actually costs -- which is the baseline any replacement has to beat.

A "round trip" is a wait that cannot complete without the network, including
waiting for our own commit to come back down the watch stream. No request is
sent for that one, but the answer cannot be given until it arrives, so leaving
it out would report the fast path as costing one traversal when it costs two.
"""

from __future__ import annotations

import pytest

from nmos.registry.metrics import Event
from nmos.registry.tests._fixtures import (
    NODE_ID,
    make_device,
    make_node,
    make_sender,
)
from nmos.registry.tests.test_etcd_backend import _start_backend
from nmos.registry.types import Body, ResourceType

pytestmark = pytest.mark.e2e


def _body(raw: dict) -> Body:
    import json

    return Body(text=json.dumps(raw), data=raw)


async def test_a_steady_state_registration_costs_two_traversals(
    etcd_endpoint: str, namespace: str,
) -> None:
    """One CAS, plus waiting for it to return through the watch.

    This is the etcd backend at its best: the fast path hits, so there is no
    linearizable read and no retry. Two is therefore the floor for this design,
    not its average -- and it is the number the native backend's single
    quorum round has to be measured against.
    """
    registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        # The Node's first registration also grants its lease, so it is not
        # representative; the steady state is the resources that follow.
        await backend.register(ResourceType.NODE, _body(make_node()))
        await backend.register(ResourceType.DEVICE, _body(make_device()))

        before = backend.metrics.counter(Event.MUTATION).total_units
        for index in range(8):
            raw = make_sender(f"{'%08x' % index}-0000-4000-8000-000000000000")
            result = await backend.register(ResourceType.SENDER, _body(raw))
            assert result.ok, result
        spent = backend.metrics.counter(Event.MUTATION).total_units - before

        assert spent == 16, f"8 registrations cost {spent} traversals, not 16"
    finally:
        await backend.close()


async def test_the_first_registration_of_a_node_pays_for_its_lease(
    etcd_endpoint: str, namespace: str,
) -> None:
    """Three, not two -- and only once per Node, because the lease is memoised.

    Worth pinning separately: charging every registration for a grant that
    happens once per Node would overstate the steady-state cost, and a change
    that stopped memoising would otherwise show up only as a benchmark
    regression nobody could explain.
    """
    registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        with_lease = backend.metrics.counter(Event.MUTATION).total_units
        await backend.register(ResourceType.NODE, _body(make_node()))
        first = backend.metrics.counter(Event.MUTATION).total_units - with_lease
        assert first == 3, f"first Node registration cost {first}, not 3"

        before = backend.metrics.counter(Event.MUTATION).total_units
        await backend.register(ResourceType.NODE, _body(make_node()))
        again = backend.metrics.counter(Event.MUTATION).total_units - before
        assert again == 2, f"second Node registration cost {again}, not 2"
    finally:
        await backend.close()


async def test_a_heartbeat_costs_one_traversal(
    etcd_endpoint: str, namespace: str,
) -> None:
    """The lease renewal, and nothing else -- no write, no fence.

    The property ``nmos/etcd/lease.py`` argues for: 100 Nodes at the 5 s
    default must not become 100 consensus writes per second.
    """
    registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        await backend.register(ResourceType.NODE, _body(make_node()))

        before = backend.metrics.counter(Event.MUTATION).total_units
        assert await backend.heartbeat(NODE_ID) is not None
        spent = backend.metrics.counter(Event.MUTATION).total_units - before

        assert spent == 1, f"heartbeat cost {spent} traversals, not 1"
    finally:
        await backend.close()


async def test_a_locally_decided_rejection_costs_nothing(
    etcd_endpoint: str, namespace: str,
) -> None:
    """A Sender whose Device is absent is refused without touching etcd.

    Recorded as zero rather than omitted: a backend whose free rejections
    vanished from the denominator could improve its reported average by
    refusing more requests.
    """
    registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        before = backend.metrics.counter(Event.MUTATION)
        seen, spent = before.count, before.total_units

        result = await backend.register(ResourceType.SENDER, _body(make_sender()))
        assert not result.ok

        after = backend.metrics.counter(Event.MUTATION)
        assert after.count == seen + 1, "the rejection was not counted at all"
        assert after.total_units == spent, "a local rejection used the network"
    finally:
        await backend.close()


async def test_the_headline_ratio_reports_the_measured_cost(
    etcd_endpoint: str, namespace: str,
) -> None:
    """``round_trips_per_mutation`` is what the acceptance criteria read."""
    registry, backend = await _start_backend(etcd_endpoint, namespace)
    try:
        await backend.register(ResourceType.NODE, _body(make_node()))
        await backend.register(ResourceType.DEVICE, _body(make_device()))
        await backend.register(ResourceType.SENDER, _body(make_sender()))

        measured = backend.metrics.round_trips_per_mutation
        # Three registrations: 3 + 2 + 2 traversals.
        assert measured == pytest.approx(7 / 3)
    finally:
        await backend.close()
