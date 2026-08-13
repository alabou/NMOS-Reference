# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Core value types for the registry: resource kinds, cursors, records, events.

Three ideas carry most of the weight here, and each exists for a specific
normative reason rather than for convenience.

**Resource kind as an enum, not a string.** IS-04 uses the singular form on
the Registration API wire (``{"type": "sender", ...}``) and the plural form in
URLs (``/resource/senders/{id}``, ``/senders``). Converting between them by
string surgery is how the AMWA mock registry ends up doing
``resource_type.rstrip("s")`` — which strips *every* trailing ``s``, so a
hypothetical "status" type would become "statu". ``ResourceType`` holds both
forms explicitly and parses only the exact spellings the RAML enumerates.

**Paging cursors are registry-owned, not resource-owned.** ``APIs - Query
Parameters.md:15-17`` says the registry SHOULD maintain ``creation`` and
``update`` timestamps alongside each resource, that they SHOULD NOT appear in
the response body, and that there SHOULD NOT be duplicates within a type so
that paging cannot skip a record. They are therefore a separate ``TaiCursor``
allocated by the store — deliberately *not* the resource's own ``version``
attribute, which is Node-controlled, may repeat, and may even go backwards.
(The mock registry pages on ``version``; that is one of the reasons its paging
does not match the spec.)

**Deletion is two-stage.** A resource is first marked *non-extant* and only
later *forgotten*, mirroring nmos-cpp's ``erase_resource`` →
``forget_erased_resources``. The intermediate state is what lets a removal
grain carry the resource's final content, and what keeps paging cursors
monotonic across a delete.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator

from nmos.node.types import utc_to_tai


# ---------------------------------------------------------------------------
# Resource kinds
# ---------------------------------------------------------------------------

class ResourceType(Enum):
    """The six registerable IS-04 resource types.

    The value is the *singular* name used in the Registration API POST
    envelope's ``type`` field, as fixed by
    ``registrationapi-resource-post-request.json``. ``plural`` is the URL
    segment used by both APIs, as fixed by the ``resourceType`` enum in
    ``RegistrationAPI.raml:75-82`` and the collection names in
    ``queryapi-base.json``.

    Declaration order is the registration dependency order required by
    ``Behaviour - Registration.md:57-64`` — Node, then Devices, then Sources,
    Flows, Senders, Receivers. Several places iterate this enum and rely on
    that order, so do not reorder it.
    """

    NODE = "node"
    DEVICE = "device"
    SOURCE = "source"
    FLOW = "flow"
    SENDER = "sender"
    RECEIVER = "receiver"

    @property
    def plural(self) -> str:
        """URL segment for this type (``sender`` -> ``senders``)."""
        return self.value + "s"

    @property
    def topic(self) -> str:
        """Query API WebSocket grain ``topic`` for this type.

        ``Behaviour - Querying.md:49``: the grain ``topic`` and the event
        ``path`` together form the Query API resource path, so the topic is
        the collection path with both slashes — e.g. ``/senders/``.
        """
        return f"/{self.plural}/"

    @classmethod
    def from_singular(cls, name: str) -> ResourceType | None:
        """Parse the Registration API POST envelope's ``type`` value.

        Returns None rather than raising: callers turn an unparseable type
        into an HTTP 400 with a useful message, which is more informative
        than an exception traceback.
        """
        try:
            return cls(name)
        except ValueError:
            return None

    @classmethod
    def from_plural(cls, name: str) -> ResourceType | None:
        """Parse a URL collection segment (``senders`` -> SENDER).

        Exact match only. This is what keeps a bad path segment out of the
        store instead of silently aliasing onto a real type.
        """
        for member in cls:
            if member.plural == name:
                return member
        return None


# The parent-reference attribute for each type, per
# ``Behaviour - Registration.md:57-64``. A Node has no parent. v1.0 Flows had
# no ``device_id`` and were garbage collected via ``source_id`` (``:66``);
# this registry serves v1.3 only, where ``device_id`` is required on Flows, so
# the v1.0 fallback does not apply.
PARENT_KEY: dict[ResourceType, str | None] = {
    ResourceType.NODE: None,
    ResourceType.DEVICE: "node_id",
    ResourceType.SOURCE: "device_id",
    ResourceType.FLOW: "device_id",
    ResourceType.SENDER: "device_id",
    ResourceType.RECEIVER: "device_id",
}

