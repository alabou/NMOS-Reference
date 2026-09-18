# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Keep the Rust store corpus honest.

``rust/crates/nmos-registry-core/tests/store_cases.json`` records what this
store did across a 400-step randomised sequence, and the Rust suite asserts its
own store reaches the same answers. That only means anything while the
recording is current.

Without this test the failure is silent and the wrong way round: change the
Python store, and the Rust side keeps agreeing with what Python *used* to do.
Both suites stay green and the two implementations diverge precisely because
the check that was supposed to catch it is comparing against a stale answer.

The corpus is deliberately deterministic -- authoritative cursors, explicit
health, no clock reads -- so this can compare it outright rather than
approximately.
"""

from __future__ import annotations

import json

import pytest

from nmos.registry.tests._store_corpus import OUTPUT, build


def _committed() -> dict[str, object]:
    if not OUTPUT.exists():
        pytest.fail(
            f"{OUTPUT} is missing.\n"
            f"  python -m nmos.registry.tests._store_corpus",
        )
    return dict(json.loads(OUTPUT.read_text()))


def test_the_committed_corpus_matches_what_python_does_now() -> None:
    """Fails the moment the store's behaviour changes without a rebuild."""
    fresh = build()
    committed = _committed()

    fresh_steps = fresh["steps"]
    committed_steps = committed.get("steps", [])
    assert len(fresh_steps) == len(committed_steps), (
        f"the corpus has {len(committed_steps)} steps but the generator now "
        f"produces {len(fresh_steps)}\n\n"
        f"  python -m nmos.registry.tests._store_corpus"
    )

    drifted: list[str] = []
    for number, (new, old) in enumerate(zip(fresh_steps, committed_steps)):
        if new["op"] != old["op"] or new["id"] != old["id"]:
            drifted.append(
                f"step {number}: the sequence itself changed "
                f"({old['op']} {old['id']} -> {new['op']} {new['id']})",
            )
        elif new["outcome"] != old["outcome"]:
            drifted.append(
                f"step {number} ({new['op']} {new['resource_type']}): "
                f"committed {old['outcome']} but python now says {new['outcome']}",
            )
        elif new["state"]["ordered"] != old["state"]["ordered"]:
            drifted.append(
                f"step {number} ({new['op']}): the cursor-ordered view changed",
            )
        elif new["state"]["pages"] != old["state"]["pages"]:
            drifted.append(
                f"step {number} ({new['op']}): a page or its headers changed",
            )
        elif new["state"]["statistics"] != old["state"]["statistics"]:
            drifted.append(
                f"step {number} ({new['op']}): the statistics changed",
            )
        if len(drifted) >= 5:
            break

    assert not drifted, (
        "the Rust store corpus is stale -- the Rust tests are agreeing with a "
        "Python that no longer exists:\n  "
        + "\n  ".join(drifted)
        + "\n\n  python -m nmos.registry.tests._store_corpus"
    )


def test_the_corpus_is_reproducible() -> None:
    """Guard the guard.

    The comparison above is only meaningful because the generator is
    deterministic. It reads no clock: cursors are supplied authoritatively and
    health is set explicitly, which is also why the Rust side can replay the
    sequence at all. If a clock read crept in, this test would start failing
    intermittently rather than the drift guard failing always -- so it is
    asserted directly.
    """
    assert build() == build(), (
        "the corpus generator is no longer deterministic; something in the "
        "sequence is reading a clock or iterating an unordered container"
    )


def test_the_corpus_still_exercises_the_interesting_paths() -> None:
    """Guard the guard.

    A sequence that only ever registers would compare a great deal of nothing.
    These are the interactions the fuzz exists to reach -- the ones the
    hand-written tests each cover in isolation and never in combination.
    """
    steps = _committed()["steps"]
    assert isinstance(steps, list)

    kinds: dict[str, int] = {}
    for step in steps:
        kinds[step["op"]] = kinds.get(step["op"], 0) + 1
    for operation in ("register", "delete", "forget", "remove_one", "set_health"):
        assert kinds.get(operation, 0) > 10, f"only {kinds.get(operation, 0)} {operation}"

    # More than one reason for a refusal, or the 400 paths are barely covered.
    refusals = {
        step["outcome"].get("error")
        for step in steps
        if step["op"] == "register" and not step["outcome"]["ok"]
    }
    assert len(refusals) >= 3, f"only {refusals} refusal reasons"

    # A cascade, which is where child ordering and the revive rules meet.
    cascades = sum(
        1 for step in steps
        if step["op"] == "delete" and len(step["outcome"]["removed"]) > 1
    )
    assert cascades > 0, "no delete ever cascaded"

    # And pages that actually contain records.
    with_records = sum(
        1 for step in steps for page in step["state"]["pages"] if page["ids"]
    )
    assert with_records > 50, f"only {with_records} non-empty pages"


def test_the_cascade_order_is_deterministic() -> None:
    """The defect this corpus found, pinned.

    ``_erase_subtree`` walked ``self._children`` -- a ``set`` -- so sibling
    removal grains came out in *hash* order. Two cluster members deleting the
    same Node published them in different orders, and so did one member across
    a restart, because ``PYTHONHASHSEED`` is randomised by default. Measured on
    this very sequence: seed 2 produced ``[...64, ...69]`` where seeds 1, 3 and
    4 produced ``[...69, ...64]``.

    Only children-before-parent is required by the protocol, so this was never
    wrong for a single client -- it made the grain stream unreproducible, which
    is what ``subtree`` sorts to avoid and what a mixed-implementation cluster
    test would report as a phantom.
    """
    steps = _committed()["steps"]
    assert isinstance(steps, list)

    cascades = [
        step for step in steps
        if step["op"] == "delete" and len(step["outcome"]["removed"]) > 1
    ]
    assert cascades, "no cascade recorded, so this asserts nothing"

    for step in cascades:
        removed = step["outcome"]["removed"]
        # The parent goes last; everything before it is a descendant.
        descendants = removed[:-1]
        # Siblings of one parent are contiguous and ascending. Checking the
        # whole prefix is sorted is stronger than the protocol requires and is
        # exactly what determinism means here.
        assert descendants == sorted(descendants), (
            f"cascade removals are not in id order: {removed}"
        )
