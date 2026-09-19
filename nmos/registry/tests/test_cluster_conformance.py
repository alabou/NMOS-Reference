# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""What every distributed backend must do with a genuine three-member quorum.

    pytest nmos/registry/tests/test_cluster_conformance.py -m e2e

These are the claims that are about *consensus*, not about any one storage
layer: three registries share one view, disjoint Nodes do not contend,
three members tolerate one failure, and losing quorum stops writes without
stopping reads. Every distributed backend has to satisfy all four, by the same
argument and with the same observable behaviour, so they are written once and
parameterised over ``BACKENDS`` rather than written per backend.

Nothing here touches a revision, a key or an envelope. Those are etcd's model;
a conformance test that asserted on them would be asserting that the second
backend was etcd, which is the opposite of what it is for. Everything goes
through ``RegistryBackend`` and the local store.

This replaces the hand-rolled three-member rig that used to live in
``test_etcd_cluster_e2e.py``; the rig now lives in ``rigs/`` so both backends
drive an identical one.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from nmos.registry.backend import BackendState
from nmos.registry.decode import decode_resource
from nmos.registry.tests._fixtures import (
    NODE_ID,
    NODE_ID_2,
    make_device,
    make_node,
    make_sender,
)
from nmos.registry.tests.rigs.raft_rig import RIG_TIMING
from nmos.registry.tests.rigs import (
    BACKENDS,
    ClusterRig,
    RigUnavailable,
    make_rig,
)
from nmos.registry.tests.test_etcd_backend import _eventually, build_registry
from nmos.registry.types import Body, ResourceType

pytestmark = pytest.mark.e2e


@pytest.fixture
async def rig(request: Any, tmp_path: Path) -> AsyncIterator[ClusterRig]:
    """A three-member cluster of whichever backend is being exercised.

    Function-scoped deliberately. ``asyncio_mode = "auto"`` gives every test its
    own event loop, and an in-process backend's listeners and timers live on
    that loop -- a session-scoped rig would outlive the loop it was built on and
    fail in a way that pointed at the wrong test.
    """
    backend = request.param
    try:
        created = make_rig(backend, size=3, root=tmp_path)
    except RigUnavailable as exc:
        pytest.skip(str(exc))

    await created.start_all()
    try:
        yield created
    finally:
        await created.stop_all()


def _parameterise(fn: Any) -> Any:
    return pytest.mark.parametrize("rig", BACKENDS, indirect=True)(fn)


async def _register(backend: Any, resource_type: ResourceType, raw: dict) -> Any:
    decode_resource(resource_type, raw)
    return await backend.register(resource_type, Body.from_data(raw))


def _cluster_state(backends: list[Any]) -> str:
    """Role, term and leader of every member, for a refusal message.

    A refusal that says only "could not commit" leaves the reader to guess
    whether the cluster had no leader, had two, or had one that could not reach
    a quorum -- and those want different fixes.
    """
    parts = []
    for backend in backends:
        node = getattr(backend, "_node", None)
        if node is None:
            continue
        parts.append(
            f"m{node.index}:{node.role.value[:4]} t{node.term} "
            f"lead={node.leader} commit={node.commit_index} "
            f"applied={node.last_applied} quorum={node.has_quorum}",
        )
    return "  ".join(parts)


def _namespace() -> str:
    return f"/nmos-test/conformance/{uuid.uuid4().hex[:8]}"


async def _whole_cluster(
    rig: ClusterRig, namespace: str, **overrides: Any,
) -> tuple[list[Any], list[Any]]:
    """A registry and a started backend for *every* member.

    Every member, even when the test only drives one of them. With etcd the
    other members exist as separate processes whether a registry is attached
    or not; with raft the member *is* the backend, so a test that built one
    backend would be testing a one-member cluster -- which has no quorum, no
    leader, and nothing to say about the three-member claims this file is for.
    """
    registries = [build_registry() for _ in range(rig.size)]
    backends = list(await asyncio.gather(*(
        rig.backend_for(index, registry, namespace, **overrides)
        for index, registry in enumerate(registries)
    )))
    return registries, backends