# The type a given type's parent reference must point at. Used to enforce
# ``Behaviour - Registration.md:104`` — "the parent resource referred to
# either doesn't exist in the registry or the ID matches the wrong type of
# resource" is a 400.
PARENT_TYPE: dict[ResourceType, ResourceType | None] = {
    ResourceType.NODE: None,
    ResourceType.DEVICE: ResourceType.NODE,
    ResourceType.SOURCE: ResourceType.DEVICE,
    ResourceType.FLOW: ResourceType.DEVICE,
    ResourceType.SENDER: ResourceType.DEVICE,
    ResourceType.RECEIVER: ResourceType.DEVICE,
}


# ---------------------------------------------------------------------------
# TAI cursors
# ---------------------------------------------------------------------------

@dataclass(frozen=True, order=True)
class TaiCursor:
    """A ``<seconds>:<nanoseconds>`` TAI instant used as a paging cursor.

    Ordering is lexicographic on (seconds, nanoseconds), which ``order=True``
    gives us from the field declaration order — so cursors sort correctly with
    plain ``<``/``>`` and ``sorted()``.

    Frozen because a cursor is a recorded instant. Re-stamping a resource
    allocates a new cursor rather than mutating the old one, which is what
    keeps the "no duplicate timestamps within a type" invariant checkable.
    """

    seconds: int
    nanoseconds: int

    def __str__(self) -> str:
        return f"{self.seconds}:{self.nanoseconds}"

    @classmethod
    def now(cls) -> TaiCursor:
        """Current wall-clock instant as TAI."""
        sec, nsec = utc_to_tai(time.time())
        return cls(sec, nsec)

    @classmethod
    def min(cls) -> TaiCursor:
        """The ``0:0`` cursor.

        ``APIs - Query Parameters.md:100`` defines the ``first`` paging link
        as the query with ``paging.since=0:0``, so this is a real protocol
        value and not just a sentinel.
        """
        return cls(0, 0)

    @classmethod
    def parse(cls, text: str) -> TaiCursor | None:
        """Parse ``"<seconds>:<nanoseconds>"``.

        Returns None on anything that does not match the RAML pattern
        ``^[0-9]+:[0-9]+$`` (``QueryAPI.raml:31,35``) so the caller can answer
        400 rather than raise. Note the pattern permits neither a sign nor
        whitespace, so ``int()`` on the halves is not sufficient on its own —
        ``int("+1")`` and ``int(" 1")`` both succeed but are not valid
        cursors.
        """
        head, sep, tail = text.partition(":")
        if not sep or not head.isdigit() or not tail.isdigit():
            return None
        return cls(int(head), int(tail))

    def next(self) -> TaiCursor:
        """The smallest cursor strictly greater than this one.

        Used by the store to break ties when two resources of the same type
        would otherwise land on the same instant, preserving the uniqueness
        that ``APIs - Query Parameters.md:17`` asks for.
        """
        if self.nanoseconds >= 999_999_999:
            return TaiCursor(self.seconds + 1, 0)
        return TaiCursor(self.seconds, self.nanoseconds + 1)


# ---------------------------------------------------------------------------
# Stored resources
# ---------------------------------------------------------------------------

