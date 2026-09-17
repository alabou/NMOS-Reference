# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Randomised churn: members up and down at arbitrary times, for arbitrary periods.

    pytest nmos/raft/tests/test_chaos_soak.py

Turn it up before a release. Each variable widens one axis and nothing else,
so a long run is the same test rather than a different one:

    RAFT_SOAK_SEEDS=200          # memory runs: seeds 1..200 instead of SEEDS
    RAFT_SOAK_SEED=1234          # memory runs: that one seed only
    RAFT_SOAK_STEPS=2000         # longer memory runs
    RAFT_SOAK_SOCKET_SEEDS=20    # socket runs: seeds 1..20
    RAFT_SOAK_SOCKET_STEPS=200   # longer socket runs

A 40-seed memory sweep takes about 14 minutes and an 8-seed socket sweep about
90 seconds, which is the right order for a pre-release gate rather than a
per-commit one.

Every other consensus test in this suite arranges one scenario and asserts one
outcome, which proves exactly the scenario it arranged. This file does the
opposite: it generates interleavings nobody chose, and after **every step**
evaluates the properties Raft promises are true at all times (see
``_invariants.py``, which quotes them from the paper).

The committed run is small enough to sit in the default gate. The seed count is
an environment variable precisely so the same file can be run for an hour
before a release without becoming a different test.

Two transports, one driver
--------------------------
The same churn runs twice: over memory pipes (``_harness.py``) and over real
TCP through cuttable forwarders (``_sockets.py``, ``_proxy.py``). They are not
alternatives, they answer different questions.

Memory pipes buy **breadth**. A step costs a few method calls, so a run
explores many interleavings cheaply -- and everything it proves, it proves
about a cluster that has never opened a socket, because the transport, the
``Hello``/``HelloAck`` handshake, framing, checksums, the CONTROL/BULK split
and every reconnection path are exactly what it replaces.

Sockets buy **fidelity**, and cost real election windows, so they run fewer
seeds and fewer steps. There a cut link really strands a TCP connection
mid-stream and the transport really has to notice, reconnect and re-handshake.

Because the driver and the invariants are the same objects either way, a
failure that appears only on sockets is a finding about the transport rather
than about two harnesses that drifted apart.

Seeds bias a run; they do not replay it
---------------------------------------
Everything the driver *chooses* comes from one seeded ``random.Random``: which
member falls over, when, for how long, what the client does meanwhile, and how
long each link delays. But the driver also branches on the *outcome* of awaits
-- whether a proposal committed or timed out -- and those depend on the real
asyncio clock, which no seed controls. Two runs of the same seed therefore
explore the same region without being identical, and a seed added to ``SEEDS``
is a strong probabilistic regression test, not a deterministic one.

Exact replay would need a virtual clock driving the whole event loop, which is
a much larger thing than this file. So failures are made to explain themselves
instead: every violation carries the trace of what the run was doing, and the
trace is the artefact to read, not the seed to re-run.

The fault budget is counted in *amnesia*, not in downtime
---------------------------------------------------------
This backend keeps its log in memory, so a member that restarts comes back
having forgotten everything it had acknowledged. An entry is committed once a
quorum holds it -- so if a quorum's worth of members forget, that entry is gone
even though no two of them were ever down at the same time.

The budget therefore counts members that have forgotten and not yet been caught
up, not members that are currently down. Restarting one member, waiting for it
to be promoted, then restarting the next is safe; restarting the second one
first is not, and no consensus algorithm can make it so. This is the real form
of the design's documented trade, and it is stricter than "``f`` simultaneous
failures": the window is promotion, not downtime.

Within that budget nothing is off limits -- a member may be down for one tick
or for the whole run, and the one-way partitions are exactly the case symmetric
``isolate`` could never produce. Beyond it,
``test_a_forgotten_quorum_still_recovers`` asserts what is still owed when the
budget is deliberately blown: the cluster must come back, even though the data
need not.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

import pytest

from nmos.raft.errors import RaftError
from nmos.raft.operations import ProposalId, RegisterOp, UnregisterOp
from nmos.raft.tests._harness import FAST, Cluster
from nmos.raft.tests._invariants import InvariantViolation, SafetyMonitor
from nmos.raft.tests._sockets import SOCKET_TIMING, SocketCluster
from nmos.registry.tests._fixtures import make_node
from nmos.registry.types import ResourceType

