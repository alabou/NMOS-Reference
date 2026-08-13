# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for RegistryStore.

Each test names the normative requirement it pins. Where the AMWA test-suite
mock registry behaves differently, that is called out — those divergences are
the reason several of these tests exist.
"""

from __future__ import annotations

import pytest

from nmos.registry.decode import DecodeFailure, decode_resource
from nmos.registry.store import RegistryStore, health_now
from nmos.registry.tests._fixtures import (
    DEVICE_ID,
    DEVICE_ID_2,
    FLOW_ID,
    NODE_ID,
    NODE_ID_2,
    RECEIVER_ID,
    SENDER_ID,
    SOURCE_ID,
    make_device,
    make_flow,
    make_node,
    make_receiver,
    make_sender,
    make_source,
    tai_version,
)
from nmos.registry.types import (
    EventKind,
    RegistrationError,
    RegistrationResult,
    ResourceType,
    TaiCursor,
)


def register(store: RegistryStore, resource_type: ResourceType, raw: dict[str, object]):
    """Validate then insert, the way the Registration API handler does.

    ``decode_resource`` is called for its validation side effect only, exactly
    as ``decode_post_envelope`` does; its result is not stored.
    """
    decode_resource(resource_type, raw)
    return store.insert_or_update(resource_type, dict(raw))


def register_tree(store: RegistryStore) -> None:
    """Register the full Node -> Device -> Source -> Flow -> Sender/Receiver tree.

    In the dependency order mandated by ``Behaviour - Registration.md:57-64``.
    """
    register(store, ResourceType.NODE, make_node())
    register(store, ResourceType.DEVICE, make_device())
    register(store, ResourceType.SOURCE, make_source())
    register(store, ResourceType.FLOW, make_flow())
    register(store, ResourceType.SENDER, make_sender())
    register(store, ResourceType.RECEIVER, make_receiver())


@pytest.fixture
def store() -> RegistryStore:
    return RegistryStore(gc_interval=12.0, forget_interval=60.0)


# ---------------------------------------------------------------------------
# Registration basics
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_first_registration_creates(self, store: RegistryStore) -> None:
        """:25 -- a new record answers 201 (Created)."""
        result = register(store, ResourceType.NODE, make_node())
        assert result.ok
        assert result.created is True
        assert [e.kind for e in result.events] == [EventKind.ADDED]

    def test_second_registration_updates(self, store: RegistryStore) -> None:
        """:25 -- "The Registration API indicates that it has received an
        update to a previous record by sending a 200 (OK) response, rather
        than a 201 (Created) response"."""
        register(store, ResourceType.NODE, make_node())
        result = register(
            store, ResourceType.NODE,
            make_node(version=tai_version(+1), label="renamed"),
        )
        assert result.ok
        assert result.created is False
        assert [e.kind for e in result.events] == [EventKind.MODIFIED]

    def test_modified_event_carries_both_states(self, store: RegistryStore) -> None:
        """Querying:135 -- a modified event carries pre AND post, with all
        attributes present rather than only the changed ones."""
        register(store, ResourceType.NODE, make_node(label="before"))
        result = register(
            store, ResourceType.NODE,
            make_node(version=tai_version(+1), label="after"),
        )
        event = result.events[0]
        assert event.pre is not None and event.post is not None
        assert event.pre["label"] == "before"
        assert event.post["label"] == "after"
        # "All attributes of the resource MUST be specified".
        assert set(event.pre) == set(event.post)
        assert "interfaces" in event.post

    def test_raw_json_is_served_verbatim(self, store: RegistryStore) -> None:
        """Vendor extensions and unmodelled attributes must survive.

        ``node.json`` declares an optional, deprecated ``hostname`` that
        ``NNode`` has no member for. Storing only the typed view would drop
        it, and the registry would be silently rewriting a Node's
        registration.
        """
        raw = make_node(hostname="studio-node-1.example.com")
        raw["urn:x-vendor:private"] = {"nested": [1, 2, 3]}
        register(store, ResourceType.NODE, raw)

        stored = store.get(ResourceType.NODE, NODE_ID)
        assert stored is not None
        assert stored.raw["hostname"] == "studio-node-1.example.com"
        assert stored.raw["urn:x-vendor:private"] == {"nested": [1, 2, 3]}

    def test_invalid_resource_is_rejected(self) -> None:
        """:100 -- a body that does not meet the schema is a 400.

        Decoding is the validation, so a Node missing its required
        ``interfaces`` never reaches the store.
        """
        broken = make_node()
        del broken["interfaces"]
        with pytest.raises(DecodeFailure):
            decode_resource(ResourceType.NODE, broken)


