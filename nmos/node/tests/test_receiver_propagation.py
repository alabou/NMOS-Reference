# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Targeted unit tests for sender→receiver constraint propagation.

Covers the code paths in compatibility.py's port of
updateReceiverConstraintsToFlowProperties / checkReceiverNativePropertiesCompatibility
/ updateReceiverNativePropertiesCompatibility that the config-driven and AES3
integration tests do not exercise:

- the NConstraint→RangeValue converter (all scalar types, enum + min/max + infinite)
- _compliant_value_to_json for every value type
- the check-gate: accept/reject, preference/layer/format gating, the EXACT
  layer-compatibility-group guard (match / mismatch / empty=ALL wildcard), and
  unconstrained-property acceptance
- the native-properties update: value written, no-matching-set, layer/format select
- the recursive driver: mux-trunk early return, parents branch (dispatch + recursion,
  static-parent skip, coded-parent raise), and leaf error/edge branches
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any

import pytest

try:
    from caps.MatroxCCF import (
        Cap, RangeValue, RangeType,
        CapFormatFrameWidth, CapFormatFrameHeight, CapFormatGrainRate,
        CapFormatColorspace, CapFormatComponentDepth,
    )
    HAS_CCF = True
except ImportError:
    HAS_CCF = False

import nmos.node.compatibility as C
from nmos.errors import UnexpectedError

pytestmark = pytest.mark.skipif(not HAS_CCF, reason="MatroxCCF not available")


# Meta-attribute URN keys for building receiver constraint sets from JSON.
_PREF = "urn:x-nmos:cap:meta:preference"
_LAYER = "urn:x-matrox:cap:meta:layer"
_FORMAT = "urn:x-matrox:cap:meta:format"
_ENABLED = "urn:x-nmos:cap:meta:enabled"
_LAYER_ENABLED = "urn:x-matrox:cap:meta:layer_enabled"
_GROUPS = "urn:x-matrox:cap:meta:layer_compatibility_groups"

_VIDEO = "urn:x-nmos:format:video"
_AUDIO = "urn:x-nmos:format:audio"


def _make_csf(*constraint_sets: dict) -> Any:
    """Build a defined NArrayOfConstraintSet from JSON-shaped constraint-set dicts."""
    from nmos.types.generated.narray_of_constraint_set import NArrayOfConstraintSet
    csf = NArrayOfConstraintSet()
    csf.decode_value(list(constraint_sets))
    return csf


def _props(**kw: Any) -> dict[str, Any]:
    """Build a {cap_name -> CCF Cap} generic-properties map from name->RangeValue."""
    return {name: Cap(name, rv) for name, rv in kw.items()}


def _nconstraint(data: dict) -> Any:
    from nmos.types.generated.nconstraint import NConstraint
    c = NConstraint()
    c.decode_value(data)
    return c


# ---------------------------------------------------------------------------
# _nconstraint_to_range — converter completeness
# ---------------------------------------------------------------------------

