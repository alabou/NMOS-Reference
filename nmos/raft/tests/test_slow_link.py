# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""A snapshot over a slow link completes, at the link's own speed.

Two defects stalled such a transfer for good, each on its own, and the chaos
soak could show neither: it models a link's latency, not its bandwidth, so no
message there takes longer to cross for being larger. Both were measured over
real sockets (part 17 of the fix record):

* **A chunk slower than ``election_min``.** A leader sent the chunk in flight
  again, under a new id, once its answer was overdue, and only the newest
  copy's answer drove the transfer. When one chunk's round trip outlasts
  ``election_min`` every answer arrives already superseded, so the transfer
  never passed its first chunk, and the copies -- each a whole chunk -- queued
  behind it: 183 copies of chunk 0 in 30 s at 16 KiB/s, 4 KiB chunks, a 150 ms
  ``election_min``. A chunk is now sent again only once the connection it went
  out on has ended, which is the only way it can be lost.
* **A chunk slower than the read deadline.** While a chunk crosses, all its
  sender can hear on that connection is answers: to the chunk, which cannot
  come until the chunk is whole, and to the sender's own heartbeat, which is
  queued behind the chunk. So a chunk taking longer than the deadline to cross
  starved the sender's reads, and it closed its own working connection: once
  every 5.07 s, the transfer never past its first chunk. Both ends of a
  connection now send their own heartbeat.

Real transports on loopback. The leader's connections to one member run through
a forwarder held to a rate, and that member restarts empty, so it can catch up
only by snapshot.
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from pathlib import Path

from nmos.raft.node import RaftTiming
from nmos.raft.tests._sockets import SocketCluster
from nmos.raft.tests.test_consensus import _register
from nmos.raft.transport import CONN_READ_TIMEOUT

TIMING = RaftTiming(
    heartbeat=0.020, election_min=0.150, election_max=0.300,
    compaction_threshold=8, snapshot_chunk=4096,
)


async def _catch_up_by_snapshot(
    root: Path, *, chunk: int, rate: float, registrations: int,
    within: float, conn_read_timeout: float = CONN_READ_TIMEOUT,
) -> str | None:
    """Restart a member empty behind a link held to ``rate`` bytes/s, and wait.

    ``None`` if it caught up within ``within`` seconds, otherwise what it did
    instead.
    """
    cluster = SocketCluster(
        3, root, timing=dataclasses.replace(TIMING, snapshot_chunk=chunk),
        conn_read_timeout=conn_read_timeout,
    )
    await cluster.start()
    loop = asyncio.get_running_loop()
    try:
        leader = await cluster.elect(timeout=10.0)
        await asyncio.gather(*(
            leader.node.propose(_register(str(uuid.uuid4()), leader.index))
            for _ in range(registrations)
        ))
        await cluster.settle(20)
        payload = leader.node._snapshot
        assert leader.node._snapshot_meta is not None, "the leader never compacted"
        assert len(payload) > 2 * chunk, (
            f"a snapshot of {len(payload)} bytes is under three chunks of {chunk}: "
            f"it proves nothing about a transfer"
        )
        follower = next(m for m in cluster.members if m.index != leader.index)
        cluster._mesh._proxies[(leader.index, follower.index)].rate = rate  # noqa: SLF001

        began = loop.time()
        replacement = await cluster.restart(follower.index)
        target = leader.node.commit_index
        while loop.time() - began < within:
            if replacement.node.last_applied >= target:
                return None
            await asyncio.sleep(0.05)
        return (
            f"{within:.0f}s after restarting behind a {rate / 1024:.0f} KiB/s link "
            f"it had applied {replacement.node.last_applied} of {target}: a "
            f"{len(payload)}-byte snapshot in {chunk}-byte chunks, each "
            f"{chunk / rate:.2f}s across, never arrived"
        )
    finally:
        await cluster.close()


class TestASnapshotOverASlowLink:

    async def test_chunks_slower_than_election_min_complete(
        self, tmp_path: Path,
    ) -> None:
        # 4 KiB at 16 KiB/s: 0.25 s a chunk, against a 0.15 s election_min.
        # About 2 s of transfer; the stall never passed its first chunk.
        failure = await _catch_up_by_snapshot(
            tmp_path, chunk=4096, rate=16 * 1024, registrations=60, within=15.0,
        )
        assert failure is None, failure

    async def test_chunks_slower_than_the_read_deadline_complete(
        self, tmp_path: Path,
    ) -> None:
        # 32 KiB at 24 KiB/s: 1.3 s a chunk, against a 0.5 s read deadline --
        # and against election_min as well, so both fixes are needed. About
        # 5 s of transfer.
        failure = await _catch_up_by_snapshot(
            tmp_path, chunk=32 * 1024, rate=24 * 1024, registrations=240,
            within=25.0, conn_read_timeout=0.5,
        )
        assert failure is None, failure
