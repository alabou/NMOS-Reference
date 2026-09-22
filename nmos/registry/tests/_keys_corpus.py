# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Export the etcd key layout and envelope behaviour for the Rust port.

The same arrangement as ``nmos/raft/tests/_wire_corpus.py``, at the layer where
drift is worst. A raft frame that decodes differently fails to parse and the
link drops, loudly. An etcd **key** that is spelled differently is written
successfully and then never seen by the watcher -- the resource is in the
cluster and invisible on the member that did not write it, with nothing failing
anywhere. That is the failure this corpus exists to make impossible.

The refusals are recorded too, and for a second reason: they are what an
operator reads when a key turns out to be unreadable, and two implementations
describing the same corrupt value in different words is a support problem that
surfaces while somebody is comparing two members' logs at three in the morning.

Four kinds of case, in one list so the shared staleness guard can check it:

``namespace``
    Which configured prefixes are accepted, and the refusal for the rest.
``key``
    Every key constructor, byte for byte.
``parse``
    A key in, and what it identifies -- or nothing, or a refusal.
``envelope``
    A value in, and the fields it yields -- or a refusal. Plus the bytes an
    envelope encodes to, when it is one this implementation would write.

Regenerate with::

    python -m nmos.registry.tests._keys_corpus
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from nmos.registry.keys import ENVELOPE_VERSION, Envelope, KeyError_, Namespace
from nmos.registry.types import Body, ResourceType, TaiCursor

OUTPUT = (
    Path(__file__).resolve().parents[3]
    / "rust" / "crates" / "nmos-registry-etcd" / "tests" / "key_vectors.json"
)

# The namespace every key and parse case is built against, unless it says
# otherwise. Short and unremarkable on purpose: a prefix containing a word that
# also appears in a refusal is how an assertion matches by accident.
NS = "/nmos"

# Where an interpolated CPython exception begins. Only one refusal has one --
# ``UnicodeDecodeError``'s wording for a value that is not UTF-8 -- and the
# marker is taken from ``keys.py``'s own format string so that rewording it
# shortens the prefix here rather than silently ceasing to match.
#
# The *other* user of this same prefix, a value that is valid UTF-8 but not
# valid JSON, interpolates ``JsonSpanError``, which the Rust scanner reproduces
# exactly. So the boundary is decided per case, by whether the bytes decode,
# rather than by the format string alone -- otherwise the cases Rust CAN match
# exactly would be recorded as if it could not.
_UNICODE_INTERPOLATION = "envelope is not valid JSON: "


def _namespace_cases() -> list[dict[str, Any]]:
    """Which prefixes a namespace accepts."""
    prefixes = [
        "/nmos",
        "/a/b",
        "/x",
        "/nmos-registry",
        "/ns with space",
        "/ns'quote",
        "/ns\"double",
        "/café",
        # Refused.
        "nmos",
        "/nmos/",
        "/",
        "",
        "nmos/",
        "//",
        "/a/b/",
        " /nmos",
        "/nmos\n",
        # Refused *and* awkward to quote. The refusal interpolates `repr()`,
        # whose quote choice is Python's -- single, unless that would need
        # escaping and double would not. Without a refused prefix that reaches
        # the rule, an implementation that always double-quoted passed the
        # whole tier; found by mutation, not by reading.
        "ns'quote",
        "/ns'quote/",
        "ns\"double",
        "ns'both\"",
        "ns\nnewline",
        "ns\tab",
        "ns\\back",
        "ns\x00nul",
        "nscafé",
    ]
    cases = []
    for prefix in prefixes:
        case: dict[str, Any] = {"kind": "namespace", "name": prefix, "prefix": prefix}
        try:
            Namespace(prefix)
        except KeyError_ as exc:
            case["accepted"] = False
            case["message"] = str(exc)
        else:
            case["accepted"] = True
        cases.append(case)
    return cases


