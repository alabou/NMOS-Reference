#!/usr/bin/env python3
# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Registry load generator, for comparing this registry against nmos-cpp.

    python3 bench_registry/loadgen.py --target http://127.0.0.1:8447 --query http://127.0.0.1:8446
    python3 bench_registry/loadgen.py --compare              # run the whole matrix

What this measures, and why in this order
-----------------------------------------
**Registration and update are headline metrics here, co-equal with Query.**
Three facts make them the ones that matter:

* Query never touches etcd, so it is identical standalone and distributed.
  Every cost distribution adds lands on POST and DELETE.
* Node startup is a *serial dependent chain* -- IS-04 fixes the order Node ->
  Devices -> Sources/Flows/Senders/Receivers, so a Node with 100 sender/receiver
  pairs issues ~200 POSTs whose parents must land first. Per-request latency,
  not aggregate throughput, is what sets how long a facility takes to come
  online, and it multiplies by every Node powering up at once.
* This project routes BCP-008 status through IS-04 registration, so every
  Sender/Receiver status transition is a registration *update* that fans out to
  a WebSocket grain on every member. Update latency is how fast status
  propagates.

Separating the taxes
--------------------
nmos-cpp registration is an in-memory insert with no consensus at all, so a
single side-by-side number would conflate three unrelated costs. The matrix
measures them apart::

    nmos-cpp        -> Python standalone     the PYTHON tax (what Rust recovers)
    standalone      -> distributed 1         the ETCD tax  (no quorum involved)
    distributed 1   -> 3 -> 5                the CONSENSUS tax

The 1-member distributed configuration exists purely to split the last two,
which are otherwise indistinguishable.

Matched observability
---------------------
Both sides must log the same amount or this measures logging. nmos-cpp's
``logging_level`` runs 40 (least verbose) to **-40 (most verbose)**, and every
config bundled in ``nmos-registry/`` is pinned at -40 -- the most verbose
setting that exists. ``--verify-log-volume`` counts bytes written by each target
during an identical workload and fails if they differ by more than an order of
magnitude, because settings that *claim* to match are not evidence that they do.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Sequence

import aiohttp

REGISTRATION_PATH = "/x-nmos/registration/v1.3"
QUERY_WS_PATH = "/x-nmos/query/v1.3/subscriptions"

# The AMWA/Sony scalability study (Rob Porter, IBC 2018 / SMPTE 2018) gives each
# Node exactly six resources -- 1x Node, Device, Sender, Receiver, Source, Flow --
# and reports 2,500 Nodes = 15,000 resources registered in 3m42s. Matching that
# shape is what makes our numbers comparable to a published reference instead of
# only to themselves.
AMWA_RESOURCES_PER_NODE = 6
AMWA_REFERENCE_NODES = 2500
AMWA_REFERENCE_SECONDS = 222.0  # 3m42s
_NO_MATCH_UUID = "00000000-dead-4000-8000-000000000000"

QUERY_PATH = "/x-nmos/query/v1.3"


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------

