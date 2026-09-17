# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Raft under process-level faults: SIGKILL, SIGSTOP, real restarts.

    pytest nmos/registry/tests/test_raft_process_faults.py -m e2e

The third and least observable rung of the fault testing, and an addition to
the other two rather than a replacement. ``nmos/raft/tests/test_chaos_soak.py``
evaluates all five of Raft's safety properties after every step, because there
the members are objects and their logs are readable; that is where the
algorithm is tested and it is where four of the five bugs found so far were
caught. Out here the members are processes, so the assertions are only what an
operator can see -- and the faults are ones no in-process rig can produce.

What is only reachable from here
--------------------------------
* **SIGKILL** rather than a graceful close. ``persist.py``'s term file is the
  one piece of durable state the election-safety argument depends on, written
  with an atomic rename plus a directory fsync. Nothing else in the suite kills
  a process mid-write and then asks whether that file can still be read.
* **SIGSTOP**, which is *not* unreachability and cannot be expressed anywhere
  else. A frozen member keeps its TCP connections open -- no RST, no FIN, the
  kernel still ACKing into its receive buffer -- so it looks alive to every
  peer while answering nothing at all. The in-process rigs' ``stop()`` means
  unreachable, which is the opposite signal, and this is the case check-quorum,
  the leader lease and the replication pause all interact over.
* **Genuinely independent scheduling**, since each member has its own
  interpreter and its own event loop.

Skips rather than fails when it cannot run, matching the other e2e rigs.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from nmos.registry.tests._fixtures import make_node
from nmos.registry.tests._processes import ProcessCluster

pytestmark = pytest.mark.e2e

# Real processes need real time: an interpreter start, a TLS-free bind, an
# election window and a snapshot are all seconds rather than milliseconds.
READY_TIMEOUT = 90.0
CONVERGE_TIMEOUT = 60.0


@pytest.fixture
def cluster(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ProcessCluster]:
    size = getattr(request, "param", 3)
    created = ProcessCluster(size, tmp_path)
    created.start()
    try:
        if not created.await_writable(0, _node(), timeout=READY_TIMEOUT):
            _skip_with_output(created, "the cluster never accepted a write")
        yield created
    finally:
        created.stop_all()


def _node() -> dict:
    return make_node(str(uuid.uuid4()))


def _skip_with_output(cluster: ProcessCluster, why: str) -> None:
    """Skip, quoting whatever the processes managed to say.

    A bare skip here would hide the usual causes -- a busy port, a missing
    dependency, an import error -- behind a message that looks like a
    deliberate exclusion.
    """
    tails = []
    for member in cluster.members:
        try:
            text = member.stdout_path.read_text(errors="replace")
        except OSError:
            continue
        if text.strip():
            tails.append(f"m{member.index}: {text.strip()[-600:]}")
    pytest.skip(f"{why}\n" + "\n".join(tails))


def _converged_on(
    cluster: ProcessCluster, node_id: str, indices: list[int],
    timeout: float = CONVERGE_TIMEOUT,
) -> None:
    deadline = time.monotonic() + timeout
    pending = set(indices)
    while pending and time.monotonic() < deadline:
        for index in sorted(pending):
            try:
                if node_id in cluster.node_ids(index):
                    pending.discard(index)
            except Exception:
                pass
        if pending:
            time.sleep(0.25)
    assert not pending, (
        f"{node_id} never reached member(s) {sorted(pending)} within "
        f"{timeout}s"
    )


class TestSigkill:
    """A crash, not a close."""

    def test_the_term_file_survives_a_kill_and_the_member_rejoins(
        self, cluster: ProcessCluster,
    ) -> None:
        """The claim ``persist.py``'s atomic rename exists to make.

        A member killed outright while writing its term file must come back
        able to read it. If the rename were not atomic, or the directory entry
        not fsynced, the file could be absent or truncated -- and a member that
        cannot recover its term is a member that may vote twice in one term,
        which is the failure the whole file exists to prevent.

        Killed under load rather than idle, because the file is only written
        when the term changes and a quiet cluster never changes terms.
        """
        victim = 2
        for _ in range(4):
            cluster.register(0, _node())
        cluster.kill(victim)

        # The survivors carry on: two of three is a quorum.
        survivor_node = _node()
        assert cluster.await_writable(0, survivor_node, timeout=CONVERGE_TIMEOUT)

        cluster.restart(victim)
        assert cluster.await_writable(victim, _node(), timeout=READY_TIMEOUT), (
            "the killed member never accepted a write again, which is what a "
            "term file it could not read would look like"
        )
        _converged_on(cluster, survivor_node["id"], [0, 1, victim])

    def test_killing_the_leader_lets_the_cluster_elect_another(
        self, cluster: ProcessCluster,
    ) -> None:
        leader = cluster.leader_index()
        assert leader is not None, "no leader was ever announced in the logs"
        survivors = [m.index for m in cluster.members if m.index != leader]

        cluster.kill(leader)

        node = _node()
        assert cluster.await_writable(
            survivors[0], node, timeout=CONVERGE_TIMEOUT,
        ), "the survivors never elected a replacement"
        _converged_on(cluster, node["id"], survivors)