class TestNConstraintToRangeCompleteness:

    def test_float_enum(self) -> None:
        rng = C._nconstraint_to_range(_nconstraint({"enum": [1.5, 2.5]}))
        assert rng.type == RangeType.FLOAT
        assert rng.includes_value(1.5)
        assert not rng.includes_value(3.0)

    def test_float_minmax(self) -> None:
        rng = C._nconstraint_to_range(_nconstraint({"minimum": 1.0, "maximum": 2.0}))
        assert rng.type == RangeType.FLOAT
        assert rng.includes_value(1.5)
        assert not rng.includes_value(2.5)

    def test_rational_minmax(self) -> None:
        rng = C._nconstraint_to_range(_nconstraint({
            "minimum": {"numerator": 24, "denominator": 1},
            "maximum": {"numerator": 60, "denominator": 1},
        }))
        assert rng.type == RangeType.RATIONAL
        assert rng.includes_value(Fraction(30, 1))
        assert not rng.includes_value(Fraction(120, 1))

    def test_int_min_only(self) -> None:
        rng = C._nconstraint_to_range(_nconstraint({"minimum": 720}))
        assert rng.type == RangeType.INT
        assert rng.includes_value(1080)
        assert not rng.includes_value(480)

    def test_unconstrained_rational_is_infinite(self) -> None:
        from nmos.types.generated.nconstraint import NConstraint
        from nmos.types.generated.nconstraint_rational import NConstraintRational, NConstraintRationalValue
        inner = NConstraintRational()
        inner.set_value(NConstraintRationalValue())  # no enum / min / max
        c = NConstraint()
        c.value = inner
        rng = C._nconstraint_to_range(c)
        assert rng is not None and rng.is_infinite()

    def test_unconstrained_int_is_infinite(self) -> None:
        # An int constraint with neither enum nor min/max → infinite (accept all).
        from nmos.types.generated.nconstraint import NConstraint
        from nmos.types.generated.nconstraint_int import NConstraintInt, NConstraintIntValue
        inner = NConstraintInt()
        inner.set_value(NConstraintIntValue())  # all bound fields undefined
        c = NConstraint()
        c.value = inner
        rng = C._nconstraint_to_range(c)
        assert rng is not None and rng.is_infinite()
        assert rng.includes_value(12345)


# ---------------------------------------------------------------------------
# _compliant_value_to_json — every value type
# ---------------------------------------------------------------------------

class TestCompliantValueToJson:

    def test_int(self) -> None:
        assert C._compliant_value_to_json(Cap(CapFormatFrameWidth, RangeValue(values=(1920,), type=RangeType.INT))) == 1920

    def test_bool(self) -> None:
        assert C._compliant_value_to_json(Cap("b", RangeValue(values=(True,), type=RangeType.BOOL))) is True

    def test_float(self) -> None:
        assert C._compliant_value_to_json(Cap("f", RangeValue(values=(1.5,), type=RangeType.FLOAT))) == 1.5

    def test_rational(self) -> None:
        out = C._compliant_value_to_json(Cap(CapFormatGrainRate, RangeValue(values=(Fraction(60, 1),), type=RangeType.RATIONAL)))
        assert out == {"numerator": 60, "denominator": 1}

    def test_string(self) -> None:
        assert C._compliant_value_to_json(Cap(CapFormatColorspace, RangeValue(values=("BT709",), type=RangeType.STRING))) == "BT709"

    def test_min_extraction(self) -> None:
        # No enumerated values, only a min bound → the min is used.
        assert C._compliant_value_to_json(Cap(CapFormatFrameWidth, RangeValue(min=720, type=RangeType.INT))) == 720

    def test_no_value(self) -> None:
        assert C._compliant_value_to_json(Cap(CapFormatFrameWidth, RangeValue(infinite=True, type=RangeType.INT))) is None


# ---------------------------------------------------------------------------
# _check_receiver_native_properties_compatibility — the gate
# ---------------------------------------------------------------------------