# Ids the constructors are exercised with. The registry only ever writes
# validated RFC-4122 UUIDs, so most of these are unreachable through it -- they
# are here because the constructors do not check, and "unreachable" is a claim
# worth recording rather than one worth assuming. The separator-bearing ones in
# particular produce a key that cannot be parsed back, and both implementations
# must agree that this is what happens.
_IDS = [
    "3b8be755-08ff-452b-b217-c9151eb21193",
    "n1",
    "",
    "a/b",
    "self",
    "devices",
    "café",
    "id with space",
]


def _key_cases() -> list[dict[str, Any]]:
    """Every key constructor, byte for byte."""
    namespace = Namespace(NS)
    cases: list[dict[str, Any]] = []

    def record(
        name: str, call: str, args: list[str], key: bytes, prefix: str = NS,
    ) -> None:
        # The recorded prefix is what the far side rebuilds the namespace
        # from, so a case that records the wrong one compares a key against a
        # namespace that did not produce it. Caught the hard way: the
        # alternative-namespace cases were all recorded against the default.
        if not key.startswith(prefix.encode()):
            raise SystemExit(
                f"{name}: key {key!r} was not built from prefix {prefix!r}",
            )
        cases.append({
            "kind": "key",
            "name": name,
            "prefix": prefix,
            "call": call,
            "args": args,
            "key_hex": key.hex(),
        })

    for call, value in [
        ("root", namespace.root),
        ("meta_config", namespace.meta_config),
        ("ids_root", namespace.ids_root),
        ("nodes_root", namespace.nodes_root),
    ]:
        record(call, call, [], value)

    for index, resource_id in enumerate(_IDS):
        record(f"id_claim#{index}", "id_claim", [resource_id],
               namespace.id_claim(resource_id))
        record(f"node#{index}", "node", [resource_id],
               namespace.node(resource_id))
        record(f"node_subtree#{index}", "node_subtree", [resource_id],
               namespace.node_subtree(resource_id))
        record(f"device#{index}", "device", ["n1", resource_id],
               namespace.device("n1", resource_id))
        record(f"device_subtree#{index}", "device_subtree", ["n1", resource_id],
               namespace.device_subtree("n1", resource_id))

    for resource_type in ResourceType:
        name = f"child/{resource_type.value}"
        args = [resource_type.value, "n1", "d1", "r1"]
        try:
            key = namespace.child(resource_type, "n1", "d1", "r1")
        except KeyError_ as exc:
            cases.append({
                "kind": "key",
                "name": name,
                "prefix": NS,
                "call": "child",
                "args": args,
                "message": str(exc),
            })
        else:
            record(name, "child", args, key)

    # A few more namespaces, so a prefix that is not the default cannot be
    # accidentally hard-coded on the far side.
    for prefix in ("/a/b", "/x", "/café"):
        other = Namespace(prefix)
        record(f"root@{prefix}", "root", [], other.root, prefix)
        record(f"node@{prefix}", "node", ["n1"], other.node("n1"), prefix)
        record(
            f"child@{prefix}", "child", ["sender", "n1", "d1", "s1"],
            other.child(ResourceType.SENDER, "n1", "d1", "s1"), prefix,
        )
    return cases


