# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Forwarding: what happens when a Node does not stay on one member.

Split out of ``test_cluster_conformance.py`` for one reason -- **these run in
the default gate and that file does not.** It is marked ``e2e``, which is right
for most of what it holds, and wrong for these: they are the only tests
anywhere that exercise a **forwarded mutation**.

That path had never been covered. Both in-memory harnesses refuse a forwarded
mutation on purpose -- ``_harness.py`` correlates only read indexes, and the
Rust fabric's ``request`` serves nothing else either -- so the whole consensus
suite could not reach it, and a deadlock lived there in both implementations
until a workload that moves a Node between members found it.

Cheap enough to gate: the raft parametrisation is in-process and runs in about
half a second each. The etcd one spins a real cluster and costs a couple of
seconds of setup, and skips itself when no etcd binary is available.
"""
from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from nmos.raft.messages import Forward, ForwardReply
from nmos.registry.backend import BackendState, MutationUnavailable
from nmos.registry.tests._fixtures import (
    DEVICE_ID,
    NODE_ID,
    SENDER_ID,
    make_device,
    make_node,
    make_sender,
)
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
from nmos.registry.tests.test_etcd_backend import _eventually, build_registry
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
    purpose -- ``_harness.py`` correlates only read indexes, and the Rust
    fabric's ``request`` serves nothing else either -- so the whole consensus
    suite has never exercised a forwarded mutation. Only a rig on the real
    transport can, which is this one.
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


@pytest.mark.parametrize("rig", ["raft"], indirect=True)
async def test_an_owner_that_cannot_commit_says_so_at_once(rig: ClusterRig) -> None:
    """An owner's refusal reaches the forwarder -- promptly, and with its reason.

    The owner of a Node could not commit a mutation forwarded to it, and
    raised. The transport serves forwarded work on a task with nobody to hand
    an exception to, so no reply was written: the forwarder waited out its
    whole deadline and reported "did not answer". Measured over real sockets
    before the fix: the owner failed with "no leader elected" in 0.00s, the
    forwarder answered after the full mutation timeout. The transports' own
    capacity refusal states the contract this broke -- "saying so immediately
    is strictly better than making the caller wait out a deadline to learn it".

    Raft only: what is injected is the raft owner's commit failing at once,
    because what is under test is what happens to that failure on its way back.
    """
    namespace = _namespace()
    registries, backends = await _whole_cluster(rig, namespace)
    try:
        assert all(b.state is BackendState.READY for b in backends)
        assert (await _register(backends[0], ResourceType.NODE, make_node(NODE_ID))).ok
        # The member index, not the list position: the rig lists members in the
        # order it built them, while a member's index is its place in the
        # cluster's canonical order.
        owner = backends[0]._node.index
        await _eventually(
            lambda: all(b._owner_for(NODE_ID) == owner for b in backends),
            timeout=20.0,
        )

        reason = "registration of the device could not commit: no leader elected"

        async def cannot_commit(*_: Any, **__: Any) -> Any:
            raise MutationUnavailable(reason)

        backends[0]._register_as_owner = cannot_commit

        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            await _register(
                backends[1], ResourceType.DEVICE, make_device(DEVICE_ID, NODE_ID),
            )
        except MutationUnavailable as error:
            answered = str(error)
        else:
            pytest.fail("a registration the owner could not commit was accepted")
        elapsed = loop.time() - started

        assert reason in answered, (
            f"the forwarder answered {answered!r} after {elapsed:.2f}s; the owner "
            f"had said {reason!r} at once"
        )
        assert elapsed < 1.0, (
            f"the forwarder took {elapsed:.2f}s to learn what the owner knew at once"
        )
    finally:
        for backend in backends:
            await backend.close()


@pytest.mark.parametrize("rig", ["raft"], indirect=True)
async def test_a_moved_node_is_routed_again_once_and_then_answered_unavailable(
    rig: ClusterRig,
) -> None:
    """One retry when a Node has moved -- and no more, enforced.

    "One retry, as owner or forwarder depending on where it moved to -- and no
    more", written above a retry that re-entered ``register`` with nothing
    spent. A forwarder whose ownership table is behind names the same former
    owner every time, so each retry forwarded to the member that had just
    answered ``not_owner``: measured over real sockets as 490 forwards in 0.4s,
    ended only by ``RecursionError`` (which the transport reported as a failed
    link); the same recursion aborts the Rust process on a stack overflow.

    The former owner here refuses the first ten forwards and then accepts, so
    the unbounded version ends in a count -- eleven forwards, then success --
    rather than in the recursion limit. Raft only: ownership is raft's.
    """
    namespace = _namespace()
    registries, backends = await _whole_cluster(rig, namespace)
    try:
        assert all(b.state is BackendState.READY for b in backends)
        assert (await _register(backends[0], ResourceType.NODE, make_node(NODE_ID))).ok
        owner = backends[0]._node.index
        await _eventually(
            lambda: all(b._owner_for(NODE_ID) == owner for b in backends),
            timeout=20.0,
        )

        forwards = 0

        async def moved(message: Any) -> ForwardReply:
            nonlocal forwards
            forwards += 1
            return ForwardReply(
                ok=forwards > 10, created=False, error="", detail="",
                applied_index=0, not_owner=forwards <= 10,
                request_id=message.request_id, owner=None,
            )

        backends[0]._node._forwarder = moved

        try:
            result = await _register(
                backends[1], ResourceType.DEVICE, make_device(DEVICE_ID, NODE_ID),
            )
            answered = f"ok={result.ok}"
        except MutationUnavailable as error:
            answered = str(error)

        assert forwards == 2, (
            f"the forwarder forwarded {forwards} times to a member that kept "
            f"answering not_owner (and was answered {answered!r}); one retry and "
            f"no more is two forwards"
        )
        assert "no longer owns node" in answered, (
            f"a Node that stayed moved was answered {answered!r}, not a 503"
        )
    finally:
        for backend in backends:
            await backend.close()


@pytest.mark.parametrize("rig", ["raft"], indirect=True)
async def test_a_heartbeat_for_a_moved_node_is_routed_again_once_and_then_answered_unavailable(
    rig: ClusterRig,
) -> None:
    """A heartbeat is routed as a registration is: one retry, then a 503.

    Its forwarder read the owner's "not the owner" as a plain refusal and
    answered **404** -- the terminal "re-register every resource"
    (``Behaviour - Registration.md:112-114``) -- for a Node the cluster still
    held. Unreached only because no owner ever said it: each forwarded the
    heartbeat on instead (``test_a_forwarded_heartbeat_is_never_forwarded_again``).
    Raft only: ownership is raft's.
    """
    namespace = _namespace()
    registries, backends = await _whole_cluster(rig, namespace)
    try:
        assert all(b.state is BackendState.READY for b in backends)
        assert (await _register(backends[0], ResourceType.NODE, make_node(NODE_ID))).ok
        owner = backends[0]._node.index
        await _eventually(
            lambda: all(b._owner_for(NODE_ID) == owner for b in backends),
            timeout=20.0,
        )

        forwards = 0

        async def moved(message: Any) -> ForwardReply:
            nonlocal forwards
            forwards += 1
            return ForwardReply(
                ok=forwards > 10, created=False, error="", detail="",
                applied_index=0, not_owner=forwards <= 10,
                request_id=message.request_id, owner=None,
            )

        backends[0]._node._forwarder = moved

        try:
            health = await backends[1].heartbeat(NODE_ID)
            answered = f"health={health}"
        except MutationUnavailable as error:
            answered = str(error)

        assert forwards == 2, (
            f"the forwarder forwarded a heartbeat {forwards} times to a member "
            f"that kept answering not_owner (and was answered {answered!r}); one "
            f"retry and no more is two forwards"
        )
        assert "no longer owns node" in answered, (
            f"a heartbeat for a Node that stayed moved was answered "
            f"{answered!r}, not a 503"
        )
    finally:
        for backend in backends:
            await backend.close()


@pytest.mark.parametrize("rig", ["raft"], indirect=True)
async def test_a_forwarded_heartbeat_is_never_forwarded_again(
    rig: ClusterRig,
) -> None:
    """A member that does not own a forwarded heartbeat's Node says so.

    As ``_on_forward`` answers a registration: a request that hops between
    members has no bound on its latency, and two members whose tables disagree
    about the owner handed a heartbeat back and forth until an RPC deadline cut
    the chain. Registration had that rule; the heartbeat was answered by the
    member's own ``heartbeat``, which forwards. Raft only: ownership is raft's.
    """
    namespace = _namespace()
    registries, backends = await _whole_cluster(rig, namespace)
    try:
        assert all(b.state is BackendState.READY for b in backends)
        assert (await _register(backends[0], ResourceType.NODE, make_node(NODE_ID))).ok
        owner = backends[0]._node.index
        await _eventually(
            lambda: all(b._owner_for(NODE_ID) == owner for b in backends),
            timeout=20.0,
        )

        forwarded = 0
        answer: Callable[[Forward], Awaitable[ForwardReply]] = (
            backends[0]._node._forwarder
        )

        async def counting(message: Forward) -> ForwardReply:
            nonlocal forwarded
            forwarded += 1
            return await answer(message)

        backends[0]._node._forwarder = counting

        reply = await backends[1]._on_forward(Forward(
            verb="heartbeat", resource_type="node", resource_id=NODE_ID,
            body_text="", request_id=7,
        ))
        assert forwarded == 0, (
            f"a member that does not own the Node forwarded the heartbeat it "
            f"was handed on to the owner ({forwarded}x), and answered {reply}"
        )
        assert reply.not_owner and reply.owner == owner, reply
    finally:
        for backend in backends:
            await backend.close()


def _replica(backend: Any, registry: Any) -> list[str]:
    """What every member must agree on: content, cursors and ownership.

    Health is left out -- a heartbeat refreshes it on the owner only, by design
    -- and so are tombstones, which each member forgets on its own schedule.
    """
    rows = []
    for resource_type in ResourceType:
        for resource in registry.store.iter_extant(resource_type):
            rows.append(
                f"{resource_type.value} {resource.id} v={resource.version} "
                f"c={resource.created} u={resource.updated} "
                f"parent={resource.parent_id} body={resource.body.text}",
            )
            if resource_type is ResourceType.NODE:
                rows.append(f"owner {resource.id} = {backend._owner_for(resource.id)}")
    return sorted(rows)


async def _all_apply_what_is_committed(backends: list[Any], leader: Any) -> None:
    """Every member applies through the leader's commit index; a stopped one never does."""
    committed = leader._node.commit_index
    try:
        await _eventually(
            lambda: all(b._node.last_applied >= committed for b in backends),
            timeout=20.0,
        )
    except AssertionError:
        pytest.fail(
            f"the members stopped applying: {committed} committed; "
            f"{_cluster_state(backends)}",
        )


