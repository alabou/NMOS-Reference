"""Does it matter *which* cluster member a Controller queries?

`compare.py` drives registration and query at member 0, and member 0 therefore
owns everything it registered. Every published cluster number is that one case:
the Node registered here, the Controller asks here. This asks whether the
numbers generalise.

Two dimensions, which are different questions and are easy to conflate:

* **Which member the Controller asks.** It picks one registry and stays there.
* **Which member the resource was registered with.** A Controller asking
  registry A about a Node that registered with registry B -- which is the
  ordinary case the moment there is more than one Node.

Measured rather than reasoned about:

1. **Cost** -- is a query at a member that owns nothing slower than at the
   owner? Architecturally it should not be: `handlers_query.py` touches no
   backend, takes no fence and forwards nothing, so it reads the local
   replicated store whoever it belongs to. That is a claim about the code and
   is worth testing against the running thing.

2. **Staleness** -- a non-leader applies a moment after the leader, so a query
   there can miss a registration that has already been acknowledged. This
   measures how many resources each member reports for the same question at
   the same instant.

3. **Registration** -- the expensive locality, and the one already known to
   matter. Registering at a member that does *not* own the Node forwards to
   the owner, which is a round trip the benchmark never pays.

Usage:  probe_locality.py [members] [nodes] [queries]
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path


import aiohttp

from bench_registry.compare import (
    REPO,
    RUST_REGISTRY,
    WORK,
    _free_pair,
    _free_port,
    _wait_http,
)
from nmos.registry.tests._fixtures import make_node


def start_cluster(members: int) -> tuple[list[subprocess.Popen[bytes]],
                                         list[tuple[int, int]]]:
    """Spawn `members` Rust registries. Returns processes and (reg, query) ports.

    Same command line as `compare.py::start_rust_raft`, but every member's
    front ports are kept rather than only member 0's -- which is the whole
    point of this probe.
    """
    pairs = [_free_pair() for _ in range(members)]
    advertised = [f"127.0.0.1:{client}" for client, _peer in pairs]
    processes: list[subprocess.Popen[bytes]] = []
    fronts: list[tuple[int, int]] = []

    for index in range(members):
        registration_port, query_port, ws_port = (
            _free_port(), _free_port(), _free_port(),
        )
        fronts.append((registration_port, query_port))
        state_dir = WORK / "locality-raft" / f"m{index}"
        if state_dir.exists():
            shutil.rmtree(state_dir)
        state_dir.mkdir(parents=True)

        command = [
            str(RUST_REGISTRY),
            "--registryDisableTLS",
            "--registryAddr", "127.0.0.1",
            "--registrationPort", str(registration_port),
            "--queryPort", str(query_port),
            "--queryWebSocketPort", str(ws_port),
            "--logFile", "",
            "--statusInterval", "0",
            "--distributed",
            "--distributedBackend", "raft",
            "--raftDisableTLS",
            "--raftStateDir", str(state_dir),
            "--raftNamespace", "/bench/locality",
            "--registryAdvertisedHost", advertised[index],
        ]
        for other in advertised:
            if other != advertised[index]:
                command += ["--registryNeighbour", other]

        environment = dict(os.environ)
        environment["NMOS_LOG_LEVEL"] = "WARNING"
        stdout_path = WORK / f"locality-m{index}.out"
        processes.append(subprocess.Popen(
            command, cwd=str(REPO),
            stdout=stdout_path.open("wb"), stderr=subprocess.STDOUT,
            env=environment,
        ))

    for _registration, query_port in fronts:
        if not _wait_http(f"http://127.0.0.1:{query_port}/x-nmos/query/v1.3/",
                          timeout=90.0):
            for process in processes:
                process.kill()
            raise SystemExit(f"cluster did not start; see {WORK}/locality-m*.out")
    return processes, fronts


def pct(values: list[float], fraction: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


async def register(session: aiohttp.ClientSession, base: str,
                   node_id: str) -> tuple[float, int]:
    body = {"type": "node", "data": make_node(node_id)}
    started = time.perf_counter()
    async with session.post(
        f"{base}/x-nmos/registration/v1.3/resource",
        json=body,
    ) as response:
        await response.read()
        return (time.perf_counter() - started) * 1000.0, response.status


async def query_one(session: aiohttp.ClientSession, base: str,
                    node_id: str) -> tuple[float, int]:
    """A single resource by id -- "tell me about this Node"."""
    started = time.perf_counter()
    async with session.get(
        f"{base}/x-nmos/query/v1.3/nodes/{node_id}",
    ) as response:
        await response.read()
        return (time.perf_counter() - started) * 1000.0, response.status


async def query_nodes(session: aiohttp.ClientSession, base: str,
                      *, limit: int = 100) -> tuple[float, int, int]:
    started = time.perf_counter()
    async with session.get(
        f"{base}/x-nmos/query/v1.3/nodes",
        params={"paging.limit": str(limit)},
    ) as response:
        payload = await response.json()
        return (
            (time.perf_counter() - started) * 1000.0,
            response.status,
            len(payload) if isinstance(payload, list) else -1,
        )


async def main(members: int, nodes: int, queries: int) -> None:
    processes, fronts = start_cluster(members)
    reg_bases = [f"http://127.0.0.1:{r}" for r, _q in fronts]
    query_bases = [f"http://127.0.0.1:{q}" for _r, q in fronts]
    try:
        async with aiohttp.ClientSession() as session:
            print(f"cluster of {members}; registering {nodes} nodes at member 0")
            ids = [str(uuid.uuid4()) for _ in range(nodes)]
            local_reg: list[float] = []
            for node_id in ids:
                elapsed, status = await register(session, reg_bases[0], node_id)
                if status in (200, 201):
                    local_reg.append(elapsed)
            print(f"  registration AT THE OWNER (member 0): "
                  f"p50={pct(local_reg, 0.5):7.2f} ms  "
                  f"p90={pct(local_reg, 0.9):7.2f} ms  n={len(local_reg)}")

            # Let replication settle so the staleness reading below is about
            # steady state rather than about a burst still landing.
            await asyncio.sleep(2.0)

            print()
            print("  QUERY COST, same question at every member")
            print(f"  {'member':>8}  {'p50 ms':>8}  {'p90 ms':>8}  "
                  f"{'p99 ms':>8}  {'nodes seen':>10}")
            seen_per_member: list[int] = []
            for index, base in enumerate(query_bases):
                samples: list[float] = []
                seen = -1
                for _ in range(queries):
                    elapsed, status, count = await query_nodes(session, base)
                    if status == 200:
                        samples.append(elapsed)
                        seen = count
                seen_per_member.append(seen)
                tag = "owner" if index == 0 else "external"
                print(f"  {index:>8}  {pct(samples, 0.5):>8.2f}  "
                      f"{pct(samples, 0.9):>8.2f}  {pct(samples, 0.99):>8.2f}  "
                      f"{seen:>10}   ({tag})")

            print()
            print(f"  resources visible per member: {seen_per_member}")
            print(f"  STALENESS SPREAD: "
                  f"{max(seen_per_member) - min(seen_per_member)} resources")

            # -- resource locality, the question that matters most ---------
            #
            # One querier, fixed, as a Controller would be. Half the Nodes
            # registered with it, half with a different member. Asking about
            # each, by id, is the closest thing to what a Controller does.
            print()
            print("  RESOURCE LOCALITY: one fixed querier (member 0), asked "
                  "about Nodes")
            print("  registered here versus registered elsewhere")

            elsewhere = [str(uuid.uuid4()) for _ in range(nodes)]
            for node_id in elsewhere:
                await register(session, reg_bases[1 % members], node_id)
            await asyncio.sleep(2.0)

            here_samples: list[float] = []
            away_samples: list[float] = []
            misses = 0
            for round_index in range(queries):
                mine = ids[round_index % len(ids)]
                theirs = elsewhere[round_index % len(elsewhere)]
                elapsed, status = await query_one(session, query_bases[0], mine)
                if status == 200:
                    here_samples.append(elapsed)
                else:
                    misses += 1
                elapsed, status = await query_one(
                    session, query_bases[0], theirs,
                )
                if status == 200:
                    away_samples.append(elapsed)
                else:
                    misses += 1

            print(f"    Node registered HERE (member 0 owns it):     "
                  f"p50={pct(here_samples, 0.5):6.2f}  "
                  f"p90={pct(here_samples, 0.9):6.2f}  "
                  f"p99={pct(here_samples, 0.99):6.2f} ms  "
                  f"n={len(here_samples)}")
            print(f"    Node registered ELSEWHERE (member 1 owns it):"
                  f" p50={pct(away_samples, 0.5):6.2f}  "
                  f"p90={pct(away_samples, 0.9):6.2f}  "
                  f"p99={pct(away_samples, 0.99):6.2f} ms  "
                  f"n={len(away_samples)}")
            if here_samples and away_samples:
                ratio = pct(away_samples, 0.5) / pct(here_samples, 0.5)
                print(f"    ratio (external / local) at p50: x{ratio:.2f}")
            print(f"    lookups that 404'd: {misses}")

            # -- visibility lag, the risk that is not about cost ----------
            #
            # A registration is acknowledged once it is committed and applied
            # *at the member that answered*. Another member applies the same
            # entry a moment later. So the question a Controller actually cares
            # about is not "is a remote query slower" -- it is not -- but "how
            # long after the Node is told yes can I see it from here".
            #
            # Measured by registering at member 0 and polling every other
            # member by id until it appears.
            print()
            print("  VISIBILITY LAG: registered at member 0, polled by id at "
                  "every other member")
            lags: dict[int, list[float]] = {i: [] for i in range(1, members)}
            immediate = {i: 0 for i in range(1, members)}
            rounds = 40
            for _ in range(rounds):
                node_id = str(uuid.uuid4())
                _elapsed, status = await register(
                    session, reg_bases[0], node_id,
                )
                if status not in (200, 201):
                    continue
                acknowledged = time.perf_counter()
                for index in range(1, members):
                    while True:
                        _e, code = await query_one(
                            session, query_bases[index], node_id,
                        )
                        if code == 200:
                            break
                    lag = (time.perf_counter() - acknowledged) * 1000.0
                    lags[index].append(lag)
                    # First poll already found it: nothing to wait for.
                    if lag < 1.0:
                        immediate[index] += 1
            for index in range(1, members):
                rows = lags[index]
                print(f"    member {index}: p50={pct(rows, 0.5):6.2f}  "
                      f"p90={pct(rows, 0.9):6.2f}  "
                      f"max={max(rows) if rows else float('nan'):6.2f} ms   "
                      f"visible on the first poll: "
                      f"{immediate[index]}/{len(rows)}")

            # 3. Registration at a member that does not own the Node.
            print()
            if members > 1:
                extra = [str(uuid.uuid4()) for _ in range(nodes)]
                remote_reg: list[float] = []
                for node_id in extra:
                    elapsed, status = await register(
                        session, reg_bases[1], node_id,
                    )
                    if status in (200, 201):
                        remote_reg.append(elapsed)
                print(f"  registration AT A DIFFERENT MEMBER (member 1, which "
                      f"then owns them):")
                print(f"    p50={pct(remote_reg, 0.5):7.2f} ms  "
                      f"p90={pct(remote_reg, 0.9):7.2f} ms  "
                      f"n={len(remote_reg)}")

                # The forwarded path: update a Node member 0 owns, at member 1.
                forwarded: list[float] = []
                refused: dict[int, int] = {}
                for node_id in ids[: min(len(ids), 50)]:
                    elapsed, status = await register(
                        session, reg_bases[1], node_id,
                    )
                    if status in (200, 201):
                        forwarded.append(elapsed)
                    else:
                        refused[status] = refused.get(status, 0) + 1
                print(f"  re-registering a Node OWNED BY MEMBER 0, sent to "
                      f"member 1 (forwarded):")
                print(f"    p50={pct(forwarded, 0.5):7.2f} ms  "
                      f"p90={pct(forwarded, 0.9):7.2f} ms  "
                      f"n={len(forwarded)}")
                if refused:
                    # Reported rather than left as a silent `nan`: a row with
                    # no samples looks like a measurement that was not taken,
                    # when it is a measurement that was refused -- and which of
                    # those it is changes what the number above means.
                    print(f"    refused: "
                          + ", ".join(f"HTTP {code} x{count}"
                                      for code, count in sorted(refused.items())))
    finally:
        for process in processes:
            process.kill()
        for process in processes:
            process.wait()


if __name__ == "__main__":
    asyncio.run(main(
        int(sys.argv[1]) if len(sys.argv) > 1 else 5,
        int(sys.argv[2]) if len(sys.argv) > 2 else 200,
        int(sys.argv[3]) if len(sys.argv) > 3 else 200,
    ))
