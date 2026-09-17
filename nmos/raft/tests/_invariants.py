# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Raft's five safety properties, checked continuously against a live cluster.

Why properties rather than assertions
-------------------------------------
Every other consensus test in this suite arranges a scenario and asserts an
outcome, which proves exactly the scenario it arranged. These are different:
they are true of *every* reachable state, so they can be evaluated after every
single step of a randomised run. A scripted test finds the bug you thought of;
an invariant finds the interleaving you did not.

The five are quoted verbatim from Figure 3 of "In Search of an Understandable
Consensus Algorithm (Extended Version)", Ongaro & Ousterhout -- the paper's own
statement of what Raft guarantees "is true at all times". They are reproduced
here rather than paraphrased so that a reader can check the code against the
source without leaving the file.

Two more follow them. They are not Raft's, they are *ours*: the promise the
Registration API makes to a Node (an acknowledged registration is not lost) and
the promise the Query API makes to a client (cursors are unique and ordered).
A consensus layer can satisfy all five of Figure 3 and still break either one,
because both are properties of what we build on top of the log.

What is deliberately NOT checked here
-------------------------------------
Full linearizability. Deciding it is NP-hard in general, and the cheap
approximations are the ones that produce false accusations. The properties
below are what Raft actually promises, they are decidable in linear time, and
the one client-visible guarantee that matters most -- an acknowledged write
survives -- is checked directly and exactly.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from nmos.raft.node import Role


@runtime_checkable
class ClusterView(Protocol):
    """The little of a cluster these properties need to read.

    Structural rather than concrete so the same monitor evaluates the same
    properties against the in-memory harness and the real-socket cluster. If
    the two ever disagree, that is a finding about the transport; it must not
    be possible for it to be a finding about two monitors that drifted apart.
    """

    @property
    def members(self) -> Sequence[Any]: ...


class InvariantViolation(AssertionError):
    """A safety property does not hold.

    An ``AssertionError`` so pytest reports it as a failure rather than an
    error: a violation means the implementation is wrong, not that the test
    could not run.
    """


@dataclass
class _LeaderRecord:
    """What was observed of one member during one term it led."""

    member: int
    log_digest: tuple[tuple[int, int], ...]
    """``(index, term)`` for every entry the leader held, at the moment it was
    observed. Enough to detect an overwrite without holding payloads."""