@pytest.mark.parametrize("rig", ["raft"], indirect=True)
async def test_a_node_registered_at_two_members_at_once_is_created_once(
    rig: ClusterRig,
) -> None:
    """Both commit; one creates, one updates, and every member keeps applying.

    Two members take the first registration of the same new Node at the same
    moment -- a Node retrying against a second registry before the first
    answered, say. Each finds the Node absent and unowned, so each proposes a
    create with a fused claim; both commit, and the second to apply finds the
    Node already there. Apply used to call that a divergence ("the two stores
    disagree about what is registered") and stop -- on every member, since
    every member computes the same thing -- so the cluster went on committing
    while applying nothing: the chaos soak's largest failure class.

    Raft only: ownership, and the claim that races, are raft's.
    """
    namespace = _namespace()
    registries, backends = await _whole_cluster(rig, namespace)
    try:
        assert all(b.state is BackendState.READY for b in backends)
        racers = backends[:2]

        # Each racer must register as the owner itself -- proposing the create
        # and the claim -- or nothing races and this proves nothing.
        took_ownership: list[tuple[int, bool]] = []
        for backend in racers:
            original = backend._register_as_owner

            def as_owner(
                resource_type: ResourceType, body: Body, node_id: str, *,
                claim: bool, _original: Any = original,
                _member: int = backend._node.index,
            ) -> Any:
                took_ownership.append((_member, claim))
                return _original(resource_type, body, node_id, claim=claim)

            backend._register_as_owner = as_owner

        # Answers collected rather than raised: a stopped applier shows first as
        # a registration that never commits, and the applied indices below are
        # the measurement that says why.
        #
        # One body for both, as a retrying Node sends: ``make_node`` stamps a
        # fresh version per call, and two versions would make whichever commits
        # second a version regression -- correctly refused, and not this race.
        raw = make_node(NODE_ID)
        answers = await asyncio.gather(*(
            _register(backend, ResourceType.NODE, raw) for backend in racers
        ), return_exceptions=True)
        assert sorted(took_ownership) == sorted(
            (backend._node.index, True) for backend in racers
        ), f"not both racers claimed the Node: {took_ownership}"

        leader = next(b for b in backends if b._node.leader == b._node.index)
        await _all_apply_what_is_committed(backends, leader)

        first, second = answers
        assert not isinstance(first, BaseException), first
        assert not isinstance(second, BaseException), second
        assert first.ok and second.ok, (first, second)
        assert [first.created, second.created].count(True) == 1, (
            f"one registration creates the Node (201) and the other updates it "
            f"(200): {[first.created, second.created]}"
        )

        # The cluster still takes registrations -- routed to whichever member
        # the later claim made the owner.
        device = await _register(
            backends[2], ResourceType.DEVICE, make_device(DEVICE_ID, NODE_ID),
        )
        assert device.ok and device.created, device

        await _all_apply_what_is_committed(backends, leader)
        replicas = [
            _replica(backend, registry)
            for backend, registry in zip(backends, registries, strict=True)
        ]
        for position, replica in enumerate(replicas[1:], start=1):
            assert replica == replicas[0], (
                f"member {position} and member 0 hold different registries"
            )
        assert backends[0]._owner_for(NODE_ID) is not None, "the raced Node has no owner"
    finally:
        for backend in backends:
            await backend.close()


