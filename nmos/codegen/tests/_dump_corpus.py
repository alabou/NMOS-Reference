# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Generate the synthesised-JSON parity corpus.

Everything the registry *makes up* rather than stores goes out through
``JsonEngine.dump_any`` -- the discovery ladders, subscription responses, health
responses and error bodies. That is ``json.dumps`` with its DEFAULT separators,
so the bytes carry ``", "`` and ``": "`` where ``serde_json`` writes neither.

Both are valid JSON and no client cares. It matters because byte-comparison
against Python is how this port checks itself, and a whitespace difference on
every synthesised response would mean giving that up exactly where it is
cheapest to have.

Regenerate with::

    python -m nmos.codegen.tests._dump_corpus
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nmos.json.engine import JsonEngine

OUTPUT = (
    Path(__file__).parent.parent.parent.parent
    / "rust" / "crates" / "nmos-json" / "tests" / "dump_cases.json"
)

# Shapes taken from what the registry actually synthesises, plus the awkward
# cases: empty containers, nesting, unicode, and the characters that have to be
# escaped identically by both encoders.
STRUCTURES: list[tuple[str, Any]] = [
    ("discovery_ladder", ["x-nmos/"]),
    ("versions", ["v1.3/"]),
    ("query_base", ["nodes/", "devices/", "sources/", "flows/", "senders/",
                    "receivers/", "subscriptions/"]),
    ("health", {"health": "1600000000"}),
    ("error_body", {"code": 400, "error": "bad request", "debug": None}),
    ("subscription", {
        "id": "3b8be755-08ff-452b-b217-c9151eb21193",
        "ws_href": "ws://example.com:8448/x-nmos/query/v1.3/subscriptions/x",
        "max_update_rate_ms": 100,
        "persist": True,
        "secure": False,
        "resource_path": "/senders",
        "params": {},
    }),
    ("empty_object", {}),
    ("empty_array", []),
    ("nested", {"a": [1, 2, {"b": [3, {}]}], "c": {"d": []}}),
    ("unicode", {"label": "caf\u00e9 \u2014 \U0001f600"}),
    ("escapes", {"s": 'quote " backslash \\ newline \n tab \t'}),
    ("control_chars", {"s": "\x00\x1f\x7f"}),
    ("numbers", {"i": 0, "neg": -1, "big": 2**53, "f": 1.5, "small": 1e-5}),
    ("bools_and_null", {"t": True, "f": False, "n": None}),
    ("array_of_objects", [{"a": 1}, {"b": 2}]),
    ("deep", {"a": {"b": {"c": {"d": {"e": [1, [2, [3]]]}}}}}),
]


def build() -> list[dict[str, Any]]:
    """What ``dump_any`` actually wrote, for each structure."""
    return [
        {
            "label": label,
            "value": value,
            "dumped": JsonEngine.dump_any(value),
        }
        for label, value in STRUCTURES
    ]


def main() -> None:
    cases = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # NOT sort_keys: the whole point is that `dumped` preserves the insertion
    # order Python used, so sorting the stored `value` would make the Rust side
    # re-serialise a differently-ordered object and compare it against bytes
    # produced from the original. The corpus is deterministic regardless,
    # because the structures above are literals.
    OUTPUT.write_text(
        json.dumps(cases, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"{len(cases)} dump cases -> {OUTPUT}")


if __name__ == "__main__":
    main()