def _parse_inputs() -> list[tuple[str, bytes]]:
    """Keys to parse, named for the diff."""
    namespace = Namespace(NS)
    inputs: list[tuple[str, bytes]] = [
        ("node", namespace.node("n1")),
        ("device", namespace.device("n1", "d1")),
        ("meta_config", namespace.meta_config),
        ("id_claim", namespace.id_claim("n1")),
        ("root", namespace.root),
        ("nodes_root", namespace.nodes_root),
        ("ids_root", namespace.ids_root),
        ("node_subtree", namespace.node_subtree("n1")),
        ("device_subtree", namespace.device_subtree("n1", "d1")),
    ]
    for resource_type in (
        ResourceType.SOURCE, ResourceType.FLOW,
        ResourceType.SENDER, ResourceType.RECEIVER,
    ):
        inputs.append((
            f"child/{resource_type.value}",
            namespace.child(resource_type, "n1", "d1", "r1"),
        ))

    literals: list[tuple[str, str]] = [
        # Outside the namespace, including the prefix-of-the-prefix trap.
        ("outside", "/other/nodes/n1/self"),
        ("prefix_extended", "/nmosX/nodes/n1/self"),
        ("prefix_only", "/nmos"),
        ("empty", ""),
        ("slash", "/"),
        # Sections.
        ("unknown_section", "/nmos/things/x"),
        ("meta_nested", "/nmos/meta/config/extra"),
        ("meta_bare", "/nmos/meta"),
        ("ids_nested", "/nmos/ids/a/b"),
        ("ids_bare", "/nmos/ids"),
        ("section_empty", "/nmos//self"),
        # Node shape.
        ("node_no_self", "/nmos/nodes/n1"),
        ("node_wrong_leaf", "/nmos/nodes/n1/other"),
        ("node_empty_id", "/nmos/nodes//self"),
        ("nodes_bare", "/nmos/nodes"),
        # Device shape.
        ("device_wrong_middle", "/nmos/nodes/n1/gadgets/d1/self"),
        ("device_wrong_leaf", "/nmos/nodes/n1/devices/d1/other"),
        ("device_no_leaf", "/nmos/nodes/n1/devices/d1"),
        # Child shape.
        ("child_unknown_collection", "/nmos/nodes/n1/devices/d1/widgets/w1"),
        ("child_singular_collection", "/nmos/nodes/n1/devices/d1/sender/s1"),
        ("child_nodes_collection", "/nmos/nodes/n1/devices/d1/nodes/x"),
        ("child_devices_collection", "/nmos/nodes/n1/devices/d1/devices/x"),
        ("child_self_collection", "/nmos/nodes/n1/devices/d1/self/x"),
        ("child_empty_collection", "/nmos/nodes/n1/devices/d1//s1"),
        ("child_too_deep", "/nmos/nodes/n1/devices/d1/senders/s1/extra"),
        ("child_trailing_slash", "/nmos/nodes/n1/devices/d1/senders/s1/"),
        # Unicode and whitespace inside ids.
        ("node_unicode_id", "/nmos/nodes/café/self"),
        ("node_space_id", "/nmos/nodes/n 1/self"),
        # Keys whose refusal has to quote something awkward, for the same
        # reason the namespace tier carries them: the message interpolates
        # `repr()`, and its quote choice is not Rust's.
        ("quote_in_key", "/nmos/things/it's"),
        ("double_quote_in_key", "/nmos/things/say\"x\""),
        ("both_quotes_in_key", "/nmos/things/it's \"x\""),
        ("newline_in_key", "/nmos/things/a\nb"),
        ("tab_in_key", "/nmos/things/a\tb"),
        ("backslash_in_key", "/nmos/things/a\\b"),
        ("control_in_key", "/nmos/things/a\x01b"),
        ("unicode_in_refusal", "/nmos/things/café"),
    ]
    inputs.extend((name, text.encode()) for name, text in literals)
    # Not UTF-8. Python decodes with errors="replace", so this must be
    # *reported* -- with the replacement character in the message -- rather
    # than becoming a different failure.
    inputs.append(("not_utf8", b"/nmos/nodes/\xff\xfe/self"))
    inputs.append(("not_utf8_outside", b"\xff\xfe/nodes/n1/self"))
    return inputs


def _parse_cases() -> list[dict[str, Any]]:
    """What each key identifies, or the refusal."""
    namespace = Namespace(NS)
    cases = []
    for name, key in _parse_inputs():
        case: dict[str, Any] = {
            "kind": "parse",
            "name": name,
            "prefix": NS,
            "key_hex": key.hex(),
        }
        try:
            parsed = namespace.parse(key)
        except KeyError_ as exc:
            case["outcome"] = "error"
            case["message"] = str(exc)
        else:
            if parsed is None:
                case["outcome"] = "ignored"
            else:
                case["outcome"] = "parsed"
                case["resource_type"] = parsed.resource_type.value
                case["resource_id"] = parsed.resource_id
                case["node_id"] = parsed.node_id
                case["device_id"] = parsed.device_id
                case["is_node"] = parsed.is_node
                case["depth"] = parsed.depth
        cases.append(case)
    return cases


