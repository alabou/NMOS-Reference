# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for joining journal steps to the node's debug trace.

The distinction these cover is the one a reader of a run's manifest cares about:
an empty causal chain because the node traced nothing, versus an empty chain
because the log was never where this run looked. The log path is derived from the
node's command line through an environment-dependent temporary directory, so the
second case is reachable in practice and must stay tellable from the first.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..apps.nmos_controller.trace_join import TraceJoiner
from ..enums import CorrelationKind

TRACE_ID = "abc123"


def _log(path: Path, *records: dict[str, object]) -> Path:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


class TestAvailability:
    """``available`` is what the manifest's ``debug_log_present`` reports."""

    def test_no_path_is_unavailable(self) -> None:
        assert not TraceJoiner(None).available

    def test_a_path_that_does_not_exist_is_unavailable(
        self, tmp_path: Path
    ) -> None:
        # The temp-dir mismatch case: tracing is on and a path was derived, but
        # the node is writing somewhere else entirely.
        joiner = TraceJoiner(str(tmp_path / "nmos-controller-127.0.0.1-5050.log"))
        assert not joiner.available

    def test_an_existing_log_is_available(self, tmp_path: Path) -> None:
        path = _log(tmp_path / "trace.log", {"trace_id": TRACE_ID, "kind": "request_in"})
        assert TraceJoiner(str(path)).available

    def test_a_directory_is_not_a_readable_log(self, tmp_path: Path) -> None:
        directory = tmp_path / "not-a-file"
        directory.mkdir()
        assert not TraceJoiner(str(directory)).available


class TestSliceFor:
    """Correlation verdicts."""

    def test_tracing_off_reports_disabled(self) -> None:
        records, kind = TraceJoiner(None).slice_for([TRACE_ID])
        assert (records, kind) == ((), CorrelationKind.DISABLED)

    def test_missing_log_reports_unavailable_not_disabled(
        self, tmp_path: Path
    ) -> None:
        # Distinguishing these two is the whole point: DISABLED is a choice the
        # operator made, UNAVAILABLE is something that went wrong.
        joiner = TraceJoiner(str(tmp_path / "absent.log"))
        records, kind = joiner.slice_for([TRACE_ID])
        assert (records, kind) == ((), CorrelationKind.UNAVAILABLE)

    def test_a_step_with_no_ids_is_unavailable(self, tmp_path: Path) -> None:
        path = _log(tmp_path / "trace.log", {"trace_id": TRACE_ID, "kind": "request_in"})
        records, kind = TraceJoiner(str(path)).slice_for([])
        assert (records, kind) == ((), CorrelationKind.UNAVAILABLE)

    def test_matching_ids_report_full(self, tmp_path: Path) -> None:
        path = _log(
            tmp_path / "trace.log",
            {"trace_id": TRACE_ID, "kind": "request_in", "seq": 1},
            {"trace_id": "other", "kind": "request_in", "seq": 2},
        )
        records, kind = TraceJoiner(str(path)).slice_for([TRACE_ID])
        assert kind is CorrelationKind.FULL
        assert [r.get("seq") for r in records] == [1]

    def test_client_events_are_interesting(self, tmp_path: Path) -> None:
        path = _log(
            tmp_path / "trace.log",
            {"trace_id": TRACE_ID, "kind": "client.ui.click"},
        )
        _records, kind = TraceJoiner(str(path)).slice_for([TRACE_ID])
        assert kind is CorrelationKind.FULL

    def test_uninteresting_kinds_do_not_count_as_correlation(
        self, tmp_path: Path
    ) -> None:
        path = _log(
            tmp_path / "trace.log",
            {"trace_id": TRACE_ID, "kind": "session_start"},
        )
        records, kind = TraceJoiner(str(path)).slice_for([TRACE_ID])
        assert (records, kind) == ((), CorrelationKind.UNAVAILABLE)

    def test_malformed_lines_are_skipped_not_fatal(self, tmp_path: Path) -> None:
        # The last line is likely the one being written right now.
        path = tmp_path / "trace.log"
        path.write_text(
            json.dumps({"trace_id": TRACE_ID, "kind": "request_in"}) + "\n"
            + '{"trace_id": "abc123", "kind": "req',
            encoding="utf-8",
        )
        _records, kind = TraceJoiner(str(path)).slice_for([TRACE_ID])
        assert kind is CorrelationKind.FULL

    def test_limit_caps_the_returned_records(self, tmp_path: Path) -> None:
        path = _log(
            tmp_path / "trace.log",
            *({"trace_id": TRACE_ID, "kind": "request_in", "seq": n}
              for n in range(10)),
        )
        records, kind = TraceJoiner(str(path)).slice_for([TRACE_ID], limit=3)
        assert kind is CorrelationKind.FULL
        assert len(records) == 3


class TestRotation:
    """A rotated log means a real gap, so it must be reported rather than hidden."""

    def test_no_rotation_reported_for_a_stable_log(self, tmp_path: Path) -> None:
        path = _log(tmp_path / "trace.log", {"trace_id": TRACE_ID, "kind": "request_in"})
        joiner = TraceJoiner(str(path))
        joiner.slice_for([TRACE_ID])
        assert not joiner.rotated

    def test_inode_change_is_reported_as_rotation(self, tmp_path: Path) -> None:
        path = tmp_path / "trace.log"
        _log(path, {"trace_id": TRACE_ID, "kind": "request_in"})
        joiner = TraceJoiner(str(path))
        assert not joiner.rotated

        # A new inode at the same name is what the node's RotatingFileHandler
        # leaves behind. Built as a separate file and moved into place because
        # unlink-then-recreate lets the filesystem hand back the same inode, and
        # the test would then pass or fail on allocator behaviour rather than on
        # the rotation logic.
        replacement = _log(
            tmp_path / "rotated.log",
            {"trace_id": TRACE_ID, "kind": "request_in", "seq": 99},
        )
        os.replace(replacement, path)
        assert path.stat().st_ino != joiner._inode

        joiner.slice_for([TRACE_ID])
        assert joiner.rotated
