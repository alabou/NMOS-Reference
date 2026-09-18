# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Generate the encode parity corpus: the bytes a grain carries.

The decode corpus proves the two type trees agree on *what to accept*. This
proves they agree on *what to write*, which is the other half and the one with
a client on the far end of it.

Why this is the byte-level gate and not a structural one
--------------------------------------------------------
``build_grain`` (``nmos/registry/subscriptions.py``) is the registry's only
call to ``JsonEngine().encode()``. Everything else on the wire is either a
stored body spliced verbatim or a synthesised dict going through ``dump_any``.
So this path is exactly where a generated type's encoding becomes something a
subscriber parses, and a difference in it is a difference in the protocol --
not a formatting preference.

Comparing parsed structures would miss most of what can go wrong here, because
the interesting failures all survive a round trip:

* **member order.** Python writes in *descriptor* order. Rust's derived
  ``Serialize`` writes in struct declaration order, which the emitter renders
  from the same descriptors -- but ``#[serde(flatten)]`` on the 11 embedded
  types goes through a map serializer, and whether that preserves position is
  a property of serde, not of the model.
* **applied defaults.** Decoding injects members the body never carried:
  ``urn:x-nmos:cap:meta:enabled``, ``transfer_characteristic``, a Rational's
  ``denominator``. They are marked defined, so they are written out. An
  implementation that skipped them would still parse identically.
* **float spelling.** ``12345678.0`` must not become ``12345678``, ``1e+16``
  must not become ``1e16``. Both read back as the same double, so only a byte
  comparison sees it.
* **null asymmetry.** A URL given as ``null`` decodes to a defined empty string
  and re-encodes as ``null``; a plain string given as ``null`` is dropped and
  does not re-encode at all.
* **escaping.** Non-ASCII goes out raw and control characters escaped, and the
  set of characters each language chooses to escape is not identical by
  default.

Cases
-----
The six registry fixtures, plus variants chosen to reach the paths above:
optional members present and absent, floats across the spelling boundaries,
unicode and escapes, empty and populated containers, nulls, and the polymorphic
branches a Flow and a Source can take.

Rejected bodies are not recorded -- there is nothing to encode -- but they are
counted, so a variant that silently stops being a valid resource shows up as a
drop in coverage rather than as a case that quietly disappeared.

Regenerate with::

    python -m nmos.codegen.tests._encode_corpus
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nmos.json.engine import JsonEngine
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
    / "rust" / "crates" / "nmos-types" / "tests" / "encode_cases.json"
)

# The fixtures stamp `version` from the clock, which is right for a registry
# test and wrong here: the recorded bytes would differ on every regeneration,
# so the corpus would churn in git and its drift guard could never compare
# encodings at all. A fixed TAI timestamp is as valid as any other -- what this
# corpus is about is the *encoding*, not the instant.
FIXED_VERSION = "1700000000:123456789"

# Floats chosen to straddle every boundary where the two languages spell the
# same double differently. `_float_corpus.py` covers the formatter in isolation;
# these prove the formatter is actually reached through a generated type.
FLOAT_PROBES: list[float] = [
    0.0,
    1.0,
    1.5,
    0.1,
    1000000.0,        # integral: keeps `.0` in Python, loses it in Rust's `{}`
    12345678.0,       # the value `%g` used to truncate to 12345700
    1234567890.0,
    3.141592653589793,
    1e15,             # last fixed-notation magnitude
    1e16,             # first exponent-notation magnitude
    1e-4,             # last fixed-notation small
    1e-5,             # first exponent-notation small: `1e-05`, not `1e-5`
    1e20,
    1e308,
    5e-324,
    -2.5,
    -0.0,             # the sign survives
]


def _constraint_caps(*bounds: float) -> dict[str, Any]:
    """Receiver caps carrying floats, which is where floats reach the wire.

    Every float in NMOS is a capability constraint bound, so this is not a
    contrived shape -- it is the only shape.
    """
    return {
        "media_types": ["video/raw"],
        "constraint_sets": [
            {
                "urn:x-nmos:cap:transport:bit_rate": {
                    "minimum": bounds[0],
                    "maximum": bounds[-1],
                },
            },
        ],
    }


def _coded_flow() -> dict[str, Any]:
    """A coded video Flow: the second branch of the Flow polymorphic chain.

    Dispatch is on ``format`` *and* ``media_type``: ``NFlowVideoRaw`` needs
    ``media_type == video/raw`` and ``NFlowVideoCoded`` needs it to be anything
    else, so naming a coded media type is what selects this branch.

    ``components`` stays because a coded video Flow must carry it: BCP-006-01,
    -02 and -03 all require it at IS-04 v1.3, and the AMWA suites fail a Node
    that omits it. The core ``flow_video_coded.json`` asks only for
    ``media_type``, which is why this looks like extra strictness when the
    schema is read on its own -- see ``test_schema_agreement.py``.
    """
    return make_flow(
        media_type="video/H264",
        bit_rate=25000,
        profile="High",
        level="4.1",
    )