@dataclass
class Samples:
    """Latencies for one phase, plus what went wrong."""

    name: str
    seconds: list[float] = field(default_factory=list)
    errors: dict[int, int] = field(default_factory=dict)
    started: float = 0.0
    finished: float = 0.0

    def record(self, elapsed: float, status: int) -> None:
        self.seconds.append(elapsed)
        if status >= 400:
            self.errors[status] = self.errors.get(status, 0) + 1

    @property
    def count(self) -> int:
        return len(self.seconds)

    @property
    def wall(self) -> float:
        return max(self.finished - self.started, 1e-9)

    @property
    def rate(self) -> float:
        return self.count / self.wall

    def percentile(self, fraction: float) -> float:
        """Nearest-rank: a reported p99 must be a latency that really happened."""
        if not self.seconds:
            return 0.0
        ordered = sorted(self.seconds)
        index = max(0, min(len(ordered) - 1, round(fraction * len(ordered)) - 1))
        return ordered[index]

    def render(self) -> str:
        if not self.seconds:
            return f"{self.name:26} (no samples)"
        errors = (
            " " + " ".join(f"{code}x{n}" for code, n in sorted(self.errors.items()))
            if self.errors else ""
        )
        return (
            f"{self.name:26} {self.count:>6} "
            f"{self.rate:>9.1f}/s "
            f"p50 {self.percentile(0.50) * 1e3:>7.2f}ms "
            f"p95 {self.percentile(0.95) * 1e3:>7.2f}ms "
            f"p99 {self.percentile(0.99) * 1e3:>7.2f}ms "
            f"max {max(self.seconds) * 1e3:>7.2f}ms{errors}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "rate_per_s": self.rate,
            "p50_ms": self.percentile(0.50) * 1e3,
            "p95_ms": self.percentile(0.95) * 1e3,
            "p99_ms": self.percentile(0.99) * 1e3,
            "max_ms": max(self.seconds) * 1e3 if self.seconds else 0.0,
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------

def _tai(offset: float = 0.0) -> str:
    """TAI version string. Offset lets an update carry a later version."""
    now = time.time() + offset
    seconds = int(now) + 37  # TAI-UTC as of this project's other fixtures
    return f"{seconds}:{int((now % 1) * 1e9)}"


def _uuid(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"nmos-bench/{seed}"))


def make_node(index: int, *, version: str | None = None) -> dict[str, Any]:
    node_id = _uuid(f"node/{index}")
    return {
        "id": node_id,
        "version": version or _tai(),
        "label": f"bench-node-{index}",
        "description": "load generator node",
        "tags": {},
        "href": f"http://192.0.2.{1 + index % 250}:8080/",
        "hostname": f"bench-{index}",
        "caps": {},
        "api": {
            "versions": ["v1.3"],
            "endpoints": [
                {
                    "host": f"192.0.2.{1 + index % 250}",
                    "port": 8080,
                    "protocol": "http",
                },
            ],
        },
        "services": [],
        "clocks": [],
        "interfaces": [],
    }


def make_device(node_index: int, index: int, *, version: str | None = None) -> dict[str, Any]:
    return {
        "id": _uuid(f"device/{node_index}/{index}"),
        "version": version or _tai(),
        "label": f"bench-device-{node_index}-{index}",
        "description": "load generator device",
        "tags": {},
        "type": "urn:x-nmos:device:generic",
        "node_id": _uuid(f"node/{node_index}"),
        "senders": [],
        "receivers": [],
        "controls": [],
    }


def make_source(node_index: int, device_index: int, index: int,
                *, version: str | None = None) -> dict[str, Any]:
    return {
        "id": _uuid(f"source/{node_index}/{device_index}/{index}"),
        "version": version or _tai(),
        "label": f"bench-source-{node_index}-{device_index}-{index}",
        "description": "load generator source",
        "tags": {},
        "device_id": _uuid(f"device/{node_index}/{device_index}"),
        "caps": {},
        "format": "urn:x-nmos:format:video",
        "parents": [],
        "clock_name": None,
        "grain_rate": {"numerator": 25, "denominator": 1},
    }


def make_flow(node_index: int, device_index: int, index: int,
              *, version: str | None = None) -> dict[str, Any]:
    return {
        "id": _uuid(f"flow/{node_index}/{device_index}/{index}"),
        "version": version or _tai(),
        "label": f"bench-flow-{node_index}-{device_index}-{index}",
        "description": "load generator flow",
        "tags": {},
        "device_id": _uuid(f"device/{node_index}/{device_index}"),
        "source_id": _uuid(f"source/{node_index}/{device_index}/{index}"),
        "parents": [],
        "format": "urn:x-nmos:format:video",
        "media_type": "video/raw",
        "frame_width": 1920,
        "frame_height": 1080,
        "interlace_mode": "progressive",
        "colorspace": "BT709",
        "components": [
            {"name": "Y", "width": 1920, "height": 1080, "bit_depth": 10},
            {"name": "Cb", "width": 960, "height": 1080, "bit_depth": 10},
            {"name": "Cr", "width": 960, "height": 1080, "bit_depth": 10},
        ],
        "grain_rate": {"numerator": 25, "denominator": 1},
    }


