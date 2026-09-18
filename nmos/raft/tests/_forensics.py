# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Record why a chaos run reached a bad state, not just that it did.

``_Trace`` in ``test_chaos_soak.py`` records what the *driver* did -- stop
member 3, heal, register at member 0. That is enough to see the shape of a run
and nowhere near enough to explain a safety violation, because none of the
inputs the implementation actually branched on are in it.

A concrete example, and the reason this file exists. A soak run failed with::

    Leader Completeness violated after 108 steps
      member 2 leads term 7 holding term 7 at index 11,
      which was committed in term 6

To act on that you have to answer three questions, and the driver trace answers
none of them:

1. **Was index 11 genuinely committed?** Some member reported a
   ``commit_index`` covering it. Which member, in which role, and -- if it was
   the leader -- what ``match_index`` values was it counting at the time? A
   leader that committed on a real quorum and a follower that committed on a
   bad ``leader_commit`` are different bugs with different fixes.
2. **How did the later term's leader win without the entry?** Raft's election
   restriction exists precisely to stop that, so either a voter granted a vote
   it should have refused, or the voter no longer had the entry to compare
   against.
3. **Did a member forget?** This backend keeps its log in memory, so a restart
   is amnesia. An entry that was on a quorum can stop being on a quorum without
   any message being lost.

So this records the decision-driving inputs as they happen -- commit
provenance, every vote and the logs it was decided on, and every restart -- and
renders the ones bearing on a particular index and term when something breaks.

It is deliberately a ring buffer with a bounded size. A 2000-step run over five
members produces a great deal of this, almost all of it irrelevant to whatever
eventually fails, and an unbounded recorder turns a soak into a memory test.

Cost, because this runs inside the default gate
-----------------------------------------------
Recording is off unless a monitor is given a ``Forensics``. When it is on, the
per-step cost is one small tuple per member plus one per message, with no
formatting: ``render`` is called once, after a failure, on a run that is over.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from nmos.raft.node import Role


@dataclass(frozen=True)
class CommitWitness:
    """Who first claimed an index was committed, and on what evidence.

    ``peers`` is the leader's view of its followers at that instant --
    ``match_index`` per peer -- which is the whole basis on which a leader
    advances its commit index. For a follower it is empty, and the claim came
    from a ``leader_commit`` it was sent instead.
    """

    step: int
    member: int
    role: str
    term: int
    entry_term: int
    commit_index: int
    log_first: int
    log_last: int
    peers: tuple[tuple[int, int], ...]
    """``(peer index, match_index)``, leaders only."""

    def render(self, index: int) -> str:
        where = (
            "  peers: "
            + ", ".join(f"m{p}=match {m}" for p, m in self.peers)
            if self.peers
            else "  (a follower -- it was told this, it did not decide it)"
        )
        return (
            f"  index {index} first seen committed at step {self.step} by "
            f"member {self.member} ({self.role}, term {self.term})\n"
            f"  its commit_index was {self.commit_index}, "
            f"log [{self.log_first}..{self.log_last}], "
            f"entry at {index} was term {self.entry_term}\n"
            f"{where}"
        )


@dataclass(frozen=True)
class VoteRecord:
    """One vote decision, with the logs it was decided on.

    Raft's election restriction is a comparison between the candidate's last
    ``(index, term)`` and the voter's. Recording both sides is what makes a
    wrong grant distinguishable from a right grant by a voter that had already
    lost the entry.
    """

    step: int
    candidate: int
    voter: int
    term: int
    candidate_last_index: int
    candidate_last_term: int
    voter_last_index: int
    voter_last_term: int
    voter_log_first: int
    granted: bool

    def render(self) -> str:
        verdict = "GRANTED" if self.granted else "refused"
        return (
            f"  step {self.step}: m{self.voter} -> m{self.candidate} "
            f"term {self.term} {verdict}\n"
            f"      candidate last=({self.candidate_last_index},"
            f"{self.candidate_last_term})  "
            f"voter last=({self.voter_last_index},{self.voter_last_term}) "
            f"first={self.voter_log_first}"
        )


@dataclass(frozen=True)
class RestartRecord:
    """A member that restarted, and therefore forgot.

    The log lives in memory, so this is the one event that can remove an entry
    from a quorum without any message being lost or any rule being broken.
    """

    step: int
    member: int
    log_last_before: int
    commit_before: int

    def render(self) -> str:
        return (
            f"  step {self.step}: m{self.member} restarted, discarding a log "
            f"through index {self.log_last_before} "
            f"(commit_index was {self.commit_before})"
        )


