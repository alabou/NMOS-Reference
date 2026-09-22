# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Generate the span-slicing parity corpus.

``nmos/json/spans.py`` is what keeps a registration byte-for-byte: it slices the
``data`` value out of the POST envelope so the registry stores what arrived
rather than a re-rendering of it. The Rust side re-implements it, and this is
the evidence that the two agree.

Two things have to match, and the second is the fiddly one
----------------------------------------------------------
1. **The span**, for every well-formed object -- byte-identical, including the
   spelling a parse would have thrown away (``1e3`` stays ``1e3``,
   ``"caf\\u00e9"`` stays escaped).
2. **The error message**, for every malformed one. These are not diagnostics:
   ``nmos/registry/decode.py:149`` writes them into the HTTP 400 body as
   ``invalid JSON body: {exc}``, so a client reads text like::

       bad value for 'data' at offset 37: Expecting value: line 1 column 5 (char 4)

   The suffix comes from Python's own ``json.decoder``, which is why Rust has to
   scan rather than delegate to ``serde_json`` -- whose messages are worded
   entirely differently.

Cases are chosen for where the two could plausibly part company
---------------------------------------------------------------
* **Unicode**, because Python reports *character* offsets while a Rust scanner
  naturally works in bytes, and the two differ the moment a body is not ASCII;
* **``NaN`` / ``Infinity``**, which Python's ``json`` accepts and no JSON parser
  should -- a body carrying one is stored today;
* **a leading zero**, where Python's number scanner stops early and the *outer*
  scan reports the error;
* **duplicate keys**, which resolve to the last occurrence;
* **a nested key with the same name**, which must not be mistaken for the
  top-level one;
* **seeded random mutation** of well-formed documents, which is the only part of
  this corpus nobody designed -- and therefore the part most likely to find
  something.

Regenerate with::

    python -m nmos.codegen.tests._span_corpus
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from nmos.json.spans import JsonSpanError, member_spans

OUTPUT = (
    Path(__file__).parent.parent.parent.parent
    / "rust" / "crates" / "nmos-json" / "tests" / "span_cases.json"
)

SEED = 20260917

WELL_FORMED: list[str] = [
    "{}",
    "  {  }  ",
    '{"a": 1}',
    '{"a": 1, "b": 2}',
    '{"type": "node", "data": {"x": 1e3}}',
    # Spelling a parse would normalise away.
    '{"x": 1e3, "y": 1000.0, "z": 1E+3, "w": -0.0}',
    '{"s": "caf\\u00e9", "t": "café"}',
    '{"s": "quote \\" backslash \\\\ newline \\n tab \\t"}',
    '{"nested": {"a": [1, 2, {"b": null}]}, "after": true}',
    '{"empty_obj": {}, "empty_arr": [], "empty_str": ""}',
    # Python's json accepts these; they are not valid JSON.
    '{"a": NaN}',
    '{"a": Infinity}',
    '{"a": -Infinity}',
    '{"a": [NaN, Infinity]}',
    # Duplicates: the last wins.
    '{"a": 1, "a": 2}',
    '{"a": {"a": 1}, "a": 3}',
    # A nested key with the top-level name must not shadow it.
    '{"a": {"data": 1}, "data": 2}',
    # Whitespace in every gap.
    '{\n  "a"  :  1 ,\n  "b" : [ 1 , 2 ]\n}',
    '{"a":1,"b":2}',
    # Numbers at the edges.
    '{"a": -1, "b": 0, "c": 1e308, "d": 5e-324, "e": 1.5E-7}',
    # Unicode keys, which move every offset after them.
    '{"café": 1, "b": 2}',
    '{"\\u00e9": 1}',
    # Deep nesting.
    '{"a": {"b": {"c": {"d": {"e": [1, [2, [3]]]}}}}}',
]

MALFORMED: list[str] = [
    "",
    "   ",
    "[1,2]",
    "null",
    '"a string"',
    "{",
    '{"a"',
    '{"a":',
    '{"a": 1',
    '{"a" 1}',
    "{a: 1}",
    "{'a': 1}",
    '{"a": 1,}',
    '{"a": 1 2}',
    '{"a": xyz}',
    '{"a": True}',
    '{"a": -}',
    '{"a": 01}',
    '{"a": "abc}',
    '{"a": "a\\qb"}',
    '{"a": "x\ty"}',
    '{"a": {"b" 1}}',
    '{"a": [1 2]}',
    '{"a": {b: 1}}',
    '{"abc',
    '{"café": 1 2}',
    '{"café": xyz}',
    '{"a": [1, 2',
    '{"a": {"b": 1',
]


def _mutations(rng: random.Random, source: str, count: int) -> list[str]:
    """Random single-edit corruptions of a well-formed document.

    Deliberately crude: delete, duplicate or replace one character. The point is
    not realism but coverage of positions nobody thought to write a case for.
    """
    out: list[str] = []
    if not source:
        return out
    alphabet = '{}[]",:0123456789abcnetru \\\t\n'
    for _ in range(count):
        index = rng.randrange(len(source))
        choice = rng.randrange(3)
        if choice == 0:
            out.append(source[:index] + source[index + 1 :])
        elif choice == 1:
            out.append(source[:index] + source[index] + source[index:])
        else:
            out.append(source[:index] + rng.choice(alphabet) + source[index + 1 :])
    return out


def run_one(source: str) -> dict[str, Any]:
    """What Python's scanner did with this text, recorded exactly."""
    try:
        spans = member_spans(source)
    except JsonSpanError as exc:
        return {"ok": False, "message": str(exc)}
    except RecursionError:
        # Deep nesting can exhaust the interpreter stack; not a span error and
        # not something the Rust side should be asked to reproduce.
        return {"ok": None, "message": "recursion"}
    return {"ok": True, "spans": [[name, span] for name, (span, _v) in spans.items()]}


def build() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(source: str, origin: str) -> None:
        if source in seen:
            return
        seen.add(source)
        record = run_one(source)
        if record["ok"] is None:
            return
        record.update({"source": source, "origin": origin})
        cases.append(record)

    for source in WELL_FORMED:
        add(source, "well_formed")
    for source in MALFORMED:
        add(source, "malformed")

    rng = random.Random(SEED)
    for source in WELL_FORMED:
        for mutated in _mutations(rng, source, 30):
            add(mutated, "fuzz")

    return cases


def main() -> None:
    cases = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(cases, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    rejected = sum(1 for c in cases if not c["ok"])
    print(f"{len(cases)} span cases ({rejected} rejected) -> {OUTPUT}")


if __name__ == "__main__":
    main()
