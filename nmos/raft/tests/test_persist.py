# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The term/vote file: the one piece of durability this backend keeps.

These pin three separable claims:

* the state survives a restart, which is what makes election safety hold;
* the incarnation counts restarts, which is what lets a leader notice a member
  that came back with an empty log;
* the write is atomic, so a crash mid-save cannot produce a file that parses as
  term 0 -- which would silently undo the first claim.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from nmos.raft import persist
from nmos.raft.persist import (
    STATE_VERSION,
    PersistentState,
    PersistentStateError,
    TermStore,
)


class TestFreshMember:
    def test_a_missing_file_starts_at_term_zero(self, tmp_path: Path) -> None:
        store = TermStore(tmp_path / "state.json")
        state = store.load()
        assert state == PersistentState(
            term=0, voted_for=None, incarnation=1,
        )

    def test_loading_creates_the_file(self, tmp_path: Path) -> None:
        """So a member that never votes still records that it existed."""
        path = tmp_path / "state.json"
        TermStore(path).load()
        assert path.is_file()
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["version"] == STATE_VERSION


class TestSurvivingARestart:
    def test_the_term_and_vote_come_back(self, tmp_path: Path) -> None:
        """The whole reason this file exists.

        Without it, a restarted member votes a second time in a term it has
        already voted in, two candidates each collect a majority, and an
        acknowledged registration is lost.
        """
        path = tmp_path / "state.json"
        first = TermStore(path)
        first.load()
        first.save(PersistentState(term=5, voted_for=2, incarnation=1))

        reloaded = TermStore(path).load()
        assert reloaded.term == 5
        assert reloaded.voted_for == 2

    def test_a_vote_for_nobody_round_trips_as_none(self, tmp_path: Path) -> None:
        """``None`` and "voted for member 0" must not be confused.

        Member indices start at 0, so a falsy check here would read "voted for
        the first member" as "has not voted" -- and that member could then be
        voted for twice in one term.
        """
        path = tmp_path / "state.json"
        store = TermStore(path)
        store.load()
        store.save(PersistentState(term=3, voted_for=0, incarnation=1))
        assert TermStore(path).load().voted_for == 0

        store.save(PersistentState(term=4, voted_for=None, incarnation=1))
        assert TermStore(path).load().voted_for is None


