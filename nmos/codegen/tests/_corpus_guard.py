# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Shared staleness check for the generated Rust parity corpora.

Each ``_*_corpus.py`` module writes a JSON file under ``rust/crates/`` that a
Rust test then asserts against. The recording is only worth something while it
reflects what this implementation currently does.

Without a guard the failure is silent and the wrong way round: change the Python,
and the Rust suite keeps agreeing with a recording of the old behaviour. Both
suites stay green while the two implementations have diverged — which is the one
thing the corpus exists to detect.

Every generator these guard is deterministic: the two that draw random values
seed their own ``random.Random``, so rebuilding produces the same records or the
behaviour changed. That is what makes a direct comparison the right check here,
where ``test_oauth2_corpus.py`` has to compare verdicts instead because it mints
fresh keys on every run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Sequence

# How many differing records to name before giving up on the list. Enough to see
# a pattern; a diff of 800 records in a failure message is not read by anyone.
_MAX_REPORTED = 8


def _keyed(
    records: Sequence[dict[str, Any]],
    key: Callable[[int, dict[str, Any]], str],
    *,
    origin: str,
) -> dict[str, dict[str, Any]]:
    """Index records by `key`, refusing to let two records share one.

    A colliding key does not fail the comparison -- it quietly shrinks it, and
    the guard then reports success while checking a subset. That is worse than
    no guard, because it also reports coverage it does not have.

    Found the hard way: keying the decode corpus by ``label`` alone collapsed
    217 records to 114, because each of the five resource types contributes a
    ``valid``, a ``keys_reversed`` and so on. Tampering with a dropped record
    was not detected.
    """
    keyed: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(records):
        name = key(index, record)
        assert name not in keyed, (
            f"the {origin} corpus has two records keyed {name!r}, so the "
            f"staleness check would compare only one of them and silently "
            f"cover {len(records)} records with fewer. Give this corpus a key "
            f"function that distinguishes them."
        )
        keyed[name] = record
    return keyed


def check_corpus(
    *,
    name: str,
    output: Path,
    build: Callable[[], list[dict[str, Any]]],
    module: str,
    key: Callable[[int, dict[str, Any]], str] | None = None,
) -> None:
    """Fail unless ``output`` matches what ``build`` produces now.

    Args:
        name: The corpus's short name, for the failure message.
        output: The committed JSON file.
        build: The generator's ``build()``.
        module: Dotted module path, so the message can say how to regenerate.
        key: Identifies a record for the diff. Defaults to its position, which
            is right for the corpora whose records carry no natural label.
    """
    if key is None:
        def key(index: int, _record: dict[str, Any]) -> str:  # noqa: E306
            return f"#{index}"

    regenerate = (
        f"Regenerate and re-run the Rust suite:\n"
        f"  python -m {module}\n"
        f"  (cd rust && cargo test)"
    )

    assert output.exists(), (
        f"{output} is missing — the Rust {name} parity test has nothing to "
        f"assert against.\n{regenerate}"
    )

    committed: list[dict[str, Any]] = json.loads(output.read_text())
    current = build()

    assert len(committed) == len(current), (
        f"the {name} corpus changed size — {len(committed)} recorded, "
        f"{len(current)} now.\n{regenerate}"
    )

    recorded = _keyed(committed, key, origin=f"committed {name}")
    rebuilt = _keyed(current, key, origin=name)

    added = sorted(set(rebuilt) - set(recorded))[:_MAX_REPORTED]
    removed = sorted(set(recorded) - set(rebuilt))[:_MAX_REPORTED]
    assert not added and not removed, (
        f"the {name} corpus's cases changed — added {added}, "
        f"removed {removed}.\n{regenerate}"
    )

    differing = [name_ for name_, record in rebuilt.items() if recorded[name_] != record]
    assert not differing, (
        f"{len(differing)} {name} case(s) now behave differently: "
        f"{differing[:_MAX_REPORTED]}"
        f"{' …' if len(differing) > _MAX_REPORTED else ''}\n"
        f"Either this implementation changed and the recording is stale, or "
        f"something regressed. Both need the same first step:\n{regenerate}"
    )