# ---------------------------------------------------------------------------
# The 400 conditions of Behaviour - Registration.md:98-104
# ---------------------------------------------------------------------------

class TestRegistrationErrors:
    def test_id_reused_by_another_type(self, store: RegistryStore) -> None:
        """:101 -- "The id included in the request has already been used by
        another resource type held in the registry"."""
        register(store, ResourceType.NODE, make_node())
        # Re-use the Node's id for a Device.
        result = register(
            store, ResourceType.DEVICE, make_device(device_id=NODE_ID),
        )
        assert result.error is RegistrationError.ID_TYPE_CONFLICT

    def test_version_regression(self, store: RegistryStore) -> None:
        """:102 -- "The version included in the request is earlier than the
        matching resource already held in the registry"."""
        register(store, ResourceType.NODE, make_node(version=tai_version(+10)))
        result = register(
            store, ResourceType.NODE, make_node(version=tai_version(-10)),
        )
        assert result.error is RegistrationError.VERSION_REGRESSION

    def test_identical_version_is_accepted(self, store: RegistryStore) -> None:
        """Re-POSTing an unchanged version is NOT an error.

        A Node that gets a 404 on heartbeat "MUST re-register each of its
        resources" (:114), replaying them verbatim. Rejecting an equal
        version would break that documented recovery path.
        """
        version = tai_version()
        register(store, ResourceType.NODE, make_node(version=version))
        result = register(store, ResourceType.NODE, make_node(version=version))
        assert result.ok
        assert result.created is False

    def test_parent_id_modified(self, store: RegistryStore) -> None:
        """:103 -- "A parent resource ID has been modified (for example the
        node_id in a Device registration is modified during an update)"."""
        register(store, ResourceType.NODE, make_node())
        register(store, ResourceType.NODE, make_node(node_id=NODE_ID_2))
        register(store, ResourceType.DEVICE, make_device())

        result = register(
            store, ResourceType.DEVICE,
            make_device(node_id=NODE_ID_2, version=tai_version(+1)),
        )
        assert result.error is RegistrationError.PARENT_CHANGED

    def test_parent_missing(self, store: RegistryStore) -> None:
        """:55 / :104 -- referential integrity.

        "resources MUST only be accepted by a Registration API where the
        registry already has a record of the corresponding parent resource".
        The AMWA mock skips this check entirely.
        """
        result = register(store, ResourceType.DEVICE, make_device())
        assert result.error is RegistrationError.PARENT_MISSING

    def test_parent_is_wrong_type(self, store: RegistryStore) -> None:
        """:104 -- "the ID matches the wrong type of resource".

        A Source whose device_id names a Node, not a Device.
        """
        register(store, ResourceType.NODE, make_node())
        result = register(
            store, ResourceType.SOURCE, make_source(device_id=NODE_ID),
        )
        assert result.error is RegistrationError.PARENT_MISSING

    def test_registration_order_is_enforced_end_to_end(
        self, store: RegistryStore,
    ) -> None:
        """:57-64 -- the documented order succeeds; reversing it does not."""
        register_tree(store)
        for resource_type in ResourceType:
            assert store.count_extant(resource_type) == 1

        fresh = RegistryStore()
        # Sender before its Device (and before the Node) must be refused.
        assert (
            register(fresh, ResourceType.SENDER, make_sender()).error
            is RegistrationError.PARENT_MISSING
        )


# ---------------------------------------------------------------------------
# Deletion and cascade
# ---------------------------------------------------------------------------