class TestCheckReceiverNativePropertiesCompatibility:

    def _check(self, csf, props, groups=None, layer=-1, fmt=_VIDEO):
        return C._check_receiver_native_properties_compatibility(props, groups, csf, layer, fmt)

    def test_accept_when_props_in_non_native_set(self) -> None:
        csf = _make_csf({_PREF: 1, CapFormatFrameWidth: {"enum": [1920, 3840]}})
        props = _props(**{CapFormatFrameWidth: RangeValue(values=(1920,), type=RangeType.INT)})
        assert self._check(csf, props) is True

    def test_reject_when_prop_out_of_range(self) -> None:
        csf = _make_csf({_PREF: 1, CapFormatFrameWidth: {"enum": [1920, 3840]}})
        props = _props(**{CapFormatFrameWidth: RangeValue(values=(1280,), type=RangeType.INT)})
        assert self._check(csf, props) is False

    def test_reject_when_only_native_set(self) -> None:
        # preference=100 sets are skipped (native is produced, not tested).
        csf = _make_csf({_PREF: 100, CapFormatFrameWidth: {"enum": [1920]}})
        props = _props(**{CapFormatFrameWidth: RangeValue(values=(1920,), type=RangeType.INT)})
        assert self._check(csf, props) is False

    def test_empty_constraint_sets(self) -> None:
        from nmos.types.generated.narray_of_constraint_set import NArrayOfConstraintSet
        assert self._check(NArrayOfConstraintSet(), _props()) is False

    def test_unconstrained_property_accepted(self) -> None:
        # The set exists and matches gating, but does not constrain frame_height.
        csf = _make_csf({_PREF: 1, CapFormatFrameWidth: {"enum": [1920]}})
        props = _props(**{
            CapFormatFrameWidth: RangeValue(values=(1920,), type=RangeType.INT),
            CapFormatFrameHeight: RangeValue(values=(1080,), type=RangeType.INT),
        })
        assert self._check(csf, props) is True

    def test_disabled_set_skipped(self) -> None:
        csf = _make_csf({_PREF: 1, _ENABLED: False, CapFormatFrameWidth: {"enum": [1920]}})
        props = _props(**{CapFormatFrameWidth: RangeValue(values=(1920,), type=RangeType.INT)})
        assert self._check(csf, props) is False

    def test_layer_format_gate_layer_mismatch(self) -> None:
        csf = _make_csf({_PREF: 1, _LAYER: 1, _FORMAT: _VIDEO, CapFormatFrameWidth: {"enum": [1920]}})
        props = _props(**{CapFormatFrameWidth: RangeValue(values=(1920,), type=RangeType.INT)})
        assert self._check(csf, props, layer=0) is False

    def test_layer_format_gate_format_mismatch(self) -> None:
        csf = _make_csf({_PREF: 1, _LAYER: 0, _FORMAT: _AUDIO, CapFormatFrameWidth: {"enum": [1920]}})
        props = _props(**{CapFormatFrameWidth: RangeValue(values=(1920,), type=RangeType.INT)})
        assert self._check(csf, props, layer=0, fmt=_VIDEO) is False

    def test_layered_set_skipped_when_no_source_layer(self) -> None:
        # source_layer < 0 → sets that carry a layer are skipped.
        csf = _make_csf({_PREF: 1, _LAYER: 0, _FORMAT: _VIDEO, CapFormatFrameWidth: {"enum": [1920]}})
        props = _props(**{CapFormatFrameWidth: RangeValue(values=(1920,), type=RangeType.INT)})
        assert self._check(csf, props, layer=-1) is False

    # --- EXACT layer-compatibility-group guard (the debated semantics) ---

    def test_group_guard_exact_match(self) -> None:
        csf = _make_csf({_PREF: 1, _LAYER: 0, _FORMAT: _VIDEO, _GROUPS: [0],
                         CapFormatFrameWidth: {"enum": [1920]}})
        props = _props(**{CapFormatFrameWidth: RangeValue(values=(1920,), type=RangeType.INT)})
        assert self._check(csf, props, groups=[0], layer=0) is True

    def test_group_guard_mismatch_skips(self) -> None:
        csf = _make_csf({_PREF: 1, _LAYER: 0, _FORMAT: _VIDEO, _GROUPS: [0],
                         CapFormatFrameWidth: {"enum": [1920]}})
        props = _props(**{CapFormatFrameWidth: RangeValue(values=(1920,), type=RangeType.INT)})
        assert self._check(csf, props, groups=[1], layer=0) is False

    def test_group_guard_empty_compliant_is_wildcard(self) -> None:
        # Empty compliant groups == ALL groups → matches a set with groups={0}.
        csf = _make_csf({_PREF: 1, _LAYER: 0, _FORMAT: _VIDEO, _GROUPS: [0],
                         CapFormatFrameWidth: {"enum": [1920]}})
        props = _props(**{CapFormatFrameWidth: RangeValue(values=(1920,), type=RangeType.INT)})
        assert self._check(csf, props, groups=[], layer=0) is True

    def test_group_guard_empty_receiver_is_wildcard(self) -> None:
        # Receiver set without groups == ALL groups → matches any compliant groups.
        csf = _make_csf({_PREF: 1, _LAYER: 0, _FORMAT: _VIDEO,
                         CapFormatFrameWidth: {"enum": [1920]}})
        props = _props(**{CapFormatFrameWidth: RangeValue(values=(1920,), type=RangeType.INT)})
        assert self._check(csf, props, groups=[0, 1], layer=0) is True