class TestSigstop:
    """The fault no in-process rig can produce.

    A frozen member is not unreachable. Its sockets stay open and its peers go
    on believing it is there -- which is the difference between "this member is
    gone" and "this member is present and useless", and only the second one
    exercises the paths that wait for an answer.
    """

    def test_a_frozen_follower_does_not_stop_the_cluster(
        self, cluster: ProcessCluster,
    ) -> None:
        leader = cluster.leader_index()
        assert leader is not None
        frozen = next(m.index for m in cluster.members if m.index != leader)
        writer = next(
            m.index for m in cluster.members
            if m.index not in (leader, frozen)
        )

        cluster.freeze(frozen)
        try:
            node = _node()
            assert cluster.await_writable(
                writer, node, timeout=CONVERGE_TIMEOUT,
            ), (
                "a single frozen follower stopped a three-member cluster from "
                "committing, though two members is a quorum"
            )
            _converged_on(cluster, node["id"], [leader, writer])
        finally:
            cluster.thaw(frozen)

        # And it catches up once it is running again, rather than staying
        # behind a replication pause that nothing released.
        _converged_on(cluster, node["id"], [frozen])

    def test_a_frozen_leader_is_replaced_and_steps_down_on_thaw(
        self, cluster: ProcessCluster,
    ) -> None:
        """The interesting half, and the reason SIGSTOP is worth the trouble.

        A frozen leader stops heartbeating but never closes a connection, so
        its followers time out and elect a replacement while it still believes
        it leads. When it resumes it is a leader in a stale term with live
        links to everyone -- and it must discover that and stand down, not
        resume issuing appends.
        """
        leader = cluster.leader_index()
        assert leader is not None
        survivors = [m.index for m in cluster.members if m.index != leader]

        cluster.freeze(leader)
        try:
            node = _node()
            assert cluster.await_writable(
                survivors[0], node, timeout=CONVERGE_TIMEOUT,
            ), "the survivors never replaced a frozen leader"
            _converged_on(cluster, node["id"], survivors)
        finally:
            cluster.thaw(leader)

        # The thawed member rejoins as a follower and catches up. If it went on
        # believing it led, it would answer writes it could never commit.
        _converged_on(cluster, node["id"], [leader], timeout=CONVERGE_TIMEOUT)

        after = _node()
        assert cluster.await_writable(
            survivors[0], after, timeout=CONVERGE_TIMEOUT,
        )
        _converged_on(cluster, after["id"], [0, 1, 2])


class TestRollingRestart:
    """The documented upgrade procedure, with real processes.

    Paced by promotion rather than by a sleep: the constraint this backend
    actually has is that a member must be caught up before the next one goes,
    and "caught up" is observable from out here only as "it answers, and it has
    the data".
    """

    def test_acknowledged_registrations_survive_a_paced_rolling_restart(
        self, cluster: ProcessCluster,
    ) -> None:
        acknowledged: list[str] = []
        for index in range(len(cluster.members)):
            node = _node()
            assert cluster.await_writable(0, node, timeout=CONVERGE_TIMEOUT)
            acknowledged.append(node["id"])

            cluster.restart(index)
            # Wait for it to be serving again *and* holding what came before,
            # which is the observable form of "promoted".
            assert cluster.await_writable(
                index, _node(), timeout=READY_TIMEOUT,
            ), f"member {index} never came back"
            _converged_on(cluster, node["id"], [index])

        everywhere = list(range(len(cluster.members)))
        for node_id in acknowledged:
            _converged_on(cluster, node_id, everywhere)


class TestTheProcessFaultsActuallyBite:
    """Guard the guard, for this rig.

    Every test above would pass just as happily against faults that did
    nothing -- a SIGSTOP delivered to the wrong process, a kill that left the
    interpreter running, a leader lookup that always returned member 0. That is
    the standing failure mode of fault injection: it looks exactly like
    success. These assert the mechanisms directly, from outside.
    """

    def test_a_frozen_member_stops_answering_and_answers_again_on_thaw(
        self, cluster: ProcessCluster,
    ) -> None:
        """SIGSTOP must actually suspend the process.

        Checked with a short deadline on purpose. A frozen member does not
        *refuse* connections -- its listening socket is open and the kernel
        completes the handshake on its behalf -- so the observable difference
        between frozen and running is that the request hangs rather than being
        rejected. A check without a deadline would simply wait.
        """
        target = 1
        assert cluster.answers(target, timeout=10.0), (
            "the member was not answering before being frozen, so freezing it "
            "would prove nothing"
        )

        cluster.freeze(target)
        try:
            assert not cluster.answers(target, timeout=2.0), (
                "a frozen member went on serving requests, so SIGSTOP reached "
                "nothing and every fault above is inert"
            )
        finally:
            cluster.thaw(target)

        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if cluster.answers(target, timeout=5.0):
                return
            time.sleep(0.25)
        raise AssertionError("a thawed member never started answering again")

    def test_a_killed_member_is_gone(self, cluster: ProcessCluster) -> None:
        target = 2
        assert cluster.members[target].alive
        cluster.kill(target)
        assert not cluster.members[target].alive
        assert not cluster.answers(target, timeout=2.0)

    def test_the_leader_lookup_names_a_real_member(
        self, cluster: ProcessCluster,
    ) -> None:
        """Leadership is read from the logs, so the reading must be checked.

        A lookup that always answered "member 0" would make the leader-specific
        tests above quietly test a follower instead.
        """
        leader = cluster.leader_index()
        assert leader is not None, "no leader was ever announced in the logs"
        assert 0 <= leader < len(cluster.members)

        # Killing it must eventually produce a *different* answer, which a
        # constant or a stale read could not do.
        cluster.kill(leader)
        survivors = [m.index for m in cluster.members if m.index != leader]
        assert cluster.await_writable(
            survivors[0], _node(), timeout=CONVERGE_TIMEOUT,
        )
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            replacement = cluster.leader_index()
            if replacement is not None and replacement != leader:
                assert replacement in survivors
                return
            time.sleep(0.25)
        raise AssertionError(
            f"the leader lookup still names member {leader} after it was "
            f"killed, so it is not reading current state",
        )
