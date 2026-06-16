# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for nmos.controller.compat."""

from __future__ import annotations

from typing import Any

import pytest

from nmos.controller.cache import GroupedResource, NaturalGroupView
from nmos.controller.compat import (
    compatible_sender_groups,
    compatible_sender_groups_superset,
    compatible_senders,
    is_compatible,
    nmos_caps_json_to_ccf_caps,
    pair_by_identity,
    resource_ccf_caps,
)
from nmos.controller.grouping import GROUP_HINT_TAG, GroupHint


def _make_sender(sid: str, caps_json: dict[str, Any] | None = None) -> dict[str, Any]:
    """Construct a minimal sender dict. ``caps_json=None`` omits the
    ``caps`` field entirely — simulating a sender that hasn't declared
    its capabilities (BCP-004-01 pre-adoption).

    ``format`` and ``transport`` default to values that match
    ``_make_receiver`` so the format / transport checks
    in ``filter_sender_cs_by_receiver`` pass by default. Tests that
    want a mismatch override the fields directly on the returned dict.
    """
    res: dict[str, Any] = {
        "id": sid,
        "device_id": "d1",
        "label": f"sender {sid}",
        "format":    "urn:x-nmos:format:audio",
        "transport": "urn:x-nmos:transport:rtp",
        "tags": {},
    }
    if caps_json is not None:
        res["caps"] = caps_json
    return res


def _make_receiver(rid: str, caps_json: dict[str, Any] | None = None) -> dict[str, Any]:
    res: dict[str, Any] = {
        "id": rid,
        "device_id": "d2",
        "label": f"receiver {rid}",
        "format":    "urn:x-nmos:format:audio",
        "transport": "urn:x-nmos:transport:rtp",
        "tags": {},
    }
    if caps_json is not None:
        res["caps"] = caps_json
    return res


class TestJsonToCCFCaps:
    def test_empty_dict(self) -> None:
        # An empty dict is a valid caps declaration (no constraints).
        caps = nmos_caps_json_to_ccf_caps({})
        # Either returns an empty Caps object or None — both acceptable; the
        # important point is no exception.
        assert caps is None or caps is not None  # tautology guard

    def test_not_dict_returns_none(self) -> None:
        assert nmos_caps_json_to_ccf_caps(None) is None
        assert nmos_caps_json_to_ccf_caps("string") is None
        assert nmos_caps_json_to_ccf_caps([]) is None

    def test_resource_caps_extraction(self) -> None:
        sender = _make_sender("s1", caps_json={})
        # Should at minimum not raise.
        resource_ccf_caps(sender)


class TestIsCompatible:
    def test_unknown_caps_treated_as_compatible(self) -> None:
        # Caps missing on either side → fall back to "compatible" to
        # preserve the never-drop-a-candidate behaviour.
        assert is_compatible(None, None) is True

    def test_missing_caps_field_treated_as_unknown(self) -> None:
        # Senders / receivers that omit the caps field altogether fall
        # into the "unknown" bucket and pair compatibly.
        s_caps = resource_ccf_caps(_make_sender("s1"))
        r_caps = resource_ccf_caps(_make_receiver("r1"))
        assert is_compatible(s_caps, r_caps) is True


class TestCompatibleSenders:
    def test_returns_all_when_caps_unknown(self) -> None:
        receiver = _make_receiver("r1", None)
        senders = [_make_sender("s1"), _make_sender("s2"), _make_sender("s3")]
        result = compatible_senders(receiver, senders)
        assert len(result) == 3  # unknown caps → kept