# Bodies whose spelling a re-encode would destroy, plus the ones whose content
# resembles the envelope's own punctuation -- the splice is textual, so a body
# carrying `}, "data": {` is the case that would break it.
_BODIES = [
    '{"id": "n1"}',
    '{}',
    '{"id":"n1","rate":1e3}',
    '{"id": "n1", "label": "caf\\u00e9"}',
    '{"id": "n1", "label": "café"}',
    '{"id": "n1", "n": 1.50}',
    '{"id": "n1", "n": 1000000.0}',
    '{"id": "n1", "nested": {"a": [1, 2, {"b": null}]}}',
    '{"id": "n1", "label": "}, \\"data\\": {\\"id\\": \\"spoof\\"}"}',
    '{"id": "n1", "label": "a\\nb\\tc"}',
    '{ "id" : "n1" , "spaced" : true }',
    '{"id": "n1", "dup": 1, "dup": 2}',
    '{"id": "n1", "emoji": "\U0001f600"}',
]

_CURSORS = [
    TaiCursor(0, 0),
    TaiCursor(10, 20),
    TaiCursor(1, 999999999),
    TaiCursor(3913056000, 123456789),
]

_HEALTHS = [0, 1, 1700000000, -1, 9223372036854775807]


def _encode_cases() -> list[dict[str, Any]]:
    """The bytes an envelope this implementation would write encodes to."""
    cases = []
    rng = random.Random(20260920)
    for index, body_text in enumerate(_BODIES):
        resource_type = list(ResourceType)[index % len(ResourceType)]
        created = _CURSORS[index % len(_CURSORS)]
        updated = _CURSORS[(index + 1) % len(_CURSORS)]
        health = _HEALTHS[index % len(_HEALTHS)]
        envelope = Envelope(
            version=ENVELOPE_VERSION,
            resource_type=resource_type,
            body=Body(body_text),
            created=created,
            updated=updated,
            health=health,
        )
        encoded = envelope.encode()
        cases.append({
            "kind": "encode",
            "name": f"body#{index}",
            "version": ENVELOPE_VERSION,
            "resource_type": resource_type.value,
            "body_text": body_text,
            "created": str(created),
            "updated": str(updated),
            "health": health,
            "encoded_hex": encoded.hex(),
            # Encoding then decoding must give the fields back unchanged --
            # including the body's exact bytes, which is the guarantee the
            # splice exists for.
            "round_trips": Envelope.decode(encoded).body.text == body_text,
        })

    # A seeded sweep over the field combinations the list above does not
    # reach, so a difference in, say, how a negative health is spelled cannot
    # hide behind the hand-picked cases.
    for index in range(24):
        resource_type = rng.choice(list(ResourceType))
        body_text = rng.choice(_BODIES)
        created = TaiCursor(rng.randrange(0, 2**32), rng.randrange(0, 10**9))
        updated = TaiCursor(rng.randrange(0, 2**32), rng.randrange(0, 10**9))
        health = rng.choice(_HEALTHS)
        envelope = Envelope(
            version=ENVELOPE_VERSION,
            resource_type=resource_type,
            body=Body(body_text),
            created=created,
            updated=updated,
            health=health,
        )
        encoded = envelope.encode()
        cases.append({
            "kind": "encode",
            "name": f"random#{index}",
            "version": ENVELOPE_VERSION,
            "resource_type": resource_type.value,
            "body_text": body_text,
            "created": str(created),
            "updated": str(updated),
            "health": health,
            "encoded_hex": encoded.hex(),
            "round_trips": Envelope.decode(encoded).body.text == body_text,
        })
    return cases


