# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Type and member descriptors for the NMOS code generator.

These dataclasses mirror Go's T_Desc and M_Desc, with
readable Python names instead of single-letter abbreviations.

A TypeDesc + its MemberDescs fully describe an NMOS type. The code generator
uses these to produce:
- A Python module with {Name}Value and {Name} classes
- An enum registration module for the type's JSON property names
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MemberDesc:
    """Describes one member/field of a type.

    Maps to Go's M_Desc:
      N -> name, T -> type_name, J -> json_key, O -> optional,
      E -> embedded, D -> default, A -> assertion, C -> casting
    """

    name: str
    """PascalCase field name (e.g., 'FrameWidth', 'SourceId')."""

    type_name: str
    """Type of the field (e.g., 'NString', 'NInt', 'NResourceCore')."""

    json_key: str = ""
    """JSON property name. '-' means internal-only (not serialized)."""

    optional: bool = False
    """If True, the field is optional (not required in JSON)."""

    embedded: bool = False
    """If True, the field's members are flattened into parent JSON scope."""

    default: str = ""
    """Default value expression as a Python string. Empty means no default."""

    assertion: str = ""
    """Name of assertion function: func(probe) -> None, raises on invalid."""

    casting: str = ""
    """Name of casting function: func(value) -> value, applied on set."""


@dataclass
class TypeDesc:
    """Describes a complete NMOS type.

    Maps to Go's T_Desc:
      P -> package, N -> name, V -> is_value, B -> is_base, A -> is_array,
      W -> is_array_values, E -> is_embedded, S -> is_sealed,
      I -> imports, F -> functions, T -> poly_types, M -> members
    """

    package: str
    """Package/module name (e.g., 'nmos', 'is04')."""

    name: str
    """PascalCase type name (e.g., 'NSource', 'NDevice')."""

    is_value: bool = False
    """Value type: wraps a single primitive. JSON is not an object."""

    is_base: bool = False
    """Base value type: does not support literal {} construction."""

    is_array: bool = False
    """Array type: wraps a list of non-value types."""

    is_array_values: bool = False
    """Array of value types."""

    is_embedded: bool = False
    """Embedded type: fields are flattened into parent's JSON scope."""

    is_sealed: bool = False
    """Sealed type: rejects unknown JSON properties during decode."""

    imports: list[str] = field(default_factory=list)
    """Additional import paths needed by this type."""

    functions: list[str] = field(default_factory=list)
    """Additional function definitions to include verbatim."""

    poly_types: list[str] = field(default_factory=list)
    """Polymorphic concrete type names for discriminated union dispatch."""

    predicates: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)
    """Polymorphic type predicates. Maps concrete_type_name to a list of
    (json_key, expected_value, comparison) tuples.
    comparison is one of: "eq" (string/int equality), "neq" (not equal),
    "in" (value in set), "notin" (value not in set), "type_bool",
    "type_int", "type_float", "type_str", "fallback" (always matches)."""

    members: list[MemberDesc] = field(default_factory=list)
    """Member fields of this type."""

    def validate(self) -> None:
        """Validate descriptor constraints."""
        if (self.is_value or self.is_array) and (
            len(self.members) != 1 or self.members[0].name != "value"
        ):
            raise ValueError(
                f"Value/array types must have exactly one member named 'value', "
                f"got {[m.name for m in self.members]}"
            )

        if (self.is_value or self.is_array) and self.is_embedded:
            raise ValueError("Value and array types cannot be embedded")
