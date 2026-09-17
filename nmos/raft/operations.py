# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The replicated unit: a registry operation, not a key-value pair.

This is the design decision the whole package turns on. The etcd backend
replicates *writes to a key-value store* and rebuilds a registry view from
them, which is why it needs an envelope format, a key layout, a watch, and a
fence to tell it when its view has caught up to a write it just made. Here the
log carries the operation itself, so applying a committed entry **is** the
store mutation plus its grain publication, and none of that machinery exists.

Determinism is the whole contract
---------------------------------
Every member applies the same entries in the same order and must reach the same
state. That makes the choice of what travels in an operation a correctness
question rather than an efficiency one, and it produces one rule:

    **Anything a member would otherwise read from its local environment must
    be decided once, by the proposer, and carried.**

Concretely, ``created``/``updated`` cursors and ``health`` are fields here
rather than defaults filled in at apply time, because ``apply_committed`` would
otherwise call ``health_now()`` and ``_next_cursor()`` -- both of which read
local state and would give a different answer on every member. Those two
defaults are precisely the ones ``machine.py`` must always override.

What is *not* carried, and why
------------------------------
The proposer's ``PreparedRegistration`` is not on the wire. It is tempting --
validation has already happened, so why do it twice -- but it would be wrong:
``store.prepare`` decides five things, and four of them are scoped to the
Node's own subtree while one, the id-uniqueness check against ``_type_of``, is
global across every Node. An owner cannot decide that one, because a
concurrent registration under a different Node on a different member might be
claiming the same id.

The log is what serialises those, so apply re-runs ``prepare`` against the
replicated store and *that* answer is authoritative. What travels instead is
``expect_created``: the proposer's belief about 201-vs-200, carried purely as a
tripwire. If apply disagrees, the two members have diverged, and saying so
loudly beats serving two different answers quietly.

Bodies travel verbatim
----------------------
``body_text`` is the request body exactly as the client sent it. The registry's
guarantee is that what was registered is what every member serves, byte for
byte, including vendor extensions the generated types do not model -- so a
re-encode here, however lossless it looked, would break that guarantee at the
one point nobody would think to check.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Union

from nmos.raft.errors import RaftProtocolError
from nmos.raft.wire import Reader, Writer
from nmos.registry.types import ResourceType, TaiCursor


class OpKind(IntEnum):
    """What an entry does. Numbers are permanent, like every wire number."""

    NOOP = 0
    """A leader's term-establishing entry.

    Raft §8: a new leader cannot know what is committed from previous terms
    until it commits an entry of its own, so it appends one that does nothing.
    """

    REGISTER = 1
    UNREGISTER = 2

    EXPIRE = 3
    """A Node whose lease lapsed, and its whole subtree.

    Decided by the owner against its own clock and then replicated, rather than
    each member expiring on its own clock -- which is how members end up
    disagreeing about which Nodes are alive.
    """

    FORGET = 4
    """Drop tombstones whose forget interval has elapsed.

    Replicated for the same reason and with the same shape as EXPIRE: the
    decision reads a clock, so it is made once and the *result* is what every
    member applies.
    """

    CLAIM_OWNERSHIP = 5
    RELEASE_OWNERSHIP = 6

    MEMBER_DOWN = 7
    """One member is gone; release every Node it owned, atomically.

    A single entry rather than one per Node, so a member holding a thousand
    Nodes does not put a thousand entries through consensus at the moment the
    cluster is already one member down.
    """


@dataclass(frozen=True)
class ProposalId:
    """Identifies a proposal so its originator can be answered.

    ``(member, sequence)`` rather than a UUID: it is two varints instead of
    sixteen bytes on every entry, and it is ordered.

    The sequence must be unique over the member's whole **history**, not just
    over one run of it. A restarted member has no outstanding waiters of its
    own, which is what an earlier version of this docstring relied on -- but
    its *entries* survive it, sitting in the cluster's log and applying after
    it returns. If the new incarnation mints the same ids, an old entry's
    outcome resolves a new caller's future: a registration answered with an
    unregistration's result. ``node.py`` therefore seeds the sequence from the
    incarnation, giving each run of the member its own range.
    """

    member: int
    sequence: int


@dataclass(frozen=True)
class NoopOp:
    KIND = OpKind.NOOP
    proposal: ProposalId