class TestCompatibleSenderGroups:
    def _group(
        self, device_id: str, members: list[tuple[int, dict[str, Any]]],
    ) -> NaturalGroupView:
        grouped_members = [
            GroupedResource(
                id=res["id"],
                label=res["label"],
                description="",
                device_id=device_id,
                device_serial="",
                device_label="",
                hint=GroupHint(
                    group_name="RTP 0", role_name=f"AUDIO {role}",
                    groupable=True, format="AUDIO", role=role,
                ),
                resource=res,
            )
            for role, res in members
        ]
        return NaturalGroupView(
            device_id=device_id,
            device_serial="",
            device_label="",
            hint_key=("RTP", 0, "AUDIO"),
            members=grouped_members,
        )

    def test_role_shape_mismatch_rejected(self) -> None:
        recv_group = self._group(
            "d1",
            [(0, _make_receiver("r0")), (1, _make_receiver("r1"))],
        )
        sender_group = self._group(
            "d2",
            [(0, _make_sender("s0"))],  # only one role → mismatch
        )
        result = compatible_sender_groups(recv_group, [sender_group])
        assert result == []

    def test_matching_role_shape_accepted(self) -> None:
        recv_group = self._group(
            "d1",
            [(0, _make_receiver("r0")), (1, _make_receiver("r1"))],
        )
        sender_group = self._group(
            "d2",
            [(0, _make_sender("s0")), (1, _make_sender("s1"))],
        )
        result = compatible_sender_groups(recv_group, [sender_group])
        assert len(result) == 1
        assert result[0] is sender_group

    def test_empty_receiver_group_yields_empty(self) -> None:
        recv_group = NaturalGroupView(
            device_id="d1", device_serial="", device_label="",
            hint_key=("RTP", 0, "AUDIO"), members=[],
        )
        sender_group = self._group("d2", [(0, _make_sender("s0"))])
        assert compatible_sender_groups(recv_group, [sender_group]) == []

    def test_transport_mismatch_rejected(self) -> None:
        """Receiver rtp.ucast + sender rtp.mcast fail the
        ``isTransportCompatible`` whitelist — drop the pair even if
        role shape matches.
        """
        rx = _make_receiver("r0")
        rx["transport"] = "urn:x-nmos:transport:rtp.ucast"
        tx = _make_sender("s0")
        tx["transport"] = "urn:x-nmos:transport:rtp.mcast"
        recv_group = self._group("d1", [(0, rx)])
        sender_group = self._group("d2", [(0, tx)])
        assert compatible_sender_groups(recv_group, [sender_group]) == []

    def test_mux_group_distinct_format_same_role_not_collapsed(self) -> None:
        """Regression: a MUX group with ``VIDEO 0`` + ``AUDIO 0`` at the
        same role index (0) must NOT collapse to a single leaf in the
        role-map. Before the fix, the dict ``{m.hint.role: m}``
        overwrote role 0 with whichever member iterated last, silently
        dropping the other leaf.

        This is config1's shape: three receivers (VIDEO 0, AUDIO 0,
        AUDIO 1), three senders with the same leaf-tuple shape.
        """
        def _node(dev: str, kind: str, members: list[tuple[str, int]]) -> NaturalGroupView:
            make = _make_sender if kind == "sender" else _make_receiver
            grouped = []
            for fmt, role in members:
                r = make(f"{kind}-{fmt}-{role}")
                grouped.append(GroupedResource(
                    id=r["id"], label=r["label"], description="",
                    device_id=dev, device_serial="", device_label="",
                    hint=GroupHint(
                        group_name="RTP 0", role_name=f"{fmt} {role}",
                        groupable=True, format=fmt, role=role,
                    ),
                    resource=r,
                ))
            return NaturalGroupView(
                device_id=dev, device_serial="", device_label="",
                hint_key=("RTP", 0), members=grouped,
            )

        leaves = [("VIDEO", 0), ("AUDIO", 0), ("AUDIO", 1)]
        recv_group = _node("d1", "receiver", leaves)
        sender_group = _node("d2", "sender", leaves)

        # Leaf tuples match → role-shape match → per-leaf compat all
        # pass (default fixtures have compatible format/transport).
        result = compatible_sender_groups(recv_group, [sender_group])
        assert len(result) == 1

        # And a sender group missing the distinct-format leaf
        # (e.g. only AUDIO 0 + AUDIO 1, no VIDEO) must NOT pass the
        # shape check — even though role indices [0, 1] alone would.
        audio_only = _node("d3", "sender", [("AUDIO", 0), ("AUDIO", 1)])
        assert compatible_sender_groups(recv_group, [audio_only]) == []

    def test_format_mismatch_rejected(self) -> None:
        """Leaf ``format`` URN mismatch drops the pair."""
        rx = _make_receiver("r0")
        rx["format"] = "urn:x-nmos:format:video"
        tx = _make_sender("s0")
        tx["format"] = "urn:x-nmos:format:audio"
        recv_group = self._group("d1", [(0, rx)])
        sender_group = self._group("d2", [(0, tx)])
        assert compatible_sender_groups(recv_group, [sender_group]) == []


