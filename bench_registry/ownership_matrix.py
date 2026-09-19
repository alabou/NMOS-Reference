"""The scenario the benchmark never covers.

node0 registers with member 0, node1 with member 1, node2 with member 2 -- and
then a Controller pinned to member 0 asks about *all* of them. `compare.py`
only ever asks member 0 about resources member 0 registered, so every published
cluster number is the diagonal of this matrix.

Reports, from one fixed querier, the cost of asking about a resource owned by
each member in turn -- by id, as a collection, and through a filter that must
evaluate every resource rather than hit an index.

Usage:  ownership_matrix.py [members] [nodes_per_member] [queries]
"""
from __future__ import annotations

import asyncio
import sys
import time
import uuid


import aiohttp

from bench_registry.locality import pct, query_one, register, start_cluster


async def query_filtered(session: aiohttp.ClientSession, base: str,
                         label: str) -> tuple[float, int, int]:
    """A basic query that cannot be answered from an index.

    ``label`` is a Node field, so the registry must look inside each stored
    body. If ownership were ever consulted on the read path, a filter sweeping
    every resource is where it would show.
    """
    started = time.perf_counter()
    async with session.get(
        f"{base}/x-nmos/query/v1.3/nodes",
        params={"label": label, "paging.limit": "100"},
    ) as response:
        payload = await response.json()
        return (
            (time.perf_counter() - started) * 1000.0,
            response.status,
            len(payload) if isinstance(payload, list) else -1,
        )


async def main(members: int, per_member: int, queries: int) -> None:
    processes, fronts = start_cluster(members)
    reg_bases = [f"http://127.0.0.1:{r}" for r, _q in fronts]
    query_bases = [f"http://127.0.0.1:{q}" for _r, q in fronts]
    try:
        async with aiohttp.ClientSession() as session:
            print(f"cluster of {members}; {per_member} Nodes registered with "
                  f"EACH member")
            owned: dict[int, list[str]] = {}
            for member in range(members):
                ids = [str(uuid.uuid4()) for _ in range(per_member)]
                for node_id in ids:
                    await register(session, reg_bases[member], node_id)
                owned[member] = ids
            total = members * per_member
            await asyncio.sleep(2.0)

            print()
            print(f"  ONE QUERIER (member 0) asked about a Node owned by each "
                  f"member, by id")
            print(f"  {'owned by':>10}  {'p50 ms':>8}  {'p90 ms':>8}  "
                  f"{'p99 ms':>8}  {'404s':>6}")
            # Interleaved by owner, not owner by owner. Measuring one owner
            # to completion and then the next lets warm-up ride along with the
            # owner index, and at small sample sizes that alone makes the
            # *local* row -- measured first -- look three times slower than
            # the external ones. It is an artefact every time.
            collected: dict[int, list[float]] = {o: [] for o in range(members)}
            missed: dict[int, int] = {o: 0 for o in range(members)}
            for index in range(queries):
                for owner in range(members):
                    node_id = owned[owner][index % per_member]
                    elapsed, status = await query_one(
                        session, query_bases[0], node_id,
                    )
                    if status == 200:
                        collected[owner].append(elapsed)
                    else:
                        missed[owner] += 1

            baseline = None
            for owner in range(members):
                by_id = collected[owner]
                misses = missed[owner]
                p50 = pct(by_id, 0.5)
                if baseline is None:
                    baseline = p50
                tag = "  (local)" if owner == 0 else f"  (x{p50 / baseline:.2f})"
                print(f"  {owner:>10}  {p50:>8.2f}  {pct(by_id, 0.9):>8.2f}  "
                      f"{pct(by_id, 0.99):>8.2f}  {misses:>6}{tag}")

            print()
            print(f"  THE WHOLE COLLECTION from each member ({total} Nodes, "
                  f"paged at 100)")
            for index, base in enumerate(query_bases):
                page: list[float] = []
                seen = -1
                for _ in range(queries // 2):
                    started = time.perf_counter()
                    async with session.get(
                        f"{base}/x-nmos/query/v1.3/nodes",
                        params={"paging.limit": "100"},
                    ) as response:
                        payload = await response.json()
                        seen = len(payload)
                    page.append((time.perf_counter() - started) * 1000.0)
                print(f"    member {index}: p50={pct(page, 0.5):6.2f}  "
                      f"p90={pct(page, 0.9):6.2f} ms   page={seen}")

            print()
            print(f"  FILTERED query from member 0, matching a Node owned by "
                  f"each member")
            print(f"  (a filter the registry cannot answer from an index)")
            for owner in range(members):
                filtered: list[float] = []
                matched = -1
                for _ in range(queries // 4):
                    elapsed, status, count = await query_filtered(
                        session, query_bases[0], "node-under-test",
                    )
                    if status == 200:
                        filtered.append(elapsed)
                        matched = count
                tag = "(local)" if owner == 0 else "(external)"
                print(f"    owned by {owner}: p50={pct(filtered, 0.5):6.2f}  "
                      f"p90={pct(filtered, 0.9):6.2f} ms  matched={matched} "
                      f"{tag}")
                break  # the filter sweeps everything; owner is not a variable

            print()
            print("  TOTAL VISIBLE from each member (should be every Node, "
                  "whoever registered it)")
            for index, base in enumerate(query_bases):
                count = 0
                async with session.get(
                    f"{base}/x-nmos/query/v1.3/nodes",
                    params={"paging.limit": "1000"},
                ) as response:
                    payload = await response.json()
                    count = len(payload) if isinstance(payload, list) else -1
                print(f"    member {index}: {count} of {total}")
    finally:
        for process in processes:
            process.kill()
        for process in processes:
            process.wait()


if __name__ == "__main__":
    asyncio.run(main(
        int(sys.argv[1]) if len(sys.argv) > 1 else 5,
        int(sys.argv[2]) if len(sys.argv) > 2 else 100,
        int(sys.argv[3]) if len(sys.argv) > 3 else 200,
    ))