@_parameterise
async def test_three_registries_share_one_view(
    rig: ClusterRig, request: Any,
) -> None:
    """Register on member 0, read it back on members 1 and 2.

    Identical content *and* identical cursors: the latter is what makes a paged
    Query answer the same whichever member serves it, and it is the property a
    backend that allocated cursors locally would quietly break.
    """
    namespace = _namespace()
    registries, backends = await _whole_cluster(rig, namespace)

    try:
        assert all(b.state is BackendState.READY for b in backends)

        node, device, sender = make_node(), make_device(), make_sender()
        for resource_type, raw in (
            (ResourceType.NODE, node),
            (ResourceType.DEVICE, device),
            (ResourceType.SENDER, sender),
        ):
            assert (await _register(backends[0], resource_type, raw)).ok

        for registry in registries[1:]:
            await _eventually(
                lambda r=registry: r.store.get(
                    ResourceType.SENDER, sender["id"],
                ) is not None,
                timeout=20.0,
            )

        reference = registries[0].store.get(ResourceType.SENDER, sender["id"])
        assert reference is not None
        for registry in registries[1:]:
            other = registry.store.get(ResourceType.SENDER, sender["id"])
            assert other is not None
            assert other.raw == reference.raw
            assert other.created == reference.created
            assert other.updated == reference.updated
    finally:
        for backend in backends:
            await backend.close()


@_parameterise
async def test_the_cluster_reports_the_failure_tolerance_it_promises(
    rig: ClusterRig, request: Any,
) -> None:
    """Three members, one tolerated failure -- asserted, not assumed."""
    assert rig.size == 3
    assert rig.quorum == 2
    assert rig.failures_tolerated == 1


@_parameterise
async def test_registries_can_write_concurrently_to_different_nodes(
    rig: ClusterRig, request: Any,
) -> None:
    """Different Nodes are disjoint, so there is no cross-member contention."""
    namespace = _namespace()
    registry_a, registry_b = build_registry(), build_registry()
    backend_a, backend_b = await asyncio.gather(
        rig.backend_for(0, registry_a, namespace),
        rig.backend_for(1, registry_b, namespace),
    )

    try:
        results = await asyncio.gather(
            _register(backend_a, ResourceType.NODE, make_node(NODE_ID)),
            _register(backend_b, ResourceType.NODE, make_node(NODE_ID_2)),
        )
        assert all(result.ok and result.created for result in results)

        for registry in (registry_a, registry_b):
            for node_id in (NODE_ID, NODE_ID_2):
                await _eventually(
                    lambda r=registry, n=node_id: r.store.get(
                        ResourceType.NODE, n,
                    ) is not None,
                    timeout=20.0,
                )
    finally:
        await backend_a.close()
        await backend_b.close()


@pytest.mark.parametrize("rig", ["etcd"], indirect=True)
async def test_losing_the_local_member_fails_over_to_the_others(
    rig: ClusterRig, request: Any,
) -> None:
    """etcd only, and the exception proves the rule rather than bending it.

    This asserts that a registry *outlives* the storage member it prefers:
    killing etcd member 0 costs the registry in front of it a failover to
    another endpoint, not its existence. That is a property of etcd's
    two-tier topology -- registry as client of a separate cluster -- and raft
    has no counterpart, because there the member and the registry are one
    object and killing it kills both.

    Written as an etcd-only test rather than smuggled into the shared suite
    with a conditional, so the divergence is visible in the parameterisation
    instead of buried in a branch. The *claim* both backends share -- three
    members tolerate one failure -- is covered by
    ``test_quorum_loss_stops_writes_but_not_reads`` and by the topology test
    above.
    """
    registry = build_registry()
    backend = await rig.backend_for(0, registry, _namespace())

    try:
        node = make_node()
        assert (await _register(backend, ResourceType.NODE, node)).ok

        await rig.kill(0)
        await asyncio.sleep(1.0)

        # Query is unaffected -- it never touched the storage layer.
        assert registry.store.get(ResourceType.NODE, node["id"]) is not None

        device = make_device()
        await _eventually(
            lambda: backend.state in (
                BackendState.READY, BackendState.DEGRADED,
            ),
            timeout=10.0,
        )
        for _ in range(20):
            try:
                result = await _register(backend, ResourceType.DEVICE, device)
                if result.ok:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.5)
        else:
            pytest.fail(
                "no write succeeded after losing the local member; a "
                "3-member cluster must tolerate one failure",
            )

        assert registry.store.get(ResourceType.DEVICE, device["id"]) is not None
    finally:
        await backend.close()


