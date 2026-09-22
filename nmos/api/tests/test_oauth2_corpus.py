# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Keep the Rust OAuth 2.0 corpus honest.

``rust/crates/nmos-registry-http/tests/oauth2_cases.json`` records what this
module decides about a set of minted tokens, and the Rust suite asserts its own
answers match. That only means something while the recording reflects the
current behaviour.

Without this test the failure is silent and the wrong way round: relax a check
here, and the Rust side keeps agreeing with a verdict this module no longer
returns. Both suites stay green while the two implementations have diverged --
which is precisely the thing the corpus exists to detect.

The comparison is over **verdicts**, not bytes. Every regeneration mints fresh
keys and therefore fresh token strings, so comparing the file literally would
fail on every run and teach everyone to ignore it.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from nmos.api.tests._oauth2_corpus import OUTPUT, build


def _verdicts(corpus: dict[str, Any]) -> dict[str, tuple[bool, bool, bool]]:
    """Case name to the three booleans, which is the whole contract."""
    return {
        str(case["name"]): (
            bool(case["verified"]),
            bool(case["allowed"]),
            bool(case["valid_token"]),
        )
        for case in corpus["cases"]
    }


def _committed() -> dict[str, Any]:
    if not OUTPUT.exists():
        pytest.fail(
            f"{OUTPUT} is missing.\n"
            f"  python -m nmos.api.tests._oauth2_corpus",
        )
    parsed: dict[str, Any] = json.loads(OUTPUT.read_text(encoding="utf-8"))
    return parsed


def test_the_committed_corpus_matches_this_implementation() -> None:
    """Fails the moment a token's verdict changes."""
    committed = _verdicts(_committed())
    current = _verdicts(build())

    added = sorted(set(current) - set(committed))
    removed = sorted(set(committed) - set(current))
    assert not added and not removed, (
        f"the case list changed — added {added}, removed {removed}.\n"
        "Regenerate and re-run the Rust suite:\n"
        "  python -m nmos.api.tests._oauth2_corpus\n"
        "  (cd rust && cargo test -p nmos-registry-http --test oauth2_parity)"
    )

    differing = {
        name: (committed[name], verdict)
        for name, verdict in current.items()
        if committed[name] != verdict
    }
    assert not differing, (
        "the verdict changed for "
        f"{len(differing)} case(s) — (verified, allowed, valid_token) "
        f"recorded vs now: {differing}\n"
        "Regenerate and re-run the Rust suite:\n"
        "  python -m nmos.api.tests._oauth2_corpus\n"
        "  (cd rust && cargo test -p nmos-registry-http --test oauth2_parity)"
    )


def test_the_corpus_covers_both_answers() -> None:
    """A corpus that only ever refuses would pass parity and prove nothing.

    Guards the two distinctions the Rust port is most likely to get wrong: a
    token that verifies but is then rejected as invalid (401), against one that
    verifies and is merely not permitted (403).
    """
    verdicts = _verdicts(build()).values()
    verified = [v for v in verdicts if v[0]]
    assert len(verified) >= 5, "too few tokens verify"
    assert len(list(verdicts)) - len(verified) >= 5, "too few fail to verify"
    assert any(allowed for _, allowed, _ in verdicts), "nothing is ever allowed"
    assert any(
        not valid for ok, _, valid in verdicts if ok
    ), "no verified-but-invalid (401) case"
    assert any(
        valid and not allowed for _, allowed, valid in verdicts
    ), "no forbidden (403) case"
