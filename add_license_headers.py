#!/usr/bin/env python3
# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Add Apache 2.0 license headers to all Python files in nmos-reference.

Usage:
    python3 add_license_headers.py          # dry-run (shows what would change)
    python3 add_license_headers.py --apply  # actually modify files

Skips files that already have the header, __pycache__, .pyc, generated/, and
files under caps/ or sdp/ or pep/ (shared modules with separate licensing).
"""

import argparse
import sys
from pathlib import Path

HEADER = """\
# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0
"""

# Directories to skip (shared modules, generated code, caches)
SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", "caps", "sdp", "pep"}

# Marker to detect if header is already present
MARKER = "SPDX-License-Identifier"

def should_skip(path: Path, root: Path) -> bool:
    """Check if a file should be skipped."""
    rel = path.relative_to(root)
    parts = rel.parts
    # Skip if any parent directory is in SKIP_DIRS
    for part in parts[:-1]:
        if part in SKIP_DIRS:
            return True
    # Skip __init__.py in generated/ directories
    if "generated" in parts:
        return True
    return False

def has_header(content: str) -> bool:
    """Check if file already has a license header (Apache, BSD, or SPDX)."""
    head = content[:500]  # Check first 500 chars only
    return (MARKER in head
            or "SPDX-License-Identifier" in head
            or "All rights reserved" in head)

def add_header(content: str) -> str:
    """Prepend the license header to file content.

    Handles shebang lines (#!) and encoding declarations (# -*- coding)
    by inserting the header AFTER them.
    """
    lines = content.split("\n")
    insert_at = 0

    # Skip shebang
    if lines and lines[0].startswith("#!"):
        insert_at = 1

    # Skip encoding declaration
    if insert_at < len(lines) and lines[insert_at].startswith("# -*-"):
        insert_at += 1

    # Insert header with a blank line separator
    header_lines = HEADER.rstrip("\n").split("\n")

    # If the next line is already blank, don't add an extra one
    rest = lines[insert_at:]
    if rest and rest[0].strip() == "":
        new_lines = lines[:insert_at] + header_lines + rest
    else:
        new_lines = lines[:insert_at] + header_lines + [""] + rest

    return "\n".join(new_lines)

def main() -> None:
    parser = argparse.ArgumentParser(description="Add Apache 2.0 license headers to Python files")
    parser.add_argument("--apply", action="store_true", help="Actually modify files (default: dry-run)")
    parser.add_argument("--root", default=".", help="Root directory to scan")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    py_files = sorted(root.rglob("*.py"))

    added = 0
    skipped_dir = 0
    skipped_exists = 0
    skipped_empty = 0

    for path in py_files:
        if should_skip(path, root):
            skipped_dir += 1
            continue

        content = path.read_text(encoding="utf-8", errors="replace")

        if not content.strip():
            skipped_empty += 1
            continue

        if has_header(content):
            skipped_exists += 1
            continue

        rel = path.relative_to(root)

        if args.apply:
            new_content = add_header(content)
            path.write_text(new_content, encoding="utf-8")
            print(f"  + {rel}")
        else:
            print(f"  would add: {rel}")

        added += 1

    print(f"\n{'Applied' if args.apply else 'Would add'}: {added} files")
    print(f"Skipped (already has header): {skipped_exists}")
    print(f"Skipped (excluded dirs): {skipped_dir}")
    print(f"Skipped (empty): {skipped_empty}")
    print(f"Total .py files scanned: {len(py_files)}")

    if not args.apply and added > 0:
        print(f"\nRe-run with --apply to modify files.")

if __name__ == "__main__":
    main()
