# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Record what ``decode_post_envelope`` accepts, rejects and says.

``handlers_registration.py:158`` puts ``str(exc)`` straight into the 400 body,
so every message here is an observable part of the API — including the span
scanner's own wording, which Python interpolates verbatim. That last part is the
easy one to get wrong: reaching for the JSON parser's error instead produces a
different sentence for the same request, and nothing else notices.

This records, per case, whether the envelope was accepted, the resource type it
named, the **exact bytes** the registry would store, and the failure message.
Regenerate with::

    python -m nmos.registry.tests._envelope_corpus

``test_envelope_corpus.py`` fails when the recording is stale.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nmos.registry.decode import DecodeFailure, decode_post_envelope

OUTPUT = (
    Path(__file__).resolve().parents[3]
    / "rust"
    / "crates"
    / "nmos-registry"
    / "tests"
    / "envelope_cases.json"
)

NODE = json.dumps(
    {
        "id": "3b8be755-08ff-452b-b217-c9151eb21193",
        "version": "1600000000:0",
        "label": "n",
        "description": "",
        "tags": {},
        "href": "http://example.test/",
        "hostname": "example",
        "caps": {},
        "api": {"versions": ["v1.3"], "endpoints": []},
        "services": [],
        "clocks": [],
        "interfaces": [],
    },
)

DEVICE = json.dumps(
    {
        "id": "58f6b536-ca4c-43fd-880a-9df2501fc125",
        "version": "1600000000:0",
        "label": "d",
        "description": "",
        "tags": {},
        "type": "urn:x-nmos:device:generic",
        "node_id": "3b8be755-08ff-452b-b217-c9151eb21193",
        "senders": [],
        "receivers": [],
        "controls": [],
    },
)

#: ``(name, source)``. Grouped by the rule each one exercises.
CASES: list[tuple[str, str]] = [
    # --- accepted -------------------------------------------------------
    ("valid_node", f'{{"type":"node","data":{NODE}}}'),
    ("valid_device", f'{{"type":"device","data":{DEVICE}}}'),
    ("members_reordered", f'{{"data":{NODE},"type":"node"}}'),
    ("extra_envelope_member", f'{{"type":"node","data":{NODE},"extra":1}}'),
    ("whitespace_everywhere", f'{{ "type" : "node" , "data" : {NODE} }}'),
    # Spellings a parse normalises irreversibly — these must survive as bytes.
    (
        "escaped_ascii_survives",
        '{"type":"node","data":'
        + NODE.replace('"label": "n"', '"label": "\\u0041"')
        + "}",
    ),
    (
        "exponent_survives",
        '{"type":"node","data":'
        + NODE.replace('"label": "n"', '"label": "n", "x-exp": 1e3')
        + "}",
    ),
    (
        "non_ascii_survives",
        '{"type":"node","data":'
        + NODE.replace('"label": "n"', '"label": "café"')
        + "}",
    ),
    # --- not JSON at all -------------------------------------------------
    ("not_json", "not json"),
    ("empty_body", ""),
    ("truncated_object", '{"type":"node"'),
    ("trailing_garbage", '{"type":"node","data":{}} trailing'),
    ("single_quotes", "{'type':'node'}"),
    ("leading_zero_number", '{"type":"node","data":{"a":01}}'),
    # --- valid JSON, wrong shape ----------------------------------------
    ("array_document", "[]"),
    ("string_document", '"a string"'),
    ("number_document", "42"),
    ("null_document", "null"),
    ("bool_document", "true"),
    # --- the type member -------------------------------------------------
    ("type_missing", '{"data":{}}'),
    ("type_is_number", '{"type":42,"data":{}}'),
    ("type_is_null", '{"type":null,"data":{}}'),
    ("type_is_array", '{"type":["node"],"data":{}}'),
    ("type_is_object", '{"type":{},"data":{}}'),
    ("type_unknown", '{"type":"widget","data":{}}'),
    ("type_plural", '{"type":"nodes","data":{}}'),
    ("type_empty", '{"type":"","data":{}}'),
    ("type_wrong_case", '{"type":"Node","data":{}}'),
    # --- the data member -------------------------------------------------
    ("data_missing", '{"type":"node"}'),
    ("data_is_array", '{"type":"node","data":[]}'),
    ("data_is_string", '{"type":"node","data":"x"}'),
    ("data_is_null", '{"type":"node","data":null}'),
    ("data_is_number", '{"type":"node","data":42}'),
    ("data_is_bool", '{"type":"node","data":false}'),
    # --- validation, one per resource type -------------------------------
    ("node_empty", '{"type":"node","data":{}}'),
    ("device_empty", '{"type":"device","data":{}}'),
    ("source_empty", '{"type":"source","data":{}}'),
    ("flow_empty", '{"type":"flow","data":{}}'),
    ("sender_empty", '{"type":"sender","data":{}}'),
    ("receiver_empty", '{"type":"receiver","data":{}}'),
    ("node_bad_uuid", '{"type":"node","data":{"id":"not-a-uuid"}}'),
    (
        "node_bad_version",
        '{"type":"node","data":{"id":"3b8be755-08ff-452b-b217-c9151eb21193",'
        '"version":"nope"}}',
    ),
    (
        "node_missing_label",
        '{"type":"node","data":{"id":"3b8be755-08ff-452b-b217-c9151eb21193",'
        '"version":"1600000000:0"}}',
    ),
]


def build() -> dict[str, Any]:
    cases = []
    for name, source in CASES:
        entry: dict[str, Any] = {"name": name, "source": source}
        try:
            resource_type, body = decode_post_envelope(source)
        except DecodeFailure as exc:
            entry["ok"] = False
            entry["error"] = str(exc)
        else:
            entry["ok"] = True
            entry["type"] = resource_type.value
            entry["stored"] = body.text
        cases.append(entry)
    return {"cases": cases}


def main() -> None:
    corpus = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(corpus, indent=1, sort_keys=True) + "\n")
    accepted = sum(1 for case in corpus["cases"] if case["ok"])
    print(
        f"{len(corpus['cases'])} cases "
        f"({accepted} accepted, {len(corpus['cases']) - accepted} rejected) "
        f"-> {OUTPUT}",
    )


if __name__ == "__main__":
    main()