@dataclass(frozen=True)
class RegisterOp:
    """Register or update one resource."""

    KIND = OpKind.REGISTER

    proposal: ProposalId
    resource_type: ResourceType
    resource_id: str
    node_id: str
    body_text: str
    created: TaiCursor
    updated: TaiCursor
    health: int
    expect_created: bool
    claim_owner: int | None = None
    """Fused ownership claim.

    When the proposer is taking ownership of a previously unowned Node, the
    claim rides along on the registration instead of being a separate entry.
    That keeps a Node's first registration to one round trip rather than two,
    which matters because a facility powering up is entirely first
    registrations.
    """


@dataclass(frozen=True)
class UnregisterOp:
    KIND = OpKind.UNREGISTER

    proposal: ProposalId
    resource_type: ResourceType
    resource_id: str


@dataclass(frozen=True)
class ExpireOp:
    KIND = OpKind.EXPIRE

    proposal: ProposalId
    node_id: str


@dataclass(frozen=True)
class ForgetOp:
    KIND = OpKind.FORGET

    proposal: ProposalId
    victims: tuple[tuple[ResourceType, str], ...]


@dataclass(frozen=True)
class ClaimOwnershipOp:
    KIND = OpKind.CLAIM_OWNERSHIP

    proposal: ProposalId
    node_id: str
    owner: int


@dataclass(frozen=True)
class ReleaseOwnershipOp:
    KIND = OpKind.RELEASE_OWNERSHIP

    proposal: ProposalId
    node_id: str


@dataclass(frozen=True)
class MemberDownOp:
    KIND = OpKind.MEMBER_DOWN

    proposal: ProposalId
    member: int


RegistryOperation = Union[
    NoopOp,
    RegisterOp,
    UnregisterOp,
    ExpireOp,
    ForgetOp,
    ClaimOwnershipOp,
    ReleaseOwnershipOp,
    MemberDownOp,
]


# ---------------------------------------------------------------------------
# Codec
# ---------------------------------------------------------------------------

def _write_proposal(writer: Writer, proposal: ProposalId) -> None:
    writer.bytes_(
        1,
        Writer().uint(1, proposal.member).uint(2, proposal.sequence).take(),
    )


def _read_proposal(payload: bytes) -> ProposalId:
    member = sequence = 0
    reader = Reader(payload)
    for number, wire in reader:
        if number == 1:
            member = reader.uint()
        elif number == 2:
            sequence = reader.uint()
        else:
            reader.skip(wire)
    return ProposalId(member=member, sequence=sequence)


def _write_cursor(writer: Writer, field: int, cursor: TaiCursor) -> None:
    writer.bytes_(
        field,
        Writer().uint(1, cursor.seconds).uint(2, cursor.nanoseconds).take(),
    )


def _read_cursor(payload: bytes) -> TaiCursor:
    seconds = nanoseconds = 0
    reader = Reader(payload)
    for number, wire in reader:
        if number == 1:
            seconds = reader.uint()
        elif number == 2:
            nanoseconds = reader.uint()
        else:
            reader.skip(wire)
    return TaiCursor(seconds, nanoseconds)


def _resource_type(value: str) -> ResourceType:
    try:
        return ResourceType(value)
    except ValueError as exc:
        raise RaftProtocolError(f"unknown resource type {value!r}") from exc


def encode_operation(op: RegistryOperation) -> bytes:
    """Serialise one operation. Field 1 is always the kind."""
    writer = Writer().uint(1, int(op.KIND))
    body = Writer()
    _write_proposal(body, op.proposal)

    if isinstance(op, RegisterOp):
        body.string(2, op.resource_type.value)
        body.string(3, op.resource_id)
        body.string(4, op.node_id)
        body.string(5, op.body_text)
        _write_cursor(body, 6, op.created)
        _write_cursor(body, 7, op.updated)
        body.uint(8, op.health)
        body.bool_(9, op.expect_created)
        # Present-or-absent: member 0 is a real member, so a sentinel zero
        # would claim ownership for the first member on every registration
        # that was not claiming anything.
        if op.claim_owner is not None:
            body.uint(10, op.claim_owner)
    elif isinstance(op, UnregisterOp):
        body.string(2, op.resource_type.value)
        body.string(3, op.resource_id)
    elif isinstance(op, ExpireOp):
        body.string(2, op.node_id)
    elif isinstance(op, ForgetOp):
        for resource_type, resource_id in op.victims:
            body.bytes_(
                2,
                Writer().string(1, resource_type.value)
                .string(2, resource_id).take(),
            )
    elif isinstance(op, ClaimOwnershipOp):
        body.string(2, op.node_id)
        body.uint(3, op.owner)
    elif isinstance(op, ReleaseOwnershipOp):
        body.string(2, op.node_id)
    elif isinstance(op, MemberDownOp):
        body.uint(2, op.member)

    writer.bytes_(2, body.take())
    return writer.take()


