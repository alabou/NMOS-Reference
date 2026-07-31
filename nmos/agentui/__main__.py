# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Command-line entry point: ``python -m nmos.agentui``.

Option names follow ``nmos_node.py``'s camelCase convention so the two tools read
alike on the same command line.

This never starts a node. ``start-node*.sh`` is the launch contract, and it drives
a configuration matrix that produces materially different interfaces — so which
configuration a run exercises stays the operator's choice, made by which script
they ran.

Run:

.. code-block:: bash

    export NMOS_CONTROLLER_ADMIN_PASSWORD=admin
    export PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright"
    .venv/bin/python -m nmos.agentui --listScenarios
    .venv/bin/python -m nmos.agentui --scenario attach-and-look
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .attach import DEFAULT_ARTIFACTS_ROOT, attach_controller
from .core.tutorial import Tutorial
from .enums import TlsPolicy
from .errors import AgentUiError, DependencyMissing
from .scenarios import SCENARIOS


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m nmos.agentui",
        description=(
            "Drive the embedded NMOS Controller UI through a real browser, acting "
            "only through the affordances a signed-in operator has, and write a "
            "screenshot-and-text journal of every step. Attaches to a node you "
            "already started; never launches one."
        ),
    )
    parser.add_argument("--scenario", default="",
                        help="Scenario to run (see --listScenarios).")
    parser.add_argument("--listScenarios", dest="list_scenarios",
                        action="store_true",
                        help="List the available scenarios and exit.")
    parser.add_argument("--controlPort", dest="control_port", type=int, default=0,
                        help="Disambiguate when several nodes serve a "
                             "Controller UI (0 = auto, error if ambiguous).")
    parser.add_argument("--artifactsRoot", dest="artifacts_root", default="",
                        help=f"Where run journals are written "
                             f"(default: {DEFAULT_ARTIFACTS_ROOT}).")
    parser.add_argument("--headed", action="store_true",
                        help="Show the browser window instead of running headless.")
    parser.add_argument("--stepTimeoutMs", dest="step_timeout_ms", type=int,
                        default=15_000,
                        help="How long any single wait may take (default 15000).")
    parser.add_argument("--tutorial", action="store_true",
                        help="Also write tutorial.md: the same run told as a "
                             "step-by-step lesson, with the internals collapsed "
                             "behind expandable sections.")
    parser.add_argument("--pinChain", dest="pin_chain", action="store_true",
                        help="For a TLS node, pin every certificate in the chain "
                             "rather than only the leaf. There is deliberately no "
                             "option to disable verification.")
    return parser


def _list_scenarios() -> int:
    """Print the scenario table."""
    width = max(len(name) for name in SCENARIOS)
    print("Available scenarios:\n")
    for name, scenario in SCENARIOS.items():
        marker = "  [MAKES CHANGES]" if scenario.mutating else ""
        print(f"  {name:<{width}}  {scenario.description}{marker}")
    print(
        "\nScenarios marked [MAKES CHANGES] issue real IS-05/IS-11 calls and "
        "perform no teardown:\nthey leave the rig in the state they reached, for "
        "inspection."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run a scenario and report where its journal landed."""
    args = build_parser().parse_args(argv)

    if args.list_scenarios:
        return _list_scenarios()

    if not args.scenario:
        print("error: --scenario is required (or use --listScenarios)",
              file=sys.stderr)
        return 2
    scenario = SCENARIOS.get(args.scenario)
    if scenario is None:
        print(f"error: unknown scenario {args.scenario!r}. Known: "
              f"{', '.join(SCENARIOS)}", file=sys.stderr)
        return 2

    root = Path(args.artifacts_root) if args.artifacts_root else None
    policy = TlsPolicy.PIN_CHAIN_SPKI if args.pin_chain else TlsPolicy.PIN_LEAF_SPKI

    tutorial = None
    if args.tutorial:
        tutorial = Tutorial(
            Path("."),          # replaced with the run directory on attach
            title=f"Tutorial — {scenario.description.removeprefix('TUTORIAL: ')}",
            goal=scenario.description.removeprefix("TUTORIAL: "),
            audience="No prior knowledge of this Controller is assumed. Every "
                     "step is something you do in the browser.",
        )

    try:
        with attach_controller(
            scenario=scenario.name,
            control_port=args.control_port or None,
            artifacts_root=root,
            tls=policy,
            headless=not args.headed,
            step_timeout_ms=args.step_timeout_ms,
            mutating=scenario.mutating,
            tutorial=tutorial,
        ) as session:
            journal = session.journal
            try:
                scenario.run(session)
            finally:
                # Reported even on failure: the journal is the deliverable, and a
                # failed run's journal is usually the one worth reading.
                print(f"\njournal: {journal.markdown_path}")
                print(f"manifest: {journal.manifest_path}")
                if tutorial is not None:
                    print(f"tutorial: {journal.root / 'tutorial.md'}")
    except DependencyMissing as exc:
        print(f"error: {exc.msg}", file=sys.stderr)
        for remedy in exc.remedies:
            print(f"  fix: {remedy}", file=sys.stderr)
        return 3
    except AgentUiError as exc:
        # Deliberately not a traceback: these errors are already phrased for a
        # reader, and the journal holds the evidence.
        print(f"error: {type(exc).__name__}: {exc.msg}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