class TestIncarnation:
    def test_it_increments_on_every_load(self, tmp_path: Path) -> None:
        """It counts starts, which is what "has this member been reset?" means."""
        path = tmp_path / "state.json"
        assert TermStore(path).load().incarnation == 1
        assert TermStore(path).load().incarnation == 2
        assert TermStore(path).load().incarnation == 3

    def test_it_survives_independently_of_the_term(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        store = TermStore(path)
        store.load()
        store.save(PersistentState(term=9, voted_for=1, incarnation=1))

        reloaded = TermStore(path).load()
        assert reloaded.term == 9
        assert reloaded.incarnation == 2


class TestAtomicity:
    def test_a_save_leaves_no_temporary_files(self, tmp_path: Path) -> None:
        store = TermStore(tmp_path / "state.json")
        store.load()
        store.save(PersistentState(term=2, voted_for=1, incarnation=1))
        assert [p.name for p in tmp_path.iterdir()] == ["state.json"]

    def test_an_unreadable_file_refuses_to_start(self, tmp_path: Path) -> None:
        """Refusing beats guessing: guessing here means guessing about a vote."""
        path = tmp_path / "state.json"
        path.write_text("{ this is not json", encoding="utf-8")
        with pytest.raises(PersistentStateError, match="unreadable"):
            TermStore(path).load()

    def test_an_unknown_state_version_refuses_to_start(
        self, tmp_path: Path,
    ) -> None:
        path = tmp_path / "state.json"
        path.write_text(
            json.dumps({
                "version": STATE_VERSION + 1,
                "term": 1, "voted_for": None, "incarnation": 1,
            }),
            encoding="utf-8",
        )
        with pytest.raises(PersistentStateError, match="state version"):
            TermStore(path).load()

    @pytest.mark.parametrize(
        "document",
        [
            pytest.param({"version": STATE_VERSION}, id="nothing but a version"),
            pytest.param(
                {"version": STATE_VERSION, "voted_for": None, "incarnation": 1},
                id="no term",
            ),
            pytest.param(
                {"version": STATE_VERSION, "term": 1, "incarnation": 1},
                id="no vote",
            ),
            pytest.param(
                {"version": STATE_VERSION, "term": 1, "voted_for": None},
                id="no incarnation",
            ),
            pytest.param(
                {
                    "version": STATE_VERSION, "term": "not a number",
                    "voted_for": None, "incarnation": 1,
                },
                id="an unparseable term",
            ),
            pytest.param(
                {
                    "version": STATE_VERSION, "term": 1,
                    "voted_for": ["a", "list"], "incarnation": 1,
                },
                id="a vote that is not a member index",
            ),
        ],
    )
    def test_a_structurally_wrong_file_refuses_the_same_way(
        self, tmp_path: Path, document: dict[str, object],
    ) -> None:
        """Valid JSON is not the same as a usable file, and both must refuse.

        These used to escape the ``try`` around ``json.loads`` and reach the
        caller as a bare ``KeyError`` or ``ValueError``, so the member died
        with a traceback instead of being told what the file was and what to
        do about it -- at the one moment an operator most needs to be told.
        """
        path = tmp_path / "state.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(PersistentStateError, match="already voted"):
            TermStore(path).load()

    @pytest.mark.parametrize(
        "text", ["[]", '"a string"', "42", "null"],
    )
    def test_a_json_document_that_is_not_an_object_refuses_to_start(
        self, tmp_path: Path, text: str,
    ) -> None:
        """``raw.get`` would raise ``AttributeError`` on all of these."""
        path = tmp_path / "state.json"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(PersistentStateError, match="not an object"):
            TermStore(path).load()


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 only")
class TestWindowsDurability:
    """The entry-level platform's stand-in for the directory fsync.

    Linux is the deployment target and takes ``os.replace`` plus that fsync;
    these pin that the Windows substitute keeps *both* halves. Atomicity alone
    is the half that is easy to get by accident, and it is not the half that
    prevents a term change from being lost in a crash.
    """

    def test_the_move_is_replace_existing_and_write_through(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[tuple[str, str, int]] = []

        def move_file_ex(source: str, target: str, flags: int) -> int:
            calls.append((source, target, flags))
            return 1

        monkeypatch.setattr(persist, "_move_file_ex", move_file_ex)

        persist._replace_durably("temporary", Path("state.json"))

        # MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH, spelled as the
        # literals ``winbase.h`` defines rather than as the enum under test,
        # so a wrong value in that enum fails here instead of agreeing with
        # itself.
        assert calls == [("temporary", "state.json", 0x1 | 0x8)]

    def test_a_failed_move_raises_rather_than_reporting_success(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A swallowed failure here is a vote that never reached the disk.

        ``os.replace`` raises on the other platform, so this one must too --
        otherwise ``save`` returns, the member votes, and the file still holds
        the previous term.
        """
        def move_file_ex(_source: str, _target: str, _flags: int) -> int:
            return 0

        monkeypatch.setattr(persist, "_move_file_ex", move_file_ex)

        with pytest.raises(OSError):
            persist._replace_durably("temporary", Path("state.json"))

    def test_a_real_save_replaces_an_existing_file(self, tmp_path: Path) -> None:
        """The same claim as ``TestAtomicity``, through the real Win32 call.

        The two tests above stub the API out; without this one nothing would
        notice that the real prototype had been declared wrongly.
        """
        path = tmp_path / "state.json"
        store = TermStore(path)
        store.save(PersistentState(term=1, voted_for=None, incarnation=1))
        store.save(PersistentState(term=9, voted_for=2, incarnation=3))

        assert TermStore(path).load() == PersistentState(
            term=9, voted_for=2, incarnation=4,
        )
        assert list(tmp_path.iterdir()) == [path]


class TestWriteAccounting:
    def test_writes_are_counted(self, tmp_path: Path) -> None:
        """The claim "this fsyncs on term changes, not on writes" is checkable.

        A regression that started persisting per mutation would otherwise show
        up only as throughput quietly collapsing, with nothing to point at.
        """
        store = TermStore(tmp_path / "state.json")
        store.load()                      # creates the file: one write
        assert store.writes == 1

        for term in range(2, 6):
            store.save(PersistentState(term=term, voted_for=None, incarnation=1))
        assert store.writes == 5
