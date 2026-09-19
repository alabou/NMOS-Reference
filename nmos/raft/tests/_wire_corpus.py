# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Export the wire golden vectors for the Rust codec, from the test that owns them.

``test_wire.py`` holds ``_GOLDEN``: hex encodings of frames that a codec change
must not alter without someone deciding to alter them. The Rust codec has to
produce the same bytes, and the obvious way to arrange that -- copying the hex
into a Rust file -- would give the two implementations *separate* recordings.
Two recordings drift, and the drift is invisible: each side keeps agreeing with
its own copy.

So the vectors are read out of ``test_wire.py`` **without modifying it**, which
is decision #12 of the port plan: "``nmos/raft/tests/test_wire.py`` **not
modified**. Rust wire vectors exported from the unmodified module at build
time." This imports ``_GOLDEN`` and ``_frames`` and writes what they already
say; the names begin with an underscore, and reaching for them is the point --
a public accessor added for this exporter's benefit would be a modification.

Regenerate with::

    python -m nmos.raft.tests._wire_corpus
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nmos.raft.wire import Writer
from nmos.raft.tests.test_wire import _GOLDEN, _frames

# How each golden payload is built, field by field, mirroring `_frames()`.
# Transcribed rather than derived: `_frames` expresses this as chained `Writer`
# calls, and reading it back out of the bytes would be re-deriving the answer
# from the answer.
_FIELDS: dict[str, list[tuple[str, int, int | bool | str]]] = {
    "request_vote": [
        ("uint", 1, 5),
        ("uint", 2, 0),
        ("uint", 3, 10),
        ("uint", 4, 5),
    ],
    "vote_reply_granted": [
        ("uint", 1, 5),
        ("bool", 2, True),
        ("bool", 3, True),
    ],
    "hello": [
        ("uint", 1, 1),
        ("uint", 2, 0),
        ("string", 3, "nmos-registry-abc"),
        ("uint", 4, 2),
        ("uint", 5, 7),
    ],
    "append_empty": [
        ("uint", 1, 9),
        ("uint", 2, 1),
        ("uint", 3, 42),
        ("uint", 4, 9),
        ("uint", 5, 42),
        ("uint", 9, 3),
    ],
    "install_snapshot": [
        ("uint", 1, 4),
        ("uint", 2, 0),
        ("uint", 3, 100),
        ("uint", 4, 4),
        ("bytes", 7, "010203"),
        ("bool", 8, True),
    ],
    "ping": [],
}

OUTPUT = (
    Path(__file__).resolve().parents[3]
    / "rust" / "crates" / "nmos-registry-raft" / "tests" / "wire_vectors.json"
)


def build() -> dict[str, Any]:
    """Each golden frame, as fields plus the bytes they must encode to."""
    frames = _frames()

    missing = sorted(set(_GOLDEN) - set(frames))
    extra = sorted(set(frames) - set(_GOLDEN))
    if missing or extra:
        raise SystemExit(
            f"test_wire.py's _GOLDEN and _frames disagree — "
            f"only in _GOLDEN: {missing}, only in _frames: {extra}",
        )

    cases = []
    for name in sorted(_GOLDEN):
        frame = frames[name]

        # `_FIELDS` is transcribed from `_frames`, so it can drift from it.
        # Rebuilding the payload from the transcription and comparing makes the
        # duplication self-checking: if the two disagree, the export fails here
        # rather than handing Rust a description of a frame that no longer
        # exists.
        rebuilt = Writer()
        for kind, number, value in _FIELDS[name]:
            if kind == "uint":
                rebuilt.uint(number, int(value))
            elif kind == "bool":
                rebuilt.bool_(number, bool(value))
            elif kind == "string":
                rebuilt.string(number, str(value))
            elif kind == "bytes":
                rebuilt.bytes_(number, bytes.fromhex(str(value)))
            else:
                raise SystemExit(f"{name}: unknown field kind {kind!r}")
        if rebuilt.take() != frame.payload:
            raise SystemExit(
                f"{name}: _FIELDS no longer describes the frame _frames builds "
                f"-- transcription is stale",
            )
        cases.append({
            "name": name,
            # The routing header, so the Rust side builds the frame from the
            # same description rather than from the answer.
            "stream": int(frame.stream),
            "type": int(frame.type),
            "flags": int(frame.flags),
            "minor": int(frame.minor),
            "payload_hex": frame.payload.hex(),
            # The fields the payload is built from, so the Rust side can build
            # it with its own `Writer` rather than replaying bytes. Replaying
            # pre-built hex tests the framing and nothing below it; the
            # primitives -- varint, tag, length prefix -- are where an
            # off-by-one hides.
            "fields": [
                {"kind": kind, "field": number, "value": value}
                for kind, number, value in _FIELDS[name]
            ],
            # And what it must serialise to.
            "encoded_hex": _GOLDEN[name],
        })
    return {
        "protocol_major": 1,
        "cases": cases,
    }


def main() -> None:
    corpus = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(corpus, indent=2, sort_keys=True) + "\n")
    print(f"{len(corpus['cases'])} wire vectors -> {OUTPUT}")


if __name__ == "__main__":
    main()
