# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Apply, and the determinism it has to guarantee.

The failure being guarded against is the worst kind this system has: two
members apply the same log, reach different states, and both keep serving.
Nothing raises. A Controller asking member 0 and member 1 the same question
gets two different answers, and neither is identifiably wrong.

So the central test here is a *replay*: build an operation log, apply it
through several independently-constructed state machines whose local
environments differ, and assert the resulting stores are byte-identical. If
apply ever reads a clock, a counter or a set's iteration order, this is what
catches it -- and the static guard below catches the same thing at the source,
because a replay can only fail after someone has already written the bug.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from nmos.raft.cursors import CursorAllocator
from nmos.raft.log import Entry
from nmos.raft.machine import DivergenceDetected, StateMachine
from nmos.raft.operations import (
    ClaimOwnershipOp,
    ExpireOp,
    ForgetOp,
    MemberDownOp,
    ProposalId,
    RegisterOp,
    RegistryOperation,
    ReleaseOwnershipOp,
    UnregisterOp,
)
from nmos.raft.ownership import OwnershipTable
from nmos.registry.registry import Registry
from nmos.registry.store import RegistryStore
from nmos.registry.subscriptions import SubscriptionManager
from nmos.registry.tests._fixtures import (
    DEVICE_ID,
    NODE_ID,
    SENDER_ID,
    make_device,
    make_node,
    make_sender,
)
from nmos.registry.types import ResourceType, TaiCursor


def _machine(member: int = 0) -> tuple[Registry, StateMachine]:
    registry = Registry(RegistryStore(), query_id=f"q{member}")
    registry.attach_subscriptions(SubscriptionManager(registry))
    machine = StateMachine(
        registry,
        ownership=OwnershipTable(),
        cursors=CursorAllocator(member),
        member=member,
    )
    return registry, machine


def _register(
    index: int, resource_type: ResourceType, raw: dict, *,
    node_id: str = NODE_ID, created: bool = True, claim: int | None = None,
) -> Entry[RegistryOperation]:
    return Entry(
        term=1, index=index, payload=b"",
        value=RegisterOp(
            proposal=ProposalId(member=0, sequence=index),
            resource_type=resource_type,
            resource_id=raw["id"],
            node_id=node_id,
            body_text=json.dumps(raw),
            created=TaiCursor(1000 + index, 8),
            updated=TaiCursor(1000 + index, 8),
            health=5000,
            expect_created=created,
            claim_owner=claim,
        ),
    )


def _entry(index: int, operation: RegistryOperation) -> Entry[RegistryOperation]:
    return Entry(term=1, index=index, payload=b"", value=operation)


def _digest(registry: Registry) -> str:
    """A byte-level fingerprint of everything a client could observe."""
    hasher = hashlib.sha256()
    for resource_type in ResourceType:
        bucket = registry.store._by_type[resource_type]  # noqa: SLF001
        for resource_id in sorted(bucket):
            resource = bucket[resource_id]
            hasher.update(
                f"{resource_type.value}|{resource_id}|{resource.extant}|"
                f"{resource.health}|{resource.created}|{resource.updated}|"
                f"{resource.parent_id}|{resource.body.text}\n".encode(),
            )
    return hasher.hexdigest()


def _seed() -> list[Entry[RegistryOperation]]:
    return [
        _register(1, ResourceType.NODE, make_node(), claim=0),
        _register(2, ResourceType.DEVICE, make_device()),
        _register(3, ResourceType.SENDER, make_sender()),
    ]


class _RecordingRegistry:
    """Wraps a Registry and records what apply publishes.

    A wrapper rather than a monkeypatch because ``Registry`` uses ``__slots__``
    -- and the state machine only ever touches ``store`` and ``publish``, so
    standing in for it costs two lines and asserts that narrowness at the same
    time.
    """

    def __init__(self, inner: Registry) -> None:
        self.inner = inner
        self.published: list[tuple[str, str]] = []

    @property
    def store(self) -> RegistryStore:
        return self.inner.store

    def publish(self, events: list) -> None:
        self.published.extend(
            (event.resource_type.value, event.resource_id) for event in events
        )
        self.inner.publish(events)


def _recording(member: int = 0) -> tuple[_RecordingRegistry, StateMachine]:
    registry, _ = _machine(member)
    recorder = _RecordingRegistry(registry)
    machine = StateMachine(
        recorder,  # type: ignore[arg-type]
        ownership=OwnershipTable(),
        cursors=CursorAllocator(member),
        member=member,
    )
    return recorder, machine


