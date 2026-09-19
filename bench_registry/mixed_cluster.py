"""One raft cluster, two implementations.

The strongest conformance evidence available: the Python and Rust registries
have to agree on the cluster token, the Hello handshake, the frame format,
every message encoding, the operation encoding and the commit rules. A
disagreement shows up as a cluster that will not elect or will not replicate,
rather than as a suite that passes on each side separately.

Usage:  mixed_cluster.py [layout]
          prr (default)  member 0 Python, members 1 and 2 Rust
          rpp            member 0 Rust, members 1 and 2 Python
          ppp / rrr      homogeneous controls
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

import aiohttp

from nmos.registry.tests._fixtures import make_node

SCRATCH = REPO / "bench_registry" / ".work"
SCRATCH.mkdir(parents=True, exist_ok=True)


def reg_port(index: int) -> int:
    return 8544 + index * 10


def query_port(index: int) -> int:
    return 8543 + index * 10


def start(layout: str) -> list[subprocess.Popen[bytes]]:
    state = REPO / ".raft"
    if state.exists():
        shutil.rmtree(state)
    processes = []
    for index, kind in enumerate(layout):
        command = [str(REPO / "start-registry-raft.sh"), str(index),
                   str(len(layout))]
        if kind == "r":
            command.append("--rust")
        out = (SCRATCH / f"mixed-m{index}.out").open("wb")
        processes.append(subprocess.Popen(
            command, cwd=str(REPO), stdout=out, stderr=subprocess.STDOUT,
        ))
        print(f"  member {index}: {'Rust' if kind == 'r' else 'Python'}")
    return processes


async def post_node(session: aiohttp.ClientSession, index: int,
                    node_id: str) -> tuple[int, str]:
    body = {"type": "node", "data": make_node(node_id)}
    try:
        async with session.post(
            f"http://127.0.0.1:{reg_port(index)}"
            f"/x-nmos/registration/v1.3/resource",
            json=body, timeout=aiohttp.ClientTimeout(total=5),
        ) as response:
            return response.status, (await response.text())[:200]
    except Exception as error:  # noqa: BLE001 - reported, not handled
        return -1, repr(error)


async def get_node(session: aiohttp.ClientSession, index: int,
                   node_id: str) -> int:
    try:
        async with session.get(
            f"http://127.0.0.1:{query_port(index)}"
            f"/x-nmos/query/v1.3/nodes/{node_id}",
            timeout=aiohttp.ClientTimeout(total=5),
        ) as response:
            return response.status
    except Exception:  # noqa: BLE001
        return -1


async def run(layout: str) -> int:
    size = len(layout)
    print(f"layout: {layout}  (position n = member n; p=Python, r=Rust)")
    processes = start(layout)
    try:
        async with aiohttp.ClientSession() as session:
            print("\nwaiting for the cluster to accept a write", end="", flush=True)
            formed = False
            detail = ""
            for _ in range(120):
                status, detail = await post_node(
                    session, 0, str(uuid.uuid4()),
                )
                if status in (200, 201):
                    formed = True
                    break
                print(".", end="", flush=True)
                await asyncio.sleep(1.0)
            print()
            if not formed:
                print(f"FAILED: no write accepted. Last: {detail}")
                return 1
            print("cluster formed: a write was accepted")

            # One Node registered with each member, then every member asked
            # about all of them. Consensus is what makes that work.
            ids = [str(uuid.uuid4()) for _ in range(size)]
            print()
            for index, node_id in enumerate(ids):
                status, detail = await post_node(session, index, node_id)
                print(f"  registered {node_id[:8]} with member {index} "
                      f"-> HTTP {status}")
                if status not in (200, 201):
                    print(f"    {detail}")
                    return 1

            await asyncio.sleep(3.0)
            print()
            print("  every member asked about every Node")
            header = "".join(f"{'m' + str(i):>10}" for i in range(size))
            print(f"    {'asked \\ registered with':<26}{header}")
            failures = 0
            for asked in range(size):
                cells = []
                for owner in range(size):
                    status = await get_node(session, asked, ids[owner])
                    if status != 200:
                        failures += 1
                    cells.append(f"{status:>10}")
                print(f"    member {asked:<20}{''.join(cells)}")

            print()
            if failures:
                print(f"RESULT: FAILED -- {failures} lookups did not return 200")
                for index in range(size):
                    print(f"--- member {index} ---")
                    text = (SCRATCH / f"mixed-m{index}.out").read_text(
                        errors="replace",
                    )
                    for line in text.splitlines():
                        if "registry: At " not in line:
                            print(f"  {line}")
                return 1
            print("RESULT: every member serves every Node -- the mixed "
                  "cluster replicates.")
            return 0
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    sys.exit(asyncio.run(run(sys.argv[1] if len(sys.argv) > 1 else "prr")))