class TestDeletion:
    def test_delete_unknown_returns_none(self, store: RegistryStore) -> None:
        """RegistrationAPI.raml:93 -- 404 when the resource does not exist."""
        assert store.delete(ResourceType.NODE, NODE_ID) is None

    def test_delete_cascades_to_all_descendants(
        self, store: RegistryStore,
    ) -> None:
        """:68 -- "Where a DELETE is issued against a parent resource, all
        child resources MUST be removed from the registry immediately".

        The AMWA mock removes only the addressed resource, leaving orphans
        that nothing will ever collect.
        """
        register_tree(store)
        events = store.delete(ResourceType.NODE, NODE_ID)

        assert events is not None
        assert len(events) == 6
        assert all(e.kind is EventKind.REMOVED for e in events)
        for resource_type in ResourceType:
            assert store.count_extant(resource_type) == 0

    def test_cascade_removes_children_before_parents(
        self, store: RegistryStore,
    ) -> None:
        """Children are erased first, mirroring the registration order.

        A client replaying the events in order never sees a parent vanish
        while its children are still present.
        """
        register_tree(store)
        events = store.delete(ResourceType.NODE, NODE_ID)
        assert events is not None
        order = [e.resource_id for e in events]
        assert order.index(SENDER_ID) < order.index(DEVICE_ID)
        assert order.index(FLOW_ID) < order.index(DEVICE_ID)
        assert order.index(SOURCE_ID) < order.index(DEVICE_ID)
        assert order.index(RECEIVER_ID) < order.index(DEVICE_ID)
        assert order.index(DEVICE_ID) < order.index(NODE_ID)

    def test_out_of_order_unregistration_is_cleaned_up(
        self, store: RegistryStore,
    ) -> None:
        """:74 -- "If a Node unregisters a resource in the incorrect order,
        the Registration API MUST clean up related child resources on the
        Node's behalf in order to prevent stale entries remaining"."""
        register_tree(store)
        # Delete the Device without deleting its children first.
        store.delete(ResourceType.DEVICE, DEVICE_ID)
        assert store.count_extant(ResourceType.SENDER) == 0
        assert store.count_extant(ResourceType.RECEIVER) == 0
        assert store.count_extant(ResourceType.FLOW) == 0
        assert store.count_extant(ResourceType.SOURCE) == 0
        # The Node itself is untouched -- it is the parent, not a child.
        assert store.count_extant(ResourceType.NODE) == 1

    def test_deleted_resource_is_hidden_but_not_forgotten(
        self, store: RegistryStore,
    ) -> None:
        """Stage one of the two-stage lifecycle.

        Invisible to API clients, still present for the statistics and the
        forget timer.
        """
        register(store, ResourceType.NODE, make_node())
        store.delete(ResourceType.NODE, NODE_ID)

        assert store.get(ResourceType.NODE, NODE_ID) is None
        assert store.get(ResourceType.NODE, NODE_ID, include_non_extant=True) is not None
        assert store.statistics().non_extant == 1

    def test_reregistering_a_deleted_id_creates(
        self, store: RegistryStore,
    ) -> None:
        """A tombstone must not make a fresh registration look like an update.

        The Node's own state machine keys off 201-vs-200 (:90-92), so a
        re-registration after deletion has to answer 201.
        """
        register(store, ResourceType.NODE, make_node())
        store.delete(ResourceType.NODE, NODE_ID)
        result = register(store, ResourceType.NODE, make_node())
        assert result.created is True
        assert store.statistics().non_extant == 0

    def test_revived_node_does_not_adopt_erased_children(
        self, store: RegistryStore,
    ) -> None:
        """A tombstoned child must not be re-attached to a fresh parent.

        Otherwise a later cascade delete would emit removal events for
        resources the client was already told had gone.
        """
        register_tree(store)
        store.delete(ResourceType.NODE, NODE_ID)
        register(store, ResourceType.NODE, make_node())

        events = store.delete(ResourceType.NODE, NODE_ID)
        assert events is not None
        assert [e.resource_id for e in events] == [NODE_ID]


# ---------------------------------------------------------------------------
# Health, heartbeat and garbage collection
# ---------------------------------------------------------------------------

