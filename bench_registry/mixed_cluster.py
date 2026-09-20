"""One cluster, two implementations.

The strongest conformance evidence available: a disagreement shows up as a
cluster that will not form or will not replicate, rather than as a suite that
passes on each side separately.

What is being proven differs by backend, and the difference matters when
reading the result.

``raft`` -- the registries *are* the cluster. They have to agree on the
cluster token, the Hello handshake, the frame format, every message encoding,
the operation encoding and the commit rules. Consensus itself is under test.

``etcd`` -- the registries are *clients* of one etcd cluster, so consensus is
etcd's and is not under test. What is under test is everything the two
implementations must agree on to share a database: the key layout, the value
envelope, the lease model, the transaction shapes and the watch semantics. A
Node written by one implementation has to be readable, updatable and
deletable by the other, byte for byte.

Usage:  mixed_cluster.py [layout] [--backend raft|etcd]
          prr (default)  member 0 Python, members 1 and 2 Rust
          rpp            member 0 Rust, members 1 and 2 Python
          ppp / rrr      homogeneous controls
"""
from __future__ import annotations

import argparse
import asyncio
import enum
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


class Backend(enum.Enum):
    """Which distributed backend the members run, and how to launch them.

    The port bases are not a choice made here -- they are what the launcher
    scripts already assign, and they differ so that a raft rig and an etcd rig
    can be up at the same time without colliding. Reading them off the script
    that owns them is what keeps this harness from drifting away from the way
    the registries are actually started.
    """

    RAFT = ("raft", "start-registry-raft.sh", 8544, 8543)
    ETCD = ("etcd", "start-registry-dist.sh", 8444, 8443)

    def __init__(self, label: str, launcher: str,
                 reg_base: int, query_base: int) -> None:
        self.label = label
        self.launcher = launcher
        self.reg_base = reg_base
        self.query_base = query_base

    def reg_port(self, index: int) -> int:
        return self.reg_base + index * 10

    def query_port(self, index: int) -> int:
        return self.query_base + index * 10


def start_etcd(members: int) -> None:
    """Bring up the etcd cluster the members will share.

    Wiped first. A previous run's Nodes live in etcd's data directories, and
    this harness asserts on exactly which Nodes every member can see -- so a
    leftover would either mask a replication failure or invent one. The raft
    path does the same thing by removing ``.raft``.

    ``--detach`` because ``up`` otherwise supervises the members in the
    foreground and would never return to us.
    """
    run = [sys.executable, str(REPO / "etcd_cluster.py"),
           "--members", str(members)]
    # `down` before `wipe` because wiping the data directory underneath a
    # running member is how you get a cluster that is up but has forgotten
    # who it is.
    subprocess.run([*run, "down"], cwd=str(REPO), capture_output=True)
    subprocess.run([*run, "wipe"], cwd=str(REPO), capture_output=True)
    result = subprocess.run(
        [*run, "up", "--detach"], cwd=str(REPO), capture_output=True,
    )
    if result.returncode != 0:
        raise SystemExit(
            "etcd cluster did not start:\n"
            + result.stdout.decode(errors="replace")
            + result.stderr.decode(errors="replace"),
        )


def stop_etcd(members: int) -> None:
    subprocess.run(
        [sys.executable, str(REPO / "etcd_cluster.py"),
         "--members", str(members), "down"],
        cwd=str(REPO), capture_output=True,
    )


def start(layout: str, backend: Backend) -> list[subprocess.Popen[bytes]]:
    if backend is Backend.RAFT:
        state = REPO / ".raft"
        if state.exists():
            shutil.rmtree(state)
    else:
        print(f"  etcd: bringing up {len(layout)} member(s)")
        start_etcd(len(layout))
    processes = []
    for index, kind in enumerate(layout):
        command = [str(REPO / backend.launcher), str(index), str(len(layout))]
        if kind == "r":
            command.append("--rust")
        out = (SCRATCH / f"mixed-m{index}.out").open("wb")
        processes.append(subprocess.Popen(
            command, cwd=str(REPO), stdout=out, stderr=subprocess.STDOUT,
        ))
        print(f"  member {index}: {'Rust' if kind == 'r' else 'Python'}")
    return processes


