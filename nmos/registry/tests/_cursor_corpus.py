# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Which cursor strings this implementation accepts, for the Rust port.

``TaiCursor.parse`` is the accept/reject boundary for three client-reachable
inputs: ``paging.since``, ``paging.until`` and a resource's ``version``. All
three are bounded by the same pattern, ``^[0-9]+:[0-9]+$``
(``QueryAPI.raml:31,35`` and ``resource_core.json``'s ``version``), and that
pattern puts **no ceiling on either field**.

Why this has its own corpus rather than riding on ``_store_corpus.py``
----------------------------------------------------------------------
It did ride on it, and that did not work. Boundary versions were added to the
step generator and duly appeared in the recording -- and a Rust build that
rejected them still replayed all 400 steps cleanly, because a version is only
*parsed* when it updates an existing resource, and none of the boundary steps
happened to land on an update. The corpus had coverage without exercise, which
is the more dangerous of the two failure modes because it reads as coverage.

So the boundary gets an oracle aimed straight at it. Every string here is
recorded with what this implementation does, and the Rust asserts the same
verdict -- no sequence, no chance of missing the path.

The case that motivated it
--------------------------
``0:5000000000`` is pattern-valid, and Python's ``int()`` parses it, so a
registration carrying it is accepted here. A Rust build whose nanosecond field
was 32 bits returned ``None`` and refused the same body with a 400. Measured at
``store.prepare``: ``PreparedRegistration`` here, ``RegistrationError.SCHEMA``
there.

Regenerate with::

    python -m nmos.registry.tests._cursor_corpus
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nmos.registry.types import TaiCursor

OUTPUT = (
    Path(__file__).resolve().parents[3]
    / "rust" / "crates" / "nmos-registry-core" / "tests" / "cursor_cases.json"
)

# Grouped by what each group is testing, because a flat list of strings is
# unreadable and the next person to add one needs to know which group it joins.
_CASES: list[str] = [
    # -- ordinary -----------------------------------------------------------
    "0:0",
    "1:2",
    "1441716120:318744030",
    "1700000000:123456789",
    "0:999999999",

    # -- pattern-valid, not a real instant ----------------------------------
    # The schema bounds neither field. These are what a 32-bit nanosecond
    # field silently narrowed away.
    "0:1000000000",
    "0:1500000000",
    "0:4294967295",
    "0:4294967296",
    "0:5000000000",
    "0:18446744073709551615",

    # Nothing here has twenty or more digits, and that is deliberate. Python's
    # `int()` accepts `99999999999999999999999:0`; the Rust does not, and that
    # divergence was raised and **accepted** rather than closed, because closing
    # it means arbitrary precision in a type compared on every store insert and
    # every page query. A case added here would fail the Rust parity test, and
    # it would be reporting an agreed decision as a defect.

    # -- leading zeros, which the pattern permits and `int()` accepts -------
    "00:00",
    "0000000001:0000000002",

    # -- rejected: the pattern permits neither sign, space nor separator ----
    "",
    ":",
    "1:",
    ":2",
    "1",
    "1:2:3",
    "+1:2",
    "1:+2",
    " 1:2",
    "1:2 ",
    "1_0:2",
    "1:2_0",
    "-1:2",
    "1.0:2",
    "a:2",
    "1:b",
    "0x10:2",
    # A trailing newline: `str.isdigit()` rejects it, and this is the same
    # hazard the validators' `$`-versus-`\\Z` fix addressed elsewhere.
    "1:2\n",
    "\n1:2",
]


def build() -> dict[str, Any]:
    """Each candidate string, and what this implementation makes of it."""
    cases = []
    for text in _CASES:
        parsed = TaiCursor.parse(text)
        cases.append({
            "text": text,
            # `None` means refused. For an accepted string the two fields are
            # recorded separately rather than as the rendered form, so a
            # difference says which half disagreed.
            "seconds": None if parsed is None else parsed.seconds,
            "nanoseconds": None if parsed is None else parsed.nanoseconds,
            # What it renders back to. A cursor that parses but does not
            # round-trip would corrupt `X-Paging-Since`/`Until`, which echo it.
            "rendered": None if parsed is None else str(parsed),
        })

    if len({case["text"] for case in cases}) != len(cases):
        raise SystemExit("two cases share a string")
    return {"cases": cases}


def main() -> None:
    corpus = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(corpus, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    accepted = sum(1 for case in corpus["cases"] if case["seconds"] is not None)
    print(
        f"{len(corpus['cases'])} cursor cases "
        f"({accepted} accepted, {len(corpus['cases']) - accepted} refused) "
        f"-> {OUTPUT}",
    )


if __name__ == "__main__":
    main()