class TestHealthAndGarbageCollection:
    def test_heartbeat_unknown_node(self, store: RegistryStore) -> None:
        """:112-114 -- 404 on heartbeat for a Node the registry does not hold."""
        assert store.heartbeat(NODE_ID) is None

    def test_heartbeat_returns_health(self, store: RegistryStore) -> None:
        register(store, ResourceType.NODE, make_node())
        health = store.heartbeat(NODE_ID)
        assert health is not None
        assert abs(health - health_now()) <= 1

    def test_heartbeat_refreshes_descendants(
        self, store: RegistryStore,
    ) -> None:
        """A heartbeat refreshes the Node AND every sub-resource.

        This mirrors nmos-cpp's ``set_resource_health``, whose comment is
        "set the health of the resource and all of its sub-resources, to
        prevent them expiring". Without the recursion each sub-resource would
        expire independently one collection interval after registration,
        leaving a live Node with no children.
        """
        register_tree(store)
        for resource_type in ResourceType:
            for resource in store.iter_extant(resource_type):
                resource.health = 1000

        store.heartbeat(NODE_ID)

        for resource_type in ResourceType:
            for resource in store.iter_extant(resource_type):
                assert resource.health > 1000, f"{resource_type.value} not refreshed"

    def test_gc_collects_silent_node_and_subresources(
        self, store: RegistryStore,
    ) -> None:
        """:51 -- "If heartbeats fail over a period greater than the garbage
        collection interval, both the Node and all registered sub-resources
        SHOULD be removed from the registry automatically".

        The AMWA mock records the heartbeat time but never acts on it.
        """
        register_tree(store)
        stale = health_now() - 13  # one second past the 12 s interval
        for resource_type in ResourceType:
            for resource in store.iter_extant(resource_type):
                resource.health = stale

        events = store.collect_garbage()

        assert len(events) == 6
        assert all(e.kind is EventKind.REMOVED for e in events)
        for resource_type in ResourceType:
            assert store.count_extant(resource_type) == 0

    def test_gc_spares_a_heartbeating_node(self, store: RegistryStore) -> None:
        register_tree(store)
        store.heartbeat(NODE_ID)
        assert store.collect_garbage() == []
        assert store.count_extant(ResourceType.NODE) == 1

    def test_gc_boundary_is_the_interval(self, store: RegistryStore) -> None:
        """Collection triggers strictly past the interval, not at it.

        12 s is "just after two failed heartbeats at the default 5 second
        interval" (:47); expiring exactly at the boundary would collect a
        Node whose third heartbeat is still in flight.
        """
        register(store, ResourceType.NODE, make_node())
        node = store.get(ResourceType.NODE, NODE_ID)
        assert node is not None

        node.health = health_now() - 12
        assert store.collect_garbage() == []
        assert store.count_extant(ResourceType.NODE) == 1

        node.health = health_now() - 13
        assert len(store.collect_garbage()) == 1

    def test_forget_drops_tombstone_after_interval(self) -> None:
        """Stage two: a non-extant record is eventually dropped entirely."""
        store = RegistryStore(gc_interval=12.0, forget_interval=60.0)
        register(store, ResourceType.NODE, make_node())
        store.delete(ResourceType.NODE, NODE_ID)
        assert store.statistics().non_extant == 1

        # Not yet due.
        store.collect_garbage()
        assert store.statistics().non_extant == 1

        stored = store.get(ResourceType.NODE, NODE_ID, include_non_extant=True)
        assert stored is not None
        stored.health = health_now() - 61

        store.collect_garbage()
        assert store.statistics().non_extant == 0
        assert store.get(ResourceType.NODE, NODE_ID, include_non_extant=True) is None


# ---------------------------------------------------------------------------
# Paging cursors
# ---------------------------------------------------------------------------