def _cases() -> list[tuple[str, ResourceType, dict[str, Any]]]:
    """Every body worth encoding, with the reason it is here in its label."""
    out: list[tuple[str, ResourceType, dict[str, Any]]] = [
        # The baselines. Each exercises a different mix of embedded types,
        # arrays and defaults.
        ("node", ResourceType.NODE, make_node()),
        ("device", ResourceType.DEVICE, make_device()),
        ("source", ResourceType.SOURCE, make_source()),
        ("flow", ResourceType.FLOW, make_flow()),
        ("sender", ResourceType.SENDER, make_sender()),
        ("receiver", ResourceType.RECEIVER, make_receiver()),
    ]

    # Optional members present, where the baseline leaves them absent. The two
    # spellings of an optional member -- written, or skipped entirely -- are
    # separate encode paths.
    out += [
        (
            "node_with_services_and_clocks",
            ResourceType.NODE,
            make_node(
                services=[
                    {"href": "http://192.0.2.1:8080/x/", "type": "urn:x-nmos:service:x"},
                ],
                clocks=[{"name": "clk0", "ref_type": "internal"}],
                interfaces=[
                    {
                        "name": "eth0",
                        "chassis_id": "74-26-96-b4-b8-40",
                        "port_id": "74-26-96-b4-b8-41",
                    },
                ],
            ),
        ),
        (
            "node_with_tags",
            ResourceType.NODE,
            make_node(tags={"urn:x-nmos:tag:grouphint/v1.0": ["group", "role"]}),
        ),
        (
            "node_with_empty_tag_list",
            ResourceType.NODE,
            make_node(tags={"urn:x-nmos:tag:grouphint/v1.0": []}),
        ),
        ("flow_with_bit_depth", ResourceType.FLOW, make_flow(bit_depth=10)),
        # A coded video Flow takes the *other* polymorphic branch. `components`
        # has to go rather than be nulled: an array member rejects null, so
        # `make_flow(components=None)` would be a rejected body and would
        # silently cover nothing.
        ("flow_coded", ResourceType.FLOW, _coded_flow()),
        (
            "source_audio_channels",
            ResourceType.SOURCE,
            make_source(
                format="urn:x-nmos:format:audio",
                channels=[{"label": "Left", "symbol": "L"}, {"label": "Right", "symbol": "R"}],
            ),
        ),
        (
            "sender_active_subscription",
            ResourceType.SENDER,
            make_sender(
                subscription={
                    "receiver_id": "8a4d1c0e-6f3b-4a1e-9a2c-1f5b7d3e9c02",
                    "active": True,
                },
            ),
        ),
        (
            "receiver_constraint_sets",
            ResourceType.RECEIVER,
            make_receiver(
                caps={
                    "media_types": ["video/raw", "video/H264"],
                    "constraint_sets": [
                        {
                            "urn:x-nmos:cap:meta:label": "1080i50",
                            "urn:x-nmos:cap:format:grain_rate": {
                                "enum": [{"numerator": 25, "denominator": 1}],
                            },
                            "urn:x-nmos:cap:format:frame_width": {"enum": [1920]},
                        },
                    ],
                },
            ),
        ),
    ]

    # Nulls. Each of the three behaviours is a different encode outcome, and
    # only one of them writes `null`.
    out += [
        (
            "source_null_clock_name",
            ResourceType.SOURCE,
            make_source(clock_name=None),
        ),
        (
            "sender_null_receiver_id",
            ResourceType.SENDER,
            make_sender(subscription={"receiver_id": None, "active": False}),
        ),
    ]

    # Text that has to survive byte-for-byte. `test_03_2` of the AMWA suite
    # registers a Node containing emoji, so this is the case that suite will
    # find if it is wrong.
    for label, text in [
        ("ascii", "plain"),
        ("accented", "café"),
        ("emoji", "café \U0001f600"),
        ("quote", 'a "quoted" label'),
        ("backslash", "a\\b"),
        ("control", "line\nbreak\ttab"),
        ("solidus", "a/b"),
        ("bmp_edge", "߿ࠀ￿"),
    ]:
        out.append((f"node_label_{label}", ResourceType.NODE, make_node(label=text)))

    # Floats, one case per probe, so a failure names the value.
    for value in FLOAT_PROBES:
        out.append(
            (
                f"receiver_float_{value!r}",
                ResourceType.RECEIVER,
                make_receiver(caps=_constraint_caps(value)),
            ),
        )

    return out


def build() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for label, resource_type, body in _cases():
        body = {**body, "version": FIXED_VERSION}
        try:
            obj = decode_resource(resource_type, body)
        except Exception:  # noqa: BLE001 - a rejected variant simply has no encoding
            continue
        cases.append(
            {
                "label": label,
                "resource_type": resource_type.value,
                "body": body,
                "encoded": JsonEngine().encode(obj),
            },
        )
    return cases


def main() -> None:
    cases = build()
    total = len(_cases())
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(cases, indent=2, sort_keys=True) + "\n")
    print(f"{len(cases)} of {total} bodies encoded -> {OUTPUT}")


if __name__ == "__main__":
    main()