# Seeds run by default. Small on purpose -- this sits in the ordinary gate, and
# a suite that takes a minute stops being run. Any seed that ever failed gets
# appended here permanently, with a comment saying what it found.
SEEDS: tuple[int, ...] = (1, 2, 3, 5, 8)

# Steps per run. Each step is one client operation or one fault, followed by a
# full evaluation of every invariant.
STEPS = int(os.environ.get("RAFT_SOAK_STEPS", "120"))

# The socket runs are shorter and fewer. A step there costs a real election
# window rather than a few method calls, so breadth is bought on memory pipes
# and fidelity on sockets -- running both at the memory soak's size would add
# minutes to every gate for interleavings the memory soak has already covered.
SOCKET_STEPS = int(os.environ.get("RAFT_SOAK_SOCKET_STEPS", "40"))
SOCKET_SEEDS: tuple[int, ...] = (1, 2)


class Event(Enum):
    """What the driver may do at each step.

    An enum rather than bare strings so a weighting table cannot drift from the
    dispatch, and so a failing run's trace reads as names.
    """

    REGISTER = "register"
    UNREGISTER = "unregister"
    STOP = "stop"
    RESUME = "resume"
    RESTART = "restart"
    BLOCK_ONE_WAY = "block-one-way"
    PARTITION = "partition"
    HEAL = "heal"
    IDLE = "idle"


# Weighted so the cluster spends most of its time doing work with something
# broken, rather than most of its time broken. A run that is all faults never
# commits anything, and then every invariant holds vacuously.
WEIGHTS: dict[Event, int] = {
    Event.REGISTER: 30,
    Event.UNREGISTER: 5,
    Event.STOP: 8,
    Event.RESUME: 8,
    Event.RESTART: 6,
    Event.BLOCK_ONE_WAY: 6,
    Event.PARTITION: 4,
    Event.HEAL: 8,
    Event.IDLE: 15,
}


@dataclass
class _Trace:
    """What happened, for the failure message.

    Kept because a seed alone reproduces a run but does not explain it, and the
    first question of any soak failure is "what was it doing?".
    """

    seed: int
    lines: list[str]

    def record(self, step: int, event: Event, detail: str = "") -> None:
        self.lines.append(f"  {step:4d} {event.value:<14} {detail}")

    def render(self, tail: int = 40) -> str:
        shown = self.lines[-tail:]
        elided = len(self.lines) - len(shown)
        head = f"seed {self.seed}, {len(self.lines)} steps"
        if elided > 0:
            head += f" (last {tail} shown, {elided} elided)"
        return head + "\n" + "\n".join(shown)


class ChurnTarget(Protocol):
    """What the driver needs of a cluster, whatever it is made of.

    Satisfied by both ``Cluster`` (memory pipes) and ``SocketCluster`` (real
    TCP through cuttable proxies), so one driver and one set of invariants run
    against both. The method names on ``network`` are identical on purpose:
    a driver that branched on which transport it was driving would be two
    drivers, and the second one would be the untested one.
    """

    timing: Any
    network: Any

    @property
    def members(self) -> Sequence[Any]: ...

    @property
    def leaders(self) -> Sequence[Any]: ...

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def restart(self, index: int) -> Any: ...

    async def settle(self, rounds: int = ...) -> None: ...

    async def elect(self, *, timeout: float = ...) -> Any: ...


