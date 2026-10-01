# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The failure vocabulary, split by the decision each failure forces.

Mirrors the framing of ``nmos/etcd/errors.py``: the classes exist so callers
can make three *different* decisions without inspecting a message.

* **Retry, or give up?** ``RaftUnavailable`` is transient -- no leader yet, a
  peer down, quorum briefly lost. Everything else is not.
* **503, or 500?** A registry that cannot commit right now is not a registry
  that is broken. ``RaftUnavailable`` becomes ``MutationUnavailable`` at the
  backend boundary and reaches the Node as a 503 with ``Retry-After``.
* **Resume, or rebuild?** ``RaftLogCompacted`` has its own class precisely so
  the replication loop cannot treat "the entries you need are gone" as a
  transient reconnect. It means a snapshot transfer, and mistaking it for
  anything else leaves a follower permanently behind while looking healthy.

``RaftProtocolError`` is the one that is never retried and never tolerated. A
frame that does not parse is not a network hiccup; it is a peer speaking
something this member does not understand, and the only safe response is to
drop the link rather than guess at the bytes.

``RaftInvariantViolated`` and ``RaftUnexpectedError`` force the last decision,
which is no decision at all: the member stops. One is a state the
implementation proves impossible, the other an exception nothing in it
expected; both mean a defect, and a defect is not served through.
"""

from __future__ import annotations

from enum import Enum
from typing import TypeAlias


class RaftError(Exception):
    """Base for every failure originating in the consensus layer."""


class RaftUnavailable(RaftError):
    """No progress is possible right now, but nothing is wrong.

    No leader has been elected yet, the leader is unreachable, or fewer than a
    quorum of members are alive. Retryable by definition: the condition is
    about availability, and availability comes back.
    """


class RaftCursorReservationFailed(RaftError):
    """A paging-cursor reservation could not be made durable.

    No cursor leaves a member until an upper bound on it is on disk
    (``cursors.py``, "A reservation that outlives the process"), so the
    mutation that asked for one cannot go ahead. Unlike ``RaftUnavailable``
    something *is* wrong -- a disk refused a write -- but the answer to the
    client is the same retryable 503: the Node tries again, here or at another
    member, and nothing was proposed.
    """


class RaftClusterMismatch(RaftError):
    """A peer belongs to a different cluster, or to a different member set.

    Fatal, and deliberately not retried. Two clusters that each believe they
    are the whole thing is the failure the cluster token exists to prevent, so
    a handshake that disagrees about identity ends the connection rather than
    negotiating.
    """


class RaftProtocolError(RaftError):
    """A frame violated the wire format.

    Bad magic, an unsupported major version, a length past the cap, a failed
    checksum, a reserved flag set. Always drops the link: a mis-parsed frame is
    worse than a dropped one, because a mis-decoded commit index would apply
    entries that were never committed.
    """


class RaftLogCompacted(RaftError):
    """The requested index is below the log's first retained entry.

    The follower asking for it is too far behind to be caught up by replication
    and needs a snapshot instead. Distinct from every transient error for the
    reason in the module docstring.
    """


class RaftInvariantViolated(RaftError):
    """A member's own state contradicts something Raft guarantees.

    Raised only for conditions that **cannot** arise from anything a peer
    sends, a disk does, or an operator types: the log and the state machine are
    held in memory and rebuilt from the leader on every restart, and nothing
    applied is persisted. So this means a defect in this implementation, not a
    hostile message or a corrupt file.

    One comes from a peer and is no exception to that: a leader contradicting
    an entry this member committed (``RaftNode._contradicting_committed``).
    Raft makes it impossible -- every leader holds every committed entry -- so
    it too means a defect, one that lost committed data from the cluster.

    That is why it is not recovered from anywhere. ``go.etcd.io/raft`` takes
    the same position and states it more bluntly -- 32 ``Panicf`` sites, no
    ``recover()`` in the library at all -- on the reasoning that continuing
    from a state you have proven impossible can only spread the damage. The
    difference in our favour is the recovery: etcd has to replay a persisted
    WAL, while a member here comes back with nothing and is caught up by the
    leader as a **non-voting** learner, so it cannot even vote until it is
    whole again.

    Distinct from every other error in this module because it must not be
    swallowed, and nothing in the member swallows it: an invariant that is
    broken stays broken, and a loop that logged it once per wake-up would be a
    silent failure wearing the costume of a handled one. The member's loops
    used to catch ``Exception`` around it so that one bad apply could not kill
    a member; they no longer do -- an exception nothing expected is a defect
    too (``RaftUnexpectedError``), and a defect is not something to serve
    through.

    **What happens instead is a stop** (``RaftNode._fail``): the member stops
    leading, campaigning and applying at once, answers every waiting caller
    "unavailable", closes its transport, and signals its owner
    (``RaftNode.wait_for_failure``), which ends the process with status 1
    (``nmos_registry.py``). The restart that brings it back -- automatic under
    a service manager that restarts on failure -- is the recovery described
    above. Merely re-raising it did none of that: it ended the task that found
    it and nothing else, and the member served on from a store that no longer
    moved.
    """


class MemberTask(Enum):
    """The parts of a member a defect can surface in, named by the stop.

    One value per place that catches an exception nothing expected and stops
    the member for it (``RaftUnexpectedError``): the two loops a member runs
    for itself, and the three places the transport delivers to its handlers.
    """

    TICK = "tick"
    APPLY = "apply"
    OUTBOUND_LINK = "outbound link"
    INBOUND_CONNECTION = "inbound connection"
    APPLICATION_REQUEST = "application request"


class RaftUnexpectedError(RaftError):
    """An exception nothing in the consensus layer expected: a defect.

    The same position as ``RaftInvariantViolated``, reached from the other
    direction. An invariant check finds a state the implementation proves
    impossible; this is raised where an exception arrives that the code around
    it never anticipated -- in the tick, in apply, or in a handler the
    transport delivered to. Either way the member is in a state its own logic
    did not foresee, and continuing from it can only spread the damage, so the
    member stops (``RaftNode._fail``) and the process exits with status 1 for
    a service manager to restart. The Rust registry does the same thing to a
    panic: it aborts the process (``nmos-registry-bin``'s panic policy).

    What this is **not** for: the failures the code does expect and handles
    where they happen -- a link that drops, a peer that disagrees about the
    cluster, a frame that does not parse, a term file that cannot be saved.
    Those keep their own classes and their own recovery.

    The cause is chained as ``__cause__``, so the log line that announces the
    stop and the exit that follows carry the original traceback.
    """

    def __init__(self, task: MemberTask, cause: BaseException) -> None:
        super().__init__(f"unexpected error in {task.value}: {cause!r}")
        self.task = task
        self.cause = cause
        self.__cause__ = cause


MemberFailure: TypeAlias = RaftInvariantViolated | RaftUnexpectedError
"""What a member stops on, and what its owner is handed (``wait_for_failure``)."""


class RaftNotLeader(RaftError):
    """This member cannot append; someone else is the leader.

    Carries the leader's index when one is known, so the caller can forward
    rather than wait out an election that has already finished.
    """

    def __init__(self, message: str, *, leader: int | None = None) -> None:
        super().__init__(message)
        self.leader = leader


class RaftNotOwner(RaftError):
    """This member does not own the Node subtree the mutation targets.

    Carries the owner's index for the same reason ``RaftNotLeader`` carries the
    leader's: the answer to "who should have this?" is already known here, and
    making the caller rediscover it costs a round trip.
    """

    def __init__(self, message: str, *, owner: int | None = None) -> None:
        super().__init__(message)
        self.owner = owner