@pytest.mark.parametrize("rig", ["raft"], indirect=True)
async def test_an_update_racing_an_unregister_recreates_the_resource(
    rig: ClusterRig,
) -> None:
    """The update commits second, creates the resource, and stamps its own cursor.

    An unregister takes no per-Node gate, so an update can be predicted against
    a store that still holds the resource while the removal is already on its
    way into the log. The update applies to an absent resource: it creates it
    -- 201, as one registry answers a POST that follows a DELETE -- stamped
    with its own cursor, not with the creation cursor the proposer copied from
    the record the removal erased. Apply used to stop at that entry, on every
    member.
    """
    namespace = _namespace()
    registries, backends = await _whole_cluster(rig, namespace)
    try:
        assert all(b.state is BackendState.READY for b in backends)
        # Everything at the leader, so both proposals enter the log in the
        # order they are made, with no link between them to reorder.
        position, at = next(
            (position, b) for position, b in enumerate(backends)
            if b._node.leader == b._node.index
        )
        assert (await _register(at, ResourceType.NODE, make_node(NODE_ID))).ok
        registered = await _register(
            at, ResourceType.DEVICE, make_device(DEVICE_ID, NODE_ID),
        )
        assert registered.ok and registered.created, registered
        existing = registries[position].store.get(ResourceType.DEVICE, DEVICE_ID)
        assert existing is not None
        original = existing.created

        removed: bool | BaseException
        updated: Any
        removed, updated = await asyncio.gather(
            at.unregister(ResourceType.DEVICE, DEVICE_ID),
            _register(at, ResourceType.DEVICE, make_device(DEVICE_ID, NODE_ID)),
            return_exceptions=True,
        )
        await _all_apply_what_is_committed(backends, at)

        assert removed is True, f"the unregister was answered as {removed!r}"
        assert not isinstance(updated, BaseException), updated
        assert updated.ok and updated.created, (
            f"the update that committed after the removal was answered as "
            f"{updated!r}; it recreates the Device, which is a 201"
        )
        for backend, registry in zip(backends, registries, strict=True):
            member = backend._node.index
            device = registry.store.get(ResourceType.DEVICE, DEVICE_ID)
            assert device is not None, f"m{member} does not hold the recreated Device"
            assert device.created == device.updated, (
                f"m{member}: the recreated Device carries a creation cursor "
                f"other than its own entry's"
            )
            assert device.created > original, (
                f"m{member}: the recreated Device was stamped {device.created}, "
                f"not after the record the removal erased ({original}), so a "
                f"client paging by creation has passed it"
            )
        replicas = [
            _replica(backend, registry)
            for backend, registry in zip(backends, registries, strict=True)
        ]
        for index, replica in enumerate(replicas[1:], start=1):
            assert replica == replicas[0], (
                f"member {index} and member 0 hold different registries"
            )
    finally:
        for backend in backends:
            await backend.close()