class ChurnDriver:
    """Drives one seeded run: faults, client traffic, and the invariant checks."""

    def __init__(self, cluster: ChurnTarget, seed: int, *, max_delay: float) -> None:
        self.cluster = cluster
        self.rng = random.Random(seed)
        self.monitor = SafetyMonitor(cluster=cluster)
        self.trace = _Trace(seed=seed, lines=[])
        self.down: set[int] = set()
        self.blocked: list[tuple[int, int]] = []
        self.registered: list[str] = []

        # Link delay comes from the same seed, so message timing is part of
        # what a run's shape depends on rather than a source of silent drift.
        cluster.network.rng = self.rng
        cluster.network.max_delay = max_delay

    @property
    def budget(self) -> int:
        """How many members may be down, or forgetful, at once."""
        return len(self.cluster.members) // 2

    @property
    def forgetful(self) -> int:
        """Members that have restarted and not yet been promoted back.

        Read from the nodes rather than tracked here, because promotion is the
        cluster's decision and not the driver's: the driver may restart another
        member only once the cluster has actually caught the last one up.
        """
        return sum(1 for m in self.cluster.members if not m.node.voting)

    async def run(self, steps: int) -> None:
        for step in range(steps):
            event = self._choose()
            await self._apply(step, event)
            # One settle tick per step: enough for a message to cross a link,
            # far too little to let the cluster quiesce -- which is the point.
            # Invariants must hold mid-flight, not only at rest.
            await asyncio.sleep(self.cluster.timing.heartbeat)
            try:
                self.monitor.check()
            except InvariantViolation as violation:
                raise InvariantViolation(
                    f"{violation}\n\nTrace:\n{self.trace.render()}",
                ) from violation

    def _choose(self) -> Event:
        events = list(WEIGHTS)
        return self.rng.choices(
            events, weights=[WEIGHTS[e] for e in events], k=1,
        )[0]

    async def _apply(self, step: int, event: Event) -> None:
        network = self.cluster.network
        live = [
            m.index for m in self.cluster.members if m.index not in self.down
        ]

        if event is Event.REGISTER and live:
            await self._register(step, self.rng.choice(live))

        elif event is Event.UNREGISTER and live and self.registered:
            await self._unregister(step, self.rng.choice(live))

        elif event is Event.STOP and len(self.down) < self.budget and live:
            victim = self.rng.choice(live)
            network.stop(victim)
            self.down.add(victim)
            self.trace.record(step, event, f"member {victim}")

        elif event is Event.RESUME and self.down:
            back = self.rng.choice(sorted(self.down))
            network.resume(back)
            self.down.discard(back)
            self.trace.record(step, event, f"member {back}")

        elif (
            event is Event.RESTART and live
            and len(self.down) < self.budget
            # Counted in amnesia: this restart would make one more member
            # forgetful, and a quorum of forgetful members loses committed
            # entries by construction. See the module docstring.
            and self.forgetful + 1 <= self.budget
        ):
            # A restart empties the log and bumps the incarnation: the
            # scenario the non-voting rejoin exists for, now arriving at a
            # moment nobody chose.
            victim = self.rng.choice(live)
            await self.cluster.restart(victim)
            self.trace.record(step, event, f"member {victim}")

        elif event is Event.BLOCK_ONE_WAY and len(live) >= 2:
            source, target = self.rng.sample(live, 2)
            network.block(source, target)
            self.blocked.append((source, target))
            self.trace.record(step, event, f"{source} -> {target}")

        elif event is Event.PARTITION and len(live) >= 3:
            shuffled = list(live)
            self.rng.shuffle(shuffled)
            cut = self.rng.randint(1, len(shuffled) - 1)
            left, right = set(shuffled[:cut]), set(shuffled[cut:])
            network.partition(left, right)
            self.blocked.clear()
            self.trace.record(step, event, f"{sorted(left)} | {sorted(right)}")

        elif event is Event.HEAL:
            network.heal()
            self.blocked.clear()
            self.trace.record(step, event)

        else:
            self.trace.record(step, Event.IDLE)

    # -- client traffic ---------------------------------------------------

    async def _register(self, step: int, member_index: int) -> None:
        """Register a Node, and record it only if the cluster said yes.

        The distinction is the entire point of the durability check: a refused
        or timed-out registration promises nothing and must not be expected to
        survive, while an acknowledged one must survive anything within the
        fault budget.
        """
        node_id = str(uuid.UUID(int=self.rng.getrandbits(128), version=4))
        member = self.cluster.members[member_index]
        cursor = member.node.cursors.allocate(ResourceType.NODE)
        operation = RegisterOp(
            proposal=ProposalId(member_index, 0),
            resource_type=ResourceType.NODE,
            resource_id=node_id,
            node_id=node_id,
            body_text=json.dumps(make_node(node_id)),
            created=cursor,
            updated=cursor,
            # Zero, so nothing the soak registers is ever garbage-collected
            # mid-run: an expiry would remove a resource the durability check
            # is entitled to expect, and report it as consensus losing data.
            health=0,
            expect_created=True,
            claim_owner=member_index,
        )
        try:
            outcome = await asyncio.wait_for(
                member.node.propose(operation), timeout=1.0,
            )
        except (TimeoutError, asyncio.TimeoutError, RaftError) as refused:
            self.trace.record(
                step, Event.REGISTER,
                f"member {member_index} refused ({type(refused).__name__})",
            )
            return
        if outcome.result.ok:
            self.monitor.acknowledged.add(node_id)
            self.registered.append(node_id)
            self.trace.record(
                step, Event.REGISTER,
                f"member {member_index} ACK {node_id[:8]}",
            )
        else:
            self.trace.record(
                step, Event.REGISTER, f"member {member_index} rejected",
            )

    async def _unregister(self, step: int, member_index: int) -> None:
        node_id = self.rng.choice(self.registered)
        member = self.cluster.members[member_index]
        operation = UnregisterOp(
            proposal=ProposalId(member_index, 0),
            resource_type=ResourceType.NODE,
            resource_id=node_id,
        )
        try:
            await asyncio.wait_for(member.node.propose(operation), timeout=1.0)
        except (TimeoutError, asyncio.TimeoutError, RaftError):
            # A delete that was neither confirmed nor refused may still have
            # committed -- the entry can be in the log with the answer lost on
            # the way back. Its fate is genuinely undecided, so the final check
            # must assert neither presence nor absence, and the id is released
            # from the durability obligation entirely.
            #
            # Demanding it survive is what made this soak report data loss that
            # the cluster had not suffered, and only under load, because load
            # is what makes a proposal time out.
            self.monitor.undecided.add(node_id)
            self.registered.remove(node_id)
            self.trace.record(
                step, Event.UNREGISTER,
                f"member {member_index} undecided {node_id[:8]}",
            )
            return
        self.monitor.unregistered.add(node_id)
        self.registered.remove(node_id)
        self.trace.record(
            step, Event.UNREGISTER, f"member {member_index} {node_id[:8]}",
        )

    # -- the end of the run ----------------------------------------------

    async def converge(self) -> None:
        """Heal everything, bring everyone back, and let the cluster settle.

        Convergence is only promised to a connected cluster, so the final
        durability claim is made here and not a moment earlier.
        """
        self.cluster.network.heal()
        self.cluster.network.max_delay = 0.0
        for index in sorted(self.down):
            self.cluster.network.resume(index)
        self.down.clear()
        self.blocked.clear()

        # Generously long: a member that spent the run stopped may need a
        # snapshot transfer, and this is a correctness check, not a timing one.
        for _ in range(60):
            await self.cluster.settle(10)
            self.monitor.check()
            if self._converged():
                return
        raise InvariantViolation(
            f"cluster did not converge after the run\n"
            f"{self.state()}\n\nTrace:\n{self.trace.render()}",
        )

    def _converged(self) -> bool:
        applied = {m.node.last_applied for m in self.cluster.members}
        return len(applied) == 1 and len(self.cluster.leaders) == 1

    def state(self) -> str:
        return "\n".join(
            f"  member {m.index}: role={m.node.role.value} term={m.node.term} "
            f"commit={m.node.commit_index} applied={m.node.last_applied} "
            f"log={m.node.log.first_index}..{m.node.log.last_index}"
            for m in self.cluster.members
        )


