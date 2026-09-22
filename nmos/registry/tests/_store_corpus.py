# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Generate the store/paging differential corpus.

The unit tests on either side assert what someone thought to assert. This
records what the Python store *actually does* across a randomised operation
sequence, so the Rust port can be held to it rather than to a second reading of
the same specification.

What is compared, and why those things
--------------------------------------
After every operation, three views are recorded:

* **the ordered ids**, per ``(type, order)``. This is what a Query pages over,
  and it is where the index redesign could silently diverge -- a phantom entry
  or a missed reposition shows up here and in almost nothing else;
* **page contents**, for a fixed set of paging queries. The window arithmetic
  has nine worked examples and a great many unworked ones, and a randomised
  sequence reaches the unworked ones;
* **the ``X-Paging-*`` and ``Link`` headers**, as strings. Byte equality,
  because a client string-matches the ``prev`` link against the header it was
  handed -- the percent-encoding of a colon is a real divergence, not a
  cosmetic one.

Why cursors are supplied rather than allocated
----------------------------------------------
``apply_committed`` takes authoritative ``created``/``updated`` cursors, which
is how a distributed backend makes every member page identically. The corpus
uses that path, so both implementations are driven with the same cursors and
the recorded state is comparable at all.

That is not a test-only contrivance: it is the same entry point
``raft_backend.py`` uses on every applied revision. Allocating locally would
make the two sides disagree on every cursor and leave nothing to compare.

Health is set explicitly for the same reason. ``heartbeat()`` reads the clock,
so a sequence containing one could not be replayed.

Regenerate with::

    python -m nmos.registry.tests._store_corpus
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from nmos.registry.paging import apply_paging, paging_headers, parse_paging
from nmos.registry.query_filter import matches
from nmos.registry.store import RegistryStore
from nmos.registry.types import (
    Body,
    RegistrationResult,
    ResourceType,
    TaiCursor,
)

OUTPUT = (
    Path(__file__).parent.parent.parent.parent
    / "rust" / "crates" / "nmos-registry-core" / "tests" / "store_cases.json"
)

SEED = 20260918
STEPS = 400

BASE_URL = "http://example.test/x-nmos/query/v1.3/nodes/"
DEFAULT_LIMIT = 10
MAX_LIMIT = 100

# The paging queries evaluated after every step. Chosen to straddle the
# boundaries the worked examples pin down: an unbounded page, both directions,
# a zero limit, a window that collapses, and a non-default order.
PAGING_QUERIES: list[dict[str, str]] = [
    {},
    {"paging.limit": "3"},
    {"paging.limit": "0"},
    {"paging.since": "0:0"},
    {"paging.since": "1000:5"},
    {"paging.until": "1000:8"},
    {"paging.since": "1000:2", "paging.until": "1000:9"},
    {"paging.since": "1000:2", "paging.until": "1000:9", "paging.limit": "2"},
    {"paging.order": "create"},
    {"paging.order": "create", "paging.limit": "4"},
]

# A couple of basic-query filters, so the "report the collection's maximum, not
# the page's" rule is exercised against a filtered set rather than only in the
# two edge cases that were written by hand.
FILTERS: list[list[tuple[str, str]]] = [
    [],
    [("label", "even")],
]


def _uuid(tag: int) -> str:
    return f"{tag:08x}-0000-4000-8000-00000000000a"


NODE_IDS = [_uuid(n) for n in range(1, 4)]
DEVICE_IDS = [_uuid(n) for n in range(10, 16)]
CHILD_IDS = [_uuid(n) for n in range(100, 112)]

CHILD_TYPES = [
    ResourceType.SOURCE,
    ResourceType.FLOW,
    ResourceType.SENDER,
    ResourceType.RECEIVER,
]


def _body(resource_id: str, version: str, parent_key: str | None,
          parent_id: str | None, label: str) -> Body:
    """A body whose text is fixed, so both sides store identical bytes.

    Built as text rather than through ``from_data`` because the fidelity
    guarantee is about bytes, and a dict would be re-serialised by each
    language's own encoder -- which is exactly the difference this corpus
    exists to detect.
    """
    parts = [
        f'"id": "{resource_id}"',
        f'"version": "{version}"',
        f'"label": "{label}"',
    ]
    if parent_key is not None and parent_id is not None:
        parts.append(f'"{parent_key}": "{parent_id}"')
    return Body("{" + ", ".join(parts) + "}")


