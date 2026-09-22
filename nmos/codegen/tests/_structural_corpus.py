# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Generate the structural decode corpus: mutate every position, not just the top.

``_decode_corpus.py`` mutates a resource's top-level keys. That reaches the six
resource types and stops there, which leaves most of the generated tree
untested: an endpoint inside ``api``, a component inside ``components``, a
channel, a clock, an interface and a constraint set are all separate generated
types with their own decode paths, their own required-member checks and their
own assertions, and a top-level mutation never enters any of them.

It also never built a Receiver at all.

So this walks each body to every position a value can occupy -- through objects,
through array elements, to any depth -- and mutates there. The nested types stop
being reachable only by the happy path.

What each mutation is for
-------------------------
* **delete** -- required-member checks fire in *descriptor* order, and inside a
  nested type that order is the nested type's own. Which member gets named is
  the assertion.
* **null** -- the null rules are per base type and are the fidelity quirk most
  likely to be "cleaned up": a null string is dropped and stays undefined, a
  null URL becomes a defined empty string, and a null anything-else is an error.
* **wrong type** -- the message renders a *Python* type name (``int``,
  ``NoneType``, ``list``), which Rust has to reproduce from its own values.
* **trailing newline** -- the reason ``nmos/validators.py`` moved from ``$`` to
  ``\\Z`` during this port. Python's ``$`` matched before a final newline, so
  every pattern-checked string accepted one and it reached a 201 ``Location``
  header. Every string position is probed, because which ones are
  pattern-checked is exactly what is being asserted rather than assumed.
* **emptied container** -- an empty array or object passes a type check and then
  meets whatever assertion counts its contents, which is a different code path
  from a missing member.

Bodies are chosen to be rich rather than minimal: the fixtures are deliberately
small, so a Node with no services reaches no service type. The variants here
populate the optional members precisely so their types get walked into.

Regenerate with::

    python -m nmos.codegen.tests._structural_corpus
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
    make_receiver,
    make_sender,
    make_source,
)
from nmos.registry.types import ResourceType

OUTPUT = (
    Path(__file__).parent.parent.parent.parent
    / "rust" / "crates" / "nmos-types" / "tests" / "structural_cases.json"
)

# As in `_encode_corpus`: a fixed version keeps the corpus stable across runs,
# so its drift guard compares behaviour rather than the clock.
FIXED_VERSION = "1700000000:123456789"

Path_ = tuple[str | int, ...]


def _bodies() -> list[tuple[str, ResourceType, dict[str, Any]]]:
    """One rich body per resource type, plus the minimal ones.

    The minimal fixtures are what the registry tests use, so they stay. The
    rich variants exist to make the nested types reachable: a Node with no
    services never enters `NNodeService`, and no top-level mutation can put it
    there.
    """
    return [
        ("node", ResourceType.NODE, make_node()),
        (
            "node_rich",
            ResourceType.NODE,
            make_node(
                services=[
                    {"href": "http://192.0.2.1:8080/x/", "type": "urn:x-nmos:service:x"},
                ],
                clocks=[
                    {"name": "clk0", "ref_type": "internal"},
                    {
                        "name": "clk1",
                        "ref_type": "ptp",
                        "traceable": True,
                        "version": "IEEE1588-2008",
                        "gmid": "08-00-11-ff-fe-21-e1-b0",
                        "locked": True,
                    },
                ],
                interfaces=[
                    {
                        "name": "eth0",
                        "chassis_id": "74-26-96-b4-b8-40",
                        "port_id": "74-26-96-b4-b8-41",
                        "attached_network_device": {
                            "chassis_id": "74-26-96-00-00-01",
                            "port_id": "74-26-96-00-00-02",
                        },
                    },
                ],
                tags={"urn:x-nmos:tag:grouphint/v1.0": ["group", "role"]},
            ),
        ),
        ("device", ResourceType.DEVICE, make_device()),
        ("source", ResourceType.SOURCE, make_source()),
        (
            "source_audio",
            ResourceType.SOURCE,
            make_source(
                format="urn:x-nmos:format:audio",
                channels=[
                    {"label": "Left", "symbol": "L"},
                    {"label": "Right", "symbol": "R"},
                ],
            ),
        ),
        ("flow", ResourceType.FLOW, make_flow()),
        ("sender", ResourceType.SENDER, make_sender()),
        (
            "sender_subscribed",
            ResourceType.SENDER,
            make_sender(
                subscription={
                    "receiver_id": "8a4d1c0e-6f3b-4a1e-9a2c-1f5b7d3e9c02",
                    "active": True,
                },
            ),
        ),
        # The decode corpus has no Receiver at all, so every Receiver type and
        # every capability type below it is currently reached by nothing.
        ("receiver", ResourceType.RECEIVER, make_receiver()),
        (
            "receiver_caps",
            ResourceType.RECEIVER,
            make_receiver(
                caps={
                    "media_types": ["video/raw"],
                    "constraint_sets": [
                        {
                            "urn:x-nmos:cap:meta:label": "1080i50",
                            "urn:x-nmos:cap:format:grain_rate": {
                                "enum": [{"numerator": 25, "denominator": 1}],
                            },
                            "urn:x-nmos:cap:format:frame_width": {"enum": [1920]},
                            "urn:x-nmos:cap:transport:bit_rate": {
                                "minimum": 1000,
                                "maximum": 25000000,
                            },
                        },
                    ],
                },
            ),
        ),
    ]