# ---------------------------------------------------------------------------
# A member behind what is committed
# ---------------------------------------------------------------------------

class _HeldApply:
    """A member that holds committed entries without applying them.

    What every follower is between learning a commit index and applying up to
    it, held open for as long as a test needs: it still receives, acknowledges
    and commits entries -- raft is untouched -- but its store, and anything it
    would answer from it, stays where it was until ``release``.
    """

    def __init__(self, backend: Any) -> None:
        self._node = backend._node
        self._node._schedule_apply = lambda: None

    def release(self) -> None:
        del self._node._schedule_apply
        self._node._schedule_apply()


async def _released_after(held: _HeldApply | _HeldWatch, seconds: float) -> None:
    await asyncio.sleep(seconds)
    held.release()


async def _with_owner(backends: list[Any]) -> Any:
    """Register the Node at the first member, which then owns it, and wait
    until every member knows so."""
    owner = backends[0]
    assert (await _register(owner, ResourceType.NODE, make_node(NODE_ID))).ok
    await _eventually(
        lambda: all(b._owner_for(NODE_ID) == owner._node.index for b in backends),
        timeout=20.0,
    )
    return owner


@pytest.mark.parametrize("rig", ["raft"], indirect=True)
async def test_a_member_behind_on_a_parent_waits_for_it_rather_than_refusing(
    rig: ClusterRig,
) -> None:
    """A Sender whose Device is committed but not yet applied here is no 400.

    The member looked the Sender's Device up in its own store to find the
    Node, did not find it, and answered ``PARENT_MISSING`` -- a 400, which a
    Node "MUST NOT" retry without corrective action (``Behaviour -
    Registration.md:96``) -- about a Device the cluster had acknowledged. The
    chaos soak counted 352 400s for fresh registrations in one run set. The
    member now learns a read index and applies through it before any answer
    its store decides, and the Sender then registers.

    Raft only: what is held back is a raft member's applier.
    """
    registries, backends = await _whole_cluster(rig, _namespace())
    try:
        assert all(b.state is BackendState.READY for b in backends)
        owner = await _with_owner(backends)
        behind = backends[1]

        held = _HeldApply(behind)
        assert (await _register(
            owner, ResourceType.DEVICE, make_device(DEVICE_ID, NODE_ID),
        )).ok
        assert registries[1].store.get(ResourceType.DEVICE, DEVICE_ID) is None

        release = asyncio.create_task(_released_after(held, 0.2))
        try:
            result = await _register(behind, ResourceType.SENDER, make_sender())
        finally:
            await release
        assert result.ok and result.created, (
            f"a Sender whose Device the cluster had acknowledged was answered "
            f"{result.error} by a member that had not yet applied the Device"
        )
        assert registries[1].store.get(ResourceType.SENDER, SENDER_ID) is not None
    finally:
        for backend in backends:
            await backend.close()


