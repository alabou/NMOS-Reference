# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The boundary between "decide what the registry state should be" and "make it so".

``Registry`` and ``RegistryStore`` are a synchronous state machine. A backend is
the asynchronous thing underneath them that makes a change durable: in
standalone mode that is nothing at all, and in distributed mode it is an etcd
transaction, a fence, and a watch.

Why the async boundary is here and not on ``Registry``
------------------------------------------------------
It would seem natural to make ``Registry.register`` a coroutine and have it
await the backend. That is wrong twice over.

First, ``store.py`` documents an invariant it depends on: "Every public method
here completes without awaiting, so no other coroutine can observe a
half-applied mutation. That invariant is the reason there are no locks."
Introducing an ``await`` inside the mutation path would silently invalidate
that, and the failure would show up as a rare interleaving rather than as a test
failure.

Second, standalone mode has no I/O to await. Making its registration path a
coroutine would add scheduling overhead to the one path this project most wants
to be fast, in exchange for nothing.

So ``Registry`` stays synchronous and keeps its exact existing API, and the
backend is the async layer above it. Handlers await the backend; the backend
either calls straight through (standalone) or does the distributed work and then
applies the result through the same synchronous store methods.

State
-----
``BackendState`` is what the Registration API answers from. Query is deliberately
unaffected by it: a registry serving a cached view during an etcd outage is
still a useful registry, and refusing reads because writes are impossible would
turn a partial outage into a total one.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Protocol, runtime_checkable

from nmos.registry.registry import Registry
from nmos.registry.types import Body, RegistrationResult, ResourceType


class MutationUnavailable(Exception):
    """A mutation could not be completed for a reason that is not the client's.

    Distinct from a ``RegistrationResult`` failure, which is a 400 the Node must
    not retry. This is the 503 case: the storage layer could not commit within
    its deadline, or lost quorum part-way through. The body was fine; the
    registry was not able to act on it right now.

    Defined here rather than in the etcd backend so the handlers can catch it
    without importing anything that requires the optional etcd extra.
    """


class BackendState(Enum):
    """Lifecycle of the storage layer behind the Registration API."""

    STARTING = "starting"
    """Not yet consistent. Mutations answer 503; Query is not yet serving a
    trustworthy view either, so the registry has not finished coming up."""

    READY = "ready"
    """Fully consistent and accepting mutations."""

    DEGRADED = "degraded"
    """Cannot mutate -- etcd is unreachable or has no quorum -- but the cached
    Query view remains valid and is still served. Registration answers 503 with
    Retry-After; Query answers normally."""

    RESYNCING = "resyncing"
    """Rebuilding the local view after a compaction. The previous snapshot is
    still served while the replacement is built off to the side, so Query never
    sees an empty or half-loaded store."""

    STOPPING = "stopping"
    """Shutting down."""

    @property
    def accepts_mutations(self) -> bool:
        return self is BackendState.READY

    @property
    def serves_queries(self) -> bool:
        """Every state except STARTING has a view worth serving."""
        return self is not BackendState.STARTING


@runtime_checkable
class RegistryBackend(Protocol):
    """What the Registration API needs from the storage layer.

    Every method is a coroutine even where an implementation does not await,
    so the handler code is identical in both modes and cannot accidentally
    depend on standalone's synchrony.
    """

    @property
    def state(self) -> BackendState: ...

    async def start(self) -> None:
        """Bring the backend to READY, or raise."""
        ...

    async def register(
        self, resource_type: ResourceType, body: Body,
    ) -> RegistrationResult: ...

    async def unregister(
        self, resource_type: ResourceType, resource_id: str,
    ) -> bool: ...

    async def heartbeat(self, node_id: str) -> int | None:
        """Refresh a Node's liveness. Returns the new health, or None if absent."""
        ...

    async def collect_garbage(self) -> int:
        """Run one collection pass. Returns how many resources were collected."""
        ...

    async def close(self) -> None: ...


class StandaloneRegistryBackend:
    """The existing in-memory registry, behind the async interface.

    Every method completes without awaiting, so the behaviour is byte-for-byte
    what it was before this boundary existed -- which is the point. Standalone
    mode is not a degraded distributed mode; it is the original registry, and
    this class must not add semantics to it.
    """

    __slots__ = ("_registry", "_state")

    def __init__(self, registry: Registry) -> None:
        self._registry = registry
        # There is nothing to load and nothing that can be unavailable, so a
        # standalone backend is READY from construction. Reporting STARTING
        # until an explicit start() would make the Registration API answer 503
        # for anyone who forgot to call it.
        self._state = BackendState.READY

    @property
    def state(self) -> BackendState:
        return self._state

    @property
    def registry(self) -> Registry:
        return self._registry

    async def start(self) -> None:
        self._state = BackendState.READY

    async def register(
        self, resource_type: ResourceType, body: Body,
    ) -> RegistrationResult:
        return self._registry.register(resource_type, body)

    async def unregister(
        self, resource_type: ResourceType, resource_id: str,
    ) -> bool:
        return self._registry.unregister(resource_type, resource_id)

    async def heartbeat(self, node_id: str) -> int | None:
        return self._registry.store.heartbeat(node_id)

    async def collect_garbage(self) -> int:
        return self._registry.collect_garbage()

    async def close(self) -> None:
        self._state = BackendState.STOPPING
