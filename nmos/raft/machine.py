# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The applier: a committed entry becomes a store mutation and its grains.

The only writer
---------------
Every change to the store goes through here, whether this member proposed it or
learned it from the leader. That is the same property the etcd backend gets
from "the watch is the only writer", and it buys the same thing: a locally
originated change and a remote one take an identical path, so there is nothing
to suppress, nothing to deduplicate, and no way for the two to diverge.

Synchronous, and that is load-bearing
-------------------------------------
``store.py`` documents an invariant it depends on: every public method completes
without awaiting, so no other coroutine can observe a half-applied mutation, and
that is why there are no locks. ``apply`` preserves it -- the whole of a run,
from the first mutation to ``registry.publish``, contains no ``await``.

The caller is responsible for the other half: apply a *bounded* run, yield
between runs, never inside one. A 50,000-entry catch-up applied in one block
would stall the HTTP server and, worse, the heartbeat timer -- causing an
election, which causes more catch-up. ``node.py`` owns that loop.

Determinism, stated as rules
----------------------------
Every member applies the same entries and must reach the same state, byte for
byte. Three rules, each suppressing a default that would otherwise read local
state:

1. ``health=`` is always passed. ``apply_committed`` defaults it to
   ``health_now()``.
2. ``created=``/``updated=`` are always passed. It otherwise calls
   ``_next_cursor()``, which is per-member state.
3. Nothing here reads a clock, a random source, or iterates a set in a way
   that reaches output. ``_erase_subtree`` walks ``_children``, which *is* a
   set, so removal events are sorted before publication -- the same total
   order the etcd backend imposes for the same reason.

The tripwire
------------
``RegisterOp.expect_created`` carries the proposer's belief about 201-vs-200.
Apply re-runs ``store.prepare`` and *that* answer is authoritative, because the
id-uniqueness check is global and the proposer could not decide it. When the
two disagree, the proposer and this member have diverged about what the store
contains, and that is reported rather than reconciled: a member that quietly
serves its own version of the truth is the failure this whole design exists to
prevent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Sequence

from nmos.raft.cursors import CursorAllocator
from nmos.raft.log import Entry
from nmos.raft.operations import (
    ClaimOwnershipOp,
    ExpireOp,
    ForgetOp,
    MemberDownOp,
    NoopOp,
    ProposalId,
    RegisterOp,
    RegistryOperation,
    ReleaseOwnershipOp,
    UnregisterOp,
)
from nmos.raft.ownership import OwnershipTable
from nmos.raft.snapshot import SnapshotStore
from nmos.registry.registry import Registry
from nmos.registry.store import RegistryStore
from nmos.registry.types import (
    Body,
    RegistrationResult,
    ResourceEvent,
    ResourceType,
)

log = logging.getLogger(__name__)


class DivergenceDetected(Exception):
    """Apply disagreed with the proposer about what the store contained.

    Never recovered from in place. The member raises, degrades, and asks for a
    fresh snapshot, because the one thing worse than being behind is serving a
    private version of the truth.
    """


@dataclass
class Outcome:
    """What one applied operation produced for whoever proposed it."""

    result: Any