# ---------------------------------------------------------------------------
# _update_receiver_native_properties_compatibility — native write-back
# ---------------------------------------------------------------------------

class TestUpdateReceiverNativeProperties:

    def _native_value(self, csf, cap_name) -> Any:
        from nmos.enums import EnumRegistry
        for cs in csf.value:
            pref = cs.MetaPreference.value if cs.MetaPreference.defined else 0
            if pref != 100:
                continue
            nc = cs.Constraints.get().get(EnumRegistry.get(cap_name))
            if nc is None:
                return None
            return list(nc.value.value.Enum.value)
        return None

    def test_writes_single_value_into_native_set(self) -> None:
        csf = _make_csf({_PREF: 100, CapFormatFrameWidth: {"minimum": 1280, "maximum": 3840}})
        props = _props(**{CapFormatFrameWidth: RangeValue(values=(1920,), type=RangeType.INT)})
        ok = C._update_receiver_native_properties_compatibility(props, csf, -1, _VIDEO)
        assert ok is True
        assert self._native_value(csf, CapFormatFrameWidth) == [1920]

    def test_no_matching_native_set(self) -> None:
        csf = _make_csf({_PREF: 1, CapFormatFrameWidth: {"enum": [1920]}})  # only non-native
        props = _props(**{CapFormatFrameWidth: RangeValue(values=(1920,), type=RangeType.INT)})
        assert C._update_receiver_native_properties_compatibility(props, csf, -1, _VIDEO) is False

    def test_empty_constraint_sets(self) -> None:
        from nmos.types.generated.narray_of_constraint_set import NArrayOfConstraintSet
        empty = NArrayOfConstraintSet()
        empty.decode_value([])  # defined but empty
        props = _props(**{CapFormatFrameWidth: RangeValue(values=(1920,), type=RangeType.INT)})
        assert C._update_receiver_native_properties_compatibility(props, empty, -1, _VIDEO) is False

    def test_disabled_native_set_skipped(self) -> None:
        csf = _make_csf({_PREF: 100, _ENABLED: False, CapFormatFrameWidth: {"minimum": 1, "maximum": 4096}})
        props = _props(**{CapFormatFrameWidth: RangeValue(values=(1920,), type=RangeType.INT)})
        assert C._update_receiver_native_properties_compatibility(props, csf, -1, _VIDEO) is False

    def test_property_with_no_extractable_value_skipped(self) -> None:
        # A generic property whose compliant value can't be extracted (infinite
        # range) is skipped during the native write, but the set still updates.
        csf = _make_csf({_PREF: 100,
                         CapFormatFrameWidth: {"minimum": 1, "maximum": 4096},
                         CapFormatFrameHeight: {"minimum": 1, "maximum": 4096}})
        props = _props(**{
            CapFormatFrameWidth: RangeValue(values=(1920,), type=RangeType.INT),
            CapFormatFrameHeight: RangeValue(infinite=True, type=RangeType.INT),  # no value
        })
        ok = C._update_receiver_native_properties_compatibility(props, csf, -1, _VIDEO)
        assert ok is True
        assert self._native_value(csf, CapFormatFrameWidth) == [1920]
        # frame_height had no extractable value → left as its original range.
        from nmos.enums import EnumRegistry
        for cs in csf.value:
            nc = cs.Constraints.get()[EnumRegistry.get(CapFormatFrameHeight)]
            assert nc.value.value.Minimum.value == 1  # unchanged range, not a single enum

    def test_layer_format_selection(self) -> None:
        # Two native sets; only the layer/format-matching one is updated.
        csf = _make_csf(
            {_PREF: 100, _LAYER: 0, _FORMAT: _AUDIO, CapFormatFrameWidth: {"enum": [1]}},
            {_PREF: 100, _LAYER: 0, _FORMAT: _VIDEO, CapFormatFrameWidth: {"minimum": 1, "maximum": 4096}},
        )
        props = _props(**{CapFormatFrameWidth: RangeValue(values=(1920,), type=RangeType.INT)})
        ok = C._update_receiver_native_properties_compatibility(props, csf, 0, _VIDEO)
        assert ok is True
        from nmos.enums import EnumRegistry
        # The video layer-0 native set got 1920; the audio one is untouched ([1]).
        vals = {}
        for cs in csf.value:
            fmt = str(cs.MetaFormat.value) if cs.MetaFormat.defined else ""
            nc = cs.Constraints.get().get(EnumRegistry.get(CapFormatFrameWidth))
            vals[fmt] = list(nc.value.value.Enum.value)
        assert vals[_VIDEO] == [1920]
        assert vals[_AUDIO] == [1]


