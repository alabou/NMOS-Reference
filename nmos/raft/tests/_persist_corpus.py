# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Export what a member says when it refuses to start, for the Rust port.

``TermStore.load`` refuses rather than guessing, because guessing here means
guessing about whether this member has already cast a vote. The refusal is the
only thing an operator sees, and it is the thing that tells them whether to
delete the file or to go looking for a disk problem.

Two implementations that refuse *differently* for the same file are a support
problem that nobody notices until someone is reading a log at three in the
morning. So the messages are recorded from the Python and asserted by the Rust,
rather than each side being written to its own idea of good wording.

The path is replaced by a placeholder: it is the one part of the message that
legitimately differs between two runs, let alone two implementations.

How far parity goes, and why not further
----------------------------------------
Two of the three refusals interpolate a caught exception -- ``KeyError('term')``,
``invalid literal for int() with base 10``, and ``json.decoder``'s
``Expecting property name enclosed in double quotes``. Those are CPython's
wording for CPython's exceptions. Reproducing them in Rust would mean
hand-copying strings out of the standard library and keeping them in step with
it forever, to make a startup log line match.

So each case carries a ``parity_prefix``: everything up to the point where the
interpolated text begins. The Rust asserts its message *starts with* that, which
is what actually matters -- both implementations must identify the same problem
in the same words, so a reader who has seen one recognises the other. What
follows is allowed to describe the failure in each language's own terms.

This is deliberately a weaker guarantee than the JSON decode path's, where the
error text reaches an HTTP 400 body and is an API contract. A startup refusal
goes to a log, and is read by a person.

Regenerate with::

    python -m nmos.raft.tests._persist_corpus
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from nmos.raft.persist import STATE_VERSION, PersistentStateError, TermStore

OUTPUT = (
    Path(__file__).resolve().parents[3]
    / "rust" / "crates" / "nmos-registry-raft" / "tests" / "persist_refusals.json"
)

# Where the path appears in a message. The Rust side substitutes its own.
PATH_PLACEHOLDER = "<PATH>"

# Every shape of unusable file, named by what is wrong with it. Each must
# refuse, and refuse in a way that says which of these it was -- "state version
# None" for a file that has no version is right, and "state version None" for a
# bare `[]` is not, because it sends the reader after the wrong thing.
_CASES: list[tuple[str, str]] = [
    ("not json at all", "{ this is not json"),
    ("a json list", "[]"),
    ("a json string", '"a string"'),
    ("a json number", "42"),
    ("a json null", "null"),
    ("an unknown version", json.dumps({
        "version": STATE_VERSION + 1, "term": 1, "voted_for": None,
        "incarnation": 1,
    })),
    ("a quoted version", json.dumps({
        "version": str(STATE_VERSION), "term": 1, "voted_for": None,
        "incarnation": 1,
    })),
    ("no version", json.dumps({
        "term": 1, "voted_for": None, "incarnation": 1,
    })),
    ("no term", json.dumps({
        "version": STATE_VERSION, "voted_for": None, "incarnation": 1,
    })),
    ("no vote", json.dumps({
        "version": STATE_VERSION, "term": 1, "incarnation": 1,
    })),
    ("no incarnation", json.dumps({
        "version": STATE_VERSION, "term": 1, "voted_for": None,
    })),
    ("an unparseable term", json.dumps({
        "version": STATE_VERSION, "term": "not a number", "voted_for": None,
        "incarnation": 1,
    })),
    ("a vote that is not a member index", json.dumps({
        "version": STATE_VERSION, "term": 1, "voted_for": ["a", "list"],
        "incarnation": 1,
    })),
]


# Where an interpolated CPython exception begins, for each refusal that has
# one. Taken from `persist.py`'s own format strings rather than guessed, so a
# reworded refusal shortens the prefix here instead of silently ceasing to
# match.
_INTERPOLATION_POINTS = (
    "is unreadable: ",
    "does not hold a usable term and vote: ",
)


def _parity_prefix(message: str) -> str:
    """How much of ``message`` the other implementation must reproduce exactly.

    Everything up to the first interpolated exception, or the whole message
    when there is none.
    """
    for marker in _INTERPOLATION_POINTS:
        position = message.find(marker)
        if position != -1:
            return message[: position + len(marker)]
    return message


def build() -> dict[str, Any]:
    """Each unusable file, and the refusal this implementation produces."""
    cases = []
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "state.json"
        for name, text in _CASES:
            path.write_text(text)
            try:
                TermStore(path).load()
            except PersistentStateError as exc:
                message = str(exc).replace(str(path), PATH_PLACEHOLDER)
            else:
                # Not an assertion about wording -- an assertion that the file
                # was refused at all. A case that silently loads is a hole in
                # the corpus, and the corpus is what the Rust side trusts.
                raise SystemExit(
                    f"{name!r} was accepted; it must refuse, or it does not "
                    f"belong in this corpus",
                )
            cases.append({
                "name": name,
                "file": text,
                "refusal": message,
                "parity_prefix": _parity_prefix(message),
            })

    names = {case["name"] for case in cases}
    if len(names) != len(cases):
        raise SystemExit("two cases share a name")
    return {"path_placeholder": PATH_PLACEHOLDER, "cases": cases}


def main() -> None:
    corpus = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(corpus, indent=2, sort_keys=True) + "\n")
    print(f"{len(corpus['cases'])} refusals -> {OUTPUT}")


if __name__ == "__main__":
    main()