@pytest.mark.parametrize("rig", ["raft"], indirect=True)
async def test_a_member_behind_on_a_node_waits_for_it_before_its_device(
    rig: ClusterRig,
) -> None:
    """A Device whose Node is committed but not yet applied here is no 400.

    The Device names its Node, so nothing is looked up to route it -- but this
    member's ownership table is as far behind as its store, shows the Node
    unowned, and so this member registers the Device as its owner, validating
    it against a store without the Node: ``PARENT_MISSING``, returned as
    "authoritative, and free" because this member owns the Node. An owner is
    only as current as what it has applied.

    Raft only: what is held back is a raft member's applier.
    """
    registries, backends = await _whole_cluster(rig, _namespace())
    try:
        assert all(b.state is BackendState.READY for b in backends)
        behind = backends[1]
        held = _HeldApply(behind)
        assert (await _register(
            backends[0], ResourceType.NODE, make_node(NODE_ID),
        )).ok
        assert registries[1].store.get(ResourceType.NODE, NODE_ID) is None

        release = asyncio.create_task(_released_after(held, 0.2))
        try:
            result = await _register(
                behind, ResourceType.DEVICE, make_device(DEVICE_ID, NODE_ID),
            )
        finally:
            await release
        assert result.ok and result.created, (
            f"a Device whose Node the cluster had acknowledged was answered "
            f"{result.error} by a member that had not yet applied the Node"
        )

        leader = next(b for b in backends if b._node.leader == b._node.index)
        await _all_apply_what_is_committed(backends, leader)
        replicas = [
            _replica(backend, registry)
            for backend, registry in zip(backends, registries, strict=True)
        ]
        for position, replica in enumerate(replicas[1:], start=1):
            assert replica == replicas[0], (
                f"member {position} and member 0 hold different registries"
            )
    finally:
        for backend in backends:
            await backend.close()