async def _soak(
    seed: int,
    size: int,
    tmp_path: Path,
    *,
    sockets: bool = False,
    steps: int | None = None,
) -> None:
    """One run, against either transport.

    The only thing that differs is what the cluster is made of. The driver, the
    invariants and the assertions are the same objects either way, which is
    what makes a socket-only failure mean "the transport" rather than "the
    other harness".
    """
    cluster: ChurnTarget
    if sockets:
        cluster = SocketCluster(size, tmp_path)
        elect_timeout, max_delay = 20.0, SOCKET_TIMING.heartbeat / 4
    else:
        cluster = Cluster(size, tmp_path)
        elect_timeout, max_delay = 5.0, FAST.heartbeat / 2

    await cluster.start()
    driver = ChurnDriver(cluster, seed, max_delay=max_delay)
    try:
        await cluster.elect(timeout=elect_timeout)
        await driver.run(steps if steps is not None else STEPS)
        await driver.converge()
        try:
            driver.monitor.check_acknowledged_writes_survived()
        except InvariantViolation as violation:
            # The trace belongs on this one too. A lost registration is the
            # one failure whose cause is always somewhere earlier in the run,
            # and a seed does not replay it (see the module docstring).
            raise InvariantViolation(
                f"{violation}\n\n{driver.state()}\n\n"
                f"Trace:\n{driver.trace.render(tail=len(driver.trace.lines))}",
            ) from violation
    finally:
        await cluster.close()


