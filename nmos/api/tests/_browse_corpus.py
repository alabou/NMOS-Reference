# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Record what ``_json_to_html`` renders, for the Rust browsing view to match.

The browsing view is 200 lines of fiddly string building whose output is
asserted *byte for byte* by ``test_html_links.py`` — exact ``<a href>``
targets, exact span classes, exact escaping. Hand-written Rust expectations
would only assert what someone thought to assert, and the interesting failures
live in the interactions: a UUID under a page that is itself a UUID, a resolver
declining a cross-reference, a bare ``"v1.3"`` beside a ``"v1.3/"``.

So this records the Python renderer's actual output across a fixed corpus, and
the Rust suite asserts byte equality against the recording. Regenerate with::

    python -m nmos.api.tests._browse_corpus

``test_browse_corpus.py`` fails when the recording is stale, which is the half
that matters: without it, changing the Python renderer leaves the Rust side
agreeing with what Python *used* to do, both suites green, and the two
implementations quietly divergent.

Every case is deterministic — no clocks, no randomness, no dict iteration that
is not already document order — so the comparison can be exact.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nmos.api.response import _json_to_html
from nmos.registry.links import make_link_resolver

OUTPUT = (
    Path(__file__).resolve().parents[3]
    / "rust"
    / "crates"
    / "nmos-registry-http"
    / "tests"
    / "browse_cases.json"
)

QUERY_BASE = "/x-nmos/query/v1.3"
SENDERS = f"{QUERY_BASE}/senders/"
SOURCES = f"{QUERY_BASE}/sources/"
NODES = f"{QUERY_BASE}/nodes/"

UUID_A = "3b8be755-08ff-452b-b217-c9151eb21193"
UUID_B = "58f6b536-ca4c-43fd-880a-9df2501fc125"
UUID_UPPER = "3B8BE755-08FF-452B-B217-C9151EB21193"

