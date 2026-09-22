# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Keep the Rust CLI corpus honest.

``rust/crates/nmos-registry-bin/tests/cli_flags.json`` records this program's
command line, and the Rust suite asserts its own parser accepts exactly the same
flags with the same defaults, arities and choices. That only means anything
while the recording is current.

Without this test the failure is silent and the wrong way round: add a flag
here, and the Rust side keeps agreeing with a command line that no longer
exists. Both suites stay green, and a launch script carrying the new flag fails
only against the Rust binary — at which point nothing points back here.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from nmos.registry.tests._cli_corpus import OUTPUT, build


def _committed() -> dict[str, dict[str, Any]]:
    if not OUTPUT.exists():
        pytest.fail(
            f"{OUTPUT} is missing.\n"
            f"  python -m nmos.registry.tests._cli_corpus",
        )
    corpus: dict[str, Any] = json.loads(OUTPUT.read_text(encoding="utf-8"))
    flags: list[dict[str, Any]] = corpus["flags"]
    return {str(flag["dest"]): flag for flag in flags}


def test_the_committed_corpus_matches_this_command_line() -> None:
    """Fails the moment a flag, default, arity or choice list changes."""
    committed = _committed()
    current = {str(flag["dest"]): flag for flag in build()["flags"]}

    added = sorted(set(current) - set(committed))
    removed = sorted(set(committed) - set(current))
    assert not added and not removed, (
        f"the command line changed — added {added}, removed {removed}.\n"
        "Regenerate and re-run the Rust suite:\n"
        "  python -m nmos.registry.tests._cli_corpus\n"
        "  (cd rust && cargo test -p nmos-registry-bin)"
    )

    differing = sorted(name for name, flag in current.items() if committed[name] != flag)
    assert not differing, (
        f"{len(differing)} flag(s) changed shape: {differing}\n"
        "Regenerate and re-run the Rust suite:\n"
        "  python -m nmos.registry.tests._cli_corpus\n"
        "  (cd rust && cargo test -p nmos-registry-bin)"
    )


def test_every_flag_is_camel_case_like_the_node_launcher() -> None:
    """The spelling is the contract.

    ``nmos_registry.py``'s flags deliberately mirror ``nmos_node.py``'s, and
    the Rust parser has to give each one explicitly because clap would
    otherwise derive a kebab-case name from its field. A flag added here in a
    different style would be a divergence nobody notices until a launch script
    fails against one implementation.
    """
    odd = [
        flag["options"]
        for flag in build()["flags"]
        if any("-" in option.removeprefix("--") for option in flag["options"])
    ]
    assert not odd, f"non-camelCase flags: {odd}"
