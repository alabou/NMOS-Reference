# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Which member owns which Node subtree, derived from the log.

Why ownership exists
--------------------
The etcd backend must read before it writes. It validates a registration
against its local store, but that store is a read model that may be behind, so
a rejection it produces might be a lie -- a parent registered a moment ago on
another member simply has not arrived yet. ``etcd_backend.py`` is explicit
about the consequence: a 400 is terminal, something the Node "MUST NOT" retry,
so it can never be returned without a linearizable read first.

Ownership removes the premise. If exactly one member is responsible for a
Node's subtree, that member's view of the subtree is authoritative by
construction, and the four subtree-scoped checks in ``store.prepare`` can be
decided locally and returned immediately. That is where "a 400 costs zero round
trips" comes from, and it is most of why a registration costs one round trip
here instead of two or three.

(The fifth check -- id uniqueness against ``_type_of`` -- is global, not
subtree-scoped, and is *not* covered by this. See ``operations.py``: apply
re-runs ``prepare`` against the replicated store, and that answer is the
authoritative one.)

The table is a replicated derivation, not a negotiation
-------------------------------------------------------
Nothing here talks to anyone. Ownership changes are operations in the log, so
every member computes the same table from the same entries, in the same order,
and there is no protocol for two members to disagree about. ``epoch`` is the
log index of the entry that set the current owner, which makes it monotonic by
construction and makes "who claimed most recently" answerable without a clock.

The epoch checks below are therefore defensive rather than load-bearing: in a
correctly ordered apply they can never fire. They exist because a table that
silently accepted a stale claim would produce two members each believing they
owned a Node, and the resulting divergence would be discovered somewhere far
away from the cause.
"""

from __future__ import annotations

from dataclasses import dataclass

from nmos.raft.wire import Reader, Writer


@dataclass(frozen=True)
class Ownership:
    """Who owns a Node, and the log index that decided it."""

    owner: int
    epoch: int


class OwnershipTable:
    """The per-Node ownership map, applied from the log.

    Not thread-safe and deliberately not asynchronous: like the store, it is
    mutated only from the synchronous apply step, so there is no interleaving
    for a lock to protect against.
    """

    __slots__ = ("_by_node",)

    def __init__(self) -> None:
        self._by_node: dict[str, Ownership] = {}

    # -- reading --------------------------------------------------------

    def owner_of(self, node_id: str) -> Ownership | None:
        """The current owner, or None when the Node is unowned.

        Unowned is a normal state, not an error: it is what a Node looks like
        between its owner dying and whichever member it re-registers with
        claiming it.
        """
        return self._by_node.get(node_id)

    def is_owned_by(self, node_id: str, member: int) -> bool:
        current = self._by_node.get(node_id)
        return current is not None and current.owner == member

    def nodes_owned_by(self, member: int) -> tuple[str, ...]:
        """Every Node this member owns, in a fixed order.

        Sorted, because the answer feeds ``member_down`` and a member-down
        entry must produce the same result on every member that applies it.
        """
        return tuple(sorted(
            node_id for node_id, held in self._by_node.items()
            if held.owner == member
        ))

    def __len__(self) -> int:
        return len(self._by_node)

    def __contains__(self, node_id: object) -> bool:
        return node_id in self._by_node

    # -- mutation, from apply only --------------------------------------

    def claim(self, node_id: str, owner: int, epoch: int) -> bool:
        """Set the owner. Returns whether anything changed.

        A claim at or below the current epoch is ignored. In a correctly
        ordered apply that cannot happen -- epochs are log indices -- so this
        is the tripwire described in the module docstring rather than an
        expected path.
        """
        current = self._by_node.get(node_id)
        if current is not None and epoch <= current.epoch:
            return False
        self._by_node[node_id] = Ownership(owner=owner, epoch=epoch)
        return True

    def release(self, node_id: str, epoch: int) -> bool:
        """Drop the owner, leaving the Node unowned. Returns whether it changed.

        Note the Node's *resources* are untouched. Releasing ownership says
        nothing about whether the Node is still registered -- a member dying
        does not expire the resources it happened to be responsible for, it
        only means somebody else has to take over answering for them.
        """
        current = self._by_node.get(node_id)
        if current is None or epoch <= current.epoch:
            return False
        del self._by_node[node_id]
        return True

    def member_down(self, member: int, epoch: int) -> tuple[str, ...]:
        """Release every Node ``member`` owned. Returns which ones.

        One operation rather than one per Node: a member holding a thousand
        Nodes must not put a thousand entries through consensus at the exact
        moment the cluster is already a member short.
        """
        released = []
        for node_id in self.nodes_owned_by(member):
            if self.release(node_id, epoch):
                released.append(node_id)
        return tuple(released)

    # -- snapshot transfer ----------------------------------------------

    def encode(self) -> bytes:
        """Serialise for ``InstallSnapshot``.

        The table travels with the snapshot because it is state derived from
        entries the snapshot has replaced. A follower that installed a snapshot
        and rebuilt ownership only from entries *after* it would believe every
        Node was unowned, and would start claiming Nodes that already have
        owners.

        Entries are written in sorted order so two members produce byte-
        identical snapshots from equal tables, which is what lets a snapshot be
        compared or checksummed at all.
        """
        writer = Writer()
        for node_id in sorted(self._by_node):
            held = self._by_node[node_id]
            writer.bytes_(
                1,
                Writer().string(1, node_id).uint(2, held.owner)
                .uint(3, held.epoch).take(),
            )
        return writer.take()

    @classmethod
    def decode(cls, payload: bytes) -> OwnershipTable:
        table = cls()
        reader = Reader(payload)
        for number, wire in reader:
            if number == 1:
                node_id, owner, epoch = _read_entry(reader.bytes_())
                table._by_node[node_id] = Ownership(owner=owner, epoch=epoch)
            else:
                reader.skip(wire)
        return table


def _read_entry(payload: bytes) -> tuple[str, int, int]:
    node_id = ""
    owner = epoch = 0
    reader = Reader(payload)
    for number, wire in reader:
        if number == 1:
            node_id = reader.string()
        elif number == 2:
            owner = reader.uint()
        elif number == 3:
            epoch = reader.uint()
        else:
            reader.skip(wire)
    return node_id, owner, epoch
