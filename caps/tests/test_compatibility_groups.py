# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Compliance tests for the layer_compatibility_groups filter API on
caps.MatroxCCF.Caps and caps.MatroxCCF.Cons.

The Matrox CCF exposes two group-aware entry points on Caps (and the
symmetric Cons) that are not yet called by any production code path:

    Caps.get_compatibility_groups()  -> Set[int]
    Caps._filter(..., compatibility_group=N)
    Caps.get(..., compatibility_group=N)
    Caps.get_capset(..., compatibility_group=N)

These implement the spec requirements stated in SenderCapabilities.md and
ReceiverCapabilities.md:
    C1 — a CapSet without the attribute MUST be assumed part of all groups,
         so it MUST be included by every per-group filter (MatroxCCF.py:928).
    C3 — group values are unsigned integers in the range 0..63
         (MatroxCCF.py:914, 1207).

Because the API is dormant, the `caps/tests/` directory did not exist prior
to this module. These tests provide a safety net so that any future wiring
(IS-11 constraint-set selection, sender/receiver mapping, etc.) has coverage
to rely on.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# MatroxCCF is a standalone module. Follow the convention used by
# nmos/node/tests/*.py: add caps/ to sys.path and import as a top-level name.

try:
    from caps.MatroxCCF import (  # type: ignore[import-not-found]
        Caps, CapSet, Capability, RangeValue, RangeType,
        Cons, ConSet, Constraint,
        CapFormatMediaType,
    )
    HAS_CCF = True
except ImportError:
    HAS_CCF = False


# ---------------------------------------------------------------------------
# Fixtures — construct Caps and Cons whose group topology is known
# ---------------------------------------------------------------------------

def _make_capset(label: str, groups: set[int] | None, preference: int = 0) -> "CapSet":
    """Build a trunk CapSet (format=None, layer=None) tagged with the given
    layer_compatibility_groups. Preference lets us distinguish ordering."""
    return CapSet(
        caps={},
        preference=preference,
        label=label,
        format=None,
        layer=None,
        layer_compatibility_groups=groups,
    )


def _make_conset(label: str, groups: set[int] | None, preference: int = 0) -> "ConSet":
    return ConSet(
        cons={},
        preference=preference,
        label=label,
        format=None,
        layer=None,
        layer_compatibility_groups=groups,
    )


@pytest.fixture
def mixed_caps() -> "Caps":
    """Caps with: group {0}, group {1}, group {0,1}, and a CapSet with no attribute."""
    return Caps(capsets=[
        _make_capset("only-0", {0}, preference=10),
        _make_capset("only-1", {1}, preference=20),
        _make_capset("both", {0, 1}, preference=15),
        _make_capset("missing-attr", None, preference=5),
    ])


@pytest.fixture
def mixed_cons() -> "Cons":
    return Cons(consets=[
        _make_conset("only-0", {0}, preference=10),
        _make_conset("only-1", {1}, preference=20),
        _make_conset("both", {0, 1}, preference=15),
        _make_conset("missing-attr", None, preference=5),
    ])