class TestCursors:
    def test_cursors_are_unique_within_a_type(
        self, store: RegistryStore,
    ) -> None:
        """:17 -- "there SHOULD NOT be duplicate creation or update
        timestamps stored against resources of the same type".

        Two registrations inside one clock tick must still get distinct
        cursors, or a client paging from the first would skip the second.
        """
        register(store, ResourceType.NODE, make_node())
        register(store, ResourceType.NODE, make_node(node_id=NODE_ID_2))

        cursors = [r.created for r in store.iter_extant(ResourceType.NODE)]
        assert len(set(cursors)) == 2

    def test_cursors_are_monotonic(self, store: RegistryStore) -> None:
        register(store, ResourceType.NODE, make_node())
        register(store, ResourceType.NODE, make_node(node_id=NODE_ID_2))
        register(store, ResourceType.DEVICE, make_device())
        register(
            store, ResourceType.DEVICE,
            make_device(device_id=DEVICE_ID_2, node_id=NODE_ID_2),
        )

        for resource_type in (ResourceType.NODE, ResourceType.DEVICE):
            seen = [r.created for r in store.iter_extant(resource_type)]
            assert seen == sorted(seen)

    def test_created_is_stable_across_updates(
        self, store: RegistryStore,
    ) -> None:
        """``paging.order=create`` is only meaningful if creation is stable."""
        register(store, ResourceType.NODE, make_node())
        original = store.get(ResourceType.NODE, NODE_ID)
        assert original is not None
        created = original.created

        register(
            store, ResourceType.NODE,
            make_node(version=tai_version(+1), label="renamed"),
        )
        updated = store.get(ResourceType.NODE, NODE_ID)
        assert updated is not None
        assert updated.created == created
        assert updated.updated > created

    def test_registry_cursors_are_not_the_resource_version(
        self, store: RegistryStore,
    ) -> None:
        """:15-17 -- cursors are registry-maintained, not Node-supplied.

        The AMWA mock pages on the resource's own ``version``, which is
        Node-controlled and may repeat across resources.
        """
        shared = tai_version(-3600)
        register(store, ResourceType.NODE, make_node(version=shared))
        register(
            store, ResourceType.NODE,
            make_node(node_id=NODE_ID_2, version=shared),
        )
        resources = list(store.iter_extant(ResourceType.NODE))
        assert resources[0].version == resources[1].version
        assert resources[0].created != resources[1].created

    def test_cursor_parse_rejects_malformed(self) -> None:
        """QueryAPI.raml:31 -- the pattern is ``^[0-9]+:[0-9]+$``.

        ``int()`` alone would accept a sign or surrounding whitespace, which
        the RAML pattern does not.
        """
        assert TaiCursor.parse("1441716120:318744030") == TaiCursor(
            1441716120, 318744030,
        )
        assert TaiCursor.parse("0:0") == TaiCursor.min()
        for bad in ("", "1441716120", "abc:def", "-1:0", " 1:0", "1:0 ", "1:2:3"):
            assert TaiCursor.parse(bad) is None, bad

    def test_cursor_next_carries_into_the_next_second(self) -> None:
        assert TaiCursor(5, 0).next() == TaiCursor(5, 1)
        assert TaiCursor(5, 999_999_999).next() == TaiCursor(6, 0)


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

class TestStatistics:
    def test_empty_registry(self, store: RegistryStore) -> None:
        """An empty registry reports "least health: <now>", not 0.

        nmos-cpp's ``least_health`` seeds both halves of its result with the
        current health, so there is no minimum to take over an empty set.
        """
        stats = store.statistics()
        assert stats.total == 0
        assert stats.non_extant == 0
        assert stats.most_recent_update == TaiCursor.min()
        assert abs(stats.least_health - health_now()) <= 1

    def test_total_counts_all_extant_kinds(self, store: RegistryStore) -> None:
        """nmos-cpp's ``by_type.count(true)`` is every extant resource.

        Subscriptions and grains are included in the leading total, not just
        the six IS-04 types -- they merely happen to be zero in the commonly
        quoted sample lines.
        """
        register_tree(store)
        stats = store.statistics(subscriptions=2, grains=3)
        assert stats.total == 6 + 2 + 3
        assert stats.subscriptions == 2
        assert stats.grains == 3

    def test_non_extant_is_not_subtracted(self, store: RegistryStore) -> None:
        """``by_type.count(false)`` is reported alongside, not deducted."""
        register_tree(store)
        store.delete(ResourceType.SENDER, SENDER_ID)

        stats = store.statistics()
        assert stats.non_extant == 1
        assert stats.per_type[ResourceType.SENDER] == 0
        assert stats.total == 5

    def test_render_matches_nmos_cpp_format(self, store: RegistryStore) -> None:
        """The rendered line must be directly comparable with nmos-cpp's.

        Format from ``put_resources_statistics`` in
        ``Development/nmos/log_manip.h``.
        """
        register(store, ResourceType.NODE, make_node())
        node = store.get(ResourceType.NODE, NODE_ID)
        assert node is not None
        node.health = 1785684536
        node.updated = TaiCursor(1785604718, 431043782)

        assert store.statistics().render() == (
            "1 resources (1 nodes, 0 devices, 0 sources, 0 flows, "
            "0 senders, 0 receivers, 0 subscriptions, 0 grains), "
            "most recent update: 1785604718:431043782, "
            "least health: 1785684536, 0 non-extant resources"
        )


# ---------------------------------------------------------------------------
# Resource type parsing
# ---------------------------------------------------------------------------