def make_sender(node_index: int, device_index: int, index: int,
                *, version: str | None = None) -> dict[str, Any]:
    return {
        "id": _uuid(f"sender/{node_index}/{device_index}/{index}"),
        "version": version or _tai(),
        "label": f"bench-sender-{node_index}-{device_index}-{index}",
        "description": "load generator sender",
        "tags": {},
        "flow_id": None,
        "transport": "urn:x-nmos:transport:rtp.mcast",
        "device_id": _uuid(f"device/{node_index}/{device_index}"),
        "manifest_href": None,
        "interface_bindings": [],
        "caps": {},
        "subscription": {"receiver_id": None, "active": False},
    }


def make_receiver(node_index: int, device_index: int, index: int,
                  *, version: str | None = None) -> dict[str, Any]:
    return {
        "id": _uuid(f"receiver/{node_index}/{device_index}/{index}"),
        "version": version or _tai(),
        "label": f"bench-receiver-{node_index}-{device_index}-{index}",
        "description": "load generator receiver",
        "tags": {},
        "device_id": _uuid(f"device/{node_index}/{device_index}"),
        "transport": "urn:x-nmos:transport:rtp.mcast",
        "interface_bindings": [],
        "subscription": {"sender_id": None, "active": False},
        "format": "urn:x-nmos:format:video",
        "caps": {"media_types": ["video/raw"]},
    }


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

class Driver:
    """Issues Registration and Query requests and times them."""

    def __init__(
        self, session: aiohttp.ClientSession, registration: str, query: str,
    ) -> None:
        self._session = session
        self._registration = registration.rstrip("/")
        self._query = query.rstrip("/")

    async def post(self, resource_type: str, data: dict[str, Any]) -> tuple[float, int]:
        url = f"{self._registration}{REGISTRATION_PATH}/resource"
        body = {"type": resource_type, "data": data}
        started = time.perf_counter()
        async with self._session.post(url, json=body) as response:
            await response.read()
            return time.perf_counter() - started, response.status

    async def heartbeat(self, node_id: str) -> tuple[float, int]:
        url = f"{self._registration}{REGISTRATION_PATH}/health/nodes/{node_id}"
        started = time.perf_counter()
        async with self._session.post(url) as response:
            await response.read()
            return time.perf_counter() - started, response.status

    async def delete(self, plural: str, resource_id: str) -> tuple[float, int]:
        url = f"{self._registration}{REGISTRATION_PATH}/resource/{plural}/{resource_id}"
        started = time.perf_counter()
        async with self._session.delete(url) as response:
            await response.read()
            return time.perf_counter() - started, response.status

    async def query(
        self, collection: str, params: dict[str, str] | None = None,
    ) -> tuple[float, int]:
        url = f"{self._query}{QUERY_PATH}/{collection}"
        started = time.perf_counter()
        async with self._session.get(url, params=params or {}) as response:
            await response.read()
            return time.perf_counter() - started, response.status


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------

async def phase_node_online(
    driver: Driver, nodes: int, devices: int, pairs: int,
) -> Samples:
    """Wall-clock to bring ONE complete Node online, in the mandated order.

    The headline latency number, and the one an operator actually feels:
    IS-04 requires parents before children, so this chain is serial by
    specification, not by choice.
    """
    samples = Samples("node-online (chain)")
    samples.started = time.perf_counter()
    for node_index in range(nodes):
        chain_started = time.perf_counter()
        _, status = await driver.post("node", make_node(node_index))
        if status >= 400:
            samples.record(time.perf_counter() - chain_started, status)
            continue
        for device_index in range(devices):
            await driver.post("device", make_device(node_index, device_index))
            for pair in range(pairs):
                await driver.post(
                    "sender", make_sender(node_index, device_index, pair),
                )
                await driver.post(
                    "receiver", make_receiver(node_index, device_index, pair),
                )
        samples.record(time.perf_counter() - chain_started, 200)
    samples.finished = time.perf_counter()
    return samples