def _socket_seeds() -> tuple[int, ...]:
    count = os.environ.get("RAFT_SOAK_SOCKET_SEEDS")
    return tuple(range(1, int(count) + 1)) if count else SOCKET_SEEDS


def _seeds() -> tuple[int, ...]:
    """The committed seeds, or a longer sweep when asked for one."""
    count = os.environ.get("RAFT_SOAK_SEEDS")
    if count:
        return tuple(range(1, int(count) + 1))
    single = os.environ.get("RAFT_SOAK_SEED")
    if single:
        return (int(single),)
    return SEEDS


@pytest.mark.parametrize("seed", _seeds())
@pytest.mark.parametrize("size", [3, 5])
async def test_churn_preserves_every_safety_property(
    seed: int, size: int, tmp_path: Path,
) -> None:
    """Members fall over at arbitrary times; nothing Raft promises is broken.

    The assertion is not at the end. It is after every one of the ``STEPS``
    steps, via ``SafetyMonitor.check`` -- Figure 3's properties are true *at all
    times*, so checking them only at rest would miss precisely the transient
    violations that a converging cluster hides.
    """
    await _soak(seed, size, tmp_path)


@pytest.mark.parametrize("seed", _socket_seeds())
@pytest.mark.parametrize("size", [3, 5])
async def test_churn_over_real_sockets(
    seed: int, size: int, tmp_path: Path,
) -> None:
    """The same churn, over ``RaftTransport`` and real TCP.

    Everything the in-memory soak proves, it proves about a cluster that has
    never opened a socket. The transport, the ``Hello``/``HelloAck`` handshake,
    framing, checksums, the CONTROL/BULK split and every reconnection path are
    replaced by method calls there, and so are untested by it.

    Here they are real, and a partition really strands a TCP connection
    mid-stream -- see ``_proxy.py`` for why that needs a forwarder per directed
    link rather than a flag.

    Fewer seeds and fewer steps than the memory soak, deliberately: this is
    about *fidelity*, not coverage. Breadth is cheap on memory pipes and
    expensive on sockets, so the two are pointed at what each is good for. Turn
    it up with ``RAFT_SOAK_SOCKET_SEEDS`` before a release.
    """
    await _soak(seed, size, tmp_path, sockets=True, steps=SOCKET_STEPS)


class TestTheSocketFaultsActuallyBite:
    """Guard the guard, for the socket rig.

    A proxy that quietly forwarded everything would make every socket soak
    above pass while injecting nothing -- the failure mode of all fault
    injection, and invisible because it looks exactly like success. These
    assert that a cut is felt and that healing is recovered from, using the
    transport's own view of which peers it can reach.
    """

    async def test_a_cut_link_is_noticed_and_a_healed_one_recovered(
        self, tmp_path: Path,
    ) -> None:
        cluster = SocketCluster(3, tmp_path)
        await cluster.start()
        try:
            await cluster.elect(timeout=20.0)
            member = cluster.members[0]
            assert member.transport.live == frozenset({1, 2}), (
                "the rig did not reach a fully connected cluster to begin with"
            )

            cluster.network.stop(1)
            await _until(
                lambda: 1 not in member.transport.live,
                cluster, "member 0 never noticed that member 1 was cut off",
            )

            cluster.network.resume(1)
            await _until(
                lambda: 1 in member.transport.live,
                cluster, "member 0 never reconnected to member 1",
            )
        finally:
            await cluster.close()

    async def test_a_one_way_cut_is_felt_in_one_direction_only(
        self, tmp_path: Path,
    ) -> None:
        """The case the memory harness could express and sockets could not.

        Member 0 can no longer reach member 1, while member 1 still reaches
        member 0. Over TCP that needs a forwarder per *directed* link, which is
        the whole reason ``_proxy.py`` exists.
        """
        cluster = SocketCluster(3, tmp_path)
        await cluster.start()
        try:
            await cluster.elect(timeout=20.0)
            cluster.network.block(0, 1)

            await _until(
                lambda: 1 not in cluster.members[0].transport.live,
                cluster, "the outbound direction was never cut",
            )
            assert 0 in cluster.members[1].transport.live, (
                "the reverse direction was cut too, so this is a symmetric "
                "partition and not the one-way case it claims to be"
            )
        finally:
            await cluster.close()