@dataclass
class RegisteredResource:
    """One resource held by the registry.

    Two representations are kept deliberately:

    ``typed`` is the decoded generated type (``NNodeValue``, ``NSenderValue``,
    …). Producing it *is* the schema validation required by ``APIs.md:22`` —
    a resource that will not decode, or that fails ``assert_valid()``, is
    rejected with a 400. It is also what gives the rest of the code a checked
    view of the resource.

    ``raw`` is the JSON object exactly as the Node sent it, and is what both
    the Query API and the WebSocket grains actually serve. This matters
    because the generated types do not model every attribute of every
    resource — ``node.json`` declares an optional, deprecated ``hostname``
    that ``NNode`` has no member for, and a third-party Node may legitimately
    carry vendor extensions. Serving ``raw`` means the registry never
    silently rewrites a Node's registration, and guarantees the HTTP and
    WebSocket views agree byte-for-byte.
    """

    resource_type: ResourceType
    id: str
    typed: Any
    raw: dict[str, Any]
    version: str
    """The resource's own ``version`` attribute, ``"<sec>:<nsec>"``.

    Node-controlled. Used only for the monotonicity check of
    ``Behaviour - Registration.md:102`` — never as a paging cursor.
    """

    created: TaiCursor
    """Registry-assigned creation cursor. Stable across updates."""

    updated: TaiCursor
    """Registry-assigned update cursor. Re-stamped on every accepted POST."""

    parent_id: str | None
    """Value of this type's PARENT_KEY attribute, or None for a Node."""

    extant: bool = True
    """False once deleted or garbage collected, until forgotten.

    A non-extant resource is excluded from every Query API response and from
    every subscription match, but is still counted in the status line's
    "non-extant resources" figure and still participates in ``least health``.
    """

    health: int = 0
    """Liveness timestamp in TAI seconds. Every resource carries one.

    Only Nodes are heartbeated — ``Behaviour - Registration.md:51``: "Nodes
    only need perform a heartbeat to maintain their Node resource" — but a
    heartbeat refreshes the health of the Node **and, recursively, all of its
    sub-resources**. This mirrors nmos-cpp's ``set_resource_health``, whose
    own comment is "set the health of the resource and all of its
    sub-resources, to prevent them expiring".

    That recursion is not an optimisation, it is what makes garbage collection
    correct: GC expires *any* resource whose health has fallen behind the
    collection interval, so if a heartbeat refreshed only the Node, every
    sub-resource would expire on its own after one interval and the Node
    would be left childless while still alive.
    """

    def version_cursor(self) -> TaiCursor | None:
        """Parse ``version`` into a comparable cursor, or None if malformed."""
        return TaiCursor.parse(self.version)


@dataclass
class Tombstone:
    """A forgotten-pending record of a resource that has been removed.

    Kept only so the two-stage lifecycle has something to time out against;
    the resource's content lives on in the ``RegisteredResource`` until the
    forget interval elapses.
    """

    resource_type: ResourceType
    id: str
    erased_at: TaiCursor


# ---------------------------------------------------------------------------
# Change events
# ---------------------------------------------------------------------------

class EventKind(Enum):
    """The four Query API WebSocket event shapes.

    ``Behaviour - Querying.md:85-210`` defines these by which of ``pre`` and
    ``post`` are present, rather than by an explicit discriminator on the
    wire. Naming them here keeps the intent readable at the call sites; the
    grain builder converts the name back into the pre/post shape.
    """

    ADDED = "added"       # post only
    REMOVED = "removed"   # pre only
    MODIFIED = "modified"  # pre and post, differing
    SYNC = "sync"         # pre and post, identical


@dataclass(frozen=True)
class ResourceEvent:
    """A single change to a single resource, ready to become grain data.

    ``pre`` and ``post`` are raw JSON objects (or None), taken straight from
    ``RegisteredResource.raw`` — see that field's note for why the raw form
    is what gets published.
    """

    kind: EventKind
    resource_type: ResourceType
    resource_id: str
    pre: dict[str, Any] | None
    post: dict[str, Any] | None

    @classmethod
    def added(cls, res: RegisteredResource) -> ResourceEvent:
        return cls(EventKind.ADDED, res.resource_type, res.id, None, res.raw)

    @classmethod
    def removed(cls, res: RegisteredResource) -> ResourceEvent:
        return cls(EventKind.REMOVED, res.resource_type, res.id, res.raw, None)

    @classmethod
    def modified(
        cls, pre: dict[str, Any], res: RegisteredResource,
    ) -> ResourceEvent:
        return cls(EventKind.MODIFIED, res.resource_type, res.id, pre, res.raw)

    @classmethod
    def sync(cls, res: RegisteredResource) -> ResourceEvent:
        """A synchronisation event: ``pre`` and ``post`` identical.

        ``Behaviour - Querying.md:166`` — used for the initial burst that
        tells a newly connected client the current state of the topic.
        """
        return cls(EventKind.SYNC, res.resource_type, res.id, res.raw, res.raw)