@pytest.mark.parametrize("rig", ["raft"], indirect=True)
async def test_a_member_behind_on_a_resource_does_not_call_it_absent(
    rig: ClusterRig,
) -> None:
    """A DELETE of a resource committed but not yet applied here is no 404.

    ``unregister`` answered from the local store -- "the local store is a
    complete replica, so 'not here' is not a guess" -- which is true of a
    replica only as of what it has applied. The chaos soak counted 77 404s on
    DELETE of acknowledged resources in one run set.

    Raft only: what is held back is a raft member's applier.
    """
    registries, backends = await _whole_cluster(rig, _namespace())
    try:
        assert all(b.state is BackendState.READY for b in backends)
        owner = await _with_owner(backends)
        behind = backends[1]

        held = _HeldApply(behind)
        assert (await _register(
            owner, ResourceType.DEVICE, make_device(DEVICE_ID, NODE_ID),
        )).ok
        assert registries[1].store.get(ResourceType.DEVICE, DEVICE_ID) is None

        release = asyncio.create_task(_released_after(held, 0.2))
        try:
            deleted = await behind.unregister(ResourceType.DEVICE, DEVICE_ID)
        finally:
            await release
        assert deleted, (
            "a Device the cluster had acknowledged was answered 404 on DELETE "
            "by a member that had not yet applied it"
        )
        await _eventually(
            lambda: all(
                r.store.get(ResourceType.DEVICE, DEVICE_ID) is None
                for r in registries
            ),
            timeout=20.0,
        )
    finally:
        for backend in backends:
            await backend.close()


@pytest.mark.parametrize("rig", ["raft"], indirect=True)
async def test_a_member_behind_on_a_node_does_not_tell_it_to_re_register(
    rig: ClusterRig,
) -> None:
    """A heartbeat for a Node committed but not yet applied here is no 404.

    A 404 on heartbeat tells the Node to re-register every resource it has
    (``Behaviour - Registration.md:112-114``). This member's ownership table
    did not show the Node, so it answered from its store, which did not hold
    it either. The chaos soak counted 187 404s on heartbeats of acknowledged
    Nodes in one run set. Once current, this member finds the Node's owner and
    forwards the heartbeat there.

    Raft only: what is held back is a raft member's applier.
    """
    registries, backends = await _whole_cluster(rig, _namespace())
    try:
        assert all(b.state is BackendState.READY for b in backends)
        behind = backends[1]
        held = _HeldApply(behind)
        assert (await _register(
            backends[0], ResourceType.NODE, make_node(NODE_ID),
        )).ok
        assert registries[1].store.get(ResourceType.NODE, NODE_ID) is None

        release = asyncio.create_task(_released_after(held, 0.2))
        try:
            health = await behind.heartbeat(NODE_ID)
        finally:
            await release
        assert health is not None, (
            "a Node the cluster had acknowledged was answered 404 on heartbeat "
            "by a member that had not yet applied it"
        )
    finally:
        for backend in backends:
            await backend.close()


# ---------------------------------------------------------------------------
# An etcd member whose watch is behind
# ---------------------------------------------------------------------------

class _HeldWatch:
    """An etcd member whose watch holds what etcd sends without applying it.

    What every member is between etcd committing a revision and its watch
    delivering it, held open for as long as a test needs: etcd has the change
    and the other members apply it, but this member's store, its lease table
    and its fence stay where they were until ``release`` applies what was held,
    in order. Release is synchronous, so nothing the watch delivers meanwhile
    can overtake it.
    """

    def __init__(self, backend: Any) -> None:
        self._backend = backend
        self._held: list[Any] = []
        backend._apply_batch = self._held.append

    def release(self) -> None:
        del self._backend._apply_batch
        for batch in self._held:
            self._backend._apply_batch(batch)
        self._held.clear()


async def _node_applied_at(registry: Any) -> None:
    await _eventually(
        lambda: registry.store.get(ResourceType.NODE, NODE_ID) is not None,
        timeout=20.0,
    )


