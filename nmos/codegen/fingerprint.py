# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Drift detection between the type model and the code generated from it.

``nmos/types/generated/`` is committed, which is what lets a fresh checkout run
without a codegen step. The price of committing generated code is that it can go
stale against its source, silently, and a stale type layer is not a build error
-- it is a registry validating bodies against a model nobody edits any more.

The ETCD generator already solved this for its own inputs: it stamps
``PROTO_FINGERPRINT`` into the generated package and refuses to start when the
vendored protos no longer hash to it. This is the same mechanism for the NMOS
generator, which until now had none.

Two fingerprints, not one
-------------------------
They demand the same remedy -- regenerate -- but they answer different
questions, and a single digest would report the wrong one half the time:

* ``MODEL_FINGERPRINT`` covers the **model**: the descriptors that say what the
  types *are*. A mismatch means someone edited the model and did not regenerate.
* ``EMITTER_FINGERPRINT`` covers the **emitter**: the generator and its
  templates, which say how the model is rendered. A mismatch means someone
  changed how code is produced and did not regenerate.

Keeping them apart also matters once a second emitter exists. A Rust tree
generated from the same descriptors must agree with the Python tree on
``MODEL_FINGERPRINT`` -- that shared value is what makes "both trees describe
one model" checkable -- while each tree's ``EMITTER_FINGERPRINT`` is its own,
because they are rendered by different templates.

What is deliberately NOT an input
---------------------------------
``nmos/codegen/namespaces.py`` looks like a model input and is not one. It is
imported by ``go_parser.py`` -- the historical bootstrap tool -- and by
application code, but never by ``generate.py``, ``generator.py`` or any
definitions module. Its values were baked into the descriptors when they were
first lifted out of Go, so editing it today and regenerating changes nothing.
Including it here would make the guard fire on edits that cannot affect the
output, which trains people to regenerate on a false alarm and then to ignore
the guard.

``go_parser.py`` is excluded for the same reason: it writes descriptors by hand
once, it is not a pipeline stage, and ``generate.py`` never imports it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_CODEGEN_DIR = Path(__file__).parent
_DEFINITIONS_DIR = _CODEGEN_DIR / "definitions"
_TEMPLATES_DIR = _CODEGEN_DIR / "templates"

MODEL_SCHEMA_FILE = "descriptors.py"
"""The ``TypeDesc``/``MemberDesc`` shape itself.

Part of the model rather than the emitter: adding a field to ``MemberDesc``
changes what a descriptor is able to say, so a descriptor set that predates it
describes a different model even if none of its own bytes moved.
"""

EMITTER_FILES = ("generator.py", "emitters.py")
"""Generator modules.

Not ``generate.py``: that is the command-line entry point, and its argument
parsing and progress output do not change a single byte of generated code.
Including it would fire the guard on edits that cannot affect the output, which
is how a guard earns being ignored.
"""


class GeneratedOutOfDate(Exception):
    """The committed generated tree does not match the model it came from."""


def _digest_files(paths: list[tuple[str, Path]]) -> str:
    """SHA-256 over ``(name, bytes)`` pairs, in the order given.

    The name is hashed as well as the contents so that adding, removing or
    renaming a file is a change too. Hashing only contents would let a deleted
    descriptor module go unnoticed whenever another module happened to be
    edited in the same commit -- and a deleted module is exactly the case where
    the generated tree keeps orphaned files behind.
    """
    digest = hashlib.sha256()
    for name, path in paths:
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _definition_files() -> list[tuple[str, Path]]:
    """Every ``.py`` under ``definitions/``, sorted, plus the descriptor schema.

    ``predicates.py`` is in here and is authoritative on equal footing with the
    ``TypeDesc`` literals: it is hand-written, it is not derivable from any
    other input, and it decides which concrete variant a polymorphic body
    decodes to. A guard that skipped it would miss the one input whose edits
    change behaviour without changing any type's shape.
    """
    files = sorted(
        (path.name, path)
        for path in _DEFINITIONS_DIR.glob("*.py")
    )
    files.append((MODEL_SCHEMA_FILE, _CODEGEN_DIR / MODEL_SCHEMA_FILE))
    return files


def _emitter_files() -> list[tuple[str, Path]]:
    """The generator modules and every template, sorted."""
    files = [
        (name, _CODEGEN_DIR / name)
        for name in EMITTER_FILES
        if (_CODEGEN_DIR / name).exists()
    ]
    files.extend(
        sorted(
            (f"templates/{path.name}", path)
            for path in _TEMPLATES_DIR.glob("*")
            if path.is_file()
        ),
    )
    return files


def model_fingerprint() -> str:
    """Digest of the type model: the descriptors and their schema."""
    return _digest_files(_definition_files())


def emitter_fingerprint() -> str:
    """Digest of the emitter: generator modules and templates."""
    return _digest_files(_emitter_files())


def check_generated_current() -> None:
    """Verify ``nmos/types/generated/`` was built from the current model.

    Cheap enough to run at startup -- a dozen file reads and a hash -- and the
    failure it prevents is quiet rather than loud: a type layer that no longer
    matches the descriptors anyone is reading, so a body is accepted or rejected
    for reasons the model no longer states.

    Raises:
        GeneratedOutOfDate: the tree is missing, predates fingerprinting, or was
            generated from a different model or emitter.
    """
    try:
        from nmos.types.generated import (  # noqa: PLC0415
            EMITTER_FINGERPRINT,
            MODEL_FINGERPRINT,
        )
    except ImportError as exc:
        raise GeneratedOutOfDate(
            "the generated NMOS types are missing or predate fingerprinting.\n"
            "  python -m nmos.codegen.generate",
        ) from exc

    current_model = model_fingerprint()
    if MODEL_FINGERPRINT != current_model:
        raise GeneratedOutOfDate(
            f"the generated NMOS types are stale: they were built from a model "
            f"with fingerprint {MODEL_FINGERPRINT[:12]}, but "
            f"nmos/codegen/definitions/ now hashes to {current_model[:12]}.\n"
            f"  python -m nmos.codegen.generate",
        )

    current_emitter = emitter_fingerprint()
    if EMITTER_FINGERPRINT != current_emitter:
        raise GeneratedOutOfDate(
            f"the generated NMOS types are stale: they were built by an emitter "
            f"with fingerprint {EMITTER_FINGERPRINT[:12]}, but "
            f"nmos/codegen/generator.py and templates/ now hash to "
            f"{current_emitter[:12]}.\n"
            f"  python -m nmos.codegen.generate",
        )
