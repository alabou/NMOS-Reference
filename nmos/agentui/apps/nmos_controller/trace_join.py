# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Joining journal steps to the node's own debug trace.

No instrumentation is added anywhere for this. Everything needed already exists in
the Controller when it runs with ``--debug-in-depth``:

* the server stamps ``data-debug="1"`` on the document,
* its own script posts every click, submit, and change to a client-event endpoint
  carrying a trace id,
* and it stamps ``X-Trace-Id`` on each request it makes.

The driver observes those ids *passively*, off the page's own traffic, and this
module reads the node's rotating log and selects the records sharing them. One
journal step then shows the whole causal chain: the click, the request the page
made, the Controller's handling, its outbound IS-05/IS-11 call, the status, and
what the pixels said.

The log path is **derived** from the node's command line rather than fetched from
the Controller's diagnostic snapshot endpoint. That endpoint is part of the JSON
API, and reading it is not something the interface offers an operator. If the
derivation ever drifts from the node's, the tail simply finds nothing and the step
records correlation as unavailable — degraded, never wrong.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from ...enums import CorrelationKind

#: Record kinds worth showing beside a step. Everything else in the log is
#: internal churn that would bury the causal chain rather than explain it.
INTERESTING_KINDS = (
    "client.ui",
    "request_in",
    "request_out",
    "request_error",
    "call_out",
    "call_in",
    "reservation",
)


class TraceJoiner:
    """Reads a node's debug log and slices it by trace id."""

    def __init__(self, log_path: str | None) -> None:
        self._path = Path(log_path) if log_path else None
        self._inode: int | None = None
        self._rotated = False
        if self._path is not None and self._path.is_file():
            self._inode = self._path.stat().st_ino

    @property
    def available(self) -> bool:
        """Whether there is a readable log to join against."""
        return self._path is not None and self._path.is_file()

    @property
    def rotated(self) -> bool:
        """Whether the log rotated mid-run.

        The node writes to a rotating handler, so a long run can lose its tail.
        Detecting it by inode change means a gap is reported rather than silently
        producing a step with no correlation for no apparent reason.
        """
        return self._rotated

    def _records(self) -> list[Mapping[str, object]]:
        """Parse the log, skipping anything unreadable.

        Malformed lines are skipped rather than fatal: this is diagnostic
        enrichment, and one truncated line — likely the one being written right
        now — must not cost the run.
        """
        if self._path is None or not self._path.is_file():
            return []

        current = self._path.stat().st_ino
        if self._inode is not None and current != self._inode:
            self._rotated = True
            self._inode = current

        found: list[Mapping[str, object]] = []
        try:
            with self._path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(parsed, dict):
                        found.append(parsed)
        except OSError:
            return []
        return found

    def slice_for(
        self,
        trace_ids: Sequence[str],
        *,
        limit: int = 40,
    ) -> tuple[tuple[Mapping[str, object], ...], CorrelationKind]:
        """Return the records sharing any of ``trace_ids``.

        Correlation is reported as:

        ``DISABLED``
            the node is not tracing, so no ids exist to join on;
        ``UNAVAILABLE``
            tracing is on but the log could not be read, or the step produced no
            ids at all;
        ``FULL``
            records were found and matched.

        Matching is on the exact id only, never on a time window. A window join
        looks plausible and quietly attributes unrelated activity to a step.
        """
        if self._path is None:
            return (), CorrelationKind.DISABLED
        if not trace_ids:
            return (), CorrelationKind.UNAVAILABLE

        wanted = {t for t in trace_ids if t}
        matched: list[Mapping[str, object]] = []
        for record in self._records():
            if str(record.get("trace_id", "")) not in wanted:
                continue
            if not _is_interesting(str(record.get("kind", ""))):
                continue
            matched.append(record)

        if not matched:
            return (), CorrelationKind.UNAVAILABLE
        return tuple(matched[:limit]), CorrelationKind.FULL


def _is_interesting(kind: str) -> bool:
    """Whether a record kind belongs in a step's correlated trace.

    Client-side UI events are emitted as ``client.<something>``, so the whole
    family is accepted rather than enumerating each variant the page might send.
    """
    if kind.startswith("client."):
        return True
    return kind in INTERESTING_KINDS
