# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The exported layout corpus still describes what this module derives.

Without this, changing the token derivation here leaves the Rust suite
asserting against a recording of the old digest -- both suites green while a
Rust member and a Python member would refuse each other's handshake. That is
the one failure this corpus exists to detect, so it must not be able to go
stale quietly.
"""

from __future__ import annotations

from typing import Any

from nmos.cluster.tests import _layout_corpus
from nmos.codegen.tests._corpus_guard import check_corpus


def _by_name(_index: int, record: dict[str, Any]) -> str:
    return str(record["name"])


def test_the_layout_corpus_matches_the_current_derivation() -> None:
    check_corpus(
        name="cluster layout",
        output=_layout_corpus.OUTPUT,
        build=_layout_corpus.build,
        module="nmos.cluster.tests._layout_corpus",
        key=_by_name,
        records_key="cases",
    )
