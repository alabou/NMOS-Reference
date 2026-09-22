# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Keep the Rust validator corpus honest.

`rust/crates/nmos-json/tests/validator_cases.json` records what
`nmos/validators.py` decided for every case, and the Rust test suite asserts its
own validators reach the same verdicts. That only means anything while the
recording is current.

Without this test the failure is silent and the wrong way round: change a
Python validator, and the Rust side keeps agreeing with what Python used to do.
The Rust tests stay green, and the two implementations diverge precisely because
the check that was supposed to catch it is comparing against a stale answer.

So: regenerate in memory, compare to what is committed, and fail with the
command to fix it.
"""

from __future__ import annotations

import json

import pytest

from nmos.codegen.tests._decode_corpus import OUTPUT as DECODE_OUTPUT
from nmos.codegen.tests._dump_corpus import OUTPUT as DUMP_OUTPUT
from nmos.codegen.tests._dump_corpus import build as build_dump
from nmos.codegen.tests._encode_corpus import OUTPUT as ENCODE_OUTPUT
from nmos.codegen.tests._encode_corpus import build as build_encode
from nmos.codegen.tests._structural_corpus import OUTPUT as STRUCTURAL_OUTPUT
from nmos.codegen.tests._structural_corpus import build as build_structural
from nmos.codegen.tests._float_corpus import OUTPUT as FLOAT_OUTPUT
from nmos.codegen.tests._float_corpus import build as build_float
from nmos.codegen.tests._span_corpus import OUTPUT as SPAN_OUTPUT
from nmos.codegen.tests._span_corpus import build as build_span
from nmos.codegen.tests._decode_corpus import build as build_decode
from nmos.codegen.tests._validator_corpus import OUTPUT, build


def _committed() -> list[dict[str, object]]:
    if not OUTPUT.exists():
        pytest.fail(
            f"{OUTPUT} is missing.\n"
            f"  python -m nmos.codegen.tests._validator_corpus",
        )
    return list(json.loads(OUTPUT.read_text(encoding="utf-8")))


def test_the_committed_corpus_matches_what_python_does_now() -> None:
    """Fails the moment a validator changes without the corpus being rebuilt."""
    fresh = build()
    committed = _committed()

    by_key = {(c["validator"], c["label"]): c for c in committed}
    drifted = []
    for case in fresh:
        key = (case["validator"], case["label"])
        old = by_key.get(key)
        if old is None:
            drifted.append(f"{key[0]}/{key[1]}: missing from the committed corpus")
        elif old["ok"] != case["ok"] or old.get("message") != case.get("message"):
            drifted.append(
                f"{key[0]}/{key[1]}: committed {old['ok']}/{old.get('message')!r} "
                f"but python now says {case['ok']}/{case.get('message')!r}",
            )

    removed = set(by_key) - {(c["validator"], c["label"]) for c in fresh}
    drifted.extend(f"{v}/{lbl}: no longer generated" for v, lbl in sorted(removed))

    assert not drifted, (
        "the Rust validator corpus is stale -- the Rust tests are agreeing with "
        "a Python that no longer exists:\n  "
        + "\n  ".join(drifted)
        + "\n\n  python -m nmos.codegen.tests._validator_corpus"
    )


def test_the_corpus_covers_both_verdicts() -> None:
    """Guard the guard.

    A corpus of only-accepts or only-rejects would still compare cleanly while
    testing half of what matters. Both arms have to be represented for the
    comparison to mean anything.
    """
    cases = _committed()
    accepted = sum(1 for c in cases if c["ok"])
    rejected = len(cases) - accepted
    assert accepted > 20, f"only {accepted} accepting cases"
    assert rejected > 20, f"only {rejected} rejecting cases"


def test_every_rejection_carries_a_message() -> None:
    """The message is the HTTP 400 body, so a blank one is a defect in itself."""
    blank = [
        f"{c['validator']}/{c['label']}"
        for c in _committed()
        if not c["ok"] and not c.get("message")
    ]
    assert not blank, f"rejections with no message: {blank}"


def test_the_committed_decode_corpus_matches_what_python_does_now() -> None:
    """Same guard, for the decode corpus.

    The Rust decode-parity test asserts the generated types agree with what
    Python decided. Change a descriptor or the Python template without
    rebuilding this, and Rust keeps agreeing with a Python that no longer
    exists -- green tests, diverging implementations.
    """
    if not DECODE_OUTPUT.exists():
        pytest.fail(
            f"{DECODE_OUTPUT} is missing.\n"
            f"  python -m nmos.codegen.tests._decode_corpus",
        )
    fresh = build_decode()
    committed = list(json.loads(DECODE_OUTPUT.read_text(encoding="utf-8")))

    by_key = {(c["resource_type"], c["label"]): c for c in committed}
    drifted = []
    for case in fresh:
        key = (case["resource_type"], case["label"])
        old = by_key.get(key)
        if old is None:
            drifted.append(f"{key[0]}/{key[1]}: missing from the committed corpus")
        elif old["ok"] != case["ok"] or old.get("message") != case.get("message"):
            drifted.append(
                f"{key[0]}/{key[1]}: committed {old['ok']}/{old.get('message')!r} "
                f"but python now says {case['ok']}/{case.get('message')!r}",
            )

    assert not drifted, (
        "the Rust decode corpus is stale:\n  "
        + "\n  ".join(drifted[:10])
        + "\n\n  python -m nmos.codegen.tests._decode_corpus"
    )


def test_the_committed_float_corpus_matches_what_python_does_now() -> None:
    """The encoder's float spelling is pinned by a generated corpus too.

    This one guards a fix as much as a format: ``_write_float_value`` used
    ``f"{value:g}"`` and truncated to six significant digits, so 673 of these
    811 values came back as a different number. A silent revert would make the
    Rust side agree with the broken behaviour again.
    """
    if not FLOAT_OUTPUT.exists():
        pytest.fail(
            f"{FLOAT_OUTPUT} is missing.\n"
            f"  python -m nmos.codegen.tests._float_corpus",
        )
    fresh = {c["bits"]: c for c in build_float()}
    committed = {
        c["bits"]: c
        for c in json.loads(FLOAT_OUTPUT.read_text(encoding="utf-8"))
    }

    drifted = [
        f"bits {bits}: committed {old.get('formatted')!r} "
        f"but python now says {fresh[bits].get('formatted')!r}"
        for bits, old in committed.items()
        if bits in fresh and old.get("formatted") != fresh[bits].get("formatted")
    ]
    assert not drifted, (
        "the float corpus is stale:\n  "
        + "\n  ".join(drifted[:10])
        + "\n\n  python -m nmos.codegen.tests._float_corpus"
    )


def test_the_committed_dump_corpus_matches_what_python_does_now() -> None:
    """Synthesised responses are byte-compared, so their corpus must be current."""
    if not DUMP_OUTPUT.exists():
        pytest.fail(
            f"{DUMP_OUTPUT} is missing.\n"
            f"  python -m nmos.codegen.tests._dump_corpus",
        )
    fresh = {c["label"]: c for c in build_dump()}
    committed = {
        c["label"]: c
        for c in json.loads(DUMP_OUTPUT.read_text(encoding="utf-8"))
    }

    drifted = [
        f"{label}: committed {old['dumped']!r} but python now writes "
        f"{fresh[label]['dumped']!r}"
        for label, old in committed.items()
        if label in fresh and old["dumped"] != fresh[label]["dumped"]
    ]
    assert not drifted, (
        "the dump corpus is stale:\n  "
        + "\n  ".join(drifted[:5])
        + "\n\n  python -m nmos.codegen.tests._dump_corpus"
    )


def test_the_float_corpus_still_covers_the_truncation_bug() -> None:
    """Guard the guard.

    The corpus only proves anything while it contains values that ``%g`` would
    have mangled. If the spread ever narrowed to six-significant-digit values,
    every case would pass under either implementation.
    """
    cases = json.loads(FLOAT_OUTPUT.read_text(encoding="utf-8"))
    would_have_been_lossy = sum(
        1
        for c in cases
        if c.get("dumped") is not None and len(c["formatted"].split("e")[0].replace("-", "").replace(".", "").lstrip("0")) > 6
    )
    assert would_have_been_lossy > 100, (
        f"only {would_have_been_lossy} values carry more than six significant "
        f"digits; the corpus no longer exercises the bug it was built for"
    )


def test_the_committed_span_corpus_matches_what_python_does_now() -> None:
    """Span slicing is what keeps a registration byte-for-byte.

    Both halves are pinned: the spans themselves, and the error messages, which
    reach the HTTP 400 body verbatim through ``decode.py:149``.
    """
    if not SPAN_OUTPUT.exists():
        pytest.fail(
            f"{SPAN_OUTPUT} is missing.\n"
            f"  python -m nmos.codegen.tests._span_corpus",
        )
    fresh = {c["source"]: c for c in build_span()}
    committed = {
        c["source"]: c
        for c in json.loads(SPAN_OUTPUT.read_text(encoding="utf-8"))
    }

    drifted = []
    for source, old in committed.items():
        new = fresh.get(source)
        if new is None:
            drifted.append(f"{source!r}: no longer generated")
        elif old["ok"] != new["ok"]:
            drifted.append(f"{source!r}: verdict changed")
        elif not old["ok"] and old.get("message") != new.get("message"):
            drifted.append(
                f"{source!r}: committed {old.get('message')!r} "
                f"but python now says {new.get('message')!r}",
            )
        elif old["ok"] and old.get("spans") != new.get("spans"):
            drifted.append(f"{source!r}: spans changed")

    assert not drifted, (
        "the span corpus is stale:\n  "
        + "\n  ".join(drifted[:10])
        + "\n\n  python -m nmos.codegen.tests._span_corpus"
    )


def test_the_span_corpus_keeps_its_fuzz_tier() -> None:
    """Guard the guard.

    The hand-written cases were all passing before the fuzz tier existed; the
    fuzz tier found five real bugs on its first run. If it ever shrank away,
    the corpus would go back to testing only what someone thought to write.
    """
    cases = json.loads(SPAN_OUTPUT.read_text(encoding="utf-8"))
    fuzzed = sum(1 for c in cases if c.get("origin") == "fuzz")
    assert fuzzed > 100, f"only {fuzzed} fuzz cases"


def test_the_committed_structural_corpus_matches_what_python_does_now() -> None:
    """The deep corpus needs the same guard, and needs it more.

    It is the only one that reaches the nested generated types, and it caught
    seven assertions the Rust emitter was skipping. A stale recording here would
    hand that coverage back without anything going red -- the Rust suite would
    keep agreeing with a Python that no longer exists, in exactly the places
    nothing else looks.
    """
    if not STRUCTURAL_OUTPUT.exists():
        pytest.fail(
            f"{STRUCTURAL_OUTPUT} is missing.\n"
            f"  python -m nmos.codegen.tests._structural_corpus",
        )
    fresh = {c["label"]: c for c in build_structural()}
    committed = {
        c["label"]: c
        for c in json.loads(STRUCTURAL_OUTPUT.read_text(encoding="utf-8"))
    }

    drifted = []
    for label, old in committed.items():
        new = fresh.get(label)
        if new is None:
            drifted.append(f"{label}: no longer generated")
        elif old["ok"] != new["ok"] or old.get("message") != new.get("message"):
            drifted.append(
                f"{label}: committed {old['ok']}/{old.get('message')!r} "
                f"but python now says {new['ok']}/{new.get('message')!r}",
            )
    for label in fresh.keys() - committed.keys():
        drifted.append(f"{label}: a new case that was never committed")

    assert not drifted, (
        "the structural corpus is stale:\n  "
        + "\n  ".join(drifted[:10])
        + "\n\n  python -m nmos.codegen.tests._structural_corpus"
    )


def test_the_structural_corpus_keeps_its_depth() -> None:
    """Guard the guard.

    Depth is the whole reason this corpus exists. The bodies it walks populate
    optional members precisely so the nested types become reachable, and a
    fixture that quietly stopped doing so would shrink it back to what the
    top-level corpus already covers -- with no test failing to say so.
    """
    cases = json.loads(STRUCTURAL_OUTPUT.read_text(encoding="utf-8"))

    def depth(label: str) -> int:
        return len(label.rsplit(":", 1)[-1].split("."))

    deep = sum(1 for c in cases if depth(c["label"]) >= 3)
    assert deep > 100, f"only {deep} cases reach three levels or deeper"

    # The element types that exist solely to be reached by walking into an
    # array, and the Receiver this corpus is the only one to build.
    for fragment in (
        "api.endpoints.0",
        "interfaces.0",
        "clocks.0",
        "components.0",
        "channels.0",
        "caps.constraint_sets.0",
    ):
        assert any(fragment in c["label"] for c in cases), (
            f"nothing mutates inside {fragment}"
        )
    assert any(c["resource_type"] == "receiver" for c in cases)

    # Both verdicts, or the comparison degenerates into "both reject everything".
    accepted = sum(1 for c in cases if c["ok"])
    assert accepted > 50, f"only {accepted} accepted cases"
    assert len(cases) - accepted > 300, "too few rejections to be interesting"


def _first_difference(old: str, new: str, window: int = 45) -> str:
    """Point at where two encodings part company.

    A whole encoded resource is several hundred characters, and printing two of
    them side by side buries a one-character change in two walls of identical
    text. The reader needs the position and its neighbourhood, not the body.
    """
    at = next(
        (i for i, (a, b) in enumerate(zip(old, new)) if a != b),
        min(len(old), len(new)),
    )
    start = max(0, at - window)
    return (
        f"differs at character {at}\n"
        f"      committed ...{old[start:at + window]}...\n"
        f"      but now   ...{new[start:at + window]}..."
    )


def test_the_committed_encode_corpus_matches_what_python_does_now() -> None:
    """The bytes a generated type writes are pinned, not just its verdict.

    This is the only corpus whose payload can be compared outright: the others
    record a verdict or a message, but an encoding *is* the artefact, and it is
    deterministic because ``_encode_corpus`` stamps a fixed ``version`` instead
    of reading the clock.

    A drift here is a wire change. Member order, an applied default that stopped
    applying, a float spelling, an escaping rule -- each of them alters what a
    subscriber receives, and each of them would otherwise be invisible until the
    Rust suite started agreeing with a Python that no longer exists.
    """
    if not ENCODE_OUTPUT.exists():
        pytest.fail(
            f"{ENCODE_OUTPUT} is missing.\n"
            f"  python -m nmos.codegen.tests._encode_corpus",
        )
    fresh = {c["label"]: c for c in build_encode()}
    committed = {
        c["label"]: c
        for c in json.loads(ENCODE_OUTPUT.read_text(encoding="utf-8"))
    }

    drifted = []
    for label, old in committed.items():
        new = fresh.get(label)
        if new is None:
            drifted.append(f"{label}: no longer encodes (python now rejects the body?)")
        elif old["encoded"] != new["encoded"]:
            drifted.append(f"{label}: {_first_difference(old['encoded'], new['encoded'])}")
    for label in fresh.keys() - committed.keys():
        drifted.append(f"{label}: a new case that was never committed")

    assert not drifted, (
        "the encode corpus is stale:\n  "
        + "\n  ".join(drifted[:5])
        + "\n\n  python -m nmos.codegen.tests._encode_corpus"
    )


def test_the_encode_corpus_still_reaches_the_paths_it_was_built_for() -> None:
    """Guard the guard.

    Byte equality over a corpus of near-identical bodies proves very little.
    These are the four properties that make the comparison worth running, and
    each is asserted against the recorded bytes rather than against the case
    list, so a case that stopped exercising its path is caught as well as one
    that disappeared.
    """
    cases = {
        c["label"]: c
        for c in json.loads(ENCODE_OUTPUT.read_text(encoding="utf-8"))
    }

    # A default the body never carried, injected by decode and then written.
    flow = cases["flow"]
    assert "transfer_characteristic" not in flow["body"]
    assert '"transfer_characteristic":"SDR"' in flow["encoded"], (
        "the applied-default path is no longer covered"
    )

    # Float spellings Rust's own formatter gets wrong.
    assert '"minimum":1e+16' in cases["receiver_float_1e+16"]["encoded"]
    assert '"minimum":1000000.0' in cases["receiver_float_1000000.0"]["encoded"]
    assert '"minimum":1e-05' in cases["receiver_float_1e-05"]["encoded"]

    # Non-ASCII goes out raw, not \\u-escaped, and control characters do not.
    emoji = cases["node_label_emoji"]["encoded"]
    assert "\U0001f600" in emoji and "\\ud83d" not in emoji
    assert "\\n" in cases["node_label_control"]["encoded"]

    # A null that survives as null, distinct from one that is dropped.
    assert '"clock_name":null' in cases["source_null_clock_name"]["encoded"]