@pytest.mark.parametrize("rig", ["etcd"], indirect=True)
async def test_an_etcd_member_behind_on_a_parent_waits_for_it_rather_than_refusing(
    rig: ClusterRig,
) -> None:
    """A Sender whose Device is in etcd but not yet in this member's store is no 400.

    ``_placement`` looked the Device up in the local store -- fed by the watch,
    and behind etcd -- and answered ``PARENT_MISSING`` without the fence
    ``register``'s own rule requires before any rejection: "a parent registered
    a moment ago on another member is not here yet, and PARENT_MISSING would be
    a lie". A Node "MUST NOT" retry a 400 (``Behaviour - Registration.md:96``).

    etcd only: what is held back is an etcd member's watch.
    """
    registries, backends = await _whole_cluster(rig, _namespace())
    try:
        assert all(b.state is BackendState.READY for b in backends)
        first, behind = backends[0], backends[1]
        assert (await _register(first, ResourceType.NODE, make_node(NODE_ID))).ok
        await _node_applied_at(registries[1])

        held = _HeldWatch(behind)
        assert (await _register(
            first, ResourceType.DEVICE, make_device(DEVICE_ID, NODE_ID),
        )).ok
        assert registries[1].store.get(ResourceType.DEVICE, DEVICE_ID) is None

        release = asyncio.create_task(_released_after(held, 0.2))
        try:
            result = await _register(behind, ResourceType.SENDER, make_sender())
        finally:
            await release
        assert result.ok and result.created, (
            f"a Sender whose Device etcd held was answered {result.error} by a "
            f"member whose watch had not yet delivered the Device"
        )
    finally:
        for backend in backends:
            await backend.close()


@pytest.mark.parametrize("rig", ["etcd"], indirect=True)
async def test_an_etcd_member_behind_on_a_resource_does_not_call_it_absent(
    rig: ClusterRig,
) -> None:
    """A DELETE of a resource in etcd but not yet in this member's store is no 404.

    ``_unregister`` answered from the local store, so the delete never happened:
    the resource stayed registered while the Node believed it gone, for as long
    as the Node went on heartbeating.

    etcd only: what is held back is an etcd member's watch.
    """
    registries, backends = await _whole_cluster(rig, _namespace())
    try:
        assert all(b.state is BackendState.READY for b in backends)
        first, behind = backends[0], backends[1]
        assert (await _register(first, ResourceType.NODE, make_node(NODE_ID))).ok
        await _node_applied_at(registries[1])

        held = _HeldWatch(behind)
        assert (await _register(
            first, ResourceType.DEVICE, make_device(DEVICE_ID, NODE_ID),
        )).ok
        assert registries[1].store.get(ResourceType.DEVICE, DEVICE_ID) is None

        release = asyncio.create_task(_released_after(held, 0.2))
        try:
            deleted = await behind.unregister(ResourceType.DEVICE, DEVICE_ID)
        finally:
            await release
        assert deleted, (
            "a Device etcd held was answered 404 on DELETE by a member whose "
            "watch had not yet delivered it"
        )
        await _eventually(
            lambda: all(
                r.store.get(ResourceType.DEVICE, DEVICE_ID) is None
                for r in registries
            ),
            timeout=20.0,
        )
    finally:
        for backend in backends:
            await backend.close()


@pytest.mark.parametrize("rig", ["etcd"], indirect=True)
async def test_an_etcd_member_behind_on_a_node_does_not_tell_it_to_re_register(
    rig: ClusterRig,
) -> None:
    """A heartbeat for a Node in etcd but not yet in this member's view is no 404.

    ``_heartbeat`` answered from the lease table the watch maintains -- "not
    ours to renew, and answered without touching the network" -- and a 404
    makes the Node re-register every resource it has (``Behaviour -
    Registration.md:112-114``). A Node that fails over heartbeats the new
    member first, and a 200 there means "the Node and its resources are still
    present in the registry cluster" (``:126``).

    etcd only: what is held back is an etcd member's watch.
    """
    registries, backends = await _whole_cluster(rig, _namespace())
    try:
        assert all(b.state is BackendState.READY for b in backends)
        first, behind = backends[0], backends[1]
        held = _HeldWatch(behind)
        assert (await _register(first, ResourceType.NODE, make_node(NODE_ID))).ok
        assert registries[1].store.get(ResourceType.NODE, NODE_ID) is None

        release = asyncio.create_task(_released_after(held, 0.2))
        try:
            health = await behind.heartbeat(NODE_ID)
        finally:
            await release
        assert health is not None, (
            "a Node etcd held was answered 404 on heartbeat by a member whose "
            "watch had not yet delivered it"
        )
    finally:
        for backend in backends:
            await backend.close()


