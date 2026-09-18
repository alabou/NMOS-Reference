# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The soak's forensics must actually be recording when a soak fails.

A recorder that is wired to nothing looks exactly like a run in which nothing
interesting happened: empty buffers, no error, a green suite. And the moment it
matters is the moment it is too late to find out -- a soak violation is rare and
not replayable from its seed, so a failure that arrives without the evidence is
one that has to be waited for all over again.

So two things are pinned here.

**That the wiring exists.** A short real churn run must leave commits, votes and
leaderships in the buffer. This is the test that fails if someone removes
``ChurnDriver._record_votes``, or the ``note_commit`` call from
``SafetyMonitor._observe``, or forgets to set ``forensics.step``.

Votes are recorded by wrapping ``on_request_vote`` on each node rather than by
hooking the harness's dispatch loop, because there are two transports and only
the in-memory one has a dispatch loop: over real sockets a vote arrives through
``RaftTransport`` and never passes through the harness. A network-level hook
recorded nothing for precisely the runs the socket soak exists to cover, and did
it silently.

**That the report can be aimed.** ``_disputed`` reads the index and term back
out of an invariant's own message so the report can be filtered to the records
that bear on the failure. That is a seam between two files -- prose on one side,
a regular expression on the other -- and the messages below are copied verbatim
from ``_invariants.py`` so that rewording one without the other fails here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nmos.raft.tests._forensics import Forensics
from nmos.raft.tests._harness import FAST, Cluster
from nmos.raft.tests._invariants import InvariantViolation
from nmos.raft.tests._sockets import SOCKET_TIMING, SocketCluster
from nmos.raft.tests.test_chaos_soak import ChurnDriver, _disputed


class TestDisputedReadsTheInvariantMessages:
    """Every message an invariant can raise, and what should be read from it.

    Copied verbatim from ``_invariants.py``. If a message is reworded and this
    is not, the report silently stops being filtered -- it still renders, just
    without the records that explain anything.
    """

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            # Leader Completeness -- the one this was built for.
            (
                "member 2 leads term 7 holding term 7 at index 11, which was "
                "committed in term 6",
                (11, 7),
            ),
            (
                "member 2 leads term 7 without index 11, committed earlier in "
                "term 6",
                (11, 7),
            ),
            # State Machine Safety.
            (
                "index 7 applied as '2:0802' by one member and '3:0800' by "
                "member 1",
                (7, None),
            ),
            # Election Safety names a term and no index.
            (
                "term 4 has been led by both member 0 and member 2",
                (None, 4),
            ),
            # Leader Append-Only.
            (
                "member 1, still leader in term 3, changed index 9 from term 2 "
                "to term 3",
                (9, 3),
            ),
            (
                "member 1, still leader in term 3, shortened its log from "
                "index 9 to 7",
                (9, 3),
            ),
            # Log Matching names indices but no term of its own.
            (
                "members 0 and 1 agree at index 5 (term 2) but differ at "
                "index 6: terms 2 vs 3",
                (5, 2),
            ),
            # Nothing to aim at is not an error.
            ("something nobody anticipated", (None, None)),
        ],
    )
    def test_the_index_and_term_are_read_back(
        self, message: str, expected: tuple[int | None, int | None],
    ) -> None:
        assert _disputed(message) == expected

    def test_a_malformed_message_does_not_raise(self) -> None:
        """A report that crashed would replace the failure with its own.

        The one thing this seam must never do is turn a safety violation into a
        parse error, because then the violation is lost.
        """
        for message in ("", "index", "term", "index x term y", "\x00"):
            assert _disputed(message) == (None, None)


class _PeerLike:
    """Just enough of a peer state for a commit witness to read."""

    def __init__(self, match_index: int) -> None:
        self.match_index = match_index


class _FollowerLike:
    """A member holding an index committed, with no leader ever seen doing so."""

    def __init__(self) -> None:
        from nmos.raft.node import Role

        self.index = 1
        self.role = Role.FOLLOWER
        self.term = 1
        self.commit_index = 3
        self.log = type("L", (), {"first_index": 1, "last_index": 5})()
        self._peers: dict[int, Any] = {}


def deciders_index(driver: ChurnDriver) -> list[int]:
    """Indices seen both by some member and by a leader."""
    return sorted(set(driver.forensics.commits) & set(driver.forensics.deciders))