def _head(**overrides: str | None) -> str:
    """An envelope's metadata, with members replaced or dropped.

    ``None`` drops the member, which is how the "absent" cases are spelled.

    Spelled textually rather than via ``json.dumps`` so a case can be invalid
    JSON, carry a duplicate member, or use a literal Python has no value for.
    """
    members = {
        "v": "1",
        "type": '"node"',
        "created": '"1:2"',
        "updated": '"3:4"',
        "health": "5",
        "data": '{"id": "n1"}',
    }
    for name, value in overrides.items():
        if value is None:
            members.pop(name, None)
        else:
            members[name] = value
    return "{" + ", ".join(f'"{n}": {v}' for n, v in members.items()) + "}"


def _decode_inputs() -> list[tuple[str, bytes]]:
    """Values to decode, named for the diff."""
    cases: list[tuple[str, str]] = [
        ("valid", _head()),
        # Every resource type, so a type table that is wrong in one entry shows.
        *(
            (f"type/{t.value}", _head(type=f'"{t.value}"'))
            for t in ResourceType
        ),
        # `v`.
        ("v_missing", _head(v=None)),
        ("v_zero", _head(v="0")),
        ("v_negative", _head(v="-1")),
        ("v_future", _head(v="2")),
        ("v_far_future", _head(v="99999999999999999999")),
        ("v_far_past", _head(v="-99999999999999999999")),
        ("v_float", _head(v="1.0")),
        ("v_string", _head(v='"1"')),
        ("v_null", _head(v="null")),
        # `isinstance(True, int)` is true in Python, so the obvious spelling of
        # an "is this an integer" check accepts these. Both implementations
        # refuse them, and these cases are what holds that.
        ("v_true", _head(v="true")),
        ("v_false", _head(v="false")),
        # `type`.
        ("type_missing", _head(type=None)),
        ("type_unknown", _head(type='"nod"')),
        ("type_plural", _head(type='"nodes"')),
        ("type_cased", _head(type='"Node"')),
        ("type_number", _head(type="7")),
        ("type_null", _head(type="null")),
        ("type_object", _head(type='{"a": 1}')),
        ("type_array", _head(type='["node"]')),
        ("type_empty", _head(type='""')),
        # `created` / `updated`.
        ("created_missing", _head(created=None)),
        ("created_number", _head(created="12")),
        ("created_null", _head(created="null")),
        ("created_malformed", _head(created='"x"')),
        ("created_no_colon", _head(created='"12"')),
        ("created_empty", _head(created='""')),
        ("created_negative", _head(created='"-1:0"')),
        ("created_spaced", _head(created='" 1:2"')),
        ("created_extra_colon", _head(created='"1:2:3"')),
        ("updated_missing", _head(updated=None)),
        ("updated_malformed", _head(updated='"y"')),
        ("cursor_large", _head(created='"18446744073709551615:999999999"')),
        # `health`.
        ("health_missing", _head(health=None)),
        ("health_zero", _head(health="0")),
        ("health_negative", _head(health="-7")),
        ("health_float", _head(health="3.5")),
        ("health_float_integral", _head(health="7.0")),
        ("health_string", _head(health='"5"')),
        ("health_null", _head(health="null")),
        ("health_true", _head(health="true")),
        ("health_false", _head(health="false")),
        ("health_max_i64", _head(health="9223372036854775807")),
        ("health_min_i64", _head(health="-9223372036854775808")),
        ("health_beyond_i64", _head(health="99999999999999999999")),
        # `data`.
        ("data_missing", _head(data=None)),
        ("data_array", _head(data="[]")),
        ("data_string", _head(data='"x"')),
        ("data_null", _head(data="null")),
        ("data_number", _head(data="7")),
        ("data_empty_object", _head(data="{}")),
        ("data_nested", _head(data='{"a": {"b": [1, 2]}}')),
        ("data_spaced", _head(data='{ "id" : "n1" }')),
        # Member order must not matter, and a duplicate member must resolve the
        # way `json.loads` resolves it -- last one wins.
        ("reordered", '{"data": {"id": "n1"}, "health": 5, "updated": "3:4", '
                      '"created": "1:2", "type": "node", "v": 1}'),
        ("duplicate_v", '{"v": 9, "v": 1, "type": "node", "created": "1:2", '
                        '"updated": "3:4", "health": 5, "data": {"id": "n1"}}'),
        ("duplicate_data", '{"v": 1, "type": "node", "created": "1:2", '
                           '"updated": "3:4", "health": 5, '
                           '"data": {"id": "first"}, "data": {"id": "last"}}'),
        ("extra_member", '{"v": 1, "type": "node", "created": "1:2", '
                         '"updated": "3:4", "health": 5, "data": {"id": "n1"}, '
                         '"future": {"unknown": true}}'),
        # Not an object, and not JSON.
        ("array", "[1, 2]"),
        ("number", "7"),
        ("string", '"text"'),
        ("null", "null"),
        ("true", "true"),
        ("empty_object", "{}"),
        ("empty", ""),
        ("whitespace", "   "),
        ("truncated", '{"v": 1, "type": "node"'),
        ("not_json", "{not json"),
        ("trailing_comma", '{"v": 1,}'),
        ("single_quoted", "{'v': 1}"),
        # Python's decoder accepts these and `serde_json` does not, so the
        # verdict has to come from the same rules on both sides. The nested
        # ones matter most and were the ones missed: a port that answered
        # "is this JSON at all" with `serde_json` gets the bare cases right by
        # accident and calls `[NaN]` invalid where Python calls it valid and
        # merely the wrong shape -- a different sentence in an operator's log
        # for the same value.
        ("nan_bare", "NaN"),
        ("infinity_bare", "Infinity"),
        ("negative_infinity_bare", "-Infinity"),
        ("nan_in_array", "[NaN]"),
        ("infinity_in_array", "[1, Infinity]"),
        ("nan_in_nested_array", "[[{\"a\": NaN}]]"),
        ("nan_in_body", _head(data='{"id": "n1", "n": NaN}')),
        ("infinity_in_body", _head(data='{"id": "n1", "n": -Infinity}')),
        # Trailing content after a complete value. `json.loads` calls this
        # `Extra data`, so it is not a document -- but the span scanner stops
        # at the closing brace, so an *object* with a tail still decodes.
        ("extra_data_after_array", "[1] x"),
        ("extra_data_after_number", "7 7"),
        ("extra_data_after_object", _head() + "x"),
        # Leading and trailing whitespace around the whole value.
        ("padded", "  " + _head() + "  "),
    ]
    inputs = [(name, text.encode()) for name, text in cases]
    # Not UTF-8 at all.
    inputs.append(("not_utf8", b"\xff\xfe"))
    inputs.append(("not_utf8_tail", b'{"v": 1, "type": "\xff"}'))
    return inputs


