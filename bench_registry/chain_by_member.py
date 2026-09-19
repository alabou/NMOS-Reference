"""Does it matter which member a Node registers *with*?

`node_online` is the headline latency number and it is a strictly serial chain
-- node, then device, then sender and receiver, each awaiting the last, because
IS-04 requires parents before children. So any per-registration cost multiplies
by the chain length, and a 2x swing between benchmark runs is one extra round
trip per step.

The suspected variable is the election. `compare.py` always drives member 0,
and member 0 wins the election one time in five at this size. A follower must
forward every proposal to the leader, which is a round trip the leader does not
pay -- times the chain.

This runs the same chain at every member. The leader identifies itself by being
the fast one; nothing has to parse a log or ask for a role.

Usage:  chain_by_member.py [members] [chains]
"""
from __future__ import annotations

import asyncio
import sys
import time
import uuid


import aiohttp

from nmos.registry.tests._fixtures import (
    make_device,
    make_node,
    make_receiver,
    make_sender,
)
from bench_registry.locality import pct, start_cluster


async def post(session: aiohttp.ClientSession, base: str,
               kind: str, data: dict[str, object]) -> int:
    async with session.post(
        f"{base}/x-nmos/registration/v1.3/resource",
        json={"type": kind, "data": data},
    ) as response:
        await response.read()
        return response.status


async def one_chain(session: aiohttp.ClientSession, base: str) -> float:
    """One complete Node: node -> device -> sender + receiver. Serial."""
    node_id = str(uuid.uuid4())
    device_id = str(uuid.uuid4())
    started = time.perf_counter()
    await post(session, base, "node", make_node(node_id))
    await post(session, base, "device", make_device(device_id, node_id))
    await post(session, base, "sender",
               make_sender(str(uuid.uuid4()), str(uuid.uuid4()), device_id))
    await post(session, base, "receiver",
               make_receiver(str(uuid.uuid4()), device_id))
    return (time.perf_counter() - started) * 1000.0


async def main(members: int, chains: int) -> None:
    processes, fronts = start_cluster(members)
    reg_bases = [f"http://127.0.0.1:{r}" for r, _q in fronts]
    try:
        async with aiohttp.ClientSession() as session:
            # Warm every member so the first chain does not pay for a cold
            # connection and get reported as a consensus cost.
            for _ in range(5):
                for base in reg_bases:
                    await one_chain(session, base)
            await asyncio.sleep(1.0)

            print(f"cluster of {members}; {chains} serial 4-resource chains at "
                  f"each member")
            print(f"  {'member':>8}  {'p50 ms':>8}  {'p90 ms':>8}  "
                  f"{'p99 ms':>8}")
            # Interleaved, not member by member. Measuring one member to
            # completion and then the next lets warm-up and cache state ride
            # along with the member index, which produced a tidy monotonic
            # ranking that had nothing to do with which member led.
            collected: dict[int, list[float]] = {
                index: [] for index in range(members)
            }
            for _ in range(chains):
                for index, base in enumerate(reg_bases):
                    collected[index].append(await one_chain(session, base))
            medians: list[float] = []
            for index in range(members):
                samples = collected[index]
                medians.append(pct(samples, 0.5))
                print(f"  {index:>8}  {pct(samples, 0.5):>8.2f}  "
                      f"{pct(samples, 0.9):>8.2f}  {pct(samples, 0.99):>8.2f}")

            fastest = min(range(members), key=lambda i: medians[i])
            slowest = max(range(members), key=lambda i: medians[i])
            print()
            print(f"  fastest member: {fastest} at {medians[fastest]:.2f} ms "
                  f"-- almost certainly the leader")
            print(f"  slowest member: {slowest} at {medians[slowest]:.2f} ms")
            print(f"  SPREAD across members: "
                  f"x{medians[slowest] / medians[fastest]:.2f}")
            others = [m for i, m in enumerate(medians) if i != fastest]
            if others:
                print(f"  leader {medians[fastest]:.2f} ms vs follower median "
                      f"{sorted(others)[len(others) // 2]:.2f} ms "
                      f"-> x{sorted(others)[len(others) // 2] / medians[fastest]:.2f}")
    finally:
        for process in processes:
            process.kill()
        for process in processes:
            process.wait()


if __name__ == "__main__":
    asyncio.run(main(
        int(sys.argv[1]) if len(sys.argv) > 1 else 5,
        int(sys.argv[2]) if len(sys.argv) > 2 else 60,
    ))