@pytest.mark.parametrize("rig", ["etcd"], indirect=True)
async def test_an_etcd_member_behind_on_a_node_puts_its_device_on_the_nodes_lease(
    rig: ClusterRig,
) -> None:
    """A Device registered at a member that had not seen its Node hangs off the Node's lease.

    Every key in a Node's subtree hangs off the Node's lease, and that is the
    whole of distributed garbage collection: when the Node stops
    heartbeating, etcd removes every key on it. The lease a child is written
    with was read from the lease table before the fenced path's fence -- when,
    at a member that had not yet seen the Node, the table held nothing for it
    -- so the Device and its id claim went out on no lease at all. Measured:
    the Node's key on lease 1151691046727498246, the Device and its claim on
    0. A key with no lease outlives the Node's expiry: a Device in etcd whose
    Node is gone.

    etcd only: leases are etcd's.
    """
    registries, backends = await _whole_cluster(rig, _namespace())
    try:
        assert all(b.state is BackendState.READY for b in backends)
        first, behind = backends[0], backends[1]
        held = _HeldWatch(behind)
        assert (await _register(first, ResourceType.NODE, make_node(NODE_ID))).ok
        assert registries[1].store.get(ResourceType.NODE, NODE_ID) is None

        release = asyncio.create_task(_released_after(held, 0.2))
        try:
            result = await _register(
                behind, ResourceType.DEVICE, make_device(DEVICE_ID, NODE_ID),
            )
        finally:
            await release
        assert result.ok and result.created, result

        from nmos.etcd.kv import first_kv

        ns = behind.namespace
        read = await behind.kv.read_set([
            ns.node(NODE_ID), ns.device(NODE_ID, DEVICE_ID), ns.id_claim(DEVICE_ID),
        ])
        node, device, claim = (first_kv(response) for response in read.responses)
        assert node is not None and device is not None and claim is not None
        assert node.lease, "the Node itself is on no lease"
        assert (device.lease, claim.lease) == (node.lease, node.lease), (
            f"the Device is on lease {device.lease} and its claim on "
            f"{claim.lease}, not the Node's {node.lease}: they would outlive the "
            f"Node's expiry"
        )
    finally:
        for backend in backends:
            await backend.close()


@pytest.mark.parametrize("rig", ["etcd"], indirect=True)
async def test_an_etcd_fence_that_times_out_does_not_degrade_the_member(
    rig: ClusterRig,
) -> None:
    """A fence that times out is a 503 from a member that stays READY.

    A fence that times out means this member's view is behind -- its watch has
    not delivered what etcd holds -- and that is no failure of etcd's. The
    commit may well have succeeded, and the Node replays it. Degrading would
    turn one slow answer into many: a member degrades until its watch
    reconnects, so one whose watch is merely slow would refuse every mutation
    while nothing was wrong with etcd. Both implementations pin this.

    etcd only: what is held back is an etcd member's watch.
    """
    namespace = _namespace()
    registries = [build_registry() for _ in range(rig.size)]
    backends = list(await asyncio.gather(*(
        rig.backend_for(
            index, registry, namespace,
            # Only the member held back waits briefly: its fence is the one
            # that must time out, and every other keeps the rig's deadline.
            **({"mutation_timeout": 0.5} if index == 1 else {}),
        )
        for index, registry in enumerate(registries)
    )))
    try:
        assert all(b.state is BackendState.READY for b in backends)
        first, behind = backends[0], backends[1]
        held = _HeldWatch(behind)
        assert (await _register(first, ResourceType.NODE, make_node(NODE_ID))).ok
        try:
            # The refusal is the fence's own, not some other 503 that happens
            # not to degrade: its message is the one ``RevisionFence.wait`` gives.
            with pytest.raises(MutationUnavailable, match="still waiting for"):
                await _register(
                    behind, ResourceType.DEVICE, make_device(DEVICE_ID, NODE_ID),
                )
        finally:
            held.release()
        assert behind.state is BackendState.READY, (
            f"a fence that timed out left the member {behind.state.name}: it "
            f"refuses every mutation until its watch reconnects, and a watch "
            f"that is merely slow never does"
        )
    finally:
        for backend in backends:
            await backend.close()
