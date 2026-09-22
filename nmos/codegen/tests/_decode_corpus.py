# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Generate the decode parity corpus: what Python's generated types decide.

The validator corpus proves the 67 hand-ported assertions agree. This proves
the far larger claim: that the *generated* types agree -- that the Rust emitter
produces decode paths reaching the same verdict, with the same message, as the
Python ones rendered from the same descriptors.

It is the assertion the whole emitter exists to satisfy, and it cannot be made
by reading the template. Only by running both.

What the cases are chosen to pin
--------------------------------
Bodies come from ``nmos/registry/tests/_fixtures.py`` -- the shapes every
registry test already depends on -- and are then mutated along the axes where
the two implementations could plausibly disagree:

* **member order**, because Python fails in *descriptor* order rather than
  document order. Two bodies differing only in key order must produce the same
  error, which is the single property that forced a generated decode rather
  than ``#[derive(Deserialize)]``;
* **missing required members**, singly and in pairs, because which one is named
  is decided by descriptor order;
* **nulls**, because a null string is silently dropped while a null URL becomes
  a defined empty string;
* **wrong types**, because the "expected X, got Y" message renders a *Python*
  type name (``int``, ``NoneType``) that Rust has to reproduce;
* **polymorphic discriminators**, including near-misses, because dispatch is
  ordered and committal: once a predicate matches, that variant's error is the
  answer and no later variant is tried.

Regenerate with::

    python -m nmos.codegen.tests._decode_corpus
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from nmos.errors import NmosError
from nmos.registry.decode import decode_resource
from nmos.registry.tests._fixtures import (
    make_device,
    make_flow,
    make_node,
    make_sender,
    make_source,
)
from nmos.registry.types import ResourceType

OUTPUT = (
    Path(__file__).parent.parent.parent.parent
    / "rust" / "crates" / "nmos-types" / "tests" / "decode_cases.json"
)

# As in `_encode_corpus` and `_structural_corpus`: a fixed version keeps the
# corpus stable across runs, so its drift guard compares behaviour rather than
# the clock. Without it the fixtures stamp `tai_version()` at import time, every
# regeneration produces a different file, and the recording cannot be checked
# against a fresh build at all -- which is what `test_codegen_corpora.py` found.
FIXED_VERSION = "1700000000:123456789"

BASE: list[tuple[ResourceType, Any]] = [
    (ResourceType.NODE, make_node(version=FIXED_VERSION)),
    (ResourceType.DEVICE, make_device(version=FIXED_VERSION)),
    (ResourceType.SOURCE, make_source(version=FIXED_VERSION)),
    (ResourceType.FLOW, make_flow(version=FIXED_VERSION)),
    (ResourceType.SENDER, make_sender(version=FIXED_VERSION)),
]


def _reversed_keys(body: dict[str, Any]) -> dict[str, Any]:
    """The same body with its keys in the opposite order.

    Must decode to the same verdict. If it does not, decode is following the
    document rather than the descriptor.
    """
    return dict(reversed(list(body.items())))


def _mutations(
    resource_type: ResourceType, body: dict[str, Any],
) -> list[tuple[str, Any]]:
    """Cases derived from one good body."""
    out: list[tuple[str, Any]] = [
        ("valid", body),
        ("keys_reversed", _reversed_keys(body)),
        ("not_an_object", "a string"),
        ("empty_object", {}),
    ]

    for key in sorted(body):
        missing = copy.deepcopy(body)
        del missing[key]
        out.append((f"missing_{key}", missing))

        nulled = copy.deepcopy(body)
        nulled[key] = None
        out.append((f"null_{key}", nulled))

        retyped = copy.deepcopy(body)
        retyped[key] = 12345 if isinstance(body[key], str) else "not-the-right-type"
        out.append((f"retyped_{key}", retyped))

    # Two required members absent at once: which one is named is decided by
    # descriptor order, not by which was removed first.
    keys = sorted(body)
    if len(keys) >= 2:
        both = copy.deepcopy(body)
        del both[keys[0]]
        del both[keys[1]]
        out.append((f"missing_{keys[0]}_and_{keys[1]}", both))

    # Polymorphic discriminators: each real format, plus a near-miss.
    if "format" in body:
        for fmt in (
            "urn:x-nmos:format:video",
            "urn:x-nmos:format:audio",
            "urn:x-nmos:format:data",
            "urn:x-nmos:format:mux",
            "urn:x-nmos:format:smell",
            "urn:x-nmos:format:video\n",
        ):
            variant = copy.deepcopy(body)
            variant["format"] = fmt
            # The label must survive a trailing newline distinctly: stripping
            # it would collide `format:video` with `format:video\n`, which are
            # accepted and rejected respectively, and the corpus would then
            # hold two different verdicts under one key.
            suffix = fmt.rsplit(":", 1)[-1].replace("\n", "_newline")
            out.append((f"format_{suffix}", variant))

    return out


def run_one(resource_type: ResourceType, body: Any) -> dict[str, Any]:
    """Decode one body and record exactly what Python decided."""
    try:
        decode_resource(resource_type, body)
    except NmosError as exc:
        return {"ok": False, "kind": type(exc).__name__, "message": exc.msg or str(exc)}
    except Exception as exc:  # noqa: BLE001 - the DecodeFailure wrapper and friends
        return {"ok": False, "kind": type(exc).__name__, "message": str(exc)}
    return {"ok": True}


def build() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for resource_type, body in BASE:
        for label, mutated in _mutations(resource_type, body):
            record = run_one(resource_type, mutated)
            record.update(
                {
                    "resource_type": resource_type.value,
                    "label": label,
                    "body": mutated,
                },
            )
            cases.append(record)
    return cases


def main() -> None:
    cases = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(cases, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    rejected = sum(1 for c in cases if not c["ok"])
    print(f"{len(cases)} cases ({rejected} rejected) -> {OUTPUT}")


if __name__ == "__main__":
    main()
