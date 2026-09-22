# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Keep the Rust registration-envelope corpus honest.

``rust/crates/nmos-registry/tests/envelope_cases.json`` records the verdict, the
stored bytes and the failure message Python produces for 43 ``POST /resource``
envelopes, and the Rust suite asserts its own decoder reaches the same answers.
That only means anything while the recording is current.

Without this test the failure is silent and the wrong way round: change the
decoder — or the span scanner whose wording reaches the 400 body — and the Rust
side keeps agreeing with what Python *used* to do.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from nmos.registry.tests._envelope_corpus import CASES, OUTPUT, build


def _committed() -> dict[str, dict[str, Any]]:
    if not OUTPUT.exists():
        pytest.fail(
            f"{OUTPUT} is missing.\n"
            f"  python -m nmos.registry.tests._envelope_corpus",
        )
    corpus: dict[str, Any] = json.loads(OUTPUT.read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = corpus["cases"]
    return {str(case["name"]): case for case in cases}


def test_the_committed_corpus_matches_what_python_decodes_now() -> None:
    """Fails the moment a verdict, a stored span or a message changes."""
    committed = _committed()
    current = {str(case["name"]): case for case in build()["cases"]}

    assert committed.keys() == current.keys(), (
        "the corpus case list changed; regenerate it:\n"
        "  python -m nmos.registry.tests._envelope_corpus"
    )

    differing = sorted(name for name, case in current.items() if committed[name] != case)
    assert not differing, (
        f"{len(differing)} recorded envelope(s) no longer match: {differing}\n"
        "Regenerate and re-run the Rust suite:\n"
        "  python -m nmos.registry.tests._envelope_corpus\n"
        "  (cd rust && cargo test -p nmos-registry)"
    )


def test_the_corpus_keeps_both_verdicts_and_every_resource_type() -> None:
    """A corpus of only-rejections would prove the decoder refuses everything."""
    committed = _committed()
    accepted = [case for case in committed.values() if case["ok"]]
    rejected = [case for case in committed.values() if not case["ok"]]
    assert len(accepted) >= 5, "the corpus lost its accepting cases"
    assert len(rejected) >= 20, "the corpus lost its rejecting cases"

    from nmos.registry.types import ResourceType

    validated = {
        case["error"].split(" failed validation", 1)[0]
        for case in rejected
        if "failed validation" in case["error"]
    }
    missing = {rt.value for rt in ResourceType} - validated
    assert not missing, f"no validation case for: {sorted(missing)}"


def test_every_case_has_a_distinct_name() -> None:
    """A duplicate name silently drops a case from the comparison above."""
    names = [name for name, _source in CASES]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert not duplicates, f"duplicated case names: {duplicates}"