class TestApplyingRegistrations:
    def test_a_registration_lands_in_the_store(self) -> None:
        registry, machine = _machine()
        machine.apply(_seed())

        assert registry.store.get(ResourceType.NODE, NODE_ID) is not None
        assert registry.store.get(ResourceType.SENDER, SENDER_ID) is not None
        assert machine.last_applied == 3

    def test_the_carried_cursors_and_health_are_what_is_stored(self) -> None:
        """Rules 1 and 2: apply must never fill these in locally."""
        registry, machine = _machine()
        machine.apply(_seed())

        sender = registry.store.get(ResourceType.SENDER, SENDER_ID)
        assert sender is not None
        assert sender.created == TaiCursor(1003, 8)
        assert sender.updated == TaiCursor(1003, 8)
        assert sender.health == 5000

    def test_the_body_is_stored_verbatim(self) -> None:
        registry, machine = _machine()
        raw = make_sender()
        text = json.dumps(raw, indent=3)
        machine.apply([
            _register(1, ResourceType.NODE, make_node(), claim=0),
            _register(2, ResourceType.DEVICE, make_device()),
            Entry(
                term=1, index=3, payload=b"",
                value=RegisterOp(
                    proposal=ProposalId(0, 3),
                    resource_type=ResourceType.SENDER,
                    resource_id=raw["id"], node_id=NODE_ID, body_text=text,
                    created=TaiCursor(1, 0), updated=TaiCursor(1, 0),
                    health=1, expect_created=True,
                ),
            ),
        ])
        stored = registry.store.get(ResourceType.SENDER, SENDER_ID)
        assert stored is not None
        assert stored.body.text == text

    def test_an_authoritative_rejection_is_returned_not_raised(self) -> None:
        """A Sender whose Device is absent is simply refused."""
        registry, machine = _machine()
        outcomes = machine.apply([
            _register(1, ResourceType.SENDER, make_sender()),
        ])
        outcome = outcomes[ProposalId(member=0, sequence=1)]
        assert not outcome.result.ok

    def test_entries_already_applied_are_skipped(self) -> None:
        """Ordinary after a snapshot install, where the log overlaps it."""
        registry, machine = _machine()
        machine.apply(_seed())
        machine.apply(_seed())
        assert machine.last_applied == 3


class TestTheDivergenceTripwire:
    def test_disagreeing_about_created_raises(self) -> None:
        """The proposer and this member disagree about what is registered.

        Reported rather than reconciled: a member that quietly serves its own
        version of the truth is the failure the whole design exists to stop.
        """
        registry, machine = _machine()
        machine.apply(_seed())

        with pytest.raises(DivergenceDetected, match="disagree"):
            machine.apply([
                _register(
                    4, ResourceType.SENDER, make_sender(), created=True,
                ),
            ])

    def test_an_update_declared_as_an_update_is_fine(self) -> None:
        registry, machine = _machine()
        machine.apply(_seed())
        machine.apply([
            _register(4, ResourceType.SENDER, make_sender(), created=False),
        ])
        assert machine.last_applied == 4


class TestDeletionAndExpiry:
    def test_unregister_cascades(self) -> None:
        registry, machine = _machine()
        machine.apply(_seed())
        machine.apply([
            _entry(4, UnregisterOp(
                proposal=ProposalId(0, 4),
                resource_type=ResourceType.DEVICE, resource_id=DEVICE_ID,
            )),
        ])
        assert registry.store.get(ResourceType.DEVICE, DEVICE_ID) is None
        assert registry.store.get(ResourceType.SENDER, SENDER_ID) is None
        assert registry.store.get(ResourceType.NODE, NODE_ID) is not None

    def test_unregistering_something_absent_reports_false(self) -> None:
        registry, machine = _machine()
        outcomes = machine.apply([
            _entry(1, UnregisterOp(
                proposal=ProposalId(0, 1),
                resource_type=ResourceType.NODE, resource_id=NODE_ID,
            )),
        ])
        assert outcomes[ProposalId(0, 1)].result is False

    def test_expiry_removes_the_whole_subtree(self) -> None:
        registry, machine = _machine()
        machine.apply(_seed())
        outcomes = machine.apply([
            _entry(4, ExpireOp(proposal=ProposalId(0, 4), node_id=NODE_ID)),
        ])
        assert outcomes[ProposalId(0, 4)].result == 3
        assert registry.store.get(ResourceType.NODE, NODE_ID) is None

    def test_forget_drops_tombstones_and_emits_nothing(self) -> None:
        """A tombstone was already invisible; dropping it changes nothing."""
        registry, machine = _machine()
        machine.apply(_seed())
        machine.apply([
            _entry(4, UnregisterOp(
                proposal=ProposalId(0, 4),
                resource_type=ResourceType.SENDER, resource_id=SENDER_ID,
            )),
        ])
        outcomes = machine.apply([
            _entry(5, ForgetOp(
                proposal=ProposalId(0, 5),
                victims=((ResourceType.SENDER, SENDER_ID),),
            )),
        ])
        assert outcomes[ProposalId(0, 5)].result == 1
        assert registry.store.statistics().non_extant == 0


