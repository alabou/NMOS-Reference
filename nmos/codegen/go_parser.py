# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Parse Go source files and extract TypeDesc definitions.

Reads Go source code containing T_Desc struct literals and converts them
to Python TypeDesc/MemberDesc instances.

Usage:
    python -m nmos.codegen.go_parser /path/to/file.go
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from nmos.codegen.descriptors import MemberDesc, TypeDesc
from nmos.codegen.namespaces import NAMESPACE_MAP as _NAMESPACE_MAP


_ENUM_MAP: dict[str, str] = {
    "enums.DeviceGeneric": 'EnumRegistry.get("urn:x-nmos:device:generic")',
    "enums.Unconstrained": 'EnumRegistry.get("unconstrained")',
    "enums.Unknown": 'EnumRegistry.get("unknown")',
    "enums.Progressive": 'EnumRegistry.get("progressive")',
    "enums.SDR": 'EnumRegistry.get("SDR")',
    "enums.FormatVideo": 'EnumRegistry.get("urn:x-nmos:format:video")',
    "enums.FormatAudio": 'EnumRegistry.get("urn:x-nmos:format:audio")',
    "enums.FormatData": 'EnumRegistry.get("urn:x-nmos:format:data")',
    "enums.FormatMux": 'EnumRegistry.get("urn:x-nmos:format:mux")',
}


def _go_default_to_python(d: str) -> str:
    """Convert Go default value syntax to Python."""
    d = d.strip()
    # Remove backticks
    if d.startswith("`") and d.endswith("`"):
        d = d[1:-1]
    # Remove outer quotes and unescape inner quotes
    elif d.startswith('"') and d.endswith('"'):
        d = d[1:-1].replace('\\"', '"')
    d = d.strip("'")
    if d == "nil":
        return "None"
    if d == "false":
        return "False"
    if d == "true":
        return "True"
    # Numeric
    if re.match(r"^-?\d+(\.\d+)?$", d):
        return d
    # Enum reference like enums.DeviceGeneric -> EnumRegistry.get(...)
    if d.startswith("enums."):
        return _ENUM_MAP.get(d, f'EnumRegistry.get("{d.split(".", 1)[1]}")')
    # String literal
    if d and not d.startswith('"'):
        return f'"{d}"'
    return d


def _resolve_json_key(raw: str) -> str:
    """Resolve Go JSON key expressions like H26x_NAMESPACE + 'key'."""
    raw = raw.strip()
    # Handle string concatenation: NAMESPACE + "key"
    for ns_const, ns_value in _NAMESPACE_MAP.items():
        if ns_const in raw:
            rest = raw.replace(ns_const, "").strip().lstrip("+").strip().strip('"')
            return ns_value + rest
    return raw.strip('"')


def parse_go_file(filepath: str) -> list[TypeDesc]:
    """Parse a Go file.go and return all TypeDesc definitions."""
    text = Path(filepath).read_text()

    types: list[TypeDesc] = []

    # Find each &gen.T_Desc{ and then brace-match to find the closing }
    marker = "&gen.T_Desc{"
    pos = 0
    while True:
        idx = text.find(marker, pos)
        if idx == -1:
            break

        # Find variable name before :=
        line_start = text.rfind("\n", 0, idx) + 1
        pre = text[line_start:idx].strip()
        var_match = re.match(r'(\w+)\s*:=\s*', pre)
        var_name = var_match.group(1) if var_match else "unknown"

        # Brace-match from the opening {
        brace_start = idx + len(marker) - 1  # points to {
        block = _extract_brace_block(text, brace_start)
        if block:
            desc = _parse_tdesc_block(block)
            if desc:
                types.append(desc)

        pos = brace_start + len(block) if block else idx + len(marker)

    return types


def _extract_brace_block(text: str, start: int) -> str:
    """Extract content between matching braces, handling nesting."""
    if text[start] != "{":
        return ""
    depth = 0
    in_string = False
    in_backtick = False
    i = start
    while i < len(text):
        c = text[i]
        if in_backtick:
            if c == "`":
                in_backtick = False
        elif in_string:
            if c == "\\" and i + 1 < len(text):
                i += 1  # skip escaped char
            elif c == '"':
                in_string = False
        else:
            if c == "`":
                in_backtick = True
            elif c == '"':
                in_string = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start + 1 : i]
        i += 1
    return ""


def _extract_brace_block_str(text: str, start: int) -> str:
    """Same as _extract_brace_block but works on a string offset."""
    return _extract_brace_block(text, start)