class TestTransportCompatible:
    """Unit tests for the ``isTransportCompatible`` whitelist."""

    def test_rtp_generic_accepts_unicast_and_multicast(self) -> None:
        from nmos.controller.compat import transport_compatible
        assert transport_compatible(
            "urn:x-nmos:transport:rtp",
            "urn:x-nmos:transport:rtp.ucast",
        ) is True
        assert transport_compatible(
            "urn:x-nmos:transport:rtp",
            "urn:x-nmos:transport:rtp.mcast",
        ) is True

    def test_rtp_unicast_rejects_multicast(self) -> None:
        from nmos.controller.compat import transport_compatible
        assert transport_compatible(
            "urn:x-nmos:transport:rtp.ucast",
            "urn:x-nmos:transport:rtp.mcast",
        ) is False
        assert transport_compatible(
            "urn:x-nmos:transport:rtp.mcast",
            "urn:x-nmos:transport:rtp.ucast",
        ) is False

    def test_unknown_or_missing_transport_rejected(self) -> None:
        from nmos.controller.compat import transport_compatible
        assert transport_compatible(None, "urn:x-nmos:transport:rtp") is False
        assert transport_compatible("urn:x-nmos:transport:rtp", None) is False
        assert transport_compatible(
            "urn:x-nmos:transport:unknown-family",
            "urn:x-nmos:transport:rtp",
        ) is False

    def test_srt_variants_interchangeable(self) -> None:
        """SRT and SRT/MPEG2-TS are mutually compatible per the
        transport-compatibility matrix.
        """
        from nmos.codegen.namespaces import SRT_TRANSPORT_NAMESPACE
        from nmos.controller.compat import transport_compatible
        srt = SRT_TRANSPORT_NAMESPACE + "transport:srt"
        srt_mp2t = SRT_TRANSPORT_NAMESPACE + "transport:srt.mp2t"
        assert transport_compatible(
            srt,
            srt_mp2t,
        ) is True
        assert transport_compatible(
            srt_mp2t,
            srt,
        ) is True


# ---------------------------------------------------------------------------
# CCF Caps → NMOS JSON inverse converter + intersection
# ---------------------------------------------------------------------------

