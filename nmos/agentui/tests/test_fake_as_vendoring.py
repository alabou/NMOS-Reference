# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The vendored Authorization Server must stay byte-identical to its source.

``fake-as/`` holds a copy of two files from the ``security/`` project so the
TLS + OAuth 2.0 tutorial runs from a plain checkout of this repository, with
no Keycloak, no Docker, and no second project alongside it.

Duplication is deliberate — the two projects release separately and neither
may depend on the other — but duplication that is allowed to drift is worse
than either a shared package or no copy at all: the tutorial would slowly
stop demonstrating the server the certification suite actually validates
against.

Identical copies are what makes that tractable. Syncing is a file copy
(``./sync-fake-as.sh``) and drift is a comparison, which is what these tests
do. They skip when ``security/`` is not checked out, since that is the normal
state once this repository is released on its own — at which point the
vendored copies are simply the shipped source and there is nothing to
compare them to.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

#: Repository root — ``nmos/agentui/tests/`` is three levels down.
_REPO = Path(__file__).resolve().parents[3]

#: The vendored copies that ship with this repository.
_VENDORED = _REPO / "fake-as"

#: The source of truth, when it is present alongside.
_SOURCE = _REPO.parent / "security"

#: The whole closure: ``ipmx_fake_as`` imports ``ipmx_security_tokens`` as a
#: flat sibling, and that module needs only the standard library plus
#: ``cryptography``. Nothing else from ``security/`` is reachable.
_FILES = ("ipmx_fake_as.py", "ipmx_security_tokens.py")

_needs_source = pytest.mark.skipif(
    not _SOURCE.is_dir(),
    reason="security/ is not checked out; the vendored copies are the source",
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("name", _FILES)
def test_vendored_file_is_present(name: str) -> None:
    """The tutorial cannot start an Authorization Server that is not here."""
    assert (_VENDORED / name).is_file(), (
        f"fake-as/{name} is missing. Run ./sync-fake-as.sh, or fetch it from "
        f"the security/ project — start-fake-as.sh cannot run without it."
    )


def test_vendored_closure_is_complete() -> None:
    """Every project-local import of the vendored files is also vendored.

    A copy that imports a third module from ``security/`` would work in this
    workspace and fail the moment this repository is checked out on its own —
    the exact failure the vendoring exists to prevent, and one that no amount
    of hash-matching would catch.
    """
    vendored = {p.stem for p in _VENDORED.glob("*.py")}
    for name in _FILES:
        source = (_VENDORED / name).read_text()
        for line in source.splitlines():
            stripped = line.strip()
            if not stripped.startswith(("from ipmx", "import ipmx")):
                continue
            module = stripped.split()[1].split(".")[0]
            assert module in vendored, (
                f"fake-as/{name} imports {module!r}, which is not vendored. "
                f"Add it to FILES in sync-fake-as.sh and to _FILES here."
            )


@_needs_source
@pytest.mark.parametrize("name", _FILES)
def test_vendored_copy_matches_security(name: str) -> None:
    """Byte-for-byte, in the direction that matters.

    ``security/`` is the source of truth: it is where the certification
    suite exercises this server hardest. A change made only to the vendored
    copy is the drift this catches.
    """
    vendored, source = _VENDORED / name, _SOURCE / name
    if not source.is_file():
        pytest.skip(f"security/{name} not present")
    assert _digest(vendored) == _digest(source), (
        f"fake-as/{name} differs from security/{name}.\n"
        f"security/ is the source of truth — edit there, then run "
        f"./sync-fake-as.sh. If the vendored copy holds the change you want, "
        f"move it to security/ first so the certification suite gets it too."
    )