_I64_MIN = -(2 ** 63)
_I64_MAX = 2 ** 63 - 1


def _fits_i64(value: int) -> bool:
    """Whether a port with a 64-bit integer can hold this exactly."""
    return _I64_MIN <= value <= _I64_MAX


def _decode_cases() -> list[dict[str, Any]]:
    """What each stored value yields, or the refusal."""
    cases = []
    for name, value in _decode_inputs():
        case: dict[str, Any] = {
            "kind": "decode",
            "name": name,
            "value_hex": value.hex(),
        }
        try:
            envelope = Envelope.decode(value)
        except KeyError_ as exc:
            message = str(exc)
            case["outcome"] = "error"
            case["message"] = message
            # Only a value that is not UTF-8 interpolates CPython's words. A
            # value that is valid UTF-8 and invalid JSON interpolates the span
            # scanner's, which the Rust reproduces exactly, so it is recorded
            # as an exact match rather than a prefix.
            try:
                value.decode("utf-8")
            except UnicodeDecodeError:
                case["parity_prefix"] = _UNICODE_INTERPOLATION
            else:
                case["parity_prefix"] = message
        else:
            case["outcome"] = "decoded"
            case["version"] = envelope.version
            case["resource_type"] = envelope.resource_type.value
            case["body_text"] = envelope.body.text
            case["created"] = str(envelope.created)
            case["updated"] = str(envelope.updated)
            case["health"] = envelope.health
            # Python's int is unbounded; a port with a fixed-width one has to
            # saturate. Recorded so the far side compares the *verdict* --
            # which must match -- without being asked to reproduce a value it
            # has no type for. Reachable only from a hand-written key: this
            # registry writes a version of 1 and a health in seconds.
            case["version_fits_i64"] = _fits_i64(envelope.version)
            case["health_fits_i64"] = _fits_i64(envelope.health)
        cases.append(case)
    return cases