class TestOwnership:
    def test_a_fused_claim_takes_ownership_in_one_entry(self) -> None:
        """A Node's first registration must not cost a second round trip."""
        registry, machine = _machine()
        machine.apply([_register(1, ResourceType.NODE, make_node(), claim=2)])
        assert machine.ownership.is_owned_by(NODE_ID, 2)

    def test_a_standalone_claim_and_release(self) -> None:
        registry, machine = _machine()
        machine.apply([
            _entry(1, ClaimOwnershipOp(
                proposal=ProposalId(0, 1), node_id=NODE_ID, owner=1,
            )),
        ])
        assert machine.ownership.is_owned_by(NODE_ID, 1)

        machine.apply([
            _entry(2, ReleaseOwnershipOp(
                proposal=ProposalId(0, 2), node_id=NODE_ID,
            )),
        ])
        assert machine.ownership.owner_of(NODE_ID) is None

    def test_member_down_releases_everything_that_member_held(self) -> None:
        registry, machine = _machine()
        machine.apply([
            _entry(1, ClaimOwnershipOp(
                proposal=ProposalId(0, 1), node_id="a", owner=1,
            )),
            _entry(2, ClaimOwnershipOp(
                proposal=ProposalId(0, 2), node_id="b", owner=1,
            )),
            _entry(3, ClaimOwnershipOp(
                proposal=ProposalId(0, 3), node_id="c", owner=0,
            )),
        ])
        outcomes = machine.apply([
            _entry(4, MemberDownOp(proposal=ProposalId(0, 4), member=1)),
        ])
        assert outcomes[ProposalId(0, 4)].result == 2
        assert machine.ownership.is_owned_by("c", 0)

    def test_the_epoch_is_the_log_index(self) -> None:
        """Monotonic by construction, so "who claimed last" needs no clock."""
        registry, machine = _machine()
        machine.apply([
            _entry(17, ClaimOwnershipOp(
                proposal=ProposalId(0, 1), node_id=NODE_ID, owner=1,
            )),
        ])
        held = machine.ownership.owner_of(NODE_ID)
        assert held is not None
        assert held.epoch == 17


class TestDeterminism:
    """Risk 1: two members applying one log must reach one state."""

    def test_the_same_log_produces_the_same_store_on_every_member(
        self,
    ) -> None:
        entries = _seed() + [
            _register(4, ResourceType.SENDER, make_sender(), created=False),
            _entry(5, UnregisterOp(
                proposal=ProposalId(0, 5),
                resource_type=ResourceType.SENDER, resource_id=SENDER_ID,
            )),
        ]

        digests = set()
        for member in range(5):
            registry, machine = _machine(member)
            machine.apply(entries)
            digests.add(_digest(registry))

        assert len(digests) == 1, "members diverged applying an identical log"

    def test_applying_in_chunks_is_the_same_as_applying_at_once(self) -> None:
        """The caller bounds each run; the result must not depend on where."""
        entries = _seed() + [
            _register(4, ResourceType.SENDER, make_sender(), created=False),
        ]

        whole_registry, whole = _machine()
        whole.apply(entries)

        chunked_registry, chunked = _machine()
        for entry in entries:
            chunked.apply([entry])

        assert _digest(whole_registry) == _digest(chunked_registry)

    def test_a_cascade_publishes_events_in_a_total_order(self) -> None:
        """``_erase_subtree`` walks a set, so the order must be imposed.

        Two members would otherwise describe the same deletion in a different
        order to their subscribers -- a divergence even though both end in the
        same state.
        """
        orders = set()
        for member in range(5):
            recorder, machine = _recording(member)
            machine.apply(_seed())
            recorder.published.clear()
            machine.apply([
                _entry(4, ExpireOp(
                    proposal=ProposalId(0, 4), node_id=NODE_ID,
                )),
            ])
            orders.add(tuple(recorder.published))

        assert len(orders) == 1, "removal order differed between members"

    def test_children_are_removed_before_their_parents(self) -> None:
        """A subscriber must never see a parent vanish while a child remains."""
        recorder, machine = _recording()
        machine.apply(_seed())
        recorder.published.clear()
        machine.apply([
            _entry(4, ExpireOp(proposal=ProposalId(0, 4), node_id=NODE_ID)),
        ])

        kinds = [kind for kind, _id in recorder.published]
        assert kinds.index("sender") < kinds.index("device")
        assert kinds.index("device") < kinds.index("node")


class TestStaticDeterminismGuard:
    """Catches the bug at the source, before a replay could ever fail.

    A replay test can only fail once someone has written the bug and a test
    happens to exercise the path. Grepping is cruder and far earlier: the
    regression that will actually happen here is someone reaching for
    ``health_now()`` inside apply because it was convenient.
    """

    def test_apply_reads_no_clock_and_no_randomness(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "machine.py"
        ).read_text(encoding="utf-8")
        # Strip docstrings and comments: they discuss these names precisely
        # because the code must not use them.
        code = re.sub(r'""".*?"""', "", source, flags=re.S)
        code = re.sub(r"#.*", "", code)

        for forbidden in (
            "health_now(", "time.time(", "time.monotonic(",
            "TaiCursor.now(", "random.", "datetime.",
        ):
            assert forbidden not in code, (
                f"{forbidden} appears in machine.py: apply must not read "
                f"local state, or two members will diverge"
            )