async def post_node(session: aiohttp.ClientSession, index: int, node_id: str,
                    backend: Backend, *, label: str = "") -> tuple[int, str]:
    """Register (or re-register) a Node with one member.

    ``label`` goes into the Node's own ``label`` field, so an update made by
    one implementation is distinguishable from the original made by another --
    which is what turns "the members agree" into "the update propagated".
    """
    # `make_node` stamps a fresh TAI version on every call, so a re-register
    # is a genuine update rather than a write the registry rejects as stale.
    data = make_node(node_id, label=label) if label else make_node(node_id)
    body = {"type": "node", "data": data}
    try:
        async with session.post(
            f"http://127.0.0.1:{backend.reg_port(index)}"
            f"/x-nmos/registration/v1.3/resource",
            json=body, timeout=aiohttp.ClientTimeout(total=5),
        ) as response:
            return response.status, (await response.text())[:200]
    except Exception as error:  # noqa: BLE001 - reported, not handled
        return -1, repr(error)


async def delete_node(session: aiohttp.ClientSession, index: int,
                      node_id: str, backend: Backend) -> int:
    try:
        async with session.delete(
            f"http://127.0.0.1:{backend.reg_port(index)}"
            f"/x-nmos/registration/v1.3/resource/nodes/{node_id}",
            timeout=aiohttp.ClientTimeout(total=5),
        ) as response:
            return response.status
    except Exception:  # noqa: BLE001
        return -1


async def get_node(session: aiohttp.ClientSession, index: int, node_id: str,
                   backend: Backend) -> int:
    status, _ = await fetch_node(session, index, node_id, backend)
    return status


async def fetch_node(session: aiohttp.ClientSession, index: int, node_id: str,
                     backend: Backend) -> tuple[int, bytes]:
    """Status and the exact response bytes.

    The bytes matter. A stored body is spliced verbatim out of the request and
    served back without re-encoding, so if the two implementations disagree
    about the envelope -- key order, separators, float formatting, where the
    span starts and ends -- the status stays 200 and only the bytes differ.
    Comparing them is the only way that failure is visible.
    """
    try:
        async with session.get(
            f"http://127.0.0.1:{backend.query_port(index)}"
            f"/x-nmos/query/v1.3/nodes/{node_id}",
            timeout=aiohttp.ClientTimeout(total=5),
        ) as response:
            return response.status, await response.read()
    except Exception:  # noqa: BLE001
        return -1, b""


def dump_member_logs(size: int) -> None:
    for index in range(size):
        print(f"--- member {index} ---")
        text = (SCRATCH / f"mixed-m{index}.out").read_text(errors="replace")
        for line in text.splitlines():
            if "registry: At " not in line:
                print(f"  {line}")