def _parse_tdesc_block(block: str) -> TypeDesc | None:
    """Parse a T_Desc block content into a TypeDesc."""
    # Extract fields
    package = _extract_string(block, "P")
    name = _extract_string(block, "N")

    if not name:
        return None

    # Extract top-level flags. Only match flags BEFORE the M: member list
    # to avoid false positives from member-level E:true, A:true etc.
    m_start = block.find("M: []gen.M_Desc{")
    top_block = block[:m_start] if m_start >= 0 else block

    is_value = "V: true" in top_block or "V:true" in top_block
    is_base = "B: true" in top_block or "B:true" in top_block
    is_array = "A: true" in top_block or "A:true" in top_block
    is_array_values = "W: true" in top_block or "W:true" in top_block
    is_embedded = "E: true" in top_block or "E:true" in top_block
    is_sealed = "S: true" in top_block or "S:true" in top_block

    # Extract polymorphic types T: []string{...}
    poly_types: list[str] = []
    poly_match = re.search(r'T:\s*\[\]string\{([^}]+)\}', block)
    if poly_match:
        for t in poly_match.group(1).split(","):
            t = t.strip().strip('"')
            if t:
                poly_types.append(t)

    # Extract members M: []gen.M_Desc{ ... }
    members: list[MemberDesc] = []
    m_idx = block.find("M: []gen.M_Desc{")
    if m_idx == -1:
        m_idx = block.find("M: []gen.M_Desc {")
    if m_idx >= 0:
        # Find the opening { of the M_Desc slice
        brace_pos = block.index("{", m_idx + len("M: []gen.M_Desc"))
        m_content = _extract_brace_block_str(block, brace_pos)
        # Now find each member: {N: ..., T: ..., ...}
        pos2 = 0
        while True:
            mb_start = m_content.find("{", pos2)
            if mb_start == -1:
                break
            mb_inner = _extract_brace_block_str(m_content, mb_start)
            if mb_inner:
                member = _parse_mdesc(mb_inner)
                if member:
                    members.append(member)
                pos2 = mb_start + len(mb_inner) + 2
            else:
                break

    return TypeDesc(
        package=package or "nmos",
        name=name,
        is_value=is_value,
        is_base=is_base,
        is_array=is_array,
        is_array_values=is_array_values,
        is_embedded=is_embedded,
        is_sealed=is_sealed,
        poly_types=poly_types if poly_types else [],
        members=members,
    )


def _parse_mdesc(content: str) -> MemberDesc | None:
    """Parse a single M_Desc content."""
    name = _extract_string(content, "N")
    type_name = _extract_string(content, "T")
    json_key_raw = _extract_string(content, "J")

    if not name or not type_name:
        return None

    json_key = _resolve_json_key(json_key_raw) if json_key_raw else ""

    optional = "O: true" in content or "O:true" in content
    embedded = "E: true" in content or "E:true" in content
    # Extract default — handle escaped quotes in Go strings like "\"IP OUT/IN\""
    default = ""
    d_match = re.search(r'D:\s*(`[^`]*`|"(?:[^"\\]|\\.)*")', content)
    if d_match:
        default = _go_default_to_python(d_match.group(1))

    # Extract assertion name (A: "CheckXxx")
    assertion = ""
    a_match = re.search(r'A:\s*"([^"]*)"', content)
    if a_match:
        assertion = a_match.group(1)

    # Map Go types to Python types
    type_name = _map_go_type(type_name)

    return MemberDesc(
        name=name,
        type_name=type_name,
        json_key=json_key,
        optional=optional,
        embedded=embedded,
        default=default,
        assertion=assertion,
    )


def _map_go_type(go_type: str) -> str:
    """Map Go type names to Python equivalents."""
    # Strip Go package prefixes (e.g., "nmos.NString" -> "NString")
    if "." in go_type and not go_type.startswith("[]") and not go_type.startswith("map["):
        parts = go_type.rsplit(".", 1)
        if parts[0] in ("nmos", "enums", "gen", "is04", "is05", "is11", "is12"):
            go_type = parts[1]

    # Array types: []XxxValue -> list[XxxValue]
    if go_type.startswith("[]"):
        inner = _map_go_type(go_type[2:])
        return f"list[{inner}]"
    # Pointer types: *XxxValue -> XxxValue (Python has no pointers)
    if go_type.startswith("*"):
        return _map_go_type(go_type[1:])
    # Map types: map[X]Y -> dict[X, Y]
    map_match = re.match(r'map\[([^\]]+)\](.+)', go_type)
    if map_match:
        k, v = map_match.group(1), map_match.group(2)
        k = _map_go_type(k.replace("enums.EnumId", "EnumId"))
        v = _map_go_type(v)
        if k.startswith("*"):
            k = k[1:]
        if v.startswith("*"):
            v = v[1:]
        return f"dict[{k}, {v}]"
    # Go primitives
    if go_type == "interface{}":
        return "object"
    if go_type == "Polymorphic":
        return "object"
    if go_type == "int64":
        return "int"
    if go_type == "float64":
        return "float"
    if go_type == "bool":
        return "bool"
    if go_type == "string":
        return "str"
    if go_type == "[2]string":
        return "tuple[str, str]"
    if go_type == "enums.EnumId":
        return "EnumId"
    if go_type == "time.Time":
        return "datetime"
    if go_type == "url.URL":
        return "str"  # URLs as strings in Python
    return go_type


