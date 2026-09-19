# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The exported Rust corpora still describe what this implementation does.

``_wire_corpus.py`` and ``_messages_corpus.py`` each write a JSON file that a
Rust test asserts against. The recording is only worth something while it
reflects the current Python, and without this guard the failure is silent and
the wrong way round: change ``test_wire.py``'s golden vectors, and the Rust
suite keeps agreeing with a recording of the old bytes. Both suites stay green
while the two implementations have diverged -- which is the one thing a
cross-implementation corpus exists to detect.

This is the same guard the codegen corpora use, and it is here rather than
alongside them because it protects the wire format of a **mixed Python/Rust
cluster**: the consequence of drift is not a failing test, it is two members
that cannot talk to each other.
"""

from __future__ import annotations

from typing import Any

from nmos.codegen.tests._corpus_guard import check_corpus
from nmos.raft.tests import (
    _messages_corpus,
    _operations_corpus,
    _persist_corpus,
    _wire_corpus,
)


def _by_name(_index: int, record: dict[str, Any]) -> str:
    """Name a record the way the JSON file does, for a legible diff.

    Position would work, but "case #7 differs" sends the reader counting
    through a JSON file to find out which message that is.

    The message corpus needs the variant too: each sample appears as itself,
    saturated and with its optional fields absent, so the name alone names
    three records. Keying on it collapsed 30 records to 14 and the guard's own
    collision check refused -- which is what that check is for.
    """
    if "name" in record:
        return str(record["name"])
    if "operation" in record:
        return str(record["operation"])
    return f"{record['message']}/{record['variant']}"


def test_the_wire_corpus_matches_the_current_frames() -> None:
    check_corpus(
        name="raft wire",
        output=_wire_corpus.OUTPUT,
        build=_wire_corpus.build,
        module="nmos.raft.tests._wire_corpus",
        key=_by_name,
        records_key="cases",
    )


def test_the_message_corpus_matches_the_current_messages() -> None:
    check_corpus(
        name="raft message",
        output=_messages_corpus.OUTPUT,
        build=_messages_corpus.build,
        module="nmos.raft.tests._messages_corpus",
        key=_by_name,
        records_key="cases",
    )


def test_the_refusal_corpus_matches_the_current_refusals() -> None:
    """Reworded refusals must be re-recorded, not left to drift.

    This one differs from the other two in what drift means: the wire corpora
    protect bytes on a link, and a difference there stops two members talking.
    Here a difference only means the two implementations describe the same
    broken file in different words -- which nothing detects at runtime, and
    which is discovered by an operator comparing two logs.
    """
    check_corpus(
        name="raft refusal",
        output=_persist_corpus.OUTPUT,
        build=_persist_corpus.build,
        module="nmos.raft.tests._persist_corpus",
        key=_by_name,
        records_key="cases",
    )


def test_the_operation_corpus_matches_the_current_operations() -> None:
    """The corpus whose drift is a state-machine divergence, not a link fault.

    A frame that decodes differently fails to parse and the link drops. An
    *operation* that decodes differently applies a different mutation from the
    same committed entry, so two members reach different states from an
    identical log -- which is the failure every other part of this package is
    arranged to prevent.
    """
    check_corpus(
        name="raft operation",
        output=_operations_corpus.OUTPUT,
        build=_operations_corpus.build,
        module="nmos.raft.tests._operations_corpus",
        key=_by_name,
        records_key="cases",
    )
