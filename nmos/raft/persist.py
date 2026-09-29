# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The only thing in this backend that reaches the disk: well under 100 bytes.

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

The cursor reservation
----------------------
The third thing a restart must not forget is which paging cursors this member
has already handed out. A cursor is unique across members by construction, but
not across two runs of one member, and once the log's cursors are ahead of the
member's clock a new run re-mints its predecessor's cursors exactly -- see
``cursors.py`` for the mechanism and the measurement. So the file also carries
``cursor_reservation``: a bound at or above every cursor this member has handed
out, written before any cursor above the previous bound leaves the member.

Adding it did not change ``STATE_VERSION``, deliberately. The version exists so
that a member never guesses about a *vote*, and the new key changes nothing
about the vote: both implementations have always read the three vote fields by
name and ignored any other key, so a member built before the key existed still
reads its vote correctly from a file that has it. Bumping the version would buy
no safety and would turn every downgrade into a member that refuses to start. A
file without the key -- written before it existed -- resumes no reservation,
which is exactly what that file's writer did.

It costs one extra write per ``RESERVATION_WINDOW_SECONDS`` of cursor progress
(``cursors.py``), on the same path and with the same durability as the vote.

Why the write is synchronous
----------------------------
``save`` blocks the event loop. That is deliberate and it is the one place this
package knowingly does so. It sits on the election path, it is a few bytes to
the page cache plus one fsync, and moving it to a thread would let the loop run
between "I decided to vote" and "that vote is durable" -- which is precisely
the window the whole mechanism exists to close. The reservation has the same
window, between "this cursor is handed out" and "its bound is durable".
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from nmos.raft.errors import RaftError
from nmos.registry.types import TaiCursor

# Bumped only if the file's shape changes in a way a reader of the vote would
# misread. A member that finds a version it does not understand refuses to
# start rather than guessing, because guessing here means guessing about
# whether it has already voted. (``cursor_reservation`` is additive and did not
# bump it; see "The cursor reservation" above.)
STATE_VERSION = 1


if sys.platform == "win32":
    # Everything in this block exists for the entry-level Windows rig. The
    # deployment target is Linux and takes the plain rename-then-fsync-the-
    # directory sequence in ``save`` below, unchanged and unconditional; the
    # imports here are inside the guard because ``ctypes.wintypes`` does not
    # merely go unused elsewhere, it raises ``ValueError: _type_ 'v' not
    # supported`` at import time on a non-Windows build of ``_ctypes``.
    #
    # Windows cannot open a directory through ``os.open``, so the POSIX
    # sequence has no expression there at all. ``MoveFileExW`` is the one call
    # that covers both halves: ``REPLACE_EXISTING`` is the flag ``os.replace``
    # itself passes on this platform, and ``WRITE_THROUGH`` is the part that
    # matters here -- it does not return until the move has reached the disk,
    # which is what fsyncing the directory buys on POSIX.
    #
    # The prototype is bound once, at import, rather than per ``save``: the
    # call sits on the election path, and re-loading kernel32 on every term
    # change would be work done inside the window this whole file exists to
    # keep short.
    import ctypes
    from ctypes import wintypes
    from enum import IntFlag

    class _MoveFileFlag(IntFlag):
        """The ``MoveFileExW`` flags this needs, from ``winbase.h``."""

        REPLACE_EXISTING = 0x1
        WRITE_THROUGH = 0x8

    _MOVE_FILE_FLAGS = int(
        _MoveFileFlag.REPLACE_EXISTING | _MoveFileFlag.WRITE_THROUGH,
    )

    _move_file_ex = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
    _move_file_ex.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
    )
    _move_file_ex.restype = wintypes.BOOL

    def _replace_durably(source: str, target: Path) -> None:
        """``os.replace``, plus the durability POSIX gets from the fsync.

        Raises ``OSError`` on failure, exactly as ``os.replace`` does, so the
        caller's cleanup path does not have to know which platform it is on.
        """
        if not _move_file_ex(source, str(target), _MOVE_FILE_FLAGS):
            raise ctypes.WinError(ctypes.get_last_error())