async def phase_cold_burst(
    driver: Driver, nodes: int, devices: int, pairs: int, concurrency: int,
) -> dict[str, Samples]:
    """Every resource, registered as fast as the ordering allows.

    Parents are serialised because they must be; siblings are issued
    concurrently because nothing in IS-04 orders them, and a real Node with 100
    senders would not wait for each before sending the next.
    """
    by_type = {
        name: Samples(f"cold-burst {name}")
        for name in ("node", "device", "sender", "receiver")
    }
    for samples in by_type.values():
        samples.started = time.perf_counter()

    semaphore = asyncio.Semaphore(concurrency)

    async def issue(resource_type: str, data: dict[str, Any]) -> None:
        async with semaphore:
            elapsed, status = await driver.post(resource_type, data)
            by_type[resource_type].record(elapsed, status)

    for node_index in range(nodes):
        await issue("node", make_node(node_index))
    for node_index in range(nodes):
        await asyncio.gather(*[
            issue("device", make_device(node_index, device_index))
            for device_index in range(devices)
        ])
    tasks = []
    for node_index in range(nodes):
        for device_index in range(devices):
            for pair in range(pairs):
                tasks.append(issue(
                    "sender", make_sender(node_index, device_index, pair),
                ))
                tasks.append(issue(
                    "receiver", make_receiver(node_index, device_index, pair),
                ))
    await asyncio.gather(*tasks)

    for samples in by_type.values():
        samples.finished = time.perf_counter()
    return by_type


async def phase_update_churn(
    driver: Driver, nodes: int, devices: int, pairs: int, rounds: int,
    concurrency: int,
) -> Samples:
    """Updates to resources that already exist -- the BCP-008 status path."""
    samples = Samples("update-churn (sender)")
    samples.started = time.perf_counter()
    semaphore = asyncio.Semaphore(concurrency)

    async def issue(data: dict[str, Any]) -> None:
        async with semaphore:
            elapsed, status = await driver.post("sender", data)
            samples.record(elapsed, status)

    for round_index in range(rounds):
        tasks = []
        for node_index in range(nodes):
            for device_index in range(devices):
                for pair in range(pairs):
                    sender = make_sender(
                        node_index, device_index, pair,
                        version=_tai(float(round_index + 1)),
                    )
                    sender["label"] += f"-r{round_index}"
                    tasks.append(issue(sender))
        await asyncio.gather(*tasks)

    samples.finished = time.perf_counter()
    return samples


async def phase_heartbeat(
    driver: Driver, nodes: int, rounds: int, concurrency: int,
) -> Samples:
    """The highest-frequency Registration operation: N nodes every 5 s."""
    samples = Samples("heartbeat")
    samples.started = time.perf_counter()
    semaphore = asyncio.Semaphore(concurrency)

    async def issue(node_id: str) -> None:
        async with semaphore:
            elapsed, status = await driver.heartbeat(node_id)
            samples.record(elapsed, status)

    for _ in range(rounds):
        await asyncio.gather(*[
            issue(_uuid(f"node/{index}")) for index in range(nodes)
        ])

    samples.finished = time.perf_counter()
    return samples


async def phase_query(
    driver: Driver, iterations: int, concurrency: int, paging_limit: int,
) -> Samples:
    """Collection reads with paging. Should be unchanged by distribution."""
    samples = Samples("query senders (paged)")
    samples.started = time.perf_counter()
    semaphore = asyncio.Semaphore(concurrency)

    async def issue() -> None:
        async with semaphore:
            elapsed, status = await driver.query(
                "senders", {"paging.limit": str(paging_limit)},
            )
            samples.record(elapsed, status)

    await asyncio.gather(*[issue() for _ in range(iterations)])
    samples.finished = time.perf_counter()
    return samples


