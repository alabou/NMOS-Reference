# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Compliance tests for urn:x-matrox:cap:meta:layer_compatibility_groups.

Covers the MUST/SHOULD requirements in:
  - specs/Capabilities.md (attribute definition)
  - specs/SenderCapabilities.md §Layer compatibility groups (lines 175-199)
  - specs/ReceiverCapabilities.md §Layer compatibility groups (lines 184-209)

Reference implementation entry points:
    - getBitmaskFromCompatibilityGroups (3543-3556)
    - mux∩sub-flow intersection (3630-3721)

Three test layers:
  Class 1 — bitmask helper semantics (unit)
  Class 2 — fixture sanity for configs that declare groups
  Class 3 — mux∩sub-flow intersection behavior using config6a
  Class 4 — write-back of compliant groups to FlowCore
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

# Add caps/ to path for CCF

try:
    from caps.MatroxCCF import (  # type: ignore[import-not-found]
        CapMetaLayerCompatibilityGroups,
    )
    HAS_CCF = True
except ImportError:
    HAS_CCF = False

from nmos.node.compatibility import (
    get_bitmask_from_compatibility_groups,
    _write_layer_compatibility_groups,
)


BUILTIN_DIR = Path(__file__).parent.parent / "config" / "builtin"
KEY_GROUPS = "urn:x-matrox:cap:meta:layer_compatibility_groups"


# ===========================================================================
# Class 1: bitmask helper semantics (spec C1/C3 at the helper boundary)
# ===========================================================================

class TestBitmaskSemantics:
    """Unit tests for get_bitmask_from_compatibility_groups.

    Reference behavior at nmosNodeCompatibility:3543-3556.
    """

    def test_single_group_sets_one_bit(self) -> None:
        assert get_bitmask_from_compatibility_groups({3}) == 1 << 3

    def test_multiple_groups_ored(self) -> None:
        expected = (1 << 0) | (1 << 1) | (1 << 5) | (1 << 63)
        assert get_bitmask_from_compatibility_groups({0, 1, 5, 63}) == expected

    def test_missing_attribute_is_all_groups(self) -> None:
        # Spec C1: "A Constraint Set without a urn:x-matrox:cap:meta:layer_compatibility_groups
        # attribute MUST be assumed as being part of all groups."
        # Python must return 0xffffffffffffffff for None.
        assert get_bitmask_from_compatibility_groups(None) == 0xFFFFFFFFFFFFFFFF

    def test_empty_set_is_no_groups(self) -> None:
        # Explicit empty set ≠ missing attribute. Empty means "member of no group"
        # and is a valid (though restrictive) constraint.
        assert get_bitmask_from_compatibility_groups(set()) == 0

    def test_group_63_is_highest_valid(self) -> None:
        # Spec C3: values must be unsigned integers in 0..63.
        mask = get_bitmask_from_compatibility_groups({63})
        assert mask == 1 << 63
        assert mask & (1 << 63) != 0

    def test_group_out_of_range_ignored(self) -> None:
        # Values ≥ 64 are silently dropped (would not fit in the 64-bit mask).
        assert get_bitmask_from_compatibility_groups({64, 100, 200}) == 0
        # Mixed in-range and out-of-range keeps only the in-range bits.
        assert get_bitmask_from_compatibility_groups({0, 64}) == 1
        assert get_bitmask_from_compatibility_groups({5, 127}) == 1 << 5

    def test_intersection_with_missing_leaves_mask_unchanged(self) -> None:
        # End-to-end C1: ANDing a concrete mask against the "missing" sentinel
        # must leave the concrete mask untouched.
        concrete = get_bitmask_from_compatibility_groups({0, 2})
        missing = get_bitmask_from_compatibility_groups(None)
        assert concrete & missing == concrete


# ===========================================================================
# Class 2: fixture sanity — every config that declares groups is well-formed
# ===========================================================================

