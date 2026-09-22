# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Every launcher that starts a distributed registry names its backend.

``--distributedBackend`` defaults to ``raft``, so a script that passes
``--distributed`` and then a family of ``--etcd*`` flags is refused at startup
rather than quietly coming up on the wrong storage layer. That refusal is the
real protection; this file is the one that catches the mistake before anyone
runs the script, and catches it for scripts nobody has run recently.

Cheap, permanent, and it fails for the right reason: a script added later that
forgets the selector names itself here, rather than surfacing as an operator
reading a CONFIG error they did not expect.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

# Scripts that merely *mention* --distributed in a comment or help text are not
# launching anything. Only a script that passes it as an argument has to say
# which backend it means.
_CONTINUATIONS = ("\\", "^")


def _launchers() -> list[Path]:
    return sorted(
        path for pattern in ("*.sh", "*.bat")
        for path in REPO_ROOT.glob(pattern)
    )


def _passes_distributed(text: str) -> bool:
    """Whether the script passes ``--distributed`` as an argument.

    Distinguished from a mention by position: an argument sits alone on its
    line, or ends the line with a shell/batch continuation. A comment or a
    sentence has it mid-prose.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("REM "):
            continue
        if stripped in {"--distributed", *(
            f"--distributed {c}" for c in _CONTINUATIONS
        )}:
            return True
    return False


def test_at_least_one_launcher_is_checked() -> None:
    """Guard the guard: a glob that matched nothing would pass vacuously."""
    assert _launchers(), "no launch scripts found to check"


@pytest.mark.parametrize(
    "script", _launchers(), ids=lambda p: p.name,
)
def test_a_distributed_launcher_names_its_backend(script: Path) -> None:
    text = script.read_text(encoding="utf-8")
    if not _passes_distributed(text):
        return
    assert "--distributedBackend" in text, (
        f"{script.name} passes --distributed without --distributedBackend. "
        f"The default is raft, so this script would start a raft member while "
        f"its --etcd* flags were read by nothing at all."
    )