def _extract_string(content: str, field: str) -> str | None:
    """Extract a string field value like N: 'NSource' from a Go struct literal.

    Also handles namespace concatenation patterns like:
      J: SYNCMEDIA_NAMESPACE + "synchronous_media"
    """
    # Simple quoted string: J: "some_key"
    pattern = re.compile(rf'{field}:\s*"([^"]*)"')
    m = pattern.search(content)
    if m:
        return m.group(1)
    # Namespace + string: J: SOME_NAMESPACE + "some_key"
    ns_pattern = re.compile(rf'{field}:\s*(\w+)\s*\+\s*"([^"]*)"')
    m = ns_pattern.search(content)
    if m:
        return m.group(1) + ' + "' + m.group(2) + '"'  # pass through for _resolve_json_key
    return None


def generate_python_defs(types: list[TypeDesc], module_name: str) -> str:
    """Generate a Python definitions module from parsed types."""
    lines: list[str] = []
    lines.append(f'"""Type definitions auto-generated from Go source. Module: {module_name}."""')
    lines.append("")
    lines.append("from __future__ import annotations")
    lines.append("")
    lines.append("from nmos.codegen.descriptors import MemberDesc, TypeDesc")
    lines.append("")

    var_names: list[str] = []

    for t in types:
        var = _to_snake_var(t.name)
        var_names.append(var)

        lines.append(f"{var} = TypeDesc(")
        lines.append(f'    package="{t.package}",')
        lines.append(f'    name="{t.name}",')
        if t.is_value:
            lines.append(f"    is_value=True,")
        if t.is_base:
            lines.append(f"    is_base=True,")
        if t.is_array:
            lines.append(f"    is_array=True,")
        if t.is_array_values:
            lines.append(f"    is_array_values=True,")
        if t.is_embedded:
            lines.append(f"    is_embedded=True,")
        if t.is_sealed:
            lines.append(f"    is_sealed=True,")
        if t.poly_types:
            lines.append(f"    poly_types={t.poly_types!r},")

        if t.members:
            lines.append(f"    members=[")
            for m in t.members:
                parts = [f'name="{m.name}"', f'type_name="{m.type_name}"']
                if m.json_key:
                    parts.append(f'json_key="{m.json_key}"')
                if m.optional:
                    parts.append("optional=True")
                if m.embedded:
                    parts.append("embedded=True")
                if m.default:
                    # Default is already a Python literal (e.g., "None", "False", "1", '"enums.X"')
                    parts.append(f"default={m.default!r}")
                if m.assertion:
                    parts.append(f'assertion="{m.assertion}"')
                lines.append(f"        MemberDesc({', '.join(parts)}),")
            lines.append(f"    ],")

        lines.append(")")
        lines.append("")

    lines.append("ALL_TYPES = [")
    for v in var_names:
        lines.append(f"    {v},")
    lines.append("]")
    lines.append("")

    return "\n".join(lines)


def _to_snake_var(name: str) -> str:
    """Convert PascalCase type name to snake_case variable name."""
    result: list[str] = []
    for i, c in enumerate(name):
        if c.isupper() and i > 0 and not name[i - 1].isupper():
            result.append("_")
        result.append(c.lower())
    return "".join(result)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m nmos.codegen.go_parser <go_file> [module_name]")
        sys.exit(1)

    go_file = sys.argv[1]
    module_name = sys.argv[2] if len(sys.argv) > 2 else Path(go_file).parent.name

    types = parse_go_file(go_file)
    print(f"Parsed {len(types)} types from {go_file}")

    output = generate_python_defs(types, module_name)
    print(output)


if __name__ == "__main__":
    main()
