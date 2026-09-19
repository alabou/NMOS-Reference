# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The only thing in this backend that reaches the disk: about 24 bytes.

Why any disk at all
-------------------
The premise of this package is that the replicated log lives in memory and is
never fsynced, because IS-04 state regenerates from Node re-registration within
the garbage-collection interval. That premise is sound for the *log*. It is not
sound for the *vote*, and the difference is worth stating exactly, because
"in-memory Raft" sounds like it should mean no disk at all.

Raft's election safety argument requires ``currentTerm`` and ``votedFor`` to
survive a crash. Consider three members A, B and C:

    A is leader in term 5 and replicates entry E to B. Quorum {A, B} commits
    it, ``apply_committed`` runs, and the client is told 201. B then crashes
    and restarts. C -- which never received E -- times out and campaigns in
    term 6 with ``lastLogIndex`` behind A's.

If B comes back with no memory of having voted in term 5, it votes for C. C
wins with {B, C}, and C's log does not contain E. **An acknowledged
registration is lost after a single, non-simultaneous failure** -- and a
rolling restart of a three-member cluster, which is how this design resizes and
upgrades, is exactly that scenario three times over.

Persisting the vote is what closes it, and it is cheap in a way the log is not:
the term changes on elections, which are rare, whereas the log changes on every
registration. The expensive fsync goes; this one stays.

The second half of the fix lives elsewhere
------------------------------------------
Persisting the vote alone is necessary but not sufficient, because a restarted
member also comes back with an *empty log*, and Raft's up-to-dateness check
makes an empty log vote for anybody. ``incarnation`` is how the rest of the
system notices: it increments on every start, travels in the handshake, and
tells a leader that this peer has been reset and must be caught up and
explicitly promoted before its vote or its acknowledgement counts. See
``node.py`` for the non-voting rejoin that uses it.

Why the write is synchronous
----------------------------
``save`` blocks the event loop. That is deliberate and it is the one place this
package knowingly does so. It sits on the election path, it is a few bytes to
the page cache plus one fsync, and moving it to a thread would let the loop run
between "I decided to vote" and "that vote is durable" -- which is precisely
the window the whole mechanism exists to close.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from nmos.raft.errors import RaftError

# Bumped only if the file's shape changes. A member that finds a version it
# does not understand refuses to start rather than guessing, because guessing
# here means guessing about whether it has already voted.
STATE_VERSION = 1


class PersistentStateError(RaftError):
    """The persisted term/vote file is unreadable or not ours.

    Always fatal at startup. Continuing would mean starting with no memory of
    a vote that may well have been cast, which is the exact failure this file
    exists to prevent.
    """


@dataclass(frozen=True)
class PersistentState:
    """What must survive a crash for elections to stay safe."""

    term: int
    voted_for: int | None
    incarnation: int


class TermStore:
    """Reads and writes the term/vote file, atomically.

    Args:
        path: The file itself. Its parent directory must already exist; this
            class does not create directories, so a typo in a deployment path
            fails loudly instead of quietly persisting state somewhere nobody
            will look for it.
    """

    __slots__ = ("_path", "_writes")

    def __init__(self, path: Path) -> None:
        self._path = path
        self._writes = 0

    @property
    def path(self) -> Path:
        return self._path

    @property
    def writes(self) -> int:
        """How many times state has been flushed since construction.

        Exposed for the benchmark and for tests: the claim "this design fsyncs
        on term changes, not on writes" is checkable, and a regression that
        started persisting per mutation would otherwise show up only as an
        unexplained loss of throughput.
        """
        return self._writes

    def load(self) -> PersistentState:
        """Read the stored state and bump the incarnation.

        A fresh member -- no file, or an empty directory -- starts at term 0
        with no vote and incarnation 1. Bumping on *load* rather than on first
        save is what makes the counter mean "how many times this member has
        started", which is what a leader needs in order to notice a peer that
        has been reset.
        """
        if not self._path.exists():
            state = PersistentState(term=0, voted_for=None, incarnation=1)
            self.save(state)
            return state

        try:
            raw = json.loads(self._path.read_text())
        except (OSError, ValueError) as exc:
            raise PersistentStateError(
                f"{self._path} is unreadable: {exc}. Refusing to start with no "
                f"memory of whether this member has already voted; delete it "
                f"only if this member is genuinely new to the cluster.",
            ) from exc

        if not isinstance(raw, dict):
            raise PersistentStateError(
                f"{self._path} holds a JSON {type(raw).__name__}, not an "
                f"object. Refusing to start with no memory of whether this "
                f"member has already voted.",
            )

        version = raw.get("version")
        if version != STATE_VERSION:
            raise PersistentStateError(
                f"{self._path} has state version {version!r}, this member "
                f"understands {STATE_VERSION}",
            )

        # The three values, read inside the same refusal as an unparseable
        # file. A document that is valid JSON but has no ``term`` is no more
        # usable than one that is not JSON at all, and until this ``try`` was
        # here the ``KeyError`` escaped from *outside* the one above -- so the
        # member died with a traceback rather than the refusal, at the one
        # moment an operator most needs to be told what to do about it.
        try:
            term = int(raw["term"])
            stored_vote = raw["voted_for"]
            voted_for = None if stored_vote is None else int(stored_vote)
            incarnation = int(raw["incarnation"]) + 1
        except (KeyError, TypeError, ValueError) as exc:
            raise PersistentStateError(
                f"{self._path} does not hold a usable term and vote: {exc!r}. "
                f"Refusing to start with no memory of whether this member has "
                f"already voted; delete it only if this member is genuinely "
                f"new to the cluster.",
            ) from exc

        state = PersistentState(
            term=term, voted_for=voted_for, incarnation=incarnation,
        )
        self.save(state)
        return state

    def save(self, state: PersistentState) -> None:
        """Write and fsync, atomically. Blocking, for the reason above.

        Written to a temporary file in the same directory and renamed over the
        target: ``os.replace`` is atomic within a filesystem, so a crash midway
        leaves either the old state or the new one, never a half-written file
        that parses as term 0.

        The directory is fsynced as well as the file. Without that, the rename
        itself can be lost on a crash even though the data was flushed -- and
        the member would come back with the *previous* term, which is the state
        this is meant to rule out.
        """
        payload = json.dumps(
            {
                "version": STATE_VERSION,
                "term": state.term,
                "voted_for": state.voted_for,
                "incarnation": state.incarnation,
            },
            indent=2,
        )

        directory = self._path.parent
        handle, temporary = tempfile.mkstemp(
            dir=str(directory), prefix=".raft-state-",
        )
        try:
            with os.fdopen(handle, "w") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        except BaseException:
            # Best-effort cleanup; the original file is untouched either way,
            # because the rename is the only thing that publishes the new one.
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

        directory_fd = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

        self._writes += 1