class TestCapsRoundTrip:
    """Confirm the CCF→JSON inverse mirrors the JSON→CCF forward path
    closely enough for the controller's caps picker to round-trip a
    sender declaration through ``caps_constrict_by_cons`` without
    losing its structure."""

    def test_round_trip_preserves_labels_and_media_type(self) -> None:
        from caps.MatroxCCF import (
            convert_caps_caps_to_json,
            convert_caps_json_to_caps,
        )
        src = {"constraint_sets": [{
            "urn:x-nmos:cap:meta:label":          "Native",
            "urn:x-nmos:cap:meta:preference":     100,
            "urn:x-matrox:cap:meta:format":       "urn:x-nmos:format:audio",
            "urn:x-matrox:cap:meta:layer":        0,
            "urn:x-nmos:cap:format:media_type":   {"enum": ["audio/L24"]},
            "urn:x-nmos:cap:format:channel_count": {"enum": [1, 2]},
        }]}
        ccf = convert_caps_json_to_caps(src)
        back = convert_caps_caps_to_json(ccf)
        assert back["constraint_sets"][0]["urn:x-nmos:cap:meta:label"] == "Native"
        assert back["constraint_sets"][0]["urn:x-nmos:cap:meta:preference"] == 100
        assert (
            back["constraint_sets"][0]["urn:x-nmos:cap:format:media_type"]
            == {"enum": ["audio/L24"]}
        )
        # Channel_count enum order should survive the round-trip.
        assert set(
            back["constraint_sets"][0]["urn:x-nmos:cap:format:channel_count"]["enum"]
        ) == {1, 2}

    def test_round_trip_rational_enum_becomes_numerator_denominator(self) -> None:
        from caps.MatroxCCF import (
            convert_caps_caps_to_json,
            convert_caps_json_to_caps,
        )
        src = {"constraint_sets": [{
            "urn:x-nmos:cap:meta:label": "60Hz",
            "urn:x-nmos:cap:meta:preference": 50,
            "urn:x-nmos:cap:format:grain_rate": {
                "enum": [{"numerator": 60, "denominator": 1}],
            },
        }]}
        ccf = convert_caps_json_to_caps(src)
        back = convert_caps_caps_to_json(ccf)
        enum = back["constraint_sets"][0]["urn:x-nmos:cap:format:grain_rate"]["enum"]
        assert enum == [{"numerator": 60, "denominator": 1}]

    def test_receiver_enum_superset_of_sender_keeps_cs_narrowed(self) -> None:
        """When the receiver declares values the sender doesn't
        support (common: receiver lists multiple media_types, sender
        has a single mode), the strict inclusion check of the
        non-adjust primitive would over-reject. Using
        ``caps_constrict_adjust_by_cons`` with receiver=Caps and
        sender=Cons (via ``to_cons()``) keeps the CS and narrows the
        result to the sender's offering.

        This is the specific scenario the user flagged: sender audio 0
        offers PCM L24, receiver audio 0 accepts L24 or AC3 — the pair
        should be compatible (they overlap on L24), not dropped.
        """
        from nmos.controller.compat import filter_sender_cs_by_receiver
        # The intersection must include a trunk CS
        # (no ``cap:meta:format`` + no ``cap:meta:layer``) or the
        # whole result is dropped. Real senders/receivers publish
        # one trunk CS describing the mux-level config plus
        # per-layer CSs; tests mirror that shape.
        sender = _make_sender("s1", {
            "constraint_sets": [
                {"urn:x-nmos:cap:meta:label": "Trunk"},
                {
                    "urn:x-nmos:cap:meta:label": "Sender-Native-PCM",
                    "urn:x-nmos:cap:meta:preference": 100,
                    "urn:x-matrox:cap:meta:format": "urn:x-nmos:format:audio",
                    "urn:x-matrox:cap:meta:layer": 0,
                    "urn:x-nmos:cap:format:media_type":
                        {"enum": ["audio/L24"]},
                },
            ],
        })
        receiver = _make_receiver("r1", {
            "constraint_sets": [
                {"urn:x-nmos:cap:meta:label": "Trunk"},
                {
                    "urn:x-nmos:cap:meta:label": "Receiver-AnyAudio",
                    "urn:x-nmos:cap:meta:preference": 100,
                    "urn:x-matrox:cap:meta:format": "urn:x-nmos:format:audio",
                    "urn:x-matrox:cap:meta:layer": 0,
                    "urn:x-nmos:cap:format:media_type":
                        {"enum": ["audio/L24", "audio/ac3"]},
                },
            ],
        })
        out = filter_sender_cs_by_receiver(sender, receiver)
        kept = out["caps"]["constraint_sets"]
        # Trunk + layer-specific → 2 capsets survive.
        assert len(kept) == 2
        # Find the layer-specific result and check the narrowing.
        layer_cs = next(
            c for c in kept
            if c.get("urn:x-matrox:cap:meta:layer") is not None
        )
        assert (
            layer_cs["urn:x-nmos:cap:format:media_type"]
            == {"enum": ["audio/L24"]}
        )
        assert layer_cs["urn:x-nmos:cap:meta:preference"] == 100

    def test_sender_missing_param_preserves_receiver_constraint(self) -> None:
        """``caps.to_cons()`` reinterprets a missing sender parameter
        as "don't care" (Cons semantic). The receiver's constraint on
        that parameter survives into the narrowed result — it's
        neither dropped nor widened to infinite.
        """
        from nmos.controller.compat import filter_sender_cs_by_receiver
        # Trunk CSs on both sides satisfy the mux-valid requirement.
        # The layer-specific CSs carry the actual
        # param data the test is asserting against.
        sender = _make_sender("s1", {
            "constraint_sets": [
                {"urn:x-nmos:cap:meta:label": "Trunk"},
                {
                    "urn:x-nmos:cap:meta:label": "Sender",
                    "urn:x-nmos:cap:meta:preference": 100,
                    "urn:x-matrox:cap:meta:format": "urn:x-nmos:format:audio",
                    "urn:x-matrox:cap:meta:layer": 0,
                    "urn:x-nmos:cap:format:media_type":
                        {"enum": ["audio/L24"]},
                },
            ],
        })
        receiver = _make_receiver("r1", {
            "constraint_sets": [
                {"urn:x-nmos:cap:meta:label": "Trunk"},
                {
                    "urn:x-nmos:cap:meta:label": "Receiver",
                    "urn:x-nmos:cap:meta:preference": 100,
                    "urn:x-matrox:cap:meta:format": "urn:x-nmos:format:audio",
                    "urn:x-matrox:cap:meta:layer": 0,
                    "urn:x-nmos:cap:format:media_type":
                        {"enum": ["audio/L24"]},
                    "urn:x-nmos:cap:format:channel_count":
                        {"enum": [2]},
                },
            ],
        })
        out = filter_sender_cs_by_receiver(sender, receiver)
        kept = out["caps"]["constraint_sets"]
        assert len(kept) == 2   # trunk + layer-specific
        layer_cs = next(
            c for c in kept
            if c.get("urn:x-matrox:cap:meta:layer") is not None
        )
        # The receiver's channel_count constraint is preserved in the
        # result — the sender's silence on this param is "don't care",
        # not "any value" (which would've widened it).
        assert (
            layer_cs.get("urn:x-nmos:cap:format:channel_count")
            == {"enum": [2]}
        )

    def test_pcm_only_receiver_narrows_sender_cs_list(self) -> None:
        """End-to-end: PCM-only receiver paired with an AAC+PCM sender.
        The algorithm iterates receiver capsets as X and uses the
        full sender as Cons (Y). For each receiver capset, every
        sender conset whose media_type intersects produces a result
        — so a 1-CS PCM receiver paired with a 2-CS AAC+PCM sender
        yields ONE kept result (from the sender's PCM conset; the
        AAC conset is dropped). Result label + preference come from
        the receiver's capset, not the sender's.
        """
        from nmos.controller.compat import filter_sender_cs_by_receiver
        sender = _make_sender("s1", {
            "constraint_sets": [
                {"urn:x-nmos:cap:meta:label": "Trunk"},
                {
                    "urn:x-nmos:cap:meta:label": "Native-AAC",
                    "urn:x-nmos:cap:meta:preference": 100,
                    "urn:x-matrox:cap:meta:format": "urn:x-nmos:format:audio",
                    "urn:x-matrox:cap:meta:layer": 0,
                    "urn:x-nmos:cap:format:media_type":
                        {"enum": ["audio/mpeg4-generic"]},
                },
                {
                    "urn:x-nmos:cap:meta:label": "Native-PCM",
                    "urn:x-nmos:cap:meta:preference": 50,
                    "urn:x-matrox:cap:meta:format": "urn:x-nmos:format:audio",
                    "urn:x-matrox:cap:meta:layer": 0,
                    "urn:x-nmos:cap:format:media_type":
                        {"enum": ["audio/L24"]},
                },
            ],
        })
        receiver = _make_receiver("r1", {
            "constraint_sets": [
                {"urn:x-nmos:cap:meta:label": "Trunk"},
                {
                    "urn:x-nmos:cap:meta:label": "Receiver-PCM",
                    "urn:x-nmos:cap:meta:preference": 100,
                    "urn:x-matrox:cap:meta:format": "urn:x-nmos:format:audio",
                    "urn:x-matrox:cap:meta:layer": 0,
                    "urn:x-nmos:cap:format:media_type":
                        {"enum": ["audio/L24"]},
                },
            ],
        })
        out = filter_sender_cs_by_receiver(sender, receiver)
        kept = out["caps"]["constraint_sets"]
        # Trunk (receiver) + one layer-specific pair = 2 surviving CSs.
        # The AAC sender conset has media_type=mpeg4 while the receiver
        # only accepts L24 → empty per-param → that Y dropped; the
        # PCM sender conset does intersect → layer-specific result kept.
        assert len(kept) == 2
        layer_cs = next(
            c for c in kept
            if c.get("urn:x-matrox:cap:meta:layer") is not None
        )
        # Result carries the receiver's label + preference (the X
        # side); sender's labels never appear on this page.
        assert layer_cs["urn:x-nmos:cap:meta:label"] == "Receiver-PCM"
        assert layer_cs["urn:x-nmos:cap:meta:preference"] == 100
        # And the narrowed media_type is L24 — the overlap.
        assert (
            layer_cs["urn:x-nmos:cap:format:media_type"]
            == {"enum": ["audio/L24"]}
        )


