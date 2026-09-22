# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Run code generation: render the type model into every target language.

Reads ``ALL_TYPES`` from each definitions module -- the authoritative, committed
model -- and renders it through one emitter per language.

Usage::

    python -m nmos.codegen.generate               # both languages
    python -m nmos.codegen.generate --lang python # just nmos/types/generated/
    python -m nmos.codegen.generate --lang rust   # just the Rust crate

``both`` is the default on purpose. A default of one language would mean a model
edit regenerates one tree and leaves the other describing a model that no longer
exists -- which the fingerprint guard would catch, but only after someone had
committed it.

Base types (``NString``, ``NInt``, ...) are hand-written in ``nmos/json/types.py``
and skipped here.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from nmos.codegen.descriptors import TypeDesc
from nmos.codegen.emitters import Emitter, select
from nmos.codegen.fingerprint import emitter_fingerprint, model_fingerprint
from nmos.codegen.generator import emit_index, emit_type

# Base types that are hand-written in nmos/json/types.py — do not generate
_HAND_WRITTEN = {
    "NBool", "NString", "NHyperlink", "NInt", "NFloat", "NNull", "NNullString",
    "NEnum", "NUrl", "NTime", "NGeneric",
    "NArrayOfBool", "NArrayOfString", "NArrayOfHyperlink", "NArrayOfInt",
    "NArrayOfFloat", "NArrayOfNull", "NArrayOfNullString", "NArrayOfEnum",
    "NArrayOfUrl", "NArrayOfTime", "NArrayOfGeneric",
    "NTags",
    # Map types with custom embedded decode logic (hand-written Go types)
    "NConstraints", "NTransportConstraints",
}


_INIT_TEMPLATE = '''"""Generated NMOS types. DO NOT EDIT."""

MODEL_FINGERPRINT = "{model}"
"""SHA-256 over nmos/codegen/definitions/ and descriptors.py.

A second implementation generated from the same descriptors carries this same
value, which is what makes "both trees describe one model" checkable.
"""

EMITTER_FINGERPRINT = "{emitter}"
"""SHA-256 over nmos/codegen/generator.py, emitters.py and templates/.

Per-tree, unlike MODEL_FINGERPRINT: two languages are rendered by two templates.
"""
'''

RUST_EDITION = "2024"
"""Matches `edition` in `rust/Cargo.toml`. rustfmt is invoked directly rather
than through cargo, so it has no manifest to read this from."""

_FINGERPRINT_RS = '''//! Model and emitter digests for this generated tree. DO NOT EDIT.

/// SHA-256 over `nmos/codegen/definitions/` and `descriptors.py`.
///
/// The Python tree carries this same value. `nmos/codegen/tests/
/// test_fingerprint.py` asserts they are equal, which is what makes "both
/// implementations describe one model" a checkable claim rather than a hope.
// Wrapped because a 64-character digest plus the declaration exceeds rustfmt's
// line width, and `cargo fmt --check` is part of the gate. Emitting it already
// wrapped keeps a regeneration from dirtying the tree.
pub const MODEL_FINGERPRINT: &str =
    "{model}";

/// SHA-256 over the generator and its templates.
///
/// Per-tree: the two languages are rendered by two templates, so these differ
/// between the trees by design.
pub const EMITTER_FINGERPRINT: &str =
    "{emitter}";
'''


def load_model() -> list[TypeDesc]:
    """Every descriptor, with polymorphic predicates attached.

    ``predicates.py`` is hand-written and is not derivable from anything else in
    the model, which is why it is part of the model fingerprint and why it is
    merged in here rather than living inside the ``TypeDesc`` literals.
    """
    from nmos.codegen.definitions.base_types import ALL_TYPES as base
    from nmos.codegen.definitions.constraint_types import ALL_TYPES as constraint
    from nmos.codegen.definitions.controller_db_types import (
        ALL_TYPES as controller_db,
    )
    from nmos.codegen.definitions.is04_types import ALL_TYPES as is04
    from nmos.codegen.definitions.is05_types import ALL_TYPES as is05
    from nmos.codegen.definitions.is11_types import ALL_TYPES as is11
    from nmos.codegen.definitions.is12_types import ALL_TYPES as is12
    from nmos.codegen.definitions.predicates import ALL_PREDICATES

    all_types = base + constraint + is04 + is05 + is11 + is12 + controller_db
    for desc in all_types:
        if desc.name in ALL_PREDICATES:
            desc.predicates = ALL_PREDICATES[desc.name]
    return list(all_types)


def run(emitter: Emitter, all_types: list[TypeDesc]) -> tuple[int, int, int]:
    """Render every type this emitter handles. Returns (emitted, skipped, errors)."""
    emitted: list[str] = []
    skipped = 0
    errors = 0
    known = frozenset(desc.name for desc in all_types)

    for desc in all_types:
        if desc.name in _HAND_WRITTEN or desc.name in emitter.skip:
            skipped += 1
            continue
        try:
            emit_type(emitter, desc, known)
            emitted.append(desc.name)
        except Exception as exc:  # noqa: BLE001 - reported per type, run continues
            print(f"  ERROR generating {desc.name}: {exc}", file=sys.stderr)
            errors += 1

    if not errors:
        # The index is a whole-corpus artifact, so it can only be written once
        # every type has rendered. Rust needs it -- a module absent from mod.rs
        # is not part of the crate, and nothing would report that.
        emit_index(emitter, emitted)

    return len(emitted), skipped, errors


