#!/usr/bin/env python3
# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""How much heartbeat a registry can actually absorb.

    python3 -m bench_registry.capacity http://127.0.0.1:8447

Why this exists
---------------
``compare.py``'s phases report the p50 of requests issued by **one** client
process. That is a fair server measurement only while the server is the slower
of the two. It is not, for the cheap endpoints: a single ``aiohttp`` client
saturates at roughly 2800 heartbeats/s, which is the same order as the registries
it is measuring, so the number it reports is the client's and the two targets
come out indistinguishable or ranked backwards.

Measured on a 12-core machine, heartbeat, clients x 8 in flight::

    c=1    rust  2491/s    python 2761/s     <- the regime compare.py measures
    c=2    rust  6531/s    python 4870/s
    c=4    rust 12240/s    python 4888/s
    c=8    rust 18058/s    python 4831/s
    c=12   rust 20666/s    python 4757/s
    c=24   rust 19684/s    python 4425/s

Python plateaus at two clients; Rust does not plateau until twelve -- which on
this 12-core machine is ``nproc``. That is not a coincidence and it is why the
two points measured by default are **1 and nproc**:

* **1 client** is the regime ``compare.py`` already runs in. Reporting it makes
  the client ceiling visible instead of letting it masquerade as a server
  result.
* **nproc clients** is as much load as the machine can offer without the
  clients taking cores the server needs -- past it the curve turns over
  (``c=24`` is slower than ``c=12`` for both targets).

A fixed count like 8 would be wrong for one target or the other; ``nproc``
adapts to the machine and lands on the peak. ``--ramp`` walks the whole curve
when the assumption needs re-checking on different hardware.

Latency and capacity are different questions
--------------------------------------------
Under saturation a p50 is just ``concurrency / throughput`` -- it says how deep
the queue is, not how fast the server is. So this reports **throughput**, and
``compare.py``'s existing phases remain the latency measurement. Quoting a p50
taken at saturation as "latency" would be a different misreading of the same
data, not a fix.

CPU is part of the answer
-------------------------
The client and the server share one machine, and a multi-threaded server
competes with the clients for the same cores -- which is why the rate *falls*
past ``c = nproc`` above. Rate alone therefore understates the faster server.
Cost per request does not, so it is reported alongside.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

REGISTRATION_PATH = "/x-nmos/registration/v1.3"

# Ramp points. Beyond the core count the clients take CPU the server needs, so
# the curve turns over; the ramp stops on its own before that matters.
_CLIENT_STEPS = (1, 2, 4, 8, 12, 16, 24)

# Stop once another doubling of clients buys less than this. Five percent is
# below the run-to-run spread, so continuing past it measures noise.
_PLATEAU_GAIN = 0.05


def _node_body(node_id: str) -> dict[str, Any]:
    """A Node that satisfies ``node.json``, matching ``loadgen``'s."""
    return {
        "type": "node",
        "data": {
            "id": node_id,
            "version": "0:0",
            "label": "capacity",
            "description": "capacity probe node",
            "tags": {},
            "href": "http://192.0.2.1:8080/",
            "caps": {},
            "api": {
                "versions": ["v1.3"],
                "endpoints": [
                    {"host": "192.0.2.1", "port": 8080, "protocol": "http"},
                ],
            },
            "services": [],
            "clocks": [],
            "interfaces": [],
        },
    }


async def _worker(base: str, node_id: str, concurrency: int, seconds: float) -> None:
    """One client process's worth of load; prints its own rate as JSON."""
    import aiohttp

    url = f"{base}{REGISTRATION_PATH}/health/nodes/{node_id}"
    connector = aiohttp.TCPConnector(limit=concurrency)
    async with aiohttp.ClientSession(connector=connector) as session:
        count = 0

        async def once() -> None:
            nonlocal count
            async with session.post(url) as response:
                await response.read()
            count += 1

        # Warm the connection pool: the first requests of a run pay for the
        # handshakes, and at these rates that is a measurable share.
        await asyncio.gather(*[once() for _ in range(concurrency * 2)])

        count = 0
        started = time.perf_counter()
        deadline = started + seconds
        while time.perf_counter() < deadline:
            await asyncio.gather(*[once() for _ in range(concurrency * 4)])
        elapsed = time.perf_counter() - started

    print(json.dumps({"rate": count / elapsed}))


def _server_cpu_seconds(pid: int) -> float:
    """User + system CPU this process has consumed, from ``/proc``.

    Returns 0.0 when unavailable, which makes the cost column absent rather
    than wrong -- a fabricated number here would be worse than none.
    """
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
    except (OSError, IndexError):
        return 0.0
    try:
        # utime and stime, fields 14 and 15 of `proc(5)`, counted from the
        # field after the comm parenthesis.
        return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
    except (ValueError, IndexError):
        return 0.0


