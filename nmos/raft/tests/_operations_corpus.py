# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Export the operation samples for the Rust codec, from the test that owns them.

The same arrangement as ``_wire_corpus.py`` and ``_messages_corpus.py``, at the
layer that matters most for a mixed cluster: an *operation* is what a committed
entry contains, so two implementations that encode one differently do not merely
fail to talk -- they apply different mutations from the same entry, which is a
state-machine divergence rather than a connection problem.

``test_operations.py`` is read and **not modified**. Its ``_SAMPLES`` set every
field to something distinguishable, unlike ``test_messages.py``'s, so no
saturated variant is needed here; a check below asserts that rather than
assuming it, because the samples can be edited and this exporter is what decides
whether the Rust side ever sees a field set.

Regenerate with::

    python -m nmos.raft.tests._operations_corpus
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from nmos.raft.operations import OpKind, ProposalId, encode_operation
from nmos.raft.tests.test_operations import _SAMPLES
from nmos.registry.types import ResourceType, TaiCursor

OUTPUT = (
    Path(__file__).resolve().parents[3]
    / "rust" / "crates" / "nmos-registry-raft" / "tests" / "operation_vectors.json"
)

# Fields that are legitimately at a default in a sample, with why. Anything
# else at a default is an oversight, and the check below says so -- which is
# the check `test_messages.py` turned out to need and not have.
_ALLOWED_DEFAULTS: dict[str, set[str]] = {
    # A no-op has nothing but its proposal, which is the point of it.
    "NoopOp": set(),
}


def _value(value: Any) -> Any:
    """One field value, in a form JSON carries and Rust can rebuild from."""
    if isinstance(value, ProposalId):
        return {"member": value.member, "sequence": value.sequence}
    if isinstance(value, TaiCursor):
        return {"seconds": value.seconds, "nanoseconds": value.nanoseconds}
    if isinstance(value, ResourceType):
        return value.value
    if isinstance(value, tuple):
        return [_value(item) for item in value]
    if isinstance(value, (bool, int, str)) or value is None:
        return value
    raise SystemExit(f"no JSON form for {type(value).__name__}: {value!r}")


def _defaulted(sample: Any) -> list[str]:
    """Fields left at a value a missing field would decode to."""
    defaults = (False, 0, "", (), b"", None)
    return [
        field.name
        for field in dataclasses.fields(sample)
        if field.name != "proposal"
        and any(getattr(sample, field.name) == d for d in defaults)
    ]


def build() -> dict[str, Any]:
    """Each sample operation, as fields plus the bytes it must encode to."""
    cases = []
    samples: list[Any] = list(_SAMPLES)
    for sample in samples:
        name = type(sample).__name__
        kind = sample.KIND
        if not isinstance(kind, OpKind):
            raise SystemExit(f"{name}.KIND is not an OpKind")

        # A field at its default cannot detect a decoder that drops it: the
        # value it reads back is the same either way. `test_messages.py` has
        # thirteen such fields and two decode mutations survived its whole
        # corpus, which is why this is checked here rather than trusted.
        unexplained = set(_defaulted(sample)) - _ALLOWED_DEFAULTS.get(name, set())
        if unexplained:
            raise SystemExit(
                f"{name} leaves {sorted(unexplained)} at a default, so the "
                f"corpus cannot tell a decoder that drops them. Give the "
                f"sample a distinguishable value, or record why not in "
                f"_ALLOWED_DEFAULTS.",
            )

        cases.append({
            "operation": name,
            "kind": int(kind),
            "fields": {
                field.name: _value(getattr(sample, field.name))
                for field in dataclasses.fields(sample)
            },
            "encoded_hex": encode_operation(sample).hex(),
        })

    if len({case["operation"] for case in cases}) != len(cases):
        raise SystemExit("two cases share an operation name")

    covered = {case["kind"] for case in cases}
    missing = sorted(int(kind) for kind in OpKind if int(kind) not in covered)
    if missing:
        raise SystemExit(
            f"no sample for operation kind(s) {missing}; the Rust side would "
            f"be untested for them",
        )
    return {"cases": cases}


def main() -> None:
    corpus = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(corpus, indent=2, sort_keys=True) + "\n")
    print(f"{len(corpus['cases'])} operation vectors -> {OUTPUT}")


if __name__ == "__main__":
    main()
