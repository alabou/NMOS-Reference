# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""What a conformance test needs from a cluster, independent of the backend.

Kept to the operations a test actually performs -- bring the cluster up, put a
registry in front of a member, take a member away, lose quorum -- because every
method here has to be implementable by two backends that share a purpose and
almost nothing else.

Why the rig builds the backend
------------------------------
It would be tidier for the test to construct its own backend from a config the
rig handed it, and that is what the first version did. It does not survive
contact with the second backend: for etcd the storage layer is a separate
process and the registry is its *client*, so "kill member 2" leaves every
registry running; for raft the member and the registry are the same object, so
"kill member 2" has to close the thing the test is holding.

Making the rig own both halves is what lets ``kill(2)`` mean the same
operational thing -- *that member is gone* -- on both, which is the only way a
shared test can assert anything about it.

Deliberately absent
-------------------
* Anything revision-shaped. Revisions, ``mod_revision`` and MVCC compaction are
  etcd's model, not a general one; exposing them would force the raft backend
  to emulate them, which is the coupling the second backend exists to avoid.
* Anything that writes to the storage layer directly. A test that seeds state
  behind the registry's back is testing the storage layer, and this suite is
  about what the *registry* does.

Everything is a coroutine, including the lifecycle. Stopping an in-process
member is genuinely asynchronous; pretending otherwise would push an
``asyncio.run`` or a fire-and-forget task into the rig, and the etcd side
simply does not await anything.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


class RigUnavailable(Exception):
    """This backend cannot be exercised here.

    A missing etcd binary, an absent certificate set -- conditions where the
    honest answer is "not installed", not "failed". Callers turn it into a
    skip, which is the rule ``etcd_server.py`` already follows.
    """


@runtime_checkable
class ClusterRig(Protocol):
    """A cluster of ``size`` members, with a registry in front of each."""

    @property
    def size(self) -> int:
        """How many members: 1, 3 or 5."""
        ...

    @property
    def quorum(self) -> int:
        """How many members a write needs. Topology, not backend."""
        ...

    @property
    def failures_tolerated(self) -> int:
        ...

    async def start_all(self) -> None:
        """Bring the storage layer up, if it is separate from the registries.

        A no-op for a backend whose members *are* the registries -- there the
        cluster forms when the backends start.
        """
        ...

    async def stop_all(self) -> None:
        """Tear everything down. Safe to call twice."""
        ...

    async def backend_for(
        self, index: int, registry: Any, namespace: str, **overrides: Any,
    ) -> Any:
        """A started ``RegistryBackend`` for member ``index``.

        ``overrides`` replaces named config fields -- the timeouts, mostly,
        which the outage tests shorten so a refusal arrives inside the test
        rather than after it.
        """
        ...

    async def kill(self, index: int) -> None:
        """Member ``index`` is gone, abruptly.

        Must not return until it is genuinely unreachable, or a test that
        expects the next operation to fail may still reach a dying member.
        """
        ...

    async def lose_quorum(self) -> None:
        """Take away enough members that no write can commit.

        An intent rather than "kill members 1 and 2", so the test reads as the
        condition it is about and a 5-member rig does the right thing without
        the test knowing how many that is. Member 0 is always left standing,
        because that is the one tests attach to.
        """
        ...