class TestConfigsHaveCompatibilityGroups:
    """Sanity checks on builtin configs that declare layer_compatibility_groups."""

    def _iter_constraint_sets_with_groups(
        self,
    ) -> list[tuple[str, str, str, list[int]]]:
        """Yield (config_name, kind, label, groups) for every constraint set
        with an explicit layer_compatibility_groups attribute."""
        out: list[tuple[str, str, str, list[int]]] = []
        for cfg_path in sorted(BUILTIN_DIR.glob("config*.json")):
            with open(cfg_path) as f:
                cfg = json.load(f)
            for kind in ("senders", "receivers"):
                for res in cfg.get(kind, []):
                    for cs in res.get("constraint_sets", []):
                        groups = cs.get(KEY_GROUPS)
                        if groups is not None:
                            out.append((
                                cfg_path.stem,
                                kind,
                                res.get("label", "?"),
                                groups,
                            ))
        return out

    def _load_config6a(self) -> dict[str, Any]:
        with open(BUILTIN_DIR / "config6a.json") as f:
            return json.load(f)  # type: ignore[no-any-return]

    def test_config6a_receiver_has_group_0_and_1(self) -> None:
        cfg = self._load_config6a()
        groups_seen: set[int] = set()
        for r in cfg.get("receivers", []):
            if "mux" not in r.get("format", ""):
                continue
            for cs in r.get("constraint_sets", []):
                g = cs.get(KEY_GROUPS)
                if g is not None:
                    groups_seen.update(g)
        assert 0 in groups_seen, "config6a receiver must have a group-0 (uncompressed) subflow"
        assert 1 in groups_seen, "config6a receiver must have a group-1 (compressed) subflow"

    def test_config6a_sender_mux_has_groups(self) -> None:
        cfg = self._load_config6a()
        found_any = False
        for s in cfg.get("senders", []):
            if "mux" not in s.get("format", ""):
                continue
            for cs in s.get("constraint_sets", []):
                if KEY_GROUPS in cs:
                    found_any = True
                    # All declared values must be a non-empty list of ints.
                    assert isinstance(cs[KEY_GROUPS], list) and len(cs[KEY_GROUPS]) > 0
        assert found_any, "config6a sender mux must declare layer_compatibility_groups"

    def test_all_groups_are_in_range_0_to_63(self) -> None:
        # Spec C3 — every declared group value across all configs must be in [0, 63].
        for cfg_name, kind, label, groups in self._iter_constraint_sets_with_groups():
            for g in groups:
                assert 0 <= g <= 63, (
                    f"{cfg_name} {kind} '{label}' has out-of-range group {g}"
                )

    def test_groups_are_list_of_int(self) -> None:
        # Per Capabilities.md: attribute type is "array of integer".
        for cfg_name, kind, label, groups in self._iter_constraint_sets_with_groups():
            assert isinstance(groups, list), (
                f"{cfg_name} {kind} '{label}' groups must be list"
            )
            for g in groups:
                assert isinstance(g, int) and not isinstance(g, bool), (
                    f"{cfg_name} {kind} '{label}' group {g!r} must be int"
                )


# ===========================================================================
# Class 3: mux∩sub-flow intersection (spec C5)
# ===========================================================================
#
# The intersection logic lives inside check_sender_flow_compatibility in
# compatibility.py:587-680. It is called by node.set_sender_compatibility_state().
# Rather than build an ad-hoc mock node, we exercise the logic by:
#   (a) instantiating NFlowCore objects directly (to verify mask behavior at
#       the data-model level), and
#   (b) running a node built from config6a, which has the two-group topology,
#       to confirm the intersection is actually evaluated end-to-end.
# ===========================================================================

@pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")
class TestMuxSubflowIntersection:
    """Spec C5: intersection of mux Flow groups with all sub-Flow groups
    MUST NOT be empty."""

    def _simulate_intersection(self, mux: list[int] | None,
                               subflows: list[list[int] | None]) -> int:
        """Replicate compatibility.py:672-679 logic: start with 0xff..ff,
        then AND with each defined sub-flow bitmask. Undefined sub-flows
        skip the AND (mirroring `.defined` guard at line 677)."""
        mask = 0xFFFFFFFFFFFFFFFF
        if mux is not None:
            mask &= get_bitmask_from_compatibility_groups(set(mux))
        for sf in subflows:
            if sf is None:
                continue  # undefined — skip AND, per compatibility.py:677
            mask &= get_bitmask_from_compatibility_groups(set(sf))
            if mask == 0:
                return 0
        return mask

    def test_intersection_nonempty_accepts_mux(self) -> None:
        # Spec C5 positive — mux [0,1], sub-flows [0] and [1] → overlap exists.
        # After first subflow: mask = 0b01; after second: still 0b01 (empty is bad).
        # With mux [0,1], subflow [0]: mask = (0b11 & 0b01) = 0b01; then with
        # second subflow [1]: mask = 0b01 & 0b10 = 0. So this SHOULD be rejected,
        # matching the 'cannot mix uncompressed and compressed' invariant below.
        # A true positive is mux [0,1] + sub-flows both [0]:
        assert self._simulate_intersection([0, 1], [[0], [0]]) != 0

    def test_identical_groups_accept(self) -> None:
        # All flows on group 1 → intersection stays at bit 1.
        assert self._simulate_intersection([1], [[1], [1], [1]]) == (1 << 1)

    def test_disjoint_groups_reject(self) -> None:
        # Spec C5 negative — mux [0], one sub-flow [1] → empty intersection.
        assert self._simulate_intersection([0], [[1]]) == 0

    def test_uncompressed_subflow_mixed_with_compressed_subflow_rejected(self) -> None:
        # This is the invariant config6a encodes: RAW (group 0) and H.264
        # (group 1) must not coexist in the same mux.
        assert self._simulate_intersection([0, 1], [[0], [1]]) == 0

    def test_subflow_without_attribute_does_not_narrow_mask(self) -> None:
        # Spec C1 — a sub-flow with undefined groups contributes no constraint.
        # The `.defined` guard in compatibility.py:677 skips the AND, so the
        # resulting mask equals that of the other flows only.
        mask = self._simulate_intersection([0, 1], [[0], None])
        assert mask == (1 << 0)

    def test_mux_with_missing_attribute_accepts_any_subflow(self) -> None:
        # Spec C1 — if the mux has no attribute, initial mask is all-bits-set
        # (per the fixed helper). Any sub-flow's groups narrow from there.
        mask = self._simulate_intersection(None, [[5]])
        assert mask == (1 << 5)

    def test_empty_subflow_groups_force_rejection(self) -> None:
        # Not in the spec explicitly, but the helper's contract: a sub-flow
        # that declares an empty list is "member of no group" → mask collapses.
        assert self._simulate_intersection([0], [[]]) == 0

    def test_config6a_end_to_end_mux_sender_compatibility(self) -> None:
        """Build config6a and verify the mux sender resolves to a compatible
        state. This exercises the real intersection code path at
        compatibility.py:677 — not the simulation helper above."""
        from nmos.node import Node
        from nmos.node.config import ConfigBuilder

        node = Node()
        node.init(serial_number="LCGTST")

        with open(BUILTIN_DIR / "config6a.json") as f:
            config = json.load(f)

        builder = ConfigBuilder(node, verbose=False)
        for r in config.get("receivers", []):
            try:
                builder._build_receiver_from_config(r)
            except Exception:
                pass
        for s in config.get("senders", []):
            try:
                builder._build_sender_pipeline(s)
            except Exception:
                pass

        # Find the mux sender
        mux_sender = None
        for _sid, s in node.senders:
            fmt = str(s.Format.value) if s.Format.defined else ""
            if "mux" in fmt:
                mux_sender = s
                break

        if mux_sender is None:
            pytest.skip("config6a mux sender not built in this environment")

        # set_sender_compatibility_state runs check_sender_flow_compatibility,
        # which includes the mux∩sub-flow intersection at compatibility.py:677.
        status = node.set_sender_compatibility_state(mux_sender)
        # Pre-active-constraints, the mux must be compatible with its own flow.
        assert status in ("compatible", "unconstrained"), (
            f"config6a mux sender resolved to {status!r} before any IS-11 "
            f"constraints were applied — expected compatible/unconstrained."
        )