def stamp(emitter: Emitter, model: str, emitter_digest: str) -> None:
    """Write the fingerprints into the emitter's tree."""
    if emitter.lang == "python":
        (emitter.output_dir / "__init__.py").write_text(
            _INIT_TEMPLATE.format(model=model, emitter=emitter_digest),
            encoding="utf-8",
            newline="\n",
        )
        return

    (emitter.output_dir / "fingerprint.rs").write_text(
        _FINGERPRINT_RS.format(model=model, emitter=emitter_digest),
        encoding="utf-8",
        newline="\n",
    )
    # A sidecar the Python test can read without parsing Rust or invoking cargo.
    (emitter.output_dir / "fingerprint.json").write_text(
        f'{{\n  "model_fingerprint": "{model}",\n'
        f'  "emitter_fingerprint": "{emitter_digest}"\n}}\n',
        encoding="utf-8",
        newline="\n",
    )


def _find_rustfmt() -> str | None:
    """``rustfmt``, on ``PATH`` or where rustup puts it.

    The fallback is not belt-and-braces. rustup installs into
    ``~/.cargo/bin`` and adds it to the shell profile, so the tool is on
    ``PATH`` in a login shell and absent from one started by an editor, a hook
    or a CI step that does not source it. Without this, whether the committed
    tree comes out formatted depends on how the generator happened to be
    launched, which is exactly the kind of difference that shows up later as an
    unexplained diff.
    """
    found = shutil.which("rustfmt")
    if found is not None:
        return found
    fallback = Path.home() / ".cargo" / "bin" / "rustfmt"
    return str(fallback) if fallback.is_file() else None


def format_rust(emitter: Emitter) -> None:
    """Run ``rustfmt`` over the tree just emitted.

    A Jinja template cannot practically produce rustfmt-canonical output.
    rustfmt reorders imports within a group, sorts lower-case items after
    upper-case ones inside a brace list, decides for itself where an expression
    wraps, and collapses blank lines -- all of which would have to be
    reimplemented in the template and would then drift with every toolchain
    release.

    So the generator does what every Rust code generator does and hands the
    result to the formatter. The alternative is not "unformatted code": it is a
    tree that ``cargo fmt --check`` rejects, which means either the gate stops
    checking generated code or every regeneration dirties 260 files. Both were
    true here before this ran.

    Absence of a toolchain is not an error. The Python side of this project is
    generated and tested without Rust installed, and refusing to emit because a
    formatter is missing would break that for no benefit -- the code is valid
    either way.
    """
    binary = _find_rustfmt()
    if binary is None:
        print(
            "[rust] rustfmt not found; the tree is correct but unformatted, "
            "and `cargo fmt --check` will report it",
            file=sys.stderr,
        )
        return

    sources = sorted(emitter.output_dir.glob("*.rs"))
    if not sources:
        return

    result = subprocess.run(  # noqa: S603 - a fixed binary on a fixed file set
        [binary, "--edition", RUST_EDITION, *(str(p) for p in sources)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        # Worth shouting about: it means the emitter produced something that
        # does not parse, which no amount of formatting will fix.
        print(
            f"[rust] rustfmt failed ({result.returncode}):\n{result.stderr}",
            file=sys.stderr,
        )
        return
    print(f"[rust] formatted {len(sources)} files with rustfmt")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lang",
        default="both",
        help="which tree to generate: python, rust, or both (default: both)",
    )
    args = parser.parse_args()
    emitters = select(args.lang)

    # Computed from the inputs before anything is emitted, and written only once
    # generation has succeeded. Stamping first would leave a fingerprint
    # asserting a tree is current on top of one that failed half way -- the
    # single state the guard exists to make impossible.
    model = model_fingerprint()
    emitter_digest = emitter_fingerprint()

    all_types = load_model()
    failed = False

    for emitter in emitters:
        emitter.output_dir.mkdir(parents=True, exist_ok=True)
        emitted, skipped, errors = run(emitter, all_types)
        print(
            f"[{emitter.lang}] generated {emitted} types, "
            f"skipped {skipped}, {errors} errors -> {emitter.output_dir}",
        )
        if errors:
            failed = True
            print(
                f"[{emitter.lang}] NOT stamping fingerprints: the tree is not "
                f"current.",
                file=sys.stderr,
            )
            continue
        if emitter.lang == "rust":
            format_rust(emitter)
        stamp(emitter, model, emitter_digest)

    print(f"Model fingerprint:   {model[:12]}")
    print(f"Emitter fingerprint: {emitter_digest[:12]}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