# ---------------------------------------------------------------------------
# Subset-of-group matching (compatible_sender_groups_superset)
# ---------------------------------------------------------------------------

def _mixed_group(
    device_id: str, kind: str, leaves: list[tuple[str, int]],
    *, transport: str = "urn:x-nmos:transport:rtp",
) -> NaturalGroupView:
    """Build a natural group with mixed (format, role) leaves — the
    multi-format shape used by MUX senders / receivers. Each member
    carries an NMOS ``format`` URN matching its leaf format.
    """
    make = _make_sender if kind == "sender" else _make_receiver
    grouped: list[GroupedResource] = []
    for fmt, role in leaves:
        r = make(f"{kind}-{device_id}-{fmt}-{role}")
        r["format"] = f"urn:x-nmos:format:{fmt.lower()}"
        r["transport"] = transport
        grouped.append(GroupedResource(
            id=r["id"], label=r["label"], description="",
            device_id=device_id, device_serial="", device_label="",
            hint=GroupHint(
                group_name="RTP 0", role_name=f"{fmt} {role}",
                groupable=True, format=fmt, role=role,
            ),
            resource=r,
        ))
    return NaturalGroupView(
        device_id=device_id, device_serial="", device_label="",
        hint_key=("RTP", 0), members=grouped,
    )