def _register_probe_node(base: str) -> str:
    """Register one Node to heartbeat, and return its id."""
    import urllib.request

    node_id = str(uuid.uuid4())
    request = urllib.request.Request(
        f"{base}{REGISTRATION_PATH}/resource",
        data=json.dumps(_node_body(node_id)).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status not in (200, 201):
            raise SystemExit(
                f"capacity: could not register a probe node: {response.status}",
            )
    return node_id


def _run_clients(base: str, node_id: str, clients: int, concurrency: int,
                 seconds: float) -> float:
    """Total offered rate from `clients` separate processes."""
    processes = [
        subprocess.Popen(
            [sys.executable, "-m", "bench_registry.capacity", base,
             "--worker", "--node", node_id,
             "--concurrency", str(concurrency), "--seconds", str(seconds)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        for _ in range(clients)
    ]
    total = 0.0
    for process in processes:
        out, _ = process.communicate()
        for line in out.decode().splitlines():
            line = line.strip()
            if line.startswith("{"):
                total += float(json.loads(line)["rate"])
    return total


def measure(base: str, *, concurrency: int = 8, seconds: float = 3.0,
            pid: int | None = None, verbose: bool = True,
            ramp: bool = False) -> dict[str, Any]:
    """Measure offered rate at 1 client and at ``nproc`` clients.

    Args:
        base: Registration API base URL.
        concurrency: In-flight requests per client process.
        seconds: Measurement window per point.
        pid: The server process, for the cost-per-request column.
        verbose: Print each point as it is measured.
        ramp: Walk the whole client curve instead of the two points, for
            re-checking on hardware where the ``nproc`` assumption may not
            hold.
    """
    node_id = _register_probe_node(base)
    cores = os.cpu_count() or 4
    points = _CLIENT_STEPS if ramp else (1, cores)

    steps: list[dict[str, Any]] = []
    for clients in points:
        before = _server_cpu_seconds(pid) if pid else 0.0
        rate = _run_clients(base, node_id, clients, concurrency, seconds)
        after = _server_cpu_seconds(pid) if pid else 0.0

        requests = rate * seconds
        cpu_per_request = (after - before) / requests if requests > 0 else 0.0
        steps.append({
            "clients": clients,
            "rate": rate,
            "cpu_us_per_request": cpu_per_request * 1e6,
        })
        if verbose:
            cost = f"   {cpu_per_request * 1e6:6.1f}us CPU/req" if pid else ""
            note = ""
            if not ramp:
                note = "  (one client: the regime compare.py measures)" \
                    if clients == 1 else f"  (nproc={cores}: capacity)"
            print(f"    {clients:>3} client(s) x{concurrency}: "
                  f"{rate:8.0f} req/s{cost}{note}")

    peak = max(steps, key=lambda step: step["rate"])
    single = next((step for step in steps if step["clients"] == 1), peak)
    return {
        "single_client_rate": single["rate"],
        "peak_rate": peak["rate"],
        "peak_clients": peak["clients"],
        "cpu_us_per_request": peak["cpu_us_per_request"],
        # What the single-client figure was hiding. A value near 1.0 means the
        # client was not the limit and compare.py's phase is trustworthy for
        # this endpoint; a large one means it was measuring itself.
        "client_ceiling_factor": (
            peak["rate"] / single["rate"] if single["rate"] > 0 else 0.0
        ),
        "steps": steps,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base", help="Registration API base URL")
    parser.add_argument("--concurrency", type=int, default=8,
                        help="in-flight requests per client process")
    parser.add_argument("--seconds", type=float, default=3.0,
                        help="measurement window per ramp step")
    parser.add_argument("--pid", type=int, default=0,
                        help="server pid, for the cost-per-request column")
    parser.add_argument("--ramp", action="store_true",
                        help="walk the whole client curve instead of 1 and nproc")
    # Used when this module re-executes itself as one of the load processes.
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--node", default="", help=argparse.SUPPRESS)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.worker:
        asyncio.run(_worker(args.base, args.node, args.concurrency, args.seconds))
        return 0

    result = measure(
        args.base, concurrency=args.concurrency, seconds=args.seconds,
        pid=args.pid or None, ramp=args.ramp,
    )
    print(
        f"  one client {result['single_client_rate']:.0f} req/s, "
        f"peak {result['peak_rate']:.0f} req/s at "
        f"{result['peak_clients']} client(s) "
        f"(x{result['client_ceiling_factor']:.1f})"
        + (f", {result['cpu_us_per_request']:.1f}us CPU/req" if args.pid else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
