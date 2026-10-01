# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Which registry a process-level test spawns: the Python, or the Rust binary.

The two registries take the same command line -- ``cli_parity.rs`` in the Rust
binary's tests enforces it, flag by flag -- so a rig that spawns one can spawn
the other by changing only how the process starts: ``python nmos_registry.py``
or ``rust/target/release/nmos-registry``. Everything else, the flags, the
ports, the state directory and the faults, is the rig's and is the same for
both.

Every process-level suite is therefore parametrized over both, through the
``implementation`` fixture: ``test_x[python]`` and ``test_x[rust]``. The Rust
half **skips, never silently passes**, when no binary is built, and says how to
build one; ``NMOS_RUST_REGISTRY`` points it at a binary elsewhere, as it does
the launchers. The lookup mirrors ``registry-runtime.sh`` exactly, debug
fallback and warning included, so what a test drives is what an operator's
launcher would have run.
"""

from __future__ import annotations

import enum
import os
import sys
import warnings
from collections.abc import Mapping
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
RUST_RELEASE = REPO_ROOT / "rust" / "target" / "release" / "nmos-registry"
RUST_DEBUG = REPO_ROOT / "rust" / "target" / "debug" / "nmos-registry"
BUILD_HINT = "cd rust && cargo build --release -p nmos-registry-bin"
ENVIRONMENT_VARIABLE = "NMOS_RUST_REGISTRY"


class Implementation(enum.Enum):
    """One of the two registries, as a test id names it."""

    PYTHON = "python"
    RUST = "rust"


def find_rust_registry() -> Path:
    """The Rust registry binary, or a skip that says how to get one.

    ``registry-runtime.sh``'s order: the variable, which must name an
    executable; the release build; the debug build, with the launcher's
    warning, because a rig quietly ten times slower is worse than one that
    refuses to start; else a skip with the build hint.
    """
    named = os.environ.get(ENVIRONMENT_VARIABLE)
    if named:
        path = Path(named)
        if not path.is_file() or not os.access(path, os.X_OK):
            pytest.skip(f"{ENVIRONMENT_VARIABLE}={named} is not executable")
        return path
    if RUST_RELEASE.is_file() and os.access(RUST_RELEASE, os.X_OK):
        return RUST_RELEASE
    if RUST_DEBUG.is_file() and os.access(RUST_DEBUG, os.X_OK):
        warnings.warn(
            f"using the DEBUG build at {RUST_DEBUG}: it is much slower than "
            f"release; build with `{BUILD_HINT}`",
            RuntimeWarning, stacklevel=2,
        )
        return RUST_DEBUG
    pytest.skip(
        f"no Rust registry is built: expected {RUST_RELEASE}; build it with "
        f"`{BUILD_HINT}`, or point {ENVIRONMENT_VARIABLE} at a binary elsewhere",
    )


def registry_command(implementation: Implementation) -> list[str]:
    """How a registry process starts; the flags follow, the same for both."""
    if implementation is Implementation.PYTHON:
        return [sys.executable, str(REPO_ROOT / "nmos_registry.py")]
    return [str(find_rust_registry())]


def registry_environment(
    implementation: Implementation, base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """The environment a registry process gets, over ``base``.

    ``PYTHONPATH`` for the Python only -- the binary needs none -- and
    ``NO_COLOR`` for the Rust, whose console sink colours its output otherwise,
    and a rig's captured ``.out`` file is read by people.
    """
    environment = dict(base or {})
    if implementation is Implementation.PYTHON:
        environment["PYTHONPATH"] = str(REPO_ROOT)
    else:
        environment.pop("PYTHONPATH", None)
        environment["NO_COLOR"] = "1"
    return environment


def launcher_flags(implementation: Implementation) -> list[str]:
    """What a launcher takes to run the Rust registry: ``--rust``, or nothing."""
    return ["--rust"] if implementation is Implementation.RUST else []


IMPLEMENTATIONS = [
    pytest.param(Implementation.PYTHON, id="python"),
    pytest.param(Implementation.RUST, id="rust"),
]


@pytest.fixture(scope="module", params=IMPLEMENTATIONS)
def implementation(request: pytest.FixtureRequest) -> Implementation:
    """Each process-level test, once per registry implementation.

    Module scope: a module's tests run all ``[python]`` and then all
    ``[rust]``, which a module-scoped rig on fixed ports depends on, and a
    skip for the Rust half is decided once and reported for every test in it.
    """
    chosen: Implementation = request.param
    if chosen is Implementation.RUST:
        if sys.platform == "win32":
            pytest.skip("--rust is not available on native Windows")
        find_rust_registry()
    return chosen