class TestSupersetMatch:
    """Superset-matching: sender groups whose leaf signature contains
    the receiver subset. Each subset leaf must pair with a compatible
    sender leaf; extra sender legs are ignored.
    """

    def test_mux_sender_matches_audio_only_subset(self) -> None:
        """Receiver subset {AUDIO 0, AUDIO 1} against a V+A+A MUX
        sender group. The extra VIDEO 0 leg is ignored; the matcher
        returns the two audio legs in subset-signature order.
        """
        subset = _mixed_group(
            "d1", "receiver", [("AUDIO", 0), ("AUDIO", 1)],
        )
        mux_sender = _mixed_group(
            "d2", "sender",
            [("VIDEO", 0), ("AUDIO", 0), ("AUDIO", 1)],
        )
        results = compatible_sender_groups_superset(subset, [mux_sender])
        assert len(results) == 1
        match = results[0]
        assert match.group is mux_sender
        matched_leaves = [
            (m.hint.format, m.hint.role)
            for m in match.matched_members if m.hint is not None
        ]
        assert matched_leaves == [("AUDIO", 0), ("AUDIO", 1)]
        assert {m.id for m in match.matched_members} == match.matched_ids

    def test_missing_leaf_rejected(self) -> None:
        """Sender group missing a subset leaf is dropped, even if
        role-index alone would line up (the format check prevents a
        VIDEO leg from standing in for an AUDIO leg).
        """
        subset = _mixed_group(
            "d1", "receiver", [("AUDIO", 0), ("AUDIO", 1)],
        )
        partial_sender = _mixed_group(
            "d2", "sender", [("VIDEO", 0), ("AUDIO", 0)],
        )
        assert compatible_sender_groups_superset(
            subset, [partial_sender],
        ) == []

    def test_exact_shape_match_works_as_sub_case(self) -> None:
        """Strict shape equality is the zero-extras sub-case of the
        superset match.
        """
        subset = _mixed_group(
            "d1", "receiver", [("AUDIO", 0), ("AUDIO", 1)],
        )
        exact_sender = _mixed_group(
            "d2", "sender", [("AUDIO", 0), ("AUDIO", 1)],
        )
        results = compatible_sender_groups_superset(subset, [exact_sender])
        assert len(results) == 1
        assert len(results[0].matched_members) == 2

    def test_format_mismatch_on_leaf_rejected(self) -> None:
        """If the sender leaf's NMOS ``format`` URN differs from the
        receiver leaf's (even with matching role index), the pair is
        dropped via the ``format != senderFormat`` guard.
        """
        subset = _mixed_group("d1", "receiver", [("AUDIO", 0)])
        sender_group = _mixed_group("d2", "sender", [("AUDIO", 0)])
        # Corrupt the sender's NMOS format URN so format_compatible
        # fails; hint.format is still "AUDIO" so multiset containment
        # passes, isolating the NMOS-format check.
        sender_group.members[0].resource["format"] = (
            "urn:x-nmos:format:video"
        )
        assert compatible_sender_groups_superset(
            subset, [sender_group],
        ) == []

    def test_transport_mismatch_on_leaf_rejected(self) -> None:
        """Sender rtp.ucast + receiver rtp.mcast fail the transport
        whitelist — reject the group.
        """
        subset = _mixed_group(
            "d1", "receiver", [("AUDIO", 0)],
            transport="urn:x-nmos:transport:rtp.mcast",
        )
        sender_group = _mixed_group(
            "d2", "sender", [("AUDIO", 0)],
            transport="urn:x-nmos:transport:rtp.ucast",
        )
        assert compatible_sender_groups_superset(
            subset, [sender_group],
        ) == []

    def test_empty_subset_yields_empty(self) -> None:
        empty_view = NaturalGroupView(
            device_id="d1", device_serial="", device_label="",
            hint_key=("RTP", 0), members=[],
        )
        sender_group = _mixed_group("d2", "sender", [("AUDIO", 0)])
        assert compatible_sender_groups_superset(
            empty_view, [sender_group],
        ) == []

    def test_preserves_supplied_sender_group_order(self) -> None:
        """When multiple sender groups match, results are returned in
        the supplied order (callers rely on this for stable UI
        rendering).
        """
        subset = _mixed_group("d1", "receiver", [("AUDIO", 0)])
        a = _mixed_group("d2", "sender", [("AUDIO", 0)])
        b = _mixed_group(
            "d3", "sender", [("VIDEO", 0), ("AUDIO", 0)],
        )
        c = _mixed_group(
            "d4", "sender", [("AUDIO", 0), ("AUDIO", 1)],
        )
        order = [c, a, b]
        results = compatible_sender_groups_superset(subset, order)
        assert [m.group.device_id for m in results] == ["d4", "d2", "d3"]


