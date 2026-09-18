# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Record the NMOS error body for every status, for the Rust port to match.

``error_response`` puts ``str(exc)`` straight into ``debug``
(``handlers_registration.py:158``), so the body is an observable part of the
API rather than a debugging aid — and ``error`` is
``http.HTTPStatus(status).phrase``, which is a 62-entry table that a Rust port
has to reproduce rather than approximate. ``StatusCode::canonical_reason`` is
*not* the same table: it returns ``None`` for codes CPython knows, and differs
in wording on others.

So this records what Python actually produces, across every status CPython
recognises and a set of ``debug`` strings chosen to exercise the encoder —
quotes, backslashes, control characters, non-ASCII, an empty string.
Regenerate with::

    python -m nmos.api.tests._error_corpus

``test_error_corpus.py`` fails when the recording is stale.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from pathlib import Path
from typing import Any

from nmos.json.engine import JsonEngine

OUTPUT = (
    Path(__file__).resolve().parents[3]
    / "rust"
    / "crates"
    / "nmos-registry-http"
    / "tests"
    / "error_cases.json"
)

#: ``debug`` strings that exercise the encoder rather than the status table.
DEBUG_VALUES: list[str] = [
    "",
    "plain message",
    'quote " and \\ back',
    "café",
    "日本語",
    "\U0001f600",
    "newline\nand\ttab",
    "control\x00\x1f",
    "invalid TAI timestamp: 'y'",
    'resource_path "/it\'s" is not subscribable',
    "a" * 200,
]

#: Statuses the registry actually answers with, each paired with every debug
#: value. The remaining statuses are covered once, for the phrase table.
HOT_STATUSES: list[int] = [400, 401, 403, 404, 405, 409, 500, 501, 503]


def _body(status: int, debug: str) -> str:
    try:
        status_text = HTTPStatus(status).phrase
    except ValueError:
        status_text = "Unknown Error"
    body = {"code": status, "error": status_text, "debug": debug}
    return str(JsonEngine.dump_any(body, indent=2))


def build() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []

    # Every status CPython knows, so the phrase table is covered entry for
    # entry rather than at the nine codes that happen to be reachable today.
    for status in range(100, 600):
        try:
            HTTPStatus(status)
        except ValueError:
            continue
        cases.append(
            {"status": status, "debug": "x", "body": _body(status, "x")},
        )

    # Plus codes CPython does NOT know, which take the "Unknown Error" branch.
    for status in (599, 599 - 1, 209, 449):
        if status not in {case["status"] for case in cases}:
            cases.append(
                {"status": status, "debug": "x", "body": _body(status, "x")},
            )

    for status in HOT_STATUSES:
        for debug in DEBUG_VALUES:
            cases.append(
                {"status": status, "debug": debug, "body": _body(status, debug)},
            )

    return {"cases": cases}


def main() -> None:
    corpus = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(corpus, indent=1, sort_keys=True) + "\n")
    print(f"{len(corpus['cases'])} cases -> {OUTPUT}")


if __name__ == "__main__":
    main()
