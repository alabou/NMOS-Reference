# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Keep the generated Rust parity corpora honest.

Six JSON files under ``rust/crates/`` record what this implementation does, and
Rust tests assert their own answers match. Seven other corpora already had a
staleness guard — ``store``, ``cli``, ``oauth2``, ``browse``, ``error``,
``envelope``, ``validator`` — and these six did not, so they could drift in
silence: change the Python, and the Rust suite keeps agreeing with a recording
of behaviour that no longer exists.

The two largest unguarded files were ``structural_cases.json`` (789 KB) and
``float_cases.json``, which is the wrong way round — the bigger the recording,
the less likely anyone notices it has gone stale by reading it.

Each check rebuilds the corpus and compares. That requires every generator to
be reproducible **across processes**, and adding these guards found that
``_decode_corpus`` was not: its fixtures stamped ``tai_version()`` at import
time, so every regeneration produced a different file and the recording could
never be checked against a fresh build. It now pins a fixed version, which is
what ``_encode_corpus`` and ``_structural_corpus`` already did and said they did
it for. The two generators that draw random values were fine -- both seed their
own ``random.Random``.

So a difference here means behaviour changed, not that the generator wandered.
"""

from __future__ import annotations

from typing import Any, Callable

import pytest

from nmos.codegen.tests import (
    _decode_corpus,
    _dump_corpus,
    _encode_corpus,
    _float_corpus,
    _span_corpus,
    _structural_corpus,
)
from nmos.codegen.tests._corpus_guard import check_corpus


def _by_label(_index: int, record: dict[str, Any]) -> str:
    return str(record["label"])


def _by_type_and_label(_index: int, record: dict[str, Any]) -> str:
    """Decode labels repeat across resource types.

    Every one of the five base bodies contributes a ``valid``, a
    ``keys_reversed`` and a ``missing_<key>`` per key, so ``label`` alone names
    up to five different records. Keying by it collapsed 217 cases to 114 and
    the guard then checked only the survivors.
    """
    return f"{record['resource_type']}:{record['label']}"


def _by_bits(_index: int, record: dict[str, Any]) -> str:
    """A float case is identified by its exact bit pattern.

    Not by the decimal value: ``-0.0``, the two NaNs and the subnormals are
    distinct cases that render identically, and keying on the rendering would
    silently collapse them into one.
    """
    return str(record["bits"])


def _by_position(index: int, record: dict[str, Any]) -> str:
    """Span cases carry no unique field — two mutations can yield one source."""
    return f"#{index} {str(record.get('origin', ''))[:24]}"


# (name, module, key) — the module supplies OUTPUT and build().
_CORPORA: list[tuple[str, Any, Callable[[int, dict[str, Any]], str]]] = [
    ("structural", _structural_corpus, _by_label),
    ("decode", _decode_corpus, _by_type_and_label),
    ("dump", _dump_corpus, _by_label),
    ("encode", _encode_corpus, _by_label),
    ("float", _float_corpus, _by_bits),
    ("span", _span_corpus, _by_position),
]


@pytest.mark.parametrize(
    ("name", "module", "key"), _CORPORA, ids=[row[0] for row in _CORPORA],
)
def test_the_committed_corpus_matches_this_implementation(
    name: str, module: Any, key: Callable[[int, dict[str, Any]], str],
) -> None:
    check_corpus(
        name=name,
        output=module.OUTPUT,
        build=module.build,
        module=module.__name__,
        key=key,
    )


@pytest.mark.parametrize(
    ("name", "module"),
    [(row[0], row[1]) for row in _CORPORA],
    ids=[row[0] for row in _CORPORA],
)
def test_the_corpus_is_not_empty(name: str, module: Any) -> None:
    """Guard the guard.

    A generator that silently produced nothing would make the check above pass
    against an empty file, and the Rust side would assert nothing while
    reporting success.
    """
    assert module.build(), f"the {name} generator produced no cases"