async def phase_query_filtered(
    driver: Driver, iterations: int, concurrency: int, paging_limit: int,
) -> Samples:
    """Collection reads with a basic query that must be evaluated per resource.

    Deliberately NOT ``device_id``. That is the parent reference
    (``PARENT_KEY``) -- it is in the etcd key path and in the store's
    ``_children`` index, so a registry can answer it structurally without ever
    looking at a resource body, and measuring it would say nothing about the
    cost of filtering.

    ``subscription.sender_id`` is the opposite case and a real controller
    question -- "which receiver is consuming this sender?". It lives inside a
    nested object (``APIs - Query Parameters.md:446``), appears in no key, and
    is reachable only by inspecting each candidate. Its value is chosen not to
    match anything, so the filter runs over the whole collection rather than
    short-circuiting on an early hit -- the worst case, which is what a
    scalability number should report.
    """
    samples = Samples("query receivers (filtered)")
    samples.started = time.perf_counter()
    semaphore = asyncio.Semaphore(concurrency)

    async def issue() -> None:
        async with semaphore:
            elapsed, status = await driver.query(
                "receivers",
                {
                    "paging.limit": str(paging_limit),
                    "subscription.sender_id": _NO_MATCH_UUID,
                },
            )
            samples.record(elapsed, status)

    await asyncio.gather(*[issue() for _ in range(iterations)])
    samples.finished = time.perf_counter()
    return samples


async def phase_amwa_scale(
    driver: Driver, nodes: int, concurrency: int,
) -> tuple[Samples, dict[str, Any]]:
    """The AMWA/Sony scalability shape: N Nodes x 6 resources, timed end to end.

    Rob Porter's study (IBC 2018 / SMPTE 2018) gives each Node exactly
    1x Node, Device, Sender, Receiver, Source and Flow, and reports **2,500
    Nodes = 15,000 resources in 3m42s**. Reproducing that shape is what lets a
    number here be compared with a published reference rather than only with
    our own other runs.

    Ordering follows IS-04: Node, then Device, then Source, then Flow (which
    references the Source), then Sender and Receiver. Nodes are independent of
    one another, so they proceed concurrently -- which is what really happens
    when a facility powers up.
    """
    samples = Samples(f"amwa-scale ({nodes} nodes)")
    semaphore = asyncio.Semaphore(concurrency)
    samples.started = time.perf_counter()

    async def one_node(index: int) -> None:
        async with semaphore:
            # Serial WITHIN a node: each resource needs its parent present.
            for resource_type, data in (
                ("node", make_node(index)),
                ("device", make_device(index, 0)),
                ("source", make_source(index, 0, 0)),
                ("flow", make_flow(index, 0, 0)),
                ("sender", make_sender(index, 0, 0)),
                ("receiver", make_receiver(index, 0, 0)),
            ):
                elapsed, status = await driver.post(resource_type, data)
                samples.record(elapsed, status)

    await asyncio.gather(*[one_node(index) for index in range(nodes)])
    samples.finished = time.perf_counter()

    resources = nodes * AMWA_RESOURCES_PER_NODE
    seconds = samples.wall
    # The reference rate, scaled to whatever node count was actually run.
    reference = AMWA_REFERENCE_SECONDS * (nodes / AMWA_REFERENCE_NODES)
    summary = {
        "nodes": nodes,
        "resources": resources,
        "seconds": seconds,
        "resources_per_s": resources / seconds,
        "amwa_reference_resources_per_s":
            AMWA_REFERENCE_NODES * AMWA_RESOURCES_PER_NODE
            / AMWA_REFERENCE_SECONDS,
        "amwa_equivalent_seconds_for_this_size": reference,
        "errors": dict(samples.errors),
    }
    print(
        f"\n  AMWA-shape scale: {nodes} nodes x {AMWA_RESOURCES_PER_NODE} = "
        f"{resources} resources in {seconds:.1f}s "
        f"({summary['resources_per_s']:.1f} resources/s)",
    )
    print(
        f"    reference: 2,500 nodes / 15,000 resources in 222s "
        f"= {summary['amwa_reference_resources_per_s']:.1f} resources/s "
        f"(AMWA study, Mininet, 2018)",
    )
    return samples, summary