@_parameterise
async def test_quorum_loss_stops_writes_but_not_reads(
    rig: ClusterRig, request: Any,
) -> None:
    """Beyond what the cluster promises, writes must stop and reads must not.

    Committing without quorum is exactly what consensus exists to prevent; and
    refusing reads because writes are impossible would turn a partial outage
    into a total one, which ``BackendState`` is explicit about.
    """
    registries, backends = await _whole_cluster(
        rig, _namespace(), rpc_timeout=1.0, mutation_timeout=2.0,
    )
    registry, backend = registries[0], backends[0]

    try:
        node = make_node()
        assert (await _register(backend, ResourceType.NODE, node)).ok

        await rig.lose_quorum()
        await asyncio.sleep(1.0)

        with pytest.raises(Exception):
            await _register(backend, ResourceType.DEVICE, make_device())

        # The cached view keeps serving. Refusing reads because writes are
        # impossible turns a partial outage into a total one.
        assert registry.store.get(ResourceType.NODE, node["id"]) is not None
        assert backend.state.serves_queries is True
    finally:
        await backends[0].close()


async def _drive_a_moving_node(
    rig: ClusterRig, pick: Any, turns: int, label: str,
) -> None:
    """Register a Node once, then talk to whichever member ``pick`` chooses.

    Ownership means the *first* registration decides the owner, so every call
    afterwards that lands elsewhere must be **forwarded** to it -- registrations
    and heartbeats alike. ``raft_backend.heartbeat`` forwards too, and a Node
    beats every few seconds, which makes this the busiest forwarding path in a
    real deployment.

    That path is covered by nothing else. Both in-memory harnesses refuse it on
    purpose -- ``_harness.py`` raises "the memory transport does not correlate
    requests", and the Rust fabric's ``request`` is unimplemented -- so the
    whole consensus suite has never exercised a correlated request. Only a rig
    on the real transport can, which is this one.
    """
    namespace = _namespace()
    registries, backends = await _whole_cluster(rig, namespace)
    try:
        assert all(b.state is BackendState.READY for b in backends)

        assert (await _register(backends[0], ResourceType.NODE, make_node(NODE_ID))).ok

        # Ownership is replicated, so the other members have to learn who owns
        # this Node before they can forward to it. Without this wait the test
        # measures the gap rather than the forwarding.
        await _eventually(
            lambda: all(
                r.store.get(ResourceType.NODE, NODE_ID) is not None
                for r in registries
            ),
            timeout=20.0,
        )

        refusals: list[str] = []
        for turn in range(1, turns + 1):
            # All three members share this test's event loop, so a loop that
            # never yields for longer than `election_min` starves their
            # election timers: a follower stops hearing the leader, becomes a
            # pre-candidate, clears its `leader` to release the lease, and then
            # legitimately refuses mutations with "no leader elected".
            #
            # Observed exactly that before this line -- one follower in `pre-`
            # with its commit index one behind at every refusal, while the
            # leader held term 1 throughout. That is the rig, not the code: in
            # production each member is its own process and cannot have its
            # timers starved by another member's load.
            await asyncio.sleep(RIG_TIMING.heartbeat)
            member = pick(turn)
            node = make_node(NODE_ID)
            node["label"] = f"{label} turn {turn}"
            try:
                result = await _register(
                    backends[member], ResourceType.NODE, node,
                )
                if not result.ok:
                    refusals.append(
                        f"register at member {member}, turn {turn}: {result}",
                    )
            except Exception as error:  # noqa: BLE001 - reported, not handled
                refusals.append(
                    f"register at member {member}, turn {turn}: {error!r}"
                    f" | {_cluster_state(backends)}",
                )

            try:
                if await backends[member].heartbeat(NODE_ID) is None:
                    refusals.append(
                        f"heartbeat at member {member}, turn {turn}: refused",
                    )
            except Exception as error:  # noqa: BLE001 - reported, not handled
                refusals.append(
                    f"heartbeat at member {member}, turn {turn}: {error!r}",
                )

        assert not refusals, (
            f"a Node moving between members was refused {len(refusals)} of "
            f"{2 * turns} times, though every member can reach the owner:\n  "
            + "\n  ".join(refusals)
        )

        final = f"{label} turn {turns}"
        await _eventually(
            lambda: all(
                (held := r.store.get(ResourceType.NODE, NODE_ID)) is not None
                and held.raw["label"] == final
                for r in registries
            ),
            timeout=20.0,
        )
    finally:
        for backend in backends:
            await backend.close()


@_parameterise
async def test_a_node_that_moves_between_members_round_robin(
    rig: ClusterRig, request: Any,
) -> None:
    """Every call goes to a different member than the last.

    The systematic version: after the first registration nothing ever lands on
    the owner again, so every single call is a forward.
    """
    await _drive_a_moving_node(
        rig, lambda turn: turn % rig.size, turns=3 * rig.size,
        label="round robin",
    )


