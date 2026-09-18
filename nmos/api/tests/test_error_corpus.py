# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Keep the Rust error-body corpus honest.

``rust/crates/nmos-registry-http/tests/error_cases.json`` records the NMOS
error body Python produces for 165 ``(status, debug)`` pairs, and the Rust suite
asserts its own ``error_body`` produces the same bytes. That only means anything
while the recording is current.

Without this test the failure is silent and the wrong way round: change the
error format — or upgrade to a CPython whose ``HTTPStatus`` table has moved —
and the Rust side keeps agreeing with what Python *used* to do.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from nmos.api.tests._error_corpus import OUTPUT, build


def _committed() -> list[dict[str, Any]]:
    if not OUTPUT.exists():
        pytest.fail(
            f"{OUTPUT} is missing.\n"
            f"  python -m nmos.api.tests._error_corpus",
        )
    corpus: dict[str, Any] = json.loads(OUTPUT.read_text())
    cases: list[dict[str, Any]] = corpus["cases"]
    return cases


def _key(case: dict[str, Any]) -> tuple[int, str]:
    return int(case["status"]), str(case["debug"])


def test_the_committed_corpus_matches_what_python_produces_now() -> None:
    """Fails the moment the error format or the phrase table changes."""
    committed = {_key(case): case["body"] for case in _committed()}
    current = {_key(case): case["body"] for case in build()["cases"]}

    assert committed.keys() == current.keys(), (
        "the corpus case list changed; regenerate it:\n"
        "  python -m nmos.api.tests._error_corpus"
    )

    differing = sorted(key for key, body in current.items() if committed[key] != body)
    assert not differing, (
        f"{len(differing)} recorded error body/bodies no longer match: "
        f"{differing[:5]}\n"
        "Regenerate and re-run the Rust suite:\n"
        "  python -m nmos.api.tests._error_corpus\n"
        "  (cd rust && cargo test -p nmos-registry-http)"
    )


def test_the_corpus_covers_the_whole_status_table() -> None:
    """The phrase table is the point; a shrunken corpus would hide a gap."""
    from http import HTTPStatus

    known = {status.value for status in HTTPStatus}
    covered = {int(case["status"]) for case in _committed()}
    assert known <= covered, (
        f"the corpus no longer covers every status CPython knows; "
        f"missing {sorted(known - covered)}"
    )