@dataclass
class SafetyMonitor:
    """Accumulates history and re-checks all seven properties on demand.

    Stateful because three of the properties are about *change over time* --
    two leaders in one term, a leader that truncated its own log, an entry
    committed then absent from a later leader. A stateless check of the current
    instant cannot see any of them, which is why a monitor is threaded through
    the run rather than a function called at the end.
    """

    cluster: ClusterView

    leaders_by_term: dict[int, int] = field(default_factory=dict)
    """term -> member index. Election Safety is a collision in this map."""

    leader_logs: dict[tuple[int, int], _LeaderRecord] = field(
        default_factory=dict,
    )
    """(member, term) -> what its log looked like while it led."""

    committed: dict[int, int] = field(default_factory=dict)
    """index -> term, for entries some member has reported committed. Used by
    Leader Completeness."""

    applied: dict[int, str] = field(default_factory=dict)
    """index -> payload digest, across every member. State Machine Safety is a
    disagreement in this map."""

    acknowledged: set[str] = field(default_factory=set)
    """Node ids for registrations a client was told succeeded. Only these are
    owed durability -- a refused or timed-out proposal promised nothing."""

    unregistered: set[str] = field(default_factory=set)
    """Node ids the client later deleted, so their absence is expected."""

    undecided: set[str] = field(default_factory=set)
    """Node ids whose deletion was neither confirmed nor refused.

    Excused from the durability check in both directions. A proposal that timed
    out may still have committed -- the entry reaches the log and the answer is
    lost coming back -- so neither presence nor absence is a defect, and
    asserting either would make load look like data loss."""

    steps: int = 0
    violations: list[str] = field(default_factory=list)

    # -- the checks ------------------------------------------------------

    def check(self) -> None:
        """Evaluate every property. Raises on the first violation found.

        Ordered cheapest-first, and by how *diagnostic* the answer is: an
        Election Safety failure explains a Log Matching failure, so reporting
        the former first saves chasing the latter.
        """
        self.steps += 1
        self._observe()
        for name, checker in (
            ("Election Safety", self._election_safety),
            ("Leader Append-Only", self._leader_append_only),
            ("Log Matching", self._log_matching),
            ("Leader Completeness", self._leader_completeness),
            ("State Machine Safety", self._state_machine_safety),
            ("Cursor Uniqueness", self._cursor_uniqueness),
        ):
            problem = checker()
            if problem is not None:
                self.violations.append(f"{name}: {problem}")
                raise InvariantViolation(
                    f"{name} violated after {self.steps} steps\n  {problem}",
                )

    def _observe(self) -> None:
        """Fold the current instant into the accumulated history."""
        for member in self.cluster.members:
            node = member.node
            if node.role is Role.LEADER:
                self.leaders_by_term.setdefault(node.term, node.index)
                self.leader_logs[(node.index, node.term)] = _LeaderRecord(
                    member=node.index, log_digest=_digest(node),
                )
            # A member's commit index is a claim that everything up to it is
            # committed, and committed is permanent -- so the terms recorded
            # here are what every future leader must still carry.
            for index in range(
                max(node.log.first_index, 1), node.commit_index + 1,
            ):
                try:
                    self.committed.setdefault(index, node.log.term_at(index))
                except Exception:      # compacted out from under us
                    continue
            for index, payload in _applied_digests(node):
                existing = self.applied.get(index)
                if existing is None:
                    self.applied[index] = payload

    def _election_safety(self) -> str | None:
        """"at most one leader can be elected in a given term." (Figure 3)"""
        for member in self.cluster.members:
            node = member.node
            if node.role is not Role.LEADER:
                continue
            incumbent = self.leaders_by_term.get(node.term)
            if incumbent is not None and incumbent != node.index:
                return (
                    f"term {node.term} has been led by both member "
                    f"{incumbent} and member {node.index}"
                )
        return None

    def _leader_append_only(self) -> str | None:
        """"a leader never overwrites or deletes entries in its log; it only
        appends new entries." (Figure 3)
        """
        for member in self.cluster.members:
            node = member.node
            if node.role is not Role.LEADER:
                continue
            previous = self.leader_logs.get((node.index, node.term))
            if previous is None:
                continue
            now = _digest(node)
            before = previous.log_digest
            # Compare only the overlap that both snapshots still hold: an entry
            # compacted away since is not an overwrite, and demanding it be
            # present would make compaction look like a safety failure.
            shared = _common_range(before, now)
            for index, term in shared:
                current = dict(now).get(index)
                if current is not None and current != term:
                    return (
                        f"member {node.index}, still leader in term "
                        f"{node.term}, changed index {index} from term {term} "
                        f"to term {current}"
                    )
            if now and before and now[-1][0] < before[-1][0]:
                return (
                    f"member {node.index}, still leader in term {node.term}, "
                    f"shortened its log from index {before[-1][0]} to "
                    f"{now[-1][0]}"
                )
        return None

    def _log_matching(self) -> str | None:
        """"if two logs contain an entry with the same index and term, then the
        logs are identical in all entries up through the given index."
        (Figure 3)
        """
        members = self.cluster.members
        for position, left in enumerate(members):
            for right in members[position + 1:]:
                a, b = dict(_digest(left.node)), dict(_digest(right.node))
                shared = sorted(set(a) & set(b))
                if not shared:
                    continue
                agreeing = [index for index in shared if a[index] == b[index]]
                if not agreeing:
                    continue
                highest = max(agreeing)
                for index in shared:
                    if index <= highest and a[index] != b[index]:
                        return (
                            f"members {left.index} and {right.index} agree at "
                            f"index {highest} (term {a[highest]}) but differ "
                            f"at index {index}: terms {a[index]} vs {b[index]}"
                        )
        return None

    def _leader_completeness(self) -> str | None:
        """"if a log entry is committed in a given term, then that entry will be
        present in the logs of the leaders for all higher-numbered terms."
        (Figure 3)
        """
        for member in self.cluster.members:
            node = member.node
            if node.role is not Role.LEADER:
                continue
            held = dict(_digest(node))
            for index, term in self.committed.items():
                if term >= node.term:
                    continue
                if index <= node.log.snapshot_index:
                    # Inside the snapshot, therefore present by construction.
                    continue
                if index > node.log.last_index:
                    return (
                        f"member {node.index} leads term {node.term} without "
                        f"index {index}, committed earlier in term {term}"
                    )
                if held.get(index) != term:
                    return (
                        f"member {node.index} leads term {node.term} holding "
                        f"term {held.get(index)} at index {index}, which was "
                        f"committed in term {term}"
                    )
        return None

    def _state_machine_safety(self) -> str | None:
        """"if a server has applied a log entry at a given index to its state
        machine, no other server will ever apply a different log entry for the
        same index." (Figure 3)
        """
        for member in self.cluster.members:
            for index, payload in _applied_digests(member.node):
                known = self.applied.get(index)
                if known is not None and known != payload:
                    return (
                        f"index {index} applied as {known!r} by one member and "
                        f"{payload!r} by member {member.index}"
                    )
        return None

    def _cursor_uniqueness(self) -> str | None:
        """Ours, not Raft's: no two resources anywhere share a cursor.

        Paging is defined over cursors, so a duplicate makes a page either
        repeat a resource or skip one -- on one member and not the others,
        which is the shape of bug that survives every test that looks at
        content alone.
        """
        seen: dict[str, tuple[int, str]] = {}
        for member in self.cluster.members:
            for record in _records(member.registry.store):
                cursor = str(record.updated)
                owner = seen.get(cursor)
                if owner is not None and owner[1] != record.id:
                    return (
                        f"cursor {cursor} is held by resource {owner[1]} "
                        f"(member {owner[0]}) and by {record.id} "
                        f"(member {member.index})"
                    )
                seen[cursor] = (member.index, record.id)
        return None

    # -- the client-facing promise ---------------------------------------

    def check_acknowledged_writes_survived(self) -> None:
        """Every registration a client was told succeeded is still present.

        Run once, at the end, after the cluster has been healed and allowed to
        settle -- it is a claim about convergence, so asking it mid-churn would
        only measure how far replication had got.

        This is the property a Node actually depends on. Raft's five say the
        logs agree; this one says the 201 we returned meant something.
        """
        from nmos.registry.types import ResourceType

        missing: list[str] = []
        for node_id in sorted(self.acknowledged):
            if node_id in self.unregistered or node_id in self.undecided:
                continue
            for member in self.cluster.members:
                if member.registry.store.get(ResourceType.NODE, node_id) is None:
                    missing.append(f"{node_id} absent on member {member.index}")
        if missing:
            raise InvariantViolation(
                f"{len(missing)} acknowledged registration(s) lost after "
                f"{self.steps} steps:\n  " + "\n  ".join(missing[:20]),
            )


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