class Script:
    """Builds a random operation sequence and runs it against a store."""

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.store = RegistryStore(gc_interval=12, forget_interval=60)
        self.operations: list[dict[str, Any]] = []
        # A monotonically increasing supply of authoritative cursors, so the
        # sequence is replayable and both sides see identical values.
        self.tick = 0

    def next_cursor(self) -> TaiCursor:
        self.tick += 1
        return TaiCursor(1000, self.tick)

    def pick_target(self) -> tuple[ResourceType, str]:
        """A resource to act on: usually one that exists, sometimes not.

        Choosing a type and an id independently -- the obvious thing -- makes
        almost every delete a miss, because the id belongs to some other type.
        Measured at 2 hits in 68 deletes, which exercised the 404 path
        thoroughly and the cascade path barely at all.

        One in six is still a deliberate miss, because "delete something that
        is not there" is a real request and its answer is part of the contract.
        """
        if self.rng.randrange(6) == 0:
            return (
                self.rng.choice(list(ResourceType)),
                self.rng.choice(NODE_IDS + DEVICE_IDS + CHILD_IDS),
            )
        live = [
            (resource_type, resource.id)
            for resource_type in ResourceType
            for resource in self.store.iter_extant(resource_type)
        ]
        if not live:
            return (ResourceType.NODE, self.rng.choice(NODE_IDS))
        live.sort(key=lambda pair: (pair[0].value, pair[1]))
        return live[self.rng.randrange(len(live))]

    def _version(self) -> str:
        """The version this registration carries.

        Ordinarily ``<tick>:0``, which is what exercises the version-regression
        rule. One registration in twenty carries a **boundary** version
        instead.

        Those matter because ``resource_core.json`` bounds the version only by
        ``^[0-9]+:[0-9]+$`` -- no ceiling on either field -- so a nanosecond
        field of ``5000000000`` is schema-valid and this implementation
        accepts it. A corpus that only ever emitted ``<tick>:0`` could not tell
        that a second implementation had quietly narrowed the accepted range,
        and one had: it refused ``0:5000000000`` with a 400 where this accepts
        it, and every one of the 400 recorded steps passed anyway.

        Chosen from the tick rather than from ``self.rng``, deliberately.
        Drawing here would consume from the same stream every other decision
        uses, shifting the whole sequence -- which it did on the first attempt,
        and the corpus lost its ``version_regression`` refusals entirely while
        still looking like a 400-step corpus. The coverage guard in
        ``store_parity.rs`` caught it; nothing else would have.

        Kept rare so the ordinary path still dominates.
        """
        if self.tick % 17 != 0:
            return f"{2000 + self.tick}:0"
        boundaries = [
            # Nanoseconds at and beyond one second. Pattern-valid, and not a
            # real instant -- which is the point: the schema permits it.
            f"{2000 + self.tick}:999999999",
            f"{2000 + self.tick}:1000000000",
            f"{2000 + self.tick}:5000000000",
            # Beyond 32 bits, which is where a narrower field gives up.
            f"{2000 + self.tick}:4294967296",
            # Leading zeros: pattern-valid, and `int()` accepts them.
            f"{2000 + self.tick}:000000001",
            f"0000{2000 + self.tick}:0",
        ]
        # Seconds still rise with the tick, so these stay ordered against the
        # ordinary versions around them and do not accidentally become
        # version-regression cases -- which would test something else.
        return boundaries[(self.tick // 17) % len(boundaries)]

    def _register(
        self, resource_type: ResourceType, resource_id: str,
        parent_id: str | None, label: str,
    ) -> dict[str, Any]:
        version = self._version()
        parent_key = {
            ResourceType.NODE: None,
            ResourceType.DEVICE: "node_id",
        }.get(resource_type, "device_id")
        body = _body(resource_id, version, parent_key, parent_id, label)
        cursor = self.next_cursor()

        prepared = self.store.prepare(resource_type, body.data)
        if isinstance(prepared, RegistrationResult):
            outcome: dict[str, Any] = {
                "ok": False, "error": prepared.error.value if prepared.error else None,
            }
        else:
            created = cursor if prepared.creates else None
            result = self.store.apply_committed(
                prepared, body, created=created, updated=cursor, health=500,
            )
            outcome = {"ok": True, "created": result.created}

        return {
            "op": "register",
            "resource_type": resource_type.value,
            "id": resource_id,
            "parent_id": parent_id,
            "label": label,
            "version": version,
            "cursor": str(cursor),
            "body": body.text,
            "outcome": outcome,
        }

    def step(self) -> dict[str, Any]:
        choice = self.rng.randrange(100)

        if choice < 45:
            kind = self.rng.randrange(3)
            if kind == 0:
                node_id = self.rng.choice(NODE_IDS)
                return self._register(
                    ResourceType.NODE, node_id, None,
                    "even" if self.tick % 2 == 0 else "odd",
                )
            if kind == 1:
                return self._register(
                    ResourceType.DEVICE,
                    self.rng.choice(DEVICE_IDS),
                    self.rng.choice(NODE_IDS),
                    "even" if self.tick % 2 == 0 else "odd",
                )
            return self._register(
                self.rng.choice(CHILD_TYPES),
                self.rng.choice(CHILD_IDS),
                self.rng.choice(DEVICE_IDS),
                "even" if self.tick % 2 == 0 else "odd",
            )

        if choice < 62:
            resource_type, resource_id = self.pick_target()
            events = self.store.delete(resource_type, resource_id)
            return {
                "op": "delete",
                "resource_type": resource_type.value,
                "id": resource_id,
                "outcome": {
                    "removed": [] if events is None else [e.resource_id for e in events],
                    "found": events is not None,
                },
            }

        if choice < 74:
            resource_type, resource_id = self.pick_target()
            event = self.store.remove_one(resource_type, resource_id)
            return {
                "op": "remove_one",
                "resource_type": resource_type.value,
                "id": resource_id,
                "outcome": {"removed": event is not None},
            }

        if choice < 86:
            resource_type, resource_id = self.pick_target()
            dropped = self.store.forget(resource_type, resource_id)
            return {
                "op": "forget",
                "resource_type": resource_type.value,
                "id": resource_id,
                "outcome": {"dropped": dropped},
            }

        # Set health explicitly rather than heartbeating, so the step is
        # replayable -- ``heartbeat`` reads the clock.
        resource_type, resource_id = self.pick_target()
        health = self.rng.choice([0, 100, 500, 900])
        found = self.store.get(
            resource_type, resource_id, include_non_extant=True,
        )
        if found is not None:
            found.health = health
        return {
            "op": "set_health",
            "resource_type": resource_type.value,
            "id": resource_id,
            "health": health,
            "outcome": {"applied": found is not None},
        }

    def observe(self) -> dict[str, Any]:
        """Everything a client could see, right now."""
        ordered: dict[str, list[str]] = {}
        for resource_type in ResourceType:
            for order in ("create", "update"):
                key = f"{resource_type.value}/{order}"
                ordered[key] = [
                    resource.id
                    for resource in self.store.iter_ordered(resource_type, order)
                ]

        # Rotated rather than exhaustive. Evaluating all forty combinations
        # after every step produced a 13 MB corpus for 400 steps, almost all of
        # it repeated Link headers. One combination per step still visits every
        # one of them several times over the run, and a divergence still
        # localises to a single step.
        pages: list[dict[str, Any]] = []
        rotation = self.tick % (len(PAGING_QUERIES) * len(FILTERS))
        chosen_query = PAGING_QUERIES[rotation % len(PAGING_QUERIES)]
        chosen_filters = FILTERS[rotation // len(PAGING_QUERIES)]
        for resource_type in (ResourceType.NODE, ResourceType.DEVICE):
            for query in (chosen_query,):
                request = parse_paging(
                    query, default_limit=DEFAULT_LIMIT, max_limit=MAX_LIMIT,
                )
                ordered_for_request = list(
                    self.store.iter_ordered(resource_type, request.order),
                )
                for filters in (chosen_filters,):
                    matched = [
                        resource for resource in ordered_for_request
                        if matches(resource.raw, filters)
                    ]
                    page = apply_paging(
                        matched, ordered_for_request, request, presorted=True,
                    )
                    headers = paging_headers(
                        page, BASE_URL, filters, request.order,
                    )
                    pages.append({
                        "resource_type": resource_type.value,
                        "query": query,
                        "filters": [list(pair) for pair in filters],
                        "ids": [resource.id for resource in page.resources],
                        "headers": headers,
                    })

        statistics = self.store.statistics()
        return {
            "ordered": ordered,
            "pages": pages,
            "statistics": {
                "total": statistics.total,
                "non_extant": statistics.non_extant,
                "most_recent_update": str(statistics.most_recent_update),
                "per_type": {
                    resource_type.value: count
                    for resource_type, count in statistics.per_type.items()
                },
            },
        }


def build() -> dict[str, Any]:
    script = Script(SEED)
    steps: list[dict[str, Any]] = []
    for _ in range(STEPS):
        operation = script.step()
        operation["state"] = script.observe()
        steps.append(operation)
    return {
        "seed": SEED,
        "base_url": BASE_URL,
        "default_limit": DEFAULT_LIMIT,
        "max_limit": MAX_LIMIT,
        "paging_queries": PAGING_QUERIES,
        "filters": [[list(pair) for pair in group] for group in FILTERS],
        "steps": steps,
    }


def main() -> None:
    corpus = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(corpus, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    operations = corpus["steps"]
    kinds: dict[str, int] = {}
    for step in operations:
        kinds[step["op"]] = kinds.get(step["op"], 0) + 1
    print(f"{len(operations)} steps -> {OUTPUT}")
    print("  " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))


if __name__ == "__main__":
    main()
