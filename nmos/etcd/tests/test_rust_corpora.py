# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The exported Rust message corpus still describes this wire contract.

``_proto_corpus.py`` writes the bytes each etcd message serialises to, and a
Rust test asserts its own generated types produce the same. The recording is
worth something only while it reflects the current protos.

Without a guard the failure is silent and the wrong way round: re-vendor a
newer etcd, regenerate both trees, and the Rust suite keeps agreeing with a
recording of the *old* wire format. Both suites stay green while the clients
have diverged from the database they talk to -- which is the one thing a
cross-implementation corpus exists to detect.

This is the same guard the codegen, registry and raft corpora use. It is here
rather than alongside them because what it protects is the contract with
**etcd itself**, not between the two implementations: drift here is not a
failing test, it is a member writing records the database stores under a
schema nobody else reads.
"""

from __future__ import annotations

from typing import Any

from nmos.codegen.tests._corpus_guard import check_corpus
from nmos.etcd.tests import _proto_corpus


def _by_name(_index: int, record: dict[str, Any]) -> str:
    """Name a record the way the JSON file does, for a legible diff.

    The case name rather than the message type: five ``Compare`` vectors cover
    the five compare targets, and keying on the type would collapse them --
    which the guard's own collision check would refuse, correctly.
    """
    return str(record["name"])


def test_the_message_corpus_matches_the_current_protos() -> None:
    check_corpus(
        name="etcd message",
        output=_proto_corpus.OUTPUT,
        build=_proto_corpus.build,
        module="nmos.etcd.tests._proto_corpus",
        key=_by_name,
        # The corpus carries the proto fingerprint beside its cases, and
        # `records_key` makes the guard compare everything outside `cases` as a
        # whole. So a re-vendored proto that moved the fingerprint without the
        # recording being refreshed fails here, rather than at etcd.
        records_key="cases",
    )