class StateMachine:
    """Applies committed operations to the registry.

    Args:
        registry: The registry whose store this mutates and whose subscribers
            it publishes to.
        ownership: The replicated ownership table, mutated by the same entries.
        cursors: This member's cursor allocator, kept above every cursor the
            cluster has published.
        member: This member's index, for logging and for deciding whether an
            ownership change is about us.
    """

    __slots__ = (
        "_registry", "_ownership", "_cursors", "_member", "_last_applied",
        "_snapshots",
    )

    def __init__(
        self,
        registry: Registry,
        *,
        ownership: OwnershipTable,
        cursors: CursorAllocator,
        member: int,
        snapshots: SnapshotStore | None = None,
    ) -> None:
        self._registry = registry
        self._ownership = ownership
        self._cursors = cursors
        self._member = member
        self._last_applied = 0
        self._snapshots = snapshots

    @property
    def last_applied(self) -> int:
        return self._last_applied

    @property
    def ownership(self) -> OwnershipTable:
        return self._ownership

    @property
    def cursors(self) -> CursorAllocator:
        return self._cursors

    @property
    def store_intervals(self) -> tuple[float, float]:
        """``(gc_interval, forget_interval)`` of the store being replaced.

        A store rebuilt from a snapshot has to be configured like the one it
        replaces, or this member would expire and forget on a different
        schedule from its peers -- which is a divergence that only shows up
        much later, as resources disappearing from one member first.
        """
        store = self._registry.store
        return store.gc_interval, store.forget_interval

    def install_snapshot(
        self, store: RegistryStore, ownership: OwnershipTable, index: int,
    ) -> None:
        """Replace the whole local state with a snapshot's.

        The store is swapped in complete rather than filled in place, so Query
        never observes a half-loaded registry -- a member serving an empty view
        for the length of an install looks, to a Controller, exactly like a
        member whose registry was wiped.

        Ownership is replaced too. A member that rebuilt ownership only from
        entries *after* the snapshot would believe every Node was unowned and
        would start claiming Nodes that already have owners.
        """
        self._registry.swap_store(store)
        self._ownership = ownership
        self._last_applied = index

    def apply(
        self, entries: Sequence[Entry[RegistryOperation]],
    ) -> dict[ProposalId, Outcome]:
        """Apply a bounded run of entries. Synchronous from first to last.

        Returns one outcome per proposal, for the member that proposed it to
        resolve its waiters with. Entries proposed elsewhere still produce an
        outcome; the caller simply has nobody waiting on them.
        """
        outcomes: dict[ProposalId, Outcome] = {}
        events: list[ResourceEvent] = []

        for entry in entries:
            if entry.index <= self._last_applied:
                # Already applied. Ordinary after a snapshot install, where the
                # log may still hold entries the snapshot covers.
                continue
            outcome = self._apply_one(entry, events)
            if outcome is not None:
                outcomes[entry.value.proposal] = outcome
            self._last_applied = entry.index

        if events:
            # One publication for the whole run, in the same uninterrupted step
            # as the mutations it describes.
            self._registry.publish(events)
        return outcomes

    # -- dispatch -------------------------------------------------------

    def _apply_one(
        self, entry: Entry[RegistryOperation], events: list[ResourceEvent],
    ) -> Outcome | None:
        operation = entry.value

        if isinstance(operation, NoopOp):
            return None
        if isinstance(operation, RegisterOp):
            return self._apply_register(operation, entry.index, events)
        if isinstance(operation, UnregisterOp):
            return self._apply_unregister(operation, events)
        if isinstance(operation, ExpireOp):
            return self._apply_expire(operation, events)
        if isinstance(operation, ForgetOp):
            return self._apply_forget(operation)
        if isinstance(operation, ClaimOwnershipOp):
            self._ownership.claim(
                operation.node_id, operation.owner, entry.index,
            )
            return Outcome(result=True)
        if isinstance(operation, ReleaseOwnershipOp):
            self._ownership.release(operation.node_id, entry.index)
            return Outcome(result=True)
        if isinstance(operation, MemberDownOp):
            released = self._ownership.member_down(
                operation.member, entry.index,
            )
            if released:
                log.info(
                    "raft: member %d is down; released %d Node(s)",
                    operation.member, len(released),
                )
            return Outcome(result=len(released))
        return None

    # -- copy-on-write hooks --------------------------------------------

    def _capture_before_mutating(
        self, resource_type: ResourceType, resource_id: str,
    ) -> None:
        """Hand the snapshot a pre-image of what is about to change.

        Before, not after: ``apply_committed`` mutates records in place, so
        once it has run there is nothing left to photograph. A no-op unless a
        capture happens to be open, which is the common case -- the cost on the
        hot path is one attribute read.
        """
        if self._snapshots is None:
            return
        capture = self._snapshots.capture
        if capture is None:
            return
        existing = self._registry.store.get(
            resource_type, resource_id, include_non_extant=True,
        )
        if existing is None:
            # Nothing to preserve, and the snapshot must not invent it: this
            # resource did not exist at the pinned index.
            capture.capture_created(resource_type, resource_id)
        else:
            capture.capture(existing)

    def _capture_subtree(
        self, resource_type: ResourceType, resource_id: str,
    ) -> None:
        """Photograph a whole cascade before it is erased."""
        if self._snapshots is None or self._snapshots.capture is None:
            return
        capture = self._snapshots.capture
        for resource in self._registry.store.subtree(resource_type, resource_id):
            capture.capture(resource)

    # -- operations -----------------------------------------------------

    def _apply_register(
        self, op: RegisterOp, index: int, events: list[ResourceEvent],
    ) -> Outcome:
        store = self._registry.store
        # ``Body(text)`` -- not ``from_data`` -- so the bytes the client sent
        # survive apply exactly. ``from_data`` re-serialises, which is
        # precisely the normalisation the fidelity guarantee forbids.
        body = Body(op.body_text)

        prepared = store.prepare(op.resource_type, body.data)
        if isinstance(prepared, RegistrationResult):
            # Authoritative rejection. The proposer's optimistic check passed
            # and this one did not, which for a subtree-scoped rule would be a
            # divergence -- but ID_TYPE_CONFLICT is global and genuinely only
            # decidable here, so a rejection at this point is expected and is
            # simply the answer.
            return Outcome(result=prepared)

        if prepared.creates != op.expect_created:
            raise DivergenceDetected(
                f"proposer expected created={op.expect_created} for "
                f"{op.resource_type.value} {op.resource_id}, this member "
                f"computed {prepared.creates}; the two stores disagree about "
                f"what is registered",
            )

        if op.claim_owner is not None:
            # Fused claim: a Node's first registration takes ownership in the
            # same entry rather than paying a second round trip for it.
            self._ownership.claim(op.node_id, op.claim_owner, index)

        self._capture_before_mutating(op.resource_type, op.resource_id)
        result = store.apply_committed(
            prepared,
            body,
            created=op.created,
            updated=op.updated,
            health=op.health,
        )
        self._cursors.observe(op.resource_type, op.updated)
        events.extend(result.events)
        return Outcome(result=result)

    def _apply_unregister(
        self, op: UnregisterOp, events: list[ResourceEvent],
    ) -> Outcome:
        self._capture_subtree(op.resource_type, op.resource_id)
        removed = self._registry.store.delete(op.resource_type, op.resource_id)
        if removed is None:
            return Outcome(result=False)
        events.extend(self._ordered(removed))
        return Outcome(result=True)

    def _apply_expire(
        self, op: ExpireOp, events: list[ResourceEvent],
    ) -> Outcome:
        self._capture_subtree(ResourceType.NODE, op.node_id)
        removed = self._registry.store.delete(ResourceType.NODE, op.node_id)
        if removed is None:
            return Outcome(result=0)
        events.extend(self._ordered(removed))
        log.info(
            "raft: expired node %s and %d sub-resource(s)",
            op.node_id, len(removed) - 1,
        )
        return Outcome(result=len(removed))

    def _apply_forget(self, op: ForgetOp) -> Outcome:
        store = self._registry.store
        for resource_type, resource_id in op.victims:
            self._capture_before_mutating(resource_type, resource_id)
        forgotten = sum(
            1 for resource_type, resource_id in op.victims
            if store.forget(resource_type, resource_id)
        )
        # No events: a tombstone was already invisible to every client, so
        # dropping it changes nothing anyone can observe.
        return Outcome(result=forgotten)

    # -- determinism helpers --------------------------------------------

    @staticmethod
    def _ordered(events: Sequence[ResourceEvent]) -> list[ResourceEvent]:
        """Impose a total order on removal events before they are published.

        ``_erase_subtree`` walks ``_children``, which is a ``set``, so the
        order it produces depends on hash iteration and differs between
        members. Subscribers would then see the same deletion described in a
        different order on each member -- which is a divergence, even though
        every member ends in the same state.

        Deepest first, as the store already intends, then by id within a
        depth: a subscriber must never see a parent disappear while its
        children are still present.
        """
        return sorted(
            events,
            key=lambda event: (
                -_depth(event), event.resource_type.value, event.resource_id,
            ),
        )


def _depth(event: ResourceEvent) -> int:
    """Node 0, Device 1, everything else 2 -- the subtree depth."""
    if event.resource_type is ResourceType.NODE:
        return 0
    if event.resource_type is ResourceType.DEVICE:
        return 1
    return 2

