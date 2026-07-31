# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Wiring a run together: discover, launch, sign in, hand over a session.

The ordering here is deliberate at three points.

**Discovery happens before the browser starts.** A node that cannot be found is not
worth a 656 MB browser launch, and a missing dependency should be reported before
anything else has happened.

**The entry navigation happens exactly once, here, outside any step.** It is the
equivalent of a person typing an address, and the launcher latches it so a second
attempt fails. Everything after it is reached by clicking.

**The manifest is written in a ``finally``.** A run that crashes is exactly the run
whose provenance and fidelity ledger someone wants to read.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .apps.nmos_controller import discovery
from .apps.nmos_controller.adapter import ControllerAdapter
from .apps.nmos_controller.session import ControllerSession
from .apps.nmos_controller.trace_join import TraceJoiner
from .core.journal import Journal
from .core.tutorial import Tutorial
from .core.step import Recorder
from .core.surface import Surface
from .deps import describe_environment, require_playwright
from .enums import TlsPolicy

#: Default location for run artifacts, relative to the project root.
DEFAULT_ARTIFACTS_ROOT = Path("artifacts") / "agentui"


@contextmanager
def attach_controller(
    *,
    scenario: str,
    control_port: int | None = None,
    admin_password_env: str = discovery.PASSWORD_ENV,
    artifacts_root: Path | None = None,
    tls: TlsPolicy = TlsPolicy.PIN_LEAF_SPKI,
    headless: bool = True,
    step_timeout_ms: int = 15_000,
    mutating: bool = False,
    tutorial: Tutorial | None = None,
) -> Iterator[ControllerSession]:
    """Attach to a running node's Controller UI and yield a signed-in session.

    ``tls`` is the *fallback* policy. When the certificate's own DNS name resolves
    to the discovered address, the cleaner name-based path is chosen instead and no
    browser flag is used at all.

    ``mutating`` is recorded in the manifest so a reader can tell whether the run
    changed anything. It is the caller's declaration rather than something inferred,
    because a scenario knows its intent and inference would sometimes be wrong in
    the direction that matters.
    """
    # Both halves of the optional dependency, checked before anything else so a
    # half-installed environment fails with one actionable message.
    require_playwright()

    found = discovery.discover(control_port=control_port, prefer_policy=tls)
    credentials = discovery.read_password(admin_password_env)
    adapter = ControllerAdapter(found)

    root = artifacts_root if artifacts_root is not None else DEFAULT_ARTIFACTS_ROOT
    journal = Journal(root, scenario=scenario,
                      title=f"Controller UI walkthrough — {scenario}")
    joiner = TraceJoiner(found.debug_log_path)

    # Imported here rather than at module scope: this module is part of the public
    # surface and must stay importable with no browser dependency present.
    from .driver.launcher import BrowserRun

    run = BrowserRun(pin=found.pin, headless=headless)
    surface = run.start()
    recorder = Recorder(surface, journal, adapter,
                        trace_resolver=joiner.slice_for)
    session = ControllerSession(surface, recorder, adapter, credentials,
                                step_timeout_ms=step_timeout_ms)
    if tutorial is not None:
        tutorial.root = journal.root
        session.start_tutorial(tutorial)

    error = ""
    tutorial_summary = ""
    try:
        run.enter(found.entry_url)
        session.sign_in()
        session.check_preconditions()
        yield session
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        info = run.info()
        environment = dict(describe_environment())
        environment.update({
            "browser_version": info.browser_version,
            "chromium_args": " ".join(info.chromium_args) or "(none)",
            "headless": str(info.headless),
            "viewport": info.viewport,
        })

        target = found.target.to_json()
        target["tls_detail"] = found.pin.to_json()
        target["debug_log_path"] = found.debug_log_path or "(tracing disabled)"
        if joiner.rotated:
            target["debug_log_rotated"] = "true"

        if tutorial is not None:
            # Written before the browser closes so a crashed run still leaves the
            # lessons it did reach, and built from the journal's own records so the
            # internals shown are the calls that actually happened.
            tutorial.write(
                records=[r.to_json() for r in journal.records],
                summary=tutorial_summary,
            )

        journal.finalise(
            target=target,
            environment=environment,
            fidelity=recorder.ledger,
            sse=session.sse_verdict,
            mutating=mutating,
            debug_tracing=found.debug_tracing,
            controller_js_version=_js_version(surface),
            error=error,
        )
        run.close()


def _js_version(surface: Surface) -> str:
    """Extract the application's script version from its console beacon.

    Recorded because the assets are cache-busted independently of the version the
    script reports, so a mismatch is possible — and a stale script would invalidate
    every wait signal this driver relies on.
    """
    for record in surface.console_history():
        if "nmos-controller.js v" in record.text:
            _, _, tail = record.text.partition("nmos-controller.js v")
            return tail.split("]")[0].strip()
    return ""
