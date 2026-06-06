# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Run code generation: produce Python type modules from all descriptors.

Imports ALL_TYPES from every definition module and generates Python source
files into nmos/types/generated/.

Usage:
    python -m nmos.codegen.generate

The base types from genTypes0 (NString, NInt, NBool, etc.) are hand-written
in nmos/json/types.py and are NOT generated here — they are skipped.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from nmos.codegen.generator import generate_type

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


def main() -> None:
    output_dir = str(Path(__file__).parent.parent / "types" / "generated")
    os.makedirs(output_dir, exist_ok=True)

    # Write __init__.py
    init_path = os.path.join(output_dir, "__init__.py")
    with open(init_path, "w") as f:
        f.write('"""Generated NMOS types. DO NOT EDIT."""\n')

    # Import all definition modules
    from nmos.codegen.definitions.base_types import ALL_TYPES as base
    from nmos.codegen.definitions.constraint_types import ALL_TYPES as constraint
    from nmos.codegen.definitions.is04_types import ALL_TYPES as is04
    from nmos.codegen.definitions.is05_types import ALL_TYPES as is05
    from nmos.codegen.definitions.is11_types import ALL_TYPES as is11
    from nmos.codegen.definitions.is12_types import ALL_TYPES as is12
    from nmos.codegen.definitions.controller_db_types import ALL_TYPES as controller_db

    all_types = base + constraint + is04 + is05 + is11 + is12 + controller_db

    # Inject predicate data into polymorphic types
    from nmos.codegen.definitions.predicates import ALL_PREDICATES
    for desc in all_types:
        if desc.name in ALL_PREDICATES:
            desc.predicates = ALL_PREDICATES[desc.name]

    generated = 0
    skipped = 0
    errors = 0

    for desc in all_types:
        if desc.name in _HAND_WRITTEN:
            skipped += 1
            continue
        try:
            filepath = generate_type(output_dir, desc)
            generated += 1
        except Exception as e:
            print(f"  ERROR generating {desc.name}: {e}", file=sys.stderr)
            errors += 1

    print(f"Generated {generated} types, skipped {skipped} hand-written, {errors} errors")
    print(f"Output: {output_dir}")


if __name__ == "__main__":
    main()