def _positions(value: Any, prefix: Path_ = ()) -> list[Path_]:
    """Every position in a document that holds a value, depth first.

    Array indices are included as positions in their own right, which is what
    takes the walk into the element types -- a component, an endpoint, a
    constraint set -- rather than stopping at the array.
    """
    found: list[Path_] = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.append((*prefix, key))
            found.extend(_positions(item, (*prefix, key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.append((*prefix, index))
            found.extend(_positions(item, (*prefix, index)))
    return found


def _read(body: Any, path: Path_) -> Any:
    for step in path:
        body = body[step]
    return body


def _write(body: Any, path: Path_, value: Any) -> None:
    for step in path[:-1]:
        body = body[step]
    body[path[-1]] = value


def _remove(body: Any, path: Path_) -> None:
    for step in path[:-1]:
        body = body[step]
    if isinstance(body, list):
        body.pop(int(path[-1]))
    else:
        del body[path[-1]]


def _label(path: Path_) -> str:
    return ".".join(str(step) for step in path)


def _mutations_at(body: dict[str, Any], path: Path_) -> list[tuple[str, Any]]:
    """The mutations worth making at one position, given what is there."""
    current = _read(body, path)
    where = _label(path)

    out: list[tuple[str, Any]] = []

    missing = copy.deepcopy(body)
    _remove(missing, path)
    out.append((f"delete:{where}", missing))

    nulled = copy.deepcopy(body)
    _write(nulled, path, None)
    out.append((f"null:{where}", nulled))

    # A type the position cannot hold. Picking it by what IS there keeps the
    # mutation meaningful: replacing a string with another string proves
    # nothing about the type check.
    wrong: Any = "not-the-right-type" if not isinstance(current, str) else 12345
    retyped = copy.deepcopy(body)
    _write(retyped, path, wrong)
    out.append((f"retype:{where}", retyped))

    if isinstance(current, str):
        # The `$` versus `\Z` case. Probed at every string position because
        # which are pattern-checked is the thing being asserted.
        newlined = copy.deepcopy(body)
        _write(newlined, path, current + "\n")
        out.append((f"newline:{where}", newlined))

    if isinstance(current, (list, dict)) and current:
        emptied = copy.deepcopy(body)
        _write(emptied, path, [] if isinstance(current, list) else {})
        out.append((f"empty:{where}", emptied))

    return out


def run_one(resource_type: ResourceType, body: Any) -> dict[str, Any]:
    """Decode one body and record exactly what Python decided."""
    try:
        decode_resource(resource_type, body)
    except NmosError as exc:
        return {"ok": False, "kind": type(exc).__name__, "message": exc.msg or str(exc)}
    except Exception as exc:  # noqa: BLE001 - DecodeFailure and friends
        return {"ok": False, "kind": type(exc).__name__, "message": str(exc)}
    return {"ok": True}


def build() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for name, resource_type, raw in _bodies():
        body = {**raw, "version": FIXED_VERSION}
        for label, mutated in (
            (f"{name}:valid", body),
            *(
                (f"{name}:{label}", mutated)
                for path in _positions(body)
                for label, mutated in _mutations_at(body, path)
            ),
        ):
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
    depth = max(len(c["label"].split(":")[-1].split(".")) for c in cases)
    print(
        f"{len(cases)} structural cases ({rejected} rejected, "
        f"deepest path {depth} levels) -> {OUTPUT}",
    )


if __name__ == "__main__":
    main()