async def phase_status_fanout(
    session: aiohttp.ClientSession,
    driver: Driver,
    query_base: str,
    ws_base: str,
    subscribers: int,
    updates: int,
) -> Samples:
    """Update -> grain-on-the-wire, over the Query **WebSocket**.

    This is the half of the BCP-008 path that the REST phases cannot see. This
    project carries Sender/Receiver status as IS-04 registration updates, so a
    status change only reaches a Controller when the grain arrives on its
    subscription. Measuring the POST alone measures half the system.

    Each subscriber gets its own WebSocket. The clock starts when the POST is
    issued and stops when *that* resource's grain arrives, so the figure
    includes commit, watch application, grain construction and delivery.
    """
    samples = Samples(f"status-fanout ({subscribers} subs)")

    # One subscription to /senders, shared by every subscriber connection --
    # which is what a room full of Controllers watching the same topic looks
    # like, and the case where fan-out cost actually shows up.
    async with session.post(
        f"{query_base}{QUERY_WS_PATH}",
        json={
            "max_update_rate_ms": 0,
            "resource_path": "/senders",
            "params": {},
            "persist": True,
            "secure": False,
        },
    ) as response:
        if response.status not in (200, 201):
            print(f"  status-fanout: subscription refused ({response.status})")
            return samples
        subscription = await response.json()

    ws_href = subscription.get("ws_href") or ""
    if ws_base and ws_href:
        # Rewrite the advertised host: the registry advertises the address it
        # was configured with, which need not be the one we can reach.
        tail = ws_href.split("/x-nmos/", 1)[-1]
        ws_href = f"{ws_base}/x-nmos/{tail}"

    sockets = []
    try:
        for _ in range(subscribers):
            sockets.append(await session.ws_connect(ws_href, heartbeat=None))

        # Drain the SYNC burst each connection receives on attach.
        for socket in sockets:
            try:
                while True:
                    await asyncio.wait_for(socket.receive(), timeout=0.5)
            except (asyncio.TimeoutError, Exception):
                pass

        for round_index in range(updates):
            sender = make_sender(0, 0, 0, version=_tai(float(round_index + 1)))
            sender["label"] = f"fanout-{round_index}"
            target = sender["id"]

            started = time.perf_counter()
            _elapsed, status = await driver.post("sender", sender)
            if status >= 400:
                samples.record(time.perf_counter() - started, status)
                continue

            async def wait_for_grain(socket: Any) -> float:  # noqa: ANN401
                deadline = time.perf_counter() + 10.0
                while time.perf_counter() < deadline:
                    message = await asyncio.wait_for(
                        socket.receive(), timeout=10.0,
                    )
                    if message.type is not aiohttp.WSMsgType.TEXT:
                        continue
                    grain = json.loads(message.data)
                    for change in (
                        grain.get("grain", {}).get("data", []) or []
                    ):
                        post = change.get("post") or {}
                        if post.get("id") == target:
                            return time.perf_counter() - started
                raise asyncio.TimeoutError

            try:
                latencies = await asyncio.gather(
                    *[wait_for_grain(socket) for socket in sockets],
                )
                # The slowest subscriber is the one that matters: the status is
                # only "propagated" once every Controller has it.
                samples.record(max(latencies), 200)
            except (asyncio.TimeoutError, Exception):
                samples.record(time.perf_counter() - started, 504)
    finally:
        for socket in sockets:
            await socket.close()

    if samples.count:
        samples.started = 0.0
        samples.finished = sum(samples.seconds)
    return samples


