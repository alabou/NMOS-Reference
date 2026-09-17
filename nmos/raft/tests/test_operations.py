# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Operation encoding, and the determinism rules it exists to enforce.

An operation is what every member applies to reach the same state, so the tests
that matter here are not really about bytes. They are about whether everything
a member would otherwise read from its own environment -- a clock, a local
counter -- travels in the entry instead.

The bug this prevents has no error message. Two members apply the same log,
one stamps ``health`` from its own clock, and from then on they serve different
answers to the same query with nothing to indicate which is right.
"""

from __future__ import annotations

import pytest

from nmos.raft.errors import RaftProtocolError
from nmos.raft.operations import (
    ClaimOwnershipOp,
    ExpireOp,
    ForgetOp,
    MemberDownOp,
    NoopOp,
    OpKind,
    ProposalId,
    RegisterOp,
    ReleaseOwnershipOp,
    UnregisterOp,
    decode_operation,
    encode_operation,
)
from nmos.raft.wire import Writer
from nmos.registry.types import ResourceType, TaiCursor

_SAMPLES = [
    NoopOp(proposal=ProposalId(member=1, sequence=2)),
    RegisterOp(
        proposal=ProposalId(member=0, sequence=7),
        resource_type=ResourceType.SENDER,
        resource_id="8c4f1e2a-0000-4000-8000-000000000000",
        node_id="1f2e3d4c-0000-4000-8000-000000000000",
        body_text='{"id":"8c4f1e2a","label":"a sender"}',
        created=TaiCursor(1000, 8), updated=TaiCursor(1001, 16),
        health=1234, expect_created=True, claim_owner=2,
    ),
    UnregisterOp(
        proposal=ProposalId(member=2, sequence=1),
        resource_type=ResourceType.DEVICE, resource_id="dev-1",
    ),
    ExpireOp(proposal=ProposalId(member=1, sequence=9), node_id="node-1"),
    ForgetOp(
        proposal=ProposalId(member=1, sequence=10),
        victims=(
            (ResourceType.SENDER, "s1"),
            (ResourceType.FLOW, "f1"),
            (ResourceType.RECEIVER, "r1"),
        ),
    ),
    ClaimOwnershipOp(
        proposal=ProposalId(member=3, sequence=1), node_id="node-1", owner=3,
    ),
    ReleaseOwnershipOp(
        proposal=ProposalId(member=3, sequence=2), node_id="node-1",
    ),
    MemberDownOp(proposal=ProposalId(member=0, sequence=3), member=2),
]


@pytest.mark.parametrize("op", _SAMPLES, ids=lambda o: type(o).__name__)
def test_every_operation_round_trips(op: object) -> None:
    assert decode_operation(encode_operation(op)) == op  # type: ignore[arg-type]


class TestDeterminismCarriedOnTheWire:
    """The fields that exist so apply never reads local state."""

    def test_a_registration_carries_both_cursors_and_health(self) -> None:
        """``apply_committed`` would otherwise default all three locally.

        Its defaults call ``health_now()`` and ``_next_cursor()``, which read
        this member's clock and this member's counter. Two members applying the
        same entry would then store different values and serve different
        answers, with nothing to indicate which was right.
        """
        op = _SAMPLES[1]
        assert isinstance(op, RegisterOp)
        decoded = decode_operation(encode_operation(op))
        assert isinstance(decoded, RegisterOp)
        assert decoded.created == TaiCursor(1000, 8)
        assert decoded.updated == TaiCursor(1001, 16)
        assert decoded.health == 1234

    def test_a_forget_carries_its_victims_rather_than_a_threshold(self) -> None:
        """The decision reads a clock; the *result* is what is replicated.

        Sending "forget anything older than T" would have every member
        re-evaluate T against its own clock, which is the same divergence in a
        different costume.
        """
        op = _SAMPLES[4]
        assert isinstance(op, ForgetOp)
        decoded = decode_operation(encode_operation(op))
        assert isinstance(decoded, ForgetOp)
        assert decoded.victims == op.victims

    def test_an_expiry_names_the_node_rather_than_a_deadline(self) -> None:
        op = _SAMPLES[3]
        assert isinstance(op, ExpireOp)
        assert decode_operation(encode_operation(op)) == op


class TestAbsentVersusZero:
    def test_a_registration_that_claims_nothing(self) -> None:
        """Member 0 is a real member; a sentinel zero would claim for it."""
        op = RegisterOp(
            proposal=ProposalId(member=1, sequence=1),
            resource_type=ResourceType.NODE, resource_id="n", node_id="n",
            body_text="{}", created=TaiCursor(1, 1), updated=TaiCursor(1, 1),
            health=1, expect_created=False, claim_owner=None,
        )
        decoded = decode_operation(encode_operation(op))
        assert isinstance(decoded, RegisterOp)
        assert decoded.claim_owner is None

    def test_a_registration_claiming_for_member_zero(self) -> None:
        op = RegisterOp(
            proposal=ProposalId(member=0, sequence=1),
            resource_type=ResourceType.NODE, resource_id="n", node_id="n",
            body_text="{}", created=TaiCursor(1, 1), updated=TaiCursor(1, 1),
            health=1, expect_created=True, claim_owner=0,
        )
        decoded = decode_operation(encode_operation(op))
        assert isinstance(decoded, RegisterOp)
        assert decoded.claim_owner == 0

    def test_a_proposal_from_member_zero_sequence_zero(self) -> None:
        """The very first proposal a fresh member makes."""
        op = NoopOp(proposal=ProposalId(member=0, sequence=0))
        assert decode_operation(encode_operation(op)) == op


class TestFidelity:
    def test_the_body_travels_verbatim(self) -> None:
        """Byte-for-byte, including what the type layer does not model.

        A re-encode here -- even one that looked lossless -- would mean the
        member that applies the entry serves something the client never sent,
        at the one point nobody would think to check.
        """
        body = (
            '{"id":"x",  "urn:x-vendor:odd":[1,2,3],\n'
            '  "label":"ünicode \\u00e9",  "tags":{}}'
        )
        op = RegisterOp(
            proposal=ProposalId(member=0, sequence=1),
            resource_type=ResourceType.SENDER, resource_id="x", node_id="n",
            body_text=body, created=TaiCursor(1, 0), updated=TaiCursor(1, 0),
            health=1, expect_created=True,
        )
        decoded = decode_operation(encode_operation(op))
        assert isinstance(decoded, RegisterOp)
        assert decoded.body_text == body

    def test_an_empty_forget_round_trips(self) -> None:
        op = ForgetOp(proposal=ProposalId(member=0, sequence=1), victims=())
        assert decode_operation(encode_operation(op)) == op


class TestRejections:
    def test_an_unknown_kind_is_refused(self) -> None:
        payload = Writer().uint(1, 250).bytes_(2, b"").take()
        with pytest.raises(RaftProtocolError, match="unknown operation kind"):
            decode_operation(payload)

    def test_a_missing_kind_is_refused(self) -> None:
        with pytest.raises(RaftProtocolError, match="no kind"):
            decode_operation(Writer().bytes_(2, b"").take())

    def test_an_unknown_resource_type_is_refused(self) -> None:
        """A peer naming a type this build does not have is not guessable."""
        body = (
            Writer()
            .bytes_(1, Writer().uint(1, 0).uint(2, 1).take())
            .string(2, "hologram")
            .string(3, "id")
            .take()
        )
        payload = Writer().uint(1, int(OpKind.UNREGISTER)).bytes_(2, body).take()
        with pytest.raises(RaftProtocolError, match="unknown resource type"):
            decode_operation(payload)

    def test_an_ownership_claim_with_no_owner_is_refused(self) -> None:
        """Defaulting it to zero would silently hand the Node to member 0."""
        body = (
            Writer()
            .bytes_(1, Writer().uint(1, 0).uint(2, 1).take())
            .string(2, "node-1")
            .take()
        )
        payload = (
            Writer().uint(1, int(OpKind.CLAIM_OWNERSHIP)).bytes_(2, body).take()
        )
        with pytest.raises(RaftProtocolError, match="names no owner"):
            decode_operation(payload)


class TestForwardCompatibility:
    def test_unknown_fields_inside_an_operation_are_skipped(self) -> None:
        """A newer member's extra field must not break an older one."""
        op = _SAMPLES[3]
        assert isinstance(op, ExpireOp)
        body = (
            Writer()
            .bytes_(
                1,
                Writer().uint(1, op.proposal.member)
                .uint(2, op.proposal.sequence).take(),
            )
            .string(2, op.node_id)
            .uint(50, 999)
            .string(51, "from the future")
            .take()
        )
        payload = Writer().uint(1, int(OpKind.EXPIRE)).bytes_(2, body).take()
        assert decode_operation(payload) == op


class TestEncodingSize:
    def test_a_registration_is_small_beside_its_body(self) -> None:
        """Every entry is replicated to every member; overhead is not free.

        Not a performance test -- it is a tripwire. A change that started
        carrying something large per entry (a serialised PreparedRegistration,
        say) would show up here rather than as an unexplained drop in
        throughput weeks later.
        """
        op = RegisterOp(
            proposal=ProposalId(member=0, sequence=1),
            resource_type=ResourceType.SENDER, resource_id="s", node_id="n",
            body_text="", created=TaiCursor(1, 0), updated=TaiCursor(1, 0),
            health=1, expect_created=True,
        )
        assert len(encode_operation(op)) < 64

    def test_kinds_are_stable(self) -> None:
        """Wire numbers are permanent; reusing one silently reinterprets a log."""
        assert [int(kind) for kind in OpKind] == [0, 1, 2, 3, 4, 5, 6, 7]