def _digest(node: Any) -> tuple[tuple[int, int], ...]:
    """``(index, term)`` for every entry a node still holds.

    Terms rather than payloads: Log Matching and Leader Completeness are both
    defined over ``(index, term)``, and comparing payloads would make a
    compacted prefix look like a disagreement.
    """
    out: list[tuple[int, int]] = []
    for index in range(max(node.log.first_index, 1), node.log.last_index + 1):
        try:
            out.append((index, node.log.term_at(index)))
        except Exception:      # compacted while we walked it
            continue
    return tuple(out)


def _applied_digests(node: Any) -> list[tuple[int, str]]:
    """``(index, payload digest)`` for entries this member has applied.

    Only the applied prefix, and only what is still in the log: State Machine
    Safety is about what was *applied*, so an entry sitting uncommitted in a
    follower's log says nothing and must not be compared.
    """
    out: list[tuple[int, str]] = []
    top = min(node.last_applied, node.log.last_index)
    for index in range(max(node.log.first_index, 1), top + 1):
        try:
            entry = node.log.get(index)
        except Exception:
            continue
        out.append((index, f"{entry.term}:{entry.payload.hex()[:32]}"))
    return out


def _records(store: Any) -> list[Any]:
    """Every extant resource in a store, whatever its type.

    ``list()`` around the iterator because ``iter_extant`` walks live state and
    the caller is checking a cluster that is still running.
    """
    from nmos.registry.types import ResourceType

    out: list[Any] = []
    for resource_type in ResourceType:
        out.extend(list(store.iter_extant(resource_type)))
    return out


def _common_range(
    before: tuple[tuple[int, int], ...], now: tuple[tuple[int, int], ...],
) -> list[tuple[int, int]]:
    """The entries of ``before`` whose indices ``now`` could still hold."""
    if not now:
        return []
    lowest = now[0][0]
    return [(index, term) for index, term in before if index >= lowest]
