# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Code generator: renders TypeDesc into Python source files.

Given a TypeDesc, produces:
- types/{name_lower}.py -- the type and value classes
- (enum constants are included inline in the type module)

Uses Jinja2 templates from the templates/ directory.
"""

from __future__ import annotations

import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from nmos.codegen.descriptors import TypeDesc

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def _to_snake(name: str) -> str:
    """Convert PascalCase to snake_case (e.g., 'NSource' -> 'n_source')."""
    result: list[str] = []
    for i, c in enumerate(name):
        if c.isupper() and i > 0 and not name[i - 1].isupper():
            result.append("_")
        result.append(c.lower())
    return "".join(result)


def generate_type(output_dir: str, desc: TypeDesc) -> str:
    """Generate a Python module for the given type descriptor.

    Args:
        output_dir: Directory to write the generated file into.
        desc: The type descriptor.

    Returns:
        The path to the generated file.
    """
    desc.validate()

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["to_snake"] = _to_snake

    template = env.get_template("type.py.jinja2")

    rendered = template.render(t=desc)

    filename = f"{_to_snake(desc.name)}.py"
    filepath = os.path.join(output_dir, filename)

    os.makedirs(output_dir, exist_ok=True)

    with open(filepath, "w") as f:
        f.write(rendered)

    return filepath
