# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The ownership table: exactly one owner per Node, always.

The invariant matters because ownership is what lets a member answer 400
without a round trip. Two members each believing they own a Node would each
validate against their own view and each propose, and the per-Node
serialisation the whole design rests on would be gone -- with no error anywhere
to say so.
"""

from __future__ import annotations

from nmos.raft.ownership import Ownership, OwnershipTable


class TestClaiming:
    def test_an_unowned_node_has_no_owner(self) -> None:
        """Unowned is a normal state, not an error.

        It is what a Node looks like between its owner dying and whichever
        member it re-registers with taking over.
        """
        table = OwnershipTable()
        assert table.owner_of("node-1") is None
        assert "node-1" not in table

    def test_claiming_records_the_owner_and_the_epoch(self) -> None:
        table = OwnershipTable()
        assert table.claim("node-1", owner=2, epoch=10) is True
        assert table.owner_of("node-1") == Ownership(owner=2, epoch=10)

    def test_a_later_claim_takes_over(self) -> None:
        table = OwnershipTable()
        table.claim("node-1", owner=2, epoch=10)
        assert table.claim("node-1", owner=0, epoch=11) is True
        assert table.owner_of("node-1") == Ownership(owner=0, epoch=11)

    def test_a_stale_claim_is_ignored(self) -> None:
        """Cannot happen in a correctly ordered apply; that is the point.

        Epochs are log indices, so a claim always arrives with a higher epoch
        than the one it replaces. Accepting a stale one would leave two members
        believing they owned the Node, and the divergence would surface far
        from its cause.
        """
        table = OwnershipTable()
        table.claim("node-1", owner=2, epoch=10)
        assert table.claim("node-1", owner=1, epoch=9) is False
        assert table.claim("node-1", owner=1, epoch=10) is False
        assert table.owner_of("node-1") == Ownership(owner=2, epoch=10)

    def test_ownership_is_per_node(self) -> None:
        table = OwnershipTable()
        table.claim("node-1", owner=0, epoch=1)
        table.claim("node-2", owner=1, epoch=2)
        assert table.is_owned_by("node-1", 0)
        assert table.is_owned_by("node-2", 1)
        assert not table.is_owned_by("node-1", 1)


class TestReleasing:
    def test_releasing_leaves_the_node_unowned(self) -> None:
        table = OwnershipTable()
        table.claim("node-1", owner=2, epoch=10)
        assert table.release("node-1", epoch=11) is True
        assert table.owner_of("node-1") is None

    def test_releasing_an_unowned_node_changes_nothing(self) -> None:
        assert OwnershipTable().release("node-1", epoch=1) is False

    def test_a_stale_release_is_ignored(self) -> None:
        table = OwnershipTable()
        table.claim("node-1", owner=2, epoch=10)
        assert table.release("node-1", epoch=10) is False
        assert table.owner_of("node-1") is not None

    def test_release_says_nothing_about_the_resources(self) -> None:
        """A member dying does not expire what it was answering for.

        Documented as a test because the opposite reading is tempting: losing
        the member responsible for a Node must not look like losing the Node.
        """
        table = OwnershipTable()
        table.claim("node-1", owner=2, epoch=10)
        table.release("node-1", epoch=11)
        # Nothing here touches resources at all -- the table holds ownership
        # and only ownership, which is what makes that separation checkable.
        assert len(table) == 0


class TestMemberDown:
    def test_every_node_the_member_owned_is_released(self) -> None:
        table = OwnershipTable()
        table.claim("a", owner=1, epoch=1)
        table.claim("b", owner=1, epoch=2)
        table.claim("c", owner=0, epoch=3)

        released = table.member_down(1, epoch=10)
        assert released == ("a", "b")
        assert table.owner_of("a") is None
        assert table.owner_of("b") is None
        assert table.owner_of("c") == Ownership(owner=0, epoch=3)

    def test_a_member_owning_nothing_releases_nothing(self) -> None:
        table = OwnershipTable()
        table.claim("a", owner=0, epoch=1)
        assert table.member_down(2, epoch=10) == ()

    def test_the_released_set_is_deterministic(self) -> None:
        """Every member applying the entry must compute the same answer.

        Dict iteration follows insertion order, which differs between a member
        that has been up for a week and one that just installed a snapshot --
        so the order has to come from the contents, not the history.
        """
        forward = OwnershipTable()
        for index, node_id in enumerate(["c", "a", "b"]):
            forward.claim(node_id, owner=1, epoch=index + 1)

        backward = OwnershipTable()
        for index, node_id in enumerate(["b", "a", "c"]):
            backward.claim(node_id, owner=1, epoch=index + 1)

        assert forward.member_down(1, epoch=99) == backward.member_down(
            1, epoch=99,
        )


class TestSnapshotTransfer:
    def test_a_table_round_trips(self) -> None:
        table = OwnershipTable()
        table.claim("node-1", owner=0, epoch=5)
        table.claim("node-2", owner=2, epoch=7)

        restored = OwnershipTable.decode(table.encode())
        assert restored.owner_of("node-1") == Ownership(owner=0, epoch=5)
        assert restored.owner_of("node-2") == Ownership(owner=2, epoch=7)
        assert len(restored) == 2

    def test_an_empty_table_round_trips(self) -> None:
        assert len(OwnershipTable.decode(OwnershipTable().encode())) == 0

    def test_the_encoding_is_order_independent(self) -> None:
        """Equal tables must serialise identically, whatever their history.

        Without it, two members with the same ownership would produce
        different snapshot bytes, and a snapshot could not be compared or
        checksummed at all.
        """
        forward = OwnershipTable()
        forward.claim("z", owner=1, epoch=1)
        forward.claim("a", owner=2, epoch=2)

        backward = OwnershipTable()
        backward.claim("a", owner=2, epoch=2)
        backward.claim("z", owner=1, epoch=1)

        assert forward.encode() == backward.encode()

    def test_ownership_travels_with_the_snapshot(self) -> None:
        """A follower that rebuilt ownership only from later entries would
        believe every Node was unowned, and would start claiming Nodes that
        already have owners."""
        leader = OwnershipTable()
        leader.claim("node-1", owner=0, epoch=100)

        follower = OwnershipTable.decode(leader.encode())
        assert follower.is_owned_by("node-1", 0)
        # And a claim from before the snapshot is still correctly rejected.
        assert follower.claim("node-1", owner=1, epoch=50) is False
