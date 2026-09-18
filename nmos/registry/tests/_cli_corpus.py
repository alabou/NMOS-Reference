# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Record ``nmos_registry.py``'s command line, for the Rust CLI to match.

The two implementations' flags must be **identical**: an operator, a launch
script and every ``start-registry*.sh`` in this repository all move between them
without changing a word, and the plan's M6 gate runs those scripts against the
Rust binary with only the executable path swapped. A flag that differs in
spelling, default, type or arity breaks that silently — the script still runs,
it just configures something else.

Transcribing 69 flags by hand and reading them back is exactly the sort of
comparison a person does badly. So this dumps what ``argparse`` actually holds
— every option string, its default, whether it takes a value, whether it may
repeat, and any ``choices`` — and the Rust suite asserts its own parser agrees.

Regenerate with::

    python -m nmos.registry.tests._cli_corpus

``test_cli_corpus.py`` fails when the recording is stale, which is the half that
matters: without it, adding a flag to Python leaves the Rust side agreeing with
a command line that no longer exists.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import nmos_registry

OUTPUT = (
    Path(__file__).resolve().parents[3]
    / "rust"
    / "crates"
    / "nmos-registry-bin"
    / "tests"
    / "cli_flags.json"
)


def _parser() -> argparse.ArgumentParser:
    """The real parser, built the way ``parse_args`` builds it.

    Reached by calling ``parse_args`` with a sentinel that makes argparse raise
    before it can exit the process: the factory is inline in ``parse_args`` and
    is not separately exposed, and copying its body here would defeat the point
    of recording the real thing.
    """
    captured: dict[str, argparse.ArgumentParser] = {}
    original = argparse.ArgumentParser.parse_args

    def capture(self: argparse.ArgumentParser, *args: Any, **kwargs: Any) -> Any:
        captured["parser"] = self
        return original(self, *args, **kwargs)

    argparse.ArgumentParser.parse_args = capture  # type: ignore[method-assign]
    try:
        nmos_registry.parse_args([])
    finally:
        argparse.ArgumentParser.parse_args = original  # type: ignore[method-assign]
    return captured["parser"]


def _kind(action: argparse.Action) -> str:
    """How the flag behaves, in terms both parsers share."""
    if isinstance(action, argparse._StoreTrueAction):  # noqa: SLF001
        return "flag"
    if isinstance(action, argparse._AppendAction):  # noqa: SLF001
        return "append"
    if isinstance(action, argparse._HelpAction):  # noqa: SLF001
        return "help"
    return "value"


def _group_of(parser: argparse.ArgumentParser, action: argparse.Action) -> str:
    for group in parser._action_groups:  # noqa: SLF001
        if action in group._group_actions:  # noqa: SLF001
            return str(group.title)
    return ""


def build() -> dict[str, Any]:
    parser = _parser()
    flags = []
    for action in parser._actions:  # noqa: SLF001
        kind = _kind(action)
        if kind == "help":
            continue
        default = action.default
        # Only JSON-representable defaults are recorded; `None` stays `None`.
        if not isinstance(default, (str, int, float, bool, type(None))):
            default = str(default)
        flags.append(
            {
                "options": sorted(action.option_strings),
                "dest": action.dest,
                "kind": kind,
                "default": default,
                "choices": sorted(action.choices) if action.choices else None,
                "group": _group_of(parser, action),
            },
        )
    flags.sort(key=lambda f: f["dest"])
    return {"prog": "nmos_registry.py", "flags": flags}


def main() -> None:
    corpus = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(corpus, indent=1, sort_keys=True) + "\n")
    groups: dict[str, int] = {}
    for flag in corpus["flags"]:
        groups[flag["group"]] = groups.get(flag["group"], 0) + 1
    print(f"{len(corpus['flags'])} flags -> {OUTPUT}")
    for group, count in sorted(groups.items()):
        print(f"  {group}: {count}")


if __name__ == "__main__":
    main()