# ---------------------------------------------------------------------------
# _update_receiver_constraints_to_flow_properties — recursive driver
# ---------------------------------------------------------------------------

_UNDEF = object()


class _Fld:
    """Minimal NMOS-field shim with .defined / .value."""
    def __init__(self, v: Any = _UNDEF) -> None:
        self.defined = v is not _UNDEF
        self._v = v

    @property
    def value(self) -> Any:
        assert self.defined
        return self._v


class _Obj:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


class _Ptr:
    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def get(self) -> Any:
        return self._inner


class _Store:
    def __init__(self, d: dict | None = None) -> None:
        self.d = d or {}

    def get(self, k: Any) -> Any:
        return self.d.get(k)


class _Node:
    def __init__(self) -> None:
        self.flows = _Store()
        self.sources = _Store()
        self.receivers = _Store()


def _flow(fmt: str, *, source_id: Any = _UNDEF, parents: Any = _UNDEF,
          media_type: str = "video/raw", static: bool = False) -> Any:
    return _Ptr(_Obj(
        FlowCore=_Obj(
            SourceId=_Fld(source_id),
            Parents=_Fld(parents),
            Static=_Fld(static),
        ),
        Format=_Fld(fmt),
        MediaType=_Fld(media_type),
    ))


def _source(fmt: str, *, receiver_id: Any = _UNDEF, layer: Any = _UNDEF) -> Any:
    return _Ptr(_Obj(
        SourceCore=_Obj(ReceiverId=_Fld(receiver_id), Layer=_Fld(layer)),
        Format=_Fld(fmt),
    ))


def _caps_set() -> Any:
    return Cap  # unused placeholder to keep import side effects explicit