# ===========================================================================
# Filter semantics — spec C1
# ===========================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestCCFCompatibilityGroupFilter:
    """Verify Caps/Cons filter and aggregator behavior per MatroxCCF.py:876-969
    and 1169-1259."""

    # ----- Filter inclusion (spec C1: missing attribute = all groups) -----

    def test_filter_by_group_0_returns_only_group_0_and_missing(
        self, mixed_caps: "Caps"
    ) -> None:
        subset = mixed_caps._filter(compatibility_group=0)
        labels = {cs.label for cs in subset}
        # Group 0 explicit members plus the "both" CapSet plus the missing-attribute one.
        assert labels == {"only-0", "both", "missing-attr"}

    def test_filter_by_group_1_returns_only_group_1_and_missing(
        self, mixed_caps: "Caps"
    ) -> None:
        subset = mixed_caps._filter(compatibility_group=1)
        labels = {cs.label for cs in subset}
        assert labels == {"only-1", "both", "missing-attr"}

    def test_filter_by_group_2_returns_only_missing(
        self, mixed_caps: "Caps"
    ) -> None:
        # No explicit member of group 2, but the missing-attribute CapSet is still in.
        subset = mixed_caps._filter(compatibility_group=2)
        labels = {cs.label for cs in subset}
        assert labels == {"missing-attr"}

    def test_filter_by_group_0_on_cons_matches_caps_semantics(
        self, mixed_cons: "Cons"
    ) -> None:
        subset = mixed_cons._filter(compatibility_group=0)
        labels = {cs.label for cs in subset}
        assert labels == {"only-0", "both", "missing-attr"}

    # ----- Aggregator (get_compatibility_groups) ------------------------

    def test_get_compatibility_groups_returns_union_of_explicit(self) -> None:
        caps = Caps(capsets=[
            _make_capset("a", {0}),
            _make_capset("b", {1, 2}),
            _make_capset("c", None),  # missing attribute doesn't contribute
        ])
        assert caps.get_compatibility_groups() == {0, 1, 2}

    def test_get_compatibility_groups_empty_returns_sentinel_63(self) -> None:
        # When no CapSet has an explicit attribute, the aggregator returns {63}
        # as a sentinel meaning "any group" (MatroxCCF.py:888-890).
        caps = Caps(capsets=[_make_capset("a", None), _make_capset("b", None)])
        assert caps.get_compatibility_groups() == {63}

    def test_get_compatibility_groups_on_cons_matches_caps_semantics(self) -> None:
        cons = Cons(consets=[
            _make_conset("a", {0}),
            _make_conset("b", {1, 2}),
            _make_conset("c", None),
        ])
        assert cons.get_compatibility_groups() == {0, 1, 2}

    # ----- Validation (exclusivity + range + type) ----------------------

    def test_filter_rejects_combined_with_format(self, mixed_caps: "Caps") -> None:
        # MatroxCCF.py:912 — compatibility_group is exclusive to other filters.
        with pytest.raises(ValueError, match="exclusive"):
            mixed_caps._filter(format="urn:x-nmos:format:video", compatibility_group=0)

    def test_filter_rejects_combined_with_layer(self, mixed_caps: "Caps") -> None:
        with pytest.raises(ValueError, match="exclusive"):
            mixed_caps._filter(layer=0, compatibility_group=0)

    def test_filter_rejects_combined_with_media_types(self, mixed_caps: "Caps") -> None:
        with pytest.raises(ValueError, match="exclusive"):
            mixed_caps._filter(media_types=["video/raw"], compatibility_group=0)

    def test_filter_rejects_negative_group(self, mixed_caps: "Caps") -> None:
        # Spec C3 — range 0..63.
        with pytest.raises(ValueError, match="compatibility_group"):
            mixed_caps._filter(compatibility_group=-1)

    def test_filter_rejects_group_above_63(self, mixed_caps: "Caps") -> None:
        with pytest.raises(ValueError, match="compatibility_group"):
            mixed_caps._filter(compatibility_group=64)

    def test_filter_accepts_group_63(self) -> None:
        # Spec C3 edge — 63 is the highest valid group. The sentinel from the
        # aggregator also uses 63, so filtering by 63 must not raise.
        caps = Caps(capsets=[_make_capset("any", None), _make_capset("g63", {63})])
        subset = caps._filter(compatibility_group=63)
        labels = {cs.label for cs in subset}
        # Both CapSets match: the None one per spec C1, and the {63} one explicitly.
        assert labels == {"any", "g63"}

    def test_filter_rejects_non_int(self, mixed_caps: "Caps") -> None:
        with pytest.raises(ValueError, match="Optional\\[int\\]"):
            mixed_caps._filter(compatibility_group="0")  # type: ignore[arg-type]

    # ----- get/get_capset entry points ---------------------------------

    def test_get_capset_with_group_filter_returns_highest_preference(
        self, mixed_caps: "Caps"
    ) -> None:
        # Group 0 members: only-0 (pref 10), both (pref 15), missing-attr (pref 5).
        # get_capset sorts by preference descending and returns index 0.
        cs = mixed_caps.get_capset(compatibility_group=0)
        assert cs.label == "both"

    def test_get_capset_raises_indexerror_when_no_match(self) -> None:
        # All CapSets explicitly exclude group 5 (none declare it, none are
        # missing-attr), so filter returns an empty list.
        caps = Caps(capsets=[
            _make_capset("only-0", {0}),
            _make_capset("only-1", {1}),
        ])
        with pytest.raises(IndexError):
            caps.get_capset(compatibility_group=5)

    def test_get_returns_caps_with_filtered_true(
        self, mixed_caps: "Caps"
    ) -> None:
        # Caps.get wraps the filtered list with filtered=True (line 961).
        result = mixed_caps.get(compatibility_group=1)
        assert result.filtered is True
        labels = {cs.label for cs in result.capsets}
        assert labels == {"only-1", "both", "missing-attr"}