#: ``(name, json_text, request_path, use_resolver)``.
#:
#: Grouped by the rule each case exercises, because a failure names the case
#: and the name is the only explanation the Rust side gets.
CASES: list[tuple[str, str, str, bool]] = [
    # --- shapes ---------------------------------------------------------
    ("empty_object", "{}", QUERY_BASE, False),
    ("empty_array", "[]", QUERY_BASE, False),
    ("empty_nested", '{"a":{},"b":[]}', QUERY_BASE, False),
    ("scalars", '{"s":"x","i":1,"f":1.5,"t":true,"f2":false,"n":null}', QUERY_BASE, False),
    ("key_order_reversed", '{"zulu":1,"mike":2,"alpha":3}', QUERY_BASE, False),
    ("nested_deep", '{"a":{"b":{"c":[1,[2,[3]]]}}}', QUERY_BASE, False),
    ("array_of_objects", '[{"id":"a"},{"id":"b"}]', SENDERS, False),
    ("single_entry_no_comma", '{"only":1}', QUERY_BASE, False),
    # --- numbers, which go through str() not json.dumps() ---------------
    ("int_stays_int", '{"port":8080}', QUERY_BASE, False),
    ("float_keeps_point", '{"a":1.0}', QUERY_BASE, False),
    ("float_exponent_small", '{"a":1e-05}', QUERY_BASE, False),
    ("float_exponent_large", '{"a":1e+20}', QUERY_BASE, False),
    ("float_six_sig_digits", '{"a":1234567.8}', QUERY_BASE, False),
    ("negative_and_zero", '{"a":-1,"b":0,"c":-0.5}', QUERY_BASE, False),
    ("big_integer", '{"a":9007199254740993}', QUERY_BASE, False),
    # --- escaping -------------------------------------------------------
    ("markup_in_value", '{"a":"<script>alert(1)</script>"}', QUERY_BASE, False),
    ("markup_in_key", '{"<k>":"v"}', QUERY_BASE, False),
    ("apostrophe", '{"a":"it\'s"}', QUERY_BASE, False),
    ("ampersand", '{"a":"a&b"}', QUERY_BASE, False),
    ("quotes_in_value", '{"a":"say \\"hi\\""}', QUERY_BASE, False),
    ("backslash", '{"a":"a\\\\b"}', QUERY_BASE, False),
    ("non_ascii", '{"label":"caf\\u00e9"}', QUERY_BASE, False),
    ("non_ascii_literal", '{"label":"caf\u00e9"}', QUERY_BASE, False),
    ("cjk", '{"label":"\u65e5\u672c\u8a9e"}', QUERY_BASE, False),
    ("emoji", '{"label":"\U0001f600"}', QUERY_BASE, False),
    ("control_chars", '{"a":"x\\ny\\tz"}', QUERY_BASE, False),
    ("markup_in_path", "{}", "/x-nmos/<script>", False),
    ("apostrophe_in_path", "{}", "/x-nmos/it's", False),
    # --- links: absolute ------------------------------------------------
    ("absolute_http", f'{{"href":"http://example.test/a"}}', SENDERS, False),
    ("absolute_https", f'{{"href":"https://example.test/a"}}', SENDERS, False),
    ("absolute_ws", f'{{"href":"ws://example.test/x"}}', SENDERS, False),
    ("absolute_with_query", '{"href":"https://e.test/a?b=c&d=e"}', SENDERS, False),
    ("absolute_plus_scheme", '{"href":"a+b://e.test/"}', SENDERS, False),
    # --- links: UUIDs ---------------------------------------------------
    ("uuid_in_collection", f'{{"id":"{UUID_A}"}}', SENDERS, False),
    ("uuid_under_a_resource_page", f'{{"id":"{UUID_A}"}}', f"{SENDERS}{UUID_A}", False),
    ("uuid_with_trailing_slash", f'{{"id":"{UUID_A}/"}}', SENDERS, False),
    ("uuid_uppercase", f'{{"id":"{UUID_UPPER}"}}', SENDERS, False),
    ("uuid_array_inherits_key", f'{{"parents":["{UUID_A}","{UUID_B}"]}}', SOURCES, False),
    ("uuid_at_document_root", f'["{UUID_A}"]', SENDERS, False),
    ("not_quite_a_uuid", '{"id":"3b8be755-08ff-452b-b217-c9151eb2119"}', SENDERS, False),
    # --- links: relative ------------------------------------------------
    ("relative_collection", '{"a":"nodes/"}', QUERY_BASE, False),
    ("relative_nested", '{"a":"nodes/senders/"}', QUERY_BASE, False),
    ("bare_version_is_data", '{"versions":["v1.3","v1.2"]}', NODES, False),
    ("version_index_is_a_link", '{"a":"v1.3/"}', "/x-nmos/query", False),
    ("unknown_segment_is_not_linked", '{"a":"nodes/wat/"}', QUERY_BASE, False),
    ("root_relative_x_nmos", '{"a":"/x-nmos/query/v1.3/"}', QUERY_BASE, False),
    ("root_relative_x_manufacturer", '{"a":"/x-manufacturer/exclusive/"}', QUERY_BASE, False),
    ("root_relative_other_is_not_linked", '{"a":"/etc/passwd"}', QUERY_BASE, False),
    ("plain_label_is_not_linked", '{"label":"my sender"}', SENDERS, False),
    ("registration_ladder", '{"a":"registration/"}', "/x-nmos", False),
    # --- the resolver ---------------------------------------------------
    ("resolver_maps_flow_id", f'{{"flow_id":"{UUID_A}"}}', SENDERS, True),
    ("resolver_maps_device_id", f'{{"device_id":"{UUID_A}"}}', SENDERS, True),
    ("resolver_declines_unknown_reference", f'{{"monitor_sibling_id":"{UUID_A}"}}', SOURCES, True),
    ("resolver_does_not_shadow_absolute", '{"href":"https://e.test/"}', SENDERS, True),
    ("resolver_with_own_id", f'{{"id":"{UUID_A}"}}', SENDERS, True),
    # --- malformed ------------------------------------------------------
    ("not_json", "not json at all <b>", QUERY_BASE, False),
    ("truncated_json", '{"a":', QUERY_BASE, False),
    ("bare_string_document", '"hello"', QUERY_BASE, False),
    ("bare_number_document", "42", QUERY_BASE, False),
    ("bare_null_document", "null", QUERY_BASE, False),
    # --- paths ----------------------------------------------------------
    ("path_without_trailing_slash", f'{{"id":"{UUID_A}"}}', QUERY_BASE, False),
    ("path_with_double_slash", '{"a":"nodes/"}', f"{QUERY_BASE}//", False),
    ("path_root", '{"a":"x-nmos/"}', "/", False),
]


def build() -> dict[str, Any]:
    cases = []
    for name, json_text, request_path, use_resolver in CASES:
        resolver = (
            make_link_resolver(request_path, QUERY_BASE) if use_resolver else None
        )
        cases.append(
            {
                "name": name,
                "json": json_text,
                "path": request_path,
                "resolver": use_resolver,
                "html": _json_to_html(json_text, request_path, resolver),
            },
        )
    return {"api_base": QUERY_BASE, "cases": cases}


def main() -> None:
    corpus = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(corpus, indent=1, sort_keys=True) + "\n")
    print(f"{len(corpus['cases'])} cases -> {OUTPUT}")


if __name__ == "__main__":
    main()
