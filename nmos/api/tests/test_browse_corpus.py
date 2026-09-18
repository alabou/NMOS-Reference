# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Keep the Rust browsing-view corpus honest.

``rust/crates/nmos-registry-http/tests/browse_cases.json`` records what
``_json_to_html`` renders for 63 cases, and the Rust suite asserts its own
renderer produces the same bytes. That only means anything while the recording
is current.

Without this test the failure is silent and the wrong way round: change the
Python renderer, and the Rust side keeps agreeing with what Python *used* to
do. Both suites stay green and the two implementations diverge precisely
because the check that was supposed to catch it is comparing against a stale
answer.

Every case is deterministic — no clocks, no randomness — so this compares
outright rather than approximately.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from nmos.api.tests._browse_corpus import CASES, OUTPUT, build


def _committed_cases() -> dict[str, dict[str, Any]]:
    if not OUTPUT.exists():
        pytest.fail(
            f"{OUTPUT} is missing.\n"
            f"  python -m nmos.api.tests._browse_corpus",
        )
    corpus: dict[str, Any] = json.loads(OUTPUT.read_text())
    cases: list[dict[str, Any]] = corpus["cases"]
    return {str(case["name"]): case for case in cases}


def test_the_committed_corpus_matches_what_python_renders_now() -> None:
    """Fails the moment the renderer's output changes without a rebuild."""
    committed_cases = _committed_cases()
    current_cases = {str(case["name"]): case for case in build()["cases"]}

    assert committed_cases.keys() == current_cases.keys(), (
        "the corpus case list changed; regenerate it:\n"
        "  python -m nmos.api.tests._browse_corpus"
    )

    differing = [
        name
        for name, case in current_cases.items()
        if committed_cases[name]["html"] != case["html"]
    ]
    assert not differing, (
        f"{len(differing)} recorded page(s) no longer match what the renderer "
        f"produces: {', '.join(sorted(differing))}\n"
        "Regenerate and re-run the Rust suite:\n"
        "  python -m nmos.api.tests._browse_corpus\n"
        "  (cd rust && cargo test -p nmos-registry-http)"
    )


def test_every_case_has_a_distinct_name() -> None:
    """A duplicate name silently drops a case from the comparison above."""
    names = [name for name, _json, _path, _resolver in CASES]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert not duplicates, f"duplicated case names: {duplicates}"
