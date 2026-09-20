# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The exported Rust corpora still describe what this registry does.

Four generators under this directory write JSON that a Rust test then asserts
against. A recording is worth something only while it reflects the current
Python, and without a guard the failure is silent and the wrong way round:
change the Python, and the Rust suite keeps agreeing with a recording of the
old behaviour. Both suites stay green while the two implementations have
diverged -- which is the one thing a cross-implementation corpus exists to
detect.

These went unguarded for longer than the codegen ones, and it cost something
real. ``_cursor_corpus.py`` was added after a 32-bit nanosecond field in the
Rust refused ``0:5000000000`` -- a version string the AMWA schema permits and
this registry accepts -- so the same registration was a 201 here and a 400
there. Nothing failed. The lesson is not about that one field: it is that a
corpus nobody re-derives is a photograph, and this is what keeps it a mirror.
"""

from __future__ import annotations

from typing import Any

from nmos.codegen.tests._corpus_guard import check_corpus
from nmos.registry.tests import (
    _cli_corpus,
    _cursor_corpus,
    _envelope_corpus,
    _keys_corpus,
    _store_corpus,
)


def _by_position(index: int, _record: dict[str, Any]) -> str:
    return f"#{index}"


def _by_text(_index: int, record: dict[str, Any]) -> str:
    return str(record["text"])


def _by_kind_and_name(_index: int, record: dict[str, Any]) -> str:
    """Name a key case the way its own file does.

    The kind is part of the identity: a namespace prefix, a parsed key and a
    stored value can all legitimately be called ``node``, and keying on the
    name alone would collapse them -- which the guard's own collision check
    would then refuse, correctly.
    """
    return f"{record['kind']}/{record['name']}"


def test_the_store_corpus_matches_the_current_store() -> None:
    check_corpus(
        name="store",
        output=_store_corpus.OUTPUT,
        build=_store_corpus.build,
        module="nmos.registry.tests._store_corpus",
        # Steps are a sequence and only meaningful in order, so position is the
        # identity. A step that moved is a different corpus, not a changed
        # record, and reporting it as "#137 differs" is the honest description.
        key=_by_position,
        records_key="steps",
    )


def test_the_cursor_corpus_matches_the_current_parser() -> None:
    check_corpus(
        name="cursor",
        output=_cursor_corpus.OUTPUT,
        build=_cursor_corpus.build,
        module="nmos.registry.tests._cursor_corpus",
        key=_by_text,
        records_key="cases",
    )


def test_the_envelope_corpus_matches_the_current_decoder() -> None:
    check_corpus(
        name="envelope",
        output=_envelope_corpus.OUTPUT,
        build=_envelope_corpus.build,
        module="nmos.registry.tests._envelope_corpus",
        key=_by_position,
        records_key="cases",
    )


def test_the_key_corpus_matches_the_current_key_layout() -> None:
    """The etcd key layout and storage envelope, not the POST envelope.

    Distinct from ``_envelope_corpus`` above, which records the *Registration
    API* envelope a Node posts. This one records what goes into etcd: the key
    a resource is stored under and the value stored there.

    Drift here is the quietest failure in the port. A raft frame that decodes
    differently fails to parse and the link drops. A key spelled differently is
    written successfully and never seen by the watcher -- the resource is in
    the cluster and invisible on the member that did not write it, with nothing
    failing anywhere.
    """
    check_corpus(
        name="etcd key",
        output=_keys_corpus.OUTPUT,
        build=_keys_corpus.build,
        module="nmos.registry.tests._keys_corpus",
        key=_by_kind_and_name,
        records_key="cases",
    )


def test_the_cli_corpus_matches_the_current_parser() -> None:
    check_corpus(
        name="cli",
        output=_cli_corpus.OUTPUT,
        build=_cli_corpus.build,
        module="nmos.registry.tests._cli_corpus",
        key=_by_position,
        records_key="flags",
    )