async def run(layout: str, backend: Backend) -> int:
    size = len(layout)
    print(f"backend: {backend.label}")
    print(f"layout: {layout}  (position n = member n; p=Python, r=Rust)")
    processes = start(layout, backend)
    try:
        async with aiohttp.ClientSession() as session:
            print("\nwaiting for the cluster to accept a write", end="", flush=True)
            formed = False
            detail = ""
            for _ in range(120):
                status, detail = await post_node(
                    session, 0, str(uuid.uuid4()), backend,
                )
                if status in (200, 201):
                    formed = True
                    break
                print(".", end="", flush=True)
                await asyncio.sleep(1.0)
            print()
            if not formed:
                print(f"FAILED: no write accepted. Last: {detail}")
                dump_member_logs(size)
                return 1
            print("cluster formed: a write was accepted")

            # One Node registered with each member, then every member asked
            # about all of them. Consensus -- etcd's or the members' own --
            # is what makes that work.
            ids = [str(uuid.uuid4()) for _ in range(size)]
            print()
            for index, node_id in enumerate(ids):
                status, detail = await post_node(
                    session, index, node_id, backend,
                    label=f"registered-by-m{index}",
                )
                print(f"  registered {node_id[:8]} with member {index} "
                      f"-> HTTP {status}")
                if status not in (200, 201):
                    print(f"    {detail}")
                    dump_member_logs(size)
                    return 1

            await asyncio.sleep(3.0)
            print()
            print("  every member asked about every Node")
            header = "".join(f"{'m' + str(i):>10}" for i in range(size))
            print(f"    {'asked \\ registered with':<26}{header}")
            failures = 0
            bodies: dict[str, list[bytes]] = {node_id: [] for node_id in ids}
            for asked in range(size):
                cells = []
                for owner in range(size):
                    status, payload = await fetch_node(
                        session, asked, ids[owner], backend,
                    )
                    if status != 200:
                        failures += 1
                    bodies[ids[owner]].append(payload)
                    cells.append(f"{status:>10}")
                print(f"    member {asked:<20}{''.join(cells)}")

            if failures:
                print()
                print(f"RESULT: FAILED -- {failures} lookups did not return 200")
                dump_member_logs(size)
                return 1

            # Same status is not the same answer. A registry serves the stored
            # body back verbatim, so two implementations that disagree about
            # the envelope both return 200 and differ only here.
            print()
            print("  byte-for-byte: every member's copy of each Node")
            for owner, node_id in enumerate(ids):
                served = bodies[node_id]
                if len(set(served)) != 1:
                    print(f"    {node_id[:8]} DIFFERS across members")
                    for asked, payload in enumerate(served):
                        print(f"      m{asked}: {payload[:160]!r}")
                    print()
                    print("RESULT: FAILED -- members disagree about the bytes")
                    return 1
                print(f"    {node_id[:8]} identical on all {size} members "
                      f"({len(served[0])} bytes)")

            # Cross-implementation mutation. Each Node is updated by the NEXT
            # member round-robin, never the one that registered it, so in any
            # mixed layout at least one update crosses implementations: one
            # writes the record the other created.
            print()
            print("  cross-implementation update (owner -> updater)")
            for owner, node_id in enumerate(ids):
                updater = (owner + 1) % size
                status, detail = await post_node(
                    session, updater, node_id, backend,
                    label=f"updated-by-m{updater}",
                )
                kinds = f"{layout[owner]}->{layout[updater]}"
                print(f"    {node_id[:8]} m{owner} -> m{updater} [{kinds}] "
                      f"-> HTTP {status}")
                if status not in (200, 201):
                    print(f"      {detail}")
                    dump_member_logs(size)
                    return 1

            await asyncio.sleep(3.0)
            for owner, node_id in enumerate(ids):
                updater = (owner + 1) % size
                want = f'"label": "updated-by-m{updater}"'.encode()
                compact = f'"label":"updated-by-m{updater}"'.encode()
                for asked in range(size):
                    _, payload = await fetch_node(
                        session, asked, node_id, backend,
                    )
                    if want not in payload and compact not in payload:
                        print(f"    m{asked} still serves a stale "
                              f"{node_id[:8]}: {payload[:200]!r}")
                        print()
                        print("RESULT: FAILED -- an update did not propagate")
                        return 1
            print(f"    every member serves the updated label for all "
                  f"{size} Nodes")

            # Delete from a member that did not create the record either.
            print()
            print("  cross-implementation delete")
            for owner, node_id in enumerate(ids):
                deleter = (owner + 2) % size
                status = await delete_node(session, deleter, node_id, backend)
                kinds = f"{layout[owner]}->{layout[deleter]}"
                print(f"    {node_id[:8]} m{owner} -> m{deleter} [{kinds}] "
                      f"-> HTTP {status}")
                if status not in (200, 204):
                    dump_member_logs(size)
                    return 1

            await asyncio.sleep(3.0)
            for node_id in ids:
                for asked in range(size):
                    status = await get_node(session, asked, node_id, backend)
                    if status != 404:
                        print(f"    m{asked} still serves deleted "
                              f"{node_id[:8]} (HTTP {status})")
                        print()
                        print("RESULT: FAILED -- a delete did not propagate")
                        return 1
            print(f"    every member returns 404 for all {size} deleted Nodes")

            print()
            print(f"RESULT: PASS -- {backend.label}: register, read, update "
                  f"and delete all cross implementations, and every member "
                  f"serves identical bytes.")
            return 0
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        if backend is Backend.ETCD:
            stop_etcd(size)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one cluster with members of both implementations",
    )
    parser.add_argument("layout", nargs="?", default="prr",
                        help="one character per member: p=Python, r=Rust")
    parser.add_argument("--backend", default=Backend.RAFT.label,
                        choices=[member.label for member in Backend])
    args = parser.parse_args()
    if set(args.layout) - {"p", "r"} or not args.layout:
        parser.error("layout must be a non-empty string of 'p' and 'r'")
    backend = next(m for m in Backend if m.label == args.backend)
    # etcd's own cluster sizes are the only ones `etcd_cluster.py` will form,
    # and this rig runs one registry per etcd member.
    if backend is Backend.ETCD and len(args.layout) not in (1, 3, 5):
        parser.error("--backend etcd needs a layout of 1, 3 or 5 members")
    return asyncio.run(run(args.layout, backend))


if __name__ == "__main__":
    sys.exit(main())