@_parameterise
async def test_a_node_that_moves_between_members_at_random(
    rig: ClusterRig, request: Any,
) -> None:
    """The same, chosen at random rather than in rotation.

    Round robin is regular, and regularity is its weakness: it produces one
    interleaving of forwards and appends and never any other. Random selection
    revisits the owner sometimes -- which exercises the *local* path and the
    forwarded one in the same run -- and varies the spacing between forwards,
    which is what decides whether a forward is outstanding when an unrelated
    reply arrives.

    Seeded, so a failure is reproducible; the seed is in the name of the
    variable rather than hidden in a global, so changing it is a deliberate act.
    """
    chooser = random.Random(20260919)
    await _drive_a_moving_node(
        rig, lambda _turn: chooser.randrange(rig.size), turns=4 * rig.size,
        label="random",
    )


@_parameterise
async def test_nodes_that_roam_the_cluster_stay_visible(
    rig: ClusterRig, request: Any,
) -> None:
    """Several Nodes, each talking to whichever member it feels like.

    The shape a real deployment has once there is more than one Node: each one
    registers somewhere, so **every member owns some Nodes and forwards for
    others** -- no member is special, which the single-Node tests above cannot
    say. Registration, heartbeat and query each pick a member independently, so
    the interleavings are not the one pattern a rotation produces.

    The assertion is strict on purpose. A Node that has registered **stays**
    registered: it keeps updating and keeps beating, so after the first
    registration has propagated there is no moment when it is legitimately
    absent. Every query from then on must find it. A 404 after that point is a
    member that lost a resource it had, and that is a defect however rare.

    Replication lag is real but is spent before the loop starts -- measured at
    1.4-4.9 ms across members -- which is why the settle below is a wait for
    propagation rather than a sleep.
    """
    nodes = 4
    rounds = 6
    chooser = random.Random(20260919)

    namespace = _namespace()
    registries, backends = await _whole_cluster(rig, namespace)
    try:
        assert all(b.state is BackendState.READY for b in backends)

        ids = [str(uuid.UUID(int=index + 1, version=4)) for index in range(nodes)]
        for node_id in ids:
            member = chooser.randrange(rig.size)
            assert (
                await _register(backends[member], ResourceType.NODE, make_node(node_id))
            ).ok, f"the first registration of {node_id[:8]} at member {member} failed"

        # Propagation happens once, here. After this every member holds every
        # Node and must keep holding it.
        await _eventually(
            lambda: all(
                r.store.get(ResourceType.NODE, node_id) is not None
                for r in registries
                for node_id in ids
            ),
            timeout=20.0,
        )

        problems: list[str] = []
        for turn in range(1, rounds + 1):
            # The rig runs all members on this event loop; yielding keeps their
            # election timers fed. See the note in `_drive_a_moving_node`.
            await asyncio.sleep(RIG_TIMING.heartbeat)

            for node_id in ids:
                updating = chooser.randrange(rig.size)
                body = make_node(node_id)
                body["label"] = f"turn {turn}"
                try:
                    result = await _register(
                        backends[updating], ResourceType.NODE, body,
                    )
                    if not result.ok:
                        problems.append(
                            f"update {node_id[:8]} at m{updating}, turn {turn}:"
                            f" {result}",
                        )
                except Exception as error:  # noqa: BLE001 - reported
                    problems.append(
                        f"update {node_id[:8]} at m{updating}, turn {turn}:"
                        f" {error!r}",
                    )

                beating = chooser.randrange(rig.size)
                try:
                    if await backends[beating].heartbeat(node_id) is None:
                        problems.append(
                            f"heartbeat {node_id[:8]} at m{beating}, turn "
                            f"{turn}: refused",
                        )
                except Exception as error:  # noqa: BLE001 - reported
                    problems.append(
                        f"heartbeat {node_id[:8]} at m{beating}, turn {turn}:"
                        f" {error!r}",
                    )

            # The Controller: a random member, asked about a random Node. It is
            # a *read*, so it never forwards and never waits on consensus --
            # which is exactly why it must always succeed.
            for _ in range(rig.size):
                asked = chooser.randrange(rig.size)
                wanted = chooser.choice(ids)
                if registries[asked].store.get(ResourceType.NODE, wanted) is None:
                    problems.append(
                        f"query m{asked} for {wanted[:8]}, turn {turn}: absent, "
                        f"though it registered and has never stopped beating",
                    )

        assert not problems, (
            f"{len(problems)} failures across {rounds} rounds and {nodes} "
            "Nodes:\n  " + "\n  ".join(problems[:20])
        )
    finally:
        for backend in backends:
            await backend.close()
