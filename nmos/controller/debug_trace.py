# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Deep-debug tracing for the embedded NMOS controller.

Enabled by ``--debug-in-depth`` on the Node command line. Writes a
single chronological JSONL log capturing every:

  * incoming controller request (method, path, admin session prefix,
    trace id);
  * outbound HTTP call to a remote Node (method, url, trace id,
    body preview, response status);
  * reservation-session lifecycle event (acquire / renew / keepalive /
    release, success or failure);
  * client-side browser event POSTed via ``/api/debug/client-event``.

One log per controller instance, named
``/tmp/nmos-controller-{addr}-{controlPort}.log``. Multiple
controllers running on the same box get separate files.

When ``--debug-in-depth`` is OFF the module is a no-op: callers can
still import and call into it, but every method is a guard-and-return.
This keeps instrumentation sites in production code tiny and
production paths unaffected.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Final


# Correlation / trace id length (hex chars). 12 = 48 bits of entropy,
# plenty for per-request distinctness over a debugging session.
_TRACE_ID_LEN: Final[int] = 12


class DebugTrace:
    """Per-controller tracer.

    One instance lives on ``app["controller_debug_trace"]``. When
    ``enabled`` is False every method is effectively a no-op; the
    controller still carries the object so call sites can blindly
    dispatch without an ``if self._debug:`` dance.
    """

    def __init__(self, log_path: str | None = None) -> None:
        self._enabled: bool = bool(log_path)
        self._log_path: str | None = log_path
        self._logger: logging.Logger | None = None
        self._lock = threading.Lock()
        if self._enabled:
            self._install_handler()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def log_path(self) -> str | None:
        return self._log_path

    # ------------------------------------------------------------------
    # Internal setup
    # ------------------------------------------------------------------

    def _install_handler(self) -> None:
        """Attach a 10 MB rotating file handler to a dedicated logger.

        Uses a dedicated logger name (``nmos.controller.debug_trace``)
        so we don't inherit every other module's handlers — the
        debug log stays clean and dedicated.
        """
        assert self._log_path is not None
        Path(self._log_path).parent.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("nmos.controller.debug_trace")
        logger.setLevel(logging.DEBUG)
        # Avoid duplicate handlers when multiple DebugTrace instances
        # are created in the same process (e.g. two tests).
        for h in list(logger.handlers):
            if isinstance(h, logging.handlers.RotatingFileHandler) \
                    and Path(h.baseFilename).resolve() == Path(self._log_path).resolve():
                logger.removeHandler(h)
                h.close()
        handler = logging.handlers.RotatingFileHandler(
            self._log_path,
            maxBytes=10 * 1024 * 1024,  # 10 MB per file
            backupCount=3,               # keep 3 rotations
            encoding="utf-8",
        )
        # JSONL — one object per line, easy to grep / jq.
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        # Do not propagate to the root logger — root already mirrors
        # the same events to stdout; we don't want double entries in
        # the debug file when root gets the rotating handler too.
        logger.propagate = False
        self._logger = logger
        # First line marks the start of a new debug session so you
        # can tell where one run ends and the next begins.
        self.emit("session_start", path=self._log_path)

    # ------------------------------------------------------------------
    # Correlation ids
    # ------------------------------------------------------------------

    @staticmethod
    def new_trace_id() -> str:
        """Mint a fresh hex trace id. Safe to call when disabled."""
        return secrets.token_hex(_TRACE_ID_LEN // 2)

    # ------------------------------------------------------------------
    # Emit
    # ------------------------------------------------------------------

    def emit(self, kind: str, **fields: Any) -> None:
        """Append one structured event line to the debug log.

        No-op when tracing is disabled. Never raises — debug failures
        must not affect production paths.
        """
        if not self._enabled or self._logger is None:
            return
        record: dict[str, Any] = {
            "t": round(time.time(), 6),
            "kind": kind,
            **fields,
        }
        try:
            with self._lock:
                self._logger.info(json.dumps(record, default=str))
        except Exception:
            # Debug instrumentation must never bubble an exception
            # into the request path. Swallow and move on.
            pass