def decode_operation(data: bytes) -> RegistryOperation:
    """Parse one operation, or raise :class:`RaftProtocolError`.

    Called on receipt rather than at apply time, deliberately. A malformed
    entry discovered here can still drop the link; discovered inside apply it
    would be a synchronous mutation that has nowhere to fail.
    """
    kind_value: int | None = None
    body = b""
    reader = Reader(data)
    for number, wire in reader:
        if number == 1:
            kind_value = reader.uint()
        elif number == 2:
            body = reader.bytes_()
        else:
            reader.skip(wire)

    if kind_value is None:
        raise RaftProtocolError("operation has no kind")
    try:
        kind = OpKind(kind_value)
    except ValueError as exc:
        raise RaftProtocolError(f"unknown operation kind {kind_value}") from exc

    proposal = ProposalId(member=0, sequence=0)
    resource_type_name = ""
    resource_id = node_id = body_text = ""
    created = updated = TaiCursor.min()
    health = 0
    expect_created = False
    claim_owner: int | None = None
    member = 0
    victims: list[tuple[ResourceType, str]] = []

    inner = Reader(body)
    for number, wire in inner:
        if number == 1:
            proposal = _read_proposal(inner.bytes_())
        elif kind is OpKind.REGISTER:
            if number == 2:
                resource_type_name = inner.string()
            elif number == 3:
                resource_id = inner.string()
            elif number == 4:
                node_id = inner.string()
            elif number == 5:
                body_text = inner.string()
            elif number == 6:
                created = _read_cursor(inner.bytes_())
            elif number == 7:
                updated = _read_cursor(inner.bytes_())
            elif number == 8:
                health = inner.uint()
            elif number == 9:
                expect_created = inner.bool_()
            elif number == 10:
                claim_owner = inner.uint()
            else:
                inner.skip(wire)
        elif kind is OpKind.UNREGISTER:
            if number == 2:
                resource_type_name = inner.string()
            elif number == 3:
                resource_id = inner.string()
            else:
                inner.skip(wire)
        elif kind in (OpKind.EXPIRE, OpKind.RELEASE_OWNERSHIP):
            if number == 2:
                node_id = inner.string()
            else:
                inner.skip(wire)
        elif kind is OpKind.FORGET:
            if number == 2:
                victims.append(_read_victim(inner.bytes_()))
            else:
                inner.skip(wire)
        elif kind is OpKind.CLAIM_OWNERSHIP:
            if number == 2:
                node_id = inner.string()
            elif number == 3:
                claim_owner = inner.uint()
            else:
                inner.skip(wire)
        elif kind is OpKind.MEMBER_DOWN:
            if number == 2:
                member = inner.uint()
            else:
                inner.skip(wire)
        else:
            inner.skip(wire)

    if kind is OpKind.NOOP:
        return NoopOp(proposal=proposal)
    if kind is OpKind.REGISTER:
        return RegisterOp(
            proposal=proposal,
            resource_type=_resource_type(resource_type_name),
            resource_id=resource_id, node_id=node_id, body_text=body_text,
            created=created, updated=updated, health=health,
            expect_created=expect_created, claim_owner=claim_owner,
        )
    if kind is OpKind.UNREGISTER:
        return UnregisterOp(
            proposal=proposal,
            resource_type=_resource_type(resource_type_name),
            resource_id=resource_id,
        )
    if kind is OpKind.EXPIRE:
        return ExpireOp(proposal=proposal, node_id=node_id)
    if kind is OpKind.FORGET:
        return ForgetOp(proposal=proposal, victims=tuple(victims))
    if kind is OpKind.CLAIM_OWNERSHIP:
        if claim_owner is None:
            raise RaftProtocolError("ownership claim names no owner")
        return ClaimOwnershipOp(
            proposal=proposal, node_id=node_id, owner=claim_owner,
        )
    if kind is OpKind.RELEASE_OWNERSHIP:
        return ReleaseOwnershipOp(proposal=proposal, node_id=node_id)
    return MemberDownOp(proposal=proposal, member=member)


def _read_victim(payload: bytes) -> tuple[ResourceType, str]:
    name = resource_id = ""
    reader = Reader(payload)
    for number, wire in reader:
        if number == 1:
            name = reader.string()
        elif number == 2:
            resource_id = reader.string()
        else:
            reader.skip(wire)
    return _resource_type(name), resource_id