def build() -> dict[str, Any]:
    """Every case, in one list the shared staleness guard can check."""
    cases = (
        _namespace_cases()
        + _key_cases()
        + _parse_cases()
        + _encode_cases()
        + _decode_cases()
    )

    names = [f"{case['kind']}/{case['name']}" for case in cases]
    if len(set(names)) != len(names):
        duplicates = sorted({n for n in names if names.count(n) > 1})
        raise SystemExit(f"two cases share a name: {duplicates}")

    # A corpus that reaches only the accept path proves nothing about the
    # refusals, which is half of what "identical in what it rejects" means.
    for kind, outcome_key, wanted in (
        ("namespace", "accepted", {True, False}),
        ("parse", "outcome", {"parsed", "ignored", "error"}),
        ("decode", "outcome", {"decoded", "error"}),
    ):
        seen = {case.get(outcome_key) for case in cases if case["kind"] == kind}
        missing = wanted - seen
        if missing:
            raise SystemExit(
                f"the {kind} cases never produce {sorted(map(str, missing))}, "
                f"so the Rust side would be untested for it",
            )

    # The quirks this corpus exists to pin. Each is a place where a reasonable
    # independent implementation would do something else, so a corpus that
    # stopped reaching one would go on passing while saying nothing about it.
    decoded = [c for c in cases if c["kind"] == "decode" and c["outcome"] == "decoded"]
    by_name = {c["name"]: c for c in cases if c["kind"] == "decode"}

    # A boolean must be refused where an integer is required. `isinstance(True,
    # int)` is true in Python, so this is the one check whose *obvious*
    # spelling is wrong -- and the value it would store, a health of 1, makes
    # the resource expire on the next collection pass rather than failing.
    for name in ("v_true", "v_false", "health_true", "health_false"):
        case = by_name.get(name)
        if case is None or case["outcome"] != "error":
            raise SystemExit(
                f"the {name!r} case must be present and refused; without it a "
                f"port that accepted a boolean as an integer would pass",
            )
    # And the integers those booleans decode to must still be accepted, which
    # is the half a careless fix breaks.
    for name in ("health_zero", "v_zero"):
        case = by_name.get(name)
        if case is None or case["outcome"] != "decoded":
            raise SystemExit(
                f"the {name!r} case must be present and accepted; 0 and 1 are "
                f"legitimate values and a fix that refused them would pass",
            )

    if not any(
        not c["version_fits_i64"] or not c["health_fits_i64"] for c in decoded
    ):
        raise SystemExit(
            "no decode case carries an integer outside 64 bits, so a port that "
            "refuses one instead of saturating would not be detected",
        )

    # Every resource type must appear as something a key names and as
    # something an envelope carries, or a table that is wrong in one entry
    # passes.
    for kind, field in (("parse", "resource_type"), ("decode", "resource_type")):
        seen = {case.get(field) for case in cases if case["kind"] == kind}
        missing = {t.value for t in ResourceType} - seen
        if missing:
            raise SystemExit(
                f"no {kind} case yields resource type(s) {sorted(missing)}",
            )

    return {"envelope_version": ENVELOPE_VERSION, "namespace": NS, "cases": cases}


def main() -> None:
    corpus = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(corpus, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    counts: dict[str, int] = {}
    for case in corpus["cases"]:
        counts[case["kind"]] = counts.get(case["kind"], 0) + 1
    summary = ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))
    print(f"{len(corpus['cases'])} key vectors ({summary}) -> {OUTPUT}")


if __name__ == "__main__":
    main()