class TestResourceType:
    def test_singular_and_plural(self) -> None:
        assert ResourceType.SENDER.value == "sender"
        assert ResourceType.SENDER.plural == "senders"
        assert ResourceType.SENDER.topic == "/senders/"

    def test_parsing_is_exact(self) -> None:
        """No ``rstrip("s")``.

        The AMWA mock derives the singular by stripping trailing ``s``
        characters, which mangles any name ending in more than one.
        """
        assert ResourceType.from_plural("senders") is ResourceType.SENDER
        assert ResourceType.from_singular("sender") is ResourceType.SENDER
        # Wrong number, unknown names and near-misses are all rejected.
        assert ResourceType.from_plural("sender") is None
        assert ResourceType.from_singular("senders") is None
        assert ResourceType.from_plural("subscriptions") is None
        assert ResourceType.from_plural("") is None

    def test_declaration_order_is_registration_order(self) -> None:
        """:57-64 -- several call sites iterate the enum and rely on this."""
        assert list(ResourceType) == [
            ResourceType.NODE,
            ResourceType.DEVICE,
            ResourceType.SOURCE,
            ResourceType.FLOW,
            ResourceType.SENDER,
            ResourceType.RECEIVER,
        ]


class TestCursorOrderedIndexes:
    """The indexes that let a Query page without sorting.

    ``iter_ordered`` is a performance optimisation with a correctness
    obligation: it must return exactly what ``sorted(iter_extant(), key=...)``
    would, or paged Query answers change. Every test here is that equivalence
    under a different mutation shape.
    """

    @staticmethod
    def _ids(store: RegistryStore, resource_type: ResourceType, order: str):
        return [r.id for r in store.iter_ordered(resource_type, order)]

    @staticmethod
    def _expected(store: RegistryStore, resource_type: ResourceType, order: str):
        key = (lambda r: r.created) if order == "create" else (lambda r: r.updated)
        return [
            r.id for r in
            sorted(store.iter_extant(resource_type), key=lambda r: (key(r), r.id))
        ]

    def _assert_matches_sort(self, store: RegistryStore) -> None:
        for resource_type in ResourceType:
            for order in ("create", "update"):
                assert self._ids(store, resource_type, order) == self._expected(
                    store, resource_type, order,
                ), f"{resource_type} by {order}"

    def test_registration_order_is_ascending_in_both_indexes(self) -> None:
        store = RegistryStore()
        register_tree(store)
        register(store, ResourceType.DEVICE, make_device(DEVICE_ID_2))
        self._assert_matches_sort(store)

    def test_an_update_moves_only_the_update_index(self) -> None:
        """``created`` does not move on an update, so the create index must not.

        This is the whole reason the two indexes are maintained separately
        rather than one being derived from the other.
        """
        store = RegistryStore()
        register_tree(store)
        register(store, ResourceType.DEVICE, make_device(DEVICE_ID_2))

        create_before = self._ids(store, ResourceType.DEVICE, "create")
        assert self._ids(store, ResourceType.DEVICE, "update") == [
            DEVICE_ID, DEVICE_ID_2,
        ]

        register(store, ResourceType.DEVICE, make_device(version=tai_version()))

        assert self._ids(store, ResourceType.DEVICE, "create") == create_before
        assert self._ids(store, ResourceType.DEVICE, "update") == [
            DEVICE_ID_2, DEVICE_ID,
        ]
        self._assert_matches_sort(store)

    def test_a_revive_moves_both_indexes(self) -> None:
        """A revive assigns a fresh ``created``, so the create index reorders.

        The record is replaced in place, so the underlying dict keeps its old
        position — which is exactly the case a naive "insertion order is create
        order" index gets wrong.
        """
        store = RegistryStore()
        register_tree(store)
        register(store, ResourceType.DEVICE, make_device(DEVICE_ID_2))

        store.delete(ResourceType.DEVICE, DEVICE_ID)
        register(store, ResourceType.DEVICE, make_device(version=tai_version()))

        assert self._ids(store, ResourceType.DEVICE, "create") == [
            DEVICE_ID_2, DEVICE_ID,
        ]
        self._assert_matches_sort(store)

    def test_non_extant_resources_are_skipped(self) -> None:
        store = RegistryStore()
        register_tree(store)
        register(store, ResourceType.DEVICE, make_device(DEVICE_ID_2))

        store.delete(ResourceType.DEVICE, DEVICE_ID_2)

        assert DEVICE_ID_2 not in self._ids(store, ResourceType.DEVICE, "update")
        self._assert_matches_sort(store)

    def test_forgetting_drops_from_both_indexes(self) -> None:
        # A negative forget interval makes stage two of the deletion
        # lifecycle fire on the next pass instead of a minute later.
        store = RegistryStore(forget_interval=-1.0)
        register_tree(store)
        store.delete(ResourceType.NODE, NODE_ID)
        store.collect_garbage()

        for resource_type in ResourceType:
            for order in ("create", "update"):
                assert self._ids(store, resource_type, order) == []
        # The index must be emptied, not merely filtered on read: a leaked
        # entry would keep the forgotten resource's id alive forever.
        assert all(not index for index in store._order.values())

    def test_out_of_order_cursors_are_re_sorted_lazily(self) -> None:
        """The preload shape: cursors supplied by etcd, applied in key order.

        ``apply_committed`` accepts the authoritative cursor, so the store does
        not choose it and cannot assume it increases. Appending blindly would
        corrupt the order silently; the dirty flag turns it into one re-sort.
        """
        store = RegistryStore()
        register(store, ResourceType.NODE, make_node())

        # Applied newest-first, which is what a key-ordered preload can produce.
        for device_id, seconds in (
            (DEVICE_ID_2, 5000), (DEVICE_ID, 1000),
        ):
            raw = make_device(device_id)
            prepared = store.prepare(ResourceType.DEVICE, raw)
            assert not isinstance(prepared, RegistrationResult)
            store.apply_committed(
                prepared, dict(raw),
                created=TaiCursor(seconds, 0), updated=TaiCursor(seconds, 0),
                health=health_now(),
            )

        assert store._order_dirty[(ResourceType.DEVICE, "update")] is True
        assert self._ids(store, ResourceType.DEVICE, "update") == [
            DEVICE_ID, DEVICE_ID_2,
        ]
        # Re-sorted once, then clean: the cost is per-preload, not per-query.
        assert store._order_dirty[(ResourceType.DEVICE, "update")] is False
        self._assert_matches_sort(store)

    def test_colliding_cursors_break_the_tie_on_id(self) -> None:
        """Two resources on one cursor must order identically on every member.

        The cluster-determinism guarantee: without the id tie-break the order
        is whatever the local dict happened to be.
        """
        store = RegistryStore()
        register(store, ResourceType.NODE, make_node())

        for device_id in (DEVICE_ID_2, DEVICE_ID):
            raw = make_device(device_id)
            prepared = store.prepare(ResourceType.DEVICE, raw)
            assert not isinstance(prepared, RegistrationResult)
            store.apply_committed(
                prepared, dict(raw),
                created=TaiCursor(7000, 0), updated=TaiCursor(7000, 0),
                health=health_now(),
            )

        assert self._ids(store, ResourceType.DEVICE, "update") == sorted(
            [DEVICE_ID, DEVICE_ID_2],
        )

    def test_the_tail_moving_backwards_marks_the_index_dirty(self) -> None:
        """The tail is not exempt from the ordering check.

        A revive re-applies the *same* id, so it is frequently already at the
        end of the index. If its new cursor is lower than the one it replaces
        -- which only etcd-supplied cursors can produce -- it has to move
        earlier. Comparing the tail against itself would compare the new cursor
        with the new cursor, always look ordered, and leave it at the end.
        """
        store = RegistryStore()
        register(store, ResourceType.NODE, make_node())

        def apply(device_id: str, seconds: int) -> None:
            raw = make_device(device_id, version=tai_version())
            prepared = store.prepare(ResourceType.DEVICE, raw)
            assert not isinstance(prepared, RegistrationResult)
            store.apply_committed(
                prepared, dict(raw),
                created=TaiCursor(seconds, 0), updated=TaiCursor(seconds, 0),
                health=health_now(),
            )

        apply(DEVICE_ID_2, 1000)
        apply(DEVICE_ID, 5000)          # DEVICE_ID is now the tail
        assert self._ids(store, ResourceType.DEVICE, "update") == [
            DEVICE_ID_2, DEVICE_ID,
        ]

        apply(DEVICE_ID, 500)           # the tail moves BELOW the other record

        assert self._ids(store, ResourceType.DEVICE, "update") == [
            DEVICE_ID, DEVICE_ID_2,
        ]
        self._assert_matches_sort(store)