async def _until(
    condition: Any, cluster: SocketCluster, complaint: str,
    *, timeout: float = 10.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if condition():
            return
        await asyncio.sleep(cluster.timing.heartbeat)
    raise AssertionError(complaint)


@pytest.mark.parametrize("size", [3, 5])
async def test_a_forgotten_quorum_still_recovers(
    size: int, tmp_path: Path,
) -> None:
    """Blow the budget on purpose: the data may go, the cluster may not.

    The regression test for the deadlock this soak found. Enough members
    restart, faster than they can be promoted, that a quorum of voters becomes
    impossible -- and the old non-voting rejoin then had no way out, because
    only a leader promotes and no leader could be elected. Terms climbed
    forever and the registry answered 503 until an operator deleted the term
    files.

    Nothing is asserted about the registrations: once a quorum has forgotten,
    entries that lived only on those members are genuinely gone, and pretending
    otherwise would be asserting something no consensus algorithm can deliver
    from a volatile log. What must survive is the *cluster*.
    """
    cluster = Cluster(size, tmp_path)
    await cluster.start()
    try:
        leader = await cluster.elect(timeout=5.0)
        survivor = next(
            m.index for m in cluster.members if m.index != leader.index
        )
        doomed = [m.index for m in cluster.members if m.index != survivor]

        # The survivor is cut off while the others restart, so it can neither
        # promote them nor be promoted -- which is what leaves a quorum
        # forgetful at the same instant.
        cluster.network.isolate(survivor)
        for index in doomed:
            await cluster.restart(index)
        cluster.network.heal()
        for index in doomed:
            cluster.network.resume(index)

        recovered = await cluster.elect(timeout=15.0)
        assert recovered.node.voting is True
    finally:
        await cluster.close()


async def test_a_whole_cluster_restart_comes_back(tmp_path: Path) -> None:
    """Stop every member and start them all again. The commonest case of all.

    An upgrade, a power cycle, a ``systemctl restart`` across the fleet: every
    member returns having forgotten, so under the original rule every member
    refused to vote and the cluster never elected anyone again. It stayed dead,
    answering 503, with nothing in the logs to say why.

    Runs on one ``tmp_path`` deliberately -- the term files must be the *same*
    files, because it is the persisted incarnation that makes the second boot
    different from the first.
    """
    first = Cluster(3, tmp_path)
    await first.start()
    try:
        await first.elect(timeout=5.0)
    finally:
        await first.close()

    second = Cluster(3, tmp_path)
    await second.start()
    try:
        leader = await second.elect(timeout=15.0)
        assert leader.node.term > 1, (
            "a restarted cluster must hold a fresh election, not resume the "
            "term it left"
        )
    finally:
        await second.close()


async def test_the_soak_can_actually_fail(tmp_path: Path) -> None:
    """Guard the guard: an injected violation must be caught.

    Without this, every green run above is consistent with a monitor that
    checks nothing -- the failure mode of every invariant suite, and one that
    is invisible precisely because it looks like success.
    """
    cluster = Cluster(3, tmp_path)
    await cluster.start()
    try:
        leader = await cluster.elect(timeout=5.0)
        monitor = SafetyMonitor(cluster=cluster)
        monitor.check()

        # Claim some other member led this same term. Election Safety must
        # notice, because that is the one thing it is for.
        impostor = next(
            m for m in cluster.members if m.index != leader.index
        )
        monitor.leaders_by_term[leader.node.term] = impostor.index

        with pytest.raises(InvariantViolation, match="Election Safety"):
            monitor.check()
    finally:
        await cluster.close()


async def test_acknowledged_write_loss_is_detected(tmp_path: Path) -> None:
    """The durability check must fail when a write really is missing.

    Same argument as above, aimed at the other half: the five Raft properties
    can all hold while the registry still loses a resource, so the check that
    would catch that has to be shown to work.
    """
    cluster = Cluster(3, tmp_path)
    await cluster.start()
    try:
        await cluster.elect(timeout=5.0)
        monitor = SafetyMonitor(cluster=cluster)
        monitor.acknowledged.add("11111111-2222-4333-8444-555555555555")

        with pytest.raises(InvariantViolation, match="acknowledged"):
            monitor.check_acknowledged_writes_survived()
    finally:
        await cluster.close()
