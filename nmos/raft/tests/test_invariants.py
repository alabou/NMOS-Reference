# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The safety monitor's own properties, against histories written out by hand.

The monitor decides what counts as a safety failure, so a rule it gets wrong
fails correct runs -- or passes broken ones -- in every soak at once. Each
history here is one a soak produced and that was decoded message by message,
replayed as the observations the monitor would have made of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nmos.raft.node import Role
from nmos.raft.tests._invariants import SafetyMonitor


@dataclass
class _Log:
    """The part of a log the monitor reads: ``index -> term``, above a snapshot."""

    terms: dict[int, int]
    snapshot_index: int

    @property
    def first_index(self) -> int:
        return self.snapshot_index + 1

    @property
    def last_index(self) -> int:
        return max(self.terms, default=self.snapshot_index)

    def term_at(self, index: int) -> int:
        return self.terms[index]


@dataclass
class _Node:
    index: int
    role: Role
    term: int
    log: _Log
    commit_index: int
    # Nothing applied, so the payload-level properties have nothing to read:
    # these histories are about which terms leaders hold.
    last_applied: int = 0


@dataclass
class _Member:
    node: _Node


@dataclass
class _Cluster:
    members: list[_Member] = field(default_factory=list)


def _log(snapshot_index: int, **terms: int) -> _Log:
    """``_log(133, i134=11, i135=11)`` -- entries above a snapshot, by index."""
    return _Log(
        terms={int(name[1:]): term for name, term in terms.items()},
        snapshot_index=snapshot_index,
    )


def _through(last: int, term: int, *, snapshot_index: int) -> dict[str, int]:
    return {f"i{index}": term for index in range(snapshot_index + 1, last + 1)}


class TestLeaderCompleteness:
    """Owed to the leaders of terms after the one it was committed *in*.

    "if a log entry is committed in a given term, then that entry will be
    present in the logs of the leaders for all higher-numbered terms." (Figure
    3). The term it was committed in is not its own term: a leader commits an
    entry of an earlier term only indirectly, when one of its own term commits
    above it (``commit.rs``, "The current-term check"). The monitor took the
    entry's term for the commit term, and so owed an entry of term 11,
    committed in term 13, to a leader of term 12 elected before it.
    """

    @staticmethod
    def _seed_141387() -> tuple[SafetyMonitor, _Cluster]:
        """Seed 141387, run 2, decoded. m2 led term 11 and appended (141, t11),
        which reached nobody. m1 won term 12 with m0's vote -- both ended at
        (140, t11) -- and led it, cut off, appending (141, t12). m2 won term 13,
        gave m0 (141, t11) and committed its own (142, t13) above it: (141, t11)
        committed in term 13. m1 still led term 12 when that was observed."""
        base = _through(140, 11, snapshot_index=133)
        m0 = _Node(0, Role.FOLLOWER, 12, _log(133, **base), commit_index=140)
        m1 = _Node(1, Role.LEADER, 12, _log(133, **base, i141=12), commit_index=140)
        m2 = _Node(2, Role.FOLLOWER, 12, _log(133, **base, i141=11), commit_index=140)
        cluster = _Cluster([_Member(m0), _Member(m1), _Member(m2)])
        monitor = SafetyMonitor(cluster=cluster)
        monitor._observe()
        assert monitor._leader_completeness() is None

        m2.role, m2.term, m2.commit_index = Role.LEADER, 13, 142
        m2.log = _log(133, **base, i141=11, i142=13)
        m0.term, m0.commit_index = 13, 142
        m0.log = _log(133, **base, i141=11, i142=13)
        monitor._observe()
        return monitor, cluster

    def test_an_entry_committed_after_a_leader_was_elected_is_not_owed_to_it(
        self,
    ) -> None:
        monitor, _ = self._seed_141387()

        problem = monitor._leader_completeness()

        assert problem is None, (
            f"a leader of term 12 was held to an entry first committed in "
            f"term 13: {problem}"
        )

    def test_a_leader_elected_after_the_commit_still_owes_it(self) -> None:
        # The rule's other side, which must not weaken: once (141, t11) is
        # committed in term 13, a leader of any later term lacking it is a
        # real violation.
        monitor, cluster = self._seed_141387()
        m1 = cluster.members[1].node
        m1.term = 14
        m1.log = _log(133, **_through(140, 11, snapshot_index=133), i141=14)

        problem = monitor._leader_completeness()

        assert problem is not None and "index 141" in problem, (
            f"a leader of term 14 without the entry committed in term 13 "
            f"passed: {problem}"
        )
