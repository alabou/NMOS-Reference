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
"""

from __future__ import annotations


class RaftError(Exception):
    """Base for every failure originating in the consensus layer."""


class RaftUnavailable(RaftError):
    """No progress is possible right now, but nothing is wrong.

    No leader has been elected yet, the leader is unreachable, or fewer than a
    quorum of members are alive. Retryable by definition: the condition is
    about availability, and availability comes back.
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
