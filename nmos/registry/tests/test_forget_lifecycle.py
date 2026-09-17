# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Stage two of the resource lifecycle, and the leak that used to skip it.

``delete`` and garbage collection do not drop a resource; they mark it
non-extant (``store.py``, "Deletion is two-stage"). Stage two — ``_forget`` —
is what actually frees the id and removes the record. Until it runs, the
resource is invisible to every client but still occupies ``_by_type`` and,
crucially, still owns its id in ``_type_of``.

**The bug these pin.** ``gc.py`` calls the *backend's* ``collect_garbage``, and
the distributed backend answered 0 without touching the store. Stage two lived
only inside ``RegistryStore.collect_garbage``, so in distributed mode ``_forget``
was never reached. Three consequences, none of which surfaced as an error:

* ``_by_type`` grew without bound — a registry that churns senders leaks a
  record per delete, forever;
* the status line's non-extant count never fell;
* an id, once deleted, could never be registered again under a *different*
  type, because ``prepare`` still saw the stale ``_type_of`` mapping and
  answered ``ID_TYPE_CONFLICT`` — permanently.

Expiry and forgetting are separately suppressible for exactly this reason: the
distributed backends must disable the first (liveness is a lease) and must not
disable the second (it is purely local bookkeeping over records that are
already retired).
"""

from __future__ import annotations

import pytest

from nmos.registry.store import RegistryStore, health_now
from nmos.registry.tests._fixtures import (
    DEVICE_ID,
    NODE_ID,
    SENDER_ID,
    make_device,
    make_node,
    make_sender,
)
from nmos.registry.types import RegistrationError, ResourceType


def _store(*, forget_interval: float = 60.0) -> RegistryStore:
    return RegistryStore(gc_interval=12.0, forget_interval=forget_interval)


def _register(store: RegistryStore, resource_type: ResourceType, raw: dict) -> None:
    prepared = store.prepare(resource_type, raw)
    assert not hasattr(prepared, "error"), prepared
    store.apply_committed(prepared, _body(raw))


def _body(raw: dict) -> object:
    from nmos.registry.types import Body

    import json

    return Body(text=json.dumps(raw), data=raw)


def _seed_node(store: RegistryStore) -> None:
    _register(store, ResourceType.NODE, make_node())
    _register(store, ResourceType.DEVICE, make_device())
    _register(store, ResourceType.SENDER, make_sender())


def _age_tombstones(store: RegistryStore, seconds: int) -> None:
    """Rewind every tombstone's health, so its forget interval has elapsed.

    The alternative is sleeping. ``forgettable`` compares ``health <
    forget_before``, and a record retired in the current second is not strictly
    older than the threshold even at ``forget_interval=0`` -- health has
    one-second resolution by design (``health_now``). Reaching into the records
    is how a lifecycle measured in seconds gets tested in milliseconds.
    """
    for bucket in store._by_type.values():  # noqa: SLF001
        for resource in bucket.values():
            if not resource.extant:
                resource.health -= seconds


class TestForgettable:
    """The pure query half: which tombstones are past saving."""

    def test_an_extant_resource_is_never_forgettable(self) -> None:
        store = _store()
        _seed_node(store)
        assert store.forgettable() == []

    def test_a_fresh_tombstone_is_not_yet_forgettable(self) -> None:
        store = _store(forget_interval=60.0)
        _seed_node(store)
        store.delete(ResourceType.SENDER, SENDER_ID)
        assert store.forgettable() == []

    def test_an_elapsed_tombstone_is_forgettable(self) -> None:
        store = _store(forget_interval=60.0)
        _seed_node(store)
        store.delete(ResourceType.SENDER, SENDER_ID)
        # Measure from far enough in the future that the interval has passed.
        victims = store.forgettable(health_now() + 120)
        assert (ResourceType.SENDER, SENDER_ID) in victims

    def test_it_mutates_nothing(self) -> None:
        """It answers a question; ``forget`` acts on the answer."""
        store = _store(forget_interval=0.0)
        _seed_node(store)
        store.delete(ResourceType.SENDER, SENDER_ID)
        before = store.statistics().non_extant
        store.forgettable(health_now() + 120)
        store.forgettable(health_now() + 120)
        assert store.statistics().non_extant == before

    def test_the_order_is_stable_across_insertion_histories(self) -> None:
        """Two members must produce byte-identical victim lists.

        Bucket iteration follows insertion order, which differs between a
        member that has been up for a week and one that preloaded a minute
        ago. A distributed backend that replicates "forget these" needs the
        list to be a function of the contents, not of the history.
        """
        forward = _store(forget_interval=0.0)
        _register(forward, ResourceType.NODE, make_node())
        _register(forward, ResourceType.DEVICE, make_device())
        _register(forward, ResourceType.SENDER, make_sender())
        forward.delete(ResourceType.NODE, NODE_ID)

        backward = _store(forget_interval=0.0)
        _register(backward, ResourceType.NODE, make_node())
        _register(backward, ResourceType.DEVICE, make_device())
        _register(backward, ResourceType.SENDER, make_sender())
        backward.delete(ResourceType.NODE, NODE_ID)

        moment = health_now() + 120
        assert forward.forgettable(moment) == backward.forgettable(moment)
        assert forward.forgettable(moment) == sorted(
            forward.forgettable(moment),
            key=lambda victim: (victim[0].value, victim[1]),
        )


class TestForget:
    """The mutation half: addressable, clockless, and refuses live records."""

    def test_forgetting_frees_the_id_for_a_different_type(self) -> None:
        """The user-visible consequence of the leak.

        While the tombstone holds the id in ``_type_of``, re-registering it as
        another type is refused — and no amount of waiting helps, because the
        record that owns the id is never dropped.
        """
        store = _store(forget_interval=0.0)
        _seed_node(store)
        store.delete(ResourceType.SENDER, SENDER_ID)

        # Still claimed by the tombstone.
        clash = make_device(SENDER_ID)
        refused = store.prepare(ResourceType.DEVICE, clash)
        assert getattr(refused, "error", None) is RegistrationError.ID_TYPE_CONFLICT

        assert store.forget(ResourceType.SENDER, SENDER_ID) is True

        # Now the id is genuinely free.
        allowed = store.prepare(ResourceType.DEVICE, clash)
        assert getattr(allowed, "error", None) is None

    def test_forgetting_an_extant_resource_is_refused(self) -> None:
        """Stage two must not do stage one's job.

        Dropping a live resource here would erase it with no removal grain,
        so every subscriber would simply stop seeing it with no event to
        explain why.
        """
        store = _store()
        _seed_node(store)
        assert store.forget(ResourceType.SENDER, SENDER_ID) is False
        assert store.get(ResourceType.SENDER, SENDER_ID) is not None

    def test_forgetting_an_absent_resource_is_harmless(self) -> None:
        store = _store()
        assert store.forget(ResourceType.SENDER, SENDER_ID) is False

    def test_forgetting_is_idempotent(self) -> None:
        store = _store(forget_interval=0.0)
        _seed_node(store)
        store.delete(ResourceType.SENDER, SENDER_ID)
        assert store.forget(ResourceType.SENDER, SENDER_ID) is True
        assert store.forget(ResourceType.SENDER, SENDER_ID) is False

    def test_forgetting_reads_no_clock(self) -> None:
        """Applying a victim list must give the same store on every member.

        ``forgettable`` owns the clock; ``forget`` must not, or two members
        applying the same replicated decision at different moments would
        diverge.
        """
        store = _store(forget_interval=10_000.0)
        _seed_node(store)
        store.delete(ResourceType.SENDER, SENDER_ID)
        # Nowhere near the forget interval -- and it goes anyway, because the
        # decision was the caller's.
        assert store.forget(ResourceType.SENDER, SENDER_ID) is True


class TestStandaloneCollectionStillDoesBothStages:
    """The refactor must not have moved behaviour out of standalone."""

    def test_collect_garbage_still_forgets_elapsed_tombstones(self) -> None:
        store = _store(forget_interval=60.0)
        _seed_node(store)
        store.delete(ResourceType.SENDER, SENDER_ID)
        assert store.statistics().non_extant == 1

        _age_tombstones(store, 120)
        store.collect_garbage()
        assert store.statistics().non_extant == 0

    def test_the_returned_count_is_expiry_only(self) -> None:
        """Forgetting emits no grains, so it is not a 'collection'."""
        store = _store(forget_interval=0.0)
        _seed_node(store)
        store.delete(ResourceType.SENDER, SENDER_ID)
        events = store.collect_garbage()
        assert events == []


@pytest.mark.asyncio
class TestBackendsAgreeOnTheLifecycle:
    """Equivalence: every backend must forget, and none may count it."""

    async def test_standalone_forgets_and_reports_expiry_only(self) -> None:
        from nmos.registry.backend import StandaloneRegistryBackend
        from nmos.registry.registry import Registry

        registry = Registry(_store(forget_interval=60.0), query_id="q")
        _seed_node(registry.store)
        registry.store.delete(ResourceType.SENDER, SENDER_ID)
        _age_tombstones(registry.store, 120)

        backend = StandaloneRegistryBackend(registry)
        collected = await backend.collect_garbage()

        assert registry.store.statistics().non_extant == 0
        assert collected == 0

    async def test_the_distributed_backend_forgets_without_expiring(
        self,
    ) -> None:
        """The bug itself: this used to ``return 0`` and touch nothing.

        No etcd is involved. Stage two is purely local bookkeeping over records
        that are already non-extant, which is exactly why suppressing it along
        with expiry was wrong — and why it can be asserted without a cluster.
        """
        from pathlib import Path

        from nmos.cluster.layout import MemberSpec, derive_cluster
        from nmos.registry.distributed import EtcdConfig
        from nmos.registry.etcd_backend import EtcdRegistryBackend
        from nmos.registry.registry import Registry

        registry = Registry(_store(forget_interval=60.0), query_id="q")
        _seed_node(registry.store)
        registry.store.delete(ResourceType.SENDER, SENDER_ID)
        _age_tombstones(registry.store, 120)

        # An extant Node remains, so a backend that ran expiry instead of
        # forgetting would visibly remove the wrong thing.
        assert registry.store.get(ResourceType.NODE, NODE_ID) is not None
        assert registry.store.statistics().non_extant == 1

        layout = derive_cluster(
            [MemberSpec(host="h0")], local_host="h0", namespace="/t",
        )
        backend = EtcdRegistryBackend(
            registry,
            EtcdConfig(
                layout=layout, endpoints=("h0:2381",), namespace="/t",
                external=True, binary="", data_dir=Path(), bootstrap=False,
                tls=False, certificate="", key="", trusted_root_ca=(),
                certificate_name="", client_crl_file="", peer_crl_file="",
                rpc_timeout=2.0, mutation_timeout=7.0,
            ),
        )

        collected = await backend.collect_garbage()

        # Forgotten...
        assert registry.store.statistics().non_extant == 0
        # ...expiry still suppressed: the silent Node is untouched...
        assert registry.store.get(ResourceType.NODE, NODE_ID) is not None
        # ...and the count still means "expired", as it does in standalone.
        assert collected == 0

    async def test_a_deleted_id_is_reusable_after_collection(self) -> None:
        """The end-to-end shape of the leak, through the backend seam."""
        from nmos.registry.backend import StandaloneRegistryBackend
        from nmos.registry.registry import Registry

        registry = Registry(_store(forget_interval=60.0), query_id="q")
        _seed_node(registry.store)
        registry.store.delete(ResourceType.DEVICE, DEVICE_ID)
        _age_tombstones(registry.store, 120)

        backend = StandaloneRegistryBackend(registry)
        await backend.collect_garbage()

        reused = registry.store.prepare(ResourceType.NODE, make_node(DEVICE_ID))
        assert getattr(reused, "error", None) is None