# ---------------------------------------------------------------------------
# Pair-by-identity (sender ↔ receiver leaf-identity pairing at caps/configure)
# ---------------------------------------------------------------------------

def _tagged(resource: dict[str, Any], fmt: str, role: int) -> dict[str, Any]:
    """Attach a parseable group-hint tag to the resource so
    ``extract_group_hint`` can read it.
    """
    resource["tags"] = {
        GROUP_HINT_TAG: [f"RTP 0:{fmt} {role}"],
    }
    return resource


class TestPairByIdentity:
    def test_pairs_regardless_of_url_order(self) -> None:
        s_a0 = _tagged(_make_sender("s_a0"), "AUDIO", 0)
        s_a1 = _tagged(_make_sender("s_a1"), "AUDIO", 1)
        r_a0 = _tagged(_make_receiver("r_a0"), "AUDIO", 0)
        r_a1 = _tagged(_make_receiver("r_a1"), "AUDIO", 1)

        # Deliberately misorder the senders relative to the receivers.
        pairs = pair_by_identity([s_a1, s_a0], [r_a0, r_a1])
        assert [p[0]["id"] for p in pairs] == ["s_a0", "s_a1"]
        assert [p[1]["id"] for p in pairs] == ["r_a0", "r_a1"]

    def test_duplicate_sender_leaf_raises(self) -> None:
        s_a0_a = _tagged(_make_sender("s_a0_a"), "AUDIO", 0)
        s_a0_b = _tagged(_make_sender("s_a0_b"), "AUDIO", 0)
        r_a0 = _tagged(_make_receiver("r_a0"), "AUDIO", 0)
        with pytest.raises(ValueError, match="leaf"):
            pair_by_identity([s_a0_a, s_a0_b], [r_a0])

    def test_receiver_leaf_without_match_raises(self) -> None:
        s_a0 = _tagged(_make_sender("s_a0"), "AUDIO", 0)
        r_v0 = _tagged(_make_receiver("r_v0"), "VIDEO", 0)
        with pytest.raises(ValueError, match="no matching sender"):
            pair_by_identity([s_a0], [r_v0])

    def test_sender_without_hint_raises(self) -> None:
        s = _make_sender("s1")           # no tags → no hint
        r = _tagged(_make_receiver("r1"), "AUDIO", 0)
        with pytest.raises(ValueError, match="group hint"):
            pair_by_identity([s], [r])

    def test_receiver_without_hint_raises(self) -> None:
        s = _tagged(_make_sender("s1"), "AUDIO", 0)
        r = _make_receiver("r1")         # no tags → no hint
        with pytest.raises(ValueError, match="group hint"):
            pair_by_identity([s], [r])

    def test_mux_pairs_every_leg(self) -> None:
        """Mixed-format subset pairs correctly — video leg to video,
        audio legs to their role-indexed partners.
        """
        s_v0 = _tagged(_make_sender("s_v0"), "VIDEO", 0)
        s_v0["format"] = "urn:x-nmos:format:video"
        s_a0 = _tagged(_make_sender("s_a0"), "AUDIO", 0)
        s_a1 = _tagged(_make_sender("s_a1"), "AUDIO", 1)
        r_v0 = _tagged(_make_receiver("r_v0"), "VIDEO", 0)
        r_v0["format"] = "urn:x-nmos:format:video"
        r_a0 = _tagged(_make_receiver("r_a0"), "AUDIO", 0)
        r_a1 = _tagged(_make_receiver("r_a1"), "AUDIO", 1)

        pairs = pair_by_identity([s_a1, s_v0, s_a0], [r_v0, r_a0, r_a1])
        assert [p[0]["id"] for p in pairs] == ["s_v0", "s_a0", "s_a1"]
        assert [p[1]["id"] for p in pairs] == ["r_v0", "r_a0", "r_a1"]