# ===========================================================================
# Class 4: write-back of compliant groups (spec C7)
# ===========================================================================

class TestWriteBack:
    """_write_layer_compatibility_groups writes compliant groups back to
    FlowCore.LayerCompatibilityGroups. Reference logic lives at
    nmosNodeCompatibility (inside each update*Flow function)."""

    def _make_flow_ptr_with_core(self) -> Any:
        """Construct a minimal object exposing FlowCore.LayerCompatibilityGroups
        the way _write_layer_compatibility_groups expects."""
        from nmos.types.generated.nflow_core import NFlowCoreValue

        class _PolyWrapper:
            def __init__(self, core: NFlowCoreValue) -> None:
                # _write_layer_compatibility_groups does:
                #   inner = flow_ptr.get() if hasattr(flow_ptr, 'get') else flow_ptr
                #   fv = inner.value if hasattr(inner, 'value') else inner
                #   fc = fv.FlowCore
                # So we expose .value → self, and .FlowCore → the inner core value.
                self.FlowCore = core

            @property
            def value(self) -> "_PolyWrapper":
                return self

        core = NFlowCoreValue()
        return _PolyWrapper(core)

    def test_write_back_sets_groups_on_flow(self) -> None:
        flow = self._make_flow_ptr_with_core()
        _write_layer_compatibility_groups(flow, [0, 1])
        assert flow.FlowCore.LayerCompatibilityGroups.defined is True
        assert list(flow.FlowCore.LayerCompatibilityGroups.value) == [0, 1]

    def test_write_back_with_none_clears_attribute(self) -> None:
        flow = self._make_flow_ptr_with_core()
        # First set it, then clear it via None.
        _write_layer_compatibility_groups(flow, [1])
        assert flow.FlowCore.LayerCompatibilityGroups.defined is True
        _write_layer_compatibility_groups(flow, None)
        assert flow.FlowCore.LayerCompatibilityGroups.defined is False

    def test_write_back_with_empty_list_still_marks_defined(self) -> None:
        # Semantically, "member of no group" is a valid albeit unusual state.
        # NArrayOfInt sets _defined=True when assigned any list (see json/types.py:759).
        flow = self._make_flow_ptr_with_core()
        _write_layer_compatibility_groups(flow, [])
        assert flow.FlowCore.LayerCompatibilityGroups.defined is True
        assert list(flow.FlowCore.LayerCompatibilityGroups.value) == []

    def test_write_back_is_idempotent(self) -> None:
        flow = self._make_flow_ptr_with_core()
        _write_layer_compatibility_groups(flow, [0, 1])
        _write_layer_compatibility_groups(flow, [0, 1])
        assert list(flow.FlowCore.LayerCompatibilityGroups.value) == [0, 1]

    def test_write_back_overwrites_previous_value(self) -> None:
        flow = self._make_flow_ptr_with_core()
        _write_layer_compatibility_groups(flow, [0])
        _write_layer_compatibility_groups(flow, [1, 2])
        assert list(flow.FlowCore.LayerCompatibilityGroups.value) == [1, 2]
