# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""One namespace decision, three places that must agree.

``nmos/codegen/namespaces.py`` chooses, per feature area, between the standard
``urn:x-nmos:`` namespace and the private ``urn:x-matrox:`` one. That choice
then appears in three places, and until these tests existed, only discipline
kept them together:

1. **``namespaces.py`` itself**, which is live at runtime -- ``node/config``
   builds its coercion table from it, and ``controller/compat.py`` reads it.
2. **``nmos/codegen/definitions/*.py``**, where the choice is *frozen* into
   ``json_key`` literals such as ``"urn:x-matrox:info_block"``. It was applied
   once, by ``go_parser.py``, when the descriptors were lifted out of Go.
   ``generate.py`` does **not** re-apply it -- it never imports ``namespaces``.
3. **``caps/MatroxCCF.py``**, whose ``Cap*`` constants carry the same prefixes.
   ``namespaces.py:30-33`` warns that these must be updated by hand.

Why this needed a test
----------------------
``namespaces.py``'s docstring used to say that changing a value and
regenerating switched the namespace everywhere. It does not, because (2) is
frozen. Following that instruction produced a split: the generated types kept
encoding the old prefix while ``node/config`` treated the new one as
configured.

And it failed *quietly*. ``alternate_namespaces()`` gives dual-namespace decode
tolerance, so nothing raised -- one half simply emitted a form the other half
did not consider configured. Namespace-prefix mixing in this codebase has a
history of degrading silently rather than erroring, which is exactly the class
of bug a test has to catch instead of a comment.

How it is checked
-----------------
Rather than restate the constant-to-suffix mapping (a fourth copy, with the
same drift problem), these tests reuse ``node/config``'s coercion table. Its
**keys are, by construction, every URN in a non-configured namespace** -- that
is what it exists to coerce. So the assertion is simply that no committed URN
appears among them.

``caps/`` is a shared module. These tests only READ it.
"""

from __future__ import annotations

import re
from pathlib import Path

from nmos.node.config import _get_coerce_table

_MATROX_CCF = Path(__file__).parent.parent.parent.parent / "caps" / "MatroxCCF.py"

_URN_LITERAL = re.compile(r'"(urn:x-[^"]+)"')


def _all_json_keys() -> list[tuple[str, str]]:
    """Every ``(type_name, json_key)`` in the committed descriptors."""
    from nmos.codegen.definitions.base_types import ALL_TYPES as base
    from nmos.codegen.definitions.constraint_types import ALL_TYPES as constraint
    from nmos.codegen.definitions.controller_db_types import (
        ALL_TYPES as controller_db,
    )
    from nmos.codegen.definitions.is04_types import ALL_TYPES as is04
    from nmos.codegen.definitions.is05_types import ALL_TYPES as is05
    from nmos.codegen.definitions.is11_types import ALL_TYPES as is11
    from nmos.codegen.definitions.is12_types import ALL_TYPES as is12

    keys: list[tuple[str, str]] = []
    for desc in base + constraint + is04 + is05 + is11 + is12 + controller_db:
        for member in desc.members:
            if member.json_key and member.json_key != "-":
                keys.append((desc.name, member.json_key))
    return keys


def test_the_coercion_table_is_non_empty() -> None:
    """Guard the guard.

    Both tests below assert "nothing appears in this table". If the table were
    ever empty they would pass vacuously and stop protecting anything, which
    looks exactly like success.
    """
    table = _get_coerce_table()
    assert table, "the coercion table is empty; the checks below would be vacuous"
    assert any("urn:x-matrox:" in urn for urn in table)
    assert any("urn:x-nmos:" in urn for urn in table)


def test_descriptor_json_keys_use_the_configured_namespace() -> None:
    """The frozen literals in ``definitions/`` still match ``namespaces.py``.

    This is the assertion that was missing. The prefixes in the descriptors
    were applied at bootstrap and are never re-applied, so without this a
    change to ``namespaces.py`` desynchronises the generated types from the
    runtime configuration with nothing reporting it.

    It protects the Rust tree too: that emitter reads the same descriptors, so
    a drifted literal becomes a drifted JSON key in *both* implementations --
    and the Rust/Python parity harness cannot see it, because it compares the
    two against each other rather than against the configured namespace.
    """
    table = _get_coerce_table()
    drifted = [
        (type_name, key, table[key])
        for type_name, key in _all_json_keys()
        if key in table
    ]
    assert not drifted, (
        "descriptor json_key values are in a non-configured namespace -- "
        "nmos/codegen/definitions/ has drifted from nmos/codegen/namespaces.py:\n"
        + "\n".join(
            f"  {name}.{key!r} should be {want!r}" for name, key, want in drifted
        )
    )


def test_matrox_ccf_constants_use_the_configured_namespace() -> None:
    """``caps/MatroxCCF.py``'s constants agree with ``namespaces.py``.

    Turns the hand-maintenance warning at ``namespaces.py:30-33`` into
    something that fires. Read-only: ``caps/`` is shared with other projects and
    is not modified here. If this fails, the fix is a decision, not a cleanup.

    Constants whose suffix is not namespace-switchable -- ``format:audio`` and
    the rest of standard NMOS -- are unaffected, because only switchable
    suffixes appear in the coercion table at all.
    """
    if not _MATROX_CCF.exists():
        # caps/ is shared and may legitimately be absent from a slim checkout.
        return

    table = _get_coerce_table()
    source = _MATROX_CCF.read_text(encoding="utf-8")
    drifted = sorted(
        {
            (urn, table[urn])
            for urn in _URN_LITERAL.findall(source)
            if urn in table
        },
    )
    assert not drifted, (
        "caps/MatroxCCF.py constants are in a non-configured namespace -- see "
        "the warning at nmos/codegen/namespaces.py:30-33:\n"
        + "\n".join(f"  {urn!r} should be {want!r}" for urn, want in drifted)
    )
