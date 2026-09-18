# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""What it means to render the type model into one language.

The model in ``definitions/`` is authoritative and language-neutral. An
``Emitter`` is everything that turns it into files for a particular target: a
template, an output directory, a filename rule, and whatever extra Jinja filters
that template needs.

Two emitters, not a parent and a child
--------------------------------------
``PYTHON`` and ``RUST`` are peers. Neither reads the other's output, and there is
no step where Rust is derived from the Python types -- both read the same
``TypeDesc`` list. That is what makes the shared ``MODEL_FINGERPRINT`` mean
something: if the two trees carry the same digest, they were built from the same
model, and a mixed-language cluster can be trusted to agree about what a Node is.

Why filenames are shared
------------------------
Both emitters name files with the same ``to_snake`` rule, so ``NNode`` becomes
``nnode.py`` on one side and ``nnode.rs`` on the other. That one-to-one
correspondence is worth more than it costs: a reviewer comparing the two
implementations of a type never has to work out which file to open, and the
orphan check in ``tests/test_fingerprint.py`` can compare the two trees by name.

Why Rust emits fewer types than Python
--------------------------------------
Three descriptor categories exist only to satisfy Go's type system and carry no
JSON of their own:

* **pointer** types -- their Python ``encode``/``decode_value`` are literally
  ``pass``, and every member referring to one has ``json_key="-"``;
* the three **pointer maps** (``NSenderPtrs`` and friends) -- likewise reachable
  only through ``-`` members.

Python emits them because the template has a branch for them and a dead module
costs nothing there. Rust would warn about unused code and, worse, a reader would
reasonably assume an emitted type is part of the wire format. So the Rust emitter
skips them, and ``SKIPPED_FOR_RUST`` records exactly which and why rather than
leaving it as a silent difference in file counts between the two trees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

_CODEGEN_DIR = Path(__file__).parent
_PACKAGE_ROOT = _CODEGEN_DIR.parent
_REPO_ROOT = _PACKAGE_ROOT.parent


def to_snake(name: str) -> str:
    """PascalCase to snake_case: ``NSource`` becomes ``n_source``.

    A run of capitals is kept together, which is why ``NNode`` becomes ``nnode``
    rather than ``n_node``. Shared by both emitters so the two trees correspond
    file for file.
    """
    result: list[str] = []
    for index, char in enumerate(name):
        if char.isupper() and index > 0 and not name[index - 1].isupper():
            result.append("_")
        result.append(char.lower())
    return "".join(result)


@dataclass(frozen=True)
class Emitter:
    """One target language's rendering rules.

    Args:
        lang: The name used by ``generate.py --lang``.
        template: Template filename under ``templates/``.
        output_dir: Where rendered modules are written.
        extension: File extension, without the dot.
        index_template: Template for the module index, when the language needs
            one. Rust does: it has no implicit package namespace, so every
            module has to be declared in a ``mod.rs``. Python does not.
        index_name: Filename for that index.
        preserved: Files in ``output_dir`` that are hand-written and must never
            be treated as generated output. They are excluded from the orphan
            check and never deleted.
        skip: Type names this emitter does not render at all.
    """

    lang: str
    template: str
    output_dir: Path
    extension: str
    index_template: str | None = None
    index_name: str | None = None
    preserved: frozenset[str] = field(default_factory=frozenset)
    skip: frozenset[str] = field(default_factory=frozenset)

    def filename(self, type_name: str) -> str:
        """The file this emitter writes for a type."""
        return f"{to_snake(type_name)}.{self.extension}"

    def path_for(self, type_name: str) -> Path:
        return self.output_dir / self.filename(type_name)


# Pointer types and pointer maps: no JSON of their own, reachable only through
# ``json_key="-"`` members. See the module docstring.
SKIPPED_FOR_RUST = frozenset({
    "NSenderPtr", "NSourcePtr", "NFlowPtr", "NDevicePtr", "NNodePtr",
    "NReceiverPtr",
    "NSenderPtrs", "NSourcePtrs", "NFlowPtrs",
})


PYTHON = Emitter(
    lang="python",
    template="type.py.jinja2",
    output_dir=_PACKAGE_ROOT / "types" / "generated",
    extension="py",
    # Hand-written modules that live inside the generated tree. The generator
    # never produces them and must never remove them.
    preserved=frozenset({
        "__init__.py", "nconstraints.py", "ntransport_constraints.py",
    }),
)


RUST = Emitter(
    lang="rust",
    template="type.rs.jinja2",
    output_dir=_REPO_ROOT / "rust" / "crates" / "nmos-types" / "src" / "generated",
    extension="rs",
    index_template="mod.rs.jinja2",
    index_name="mod.rs",
    preserved=frozenset({"mod.rs", "fingerprint.rs", "fingerprint.json"}),
    skip=SKIPPED_FOR_RUST,
)


ALL: dict[str, Emitter] = {PYTHON.lang: PYTHON, RUST.lang: RUST}


def select(lang: str) -> list[Emitter]:
    """Resolve a ``--lang`` value to the emitters it names.

    ``both`` is the default everywhere, deliberately: a default of one language
    means a model edit regenerates one tree and silently staleness the other,
    which is the exact failure the fingerprint guard exists to catch. Better not
    to create it in the first place.
    """
    if lang == "both":
        return [PYTHON, RUST]
    try:
        return [ALL[lang]]
    except KeyError:
        raise SystemExit(
            f"unknown --lang {lang!r}; expected one of: "
            f"{', '.join(sorted(ALL))}, both",
        ) from None