class PersistentStateError(RaftError):
    """The persisted term/vote file is unreadable or not ours.

    Always fatal at startup. Continuing would mean starting with no memory of
    a vote that may well have been cast, which is the exact failure this file
    exists to prevent.
    """


@dataclass(frozen=True)
class PersistentState:
    """What must survive a crash for elections and paging to stay safe.

    ``cursor_reservation`` has no default on purpose. Every save writes the
    whole file, so a construction site that forgot it would silently erase the
    reservation the previous save recorded -- and the next incarnation would
    resume below cursors already handed out.
    """

    term: int
    voted_for: int | None
    incarnation: int
    cursor_reservation: TaiCursor | None


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
            state = PersistentState(
                term=0, voted_for=None, incarnation=1, cursor_reservation=None,
            )
            self.save(state)
            return state

        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
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
            cursor_reservation=self._cursor_reservation(raw),
        )
        self.save(state)
        return state

    def _cursor_reservation(self, raw: dict[str, object]) -> TaiCursor | None:
        """The stored reservation: absent or ``null`` is none, else a cursor.

        Anything else refuses, as an unusable vote does. Resuming *no*
        reservation instead would be a guess in the one direction that is not
        safe: this member could then re-mint cursors it has already handed out.
        """
        stored = raw.get("cursor_reservation")
        if stored is None:
            return None
        reservation = (
            TaiCursor.parse(stored) if isinstance(stored, str) else None
        )
        if reservation is None:
            raise PersistentStateError(
                f"{self._path} holds a cursor reservation that is not a "
                f"cursor: {stored!r}. Refusing to start without knowing which "
                f"paging cursors this member has already handed out; removing "
                f"the key starts it without that knowledge, and a cursor it "
                f"hands out may then repeat one it handed out before.",
            )
        return reservation

    def save(self, state: PersistentState) -> None:
        """Write and fsync, atomically. Blocking, for the reason above.

        Written to a temporary file in the same directory and renamed over the
        target: ``os.replace`` is atomic within a filesystem, so a crash midway
        leaves either the old state or the new one, never a half-written file
        that parses as term 0.

        The directory is fsynced as well as the file. Without that, the rename
        itself can be lost on a crash even though the data was flushed -- and
        the member would come back with the *previous* term, which is the state
        this is meant to rule out. Windows cannot fsync a directory and gets
        the same guarantee from a write-through move instead; see the platform
        block at the top of this module.

        ``encoding`` and ``newline`` are pinned so the file is the same bytes
        on every platform. Nothing here is non-ASCII today -- ``json.dumps``
        escapes it -- but a state file whose contents depend on the locale of
        whichever machine last wrote it is not a thing to leave to chance in
        the one file this backend cannot afford to misread.
        """
        # ``cursor_reservation`` is written even when there is none, as
        # ``null``, the way ``voted_for`` is: every file this version writes has
        # the same five keys in the same order, whichever implementation wrote
        # it. (A missing key still loads -- as no reservation -- because files
        # written before the key existed have none.)
        payload = json.dumps(
            {
                "version": STATE_VERSION,
                "term": state.term,
                "voted_for": state.voted_for,
                "incarnation": state.incarnation,
                "cursor_reservation": (
                    None if state.cursor_reservation is None
                    else str(state.cursor_reservation)
                ),
            },
            indent=2,
        )

        directory = self._path.parent
        handle, temporary = tempfile.mkstemp(
            dir=str(directory), prefix=".raft-state-",
        )
        try:
            with os.fdopen(
                handle, "w", encoding="utf-8", newline="\n",
            ) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if sys.platform == "win32":
                _replace_durably(temporary, self._path)
            else:
                os.replace(temporary, self._path)
        except BaseException:
            # Best-effort cleanup; the original file is untouched either way,
            # because the rename is the only thing that publishes the new one.
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

        # The second half of the durable publish. Windows already got it from
        # the write-through move above and cannot do this at all, so it is the
        # one step that is genuinely POSIX-only.
        if sys.platform != "win32":
            directory_fd = os.open(str(directory), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)

        self._writes += 1