# ---------------------------------------------------------------------------
# Registration outcomes
# ---------------------------------------------------------------------------

class RegistrationError(Enum):
    """The 400-yielding conditions of ``Behaviour - Registration.md:98-104``.

    Modelled as an enum rather than free text so the handler maps each to a
    fixed status and the tests can assert on the specific condition rather
    than on message wording.
    """

    SCHEMA = "schema"
    """Body does not meet the JSON schema for that resource type (``:100``)."""

    ID_TYPE_CONFLICT = "id_type_conflict"
    """The id is already used by another resource type (``:101``)."""

    VERSION_REGRESSION = "version_regression"
    """The version is earlier than the stored one (``:102``)."""

    PARENT_CHANGED = "parent_changed"
    """A parent resource id was modified during an update (``:103``)."""

    PARENT_MISSING = "parent_missing"
    """Parent absent, or the id names the wrong resource type (``:104``)."""


@dataclass(frozen=True)
class PreparedRegistration:
    """A registration that has passed validation but has not been applied.

    The gap between deciding and applying is what the distributed backend needs:
    it validates against the local store, commits to etcd, and only then applies
    — and the thing it has to carry across those steps is exactly this. Frozen,
    because a decision made against one state must not be quietly edited before
    being applied against another.

    ``creates`` is the 201-vs-200 answer of ``Behaviour - Registration.md:25``,
    decided here rather than inferred later from whether the store happened to
    hold the id.
    """

    resource_type: ResourceType
    resource_id: str
    version: str
    parent_id: str | None

    creates: bool
    """True when this registration adds a resource rather than updating one."""

    reviving: bool
    """True when the id exists but is non-extant.

    A separate flag from ``creates`` even though a revive always creates:
    application has to clear the old parent/child links that the tombstoned
    record still carries, and only this distinguishes that case from a genuinely
    new id.
    """


@dataclass
class RegistrationResult:
    """Outcome of a ``POST /resource``.

    ``created`` distinguishes the 201 and 200 responses of
    ``Behaviour - Registration.md:25``; it is meaningful only when ``error``
    is None.
    """

    created: bool
    events: list[ResourceEvent] = field(default_factory=list)
    error: RegistrationError | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.error is None

    @classmethod
    def failure(
        cls, error: RegistrationError, detail: str,
    ) -> RegistrationResult:
        return cls(created=False, error=error, detail=detail)


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RegistryStatistics:
    """Snapshot backing the periodic status line.

    Field names and semantics follow nmos-cpp's ``put_resources_statistics``
    (``Development/nmos/log_manip.h``) exactly, so the rendered line is
    directly comparable with an nmos-cpp registry's log. In particular
    ``total`` counts every *extant* resource across all eight kinds —
    subscriptions and grains included — and ``non_extant`` is reported
    separately rather than subtracted from ``total`` or from the per-type
    counts.

    ``least_health`` is the minimum health among **extant** resources only.
    nmos-cpp's ``least_health()`` returns a pair (extant, non-extant) and the
    status line prints ``.first``; when there is nothing to take a minimum
    over, both halves default to the current health, so an empty registry
    reports "least health: <now>" rather than 0.
    """

    total: int
    per_type: dict[ResourceType, int]
    subscriptions: int
    grains: int
    most_recent_update: TaiCursor
    least_health: int
    non_extant: int

    def __iter__(self) -> Iterator[tuple[str, int]]:
        """Yield the eight per-kind counters in nmos-cpp's fixed order."""
        for rt in ResourceType:
            yield rt.plural, self.per_type.get(rt, 0)
        yield "subscriptions", self.subscriptions
        yield "grains", self.grains

    def render(self) -> str:
        """Render the counters exactly as nmos-cpp does.

        ``"<total> resources (<n> nodes, …, <n> grains), most recent update:
        <ver>, least health: <h>, <n> non-extant resources"``. The caller
        prefixes ``"At <now>, the registry contains "``.
        """
        parts = ", ".join(f"{count} {label}" for label, count in self)
        return (
            f"{self.total} resources ({parts}), "
            f"most recent update: {self.most_recent_update}, "
            f"least health: {self.least_health}, "
            f"{self.non_extant} non-extant resources"
        )
