# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Rendering one type descriptor into one target language.

Given a ``TypeDesc`` and an ``Emitter``, produces that emitter's module for the
type. The descriptor is the only input; everything language-specific lives in
the emitter and its template.

The Jinja environment is built once per process
-----------------------------------------------
It used to be rebuilt inside ``generate_type``, which meant constructing an
``Environment`` and re-reading the template from disk 269 times per run. Caching
it is not only faster: it also makes "the template" a single object for the
whole run, so a template that fails to parse fails once and immediately rather
than on the first type that happens to reach it.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, Template

from nmos.codegen.descriptors import TypeDesc
from nmos.codegen.emitters import Emitter, to_snake

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def _to_snake(name: str) -> str:
    """Retained under its original name: the Python template calls this filter,
    and the fingerprint test imports it to predict filenames."""
    return to_snake(name)


@lru_cache(maxsize=None)
def _environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["to_snake"] = _to_snake
    env.filters["rust_ident"] = rust_ident
    env.filters["rust_str"] = rust_str
    env.filters["rust_default"] = rust_default
    return env


# ---------------------------------------------------------------------------
# Rust-specific filters
# ---------------------------------------------------------------------------

# Members named after a Rust keyword. Python has no such collision because its
# member names are PascalCase attributes, but the snake_case field names Rust
# wants land on `enum`, `type`, `static` and friends. `r#` is the escape.
_RUST_KEYWORDS = frozenset({
    "as", "break", "const", "continue", "crate", "dyn", "else", "enum",
    "extern", "false", "fn", "for", "if", "impl", "in", "let", "loop", "match",
    "mod", "move", "mut", "pub", "ref", "return", "self", "Self", "static",
    "struct", "super", "trait", "true", "type", "unsafe", "use", "where",
    "while", "async", "await", "box", "final", "macro", "override", "priv",
    "try", "typeof", "unsized", "virtual", "yield", "abstract", "become", "do",
})


def rust_ident(name: str) -> str:
    """A snake_case Rust field name, escaped when it collides with a keyword."""
    ident = to_snake(name)
    return f"r#{ident}" if ident in _RUST_KEYWORDS else ident


def rust_str(value: str) -> str:
    """A Rust string literal.

    Escapes only what must be escaped inside a `"..."` literal. JSON keys and
    enum values reach here, and some carry characters -- a backslash has never
    appeared in one, but a quote could, and silently producing a broken literal
    would fail the build in a file nobody wrote by hand.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def rust_default(expression: str, type_name: str) -> str:
    """Translate a descriptor's Python default expression into Rust.

    The descriptors store defaults as Python source -- ``'False'``, ``'1'``,
    ``'EnumRegistry.get("SDR")'`` -- because that is what the Python emitter
    pastes straight into its output. There are only a handful of distinct
    shapes, and an unrecognised one raises rather than guessing: a silently
    wrong default would change what the registry stores for a member the client
    never sent, which is the hardest kind of difference to notice.
    """
    expression = expression.strip()

    if expression.startswith("EnumRegistry.get("):
        inner = expression[len("EnumRegistry.get(") : -1].strip().strip("\"'")
        return f'EnumId::new({rust_str(inner)})'
    if expression == "True":
        return "true"
    if expression == "False":
        return "false"
    if expression == "None":
        # Only ever on a nullable member, where Python's None is a DEFINED
        # null rather than an absence.
        return "Nullable::Null"
    if expression.lstrip("-").isdigit():
        return expression

    raise ValueError(
        f"no Rust translation for default {expression!r} on a {type_name} member",
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _template(emitter: Emitter) -> Template:
    return _environment().get_template(emitter.template)


def render_type(
    emitter: Emitter, desc: TypeDesc, known: frozenset[str] = frozenset(),
) -> str:
    """Render one type, returning the source text without writing it.

    ``known`` is every type name in the model. The Rust template needs it to
    resolve a member typed ``NClockValue``: Python emits two classes per
    descriptor (``NClock`` and ``NClockValue``) and members may name either,
    but Rust collapses the pair to one struct, so the ``Value`` suffix has to
    be stripped -- and only when what remains is a real type, since a name like
    ``NConstraintValue`` could legitimately be its own descriptor.
    """
    desc.validate()
    return _template(emitter).render(t=desc, known=known)


def emit_type(
    emitter: Emitter, desc: TypeDesc, known: frozenset[str] = frozenset(),
) -> Path:
    """Render one type and write it into the emitter's output directory."""
    rendered = render_type(emitter, desc, known)
    path = emitter.path_for(desc.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered)
    return path


def emit_index(emitter: Emitter, type_names: list[str]) -> Path | None:
    """Render the module index, for emitters whose language needs one.

    Rust does: it has no implicit package namespace, so a file that is not named
    in a ``mod.rs`` is not part of the crate at all -- it simply would not
    compile in, silently. Python does not, which is why this returns ``None``
    there rather than writing an empty file.
    """
    if emitter.index_template is None or emitter.index_name is None:
        return None
    rendered = _environment().get_template(emitter.index_template).render(
        modules=sorted(to_snake(name) for name in type_names),
    )
    path = emitter.output_dir / emitter.index_name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered)
    return path


def generate_type(output_dir: str, desc: TypeDesc) -> str:
    """Render a Python module for ``desc`` into ``output_dir``.

    Kept for callers that predate the emitter split, and because the Python
    output path is the one with 51,946 committed lines riding on it.
    """
    from nmos.codegen.emitters import PYTHON

    rendered = render_type(PYTHON, desc)
    path = Path(output_dir) / PYTHON.filename(desc.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered)
    return str(path)
