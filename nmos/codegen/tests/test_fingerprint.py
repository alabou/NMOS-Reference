# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The guard that makes committing the generated type tree safe.

``nmos/types/generated/`` is 50,000 lines of tracked, committed code, which is
what lets a fresh checkout run with no codegen step. The cost is that it can
drift from the descriptors it was built from, and the failure is quiet: a
registry accepting or rejecting bodies for reasons the model no longer states.

These tests pin the check that closes it. They mirror
``nmos/etcd/tests/test_generated_fingerprint.py``, which has guarded the ETCD
stubs the same way, plus two cases that tree does not need:

* **orphan detection** -- a renamed or deleted type leaves its old module behind,
  still importable, describing a type the model no longer has;
* **cross-tree equality** -- once a second emitter exists, both trees must carry
  the same ``MODEL_FINGERPRINT``, which is what makes "these two implementations
  describe one model" a checkable claim rather than an intention.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nmos.codegen import fingerprint as fp
from nmos.codegen.fingerprint import (
    GeneratedOutOfDate,
    check_generated_current,
    emitter_fingerprint,
    model_fingerprint,
)

_GENERATED_DIR = Path(__file__).parent.parent.parent / "types" / "generated"

_RUST_FINGERPRINT_JSON = (
    Path(__file__).parent.parent.parent.parent
    / "rust" / "crates" / "nmos-types" / "src" / "generated" / "fingerprint.json"
)

# Hand-written modules that live *inside* the generated tree. The generator
# never emits them and must never delete them; they are listed in
# ``generate._HAND_WRITTEN`` for the first reason and here for the second.
_HAND_WRITTEN_IN_TREE = {
    "__init__.py",
    "nconstraints.py",
    "ntransport_constraints.py",
}


def _expected_module_names() -> set[str]:
    """Every filename the generator would emit, by the rule it uses."""
    from nmos.codegen.definitions.base_types import ALL_TYPES as base
    from nmos.codegen.definitions.constraint_types import ALL_TYPES as constraint
    from nmos.codegen.definitions.controller_db_types import (
        ALL_TYPES as controller_db,
    )
    from nmos.codegen.definitions.is04_types import ALL_TYPES as is04
    from nmos.codegen.definitions.is05_types import ALL_TYPES as is05
    from nmos.codegen.definitions.is11_types import ALL_TYPES as is11
    from nmos.codegen.definitions.is12_types import ALL_TYPES as is12
    from nmos.codegen.generate import _HAND_WRITTEN
    from nmos.codegen.generator import _to_snake

    all_types = base + constraint + is04 + is05 + is11 + is12 + controller_db
    return {
        f"{_to_snake(desc.name)}.py"
        for desc in all_types
        if desc.name not in _HAND_WRITTEN
    }


def test_the_committed_tree_matches_the_current_model() -> None:
    """Fails the moment someone edits a descriptor without regenerating."""
    check_generated_current()


def test_fingerprints_are_stable() -> None:
    assert model_fingerprint() == model_fingerprint()
    assert emitter_fingerprint() == emitter_fingerprint()
    assert len(model_fingerprint()) == 64
    assert len(emitter_fingerprint()) == 64


def test_model_and_emitter_fingerprints_differ() -> None:
    """Two digests over two input sets, so a mismatch names the right cause."""
    assert model_fingerprint() != emitter_fingerprint()


def test_fingerprint_covers_file_names_not_just_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adding or removing a descriptor module is a change too."""
    before = model_fingerprint()

    spare = tmp_path / "definitions"
    spare.mkdir()
    for name, path in fp._definition_files():
        if name != fp.MODEL_SCHEMA_FILE:
            (spare / name).write_bytes(path.read_bytes())
    # Same bytes, one extra empty module: contents-only hashing would miss it.
    (spare / "zz_extra_types.py").write_bytes(b"")
    monkeypatch.setattr(fp, "_DEFINITIONS_DIR", spare)

    assert model_fingerprint() != before


def test_a_changed_model_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stale types must be a clear message, not a subtle behaviour change."""
    monkeypatch.setattr(fp, "model_fingerprint", lambda: "0" * 64)
    with pytest.raises(GeneratedOutOfDate, match="definitions"):
        fp.check_generated_current()


def test_a_changed_emitter_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Editing the template without regenerating is caught separately."""
    monkeypatch.setattr(fp, "emitter_fingerprint", lambda: "0" * 64)
    with pytest.raises(GeneratedOutOfDate, match="emitter"):
        fp.check_generated_current()


def test_namespaces_is_not_a_model_input() -> None:
    """``namespaces.py`` is a bootstrap input, not a generation input.

    Its docstring says regenerating applies a change made there; it does not,
    because nothing in the generation path imports it. Including it in the
    model digest would fire the guard on edits that cannot affect the output,
    which is how a guard gets ignored. See ``fingerprint.py``'s module docstring.
    """
    names = {name for name, _ in fp._definition_files()}
    assert "namespaces.py" not in names
    assert "go_parser.py" not in names


def test_no_orphaned_modules_in_the_generated_tree() -> None:
    """A renamed or deleted type must not leave its old module behind.

    Nothing else catches this: the stale module still imports, still exports a
    class, and describes a type the model no longer has.
    """
    on_disk = {
        path.name for path in _GENERATED_DIR.glob("*.py")
    }
    expected = _expected_module_names() | _HAND_WRITTEN_IN_TREE

    orphans = on_disk - expected
    missing = expected - on_disk
    assert not orphans, f"generated modules with no descriptor: {sorted(orphans)}"
    assert not missing, f"descriptors with no generated module: {sorted(missing)}"


def test_both_trees_describe_the_same_model() -> None:
    """Python and Rust must agree on ``MODEL_FINGERPRINT``.

    Skips until the Rust tree exists. When it does, this is the assertion that
    makes a mixed-language cluster's type agreement checkable: either tree being
    stale fails here, rather than at a peer that rejects a body the other
    accepted.
    """
    if not _RUST_FINGERPRINT_JSON.exists():
        pytest.skip("the Rust generated tree does not exist yet")

    from nmos.types.generated import MODEL_FINGERPRINT

    rust = json.loads(_RUST_FINGERPRINT_JSON.read_text(encoding="utf-8"))
    assert rust["model_fingerprint"] == MODEL_FINGERPRINT, (
        "the Python and Rust generated trees were built from different models; "
        "regenerate both with: python -m nmos.codegen.generate"
    )