async def phase_heartbeat_soak(
    driver: Driver, nodes: int, seconds: float, interval: float,
) -> tuple[Samples, dict[str, Any]]:
    """N nodes beating every ``interval`` for ``seconds``, as a real fleet does.

    The short heartbeat phase measures per-request latency; this measures what
    the registry actually lives with. It is also where the design's largest
    efficiency claim is visible: a beat here is a lease renewal that writes
    nothing, so the etcd write rate should stay flat as node count grows, where
    a health-key-per-beat design grows linearly.
    """
    samples = Samples(f"heartbeat-soak ({nodes}n {seconds:.0f}s)")
    samples.started = time.perf_counter()
    deadline = samples.started + seconds
    rounds = 0

    while time.perf_counter() < deadline:
        round_started = time.perf_counter()
        results = await asyncio.gather(*[
            driver.heartbeat(_uuid(f"node/{index}")) for index in range(nodes)
        ])
        for elapsed, status in results:
            samples.record(elapsed, status)
        rounds += 1
        # Hold the real cadence rather than hammering: the question is whether
        # the registry keeps up with the specified rate, not how fast it can be
        # driven.
        sleep = interval - (time.perf_counter() - round_started)
        # Never sleep past the deadline: otherwise a 6 s soak at a 5 s cadence
        # runs for 10 s, and the reported rate is computed over a window the
        # caller did not ask for.
        remaining = deadline - time.perf_counter()
        if sleep > 0 and remaining > 0:
            await asyncio.sleep(min(sleep, remaining))

    samples.finished = time.perf_counter()
    summary = {
        "nodes": nodes,
        "rounds": rounds,
        "beats": samples.count,
        "beats_per_s": samples.rate,
        "kept_cadence": rounds >= int(seconds / interval) - 1,
        "errors": dict(samples.errors),
    }
    print(
        f"\n  heartbeat soak: {nodes} nodes x {rounds} rounds "
        f"= {samples.count} beats in {samples.wall:.1f}s "
        f"({samples.rate:.0f} beats/s), cadence held: "
        f"{summary['kept_cadence']}",
    )
    return samples, summary