class TestPropagationDriver:

    def test_mux_trunk_returns_without_propagating(self) -> None:
        # Mux format is not in {video,audio,data} → early return, no error.
        node = _Node()
        flow = _flow("urn:x-nmos:format:mux", media_type="application/AM824")
        C._update_receiver_constraints_to_flow_properties(node, flow, None, None)

    def test_leaf_no_source_raises(self) -> None:
        node = _Node()
        flow = _flow(_VIDEO)  # SourceId undefined
        with pytest.raises(UnexpectedError):
            C._update_receiver_constraints_to_flow_properties(node, flow, _compliant(), None)

    def test_leaf_missing_source_raises(self) -> None:
        node = _Node()
        flow = _flow(_VIDEO, source_id="missing")
        with pytest.raises(UnexpectedError):
            C._update_receiver_constraints_to_flow_properties(node, flow, _compliant(), None)

    def test_leaf_source_format_mismatch_raises(self) -> None:
        node = _Node()
        node.sources.d["s1"] = _source(_AUDIO, receiver_id="r1")  # audio source vs video flow
        flow = _flow(_VIDEO, source_id="s1")
        with pytest.raises(UnexpectedError):
            C._update_receiver_constraints_to_flow_properties(node, flow, _compliant(), None)

    def test_leaf_no_linked_receiver_noop(self) -> None:
        node = _Node()
        node.sources.d["s1"] = _source(_VIDEO)  # ReceiverId undefined
        flow = _flow(_VIDEO, source_id="s1")
        C._update_receiver_constraints_to_flow_properties(node, flow, _compliant(), None)

    def test_leaf_receiver_not_found_noop(self) -> None:
        node = _Node()
        node.sources.d["s1"] = _source(_VIDEO, receiver_id="r1")
        flow = _flow(_VIDEO, source_id="s1")
        C._update_receiver_constraints_to_flow_properties(node, flow, _compliant(), None)

    def test_leaf_invalid_receiver_type_raises(self) -> None:
        node = _Node()
        node.sources.d["s1"] = _source(_VIDEO, receiver_id="r1")
        node.receivers.d["r1"] = _Ptr(_Obj())  # not an NReceiver*Value
        flow = _flow(_VIDEO, source_id="s1")
        with pytest.raises(UnexpectedError):
            C._update_receiver_constraints_to_flow_properties(node, flow, _compliant(), None)

    def test_leaf_layer_invariant_raises(self) -> None:
        # Non-mux receiver but source has a layer >= 0 → invalid.
        from nmos.types.generated.nreceiver_video import NReceiverVideoValue
        node = _Node()
        node.sources.d["s1"] = _source(_VIDEO, receiver_id="r1", layer=0)
        node.receivers.d["r1"] = _Ptr(NReceiverVideoValue())
        flow = _flow(_VIDEO, source_id="s1")
        with pytest.raises(UnexpectedError):
            C._update_receiver_constraints_to_flow_properties(node, flow, _compliant(), None)

    def test_leaf_static_receiver_noop(self) -> None:
        from nmos.types.generated.nreceiver_video import NReceiverVideoValue
        node = _Node()
        node.sources.d["s1"] = _source(_VIDEO, receiver_id="r1")  # layer undefined → -1
        rv = NReceiverVideoValue()
        rv.ReceiverCore.Static.value = True
        node.receivers.d["r1"] = _Ptr(rv)
        flow = _flow(_VIDEO, source_id="s1")
        # Static receiver → returns before touching caps; no error.
        C._update_receiver_constraints_to_flow_properties(node, flow, _compliant(), None)

    # --- parents branch: dispatch + recursion ---

    def test_parents_branch_dispatch_and_recurse(self, monkeypatch) -> None:
        calls = {"video": 0}
        monkeypatch.setattr(C, "update_raw_video_flow",
                            lambda *a, **k: calls.__setitem__("video", calls["video"] + 1))
        node = _Node()
        # Parent is a raw video leaf with no linked receiver → recursion terminates.
        node.flows.d["p1"] = _flow(_VIDEO, media_type="video/raw", source_id="ps1")
        node.sources.d["ps1"] = _source(_VIDEO)  # no receiver → recursion no-ops
        child = _flow(_VIDEO, media_type="video/H264", parents=["p1"])
        C._update_receiver_constraints_to_flow_properties(node, child, _compliant(), None)
        assert calls["video"] == 1  # parent raw-video update dispatched once

    def test_parents_branch_static_parent_skipped(self, monkeypatch) -> None:
        calls = {"video": 0}
        monkeypatch.setattr(C, "update_raw_video_flow",
                            lambda *a, **k: calls.__setitem__("video", calls["video"] + 1))
        node = _Node()
        node.flows.d["p1"] = _flow(_VIDEO, media_type="video/raw", source_id="ps1", static=True)
        child = _flow(_VIDEO, media_type="video/H264", parents=["p1"])
        C._update_receiver_constraints_to_flow_properties(node, child, _compliant(), None)
        assert calls["video"] == 0  # static parent skipped

    def test_parents_branch_coded_parent_raises(self) -> None:
        node = _Node()
        # Parent declares video format but a coded media_type → not raw → raise.
        node.flows.d["p1"] = _flow(_VIDEO, media_type="video/H264")
        child = _flow(_VIDEO, media_type="video/H264", parents=["p1"])
        with pytest.raises(UnexpectedError):
            C._update_receiver_constraints_to_flow_properties(node, child, _compliant(), None)

    def test_parents_branch_missing_parent_raises(self) -> None:
        node = _Node()
        child = _flow(_VIDEO, parents=["nope"])
        with pytest.raises(UnexpectedError):
            C._update_receiver_constraints_to_flow_properties(node, child, _compliant(), None)

    def test_parents_branch_audio_dispatch(self, monkeypatch) -> None:
        calls = {"audio": 0}
        monkeypatch.setattr(C, "update_raw_audio_flow",
                            lambda *a, **k: calls.__setitem__("audio", calls["audio"] + 1))
        node = _Node()
        node.flows.d["p1"] = _flow(_AUDIO, media_type="audio/L24", source_id="ps1")
        node.sources.d["ps1"] = _source(_AUDIO)  # no receiver → recursion no-ops
        child = _flow(_AUDIO, media_type="audio/mpeg4-generic", parents=["p1"])
        C._update_receiver_constraints_to_flow_properties(node, child, _compliant(), None)
        assert calls["audio"] == 1

    def test_parents_branch_data_dispatch(self, monkeypatch) -> None:
        calls = {"data": 0}
        monkeypatch.setattr(C, "update_data_flow",
                            lambda *a, **k: calls.__setitem__("data", calls["data"] + 1))
        node = _Node()
        node.flows.d["p1"] = _flow("urn:x-nmos:format:data", media_type="application/json", source_id="ps1")
        node.sources.d["ps1"] = _source("urn:x-nmos:format:data")
        child = _flow("urn:x-nmos:format:data", media_type="application/json", parents=["p1"])
        C._update_receiver_constraints_to_flow_properties(node, child, _compliant(), None)
        assert calls["data"] == 1

    def test_parents_branch_coded_audio_parent_raises(self) -> None:
        node = _Node()
        node.flows.d["p1"] = _flow(_AUDIO, media_type="audio/mpeg4-generic")  # coded audio parent
        child = _flow(_AUDIO, media_type="audio/mpeg4-generic", parents=["p1"])
        with pytest.raises(UnexpectedError):
            C._update_receiver_constraints_to_flow_properties(node, child, _compliant(), None)

    def test_parents_branch_unknown_format_raises(self) -> None:
        node = _Node()
        node.flows.d["p1"] = _flow("urn:x-nmos:format:mux", media_type="application/AM824")
        child = _flow(_VIDEO, parents=["p1"])
        with pytest.raises(UnexpectedError):
            C._update_receiver_constraints_to_flow_properties(node, child, _compliant(), None)

    def test_leaf_receiver_without_caps_noop(self) -> None:
        # Non-static, non-mux receiver with no Caps → returns before update.
        from nmos.types.generated.nreceiver_video import NReceiverVideoValue
        node = _Node()
        node.sources.d["s1"] = _source(_VIDEO, receiver_id="r1")  # layer -1
        node.receivers.d["r1"] = _Ptr(NReceiverVideoValue())  # Caps undefined
        flow = _flow(_VIDEO, source_id="s1")
        C._update_receiver_constraints_to_flow_properties(node, flow, _compliant(), None)


def _compliant() -> Any:
    """A compliant CCF CapSet carrying a generic video property."""
    from caps.MatroxCCF import CapSet
    return CapSet(caps={CapFormatFrameWidth: Cap(CapFormatFrameWidth, RangeValue(values=(1920,), type=RangeType.INT))})
