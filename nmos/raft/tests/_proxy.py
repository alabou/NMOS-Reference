# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""A cuttable TCP forwarder, so a partition can be injected into real sockets.

The in-memory harness makes a partition a method call. Over real TCP there is
no such lever: two processes on one machine can always reach each other, and
nothing short of firewall rules -- which need privileges the test suite does
not have and cannot ask for -- will stop them.

So each *directed* link gets a forwarder. Member A does not dial member B; it
dials A's own proxy for B, which forwards to B. Cutting that proxy makes B
unreachable from A while leaving B -> A alone, which is what finally makes
one-way partitions expressible over sockets.

What a cut has to do, and why closing the listener is not enough
---------------------------------------------------------------
A real partition does not politely stop new connections and leave established
ones running: it strands whatever was in flight and the endpoints eventually
notice. So ``cut`` closes every live connection **and** refuses new ones, which
is what forces the transport down the path that matters -- detecting the loss,
reconnecting, redoing the ``Hello``/``HelloAck`` handshake, and resuming
replication. None of that is reachable from the memory harness, where a
partition is a boolean and the "connection" is a method call.

Delay is per direction and preserves order, because TCP does. See the fault
model note in ``_harness.py``: injecting reordering inside a single connection
would be testing a network this transport cannot encounter.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any


class LinkProxy:
    """One directed link, ``listen_port -> target``.

    Args:
        target: ``(host, port)`` this forwards to.
        name: For diagnostics only, e.g. ``"0->2"``.
    """

    def __init__(self, target: tuple[str, int], *, name: str) -> None:
        self._target = target
        self._name = name
        self._server: asyncio.AbstractServer | None = None
        self._connections: set[asyncio.StreamWriter] = set()
        self._pumps: set[asyncio.Task[None]] = set()
        self._cut = False
        self.port = 0

        self.delay = 0.0
        """Seconds added to each relayed chunk. Order-preserving."""

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> int:
        self._server = await asyncio.start_server(
            self._accept, "127.0.0.1", 0,
        )
        self.port = int(self._server.sockets[0].getsockname()[1])
        return self.port

    async def close(self) -> None:
        self._cut = True
        self._drop_connections()
        for pump in list(self._pumps):
            pump.cancel()
        if self._pumps:
            await asyncio.gather(*list(self._pumps), return_exceptions=True)
        self._pumps.clear()
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), timeout=5.0)
            self._server = None

    # -- faults ----------------------------------------------------------

    @property
    def is_cut(self) -> bool:
        return self._cut

    def cut(self) -> None:
        """Strand this direction: kill live connections, refuse new ones."""
        if self._cut:
            return
        self._cut = True
        self._drop_connections()

    def heal(self) -> None:
        self._cut = False

    def _drop_connections(self) -> None:
        for writer in list(self._connections):
            with contextlib.suppress(Exception):
                writer.close()
        self._connections.clear()

    # -- forwarding ------------------------------------------------------

    async def _accept(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        if self._cut:
            with contextlib.suppress(Exception):
                writer.close()
            return
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                self._target[0], self._target[1],
            )
        except OSError:
            with contextlib.suppress(Exception):
                writer.close()
            return

        self._connections.add(writer)
        self._connections.add(upstream_writer)
        forward = self._spawn(self._pump(reader, upstream_writer))
        backward = self._spawn(self._pump(upstream_reader, writer))
        try:
            await asyncio.wait(
                {forward, backward}, return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in (forward, backward):
                task.cancel()
            for endpoint in (writer, upstream_writer):
                self._connections.discard(endpoint)
                with contextlib.suppress(Exception):
                    endpoint.close()

    def _spawn(self, coro: Any) -> asyncio.Task[None]:
        task: asyncio.Task[None] = asyncio.create_task(coro)
        self._pumps.add(task)
        task.add_done_callback(self._pumps.discard)
        return task

    async def _pump(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        """Relay bytes until the link closes or is cut.

        Byte-oriented rather than frame-oriented on purpose: this proxy must
        not understand the protocol it carries. A cut mid-frame is a thing real
        networks do, and the transport's checksum and length handling should be
        what notices, not a helpful proxy that only ever cuts on a boundary.
        """
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk or self._cut:
                    return
                if self.delay > 0.0:
                    await asyncio.sleep(self.delay)
                writer.write(chunk)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
            return
        except asyncio.CancelledError:
            raise


class ProxyMesh:
    """A ``LinkProxy`` for every ordered pair of members.

    ``dial_targets(source)`` is what a member's transport is built with, so
    every member reaches its peers only through links this mesh controls.
    """

    def __init__(self, real_ports: dict[int, int]) -> None:
        self._real = real_ports
        self._proxies: dict[tuple[int, int], LinkProxy] = {}

    async def start(self) -> None:
        for source in self._real:
            for target, port in self._real.items():
                if source == target:
                    continue
                proxy = LinkProxy(
                    ("127.0.0.1", port), name=f"{source}->{target}",
                )
                await proxy.start()
                self._proxies[(source, target)] = proxy

    async def close(self) -> None:
        for proxy in self._proxies.values():
            await proxy.close()
        self._proxies.clear()

    def dial_targets(self, source: int) -> dict[int, tuple[str, int]]:
        return {
            target: ("127.0.0.1", proxy.port)
            for (origin, target), proxy in self._proxies.items()
            if origin == source
        }

    # -- faults ----------------------------------------------------------

    def cut(self, source: int, target: int) -> None:
        proxy = self._proxies.get((source, target))
        if proxy is not None:
            proxy.cut()

    def heal_link(self, source: int, target: int) -> None:
        proxy = self._proxies.get((source, target))
        if proxy is not None:
            proxy.heal()

    def cut_both(self, a: int, b: int) -> None:
        self.cut(a, b)
        self.cut(b, a)

    def isolate(self, index: int) -> None:
        for other in self._real:
            if other != index:
                self.cut_both(index, other)

    def rejoin(self, index: int) -> None:
        for other in self._real:
            if other != index:
                self.heal_link(index, other)
                self.heal_link(other, index)

    def heal(self) -> None:
        for proxy in self._proxies.values():
            proxy.heal()

    def set_delay(self, seconds: float) -> None:
        for proxy in self._proxies.values():
            proxy.delay = seconds

    @property
    def members(self) -> tuple[int, ...]:
        return tuple(sorted(self._real))