async def phase_delete(driver: Driver, nodes: int) -> Samples:
    """Node deletes, each cascading over its whole subtree."""
    samples = Samples("delete (cascade)")
    samples.started = time.perf_counter()
    for index in range(nodes):
        elapsed, status = await driver.delete("nodes", _uuid(f"node/{index}"))
        samples.record(elapsed, status)
    samples.finished = time.perf_counter()
    return samples


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run(args: argparse.Namespace) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=args.timeout)
    connector = aiohttp.TCPConnector(limit=args.concurrency * 2)
    results: dict[str, Samples] = {}

    async with aiohttp.ClientSession(
        timeout=timeout, connector=connector,
    ) as session:
        driver = Driver(session, args.target, args.query or args.target)
        extras: dict[str, Any] = {}

        print(
            f"\n{args.label or args.target}: "
            f"{args.nodes} node(s) x {args.devices} device(s) x "
            f"{args.pairs} sender/receiver pair(s), "
            f"concurrency {args.concurrency}\n",
        )

        if not args.skip_chain:
            results["node_online"] = await phase_node_online(
                driver, min(args.nodes, args.chain_nodes), args.devices,
                args.pairs,
            )
            await phase_delete(driver, min(args.nodes, args.chain_nodes))

        for name, samples in (
            await phase_cold_burst(
                driver, args.nodes, args.devices, args.pairs, args.concurrency,
            )
        ).items():
            results[f"cold_{name}"] = samples

        results["update_churn"] = await phase_update_churn(
            driver, args.nodes, args.devices, args.pairs, args.rounds,
            args.concurrency,
        )
        results["heartbeat"] = await phase_heartbeat(
            driver, args.nodes, args.rounds, args.concurrency,
        )
        results["query"] = await phase_query(
            driver, args.query_iterations, args.concurrency, args.paging_limit,
        )
        results["query_filtered"] = await phase_query_filtered(
            driver, args.query_iterations, args.concurrency, args.paging_limit,
        )
        # Fan-out BEFORE the delete phase: it updates an existing sender, and
        # once the delete cascade has removed its parent device the update is a
        # legitimate 400 rather than a measurement.
        if args.subscribers > 0:
            results["status_fanout"] = await phase_status_fanout(
                session, driver,
                args.query or args.target,
                args.ws or (args.query or args.target).replace("http", "ws"),
                args.subscribers, args.fanout_updates,
            )

        results["delete"] = await phase_delete(driver, args.nodes)

        if args.amwa_nodes > 0:
            samples, summary = await phase_amwa_scale(
                driver, args.amwa_nodes, args.concurrency,
            )
            results["amwa_scale"] = samples
            extras["amwa_scale"] = summary

        if args.soak_seconds > 0:
            samples, summary = await phase_heartbeat_soak(
                driver,
                args.soak_nodes or args.amwa_nodes or args.nodes,
                args.soak_seconds, args.heartbeat_interval,
            )
            results["heartbeat_soak"] = samples
            extras["heartbeat_soak"] = summary

    for samples in results.values():
        print("  " + samples.render())
    print()

    summary = {name: samples.as_dict() for name, samples in results.items()}
    for name, detail in extras.items():
        summary.setdefault(name, {}).update(detail)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Registry load generator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--target", default="http://127.0.0.1:8447",
                        help="Registration API base URL")
    parser.add_argument("--query", default="",
                        help="Query API base URL (defaults to --target)")
    parser.add_argument("--label", default="", help="Name for this run")

    parser.add_argument("--nodes", type=int, default=10)
    parser.add_argument("--devices", type=int, default=2)
    parser.add_argument("--pairs", type=int, default=5,
                        help="Sender/receiver pairs per device")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--query-iterations", type=int, default=200)
    parser.add_argument("--paging-limit", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--chain-nodes", type=int, default=3,
                        help="Nodes to time as a full serial chain")
    parser.add_argument("--skip-chain", action="store_true")
    parser.add_argument("--ws", default="",
                        help="Query WebSocket base URL (default: --query with "
                             "http swapped for ws)")

    g = parser.add_argument_group("status fan-out (Query WebSocket)")
    g.add_argument("--subscribers", type=int, default=0,
                   help="WebSocket subscribers to attach. 0 disables the "
                        "phase. This is the only phase that exercises the "
                        "Query WebSocket at all.")
    g.add_argument("--fanout-updates", type=int, default=20,
                   help="Sender updates to time end-to-end")

    g = parser.add_argument_group("AMWA-shape scale")
    g.add_argument("--amwa-nodes", type=int, default=0,
                   help="Nodes to register with the AMWA study's six-resource "
                        "shape (Node, Device, Sender, Receiver, Source, Flow). "
                        "0 disables. The published reference is 2500.")

    g = parser.add_argument_group("heartbeat soak")
    g.add_argument("--soak-seconds", type=float, default=0.0,
                   help="Sustain heartbeats for this long. 0 disables.")
    g.add_argument("--soak-nodes", type=int, default=0,
                   help="Nodes to beat (default: --amwa-nodes, else --nodes)")
    g.add_argument("--heartbeat-interval", type=float, default=5.0,
                   help="Heartbeat cadence; 5 s is the IS-04 default and the "
                        "AMWA study's recommendation")

    parser.add_argument("--json", default="",
                        help="Write results to this JSON file")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    results = asyncio.run(run(args))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(
                {"label": args.label or args.target, "phases": results},
                handle, indent=2,
            )
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