class TestAbsentVsEmptyCapabilities:
    """Controller caps policy (sender and receiver alike):

    * ``constraint_sets`` attribute ABSENT (no ``caps``, or ``caps`` without
      the attribute) → capable of EVERYTHING (universal capset).
    * present ``[]`` → capable of NOTHING (BCP-004-01: empty array
      unsatisfiable).
    * present ``[..]`` → the declared sets (normal CCF).
    """

    def test_resource_ccf_caps_absent_is_universal(self) -> None:
        for res in (
            _make_sender("s1"),                              # caps omitted
            _make_sender("s2", {}),                          # caps, no constraint_sets
            _make_sender("s3", {"media_types": ["video/raw"]}),  # media_types only
        ):
            caps = resource_ccf_caps(res)
            assert caps is not None and len(caps.capsets) == 1
            assert not caps.capsets[0].caps   # empty constraints = universal

    def test_resource_ccf_caps_empty_is_nothing(self) -> None:
        caps = resource_ccf_caps(_make_sender("s1", {"constraint_sets": []}))
        assert caps is not None and len(caps.capsets) == 0   # capable of nothing

    def test_is_compatible_absent_vs_present(self) -> None:
        absent = resource_ccf_caps(_make_sender("s"))        # universal
        cs = resource_ccf_caps(_make_receiver("r", {"constraint_sets": [
            {"urn:x-nmos:cap:meta:label": "T"}]}))
        empty = resource_ccf_caps(_make_receiver("r2", {"constraint_sets": []}))
        assert is_compatible(absent, cs) is True             # universal ∩ CS = CS
        assert is_compatible(cs, absent) is True
        assert is_compatible(empty, cs) is False             # nothing ∩ CS = nothing
        assert is_compatible(cs, empty) is False
        assert is_compatible(empty, empty) is False

    def test_filter_absent_sender_shows_receiver_cs(self) -> None:
        from nmos.controller.compat import filter_sender_cs_by_receiver
        sender = _make_sender("s1")                          # no caps → universal
        # Non-mux receiver: one trunk CS (no meta:format/layer) with a real
        # constraint. universal sender ∩ trunk-CS = that CS.
        receiver = _make_receiver("r1", {"constraint_sets": [
            {"urn:x-nmos:cap:meta:label": "RX",
             "urn:x-nmos:cap:meta:preference": 100,
             "urn:x-nmos:cap:format:media_type": {"enum": ["audio/L24"]}}]})
        out = filter_sender_cs_by_receiver(sender, receiver)
        kept = out["caps"]["constraint_sets"]
        assert len(kept) == 1
        # The receiver's constraint flows through (universal ∩ Y = Y).
        assert kept[0]["urn:x-nmos:cap:format:media_type"] == {"enum": ["audio/L24"]}

    def test_filter_absent_receiver_returns_sender_unchanged(self) -> None:
        from nmos.controller.compat import filter_sender_cs_by_receiver
        sender = _make_sender("s1", {"constraint_sets": [
            {"urn:x-nmos:cap:meta:label": "Trunk"},
            {"urn:x-nmos:cap:meta:label": "SX",
             "urn:x-matrox:cap:meta:format": "urn:x-nmos:format:audio",
             "urn:x-matrox:cap:meta:layer": 0,
             "urn:x-nmos:cap:format:media_type": {"enum": ["audio/L24"]}}]})
        receiver = _make_receiver("r1")                      # no caps → universal
        out = filter_sender_cs_by_receiver(sender, receiver)
        # universal receiver: sender's caps pass through unchanged.
        assert len(out["caps"]["constraint_sets"]) == 2

    def test_filter_empty_sender_capable_of_nothing(self) -> None:
        from nmos.controller.compat import filter_sender_cs_by_receiver
        sender = _make_sender("s1", {"constraint_sets": []})
        receiver = _make_receiver("r1", {"constraint_sets": [
            {"urn:x-nmos:cap:meta:label": "Trunk"}]})
        out = filter_sender_cs_by_receiver(sender, receiver)
        assert out["caps"]["constraint_sets"] == []          # nothing to negotiate