class TestEveryViolationArrivesWithItsEvidence:
    """A safety violation must carry the forensics, wherever it is found.

    ``ChurnDriver.run`` has always attached them. ``converge`` did not, and that
    is where a real failure landed: a full-gate run reported only

        Leader Completeness violated after 121 steps
          member 2 leads term 10 holding term 10 at index 21, which was
          committed in term 6

    with no record of which member first claimed index 21 or which votes
    elected term 10 -- all of it sitting unread in ``driver.forensics``. The
    gap mattered more at converge than during the run, because converge is
    where members that spent the run partitioned rejoin, which is exactly when
    a completeness failure surfaces.
    """

    @staticmethod
    def _rigged(driver: ChurnDriver) -> None:
        """Make the next safety check fail, the way a real one would."""
        def explode() -> None:
            raise InvariantViolation(
                "Leader Completeness violated after 1 steps\n"
                "  member 2 leads term 10 holding term 10 at index 21, "
                "which was committed in term 6",
            )
        driver.monitor.check = explode  # type: ignore[method-assign]

    async def test_converge_attaches_the_forensics_and_the_trace(
        self, tmp_path: Path,
    ) -> None:
        cluster = Cluster(3, tmp_path, timing=FAST)
        await cluster.start()
        try:
            driver = ChurnDriver(cluster, seed=1, max_delay=0.0)
            await driver.run(20)
            self._rigged(driver)
            with pytest.raises(InvariantViolation) as caught:
                await driver.converge()
        finally:
            await cluster.close()

        message = str(caught.value)
        assert "Forensics:" in message, (
            "converge raised a bare violation -- the evidence that explains it "
            f"was never attached:\n{message}"
        )
        assert "Trace:" in message, "converge dropped the event trace"
        assert "member 2 leads term 10" in message, "the original cause was lost"

    async def test_run_attaches_them_too(self, tmp_path: Path) -> None:
        """The half that already worked, pinned so it cannot regress alone."""
        cluster = Cluster(3, tmp_path, timing=FAST)
        await cluster.start()
        try:
            driver = ChurnDriver(cluster, seed=1, max_delay=0.0)
            await driver.run(5)
            self._rigged(driver)
            with pytest.raises(InvariantViolation) as caught:
                await driver.run(1)
        finally:
            await cluster.close()

        assert "Forensics:" in str(caught.value)
        assert "Trace:" in str(caught.value)


