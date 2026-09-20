# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Forwarding: what happens when a Node does not stay on one member.

Split out of ``test_cluster_conformance.py`` for one reason -- **these run in
the default gate and that file does not.** It is marked ``e2e``, which is right
for most of what it holds, and wrong for these: they are the only tests
anywhere that exercise a **forwarded mutation**.

That path had never been covered. Both in-memory harnesses refuse correlated
requests on purpose -- ``_harness.py`` raises "the memory transport does not
correlate requests" and the Rust fabric's ``request`` is unimplemented -- so
the whole consensus suite could not reach it, and a deadlock lived there in
both implementations until a workload that moves a Node between members found
it.

Cheap enough to gate: the raft parametrisation is in-process and runs in about
half a second each. The etcd one spins a real cluster and costs a couple of
seconds of setup, and skips itself when no etcd binary is available.
"""
from __future__ import annotations

import asyncio
import random
import uuid
from typing import Any

from nmos.registry.backend import BackendState
from nmos.registry.tests._fixtures import NODE_ID, make_node
from nmos.registry.tests.rigs import ClusterRig
from nmos.registry.tests.rigs.raft_rig import RIG_TIMING
from nmos.registry.tests.test_cluster_conformance import (
    _cluster_state,
    _namespace,
    _parameterise,
    _register,
    _whole_cluster,
    rig,  # noqa: F401 -- a fixture, used by name
)
from nmos.registry.tests.test_etcd_backend import _eventually
from nmos.registry.types import Body, ResourceType


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