@dataclass
class Forensics:
    """The recorder. Attach to a ``SafetyMonitor`` and a network to fill it."""

    capacity: int = 4000

    step: int = 0
    """Set by the driver before each step so every record is placed in time."""

    commits: dict[int, CommitWitness] = field(default_factory=dict)
    """log index -> the first member observed claiming it committed.

    "First" is in the monitor's member-iteration order, which is *not* time
    order and usually *not* the member that decided. A follower's commit index
    is something it was told; only a leader's is a decision. So this answers
    "who claimed it" and the next field answers "on what basis"."""

    by_member: dict[tuple[int, int], CommitWitness] = field(default_factory=dict)
    """(member, log index) -> the first time *that member* claimed it committed.

    ``commits`` keeps only the first claimant across the whole cluster, which is
    the right answer for "when did this index become committed" and the wrong one
    for "why does member 1 disagree".

    A State Machine Safety failure names two members, and the interesting
    evidence belongs to the one that is out of step: its commit index, its log
    range, and -- if it was leading when it decided -- the match indices it
    counted. The first real occurrence reported the witness for member 0, which
    was the member that was *right*; nothing explained member 1."""
    deciders: dict[int, CommitWitness] = field(default_factory=dict)
    """log index -> the first member observed claiming it *as leader*.

    Separate from ``commits`` rather than replacing it, because the two say
    different things and a report that showed only one would mislead. If an
    index appears here, its ``peers`` are the match indices the commit was
    actually counted on, which is what decides whether the quorum was real. If
    it appears only in ``commits``, no leader was ever caught holding it
    committed -- which is itself worth seeing."""

    votes: deque[VoteRecord] = field(default_factory=deque)
    restarts: deque[RestartRecord] = field(default_factory=deque)
    leaderships: deque[tuple[int, int, int, int]] = field(default_factory=deque)
    """``(step, member, term, log_last)`` each time a member is first seen
    leading a term."""

    def _trim(self, buffer: deque[Any]) -> None:
        while len(buffer) > self.capacity:
            buffer.popleft()

    # -- recording ------------------------------------------------------

    def note_commit(self, index: int, node: Any, entry_term: int) -> None:
        """Observe that ``index`` is covered by this member's commit index.

        Called for every member on every step, so it is deliberately cheap and
        deliberately records two different things -- see ``commits`` and
        ``deciders``. Neither is overwritten once set: the first claim and the
        first leader-claim are the ones that explain a later contradiction, and
        everything after them is a consequence.
        """
        is_leader = node.role is Role.LEADER
        seen_here = (node.index, index) in self.by_member
        if (
            seen_here
            and index in self.commits
            and (not is_leader or index in self.deciders)
        ):
            return

        peers: tuple[tuple[int, int], ...] = ()
        if is_leader:
            # The whole basis on which a leader advances its commit index. A
            # quorum that was never really there shows up here and nowhere
            # else.
            peers = tuple(
                (peer, state.match_index)
                for peer, state in sorted(node._peers.items())  # noqa: SLF001
            )
        witness = CommitWitness(
            step=self.step,
            member=node.index,
            role=node.role.name,
            term=node.term,
            entry_term=entry_term,
            commit_index=node.commit_index,
            log_first=node.log.first_index,
            log_last=node.log.last_index,
            peers=peers,
        )
        self.commits.setdefault(index, witness)
        self.by_member.setdefault((node.index, index), witness)
        if is_leader:
            self.deciders.setdefault(index, witness)

    def note_leadership(self, node: Any) -> None:
        if any(m == node.index and t == node.term for _, m, t, _ in self.leaderships):
            return
        self.leaderships.append(
            (self.step, node.index, node.term, node.log.last_index),
        )
        self._trim(self.leaderships)

    def note_vote(self, voter: Any, message: Any, granted: bool) -> None:
        self.votes.append(VoteRecord(
            step=self.step,
            candidate=message.candidate,
            voter=voter.index,
            term=message.term,
            candidate_last_index=message.last_log_index,
            candidate_last_term=message.last_log_term,
            voter_last_index=voter.log.last_index,
            voter_last_term=(
                voter.log.term_at(voter.log.last_index)
                if voter.log.last_index >= max(voter.log.first_index, 1)
                else 0
            ),
            voter_log_first=voter.log.first_index,
            granted=granted,
        ))
        self._trim(self.votes)

    def note_restart(self, node: Any) -> None:
        self.restarts.append(RestartRecord(
            step=self.step,
            member=node.index,
            log_last_before=node.log.last_index,
            commit_before=node.commit_index,
        ))
        self._trim(self.restarts)

    # -- reporting ------------------------------------------------------

    def render(self, *, index: int | None = None, term: int | None = None) -> str:
        """The records bearing on one index and the terms around it.

        Filtered rather than dumped. A run produces thousands of votes and the
        handful that decided the disputed terms are the evidence; the rest is
        what made the earlier trace unreadable.
        """
        lines: list[str] = ["Forensics:"]

        if index is not None:
            witness = self.commits.get(index)
            decider = self.deciders.get(index)
            lines.append(
                witness.render(index) if witness
                else f"  index {index}: never observed as committed (!)",
            )
            others = [
                w for (member, at), w in sorted(self.by_member.items())
                if at == index and w is not witness and w is not decider
            ]
            if others:
                # The member that is *out of step* is the one whose evidence
                # explains a disagreement, and it is never the first claimant --
                # that one is, by construction, the member that was right.
                lines.append("  and as each other member saw it:")
                lines.extend(w.render(index) for w in others)
            if decider is None and witness is not None:
                lines.append(
                    "  no leader was ever observed holding this index "
                    "committed, so the quorum behind it cannot be checked",
                )
            elif decider is not None and witness is not None and decider is not witness:
                lines.append("  and, as decided at a leader:")
                lines.append(decider.render(index))

        if term is not None:
            lines.append(f"  -- votes in terms {max(1, term - 2)}..{term} --")
            relevant = [
                v for v in self.votes if max(1, term - 2) <= v.term <= term
            ]
            lines.extend(
                v.render() for v in relevant[-25:]
            ) if relevant else lines.append("  (none recorded)")

            lines.append("  -- leaderships --")
            leads = [
                f"  step {s}: m{m} led term {t} with log through {last}"
                for s, m, t, last in self.leaderships
                if t <= term
            ]
            lines.extend(leads[-10:] if leads else ["  (none recorded)"])

        lines.append("  -- restarts --")
        restart_lines = [r.render() for r in self.restarts]
        lines.extend(restart_lines[-10:] if restart_lines else ["  (none)"])

        return "\n".join(lines)