class TestTheRecorderIsWiredToSomething:
    """A short real run, asserting the buffers filled.

    Short deliberately: this is a wiring test, not a soak. It must be cheap
    enough to sit in the ordinary gate, because the failure it catches -- a
    recorder connected to nothing -- is silent by construction and would
    otherwise be found only by a soak failure that arrived with no evidence.
    """

    async def test_a_churn_run_records_commits_votes_and_leaders(
        self, tmp_path: Path,
    ) -> None:
        cluster = Cluster(3, tmp_path, timing=FAST)
        await cluster.start()
        try:
            driver = ChurnDriver(cluster, seed=1, max_delay=0.0)
            await driver.run(40)
        finally:
            await cluster.close()

        forensics = driver.forensics

        assert forensics.commits, (
            "no commit provenance recorded -- SafetyMonitor._observe is no "
            "longer calling note_commit, so a Leader Completeness failure "
            "would arrive with nothing to explain it"
        )
        assert forensics.votes, (
            "no votes recorded -- ChurnDriver._record_votes is no longer "
            "wrapping on_request_vote, so an election could not be explained"
        )
        assert forensics.leaderships, "no leadership recorded"

        # Steps are what place a record in time. All-zero means the driver
        # stopped advancing `forensics.step` and every record claims step 0.
        assert any(w.step > 0 for w in forensics.commits.values()), (
            "every commit claims step 0 -- ChurnDriver.run is not advancing "
            "forensics.step"
        )

    async def test_votes_are_recorded_over_real_sockets_too(
        self, tmp_path: Path,
    ) -> None:
        """The transport that has no dispatch loop to hook.

        This is the regression test for a hole the recorder shipped with. Votes
        were first hooked in ``_harness._dispatch``, which only exists for the
        in-memory transport: over sockets a ``RequestVote`` arrives through
        ``RaftTransport`` and never passes through the harness, so the hook
        recorded nothing and said nothing about it.

        That is the worse half. The socket soak is where the first Raft defect
        of this session actually surfaced, so the one transport whose failures
        most needed explaining was the one producing no evidence.

        Short, and a socket cluster is not cheap -- but the failure it catches
        is silent, and the alternative is discovering it from a soak failure
        that arrived empty.
        """
        cluster = SocketCluster(3, tmp_path, timing=SOCKET_TIMING)
        await cluster.start()
        try:
            driver = ChurnDriver(cluster, seed=1, max_delay=0.0)
            await driver.run(25)
        finally:
            await cluster.close()

        assert driver.forensics.votes, (
            "no votes recorded over sockets -- the recorder is hooked to "
            "something only the in-memory transport has"
        )
        assert driver.forensics.commits, "no commit provenance over sockets"

    async def test_a_commit_witness_carries_the_evidence_not_just_the_claim(
        self, tmp_path: Path,
    ) -> None:
        """The distinction the whole file exists for.

        "Index 11 was committed" is the claim. "Member 0, leading term 2, with
        peers matching at 11 and 11" is the evidence, and only the second tells
        you whether the claim was sound. A witness recorded from a leader must
        carry its peers' match indices.
        """
        cluster = Cluster(3, tmp_path, timing=FAST)
        await cluster.start()
        try:
            driver = ChurnDriver(cluster, seed=2, max_delay=0.0)
            await driver.run(40)
        finally:
            await cluster.close()

        deciders = list(driver.forensics.deciders.values())
        assert deciders, (
            "no index was ever observed committed at a leader, so no quorum "
            "could be checked after the fact -- note_commit is no longer "
            "recording the leader's view separately from the first claimant's"
        )
        assert all(w.role == "LEADER" for w in deciders)
        assert all(w.peers for w in deciders), (
            "a decider has no peer match indices, so the quorum it committed "
            "on cannot be checked after the fact"
        )

        # Whether the two differ in any *particular* run is a coin flip -- the
        # monitor walks members in index order, so they coincide whenever
        # member 0 happens to be the leader. The mechanism is asserted
        # directly in `test_a_leaders_view_is_kept_even_when_a_follower_claims_first`
        # instead of being fished for here.

    async def test_the_report_names_the_index_it_was_aimed_at(
        self, tmp_path: Path,
    ) -> None:
        """Rendering is only useful if it answers the question that was asked."""
        cluster = Cluster(3, tmp_path, timing=FAST)
        await cluster.start()
        try:
            driver = ChurnDriver(cluster, seed=3, max_delay=0.0)
            await driver.run(40)
        finally:
            await cluster.close()

        index = min(driver.forensics.commits)
        report = driver.forensics.render(index=index, term=3)

        assert f"index {index} first seen committed" in report
        assert "-- votes in terms" in report
        assert "-- restarts --" in report

    def test_a_leaders_view_is_kept_even_when_a_follower_claims_first(self) -> None:
        """The two-witness split, asserted on the mechanism rather than a run.

        `SafetyMonitor._observe` walks members in index order, so the first
        member seen holding an index committed is usually whichever has the
        lowest number -- not the one that decided. A follower's commit index is
        something it was *told*; only a leader's carries the `match_index`
        values the quorum was counted on. Keeping just the first would throw
        away the only record that can answer "was that quorum real?".
        """
        class _Log:
            first_index = 1
            last_index = 9

        class _Node:
            def __init__(self, index: int, role: Any) -> None:
                self.index = index
                self.role = role
                self.term = 4
                self.commit_index = 7
                self.log = _Log()
                self._peers: dict[int, Any] = {}

        from nmos.raft.node import Role

        forensics = Forensics()

        follower = _Node(0, Role.FOLLOWER)
        forensics.note_commit(7, follower, entry_term=4)

        leader = _Node(2, Role.LEADER)
        leader._peers = {0: _PeerLike(7), 1: _PeerLike(7)}
        forensics.note_commit(7, leader, entry_term=4)

        assert forensics.commits[7].member == 0, "the first claim was not kept"
        assert forensics.deciders[7].member == 2, "the leader's view was not kept"
        assert forensics.deciders[7].peers == ((0, 7), (1, 7)), (
            "the quorum the leader counted was not recorded, so it cannot be "
            "checked after the fact"
        )

        report = forensics.render(index=7, term=4)
        assert "and, as decided at a leader:" in report

    def test_an_index_no_leader_was_seen_holding_says_so(self) -> None:
        """Silence would read as "the quorum was fine"."""
        from nmos.raft.node import Role

        forensics = Forensics()
        forensics.note_commit(3, _FollowerLike(), entry_term=1)
        report = forensics.render(index=3, term=1)
        assert "no leader was ever observed holding this index" in report

    def test_an_unknown_index_says_so_rather_than_rendering_nothing(self) -> None:
        """An index never seen committed is itself a finding.

        It would mean the violation is about an entry no member ever claimed --
        which points at the monitor rather than at the implementation, and is
        worth saying out loud instead of leaving a blank line.
        """
        report = Forensics().render(index=999, term=1)
        assert "never observed as committed" in report
